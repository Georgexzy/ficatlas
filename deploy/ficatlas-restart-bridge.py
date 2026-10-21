#!/usr/bin/env python3
"""Narrow host-side bridge for the FicAtlas admin restart button.

The API can write one fixed schedule document into the shared directory. This
root-owned service accepts only its restart_at timestamp, removes the request
before acting (so boot cannot trigger a restart loop), and runs a fixed reboot
command. It never executes input from the document as a command or arguments.
"""
import fcntl
import json
import logging
import math
import os
from pathlib import Path
import signal
import subprocess
import threading
import time

DIRECTORY = Path("/var/lib/ficatlas-restart-bridge")
REQUEST = DIRECTORY / "restart.json"
READY = DIRECTORY / "bridge.ready"
LOCK = DIRECTORY / "bridge.lock"
log = logging.getLogger("ficatlas-restart-bridge")
READY_MARKER = "ficatlas-restart-bridge-v2\n"
LATE_GRACE_SECONDS = 60


def write_status(value: dict) -> None:
    temp = DIRECTORY / ".bridge-status.tmp"
    temp.write_text(json.dumps(value))
    os.replace(temp, DIRECTORY / "bridge-status.json")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    stopping = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopping.set())
    signal.signal(signal.SIGINT, lambda *_: stopping.set())
    DIRECTORY.mkdir(mode=0o770, parents=True, exist_ok=True)
    LOCK.touch(mode=0o660, exist_ok=True)
    LOCK.chmod(0o660)
    READY.write_text(READY_MARKER)
    READY.chmod(0o644)
    log.info("restart bridge ready")
    last_request_id = None
    last_error = None
    try:
        while not stopping.is_set():
            due = None
            with LOCK.open("a+") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try:
                    request = json.loads(REQUEST.read_text())
                    if not isinstance(request, dict):
                        raise ValueError("restart request must be an object")
                    restart_at = request.get("restart_at")
                    request_id = request.get("id")
                    if (not isinstance(request_id, str)
                            or not isinstance(restart_at, (int, float))
                            or isinstance(restart_at, bool)
                            or not math.isfinite(restart_at)):
                        raise ValueError("restart request has no valid id or time")
                    if request_id != last_request_id:
                        write_status({"state": "armed", "id": request_id,
                                      "restart_at": restart_at, "at": time.time()})
                        last_request_id = request_id
                    last_error = None
                    if restart_at <= time.time() - LATE_GRACE_SECONDS:
                        REQUEST.unlink(missing_ok=True)
                        write_status({"state": "missed", "id": request_id,
                                      "restart_at": restart_at, "at": time.time()})
                        log.error("discarded a restart request more than %s seconds late", LATE_GRACE_SECONDS)
                    elif restart_at <= time.time():
                        due = request
                        REQUEST.unlink(missing_ok=True)
                        write_status({"state": "restarting", "id": request.get("id"),
                                      "started_at": time.time()})
                except FileNotFoundError:
                    last_request_id = None
                except (OSError, ValueError, TypeError, AttributeError) as exc:
                    error = str(exc)
                    if error != last_error:
                        log.error("ignoring unreadable restart request: %s", exc)
                        write_status({"state": "request-unreadable", "error": error,
                                      "at": time.time()})
                        last_error = error
                finally:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            if due:
                log.warning("scheduled host restart is due; rebooting now")
                result = subprocess.run(["/usr/bin/systemctl", "reboot"], check=False)
                write_status({"state": "reboot-command-failed", "id": due.get("id"),
                              "returncode": result.returncode, "at": time.time()})
                log.error("systemctl reboot returned %s", result.returncode)
            stopping.wait(1)
    finally:
        READY.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
