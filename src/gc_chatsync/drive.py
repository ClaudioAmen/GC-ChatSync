"""Google Drive operations wrapper using rclone.

Handles manifest operations, directory syncing, and remote staging for
GC-ChatSync.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("gc_chatsync.drive")

# Standard file exclusion patterns for Antigravity data
EXCLUDE_PATTERNS = [
    "*.db-wal",
    "*.db-shm",
    "installation_id",
    "antigravity_state.pbtxt",
    "cache/**",
    "crashes/**",
    "presence/**",
    "log/**",
    ".backups/**",
    "**/.system_generated/tasks/**",
    "**/.system_generated/steps/**",
    "**/.system_generated/messages/**",
]


class RcloneError(Exception):
    """Base exception for rclone operations."""
    def __init__(self, message: str, returncode: Optional[int] = None):
        super().__init__(message)
        self.returncode = returncode


NOT_FOUND_CODES = {3, 4}

RCLONE_PERF_FLAGS = [
    "--checkers", "4",
    "--transfers", "4",
    "--drive-chunk-size", "32M",
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
            antigravity_version=data.get("antigravity_version"),
        )

    @classmethod
    def create_now(
        cls,
        device_id: str,
        epoch: int = 1,
        active_device: Optional[str] = None,
        active_since_utc: Optional[str] = None,
        antigravity_version: Optional[str] = None,
    ) -> SyncManifest:
        now_utc = datetime.now(timezone.utc).isoformat()
        return cls(
            last_device=device_id,
            last_sync_utc=now_utc,
            epoch=epoch,
            active_device=active_device,
            active_since_utc=active_since_utc,
            antigravity_version=antigravity_version,
        )


class DriveClient:
    """Executes rclone commands to interact with Google Drive."""

    def __init__(self, remote_name: str = "gdrive", remote_folder: str = "GC-ChatSync"):
        self.remote_name = remote_name
        self.remote_folder = remote_folder.strip("/")
        self._rclone_bin = self._find_rclone()

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
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                input=input_data,
                timeout=timeout,
                check=False,
            )
            if res.returncode != 0:
                err_msg = res.stderr.strip() or f"rclone exited with code {res.returncode}"
                if log_errors and res.returncode not in NOT_FOUND_CODES:
                    logger.error(f"Command failed ({cmd}): {err_msg}")
                raise RcloneError(err_msg, returncode=res.returncode)
            return res.stdout
        except subprocess.TimeoutExpired as e:
            raise RcloneError(f"rclone command timed out after {timeout} seconds: {e}") from e

    def test_connection(self) -> bool:
        """Check if remote is accessible and authorized (fast directory ping without slow quota checks)."""
        try:
            self.run_cmd(["lsf", self.remote_base, "--max-depth", "1"], timeout=10, log_errors=False)
            return True
        except RcloneError as e:
            if e.returncode in NOT_FOUND_CODES:
                # Target folder does not exist yet on remote, but remote connection is verified
                return True
            logger.warning(f"Google Drive connection test failed: {e}")
            return False
        except Exception as e:
            logger.warning(f"Google Drive connection test failed: {e}")
            return False

    def get_manifest(self) -> Optional[SyncManifest]:
        """Fetch cloud manifest if it exists.
        
        Returns None if remote file does not exist (exit code 3 or 4).
        Raises RcloneError if network, auth, timeout, or JSON corruption occurred.
        """
        remote_path = f"{self.remote_base}/manifest.json"
        try:
            output = self.run_cmd(["cat", remote_path], timeout=25, log_errors=False)
            data = json.loads(output)
            return SyncManifest.from_dict(data)
        except RcloneError as e:
            if e.returncode in NOT_FOUND_CODES:
                return None
            raise
        except json.JSONDecodeError as e:
            logger.error(f"Corrupted remote manifest.json: {e}")
            raise RcloneError(f"Corrupted remote manifest.json: {e}") from e

    def put_manifest(self, manifest: SyncManifest) -> None:
        """Upload sync manifest to Google Drive."""
        remote_path = f"{self.remote_base}/manifest.json"
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, suffix=".json") as f:
            json.dump(manifest.to_dict(), f, indent=2)
            tmp_path = Path(f.name)
        try:
            self.run_cmd(["copyto", str(tmp_path), remote_path], timeout=60)
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
            self.run_cmd(["copyto", remote_file, str(local_staging_path)] + RCLONE_PERF_FLAGS, timeout=60, log_errors=False)
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
        self.run_cmd(["copyto", str(local_db_path), remote_file] + RCLONE_PERF_FLAGS, timeout=60)
        logger.info("Uploaded conversation_summaries.db to Google Drive")

    def pull_conversations(self, local_conversations_dir: Path) -> None:
        """Download new/updated conversation session databases from remote."""
        local_conversations_dir.mkdir(parents=True, exist_ok=True)
        remote_dir = f"{self.remote_base}/conversations"
        args = ["copy", remote_dir, str(local_conversations_dir), "--update", "--fast-list"] + RCLONE_PERF_FLAGS
        for pat in EXCLUDE_PATTERNS:
            args.extend(["--exclude", pat])
        try:
            self.run_cmd(args, timeout=180, log_errors=False)
        except RcloneError as e:
            if e.returncode in NOT_FOUND_CODES:
                logger.debug(f"Remote conversations folder not found on Drive (clean remote): {e}")
            else:
                raise

    def push_conversations(self, local_conversations_dir: Path) -> None:
        """Upload new/updated conversation session databases to remote."""
        if not local_conversations_dir.is_dir():
            return
        remote_dir = f"{self.remote_base}/conversations"
        args = ["copy", str(local_conversations_dir), remote_dir, "--update", "--fast-list"] + RCLONE_PERF_FLAGS
        for pat in EXCLUDE_PATTERNS:
            args.extend(["--exclude", pat])
        self.run_cmd(args, timeout=180)

    def pull_brain(self, local_brain_dir: Path) -> None:
        """Download new/updated brain artifacts and transcripts from remote."""
        local_brain_dir.mkdir(parents=True, exist_ok=True)
        remote_dir = f"{self.remote_base}/brain"
        args = ["copy", remote_dir, str(local_brain_dir), "--update", "--fast-list"] + RCLONE_PERF_FLAGS
        for pat in EXCLUDE_PATTERNS:
            args.extend(["--exclude", pat])
        try:
            self.run_cmd(args, timeout=300, log_errors=False)
        except RcloneError as e:
            if e.returncode in NOT_FOUND_CODES:
                logger.debug(f"Remote brain folder not found on Drive (clean remote): {e}")
            else:
                raise

    def push_brain(self, local_brain_dir: Path) -> None:
        """Upload new/updated brain artifacts and transcripts to remote."""
        if not local_brain_dir.is_dir():
            return
        remote_dir = f"{self.remote_base}/brain"
        args = ["copy", str(local_brain_dir), remote_dir, "--update", "--fast-list"] + RCLONE_PERF_FLAGS
        for pat in EXCLUDE_PATTERNS:
            args.extend(["--exclude", pat])
        self.run_cmd(args, timeout=300)

    def pull_projects(self, local_projects_staging: Path) -> None:
        """Download remote project definitions into a staging directory for translation."""
        local_projects_staging.mkdir(parents=True, exist_ok=True)
        remote_dir = f"{self.remote_base}/projects"
        args = ["copy", remote_dir, str(local_projects_staging), "--update", "--fast-list"] + RCLONE_PERF_FLAGS
        try:
            self.run_cmd(args, timeout=120, log_errors=False)
        except RcloneError as e:
            if e.returncode in NOT_FOUND_CODES:
                logger.debug(f"Remote projects folder not found on Drive (clean remote): {e}")
            else:
                raise

    def push_projects(self, local_projects_dir: Path) -> None:
        """Upload local project definitions to Google Drive."""
        if not local_projects_dir.is_dir():
            return
        remote_dir = f"{self.remote_base}/projects"
        args = ["copy", str(local_projects_dir), remote_dir, "--update", "--fast-list"] + RCLONE_PERF_FLAGS
        self.run_cmd(args, timeout=120)
