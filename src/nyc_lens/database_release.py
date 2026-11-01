"""Bound a Windows sharing-lock check after the owned worker has stopped."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import time

from .paths import filesystem_path

DATABASE_RELEASE_SECONDS = 1.0


def _available(database: Path) -> bool:
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    api.CreateFileW.restype = wintypes.HANDLE
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    # Match DuckDB's write-lock sharing mode. OPEN_EXISTING never creates or
    # truncates a database; no bytes are read/written and the handle is private.
    handle = api.CreateFileW(str(filesystem_path(database)), 0xC0000000, 4, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        error = ctypes.get_last_error()
        if error in {32, 33}:  # sharing/lock violation, independent of OS language
            return False
        raise ctypes.WinError(error)
    if not api.CloseHandle(handle):
        raise ctypes.WinError(ctypes.get_last_error())
    return True


def wait_for_database_release(database: Path) -> None:
    """Check only the captured database while its existing admission is held.

    This does not rerun a worker, query or transaction. A new external opener
    can race the final connect; that connect must still succeed normally.
    """
    if os.name != "nt":
        return
    deadline = time.monotonic() + DATABASE_RELEASE_SECONDS
    while not _available(database):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("DATABASE_FILE_RELEASE_UNCONFIRMED")
        time.sleep(min(.02, remaining))
