#!/usr/bin/env python3
"""Desktop UI for authoring and deploying The Bazaar skin packs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageGrab, ImageTk

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
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        window_width = min(1440, max(1024, screen_width - 80))
        window_height = min(900, max(640, screen_height - 140))
        window_x = max(0, (screen_width - window_width) // 2)
        window_y = max(0, (screen_height - window_height - 40) // 2)
        self.root.geometry(
            f"{window_width}x{window_height}+{window_x}+{window_y}"
        )
        self.root.minsize(1024, 640)
        self.root.configure(bg=COLORS["window"])
        self.catalog = catalog()
        self.first_run = False
        self.game_dir_override: Path | None = None
        self.settings_payload: dict = {}
        self.managed_workspaces: dict[str, bool] = {}
        self.hub_selections: dict[str, str] = {}
        self.workspace = self._open_last_or_default()
        self._load_managed_workspaces()
        self.catalog = discovered_catalog(self.game_dir_override)
        self.preview_images: dict[str, ImageTk.PhotoImage] = {}
        self.slot_widgets: dict[str, dict[str, tk.Widget]] = {}
        self.busy = False
        self._configure_style()
        self._build_ui()
        self._configure_shortcuts()
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

    def _configure_shortcuts(self) -> None:
        self.root.bind_all(
            "<Control-Key-1>",
            lambda _event: self._select_hub_page(),
        )
        self.root.bind_all(
            "<Control-Key-2>",
            lambda _event: self.main_pages.select(1),
        )
        self.root.bind_all(
            "<Control-Key-3>",
            lambda _event: self.main_pages.select(2),
        )
        self.root.bind_all(
            "<Control-Key-4>",
            lambda _event: self.main_pages.select(3),
        )
        self.root.bind_all(
            "<F5>",
            lambda _event: self._refresh_deployment_status(),
        )
        self.root.bind_all(
            "<Control-Key-g>",
            lambda _event: self._launch_asset_generator(),
        )
        self.root.bind_all(
            "<Control-Key-p>",
            lambda _event: self._launch_spine_manager(),
        )

    def _select_hub_page(self) -> None:
        self.main_pages.select(0)
        children = self.hub_tree.get_children()
        if children and not self.hub_tree.selection():
            self.hub_tree.selection_set(children[0])
            self.hub_tree.focus(children[0])
        self.hub_tree.focus_set()

    def _settings_path(self) -> Path:
        return WORKSPACES_ROOT.parent / "studio-settings.json"

    def _open_last_or_default(self) -> StudioWorkspace:
        settings = self._settings_path()
        self.first_run = not settings.is_file()
        if settings.is_file():
            try:
                payload = json.loads(settings.read_text(encoding="utf-8"))
                self.settings_payload = payload
                path = Path(payload["workspace"])
                if payload.get("game_dir"):
                    self.game_dir_override = Path(payload["game_dir"])
                if path.is_dir():
                    return StudioWorkspace.load(path)
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                pass
        return StudioWorkspace.create("local.custom.skin")

    def _load_managed_workspaces(self) -> None:
        self.hub_selections = {
            str(key): str(value)
            for key, value in (
                self.settings_payload.get("target_selections") or {}
            ).items()
            if isinstance(key, str) and isinstance(value, str)
        }
        records = self.settings_payload.get("managed_workspaces") or []
        for record in records:
            try:
                path = str(Path(record["path"]).resolve())
                if Path(path).is_dir():
                    self.managed_workspaces[path] = bool(
                        record.get("enabled", True)
                    )
            except (KeyError, OSError, TypeError):
                continue
        current = str(self.workspace.directory.resolve())
        self.managed_workspaces.setdefault(current, True)
        if WORKSPACES_ROOT.is_dir():
            for state_file in WORKSPACES_ROOT.glob("*/studio.json"):
                path = str(state_file.parent.resolve())
                self.managed_workspaces.setdefault(path, False)

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
                    "managed_workspaces": [
                        {"path": path, "enabled": enabled}
                        for path, enabled in sorted(
                            self.managed_workspaces.items()
                        )
                        if Path(path).is_dir()
                    ],
                    "target_selections": self.hub_selections,
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
        self.play_button = ttk.Button(
            header,
            text="启动游戏",
            style="Accent.TButton",
            command=self._launch_game,
        )
        self.play_button.pack(side="right", padx=(8, 4))

        self.main_pages = ttk.Notebook(outer)
        self.main_pages.pack(fill="both", expand=True)
        hub = ttk.Frame(self.main_pages, padding=18)
        editor = ttk.Frame(self.main_pages, style="Window.TFrame")
        generator = ttk.Frame(self.main_pages, padding=24)
        spine = ttk.Frame(self.main_pages, padding=24)
        self.main_pages.add(hub, text="皮肤控制中台")
        self.main_pages.add(editor, text="资产导入管理器")
        self.main_pages.add(generator, text="素材包制作器")
        self.main_pages.add(spine, text="Spine 动画管理器")
        self.editor_page = editor
        self._build_control_hub(hub)
        self._build_asset_generator_component(generator)
        self._build_spine_manager_component(spine)

        body = ttk.Panedwindow(editor, orient="horizontal")
        body.pack(fill="both", expand=True)
        sidebar = ttk.Frame(body, padding=16)
        workspace_panel = ttk.Frame(body, style="Alt.TFrame", padding=0)
        body.add(sidebar, weight=0)
        body.add(workspace_panel, weight=1)
        self._build_sidebar(sidebar)
        self._build_workspace(workspace_panel)

    def _build_asset_generator_component(self, parent: ttk.Frame) -> None:
        heading = ttk.Frame(parent)
        heading.pack(fill="x", pady=(0, 18))
        ttk.Label(
            heading,
            text="素材包制作器",
            font=("Microsoft YaHei UI", 18, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            heading,
            text="皮肤管理器内置组件 · 从原始图片生成标准资产包，再回到控制中台部署。",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(4, 0))

        workflow = ttk.LabelFrame(parent, text="制作流程", padding=18)
        workflow.pack(fill="x", pady=(0, 16))
        for index, text in enumerate(
            (
                "导入人物、背景和图标素材",
                "调整各槽位构图并实时预览",
                "生成完整资产包并导入皮肤管理器",
            ),
            start=1,
        ):
            ttk.Label(
                workflow,
                text=f"{index}. {text}",
                font=("Microsoft YaHei UI", 11),
            ).pack(anchor="w", pady=4)

        actions = ttk.LabelFrame(parent, text="组件操作", padding=18)
        actions.pack(fill="x")
        self.generator_status = ttk.Label(
            actions,
            text="正在检查素材包制作器…",
            style="Muted.TLabel",
        )
        self.generator_status.pack(anchor="w", pady=(0, 12))
        row = ttk.Frame(actions)
        row.pack(fill="x")
        self.generator_launch_button = ttk.Button(
            row,
            text="打开素材包制作器（Ctrl+G）",
            style="Accent.TButton",
            command=self._launch_asset_generator,
        )
        self.generator_launch_button.pack(side="left")
        ttk.Button(
            row,
            text="返回皮肤控制中台",
            command=lambda: self.main_pages.select(0),
        ).pack(side="left", padx=(10, 0))
        self._refresh_generator_component()

    def _build_spine_manager_component(self, parent: ttk.Frame) -> None:
        heading = ttk.Frame(parent)
        heading.pack(fill="x", pady=(0, 18))
        ttk.Label(
            heading,
            text="Spine 动画管理器",
            font=("Microsoft YaHei UI", 18, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            heading,
            text=(
                "皮肤管理器内置组件 · 导入 Spine 3.5–3.8、4.0–4.2 资源、离线预览位置，"
                "部署与恢复继续使用统一事务。"
            ),
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(4, 0))

        workflow = ttk.LabelFrame(parent, text="动画替换流程", padding=18)
        workflow.pack(fill="x", pady=(0, 16))
        for index, text in enumerate(
            (
                "导入 JSON、Atlas 和 Atlas 声明的贴图",
                "选择职业默认皮肤与动画并拖拽调整位置",
                "通过皮肤管理器事务合并当前多职业皮肤后部署",
            ),
            start=1,
        ):
            ttk.Label(
                workflow,
                text=f"{index}. {text}",
                font=("Microsoft YaHei UI", 11),
            ).pack(anchor="w", pady=4)

        actions = ttk.LabelFrame(parent, text="组件操作", padding=18)
        actions.pack(fill="x")
        self.spine_manager_status = ttk.Label(
            actions,
            text="正在检查 Spine 动画管理器…",
            style="Muted.TLabel",
        )
        self.spine_manager_status.pack(anchor="w", pady=(0, 12))
        row = ttk.Frame(actions)
        row.pack(fill="x")
        self.spine_manager_launch_button = ttk.Button(
            row,
            text="打开 Spine 动画管理器（Ctrl+P）",
            style="Accent.TButton",
            command=self._launch_spine_manager,
        )
        self.spine_manager_launch_button.pack(side="left")
        ttk.Button(
            row,
            text="返回皮肤控制中台",
            command=lambda: self.main_pages.select(0),
        ).pack(side="left", padx=(10, 0))
        self._refresh_spine_manager_component()

    def _build_control_hub(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)
        title = ttk.Frame(parent)
        title.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        ttk.Label(
            title,
            text="多职业皮肤控制中台",
            font=("Microsoft YaHei UI", 16, "bold"),
        ).pack(side="left")
        ttk.Label(
            title,
            text="点击勾叉启停；点击资产包单元格切换。键盘可用空格启停、F4 选择资产包。",
            style="Muted.TLabel",
        ).pack(side="left", padx=(18, 0))

        columns = ("hero", "skin", "pack", "version", "path")
        table_group = ttk.LabelFrame(parent, text="已管理的职业皮肤", padding=10)
        table_group.grid(row=1, column=0, sticky="nsew")
        self.hub_tree = ttk.Treeview(
            table_group,
            columns=columns,
            show="tree headings",
            selectmode="browse",
        )
        headings = {
            "hero": "职业",
            "skin": "被替换皮肤",
            "pack": "资产包（点击选择）",
            "version": "版本",
            "path": "来源工作区",
        }
        widths = {
            "hero": 120,
            "skin": 260,
            "pack": 280,
            "version": 80,
            "path": 460,
        }
        self.hub_tree.heading("#0", text="启用")
        self.hub_tree.column("#0", width=82, minwidth=82, stretch=False, anchor="center")
        for column in columns:
            self.hub_tree.heading(column, text=headings[column])
            self.hub_tree.column(
                column,
                width=widths[column],
                stretch=column == "path",
            )
        hub_vertical = ttk.Scrollbar(
            table_group,
            orient="vertical",
            command=self.hub_tree.yview,
        )
        hub_horizontal = ttk.Scrollbar(
            table_group,
            orient="horizontal",
            command=self.hub_tree.xview,
        )
        self.hub_tree.configure(
            yscrollcommand=hub_vertical.set,
            xscrollcommand=hub_horizontal.set,
        )
        self.hub_tree.grid(row=0, column=0, sticky="nsew")
        hub_vertical.grid(row=0, column=1, sticky="ns")
        hub_horizontal.grid(row=1, column=0, sticky="ew")
        table_group.rowconfigure(0, weight=1)
        table_group.columnconfigure(0, weight=1)
        self.hub_tree.bind("<Double-1>", self._hub_double_clicked)
        self.hub_tree.bind("<Button-1>", self._hub_clicked, add="+")
        self.hub_tree.bind(
            "<space>",
            lambda _event: (self._hub_toggle(), "break")[1],
        )
        self.hub_tree.bind(
            "<Return>",
            lambda _event: (self._hub_open(), "break")[1],
        )
        self.hub_tree.bind(
            "<F4>",
            lambda _event: (self._hub_open_selected_pack(), "break")[1],
        )
        self._build_hub_status_icons()

        actions = ttk.Frame(parent)
        actions.grid(row=2, column=0, sticky="ew", pady=(14, 0))
        workspace_actions = ttk.LabelFrame(
            actions,
            text="资产管理",
            padding=10,
        )
        workspace_actions.pack(side="left", fill="x", expand=True)
        deployment_actions = ttk.LabelFrame(
            actions,
            text="部署",
            padding=10,
        )
        deployment_actions.pack(side="right", fill="x", padx=(12, 0))
        ttk.Button(
            workspace_actions,
            text="打开资产导入管理器",
            command=self._hub_open,
        ).pack(side="left")
        ttk.Button(
            workspace_actions,
            text="添加现有工作区…",
            command=self._hub_add,
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            deployment_actions,
            text="部署",
            style="Accent.TButton",
            command=self._deploy_all,
        ).pack(side="left")
        ttk.Button(
            deployment_actions,
            text="取消部署",
            style="Danger.TButton",
            command=self._undeploy,
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            deployment_actions,
            text="刷新状态",
            command=self._refresh_deployment_status,
        ).pack(side="left", padx=(8, 0))

    def _build_sidebar(self, parent: ttk.Frame) -> None:
        parent.configure(width=330)
        actions = ttk.Frame(parent)
        actions.pack(fill="x", side="bottom", pady=(18, 0))
        self.deploy_button = ttk.Button(
            actions,
            text="保存并加入控制中台",
            style="Accent.TButton",
            command=self._import_current_to_hub,
        )
        self.deploy_button.pack(fill="x", pady=(0, 8))
        ttk.Button(
            actions,
            text="返回皮肤控制中台",
            command=lambda: self.main_pages.select(0),
        ).pack(fill="x")

        sections = ttk.Notebook(parent)
        sections.pack(fill="both", expand=True)
        target_panel = ttk.Frame(sections, padding=12)
        pack_panel = ttk.Frame(sections, padding=12)
        sections.add(target_panel, text="目标与游戏")
        sections.add(pack_panel, text="资产包")

        ttk.Label(
            target_panel,
            text="资产导入目标",
            font=("Microsoft YaHei UI", 13, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            target_panel,
            text="选择要将当前这套资产导入到的职业和被替换皮肤。",
            style="Muted.TLabel",
            wraplength=275,
        ).pack(anchor="w", pady=(3, 12))

        self.hero_var = tk.StringVar()
        self.hero_combo = ttk.Combobox(
            target_panel,
            textvariable=self.hero_var,
            state="readonly",
            values=[hero["display_name"] for hero in self.catalog["heroes"]],
        )
        self.hero_combo.pack(fill="x", pady=(0, 8))
        self.hero_combo.bind("<<ComboboxSelected>>", self._hero_changed)

        self.skin_var = tk.StringVar()
        self.skin_combo = ttk.Combobox(
            target_panel,
            textvariable=self.skin_var,
            state="readonly",
        )
        self.skin_combo.pack(fill="x")
        self.skin_combo.bind("<<ComboboxSelected>>", self._skin_changed)
        self.hero_support = ttk.Label(
            target_panel,
            text="",
            style="Muted.TLabel",
            wraplength=275,
        )
        self.hero_support.pack(anchor="w", pady=(8, 18))

        self.game_status = ttk.Label(
            target_panel,
            text="正在查找 The Bazaar…",
            style="Muted.TLabel",
            wraplength=275,
        )
        self.game_status.pack(anchor="w", pady=(0, 8))
        ttk.Button(
            target_panel,
            text="重新扫描游戏位置",
            command=self._refresh_deployment_status,
        ).pack(anchor="w")
        ttk.Button(
            target_panel,
            text="手动选择游戏目录…",
            command=self._locate_game,
        ).pack(anchor="w", pady=(4, 14))

        ttk.Separator(target_panel).pack(fill="x", pady=(0, 16))
        ttk.Label(
            pack_panel,
            text="待导入资产包",
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
            ttk.Label(pack_panel, text=label, style="Muted.TLabel").pack(
                anchor="w", pady=(10, 3)
            )
            entry = ttk.Entry(pack_panel, textvariable=variable)
            entry.pack(fill="x")
            entry.bind("<FocusOut>", lambda _event: self._metadata_changed())

        ttk.Separator(pack_panel).pack(fill="x", pady=16)
        self.drop_zone = tk.Label(
            pack_panel,
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
                pack_panel,
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
        actions = ttk.LabelFrame(
            self.package_tab,
            text="工作区",
            padding=10,
        )
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
            text="打开工作区文件夹",
            command=self._open_workspace_folder,
        ).pack(side="left", padx=5)

        package_actions = ttk.LabelFrame(
            self.package_tab,
            text="导出与清理",
            padding=10,
        )
        package_actions.pack(fill="x", pady=(10, 0))
        ttk.Button(
            package_actions,
            text="导出完整 ZIP",
            command=self._export_zip,
        ).pack(side="left")
        ttk.Button(
            package_actions,
            text="清空当前工作区资产",
            command=self._clear_loaded_skin,
        ).pack(side="left", padx=(8, 0))

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
        if hasattr(self, "hub_tree"):
            self._refresh_hub()

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
            self.managed_workspaces.setdefault(
                str(self.workspace.directory.resolve()),
                True,
            )
            self._remember_workspace()
            self._refresh_summary()
            self._refresh_hub()
        except ValueError as error:
            self._write_log(f"元数据未保存：{error}")

    def _refresh_all(self) -> None:
        self._refresh_hub()
        self._refresh_visuals()
        self._refresh_audio()
        self._refresh_animation()
        self._refresh_summary()
        self._refresh_deployment_status()

    def _refresh_hub(self) -> None:
        if not hasattr(self, "hub_tree"):
            return
        selected = self.hub_tree.selection()
        selected_target_key = None
        if selected and hasattr(self, "hub_rows"):
            selected_target_key = (
                self.hub_rows.get(selected[0]) or {}
            ).get("target_key")
        selected_path = (
            self.hub_tree.set(selected[0], "path") if selected else None
        )
        self._close_hub_pack_editor()
        for item in self.hub_tree.get_children():
            self.hub_tree.delete(item)
        self.hub_paths: dict[str, str] = {}
        self.hub_rows: dict[str, dict] = {}
        self.hub_pack_choices: dict[str, dict[str, str]] = {}
        grouped: dict[str, list[dict]] = {}
        for path_text, enabled in sorted(self.managed_workspaces.items()):
            path = Path(path_text)
            try:
                workspace = StudioWorkspace.load(path)
            except Exception:
                continue
            state = workspace.state
            target = state.get("target") or {}
            pack = state.get("pack") or {}
            hero = str(target.get("hero") or "")
            skin = str(target.get("skin") or "")
            target_key = self._hub_target_key(hero, skin)
            grouped.setdefault(target_key, []).append(
                {
                    "path": path_text,
                    "enabled": enabled,
                    "hero": hero,
                    "skin": skin,
                    "pack": pack,
                }
            )

        current_path = str(self.workspace.directory.resolve())
        for index, (target_key, records) in enumerate(sorted(grouped.items())):
            paths = {record["path"] for record in records}
            enabled_records = [record for record in records if record["enabled"]]
            selected_pack_path = self.hub_selections.get(target_key)
            if enabled_records:
                selected_pack_path = enabled_records[0]["path"]
            elif selected_pack_path not in paths:
                selected_pack_path = (
                    current_path if current_path in paths else records[0]["path"]
                )
            self.hub_selections[target_key] = selected_pack_path
            selected_record = next(
                record
                for record in records
                if record["path"] == selected_pack_path
            )
            pack = selected_record["pack"]
            pack_name = str(pack.get("name") or pack.get("id") or "未命名资产包")
            version = str(pack.get("version") or "")
            pack_display = pack_name
            iid = f"target-{index}"
            self.hub_paths[iid] = selected_pack_path
            choices: dict[str, str] = {}
            for record in records:
                candidate_pack = record["pack"]
                candidate_name = str(
                    candidate_pack.get("name")
                    or candidate_pack.get("id")
                    or "未命名资产包"
                )
                candidate_version = str(candidate_pack.get("version") or "")
                label = (
                    f"{candidate_name} · {candidate_version}"
                    if candidate_version
                    else candidate_name
                )
                if label in choices:
                    label = f"{label} · {Path(record['path']).name}"
                choices[label] = record["path"]
            self.hub_pack_choices[iid] = choices
            selected_choice_label = next(
                label
                for label, path in choices.items()
                if path == selected_pack_path
            )
            enabled = bool(self.managed_workspaces.get(selected_pack_path, False))
            self.hub_rows[iid] = {
                "target_key": target_key,
                "paths": sorted(paths),
                "selected_path": selected_pack_path,
                "pack_display": selected_choice_label,
            }
            self.hub_tree.insert(
                "",
                "end",
                iid=iid,
                text="",
                image=(
                    self.hub_enabled_icon
                    if enabled
                    else self.hub_disabled_icon
                ),
                values=(
                    selected_record["hero"],
                    self._hub_skin_label(
                        selected_record["hero"],
                        selected_record["skin"],
                    ),
                    f"▼  {pack_name}",
                    version,
                    selected_pack_path,
                ),
            )
            if (
                selected_target_key == target_key
                or selected_path == selected_pack_path
            ):
                self.hub_tree.selection_set(iid)

    @staticmethod
    def _hub_target_key(hero: str, skin: str) -> str:
        return f"{hero}|{skin}"

    def _hub_skin_label(self, hero: str, skin: str) -> str:
        for hero_record in self.catalog["heroes"]:
            if hero_record["id"] != hero:
                continue
            skins = hero_record.get("skins") or []
            for index, skin_record in enumerate(skins):
                if skin_record["id"] == skin:
                    suffix = "（默认皮肤）" if index == 0 else ""
                    return f"{skin}{suffix}"
        return skin

    def _build_hub_status_icons(self) -> None:
        def build(enabled: bool) -> ImageTk.PhotoImage:
            image = Image.new("RGBA", (24, 24), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            fill = COLORS["accent"] if enabled else COLORS["danger"]
            draw.ellipse((2, 2, 21, 21), fill=fill)
            if enabled:
                draw.line((7, 12, 10, 16, 17, 8), fill="white", width=3)
            else:
                draw.line((8, 8, 16, 16), fill="white", width=3)
                draw.line((16, 8, 8, 16), fill="white", width=3)
            return ImageTk.PhotoImage(image)

        self.hub_enabled_icon = build(True)
        self.hub_disabled_icon = build(False)

    def _hub_clicked(self, event: tk.Event) -> str | None:
        iid = self.hub_tree.identify_row(event.y)
        column = self.hub_tree.identify_column(event.x)
        if column != "#3":
            self._close_hub_pack_editor()
        if not iid:
            return None
        self.hub_tree.selection_set(iid)
        self.hub_tree.focus(iid)
        if column == "#0":
            self.root.after_idle(self._hub_toggle)
            return "break"
        if column == "#3":
            self.root.after_idle(lambda: self._open_hub_pack_editor(iid))
            return "break"
        return None

    def _hub_double_clicked(self, event: tk.Event) -> str | None:
        column = self.hub_tree.identify_column(event.x)
        if column in {"#0", "#3"}:
            return "break"
        self._hub_open()
        return "break"

    def _open_hub_pack_editor(self, iid: str) -> None:
        choices = self.hub_pack_choices.get(iid) or {}
        if not choices:
            return
        self._close_hub_pack_editor()
        bbox = self.hub_tree.bbox(iid, "pack")
        if not bbox:
            return
        x, y, width, height = bbox
        row = self.hub_rows[iid]
        variable = tk.StringVar(value=row["pack_display"])
        editor = ttk.Combobox(
            self.hub_tree,
            textvariable=variable,
            values=list(choices),
            state="readonly",
        )
        editor.place(x=x, y=y, width=width, height=height)
        editor.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._hub_pack_selected(iid, variable.get()),
        )
        editor.bind(
            "<Escape>",
            lambda _event: self._close_hub_pack_editor(restore_focus=True),
        )
        self.hub_pack_editor = editor
        editor.focus_set()
        editor.event_generate(
            "<Button-1>",
            x=max(1, width - 8),
            y=max(1, height // 2),
        )

    def _hub_open_selected_pack(self) -> None:
        selected = self.hub_tree.selection()
        if selected:
            self._open_hub_pack_editor(selected[0])

    def _close_hub_pack_editor(self, restore_focus: bool = False) -> None:
        editor = getattr(self, "hub_pack_editor", None)
        if editor is not None:
            try:
                editor.destroy()
            except tk.TclError:
                pass
        self.hub_pack_editor = None
        if restore_focus:
            try:
                self.hub_tree.focus_set()
            except tk.TclError:
                pass

    def _hub_pack_selected(self, iid: str, label: str) -> None:
        selected_path = (self.hub_pack_choices.get(iid) or {}).get(label)
        row = self.hub_rows.get(iid)
        if not selected_path or not row:
            self._close_hub_pack_editor()
            return
        was_enabled = any(
            self.managed_workspaces.get(path, False)
            for path in row["paths"]
        )
        for path in row["paths"]:
            self.managed_workspaces[path] = False
        self.managed_workspaces[selected_path] = was_enabled
        self.hub_selections[row["target_key"]] = selected_path
        self._remember_workspace()
        self._refresh_hub()

    def _hub_selected_path(self) -> Path | None:
        selected = self.hub_tree.selection()
        if not selected:
            return None
        value = self.hub_paths.get(selected[0])
        return Path(value) if value else None

    def _hub_toggle(self) -> None:
        path = self._hub_selected_path()
        if path is None:
            return
        key = str(path.resolve())
        selected = self.hub_tree.selection()
        row = self.hub_rows.get(selected[0]) if selected else None
        enable = not self.managed_workspaces.get(key, False)
        if row:
            for candidate in row["paths"]:
                self.managed_workspaces[candidate] = False
            self.hub_selections[row["target_key"]] = key
        self.managed_workspaces[key] = enable
        self._remember_workspace()
        self._refresh_hub()

    def _hub_open(self) -> None:
        path = self._hub_selected_path()
        if path is None:
            return
        try:
            self.workspace = StudioWorkspace.load(path)
            self._load_workspace_into_ui()
            self._refresh_all()
            self.main_pages.select(self.editor_page)
            self._write_log(f"已从控制中台打开：{path}")
        except Exception as error:
            self._show_error("无法打开工作区", error)

    def _hub_add(self) -> None:
        path = filedialog.askdirectory(
            parent=self.root,
            title="添加皮肤管理器工作区",
            initialdir=WORKSPACES_ROOT,
        )
        if not path:
            return
        try:
            workspace = StudioWorkspace.load(Path(path))
            key = str(workspace.directory.resolve())
            self.managed_workspaces[key] = True
            self._remember_workspace()
            self._refresh_hub()
        except Exception as error:
            self._show_error("无法添加工作区", error)

    def _import_current_to_hub(self) -> None:
        self._metadata_changed()
        path = str(self.workspace.directory.resolve())
        target = self.workspace.state.get("target") or {}
        target_key = self._hub_target_key(
            str(target.get("hero") or ""),
            str(target.get("skin") or ""),
        )
        self.managed_workspaces.setdefault(path, False)
        self.hub_selections[target_key] = path
        self._remember_workspace()
        self._refresh_hub()
        self.main_pages.select(0)
        self._write_log(f"资产包已加入控制中台：{path}")

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
            self.managed_workspaces[
                str(self.workspace.directory.resolve())
            ] = True
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
            self.managed_workspaces[
                str(self.workspace.directory.resolve())
            ] = True
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
                    "已安装 " + ", ".join(
                        f"{item['id']} {item['version']}"
                        for item in result["packs"]
                    ) + "。",
                ),
            )

    def _deploy_all(self) -> None:
        if self.busy:
            return
        self._metadata_changed()
        workspaces: list[StudioWorkspace] = []
        invalid: list[str] = []
        for path_text, enabled in self.managed_workspaces.items():
            if not enabled:
                continue
            try:
                workspaces.append(StudioWorkspace.load(Path(path_text)))
            except Exception as error:
                invalid.append(f"{path_text}: {error}")
        if invalid:
            messagebox.showerror(
                "工作区不可用",
                "\n".join(invalid),
                parent=self.root,
            )
            return
        if not workspaces:
            messagebox.showinfo(
                "没有启用的皮肤",
                "请先在控制中台启用至少一个职业皮肤。",
                parent=self.root,
            )
            return
        if not messagebox.askyesno(
            "部署多职业皮肤",
            (
                f"将同时部署 {len(workspaces)} 个已启用皮肤。\n\n"
                "继续前请关闭 The Bazaar。相同英雄/皮肤只能启用一个工作区。"
            ),
            parent=self.root,
        ):
            return
        self._run_background(
            "正在部署全部已启用皮肤…",
            lambda: StudioWorkspace.deploy_many(
                workspaces,
                self.game_dir_override,
            ),
            lambda result: self._operation_complete(
                "多职业皮肤部署完成",
                "已安装 " + ", ".join(
                    f"{item['id']} {item['version']}"
                    for item in result["packs"]
                ) + "。",
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

    def _asset_generator_command(self) -> list[str] | None:
        candidates: list[Path] = []
        if getattr(sys, "frozen", False):
            executable_dir = Path(sys.executable).resolve().parent
            candidates.extend(
                [
                    executable_dir / "TheBazaarAssetGenerator.exe",
                    executable_dir.parent
                    / "asset-generator"
                    / "TheBazaarAssetGenerator.exe",
                ]
            )
        candidates.extend(
            [
                PROJECT_ROOT
                / "dist"
                / "asset-generator"
                / "TheBazaarAssetGenerator.exe",
                Path.home()
                / "AppData"
                / "Local"
                / "Programs"
                / "TheBazaarModManager"
                / "TheBazaarAssetGenerator.exe",
            ]
        )
        for candidate in candidates:
            if candidate.is_file():
                return [str(candidate)]
        source = PROJECT_ROOT / "tools" / "asset_generator_ui.py"
        if source.is_file() and not getattr(sys, "frozen", False):
            return [sys.executable, str(source)]
        return None

    def _refresh_generator_component(self) -> None:
        command = self._asset_generator_command()
        if command:
            self.generator_status.configure(
                text=f"组件可用：{command[-1]}",
                foreground=COLORS["accent"],
            )
            self.generator_launch_button.configure(state="normal")
        else:
            self.generator_status.configure(
                text="未找到素材包制作器组件，请使用完整安装包修复安装。",
                foreground=COLORS["warning"],
            )
            self.generator_launch_button.configure(state="disabled")

    def _launch_asset_generator(self) -> None:
        command = self._asset_generator_command()
        if not command:
            self._refresh_generator_component()
            messagebox.showerror(
                "组件不可用",
                "未找到素材包制作器，请使用完整安装包修复安装。",
                parent=self.root,
            )
            return
        try:
            subprocess.Popen(
                command,
                cwd=str(Path(command[-1]).resolve().parent),
            )
            self._write_log("已打开素材包制作器组件。")
        except OSError as error:
            self._show_error("无法打开素材包制作器", error)

    def _spine_manager_command(self) -> list[str] | None:
        candidates: list[Path] = []
        if getattr(sys, "frozen", False):
            executable_dir = Path(sys.executable).resolve().parent
            candidates.extend(
                [
                    executable_dir / "TheBazaarSpineManager.exe",
                    executable_dir.parent
                    / "spine-manager"
                    / "TheBazaarSpineManager.exe",
                ]
            )
        candidates.extend(
            [
                PROJECT_ROOT
                / "dist"
                / "spine-manager"
                / "TheBazaarSpineManager.exe",
                Path.home()
                / "AppData"
                / "Local"
                / "Programs"
                / "TheBazaarModManager"
                / "TheBazaarSpineManager.exe",
            ]
        )
        for candidate in candidates:
            if candidate.is_file():
                return [str(candidate)]
        source = PROJECT_ROOT / "tools" / "bazaar_spine_manager_ui.py"
        if source.is_file() and not getattr(sys, "frozen", False):
            return [sys.executable, str(source)]
        return None

    def _refresh_spine_manager_component(self) -> None:
        command = self._spine_manager_command()
        if command:
            self.spine_manager_status.configure(
                text=f"组件可用：{command[-1]}",
                foreground=COLORS["accent"],
            )
            self.spine_manager_launch_button.configure(state="normal")
        else:
            self.spine_manager_status.configure(
                text="未找到 Spine 动画管理器组件，请使用完整安装包修复安装。",
                foreground=COLORS["warning"],
            )
            self.spine_manager_launch_button.configure(state="disabled")

    def _launch_spine_manager(self) -> None:
        command = self._spine_manager_command()
        if not command:
            self._refresh_spine_manager_component()
            messagebox.showerror(
                "组件不可用",
                "未找到 Spine 动画管理器，请使用完整安装包修复安装。",
                parent=self.root,
            )
            return
        try:
            subprocess.Popen(
                command,
                cwd=str(Path(command[-1]).resolve().parent),
            )
            self._write_log("已打开 Spine 动画管理器组件。")
        except OSError as error:
            self._show_error("无法打开 Spine 动画管理器", error)

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
            if os.environ.get("BAZAAR_SKIN_MANAGER_STUDIO_DEBUG") == "1":
                Path(
                    tempfile.gettempdir(),
                    "bazaar-manager-smoke-deploy.log",
                ).write_text(traceback.format_exc(), encoding="utf-8")
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
