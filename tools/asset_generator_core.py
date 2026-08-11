#!/usr/bin/env python3
"""End-to-end deterministic asset generation, Manager import, and deployment.

The generator owns raster derivation and pack construction. Deployment is
delegated to :class:`mod_studio_core.StudioWorkspace`, so this module does not
duplicate any Skin Manager install or rollback behavior.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from PIL import Image

from adapter_registry import AdapterRegistry, DEFAULT_ADAPTER_DIRECTORY
from bazaar_skin_manager import (
    installation_diagnostics,
    local_app_data,
    preferred_game_install,
    sha256_file,
)
from badge_pipeline import compose_badge, extract_game_template
from mod_studio_core import PROJECT_ROOT, WORKSPACES_ROOT, StudioWorkspace
from skin_pack_builder import (
    _load_badge_template,
    alpha_contain_scale,
    apply_declared_clip_mask,
    fit_alpha_contain,
    fit_cover,
    generate_pack,
    remove_edge_connected_background,
    scaled_target_bounds,
    split_authored_underlay,
    translate_rgba,
)


PROFILE_SCHEMA = 1
AUTHORING_RECIPE_ID = "deterministic-raster-v1"
AUTHORING_RECIPE_VERSION = 2
ASSET_GENERATOR_VERSION = "1.4.7"
ProgressCallback = Callable[[str, str], None]


def _noop_progress(_stage: str, _message: str) -> None:
    return None


def _safe_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _resolve_path(value: str | Path, *, base: Path) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def authoring_adapters(registry: AdapterRegistry | None = None) -> tuple:
    """Return only targets the deterministic generator can actually author.

    A hero merely being deployable by Skin Manager is not sufficient: its
    verified adapter must also define the geometry and layer recipe used to
    derive every output asset.
    """
    registry = registry or AdapterRegistry.load(DEFAULT_ADAPTER_DIRECTORY)
    return tuple(
        adapter
        for adapter in registry.records
        if (adapter.payload.get("authoring_recipe") or {}).get("id")
        == AUTHORING_RECIPE_ID
        and int(
            (adapter.payload.get("authoring_recipe") or {}).get("version") or 0
        )
        == AUTHORING_RECIPE_VERSION
    )


def ensure_local_badge_assets(
    destination: Path,
    *,
    source_root: Path | None = None,
    game_dir: Path | None = None,
    strict: bool = False,
) -> Path:
    """Prepare the game-derived hero badge template outside the public source.

    Local maintainer builds may bundle an already-audited template.  Public
    builds intentionally do not, so the same deterministic layers are
    extracted from the user's installed game the first time authoring needs
    them.
    """

    destination = destination.resolve()
    template = destination / "badge-templates" / "hero-select-gold" / "template.json"
    if template.is_file():
        return destination
    if source_root is not None and source_root.resolve().is_dir():
        shutil.copytree(source_root.resolve(), destination, dirs_exist_ok=True)
        if template.is_file():
            return destination
    destination.mkdir(parents=True, exist_ok=True)
    try:
        game = preferred_game_install(game_dir)
        extract_game_template(
            game.game_dir,
            template.parent,
        )
    except Exception:
        if strict:
            raise
    return destination


def retarget_automatic_pack_id(
    pack_id: str,
    previous_hero: str,
    selected_hero: str,
) -> str:
    """Retarget only ids created automatically for a new generator project."""

    parts = pack_id.strip().split(".")
    if (
        len(parts) == 3
        and parts[0].casefold() == "local"
        and parts[1].casefold() == previous_hero.casefold()
        and len(parts[2]) == 10
        and all(character in "0123456789abcdefABCDEF" for character in parts[2])
    ):
        return f"local.{selected_hero.casefold()}.{parts[2]}"
    return pack_id


def profile_for_workspace_edit(
    workspace: StudioWorkspace,
    *,
    profile_path: Path,
    badge_template_root: Path,
    workspace_root: Path,
    output_zip: Path,
    game_dir: Path | None = None,
    registry: AdapterRegistry | None = None,
    input_search_roots: Iterable[Path] = (),
) -> "GeneratorProfile":
    """Rehydrate a generator profile from a Manager library workspace.

    Generated packs archive their original inputs and deterministic adjustment
    metadata under ``authoring``.  Reusing that data is what makes Manager's
    Edit action a real round trip instead of starting a visually similar but
    unrelated pack.  The pack id is deliberately preserved alongside the
    display name: Manager replaces library entries by id, while the name alone
    is not unique.

    Legacy packs may not contain authoring inputs.  They still open with the
    original identity and target, but with empty material fields so the user is
    never tricked into regenerating from a lossy rendered output.
    """

    registry = registry or AdapterRegistry.load(DEFAULT_ADAPTER_DIRECTORY)
    state = workspace.state
    pack = state.get("pack") or {}
    target = state.get("target") or {}
    root_authoring = state.get("authoring") or {}
    authoring = root_authoring
    if root_authoring.get("mode") == "manual_slots":
        authoring = root_authoring.get("automatic_draft") or root_authoring
    generator = authoring.get("generator") or {}

    adapter_id = str(generator.get("adapter_id") or "").strip()
    adapter = registry.find_by_id(adapter_id) if adapter_id else None
    if adapter is None:
        adapter = registry.find(
            str(target.get("hero") or ""),
            str(target.get("skin") or ""),
        )
    if adapter is None or adapter not in authoring_adapters(registry):
        raise ValueError(
            "该皮肤包的目标没有可用的确定性制作配方："
            f"{target.get('hero') or '未知英雄'} / {target.get('skin') or '未知皮肤'}"
        )

    edit_root = profile_path.resolve().parent
    metadata_path = edit_root / "inputs" / "edit-input-metadata.json"
    input_records = authoring.get("inputs") or {}
    input_names = ("character", "background", "small_icon", "small_icon_source")
    direct_paths: dict[str, Path | None] = {}
    for input_name in input_names:
        record = input_records.get(input_name) or {}
        relative = str(record.get("workspace_file") or "").strip()
        candidate = (workspace.directory / relative).resolve() if relative else None
        direct_paths[input_name] = (
            candidate
            if candidate is not None
            and _safe_relative_to(candidate, workspace.directory)
            and candidate.is_file()
            else None
        )
    missing_hashes = {
        str(record.get("sha256") or "").casefold()
        for input_name, record in input_records.items()
        if input_name in input_names
        and direct_paths[input_name] is None
        and str(record.get("sha256") or "").strip()
    }
    recovered_by_hash: dict[str, Path] = {}
    for search_root in input_search_roots:
        root = Path(search_root).resolve()
        if not root.is_dir() or recovered_by_hash.keys() >= missing_hashes:
            continue
        for candidate in root.rglob("*"):
            if recovered_by_hash.keys() >= missing_hashes:
                break
            if (
                not candidate.is_file()
                or candidate.suffix.casefold()
                not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
            ):
                continue
            digest = sha256_file(candidate).casefold()
            if digest in missing_hashes and digest not in recovered_by_hash:
                recovered_by_hash[digest] = candidate.resolve()
    input_paths: dict[str, Path | None] = {}
    metadata: dict[str, dict] = {}
    generated_fields = {"sha256", "bytes", "image_size", "workspace_file"}
    for input_name in input_names:
        record = input_records.get(input_name) or {}
        if direct_paths[input_name] is not None:
            input_paths[input_name] = direct_paths[input_name]
            metadata[input_name] = {
                key: value
                for key, value in record.items()
                if key not in generated_fields
            }
            metadata[input_name].setdefault("aigc", False)
        elif str(record.get("sha256") or "").casefold() in recovered_by_hash:
            input_paths[input_name] = recovered_by_hash[
                str(record.get("sha256") or "").casefold()
            ]
            metadata[input_name] = {
                key: value
                for key, value in record.items()
                if key not in generated_fields
            }
            metadata[input_name].setdefault("aigc", False)
        else:
            input_paths[input_name] = None

    # Never keep the active edit profile pointed at a generated workspace or
    # library workspace. The generator cleans its output directory before each
    # build, and Manager replaces the library directory after a successful
    # import. A private edit-session copy survives both lifecycle operations.
    for input_name, source in tuple(input_paths.items()):
        if source is None:
            continue
        extension = source.suffix.casefold() or ".png"
        destination = metadata_path.parent / f"edit-{input_name}{extension}"
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != destination.resolve():
            shutil.copy2(source, destination)
        input_paths[input_name] = destination.resolve()

    metadata.setdefault("character", {"origin": "not_provided", "aigc": False})
    metadata.setdefault("background", {"origin": "not_provided", "aigc": False})
    metadata.setdefault("small_icon", {"origin": "not_provided", "aigc": False})
    metadata.setdefault(
        "small_icon_source", {"origin": "not_provided", "aigc": False}
    )
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    missing = metadata_path.parent / "not-provided"
    adjustments = authoring.get("adjustments") or {}
    character_canvas = adjustments.get("character_canvas") or (0, 0)
    background_adjustment = adjustments.get("background") or {}
    background_offset = background_adjustment.get("offset") or (0, 0)
    per_output = adjustments.get("per_output") or {}
    small_icon_record = input_records.get("small_icon") or {}
    small_icon_mode = "none"
    if input_paths["small_icon"] is not None:
        preset = str(small_icon_record.get("preset") or "").casefold()
        small_icon_mode = (
            preset
            if preset in {"outline", "block-gaps", "silhouette"}
            else "user"
        )

    return GeneratorProfile(
        profile_path=profile_path.resolve(),
        adapter_id=adapter.adapter_id,
        pack_id=str(pack.get("id") or workspace.directory.name).strip(),
        name=str(pack.get("name") or pack.get("id") or workspace.directory.name).strip(),
        version=str(pack.get("version") or "0.1.0").strip(),
        character=input_paths["character"] or missing / "character",
        background=input_paths["background"] or missing / "background",
        small_icon=input_paths["small_icon"] or missing / "small-icon",
        small_icon_source=input_paths["small_icon_source"],
        input_metadata=metadata_path,
        badge_template_root=badge_template_root.resolve(),
        workspace_root=workspace_root.resolve(),
        output_zip=output_zip.resolve(),
        game_dir=game_dir.resolve() if game_dir is not None else None,
        character_offset_x=int(character_canvas[0]) if len(character_canvas) > 0 else 0,
        character_offset_y=int(character_canvas[1]) if len(character_canvas) > 1 else 0,
        character_scale=float(adjustments.get("character_scale") or 1.0),
        background_offset_x=int(background_offset[0]) if len(background_offset) > 0 else 0,
        background_offset_y=int(background_offset[1]) if len(background_offset) > 1 else 0,
        background_scale=float(background_adjustment.get("scale") or 1.0),
        output_offsets={
            str(slot): (int(offset[0]), int(offset[1]))
            for slot, offset in per_output.items()
            if isinstance(offset, (list, tuple)) and len(offset) >= 2
        },
        small_icon_mode=small_icon_mode,
    )


def installed_manager_metadata_path(install_root: Path | None = None) -> Path:
    root = install_root or (
        local_app_data() / "Programs" / "TheBazaarModManager"
    )
    return root / "manager-build.json"


def require_installed_manager_adapter(
    adapter_id: str,
    *,
    install_root: Path | None = None,
    registry: AdapterRegistry | None = None,
) -> dict:
    """Fail before import when the installed Manager cannot accept the pack.

    The generator and Manager are separate frozen applications, so each has
    its own verified adapter registry. The installed build sidecar is the
    explicit capability handshake between them.
    """
    registry = registry or AdapterRegistry.load(DEFAULT_ADAPTER_DIRECTORY)
    expected = registry.find_by_id(adapter_id)
    if expected is None:
        raise ValueError(f"Unknown adapter: {adapter_id}")

    metadata_path = installed_manager_metadata_path(install_root)
    if not metadata_path.is_file():
        raise RuntimeError(
            "未找到已安装 Skin Manager 的能力信息；请先安装或升级 Skin Manager。"
        )
    metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
    capabilities = metadata.get("adapters")
    manager_version = str(metadata.get("version") or "未知版本")
    if not isinstance(capabilities, list):
        raise RuntimeError(
            f"已安装 Skin Manager {manager_version} 未公布英雄适配能力；"
            "请升级到 1.1.2 或更高版本。"
        )
    installed = next(
        (
            item
            for item in capabilities
            if str(item.get("id") or "").casefold() == adapter_id.casefold()
        ),
        None,
    )
    if installed is None:
        raise RuntimeError(
            f"已安装 Skin Manager {manager_version} 不支持适配器 "
            f"{adapter_id}（{expected.hero} / {expected.skin}）；请升级 Skin Manager。"
        )
    installed_version = int(installed.get("adapter_version") or 0)
    if installed_version != expected.adapter_version:
        raise RuntimeError(
            f"素材包需要适配器 {adapter_id} v{expected.adapter_version}，"
            f"已安装 Skin Manager {manager_version} 提供 v{installed_version}；"
            "请安装与素材包制作器配套的 Skin Manager。"
        )
    return {
        "manager_version": manager_version,
        "adapter": installed,
        "metadata_path": str(metadata_path.resolve()),
    }


@dataclass(frozen=True)
class GeneratorProfile:
    profile_path: Path
    adapter_id: str
    pack_id: str
    name: str
    version: str
    character: Path
    background: Path
    small_icon: Path
    input_metadata: Path
    badge_template_root: Path
    workspace_root: Path
    output_zip: Path
    game_dir: Path | None = None
    small_icon_source: Path | None = None
    character_offset_x: int = 0
    character_offset_y: int = 0
    background_offset_x: int = 0
    background_offset_y: int = 0
    output_offsets: dict[str, tuple[int, int]] = field(default_factory=dict)
    small_icon_mode: str = "none"
    character_scale: float = 1.0
    background_scale: float = 1.0

    @classmethod
    def load(cls, path: Path, *, validate: bool = True) -> "GeneratorProfile":
        path = path.resolve()
        payload = json.loads(path.read_text(encoding="utf-8"))
        if int(payload.get("schema_version") or 0) != PROFILE_SCHEMA:
            raise ValueError(
                f"Unsupported generator profile schema: {payload.get('schema_version')}"
            )
        pack = payload.get("pack") or {}
        inputs = payload.get("inputs") or {}
        paths = payload.get("paths") or {}
        adjustment = payload.get("character_adjustment") or {}
        background_adjustment = payload.get("background_adjustment") or {}
        output_adjustments = payload.get("output_adjustments") or {}
        small_icon_generation = payload.get("small_icon_generation") or {}
        # Repository profiles are intentionally relocatable. External profiles
        # may opt into profile-relative paths for portable authoring folders.
        relative_to = str(payload.get("relative_to") or "project").casefold()
        if relative_to == "project":
            base = PROJECT_ROOT
        elif relative_to == "profile":
            base = path.parent
        else:
            raise ValueError("relative_to must be 'project' or 'profile'.")
        game_dir = paths.get("game_dir")
        small_icon_source = inputs.get("small_icon_source")
        profile = cls(
            profile_path=path,
            adapter_id=str(payload.get("adapter_id") or "").strip(),
            pack_id=str(pack.get("id") or "").strip(),
            name=str(pack.get("name") or "").strip(),
            version=str(pack.get("version") or "").strip(),
            character=_resolve_path(inputs.get("character", ""), base=base),
            background=_resolve_path(inputs.get("background", ""), base=base),
            small_icon=_resolve_path(inputs.get("small_icon", ""), base=base),
            input_metadata=_resolve_path(inputs.get("metadata", ""), base=base),
            badge_template_root=_resolve_path(
                paths.get("badge_template_root", "manager/assets"), base=base
            ),
            workspace_root=_resolve_path(
                paths.get("workspace_root", ".codex-work/asset-generator/workspaces"),
                base=base,
            ),
            output_zip=_resolve_path(paths.get("output_zip", ""), base=base),
            game_dir=(
                _resolve_path(game_dir, base=base) if str(game_dir or "").strip() else None
            ),
            small_icon_source=(
                _resolve_path(small_icon_source, base=base)
                if str(small_icon_source or "").strip()
                else None
            ),
            character_offset_x=int(adjustment.get("offset_x") or 0),
            character_offset_y=int(adjustment.get("offset_y") or 0),
            character_scale=float(adjustment.get("scale") or 1.0),
            background_offset_x=int(background_adjustment.get("offset_x") or 0),
            background_offset_y=int(background_adjustment.get("offset_y") or 0),
            background_scale=float(background_adjustment.get("scale") or 1.0),
            output_offsets={
                str(slot): (
                    int((value or {}).get("offset_x") or 0),
                    int((value or {}).get("offset_y") or 0),
                )
                for slot, value in output_adjustments.items()
            },
            small_icon_mode=str(
                small_icon_generation.get("mode") or "none"
            ).casefold(),
        )
        if profile.small_icon.is_file() and profile.small_icon_mode == "none":
            # A concrete user input is authoritative. "None" is the fallback
            # for an empty field, not a veto that may silently discard a file.
            profile = cls(**{**asdict(profile), "small_icon_mode": "user"})
        if validate:
            profile.validate()
        return profile

    def validate(self) -> None:
        for label, value in (
            ("adapter_id", self.adapter_id),
            ("pack.id", self.pack_id),
            ("pack.name", self.name),
            ("pack.version", self.version),
        ):
            if not value:
                raise ValueError(f"Generator profile is missing {label}.")
        if not any(
            adapter.adapter_id.casefold() == self.adapter_id.casefold()
            for adapter in authoring_adapters()
        ):
            raise ValueError(
                f"Adapter {self.adapter_id} has no supported deterministic recipe."
            )
        for label, path in (
            ("character", self.character),
            ("input metadata", self.input_metadata),
        ):
            if not path.is_file():
                raise FileNotFoundError(f"Missing {label}: {path}")
        if not self.badge_template_root.is_dir():
            raise FileNotFoundError(
                f"Missing badge template root: {self.badge_template_root}"
            )
        if self.output_zip.suffix.casefold() != ".zip":
            raise ValueError("paths.output_zip must end in .zip")
        if self.game_dir is not None and not self.game_dir.is_dir():
            raise FileNotFoundError(f"Game directory does not exist: {self.game_dir}")
        metadata = json.loads(self.input_metadata.read_text(encoding="utf-8"))
        for key, path in (
            ("character", self.character),
            ("background", self.background),
            ("small_icon", self.small_icon),
            ("small_icon_source", self.small_icon_source),
        ):
            if path is not None and path.is_file() and key not in metadata:
                raise ValueError(f"Input metadata is missing {key}.")
        with Image.open(self.character) as source:
            if source.width < 64 or source.height < 64:
                raise ValueError("Character source is too small for the asset pipeline.")
            if abs(self.character_offset_x) >= source.width:
                raise ValueError("Character horizontal offset moves the complete source off-canvas.")
            if abs(self.character_offset_y) >= source.height:
                raise ValueError("Character vertical offset moves the complete source off-canvas.")
        if self.small_icon.is_file():
            with Image.open(self.small_icon) as source:
                if source.convert("RGBA").getchannel("A").getbbox() is None:
                    raise ValueError(
                        "用户提供的小图标没有可见像素；请重新导入，并关闭扣色或降低容差。"
                    )
        for slot, offset in self.output_offsets.items():
            if not slot or len(offset) != 2:
                raise ValueError("Each output adjustment must name a slot and contain X/Y.")
            if max(abs(int(offset[0])), abs(int(offset[1]))) > 16384:
                raise ValueError(f"Output adjustment is unreasonably large: {slot}")
        if not 0.25 <= self.character_scale <= 3.0:
            raise ValueError("Character scale must be between 25% and 300%.")
        if max(abs(self.background_offset_x), abs(self.background_offset_y)) > 16384:
            raise ValueError("Background crop offset is unreasonably large.")
        if not 1.0 <= self.background_scale <= 3.0:
            raise ValueError("Background scale must be between 100% and 300%.")
        if self.small_icon_mode not in {
            "none",
            "user",
            "outline",
            "block-gaps",
            "silhouette",
        }:
            raise ValueError(f"Unsupported small icon mode: {self.small_icon_mode}")

    @property
    def generated_workspace(self) -> Path:
        return self.workspace_root / self.pack_id

    def save(self, path: Path | None = None) -> Path:
        path = (path or self.profile_path).resolve()
        base = path.parent

        def portable(value: Path | None) -> str | None:
            if value is None:
                return None
            try:
                return Path(os.path.relpath(value.resolve(), base)).as_posix()
            except ValueError:
                return str(value.resolve())

        payload = {
            "schema_version": PROFILE_SCHEMA,
            "relative_to": "profile",
            "adapter_id": self.adapter_id,
            "pack": {
                "id": self.pack_id,
                "name": self.name,
                "version": self.version,
            },
            "inputs": {
                "character": portable(self.character),
                "background": portable(self.background),
                "small_icon": portable(self.small_icon),
                "small_icon_source": portable(self.small_icon_source),
                "metadata": portable(self.input_metadata),
            },
            "character_adjustment": {
                "offset_x": self.character_offset_x,
                "offset_y": self.character_offset_y,
                "scale": self.character_scale,
            },
            "background_adjustment": {
                "offset_x": self.background_offset_x,
                "offset_y": self.background_offset_y,
                "scale": self.background_scale,
                "fit": "cover",
            },
            "output_adjustments": {
                slot: {"offset_x": offset[0], "offset_y": offset[1]}
                for slot, offset in sorted(self.output_offsets.items())
                if offset != (0, 0)
            },
            "small_icon_generation": {
                "mode": self.small_icon_mode,
            },
            "paths": {
                "badge_template_root": portable(self.badge_template_root),
                "workspace_root": portable(self.workspace_root),
                "output_zip": portable(self.output_zip),
                "game_dir": str(self.game_dir.resolve()) if self.game_dir else None,
            },
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
        return path


@dataclass(frozen=True)
class PipelineResult:
    profile: str
    generated_workspace: str | None = None
    output_zip: str | None = None
    zip_sha256: str | None = None
    generated_visual_slots: list[str] | None = None
    skipped_visual_slots: list[str] | None = None
    manager_workspace: str | None = None
    imported_kind: str | None = None
    deployed_game: str | None = None
    doctor_healthy: bool | None = None
    doctor: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class LivePreviewRenderer:
    """Render adapter slots in memory using the same declared layer recipes.

    Expensive input decoding and foreground preparation happen once. Mouse
    movement then only refits/composites the requested outputs, so the UI can
    update without writing a temporary pack on every drag event.
    """

    def __init__(self, profile: GeneratorProfile) -> None:
        if not profile.character.is_file():
            raise FileNotFoundError(f"Missing character: {profile.character}")
        if not profile.input_metadata.is_file():
            raise FileNotFoundError(f"Missing input metadata: {profile.input_metadata}")
        if not profile.badge_template_root.is_dir():
            raise FileNotFoundError(
                f"Missing badge template root: {profile.badge_template_root}"
            )
        registry = AdapterRegistry.load(DEFAULT_ADAPTER_DIRECTORY)
        adapter = registry.find_by_id(profile.adapter_id)
        if adapter is None:
            raise ValueError(f"Unknown adapter: {profile.adapter_id}")
        self.recipe = adapter.payload.get("authoring_recipe") or {}
        self.output_recipes = self.recipe.get("outputs") or {}
        metadata = json.loads(profile.input_metadata.read_text(encoding="utf-8"))
        self.loaded_inputs: dict[str, Image.Image] = {}
        for name, path in {
            "character": profile.character,
            "background": profile.background,
            "small_icon": profile.small_icon,
        }.items():
            if path.is_file():
                with Image.open(path) as loaded:
                    self.loaded_inputs[name] = loaded.convert("RGBA")
            elif name != "character":
                self.loaded_inputs[name] = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        character_metadata = metadata.get("character") or {}
        authoritative_alpha = bool(character_metadata.get("authoritative_alpha"))
        removal = (self.recipe.get("foreground") or {}).get("remove_background") or {}
        self.foreground = (
            self.loaded_inputs["character"].copy()
            if authoritative_alpha
            else remove_edge_connected_background(
                self.loaded_inputs["character"],
                tolerance=int(removal.get("tolerance", 34)),
                feather=int(removal.get("feather", 30)),
            )
        )
        transparency = None if authoritative_alpha else (
            (self.recipe.get("foreground") or {}).get("transparent_lassos")
        )
        if transparency:
            self.foreground, _discarded, _mask = split_authored_underlay(
                self.foreground, transparency
            )
        self.fit_reference = self.foreground.copy()
        cast_shadow = None if authoritative_alpha else (
            (self.recipe.get("foreground") or {}).get("cast_shadow_lasso")
        )
        self.character_shadow: Image.Image | None = None
        if cast_shadow:
            self.foreground, self.character_shadow, _mask = split_authored_underlay(
                self.foreground, cast_shadow
            )
        self.badge_template_root = profile.badge_template_root

    def _resolve_recipe(self, slot: str) -> dict:
        if slot not in self.output_recipes:
            raise ValueError(f"Unknown preview slot: {slot}")
        recipe = self.output_recipes[slot]
        visited = {slot}
        while recipe.get("alias_of"):
            alias = str(recipe["alias_of"])
            if alias in visited or alias not in self.output_recipes:
                raise ValueError(f"Invalid output alias chain for {slot}")
            visited.add(alias)
            recipe = self.output_recipes[alias]
        return recipe

    def size(self, slot: str) -> tuple[int, int]:
        return tuple(int(value) for value in self._resolve_recipe(slot)["size"])

    def render(
        self,
        slot: str,
        *,
        character_canvas_offset: tuple[int, int] = (0, 0),
        character_scale: float = 1.0,
        background_offset: tuple[int, int] = (0, 0),
        background_scale: float = 1.0,
        local_offset: tuple[int, int] = (0, 0),
    ) -> Image.Image:
        output_recipe = self._resolve_recipe(slot)
        renderer = output_recipe.get("renderer", "layers")
        if renderer == "layered_badge":
            template_images, _metadata = _load_badge_template(
                output_recipe["template"], template_root=self.badge_template_root
            )
            crop = output_recipe.get("character_crop", [0.0, 0.0, 1.0, 1.0])
            crop_box = (
                round(float(crop[0]) * self.foreground.width),
                round(float(crop[1]) * self.foreground.height),
                round(float(crop[2]) * self.foreground.width),
                round(float(crop[3]) * self.foreground.height),
            )
            foreground = self.foreground.crop(crop_box)
            reference = self.fit_reference.crop(crop_box)
            shadow = (
                None
                if self.character_shadow is None
                else self.character_shadow.crop(crop_box)
            )
            anchor = tuple(
                float(value) for value in output_recipe.get("anchor", [0.5, 1.0])
            )
            bounds = scaled_target_bounds(
                tuple(int(value) for value in output_recipe["target_alpha_bounds"]),
                character_scale,
                anchor=anchor,
            )
            scale = alpha_contain_scale(reference, target_bounds=bounds)
            output_size = tuple(int(value) for value in output_recipe["size"])
            template_size = template_images["base"].size
            dx = round(character_canvas_offset[0] * scale) + round(
                local_offset[0] * template_size[0] / output_size[0]
            )
            dy = round(character_canvas_offset[1] * scale) + round(
                local_offset[1] * template_size[1] / output_size[1]
            )
            shifted_bounds = (
                bounds[0] + dx,
                bounds[1] + dy,
                bounds[2] + dx,
                bounds[3] + dy,
            )
            return compose_badge(
                foreground,
                shadow=shadow,
                fit_reference=reference,
                base=template_images["base"],
                frame_upper=template_images["frame_upper"],
                frame_lower=template_images["frame_lower"],
                frame_lower_occlusion=template_images["frame_lower_occlusion"],
                target_bounds=shifted_bounds,
                output_size=output_size,
            )
        if renderer != "layers":
            raise ValueError(f"Unsupported preview renderer for {slot}: {renderer}")
        size = tuple(int(value) for value in output_recipe["size"])
        rendered = Image.new("RGBA", size, (0, 0, 0, 0))
        for declaration in output_recipe.get("layers") or []:
            if declaration.get("optional") and declaration.get("input") not in self.loaded_inputs:
                continue
            layer = dict(declaration)
            for condition_input, overrides in layer.pop("when_input_present", {}).items():
                if condition_input in self.loaded_inputs:
                    layer.update(overrides)
            input_name = layer["input"]
            if input_name == "character":
                source = self.foreground
                reference = self.fit_reference
            elif input_name == "character_shadow":
                if self.character_shadow is None:
                    continue
                source = self.character_shadow
                reference = self.fit_reference
            else:
                source = self.loaded_inputs[input_name]
                reference = None
            if layer.get("flip_x"):
                source = source.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
                if reference is not None:
                    reference = reference.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            fit = layer.get("fit")
            if fit == "cover":
                effective_background_offset = (
                    round(background_offset[0] * size[0] / 1024),
                    round(background_offset[1] * size[1] / 1024),
                )
                fitted = fit_cover(
                    source,
                    size=size,
                    zoom=background_scale if input_name == "background" else 1.0,
                    offset=(
                        effective_background_offset
                        if input_name == "background"
                        else (0, 0)
                    ),
                )
            elif fit == "alpha_contain":
                bounds = tuple(
                    int(value)
                    for value in layer.get(
                        "target_alpha_bounds", output_recipe["target_alpha_bounds"]
                    )
                )
                anchor = tuple(
                    float(value)
                    for value in layer.get(
                        "anchor", output_recipe.get("anchor", [0.5, 1.0])
                    )
                )
                if input_name in {"character", "character_shadow"}:
                    bounds = scaled_target_bounds(
                        bounds,
                        character_scale,
                        anchor=anchor,
                    )
                if source.getchannel("A").getbbox() is None:
                    fitted = Image.new("RGBA", size, (0, 0, 0, 0))
                else:
                    fitted = fit_alpha_contain(
                        source,
                        size=size,
                        target_bounds=bounds,
                        anchor=anchor,
                        fit_reference=reference,
                    )
            else:
                raise ValueError(f"Unsupported fit mode for {slot}: {fit}")
            if input_name in {"character", "character_shadow"}:
                scale = alpha_contain_scale(
                    reference if reference is not None else source,
                    target_bounds=bounds,
                )
                fitted = translate_rgba(
                    fitted,
                    (
                        round(character_canvas_offset[0] * scale) + local_offset[0],
                        round(character_canvas_offset[1] * scale) + local_offset[1],
                    ),
                )
            elif input_name == "small_icon":
                fitted = translate_rgba(fitted, local_offset)
            fitted = apply_declared_clip_mask(fitted, layer.get("clip_mask"))
            rendered.alpha_composite(fitted)
        return rendered

    def render_portrait_composite(
        self,
        *,
        character_canvas_offset: tuple[int, int] = (0, 0),
        character_scale: float = 1.0,
        background_offset: tuple[int, int] = (0, 0),
        background_scale: float = 1.0,
        local_offset: tuple[int, int] = (0, 0),
    ) -> Image.Image:
        """Preview the game's separate encounter background/foreground stack.

        The exported assets remain separate. Flattening the background into
        ``portrait_gameplay`` would put a square above the native frame; this
        helper only mirrors the final on-screen composition for authoring.
        """
        background = self.render(
            "portrait_background",
            background_offset=background_offset,
            background_scale=background_scale,
        )
        foreground = self.render(
            "portrait_gameplay",
            character_canvas_offset=character_canvas_offset,
            character_scale=character_scale,
            local_offset=local_offset,
        )
        if background.size != foreground.size:
            raise ValueError("Portrait foreground/background sizes do not match.")
        background.alpha_composite(foreground)
        return background


def clean_generated_workspace(profile: GeneratorProfile) -> None:
    target = profile.generated_workspace.resolve()
    root = profile.workspace_root.resolve()
    if target == root or not _safe_relative_to(target, root):
        raise ValueError(f"Unsafe generator workspace cleanup target: {target}")
    if target.exists():
        shutil.rmtree(target)


def apply_character_offset(
    source: Image.Image,
    offset_x: int,
    offset_y: int,
) -> Image.Image:
    """Translate RGBA pixels on their existing canvas with deterministic clipping."""
    image = source.convert("RGBA")
    width, height = image.size
    if abs(offset_x) >= width or abs(offset_y) >= height:
        raise ValueError("Character offset moves the complete source off-canvas.")
    output = Image.new("RGBA", image.size, (0, 0, 0, 0))
    source_left = max(0, -offset_x)
    source_top = max(0, -offset_y)
    source_right = min(width, width - offset_x)
    source_bottom = min(height, height - offset_y)
    if source_right <= source_left or source_bottom <= source_top:
        return output
    clipped = image.crop((source_left, source_top, source_right, source_bottom))
    output.alpha_composite(
        clipped,
        dest=(max(0, offset_x), max(0, offset_y)),
    )
    return output


def generate_assets(
    profile: GeneratorProfile,
    *,
    clean: bool = True,
    progress: ProgressCallback = _noop_progress,
) -> dict:
    progress("validate", "正在校验人物源图、元数据和徽章模板")
    profile.validate()
    if clean:
        progress("clean", "正在清理上一次生成工作区")
        clean_generated_workspace(profile)
    profile.workspace_root.mkdir(parents=True, exist_ok=True)
    profile.output_zip.parent.mkdir(parents=True, exist_ok=True)
    metadata = json.loads(profile.input_metadata.read_text(encoding="utf-8"))
    present_materials = ["人物"]
    if profile.background.is_file():
        present_materials.append("背景")
    if profile.small_icon.is_file():
        present_materials.append("小图标")
    progress("generate", f"正在从现有素材生成资产：{'、'.join(present_materials)}")
    result = generate_pack(
        adapter_id=profile.adapter_id,
        character=profile.character,
        background=profile.background if profile.background.is_file() else None,
        small_icon=(
            profile.small_icon
            if profile.small_icon.is_file()
            else None
        ),
        workspace_root=profile.workspace_root,
        output_zip=profile.output_zip,
        pack_id=profile.pack_id,
        name=profile.name,
        version=profile.version,
        input_metadata=metadata,
        supplemental_inputs=(
            {"small_icon_source": profile.small_icon_source}
            if profile.small_icon_source is not None
            and profile.small_icon_source.is_file()
            else None
        ),
        badge_template_root=profile.badge_template_root,
        character_canvas_offset=(
            profile.character_offset_x,
            profile.character_offset_y,
        ),
        character_scale=profile.character_scale,
        background_offset=(
            profile.background_offset_x,
            profile.background_offset_y,
        ),
        background_scale=profile.background_scale,
        output_offsets=profile.output_offsets,
        allow_partial=True,
    )
    generated_count = len(result.get("outputs") or {})
    skipped_count = len(result.get("skipped_outputs") or {})
    summary = f"已生成 {generated_count} 项"
    if skipped_count:
        summary += f"，因素材留空暂不替换 {skipped_count} 项"
    progress("package", f"素材包已生成（{summary}）：{result['zip']}")
    return result


def _remember_manager_workspace(workspace: StudioWorkspace, game_dir: Path | None) -> None:
    settings = WORKSPACES_ROOT.parent / "studio-settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    temporary = settings.with_suffix(settings.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            {
                "workspace": str(workspace.directory),
                "game_dir": str(game_dir) if game_dir is not None else None,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary.replace(settings)


def import_into_manager(
    profile: GeneratorProfile,
    archive: Path | None = None,
    *,
    progress: ProgressCallback = _noop_progress,
) -> tuple[StudioWorkspace, dict]:
    archive = (archive or profile.output_zip).resolve()
    if not archive.is_file():
        raise FileNotFoundError(f"Generated asset pack does not exist: {archive}")
    capability = require_installed_manager_adapter(profile.adapter_id)
    progress(
        "import",
        "已确认 Skin Manager "
        f"{capability['manager_version']} 支持 {profile.adapter_id}。",
    )
    progress("import", "正在将素材包导入 Skin Manager 工作区")
    # Validate in an isolated disposable workspace before touching an existing
    # Manager authoring workspace with the same pack id.
    with tempfile.TemporaryDirectory() as temporary:
        validation_workspace = StudioWorkspace.create(
            profile.pack_id,
            root=Path(temporary),
            name=profile.name,
            version=profile.version,
        )
        validation_workspace.import_zip(archive)
    workspace = StudioWorkspace.create(
        profile.pack_id,
        name=profile.name,
        version=profile.version,
    )
    summary = workspace.import_zip(archive)
    _remember_manager_workspace(workspace, profile.game_dir)
    result = {
        "kind": summary.kind,
        "visual_slots": summary.visual_slots,
        "audio_routes": summary.audio_routes,
        "animation_files": summary.animation_files,
        "ignored": summary.ignored,
    }
    progress("import", f"Skin Manager 已导入 {len(summary.visual_slots)} 个视觉槽位")
    return workspace, result


def deploy_from_manager(
    workspace: StudioWorkspace,
    *,
    game_dir: Path | None,
    progress: ProgressCallback = _noop_progress,
) -> tuple[dict, dict]:
    progress("deploy", "正在通过 Skin Manager 的可逆部署接口安装资产包")
    record = workspace.deploy(game_dir)
    progress("doctor", "正在运行部署后 doctor")
    doctor = installation_diagnostics()
    if not doctor.get("healthy"):
        raise RuntimeError(
            "Deployment completed but Skin Manager doctor is unhealthy: "
            + json.dumps(doctor, ensure_ascii=False)
        )
    progress("complete", "全流程完成，Skin Manager doctor 状态正常")
    return record, doctor


def run_pipeline(
    profile: GeneratorProfile,
    stages: Iterable[str] = ("generate", "import", "deploy"),
    *,
    clean: bool = True,
    progress: ProgressCallback = _noop_progress,
) -> PipelineResult:
    requested = tuple(stages)
    invalid = set(requested) - {"generate", "import", "deploy", "doctor"}
    if invalid:
        raise ValueError("Unknown pipeline stages: " + ", ".join(sorted(invalid)))
    generated: dict | None = None
    workspace: StudioWorkspace | None = None
    imported: dict | None = None
    deployed: dict | None = None
    doctor: dict | None = None
    if "generate" in requested:
        generated = generate_assets(profile, clean=clean, progress=progress)
    if "import" in requested or "deploy" in requested:
        workspace, imported = import_into_manager(profile, progress=progress)
    if "deploy" in requested:
        assert workspace is not None
        deployed, doctor = deploy_from_manager(
            workspace, game_dir=profile.game_dir, progress=progress
        )
    elif "doctor" in requested:
        doctor = installation_diagnostics()
        progress(
            "doctor",
            "Skin Manager doctor 正常" if doctor.get("healthy") else "Skin Manager doctor 异常",
        )
    return PipelineResult(
        profile=str(profile.profile_path),
        generated_workspace=(
            str(generated["workspace"]) if generated is not None else None
        ),
        output_zip=(str(generated["zip"]) if generated is not None else str(profile.output_zip)),
        zip_sha256=(generated.get("zip_sha256") if generated is not None else None),
        generated_visual_slots=(
            sorted(generated.get("outputs") or {}) if generated is not None else None
        ),
        skipped_visual_slots=(
            sorted(generated.get("skipped_outputs") or {})
            if generated is not None
            else None
        ),
        manager_workspace=(str(workspace.directory) if workspace is not None else None),
        imported_kind=(imported.get("kind") if imported is not None else None),
        deployed_game=(
            str((deployed.get("game") or {}).get("game_dir") or profile.game_dir)
            if deployed is not None
            else None
        ),
        doctor_healthy=(bool(doctor.get("healthy")) if doctor is not None else None),
        doctor=doctor,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, type=Path)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--generate", action="store_true")
    group.add_argument("--import-pack", action="store_true")
    group.add_argument("--deploy", action="store_true")
    group.add_argument("--doctor", action="store_true")
    group.add_argument("--all", action="store_true")
    parser.add_argument("--no-clean", action="store_true")
    parser.add_argument("--game-dir", type=Path)
    parser.add_argument("--adapter")
    parser.add_argument("--pack-id")
    parser.add_argument("--name")
    parser.add_argument("--output-zip", type=Path)
    parser.add_argument("--workspace-root", type=Path)
    args = parser.parse_args()
    profile = GeneratorProfile.load(args.profile)
    overrides = {
        key: value
        for key, value in {
            "adapter_id": args.adapter,
            "pack_id": args.pack_id,
            "name": args.name,
            "output_zip": args.output_zip.resolve() if args.output_zip else None,
            "workspace_root": (
                args.workspace_root.resolve() if args.workspace_root else None
            ),
            "game_dir": args.game_dir.resolve() if args.game_dir else None,
        }.items()
        if value is not None
    }
    if overrides:
        profile = GeneratorProfile(**{**asdict(profile), **overrides})
        profile.validate()
    if args.generate:
        stages = ("generate",)
    elif args.import_pack:
        stages = ("import",)
    elif args.deploy:
        stages = ("import", "deploy")
    elif args.doctor:
        stages = ("doctor",)
    else:
        stages = ("generate", "import", "deploy")

    def report(stage: str, message: str) -> None:
        print(f"[{stage}] {message}", flush=True)

    result = run_pipeline(
        profile,
        stages,
        clean=not args.no_clean,
        progress=report,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
