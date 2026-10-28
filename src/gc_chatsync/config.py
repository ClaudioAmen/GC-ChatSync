import json
import logging
import os
import platform
import re
import shutil
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger("gc_chatsync.config")


@dataclass
class AppConfig:
    """Application configuration for GC-ChatSync."""
    device_id: str
    rclone_remote: str = "gdrive"
    remote_folder: str = "GC-ChatSync"
    antigravity_data_dir: str = ""
    antigravity_bin_path: str = ""
    workspace_path_mappings: Dict[str, str] = field(default_factory=dict)
    auto_sync_on_launch: bool = True
    auto_sync_on_close: bool = True
    check_interval_seconds: int = 60

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> AppConfig:
        return cls(
            device_id=data.get("device_id", get_default_device_id()),
            rclone_remote=data.get("rclone_remote", "gdrive"),
            remote_folder=data.get("remote_folder", "GC-ChatSync"),
            antigravity_data_dir=data.get("antigravity_data_dir", str(get_default_antigravity_data_dir())),
            antigravity_bin_path=data.get("antigravity_bin_path", str(find_antigravity_executable() or "")),
            workspace_path_mappings=data.get("workspace_path_mappings", {}),
            auto_sync_on_launch=data.get("auto_sync_on_launch", True),
            auto_sync_on_close=data.get("auto_sync_on_close", True),
            check_interval_seconds=data.get("check_interval_seconds", 60),
        )


def get_system_name() -> str:
    """Return 'windows', 'linux', or 'darwin'."""
    return platform.system().lower()


def get_config_dir() -> Path:
    """Return the configuration directory based on OS standard paths."""
    sys_name = get_system_name()
    try:
        home = Path.home()
    except RuntimeError:
        home = Path("C:/") if sys_name == "windows" else Path("/tmp")

    if sys_name == "windows":
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else home / "AppData" / "Roaming"
    else:
        xdg_config = os.environ.get("XDG_CONFIG_HOME")
        base = Path(xdg_config) if xdg_config else home / ".config"

    cfg_dir = base / "gc-chatsync"
    try:
        cfg_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return cfg_dir


def get_config_file_path() -> Path:
    """Return the path to config.json."""
    return get_config_dir() / "config.json"


def get_logs_dir() -> Path:
    """Return the logs directory based on OS standard paths."""
    log_dir = get_config_dir() / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return log_dir


def get_log_file_path() -> Path:
    """Return the path to sync.log."""
    return get_logs_dir() / "sync.log"


def get_default_device_id() -> str:
    """Generate a readable default device ID (e.g. windows-MyPC or linux-ThinkPad)."""
    os_name = get_system_name()
    node = platform.node() or "device"
    clean_node = "".join(c for c in node if c.isalnum() or c in "-_")
    return f"{os_name}-{clean_node}"


def get_default_antigravity_data_dir() -> Path:
    """Return the default Antigravity data storage directory."""
    sys_name = get_system_name()
    try:
        home = Path.home()
    except RuntimeError:
        home = Path("C:/") if sys_name == "windows" else Path("/tmp")

    if sys_name == "windows":
        return home / ".gemini" / "antigravity"
    return home / ".gemini" / "antigravity"


def get_default_antigravity_projects_dir() -> Path:
    """Return the default Antigravity projects definitions directory."""
    sys_name = get_system_name()
    try:
        home = Path.home()
    except RuntimeError:
        home = Path("C:/") if sys_name == "windows" else Path("/tmp")

    return home / ".gemini" / "config" / "projects"


def find_antigravity_executable() -> Optional[Path]:
    """Attempt to locate the Antigravity binary on the system."""
    sys_name = get_system_name()
    which_bin = shutil.which("antigravity") or shutil.which("antigravity.exe")
    if which_bin:
        return Path(which_bin)

    try:
        home = Path.home()
    except RuntimeError:
        home = Path("C:/") if sys_name == "windows" else Path("/tmp")

    if sys_name == "linux":
        candidates = [
            Path("/usr/bin/antigravity"),
            Path("/usr/local/bin/antigravity"),
            Path("/opt/antigravity/antigravity"),
            Path("/opt/Google/Antigravity/antigravity"),
            home / ".local" / "bin" / "antigravity",
            home / "Applications" / "antigravity",
        ]
    elif sys_name == "windows":
        local_app_data = os.environ.get("LOCALAPPDATA")
        prog_files = os.environ.get("ProgramFiles")
        candidates = []
        if local_app_data:
            candidates.append(Path(local_app_data) / "Programs" / "antigravity" / "Antigravity.exe")
            candidates.append(Path(local_app_data) / "Programs" / "Antigravity" / "Antigravity.exe")
        if prog_files:
            candidates.append(Path(prog_files) / "Antigravity" / "Antigravity.exe")
            candidates.append(Path(prog_files) / "Google" / "Antigravity" / "Antigravity.exe")
        candidates.append(home / "AppData" / "Local" / "Programs" / "antigravity" / "Antigravity.exe")
    else:
        candidates = [
            Path("/Applications/Antigravity.app/Contents/MacOS/Antigravity"),
            home / "Applications" / "Antigravity.app" / "Contents" / "MacOS" / "Antigravity",
        ]

    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate

    return None


def get_antigravity_version(bin_path: Optional[str] = None) -> Optional[str]:
    """Detect Antigravity application version from user logs or installation directory."""
    log_candidates = []
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            log_candidates.append(Path(appdata) / "Antigravity" / "logs" / "main.log")
            log_candidates.append(Path(appdata) / "Antigravity" / "logs" / "language_server.log")
    else:
        try:
            home = Path.home()
            log_candidates.append(home / ".config" / "Antigravity" / "logs" / "main.log")
            log_candidates.append(home / ".config" / "Antigravity" / "logs" / "language_server.log")
        except RuntimeError:
            pass

    for log_path in log_candidates:
        if log_path.is_file():
            try:
                size = log_path.stat().st_size
                with open(log_path, "rb") as f:
                    if size > 30000:
                        f.seek(size - 30000)
                    text = f.read().decode("utf-8", errors="ignore")
                matches = re.findall(
                    r"(?:Starting app \(v|Language server version:\s*)([0-9]+\.[0-9]+\.[0-9]+)",
                    text,
                )
                if matches:
                    return matches[-1]
            except Exception:
                pass

    target_bin = Path(bin_path) if bin_path else find_antigravity_executable()
    if target_bin:
        try:
            version_file = target_bin.parent / "version"
            if version_file.is_file():
                ver = version_file.read_text(encoding="utf-8").strip()
                if ver:
                    return ver
        except Exception:
            pass
    return None


def load_config() -> AppConfig:
    """Load configuration from config.json, or create default if not present."""
    cfg_path = get_config_file_path()
    if not cfg_path.is_file():
        cfg = AppConfig(
            device_id=get_default_device_id(),
            antigravity_data_dir=str(get_default_antigravity_data_dir()),
            antigravity_bin_path=str(find_antigravity_executable() or "antigravity"),
        )
        save_config(cfg)
        return cfg

    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return AppConfig.from_dict(data)
    except Exception as e:
        logger.error(f"Failed to load config from {cfg_path}: {e}. Using defaults.")
        return AppConfig(
            device_id=get_default_device_id(),
            antigravity_data_dir=str(get_default_antigravity_data_dir()),
            antigravity_bin_path=str(find_antigravity_executable() or "antigravity"),
        )


def save_config(config: AppConfig) -> bool:
    """Persist AppConfig to config.json."""
    cfg_path = get_config_file_path()
    try:
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(config.to_dict(), f, indent=2)
        return True
    except Exception as e:
        logger.error(f"Failed to save config to {cfg_path}: {e}")
        return False
