#!/usr/bin/env python3
"""Desktop UI for the deterministic Bazaar asset generator."""

from __future__ import annotations

import hashlib
import json
import queue
import shutil
import sys
import threading
import traceback
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Callable
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

from PIL import Image, ImageGrab, ImageTk

from asset_generator_core import (
    ASSET_GENERATOR_VERSION,
    GeneratorProfile,
    LivePreviewRenderer,
    PipelineResult,
    authoring_adapters,
    ensure_local_badge_assets,
    profile_for_workspace_edit,
    retarget_automatic_pack_id,
    run_pipeline,
)
from bazaar_skin_manager import manager_root
from mod_studio_core import PROJECT_ROOT, WORKSPACES_ROOT, StudioWorkspace
from mod_studio_core import remove_color_screen
from skin_pack_builder import derive_small_icon_file, has_authored_transparency

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    RootClass = TkinterDnD.Tk
    DND_AVAILABLE = True
except ImportError:
    DND_FILES = "DND_Files"
    RootClass = tk.Tk
    DND_AVAILABLE = False


COLORS = {
    "window": "#10151d",
    "panel": "#171e29",
    "panel_alt": "#1e2734",
    "line": "#303c4d",
    "text": "#edf2f7",
    "muted": "#9aa8ba",
    "accent": "#66d6b3",
    "accent_dark": "#214b43",
    "warning": "#f0bb64",
    "danger": "#ef8585",
}

DEFAULT_PROFILE = (
    PROJECT_ROOT
    / "generator-projects"
    / "dooley-chameleon"
    / "generator-profile.json"
)
USER_PROJECT_ROOT = manager_root() / "asset-generator" / "current"
USER_PROFILE = USER_PROJECT_ROOT / "generator-profile.json"


def default_output_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return PROJECT_ROOT


PREVIEW_SLOTS = (
    ("中央立绘", "standing_overlay"),
    ("徽章", "hero_select"),
    ("商店 / 对局头像（背景合成）", "portrait_gameplay"),
    ("列表头像", "portrait_small"),
    ("小图标", "hero_icon_small"),
)
SMALL_ICON_SOURCE_PREVIEW = "small_icon_source_preview"
PREVIEW_CARDS = (
    ("中央立绘", "standing_overlay"),
    ("徽章", "hero_select"),
    ("商店 / 对局头像（背景合成）", "portrait_gameplay"),
    ("列表头像", "portrait_small"),
    ("图标生成源图", SMALL_ICON_SOURCE_PREVIEW),
    ("小图标", "hero_icon_small"),
)
ICON_MODE_LABELS = {
    "未提供时不生成": "none",
    "用户提供": "user",
    "从人物生成：描边": "outline",
    "从人物生成：色块缝隙": "block-gaps",
    "从人物生成：实心剪影": "silhouette",
}
ICON_MODE_NAMES = {value: label for label, value in ICON_MODE_LABELS.items()}


class AssetGeneratorUI:
    def __init__(
        self,
        parent: tk.Misc | None = None,
        *,
        on_import: Callable[[GeneratorProfile, PipelineResult], None] | None = None,
        on_generated: Callable[[GeneratorProfile, PipelineResult], None] | None = None,
        on_generation_failed: Callable[[str], None] | None = None,
        on_material_import: Callable[[str, Path], None] | None = None,
        on_choose_asset: Callable[[str], Path | None] | None = None,
        on_effective_action: Callable[[str], bool] | None = None,
    ) -> None:
        self.embedded = parent is not None
        self.root = parent.winfo_toplevel() if parent is not None else RootClass()
        self.host = parent if parent is not None else self.root
        self.on_import = on_import
        self.on_generated = on_generated
        self.on_generation_failed = on_generation_failed
        self.on_material_import = on_material_import
        self.on_choose_asset = on_choose_asset
        self.on_effective_action = on_effective_action
        self.pending_embedded_action: str | None = None
        self.editing_workspace_id: str | None = None
        if not self.embedded:
            self.root.title(
                f"The Bazaar 素材包制作器 v{ASSET_GENERATOR_VERSION}"
            )
            screen_width = self.root.winfo_screenwidth()
            screen_height = self.root.winfo_screenheight()
            window_width = min(1380, max(1024, screen_width - 80))
            window_height = min(900, max(640, screen_height - 140))
            window_x = max(0, (screen_width - window_width) // 2)
            window_y = max(0, (screen_height - window_height - 40) // 2)
            self.root.geometry(
                f"{window_width}x{window_height}+{window_x}+{window_y}"
            )
            self.root.minsize(1024, 640)
            self.root.configure(bg=COLORS["window"])
            try:
                self.root.tk.call("tk", "scaling", 1.25)
            except tk.TclError:
                pass
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.profile: GeneratorProfile | None = None
        self.preview_canvases: dict[str, tk.Canvas] = {}
        self.preview_photos: dict[str, ImageTk.PhotoImage] = {}
        self.preview_scales: dict[str, float] = {}
        self.preview_offset_vars: dict[str, tk.StringVar] = {}
        self.preview_drag_origins: dict[str, tuple[int, int, int, int]] = {}
        self.output_offsets: dict[str, tuple[int, int]] = {}
        self.user_small_icon_path = ""
        self.small_icon_brown_preview_var = tk.BooleanVar(value=False)
        self.live_renderer: LivePreviewRenderer | None = None
        self.manual_override_pack_id = ""
        self.manual_preview_overrides: dict[str, Image.Image] = {}
        self.preview_refresh_job: str | None = None
        self.pending_preview_slots: set[str] = set()
        self.path_entries: dict[str, ttk.Entry] = {}
        self.form_scroll_canvases: list[tk.Canvas] = []
        self.character_offset_x = 0
        self.character_offset_y = 0
        self.character_scale = 1.0
        self.background_offset_x = 0
        self.background_offset_y = 0
        self.background_scale = 1.0
        self.character_canvas_photo: ImageTk.PhotoImage | None = None
        self.character_checker_photo: ImageTk.PhotoImage | None = None
        self.character_canvas_scale = 1.0
        self.character_canvas_origin = (0, 0)
        self.character_drag_origin: tuple[int, int, int, int] | None = None
        self.busy = False
        self.authoring_adapter_records = authoring_adapters()
        if not self.authoring_adapter_records:
            raise RuntimeError("没有可用于素材生成的已验证英雄适配器。")
        self.adapter_labels = {
            f"{record.hero} · {record.skin}": record.adapter_id
            for record in self.authoring_adapter_records
        }
        self._configure_style()
        self._build_ui()
        self.root.bind("<Control-v>", self._global_paste, add="+")
        self.root.bind("<Control-V>", self._global_paste, add="+")
        self.root.bind_all(
            "<Control-Key-1>",
            lambda _event: self.authoring_pages.select(0),
        )
        self.root.bind_all(
            "<Control-Key-2>",
            lambda _event: self.authoring_pages.select(1),
        )
        self.root.bind_all("<MouseWheel>", self._scroll_form_under_pointer, add="+")
        if USER_PROFILE.is_file():
            self._load_profile(USER_PROFILE, fallback_to_new=True)
        elif DEFAULT_PROFILE.is_file():
            self._load_profile(DEFAULT_PROFILE)
        else:
            self._new_project()
        self.root.after(100, self._poll_events)

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            ".",
            background=COLORS["panel"],
            foreground=COLORS["text"],
            fieldbackground=COLORS["panel_alt"],
            bordercolor=COLORS["line"],
            lightcolor=COLORS["line"],
            darkcolor=COLORS["line"],
            font=("Microsoft YaHei UI", 10),
        )
        style.configure("Window.TFrame", background=COLORS["window"])
        style.configure("Alt.TFrame", background=COLORS["panel_alt"])
        style.configure(
            "Alt.TLabel",
            background=COLORS["panel_alt"],
            foreground=COLORS["text"],
        )
        style.configure("TLabel", background=COLORS["panel"], foreground=COLORS["text"])
        style.configure(
            "Muted.TLabel", background=COLORS["panel"], foreground=COLORS["muted"]
        )
        style.configure(
            "Title.TLabel",
            background=COLORS["window"],
            foreground=COLORS["text"],
            font=("Microsoft YaHei UI", 20, "bold"),
        )
        style.configure(
            "Accent.TButton",
            background=COLORS["accent"],
            foreground="#0b211b",
            padding=(16, 9),
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        style.map(
            "Accent.TButton",
            background=[("active", "#80e5c7"), ("disabled", "#45635d")],
        )
        style.configure("TButton", padding=(12, 8))
        style.configure(
            "Horizontal.TProgressbar",
            troughcolor=COLORS["panel_alt"],
            background=COLORS["accent"],
        )

    def _build_ui(self) -> None:
        outer = ttk.Frame(
            self.host,
            style="Window.TFrame" if not self.embedded else "TFrame",
            padding=18 if not self.embedded else 0,
        )
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(
            outer,
            style="Window.TFrame" if not self.embedded else "TFrame",
        )
        header.pack(fill="x", pady=(0, 14))
        ttk.Label(
            header,
            text="皮肤制作" if self.embedded else "素材包制作器",
            style="Title.TLabel" if not self.embedded else "TLabel",
            font=("Microsoft YaHei UI", 17, "bold") if self.embedded else None,
        ).pack(side="left")
        ttk.Label(
            header,
            text=(
                "放入一级素材、调整预览；完成后导入皮肤库或导出到指定位置"
                if self.embedded
                else "放入素材、调整预览、生成素材包；安装继续复用 Skin Manager"
            ),
            style="Muted.TLabel",
        ).pack(side="left", padx=(18, 0), pady=(9, 0))
        ttk.Button(header, text="载入配置…", command=self._choose_profile).pack(side="right")
        ttk.Button(header, text="保存配置", command=self._save_profile).pack(
            side="right", padx=(0, 8)
        )
        ttk.Button(header, text="新建项目", command=self._new_project).pack(
            side="right", padx=(0, 8)
        )

        body = ttk.Panedwindow(outer, orient="horizontal")
        body.pack(fill="both", expand=True)
        form = ttk.Frame(body, padding=16)
        preview = ttk.Frame(body, style="Alt.TFrame", padding=16)
        body.add(form, weight=0)
        body.add(preview, weight=1)
        self._build_form(form)
        self._build_preview(preview)

        footer = ttk.Frame(outer, style="Window.TFrame")
        footer.pack(fill="x", pady=(14, 0))
        self.progress = ttk.Progressbar(footer, mode="indeterminate")
        self.progress.pack(side="left", fill="x", expand=True, padx=(0, 12))
        self.status_var = tk.StringVar(value="等待载入生成配置")
        ttk.Label(footer, textvariable=self.status_var, style="Muted.TLabel").pack(
            side="left"
        )

    def _scrollable_notebook_page(
        self,
        notebook: ttk.Notebook,
        title: str,
    ) -> ttk.Frame:
        page = ttk.Frame(notebook)
        canvas = tk.Canvas(
            page,
            bg=COLORS["panel"],
            highlightthickness=0,
            borderwidth=0,
            yscrollincrement=24,
        )
        scrollbar = ttk.Scrollbar(page, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        content = ttk.Frame(canvas, padding=14)
        window = canvas.create_window((0, 0), window=content, anchor="nw")

        def update_scroll_region(_event: tk.Event | None = None) -> None:
            bounds = canvas.bbox("all")
            if bounds is not None:
                canvas.configure(scrollregion=bounds)

        content.bind("<Configure>", update_scroll_region)
        canvas.bind(
            "<Configure>",
            lambda event: (
                canvas.itemconfigure(window, width=event.width),
                update_scroll_region(),
            ),
        )
        notebook.add(page, text=title)
        self.form_scroll_canvases.append(canvas)
        return content

    def _scroll_form_under_pointer(self, event: tk.Event) -> str | None:
        pointer_x = self.root.winfo_pointerx()
        pointer_y = self.root.winfo_pointery()
        for canvas in self.form_scroll_canvases:
            if not canvas.winfo_viewable():
                continue
            left = canvas.winfo_rootx()
            top = canvas.winfo_rooty()
            if not (
                left <= pointer_x < left + canvas.winfo_width()
                and top <= pointer_y < top + canvas.winfo_height()
            ):
                continue
            bounds = canvas.bbox("all")
            if bounds is None or bounds[3] - bounds[1] <= canvas.winfo_height():
                return None
            delta = int(getattr(event, "delta", 0))
            units = -int(delta / 120) if abs(delta) >= 120 else (-1 if delta > 0 else 1)
            canvas.yview_scroll(units * 3, "units")
            return "break"
        return None

    def _build_form(self, parent: ttk.Frame) -> None:
        parent.configure(width=460)
        self.vars = {
            "adapter": tk.StringVar(),
            "pack_id": tk.StringVar(),
            "name": tk.StringVar(),
            "version": tk.StringVar(),
            "character": tk.StringVar(),
            "background": tk.StringVar(),
            "small_icon": tk.StringVar(),
            "small_icon_source": tk.StringVar(),
            "metadata": tk.StringVar(),
            "badge_root": tk.StringVar(),
            "workspace_root": tk.StringVar(),
            "output_zip": tk.StringVar(),
            "game_dir": tk.StringVar(),
        }
        actions = ttk.Frame(parent)
        actions.pack(side="bottom", fill="x", pady=(12, 0))
        self.action_buttons: list[ttk.Button] = []
        action_specs = (
            (
                ("导入到皮肤库", self._embedded_import, True),
                ("导出到指定位置…", self._embedded_export, False),
            )
            if self.embedded
            else (
                ("1  生成资产包", lambda: self._start(("generate",)), False),
                ("2  导入 Skin Manager", lambda: self._start(("import",)), False),
                ("3  部署并运行 Doctor", lambda: self._start(("import", "deploy")), False),
                ("从头运行全部", lambda: self._start(("generate", "import", "deploy")), True),
            )
        )
        for text, command, accent in action_specs:
            button = ttk.Button(
                actions,
                text=text,
                style="Accent.TButton" if accent else "TButton",
                command=command,
            )
            button.pack(fill="x", pady=4)
            self.action_buttons.append(button)

        self.authoring_pages = ttk.Notebook(parent)
        self.authoring_pages.pack(side="top", fill="both", expand=True)
        materials = self._scrollable_notebook_page(self.authoring_pages, "素材")
        advanced = self._scrollable_notebook_page(
            self.authoring_pages,
            "高级设置",
        )

        ttk.Label(
            materials,
            text="放入现有素材",
            font=("Microsoft YaHei UI", 15, "bold"),
        ).pack(anchor="w")
        target = ttk.LabelFrame(materials, text="生成目标", padding=10)
        target.pack(fill="x", pady=(10, 8))
        self.adapter_display_var = tk.StringVar()
        self.adapter_selector = ttk.Combobox(
            target,
            textvariable=self.adapter_display_var,
            values=tuple(self.adapter_labels),
            state="readonly",
        )
        self.adapter_selector.pack(fill="x")
        self.adapter_selector.bind(
            "<<ComboboxSelected>>", self._authoring_target_changed
        )
        ttk.Label(
            target,
            text="只列出同时具备 Manager 部署契约和确定性生成配方的英雄皮肤。",
            style="Muted.TLabel",
            wraplength=365,
        ).pack(anchor="w", pady=(5, 0))
        ttk.Label(
            materials,
            text="人物图可直接拖到右侧大画布，或点击画布后按 Ctrl+V。背景和小图标没有就留空；小图标也可从单独的图标源图生成。",
            style="Muted.TLabel",
            wraplength=390,
        ).pack(anchor="w", pady=(6, 16))
        inputs = ttk.Frame(materials)
        inputs.pack(fill="x", pady=(0, 10))
        self._path_field(inputs, 0, "人物源图", "character", (("PNG", "*.png"),))
        self._path_field(
            inputs,
            1,
            "背景",
            "background",
            (("Images", "*.png *.jpg *.jpeg *.bmp"),),
        )
        self._path_field(inputs, 2, "小图标", "small_icon", (("PNG", "*.png"),))
        self._path_field(
            inputs,
            3,
            "图标生成源图",
            "small_icon_source",
            (("Images", "*.png *.jpg *.jpeg *.bmp *.webp"),),
        )
        ttk.Label(inputs, text="图标生成方式").grid(
            row=4, column=0, sticky="w", pady=(12, 4)
        )
        self.small_icon_mode_var = tk.StringVar(value="未提供时不生成")
        icon_mode = ttk.Combobox(
            inputs,
            textvariable=self.small_icon_mode_var,
            values=tuple(ICON_MODE_LABELS),
            state="readonly",
        )
        icon_mode.grid(row=4, column=1, columnspan=4, sticky="ew", padx=(10, 0), pady=(12, 4))
        icon_mode.bind("<<ComboboxSelected>>", self._small_icon_mode_changed)

        screen = ttk.LabelFrame(materials, text="绿幕 / 白幕", padding=10)
        screen.pack(fill="x", pady=(10, 0))
        self.chroma_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            screen,
            text="导入或粘贴时扣除底色",
            variable=self.chroma_enabled,
        ).grid(row=0, column=0, columnspan=3, sticky="w")
        self.chroma_color = tk.StringVar(value="#00FF00")
        ttk.Button(
            screen,
            text="绿幕",
            command=lambda: self._select_chroma_preset("#00FF00"),
        ).grid(row=1, column=0, sticky="ew", pady=(7, 0))
        ttk.Button(
            screen,
            text="白幕",
            command=lambda: self._select_chroma_preset("#FFFFFF"),
        ).grid(row=1, column=1, sticky="ew", padx=5, pady=(7, 0))
        ttk.Button(
            screen,
            textvariable=self.chroma_color,
            command=self._choose_chroma_color,
        ).grid(row=1, column=2, sticky="ew", pady=(7, 0))
        ttk.Label(screen, text="容差", style="Muted.TLabel").grid(
            row=2, column=0, sticky="w", pady=(7, 0)
        )
        self.chroma_tolerance = tk.IntVar(value=28)
        ttk.Spinbox(
            screen,
            from_=0,
            to=128,
            width=6,
            textvariable=self.chroma_tolerance,
        ).grid(row=2, column=1, sticky="w", pady=(7, 0))
        for column in range(3):
            screen.columnconfigure(column, weight=1)

        ttk.Label(
            materials,
            text="所有生成位置都在右侧直接拖动；不需要先载入配置文件。",
            style="Muted.TLabel",
            wraplength=390,
        ).pack(anchor="w", pady=(10, 0))

        ttk.Label(advanced, text="项目", font=("Microsoft YaHei UI", 13, "bold")).pack(
            anchor="w"
        )
        grid = ttk.Frame(advanced)
        grid.pack(fill="x", pady=(10, 14))
        self._field(grid, 0, "适配器 ID（随生成目标同步）", "adapter")
        self._field(grid, 1, "内部 ID（自动生成，防止覆盖其他皮肤）", "pack_id", readonly=True)
        self._field(grid, 2, "名称", "name")
        self._field(grid, 3, "版本", "version")
        paths = ttk.Frame(advanced)
        paths.pack(fill="x")
        self._path_field(paths, 0, "输入元数据", "metadata", (("JSON", "*.json"),))
        self._path_field(paths, 1, "徽章模板目录", "badge_root", directory=True)
        self._path_field(paths, 2, "生成工作区", "workspace_root", directory=True)
        self._path_field(paths, 3, "输出 ZIP", "output_zip", (("ZIP", "*.zip"),), save=True)
        self._path_field(paths, 4, "游戏目录", "game_dir", directory=True)

        self.clean_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            advanced,
            text="每次生成前清空专用输出工作区（源文件不会被删除）",
            variable=self.clean_var,
        ).pack(anchor="w", pady=(14, 0))

    def _field(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        key: str,
        *,
        readonly: bool = False,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(
            parent,
            textvariable=self.vars[key],
            width=40,
            state="readonly" if readonly else "normal",
        ).grid(
            row=row, column=1, sticky="ew", padx=(10, 0), pady=4
        )
        parent.columnconfigure(1, weight=1)

    def _path_field(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        key: str,
        filetypes: tuple[tuple[str, str], ...] = (("All files", "*.*"),),
        *,
        directory: bool = False,
        save: bool = False,
    ) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        entry = ttk.Entry(parent, textvariable=self.vars[key], width=32)
        entry.grid(row=row, column=1, sticky="ew", padx=(10, 6), pady=4)
        self.path_entries[key] = entry
        if key in {"character", "background", "small_icon", "small_icon_source"}:
            entry.bind("<Control-v>", lambda event, selected=key: self._paste_input(selected))
            entry.bind("<Control-V>", lambda event, selected=key: self._paste_input(selected))
            if DND_AVAILABLE:
                entry.drop_target_register(DND_FILES)
                entry.dnd_bind(
                    "<<Drop>>",
                    lambda event, selected=key: self._drop_input(selected, event.data),
                )

        def browse() -> None:
            current = Path(self.vars[key].get() or PROJECT_ROOT)
            initial = current if current.is_dir() else current.parent
            if directory:
                selected = filedialog.askdirectory(initialdir=initial)
            elif save:
                selected = filedialog.asksaveasfilename(
                    initialdir=initial,
                    initialfile=current.name,
                    defaultextension=".zip",
                    filetypes=filetypes,
                )
            else:
                selected = filedialog.askopenfilename(initialdir=initial, filetypes=filetypes)
            if selected:
                if key in {"character", "background", "small_icon", "small_icon_source"}:
                    self._accept_material_file(key, Path(selected))
                else:
                    self.vars[key].set(selected)
                    self._input_changed(key)

        ttk.Button(parent, text="…", width=3, command=browse).grid(row=row, column=2, pady=4)
        if key in {"character", "background", "small_icon", "small_icon_source"}:
            ttk.Button(
                parent,
                text="粘贴",
                width=5,
                command=lambda selected=key: self._paste_material_button(selected),
            ).grid(row=row, column=3, padx=(6, 0), pady=4)
            ttk.Button(
                parent,
                text="清空",
                width=5,
                command=lambda selected=key: self._clear_material(selected),
            ).grid(row=row, column=4, padx=(6, 0), pady=4)
            if self.on_choose_asset is not None:
                ttk.Button(
                    parent,
                    text="素材库",
                    width=6,
                    command=lambda selected=key: self._choose_library_material(selected),
                ).grid(row=row, column=5, padx=(6, 0), pady=4)
        parent.columnconfigure(1, weight=1)

    def _choose_library_material(self, key: str) -> None:
        if self.on_choose_asset is None:
            return
        try:
            selected = self.on_choose_asset(key)
        except Exception as error:
            messagebox.showerror("无法选择一级素材", str(error), parent=self.root)
            return
        if selected is not None:
            self._accept_material_file(key, selected)

    def _build_preview(self, parent: ttk.Frame) -> None:
        placement_header = ttk.Frame(parent, style="Alt.TFrame")
        placement_header.pack(fill="x")
        ttk.Label(
            placement_header,
            text="整体人物位置",
            style="Alt.TLabel",
            font=("Microsoft YaHei UI", 14, "bold"),
        ).pack(side="left")
        self.offset_var = tk.StringVar(value="X 0 px · Y 0 px")
        ttk.Label(
            placement_header,
            textvariable=self.offset_var,
            style="Alt.TLabel",
        ).pack(side="left", padx=(14, 0))
        ttk.Label(
            placement_header,
            text="人物缩放",
            style="Alt.TLabel",
        ).pack(side="left", padx=(18, 5))
        self.character_scale_percent_var = tk.IntVar(value=100)
        scale_box = ttk.Spinbox(
            placement_header,
            from_=25,
            to=300,
            increment=5,
            width=5,
            textvariable=self.character_scale_percent_var,
            command=self._character_scale_changed,
        )
        scale_box.pack(side="left")
        scale_box.bind("<Return>", self._character_scale_changed)
        scale_box.bind("<FocusOut>", self._character_scale_changed)
        ttk.Label(placement_header, text="%", style="Alt.TLabel").pack(side="left")
        ttk.Button(
            placement_header,
            text="重置缩放",
            command=self._reset_character_scale,
        ).pack(side="right", padx=(0, 8))
        ttk.Button(
            placement_header,
            text="重置位置",
            command=self._reset_character_offset,
        ).pack(side="right")

        background_header = ttk.Frame(parent, style="Alt.TFrame")
        background_header.pack(fill="x", pady=(7, 0))
        ttk.Label(
            background_header,
            text="背景裁剪",
            style="Alt.TLabel",
            font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(side="left")
        ttk.Label(background_header, text="X", style="Alt.TLabel").pack(
            side="left", padx=(12, 4)
        )
        self.background_offset_x_var = tk.IntVar(value=0)
        background_x = ttk.Spinbox(
            background_header,
            from_=-16384,
            to=16384,
            width=6,
            textvariable=self.background_offset_x_var,
            command=self._background_adjustment_changed,
        )
        background_x.pack(side="left")
        background_x.bind("<Return>", self._background_adjustment_changed)
        background_x.bind("<FocusOut>", self._background_adjustment_changed)
        ttk.Label(background_header, text="Y", style="Alt.TLabel").pack(
            side="left", padx=(8, 4)
        )
        self.background_offset_y_var = tk.IntVar(value=0)
        background_y = ttk.Spinbox(
            background_header,
            from_=-16384,
            to=16384,
            width=6,
            textvariable=self.background_offset_y_var,
            command=self._background_adjustment_changed,
        )
        background_y.pack(side="left")
        background_y.bind("<Return>", self._background_adjustment_changed)
        background_y.bind("<FocusOut>", self._background_adjustment_changed)
        ttk.Label(background_header, text="缩放", style="Alt.TLabel").pack(
            side="left", padx=(12, 4)
        )
        self.background_scale_percent_var = tk.IntVar(value=100)
        background_scale = ttk.Spinbox(
            background_header,
            from_=100,
            to=300,
            increment=5,
            width=5,
            textvariable=self.background_scale_percent_var,
            command=self._background_adjustment_changed,
        )
        background_scale.pack(side="left")
        background_scale.bind("<Return>", self._background_adjustment_changed)
        background_scale.bind("<FocusOut>", self._background_adjustment_changed)
        ttk.Label(background_header, text="%", style="Alt.TLabel").pack(side="left")
        ttk.Button(
            background_header,
            text="重置背景",
            command=self._reset_background_adjustment,
        ).pack(side="right")
        ttk.Label(
            parent,
            text=(
                "背景按原生 1024×1024 头像画布做 cover 裁剪，X/Y 调整取景、缩放继续放大；"
                "裁剪会自动钳制，不会露出画布外空边。下方头像显示背景与人物的实际分层合成预览。"
            ),
            style="Alt.TLabel",
        ).pack(anchor="w", pady=(3, 7))
        self.character_canvas = tk.Canvas(
            parent,
            height=235,
            bg="#0d1219",
            highlightthickness=1,
            highlightbackground=COLORS["line"],
            cursor="fleur",
        )
        self.character_canvas.pack(fill="x")
        self.character_canvas.bind("<Configure>", lambda _event: self._render_character_canvas())
        self.character_canvas.bind("<ButtonPress-1>", self._character_drag_start)
        self.character_canvas.bind("<B1-Motion>", self._character_drag_motion)
        self.character_canvas.bind("<ButtonRelease-1>", self._character_drag_end)
        self.character_canvas.bind(
            "<Control-v>", lambda _event: self._paste_input("character")
        )
        self.character_canvas.bind(
            "<Control-V>", lambda _event: self._paste_input("character")
        )
        if DND_AVAILABLE:
            self.character_canvas.drop_target_register(DND_FILES)
            self.character_canvas.dnd_bind(
                "<<Drop>>",
                lambda event: self._drop_input("character", event.data),
            )

        ttk.Label(
            parent,
            text="生成结果",
            style="Alt.TLabel",
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(anchor="w", pady=(10, 0))
        self.manual_override_var = tk.StringVar(value="")
        ttk.Label(
            parent,
            textvariable=self.manual_override_var,
            style="Alt.TLabel",
            foreground=COLORS["warning"],
        ).pack(anchor="w", pady=(2, 0))
        grid = ttk.Frame(parent, style="Alt.TFrame")
        grid.pack(fill="both", expand=True, pady=(6, 8))
        for index, (title, slot) in enumerate(PREVIEW_CARDS):
            card = ttk.Frame(grid, padding=8)
            card.grid(row=index // 3, column=index % 3, sticky="nsew", padx=5, pady=5)
            header = ttk.Frame(card)
            header.pack(fill="x", pady=(0, 5))
            ttk.Label(header, text=title).pack(side="left")
            offset_var = tk.StringVar(value="X +0 · Y +0")
            if slot == SMALL_ICON_SOURCE_PREVIEW:
                ttk.Label(
                    header,
                    text="留空则不生成小图标",
                    style="Muted.TLabel",
                ).pack(side="left", padx=(8, 0))
            else:
                ttk.Label(header, textvariable=offset_var, style="Muted.TLabel").pack(
                    side="left", padx=(8, 0)
                )
                ttk.Button(
                    header,
                    text="重置",
                    width=5,
                    command=lambda current=slot: self._reset_output_offset(current),
                ).pack(side="right")
                if slot == "hero_icon_small":
                    ttk.Checkbutton(
                        header,
                        text="棕色底预览",
                        variable=self.small_icon_brown_preview_var,
                        command=lambda: self._schedule_live_previews(
                            ("hero_icon_small",)
                        ),
                    ).pack(side="right", padx=(0, 6))
            canvas = tk.Canvas(
                card,
                height=125,
                bg="#0d1219",
                highlightthickness=1,
                highlightbackground=COLORS["line"],
                cursor="arrow" if slot == SMALL_ICON_SOURCE_PREVIEW else "fleur",
            )
            canvas.pack(fill="both", expand=True)
            if slot == SMALL_ICON_SOURCE_PREVIEW:
                canvas.bind("<Configure>", lambda _event: self._render_small_icon_source_preview())
                canvas.bind(
                    "<Control-v>",
                    lambda _event: self._paste_input("small_icon_source"),
                )
                canvas.bind(
                    "<Control-V>",
                    lambda _event: self._paste_input("small_icon_source"),
                )
                if DND_AVAILABLE:
                    canvas.drop_target_register(DND_FILES)
                    canvas.dnd_bind(
                        "<<Drop>>",
                        lambda event: self._drop_input("small_icon_source", event.data),
                    )
            else:
                canvas.bind(
                    "<Configure>",
                    lambda _event, current=slot: self._schedule_live_previews((current,)),
                )
                canvas.bind(
                    "<ButtonPress-1>",
                    lambda event, current=slot: self._output_drag_start(current, event),
                )
                canvas.bind(
                    "<B1-Motion>",
                    lambda event, current=slot: self._output_drag_motion(current, event),
                )
                canvas.bind(
                    "<ButtonRelease-1>",
                    lambda event, current=slot: self._output_drag_end(current, event),
                )
            self.preview_canvases[slot] = canvas
            if slot != SMALL_ICON_SOURCE_PREVIEW:
                self.preview_offset_vars[slot] = offset_var
        for column in range(3):
            grid.columnconfigure(column, weight=1, uniform="preview")
        for row in range(2):
            grid.rowconfigure(row, weight=1, uniform="preview")

        ttk.Label(parent, text="流水线日志", style="Alt.TLabel").pack(anchor="w")
        log_frame = ttk.Frame(parent)
        log_frame.pack(fill="x", pady=(6, 0))
        self.log = tk.Text(
            log_frame,
            height=4,
            bg="#0d1219",
            fg=COLORS["text"],
            insertbackground=COLORS["text"],
            relief="flat",
            wrap="word",
            font=("Cascadia Mono", 9),
        )
        self.log.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        scrollbar.pack(side="right", fill="y")
        self.log.configure(yscrollcommand=scrollbar.set, state="disabled")

    def _choose_profile(self) -> None:
        selected = filedialog.askopenfilename(
            initialdir=DEFAULT_PROFILE.parent,
            filetypes=(("Generator profile", "*.json"),),
        )
        if selected:
            self._load_profile(Path(selected))

    def _new_project(self) -> None:
        self.editing_workspace_id = None
        USER_PROJECT_ROOT.mkdir(parents=True, exist_ok=True)
        inputs = USER_PROJECT_ROOT / "inputs"
        inputs.mkdir(parents=True, exist_ok=True)
        metadata = inputs / "input-metadata.json"
        if not metadata.is_file():
            metadata.write_text(
                json.dumps(
                    {
                        "character": {
                            "origin": "user_supplied",
                            "aigc": False,
                            "authoritative_alpha": True,
                            "alpha_method": "user-supplied transparent source",
                        },
                        "background": {
                            "origin": "user_supplied",
                            "aigc": False,
                        },
                        "small_icon": {
                            "origin": "user_supplied",
                            "aigc": False,
                        },
                        "small_icon_source": {
                            "origin": "user_supplied",
                            "aigc": False,
                        },
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        default_adapter = self.authoring_adapter_records[0]
        pack_id = f"local.{default_adapter.hero.casefold()}.{uuid.uuid4().hex[:10]}"
        profile = GeneratorProfile(
            profile_path=USER_PROFILE,
            adapter_id=default_adapter.adapter_id,
            pack_id=pack_id,
            name=f"自定义 {default_adapter.hero} 皮肤",
            version="0.1.0",
            character=inputs / "character-not-provided",
            background=inputs / "background-not-provided",
            small_icon=inputs / "small-icon-not-provided",
            input_metadata=metadata,
            badge_template_root=self._ensure_local_badge_assets(),
            workspace_root=(
                WORKSPACES_ROOT
                if self.embedded
                else USER_PROJECT_ROOT / "workspaces"
            ),
            output_zip=default_output_directory() / "Dooley-Custom-0.1.0.zip",
            game_dir=None,
            small_icon_source=None,
            small_icon_mode="none",
        )
        profile.save()
        self._populate_profile(profile)
        self.status_var.set("新项目：先放入人物源图；背景和小图标可稍后补充")
        self._append_log("project", f"已创建本地项目：{USER_PROJECT_ROOT}")

    def _ensure_local_badge_assets(self) -> Path:
        source = PROJECT_ROOT / "manager" / "assets"
        destination = USER_PROJECT_ROOT / "resources" / "manager-assets"
        game_dir = None
        if self.profile is not None and self.profile.game_dir is not None:
            game_dir = self.profile.game_dir
        return ensure_local_badge_assets(
            destination,
            source_root=source,
            game_dir=game_dir,
        )

    def edit_workspace(self, workspace: StudioWorkspace) -> GeneratorProfile:
        """Load one library pack as the active creation project."""

        pack = workspace.state.get("pack") or {}
        pack_id = str(pack.get("id") or workspace.directory.name).strip()
        version = str(pack.get("version") or "0.1.0").strip()
        current_profile = None
        if self.profile is not None:
            try:
                current_profile = self._profile_from_form(validate=False)
            except Exception:
                current_profile = self.profile
        profile = profile_for_workspace_edit(
            workspace,
            profile_path=USER_PROFILE,
            badge_template_root=self._ensure_local_badge_assets(),
            workspace_root=(
                WORKSPACES_ROOT
                if self.embedded
                else USER_PROJECT_ROOT / "generated-workspaces"
            ),
            output_zip=USER_PROJECT_ROOT / "exports" / f"{pack_id}-{version}.zip",
            game_dir=self.profile.game_dir if self.profile is not None else None,
            input_search_roots=(
                USER_PROJECT_ROOT / "generated-workspaces",
                USER_PROJECT_ROOT / "workspaces",
                USER_PROJECT_ROOT / "inputs",
                manager_root() / "library-assets",
            ),
        )
        if (
            current_profile is not None
            and current_profile.pack_id.casefold() == profile.pack_id.casefold()
        ):
            # Re-entering the same pack after a failed build must not discard
            # adjustments the UI already autosaved. Only the source paths are
            # rehydrated; identity and current layout work remain intact.
            profile = replace(
                profile,
                character_offset_x=current_profile.character_offset_x,
                character_offset_y=current_profile.character_offset_y,
                character_scale=current_profile.character_scale,
                background_offset_x=current_profile.background_offset_x,
                background_offset_y=current_profile.background_offset_y,
                background_scale=current_profile.background_scale,
                output_offsets=current_profile.output_offsets,
            )
        self.editing_workspace_id = profile.pack_id
        profile.save()
        self._populate_profile(profile)
        name = profile.name
        if profile.character.is_file():
            detail = "原始素材和调整参数已恢复；加入皮肤库时会更新原皮肤包"
        else:
            detail = "该旧皮肤包未保存人物原图，请补充人物素材后再加入皮肤库"
        self.status_var.set(f"正在编辑：{name} · {detail}")
        self._append_log(
            "edit",
            f"已载入皮肤库项目：{name}（{profile.pack_id}）；{detail}",
        )
        return profile

    @staticmethod
    def draft_fingerprint(profile: GeneratorProfile) -> str:
        """Identify every automatic-mode value that can change a draft."""

        def input_record(path: Path | None) -> dict | None:
            if path is None:
                return None
            resolved = path.resolve()
            record: dict[str, object] = {"path": str(resolved)}
            if resolved.is_file():
                stat = resolved.stat()
                record.update({"bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns})
            return record

        payload = {
            "adapter_id": profile.adapter_id,
            "pack_id": profile.pack_id,
            "name": profile.name,
            "version": profile.version,
            "inputs": {
                "character": input_record(profile.character),
                "background": input_record(profile.background),
                "small_icon": input_record(profile.small_icon),
                "small_icon_source": input_record(profile.small_icon_source),
                "metadata": input_record(profile.input_metadata),
            },
            "character": {
                "x": profile.character_offset_x,
                "y": profile.character_offset_y,
                "scale": profile.character_scale,
            },
            "background": {
                "x": profile.background_offset_x,
                "y": profile.background_offset_y,
                "scale": profile.background_scale,
            },
            "output_offsets": profile.output_offsets,
            "small_icon_mode": profile.small_icon_mode,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def current_draft_fingerprint(self) -> str:
        return self.draft_fingerprint(self._profile_from_form(validate=False))

    def has_draft_source(self) -> bool:
        try:
            return self._profile_from_form(validate=False).character.is_file()
        except Exception:
            return False

    def generate_shared_draft(self) -> bool:
        """Materialize the live form for a mode switch without publishing it."""

        if not self.embedded:
            raise RuntimeError("共享草稿切换只能在皮肤管理器内使用。")
        self.pending_embedded_action = "mode-switch"
        started = self._start(("generate",), clean_override=False)
        if not started:
            self.pending_embedded_action = None
        else:
            self.status_var.set("正在把当前默认草稿同步到逐槽位模式…")
        return started

    def _populate_profile(self, profile: GeneratorProfile) -> None:
        if (
            self.embedded
            and profile.workspace_root.resolve() != WORKSPACES_ROOT.resolve()
        ):
            profile = replace(profile, workspace_root=WORKSPACES_ROOT.resolve())
            profile.save()
        if not profile.badge_template_root.is_dir():
            profile = replace(
                profile,
                badge_template_root=self._ensure_local_badge_assets(),
            )
            profile.save()
        self.profile = profile
        values = {
            "adapter": profile.adapter_id,
            "pack_id": profile.pack_id,
            "name": profile.name,
            "version": profile.version,
            "character": profile.character if profile.character.is_file() else "",
            "background": profile.background if profile.background.is_file() else "",
            "small_icon": profile.small_icon if profile.small_icon.is_file() else "",
            "small_icon_source": (
                profile.small_icon_source
                if profile.small_icon_source is not None
                and profile.small_icon_source.is_file()
                else ""
            ),
            "metadata": profile.input_metadata,
            "badge_root": profile.badge_template_root,
            "workspace_root": profile.workspace_root,
            "output_zip": profile.output_zip,
            "game_dir": profile.game_dir or "",
        }
        for key, value in values.items():
            self.vars[key].set(str(value))
        self._update_character_alpha_metadata(migrate_stale_only=True)
        selected_label = next(
            (
                label
                for label, adapter_id in self.adapter_labels.items()
                if adapter_id.casefold() == profile.adapter_id.casefold()
            ),
            "",
        )
        self.adapter_display_var.set(selected_label)
        self.small_icon_mode_var.set(
            ICON_MODE_NAMES.get(profile.small_icon_mode, "未提供时不生成")
        )
        self.user_small_icon_path = (
            str(profile.small_icon)
            if profile.small_icon_mode == "user" and profile.small_icon.is_file()
            else ""
        )
        self.path_entries["small_icon"].configure(
            state="normal" if profile.small_icon_mode == "user" else "disabled"
        )
        self.character_offset_x = profile.character_offset_x
        self.character_offset_y = profile.character_offset_y
        self.character_scale = profile.character_scale
        self.background_offset_x = profile.background_offset_x
        self.background_offset_y = profile.background_offset_y
        self.background_scale = profile.background_scale
        if hasattr(self, "character_scale_percent_var"):
            self.character_scale_percent_var.set(round(profile.character_scale * 100))
        if hasattr(self, "background_offset_x_var"):
            self.background_offset_x_var.set(profile.background_offset_x)
            self.background_offset_y_var.set(profile.background_offset_y)
            self.background_scale_percent_var.set(round(profile.background_scale * 100))
        self.output_offsets = {
            slot: profile.output_offsets.get(slot, (0, 0))
            for _title, slot in PREVIEW_SLOTS
        }
        self._update_offset_label()
        self._update_output_offset_labels()
        self._render_character_canvas()
        self._render_small_icon_source_preview()
        self._rebuild_live_renderer()

    def _authoring_target_changed(self, _event: tk.Event | None = None) -> None:
        adapter_id = self.adapter_labels.get(self.adapter_display_var.get())
        if not adapter_id:
            return
        previous_id = self.vars["adapter"].get().strip()
        previous = next(
            (
                record
                for record in self.authoring_adapter_records
                if record.adapter_id.casefold() == previous_id.casefold()
            ),
            None,
        )
        selected = next(
            record
            for record in self.authoring_adapter_records
            if record.adapter_id == adapter_id
        )
        self.vars["adapter"].set(adapter_id)
        if previous is not None:
            if self.editing_workspace_id is None:
                self.vars["pack_id"].set(
                    retarget_automatic_pack_id(
                        self.vars["pack_id"].get(),
                        previous.hero,
                        selected.hero,
                    )
                )
            if self.vars["name"].get().strip() == f"自定义 {previous.hero} 皮肤":
                self.vars["name"].set(f"自定义 {selected.hero} 皮肤")
            output_text = self.vars["output_zip"].get().strip()
            if output_text:
                output = Path(output_text)
                old_name = f"{previous.hero}-Custom-{self.vars['version'].get().strip()}.zip"
                if output.name == old_name:
                    self.vars["output_zip"].set(
                        str(output.with_name(
                            f"{selected.hero}-Custom-{self.vars['version'].get().strip()}.zip"
                        ))
                    )
        self._autosave_profile()
        self._rebuild_live_renderer()
        self.status_var.set(f"生成目标已切换：{self.adapter_display_var.get()}")

    def _save_profile(self) -> None:
        try:
            profile = self._profile_from_form(validate=False)
            saved = profile.save()
        except Exception as error:
            messagebox.showerror("无法保存配置", str(error), parent=self.root)
            return
        self.profile = profile
        self.status_var.set(f"配置已保存：{saved.name}")
        self._append_log("profile", f"已保存画布位置与输入路径：{saved}")

    def _autosave_profile(self) -> None:
        if self.profile is None:
            return
        try:
            profile = self._profile_from_form(validate=False)
            profile.save()
            self.profile = profile
        except Exception:
            # Incomplete text being edited in advanced settings is not fatal;
            # the explicit Save action will surface a useful error if needed.
            return

    def _select_chroma_preset(self, color: str) -> None:
        self.chroma_color.set(color)
        self.chroma_enabled.set(True)
        name = "绿幕" if color == "#00FF00" else "白幕"
        self.status_var.set(f"已选择{name}；下一次导入或粘贴图片时应用")

    def _choose_chroma_color(self) -> None:
        chosen = colorchooser.askcolor(
            color=self.chroma_color.get(),
            parent=self.root,
        )[1]
        if chosen:
            self.chroma_color.set(chosen.upper())
            self.chroma_enabled.set(True)

    def _accept_material_file(self, key: str, source: Path) -> None:
        source = source.resolve()
        selected = source
        if self.chroma_enabled.get():
            if self.profile is None:
                self._new_project()
            assert self.profile is not None
            destination = (
                self.profile.profile_path.parent
                / "inputs"
                / f"{key}-screen-removed.png"
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            with Image.open(source) as loaded:
                processed = remove_color_screen(
                    loaded.convert("RGBA"),
                    self.chroma_color.get(),
                    self.chroma_tolerance.get(),
                )
            if key in {"character", "small_icon"} and processed.getchannel("A").getbbox() is None:
                messagebox.showerror(
                    "扣色结果为空",
                    "扣除底色后图片已完全透明；已取消本次导入。请关闭扣色或降低容差后重试。",
                    parent=self.root,
                )
                return
            processed.save(destination, "PNG", optimize=True)
            selected = destination.resolve()
            self._append_log(
                "input",
                f"已按 {self.chroma_color.get()} / 容差 {self.chroma_tolerance.get()} 扣除底色：{source.name}",
            )
        if key == "small_icon" and self._selected_small_icon_mode() != "user":
            self.small_icon_mode_var.set(ICON_MODE_NAMES["user"])
            self._small_icon_mode_changed()
        self.vars[key].set(str(selected))
        self._input_changed(key)
        if self.on_material_import is not None:
            self.on_material_import(key, selected)

    def _clear_material(self, key: str) -> None:
        if key == "small_icon":
            self.small_icon_mode_var.set(ICON_MODE_NAMES["none"])
            self._small_icon_mode_changed()
        self.vars[key].set("")
        if key == "small_icon":
            self.user_small_icon_path = ""
            self._update_small_icon_metadata("none")
        self._input_changed(key)
        self.status_var.set(f"已清空：{key}")
        self._append_log("input", f"已清空当前素材：{key}")

    def _drop_input(self, key: str, data: str) -> str:
        candidates = [Path(value) for value in self.root.tk.splitlist(data)]
        selected = next(
            (
                path
                for path in candidates
                if path.is_file()
                and path.suffix.casefold() in {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
            ),
            None,
        )
        if selected is None:
            self.status_var.set("拖入内容不是可用图片文件")
            return "break"
        self._accept_material_file(key, selected)
        self._append_log("input", f"拖入 {key}: {selected}")
        return "break"

    def _paste_input(self, key: str) -> str | None:
        clipboard = None
        try:
            clipboard = ImageGrab.grabclipboard()
        except (OSError, NotImplementedError):
            pass
        if isinstance(clipboard, list):
            selected = next((Path(value) for value in clipboard if Path(value).is_file()), None)
            if selected is not None:
                self._accept_material_file(key, selected)
                self._append_log("input", f"粘贴文件 {key}: {selected}")
                return "break"
        if isinstance(clipboard, Image.Image):
            if self.profile is None:
                self._new_project()
            assert self.profile is not None
            destination = self.profile.profile_path.parent / "inputs" / f"{key}-pasted.png"
            destination.parent.mkdir(parents=True, exist_ok=True)
            image = clipboard.convert("RGBA")
            if self.chroma_enabled.get():
                image = remove_color_screen(
                    image,
                    self.chroma_color.get(),
                    self.chroma_tolerance.get(),
                )
            if key in {"character", "small_icon"} and image.getchannel("A").getbbox() is None:
                messagebox.showerror(
                    "扣色结果为空",
                    "扣除底色后图片已完全透明；已取消本次粘贴。请关闭扣色或降低容差后重试。",
                    parent=self.root,
                )
                return "break"
            image.save(destination, optimize=True)
            if key == "small_icon" and self._selected_small_icon_mode() != "user":
                self.small_icon_mode_var.set(ICON_MODE_NAMES["user"])
                self._small_icon_mode_changed()
            self.vars[key].set(str(destination.resolve()))
            self._input_changed(key)
            if self.on_material_import is not None:
                self.on_material_import(key, destination.resolve())
            self._append_log("input", f"剪贴板图片已保存为 {destination}")
            return "break"
        try:
            text = self.root.clipboard_get().strip().strip('"')
        except tk.TclError:
            return None
        selected = Path(text)
        if selected.is_file():
            self._accept_material_file(key, selected)
            self._append_log("input", f"粘贴路径 {key}: {selected}")
            return "break"
        return None

    def _paste_material_button(self, key: str) -> None:
        if key == "small_icon" and self._selected_small_icon_mode() != "user":
            self.small_icon_mode_var.set(ICON_MODE_NAMES["user"])
            self._small_icon_mode_changed()
        if self._paste_input(key) != "break":
            self.status_var.set("剪贴板中没有可用的图片、图片文件或图片路径")

    def _global_paste(self, _event: tk.Event) -> str | None:
        focused = self.root.focus_get()
        if focused is not None and focused.winfo_class() in {"Entry", "TEntry"}:
            return None
        return self._paste_input("character")

    def _input_changed(self, key: str) -> None:
        if key == "character":
            self._update_character_alpha_metadata()
            self.character_offset_x = 0
            self.character_offset_y = 0
            self.character_scale = 1.0
            self.character_scale_percent_var.set(100)
            self.output_offsets = {
                slot: (0, 0) for _title, slot in PREVIEW_SLOTS
            }
            self._update_offset_label()
            self._render_character_canvas()
            self._render_small_icon_source_preview()
            self._update_output_offset_labels()
            if self._selected_small_icon_mode() not in {"none", "user"}:
                self._materialize_derived_small_icon()
        elif key == "background":
            self.background_offset_x = 0
            self.background_offset_y = 0
            self.background_scale = 1.0
            self.background_offset_x_var.set(0)
            self.background_offset_y_var.set(0)
            self.background_scale_percent_var.set(100)
        elif key == "small_icon" and self._selected_small_icon_mode() == "user":
            self.user_small_icon_path = self.vars["small_icon"].get().strip()
        elif key == "small_icon_source":
            self._render_small_icon_source_preview()
            if self._selected_small_icon_mode() not in {"none", "user"}:
                self._materialize_derived_small_icon()
            else:
                self._update_small_icon_metadata("user")
        self._autosave_profile()
        self._rebuild_live_renderer()

    def _update_character_alpha_metadata(
        self,
        *,
        migrate_stale_only: bool = False,
    ) -> None:
        character_text = self.vars["character"].get().strip()
        metadata_text = self.vars["metadata"].get().strip()
        if not character_text or not metadata_text:
            return
        character = Path(character_text)
        metadata_path = Path(metadata_text)
        if not character.is_file():
            return
        payload: dict = {}
        if metadata_path.is_file():
            try:
                payload = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                payload = {}
        existing = dict(payload.get("character") or {})
        with Image.open(character) as loaded:
            authoritative = has_authored_transparency(loaded)
        if migrate_stale_only and not (
            existing.get("authoritative_alpha") is True
            and existing.get("alpha_method") == "user-supplied transparent source"
            and not authoritative
        ):
            return
        character_metadata = existing
        character_metadata.update(
            {
                "origin": character_metadata.get("origin") or "user_supplied",
                "aigc": False,
                "authoritative_alpha": authoritative,
                "alpha_method": (
                    "user-supplied transparent source"
                    if authoritative
                    else "automatic edge-connected background removal"
                ),
            }
        )
        payload["character"] = character_metadata
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _selected_small_icon_mode(self) -> str:
        return ICON_MODE_LABELS.get(self.small_icon_mode_var.get(), "none")

    def _small_icon_mode_changed(self, _event: tk.Event | None = None) -> None:
        mode = self._selected_small_icon_mode()
        entry = self.path_entries["small_icon"]
        if mode == "user":
            entry.configure(state="normal")
            self.vars["small_icon"].set(self.user_small_icon_path)
            self._update_small_icon_metadata(mode)
        elif mode == "none":
            current = self.vars["small_icon"].get().strip()
            if current and "small-icon-derived-" not in Path(current).name:
                self.user_small_icon_path = current
            entry.configure(state="disabled")
            self.vars["small_icon"].set("")
            self._update_small_icon_metadata(mode)
        else:
            current = self.vars["small_icon"].get().strip()
            if current and "small-icon-derived-" not in Path(current).name:
                self.user_small_icon_path = current
            entry.configure(state="disabled")
            self._materialize_derived_small_icon()
        self._autosave_profile()
        self._rebuild_live_renderer()

    def _materialize_derived_small_icon(self) -> None:
        mode = self._selected_small_icon_mode()
        icon_source_text = self.vars["small_icon_source"].get().strip()
        source_text = icon_source_text
        if mode in {"none", "user"} or not source_text or not Path(source_text).is_file():
            self.vars["small_icon"].set("")
            return
        if self.profile is None:
            self._new_project()
        assert self.profile is not None
        destination = (
            self.profile.profile_path.parent
            / "inputs"
            / f"small-icon-derived-{mode}.png"
        )
        try:
            derive_small_icon_file(
                Path(source_text),
                destination,
                normalized_region=(0.0, 0.0, 1.0, 1.0),
                preset=mode,
            )
        except Exception as error:
            self.status_var.set(f"小图标生成失败：{error}")
            self.vars["small_icon"].set("")
            return
        self.vars["small_icon"].set(str(destination))
        self._update_small_icon_metadata(
            mode,
            derived_from="small_icon_source",
        )
        self.status_var.set(f"已用“{self.small_icon_mode_var.get()}”生成小图标")

    def _update_small_icon_metadata(
        self,
        mode: str,
        *,
        derived_from: str | None = None,
    ) -> None:
        metadata_text = self.vars["metadata"].get().strip()
        if not metadata_text or not Path(metadata_text).is_file():
            return
        path = Path(metadata_text)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        payload["small_icon"] = (
            {
                "origin": "not_provided",
                "aigc": False,
            }
            if mode == "none"
            else {
                "origin": "user_supplied",
                "aigc": False,
            }
            if mode == "user"
            else {
                "origin": "deterministic_derivative",
                "aigc": False,
                "derived_from": derived_from or "character",
                "preset": mode,
                "normalized_region": [0.0, 0.0, 1.0, 1.0],
            }
        )
        icon_source_text = self.vars["small_icon_source"].get().strip()
        if icon_source_text and Path(icon_source_text).is_file():
            payload["small_icon_source"] = {
                "origin": "user_supplied",
                "aigc": False,
            }
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)

    def _update_offset_label(self) -> None:
        if hasattr(self, "offset_var"):
            self.offset_var.set(
                f"X {self.character_offset_x:+d} px · Y {self.character_offset_y:+d} px"
            )

    def _update_output_offset_labels(self) -> None:
        for _title, slot in PREVIEW_SLOTS:
            x, y = self.output_offsets.get(slot, (0, 0))
            if slot in self.preview_offset_vars:
                self.preview_offset_vars[slot].set(f"X {x:+d} · Y {y:+d}")

    def _render_character_canvas(self) -> None:
        if not hasattr(self, "character_canvas"):
            return
        canvas = self.character_canvas
        width = max(1, canvas.winfo_width())
        height = max(1, canvas.winfo_height())
        if width < 80 or height < 80:
            return
        path_text = self.vars.get("character", tk.StringVar()).get().strip()
        path = Path(path_text) if path_text else None
        canvas.delete("all")
        if path is None or not path.is_file():
            canvas.create_text(
                width // 2,
                height // 2,
                text="拖入或粘贴人物源图",
                fill=COLORS["muted"],
            )
            return
        try:
            with Image.open(path) as loaded:
                source = loaded.convert("RGBA")
        except Exception as error:
            canvas.create_text(
                width // 2,
                height // 2,
                text=f"无法预览：{error}",
                fill=COLORS["danger"],
            )
            return
        margin = 14
        scale = min((width - margin * 2) / source.width, (height - margin * 2) / source.height)
        frame_size = (
            max(1, round(source.width * scale)),
            max(1, round(source.height * scale)),
        )
        origin = ((width - frame_size[0]) // 2, (height - frame_size[1]) // 2)
        checker = Image.new("RGBA", frame_size, (218, 224, 231, 255))
        pixels = checker.load()
        tile = 14
        for y in range(frame_size[1]):
            for x in range(frame_size[0]):
                if (x // tile + y // tile) % 2:
                    pixels[x, y] = (177, 187, 199, 255)
        display_size = (
            max(1, round(frame_size[0] * self.character_scale)),
            max(1, round(frame_size[1] * self.character_scale)),
        )
        resized = source.resize(display_size, Image.Resampling.LANCZOS)
        self.character_checker_photo = ImageTk.PhotoImage(checker)
        self.character_canvas_photo = ImageTk.PhotoImage(resized)
        self.character_canvas_scale = scale * self.character_scale
        self.character_canvas_origin = origin
        self.character_source_size = source.size
        canvas.create_image(*origin, image=self.character_checker_photo, anchor="nw")
        image_x = origin[0] + round((frame_size[0] - display_size[0]) * 0.5)
        image_y = origin[1] + frame_size[1] - display_size[1]
        image_x += round(self.character_offset_x * self.character_canvas_scale)
        image_y += round(self.character_offset_y * self.character_canvas_scale)
        canvas.create_image(
            image_x,
            image_y,
            image=self.character_canvas_photo,
            anchor="nw",
            tags=("character",),
        )
        # Mask pixels translated beyond the authoritative source canvas.
        x0, y0 = origin
        x1, y1 = x0 + frame_size[0], y0 + frame_size[1]
        mask = "#0d1219"
        canvas.create_rectangle(0, 0, width, y0, fill=mask, outline=mask)
        canvas.create_rectangle(0, y1, width, height, fill=mask, outline=mask)
        canvas.create_rectangle(0, y0, x0, y1, fill=mask, outline=mask)
        canvas.create_rectangle(x1, y0, width, y1, fill=mask, outline=mask)
        canvas.create_rectangle(x0, y0, x1, y1, outline=COLORS["accent"], width=2)

    def _render_small_icon_source_preview(self) -> None:
        canvas = self.preview_canvases.get(SMALL_ICON_SOURCE_PREVIEW)
        if canvas is None:
            return
        width = max(1, canvas.winfo_width())
        height = max(1, canvas.winfo_height())
        if width < 20 or height < 20:
            return
        source_text = self.vars.get("small_icon_source", tk.StringVar()).get().strip()
        canvas.delete("all")
        if not source_text or not Path(source_text).is_file():
            canvas.create_text(
                width // 2,
                height // 2,
                text="拖入或粘贴图标生成源图",
                fill=COLORS["muted"],
                width=max(80, width - 20),
                justify="center",
            )
            return
        try:
            with Image.open(source_text) as loaded:
                source = loaded.convert("RGBA")
            self._render_output_canvas(SMALL_ICON_SOURCE_PREVIEW, source)
        except Exception as error:
            canvas.create_text(
                width // 2,
                height // 2,
                text=f"无法预览\n{error}",
                fill=COLORS["danger"],
                width=max(80, width - 20),
                justify="center",
            )

    def _character_drag_start(self, event: tk.Event) -> None:
        self.character_canvas.focus_set()
        self.character_drag_origin = (
            event.x,
            event.y,
            self.character_offset_x,
            self.character_offset_y,
        )

    def _character_drag_motion(self, event: tk.Event) -> None:
        if self.character_drag_origin is None or self.character_canvas_scale <= 0:
            return
        start_x, start_y, offset_x, offset_y = self.character_drag_origin
        width, height = getattr(self, "character_source_size", (1, 1))
        delta_x = round((event.x - start_x) / self.character_canvas_scale)
        delta_y = round((event.y - start_y) / self.character_canvas_scale)
        self.character_offset_x = max(-width + 1, min(width - 1, offset_x + delta_x))
        self.character_offset_y = max(-height + 1, min(height - 1, offset_y + delta_y))
        self._update_offset_label()
        self._render_character_canvas()
        self._schedule_live_previews()

    def _character_drag_end(self, _event: tk.Event) -> None:
        self.character_drag_origin = None
        self._autosave_profile()

    def _reset_character_offset(self) -> None:
        self.character_offset_x = 0
        self.character_offset_y = 0
        self._update_offset_label()
        self._render_character_canvas()
        self._schedule_live_previews()
        self._autosave_profile()

    def _character_scale_changed(self, _event: tk.Event | None = None) -> None:
        try:
            percent = int(self.character_scale_percent_var.get())
        except (tk.TclError, ValueError):
            return
        percent = max(25, min(300, percent))
        if self.character_scale_percent_var.get() != percent:
            self.character_scale_percent_var.set(percent)
        self.character_scale = percent / 100.0
        self._render_character_canvas()
        self._schedule_live_previews()
        self._autosave_profile()

    def _reset_character_scale(self) -> None:
        self.character_scale_percent_var.set(100)
        self._character_scale_changed()

    def _background_adjustment_changed(self, _event: tk.Event | None = None) -> None:
        try:
            offset_x = int(self.background_offset_x_var.get())
            offset_y = int(self.background_offset_y_var.get())
            percent = int(self.background_scale_percent_var.get())
        except (tk.TclError, ValueError):
            return
        offset_x = max(-16384, min(16384, offset_x))
        offset_y = max(-16384, min(16384, offset_y))
        percent = max(100, min(300, percent))
        self.background_offset_x_var.set(offset_x)
        self.background_offset_y_var.set(offset_y)
        self.background_scale_percent_var.set(percent)
        self.background_offset_x = offset_x
        self.background_offset_y = offset_y
        self.background_scale = percent / 100.0
        self._schedule_live_previews()
        self._autosave_profile()

    def _reset_background_adjustment(self) -> None:
        self.background_offset_x_var.set(0)
        self.background_offset_y_var.set(0)
        self.background_scale_percent_var.set(100)
        self._background_adjustment_changed()

    def _output_drag_start(self, slot: str, event: tk.Event) -> None:
        x, y = self.output_offsets.get(slot, (0, 0))
        self.preview_drag_origins[slot] = (event.x, event.y, x, y)

    def _output_drag_motion(self, slot: str, event: tk.Event) -> None:
        origin = self.preview_drag_origins.get(slot)
        scale = self.preview_scales.get(slot, 0.0)
        if origin is None or scale <= 0:
            return
        start_x, start_y, offset_x, offset_y = origin
        delta_x = round((event.x - start_x) / scale)
        delta_y = round((event.y - start_y) / scale)
        if self.live_renderer is not None:
            width, height = self.live_renderer.size(slot)
        else:
            width, height = (4096, 4096)
        self.output_offsets[slot] = (
            max(-width + 1, min(width - 1, offset_x + delta_x)),
            max(-height + 1, min(height - 1, offset_y + delta_y)),
        )
        self._update_output_offset_labels()
        self._schedule_live_previews((slot,))

    def _output_drag_end(self, slot: str, _event: tk.Event) -> None:
        self.preview_drag_origins.pop(slot, None)
        self._autosave_profile()

    def _reset_output_offset(self, slot: str) -> None:
        self.output_offsets[slot] = (0, 0)
        self._update_output_offset_labels()
        self._schedule_live_previews((slot,))
        self._autosave_profile()

    def _load_profile(self, path: Path, *, fallback_to_new: bool = False) -> None:
        try:
            profile = GeneratorProfile.load(path, validate=not fallback_to_new)
        except Exception as error:
            if fallback_to_new:
                self._new_project()
                return
            messagebox.showerror("无法载入配置", str(error), parent=self.root)
            return
        self._populate_profile(profile)
        self.status_var.set(f"已载入：{path.name}")
        self._append_log("profile", f"已载入 {path}")

    def _profile_from_form(self, *, validate: bool = True) -> GeneratorProfile:
        if self.profile is None:
            raise ValueError("请先载入生成配置。")

        def image_path(key: str) -> Path:
            value = self.vars[key].get().strip()
            if value:
                return Path(value).resolve()
            # Keep an explicit non-file path when an author clears an optional
            # material. Reusing the previous profile value would silently bring
            # stale pixels back into the preview or exported pack.
            return (
                self.profile.profile_path.parent
                / "inputs"
                / f"{key}-not-provided"
            ).resolve()

        def optional_image_path(key: str) -> Path | None:
            value = self.vars[key].get().strip()
            return Path(value).resolve() if value else None

        profile = replace(
            self.profile,
            adapter_id=self.vars["adapter"].get().strip(),
            pack_id=self.vars["pack_id"].get().strip(),
            name=self.vars["name"].get().strip(),
            version=self.vars["version"].get().strip(),
            character=image_path("character"),
            background=image_path("background"),
            small_icon=image_path("small_icon"),
            small_icon_source=optional_image_path("small_icon_source"),
            input_metadata=Path(self.vars["metadata"].get()).resolve(),
            badge_template_root=Path(self.vars["badge_root"].get()).resolve(),
            workspace_root=(
                WORKSPACES_ROOT.resolve()
                if self.embedded
                else Path(self.vars["workspace_root"].get()).resolve()
            ),
            output_zip=Path(self.vars["output_zip"].get()).resolve(),
            game_dir=(
                Path(self.vars["game_dir"].get()).resolve()
                if self.vars["game_dir"].get().strip()
                else None
            ),
            character_offset_x=self.character_offset_x,
            character_offset_y=self.character_offset_y,
            character_scale=self.character_scale,
            background_offset_x=self.background_offset_x,
            background_offset_y=self.background_offset_y,
            background_scale=self.background_scale,
            output_offsets={
                slot: offset
                for slot, offset in self.output_offsets.items()
                if offset != (0, 0)
            },
            small_icon_mode=self._selected_small_icon_mode(),
        )
        if validate:
            profile.validate()
        return profile

    def _start(
        self,
        stages: tuple[str, ...],
        *,
        clean_override: bool | None = None,
    ) -> bool:
        if self.busy:
            return False
        try:
            profile = self._profile_from_form()
        except Exception as error:
            messagebox.showerror("配置无效", str(error), parent=self.root)
            return False
        if "deploy" in stages and not messagebox.askyesno(
            "确认部署",
            "这会通过 Skin Manager 先恢复当前托管内容，再部署新资产包。继续吗？",
            parent=self.root,
        ):
            return False
        self.busy = True
        self.profile = profile
        clean = self.clean_var.get() if clean_override is None else clean_override
        if self.embedded:
            # This directory is the shared cache for both authoring modes.
            # Rebuilding state is safe; deleting it would also delete the
            # per-slot source files required when the user switches back.
            clean = False
        for button in self.action_buttons:
            button.configure(state="disabled")
        self.progress.start(10)
        self.status_var.set("流水线运行中…")

        def worker() -> None:
            try:
                result = run_pipeline(
                    profile,
                    stages,
                    clean=clean,
                    progress=lambda stage, message: self.events.put(
                        ("progress", (stage, message))
                    ),
                )
                self.events.put(("complete", result))
            except Exception:
                self.events.put(("error", traceback.format_exc()))

        threading.Thread(target=worker, daemon=True).start()
        return True

    def _embedded_import(self) -> None:
        if self.busy:
            return
        if self.on_effective_action is not None and self.on_effective_action("import"):
            return
        try:
            profile = self._profile_from_form(validate=False)
            output = USER_PROJECT_ROOT / "exports" / (
                f"{profile.pack_id}-{profile.version}.zip"
            )
            self.vars["output_zip"].set(str(output))
            self.vars["workspace_root"].set(
                str(WORKSPACES_ROOT)
            )
            self.pending_embedded_action = "import"
            self._start(("generate",), clean_override=False)
        except Exception as error:
            messagebox.showerror("无法生成皮肤", str(error), parent=self.root)

    def _embedded_export(self) -> None:
        if self.busy:
            return
        if self.on_effective_action is not None and self.on_effective_action("export"):
            return
        try:
            profile = self._profile_from_form(validate=False)
        except Exception as error:
            messagebox.showerror("配置无效", str(error), parent=self.root)
            return
        selected = filedialog.asksaveasfilename(
            parent=self.root,
            title="导出皮肤包到指定位置",
            initialfile=f"{profile.name}-{profile.version}.zip",
            defaultextension=".zip",
            filetypes=(("皮肤包 ZIP", "*.zip"),),
        )
        if not selected:
            return
        self.vars["output_zip"].set(selected)
        self.pending_embedded_action = "export"
        self._start(("generate",), clean_override=False)

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "progress":
                    stage, message = payload
                    self.status_var.set(message)
                    self._append_log(stage, message)
                elif kind == "complete":
                    self._finish(payload)
                elif kind == "error":
                    self._fail(str(payload))
        except queue.Empty:
            pass
        self.root.after(100, self._poll_events)

    def _finish(self, result: PipelineResult) -> None:
        self._set_idle()
        self.status_var.set("流水线完成")
        self._append_log("result", json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        workspace = Path(result.generated_workspace) if result.generated_workspace else None
        if workspace is None and result.manager_workspace:
            workspace = Path(result.manager_workspace)
        if workspace is not None:
            self._refresh_previews(workspace)
        action = self.pending_embedded_action
        self.pending_embedded_action = None
        completed_profile = self.profile or self._profile_from_form(validate=False)
        if result.generated_workspace:
            self.editing_workspace_id = completed_profile.pack_id
        if self.embedded and self.on_generated is not None and result.generated_workspace:
            try:
                self.on_generated(completed_profile, result)
            except Exception as error:
                messagebox.showerror("无法接续生成草稿", str(error), parent=self.root)
                return
        if self.embedded and action == "import" and self.on_import is not None:
            try:
                self.on_import(completed_profile, result)
            except Exception as error:
                messagebox.showerror("无法加入皮肤库", str(error), parent=self.root)
                return
        if self.embedded and action == "mode-switch":
            self.status_var.set("当前草稿已同步到逐槽位模式")
            return
        message = "操作完成。"
        if self.embedded and action == "import":
            message = "皮肤已经生成并加入皮肤库。"
        elif self.embedded and action == "export":
            message = f"皮肤包已导出：\n{result.output_zip}"
        if result.doctor_healthy is True:
            message += " Skin Manager doctor 正常。"
        messagebox.showinfo("完成", message, parent=self.root)

    def _fail(self, details: str) -> None:
        self._set_idle()
        self.pending_embedded_action = None
        self.status_var.set("流水线失败")
        self._append_log("error", details)
        last = details.strip().splitlines()[-1] if details.strip() else "未知错误"
        if self.on_generation_failed is not None:
            self.on_generation_failed(details)
        messagebox.showerror("流水线失败", last, parent=self.root)

    def _set_idle(self) -> None:
        self.busy = False
        self.progress.stop()
        for button in self.action_buttons:
            button.configure(state="normal")

    def _append_log(self, stage: str, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", f"[{stage}] {message}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def set_manual_preview_overrides(
        self,
        pack_id: str,
        images: dict[str, Image.Image],
        *,
        total_count: int | None = None,
    ) -> None:
        """Expose sparse manual edits in the default-mode preview.

        The automatic form remains the canonical base draft.  Manual mode
        contributes only explicitly edited slots, which avoids attempting an
        impossible reverse conversion from arbitrary bitmaps into global
        character/background parameters.
        """

        self.manual_override_pack_id = pack_id.strip()
        self.manual_preview_overrides = {
            slot: image.copy() for slot, image in images.items()
        }
        count = len(self.manual_preview_overrides) if total_count is None else total_count
        self.manual_override_var.set(
            (
                f"当前草稿包含 {count} 个逐槽位覆盖；预览和导出优先使用这些覆盖。"
                if count
                else ""
            )
        )
        self._schedule_live_previews()

    def clear_manual_preview_overrides(self) -> None:
        self.manual_override_pack_id = ""
        self.manual_preview_overrides = {}
        self.manual_override_var.set("")
        self._schedule_live_previews()

    def _rebuild_live_renderer(self) -> None:
        self._render_small_icon_source_preview()
        character_text = self.vars["character"].get().strip()
        if not character_text or not Path(character_text).is_file():
            self.live_renderer = None
            for _title, slot in PREVIEW_SLOTS:
                canvas = self.preview_canvases.get(slot)
                if canvas is None:
                    continue
                canvas.delete("all")
            return
        try:
            profile = self._profile_from_form(validate=False)
            self.live_renderer = LivePreviewRenderer(profile)
        except Exception as error:
            self.live_renderer = None
            for _title, slot in PREVIEW_SLOTS:
                canvas = self.preview_canvases.get(slot)
                if canvas is None:
                    continue
                canvas.delete("all")
                canvas.create_text(
                    max(1, canvas.winfo_width()) // 2,
                    max(1, canvas.winfo_height()) // 2,
                    text=f"无法预览\n{error}",
                    fill=COLORS["danger"],
                    width=max(80, canvas.winfo_width() - 20),
                    justify="center",
                )
            return
        self._schedule_live_previews()

    def _schedule_live_previews(self, slots: tuple[str, ...] | None = None) -> None:
        requested = slots or tuple(slot for _title, slot in PREVIEW_SLOTS)
        self.pending_preview_slots.update(requested)
        if self.preview_refresh_job is not None:
            self.root.after_cancel(self.preview_refresh_job)
        self.preview_refresh_job = self.root.after(45, self._refresh_live_previews)

    def _refresh_live_previews(self) -> None:
        self.preview_refresh_job = None
        slots = tuple(self.pending_preview_slots)
        self.pending_preview_slots.clear()
        current_pack_id = self.vars["pack_id"].get().strip()
        use_manual = (
            bool(current_pack_id)
            and current_pack_id.casefold() == self.manual_override_pack_id.casefold()
        )
        if self.live_renderer is None and not use_manual:
            return
        for slot in slots:
            canvas = self.preview_canvases.get(slot)
            if canvas is None or canvas.winfo_width() < 20 or canvas.winfo_height() < 20:
                continue
            try:
                manual_preview = (
                    self.manual_preview_overrides.get(slot) if use_manual else None
                )
                if manual_preview is not None:
                    self._render_output_canvas(slot, manual_preview)
                    continue
                if self.live_renderer is None:
                    continue
                render_arguments = {
                    "character_canvas_offset": (
                        self.character_offset_x,
                        self.character_offset_y,
                    ),
                    "character_scale": self.character_scale,
                    "background_offset": (
                        self.background_offset_x,
                        self.background_offset_y,
                    ),
                    "background_scale": self.background_scale,
                    "local_offset": self.output_offsets.get(slot, (0, 0)),
                }
                preview = (
                    self.live_renderer.render_portrait_composite(**render_arguments)
                    if slot == "portrait_gameplay"
                    else self.live_renderer.render(slot, **render_arguments)
                )
                self._render_output_canvas(slot, preview)
            except Exception as error:
                canvas.delete("all")
                canvas.create_text(
                    canvas.winfo_width() // 2,
                    canvas.winfo_height() // 2,
                    text=f"无法预览\n{error}",
                    fill=COLORS["danger"],
                    width=max(80, canvas.winfo_width() - 20),
                    justify="center",
                )

    def _render_output_canvas(self, slot: str, source: Image.Image) -> None:
        canvas = self.preview_canvases[slot]
        width = max(1, canvas.winfo_width())
        height = max(1, canvas.winfo_height())
        margin = 6
        scale = min(
            (width - margin * 2) / source.width,
            (height - margin * 2) / source.height,
        )
        display_size = (
            max(1, round(source.width * scale)),
            max(1, round(source.height * scale)),
        )
        preview = source.convert("RGBA").resize(display_size, Image.Resampling.LANCZOS)
        if slot == "hero_icon_small" and self.small_icon_brown_preview_var.get():
            checker = Image.new("RGBA", display_size, (84, 51, 34, 255))
        else:
            checker = Image.new("RGBA", display_size, (218, 224, 231, 255))
            tile = 10
            for y in range(0, display_size[1], tile):
                for x in range(0, display_size[0], tile):
                    if (x // tile + y // tile) % 2:
                        checker.paste(
                            (177, 187, 199, 255),
                            (
                                x,
                                y,
                                min(display_size[0], x + tile),
                                min(display_size[1], y + tile),
                            ),
                        )
        checker.alpha_composite(preview)
        photo = ImageTk.PhotoImage(checker)
        self.preview_photos[slot] = photo
        self.preview_scales[slot] = scale
        canvas.delete("all")
        canvas.create_image(width // 2, height // 2, image=photo, anchor="center")
        canvas.create_rectangle(
            (width - display_size[0]) // 2,
            (height - display_size[1]) // 2,
            (width + display_size[0]) // 2,
            (height + display_size[1]) // 2,
            outline=COLORS["accent"],
        )

    def _refresh_previews(self, _workspace: Path) -> None:
        """Refresh after a pipeline run without coupling the UI to pack files."""
        self._rebuild_live_renderer()

    def self_test_layout(self) -> None:
        """Exercise the minimum supported window instead of only importing Tk."""
        self.root.geometry("1120x720+0+0")
        self.root.update_idletasks()
        self.root.update()
        window_bottom = self.root.winfo_rooty() + self.root.winfo_height()
        for button in self.action_buttons:
            button_bottom = button.winfo_rooty() + button.winfo_height()
            if not button.winfo_ismapped() or button_bottom > window_bottom:
                raise RuntimeError(
                    f"pipeline action is outside the visible window: {button.cget('text')}"
                )
        if len(self.form_scroll_canvases) != 2:
            raise RuntimeError("both authoring tabs must provide vertical scrolling")
        for canvas in self.form_scroll_canvases:
            if canvas.cget("yscrollcommand") in (None, ""):
                raise RuntimeError("authoring tab is missing its vertical scrollbar")

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    app = AssetGeneratorUI()
    if "--self-test" in sys.argv:
        try:
            app.self_test_layout()
            app.root.withdraw()
            app.root.update_idletasks()
            return 0
        except Exception:
            return 2
        finally:
            app.root.destroy()
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
