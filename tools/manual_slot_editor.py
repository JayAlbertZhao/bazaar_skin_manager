#!/usr/bin/env python3
"""Manual per-slot authoring surface for the integrated skin manager."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import shutil
from typing import Callable
import uuid
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from asset_generator_core import authoring_adapters
from mod_studio_core import SUPPORTED_IMAGE_EXTENSIONS, WORKSPACES_ROOT, StudioWorkspace


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


class ManualSlotEditor:
    """Create complete packs by supplying each logical image slot manually."""

    def __init__(
        self,
        parent: tk.Misc,
        *,
        catalog: dict,
        on_import: Callable[[StudioWorkspace], None],
        on_choose_asset: Callable[[], Path | None] | None = None,
    ) -> None:
        self.root = parent.winfo_toplevel()
        self.host = parent
        self.catalog = catalog
        self.on_import = on_import
        self.on_choose_asset = on_choose_asset
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
        self.current_slot = ""
        self.current_layer = "direct"
        self.editing_workspace: StudioWorkspace | None = None
        self.pack_id = ""
        self.preview_photo: ImageTk.PhotoImage | None = None
        self.preview_scale = 1.0
        self.drag_origin: tuple[int, int, int, int] | None = None
        self._loading_controls = False
        self._build_ui()
        self.new_project()

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
        ttk.Button(heading, text="新建项目", command=self.new_project).pack(side="right")

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

        self.canvas = tk.Canvas(
            editor,
            bg="#0d1219",
            highlightthickness=1,
            highlightbackground=COLORS["line"],
            cursor="fleur",
        )
        self.canvas.grid(row=4, column=0, sticky="nsew")
        self.canvas.bind("<Configure>", lambda _event: self._render_preview())
        self.canvas.bind("<ButtonPress-1>", self._drag_start)
        self.canvas.bind("<B1-Motion>", self._drag_motion)
        self.canvas.bind("<ButtonRelease-1>", self._drag_end)

        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(10, 0))
        self.status_var = tk.StringVar(value="逐槽位模式等待输入")
        ttk.Label(footer, textvariable=self.status_var, foreground=COLORS["muted"]).pack(
            side="left"
        )
        ttk.Button(footer, text="导出 ZIP…", command=self._export).pack(side="right")
        ttk.Button(
            footer,
            text="导入到皮肤库",
            style="Accent.TButton",
            command=self._import,
        ).pack(side="right", padx=(0, 8))

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
        for slot in self.slot_names:
            output = _resolved_output(recipe, slot)
            size = output.get("size")
            if isinstance(size, list) and len(size) == 2:
                sizes[slot] = (int(size[0]), int(size[1]))
            dependencies = {str(value) for value in output.get("depends_on") or []}
            if {"background", "character"}.issubset(dependencies):
                background_slots.add(slot)
        for replacement in record.payload.get("visual_replacements") or []:
            slot = str(replacement.get("slot") or "")
            deployment = replacement.get("deployment") or {}
            target_size = deployment.get("target_size")
            if slot not in sizes and isinstance(target_size, list) and len(target_size) == 2:
                sizes[slot] = (int(target_size[0]), int(target_size[1]))
        return sizes, background_slots

    def new_project(self) -> None:
        self.editing_workspace = None
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
            raise ValueError("该皮肤不是逐槽位模式项目。")
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
        self._apply_target_contracts(select_first=True)
        self.status_var.set(f"正在编辑：{self.name_var.get()}；保存后覆盖同名皮肤包")

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
        if state.mode == "layered" and slot not in self.background_slots:
            state.mode = "direct"
        self.mode_var.set(state.mode)
        self.slot_title_var.set(f"{self.slot_names[slot]} · {slot}")
        size = self.slot_sizes[slot]
        self.size_var.set(f"输出 {size[0]}×{size[1]}")
        self.layered_radio.configure(
            state="normal" if slot in self.background_slots else "disabled"
        )
        self._show_mode_rows()
        self.current_layer = "direct" if state.mode == "direct" else "character"
        self._load_layer_controls()
        self._render_preview()

    def _show_mode_rows(self) -> None:
        layered = self.mode_var.get() == "layered"
        for layer_id, widgets in self.path_rows.items():
            visible = (layer_id == "direct") != layered
            for widget in widgets:
                if visible:
                    widget.grid()
                else:
                    widget.grid_remove()
        values = (
            (LAYER_LABELS["background"], LAYER_LABELS["character"])
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
            self._refresh_slot_status(self.current_slot)
        self._render_preview()

    def _clear_layer(self, layer_id: str) -> None:
        self.path_vars[layer_id].set("")
        if self.current_slot:
            self.slot_states[self.current_slot].layer(layer_id).path = ""
            self._refresh_slot_status(self.current_slot)
        self._render_preview()

    def _clear_slot(self) -> None:
        if not self.current_slot:
            return
        self.slot_states[self.current_slot] = SlotState()
        self._load_slot(self.current_slot)
        self._refresh_slot_status(self.current_slot)

    def _transform_changed(self, _event: tk.Event | None = None) -> None:
        if self._loading_controls:
            return
        self._commit_controls()
        self._load_layer_controls()
        self._render_preview()

    def _reset_transform(self) -> None:
        if not self.current_slot:
            return
        layer = self.slot_states[self.current_slot].layer(self.current_layer)
        layer.x = 0
        layer.y = 0
        layer.scale = 1.0
        self._load_layer_controls()
        self._render_preview()

    def _open_image(self, path_text: str) -> Image.Image | None:
        path = Path(path_text) if path_text else None
        if path is None or not path.is_file():
            return None
        with Image.open(path) as loaded:
            return loaded.convert("RGBA")

    def _render_slot(self, slot: str) -> tuple[Image.Image, Image.Image | None]:
        state = self.slot_states[slot]
        size = self.slot_sizes[slot]
        if state.mode == "direct":
            source = self._open_image(state.direct.path)
            if source is None:
                return Image.new("RGBA", size, (0, 0, 0, 0)), None
            return _fit_layer(source, size, state.direct, fit="contain"), None

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

    def build_workspace(self) -> StudioWorkspace:
        self._commit_controls()
        pack_id, name, version = self._validated_identity()
        adapter = self._adapter()
        workspace = self.editing_workspace
        if workspace is None:
            workspace = StudioWorkspace.create(
                pack_id,
                root=WORKSPACES_ROOT,
                name=name,
                version=version,
                hero=adapter.hero,
                skin=adapter.skin,
                skin_name_contains=adapter.skin_name_contains,
            )
        workspace.set_metadata(
            pack_id=pack_id,
            name=name,
            version=version,
            hero=adapter.hero,
            skin=adapter.skin,
            skin_name_contains=adapter.skin_name_contains,
        )
        author_inputs: dict[str, dict] = {}
        manual_slots: dict[str, dict] = {}
        for slot, state in self.slot_states.items():
            if not state.has_input():
                continue
            record = {"mode": state.mode, "size": list(self.slot_sizes[slot])}
            for layer_id in ("direct", "background", "character"):
                layer = state.layer(layer_id)
                relative = self._copy_author_source(
                    workspace, slot, layer_id, layer.path
                )
                if relative:
                    record[layer_id] = layer.to_dict(relative)
                    author_inputs[f"{slot}.{layer_id}"] = {
                        "workspace_file": relative,
                        "origin": "user_supplied",
                        "aigc": False,
                    }
            manual_slots[slot] = record

        for slot in list((workspace.state.get("visual_slots") or {})):
            workspace.clear_visual(slot)
        paired_background: Image.Image | None = None
        for slot, state in self.slot_states.items():
            if not state.has_input():
                continue
            rendered, background = self._render_slot(slot)
            workspace.import_pil_image(slot, rendered)
            if slot == PORTRAIT_PAIR_SLOT and background is not None:
                paired_background = background
        explicit_background = self.slot_states.get(PORTRAIT_BACKGROUND_SLOT)
        if paired_background is not None and not (
            explicit_background and explicit_background.has_input()
        ):
            workspace.import_pil_image(PORTRAIT_BACKGROUND_SLOT, paired_background)

        workspace.state["authoring"] = {
            "schema": MANUAL_AUTHORING_SCHEMA,
            "mode": "manual_slots",
            "inputs": author_inputs,
            "manual_slots": manual_slots,
        }
        workspace.state.setdefault("library_assets", {"inputs": {}, "visual_slots": {}, "audio": {}, "animation": None})
        workspace.state["library_assets"]["inputs"] = {}
        workspace.save()
        workspace.build_pack()
        self.editing_workspace = workspace
        self.status_var.set(f"已生成 {len(workspace.state.get('visual_slots') or {})} 个槽位")
        return workspace

    def _import(self) -> None:
        try:
            workspace = self.build_workspace()
            self.on_import(workspace)
        except Exception as error:
            messagebox.showerror("逐槽位生成失败", str(error), parent=self.root)

    def _export(self) -> None:
        try:
            workspace = self.build_workspace()
        except Exception as error:
            messagebox.showerror("逐槽位生成失败", str(error), parent=self.root)
            return
        selected = filedialog.asksaveasfilename(
            parent=self.root,
            title="导出逐槽位皮肤包",
            initialfile=f"{self.name_var.get().strip()}-{self.version_var.get().strip()}.zip",
            defaultextension=".zip",
            filetypes=(("皮肤包 ZIP", "*.zip"),),
        )
        if not selected:
            return
        try:
            workspace.export_zip(Path(selected))
            messagebox.showinfo("导出完成", f"皮肤包已导出：\n{selected}", parent=self.root)
        except Exception as error:
            messagebox.showerror("导出失败", str(error), parent=self.root)

    def self_test(self) -> None:
        if set(self.slot_states) != set(self.slot_sizes):
            raise RuntimeError("manual slot state and adapter contract diverged")
        if not self.slot_tree.get_children():
            raise RuntimeError("manual slot list is empty")
