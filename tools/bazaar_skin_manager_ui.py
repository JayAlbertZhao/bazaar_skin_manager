#!/usr/bin/env python3
"""Desktop UI for authoring and deploying The Bazaar skin packs."""

from __future__ import annotations

import json
import os
import sys
import threading
import traceback
from pathlib import Path
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, ttk

from PIL import Image, ImageGrab, ImageTk

from mod_studio_core import (
    PREVIEW_SIZE,
    PROJECT_ROOT,
    SUPPORTED_AUDIO_EXTENSIONS,
    SUPPORTED_IMAGE_EXTENSIONS,
    WORKSPACES_ROOT,
    StudioWorkspace,
    catalog,
    compose_image_preview,
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


class ModManagerStudio:
    def __init__(self) -> None:
        self.root = RootClass()
        self.root.title("The Bazaar Skin Manager")
        self.root.geometry("1440x900")
        self.root.minsize(1180, 720)
        self.root.configure(bg=COLORS["window"])
        self.catalog = catalog()
        self.first_run = False
        self.game_dir_override: Path | None = None
        self.workspace = self._open_last_or_default()
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
            font=("Segoe UI", 10),
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
            font=("Segoe UI Semibold", 19),
        )
        style.configure(
            "Accent.TButton",
            background=COLORS["accent"],
            foreground="#0d1c19",
            borderwidth=0,
            padding=(18, 10),
            font=("Segoe UI Semibold", 10),
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
            text="The Bazaar Skin Manager",
            style="Title.TLabel",
        ).pack(side="left")
        self.install_status = ttk.Label(
            header,
            text="Checking deployment…",
            style="Muted.TLabel",
        )
        self.install_status.pack(side="right", padx=(12, 0))
        ttk.Button(
            header,
            text="Refresh status",
            command=self._refresh_deployment_status,
        ).pack(side="right")
        ttk.Button(
            header,
            text="Restore original",
            style="Danger.TButton",
            command=self._undeploy,
        ).pack(side="right", padx=(8, 4))
        self.play_button = ttk.Button(
            header,
            text="START GAME",
            style="Accent.TButton",
            command=self._launch_game,
        )
        self.play_button.pack(side="right", padx=(8, 4))
        self.header_deploy_button = ttk.Button(
            header,
            text="DEPLOY",
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
            text="DEPLOY",
            style="Accent.TButton",
            command=self._deploy,
        )
        self.deploy_button.pack(fill="x", pady=(0, 8))
        ttk.Button(
            actions,
            text="Undeploy / restore original",
            style="Danger.TButton",
            command=self._undeploy,
        ).pack(fill="x")

        ttk.Label(
            parent,
            text="Target",
            font=("Segoe UI Semibold", 13),
        ).pack(anchor="w")
        ttk.Label(
            parent,
            text="Choose a hero, then one of its known skins.",
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
        self.skin_combo.bind("<<ComboboxSelected>>", lambda _event: self._metadata_changed())
        self.hero_support = ttk.Label(
            parent,
            text="",
            style="Muted.TLabel",
            wraplength=275,
        )
        self.hero_support.pack(anchor="w", pady=(8, 18))

        self.game_status = ttk.Label(
            parent,
            text="Searching for The Bazaar...",
            style="Muted.TLabel",
            wraplength=275,
        )
        self.game_status.pack(anchor="w", pady=(0, 8))
        ttk.Button(
            parent,
            text="Rescan game location",
            command=self._refresh_deployment_status,
        ).pack(anchor="w")
        ttk.Button(
            parent,
            text="Locate game manually...",
            command=self._locate_game,
        ).pack(anchor="w", pady=(4, 14))

        ttk.Separator(parent).pack(fill="x", pady=(0, 16))
        ttk.Label(
            parent,
            text="Package",
            font=("Segoe UI Semibold", 13),
        ).pack(anchor="w")

        self.pack_id_var = tk.StringVar()
        self.pack_name_var = tk.StringVar()
        self.pack_version_var = tk.StringVar()
        for label, variable in (
            ("Pack id", self.pack_id_var),
            ("Display name", self.pack_name_var),
            ("Version", self.pack_version_var),
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
                "DROP COMPLETE PACK OR ASSET ZIP HERE\n"
                "or click to browse"
            ),
            bg=COLORS["empty"],
            fg=COLORS["muted"],
            activebackground=COLORS["panel_alt"],
            activeforeground=COLORS["text"],
            height=6,
            cursor="hand2",
            relief="flat",
            font=("Segoe UI Semibold", 9),
        )
        self.drop_zone.pack(fill="x")
        self.drop_zone.bind("<Button-1>", lambda _event: self._browse_package())
        self._register_drop(self.drop_zone, self._drop_package)
        if not DND_AVAILABLE:
            ttk.Label(
                parent,
                text="This source run uses click-to-browse. The release build bundles native drag-and-drop.",
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
        notebook.add(self.visual_tab, text="Visual slots")
        notebook.add(self.audio_tab, text="Audio")
        notebook.add(self.animation_tab, text="Skeleton / animation")
        notebook.add(self.package_tab, text="Package & log")
        self._build_visual_tab()
        self._build_audio_tab()
        self._build_animation_tab()
        self._build_package_tab()

    def _build_visual_tab(self) -> None:
        controls = ttk.Frame(self.visual_tab, style="Alt.TFrame")
        controls.pack(fill="x", pady=(0, 10))
        ttk.Label(
            controls,
            text="Unfilled slots use the original game asset.",
            style="Alt.TLabel",
            font=("Segoe UI Semibold", 11),
        ).pack(side="left")
        self.chroma_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            controls,
            text="Remove colour screen on import",
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
            text="Tolerance",
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
            text="ORIGINAL",
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
            font=("Segoe UI Semibold", 11),
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
            text="Original fallback",
            bg=COLORS["panel"],
            fg=COLORS["muted"],
        )
        status.pack(anchor="w")
        buttons = tk.Frame(text, bg=COLORS["panel"])
        buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(
            buttons,
            text="Import",
            command=lambda item=slot["id"]: self._browse_visual(item),
        ).pack(side="left")
        ttk.Button(
            buttons,
            text="Paste",
            command=lambda item=slot["id"]: self._paste_visual(item),
        ).pack(side="left", padx=5)
        ttk.Button(
            buttons,
            text="Clear",
            command=lambda item=slot["id"]: self._clear_visual(item),
        ).pack(side="left")
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
            text="Import one line at a time, or drop a named audio/package ZIP.",
            style="Alt.TLabel",
            font=("Segoe UI Semibold", 11),
        ).pack(side="left")
        ttk.Button(
            top,
            text="Import audio ZIP",
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
        self.audio_tree.heading("category", text="Category")
        self.audio_tree.heading("slot", text="Logical slot")
        self.audio_tree.heading("variants", text="Files")
        self.audio_tree.heading("status", text="Status")
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
            text="Add file to selected line",
            command=self._browse_audio_line,
        ).pack(side="left")
        ttk.Button(
            controls,
            text="Clear selected line",
            command=self._clear_audio_line,
        ).pack(side="left", padx=6)
        ttk.Label(
            controls,
            text="Non-WAV input is automatically converted with ffmpeg when available.",
            style="Alt.TLabel",
        ).pack(side="right")

    def _build_animation_tab(self) -> None:
        ttk.Label(
            self.animation_tab,
            text="Skeleton / animation authoring sources",
            style="Alt.TLabel",
            font=("Segoe UI Semibold", 13),
        ).pack(anchor="w")
        ttk.Label(
            self.animation_tab,
            text=(
                "The package format can carry Spine source sets or Unity "
                "AssetBundles. The current 0.4.x runtime deploys the static "
                "fallback; dynamic playback remains gated until the prefab "
                "adapter is verified. This prevents a source bundle from being "
                "silently advertised as working animation."
            ),
            style="Alt.TLabel",
            wraplength=850,
            justify="left",
        ).pack(anchor="w", pady=(6, 18))
        self.animation_mode = tk.StringVar(value="spine_source")
        ttk.Radiobutton(
            self.animation_tab,
            text="Spine source set (.skel/.json + .atlas + textures)",
            variable=self.animation_mode,
            value="spine_source",
        ).pack(anchor="w")
        ttk.Radiobutton(
            self.animation_tab,
            text="Unity AssetBundle prefab (.bundle/.assetbundle)",
            variable=self.animation_mode,
            value="unity_asset_bundle",
        ).pack(anchor="w", pady=(5, 12))
        self.animation_drop = tk.Label(
            self.animation_tab,
            text="DROP ANIMATION FILES HERE\nor click to browse",
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
            text="No animation source in this package.",
            style="Alt.TLabel",
        )
        self.animation_status.pack(anchor="w", pady=(12, 0))
        ttk.Button(
            self.animation_tab,
            text="Clear animation source",
            command=self._clear_animation,
        ).pack(anchor="w", pady=(8, 0))

    def _build_package_tab(self) -> None:
        actions = ttk.Frame(self.package_tab, style="Alt.TFrame")
        actions.pack(fill="x")
        ttk.Button(
            actions,
            text="New workspace",
            command=self._new_workspace,
        ).pack(side="left")
        ttk.Button(
            actions,
            text="Open workspace",
            command=self._open_workspace,
        ).pack(side="left", padx=5)
        ttk.Button(
            actions,
            text="Export complete ZIP",
            command=self._export_zip,
        ).pack(side="left")
        ttk.Button(
            actions,
            text="Open workspace folder",
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
        return f"{skin['display_name']}  ·  {skin['id']}"

    def _set_skin_options(self, hero: dict, selected_id: str | None = None) -> None:
        labels = [self._skin_label(skin) for skin in hero["skins"]]
        self.skin_combo.configure(values=labels)
        selected = next(
            (skin for skin in hero["skins"] if skin["id"] == selected_id),
            hero["skins"][0],
        )
        self.skin_var.set(self._skin_label(selected))
        if hero.get("runtime_supported"):
            self.hero_support.configure(
                text="Verified runtime adapter available.",
                foreground=COLORS["accent"],
            )
            self.deploy_button.configure(state="normal")
            self.header_deploy_button.configure(state="normal")
        else:
            self.hero_support.configure(
                text="Catalog entry only; runtime adapter not verified in this release.",
                foreground=COLORS["warning"],
            )
            self.deploy_button.configure(state="disabled")
            self.header_deploy_button.configure(state="disabled")

    def _hero_changed(self, _event=None) -> None:
        self._set_skin_options(self._selected_hero())
        self._metadata_changed()
        self._refresh_audio()

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
            self._write_log(f"Metadata not saved: {error}")

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
                preview.configure(image="", text="ORIGINAL")
                status.configure(
                    text="Original fallback",
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
                preview.configure(image="", text="INVALID")
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
                    route["category"],
                    slot,
                    count,
                    "✓ Ready" if count else "— Original",
                ),
                tags=("filled" if count else "original",),
            )
        if filled:
            self.audio_summary.configure(
                text=f"✓ {filled}/{len(routes)} audio routes filled",
                foreground=COLORS["accent"],
            )
        else:
            self.audio_summary.configure(
                text=f"0/{len(routes)} · original audio",
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
                    f"{len(files)} source file(s) included · "
                    "static fallback remains active in runtime 0.4.x"
                ),
                foreground=COLORS["warning"],
            )
        else:
            self.animation_status.configure(
                text="No animation source in this package.",
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
                f"Workspace: {self.workspace.directory}\n"
                f"Filled: {visuals}/{len(self.catalog['visual_slots'])} visual "
                f"slots · {routes} audio routes · {animation} animation files. "
                "Everything else falls back to the original game."
            )
        )

    def _refresh_deployment_status(self) -> None:
        try:
            status = self.workspace.diagnostics()
            if status.get("healthy"):
                text = "Deployed · healthy"
                color = COLORS["accent"]
            elif status.get("installed"):
                text = f"Deployed · {status.get('state', 'needs attention')}"
                color = COLORS["warning"]
            else:
                text = "Original / not deployed"
                color = COLORS["muted"]
            self.install_status.configure(text=text, foreground=color)
        except Exception as error:
            self.install_status.configure(
                text=f"Status error: {error}",
                foreground=COLORS["danger"],
            )
        game = self.workspace.detected_game(self.game_dir_override)
        if game:
            build = f" · Steam build {game.build_id}" if game.build_id else ""
            self.game_status.configure(
                text=f"Game found: {game.game_dir}{build}",
                foreground=COLORS["accent"],
            )
            if not self.busy:
                self.play_button.configure(state="normal")
        else:
            self.game_status.configure(
                text="The Bazaar was not found in common Steam locations.",
                foreground=COLORS["warning"],
            )
            self.play_button.configure(state="disabled")

    def _locate_game(self) -> None:
        selected = filedialog.askdirectory(
            parent=self.root,
            title="Select the folder containing TheBazaar.exe",
            initialdir=self.game_dir_override or Path.home(),
        )
        if not selected:
            return
        game = self.workspace.detected_game(Path(selected))
        if not game:
            messagebox.showerror(
                "Invalid game folder",
                "Select the folder containing TheBazaar.exe and TheBazaar_Data.",
                parent=self.root,
            )
            return
        self.game_dir_override = game.game_dir
        self._remember_workspace()
        self._write_log(f"Using manually selected game: {game.game_dir}")
        self._refresh_deployment_status()

    def _show_first_run_help(self) -> None:
        game = self.workspace.detected_game(self.game_dir_override)
        game_line = (
            f"The Bazaar was found at:\n{game.game_dir}\n\n"
            if game
            else (
                "The Bazaar was not found. Install it through Steam, then "
                "use Rescan or Locate game manually.\n\n"
            )
        )
        messagebox.showinfo(
            "Welcome",
            (
                game_line
                + "1. Obtain a compatible asset-pack ZIP.\n"
                "2. Drag the ZIP into the package area.\n"
                "3. Close the game and press DEPLOY.\n"
                "4. Press START GAME to launch through Steam."
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
            title=f"Import {slot}",
            filetypes=[
                ("Images", "*.png *.jpg *.jpeg *.webp *.bmp"),
                ("All files", "*.*"),
            ],
        )
        if path:
            self._import_visual(slot, Path(path))

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
            self._write_log(f"Imported visual {slot}: {destination}")
            self._refresh_visuals()
            self._refresh_summary()
        except Exception as error:
            self._show_error("Visual import failed", error)

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
                raise ValueError("The clipboard contains no image or image file.")
            self._write_log(f"Pasted visual {slot}: {destination}")
            self._refresh_visuals()
            self._refresh_summary()
        except Exception as error:
            self._show_error("Clipboard import failed", error)

    def _clear_visual(self, slot: str) -> None:
        self.workspace.clear_visual(slot)
        self._write_log(f"Cleared visual {slot}; original fallback restored.")
        self._refresh_visuals()
        self._refresh_summary()

    def _selected_audio_slot(self) -> str | None:
        selection = self.audio_tree.selection()
        return selection[0] if selection else None

    def _browse_audio_line(self) -> None:
        slot = self._selected_audio_slot()
        if not slot:
            messagebox.showinfo(
                "Select a line",
                "Select an audio logical slot first.",
                parent=self.root,
            )
            return
        path = filedialog.askopenfilename(
            parent=self.root,
            title=f"Add audio to {slot}",
            filetypes=[
                (
                    "Audio",
                    "*.wav *.mp3 *.flac *.ogg *.m4a *.aac *.opus",
                ),
                ("All files", "*.*"),
            ],
        )
        if path:
            self._import_audio_line(slot, Path(path))

    def _import_audio_line(self, slot: str, path: Path) -> None:
        try:
            destination = self.workspace.import_audio(slot, path)
            self._write_log(f"Imported audio {slot}: {destination}")
            self._refresh_audio()
            self._refresh_summary()
        except Exception as error:
            self._show_error("Audio import failed", error)

    def _drop_audio_files(self, paths: list[Path]) -> None:
        if len(paths) == 1 and paths[0].suffix.casefold() == ".zip":
            self._import_package(paths[0])
            return
        slot = self._selected_audio_slot()
        if not slot:
            self._show_error(
                "Audio import failed",
                ValueError("Select a logical slot before dropping audio files."),
            )
            return
        for path in paths:
            self._import_audio_line(slot, path)

    def _clear_audio_line(self) -> None:
        slot = self._selected_audio_slot()
        if not slot:
            return
        self.workspace.clear_audio_route(slot)
        self._write_log(f"Cleared audio {slot}; original fallback restored.")
        self._refresh_audio()
        self._refresh_summary()

    def _browse_audio_package(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Import audio or complete asset ZIP",
            filetypes=[("ZIP packages", "*.zip"), ("All files", "*.*")],
        )
        if path:
            self._import_package(Path(path))

    def _browse_package(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root,
            title="Import complete asset package",
            filetypes=[("ZIP packages", "*.zip"), ("All files", "*.*")],
        )
        if path:
            self._import_package(Path(path))

    def _drop_package(self, paths: list[Path]) -> None:
        if paths:
            self._import_package(paths[0])

    def _import_package(self, path: Path) -> None:
        try:
            if path.suffix.casefold() != ".zip":
                raise ValueError("The package entry accepts ZIP files.")
            summary = self.workspace.import_zip(path)
            self._write_log(
                f"Imported {summary.kind}: "
                f"{len(summary.visual_slots)} visual slots, "
                f"{len(summary.audio_routes)} audio routes, "
                f"{len(summary.animation_files)} animation files; "
                f"{len(summary.ignored)} ignored."
            )
            self._load_workspace_into_ui()
            self._refresh_all()
        except Exception as error:
            self._show_error("Package import failed", error)

    def _browse_animation(self) -> None:
        paths = filedialog.askopenfilenames(
            parent=self.root,
            title="Import animation sources",
            filetypes=[
                (
                    "Animation sources",
                    "*.skel *.json *.atlas *.png *.bundle *.assetbundle",
                ),
                ("All files", "*.*"),
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
            self._write_log(f"Imported {len(accepted)} animation source files.")
            self._refresh_animation()
            self._refresh_summary()
        except Exception as error:
            self._show_error("Animation import failed", error)

    def _clear_animation(self) -> None:
        self.workspace.clear_animation()
        self._write_log("Cleared animation authoring sources.")
        self._refresh_animation()
        self._refresh_summary()

    def _new_workspace(self) -> None:
        pack_id = self._prompt_text(
            "New workspace",
            "Pack id:",
            "local.custom.skin",
        )
        if not pack_id:
            return
        try:
            self.workspace = StudioWorkspace.create(pack_id)
            self._load_workspace_into_ui()
            self._refresh_all()
            self._write_log(f"Created workspace {self.workspace.directory}")
        except Exception as error:
            self._show_error("Could not create workspace", error)

    def _open_workspace(self) -> None:
        path = filedialog.askdirectory(
            parent=self.root,
            title="Open Skin Manager workspace",
            initialdir=WORKSPACES_ROOT,
        )
        if not path:
            return
        try:
            self.workspace = StudioWorkspace.load(Path(path))
            self._load_workspace_into_ui()
            self._refresh_all()
            self._write_log(f"Opened workspace {self.workspace.directory}")
        except Exception as error:
            self._show_error("Could not open workspace", error)

    def _export_zip(self) -> None:
        self._metadata_changed()
        default = (
            f"{self.workspace.state['pack']['id']}-"
            f"{self.workspace.state['pack']['version']}.zip"
        )
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Export complete asset package",
            defaultextension=".zip",
            initialfile=default,
            filetypes=[("ZIP package", "*.zip")],
        )
        if not path:
            return
        try:
            destination = self.workspace.export_zip(Path(path))
            self._write_log(f"Exported complete package: {destination}")
        except Exception as error:
            self._show_error("Package export failed", error)

    def _open_workspace_folder(self) -> None:
        os.startfile(self.workspace.directory)

    def _deploy(self) -> None:
        if self.busy:
            return
        self._metadata_changed()
        if messagebox.askyesno(
            "Deploy skin",
            (
                "Deploy this workspace and replace the currently managed skin?\n\n"
                "Close The Bazaar before continuing. Unfilled slots will use "
                "the original game assets."
            ),
            parent=self.root,
        ):
            self._run_background(
                "Deploying…",
                lambda: self.workspace.deploy(self.game_dir_override),
                lambda result: self._operation_complete(
                    "Deploy complete",
                    f"Installed {result['pack']['id']} {result['pack']['version']}.",
                ),
            )

    def _undeploy(self) -> None:
        if self.busy:
            return
        if messagebox.askyesno(
            "Restore original assets",
            "Remove the managed runtime and pack and restore original assets?",
            parent=self.root,
        ):
            self._run_background(
                "Undeploying…",
                self.workspace.undeploy,
                lambda removed: self._operation_complete(
                    "Undeploy complete",
                    f"Removed {len(removed)} managed path(s).",
                ),
            )

    def _launch_game(self) -> None:
        if self.busy:
            return
        self._run_background(
            "Starting The Bazaar through Steam...",
            lambda: self.workspace.launch_game(self.game_dir_override),
            self._launch_complete,
        )

    def _launch_complete(self, result: dict) -> None:
        self.busy = False
        self._set_skin_options(self._selected_hero(), self._selected_skin()["id"])
        self._write_log(
            f"Started The Bazaar through Steam ({result['method']}): "
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
        self._show_error("Operation failed", error)
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

        ttk.Button(window, text="Create", command=accept).pack(
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
        self._write_log(f"ERROR: {error}")
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
    ModManagerStudio().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
