# 🌌 GC-ChatSync (Google Cloud / Drive Chat Sync)

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Linux%20%7C%20Windows-brightgreen.svg)](#supported-platforms)
[![Storage](https://img.shields.io/badge/Storage-Google%20Drive-orange.svg)](#how-it-works)
[![Built with AI](https://img.shields.io/badge/Built%20with-Google%20Antigravity%20AI-blueviolet.svg)](#about-the-project)
[![Indie Dev](https://img.shields.io/badge/Dev-Independent-success.svg)](#about-the-project)

> **Zero-bloat, cross-platform Google Drive synchronization for Google Antigravity AI chat histories, brain artifacts, and conversation indexes.**

---

## 👨‍💻 About The Project

> [!NOTE]
> 🚀 **Author Note:** This is my very first open-source project as an independent developer, designed and developed in continuous pair-programming collaboration with **Google Antigravity AI**. 
> Built out of a real everyday need to keep my desktop workstation (Windows) and laptop (Linux) fully synchronized, this project is shared with the community as a modular, lightweight solution. Feedback, issues, and contributions are super welcome!

---

## 💡 The Problem

Google Antigravity is a groundbreaking AI-first development environment, but currently lacks native, multi-device cloud synchronization for ongoing conversations and session state across different machines.

Simply pointing general-purpose cloud storage clients (such as Google Drive for Desktop, OneDrive, or Dropbox) directly at the internal `~/.gemini/antigravity/` folder leads to serious issues:
1. **SQLite WAL Locking & Corruption:** Antigravity runs SQLite databases in Write-Ahead Log (`WAL`) mode. Syncing raw database files while the application is active causes lock contention, desynchronized `-wal` files, and database corruption.
2. **Conversation Overwrites:** The global conversation index (`conversation_summaries.db`) lists all sessions in the sidebar. A blind file overwrite from one PC silently wipes out sessions created on the other.
3. **No Linux Desktop Client:** Google Drive does not provide an official desktop synchronization client for Linux.

---

## ⚡ The Solution

`GC-ChatSync` (`gc-chatsync`) is an intelligent, lightweight synchronization wrapper built with **Python** and **`rclone`**:

- 🔒 **Safe Lifecycle Execution:** Synchronizes *before* Antigravity opens (PULL) and *after* it gracefully exits (PUSH), guaranteeing that database files are never touched while locked.
- 🔀 **Intelligent SQL Index Merging:** Performs two-way incremental merges on `conversation_summaries.db` (`INSERT OR REPLACE INTO conversation_summaries ...`), combining chat histories without dropping sessions from either device.
- ☁️ **Cloud Token State (`sync_state.json`):** Maintains a tiny manifest on Google Drive tracking the last machine that synced (`"last_pc"`).
  - If the cloud was modified by the *other* PC $\rightarrow$ automatically **pulls** and merges changes before launch.
  - If you are on the *same* machine $\rightarrow$ launches Antigravity instantly with zero redundant downloads.
  - Upon closing Antigravity $\rightarrow$ automatically **pushes** new/updated sessions to Drive and updates the token.
- 🚀 **Zero Overhead & Zero Costs:** No heavy background daemons, no memory footprint during development, and 100% free Google Drive API usage via standard OAuth.

---

## 🏗️ Architecture & Workflow

```
[User Launches GC-ChatSync]
            │
            ▼
┌────────────────────────────────────────────────────────┐
│               GC-ChatSync PRE-CHECK                    │
│      Checks Google Drive: sync_state.json              │
└───────────────────────────┬────────────────────────────┘
                            │
              ┌─────────────┴─────────────┐
              ▼                           ▼
     [Other PC modified Drive]   [Current PC is up-to-date]
              │                           │
              ▼                           │
┌──────────────────────────────┐          │
│ 1. PULL deltas via rclone    │          │
│ 2. Merge SQLite indexes      │          │
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
│  1. Checkpoint local SQLite databases                  │
│  2. PUSH new/updated sessions to Google Drive          │
│  3. Set last_pc = current_pc in cloud manifest         │
└────────────────────────────────────────────────────────┘
```

---

## 🖥️ Supported Platforms

| Platform | Antigravity Flavor | Status |
| :--- | :--- | :--- |
| **Linux (Ubuntu, Debian, Fedora, Arch)** | Antigravity 2.0 Standalone / CLI | ✅ Supported |
| **Windows 10 / 11** | Antigravity 2.0 Standalone / CLI | ✅ Supported |
| **macOS** | Antigravity 2.0 / CLI | 🚧 Planned |

---

## 📂 Synchronized Antigravity Data

```
~/.gemini/antigravity/
├── conversations/            --> Synced (Each chat session .db with unique UUID)
├── brain/                    --> Synced (Transcripts, scratchpads, generated artifacts)
├── conversation_summaries.db --> Merged via SQL (Sidebar conversation list)
└── sync_state.json           --> Cloud state manifest
```

*Automatically excluded:* `crashes/`, `presence/`, `cache/`, `installation_id`, `*.db-wal`, `*.db-shm`.

---

## 🚀 Getting Started

### 1. Prerequisites
- Python 3.10+
- [`rclone`](https://rclone.org/) configured with your Google Drive remote (named `gdrive`):

```bash
# On Linux (Ubuntu/Debian):
sudo apt install rclone
rclone config   # Follow interactive prompt: choose 'drive' and log in with your Google account

# On Windows (PowerShell):
winget install Rclone.Rclone
rclone config
```

### 2. Installation
```bash
git clone https://github.com/<your-username>/gc-chatsync.git
cd gc-chatsync
pip install -e .
```

### 3. Usage
Launch Antigravity seamlessly through the `gc-chatsync` wrapper:

```bash
# On Linux:
gc-chatsync run

# On Windows:
gc-chatsync.exe run
```

---

## 🗺️ Roadmap

- [x] Initial architecture & safe SQLite merge strategy
- [x] Repository initialization & open-source documentation
- [ ] Core Python package (`gc_chatsync`):
  - [ ] Cross-platform environment detection (`Linux` vs `Windows`)
  - [ ] SQLite two-way index merge engine
  - [ ] Cloud state lock & manifest validation
  - [ ] Integrated rclone runner
- [ ] Desktop launch helpers:
  - [ ] Linux `.desktop` desktop entry
  - [ ] Windows `.bat` / shortcut launcher
- [ ] Optional end-to-end encryption (`AES-256`) before cloud upload
- [ ] Cross-platform workspace path translation (`C:\...` $\leftrightarrow$ `/home/...`)

---

## 🤝 Contributing

Contributions, issues, ideas, and pull requests are welcome! If you find this project helpful, please consider leaving a ⭐ on GitHub!

---

## 📄 License

Distributed under the **MIT License**. See [`LICENSE`](LICENSE) for more information.
