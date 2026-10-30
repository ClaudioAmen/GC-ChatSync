"""Unit tests for ProcessLock and dirty session tracking."""

import gc
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from gc_chatsync.lock import (
    LockError,
    ProcessLock,
    clear_session_dirty,
    get_dirty_session_info,
    is_pid_running,
    is_session_dirty,
    mark_session_dirty,
)


class TestProcessLock(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_dir = Path(self.temp_dir.name)
        ProcessLock._depth = 0

    def tearDown(self):
        ProcessLock._depth = 0
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

    @patch("gc_chatsync.lock.get_config_dir")
    def test_acquire_and_release(self, mock_dir):
        mock_dir.return_value = self.config_dir

        lock = ProcessLock("test.lock")
        self.assertTrue(lock.acquire())
        self.assertTrue(lock.lock_file.is_file())

        lock.release()
        self.assertFalse(lock.lock_file.is_file())

    @patch("gc_chatsync.lock.get_config_dir")
    def test_context_manager_reentrancy(self, mock_dir):
        mock_dir.return_value = self.config_dir

        with ProcessLock("test.lock"):
            self.assertEqual(ProcessLock._depth, 1)
            with ProcessLock("test.lock"):
                self.assertEqual(ProcessLock._depth, 2)
            self.assertEqual(ProcessLock._depth, 1)
        self.assertEqual(ProcessLock._depth, 0)
        self.assertFalse((self.config_dir / "test.lock").exists())

    @patch("gc_chatsync.lock.get_config_dir")
    @patch("gc_chatsync.lock.is_pid_running", return_value=True)
    def test_active_lock_conflict_raises_error(self, mock_running, mock_dir):
        mock_dir.return_value = self.config_dir

        lock_file = self.config_dir / "test.lock"
        lock_file.write_text(json.dumps({
            "pid": 9999999,
            "started_utc": "2026-01-01T00:00:00Z",
            "command": "other",
        }))

        lock = ProcessLock("test.lock")
        with self.assertRaises(LockError):
            lock.acquire()

    @patch("gc_chatsync.lock.get_config_dir")
    @patch("gc_chatsync.lock.is_pid_running", return_value=False)
    def test_stale_lock_is_recovered(self, mock_running, mock_dir):
        mock_dir.return_value = self.config_dir

        lock_file = self.config_dir / "test.lock"
        lock_file.write_text(json.dumps({
            "pid": 12345,
            "started_utc": "2026-01-01T00:00:00Z",
            "command": "dead-proc",
        }))

        lock = ProcessLock("test.lock")
        self.assertTrue(lock.acquire())
        data = json.loads(lock_file.read_text())
        self.assertEqual(data["pid"], os.getpid())
        lock.release()

    @patch("gc_chatsync.lock.get_config_dir")
    def test_session_dirty_lifecycle(self, mock_dir):
        mock_dir.return_value = self.config_dir

        self.assertFalse(is_session_dirty())
        mark_session_dirty("test-device")
        self.assertTrue(is_session_dirty())

        info = get_dirty_session_info()
        self.assertIsNotNone(info)
        self.assertEqual(info.get("device_id"), "test-device")

        clear_session_dirty()
        self.assertFalse(is_session_dirty())

    @patch("gc_chatsync.lock.get_system_name", return_value="windows")
    @patch("subprocess.run")
    def test_windows_is_pid_running_csv_fallback_exact_match(self, mock_run, mock_sys):
        from unittest.mock import MagicMock
        res = MagicMock()
        res.stdout = '"python.exe","12345","Console","1","15,000 K"\n'
        mock_run.return_value = res

        # Simulate ctypes failure to trigger tasklist CSV fallback
        with patch.dict("sys.modules", {"ctypes": None}):
            self.assertTrue(is_pid_running(12345))
            # Substring PID (e.g. 123) must NOT falsely match PID 12345
            self.assertFalse(is_pid_running(123))


if __name__ == "__main__":
    unittest.main()
