"""Command-line interface for GC-ChatSync."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

from gc_chatsync import __version__
from gc_chatsync.config import (
    AppConfig,
    find_antigravity_executable,
    get_config_file_path,
    get_default_antigravity_data_dir,
    get_default_device_id,
    load_config,
    save_config,
)
from gc_chatsync.runner import SyncRunner


def setup_logging(verbose: bool = False) -> None:
    """Configure root logger level and format."""
    level = logging.DEBUG if verbose else logging.INFO
    format_str = "%(asctime)s [%(levelname)s] %(name)s: %(message)s" if verbose else "[%(levelname)s] %(message)s"
    logging.basicConfig(level=level, format=format_str, datefmt="%H:%M:%S")


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

    conn_icon = "✅ Connected" if status.drive_connected else "❌ Disconnected (check rclone config)"
    print(f"Google Drive:      {conn_icon}")

    if status.drive_connected:
        print("-" * 55)
        print(f"Cloud Last Device: {status.cloud_last_device or '(no sync yet)'}")
        print(f"Cloud Last Sync:   {status.cloud_last_sync_utc or 'N/A'}")
        sync_icon = "🟢 Up to date" if status.in_sync else "🟡 Updates available in cloud"
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
    return runner.run(extra_args=args.app_args)


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
    p_run.add_argument("app_args", nargs="*", help="Arguments forwarded directly to Antigravity.")

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

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """Entry point for the gc-chatsync command line interface."""
    parser = build_parser()
    args = parser.parse_args(argv)

    setup_logging(verbose=args.verbose)

    if not args.command:
        parser.print_help()
        return 0

    commands = {
        "run": cmd_run,
        "pull": cmd_pull,
        "push": cmd_push,
        "status": cmd_status,
        "setup": cmd_setup,
    }

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
