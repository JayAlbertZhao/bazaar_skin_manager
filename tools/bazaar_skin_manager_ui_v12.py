#!/usr/bin/env python3
"""Skin Manager 1.2 interaction shell.

This module deliberately keeps deployment in ``StudioWorkspace`` and adds a
new UI/data layer around it.  The 1.1 editor remains available as migration
code, but is no longer the primary navigation model.
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import struct
import tempfile
import threading
import traceback
import zipfile
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageGrab, ImageTk

from asset_generator_ui import AssetGeneratorUI, PREVIEW_SLOTS
from manual_slot_editor import ManualSlotEditor
from bazaar_skin_manager import (
    MANAGER_VERSION,
    atomic_copy_tree,
    bepinex_status,
    detect_installs,
    find_steam_executable,
    installation_diagnostics,
    launch_game,
    manager_root,
    preferred_game_install,
    uninstall,
)
from mod_studio_core import (
    PROJECT_ROOT,
    SUPPORTED_IMAGE_EXTENSIONS,
    WORKSPACES_ROOT,
    StudioWorkspace,
    compose_image_preview,
    discovered_catalog,
    materialized_pack_id,
)
from skin_library_core import AssetLibrary, classify_file
from spine_manager_core import import_spine_package, targets as spine_targets
from spine_static_preview import (
    HERO_ROOT_X,
    HERO_ROOT_Y,
    READY_CENTER_X,
    READY_CENTER_Y,
    READY_HEIGHT,
    READY_WIDTH,
    REFERENCE_HEIGHT,
    REFERENCE_WIDTH,
    calculate_preview_metrics,
    render_setup_pose,
)
from support_report import (
    append_error_log,
    build_diagnostic_report,
)
from update_service import (
    download_release_installer,
    fetch_latest_release,
    launch_verified_installer,
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

TYPE_NAMES = {
    "character_source": "人物原图",
    "background": "背景",
    "small_icon": "小图标",
    "icon_source": "图标生成原图",
    "derived_image": "派生图像",
    "other_image": "其他图像",
    "audio": "音频",
    "spine": "Spine 动画",
    "other": "其他",
}


def copy_image_to_clipboard(path: Path) -> None:
    """Copy an image as a top-down CF_DIBV5 while preserving alpha."""
    with Image.open(path) as loaded:
        image = loaded.convert("RGBA")
    header = bytearray(124)
    pixel_bytes = image.tobytes("raw", "BGRA")
    struct.pack_into(
        "<IiiHHIIiiII",
        header,
        0,
        124,
        image.width,
        -image.height,
        1,
        32,
        3,  # BI_BITFIELDS
        len(pixel_bytes),
        0,
        0,
        0,
        0,
    )
    struct.pack_into(
        "<IIII",
        header,
        40,
        0x00FF0000,
        0x0000FF00,
        0x000000FF,
        0xFF000000,
    )
    struct.pack_into("<I", header, 56, 0x73524742)  # LCS_sRGB
    struct.pack_into("<I", header, 108, 4)  # LCS_GM_IMAGES
    dib = bytes(header) + pixel_bytes
    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32
    kernel32.GlobalAlloc.argtypes = (ctypes.c_uint, ctypes.c_size_t)
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = (ctypes.c_void_p,)
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = (ctypes.c_void_p,)
    kernel32.GlobalFree.argtypes = (ctypes.c_void_p,)
    user32.SetClipboardData.argtypes = (ctypes.c_uint, ctypes.c_void_p)
    user32.SetClipboardData.restype = ctypes.c_void_p
    handle = kernel32.GlobalAlloc(0x0002, len(dib))
    if not handle:
        raise OSError("无法分配剪贴板内存。")
    pointer = kernel32.GlobalLock(handle)
    if not pointer:
        kernel32.GlobalFree(handle)
        raise OSError("无法锁定剪贴板内存。")
    ctypes.memmove(pointer, dib, len(dib))
    kernel32.GlobalUnlock(handle)
    if not user32.OpenClipboard(None):
        kernel32.GlobalFree(handle)
        raise OSError("剪贴板正被其他程序占用。")
    transferred = False
    try:
        if not user32.EmptyClipboard():
            raise OSError("无法清空剪贴板。")
        if not user32.SetClipboardData(17, handle):  # CF_DIBV5
            raise OSError("无法写入图像剪贴板。")
        transferred = True
    finally:
        user32.CloseClipboard()
        if not transferred:
            kernel32.GlobalFree(handle)


def ellipsize(value: object, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)] + "…"


def _read_complete_pack_identity(archive: Path) -> tuple[str, str, str]:
    with zipfile.ZipFile(archive) as package:
        manifests = [
            name
            for name in package.namelist()
            if Path(name.replace("\\", "/")).name.casefold() == "mod.json"
        ]
        if len(manifests) != 1:
            raise ValueError("皮肤包 ZIP 必须且只能包含一个 mod.json。")
        payload = json.loads(package.read(manifests[0]).decode("utf-8-sig"))
    pack_id = str(payload.get("id") or "").strip()
    if not pack_id:
        raise ValueError("皮肤包 mod.json 缺少 id。")
    return (
        pack_id,
        str(payload.get("name") or pack_id).strip(),
        str(payload.get("version") or "0.1.0").strip(),
    )


EMBEDDED_LIBRARY_INDEX = "authoring/library-assets/index.json"


def export_pack_with_library_assets(
    workspace: StudioWorkspace,
    destination: Path,
    library: AssetLibrary,
) -> Path:
    """Export a runnable pack plus the reusable first-class assets it cites."""
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    references = workspace.state.get("library_assets") or {}
    referenced_ids: set[str] = set()
    for group in ("inputs", "visual_slots", "audio"):
        referenced_ids.update(str(value) for value in (references.get(group) or {}).values())
    if references.get("animation"):
        referenced_ids.add(str(references["animation"]))

    records: list[dict] = []
    with tempfile.TemporaryDirectory() as temporary:
        base_archive = Path(temporary) / "base.zip"
        workspace.export_zip(base_archive)
        staged = destination.with_name(destination.name + ".staging")
        if staged.exists():
            staged.unlink()
        with zipfile.ZipFile(base_archive) as source, zipfile.ZipFile(
            staged,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as output:
            for info in source.infolist():
                output.writestr(info, source.read(info.filename))
            for asset_id in sorted(referenced_ids):
                record = library.assets.get(asset_id)
                if not record:
                    raise ValueError(f"皮肤包引用的一级素材不存在：{asset_id}")
                portable = {
                    key: value
                    for key, value in record.items()
                    if key not in {"source", "files"}
                }
                portable["original_id"] = asset_id
                portable["files"] = []
                for source_file in library.record_files(record):
                    name = source_file.name
                    relative = f"authoring/library-assets/{asset_id}/{name}"
                    output.write(source_file, relative)
                    portable["files"].append(relative)
                records.append(portable)
            index = {
                "schema_version": 1,
                "references": references,
                "assets": records,
            }
            output.writestr(
                EMBEDDED_LIBRARY_INDEX,
                json.dumps(index, ensure_ascii=False, indent=2).encode("utf-8"),
            )
        os.replace(staged, destination)
    return destination


def import_embedded_library_assets(
    workspace: StudioWorkspace,
    library: AssetLibrary,
) -> int:
    """Restore first-class assets embedded by ``export_pack_with_library_assets``."""
    index_path = workspace.directory / EMBEDDED_LIBRARY_INDEX
    if not index_path.is_file():
        return 0
    payload = json.loads(index_path.read_text(encoding="utf-8-sig"))
    if int(payload.get("schema_version") or 0) != 1:
        raise ValueError("不支持的一级素材随包索引版本。")
    id_map: dict[str, str] = {}
    for record in payload.get("assets") or []:
        original_id = str(record.get("original_id") or record.get("id") or "")
        files: list[Path] = []
        for relative in record.get("files") or []:
            candidate = (workspace.directory / str(relative)).resolve()
            try:
                candidate.relative_to(workspace.directory.resolve())
            except ValueError as error:
                raise ValueError(f"一级素材包含不安全路径：{relative}") from error
            if not candidate.is_file():
                raise ValueError(f"一级素材文件缺失：{relative}")
            files.append(candidate)
        if not files:
            continue
        imported = library.import_files(
            files,
            asset_type=str(record.get("type") or "other"),
            name=str(record.get("name") or original_id or "导入素材"),
            metadata=dict(record.get("metadata") or {}),
            source=f"皮肤包：{(workspace.state.get('pack') or {}).get('id')}",
        )
        id_map[original_id] = imported["id"]

    raw_references = payload.get("references") or {}
    references = {"inputs": {}, "visual_slots": {}, "audio": {}, "animation": None}
    for group in ("inputs", "visual_slots", "audio"):
        references[group] = {
            str(key): id_map.get(str(value), str(value))
            for key, value in (raw_references.get(group) or {}).items()
            if id_map.get(str(value), str(value)) in library.assets
        }
    animation = raw_references.get("animation")
    if animation:
        mapped = id_map.get(str(animation), str(animation))
        references["animation"] = mapped if mapped in library.assets else None
    workspace.state["library_assets"] = references
    workspace.save()
    return len(id_map)


class SkinManagerV12:
    """Five-page 1.2 manager UI backed by the proven deployment core."""

    def __init__(self) -> None:
        self.root = RootClass()
        self.root.title(f"The Bazaar 皮肤管理器 v{MANAGER_VERSION}")
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        width = min(1500, max(1024, screen_width - 70))
        height = min(940, max(640, screen_height - 120))
        self.root.geometry(
            f"{width}x{height}+{max(0, (screen_width-width)//2)}+"
            f"{max(0, (screen_height-height-35)//2)}"
        )
        self.root.minsize(1024, 640)
        self.root.configure(bg=COLORS["window"])
        self.settings_path = WORKSPACES_ROOT.parent / "studio-settings.json"
        self.settings = self._load_settings()
        self.game_dir_override = (
            Path(self.settings["game_dir"]).resolve()
            if self.settings.get("game_dir")
            else None
        )
        auto_located = False
        if self.game_dir_override is None or not self.game_dir_override.is_dir():
            complete = [install for install in detect_installs() if install.complete]
            if complete:
                self.game_dir_override = complete[0].game_dir
                auto_located = True
        self.catalog = discovered_catalog(self.game_dir_override)
        self.workspaces: dict[str, StudioWorkspace] = {}
        self.assignments: dict[str, dict] = {}
        self.mapping_models: list[dict] = []
        self.asset_library = AssetLibrary()
        self.photos: dict[str, ImageTk.PhotoImage] = {}
        self.busy = False
        self.selected_pack_path: str | None = None
        self.selected_asset_id: str | None = None
        self.active_creation_workspace: StudioWorkspace | None = None
        self.active_automatic_fingerprint: str | None = None
        self.pending_manual_mode_switch = False
        self.pending_effective_action: str | None = None
        self.creation_mode_guard = False
        self.spine_files: list[Path] = []
        self.latest_release: dict | None = None
        self.update_check_running = False
        self._configure_style()
        self._load_library_state()
        if auto_located:
            self._save_settings()
        self._build_ui()
        self._refresh_everything()
        self.root.after(1500, self._auto_check_for_updates)

    # ---------- persisted state ----------

    def _load_settings(self) -> dict:
        if not self.settings_path.is_file():
            return {"schema_version": 2}
        try:
            payload = json.loads(self.settings_path.read_text(encoding="utf-8-sig"))
            return payload if isinstance(payload, dict) else {"schema_version": 2}
        except (OSError, json.JSONDecodeError):
            return {"schema_version": 2}

    def _load_library_state(self) -> None:
        candidates: set[Path] = set()
        for record in self.settings.get("managed_workspaces") or []:
            try:
                path = Path(record["path"]).resolve()
                if path.is_dir():
                    candidates.add(path)
            except (KeyError, TypeError, OSError):
                continue
        if WORKSPACES_ROOT.is_dir():
            candidates.update(path.parent for path in WORKSPACES_ROOT.glob("*/studio.json"))
        current = self.settings.get("workspace")
        if current and Path(current).is_dir():
            candidates.add(Path(current).resolve())

        for path in sorted(candidates, key=lambda value: str(value).casefold()):
            try:
                workspace = StudioWorkspace.load(path)
                self.workspaces[str(workspace.directory)] = workspace
            except Exception:
                continue
        raw_assignments = self.settings.get("assignments") or {}
        if isinstance(raw_assignments, dict):
            for key, value in raw_assignments.items():
                if not isinstance(value, dict):
                    continue
                pack_path = str(value.get("pack_path") or "")
                if pack_path and str(Path(pack_path).resolve()) in self.workspaces:
                    self.assignments[str(key)] = {
                        "pack_path": str(Path(pack_path).resolve()),
                        "enabled": bool(value.get("enabled", False)),
                    }
        if not self.assignments:
            for key, path in (self.settings.get("target_selections") or {}).items():
                resolved = str(Path(path).resolve())
                if resolved in self.workspaces:
                    self.assignments[str(key)] = {"pack_path": resolved, "enabled": True}
        self.mapping_models = [
            {"target_key": key, **value}
            for key, value in sorted(self.assignments.items())
        ]
        for workspace in self.workspaces.values():
            try:
                self.asset_library.register_workspace(workspace)
            except Exception:
                # One malformed legacy authoring record must not hide the pack.
                continue

    def _save_settings(self) -> None:
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(self.settings)
        payload.update(
            {
                "schema_version": 2,
                "game_dir": str(self.game_dir_override) if self.game_dir_override else None,
                "workspace": next(iter(self.workspaces), None),
                "managed_workspaces": [
                    {
                        "path": path,
                        "enabled": any(
                            row.get("pack_path") == path and row.get("enabled")
                            for row in self.assignments.values()
                        ),
                    }
                    for path in sorted(self.workspaces)
                ],
                "assignments": self.assignments,
                "target_selections": {
                    key: value["pack_path"]
                    for key, value in self.assignments.items()
                    if value.get("pack_path")
                },
            }
        )
        temporary = self.settings_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.settings_path)
        self.settings = payload

    # ---------- global UI ----------

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
        style.configure("TLabel", background=COLORS["panel"], foreground=COLORS["text"])
        style.configure("Muted.TLabel", background=COLORS["panel"], foreground=COLORS["muted"])
        style.configure("Alt.TLabel", background=COLORS["panel_alt"], foreground=COLORS["text"])
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
            padding=(17, 9),
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        style.map("Accent.TButton", background=[("active", "#79e3c3"), ("disabled", "#45635d")])
        style.configure("Danger.TButton", background="#5b292f", foreground="#ffdce0", padding=(13, 8))
        style.configure("TButton", padding=(10, 7))
        style.configure("TEntry", padding=6)
        style.configure("TCombobox", padding=6)
        style.configure("Treeview", rowheight=30, background=COLORS["panel_alt"], fieldbackground=COLORS["panel_alt"])
        style.configure("TNotebook", background=COLORS["window"], borderwidth=0)
        style.configure("TNotebook.Tab", padding=(18, 10))

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, style="Window.TFrame", padding=20)
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer, style="Window.TFrame")
        header.pack(fill="x", pady=(0, 12))
        ttk.Label(
            header,
            text=f"The Bazaar 皮肤管理器 · v{MANAGER_VERSION}",
            style="Title.TLabel",
        ).pack(side="left")
        self.global_status = ttk.Label(header, text="正在扫描游戏…", style="Muted.TLabel")
        self.global_status.pack(side="right", padx=(12, 0))
        self.launch_button = ttk.Button(
            header,
            text="启动游戏",
            style="Accent.TButton",
            command=self._launch_game,
        )
        self.launch_button.pack(side="right", padx=(8, 0))

        self.pages = ttk.Notebook(outer)
        self.pages.pack(fill="both", expand=True)
        self.deployment_page = ttk.Frame(self.pages, padding=16)
        self.management_page = ttk.Frame(self.pages, padding=16)
        self.creation_page = ttk.Frame(self.pages, padding=12)
        self.animation_page = ttk.Frame(self.pages, padding=16)
        self.settings_page = ttk.Frame(self.pages, padding=16)
        for frame, title in (
            (self.deployment_page, "皮肤部署"),
            (self.management_page, "皮肤管理"),
            (self.creation_page, "皮肤制作"),
            (self.animation_page, "动画导入"),
            (self.settings_page, "设置"),
        ):
            self.pages.add(frame, text=title)
        self.pages.bind("<<NotebookTabChanged>>", self._page_changed)
        self._build_deployment_page()
        self._build_management_page()
        self._build_creation_page()
        self._build_animation_page()
        self._build_settings_page()

    def _page_changed(self, _event=None) -> None:
        selected = self.pages.select()
        if selected == str(self.management_page):
            self._refresh_pack_gallery()
            self._refresh_asset_gallery()
        elif selected == str(self.deployment_page):
            self._refresh_deployment_rows()
        elif selected == str(self.settings_page):
            self._refresh_settings_status()

    # ---------- deployment ----------

    def _build_deployment_page(self) -> None:
        page = self.deployment_page
        page.columnconfigure(0, weight=1)
        page.rowconfigure(1, weight=1)
        toolbar = ttk.Frame(page)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(toolbar, text="皮肤部署", font=("Microsoft YaHei UI", 16, "bold")).pack(side="left")
        self.deploy_summary = ttk.Label(toolbar, text="", style="Muted.TLabel")
        self.deploy_summary.pack(side="left", padx=(14, 0))
        self.deploy_button = ttk.Button(toolbar, text="部署更改", style="Accent.TButton", command=self._deploy_all)
        self.deploy_button.pack(side="right")
        ttk.Button(toolbar, text="取消全部部署", style="Danger.TButton", command=self._undeploy_all).pack(side="right", padx=(0, 8))
        ttk.Button(toolbar, text="刷新", command=self._rescan_game).pack(side="right", padx=(0, 8))

        frame = ttk.Frame(page, style="Alt.TFrame")
        frame.grid(row=1, column=0, sticky="nsew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.mapping_canvas = tk.Canvas(frame, bg=COLORS["panel_alt"], highlightthickness=0)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.mapping_canvas.yview)
        self.mapping_canvas.configure(yscrollcommand=scroll.set)
        self.mapping_canvas.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        self.mapping_rows_frame = tk.Frame(self.mapping_canvas, bg=COLORS["panel_alt"])
        self.mapping_window = self.mapping_canvas.create_window((0, 0), window=self.mapping_rows_frame, anchor="nw")
        self.mapping_rows_frame.bind("<Configure>", lambda _e: self.mapping_canvas.configure(scrollregion=self.mapping_canvas.bbox("all")))
        self.mapping_canvas.bind("<Configure>", lambda e: self.mapping_canvas.itemconfigure(self.mapping_window, width=e.width))
        self.mapping_canvas.bind("<MouseWheel>", lambda e: self.mapping_canvas.yview_scroll(int(-e.delta / 120), "units"))

        footer = ttk.Frame(page)
        footer.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        ttk.Button(footer, text="＋ 添加皮肤映射", command=self._add_mapping).pack(side="left")
        ttk.Label(
            footer,
            text="左侧选择自定义皮肤，右侧选择被替换的游戏原皮肤；选择只改变计划，部署更改后才写入游戏。",
            style="Muted.TLabel",
        ).pack(side="left", padx=(12, 0))
        self.mapping_rows: list[dict] = []

    def _target_records(self) -> list[tuple[str, str, dict]]:
        records: list[tuple[str, str, dict]] = []
        for hero in self.catalog.get("heroes") or []:
            for skin in hero.get("skins") or []:
                target = {
                    "game": "the-bazaar",
                    "hero": hero["id"],
                    "skin": skin["id"],
                    "skin_name_contains": skin.get("name_contains") or "",
                }
                key = f"{hero['id']}|{skin['id']}"
                is_default = str(skin["id"]).endswith("01/A")
                default = "（默认皮肤）" if is_default else ""
                support = {
                    "supported": "",
                    "compatible_unverified": " · 自动兼容",
                }.get(skin.get("deployment_status"), " · 通用运行")
                skin_name = "默认皮肤" if is_default else (skin.get("display_name") or skin["id"])
                label = f"{hero.get('display_name') or hero['id']} · {skin_name}{default}{support}"
                records.append((key, label, target | {"deployment_status": skin.get("deployment_status"), "adapter_id": skin.get("adapter_id")}))
        return records

    def _target_groups(self) -> list[dict]:
        """Group dynamic catalog skins by hero for the deployment editor."""
        flat_records = {key: (label, target) for key, label, target in self._target_records()}
        groups: list[dict] = []
        for hero in self.catalog.get("heroes") or []:
            hero_id = str(hero.get("id") or "")
            hero_label = str(hero.get("display_name") or hero_id)
            targets: list[tuple[str, str, dict]] = []
            for skin in hero.get("skins") or []:
                key = f"{hero_id}|{skin['id']}"
                _flat_label, target = flat_records[key]
                is_default = str(skin["id"]).endswith("01/A")
                skin_name = "默认皮肤" if is_default else str(
                    skin.get("display_name") or skin["id"]
                )
                support = {
                    "supported": "",
                    "compatible_unverified": " · 自动兼容",
                }.get(skin.get("deployment_status"), " · 通用运行")
                targets.append((key, f"{skin_name}{support}", target))
            targets.sort(key=lambda item: (not item[0].endswith("01/A"), item[1].casefold()))
            groups.append(
                {
                    "hero_id": hero_id,
                    "label": hero_label,
                    "targets": targets,
                }
            )
        return groups

    def _pack_records(self) -> list[tuple[str, str, StudioWorkspace]]:
        result: list[tuple[str, str, StudioWorkspace]] = []
        for path, workspace in sorted(
            self.workspaces.items(),
            key=lambda item: str((item[1].state.get("pack") or {}).get("name") or "").casefold(),
        ):
            pack = workspace.state.get("pack") or {}
            label = f"{pack.get('name') or pack.get('id')} · v{pack.get('version') or '0.1.0'}"
            result.append((path, label, workspace))
        return result

    def _installed_mapping_records(self) -> dict[str, str]:
        try:
            diagnostics = installation_diagnostics()
        except Exception:
            return {}
        if not diagnostics.get("installed"):
            return {}
        result: dict[str, str] = {}
        for record in ((diagnostics.get("components") or {}).get("packs") or []):
            target = record.get("target") or {}
            hero = str(target.get("hero") or "")
            skin = str(target.get("skin") or "")
            if not hero or not skin:
                continue
            source_pack = record.get("source_pack") or {}
            result[f"{hero}|{skin}"] = str(source_pack.get("id") or record.get("id") or "")
        return result

    def _pack_is_deployed(self, pack_id: str) -> bool:
        return bool(pack_id) and pack_id in set(self._installed_mapping_records().values())

    def _refresh_deployment_rows(self) -> None:
        for child in self.mapping_rows_frame.winfo_children():
            child.destroy()
        self.mapping_rows.clear()
        self.installed_mappings = self._installed_mapping_records()
        models = list(self.mapping_models)
        if not models:
            models = [{"target_key": "", "pack_path": "", "enabled": True}]
        for index, model in enumerate(models):
            self._build_mapping_row(index, model)
        enabled = sum(1 for model in self.mapping_models if model.get("enabled"))
        self.deploy_summary.configure(text=f"计划：{enabled} 条启用映射 · 共 {len(self.mapping_models)} 条")

    def _build_mapping_row(self, index: int, model: dict) -> None:
        pack_records = self._pack_records()
        target_groups = self._target_groups()
        pack_labels = {path: label for path, label, _workspace in pack_records}
        pack_paths = {label: path for path, label, _workspace in pack_records}
        groups = {group["label"]: group for group in target_groups}
        target_key = str(model.get("target_key") or "")
        selected_group = next(
            (
                group
                for group in target_groups
                if any(item[0] == target_key for item in group["targets"])
            ),
            None,
        )
        selected_target = next(
            (
                item
                for item in ((selected_group or {}).get("targets") or [])
                if item[0] == target_key
            ),
            None,
        )

        card = tk.Frame(
            self.mapping_rows_frame,
            bg=COLORS["panel"],
            highlightthickness=1,
            highlightbackground=COLORS["line"],
        )
        card.pack(fill="x", padx=8, pady=7)
        card.grid_columnconfigure(2, weight=1)
        card.grid_columnconfigure(5, weight=1)
        enabled_var = tk.BooleanVar(value=bool(model.get("enabled", True)))
        enabled = ttk.Checkbutton(card, text="启用", variable=enabled_var)
        enabled.grid(row=0, column=0, rowspan=2, padx=(12, 8), pady=12)

        custom_preview = tk.Label(card, bg=COLORS["empty"], width=92, height=92)
        custom_preview.grid(row=0, column=1, rowspan=2, padx=(0, 10), pady=10)
        custom_var = tk.StringVar(value=pack_labels.get(str(model.get("pack_path") or ""), "选择自定义皮肤…"))
        ttk.Label(card, text="自定义皮肤", style="Muted.TLabel").grid(row=0, column=2, sticky="sw", padx=(0, 10))
        custom_combo = ttk.Combobox(card, state="readonly", textvariable=custom_var, values=("选择自定义皮肤…",) + tuple(pack_paths), width=34)
        custom_combo.grid(row=1, column=2, sticky="new", padx=(0, 14), pady=(2, 10))

        ttk.Label(card, text="→", font=("Microsoft YaHei UI", 23, "bold")).grid(row=0, column=3, rowspan=2, padx=10)
        target_preview = tk.Label(card, bg=COLORS["empty"], width=92, height=92)
        target_preview.grid(row=0, column=4, rowspan=2, padx=(8, 10), pady=10)
        target_fields = ttk.Frame(card)
        target_fields.grid(row=0, column=5, rowspan=2, sticky="nsew", pady=(6, 10))
        target_fields.columnconfigure(1, weight=1)
        ttk.Label(target_fields, text="职业", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(target_fields, text="被替换皮肤", style="Muted.TLabel").grid(row=0, column=1, sticky="w", padx=(8, 0))
        hero_var = tk.StringVar(value=(selected_group or {}).get("label", "选择职业…"))
        hero_combo = ttk.Combobox(
            target_fields,
            state="readonly",
            textvariable=hero_var,
            values=("选择职业…",) + tuple(groups),
            width=18,
        )
        hero_combo.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        current_targets = {
            label: (key, target)
            for key, label, target in ((selected_group or {}).get("targets") or [])
        }
        target_var = tk.StringVar(
            value=(selected_target[1] if selected_target else "选择该职业的皮肤…")
        )
        target_combo = ttk.Combobox(
            target_fields,
            state="readonly",
            textvariable=target_var,
            values=("选择该职业的皮肤…",) + tuple(current_targets),
            width=25,
        )
        target_combo.grid(row=1, column=1, sticky="ew", padx=(8, 0), pady=(2, 0))

        status = ttk.Label(card, text="映射未完成", style="Muted.TLabel", width=16, anchor="center")
        status.grid(row=0, column=6, rowspan=2, padx=12)
        ttk.Button(card, text="删除", style="Danger.TButton", command=lambda i=index: self._remove_mapping(i)).grid(row=0, column=7, rowspan=2, padx=(0, 12))
        row = {
            "enabled": enabled_var,
            "pack": custom_var,
            "hero": hero_var,
            "target": target_var,
            "pack_paths": pack_paths,
            "groups": groups,
            "targets": current_targets,
            "target_combo": target_combo,
            "pack_preview": custom_preview,
            "target_preview": target_preview,
            "status": status,
        }
        self.mapping_rows.append(row)
        callback = lambda _e=None, selected=row: self._mapping_changed(selected)
        custom_combo.bind("<<ComboboxSelected>>", callback)
        hero_combo.bind(
            "<<ComboboxSelected>>",
            lambda _e=None, selected=row: self._mapping_hero_changed(selected),
        )
        target_combo.bind("<<ComboboxSelected>>", callback)
        enabled.configure(command=callback)
        self._render_mapping_row(row)

    def _mapping_hero_changed(self, row: dict) -> None:
        group = row["groups"].get(row["hero"].get())
        targets = {
            label: (key, target)
            for key, label, target in ((group or {}).get("targets") or [])
        }
        row["targets"] = targets
        row["target_combo"].configure(
            values=("选择该职业的皮肤…",) + tuple(targets)
        )
        # Each hero starts at its detected default skin. The author can then
        # choose another skin without searching a global list.
        row["target"].set(next(iter(targets), "选择该职业的皮肤…"))
        self._mapping_changed(row)

    @staticmethod
    def _mapping_target_data(row: dict) -> tuple[str, dict] | None:
        return row["targets"].get(row["target"].get())

    def _mapping_changed(self, _row: dict) -> None:
        self._sync_mapping_models()
        for row in self.mapping_rows:
            self._render_mapping_row(row)
        self._save_settings()

    def _sync_mapping_models(self) -> None:
        models: list[dict] = []
        assignments: dict[str, dict] = {}
        for row in self.mapping_rows:
            pack_path = row["pack_paths"].get(row["pack"].get(), "")
            target_data = self._mapping_target_data(row)
            target_key = target_data[0] if target_data else ""
            if not pack_path and not target_key:
                continue
            model = {"pack_path": pack_path, "target_key": target_key, "enabled": bool(row["enabled"].get())}
            models.append(model)
            if pack_path and target_key:
                assignments[target_key] = {"pack_path": pack_path, "enabled": model["enabled"]}
        self.mapping_models = models
        self.assignments = assignments

    def _render_mapping_row(self, row: dict) -> None:
        pack_path = row["pack_paths"].get(row["pack"].get(), "")
        target_data = self._mapping_target_data(row)
        self._set_preview(row["pack_preview"], self._pack_cover(self.workspaces.get(pack_path)), (92, 92), f"map-pack-{id(row)}")
        target = target_data[1] if target_data else None
        self._set_preview(row["target_preview"], self._target_cover(target), (92, 92), f"map-target-{id(row)}")
        if not pack_path or not target_data:
            text, color = "映射未完成", COLORS["muted"]
        elif target and target.get("deployment_status") != "supported":
            text, color = "未适配", COLORS["danger"]
        elif not row["enabled"].get():
            text, color = "未启用", COLORS["muted"]
        else:
            workspace = self.workspaces.get(pack_path)
            pack_id = str(((workspace.state.get("pack") or {}).get("id") if workspace else "") or "")
            installed_pack = self.installed_mappings.get(target_data[0]) if target_data else None
            if installed_pack and installed_pack == pack_id:
                text, color = "已部署", COLORS["accent"]
            else:
                text, color = "待应用", COLORS["warning"]
        row["status"].configure(text=text, foreground=color)

    def _add_mapping(self) -> None:
        self._sync_mapping_models()
        self.mapping_models.append({"target_key": "", "pack_path": "", "enabled": True})
        self._refresh_deployment_rows()

    def _remove_mapping(self, index: int) -> None:
        self._sync_mapping_models()
        if 0 <= index < len(self.mapping_models):
            self.mapping_models.pop(index)
        self.assignments = {
            model["target_key"]: {"pack_path": model["pack_path"], "enabled": model["enabled"]}
            for model in self.mapping_models
            if model.get("target_key") and model.get("pack_path")
        }
        self._save_settings()
        self._refresh_deployment_rows()

    # ---------- management galleries ----------

    def _build_management_page(self) -> None:
        page = self.management_page
        page.columnconfigure(0, weight=1)
        page.rowconfigure(0, weight=1)
        self.management_tabs = ttk.Notebook(page)
        self.management_tabs.grid(row=0, column=0, sticky="nsew")
        self.pack_tab = ttk.Frame(self.management_tabs, padding=12)
        self.asset_tab = ttk.Frame(self.management_tabs, padding=12)
        self.management_tabs.add(self.pack_tab, text="皮肤包管理")
        self.management_tabs.add(self.asset_tab, text="一级素材管理")
        self._build_pack_tab()
        self._build_asset_tab()

    def _gallery(self, parent: ttk.Frame) -> tuple[tk.Canvas, tk.Frame, int]:
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)
        holder = ttk.Frame(parent, style="Alt.TFrame")
        holder.grid(row=1, column=0, sticky="nsew")
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)
        canvas = tk.Canvas(holder, bg=COLORS["panel_alt"], highlightthickness=0)
        scroll = ttk.Scrollbar(holder, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        cards = tk.Frame(canvas, bg=COLORS["panel_alt"])
        window = canvas.create_window((0, 0), window=cards, anchor="nw")
        cards.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window, width=e.width))
        canvas.bind("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))
        return canvas, cards, window

    def _build_pack_tab(self) -> None:
        tab = self.pack_tab
        toolbar = ttk.Frame(tab)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        toolbar.columnconfigure(1, weight=1)
        ttk.Label(toolbar, text="搜索", style="Muted.TLabel").grid(row=0, column=0, padx=(0, 8))
        self.pack_query = tk.StringVar()
        entry = ttk.Entry(toolbar, textvariable=self.pack_query)
        entry.grid(row=0, column=1, sticky="ew")
        self.pack_query.trace_add("write", lambda *_: self._refresh_pack_gallery())
        self.pack_filter = tk.StringVar(value="全部内容")
        filter_box = ttk.Combobox(
            toolbar,
            textvariable=self.pack_filter,
            values=("全部内容", "含图像", "含音频", "含动画", "存在问题"),
            state="readonly",
            width=12,
        )
        filter_box.grid(row=0, column=2, padx=(8, 0))
        filter_box.bind("<<ComboboxSelected>>", lambda _event: self._refresh_pack_gallery())
        ttk.Button(toolbar, text="新建皮肤", command=lambda: self.pages.select(self.creation_page)).grid(row=0, column=3, padx=(8, 0))
        ttk.Button(toolbar, text="导入皮肤包 ZIP…", command=self._import_pack_zip).grid(row=0, column=4, padx=(8, 0))
        self.pack_canvas, self.pack_cards, _ = self._gallery(tab)
        footer = ttk.Frame(tab)
        footer.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        footer.columnconfigure(0, weight=1)
        self.pack_detail = ttk.Label(footer, text="选择一个皮肤包。", style="Muted.TLabel", anchor="w")
        self.pack_detail.grid(row=0, column=0, sticky="ew")
        actions = ttk.Frame(footer)
        actions.grid(row=1, column=0, sticky="ew", pady=(7, 0))
        self.pack_use = ttk.Button(actions, text="用于部署…", command=self._use_pack, state="disabled")
        self.pack_use.pack(side="left")
        self.pack_edit = ttk.Button(actions, text="编辑", command=self._edit_pack, state="disabled")
        self.pack_edit.pack(side="left", padx=(8, 0))
        self.pack_spine = ttk.Button(
            actions,
            text="Spine 调节",
            command=self._edit_pack_spine,
            state="disabled",
        )
        self.pack_spine.pack(side="left", padx=(8, 0))
        self.pack_export = ttk.Button(actions, text="导出 ZIP", command=self._export_pack, state="disabled")
        self.pack_export.pack(side="left", padx=(8, 0))
        self.pack_delete = ttk.Button(actions, text="删除", style="Danger.TButton", command=self._delete_pack, state="disabled")
        self.pack_delete.pack(side="left", padx=(8, 0))

    def _build_asset_tab(self) -> None:
        tab = self.asset_tab
        toolbar = ttk.Frame(tab)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        toolbar.columnconfigure(1, weight=1)
        ttk.Label(toolbar, text="搜索", style="Muted.TLabel").grid(row=0, column=0, padx=(0, 8))
        self.asset_query = tk.StringVar()
        ttk.Entry(toolbar, textvariable=self.asset_query).grid(row=0, column=1, sticky="ew")
        self.asset_query.trace_add("write", lambda *_: self._refresh_asset_gallery())
        self.asset_type = tk.StringVar(value="全部类型")
        type_box = ttk.Combobox(
            toolbar,
            textvariable=self.asset_type,
            state="readonly",
            values=("全部类型",) + tuple(TYPE_NAMES.values()) + ("未引用", "存在问题"),
            width=16,
        )
        type_box.grid(row=0, column=2, padx=(8, 0))
        type_box.bind("<<ComboboxSelected>>", lambda _e: self._refresh_asset_gallery())
        ttk.Button(toolbar, text="导入普通素材…", command=self._import_primary_asset).grid(row=0, column=3, padx=(8, 0))
        ttk.Button(
            toolbar,
            text="打开素材目录",
            command=lambda: os.startfile(self.asset_library.root),
        ).grid(row=0, column=4, padx=(8, 0))
        self.asset_canvas, self.asset_cards, _ = self._gallery(tab)
        footer = ttk.Frame(tab)
        footer.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        footer.columnconfigure(0, weight=1)
        self.asset_detail = ttk.Label(footer, text="选择一个一级素材。", style="Muted.TLabel", anchor="w")
        self.asset_detail.grid(row=0, column=0, sticky="ew")
        actions = ttk.Frame(footer)
        actions.grid(row=1, column=0, sticky="ew", pady=(7, 0))
        self.asset_use = ttk.Button(actions, text="用于皮肤…", command=self._use_asset, state="disabled")
        self.asset_use.pack(side="left")
        self.asset_edit = ttk.Button(actions, text="编辑信息", command=self._edit_asset, state="disabled")
        self.asset_edit.pack(side="left", padx=(8, 0))
        self.asset_copy = ttk.Button(actions, text="复制到剪贴板", command=self._copy_asset, state="disabled")
        self.asset_copy.pack(side="left", padx=(8, 0))
        self.asset_delete = ttk.Button(actions, text="删除", style="Danger.TButton", command=self._delete_asset, state="disabled")
        self.asset_delete.pack(side="left", padx=(8, 0))

    def _refresh_pack_gallery(self) -> None:
        if not hasattr(self, "pack_cards"):
            return
        for child in self.pack_cards.winfo_children():
            child.destroy()
        query = self.pack_query.get().strip().casefold()
        records = []
        for path, _label, workspace in self._pack_records():
            pack = workspace.state.get("pack") or {}
            haystack = " ".join(str(pack.get(key) or "") for key in ("id", "name", "version")).casefold()
            if query and query not in haystack:
                continue
            selected_filter = self.pack_filter.get()
            has_images = bool(workspace.state.get("visual_slots"))
            has_audio = bool((workspace.audio_manifest() or {}).get("routes"))
            has_animation = bool((workspace.state.get("animation") or {}).get("files"))
            refs = workspace.state.get("library_assets") or {}
            referenced_ids = {
                str(value)
                for group in ("inputs", "visual_slots", "audio")
                for value in (refs.get(group) or {}).values()
            }
            if refs.get("animation"):
                referenced_ids.add(str(refs["animation"]))
            has_problem = any(value not in self.asset_library.assets for value in referenced_ids)
            if selected_filter == "含图像" and not has_images:
                continue
            if selected_filter == "含音频" and not has_audio:
                continue
            if selected_filter == "含动画" and not has_animation:
                continue
            if selected_filter == "存在问题" and not has_problem:
                continue
            records.append((path, workspace))
        columns = max(1, min(6, max(1, self.pack_canvas.winfo_width()) // 230))
        for index, (path, workspace) in enumerate(records):
            self._pack_card(path, workspace, index // columns, index % columns)
        for column in range(columns):
            self.pack_cards.grid_columnconfigure(column, weight=1, uniform="pack")
        if not records:
            tk.Label(self.pack_cards, text="还没有皮肤包。可以新建皮肤或导入皮肤包 ZIP。", bg=COLORS["panel_alt"], fg=COLORS["muted"], pady=60).grid(row=0, column=0, sticky="ew")

    def _pack_card(self, path: str, workspace: StudioWorkspace, row: int, column: int) -> None:
        selected = path == self.selected_pack_path
        background = COLORS["accent_dark"] if selected else COLORS["panel"]
        card = tk.Frame(self.pack_cards, bg=background, highlightthickness=2, highlightbackground=COLORS["accent"] if selected else COLORS["line"])
        card.grid(row=row, column=column, sticky="nsew", padx=7, pady=7)
        cover = tk.Label(card, bg=background)
        cover.pack(fill="x", padx=9, pady=(9, 5))
        self._set_preview(cover, self._pack_cover(workspace), (190, 150), f"pack-{path}")
        pack = workspace.state.get("pack") or {}
        name = str(pack.get("name") or pack.get("id") or "未命名皮肤")
        tk.Label(card, text=ellipsize(name, 44), bg=background, fg=COLORS["text"], font=("Microsoft YaHei UI", 10, "bold"), wraplength=190).pack(fill="x", padx=8)
        refs = workspace.state.get("library_assets") or {}
        ref_count = sum(len(refs.get(group) or {}) for group in ("inputs", "visual_slots", "audio")) + (1 if refs.get("animation") else 0)
        tk.Label(card, text=f"v{pack.get('version') or '0.1.0'} · 图像 {len(workspace.state.get('visual_slots') or {})} · 一级素材 {ref_count}", bg=background, fg=COLORS["muted"], font=("Microsoft YaHei UI", 8)).pack(fill="x", padx=8, pady=(3, 8))
        for widget in (card, cover):
            widget.bind("<Button-1>", lambda _e, value=path: self._select_pack(value))
            widget.bind("<Double-Button-1>", lambda _e, value=path: (self._select_pack(value), self._edit_pack()))

    def _select_pack(self, path: str) -> None:
        self.selected_pack_path = path
        workspace = self.workspaces.get(path)
        state = "normal" if workspace else "disabled"
        for button in (self.pack_use, self.pack_edit, self.pack_export, self.pack_delete):
            button.configure(state=state)
        has_spine = bool(
            workspace
            and (workspace.state.get("animation") or {}).get("mode") == "spine"
            and (workspace.state.get("animation") or {}).get("files")
        )
        self.pack_spine.configure(state="normal" if has_spine else "disabled")
        if workspace:
            pack = workspace.state.get("pack") or {}
            text = f"{ellipsize(pack.get('name'), 45)} · {ellipsize(pack.get('id'), 42)} · v{ellipsize(pack.get('version'), 16)} · {ellipsize(path, 70)}"
            self.pack_detail.configure(text=text)
        self._refresh_pack_gallery()

    def _refresh_asset_gallery(self) -> None:
        if not hasattr(self, "asset_cards"):
            return
        for child in self.asset_cards.winfo_children():
            child.destroy()
        workspaces = list(self.workspaces.values())
        refs = self.asset_library.references(workspaces)
        query = self.asset_query.get().strip().casefold()
        selected_type = self.asset_type.get()
        records = []
        for record in self.asset_library.assets.values():
            if query and query not in (str(record.get("name")) + " " + str(record.get("id"))).casefold():
                continue
            record_refs = refs.get(record["id"], [])
            broken = not bool(self.asset_library.record_files(record))
            if selected_type == "未引用" and record_refs:
                continue
            if selected_type == "存在问题" and not broken:
                continue
            if selected_type not in {"全部类型", "未引用", "存在问题"} and TYPE_NAMES.get(record.get("type")) != selected_type:
                continue
            records.append(record)
        records.sort(key=lambda item: (TYPE_NAMES.get(item.get("type"), ""), str(item.get("name")).casefold()))
        columns = max(1, min(6, max(1, self.asset_canvas.winfo_width()) // 220))
        for index, record in enumerate(records):
            self._asset_card(record, refs.get(record["id"], []), index // columns, index % columns)
        for column in range(columns):
            self.asset_cards.grid_columnconfigure(column, weight=1, uniform="asset")
        if not records:
            tk.Label(self.asset_cards, text="还没有符合条件的一级素材。皮肤制作和动画导入会自动把内部素材保存到这里。", bg=COLORS["panel_alt"], fg=COLORS["muted"], pady=60).grid(row=0, column=0, sticky="ew")

    def _asset_card(self, record: dict, refs: list[dict], row: int, column: int) -> None:
        asset_id = record["id"]
        selected = asset_id == self.selected_asset_id
        background = COLORS["accent_dark"] if selected else COLORS["panel"]
        card = tk.Frame(self.asset_cards, bg=background, highlightthickness=2, highlightbackground=COLORS["accent"] if selected else COLORS["line"])
        card.grid(row=row, column=column, sticky="nsew", padx=7, pady=7)
        preview = tk.Label(card, bg=background)
        preview.pack(fill="x", padx=9, pady=(9, 5))
        self._set_asset_preview(preview, record, (180, 128), f"asset-{asset_id}")
        tk.Label(card, text=ellipsize(record.get("name"), 40), bg=background, fg=COLORS["text"], font=("Microsoft YaHei UI", 10, "bold"), wraplength=180).pack(fill="x", padx=8)
        tk.Label(card, text=f"{TYPE_NAMES.get(record.get('type'), record.get('type'))} · 被 {len(refs)} 个皮肤引用", bg=background, fg=COLORS["muted"], font=("Microsoft YaHei UI", 8)).pack(fill="x", padx=8, pady=(3, 8))
        for widget in (card, preview):
            widget.bind("<Button-1>", lambda _e, value=asset_id: self._select_asset(value))

    def _select_asset(self, asset_id: str) -> None:
        self.selected_asset_id = asset_id
        record = self.asset_library.assets.get(asset_id)
        state = "normal" if record else "disabled"
        for button in (self.asset_use, self.asset_edit, self.asset_copy, self.asset_delete):
            button.configure(state=state)
        if record:
            refs = self.asset_library.references(self.workspaces.values()).get(asset_id, [])
            metadata = record.get("metadata") or {}
            size = metadata.get("image_size") or metadata.get("runtime_version") or "—"
            license_text = metadata.get("license") or "未填写许可"
            self.asset_detail.configure(
                text=(
                    f"{ellipsize(record.get('name'), 34)} · {TYPE_NAMES.get(record.get('type'), record.get('type'))} · "
                    f"尺寸/版本 {size} · 引用 {len(refs)} · 许可 {ellipsize(license_text, 22)} · "
                    f"SHA {str(record.get('sha256') or '')[:12]} · {ellipsize(record.get('source'), 42)}"
                )
            )
        self._refresh_asset_gallery()

    # ---------- creation ----------

    def _build_creation_page(self) -> None:
        self.creation_modes = ttk.Notebook(self.creation_page)
        self.creation_modes.pack(fill="both", expand=True)
        automatic_page = ttk.Frame(self.creation_modes)
        manual_page = ttk.Frame(self.creation_modes)
        self.creation_modes.add(automatic_page, text="默认 / 草稿模式")
        self.creation_modes.add(manual_page, text="逐槽位模式")
        self.generator = AssetGeneratorUI(
            automatic_page,
            on_import=self._generator_import_complete,
            on_generated=self._generator_draft_generated,
            on_generation_failed=self._generator_generation_failed,
            on_material_import=self._generator_material_imported,
            on_choose_asset=self._generator_choose_asset,
            on_effective_action=self._generator_effective_action,
        )
        self.manual_slot_editor = ManualSlotEditor(
            manual_page,
            catalog=self.catalog,
            on_import=self._manual_slot_import_complete,
            on_choose_asset=self._manual_slot_choose_asset,
            game_dir_provider=lambda: self.game_dir_override,
        )
        self.creation_modes.bind("<<NotebookTabChanged>>", self._creation_mode_changed)

    def _creation_mode_changed(self, _event: tk.Event | None = None) -> None:
        if getattr(self, "creation_mode_guard", False):
            return
        selected = self.creation_modes.index(self.creation_modes.select())
        if selected == 1:
            self._enter_manual_creation_mode()
        else:
            self._enter_automatic_creation_mode()

    def _select_creation_mode(self, index: int) -> None:
        self.creation_mode_guard = True
        try:
            self.creation_modes.select(index)
        except Exception:
            self.creation_mode_guard = False
            raise
        # ttk queues <<NotebookTabChanged>>. Keep the guard through the next
        # idle turn rather than clearing it before that virtual event arrives.
        self.root.after_idle(
            lambda: setattr(self, "creation_mode_guard", False)
        )

    def _enter_manual_creation_mode(self) -> None:
        """Show manual mode immediately and synchronize its base in background."""

        if getattr(self.generator, "busy", False) is True:
            self.manual_slot_editor.show_background_sync()
            return
        workspace = self.active_creation_workspace
        if not self.generator.has_draft_source():
            # Keep the original standalone per-slot workflow available. An
            # empty default form has nothing to materialize, so the existing
            # manual cache (or its blank new project) is already authoritative.
            if (
                workspace is not None
                and self.manual_slot_editor.editing_workspace is not workspace
            ):
                try:
                    self.manual_slot_editor.edit_workspace(workspace)
                except Exception as error:
                    self._show_error("无法接续逐槽位草稿", error)
            return
        try:
            fingerprint = self.generator.current_draft_fingerprint()
        except Exception as error:
            self._show_error("无法读取默认草稿", error)
            return
        if (
            workspace is not None
            and fingerprint == getattr(self, "active_automatic_fingerprint", None)
        ):
            if self.manual_slot_editor.editing_workspace is not workspace:
                try:
                    self.manual_slot_editor.edit_workspace(workspace)
                except Exception as error:
                    self._show_error("无法接续逐槽位草稿", error)
            return

        self.pending_manual_mode_switch = True
        self.manual_slot_editor.show_background_sync()
        if not self.generator.generate_shared_draft():
            self.pending_manual_mode_switch = False

    def _enter_automatic_creation_mode(self) -> None:
        """Publish manual overrides to the default preview without disk I/O."""

        try:
            images = self.manual_slot_editor.commit_for_mode_switch(
                {slot for _title, slot in PREVIEW_SLOTS}
            )
            self.generator.set_manual_preview_overrides(
                self.manual_slot_editor.current_pack_id(),
                images,
                total_count=self.manual_slot_editor.override_count(),
            )
            self._save_settings()
        except Exception as error:
            self._show_error("无法保存逐槽位草稿", error)

    def _generator_effective_action(self, action: str) -> bool:
        """Route default-mode publish/export through sparse manual overrides."""

        if action not in {"import", "export"}:
            return False
        if not self.manual_slot_editor.has_overrides():
            return False
        pack_id = self.generator.vars["pack_id"].get().strip()
        if pack_id.casefold() != self.manual_slot_editor.current_pack_id().casefold():
            return False
        images = self.manual_slot_editor.commit_for_mode_switch(
            {slot for _title, slot in PREVIEW_SLOTS}
        )
        self.generator.set_manual_preview_overrides(
            pack_id,
            images,
            total_count=self.manual_slot_editor.override_count(),
        )
        try:
            fingerprint = self.generator.current_draft_fingerprint()
        except Exception as error:
            self._show_error("无法读取默认草稿", error)
            return True
        if (
            self.active_creation_workspace is not None
            and fingerprint == self.active_automatic_fingerprint
        ):
            self._dispatch_effective_action(action)
            return True

        self.pending_effective_action = action
        self.pending_manual_mode_switch = True
        self.manual_slot_editor.show_background_sync()
        if not self.generator.generate_shared_draft():
            self.pending_effective_action = None
            self.pending_manual_mode_switch = False
        return True

    def _dispatch_effective_action(self, action: str) -> None:
        if action == "import":
            self.manual_slot_editor.import_to_library()
        elif action == "export":
            self.manual_slot_editor.export_to_zip()

    def _generator_draft_generated(self, profile, result) -> None:
        if not result.generated_workspace:
            return
        workspace = StudioWorkspace.load(Path(result.generated_workspace))
        path = str(workspace.directory)
        self.workspaces[path] = workspace
        self.active_creation_workspace = workspace
        self.active_automatic_fingerprint = self.generator.draft_fingerprint(profile)
        self.selected_pack_path = path
        self._save_settings()
        if self.pending_manual_mode_switch or getattr(self, "pending_effective_action", None):
            self.pending_manual_mode_switch = False
            self.manual_slot_editor.continue_from_automatic_workspace(
                workspace,
                preserve_overrides=True,
            )
            images = self.manual_slot_editor.commit_for_mode_switch(
                {slot for _title, slot in PREVIEW_SLOTS}
            )
            self.generator.set_manual_preview_overrides(
                self.manual_slot_editor.current_pack_id(),
                images,
                total_count=self.manual_slot_editor.override_count(),
            )
        action = getattr(self, "pending_effective_action", None)
        self.pending_effective_action = None
        if action:
            self.root.after_idle(lambda selected=action: self._dispatch_effective_action(selected))

    def _generator_generation_failed(self, _details: str) -> None:
        self.pending_manual_mode_switch = False
        self.pending_effective_action = None

    def _manual_slot_choose_asset(self) -> Path | None:
        records = [
            record
            for record in self.asset_library.assets.values()
            if record.get("type")
            in {
                "character_source",
                "background",
                "small_icon",
                "icon_source",
                "derived_image",
                "other_image",
            }
            and self.asset_library.preview_path(record) is not None
        ]
        if not records:
            messagebox.showinfo(
                "没有可用一级素材",
                "请先导入图像素材，或使用槽位旁的“导入…”按钮。",
                parent=self.root,
            )
            return None
        choice = AssetChoiceDialog(self, self.root, "从一级素材选择", records).show()
        return self.asset_library.preview_path(choice) if choice else None

    def _manual_slot_import_complete(self, workspace: StudioWorkspace) -> None:
        path = str(workspace.directory)
        self.workspaces[path] = workspace
        self.active_creation_workspace = workspace
        self.asset_library.register_workspace(workspace)
        self.selected_pack_path = path
        self._save_settings()
        self.pages.select(self.management_page)
        self.management_tabs.select(self.pack_tab)
        self._refresh_everything()

    def _generator_choose_asset(self, key: str) -> Path | None:
        accepted = {
            "character": {"character_source", "other_image", "derived_image"},
            "background": {"background", "other_image"},
            "small_icon": {"small_icon", "other_image", "derived_image"},
            "small_icon_source": {"icon_source", "character_source", "other_image"},
        }.get(key, {"other_image"})
        records = [
            record
            for record in self.asset_library.assets.values()
            if record.get("type") in accepted
            and self.asset_library.preview_path(record) is not None
        ]
        if not records:
            messagebox.showinfo(
                "没有可用一级素材",
                "请先导入素材，或直接使用当前输入框的浏览/粘贴按钮。",
                parent=self.root,
            )
            return None
        choice = AssetChoiceDialog(self, self.root, "从一级素材选择", records).show()
        return self.asset_library.preview_path(choice) if choice else None

    def _generator_material_imported(self, key: str, path: Path) -> None:
        asset_type = {
            "character": "character_source",
            "background": "background",
            "small_icon": "small_icon",
            "small_icon_source": "icon_source",
        }.get(key, "other_image")
        self.asset_library.import_file(path, asset_type=asset_type, name=path.stem)
        self._refresh_asset_gallery()

    def _copy_asset(self) -> None:
        record = self.asset_library.assets.get(self.selected_asset_id or "")
        if not record:
            return
        files = self.asset_library.record_files(record)
        image_path = self.asset_library.preview_path(record)
        try:
            if image_path is not None:
                copy_image_to_clipboard(image_path)
                self.asset_detail.configure(
                    text=f"已复制图像：{record.get('name') or record.get('id')}"
                )
            elif files:
                self.root.clipboard_clear()
                self.root.clipboard_append(str(files[0]))
                self.root.update()
                self.asset_detail.configure(text=f"已复制素材路径：{files[0]}")
            else:
                raise FileNotFoundError("该一级素材没有可复制的文件。")
        except Exception as error:
            self._show_error("复制失败", error)

    def _generator_import_complete(self, _profile, result) -> None:
        if not result.generated_workspace:
            raise ValueError("生成器没有返回共享草稿工作区。")
        workspace = StudioWorkspace.load(Path(result.generated_workspace))
        path = str(workspace.directory)
        self.workspaces[path] = workspace
        self.active_creation_workspace = workspace
        self.asset_library.register_workspace(workspace)
        self.selected_pack_path = path
        self._save_settings()
        self.pages.select(self.management_page)
        self.management_tabs.select(self.pack_tab)
        self._refresh_everything()

    # ---------- animation import ----------

    def _build_animation_page(self) -> None:
        page = self.animation_page
        page.columnconfigure(1, weight=1)
        page.rowconfigure(1, weight=1)
        ttk.Label(page, text="动画导入", font=("Microsoft YaHei UI", 16, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))
        form = ttk.LabelFrame(page, text="Spine 一级素材", padding=14)
        form.grid(row=1, column=0, sticky="nsw", padx=(0, 12))
        self.spine_name = tk.StringVar(value="新 Spine 动画")
        self.spine_runtime = tk.StringVar(value="4.2")
        self.spine_author = tk.StringVar()
        self.spine_license = tk.StringVar()
        self.spine_target = tk.StringVar(value="未指定（导入后再配置）")
        for row, (label, variable) in enumerate((
            ("名称", self.spine_name),
            ("Spine 版本", self.spine_runtime),
            ("作者", self.spine_author),
            ("许可 / 来源", self.spine_license),
        )):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=5)
            if variable is self.spine_runtime:
                ttk.Combobox(
                    form,
                    textvariable=variable,
                    values=("4.2", "4.1"),
                    state="readonly",
                    width=32,
                ).grid(row=row, column=1, sticky="ew", padx=(10, 0), pady=5)
            else:
                ttk.Entry(form, textvariable=variable, width=34).grid(row=row, column=1, sticky="ew", padx=(10, 0), pady=5)
        ttk.Label(form, text="兼容目标").grid(row=4, column=0, sticky="w", pady=5)
        self.spine_target_box = ttk.Combobox(form, textvariable=self.spine_target, state="readonly", width=34)
        self.spine_target_box.grid(row=4, column=1, sticky="ew", padx=(10, 0), pady=5)
        ttk.Button(form, text="选择 Spine ZIP 或散文件…", command=self._browse_spine_files).grid(row=5, column=0, columnspan=2, sticky="ew", pady=(12, 5))
        ttk.Button(form, text="清空输入", command=self._clear_spine_inputs).grid(row=6, column=0, sticky="ew", pady=5)
        ttk.Button(form, text="导入为一级素材", style="Accent.TButton", command=self._import_spine_asset).grid(row=6, column=1, sticky="ew", padx=(8, 0), pady=5)

        preview = ttk.Frame(page, style="Alt.TFrame", padding=14)
        preview.grid(row=1, column=1, sticky="nsew")
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(1, weight=1)
        self.spine_files_label = ttk.Label(preview, text="尚未选择动画文件。", style="Alt.TLabel")
        self.spine_files_label.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self.spine_preview = tk.Label(preview, bg=COLORS["empty"], text="选择 Spine 文件后显示纹理预览", fg=COLORS["muted"])
        self.spine_preview.grid(row=1, column=0, sticky="nsew")
        ttk.Label(preview, text="ZIP 可包含多页 Atlas、嵌套源图和 Spine 工程；导入时会自动校验并归一化。", style="Alt.TLabel").grid(row=2, column=0, sticky="w", pady=(8, 0))

    def _browse_spine_files(self) -> None:
        values = filedialog.askopenfilenames(
            parent=self.root,
            title="选择 Spine 动画源",
            filetypes=(("Spine package", "*.zip"), ("Spine files", "*.json *.atlas *.png"), ("All files", "*.*")),
        )
        if not values:
            return
        self.spine_files = [Path(value).resolve() for value in values]
        archives = [path for path in self.spine_files if path.suffix.casefold() == ".zip"]
        if archives and len(self.spine_files) != 1:
            self.spine_files = []
            self._show_error("动画导入失败", ValueError("Spine ZIP 必须单独选择，不能与散文件混合导入。"))
            return
        if len(self.spine_files) == 1 and self.spine_name.get().strip() in {"", "新 Spine 动画"}:
            self.spine_name.set(self.spine_files[0].stem)
        self.spine_files_label.configure(text=" · ".join(ellipsize(path.name, 28) for path in self.spine_files))
        texture = next((path for path in self.spine_files if path.suffix.casefold() == ".png"), None)
        self._set_preview(self.spine_preview, texture, (640, 520), "spine-import")

    def _clear_spine_inputs(self) -> None:
        self.spine_files = []
        self.spine_files_label.configure(text="尚未选择动画文件。")
        self._set_preview(self.spine_preview, None, (640, 520), "spine-import")

    def _import_spine_asset(self) -> None:
        if not self.spine_files:
            self._show_error("动画导入失败", ValueError("请先选择 Spine ZIP，或 JSON、ATLAS 和纹理文件。"))
            return
        target_value = self.spine_target.get()
        target_map = {label: target for _key, label, target in self._target_records()}
        try:
            record = self.asset_library.import_spine(
                self.spine_files,
                name=self.spine_name.get(),
                runtime_version=self.spine_runtime.get(),
                target=target_map.get(target_value),
                author=self.spine_author.get(),
                license_text=self.spine_license.get(),
            )
        except Exception as error:
            self._show_error("动画导入失败", error)
            return
        self.spine_runtime.set(str((record.get("metadata") or {}).get("runtime_version") or self.spine_runtime.get())[:3])
        self.selected_asset_id = record["id"]
        self.pages.select(self.management_page)
        self.management_tabs.select(self.asset_tab)
        self._refresh_asset_gallery()

    # ---------- settings ----------

    def _build_settings_page(self) -> None:
        page = self.settings_page
        page.columnconfigure(0, weight=1)
        game = ttk.LabelFrame(page, text="游戏与 Steam", padding=16)
        game.grid(row=0, column=0, sticky="ew")
        game.columnconfigure(1, weight=1)
        self.game_path_var = tk.StringVar(value=str(self.game_dir_override or ""))
        ttk.Label(game, text="The Bazaar 位置").grid(row=0, column=0, sticky="w")
        ttk.Entry(game, textvariable=self.game_path_var, state="readonly").grid(row=0, column=1, sticky="ew", padx=10)
        ttk.Button(game, text="重新自动定位", command=self._auto_locate_game).grid(row=0, column=2)
        ttk.Button(game, text="手动定位…", command=self._manual_locate_game).grid(row=0, column=3, padx=(8, 0))
        self.steam_status = ttk.Label(game, text="", style="Muted.TLabel")
        self.steam_status.grid(row=1, column=0, columnspan=4, sticky="w", pady=(10, 0))

        data = ttk.LabelFrame(page, text="数据与高级设置", padding=16)
        data.grid(row=1, column=0, sticky="ew", pady=(14, 0))
        data.columnconfigure(1, weight=1)
        rows = (
            ("皮肤包", WORKSPACES_ROOT),
            ("一级素材", self.asset_library.root),
            ("设置", self.settings_path),
        )
        for index, (label, path) in enumerate(rows):
            ttk.Label(data, text=label).grid(row=index, column=0, sticky="w", pady=5)
            ttk.Label(data, text=ellipsize(path, 100), style="Muted.TLabel").grid(row=index, column=1, sticky="w", padx=10, pady=5)
            ttk.Button(data, text="打开", command=lambda selected=path: os.startfile(selected if selected.is_dir() else selected.parent)).grid(row=index, column=2, pady=5)
        self.compatibility_status = ttk.Label(data, text="", style="Muted.TLabel", wraplength=900)
        self.compatibility_status.grid(row=3, column=0, columnspan=3, sticky="w", pady=(12, 0))

        updates = ttk.LabelFrame(page, text="软件更新", padding=16)
        updates.grid(row=2, column=0, sticky="ew", pady=(14, 0))
        updates.columnconfigure(0, weight=1)
        self.update_status = ttk.Label(
            updates,
            text=f"当前版本：v{MANAGER_VERSION}",
            style="Muted.TLabel",
            wraplength=850,
        )
        self.update_status.grid(row=0, column=0, sticky="w")
        self.auto_update_var = tk.BooleanVar(
            value=bool(self.settings.get("auto_check_updates", True))
        )
        ttk.Checkbutton(
            updates,
            text="启动时自动检查 GitHub 正式版",
            variable=self.auto_update_var,
            command=self._save_update_preference,
        ).grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.check_update_button = ttk.Button(
            updates,
            text="检查更新",
            command=lambda: self._check_for_updates(manual=True),
        )
        self.check_update_button.grid(row=0, column=1, padx=(10, 0))
        self.install_update_button = ttk.Button(
            updates,
            text="下载并安装",
            command=self._download_available_update,
            state="disabled",
        )
        self.install_update_button.grid(row=0, column=2, padx=(8, 0))

        feedback = ttk.LabelFrame(page, text="错误日志与反馈", padding=16)
        feedback.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        feedback.columnconfigure(0, weight=1)
        self.error_log_path = manager_root() / "ui-error.log"
        ttk.Label(
            feedback,
            text=(
                "反馈报告会先在本机生成并脱敏；只有点击按钮后才复制到剪贴板。"
                "复制后可直接粘贴到 IM 发给维护者；软件不会上传日志。"
            ),
            style="Muted.TLabel",
            wraplength=850,
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(
            feedback,
            text="复制脱敏诊断",
            command=self._copy_diagnostic_report,
        ).grid(row=0, column=1, padx=(10, 0))
        ttk.Button(
            feedback,
            text="打开日志目录",
            command=self._open_error_log_directory,
        ).grid(row=0, column=2, padx=(8, 0))

    def _refresh_settings_status(self) -> None:
        self.game_path_var.set(str(self.game_dir_override or ""))
        steam = find_steam_executable()
        self.steam_status.configure(
            text=(f"Steam：{steam}" if steam else "未找到 Steam；启动游戏不可用。"),
            foreground=COLORS["accent"] if steam else COLORS["warning"],
        )
        supported = sum(
            1
            for _key, _label, target in self._target_records()
            if target.get("deployment_status") == "supported"
        )
        compatible = sum(
            1
            for _key, _label, target in self._target_records()
            if target.get("deployment_status") == "compatible_unverified"
        )
        detected = len(self._target_records())
        self.compatibility_status.configure(
            text=(
                f"当前 Steam build：{self.catalog.get('steam_build') or '未知'} · "
                f"已发现 {detected} 个职业皮肤目标 · 已验证 {supported} 个 · "
                f"自动兼容 {compatible} 个。"
            )
        )

    def _save_update_preference(self) -> None:
        self.settings["auto_check_updates"] = bool(self.auto_update_var.get())
        self._save_settings()

    def _auto_check_for_updates(self) -> None:
        if bool(self.settings.get("auto_check_updates", True)):
            self._check_for_updates(manual=False)

    def _check_for_updates(self, *, manual: bool) -> None:
        if self.update_check_running:
            return
        self.update_check_running = True
        self.check_update_button.configure(state="disabled")
        self.update_status.configure(text="正在检查 GitHub 正式版…", foreground=COLORS["warning"])

        def worker() -> None:
            try:
                release = fetch_latest_release(MANAGER_VERSION)
            except Exception as error:
                self.root.after(
                    0,
                    lambda caught=error: self._finish_update_check(
                        None, caught, manual=manual
                    ),
                )
            else:
                self.root.after(
                    0,
                    lambda result=release: self._finish_update_check(
                        result, None, manual=manual
                    ),
                )

        threading.Thread(target=worker, daemon=True).start()

    def _finish_update_check(
        self,
        release: dict | None,
        error: Exception | None,
        *,
        manual: bool,
    ) -> None:
        self.update_check_running = False
        self.check_update_button.configure(state="normal")
        if error is not None:
            self.update_status.configure(
                text=f"更新检查失败：{error}", foreground=COLORS["warning"]
            )
            if manual:
                self._show_error("更新检查失败", error)
            return
        assert release is not None
        self.latest_release = release
        if not release.get("update_available"):
            self.install_update_button.configure(state="disabled")
            self.update_status.configure(
                text=f"当前已是最新正式版：v{MANAGER_VERSION}",
                foreground=COLORS["accent"],
            )
            if manual:
                messagebox.showinfo(
                    "没有可用更新",
                    f"当前 v{MANAGER_VERSION} 已是 GitHub 最新正式版。",
                    parent=self.root,
                )
            return
        version = str(release["version"])
        self.install_update_button.configure(state="normal")
        self.update_status.configure(
            text=f"发现正式版 v{version}；安装包会在下载后校验 SHA-256。",
            foreground=COLORS["accent"],
        )
        already_notified = str(self.settings.get("last_notified_version") or "")
        if not manual and already_notified != version:
            self.settings["last_notified_version"] = version
            self._save_settings()
            if messagebox.askyesno(
                "发现软件更新",
                f"GitHub 已发布 v{version}。现在下载、校验并安装吗？",
                parent=self.root,
            ):
                self._download_available_update(confirm=False)

    def _download_available_update(self, *, confirm: bool = True) -> None:
        release = self.latest_release
        if not release or not release.get("update_available"):
            self._check_for_updates(manual=True)
            return
        version = str(release["version"])
        if confirm and not messagebox.askyesno(
            "安装软件更新",
            f"下载 GitHub 正式版 v{version}，校验 SHA-256 后关闭当前程序并升级？",
            parent=self.root,
        ):
            return
        update_root = manager_root() / "updates"
        self._background(
            f"正在下载并校验 v{version}…",
            lambda: download_release_installer(release, update_root),
            self._launch_downloaded_update,
        )

    def _launch_downloaded_update(self, result: dict) -> None:
        try:
            launch_verified_installer(Path(result["path"]))
        except Exception as error:
            self._background_error(error, traceback.format_exc())
            return
        self.global_status.configure(
            text=f"v{result['version']} 安装程序已启动；正在关闭当前版本…",
            foreground=COLORS["accent"],
        )
        self.root.after(400, self.root.destroy)

    def _diagnostic_report(self) -> str:
        game = {
            "path": str(self.game_dir_override or ""),
            "steam_build": self.catalog.get("steam_build"),
            "catalog_source": self.catalog.get("source"),
        }
        loader: dict = {}
        if self.game_dir_override:
            try:
                loader = bepinex_status(self.game_dir_override)
            except Exception as error:
                loader = {"status_error": str(error)}
        try:
            raw_deployment = installation_diagnostics()
            deployment = {
                key: raw_deployment.get(key)
                for key in (
                    "installed",
                    "healthy",
                    "operational",
                    "state",
                    "update_required",
                    "checks",
                    "compatibility_errors",
                    "deployment_warnings",
                    "compatibility_notes",
                )
            }
        except Exception as error:
            deployment = {"status_error": str(error)}
        return build_diagnostic_report(
            manager_version=MANAGER_VERSION,
            error_log=self.error_log_path,
            game=game,
            loader=loader,
            deployment=deployment,
        )

    def _copy_diagnostic_report(self, *, popup: bool = True) -> str:
        report = self._diagnostic_report()
        self.root.clipboard_clear()
        self.root.clipboard_append(report)
        self.root.update_idletasks()
        if popup:
            messagebox.showinfo(
                "诊断已复制",
                "脱敏诊断报告已复制到剪贴板，可以直接粘贴到 IM 发给维护者。",
                parent=self.root,
            )
        return report

    def _open_error_log_directory(self) -> None:
        self.error_log_path.parent.mkdir(parents=True, exist_ok=True)
        os.startfile(self.error_log_path.parent)

    def _auto_locate_game(self) -> None:
        complete = [install for install in detect_installs() if install.complete]
        if not complete:
            messagebox.showwarning("未找到游戏", "Steam 库中没有找到完整的 The Bazaar 安装。可使用手动定位。", parent=self.root)
            return
        self.game_dir_override = complete[0].game_dir
        self._save_settings()
        self._rescan_game()

    def _manual_locate_game(self) -> None:
        selected = filedialog.askdirectory(parent=self.root, title="选择 The Bazaar 游戏目录", initialdir=self.game_dir_override or Path.home())
        if not selected:
            return
        path = Path(selected).resolve()
        if not (path / "TheBazaar.exe").is_file() or not (path / "TheBazaar_Data").is_dir():
            messagebox.showerror("目录无效", "请选择包含 TheBazaar.exe 和 TheBazaar_Data 的游戏目录。", parent=self.root)
            return
        self.game_dir_override = path
        self._save_settings()
        self._rescan_game()

    # ---------- lifecycle actions ----------

    def _import_pack_archive(self, archive: Path, *, replace_existing: bool = False) -> StudioWorkspace:
        archive = archive.resolve()
        pack_id, name, version = _read_complete_pack_identity(archive)
        destination = WORKSPACES_ROOT / pack_id
        existing = self.workspaces.get(str(destination.resolve()))
        if existing and not replace_existing:
            if not messagebox.askyesno("更新已有皮肤包", f"皮肤库中已存在 {name}。用导入内容更新它吗？", parent=self.root):
                raise RuntimeError("用户取消了皮肤包更新。")
        with tempfile.TemporaryDirectory() as temporary:
            validation = StudioWorkspace.create("import.validation", root=Path(temporary))
            validation.import_zip(archive)
        workspace = StudioWorkspace.create(pack_id, name=name, version=version)
        workspace.import_zip(archive)
        import_embedded_library_assets(workspace, self.asset_library)
        path = str(workspace.directory.resolve())
        self.workspaces[path] = workspace
        self.asset_library.register_workspace(workspace)
        self._save_settings()
        return workspace

    def _import_pack_zip(self) -> None:
        selected = filedialog.askopenfilename(parent=self.root, title="导入皮肤包 ZIP", filetypes=(("皮肤包 ZIP", "*.zip"),))
        if not selected:
            return
        try:
            workspace = self._import_pack_archive(Path(selected))
        except RuntimeError as error:
            if "取消" not in str(error):
                self._show_error("皮肤包导入失败", error)
            return
        except Exception as error:
            self._show_error("皮肤包导入失败", error)
            return
        self.selected_pack_path = str(workspace.directory)
        self._refresh_everything()

    def _use_pack(self) -> None:
        if not self.selected_pack_path:
            return
        self.mapping_models.append({"pack_path": self.selected_pack_path, "target_key": "", "enabled": True})
        self.pages.select(self.deployment_page)
        self._refresh_deployment_rows()

    def _edit_pack(self) -> None:
        workspace = self.workspaces.get(self.selected_pack_path or "")
        if not workspace:
            return
        try:
            self.active_creation_workspace = workspace
            self.pages.select(self.creation_page)
            self.root.update_idletasks()
            authoring = (getattr(workspace, "state", {}) or {}).get("authoring") or {}
            if authoring.get("mode") == "manual_slots":
                self.active_automatic_fingerprint = None
                self.manual_slot_editor.edit_workspace(workspace)
                if hasattr(self, "creation_modes"):
                    self._select_creation_mode(1)
            else:
                profile = self.generator.edit_workspace(workspace)
                self.active_automatic_fingerprint = self.generator.draft_fingerprint(profile)
                if hasattr(self, "creation_modes"):
                    self._select_creation_mode(0)
        except Exception as error:
            self._show_error("无法编辑皮肤包", error)

    def _edit_pack_spine(self) -> None:
        workspace = self.workspaces.get(self.selected_pack_path or "")
        if not workspace:
            return
        animation = workspace.state.get("animation") or {}
        if animation.get("mode") != "spine" or not animation.get("files"):
            messagebox.showinfo(
                "没有 Spine 引用",
                "请先为该皮肤引用一个 Spine 一级素材。",
                parent=self.root,
            )
            return
        dialog = PackEditorDialog(self, workspace)
        dialog.show()
        dialog.tabs.select(2)

    def _export_pack(self) -> None:
        workspace = self.workspaces.get(self.selected_pack_path or "")
        if not workspace:
            return
        self._export_workspace(workspace)

    def _export_workspace(self, workspace: StudioWorkspace) -> None:
        pack = workspace.state.get("pack") or {}
        selected = filedialog.asksaveasfilename(parent=self.root, title="导出皮肤包", initialfile=f"{pack.get('name')}-{pack.get('version')}.zip", defaultextension=".zip", filetypes=(("皮肤包 ZIP", "*.zip"),))
        if not selected:
            return
        try:
            export_pack_with_library_assets(workspace, Path(selected), self.asset_library)
            messagebox.showinfo("导出完成", f"皮肤包已导出：\n{selected}", parent=self.root)
        except Exception as error:
            self._show_error("导出失败", error)

    def _delete_pack(self) -> None:
        path = self.selected_pack_path
        workspace = self.workspaces.get(path or "")
        if not path or not workspace:
            return
        affected = [key for key, value in self.assignments.items() if value.get("pack_path") == path]
        pack = workspace.state.get("pack") or {}
        if self._pack_is_deployed(str(pack.get("id") or "")):
            messagebox.showwarning("无法删除", "该皮肤包当前仍在游戏中部署。请先点击“取消全部部署”，再删除皮肤包。", parent=self.root)
            return
        text = f"删除皮肤包“{pack.get('name') or pack.get('id')}”？\n\n一级素材不会删除。"
        if affected:
            text += f"\n同时会清除 {len(affected)} 条计划映射。"
        if not messagebox.askyesno("删除皮肤包", text, parent=self.root):
            return
        for key in affected:
            self.assignments.pop(key, None)
        self.mapping_models = [model for model in self.mapping_models if model.get("pack_path") != path]
        shutil.rmtree(workspace.directory)
        self.workspaces.pop(path, None)
        self.selected_pack_path = None
        self._save_settings()
        self._refresh_everything()

    def _import_primary_asset(self) -> None:
        selected = filedialog.askopenfilename(parent=self.root, title="导入一级素材", filetypes=(("Images / Audio", "*.png *.jpg *.jpeg *.webp *.bmp *.wav *.mp3 *.flac *.ogg"), ("All files", "*.*")))
        if not selected:
            return
        path = Path(selected)
        try:
            record = self.asset_library.import_file(path, asset_type=classify_file(path))
        except Exception as error:
            self._show_error("素材导入失败", error)
            return
        self.selected_asset_id = record["id"]
        self._refresh_asset_gallery()

    def _use_asset(self) -> None:
        record = self.asset_library.assets.get(self.selected_asset_id or "")
        if not record:
            return
        if not self.workspaces:
            messagebox.showinfo("还没有皮肤包", "请先在“皮肤制作”创建皮肤，或导入一个皮肤包。", parent=self.root)
            return
        dialog = AssetUseDialog(self, record)
        dialog.show()

    def _edit_asset(self) -> None:
        record = self.asset_library.assets.get(self.selected_asset_id or "")
        if not record:
            return
        AssetMetadataDialog(self, record).show()

    def _delete_asset(self) -> None:
        asset_id = self.selected_asset_id
        record = self.asset_library.assets.get(asset_id or "")
        if not asset_id or not record:
            return
        refs = self.asset_library.references(self.workspaces.values()).get(asset_id, [])
        if refs:
            messagebox.showwarning("无法删除一级素材", "该素材仍被以下皮肤使用：\n" + "\n".join(f"• {item.get('pack_name')} · {item.get('role')}" for item in refs), parent=self.root)
            return
        if not messagebox.askyesno("删除一级素材", f"永久删除“{record.get('name')}”及其受管文件吗？", parent=self.root):
            return
        self.asset_library.remove(asset_id)
        self.selected_asset_id = None
        self._refresh_asset_gallery()

    def _deploy_all(self) -> None:
        if self.busy:
            return
        self._sync_mapping_models()
        target_map = {key: target for key, _label, target in self._target_records()}
        assignments: list[tuple[StudioWorkspace, dict]] = []
        invalid: list[str] = []
        seen_targets: set[str] = set()
        for model in self.mapping_models:
            if not model.get("enabled"):
                continue
            key = str(model.get("target_key") or "")
            path = str(model.get("pack_path") or "")
            if not key or not path:
                invalid.append("存在未完成的启用映射。")
                continue
            if key in seen_targets:
                invalid.append(f"游戏原皮肤 {key} 被重复映射。")
                continue
            seen_targets.add(key)
            target = target_map.get(key)
            workspace = self.workspaces.get(path)
            if not target or target.get("deployment_status") != "supported":
                invalid.append(f"{key} 尚无已验证适配器。")
            elif not workspace:
                invalid.append(f"皮肤包路径不可用：{path}")
            else:
                assignments.append((workspace, target))
        if invalid:
            messagebox.showerror("部署方案不可用", "\n".join(invalid), parent=self.root)
            return
        if not assignments:
            messagebox.showinfo("没有启用映射", "请先建立并启用至少一条“自定义皮肤 → 游戏原皮肤”映射。", parent=self.root)
            return
        try:
            selected_game = preferred_game_install(self.game_dir_override)
            loader = bepinex_status(selected_game.game_dir)
        except Exception as error:
            self._show_error("无法准备部署", error)
            return
        loader_note = ""
        if not loader["ready"]:
            loader_note = (
                "\n\n未检测到完整的 BepInEx。皮肤管理器将自动安装并校验官方 "
                f"BepInEx {loader['bootstrap_version']}；不需要 BazaarPlusPlus。"
            )
        if not messagebox.askyesno(
            "部署更改",
            f"将部署 {len(assignments)} 条皮肤映射。继续前请关闭 The Bazaar。"
            + loader_note,
            parent=self.root,
        ):
            return
        self.assignments = {
            model["target_key"]: {"pack_path": model["pack_path"], "enabled": model["enabled"]}
            for model in self.mapping_models
            if model.get("target_key") and model.get("pack_path")
        }
        self._save_settings()
        self._background(
            "正在部署皮肤映射…",
            lambda: StudioWorkspace.deploy_assignments(assignments, self.game_dir_override),
            self._finish_deploy,
        )

    def _finish_deploy(self, result: dict) -> None:
        packs = "、".join(item["id"] for item in result.get("packs") or [])
        warnings = list(result.get("deployment_warnings") or [])
        text = "已部署 " + packs
        if warnings:
            text += (
                f"\n\n当前游戏版本有 {len(warnings)} 项兼容降级；"
                "可用部分已继续部署，不兼容槽位保留游戏当前素材。"
            )
        self._finish_action(
            "部署完成" if not warnings else "部署完成（兼容模式）",
            text,
        )

    def _undeploy_all(self) -> None:
        if self.busy:
            return
        try:
            diagnostics = installation_diagnostics()
        except Exception as error:
            self._show_error("无法读取部署状态", error)
            return
        if not diagnostics.get("installed"):
            messagebox.showinfo("当前未部署", "游戏当前已经是原版状态。", parent=self.root)
            return
        if not messagebox.askyesno("取消全部部署", "恢复所有游戏原皮肤？皮肤包和一级素材不会删除。", parent=self.root):
            return
        self._background("正在恢复原版…", uninstall, lambda removed: self._finish_action("恢复完成", f"已移除 {len(removed)} 个托管路径。"))

    def _launch_game(self) -> None:
        if self.busy:
            return
        if self.game_dir_override is None:
            self._auto_locate_game()
            if self.game_dir_override is None:
                self.pages.select(self.settings_page)
                return
        self._background(
            "正在通过 Steam 启动 The Bazaar…",
            lambda: launch_game(self.game_dir_override),
            lambda result: self._finish_action(
                "已启动游戏", f"启动方式：{result.get('method')}", popup=False
            ),
        )

    def _rescan_game(self) -> None:
        try:
            self.catalog = discovered_catalog(self.game_dir_override)
        except Exception as error:
            self._show_error("游戏扫描失败", error)
            return
        self._refresh_everything()

    def _background(self, status: str, operation, complete) -> None:
        self.busy = True
        self.deploy_button.configure(state="disabled")
        self.launch_button.configure(state="disabled")
        self.global_status.configure(text=status, foreground=COLORS["warning"])

        def worker() -> None:
            try:
                result = operation()
            except Exception as error:
                details = traceback.format_exc()
                self.root.after(0, lambda caught=error, trace=details: self._background_error(caught, trace))
            else:
                self.root.after(0, lambda: complete(result))

        threading.Thread(target=worker, daemon=True).start()

    def _background_error(self, error: Exception, details: str) -> None:
        self.busy = False
        self.deploy_button.configure(state="normal")
        self.launch_button.configure(state="normal")
        self._show_error("操作失败", error, details=details)
        if messagebox.askyesno(
            "复制错误诊断？",
            "是否复制脱敏诊断报告？复制后可以直接粘贴到 IM 发给维护者。\n\n软件不会上传日志。",
            parent=self.root,
        ):
            self._copy_diagnostic_report(popup=False)
        self._refresh_global_status()

    def _finish_action(self, title: str, text: str, *, popup: bool = True) -> None:
        self.busy = False
        self.deploy_button.configure(state="normal")
        self.launch_button.configure(state="normal")
        self._refresh_global_status()
        self._refresh_deployment_rows()
        if popup:
            messagebox.showinfo(title, text, parent=self.root)

    # ---------- previews and refresh ----------

    def _pack_cover(self, workspace: StudioWorkspace | None) -> Path | None:
        if workspace is None:
            return None
        for slot in ("store_image", "collection_list", "hero_select", "portrait_gameplay", "standing_overlay"):
            path = workspace.visual_path(slot)
            if path:
                return path
        return None

    def _target_cover(self, target: dict | None) -> Path | None:
        if not target:
            return None
        hero = str(target.get("hero") or "").casefold()
        source = PROJECT_ROOT / "manager" / "assets" / "badge-templates" / "hero-select-gold" / "sources" / f"{hero}.png"
        if source.is_file():
            return source
        fallback = PROJECT_ROOT / "manager" / "assets" / "badge-templates" / "hero-select-gold" / "empty_preview.png"
        return fallback if fallback.is_file() else None

    def _set_asset_preview(
        self,
        widget: tk.Label,
        record: dict,
        size: tuple[int, int],
        key: str,
    ) -> None:
        path = self.asset_library.preview_path(record)
        if path:
            self._set_preview(widget, path, size, key)
            return
        image = Image.new("RGBA", size, (36, 45, 58, 255))
        draw = ImageDraw.Draw(image)
        asset_type = str(record.get("type") or "other")
        if asset_type == "audio":
            digest = bytes.fromhex(str(record.get("sha256") or "00" * 32)[:64])
            center = size[1] // 2
            draw.line((16, center, size[0] - 16, center), fill=(101, 212, 179, 120), width=2)
            width = max(2, (size[0] - 32) // max(1, len(digest)))
            for index, value in enumerate(digest):
                height = 8 + int(value / 255 * (size[1] * 0.36))
                x = 16 + index * width
                draw.rectangle((x, center - height, x + max(1, width - 1), center + height), fill=(101, 212, 179, 230))
            label = "AUDIO"
        elif asset_type == "spine":
            draw.ellipse((45, 18, size[0] - 45, size[1] - 18), outline=(101, 212, 179, 255), width=4)
            draw.line((size[0] // 2, 32, size[0] // 2, size[1] - 32), fill=(101, 212, 179, 255), width=4)
            label = "SPINE"
        else:
            draw.rectangle((size[0] // 3, size[1] // 4, size[0] * 2 // 3, size[1] * 3 // 4), outline=(107, 122, 143, 255), width=3)
            label = TYPE_NAMES.get(asset_type, "FILE")
        draw.text((10, size[1] - 20), label, fill=(238, 242, 247, 230))
        photo = ImageTk.PhotoImage(image)
        self.photos[key] = photo
        widget.configure(image=photo, text="", width=size[0], height=size[1])

    def _set_preview(self, widget: tk.Label, path: Path | None, size: tuple[int, int], key: str) -> None:
        try:
            if path and path.is_file():
                with Image.open(path) as opened:
                    image = compose_image_preview(opened, size=size)
            else:
                image = Image.new("RGBA", size, (36, 45, 58, 255))
                draw = ImageDraw.Draw(image)
                draw.rectangle((size[0] // 3, size[1] // 3, size[0] * 2 // 3, size[1] * 2 // 3), outline=(107, 122, 143, 255), width=3)
                draw.line((size[0] // 3, size[1] * 2 // 3, size[0] // 2, size[1] // 2, size[0] * 2 // 3, size[1] * 2 // 3), fill=(107, 122, 143, 255), width=3)
            photo = ImageTk.PhotoImage(image)
            self.photos[key] = photo
            widget.configure(image=photo, text="", width=size[0], height=size[1])
        except Exception:
            widget.configure(image="", text="无法预览", fg=COLORS["danger"], width=max(8, size[0] // 10), height=max(3, size[1] // 22))

    def _refresh_global_status(self) -> None:
        try:
            diagnostics = installation_diagnostics()
        except Exception:
            diagnostics = {"installed": False}
        build = self.catalog.get("steam_build") or "未知 build"
        if diagnostics.get("installed"):
            if diagnostics.get("state") == "degraded":
                text, color = (
                    f"已部署（兼容模式） · Steam {build}",
                    COLORS["warning"],
                )
            else:
                text, color = f"已部署 · Steam {build}", COLORS["accent"]
        else:
            text, color = f"原版 / 未部署 · Steam {build}", COLORS["muted"]
        self.global_status.configure(text=text, foreground=color)

    def _refresh_everything(self) -> None:
        target_labels = ["未指定（导入后再配置）"] + [label for _key, label, _target in self._target_records()]
        if hasattr(self, "spine_target_box"):
            self.spine_target_box.configure(values=tuple(target_labels))
        self._refresh_global_status()
        self._refresh_deployment_rows()
        self._refresh_pack_gallery()
        self._refresh_asset_gallery()
        self._refresh_settings_status()

    def _show_error(
        self,
        title: str,
        error: Exception,
        *,
        details: str | None = None,
    ) -> None:
        trace = details or "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )
        append_error_log(manager_root() / "ui-error.log", title, trace)
        messagebox.showerror(title, str(error), parent=self.root)

    def self_test_layout(self) -> None:
        self.root.geometry("1120x720+0+0")
        self.root.update_idletasks()
        self.root.update()
        bottom = self.root.winfo_rooty() + self.root.winfo_height()
        for button in (self.launch_button, self.deploy_button):
            if not button.winfo_ismapped() or button.winfo_rooty() + button.winfo_height() > bottom:
                raise RuntimeError(f"关键按钮不可见：{button.cget('text')}")
        if self.pages.tab(self.pages.select(), "text") != "皮肤部署":
            raise RuntimeError("默认页面必须是皮肤部署。")
        expected = ["皮肤部署", "皮肤管理", "皮肤制作", "动画导入", "设置"]
        actual = [self.pages.tab(index, "text") for index in range(self.pages.index("end"))]
        if actual != expected:
            raise RuntimeError(f"一级导航错误：{actual}")
        creation_modes = [
            self.creation_modes.tab(index, "text")
            for index in range(self.creation_modes.index("end"))
        ]
        if creation_modes != ["默认 / 草稿模式", "逐槽位模式"]:
            raise RuntimeError(f"皮肤制作模式错误：{creation_modes}")
        self.manual_slot_editor.self_test()

    def run(self) -> None:
        self.root.mainloop()


class PackEditorDialog:
    """Context editor; assets remain first-class and deployment remains external."""

    def __init__(self, app: SkinManagerV12, workspace: StudioWorkspace) -> None:
        self.app = app
        self.original_workspace = workspace
        self.original_path = str(workspace.directory.resolve())
        drafts_root = manager_root() / "editor-drafts"
        drafts_root.mkdir(parents=True, exist_ok=True)
        self._draft_directory = tempfile.TemporaryDirectory(
            prefix="pack-",
            dir=drafts_root,
        )
        draft_path = Path(self._draft_directory.name) / "workspace"
        shutil.copytree(workspace.directory, draft_path)
        self.workspace = StudioWorkspace.load(draft_path)
        self.dirty = False
        self.window = tk.Toplevel(app.root)
        self.window.title("编辑皮肤包")
        self.window.geometry("980x700")
        self.window.minsize(820, 560)
        self.window.configure(bg=COLORS["window"])
        self.preview_photo = None
        self.spine_preview_photo = None
        self.spine_package = None
        self.spine_preview_pose = None
        self.spine_preview_metrics = None
        self.spine_preview_job = None
        self.spine_preview_canvas_scale = 0.0
        self.spine_preview_game_pixels_per_unit = 0.0
        self.spine_drag_origin = None
        self.window.protocol("WM_DELETE_WINDOW", self._close_requested)

    def show(self) -> None:
        outer = ttk.Frame(self.window, padding=16)
        outer.pack(fill="both", expand=True)
        pack = self.workspace.state.get("pack") or {}
        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 10))
        ttk.Label(header, text=f"编辑：{pack.get('name') or pack.get('id')}", font=("Microsoft YaHei UI", 15, "bold")).pack(side="left")
        ttk.Button(header, text="返回皮肤包管理", command=self._close_requested).pack(side="right")
        self.tabs = ttk.Notebook(outer)
        self.tabs.pack(fill="both", expand=True)
        visual = ttk.Frame(self.tabs, padding=12)
        audio = ttk.Frame(self.tabs, padding=12)
        animation = ttk.Frame(self.tabs, padding=12)
        info = ttk.Frame(self.tabs, padding=12)
        self.tabs.add(visual, text="图像槽位")
        self.tabs.add(audio, text="音频")
        self.tabs.add(animation, text="Spine 动画")
        self.tabs.add(info, text="皮肤包信息与日志")
        self._build_visual(visual)
        self._build_audio(audio)
        self._build_animation(animation)
        self.name_var = tk.StringVar(value=pack.get("name") or "")
        self.version_var = tk.StringVar(value=pack.get("version") or "0.1.0")
        self.name_var.trace_add("write", lambda *_: setattr(self, "dirty", True))
        self.version_var.trace_add("write", lambda *_: setattr(self, "dirty", True))
        for label, variable in (("名称", self.name_var), ("版本", self.version_var)):
            row = ttk.Frame(info)
            row.pack(fill="x", pady=6)
            ttk.Label(row, text=label, width=12).pack(side="left")
            ttk.Entry(row, textvariable=variable).pack(side="left", fill="x", expand=True)
        ttk.Label(info, text=f"稳定 ID：{pack.get('id')}\n工作区：{self.workspace.directory}", style="Muted.TLabel").pack(anchor="w", pady=(12, 0))
        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(10, 0))
        ttk.Button(footer, text="保存修改", style="Accent.TButton", command=self._save).pack(side="left")
        ttk.Button(footer, text="清空全部引用", style="Danger.TButton", command=self._clear_all).pack(side="left", padx=(8, 0))
        ttk.Button(footer, text="导出 ZIP", command=lambda: self.app._export_workspace(self.workspace)).pack(side="left", padx=(8, 0))
        self.window.transient(self.app.root)
        self.window.grab_set()

    def _build_animation(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(0, weight=1)
        controls = ttk.LabelFrame(parent, text="位置与缩放", padding=12)
        controls.grid(row=0, column=0, sticky="nsw", padx=(0, 12))
        animation = self.workspace.state.get("animation") or {}
        placement = animation.get("placement") or {}
        self.spine_animation_var = tk.StringVar(
            value=str(placement.get("animation") or "")
        )
        self.spine_x_var = tk.DoubleVar(
            value=float(placement.get("root_x_offset", 0.0))
        )
        self.spine_y_var = tk.DoubleVar(
            value=float(placement.get("root_y_offset", 300.0))
        )
        self.spine_scale_var = tk.DoubleVar(
            value=float(placement.get("scale_multiplier", 1.0))
        )
        self.spine_status_var = tk.StringVar(value="正在读取 Spine 素材…")
        ttk.Label(controls, text="动画").grid(row=0, column=0, sticky="w", pady=5)
        self.spine_animation_box = ttk.Combobox(
            controls,
            textvariable=self.spine_animation_var,
            state="readonly",
            width=24,
        )
        self.spine_animation_box.grid(row=0, column=1, sticky="ew", pady=5)
        for row, (label, variable, start, end, increment) in enumerate(
            (
                ("X 位置", self.spine_x_var, -500, 500, 5),
                ("Y 位置", self.spine_y_var, -500, 500, 5),
                ("缩放", self.spine_scale_var, 0.1, 4.0, 0.05),
            ),
            start=1,
        ):
            ttk.Label(controls, text=label).grid(row=row, column=0, sticky="w", pady=5)
            ttk.Spinbox(
                controls,
                from_=start,
                to=end,
                increment=increment,
                textvariable=variable,
                width=14,
            ).grid(row=row, column=1, sticky="ew", pady=5)
        ttk.Button(
            controls,
            text="恢复推荐值",
            command=self._reset_spine_placement,
        ).grid(row=4, column=0, columnspan=2, sticky="ew", pady=(12, 5))
        ttk.Label(
            controls,
            text=(
                "拖动右侧角色可调整 X/Y。\n"
                "Y 增大时角色上移；缩放 1.0 为原始倍率。"
            ),
            style="Muted.TLabel",
            wraplength=220,
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))

        preview = ttk.Frame(parent, style="Alt.TFrame", padding=10)
        preview.grid(row=0, column=1, sticky="nsew")
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(0, weight=1)
        self.spine_preview_canvas = tk.Canvas(
            preview,
            bg=COLORS["empty"],
            highlightthickness=0,
            cursor="fleur",
        )
        self.spine_preview_canvas.grid(row=0, column=0, sticky="nsew")
        self.spine_preview_canvas.bind("<Configure>", self._schedule_spine_preview)
        self.spine_preview_canvas.bind("<ButtonPress-1>", self._spine_drag_start)
        self.spine_preview_canvas.bind("<B1-Motion>", self._spine_drag_motion)
        self.spine_preview_canvas.bind("<ButtonRelease-1>", self._spine_drag_end)
        ttk.Label(
            preview,
            textvariable=self.spine_status_var,
            style="Alt.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(8, 0))
        for variable in (
            self.spine_animation_var,
            self.spine_x_var,
            self.spine_y_var,
            self.spine_scale_var,
        ):
            variable.trace_add("write", self._spine_parameter_changed)
        self.window.after_idle(self._prepare_spine_preview)

    def _spine_target(self):
        target = self.workspace.state.get("target") or {}
        hero = str(target.get("hero") or "").casefold()
        skin = str(target.get("skin") or "").casefold()
        return next(
            (
                item
                for item in spine_targets()
                if item.hero.casefold() == hero and item.skin.casefold() == skin
            ),
            None,
        )

    def _prepare_spine_preview(self) -> None:
        animation = self.workspace.state.get("animation") or {}
        if animation.get("mode") != "spine" or not animation.get("files"):
            self.spine_status_var.set("当前皮肤没有引用 Spine 一级素材。")
            self._schedule_spine_preview()
            return
        try:
            preview_root = self.workspace.directory.parent / "spine-preview-normalized"
            self.spine_package = import_spine_package(
                self.workspace.directory / "animation",
                workspace=preview_root,
            )
            animations = list(self.spine_package.animations)
            self.spine_animation_box.configure(values=animations)
            selected = self.spine_animation_var.get().strip()
            if selected not in animations:
                self.spine_animation_var.set(
                    "idle" if "idle" in animations else animations[0]
                )
            self.spine_preview_pose = render_setup_pose(self.spine_package)
            target = self._spine_target()
            if target is None:
                raise ValueError("当前皮肤目标没有可用的 Spine 适配器。")
            game = preferred_game_install(self.app.game_dir_override)
            self.spine_preview_metrics = calculate_preview_metrics(
                game,
                self.spine_package,
                target,
            )
            self.spine_status_var.set(
                f"Spine {self.spine_package.version} · 拖动角色调整位置"
            )
        except Exception as error:
            self.spine_package = None
            self.spine_preview_pose = None
            self.spine_preview_metrics = None
            self.spine_status_var.set(f"预览不可用：{error}")
        self._schedule_spine_preview()

    def _spine_parameter_changed(self, *_args) -> None:
        self.dirty = True
        self._schedule_spine_preview()

    def _schedule_spine_preview(self, *_args) -> None:
        if not hasattr(self, "spine_preview_canvas"):
            return
        if self.spine_preview_job is not None:
            self.window.after_cancel(self.spine_preview_job)
        self.spine_preview_job = self.window.after(35, self._render_spine_preview)

    def _render_spine_preview(self) -> None:
        self.spine_preview_job = None
        canvas = self.spine_preview_canvas
        width = max(1, canvas.winfo_width())
        height = max(1, canvas.winfo_height())
        if width < 80 or height < 80:
            return
        display_width = min(width, round(height * 16 / 9))
        display_height = min(height, round(width * 9 / 16))
        origin_x = (width - display_width) // 2
        origin_y = (height - display_height) // 2
        frame_scale = display_width / REFERENCE_WIDTH
        frame = Image.new("RGBA", (width, height), (17, 21, 28, 255))
        background_path = (
            PROJECT_ROOT / "manager" / "spine-preview" / "hero-select-background.jpg"
        )
        if background_path.is_file():
            with Image.open(background_path) as loaded:
                background = loaded.convert("RGBA").resize(
                    (display_width, display_height),
                    Image.Resampling.LANCZOS,
                )
            frame.alpha_composite(background, (origin_x, origin_y))
        draw = ImageDraw.Draw(frame, "RGBA")
        pose = self.spine_preview_pose
        metrics = self.spine_preview_metrics
        try:
            root_x_offset = float(self.spine_x_var.get())
            root_y_offset = float(self.spine_y_var.get())
            scale_multiplier = float(self.spine_scale_var.get())
        except (tk.TclError, ValueError):
            root_x_offset, root_y_offset, scale_multiplier = 0.0, 300.0, 1.0
        if pose is not None and metrics is not None:
            game_pixels_per_unit = (
                metrics.reference_pixels_per_spine_unit * scale_multiplier
            )
            self.spine_preview_game_pixels_per_unit = game_pixels_per_unit
            self.spine_preview_canvas_scale = frame_scale
            pose_scale = (
                game_pixels_per_unit / pose.pixels_per_spine_unit * frame_scale
            )
            pose_image = pose.image.resize(
                (
                    max(1, round(pose.image.width * pose_scale)),
                    max(1, round(pose.image.height * pose_scale)),
                ),
                Image.Resampling.LANCZOS,
            )
            root_x = origin_x + (
                HERO_ROOT_X + root_x_offset * game_pixels_per_unit
            ) * frame_scale
            root_y = origin_y + (
                REFERENCE_HEIGHT - HERO_ROOT_Y - root_y_offset * game_pixels_per_unit
            ) * frame_scale
            image_x = round(
                root_x + pose.min_x * game_pixels_per_unit * frame_scale
            )
            image_y = round(
                root_y - pose.max_y * game_pixels_per_unit * frame_scale
            )
            frame.alpha_composite(pose_image, (image_x, image_y))
            ready_center_x = origin_x + READY_CENTER_X * frame_scale
            ready_center_y = origin_y + (
                REFERENCE_HEIGHT - READY_CENTER_Y
            ) * frame_scale
            ready_width = READY_WIDTH * frame_scale
            ready_height = READY_HEIGHT * frame_scale
            draw.rounded_rectangle(
                (
                    ready_center_x - ready_width / 2,
                    ready_center_y - ready_height / 2,
                    ready_center_x + ready_width / 2,
                    ready_center_y + ready_height / 2,
                ),
                radius=max(8, round(ready_height * 0.42)),
                fill=(14, 102, 166, 240),
                outline=(255, 207, 91, 255),
                width=max(2, round(5 * frame_scale)),
            )
            draw.text(
                (ready_center_x, ready_center_y),
                "READY UI",
                anchor="mm",
                fill=(255, 255, 255, 255),
            )
        else:
            self.spine_preview_canvas_scale = 0.0
            self.spine_preview_game_pixels_per_unit = 0.0
            draw.text(
                (width / 2, height / 2),
                "为当前皮肤引用 Spine 一级素材后可预览",
                anchor="mm",
                fill=(255, 255, 255, 230),
            )
        self.spine_preview_photo = ImageTk.PhotoImage(frame)
        canvas.delete("all")
        canvas.create_image(0, 0, image=self.spine_preview_photo, anchor="nw")

    def _spine_drag_start(self, event: tk.Event) -> None:
        if (
            self.spine_preview_canvas_scale <= 0
            or self.spine_preview_game_pixels_per_unit <= 0
        ):
            return
        self.spine_drag_origin = (
            event.x,
            event.y,
            float(self.spine_x_var.get()),
            float(self.spine_y_var.get()),
        )

    def _spine_drag_motion(self, event: tk.Event) -> None:
        if self.spine_drag_origin is None:
            return
        start_x, start_y, root_x, root_y = self.spine_drag_origin
        denominator = (
            self.spine_preview_canvas_scale
            * self.spine_preview_game_pixels_per_unit
        )
        if denominator <= 0:
            return
        self.spine_x_var.set(
            round(max(-500, min(500, root_x + (event.x - start_x) / denominator)), 1)
        )
        self.spine_y_var.set(
            round(max(-500, min(500, root_y - (event.y - start_y) / denominator)), 1)
        )

    def _spine_drag_end(self, _event: tk.Event) -> None:
        self.spine_drag_origin = None

    def _reset_spine_placement(self) -> None:
        self.spine_x_var.set(0.0)
        self.spine_y_var.set(300.0)
        self.spine_scale_var.set(1.0)

    def _store_spine_placement(self) -> None:
        animation = self.workspace.state.get("animation") or {}
        if animation.get("mode") != "spine" or not animation.get("files"):
            return
        scale = float(self.spine_scale_var.get())
        if scale < 0.1 or scale > 4.0:
            raise ValueError("Spine 缩放必须在 0.1 到 4.0 之间。")
        animation["placement"] = {
            "animation": self.spine_animation_var.get().strip(),
            "root_x_offset": float(self.spine_x_var.get()),
            "root_y_offset": float(self.spine_y_var.get()),
            "scale_multiplier": scale,
        }
        self.workspace.state["animation"] = animation
        self.workspace.save()

    def _build_visual(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.columnconfigure(2, weight=0)
        parent.rowconfigure(0, weight=1)
        columns = ("slot", "file", "asset")
        self.tree = ttk.Treeview(parent, columns=columns, show="headings", selectmode="browse")
        for column, title, width in (("slot", "槽位", 220), ("file", "当前文件", 330), ("asset", "一级素材", 280)):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, stretch=column != "slot")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(parent, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        preview_panel = ttk.Frame(parent, style="Alt.TFrame", padding=10)
        preview_panel.grid(row=0, column=2, sticky="ns", padx=(12, 0))
        self.visual_preview = tk.Label(
            preview_panel,
            bg=COLORS["empty"],
            width=260,
            height=260,
        )
        self.visual_preview.pack()
        self.visual_usage = ttk.Label(
            preview_panel,
            text="选择槽位查看用途。",
            style="Alt.TLabel",
            wraplength=260,
        )
        self.visual_usage.pack(fill="x", pady=(8, 0))
        refs = (self.workspace.state.get("library_assets") or {}).get("visual_slots") or {}
        for slot in self.app.catalog.get("visual_slots") or []:
            slot_id = slot["id"]
            path = self.workspace.visual_path(slot_id)
            self.tree.insert("", "end", iid=slot_id, values=(f"{slot.get('name') or slot_id} · {slot_id}", path.name if path else "使用原版", refs.get(slot_id) or "—"))
        actions = ttk.Frame(parent)
        actions.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(actions, text="从一级素材选择…", command=self._choose_asset).pack(side="left")
        ttk.Button(actions, text="导入新素材…", command=self._import).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="粘贴", command=self._paste).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="清空槽位", command=self._clear_slot).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="对比原版", command=self._compare_original).pack(side="left", padx=(8, 0))
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._visual_selection_changed())
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children[0])
            self._visual_selection_changed()

    def _build_audio(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)
        ttk.Label(
            parent,
            text="每条逻辑语音路由可引用一个包含多个变体的一级音频素材；导入时沿用现有转码与响度处理。",
            style="Muted.TLabel",
        ).grid(row=0, column=0, sticky="w", pady=(0, 10))
        columns = ("route", "variants", "asset")
        self.audio_tree = ttk.Treeview(
            parent,
            columns=columns,
            show="headings",
            selectmode="browse",
        )
        for column, title, width in (
            ("route", "逻辑路由", 360),
            ("variants", "当前变体", 120),
            ("asset", "一级素材", 300),
        ):
            self.audio_tree.heading(column, text=title)
            self.audio_tree.column(column, width=width, stretch=column != "variants")
        self.audio_tree.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(parent, orient="vertical", command=self.audio_tree.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.audio_tree.configure(yscrollcommand=scroll.set)
        actions = ttk.Frame(parent)
        actions.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        ttk.Button(actions, text="从一级素材选择…", command=self._choose_audio_asset).pack(side="left")
        ttk.Button(actions, text="导入新音频…", command=self._import_audio).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="清空路由", command=self._clear_audio).pack(side="left", padx=(8, 0))
        self._reload_audio_rows()

    def _visual_selection_changed(self) -> None:
        slot = self._selected_slot()
        if not slot:
            return
        path = self.workspace.visual_path(slot)
        self.app._set_preview(
            self.visual_preview,
            path,
            (260, 260),
            f"pack-editor-{id(self)}-{slot}",
        )
        slot_record = next(
            (
                item
                for item in self.app.catalog.get("visual_slots") or []
                if item.get("id") == slot
            ),
            {},
        )
        reference = (
            ((self.workspace.state.get("library_assets") or {}).get("visual_slots") or {}).get(slot)
            or "未引用一级素材"
        )
        self.visual_usage.configure(
            text=(
                f"{slot_record.get('name') or slot}\n"
                f"稳定槽位：{slot}\n"
                f"用途：{slot_record.get('description') or '—'}\n"
                f"一级素材：{reference}"
            )
        )

    def _compare_original(self) -> None:
        slot = self._selected_slot()
        if not slot:
            return
        try:
            original = self.workspace.export_original_visual(
                slot, self.app.game_dir_override
            )
        except Exception as error:
            messagebox.showerror("无法读取原版贴图", str(error), parent=self.window)
            return
        replacement = self.workspace.visual_path(slot)
        comparison = tk.Toplevel(self.window)
        comparison.title(f"原版 / 当前替换 · {slot}")
        comparison.configure(bg=COLORS["window"])
        body = ttk.Frame(comparison, padding=14)
        body.pack(fill="both", expand=True)
        photos: list[ImageTk.PhotoImage] = []
        for column, (title, path) in enumerate(
            (("原版", original), ("当前替换", replacement))
        ):
            panel = ttk.Frame(body, padding=8)
            panel.grid(row=0, column=column, sticky="nsew")
            ttk.Label(panel, text=title, font=("Microsoft YaHei UI", 12, "bold")).pack()
            if path and path.is_file():
                with Image.open(path) as opened:
                    preview = compose_image_preview(opened, size=(360, 300))
                    size = opened.size
            else:
                preview = Image.new("RGBA", (360, 300), (36, 45, 58, 255))
                size = None
            photo = ImageTk.PhotoImage(preview)
            photos.append(photo)
            ttk.Label(panel, image=photo).pack(pady=8)
            ttk.Label(
                panel,
                text=f"{size[0]} × {size[1]}" if size else "未填充；部署时使用原版",
                style="Muted.TLabel",
            ).pack()
            body.columnconfigure(column, weight=1)
        comparison._preview_images = photos  # type: ignore[attr-defined]
        comparison.transient(self.window)

    def _audio_route(self) -> str | None:
        selected = self.audio_tree.selection()
        return selected[0] if selected else None

    def _reload_audio_rows(self) -> None:
        for item in self.audio_tree.get_children():
            self.audio_tree.delete(item)
        manifest = self.workspace.audio_manifest() or {}
        active = {
            str(route.get("logical_slot")): route
            for route in manifest.get("routes") or []
        }
        refs = ((self.workspace.state.get("library_assets") or {}).get("audio") or {})
        for route in self.workspace.audio_route_catalog():
            logical_slot = str(route.get("logical_slot") or "")
            current = active.get(logical_slot) or {}
            self.audio_tree.insert(
                "",
                "end",
                iid=logical_slot,
                values=(
                    f"{route.get('category') or '语音'} · {logical_slot}",
                    len(current.get("variants") or []),
                    refs.get(logical_slot) or "—",
                ),
            )

    def _choose_audio_asset(self) -> None:
        route = self._audio_route()
        if not route:
            return
        records = [
            record
            for record in self.app.asset_library.assets.values()
            if record.get("type") == "audio"
        ]
        if not records:
            messagebox.showinfo("一级音频素材为空", "请先导入一个音频文件。", parent=self.window)
            return
        labels = {
            f"{record.get('name')} · {record['id']}": record for record in records
        }
        choice = ChoiceDialog(self.window, "选择一级音频素材", tuple(labels)).show()
        if choice:
            self._attach_audio(route, labels[choice])

    def _attach_audio(self, route: str, record: dict) -> None:
        files = [
            path
            for path in self.app.asset_library.record_files(record)
            if path.suffix.casefold() in {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".opus"}
        ]
        if not files:
            messagebox.showerror("素材不可用", "该一级素材没有可用音频文件。", parent=self.window)
            return
        try:
            self.workspace.clear_audio_route(route)
            for source in files:
                self.workspace.import_audio(route, source)
        except Exception as error:
            messagebox.showerror("音频导入失败", str(error), parent=self.window)
            return
        refs = self.workspace.state.setdefault("library_assets", {}).setdefault("audio", {})
        refs[route] = record["id"]
        self.workspace.save()
        self.dirty = True
        self._reload_audio_rows()

    def _import_audio(self) -> None:
        route = self._audio_route()
        if not route:
            return
        selected = filedialog.askopenfilenames(
            parent=self.window,
            title=f"导入 {route} 的音频变体",
            filetypes=(("Audio", "*.wav *.mp3 *.flac *.ogg *.m4a *.aac *.opus"),),
        )
        if not selected:
            return
        try:
            record = self.app.asset_library.import_files(
                [Path(value) for value in selected],
                asset_type="audio",
                name=f"{(self.workspace.state.get('pack') or {}).get('name')} · {route}",
                metadata={"logical_slot": route, "variant_count": len(selected)},
            )
        except Exception as error:
            messagebox.showerror("一级音频素材导入失败", str(error), parent=self.window)
            return
        self._attach_audio(route, record)

    def _clear_audio(self) -> None:
        route = self._audio_route()
        if not route:
            return
        self.workspace.clear_audio_route(route)
        ((self.workspace.state.get("library_assets") or {}).get("audio") or {}).pop(route, None)
        self.workspace.save()
        self.dirty = True
        self._reload_audio_rows()

    def _selected_slot(self) -> str | None:
        selected = self.tree.selection()
        return selected[0] if selected else None

    def _choose_asset(self) -> None:
        slot = self._selected_slot()
        if not slot:
            return
        records = [record for record in self.app.asset_library.assets.values() if record.get("type") in {"character_source", "background", "small_icon", "icon_source", "derived_image", "other_image"}]
        if not records:
            messagebox.showinfo("一级素材为空", "请先在一级素材管理或皮肤制作中导入图像。", parent=self.window)
            return
        choice = AssetChoiceDialog(self.app, self.window, "选择一级素材", records).show()
        if choice:
            self._attach_asset(slot, choice)

    def _attach_asset(self, slot: str, record: dict) -> None:
        files = self.app.asset_library.record_files(record)
        source = next((path for path in files if path.suffix.casefold() in SUPPORTED_IMAGE_EXTENSIONS), None)
        if not source:
            messagebox.showerror("素材不可用", "该一级素材没有可用图像文件。", parent=self.window)
            return
        self.workspace.import_visual(slot, source)
        refs = self.workspace.state.setdefault("library_assets", {}).setdefault("visual_slots", {})
        refs[slot] = record["id"]
        self.workspace.save()
        self.dirty = True
        self._reload_row(slot)

    def _import(self) -> None:
        slot = self._selected_slot()
        if not slot:
            return
        selected = filedialog.askopenfilename(parent=self.window, filetypes=(("Images", "*.png *.jpg *.jpeg *.webp *.bmp"),))
        if not selected:
            return
        source = Path(selected)
        asset_type = "small_icon" if slot == "hero_icon_small" else "other_image"
        record = self.app.asset_library.import_file(source, asset_type=asset_type)
        self._attach_asset(slot, record)

    def _paste(self) -> None:
        slot = self._selected_slot()
        if not slot:
            return
        clipboard = ImageGrab.grabclipboard()
        if isinstance(clipboard, Image.Image):
            destination = manager_root() / "clipboard" / f"{slot}.png"
            destination.parent.mkdir(parents=True, exist_ok=True)
            clipboard.convert("RGBA").save(destination)
            record = self.app.asset_library.import_file(destination, asset_type="small_icon" if slot == "hero_icon_small" else "other_image")
            self._attach_asset(slot, record)
        else:
            messagebox.showinfo("剪贴板无图片", "剪贴板中没有可用图片。", parent=self.window)

    def _clear_slot(self) -> None:
        slot = self._selected_slot()
        if not slot:
            return
        self.workspace.clear_visual(slot)
        ((self.workspace.state.get("library_assets") or {}).get("visual_slots") or {}).pop(slot, None)
        self.workspace.save()
        self.dirty = True
        self._reload_row(slot)

    def _reload_row(self, slot: str) -> None:
        path = self.workspace.visual_path(slot)
        ref = (((self.workspace.state.get("library_assets") or {}).get("visual_slots") or {}).get(slot) or "—")
        current = list(self.tree.item(slot, "values"))
        self.tree.item(slot, values=(current[0], path.name if path else "使用原版", ref))
        self._visual_selection_changed()

    def _clear_all(self) -> None:
        refs = self.workspace.state.get("library_assets") or {}
        reference_count = sum(len(refs.get(group) or {}) for group in ("inputs", "visual_slots", "audio")) + (1 if refs.get("animation") else 0)
        if not messagebox.askyesno("清空全部引用", f"清空当前皮肤包的全部图像、音频和动画引用吗？\n\n将清除 {reference_count} 个一级素材引用；一级素材本体保留。", parent=self.window):
            return
        self.workspace.clear_loaded_assets()
        self.workspace.state["library_assets"] = {"inputs": {}, "visual_slots": {}, "audio": {}, "animation": None}
        self.workspace.save()
        self.dirty = True
        for slot in self.tree.get_children():
            self._reload_row(slot)
        self._reload_audio_rows()

    def _save(self) -> None:
        try:
            self._store_spine_placement()
        except (tk.TclError, ValueError) as error:
            messagebox.showerror("Spine 参数错误", str(error), parent=self.window)
            return
        pack = self.workspace.state.get("pack") or {}
        self.workspace.set_pack_metadata(pack_id=pack.get("id") or "local.skin", name=self.name_var.get(), version=self.version_var.get())
        atomic_copy_tree(self.workspace.directory, self.original_workspace.directory)
        saved = StudioWorkspace.load(self.original_workspace.directory)
        self.app.workspaces[self.original_path] = saved
        self.dirty = False
        self.app._refresh_everything()
        self._destroy()

    def _close_requested(self) -> None:
        if self.dirty:
            choice = messagebox.askyesnocancel(
                "保存皮肤包修改",
                "当前皮肤包有未保存修改。\n\n是：保存并离开\n否：放弃修改\n取消：继续编辑",
                parent=self.window,
            )
            if choice is None:
                return
            if choice:
                self._save()
                return
        self._destroy()

    def _destroy(self) -> None:
        try:
            self.window.grab_release()
        except tk.TclError:
            pass
        self.window.destroy()
        self._draft_directory.cleanup()


class AssetMetadataDialog:
    def __init__(self, app: SkinManagerV12, record: dict) -> None:
        self.app = app
        self.record = record
        self.window = tk.Toplevel(app.root)
        self.window.title("编辑一级素材信息")

    def show(self) -> None:
        frame = ttk.Frame(self.window, padding=16)
        frame.pack(fill="both", expand=True)
        metadata = self.record.get("metadata") or {}
        self.variables = {
            "name": tk.StringVar(value=self.record.get("name") or ""),
            "author": tk.StringVar(value=metadata.get("author") or ""),
            "license": tk.StringVar(value=metadata.get("license") or ""),
            "source_url": tk.StringVar(value=metadata.get("source_url") or ""),
            "notes": tk.StringVar(value=metadata.get("notes") or ""),
        }
        for row, (key, label) in enumerate(
            (
                ("name", "名称"),
                ("author", "作者"),
                ("license", "许可 / 来源"),
                ("source_url", "来源链接"),
                ("notes", "备注"),
            )
        ):
            ttk.Label(frame, text=label, width=12).grid(row=row, column=0, sticky="w", pady=5)
            ttk.Entry(frame, textvariable=self.variables[key], width=64).grid(
                row=row, column=1, sticky="ew", pady=5
            )
        frame.columnconfigure(1, weight=1)
        ttk.Label(
            frame,
            text=f"稳定 ID：{self.record.get('id')}\n受管文件不会因改名而移动。",
            style="Muted.TLabel",
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(10, 12))
        actions = ttk.Frame(frame)
        actions.grid(row=6, column=0, columnspan=2, sticky="ew")
        ttk.Button(actions, text="保存", style="Accent.TButton", command=self._save).pack(side="left")
        ttk.Button(actions, text="取消", command=self.window.destroy).pack(side="left", padx=(8, 0))
        self.window.transient(self.app.root)
        self.window.grab_set()

    def _save(self) -> None:
        if not self.variables["name"].get().strip():
            messagebox.showerror("名称不能为空", "请输入一级素材名称。", parent=self.window)
            return
        self.app.asset_library.update_metadata(
            self.record["id"],
            **{key: variable.get() for key, variable in self.variables.items()},
        )
        self.window.destroy()
        self.app._refresh_asset_gallery()


class AssetUseDialog:
    def __init__(self, app: SkinManagerV12, record: dict) -> None:
        self.app = app
        self.record = record
        self.window = tk.Toplevel(app.root)
        self.window.title("将一级素材用于皮肤")

    def show(self) -> None:
        frame = ttk.Frame(self.window, padding=16)
        frame.pack(fill="both", expand=True)
        pack_labels = {
            f"{(workspace.state.get('pack') or {}).get('name')} · {(workspace.state.get('pack') or {}).get('id')}": workspace
            for workspace in self.app.workspaces.values()
        }
        self.pack_var = tk.StringVar(value=next(iter(pack_labels), ""))
        self.pack_labels = pack_labels
        self.slot_var = tk.StringVar(value="hero_icon_small" if self.record.get("type") == "small_icon" else "standing_overlay")
        ttk.Label(frame, text=f"一级素材：{self.record.get('name')}", font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w", pady=(0, 12))
        ttk.Label(frame, text="皮肤包").pack(anchor="w")
        pack_box = ttk.Combobox(frame, textvariable=self.pack_var, values=tuple(pack_labels), state="readonly", width=55)
        pack_box.pack(fill="x", pady=(4, 10))
        pack_box.bind("<<ComboboxSelected>>", lambda _e: self._refresh_slots())
        ttk.Label(frame, text="目标槽位").pack(anchor="w")
        self.slot_box = ttk.Combobox(frame, textvariable=self.slot_var, state="readonly", width=55)
        self.slot_box.pack(fill="x", pady=(4, 12))
        self._refresh_slots()
        ttk.Button(frame, text="建立引用", style="Accent.TButton", command=self._apply).pack(side="left")
        ttk.Button(frame, text="取消", command=self.window.destroy).pack(side="left", padx=(8, 0))
        self.window.transient(self.app.root)
        self.window.grab_set()

    def _refresh_slots(self) -> None:
        workspace = self.pack_labels.get(self.pack_var.get())
        asset_type = self.record.get("type")
        if asset_type == "spine":
            values = ("animation",)
        elif asset_type == "audio" and workspace is not None:
            values = tuple(route["logical_slot"] for route in workspace.audio_route_catalog())
        else:
            values = tuple(slot["id"] for slot in self.app.catalog.get("visual_slots") or [])
        self.slot_box.configure(values=values)
        if self.slot_var.get() not in values:
            self.slot_var.set(values[0] if values else "")

    def _apply(self) -> None:
        workspace = self.pack_labels.get(self.pack_var.get())
        if not workspace:
            return
        files = self.app.asset_library.record_files(self.record)
        source = next((path for path in files if path.suffix.casefold() in SUPPORTED_IMAGE_EXTENSIONS), None)
        if self.record.get("type") == "spine":
            workspace.import_animation(files, "spine")
            refs = workspace.state.setdefault("library_assets", {})
            refs["animation"] = self.record["id"]
            workspace.save()
        elif self.record.get("type") == "audio":
            sources = [
                path
                for path in files
                if path.suffix.casefold() in {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac", ".opus"}
            ]
            if not sources or not self.slot_var.get():
                messagebox.showerror("不能建立引用", "该素材没有可用音频文件或目标音频路由。", parent=self.window)
                return
            workspace.clear_audio_route(self.slot_var.get())
            for source in sources:
                workspace.import_audio(self.slot_var.get(), source)
            refs = workspace.state.setdefault("library_assets", {}).setdefault("audio", {})
            refs[self.slot_var.get()] = self.record["id"]
            workspace.save()
        elif source:
            workspace.import_visual(self.slot_var.get(), source)
            refs = workspace.state.setdefault("library_assets", {}).setdefault("visual_slots", {})
            refs[self.slot_var.get()] = self.record["id"]
            workspace.save()
        else:
            messagebox.showerror("不能建立引用", "该素材类型暂时不能用于图像槽位。", parent=self.window)
            return
        self.window.destroy()
        self.app._refresh_everything()


class ChoiceDialog:
    def __init__(self, parent: tk.Misc, title: str, values: tuple[str, ...]) -> None:
        self.parent = parent
        self.values = values
        self.result: str | None = None
        self.window = tk.Toplevel(parent)
        self.window.title(title)

    def show(self) -> str | None:
        frame = ttk.Frame(self.window, padding=14)
        frame.pack(fill="both", expand=True)
        self.var = tk.StringVar(value=self.values[0] if self.values else "")
        box = ttk.Combobox(frame, textvariable=self.var, values=self.values, state="readonly", width=70)
        box.pack(fill="x")
        ttk.Button(frame, text="选择", style="Accent.TButton", command=self._accept).pack(side="left", pady=(12, 0))
        ttk.Button(frame, text="取消", command=self.window.destroy).pack(side="left", padx=(8, 0), pady=(12, 0))
        self.window.transient(self.parent.winfo_toplevel())
        self.window.grab_set()
        self.window.wait_window()
        return self.result

    def _accept(self) -> None:
        self.result = self.var.get()
        self.window.destroy()


class AssetChoiceDialog:
    """Image-first picker for reusable primary assets."""

    def __init__(
        self,
        app: SkinManagerV12,
        parent: tk.Misc,
        title: str,
        records: list[dict],
    ) -> None:
        self.app = app
        self.parent = parent
        self.records = sorted(
            records,
            key=lambda record: str(record.get("name") or record.get("id") or "").casefold(),
        )
        self.result: dict | None = None
        self.window = tk.Toplevel(parent)
        self.window.title(title)
        self.window.geometry("760x430")

    def show(self) -> dict | None:
        frame = ttk.Frame(self.window, padding=14)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.columnconfigure(1, weight=1)
        frame.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            frame,
            columns=("name", "type"),
            show="headings",
            selectmode="browse",
        )
        self.tree.heading("name", text="素材")
        self.tree.heading("type", text="类型")
        self.tree.column("name", width=260, anchor="w")
        self.tree.column("type", width=120, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        self.preview = tk.Label(frame, bg=COLORS["empty"])
        self.preview.grid(row=0, column=1, sticky="nsew")
        for index, record in enumerate(self.records):
            self.tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    record.get("name") or record.get("id"),
                    TYPE_NAMES.get(record.get("type"), record.get("type")),
                ),
            )
        self.tree.bind("<<TreeviewSelect>>", self._selection_changed)
        self.tree.bind("<Double-1>", lambda _event: self._accept())
        buttons = ttk.Frame(frame)
        buttons.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Button(buttons, text="选择", style="Accent.TButton", command=self._accept).pack(side="left")
        ttk.Button(buttons, text="取消", command=self.window.destroy).pack(side="left", padx=(8, 0))
        if self.records:
            self.tree.selection_set("0")
            self.tree.focus("0")
            self._selection_changed()
        self.window.transient(self.parent.winfo_toplevel())
        self.window.grab_set()
        self.window.wait_window()
        return self.result

    def _selected_record(self) -> dict | None:
        selected = self.tree.selection()
        return self.records[int(selected[0])] if selected else None

    def _selection_changed(self, _event: tk.Event | None = None) -> None:
        record = self._selected_record()
        if record is not None:
            self.app._set_asset_preview(
                self.preview,
                record,
                (330, 330),
                f"asset-choice-{id(self)}",
            )

    def _accept(self) -> None:
        self.result = self._selected_record()
        if self.result is not None:
            self.window.destroy()


def main() -> int:
    app = SkinManagerV12()
    if "--self-test" in os.sys.argv:
        try:
            app.self_test_layout()
            return 0
        except Exception:
            return 2
        finally:
            app.root.destroy()
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
