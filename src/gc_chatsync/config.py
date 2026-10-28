"""Configuration management and platform-specific path detection."""

from __future__ import annotations

import json
import os
import platform
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


@dataclass
class AppConfig:
    """Configuration settings for GC-ChatSync."""
    device_id: str
    rclone_remote: str = "gdrive"
    remote_folder: str = "GC-ChatSync"
    antigravity_data_dir: str = ""
    antigravity_bin_path: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(self, data: dict) -> AppConfig:
        return AppConfig(
            device_id=data.get("device_id", get_default_device_id()),
            rclone_remote=data.get("rclone_remote", "gdrive"),
            remote_folder=data.get("remote_folder", "GC-ChatSync"),
            antigravity_data_dir=data.get("antigravity_data_dir", str(get_default_antigravity_data_dir())),
            antigravity_bin_path=data.get("antigravity_bin_path", str(find_antigravity_executable() or "")),
        )


def get_system_name() -> str:
    """Return 'linux', 'windows', or 'darwin'."""
    return platform.system().lower()


def get_default_device_id() -> str:
    """Generate a clean device identifier (e.g. 'linux-laptop')."""
    hostname = platform.node().strip()
    sys_name = get_system_name()
    if hostname:
        return f"{sys_name}-{hostname}"
    return f"{sys_name}-device"


def get_config_dir() -> Path:
    """Return the configuration directory based on OS standard paths."""
    sys_name = get_system_name()
    if sys_name == "windows":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    else:
        xdg_config = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg_config) if xdg_config else Path.home() / ".config"

    cfg_dir = base / "gc-chatsync"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    return cfg_dir


def get_config_file_path() -> Path:
    """Return the path to config.json."""
    return get_config_dir() / "config.json"


def get_default_antigravity_data_dir() -> Path:
    """Return default Antigravity app data directory (~/.gemini/antigravity)."""
    sys_name = get_system_name()
    if sys_name == "windows":
        userprofile = os.environ.get("USERPROFILE")
        home = Path(userprofile) if userprofile else Path.home()
    else:
        home = Path.home()
    return home / ".gemini" / "antigravity"


def find_antigravity_executable() -> Optional[Path]:
    """Search for the Antigravity executable across common OS locations."""
    sys_name = get_system_name()

    # 1. Check PATH
    cmd_name = "antigravity.exe" if sys_name == "windows" else "antigravity"
    in_path = shutil.which(cmd_name)
    if in_path:
        return Path(in_path)

    candidates: list[Path] = []

    if sys_name == "windows":
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        prog_files = os.environ.get("ProgramFiles", "C:\\Program Files")
        prog_files_x86 = os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")

        if local_app_data:
            candidates.append(Path(local_app_data) / "Programs" / "Antigravity" / "Antigravity.exe")
            candidates.append(Path(local_app_data) / "Antigravity" / "Antigravity.exe")
        candidates.append(Path(prog_files) / "Antigravity" / "Antigravity.exe")
        candidates.append(Path(prog_files_x86) / "Antigravity" / "Antigravity.exe")
    else:
        home = Path.home()
        candidates.extend([
            Path("/usr/bin/antigravity"),
            Path("/usr/local/bin/antigravity"),
            Path("/opt/Antigravity/antigravity"),
            Path("/opt/antigravity/antigravity"),
            home / "Downloads" / "Antigravity" / "Antigravity-x64" / "antigravity",
            home / ".local" / "bin" / "antigravity",
            home / "Applications" / "Antigravity" / "antigravity",
        ])

    for candidate in candidates:
        if candidate.is_file() and (sys_name == "windows" or os.access(candidate, os.X_OK)):
            return candidate

    return None


def load_config() -> AppConfig:
    """Load config from disk or create default."""
    cfg_file = get_config_file_path()
    if cfg_file.is_file():
        try:
            with open(cfg_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return AppConfig.from_dict(data)
        except Exception:
            pass

    # Defaults
    bin_path = find_antigravity_executable()
    cfg = AppConfig(
        device_id=get_default_device_id(),
        rclone_remote="gdrive",
        remote_folder="GC-ChatSync",
        antigravity_data_dir=str(get_default_antigravity_data_dir()),
        antigravity_bin_path=str(bin_path) if bin_path else "",
    )
    save_config(cfg)
    return cfg


def save_config(cfg: AppConfig) -> None:
    """Save config to config.json."""
    cfg_file = get_config_file_path()
    with open(cfg_file, "w", encoding="utf-8") as f:
        json.dump(cfg.to_dict(), f, indent=2)
