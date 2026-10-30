"""Unit tests for system theme detection."""

import sys
import unittest
from unittest.mock import MagicMock, patch

from gc_chatsync import theme


def _completed(stdout: str, returncode: int = 0) -> MagicMock:
    res = MagicMock()
    res.stdout = stdout
    res.returncode = returncode
    return res


class TestLinuxDetection(unittest.TestCase):

    @patch("gc_chatsync.theme.platform.system", return_value="Linux")
    @patch("gc_chatsync.theme.subprocess.run")
    def test_prefer_dark(self, mock_run, _):
        mock_run.return_value = _completed("'prefer-dark'\n")
        self.assertEqual(theme.detect_system_theme(), theme.DARK)

    @patch("gc_chatsync.theme.platform.system", return_value="Linux")
    @patch("gc_chatsync.theme.subprocess.run")
    def test_prefer_light(self, mock_run, _):
        mock_run.return_value = _completed("'prefer-light'\n")
        self.assertEqual(theme.detect_system_theme(), theme.LIGHT)

    @patch("gc_chatsync.theme.platform.system", return_value="Linux")
    @patch("gc_chatsync.theme.subprocess.run")
    def test_default_scheme_uses_gtk_theme_name(self, mock_run, _):
        mock_run.side_effect = [_completed("'default'"), _completed("'Adwaita-dark'")]
        self.assertEqual(theme.detect_system_theme(), theme.DARK)

    @patch("gc_chatsync.theme.platform.system", return_value="Linux")
    @patch("gc_chatsync.theme.subprocess.run")
    def test_default_scheme_light_gtk_theme(self, mock_run, _):
        mock_run.side_effect = [_completed("'default'"), _completed("'Yaru'")]
        self.assertEqual(theme.detect_system_theme(), theme.LIGHT)

    @patch("gc_chatsync.theme.platform.system", return_value="Linux")
    @patch("gc_chatsync.theme.subprocess.run", side_effect=FileNotFoundError("gsettings"))
    def test_missing_gsettings_falls_back_to_dark(self, *_):
        self.assertEqual(theme.detect_system_theme(), theme.DARK)


class TestWindowsDetection(unittest.TestCase):

    def _fake_winreg(self, value=None, error=None) -> MagicMock:
        fake = MagicMock()
        if error:
            fake.OpenKey.side_effect = error
        else:
            fake.QueryValueEx.return_value = (value, 4)
        return fake

    @patch("gc_chatsync.theme.platform.system", return_value="Windows")
    def test_light_mode(self, _):
        with patch.dict(sys.modules, {"winreg": self._fake_winreg(value=1)}):
            self.assertEqual(theme.detect_system_theme(), theme.LIGHT)

    @patch("gc_chatsync.theme.platform.system", return_value="Windows")
    def test_dark_mode(self, _):
        with patch.dict(sys.modules, {"winreg": self._fake_winreg(value=0)}):
            self.assertEqual(theme.detect_system_theme(), theme.DARK)

    @patch("gc_chatsync.theme.platform.system", return_value="Windows")
    def test_registry_error_falls_back_to_dark(self, _):
        fake = self._fake_winreg(error=OSError("key not found"))
        with patch.dict(sys.modules, {"winreg": fake}):
            self.assertEqual(theme.detect_system_theme(), theme.DARK)


class TestPalette(unittest.TestCase):

    def test_explicit_palettes(self):
        self.assertEqual(theme.get_palette(theme.LIGHT).name, theme.LIGHT)
        self.assertEqual(theme.get_palette(theme.DARK).name, theme.DARK)

    @patch("gc_chatsync.theme.platform.system", return_value="Plan9")
    def test_unknown_os_uses_dark(self, _):
        self.assertEqual(theme.get_palette().name, theme.DARK)

    def test_gui_rounded_components_and_theme_toggle(self):
        try:
            import tkinter as tk
        except ImportError:
            self.skipTest("Tkinter not installed")

        from unittest.mock import patch
        from gc_chatsync.gui import ChatSyncGUI

        try:
            root = tk.Tk()
            root.withdraw()
        except tk.TclError:
            self.skipTest("No display available for Tkinter")

        try:
            with patch("gc_chatsync.gui.ChatSyncGUI.refresh_status_async"):
                gui = ChatSyncGUI(root, palette=theme.DARK_PALETTE)
                self.assertEqual(gui.palette.name, theme.DARK)
                gui._set_progress(65, "Testing progress bar...")
                self.assertEqual(gui.progress_bar["value"], 65)

                # Toggle theme Dark -> Light -> Dark
                gui.toggle_theme()
                self.assertEqual(gui.palette.name, theme.LIGHT)
                gui.toggle_theme()
                self.assertEqual(gui.palette.name, theme.DARK)

                # Disable and re-enable rounded buttons
                gui._set_buttons_state(False)
                self.assertFalse(gui.btn_run._enabled)
                gui._set_buttons_state(True)
                self.assertTrue(gui.btn_run._enabled)
                gui._on_close()
        finally:
            try:
                root.destroy()
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
