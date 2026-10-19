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
    READY.write_text("ready\n")
    READY.chmod(0o644)
    log.info("restart bridge ready")
    try:
        while not stopping.is_set():
            due = None
            with LOCK.open("a+") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                try:
                    request = json.loads(REQUEST.read_text())
                    restart_at = request.get("restart_at")
                    if isinstance(restart_at, (int, float)) and restart_at <= time.time():
                        due = request
                        REQUEST.unlink(missing_ok=True)
                        write_status({"state": "restarting", "id": request.get("id"),
                                      "started_at": time.time()})
                except FileNotFoundError:
                    pass
                except (ValueError, TypeError, AttributeError) as exc:
                    log.error("ignoring invalid restart request: %s", exc)
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
