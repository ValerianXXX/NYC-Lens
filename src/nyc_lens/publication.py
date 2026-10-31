"""One database transaction for a local data publication, including nested helpers."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps


_OWNERS = ContextVar("nyc_external_publication_owners", default=())


@dataclass
class _Publication:
    connection: object
    failed: bool = False


@contextmanager
def publication_transaction(conn):
    owner = next((item for item in _OWNERS.get() if item.connection is conn), None)
    if owner is not None:
        try:
            yield
        except BaseException:
            owner.failed = True
            raise
        return
    # Never silently commit an unrelated transaction owned by a direct caller.
    conn.execute("BEGIN")
    owner = _Publication(conn)
    token = _OWNERS.set((*_OWNERS.get(), owner))
    try:
        yield
        if owner.failed:
            raise RuntimeError("PUBLICATION_ABORTED: a nested publication failed")
        conn.execute("COMMIT")
    except BaseException as error:
        try:
            conn.execute("ROLLBACK")
        except BaseException:
            error.add_note("Publication rollback could not be confirmed; inspect the active catalog before retrying.")
        raise
    finally:
        _OWNERS.reset(token)


def atomic_publication(function):
    @wraps(function)
    def wrapped(conn, *args, **kwargs):
        with publication_transaction(conn):
            return function(conn, *args, **kwargs)
    return wrapped
