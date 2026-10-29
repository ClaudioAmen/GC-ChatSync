"""Google Drive operations wrapper using rclone.

Handles manifest operations, directory syncing, and remote staging for
GC-ChatSync.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("gc_chatsync.drive")

# Anchored file exclusion patterns for Antigravity data
EXCLUDE_PATTERNS = [
    "*.db-wal",
    "*.db-shm",
    "installation_id",
    "antigravity_state.pbtxt",
    "**/.system_generated/steps/**",
    "**/.system_generated/tasks/**",
    "**/.system_generated/messages/**",
    "**/.system_generated/subagents/**",
    "**/.system_generated/logs/chunks/**",
    "**/.system_generated/crashes/**",
    "**/.system_generated/presence/**",
    "**/.backups/**",
    "**/.tempmediaStorage/**",
    "**/tempmediaStorage/**",
]


def get_exclude_patterns(sync_full_brain: bool = False) -> List[str]:
    """Return anchored exclude patterns, optionally omitting steps/tasks/messages if sync_full_brain is True."""
    if sync_full_brain:
        return [
            p for p in EXCLUDE_PATTERNS
            if not any(k in p for k in ("steps", "tasks", "messages", "subagents"))
        ]
    return list(EXCLUDE_PATTERNS)


class RcloneError(Exception):
    """Base exception for rclone operations."""
    def __init__(self, message: str, returncode: Optional[int] = None):
        super().__init__(message)
        self.returncode = returncode


class ManifestCorruptedError(RcloneError):
    """Raised when remote manifest.json exists on Drive but contains invalid or corrupted JSON."""
    pass


NOT_FOUND_CODES = {3, 4}

RCLONE_PERF_FLAGS = [
    "--checkers", "16",
    "--transfers", "8",
    "--drive-chunk-size", "64M",
    "--drive-pacer-min-sleep", "10ms",
    "--drive-pacer-burst", "200",
]


class RcloneNotFoundError(RcloneError):
    """Raised when rclone executable is not found on PATH."""
    pass


class DriveConnectionError(RcloneError):
    """Raised when connection to Google Drive fails."""
    pass


@dataclass
class SyncManifest:
    """Cloud state token tracking latest synchronizing device, revision epoch, and active witness."""
    last_device: str
    last_sync_utc: str
    epoch: int = 1
    version: int = 1
    active_device: Optional[str] = None
    active_since_utc: Optional[str] = None
    active_heartbeat_utc: Optional[str] = None
    antigravity_version: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SyncManifest:
        return cls(
            last_device=str(data.get("last_device", "unknown")),
            last_sync_utc=str(data.get("last_sync_utc", "")),
            epoch=int(data.get("epoch", 1)),
            version=int(data.get("version", 1)),
            active_device=data.get("active_device"),
            active_since_utc=data.get("active_since_utc"),
            active_heartbeat_utc=data.get("active_heartbeat_utc"),
            antigravity_version=data.get("antigravity_version"),
        )

    @classmethod
    def create_now(
        cls,
        device_id: str,
        epoch: int = 1,
        active_device: Optional[str] = None,
        active_since_utc: Optional[str] = None,
        active_heartbeat_utc: Optional[str] = None,
        antigravity_version: Optional[str] = None,
    ) -> SyncManifest:
        now_utc = datetime.now(timezone.utc).isoformat()
        return cls(
            last_device=device_id,
            last_sync_utc=now_utc,
            epoch=epoch,
            active_device=active_device,
            active_since_utc=active_since_utc,
            active_heartbeat_utc=active_heartbeat_utc,
            antigravity_version=antigravity_version,
        )


class DriveClient:
    """Executes rclone commands to interact with Google Drive."""

    def __init__(self, remote_name: str = "gdrive", remote_folder: str = "GC-ChatSync"):
        self.remote_name = remote_name
        self.remote_folder = remote_folder.strip("/")
        self._rclone_bin = self._find_rclone()
        self._cached_manifest: Optional[SyncManifest] = None
        self._cached_manifest_time: float = 0.0
        self._manifest_lock = threading.Lock()

    @staticmethod
    def _find_rclone() -> str:
        bin_path = shutil.which("rclone")
        if not bin_path:
            raise RcloneNotFoundError(
                "rclone binary not found. Please install rclone (https://rclone.org)."
            )
        return bin_path

    @property
    def remote_base(self) -> str:
        """Full remote path (e.g. 'gdrive:GC-ChatSync')."""
        return f"{self.remote_name}:{self.remote_folder}"

    def run_cmd(
        self,
        args: List[str],
        timeout: int = 60,
        input_data: Optional[str] = None,
        log_errors: bool = True,
    ) -> str:
        """Run an rclone command and return its standard output."""
        cmd = [self._rclone_bin] + args
        logger.debug(f"Executing: {' '.join(cmd)}")
        extra_kwargs = {}
        if sys.platform == "win32":
            extra_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        if input_data is not None:
            extra_kwargs["input"] = input_data
        else:
            extra_kwargs["stdin"] = subprocess.DEVNULL
        t0 = time.perf_counter()
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                **extra_kwargs,
            )
            if res.returncode != 0:
                err_msg = res.stderr.strip() or f"rclone exited with code {res.returncode}"
                if log_errors and res.returncode not in NOT_FOUND_CODES:
                    logger.error(f"Command failed ({cmd}): {err_msg}")
                raise RcloneError(err_msg, returncode=res.returncode)
            return res.stdout
        except subprocess.TimeoutExpired as e:
            raise RcloneError(f"rclone command timed out after {timeout} seconds: {e}") from e
        except (FileNotFoundError, OSError) as e:
            if log_errors:
                logger.error(f"rclone executable '{self._rclone_bin}' could not be executed: {e}")
            raise RcloneNotFoundError(f"rclone executable '{self._rclone_bin}' could not be executed: {e}") from e
        finally:
            elapsed = time.perf_counter() - t0
            cmd_action = args[0] if args else "unknown"
            logger.debug(f"rclone {cmd_action} completed in {elapsed:.2f}s")

    def run_cmd_streaming(
        self,
        args: List[str],
        progress_cb: Optional[Callable[[str], None]] = None,
        timeout: int = 600,
        log_errors: bool = True,
    ) -> str:
        """Run an rclone command with live progress parsing via --use-json-log.

        Emits active file transfers and status lines to `progress_cb`.
        """
        if progress_cb is None:
            return self.run_cmd(args, timeout=timeout, log_errors=log_errors)

        cmd = [self._rclone_bin] + list(args) + ["-v", "--use-json-log", "--stats", "1s"]
        logger.debug(f"Executing (streaming): {' '.join(cmd)}")

        extra_kwargs = {}
        if sys.platform == "win32":
            extra_kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        extra_kwargs["stdin"] = subprocess.DEVNULL

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                **extra_kwargs,
            )
        except Exception as e:
            raise RcloneError(f"Failed to start rclone: {e}") from e

        all_lines: List[str] = []
        error_lines: List[str] = []

        def reader():
            for raw_line in iter(proc.stdout.readline, ""):
                line = raw_line.strip()
                if not line:
                    continue
                all_lines.append(line)
                try:
                    data = json.loads(line)
                    lvl = data.get("level")
                    msg = data.get("msg", "")
                    obj = data.get("object")
                    stats = data.get("stats")

                    if lvl == "error":
                        error_lines.append(f"{obj or 'error'}: {msg}")
                    elif obj and msg:
                        clean_msg = f"{obj} ({msg})"
                        progress_cb(clean_msg)
                    elif stats:
                        transfers = stats.get("transfers", 0)
                        total_transfers = stats.get("totalTransfers", 0)
                        bytes_done = stats.get("bytes", 0)
                        total_bytes = stats.get("totalBytes", 0)
                        pct = int((bytes_done / total_bytes) * 100) if total_bytes > 0 else 0
                        speed = stats.get("speed", 0)
                        speed_mb = f"{speed / (1024 * 1024):.1f} MB/s" if speed > 0 else ""
                        status_str = f"{transfers}/{total_transfers} files ({pct}%) {speed_mb}".strip()
                        progress_cb(status_str)
                except Exception:
                    if "error" in line.lower():
                        error_lines.append(line)

        reader_thread = threading.Thread(target=reader, daemon=True)
        reader_thread.start()
        reader_thread.join(timeout=timeout)

        if reader_thread.is_alive():
            proc.kill()
            raise RcloneError(f"rclone command timed out after {timeout} seconds")

        proc.wait()
        if proc.returncode != 0:
            err_msg = "\n".join(error_lines) or f"rclone exited with code {proc.returncode}"
            if log_errors and proc.returncode not in NOT_FOUND_CODES:
                logger.error(f"Command failed ({cmd}): {err_msg}")
            raise RcloneError(err_msg, returncode=proc.returncode)

        return "\n".join(all_lines)

    def test_connection(self) -> bool:
        """Check if remote is accessible and authorized.
        
        Fetches the manifest with force_refresh=True, doubling as an auth/connectivity probe.
        If the manifest is corrupted, Drive is still accessible, but an explicit warning is logged.
        """
        try:
            self.get_manifest(force_refresh=True)
            return True
        except ManifestCorruptedError as e:
            logger.warning(f"Google Drive is reachable, but manifest is corrupted: {e}")
            return True
        except RcloneError as e:
            if e.returncode in NOT_FOUND_CODES:
                return True
            logger.warning(f"Google Drive connection test failed: {e}")
            return False
        except Exception as e:
            logger.warning(f"Google Drive connection test failed: {e}")
            return False

    def get_manifest(self, force_refresh: bool = False) -> Optional[SyncManifest]:
        """Fetch cloud manifest if it exists.
        
        Returns None if remote file does not exist (exit code 3 or 4).
        Raises ManifestCorruptedError if remote manifest has invalid or corrupted JSON.
        Raises RcloneError if network, auth, or timeout occurred.
        Caches manifest for 15s to avoid redundant round-trips within the same sync cycle.
        """
        with self._manifest_lock:
            now = time.time()
            if not force_refresh and self._cached_manifest_time and (now - self._cached_manifest_time < 15.0):
                return self._cached_manifest

            remote_path = f"{self.remote_base}/manifest.json"
            try:
                output = self.run_cmd(["cat", remote_path], timeout=60, log_errors=False)
                data = json.loads(output)
                manifest = SyncManifest.from_dict(data)
                self._cached_manifest = manifest
                self._cached_manifest_time = now
                return manifest
            except RcloneError as e:
                if e.returncode in NOT_FOUND_CODES:
                    self._cached_manifest = None
                    self._cached_manifest_time = now
                    return None
                raise
            except json.JSONDecodeError as e:
                logger.error(f"Corrupted remote manifest.json: {e}")
                raise ManifestCorruptedError(f"Corrupted remote manifest.json: {e}") from e
            except Exception as e:
                if not isinstance(e, RcloneError):
                    logger.error(f"Corrupted remote manifest.json: {e}")
                    raise ManifestCorruptedError(f"Corrupted remote manifest.json: {e}") from e
                raise

    def get_manifest_backup(self) -> Optional[SyncManifest]:
        """Fetch cloud manifest.json.bak if it exists."""
        remote_path = f"{self.remote_base}/manifest.json.bak"
        try:
            output = self.run_cmd(["cat", remote_path], timeout=60, log_errors=False)
            data = json.loads(output)
            return SyncManifest.from_dict(data)
        except Exception:
            return None

    def put_manifest(self, manifest: SyncManifest) -> None:
        """Upload sync manifest to Google Drive, maintaining a safety .bak on remote."""
        with self._manifest_lock:
            remote_path = f"{self.remote_base}/manifest.json"
            remote_bak = f"{self.remote_base}/manifest.json.bak"
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".json") as f:
                json.dump(manifest.to_dict(), f, indent=2)
                tmp_path = Path(f.name)
            try:
                self.run_cmd(["copyto", str(tmp_path), remote_path], timeout=60)
                try:
                    self.run_cmd(["copyto", str(tmp_path), remote_bak], timeout=60, log_errors=False)
                except Exception as bak_err:
                    logger.debug(f"Could not update remote manifest backup: {bak_err}")
                self._cached_manifest = manifest
                self._cached_manifest_time = time.time()
                logger.info(f"Updated remote manifest (last_device={manifest.last_device}, epoch={manifest.epoch})")
            finally:
                try:
                    tmp_path.unlink()
                except Exception:
                    pass

    def pull_summaries(self, local_staging_path: Path) -> bool:
        """Download remote conversation_summaries.db to local staging file."""
        remote_file = f"{self.remote_base}/conversation_summaries.db"
        local_staging_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.run_cmd(["copyto", remote_file, str(local_staging_path)] + RCLONE_PERF_FLAGS, timeout=120, log_errors=False)
            return local_staging_path.is_file()
        except RcloneError as e:
            if e.returncode in NOT_FOUND_CODES:
                logger.debug(f"Remote conversation_summaries.db not found on Drive (clean remote): {e}")
                return False
            raise

    def push_summaries(self, local_db_path: Path) -> None:
        """Upload merged local conversation_summaries.db to Google Drive."""
        if not local_db_path.is_file():
            raise FileNotFoundError(f"Local database not found: {local_db_path}")
        remote_file = f"{self.remote_base}/conversation_summaries.db"
        self.run_cmd(["copyto", str(local_db_path), remote_file] + RCLONE_PERF_FLAGS, timeout=120)
        logger.info("Uploaded conversation_summaries.db to Google Drive")

    def pull_conversations(
        self,
        local_conversations_dir: Path,
        progress_cb: Optional[Callable[[str], None]] = None,
        sync_full_brain: bool = False,
    ) -> None:
        """Download new/updated conversation session databases from remote."""
        local_conversations_dir.mkdir(parents=True, exist_ok=True)
        remote_dir = f"{self.remote_base}/conversations"
        args = ["copy", remote_dir, str(local_conversations_dir), "--update", "--fast-list"] + RCLONE_PERF_FLAGS
        for pat in get_exclude_patterns(sync_full_brain=sync_full_brain):
            args.extend(["--exclude", pat])
        try:
            self.run_cmd_streaming(args, progress_cb=progress_cb, timeout=600, log_errors=False)
        except RcloneError as e:
            if e.returncode in NOT_FOUND_CODES:
                logger.debug(f"Remote conversations folder not found on Drive (clean remote): {e}")
            else:
                raise

    def push_conversations(
        self,
        local_conversations_dir: Path,
        progress_cb: Optional[Callable[[str], None]] = None,
        sync_full_brain: bool = False,
    ) -> None:
        """Upload new/updated conversation session databases to remote."""
        if not local_conversations_dir.is_dir():
            return
        remote_dir = f"{self.remote_base}/conversations"
        args = ["copy", str(local_conversations_dir), remote_dir, "--update", "--fast-list"] + RCLONE_PERF_FLAGS
        for pat in get_exclude_patterns(sync_full_brain=sync_full_brain):
            args.extend(["--exclude", pat])
        self.run_cmd_streaming(args, progress_cb=progress_cb, timeout=600)

    def pull_brain(
        self,
        local_brain_dir: Path,
        progress_cb: Optional[Callable[[str], None]] = None,
        sync_full_brain: bool = False,
    ) -> None:
        """Download new/updated brain artifacts and transcripts from remote."""
        local_brain_dir.mkdir(parents=True, exist_ok=True)
        remote_dir = f"{self.remote_base}/brain"
        args = ["copy", remote_dir, str(local_brain_dir), "--update", "--fast-list"] + RCLONE_PERF_FLAGS
        for pat in get_exclude_patterns(sync_full_brain=sync_full_brain):
            args.extend(["--exclude", pat])
        try:
            self.run_cmd_streaming(args, progress_cb=progress_cb, timeout=600, log_errors=False)
        except RcloneError as e:
            if e.returncode in NOT_FOUND_CODES:
                logger.debug(f"Remote brain folder not found on Drive (clean remote): {e}")
            else:
                raise

    def push_brain(
        self,
        local_brain_dir: Path,
        progress_cb: Optional[Callable[[str], None]] = None,
        sync_full_brain: bool = False,
    ) -> None:
        """Upload new/updated brain artifacts and transcripts to remote."""
        if not local_brain_dir.is_dir():
            return
        remote_dir = f"{self.remote_base}/brain"
        args = ["copy", str(local_brain_dir), remote_dir, "--update", "--fast-list"] + RCLONE_PERF_FLAGS
        for pat in get_exclude_patterns(sync_full_brain=sync_full_brain):
            args.extend(["--exclude", pat])
        self.run_cmd_streaming(args, progress_cb=progress_cb, timeout=600)

    def pull_projects(self, local_projects_staging: Path, progress_cb: Optional[Callable[[str], None]] = None) -> None:
        """Download remote project definitions into a staging directory for translation."""
        local_projects_staging.mkdir(parents=True, exist_ok=True)
        remote_dir = f"{self.remote_base}/projects"
        args = ["copy", remote_dir, str(local_projects_staging), "--update", "--fast-list"] + RCLONE_PERF_FLAGS
        try:
            self.run_cmd_streaming(args, progress_cb=progress_cb, timeout=180, log_errors=False)
        except RcloneError as e:
            if e.returncode in NOT_FOUND_CODES:
                logger.debug(f"Remote projects folder not found on Drive (clean remote): {e}")
            else:
                raise

    def push_projects(self, local_projects_dir: Path, progress_cb: Optional[Callable[[str], None]] = None) -> None:
        """Upload local project definitions to Google Drive."""
        if not local_projects_dir.is_dir():
            return
        remote_dir = f"{self.remote_base}/projects"
        args = ["copy", str(local_projects_dir), remote_dir, "--update", "--fast-list"] + RCLONE_PERF_FLAGS
        self.run_cmd_streaming(args, progress_cb=progress_cb, timeout=180)
