"""SQLite merge engine for Antigravity conversation indexes.

Performs safe two-way incremental merges of conversation_summaries.db
with automatic pre-merge backups and dynamic column introspection to absorb
future Antigravity schema updates.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

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


def checkpoint_db(db_path: Path) -> None:
    """Flush WAL journal files into the main SQLite database."""
    if not db_path.is_file():
        return
    try:
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
        finally:
            conn.close()
    except sqlite3.Error as e:
        logger.warning(f"Could not checkpoint WAL for {db_path}: {e}")


def create_backup(db_path: Path, max_backups: int = 5) -> Optional[Path]:
    """Create a timestamped backup of the database and prune old backups.

    Keeps the last `max_backups` copies in a `.backups` directory next to the database.
    """
    if not db_path.is_file():
        return None

    # Checkpoint before backup to ensure WAL data is committed into .db
    checkpoint_db(db_path)

    backup_dir = db_path.parent / ".backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    backup_file = backup_dir / f"{db_path.name}.bak_{timestamp}"

    shutil.copy2(db_path, backup_file)
    logger.info(f"Database backup created: {backup_file}")

    # Prune old backups
    existing_backups = sorted(
        backup_dir.glob(f"{db_path.name}.bak_*"),
        key=lambda p: p.stat().st_mtime
    )
    while len(existing_backups) > max_backups:
        oldest = existing_backups.pop(0)
        try:
            oldest.unlink()
            logger.debug(f"Pruned old backup: {oldest}")
        except OSError as e:
            logger.warning(f"Failed to prune old backup {oldest}: {e}")

    return backup_file


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

        # 3. Dynamic column discovery (schema buffer for future Antigravity versions)
        local_cols = set(get_table_columns(conn, "main", "conversation_summaries"))
        remote_cols = set(get_table_columns(conn, "remote_db", "conversation_summaries"))

        if "conversation_id" not in local_cols or "conversation_id" not in remote_cols:
            raise DatabaseMergeError("Both databases must contain 'conversation_id' in conversation_summaries.")

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
