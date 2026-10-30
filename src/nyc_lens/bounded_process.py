"""Supervise a trusted Python worker; never accept a command from a tool user."""
from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from .process_limits import WindowsJob


@dataclass(frozen=True)
class ProcessLimits:
    wall_seconds: float = 120
    memory_mb: int = 2048

    def __post_init__(self) -> None:
        if (type(self.wall_seconds) not in (int, float) or not math.isfinite(self.wall_seconds)
            or not 0.05 <= self.wall_seconds <= 3600):
            raise ValueError("Worker wall budget must be a finite 0.05..3600 seconds")
        if type(self.memory_mb) is not int or not 64 <= self.memory_mb <= 16_384:
            raise ValueError("Worker memory budget must be an integer 64..16384 MiB")


@dataclass(frozen=True)
class ProcessOutcome:
    reason: str
    returncode: int | None
    pid: int | None
    process_stopped: bool
    memory_boundary: str
    elapsed_seconds: float


def run_python_worker(script: Path, arguments: list[str], *, limits: ProcessLimits,
                      cancel: threading.Event | None = None) -> ProcessOutcome:
    """Run a trusted local script behind a start gate and wait for actual exit.

    This internal primitive has no shell, output capture, provider or retries.
    The application supplies the script and owns any input/output file protocol.
    """
    started = time.monotonic()
    boundary = "WINDOWS_JOB_COMMITTED_MEMORY" if os.name == "nt" else "POSIX_ADDRESS_SPACE"
    if cancel is not None and cancel.is_set():
        return ProcessOutcome("CANCELLED", None, None, True, boundary, 0)
    if os.name != "nt" and sys.platform != "linux":
        raise RuntimeError("RESOURCE_GUARD_UNAVAILABLE")
    script = Path(script).resolve(strict=True)
    if not script.is_file() or not all(isinstance(value, str) for value in arguments):
        raise ValueError("A trusted worker file and string arguments are required")
    environment = dict(os.environ)
    environment.update(OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
    command = [sys.executable, "-m", "nyc_lens.worker_bootstrap", "--memory-mb", str(limits.memory_mb),
               "--parent-pid", str(os.getpid()), "--script", str(script), *arguments]
    job = WindowsJob(limits.memory_mb * 1024 * 1024) if os.name == "nt" else None
    process = None
    job_assigned = False
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, env=environment, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt")
        if job:
            job.assign(process.pid)
            job_assigned = True
        process.stdin.write(b"RUN\n")
        process.stdin.flush()
        process.stdin.close()
        reason = "EXITED"
        while process.poll() is None:
            if cancel is not None and cancel.is_set():
                reason = "CANCELLED"
                break
            if time.monotonic() - started >= limits.wall_seconds:
                reason = "TIMED_OUT"
                break
            if cancel is None:
                time.sleep(0.02)
            else:
                cancel.wait(0.02)
        if reason != "EXITED":
            if job:
                job.terminate()
            else:
                os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
        # Descendants must not outlive a successful or stopped worker either.
        if job:
            job.terminate()
            job.wait_empty()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        return ProcessOutcome(reason, process.returncode, process.pid, True, boundary,
                              round(time.monotonic() - started, 3))
    finally:
        if process is not None:
            if process.poll() is None:
                if job and job_assigned:
                    job.terminate()
                elif os.name == "nt":
                    # Assignment failed while the worker was still behind the
                    # start gate; an empty Job cannot terminate that process.
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
            if process.stdin and not process.stdin.closed:
                process.stdin.close()
        if job:
            job.close()
