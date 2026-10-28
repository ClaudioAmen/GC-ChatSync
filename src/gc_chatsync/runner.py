"""Sync lifecycle runner and Antigravity process orchestrator."""

from __future__ import annotations

import json
import logging
import sqlite3
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from gc_chatsync.config import (
    AppConfig,
    find_antigravity_executable,
    get_default_antigravity_projects_dir,
    get_system_name,
)
from gc_chatsync.drive import DriveClient, SyncManifest
from gc_chatsync.lock import (
    ProcessLock,
    clear_session_dirty,
    is_session_dirty,
    mark_session_dirty,
)
from gc_chatsync.merger import checkpoint_db, merge_summaries_databases
from gc_chatsync.project_mapper import (
    translate_project_json,
    translate_workspace_uris_string,
)

logger = logging.getLogger("gc_chatsync.runner")


@dataclass
class SyncStatus:
    """Status summary of the local and cloud environment."""
    device_id: str
    os_name: str
    data_dir: str
    projects_dir: str
    bin_path: str
    remote_name: str
    drive_connected: bool
    cloud_last_device: Optional[str] = None
    cloud_last_sync_utc: Optional[str] = None
    cloud_epoch: Optional[int] = None
    in_sync: bool = False
    session_dirty: bool = False


class SyncRunner:
    """Main orchestrator for GC-ChatSync operations."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.data_dir = Path(config.antigravity_data_dir).resolve()
        self.projects_dir = get_default_antigravity_projects_dir().resolve()
        self.drive = DriveClient(
            remote_name=config.rclone_remote,
            remote_folder=config.remote_folder,
        )

    def get_status(self) -> SyncStatus:
        """Query local and cloud status."""
        connected = self.drive.test_connection()
        cloud_last_dev = None
        cloud_last_utc = None
        cloud_epoch = None
        in_sync = False

        if connected:
            manifest = self.drive.get_manifest()
            if manifest:
                cloud_last_dev = manifest.last_device
                cloud_last_utc = manifest.last_sync_utc
                cloud_epoch = manifest.epoch
                in_sync = (cloud_last_dev == self.config.device_id)

        return SyncStatus(
            device_id=self.config.device_id,
            os_name=get_system_name(),
            data_dir=str(self.data_dir),
            projects_dir=str(self.projects_dir),
            bin_path=self.config.antigravity_bin_path,
            remote_name=self.config.rclone_remote,
            drive_connected=connected,
            cloud_last_device=cloud_last_dev,
            cloud_last_sync_utc=cloud_last_utc,
            cloud_epoch=cloud_epoch,
            in_sync=in_sync,
            session_dirty=is_session_dirty(),
        )

    def pull(self, force: bool = False) -> bool:
        """Pull deltas from Google Drive and merge them into local storage.

        Returns True if sync was performed or already up to date, False if skipped due to error.
        Protected by ProcessLock.
        """
        with ProcessLock():
            logger.info("Checking Google Drive status...")
            if not self.drive.test_connection():
                logger.warning("Google Drive is unreachable. Skipping pull (working offline).")
                return False

            manifest = self.drive.get_manifest()
            if manifest is None and not force:
                logger.info("Cloud storage has no manifest (clean remote). Skipping pull.")
                return True

            if manifest and manifest.last_device == self.config.device_id and not force:
                logger.info(
                    f"Local device ({self.config.device_id}) is already the latest author in cloud "
                    f"(epoch {manifest.epoch}). No pull needed."
                )
                return True

            logger.info("Pulling conversations and brain artifacts from Google Drive...")
            conv_dir = self.data_dir / "conversations"
            brain_dir = self.data_dir / "brain"
            self.drive.pull_conversations(conv_dir)
            self.drive.pull_brain(brain_dir)

            current_os = get_system_name()

            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_path = Path(tmp_dir)

                # 2. Pull remote projects into staging and translate URIs
                remote_projects_staging = tmp_path / "remote_projects"
                self.drive.pull_projects(remote_projects_staging)

                if remote_projects_staging.is_dir():
                    self.projects_dir.mkdir(parents=True, exist_ok=True)
                    for proj_file in remote_projects_staging.glob("*.json"):
                        try:
                            with open(proj_file, "r", encoding="utf-8") as f:
                                raw_data = json.load(f)
                            translated_data = translate_project_json(raw_data, target_os=current_os)
                            target_file = self.projects_dir / proj_file.name
                            with open(target_file, "w", encoding="utf-8") as f:
                                json.dump(translated_data, f, indent=2)
                            logger.debug(f"Translated and saved project definition: {target_file.name}")
                        except Exception as e:
                            logger.warning(f"Failed to translate project file {proj_file.name}: {e}")

                # 3. Pull remote conversation_summaries.db and merge
                remote_db_staging = tmp_path / "remote_summaries.db"
                has_remote_db = self.drive.pull_summaries(remote_db_staging)

                local_db = self.data_dir / "conversation_summaries.db"
                if has_remote_db and remote_db_staging.is_file():
                    merge_res = merge_summaries_databases(local_db, remote_db_staging)
                    logger.info(
                        f"Index merge complete: {merge_res.inserted} inserted, "
                        f"{merge_res.updated} updated, {merge_res.unchanged} unchanged."
                    )

                    # Translate workspace_uris in local database to match current OS paths
                    self._translate_local_db_workspace_uris(local_db)
                else:
                    logger.info("No remote conversation_summaries.db found on Drive.")

            return True

    def _translate_local_db_workspace_uris(self, db_path: Path) -> None:
        """Translate workspace_uris in conversation_summaries table to current OS paths."""
        if not db_path.is_file():
            return
        checkpoint_db(db_path)
        current_os = get_system_name()
        conn = sqlite3.connect(db_path)
        try:
            cursor = conn.cursor()
            rows = cursor.execute(
                "SELECT conversation_id, workspace_uris FROM conversation_summaries WHERE workspace_uris != '';"
            ).fetchall()
            for cid, uris_str in rows:
                translated = translate_workspace_uris_string(uris_str, target_os=current_os)
                if translated != uris_str:
                    cursor.execute(
                        "UPDATE conversation_summaries SET workspace_uris = ? WHERE conversation_id = ?;",
                        (translated, cid),
                    )
            conn.commit()
            logger.debug("Translated workspace_uris in local summaries database.")
        except sqlite3.Error as e:
            logger.warning(f"Failed to translate workspace_uris in SQLite index: {e}")
        finally:
            conn.close()
            checkpoint_db(db_path)

    def push(self) -> bool:
        """Push local conversations, projects, and merged index to Google Drive.
        
        Protected by ProcessLock. Updates monotonic epoch and clears dirty session flag.
        """
        with ProcessLock():
            logger.info("Connecting to Google Drive for push...")
            if not self.drive.test_connection():
                logger.warning("Google Drive is unreachable. Push postponed.")
                return False

            # 1. Flush local SQLite WAL journals
            local_db = self.data_dir / "conversation_summaries.db"
            checkpoint_db(local_db)

            conv_dir = self.data_dir / "conversations"
            if conv_dir.is_dir():
                for db_file in conv_dir.glob("*.db"):
                    checkpoint_db(db_file)

            # 2. Upload conversations and brain
            brain_dir = self.data_dir / "brain"
            logger.info("Uploading conversations and brain artifacts...")
            self.drive.push_conversations(conv_dir)
            self.drive.push_brain(brain_dir)

            # 3. Upload project definitions
            if self.projects_dir.is_dir():
                logger.info("Uploading project configurations...")
                self.drive.push_projects(self.projects_dir)

            # 4. Upload conversation_summaries.db
            if local_db.is_file():
                logger.info("Uploading conversation_summaries.db...")
                self.drive.push_summaries(local_db)

            # 5. Update cloud manifest token with incremented epoch
            existing_manifest = self.drive.get_manifest()
            next_epoch = (existing_manifest.epoch + 1) if existing_manifest else 1
            new_manifest = SyncManifest.create_now(self.config.device_id, epoch=next_epoch)
            self.drive.put_manifest(new_manifest)

            # 6. Clear dirty state flag
            clear_session_dirty()
            logger.info(f"Sync complete! State updated to '{self.config.device_id}' (epoch {next_epoch}).")
            return True

    def launch_app(self, extra_args: Optional[list[str]] = None) -> int:
        """Launch Antigravity executable and wait for it to exit."""
        bin_path_str = self.config.antigravity_bin_path
        if not bin_path_str:
            found = find_antigravity_executable()
            if found:
                bin_path_str = str(found)
                self.config.antigravity_bin_path = bin_path_str

        if not bin_path_str or not Path(bin_path_str).is_file():
            logger.error("Antigravity executable not found. Please set antigravity_bin_path in config.json.")
            print("\n❌ Error: Antigravity executable not found!")
            print("Configure the executable path using: gc-chatsync setup\n")
            return 1

        cmd = [bin_path_str] + (extra_args or [])
        logger.info(f"Starting Antigravity: {bin_path_str}")
        print(f"🚀 Launching Antigravity ({bin_path_str})...")

        try:
            proc = subprocess.Popen(cmd)
            proc.wait()
            logger.info("Antigravity closed gracefully.")
            return proc.returncode
        except Exception as e:
            logger.error(f"Failed to launch Antigravity: {e}")
            return 1

    def run(self, extra_args: Optional[list[str]] = None) -> int:
        """Full lifecycle: PULL -> LAUNCH -> PUSH with concurrency locking and dirty session recovery."""
        with ProcessLock():
            # If an earlier run crashed before pushing, notify and push residual changes
            if is_session_dirty():
                logger.warning("Unclean exit detected from previous session. Pushing residual changes first...")
                print("⚠️ [GC-ChatSync] Unfinished sync detected from previous session. Pushing residual changes first...")
                self.push()

            print("🌌 [GC-ChatSync] Step 1/3: Checking & Pulling from Google Drive...")
            self.pull()

            # Mark session dirty before launching the editor
            mark_session_dirty(self.config.device_id)

            print("🌌 [GC-ChatSync] Step 2/3: Running Antigravity...")
            exit_code = self.launch_app(extra_args)

            print("🌌 [GC-ChatSync] Step 3/3: Pushing updates to Google Drive...")
            push_success = self.push()
            if push_success:
                clear_session_dirty()

            print("🌌 [GC-ChatSync] All done!")
            return exit_code
