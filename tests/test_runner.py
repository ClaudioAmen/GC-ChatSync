"""Unit tests for SyncRunner and orchestration."""

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from gc_chatsync.config import AppConfig
from gc_chatsync.drive import SyncManifest
from gc_chatsync.runner import SyncRunner


class TestSyncRunner(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)
        self.data_dir = self.tmp_path / "antigravity"
        self.data_dir.mkdir()

        self.patch_lock = patch("gc_chatsync.runner.ProcessLock")
        self.mock_lock = self.patch_lock.start()

        self.cfg = AppConfig(
            device_id="test-laptop",
            rclone_remote="testremote",
            remote_folder="TestSync",
            antigravity_data_dir=str(self.data_dir),
            antigravity_bin_path="/bin/true",
        )

    def tearDown(self):
        self.patch_lock.stop()
        self.temp_dir.cleanup()

    @patch("gc_chatsync.drive.DriveClient.test_connection")
    @patch("gc_chatsync.drive.DriveClient.get_manifest")
    def test_get_status_in_sync(self, mock_manifest, mock_conn):
        mock_conn.return_value = True
        mock_manifest.return_value = SyncManifest(
            last_device="test-laptop",
            last_sync_utc="2026-10-28T16:00:00Z"
        )

        runner = SyncRunner(self.cfg)
        status = runner.get_status()

        self.assertTrue(status.drive_connected)
        self.assertTrue(status.in_sync)
        self.assertEqual(status.cloud_last_device, "test-laptop")

    @patch("gc_chatsync.drive.DriveClient.test_connection")
    @patch("gc_chatsync.drive.DriveClient.get_manifest")
    def test_pull_skips_when_device_matches(self, mock_manifest, mock_conn):
        mock_conn.return_value = True
        mock_manifest.return_value = SyncManifest(
            last_device="test-laptop",
            last_sync_utc="2026-10-28T16:00:00Z"
        )

        runner = SyncRunner(self.cfg)
        # Should return True without pulling
        self.assertTrue(runner.pull(force=False))

    def test_translate_local_db_workspace_uris(self):
        db_path = self.data_dir / "conversation_summaries.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute("""
                CREATE TABLE conversation_summaries (
                    conversation_id TEXT PRIMARY KEY,
                    workspace_uris TEXT NOT NULL DEFAULT ''
                );
            """)
            conn.execute("""
                INSERT INTO conversation_summaries VALUES
                ('cid-1', '["file:///C:/Users/Claudio/Documents/progetti"]');
            """)

        runner = SyncRunner(self.cfg)
        runner._translate_local_db_workspace_uris(db_path)

        with sqlite3.connect(db_path) as conn:
            row = conn.execute("SELECT workspace_uris FROM conversation_summaries WHERE conversation_id='cid-1'").fetchone()
            # On Linux, this should be translated to /home/...
            self.assertIn("/home/", row[0])


if __name__ == "__main__":
    unittest.main()
