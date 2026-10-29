# 🤖 AI Assistant Onboarding Prompt: GC-ChatSync Setup

> **Note for the User:** If you are using an AI coding assistant (such as **Google Antigravity**, **Cursor**, **Claude Code**, **GitHub Copilot**, or **ChatGPT**), you can simply copy and paste the prompt below into your assistant chat. It will inspect your system, check your tools, and set up GC-ChatSync automatically for you.

---

### Copy & Paste the prompt below into your AI coding assistant:

```markdown
You are an expert AI software engineer helping me set up **GC-ChatSync** on my machine.
GC-ChatSync is a cross-platform synchronization tool that keeps Google Antigravity chat histories, session trajectories, brain artifacts, and project definitions synchronized between computers (Linux and Windows) using personal Google Drive storage and `rclone`.

Repository location: the current project workspace directory.

**Safety Guideline:** Always ask my permission before running commands that install packages or modify system settings.

Please perform the following onboarding workflow step-by-step:

---

### Step 1: Environment & Operating System Inspection
1. Detect my operating system (`Linux` or `Windows`).
2. Verify that Python 3.10+ is installed (`python3 --version` or `python --version`).
3. Check GUI support (Tkinter):
   - Test with `python3 -m tkinter` (or `python -m tkinter`).
   - If missing on Linux (Ubuntu/Debian), explain that Tkinter is required for the dashboard and ask permission to install via `sudo apt install -y python3-tk`.
4. Check if `rclone` is installed on PATH (`rclone version`):
   - **On Linux (Ubuntu/Debian):** If missing, ask permission and offer to install via `sudo apt update && sudo apt install -y rclone`.
   - **On Linux (Arch/Fedora):** Offer `sudo pacman -S rclone` or `sudo dnf install rclone`.
   - **On Windows:** If missing, ask permission and offer to install via `winget install Rclone.Rclone` or `choco install rclone`.

---

### Step 2: Google Drive Authorization Verification (Mandatory Personal Client ID)
1. Run `rclone listremotes` to check if a remote named `gdrive:` (or similar) already exists.
2. If configured, test connectivity using `rclone about gdrive:`.
3. Check if a custom `client_id` is configured in `rclone config show gdrive`.
   - **Crucial Requirement:** A personal Google Cloud OAuth Client ID is a **strict mandatory dependency** for GC-ChatSync. Without it, Google throttles the shared public rclone client pool (`RATE_LIMIT_EXCEEDED`), causing sync timeouts and upload stalls on multi-megabyte chat sessions. It is 100% free and requires no credit card.
4. If missing or setting up from scratch:
   - Guide the user to get credentials from [Google Cloud Console](https://console.cloud.google.com/):
     a. Enable **Google Drive API** in Library.
     b. Set **OAuth consent screen** to External, add user's email to Test Users.
     c. Create **OAuth Client ID** of type **Desktop app**.
   - Guide through `rclone config`:
     - Select `n` (New remote) -> name: `gdrive` (or edit existing `e`)
     - Storage type: `drive` (Google Drive)
     - **Client ID**: paste user's Google Client ID
     - **Client Secret**: paste user's Google Client Secret
     - Scope: `1` (Full access: `drive`)
     - Advanced config: `n`
     - Auto config: `y` (opens browser to authorize)
     - Confirm and save: `y` -> `q`

---

### Step 3: Package Installation
1. Install GC-ChatSync:
   - Linux: `pipx install .` (recommended) or `pip install --user --break-system-packages -e .` (or standard `pip install -e .` within a virtual environment).
   - Windows: `pip install -e .`
2. Verify that `gc-chatsync --version` responds in the terminal.

---

### Step 4: Initial Configuration & Shortcut Integration
1. Ask me:
   > *"GC-ChatSync is ready to be configured. Would you like me to link it directly to your habitual Antigravity application icon (with automatic backup), or would you prefer a separate 'GC-ChatSync' desktop icon?"*
2. Based on my preference, run the appropriate command:
   - Link habitual icon (recommended): `gc-chatsync install-shortcut`
   - Create separate icon: `gc-chatsync install-shortcut --standalone`
3. If I have custom project paths (e.g., projects stored in a different location on Windows vs Linux), ask if I need custom path mappings in `~/.config/gc-chatsync/config.json`.

---

### Step 5: Verification & Cloud Status Check
1. Run `gc-chatsync status` to confirm that:
   - Google Drive connection is `✅ Connected`.
   - Local paths are detected correctly.
   - Sync alignment status is reported.
2. Inform me that the setup is complete and explain:
   - Clicking the Antigravity desktop icon will now open the GC-ChatSync dashboard with dark/light auto-theme and real-time visual progress bar (`ttk.Progressbar`).
   - Clicking **"🚀 Launch Antigravity (Auto-Sync)"** pulls any new chats in parallel from Google Drive, launches Antigravity, and automatically pushes updates on close (brief 1-3 second network delays during checks are normal due to Google Drive API round-trips and rate limits).
   - Clicking **"⚡ Bypass Sync & Launch Directly"** launches Antigravity directly without running cloud pull or push.
   - I can also launch directly from CLI using `gc-chatsync launch` or run manual synchronizations with `gc-chatsync pull` and `gc-chatsync push`.
```
