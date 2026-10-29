"""Unit tests for Reconcile / Doctor Engine."""

import gc
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from gc_chatsync.config import AppConfig
from gc_chatsync.drive import DriveClient, ManifestCorruptedError, SyncManifest
from gc_chatsync.reconcile import extract_session_preview, run_reconcile
from gc_chatsync.runner import SyncRunner


class TestReconcileEngine(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)
        self.data_dir = self.dir_path / "antigravity"
        self.conv_dir = self.data_dir / "conversations"
        self.conv_dir.mkdir(parents=True, exist_ok=True)
        self.local_db = self.data_dir / "conversation_summaries.db"
        self._create_sample_summaries_db(self.local_db)

        self.cfg = AppConfig(
            antigravity_bin_path=self.dir_path / "fake_antigravity",
            antigravity_data_dir=self.data_dir,
            remote_folder="TestSync",
            rclone_remote="testremote",
            device_id="test-device",
        )
        rclone_dummy = "rclone.exe" if sys.platform == "win32" else "/usr/bin/rclone"
        self.patch_which = patch("shutil.which", return_value=rclone_dummy)
        self.patch_which.start()

    def tearDown(self):
        self.patch_which.stop()
        gc.collect()
        try:
            self.temp_dir.cleanup()
        except PermissionError:
            time.sleep(0.1)
            gc.collect()
            try:
                self.temp_dir.cleanup()
            except Exception:
                pass

    def _create_sample_summaries_db(self, path: Path):
        conn = sqlite3.connect(path)
        try:
            conn.execute("""
                CREATE TABLE conversation_summaries (
                    conversation_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '',
                    preview TEXT NOT NULL DEFAULT '',
                    step_count INTEGER NOT NULL DEFAULT 0,
                    last_modified_time DATETIME NOT NULL,
                    workspace_uris TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT ''
                );
            """)
            conn.commit()
        finally:
            conn.close()

    def _create_sample_session_db(self, path: Path, title: str = "Test Session"):
        conn = sqlite3.connect(path)
        try:
            conn.execute("CREATE TABLE trajectory_meta (trajectory_id TEXT, cascade_id TEXT, trajectory_type INT, source INT);")
            conn.execute("CREATE TABLE steps (idx INT, step_type INT, status INT, step_payload BLOB);")
            conn.execute("INSERT INTO steps VALUES (0, 14, 3, ?);", (f"Prompt: {title}".encode("utf-8"),))
            conn.commit()
        finally:
            conn.close()

    @patch("gc_chatsync.reconcile.is_antigravity_process_running", return_value=True)
    def test_reconcile_aborts_when_antigravity_running(self, mock_running):
        runner = SyncRunner(self.cfg)
        rep = run_reconcile(runner)
        self.assertEqual(rep.status, "CRITICAL_ABORT")
        self.assertEqual(rep.code, 2)
        self.assertTrue(any("currently running" in e for e in rep.errors))

    @patch("gc_chatsync.reconcile.is_antigravity_process_running", return_value=False)
    @patch("gc_chatsync.drive.DriveClient.test_connection", return_value=False)
    def test_reconcile_indexes_orphan_session(self, mock_conn, mock_running):
        # Create an orphan session db
        orphan_file = self.conv_dir / "orphan-1234.db"
        self._create_sample_session_db(orphan_file, title="Restored Python Project")

        runner = SyncRunner(self.cfg)
        rep = run_reconcile(runner)

        self.assertEqual(rep.orphans_found, 1)
        self.assertEqual(rep.orphans_indexed, 1)
        self.assertEqual(len(rep.corrupt_files), 0)

        # Verify index was updated with Epoch 0 timestamp so cloud updates supersede it
        conn = sqlite3.connect(self.local_db)
        try:
            row = conn.execute(
                "SELECT preview, last_modified_time FROM conversation_summaries WHERE conversation_id='orphan-1234';"
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertIn("Restored Python Project", row[0])
            self.assertTrue(row[1].startswith("1970-01-01"))
        finally:
            conn.close()

    @patch("gc_chatsync.reconcile.is_antigravity_process_running", return_value=False)
    @patch("gc_chatsync.drive.DriveClient.test_connection", return_value=False)
    def test_reconcile_quarantines_corrupted_session(self, mock_conn, mock_running):
        # Create a corrupt session db
        corrupt_file = self.conv_dir / "corrupt-5678.db"
        corrupt_file.write_bytes(b"NOT_A_VALID_SQLITE_DATABASE_JUNK_DATA")

        runner = SyncRunner(self.cfg)
        rep = run_reconcile(runner)

        self.assertIn("corrupt-5678.db", rep.corrupt_files)
        self.assertEqual(rep.status, "WARNING")
        self.assertEqual(rep.code, 1)

        # Check quarantine directory
        quarantine_file = self.data_dir / ".backups" / "corrupted" / "corrupt-5678.db"
        self.assertTrue(quarantine_file.is_file())

        # Check placeholder in index
        conn = sqlite3.connect(self.local_db)
        try:
            row = conn.execute("SELECT title, preview FROM conversation_summaries WHERE conversation_id='corrupt-5678';").fetchone()
            self.assertIsNotNone(row)
            self.assertIn("Corrupted / Quarantined", row[0])
        finally:
            conn.close()

    @patch("gc_chatsync.reconcile.is_antigravity_process_running", return_value=False)
    @patch("gc_chatsync.drive.DriveClient.test_connection", return_value=True)
    @patch("gc_chatsync.drive.DriveClient.get_manifest")
    @patch("gc_chatsync.drive.DriveClient.put_manifest")
    def test_reconcile_clears_stale_cloud_witness(self, mock_put, mock_get, mock_conn, mock_running):
        # Manifest with stale witness (>15m ago)
        mock_get.return_value = SyncManifest(
            last_device="other-device",
            last_sync_utc="2026-10-28T10:00:00Z",
            active_device="windows-stale-desktop",
            active_since_utc="2026-10-28T10:00:00Z",
            epoch=2,
        )

        runner = SyncRunner(self.cfg)
        with patch.object(runner.drive, "run_cmd", return_value=""):
            rep = run_reconcile(runner)

        self.assertTrue(rep.stale_witness_cleared)
        self.assertTrue(mock_put.called)
        saved_manifest = mock_put.call_args[0][0]
        self.assertIsNone(saved_manifest.active_device)
        self.assertIsNone(saved_manifest.active_since_utc)

    @patch("gc_chatsync.reconcile.is_antigravity_process_running", return_value=False)
    @patch("gc_chatsync.drive.DriveClient.test_connection", return_value=True)
    @patch("gc_chatsync.drive.DriveClient.get_manifest", side_effect=ManifestCorruptedError("JSON error"))
    @patch("gc_chatsync.drive.DriveClient.get_manifest_backup")
    @patch("gc_chatsync.drive.DriveClient.put_manifest")
    def test_reconcile_repairs_corrupted_manifest_from_backup(self, mock_put, mock_bak, mock_get, mock_conn, mock_running):
        backup_manifest = SyncManifest(
            last_device="backup-device",
            last_sync_utc="2026-10-28T10:00:00Z",
            epoch=5,
        )
        mock_bak.return_value = backup_manifest

        runner = SyncRunner(self.cfg)
        with patch.object(runner.drive, "run_cmd", return_value=""):
            rep = run_reconcile(runner)

        self.assertTrue(rep.manifest_repaired)
        self.assertTrue(mock_put.called)
        restored = mock_put.call_args[0][0]
        self.assertEqual(restored.last_device, "backup-device")
        self.assertEqual(restored.epoch, 5)

    @patch("gc_chatsync.reconcile.is_antigravity_process_running", return_value=False)
    @patch("gc_chatsync.drive.DriveClient.test_connection", return_value=True)
    @patch("gc_chatsync.drive.DriveClient.get_manifest", side_effect=ManifestCorruptedError("JSON error"))
    @patch("gc_chatsync.drive.DriveClient.get_manifest_backup", return_value=None)
    @patch("gc_chatsync.drive.DriveClient.put_manifest")
    def test_reconcile_reconstructs_manifest_when_no_backup(self, mock_put, mock_bak, mock_get, mock_conn, mock_running):
        runner = SyncRunner(self.cfg)
        with patch.object(runner.drive, "run_cmd", return_value=""):
            rep = run_reconcile(runner, repair_manifest=True)

        self.assertTrue(rep.manifest_repaired)
        self.assertTrue(mock_put.called)
        reconstructed = mock_put.call_args[0][0]
        self.assertEqual(reconstructed.epoch, 1)
        self.assertEqual(reconstructed.last_device, "test-device")


if __name__ == "__main__":
    unittest.main()
