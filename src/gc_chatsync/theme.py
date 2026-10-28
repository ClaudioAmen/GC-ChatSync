"""System theme detection and color palettes for the GUI.

Uses only the standard library. Detection runs once at startup;
if the system preference cannot be determined, the dark theme is used.
"""

from __future__ import annotations

import logging
import platform
import subprocess
import sys
from dataclasses import dataclass

logger = logging.getLogger("gc_chatsync.theme")

DARK = "dark"
LIGHT = "light"
FALLBACK_THEME = DARK


@dataclass(frozen=True)
class Palette:
    """Color set used by the GUI widgets."""
    name: str
    bg: str
    surface: str
    border: str
    fg: str
    muted: str
    accent: str
    accent_hover: str
    accent_fg: str
    button: str
    button_hover: str
    success: str
    warning: str
    error: str
    log_bg: str
    log_fg: str


DARK_PALETTE = Palette(
    name=DARK,
    bg="#1e1f22",
    surface="#2b2d31",
    border="#3a3c42",
    fg="#e6e6e6",
    muted="#9aa0a6",
    accent="#4f8cff",
    accent_hover="#6a9dff",
    accent_fg="#ffffff",
    button="#383a40",
    button_hover="#45474e",
    success="#4caf50",
    warning="#ffb74d",
    error="#ef5350",
    log_bg="#16171a",
    log_fg="#d4d4d4",
)

LIGHT_PALETTE = Palette(
    name=LIGHT,
    bg="#f5f6f8",
    surface="#ffffff",
    border="#d9dce1",
    fg="#1f2328",
    muted="#5f6368",
    accent="#2f6fed",
    accent_hover="#4a82f0",
    accent_fg="#ffffff",
    button="#e9ebef",
    button_hover="#dcdfe5",
    success="#2e7d32",
    warning="#e65100",
    error="#c62828",
    log_bg="#fafbfc",
    log_fg="#24292f",
)


def _run(cmd: list[str]) -> str:
    """Run a short command and return stripped stdout ('' on any failure)."""
    try:
        kwargs = {}
        if sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=3, check=False, **kwargs)
        if res.returncode != 0:
            return ""
        return res.stdout.strip().strip("'\"")
    except (OSError, subprocess.SubprocessError):
        return ""


def _detect_linux() -> str | None:
    """Detect theme on Linux desktops exposing GNOME/GTK settings."""
    scheme = _run(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"])
    if scheme == "prefer-dark":
        return DARK
    if scheme == "prefer-light":
        return LIGHT

    gtk_theme = _run(["gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"])
    if gtk_theme:
        return DARK if "dark" in gtk_theme.lower() else LIGHT

    return None


def _detect_windows() -> str | None:
    """Detect theme on Windows via the AppsUseLightTheme registry value."""
    try:
        import winreg  # type: ignore[import-not-found]

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        try:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        finally:
            winreg.CloseKey(key)
        return LIGHT if int(value) == 1 else DARK
    except Exception as e:  # registry key missing or inaccessible
        logger.debug(f"Windows theme detection failed: {e}")
        return None


def _detect_macos() -> str | None:
    """Detect theme on macOS (key is absent when light mode is active)."""
    style = _run(["defaults", "read", "-g", "AppleInterfaceStyle"])
    return DARK if style.lower() == "dark" else LIGHT


def detect_system_theme() -> str:
    """Return 'dark' or 'light' according to the OS preference, falling back to dark."""
    system = platform.system().lower()
    if system == "linux":
        detected = _detect_linux()
    elif system == "windows":
        detected = _detect_windows()
    elif system == "darwin":
        detected = _detect_macos()
    else:
        detected = None

    if detected is None:
        logger.debug(f"Could not detect system theme, using fallback '{FALLBACK_THEME}'.")
        return FALLBACK_THEME
    return detected


def get_palette(theme: str | None = None) -> Palette:
    """Return the palette for the given theme (auto-detected when None)."""
    selected = theme or detect_system_theme()
    return LIGHT_PALETTE if selected == LIGHT else DARK_PALETTE
