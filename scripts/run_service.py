#!/usr/bin/env python3
"""Start a service detached from the launching shell.

Running ``uvicorn &`` from a script that then exits takes the child down with
it, because the child stays in the shell's process group. This double-forks,
reparents to init, and writes a pidfile + log, so a service survives the shell
that started it. This is how ``scripts/start-all.sh`` keeps four services up.

Usage:
    python3 scripts/run_service.py <name> <command> [args...]
    python3 scripts/run_service.py --stop <name>
    python3 scripts/run_service.py --status
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

STATE_DIR = Path(__file__).resolve().parent.parent / ".run"
LOG_DIR = STATE_DIR / "logs"


def _pidfile(name: str) -> Path:
    return STATE_DIR / f"{name}.pid"


def _read_pid(name: str) -> int | None:
    path = _pidfile(name)
    if not path.exists():
        return None
    try:
        return int(path.read_text().strip())
    except ValueError:
        return None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def start(name: str, command: list[str]) -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    existing = _read_pid(name)
    if existing and _alive(existing):
        print(f"{name}: already running (pid {existing})")
        return existing

    log_path = LOG_DIR / f"{name}.log"
    log_file = open(log_path, "ab", buffering=0)

    # First fork: detach from the shell's process group/session.
    if os.fork() != 0:
        os.waitpid(-1, os.WNOHANG)
        return 0

    os.setsid()
    # Second fork: guarantee we can never reacquire a controlling terminal.
    if os.fork() != 0:
        os._exit(0)

    devnull = os.open(os.devnull, os.O_RDONLY)
    os.dup2(devnull, 0)
    os.dup2(log_file.fileno(), 1)
    os.dup2(log_file.fileno(), 2)

    process = subprocess.Popen(
        command,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    _pidfile(name).write_text(str(process.pid))
    (STATE_DIR / f"{name}.json").write_text(
        json.dumps({"pid": process.pid, "cmd": command, "log": str(log_path)})
    )
    os._exit(0)


def stop(name: str) -> None:
    pid = _read_pid(name)
    if not pid or not _alive(pid):
        print(f"{name}: not running")
        _pidfile(name).unlink(missing_ok=True)
        return
    os.kill(pid, signal.SIGTERM)
    for _ in range(50):
        if not _alive(pid):
            break
        time.sleep(0.1)
    if _alive(pid):
        os.kill(pid, signal.SIGKILL)
    print(f"{name}: stopped (pid {pid})")
    _pidfile(name).unlink(missing_ok=True)


def status() -> None:
    if not STATE_DIR.exists():
        print("no services tracked")
        return
    found = False
    for path in sorted(STATE_DIR.glob("*.pid")):
        name = path.stem
        pid = _read_pid(name) or 0
        state = "running" if pid and _alive(pid) else "dead"
        print(f"  {name:<22} {state:<8} pid={pid}")
        found = True
    if not found:
        print("no services tracked")


def main() -> int:
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        return 2
    if argv[0] == "--status":
        status()
        return 0
    if argv[0] == "--stop":
        for name in argv[1:]:
            stop(name)
        return 0
    if argv[0] == "--stop-all":
        for path in sorted(STATE_DIR.glob("*.pid")) if STATE_DIR.exists() else []:
            stop(path.stem)
        return 0
    start(argv[0], argv[1:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
