"""Unit tests for SyncRunner lifecycle orchestration."""

import gc
import json
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from gc_chatsync.config import AppConfig
from gc_chatsync.drive import RcloneError, SyncManifest
from gc_chatsync.lock import clear_session_dirty, is_session_dirty
from gc_chatsync.merger import DatabaseMergeError
from gc_chatsync.runner import SyncRunner


class TestSyncRunner(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)
        self.data_dir = self.tmp_path / "antigravity"
        self.data_dir.mkdir()
        self.config_dir = self.tmp_path / "config"
        self.config_dir.mkdir()

        self.patch_cfg_dir = patch("gc_chatsync.lock.get_config_dir", return_value=self.config_dir)
        self.patch_cfg_dir.start()

        rclone_dummy = "rclone.exe" if sys.platform == "win32" else "/usr/bin/rclone"
        self.patch_which = patch("shutil.which", return_value=rclone_dummy)
        self.mock_which = self.patch_which.start()

        self.patch_lock = patch("gc_chatsync.runner.ProcessLock")
        self.mock_lock = self.patch_lock.start()

        clear_session_dirty()

        self.cfg = AppConfig(
            device_id="test-laptop",
            rclone_remote="testremote",
            remote_folder="TestSync",
            antigravity_data_dir=str(self.data_dir),
            antigravity_bin_path="/bin/true",
        )

    def tearDown(self):
        clear_session_dirty()
        self.patch_lock.stop()
        self.patch_which.stop()
        self.patch_cfg_dir.stop()
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

        self.assertTrue(status.connected)
        self.assertTrue(status.in_sync)
        self.assertEqual(status.last_sync_utc, "2026-10-28T16:00:00Z")

    @patch("gc_chatsync.drive.DriveClient.test_connection")
    @patch("gc_chatsync.drive.DriveClient.get_manifest")
    def test_get_status_cloud_ahead(self, mock_manifest, mock_conn):
        mock_conn.return_value = True
        mock_manifest.return_value = SyncManifest(
            last_device="other-desktop",
            last_sync_utc="2026-10-28T17:00:00Z",
            epoch=3,
        )

        runner = SyncRunner(self.cfg)
        status = runner.get_status()

        self.assertTrue(status.connected)
        self.assertFalse(status.in_sync)
        self.assertEqual(status.last_device, "other-desktop")

    def test_translate_local_db_workspace_uris(self):
        db_path = self.data_dir / "conversation_summaries.db"
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("""
                CREATE TABLE conversation_summaries (
                    conversation_id TEXT PRIMARY KEY,
                    workspace_uris TEXT NOT NULL DEFAULT ''
                );
            """)
            conn.execute("""
                INSERT INTO conversation_summaries VALUES
                ('cid-1', '["file:///C:/Users/WindowsUser/Documents/projects"]');
            """)
            conn.commit()
        finally:
            conn.close()

        runner = SyncRunner(self.cfg)
        runner._translate_local_db_workspace_uris(db_path)

        conn2 = sqlite3.connect(db_path)
        try:
            row = conn2.execute("SELECT workspace_uris FROM conversation_summaries WHERE conversation_id='cid-1'").fetchone()
            if sys.platform == "win32":
                self.assertIn("file:///c:", row[0].lower())
            else:
                self.assertTrue(row[0].startswith('["file:///'))
                self.assertNotIn("C:/Users/WindowsUser", row[0])
        finally:
            conn2.close()

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

    @patch("gc_chatsync.runner.is_antigravity_process_running", return_value=False)
    @patch("subprocess.Popen")
    def test_launch_app_calls_on_launched(self, mock_popen, mock_running):
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

        # Ensure stdio is isolated to prevent EPIPE crashes
        mock_popen.assert_called_once()
        _, kwargs = mock_popen.call_args
        self.assertEqual(kwargs.get("stdin"), subprocess.DEVNULL)
        self.assertEqual(kwargs.get("stdout"), subprocess.DEVNULL)
        self.assertEqual(kwargs.get("stderr"), subprocess.DEVNULL)
        if sys.platform != "win32":
            self.assertTrue(kwargs.get("start_new_session"))

    @patch("gc_chatsync.runner.is_antigravity_process_running", return_value=True)
    @patch("gc_chatsync.drive.DriveClient.test_connection", return_value=True)
    def test_push_force_bypasses_running_process(self, mock_conn, mock_running):
        runner = SyncRunner(self.cfg)
        # Without force: should return False because Antigravity is running
        self.assertFalse(runner.push(force=False))

        # With force: should bypass process check and attempt snapshot/push
        with patch.object(runner.drive, "put_manifest") as mock_put, \
             patch.object(runner.drive, "push_projects"), \
             patch.object(runner.drive, "push_brain"), \
             patch.object(runner.drive, "push_conversations"), \
             patch.object(runner.drive, "push_summaries"), \
             patch("gc_chatsync.merger.snapshot_db", return_value=True):
            success = runner.push(force=True)
            self.assertTrue(success)
            self.assertTrue(mock_put.called)

    @patch("gc_chatsync.runner.SyncRunner.launch_app", return_value=0)
    @patch("gc_chatsync.runner.SyncRunner.pull", return_value=True)
    @patch("gc_chatsync.runner.SyncRunner.push", return_value=False)
    @patch("gc_chatsync.drive.DriveClient.get_manifest", return_value=None)
    @patch("gc_chatsync.drive.DriveClient.put_manifest")
    def test_run_postponed_push_returns_exit_code_2(
        self, mock_put, mock_get_m, mock_push, mock_pull, mock_launch
    ):
        runner = SyncRunner(self.cfg)
        code = runner.run()
        # When Antigravity exited with 0 but push was postponed/failed, run returns code 2
        self.assertEqual(code, 2)

    @patch("gc_chatsync.runner.is_antigravity_process_running", return_value=False)
    @patch("gc_chatsync.drive.DriveClient.test_connection", return_value=True)
    def test_push_network_error_returns_false_without_raising(self, mock_conn, mock_running):
        runner = SyncRunner(self.cfg)
        with patch.object(runner.drive, "get_manifest", return_value=None), \
             patch.object(runner.drive, "push_brain", side_effect=RcloneError("Connection reset by peer")), \
             patch("gc_chatsync.merger.snapshot_db", return_value=True):
            success = runner.push(force=True)
            self.assertFalse(success)

    @patch("gc_chatsync.runner.SyncRunner.pull", side_effect=RcloneError("Connection timed out"))
    @patch("gc_chatsync.runner.SyncRunner.launch_app", return_value=0)
    @patch("gc_chatsync.runner.SyncRunner.push")
    def test_pull_failure_rclone_error_runs_offline_and_skips_push(
        self, mock_push, mock_launch, mock_pull
    ):
        runner = SyncRunner(self.cfg)
        code = runner.run()

        self.assertEqual(code, 0)
        mock_launch.assert_called_once_with(None, enable_heartbeat=False)
        mock_push.assert_not_called()
        self.assertTrue(is_session_dirty())

    @patch("gc_chatsync.runner.SyncRunner.pull", side_effect=DatabaseMergeError("Incompatible schema"))
    @patch("gc_chatsync.runner.SyncRunner.launch_app")
    @patch("gc_chatsync.runner.SyncRunner.push")
    def test_pull_failure_database_merge_error_aborts_launch(
        self, mock_push, mock_launch, mock_pull
    ):
        runner = SyncRunner(self.cfg)
        code = runner.run()

        self.assertEqual(code, 1)
        mock_launch.assert_not_called()
        mock_push.assert_not_called()

    @patch("gc_chatsync.runner.is_antigravity_process_running", return_value=False)
    @patch("gc_chatsync.drive.DriveClient.test_connection", return_value=True)
    def test_push_uploads_data_first_and_manifest_strictly_last(self, mock_conn, mock_running):
        runner = SyncRunner(self.cfg)
        conv_dir = self.data_dir / "conversations"
        conv_dir.mkdir(parents=True, exist_ok=True)
        (conv_dir / "session1.db").touch()
        (self.data_dir / "conversation_summaries.db").touch()
        (self.data_dir / "brain").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "projects").mkdir(parents=True, exist_ok=True)

        call_order = []

        def track(name):
            def _fn(*args, **kwargs):
                call_order.append(name)
            return _fn

        with patch.object(runner.drive, "push_conversations", side_effect=track("conv")), \
             patch.object(runner.drive, "push_brain", side_effect=track("brain")), \
             patch.object(runner.drive, "push_projects", side_effect=track("projects")), \
             patch.object(runner.drive, "push_summaries", side_effect=track("summaries")), \
             patch.object(runner.drive, "put_manifest", side_effect=track("manifest")), \
             patch("gc_chatsync.merger.snapshot_db", return_value=True):
            success = runner.push(force=True, full=True)
            self.assertTrue(success)

        self.assertIn("manifest", call_order)
        self.assertEqual(call_order[-1], "manifest")
        self.assertIn("conv", call_order[:-1])
        self.assertIn("brain", call_order[:-1])
        self.assertIn("summaries", call_order[:-1])

    @patch("gc_chatsync.runner.is_antigravity_process_running", return_value=False)
    @patch("gc_chatsync.drive.DriveClient.test_connection", return_value=True)
    @patch("gc_chatsync.runner.get_last_push_timestamp", return_value=999999.0)
    @patch("gc_chatsync.merger.needs_db_snapshot")
    def test_push_full_resets_last_push_timestamp(self, mock_needs, mock_ts, mock_conn, mock_running):
        runner = SyncRunner(self.cfg)
        conv_dir = self.data_dir / "conversations"
        conv_dir.mkdir(parents=True, exist_ok=True)
        (conv_dir / "conv_a.db").touch()

        with patch.object(runner.drive, "push_conversations"), \
             patch.object(runner.drive, "push_brain"), \
             patch.object(runner.drive, "push_projects"), \
             patch.object(runner.drive, "push_summaries"), \
             patch.object(runner.drive, "put_manifest"), \
             patch("gc_chatsync.merger.snapshot_db", return_value=True):
            runner.push(force=True, full=True)

        mock_needs.assert_called_with(conv_dir / "conv_a.db", 0.0)

    @patch("gc_chatsync.runner.is_antigravity_process_running", return_value=False)
    @patch("subprocess.Popen")
    def test_launch_app_joins_heartbeat_thread_on_exit(self, mock_popen, mock_running):
        mock_proc = mock_popen.return_value
        mock_proc.wait.return_value = 0
        mock_proc.returncode = 0

        fake_bin = self.tmp_path / "fake_antigravity"
        fake_bin.touch()

        runner = SyncRunner(self.cfg)
        runner.config.antigravity_bin_path = str(fake_bin)

        with patch("threading.Thread") as mock_thread_cls:
            mock_th = MagicMock()
            mock_thread_cls.return_value = mock_th
            mock_th.is_alive.return_value = True

            code = runner.launch_app(enable_heartbeat=True)
            self.assertEqual(code, 0)
            mock_th.start.assert_called_once()
            mock_th.join.assert_called_once_with(timeout=5.0)

    @patch("gc_chatsync.runner.is_antigravity_process_running", return_value=False)
    @patch("subprocess.Popen")
    def test_launch_app_offline_does_not_start_heartbeat(self, mock_popen, mock_running):
        mock_proc = mock_popen.return_value
        mock_proc.wait.return_value = 0
        mock_proc.returncode = 0

        fake_bin = self.tmp_path / "fake_antigravity"
        fake_bin.touch()

        runner = SyncRunner(self.cfg)
        runner.config.antigravity_bin_path = str(fake_bin)

        with patch("threading.Thread") as mock_thread_cls:
            mock_th = MagicMock()
            mock_thread_cls.return_value = mock_th

            code = runner.launch_app(enable_heartbeat=False)
            self.assertEqual(code, 0)
            mock_th.start.assert_not_called()

    def test_heartbeat_updates_only_matching_device_and_epoch(self):
        runner = SyncRunner(self.cfg)
        manifest_matching = SyncManifest(
            last_device="other",
            last_sync_utc="2026-10-28T12:00:00Z",
            epoch=2,
            active_device="test-laptop",
            active_since_utc="2026-10-28T12:00:00Z",
        )

        with patch.object(runner.drive, "get_manifest", return_value=manifest_matching), \
             patch.object(runner.drive, "put_manifest") as mock_put:
            res = runner._run_heartbeat_step(session_epoch=2)
            self.assertTrue(res)
            self.assertTrue(mock_put.called)
            saved = mock_put.call_args[0][0]
            self.assertIsNotNone(saved.active_heartbeat_utc)

        # Case 2: active_device belongs to a different device
        manifest_other = SyncManifest(
            last_device="other",
            last_sync_utc="2026-10-28T12:00:00Z",
            epoch=2,
            active_device="windows-desktop",
            active_since_utc="2026-10-28T12:00:00Z",
        )
        with patch.object(runner.drive, "get_manifest", return_value=manifest_other), \
             patch.object(runner.drive, "put_manifest") as mock_put:
            res = runner._run_heartbeat_step(session_epoch=2)
            self.assertFalse(res)
            mock_put.assert_not_called()

        # Case 3: epoch changed in cloud during session
        manifest_new_epoch = SyncManifest(
            last_device="test-laptop",
            last_sync_utc="2026-10-28T12:00:00Z",
            epoch=3,
            active_device="test-laptop",
            active_since_utc="2026-10-28T12:00:00Z",
        )
        with patch.object(runner.drive, "get_manifest", return_value=manifest_new_epoch), \
             patch.object(runner.drive, "put_manifest") as mock_put:
            res = runner._run_heartbeat_step(session_epoch=2)
            self.assertFalse(res)
            mock_put.assert_not_called()


if __name__ == "__main__":
    unittest.main()
