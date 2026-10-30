"""Unit tests for desktop shortcut integration."""

import gc
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from gc_chatsync.desktop import (
    create_linux_standalone_shortcut,
    create_standalone_shortcut,
    create_windows_standalone_shortcut,
    get_linux_desktop_dir,
    get_linux_exec_command,
    integrate_desktop_shortcut,
    integrate_linux_shortcut,
    integrate_windows_shortcut,
    restore_desktop_shortcut,
    restore_linux_shortcut,
    restore_windows_shortcut,
)


class TestDesktopShortcut(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.apps_dir = Path(self.temp_dir.name) / "applications"
        self.apps_dir.mkdir(parents=True)
        self.desktop_dir = Path(self.temp_dir.name) / "Desktop"
        self.desktop_dir.mkdir(parents=True)

    def tearDown(self):
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

    @patch("shutil.which", return_value="/usr/bin/gc-chatsync")
    def test_get_linux_exec_command_system_path(self, _):
        self.assertEqual(get_linux_exec_command(), "gc-chatsync")

    @patch("shutil.which", return_value="/home/user/.local/bin/gc-chatsync")
    def test_get_linux_exec_command_user_path(self, _):
        self.assertEqual(get_linux_exec_command(), "/home/user/.local/bin/gc-chatsync")

    @patch("gc_chatsync.desktop.get_linux_applications_dir")
    @patch("gc_chatsync.desktop.get_linux_desktop_dir")
    @patch("gc_chatsync.desktop._refresh_linux_desktop_database")
    @patch("shutil.which", return_value="/usr/bin/gc-chatsync")
    def test_integrate_patches_menu_and_desktop_dir(self, mock_which, mock_refresh, mock_desktop_dir, mock_apps_dir):
        mock_apps_dir.return_value = self.apps_dir
        mock_desktop_dir.return_value = self.desktop_dir

        menu_file = self.apps_dir / "antigravity.desktop"
        screen_file = self.desktop_dir / "Antigravity.desktop"

        menu_file.write_text("""[Desktop Entry]
Name=Antigravity
Exec=/old/path/to/antigravity %U
Icon=/path/to/icon.png
Type=Application
""")
        screen_file.write_text("""[Desktop Entry]
Name=Antigravity
Exec=/old/path/to/antigravity %U
Icon=/path/to/icon.png
Type=Application
""")

        success = integrate_linux_shortcut(backup=True)
        self.assertTrue(success)

        # Verify backups
        self.assertTrue((self.apps_dir / "antigravity.desktop.bak").is_file())
        self.assertTrue((self.desktop_dir / "Antigravity.desktop.bak").is_file())

        # Verify Exec in both
        self.assertIn("Exec=gc-chatsync", menu_file.read_text())
        self.assertIn("Exec=gc-chatsync", screen_file.read_text())

    @patch("gc_chatsync.desktop.get_linux_applications_dir")
    @patch("gc_chatsync.desktop.get_linux_desktop_dir")
    @patch("gc_chatsync.desktop._refresh_linux_desktop_database")
    @patch("shutil.which", return_value="/usr/bin/gc-chatsync")
    def test_create_linux_standalone_shortcut(self, mock_which, mock_refresh, mock_desktop_dir, mock_apps_dir):
        mock_apps_dir.return_value = self.apps_dir
        mock_desktop_dir.return_value = self.desktop_dir

        success = create_linux_standalone_shortcut()
        self.assertTrue(success)

        # Menu standalone
        menu_standalone = self.apps_dir / "gc-chatsync.desktop"
        self.assertTrue(menu_standalone.is_file())
        self.assertIn("GC-ChatSync (Antigravity)", menu_standalone.read_text())

        # Desktop folder standalone
        screen_standalone = self.desktop_dir / "GC-ChatSync.desktop"
        self.assertTrue(screen_standalone.is_file())
        self.assertIn("GC-ChatSync (Antigravity)", screen_standalone.read_text())

    @patch("gc_chatsync.desktop.get_linux_applications_dir")
    @patch("gc_chatsync.desktop.get_linux_desktop_dir")
    @patch("gc_chatsync.desktop._refresh_linux_desktop_database")
    def test_restore_desktop_file(self, mock_refresh, mock_desktop_dir, mock_apps_dir):
        mock_apps_dir.return_value = self.apps_dir
        mock_desktop_dir.return_value = self.desktop_dir

        menu_file = self.apps_dir / "antigravity.desktop"
        menu_bak = self.apps_dir / "antigravity.desktop.bak"
        menu_bak.write_text("Exec=original-antigravity")
        menu_file.write_text("Exec=gc-chatsync")

        screen_file = self.desktop_dir / "Antigravity.desktop"
        screen_bak = self.desktop_dir / "Antigravity.desktop.bak"
        screen_bak.write_text("Exec=original-antigravity")
        screen_file.write_text("Exec=gc-chatsync")

        success = restore_linux_shortcut()
        self.assertTrue(success)
        self.assertEqual(menu_file.read_text(), "Exec=original-antigravity")
        self.assertEqual(screen_file.read_text(), "Exec=original-antigravity")

    @patch("gc_chatsync.desktop.get_system_name")
    @patch("gc_chatsync.desktop.integrate_linux_shortcut")
    @patch("gc_chatsync.desktop.create_linux_standalone_shortcut")
    @patch("gc_chatsync.desktop.restore_linux_shortcut")
    def test_unified_dispatch_linux(self, mock_restore, mock_create, mock_integrate, mock_sys):
        mock_sys.return_value = "linux"
        mock_integrate.return_value = True
        mock_create.return_value = True
        mock_restore.return_value = True

        self.assertTrue(integrate_desktop_shortcut())
        self.assertTrue(create_standalone_shortcut())
        self.assertTrue(restore_desktop_shortcut())

    @patch("gc_chatsync.desktop._get_windows_desktop_dir")
    @patch("subprocess.run")
    def test_windows_shortcuts(self, mock_run, mock_desktop_dir):
        mock_desktop_dir.return_value = self.apps_dir
        mock_run.return_value = MagicMock(returncode=0)

        # Test standalone
        self.assertTrue(create_windows_standalone_shortcut())

        # Test integrate with backup
        orig_lnk = self.apps_dir / "Antigravity.lnk"
        orig_lnk.write_text("original link data")
        self.assertTrue(integrate_windows_shortcut(backup=True))
        bak_lnk = self.apps_dir / "Antigravity.lnk.bak"
        self.assertTrue(bak_lnk.is_file())

        # Test restore
        orig_lnk.write_text("modified link data")
        self.assertTrue(restore_windows_shortcut())
        self.assertEqual(orig_lnk.read_text(), "original link data")

    @patch("subprocess.run")
    def test_windows_lnk_escapes_single_quotes(self, mock_run):
        from gc_chatsync.desktop import _create_windows_lnk
        mock_run.return_value = MagicMock(returncode=0)

        path_with_quote = self.apps_dir / "O'Brien's Desktop" / "Antigravity.lnk"
        self.assertTrue(_create_windows_lnk(path_with_quote, "O'Brien's App"))
        ps_cmd = mock_run.call_args[0][0][3]
        self.assertIn("O''Brien''s Desktop", ps_cmd)
        self.assertIn("O''Brien''s App", ps_cmd)


if __name__ == "__main__":
    unittest.main()
