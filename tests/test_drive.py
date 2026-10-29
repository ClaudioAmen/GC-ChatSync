"""Unit tests for DriveClient and SyncManifest."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from gc_chatsync.drive import (
    DriveClient,
    ManifestCorruptedError,
    RcloneError,
    SyncManifest,
    get_exclude_patterns,
)


class TestDriveClient(unittest.TestCase):

    def test_manifest_serialization(self):
        m = SyncManifest(last_device="linux-pc", last_sync_utc="2026-10-28T16:00:00Z", version=1)
        data = m.to_dict()
        self.assertEqual(data["last_device"], "linux-pc")
        self.assertEqual(data["version"], 1)

        restored = SyncManifest.from_dict(data)
        self.assertEqual(restored.last_device, "linux-pc")
        self.assertEqual(restored.last_sync_utc, "2026-10-28T16:00:00Z")

    @patch("shutil.which")
    def test_remote_base_property(self, mock_which):
        mock_which.return_value = "/usr/bin/rclone"
        client = DriveClient(remote_name="myremote", remote_folder="/GC-ChatSync/")
        self.assertEqual(client.remote_base, "myremote:GC-ChatSync")

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_test_connection_success(self, mock_run, mock_which):
        mock_which.return_value = "/usr/bin/rclone"
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = '{"last_device": "linux-pc", "last_sync_utc": "2026-10-28T16:00:00Z", "epoch": 1}'
        mock_run.return_value = mock_res

        client = DriveClient(remote_name="gdrive")
        self.assertTrue(client.test_connection())

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_test_connection_failure(self, mock_run, mock_which):
        mock_which.return_value = "/usr/bin/rclone"
        mock_res = MagicMock()
        mock_res.returncode = 1
        mock_res.stderr = "token expired"
        mock_run.return_value = mock_res

        client = DriveClient(remote_name="gdrive")
        self.assertFalse(client.test_connection())

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_get_manifest_parsed(self, mock_run, mock_which):
        mock_which.return_value = "/usr/bin/rclone"
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = json.dumps({
            "last_device": "windows-desktop",
            "last_sync_utc": "2026-10-28T12:00:00Z",
            "version": 1
        })
        mock_run.return_value = mock_res

        client = DriveClient(remote_name="gdrive")
        manifest = client.get_manifest()
        self.assertIsNotNone(manifest)
        self.assertEqual(manifest.last_device, "windows-desktop")

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_put_manifest_calls_copyto(self, mock_run, mock_which):
        mock_which.return_value = "/usr/bin/rclone"
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_run.return_value = mock_res

        client = DriveClient(remote_name="gdrive")
        m = SyncManifest(last_device="linux-laptop", last_sync_utc="2026-10-28T16:30:00Z")
        client.put_manifest(m)

        all_calls = [call[0][0] for call in mock_run.call_args_list]
        self.assertEqual(len(all_calls), 2)
        self.assertIn("copyto", all_calls[0])
        self.assertIn("gdrive:GC-ChatSync/manifest.json", all_calls[0])
        self.assertIn("copyto", all_calls[1])
        self.assertIn("gdrive:GC-ChatSync/manifest.json.bak", all_calls[1])

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_get_manifest_corrupted_raises_manifest_corrupted_error(self, mock_run, mock_which):
        mock_which.return_value = "/usr/bin/rclone"
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = "NOT_A_JSON_STRING{{broken"
        mock_run.return_value = mock_res

        client = DriveClient(remote_name="gdrive")
        with self.assertRaises(ManifestCorruptedError):
            client.get_manifest(force_refresh=True)

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_test_connection_returns_true_on_corrupted_manifest(self, mock_run, mock_which):
        mock_which.return_value = "/usr/bin/rclone"
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = "{this is invalid json"
        mock_run.return_value = mock_res

        client = DriveClient(remote_name="gdrive")
        # Drive is still reachable even if manifest is corrupt
        self.assertTrue(client.test_connection())

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_get_manifest_backup_fetches_bak_file(self, mock_run, mock_which):
        mock_which.return_value = "/usr/bin/rclone"
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = '{"last_device": "backup-device", "last_sync_utc": "2026-10-28T16:00:00Z", "epoch": 4}'
        mock_run.return_value = mock_res

        client = DriveClient(remote_name="gdrive")
        bak = client.get_manifest_backup()
        self.assertIsNotNone(bak)
        self.assertEqual(bak.last_device, "backup-device")
        self.assertEqual(bak.epoch, 4)
        called_cmd = mock_run.call_args[0][0]
        self.assertIn("gdrive:GC-ChatSync/manifest.json.bak", called_cmd)

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_live_connection_to_gdrive_mocked(self, mock_run, mock_which):
        mock_which.return_value = "/usr/bin/rclone"
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = '{"last_device": "linux-laptop", "last_sync_utc": "2026-10-28T16:00:00Z"}'
        mock_run.return_value = mock_res

        client = DriveClient(remote_name="gdrive")
        self.assertTrue(client.test_connection())

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_pull_handles_directory_not_found(self, mock_run, mock_which):
        mock_which.return_value = "/usr/bin/rclone"
        mock_res = MagicMock()
        mock_res.returncode = 3
        mock_res.stderr = "error reading source root directory: directory not found"
        mock_run.return_value = mock_res

        client = DriveClient(remote_name="gdrive")
        with tempfile.TemporaryDirectory() as tmp:
            # Should not raise exception
            client.pull_projects(Path(tmp))
            client.pull_conversations(Path(tmp))
            client.pull_brain(Path(tmp))

    def test_exclude_patterns_are_anchored(self):
        from gc_chatsync.drive import EXCLUDE_PATTERNS
        self.assertIn("**/.system_generated/steps/**", EXCLUDE_PATTERNS)
        self.assertIn("**/.system_generated/tasks/**", EXCLUDE_PATTERNS)
        self.assertIn("**/.system_generated/messages/**", EXCLUDE_PATTERNS)
        self.assertIn("**/.system_generated/logs/chunks/**", EXCLUDE_PATTERNS)
        # Verify unanchored root patterns are NOT present
        self.assertNotIn("steps/**", EXCLUDE_PATTERNS)
        self.assertNotIn("tasks/**", EXCLUDE_PATTERNS)

    def test_get_exclude_patterns_sync_full_brain(self):
        patterns_default = get_exclude_patterns(sync_full_brain=False)
        self.assertTrue(any("steps" in p for p in patterns_default))
        self.assertTrue(any("tasks" in p for p in patterns_default))

        patterns_full = get_exclude_patterns(sync_full_brain=True)
        # steps and tasks are not excluded when sync_full_brain is True
        self.assertFalse(any("steps" in p for p in patterns_full))
        self.assertFalse(any("tasks" in p for p in patterns_full))
        # But transient wal and shm files are still excluded
        self.assertTrue(any("*.db-wal" in p for p in patterns_full))


if __name__ == "__main__":
    unittest.main()
