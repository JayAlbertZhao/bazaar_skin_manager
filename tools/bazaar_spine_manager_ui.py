#!/usr/bin/env python3
"""Windows desktop UI for The Bazaar Spine Manager."""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import sys
import threading
import time
import traceback
import webbrowser
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import filedialog, messagebox
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageDraw, ImageTk

from bazaar_skin_manager import detect_installs, explicit_install
from spine_manager_core import (
    STATE_ROOT,
    SpinePackage,
    SpinePlacement,
    SpineTarget,
    deploy,
    import_spine_package,
    installation_manifest,
    restore,
    targets,
)
from spine_static_preview import (
    HERO_ROOT_X,
    HERO_ROOT_Y,
    READY_CENTER_X,
    READY_CENTER_Y,
    READY_HEIGHT,
    READY_WIDTH,
    REFERENCE_HEIGHT,
    REFERENCE_WIDTH,
    PreviewMetrics,
    RenderedPose,
    calculate_preview_metrics,
    render_setup_pose,
)


APP_VERSION = "1.4.5"
PROJECT_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
PREVIEW_BACKGROUND_PATH = (
    PROJECT_ROOT / "manager" / "spine-preview" / "hero-select-background.jpg"
)
SETTINGS_PATH = STATE_ROOT / "settings.json"
LOG_DIRECTORY = STATE_ROOT / "logs"
LOG_PATH = LOG_DIRECTORY / "bazaar_spine_manager.log"
LOGGER = logging.getLogger("bazaar_spine_manager")


def configure_logging() -> Path:
    LOG_DIRECTORY.mkdir(parents=True, exist_ok=True)
    if not any(isinstance(handler, RotatingFileHandler) for handler in LOGGER.handlers):
        handler = RotatingFileHandler(
            LOG_PATH,
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s.%(msecs)03d %(levelname)s [%(threadName)s] "
                "%(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False
    LOGGER.info(
        "application_start version=%s frozen=%s python=%s os=%s cwd=%s argv=%s",
        APP_VERSION,
        bool(getattr(sys, "frozen", False)),
        platform.python_version(),
        platform.platform(),
        Path.cwd(),
        sys.argv,
    )
    return LOG_PATH


class SpineManagerApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"The Bazaar Spine Manager v{APP_VERSION}")
        self.root.geometry("1160x900")
        self.root.minsize(1020, 800)
        self.available_targets = targets()
        self.package: SpinePackage | None = None
        self.preview_pose: RenderedPose | None = None
        self.preview_metrics: PreviewMetrics | None = None
        self.preview_metrics_key: tuple[str, str, str] | None = None
        self.preview_drag_origin: tuple[float, float, float, float] | None = None
        self.preview_refresh_job: str | None = None
        self.preview_canvas_scale = 0.0
        self.preview_game_pixels_per_unit = 0.0
        self.preview_photo = None
        with Image.open(PREVIEW_BACKGROUND_PATH) as background:
            self.preview_background = background.convert("RGB")
        self.busy = False
        self.settings = self._load_settings()
        self.game_var = tk.StringVar(value=self.settings.get("game_dir", ""))
        self.package_var = tk.StringVar(value=self.settings.get("package", ""))
        self.target_var = tk.StringVar()
        self.animation_var = tk.StringVar()
        self.root_x_var = tk.DoubleVar(value=float(self.settings.get("root_x", 0.0)))
        self.root_y_var = tk.DoubleVar(value=float(self.settings.get("root_y", 300.0)))
        self.scale_var = tk.DoubleVar(value=float(self.settings.get("scale", 1.0)))
        self.preview_status_var = tk.StringVar(value="导入 Spine 包后即可拖拽定位。")
        self.status_var = tk.StringVar(value="准备就绪")
        self._configure_style()
        self._build()
        self._append(f"诊断日志：{LOG_PATH}")
        self._restore_target_selection()
        self._detect_game_if_needed()
        for variable in (self.root_x_var, self.root_y_var, self.scale_var):
            variable.trace_add("write", self._schedule_preview_render)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _configure_style(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=("Microsoft YaHei UI", 10))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 20, "bold"))
        style.configure("Section.TLabel", font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 10, "bold"), padding=(16, 9))
        style.configure("TButton", padding=(10, 7))

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=18)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="The Bazaar Spine Manager", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text="导入 Spine 4.1/4.2 JSON 资源包（支持多页 Atlas）；部署、备份和恢复统一交给皮肤管理器事务处理。",
        ).pack(anchor="w", pady=(3, 14))

        actions = ttk.Frame(outer)
        actions.pack(side="bottom", fill="x", pady=(10, 0))
        ttk.Button(actions, text="部署到游戏", style="Accent.TButton", command=self._deploy).pack(side="left")
        ttk.Button(actions, text="恢复原始文件", command=self._restore).pack(side="left", padx=8)
        ttk.Button(actions, text="刷新状态", command=self._refresh_status).pack(side="left")
        ttk.Button(actions, text="打开日志目录", command=self._open_log_directory).pack(side="left", padx=8)
        ttk.Label(actions, textvariable=self.status_var).pack(side="right")

        self.log = tk.Text(outer, height=4, wrap="word", state="disabled")
        self.log.pack(side="bottom", fill="x", pady=(10, 0))

        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)
        setup = ttk.Frame(notebook, padding=16)
        placement = ttk.Frame(notebook, padding=16)
        notebook.add(setup, text="导入与目标")
        notebook.add(placement, text="位置预览")
        self._build_setup(setup)
        self._build_placement(placement)
        self._refresh_status()

    def _row(self, parent, row: int, label: str, variable: tk.Variable, browse=None):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=7)
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", pady=7)
        if browse:
            ttk.Button(parent, text="浏览", command=browse).grid(row=row, column=2, padx=(8, 0), pady=7)
        return entry

    def _build_setup(self, page) -> None:
        page.columnconfigure(1, weight=1)
        ttk.Label(page, text="游戏与资源", style="Section.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        self._row(page, 1, "The Bazaar 目录", self.game_var, self._browse_game)
        self._row(page, 2, "Spine ZIP / 文件夹", self.package_var, self._browse_package)
        ttk.Label(page, text="替换目标").grid(row=3, column=0, sticky="w", pady=7)
        self.target_combo = ttk.Combobox(
            page,
            textvariable=self.target_var,
            state="readonly",
            values=[self._target_label(item) for item in self.available_targets],
        )
        self.target_combo.grid(row=3, column=1, sticky="ew", pady=7)
        self.target_combo.bind("<<ComboboxSelected>>", self._target_changed)
        ttk.Button(page, text="导入并验证", command=self._import).grid(row=3, column=2, padx=(8, 0), pady=7)
        self.package_info = ttk.Label(
            page,
            text="尚未导入。要求：一个 JSON、一个 atlas、Atlas 声明的 PNG 页面，Spine 4.1/4.2，包含 default skin。",
            wraplength=760,
            justify="left",
        )
        self.package_info.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(14, 0))

    def _build_placement(self, page) -> None:
        page.columnconfigure(1, weight=1)
        page.rowconfigure(8, weight=1)
        ttk.Label(page, text="部署参数", style="Section.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(page, text="默认动画").grid(row=1, column=0, sticky="w", pady=7)
        self.animation_combo = ttk.Combobox(page, textvariable=self.animation_var, state="readonly")
        self.animation_combo.grid(row=1, column=1, sticky="ew", pady=7)
        ttk.Label(page, text="根骨 X 偏移").grid(row=2, column=0, sticky="w", pady=7)
        ttk.Spinbox(page, from_=-500, to=500, increment=5, textvariable=self.root_x_var).grid(row=2, column=1, sticky="ew")
        ttk.Label(page, text="根骨 Y 偏移（正数上移）").grid(row=3, column=0, sticky="w", pady=7)
        ttk.Spinbox(page, from_=-500, to=500, increment=5, textvariable=self.root_y_var).grid(row=3, column=1, sticky="ew")
        ttk.Label(page, text="游戏缩放倍率").grid(row=4, column=0, sticky="w", pady=7)
        ttk.Spinbox(page, from_=0.1, to=5.0, increment=0.05, textvariable=self.scale_var).grid(row=4, column=1, sticky="ew")
        ttk.Label(
            page,
            text="已验证 Y=300 可避开 Ready UI；也可直接在下方拖动角色，X/Y 参数会自动反向填充。",
            wraplength=760,
        ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(3, 12))

        ttk.Separator(page).grid(row=6, column=0, columnspan=3, sticky="ew", pady=8)
        ttk.Label(page, text="游戏内嵌定位预览", style="Section.TLabel").grid(row=7, column=0, columnspan=3, sticky="w")
        self.preview_canvas = tk.Canvas(
            page,
            height=400,
            bg="#11151c",
            highlightthickness=1,
            highlightbackground="#526071",
            cursor="fleur",
        )
        self.preview_canvas.grid(row=8, column=0, columnspan=3, sticky="nsew", pady=(8, 0))
        self.preview_canvas.bind("<Configure>", self._schedule_preview_render)
        self.preview_canvas.bind("<ButtonPress-1>", self._preview_drag_start)
        self.preview_canvas.bind("<B1-Motion>", self._preview_drag_motion)
        self.preview_canvas.bind("<ButtonRelease-1>", self._preview_drag_end)
        controls = ttk.Frame(page)
        controls.grid(row=9, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Button(controls, text="重新生成静态姿态", command=self._prepare_embedded_preview).pack(side="left")
        ttk.Button(controls, text="重置为 X=0 / Y=300", command=self._reset_preview_position).pack(side="left", padx=8)
        ttk.Label(controls, textvariable=self.preview_status_var).pack(side="right")
        ttk.Label(
            page,
            text="背景直接提取自 Hero Select 场景；静态姿态仅用于位置和遮挡判断。Ready 层按游戏真实坐标覆盖在角色前方。",
            wraplength=760,
        ).grid(row=10, column=0, columnspan=3, sticky="w", pady=(8, 0))

    def _target_label(self, target: SpineTarget) -> str:
        return f"{target.hero} — {target.skin}"

    def _selected_target(self) -> SpineTarget:
        value = self.target_var.get()
        for item in self.available_targets:
            if self._target_label(item) == value:
                return item
        raise ValueError("请选择替换目标。")

    def _placement(self) -> SpinePlacement:
        animation = self.animation_var.get().strip()
        if not animation:
            raise ValueError("请选择默认动画。")
        return SpinePlacement(
            animation=animation,
            root_x_offset=float(self.root_x_var.get()),
            root_y_offset=float(self.root_y_var.get()),
            scale_multiplier=float(self.scale_var.get()),
        )

    def _browse_game(self) -> None:
        value = filedialog.askdirectory(title="选择 The Bazaar 游戏目录")
        if value:
            self.game_var.set(value)
            self._save_settings()
            self.preview_metrics = None
            self.preview_metrics_key = None
            self._refresh_preview_metrics()

    def _browse_package(self) -> None:
        value = filedialog.askopenfilename(
            title="选择 Spine ZIP",
            filetypes=(("ZIP", "*.zip"), ("所有文件", "*.*")),
        )
        if value:
            self.package_var.set(value)
            self._import()

    def _detect_game_if_needed(self) -> None:
        if self.game_var.get() and Path(self.game_var.get()).is_dir():
            return
        installs = detect_installs()
        if installs:
            self.game_var.set(str(installs[0].game_dir))

    def _restore_target_selection(self) -> None:
        labels = [self._target_label(item) for item in self.available_targets]
        saved = self.settings.get("target")
        self.target_var.set(saved if saved in labels else (labels[0] if labels else ""))

    def _target_changed(self, _event=None) -> None:
        self.preview_metrics = None
        self.preview_metrics_key = None
        self._save_settings()
        self._refresh_preview_metrics()

    def _import(self) -> None:
        try:
            source = Path(self.package_var.get())
            self.package = import_spine_package(source)
            values = list(self.package.animations)
            self.animation_combo.configure(values=values)
            preferred = self.settings.get("animation")
            self.animation_var.set(preferred if preferred in values else ("a" if "a" in values else values[0]))
            self.package_info.configure(
                text=(
                    f"已导入：Spine {self.package.version}；纹理 {self.package.width}×{self.package.height}；"
                    f"动画 {', '.join(values)}；skins {', '.join(self.package.skins)}"
                )
            )
            self._prepare_embedded_preview()
            self._append("导入和格式验证完成。")
            self._save_settings()
        except Exception as error:
            LOGGER.exception("spine_import_failed source=%s", self.package_var.get())
            messagebox.showerror("导入失败", str(error), parent=self.root)

    def _prepare_embedded_preview(self) -> None:
        try:
            if self.package is None:
                self._import()
                return
            self.preview_status_var.set("正在生成离线静态姿态…")
            self.root.update_idletasks()
            started = time.perf_counter()
            self.preview_pose = render_setup_pose(self.package)
            LOGGER.info(
                "static_preview_pose_ready size=%s elapsed_seconds=%.3f",
                self.preview_pose.image.size,
                time.perf_counter() - started,
            )
            self.preview_metrics = None
            self.preview_metrics_key = None
            self._refresh_preview_metrics()
            self._render_embedded_preview()
        except Exception as error:
            LOGGER.exception("static_preview_prepare_failed")
            self.preview_status_var.set(f"预览生成失败：{error}")
            messagebox.showerror("预览失败", str(error), parent=self.root)

    def _refresh_preview_metrics(self) -> None:
        if self.package is None:
            self._schedule_preview_render()
            return
        try:
            game = explicit_install(Path(self.game_var.get()))
            target = self._selected_target()
            key = (str(game.game_dir.resolve()), target.adapter_id, str(self.package.json_path))
            if self.preview_metrics is None or self.preview_metrics_key != key:
                self.preview_metrics = calculate_preview_metrics(game, self.package, target)
                self.preview_metrics_key = key
                LOGGER.info(
                    "preview_metrics target=%s skeleton_scale=%.9f reference_pixels_per_unit=%.6f",
                    target.adapter_id,
                    self.preview_metrics.skeleton_data_scale,
                    self.preview_metrics.reference_pixels_per_spine_unit,
                )
            self._schedule_preview_render()
        except Exception as error:
            self.preview_metrics = None
            self.preview_metrics_key = None
            self.preview_status_var.set(f"无法读取游戏缩放：{error}")
            LOGGER.exception("preview_metrics_failed")
            self._schedule_preview_render()

    def _schedule_preview_render(self, *_args) -> None:
        if not hasattr(self, "preview_canvas"):
            return
        if self.preview_refresh_job is not None:
            self.root.after_cancel(self.preview_refresh_job)
        self.preview_refresh_job = self.root.after(35, self._render_embedded_preview)

    def _render_embedded_preview(self) -> None:
        self.preview_refresh_job = None
        canvas = self.preview_canvas
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
        background = self.preview_background.resize(
            (display_width, display_height), Image.Resampling.LANCZOS
        ).convert("RGBA")
        frame.alpha_composite(background, (origin_x, origin_y))
        draw = ImageDraw.Draw(frame, "RGBA")

        if self.preview_pose is not None and self.preview_metrics is not None:
            try:
                root_x_offset = float(self.root_x_var.get())
                root_y_offset = float(self.root_y_var.get())
                scale_multiplier = float(self.scale_var.get())
            except (tk.TclError, ValueError):
                root_x_offset, root_y_offset, scale_multiplier = 0.0, 300.0, 1.0
            game_pixels_per_unit = (
                self.preview_metrics.reference_pixels_per_spine_unit * scale_multiplier
            )
            self.preview_game_pixels_per_unit = game_pixels_per_unit
            self.preview_canvas_scale = frame_scale
            pose_scale = (
                game_pixels_per_unit
                / self.preview_pose.pixels_per_spine_unit
                * frame_scale
            )
            pose_width = max(1, round(self.preview_pose.image.width * pose_scale))
            pose_height = max(1, round(self.preview_pose.image.height * pose_scale))
            pose = self.preview_pose.image.resize(
                (pose_width, pose_height), Image.Resampling.LANCZOS
            )
            root_x = origin_x + (
                HERO_ROOT_X + root_x_offset * game_pixels_per_unit
            ) * frame_scale
            root_y = origin_y + (
                REFERENCE_HEIGHT - HERO_ROOT_Y - root_y_offset * game_pixels_per_unit
            ) * frame_scale
            root_in_pose_x = -self.preview_pose.min_x * game_pixels_per_unit * frame_scale
            root_in_pose_y = self.preview_pose.max_y * game_pixels_per_unit * frame_scale
            image_x = round(root_x - root_in_pose_x)
            image_y = round(root_y - root_in_pose_y)

            placeholder_width = 600 * 0.44 * frame_scale
            placeholder_height = 1000 * 0.44 * frame_scale
            draw.rectangle(
                (
                    root_x - placeholder_width / 2,
                    root_y - placeholder_height,
                    root_x + placeholder_width / 2,
                    root_y,
                ),
                outline=(255, 205, 80, 190),
                width=2,
            )
            frame.alpha_composite(pose, (image_x, image_y))

            ready_center_x = origin_x + READY_CENTER_X * frame_scale
            ready_center_y = origin_y + (
                REFERENCE_HEIGHT - READY_CENTER_Y
            ) * frame_scale
            ready_width = READY_WIDTH * frame_scale
            ready_height = READY_HEIGHT * frame_scale
            ready_box = (
                ready_center_x - ready_width / 2,
                ready_center_y - ready_height / 2,
                ready_center_x + ready_width / 2,
                ready_center_y + ready_height / 2,
            )
            draw.rounded_rectangle(
                ready_box,
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
                stroke_width=1,
                stroke_fill=(15, 34, 54, 255),
            )
            cross = 7
            draw.line((root_x - cross, root_y, root_x + cross, root_y), fill=(255, 70, 70, 255), width=2)
            draw.line((root_x, root_y - cross, root_x, root_y + cross), fill=(255, 70, 70, 255), width=2)
            self.preview_status_var.set(
                f"X={root_x_offset:g} / Y={root_y_offset:g} / 缩放={scale_multiplier:g}"
            )
        else:
            self.preview_canvas_scale = 0.0
            self.preview_game_pixels_per_unit = 0.0
            draw.text(
                (width / 2, height / 2),
                "请先导入 Spine 包并选择有效游戏目录",
                anchor="mm",
                fill=(255, 255, 255, 230),
            )

        self.preview_photo = ImageTk.PhotoImage(frame)
        canvas.delete("all")
        canvas.create_image(0, 0, image=self.preview_photo, anchor="nw")

    def _preview_drag_start(self, event: tk.Event) -> None:
        if self.preview_canvas_scale <= 0 or self.preview_game_pixels_per_unit <= 0:
            return
        self.preview_drag_origin = (
            event.x,
            event.y,
            float(self.root_x_var.get()),
            float(self.root_y_var.get()),
        )

    def _preview_drag_motion(self, event: tk.Event) -> None:
        if self.preview_drag_origin is None:
            return
        start_x, start_y, root_x, root_y = self.preview_drag_origin
        denominator = self.preview_canvas_scale * self.preview_game_pixels_per_unit
        if denominator <= 0:
            return
        next_x = root_x + (event.x - start_x) / denominator
        next_y = root_y - (event.y - start_y) / denominator
        self.root_x_var.set(round(max(-500, min(500, next_x)), 1))
        self.root_y_var.set(round(max(-500, min(500, next_y)), 1))

    def _preview_drag_end(self, _event: tk.Event) -> None:
        self.preview_drag_origin = None
        self._save_settings()

    def _reset_preview_position(self) -> None:
        self.root_x_var.set(0.0)
        self.root_y_var.set(300.0)
        self.scale_var.set(1.0)
        self._save_settings()

    def _deploy(self) -> None:
        if self.busy:
            return
        try:
            if self.package is None:
                self._import()
            if self.package is None:
                return
            game = explicit_install(Path(self.game_var.get()))
            target = self._selected_target()
            placement = self._placement()
        except Exception as error:
            messagebox.showerror("参数错误", str(error), parent=self.root)
            return
        if not messagebox.askyesno(
            "确认部署",
            "请先关闭游戏。将保留当前皮肤资产包，并通过皮肤管理器统一事务写入 Spine 替换。是否继续？",
            parent=self.root,
        ):
            return
        self._run_background(
            lambda progress: deploy(game, self.package, target, placement, progress),
            "部署完成。当前皮肤资产与 Spine 替换已由皮肤管理器统一托管。",
            "部署",
        )

    def _restore(self) -> None:
        if self.busy:
            return
        if not messagebox.askyesno(
            "移除 Spine 替换",
            "请先关闭游戏。确认移除全部 Spine 替换并保留当前皮肤资产包？",
            parent=self.root,
        ):
            return
        self._run_background(restore, "Spine 替换已移除；其他皮肤资产保持部署。", "恢复")

    def _run_background(self, action, success: str, operation: str) -> None:
        self.busy = True
        self.status_var.set(f"{operation}准备中…")
        started = time.perf_counter()
        LOGGER.info("operation_start operation=%s", operation)

        def worker():
            try:
                result = action(self._report_progress)
            except Exception as error:
                details = traceback.format_exc()
                LOGGER.exception(
                    "operation_failed operation=%s elapsed_seconds=%.3f",
                    operation,
                    time.perf_counter() - started,
                )
                self.root.after(
                    0,
                    lambda caught=error, stack=details: self._finish_error(caught, stack),
                )
                return
            LOGGER.info(
                "operation_complete operation=%s elapsed_seconds=%.3f",
                operation,
                time.perf_counter() - started,
            )
            self.root.after(0, lambda: self._finish_success(success, result))

        threading.Thread(
            target=worker,
            name=f"spine-manager-{operation}",
            daemon=True,
        ).start()

    def _report_progress(self, message: str) -> None:
        LOGGER.info("progress message=%s", message)
        self.root.after(0, self._show_progress, message)

    def _show_progress(self, message: str) -> None:
        self.status_var.set(message)
        self._append(message)

    def _finish_error(self, error: Exception, details: str) -> None:
        self.busy = False
        self.status_var.set("失败")
        self._append(f"失败：{error}")
        self._append(f"详细日志：{LOG_PATH}")
        LOGGER.error("ui_operation_error error=%s\n%s", error, details)
        messagebox.showerror(
            "操作失败",
            f"{error}\n\n完整诊断日志：\n{LOG_PATH}",
            parent=self.root,
        )

    def _finish_success(self, message: str, result) -> None:
        self.busy = False
        self.status_var.set("完成")
        self._append(message)
        self._append(json.dumps(result, ensure_ascii=False, indent=2) if result else "")
        self._refresh_status()
        messagebox.showinfo("完成", message, parent=self.root)

    def _refresh_status(self) -> None:
        record = installation_manifest()
        if record:
            placement = record.get("placement") or {}
            self.status_var.set(
                f"已托管 {record.get('count', 1)} 个 Spine 替换；当前："
                f"{record['target']['hero']}，Y={placement.get('root_y_offset')}，"
                f"缩放={placement.get('scale_multiplier')}"
            )
        else:
            self.status_var.set("当前未部署 Spine 替换")

    def _append(self, text: str) -> None:
        if not text:
            return
        self.log.configure(state="normal")
        self.log.insert("end", text.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _open_log_directory(self) -> None:
        try:
            LOG_DIRECTORY.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                os.startfile(LOG_DIRECTORY)
            else:
                webbrowser.open(LOG_DIRECTORY.as_uri())
        except Exception as error:
            LOGGER.exception("open_log_directory_failed path=%s", LOG_DIRECTORY)
            messagebox.showerror("无法打开日志目录", str(error), parent=self.root)

    def _load_settings(self) -> dict:
        try:
            return json.loads(SETTINGS_PATH.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, json.JSONDecodeError):
            return {}

    def _save_settings(self) -> None:
        payload = {
            "game_dir": self.game_var.get(),
            "package": self.package_var.get(),
            "target": self.target_var.get(),
            "animation": self.animation_var.get(),
            "root_x": self.root_x_var.get(),
            "root_y": self.root_y_var.get(),
            "scale": self.scale_var.get(),
        }
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _close(self) -> None:
        self._save_settings()
        self.root.destroy()


def self_test() -> int:
    available = targets()
    if not available:
        raise RuntimeError("No Spine-compatible adapters were found.")
    if not PREVIEW_BACKGROUND_PATH.is_file():
        raise RuntimeError("Bundled Hero Select preview background is missing.")
    with Image.open(PREVIEW_BACKGROUND_PATH) as background:
        if background.size != (1920, 1080):
            raise RuntimeError("Bundled preview background has an unexpected size.")
    print(json.dumps({"version": APP_VERSION, "targets": len(available)}))
    return 0


def main() -> int:
    configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--smoke-import")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if args.smoke_import:
        package = import_spine_package(Path(args.smoke_import))
        pose = render_setup_pose(package)
        print(
            json.dumps(
                {
                    "version": package.version,
                    "animations": package.animations,
                    "offline_preview_size": pose.image.size,
                }
            )
        )
        return 0
    root = tk.Tk()
    SpineManagerApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
