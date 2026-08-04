#!/usr/bin/env python3
"""Import, preview, deploy, and restore external Spine character packages."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import struct
import tempfile
import threading
import time
import zipfile
import zlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from PIL import Image, ImageChops

from adapter_registry import AdapterRecord, AdapterRegistry
from bazaar_skin_manager import (
    DEFAULT_RUNTIME,
    GameInstall,
    existing_install_record,
    install_many,
    manager_root,
    uninstall,
)


UNITY_VERSION = "6000.3.11f1"
STATE_ROOT = Path(
    os.environ.get(
        "BAZAAR_SPINE_MANAGER_HOME",
        str(manager_root().parent / "spine-manager"),
    )
)
WORKSPACE_ROOT = STATE_ROOT / "workspace"
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_EXTRACTED_BYTES = 512 * 1024 * 1024
LOGGER = logging.getLogger("bazaar_spine_manager.core")
ProgressCallback = Callable[[str], None]
SUPPORTED_SPINE_VERSIONS = ("4.1", "4.2")
TARGET_SPINE_VERSION = "4.2.43"


@contextmanager
def _stage(progress: ProgressCallback | None, label: str) -> Iterator[None]:
    started = time.perf_counter()
    stopped = threading.Event()

    def heartbeat() -> None:
        while not stopped.wait(15):
            LOGGER.info(
                "stage_heartbeat label=%s elapsed_seconds=%.3f",
                label,
                time.perf_counter() - started,
            )

    LOGGER.info("stage_start label=%s", label)
    if progress:
        progress(f"{label}…")
    threading.Thread(
        target=heartbeat,
        name="spine-manager-log-heartbeat",
        daemon=True,
    ).start()
    try:
        yield
    except Exception:
        stopped.set()
        LOGGER.exception(
            "stage_failed label=%s elapsed_seconds=%.3f",
            label,
            time.perf_counter() - started,
        )
        raise
    stopped.set()
    elapsed = time.perf_counter() - started
    LOGGER.info("stage_complete label=%s elapsed_seconds=%.3f", label, elapsed)
    if progress:
        progress(f"{label}完成（{elapsed:.1f} 秒）")


@dataclass(frozen=True)
class SpineBundleContract:
    bundle_relative: str
    unity_version: str
    supported_original_sha256: tuple[str, ...]
    prefix: str | None = None


@dataclass(frozen=True)
class SpineTarget:
    adapter_id: str
    hero: str
    skin: str
    prefix: str
    bundle_relative: str
    unity_version: str
    supported_original_sha256: tuple[str, ...]
    supported_builds: tuple[str, ...]
    additional_bundles: tuple[SpineBundleContract, ...] = ()


@dataclass(frozen=True)
class SpinePackage:
    root: Path
    json_path: Path
    atlas_path: Path
    texture_path: Path
    version: str
    animations: tuple[str, ...]
    skins: tuple[str, ...]
    atlas_scale: float
    width: int
    height: int


@dataclass(frozen=True)
class SpinePlacement:
    animation: str
    root_x_offset: float = 0.0
    root_y_offset: float = 100.0
    scale_multiplier: float = 1.0
    preview_x: float = 0.0
    preview_y: float = -180.0
    preview_scale: float = 1.0


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bundle_crc32(environment) -> int:
    checksum = 0
    for node in environment.file.files.values():
        data = node.reader.bytes if hasattr(node, "reader") else node.bytes
        checksum = zlib.crc32(data, checksum)
    return checksum & 0xFFFFFFFF


def _single(items: list, description: str):
    if len(items) != 1:
        raise RuntimeError(f"Expected one {description}; found {len(items)}.")
    return items[0]


def _safe_extract(archive: Path, destination: Path) -> None:
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("Spine archive exceeds the compressed size limit.")
    extracted = 0
    destination = destination.resolve()
    with zipfile.ZipFile(archive) as source:
        for info in source.infolist():
            extracted += int(info.file_size)
            if extracted > MAX_EXTRACTED_BYTES:
                raise ValueError("Spine archive exceeds the extraction size limit.")
            target = (destination / info.filename).resolve()
            if target != destination and destination not in target.parents:
                raise ValueError(f"Unsafe archive path: {info.filename}")
        source.extractall(destination)


def atlas_scale(text: str) -> float:
    for line in text.splitlines()[:12]:
        if line.startswith("scale:"):
            return float(line.split(":", 1)[1])
    return 1.0


@dataclass(frozen=True)
class _AtlasPage:
    name: str
    lines: tuple[str, ...]
    metadata_end: int


def _atlas_pages(text: str) -> tuple[_AtlasPage, ...]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise ValueError("The Spine atlas is empty.")
    pages = []
    for block in re.split(r"\n[ \t]*\n+", normalized):
        lines = tuple(block.splitlines())
        if not lines or not lines[0].strip():
            continue
        metadata_end = 1
        while metadata_end < len(lines) and ":" in lines[metadata_end]:
            metadata_end += 1
        pages.append(_AtlasPage(lines[0].strip(), lines, metadata_end))
    if not pages:
        raise ValueError("The Spine atlas contains no texture pages.")
    return tuple(pages)


def _resolve_atlas_texture(atlas_path: Path, page_name: str) -> Path:
    page_root = atlas_path.parent.resolve()
    direct = (page_root / page_name.replace("\\", "/")).resolve()
    if direct != page_root and page_root not in direct.parents:
        raise ValueError(f"Unsafe atlas texture page path: {page_name}")
    if direct.is_file():
        return direct
    expected = page_name.replace("\\", "/").casefold()
    candidates = [
        path
        for path in atlas_path.parent.rglob("*.png")
        if path.relative_to(atlas_path.parent).as_posix().casefold() == expected
    ]
    if not candidates:
        candidates = [
            path
            for path in atlas_path.parent.rglob("*.png")
            if path.name.casefold() == Path(page_name).name.casefold()
        ]
    return _single(candidates, f"atlas texture page {page_name!r}")


def _normalize_atlas_pages(atlas_path: Path, destination: Path) -> tuple[Path, Path, str]:
    atlas_text = atlas_path.read_text(encoding="utf-8-sig")
    pages = _atlas_pages(atlas_text)
    textures = tuple(_resolve_atlas_texture(atlas_path, page.name) for page in pages)
    scales = tuple(atlas_scale("\n".join(page.lines)) for page in pages)
    if any(abs(scale - scales[0]) > 1e-9 for scale in scales[1:]):
        raise ValueError("All pages in a multi-page Spine atlas must use the same scale.")
    if len(pages) == 1:
        return atlas_path, textures[0], atlas_text

    images = []
    try:
        for texture in textures:
            with Image.open(texture) as source:
                images.append(source.convert("RGBA"))
        width = max(image.width for image in images)
        height = sum(image.height for image in images)
        if width > 8192 or height > 8192:
            raise ValueError(
                f"Merged Spine atlas would be {width}×{height}; the supported limit is 8192×8192."
            )
        merged = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        output_blocks = []
        y_offset = 0
        bounds_pattern = re.compile(
            r"^(?P<prefix>\s*bounds\s*:\s*)(?P<x>-?\d+)\s*,\s*(?P<y>-?\d+)\s*,\s*"
            r"(?P<width>\d+)\s*,\s*(?P<height>\d+)(?P<suffix>\s*)$"
        )
        for index, (page, image) in enumerate(zip(pages, images)):
            merged.alpha_composite(image, (0, y_offset))
            lines = list(page.lines)
            if index == 0:
                lines[0] = "atlas.png"
                size_index = next(
                    (
                        line_index
                        for line_index in range(1, page.metadata_end)
                        if lines[line_index].strip().casefold().startswith("size:")
                    ),
                    None,
                )
                if size_index is None:
                    lines.insert(1, f"size:{width},{height}")
                else:
                    lines[size_index] = f"size:{width},{height}"
                output_blocks.append(lines)
            else:
                output_blocks.append(list(lines[page.metadata_end :]))
            region_lines = output_blocks[-1]
            for line_index, line in enumerate(region_lines):
                match = bounds_pattern.match(line)
                if match:
                    region_lines[line_index] = (
                        f"{match.group('prefix')}{match.group('x')},"
                        f"{int(match.group('y')) + y_offset},"
                        f"{match.group('width')},{match.group('height')}{match.group('suffix')}"
                    )
            y_offset += image.height

        destination.mkdir(parents=True, exist_ok=True)
        normalized_texture = destination / "atlas.png"
        normalized_atlas = destination / "atlas.atlas"
        merged.save(normalized_texture)
        normalized_text = "\n".join(
            line for block in output_blocks for line in block
        ) + "\n"
        normalized_atlas.write_text(normalized_text, encoding="utf-8")
        LOGGER.info(
            "atlas_pages_merged pages=%s width=%s height=%s sources=%s",
            len(pages),
            width,
            height,
            [path.name for path in textures],
        )
        return normalized_atlas, normalized_texture, normalized_text
    finally:
        for image in images:
            image.close()


def import_spine_package(source: Path, workspace: Path = WORKSPACE_ROOT) -> SpinePackage:
    source = source.resolve()
    if not source.exists():
        raise FileNotFoundError(source)
    staging = workspace.with_name(workspace.name + ".staging")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    if source.is_file() and source.suffix.casefold() == ".zip":
        _safe_extract(source, staging)
    elif source.is_dir():
        shutil.copytree(source, staging, dirs_exist_ok=True)
    else:
        raise ValueError("Import a ZIP archive or extracted Spine directory.")

    json_paths = sorted(staging.rglob("*.json"))
    atlas_paths = sorted(staging.rglob("*.atlas"))
    json_path = _single(json_paths, "Spine JSON file")
    atlas_path = _single(atlas_paths, "Spine atlas file")
    payload = json.loads(json_path.read_text(encoding="utf-8-sig"))
    skeleton = payload.get("skeleton") or {}
    version = str(skeleton.get("spine") or "")
    if not version.startswith(SUPPORTED_SPINE_VERSIONS):
        raise ValueError(
            f"Spine 4.1 or 4.2 JSON is required; found {version or 'unknown'}."
        )
    skins = tuple(
        str(item.get("name"))
        for item in payload.get("skins") or []
        if isinstance(item, dict) and item.get("name")
    )
    if "default" not in skins:
        raise ValueError("The package must contain a default skin.")
    animations = tuple(str(name) for name in (payload.get("animations") or {}))
    if not animations:
        raise ValueError("The package contains no Spine animations.")
    atlas_path, texture_path, atlas_text = _normalize_atlas_pages(
        atlas_path, staging / "normalized"
    )
    with Image.open(texture_path) as image:
        width, height = image.size

    shutil.rmtree(workspace, ignore_errors=True)
    os.replace(staging, workspace)
    return SpinePackage(
        root=workspace,
        json_path=workspace / json_path.relative_to(staging),
        atlas_path=workspace / atlas_path.relative_to(staging),
        texture_path=workspace / texture_path.relative_to(staging),
        version=version,
        animations=animations,
        skins=skins,
        atlas_scale=atlas_scale(atlas_text),
        width=width,
        height=height,
    )


def targets(registry: AdapterRegistry | None = None) -> tuple[SpineTarget, ...]:
    records = (registry or AdapterRegistry.load()).records
    resolved = []
    for record in records:
        target = _target_from_adapter(record)
        if target is not None:
            resolved.append(target)
    return tuple(resolved)


def _target_from_adapter(record: AdapterRecord) -> SpineTarget | None:
    primary_deployments = []
    all_deployments = []
    for slot in record.payload.get("visual_replacements") or []:
        deployment = slot.get("deployment") or {}
        target = str(deployment.get("target") or "")
        if Path(target).name.startswith("skin_") and target.endswith("_assets_all.bundle"):
            primary_deployments.append(deployment)
            all_deployments.append(deployment)
        for additional in slot.get("additional_deployments") or []:
            additional_target = str(additional.get("target") or "")
            if (
                Path(additional_target).name.startswith("skin_")
                and additional_target.endswith("_assets_all.bundle")
            ):
                all_deployments.append(additional)
    primary_unique = {
        str(item["target"]): item for item in primary_deployments
    }
    if len(primary_unique) != 1:
        return None
    deployment = next(iter(primary_unique.values()))
    all_unique = {str(item["target"]): item for item in all_deployments}
    prefix_token = record.skin_name_contains
    prefix = prefix_token if prefix_token.startswith("Skin_") else "Skin_" + prefix_token
    additional_bundles = tuple(
        SpineBundleContract(
            bundle_relative=str(item["target"]).replace("/", os.sep),
            unity_version=str(item.get("unity_version") or UNITY_VERSION),
            supported_original_sha256=tuple(
                str(value).casefold()
                for value in item.get("supported_original_sha256") or []
            ),
        )
        for key, item in sorted(all_unique.items())
        if key != str(deployment["target"])
    )
    return SpineTarget(
        adapter_id=record.adapter_id,
        hero=record.hero,
        skin=record.skin,
        prefix=prefix,
        bundle_relative=str(deployment["target"]).replace("/", os.sep),
        unity_version=str(deployment.get("unity_version") or UNITY_VERSION),
        supported_original_sha256=tuple(
            str(value).casefold()
            for value in deployment.get("supported_original_sha256") or []
        ),
        supported_builds=record.supported_builds,
        additional_bundles=additional_bundles,
    )


def _unitypy(version: str):
    import UnityPy

    UnityPy.config.FALLBACK_UNITY_VERSION = version
    return UnityPy


def _named(environment, type_name: str, object_name: str):
    matches = []
    for item in environment.objects:
        if item.type.name != type_name:
            continue
        data = item.read()
        if getattr(data, "m_Name", "") == object_name:
            matches.append((item, data))
    return _single(matches, f"{type_name} named {object_name!r}")


def _binary_bounds(environment, prefix: str) -> tuple[float, float, float, float]:
    item, _ = _named(environment, "TextAsset", prefix + ".skel")
    raw = item.get_raw_data()
    name_length = struct.unpack_from("<I", raw, 0)[0]
    cursor = (4 + name_length + 3) & ~3
    script_length = struct.unpack_from("<I", raw, cursor)[0]
    script = raw[cursor + 4 : cursor + 4 + script_length]
    version_length = script[8]
    if version_length & 0x80:
        raise RuntimeError("Unsupported original Spine binary header.")
    bounds_offset = 9 + version_length - 1
    return struct.unpack_from(">ffff", script, bounds_offset)


def _rewrite_atlas(text: str, page_name: str) -> str:
    lines = text.splitlines()
    if not lines:
        raise ValueError("The Spine atlas is empty.")
    lines[0] = page_name
    pma = next((index for index, line in enumerate(lines[:10]) if line.startswith("pma:")), None)
    if pma is None:
        filter_index = next(
            (index for index, line in enumerate(lines[:10]) if line.startswith("filter:")),
            2,
        )
        lines.insert(filter_index + 1, "pma:true")
    else:
        lines[pma] = "pma:true"
    return "\n".join(lines) + "\n"


def _premultiply(image: Image.Image) -> Image.Image:
    red, green, blue, alpha = image.convert("RGBA").split()
    return Image.merge(
        "RGBA",
        (
            ImageChops.multiply(red, alpha),
            ImageChops.multiply(green, alpha),
            ImageChops.multiply(blue, alpha),
            alpha,
        ),
    )


def _prepared_json(package: SpinePackage, placement: SpinePlacement) -> tuple[str, dict]:
    payload = json.loads(package.json_path.read_text(encoding="utf-8-sig"))
    skeleton = payload.setdefault("skeleton", {})
    if package.version.startswith("4.1"):
        skeleton["spine"] = TARGET_SPINE_VERSION
        LOGGER.info(
            "spine_json_version_normalized source=%s target=%s",
            package.version,
            TARGET_SPINE_VERSION,
        )
    animations = payload.get("animations") or {}
    if placement.animation not in animations:
        raise ValueError(f"Animation does not exist: {placement.animation}")
    animations["idle"] = deepcopy(animations[placement.animation])
    bones = payload.get("bones") or []
    if not bones:
        raise ValueError("Spine JSON contains no bones.")
    root = bones[0]
    root["x"] = float(root.get("x", 0.0)) + placement.root_x_offset
    root["y"] = float(root.get("y", 0.0)) + placement.root_y_offset
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")), payload


def patch_bundle(
    source_bundle: Path,
    output_bundle: Path,
    package: SpinePackage,
    target: SpineTarget,
    placement: SpinePlacement,
    progress: ProgressCallback | None = None,
) -> dict:
    LOGGER.info(
        "patch_bundle source=%s output=%s target=%s animation=%s root_x=%s root_y=%s scale=%s",
        source_bundle,
        output_bundle,
        target.adapter_id,
        placement.animation,
        placement.root_x_offset,
        placement.root_y_offset,
        placement.scale_multiplier,
    )
    with _stage(progress, "加载原始 Unity Bundle"):
        UnityPy = _unitypy(target.unity_version)
        environment = UnityPy.load(source_bundle.read_bytes())
        source_crc = bundle_crc32(environment)

    with _stage(progress, "计算 Spine 自动缩放"):
        _x, _y, original_width, original_height = _binary_bounds(environment, target.prefix)
        _original_atlas_item, original_atlas = _named(
            environment, "TextAsset", target.prefix + ".atlas"
        )
        original_atlas_scale = atlas_scale(original_atlas.m_Script)
        replacement_height = float(
            json.loads(package.json_path.read_text(encoding="utf-8-sig"))["skeleton"]["height"]
        )
        automatic_scale = (
            0.01
            * (original_height / original_atlas_scale)
            / (replacement_height / package.atlas_scale)
        )
        final_scale = automatic_scale * placement.scale_multiplier
        json_text, payload = _prepared_json(package, placement)

    with _stage(progress, "写入骨骼、Atlas 与纹理"):
        _, skeleton_text = _named(environment, "TextAsset", target.prefix + ".skel")
        skeleton_text.m_Name = target.prefix + ".json"
        skeleton_text.m_Script = json_text
        skeleton_text.save()

        original_atlas.m_Script = _rewrite_atlas(
            package.atlas_path.read_text(encoding="utf-8-sig"), target.prefix + ".png"
        )
        original_atlas.save()

        _, texture = _named(environment, "Texture2D", target.prefix)
        with Image.open(package.texture_path) as source_image:
            expected = _premultiply(source_image)
        texture.image = expected
        texture.save()

        skeleton_data_item, _ = _named(
            environment, "MonoBehaviour", target.prefix + "_SkeletonData"
        )
        tree = skeleton_data_item.read_typetree()
        tree["scale"] = final_scale
        skeleton_data_item.save_typetree(tree)

    with _stage(progress, "序列化新 Unity Bundle"):
        output_bundle.parent.mkdir(parents=True, exist_ok=True)
        output_bundle.write_bytes(environment.file.save())

    with _stage(progress, "重新加载并验证 Bundle"):
        verification = UnityPy.load(output_bundle.read_bytes())
        output_crc = bundle_crc32(verification)
    _, saved_json = _named(verification, "TextAsset", target.prefix + ".json")
    saved_payload = json.loads(saved_json.m_Script)
    _, saved_atlas = _named(verification, "TextAsset", target.prefix + ".atlas")
    _, saved_texture = _named(verification, "Texture2D", target.prefix)
    saved_data, _ = _named(
        verification, "MonoBehaviour", target.prefix + "_SkeletonData"
    )
    if "idle" not in saved_payload.get("animations", {}):
        raise RuntimeError("Saved Spine JSON is missing idle.")
    if saved_atlas.m_Script.splitlines()[0] != target.prefix + ".png":
        raise RuntimeError("Saved atlas page does not match the target material.")
    if "pma:true" not in saved_atlas.m_Script.splitlines()[:10]:
        raise RuntimeError("Saved atlas is not marked as PMA.")
    if (saved_texture.m_Width, saved_texture.m_Height) != expected.size:
        raise RuntimeError("Saved Spine texture dimensions changed unexpectedly.")
    if abs(float(saved_data.read_typetree()["scale"]) - final_scale) > 1e-5:
        raise RuntimeError("Saved SkeletonData scale does not match the profile.")
    return {
        "source_crc32": source_crc,
        "output_crc32": output_crc,
        "automatic_scale": automatic_scale,
        "final_scale": final_scale,
        "original_bounds": [original_width, original_height],
        "replacement_bounds": [
            float(payload["skeleton"]["width"]),
            replacement_height,
        ],
        "output_sha256": sha256_file(output_bundle),
    }


def _bundle_contracts(target: SpineTarget) -> tuple[SpineBundleContract, ...]:
    primary = SpineBundleContract(
        bundle_relative=target.bundle_relative,
        unity_version=target.unity_version,
        supported_original_sha256=target.supported_original_sha256,
        prefix=target.prefix,
    )
    return (primary,) + target.additional_bundles


def _bundle_path(game: GameInstall, contract: SpineBundleContract) -> Path:
    path = (game.game_dir / contract.bundle_relative).resolve()
    try:
        path.relative_to(game.game_dir.resolve())
    except ValueError as error:
        raise RuntimeError(
            f"Spine bundle target escapes the game directory: {contract.bundle_relative}"
        ) from error
    return path


def _resolve_contract_prefix(
    source_bundle: Path,
    contract: SpineBundleContract,
) -> str:
    if contract.prefix:
        return contract.prefix
    UnityPy = _unitypy(contract.unity_version)
    environment = UnityPy.load(source_bundle.read_bytes())
    text_names: set[str] = set()
    skeleton_data_names: set[str] = set()
    for item in environment.objects:
        if item.type.name not in {"TextAsset", "MonoBehaviour"}:
            continue
        try:
            name = str(getattr(item.read(), "m_Name", ""))
        except Exception:
            continue
        if item.type.name == "TextAsset":
            text_names.add(name)
        else:
            skeleton_data_names.add(name)
    candidates = sorted(
        name[:-5]
        for name in text_names
        if name.endswith(".skel")
        and name[:-5] + ".atlas" in text_names
        and name[:-5] + "_SkeletonData" in skeleton_data_names
    )
    return _single(candidates, f"Spine skeleton prefix in {source_bundle.name}")


def serialize_spine_request(
    package: SpinePackage,
    target: SpineTarget,
    placement: SpinePlacement,
) -> dict:
    return {
        "schema_version": 1,
        "adapter_id": target.adapter_id,
        "target": {
            "hero": target.hero,
            "skin": target.skin,
        },
        "package": {
            "root": str(package.root.resolve()),
            "json_path": str(package.json_path.resolve()),
            "atlas_path": str(package.atlas_path.resolve()),
            "texture_path": str(package.texture_path.resolve()),
            "json_sha256": sha256_file(package.json_path),
            "atlas_sha256": sha256_file(package.atlas_path),
            "texture_sha256": sha256_file(package.texture_path),
        },
        "placement": asdict(placement),
    }


def _load_spine_request(record: dict) -> tuple[SpinePackage, SpineTarget, SpinePlacement]:
    if int(record.get("schema_version") or 0) != 1:
        raise ValueError("Unsupported Spine deployment request schema.")
    target = next(
        (item for item in targets() if item.adapter_id == record.get("adapter_id")),
        None,
    )
    if target is None:
        raise ValueError(f"Unknown Spine adapter: {record.get('adapter_id')}")
    package_record = record.get("package") or {}
    root = Path(package_record["root"]).resolve()
    paths = {
        "json": Path(package_record["json_path"]).resolve(),
        "atlas": Path(package_record["atlas_path"]).resolve(),
        "texture": Path(package_record["texture_path"]).resolve(),
    }
    for label, path in paths.items():
        try:
            path.relative_to(root)
        except ValueError as error:
            raise ValueError(f"Spine {label} file escapes its workspace: {path}") from error
        expected = str(package_record.get(f"{label}_sha256") or "").casefold()
        if not path.is_file() or sha256_file(path).casefold() != expected:
            raise ValueError(f"Spine {label} file is missing or changed: {path}")
    payload = json.loads(paths["json"].read_text(encoding="utf-8-sig"))
    skeleton = payload.get("skeleton") or {}
    version = str(skeleton.get("spine") or "")
    animations = tuple(str(name) for name in (payload.get("animations") or {}))
    skins = tuple(
        str(item.get("name"))
        for item in payload.get("skins") or []
        if isinstance(item, dict) and item.get("name")
    )
    with Image.open(paths["texture"]) as image:
        width, height = image.size
    package = SpinePackage(
        root=root,
        json_path=paths["json"],
        atlas_path=paths["atlas"],
        texture_path=paths["texture"],
        version=version,
        animations=animations,
        skins=skins,
        atlas_scale=atlas_scale(paths["atlas"].read_text(encoding="utf-8-sig")),
        width=width,
        height=height,
    )
    placement = SpinePlacement(**(record.get("placement") or {}))
    if placement.animation not in package.animations:
        raise ValueError(f"Animation does not exist: {placement.animation}")
    return package, target, placement


def spine_patch_plan_issues(requests: list[dict], game: GameInstall) -> list[str]:
    issues: list[str] = []
    seen: set[str] = set()
    installed = existing_install_record() or {}
    recorded_by_target = {
        str(item.get("target") or "").casefold(): item
        for item in installed.get("native_patches") or []
    }
    for record in requests:
        try:
            package, target, _placement = _load_spine_request(record)
            if target.adapter_id in seen:
                raise ValueError(f"Duplicate Spine target: {target.adapter_id}")
            seen.add(target.adapter_id)
            if target.supported_builds and game.build_id not in target.supported_builds:
                raise ValueError(
                    f"Adapter {target.adapter_id} does not support Steam build "
                    f"{game.build_id or 'unknown'}."
                )
            if not package.animations:
                raise ValueError("Spine package contains no animations.")
            for contract in _bundle_contracts(target):
                path = _bundle_path(game, contract)
                if not path.is_file():
                    raise FileNotFoundError(path)
                current_hash = sha256_file(path).casefold()
                supported = set(contract.supported_original_sha256)
                if not supported or current_hash in supported:
                    continue
                recorded = recorded_by_target.get(str(path).casefold())
                backup = Path(recorded.get("backup", "")) if recorded else None
                if not (
                    recorded
                    and current_hash == str(recorded.get("patched_sha256") or "").casefold()
                    and backup is not None
                    and backup.is_file()
                    and sha256_file(backup).casefold() in supported
                ):
                    raise ValueError(
                        f"Spine bundle hash is unsupported: {path.name} ({current_hash})"
                    )
        except (KeyError, OSError, RuntimeError, ValueError) as error:
            issues.append(str(error))
    return issues


def prepare_spine_native_patches(
    requests: list[dict],
    game: GameInstall,
    staging: Path,
    prepared: list[dict],
) -> tuple[list[dict], list[dict]]:
    combined = list(prepared)
    by_target = {
        str(item["target"]).casefold(): item for item in combined
    }
    normalized_requests: list[dict] = []
    for request_index, request in enumerate(requests):
        package, target, placement = _load_spine_request(request)
        normalized = serialize_spine_request(package, target, placement)
        deployed_bundles = []
        for bundle_index, contract in enumerate(_bundle_contracts(target)):
            target_path = _bundle_path(game, contract)
            key = str(target_path).casefold()
            current = by_target.get(key)
            if current is not None:
                source = Path(current["staged"])
                original_sha256 = str(current["original_sha256"])
                original_crc32 = str(current["original_crc32"])
                backup = Path(current["backup"])
            else:
                source = target_path
                original_sha256 = sha256_file(source)
                supported = set(contract.supported_original_sha256)
                if supported and original_sha256.casefold() not in supported:
                    raise RuntimeError(
                        f"Spine bundle hash is unsupported for {target.adapter_id}: "
                        f"{target_path.name} ({original_sha256})"
                    )
                original_crc32 = ""
                backup = (
                    manager_root()
                    / "native-backups"
                    / original_sha256
                    / target_path.name
                )
            prefix = _resolve_contract_prefix(source, contract)
            bundle_target = replace(
                target,
                prefix=prefix,
                bundle_relative=contract.bundle_relative,
                unity_version=contract.unity_version,
                supported_original_sha256=contract.supported_original_sha256,
                additional_bundles=(),
            )
            output = staging / (
                f"spine-{request_index:02d}-{bundle_index:02d}-{target_path.name}"
            )
            result = patch_bundle(
                source,
                output,
                package,
                bundle_target,
                placement,
            )
            if not original_crc32:
                original_crc32 = f"{int(result['source_crc32']):08x}"
            patched_crc32 = f"{int(result['output_crc32']):08x}"
            spine_entry = {
                "adapter_id": target.adapter_id,
                "prefix": prefix,
                "automatic_scale": result["automatic_scale"],
                "final_scale": result["final_scale"],
            }
            if current is None:
                current = {
                    "slot": f"spine:{target.adapter_id}:{prefix}",
                    "slots": [f"spine:{target.adapter_id}:{prefix}"],
                    "target": str(target_path),
                    "backup": str(backup),
                    "original_sha256": original_sha256,
                    "patched_sha256": result["output_sha256"],
                    "original_crc32": original_crc32,
                    "patched_crc32": patched_crc32,
                    "staged": str(output),
                    "asset_names": [prefix],
                    "mode": "spine_bundle",
                    "spine": [spine_entry],
                }
                combined.append(current)
                by_target[key] = current
            else:
                current["staged"] = str(output)
                current["patched_sha256"] = result["output_sha256"]
                current["patched_crc32"] = patched_crc32
                current.setdefault("slots", []).append(
                    f"spine:{target.adapter_id}:{prefix}"
                )
                current.setdefault("asset_names", []).append(prefix)
                current.setdefault("spine", []).append(spine_entry)
                current["mode"] = "composed_native_bundle"
            deployed_bundles.append(
                {
                    "target": str(target_path),
                    "prefix": prefix,
                    "final_scale": result["final_scale"],
                }
            )
        normalized["deployed_bundles"] = deployed_bundles
        normalized_requests.append(normalized)
    return combined, normalized_requests


def _staged_existing_packs(record: dict, root: Path) -> list[Path]:
    staged: list[Path] = []
    for index, item in enumerate(record.get("packs") or []):
        source = Path(item.get("path") or "")
        if not source.is_dir():
            raise RuntimeError(f"Managed skin pack is missing: {source}")
        destination = root / f"pack-{index:02d}"
        shutil.copytree(source, destination)
        staged.append(destination)
    return staged


def _staged_runtime(record: dict, root: Path) -> Path:
    source = DEFAULT_RUNTIME
    if not source.is_file():
        source = Path((record.get("plugin") or {}).get("path") or "")
    if not source.is_file():
        raise RuntimeError("The managed runtime DLL is unavailable.")
    destination = root / "BazaarSkinManager.Runtime.dll"
    shutil.copy2(source, destination)
    metadata_source = source.with_name("runtime-build.json")
    if metadata_source.is_file():
        shutil.copy2(metadata_source, destination.with_name("runtime-build.json"))
    return destination


def deploy(
    game: GameInstall,
    package: SpinePackage,
    target: SpineTarget,
    placement: SpinePlacement,
    progress: ProgressCallback | None = None,
) -> dict:
    request = serialize_spine_request(package, target, placement)
    record = existing_install_record() or {}
    requests = [
        item
        for item in record.get("spine_replacements") or []
        if item.get("adapter_id") != target.adapter_id
    ]
    requests.append(request)
    with tempfile.TemporaryDirectory(prefix="bazaar-spine-deploy-") as temp:
        root = Path(temp)
        with _stage(progress, "保留当前皮肤管理器工作区"):
            packs = _staged_existing_packs(record, root)
            runtime = _staged_runtime(record, root)
        with _stage(progress, "通过皮肤管理器统一事务部署"):
            return install_many(runtime, packs, game, spine_requests=requests)


def installation_manifest() -> dict | None:
    record = existing_install_record() or {}
    requests = record.get("spine_replacements") or []
    if not requests:
        return None
    first = dict(requests[0])
    first["count"] = len(requests)
    return first


def restore(progress: ProgressCallback | None = None) -> list[str]:
    LOGGER.info("restore_start")
    record = existing_install_record() or {}
    if not record.get("spine_replacements"):
        LOGGER.info("restore_skipped reason=no_spine_replacements")
        return []
    game_dir = Path((record.get("game") or {}).get("game_dir") or "")
    from bazaar_skin_manager import explicit_install

    game = explicit_install(game_dir)
    with tempfile.TemporaryDirectory(prefix="bazaar-spine-restore-") as temp:
        root = Path(temp)
        with _stage(progress, "保留当前皮肤资产包"):
            packs = _staged_existing_packs(record, root)
            runtime = _staged_runtime(record, root)
        if packs:
            with _stage(progress, "移除 Spine 替换并重新部署皮肤资产"):
                install_many(runtime, packs, game, spine_requests=[])
        else:
            with _stage(progress, "恢复皮肤管理器托管的原始文件"):
                uninstall()
    LOGGER.info("restore_complete")
    return [str(item.get("target")) for item in record.get("native_patches") or []]
