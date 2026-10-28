"""Unit tests for SyncRunner and orchestration."""

import sqlite3
import sys
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

        self.patch_which = patch("shutil.which", return_value="/usr/bin/rclone")
        self.mock_which = self.patch_which.start()

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
        self.patch_which.stop()
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
            if sys.platform == "win32":
                self.assertIn("file:///c:", row[0].lower())
            else:
                self.assertTrue(row[0].startswith('["file:///'))
                self.assertNotIn("C:/Users", row[0])

    @patch("gc_chatsync.runner.is_session_dirty")
    @patch("gc_chatsync.drive.DriveClient.get_manifest")
    @patch("gc_chatsync.runner.SyncRunner.pull")
    @patch("gc_chatsync.runner.SyncRunner.push")
    @patch("gc_chatsync.runner.SyncRunner.launch_app")
    def test_run_recovers_dirty_session_with_pull_when_cloud_ahead(
        self, mock_launch, mock_push, mock_pull, mock_manifest, mock_dirty
    ):
        mock_dirty.return_value = True
        mock_manifest.return_value = SyncManifest(
            last_device="other-windows-pc",
            last_sync_utc="2026-10-28T18:00:00Z",
            epoch=5,
        )
        mock_launch.return_value = 0

        runner = SyncRunner(self.cfg)
        runner.run()

        # pull(force=True) should have been called during dirty recovery
        mock_pull.assert_any_call(force=True)
        # push should have been called
        self.assertTrue(mock_push.called)

    @patch("gc_chatsync.drive.DriveClient.test_connection")
    @patch("gc_chatsync.drive.DriveClient.get_manifest")
    def test_get_status_reports_active_device(self, mock_manifest, mock_conn):
        mock_conn.return_value = True
        mock_manifest.return_value = SyncManifest(
            last_device="test-laptop",
            last_sync_utc="2026-10-28T16:00:00Z",
            epoch=2,
            active_device="windows-desktop",
            active_since_utc="2026-10-28T18:30:00Z",
        )

        runner = SyncRunner(self.cfg)
        status = runner.get_status()

        self.assertEqual(status.active_device, "windows-desktop")
        self.assertEqual(status.active_since_utc, "2026-10-28T18:30:00Z")

    @patch("subprocess.Popen")
    def test_launch_app_calls_on_launched(self, mock_popen):
        mock_proc = mock_popen.return_value
        mock_proc.wait.return_value = 0
        mock_proc.returncode = 0

        fake_bin = self.tmp_path / "fake_antigravity"
        fake_bin.touch()

        runner = SyncRunner(self.cfg)
        runner.config.antigravity_bin_path = str(fake_bin)

        called = False

        def cb():
            nonlocal called
            called = True

        code = runner.launch_app(on_launched=cb)
        self.assertEqual(code, 0)
        self.assertTrue(called)


if __name__ == "__main__":
    unittest.main()
