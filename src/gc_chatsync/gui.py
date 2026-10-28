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
from gc_chatsync.drive import SyncManifest
from gc_chatsync.lock import mark_session_dirty
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
        self.root.geometry("600x600")
        self.root.minsize(540, 520)
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
        ttk.Label(header, text="Sincronizzazione Google Drive per Google Antigravity",
                  style="SubHeader.TLabel").pack(anchor="w")

        # 2. Status card
        status_body = self._card(main_frame, "Stato sincronizzazione")
        status_body.columnconfigure(1, weight=1)
        rows = [
            ("Questo computer", "lbl_device", self.config.device_id),
            ("Google Drive", "lbl_drive_status", "Verifica in corso..."),
            ("Ultimo nel cloud", "lbl_cloud_last", "--"),
            ("Allineamento", "lbl_alignment", "--"),
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

        self.btn_run = ttk.Button(actions, text="🚀  Avvia Antigravity (sync automatico)",
                                  style="Primary.TButton", command=self.on_run_clicked,
                                  cursor="hand2")
        self.btn_run.pack(fill="x")

        self.btn_bypass = ttk.Button(actions, text="⚡  Bypassa Sync e Avvia normalmente",
                                     style="Secondary.TButton", command=self.on_bypass_clicked,
                                     cursor="hand2")
        self.btn_bypass.pack(fill="x", pady=(6, 0))

        secondary = ttk.Frame(actions)
        secondary.pack(fill="x", pady=(8, 0))
        secondary.columnconfigure((0, 1, 2), weight=1, uniform="btn")

        self.btn_pull = ttk.Button(secondary, text="🔄  Riprova / Pull", style="Secondary.TButton",
                                   command=self.on_pull_clicked, cursor="hand2")
        self.btn_push = ttk.Button(secondary, text="⬆️  Forza Push", style="Secondary.TButton",
                                   command=self.on_push_clicked, cursor="hand2")
        self.btn_refresh = ttk.Button(secondary, text="🔍  Aggiorna stato", style="Secondary.TButton",
                                      command=self.refresh_status_async, cursor="hand2")
        self.btn_pull.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.btn_push.grid(row=0, column=1, sticky="ew", padx=4)
        self.btn_refresh.grid(row=0, column=2, sticky="ew", padx=(4, 0))

        # 4. Activity log
        ttk.Label(main_frame, text="REGISTRO ATTIVITÀ", style="SubHeader.TLabel",
                  font=(FONT_FAMILY, 9, "bold")).pack(anchor="w", pady=(14, 4))
        log_frame = tk.Frame(main_frame, bg=p.log_bg, highlightthickness=1,
                             highlightbackground=p.border, highlightcolor=p.border)
        log_frame.pack(fill="both", expand=True)

        self.log_text = tk.Text(
            log_frame, height=8, wrap="word", font=(MONO_FAMILY, 9), state="disabled",
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
        ttk.Button(footer, text="📄 Apri file di log", style="Link.TButton",
                   command=self.open_log_file, cursor="hand2").pack(side="left")
        self.lbl_status_bar = ttk.Label(footer, text="Pronto.", style="Footer.TLabel")
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
        self.lbl_status_bar.configure(text="Controllo stato Google Drive...")

        def worker() -> None:
            status = self.runner.get_status()
            self.root.after(0, lambda: self._apply_status(status))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_status(self, status: SyncStatus) -> None:
        p = self.palette
        if status.drive_connected:
            self.lbl_drive_status.configure(text="● Collegato", foreground=p.success)
            if status.cloud_last_device:
                when = (status.cloud_last_sync_utc or "")[:16].replace("T", " ")
                epoch_str = f" [rev {status.cloud_epoch}]" if status.cloud_epoch else ""
                self.lbl_cloud_last.configure(text=f"{status.cloud_last_device}{epoch_str}  ·  {when} UTC")
            else:
                self.lbl_cloud_last.configure(text="Nessuna sincronizzazione precedente")

            if status.active_device and status.active_device != self.runner.config.device_id:
                when_active = (status.active_since_utc or "")[:16].replace("T", " ")
                self.lbl_alignment.configure(text=f"⚠️ {status.active_device} attivo dal {when_active} UTC (sessione precedente)", foreground=p.warning)
            elif status.session_dirty:
                self.lbl_alignment.configure(text="⚠️ Push pendente da sessione precedente (premi Forza Push)", foreground=p.warning)
            elif status.cloud_last_device is None:
                self.lbl_alignment.configure(text="● Cloud vuoto (in attesa del primo push da una macchina)", foreground=p.fg_subtle)
            elif status.in_sync:
                self.lbl_alignment.configure(text="● Allineato con il cloud", foreground=p.success)
            else:
                self.lbl_alignment.configure(text=f"● Aggiornamenti disponibili da {status.cloud_last_device}", foreground=p.warning)
        else:
            self.lbl_drive_status.configure(text="● Non raggiungibile (verifica rclone)", foreground=p.error)
            self.lbl_cloud_last.configure(text="--")
            self.lbl_alignment.configure(text="--", foreground=p.fg)

        self.lbl_status_bar.configure(text="Pronto.")

    def _run_in_background(self, busy_text: str, task, on_success_msg: str | None, error_title: str) -> None:
        """Disable buttons, run `task` in a thread, report the result, refresh status."""
        self._set_buttons_state(False)
        self.lbl_status_bar.configure(text=busy_text)

        def worker() -> None:
            try:
                result = task()
                if on_success_msg and result is not False:
                    logger.info(on_success_msg)
                    self.root.after(0, lambda: messagebox.showinfo("GC-ChatSync", on_success_msg))
                elif result is False:
                    logger.warning("Operazione non riuscita: controlla la connessione e il file di log.")
            except Exception as e:
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

        # Check if another device has an unclosed active session
        try:
            status = self.runner.get_status()
            if status.active_device and status.active_device != self.runner.config.device_id:
                when_str = (status.active_since_utc or "")[:16].replace("T", " ")
                proceed = messagebox.askyesno(
                    "GC-ChatSync: Sessione attiva rilevata",
                    f"Attenzione: Il dispositivo '{status.active_device}' risulta ancora attivo dal {when_str} UTC.\n\n"
                    "Se l'altra macchina non ha chiuso normalmente, le sue ultime modifiche potrebbero non essere su Google Drive.\n\n"
                    "Vuoi continuare e avviare comunque?",
                )
                if not proceed:
                    return
        except Exception:
            pass

        self.is_running = True
        self._set_buttons_state(False)
        self.lbl_status_bar.configure(text="Sincronizzazione da Google Drive in corso...")

        def lifecycle_worker() -> None:
            try:
                # 1. PULL (proceed in offline mode if pull fails)
                try:
                    self.runner.pull()
                except Exception as e:
                    logger.warning(f"Pull could not complete ({e}). Proceeding in offline mode.")

                self.root.after(0, lambda: self.lbl_status_bar.configure(text="Avvio di Antigravity in corso..."))

                # Mark session dirty before launching the editor
                mark_session_dirty(self.runner.config.device_id)

                # Claim active witness asynchronously so Drive latency never delays launching Antigravity
                def claim_witness_async() -> None:
                    try:
                        manifest = self.runner.drive.get_manifest()
                        curr_epoch = manifest.epoch if manifest else 1
                        curr_last = manifest.last_device if manifest else ""
                        witness_manifest = SyncManifest(
                            last_device=curr_last,
                            last_sync_utc=manifest.last_sync_utc if manifest else "",
                            epoch=curr_epoch,
                            active_device=self.runner.config.device_id,
                            active_since_utc=datetime.now(timezone.utc).isoformat(),
                            antigravity_version=get_antigravity_version(self.runner.config.antigravity_bin_path),
                        )
                        self.runner.drive.put_manifest(witness_manifest)
                    except Exception as err:
                        logger.debug(f"Could not claim active witness: {err}")

                threading.Thread(target=claim_witness_async, daemon=True).start()

                # 2. HIDE GUI ONLY AFTER ANTIGRAVITY IS ACTUALLY SPAWNED
                def on_app_started() -> None:
                    self.root.after(0, self.root.withdraw)

                # Wait for Antigravity to run and exit
                self.runner.launch_app(on_launched=on_app_started)

                # 3. RESTORE GUI TO SHOW PUSH PROGRESS
                def show_push_status() -> None:
                    self.root.deiconify()
                    self.lbl_status_bar.configure(text="Salvataggio e caricamento su Google Drive in corso...")

                self.root.after(0, show_push_status)

                # 4. PUSH
                push_ok = self.runner.push()

                if push_ok:
                    def close_success() -> None:
                        self.lbl_status_bar.configure(text="Sincronizzazione completata! Chiusura...")
                        self.root.after(800, self.root.destroy)

                    self.root.after(0, close_success)
                else:
                    def show_warning() -> None:
                        self.root.deiconify()
                        messagebox.showwarning(
                            "GC-ChatSync",
                            "Il caricamento su Drive è stato rimandato (Drive non raggiungibile o database occupato).\n"
                            "I dati locali rimangono intatti e verranno caricati alla prossima sessione.",
                        )
                        self._set_buttons_state(True)
                        self.refresh_status_async()

                    self.root.after(0, show_warning)

            except Exception as e:
                logger.error(f"Errore nel ciclo di esecuzione: {e}", exc_info=True)

                def show_err() -> None:
                    self.root.deiconify()
                    messagebox.showerror("Errore GC-ChatSync", str(e))
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
        self.lbl_status_bar.configure(text="Avvio diretto di Antigravity...")

        def bypass_worker() -> None:
            try:
                def on_app_started() -> None:
                    self.root.after(0, self.root.withdraw)

                self.runner.launch_app(on_launched=on_app_started)
                self.root.after(0, self.root.destroy)
            except Exception as e:
                logger.error(f"Errore avvio diretto: {e}", exc_info=True)

                def show_err() -> None:
                    self.root.deiconify()
                    messagebox.showerror("Errore Avvio", str(e))
                    self._set_buttons_state(True)
                    self.refresh_status_async()

                self.root.after(0, show_err)
            finally:
                self.is_running = False

        threading.Thread(target=bypass_worker, daemon=True).start()

    def on_pull_clicked(self) -> None:
        """Execute a manual pull with the force flag."""
        self._run_in_background("Download da Google Drive in corso...",
                                lambda: self.runner.pull(force=True),
                                "Sincronizzazione da Google Drive completata!", "Errore Pull")

    def on_push_clicked(self) -> None:
        """Execute a manual push."""
        self._run_in_background("Caricamento su Google Drive in corso...", self.runner.push,
                                "Caricamento su Google Drive completato!", "Errore Push")

    def open_log_file(self) -> None:
        """Open the log file using the default system application."""
        log_path = get_log_file_path()
        if not log_path.is_file():
            messagebox.showinfo("GC-ChatSync", "Nessun file di log trovato al momento.")
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
            messagebox.showwarning("GC-ChatSync", f"Impossibile aprire il file di log: {e}")


def launch_gui() -> int:
    """Entrypoint to launch the Tkinter GUI."""
    if tk is None:
        print("\n❌ Errore: Tkinter non è installato in questo ambiente Python.")
        print("Su Linux esegui: sudo apt install python3-tk\n")
        return 1

    root = tk.Tk()
    ChatSyncGUI(root)
    root.mainloop()
    return 0
