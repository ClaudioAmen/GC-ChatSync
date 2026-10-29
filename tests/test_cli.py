"""Unit tests for the CLI interface."""

import sys
import unittest
from unittest.mock import MagicMock, patch

from gc_chatsync.cli import build_parser, main


class TestCLI(unittest.TestCase):

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

        args_push_force = parser.parse_args(["push", "--force"])
        self.assertEqual(args_push_force.command, "push")
        self.assertTrue(args_push_force.force)

        args_status = parser.parse_args(["status"])
        self.assertEqual(args_status.command, "status")

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
        env = {
            "HOME": "/tmp",
            "USERPROFILE": "C:\\Users\\Default" if sys.platform == "win32" else "/tmp",
            "APPDATA": "C:\\Users\\Default\\AppData\\Roaming" if sys.platform == "win32" else "/tmp",
        }
        with patch.dict("os.environ", env, clear=True):
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


if __name__ == "__main__":
    unittest.main()
