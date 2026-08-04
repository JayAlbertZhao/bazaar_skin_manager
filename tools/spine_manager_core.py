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
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image, ImageChops

from adapter_registry import AdapterRecord, AdapterRegistry
from bazaar_skin_manager import GameInstall, atomic_copy_file, manager_root


UNITY_VERSION = "6000.3.11f1"
STATE_ROOT = Path(
    os.environ.get(
        "BAZAAR_SPINE_MANAGER_HOME",
        str(manager_root().parent / "spine-manager"),
    )
)
WORKSPACE_ROOT = STATE_ROOT / "workspace"
BACKUP_ROOT = STATE_ROOT / "backups"
INSTALL_MANIFEST = STATE_ROOT / "install-manifest.json"
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
class SpineTarget:
    adapter_id: str
    hero: str
    skin: str
    prefix: str
    bundle_relative: str
    unity_version: str
    supported_original_sha256: tuple[str, ...]
    supported_builds: tuple[str, ...]


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
    deployments = []
    for slot in record.payload.get("visual_replacements") or []:
        deployment = slot.get("deployment") or {}
        target = str(deployment.get("target") or "")
        if Path(target).name.startswith("skin_") and target.endswith("_assets_all.bundle"):
            deployments.append(deployment)
    unique = {str(item["target"]): item for item in deployments}
    if len(unique) != 1:
        return None
    deployment = next(iter(unique.values()))
    prefix_token = record.skin_name_contains
    prefix = prefix_token if prefix_token.startswith("Skin_") else "Skin_" + prefix_token
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


def patch_catalog(
    source_catalog: Path,
    output_catalog: Path,
    bundle_name: str,
    source_crc: int,
    output_crc: int,
) -> dict:
    LOGGER.info(
        "patch_catalog source=%s output=%s bundle=%s source_crc=%s output_crc=%s",
        source_catalog,
        output_catalog,
        bundle_name,
        source_crc,
        output_crc,
    )
    original = source_catalog.read_bytes()
    patched = bytearray(original)
    encoded_name = bundle_name.encode("utf-8")
    positions = [match.start() for match in re.finditer(re.escape(encoded_name), original)]
    if len(positions) != 1:
        raise RuntimeError(f"Expected one catalog entry for {bundle_name}.")
    start = positions[0] + len(encoded_name)
    end = min(len(original), start + 128)
    encoded_crc = struct.pack("<I", source_crc)
    offsets = []
    cursor = start
    while True:
        offset = original.find(encoded_crc, cursor, end)
        if offset < 0:
            break
        offsets.append(offset)
        cursor = offset + 1
    if len(offsets) != 1:
        raise RuntimeError("Could not uniquely locate the bundle CRC in catalog.bin.")
    offset = offsets[0]
    patched[offset : offset + 4] = struct.pack("<I", output_crc)
    output_catalog.parent.mkdir(parents=True, exist_ok=True)
    output_catalog.write_bytes(patched)
    return {"crc_offset": offset, "output_sha256": sha256_file(output_catalog)}


def _catalog_path(game: GameInstall) -> Path:
    return game.game_dir / "TheBazaar_Data" / "StreamingAssets" / "aa" / "catalog.bin"


def _backup_path(path: Path, digest: str) -> Path:
    return BACKUP_ROOT / digest / path.name


def deploy(
    game: GameInstall,
    package: SpinePackage,
    target: SpineTarget,
    placement: SpinePlacement,
    progress: ProgressCallback | None = None,
) -> dict:
    LOGGER.info(
        "deploy_start game_dir=%s build_id=%s target=%s package_json=%s",
        game.game_dir,
        game.build_id,
        target.adapter_id,
        package.json_path,
    )
    with _stage(progress, "校验游戏目录与目标版本"):
        if target.supported_builds and game.build_id not in target.supported_builds:
            raise RuntimeError(f"Unsupported Steam build: {game.build_id or 'unknown'}")
        bundle = game.game_dir / target.bundle_relative
        catalog = _catalog_path(game)
        if not bundle.is_file() or not catalog.is_file():
            raise FileNotFoundError("Target game bundle or catalog.bin is missing.")
        existing = installation_manifest()
        source_bundle = bundle
        source_catalog = catalog
        if existing:
            recorded_game = Path(existing["game_dir"])
            if recorded_game.resolve() != game.game_dir.resolve():
                raise RuntimeError("A Spine replacement is deployed to another game directory.")
            source_bundle = Path(existing["bundle_backup"])
            source_catalog = Path(existing["catalog_backup"])

    with _stage(progress, "计算原始文件哈希"):
        source_bundle_hash = sha256_file(source_bundle)
        if (
            target.supported_original_sha256
            and source_bundle_hash.casefold() not in target.supported_original_sha256
        ):
            raise RuntimeError("The original skin bundle hash is not authorized by the adapter.")
        source_catalog_hash = sha256_file(source_catalog)
        bundle_backup = _backup_path(bundle, source_bundle_hash)
        catalog_backup = _backup_path(catalog, source_catalog_hash)

    with _stage(progress, "创建并校验原始文件备份"):
        if not bundle_backup.is_file():
            atomic_copy_file(source_bundle, bundle_backup)
        if not catalog_backup.is_file():
            atomic_copy_file(source_catalog, catalog_backup)
        if sha256_file(bundle_backup) != source_bundle_hash:
            raise RuntimeError("Bundle backup hash mismatch after copy.")
        if sha256_file(catalog_backup) != source_catalog_hash:
            raise RuntimeError("Catalog backup hash mismatch after copy.")

    with tempfile.TemporaryDirectory(prefix="bazaar-spine-") as temp:
        staging = Path(temp)
        staged_bundle = staging / bundle.name
        staged_catalog = staging / catalog.name
        bundle_result = patch_bundle(
            bundle_backup, staged_bundle, package, target, placement, progress
        )
        with _stage(progress, "更新 Addressables catalog.bin"):
            catalog_result = patch_catalog(
                catalog_backup,
                staged_catalog,
                bundle.name,
                bundle_result["source_crc32"],
                bundle_result["output_crc32"],
            )
        with _stage(progress, "替换游戏 Bundle 文件"):
            atomic_copy_file(staged_bundle, bundle)
        with _stage(progress, "替换游戏 catalog.bin"):
            atomic_copy_file(staged_catalog, catalog)

    record = {
        "schema_version": 1,
        "game_dir": str(game.game_dir.resolve()),
        "build_id": game.build_id,
        "target": asdict(target),
        "placement": asdict(placement),
        "bundle": str(bundle),
        "bundle_backup": str(bundle_backup),
        "bundle_original_sha256": source_bundle_hash,
        "bundle_patched_sha256": sha256_file(bundle),
        "catalog": str(catalog),
        "catalog_backup": str(catalog_backup),
        "catalog_original_sha256": source_catalog_hash,
        "catalog_patched_sha256": sha256_file(catalog),
        "package": {
            "version": package.version,
            "animations": list(package.animations),
            "json_sha256": sha256_file(package.json_path),
            "atlas_sha256": sha256_file(package.atlas_path),
            "texture_sha256": sha256_file(package.texture_path),
        },
        "bundle_result": bundle_result,
        "catalog_result": catalog_result,
    }
    with _stage(progress, "写入部署记录"):
        INSTALL_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        INSTALL_MANIFEST.write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    LOGGER.info("deploy_complete target=%s", target.adapter_id)
    return record


def installation_manifest() -> dict | None:
    if not INSTALL_MANIFEST.is_file():
        return None
    return json.loads(INSTALL_MANIFEST.read_text(encoding="utf-8-sig"))


def restore(progress: ProgressCallback | None = None) -> list[str]:
    LOGGER.info("restore_start")
    record = installation_manifest()
    if not record:
        LOGGER.info("restore_skipped reason=no_manifest")
        return []
    restored = []
    for target_key, backup_key, original_key in (
        ("bundle", "bundle_backup", "bundle_original_sha256"),
        ("catalog", "catalog_backup", "catalog_original_sha256"),
    ):
        with _stage(progress, f"恢复 {target_key}"):
            target = Path(record[target_key])
            backup = Path(record[backup_key])
            if not backup.is_file():
                raise RuntimeError(f"Backup is missing: {backup}")
            if sha256_file(backup) != record[original_key]:
                raise RuntimeError(f"Backup hash mismatch: {backup}")
            atomic_copy_file(backup, target)
            restored.append(str(target))
    INSTALL_MANIFEST.unlink(missing_ok=True)
    LOGGER.info("restore_complete restored=%s", restored)
    return restored
