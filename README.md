# 🌌 GC-ChatSync (Google Cloud / Drive Chat Sync)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/Version-0.1.0-brightgreen.svg)](#features)
[![Platform](https://img.shields.io/badge/Platforms-Linux%20%7C%20Windows-blue.svg)](#supported-platforms)
[![Storage](https://img.shields.io/badge/Storage-Google%20Drive-orange.svg)](#how-it-works)
[![Built with AI](https://img.shields.io/badge/Built%20with-Google%20Antigravity%20AI-blueviolet.svg)](#about-the-project)
[![Indie Dev](https://img.shields.io/badge/Dev-Independent-success.svg)](#about-the-project)

> **Zero-bloat, cross-platform Google Drive synchronization for Google Antigravity AI chat histories, brain artifacts, and conversation indexes.**

---

> [!TIP]
> 🤖 **Zero-Effort AI Setup:**
> If you are using an AI coding assistant (**Google Antigravity**, **Cursor**, **Claude Code**, **GitHub Copilot**, or **ChatGPT**), you don't even need to configure things manually!
> Simply copy and paste the ready-to-run prompt from [**`AI_PROMPT.md`**](AI_PROMPT.md) directly into your assistant's chat. It will inspect your operating system, check your tools, guide the Google Drive authorization, and configure GC-ChatSync automatically for you.

---

## 👨‍💻 About The Project

> [!NOTE]
> 🚀 **Author Note:** This is an open-source project created by an independent developer in continuous pair-programming collaboration with **Google Antigravity AI**.
> Built out of an everyday need to keep a desktop workstation (Windows) and a laptop (Linux) fully synchronized, this project is shared with the community as a modular, lightweight, and robust solution. Feedback, issues, and contributions are super welcome!

### 🎯 Design Assumptions
GC-ChatSync is designed under the assumption of **a single user working on one device at a time** in an alternating pattern:
- You work on Machine A (e.g., Windows Desktop) $\rightarrow$ Antigravity closes $\rightarrow$ Changes are pushed to Google Drive.
- You switch to Machine B (e.g., Linux Laptop) $\rightarrow$ Antigravity launches $\rightarrow$ Changes are pulled and merged from Google Drive.
- Simultaneous concurrent editing of the exact same chat session on two PCs at the same moment is not supported.

---

## 💡 The Problem

Google Antigravity is an AI-first development environment, but currently lacks native, multi-device cloud synchronization for ongoing conversations and session state across different machines.

Simply pointing general-purpose cloud storage clients (such as Google Drive for Desktop, OneDrive, or Dropbox) directly at the internal `~/.gemini/antigravity/` folder leads to serious issues:
1. **SQLite WAL Locking & Corruption:** Antigravity runs SQLite databases in Write-Ahead Log (`WAL`) mode. Syncing raw database files while the application is active causes lock contention, desynchronized `-wal` files, and database corruption.
2. **Conversation Overwrites:** The global conversation index (`conversation_summaries.db`) lists all sessions in the sidebar. A blind file overwrite from one PC silently wipes out sessions created on the other.
3. **Cross-Platform Path Incompatibility:** Project workspaces referenced in conversations use platform-dependent URIs (`file:///home/user/...` vs `file:///C:/Users/...`). Blind syncing breaks workspace links in the Antigravity sidebar.
4. **No Linux Desktop Client:** Google Drive does not provide an official desktop synchronization client for Linux.

---

## ⚡ The Solution

`GC-ChatSync` (`gc-chatsync`) is an intelligent, lightweight synchronization wrapper and graphical dashboard built with **Python 3.10+ standard library** and **`rclone`**:

- 🖥️ **Modern Graphical Dashboard (`GUI`):** Built-in dark/light GUI that automatically follows your operating system theme, providing one-click launch, real-time sync status, and integrated logging.
- 🔒 **Safe Lifecycle Execution:** Synchronizes *before* Antigravity opens (PULL) and *after* it exits (PUSH), guaranteeing that database files are never touched while locked.
- 🔀 **Intelligent SQL Index Merging:** Performs two-way incremental merges on `conversation_summaries.db` using `ATTACH DATABASE` and dynamic column discovery. Inserts missing conversations and only updates existing records when `remote.last_modified_time > local.last_modified_time`.
- 🛡️ **Non-Destructive File Sync:** Uses `rclone copy --update` instead of destructive `sync` commands, ensuring files are never deleted remotely by a local deletion or vice versa.
- 💾 **Online SQLite Backups:** Utilizes SQLite's native online backup API (`conn.backup()`) and `PRAGMA wal_checkpoint(TRUNCATE)` to generate clean, crash-safe snapshots in `.backups/` (retaining the 5 most recent snapshots).
- 🔢 **Monotonic Cloud Epochs (`manifest.json`):** Tracks cloud state with strictly increasing epoch numbers and per-device tokens, eliminating false updates caused by clock skew between machines.
- 🚨 **Crash & Dirty Session Recovery:** Tracks session state via `.session_dirty`. If your machine suddenly powers off or reboots during a session, the next launch warns you and safely flushes unsaved local changes to the cloud.
- 🔄 **Cross-Platform Workspace Translation:** Automatically translates workspace URIs in projects (`projects/*.json`) and conversation summaries between Windows (`C:/Users/...`) and Linux (`/home/...`), with support for custom root path mappings.
- 🔗 **Desktop Shortcut Integration:** Seamlessly patches your habitual Antigravity dock and desktop icon with automated backup (`.bak`), or creates a standalone shortcut if preferred.

---

## 🏗️ Architecture & Workflow

```
[User Clicks Antigravity Icon / gc-chatsync]
                    │
                    ▼
┌────────────────────────────────────────────────────────┐
│               GC-ChatSync PRE-CHECK                    │
│   Checks Google Drive manifest.json (epoch & device)   │
└───────────────────────────┬────────────────────────────┘
                            │
              ┌─────────────┴─────────────┐
              ▼                           ▼
     [Cloud has new updates]     [Current PC is up-to-date]
              │                           │
              ▼                           │
┌──────────────────────────────┐          │
│ 1. PULL deltas (copy --update)│          │
│ 2. Backup local SQLite       │          │
│ 3. Two-way SQL Index Merge   │          │
│ 4. Translate Workspace URIs  │          │
└─────────────┬────────────────┘          │
              │                           │
              └─────────────┬─────────────┘
                            ▼
┌────────────────────────────────────────────────────────┐
│                  LAUNCH ANTIGRAVITY                    │
│         (Work, chat, plan and code as usual)           │
└───────────────────────────┬────────────────────────────┘
                            │
               [Antigravity Window Closes]
                            │
                            ▼
┌────────────────────────────────────────────────────────┐
│               GC-ChatSync POST-SYNC                    │
│  1. Checkpoint SQLite WAL (TRUNCATE)                   │
│  2. Create atomic snapshot backup                      │
│  3. PUSH new/updated sessions to Google Drive          │
│  4. Increment cloud epoch in manifest.json             │
│  5. Clear dirty session flag                           │
└────────────────────────────────────────────────────────┘
```

---

## 🖥️ Supported Platforms

| Platform | Antigravity Flavor | Status |
| :--- | :--- | :--- |
| **Linux (Ubuntu, Debian, Fedora, Arch)** | Antigravity Standalone / AppImage / CLI | ✅ Supported (v0.1.0) |
| **Windows 10 / 11** | Antigravity Standalone / CLI | ✅ Supported (v0.1.0) |
| **macOS** | Antigravity Standalone / CLI | 📋 Planned |

---

## 📂 Synchronized Antigravity Data

```
Google Drive/
└── GC-ChatSync/
    ├── manifest.json              --> Cloud state token tracking epoch revision and author
    ├── conversation_summaries.db  --> Unified conversation index merged via SQL
    ├── conversations/             --> Individual chat session databases (<uuid>.db)
    ├── brain/                     --> Session logs, scratchpads, generated artifacts
    └── projects/                  --> Cross-platform translated project definitions
```

*Excluded automatically:* `crashes/`, `presence/`, `cache/`, `installation_id`, `*.db-wal`, `*.db-shm`.

---

## 🚀 Getting Started

### 1. Prerequisites (Connect your Google Drive)
Each user connects their personal Google Drive via [`rclone`](https://rclone.org/):

```bash
# On Linux (Ubuntu/Debian):
sudo apt update && sudo apt install -y rclone python3-tk
rclone config   # Choose 'drive' and log in with your Google account

# On Windows (PowerShell):
winget install Rclone.Rclone
rclone config
```

> [!TIP]
> **Google Drive Scope Note:**
> In `rclone config`, Scope `1` (Full access: `drive`) is recommended for zero-config sharing between devices. If you select Scope `2` (`drive.file`), ensure you configure the same custom OAuth `client_id` on both machines so they can see the same app directory.
> 
> **Want Zero-Knowledge Encryption?**
> You can easily wrap your `gdrive:` remote with an [`rclone crypt`](https://rclone.org/crypt/) remote (e.g. `gdrive-crypt:`). In GC-ChatSync setup, simply specify `gdrive-crypt` as your remote name. All chat files will be encrypted locally with AES-256 before being uploaded to Google Drive!

### 2. Installation
```bash
git clone https://github.com/ClaudioAmen/GC-ChatSync.git
cd GC-ChatSync

# Linux (recommended: pipx or user install):
pip install --user --break-system-packages -e .   # or: pipx install .

# Windows:
pip install -e .
```

### 3. Usage & Commands

```bash
# Launch Graphical Dashboard (Auto Dark/Light Theme)
gc-chatsync gui    # or just: gc-chatsync

# Directly launch Antigravity without cloud sync (Bypass)
gc-chatsync launch # or: gc-chatsync run --no-sync

# Check system status, Google Drive ping, and cloud alignment
gc-chatsync status

# Run full wrapper in CLI mode (PULL -> Run Antigravity -> PUSH on close)
gc-chatsync run

# Manual one-off pull or push
gc-chatsync pull
gc-chatsync push

# Connect GC-ChatSync directly to your habitual Antigravity desktop icon
gc-chatsync install-shortcut             # Updates main icon (with .bak backup)
gc-chatsync install-shortcut --standalone # Creates separate icon
gc-chatsync restore-shortcut            # Reverts original icon from backup

# Interactive setup wizard
gc-chatsync setup
```

---

## 🔒 Security & Privacy Best Practices

1. **Protect your OAuth Token:** Rclone stores its credentials in `~/.config/rclone/rclone.conf`. On Linux, ensure strict file permissions:
   ```bash
   chmod 600 ~/.config/rclone/rclone.conf
   ```
2. **Scoped Drive Permissions:** When running `rclone config`, you can choose Scope `2` (`drive.file`). This limits rclone's access strictly to files and folders created by GC-ChatSync, keeping the rest of your Google Drive inaccessible.

---

## ⚠️ Known Limitations & Troubleshooting

- **First Run on Second PC:** When launching GC-ChatSync on your second PC for the first time, use `gc-chatsync pull -f` (or click **"Riprova / Pull"** in the GUI) to pull the initial history from Drive.
- **Non-Destructive Deletions:** Because GC-ChatSync uses `copy --update` to safeguard your chat history, a conversation database deleted on one PC is not deleted from Google Drive.
- **Active Witness Tracking:** The cloud manifest tracks active sessions (`active_device`). If a machine powers off unexpectedly, the other machine warns you and performs a safety merge before pushing.
- **Schema Safety:** GC-ChatSync verifies base SQLite schema columns (`conversation_id`, `last_modified_time`) and dynamically maps columns to protect your database if Antigravity versions differ between machines.
- **Shortcut Re-patching:** An official Antigravity application update may overwrite the desktop `.desktop` file or shortcut; simply run `gc-chatsync install-shortcut` to re-hook GC-ChatSync.
- **Log Files:** Persistent session logs are written to `~/.config/gc-chatsync/logs/sync.log` (`%APPDATA%\gc-chatsync\logs\sync.log` on Windows) with automatic 5MB rotation.
- **Active Lock:** If you see `Another GC-ChatSync instance is already running`, an active process is currently holding the sync lock. If a process was terminated abruptly, stale locks are automatically recovered on the subsequent run.

---

## 🗺️ Roadmap

- [x] SQLite two-way index merge engine (`ATTACH DATABASE` + `PRAGMA table_info`)
- [x] Atomic SQLite snapshotting via `conn.backup()` and WAL checkpointing
- [x] Process locking (`ProcessLock`) and unclean exit recovery (`.session_dirty`)
- [x] Monotonic cloud revision epochs (`manifest.json`)
- [x] Cross-platform workspace path translation (`C:\...` $\leftrightarrow$ `/home/...`)
- [x] Modern Tkinter GUI with automatic Dark/Light system theme detection
- [x] Desktop shortcut integration and one-click rollback (`.bak`)
- [x] Zero-effort AI onboarding prompt (`AI_PROMPT.md`)
- [ ] Direct support for macOS paths

---

## 🤝 Contributing

Contributions, issues, ideas, and pull requests are welcome! If you find this project helpful, please consider leaving a ⭐ on GitHub!

---

## 📄 License

Distributed under the **MIT License**. See [`LICENSE`](LICENSE) for more information.
