"""Unit tests for DriveClient and SyncManifest."""

import json
import shutil
import unittest
from unittest.mock import MagicMock, patch

from gc_chatsync.drive import DriveClient, RcloneError, SyncManifest


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
        mock_res.stdout = "folder1/\nfile1.txt"
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
    def test_put_manifest_calls_rcat(self, mock_run, mock_which):
        mock_which.return_value = "/usr/bin/rclone"
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_run.return_value = mock_res

        client = DriveClient(remote_name="gdrive")
        m = SyncManifest(last_device="linux-laptop", last_sync_utc="2026-10-28T16:30:00Z")
        client.put_manifest(m)

        called_cmd = mock_run.call_args[0][0]
        self.assertIn("rcat", called_cmd)
        self.assertIn("gdrive:GC-ChatSync/manifest.json", called_cmd)

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_live_connection_to_gdrive_mocked(self, mock_run, mock_which):
        mock_which.return_value = "/usr/bin/rclone"
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = "folder1/\nfile1.txt\n"
        mock_run.return_value = mock_res

        client = DriveClient(remote_name="gdrive")
        self.assertTrue(client.test_connection())


if __name__ == "__main__":
    unittest.main()
