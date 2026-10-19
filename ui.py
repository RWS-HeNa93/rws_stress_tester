from __future__ import annotations

import ctypes
import math
import os
import re
import queue
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime
from pathlib import Path
from typing import Any

import customtkinter as ctk
import psutil
from PIL import Image, ImageDraw, ImageOps

from orchestrator import (
    describe_exit_code,
    EVENT_ABORTED,
    EVENT_ALERT,
    EVENT_FINISHED,
    EVENT_LOG,
    EVENT_PHASE,
    EVENT_STATUS,
    EVENT_TELEMETRY,
    EVENT_PHASE_LOGS,
    FURMARK_VRAM_TEST_SIZES_GB,
    STDERR_PREFIX,
    StressOrchestrator,
    VRAM_HEADROOM_GB,
    detect_vram_gb,
    is_error_line,
    recommended_vram_test_gb,
)
from telemetry import CPU_TEMP_LIMIT_C, GPU_TEMP_LIMIT_C, TELEMETRY_CATEGORIES

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

# Two skins over the same layouts, chosen on the setup screen and saved in settings.json.
# Surfaces get lighter as they nest; ACCENT is the one highlight colour.
THEMES: dict[str, dict[str, Any]] = {
    # Phosphor-green terminal: green-black surfaces, green text, monospace, uppercase titles.
    "retro": {
        "BG": "#020805",
        "PANEL": "#05110a",
        "CARD": "#0a1b10",
        "CARD_HOVER": "#102716",
        "FIELD": "#030b06",
        "CONSOLE": "#010603",
        "BORDER": "#123321",
        "BORDER_STRONG": "#1e5534",
        "TEXT": "#b9ffcf",
        "TEXT_MUTED": "#6ccc88",
        "TEXT_FAINT": "#3c7a50",
        "ACCENT": "#3dff7f",
        "ACCENT_HOVER": "#8dffb3",
        "ACCENT_BG": "#0b3018",
        "ON_ACCENT": "#012109",
        "DANGER": "#ff5f5f",
        "DANGER_BG": "#2a0a0b",
        "DANGER_HOVER_BG": "#3d1214",
        "DANGER_BORDER": "#6e2626",
        "WARNING": "#ffc95c",
        "WARNING_BG": "#2a2109",
        "RADIUS": 4,
        "RADIUS_LARGE": 6,
        "UI_FONT_FAMILIES": ("Consolas", "Cascadia Mono", "DejaVu Sans Mono"),
        "UI_SEMIBOLD_FAMILIES": (),
        "TERMINAL_STYLE": True,
    },
    # Neutral dark greys with a green accent, Segoe UI.
    "modern": {
        "BG": "#0b0e0d",
        "PANEL": "#121614",
        "CARD": "#181d1b",
        "CARD_HOVER": "#1e2522",
        "FIELD": "#0e1210",
        "CONSOLE": "#090c0b",
        "BORDER": "#232b27",
        "BORDER_STRONG": "#334039",
        "TEXT": "#e7ece9",
        "TEXT_MUTED": "#98a39d",
        "TEXT_FAINT": "#5f6a64",
        "ACCENT": "#3ddc84",
        "ACCENT_HOVER": "#62e69c",
        "ACCENT_BG": "#14291d",
        "ON_ACCENT": "#06120b",
        "DANGER": "#ff6b6b",
        "DANGER_BG": "#2b1517",
        "DANGER_HOVER_BG": "#3d1c1f",
        "DANGER_BORDER": "#5a2a2e",
        "WARNING": "#f2b84b",
        "WARNING_BG": "#2b2412",
        "RADIUS": 8,
        "RADIUS_LARGE": 12,
        "UI_FONT_FAMILIES": ("Segoe UI", "Inter", "Roboto", "DejaVu Sans"),
        "UI_SEMIBOLD_FAMILIES": ("Segoe UI Semibold",),
        "TERMINAL_STYLE": False,
    },
}
DEFAULT_THEME = "retro"
THEME_LABELS = {"retro": "Retro", "modern": "Modern"}
CURRENT_THEME = DEFAULT_THEME
(
    BG, PANEL, CARD, CARD_HOVER, FIELD, CONSOLE, BORDER, BORDER_STRONG, TEXT, TEXT_MUTED, TEXT_FAINT,
    ACCENT, ACCENT_HOVER, ACCENT_BG, ON_ACCENT, DANGER, DANGER_BG, DANGER_HOVER_BG, DANGER_BORDER,
    WARNING, WARNING_BG, RADIUS, RADIUS_LARGE, UI_FONT_FAMILIES, UI_SEMIBOLD_FAMILIES, TERMINAL_STYLE,
) = THEMES[DEFAULT_THEME].values()
MONO_FONT_FAMILIES = ("Consolas", "Cascadia Mono", "DejaVu Sans Mono")


def apply_theme(name: str) -> str:
    """Switch the module's colour, font and style constants; widgets built afterwards use them."""
    global CURRENT_THEME
    CURRENT_THEME = name if name in THEMES else DEFAULT_THEME
    globals().update(THEMES[CURRENT_THEME])
    return CURRENT_THEME


UNASSIGNED_SENSOR_OPTION = "Unassigned"
RESERVED_PRESET_NAME = "Custom"
# Caps keep the Tk text widgets responsive during multi-hour runs with chatty engines.
CONSOLE_MAX_LINES = 5000
LOG_VIEWER_MAX_LINES = 20000
MAX_EVENTS_PER_DRAIN = 500
IMAGE_RESAMPLING = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS

_font_family_cache: dict[tuple[str, ...], str | None] = {}


def _first_installed(candidates: tuple[str, ...]) -> str | None:
    if candidates not in _font_family_cache:
        try:
            installed = set(tkfont.families())
        except tk.TclError:  # no Tk root yet
            return None
        _font_family_cache[candidates] = next((family for family in candidates if family in installed), None)
    return _font_family_cache[candidates]


def ui_font(size: int, weight: str = "normal") -> ctk.CTkFont:
    """UI text font. weight: normal, semibold (falls back to bold) or bold."""
    if weight == "semibold":
        family = _first_installed(UI_SEMIBOLD_FAMILIES)
        if family:
            return ctk.CTkFont(family=family, size=size)
        weight = "bold"
    return ctk.CTkFont(family=_first_installed(UI_FONT_FAMILIES) or "TkDefaultFont", size=size, weight=weight)


def mono_font(size: int, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family=_first_installed(MONO_FONT_FAMILIES) or "TkFixedFont", size=size, weight=weight)


def build_entry(parent: Any, textvariable: Any, width: int = 120, justify: str = "center", mono: bool = False) -> ctk.CTkEntry:
    return ctk.CTkEntry(
        parent,
        textvariable=textvariable,
        width=width,
        height=32,
        corner_radius=RADIUS,
        border_width=1,
        border_color=BORDER_STRONG,
        fg_color=FIELD,
        text_color=TEXT,
        font=mono_font(12) if mono else ui_font(13),
        justify=justify,
    )


def build_option_menu(
    parent: Any,
    variable: Any,
    values: list[str],
    width: int = 150,
    command: Any | None = None,
) -> ctk.CTkOptionMenu:
    return ctk.CTkOptionMenu(
        parent,
        variable=variable,
        values=values,
        width=width,
        height=32,
        command=command,
        corner_radius=RADIUS,
        fg_color=CARD_HOVER,
        button_color=BORDER_STRONG if TERMINAL_STYLE else CARD_HOVER,
        button_hover_color=TEXT_FAINT if TERMINAL_STYLE else BORDER_STRONG,
        dropdown_fg_color=CARD,
        dropdown_hover_color=CARD_HOVER,
        dropdown_text_color=TEXT,
        text_color=TEXT,
        font=ui_font(13),
        dropdown_font=ui_font(13),
        dynamic_resizing=False,
        anchor="w",
    )


def build_checkbox(parent: Any, text: str, variable: Any, command: Any | None = None) -> ctk.CTkCheckBox:
    return ctk.CTkCheckBox(
        parent,
        text=text,
        variable=variable,
        command=command,
        checkbox_width=18,
        checkbox_height=18,
        corner_radius=4,
        border_width=1,
        fg_color=ACCENT,
        hover_color=ACCENT_HOVER,
        border_color=BORDER_STRONG,
        checkmark_color=ON_ACCENT,
        text_color=TEXT,
        font=ui_font(13),
    )


def apply_windows_dark_title_bar(window: Any) -> None:
    if os.name != "nt":
        return
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id()) or window.winfo_id()
        value = ctypes.c_int(1)
        for attribute in (20, 19):
            result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd,
                attribute,
                ctypes.byref(value),
                ctypes.sizeof(value),
            )
            if result == 0:
                break
    except Exception:
        pass


def build_button(parent: Any, text: str, role: str = "secondary", **kwargs: Any) -> ctk.CTkButton:
    palette = {
        "primary": {"fg_color": ACCENT, "hover_color": ACCENT_HOVER, "text_color": ON_ACCENT, "border_width": 0},
        "secondary": (
            {"fg_color": CARD, "hover_color": CARD_HOVER, "text_color": TEXT, "border_width": 1, "border_color": BORDER_STRONG}
            if TERMINAL_STYLE
            else {"fg_color": CARD_HOVER, "hover_color": BORDER_STRONG, "text_color": TEXT, "border_width": 0}
        ),
        "ghost": {"fg_color": "transparent", "hover_color": CARD_HOVER, "text_color": TEXT_MUTED, "border_width": 0},
        "danger": {"fg_color": DANGER_BG, "hover_color": DANGER_HOVER_BG, "text_color": DANGER, "border_width": 1, "border_color": DANGER_BORDER},
        "success": {"fg_color": ACCENT, "hover_color": ACCENT_HOVER, "text_color": ON_ACCENT, "border_width": 0},
    }[role]
    kwargs.setdefault("corner_radius", RADIUS if TERMINAL_STYLE else 6)
    kwargs.setdefault("height", 34)
    kwargs.setdefault("font", ui_font(13, "semibold" if role in ("primary", "success") else "normal"))
    return ctk.CTkButton(parent, text=text, text_color_disabled=TEXT_FAINT, **palette, **kwargs)


def build_panel(parent: Any, title: str | None = None, subtitle: str | None = None, actions: Any | None = None) -> tuple[ctk.CTkFrame, ctk.CTkFrame]:
    """A titled surface. Returns (panel, body); `actions(header)` may add widgets to the header's right side."""
    panel = ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=RADIUS_LARGE, border_width=1, border_color=BORDER_STRONG if TERMINAL_STYLE else BORDER)
    panel.grid_columnconfigure(0, weight=1)
    next_row = 0
    if title:
        header = ctk.CTkFrame(panel, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(14, 0 if subtitle else 10))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text=terminal_title(title), font=ui_font(14 if TERMINAL_STYLE else 15, "semibold"), text_color=heading_color(), anchor="w").grid(row=0, column=0, sticky="w")
        if actions is not None:
            actions(header)
        next_row = 1
        if subtitle:
            ctk.CTkLabel(
                panel,
                text=subtitle,
                font=ui_font(12),
                text_color=TEXT_MUTED,
                justify="left",
                anchor="w",
            ).grid(row=1, column=0, sticky="ew", padx=18, pady=(2, 10))
            next_row = 2
    body = ctk.CTkFrame(panel, fg_color="transparent")
    body.grid(row=next_row, column=0, sticky="nsew", padx=18, pady=(0 if title else 16, 16))
    panel.grid_rowconfigure(next_row, weight=1)
    return panel, body


def terminal_title(text: str) -> str:
    """Panel and dialog titles: a prompt and capitals in the retro skin."""
    return f"> {text.upper()}" if TERMINAL_STYLE else text


def display_case(text: str) -> str:
    return text.upper() if TERMINAL_STYLE else text


def status_tag(status: str) -> str:
    return f"[ {status} ]" if TERMINAL_STYLE else f"  {status}  "


def heading_color() -> str:
    """Titles and big readouts glow green in the retro skin and stay white in the modern one."""
    return ACCENT if TERMINAL_STYLE else TEXT


def build_divider(parent: Any) -> ctk.CTkFrame:
    return ctk.CTkFrame(parent, height=1, fg_color=BORDER, corner_radius=0)


def format_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def status_colors(status: str) -> tuple[str, str]:
    """(text, background) for a phase status pill."""
    if status == "PASSED":
        return ACCENT, ACCENT_BG
    if status == "ABORTED":
        return WARNING, WARNING_BG
    return DANGER, DANGER_BG


class TestPhaseRow(ctk.CTkFrame):
    """One selectable test phase: switch, name and description, an optional option field, minutes."""

    def __init__(
        self,
        parent: Any,
        title: str,
        description: str,
        enabled_var: ctk.BooleanVar,
        duration_var: ctk.StringVar,
        extra_field: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(parent, fg_color="transparent", corner_radius=RADIUS)
        self.enabled_var = enabled_var
        self.grid_columnconfigure(1, weight=1)

        self.switch = ctk.CTkSwitch(
            self,
            text="",
            variable=enabled_var,
            width=44,
            switch_width=38,
            switch_height=20,
            fg_color=BORDER_STRONG,
            progress_color=ACCENT,
            button_color=TEXT,
            button_hover_color="#ffffff",
        )
        self.switch.grid(row=0, column=0, rowspan=2, padx=(10, 8), pady=10)

        self.title_label = ctk.CTkLabel(self, text=display_case(title), font=ui_font(14, "bold" if TERMINAL_STYLE else "semibold"), text_color=TEXT, anchor="w")
        self.title_label.grid(row=0, column=1, sticky="sw", pady=(9, 0))
        self.description_label = ctk.CTkLabel(self, text=description, font=ui_font(12), text_color=TEXT_FAINT, anchor="w")
        self.description_label.grid(row=1, column=1, sticky="nw", pady=(0, 9))

        if extra_field:
            option = ctk.CTkFrame(self, fg_color="transparent")
            option.grid(row=0, column=2, rowspan=2, sticky="e", padx=(12, 0))
            ctk.CTkLabel(option, text=extra_field["label"], font=ui_font(12), text_color=TEXT_MUTED).grid(row=0, column=0, padx=(0, 8))
            if extra_field["kind"] == "entry":
                widget = build_entry(option, extra_field["variable"], width=extra_field.get("width", 80))
            else:
                widget = build_option_menu(option, extra_field["variable"], extra_field["values"], width=extra_field.get("width", 90))
            widget.grid(row=0, column=1)

        minutes = ctk.CTkFrame(self, fg_color="transparent")
        minutes.grid(row=0, column=3, rowspan=2, sticky="e", padx=(20, 12))
        build_entry(minutes, duration_var, width=56).grid(row=0, column=0)
        ctk.CTkLabel(minutes, text="min", font=ui_font(12), text_color=TEXT_MUTED).grid(row=0, column=1, padx=(6, 0))

        for widget in (self, self.title_label, self.description_label):
            widget.bind("<Button-1>", lambda _event: self.enabled_var.set(not self.enabled_var.get()))
        trace_id = self.enabled_var.trace_add("write", lambda *_args: self.refresh_state())
        # CTk's bind() targets its internal canvas, so bind <Destroy> on the frame itself.
        tk.Misc.bind(self, "<Destroy>", lambda event: event.widget is self and self.enabled_var.trace_remove("write", trace_id), "+")
        self.refresh_state()

    def refresh_state(self) -> None:
        enabled = bool(self.enabled_var.get())
        self.configure(fg_color=CARD if enabled else "transparent")
        self.title_label.configure(text_color=TEXT if enabled else TEXT_MUTED)
        self.description_label.configure(text_color=TEXT_MUTED if enabled else TEXT_FAINT)


def dialog_accent(dialog_type: str) -> str:
    return {"warning": WARNING, "error": DANGER}.get(dialog_type, ACCENT)


def _build_dialog_shell(window: ctk.CTkToplevel, title: str, accent: str) -> ctk.CTkFrame:
    """Window chrome shared by the small dialogs: accent strip, title. Returns the content frame."""
    window.configure(fg_color=PANEL)
    window.grid_rowconfigure(1, weight=1)
    window.grid_columnconfigure(0, weight=1)
    ctk.CTkFrame(window, height=3, fg_color=accent, corner_radius=0).grid(row=0, column=0, sticky="ew")
    content = ctk.CTkFrame(window, fg_color="transparent")
    content.grid(row=1, column=0, sticky="nsew", padx=24, pady=(18, 20))
    content.grid_columnconfigure(0, weight=1)
    ctk.CTkLabel(
        content,
        text=terminal_title(title),
        font=ui_font(16 if TERMINAL_STYLE else 17, "semibold"),
        text_color=accent if TERMINAL_STYLE else TEXT,
        anchor="w",
    ).grid(row=0, column=0, sticky="w")
    window.after(0, lambda: apply_windows_dark_title_bar(window))
    return content


class AppMessageDialog(ctk.CTkToplevel):
    def __init__(
        self,
        master: Any,
        title: str,
        message: str,
        dialog_type: str = "info",
        buttons: list[tuple[str, str, Any]] | None = None,
        default_result: Any = None,
    ) -> None:
        super().__init__(master)
        self.result = default_result

        self.title(title)
        self.geometry("560x320")
        self.minsize(480, 260)
        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda _event: self._close())

        content = _build_dialog_shell(self, title, dialog_accent(dialog_type))
        content.grid_rowconfigure(1, weight=1)

        self.message_box = ctk.CTkTextbox(
            content,
            fg_color=PANEL,
            border_width=0,
            border_spacing=0,
            text_color=TEXT_MUTED,
            font=ui_font(13),
            wrap="word",
            activate_scrollbars=True,
            scrollbar_button_color=BORDER_STRONG,
        )
        self.message_box.grid(row=1, column=0, sticky="nsew", pady=(10, 16))
        self.message_box._textbox.configure(padx=0, spacing2=2)
        self.message_box.insert("1.0", message)
        self.message_box.configure(state="disabled")

        button_bar = ctk.CTkFrame(content, fg_color="transparent")
        button_bar.grid(row=2, column=0, sticky="e")
        for index, (label, role, value) in enumerate(buttons or [("OK", "primary", True)]):
            build_button(
                button_bar,
                label,
                role=role,
                width=110,
                command=lambda v=value: self._set_result(v),
            ).grid(row=0, column=index, padx=(8 if index > 0 else 0, 0))

        self.after(10, self._activate_modal)

    def _activate_modal(self) -> None:
        self.grab_set()
        self.focus_force()

    def _set_result(self, value: Any) -> None:
        self.result = value
        self.destroy()

    def _close(self) -> None:
        self.destroy()

    def show(self) -> Any:
        self.wait_window()
        return self.result


class AppInputDialog(ctk.CTkToplevel):
    def __init__(
        self,
        master: Any,
        title: str,
        prompt: str,
        default_value: str = "",
        confirm_label: str = "Save",
        cancel_label: str = "Cancel",
    ) -> None:
        super().__init__(master)
        self.result: str | None = None

        self.title(title)
        self.geometry("480x220")
        self.resizable(False, False)
        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda _event: self._close())

        content = _build_dialog_shell(self, title, ACCENT)
        ctk.CTkLabel(content, text=prompt, text_color=TEXT_MUTED, font=ui_font(13), anchor="w").grid(
            row=1, column=0, sticky="w", pady=(8, 6)
        )
        self.input_var = ctk.StringVar(value=default_value)
        self.entry = build_entry(content, self.input_var, width=380, justify="left")
        self.entry.grid(row=2, column=0, sticky="ew")
        self.entry.bind("<Return>", lambda _event: self._submit())

        button_bar = ctk.CTkFrame(content, fg_color="transparent")
        button_bar.grid(row=3, column=0, sticky="e", pady=(18, 0))
        build_button(button_bar, cancel_label, role="secondary", width=110, command=self._close).grid(row=0, column=0)
        build_button(button_bar, confirm_label, role="primary", width=110, command=self._submit).grid(row=0, column=1, padx=(8, 0))

        self.after(10, self._activate_modal)

    def _activate_modal(self) -> None:
        self.grab_set()
        self.focus_force()
        self.entry.focus_set()
        self.entry.select_range(0, "end")

    def _submit(self) -> None:
        value = self.input_var.get().strip()
        self.result = value if value else None
        self.destroy()

    def _close(self) -> None:
        self.destroy()

    def show(self) -> str | None:
        self.wait_window()
        return self.result


class AppFilePickerDialog(ctk.CTkToplevel):
    def __init__(
        self,
        master: Any,
        title: str,
        prompt: str,
        initial_path: Path,
        file_extensions: tuple[str, ...],
        shortcut_targets: list[tuple[str, Path]] | None = None,
        confirm_label: str = "Use File",
        cancel_label: str = "Cancel",
    ) -> None:
        super().__init__(master)
        self.result: str | None = None
        self.image_cache: dict[tuple[str, tuple[int, int]], ctk.CTkImage | None] = {}
        self.preview_image_ref: ctk.CTkImage | None = None
        self.file_extensions = {
            suffix.lower() if suffix.startswith(".") else f".{suffix.lower()}"
            for suffix in file_extensions
        }
        self.shortcut_targets: list[tuple[str, Path]] = []
        seen_targets: set[str] = set()
        for label, target in shortcut_targets or []:
            target_path = Path(target)
            if not target_path.exists():
                continue
            target_key = str(target_path)
            if target_key in seen_targets:
                continue
            seen_targets.add(target_key)
            self.shortcut_targets.append((label, target_path))

        self.current_dir = self._resolve_initial_dir(Path(initial_path))
        self.selected_path: Path | None = None
        if Path(initial_path).is_file() and self._is_allowed_file(Path(initial_path)):
            self.selected_path = Path(initial_path)
            self.current_dir = self.selected_path.parent

        self.location_targets = self._build_location_targets()
        self.location_map = {label: path for label, path in self.location_targets}
        self.location_var = ctk.StringVar(value="")
        self.current_path_var = ctk.StringVar(value=str(self.current_dir))
        self.selected_path_var = ctk.StringVar(value=str(self.selected_path) if self.selected_path else "")

        self.title(title)
        self.geometry("1080x760")
        self.minsize(900, 620)
        self.transient(master)
        self.configure(fg_color=BG)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda _event: self._close())

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.after(0, lambda: apply_windows_dark_title_bar(self))

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 12))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text=terminal_title(title), font=ui_font(20, "semibold"), text_color=heading_color(), anchor="w").grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(header, text=prompt, font=ui_font(12), text_color=TEXT_MUTED, anchor="w").grid(row=1, column=0, sticky="w")
        self.location_menu = build_option_menu(
            header,
            variable=self.location_var,
            values=list(self.location_map.keys()),
            width=280,
            command=self._open_location_from_menu,
        )
        self.location_menu.grid(row=0, column=1, rowspan=2, sticky="e")

        listing_body = ctk.CTkFrame(self, fg_color="transparent")
        listing_body.grid(row=1, column=0, sticky="nsew", padx=24)
        listing_body.grid_rowconfigure(0, weight=1)
        listing_body.grid_columnconfigure(0, weight=3)
        listing_body.grid_columnconfigure(1, weight=2)

        self.list_frame = ctk.CTkScrollableFrame(
            listing_body,
            fg_color=PANEL,
            corner_radius=RADIUS_LARGE,
            border_width=1,
            border_color=BORDER,
            scrollbar_button_color=BORDER_STRONG,
            scrollbar_button_hover_color=TEXT_FAINT,
        )
        self.list_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        self.list_frame.grid_columnconfigure(0, weight=1)

        preview_frame = ctk.CTkFrame(
            listing_body,
            fg_color=PANEL,
            corner_radius=RADIUS_LARGE,
            border_width=1,
            border_color=BORDER,
        )
        preview_frame.grid(row=0, column=1, sticky="nsew")
        preview_frame.grid_columnconfigure(0, weight=1)
        preview_frame.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            preview_frame,
            text="Preview",
            text_color=TEXT,
            font=ui_font(14, "semibold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(14, 8))

        self.preview_image_label = ctk.CTkLabel(
            preview_frame,
            text="Select a logo file to preview.",
            text_color=TEXT_FAINT,
            font=ui_font(12),
            fg_color=FIELD,
            corner_radius=RADIUS,
            anchor="center",
            justify="center",
            width=320,
            height=260,
        )
        self.preview_image_label.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 12))

        self.preview_name_label = ctk.CTkLabel(
            preview_frame,
            text="No file selected",
            text_color=TEXT_MUTED,
            font=ui_font(14, "semibold"),
            anchor="w",
        )
        self.preview_name_label.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 4))

        self.preview_meta_label = ctk.CTkLabel(
            preview_frame,
            text="PNG and JPG thumbnails appear in the list.",
            text_color=TEXT_FAINT,
            font=ui_font(12),
            anchor="w",
            justify="left",
        )
        self.preview_meta_label.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 16))

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew", padx=24, pady=(14, 20))
        footer.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(footer, text="Selected", text_color=TEXT_MUTED, font=ui_font(12), anchor="w").grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.selected_entry = build_entry(footer, self.selected_path_var, justify="left")
        self.selected_entry.grid(row=0, column=1, sticky="ew")
        button_bar = ctk.CTkFrame(footer, fg_color="transparent")
        button_bar.grid(row=0, column=2, sticky="e", padx=(12, 0))
        build_button(button_bar, cancel_label, role="secondary", width=100, command=self._close).grid(row=0, column=0)
        self.confirm_button = build_button(button_bar, confirm_label, role="primary", width=110, command=self._submit)
        self.confirm_button.grid(row=0, column=1, padx=(8, 0))

        self._set_selected_path(self.selected_path)
        self._refresh_listing(scroll_to_top=True)
        self.after(10, self._activate_modal)

    def _activate_modal(self) -> None:
        self.grab_set()
        self.focus_force()
        self.location_menu.focus_set()

    def _resolve_initial_dir(self, initial_path: Path) -> Path:
        if initial_path.exists():
            if initial_path.is_dir():
                return initial_path
            if initial_path.is_file():
                return initial_path.parent

        if initial_path.parent.exists():
            return initial_path.parent

        for _label, target in self.shortcut_targets:
            if target.is_dir():
                return target
            if target.parent.exists():
                return target.parent

        home_dir = Path.home()
        return home_dir if home_dir.exists() else Path.cwd()

    def _build_location_targets(self) -> list[tuple[str, Path]]:
        locations: list[tuple[str, Path]] = []
        seen_paths: set[str] = set()

        def add_location(label: str, path: Path) -> None:
            target = Path(path)
            if not target.exists() or not target.is_dir():
                return
            path_key = str(target).casefold()
            if path_key in seen_paths:
                return
            seen_paths.add(path_key)
            locations.append((label, target))

        for label, path in self.shortcut_targets:
            add_location(label, path)

        home_dir = Path.home()
        standard_dirs = [
            ("Desktop", home_dir / "Desktop"),
            ("Documents", home_dir / "Documents"),
            ("Downloads", home_dir / "Downloads"),
            ("Pictures", home_dir / "Pictures"),
            ("Music", home_dir / "Music"),
            ("Videos", home_dir / "Videos"),
        ]
        for label, path in standard_dirs:
            add_location(label, path)

        drive_roots: list[Path] = []
        seen_drives: set[str] = set()
        for partition in psutil.disk_partitions(all=False):
            raw_mount = partition.mountpoint or partition.device
            if not raw_mount:
                continue
            normalized_mount = raw_mount.rstrip("\\/") + "\\"
            drive_key = normalized_mount.casefold()
            if drive_key in seen_drives:
                continue
            seen_drives.add(drive_key)
            drive_path = Path(normalized_mount)
            if drive_path.exists() and drive_path.is_dir():
                drive_roots.append(drive_path)
        for drive_path in sorted(drive_roots, key=lambda item: str(item).upper()):
            add_location(str(drive_path), drive_path)

        if not locations:
            add_location(str(self.current_dir), self.current_dir)

        return locations

    @staticmethod
    def _path_contains(path: Path, candidate: Path) -> bool:
        return path == candidate or candidate in path.parents

    def _sync_location_menu(self) -> None:
        if not self.location_targets:
            return

        best_label = self.location_targets[0][0]
        best_depth = -1
        current_dir = self.current_dir
        for label, target in self.location_targets:
            if self._path_contains(current_dir, target):
                depth = len(target.parts)
                if depth > best_depth:
                    best_label = label
                    best_depth = depth
        self.location_var.set(best_label)

    def _open_location_from_menu(self, selected_label: str) -> None:
        target = self.location_map.get(selected_label)
        if target is not None:
            self._open_directory(target)

    def _is_allowed_file(self, path: Path) -> bool:
        return path.is_file() and path.suffix.lower() in self.file_extensions

    @staticmethod
    def _is_hidden_path(path: Path) -> bool:
        if path.name.startswith("."):
            return True
        if os.name == "nt":
            try:
                attributes = ctypes.windll.kernel32.GetFileAttributesW(str(path))
            except Exception:
                return False
            if attributes == -1:
                return False
            return bool(attributes & 0x2 or attributes & 0x4)
        return False

    @staticmethod
    def _format_file_size(size_bytes: int) -> str:
        size = float(size_bytes)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024.0 or unit == "GB":
                return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
            size /= 1024.0
        return f"{size_bytes} B"

    @staticmethod
    def _format_timestamp(timestamp: float) -> str:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")

    def _load_ctk_image(self, path: Path, size: tuple[int, int]) -> ctk.CTkImage | None:
        cache_key = (str(path), size)
        if cache_key in self.image_cache:
            return self.image_cache[cache_key]

        try:
            with Image.open(path) as image:
                image = ImageOps.exif_transpose(image)
                if image.mode not in ("RGB", "RGBA"):
                    image = image.convert("RGBA")
                contained = ImageOps.contain(image, size, method=IMAGE_RESAMPLING)
                canvas = Image.new("RGBA", size, (0, 0, 0, 0))
                offset = ((size[0] - contained.width) // 2, (size[1] - contained.height) // 2)
                canvas.paste(contained, offset, contained if contained.mode == "RGBA" else None)
                ctk_image = ctk.CTkImage(light_image=canvas, dark_image=canvas, size=size)
        except Exception:
            ctk_image = None

        self.image_cache[cache_key] = ctk_image
        return ctk_image

    def _get_folder_icon(self, size: tuple[int, int]) -> ctk.CTkImage:
        cache_key = ("__folder__", size)
        cached = self.image_cache.get(cache_key)
        if cached is not None:
            return cached

        canvas = Image.new("RGBA", size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        width, height = size
        body = (
            max(3, width * 0.14),
            max(12, height * 0.34),
            width - max(3, width * 0.08),
            height - max(5, height * 0.14),
        )
        tab = (
            max(5, width * 0.14),
            max(5, height * 0.18),
            width * 0.48,
            height * 0.42,
        )
        draw.rounded_rectangle(tab, radius=4, fill="#2c5c41", outline=None)
        draw.rounded_rectangle(body, radius=6, fill="#1d3a2a", outline=None)
        draw.line(
            [(body[0] + 6, body[1] + 7), (body[2] - 6, body[1] + 7)],
            fill=ACCENT,
            width=2,
        )

        folder_image = ctk.CTkImage(light_image=canvas, dark_image=canvas, size=size)
        self.image_cache[cache_key] = folder_image
        return folder_image

    def _set_status(self, text: str, color: str = TEXT_MUTED) -> None:  # noqa: ARG002
        """Status bar was removed; kept for compatibility with internal callers."""
        return

    def _set_selected_path(self, path: Path | None) -> None:
        self.selected_path = path
        self.selected_path_var.set(str(path) if path else "")
        self.confirm_button.configure(state="normal" if path else "disabled")
        self._update_preview()

    def _scroll_listing_to_top(self) -> None:
        canvas = getattr(self.list_frame, "_parent_canvas", None)
        if canvas is None:
            return
        try:
            self.update_idletasks()
            canvas.yview_moveto(0)
        except Exception:
            return

    def _go_to_path(self) -> None:
        raw_path = self.current_path_var.get().strip().strip('"')
        if not raw_path:
            self._set_status("Enter a folder path or an image file path.", DANGER)
            return

        target = Path(raw_path)
        if target.is_dir():
            self._open_directory(target)
            return
        if target.is_file():
            if not self._is_allowed_file(target):
                self._set_status("Only PNG and JPG logo files are supported.", DANGER)
                return
            self.current_dir = target.parent
            self._set_selected_path(target)
            self._refresh_listing(scroll_to_top=True)
            return

        self._set_status("That path does not exist.", DANGER)

    def _go_up(self) -> None:
        parent = self.current_dir.parent
        if parent == self.current_dir:
            self._set_status("Already at the top of this drive.")
            return
        self._open_directory(parent)

    def _open_directory(self, path: Path) -> None:
        target = Path(path)
        if not target.exists() or not target.is_dir():
            self._set_status("Unable to open that folder.", DANGER)
            return
        self.current_dir = target
        self._set_selected_path(None)
        self._refresh_listing(scroll_to_top=True)

    def _select_file(self, path: Path) -> None:
        if not self._is_allowed_file(path):
            self._set_status("Only PNG and JPG logo files are supported.", DANGER)
            return
        self._set_selected_path(path)
        self._refresh_listing(scroll_to_top=False)

    def _submit_selected(self, path: Path) -> None:
        self._set_selected_path(path)
        self._submit()

    def _refresh_listing(self, scroll_to_top: bool = False) -> None:
        for widget in self.list_frame.winfo_children():
            widget.destroy()

        self.current_path_var.set(str(self.current_dir))
        self._sync_location_menu()
        has_parent = self.current_dir.parent != self.current_dir

        try:
            entries = [entry for entry in self.current_dir.iterdir() if not self._is_hidden_path(entry)]
        except OSError as exc:
            self._set_status(f"Unable to open folder: {exc}", DANGER)
            return

        folders = sorted([entry for entry in entries if entry.is_dir()], key=lambda item: item.name.lower())
        files = sorted(
            [entry for entry in entries if self._is_allowed_file(entry)],
            key=lambda item: item.name.lower(),
        )

        folder_count = len(folders)
        file_count = len(files)
        self._set_status(
            f"{folder_count} folder{'s' if folder_count != 1 else ''} | "
            f"{file_count} image file{'s' if file_count != 1 else ''}"
        )

        if self.selected_path is not None and self.selected_path.parent != self.current_dir:
            self._set_selected_path(None)

        if not folders and not files and not has_parent:
            ctk.CTkLabel(
                self.list_frame,
                text="No folders or matching image files were found here.",
                text_color=TEXT_FAINT,
                font=ui_font(12),
                anchor="center",
                justify="center",
            ).grid(row=0, column=0, sticky="ew", padx=14, pady=14)
            if scroll_to_top:
                self.after_idle(self._scroll_listing_to_top)
            return

        row_index = 0
        if has_parent:
            self._add_parent_row(row_index)
            row_index += 1
        for folder in folders:
            self._add_path_row(row_index, folder, is_directory=True)
            row_index += 1
        for file_path in files:
            self._add_path_row(row_index, file_path, is_directory=False)
            row_index += 1

        self._update_preview()
        if scroll_to_top:
            self.after_idle(self._scroll_listing_to_top)

    def _add_parent_row(self, row_index: int) -> None:
        parent_dir = self.current_dir.parent
        row = ctk.CTkFrame(
            self.list_frame,
            fg_color=CARD,
            corner_radius=RADIUS,
            border_width=1,
            border_color=CARD,
        )
        row.grid(row=row_index, column=0, sticky="ew", padx=6, pady=3)
        row.grid_columnconfigure(1, weight=1)

        thumbnail = ctk.CTkLabel(
            row,
            text="",
            text_color=TEXT_MUTED,
            font=ui_font(11, "semibold"),
            fg_color=FIELD,
            corner_radius=RADIUS,
            width=44,
            height=44,
            anchor="center",
            justify="center",
        )
        thumbnail.grid(row=0, column=0, rowspan=2, padx=(10, 12), pady=8)
        folder_icon = self._get_folder_icon((40, 40))
        thumbnail.configure(image=folder_icon)
        thumbnail.image = folder_icon

        name_label = ctk.CTkLabel(
            row,
            text="(...)",
            text_color=TEXT,
            font=ui_font(14, "semibold"),
            anchor="w",
        )
        name_label.grid(row=0, column=1, sticky="w", pady=(8, 0))

        meta_label = ctk.CTkLabel(
            row,
            text=f"Go to parent folder | {parent_dir}",
            text_color=TEXT_FAINT,
            font=ui_font(11),
            anchor="w",
        )
        meta_label.grid(row=1, column=1, sticky="w", pady=(0, 8))

        build_button(
            row,
            "Up",
            role="secondary",
            width=84,
            height=30,
            command=lambda p=parent_dir: self._open_directory(p),
        ).grid(row=0, column=2, rowspan=2, padx=10, pady=8)

        clickable_widgets = (row, thumbnail, name_label, meta_label)
        for widget in clickable_widgets:
            widget.bind("<Double-Button-1>", lambda _event, p=parent_dir: self._open_directory(p))

    def _add_path_row(self, row_index: int, path: Path, is_directory: bool) -> None:
        is_selected = not is_directory and self.selected_path == path
        row = ctk.CTkFrame(
            self.list_frame,
            fg_color=ACCENT_BG if is_selected else CARD,
            corner_radius=RADIUS,
            border_width=1,
            border_color=ACCENT if is_selected else BORDER,
        )
        row.grid(row=row_index, column=0, sticky="ew", padx=6, pady=3)
        row.grid_columnconfigure(1, weight=1)

        thumbnail = ctk.CTkLabel(
            row,
            text="",
            text_color=TEXT_MUTED,
            font=ui_font(11, "semibold"),
            fg_color=FIELD,
            corner_radius=RADIUS,
            width=44,
            height=44,
            anchor="center",
            justify="center",
        )
        thumbnail.grid(row=0, column=0, rowspan=2, padx=(10, 12), pady=8)
        if is_directory:
            folder_icon = self._get_folder_icon((40, 40))
            thumbnail.configure(image=folder_icon)
            thumbnail.image = folder_icon
        else:
            thumb_image = self._load_ctk_image(path, (40, 40))
            if thumb_image is not None:
                thumbnail.configure(image=thumb_image)
                thumbnail.image = thumb_image
            else:
                thumbnail.configure(text="IMG")

        name_label = ctk.CTkLabel(
            row,
            text=path.name,
            text_color=TEXT,
            font=ui_font(14, "semibold"),
            anchor="w",
        )
        name_label.grid(row=0, column=1, sticky="w", pady=(8, 0))

        meta_text = "Folder"
        if not is_directory:
            try:
                file_size = self._format_file_size(path.stat().st_size)
            except OSError:
                file_size = "Unknown size"
            meta_text = f"{path.suffix.upper().lstrip('.')} image | {file_size}"

        meta_label = ctk.CTkLabel(
            row,
            text=meta_text,
            text_color=TEXT_FAINT,
            font=ui_font(11),
            anchor="w",
        )
        meta_label.grid(row=1, column=1, sticky="w", pady=(0, 8))

        if is_directory:
            action = lambda p=path: self._open_directory(p)
            button_text = "Open"
            role = "secondary"
        else:
            action = lambda p=path: self._select_file(p)
            button_text = "Selected" if is_selected else "Select"
            role = "primary" if is_selected else "secondary"

        build_button(
            row,
            button_text,
            role=role,
            width=84,
            height=30,
            command=action,
        ).grid(row=0, column=2, rowspan=2, padx=10, pady=8)

        clickable_widgets = (row, thumbnail, name_label, meta_label)
        if is_directory:
            for widget in clickable_widgets:
                widget.bind("<Double-Button-1>", lambda _event, p=path: self._open_directory(p))
        else:
            for widget in clickable_widgets:
                widget.bind("<Button-1>", lambda _event, p=path: self._select_file(p))
                widget.bind("<Double-Button-1>", lambda _event, p=path: self._submit_selected(p))

    def _update_preview(self) -> None:
        if self.selected_path is None:
            self.preview_image_ref = None
            self.preview_image_label.configure(
                image=None,
                text="Select a logo file to preview.",
                text_color=TEXT_FAINT,
            )
            self.preview_name_label.configure(text="No file selected", text_color=TEXT_MUTED)
            self.preview_meta_label.configure(
                text="PNG and JPG thumbnails appear in the list.",
                text_color=TEXT_FAINT,
            )
            return

        path = self.selected_path
        try:
            file_stat = path.stat()
            with Image.open(path) as image:
                image = ImageOps.exif_transpose(image)
                width, height = image.size
        except Exception as exc:
            self.preview_image_ref = None
            self.preview_image_label.configure(
                image=None,
                text="Preview unavailable",
                text_color=DANGER,
            )
            self.preview_name_label.configure(text=path.name, text_color=TEXT)
            self.preview_meta_label.configure(text=f"Unable to load image: {exc}", text_color=DANGER)
            return

        preview_image = self._load_ctk_image(path, (320, 240))
        self.preview_image_ref = preview_image
        if preview_image is not None:
            self.preview_image_label.configure(image=preview_image, text="")
        else:
            self.preview_image_label.configure(
                image=None,
                text="Preview unavailable",
                text_color=DANGER,
            )

        self.preview_name_label.configure(text=path.name, text_color=TEXT)
        self.preview_meta_label.configure(
            text=(
                f"{width} x {height}px\n"
                f"{self._format_file_size(file_stat.st_size)}\n"
                f"Modified {self._format_timestamp(file_stat.st_mtime)}"
            ),
            text_color=TEXT_MUTED,
        )

    def _submit(self) -> None:
        if self.selected_path is None:
            self._set_status("Select an image file first.", DANGER)
            return
        self.result = str(self.selected_path)
        self.destroy()

    def _close(self) -> None:
        self.destroy()

    def show(self) -> str | None:
        self.wait_window()
        return self.result


class LogViewerModal(ctk.CTkToplevel):
    def __init__(self, master: Any, meta: dict[str, Any]) -> None:
        super().__init__(master)
        self.meta = meta
        self.log_path = Path(str(meta.get("log_file_path", "")))
        native_path = str(meta.get("native_engine_log_file_path", "") or "")
        self.native_log_path = Path(native_path) if native_path else None

        self.title(f"Log Viewer - {meta['test_name']}")
        self.geometry("1000x760")
        self.minsize(820, 600)
        self.transient(master)
        self.configure(fg_color=BG)
        self.after(0, lambda: apply_windows_dark_title_bar(self))

        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 12))
        header.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(header, text=terminal_title(meta["test_name"]), font=ui_font(20, "semibold"), text_color=heading_color()).grid(row=0, column=0, sticky="w")
        status = str(meta.get("status", "-"))
        status_fg, status_bg = status_colors(status)
        ctk.CTkLabel(
            header,
            text=status_tag(status),
            font=ui_font(11, "semibold"),
            text_color=status_fg,
            fg_color=status_bg,
            corner_radius=6,
            height=22,
        ).grid(row=0, column=1, sticky="w", padx=(12, 0))

        details, details_body = build_panel(self)
        details.grid(row=1, column=0, sticky="ew", padx=24)
        details_body.grid_columnconfigure((0, 1, 2), weight=1, uniform="meta")
        fields = [
            ("Engine", self._format_engine_name(meta.get("engine_name", "-"))),
            ("Runtime", format_duration(float(meta.get("duration_seconds", 0.0) or 0.0))),
            ("Exit code", describe_exit_code(meta["exit_code"]) if isinstance(meta.get("exit_code"), int) else "-"),
            ("Started", self._format_time(meta.get("started_at", "-"))),
            ("Finished", self._format_time(meta.get("finished_at", "-"))),
        ]
        fields.extend((str(role), str(name)) for role, name in (meta.get("tested_hardware") or {}).items())
        for index, (label, value) in enumerate(fields):
            cell = ctk.CTkFrame(details_body, fg_color="transparent")
            row, column = divmod(index, 3)
            span = 2 if label == "Exit code" else 1
            cell.grid(row=row, column=column, columnspan=span, sticky="ew", pady=(0, 10))
            ctk.CTkLabel(cell, text=label.upper(), font=ui_font(10, "semibold"), text_color=TEXT_FAINT, anchor="w").grid(row=0, column=0, sticky="w")
            ctk.CTkLabel(
                cell,
                text=value,
                font=ui_font(13),
                text_color=TEXT,
                anchor="w",
                justify="left",
                wraplength=560 if span == 2 else 270,
            ).grid(row=1, column=0, sticky="w")
        if label_issues := [str(issue) for issue in meta.get("phase_issues") or []]:
            issues_text = "\n".join(f"• {issue}" for issue in label_issues[:4])
            if len(label_issues) > 4:
                issues_text += f"\n• ... and {len(label_issues) - 4} more (see the end of the log)"
            ctk.CTkLabel(
                details_body,
                text=issues_text,
                font=ui_font(12),
                text_color=DANGER,
                anchor="w",
                justify="left",
                wraplength=900,
            ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(0, 10))
        ctk.CTkLabel(details_body, text="COMMAND", font=ui_font(10, "semibold"), text_color=TEXT_FAINT, anchor="w").grid(
            row=3, column=0, columnspan=3, sticky="w"
        )
        ctk.CTkLabel(
            details_body,
            text=self._format_command(meta.get("command_line", "-")),
            font=mono_font(11),
            text_color=TEXT_MUTED,
            justify="left",
            anchor="w",
            wraplength=900,
        ).grid(row=4, column=0, columnspan=3, sticky="w")

        self.textbox = ctk.CTkTextbox(
            self,
            fg_color=CONSOLE,
            border_width=1,
            border_color=BORDER,
            text_color=TEXT,
            font=mono_font(12),
            corner_radius=RADIUS_LARGE,
            wrap="none",
            scrollbar_button_color=BORDER_STRONG,
        )
        self.textbox.grid(row=2, column=0, sticky="nsew", padx=24, pady=12)
        self.textbox.tag_config("error", foreground=DANGER)
        self.textbox.tag_config("note", foreground=TEXT_FAINT)
        self._load_log()

        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.grid(row=3, column=0, sticky="ew", padx=24, pady=(0, 20))
        actions.grid_columnconfigure(10, weight=1)
        build_button(actions, "Copy log", command=self._copy_log, width=110).grid(row=0, column=0, padx=(0, 8))
        build_button(actions, "Open log folder", command=self._open_folder, width=140).grid(row=0, column=1, padx=(0, 8))
        if self.native_log_path is not None:
            build_button(actions, "Open engine log", command=self._open_native_log, width=140).grid(row=0, column=2)
        build_button(actions, "Close", role="primary", command=self.destroy, width=110).grid(row=0, column=11, sticky="e")

        self.bind("<Escape>", lambda _event: self.destroy())
        self.focus()


    @staticmethod
    def _format_engine_name(engine_name: Any) -> str:
        value = str(engine_name or "-").strip()
        if not value or value == "-":
            return "-"
        return Path(value).name or value

    @staticmethod
    def _format_time(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return "-"
        try:
            return datetime.fromisoformat(text).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return text

    @staticmethod
    def _format_command(command_line: Any) -> str:
        text = str(command_line or "").strip()
        if not text:
            return "-"
        text = re.sub(r'(?i)\s*logfile:(?:"[^"]*"|\S+)', "", text)
        text = re.sub(r"[ \t]{2,}", " ", text)
        text = re.sub(r"\s+\|\s+", " | ", text)
        return text.strip() or "-"

    def _load_log(self) -> None:
        try:
            with open(self.log_path, "r", encoding="utf-8", errors="replace") as handle:
                content_lines = handle.readlines()
        except OSError as exc:
            content_lines = [f"Failed to load log file: {exc}"]

        truncated_count = max(0, len(content_lines) - LOG_VIEWER_MAX_LINES)
        if truncated_count:
            content_lines = content_lines[-LOG_VIEWER_MAX_LINES:]

        if not any(line.strip() for line in content_lines):
            self.textbox.insert(
                "end",
                "This stress engine may not produce console output when it completes successfully without errors.",
                "note",
            )
            self.textbox.configure(state="disabled")
            return

        test_key = str(self.meta.get("test_key") or self.log_path.stem.replace("_stdout", "").replace("_ycruncher", ""))

        if truncated_count:
            self.textbox.insert(
                "end",
                f"[{truncated_count} earlier lines omitted - use Open Log Folder for the full file]\n",
                "note",
            )

        # Group consecutive lines with the same tag so the textbox gets few large inserts.
        chunk: list[str] = []
        chunk_is_error = False
        for line in content_lines:
            text = line[len(STDERR_PREFIX):] if line.startswith(STDERR_PREFIX) else line
            line_is_error = is_error_line(text, test_key)
            if chunk and line_is_error != chunk_is_error:
                self.textbox.insert("end", "".join(chunk), "error" if chunk_is_error else None)
                chunk = []
            chunk_is_error = line_is_error
            chunk.append(line)
        if chunk:
            self.textbox.insert("end", "".join(chunk), "error" if chunk_is_error else None)

        self.textbox.configure(state="disabled")

    def _copy_log(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(self.textbox.get("1.0", "end-1c"))

    def _open_folder(self) -> None:
        if self.log_path.parent.exists():
            self._start_file(self.log_path.parent)

    def _open_native_log(self) -> None:
        if self.native_log_path is not None and self.native_log_path.exists():
            self._start_file(self.native_log_path)

    def _start_file(self, path: Path) -> None:
        try:
            os.startfile(path)
        except (OSError, AttributeError) as exc:
            AppMessageDialog(self, "Unable to open", f"Could not open {path}:\n{exc}", dialog_type="error").show()


class TelemetrySettingsWindow(ctk.CTkToplevel):
    def __init__(self, master: "StressTesterApp") -> None:
        super().__init__(master)
        self.app = master
        self.app.telemetry_window = self
        self.title("Assign Sensors")
        self.geometry("880x720")
        self.minsize(760, 560)
        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Escape>", lambda _event: self._on_close())
        self.configure(fg_color=BG)
        self.after(0, lambda: apply_windows_dark_title_bar(self))

        self.mapping_vars: dict[str, ctk.StringVar] = {}
        self.mapping_menus: dict[str, ctk.CTkOptionMenu] = {}
        self._wrap_update_job: str | None = None

        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.bind("<Configure>", self._on_window_configure)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 12))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text=terminal_title("Assign Sensors"), font=ui_font(20, "semibold"), text_color=heading_color(), anchor="w").grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(
            header,
            text="Choose which LibreHardwareMonitor sensor feeds each reading. Temperatures drive the thermal kill switch.",
            font=ui_font(12),
            text_color=TEXT_MUTED,
            anchor="w",
            justify="left",
        ).grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.status_label = ctk.CTkLabel(header, text="", font=ui_font(12), text_color=WARNING, anchor="w", justify="left")
        self.status_label.grid(row=2, column=0, sticky="ew", pady=(10, 0))

        mapping_frame = ctk.CTkScrollableFrame(
            self,
            fg_color=PANEL,
            corner_radius=RADIUS_LARGE,
            border_width=1,
            border_color=BORDER,
            scrollbar_button_color=BORDER_STRONG,
            scrollbar_button_hover_color=TEXT_FAINT,
        )
        mapping_frame.grid(row=1, column=0, sticky="nsew", padx=24)
        mapping_frame.grid_columnconfigure(0, weight=1)
        for row_index, category in enumerate(TELEMETRY_CATEGORIES):
            if row_index:
                build_divider(mapping_frame).grid(row=row_index * 2 - 1, column=0, sticky="ew", padx=8)
            row = ctk.CTkFrame(mapping_frame, fg_color="transparent")
            row.grid(row=row_index * 2, column=0, sticky="ew", padx=8, pady=8)
            row.grid_columnconfigure(0, weight=1)

            ctk.CTkLabel(row, text=category.label, font=ui_font(14), text_color=TEXT, anchor="w").grid(row=0, column=0, sticky="w")
            var = ctk.StringVar(value=UNASSIGNED_SENSOR_OPTION)
            menu = build_option_menu(
                row,
                variable=var,
                values=[UNASSIGNED_SENSOR_OPTION],
                width=420,
                command=lambda _selected, category_key=category.key: self._on_mapping_changed(category_key),
            )
            menu.grid(row=0, column=1, sticky="e", padx=(16, 0))
            self.mapping_vars[category.key] = var
            self.mapping_menus[category.key] = menu

        footer = ctk.CTkFrame(self, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew", padx=24, pady=(14, 20))
        footer.grid_columnconfigure(10, weight=1)
        build_button(footer, "Refresh sensors", command=self._refresh_sensors, width=140).grid(row=0, column=0, padx=(0, 8))
        if self.app.pawnio_installer_path is not None:
            build_button(footer, "Install PawnIO driver", role="danger", command=self._install_pawnio_driver, width=170).grid(row=0, column=1)
        build_button(footer, "Done", role="primary", command=self._on_close, width=110).grid(row=0, column=11, sticky="e")

        self.app._refresh_telemetry_mapping_state(show_feedback=False, refresh_inventory=True)
        self.after(0, self._refresh_label_wraps)
        self.focus()

    def _on_window_configure(self, _event: Any) -> None:
        self._schedule_wrap_refresh()

    def _schedule_wrap_refresh(self) -> None:
        if self._wrap_update_job is not None:
            self.after_cancel(self._wrap_update_job)
        self._wrap_update_job = self.after(40, self._refresh_label_wraps)

    def _refresh_label_wraps(self) -> None:
        self._wrap_update_job = None
        if not self.winfo_exists():
            return
        self.update_idletasks()
        if self.status_label.winfo_exists():
            self.status_label.configure(wraplength=max(200, self.status_label.master.winfo_width() - 8))

    def apply_mapping_state(self) -> None:
        for category in TELEMETRY_CATEGORIES:
            options = list(self.app.telemetry_sensor_choices.get(category.key, {UNASSIGNED_SENSOR_OPTION: None}).keys())
            if not options:
                options = [UNASSIGNED_SENSOR_OPTION]
            self.mapping_menus[category.key].configure(values=options)
            selected = self.app.telemetry_selected_labels.get(category.key, UNASSIGNED_SENSOR_OPTION)
            if selected not in options:
                selected = UNASSIGNED_SENSOR_OPTION
            self.mapping_vars[category.key].set(selected)

        self.status_label.configure(
            text=self.app.telemetry_status_summary,
            text_color=self.app.telemetry_status_color,
        )
        self._schedule_wrap_refresh()

    def _refresh_sensors(self) -> None:
        self.app._refresh_telemetry_mapping_state(show_feedback=True, refresh_inventory=True)

    def _install_pawnio_driver(self) -> None:
        self.app._launch_pawnio_installer(prompt_first=False)

    def _on_mapping_changed(self, category_key: str) -> None:
        if category_key not in self.mapping_vars:
            return
        selected_labels = {
            category.key: self.mapping_vars[category.key].get()
            for category in TELEMETRY_CATEGORIES
        }
        mappings = self.app._collect_telemetry_mappings_from_labels(selected_labels)
        self.app._save_telemetry_mappings(mappings)

    def _on_close(self) -> None:
        if self._wrap_update_job is not None:
            self.after_cancel(self._wrap_update_job)
            self._wrap_update_job = None
        self.app.telemetry_window = None
        self.destroy()


class ThermalSettingsWindow(ctk.CTkToplevel):
    def __init__(self, master: "StressTesterApp") -> None:
        super().__init__(master)
        self.app = master
        self.app.thermal_window = self
        self.title("Thermal Settings")
        self.geometry("440x270")
        self.resizable(False, False)
        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Escape>", lambda _event: self._on_close())

        self.cpu_limit_var = ctk.StringVar(value=str(self.app.settings.get("cpu_temp_limit_c", "")))
        self.gpu_limit_var = ctk.StringVar(value=str(self.app.settings.get("gpu_temp_limit_c", "")))

        content = _build_dialog_shell(self, "Thermal Settings", ACCENT)
        ctk.CTkLabel(
            content,
            text="A running test is stopped when a temperature goes above its limit.",
            font=ui_font(12),
            text_color=TEXT_MUTED,
            anchor="w",
            justify="left",
            wraplength=400,
        ).grid(row=1, column=0, sticky="w", pady=(4, 14))

        fields = ctk.CTkFrame(content, fg_color="transparent")
        fields.grid(row=2, column=0, sticky="ew")
        fields.grid_columnconfigure(0, weight=1)
        for row, (label, variable, ceiling) in enumerate(
            (("CPU limit", self.cpu_limit_var, CPU_TEMP_LIMIT_C), ("GPU limit", self.gpu_limit_var, GPU_TEMP_LIMIT_C))
        ):
            ctk.CTkLabel(fields, text=label, font=ui_font(13), text_color=TEXT, anchor="w").grid(row=row, column=0, sticky="w", pady=5)
            build_entry(fields, variable, width=80).grid(row=row, column=1, pady=5)
            ctk.CTkLabel(fields, text=f"°C   (max {ceiling:g})", font=ui_font(12), text_color=TEXT_FAINT, anchor="w").grid(
                row=row, column=2, sticky="w", padx=(8, 0), pady=5
            )

        footer = ctk.CTkFrame(content, fg_color="transparent")
        footer.grid(row=3, column=0, sticky="e", pady=(18, 0))
        build_button(footer, "Cancel", role="secondary", width=100, command=self._on_close).grid(row=0, column=0)
        build_button(footer, "Save", role="primary", width=100, command=self._save).grid(row=0, column=1, padx=(8, 0))

        self.after(10, self._activate_modal)

    def _activate_modal(self) -> None:
        self.grab_set()
        self.focus_force()

    def _save(self) -> None:
        try:
            cpu_limit = float(self.cpu_limit_var.get().strip())
            gpu_limit = float(self.gpu_limit_var.get().strip())
        except ValueError:
            self.app._show_error("Validation Error", "CPU and GPU thermal limits must be numeric values.")
            return

        if not (math.isfinite(cpu_limit) and math.isfinite(gpu_limit)) or cpu_limit <= 0 or gpu_limit <= 0:
            self.app._show_error("Validation Error", "CPU and GPU thermal limits must be greater than zero.")
            return

        if cpu_limit > CPU_TEMP_LIMIT_C or gpu_limit > GPU_TEMP_LIMIT_C:
            self.app._show_error(
                "Validation Error",
                f"Thermal limits cannot exceed the safety ceilings of {CPU_TEMP_LIMIT_C:g} C (CPU) and {GPU_TEMP_LIMIT_C:g} C (GPU).",
            )
            return

        self.app.settings["cpu_temp_limit_c"] = cpu_limit
        self.app.settings["gpu_temp_limit_c"] = gpu_limit
        self.app.orchestrator.configure_thermal_limits(self.app.settings)
        self.app.orchestrator.save_settings(self.app.settings)
        self._on_close()

    def _on_close(self) -> None:
        self.app.thermal_window = None
        self.destroy()


class StressTesterApp(ctk.CTk):
    def __init__(self, root_path: Path) -> None:
        super().__init__()
        self.root_path = root_path
        self.orchestrator = StressOrchestrator(root_path)
        self.title("RWS Stress Tester")
        self.geometry("1280x860")
        self.minsize(1040, 700)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if os.name == "nt":
            self.after(0, lambda: self.state("zoomed"))
        self.after(0, lambda: apply_windows_dark_title_bar(self))

        self.settings = self.orchestrator.load_settings()
        apply_theme(self.settings.get("ui_theme") or DEFAULT_THEME)
        self.configure(fg_color=BG)
        self.orchestrator.configure_thermal_limits(self.settings)
        self.presets = self.orchestrator.load_presets()
        self.current_summary: dict[str, Any] | None = None

        self.test_vars: dict[str, ctk.BooleanVar] = {}
        self.duration_vars: dict[str, ctk.StringVar] = {}
        self.phase_cards: dict[str, TestPhaseRow] = {}
        self.telemetry_labels: dict[str, ctk.CTkLabel] = {}
        self.telemetry_bars: dict[str, ctk.CTkProgressBar] = {}
        self.telemetry_sensor_choices: dict[str, dict[str, str | None]] = {}
        self.telemetry_selected_labels: dict[str, str] = {
            category.key: UNASSIGNED_SENSOR_OPTION for category in TELEMETRY_CATEGORIES
        }
        self.telemetry_inventory: list[dict[str, Any]] = []
        self.telemetry_status_summary = ""
        self.telemetry_status_brief = ""
        self.telemetry_status_color = WARNING
        self.telemetry_header_status_text = ""
        self.telemetry_header_status_color = WARNING
        self.telemetry_window: TelemetrySettingsWindow | None = None
        self.thermal_window: ThermalSettingsWindow | None = None
        self.pawnio_installer_path = self._find_pawnio_installer()
        self.pawnio_status: dict[str, Any] = {"is_installed": None, "version": "", "error": ""}
        self._pawnio_prompted_this_launch = False
        self.default_phase_duration_minutes = "5"
        
        # Calculate safe RAM
        available_mb = int(psutil.virtual_memory().available / (1024 * 1024))
        safe_ram_gb = max(1.0, round((available_mb - 2560) / 1024, 1))
        self.default_ram_gb = str(safe_ram_gb)
        self.default_gpu_resolution = "1280x720"
        self.ram_gb_var = ctk.StringVar(value=self.default_ram_gb)
        self.gpu_res_var = ctk.StringVar(value=self.default_gpu_resolution)
        self.detected_vram_gb = detect_vram_gb()
        self.vram_size_options = [str(size) for size in FURMARK_VRAM_TEST_SIZES_GB]
        self.default_vram_gb = str(recommended_vram_test_gb(self.detected_vram_gb))
        self.vram_gb_var = ctk.StringVar(value=self.default_vram_gb)

        drive_options = [p.device for p in psutil.disk_partitions(all=False) if p.fstype]
        default_drive = drive_options[0] if drive_options else "C:\\"
        self.default_target_drive = default_drive
        self.target_drive_var = ctk.StringVar(value=self.default_target_drive)
        self.drive_options = drive_options if drive_options else ["C:\\"]

        self.completed_logs_data: list[dict[str, Any]] = []

        self._build_shell()
        self._load_presets_into_menu()
        self._refresh_telemetry_mapping_state(show_feedback=False, refresh_inventory=True, prompt_for_pawnio=False)
        self._show_crash_recovery_if_needed()
        self.after(150, self._maybe_prompt_pawnio_install)
        self.after(250, self._drain_events)

    def _build_shell(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.setup_frame = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        self.dashboard_frame = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        self.setup_frame.grid(row=0, column=0, sticky="nsew")
        self.dashboard_frame.grid(row=0, column=0, sticky="nsew")

        self._build_setup_screen()
        self._build_dashboard_screen()
        self.dashboard_frame.grid_remove()

    def _build_setup_screen(self) -> None:
        frame = self.setup_frame
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)

        # ---- Header: app name, sensor status, settings buttons
        header = ctk.CTkFrame(frame, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=28, pady=(20, 14))
        header.grid_columnconfigure(1, weight=1)
        brand = ctk.CTkFrame(header, fg_color="transparent")
        brand.grid(row=0, column=0, sticky="w")
        brand_font = ui_font(24, "bold") if TERMINAL_STYLE else ui_font(22, "semibold")
        ctk.CTkLabel(brand, text=display_case("RWS Stress Tester"), font=brand_font, text_color=heading_color(), anchor="w").grid(
            row=0, column=0, sticky="w"
        )
        ctk.CTkLabel(
            brand,
            text="hardware stability diagnostics" if TERMINAL_STYLE else "Hardware stability diagnostics",
            font=ui_font(12),
            text_color=TEXT_MUTED,
            anchor="w",
        ).grid(row=1, column=0, columnspan=2, sticky="w")
        if TERMINAL_STYLE:
            self.cursor_label = ctk.CTkLabel(brand, text="_", font=brand_font, text_color=ACCENT, anchor="w", width=16)
            self.cursor_label.grid(row=0, column=1, sticky="w")
            self._blink_cursor(self.cursor_label)
        self.telemetry_header_status_label = ctk.CTkLabel(header, text="", font=ui_font(12), text_color=WARNING, anchor="e")
        self.telemetry_header_status_label.grid(row=0, column=1, sticky="e", padx=(12, 14))
        self.theme_switch = ctk.CTkSegmentedButton(
            header,
            values=list(THEME_LABELS.values()),
            command=self._on_theme_selected,
            height=34,
            corner_radius=RADIUS,
            font=ui_font(12),
            fg_color=CARD,
            selected_color=BORDER_STRONG,
            selected_hover_color=BORDER_STRONG,
            unselected_color=CARD,
            unselected_hover_color=CARD_HOVER,
            text_color=TEXT,
        )
        self.theme_switch.set(THEME_LABELS[CURRENT_THEME])
        self.theme_switch.grid(row=0, column=2, padx=(0, 14))
        build_button(header, "Thermal settings", command=self._open_thermal_settings, width=140).grid(row=0, column=3, padx=(0, 8))
        build_button(header, "Assign sensors", command=self._open_telemetry_settings, width=140).grid(row=0, column=4)

        content = ctk.CTkScrollableFrame(
            frame,
            fg_color=BG,
            corner_radius=0,
            scrollbar_button_color=BORDER_STRONG,
            scrollbar_button_hover_color=TEXT_FAINT,
        )
        content.grid(row=1, column=0, sticky="nsew", padx=(16, 4), pady=(0, 16))
        content.grid_columnconfigure(0, weight=3, uniform="setup")
        content.grid_columnconfigure(1, weight=2, uniform="setup")

        # ---- Left: test phases
        def phase_actions(panel_header: Any) -> None:
            self.selection_summary_label = ctk.CTkLabel(panel_header, text="", font=ui_font(12), text_color=TEXT_MUTED)
            self.selection_summary_label.grid(row=0, column=1, sticky="e")

        phases_panel, phases_body = build_panel(content, "Test phases", "Phases run in this order. Switch on the ones to run and set their duration.", actions=phase_actions)
        phases_panel.grid(row=0, column=0, sticky="nsew", padx=(12, 8), pady=0)
        phases_body.grid_columnconfigure(0, weight=1)

        test_rows = [
            ("cpu", "CPU", "y-cruncher · cores, boost clocks and voltage", None),
            ("ram", "RAM", "y-cruncher · memory and memory controller", {"label": "GB", "kind": "entry", "variable": self.ram_gb_var, "width": 70}),
            ("gpu", "GPU", "FurMark · core, power and cooling", {"label": "Resolution", "kind": "entry", "variable": self.gpu_res_var, "width": 100}),
            ("vram", "VRAM", "FurMark · video memory", {"label": "GB", "kind": "menu", "variable": self.vram_gb_var, "values": self.vram_size_options, "width": 70}),
            ("storage_read", "Storage read", "DiskSpd · random reads", {"label": "Drive", "kind": "menu", "variable": self.target_drive_var, "values": self.drive_options, "width": 90}),
            ("storage_write", "Storage write", "DiskSpd · random writes", {"label": "Drive", "kind": "menu", "variable": self.target_drive_var, "values": self.drive_options, "width": 90}),
            ("psu", "PSU", "y-cruncher + FurMark · combined peak power", None),
        ]
        self.phase_cards.clear()
        for index, (key, title, description, extra) in enumerate(test_rows):
            if key not in self.test_vars:  # kept when the screen is rebuilt for another theme
                self.test_vars[key] = ctk.BooleanVar(value=False)
                self.duration_vars[key] = ctk.StringVar(value=self.default_phase_duration_minutes)
                self.test_vars[key].trace_add("write", lambda *_args: self._refresh_selection_summary())
                self.duration_vars[key].trace_add("write", lambda *_args: self._refresh_selection_summary())
            row = TestPhaseRow(phases_body, title, description, self.test_vars[key], self.duration_vars[key], extra)
            row.grid(row=index, column=0, sticky="ew", pady=2)
            self.phase_cards[key] = row

        # ---- Right: presets, certificate, start
        side = ctk.CTkFrame(content, fg_color="transparent")
        side.grid(row=0, column=1, sticky="nsew", padx=(8, 12))
        side.grid_columnconfigure(0, weight=1)

        preset_panel, preset_body = build_panel(side, "Presets")
        preset_panel.grid(row=0, column=0, sticky="ew")
        preset_body.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform="presetbuttons")
        if not hasattr(self, "preset_var"):
            self.preset_var = ctk.StringVar(value="Custom")
        self.preset_menu = build_option_menu(preset_body, variable=self.preset_var, values=["Custom"], command=self._apply_preset, width=200)
        self.preset_menu.grid(row=0, column=0, columnspan=4, sticky="ew", pady=(0, 10))
        for column, (label, command) in enumerate(
            (
                ("Save as", self._save_preset),
                ("Load", lambda: self._apply_preset(self.preset_var.get())),
                ("Delete", self._delete_preset),
                ("Reset", self._reset_test_settings),
            )
        ):
            build_button(preset_body, label, command=command, width=60, height=30).grid(
                row=1, column=column, sticky="ew", padx=(0 if column == 0 else 4, 0 if column == 3 else 4)
            )

        certificate_panel, certificate_body = build_panel(side, "Certificate")
        certificate_panel.grid(row=1, column=0, sticky="ew", pady=(16, 0))
        certificate_body.grid_columnconfigure(1, weight=1)
        if not hasattr(self, "logo_var"):
            self.logo_var = ctk.StringVar(value=self.settings.get("logo_path", ""))
            self.generate_report_var = ctk.BooleanVar(value=True)
            self.include_customer_name_var = ctk.BooleanVar(value=bool(self.settings.get("include_customer_name", False)))
            self.customer_name_var = ctk.StringVar(value=self.settings.get("customer_name", ""))
            self.include_machine_name_var = ctk.BooleanVar(value=bool(self.settings.get("include_machine_name", False)))
            self.certificate_machine_name_var = ctk.StringVar(value=self.settings.get("certificate_machine_name", ""))

        build_checkbox(certificate_body, "Create PDF certificate", self.generate_report_var).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 12)
        )
        ctk.CTkLabel(certificate_body, text="Logo", font=ui_font(13), text_color=TEXT_MUTED, anchor="w").grid(row=1, column=0, sticky="w", pady=4)
        self.logo_entry = build_entry(certificate_body, self.logo_var, width=160, justify="left")
        self.logo_entry.grid(row=1, column=1, sticky="ew", padx=(12, 8), pady=4)
        build_button(certificate_body, "Browse", command=self._pick_logo, width=76, height=32).grid(row=1, column=2, pady=4)

        build_checkbox(certificate_body, "Customer", self.include_customer_name_var, command=self._sync_certificate_field_states).grid(
            row=2, column=0, sticky="w", pady=4
        )
        self.customer_name_entry = build_entry(certificate_body, self.customer_name_var, width=160, justify="left")
        self.customer_name_entry.grid(row=2, column=1, columnspan=2, sticky="ew", padx=(12, 0), pady=4)
        build_checkbox(certificate_body, "Machine", self.include_machine_name_var, command=self._sync_certificate_field_states).grid(
            row=3, column=0, sticky="w", pady=4
        )
        self.certificate_machine_name_entry = build_entry(certificate_body, self.certificate_machine_name_var, width=160, justify="left")
        self.certificate_machine_name_entry.grid(row=3, column=1, columnspan=2, sticky="ew", padx=(12, 0), pady=4)
        self._sync_certificate_field_states()

        start_panel, start_body = build_panel(side)
        start_panel.grid(row=2, column=0, sticky="ew", pady=(16, 0))
        start_body.grid_columnconfigure(0, weight=1)
        self.start_summary_label = ctk.CTkLabel(start_body, text="", font=ui_font(13), text_color=TEXT_MUTED, anchor="w", justify="left")
        self.start_summary_label.grid(row=0, column=0, sticky="w", pady=(0, 12))
        self.start_button = build_button(
            start_body,
            "> START DIAGNOSTICS" if TERMINAL_STYLE else "Start diagnostics",
            role="primary",
            command=self._start_diagnostics,
            height=46,
            font=ui_font(15, "semibold"),
        )
        self.start_button.grid(row=1, column=0, sticky="ew")
        self._refresh_selection_summary()

    def _on_theme_selected(self, label: str) -> None:
        name = next((key for key, value in THEME_LABELS.items() if value == label), DEFAULT_THEME)
        if name != CURRENT_THEME:
            self._set_theme(name)

    def _set_theme(self, name: str) -> None:
        """Rebuild both screens in another skin. Form values, completed logs and console output are kept."""
        if self.orchestrator.is_session_running():
            self.theme_switch.set(THEME_LABELS[CURRENT_THEME])
            return
        for window in (self.telemetry_window, self.thermal_window):
            if window is not None and window.winfo_exists():
                window.destroy()
        self.telemetry_window = None
        self.thermal_window = None

        self.settings["ui_theme"] = apply_theme(name)
        try:
            self.orchestrator.save_settings(self.settings)
        except OSError:
            pass  # the skin still changes for this session

        console_text = self.console.get("1.0", "end-1c")
        showing_dashboard = self.dashboard_frame.winfo_ismapped()
        self.setup_frame.destroy()
        self.dashboard_frame.destroy()
        self.telemetry_labels.clear()
        self.telemetry_bars.clear()
        self.configure(fg_color=BG)
        self._build_shell()
        self._load_presets_into_menu()
        self._apply_telemetry_state_to_ui()
        if console_text.strip():
            self.console.configure(state="normal")
            self.console.insert("end", console_text + "\n")
            self.console.configure(state="disabled")
        if showing_dashboard:
            self.setup_frame.grid_remove()
            self.dashboard_frame.grid()

    def _blink_cursor(self, cursor: ctk.CTkLabel) -> None:
        if not cursor.winfo_exists():
            return  # rebuilt for another theme
        cursor.configure(text=" " if cursor.cget("text") == "_" else "_")
        self._cursor_job = self.after(530, lambda: self._blink_cursor(cursor))

    def destroy(self) -> None:
        if getattr(self, "_cursor_job", None):
            self.after_cancel(self._cursor_job)
        super().destroy()

    def _refresh_selection_summary(self) -> None:
        if not hasattr(self, "start_summary_label"):
            return
        selected = [key for key, var in self.test_vars.items() if var.get()]
        minutes = 0
        for key in selected:
            parsed = self._parse_duration_minutes(self.duration_vars[key].get())
            minutes += parsed or 0
        if not selected:
            summary = "No phases selected"
            detail = "Switch on at least one test phase."
        else:
            hours, mins = divmod(minutes, 60)
            total = f"{hours} h {mins:02d} min" if hours else f"{mins} min"
            summary = f"{len(selected)} of {len(self.test_vars)} selected · {total}"
            detail = f"{len(selected)} phase{'s' if len(selected) != 1 else ''} · about {total} of testing"
        self.selection_summary_label.configure(text=summary)
        self.start_summary_label.configure(text=detail)
        self.start_button.configure(state="normal" if selected else "disabled")

    def _build_dashboard_screen(self) -> None:
        frame = self.dashboard_frame
        frame.grid_columnconfigure(0, weight=3)
        frame.grid_columnconfigure(1, weight=2, minsize=320)
        frame.grid_rowconfigure(3, weight=1)

        # ---- Header: current phase and timers
        header = ctk.CTkFrame(frame, fg_color="transparent")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=28, pady=(20, 10))
        header.grid_columnconfigure(0, weight=1)
        status = ctk.CTkFrame(header, fg_color="transparent")
        status.grid(row=0, column=0, sticky="w")
        self.phase_index_label = ctk.CTkLabel(status, text="WAITING", font=ui_font(11, "semibold"), text_color=TEXT_FAINT, anchor="w")
        self.phase_index_label.grid(row=0, column=0, columnspan=2, sticky="w")
        self.phase_label = ctk.CTkLabel(
            status,
            text=display_case("Ready"),
            font=ui_font(26, "bold") if TERMINAL_STYLE else ui_font(24, "semibold"),
            text_color=heading_color(),
            anchor="w",
        )
        self.phase_label.grid(row=1, column=0, columnspan=2, sticky="w")
        # Processor / graphics card the current phase runs on, e.g. "GPU  NVIDIA GeForce RTX 3080".
        self.tested_hardware_label = ctk.CTkLabel(status, text="", font=ui_font(15, "semibold"), text_color=ACCENT, anchor="w", justify="left")
        self.tested_hardware_label.grid(row=2, column=0, columnspan=2, sticky="w")
        self.error_flag_label = ctk.CTkLabel(status, text="", font=ui_font(12, "semibold"), text_color=DANGER, anchor="w")
        self.error_flag_label.grid(row=3, column=0, sticky="w", padx=(0, 10))
        self.phase_status_label = ctk.CTkLabel(status, text="", font=ui_font(12), text_color=TEXT_MUTED, anchor="w", justify="left")
        self.phase_status_label.grid(row=3, column=1, sticky="w")

        timers = ctk.CTkFrame(header, fg_color="transparent")
        timers.grid(row=0, column=1, sticky="e")
        self.countdown_label = self._build_timer_tile(timers, 0, "PHASE REMAINING")
        self.full_matrix_countdown_label = self._build_timer_tile(timers, 1, "RUN REMAINING")

        self.phase_progress = ctk.CTkProgressBar(frame, height=4, corner_radius=2, fg_color=BORDER, progress_color=ACCENT)
        self.phase_progress.set(0)
        self.phase_progress.grid(row=1, column=0, columnspan=2, sticky="ew", padx=28, pady=(0, 16))

        # ---- Telemetry tiles
        telemetry_panel, telemetry_body = build_panel(frame, "Live telemetry")
        telemetry_panel.grid(row=2, column=0, sticky="nsew", padx=(28, 8), pady=(0, 16))
        telemetry_body.grid_columnconfigure((0, 1, 2), weight=1, uniform="telemetry")
        tiles = [
            ("cpu_temp", "CPU temperature"),
            ("cpu_load", "CPU load"),
            ("cpu_power", "CPU power"),
            ("gpu_temp", "GPU temperature"),
            ("gpu_load", "GPU load"),
            ("gpu_power", "GPU power"),
            ("ram_load", "RAM load"),
            ("storage_load", "Storage load"),
            ("total_power", "Total power"),
        ]
        for index, (key, label) in enumerate(tiles):
            tile = ctk.CTkFrame(telemetry_body, fg_color=CARD, corner_radius=RADIUS)
            row, column = divmod(index, 3)
            tile.grid(row=row, column=column, sticky="nsew", padx=4, pady=4)
            tile.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(tile, text=label, font=ui_font(12), text_color=TEXT_MUTED, anchor="w", height=20).grid(row=0, column=0, sticky="w", padx=12, pady=(6, 0))
            value_label = ctk.CTkLabel(tile, text="-", font=ui_font(18, "semibold"), text_color=TEXT, anchor="w", height=26)
            value_label.grid(row=1, column=0, sticky="w", padx=12)
            bar = ctk.CTkProgressBar(tile, height=4, corner_radius=2, fg_color=BORDER, progress_color=ACCENT)
            bar.set(0)
            bar.grid(row=2, column=0, sticky="ew", padx=12, pady=(4, 10))
            self.telemetry_labels[key] = value_label
            self.telemetry_bars[key] = bar

        # ---- Completed phases
        def logs_actions(panel_header: Any) -> None:
            build_button(panel_header, "Refresh", role="ghost", command=self._refresh_completed_logs_ui, width=70, height=26).grid(
                row=0, column=1, sticky="e"
            )

        logs_panel, logs_body = build_panel(frame, "Completed phases", actions=logs_actions)
        logs_panel.grid(row=2, column=1, rowspan=2, sticky="nsew", padx=(8, 28), pady=(0, 16))
        logs_body.grid_columnconfigure(0, weight=1)
        logs_body.grid_rowconfigure(1, weight=1)
        self.logs_status_label = ctk.CTkLabel(logs_body, text="", font=ui_font(12), text_color=TEXT_MUTED, anchor="w", justify="left")
        self.logs_status_label.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.dashboard_logs_list = ctk.CTkScrollableFrame(
            logs_body,
            fg_color="transparent",
            corner_radius=0,
            scrollbar_button_color=BORDER_STRONG,
            scrollbar_button_hover_color=TEXT_FAINT,
        )
        self.dashboard_logs_list.grid(row=1, column=0, sticky="nsew")
        self.dashboard_logs_list.grid_columnconfigure(0, weight=1)

        # ---- Engine output
        console_panel, console_body = build_panel(frame, "Engine output")
        console_panel.grid(row=3, column=0, sticky="nsew", padx=(28, 8), pady=(0, 16))
        console_body.grid_rowconfigure(0, weight=1)
        console_body.grid_columnconfigure(0, weight=1)
        self.console = ctk.CTkTextbox(
            console_body,
            state="disabled",
            fg_color=CONSOLE,
            border_width=1,
            border_color=BORDER,
            text_color=TEXT_MUTED,
            font=mono_font(12),
            corner_radius=RADIUS,
            wrap="word",
            scrollbar_button_color=BORDER_STRONG,
        )
        self.console.tag_config("error", foreground=DANGER)
        self.console.grid(row=0, column=0, sticky="nsew")

        # ---- Footer actions
        footer = ctk.CTkFrame(frame, fg_color="transparent")
        footer.grid(row=4, column=0, columnspan=2, sticky="ew", padx=28, pady=(0, 20))
        footer.grid_columnconfigure(1, weight=1)
        self.abort_button = build_button(footer, "Abort session", role="danger", command=self.orchestrator.abort_session, width=140)
        self.abort_button.grid(row=0, column=0, sticky="w")
        self.back_to_setup_button = build_button(footer, "Back to setup", role="primary", command=self._back_to_setup, width=140, state="disabled")
        self.back_to_setup_button.grid(row=0, column=2, sticky="e")
        self._set_dashboard_controls(session_running=False)
        self._refresh_completed_logs_ui()

    @staticmethod
    def _build_timer_tile(parent: Any, column: int, title: str) -> ctk.CTkLabel:
        tile = ctk.CTkFrame(
            parent,
            fg_color=PANEL,
            corner_radius=RADIUS,
            border_width=1,
            border_color=BORDER_STRONG if TERMINAL_STYLE else BORDER,
            width=170,
            height=70,
        )
        tile.grid(row=0, column=column, padx=(0 if column == 0 else 10, 0))
        tile.grid_propagate(False)
        tile.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(tile, text=title, font=ui_font(10, "semibold"), text_color=TEXT_FAINT).grid(row=0, column=0, pady=(9, 0))
        value = ctk.CTkLabel(tile, text="--:--", font=ui_font(24, "bold") if TERMINAL_STYLE else ui_font(22, "semibold"), text_color=heading_color())
        value.grid(row=1, column=0)
        return value

    def _load_presets_into_menu(self) -> None:
        values = ["Custom", *sorted(self.presets.keys())]
        self.preset_menu.configure(values=values)
        if self.preset_var.get() not in values:
            self.preset_var.set("Custom")

    def _open_telemetry_settings(self) -> None:
        if self.telemetry_window is not None and self.telemetry_window.winfo_exists():
            self.telemetry_window.lift()
            self.telemetry_window.focus()
            return
        TelemetrySettingsWindow(self)

    def _open_thermal_settings(self) -> None:
        if self.thermal_window is not None and self.thermal_window.winfo_exists():
            self.thermal_window.lift()
            self.thermal_window.focus()
            return
        ThermalSettingsWindow(self)

    def _refresh_pawnio_status(self) -> None:
        try:
            self.pawnio_status = self.orchestrator.get_pawnio_status()
        except Exception as exc:
            self.pawnio_status = {"is_installed": None, "version": "", "error": str(exc)}

    def _find_pawnio_installer(self) -> Path | None:
        candidate_paths = [
            self.root_path / "Engines" / "PawnIO_Driver" / "PawnIO_setup.exe",
            self.root_path / "Engines" / "PawnIO_setup.exe",
            self.root_path / "Engines" / "PawnIo.exe",
            self.root_path / "Engines" / "PawnIO.exe",
        ]
        for path in candidate_paths:
            if path.exists():
                return path

        for path in (self.root_path / "Engines").rglob("*PawnIO*.exe"):
            if path.is_file():
                return path
        for path in (self.root_path / "Engines").rglob("*PawnIo*.exe"):
            if path.is_file():
                return path
        return None

    def _cpu_temperature_driver_needed(self) -> bool:
        cpu_temp_category = next((category for category in TELEMETRY_CATEGORIES if category.key == "cpu_temp"), None)
        if cpu_temp_category is None:
            return False

        compatible_sensors = [
            sensor
            for sensor in self.telemetry_inventory
            if self._sensor_is_compatible_with_category(sensor, cpu_temp_category)
        ]
        return bool(compatible_sensors) and all(sensor.get("value") is None for sensor in compatible_sensors)

    def _maybe_prompt_pawnio_install(self) -> None:
        if self._pawnio_prompted_this_launch:
            return
        driver_missing = self.pawnio_status.get("is_installed") is False
        heuristic_driver_needed = self.pawnio_status.get("is_installed") is None and self._cpu_temperature_driver_needed()
        if not driver_missing and not heuristic_driver_needed:
            return

        self._pawnio_prompted_this_launch = True
        if driver_missing:
            should_install = self._show_dialog(
                "PawnIO Driver Not Installed",
                (
                    "PawnIO driver is not installed.\n\n"
                    "Sensors and telemetry might not work correctly until it is installed.\n\n"
                    "After the driver is installed, close the app and launch it again.\n\n"
                    "Do you want to install the PawnIO driver now?"
                ),
                dialog_type="warning",
                buttons=[
                    ("Cancel", "secondary", False),
                    ("Install PawnIO Driver", "danger", True),
                ],
                default_result=False,
            )
        else:
            should_install = self._show_dialog(
                "CPU Telemetry Driver Required",
                (
                    "LibreHardwareMonitor detected CPU temperature sensors, but none of them are returning live data.\n\n"
                    "This usually means the PawnIO driver is not installed.\n\n"
                    "Do you want to launch the PawnIO installer now?"
                ),
                dialog_type="warning",
                buttons=[
                    ("Cancel", "secondary", False),
                    ("Install PawnIO Driver", "danger", True),
                ],
                default_result=False,
            )
        if should_install:
            self._launch_pawnio_installer(prompt_first=False)

    def _launch_pawnio_installer(self, prompt_first: bool = True) -> None:
        installer_path = self.pawnio_installer_path
        if installer_path is None or not installer_path.exists():
            self._show_error(
                "Installer Missing",
                "PawnIO installer was not found in the Engines folder.",
            )
            return

        if prompt_first:
            should_launch = self._ask_yes_no(
                "Install PawnIO Driver",
                "Launch the PawnIO installer now?",
                confirm_label="Launch",
                cancel_label="Cancel",
            )
            if not should_launch:
                return

        result = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            str(installer_path),
            None,
            str(installer_path.parent),
            1,
        )
        if result <= 32:
            self._show_error(
                "Installer Launch Failed",
                f"Unable to launch {installer_path.name}.",
            )
            return
        # Do not show a follow-up modal here; it steals focus back from the installer.

    def _refresh_telemetry_mapping_state(
        self,
        show_feedback: bool = False,
        refresh_inventory: bool = True,
        prompt_for_pawnio: bool = True,
    ) -> None:
        self._refresh_pawnio_status()
        try:
            sensors = self.telemetry_inventory
            if refresh_inventory or not sensors:
                sensors = self.orchestrator.list_telemetry_sensors()
        except Exception as exc:
            self.telemetry_inventory = []
            self.telemetry_sensor_choices = {
                category.key: {UNASSIGNED_SENSOR_OPTION: None}
                for category in TELEMETRY_CATEGORIES
            }
            self.telemetry_selected_labels = {
                category.key: UNASSIGNED_SENSOR_OPTION for category in TELEMETRY_CATEGORIES
            }
            self.telemetry_status_summary = f"Telemetry discovery failed: {exc}"
            self.telemetry_status_brief = "Sensor scan failed"
            self.telemetry_status_color = DANGER
            self.telemetry_header_status_text = "●  Sensor scan failed"
            self.telemetry_header_status_color = DANGER
            self.orchestrator.configure_telemetry(self.settings.get("telemetry_mappings"))
            self._apply_telemetry_state_to_ui()
            return

        self.telemetry_inventory = sensors
        saved_mappings = self.settings.get("telemetry_mappings", {})
        invalid_saved_categories: list[str] = []
        unresolved_categories: list[str] = []
        no_data_categories: list[str] = []
        selected_labels: dict[str, str] = {}
        sensor_choices: dict[str, dict[str, str | None]] = {}
        sensors_by_id = {sensor.get("sensor_id"): sensor for sensor in sensors}

        for category in TELEMETRY_CATEGORIES:
            option_map = self._build_sensor_choices_for_category(category, sensors)
            sensor_choices[category.key] = option_map

            saved_sensor_id = saved_mappings.get(category.key) if isinstance(saved_mappings, dict) else None
            selected_label = self._find_label_for_sensor_id(option_map, saved_sensor_id)
            if selected_label is None:
                selected_label = UNASSIGNED_SENSOR_OPTION
                if saved_sensor_id:
                    invalid_saved_categories.append(category.label)
                else:
                    unresolved_categories.append(category.label)
            elif saved_sensor_id and sensors_by_id.get(saved_sensor_id, {}).get("value") is None:
                no_data_categories.append(category.label)

            selected_labels[category.key] = selected_label

        self.telemetry_sensor_choices = sensor_choices
        self.telemetry_selected_labels = selected_labels
        resolved_mappings = self._collect_telemetry_mappings_from_labels(selected_labels)
        changed = resolved_mappings != self.settings.get("telemetry_mappings", {})
        self.settings["telemetry_mappings"] = resolved_mappings
        self.orchestrator.configure_telemetry(resolved_mappings)
        if changed:
            self.orchestrator.save_settings(self.settings)

        self._update_telemetry_mapping_status(
            invalid_saved_categories=invalid_saved_categories,
            unassigned_categories=unresolved_categories,
            no_data_categories=no_data_categories,
            sensor_count=len(sensors),
            show_feedback=show_feedback,
        )
        if prompt_for_pawnio:
            self._maybe_prompt_pawnio_install()

    def _build_sensor_choices_for_category(
        self,
        category: Any,
        sensors: list[dict[str, Any]],
    ) -> dict[str, str | None]:
        option_map: dict[str, str | None] = {UNASSIGNED_SENSOR_OPTION: None}
        used_labels: set[str] = {UNASSIGNED_SENSOR_OPTION}

        compatible = [
            sensor
            for sensor in sensors
            if self._sensor_is_compatible_with_category(sensor, category)
        ]
        compatible.sort(key=lambda sensor: (sensor.get("hardware_name", ""), sensor.get("sensor_name", "")))
        for sensor in compatible:
            label = self._format_sensor_label(sensor)
            if label in used_labels:
                label = f"{label} [{sensor.get('sensor_id', '')}]"
            used_labels.add(label)
            option_map[label] = sensor.get("sensor_id")

        return option_map

    @staticmethod
    def _format_sensor_label(sensor: dict[str, Any]) -> str:
        hardware_name = sensor.get("hardware_name") or sensor.get("hardware_type") or "Unknown Hardware"
        sensor_name = sensor.get("sensor_name") or sensor.get("display_name") or "Unknown Sensor"
        value_suffix = " [no data]" if sensor.get("value") is None else ""
        return f"{hardware_name} | {sensor_name}{value_suffix}"

    @staticmethod
    def _find_label_for_sensor_id(option_map: dict[str, str | None], sensor_id: Any) -> str | None:
        if not sensor_id:
            return None
        for label, mapped_sensor_id in option_map.items():
            if mapped_sensor_id == sensor_id:
                return label
        return None

    @staticmethod
    def _sensor_is_compatible_with_category(sensor: dict[str, Any], category: Any) -> bool:
        return (
            sensor.get("sensor_type") == category.sensor_type
            and sensor.get("hardware_role") in set(category.hardware_roles)
        )

    def _collect_telemetry_mappings_from_labels(self, selected_labels: dict[str, str]) -> dict[str, str | None]:
        mappings: dict[str, str | None] = {}
        for category in TELEMETRY_CATEGORIES:
            selected_label = selected_labels.get(category.key, UNASSIGNED_SENSOR_OPTION)
            mappings[category.key] = self.telemetry_sensor_choices.get(category.key, {}).get(selected_label)
        return mappings

    def _save_telemetry_mappings(self, mappings: dict[str, str | None]) -> None:
        normalized_mappings = {
            category.key: mappings.get(category.key) or None
            for category in TELEMETRY_CATEGORIES
        }
        self.settings["telemetry_mappings"] = normalized_mappings
        self.orchestrator.configure_telemetry(normalized_mappings)
        self.orchestrator.save_settings(self.settings)
        self._refresh_telemetry_mapping_state(show_feedback=False, refresh_inventory=False)

    def _update_telemetry_mapping_status(
        self,
        invalid_saved_categories: list[str],
        unassigned_categories: list[str],
        no_data_categories: list[str],
        sensor_count: int,
        show_feedback: bool,
    ) -> None:
        parts = [f"Detected {sensor_count} LibreHardwareMonitor sensors."]
        if invalid_saved_categories:
            parts.append("Saved mappings no longer resolve: " + ", ".join(invalid_saved_categories) + ".")
        if unassigned_categories:
            parts.append("Unassigned categories: " + ", ".join(unassigned_categories) + ".")
        if no_data_categories:
            parts.append("Mapped sensors with no live reading: " + ", ".join(no_data_categories) + ".")
        if self.pawnio_status.get("is_installed") is False:
            parts.append("PawnIO driver is not installed. Sensors and telemetry might not work correctly until it is installed.")
        elif show_feedback:
            parts.append("Telemetry mappings refreshed.")

        self.telemetry_status_summary = " ".join(parts)
        brief_parts = [f"{sensor_count} sensors detected"]
        if invalid_saved_categories:
            brief_parts.append("remap needed")
        elif unassigned_categories:
            brief_parts.append("mapping incomplete")
        elif no_data_categories:
            brief_parts.append("some readings offline")
        elif self.pawnio_status.get("is_installed") is False:
            brief_parts.append("PawnIO missing")
        self.telemetry_status_brief = " | ".join(brief_parts)
        self.telemetry_status_color = (
            DANGER
            if invalid_saved_categories
            else WARNING
            if (
                unassigned_categories
                or no_data_categories
                or self.pawnio_status.get("is_installed") is False
            )
            else ACCENT
        )
        if invalid_saved_categories or unassigned_categories:
            self.telemetry_header_status_text = "●  Some sensors are not assigned"
            self.telemetry_header_status_color = DANGER
        elif self.pawnio_status.get("is_installed") is False:
            self.telemetry_header_status_text = "●  PawnIO driver missing"
            self.telemetry_header_status_color = WARNING
        else:
            self.telemetry_header_status_text = "●  All sensors assigned"
            self.telemetry_header_status_color = ACCENT
        self._apply_telemetry_state_to_ui()

    def _apply_telemetry_state_to_ui(self) -> None:
        if hasattr(self, "telemetry_header_status_label"):
            self.telemetry_header_status_label.configure(
                text=self.telemetry_header_status_text,
                text_color=self.telemetry_header_status_color,
            )
        if self.telemetry_window is not None and self.telemetry_window.winfo_exists():
            self.telemetry_window.apply_mapping_state()

    def _show_dialog(
        self,
        title: str,
        message: str,
        dialog_type: str = "info",
        buttons: list[tuple[str, str, Any]] | None = None,
        default_result: Any = None,
    ) -> Any:
        dialog = AppMessageDialog(
            self,
            title=title,
            message=message,
            dialog_type=dialog_type,
            buttons=buttons,
            default_result=default_result,
        )
        return dialog.show()

    def _show_info(self, title: str, message: str) -> None:
        self._show_dialog(title, message, dialog_type="info")

    def _show_warning(self, title: str, message: str) -> None:
        self._show_dialog(title, message, dialog_type="warning")

    def _show_error(self, title: str, message: str) -> None:
        self._show_dialog(title, message, dialog_type="error")

    def _ask_yes_no(self, title: str, message: str, confirm_label: str = "Yes", cancel_label: str = "Cancel") -> bool:
        result = self._show_dialog(
            title,
            message,
            dialog_type="question",
            buttons=[
                (cancel_label, "secondary", False),
                (confirm_label, "primary", True),
            ],
            default_result=False,
        )
        return bool(result)

    def _ask_string(self, title: str, prompt: str, default_value: str = "", confirm_label: str = "Save") -> str | None:
        dialog = AppInputDialog(
            self,
            title=title,
            prompt=prompt,
            default_value=default_value,
            confirm_label=confirm_label,
        )
        return dialog.show()

    def _show_crash_recovery_if_needed(self) -> None:
        crash_state = self.orchestrator.check_crash_recovery()
        if not crash_state:
            return
        test_key = str(crash_state.get("current_test", "") or "")
        failed_test = self.orchestrator._test_catalog().get(test_key, test_key or "Unknown")
        run_id = crash_state.get("run_id") or "Unknown"
        message = (
            "The previous diagnostics run was interrupted, most likely by a system crash or power loss.\n\n"
            f"Test marked FAILED (System Crash): {failed_test}\n"
            f"Run: {run_id}"
        )
        windows_events = crash_state.get("windows_events") or []
        if windows_events:
            shown = "\n".join(f"- {line}" for line in windows_events[:5])
            more = f"\n(and {len(windows_events) - 5} more in the run summary)" if len(windows_events) > 5 else ""
            message += f"\n\nWindows recorded since the run started:\n{shown}{more}"
        if crash_state.get("summary_path"):
            message += f"\n\nReconstructed run summary:\n{crash_state['summary_path']}"
        self._show_warning("Crash Recovery", message)

    def _pick_logo(self) -> None:
        default_dir = self.root_path / "Logotypes"
        if not default_dir.exists():
            default_dir = self.root_path

        dialog = AppFilePickerDialog(
            self,
            title="Select Report Logo",
            prompt="Choose the image used on the stability certificate.",
            initial_path=default_dir,
            file_extensions=(".png", ".jpg", ".jpeg"),
            shortcut_targets=[("Logotypes", default_dir)],
            confirm_label="Use Logo",
        )
        file_path = dialog.show()
        if file_path:
            self.logo_var.set(file_path)

    def _sync_certificate_field_states(self) -> None:
        customer_state = "normal" if self.include_customer_name_var.get() else "disabled"
        machine_state = "normal" if self.include_machine_name_var.get() else "disabled"
        self.customer_name_entry.configure(state=customer_state)
        self.certificate_machine_name_entry.configure(state=machine_state)

    def _save_preset(self) -> None:
        name = self._ask_string("Preset Name", "Enter a preset name:", confirm_label="Save")
        if not name:
            return
        if name == RESERVED_PRESET_NAME:
            self._show_error("Invalid Name", f'"{RESERVED_PRESET_NAME}" is reserved. Choose a different preset name.')
            return
        if name in self.presets and not self._ask_yes_no(
            "Overwrite Preset",
            f'A preset named "{name}" already exists. Overwrite it?',
            confirm_label="Overwrite",
        ):
            return
        self.presets[name] = self._collect_form_state()
        self.orchestrator.save_presets(self.presets)
        self._load_presets_into_menu()
        self.preset_var.set(name)

    def _delete_preset(self) -> None:
        name = self.preset_var.get()
        if name in ("", "Custom") or name not in self.presets:
            return
        del self.presets[name]
        self.orchestrator.save_presets(self.presets)
        self.preset_var.set("Custom")
        self._load_presets_into_menu()

    def _reset_test_settings(self) -> None:
        for var in self.test_vars.values():
            var.set(False)
        for var in self.duration_vars.values():
            var.set(self.default_phase_duration_minutes)
        self.ram_gb_var.set(self.default_ram_gb)
        self.gpu_res_var.set(self.default_gpu_resolution)
        self.vram_gb_var.set(self.default_vram_gb)
        target_drive = self.default_target_drive if self.default_target_drive in self.drive_options else self.drive_options[0]
        self.target_drive_var.set(target_drive)
        self.preset_var.set("Custom")

    def _apply_preset(self, preset_name: str) -> None:
        payload = self.presets.get(preset_name)
        if not isinstance(payload, dict) or not payload:
            return
        enabled_map = payload.get("enabled", {})
        durations_map = payload.get("durations", {})
        for key, enabled in (enabled_map.items() if isinstance(enabled_map, dict) else []):
            if key in self.test_vars:
                self.test_vars[key].set(bool(enabled))
        for key, value in (durations_map.items() if isinstance(durations_map, dict) else []):
            if key in self.duration_vars:
                self.duration_vars[key].set(str(value))
        if "ram_gb" in payload:
            self.ram_gb_var.set(str(payload["ram_gb"]))
            
        if "target_drive" in payload:
            valid_drives = [opt.lower() for opt in self.drive_options]
            if str(payload["target_drive"]).lower() in valid_drives:
                for opt in self.drive_options:
                    if opt.lower() == str(payload["target_drive"]).lower():
                        self.target_drive_var.set(opt)
                        break

        if "logo_path" in payload:
            self.logo_var.set(str(payload["logo_path"]))
        if "gpu_resolution" in payload:
            self.gpu_res_var.set(str(payload["gpu_resolution"]))
        if str(payload.get("vram_gb", "")) in self.vram_size_options:
            self.vram_gb_var.set(str(payload["vram_gb"]))
        if "generate_report" in payload:
            self.generate_report_var.set(bool(payload["generate_report"]))
        if "include_customer_name" in payload:
            self.include_customer_name_var.set(bool(payload["include_customer_name"]))
        if "customer_name" in payload:
            self.customer_name_var.set(str(payload["customer_name"]))
        if "include_machine_name" in payload:
            self.include_machine_name_var.set(bool(payload["include_machine_name"]))
        if "certificate_machine_name" in payload:
            self.certificate_machine_name_var.set(str(payload["certificate_machine_name"]))
        self._sync_certificate_field_states()

    def _collect_form_state(self) -> dict[str, Any]:
        enabled = {key: var.get() for key, var in self.test_vars.items()}
        durations = {}
        for key, value in self.duration_vars.items():
            try:
                durations[key] = max(0, int(value.get() or 0))
            except ValueError:
                durations[key] = 0
                
        try:
            ram_gb = float(self.ram_gb_var.get() or 0)
        except ValueError:
            ram_gb = 0.0
            
        gpu_resolution = self.gpu_res_var.get()
        generate_report = self.generate_report_var.get()
            
        return {
            "enabled": enabled,
            "durations": durations,
            "logo_path": self.logo_var.get().strip(),
            "include_customer_name": self.include_customer_name_var.get(),
            "customer_name": self.customer_name_var.get().strip(),
            "include_machine_name": self.include_machine_name_var.get(),
            "certificate_machine_name": self.certificate_machine_name_var.get().strip(),
            "ram_gb": ram_gb,
            "target_drive": self.target_drive_var.get(),
            "gpu_resolution": gpu_resolution,
            "vram_gb": int(self.vram_gb_var.get()),
            "generate_report": generate_report,
        }

    def _confirm_long_furmark_patterns(self, selections: dict[str, int]) -> bool:
        flagged_patterns: list[tuple[str, int]] = []
        pattern_labels = {
            "gpu": "GPU Stress",
            "vram": "GPU Memory (VRAM) Stress",
            "psu": "PSU / System Stress",
        }
        for key in ("gpu", "vram", "psu"):
            duration = int(selections.get(key, 0) or 0)
            if duration > 5:
                flagged_patterns.append((pattern_labels[key], duration))

        if not flagged_patterns:
            return True

        pattern_lines = "\n".join(f"- {label} ({duration} min)" for label, duration in flagged_patterns)
        should_continue = self._show_dialog(
            "Warning!",
            (
                "You are about to run the following FurMark-based stress pattern(s) for more than 5 minutes:\n"
                f"{pattern_lines}\n\n"
                "FurMark pushes the graphics card to its maximum power draw and thermal limits. "
                "In most cases, a 5-minute run is enough to confirm GPU and PSU stability because this workload is extremely aggressive.\n\n"
                "Recommended duration is 5 to 10 minutes. If you understand what you are about to do and want to continue, press Start."
            ),
            dialog_type="warning",
            buttons=[
                ("Cancel", "danger", False),
                ("Start", "success", True),
            ],
            default_result=False,
        )
        return bool(should_continue)

    @staticmethod
    def _parse_duration_minutes(raw_value: str) -> int | None:
        try:
            value = int(str(raw_value).strip())
        except ValueError:
            return None
        return value if value > 0 else None

    def _validate_start_inputs(self, payload: dict[str, Any]) -> tuple[dict[str, int], float, int] | None:
        catalog = self.orchestrator._test_catalog()
        selections: dict[str, int] = {}
        invalid_durations: list[str] = []
        for key, enabled_var in self.test_vars.items():
            if not enabled_var.get():
                selections[key] = 0
                continue
            minutes = self._parse_duration_minutes(self.duration_vars[key].get())
            if minutes is None:
                invalid_durations.append(catalog.get(key, key))
                continue
            selections[key] = minutes

        if invalid_durations:
            self._show_error(
                "Validation Error",
                "Enter a whole number of minutes greater than zero for:\n- " + "\n- ".join(invalid_durations),
            )
            return None

        if not any(selections.values()):
            self._show_error("Validation Error", "Select at least one test phase.")
            return None

        ram_gb = 0.0
        if selections.get("ram", 0) > 0:
            try:
                ram_gb = float(self.ram_gb_var.get().strip())
            except ValueError:
                ram_gb = float("nan")
            if not math.isfinite(ram_gb) or ram_gb <= 0:
                self._show_error("Validation Error", "RAM to test must be a number greater than zero.")
                return None

            # Keep a hard OS minimum buffer of 256 MB so the system does not lock up.
            available_mb = int(psutil.virtual_memory().available / (1024 * 1024))
            max_allowed_gb = (available_mb - 256) / 1024.0
            if ram_gb > max_allowed_gb:
                self._show_error(
                    "Validation Error",
                    f"Cannot allocate {ram_gb:g} GB RAM.\nAvailable for testing is approx {max(0.0, max_allowed_gb):.1f} GB.",
                )
                return None

        uses_furmark = any(selections.get(key, 0) > 0 for key in ("gpu", "vram", "psu"))
        if uses_furmark and self.orchestrator.parse_resolution(payload["gpu_resolution"]) is None:
            self._show_error("Validation Error", "GPU resolution must look like 1280x720.")
            return None

        vram_gb = payload["vram_gb"] if selections.get("vram", 0) > 0 else 0
        if vram_gb and self.detected_vram_gb and vram_gb > self.detected_vram_gb - VRAM_HEADROOM_GB:
            if not self._ask_yes_no(
                "VRAM Test Size",
                (
                    f"The VRAM test is set to {vram_gb} GB, but this graphics card reports about "
                    f"{self.detected_vram_gb:.1f} GB of video memory.\n\n"
                    "Data that does not fit is moved to system RAM, so part of the test then exercises "
                    "system RAM and the PCIe link instead of the video memory. "
                    f"Recommended size for this card: {recommended_vram_test_gb(self.detected_vram_gb)} GB."
                ),
                confirm_label="Use Anyway",
                cancel_label="Cancel",
            ):
                return None

        return selections, ram_gb, vram_gb

    def _confirm_thermal_protection(self, selections: dict[str, int]) -> bool:
        mappings = self.settings.get("telemetry_mappings", {}) or {}
        unprotected: list[str] = []
        if not mappings.get("cpu_temp"):
            unprotected.append("CPU Temperature")
        if any(selections.get(key, 0) > 0 for key in ("gpu", "vram", "psu")) and not mappings.get("gpu_temp"):
            unprotected.append("GPU Temperature")
        if not unprotected:
            return True

        return self._ask_yes_no(
            "Thermal Protection Inactive",
            (
                "The following sensors are not assigned:\n- "
                + "\n- ".join(unprotected)
                + "\n\nThe thermal kill switch cannot abort the test if these components overheat. "
                "Assign them via Assign Sensors before starting, or continue at your own risk."
            ),
            confirm_label="Start Anyway",
            cancel_label="Cancel",
        )

    def _start_diagnostics(self) -> None:
        if self.orchestrator.is_session_running():
            self._show_info("Session running", "A diagnostics session is still shutting down. Try again in a moment.")
            return

        payload = self._collect_form_state()
        self.settings["logo_path"] = payload["logo_path"]
        self.settings["include_customer_name"] = payload["include_customer_name"]
        self.settings["customer_name"] = payload["customer_name"]
        self.settings["include_machine_name"] = payload["include_machine_name"]
        self.settings["certificate_machine_name"] = payload["certificate_machine_name"]
        self.orchestrator.configure_telemetry(self.settings.get("telemetry_mappings"))
        try:
            self.orchestrator.save_settings(self.settings)
        except OSError as exc:
            self._show_warning("Settings Not Saved", f"Unable to save settings.json:\n{exc}")

        validated = self._validate_start_inputs(payload)
        if validated is None:
            return
        selections, ram_gb, vram_gb = validated

        if not self._confirm_thermal_protection(selections):
            return

        if not self._confirm_long_furmark_patterns(selections):
            return

        try:
            self.orchestrator.start_session(
                selections,
                self.settings,
                ram_gb=ram_gb,
                target_drive=payload["target_drive"],
                gpu_resolution=payload["gpu_resolution"],
                generate_report=payload["generate_report"],
                vram_gb=vram_gb,
            )
        except Exception as exc:
            self._show_error("Unable to start", str(exc))
            return

        self.console.configure(state="normal")
        self.console.delete("1.0", "end")
        self.console.configure(state="disabled")
        
        self.error_flag_label.configure(text="")
        self.phase_status_label.configure(text="")
        self.tested_hardware_label.configure(text="")
        self.phase_index_label.configure(text="STARTING")
        self.phase_label.configure(text=display_case("Preparing first phase"), text_color=heading_color())
        self.phase_progress.configure(progress_color=ACCENT)
        self.phase_progress.set(0)
        self.countdown_label.configure(text="--:--")
        self.full_matrix_countdown_label.configure(text="--:--")
        
        self.completed_logs_data = []
        self._set_dashboard_controls(session_running=True)
        self._refresh_completed_logs_ui()
            
        self.setup_frame.grid_remove()
        self.dashboard_frame.grid()

    def _back_to_setup(self) -> None:
        if self.orchestrator.session_thread and self.orchestrator.session_thread.is_alive():
            self._show_info("Session running", "Abort the current diagnostics session before returning to setup.")
            return
        self.dashboard_frame.grid_remove()
        self.setup_frame.grid()

    def _drain_events(self) -> None:
        try:
            for _ in range(MAX_EVENTS_PER_DRAIN):
                try:
                    event = self.orchestrator.event_queue.get_nowait()
                except queue.Empty:
                    break
                try:
                    self._handle_event(event)
                except Exception as exc:  # keep the UI pump alive no matter what one event does
                    try:
                        self._append_console(f"[UI] Failed to process {event.get('type')} event: {exc}", is_error=True)
                    except Exception:
                        pass
        finally:
            self.after(100, self._drain_events)

    def _handle_event(self, event: dict[str, Any]) -> None:
        event_type = event["type"]
        if event_type == EVENT_LOG:
            self._append_console(event["message"], is_error=event.get("is_error", False))
        elif event_type == EVENT_PHASE:
            self._update_phase(event)
        elif event_type == EVENT_TELEMETRY:
            self._update_telemetry(event["sample"])
        elif event_type == EVENT_ALERT:
            if hasattr(self, "error_flag_label"):
                self.error_flag_label.configure(text="●  Issues detected")
            self._append_console(f"[ALERT] {event['message']}", is_error=True)
        elif event_type == EVENT_STATUS:
            self._append_console(f"[STATUS] {event['message']}")
        elif event_type == EVENT_PHASE_LOGS:
            self._add_completed_log(event["meta"])
        elif event_type == EVENT_FINISHED:
            self.current_summary = event["summary"]
            self._handle_finished(event["summary"])
        elif event_type == EVENT_ABORTED:
            self._handle_aborted(event["message"])

    def _append_console(self, line: str, is_error: bool = False) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.console.configure(state="normal")
        if is_error:
            self.console.insert("end", f"[{timestamp}] {line}\n", "error")
        else:
            self.console.insert("end", f"[{timestamp}] {line}\n")
        line_count = int(self.console.index("end-1c").split(".")[0])
        if line_count > CONSOLE_MAX_LINES:
            self.console.delete("1.0", f"{line_count - CONSOLE_MAX_LINES + 1}.0")
        self.console.see("end")
        self.console.configure(state="disabled")

    def _update_phase(self, event: dict[str, Any]) -> None:
        self.phase_index_label.configure(text=f"PHASE {event['phase_index']} OF {event['total_phases']}")
        self.phase_label.configure(text=display_case(event["label"]), text_color=heading_color())
        self.tested_hardware_label.configure(
            text="     ".join(f"{role}  {name}" for role, name in event.get("tested_hardware", []))
        )
        phase_seconds = max(1.0, float(event.get("duration_minutes", 0) or 0) * 60)
        if event.get("finishing"):
            # Phase duration is over; engines get a short grace period to exit on their own.
            self.countdown_label.configure(text="FINISHING")
            self.phase_status_label.configure(
                text=f"Duration reached - waiting for engines to finish ({int(event.get('finishing_remaining_seconds', 0))}s max)"
            )
            self.phase_progress.set(1.0)
        else:
            self.phase_status_label.configure(text="")
            remaining = int(event["remaining_seconds"])
            mins, secs = divmod(max(0, remaining), 60)
            self.countdown_label.configure(text=f"{mins:02d}:{secs:02d}")
            self.phase_progress.set(max(0.0, min(1.0, 1.0 - remaining / phase_seconds)))

        total_remaining = int(event.get("total_remaining_seconds", 0))
        t_mins, t_secs = divmod(max(0, total_remaining), 60)
        t_hours, t_mins = divmod(t_mins, 60)
        self.full_matrix_countdown_label.configure(
            text=f"{t_hours}:{t_mins:02d}:{t_secs:02d}" if t_hours else f"{t_mins:02d}:{t_secs:02d}"
        )

    def _update_telemetry(self, sample: dict[str, Any]) -> None:
        if sample.get("cpu_power") is not None or sample.get("gpu_power") is not None:
            sample["total_power"] = (sample.get("cpu_power") or 0.0) + (sample.get("gpu_power") or 0.0)
        else:
            sample["total_power"] = None

        units = {
            "cpu_temp": " °C",
            "gpu_temp": " °C",
            "cpu_load": " %",
            "gpu_load": " %",
            "ram_load": " %",
            "storage_load": " %",
            "cpu_power": " W",
            "gpu_power": " W",
            "total_power": " W",
        }
        temperature_limits = {
            "cpu_temp": self.orchestrator.cpu_temp_limit_c,
            "gpu_temp": self.orchestrator.gpu_temp_limit_c,
        }
        scales = {
            "cpu_power": 250.0,
            "gpu_power": 450.0,
            "total_power": 700.0,
        }
        mappings = self.settings.get("telemetry_mappings", {})
        for key, value in list(sample.items()):
            if key not in self.telemetry_labels:
                continue
            label = self.telemetry_labels[key]
            bar = self.telemetry_bars[key]
            if value is None:
                label.configure(text="No data" if mappings.get(key) else "Unassigned", text_color=TEXT_FAINT)
                bar.set(0)
                continue
            color = ACCENT
            if key in temperature_limits:
                limit = temperature_limits[key]
                # Bars fill towards the abort limit and change colour as it gets close.
                fraction = value / limit if limit else 0.0
                color = DANGER if value >= limit - 5 else WARNING if value >= limit - 15 else ACCENT
            else:
                fraction = value / scales.get(key, 100.0)
            label.configure(text=f"{value:.1f}{units.get(key, '')}", text_color=heading_color() if color == ACCENT else color)
            bar.configure(progress_color=color)
            bar.set(max(0.0, min(1.0, float(fraction))))

    def _add_completed_log(self, meta: dict[str, Any]) -> None:
        log_file_path = str(meta.get("log_file_path", "") or "")
        if log_file_path and any(existing.get("log_file_path") == log_file_path for existing in self.completed_logs_data):
            return
        self.completed_logs_data.append(meta)
        self._refresh_completed_logs_ui()

    def _load_completed_logs_from_disk(self) -> None:
        if self.completed_logs_data:
            return

        run_dir = self.orchestrator.run_dir
        if run_dir is None and self.current_summary:
            run_id = str(self.current_summary.get("run_id", "") or "")
            if run_id:
                run_dir = self.root_path / "results" / run_id

        if run_dir is None:
            return

        payload = self.orchestrator.load_json(run_dir / "logs" / "engine_meta.json", [])
        if not isinstance(payload, list):
            return

        hydrated_logs: list[dict[str, Any]] = []
        seen_paths: set[str] = set()
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            log_file_path = str(entry.get("log_file_path", "") or "")
            if log_file_path and log_file_path in seen_paths:
                continue
            if log_file_path:
                seen_paths.add(log_file_path)
            hydrated_logs.append(entry)

        if hydrated_logs:
            self.completed_logs_data = hydrated_logs
            self._refresh_completed_logs_ui()

    def _refresh_completed_logs_ui(self) -> None:
        count = len(self.completed_logs_data)
        failed = sum(1 for meta in self.completed_logs_data if meta.get("status") != "PASSED")
        if count == 0:
            status_text = "Each phase appears here when it finishes."
        elif failed:
            status_text = f"{count} finished · {failed} not passed"
        else:
            status_text = f"{count} finished · all passed"
        self.logs_status_label.configure(text=status_text)

        for widget in self.dashboard_logs_list.winfo_children():
            widget.destroy()
        for index, meta in enumerate(self.completed_logs_data):
            status = str(meta.get("status", ""))
            status_fg, status_bg = status_colors(status)
            row = ctk.CTkFrame(self.dashboard_logs_list, fg_color=CARD, corner_radius=RADIUS)
            row.grid(row=index, column=0, sticky="ew", pady=3)
            row.grid_columnconfigure(0, weight=1)
            name = ctk.CTkLabel(row, text=meta["test_name"], font=ui_font(13, "semibold"), text_color=TEXT, anchor="w")
            name.grid(row=0, column=0, sticky="w", padx=12, pady=(8, 2))
            detail = ctk.CTkFrame(row, fg_color="transparent")
            detail.grid(row=1, column=0, sticky="w", padx=12, pady=(0, 10))
            ctk.CTkLabel(
                detail,
                text=status_tag(status),
                font=ui_font(10, "semibold"),
                text_color=status_fg,
                fg_color=status_bg,
                corner_radius=6,
                height=20,
            ).grid(row=0, column=0)
            ctk.CTkLabel(
                detail,
                text=format_duration(float(meta.get("duration_seconds", 0.0) or 0.0)),
                font=ui_font(12),
                text_color=TEXT_MUTED,
            ).grid(row=0, column=1, padx=(8, 0))
            build_button(row, "View log", role="ghost", command=lambda m=meta: self._open_log_modal(m), width=70, height=28).grid(
                row=0, column=1, rowspan=2, padx=(0, 6)
            )
            for widget in (row, name, detail):
                widget.bind("<Button-1>", lambda _event, m=meta: self._open_log_modal(m))

    def _open_log_modal(self, meta: dict[str, Any]) -> None:
        LogViewerModal(self, meta)

    def _handle_finished(self, summary: dict[str, Any]) -> None:
        self._load_completed_logs_from_disk()
        verdict = summary["verdict"]
        self.phase_index_label.configure(text="RUN COMPLETE")
        self.tested_hardware_label.configure(text="")
        self.phase_label.configure(text=verdict, text_color=ACCENT if verdict == "CERTIFIED STABLE" else DANGER)
        self.phase_status_label.configure(text="Review the completed phases on the right.")
        self.phase_progress.configure(progress_color=ACCENT if verdict == "CERTIFIED STABLE" else DANGER)
        self.phase_progress.set(1.0)
        self.countdown_label.configure(text="DONE")
        self.full_matrix_countdown_label.configure(text="DONE")
        self._set_dashboard_controls(session_running=False)
        report_path = summary.get("report_path", "")
        report_error = summary.get("report_error", "")
        message = verdict
        if report_path:
            message += f"\n\nReport saved to:\n{report_path}"
        elif report_error:
            message += f"\n\n{report_error}"
        else:
            message += "\n\nCertificate generation was disabled for this run."
        if summary.get("run_id"):
            message += f"\n\nRun artifacts:\n{self.root_path / 'results' / summary['run_id']}"
        # Defer the modal so the event pump is not blocked inside its nested wait loop.
        if report_error:
            self.after(50, lambda: self._show_warning("Diagnostics Complete", message))
        else:
            self.after(50, lambda: self._show_info("Diagnostics Complete", message))

    def _handle_aborted(self, message: str) -> None:
        self.current_summary = None
        self._append_console(f"[STATUS] {message}")
        self.phase_index_label.configure(text="RUN STOPPED")
        self.tested_hardware_label.configure(text="")
        self.phase_label.configure(text=display_case("Session aborted"), text_color=WARNING)
        self.phase_status_label.configure(text="Review the engine output and any completed phases.")
        self.phase_progress.configure(progress_color=WARNING)
        self.countdown_label.configure(text="--:--")
        self.full_matrix_countdown_label.configure(text="--:--")
        self._set_dashboard_controls(session_running=False)

    def _set_dashboard_controls(self, session_running: bool) -> None:
        self.abort_button.configure(state="normal" if session_running else "disabled")
        # Primary styling only when it is the next step, so it does not look clickable during a run.
        self.back_to_setup_button.configure(
            state="disabled" if session_running else "normal",
            fg_color=CARD_HOVER if session_running else ACCENT,
        )

    def _on_close(self) -> None:
        if self.orchestrator.is_session_running() and not self._ask_yes_no(
            "Abort Diagnostics",
            "A diagnostics session is running. Closing the app will abort it and no certificate will be generated.",
            confirm_label="Abort And Close",
            cancel_label="Keep Running",
        ):
            return
        self.orchestrator.shutdown()
        self.destroy()
