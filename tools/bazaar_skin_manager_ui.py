#!/usr/bin/env python3
"""Desktop UI for authoring and deploying The Bazaar skin packs."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

from PIL import Image, ImageGrab, ImageTk

from bazaar_skin_manager import MANAGER_VERSION

from mod_studio_core import (
    PREVIEW_SIZE,
    PROJECT_ROOT,
    SUPPORTED_AUDIO_EXTENSIONS,
    SUPPORTED_IMAGE_EXTENSIONS,
    WORKSPACES_ROOT,
    StudioWorkspace,
    catalog,
    compose_image_preview,
    discovered_catalog,
    restore_before_application_uninstall,
)

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    RootClass = TkinterDnD.Tk
    DND_AVAILABLE = True
except ImportError:
    DND_FILES = "DND_Files"
    RootClass = tk.Tk
    DND_AVAILABLE = False


COLORS = {
    "window": "#11151d",
    "panel": "#171d27",
    "panel_alt": "#1d2531",
    "line": "#2b3545",
    "text": "#eef2f7",
    "muted": "#94a1b2",
    "accent": "#65d4b3",
    "accent_dark": "#214c45",
    "warning": "#f4bd67",
    "danger": "#ef7b7b",
    "empty": "#242d3a",
}

ROUTE_CATEGORY_NAMES = {
    "hero_voice": "英雄语音",
    "merchant_voice": "商人语音",
    "menu_voice": "菜单语音",
}


class ModManagerStudio:
    def __init__(self) -> None:
        self.root = RootClass()
        self.root.title(f"The Bazaar 皮肤管理器 v{MANAGER_VERSION}")
        self.root.geometry("1440x900")
        self.root.minsize(1180, 720)
        self.root.configure(bg=COLORS["window"])
        self.catalog = catalog()
        self.first_run = False
        self.game_dir_override: Path | None = None
        self.workspace = self._open_last_or_default()
        self.catalog = discovered_catalog(self.game_dir_override)
        self.preview_images: dict[str, ImageTk.PhotoImage] = {}
        self.slot_widgets: dict[str, dict[str, tk.Widget]] = {}
        self.busy = False
        self._configure_style()
        self._build_ui()
        self._load_workspace_into_ui()
        self._refresh_all()
        if self.first_run:
            self.root.after(250, self._show_first_run_help)

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
        style.configure("TFrame", background=COLORS["panel"])
        style.configure("Window.TFrame", background=COLORS["window"])
        style.configure("Alt.TFrame", background=COLORS["panel_alt"])
        style.configure(
            "TLabel",
            background=COLORS["panel"],
            foreground=COLORS["text"],
        )
        style.configure(
            "Muted.TLabel",
            background=COLORS["panel"],
            foreground=COLORS["muted"],
        )
        style.configure(
            "Alt.TLabel",
            background=COLORS["panel_alt"],
            foreground=COLORS["text"],
        )
        style.configure(
            "Title.TLabel",
            background=COLORS["window"],
            foreground=COLORS["text"],
            font=("Microsoft YaHei UI", 19, "bold"),
        )
        style.configure(
            "Accent.TButton",
            background=COLORS["accent"],
            foreground="#0d1c19",
            borderwidth=0,
            padding=(18, 10),
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        style.map(
            "Accent.TButton",
            background=[("active", "#79e3c3"), ("disabled", "#45635d")],
        )
        style.configure(
            "Danger.TButton",
            background="#5b292f",
            foreground="#ffdce0",
            padding=(14, 9),
        )
        style.configure("TButton", padding=(11, 7))
        style.configure("TEntry", padding=7)
        style.configure(
            "TCombobox",
            padding=6,
            fieldbackground=COLORS["panel_alt"],
            background=COLORS["panel_alt"],
            foreground=COLORS["text"],
            arrowcolor=COLORS["muted"],
        )
        style.map(
            "TCombobox",
            fieldbackground=[
                ("readonly", COLORS["panel_alt"]),
                ("disabled", COLORS["empty"]),
            ],
            foreground=[
                ("readonly", COLORS["text"]),
                ("disabled", COLORS["muted"]),
            ],
            selectbackground=[("readonly", COLORS["panel_alt"])],
            selectforeground=[("readonly", COLORS["text"])],
        )
        style.configure(
            "TNotebook",
            background=COLORS["window"],
            borderwidth=0,
        )
        style.configure(
            "TNotebook.Tab",
            background=COLORS["panel"],
            foreground=COLORS["muted"],
            padding=(18, 10),
            borderwidth=0,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", COLORS["panel_alt"])],
            foreground=[("selected", COLORS["text"])],
        )
        style.configure(
            "Treeview",
            background=COLORS["panel_alt"],
            fieldbackground=COLORS["panel_alt"],
            foreground=COLORS["text"],
            rowheight=30,
            borderwidth=0,
        )
        style.configure(
            "Treeview.Heading",
            background=COLORS["panel"],
            foreground=COLORS["muted"],
            relief="flat",
        )
        style.map(
            "Treeview",
            background=[("selected", COLORS["accent_dark"])],
        )

    def _settings_path(self) -> Path:
        return WORKSPACES_ROOT.parent / "studio-settings.json"

    def _open_last_or_default(self) -> StudioWorkspace:
        settings = self._settings_path()
        self.first_run = not settings.is_file()
        if settings.is_file():
            try:
                payload = json.loads(settings.read_text(encoding="utf-8"))
                path = Path(payload["workspace"])
                if payload.get("game_dir"):
                    self.game_dir_override = Path(payload["game_dir"])
                if path.is_dir():
                    return StudioWorkspace.load(path)
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                pass
        return StudioWorkspace.create("local.custom.skin")

    def _remember_workspace(self) -> None:
        path = self._settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "workspace": str(self.workspace.directory),
                    "game_dir": (
                        str(self.game_dir_override)
                        if self.game_dir_override
                        else None
                    ),
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, style="Window.TFrame", padding=22)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer, style="Window.TFrame")
        header.pack(fill="x", pady=(0, 14))
        ttk.Label(
            header,
            text=f"The Bazaar 皮肤管理器 · v{MANAGER_VERSION}",
            style="Title.TLabel",
        ).pack(side="left")
        self.install_status = ttk.Label(
            header,
            text="正在检查部署状态…",
            style="Muted.TLabel",
        )
        self.install_status.pack(side="right", padx=(12, 0))
        ttk.Button(
            header,
            text="刷新状态",
            command=self._refresh_deployment_status,
        ).pack(side="right")
        ttk.Button(
            header,
            text="恢复原版",
            style="Danger.TButton",
            command=self._undeploy,
        ).pack(side="right", padx=(8, 4))
        ttk.Button(
            header,
            text="清空已加载皮肤",
            command=self._clear_loaded_skin,
        ).pack(side="right", padx=(4, 0))
        self.play_button = ttk.Button(
            header,
            text="启动游戏",
            style="Accent.TButton",
            command=self._launch_game,
        )
        self.play_button.pack(side="right", padx=(8, 4))
        self.header_deploy_button = ttk.Button(
            header,
            text="部署",
            style="Accent.TButton",
            command=self._deploy,
        )
        self.header_deploy_button.pack(side="right")

        body = ttk.Panedwindow(outer, orient="horizontal")
        body.pack(fill="both", expand=True)
        sidebar = ttk.Frame(body, padding=16)
        workspace_panel = ttk.Frame(body, style="Alt.TFrame", padding=0)
        body.add(sidebar, weight=0)
        body.add(workspace_panel, weight=1)
        self._build_sidebar(sidebar)
        self._build_workspace(workspace_panel)

    def _build_sidebar(self, parent: ttk.Frame) -> None:
        parent.configure(width=310)
        actions = ttk.Frame(parent)
        actions.pack(fill="x", side="bottom", pady=(18, 0))
        self.deploy_button = ttk.Button(
            actions,
            text="部署",
            style="Accent.TButton",
            command=self._deploy,
        )
        self.deploy_button.pack(fill="x", pady=(0, 8))
        ttk.Button(
            actions,
            text="取消部署 / 恢复原版",
            style="Danger.TButton",
            command=self._undeploy,
        ).pack(fill="x")

        ttk.Label(
            parent,
            text="目标",
            font=("Microsoft YaHei UI", 13, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            parent,
            text="选择英雄和对应皮肤。",
            style="Muted.TLabel",
            wraplength=275,
        ).pack(anchor="w", pady=(3, 12))

        self.hero_var = tk.StringVar()
        self.hero_combo = ttk.Combobox(
            parent,
            textvariable=self.hero_var,
            state="readonly",
            values=[hero["display_name"] for hero in self.catalog["heroes"]],
        )
        self.hero_combo.pack(fill="x", pady=(0, 8))
        self.hero_combo.bind("<<ComboboxSelected>>", self._hero_changed)

        self.skin_var = tk.StringVar()
        self.skin_combo = ttk.Combobox(
            parent,
            textvariable=self.skin_var,
            state="readonly",
        )
        self.skin_combo.pack(fill="x")
        self.skin_combo.bind("<<ComboboxSelected>>", self._skin_changed)
        self.hero_support = ttk.Label(
            parent,
            text="",
            style="Muted.TLabel",
            wraplength=275,
        )
        self.hero_support.pack(anchor="w", pady=(8, 18))

        self.game_status = ttk.Label(
            parent,
            text="正在查找 The Bazaar…",
            style="Muted.TLabel",
            wraplength=275,
        )
        self.game_status.pack(anchor="w", pady=(0, 8))
        ttk.Button(
            parent,
            text="重新扫描游戏位置",
            command=self._refresh_deployment_status,
        ).pack(anchor="w")
        ttk.Button(
            parent,
            text="手动选择游戏目录…",
            command=self._locate_game,
        ).pack(anchor="w", pady=(4, 14))

        ttk.Separator(parent).pack(fill="x", pady=(0, 16))
        ttk.Label(
            parent,
            text="资产包",
            font=("Microsoft YaHei UI", 13, "bold"),
        ).pack(anchor="w")

        self.pack_id_var = tk.StringVar()
        self.pack_name_var = tk.StringVar()
        self.pack_version_var = tk.StringVar()
        for label, variable in (
            ("资产包 ID", self.pack_id_var),
            ("显示名称", self.pack_name_var),
            ("版本", self.pack_version_var),
        ):
            ttk.Label(parent, text=label, style="Muted.TLabel").pack(
                anchor="w", pady=(10, 3)
            )
            entry = ttk.Entry(parent, textvariable=variable)
            entry.pack(fill="x")
            entry.bind("<FocusOut>", lambda _event: self._metadata_changed())

        ttk.Separator(parent).pack(fill="x", pady=16)
        self.drop_zone = tk.Label(
            parent,
            text=(
                "将完整资产包或 ZIP 拖到这里\n"
                "也可点击选择文件"
            ),
            bg=COLORS["empty"],
            fg=COLORS["muted"],
            activebackground=COLORS["panel_alt"],
            activeforeground=COLORS["text"],
            height=6,
            cursor="hand2",
            relief="flat",
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.drop_zone.pack(fill="x")
        self.drop_zone.bind("<Button-1>", lambda _event: self._browse_package())
        self._register_drop(self.drop_zone, self._drop_package)
        if not DND_AVAILABLE:
            ttk.Label(
                parent,
                text="当前源码运行模式仅支持点击选择；发布版支持原生拖放。",
                style="Muted.TLabel",
                wraplength=275,
            ).pack(anchor="w", pady=(6, 0))

    def _build_workspace(self, parent: ttk.Frame) -> None:
        notebook = ttk.Notebook(parent)
        notebook.pack(fill="both", expand=True)
        self.visual_tab = ttk.Frame(notebook, style="Alt.TFrame", padding=14)
        self.audio_tab = ttk.Frame(notebook, style="Alt.TFrame", padding=14)
        self.animation_tab = ttk.Frame(notebook, style="Alt.TFrame", padding=14)
        self.package_tab = ttk.Frame(notebook, style="Alt.TFrame", padding=14)
        notebook.add(self.visual_tab, text="图像槽位")
        notebook.add(self.audio_tab, text="音频")
        notebook.add(self.animation_tab, text="骨骼 / 动画")
        notebook.add(self.package_tab, text="资产包与日志")
        self._build_visual_tab()
        self._build_audio_tab()
        self._build_animation_tab()
        self._build_package_tab()

    def _build_visual_tab(self) -> None:
        controls = ttk.Frame(self.visual_tab, style="Alt.TFrame")
        controls.pack(fill="x", pady=(0, 10))
        ttk.Label(
            controls,
            text="未填充的槽位会继续使用游戏原版资产。",
            style="Alt.TLabel",
            font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(side="left")
        self.chroma_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            controls,
            text="导入时自动去除指定底色",
            variable=self.chroma_enabled,
        ).pack(side="right")
        self.chroma_color = tk.StringVar(value="#00FF00")
        ttk.Button(
            controls,
            textvariable=self.chroma_color,
            command=self._choose_chroma_color,
        ).pack(side="right", padx=6)
        self.chroma_tolerance = tk.IntVar(value=28)
        ttk.Spinbox(
            controls,
            from_=0,
            to=128,
            width=5,
            textvariable=self.chroma_tolerance,
        ).pack(side="right")
        ttk.Label(
            controls,
            text="容差",
            style="Alt.TLabel",
        ).pack(side="right", padx=(8, 3))

        canvas = tk.Canvas(
            self.visual_tab,
            bg=COLORS["panel_alt"],
            highlightthickness=0,
        )
        scrollbar = ttk.Scrollbar(
            self.visual_tab,
            orient="vertical",
            command=canvas.yview,
        )
        self.visual_grid = ttk.Frame(canvas, style="Alt.TFrame")
        self.visual_grid.bind(
            "<Configure>",
            lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        grid_window = canvas.create_window(
            (0, 0),
            window=self.visual_grid,
            anchor="nw",
        )
        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfigure(grid_window, width=event.width),
        )
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        canvas.bind_all(
            "<MouseWheel>",
            lambda event: canvas.yview_scroll(int(-event.delta / 120), "units"),
        )

        for index, slot in enumerate(self.catalog["visual_slots"]):
            self._build_visual_card(
                self.visual_grid,
                slot,
                index,
                0,
            )
        self.visual_grid.columnconfigure(0, weight=1)

    def _build_visual_card(
        self,
        parent: ttk.Frame,
        slot: dict,
        row: int,
        column: int,
    ) -> None:
        card = tk.Frame(
            parent,
            bg=COLORS["panel"],
            highlightbackground=COLORS["line"],
            highlightthickness=1,
            padx=12,
            pady=12,
        )
        card.grid(row=row, column=column, sticky="nsew", padx=6, pady=6)
        preview_frame = tk.Frame(
            card,
            bg=COLORS["empty"],
            width=PREVIEW_SIZE[0],
            height=PREVIEW_SIZE[1],
        )
        preview_frame.pack(side="left", padx=(0, 12))
        preview_frame.pack_propagate(False)
        preview = tk.Label(
            preview_frame,
            text="原版",
            bg=COLORS["empty"],
            fg=COLORS["muted"],
        )
        preview.pack(fill="both", expand=True)
        text = tk.Frame(card, bg=COLORS["panel"])
        text.pack(side="left", fill="both", expand=True)
        tk.Label(
            text,
            text=slot["name"],
            bg=COLORS["panel"],
            fg=COLORS["text"],
            font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(anchor="w")
        tk.Label(
            text,
            text=slot["id"],
            bg=COLORS["panel"],
            fg=COLORS["accent"],
            font=("Cascadia Mono", 8),
        ).pack(anchor="w")
        tk.Label(
            text,
            text=slot["description"],
            bg=COLORS["panel"],
            fg=COLORS["muted"],
            wraplength=290,
            justify="left",
        ).pack(anchor="w", pady=(5, 8))
        status = tk.Label(
            text,
            text="使用原版",
            bg=COLORS["panel"],
            fg=COLORS["muted"],
        )
        status.pack(anchor="w")
        buttons = tk.Frame(text, bg=COLORS["panel"])
        buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(
            buttons,
            text="导入",
            command=lambda item=slot["id"]: self._browse_visual(item),
        ).pack(side="left")
        ttk.Button(
            buttons,
            text="粘贴",
            command=lambda item=slot["id"]: self._paste_visual(item),
        ).pack(side="left", padx=5)
        ttk.Button(
            buttons,
            text="清除",
            command=lambda item=slot["id"]: self._clear_visual(item),
        ).pack(side="left")
        ttk.Button(
            buttons,
            text="对比原版",
            command=lambda item=slot["id"]: self._compare_original(item),
        ).pack(side="left", padx=(5, 0))
        self._register_drop(
            card,
            lambda paths, item=slot["id"]: self._drop_visual(item, paths),
        )
        self._register_drop(
            preview,
            lambda paths, item=slot["id"]: self._drop_visual(item, paths),
        )
        self._register_drop(
            preview_frame,
            lambda paths, item=slot["id"]: self._drop_visual(item, paths),
        )
        self.slot_widgets[slot["id"]] = {
            "preview": preview,
            "status": status,
        }

    def _build_audio_tab(self) -> None:
        top = ttk.Frame(self.audio_tab, style="Alt.TFrame")
        top.pack(fill="x", pady=(0, 10))
        ttk.Label(
            top,
            text="可逐条导入，也可拖入按规范命名的音频包或完整资产包 ZIP。",
            style="Alt.TLabel",
            font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(side="left")
        ttk.Button(
            top,
            text="导入音频 ZIP",
            command=self._browse_audio_package,
        ).pack(side="right")
        self.audio_summary = ttk.Label(
            top,
            text="",
            style="Alt.TLabel",
        )
        self.audio_summary.pack(side="right", padx=(0, 12))

        columns = ("category", "slot", "variants", "status")
        self.audio_tree = ttk.Treeview(
            self.audio_tab,
            columns=columns,
            show="headings",
            selectmode="browse",
        )
        self.audio_tree.heading("category", text="类别")
        self.audio_tree.heading("slot", text="逻辑槽位")
        self.audio_tree.heading("variants", text="文件数")
        self.audio_tree.heading("status", text="状态")
        self.audio_tree.column("category", width=130, stretch=False)
        self.audio_tree.column("slot", width=360)
        self.audio_tree.column("variants", width=70, anchor="center")
        self.audio_tree.column("status", width=130)
        self.audio_tree.tag_configure("filled", foreground=COLORS["accent"])
        self.audio_tree.tag_configure("original", foreground=COLORS["muted"])
        self.audio_tree.pack(fill="both", expand=True)
        self._register_drop(self.audio_tree, self._drop_audio_files)

        controls = ttk.Frame(self.audio_tab, style="Alt.TFrame")
        controls.pack(fill="x", pady=(10, 0))
        ttk.Button(
            controls,
            text="向选中槽位添加文件",
            command=self._browse_audio_line,
        ).pack(side="left")
        ttk.Button(
            controls,
            text="清除选中槽位",
            command=self._clear_audio_line,
        ).pack(side="left", padx=6)
        ttk.Label(
            controls,
            text="检测到 ffmpeg 时会自动将非 WAV 音频转码。",
            style="Alt.TLabel",
        ).pack(side="right")

    def _build_animation_tab(self) -> None:
        ttk.Label(
            self.animation_tab,
            text="骨骼 / 动画制作源文件",
            style="Alt.TLabel",
            font=("Microsoft YaHei UI", 13, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            self.animation_tab,
            text=(
                "资产包可以携带 Spine 源文件或 Unity AssetBundle。当前运行时仍使用"
                "静态图作为回退；动态播放需等待预制体适配器验证完成，以免把尚未"
                "可用的动画资源误标为已支持。"
            ),
            style="Alt.TLabel",
            wraplength=850,
            justify="left",
        ).pack(anchor="w", pady=(6, 18))
        self.animation_mode = tk.StringVar(value="spine_source")
        ttk.Radiobutton(
            self.animation_tab,
            text="Spine 源文件组（.skel/.json + .atlas + 贴图）",
            variable=self.animation_mode,
            value="spine_source",
        ).pack(anchor="w")
        ttk.Radiobutton(
            self.animation_tab,
            text="Unity AssetBundle 预制体（.bundle/.assetbundle）",
            variable=self.animation_mode,
            value="unity_asset_bundle",
        ).pack(anchor="w", pady=(5, 12))
        self.animation_drop = tk.Label(
            self.animation_tab,
            text="将动画文件拖到这里\n也可点击选择文件",
            bg=COLORS["empty"],
            fg=COLORS["muted"],
            height=8,
            cursor="hand2",
        )
        self.animation_drop.pack(fill="x")
        self.animation_drop.bind(
            "<Button-1>",
            lambda _event: self._browse_animation(),
        )
        self._register_drop(self.animation_drop, self._drop_animation)
        self.animation_status = ttk.Label(
            self.animation_tab,
            text="当前资产包没有动画源文件。",
            style="Alt.TLabel",
        )
        self.animation_status.pack(anchor="w", pady=(12, 0))
        ttk.Button(
            self.animation_tab,
            text="清除动画源文件",
            command=self._clear_animation,
        ).pack(anchor="w", pady=(8, 0))

    def _build_package_tab(self) -> None:
        actions = ttk.Frame(self.package_tab, style="Alt.TFrame")
        actions.pack(fill="x")
        ttk.Button(
            actions,
            text="新建工作区",
            command=self._new_workspace,
        ).pack(side="left")
        ttk.Button(
            actions,
            text="打开工作区",
            command=self._open_workspace,
        ).pack(side="left", padx=5)
        ttk.Button(
            actions,
            text="导出完整 ZIP",
            command=self._export_zip,
        ).pack(side="left")
        ttk.Button(
            actions,
            text="打开工作区文件夹",
            command=self._open_workspace_folder,
        ).pack(side="right")

        self.package_summary = ttk.Label(
            self.package_tab,
            text="",
            style="Alt.TLabel",
            wraplength=900,
            justify="left",
        )
        self.package_summary.pack(anchor="w", pady=(16, 10))
        self.log = tk.Text(
            self.package_tab,
            bg="#0c1016",
            fg="#cbd5e1",
            insertbackground=COLORS["text"],
            relief="flat",
            font=("Cascadia Mono", 9),
            wrap="word",
        )
        self.log.pack(fill="both", expand=True)

    def _register_drop(self, widget: tk.Widget, callback) -> None:
        if not DND_AVAILABLE:
            return
        widget.drop_target_register(DND_FILES)
        widget.dnd_bind(
            "<<Drop>>",
            lambda event: callback(
                [Path(item) for item in self.root.tk.splitlist(event.data)]
            ),
        )

    def _load_workspace_into_ui(self) -> None:
        state = self.workspace.state
        self.pack_id_var.set(state["pack"]["id"])
        self.pack_name_var.set(state["pack"]["name"])
        self.pack_version_var.set(state["pack"]["version"])
        target = state["target"]
        hero = next(
            item
            for item in self.catalog["heroes"]
            if item["id"] == target["hero"]
        )
        self.hero_var.set(hero["display_name"])
        self._set_skin_options(hero, target["skin"])
        self._remember_workspace()

    def _selected_hero(self) -> dict:
        return next(
            (
                hero
                for hero in self.catalog["heroes"]
                if hero["display_name"] == self.hero_var.get()
            ),
            self.catalog["heroes"][0],
        )

    def _selected_skin(self) -> dict:
        hero = self._selected_hero()
        return next(
            (
                skin
                for skin in hero["skins"]
                if self._skin_label(skin) == self.skin_var.get()
            ),
            hero["skins"][0],
        )

    @staticmethod
    def _skin_label(skin: dict) -> str:
        status = {
            "supported": "可部署",
            "detected_unmapped": "已检测・未适配",
            "game_update_required": "需要更新适配器",
        }.get(skin.get("deployment_status"), "离线目录")
        return f"{skin['display_name']}  ·  {skin['id']}  ·  {status}"

    def _set_skin_options(self, hero: dict, selected_id: str | None = None) -> None:
        labels = [self._skin_label(skin) for skin in hero["skins"]]
        self.skin_combo.configure(values=labels)
        selected = next(
            (skin for skin in hero["skins"] if skin["id"] == selected_id),
            hero["skins"][0],
        )
        self.skin_var.set(self._skin_label(selected))
        if selected.get("deployment_status") == "supported":
            self.hero_support.configure(
                text=f"已验证适配器：{selected.get('adapter_id')}",
                foreground=COLORS["accent"],
            )
            self.deploy_button.configure(state="normal")
            self.header_deploy_button.configure(state="normal")
        else:
            status = selected.get("deployment_status")
            message = (
                "检测到此皮肤，但尚无经过验证的适配器；导出和部署保持阻断。"
                if status == "detected_unmapped"
                else "当前游戏版本与适配器不匹配；需要更新适配器。"
            )
            self.hero_support.configure(
                text=message,
                foreground=COLORS["warning"],
            )
            self.deploy_button.configure(state="disabled")
            self.header_deploy_button.configure(state="disabled")

    def _hero_changed(self, _event=None) -> None:
        self._set_skin_options(self._selected_hero())
        self._metadata_changed()
        self._refresh_audio()

    def _skin_changed(self, _event=None) -> None:
        hero = self._selected_hero()
        skin = self._selected_skin()
        self._set_skin_options(hero, skin["id"])
        self._metadata_changed()
        self._refresh_audio()

    def _reload_discovered_catalog(self) -> None:
        target = self.workspace.state["target"]
        self.catalog = discovered_catalog(self.game_dir_override)
        self.hero_combo.configure(
            values=[hero["display_name"] for hero in self.catalog["heroes"]]
        )
        hero = next(
            (
                item
                for item in self.catalog["heroes"]
                if item["id"] == target["hero"]
            ),
            self.catalog["heroes"][0],
        )
        self.hero_var.set(hero["display_name"])
        self._set_skin_options(hero, target.get("skin"))

    def _metadata_changed(self) -> None:
        try:
            hero = self._selected_hero()
            skin = self._selected_skin()
            self.workspace.set_metadata(
                pack_id=self.pack_id_var.get(),
                name=self.pack_name_var.get(),
                version=self.pack_version_var.get(),
                hero=hero["id"],
                skin=skin["id"],
                skin_name_contains=skin["name_contains"],
            )
            self._refresh_summary()
        except ValueError as error:
            self._write_log(f"元数据未保存：{error}")

    def _refresh_all(self) -> None:
        self._refresh_visuals()
        self._refresh_audio()
        self._refresh_animation()
        self._refresh_summary()
        self._refresh_deployment_status()

    def _refresh_visuals(self) -> None:
        for slot in self.catalog["visual_slots"]:
            slot_id = slot["id"]
            widgets = self.slot_widgets[slot_id]
            path = self.workspace.visual_path(slot_id)
            preview: tk.Label = widgets["preview"]  # type: ignore[assignment]
            status: tk.Label = widgets["status"]  # type: ignore[assignment]
            if not path:
                preview.configure(image="", text="原版")
                status.configure(
                    text="使用原版",
                    fg=COLORS["muted"],
                )
                self.preview_images.pop(slot_id, None)
                continue
            try:
                with Image.open(path) as source:
                    source_size = source.size
                    rendered = compose_image_preview(source)
                photo = ImageTk.PhotoImage(rendered)
                self.preview_images[slot_id] = photo
                preview.configure(image=photo, text="")
                status.configure(
                    text=f"{path.name} · {source_size[0]}×{source_size[1]}",
                    fg=COLORS["accent"],
                )
            except OSError as error:
                preview.configure(image="", text="无效")
                status.configure(text=str(error), fg=COLORS["danger"])

    def _refresh_audio(self) -> None:
        selected = self.audio_tree.selection()
        self.audio_tree.delete(*self.audio_tree.get_children())
        active = self.workspace.audio_manifest() or {"routes": []}
        counts = {
            route["logical_slot"]: len(route.get("variants") or [])
            for route in active.get("routes") or []
        }
        routes = self.workspace.audio_route_catalog()
        filled = 0
        for route in routes:
            slot = route["logical_slot"]
            count = counts.get(slot, 0)
            if count:
                filled += 1
            self.audio_tree.insert(
                "",
                "end",
                iid=slot,
                values=(
                    ROUTE_CATEGORY_NAMES.get(
                        route["category"],
                        route["category"],
                    ),
                    slot,
                    count,
                    "✓ 已就绪" if count else "— 使用原版",
                ),
                tags=("filled" if count else "original",),
            )
        if filled:
            self.audio_summary.configure(
                text=f"✓ 已填充 {filled}/{len(routes)} 条音频路由",
                foreground=COLORS["accent"],
            )
        else:
            self.audio_summary.configure(
                text=f"0/{len(routes)} · 使用原版音频",
                foreground=COLORS["muted"],
            )
        if selected and self.audio_tree.exists(selected[0]):
            self.audio_tree.selection_set(selected[0])

    def _refresh_animation(self) -> None:
        animation = self.workspace.state.get("animation") or {}
        files = animation.get("files") or []
        if files:
            self.animation_mode.set(animation.get("mode", "spine_source"))
            self.animation_status.configure(
                text=(
                    f"已包含 {len(files)} 个源文件 · "
                    "当前运行时仍使用静态图回退"
                ),
                foreground=COLORS["warning"],
            )
        else:
            self.animation_status.configure(
                text="当前资产包没有动画源文件。",
                foreground=COLORS["muted"],
            )

    def _refresh_summary(self) -> None:
        visuals = len(self.workspace.state.get("visual_slots") or {})
        audio = self.workspace.audio_manifest() or {}
        routes = sum(
            1 for route in audio.get("routes") or [] if route.get("variants")
        )
        animation = len(
            (self.workspace.state.get("animation") or {}).get("files") or []
        )
        self.package_summary.configure(
            text=(
                f"工作区：{self.workspace.directory}\n"
                f"已填充：{visuals}/{len(self.catalog['visual_slots'])} 个图像槽位"
                f" · {routes} 条音频路由 · {animation} 个动画文件。"
                "其余内容均使用游戏原版。"
            )
        )

    def _refresh_deployment_status(self) -> None:
        try:
            status = self.workspace.diagnostics()
            if status.get("healthy"):
                text = "已部署 · 状态正常"
                color = COLORS["accent"]
            elif status.get("installed"):
                text = f"已部署 · {status.get('state', '需要处理')}"
                color = COLORS["warning"]
            else:
                text = "原版 / 未部署"
                color = COLORS["muted"]
            self.install_status.configure(text=text, foreground=color)
        except Exception as error:
            self.install_status.configure(
                text=f"状态检查失败：{error}",
                foreground=COLORS["danger"],
            )
        game = self.workspace.detected_game(self.game_dir_override)
        if game:
            build = f" · Steam 构建 {game.build_id}" if game.build_id else ""
            self.game_status.configure(
                text=f"已找到游戏：{game.game_dir}{build}",
                foreground=COLORS["accent"],
            )
            if not self.busy:
                self.play_button.configure(state="normal")
        else:
            self.game_status.configure(
                text="未在常见 Steam 目录中找到 The Bazaar。",
                foreground=COLORS["warning"],
            )
            self.play_button.configure(state="disabled")

    def _locate_game(self) -> None:
        selected = filedialog.askdirectory(
            parent=self.root,
            title="选择包含 TheBazaar.exe 的游戏目录",
            initialdir=self.game_dir_override or Path.home(),
        )
        if not selected:
            return
        game = self.workspace.detected_game(Path(selected))
        if not game:
            messagebox.showerror(
                "游戏目录无效",
                "请选择同时包含 TheBazaar.exe 和 TheBazaar_Data 的目录。",
                parent=self.root,
            )
            return
        self.game_dir_override = game.game_dir
        self._reload_discovered_catalog()
        self._remember_workspace()
        self._write_log(f"使用手动选择的游戏目录：{game.game_dir}")
        self._refresh_deployment_status()

    def _show_first_run_help(self) -> None:
        game = self.workspace.detected_game(self.game_dir_override)
        game_line = (
            f"已找到 The Bazaar：\n{game.game_dir}\n\n"
            if game
            else (
                "未找到 The Bazaar。请先通过 Steam 安装，然后重新扫描或手动"
                "选择游戏目录。\n\n"
            )
        )
        messagebox.showinfo(
            "欢迎",
            (
                game_line
                + "1. 准备兼容的资产包 ZIP。\n"
                "2. 将 ZIP 拖入左侧资产包区域。\n"
                "3. 关闭游戏后点击“部署”。\n"
                "4. 点击“启动游戏”通过 Steam 启动。"
            ),
            parent=self.root,
        )

    def _choose_chroma_color(self) -> None:
        chosen = colorchooser.askcolor(
            color=self.chroma_color.get(),
            parent=self.root,
        )[1]
        if chosen:
            self.chroma_color.set(chosen.upper())

    def _visual_import_options(self) -> dict:
        return {
            "chroma_color": (
                self.chroma_color.get() if self.chroma_enabled.get() else None
            ),
            "tolerance": self.chroma_tolerance.get(),
        }

    def _browse_visual(self, slot: str) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title=f"导入 {slot}",
            filetypes=[
                ("图像", "*.png *.jpg *.jpeg *.webp *.bmp"),
                ("所有文件", "*.*"),
            ],
        )
        if path:
            self._import_visual(slot, Path(path))

    def _compare_original(self, slot: str) -> None:
        if self.busy:
            return
        self._run_background(
            f"正在读取原版贴图：{slot}…",
            lambda: self.workspace.export_original_visual(
                slot,
                self.game_dir_override,
            ),
            lambda original: self._show_visual_comparison(slot, original),
        )

    def _show_visual_comparison(self, slot: str, original: Path) -> None:
        self.busy = False
        self._set_skin_options(self._selected_hero(), self._selected_skin()["id"])
        replacement = self.workspace.visual_path(slot)
        with Image.open(original) as loaded:
            original_size = loaded.size
            original_preview = compose_image_preview(loaded, (360, 300), 16)
        if replacement:
            with Image.open(replacement) as loaded:
                replacement_size = loaded.size
                replacement_preview = compose_image_preview(loaded, (360, 300), 16)
        else:
            replacement_size = None
            replacement_preview = Image.new("RGBA", (360, 300), COLORS["empty"])

        window = tk.Toplevel(self.root)
        window.title(f"原版 / 替换预览 · {slot}")
        window.configure(bg=COLORS["panel"])
        window.transient(self.root)
        container = ttk.Frame(window, padding=16)
        container.pack(fill="both", expand=True)
        photos = [
            ImageTk.PhotoImage(original_preview),
            ImageTk.PhotoImage(replacement_preview),
        ]
        for column, (title, photo, size) in enumerate(
            (
                ("原版", photos[0], original_size),
                ("当前替换", photos[1], replacement_size),
            )
        ):
            panel = ttk.Frame(container, padding=8)
            panel.grid(row=0, column=column, sticky="nsew")
            ttk.Label(panel, text=title, font=("Microsoft YaHei UI", 12, "bold")).pack()
            ttk.Label(panel, image=photo).pack(pady=8)
            text = f"{size[0]} × {size[1]}" if size else "未填充；继续使用原版"
            ttk.Label(panel, text=text, style="Muted.TLabel").pack()
            container.columnconfigure(column, weight=1)
        window._preview_images = photos  # type: ignore[attr-defined]
        self._refresh_deployment_status()

    def _drop_visual(self, slot: str, paths: list[Path]) -> None:
        if paths:
            self._import_visual(slot, paths[0])

    def _import_visual(self, slot: str, path: Path) -> None:
        try:
            destination = self.workspace.import_visual(
                slot,
                path,
                **self._visual_import_options(),
            )
            self._write_log(f"已导入图像 {slot}：{destination}")
            self._refresh_visuals()
            self._refresh_summary()
        except Exception as error:
            self._show_error("图像导入失败", error)

    def _paste_visual(self, slot: str) -> None:
        try:
            value = ImageGrab.grabclipboard()
            if isinstance(value, Image.Image):
                destination = self.workspace.import_pil_image(
                    slot,
                    value,
                    **self._visual_import_options(),
                )
            elif isinstance(value, list) and value:
                destination = self.workspace.import_visual(
                    slot,
                    Path(value[0]),
                    **self._visual_import_options(),
                )
            else:
                raise ValueError("剪贴板中没有图像或图像文件。")
            self._write_log(f"已粘贴图像 {slot}：{destination}")
            self._refresh_visuals()
            self._refresh_summary()
        except Exception as error:
            self._show_error("剪贴板导入失败", error)

    def _clear_visual(self, slot: str) -> None:
        self.workspace.clear_visual(slot)
        self._write_log(f"已清除图像 {slot}；该槽位恢复使用原版。")
        self._refresh_visuals()
        self._refresh_summary()

    def _selected_audio_slot(self) -> str | None:
        selection = self.audio_tree.selection()
        return selection[0] if selection else None

    def _browse_audio_line(self) -> None:
        slot = self._selected_audio_slot()
        if not slot:
            messagebox.showinfo(
                "请选择槽位",
                "请先选择一个音频逻辑槽位。",
                parent=self.root,
            )
            return
        path = filedialog.askopenfilename(
            parent=self.root,
            title=f"向 {slot} 添加音频",
            filetypes=[
                (
                    "音频",
                    "*.wav *.mp3 *.flac *.ogg *.m4a *.aac *.opus",
                ),
                ("所有文件", "*.*"),
            ],
        )
        if path:
            self._import_audio_line(slot, Path(path))

    def _import_audio_line(self, slot: str, path: Path) -> None:
        try:
            destination = self.workspace.import_audio(slot, path)
            self._write_log(f"已导入音频 {slot}：{destination}")
            self._refresh_audio()
            self._refresh_summary()
        except Exception as error:
            self._show_error("音频导入失败", error)

    def _drop_audio_files(self, paths: list[Path]) -> None:
        if len(paths) == 1 and paths[0].suffix.casefold() == ".zip":
            self._import_package(paths[0])
            return
        slot = self._selected_audio_slot()
        if not slot:
            self._show_error(
                "音频导入失败",
                ValueError("拖入音频文件前请先选择一个逻辑槽位。"),
            )
            return
        for path in paths:
            self._import_audio_line(slot, path)

    def _clear_audio_line(self) -> None:
        slot = self._selected_audio_slot()
        if not slot:
            return
        self.workspace.clear_audio_route(slot)
        self._write_log(f"已清除音频 {slot}；该槽位恢复使用原版。")
        self._refresh_audio()
        self._refresh_summary()

    def _browse_audio_package(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="导入音频包或完整资产包 ZIP",
            filetypes=[("ZIP 资产包", "*.zip"), ("所有文件", "*.*")],
        )
        if path:
            self._import_package(Path(path))

    def _browse_package(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="导入完整资产包",
            filetypes=[("ZIP 资产包", "*.zip"), ("所有文件", "*.*")],
        )
        if path:
            self._import_package(Path(path))

    def _drop_package(self, paths: list[Path]) -> None:
        if paths:
            self._import_package(paths[0])

    def _import_package(self, path: Path) -> None:
        try:
            if path.suffix.casefold() != ".zip":
                raise ValueError("资产包入口仅接受 ZIP 文件。")
            summary = self.workspace.import_zip(path)
            self._write_log(
                f"已导入 {summary.kind}："
                f"{len(summary.visual_slots)} 个图像槽位，"
                f"{len(summary.audio_routes)} 条音频路由，"
                f"{len(summary.animation_files)} 个动画文件；"
                f"忽略 {len(summary.ignored)} 项。"
            )
            self._load_workspace_into_ui()
            self._refresh_all()
        except Exception as error:
            self._show_error("资产包导入失败", error)

    def _browse_animation(self) -> None:
        paths = filedialog.askopenfilenames(
            parent=self.root,
            title="导入动画源文件",
            filetypes=[
                (
                    "动画源文件",
                    "*.skel *.json *.atlas *.png *.bundle *.assetbundle",
                ),
                ("所有文件", "*.*"),
            ],
        )
        if paths:
            self._drop_animation([Path(path) for path in paths])

    def _drop_animation(self, paths: list[Path]) -> None:
        try:
            accepted = self.workspace.import_animation(
                paths,
                self.animation_mode.get(),
            )
            self._write_log(f"已导入 {len(accepted)} 个动画源文件。")
            self._refresh_animation()
            self._refresh_summary()
        except Exception as error:
            self._show_error("动画导入失败", error)

    def _clear_animation(self) -> None:
        self.workspace.clear_animation()
        self._write_log("已清除动画源文件。")
        self._refresh_animation()
        self._refresh_summary()

    def _clear_loaded_skin(self) -> None:
        if self.busy:
            return
        try:
            deployed = bool(self.workspace.diagnostics().get("installed"))
        except Exception as error:
            self._show_error("无法检查部署状态", error)
            return
        deployment_note = (
            "\n\n检测到当前皮肤已经部署。继续后会先取消部署并恢复游戏原版，"
            "然后清空当前工作区。"
            if deployed
            else ""
        )
        if not messagebox.askyesno(
            "清空已加载皮肤",
            (
                "确定清空当前工作区中的全部图像、音频和动画资产吗？"
                "\n\n资产包元数据和所选英雄会保留。此操作不可撤销。"
                + deployment_note
            ),
            parent=self.root,
        ):
            return

        def clear_operation() -> dict:
            restored: list[str] = []
            if deployed:
                restored = self.workspace.undeploy()
            counts = self.workspace.clear_loaded_assets()
            return {"restored": restored, "counts": counts}

        self._run_background(
            "正在清空已加载皮肤…",
            clear_operation,
            self._clear_loaded_skin_complete,
        )

    def _clear_loaded_skin_complete(self, result: dict) -> None:
        self.busy = False
        counts = result["counts"]
        self._set_skin_options(self._selected_hero(), self._selected_skin()["id"])
        self._refresh_all()
        text = (
            f"已清空 {counts['visual_slots']} 个图像槽位、"
            f"{counts['audio_routes']} 条音频路由和 "
            f"{counts['animation_files']} 个动画文件。"
        )
        if result["restored"]:
            text += " 已同时取消部署并恢复游戏原版。"
        self._write_log(text)
        messagebox.showinfo("清空完成", text, parent=self.root)

    def _new_workspace(self) -> None:
        pack_id = self._prompt_text(
            "新建工作区",
            "资产包 ID：",
            "local.custom.skin",
        )
        if not pack_id:
            return
        try:
            self.workspace = StudioWorkspace.create(pack_id)
            self._load_workspace_into_ui()
            self._refresh_all()
            self._write_log(f"已创建工作区：{self.workspace.directory}")
        except Exception as error:
            self._show_error("无法创建工作区", error)

    def _open_workspace(self) -> None:
        path = filedialog.askdirectory(
            parent=self.root,
            title="打开皮肤管理器工作区",
            initialdir=WORKSPACES_ROOT,
        )
        if not path:
            return
        try:
            self.workspace = StudioWorkspace.load(Path(path))
            self._load_workspace_into_ui()
            self._refresh_all()
            self._write_log(f"已打开工作区：{self.workspace.directory}")
        except Exception as error:
            self._show_error("无法打开工作区", error)

    def _export_zip(self) -> None:
        self._metadata_changed()
        default = (
            f"{self.workspace.state['pack']['id']}-"
            f"{self.workspace.state['pack']['version']}.zip"
        )
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="导出完整资产包",
            defaultextension=".zip",
            initialfile=default,
            filetypes=[("ZIP 资产包", "*.zip")],
        )
        if not path:
            return
        try:
            destination = self.workspace.export_zip(Path(path))
            self._write_log(f"已导出完整资产包：{destination}")
        except Exception as error:
            self._show_error("资产包导出失败", error)

    def _open_workspace_folder(self) -> None:
        os.startfile(self.workspace.directory)

    def _deploy(self) -> None:
        if self.busy:
            return
        self._metadata_changed()
        if messagebox.askyesno(
            "部署皮肤",
            (
                "部署当前工作区并替换现有托管皮肤吗？\n\n"
                "继续前请关闭 The Bazaar。未填充的槽位会使用游戏原版资产。"
            ),
            parent=self.root,
        ):
            self._run_background(
                "正在部署…",
                lambda: self.workspace.deploy(self.game_dir_override),
                lambda result: self._operation_complete(
                    "部署完成",
                    f"已安装 {result['pack']['id']} {result['pack']['version']}。",
                ),
            )

    def _undeploy(self) -> None:
        if self.busy:
            return
        if messagebox.askyesno(
            "恢复游戏原版",
            "移除托管运行时和资产包，并恢复游戏原版资产吗？",
            parent=self.root,
        ):
            self._run_background(
                "正在取消部署…",
                self.workspace.undeploy,
                lambda removed: self._operation_complete(
                    "取消部署完成",
                    f"已移除 {len(removed)} 个托管路径。",
                ),
            )

    def _launch_game(self) -> None:
        if self.busy:
            return
        self._run_background(
            "正在通过 Steam 启动 The Bazaar…",
            lambda: self.workspace.launch_game(self.game_dir_override),
            self._launch_complete,
        )

    def _launch_complete(self, result: dict) -> None:
        self.busy = False
        self._set_skin_options(self._selected_hero(), self._selected_skin()["id"])
        self._write_log(
            f"已通过 Steam 启动 The Bazaar（{result['method']}）："
            f"{result['game_dir']}"
        )
        self._refresh_deployment_status()

    def _run_background(self, status: str, operation, on_success) -> None:
        self.busy = True
        self.deploy_button.configure(state="disabled")
        self.header_deploy_button.configure(state="disabled")
        self.play_button.configure(state="disabled")
        self.install_status.configure(text=status, foreground=COLORS["warning"])

        def worker() -> None:
            try:
                result = operation()
            except Exception as error:
                self.root.after(
                    0,
                    lambda caught=error: self._background_failed(caught),
                )
            else:
                self.root.after(0, lambda: on_success(result))

        threading.Thread(target=worker, daemon=True).start()

    def _background_failed(self, error: Exception) -> None:
        self.busy = False
        self._set_skin_options(self._selected_hero(), self._selected_skin()["id"])
        self._show_error("操作失败", error)
        self._refresh_deployment_status()

    def _operation_complete(self, title: str, text: str) -> None:
        self.busy = False
        self._set_skin_options(self._selected_hero(), self._selected_skin()["id"])
        self._write_log(text)
        self._refresh_deployment_status()
        messagebox.showinfo(title, text, parent=self.root)

    def _prompt_text(self, title: str, prompt: str, initial: str) -> str | None:
        window = tk.Toplevel(self.root)
        window.title(title)
        window.configure(bg=COLORS["panel"])
        window.transient(self.root)
        window.grab_set()
        value = tk.StringVar(value=initial)
        ttk.Label(window, text=prompt).pack(
            padx=18, pady=(18, 6), anchor="w"
        )
        entry = ttk.Entry(window, textvariable=value, width=44)
        entry.pack(padx=18, fill="x")
        result: list[str | None] = [None]

        def accept() -> None:
            result[0] = value.get().strip()
            window.destroy()

        ttk.Button(window, text="确定", command=accept).pack(
            padx=18, pady=18, anchor="e"
        )
        entry.focus_set()
        entry.bind("<Return>", lambda _event: accept())
        self.root.wait_window(window)
        return result[0]

    def _write_log(self, text: str) -> None:
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")

    def _show_error(self, title: str, error: Exception) -> None:
        self._write_log(f"错误：{error}")
        if os.environ.get("BAZAAR_SKIN_MANAGER_STUDIO_DEBUG") == "1":
            self._write_log(traceback.format_exc())
        messagebox.showerror(title, str(error), parent=self.root)

    def run(self) -> None:
        self.root.mainloop()


def main() -> int:
    if "--restore-before-uninstall" in sys.argv:
        try:
            restore_before_application_uninstall()
        except Exception:
            return 2
        return 0
    if "--self-test-release-runtime" in sys.argv:
        root = None
        try:
            from archspec.cpu import host
            import fmod_toolkit.fmod  # noqa: F401

            if not host().name:
                return 5
            # Exercise the frozen Tcl/Tk payload, not just the Python import.
            # A mismatched tcl86t.dll and init.tcl otherwise survives packaging
            # and only fails when the user opens the manager.
            root = RootClass()
            root.withdraw()
            root.update_idletasks()
        except Exception:
            return 3
        finally:
            if root is not None:
                root.destroy()
        return 0
    if "--smoke-import" in sys.argv:
        try:
            index = sys.argv.index("--smoke-import")
            archive = Path(sys.argv[index + 1])
            with tempfile.TemporaryDirectory() as temp:
                workspace = StudioWorkspace.create(
                    "release.smoke",
                    root=Path(temp),
                )
                workspace.import_zip(archive)
        except Exception:
            return 4
        return 0
    if "--smoke-deploy" in sys.argv:
        workspace = None
        deployed = False
        try:
            index = sys.argv.index("--smoke-deploy")
            archive = Path(sys.argv[index + 1])
            game_dir = Path(sys.argv[index + 2])
            with tempfile.TemporaryDirectory() as temp:
                workspace = StudioWorkspace.create(
                    "release.deploy-smoke",
                    root=Path(temp),
                )
                workspace.import_zip(archive)
                workspace.deploy(game_dir)
                deployed = True
                workspace.undeploy()
                deployed = False
        except Exception:
            if workspace is not None:
                try:
                    workspace.undeploy()
                except Exception:
                    return 8
            return 7
        return 0 if not deployed else 9
    ModManagerStudio().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
