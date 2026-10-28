"""Process locking and session dirty-state tracking for GC-ChatSync.

Prevents concurrent sync operations on the same machine and detects
unclean exits (crashes, sudden shutdowns) to protect SQLite databases.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

from gc_chatsync.config import get_config_dir, get_system_name

logger = logging.getLogger("gc_chatsync.lock")


def is_pid_running(pid: int) -> bool:
    """Check whether a process with the given PID is currently active."""
    if pid <= 0:
        return False
    if get_system_name() == "windows":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            SYNCHRONIZE = 0x00100000
            handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            # Fallback for Windows without admin privileges
            try:
                import subprocess
                out = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=3,
                )
                return str(pid) in out.stdout
            except Exception:
                return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            # Process exists but is owned by another user
            return True
        except OSError:
            return False


class LockError(Exception):
    """Raised when an active GC-ChatSync process lock cannot be acquired."""
    pass


class ProcessLock:
    """Cross-platform advisory process lock file with reentrancy support."""

    _depth = 0

    def __init__(self, name: str = "gc-chatsync.lock"):
        self.lock_file = get_config_dir() / name

    def acquire(self) -> bool:
        """Attempt to acquire the process lock.

        Raises LockError if another active process holds the lock.
        Stale locks from crashed processes are automatically recovered.
        """
        if ProcessLock._depth > 0:
            ProcessLock._depth += 1
            return True

        if self.lock_file.is_file():
            try:
                data = json.loads(self.lock_file.read_text(encoding="utf-8"))
                holder_pid = data.get("pid")
                if holder_pid and is_pid_running(holder_pid) and holder_pid != os.getpid():
                    holder_name = data.get("command", "another process")
                    started = data.get("started_utc", "unknown")
                    raise LockError(
                        f"Another GC-ChatSync instance is already running (PID: {holder_pid}, "
                        f"started: {started}, command: {holder_name})."
                    )
                else:
                    logger.warning(f"Found stale lock file from inactive PID {holder_pid}. Recovering lock.")
            except (json.JSONDecodeError, OSError) as e:
                logger.warning(f"Lock file corrupted or unreadable ({e}). Overriding.")

        # Write lock info
        lock_data = {
            "pid": os.getpid(),
            "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "command": " ".join(sys.argv),
        }
        try:
            self.lock_file.parent.mkdir(parents=True, exist_ok=True)
            self.lock_file.write_text(json.dumps(lock_data, indent=2), encoding="utf-8")
            ProcessLock._depth = 1
            logger.debug(f"Acquired process lock: {self.lock_file} (PID {os.getpid()})")
            return True
        except Exception as e:
            logger.error(f"Failed to create lock file {self.lock_file}: {e}")
            return False

    def release(self) -> None:
        """Release the process lock if held by this process."""
        if ProcessLock._depth <= 0:
            return

        ProcessLock._depth -= 1
        if ProcessLock._depth > 0:
            return

        if self.lock_file.is_file():
            try:
                data = json.loads(self.lock_file.read_text(encoding="utf-8"))
                if data.get("pid") == os.getpid():
                    self.lock_file.unlink(missing_ok=True)
                    logger.debug(f"Released process lock: {self.lock_file}")
            except Exception as e:
                logger.debug(f"Could not cleanly release lock: {e}")

    def __enter__(self) -> ProcessLock:
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()


# ---------------------------------------------------------------------------
# Dirty state tracker (crash / abrupt shutdown detection)
# ---------------------------------------------------------------------------

def get_dirty_state_file() -> Path:
    """Return path to .session_dirty flag."""
    return get_config_dir() / ".session_dirty"


def is_session_dirty() -> bool:
    """Check if the previous session exited uncleanly before pushing to Drive."""
    return get_dirty_state_file().is_file()


def get_dirty_session_info() -> Optional[dict]:
    """Retrieve details of the uncleanly exited session if present."""
    dirty_file = get_dirty_state_file()
    if not dirty_file.is_file():
        return None
    try:
        return json.loads(dirty_file.read_text(encoding="utf-8"))
    except Exception:
        return {"corrupted": True}


def mark_session_dirty(device_id: str) -> None:
    """Flag local session as dirty (modified locally, pending cloud push)."""
    dirty_file = get_dirty_state_file()
    dirty_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "device_id": device_id,
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "pending_push",
    }
    dirty_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.debug("Marked session state as dirty (pending push).")


def clear_session_dirty() -> None:
    """Clear dirty state flag after successful cloud push."""
    dirty_file = get_dirty_state_file()
    if dirty_file.is_file():
        try:
            dirty_file.unlink(missing_ok=True)
            logger.debug("Cleared dirty session state.")
        except Exception as e:
            logger.debug(f"Could not clear dirty flag: {e}")
