"""One admitted local operation per database; OS locks release after process exit."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import os
from pathlib import Path
from typing import Iterator

from .execution_context import in_worker


class DatabaseBusyError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("The local database is busy. Wait for its current operation to stop before retrying.")


_OWNER: ContextVar[str | None] = ContextVar("nyc_database_admission", default=None)


@contextmanager
def database_admission(database: Path) -> Iterator[None]:
    path = Path(database).resolve()
    identity = os.path.normcase(str(path))
    if in_worker() or _OWNER.get() == identity:
        yield
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".execution.lock")
    # The file is only a fixed lock byte, not a stale PID lease. Never unlink it:
    # replacing the inode while another process holds it would split ownership.
    stream = lock_path.open("a+b")
    locked = False
    token = None
    try:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise DatabaseBusyError() from None
        locked = True
        token = _OWNER.set(identity)
        yield
    finally:
        if token is not None:
            _OWNER.reset(token)
        try:
            if locked:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()
