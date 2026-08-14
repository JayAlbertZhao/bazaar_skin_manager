#!/usr/bin/env python3
"""First-class asset library for Skin Manager 1.2.

The deployable StudioWorkspace format remains backward compatible.  This
module adds a content-addressed library above it: source images, small icons,
audio and Spine groups receive stable ids and workspaces record references to
those ids.  Existing runtime builders can therefore continue consuming local
workspace files while the UI gains real asset ownership and reuse semantics.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from PIL import Image

from bazaar_skin_manager import manager_root, sha256_file
from mod_studio_core import (
    ANIMATION_EXTENSIONS,
    SUPPORTED_AUDIO_EXTENSIONS,
    SUPPORTED_IMAGE_EXTENSIONS,
    StudioWorkspace,
)


LIBRARY_SCHEMA_VERSION = 2
ASSET_TYPES = {
    "character_source",
    "background",
    "small_icon",
    "icon_source",
    "derived_image",
    "audio",
    "spine",
    "other_image",
    "other",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_id(value: str) -> str:
    value = re.sub(r"[^a-z0-9._-]+", "-", value.casefold()).strip(".-")
    return value[:96] or "asset"


def _combined_hash(files: Iterable[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted((Path(item).resolve() for item in files), key=lambda p: p.name.casefold()):
        digest.update(path.name.casefold().encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256_file(path).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=path.name + ".",
        suffix=".tmp",
        delete=False,
    ) as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


class AssetLibrary:
    """Content-addressed first-class assets and workspace references."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or (manager_root() / "library-assets")).resolve()
        self.index_path = self.root / "library-index.json"
        self.payload = self._load()

    def _load(self) -> dict:
        if not self.index_path.is_file():
            return {"schema_version": LIBRARY_SCHEMA_VERSION, "assets": {}}
        try:
            payload = json.loads(self.index_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return {"schema_version": LIBRARY_SCHEMA_VERSION, "assets": {}}
        if not isinstance(payload.get("assets"), dict):
            payload["assets"] = {}
        payload["schema_version"] = LIBRARY_SCHEMA_VERSION
        return payload

    def save(self) -> None:
        _write_json_atomic(self.index_path, self.payload)

    @property
    def assets(self) -> dict[str, dict]:
        return self.payload.setdefault("assets", {})

    def _record_directory(self, asset_id: str) -> Path:
        return self.root / asset_id

    def find_by_hash(self, digest: str, asset_type: str | None = None) -> dict | None:
        for record in self.assets.values():
            if record.get("sha256") != digest:
                continue
            if asset_type and record.get("type") != asset_type:
                continue
            if self.record_files(record):
                return record
        return None

    def record_files(self, record: dict) -> list[Path]:
        root = self._record_directory(str(record.get("id") or ""))
        result: list[Path] = []
        for relative in record.get("files") or []:
            path = (root / str(relative)).resolve()
            try:
                path.relative_to(root.resolve())
            except ValueError:
                continue
            if path.is_file():
                result.append(path)
        return result

    def preview_path(self, record: dict) -> Path | None:
        files = self.record_files(record)
        for path in files:
            if path.suffix.casefold() in SUPPORTED_IMAGE_EXTENSIONS:
                return path
        return None

    def import_files(
        self,
        files: Iterable[Path],
        *,
        asset_type: str,
        name: str,
        metadata: dict | None = None,
        source: str | None = None,
    ) -> dict:
        sources = [Path(path).resolve() for path in files]
        if not sources or any(not path.is_file() for path in sources):
            raise ValueError("At least one readable asset file is required.")
        if asset_type not in ASSET_TYPES:
            raise ValueError(f"Unknown first-class asset type: {asset_type}")
        digest = _combined_hash(sources)
        existing = self.find_by_hash(digest, asset_type)
        if existing:
            return existing

        base = _safe_id(name)
        asset_id = f"{base}.{digest[:12]}"
        counter = 2
        while asset_id in self.assets:
            asset_id = f"{base}.{digest[:10]}.{counter}"
            counter += 1
        destination = self._record_directory(asset_id)
        destination.mkdir(parents=True, exist_ok=False)
        relative_files: list[str] = []
        used_names: set[str] = set()
        for index, path in enumerate(sources, start=1):
            filename = path.name
            if filename.casefold() in used_names:
                filename = f"{path.stem}-{index}{path.suffix}"
            used_names.add(filename.casefold())
            target = destination / filename
            shutil.copy2(path, target)
            relative_files.append(filename)

        details = dict(metadata or {})
        first_image = next(
            (
                destination / relative
                for relative in relative_files
                if Path(relative).suffix.casefold() in SUPPORTED_IMAGE_EXTENSIONS
            ),
            None,
        )
        if first_image:
            try:
                with Image.open(first_image) as image:
                    details.setdefault("image_size", list(image.size))
            except OSError:
                pass
        record = {
            "id": asset_id,
            "type": asset_type,
            "name": name.strip() or sources[0].stem,
            "sha256": digest,
            "files": relative_files,
            "source": source or str(sources[0]),
            "created_at": _now(),
            "metadata": details,
        }
        self.assets[asset_id] = record
        self.save()
        return record

    def import_file(
        self,
        source: Path,
        *,
        asset_type: str,
        name: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        source = Path(source).resolve()
        return self.import_files(
            [source],
            asset_type=asset_type,
            name=name or source.stem,
            metadata=metadata,
        )

    def import_spine(
        self,
        files: Iterable[Path],
        *,
        name: str,
        runtime_version: str,
        target: dict | None = None,
        author: str = "",
        license_text: str = "",
    ) -> dict:
        sources = [Path(path).resolve() for path in files]
        if len(sources) == 1 and sources[0].suffix.casefold() == ".zip":
            return self.import_spine_package(
                sources[0],
                name=name,
                target=target,
                author=author,
                license_text=license_text,
            )
        unsupported = [
            path.name
            for path in sources
            if path.suffix.casefold() not in ANIMATION_EXTENSIONS
        ]
        if unsupported:
            raise ValueError("Unsupported Spine files: " + ", ".join(unsupported))
        extensions = {path.suffix.casefold() for path in sources}
        if ".atlas" not in extensions:
            raise ValueError("Spine asset requires an .atlas file.")
        if ".json" not in extensions:
            raise ValueError("当前已验证导入路径要求 Spine JSON 骨骼文件。")
        if ".skel" in extensions:
            raise ValueError("二进制 .skel 尚未进入已验证导入路径；请导出 Spine JSON。")
        if not ({".png"} & extensions):
            raise ValueError("Spine asset requires at least one texture PNG.")
        if len({path.name.casefold() for path in sources}) != len(sources):
            raise ValueError("Spine 导入文件存在重名；请先整理为单一可移植资源目录。")

        # Reuse the already verified Spine 4.1/4.2 importer instead of adding a
        # second, weaker atlas parser here.  Store a normalized one-page group
        # so later workspace references remain valid after content-addressing.
        from spine_manager_core import import_spine_package

        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            source_root = temporary_root / "source"
            source_root.mkdir()
            for source_file in sources:
                shutil.copy2(source_file, source_root / source_file.name)
            package = import_spine_package(
                source_root,
                workspace=temporary_root / "validated",
            )
            declared = runtime_version.strip()
            if declared and not package.version.startswith(declared):
                raise ValueError(
                    f"填写的 Spine 版本 {declared} 与资源版本 {package.version} 不一致。"
                )
            return self._store_spine_package(
                package,
                sources=sources,
                name=name,
                target=target,
                author=author,
                license_text=license_text,
                temporary_root=temporary_root,
                source=str(sources[0].parent),
            )

    def import_spine_package(
        self,
        source: Path,
        *,
        name: str,
        target: dict | None = None,
        author: str = "",
        license_text: str = "",
    ) -> dict:
        """Validate and import a portable Spine ZIP or extracted directory.

        Runtime version, atlas pages and texture names come from the package
        itself. Multi-page atlases are normalized to the same portable
        one-page representation used by the manual file importer.
        """
        source = Path(source).resolve()
        if not source.exists():
            raise FileNotFoundError(source)
        if source.is_file() and source.suffix.casefold() != ".zip":
            raise ValueError("Spine package must be a ZIP archive or directory.")

        from spine_manager_core import import_spine_package

        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary)
            package = import_spine_package(
                source,
                workspace=temporary_root / "validated",
            )
            source_files = [source] if source.is_file() else sorted(
                (path for path in source.rglob("*") if path.is_file()),
                key=lambda path: path.relative_to(source).as_posix().casefold(),
            )
            return self._store_spine_package(
                package,
                sources=source_files,
                name=name,
                target=target,
                author=author,
                license_text=license_text,
                temporary_root=temporary_root,
                source=str(source),
                package_format="zip" if source.is_file() else "directory",
            )

    def _store_spine_package(
        self,
        package,
        *,
        sources: list[Path],
        name: str,
        target: dict | None,
        author: str,
        license_text: str,
        temporary_root: Path,
        source: str,
        package_format: str = "selected_files",
    ) -> dict:
        from spine_manager_core import _rewrite_atlas

        normalized = temporary_root / "portable"
        normalized.mkdir()
        json_path = normalized / "skeleton.json"
        atlas_path = normalized / "skeleton.atlas"
        texture_path = normalized / "skeleton.png"
        shutil.copy2(package.json_path, json_path)
        atlas_text = package.atlas_path.read_text(encoding="utf-8-sig")
        atlas_path.write_text(
            _rewrite_atlas(atlas_text, texture_path.name),
            encoding="utf-8",
        )
        shutil.copy2(package.texture_path, texture_path)
        return self.import_files(
            [json_path, atlas_path, texture_path],
            asset_type="spine",
            name=name,
            metadata={
                "runtime_version": package.version,
                "source_version": package.source_version,
                "target": target or {},
                "author": author.strip(),
                "license": license_text.strip(),
                "animations": list(package.animations),
                "skins": list(package.skins),
                "image_size": [package.width, package.height],
                "atlas_scale": package.atlas_scale,
                "package_format": package_format,
                "source_files": [
                    {
                        "name": source_file.name,
                        "sha256": sha256_file(source_file),
                    }
                    for source_file in sources
                ],
            },
            source=source,
        )

    def register_workspace(self, workspace: StudioWorkspace) -> dict[str, str]:
        """Migrate authoring inputs and reusable outputs into the asset index."""
        state = workspace.state
        references = state.setdefault(
            "library_assets",
            {"inputs": {}, "visual_slots": {}, "audio": {}, "animation": None},
        )
        references.setdefault("inputs", {})
        references.setdefault("visual_slots", {})
        references.setdefault("audio", {})
        changed = False
        pack = state.get("pack") or {}
        pack_name = str(pack.get("name") or pack.get("id") or "Skin")

        input_types = {
            "character": "character_source",
            "background": "background",
            "small_icon": "small_icon",
            "small_icon_source": "icon_source",
        }
        authoring_inputs = ((state.get("authoring") or {}).get("inputs") or {})
        if (state.get("authoring") or {}).get("mode") == "manual_slots":
            for key in authoring_inputs:
                layer = str(key).rsplit(".", 1)[-1]
                input_types[str(key)] = {
                    "background": "background",
                    "character": "character_source",
                    "direct": "other_image",
                }.get(layer, "other_image")
        for key, asset_type in input_types.items():
            if references["inputs"].get(key):
                continue
            record = authoring_inputs.get(key) or {}
            relative = record.get("workspace_file") or record.get("source_file")
            candidate = workspace.directory / str(relative or "")
            if not candidate.is_file():
                candidate = workspace.directory / "authoring" / "inputs" / f"{key}.png"
            if not candidate.is_file():
                matches = list((workspace.directory / "authoring" / "inputs").glob(f"{key}.*"))
                candidate = matches[0] if matches else candidate
            if candidate.is_file():
                imported = self.import_file(
                    candidate,
                    asset_type=asset_type,
                    name=f"{pack_name} · {key}",
                    metadata={"origin_pack": pack.get("id"), "authoring": record},
                )
                references["inputs"][key] = imported["id"]
                changed = True

        # The generated small icon is useful even when no separate authoring
        # source survived an old pack import.
        if not references["visual_slots"].get("hero_icon_small"):
            icon = workspace.visual_path("hero_icon_small")
            if icon:
                imported = self.import_file(
                    icon,
                    asset_type="small_icon",
                    name=f"{pack_name} · 小图标",
                    metadata={"origin_pack": pack.get("id"), "slot": "hero_icon_small"},
                )
                references["visual_slots"]["hero_icon_small"] = imported["id"]
                changed = True

        animation = state.get("animation") or {}
        if not references.get("animation") and animation.get("files"):
            files = [workspace.directory / str(value) for value in animation["files"]]
            files = [path for path in files if path.is_file()]
            if files:
                imported = self.import_files(
                    files,
                    asset_type="spine",
                    name=f"{pack_name} · Spine",
                    metadata={
                        "origin_pack": pack.get("id"),
                        "mode": animation.get("mode"),
                        "runtime_ready": bool(animation.get("runtime_ready")),
                        "target": state.get("target") or {},
                    },
                )
                references["animation"] = imported["id"]
                changed = True

        audio_manifest = workspace.audio_manifest() or {}
        for route in audio_manifest.get("routes") or []:
            logical_slot = str(route.get("logical_slot") or "")
            if not logical_slot or references["audio"].get(logical_slot):
                continue
            files = [
                workspace.directory / str(variant.get("file") or "")
                for variant in route.get("variants") or []
            ]
            files = [path for path in files if path.is_file()]
            if files:
                imported = self.import_files(
                    files,
                    asset_type="audio",
                    name=f"{pack_name} · {logical_slot}",
                    metadata={
                        "origin_pack": pack.get("id"),
                        "logical_slot": logical_slot,
                        "variant_count": len(files),
                    },
                )
                references["audio"][logical_slot] = imported["id"]
                changed = True

        if changed:
            workspace.save()
        return {
            key: str(value)
            for group in (references.get("inputs") or {}, references.get("visual_slots") or {})
            for key, value in group.items()
        }

    def references(self, workspaces: Iterable[StudioWorkspace]) -> dict[str, list[dict]]:
        result: dict[str, list[dict]] = {asset_id: [] for asset_id in self.assets}
        for workspace in workspaces:
            refs = workspace.state.get("library_assets") or {}
            values: list[tuple[str, str]] = []
            for group_name in ("inputs", "visual_slots", "audio"):
                for key, asset_id in (refs.get(group_name) or {}).items():
                    values.append((f"{group_name}.{key}", str(asset_id)))
            if refs.get("animation"):
                values.append(("animation", str(refs["animation"])))
            pack = workspace.state.get("pack") or {}
            for role, asset_id in values:
                result.setdefault(asset_id, []).append(
                    {
                        "pack_id": pack.get("id"),
                        "pack_name": pack.get("name"),
                        "workspace": str(workspace.directory),
                        "role": role,
                    }
                )
        return result

    def remove(self, asset_id: str, *, references: list[dict] | None = None) -> None:
        if references:
            names = sorted({str(item.get("pack_name") or item.get("pack_id")) for item in references})
            raise ValueError("Asset is still used by: " + ", ".join(names))
        record = self.assets.pop(asset_id, None)
        if not record:
            return
        directory = self._record_directory(asset_id)
        if directory.is_dir():
            shutil.rmtree(directory)
        self.save()

    def update_metadata(self, asset_id: str, **values: str) -> dict:
        record = self.assets[asset_id]
        for key in ("name",):
            if key in values and values[key].strip():
                record[key] = values[key].strip()
        metadata = record.setdefault("metadata", {})
        for key in ("author", "license", "source_url", "notes"):
            if key in values:
                metadata[key] = values[key].strip()
        self.save()
        return record


def classify_file(path: Path) -> str:
    suffix = path.suffix.casefold()
    if suffix in SUPPORTED_AUDIO_EXTENSIONS:
        return "audio"
    if suffix in SUPPORTED_IMAGE_EXTENSIONS:
        return "other_image"
    return "other"
