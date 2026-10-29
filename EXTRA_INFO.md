# 📖 GC-ChatSync — Technical Reference & Deep Dive

This document provides in-depth technical details on GC-ChatSync architecture, folder layouts, SQLite merging, and troubleshooting for advanced users and developers.

---

## 📂 Synchronized Directory Structure

### Google Drive Cloud Layout (`gdrive:GC-ChatSync/`)
```text
GC-ChatSync/
├── manifest.json              --> Cloud state token tracking epoch revision, last active device, and schema version
├── conversation_summaries.db  --> Unified conversation index merged via two-way SQL
├── conversations/             --> Individual chat session databases (<uuid>.db)
├── brain/                     --> Session logs, scratchpads, and generated artifacts
└── projects/                  --> Cross-platform translated workspace configurations
```

### Local Directory Locations
| Operating System | Antigravity Data (`data_dir`) | Projects Directory (`projects_dir`) | GC-ChatSync Config & Logs |
| :--- | :--- | :--- | :--- |
| **Linux** | `~/.gemini/antigravity` | `~/.gemini/config/projects` | `~/.config/gc-chatsync/` |
| **Windows** | `%USERPROFILE%\.gemini\antigravity` | `%USERPROFILE%\.gemini\config\projects` | `%USERPROFILE%\.config\gc-chatsync\` |

### Automatic Exclusions
To optimize transfer speeds and prevent lock contention, the following transient files are automatically excluded:
- SQLite journal files: `*.db-wal`, `*.db-shm`
- Machine-specific hardware tokens: `installation_id`, `antigravity_state.pbtxt`
- Temporary and cache files: `cache/**`, `crashes/**`, `presence/**`, `log/**`, `.backups/**`, `tempmediaStorage/**`

---

## 🔄 Full Lifecycle Architecture

```text
                    [gc-chatsync run / shortcut]
                                 │
                                 ▼
     ┌────────────────────────────────────────────────────────┐
     │               GC-ChatSync PRE-CHECK                    │
     │   Inspects Google Drive manifest.json (epoch & author) │
     └───────────────────────────┬────────────────────────────┘
                                 │
                   ┌─────────────┴─────────────┐
                   ▼                           ▼
          [Cloud has new updates]     [Current device is latest]
                   │                           │
                   ▼                           │
     ┌──────────────────────────────┐          │
     │ 1. PULL deltas (copy --update)│         │
     │ 2. Backup local SQLite index │          │
     │ 3. Two-way SQL Index Merge   │          │
     │ 4. Translate Workspace Paths │          │
     └─────────────┬────────────────┘          │
                   │                           │
                   └─────────────┬─────────────┘
                                 ▼
     ┌────────────────────────────────────────────────────────┐
     │                  LAUNCH ANTIGRAVITY                    │
     │          (Work, chat, and code as usual)               │
     └───────────────────────────┬────────────────────────────┘
                                 │
                    [Antigravity Window Closes]
                                 │
                                 ▼
     ┌────────────────────────────────────────────────────────┐
     │               GC-ChatSync POST-SYNC                    │
     │  1. Checkpoint SQLite WAL (TRUNCATE mode)              │
     │  2. Create atomic snapshot backup with timestamp sync  │
     │  3. PUSH new/updated sessions to Google Drive          │
     │  4. Increment cloud epoch in manifest.json             │
     │  5. Clear dirty session flag and release locks         │
     └────────────────────────────────────────────────────────┘
```

---

## 🔀 SQLite Merge Engine & Timestamp Integrity

1. **Incremental Two-Way Merge:**
   - Antigravity maintains its global sidebar session index in `conversation_summaries.db`.
   - Rather than overwriting this file, GC-ChatSync uses SQLite's `ATTACH DATABASE` to perform an incremental SQL merge (`INSERT OR IGNORE` and `UPDATE` on newer `last_modified_time`).
   - If local and remote schemas differ (e.g. across Antigravity version upgrades), GC-ChatSync halts safely (`DatabaseMergeError`) to avoid dropping new columns.

2. **Snapshot Timestamp Preservation (`os.utime`):**
   - Live SQLite databases cannot be uploaded directly while open. GC-ChatSync uses `sqlite3.Connection.backup()` to create an atomic snapshot.
   - To prevent `rclone copy --update` from treating all unedited databases as "new" (which would needlessly re-upload hundreds of megabytes), GC-ChatSync explicitly copies the source file's modification timestamp (`st_mtime`) to the snapshot using `os.utime`. Only genuinely modified conversations (~30–40 MB) are transferred.

3. **Automatic Backups & Retention:**
   - Every pre-merge database is backed up into `.backups/` with an SHA-256 hash.
   - Automatic retention keeps 7 days of snapshots while preserving a safety floor of at least 3 backups.
   - Fast rollback is available at any time via `gc-chatsync restore-backup`.

---

## 🔑 Google Cloud OAuth Scope Details

When configuring Google Drive via `rclone config`, two permission scopes are available:

- **Scope 1 (`drive` - Full Access, Recommended):**
  - Allows full read and write access to files in your Google Drive.
  - Best for multi-device setups because files created by Device A are immediately discoverable and accessible by Device B without permission boundaries.
- **Scope 2 (`drive.file` - Application-Restricted Access):**
  - Limits rclone strictly to files and folders created by the app itself.
  - If you choose Scope 2, you **must** use the identical Google Cloud Client ID and Secret across all your devices; otherwise, a secondary device will not have permission to view or sync files created by the first.

---

## 🩺 Doctor & Self-Healing Diagnostics (`reconcile`)

Run `gc-chatsync reconcile` when you need to audit or repair local and cloud states:
- **Unindexed Session Recovery:** Scans `conversations/*.db` for valid sessions not present in `conversation_summaries.db` and re-indexes them automatically.
- **Corrupt File Quarantine:** Inspects local databases for SQLite corruption and quarantines broken files into `.corrupt/`.
- **Stale Lock Recovery:** If a previous process was killed abruptly, witness locks older than 1 hour or belonging to inactive PIDs are safely cleared.

---

## 📋 System Logs & Diagnostics

Persistent logs are recorded with automatic 5MB rotation:
- **Linux:** `~/.config/gc-chatsync/logs/sync.log`
- **Windows:** `%USERPROFILE%\.config\gc-chatsync\logs\sync.log`

Run with verbose logging for detailed step-by-step diagnostics:
```bash
gc-chatsync -v status
gc-chatsync -v run
```

---

## 🗺️ Roadmap

- [x] Two-way incremental SQLite index merge engine
- [x] Atomic SQLite snapshotting with `os.utime` timestamp synchronization
- [x] Process locking (`ProcessLock`) and unclean exit recovery (`.session_dirty`)
- [x] Monotonic cloud revision epochs (`manifest.json`)
- [x] Cross-platform workspace path translation (`C:\...` $\leftrightarrow$ `/home/...`)
- [x] Modern Tkinter GUI with automatic Dark/Light system theme detection
- [x] Self-healing diagnostic doctor (`gc-chatsync reconcile`)
- [x] Desktop application shortcut integration with one-click restore
- [x] Multi-threaded concurrent uploads (`ThreadPoolExecutor`)
- [ ] Direct support for macOS default paths
- [ ] Multi-language GUI (i18n)
