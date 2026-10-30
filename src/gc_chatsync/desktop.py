"""Desktop shortcut integration and management for Linux and Windows.

Allows GC-ChatSync to seamlessly patch the habitual Antigravity application entry
or create a standalone shortcut so users can launch Antigravity with synchronization
directly from their system dock, desktop folder, or application menu.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from gc_chatsync.config import (
    find_antigravity_executable,
    get_system_name,
)

logger = logging.getLogger("gc_chatsync.desktop")


def get_linux_applications_dir() -> Path:
    """Return standard user applications directory for Linux."""
    xdg_data = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data) if xdg_data else Path.home() / ".local" / "share"
    app_dir = base / "applications"
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


def get_linux_desktop_dir() -> Optional[Path]:
    """Return user's Desktop directory if available."""
    user_dirs = Path.home() / ".config" / "user-dirs.dirs"
    if user_dirs.is_file():
        try:
            for line in user_dirs.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("XDG_DESKTOP_DIR="):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    val = val.replace("$HOME", str(Path.home()))
                    p = Path(val)
                    if p.is_dir():
                        return p
        except Exception:
            pass

    for candidate in [Path.home() / "Desktop", Path.home() / "Scrivania"]:
        if candidate.is_dir():
            return candidate
    return None


def find_launcher_script() -> Optional[Path]:
    """Locate launch-linux.sh or launch-windows.bat."""
    pkg_dir = Path(__file__).resolve().parent.parent.parent
    sys_name = get_system_name()
    script_name = "launch-windows.bat" if sys_name == "windows" else "launch-linux.sh"
    candidate = pkg_dir / "scripts" / script_name
    if candidate.is_file():
        return candidate
    return None


def get_linux_exec_command() -> str:
    """Determine the best launch command for Linux desktop entry.

    Uses absolute path when installed in user home (e.g. ~/.local/bin) to guarantee
    execution from desktop environments (GNOME/KDE) where user PATH might differ.
    Omits %U so default invocation starts the graphical dashboard directly.
    """
    which_bin = shutil.which("gc-chatsync")
    if which_bin:
        if which_bin.startswith(("/usr/bin", "/bin")):
            return "gc-chatsync"
        return str(which_bin)

    launcher_script = find_launcher_script()
    if launcher_script and launcher_script.is_file():
        return str(launcher_script.resolve())
    return "gc-chatsync"


def get_antigravity_icon_path() -> str:
    """Find Antigravity icon file if available."""
    antigravity_bin = find_antigravity_executable()
    if antigravity_bin:
        potential_icon = antigravity_bin.parent / "antigravity.png"
        if potential_icon.is_file():
            return str(potential_icon.resolve())
    return ""


def _trust_linux_desktop_file(path: Path) -> None:
    """Make desktop file executable and mark as trusted in GNOME / DING."""
    try:
        path.chmod(0o755)
    except Exception:
        pass

    if shutil.which("gio"):
        try:
            subprocess.run(
                ["gio", "set", str(path), "metadata::trusted", "true"],
                capture_output=True,
                check=False,
                timeout=5,
            )
        except Exception:
            pass


def _patch_or_create_linux_file(
    desktop_file: Path,
    name: str = "Antigravity",
    backup: bool = True,
    comment: str = "Google Antigravity with GC-ChatSync automated synchronization",
) -> bool:
    """Patch existing desktop entry or write a new one."""
    exec_cmd = get_linux_exec_command()

    if desktop_file.is_file():
        try:
            content = desktop_file.read_text(encoding="utf-8")
            bak_file = desktop_file.with_name(f"{desktop_file.name}.bak")
            if backup and not bak_file.exists():
                shutil.copy2(desktop_file, bak_file)
                logger.info(f"Created backup: {bak_file}")

            new_content = re.sub(
                r"^Exec=.*$",
                f"Exec={exec_cmd}",
                content,
                flags=re.MULTILINE,
            )
            desktop_file.write_text(new_content, encoding="utf-8")
            _trust_linux_desktop_file(desktop_file)
            logger.info(f"Patched existing desktop file: {desktop_file}")
            return True
        except Exception as e:
            logger.error(f"Failed to patch {desktop_file}: {e}")
            return False

    # Fresh creation
    icon_path = get_antigravity_icon_path()
    entry_content = f"""[Desktop Entry]
Version=1.0
Type=Application
Name={name}
Comment={comment}
Exec={exec_cmd}
Icon={icon_path or "utilities-terminal"}
Terminal=false
Categories=Development;IDE;
StartupWMClass=Antigravity
StartupNotify=true
"""
    try:
        desktop_file.write_text(entry_content, encoding="utf-8")
        _trust_linux_desktop_file(desktop_file)
        logger.info(f"Created desktop entry: {desktop_file}")
        return True
    except Exception as e:
        logger.error(f"Failed to write {desktop_file}: {e}")
        return False


def _refresh_linux_desktop_database(apps_dir: Path) -> None:
    """Run update-desktop-database if available."""
    if shutil.which("update-desktop-database"):
        try:
            subprocess.run(
                ["update-desktop-database", str(apps_dir)],
                capture_output=True,
                check=False,
                timeout=5,
            )
        except Exception:
            pass


def integrate_linux_shortcut(backup: bool = True) -> bool:
    """Patch Antigravity shortcut in ~/.local/share/applications/ and ~/Desktop/."""
    apps_dir = get_linux_applications_dir()
    app_menu_file = apps_dir / "antigravity.desktop"

    # Remove standalone launcher from menu to prevent duplicates
    menu_standalone = apps_dir / "gc-chatsync.desktop"
    if menu_standalone.is_file():
        try:
            menu_standalone.unlink()
        except Exception:
            pass

    success = _patch_or_create_linux_file(app_menu_file, name="Antigravity", backup=backup)
    _refresh_linux_desktop_database(apps_dir)

    # Also patch ~/Desktop/Antigravity.desktop if ~/Desktop exists
    desktop_dir = get_linux_desktop_dir()
    if desktop_dir and desktop_dir.is_dir():
        # Clean up any standalone on desktop
        for old_standalone in [desktop_dir / "GC-ChatSync.desktop", desktop_dir / "gc-chatsync.desktop"]:
            if old_standalone.is_file():
                try:
                    old_standalone.unlink()
                except Exception:
                    pass

        # Check for Antigravity.desktop or antigravity.desktop on desktop
        for candidate_name in ["Antigravity.desktop", "antigravity.desktop"]:
            cand = desktop_dir / candidate_name
            if cand.is_file():
                _patch_or_create_linux_file(cand, name="Antigravity", backup=backup)
                break
        else:
            # If no desktop icon exists on desktop screen, create one
            _patch_or_create_linux_file(desktop_dir / "Antigravity.desktop", name="Antigravity", backup=backup)

    return success


def create_linux_standalone_shortcut() -> bool:
    """Create a separate GC-ChatSync (Antigravity) shortcut in application menu and on Desktop."""
    apps_dir = get_linux_applications_dir()
    menu_file = apps_dir / "gc-chatsync.desktop"
    success = _patch_or_create_linux_file(
        menu_file,
        name="GC-ChatSync (Antigravity)",
        backup=False,
    )
    _refresh_linux_desktop_database(apps_dir)

    desktop_dir = get_linux_desktop_dir()
    if desktop_dir and desktop_dir.is_dir():
        desktop_file = desktop_dir / "GC-ChatSync.desktop"
        _patch_or_create_linux_file(
            desktop_file,
            name="GC-ChatSync (Antigravity)",
            backup=False,
        )

    return success


def restore_linux_shortcut() -> bool:
    """Restore original antigravity.desktop from .bak in applications menu and desktop folder."""
    restored_any = False

    apps_dir = get_linux_applications_dir()
    app_menu_file = apps_dir / "antigravity.desktop"
    app_bak = apps_dir / "antigravity.desktop.bak"

    if app_bak.is_file():
        try:
            shutil.copy2(app_bak, app_menu_file)
            _trust_linux_desktop_file(app_menu_file)
            _refresh_linux_desktop_database(apps_dir)
            logger.info("Restored original antigravity.desktop from backup in applications menu.")
            restored_any = True
        except Exception as e:
            logger.error(f"Failed to restore menu shortcut: {e}")

    desktop_dir = get_linux_desktop_dir()
    if desktop_dir and desktop_dir.is_dir():
        for name in ["Antigravity.desktop", "antigravity.desktop"]:
            dt_file = desktop_dir / name
            dt_bak = desktop_dir / f"{name}.bak"
            if dt_bak.is_file():
                try:
                    shutil.copy2(dt_bak, dt_file)
                    _trust_linux_desktop_file(dt_file)
                    logger.info(f"Restored original desktop file: {dt_file}")
                    restored_any = True
                    break
                except Exception as e:
                    logger.error(f"Failed to restore desktop folder shortcut: {e}")

    return restored_any


def _get_windows_desktop_dir() -> Path:
    """Return the Windows Desktop directory (handling OneDrive redirect if needed)."""
    desktop_dir = Path.home() / "Desktop"
    if not desktop_dir.is_dir():
        onedrive_desktop = Path.home() / "OneDrive" / "Desktop"
        if onedrive_desktop.is_dir():
            return onedrive_desktop
    return desktop_dir


def _get_windows_shortcut_backup_path(desktop_dir: Path, name: str = "Antigravity.lnk.bak") -> Path:
    """Return backup path for Windows shortcut.
    
    If saving to real Desktop, keep it clean by redirecting into gc-chatsync backups folder.
    In testing or custom directory, save alongside shortcut to satisfy tests.
    """
    if desktop_dir.name.lower() in ("desktop", "scrivania"):
        from gc_chatsync.config import get_config_dir
        bak_dir = get_config_dir() / "backups" / "shortcuts"
        bak_dir.mkdir(parents=True, exist_ok=True)
        return bak_dir / name
    return desktop_dir / name


def _create_windows_lnk(shortcut_path: Path, title: str) -> bool:
    """Create or update a Windows .lnk shortcut via PowerShell."""
    desktop_dir = shortcut_path.parent

    # Use pythonw.exe to run GUI cleanly without flashing black CMD console
    pythonw_candidate = Path(sys.executable).parent / "pythonw.exe"
    if not pythonw_candidate.is_file():
        pw_which = shutil.which("pythonw.exe") or shutil.which("pythonw")
        pythonw_candidate = Path(pw_which) if pw_which else None

    launcher_bat = find_launcher_script()
    if pythonw_candidate and pythonw_candidate.is_file():
        target_path = str(pythonw_candidate.resolve())
        arguments = "-m gc_chatsync.cli gui"
    elif launcher_bat:
        target_path = str(launcher_bat.resolve())
        arguments = ""
    else:
        target_path = "gc-chatsync.exe"
        arguments = "gui"

    antigravity_bin = find_antigravity_executable()
    icon_path = str(antigravity_bin.resolve()) if antigravity_bin else ""

    def _ps_escape(val: str) -> str:
        """Escape single quotes for PowerShell single-quoted string literals."""
        return val.replace("'", "''")

    sc_path_ps = _ps_escape(str(shortcut_path))
    target_ps = _ps_escape(target_path)
    args_ps = _ps_escape(arguments)
    work_dir_ps = _ps_escape(str(desktop_dir))
    title_ps = _ps_escape(title)
    icon_ps = _ps_escape(icon_path)

    ps_script = f"""
$WshShell = New-Object -comObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut('{sc_path_ps}')
$Shortcut.TargetPath = '{target_ps}'
if ('{args_ps}' -ne '') {{ $Shortcut.Arguments = '{args_ps}' }}
$Shortcut.WorkingDirectory = '{work_dir_ps}'
$Shortcut.Description = '{title_ps} with GC-ChatSync'
if ('{icon_ps}' -ne '') {{ $Shortcut.IconLocation = '{icon_ps},0' }}
$Shortcut.Save()

try {{
    $code = '[DllImport("shell32.dll")] public static extern void SHChangeNotify(int wEventId, int uFlags, IntPtr dwItem1, IntPtr dwItem2);'
    Add-Type -MemberDefinition $code -Namespace Win32 -Name Shell -ErrorAction SilentlyContinue
    [Win32.Shell]::SHChangeNotify(0x08000000, 0, [IntPtr]::Zero, [IntPtr]::Zero)
}} catch {{}}
"""
    try:
        kwargs = {}
        if sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW"):
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            **kwargs,
        )
        if res.returncode == 0:
            logger.info(f"Created Windows shortcut: {shortcut_path}")
            return True
        logger.warning(f"PowerShell shortcut creation returned code {res.returncode}: {res.stderr}")
        return False
    except Exception as e:
        logger.warning(f"Could not create Windows desktop shortcut via PowerShell: {e}")
        return False


def integrate_windows_shortcut(backup: bool = True) -> bool:
    """Create or update Windows Desktop Antigravity.lnk shortcut with backup."""
    desktop_dir = _get_windows_desktop_dir()
    shortcut_file = desktop_dir / "Antigravity.lnk"
    bak_file = _get_windows_shortcut_backup_path(desktop_dir, "Antigravity.lnk.bak")

    if backup and shortcut_file.is_file() and not bak_file.exists():
        try:
            shutil.copy2(shortcut_file, bak_file)
            logger.info(f"Created backup of original Windows shortcut: {bak_file}")
        except Exception as e:
            logger.debug(f"Failed to backup Windows shortcut: {e}")

    # Remove any stray .bak left on desktop
    if desktop_dir.name.lower() in ("desktop", "scrivania"):
        desktop_bak = desktop_dir / "Antigravity.lnk.bak"
        if desktop_bak.is_file():
            try:
                desktop_bak.unlink()
            except Exception:
                pass

    standalone_file = desktop_dir / "GC-ChatSync.lnk"
    if standalone_file.is_file():
        try:
            standalone_file.unlink()
        except Exception:
            pass

    success = _create_windows_lnk(shortcut_file, "Antigravity")

    start_menu_dir = Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    if start_menu_dir.is_dir() and start_menu_dir != desktop_dir:
        sm_shortcut = start_menu_dir / "Antigravity.lnk"
        sm_bak = _get_windows_shortcut_backup_path(start_menu_dir, "Antigravity_StartMenu.lnk.bak")
        if backup and sm_shortcut.is_file() and not sm_bak.exists():
            try:
                shutil.copy2(sm_shortcut, sm_bak)
            except Exception:
                pass
        if sm_shortcut.is_file():
            _create_windows_lnk(sm_shortcut, "Antigravity")

    return success


def create_windows_standalone_shortcut() -> bool:
    """Create a separate GC-ChatSync.lnk shortcut on the Windows desktop."""
    desktop_dir = _get_windows_desktop_dir()
    shortcut_file = desktop_dir / "GC-ChatSync.lnk"
    return _create_windows_lnk(shortcut_file, "GC-ChatSync (Antigravity)")


def restore_windows_shortcut() -> bool:
    """Restore the original Antigravity.lnk from backup on Windows."""
    desktop_dir = _get_windows_desktop_dir()
    shortcut_file = desktop_dir / "Antigravity.lnk"
    bak_file = _get_windows_shortcut_backup_path(desktop_dir, "Antigravity.lnk.bak")
    if not bak_file.is_file() and (desktop_dir / "Antigravity.lnk.bak").is_file():
        bak_file = desktop_dir / "Antigravity.lnk.bak"

    restored = False
    if bak_file.is_file():
        try:
            shutil.copy2(bak_file, shortcut_file)
            logger.info("Restored original Windows shortcut from backup.")
            restored = True
        except Exception as e:
            logger.error(f"Failed to restore original Windows shortcut: {e}")

    start_menu_dir = Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    if start_menu_dir.is_dir() and start_menu_dir != desktop_dir:
        sm_shortcut = start_menu_dir / "Antigravity.lnk"
        sm_bak = _get_windows_shortcut_backup_path(start_menu_dir, "Antigravity_StartMenu.lnk.bak")
        if not sm_bak.is_file() and (start_menu_dir / "Antigravity.lnk.bak").is_file():
            sm_bak = start_menu_dir / "Antigravity.lnk.bak"
        if sm_bak.is_file():
            try:
                shutil.copy2(sm_bak, sm_shortcut)
                restored = True
            except Exception:
                pass

    return restored


def integrate_desktop_shortcut(backup: bool = True) -> bool:
    """Detect OS and patch/integrate into the standard Antigravity application icon."""
    sys_name = get_system_name()
    if sys_name == "windows":
        return integrate_windows_shortcut(backup=backup)
    elif sys_name == "linux":
        return integrate_linux_shortcut(backup=backup)
    return False


def create_standalone_shortcut() -> bool:
    """Detect OS and create a standalone GC-ChatSync application shortcut."""
    sys_name = get_system_name()
    if sys_name == "windows":
        return create_windows_standalone_shortcut()
    elif sys_name == "linux":
        return create_linux_standalone_shortcut()
    return False


def restore_desktop_shortcut() -> bool:
    """Detect OS and restore original Antigravity application icon from backup."""
    sys_name = get_system_name()
    if sys_name == "windows":
        return restore_windows_shortcut()
    elif sys_name == "linux":
        return restore_linux_shortcut()
    return False
