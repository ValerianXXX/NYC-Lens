"""Coordinate small job receipts across local readers and their lease owner.

This retries receipt I/O, never an analysis. A persistent access failure remains
unavailable; no stale or invented success is substituted.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import time

from .database_admission import DatabaseBusyError, database_admission

RECEIPT_IO_SECONDS = 1.0


class ReceiptUnavailableError(RuntimeError):
    pass


def _access(path: Path, operation):
    deadline = time.monotonic() + RECEIPT_IO_SECONDS
    while True:
        try:
            # Share one short OS-backed lock for initial creation, complete
            # reads and replacement. Do not unlink the lock's stable inode.
            with database_admission(path.with_suffix(".receipt-io")):
                return operation()
        except (DatabaseBusyError, PermissionError):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ReceiptUnavailableError("JOB_RECEIPT_UNAVAILABLE") from None
            time.sleep(min(.01, remaining))


def read_receipt(path: Path) -> bytes:
    # Unknown IDs do not create new lock files. Creation can race this check;
    # callers may observe not-found until the first receipt is available.
    try:
        if not path.exists():
            raise FileNotFoundError()
    except PermissionError:
        pass  # Preserve the same bounded unavailable behavior for denied stat.

    def read():
        with path.open("rb") as stream:
            return stream.read(65_537)

    return _access(path, read)


def write_receipt(path: Path, receipt: dict, *, create: bool = False) -> None:
    def publish():
        # The supervisor's separate lease remains the writer authority. The
        # I/O lock only coordinates handles; it does not grant that authority.
        target = path if create else path.with_suffix(".tmp")
        with target.open("x" if create else "w", encoding="utf8") as stream:
            json.dump(receipt, stream, ensure_ascii=True)
            stream.flush()
            os.fsync(stream.fileno())
        if not create:
            os.replace(target, path)

    _access(path, publish)
