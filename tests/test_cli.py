"""Unit tests for the CLI interface."""

import unittest
from unittest.mock import patch

from gc_chatsync.cli import build_parser, main


class TestCLI(unittest.TestCase):

    def test_build_parser_subcommands(self):
        parser = build_parser()
        args = parser.parse_args(["run", "--verbose"])
        self.assertEqual(args.command, "run")
        self.assertTrue(args.verbose)

        args_pull = parser.parse_args(["pull", "--force"])
        self.assertEqual(args_pull.command, "pull")
        self.assertTrue(args_pull.force)

        args_push = parser.parse_args(["push"])
        self.assertEqual(args_push.command, "push")

        args_status = parser.parse_args(["status"])
        self.assertEqual(args_status.command, "status")

    @patch("gc_chatsync.cli.cmd_status")
    def test_main_dispatches_status(self, mock_status):
        mock_status.return_value = 0
        code = main(["status"])
        self.assertEqual(code, 0)
        mock_status.assert_called_once()


if __name__ == "__main__":
    unittest.main()
