"""Own one Windows file handle for an isolated synthetic recovery test."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
import time

assert os.name == 'nt'
database, ready, release = map(Path, sys.argv[1:])
assert database.is_file() and database.parent.parent == ready.parent == release.parent
api = ctypes.WinDLL('kernel32', use_last_error=True)
api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
    ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
api.CreateFileW.restype = wintypes.HANDLE
api.CloseHandle.argtypes = [wintypes.HANDLE]
api.CloseHandle.restype = wintypes.BOOL
# Existing file only; no write, truncation, deletion or inheritable handle.
handle = api.CreateFileW(str(database), 0xC0000000, 4, None, 3, 0x80, None)
if handle == ctypes.c_void_p(-1).value:
    raise ctypes.WinError(ctypes.get_last_error())
try:
    temporary = ready.with_suffix('.pending')
    temporary.write_text(json.dumps({'pid': os.getpid(), 'held': True}), encoding='utf-8')
    temporary.replace(ready)
    until = time.monotonic() + 10
    while not release.exists() and time.monotonic() < until:
        time.sleep(.01)
    if not release.exists():
        raise RuntimeError('Synthetic file holder exceeded its release deadline')
finally:
    assert api.CloseHandle(handle)
