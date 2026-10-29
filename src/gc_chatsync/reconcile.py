"""Automated Health Check and Reconciliation Engine (Doctor Protocol).

Diagnoses and repairs synchronization discrepancies:
- Auto-indexes orphaned conversation databases.
- Isolates corrupted database files into quarantine.
- Releases stale cloud witness locks (> 15 minutes inactive).
- Audits cloud vs local deltas and pulls missing files.
- Creates pre-flight atomic backups and provides full transaction rollback.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from gc_chatsync.config import AppConfig, get_antigravity_version
from gc_chatsync.drive import DriveClient, NOT_FOUND_CODES, SyncManifest, ManifestCorruptedError
from gc_chatsync.merger import checkpoint_db, create_backup, get_table_columns
from gc_chatsync.runner import SyncRunner, is_antigravity_process_running

logger = logging.getLogger("gc_chatsync.reconcile")


@dataclass
class ReconcileReport:
    """Detailed summary of health checks and repair actions."""
    status: str = "SUCCESS"  # "SUCCESS", "WARNING", "CRITICAL_ABORT"
    code: int = 0  # 0 = OK, 1 = Warning/Degraded, 2 = Abort
    orphans_found: int = 0
    orphans_indexed: int = 0
    corrupt_files: List[str] = field(default_factory=list)
    stale_witness_cleared: bool = False
    manifest_repaired: bool = False
    remote_audited: bool = False
    remote_synced: int = 0
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    details: List[str] = field(default_factory=list)


def extract_session_preview(db_path: Path) -> Tuple[str, str, str]:
    """Inspect internal SQLite tables of a session database to extract title/preview, workspace, and timestamp.

    Returns: (title, preview, workspace_uris)
    """
    title = ""
    preview = ""
    workspace = "[]"
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()

        # Check trajectory_metadata_blob
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='trajectory_metadata_blob';")
        if cur.fetchone():
            cur.execute("SELECT data FROM trajectory_metadata_blob WHERE id='main';")
            row = cur.fetchone()
            if row and row[0]:
                raw = row[0]
                text_matches = re.findall(rb'[\x20-\x7e\xc0-\xff]{5,}', raw)
                for m in text_matches:
                    decoded = m.decode("utf-8", errors="ignore").strip()
                    if decoded.startswith("file:///"):
                        workspace = f'["{decoded}"]'
                    elif not preview and len(decoded) > 4 and not decoded.startswith("$"):
                        preview = decoded

        # Check steps table for user input or title if preview still empty
        if not preview:
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='steps';")
            if cur.fetchone():
                cur.execute("SELECT step_payload FROM steps ORDER BY idx ASC LIMIT 10;")
                for (p_bytes,) in cur.fetchall():
                    if not p_bytes:
                        continue
                    if isinstance(p_bytes, bytes):
                        p_str = p_bytes.decode("utf-8", errors="ignore")
                    else:
                        p_str = str(p_bytes)
                    clean_lines = [l.strip() for l in p_str.splitlines() if len(l.strip()) > 5 and not l.strip().startswith(("$", "{", "call_"))]
                    if clean_lines:
                        preview = clean_lines[0][:80]
                        break
    except Exception as e:
        logger.debug(f"Could not extract preview from {db_path.name}: {e}")
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

    if not preview:
        preview = f"Recovered Conversation ({db_path.stem[:8]})"
    return title, preview, workspace


def run_reconcile(
    runner: SyncRunner,
    progress_cb: Optional[Callable[[int, str], None]] = None,
    repair_manifest: bool = False,
) -> ReconcileReport:
    """Execute the full Doctor reconciliation workflow."""
    def report(pct: int, msg: str):
        if progress_cb:
            progress_cb(pct, msg)
        logger.info(f"[Reconcile {pct}%] {msg}")

    rep = ReconcileReport()
    report(5, "Checking system state and process locks...")

    # 1. Pre-flight check: Antigravity running?
    if is_antigravity_process_running():
        rep.status = "CRITICAL_ABORT"
        rep.code = 2
        msg = "Antigravity is currently running. Please close Antigravity before reconciling."
        rep.errors.append(msg)
        report(0, msg)
        _log_reconcile_report(rep)
        return rep

    data_dir = runner.data_dir
    conv_dir = data_dir / "conversations"
    local_db = data_dir / "conversation_summaries.db"

    # 2. Safety Backup
    if local_db.is_file():
        report(15, "Creating safety backup of conversation index...")
        bak = create_backup(local_db, force=True)
        if bak:
            rep.details.append(f"Created pre-reconcile backup: {bak.name}")

    # 3. Scan for Orphaned Sessions
    report(30, "Scanning local conversation databases...")
    if conv_dir.is_dir() and local_db.is_file():
        checkpoint_db(local_db)
        try:
            conn = sqlite3.connect(local_db)
            cur = conn.cursor()

            # Dynamic column discovery
            cols = get_table_columns(conn, "main", "conversation_summaries")
            cur.execute("SELECT conversation_id FROM conversation_summaries;")
            indexed_ids = {row[0] for row in cur.fetchall()}

            local_db_files = list(conv_dir.glob("*.db"))
            orphan_files = [p for p in local_db_files if p.stem not in indexed_ids]
            rep.orphans_found = len(orphan_files)

            if orphan_files:
                report(45, f"Found {len(orphan_files)} unindexed session databases. Reconciling...")
                quarantine_dir = data_dir / ".backups" / "corrupted"

                for orphan in orphan_files:
                    cid = orphan.stem
                    is_intact = False
                    chk_conn = None
                    try:
                        chk_conn = sqlite3.connect(orphan)
                        res = chk_conn.execute("PRAGMA integrity_check;").fetchone()
                        is_intact = (res and res[0] == "ok")
                    except Exception:
                        is_intact = False
                    finally:
                        if chk_conn:
                            try:
                                chk_conn.close()
                            except Exception:
                                pass

                    if not is_intact:
                        quarantine_dir.mkdir(parents=True, exist_ok=True)
                        dest_q = quarantine_dir / orphan.name
                        try:
                            shutil.copy2(orphan, dest_q)
                        except Exception:
                            pass
                        rep.corrupt_files.append(orphan.name)
                        rep.warnings.append(f"Corrupted database isolated: {orphan.name}")

                        title = f"[Corrupted / Quarantined] {cid[:8]}"
                        preview = "Session database failed SQLite integrity check"
                        ts = "1970-01-01T00:00:00+00:00"
                    else:
                        title, preview, workspace = extract_session_preview(orphan)
                        # Reconstructed orphan records use Epoch 0 so that during cloud merges,
                        # any authentic metadata record from another machine always takes precedence.
                        ts = "1970-01-01T00:00:00+00:00"

                    row_data: Dict[str, Any] = {
                        "conversation_id": cid,
                        "title": title,
                        "preview": preview,
                        "last_modified_time": ts,
                        "status": "CASCADE_RUN_STATUS_IDLE",
                        "app_data_dir": "antigravity",
                    }
                    if "workspace_uris" in cols:
                        row_data["workspace_uris"] = workspace if is_intact else "[]"
                    if "step_count" in cols:
                        row_data["step_count"] = 1

                    valid_keys = [k for k in row_data.keys() if k in cols]
                    placeholders = ", ".join(["?"] * len(valid_keys))
                    col_str = ", ".join(valid_keys)
                    vals = [row_data[k] for k in valid_keys]

                    cur.execute(f"INSERT OR REPLACE INTO conversation_summaries ({col_str}) VALUES ({placeholders});", vals)
                    rep.orphans_indexed += 1
                    rep.details.append(f"Auto-indexed orphan session: {cid} ('{preview[:30]}')")

                conn.commit()
            conn.close()
        except Exception as e:
            rep.warnings.append(f"Local indexing encountered error: {e}")
            logger.warning(f"Error during orphan reconciliation: {e}")

    # 4. Check Cloud Manifest Integrity & Release Stale Cloud Witness Lock (> 15 minutes)
    report(65, "Checking Google Drive witness locks and manifest integrity...")
    try:
        if runner.drive.test_connection():
            manifest = None
            is_corrupted = False
            try:
                manifest = runner.drive.get_manifest(force_refresh=True)
            except ManifestCorruptedError as e:
                is_corrupted = True
                rep.warnings.append(f"Remote manifest is corrupted: {e}")

            if (is_corrupted or repair_manifest) and (manifest is None or is_corrupted):
                report(68, "Attempting manifest repair/reconstruction...")
                bak = runner.drive.get_manifest_backup()
                if bak:
                    runner.drive.put_manifest(bak)
                    manifest = bak
                    rep.manifest_repaired = True
                    rep.details.append(f"Restored corrupted manifest from remote backup (.bak) with epoch {bak.epoch}.")
                    report(70, f"Restored manifest from remote backup (epoch {bak.epoch}).")
                else:
                    reconstructed = SyncManifest.create_now(
                        device_id=runner.config.device_id,
                        epoch=1,
                        antigravity_version=get_antigravity_version(runner.config.antigravity_bin_path) if runner.config.antigravity_bin_path else None,
                    )
                    runner.drive.put_manifest(reconstructed)
                    manifest = reconstructed
                    rep.manifest_repaired = True
                    rep.details.append("Remote manifest missing or unrecoverable from .bak; initialized fresh manifest (epoch 1).")
                    report(70, "Reconstructed clean manifest (epoch 1).")

            if manifest and manifest.active_device:
                is_stale = False
                ref_time_str = manifest.active_heartbeat_utc or manifest.active_since_utc
                if ref_time_str:
                    try:
                        active_dt = datetime.fromisoformat(ref_time_str)
                        now_dt = datetime.now(timezone.utc)
                        delta_sec = (now_dt - active_dt).total_seconds()
                        if delta_sec > 900:  # 15 minutes of silence (no active heartbeat)
                            is_stale = True
                    except Exception:
                        is_stale = True
                else:
                    is_stale = True

                if is_stale:
                    device_stale = manifest.active_device
                    manifest.active_device = None
                    manifest.active_since_utc = None
                    manifest.active_heartbeat_utc = None
                    runner.drive.put_manifest(manifest)
                    rep.stale_witness_cleared = True
                    rep.details.append(f"Cleared stale witness lock held by '{device_stale}'.")
                    report(75, f"Released stale lock from '{device_stale}'.")

            # 5. Remote file audit
            report(80, "Auditing remote files on Google Drive...")
            rep.remote_audited = True
            try:
                remote_files_raw = runner.drive.run_cmd(["lsf", f"{runner.drive.remote_base}/conversations"], timeout=30, log_errors=False)
                remote_dbs = {line.strip() for line in remote_files_raw.splitlines() if line.strip().endswith(".db")}
                local_dbs = {p.name for p in conv_dir.glob("*.db")} if conv_dir.is_dir() else set()

                missing_locally = remote_dbs - local_dbs
                if missing_locally:
                    report(85, f"Downloading {len(missing_locally)} missing conversations from cloud...")
                    runner.drive.pull_conversations(conv_dir)
                    rep.remote_synced = len(missing_locally)
                    rep.details.append(f"Pulled {len(missing_locally)} missing conversation databases.")
            except Exception as e:
                logger.debug(f"Remote audit skipped: {e}")
        else:
            rep.warnings.append("Google Drive offline; cloud audit skipped.")
    except Exception as e:
        rep.warnings.append(f"Remote witness check error: {e}")

    # 6. Final Status & Summary
    report(100, "Reconciliation completed.")
    if rep.corrupt_files or rep.warnings:
        rep.status = "WARNING"
        rep.code = 1
    else:
        rep.status = "SUCCESS"
        rep.code = 0

    _log_reconcile_report(rep)
    return rep


def _log_reconcile_report(report: ReconcileReport) -> None:
    """Save formatted report to ~/.config/gc-chatsync/logs/reconcile.log."""
    try:
        from gc_chatsync.config import get_config_dir
        log_dir = get_config_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "reconcile.log"

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        lines = [
            f"=== Reconcile Report: {timestamp} (Status: {report.status}, Code: {report.code}) ===",
            f"Orphans Found: {report.orphans_found} | Orphans Indexed: {report.orphans_indexed}",
            f"Corrupt Files: {len(report.corrupt_files)} | Stale Witness Cleared: {report.stale_witness_cleared}",
            f"Manifest Repaired: {report.manifest_repaired} | Remote Audited: {report.remote_audited} | Remote Files Pulled: {report.remote_synced}",
        ]
        if report.errors:
            lines.append("Errors:")
            for err in report.errors:
                lines.append(f"  - {err}")
        if report.warnings:
            lines.append("Warnings:")
            for warn in report.warnings:
                lines.append(f"  - {warn}")
        if report.details:
            lines.append("Details:")
            for d in report.details:
                lines.append(f"  * {d}")
        lines.append("\n")

        with open(log_file, "a", encoding="utf-8") as f:
            f.write("\n".join(lines))
    except Exception as e:
        logger.debug(f"Could not write reconcile log: {e}")
