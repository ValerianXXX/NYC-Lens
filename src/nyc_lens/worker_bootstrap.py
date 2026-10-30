"""Install worker-only limits before importing analysis libraries or reading data."""
from __future__ import annotations

import argparse
import ctypes
import os
import runpy
import signal
import sys


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--memory-mb", required=True, type=int)
    parser.add_argument("--parent-pid", required=True, type=int)
    parser.add_argument("--script", required=True)
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not 64 <= args.memory_mb <= 16_384 or args.parent_pid < 1:
        raise SystemExit(70)
    if sys.platform == "linux":
        import resource
        limit = args.memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        # Linux PR_SET_PDEATHSIG. Check again after setting it to close the
        # parent-exit race; this process can only terminate itself here.
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
        libc.prctl.restype = ctypes.c_int
        if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != args.parent_pid:
            raise SystemExit(71)
    elif os.name != "nt":
        raise SystemExit(70)
    # Windows waits until the parent has assigned its committed-memory Job
    # Object. EOF, bad framing or a failed native guard cannot start any task.
    if sys.stdin.buffer.readline(5) != b"RUN\n":
        raise SystemExit(72)
    sys.argv = [args.script, *args.arguments]
    runpy.run_path(args.script, run_name="__main__")


if __name__ == "__main__":
    main()
