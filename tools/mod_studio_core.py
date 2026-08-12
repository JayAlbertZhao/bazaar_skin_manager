#!/usr/bin/env python3
"""Pack authoring backend for The Bazaar Skin Manager.

The studio state is intentionally separate from deployed packs. A workspace can
contain any subset of visual and audio slots; omitted slots fall back to the
original game asset at runtime.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import wave
import zipfile
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PIL import Image

from adapter_registry import AdapterRecord, AdapterRegistry, enrich_catalog
from unity_bundle_texture_patch import export_texture_bundle

from bazaar_skin_manager import (
    DEFAULT_RUNTIME,
    atomic_copy_tree,
    detect_installs,
    explicit_install,
    existing_install_record,
    install,
    install_many,
    installation_diagnostics,
    launch_game,
    manager_root,
    mods_root,
    preferred_game_install,
    sha256_file,
    uninstall,
    validate_pack,
)


PROJECT_ROOT = Path(
    getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])
)
CATALOG_PATH = PROJECT_ROOT / "manager" / "hero-catalog.json"
ADAPTERS_PATH = PROJECT_ROOT / "manager" / "adapters"
AUDIO_ROUTE_CATALOG_PATH = PROJECT_ROOT / "manager" / "audio-route-catalog.json"
WORKSPACES_ROOT = manager_root() / "workspaces"
STATE_FILE = "studio.json"
SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
SUPPORTED_AUDIO_EXTENSIONS = {
    ".wav",
    ".mp3",
    ".flac",
    ".ogg",
    ".m4a",
    ".aac",
    ".opus",
}
ANIMATION_EXTENSIONS = {".skel", ".json", ".atlas", ".png", ".bundle", ".assetbundle"}
PREVIEW_SIZE = (160, 120)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _slug(value: str) -> str:
    result = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip(".-")
    return result or "asset"


def compose_image_preview(
    source: Image.Image,
    size: tuple[int, int] = PREVIEW_SIZE,
    checker_size: int = 10,
) -> Image.Image:
    """Fit an image inside a fixed checkerboard preview without cropping it."""
    width, height = size
    if width <= 0 or height <= 0:
        raise ValueError("preview dimensions must be positive")
    if checker_size <= 0:
        raise ValueError("checker size must be positive")

    preview = Image.new("RGBA", size, (35, 44, 57, 255))
    alternate = (45, 56, 72, 255)
    for y in range(0, height, checker_size):
        for x in range(0, width, checker_size):
            if (x // checker_size + y // checker_size) % 2:
                preview.paste(
                    alternate,
                    (
                        x,
                        y,
                        min(x + checker_size, width),
                        min(y + checker_size, height),
                    ),
                )

    fitted = source.convert("RGBA")
    fitted.thumbnail(size, Image.Resampling.LANCZOS)
    offset = (
        (width - fitted.width) // 2,
        (height - fitted.height) // 2,
    )
    preview.alpha_composite(fitted, offset)
    return preview


def _safe_pack_id(value: str) -> str:
    value = value.strip().casefold()
    value = re.sub(r"[^a-z0-9._-]+", "-", value).strip(".-")
    if not value:
        raise ValueError("Pack id is required.")
    if len(value) > 96:
        raise ValueError("Pack id must be at most 96 characters.")
    return value


def materialized_pack_id(source_pack_id: str, target: dict) -> str:
    """Return a stable runtime id while preserving the target suffix.

    Source package ids may already use the full 96-character allowance.  The
    target identity must not be truncated away, otherwise assigning one source
    package to two professions can produce colliding runtime package ids.
    """
    source = _safe_pack_id(source_pack_id)
    adapter = _adapter_for_target(target)
    suffix = f".for.{_slug(f'{adapter.hero}-{adapter.skin}').casefold()}"
    source_budget = 96 - len(suffix)
    if source_budget < 1:
        raise ValueError("Deployment target identity is too long for a pack id.")
    trimmed = source[:source_budget].rstrip(".-_") or "skin"
    return _safe_pack_id(trimmed + suffix)


def _safe_extract(archive: Path, destination: Path) -> None:
    """Extract a ZIP without traversal, absolute paths, or oversized payloads."""
    total = 0
    destination = destination.resolve()
    with zipfile.ZipFile(archive) as source:
        for item in source.infolist():
            total += item.file_size
            if total > 2 * 1024 * 1024 * 1024:
                raise ValueError("Archive expands beyond the 2 GiB safety limit.")
            normalized = item.filename.replace("\\", "/")
            if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
                raise ValueError(f"Archive contains an absolute path: {item.filename}")
            target = (destination / normalized).resolve()
            try:
                target.relative_to(destination)
            except ValueError as error:
                raise ValueError(
                    f"Archive contains a path traversal: {item.filename}"
                ) from error
        source.extractall(destination)


def _single_pack_root(extracted: Path) -> Path | None:
    manifests = sorted(extracted.rglob("mod.json"))
    if len(manifests) != 1:
        return None
    return manifests[0].parent


def adapter_registry() -> AdapterRegistry:
    return AdapterRegistry.load(ADAPTERS_PATH)


def _adapter_for_target(target: dict) -> AdapterRecord:
    adapter = adapter_registry().find(
        str(target.get("hero") or ""),
        str(target.get("skin") or ""),
    )
    if adapter is None:
        raise ValueError(
            "No verified adapter for "
            f"{target.get('hero')} / {target.get('skin')}."
        )
    return adapter


def _visual_template_map(target: dict) -> dict[str, dict]:
    manifest = _adapter_for_target(target).payload
    return {
        replacement["slot"]: replacement
        for replacement in manifest["visual_replacements"]
    }


def _shared_native_asset_key(deployment: dict | None) -> tuple[str, str] | None:
    if not deployment or deployment.get("mode") != "preload_unity_texture2d":
        return None
    target = str(deployment.get("target") or "").replace("\\", "/").casefold()
    asset_name = str(deployment.get("asset_name") or "").casefold()
    return (target, asset_name) if target and asset_name else None


def _coalesce_shared_native_images(replacements: list[dict]) -> list[dict]:
    """Use one bitmap for logical slots backed by one physical Texture2D.

    Several Bazaar UI concepts are aliases of the same Texture2D (for example
    store/marketplace images and collection/daily previews).  Per-slot editing
    may leave those logical slots with different files, but the game has only
    one physical destination.  The adapter order declares the canonical slot;
    every later alias is materialized with that same file so validation and
    deployment describe the hardware truth instead of producing a conflict.
    """

    canonical_files: dict[tuple[str, str], str] = {}
    result: list[dict] = []
    for source in replacements:
        replacement = deepcopy(source)
        deployments = []
        if replacement.get("deployment"):
            deployments.append(replacement["deployment"])
        deployments.extend(replacement.get("additional_deployments") or [])
        keys = [
            key
            for deployment in deployments
            if (key := _shared_native_asset_key(deployment)) is not None
        ]
        inherited = next(
            (canonical_files[key] for key in keys if key in canonical_files),
            None,
        )
        selected_file = inherited or str(replacement.get("file") or "")
        if selected_file:
            replacement["file"] = selected_file
            for key in keys:
                canonical_files.setdefault(key, selected_file)
        result.append(replacement)
    return result


def _verified_original_bundle(
    game_dir: Path,
    deployment: dict,
) -> Path:
    """Return the native bundle, preferring the manager's verified backup.

    The live Addressables bundle may already contain a deployed skin.  Showing
    that file as an "original" reference is actively misleading, so only a
    recorded native backup or a bundle whose hash is accepted by the adapter
    is eligible here.
    """

    relative = str(deployment.get("target") or "").replace("/", os.sep)
    if not relative:
        raise ValueError("Original visual deployment has no bundle target.")
    live = (game_dir / relative).resolve()
    record = existing_install_record() or {}
    for patch in record.get("native_patches") or []:
        try:
            recorded_target = Path(str(patch.get("target") or "")).resolve()
        except (OSError, ValueError):
            continue
        if recorded_target != live:
            continue
        backup = Path(str(patch.get("backup") or ""))
        original_sha256 = str(patch.get("original_sha256") or "").casefold()
        if (
            backup.is_file()
            and original_sha256
            and sha256_file(backup).casefold() == original_sha256
        ):
            return backup

    if not live.is_file():
        raise FileNotFoundError(live)
    supported = {
        str(value).casefold()
        for value in deployment.get("supported_original_sha256") or []
    }
    if supported and sha256_file(live).casefold() not in supported:
        raise ValueError(
            "The live game bundle is modified and its verified native backup "
            "is unavailable; original reference was not guessed."
        )
    return live


def _original_visual_deployment(adapter: AdapterRecord, slot: str) -> dict:
    templates = {
        str(item.get("slot") or ""): item
        for item in adapter.payload.get("visual_replacements") or []
    }
    template = templates.get(slot) or {}
    deployment = deepcopy(template.get("deployment") or {})
    if deployment:
        return deployment

    # Runtime-only portrait slots still point at a real Texture2D in the
    # hero's skin bundle.  Reuse that bundle contract for read-only preview.
    match_names = [str(value) for value in template.get("match_names") or []]
    if not match_names or not slot.startswith("portrait_"):
        raise ValueError(
            f"Slot {slot} has no static original image; it is supplied by the "
            "game as a layered or animated asset."
        )
    skin_deployment = next(
        (
            deepcopy(item.get("deployment") or {})
            for item in templates.values()
            if Path(str((item.get("deployment") or {}).get("target") or "")).name.startswith("skin_")
            and str((item.get("deployment") or {}).get("target") or "").endswith("_assets_all.bundle")
        ),
        None,
    )
    if not skin_deployment:
        raise ValueError(f"Slot {slot} has no verified original bundle.")
    skin_deployment["asset_name"] = match_names[0]
    skin_deployment.pop("target_size", None)
    return skin_deployment


def export_original_visual_reference(
    target: dict,
    slot: str,
    *,
    game_dir: Path | None = None,
    output: Path | None = None,
) -> Path:
    """Export and cache one verified native slot image for authoring reference."""

    adapter = _adapter_for_target(target)
    game = preferred_game_install(game_dir)
    if not adapter.supports_build(game.build_id):
        raise ValueError(
            f"Adapter {adapter.adapter_id} is not verified for Steam build "
            f"{game.build_id or 'unknown'}."
        )
    deployment = _original_visual_deployment(adapter, slot)
    source = _verified_original_bundle(game.game_dir, deployment)
    destination = output or (
        manager_root()
        / "original-slot-previews"
        / adapter.adapter_id
        / (game.build_id or "unknown-build")
        / f"{slot}.png"
    )
    if destination.is_file() and destination.stat().st_mtime_ns >= source.stat().st_mtime_ns:
        return destination
    expected = deployment.get("target_size")
    export_texture_bundle(
        source,
        destination,
        asset_name=str(deployment["asset_name"]),
        unity_version=str(deployment["unity_version"]),
        target_size=(tuple(int(value) for value in expected) if expected else None),
    )
    return destination


def _all_visual_slot_ids() -> set[str]:
    return {
        str(replacement["slot"])
        for adapter in adapter_registry().records
        for replacement in adapter.payload.get("visual_replacements") or []
        if replacement.get("slot")
    }


def _audio_template(target: dict) -> dict:
    adapter = _adapter_for_target(target)
    template = adapter.payload.get("audio_template")
    if template:
        return deepcopy(template)
    reference = str(adapter.payload.get("audio_template_ref") or "")
    if not reference:
        raise ValueError(f"Adapter {adapter.adapter_id} has no audio routes.")
    catalog_payload = _read_json(AUDIO_ROUTE_CATALOG_PATH)
    if reference != catalog_payload.get("id"):
        raise ValueError(
            f"Adapter {adapter.adapter_id} requests unknown audio template "
            f"{reference}."
        )
    hero_id = str(target.get("hero") or adapter.hero)
    hero = next(
        (
            item
            for item in catalog_payload.get("heroes") or []
            if str(item.get("hero") or "").casefold() == hero_id.casefold()
        ),
        None,
    )
    if hero is None:
        raise ValueError(f"Audio template {reference} has no hero {hero_id}.")
    routes = deepcopy(hero.get("routes") or [])
    routes.extend(deepcopy(catalog_payload.get("menu_routes") or []))
    return {
        "schema_version": 1,
        "enabled": True,
        "target": {
            "game": "The Bazaar",
            "steam_build": str(catalog_payload["source"]["steam_build"]),
            "supported_builds": deepcopy(
                catalog_payload.get("supported_builds") or []
            ),
            "hero": hero_id,
        },
        "fallback": "original",
        "audio_format": deepcopy(catalog_payload["audio_format"]),
        "playback": deepcopy(catalog_payload["playback"]),
        "routes": routes,
    }


def _retarget_audio_manifest_payload(manifest: dict, target: dict) -> dict:
    """Bind portable logical voice slots to one hero's exact FMOD routes."""

    template = _audio_template(target)
    source_routes = {
        str(route.get("logical_slot") or ""): route
        for route in manifest.get("routes") or []
        if route.get("logical_slot") and route.get("variants")
    }
    routes = []
    for exact in template.get("routes") or []:
        source = source_routes.get(str(exact.get("logical_slot") or ""))
        if source is None:
            continue
        route = {
            key: deepcopy(exact[key])
            for key in (
                "logical_slot",
                "category",
                "event_guid",
                "event_path",
                "selectors",
            )
        }
        route["variants"] = deepcopy(source.get("variants") or [])
        routes.append(route)
    result = deepcopy(manifest)
    previous_target = deepcopy(manifest.get("target") or {})
    result["target"] = {
        "game": "The Bazaar",
        "steam_build": str(template["target"]["steam_build"]),
        "hero": str(target["hero"]),
    }
    result["fallback"] = "original"
    result["audio_format"] = deepcopy(template["audio_format"])
    result["playback"] = deepcopy(template.get("playback") or {"gain": 0.8})
    result["routes"] = routes
    if previous_target and previous_target != result["target"]:
        result["source_audio_target"] = previous_target
    return result


def catalog() -> dict:
    return _read_json(CATALOG_PATH)


def discovered_catalog(game_dir: Path | None = None) -> dict:
    base = catalog()
    try:
        game = preferred_game_install(game_dir)
    except RuntimeError:
        return enrich_catalog(
            base,
            adapter_registry(),
            game_dir=None,
            build_id=None,
        )
    return enrich_catalog(
        base,
        adapter_registry(),
        game_dir=game.game_dir,
        build_id=game.build_id,
    )


def default_project(
    pack_id: str = "local.custom.skin",
    name: str = "Custom Skin",
    version: str = "0.1.0",
    hero: str | None = None,
    skin: str | None = None,
    skin_name_contains: str | None = None,
) -> dict:
    default_adapter = adapter_registry().default()
    hero = hero or default_adapter.hero
    skin = skin or default_adapter.skin
    skin_name_contains = skin_name_contains or default_adapter.skin_name_contains
    return {
        "schema_version": 1,
        "pack": {
            "id": _safe_pack_id(pack_id),
            "name": name.strip() or "Custom Skin",
            "version": version.strip() or "0.1.0",
        },
        "target": {
            "game": "the-bazaar",
            "hero": hero,
            "skin": skin,
            "skin_name_contains": skin_name_contains,
        },
        "visual_slots": {},
        "audio_manifest": None,
        "animation": {
            "mode": "none",
            "files": [],
            "runtime_ready": False,
        },
    }


@dataclass
class ImportSummary:
    kind: str
    visual_slots: list[str]
    audio_routes: list[str]
    animation_files: list[str]
    ignored: list[str]


class StudioWorkspace:
    def __init__(self, directory: Path, state: dict):
        self.directory = directory.resolve()
        self.state = state

    @classmethod
    def create(
        cls,
        pack_id: str = "local.custom.skin",
        *,
        root: Path | None = None,
        name: str = "Custom Skin",
        version: str = "0.1.0",
        hero: str | None = None,
        skin: str | None = None,
        skin_name_contains: str | None = None,
    ) -> "StudioWorkspace":
        state = default_project(
            pack_id,
            name,
            version,
            hero,
            skin,
            skin_name_contains,
        )
        directory = (root or WORKSPACES_ROOT) / state["pack"]["id"]
        directory.mkdir(parents=True, exist_ok=True)
        workspace = cls(directory, state)
        workspace.save()
        return workspace

    @classmethod
    def load(cls, directory: Path) -> "StudioWorkspace":
        directory = directory.resolve()
        state_path = directory / STATE_FILE
        if state_path.is_file():
            return cls(directory, _read_json(state_path))
        manifest_path = directory / "mod.json"
        if not manifest_path.is_file():
            raise ValueError(f"Workspace has no {STATE_FILE} or mod.json: {directory}")
        return cls(directory, cls._state_from_pack(directory))

    @staticmethod
    def _state_from_pack(pack_root: Path) -> dict:
        manifest = _read_json(pack_root / "mod.json")
        target = manifest.get("target") or {}
        default_adapter = adapter_registry().default()
        state = default_project(
            manifest.get("id", "imported.skin"),
            manifest.get("name", "Imported Skin"),
            manifest.get("version", "0.1.0"),
            target.get("hero", default_adapter.hero),
            target.get("skin", default_adapter.skin),
            target.get("skin_name_contains", default_adapter.skin_name_contains),
        )
        state["visual_slots"] = {
            entry["slot"]: entry["file"]
            for entry in manifest.get("visual_replacements") or []
            if entry.get("slot") and entry.get("file")
        }
        state["audio_manifest"] = manifest.get("audio_manifest")
        state["animation"] = deepcopy(
            manifest.get("animation")
            or {"mode": "none", "files": [], "runtime_ready": False}
        )
        if manifest.get("authoring") is not None:
            # Complete generated packs carry deterministic input/output
            # provenance here. Preserve it across Manager import and rebuild;
            # otherwise deploy would silently replace the imported manifest
            # with a provenance-stripped variant.
            state["authoring"] = deepcopy(manifest["authoring"])
        if manifest.get("source_pack") is not None:
            state["source_pack"] = deepcopy(manifest["source_pack"])
        return state

    @property
    def state_path(self) -> Path:
        return self.directory / STATE_FILE

    def save(self) -> None:
        _write_json(self.state_path, self.state)

    def visual_path(self, slot: str) -> Path | None:
        relative = (self.state.get("visual_slots") or {}).get(slot)
        if not relative:
            return None
        path = (self.directory / relative).resolve()
        try:
            path.relative_to(self.directory)
        except ValueError:
            return None
        return path if path.is_file() else None

    def set_metadata(
        self,
        *,
        pack_id: str,
        name: str,
        version: str,
        hero: str,
        skin: str,
        skin_name_contains: str,
    ) -> None:
        self.set_pack_metadata(
            pack_id=pack_id,
            name=name,
            version=version,
        )
        self.state["target"] = {
            "game": "the-bazaar",
            "hero": hero,
            "skin": skin,
            "skin_name_contains": skin_name_contains,
        }
        self.save()

    def set_pack_metadata(
        self,
        *,
        pack_id: str,
        name: str,
        version: str,
    ) -> None:
        """Edit reusable package identity without changing deployment target."""
        new_id = _safe_pack_id(pack_id)
        self.state["pack"] = {
            "id": new_id,
            "name": name.strip() or "Custom Skin",
            "version": version.strip() or "0.1.0",
        }
        self.save()

    def import_visual(
        self,
        slot: str,
        source: Path,
        *,
        chroma_color: str | None = None,
        tolerance: int = 28,
    ) -> Path:
        if slot not in _all_visual_slot_ids():
            raise ValueError(f"Unknown visual slot: {slot}")
        source = source.resolve()
        if source.suffix.casefold() not in SUPPORTED_IMAGE_EXTENSIONS:
            raise ValueError(f"Unsupported image type: {source.suffix}")
        with Image.open(source) as loaded:
            image = loaded.convert("RGBA")
        if chroma_color:
            image = remove_color_screen(image, chroma_color, tolerance)
        destination = self.directory / "assets" / f"{slot}.png"
        destination.parent.mkdir(parents=True, exist_ok=True)
        image.save(destination, "PNG", optimize=True)
        self.state.setdefault("visual_slots", {})[slot] = (
            destination.relative_to(self.directory).as_posix()
        )
        self.save()
        return destination

    def import_pil_image(
        self,
        slot: str,
        image: Image.Image,
        *,
        chroma_color: str | None = None,
        tolerance: int = 28,
    ) -> Path:
        if slot not in _all_visual_slot_ids():
            raise ValueError(f"Unknown visual slot: {slot}")
        output = image.convert("RGBA")
        if chroma_color:
            output = remove_color_screen(output, chroma_color, tolerance)
        destination = self.directory / "assets" / f"{slot}.png"
        destination.parent.mkdir(parents=True, exist_ok=True)
        output.save(destination, "PNG", optimize=True)
        self.state.setdefault("visual_slots", {})[slot] = (
            destination.relative_to(self.directory).as_posix()
        )
        self.save()
        return destination

    def replace_visual_images(self, images: dict[str, Image.Image]) -> dict[str, Path]:
        """Replace the complete visual set and persist workspace state once.

        Per-slot authoring used to call ``clear_visual`` and
        ``import_pil_image`` for every image.  Besides repeatedly rewriting
        studio.json, that made a full draft materialization unnecessarily
        expensive.  This bulk path keeps the same PNG contract while making
        the state update atomic from the caller's point of view.
        """

        unknown = sorted(set(images) - set(_all_visual_slot_ids()))
        if unknown:
            raise ValueError(f"Unknown visual slots: {', '.join(unknown)}")

        previous = dict(self.state.get("visual_slots") or {})
        next_visuals: dict[str, str] = {}
        written: dict[str, Path] = {}
        assets = self.directory / "assets"
        assets.mkdir(parents=True, exist_ok=True)
        for slot, image in images.items():
            destination = assets / f"{slot}.png"
            image.convert("RGBA").save(destination, "PNG", optimize=True)
            next_visuals[slot] = destination.relative_to(self.directory).as_posix()
            written[slot] = destination

        for slot, relative in previous.items():
            if slot in next_visuals:
                continue
            path = (self.directory / relative).resolve()
            try:
                path.relative_to(self.directory)
            except ValueError:
                continue
            if path.is_file():
                path.unlink()

        self.state["visual_slots"] = next_visuals
        self.save()
        return written

    def export_original_visual(
        self,
        slot: str,
        game_dir: Path | None = None,
    ) -> Path:
        """Export a verified original Texture2D for side-by-side preview."""
        output = self.directory / "authoring" / "original-previews" / f"{slot}.png"
        return export_original_visual_reference(
            self.state["target"],
            slot,
            game_dir=game_dir,
            output=output,
        )

    def clear_visual(self, slot: str) -> None:
        path = self.visual_path(slot)
        if path:
            path.unlink()
        self.state.setdefault("visual_slots", {}).pop(slot, None)
        self.save()

    def audio_manifest_path(self) -> Path:
        relative = self.state.get("audio_manifest")
        return (
            self.directory / relative
            if relative
            else self.directory / "audio-manifest.json"
        )

    def audio_manifest(self, create: bool = False) -> dict | None:
        path = self.audio_manifest_path()
        if path.is_file():
            return _read_json(path)
        if not create:
            return None
        template = _audio_template(self.state["target"])
        target = self.state["target"]
        result = {
            "schema_version": 1,
            "enabled": True,
            "target": {
                "game": "The Bazaar",
                "steam_build": catalog()["steam_build"],
                "hero": target["hero"],
            },
            "fallback": "original",
            "audio_format": deepcopy(template["audio_format"]),
            "playback": deepcopy(template.get("playback") or {"gain": 0.8}),
            "routes": [],
        }
        _write_json(path, result)
        self.state["audio_manifest"] = path.relative_to(self.directory).as_posix()
        self.save()
        return result

    def audio_route_catalog(self) -> list[dict]:
        try:
            template = _audio_template(self.state["target"])
        except ValueError:
            return []
        return deepcopy(template.get("routes") or [])

    def import_audio(
        self,
        logical_slot: str,
        source: Path,
        *,
        ffmpeg_path: Path | None = None,
    ) -> Path:
        source = source.resolve()
        if source.suffix.casefold() not in SUPPORTED_AUDIO_EXTENSIONS:
            raise ValueError(f"Unsupported audio type: {source.suffix}")
        route_template = next(
            (
                route
                for route in self.audio_route_catalog()
                if route["logical_slot"] == logical_slot
            ),
            None,
        )
        if route_template is None:
            raise ValueError(f"Unknown audio route: {logical_slot}")

        manifest = self.audio_manifest(create=True)
        assert manifest is not None
        route = next(
            (
                item
                for item in manifest["routes"]
                if item["logical_slot"] == logical_slot
            ),
            None,
        )
        if route is None:
            route = {
                key: deepcopy(route_template[key])
                for key in (
                    "logical_slot",
                    "category",
                    "event_guid",
                    "event_path",
                    "selectors",
                )
            }
            route["variants"] = []
            manifest["routes"].append(route)

        index = len(route["variants"]) + 1
        name = f"{_slug(logical_slot)}-{index:02d}.wav"
        destination = self.directory / "audio" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        transcode_audio(source, destination, ffmpeg_path=ffmpeg_path)
        route["variants"].append(
            {
                "file": destination.relative_to(self.directory).as_posix(),
                "sha256": sha256_file(destination),
                "weight": 1,
                "sample_name": destination.stem,
            }
        )
        _write_json(self.audio_manifest_path(), manifest)
        self.save()
        return destination

    def clear_audio_route(self, logical_slot: str) -> None:
        manifest = self.audio_manifest()
        if not manifest:
            return
        kept = []
        for route in manifest.get("routes") or []:
            if route.get("logical_slot") != logical_slot:
                kept.append(route)
                continue
            for variant in route.get("variants") or []:
                path = self.directory / variant.get("file", "")
                if path.is_file():
                    path.unlink()
        manifest["routes"] = kept
        _write_json(self.audio_manifest_path(), manifest)
        self.save()

    def import_animation(self, files: Iterable[Path], mode: str) -> list[Path]:
        accepted: list[Path] = []
        destination_root = self.directory / "animation"
        destination_root.mkdir(parents=True, exist_ok=True)
        for source in files:
            source = source.resolve()
            if source.suffix.casefold() not in ANIMATION_EXTENSIONS:
                continue
            destination = destination_root / source.name
            shutil.copy2(source, destination)
            accepted.append(destination)
        if not accepted:
            raise ValueError("No supported animation files were selected.")
        self.state["animation"] = {
            "mode": mode,
            "files": [
                path.relative_to(self.directory).as_posix() for path in accepted
            ],
            # The current public runtime uses static overlays. Keep authoring
            # sources in the pack without falsely claiming runtime playback.
            "runtime_ready": False,
        }
        self.save()
        return accepted

    def clear_animation(self) -> None:
        root = self.directory / "animation"
        if root.is_dir():
            shutil.rmtree(root)
        self.state["animation"] = {
            "mode": "none",
            "files": [],
            "runtime_ready": False,
        }
        self.save()

    def clear_loaded_assets(self) -> dict[str, int]:
        """Remove every imported authoring asset while preserving pack metadata."""
        visual_count = len(self.state.get("visual_slots") or {})
        audio = self.audio_manifest() or {}
        audio_count = sum(
            1 for route in audio.get("routes") or [] if route.get("variants")
        )
        animation_count = len(
            (self.state.get("animation") or {}).get("files") or []
        )

        # A complete pack may carry auxiliary authoring manifests with arbitrary
        # names. The workspace is dedicated storage, so keep only studio.json
        # instead of maintaining an incomplete list of generated payload files.
        for path in list(self.directory.iterdir()):
            if path.name == STATE_FILE:
                continue
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()

        self.state["visual_slots"] = {}
        self.state["audio_manifest"] = None
        self.state["animation"] = {
            "mode": "none",
            "files": [],
            "runtime_ready": False,
        }
        self.save()
        return {
            "visual_slots": visual_count,
            "audio_routes": audio_count,
            "animation_files": animation_count,
        }

    def import_zip(self, archive: Path) -> ImportSummary:
        archive = archive.resolve()
        with tempfile.TemporaryDirectory() as temp:
            extracted = Path(temp)
            _safe_extract(archive, extracted)
            pack_root = _single_pack_root(extracted)
            if pack_root is not None:
                errors = validate_pack(pack_root)
                if errors:
                    raise ValueError("Invalid complete pack: " + "; ".join(errors))
                preserved_state = self.state_path
                if preserved_state.is_file():
                    preserved_state.unlink()
                for child in list(self.directory.iterdir()):
                    if child.is_dir():
                        shutil.rmtree(child)
                    else:
                        child.unlink()
                for child in pack_root.iterdir():
                    destination = self.directory / child.name
                    if child.is_dir():
                        shutil.copytree(child, destination)
                    else:
                        shutil.copy2(child, destination)
                self.state = self._state_from_pack(self.directory)
                self.save()
                return ImportSummary(
                    "complete_pack",
                    sorted(self.state["visual_slots"]),
                    sorted(
                        route["logical_slot"]
                        for route in (
                            self.audio_manifest() or {}
                        ).get("routes", [])
                    ),
                    list((self.state.get("animation") or {}).get("files") or []),
                    [],
                )

            return self._import_loose_tree(extracted)

    def _import_loose_tree(self, root: Path) -> ImportSummary:
        visual_slots: list[str] = []
        audio_routes: list[str] = []
        animation_files: list[str] = []
        ignored: list[str] = []
        voice_manifests = sorted(root.rglob("*-voice-assets.json"))
        imported_voice_files: set[Path] = set()
        if len(voice_manifests) == 1:
            audio_routes, imported_voice_files = self._import_voice_source_manifest(
                voice_manifests[0]
            )
        elif len(voice_manifests) > 1:
            raise ValueError("Audio package contains multiple mak-voice-assets.json files.")
        # Loose reusable packs are content collections, not target bindings.
        # Accept every known logical slot here; deployment later intersects the
        # collection with the selected target adapter.
        slot_ids = _all_visual_slot_ids()
        route_catalog = self.audio_route_catalog()
        route_by_slug = {
            _slug(route["logical_slot"]).casefold(): route["logical_slot"]
            for route in route_catalog
        }
        animation_sources: list[Path] = []
        for source in sorted(root.rglob("*")):
            if not source.is_file():
                continue
            if source.resolve() in imported_voice_files or source in voice_manifests:
                continue
            stem = source.stem.casefold()
            suffix = source.suffix.casefold()
            if suffix in SUPPORTED_IMAGE_EXTENSIONS and stem in slot_ids:
                self.import_visual(stem, source)
                visual_slots.append(stem)
            elif suffix in SUPPORTED_AUDIO_EXTENSIONS:
                matched = next(
                    (
                        logical_slot
                        for slug, logical_slot in route_by_slug.items()
                        if stem == slug or stem.startswith(slug + "-")
                    ),
                    None,
                )
                if matched:
                    self.import_audio(matched, source)
                    audio_routes.append(matched)
                else:
                    ignored.append(source.relative_to(root).as_posix())
            elif suffix in ANIMATION_EXTENSIONS and "animation" in {
                part.casefold() for part in source.parts
            }:
                animation_sources.append(source)
            else:
                ignored.append(source.relative_to(root).as_posix())
        if animation_sources:
            accepted = self.import_animation(animation_sources, "spine_source")
            animation_files = [
                path.relative_to(self.directory).as_posix() for path in accepted
            ]
        return ImportSummary(
            "loose_assets",
            sorted(set(visual_slots)),
            sorted(set(audio_routes)),
            animation_files,
            ignored,
        )

    def _import_voice_source_manifest(
        self,
        manifest_path: Path,
    ) -> tuple[list[str], set[Path]]:
        """Convert a compatible voice-production package into runtime UGC."""
        source_manifest = _read_json(manifest_path)
        schema = str(source_manifest.get("schema_version", ""))
        if not re.fullmatch(
            r"[a-z0-9._-]+-voice-assets/v[0-9]+",
            schema.casefold(),
        ):
            raise ValueError(f"Unsupported voice source schema: {schema}")
        target = source_manifest.get("target") or {}
        source_hero = str(target.get("hero") or "").strip()
        if not source_hero:
            raise ValueError("Voice package target.hero is missing.")
        assets = source_manifest.get("assets") or []
        route_rows: dict[tuple[str, str, str, str, str], list[dict]] = {}
        copied_sources: set[Path] = set()

        def route_names(asset: dict) -> list[tuple[str, list[dict[str, str]]]]:
            pool = asset["logical_pool"]
            if pool == "PvP_Intro.Left+Right":
                return [
                    (
                        "PvP_Intro.Left",
                        [
                            {
                                "parameter": "VO_Hero_PvPIntro_Pan",
                                "label": "Left",
                            }
                        ],
                    ),
                    (
                        "PvP_Intro.Right",
                        [
                            {
                                "parameter": "VO_Hero_PvPIntro_Pan",
                                "label": "Right",
                            }
                        ],
                    ),
                ]
            if pool == "Upgrade.custom":
                return [("Upgrade.default", [])]
            event_path = asset.get("event_path", "")
            if event_path.startswith("event:/VO/Merchant/"):
                return [(f"Merchant.{pool}", [])]
            if event_path.startswith("event:/VO/Hero/Menu/"):
                return [(f"Menu.{pool}", [])]
            selectors: list[dict[str, str]] = []
            selector = asset.get("selector")
            if isinstance(selector, str) and "=" in selector:
                parameter, label = selector.split("=", 1)
                selectors = [{"parameter": parameter, "label": label}]
            return [(pool, selectors)]

        def category_for(event_path: str) -> str:
            if event_path.startswith("event:/VO/Hero/Menu/"):
                return "menu_voice"
            if event_path.startswith("event:/VO/Hero/"):
                return "hero_voice"
            if event_path.startswith("event:/VO/Merchant/"):
                return "merchant_voice"
            raise ValueError(f"Unsupported event path: {event_path}")

        for row in assets:
            relative = row.get("audio_file")
            if row.get("asset_action") == "fallback_original" or not relative:
                continue
            source = (manifest_path.parent / relative).resolve()
            try:
                source.relative_to(manifest_path.parent.resolve())
            except ValueError as error:
                raise ValueError(
                    f"Voice asset escapes its package: {relative}"
                ) from error
            if not source.is_file():
                raise ValueError(f"Voice package is missing {relative}")
            expected = row.get("audio_sha256")
            if expected and sha256_file(source) != expected:
                raise ValueError(f"Voice asset hash mismatch: {relative}")
            destination = self.directory / "audio" / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            transcode_audio(source, destination)
            copied_sources.add(source)
            for logical_slot, selectors in route_names(row):
                event_guid = row.get("event_guid")
                event_path = row.get("event_path")
                if row.get("logical_pool") == "Upgrade.custom":
                    event_guid = "9b7f636a-9b4d-44a6-acf6-a41dd19bd650"
                    event_path = "event:/VO/Hero/Mak/VO_Mak_Upgrade"
                if not event_guid or not event_path:
                    raise ValueError(
                        f"Voice route lacks event identity: {logical_slot}"
                    )
                selectors_json = json.dumps(
                    selectors,
                    ensure_ascii=True,
                    separators=(",", ":"),
                )
                key = (
                    logical_slot,
                    category_for(event_path),
                    event_guid,
                    event_path,
                    selectors_json,
                )
                route_rows.setdefault(key, []).append(
                    {
                        "destination": destination,
                        "sample_name": row.get("sample_name") or destination.stem,
                        "row_number": int(row.get("row_number") or 0),
                    }
                )

        routes = []
        for key in sorted(route_rows, key=lambda value: value[0].casefold()):
            logical_slot, category, event_guid, event_path, selectors_json = key
            variants = []
            seen: set[str] = set()
            for row in sorted(
                route_rows[key],
                key=lambda value: value["row_number"],
            ):
                destination = row["destination"]
                relative = destination.relative_to(self.directory).as_posix()
                if relative.casefold() in seen:
                    continue
                seen.add(relative.casefold())
                variants.append(
                    {
                        "file": relative,
                        "sha256": sha256_file(destination),
                        "weight": 1,
                        "sample_name": row["sample_name"],
                    }
                )
            routes.append(
                {
                    "logical_slot": logical_slot,
                    "category": category,
                    "event_guid": event_guid,
                    "event_path": event_path,
                    "selectors": json.loads(selectors_json),
                    "variants": variants,
                }
            )
        template = _audio_template(self.state["target"])
        runtime_manifest = {
            "schema_version": 1,
            "enabled": True,
            "target": {
                "game": "The Bazaar",
                "steam_build": catalog()["steam_build"],
                "hero": target["hero"],
            },
            "fallback": "original",
            "source_package": {
                "name": manifest_path.parent.name,
                "version": source_manifest.get("version"),
                "schema": schema,
                "hero": source_hero,
            },
            "audio_format": deepcopy(template["audio_format"]),
            "playback": deepcopy(template.get("playback") or {"gain": 0.8}),
            "routes": routes,
        }
        runtime_manifest = _retarget_audio_manifest_payload(
            runtime_manifest,
            self.state["target"],
        )
        runtime_path = self.directory / "audio-manifest.json"
        _write_json(runtime_path, runtime_manifest)
        self.state["audio_manifest"] = runtime_path.relative_to(
            self.directory
        ).as_posix()
        self.save()
        return [route["logical_slot"] for route in routes], copied_sources

    def build_pack(self) -> Path:
        adapter = _adapter_for_target(self.state["target"])
        visual_slots = self.state.get("visual_slots") or {}
        replacements = []
        # Adapter order is meaningful: the first logical slot routed to a
        # physical Texture2D is its canonical authoring slot.
        for declared in adapter.payload.get("visual_replacements") or []:
            slot = str(declared.get("slot") or "")
            relative = visual_slots.get(slot)
            if not relative:
                continue
            path = self.directory / relative
            if not path.is_file():
                continue
            template = deepcopy(declared)
            template["file"] = relative
            replacements.append(template)
        replacements = _coalesce_shared_native_images(replacements)

        manifest = {
            "schema_version": 1,
            "id": self.state["pack"]["id"],
            "name": self.state["pack"]["name"],
            "version": self.state["pack"]["version"],
            "enabled": True,
            "target": deepcopy(self.state["target"]),
            "adapter": {
                "id": adapter.adapter_id,
                "version": adapter.adapter_version,
            },
            "visual_replacements": replacements,
        }
        if self.state.get("authoring"):
            manifest["authoring"] = deepcopy(self.state["authoring"])
        if self.state.get("source_pack"):
            manifest["source_pack"] = deepcopy(self.state["source_pack"])
        audio = self.audio_manifest()
        if audio and any(route.get("variants") for route in audio.get("routes", [])):
            manifest["audio_manifest"] = self.state["audio_manifest"]
        animation = self.state.get("animation") or {}
        if animation.get("mode") != "none" and animation.get("files"):
            manifest["animation"] = deepcopy(animation)
        _write_json(self.directory / "mod.json", manifest)
        self._write_asset_index()
        self.save()
        return self.directory

    def _declared_payload_paths(self, manifest: dict) -> list[Path]:
        relatives: set[str] = set()
        for replacement in manifest.get("visual_replacements") or []:
            if replacement.get("file"):
                relatives.add(str(replacement["file"]))
        # A logical slot can share one physical Texture2D with another slot.
        # The deployment manifest intentionally points every such alias at the
        # adapter's canonical image, but the portable workspace must retain the
        # user's independently authored slot images for later editing.
        for relative in (self.state.get("visual_slots") or {}).values():
            normalized = str(relative or "").replace("\\", "/").strip()
            if not normalized:
                continue
            candidate = (self.directory / normalized).resolve()
            try:
                candidate.relative_to(self.directory)
            except ValueError as error:
                raise ValueError(
                    f"Visual slot asset escapes the workspace: {relative}"
                ) from error
            if candidate.is_file():
                relatives.add(normalized)
        # Deterministic generator packs keep their editable source material in
        # the workspace. Include those files in the portable pack whenever
        # they exist; older imported packs may carry provenance records without
        # the corresponding payload, so missing legacy sources remain optional.
        authoring_inputs = ((manifest.get("authoring") or {}).get("inputs") or {})
        for record in authoring_inputs.values():
            relative = str((record or {}).get("workspace_file") or "").strip()
            if not relative:
                continue
            normalized = relative.replace("\\", "/")
            candidate = (self.directory / normalized).resolve()
            try:
                candidate.relative_to(self.directory)
            except ValueError as error:
                raise ValueError(
                    f"Pack authoring input escapes the workspace: {relative}"
                ) from error
            if candidate.is_file():
                relatives.add(normalized)
        audio_relative = manifest.get("audio_manifest")
        if audio_relative:
            relatives.add(str(audio_relative))
            audio_path = self._resolve_payload_path(str(audio_relative))
            audio = _read_json(audio_path)
            for route in audio.get("routes") or []:
                for variant in route.get("variants") or []:
                    if variant.get("file"):
                        relatives.add(str(variant["file"]))
        animation = manifest.get("animation") or {}
        relatives.update(str(item) for item in animation.get("files") or [])
        return [self._resolve_payload_path(item) for item in sorted(relatives)]

    def _resolve_payload_path(self, relative: str) -> Path:
        normalized = relative.replace("\\", "/")
        if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
            raise ValueError(f"Pack payload path must be relative: {relative}")
        path = (self.directory / normalized).resolve()
        try:
            path.relative_to(self.directory)
        except ValueError as error:
            raise ValueError(
                f"Pack payload escapes the workspace: {relative}"
            ) from error
        if not path.is_file():
            raise FileNotFoundError(path)
        return path

    def _write_asset_index(self) -> None:
        manifest = _read_json(self.directory / "mod.json")
        files = {}
        for path in self._declared_payload_paths(manifest):
            relative = path.relative_to(self.directory).as_posix()
            files[relative] = {
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
        _write_json(
            self.directory / "asset-index.json",
            {"schema_version": 1, "files": files},
        )

    def validation_errors(self) -> list[str]:
        self.build_pack()
        return validate_pack(self.directory)

    def export_zip(self, destination: Path) -> Path:
        self.build_pack()
        destination = destination.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            destination.unlink()
        with zipfile.ZipFile(
            destination,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            manifest = _read_json(self.directory / "mod.json")
            paths = self._declared_payload_paths(manifest)
            paths.extend(
                [self.directory / "mod.json", self.directory / "asset-index.json"]
            )
            for path in sorted(set(paths)):
                relative = path.relative_to(self.directory).as_posix()
                entry = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
                entry.create_system = 3
                entry.external_attr = 0o100644 << 16
                archive.writestr(
                    entry,
                    path.read_bytes(),
                    compress_type=zipfile.ZIP_DEFLATED,
                    compresslevel=9,
                )
        return destination

    def deploy(self, game_dir: Path | None = None) -> dict:
        return self.deploy_many([self], game_dir)

    @staticmethod
    def deploy_assignments(
        assignments: list[tuple["StudioWorkspace", dict]],
        game_dir: Path | None = None,
    ) -> dict:
        """Materialize reusable packs for target-specific verified adapters."""
        if not assignments:
            raise ValueError("Select at least one deployment assignment.")
        with tempfile.TemporaryDirectory() as temp:
            materialized: list[StudioWorkspace] = []
            for index, (source, target) in enumerate(assignments):
                _adapter_for_target(target)
                destination = Path(temp) / f"assignment-{index:02d}"
                shutil.copytree(source.directory, destination)
                workspace = StudioWorkspace.load(destination)
                source_pack = deepcopy(source.state.get("pack") or {})
                source_target = source.state.get("target") or {}
                workspace.state["pack"] = {
                    "id": materialized_pack_id(
                        str(source_pack.get("id") or "imported.skin"),
                        target,
                    ),
                    "name": str(
                        source_pack.get("name") or source_pack.get("id") or "Skin"
                    ),
                    "version": str(source_pack.get("version") or "0.1.0"),
                }
                workspace.state["source_pack"] = source_pack
                workspace.state["target"] = deepcopy(target)
                if str(source_target.get("hero") or "").casefold() != str(
                    target.get("hero") or ""
                ).casefold():
                    audio = workspace.audio_manifest()
                    if audio:
                        remapped = _retarget_audio_manifest_payload(audio, target)
                        if remapped.get("routes"):
                            _write_json(workspace.audio_manifest_path(), remapped)
                        else:
                            workspace.state["audio_manifest"] = None
                    # Animation sources are also authored against a specific
                    # profession skeleton.  Cross-profession reuse is static
                    # visual-only until the Spine component materializes a
                    # verified target-specific request.
                    workspace.state["animation"] = {
                        "mode": "none",
                        "files": [],
                        "runtime_ready": False,
                    }
                workspace.save()
                materialized.append(workspace)
            return StudioWorkspace.deploy_many(materialized, game_dir)

    @staticmethod
    def deploy_many(
        workspaces: list["StudioWorkspace"],
        game_dir: Path | None = None,
    ) -> dict:
        if not workspaces:
            raise ValueError("Select at least one workspace to deploy.")
        for workspace in workspaces:
            workspace.build_pack()
            errors = validate_pack(workspace.directory)
            if errors:
                raise ValueError(
                    f"Pack validation failed for {workspace.directory}: "
                    + "; ".join(errors)
                )
        targets: set[tuple[str, str]] = set()
        for workspace in workspaces:
            target = workspace.state["target"]
            identity = (
                str(target.get("hero") or "").casefold(),
                str(target.get("skin_name_contains") or "").casefold(),
            )
            if identity in targets:
                raise ValueError(
                    "Only one enabled workspace may target each hero skin."
                )
            targets.add(identity)

        adapters = [_adapter_for_target(item.state["target"]) for item in workspaces]
        if game_dir is not None:
            game = explicit_install(game_dir)
        else:
            installs = [item for item in detect_installs() if item.complete]
            if not installs:
                raise ValueError("No complete Steam installation was detected.")
            game = installs[0]
        for adapter in adapters:
            if not adapter.supports_build(game.build_id):
                raise ValueError(
                    f"Adapter {adapter.adapter_id} is not verified for Steam "
                    f"build {game.build_id or 'unknown'}."
                )
        # Deploy the same exact payload surface as export_zip. Authoring inputs
        # must never be copied into the managed mods directory.
        with tempfile.TemporaryDirectory() as temp:
            staged_packs: list[Path] = []
            for index, workspace in enumerate(workspaces):
                manifest = _read_json(workspace.directory / "mod.json")
                staged = Path(temp) / f"pack-{index:02d}"
                paths = workspace._declared_payload_paths(manifest)
                paths.extend(
                    [
                        workspace.directory / "mod.json",
                        workspace.directory / "asset-index.json",
                    ]
                )
                for source in paths:
                    relative = source.relative_to(workspace.directory)
                    destination = staged / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
                staged_packs.append(staged)
            return install_many(DEFAULT_RUNTIME, staged_packs, game)

    def undeploy(self) -> list[str]:
        return uninstall()

    def diagnostics(self) -> dict:
        return installation_diagnostics()

    def detected_game(self, game_dir: Path | None = None):
        try:
            return preferred_game_install(game_dir)
        except RuntimeError:
            return None

    def launch_game(self, game_dir: Path | None = None) -> dict:
        return launch_game(game_dir)


def remove_color_screen(
    image: Image.Image,
    color: str,
    tolerance: int = 28,
    feather: int = 18,
) -> Image.Image:
    """Turn a selected backdrop colour into alpha with a small feather."""
    value = color.strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", value):
        raise ValueError("Screen colour must use #RRGGBB.")
    key = tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))
    tolerance = max(0, min(255, int(tolerance)))
    feather = max(1, min(128, int(feather)))
    source = image.convert("RGBA")
    output: list[tuple[int, int, int, int]] = []
    for red, green, blue, alpha in source.getdata():
        distance = math.sqrt(
            (red - key[0]) ** 2
            + (green - key[1]) ** 2
            + (blue - key[2]) ** 2
        )
        if distance <= tolerance:
            adjusted = 0
        elif distance < tolerance + feather:
            adjusted = round(alpha * (distance - tolerance) / feather)
        else:
            adjusted = alpha
        output.append((red, green, blue, adjusted))
    source.putdata(output)
    return source


def restore_before_application_uninstall() -> list[str]:
    """Restore manager-owned game files before Windows removes the manager."""
    return uninstall()


def _wav_is_runtime_ready(path: Path) -> bool:
    try:
        with wave.open(str(path), "rb") as source:
            return (
                source.getnchannels() == 1
                and source.getframerate() == 22050
                and source.getsampwidth() == 2
                and source.getcomptype() == "NONE"
            )
    except (wave.Error, OSError):
        return False


def find_ffmpeg(explicit: Path | None = None) -> Path | None:
    if explicit and explicit.is_file():
        return explicit
    candidate = shutil.which("ffmpeg")
    return Path(candidate) if candidate else None


def transcode_audio(
    source: Path,
    destination: Path,
    *,
    ffmpeg_path: Path | None = None,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix.casefold() == ".wav" and _wav_is_runtime_ready(source):
        shutil.copy2(source, destination)
        return
    ffmpeg = find_ffmpeg(ffmpeg_path)
    if ffmpeg is None:
        raise ValueError(
            "This audio file needs conversion, but ffmpeg was not found. "
            "Install ffmpeg or provide PCM16 mono 22050 Hz WAV."
        )
    process = subprocess.run(
        [
            str(ffmpeg),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "22050",
            "-c:a",
            "pcm_s16le",
            str(destination),
        ],
        capture_output=True,
        text=True,
        creationflags=(
            subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        ),
    )
    if process.returncode != 0:
        raise ValueError(
            "ffmpeg conversion failed: "
            + (process.stderr.strip() or "unknown error")
        )
