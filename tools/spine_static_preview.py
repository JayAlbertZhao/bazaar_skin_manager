#!/usr/bin/env python3
"""Offline setup-pose renderer used by the embedded Spine placement preview."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

from bazaar_skin_manager import GameInstall
from spine_manager_core import (
    SpinePackage,
    SpineTarget,
    _binary_bounds,
    _named,
    _unitypy,
    atlas_scale,
    installation_manifest,
)


REFERENCE_WIDTH = 3840
REFERENCE_HEIGHT = 2160
CANVAS_PIXELS_PER_UNIT = 100.0
HERO_PLACEHOLDER_SCALE = 0.44
HERO_ROOT_X = 1920.0
HERO_ROOT_Y = 350.0
READY_CENTER_X = 1920.0
READY_CENTER_Y = 200.0
READY_WIDTH = 700.0
READY_HEIGHT = 270.0


@dataclass(frozen=True)
class BoneWorld:
    x: float
    y: float
    a: float
    b: float
    c: float
    d: float

    def transform(self, x: float, y: float) -> tuple[float, float]:
        return (x * self.a + y * self.b + self.x, x * self.c + y * self.d + self.y)


@dataclass(frozen=True)
class RenderedPose:
    image: Image.Image
    min_x: float
    min_y: float
    max_x: float
    max_y: float
    pixels_per_spine_unit: float


@dataclass(frozen=True)
class PreviewMetrics:
    skeleton_data_scale: float
    reference_pixels_per_spine_unit: float


@dataclass(frozen=True)
class AtlasRegion:
    name: str
    bounds: tuple[int, int, int, int]
    rotate: int
    offsets: tuple[int, int, int, int] | None


def calculate_preview_metrics(
    game: GameInstall,
    package: SpinePackage,
    target: SpineTarget,
    scale_multiplier: float = 1.0,
) -> PreviewMetrics:
    bundle = game.game_dir / target.bundle_relative
    source_bundle = bundle
    existing = installation_manifest()
    if existing:
        recorded_game = Path(existing.get("game_dir") or "")
        recorded_target = (existing.get("target") or {}).get("adapter_id")
        if recorded_game.resolve() == game.game_dir.resolve() and recorded_target == target.adapter_id:
            candidate = Path(existing.get("bundle_backup") or "")
            if candidate.is_file():
                source_bundle = candidate
    UnityPy = _unitypy(target.unity_version)
    environment = UnityPy.load(source_bundle.read_bytes())
    _x, _y, _width, original_height = _binary_bounds(environment, target.prefix)
    _atlas_item, original_atlas = _named(environment, "TextAsset", target.prefix + ".atlas")
    original_atlas_scale = atlas_scale(original_atlas.m_Script)
    replacement = json.loads(package.json_path.read_text(encoding="utf-8-sig"))
    replacement_height = float(replacement["skeleton"]["height"])
    skeleton_data_scale = (
        0.01
        * (original_height / original_atlas_scale)
        / (replacement_height / package.atlas_scale)
        * scale_multiplier
    )
    return PreviewMetrics(
        skeleton_data_scale=skeleton_data_scale,
        reference_pixels_per_spine_unit=(
            skeleton_data_scale * CANVAS_PIXELS_PER_UNIT * HERO_PLACEHOLDER_SCALE
        ),
    )


def _number(value, default: float) -> float:
    return float(default if value is None else value)


def _bone_worlds(payload: dict) -> tuple[list[BoneWorld], dict[str, int]]:
    bones = payload.get("bones") or []
    name_to_index = {str(bone["name"]): index for index, bone in enumerate(bones)}
    worlds: list[BoneWorld] = []
    for bone in bones:
        rotation = math.radians(_number(bone.get("rotation"), 0) + _number(bone.get("shearX"), 0))
        rotation_y = math.radians(
            _number(bone.get("rotation"), 0) + 90 + _number(bone.get("shearY"), 0)
        )
        local_a = math.cos(rotation) * _number(bone.get("scaleX"), 1)
        local_c = math.sin(rotation) * _number(bone.get("scaleX"), 1)
        local_b = math.cos(rotation_y) * _number(bone.get("scaleY"), 1)
        local_d = math.sin(rotation_y) * _number(bone.get("scaleY"), 1)
        local_x = _number(bone.get("x"), 0)
        local_y = _number(bone.get("y"), 0)
        parent_name = bone.get("parent")
        if parent_name:
            parent = worlds[name_to_index[str(parent_name)]]
            worlds.append(
                BoneWorld(
                    x=local_x * parent.a + local_y * parent.b + parent.x,
                    y=local_x * parent.c + local_y * parent.d + parent.y,
                    a=parent.a * local_a + parent.b * local_c,
                    b=parent.a * local_b + parent.b * local_d,
                    c=parent.c * local_a + parent.d * local_c,
                    d=parent.c * local_b + parent.d * local_d,
                )
            )
        else:
            worlds.append(BoneWorld(local_x, local_y, local_a, local_b, local_c, local_d))
    return worlds, name_to_index


def _image_candidates(package: SpinePackage) -> dict[str, Path]:
    candidates: dict[str, Path] = {}
    for path in package.root.rglob("*.png"):
        if path.resolve() == package.texture_path.resolve():
            continue
        relative = path.relative_to(package.root).with_suffix("").as_posix()
        candidates.setdefault(relative, path)
        candidates.setdefault(path.stem, path)
        if relative.startswith("images/"):
            candidates.setdefault(relative[len("images/") :], path)
    return candidates


def _parse_atlas(text: str) -> dict[str, AtlasRegion]:
    lines = [line.rstrip() for line in text.replace("\r", "").split("\n")]
    regions: dict[str, AtlasRegion] = {}
    index = 0
    while index < len(lines) and not lines[index].strip():
        index += 1
    index += 1
    while index < len(lines) and (not lines[index].strip() or ":" in lines[index]):
        index += 1
    while index < len(lines):
        name = lines[index].strip()
        index += 1
        if not name:
            continue
        values: dict[str, str] = {}
        while index < len(lines) and lines[index].strip():
            line = lines[index].strip()
            if ":" not in line:
                break
            key, value = line.split(":", 1)
            values[key.strip()] = value.strip()
            index += 1
        if "bounds" in values:
            bounds = tuple(int(value) for value in values["bounds"].split(","))
            rotate_text = values.get("rotate", "0").casefold()
            rotate = 90 if rotate_text == "true" else int(rotate_text) if rotate_text not in {"false", ""} else 0
            offsets = (
                tuple(int(value) for value in values["offsets"].split(","))
                if "offsets" in values
                else None
            )
            regions[name] = AtlasRegion(name, bounds, rotate, offsets)
        while index < len(lines) and not lines[index].strip():
            index += 1
    return regions


def _atlas_region_image(page: Image.Image, region: AtlasRegion) -> Image.Image:
    x, y, width, height = region.bounds
    packed_width, packed_height = (
        (height, width) if region.rotate in {90, 270} else (width, height)
    )
    cropped = page.crop((x, y, x + packed_width, y + packed_height)).convert("RGBA")
    if region.rotate == 90:
        cropped = cropped.transpose(Image.Transpose.ROTATE_90)
    elif region.rotate == 180:
        cropped = cropped.transpose(Image.Transpose.ROTATE_180)
    elif region.rotate == 270:
        cropped = cropped.transpose(Image.Transpose.ROTATE_270)
    if region.offsets:
        offset_x, offset_y, original_width, original_height = region.offsets
        restored = Image.new("RGBA", (original_width, original_height), (0, 0, 0, 0))
        top = original_height - offset_y - cropped.height
        restored.alpha_composite(cropped, (offset_x, top))
        return restored
    return cropped


def _attachment_image(
    attachment_name: str,
    attachment: dict,
    images: dict[str, Path],
    atlas_page: Image.Image,
    atlas_regions: dict[str, AtlasRegion],
    cache: dict[str, Image.Image],
) -> Image.Image:
    path_name = str(attachment.get("path") or attachment_name).replace("\\", "/")
    key = path_name.removesuffix(".png")
    if key in cache:
        return cache[key]
    region = atlas_regions.get(key) or atlas_regions.get(Path(key).name)
    if region is not None:
        image = _atlas_region_image(atlas_page, region)
    else:
        source = images.get(key) or images.get(Path(key).name)
        if source is None:
            raise KeyError(f"Missing preview image for Spine attachment: {path_name}")
        with Image.open(source) as loaded:
            image = loaded.convert("RGBA")
    cache[key] = image
    return image


def _rgba_alpha(color: str | None) -> float:
    if not color:
        return 1.0
    color = color.strip().lstrip("#")
    if len(color) != 8:
        return 1.0
    return int(color[6:8], 16) / 255.0


def _tinted_alpha(image: Image.Image, alpha: float) -> Image.Image:
    if alpha >= 0.999:
        return image
    output = image.copy()
    channel = output.getchannel("A").point(lambda value: round(value * max(0.0, alpha)))
    output.putalpha(channel)
    return output


def _region_vertices(attachment: dict, bone: BoneWorld) -> list[tuple[float, float]]:
    width = _number(attachment.get("width"), 0) * _number(attachment.get("scaleX"), 1)
    height = _number(attachment.get("height"), 0) * _number(attachment.get("scaleY"), 1)
    x = _number(attachment.get("x"), 0)
    y = _number(attachment.get("y"), 0)
    rotation = math.radians(_number(attachment.get("rotation"), 0))
    cosine = math.cos(rotation)
    sine = math.sin(rotation)
    local = [
        (-width / 2, height / 2),
        (width / 2, height / 2),
        (width / 2, -height / 2),
        (-width / 2, -height / 2),
    ]
    return [
        bone.transform(px * cosine - py * sine + x, px * sine + py * cosine + y)
        for px, py in local
    ]


def _mesh_vertices(attachment: dict, slot_bone: BoneWorld, worlds: list[BoneWorld]) -> list[tuple[float, float]]:
    vertices = attachment.get("vertices") or []
    uvs = attachment.get("uvs") or []
    if len(vertices) == len(uvs):
        return [slot_bone.transform(float(vertices[i]), float(vertices[i + 1])) for i in range(0, len(vertices), 2)]
    output: list[tuple[float, float]] = []
    cursor = 0
    while cursor < len(vertices) and len(output) < len(uvs) // 2:
        bone_count = int(vertices[cursor])
        cursor += 1
        world_x = 0.0
        world_y = 0.0
        for _ in range(bone_count):
            bone_index = int(vertices[cursor])
            local_x = float(vertices[cursor + 1])
            local_y = float(vertices[cursor + 2])
            weight = float(vertices[cursor + 3])
            cursor += 4
            transformed_x, transformed_y = worlds[bone_index].transform(local_x, local_y)
            world_x += transformed_x * weight
            world_y += transformed_y * weight
        output.append((world_x, world_y))
    return output


def _affine_from_triangles(
    destination: list[tuple[float, float]], source: list[tuple[float, float]]
) -> tuple[float, float, float, float, float, float] | None:
    x0, y0 = destination[0]
    x1, y1 = destination[1]
    x2, y2 = destination[2]
    determinant = x0 * (y1 - y2) + x1 * (y2 - y0) + x2 * (y0 - y1)
    if abs(determinant) < 1e-8:
        return None

    def solve(values: list[float]) -> tuple[float, float, float]:
        z0, z1, z2 = values
        a = (z0 * (y1 - y2) + z1 * (y2 - y0) + z2 * (y0 - y1)) / determinant
        b = (z0 * (x2 - x1) + z1 * (x0 - x2) + z2 * (x1 - x0)) / determinant
        c = (
            z0 * (x1 * y2 - x2 * y1)
            + z1 * (x2 * y0 - x0 * y2)
            + z2 * (x0 * y1 - x1 * y0)
        ) / determinant
        return a, b, c

    a, b, c = solve([point[0] for point in source])
    d, e, f = solve([point[1] for point in source])
    return a, b, c, d, e, f


def _draw_triangle(
    canvas: Image.Image,
    texture: Image.Image,
    destination: list[tuple[float, float]],
    source: list[tuple[float, float]],
) -> None:
    left = max(0, math.floor(min(point[0] for point in destination)))
    top = max(0, math.floor(min(point[1] for point in destination)))
    right = min(canvas.width, math.ceil(max(point[0] for point in destination)) + 1)
    bottom = min(canvas.height, math.ceil(max(point[1] for point in destination)) + 1)
    if right <= left or bottom <= top:
        return
    coefficients = _affine_from_triangles(destination, source)
    if coefficients is None:
        return
    a, b, c, d, e, f = coefficients
    local_coefficients = (a, b, a * left + b * top + c, d, e, d * left + e * top + f)
    patch = texture.transform(
        (right - left, bottom - top),
        Image.Transform.AFFINE,
        local_coefficients,
        resample=Image.Resampling.BICUBIC,
    )
    mask = Image.new("L", patch.size, 0)
    ImageDraw.Draw(mask).polygon(
        [(x - left, y - top) for x, y in destination],
        fill=255,
    )
    patch.putalpha(ImageChops.multiply(patch.getchannel("A"), mask))
    canvas.alpha_composite(patch, (left, top))


def render_setup_pose(package: SpinePackage, pixels_per_spine_unit: float = 2.5) -> RenderedPose:
    payload = json.loads(package.json_path.read_text(encoding="utf-8-sig"))
    worlds, bone_indices = _bone_worlds(payload)
    slots = payload.get("slots") or []
    skin = next(item for item in payload.get("skins") or [] if item.get("name") == "default")
    skin_attachments = skin.get("attachments") or {}
    images = _image_candidates(package)
    atlas_regions = _parse_atlas(package.atlas_path.read_text(encoding="utf-8-sig"))
    with Image.open(package.texture_path) as loaded:
        atlas_page = loaded.convert("RGBA")
    cache: dict[str, Image.Image] = {}
    drawables = []
    all_points: list[tuple[float, float]] = []

    for slot in slots:
        attachment_name = slot.get("attachment")
        if not attachment_name:
            continue
        slot_name = str(slot["name"])
        attachment = (skin_attachments.get(slot_name) or {}).get(str(attachment_name))
        if not isinstance(attachment, dict):
            continue
        attachment_type = attachment.get("type", "region")
        if attachment_type not in {"region", "mesh"}:
            continue
        bone = worlds[bone_indices[str(slot["bone"])]]
        try:
            texture = _attachment_image(str(attachment_name), attachment, images, atlas_page, atlas_regions, cache)
        except KeyError:
            continue
        alpha = _rgba_alpha(slot.get("color")) * _rgba_alpha(attachment.get("color"))
        texture = _tinted_alpha(texture, alpha)
        if attachment_type == "region":
            vertices = _region_vertices(attachment, bone)
            triangles = [0, 1, 2, 2, 3, 0]
            uvs = [(0.0, 0.0), (texture.width, 0.0), (texture.width, texture.height), (0.0, texture.height)]
        else:
            vertices = _mesh_vertices(attachment, bone, worlds)
            triangles = [int(value) for value in attachment.get("triangles") or []]
            raw_uvs = attachment.get("uvs") or []
            uvs = [
                (float(raw_uvs[index]) * texture.width, (1.0 - float(raw_uvs[index + 1])) * texture.height)
                for index in range(0, len(raw_uvs), 2)
            ]
        if not vertices or len(vertices) != len(uvs):
            continue
        all_points.extend(vertices)
        drawables.append((texture, vertices, uvs, triangles))

    if not drawables or not all_points:
        raise RuntimeError("The Spine setup pose contains no renderable attachments.")
    min_x = min(point[0] for point in all_points)
    min_y = min(point[1] for point in all_points)
    max_x = max(point[0] for point in all_points)
    max_y = max(point[1] for point in all_points)
    padding = 4
    maximum_dimension = 4096
    span_x = max_x - min_x
    span_y = max_y - min_y
    if span_x > 0 or span_y > 0:
        pixels_per_spine_unit = min(
            pixels_per_spine_unit,
            (maximum_dimension - padding * 2) / max(span_x, span_y),
        )
    width = max(1, math.ceil((max_x - min_x) * pixels_per_spine_unit) + padding * 2)
    height = max(1, math.ceil((max_y - min_y) * pixels_per_spine_unit) + padding * 2)
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    def to_pixel(point: tuple[float, float]) -> tuple[float, float]:
        return (
            (point[0] - min_x) * pixels_per_spine_unit + padding,
            (max_y - point[1]) * pixels_per_spine_unit + padding,
        )

    for texture, vertices, uvs, triangles in drawables:
        pixel_vertices = [to_pixel(point) for point in vertices]
        for index in range(0, len(triangles), 3):
            indices = triangles[index : index + 3]
            if len(indices) < 3 or max(indices) >= len(pixel_vertices):
                continue
            _draw_triangle(
                canvas,
                texture,
                [pixel_vertices[item] for item in indices],
                [uvs[item] for item in indices],
            )
    return RenderedPose(canvas, min_x, min_y, max_x, max_y, pixels_per_spine_unit)
