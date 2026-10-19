from __future__ import annotations

import ctypes
import math
import os
import re
import queue
import tkinter as tk
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

APP_BG = "#010503"
SURFACE_BG = "#031008"
PANEL_BG = "#050d08"
PANEL_ALT_BG = "#07140c"
CARD_BG = "#040b07"
CARD_ACTIVE_BG = "#0a170e"
FIELD_BG = "#08110b"
CONSOLE_BG = "#020603"
GREEN_NEON = "#72ff94"
GREEN_BRIGHT = "#a0ffb6"
GREEN_SOFT = "#2f8c4d"
GREEN_DIM = "#163523"
GREEN_TEXT = "#bfffcf"
GREEN_MUTED = "#7fd494"
GREEN_FAINT = "#46735a"
RED_ACCENT = "#ff6767"
RED_BG = "#2a0a0a"
RED_SOFT = "#6e2222"
WARNING_AMBER = "#ffcd6a"
SUCCESS_GREEN = "#8effaa"

UNASSIGNED_SENSOR_OPTION = "Unassigned"
RESERVED_PRESET_NAME = "Custom"
# Caps keep the Tk text widgets responsive during multi-hour runs with chatty engines.
CONSOLE_MAX_LINES = 5000
LOG_VIEWER_MAX_LINES = 20000
MAX_EVENTS_PER_DRAIN = 500
IMAGE_RESAMPLING = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS


def ui_font(size: int, weight: str = "normal", family: str = "Bahnschrift") -> ctk.CTkFont:
    return ctk.CTkFont(family=family, size=size, weight=weight)


def mono_font(size: int, weight: str = "normal") -> ctk.CTkFont:
    return ctk.CTkFont(family="Consolas", size=size, weight=weight)


def build_entry(parent: Any, textvariable: Any, width: int = 120, justify: str = "center") -> ctk.CTkEntry:
    return ctk.CTkEntry(
        parent,
        textvariable=textvariable,
        width=width,
        height=34,
        corner_radius=10,
        border_width=1,
        border_color=GREEN_DIM,
        fg_color=FIELD_BG,
        text_color=GREEN_TEXT,
        font=mono_font(13),
        justify=justify,
    )


def build_option_menu(
    parent: Any,
    variable: Any,
    values: list[str],
    width: int = 150,
    command: Any | None = None,
    accent: str = "green",
) -> ctk.CTkOptionMenu:
    is_red = accent == "red"
    return ctk.CTkOptionMenu(
        parent,
        variable=variable,
        values=values,
        width=width,
        height=34,
        command=command,
        corner_radius=10,
        fg_color=RED_BG if is_red else FIELD_BG,
        button_color=RED_SOFT if is_red else GREEN_DIM,
        button_hover_color=RED_ACCENT if is_red else GREEN_SOFT,
        dropdown_fg_color=PANEL_ALT_BG,
        dropdown_hover_color=RED_SOFT if is_red else GREEN_DIM,
        dropdown_text_color=GREEN_TEXT,
        text_color=GREEN_TEXT,
        font=mono_font(13),
        anchor="w",
    )


def apply_windows_dark_title_bar(window: Any) -> None:
    if os.name != "nt":
        return
    try:
        window.update_idletasks()
        hwnd = window.winfo_id()
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


class CompactArrowOptionMenu(ctk.CTkOptionMenu):
    def __init__(self, *args: Any, arrow_shift: int = 10, **kwargs: Any) -> None:
        self._arrow_shift = arrow_shift
        super().__init__(*args, **kwargs)

    def _draw(self, no_color_updates: bool = False) -> None:
        super()._draw(no_color_updates)
        try:
            self._canvas.move("dropdown_arrow", -self._apply_widget_scaling(self._arrow_shift), 0)
        except Exception:
            pass


def build_button(parent: Any, text: str, role: str = "secondary", **kwargs: Any) -> ctk.CTkButton:
    palette = {
        "primary": {
            "fg_color": GREEN_NEON,
            "hover_color": GREEN_BRIGHT,
            "border_color": GREEN_BRIGHT,
            "text_color": APP_BG,
            "border_width": 1,
        },
        "secondary": {
            "fg_color": PANEL_ALT_BG,
            "hover_color": GREEN_DIM,
            "border_color": GREEN_SOFT,
            "text_color": GREEN_TEXT,
            "border_width": 1,
        },
        "danger": {
            "fg_color": RED_BG,
            "hover_color": RED_SOFT,
            "border_color": RED_ACCENT,
            "text_color": "#ffd4d4",
            "border_width": 1,
        },
        "success": {
            "fg_color": GREEN_SOFT,
            "hover_color": GREEN_NEON,
            "border_color": GREEN_NEON,
            "text_color": "#ffffff",
            "border_width": 1,
        },
    }[role]
    kwargs.setdefault("corner_radius", 16)
    kwargs.setdefault("font", mono_font(13, "bold" if role != "secondary" else "normal"))
    return ctk.CTkButton(
        parent,
        text=text,
        **palette,
        **kwargs,
    )


def build_section(parent: Any, title: str, subtitle: str | None = None, title_size: int = 19) -> tuple[ctk.CTkFrame, ctk.CTkFrame]:
    section = ctk.CTkFrame(
        parent,
        fg_color=PANEL_BG,
        corner_radius=22,
        border_width=2,
        border_color=GREEN_DIM,
    )
    section.grid_columnconfigure(0, weight=1)

    ctk.CTkLabel(
        section,
        text=title,
        font=ui_font(title_size, "bold"),
        text_color=GREEN_BRIGHT,
        anchor="w",
    ).grid(row=0, column=0, sticky="w", padx=20, pady=(16, 4))

    next_row = 1
    if subtitle:
        ctk.CTkLabel(
            section,
            text=subtitle,
            font=mono_font(12),
            text_color=GREEN_FAINT,
            justify="left",
            anchor="w",
        ).grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 10))
        next_row = 2

    body = ctk.CTkFrame(section, fg_color="transparent")
    body.grid(row=next_row, column=0, sticky="nsew", padx=16, pady=(0, 16))
    return section, body


class TestPhaseCard(ctk.CTkFrame):
    def __init__(
        self,
        parent: Any,
        title: str,
        enabled_var: ctk.BooleanVar,
        duration_var: ctk.StringVar,
        extra_fields: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(
            parent,
            width=176,
            height=188,
            corner_radius=18,
            border_width=2,
            border_color=GREEN_DIM,
            fg_color=CARD_BG,
        )
        self.grid_propagate(False)
        self.enabled_var = enabled_var
        self.detail_labels: list[ctk.CTkLabel] = []

        self.grid_columnconfigure(0, weight=1)

        self.checkbox = ctk.CTkCheckBox(
            self,
            text="",
            variable=enabled_var,
            command=self.refresh_state,
            width=24,
            height=24,
            checkbox_width=24,
            checkbox_height=24,
            corner_radius=5,
            border_width=2,
            fg_color=GREEN_NEON,
            hover_color=GREEN_BRIGHT,
            border_color=GREEN_SOFT,
            checkmark_color=APP_BG,
        )
        self.checkbox.grid(row=0, column=0, pady=(16, 6))

        self.title_label = ctk.CTkLabel(
            self,
            text=title,
            font=ui_font(22, "bold"),
            text_color=GREEN_MUTED,
            justify="center",
            wraplength=140,
        )
        self.title_label.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 10))

        details = ctk.CTkFrame(self, fg_color="transparent")
        details.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 10))
        details.grid_columnconfigure(0, weight=1)

        self._add_field_row(
            details,
            0,
            "Time (min)",
            {"kind": "entry", "variable": duration_var, "width": 62},
        )
        for index, spec in enumerate(extra_fields or [], start=1):
            self._add_field_row(details, index, spec["label"], spec)

        self.enabled_var.trace_add("write", self._on_enabled_change)
        self.refresh_state()

    def _add_field_row(self, parent: Any, row: int, label: str, spec: dict[str, Any]) -> None:
        row_frame = ctk.CTkFrame(parent, fg_color="transparent")
        row_frame.grid(row=row, column=0, sticky="ew", pady=3)
        row_frame.grid_columnconfigure(0, weight=1)
        row_frame.grid_columnconfigure(1, weight=0)

        label_widget = ctk.CTkLabel(
            row_frame,
            text=f"{label}:",
            font=mono_font(12),
            text_color=GREEN_FAINT,
            anchor="w",
        )
        label_widget.grid(row=0, column=0, sticky="w")
        if spec["kind"] == "entry":
            widget = build_entry(row_frame, spec["variable"], width=spec.get("width", 70))
        else:
            widget = build_option_menu(
                row_frame,
                spec["variable"],
                spec["values"],
                width=spec.get("width", 90),
                accent=spec.get("accent", "green"),
            )
        widget.grid(row=0, column=1, sticky="e", padx=(10, 0))
        self.detail_labels.append(label_widget)

    def _on_enabled_change(self, *_args: str) -> None:
        self.refresh_state()

    def refresh_state(self) -> None:
        enabled = bool(self.enabled_var.get())
        self.configure(
            fg_color=CARD_ACTIVE_BG if enabled else CARD_BG,
            border_color=GREEN_NEON if enabled else GREEN_DIM,
        )
        self.title_label.configure(text_color=GREEN_BRIGHT if enabled else GREEN_MUTED)
        for label in self.detail_labels:
            label.configure(text_color=GREEN_MUTED if enabled else GREEN_FAINT)


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

        palette = {
            "info": {"accent": GREEN_NEON, "badge_fg": GREEN_DIM, "badge_text": GREEN_BRIGHT},
            "warning": {"accent": WARNING_AMBER, "badge_fg": "#2a2109", "badge_text": WARNING_AMBER},
            "error": {"accent": RED_ACCENT, "badge_fg": RED_BG, "badge_text": "#ffd4d4"},
            "question": {"accent": GREEN_NEON, "badge_fg": GREEN_DIM, "badge_text": GREEN_BRIGHT},
        }.get(dialog_type, {"accent": GREEN_NEON, "badge_fg": GREEN_DIM, "badge_text": GREEN_BRIGHT})

        self.title(title)
        self.geometry("620x350")
        self.minsize(540, 300)
        self.resizable(False, False)
        self.transient(master)
        self.configure(fg_color=APP_BG)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda _event: self._close())

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        shell = ctk.CTkFrame(
            self,
            fg_color=SURFACE_BG,
            corner_radius=24,
            border_width=2,
            border_color=GREEN_DIM,
        )
        shell.grid(row=0, column=0, sticky="nsew", padx=16, pady=16)
        shell.grid_rowconfigure(1, weight=1)
        shell.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(shell, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 10))
        header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            header,
            text=dialog_type.upper(),
            fg_color=palette["badge_fg"],
            text_color=palette["badge_text"],
            corner_radius=999,
            width=90,
            height=32,
            font=mono_font(12, "bold"),
        ).grid(row=0, column=0, padx=(0, 12))
        ctk.CTkLabel(
            header,
            text=title,
            text_color=palette["accent"],
            font=ui_font(24, "bold"),
            anchor="w",
        ).grid(row=0, column=1, sticky="w")

        message_frame = ctk.CTkFrame(
            shell,
            fg_color=PANEL_BG,
            corner_radius=18,
            border_width=1,
            border_color=GREEN_DIM,
        )
        message_frame.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 12))
        message_frame.grid_rowconfigure(0, weight=1)
        message_frame.grid_columnconfigure(0, weight=1)

        self.message_box = ctk.CTkTextbox(
            message_frame,
            fg_color=CONSOLE_BG,
            border_width=0,
            text_color=GREEN_TEXT,
            font=mono_font(13),
            wrap="word",
            activate_scrollbars=True,
        )
        self.message_box.grid(row=0, column=0, sticky="nsew", padx=14, pady=14)
        self.message_box.insert("1.0", message)
        self.message_box.configure(state="disabled")

        footer = ctk.CTkFrame(shell, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 18))
        footer.grid_columnconfigure(0, weight=1)

        button_specs = buttons or [("OK", "primary", True)]
        button_bar = ctk.CTkFrame(footer, fg_color="transparent")
        button_bar.grid(row=0, column=1, sticky="e")
        for index, (label, role, value) in enumerate(button_specs):
            build_button(
                button_bar,
                label,
                role=role,
                width=120,
                command=lambda v=value: self._set_result(v),
            ).grid(row=0, column=index, padx=(10 if index > 0 else 0, 0))

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
        self.geometry("560x280")
        self.minsize(500, 250)
        self.resizable(False, False)
        self.transient(master)
        self.configure(fg_color=APP_BG)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda _event: self._close())

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        shell = ctk.CTkFrame(
            self,
            fg_color=SURFACE_BG,
            corner_radius=24,
            border_width=2,
            border_color=GREEN_DIM,
        )
        shell.grid(row=0, column=0, sticky="nsew", padx=16, pady=16)
        shell.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(shell, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 10))
        ctk.CTkLabel(
            header,
            text="INPUT",
            fg_color=GREEN_DIM,
            text_color=GREEN_BRIGHT,
            corner_radius=999,
            width=90,
            height=32,
            font=mono_font(12, "bold"),
        ).grid(row=0, column=0, padx=(0, 12))
        ctk.CTkLabel(
            header,
            text=title,
            text_color=GREEN_NEON,
            font=ui_font(24, "bold"),
            anchor="w",
        ).grid(row=0, column=1, sticky="w")

        body = ctk.CTkFrame(
            shell,
            fg_color=PANEL_BG,
            corner_radius=18,
            border_width=1,
            border_color=GREEN_DIM,
        )
        body.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 12))
        body.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            body,
            text=prompt,
            text_color=GREEN_MUTED,
            font=mono_font(13),
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=14, pady=(14, 8))

        self.input_var = ctk.StringVar(value=default_value)
        self.entry = build_entry(body, self.input_var, width=380, justify="left")
        self.entry.grid(row=1, column=0, sticky="ew", padx=14, pady=(0, 14))
        self.entry.bind("<Return>", lambda _event: self._submit())

        footer = ctk.CTkFrame(shell, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 18))
        footer.grid_columnconfigure(0, weight=1)
        button_bar = ctk.CTkFrame(footer, fg_color="transparent")
        button_bar.grid(row=0, column=1, sticky="e")
        build_button(button_bar, cancel_label, role="secondary", width=120, command=self._close).grid(row=0, column=0)
        build_button(button_bar, confirm_label, role="primary", width=120, command=self._submit).grid(row=0, column=1, padx=(10, 0))

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
        self.configure(fg_color=APP_BG)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<Escape>", lambda _event: self._close())

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.after(0, lambda: apply_windows_dark_title_bar(self))

        shell = ctk.CTkFrame(
            self,
            fg_color=SURFACE_BG,
            corner_radius=24,
            border_width=2,
            border_color=GREEN_DIM,
        )
        shell.grid(row=0, column=0, sticky="nsew", padx=16, pady=16)
        shell.grid_rowconfigure(1, weight=1)
        shell.grid_columnconfigure(0, weight=1)

        header, header_body = build_section(shell, title, prompt, title_size=24)
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 10))
        header_body.grid_columnconfigure(0, weight=1)

        path_bar = ctk.CTkFrame(header_body, fg_color="transparent")
        path_bar.grid(row=0, column=0, sticky="ew", padx=4, pady=(0, 10))
        location_menu_frame = ctk.CTkFrame(
            path_bar,
            fg_color=FIELD_BG,
            corner_radius=12,
            border_width=1,
            border_color=GREEN_DIM,
        )
        location_menu_frame.grid(row=0, column=0, sticky="w")
        self.location_menu = CompactArrowOptionMenu(
            location_menu_frame,
            variable=self.location_var,
            values=list(self.location_map.keys()),
            width=420,
            height=36,
            command=self._open_location_from_menu,
            corner_radius=11,
            fg_color=FIELD_BG,
            button_color=GREEN_DIM,
            button_hover_color=GREEN_SOFT,
            dropdown_fg_color=PANEL_ALT_BG,
            dropdown_hover_color=GREEN_DIM,
            dropdown_text_color=GREEN_TEXT,
            text_color=GREEN_TEXT,
            font=mono_font(13),
            dynamic_resizing=False,
            anchor="w",
            arrow_shift=10,
        )
        self.location_menu.grid(row=0, column=0, sticky="w", padx=1, pady=1)

        listing, listing_body = build_section(
            shell,
            "Logo Files",
            "Navigate folders, then select a PNG or JPG file.",
        )
        listing.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 10))
        listing_body.grid_rowconfigure(0, weight=1)
        listing_body.grid_columnconfigure(0, weight=3)
        listing_body.grid_columnconfigure(1, weight=2)

        self.list_frame = ctk.CTkScrollableFrame(
            listing_body,
            fg_color=CONSOLE_BG,
            corner_radius=16,
            border_width=1,
            border_color=GREEN_DIM,
        )
        self.list_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        self.list_frame.grid_columnconfigure(0, weight=1)

        preview_frame = ctk.CTkFrame(
            listing_body,
            fg_color=PANEL_ALT_BG,
            corner_radius=16,
            border_width=1,
            border_color=GREEN_DIM,
        )
        preview_frame.grid(row=0, column=1, sticky="nsew")
        preview_frame.grid_columnconfigure(0, weight=1)
        preview_frame.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            preview_frame,
            text="Preview",
            text_color=GREEN_BRIGHT,
            font=ui_font(18, "bold"),
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(16, 8))

        self.preview_image_label = ctk.CTkLabel(
            preview_frame,
            text="Select a logo file to preview.",
            text_color=GREEN_FAINT,
            font=mono_font(12),
            fg_color=FIELD_BG,
            corner_radius=14,
            anchor="center",
            justify="center",
            width=320,
            height=260,
        )
        self.preview_image_label.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 12))

        self.preview_name_label = ctk.CTkLabel(
            preview_frame,
            text="No file selected",
            text_color=GREEN_MUTED,
            font=ui_font(16, "bold"),
            anchor="w",
        )
        self.preview_name_label.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 4))

        self.preview_meta_label = ctk.CTkLabel(
            preview_frame,
            text="PNG and JPG thumbnails appear in the list.",
            text_color=GREEN_FAINT,
            font=mono_font(12),
            anchor="w",
            justify="left",
        )
        self.preview_meta_label.grid(row=3, column=0, sticky="ew", padx=16, pady=(0, 16))

        footer = ctk.CTkFrame(shell, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 18))
        footer.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            footer,
            text="Selected:",
            text_color=GREEN_MUTED,
            font=mono_font(12),
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.selected_entry = build_entry(footer, self.selected_path_var, justify="left")
        self.selected_entry.grid(row=0, column=1, sticky="ew")
        button_bar = ctk.CTkFrame(footer, fg_color="transparent")
        button_bar.grid(row=0, column=2, sticky="e", padx=(12, 0))
        build_button(button_bar, cancel_label, role="secondary", width=120, command=self._close).grid(row=0, column=0)
        self.confirm_button = build_button(button_bar, confirm_label, role="secondary", width=140, command=self._submit)
        self.confirm_button.grid(row=0, column=1, padx=(10, 0))

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
        draw.rounded_rectangle(body, radius=8, fill="#13301f", outline=GREEN_SOFT, width=2)
        draw.rounded_rectangle(tab, radius=6, fill="#1f5a37", outline=GREEN_SOFT, width=2)
        draw.line(
            [(body[0] + 6, body[1] + 7), (body[2] - 6, body[1] + 7)],
            fill=GREEN_NEON,
            width=2,
        )

        folder_image = ctk.CTkImage(light_image=canvas, dark_image=canvas, size=size)
        self.image_cache[cache_key] = folder_image
        return folder_image

    def _set_status(self, text: str, color: str = GREEN_MUTED) -> None:  # noqa: ARG002
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
            self._set_status("Enter a folder path or an image file path.", RED_ACCENT)
            return

        target = Path(raw_path)
        if target.is_dir():
            self._open_directory(target)
            return
        if target.is_file():
            if not self._is_allowed_file(target):
                self._set_status("Only PNG and JPG logo files are supported.", RED_ACCENT)
                return
            self.current_dir = target.parent
            self._set_selected_path(target)
            self._refresh_listing(scroll_to_top=True)
            return

        self._set_status("That path does not exist.", RED_ACCENT)

    def _go_up(self) -> None:
        parent = self.current_dir.parent
        if parent == self.current_dir:
            self._set_status("Already at the top of this drive.")
            return
        self._open_directory(parent)

    def _open_directory(self, path: Path) -> None:
        target = Path(path)
        if not target.exists() or not target.is_dir():
            self._set_status("Unable to open that folder.", RED_ACCENT)
            return
        self.current_dir = target
        self._set_selected_path(None)
        self._refresh_listing(scroll_to_top=True)

    def _select_file(self, path: Path) -> None:
        if not self._is_allowed_file(path):
            self._set_status("Only PNG and JPG logo files are supported.", RED_ACCENT)
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
            self._set_status(f"Unable to open folder: {exc}", RED_ACCENT)
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
                text_color=GREEN_FAINT,
                font=mono_font(12),
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
            fg_color=PANEL_ALT_BG,
            corner_radius=14,
            border_width=1,
            border_color=GREEN_DIM,
        )
        row.grid(row=row_index, column=0, sticky="ew", padx=10, pady=6)
        row.grid_columnconfigure(1, weight=1)

        thumbnail = ctk.CTkLabel(
            row,
            text="",
            text_color=GREEN_MUTED,
            font=mono_font(11, "bold"),
            fg_color=FIELD_BG,
            corner_radius=10,
            width=58,
            height=58,
            anchor="center",
            justify="center",
        )
        thumbnail.grid(row=0, column=0, rowspan=2, padx=(16, 12), pady=12)
        folder_icon = self._get_folder_icon((52, 52))
        thumbnail.configure(image=folder_icon)
        thumbnail.image = folder_icon

        name_label = ctk.CTkLabel(
            row,
            text="(...)",
            text_color=GREEN_BRIGHT,
            font=ui_font(15, "bold"),
            anchor="w",
        )
        name_label.grid(row=0, column=1, sticky="w", pady=(12, 2))

        meta_label = ctk.CTkLabel(
            row,
            text=f"Go to parent folder | {parent_dir}",
            text_color=GREEN_FAINT,
            font=mono_font(11),
            anchor="w",
        )
        meta_label.grid(row=1, column=1, sticky="w", pady=(0, 12))

        build_button(
            row,
            "Up",
            role="secondary",
            width=120,
            command=lambda p=parent_dir: self._open_directory(p),
        ).grid(row=0, column=2, rowspan=2, padx=16, pady=12)

        clickable_widgets = (row, thumbnail, name_label, meta_label)
        for widget in clickable_widgets:
            widget.bind("<Double-Button-1>", lambda _event, p=parent_dir: self._open_directory(p))

    def _add_path_row(self, row_index: int, path: Path, is_directory: bool) -> None:
        is_selected = not is_directory and self.selected_path == path
        row = ctk.CTkFrame(
            self.list_frame,
            fg_color=CARD_ACTIVE_BG if is_selected else PANEL_ALT_BG,
            corner_radius=14,
            border_width=1,
            border_color=GREEN_NEON if is_selected else GREEN_DIM,
        )
        row.grid(row=row_index, column=0, sticky="ew", padx=10, pady=6)
        row.grid_columnconfigure(1, weight=1)

        thumbnail = ctk.CTkLabel(
            row,
            text="",
            text_color=GREEN_MUTED,
            font=mono_font(11, "bold"),
            fg_color=FIELD_BG,
            corner_radius=10,
            width=58,
            height=58,
            anchor="center",
            justify="center",
        )
        thumbnail.grid(row=0, column=0, rowspan=2, padx=(16, 12), pady=12)
        if is_directory:
            folder_icon = self._get_folder_icon((52, 52))
            thumbnail.configure(image=folder_icon)
            thumbnail.image = folder_icon
        else:
            thumb_image = self._load_ctk_image(path, (52, 52))
            if thumb_image is not None:
                thumbnail.configure(image=thumb_image)
                thumbnail.image = thumb_image
            else:
                thumbnail.configure(text="IMG")

        name_label = ctk.CTkLabel(
            row,
            text=path.name,
            text_color=GREEN_BRIGHT,
            font=ui_font(15, "bold"),
            anchor="w",
        )
        name_label.grid(row=0, column=1, sticky="w", pady=(12, 2))

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
            text_color=GREEN_FAINT,
            font=mono_font(11),
            anchor="w",
        )
        meta_label.grid(row=1, column=1, sticky="w", pady=(0, 12))

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
            width=120,
            command=action,
        ).grid(row=0, column=2, rowspan=2, padx=16, pady=12)

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
                text_color=GREEN_FAINT,
            )
            self.preview_name_label.configure(text="No file selected", text_color=GREEN_MUTED)
            self.preview_meta_label.configure(
                text="PNG and JPG thumbnails appear in the list.",
                text_color=GREEN_FAINT,
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
                text_color=RED_ACCENT,
            )
            self.preview_name_label.configure(text=path.name, text_color=GREEN_BRIGHT)
            self.preview_meta_label.configure(text=f"Unable to load image: {exc}", text_color=RED_ACCENT)
            return

        preview_image = self._load_ctk_image(path, (320, 240))
        self.preview_image_ref = preview_image
        if preview_image is not None:
            self.preview_image_label.configure(image=preview_image, text="")
        else:
            self.preview_image_label.configure(
                image=None,
                text="Preview unavailable",
                text_color=RED_ACCENT,
            )

        self.preview_name_label.configure(text=path.name, text_color=GREEN_BRIGHT)
        self.preview_meta_label.configure(
            text=(
                f"{width} x {height}px\n"
                f"{self._format_file_size(file_stat.st_size)}\n"
                f"Modified {self._format_timestamp(file_stat.st_mtime)}"
            ),
            text_color=GREEN_MUTED,
        )

    def _submit(self) -> None:
        if self.selected_path is None:
            self._set_status("Select an image file first.", RED_ACCENT)
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
        self.geometry("980x760")
        self.minsize(860, 620)
        self.transient(master)
        self.configure(fg_color=APP_BG)

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        shell = ctk.CTkFrame(
            self,
            fg_color=SURFACE_BG,
            corner_radius=24,
            border_width=2,
            border_color=GREEN_DIM,
        )
        shell.grid(row=0, column=0, sticky="nsew", padx=18, pady=18)
        shell.grid_rowconfigure(0, weight=0)
        shell.grid_rowconfigure(1, weight=1)
        shell.grid_rowconfigure(2, weight=0)
        shell.grid_columnconfigure(0, weight=1)

        header, header_body = build_section(shell, meta["test_name"], "Canonical app-captured transcript with execution metadata.", title_size=24)
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 10))
        header_body.grid_columnconfigure((0, 1), weight=1)

        left_meta = [
            ("ENGINE:", self._format_engine_name(meta.get("engine_name", "-"))),
            ("STATUS:", str(meta.get("status", "-"))),
            ("EXIT CODE:", describe_exit_code(meta["exit_code"]) if isinstance(meta.get("exit_code"), int) else "-"),
            ("RUNTIME:", f"{float(meta.get('duration_seconds', 0.0)):.1f}s"),
        ]
        right_meta = [
            ("STARTED:", self._format_time(meta.get("started_at", "-"))),
            ("FINISHED:", self._format_time(meta.get("finished_at", "-"))),
        ]

        self._build_meta_column(header_body, 0, left_meta)
        self._build_meta_column(header_body, 1, right_meta)

        command_frame = ctk.CTkFrame(header_body, fg_color="transparent")
        command_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=4, pady=(8, 2))
        command_frame.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(
            command_frame,
            text="COMMAND:",
            font=mono_font(12, "bold"),
            text_color=GREEN_BRIGHT,
            justify="left",
            anchor="nw",
        ).grid(row=0, column=0, sticky="nw", padx=(0, 10))
        ctk.CTkLabel(
            command_frame,
            text=self._format_command(meta.get("command_line", "-")),
            font=mono_font(12),
            text_color=GREEN_FAINT,
            justify="left",
            anchor="w",
            wraplength=780,
        ).grid(row=0, column=1, sticky="ew")

        viewer, viewer_body = build_section(shell, "Execution Transcript")
        viewer.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 10))
        viewer.grid_rowconfigure(1, weight=1)
        viewer.grid_columnconfigure(0, weight=1)
        viewer_body.grid_rowconfigure(0, weight=1)
        viewer_body.grid_columnconfigure(0, weight=1)

        self.textbox = ctk.CTkTextbox(
            viewer_body,
            fg_color=CONSOLE_BG,
            border_width=1,
            border_color=GREEN_DIM,
            text_color=GREEN_TEXT,
            font=mono_font(13),
            corner_radius=14,
            wrap="none",
        )
        self.textbox.grid(row=0, column=0, sticky="nsew")
        self.textbox.tag_config("error", foreground=RED_ACCENT)
        self.textbox.tag_config("note", foreground=GREEN_FAINT)
        self._load_log()

        actions = ctk.CTkFrame(shell, fg_color="transparent")
        actions.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 12))
        actions.grid_columnconfigure(10, weight=1)
        build_button(actions, "Copy Log", command=self._copy_log).grid(row=0, column=0, padx=(0, 10))
        build_button(actions, "Open Log Folder", command=self._open_folder).grid(row=0, column=1, padx=10)
        if self.native_log_path is not None:
            build_button(actions, "Open Native Log", command=self._open_native_log).grid(row=0, column=2, padx=10)
        build_button(actions, "Close", role="danger", command=self.destroy, width=120).grid(row=0, column=11, sticky="e")

        self.focus()

    def _build_meta_column(self, parent: Any, column: int, rows: list[tuple[str, str]]) -> None:
        column_frame = ctk.CTkFrame(parent, fg_color="transparent")
        column_frame.grid(row=0, column=column, sticky="nw", padx=4, pady=4)
        column_frame.grid_columnconfigure(1, weight=1)
        for row_index, (label, value) in enumerate(rows):
            ctk.CTkLabel(
                column_frame,
                text=label,
                font=mono_font(12, "bold"),
                text_color=GREEN_BRIGHT,
                justify="left",
                anchor="w",
            ).grid(row=row_index, column=0, sticky="w", padx=(0, 10), pady=1)
            ctk.CTkLabel(
                column_frame,
                text=value,
                font=mono_font(12),
                text_color=GREEN_MUTED,
                justify="left",
                anchor="w",
            ).grid(row=row_index, column=1, sticky="w", pady=1)

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
            return datetime.fromisoformat(text).strftime("%H:%M")
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
        self.title("Telemetry Settings")
        self.geometry("980x760")
        self.minsize(860, 640)
        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.configure(fg_color=APP_BG)

        self.mapping_vars: dict[str, ctk.StringVar] = {}
        self.mapping_menus: dict[str, ctk.CTkOptionMenu] = {}
        self._wrap_update_job: str | None = None

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.bind("<Configure>", self._on_window_configure)

        shell = ctk.CTkFrame(
            self,
            fg_color=SURFACE_BG,
            corner_radius=24,
            border_width=2,
            border_color=GREEN_DIM,
        )
        shell.grid(row=0, column=0, sticky="nsew", padx=18, pady=18)
        shell.grid_rowconfigure(1, weight=1)
        shell.grid_columnconfigure(0, weight=1)

        header, header_body = build_section(shell, "Assign Sensors", "Bind detected LibreHardwareMonitor sensors to the app's fixed telemetry channels.", title_size=24)
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 10))
        header_body.grid_columnconfigure(0, weight=1)
        self.status_label = tk.Message(
            header_body,
            text="",
            font=("Consolas", 12),
            justify="left",
            width=1,
            bg=PANEL_BG,
            fg=WARNING_AMBER,
            bd=0,
            highlightthickness=0,
            padx=0,
            pady=0,
        )
        self.status_label.grid(row=0, column=0, sticky="ew", padx=4, pady=(0, 4))

        body_frame = ctk.CTkFrame(shell, fg_color="transparent")
        body_frame.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 10))
        body_frame.grid_rowconfigure(0, weight=1)
        body_frame.grid_columnconfigure(0, weight=1)

        mapping_section, mapping_body = build_section(body_frame, "Telemetry Channels", "Each dropdown accepts only compatible sensor roles and signal types.")
        mapping_section.grid(row=0, column=0, sticky="nsew")
        mapping_section.grid_columnconfigure(0, weight=1)
        mapping_section.grid_rowconfigure(2, weight=1)
        mapping_body.grid_rowconfigure(0, weight=1)
        mapping_body.grid_columnconfigure(0, weight=1)

        mapping_frame = ctk.CTkScrollableFrame(
            mapping_body,
            fg_color=CONSOLE_BG,
            corner_radius=16,
            border_width=1,
            border_color=GREEN_DIM,
        )
        mapping_frame.grid(row=0, column=0, sticky="nsew")
        mapping_frame.grid_columnconfigure(0, weight=1)
        for row_index, category in enumerate(TELEMETRY_CATEGORIES):
            card = ctk.CTkFrame(
                mapping_frame,
                fg_color=PANEL_ALT_BG,
                corner_radius=14,
                border_width=1,
                border_color=GREEN_DIM,
            )
            card.grid(row=row_index, column=0, sticky="ew", padx=10, pady=8)
            card.grid_columnconfigure(1, weight=1)

            ctk.CTkLabel(
                card,
                text=category.label,
                font=ui_font(15, "bold"),
                text_color=GREEN_BRIGHT,
                anchor="w",
            ).grid(row=0, column=0, sticky="w", padx=16, pady=(12, 4))
            ctk.CTkLabel(
                card,
                text=f"{category.hardware_roles[0].upper()} / {category.sensor_type.upper()}",
                font=mono_font(11),
                text_color=GREEN_FAINT,
                anchor="w",
            ).grid(row=1, column=0, sticky="w", padx=16, pady=(0, 12))
            var = ctk.StringVar(value=UNASSIGNED_SENSOR_OPTION)
            menu = build_option_menu(
                card,
                variable=var,
                values=[UNASSIGNED_SENSOR_OPTION],
                width=440,
                command=lambda _selected, category_key=category.key: self._on_mapping_changed(category_key),
            )
            menu.grid(row=0, column=1, rowspan=2, sticky="e", padx=16, pady=12)
            self.mapping_vars[category.key] = var
            self.mapping_menus[category.key] = menu

        footer = ctk.CTkFrame(shell, fg_color="transparent")
        footer.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 16))
        footer.grid_columnconfigure(10, weight=1)
        build_button(footer, "Refresh Sensors", command=self._refresh_sensors).grid(row=0, column=0, padx=(0, 10))
        if self.app.pawnio_installer_path is not None:
            build_button(footer, "Install PawnIO Driver", role="danger", command=self._install_pawnio_driver).grid(row=0, column=1, padx=10)
        build_button(footer, "Close", role="secondary", command=self._on_close, width=120).grid(row=0, column=11, sticky="e")

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
        if hasattr(self, "status_label") and self.status_label.winfo_exists():
            status_width = max(160, self.status_label.master.winfo_width() - 8)
            self.status_label.configure(width=status_width)
        self._apply_label_wraps(self)

    def _apply_label_wraps(self, widget: Any) -> None:
        for child in widget.winfo_children():
            if isinstance(child, ctk.CTkLabel):
                parent_width = child.master.winfo_width() if child.master is not None else self.winfo_width()
                child.configure(wraplength=max(160, parent_width - 40))
            self._apply_label_wraps(child)

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
            fg=self.app.telemetry_status_color,
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
        self.geometry("520x320")
        self.minsize(480, 300)
        self.resizable(False, False)
        self.transient(master)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.configure(fg_color=APP_BG)
        self.bind("<Escape>", lambda _event: self._on_close())
        self.after(0, lambda: apply_windows_dark_title_bar(self))

        self.cpu_limit_var = ctk.StringVar(value=str(self.app.settings.get("cpu_temp_limit_c", "")))
        self.gpu_limit_var = ctk.StringVar(value=str(self.app.settings.get("gpu_temp_limit_c", "")))

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        shell = ctk.CTkFrame(
            self,
            fg_color=SURFACE_BG,
            corner_radius=24,
            border_width=2,
            border_color=GREEN_DIM,
        )
        shell.grid(row=0, column=0, sticky="nsew", padx=18, pady=18)
        shell.grid_columnconfigure(0, weight=1)

        header, header_body = build_section(
            shell,
            "Thermal Settings",
            "Set the thermal test abort thresholds used during testing.",
            title_size=24,
        )
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 10))
        header_body.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            header_body,
            text="CPU",
            font=mono_font(13, "bold"),
            text_color=GREEN_BRIGHT,
            anchor="w",
        ).grid(row=0, column=0, sticky="w", padx=(4, 12), pady=8)
        build_entry(header_body, self.cpu_limit_var, width=140, justify="center").grid(row=0, column=1, sticky="w", pady=8)

        ctk.CTkLabel(
            header_body,
            text="GPU",
            font=mono_font(13, "bold"),
            text_color=GREEN_BRIGHT,
            anchor="w",
        ).grid(row=1, column=0, sticky="w", padx=(4, 12), pady=8)
        build_entry(header_body, self.gpu_limit_var, width=140, justify="center").grid(row=1, column=1, sticky="w", pady=8)

        footer = ctk.CTkFrame(shell, fg_color="transparent")
        footer.grid(row=1, column=0, sticky="ew", padx=20, pady=(0, 18))
        footer.grid_columnconfigure(10, weight=1)
        build_button(footer, "Save", role="primary", width=120, command=self._save).grid(row=0, column=8, padx=(0, 10))
        build_button(footer, "Cancel", role="secondary", width=120, command=self._on_close).grid(row=0, column=9)

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
        self.geometry("1320x980")
        self.minsize(1180, 860)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.configure(fg_color=APP_BG)

        self.settings = self.orchestrator.load_settings()
        self.orchestrator.configure_thermal_limits(self.settings)
        self.presets = self.orchestrator.load_presets()
        self.current_summary: dict[str, Any] | None = None

        self.test_vars: dict[str, ctk.BooleanVar] = {}
        self.duration_vars: dict[str, ctk.StringVar] = {}
        self.phase_cards: dict[str, TestPhaseCard] = {}
        self.telemetry_labels: dict[str, ctk.CTkLabel] = {}
        self.telemetry_bars: dict[str, ctk.CTkProgressBar] = {}
        self.telemetry_sensor_choices: dict[str, dict[str, str | None]] = {}
        self.telemetry_selected_labels: dict[str, str] = {
            category.key: UNASSIGNED_SENSOR_OPTION for category in TELEMETRY_CATEGORIES
        }
        self.telemetry_inventory: list[dict[str, Any]] = []
        self.telemetry_status_summary = ""
        self.telemetry_status_brief = ""
        self.telemetry_status_color = WARNING_AMBER
        self.telemetry_header_status_text = ""
        self.telemetry_header_status_color = WARNING_AMBER
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
        self.setup_frame = ctk.CTkFrame(self, fg_color=APP_BG)
        self.dashboard_frame = ctk.CTkFrame(self, fg_color=APP_BG)
        self.setup_frame.grid(row=0, column=0, sticky="nsew")
        self.dashboard_frame.grid(row=0, column=0, sticky="nsew")

        self._build_setup_screen()
        self._build_dashboard_screen()
        self.dashboard_frame.grid_remove()

    def _build_setup_screen(self) -> None:
        frame = self.setup_frame
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)

        setup_scroll = ctk.CTkScrollableFrame(
            frame,
            fg_color=APP_BG,
            corner_radius=0,
            border_width=0,
            scrollbar_fg_color=APP_BG,
            scrollbar_button_color=GREEN_DIM,
            scrollbar_button_hover_color=GREEN_SOFT,
        )
        setup_scroll.grid(row=0, column=0, sticky="nsew")
        setup_scroll.grid_columnconfigure(0, weight=1)

        shell = ctk.CTkFrame(
            setup_scroll,
            fg_color=SURFACE_BG,
            corner_radius=26,
            border_width=2,
            border_color=GREEN_DIM,
        )
        shell.grid(row=0, column=0, sticky="ew", padx=18, pady=18)
        shell.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(shell, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=22, pady=(14, 6))
        header.grid_columnconfigure(0, weight=1, uniform="header_edge")
        header.grid_columnconfigure(1, weight=0)
        header.grid_columnconfigure(2, weight=1, uniform="header_edge")
        ctk.CTkFrame(header, height=2, fg_color=GREEN_DIM, corner_radius=999).grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 14))
        ctk.CTkLabel(
            header,
            text="RWS Stress Tester",
            font=ui_font(34, "bold"),
            text_color=GREEN_NEON,
        ).grid(row=1, column=1)
        build_button(
            header,
            "Thermal Settings",
            command=self._open_thermal_settings,
            width=200,
            height=40,
        ).grid(row=1, column=0, sticky="w", pady=(0, 4))
        build_button(
            header,
            "Assign Sensors",
            command=self._open_telemetry_settings,
            width=200,
            height=40,
        ).grid(row=1, column=2, sticky="e", padx=(12, 0), pady=(0, 4))
        self.telemetry_header_status_label = ctk.CTkLabel(
            header,
            text="",
            justify="right",
            anchor="e",
            text_color=WARNING_AMBER,
            font=mono_font(12),
        )
        self.telemetry_header_status_label.grid(row=2, column=2, sticky="e", padx=(12, 0), pady=(6, 0))

        checkbox_style = {
            "fg_color": GREEN_NEON,
            "hover_color": GREEN_BRIGHT,
            "border_color": GREEN_SOFT,
            "checkmark_color": APP_BG,
            "text_color": GREEN_TEXT,
            "font": mono_font(14),
        }

        phase_section, phase_body = build_section(shell, "Select Test Phases:")
        phase_section.grid(row=1, column=0, sticky="ew", padx=12, pady=(8, 8))
        # Two staggered rows (4 + 3 cards): each card spans two of eight columns.
        for column in range(8):
            phase_body.grid_columnconfigure(column, weight=1, uniform="phasecards")

        test_cards = [
            ("cpu", "CPU", []),
            ("gpu", "GPU", [{"label": "Res", "kind": "entry", "variable": self.gpu_res_var, "width": 90}]),
            ("vram", "VRAM", [{"label": "Size (GB)", "kind": "menu", "variable": self.vram_gb_var, "values": self.vram_size_options, "width": 70}]),
            ("ram", "RAM", [{"label": "Cap (GB)", "kind": "entry", "variable": self.ram_gb_var, "width": 76}]),
            ("storage_read", "Storage (R)", [{"label": "Drive", "kind": "menu", "variable": self.target_drive_var, "values": self.drive_options, "width": 88}]),
            ("storage_write", "Storage (W)", [{"label": "Drive", "kind": "menu", "variable": self.target_drive_var, "values": self.drive_options, "width": 88}]),
            ("psu", "PSU", []),
        ]
        self.phase_cards.clear()
        for index, (key, title, extras) in enumerate(test_cards):
            enabled = ctk.BooleanVar(value=False)
            duration = ctk.StringVar(value=self.default_phase_duration_minutes)
            self.test_vars[key] = enabled
            self.duration_vars[key] = duration
            card = TestPhaseCard(phase_body, title, enabled, duration, extras)
            row, position = divmod(index, 4)
            card.grid(row=row, column=position * 2 + row, columnspan=2, pady=8)
            self.phase_cards[key] = card

        preset_section, preset_body = build_section(shell, "Preset manager:")
        preset_section.grid(row=2, column=0, sticky="ew", padx=12, pady=8)
        preset_body.grid_columnconfigure(0, weight=1)
        preset_controls = ctk.CTkFrame(preset_body, fg_color="transparent")
        preset_controls.grid(row=0, column=0, sticky="w", padx=4, pady=(2, 0))
        self.preset_var = ctk.StringVar(value="Custom")
        preset_menu_frame = ctk.CTkFrame(
            preset_controls,
            fg_color=FIELD_BG,
            corner_radius=10,
            border_width=1,
            border_color=GREEN_DIM,
        )
        preset_menu_frame.grid(row=0, column=0, sticky="w", pady=(0, 12))
        self.preset_menu = build_option_menu(
            preset_menu_frame,
            variable=self.preset_var,
            values=["Custom"],
            command=self._apply_preset,
            width=300,
        )
        self.preset_menu.grid(row=0, column=0, sticky="w", padx=1, pady=1)

        preset_buttons = ctk.CTkFrame(preset_controls, fg_color="transparent")
        preset_buttons.grid(row=1, column=0, sticky="w")
        build_button(preset_buttons, "Save As", command=self._save_preset, width=120).grid(row=0, column=0, padx=(0, 10))
        build_button(preset_buttons, "Delete", command=self._delete_preset, width=120).grid(row=0, column=1, padx=(0, 10))
        build_button(preset_buttons, "Load Current", command=lambda: self._apply_preset(self.preset_var.get()), width=140).grid(row=0, column=2, padx=(0, 10))
        build_button(preset_buttons, "Reset", command=self._reset_test_settings, width=120).grid(row=0, column=3)

        certificate_section, certificate_body = build_section(shell, "Stability Certificate:")
        certificate_section.grid(row=3, column=0, sticky="ew", padx=12, pady=8)
        certificate_body.grid_columnconfigure(0, weight=0)
        certificate_body.grid_columnconfigure(1, weight=0)
        certificate_body.grid_columnconfigure(2, weight=0)
        self.logo_var = ctk.StringVar(value=self.settings.get("logo_path", ""))
        self.generate_report_var = ctk.BooleanVar(value=True)
        self.include_customer_name_var = ctk.BooleanVar(value=bool(self.settings.get("include_customer_name", False)))
        self.customer_name_var = ctk.StringVar(value=self.settings.get("customer_name", ""))
        self.include_machine_name_var = ctk.BooleanVar(value=bool(self.settings.get("include_machine_name", False)))
        self.certificate_machine_name_var = ctk.StringVar(value=self.settings.get("certificate_machine_name", ""))

        ctk.CTkCheckBox(
            certificate_body,
            text="Generate certificate at the completion of the tests:",
            variable=self.generate_report_var,
            **checkbox_style,
        ).grid(row=0, column=0, columnspan=3, sticky="w", padx=4, pady=(2, 10))

        ctk.CTkLabel(certificate_body, text="Logo:", text_color=GREEN_MUTED, font=mono_font(13)).grid(row=1, column=0, sticky="w", padx=4, pady=6)
        self.logo_entry = build_entry(certificate_body, self.logo_var, width=560, justify="left")
        self.logo_entry.grid(row=1, column=1, sticky="w", padx=(0, 10), pady=6)
        build_button(certificate_body, "Browse", command=self._pick_logo, width=120).grid(row=1, column=2, sticky="w", padx=(0, 4), pady=6)

        ctk.CTkCheckBox(
            certificate_body,
            text="Customer Name:",
            variable=self.include_customer_name_var,
            command=self._sync_certificate_field_states,
            **checkbox_style,
        ).grid(row=2, column=0, sticky="w", padx=4, pady=6)
        self.customer_name_entry = build_entry(certificate_body, self.customer_name_var, width=360, justify="left")
        self.customer_name_entry.grid(row=2, column=1, sticky="w", padx=(0, 4), pady=6)

        ctk.CTkCheckBox(
            certificate_body,
            text="Machine Name:",
            variable=self.include_machine_name_var,
            command=self._sync_certificate_field_states,
            **checkbox_style,
        ).grid(row=3, column=0, sticky="w", padx=4, pady=6)
        self.certificate_machine_name_entry = build_entry(certificate_body, self.certificate_machine_name_var, width=360, justify="left")
        self.certificate_machine_name_entry.grid(row=3, column=1, sticky="w", padx=(0, 4), pady=6)
        self._sync_certificate_field_states()

        actions_panel = ctk.CTkFrame(
            shell,
            fg_color=PANEL_BG,
            corner_radius=22,
            border_width=2,
            border_color=GREEN_DIM,
        )
        actions_panel.grid(row=4, column=0, sticky="ew", padx=12, pady=(8, 12))
        actions_panel.grid_columnconfigure(0, weight=1)

        actions_center = ctk.CTkFrame(actions_panel, fg_color="transparent")
        actions_center.grid(row=0, column=0, pady=(16, 10))
        build_button(
            actions_center,
            "START",
            role="primary",
            command=self._start_diagnostics,
            width=240,
            height=58,
            corner_radius=18,
            font=ui_font(24, "bold"),
        ).grid(row=0, column=0)

    def _build_dashboard_screen(self) -> None:
        frame = self.dashboard_frame
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(0, weight=1)

        shell = ctk.CTkFrame(
            frame,
            fg_color=SURFACE_BG,
            corner_radius=26,
            border_width=2,
            border_color=GREEN_DIM,
        )
        shell.grid(row=0, column=0, sticky="nsew", padx=18, pady=18)
        shell.grid_columnconfigure(0, weight=5)
        shell.grid_columnconfigure(1, weight=3)
        shell.grid_rowconfigure(1, weight=0)
        shell.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(
            shell,
            fg_color=PANEL_BG,
            corner_radius=20,
            border_width=2,
            border_color=GREEN_DIM,
        )
        header.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(12, 6))
        header.grid_columnconfigure(0, weight=1)
        header.grid_rowconfigure(0, weight=1)
        status_frame = ctk.CTkFrame(header, fg_color="transparent")
        status_frame.grid(row=0, column=0, sticky="nsew", padx=(18, 12), pady=14)
        status_frame.grid_rowconfigure(0, weight=1)
        status_content = ctk.CTkFrame(status_frame, fg_color="transparent")
        status_content.grid(row=0, column=0, sticky="w")
        status_content.grid_columnconfigure(0, weight=1)

        self.phase_label = ctk.CTkLabel(status_content, text="Waiting", font=ui_font(28, "bold"), text_color=GREEN_NEON)
        self.phase_label.grid(row=0, column=0, sticky="w")
        self.error_flag_label = ctk.CTkLabel(
            status_content,
            text="",
            text_color=RED_ACCENT,
            font=mono_font(14, "bold"),
        )
        self.error_flag_label.grid(row=0, column=1, sticky="e", padx=(12, 0))
        self.phase_status_label = ctk.CTkLabel(
            status_content,
            text="",
            text_color=GREEN_MUTED,
            font=mono_font(12),
            anchor="w",
            justify="left",
        )
        self.phase_status_label.grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 0))

        timer_strip = ctk.CTkFrame(header, fg_color="transparent")
        timer_strip.grid(row=0, column=1, sticky="e", padx=(0, 18), pady=12)

        phase_timer = ctk.CTkFrame(
            timer_strip,
            fg_color=PANEL_ALT_BG,
            corner_radius=14,
            border_width=1,
            border_color=GREEN_DIM,
            width=190,
            height=74,
        )
        phase_timer.grid(row=0, column=0, padx=(0, 10))
        phase_timer.grid_propagate(False)
        phase_timer.grid_columnconfigure(0, weight=1)
        phase_timer.grid_rowconfigure(0, weight=1)
        phase_timer.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(
            phase_timer,
            text="PHASE REMAINING",
            text_color=GREEN_FAINT,
            font=mono_font(16, "bold"),
        ).grid(row=0, column=0, sticky="s", padx=8, pady=(6, 0))
        self.countdown_label = ctk.CTkLabel(
            phase_timer,
            text="00:00",
            text_color=GREEN_BRIGHT,
            font=ui_font(24, "bold"),
        )
        self.countdown_label.grid(row=1, column=0, sticky="n", padx=8, pady=(0, 6))

        matrix_timer = ctk.CTkFrame(
            timer_strip,
            fg_color=PANEL_ALT_BG,
            corner_radius=14,
            border_width=1,
            border_color=GREEN_DIM,
            width=190,
            height=74,
        )
        matrix_timer.grid(row=0, column=1)
        matrix_timer.grid_propagate(False)
        matrix_timer.grid_columnconfigure(0, weight=1)
        matrix_timer.grid_rowconfigure(0, weight=1)
        matrix_timer.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(
            matrix_timer,
            text="MATRIX REMAINING",
            text_color=GREEN_FAINT,
            font=mono_font(16, "bold"),
        ).grid(row=0, column=0, sticky="s", padx=8, pady=(6, 0))
        self.full_matrix_countdown_label = ctk.CTkLabel(
            matrix_timer,
            text="00:00",
            text_color=GREEN_BRIGHT,
            font=ui_font(24, "bold"),
        )
        self.full_matrix_countdown_label.grid(row=1, column=0, sticky="n", padx=8, pady=(0, 6))

        telemetry_section, telemetry_body = build_section(shell, "Live Telemetry")
        telemetry_section.grid(row=1, column=0, sticky="ew", padx=(12, 6), pady=(6, 2))
        telemetry_body.grid_columnconfigure((0, 1, 2), weight=1, uniform="telemetry")
        cards = [
            ("cpu_temp", "CPU Temperature"),
            ("cpu_load", "CPU Load"),
            ("gpu_temp", "GPU Temperature"),
            ("gpu_load", "GPU Load"),
            ("ram_load", "RAM Load"),
            ("storage_load", "Storage Load"),
            ("cpu_power", "CPU Power"),
            ("gpu_power", "GPU Power"),
            ("total_power", "Total Power"),
        ]
        for index, (key, label) in enumerate(cards):
            card = ctk.CTkFrame(
                telemetry_body,
                fg_color=PANEL_ALT_BG,
                corner_radius=14,
                border_width=1,
                border_color=GREEN_DIM,
            )
            card.grid_rowconfigure(1, weight=1)
            row = index // 3
            col = index % 3
            card.grid(row=row, column=col, padx=5, pady=5, sticky="nsew")
            ctk.CTkLabel(card, text=label, font=ui_font(13, "bold"), text_color=GREEN_BRIGHT).grid(
                row=0, column=0, sticky="w", padx=12, pady=(10, 2)
            )
            value_label = ctk.CTkLabel(card, text="0.0", font=ui_font(18, "bold"), text_color=GREEN_TEXT)
            value_label.grid(row=1, column=0, sticky="w", padx=12, pady=(0, 2))
            bar = ctk.CTkProgressBar(card, progress_color=GREEN_NEON, fg_color=GREEN_DIM, height=6)
            bar.set(0)
            bar.grid(row=2, column=0, sticky="ew", padx=12, pady=(0, 10))
            self.telemetry_labels[key] = value_label
            self.telemetry_bars[key] = bar

        logs_section, logs_body = build_section(shell, "Completed Test Logs")
        logs_section.grid(row=1, column=1, rowspan=2, sticky="nsew", padx=(6, 12), pady=6)
        logs_section.grid_rowconfigure(1, weight=1)
        logs_body.grid_columnconfigure(0, weight=1)
        logs_body.grid_rowconfigure(1, weight=1)
        self.logs_status_label = ctk.CTkLabel(
            logs_body,
            text="No completed phase logs yet.",
            text_color=GREEN_FAINT,
            justify="left",
            anchor="w",
            font=mono_font(12),
            wraplength=340,
        )
        self.logs_status_label.grid(row=0, column=0, sticky="ew", padx=4, pady=(0, 10))
        self.dashboard_logs_list = ctk.CTkScrollableFrame(
            logs_body,
            fg_color=CONSOLE_BG,
            corner_radius=14,
            border_width=1,
            border_color=GREEN_DIM,
            height=420,
        )
        self.dashboard_logs_list.grid(row=1, column=0, sticky="nsew")
        self.dashboard_logs_list.grid_columnconfigure(0, weight=1)
        console_section, console_body = build_section(shell, "Live Engine Console")
        console_section.grid(row=2, column=0, sticky="nsew", padx=(12, 6), pady=(4, 12))
        console_section.grid_rowconfigure(1, weight=1)
        console_body.grid_rowconfigure(0, weight=1)
        console_body.grid_columnconfigure(0, weight=1)
        self.console = ctk.CTkTextbox(
            console_body,
            state="disabled",
            fg_color=CONSOLE_BG,
            border_width=1,
            border_color=GREEN_DIM,
            text_color=GREEN_TEXT,
            font=mono_font(13),
            corner_radius=14,
            wrap="word",
        )
        self.console.tag_config("error", foreground=RED_ACCENT)
        self.console.grid(row=0, column=0, sticky="nsew")

        button_bar = ctk.CTkFrame(console_body, fg_color="transparent")
        button_bar.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        button_bar.grid_columnconfigure((0, 1, 2), weight=1)
        self.abort_button = build_button(button_bar, "Abort Session", role="danger", command=self.orchestrator.abort_session)
        self.abort_button.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        build_button(button_bar, "Refresh Review", command=self._refresh_completed_logs_ui).grid(row=0, column=1, sticky="ew", padx=8)
        self.back_to_setup_button = build_button(button_bar, "Back To Setup", command=self._back_to_setup, state="disabled")
        self.back_to_setup_button.grid(row=0, column=2, sticky="ew", padx=(8, 0))
        self._set_dashboard_controls(session_running=False)
        self._refresh_completed_logs_ui()

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
            self.telemetry_status_color = RED_ACCENT
            self.telemetry_header_status_text = "Sensor scan failed"
            self.telemetry_header_status_color = RED_ACCENT
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
            RED_ACCENT
            if invalid_saved_categories
            else WARNING_AMBER
            if (
                unassigned_categories
                or no_data_categories
                or self.pawnio_status.get("is_installed") is False
            )
            else SUCCESS_GREEN
        )
        if invalid_saved_categories or unassigned_categories:
            self.telemetry_header_status_text = "Unassigned sensor(s) detected"
            self.telemetry_header_status_color = RED_ACCENT
        elif self.pawnio_status.get("is_installed") is False:
            self.telemetry_header_status_text = "PawnIO driver missing"
            self.telemetry_header_status_color = WARNING_AMBER
        else:
            self.telemetry_header_status_text = "All sensors assigned"
            self.telemetry_header_status_color = WARNING_AMBER
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
        self.countdown_label.configure(text="00:00")
        self.full_matrix_countdown_label.configure(text="00:00")
        
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
                self.error_flag_label.configure(text="ERRORS DETECTED")
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
        self.phase_label.configure(text=f"Phase {event['phase_index']}/{event['total_phases']}: {event['label']}")
        if event.get("finishing"):
            # Phase duration is over; engines get a short grace period to exit on their own.
            self.countdown_label.configure(text="FINISHING")
            self.phase_status_label.configure(
                text=f"Duration reached - waiting for engines to finish ({int(event.get('finishing_remaining_seconds', 0))}s max)"
            )
        else:
            self.phase_status_label.configure(text="")
            remaining = int(event["remaining_seconds"])
            mins, secs = divmod(max(0, remaining), 60)
            self.countdown_label.configure(text=f"{mins:02d}:{secs:02d}")
        
        total_remaining = int(event.get("total_remaining_seconds", 0))
        t_mins, t_secs = divmod(max(0, total_remaining), 60)
        if hasattr(self, "full_matrix_countdown_label"):
            self.full_matrix_countdown_label.configure(text=f"{t_mins:02d}:{t_secs:02d}")

    def _update_telemetry(self, sample: dict[str, Any]) -> None:
        if sample.get("cpu_power") is not None or sample.get("gpu_power") is not None:
            sample["total_power"] = (sample.get("cpu_power") or 0.0) + (sample.get("gpu_power") or 0.0)
        else:
            sample["total_power"] = None

        units = {
            "cpu_temp": " C",
            "gpu_temp": " C",
            "cpu_load": "%",
            "gpu_load": "%",
            "ram_load": "%",
            "storage_load": "%",
            "cpu_power": " W",
            "gpu_power": " W",
            "total_power": " W",
        }
        scales = {
            "cpu_temp": 110.0,
            "gpu_temp": 100.0,
            "cpu_load": 100.0,
            "gpu_load": 100.0,
            "ram_load": 100.0,
            "storage_load": 100.0,
            "cpu_power": 250.0,
            "gpu_power": 450.0,
            "total_power": 700.0,
        }
        mappings = self.settings.get("telemetry_mappings", {})
        for key, value in list(sample.items()):
            if key not in self.telemetry_labels:
                continue
            if value is None:
                self.telemetry_labels[key].configure(
                    text="No Data" if mappings.get(key) else "Unassigned"
                )
                self.telemetry_bars[key].set(0)
                continue
            self.telemetry_labels[key].configure(text=f"{value:.1f}{units.get(key, '')}")
            max_value = scales.get(key, 100.0)
            self.telemetry_bars[key].set(max(0.0, min(1.0, float(value) / max_value)))
            
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
        if count == 0:
            status_text = "No completed phase logs yet."
        elif count == 1:
            status_text = "1 completed phase log is ready for review."
        else:
            status_text = f"{count} completed phase logs are ready for review."

        self.logs_status_label.configure(text=status_text)
        if hasattr(self, "dashboard_logs_list"):
            for widget in self.dashboard_logs_list.winfo_children():
                widget.destroy()

            if count == 0:
                ctk.CTkLabel(
                    self.dashboard_logs_list,
                    text="Completed phases will appear here as soon as each test finishes.",
                    text_color=GREEN_FAINT,
                    font=mono_font(12),
                    anchor="center",
                    justify="center",
                    wraplength=260,
                ).grid(row=0, column=0, sticky="ew", padx=12, pady=12)
            else:
                for index, meta in enumerate(self.completed_logs_data):
                    row = ctk.CTkFrame(
                        self.dashboard_logs_list,
                        fg_color=PANEL_ALT_BG,
                        corner_radius=12,
                        border_width=1,
                        border_color=GREEN_DIM,
                    )
                    row.grid(row=index, column=0, sticky="ew", padx=10, pady=6)
                    row.grid_columnconfigure(0, weight=1)
                    status_color = SUCCESS_GREEN if meta["status"] == "PASSED" else RED_ACCENT
                    ctk.CTkLabel(
                        row,
                        text=meta["test_name"],
                        font=ui_font(15, "bold"),
                        text_color=GREEN_BRIGHT,
                    ).grid(row=0, column=0, sticky="w", padx=12, pady=(10, 2))
                    ctk.CTkLabel(
                        row,
                        text=f"{meta['status']} | {float(meta.get('duration_seconds', 0.0)):.1f}s",
                        text_color=status_color,
                        font=mono_font(11, "bold"),
                    ).grid(row=1, column=0, sticky="w", padx=12, pady=(0, 8))
                    build_button(row, "Open", command=lambda m=meta: self._open_log_modal(m), width=88, height=34).grid(
                        row=0,
                        column=1,
                        rowspan=2,
                        padx=12,
                        pady=10,
                    )
    def _open_log_modal(self, meta: dict[str, Any]) -> None:
        LogViewerModal(self, meta)

    def _handle_finished(self, summary: dict[str, Any]) -> None:
        self._load_completed_logs_from_disk()
        self.phase_label.configure(text=summary["verdict"])
        self.phase_status_label.configure(text="Session complete. Review logs below.")
        self.countdown_label.configure(text="DONE")
        self.full_matrix_countdown_label.configure(text="DONE")
        self._set_dashboard_controls(session_running=False)
        report_path = summary.get("report_path", "")
        report_error = summary.get("report_error", "")
        message = summary["verdict"]
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
        self.phase_label.configure(text="Session Aborted")
        self.phase_status_label.configure(text="Review the live console, telemetry, and any completed phase logs.")
        self.countdown_label.configure(text="ABORTED")
        self.full_matrix_countdown_label.configure(text="ABORTED")
        self._set_dashboard_controls(session_running=False)

    def _set_dashboard_controls(self, session_running: bool) -> None:
        self.abort_button.configure(state="normal" if session_running else "disabled")
        self.back_to_setup_button.configure(state="disabled" if session_running else "normal")

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
