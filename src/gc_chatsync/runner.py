"""Sync lifecycle runner and Antigravity process orchestrator."""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from gc_chatsync.config import (
    AppConfig,
    find_antigravity_executable,
    get_antigravity_version,
    get_default_antigravity_projects_dir,
    get_system_name,
)
from gc_chatsync.drive import DriveClient, RcloneError, SyncManifest
from gc_chatsync.lock import (
    ProcessLock,
    clear_session_dirty,
    is_session_dirty,
    mark_session_dirty,
)
from gc_chatsync.merger import (
    DatabaseMergeError,
    are_all_dbs_accessible,
    checkpoint_db,
    create_backup,
    is_db_exclusive_accessible,
    merge_summaries_databases,
    snapshot_db,
)
from gc_chatsync.project_mapper import (
    translate_project_json,
    translate_workspace_uris_string,
)

logger = logging.getLogger("gc_chatsync.runner")


def is_antigravity_process_running() -> bool:
    """Check if any Antigravity application process is currently running on the system."""
    current_pid = os.getpid()
    if sys.platform == "win32":
        try:
            out = subprocess.check_output(
                ["tasklist", "/FI", "IMAGENAME eq Antigravity.exe", "/NH"],
                text=True,
                timeout=5,
            )
            return "antigravity.exe" in out.lower()
        except Exception:
            return False

    proc_dir = Path("/proc")
    if not proc_dir.is_dir():
        return False

    for entry in proc_dir.iterdir():
        if entry.is_dir() and entry.name.isdigit():
            pid = int(entry.name)
            if pid == current_pid:
                continue
            try:
                cmdline = (entry / "cmdline").read_bytes()
                if not cmdline:
                    continue
                cmd_str = cmdline.replace(b"\x00", b" ").decode("utf-8", errors="ignore").lower()
                if "antigravity" in cmd_str and "gc-chatsync" not in cmd_str and "python" not in cmd_str:
                    if "/antigravity" in cmd_str or "antigravity.exe" in cmd_str or cmd_str.startswith("antigravity"):
                        return True
            except (OSError, PermissionError):
                continue
    return False


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
    active_device: Optional[str] = None
    active_since_utc: Optional[str] = None


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
        active_dev = None
        active_since = None

        if connected:
            manifest = self.drive.get_manifest()
            if manifest:
                cloud_last_dev = manifest.last_device
                cloud_last_utc = manifest.last_sync_utc
                cloud_epoch = manifest.epoch
                in_sync = (cloud_last_dev == self.config.device_id)
                active_dev = manifest.active_device
                active_since = manifest.active_since_utc

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
            active_device=active_dev,
            active_since_utc=active_since,
        )

    def pull(self, force: bool = False, progress_cb: Optional[Callable[[int, str], None]] = None) -> bool:
        """Pull deltas from Google Drive and merge them into local storage.

        Returns True if sync was performed or already up to date, False if skipped due to error.
        Raises DatabaseMergeError on schema incompatibility.
        Protected by ProcessLock.
        """
        def report(pct: int, msg: str) -> None:
            if progress_cb and callable(progress_cb):
                try:
                    progress_cb(pct, msg)
                except Exception:
                    pass

        with ProcessLock():
            report(10, "Verifying Google Drive connection...")
            logger.info("Checking Google Drive status...")
            if not self.drive.test_connection():
                logger.warning("Google Drive is unreachable. Skipping pull (working offline).")
                return False

            manifest = self.drive.get_manifest()
            if manifest is None and not force:
                logger.info("Cloud storage has no manifest (clean remote). Skipping pull.")
                report(100, "Clean cloud storage (no pull needed).")
                return True

            if manifest and manifest.last_device == self.config.device_id and not force:
                logger.info(
                    f"Local device ({self.config.device_id}) is already the latest author in cloud "
                    f"(epoch {manifest.epoch}). No pull needed."
                )
                report(100, "Device already up to date with cloud.")
                return True

            local_ver = get_antigravity_version(self.config.antigravity_bin_path)
            if manifest and manifest.antigravity_version and local_ver and manifest.antigravity_version != local_ver:
                logger.warning(
                    f"Different Antigravity versions detected: local={local_ver}, remote={manifest.antigravity_version}. "
                    "Verifying schema compatibility before merge..."
                )

            report(30, "Downloading conversations, brain artifacts, and projects...")
            logger.info("Pulling conversations and brain artifacts from Google Drive...")
            conv_dir = self.data_dir / "conversations"
            brain_dir = self.data_dir / "brain"

            current_os = get_system_name()

            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_path = Path(tmp_dir)
                remote_projects_staging = tmp_path / "remote_projects"
                remote_db_staging = tmp_path / "remote_summaries.db"

                with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                    f_conv = executor.submit(self.drive.pull_conversations, conv_dir)
                    f_brain = executor.submit(self.drive.pull_brain, brain_dir)
                    f_proj = executor.submit(self.drive.pull_projects, remote_projects_staging)
                    f_sum = executor.submit(self.drive.pull_summaries, remote_db_staging)

                    f_conv.result()
                    f_brain.result()
                    f_proj.result()
                    has_remote_db = f_sum.result()

                report(75, "Translating project definitions...")
                if remote_projects_staging.is_dir():
                    self.projects_dir.mkdir(parents=True, exist_ok=True)
                    for proj_file in remote_projects_staging.glob("*.json"):
                        try:
                            target_file = self.projects_dir / proj_file.name
                            if target_file.is_file() and target_file.stat().st_mtime >= proj_file.stat().st_mtime:
                                logger.debug(f"Local project {target_file.name} is newer than remote. Preserving.")
                                continue
                            with open(proj_file, "r", encoding="utf-8") as f:
                                raw_data = json.load(f)
                            translated_data = translate_project_json(raw_data, target_os=current_os)
                            with open(target_file, "w", encoding="utf-8") as f:
                                json.dump(translated_data, f, indent=2)
                            logger.debug(f"Translated and saved project definition: {target_file.name}")
                        except Exception as e:
                            logger.warning(f"Failed to translate project file {proj_file.name}: {e}")

                report(90, "Merging conversation_summaries.db index...")
                local_db = self.data_dir / "conversation_summaries.db"
                if has_remote_db and remote_db_staging.is_file():
                    merge_res = merge_summaries_databases(local_db, remote_db_staging)
                    logger.info(
                        f"Index merge complete: {merge_res.inserted} inserted, "
                        f"{merge_res.updated} updated, {merge_res.unchanged} unchanged."
                    )
                    self._translate_local_db_workspace_uris(local_db)
                else:
                    logger.info("No remote conversation_summaries.db found on Drive.")

            report(100, "Synchronization completed successfully!")
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

    def push(self, progress_cb: Optional[Callable[[int, str], None]] = None) -> bool:
        """Push local conversations, projects, and merged index to Google Drive.
        
        Protected by ProcessLock. Updates monotonic epoch and clears dirty session flag.
        """
        def report(pct: int, msg: str) -> None:
            if progress_cb and callable(progress_cb):
                try:
                    progress_cb(pct, msg)
                except Exception:
                    pass

        with ProcessLock():
            report(10, "Verifying Google Drive connection...")
            logger.info("Connecting to Google Drive for push...")
            if not self.drive.test_connection():
                logger.warning("Google Drive is unreachable. Push postponed.")
                return False

            # Verify that Antigravity is not still running
            if is_antigravity_process_running():
                logger.warning("Antigravity process is still running. Postponing push to protect database integrity.")
                return False

            # Verify that databases are not locked by background processes
            if not are_all_dbs_accessible(self.data_dir, timeout=2.0):
                logger.warning("Databases are still locked by background processes. Postponing push to protect integrity.")
                return False

            report(25, "Creating isolated database snapshots...")
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_path = Path(tmp_dir)

                # 1. Snapshot summaries database
                local_db = self.data_dir / "conversation_summaries.db"
                staged_summaries_db = None
                if local_db.is_file():
                    create_backup(local_db)
                    staged_summaries_db = tmp_path / "conversation_summaries.db"
                    if not snapshot_db(local_db, staged_summaries_db):
                        logger.warning("Could not snapshot conversation_summaries.db. Postponing push.")
                        return False

                # 2. Snapshot conversation databases
                conv_dir = self.data_dir / "conversations"
                staged_conv_dir = tmp_path / "conversations"
                if conv_dir.is_dir():
                    staged_conv_dir.mkdir(parents=True, exist_ok=True)
                    for db_file in conv_dir.glob("*.db"):
                        target_snap = staged_conv_dir / db_file.name
                        if not snapshot_db(db_file, target_snap):
                            logger.warning(f"Could not snapshot {db_file.name}. Postponing push.")
                            return False

                report(50, "Uploading conversations, brain artifacts, and projects...")
                brain_dir = self.data_dir / "brain"
                logger.info("Uploading conversations, brain, and projects concurrently...")

                with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                    futures = []
                    if staged_conv_dir.is_dir():
                        futures.append(executor.submit(self.drive.push_conversations, staged_conv_dir))
                    if brain_dir.is_dir():
                        futures.append(executor.submit(self.drive.push_brain, brain_dir))
                    if self.projects_dir.is_dir():
                        futures.append(executor.submit(self.drive.push_projects, self.projects_dir))

                    for fut in concurrent.futures.as_completed(futures):
                        fut.result()

                # 3. Upload summaries database from clean snapshot
                report(85, "Uploading conversation_summaries.db index...")
                if staged_summaries_db and staged_summaries_db.is_file():
                    logger.info("Uploading conversation_summaries.db from snapshot...")
                    self.drive.push_summaries(staged_summaries_db)

                # 4. Update cloud manifest token with incremented epoch and release active witness
                report(95, "Updating state token on Google Drive...")
                existing_manifest = None
                try:
                    existing_manifest = self.drive.get_manifest()
                except Exception as e:
                    logger.warning(f"Failed to read existing manifest for epoch bump ({e}), proceeding with fallback.")

                next_epoch = (existing_manifest.epoch + 1) if existing_manifest else 1
                new_manifest = SyncManifest.create_now(
                    self.config.device_id,
                    epoch=next_epoch,
                    active_device=None,
                    active_since_utc=None,
                    antigravity_version=get_antigravity_version(self.config.antigravity_bin_path),
                )
                self.drive.put_manifest(new_manifest)

                # 5. Clear dirty state flag
                clear_session_dirty()
                logger.info(f"Sync complete! State updated to '{self.config.device_id}' (epoch {next_epoch}).")
                report(100, "Upload completed successfully!")
                return True

    def launch_app(
        self,
        extra_args: Optional[list[str]] = None,
        on_launched: Optional[Any] = None,
    ) -> int:
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
            if on_launched and callable(on_launched):
                try:
                    on_launched()
                except Exception as cb_err:
                    logger.debug(f"on_launched callback error: {cb_err}")
            proc.wait()

            # Wait for all child and helper Antigravity processes to exit
            for _ in range(16):
                if not is_antigravity_process_running() and are_all_dbs_accessible(self.data_dir, timeout=0.5):
                    break
                time.sleep(0.5)

            logger.info("Antigravity closed gracefully.")
            return proc.returncode
        except Exception as e:
            logger.error(f"Failed to launch Antigravity: {e}")
            return 1

    def run(self, extra_args: Optional[list[str]] = None) -> int:
        """Full lifecycle: PULL -> LAUNCH -> PUSH with concurrency locking and dirty session recovery."""
        with ProcessLock():
            # If an earlier run crashed before pushing, recover safely (Pull+Merge if cloud advanced, then Push)
            if is_session_dirty():
                logger.warning("Unclean exit detected from previous session. Recovering...")
                print("⚠️ [GC-ChatSync] Unfinished sync detected from previous session. Recovering...")
                try:
                    manifest = self.drive.get_manifest()
                    if manifest and manifest.last_device != self.config.device_id:
                        logger.warning(
                            f"Cloud has newer changes from '{manifest.last_device}' (epoch {manifest.epoch}). "
                            "Merging cloud updates with local changes first..."
                        )
                        print(f"🔄 Merging cloud updates from '{manifest.last_device}' with local changes first...")
                        self.pull(force=True)
                    self.push()
                except DatabaseMergeError as e:
                    logger.critical(f"Aborting dirty recovery due to schema mismatch: {e}")
                    print(f"\n❌ [GC-ChatSync] ABORTING: Database schema incompatibility: {e}\n")
                    return 1
                except RcloneError as e:
                    logger.warning(f"Could not reach Google Drive for recovery ({e}). Continuing.")

            print("🌌 [GC-ChatSync] Step 1/3: Checking & Pulling from Google Drive...")
            pull_ok = False
            try:
                pull_ok = self.pull()
            except DatabaseMergeError as e:
                logger.critical(f"Aborting launch: Database schema incompatibility: {e}")
                print(f"\n❌ [GC-ChatSync] ABORTING: Database schema conflict: {e}")
                print("   To protect your chat history, synchronization has been stopped.")
                print("   Please ensure Antigravity is updated to the same version on all machines.\n")
                return 1
            except Exception as e:
                logger.warning(f"Pull could not complete ({e}). Proceeding in local mode.")
                print(f"⚠️ [GC-ChatSync] Offline mode: could not connect to Google Drive ({e}).")
                pull_ok = False

            if not pull_ok:
                print("🌌 [GC-ChatSync] Step 2/3: Running Antigravity (Offline mode)...")
                exit_code = self.launch_app(extra_args)
                print("🌌 [GC-ChatSync] Offline mode: skipping cloud push to protect remote data.")
                print("   Local changes will synchronize on the next online launch.")
                return exit_code

            # Check if another device has an unclosed active session
            manifest = None
            try:
                manifest = self.drive.get_manifest()
            except RcloneError as e:
                logger.debug(f"Could not check manifest for active session: {e}")

            if manifest and manifest.active_device and manifest.active_device != self.config.device_id:
                when_str = (manifest.active_since_utc or "")[:16].replace("T", " ")
                logger.warning(
                    f"Notice: Device '{manifest.active_device}' was marked active since {when_str}. "
                    "It may not have closed cleanly."
                )
                print(f"\n⚠️ [GC-ChatSync] Notice: Device '{manifest.active_device}' was active since {when_str} UTC.")
                print("   If it exited abruptly without pushing, latest changes from that device might not be on Drive.")
                try:
                    ans = input("Do you want to proceed and launch anyway? [Y/n]: ").strip().lower()
                    if ans in ("n", "no"):
                        print("Launch cancelled.")
                        return 0
                except (EOFError, KeyboardInterrupt):
                    return 0

            # Claim active witness in cloud
            try:
                curr_epoch = manifest.epoch if manifest else 1
                curr_last = manifest.last_device if manifest else ""
                witness_manifest = SyncManifest(
                    last_device=curr_last,
                    last_sync_utc=manifest.last_sync_utc if manifest else "",
                    epoch=curr_epoch,
                    active_device=self.config.device_id,
                    active_since_utc=datetime.now(timezone.utc).isoformat(),
                    antigravity_version=get_antigravity_version(self.config.antigravity_bin_path),
                )
                self.drive.put_manifest(witness_manifest)
            except Exception as e:
                logger.debug(f"Could not claim active witness: {e}")

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
