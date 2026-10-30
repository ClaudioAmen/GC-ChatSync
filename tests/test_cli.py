"""Unit tests for the CLI interface."""

import gc
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from gc_chatsync.cli import build_parser, main
from gc_chatsync.config import AppConfig


class TestCLI(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)
        self.log_path = self.tmp_path / "sync.log"
        self.patch_log = patch("gc_chatsync.cli.get_log_file_path", return_value=self.log_path)
        self.patch_log.start()
        self.patch_cfg_dir = patch("gc_chatsync.config.get_config_dir", return_value=self.tmp_path)
        self.patch_cfg_dir.start()

    def tearDown(self):
        self.patch_cfg_dir.stop()
        self.patch_log.stop()
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

    def test_build_parser_subcommands(self):
        parser = build_parser()
        args = parser.parse_args(["run", "--verbose"])
        self.assertEqual(args.command, "run")
        self.assertTrue(args.verbose)

        args_run_nosync = parser.parse_args(["run", "--no-sync"])
        self.assertEqual(args_run_nosync.command, "run")
        self.assertTrue(args_run_nosync.no_sync)

        args_launch = parser.parse_args(["launch"])
        self.assertEqual(args_launch.command, "launch")

        args_pull = parser.parse_args(["pull", "--force"])
        self.assertEqual(args_pull.command, "pull")
        self.assertTrue(args_pull.force)

        args_push = parser.parse_args(["push"])
        self.assertEqual(args_push.command, "push")
        self.assertFalse(args_push.force)
        self.assertFalse(args_push.full)

        args_push_force = parser.parse_args(["push", "--force", "--full"])
        self.assertEqual(args_push_force.command, "push")
        self.assertTrue(args_push_force.force)
        self.assertTrue(args_push_force.full)

        args_status = parser.parse_args(["status"])
        self.assertEqual(args_status.command, "status")

        args_rec = parser.parse_args(["reconcile", "--repair-manifest"])
        self.assertEqual(args_rec.command, "reconcile")
        self.assertTrue(args_rec.repair_manifest)

        args_doc = parser.parse_args(["doctor", "--repair-manifest"])
        self.assertEqual(args_doc.command, "doctor")
        self.assertTrue(args_doc.repair_manifest)

        args_sc = parser.parse_args(["install-shortcut", "--standalone"])
        self.assertEqual(args_sc.command, "install-shortcut")
        self.assertTrue(args_sc.standalone)

    @patch("gc_chatsync.cli.cmd_status")
    def test_main_dispatches_status(self, mock_status):
        mock_status.return_value = 0
        code = main(["status"])
        self.assertEqual(code, 0)
        mock_status.assert_called_once()

    @patch("gc_chatsync.cli.launch_gui")
    def test_main_without_args_and_display_opens_gui(self, mock_gui):
        """Regression: missing `import os` crashed when no subcommand was given."""
        mock_gui.return_value = 0
        with patch.dict("os.environ", {"DISPLAY": ":0"}):
            self.assertEqual(main([]), 0)
            # Also verify %U passed by freedesktop launchers is stripped and opens GUI
            self.assertEqual(main(["%U"]), 0)
        self.assertEqual(mock_gui.call_count, 2)

    @patch("gc_chatsync.cli.launch_gui")
    @patch("gc_chatsync.cli.sys.platform", "linux")
    def test_main_without_args_headless_prints_help(self, mock_gui):
        clean_env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
        with patch.dict("os.environ", clean_env, clear=True):
            self.assertEqual(main([]), 0)
        mock_gui.assert_not_called()

    @patch("gc_chatsync.cli.cmd_status", side_effect=RuntimeError("boom"))
    def test_main_reports_command_failure(self, _):
        """Regression: undefined `logger` crashed the error handler itself."""
        self.assertEqual(main(["status"]), 1)

    @patch("gc_chatsync.desktop.integrate_desktop_shortcut", return_value=True)
    def test_main_install_shortcut_default(self, mock_integrate):
        self.assertEqual(main(["install-shortcut"]), 0)
        mock_integrate.assert_called_once_with(backup=True)

    @patch("gc_chatsync.desktop.create_standalone_shortcut", return_value=True)
    def test_main_install_shortcut_standalone(self, mock_create):
        self.assertEqual(main(["install-shortcut", "--standalone"]), 0)
        mock_create.assert_called_once()

    @patch("gc_chatsync.desktop.restore_desktop_shortcut", return_value=True)
    def test_main_restore_shortcut(self, mock_restore):
        self.assertEqual(main(["restore-shortcut"]), 0)
        mock_restore.assert_called_once()

    @patch("builtins.input", side_effect=["", "", "", "", "s"])
    @patch("gc_chatsync.cli.save_config")
    @patch("gc_chatsync.cli.SyncRunner")
    @patch("gc_chatsync.desktop.integrate_desktop_shortcut", return_value=True)
    def test_cmd_setup_yes_shortcut(self, mock_sc, mock_runner, mock_save, mock_input):
        mock_runner.return_value.drive.test_connection.return_value = True
        self.assertEqual(main(["setup"]), 0)
        mock_sc.assert_called_once_with(backup=True)

    @patch("builtins.input", side_effect=["", "", "", "", "n"])
    @patch("gc_chatsync.cli.save_config")
    @patch("gc_chatsync.cli.SyncRunner")
    @patch("gc_chatsync.desktop.create_standalone_shortcut", return_value=True)
    def test_cmd_setup_no_shortcut(self, mock_sc, mock_runner, mock_save, mock_input):
        mock_runner.return_value.drive.test_connection.return_value = True
        self.assertEqual(main(["setup"]), 0)
        mock_sc.assert_called_once()

    @patch("gc_chatsync.runner.is_antigravity_process_running", return_value=False)
    @patch("gc_chatsync.merger.restore_latest_backup")
    @patch("gc_chatsync.config.load_config")
    def test_cmd_restore_backup_uses_path_object(self, mock_load, mock_restore, mock_running):
        """Regression: cfg.antigravity_data_dir is a str, so / operator raised TypeError."""
        mock_load.return_value = AppConfig(
            device_id="test-dev",
            antigravity_data_dir=str(self.tmp_path / "antigravity"),
        )
        mock_restore.return_value = (True, "Restored from backup", self.tmp_path / "bak.db")

        code = main(["restore-backup"])
        self.assertEqual(code, 0)
        called_db_path = mock_restore.call_args[0][0]
        self.assertIsInstance(called_db_path, Path)
        self.assertEqual(called_db_path.name, "conversation_summaries.db")


if __name__ == "__main__":
    unittest.main()
