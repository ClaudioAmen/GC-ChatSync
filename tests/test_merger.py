"""Unit tests for SQLite database merge engine."""

import gc
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from gc_chatsync.merger import (
    DatabaseMergeError,
    checkpoint_db,
    create_backup,
    is_db_exclusive_accessible,
    merge_summaries_databases,
    restore_latest_backup,
    snapshot_db,
)


class TestDatabaseMerger(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)
        self.local_db = self.dir_path / "local.db"
        self.remote_db = self.dir_path / "remote.db"

    def tearDown(self):
        gc.collect()
        try:
            self.temp_dir.cleanup()
        except PermissionError:
            time.sleep(0.1)
            gc.collect()
            try:
                self.temp_dir.cleanup()
            except Exception:
                pass

    def _create_sample_db(self, path: Path):
        """Create sample conversation_summaries DB schema."""
        conn = sqlite3.connect(path)
        try:
            conn.execute("""
                CREATE TABLE conversation_summaries (
                    conversation_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL DEFAULT '',
                    preview TEXT NOT NULL DEFAULT '',
                    step_count INTEGER NOT NULL DEFAULT 0,
                    last_modified_time DATETIME NOT NULL,
                    workspace_uris TEXT NOT NULL DEFAULT '',
                    project_id TEXT NOT NULL DEFAULT ''
                );
            """)
            conn.execute("""
                CREATE VIRTUAL TABLE conversations_fts USING fts5(
                    conversation_id UNINDEXED,
                    title,
                    workspace
                );
            """)
            conn.commit()
        finally:
            conn.close()

    def test_merge_inserts_new_conversations(self):
        self._create_sample_db(self.local_db)
        self._create_sample_db(self.remote_db)

        # Local has conversation A
        conn_l = sqlite3.connect(self.local_db)
        try:
            conn_l.execute("""
                INSERT INTO conversation_summaries VALUES
                ('id-A', 'Local Chat A', 'Hello', 5, '2026-10-28 10:00:00', 'file:///home/developer', 'proj-1');
            """)
            conn_l.commit()
        finally:
            conn_l.close()

        # Remote has conversation B
        conn_r = sqlite3.connect(self.remote_db)
        try:
            conn_r.execute("""
                INSERT INTO conversation_summaries VALUES
                ('id-B', 'Remote Chat B', 'World', 10, '2026-10-28 11:00:00', 'file:///C:/Users', 'proj-2');
            """)
            conn_r.commit()
        finally:
            conn_r.close()

        result = merge_summaries_databases(self.local_db, self.remote_db)
        self.assertEqual(result.inserted, 1)

        # Verify local now has both A and B
        conn_check = sqlite3.connect(self.local_db)
        try:
            rows = conn_check.execute("SELECT conversation_id, title FROM conversation_summaries ORDER BY conversation_id;").fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0][0], "id-A")
            self.assertEqual(rows[1][0], "id-B")
        finally:
            conn_check.close()

    def test_merge_updates_newer_record(self):
        self._create_sample_db(self.local_db)
        self._create_sample_db(self.remote_db)

        # Local has chat A with step_count 5
        conn_l = sqlite3.connect(self.local_db)
        try:
            conn_l.execute("""
                INSERT INTO conversation_summaries VALUES
                ('id-A', 'Chat A Old', 'Old Preview', 5, '2026-10-28 10:00:00', '', '');
            """)
            conn_l.commit()
        finally:
            conn_l.close()

        # Remote has chat A with step_count 12 and newer timestamp
        conn_r = sqlite3.connect(self.remote_db)
        try:
            conn_r.execute("""
                INSERT INTO conversation_summaries VALUES
                ('id-A', 'Chat A Updated', 'New Preview', 12, '2026-10-28 12:00:00', '', '');
            """)
            conn_r.commit()
        finally:
            conn_r.close()

        result = merge_summaries_databases(self.local_db, self.remote_db)
        self.assertEqual(result.updated, 1)

        conn_check = sqlite3.connect(self.local_db)
        try:
            row = conn_check.execute("SELECT title, step_count FROM conversation_summaries WHERE conversation_id='id-A'").fetchone()
            self.assertEqual(row[0], "Chat A Updated")
            self.assertEqual(row[1], 12)
        finally:
            conn_check.close()

    def test_merge_ignores_older_remote_record(self):
        self._create_sample_db(self.local_db)
        self._create_sample_db(self.remote_db)

        # Local has newer chat A
        conn_l = sqlite3.connect(self.local_db)
        try:
            conn_l.execute("""
                INSERT INTO conversation_summaries VALUES
                ('id-A', 'Local Newer', 'Local', 20, '2026-10-28 15:00:00', '', '');
            """)
            conn_l.commit()
        finally:
            conn_l.close()

        # Remote has older chat A
        conn_r = sqlite3.connect(self.remote_db)
        try:
            conn_r.execute("""
                INSERT INTO conversation_summaries VALUES
                ('id-A', 'Remote Stale', 'Remote', 5, '2026-10-28 09:00:00', '', '');
            """)
            conn_r.commit()
        finally:
            conn_r.close()

        result = merge_summaries_databases(self.local_db, self.remote_db)
        self.assertEqual(result.updated, 0)
        self.assertEqual(result.inserted, 0)

        conn_check = sqlite3.connect(self.local_db)
        try:
            row = conn_check.execute("SELECT title FROM conversation_summaries WHERE conversation_id='id-A'").fetchone()
            self.assertEqual(row[0], "Local Newer")
        finally:
            conn_check.close()

    def test_backup_creation_and_pruning(self):
        self._create_sample_db(self.local_db)
        for i in range(7):
            create_backup(self.local_db, max_backups=3)

        backup_dir = self.local_db.parent / ".backups"
        backups = list(backup_dir.glob("local.db.bak_*"))
        # Should prune down to max_backups=3
        self.assertEqual(len(backups), 3)

    def test_merge_with_schema_mismatch(self):
        """Schema mismatch test: remote has a new column introduced in a newer Antigravity update.
        
        Merge should abort with DatabaseMergeError to prevent silently dropping the new column on push.
        """
        self._create_sample_db(self.local_db)
        self._create_sample_db(self.remote_db)

        # Add a new hypothetical column to remote
        conn = sqlite3.connect(self.remote_db)
        try:
            conn.execute("ALTER TABLE conversation_summaries ADD COLUMN new_feature_flag TEXT DEFAULT 'enabled';")
            conn.execute("""
                INSERT INTO conversation_summaries (conversation_id, title, step_count, last_modified_time, new_feature_flag)
                VALUES ('id-C', 'Chat from updated Antigravity', 3, '2026-10-28 16:00:00', 'enabled');
            """)
            conn.commit()
        finally:
            conn.close()

        # Merge should raise DatabaseMergeError to safeguard schema integrity
        with self.assertRaises(DatabaseMergeError):
            merge_summaries_databases(self.local_db, self.remote_db)

    def test_create_backup_deduplication(self):
        self._create_sample_db(self.local_db)
        b1 = create_backup(self.local_db)
        self.assertIsNotNone(b1)

        # Immediate second backup with zero DB changes should return identical backup path
        b2 = create_backup(self.local_db, force=False)
        self.assertEqual(b1, b2)

        # Only 1 backup file should exist
        bak_dir = self.local_db.parent / ".backups"
        self.assertEqual(len(list(bak_dir.glob("local.db.bak_*"))), 1)

    def test_create_backup_retention_and_safety_floor(self):
        self._create_sample_db(self.local_db)
        bak_dir = self.local_db.parent / ".backups"
        bak_dir.mkdir(parents=True, exist_ok=True)

        # Create 5 dummy backup files with old timestamps (10 days old)
        now = time.time()
        old_mtime = now - (10 * 86400)
        for i in range(5):
            p = bak_dir / f"local.db.bak_2026100{i}_000000_000000"
            p.write_text("old content")
            import os
            os.utime(p, (old_mtime, old_mtime))

        # Run create_backup with max_age_days=7 and min_keep=3 (force=True to bypass dedup)
        new_bak = create_backup(self.local_db, max_age_days=7, min_keep=3, force=True)
        self.assertIsNotNone(new_bak)

        # Backups remaining should be exactly min_keep=3
        remaining = list(bak_dir.glob("local.db.bak_*"))
        self.assertEqual(len(remaining), 3)

    def test_restore_latest_backup(self):
        self._create_sample_db(self.local_db)
        # Add a record to local_db
        conn = sqlite3.connect(self.local_db)
        try:
            conn.execute(
                "INSERT INTO conversation_summaries (conversation_id, title, last_modified_time) VALUES ('c1', 'Title 1', '2026-10-28');"
            )
            conn.commit()
        finally:
            conn.close()

        # Create backup containing 'c1'
        b1 = create_backup(self.local_db, force=True)
        self.assertIsNotNone(b1)

        # Now corrupt/alter local_db by deleting record
        conn2 = sqlite3.connect(self.local_db)
        try:
            conn2.execute("DELETE FROM conversation_summaries WHERE conversation_id='c1';")
            conn2.commit()
            self.assertEqual(conn2.execute("SELECT count(*) FROM conversation_summaries;").fetchone()[0], 0)
        finally:
            conn2.close()

        # Restore from latest backup
        success, msg, restored_bak = restore_latest_backup(self.local_db)
        self.assertTrue(success)
        self.assertEqual(restored_bak, b1)

        # Verify that 'c1' is restored
        conn3 = sqlite3.connect(self.local_db)
        try:
            self.assertEqual(conn3.execute("SELECT count(*) FROM conversation_summaries;").fetchone()[0], 1)
        finally:
            conn3.close()

    def test_is_db_exclusive_accessible(self):
        self._create_sample_db(self.local_db)
        self.assertTrue(is_db_exclusive_accessible(self.local_db))

        # While a lock is held, it should return False
        conn = sqlite3.connect(self.local_db)
        try:
            conn.execute("BEGIN EXCLUSIVE;")
            self.assertFalse(is_db_exclusive_accessible(self.local_db, timeout=0.1))
            conn.commit()
        finally:
            conn.close()

        self.assertTrue(is_db_exclusive_accessible(self.local_db))

    def test_schema_validation_fails_on_missing_required_column(self):
        # Create DB missing last_modified_time
        conn = sqlite3.connect(self.local_db)
        try:
            conn.execute("CREATE TABLE conversation_summaries (conversation_id TEXT PRIMARY KEY);")
            conn.commit()
        finally:
            conn.close()

        conn2 = sqlite3.connect(self.remote_db)
        try:
            conn2.execute("CREATE TABLE conversation_summaries (conversation_id TEXT PRIMARY KEY, last_modified_time DATETIME);")
            conn2.commit()
        finally:
            conn2.close()

        with self.assertRaises(DatabaseMergeError):
            merge_summaries_databases(self.local_db, self.remote_db)

    def test_snapshot_db_preserves_mtime(self):
        import os
        self._create_sample_db(self.local_db)
        past_timestamp = 1600000000.0
        os.utime(self.local_db, (past_timestamp, past_timestamp))

        target_snap = self.dir_path / "snap.db"
        success = snapshot_db(self.local_db, target_snap)
        self.assertTrue(success)
        self.assertTrue(target_snap.is_file())
        self.assertAlmostEqual(target_snap.stat().st_mtime, past_timestamp, places=1)

    def test_needs_db_snapshot_evaluation(self):
        import os
        from gc_chatsync.merger import needs_db_snapshot

        self._create_sample_db(self.local_db)
        old_time = 1700000000.0
        os.utime(self.local_db, (old_time, old_time))

        last_push = 1700001000.0
        # Case 1: Untouched file older than last push -> False
        self.assertFalse(needs_db_snapshot(self.local_db, last_push_ts=last_push))

        # Case 2: Untouched file, but first sync (last_push = 0) -> True
        self.assertTrue(needs_db_snapshot(self.local_db, last_push_ts=0.0))

        # Case 3: File modified recently -> True
        recent_time = 1700002000.0
        os.utime(self.local_db, (recent_time, recent_time))
        self.assertTrue(needs_db_snapshot(self.local_db, last_push_ts=last_push))

        # Case 4: File old, but active WAL journal exists -> True
        os.utime(self.local_db, (old_time, old_time))
        wal_file = self.local_db.with_name(self.local_db.name + "-wal")
        wal_file.write_bytes(b"dummy-wal-content")
        self.assertTrue(needs_db_snapshot(self.local_db, last_push_ts=last_push))
        wal_file.unlink()


if __name__ == "__main__":
    unittest.main()
