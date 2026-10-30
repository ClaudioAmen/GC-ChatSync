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
    surface_alt: str
    border: str
    fg: str
    muted: str
    accent: str
    accent_hover: str
    accent_fg: str
    button: str
    button_hover: str
    button_border: str
    success: str
    success_bg: str
    warning: str
    warning_bg: str
    error: str
    error_bg: str
    log_bg: str
    log_fg: str


DARK_PALETTE = Palette(
    name=DARK,
    bg="#111318",
    surface="#1a1d24",
    surface_alt="#21252e",
    border="#2c313c",
    fg="#f0f2f5",
    muted="#8b949e",
    accent="#3b82f6",
    accent_hover="#60a5fa",
    accent_fg="#ffffff",
    button="#262b36",
    button_hover="#323846",
    button_border="#373e4d",
    success="#3fb950",
    success_bg="#142d1e",
    warning="#f59e0b",
    warning_bg="#2f2412",
    error="#f85149",
    error_bg="#32181a",
    log_bg="#0d0f13",
    log_fg="#d1d5db",
)

LIGHT_PALETTE = Palette(
    name=LIGHT,
    bg="#f3f5f8",
    surface="#ffffff",
    surface_alt="#f8fafc",
    border="#dce1e8",
    fg="#111827",
    muted="#64748b",
    accent="#2563eb",
    accent_hover="#3b82f6",
    accent_fg="#ffffff",
    button="#eef2f6",
    button_hover="#e2e8f0",
    button_border="#cbd5e1",
    success="#16a34a",
    success_bg="#dcfce7",
    warning="#d97706",
    warning_bg="#fef3c7",
    error="#dc2626",
    error_bg="#fee2e2",
    log_bg="#f8fafc",
    log_fg="#1e293b",
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
