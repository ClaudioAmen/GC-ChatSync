"""SQLite merge engine for Antigravity conversation indexes.

Performs safe two-way incremental merges of conversation_summaries.db
with automatic pre-merge backups and dynamic column introspection to absorb
future Antigravity schema updates.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger("gc_chatsync.merger")


@dataclass
class MergeResult:
    """Summary of operations performed during a database merge."""
    inserted: int
    updated: int
    unchanged: int
    backup_path: Optional[Path] = None


class DatabaseMergeError(Exception):
    """Raised when database merge fails."""
    pass


def checkpoint_db(db_path: Path, max_retries: int = 3, retry_delay: float = 0.3) -> bool:
    """Flush WAL journal files into the main SQLite database with TRUNCATE.
    
    Retries if the database returns busy status (e.g. read transaction in flight).
    Returns True if checkpoint succeeded fully, False if busy or errored.
    """
    if not db_path.is_file():
        return False
    for attempt in range(max_retries):
        try:
            conn = sqlite3.connect(db_path, timeout=5.0)
            try:
                res = conn.execute("PRAGMA wal_checkpoint(TRUNCATE);").fetchone()
                if res and res[0] != 0:
                    logger.warning(
                        f"WAL checkpoint for {db_path.name} was busy (attempt {attempt + 1}/{max_retries}, status={res})"
                    )
                    time.sleep(retry_delay)
                    continue
                return True
            finally:
                conn.close()
        except sqlite3.Error as e:
            logger.warning(f"Could not checkpoint WAL for {db_path}: {e}")
            time.sleep(retry_delay)
    return False


def is_db_exclusive_accessible(db_path: Path, timeout: float = 2.0) -> bool:
    """Verify that no external process holds an active write lock on SQLite DB."""
    if not db_path.is_file():
        return True
    try:
        conn = sqlite3.connect(db_path, timeout=timeout)
        try:
            conn.execute("BEGIN EXCLUSIVE;")
            conn.execute("COMMIT;")
            return True
        finally:
            conn.close()
    except (sqlite3.OperationalError, sqlite3.DatabaseError) as e:
        logger.debug(f"Database {db_path.name} is currently locked by another process: {e}")
        return False


def needs_db_snapshot(db_path: Path, last_push_ts: float = 0.0) -> bool:
    """Determine if a SQLite conversation database has changed since last push.
    
    Returns True if:
    - WAL or SHM journal exists and is non-empty (active uncommitted or pending writes).
    - File modification timestamp is newer than last_push_ts (with 5-second safety margin).
    - last_push_ts <= 0.0 (first run or unknown state).
    """
    if not db_path.is_file():
        return False
    wal = db_path.with_name(db_path.name + "-wal")
    if wal.exists() and wal.stat().st_size > 0:
        return True
    shm = db_path.with_name(db_path.name + "-shm")
    if shm.exists() and shm.stat().st_size > 0:
        return True
    if last_push_ts <= 0.0:
        return True
    try:
        # 5-second margin for filesystem timestamp resolution and clock skew
        return db_path.stat().st_mtime > (last_push_ts - 5.0)
    except OSError:
        return True


def are_all_dbs_accessible(
    data_dir: Path, timeout: float = 0.5, last_push_ts: float = 0.0
) -> bool:
    """Verify that conversation databases and the summaries database are free of active locks.
    
    If last_push_ts > 0.0, only checks databases that were modified or have active WAL journals.
    """
    local_db = data_dir / "conversation_summaries.db"
    if not is_db_exclusive_accessible(local_db, timeout=timeout):
        return False

    conv_dir = data_dir / "conversations"
    if conv_dir.is_dir():
        for db_file in conv_dir.glob("*.db"):
            if needs_db_snapshot(db_file, last_push_ts):
                if not is_db_exclusive_accessible(db_file, timeout=timeout):
                    return False
    return True


def snapshot_db(source_path: Path, target_path: Path) -> bool:
    """Create a consistent, lock-free SQLite snapshot of source_path at target_path.
    
    Flushes WAL journals first and uses SQLite online backup API.
    """
    if not source_path.is_file():
        return False
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if not checkpoint_db(source_path):
        logger.warning(f"Could not checkpoint {source_path.name} before snapshot.")
        return False
    try:
        s_conn = sqlite3.connect(source_path)
        d_conn = sqlite3.connect(target_path)
        try:
            s_conn.backup(d_conn)
        finally:
            d_conn.close()
            s_conn.close()
        try:
            s_stat = source_path.stat()
            os.utime(target_path, (s_stat.st_atime, s_stat.st_mtime))
        except Exception as utime_err:
            logger.debug(f"Could not preserve mtime on snapshot {target_path}: {utime_err}")
        return True
    except Exception as e:
        logger.warning(f"SQLite online backup snapshot failed for {source_path}: {e}")
        try:
            shutil.copy2(source_path, target_path)
            return True
        except Exception:
            return False


def _compute_file_sha256(path: Path) -> str:
    """Calculate SHA256 checksum of a file for content deduplication."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def create_backup(
    db_path: Path,
    max_age_days: int = 7,
    min_keep: int = 3,
    force: bool = False,
    max_backups: Optional[int] = None,
) -> Optional[Path]:
    """Create a crash-safe timestamped backup of the database with retention and deduplication.

    - Content deduplication: If the database is byte-identical to the latest backup,
      creation is skipped to prevent duplicate snapshots from zero-change sessions.
    - Retention policy: Automatically prunes backups older than `max_age_days` (default 7),
      while strictly guaranteeing at least `min_keep` (default 3) snapshots are preserved.
    """
    if not db_path.is_file():
        return None

    if max_backups is not None:
        min_keep = max_backups
        force = True

    # Checkpoint before backup to ensure WAL data is committed into .db
    checkpoint_db(db_path)

    backup_dir = db_path.parent / ".backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    existing_backups = sorted(
        backup_dir.glob(f"{db_path.name}.bak_*"),
        key=lambda p: (p.stat().st_mtime, p.name),
    )

    current_hash = _compute_file_sha256(db_path)

    # 1. Deduplication check: compare hash with latest existing backup
    if not force and existing_backups and current_hash:
        latest = existing_backups[-1]
        # Check if hash is embedded in filename or content matches
        if latest.name.endswith(f"_{current_hash[:16]}") or _compute_file_sha256(latest) == current_hash:
            logger.debug(f"Database {db_path.name} identical to latest backup ({latest.name}); skipping duplicate.")
            return latest

    # 2. Create new snapshot using SQLite online backup API
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    hash_suffix = f"_{current_hash[:16]}" if current_hash else ""
    backup_file = backup_dir / f"{db_path.name}.bak_{timestamp}{hash_suffix}"

    try:
        source_conn = sqlite3.connect(db_path)
        dest_conn = sqlite3.connect(backup_file)
        try:
            source_conn.backup(dest_conn)
        finally:
            dest_conn.close()
            source_conn.close()
        logger.info(f"Database backup created: {backup_file}")
    except Exception as e:
        logger.warning(f"SQLite online backup failed for {db_path}, falling back to copy: {e}")
        shutil.copy2(db_path, backup_file)

    # 3. Prune backups older than max_age_days, preserving at least min_keep
    all_backups = sorted(
        backup_dir.glob(f"{db_path.name}.bak_*"),
        key=lambda p: (p.stat().st_mtime, p.name),
    )
    if len(all_backups) > min_keep:
        cutoff = time.time() - (max_age_days * 86400)
        candidates_to_prune = all_backups[:-min_keep]
        for candidate in candidates_to_prune:
            try:
                # If max_backups was explicitly specified, prune by count; otherwise prune by age cutoff
                if max_backups is not None or candidate.stat().st_mtime < cutoff:
                    candidate.unlink()
                    logger.debug(f"Pruned old backup: {candidate.name}")
            except OSError as e:
                logger.warning(f"Failed to prune old backup {candidate}: {e}")

    return backup_file


def restore_latest_backup(db_path: Path) -> Tuple[bool, str, Optional[Path]]:
    """Restore database from the most recent backup file in `.backups/`.

    Safeguards:
    1. Creates a safety `.pre_restore_bak` of current DB before replacing.
    2. Deletes stale `-wal` and `-shm` sidecar journals to prevent SQLite frame replays.
    """
    backup_dir = db_path.parent / ".backups"
    if not backup_dir.is_dir():
        return False, "No backup directory found.", None

    existing_backups = sorted(
        backup_dir.glob(f"{db_path.name}.bak_*"),
        key=lambda p: p.stat().st_mtime,
    )
    if not existing_backups:
        return False, "No backup snapshots available to restore.", None

    latest_backup = existing_backups[-1]

    # Pre-restore safety snapshot of the current state
    if db_path.is_file():
        safety_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safety_bak = backup_dir / f"{db_path.name}.pre_restore_{safety_ts}"
        try:
            shutil.copy2(db_path, safety_bak)
            logger.info(f"Safety snapshot saved before restore: {safety_bak.name}")
        except Exception as e:
            logger.warning(f"Could not create safety snapshot before restore: {e}")

    try:
        shutil.copy2(latest_backup, db_path)

        # Clear stale WAL/SHM sidecars
        for ext in ("-wal", "-shm"):
            sidecar = db_path.parent / (db_path.name + ext)
            if sidecar.exists():
                try:
                    sidecar.unlink()
                except OSError:
                    pass

        logger.info(f"Restored database {db_path.name} from backup {latest_backup.name}")
        return True, f"Successfully restored {db_path.name} from backup ({latest_backup.name})", latest_backup
    except Exception as e:
        logger.error(f"Failed to restore backup {latest_backup} to {db_path}: {e}")
        return False, f"Failed to restore backup: {e}", None


def get_table_columns(conn: sqlite3.Connection, db_prefix: str, table_name: str) -> List[str]:
    """Fetch column names dynamically using PRAGMA table_info."""
    qualified = f"{db_prefix}.{table_name}" if db_prefix else table_name
    cursor = conn.cursor()
    cursor.execute(f"PRAGMA {qualified}_info({table_name});")
    rows = cursor.fetchall()
    if not rows:
        # Fallback query
        cursor.execute(f"PRAGMA {db_prefix}.table_info({table_name});" if db_prefix else f"PRAGMA table_info({table_name});")
        rows = cursor.fetchall()
    return [str(row[1]) for row in rows]


def merge_summaries_databases(local_db_path: Path, remote_db_path: Path) -> MergeResult:
    """Merge remote_db_path into local_db_path without losing sessions.

    Strategy:
    1. Backup local database.
    2. ATTACH remote_db to local connection.
    3. Dynamically discover common columns between both schemas.
    4. INSERT missing conversations (new on remote).
    5. UPDATE existing conversations where remote last_modified_time > local last_modified_time.
    6. Synchronize FTS search index if present.
    """
    if not remote_db_path.is_file():
        raise DatabaseMergeError(f"Remote staging database not found: {remote_db_path}")

    # If local database doesn't exist yet, simply copy the remote one
    if not local_db_path.is_file():
        local_db_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(remote_db_path, local_db_path)
        logger.info(f"Local database initialized from remote: {local_db_path}")
        return MergeResult(inserted=1, updated=0, unchanged=0)

    # 1. Create safety backup
    backup_path = create_backup(local_db_path)

    # 2. Open connection to local DB
    checkpoint_db(local_db_path)
    checkpoint_db(remote_db_path)

    conn = sqlite3.connect(local_db_path)
    try:
        cur = conn.cursor()

        # Attach remote staging database
        cur.execute("ATTACH DATABASE ? AS remote_db;", (str(remote_db_path.resolve()),))

        # 3. Dynamic column discovery with strict base schema verification
        local_cols = set(get_table_columns(conn, "main", "conversation_summaries"))
        remote_cols = set(get_table_columns(conn, "remote_db", "conversation_summaries"))

        required_cols = {"conversation_id", "last_modified_time"}
        missing_local = required_cols - local_cols
        missing_remote = required_cols - remote_cols
        if missing_local:
            raise DatabaseMergeError(f"Local database schema missing required columns: {missing_local}")
        if missing_remote:
            raise DatabaseMergeError(f"Remote database schema missing required columns: {missing_remote}")

        if local_cols != remote_cols:
            diff = local_cols ^ remote_cols
            raise DatabaseMergeError(
                f"Schema mismatch detected in conversation_summaries between local and remote ({diff}). "
                "Aborting merge to prevent dropping columns across different Antigravity versions. "
                "Please update Antigravity on both machines to match."
            )

        # Common columns to merge
        common_cols = [c for c in get_table_columns(conn, "main", "conversation_summaries") if c in remote_cols]
        col_list_str = ", ".join(f"`{c}`" for c in common_cols)
        remote_col_list_str = ", ".join(f"r.`{c}`" for c in common_cols)

        # Count total remote items
        cur.execute("SELECT COUNT(*) FROM remote_db.conversation_summaries;")
        total_remote = cur.fetchone()[0]

        # 4. Insert new records (exist on remote, missing in local)
        insert_sql = f"""
            INSERT INTO main.conversation_summaries ({col_list_str})
            SELECT {remote_col_list_str}
            FROM remote_db.conversation_summaries r
            WHERE r.conversation_id NOT IN (
                SELECT l.conversation_id FROM main.conversation_summaries l
            );
        """
        cur.execute(insert_sql)
        inserted_count = cur.rowcount

        # 5. Update records that exist in both, where remote last_modified_time is newer
        updated_count = 0
        if "last_modified_time" in common_cols:
            update_set_clause = ", ".join(
                f"`{c}` = r.`{c}`" for c in common_cols if c != "conversation_id"
            )
            update_sql = f"""
                UPDATE main.conversation_summaries
                SET {update_set_clause}
                FROM remote_db.conversation_summaries r
                WHERE main.conversation_summaries.conversation_id = r.conversation_id
                  AND r.last_modified_time > main.conversation_summaries.last_modified_time;
            """
            cur.execute(update_sql)
            updated_count = cur.rowcount

        unchanged_count = total_remote - (inserted_count + updated_count)

        # 6. Synchronize FTS full-text search index if exists
        cur.execute("SELECT name FROM main.sqlite_master WHERE type='table' AND name='conversations_fts';")
        has_local_fts = cur.fetchone() is not None

        cur.execute("SELECT name FROM remote_db.sqlite_master WHERE type='table' AND name='conversations_fts';")
        has_remote_fts = cur.fetchone() is not None

        if has_local_fts and has_remote_fts:
            fts_cols = [c for c in get_table_columns(conn, "main", "conversations_fts")
                        if c in get_table_columns(conn, "remote_db", "conversations_fts")]
            if fts_cols and "conversation_id" in fts_cols:
                fts_col_str = ", ".join(f"`{c}`" for c in fts_cols)
                r_fts_col_str = ", ".join(f"r.`{c}`" for c in fts_cols)

                # Delete matching FTS entries that will be replaced
                cur.execute("""
                    DELETE FROM main.conversations_fts
                    WHERE conversation_id IN (
                        SELECT r.conversation_id FROM remote_db.conversations_fts r
                        INNER JOIN remote_db.conversation_summaries s ON r.conversation_id = s.conversation_id
                        INNER JOIN main.conversation_summaries m ON s.conversation_id = m.conversation_id
                        WHERE s.last_modified_time >= m.last_modified_time
                    );
                """)

                # Re-insert updated/new FTS entries from remote
                cur.execute(f"""
                    INSERT INTO main.conversations_fts ({fts_col_str})
                    SELECT {r_fts_col_str}
                    FROM remote_db.conversations_fts r
                    WHERE r.conversation_id NOT IN (
                        SELECT l.conversation_id FROM main.conversations_fts l
                    );
                """)

        conn.commit()
        logger.info(f"Merge completed: {inserted_count} inserted, {updated_count} updated, {unchanged_count} unchanged.")

        cur.execute("DETACH DATABASE remote_db;")
        return MergeResult(
            inserted=inserted_count,
            updated=updated_count,
            unchanged=max(0, unchanged_count),
            backup_path=backup_path,
        )

    except Exception as e:
        conn.rollback()
        logger.error(f"Merge failed: {e}. Attempting rollback.")
        # If backup was created and an error occurred, the user's data remains untouched
        raise DatabaseMergeError(f"Failed to merge databases: {e}") from e
    finally:
        conn.close()
        checkpoint_db(local_db_path)
