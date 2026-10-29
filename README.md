# 🌌 GC-ChatSync

> **Lightweight, cross-platform Google Drive sync for Google Antigravity AI chat histories, brain artifacts, and project workspaces.**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platforms-Linux%20%7C%20Windows-blue.svg)](#quick-start)
[![Storage](https://img.shields.io/badge/Storage-Google%20Drive-orange.svg)](#1-prerequisites-google-drive-via-rclone)

---

> [!TIP]
> 🤖 **Zero-Effort AI Setup:**
> If you use an AI coding assistant (**Google Antigravity**, **Cursor**, **Claude Code**, or **ChatGPT**), simply copy-paste the prompt from [**`AI_PROMPT.md`**](AI_PROMPT.md) into your assistant chat. It will configure rclone, environment paths, and GC-ChatSync automatically for you.

---

## 🎯 Design & Use Case

GC-ChatSync is purpose-built for developers who alternate between multiple workstations (supporting **Linux $\leftrightarrow$ Linux**, **Windows $\leftrightarrow$ Windows**, and **Linux $\leftrightarrow$ Windows** seamlessly):

- **Asynchronous Alternating Sync (1 device in use at a time):**
  - Work on **Device A** $\rightarrow$ Close Antigravity $\rightarrow$ Automatic incremental push of conversations, brain artifacts, and index to Google Drive.
  - Switch to **Device B** $\rightarrow$ Launch Antigravity $\rightarrow$ Automatic pull and incremental SQLite merge of new sessions.
  - *(Note: Concurrent real-time editing of the exact same chat session on two devices simultaneously is not supported by Antigravity's local SQLite architecture).*
- **Agnostic Cross-Platform Compatibility:** Workspace paths (`/home/...` $\leftrightarrow$ `C:\Users\...`), index entries, and OS application shortcuts adapt automatically regardless of your operating system mix.
- **Why Not Generic Cloud Clients?** Standard desktop cloud sync tools (Google Drive for Desktop, OneDrive, Dropbox) corrupt SQLite databases in Write-Ahead Log (`WAL`) mode, blindly overwrite global session index files, break absolute workspace paths across platforms, and lack native desktop clients on Linux.

---

## ⚡ Key Features

- 🖥️ **Modern Desktop GUI:** Auto dark/light theme, live file-by-file transfer streaming, and integrated activity log.
- 🔀 **Intelligent SQLite Merge:** Two-way incremental merge of `conversation_summaries.db` without wiping sessions between devices.
- 🩺 **Self-Healing Doctor (`reconcile`):** 1-click auto-repair for unindexed sessions, corrupted database quarantine, and stale witness lock release.
- ⏪ **Smart Backups & Rollback:** Hash-based deduplication, 7-day retention (with 3-backup safety floor), and 1-click snapshot restore.
- 🔒 **Offline Safety & Witness Locks:** Prevents cloud overwrites when offline; tracks active sessions via cloud tokens (`manifest.json`).
- 🔄 **Cross-Platform Workspace Translation:** Translates workspace paths (`C:\Users\...` $\leftrightarrow$ `/home/...`) automatically.
- 🔗 **Desktop Shortcut Integration:** Seamlessly hooks your habitual Antigravity desktop icon with an automated sync wrapper (with 1-click restore).

---

## 🚀 Quick Start

### 1. Prerequisites (Google Drive via rclone)

> [!IMPORTANT]
> **Mandatory Dependency — Personal Google Drive OAuth Client ID & Secret:**
> You **MUST** configure your own Google Cloud OAuth Client ID and Secret in rclone. Without it, Google throttles the shared public rclone client pool (`RATE_LIMIT_EXCEEDED`), resulting in transfer freezes and sync timeouts. Creating personal credentials is **100% free**, takes 2 minutes, and requires **no credit card**.

1. Go to [Google Cloud Console](https://console.cloud.google.com/) and create a project (e.g. `GC-ChatSync`).
2. In **APIs & Services** $\rightarrow$ **Library**, search for **Google Drive API** and click **Enable**.
3. In **OAuth consent screen**, choose **External**, enter an app name and your email, and save. Under **Audience / Test Users**, add your Google email.
4. In **Credentials** $\rightarrow$ **Create Credentials** $\rightarrow$ **OAuth client ID**, choose Application type: **Desktop app**. Copy the **Client ID** and **Client Secret**.

Configure your Google Drive remote named `gdrive` with rclone:

```bash
# Linux (Ubuntu/Debian)
sudo apt update && sudo apt install -y rclone python3-tk
rclone config   # Select 'drive', enter your Client ID & Secret, scope '1' (full access)

# Windows (PowerShell)
winget install Rclone.Rclone
rclone config
```

### 2. Installation
```bash
git clone https://github.com/ClaudioAmen/GC-ChatSync.git
cd GC-ChatSync

# Linux (pipx recommended)
pipx install .   # or: pip install --user -e .

# Windows
pip install -e .
```

### 3. Usage

```bash
# Open Graphical Dashboard (Default)
gc-chatsync gui

# One-Click Desktop Shortcut Integration
gc-chatsync install-shortcut             # Hooks habitual Antigravity icon
gc-chatsync install-shortcut --standalone # Or creates a separate desktop icon

# CLI Lifecycle & Manual Sync
gc-chatsync run              # Full lifecycle: Pull -> Launch -> Push on exit
gc-chatsync pull             # Pull and merge cloud deltas
gc-chatsync push             # Push local sessions to cloud
gc-chatsync push --full      # Force full snapshot and upload of all databases
gc-chatsync status           # View cloud alignment and active device

# Maintenance & Recovery
gc-chatsync doctor           # 🩺 Diagnose & auto-repair orphaned/unindexed sessions
gc-chatsync doctor --repair-manifest # Repair or reconstruct corrupted manifest.json
gc-chatsync restore-backup   # ⏪ Revert index to the latest pre-merge snapshot
```

---

## 🛠️ How It Works

1. **Before Launch (PULL):** Inspects the cloud manifest. If updates exist, downloads deltas via `rclone` (using 4 parallel workers), creates an atomic SQLite backup, and merges `conversation_summaries.db` incrementally.
2. **Execution:** Launches Antigravity and runs an epoch-guarded witness heartbeat thread. (If offline, launches in safe local mode, marks the session dirty for subsequent recovery, and suppresses the push on exit).
3. **On Close (PUSH):** Checkpoints SQLite WAL journals, takes crash-safe database snapshots, uploads modified sessions and brain artifacts to Drive, updates the manifest revision epoch, and clears the dirty flag.

---

## ⚠️ Known Limitations

- **Google Drive API Latency:** GC-ChatSync synchronizes via official Google Drive REST APIs. High-level timing breakdowns are logged at `INFO` level (e.g., `Pull completed in 3.42s`, `Push completed in 2.10s`). Operations are tuned using high-throughput flags (`--transfers 8`, `--checkers 16`, 64MB chunks, and pacer sleep 10ms) powered by your dedicated Google Cloud project credentials.
- **Non-Destructive Deletions:** To protect your history against accidental data loss, conversations removed locally are not deleted from Google Drive.
- **Offline Mode Safety:** When offline, Antigravity launches normally in local mode; the post-exit push is automatically suppressed to prevent overwriting cloud history. Local changes are tracked as dirty and will automatically synchronize on the next online launch.
- **Full Brain Sync Option:** By default, transient execution outputs (`steps/`, `tasks/`, `messages/`, `subagents/`) are omitted to keep transfers lightweight. If you wish to sync every step and task directory, enable `"sync_full_brain": true` in `config.json`.
- **Antigravity Updates:** Official Antigravity updates may overwrite the application desktop file or shortcut. Simply run `gc-chatsync install-shortcut` again to restore the sync hook.

---

## 📚 Detailed Documentation

Looking for directory trees, SQLite merge internals, OAuth scope deep-dives, or the complete roadmap?  
👉 See [**`EXTRA_INFO.md`**](EXTRA_INFO.md).

---

## 🤝 Contributing

Contributions, bug reports, feature suggestions, and pull requests are warmly welcome!  
If you find GC-ChatSync helpful, please consider leaving a ⭐ on GitHub!

---

## 📄 License

Distributed under the **MIT License**. See [`LICENSE`](LICENSE) for details.
