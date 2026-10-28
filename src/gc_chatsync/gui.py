"""Lightweight graphical interface (GUI) for GC-ChatSync using Tkinter.

Provides a one-click dashboard to monitor Google Drive alignment,
launch Antigravity, trigger manual syncs, and view logs without the terminal.
The color theme follows the operating system preference (dark fallback).
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading

try:
    import tkinter as tk
    from tkinter import messagebox, ttk
except ImportError:
    tk = None  # type: ignore
    ttk = None  # type: ignore

from datetime import datetime, timezone

from gc_chatsync import __version__
from gc_chatsync.config import (
    AppConfig,
    get_antigravity_version,
    get_log_file_path,
    get_system_name,
    load_config,
)
from gc_chatsync.drive import RcloneError, SyncManifest
from gc_chatsync.lock import mark_session_dirty
from gc_chatsync.merger import DatabaseMergeError
from gc_chatsync.runner import SyncRunner, SyncStatus
from gc_chatsync.theme import Palette, get_palette

logger = logging.getLogger("gc_chatsync.gui")

FONT_FAMILY = "Segoe UI" if get_system_name() == "windows" else "Helvetica"
MONO_FAMILY = "Consolas" if get_system_name() == "windows" else "Monospace"


class LogTextHandler(logging.Handler):
    """Logging handler that writes messages to a Tkinter Text widget."""

    def __init__(self, text_widget: "tk.Text"):
        super().__init__()
        self.text_widget = text_widget

    def emit(self, record: logging.LogRecord) -> None:
        msg = self.format(record)

        def append() -> None:
            try:
                self.text_widget.configure(state="normal")
                self.text_widget.insert("end", msg + "\n")
                self.text_widget.see("end")
                self.text_widget.configure(state="disabled")
            except tk.TclError:
                # Widget already destroyed (window closed while a worker was logging)
                pass

        try:
            self.text_widget.after(0, append)
        except (RuntimeError, tk.TclError):
            pass


class ChatSyncGUI:
    """Tkinter-based management dashboard."""

    def __init__(self, root: "tk.Tk", palette: Palette | None = None):
        self.root = root
        self.palette = palette or get_palette()
        self.root.title(f"GC-ChatSync v{__version__}")
        self.root.geometry("680x740")
        self.root.minsize(620, 680)
        self.root.configure(bg=self.palette.bg)

        self.config: AppConfig = load_config()
        self.runner: SyncRunner = SyncRunner(self.config)
        self.is_running = False

        self._init_styles()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.refresh_status_async()

    # ------------------------------------------------------------------ styles
    def _init_styles(self) -> None:
        p = self.palette
        style = ttk.Style(self.root)
        # 'clam' is the only built-in theme that honours custom colors on every OS
        style.theme_use("clam")

        style.configure(".", background=p.bg, foreground=p.fg, font=(FONT_FAMILY, 10))
        style.configure("TFrame", background=p.bg)
        style.configure("Card.TFrame", background=p.surface, bordercolor=p.border,
                        relief="solid", borderwidth=1)

        style.configure("TLabel", background=p.bg, foreground=p.fg)
        style.configure("Header.TLabel", font=(FONT_FAMILY, 17, "bold"))
        style.configure("SubHeader.TLabel", foreground=p.muted, font=(FONT_FAMILY, 10))
        style.configure("CardTitle.TLabel", background=p.surface, foreground=p.muted,
                        font=(FONT_FAMILY, 9, "bold"))
        style.configure("StatusKey.TLabel", background=p.surface, foreground=p.muted,
                        font=(FONT_FAMILY, 10))
        style.configure("StatusVal.TLabel", background=p.surface, foreground=p.fg,
                        font=(FONT_FAMILY, 10, "bold"))
        style.configure("Footer.TLabel", foreground=p.muted, font=(FONT_FAMILY, 9, "italic"))

        # Flat primary button
        style.configure("Primary.TButton", background=p.accent, foreground=p.accent_fg,
                        bordercolor=p.accent, lightcolor=p.accent, darkcolor=p.accent,
                        focuscolor=p.accent, relief="flat", borderwidth=0,
                        font=(FONT_FAMILY, 11, "bold"), padding=(16, 12))
        style.map("Primary.TButton",
                  background=[("disabled", p.button), ("active", p.accent_hover)],
                  foreground=[("disabled", p.muted)],
                  bordercolor=[("active", p.accent_hover)],
                  lightcolor=[("active", p.accent_hover)],
                  darkcolor=[("active", p.accent_hover)])

        # Flat secondary button
        style.configure("Secondary.TButton", background=p.button, foreground=p.fg,
                        bordercolor=p.button, lightcolor=p.button, darkcolor=p.button,
                        focuscolor=p.button, relief="flat", borderwidth=0,
                        font=(FONT_FAMILY, 10), padding=(10, 8))
        style.map("Secondary.TButton",
                  background=[("disabled", p.surface), ("active", p.button_hover)],
                  foreground=[("disabled", p.muted)],
                  bordercolor=[("active", p.button_hover)],
                  lightcolor=[("active", p.button_hover)],
                  darkcolor=[("active", p.button_hover)])

        # Text-like link button for the footer
        style.configure("Link.TButton", background=p.bg, foreground=p.accent,
                        bordercolor=p.bg, lightcolor=p.bg, darkcolor=p.bg,
                        focuscolor=p.bg, relief="flat", borderwidth=0,
                        font=(FONT_FAMILY, 9, "underline"), padding=(0, 4))
        style.map("Link.TButton",
                  background=[("active", p.bg)],
                  foreground=[("active", p.accent_hover)])

        style.configure("Vertical.TScrollbar", background=p.button, troughcolor=p.log_bg,
                        bordercolor=p.log_bg, lightcolor=p.button, darkcolor=p.button,
                        arrowcolor=p.muted, relief="flat")
        style.map("Vertical.TScrollbar", background=[("active", p.button_hover)])

        style.configure("Horizontal.TProgressbar", background=p.accent, troughcolor=p.surface,
                        bordercolor=p.border, lightcolor=p.accent, darkcolor=p.accent,
                        relief="flat")

    # ---------------------------------------------------------------------- UI
    def _card(self, parent: "ttk.Frame", title: str) -> "ttk.Frame":
        """Create a flat card with a small uppercase title and return its body."""
        card = ttk.Frame(parent, style="Card.TFrame", padding=14)
        card.pack(fill="x", pady=6)
        ttk.Label(card, text=title.upper(), style="CardTitle.TLabel").pack(anchor="w", pady=(0, 8))
        body = ttk.Frame(card, style="Card.TFrame", padding=0)
        body.configure(borderwidth=0)
        body.pack(fill="x")
        return body

    def _build_ui(self) -> None:
        p = self.palette
        main_frame = ttk.Frame(self.root, padding=20)
        main_frame.pack(fill="both", expand=True)

        # 1. Header
        header = ttk.Frame(main_frame)
        header.pack(fill="x", pady=(0, 12))
        ttk.Label(header, text="🌌 GC-ChatSync", style="Header.TLabel").pack(anchor="w")
        ttk.Label(header, text="Google Drive Synchronization for Google Antigravity",
                  style="SubHeader.TLabel").pack(anchor="w")

        # 2. Status card
        status_body = self._card(main_frame, "Synchronization Status")
        status_body.columnconfigure(1, weight=1)
        rows = [
            ("This Computer", "lbl_device", self.config.device_id),
            ("Google Drive", "lbl_drive_status", "Checking connection..."),
            ("Last Cloud Sync", "lbl_cloud_last", "--"),
            ("Alignment", "lbl_alignment", "--"),
        ]
        for i, (key, attr, value) in enumerate(rows):
            ttk.Label(status_body, text=key, style="StatusKey.TLabel").grid(
                row=i, column=0, sticky="w", pady=3, padx=(0, 16))
            lbl = ttk.Label(status_body, text=value, style="StatusVal.TLabel")
            lbl.grid(row=i, column=1, sticky="w", pady=3)
            setattr(self, attr, lbl)

        # 3. Actions
        actions = ttk.Frame(main_frame)
        actions.pack(fill="x", pady=(12, 6))

        self.btn_run = ttk.Button(actions, text="🚀  Launch Antigravity (Auto-Sync)",
                                  style="Primary.TButton", command=self.on_run_clicked,
                                  cursor="hand2")
        self.btn_run.pack(fill="x")

        self.btn_bypass = ttk.Button(actions, text="⚡  Bypass Sync & Launch Directly",
                                     style="Secondary.TButton", command=self.on_bypass_clicked,
                                     cursor="hand2")
        self.btn_bypass.pack(fill="x", pady=(6, 0))

        secondary = ttk.Frame(actions)
        secondary.pack(fill="x", pady=(8, 0))
        secondary.columnconfigure((0, 1, 2), weight=1, uniform="btn")

        self.btn_pull = ttk.Button(secondary, text="🔄  Force Pull", style="Secondary.TButton",
                                   command=self.on_pull_clicked, cursor="hand2")
        self.btn_push = ttk.Button(secondary, text="⬆️  Force Push", style="Secondary.TButton",
                                   command=self.on_push_clicked, cursor="hand2")
        self.btn_refresh = ttk.Button(secondary, text="🔍  Refresh Status", style="Secondary.TButton",
                                      command=self.refresh_status_async, cursor="hand2")
        self.btn_pull.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.btn_push.grid(row=0, column=1, sticky="ew", padx=4)
        self.btn_refresh.grid(row=0, column=2, sticky="ew", padx=(4, 0))

        # 3b. Visual progress section
        self.progress_frame = ttk.Frame(main_frame)
        self.progress_frame.pack(fill="x", pady=(10, 4))

        self.lbl_progress_step = ttk.Label(self.progress_frame, text="", style="StatusKey.TLabel", font=(FONT_FAMILY, 9))
        self.lbl_progress_step.pack(anchor="w", pady=(0, 3))

        self.progress_bar = ttk.Progressbar(self.progress_frame, orient="horizontal", mode="determinate", maximum=100)
        self.progress_bar.pack(fill="x")
        self.progress_bar["value"] = 0

        # 4. Activity log
        ttk.Label(main_frame, text="ACTIVITY LOG", style="SubHeader.TLabel",
                  font=(FONT_FAMILY, 9, "bold")).pack(anchor="w", pady=(14, 4))
        log_frame = tk.Frame(main_frame, bg=p.log_bg, highlightthickness=1,
                             highlightbackground=p.border, highlightcolor=p.border)
        log_frame.pack(fill="both", expand=True)

        self.log_text = tk.Text(
            log_frame, height=11, wrap="word", font=(MONO_FAMILY, 9), state="disabled",
            bg=p.log_bg, fg=p.log_fg, insertbackground=p.log_fg, relief="flat",
            borderwidth=0, highlightthickness=0, padx=10, pady=8,
            selectbackground=p.accent, selectforeground=p.accent_fg,
        )
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)

        self.log_handler = LogTextHandler(self.log_text)
        self.log_handler.setLevel(logging.INFO)
        self.log_handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S"))
        logging.getLogger().addHandler(self.log_handler)

        # 5. Footer
        footer = ttk.Frame(main_frame)
        footer.pack(fill="x", pady=(8, 0))
        ttk.Button(footer, text="📄 View Log File", style="Link.TButton",
                   command=self.open_log_file, cursor="hand2").pack(side="left")
        self.lbl_status_bar = ttk.Label(footer, text="Ready.", style="Footer.TLabel")
        self.lbl_status_bar.pack(side="right")

    # ----------------------------------------------------------------- helpers
    def _on_close(self) -> None:
        logging.getLogger().removeHandler(self.log_handler)
        self.root.destroy()

    def _set_buttons_state(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for btn in (self.btn_run, self.btn_bypass, self.btn_pull, self.btn_push, self.btn_refresh):
            btn.configure(state=state)

    def refresh_status_async(self) -> None:
        """Fetch status in a background thread."""
        self.lbl_status_bar.configure(text="Checking Google Drive status...")

        def worker() -> None:
            status = self.runner.get_status()
            self.root.after(0, lambda: self._apply_status(status))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_status(self, status: SyncStatus) -> None:
        p = self.palette
        if status.drive_connected:
            self.lbl_drive_status.configure(text="● Connected", foreground=p.success)
            if status.cloud_last_device:
                when = (status.cloud_last_sync_utc or "")[:16].replace("T", " ")
                epoch_str = f" [rev {status.cloud_epoch}]" if status.cloud_epoch else ""
                self.lbl_cloud_last.configure(text=f"{status.cloud_last_device}{epoch_str}  ·  {when} UTC")
            else:
                self.lbl_cloud_last.configure(text="No previous cloud sync")

            if status.active_device and status.active_device != self.runner.config.device_id:
                when_active = (status.active_since_utc or "")[:16].replace("T", " ")
                self.lbl_alignment.configure(text=f"⚠️ {status.active_device} active since {when_active} UTC (previous session)", foreground=p.warning)
            elif status.session_dirty:
                self.lbl_alignment.configure(text="⚠️ Pending push from previous session (click Force Push)", foreground=p.warning)
            elif status.cloud_last_device is None:
                self.lbl_alignment.configure(text="● Cloud empty (waiting for initial push)", foreground=p.fg_subtle)
            elif status.in_sync:
                self.lbl_alignment.configure(text="● In sync with cloud", foreground=p.success)
            else:
                self.lbl_alignment.configure(text=f"● Updates available from {status.cloud_last_device}", foreground=p.warning)
        else:
            self.lbl_drive_status.configure(text="● Unreachable (check rclone config)", foreground=p.error)
            self.lbl_cloud_last.configure(text="--")
            self.lbl_alignment.configure(text="--", foreground=p.fg)

        self.lbl_status_bar.configure(text="Ready.")

    def _set_progress(self, percent: int, description: str) -> None:
        """Update progress bar value and status text on the GUI."""
        self.progress_bar["value"] = percent
        if description:
            self.lbl_progress_step.configure(text=f"{description} ({percent}%)")
            self.lbl_status_bar.configure(text=description)
        else:
            self.lbl_progress_step.configure(text="")

    def _run_in_background(self, busy_text: str, task, on_success_msg: str | None, error_title: str) -> None:
        """Disable buttons, run `task` in a thread, report the result, refresh status."""
        self._set_buttons_state(False)
        self.lbl_status_bar.configure(text=busy_text)
        self._set_progress(10, busy_text)

        def worker() -> None:
            try:
                def on_progress(pct: int, msg: str) -> None:
                    self.root.after(0, lambda: self._set_progress(pct, msg))

                result = task(on_progress)
                if on_success_msg and result is not False:
                    self.root.after(0, lambda: self._set_progress(100, on_success_msg))
                    logger.info(on_success_msg)
                    self.root.after(0, lambda: messagebox.showinfo("GC-ChatSync", on_success_msg))
                elif result is False:
                    self.root.after(0, lambda: self._set_progress(0, "Operation could not be completed."))
                    logger.warning("Operation failed: check connection and log file.")
            except Exception as e:
                self.root.after(0, lambda: self._set_progress(0, f"Error: {e}"))
                logger.error(f"{error_title}: {e}", exc_info=True)
                self.root.after(0, lambda: messagebox.showerror(error_title, str(e)))
            finally:
                self.is_running = False
                self.root.after(0, lambda: self._set_buttons_state(True))
                self.root.after(0, self.refresh_status_async)

        threading.Thread(target=worker, daemon=True).start()

    # ----------------------------------------------------------------- actions
    def on_run_clicked(self) -> None:
        """Execute the full lifecycle: Pull -> Launch (auto-hide GUI) -> Push (auto-close GUI)."""
        if self.is_running:
            return

        self.is_running = True
        self._set_buttons_state(False)
        self._set_progress(5, "Checking cloud status and active sessions...")

        def lifecycle_worker() -> None:
            cancel_witness = threading.Event()
            witness_thread = None

            try:
                def update_progress(pct: int, msg: str) -> None:
                    self.root.after(0, lambda: self._set_progress(pct, msg))

                # 1. Check if another device has an unclosed active session (in background thread)
                try:
                    manifest = self.runner.drive.get_manifest()
                    if manifest and manifest.active_device and manifest.active_device != self.runner.config.device_id:
                        when_str = (manifest.active_since_utc or "")[:16].replace("T", " ")
                        proceed_event = threading.Event()
                        proceed_answer = [False]

                        def ask_dialog() -> None:
                            proceed_answer[0] = messagebox.askyesno(
                                "GC-ChatSync: Active Session Detected",
                                f"Warning: Device '{manifest.active_device}' appears still active since {when_str} UTC.\n\n"
                                "If the other computer did not close gracefully, its latest changes might not be on Google Drive.\n\n"
                                "Do you want to continue and launch anyway?",
                            )
                            proceed_event.set()

                        self.root.after(0, ask_dialog)
                        proceed_event.wait()
                        if not proceed_answer[0]:
                            self.root.after(0, lambda: self._set_buttons_state(True))
                            self.root.after(0, lambda: self._set_progress(0, "Launch cancelled by user."))
                            return
                except Exception as e:
                    logger.debug(f"Cloud session check skipped: {e}")

                # 2. PULL
                pull_ok = False
                try:
                    pull_ok = self.runner.pull(progress_cb=update_progress)
                except DatabaseMergeError as e:
                    logger.critical(f"Schema conflict detected: {e}")
                    def show_conflict() -> None:
                        self.root.deiconify()
                        self._set_progress(0, "Launch halted: schema conflict.")
                        messagebox.showerror(
                            "GC-ChatSync: Schema Conflict",
                            f"Synchronization stopped for safety:\n\n{e}\n\n"
                            "To prevent conversation or metadata loss in the cloud, "
                            "please update Antigravity to the same version on all devices.",
                        )
                        self._set_buttons_state(True)
                        self.refresh_status_async()

                    self.root.after(0, show_conflict)
                    return
                except Exception as e:
                    logger.warning(f"Pull failed due to network error ({e}). Proceeding in offline mode.")
                    pull_ok = False

                if not pull_ok:
                    # Offline mode: launch Antigravity, but skip push upon close
                    self.root.after(0, lambda: self._set_progress(50, "Offline mode: launching Antigravity..."))
                    def on_app_started() -> None:
                        self.root.after(0, self.root.withdraw)

                    self.runner.launch_app(on_launched=on_app_started)
                    self.root.after(0, self.root.destroy)
                    return

                # Online mode: claim active witness in a synchronized thread
                mark_session_dirty(self.runner.config.device_id)

                def claim_witness_worker() -> None:
                    try:
                        if cancel_witness.is_set():
                            return
                        curr_m = self.runner.drive.get_manifest()
                        if cancel_witness.is_set():
                            return
                        curr_epoch = curr_m.epoch if curr_m else 1
                        curr_last = curr_m.last_device if curr_m else ""
                        witness_manifest = SyncManifest(
                            last_device=curr_last,
                            last_sync_utc=curr_m.last_sync_utc if curr_m else "",
                            epoch=curr_epoch,
                            active_device=self.runner.config.device_id,
                            active_since_utc=datetime.now(timezone.utc).isoformat(),
                            antigravity_version=get_antigravity_version(self.runner.config.antigravity_bin_path),
                        )
                        if not cancel_witness.is_set():
                            self.runner.drive.put_manifest(witness_manifest)
                    except Exception as err:
                        logger.debug(f"Could not claim active witness: {err}")

                witness_thread = threading.Thread(target=claim_witness_worker, daemon=False)
                witness_thread.start()

                # 3. LAUNCH APP AND HIDE GUI ONLY AFTER APP IS SPAWNED
                self.root.after(0, lambda: self._set_progress(100, "Launching Antigravity..."))
                def on_app_started() -> None:
                    self.root.after(0, self.root.withdraw)

                self.runner.launch_app(on_launched=on_app_started)

                # 4. APP CLOSED: CANCEL PENDING WITNESS AND WAIT FOR IT
                cancel_witness.set()
                if witness_thread and witness_thread.is_alive():
                    witness_thread.join(timeout=3)

                # 5. RESTORE GUI TO SHOW PUSH PROGRESS
                def show_push_status() -> None:
                    self.root.deiconify()
                    self._set_progress(10, "Saving and uploading to Google Drive...")

                self.root.after(0, show_push_status)

                # 6. PUSH
                push_ok = self.runner.push(progress_cb=update_progress)

                if push_ok:
                    def close_success() -> None:
                        self._set_progress(100, "Synchronization completed! Closing...")
                        self.root.after(800, self.root.destroy)

                    self.root.after(0, close_success)
                else:
                    def show_warning() -> None:
                        self.root.deiconify()
                        self._set_progress(0, "Upload postponed.")
                        messagebox.showwarning(
                            "GC-ChatSync",
                            "Google Drive upload was postponed (Drive unreachable or database busy).\n"
                            "Local data remains intact and will be uploaded in your next session.",
                        )
                        self._set_buttons_state(True)
                        self.refresh_status_async()

                    self.root.after(0, show_warning)

            except Exception as e:
                logger.error(f"Error during execution lifecycle: {e}", exc_info=True)

                def show_err() -> None:
                    self.root.deiconify()
                    self._set_progress(0, f"Error: {e}")
                    messagebox.showerror("GC-ChatSync Error", str(e))
                    self._set_buttons_state(True)
                    self.refresh_status_async()

                self.root.after(0, show_err)
            finally:
                self.is_running = False

        threading.Thread(target=lifecycle_worker, daemon=True).start()

    def on_bypass_clicked(self) -> None:
        """Launch Antigravity directly, hiding GUI only after launch and closing automatically on exit."""
        if self.is_running:
            return
        self.is_running = True
        self._set_buttons_state(False)
        self.lbl_status_bar.configure(text="Directly launching Antigravity...")
        self._set_progress(50, "Directly launching Antigravity...")

        def bypass_worker() -> None:
            try:
                def on_app_started() -> None:
                    self.root.after(0, self.root.withdraw)

                self.runner.launch_app(on_launched=on_app_started)
                self.root.after(0, self.root.destroy)
            except Exception as e:
                logger.error(f"Direct launch error: {e}", exc_info=True)

                def show_err() -> None:
                    self.root.deiconify()
                    self._set_progress(0, f"Launch Error: {e}")
                    messagebox.showerror("Launch Error", str(e))
                    self._set_buttons_state(True)
                    self.refresh_status_async()

                self.root.after(0, show_err)
            finally:
                self.is_running = False

        threading.Thread(target=bypass_worker, daemon=True).start()

    def on_pull_clicked(self) -> None:
        """Execute a manual pull with the force flag."""
        self._run_in_background(
            "Downloading from Google Drive...",
            lambda cb: self.runner.pull(force=True, progress_cb=cb),
            "Synchronization from Google Drive completed!",
            "Pull Error",
        )

    def on_push_clicked(self) -> None:
        """Execute a manual push."""
        self._run_in_background(
            "Uploading to Google Drive...",
            lambda cb: self.runner.push(progress_cb=cb),
            "Upload to Google Drive completed!",
            "Push Error",
        )

    def open_log_file(self) -> None:
        """Open the log file using the default system application."""
        log_path = get_log_file_path()
        if not log_path.is_file():
            messagebox.showinfo("GC-ChatSync", "No log file found yet.")
            return

        sys_name = get_system_name()
        try:
            if sys_name == "windows":
                os.startfile(str(log_path))  # type: ignore[attr-defined]
            elif sys_name == "darwin":
                subprocess.Popen(["open", str(log_path)])
            else:
                subprocess.Popen(["xdg-open", str(log_path)])
        except OSError as e:
            messagebox.showwarning("GC-ChatSync", f"Could not open log file: {e}")


def launch_gui() -> int:
    """Entrypoint to launch the Tkinter GUI."""
    if tk is None:
        print("\n❌ Error: Tkinter is not installed in this Python environment.")
        print("On Linux run: sudo apt install python3-tk\n")
        return 1

    root = tk.Tk()
    ChatSyncGUI(root)
    root.mainloop()
    return 0
