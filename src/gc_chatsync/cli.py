"""Command-line interface for GC-ChatSync."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from gc_chatsync import __version__
from gc_chatsync.config import (
    AppConfig,
    find_antigravity_executable,
    get_config_file_path,
    get_default_antigravity_data_dir,
    get_default_device_id,
    get_log_file_path,
    load_config,
    save_config,
)
from gc_chatsync.gui import launch_gui
from gc_chatsync.runner import SyncRunner

logger = logging.getLogger("gc_chatsync.cli")

def setup_logging(verbose: bool = False) -> None:
    """Configure console logging and rotating file logging."""
    level = logging.DEBUG if verbose else logging.INFO
    format_str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # capture all to file, filter for console

    # Close and clear existing handlers
    for h in list(root_logger.handlers):
        try:
            h.close()
        except Exception:
            pass
        root_logger.removeHandler(h)

    # 1. Console handler
    c_handler = logging.StreamHandler(sys.stdout)
    c_handler.setLevel(level)
    c_format = format_str if verbose else "[%(levelname)s] %(message)s"
    c_handler.setFormatter(logging.Formatter(c_format, datefmt="%H:%M:%S"))
    root_logger.addHandler(c_handler)

    # 2. File handler (persistent across sessions, 5MB limit with 3 backups)
    log_file = get_log_file_path()
    try:
        f_handler = RotatingFileHandler(
            log_file,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        f_handler.setLevel(logging.DEBUG)
        f_handler.setFormatter(logging.Formatter(format_str, datefmt=datefmt))
        root_logger.addHandler(f_handler)
    except Exception as e:
        c_handler.handle(
            logging.LogRecord(
                "gc_chatsync", logging.WARNING, "", 0, f"Could not initialize log file: {e}", (), None
            )
        )


def cmd_status(args: argparse.Namespace) -> int:
    """Display current synchronization and environment status."""
    cfg = load_config()
    runner = SyncRunner(cfg)
    status = runner.get_status()

    print("\n" + "=" * 55)
    print(f"🌌 GC-ChatSync v{__version__} — System Status")
    print("=" * 55)
    print(f"Device ID:         {status.device_id}")
    print(f"Operating System:  {status.os_name.capitalize()}")
    print(f"Data Directory:    {status.data_dir}")
    print(f"Projects Directory:{status.projects_dir}")
    print(f"Antigravity Path:  {status.bin_path or '(not configured)'}")
    print(f"Rclone Remote:     {status.remote_name}")
    print(f"Log File:          {get_log_file_path()}")

    conn_icon = "✅ Connected" if status.drive_connected else "❌ Disconnected (check rclone config)"
    print(f"Google Drive:      {conn_icon}")

    if status.drive_connected:
        print("-" * 55)
        print(f"Cloud Last Device: {status.cloud_last_device or '(no sync yet)'}")
        print(f"Cloud Last Sync:   {status.cloud_last_sync_utc or 'N/A'}")
        if status.cloud_epoch:
            print(f"Cloud Revision:    Epoch {status.cloud_epoch}")
        if status.active_device:
            print(f"Active Session:    ⚠️ Device '{status.active_device}' active since {status.active_since_utc}")
        if status.session_dirty:
            print("Pending Changes:   ⚠️ Previous session exited without pushing! Run: gc-chatsync push")
        if status.cloud_last_device is None:
            sync_icon = "⚪ Cloud empty (initial push needed)"
        elif status.in_sync:
            sync_icon = "🟢 Up to date"
        else:
            sync_icon = f"🟡 Updates available from {status.cloud_last_device}"
        print(f"Sync Alignment:    {sync_icon}")
    print("=" * 55 + "\n")
    return 0


def cmd_pull(args: argparse.Namespace) -> int:
    """Execute manual pull from Google Drive."""
    cfg = load_config()
    runner = SyncRunner(cfg)
    success = runner.pull(force=args.force)
    return 0 if success else 1


def cmd_push(args: argparse.Namespace) -> int:
    """Execute manual push to Google Drive."""
    cfg = load_config()
    runner = SyncRunner(cfg)
    success = runner.push()
    return 0 if success else 1


def cmd_run(args: argparse.Namespace) -> int:
    """Execute full wrapper lifecycle: Pull -> Launch -> Push."""
    cfg = load_config()
    runner = SyncRunner(cfg)
    if getattr(args, "no_sync", False):
        return runner.launch_app(extra_args=args.app_args)
    return runner.run(extra_args=args.app_args)


def cmd_launch(args: argparse.Namespace) -> int:
    """Directly launch Antigravity without synchronization."""
    cfg = load_config()
    runner = SyncRunner(cfg)
    return runner.launch_app(extra_args=args.app_args)


def cmd_setup(args: argparse.Namespace) -> int:
    """Interactive wizard to configure GC-ChatSync."""
    cfg = load_config()
    print("\n" + "=" * 55)
    print("🌌 GC-ChatSync Interactive Setup Wizard")
    print("=" * 55)

    default_dev_id = cfg.device_id or get_default_device_id()
    dev_id_input = input(f"Device Identifier [{default_dev_id}]: ").strip()
    if dev_id_input:
        cfg.device_id = dev_id_input

    remote_input = input(f"Rclone Remote Name [{cfg.rclone_remote}]: ").strip()
    if remote_input:
        cfg.rclone_remote = remote_input

    folder_input = input(f"Google Drive Target Folder [{cfg.remote_folder}]: ").strip()
    if folder_input:
        cfg.remote_folder = folder_input

    # Antigravity Executable
    default_bin = cfg.antigravity_bin_path or str(find_antigravity_executable() or "")
    bin_input = input(f"Antigravity Executable Path [{default_bin}]: ").strip()
    if bin_input:
        cfg.antigravity_bin_path = bin_input
    elif default_bin:
        cfg.antigravity_bin_path = default_bin

    save_config(cfg)
    cfg_file = get_config_file_path()
    print(f"\n✅ Configuration saved to: {cfg_file}")

    # Test Drive connection
    runner = SyncRunner(cfg)
    print("Testing Google Drive connection via rclone...")
    if runner.drive.test_connection():
        print("✅ Google Drive connected successfully!\n")
    else:
        print("⚠️ Warning: Google Drive connection failed. Please ensure 'rclone config' is configured.\n")

    # Desktop shortcut integration
    integrate_input = input("Connect GC-ChatSync directly to standard Antigravity application icon? [Y/n]: ").strip().lower()
    if integrate_input not in ("n", "no"):
        from gc_chatsync.desktop import integrate_desktop_shortcut
        if integrate_desktop_shortcut(backup=True):
            print("✅ Standard Antigravity application icon linked successfully (backup saved)!\n")
        else:
            print("⚠️ Could not update desktop shortcut automatically (you can retry with: gc-chatsync install-shortcut).\n")
    else:
        from gc_chatsync.desktop import create_standalone_shortcut
        if create_standalone_shortcut():
            print("✅ Standalone 'GC-ChatSync (Antigravity)' desktop shortcut created successfully!\n")
        else:
            print("⚠️ Could not create standalone desktop shortcut.\n")

    return 0


def build_parser() -> argparse.ArgumentParser:
    """Construct CLI argument parser with global options supported on all commands."""
    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable detailed debug logs",
    )

    parser = argparse.ArgumentParser(
        prog="gc-chatsync",
        description="Automated Google Drive sync for Google Antigravity chat histories, brain artifacts and projects.",
        parents=[common_parser],
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available subcommands")

    # Command: run
    p_run = subparsers.add_parser(
        "run",
        parents=[common_parser],
        help="Orchestrate sync lifecycle: pull deltas, launch Antigravity, push updates on exit.",
    )
    p_run.add_argument(
        "--no-sync",
        action="store_true",
        help="Bypass cloud sync and launch Antigravity directly.",
    )
    p_run.add_argument("app_args", nargs="*", help="Arguments forwarded directly to Antigravity.")

    # Command: launch
    p_launch = subparsers.add_parser(
        "launch",
        parents=[common_parser],
        help="Directly launch Antigravity without synchronization.",
    )
    p_launch.add_argument("app_args", nargs="*", help="Arguments forwarded directly to Antigravity.")

    # Command: pull
    p_pull = subparsers.add_parser(
        "pull",
        parents=[common_parser],
        help="Pull deltas and merge conversations/projects from Google Drive.",
    )
    p_pull.add_argument("-f", "--force", action="store_true", help="Force pull even if this device was the last updater.")

    # Command: push
    subparsers.add_parser(
        "push",
        parents=[common_parser],
        help="Push local conversations, brain artifacts, and projects to Google Drive.",
    )

    # Command: status
    subparsers.add_parser(
        "status",
        parents=[common_parser],
        help="Show current sync status and cloud alignment.",
    )

    # Command: setup
    subparsers.add_parser(
        "setup",
        parents=[common_parser],
        help="Run interactive setup wizard.",
    )

    # Command: gui
    subparsers.add_parser(
        "gui",
        parents=[common_parser],
        help="Launch the graphical user interface.",
    )

    # Command: install-shortcut
    p_shortcut = subparsers.add_parser(
        "install-shortcut",
        parents=[common_parser],
        help="Connect GC-ChatSync directly to the standard Antigravity application icon.",
    )
    p_shortcut.add_argument(
        "-s", "--standalone",
        action="store_true",
        help="Create a separate GC-ChatSync application shortcut instead of replacing the default icon.",
    )

    # Command: restore-shortcut
    subparsers.add_parser(
        "restore-shortcut",
        parents=[common_parser],
        help="Restore original Antigravity application icon from backup.",
    )

    # Command: reconcile
    subparsers.add_parser(
        "reconcile",
        parents=[common_parser],
        help="Diagnose and repair inconsistencies: index orphan sessions, clear stale locks, audit cloud.",
    )

    # Command: restore-backup
    subparsers.add_parser(
        "restore-backup",
        parents=[common_parser],
        help="Restore conversation_summaries.db from the latest snapshot in .backups/.",
    )

    return parser


def _cmd_install_shortcut(args: argparse.Namespace) -> int:
    """Handle shortcut installation."""
    from gc_chatsync.desktop import create_standalone_shortcut, integrate_desktop_shortcut
    if getattr(args, "standalone", False):
        success = create_standalone_shortcut()
        if success:
            print("✅ Standalone 'GC-ChatSync (Antigravity)' shortcut created successfully.")
        return 0 if success else 1
    else:
        success = integrate_desktop_shortcut(backup=True)
        if success:
            print("✅ Standard Antigravity application icon linked to GC-ChatSync (backup saved).")
        return 0 if success else 1


def _cmd_restore_shortcut(args: argparse.Namespace) -> int:
    """Handle shortcut restore."""
    from gc_chatsync.desktop import restore_desktop_shortcut
    success = restore_desktop_shortcut()
    if success:
        print("✅ Original Antigravity application icon restored from backup.")
    return 0 if success else 1


def _cmd_reconcile(args: argparse.Namespace) -> int:
    """Handle reconcile command."""
    from gc_chatsync.config import load_config
    from gc_chatsync.reconcile import run_reconcile
    from gc_chatsync.runner import SyncRunner

    cfg = load_config()
    runner = SyncRunner(cfg)

    def on_progress(pct: int, msg: str):
        print(f"[{pct}%] {msg}")

    print("🩺 Running GC-ChatSync Doctor & Reconcile...\n")
    report = run_reconcile(runner, progress_cb=on_progress)

    print("\n" + "=" * 50)
    if report.status == "SUCCESS":
        print("✅ RECONCILIATION COMPLETE: Everything in sync.")
    elif report.status == "WARNING":
        print("⚠️ RECONCILIATION COMPLETE WITH WARNINGS.")
    else:
        print("❌ RECONCILIATION ABORTED.")
    print("=" * 50)

    print(f"• Orphans Indexed: {report.orphans_indexed} / {report.orphans_found}")
    print(f"• Corrupt Files: {len(report.corrupt_files)}")
    print(f"• Stale Witness Cleared: {report.stale_witness_cleared}")
    print(f"• Remote Conversations Synced: {report.remote_synced}")

    if report.warnings:
        print("\nWarnings:")
        for w in report.warnings:
            print(f"  ⚠️ {w}")
    if report.errors:
        print("\nErrors:")
        for err in report.errors:
            print(f"  ❌ {err}")

    return report.code


def _cmd_restore_backup(args: argparse.Namespace) -> int:
    """Handle restore-backup command."""
    from gc_chatsync.config import load_config
    from gc_chatsync.merger import restore_latest_backup
    from gc_chatsync.runner import is_antigravity_process_running

    if is_antigravity_process_running():
        print("❌ Error: Antigravity is currently running. Please close it before restoring backup.")
        return 2

    cfg = load_config()
    db_path = cfg.antigravity_data_dir / "conversation_summaries.db"
    success, msg, backup_path = restore_latest_backup(db_path)

    if success:
        print(f"✅ {msg}")
        return 0
    else:
        print(f"❌ {msg}")
        return 1


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point for the gc-chatsync command line interface."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    raw_args = sys.argv[1:] if argv is None else argv
    # Filter out freedesktop field codes (%U, %F, etc.) and empty args if passed by desktop launchers
    cleaned_args = [a for a in raw_args if a not in ("%U", "%u", "%F", "%f", "")]

    parser = build_parser()
    args = parser.parse_args(cleaned_args)

    setup_logging(verbose=args.verbose)

    commands = {
        "run": cmd_run,
        "launch": cmd_launch,
        "pull": cmd_pull,
        "push": cmd_push,
        "status": cmd_status,
        "setup": cmd_setup,
        "gui": lambda args: launch_gui(),
        "install-shortcut": _cmd_install_shortcut,
        "restore-shortcut": _cmd_restore_shortcut,
        "reconcile": _cmd_reconcile,
        "restore-backup": _cmd_restore_backup,
    }

    if not args.command:
        # If launched without subcommands and graphical display is present, open GUI
        has_display = bool(
            sys.platform == "win32"
            or os.environ.get("DISPLAY")
            or os.environ.get("WAYLAND_DISPLAY")
        )
        if has_display:
            return launch_gui()

        parser.print_help()
        return 0

    handler = commands.get(args.command)
    if handler:
        try:
            return handler(args)
        except Exception as e:
            logger.error(f"Command '{args.command}' failed: {e}", exc_info=args.verbose)
            print(f"\n❌ Error: {e}\n")
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
