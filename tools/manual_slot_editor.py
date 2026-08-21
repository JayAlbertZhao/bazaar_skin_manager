#!/usr/bin/env python3
"""Manual per-slot authoring surface for the integrated skin manager."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
import queue
import shutil
import threading
import time
import traceback
from typing import Callable
import uuid
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageDraw, ImageTk

from asset_generator_core import authoring_adapters, ensure_local_badge_assets
from badge_pipeline import compose_badge
from bazaar_skin_manager import manager_root
from mod_studio_core import (
    PROJECT_ROOT,
    SUPPORTED_IMAGE_EXTENSIONS,
    WORKSPACES_ROOT,
    StudioWorkspace,
    export_original_visual_reference,
)
from skin_pack_builder import _load_badge_template, apply_declared_clip_mask
from support_report import append_error_log


COLORS = {
    "panel": "#171d27",
    "panel_alt": "#1d2531",
    "line": "#2b3545",
    "text": "#eef2f7",
    "muted": "#94a1b2",
    "accent": "#65d4b3",
    "danger": "#ef7b7b",
    "empty": "#242d3a",
}

MANUAL_AUTHORING_SCHEMA = "bazaar-manual-slots/v1"
PORTRAIT_PAIR_SLOT = "portrait_gameplay"
PORTRAIT_BACKGROUND_SLOT = "portrait_background"
PORTRAIT_PREVIEW_SLOTS = {PORTRAIT_PAIR_SLOT, "portrait_small"}
HERO_SELECT_SLOT = "hero_select"
STANDING_SLOT = "standing_overlay"
BADGE_TEMPLATE_ROOT = PROJECT_ROOT / "manager" / "assets"
LAYER_LABELS = {"direct": "成品图", "background": "背景", "character": "人物"}
LAYER_IDS = {value: key for key, value in LAYER_LABELS.items()}


@dataclass
class LayerState:
    path: str = ""
    x: int = 0
    y: int = 0
    scale: float = 1.0

    @classmethod
    def from_dict(cls, payload: dict | None, workspace: Path | None = None) -> "LayerState":
        payload = payload or {}
        path = str(payload.get("path") or payload.get("workspace_file") or "")
        if path and workspace is not None and not Path(path).is_absolute():
            path = str((workspace / path).resolve())
        return cls(
            path=path,
            x=int(payload.get("x") or 0),
            y=int(payload.get("y") or 0),
            scale=float(payload.get("scale") or 1.0),
        )

    def to_dict(self, workspace_file: str | None = None) -> dict:
        result = {
            "x": int(self.x),
            "y": int(self.y),
            "scale": round(float(self.scale), 4),
        }
        if workspace_file:
            result["workspace_file"] = workspace_file
        return result


@dataclass
class SlotState:
    mode: str = "direct"
    direct: LayerState = field(default_factory=LayerState)
    background: LayerState = field(default_factory=LayerState)
    character: LayerState = field(default_factory=LayerState)

    @classmethod
    def from_dict(cls, payload: dict | None, workspace: Path | None = None) -> "SlotState":
        payload = payload or {}
        return cls(
            mode="layered" if payload.get("mode") == "layered" else "direct",
            direct=LayerState.from_dict(payload.get("direct"), workspace),
            background=LayerState.from_dict(payload.get("background"), workspace),
            character=LayerState.from_dict(payload.get("character"), workspace),
        )

    def layer(self, layer_id: str) -> LayerState:
        return getattr(self, layer_id)

    def has_input(self) -> bool:
        if self.mode == "layered":
            return bool(self.background.path or self.character.path)
        return bool(self.direct.path)


@dataclass
class ManualBuildSnapshot:
    pack_id: str
    name: str
    version: str
    hero: str
    skin: str
    skin_name_contains: str
    slot_states: dict[str, SlotState]
    slot_sizes: dict[str, tuple[int, int]]
    automatic_authoring: dict
    dirty_slots: set[str]
    workspace: "StudioWorkspace | None"


def _resolved_output(recipe: dict, slot: str) -> dict:
    outputs = recipe.get("outputs") or {}

    def resolve(current_slot: str, seen: set[str]) -> dict:
        if current_slot in seen:
            raise ValueError(f"循环槽位别名：{slot}")
        current = dict(outputs.get(current_slot) or {})
        alias = current.pop("alias_of", None)
        if not alias:
            return current
        inherited = resolve(str(alias), seen | {current_slot})
        inherited.update(current)
        return inherited

    return resolve(slot, set())


def _fit_layer(
    source: Image.Image,
    size: tuple[int, int],
    layer: LayerState,
    *,
    fit: str,
    anchor_bottom: bool = False,
) -> Image.Image:
    source = source.convert("RGBA")
    if fit == "cover":
        base = max(size[0] / source.width, size[1] / source.height)
    else:
        base = min(size[0] / source.width, size[1] / source.height)
    scale = base * max(0.25, min(4.0, float(layer.scale)))
    resized = source.resize(
        (
            max(1, round(source.width * scale)),
            max(1, round(source.height * scale)),
        ),
        Image.Resampling.LANCZOS,
    )
    x = round((size[0] - resized.width) / 2) + int(layer.x)
    y = (
        size[1] - resized.height + int(layer.y)
        if anchor_bottom
        else round((size[1] - resized.height) / 2) + int(layer.y)
    )
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    canvas.alpha_composite(resized, (x, y))
    return canvas


def automatic_draft_slot_states(
    workspace: StudioWorkspace,
    slots: list[str] | tuple[str, ...] | set[str],
    background_slots: set[str],
    template_slots: set[str] | None = None,
) -> dict[str, SlotState]:
    """Map generated outputs and archived inputs into one per-slot draft cache."""

    states = {slot: SlotState() for slot in slots}
    authoring_inputs = ((workspace.state.get("authoring") or {}).get("inputs") or {})

    def input_path(input_name: str) -> str:
        relative = str(
            (authoring_inputs.get(input_name) or {}).get("workspace_file") or ""
        ).strip()
        if not relative:
            return ""
        candidate = (workspace.directory / relative).resolve()
        return str(candidate) if candidate.is_file() else ""

    shared_background = input_path("background")
    shared_character = input_path("character")
    template_slots = set(template_slots or ())
    for slot, state in states.items():
        generated = workspace.visual_path(slot)
        if generated is not None:
            state.direct.path = str(generated)
        if slot in background_slots:
            state.background.path = shared_background
            state.character.path = shared_character
        if slot in template_slots and shared_character:
            # Template-driven slots keep the generated bitmap as their direct
            # fallback, but expose the archived source character as the
            # editable internal layer.
            state.character.path = shared_character
            state.mode = "layered"

    portrait = states.get(PORTRAIT_PAIR_SLOT)
    portrait_foreground = workspace.visual_path(PORTRAIT_PAIR_SLOT)
    portrait_background = workspace.visual_path(PORTRAIT_BACKGROUND_SLOT)
    if portrait is not None and portrait_foreground is not None:
        portrait.character.path = str(portrait_foreground)
        if portrait_background is not None:
            portrait.background.path = str(portrait_background)
            portrait.mode = "layered"
    return states


def render_layered_badge(
    source: Image.Image,
    *,
    output_recipe: dict,
    layer: LayerState,
    template_root: Path = BADGE_TEMPLATE_ROOT,
) -> Image.Image:
    """Render a badge while keeping its authored frame layers immutable."""

    template_images, _metadata = _load_badge_template(
        output_recipe["template"], template_root=template_root
    )
    crop = output_recipe.get("character_crop", [0.0, 0.0, 1.0, 1.0])
    crop_box = (
        round(float(crop[0]) * source.width),
        round(float(crop[1]) * source.height),
        round(float(crop[2]) * source.width),
        round(float(crop[3]) * source.height),
    )
    character = source.convert("RGBA").crop(crop_box)
    declared = tuple(int(value) for value in output_recipe["target_alpha_bounds"])
    anchor = tuple(float(value) for value in output_recipe.get("anchor", [0.5, 1.0]))
    factor = max(0.25, min(4.0, float(layer.scale)))
    left, top, right, bottom = declared
    width = right - left
    height = bottom - top
    anchor_x = left + width * anchor[0]
    anchor_y = top + height * anchor[1]
    scaled_width = width * factor
    scaled_height = height * factor
    bounds = (
        round(anchor_x - scaled_width * anchor[0]),
        round(anchor_y - scaled_height * anchor[1]),
        round(anchor_x + scaled_width * (1.0 - anchor[0])),
        round(anchor_y + scaled_height * (1.0 - anchor[1])),
    )
    output_size = tuple(int(value) for value in output_recipe["size"])
    template_size = template_images["base"].size
    dx = round(int(layer.x) * template_size[0] / output_size[0])
    dy = round(int(layer.y) * template_size[1] / output_size[1])
    shifted = (
        bounds[0] + dx,
        bounds[1] + dy,
        bounds[2] + dx,
        bounds[3] + dy,
    )
    return compose_badge(
        character,
        base=template_images["base"],
        frame_upper=template_images["frame_upper"],
        frame_lower=template_images["frame_lower"],
        frame_lower_occlusion=template_images["frame_lower_occlusion"],
        target_bounds=shifted,
        output_size=output_size,
    )


def portrait_frame_preview_overlay(size: tuple[int, int], declaration: dict) -> Image.Image:
    """Draw the native three-sided portrait occlusion above the preview art.

    The game owns the final animated frame.  This deterministic overlay uses
    the same verified inner edge and layer order without baking game UI pixels
    into the exported portrait textures.
    """

    reference = tuple(int(value) for value in declaration.get("reference_size", size))
    bounds = tuple(int(value) for value in declaration.get("inner_bounds", ()))
    if len(reference) != 2 or len(bounds) != 4:
        return Image.new("RGBA", size, (0, 0, 0, 0))
    sx = size[0] / reference[0]
    sy = size[1] / reference[1]
    left = round(bounds[0] * sx)
    right = round(bounds[2] * sx)
    bottom = round(bounds[3] * sy)
    radius = max(1, round(int(declaration.get("bottom_corner_radius", 0)) * min(sx, sy)))
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    panel = (54, 31, 19, 232)
    dark_edge = (43, 23, 13, 255)
    gold = (224, 174, 65, 255)
    highlight = (255, 224, 132, 230)
    draw.rectangle((0, 0, left - 1, size[1]), fill=panel)
    draw.rectangle((right + 1, 0, size[0], size[1]), fill=panel)
    draw.rectangle((0, bottom + 1, size[0], size[1]), fill=panel)
    # Only left, bottom and right are door panels.  The top deliberately stays
    # open so hair, hats and props can cross the frame just as they do in-game.
    inner_path = [
        (left, 0),
        (left, bottom - radius),
        (left + radius, bottom),
        (right - radius, bottom),
        (right, bottom - radius),
        (right, 0),
    ]
    draw.line(inner_path, fill=dark_edge, width=max(5, round(9 * min(sx, sy))), joint="curve")
    draw.line(inner_path, fill=gold, width=max(3, round(6 * min(sx, sy))), joint="curve")
    draw.line(inner_path, fill=highlight, width=max(1, round(2 * min(sx, sy))), joint="curve")
    return overlay


class ManualSlotEditor:
    """Create complete packs by supplying each logical image slot manually."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        catalog: dict,
        on_import: Callable[[StudioWorkspace], None],
        on_choose_asset: Callable[[], Path | None] | None = None,
        game_dir_provider: Callable[[], Path | None] | None = None,
    ) -> None:
        self.root = parent.winfo_toplevel()
        self.host = parent
        self.catalog = catalog
        self.on_import = on_import
        self.on_choose_asset = on_choose_asset
        self.game_dir_provider = game_dir_provider
        self.adapters = authoring_adapters()
        self.adapter_labels = {
            f"{record.hero} · {record.skin}": record.adapter_id
            for record in self.adapters
        }
        self.adapter_by_id = {record.adapter_id: record for record in self.adapters}
        self.slot_names = {
            str(record["id"]): str(record.get("name") or record["id"])
            for record in catalog.get("visual_slots") or []
        }
        self.slot_states: dict[str, SlotState] = {}
        self.slot_sizes: dict[str, tuple[int, int]] = {}
        self.background_slots: set[str] = set()
        self.template_slots: set[str] = set()
        self.output_recipes: dict[str, dict] = {}
        self.badge_template_root = ensure_local_badge_assets(
            manager_root() / "asset-generator" / "current" / "resources" / "manager-assets",
            source_root=BADGE_TEMPLATE_ROOT,
        )
        self.current_slot = ""
        self.current_layer = "direct"
        self.editing_workspace: StudioWorkspace | None = None
        self.automatic_authoring: dict = {}
        self.pack_id = ""
        self.preview_photo: ImageTk.PhotoImage | None = None
        self.original_preview_photo: ImageTk.PhotoImage | None = None
        self.original_reference_image: Image.Image | None = None
        self.original_reference_status = "选择槽位后读取原版"
        self._original_reference_request = 0
        self._original_reference_memory: dict[tuple[str, str, str], Image.Image] = {}
        self.preview_scale = 1.0
        self.drag_origin: tuple[int, int, int, int] | None = None
        self._loading_controls = False
        self.dirty_slots: set[str] = set()
        self.slot_preview_cache: dict[str, Image.Image] = {}
        self.build_events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.build_busy = False
        self.build_buttons: list[ttk.Button] = []
        self._build_ui()
        self.new_project()
        self.root.after(100, self._poll_build_events)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.host, padding=12)
        outer.pack(fill="both", expand=True)

        heading = ttk.Frame(outer)
        heading.pack(fill="x", pady=(0, 10))
        ttk.Label(
            heading,
            text="逐槽位模式",
            font=("Microsoft YaHei UI", 16, "bold"),
        ).pack(side="left")
        ttk.Label(
            heading,
            text="逐槽位导入成品图；支持背景/人物分层的槽位可分别调整两层。",
            foreground=COLORS["muted"],
        ).pack(side="left", padx=(14, 0), pady=(5, 0))
        new_button = ttk.Button(heading, text="新建项目", command=self.new_project)
        new_button.pack(side="right")
        self.build_buttons.append(new_button)
        export_button = ttk.Button(heading, text="导出 ZIP…", command=self._export)
        export_button.pack(side="right", padx=(0, 8))
        self.build_buttons.append(export_button)
        import_button = ttk.Button(
            heading,
            text="导入到皮肤库",
            style="Accent.TButton",
            command=self._import,
        )
        import_button.pack(side="right", padx=(0, 8))
        self.build_buttons.append(import_button)

        project = ttk.LabelFrame(outer, text="皮肤包", padding=10)
        project.pack(fill="x", pady=(0, 10))
        self.target_var = tk.StringVar()
        self.name_var = tk.StringVar()
        self.version_var = tk.StringVar(value="0.1.0")
        ttk.Label(project, text="生成目标").grid(row=0, column=0, sticky="w")
        self.target_box = ttk.Combobox(
            project,
            textvariable=self.target_var,
            values=tuple(self.adapter_labels),
            state="readonly",
            width=34,
        )
        self.target_box.grid(row=0, column=1, sticky="ew", padx=(8, 14))
        self.target_box.bind("<<ComboboxSelected>>", self._target_changed)
        ttk.Label(project, text="名称").grid(row=0, column=2, sticky="w")
        ttk.Entry(project, textvariable=self.name_var, width=28).grid(
            row=0, column=3, sticky="ew", padx=(8, 14)
        )
        ttk.Label(project, text="版本").grid(row=0, column=4, sticky="w")
        ttk.Entry(project, textvariable=self.version_var, width=10).grid(
            row=0, column=5, sticky="ew", padx=(8, 0)
        )
        project.columnconfigure(1, weight=1)
        project.columnconfigure(3, weight=1)

        body = ttk.Panedwindow(outer, orient="horizontal")
        body.pack(fill="both", expand=True)
        slots_panel = ttk.Frame(body, padding=8)
        editor = ttk.Frame(body, padding=10)
        body.add(slots_panel, weight=0)
        body.add(editor, weight=1)

        ttk.Label(
            slots_panel,
            text="槽位列表",
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(anchor="w", pady=(0, 6))
        self.slot_tree = ttk.Treeview(
            slots_panel,
            columns=("state",),
            show="tree headings",
            selectmode="browse",
            height=18,
        )
        self.slot_tree.heading("#0", text="槽位")
        self.slot_tree.heading("state", text="素材")
        self.slot_tree.column("#0", width=170, stretch=True)
        self.slot_tree.column("state", width=76, anchor="center", stretch=False)
        slot_scroll = ttk.Scrollbar(slots_panel, orient="vertical", command=self.slot_tree.yview)
        self.slot_tree.configure(yscrollcommand=slot_scroll.set)
        self.slot_tree.pack(side="left", fill="both", expand=True)
        slot_scroll.pack(side="right", fill="y")
        self.slot_tree.bind("<<TreeviewSelect>>", self._slot_selected)

        editor.columnconfigure(0, weight=1)
        editor.rowconfigure(4, weight=1)
        top = ttk.Frame(editor)
        top.grid(row=0, column=0, sticky="ew")
        self.slot_title_var = tk.StringVar(value="选择一个槽位")
        ttk.Label(
            top,
            textvariable=self.slot_title_var,
            font=("Microsoft YaHei UI", 13, "bold"),
        ).pack(side="left")
        self.size_var = tk.StringVar()
        ttk.Label(top, textvariable=self.size_var, foreground=COLORS["muted"]).pack(
            side="left", padx=(12, 0)
        )
        ttk.Button(top, text="清空当前槽位", command=self._clear_slot).pack(side="right")

        mode = ttk.Frame(editor)
        mode.grid(row=1, column=0, sticky="ew", pady=(8, 4))
        self.mode_var = tk.StringVar(value="direct")
        self.direct_radio = ttk.Radiobutton(
            mode,
            text="导入槽位成品图",
            value="direct",
            variable=self.mode_var,
            command=self._mode_changed,
        )
        self.direct_radio.pack(side="left")
        self.layered_radio = ttk.Radiobutton(
            mode,
            text="背景 + 人物分层合成",
            value="layered",
            variable=self.mode_var,
            command=self._mode_changed,
        )
        self.layered_radio.pack(side="left", padx=(16, 0))

        inputs = ttk.LabelFrame(editor, text="当前槽位素材", padding=8)
        inputs.grid(row=2, column=0, sticky="ew", pady=(2, 7))
        inputs.columnconfigure(1, weight=1)
        self.path_vars = {
            "direct": tk.StringVar(),
            "background": tk.StringVar(),
            "character": tk.StringVar(),
        }
        self.path_rows: dict[str, list[tk.Widget]] = {}
        for row, layer_id in enumerate(("direct", "background", "character")):
            widgets: list[tk.Widget] = []
            label = ttk.Label(inputs, text=LAYER_LABELS[layer_id])
            label.grid(row=row, column=0, sticky="w", pady=3)
            widgets.append(label)
            entry = ttk.Entry(inputs, textvariable=self.path_vars[layer_id])
            entry.grid(row=row, column=1, sticky="ew", padx=(8, 6), pady=3)
            entry.bind(
                "<Return>",
                lambda _event, selected=layer_id: self._path_entry_changed(selected),
            )
            entry.bind(
                "<FocusOut>",
                lambda _event, selected=layer_id: self._path_entry_changed(selected),
            )
            widgets.append(entry)
            browse = ttk.Button(
                inputs,
                text="导入…",
                command=lambda selected=layer_id: self._browse_layer(selected),
            )
            browse.grid(row=row, column=2, pady=3)
            widgets.append(browse)
            if self.on_choose_asset is not None:
                library = ttk.Button(
                    inputs,
                    text="素材库",
                    command=lambda selected=layer_id: self._choose_layer_asset(selected),
                )
                library.grid(row=row, column=3, padx=(6, 0), pady=3)
                widgets.append(library)
            clear = ttk.Button(
                inputs,
                text="清空",
                command=lambda selected=layer_id: self._clear_layer(selected),
            )
            clear.grid(row=row, column=4, padx=(6, 0), pady=3)
            widgets.append(clear)
            self.path_rows[layer_id] = widgets

        controls = ttk.LabelFrame(editor, text="当前层位置与缩放", padding=8)
        controls.grid(row=3, column=0, sticky="ew", pady=(0, 7))
        self.layer_var = tk.StringVar(value=LAYER_LABELS["direct"])
        self.layer_box = ttk.Combobox(
            controls,
            textvariable=self.layer_var,
            state="readonly",
            width=10,
        )
        self.layer_box.grid(row=0, column=0, padx=(0, 12))
        self.layer_box.bind("<<ComboboxSelected>>", self._layer_changed)
        self.x_var = tk.IntVar(value=0)
        self.y_var = tk.IntVar(value=0)
        self.scale_var = tk.IntVar(value=100)
        for column, (label, variable, low, high) in enumerate(
            (("X", self.x_var, -8192, 8192), ("Y", self.y_var, -8192, 8192)),
            start=1,
        ):
            ttk.Label(controls, text=label).grid(row=0, column=column * 2 - 1, sticky="e")
            box = ttk.Spinbox(
                controls,
                from_=low,
                to=high,
                width=7,
                textvariable=variable,
                command=self._transform_changed,
            )
            box.grid(row=0, column=column * 2, padx=(4, 10))
            box.bind("<Return>", self._transform_changed)
            box.bind("<FocusOut>", self._transform_changed)
        ttk.Label(controls, text="缩放").grid(row=0, column=5, sticky="e")
        scale = ttk.Spinbox(
            controls,
            from_=25,
            to=400,
            increment=5,
            width=7,
            textvariable=self.scale_var,
            command=self._transform_changed,
        )
        scale.grid(row=0, column=6, padx=(4, 2))
        scale.bind("<Return>", self._transform_changed)
        scale.bind("<FocusOut>", self._transform_changed)
        ttk.Label(controls, text="%").grid(row=0, column=7, sticky="w")
        ttk.Button(controls, text="重置当前层", command=self._reset_transform).grid(
            row=0, column=8, padx=(14, 0)
        )

        previews = ttk.Frame(editor)
        previews.grid(row=4, column=0, sticky="nsew")
        previews.columnconfigure(0, weight=1, uniform="slot-preview")
        previews.columnconfigure(1, weight=1, uniform="slot-preview")
        previews.rowconfigure(1, weight=1)
        ttk.Label(
            previews,
            text="当前调整（可拖动）",
            foreground=COLORS["muted"],
        ).grid(row=0, column=0, sticky="w", pady=(0, 5))
        self.original_reference_title_var = tk.StringVar(
            value="游戏原版参考（同画布 / 同比例）"
        )
        ttk.Label(
            previews,
            textvariable=self.original_reference_title_var,
            foreground=COLORS["muted"],
        ).grid(row=0, column=1, sticky="w", padx=(8, 0), pady=(0, 5))

        self.canvas = tk.Canvas(
            previews,
            bg="#0d1219",
            highlightthickness=1,
            highlightbackground=COLORS["line"],
            cursor="fleur",
        )
        self.canvas.grid(row=1, column=0, sticky="nsew", padx=(0, 4))
        self.canvas.bind("<Configure>", lambda _event: self._render_preview())
        self.canvas.bind("<ButtonPress-1>", self._drag_start)
        self.canvas.bind("<B1-Motion>", self._drag_motion)
        self.canvas.bind("<ButtonRelease-1>", self._drag_end)
        self.original_canvas = tk.Canvas(
            previews,
            bg="#0d1219",
            highlightthickness=1,
            highlightbackground=COLORS["line"],
        )
        self.original_canvas.grid(row=1, column=1, sticky="nsew", padx=(4, 0))
        self.original_canvas.bind(
            "<Configure>", lambda _event: self._render_original_preview()
        )

        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(10, 0))
        self.status_var = tk.StringVar(value="逐槽位模式等待输入")
        ttk.Label(footer, textvariable=self.status_var, foreground=COLORS["muted"]).pack(
            side="left"
        )
        footer_export = ttk.Button(footer, text="导出 ZIP…", command=self._export)
        footer_export.pack(side="right")
        self.build_buttons.append(footer_export)
        footer_import = ttk.Button(
            footer,
            text="导入到皮肤库",
            style="Accent.TButton",
            command=self._import,
        )
        footer_import.pack(side="right", padx=(0, 8))
        self.build_buttons.append(footer_import)

    def _adapter(self):
        adapter_id = self.adapter_labels.get(self.target_var.get())
        if not adapter_id:
            raise ValueError("请选择生成目标。")
        return self.adapter_by_id[adapter_id]

    def _slot_contracts(self) -> tuple[dict[str, tuple[int, int]], set[str]]:
        record = self._adapter()
        recipe = record.payload.get("authoring_recipe") or {}
        sizes: dict[str, tuple[int, int]] = {}
        background_slots: set[str] = {PORTRAIT_PAIR_SLOT}
        self.output_recipes = {}
        self.template_slots = set()
        for slot in self.slot_names:
            output = _resolved_output(recipe, slot)
            self.output_recipes[slot] = output
            size = output.get("size")
            if isinstance(size, list) and len(size) == 2:
                sizes[slot] = (int(size[0]), int(size[1]))
            dependencies = {str(value) for value in output.get("depends_on") or []}
            if {"background", "character"}.issubset(dependencies):
                background_slots.add(slot)
            if output.get("renderer") == "layered_badge":
                self.template_slots.add(slot)
        for replacement in record.payload.get("visual_replacements") or []:
            slot = str(replacement.get("slot") or "")
            deployment = replacement.get("deployment") or {}
            target_size = deployment.get("target_size")
            if slot not in sizes and isinstance(target_size, list) and len(target_size) == 2:
                sizes[slot] = (int(target_size[0]), int(target_size[1]))
        return sizes, background_slots

    def new_project(self) -> None:
        self.editing_workspace = None
        self.automatic_authoring = {}
        self.dirty_slots = set()
        self.slot_preview_cache = {}
        self.current_slot = ""
        self.current_layer = "direct"
        default = self.adapters[0]
        label = next(label for label, value in self.adapter_labels.items() if value == default.adapter_id)
        self.target_var.set(label)
        self.name_var.set(f"自定义 {default.hero} 皮肤")
        self.version_var.set("0.1.0")
        self.pack_id = f"local.{default.hero.casefold()}.{uuid.uuid4().hex[:10]}"
        self.slot_states = {slot: SlotState() for slot in self.slot_names}
        self._apply_target_contracts(select_first=True)
        self.status_var.set("新建逐槽位项目：选择槽位并导入图片")

    def edit_workspace(self, workspace: StudioWorkspace) -> None:
        authoring = workspace.state.get("authoring") or {}
        if authoring.get("mode") != "manual_slots":
            self.continue_from_automatic_workspace(workspace)
            return
        target = workspace.state.get("target") or {}
        label = next(
            (
                text
                for text, adapter_id in self.adapter_labels.items()
                if self.adapter_by_id[adapter_id].hero == target.get("hero")
                and self.adapter_by_id[adapter_id].skin == target.get("skin")
            ),
            "",
        )
        if not label:
            raise ValueError("该皮肤的目标不再具有可编辑适配器。")
        self.editing_workspace = workspace
        self.automatic_authoring = deepcopy(authoring.get("automatic_draft") or {})
        self.target_var.set(label)
        pack = workspace.state.get("pack") or {}
        self.pack_id = str(pack.get("id") or workspace.directory.name)
        self.name_var.set(str(pack.get("name") or self.pack_id))
        self.version_var.set(str(pack.get("version") or "0.1.0"))
        payload = authoring.get("manual_slots") or {}
        self.slot_states = {
            slot: SlotState.from_dict(payload.get(slot), workspace.directory)
            for slot in self.slot_names
        }
        declared_overrides = authoring.get("manual_overrides")
        self.dirty_slots = (
            {str(slot) for slot in declared_overrides if str(slot) in self.slot_states}
            if isinstance(declared_overrides, list)
            else {str(slot) for slot in payload if str(slot) in self.slot_states}
        )
        self.slot_preview_cache = {}
        self._apply_target_contracts(select_first=True)
        self.status_var.set(f"正在编辑：{self.name_var.get()}；保存后覆盖同名皮肤包")

    def continue_from_automatic_workspace(
        self,
        workspace: StudioWorkspace,
        *,
        preserve_overrides: bool = False,
    ) -> None:
        """Use one generated workspace as the editable per-slot draft cache."""

        authoring = workspace.state.get("authoring") or {}
        if authoring.get("mode") == "manual_slots":
            self.edit_workspace(workspace)
            return
        target = workspace.state.get("target") or {}
        label = next(
            (
                text
                for text, adapter_id in self.adapter_labels.items()
                if self.adapter_by_id[adapter_id].hero == target.get("hero")
                and self.adapter_by_id[adapter_id].skin == target.get("skin")
            ),
            "",
        )
        if not label:
            raise ValueError("自动草稿的目标不再具有可编辑适配器。")

        previous_pack_id = self.pack_id
        previous_states = deepcopy(self.slot_states)
        previous_dirty = set(self.dirty_slots)
        previous_previews = {
            slot: image.copy() for slot, image in self.slot_preview_cache.items()
        }

        self.editing_workspace = workspace
        self.automatic_authoring = deepcopy(authoring)
        self.current_slot = ""
        self.current_layer = "direct"
        self.target_var.set(label)
        pack = workspace.state.get("pack") or {}
        self.pack_id = str(pack.get("id") or workspace.directory.name)
        self.name_var.set(str(pack.get("name") or self.pack_id))
        self.version_var.set(str(pack.get("version") or "0.1.0"))
        self.slot_states = {slot: SlotState() for slot in self.slot_names}
        self._apply_target_contracts(select_first=False)

        automatic_states = automatic_draft_slot_states(
            workspace,
            tuple(self.slot_states),
            self.background_slots,
            self.template_slots,
        )
        same_project = previous_pack_id.casefold() == self.pack_id.casefold()
        if preserve_overrides and same_project:
            for slot in previous_dirty:
                if slot in automatic_states and slot in previous_states:
                    automatic_states[slot] = previous_states[slot]
            self.dirty_slots = previous_dirty & set(automatic_states)
            self.slot_preview_cache = {
                slot: image
                for slot, image in previous_previews.items()
                if slot in self.dirty_slots
            }
        else:
            self.dirty_slots = set()
            self.slot_preview_cache = {}
        self.slot_states = automatic_states

        for slot in self.slot_tree.get_children():
            self._refresh_slot_status(slot)
        configured = next(
            (slot for slot, state in self.slot_states.items() if state.has_input()),
            next(iter(self.slot_states), ""),
        )
        if configured:
            self.slot_tree.selection_set(configured)
            self.slot_tree.focus(configured)
            self._load_slot(configured)
        self.status_var.set(
            f"已接续自动草稿：{self.name_var.get()}；"
            f"保留 {len(self.dirty_slots)} 个逐槽位覆盖"
        )

    def _apply_target_contracts(self, *, select_first: bool = False) -> None:
        self._commit_controls()
        self.slot_sizes, self.background_slots = self._slot_contracts()
        supported = [slot for slot in self.slot_names if slot in self.slot_sizes]
        self.slot_states = {
            slot: self.slot_states.get(slot, SlotState()) for slot in supported
        }
        self.slot_tree.delete(*self.slot_tree.get_children())
        for slot in supported:
            self.slot_tree.insert(
                "",
                "end",
                iid=slot,
                text=self.slot_names[slot],
                values=(self._slot_status(slot),),
            )
        if select_first and supported:
            self.slot_tree.selection_set(supported[0])
            self.slot_tree.focus(supported[0])
            self._load_slot(supported[0])

    def _target_changed(self, _event: tk.Event | None = None) -> None:
        old_hero = self.pack_id.split(".")[1] if self.pack_id.startswith("local.") else ""
        record = self._adapter()
        if self.editing_workspace is None and old_hero:
            self.pack_id = f"local.{record.hero.casefold()}.{uuid.uuid4().hex[:10]}"
        if self.editing_workspace is None:
            self.name_var.set(f"自定义 {record.hero} 皮肤")
        self._apply_target_contracts(select_first=True)

    def _slot_status(self, slot: str) -> str:
        state = self.slot_states.get(slot)
        if state is None or not state.has_input():
            return "未提供"
        return "分层" if state.mode == "layered" else "成品图"

    def _freeze_dynamic_slot_sources(self, slot: str) -> None:
        """Detach an override from generated ``assets`` before editing it.

        Generated slot files are mutable outputs: the automatic pipeline and
        manual materialization both replace them in place.  Keeping one of
        those files as the source of a non-identity transform would bake the
        transform into the file and apply it again on the next edit (scale is
        squared and offsets accumulate).  The first edit therefore snapshots
        every generated layer used by that slot into immutable authoring
        storage.  Subsequent renders always start from that same source.
        """

        workspace = self.editing_workspace
        state = self.slot_states.get(slot)
        if workspace is None or state is None:
            return
        dynamic_root = (workspace.directory / "assets").resolve()
        for layer_id in ("direct", "background", "character"):
            layer = state.layer(layer_id)
            if not layer.path:
                continue
            source = Path(layer.path).resolve()
            if not source.is_file():
                continue
            try:
                source.relative_to(dynamic_root)
            except ValueError:
                continue
            destination = (
                workspace.directory
                / "authoring"
                / "manual_drafts"
                / slot
                / f"{layer_id}{source.suffix.casefold()}"
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            layer.path = str(destination.resolve())
            if slot == self.current_slot and hasattr(self, "path_vars"):
                self.path_vars[layer_id].set(layer.path)

    def _mark_dirty(self, slot: str | None = None) -> None:
        selected = slot or self.current_slot
        if not selected:
            return
        self._freeze_dynamic_slot_sources(selected)
        self.dirty_slots.add(selected)
        self.slot_preview_cache.pop(selected, None)
        self.status_var.set(
            f"草稿已修改：{len(self.dirty_slots)} 个槽位覆盖默认模式；切换模式不会重新生成"
        )

    @staticmethod
    def _state_has_renderable_input(state: SlotState) -> bool:
        """Return whether an override still has a source that can be rendered.

        A dirty marker is only metadata; it must never turn a generated base
        slot into a transparent bitmap after the user clears an input or a
        cached source disappears.  Existing transparent PNGs remain valid
        overrides because the source file itself is still present.
        """

        layer_ids = (
            ("background", "character")
            if state.mode == "layered"
            else ("direct",)
        )
        return any(
            bool(layer.path) and Path(layer.path).is_file()
            for layer_id in layer_ids
            for layer in (state.layer(layer_id),)
        )

    def effective_override_slots(self) -> set[str]:
        """Return sparse overrides that can safely shadow the automatic base."""

        return {
            slot
            for slot in getattr(self, "dirty_slots", set())
            if slot in self.slot_states
            and self._state_has_renderable_input(self.slot_states[slot])
        }

    def has_overrides(self) -> bool:
        return bool(self.effective_override_slots())

    def override_count(self) -> int:
        return len(self.effective_override_slots())

    def current_pack_id(self) -> str:
        return self.pack_id

    def commit_for_mode_switch(
        self,
        preview_slots: set[str] | None = None,
    ) -> dict[str, Image.Image]:
        """Commit Tk controls and return an in-memory override snapshot.

        This deliberately performs no file copies, PNG encoding, workspace
        rebuild, or ZIP work.  Notebook tab handlers must stay cheap.
        """

        self._commit_controls()
        overrides: dict[str, Image.Image] = {}
        effective = self.effective_override_slots()
        requested = effective & preview_slots if preview_slots is not None else effective
        for slot in requested:
            cached = self.slot_preview_cache.get(slot)
            if cached is None:
                foreground, background = self._render_slot(slot)
                cached = (
                    background.copy()
                    if background is not None
                    else Image.new("RGBA", foreground.size, (0, 0, 0, 0))
                )
                cached.alpha_composite(foreground)
                self.slot_preview_cache[slot] = cached.copy()
            overrides[slot] = cached.copy()
        return overrides

    def show_background_sync(self) -> None:
        self.status_var.set(
            "默认草稿已变化，正在后台同步未覆盖槽位；当前逐槽位调整可以继续使用"
        )

    def _refresh_slot_status(self, slot: str) -> None:
        if self.slot_tree.exists(slot):
            self.slot_tree.set(slot, "state", self._slot_status(slot))

    def _slot_selected(self, _event: tk.Event | None = None) -> None:
        selection = self.slot_tree.selection()
        if selection:
            self._load_slot(selection[0])

    def _load_slot(self, slot: str) -> None:
        self._commit_controls()
        self.current_slot = slot
        state = self.slot_states[slot]
        layered_slots = self.background_slots | self.template_slots
        if state.mode == "layered" and slot not in layered_slots:
            state.mode = "direct"
        self.mode_var.set(state.mode)
        self.slot_title_var.set(f"{self.slot_names[slot]} · {slot}")
        self.original_reference_title_var.set(
            "游戏原版全身参考（同画布 / 位置比例）"
            if slot == STANDING_SLOT
            else "游戏原版参考（同画布 / 同比例）"
        )
        size = self.slot_sizes[slot]
        self.size_var.set(f"输出 {size[0]}×{size[1]}")
        self.layered_radio.configure(
            state="normal" if slot in layered_slots else "disabled",
            text=(
                "原生框 + 人物分层合成"
                if slot in self.template_slots
                else "背景 + 人物分层合成"
            ),
        )
        self._show_mode_rows()
        self.current_layer = "direct" if state.mode == "direct" else "character"
        self._load_layer_controls()
        self._render_preview()
        self._load_original_reference(slot)

    def _load_original_reference(self, slot: str) -> None:
        """Read the verified native image without blocking the editor UI."""

        self._original_reference_request += 1
        request = self._original_reference_request
        record = self._adapter()
        game_dir = self.game_dir_provider() if self.game_dir_provider else None
        key = (record.adapter_id, slot, str(game_dir or ""))
        cached = self._original_reference_memory.get(key)
        if cached is not None:
            self.original_reference_image = cached.copy()
            self.original_reference_status = ""
            self._render_original_preview()
            return

        self.original_reference_image = None
        self.original_reference_status = "正在读取游戏原版…"
        self._render_original_preview()
        target = {"hero": record.hero, "skin": record.skin}
        results: queue.Queue[tuple[Image.Image | None, str]] = queue.Queue(maxsize=1)

        def worker() -> None:
            image: Image.Image | None = None
            error_text = ""
            try:
                reference = export_original_visual_reference(
                    target,
                    slot,
                    game_dir=game_dir,
                )
                with Image.open(reference) as loaded:
                    image = loaded.convert("RGBA")
            except Exception as error:
                error_text = str(error)
            results.put((image, error_text))

        def poll() -> None:
            if request != self._original_reference_request or slot != self.current_slot:
                return
            try:
                image, error_text = results.get_nowait()
            except queue.Empty:
                try:
                    self.root.after(50, poll)
                except (tk.TclError, RuntimeError):
                    pass
                return
            if image is not None:
                self._original_reference_memory[key] = image.copy()
                self.original_reference_image = image
                self.original_reference_status = ""
            else:
                self.original_reference_image = None
                if "no static original image" in error_text:
                    self.original_reference_status = "该槽位由游戏动态 / 分层生成\n没有可直接导出的原版位图"
                elif "native backup" in error_text:
                    self.original_reference_status = "原版备份不可用\n请先取消部署或修复游戏文件"
                else:
                    self.original_reference_status = f"原版参考不可用\n{error_text}"
            self._render_original_preview()

        threading.Thread(
            target=worker,
            name=f"original-slot-reference-{slot}",
            daemon=True,
        ).start()
        self.root.after(50, poll)

    def _show_mode_rows(self) -> None:
        layered = self.mode_var.get() == "layered"
        template_layered = layered and self.current_slot in self.template_slots
        for layer_id, widgets in self.path_rows.items():
            if template_layered:
                visible = layer_id == "character"
            else:
                visible = (layer_id == "direct") != layered
            for widget in widgets:
                if visible:
                    widget.grid()
                else:
                    widget.grid_remove()
        values = (
            ((LAYER_LABELS["character"],) if template_layered else (
                LAYER_LABELS["background"], LAYER_LABELS["character"]
            ))
            if layered
            else (LAYER_LABELS["direct"],)
        )
        self.layer_box.configure(values=values)
        if self.layer_var.get() not in values:
            self.layer_var.set(values[-1])
        if self.current_slot:
            state = self.slot_states[self.current_slot]
            for layer_id in self.path_vars:
                self.path_vars[layer_id].set(state.layer(layer_id).path)

    def _mode_changed(self) -> None:
        if not self.current_slot:
            return
        self._commit_controls()
        state = self.slot_states[self.current_slot]
        state.mode = self.mode_var.get()
        self._mark_dirty()
        self.current_layer = "direct" if state.mode == "direct" else "character"
        self._show_mode_rows()
        self._load_layer_controls()
        self._refresh_slot_status(self.current_slot)
        self._render_preview()

    def _layer_changed(self, _event: tk.Event | None = None) -> None:
        self._commit_controls()
        self.current_layer = LAYER_IDS.get(self.layer_var.get(), "direct")
        self._load_layer_controls()
        self._render_preview()

    def _load_layer_controls(self) -> None:
        if not self.current_slot:
            return
        layer = self.slot_states[self.current_slot].layer(self.current_layer)
        self._loading_controls = True
        try:
            self.layer_var.set(LAYER_LABELS[self.current_layer])
            self.x_var.set(layer.x)
            self.y_var.set(layer.y)
            self.scale_var.set(round(layer.scale * 100))
        finally:
            self._loading_controls = False

    def _commit_controls(self) -> None:
        if self._loading_controls or not self.current_slot or self.current_slot not in self.slot_states:
            return
        state = self.slot_states[self.current_slot]
        state.mode = self.mode_var.get() if self.mode_var.get() in {"direct", "layered"} else state.mode
        for layer_id, variable in self.path_vars.items():
            state.layer(layer_id).path = variable.get().strip()
        layer = state.layer(self.current_layer)
        try:
            layer.x = max(-8192, min(8192, int(self.x_var.get())))
            layer.y = max(-8192, min(8192, int(self.y_var.get())))
            layer.scale = max(0.25, min(4.0, int(self.scale_var.get()) / 100.0))
        except (tk.TclError, ValueError):
            pass

    def _browse_layer(self, layer_id: str) -> None:
        selected = filedialog.askopenfilename(
            parent=self.root,
            title=f"为{self.slot_names.get(self.current_slot, '槽位')}导入{LAYER_LABELS[layer_id]}",
            filetypes=(("图像", "*.png *.jpg *.jpeg *.webp *.bmp"),),
        )
        if selected:
            self._set_layer_path(layer_id, Path(selected))

    def _choose_layer_asset(self, layer_id: str) -> None:
        if self.on_choose_asset is None:
            return
        selected = self.on_choose_asset()
        if selected is not None:
            self._set_layer_path(layer_id, selected)

    def _path_entry_changed(self, layer_id: str) -> None:
        if not self.current_slot:
            return
        state = self.slot_states[self.current_slot]
        before = state.layer(layer_id).path
        self._commit_controls()
        if state.layer(layer_id).path != before:
            self._mark_dirty()
            self._refresh_slot_status(self.current_slot)
            self._render_preview()

    def _set_layer_path(self, layer_id: str, path: Path) -> None:
        if path.suffix.casefold() not in SUPPORTED_IMAGE_EXTENSIONS:
            messagebox.showerror("素材格式不支持", f"不支持 {path.suffix} 图像。", parent=self.root)
            return
        try:
            with Image.open(path) as image:
                image.verify()
        except Exception as error:
            messagebox.showerror("无法读取素材", str(error), parent=self.root)
            return
        self.path_vars[layer_id].set(str(path.resolve()))
        if self.current_slot:
            self.slot_states[self.current_slot].layer(layer_id).path = str(path.resolve())
            self._mark_dirty()
            self._refresh_slot_status(self.current_slot)
        self._render_preview()

    def _clear_layer(self, layer_id: str) -> None:
        self.path_vars[layer_id].set("")
        if self.current_slot:
            self.slot_states[self.current_slot].layer(layer_id).path = ""
            self._mark_dirty()
            self._refresh_slot_status(self.current_slot)
        self._render_preview()

    def _clear_slot(self) -> None:
        if not self.current_slot:
            return
        self.slot_states[self.current_slot] = SlotState()
        self._mark_dirty()
        self._load_slot(self.current_slot)
        self._refresh_slot_status(self.current_slot)

    def _transform_changed(self, _event: tk.Event | None = None) -> None:
        if self._loading_controls:
            return
        before = None
        if self.current_slot:
            layer = self.slot_states[self.current_slot].layer(self.current_layer)
            before = (layer.x, layer.y, layer.scale)
        self._commit_controls()
        if self.current_slot:
            layer = self.slot_states[self.current_slot].layer(self.current_layer)
            if before != (layer.x, layer.y, layer.scale):
                self._mark_dirty()
        self._load_layer_controls()
        self._render_preview()

    def _reset_transform(self) -> None:
        if not self.current_slot:
            return
        layer = self.slot_states[self.current_slot].layer(self.current_layer)
        changed = (layer.x, layer.y, layer.scale) != (0, 0, 1.0)
        layer.x = 0
        layer.y = 0
        layer.scale = 1.0
        if changed:
            self._mark_dirty()
        self._load_layer_controls()
        self._render_preview()

    def _open_image(self, path_text: str) -> Image.Image | None:
        path = Path(path_text) if path_text else None
        if path is None or not path.is_file():
            return None
        with Image.open(path) as loaded:
            return loaded.convert("RGBA")

    def _portrait_clip_declaration(self, slot: str) -> dict | None:
        recipes = getattr(self, "output_recipes", {})
        recipe = recipes.get(slot) or {}
        for layer in recipe.get("layers") or []:
            if layer.get("input") == "character" and layer.get("clip_mask"):
                return dict(layer["clip_mask"])
        if slot in PORTRAIT_PREVIEW_SLOTS and slot != PORTRAIT_PAIR_SLOT:
            for layer in (recipes.get(PORTRAIT_PAIR_SLOT) or {}).get("layers") or []:
                if layer.get("input") == "character" and layer.get("clip_mask"):
                    return dict(layer["clip_mask"])
        return None

    def _render_slot(
        self,
        slot: str,
        slot_states: dict[str, SlotState] | None = None,
        slot_sizes: dict[str, tuple[int, int]] | None = None,
    ) -> tuple[Image.Image, Image.Image | None]:
        state = (slot_states or self.slot_states)[slot]
        size = (slot_sizes or self.slot_sizes)[slot]
        if state.mode == "direct":
            source = self._open_image(state.direct.path)
            if source is None:
                return Image.new("RGBA", size, (0, 0, 0, 0)), None
            return _fit_layer(
                source,
                size,
                state.direct,
                fit="contain",
                anchor_bottom=slot == STANDING_SLOT,
            ), None

        if slot in getattr(self, "template_slots", set()):
            character_source = self._open_image(state.character.path)
            if character_source is None:
                return Image.new("RGBA", size, (0, 0, 0, 0)), None
            recipe = getattr(self, "output_recipes", {}).get(slot) or {}
            return render_layered_badge(
                character_source,
                output_recipe=recipe,
                layer=state.character,
                template_root=getattr(
                    self, "badge_template_root", BADGE_TEMPLATE_ROOT
                ),
            ), None

        background_source = self._open_image(state.background.path)
        character_source = self._open_image(state.character.path)
        background = (
            _fit_layer(background_source, size, state.background, fit="cover")
            if background_source is not None
            else Image.new("RGBA", size, (0, 0, 0, 0))
        )
        character = (
            _fit_layer(
                character_source,
                size,
                state.character,
                fit="contain",
                anchor_bottom=True,
            )
            if character_source is not None
            else Image.new("RGBA", size, (0, 0, 0, 0))
        )
        clip = self._portrait_clip_declaration(slot)
        if clip:
            character = apply_declared_clip_mask(character, clip)
        if slot == PORTRAIT_PAIR_SLOT:
            return character, background if background_source is not None else None
        composite = background.copy()
        composite.alpha_composite(character)
        return composite, None

    def _render_preview(self) -> None:
        canvas = self.canvas
        width = max(1, canvas.winfo_width())
        height = max(1, canvas.winfo_height())
        if width < 30 or height < 30:
            return
        canvas.delete("all")
        if not self.current_slot:
            canvas.create_text(width // 2, height // 2, text="选择槽位", fill=COLORS["muted"])
            return
        self._commit_controls()
        try:
            foreground, paired_background = self._render_slot(self.current_slot)
            source = paired_background.copy() if paired_background is not None else Image.new(
                "RGBA", foreground.size, (0, 0, 0, 0)
            )
            source.alpha_composite(foreground)
            if self.current_slot in self.dirty_slots:
                # Cache the actual exported pixels before adding the preview-
                # only frame guide.  Default mode consumes this snapshot when
                # both views share the same project.
                self.slot_preview_cache[self.current_slot] = source.copy()
            if self.current_slot in PORTRAIT_PREVIEW_SLOTS:
                clip = self._portrait_clip_declaration(self.current_slot)
                if clip:
                    source.alpha_composite(
                        portrait_frame_preview_overlay(source.size, clip)
                    )
        except Exception as error:
            canvas.create_text(
                width // 2,
                height // 2,
                text=f"无法预览\n{error}",
                fill=COLORS["danger"],
                width=max(100, width - 30),
                justify="center",
            )
            return
        margin = 14
        scale = min((width - margin * 2) / source.width, (height - margin * 2) / source.height)
        display_size = (max(1, round(source.width * scale)), max(1, round(source.height * scale)))
        preview = source.resize(display_size, Image.Resampling.LANCZOS)
        checker = Image.new("RGBA", display_size, (218, 224, 231, 255))
        tile = 14
        for y in range(0, display_size[1], tile):
            for x in range(0, display_size[0], tile):
                if (x // tile + y // tile) % 2:
                    checker.paste(
                        (177, 187, 199, 255),
                        (x, y, min(display_size[0], x + tile), min(display_size[1], y + tile)),
                    )
        checker.alpha_composite(preview)
        self.preview_photo = ImageTk.PhotoImage(checker)
        self.preview_scale = scale
        canvas.create_image(width // 2, height // 2, image=self.preview_photo, anchor="center")
        canvas.create_rectangle(
            (width - display_size[0]) // 2,
            (height - display_size[1]) // 2,
            (width + display_size[0]) // 2,
            (height + display_size[1]) // 2,
            outline=COLORS["accent"],
            width=2,
        )
        left = (width - display_size[0]) // 2
        top = (height - display_size[1]) // 2
        right = (width + display_size[0]) // 2
        bottom = (height + display_size[1]) // 2
        canvas.create_line(
            width // 2,
            top,
            width // 2,
            bottom,
            fill=COLORS["muted"],
            dash=(3, 6),
        )
        canvas.create_line(
            left,
            height // 2,
            right,
            height // 2,
            fill=COLORS["muted"],
            dash=(3, 6),
        )

    def _render_original_preview(self) -> None:
        canvas = self.original_canvas
        width = max(1, canvas.winfo_width())
        height = max(1, canvas.winfo_height())
        if width < 30 or height < 30:
            return
        canvas.delete("all")
        if not self.current_slot:
            canvas.create_text(
                width // 2,
                height // 2,
                text="选择槽位",
                fill=COLORS["muted"],
            )
            return
        if self.original_reference_image is None:
            canvas.create_text(
                width // 2,
                height // 2,
                text=self.original_reference_status,
                fill=COLORS["muted"],
                width=max(100, width - 30),
                justify="center",
            )
            return

        source = self.original_reference_image.copy()
        output_size = self.slot_sizes[self.current_slot]
        if source.size != output_size:
            source = _fit_layer(
                source,
                output_size,
                LayerState(),
                fit="contain",
                anchor_bottom=self.current_slot == STANDING_SLOT,
            )
        if self.current_slot in PORTRAIT_PREVIEW_SLOTS:
            clip = self._portrait_clip_declaration(self.current_slot)
            if clip:
                source.alpha_composite(
                    portrait_frame_preview_overlay(source.size, clip)
                )

        margin = 14
        scale = min(
            (width - margin * 2) / source.width,
            (height - margin * 2) / source.height,
        )
        display_size = (
            max(1, round(source.width * scale)),
            max(1, round(source.height * scale)),
        )
        preview = source.resize(display_size, Image.Resampling.LANCZOS)
        checker = Image.new("RGBA", display_size, (218, 224, 231, 255))
        tile = 14
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
        self.original_preview_photo = ImageTk.PhotoImage(checker)
        canvas.create_image(
            width // 2,
            height // 2,
            image=self.original_preview_photo,
            anchor="center",
        )
        left = (width - display_size[0]) // 2
        top = (height - display_size[1]) // 2
        right = (width + display_size[0]) // 2
        bottom = (height + display_size[1]) // 2
        canvas.create_rectangle(
            left,
            top,
            right,
            bottom,
            outline=COLORS["accent"],
            width=2,
        )
        canvas.create_line(
            width // 2,
            top,
            width // 2,
            bottom,
            fill=COLORS["muted"],
            dash=(3, 6),
        )
        canvas.create_line(
            left,
            height // 2,
            right,
            height // 2,
            fill=COLORS["muted"],
            dash=(3, 6),
        )

    def _drag_start(self, event: tk.Event) -> None:
        if not self.current_slot:
            return
        self._commit_controls()
        layer = self.slot_states[self.current_slot].layer(self.current_layer)
        self.drag_origin = (event.x, event.y, layer.x, layer.y)

    def _drag_motion(self, event: tk.Event) -> None:
        if self.drag_origin is None or self.preview_scale <= 0 or not self.current_slot:
            return
        start_x, start_y, source_x, source_y = self.drag_origin
        layer = self.slot_states[self.current_slot].layer(self.current_layer)
        layer.x = source_x + round((event.x - start_x) / self.preview_scale)
        layer.y = source_y + round((event.y - start_y) / self.preview_scale)
        self.x_var.set(layer.x)
        self.y_var.set(layer.y)
        self._mark_dirty()
        self._render_preview()

    def _drag_end(self, _event: tk.Event) -> None:
        self.drag_origin = None

    def _validated_identity(self) -> tuple[str, str, str]:
        name = self.name_var.get().strip()
        version = self.version_var.get().strip()
        if not name:
            raise ValueError("皮肤名称不能为空。")
        if not version:
            raise ValueError("版本不能为空。")
        if not any(state.has_input() for state in self.slot_states.values()):
            raise ValueError("请至少为一个槽位导入素材。")
        return self.pack_id, name, version

    def _copy_author_source(
        self,
        workspace: StudioWorkspace,
        slot: str,
        layer_id: str,
        source_text: str,
    ) -> str | None:
        if not source_text:
            return None
        source = Path(source_text).resolve()
        if not source.is_file():
            raise FileNotFoundError(f"{self.slot_names[slot]}的{LAYER_LABELS[layer_id]}不存在：{source}")
        destination = workspace.directory / "authoring" / "manual_inputs" / slot / f"{layer_id}{source.suffix.casefold()}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source != destination.resolve():
            shutil.copy2(source, destination)
        return destination.relative_to(workspace.directory).as_posix()

    def _build_snapshot(self) -> ManualBuildSnapshot:
        self._commit_controls()
        pack_id, name, version = self._validated_identity()
        adapter = self._adapter()
        return ManualBuildSnapshot(
            pack_id=pack_id,
            name=name,
            version=version,
            hero=adapter.hero,
            skin=adapter.skin,
            skin_name_contains=adapter.skin_name_contains,
            slot_states=deepcopy(self.slot_states),
            slot_sizes=dict(self.slot_sizes),
            automatic_authoring=deepcopy(getattr(self, "automatic_authoring", {})),
            dirty_slots=self.effective_override_slots(),
            workspace=self.editing_workspace,
        )

    def _materialize_workspace(
        self,
        snapshot: ManualBuildSnapshot,
    ) -> StudioWorkspace:
        workspace = snapshot.workspace
        if workspace is None:
            workspace = StudioWorkspace.create(
                snapshot.pack_id,
                root=WORKSPACES_ROOT,
                name=snapshot.name,
                version=snapshot.version,
                hero=snapshot.hero,
                skin=snapshot.skin,
                skin_name_contains=snapshot.skin_name_contains,
            )
        workspace.set_metadata(
            pack_id=snapshot.pack_id,
            name=snapshot.name,
            version=snapshot.version,
            hero=snapshot.hero,
            skin=snapshot.skin,
            skin_name_contains=snapshot.skin_name_contains,
        )
        author_inputs: dict[str, dict] = deepcopy(
            (snapshot.automatic_authoring.get("inputs") or {})
        )
        manual_slots: dict[str, dict] = {}
        for slot, state in snapshot.slot_states.items():
            if not state.has_input():
                continue
            record = {"mode": state.mode, "size": list(snapshot.slot_sizes[slot])}
            for layer_id in ("direct", "background", "character"):
                layer = state.layer(layer_id)
                relative = self._copy_author_source(
                    workspace, slot, layer_id, layer.path
                )
                if relative:
                    layer.path = str((workspace.directory / relative).resolve())
                    record[layer_id] = layer.to_dict(relative)
                    author_inputs[f"{slot}.{layer_id}"] = {
                        "workspace_file": relative,
                        "origin": "user_supplied",
                        "aigc": False,
                    }
            manual_slots[slot] = record

        rendered_images: dict[str, Image.Image] = {}
        paired_background: Image.Image | None = None
        for slot, state in snapshot.slot_states.items():
            if not state.has_input():
                continue
            rendered, background = self._render_slot(
                slot,
                snapshot.slot_states,
                snapshot.slot_sizes,
            )
            rendered_images[slot] = rendered
            if slot == PORTRAIT_PAIR_SLOT and background is not None:
                paired_background = background
        explicit_background = snapshot.slot_states.get(PORTRAIT_BACKGROUND_SLOT)
        if paired_background is not None and not (
            explicit_background and explicit_background.has_input()
        ):
            rendered_images[PORTRAIT_BACKGROUND_SLOT] = paired_background
        workspace.replace_visual_images(rendered_images)

        manual_authoring = {
            "schema": MANUAL_AUTHORING_SCHEMA,
            "mode": "manual_slots",
            "inputs": author_inputs,
            "manual_slots": manual_slots,
            "manual_overrides": sorted(snapshot.dirty_slots),
        }
        automatic_authoring = snapshot.automatic_authoring
        if automatic_authoring:
            manual_authoring["automatic_draft"] = deepcopy(automatic_authoring)
        workspace.state["authoring"] = manual_authoring
        workspace.state.setdefault("library_assets", {"inputs": {}, "visual_slots": {}, "audio": {}, "animation": None})
        workspace.state["library_assets"]["inputs"] = {}
        workspace.save()
        workspace.build_pack()
        return workspace

    def build_workspace(self) -> StudioWorkspace:
        """Synchronously materialize a snapshot for tests and non-UI callers."""

        snapshot = self._build_snapshot()
        workspace = self._materialize_workspace(snapshot)
        self.slot_states = snapshot.slot_states
        self.editing_workspace = workspace
        self.status_var.set(f"已生成 {len(workspace.state.get('visual_slots') or {})} 个槽位")
        return workspace

    def _set_build_busy(self, busy: bool) -> None:
        self.build_busy = busy
        for button in getattr(self, "build_buttons", []):
            button.configure(state="disabled" if busy else "normal")

    def _start_async_build(
        self,
        action: str,
        *,
        destination: Path | None = None,
    ) -> bool:
        if self.build_busy:
            return False
        try:
            snapshot = self._build_snapshot()
        except Exception as error:
            messagebox.showerror("逐槽位生成失败", str(error), parent=self.root)
            return False
        self._set_build_busy(True)
        started = time.perf_counter()
        self.status_var.set("正在后台生成逐槽位皮肤；界面仍可切换和查看")

        def worker() -> None:
            try:
                workspace = self._materialize_workspace(snapshot)
                if action == "export":
                    assert destination is not None
                    workspace.export_zip(destination)
                self.build_events.put(
                    (
                        "complete",
                        (action, workspace, destination, time.perf_counter() - started),
                    )
                )
            except Exception:
                details = traceback.format_exc()
                append_error_log(
                    manager_root() / "ui-error.log",
                    f"逐槽位{action}失败",
                    details,
                )
                self.build_events.put(("error", (action, details.strip().splitlines()[-1])))

        threading.Thread(
            target=worker,
            name=f"manual-slot-{action}",
            daemon=True,
        ).start()
        return True

    def _poll_build_events(self) -> None:
        try:
            while True:
                kind, payload = self.build_events.get_nowait()
                self._set_build_busy(False)
                if kind == "error":
                    _action, details = payload
                    self.status_var.set("逐槽位生成失败")
                    messagebox.showerror("逐槽位生成失败", details, parent=self.root)
                    continue
                action, workspace, destination, elapsed = payload
                self.editing_workspace = workspace
                self.status_var.set(
                    f"已生成 {len(workspace.state.get('visual_slots') or {})} 个槽位，"
                    f"耗时 {elapsed:.1f} 秒"
                )
                if action == "import":
                    self.on_import(workspace)
                elif action == "export":
                    messagebox.showinfo(
                        "导出完成",
                        f"皮肤包已导出：\n{destination}",
                        parent=self.root,
                    )
        except queue.Empty:
            pass
        try:
            self.root.after(100, self._poll_build_events)
        except (tk.TclError, RuntimeError):
            pass

    def import_to_library(self) -> bool:
        return self._start_async_build("import")

    def _import(self) -> None:
        self.import_to_library()

    def export_to_zip(self) -> bool:
        if self.build_busy:
            return False
        selected = filedialog.asksaveasfilename(
            parent=self.root,
            title="导出逐槽位皮肤包",
            initialfile=f"{self.name_var.get().strip()}-{self.version_var.get().strip()}.zip",
            defaultextension=".zip",
            filetypes=(("皮肤包 ZIP", "*.zip"),),
        )
        if not selected:
            return False
        return self._start_async_build("export", destination=Path(selected))

    def _export(self) -> None:
        self.export_to_zip()

    def self_test(self) -> None:
        if set(self.slot_states) != set(self.slot_sizes):
            raise RuntimeError("manual slot state and adapter contract diverged")
        if not self.slot_tree.get_children():
            raise RuntimeError("manual slot list is empty")
