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

from pathlib import Path

from gc_chatsync import __version__
from gc_chatsync.config import (
    AppConfig,
    get_log_file_path,
    get_system_name,
    load_config,
)
from gc_chatsync.drive import RcloneError
from gc_chatsync.lock import clear_session_dirty, is_session_dirty, mark_session_dirty
from gc_chatsync.merger import DatabaseMergeError
from gc_chatsync.runner import SyncRunner, SyncStatus
from gc_chatsync.theme import DARK, LIGHT, Palette, get_palette

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
        level_tag = "INFO"
        if record.levelno >= logging.ERROR:
            level_tag = "ERROR"
        elif record.levelno >= logging.WARNING:
            level_tag = "WARN"

        def append() -> None:
            try:
                self.text_widget.configure(state="normal")
                self.text_widget.insert("end", msg + "\n", (level_tag,))
                self.text_widget.see("end")
                self.text_widget.configure(state="disabled")
            except tk.TclError:
                # Widget already destroyed (window closed while a worker was logging)
                pass

        try:
            self.text_widget.after(0, append)
        except (RuntimeError, tk.TclError):
            pass


def _draw_rounded_rect(
    canvas: "tk.Canvas",
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    radius: float = 10,
    **kwargs,
) -> int:
    """Draw a smooth rounded rectangle on a Tkinter Canvas."""
    r = max(0.0, min(radius, (x2 - x1) / 2.0, (y2 - y1) / 2.0))
    points = [
        x1 + r, y1,
        x1 + r, y1,
        x2 - r, y1,
        x2 - r, y1,
        x2, y1,
        x2, y1 + r,
        x2, y1 + r,
        x2, y2 - r,
        x2, y2 - r,
        x2, y2,
        x2 - r, y2,
        x2 - r, y2,
        x1 + r, y2,
        x1 + r, y2,
        x1, y2,
        x1, y2 - r,
        x1, y2 - r,
        x1, y1 + r,
        x1, y1 + r,
        x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **kwargs)


class RoundedButton(tk.Canvas):  # type: ignore[misc]
    """Modern vector-drawn rounded button with hover and disabled states."""

    def __init__(
        self,
        parent: "tk.Widget",
        text: str,
        command,
        palette: Palette,
        primary: bool = False,
        height: int = 38,
        radius: int = 10,
        font_size: int = 10,
    ):
        parent_bg = palette.bg
        try:
            parent_bg = parent.cget("bg")
        except Exception:
            pass
        super().__init__(
            parent,
            height=height,
            bg=parent_bg,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self._text = text
        self._command = command
        self._palette = palette
        self._primary = primary
        self._height = height
        self._radius = radius
        self._font_size = font_size
        self._enabled = True
        self._hover = False

        self.bind("<Configure>", lambda _e: self._redraw())
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonRelease-1>", self._on_click)

    def set_palette(self, palette: Palette, parent_bg: str | None = None) -> None:
        self._palette = palette
        super().configure(bg=parent_bg or palette.bg)
        self._redraw()

    def configure(self, **kwargs) -> None:  # type: ignore[override]
        if "state" in kwargs:
            st = kwargs.pop("state")
            self._enabled = st != "disabled"
            super().configure(cursor="hand2" if self._enabled else "arrow")
        if "text" in kwargs:
            self._text = kwargs.pop("text")
        if kwargs:
            super().configure(**kwargs)
        self._redraw()

    config = configure

    def _on_enter(self, _event=None) -> None:
        if self._enabled:
            self._hover = True
            self._redraw()

    def _on_leave(self, _event=None) -> None:
        if self._hover:
            self._hover = False
            self._redraw()

    def _on_click(self, event=None) -> None:
        if not self._enabled or not callable(self._command):
            return
        if event is not None:
            w = self.winfo_width()
            h = self.winfo_height()
            if not (0 <= event.x <= w and 0 <= event.y <= h):
                return
        self._command()

    def _redraw(self) -> None:
        self.delete("all")
        w = max(self.winfo_width(), 20)
        h = max(self.winfo_height(), self._height)
        p = self._palette

        if not self._enabled:
            fill = p.surface_alt
            outline = p.border
            fg = p.muted
        elif self._primary:
            fill = p.accent_hover if self._hover else p.accent
            outline = fill
            fg = p.accent_fg
        else:
            fill = p.button_hover if self._hover else p.button
            outline = p.accent if self._hover else p.button_border
            fg = p.fg

        _draw_rounded_rect(
            self,
            1,
            1,
            w - 2,
            h - 2,
            radius=self._radius,
            fill=fill,
            outline=outline,
            width=1,
        )
        weight = "bold" if self._primary else "normal"
        self.create_text(
            w / 2,
            h / 2,
            text=self._text,
            fill=fg,
            font=(FONT_FAMILY, self._font_size, weight),
        )


class RoundedProgressBar(tk.Canvas):  # type: ignore[misc]
    """Smooth rounded progress bar with animated gradient-like accent fill."""

    def __init__(self, parent: "tk.Widget", palette: Palette, height: int = 10, radius: int = 5):
        super().__init__(
            parent,
            height=height,
            bg=palette.bg,
            highlightthickness=0,
            bd=0,
        )
        self._palette = palette
        self._height = height
        self._radius = radius
        self._value = 0
        self.bind("<Configure>", lambda _e: self._redraw())

    def set_palette(self, palette: Palette) -> None:
        self._palette = palette
        super().configure(bg=palette.bg)
        self._redraw()

    def __setitem__(self, key: str, val: int) -> None:
        if key == "value":
            self._value = max(0, min(100, int(val)))
            self._redraw()

    def __getitem__(self, key: str) -> int:
        if key == "value":
            return self._value
        raise KeyError(key)

    def _redraw(self) -> None:
        self.delete("all")
        w = max(self.winfo_width(), 20)
        h = max(self.winfo_height(), self._height)
        p = self._palette

        # Track
        _draw_rounded_rect(
            self,
            1,
            1,
            w - 2,
            h - 2,
            radius=self._radius,
            fill=p.surface,
            outline=p.border,
            width=1,
        )
        # Fill
        if self._value > 0:
            fill_w = max(self._radius * 2, int((w - 4) * (self._value / 100.0)))
            _draw_rounded_rect(
                self,
                2,
                2,
                2 + fill_w,
                h - 2,
                radius=max(2, self._radius - 1),
                fill=p.accent,
                outline="",
                width=0,
            )


class ChatSyncGUI:
    """Tkinter-based management dashboard with modern vector-styled controls."""

    def __init__(self, root: "tk.Tk", palette: Palette | None = None):
        self.root = root
        self.palette = palette or get_palette()
        self.root.title(f"GC-ChatSync v{__version__}")
        self.root.geometry("700x820")
        self.root.minsize(650, 740)
        self.root.configure(bg=self.palette.bg)

        self.config: AppConfig = load_config()
        self.runner: SyncRunner = SyncRunner(self.config)
        self.is_running = False
        self._last_status: SyncStatus | None = None
        self._rounded_buttons: list[RoundedButton] = []

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
        style.configure("Surface.TFrame", background=p.surface)

        style.configure("TLabel", background=p.bg, foreground=p.fg)
        style.configure("Header.TLabel", font=(FONT_FAMILY, 18, "bold"))
        style.configure("SubHeader.TLabel", foreground=p.muted, font=(FONT_FAMILY, 10))
        style.configure("SectionTitle.TLabel", background=p.bg, foreground=p.muted,
                        font=(FONT_FAMILY, 9, "bold"))
        style.configure("CardTitle.TLabel", background=p.surface, foreground=p.muted,
                        font=(FONT_FAMILY, 9, "bold"))
        style.configure("StatusKey.TLabel", background=p.surface, foreground=p.muted,
                        font=(FONT_FAMILY, 10))
        style.configure("StatusVal.TLabel", background=p.surface, foreground=p.fg,
                        font=(FONT_FAMILY, 10, "bold"))
        style.configure("Footer.TLabel", foreground=p.muted, font=(FONT_FAMILY, 9, "italic"))

        # Text-like link button for the footer and log header
        style.configure("Link.TButton", background=p.bg, foreground=p.accent,
                        bordercolor=p.bg, lightcolor=p.bg, darkcolor=p.bg,
                        focuscolor=p.bg, relief="flat", borderwidth=0,
                        font=(FONT_FAMILY, 9, "bold"), padding=(2, 2))
        style.map("Link.TButton",
                  background=[("active", p.bg)],
                  foreground=[("active", p.accent_hover)])

        style.configure("Vertical.TScrollbar", background=p.button, troughcolor=p.log_bg,
                        bordercolor=p.log_bg, lightcolor=p.button, darkcolor=p.button,
                        arrowcolor=p.muted, relief="flat")
        style.map("Vertical.TScrollbar", background=[("active", p.button_hover)])

    # ---------------------------------------------------------------------- UI
    def _card(self, parent: "ttk.Frame", title: str) -> "ttk.Frame":
        """Create a rounded surface card with an uppercase title and return its inner body."""
        p = self.palette
        card_canvas = tk.Canvas(
            parent,
            bg=p.bg,
            highlightthickness=0,
            bd=0,
        )
        card_canvas.pack(fill="x", pady=6)
        self._card_canvas = card_canvas

        inner = ttk.Frame(card_canvas, style="Surface.TFrame", padding=(18, 14, 18, 14))
        self._card_inner = inner

        header_row = ttk.Frame(inner, style="Surface.TFrame")
        header_row.pack(fill="x", pady=(0, 8))
        ttk.Label(header_row, text=title.upper(), style="CardTitle.TLabel").pack(side="left", anchor="w")

        # Subtle divider line inside card
        divider = tk.Frame(inner, bg=p.border, height=1, bd=0, highlightthickness=0)
        divider.pack(fill="x", pady=(0, 8))
        self._card_divider = divider

        body = ttk.Frame(inner, style="Surface.TFrame", padding=0)
        body.pack(fill="x")

        win_id = card_canvas.create_window(2, 2, window=inner, anchor="nw")

        def _sync_card(_event=None) -> None:
            cw = max(card_canvas.winfo_width(), 100)
            req_h = inner.winfo_reqheight() + 4
            if card_canvas.winfo_height() != req_h:
                card_canvas.configure(height=req_h)
            card_canvas.itemconfigure(win_id, width=cw - 4)
            card_canvas.delete("card_bg")
            _draw_rounded_rect(
                card_canvas,
                1,
                1,
                cw - 2,
                req_h - 2,
                radius=12,
                fill=self.palette.surface,
                outline=self.palette.border,
                width=1,
                tags=("card_bg",),
            )
            card_canvas.tag_lower("card_bg")

        card_canvas.bind("<Configure>", _sync_card)
        inner.bind("<Configure>", _sync_card)
        self._sync_card = _sync_card
        return body

    def _make_button(
        self,
        parent: "tk.Widget",
        text: str,
        command,
        primary: bool = False,
        height: int = 38,
        radius: int = 10,
        font_size: int = 10,
    ) -> RoundedButton:
        btn = RoundedButton(
            parent,
            text=text,
            command=command,
            palette=self.palette,
            primary=primary,
            height=height,
            radius=radius,
            font_size=font_size,
        )
        self._rounded_buttons.append(btn)
        return btn

    def _build_ui(self) -> None:
        p = self.palette
        main_frame = ttk.Frame(self.root, padding=(22, 18, 22, 16))
        main_frame.pack(fill="both", expand=True)
        self._main_frame = main_frame

        # 1. Header
        header = ttk.Frame(main_frame)
        header.pack(fill="x", pady=(0, 10))
        header_top = ttk.Frame(header)
        header_top.pack(fill="x")
        ttk.Label(header_top, text="🌌 GC-ChatSync", style="Header.TLabel").pack(side="left", anchor="w")

        header_right = ttk.Frame(header_top)
        header_right.pack(side="right", anchor="e")

        theme_icon = "☀️ Light" if p.name == DARK else "🌙 Dark"
        self.btn_theme = RoundedButton(
            header_right,
            text=theme_icon,
            command=self.toggle_theme,
            palette=p,
            primary=False,
            height=28,
            radius=8,
            font_size=9,
        )
        self.btn_theme.configure(width=76)
        self.btn_theme.pack(side="right", padx=(8, 0))

        self.lbl_version_pill = tk.Label(
            header_right,
            text=f"v{__version__}",
            bg=p.surface_alt,
            fg=p.muted,
            font=(MONO_FAMILY, 9, "bold"),
            padx=8,
            pady=3,
        )
        self.lbl_version_pill.pack(side="right", pady=2)

        ttk.Label(header, text="Google Drive Synchronization for Google Antigravity",
                  style="SubHeader.TLabel").pack(anchor="w", pady=(2, 0))

        # 2. Status card
        status_body = self._card(main_frame, "Synchronization Status")
        status_body.columnconfigure(1, weight=1)
        remote_target = f"{self.config.rclone_remote}:{self.config.remote_folder.strip('/')}"
        rows = [
            ("This Computer", "lbl_device", f"{self.config.device_id}  ({remote_target})"),
            ("Google Drive", "lbl_drive_status", "Checking connection..."),
            ("Last Cloud Sync", "lbl_cloud_last", "--"),
            ("Alignment", "lbl_alignment", "--"),
        ]
        self._status_key_labels: list[ttk.Label] = []
        for i, (key, attr, value) in enumerate(rows):
            k_lbl = ttk.Label(status_body, text=key, style="StatusKey.TLabel")
            k_lbl.grid(row=i, column=0, sticky="w", pady=4, padx=(0, 18))
            self._status_key_labels.append(k_lbl)
            lbl = ttk.Label(status_body, text=value, style="StatusVal.TLabel")
            lbl.grid(row=i, column=1, sticky="w", pady=4)
            setattr(self, attr, lbl)

        # 3. Actions
        actions = ttk.Frame(main_frame)
        actions.pack(fill="x", pady=(10, 4))

        self.btn_run = self._make_button(
            actions,
            text="🚀   Launch Antigravity  (Auto-Sync)",
            command=self.on_run_clicked,
            primary=True,
            height=46,
            radius=11,
            font_size=11,
        )
        self.btn_run.pack(fill="x")

        self.btn_bypass = self._make_button(
            actions,
            text="⚡   Bypass Sync & Launch Directly",
            command=self.on_bypass_clicked,
            primary=False,
            height=36,
            radius=9,
            font_size=10,
        )
        self.btn_bypass.pack(fill="x", pady=(6, 0))

        ttk.Label(actions, text="CLOUD OPERATIONS & MAINTENANCE", style="SectionTitle.TLabel").pack(
            anchor="w", pady=(12, 4)
        )

        secondary = ttk.Frame(actions)
        secondary.pack(fill="x")
        secondary.columnconfigure((0, 1, 2), weight=1, uniform="btn")

        self.btn_pull = self._make_button(secondary, text="🔄  Force Pull", command=self.on_pull_clicked, height=35, radius=9)
        self.btn_push = self._make_button(secondary, text="⬆️  Force Push", command=self.on_push_clicked, height=35, radius=9)
        self.btn_refresh = self._make_button(secondary, text="🔍  Refresh Status", command=self.refresh_status_async, height=35, radius=9)
        self.btn_pull.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.btn_push.grid(row=0, column=1, sticky="ew", padx=4)
        self.btn_refresh.grid(row=0, column=2, sticky="ew", padx=(4, 0))

        maint_row = ttk.Frame(actions)
        maint_row.pack(fill="x", pady=(6, 0))
        maint_row.columnconfigure((0, 1, 2), weight=1, uniform="maint_btn")

        self.btn_reconcile = self._make_button(maint_row, text="🩺  Check & Reconcile", command=self.on_reconcile_clicked, height=35, radius=9)
        self.btn_full_push = self._make_button(maint_row, text="📦  Full Push", command=self.on_full_push_clicked, height=35, radius=9)
        self.btn_restore = self._make_button(maint_row, text="⏪  Restore Backup", command=self.on_restore_backup_clicked, height=35, radius=9)
        self.btn_reconcile.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.btn_full_push.grid(row=0, column=1, sticky="ew", padx=4)
        self.btn_restore.grid(row=0, column=2, sticky="ew", padx=(4, 0))

        # 3b. Visual progress section
        self.progress_frame = ttk.Frame(main_frame)
        self.progress_frame.pack(fill="x", pady=(10, 2))

        self.lbl_progress_step = ttk.Label(self.progress_frame, text="Idle — ready to synchronize", style="SubHeader.TLabel", font=(FONT_FAMILY, 9))
        self.lbl_progress_step.pack(anchor="w", pady=(0, 4))

        self.progress_bar = RoundedProgressBar(self.progress_frame, palette=p, height=10, radius=5)
        self.progress_bar.pack(fill="x")
        self.progress_bar["value"] = 0

        # 4. Activity log
        log_header = ttk.Frame(main_frame)
        log_header.pack(fill="x", pady=(10, 4))
        ttk.Label(log_header, text="ACTIVITY LOG", style="SectionTitle.TLabel").pack(side="left", anchor="w")
        ttk.Button(log_header, text="🗑️ Clear Log", style="Link.TButton",
                   command=self.clear_log_view, cursor="hand2").pack(side="right", anchor="e")

        log_frame = tk.Frame(main_frame, bg=p.log_bg, highlightthickness=1,
                             highlightbackground=p.border, highlightcolor=p.border)
        log_frame.pack(fill="both", expand=True)
        self._log_frame = log_frame

        self.log_text = tk.Text(
            log_frame, height=10, wrap="word", font=(MONO_FAMILY, 9), state="disabled",
            bg=p.log_bg, fg=p.log_fg, insertbackground=p.log_fg, relief="flat",
            borderwidth=0, highlightthickness=0, padx=12, pady=10,
            selectbackground=p.accent, selectforeground=p.accent_fg,
        )
        self.log_text.tag_configure("INFO", foreground=p.log_fg)
        self.log_text.tag_configure("WARN", foreground=p.warning)
        self.log_text.tag_configure("ERROR", foreground=p.error)

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
        ttk.Label(footer, text="  ·  ", style="SubHeader.TLabel").pack(side="left")
        ttk.Button(footer, text="📁 Open Data Folder", style="Link.TButton",
                   command=self.open_data_folder, cursor="hand2").pack(side="left")
        self.lbl_status_bar = ttk.Label(footer, text="Ready.", style="Footer.TLabel")
        self.lbl_status_bar.pack(side="right")

    def toggle_theme(self) -> None:
        """Switch between Dark and Light palettes on the fly."""
        next_theme = LIGHT if self.palette.name == DARK else DARK
        self.palette = get_palette(next_theme)
        p = self.palette

        self.root.configure(bg=p.bg)
        self._init_styles()

        self.lbl_version_pill.configure(bg=p.surface_alt, fg=p.muted)
        self.btn_theme.configure(text="☀️ Light" if p.name == DARK else "🌙 Dark")
        self.btn_theme.set_palette(p, parent_bg=p.bg)

        self._card_canvas.configure(bg=p.bg)
        self._card_divider.configure(bg=p.border)
        self._sync_card()

        for btn in self._rounded_buttons:
            btn.set_palette(p, parent_bg=p.bg)

        self.progress_bar.set_palette(p)
        self._log_frame.configure(bg=p.log_bg, highlightbackground=p.border, highlightcolor=p.border)
        self.log_text.configure(
            bg=p.log_bg,
            fg=p.log_fg,
            insertbackground=p.log_fg,
            selectbackground=p.accent,
            selectforeground=p.accent_fg,
        )
        self.log_text.tag_configure("INFO", foreground=p.log_fg)
        self.log_text.tag_configure("WARN", foreground=p.warning)
        self.log_text.tag_configure("ERROR", foreground=p.error)

        if self._last_status is not None:
            self._apply_status(self._last_status)

    # ----------------------------------------------------------------- helpers
    def _on_close(self) -> None:
        logging.getLogger().removeHandler(self.log_handler)
        self.root.destroy()

    def _set_buttons_state(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for btn in (
            self.btn_run,
            self.btn_bypass,
            self.btn_pull,
            self.btn_push,
            self.btn_refresh,
            self.btn_reconcile,
            self.btn_full_push,
            self.btn_restore,
        ):
            btn.configure(state=state)

    def refresh_status_async(self) -> None:
        """Fetch status in a background thread."""
        self.lbl_status_bar.configure(text="Checking Google Drive status...")

        def worker() -> None:
            status = self.runner.get_status()
            self.root.after(0, lambda: self._apply_status(status))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_status(self, status: SyncStatus) -> None:
        self._last_status = status
        p = self.palette
        if status.drive_connected:
            self.lbl_drive_status.configure(text="● Connected", foreground=p.success)
            if status.cloud_last_device:
                when = (status.cloud_last_sync_utc or "")[:16].replace("T", " ")
                epoch_str = f" [rev {status.cloud_epoch}]" if status.cloud_epoch else ""
                self.lbl_cloud_last.configure(
                    text=f"{status.cloud_last_device}{epoch_str}  ·  {when} UTC",
                    foreground=p.fg,
                )
            else:
                self.lbl_cloud_last.configure(text="No previous cloud sync", foreground=p.muted)

            if status.active_device and status.active_device != self.runner.config.device_id:
                when_active = (status.active_since_utc or "")[:16].replace("T", " ")
                self.lbl_alignment.configure(
                    text=f"⚠️ {status.active_device} active since {when_active} UTC (previous session)",
                    foreground=p.warning,
                )
            elif status.session_dirty:
                self.lbl_alignment.configure(
                    text="⚠️ Pending push from previous session (click Force Push)",
                    foreground=p.warning,
                )
            elif status.cloud_last_device is None:
                self.lbl_alignment.configure(
                    text="● Cloud empty (waiting for initial push)",
                    foreground=p.muted,
                )
            elif status.in_sync:
                self.lbl_alignment.configure(text="● In sync with cloud", foreground=p.success)
            else:
                self.lbl_alignment.configure(
                    text=f"● Updates available from {status.cloud_last_device}",
                    foreground=p.warning,
                )
        else:
            self.lbl_drive_status.configure(text="● Unreachable (check rclone config)", foreground=p.error)
            self.lbl_cloud_last.configure(text="--", foreground=p.muted)
            self.lbl_alignment.configure(text="--", foreground=p.muted)

        self.lbl_status_bar.configure(text="Ready.")

    def _set_progress(self, percent: int, description: str) -> None:
        """Update progress bar value and status text on the GUI."""
        self.progress_bar["value"] = percent
        if description:
            self.lbl_progress_step.configure(text=f"{description} ({percent}%)")
            self.lbl_status_bar.configure(text=description)
        else:
            self.lbl_progress_step.configure(text="Idle — ready to synchronize")

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
            try:
                def update_progress(pct: int, msg: str) -> None:
                    self.root.after(0, lambda: self._set_progress(pct, msg))

                # 0. Dirty session recovery (if previous session crashed or closed offline)
                if is_session_dirty():
                    logger.warning("Unclean exit detected from previous session. Recovering...")
                    update_progress(8, "Recovering unfinished sync from previous session...")
                    try:
                        manifest = self.runner.drive.get_manifest()
                        if manifest and manifest.last_device != self.runner.config.device_id:
                            logger.warning(
                                f"Cloud has newer changes from '{manifest.last_device}' (epoch {manifest.epoch}). "
                                "Merging cloud updates with local changes first..."
                            )
                            self.runner.pull(force=True, progress_cb=update_progress)
                        self.runner.push(progress_cb=update_progress)
                    except DatabaseMergeError as e:
                        logger.critical(f"Aborting dirty recovery due to schema mismatch: {e}")

                        def show_dirty_conflict() -> None:
                            self.root.deiconify()
                            self._set_progress(0, "Launch halted: schema conflict.")
                            messagebox.showerror(
                                "GC-ChatSync: Schema Conflict",
                                f"Synchronization stopped for safety during recovery:\n\n{e}\n\n"
                                "Please update Antigravity to the same version on all devices.",
                            )
                            self._set_buttons_state(True)
                            self.refresh_status_async()

                        self.root.after(0, show_dirty_conflict)
                        return
                    except RcloneError as e:
                        logger.warning(f"Could not reach Google Drive for recovery ({e}). Continuing.")

                # 1. Check if another device has an unclosed active session (in background thread)
                manifest = None
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
                    # Offline mode: mark session dirty, launch Antigravity without heartbeat, skip push on exit
                    mark_session_dirty(self.runner.config.device_id)
                    self.root.after(0, lambda: self._set_progress(50, "Offline mode: launching Antigravity..."))

                    def on_app_started_offline() -> None:
                        self.root.after(0, self.root.withdraw)

                    self.runner.launch_app(on_launched=on_app_started_offline, enable_heartbeat=False)
                    self.root.after(0, self.root.destroy)
                    return

                # Online mode: mark session dirty and claim active witness synchronously before heartbeat starts
                mark_session_dirty(self.runner.config.device_id)
                self.runner.claim_active_witness(manifest)

                # 3. LAUNCH APP AND HIDE GUI ONLY AFTER APP IS SPAWNED
                self.root.after(0, lambda: self._set_progress(100, "Launching Antigravity..."))

                def on_app_started() -> None:
                    self.root.after(0, self.root.withdraw)

                self.runner.launch_app(on_launched=on_app_started, enable_heartbeat=True)

                # 4. RESTORE GUI TO SHOW PUSH PROGRESS
                def show_push_status() -> None:
                    self.root.deiconify()
                    self._set_progress(10, "Saving and uploading to Google Drive...")

                self.root.after(0, show_push_status)

                # 5. PUSH
                push_ok = self.runner.push(progress_cb=update_progress)

                if push_ok:
                    clear_session_dirty()

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

    def on_full_push_clicked(self) -> None:
        """Execute a full push of all conversation databases regardless of timestamps."""
        self._run_in_background(
            "Uploading all conversations (Full Push)...",
            lambda cb: self.runner.push(progress_cb=cb, full=True),
            "Full upload to Google Drive completed!",
            "Full Push Error",
        )

    def on_reconcile_clicked(self) -> None:
        """Run Doctor / Reconcile to fix unindexed sessions and stale witness locks."""
        if self.is_running:
            return

        self.is_running = True
        self._set_buttons_state(False)
        self.lbl_status_bar.configure(text="Running Doctor & Reconcile...")
        self._set_progress(10, "Diagnosing and reconciling...")

        def worker() -> None:
            try:
                def on_progress(pct: int, msg: str) -> None:
                    self.root.after(0, lambda: self._set_progress(pct, msg))

                from gc_chatsync.reconcile import run_reconcile

                rep = run_reconcile(self.runner, progress_cb=on_progress)

                if rep.status == "CRITICAL_ABORT":
                    err_msg = "\n".join(rep.errors)
                    self.root.after(0, lambda: self._set_progress(0, "Reconcile aborted."))
                    self.root.after(0, lambda: messagebox.showerror("GC-ChatSync: Reconcile Aborted", err_msg))
                else:
                    summary = (
                        f"Reconciliation completed ({rep.status}):\n\n"
                        f"• Orphan sessions indexed: {rep.orphans_indexed} / {rep.orphans_found}\n"
                        f"• Corrupt files isolated: {len(rep.corrupt_files)}\n"
                        f"• Stale witness cleared: {rep.stale_witness_cleared}\n"
                        f"• Remote conversations synced: {rep.remote_synced}\n"
                    )
                    if rep.warnings:
                        summary += "\nWarnings:\n" + "\n".join(f"- {w}" for w in rep.warnings)

                    self.root.after(0, lambda: self._set_progress(100, "Reconciliation finished."))
                    self.root.after(0, lambda: messagebox.showinfo("GC-ChatSync: Doctor & Reconcile", summary))
            except Exception as e:
                logger.error(f"Reconcile error: {e}", exc_info=True)
                self.root.after(0, lambda: self._set_progress(0, f"Error: {e}"))
                self.root.after(0, lambda: messagebox.showerror("GC-ChatSync: Reconcile Error", str(e)))
            finally:
                self.is_running = False
                self.root.after(0, lambda: self._set_buttons_state(True))
                self.root.after(0, self.refresh_status_async)

        threading.Thread(target=worker, daemon=True).start()

    def on_restore_backup_clicked(self) -> None:
        """Prompt and restore conversation_summaries.db from the latest snapshot."""
        if self.is_running:
            return

        from gc_chatsync.merger import restore_latest_backup
        from gc_chatsync.runner import is_antigravity_process_running

        if is_antigravity_process_running():
            messagebox.showerror(
                "GC-ChatSync: Restore Blocked",
                "Antigravity is currently running. Please close Antigravity before restoring a backup.",
            )
            return

        confirm = messagebox.askyesno(
            "GC-ChatSync: Restore Latest Backup",
            "Are you sure you want to restore the latest database backup?\n\n"
            "This will revert conversation_summaries.db to its previous state.\n"
            "A safety snapshot of your current database will be saved automatically.",
        )
        if not confirm:
            return

        db_path = self.runner.data_dir / "conversation_summaries.db"
        success, msg, _ = restore_latest_backup(db_path)
        if success:
            self._set_progress(100, msg)
            messagebox.showinfo("GC-ChatSync: Backup Restored", msg)
            self.refresh_status_async()
        else:
            self._set_progress(0, msg)
            messagebox.showerror("GC-ChatSync: Restore Failed", msg)

    def clear_log_view(self) -> None:
        """Clear the in-memory activity log view in the GUI."""
        try:
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.configure(state="disabled")
        except tk.TclError:
            pass

    def open_data_folder(self) -> None:
        """Open the local Antigravity data folder in the system file manager."""
        data_path = Path(self.config.antigravity_data_dir).resolve()
        if not data_path.is_dir():
            messagebox.showinfo("GC-ChatSync", f"Data folder does not exist yet:\n{data_path}")
            return

        sys_name = get_system_name()
        try:
            if sys_name == "windows":
                os.startfile(str(data_path))  # type: ignore[attr-defined]
            elif sys_name == "darwin":
                subprocess.Popen(["open", str(data_path)])
            else:
                subprocess.Popen(["xdg-open", str(data_path)])
        except OSError as e:
            messagebox.showwarning("GC-ChatSync", f"Could not open data folder: {e}")

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
