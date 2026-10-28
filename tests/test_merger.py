"""Unit tests for SQLite database merge engine."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from gc_chatsync.merger import (
    DatabaseMergeError,
    checkpoint_db,
    create_backup,
    is_db_exclusive_accessible,
    merge_summaries_databases,
)


class TestDatabaseMerger(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)
        self.local_db = self.dir_path / "local.db"
        self.remote_db = self.dir_path / "remote.db"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_sample_db(self, path: Path):
        """Create sample conversation_summaries DB schema."""
        with sqlite3.connect(path) as conn:
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

    def test_merge_inserts_new_conversations(self):
        self._create_sample_db(self.local_db)
        self._create_sample_db(self.remote_db)

        # Local has conversation A
        with sqlite3.connect(self.local_db) as conn:
            conn.execute("""
                INSERT INTO conversation_summaries VALUES
                ('id-A', 'Local Chat A', 'Hello', 5, '2026-10-28 10:00:00', 'file:///home/claudio', 'proj-1');
            """)

        # Remote has conversation B
        with sqlite3.connect(self.remote_db) as conn:
            conn.execute("""
                INSERT INTO conversation_summaries VALUES
                ('id-B', 'Remote Chat B', 'World', 10, '2026-10-28 11:00:00', 'file:///C:/Users', 'proj-2');
            """)

        result = merge_summaries_databases(self.local_db, self.remote_db)
        self.assertEqual(result.inserted, 1)

        # Verify local now has both A and B
        with sqlite3.connect(self.local_db) as conn:
            rows = conn.execute("SELECT conversation_id, title FROM conversation_summaries ORDER BY conversation_id;").fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0][0], "id-A")
            self.assertEqual(rows[1][0], "id-B")

    def test_merge_updates_newer_record(self):
        self._create_sample_db(self.local_db)
        self._create_sample_db(self.remote_db)

        # Local has chat A with step_count 5
        with sqlite3.connect(self.local_db) as conn:
            conn.execute("""
                INSERT INTO conversation_summaries VALUES
                ('id-A', 'Chat A Old', 'Old Preview', 5, '2026-10-28 10:00:00', '', '');
            """)

        # Remote has chat A with step_count 12 and newer timestamp
        with sqlite3.connect(self.remote_db) as conn:
            conn.execute("""
                INSERT INTO conversation_summaries VALUES
                ('id-A', 'Chat A Updated', 'New Preview', 12, '2026-10-28 12:00:00', '', '');
            """)

        result = merge_summaries_databases(self.local_db, self.remote_db)
        self.assertEqual(result.updated, 1)

        with sqlite3.connect(self.local_db) as conn:
            row = conn.execute("SELECT title, step_count FROM conversation_summaries WHERE conversation_id='id-A'").fetchone()
            self.assertEqual(row[0], "Chat A Updated")
            self.assertEqual(row[1], 12)

    def test_merge_ignores_older_remote_record(self):
        self._create_sample_db(self.local_db)
        self._create_sample_db(self.remote_db)

        # Local has newer chat A
        with sqlite3.connect(self.local_db) as conn:
            conn.execute("""
                INSERT INTO conversation_summaries VALUES
                ('id-A', 'Local Newer', 'Local', 20, '2026-10-28 15:00:00', '', '');
            """)

        # Remote has older chat A
        with sqlite3.connect(self.remote_db) as conn:
            conn.execute("""
                INSERT INTO conversation_summaries VALUES
                ('id-A', 'Remote Stale', 'Remote', 5, '2026-10-28 09:00:00', '', '');
            """)

        result = merge_summaries_databases(self.local_db, self.remote_db)
        self.assertEqual(result.updated, 0)
        self.assertEqual(result.inserted, 0)

        with sqlite3.connect(self.local_db) as conn:
            row = conn.execute("SELECT title FROM conversation_summaries WHERE conversation_id='id-A'").fetchone()
            self.assertEqual(row[0], "Local Newer")

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
        with sqlite3.connect(self.local_db) as conn:
            conn.execute("CREATE TABLE conversation_summaries (conversation_id TEXT PRIMARY KEY, title TEXT);")
        self._create_sample_db(self.remote_db)

        with self.assertRaises(DatabaseMergeError):
            merge_summaries_databases(self.local_db, self.remote_db)


if __name__ == "__main__":
    unittest.main()
