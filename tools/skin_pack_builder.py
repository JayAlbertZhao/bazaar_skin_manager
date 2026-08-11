#!/usr/bin/env python3
"""Deterministically derive a validated skin pack from raster source art."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import deque
from pathlib import Path
from statistics import median

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from adapter_registry import AdapterRegistry, DEFAULT_ADAPTER_DIRECTORY
from mod_studio_core import StudioWorkspace, sha256_file


GENERATOR_ID = "deterministic-raster-v1"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALPHA_THRESHOLD = 8
SMALL_ICON_PRESETS = ("outline", "block-gaps", "silhouette")


def _distance(rgb: tuple[int, int, int], background: tuple[int, int, int]) -> float:
    return math.sqrt(sum((int(a) - int(b)) ** 2 for a, b in zip(rgb, background)))


def _border_coordinates(width: int, height: int) -> list[tuple[int, int]]:
    coordinates = {(x, 0) for x in range(width)}
    coordinates.update((x, height - 1) for x in range(width))
    coordinates.update((0, y) for y in range(height))
    coordinates.update((width - 1, y) for y in range(height))
    return sorted(coordinates)


def remove_edge_connected_background(
    source: Image.Image,
    *,
    tolerance: int = 34,
    feather: int = 30,
) -> Image.Image:
    """Remove only border-connected matte pixels and preserve enclosed whites.

    The background colour is estimated from the image perimeter. Flood filling
    is limited to pixels close to that matte, so similarly coloured details
    enclosed by an outline are not selected. Partially selected JPEG edge
    pixels are decontaminated from the estimated matte to avoid white halos.
    """
    if tolerance < 0 or feather < 0:
        raise ValueError("Background tolerance and feather must be non-negative.")
    image = source.convert("RGBA")
    width, height = image.size
    if width < 2 or height < 2:
        raise ValueError("Source image must be at least 2x2 pixels.")
    pixels = image.load()
    border = _border_coordinates(width, height)
    transparent_fraction = sum(pixels[x, y][3] <= ALPHA_THRESHOLD for x, y in border) / len(border)
    if transparent_fraction >= 0.5:
        return image

    background = tuple(
        int(round(median(pixels[x, y][channel] for x, y in border)))
        for channel in range(3)
    )
    outer = tolerance + feather
    selected = bytearray(width * height)
    queue: deque[tuple[int, int]] = deque()

    def selectable(x: int, y: int) -> bool:
        red, green, blue, _alpha = pixels[x, y]
        rgb = (red, green, blue)
        neutral_matte_edge = max(rgb) - min(rgb) <= 42 and max(rgb) >= 32
        return _distance(rgb, background) <= outer or neutral_matte_edge

    for x, y in border:
        offset = y * width + x
        if not selected[offset] and selectable(x, y):
            selected[offset] = 1
            queue.append((x, y))

    while queue:
        x, y = queue.popleft()
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if nx < 0 or ny < 0 or nx >= width or ny >= height:
                continue
            offset = ny * width + nx
            if selected[offset] or not selectable(nx, ny):
                continue
            selected[offset] = 1
            queue.append((nx, ny))

    output = image.copy()
    result = output.load()
    for y in range(height):
        for x in range(width):
            if not selected[y * width + x]:
                continue
            red, green, blue, original_alpha = pixels[x, y]
            distance = _distance((red, green, blue), background)
            neutral_matte_edge = max(red, green, blue) - min(red, green, blue) <= 42
            if neutral_matte_edge:
                opacity = max(
                    abs(value - matte) / max(matte, 255 - matte, 1)
                    for value, matte in zip((red, green, blue), background)
                )
            else:
                opacity = 0.0 if distance <= tolerance else min(1.0, (distance - tolerance) / max(feather, 1))
            alpha = int(round(original_alpha * opacity))
            if alpha <= 0:
                result[x, y] = (0, 0, 0, 0)
                continue
            if neutral_matte_edge:
                red, green, blue = 0, 0, 0
            elif opacity < 1.0:
                corrected = []
                for value, matte in zip((red, green, blue), background):
                    foreground = (value - (1.0 - opacity) * matte) / opacity
                    corrected.append(max(0, min(255, int(round(foreground)))))
                red, green, blue = corrected
            result[x, y] = (red, green, blue, alpha)
    return output


def alpha_bounds(image: Image.Image, threshold: int = ALPHA_THRESHOLD) -> tuple[int, int, int, int] | None:
    alpha = image.convert("RGBA").getchannel("A")
    mask = alpha.point(lambda value: 255 if value > threshold else 0)
    return mask.getbbox()


def fit_alpha_contain(
    source: Image.Image,
    *,
    size: tuple[int, int],
    target_bounds: tuple[int, int, int, int],
    anchor: tuple[float, float] = (0.5, 1.0),
    fit_reference: Image.Image | None = None,
) -> Image.Image:
    source = source.convert("RGBA")
    reference = source if fit_reference is None else fit_reference.convert("RGBA")
    if source.size != reference.size:
        raise ValueError("Alpha-contained layers and their fit reference must share a canvas.")
    bounds = alpha_bounds(reference)
    if bounds is None:
        raise ValueError("Foreground has no visible pixels after background removal.")
    left, top, right, bottom = target_bounds
    if not (
        left < right
        and top < bottom
        and right > 0
        and bottom > 0
        and left < size[0]
        and top < size[1]
    ):
        raise ValueError(f"Invalid target alpha bounds {target_bounds} for {size}.")
    cropped = source.crop(bounds)
    reference_crop = reference.crop(bounds)
    scale = min(
        (right - left) / reference_crop.width,
        (bottom - top) / reference_crop.height,
    )
    fitted_size = (
        max(1, int(round(reference_crop.width * scale))),
        max(1, int(round(reference_crop.height * scale))),
    )
    fitted = cropped.resize(fitted_size, Image.Resampling.LANCZOS)
    x = int(round(left + ((right - left) - fitted.width) * anchor[0]))
    y = int(round(top + ((bottom - top) - fitted.height) * anchor[1]))
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    canvas.alpha_composite(fitted, (x, y))
    return canvas


def alpha_contain_scale(
    reference: Image.Image,
    *,
    target_bounds: tuple[int, int, int, int],
) -> float:
    """Return the exact scale used by :func:`fit_alpha_contain`."""
    bounds = alpha_bounds(reference)
    if bounds is None:
        raise ValueError("Foreground has no visible pixels after background removal.")
    left, top, right, bottom = target_bounds
    source_width = bounds[2] - bounds[0]
    source_height = bounds[3] - bounds[1]
    return min((right - left) / source_width, (bottom - top) / source_height)


def scaled_target_bounds(
    bounds: tuple[int, int, int, int],
    factor: float,
    *,
    anchor: tuple[float, float] = (0.5, 1.0),
) -> tuple[int, int, int, int]:
    """Scale an authored placement rectangle around its declared anchor."""
    if not 0.25 <= float(factor) <= 3.0:
        raise ValueError("Character scale must be between 25% and 300%.")
    left, top, right, bottom = bounds
    width = right - left
    height = bottom - top
    anchor_x = left + width * float(anchor[0])
    anchor_y = top + height * float(anchor[1])
    scaled_width = width * float(factor)
    scaled_height = height * float(factor)
    return (
        round(anchor_x - scaled_width * float(anchor[0])),
        round(anchor_y - scaled_height * float(anchor[1])),
        round(anchor_x + scaled_width * (1.0 - float(anchor[0]))),
        round(anchor_y + scaled_height * (1.0 - float(anchor[1]))),
    )


def translate_rgba(source: Image.Image, offset: tuple[int, int]) -> Image.Image:
    """Translate an already-fitted layer without changing its output canvas."""
    image = source.convert("RGBA")
    output = Image.new("RGBA", image.size, (0, 0, 0, 0))
    output.alpha_composite(image, dest=(int(offset[0]), int(offset[1])))
    return output


def apply_declared_clip_mask(
    source: Image.Image,
    declaration: dict | None,
) -> Image.Image:
    """Apply an authored mask while retaining the square output canvas.

    Encounter portraits use the native frame's inner edge as three door
    panels: left, bottom, and right occlude the character, while the top stays
    open so hair, hats, and props may rise above the frame.
    """

    if not declaration:
        return source.convert("RGBA")
    if declaration.get("type") != "open_top_inner_frame":
        raise ValueError(f"Unsupported clip mask: {declaration.get('type')}")
    reference_size = tuple(
        int(value) for value in declaration.get("reference_size", source.size)
    )
    if len(reference_size) != 2 or min(reference_size) <= 0:
        raise ValueError("Clip-mask reference_size must contain two positive values.")
    bounds = tuple(int(value) for value in declaration.get("inner_bounds", ()))
    if len(bounds) != 4:
        raise ValueError("Open-top clip mask requires four inner_bounds values.")
    left, _top, right, bottom = bounds
    if not (0 <= left < right <= reference_size[0] and 0 < bottom <= reference_size[1]):
        raise ValueError("Open-top clip-mask inner_bounds are outside reference_size.")
    radius = int(declaration.get("bottom_corner_radius", 0))
    if radius < 0:
        raise ValueError("Clip-mask corner radius must be non-negative.")

    image = source.convert("RGBA")
    supersample = 4
    scale_x = image.width / reference_size[0]
    scale_y = image.height / reference_size[1]
    mask = Image.new(
        "L",
        (image.width * supersample, image.height * supersample),
        0,
    )
    ImageDraw.Draw(mask).rounded_rectangle(
        (
            round(left * scale_x * supersample),
            round(-2 * radius * scale_y * supersample),
            round(right * scale_x * supersample),
            round(bottom * scale_y * supersample),
        ),
        radius=round(radius * min(scale_x, scale_y) * supersample),
        fill=255,
    )
    mask = mask.resize(image.size, Image.Resampling.LANCZOS)
    image.putalpha(ImageChops.multiply(image.getchannel("A"), mask))
    return image


def _normalise_output_offsets(
    value: dict[str, tuple[int, int]] | None,
) -> dict[str, tuple[int, int]]:
    offsets: dict[str, tuple[int, int]] = {}
    for slot, offset in (value or {}).items():
        if len(offset) != 2:
            raise ValueError(f"Output offset for {slot} must contain X and Y.")
        pair = (int(offset[0]), int(offset[1]))
        if pair != (0, 0):
            offsets[str(slot)] = pair
    return offsets


def split_authored_underlay(
    source: Image.Image,
    declaration: dict,
) -> tuple[Image.Image, Image.Image, Image.Image]:
    """Split a manually lassoed underlay without inspecting pixel colours.

    The polygons are interpreted in the declaration's coordinate space and
    rasterized as a hard partition. Hard 0/255 membership keeps the source
    RGBA lossless when the parts are recombined with no intervening layer;
    antialiasing belongs to the source artwork, not to this mask.
    """
    source = source.convert("RGBA")
    coordinate_space = tuple(
        int(value) for value in declaration.get("coordinate_space", source.size)
    )
    if len(coordinate_space) != 2 or min(coordinate_space) <= 0:
        raise ValueError("Authored underlay coordinate_space must be [width, height].")
    polygons = declaration.get("polygons") or []
    if not polygons:
        raise ValueError("Authored underlay mask requires at least one polygon.")
    scale_x = source.width / coordinate_space[0]
    scale_y = source.height / coordinate_space[1]
    mask = Image.new("L", source.size, 0)
    draw = ImageDraw.Draw(mask)
    for polygon in polygons:
        if len(polygon) < 3:
            raise ValueError("Every authored underlay polygon needs at least 3 points.")
        draw.polygon(
            [
                (round(float(x) * scale_x), round(float(y) * scale_y))
                for x, y in polygon
            ],
            fill=255,
        )
    original_alpha = source.getchannel("A")
    underlay = source.copy()
    underlay.putalpha(ImageChops.multiply(original_alpha, mask))
    foreground = source.copy()
    foreground.putalpha(ImageChops.multiply(original_alpha, ImageChops.invert(mask)))
    return foreground, underlay, mask


def fit_cover(
    source: Image.Image,
    *,
    size: tuple[int, int],
    zoom: float = 1.0,
    offset: tuple[int, int] = (0, 0),
) -> Image.Image:
    """Resize and crop an image to cover a canvas deterministically.

    ``zoom`` is relative to the minimum cover scale. Positive offsets move the
    image right/down inside the crop. The crop origin is clamped so adjustments
    can never expose an empty strip beyond the background edge.
    """
    source = source.convert("RGBA")
    if zoom < 1.0:
        raise ValueError("Background zoom must be at least 100% of cover scale.")
    scale = max(size[0] / source.width, size[1] / source.height) * zoom
    resized = source.resize(
        (
            max(1, int(round(source.width * scale))),
            max(1, int(round(source.height * scale))),
        ),
        Image.Resampling.LANCZOS,
    )
    overflow_x = max(0, resized.width - size[0])
    overflow_y = max(0, resized.height - size[1])
    left = max(0, min(overflow_x, overflow_x // 2 - int(offset[0])))
    top = max(0, min(overflow_y, overflow_y // 2 - int(offset[1])))
    return resized.crop((left, top, left + size[0], top + size[1]))


def _outline_stencil_alpha(
    fitted: Image.Image,
    binary_alpha: Image.Image,
    *,
    gap_threshold: int,
    boundary_width: int,
    palette_colors: int,
) -> Image.Image:
    block_colours = (
        fitted.convert("RGB")
        .filter(ImageFilter.MedianFilter(3))
        .quantize(
            colors=palette_colors,
            method=Image.Quantize.MEDIANCUT,
            dither=Image.Dither.NONE,
        )
        .convert("RGB")
    )
    red, green, blue = block_colours.split()
    brightest_channel = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    gap_ink = brightest_channel.point(
        lambda value: 255 if value < gap_threshold else 0
    )
    if boundary_width:
        kernel_size = boundary_width * 2 + 1
        interior = binary_alpha.filter(ImageFilter.MinFilter(kernel_size))
    else:
        interior = binary_alpha
    internal_cutouts = ImageChops.multiply(gap_ink, interior)
    return ImageChops.subtract(binary_alpha, internal_cutouts).filter(
        ImageFilter.MedianFilter(5)
    )


def _block_gap_stencil_alpha(
    fitted: Image.Image,
    binary_alpha: Image.Image,
    *,
    palette_colors: int,
    palette_merge_distance: int,
    gap_width: int,
) -> Image.Image:
    opaque_rgb = Image.new("RGB", fitted.size, "white")
    opaque_rgb.paste(fitted.convert("RGB"), mask=binary_alpha)
    block_labels = opaque_rgb.filter(ImageFilter.MedianFilter(5)).quantize(
        colors=palette_colors,
        method=Image.Quantize.MEDIANCUT,
        dither=Image.Dither.NONE,
    )
    palette = block_labels.getpalette() or []
    used_indices = sorted(
        index
        for _count, index in (block_labels.getcolors(maxcolors=256) or [])
    )
    colours = {
        index: tuple(palette[index * 3 : index * 3 + 3])
        for index in used_indices
    }
    parent = {index: index for index in used_indices}

    def find_group(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for position, first in enumerate(used_indices):
        for second in used_indices[position + 1 :]:
            distance = math.sqrt(
                sum(
                    (colours[first][channel] - colours[second][channel]) ** 2
                    for channel in range(3)
                )
            )
            if distance > palette_merge_distance:
                continue
            first_root = find_group(first)
            second_root = find_group(second)
            if first_root != second_root:
                parent[second_root] = first_root

    group_lookup = [
        find_group(index) if index in parent else -1
        for index in range(256)
    ]
    kernel_size = gap_width * 2 + 1
    block_interiors = Image.new("L", fitted.size, 0)
    for group in sorted({find_group(index) for index in used_indices}):
        group_mask = block_labels.point(
            [255 if group_lookup[value] == group else 0 for value in range(256)],
            mode="L",
        )
        group_mask = ImageChops.multiply(group_mask, binary_alpha)
        block_interiors = ImageChops.lighter(
            block_interiors,
            group_mask.filter(ImageFilter.MinFilter(kernel_size)),
        )
    outer_boundary = ImageChops.subtract(
        binary_alpha,
        binary_alpha.filter(ImageFilter.MinFilter(kernel_size)),
    )
    return ImageChops.lighter(block_interiors, outer_boundary).filter(
        ImageFilter.MedianFilter(3)
    )


def derive_small_icon_binary(
    foreground: Image.Image,
    *,
    normalized_region: tuple[float, float, float, float],
    size: tuple[int, int] = (512, 512),
    padding_fraction: float = 0.05,
    preset: str = "outline",
    gap_threshold: int = 70,
    boundary_width: int = 4,
    palette_colors: int = 8,
    palette_merge_distance: int = 45,
    block_gap_width: int = 4,
) -> Image.Image:
    """Extract a configured region as a one-colour stencil icon.

    The normalized crop is an explicit geometric prior. Within that region,
    the final crop is the bounding box of the binary alpha mask. A selectable
    deterministic preset converts it to a white-on-transparent game icon; no
    model or semantic segmentation is involved.
    """
    if preset not in SMALL_ICON_PRESETS:
        raise ValueError(
            f"Unsupported small-icon preset: {preset}. "
            f"Expected one of {', '.join(SMALL_ICON_PRESETS)}."
        )
    if not 0 <= gap_threshold <= 255:
        raise ValueError("Icon gap threshold must be between 0 and 255.")
    if boundary_width < 0:
        raise ValueError("Icon boundary width must be non-negative.")
    if not 2 <= palette_colors <= 256:
        raise ValueError("Icon palette size must be between 2 and 256.")
    if palette_merge_distance < 0:
        raise ValueError("Icon palette merge distance must be non-negative.")
    if block_gap_width < 1:
        raise ValueError("Icon block gap width must be positive.")
    image = foreground.convert("RGBA")
    left, top, right, bottom = normalized_region
    if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
        raise ValueError(f"Invalid normalized icon region: {normalized_region}")
    crop_box = (
        int(round(left * image.width)),
        int(round(top * image.height)),
        int(round(right * image.width)),
        int(round(bottom * image.height)),
    )
    region = image.crop(crop_box)
    bounds = alpha_bounds(region)
    if bounds is None:
        raise ValueError("Configured small-icon region has no visible pixels.")
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    padding = int(round(max(width, height) * padding_fraction))
    padded = (
        max(0, bounds[0] - padding),
        max(0, bounds[1] - padding),
        min(region.width, bounds[2] + padding),
        min(region.height, bounds[3] + padding),
    )
    fitted = fit_alpha_contain(
        region.crop(padded),
        size=size,
        target_bounds=(0, 0, size[0], size[1]),
        anchor=(0.5, 0.5),
    )
    binary_alpha = fitted.getchannel("A").point(
        lambda value: 255 if value > ALPHA_THRESHOLD else 0
    )
    if preset == "outline":
        stencil_alpha = _outline_stencil_alpha(
            fitted,
            binary_alpha,
            gap_threshold=gap_threshold,
            boundary_width=boundary_width,
            palette_colors=palette_colors,
        )
    elif preset == "block-gaps":
        stencil_alpha = _block_gap_stencil_alpha(
            fitted,
            binary_alpha,
            palette_colors=palette_colors,
            palette_merge_distance=palette_merge_distance,
            gap_width=block_gap_width,
        )
    else:
        stencil_alpha = binary_alpha.filter(ImageFilter.MedianFilter(5))
    stencil = Image.new("RGBA", size, (255, 255, 255, 0))
    stencil.putalpha(stencil_alpha)
    return stencil


def derive_small_icon_file(
    character: Path,
    destination: Path,
    *,
    normalized_region: tuple[float, float, float, float],
    preset: str = "outline",
    tolerance: int = 34,
    feather: int = 90,
) -> Path:
    """Create the third pipeline input from a character image without AIGC."""
    with Image.open(character) as loaded:
        foreground = remove_edge_connected_background(
            loaded.convert("RGBA"),
            tolerance=tolerance,
            feather=feather,
        )
    icon = derive_small_icon_binary(
        foreground,
        normalized_region=normalized_region,
        preset=preset,
    )
    _save_png(icon, destination)
    return destination.resolve()


def image_metrics(image: Image.Image) -> dict:
    rgba = image.convert("RGBA")
    alpha = rgba.getchannel("A")
    flattened = getattr(alpha, "get_flattened_data", alpha.getdata)
    values = list(flattened())
    width, height = rgba.size
    border = _border_coordinates(width, height)
    pixels = rgba.load()
    return {
        "size": [width, height],
        "alpha_bounds": list(alpha_bounds(rgba) or []),
        "alpha_coverage": round(sum(value > ALPHA_THRESHOLD for value in values) / len(values), 8),
        "transparent_border": round(sum(pixels[x, y][3] <= ALPHA_THRESHOLD for x, y in border) / len(border), 8),
    }


def has_authored_transparency(image: Image.Image) -> bool:
    """Return whether a source actually contains transparency to preserve.

    Old authoring profiles labelled every user image as authoritative-alpha,
    including JPEGs and fully opaque PNGs. Treating that declaration as fact
    bypassed background removal and produced nearly opaque standing canvases.
    """
    minimum, _maximum = image.convert("RGBA").getchannel("A").getextrema()
    return minimum < 255


def _validate_metrics(slot: str, metrics: dict, output_recipe: dict) -> None:
    coverage = metrics["alpha_coverage"]
    if coverage < float(output_recipe.get("minimum_alpha_coverage", 0.0)):
        raise ValueError(f"{slot} alpha coverage is too low: {coverage:.4f}")
    if coverage > float(output_recipe.get("maximum_alpha_coverage", 1.0)):
        raise ValueError(f"{slot} alpha coverage is too high: {coverage:.4f}")
    transparent_border = metrics["transparent_border"]
    minimum_border = float(output_recipe.get("minimum_transparent_border", 0.0))
    if transparent_border < minimum_border:
        raise ValueError(
            f"{slot} transparent border is too small: {transparent_border:.4f}"
        )


def _save_png(image: Image.Image, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGBA").save(
        destination,
        "PNG",
        optimize=False,
        compress_level=9,
    )


def _load_badge_template(
    declaration: dict,
    *,
    template_root: Path,
) -> tuple[dict[str, Image.Image], dict]:
    """Load a locally extracted, non-AIGC badge template and verify its ledger.

    The adapter names a template directory but does not contain or pin game-art
    pixels. ``template.json`` is generated beside locally extracted layers and
    is the authority for their filenames and hashes. This keeps official art
    out of the public source tree while making every build reproducible.
    """
    directory_name = declaration.get("directory")
    if not isinstance(directory_name, str) or not directory_name.strip():
        raise ValueError("Badge template must declare a directory.")
    root = template_root.resolve()
    directory = (root / directory_name).resolve()
    try:
        directory.relative_to(root)
    except ValueError as error:
        raise ValueError(f"Badge template escapes its root: {directory}") from error

    ledger_path = directory / "template.json"
    if not ledger_path.is_file():
        raise FileNotFoundError(
            f"Badge template is not prepared: {ledger_path}. "
            "Extract it from an installed game with tools/badge_pipeline.py."
        )
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    if int(ledger.get("schema_version") or 0) != 2:
        raise ValueError("Unsupported badge template schema.")
    if not isinstance(ledger.get("aigc"), bool):
        raise ValueError("Badge template must explicitly declare whether it is AIGC.")
    if ledger.get("colour_inference") is not False:
        raise ValueError(
            "Badge template must explicitly disable colour-based mask inference."
        )
    expected_order = ["base", "frame_upper", "character", "frame_lower"]
    if ledger.get("layer_order_back_to_front") != expected_order:
        raise ValueError("Badge template has an invalid layer order.")

    outputs = ledger.get("outputs") or {}
    images: dict[str, Image.Image] = {}
    verified_outputs: dict[str, dict] = {}
    expected_size = tuple(int(value) for value in ledger.get("size") or [])
    if len(expected_size) != 2 or min(expected_size) <= 0:
        raise ValueError("Badge template has an invalid canvas size.")
    for layer_name in (
        "base",
        "frame_upper",
        "frame_lower",
        "frame_lower_occlusion",
    ):
        layer = outputs.get(layer_name) or {}
        filename = layer.get("file")
        expected_hash = layer.get("sha256")
        if not isinstance(filename, str) or not filename:
            raise ValueError(f"Badge template is missing {layer_name} filename.")
        path = (directory / filename).resolve()
        try:
            path.relative_to(directory)
        except ValueError as error:
            raise ValueError(f"Badge layer escapes its template: {path}") from error
        actual_hash = sha256_file(path)
        if not isinstance(expected_hash, str) or actual_hash != expected_hash:
            raise ValueError(f"Badge template hash mismatch: {layer_name}")
        with Image.open(path) as loaded:
            image = loaded.convert("RGBA")
        if image.size != expected_size:
            raise ValueError(
                f"Badge template layer {layer_name} is {image.size}; "
                f"expected {expected_size}."
            )
        images[layer_name] = image
        verified_outputs[layer_name] = {
            "file": filename,
            "sha256": actual_hash,
        }
    metadata = {
        "schema_version": 1,
        "method": ledger.get("method"),
        "aigc": ledger["aigc"],
        "colour_inference": False,
        "size": list(expected_size),
        "layer_order_back_to_front": expected_order,
        "outputs": verified_outputs,
        "ledger_sha256": sha256_file(ledger_path),
    }
    return images, metadata


def generate_pack(
    *,
    adapter_id: str,
    character: Path,
    background: Path | None,
    small_icon: Path | None,
    workspace_root: Path,
    output_zip: Path,
    pack_id: str,
    name: str,
    version: str,
    input_metadata: dict | None = None,
    supplemental_inputs: dict[str, Path] | None = None,
    adapter_directory: Path = DEFAULT_ADAPTER_DIRECTORY,
    badge_template_root: Path | None = None,
    character_canvas_offset: tuple[int, int] = (0, 0),
    character_scale: float = 1.0,
    background_offset: tuple[int, int] = (0, 0),
    background_scale: float = 1.0,
    output_offsets: dict[str, tuple[int, int]] | None = None,
    allow_partial: bool = False,
) -> dict:
    registry = AdapterRegistry.load(adapter_directory)
    adapter = registry.find_by_id(adapter_id)
    if adapter is None:
        raise ValueError(f"Unknown adapter: {adapter_id}")
    recipe = adapter.payload.get("authoring_recipe") or {}
    if recipe.get("id") != GENERATOR_ID or int(recipe.get("version") or 0) != 2:
        raise ValueError(f"Adapter {adapter_id} has no supported deterministic recipe.")

    input_paths = {"character": character.resolve()}
    for input_name, input_path in (
        ("background", background),
        ("small_icon", small_icon),
    ):
        if input_path is not None:
            input_paths[input_name] = input_path.resolve()
    input_specs = recipe.get("inputs") or {}
    for input_name, input_path in (supplemental_inputs or {}).items():
        if input_name in input_paths:
            raise ValueError(f"Supplemental input duplicates core input: {input_name}")
        if input_name not in input_specs:
            raise ValueError(
                f"Adapter {adapter_id} does not declare supplemental input: {input_name}"
            )
        input_paths[input_name] = input_path.resolve()
    missing_required_inputs = [
        input_name
        for input_name, specification in input_specs.items()
        if not specification.get("optional") and input_name not in input_paths
    ]
    if missing_required_inputs and not allow_partial:
        raise ValueError(
            "Missing required adapter input(s): " + ", ".join(missing_required_inputs)
        )
    loaded_inputs: dict[str, Image.Image] = {}
    for input_name, input_path in input_paths.items():
        if not input_path.is_file():
            raise FileNotFoundError(input_path)
        accepted = {
            str(value).casefold()
            for value in ((recipe.get("inputs") or {}).get(input_name) or {}).get(
                "accepted_extensions", []
            )
        }
        if accepted and input_path.suffix.casefold() not in accepted:
            raise ValueError(
                f"Unsupported {input_name} image type: {input_path.suffix}"
            )
        with Image.open(input_path) as loaded:
            loaded_inputs[input_name] = loaded.convert("RGBA")

    input_metadata = dict(input_metadata or {})
    for input_name in input_paths:
        metadata = dict(input_metadata.get(input_name) or {})
        if metadata.get("aigc") is True:
            raise ValueError(f"AIGC input is forbidden by this authoring recipe: {input_name}")
        metadata["aigc"] = False
        input_metadata[input_name] = metadata

    character_metadata = input_metadata.get("character") or {}
    authoritative_alpha = bool(character_metadata.get("authoritative_alpha"))
    removal = (recipe.get("foreground") or {}).get("remove_background") or {}
    if removal.get("method") != "edge_connected":
        raise ValueError("Only edge_connected background removal is supported.")
    if authoritative_alpha:
        foreground = loaded_inputs["character"].copy()
    else:
        foreground = remove_edge_connected_background(
            loaded_inputs["character"],
            tolerance=int(removal.get("tolerance", 34)),
            feather=int(removal.get("feather", 30)),
        )
    transparency_declaration = None if authoritative_alpha else (
        (recipe.get("foreground") or {}).get("transparent_lassos")
    )
    authored_transparency_mask = None
    if transparency_declaration:
        foreground, _discarded, authored_transparency_mask = split_authored_underlay(
            foreground,
            transparency_declaration,
        )
    # The source depicts the character leaning against a room corner. Its dark
    # cast shadow is background underlay, not character anatomy. Keep the
    # original full silhouette as the shared fit reference, then split the
    # explicitly traced shadow before rendering any output.
    foreground_fit_reference = foreground.copy()
    cast_shadow_declaration = None if authoritative_alpha else (
        (recipe.get("foreground") or {}).get("cast_shadow_lasso")
    )
    character_shadow = (
        Image.new("RGBA", foreground.size, (0, 0, 0, 0))
        if authoritative_alpha
        else None
    )
    cast_shadow_mask = None
    if cast_shadow_declaration:
        foreground, character_shadow, cast_shadow_mask = split_authored_underlay(
            foreground,
            cast_shadow_declaration,
        )

    target = adapter.payload["target"]
    workspace = StudioWorkspace.create(
        pack_id,
        root=workspace_root,
        name=name,
        version=version,
        hero=adapter.hero,
        skin=adapter.skin,
        skin_name_contains=adapter.skin_name_contains,
    )
    workspace.state["visual_slots"] = {}
    archived_inputs: dict[str, Path] = {}
    for input_name, input_path in input_paths.items():
        archived_input = (
            workspace.directory
            / "authoring"
            / "inputs"
            / f"{input_name}{input_path.suffix.casefold()}"
        )
        archived_input.parent.mkdir(parents=True, exist_ok=True)
        if archived_input.resolve() != input_path:
            archived_input.write_bytes(input_path.read_bytes())
        archived_inputs[input_name] = archived_input
    outputs: dict[str, Image.Image] = {}
    output_metadata: dict[str, dict] = {}
    skipped_outputs: dict[str, list[str]] = {}
    output_recipes = recipe.get("outputs") or {}
    character_canvas_offset = (
        int(character_canvas_offset[0]),
        int(character_canvas_offset[1]),
    )
    character_scale = float(character_scale)
    if not 0.25 <= character_scale <= 3.0:
        raise ValueError("Character scale must be between 25% and 300%.")
    background_offset = (int(background_offset[0]), int(background_offset[1]))
    background_scale = float(background_scale)
    if not 1.0 <= background_scale <= 3.0:
        raise ValueError("Background scale must be between 100% and 300%.")
    requested_output_offsets = _normalise_output_offsets(output_offsets)
    unknown_adjustments = set(requested_output_offsets) - set(output_recipes)
    if unknown_adjustments:
        raise ValueError(
            "Output adjustment references unknown slot(s): "
            + ", ".join(sorted(unknown_adjustments))
        )

    def canonical_slot(slot_name: str) -> str:
        current = slot_name
        visited = {slot_name}
        while output_recipes[current].get("alias_of"):
            current = str(output_recipes[current]["alias_of"])
            if current in visited or current not in output_recipes:
                raise ValueError(f"Invalid output alias chain for {slot_name}.")
            visited.add(current)
        return current

    # Alias slots backed by one native texture are one generated asset family.
    # Manager validation intentionally rejects different pixels for that same
    # target, so a local adjustment made through any alias is canonicalized to
    # the shared recipe and inherited by every consumer of that asset.
    output_offsets: dict[str, tuple[int, int]] = {}
    for requested_slot, offset in requested_output_offsets.items():
        canonical = canonical_slot(requested_slot)
        existing = output_offsets.get(canonical)
        if existing is not None and existing != offset:
            raise ValueError(
                f"Conflicting output adjustments share native asset {canonical}."
            )
        output_offsets[canonical] = offset

    for slot, output_recipe in output_recipes.items():
        alias = output_recipe.get("alias_of")
        if alias:
            continue
        unavailable_dependencies = [
            dependency
            for dependency in output_recipe.get("depends_on") or []
            if dependency != "badge_template"
            and dependency not in input_paths
            and not (input_specs.get(dependency) or {}).get("optional")
        ]
        if unavailable_dependencies:
            if not allow_partial:
                raise ValueError(
                    f"{slot} is missing input dependencies: "
                    + ", ".join(unavailable_dependencies)
                )
            skipped_outputs[slot] = unavailable_dependencies
            continue
        local_offset = output_offsets.get(slot, (0, 0))
        renderer = output_recipe.get("renderer", "layers")
        if renderer == "layered_badge":
            from badge_pipeline import compose_badge

            template_images, template_metadata = _load_badge_template(
                output_recipe["template"],
                template_root=(badge_template_root or PROJECT_ROOT / "manager" / "assets"),
            )

            crop = output_recipe.get("character_crop", [0.0, 0.0, 1.0, 1.0])
            crop_box = (
                round(float(crop[0]) * foreground.width),
                round(float(crop[1]) * foreground.height),
                round(float(crop[2]) * foreground.width),
                round(float(crop[3]) * foreground.height),
            )
            cropped_foreground = foreground.crop(crop_box)
            fit_reference = foreground_fit_reference.crop(crop_box)
            shadow = (
                None if character_shadow is None else character_shadow.crop(crop_box)
            )
            anchor = tuple(
                float(value) for value in output_recipe.get("anchor", [0.5, 1.0])
            )
            target_bounds = scaled_target_bounds(
                tuple(int(value) for value in output_recipe["target_alpha_bounds"]),
                character_scale,
                anchor=anchor,
            )
            scale = alpha_contain_scale(fit_reference, target_bounds=target_bounds)
            badge_canvas_size = template_images["base"].size
            output_size = tuple(int(value) for value in output_recipe["size"])
            local_template_offset = (
                round(local_offset[0] * badge_canvas_size[0] / output_size[0]),
                round(local_offset[1] * badge_canvas_size[1] / output_size[1]),
            )
            character_template_offset = (
                round(character_canvas_offset[0] * scale) + local_template_offset[0],
                round(character_canvas_offset[1] * scale) + local_template_offset[1],
            )
            target_bounds = (
                target_bounds[0] + character_template_offset[0],
                target_bounds[1] + character_template_offset[1],
                target_bounds[2] + character_template_offset[0],
                target_bounds[3] + character_template_offset[1],
            )
            rendered = compose_badge(
                cropped_foreground,
                shadow=shadow,
                fit_reference=fit_reference,
                base=template_images["base"],
                frame_upper=template_images["frame_upper"],
                frame_lower=template_images["frame_lower"],
                frame_lower_occlusion=template_images["frame_lower_occlusion"],
                target_bounds=target_bounds,
                output_size=output_size,
            )
            metrics = image_metrics(rendered)
            _validate_metrics(slot, metrics, output_recipe)
            metrics["depends_on"] = list(
                output_recipe.get("depends_on") or []
            )
            metrics["layers"] = list(output_recipe.get("layers") or [])
            metrics["template"] = template_metadata
            metrics["character_crop"] = list(crop)
            if character_scale != 1.0 or character_canvas_offset != (0, 0) or local_offset != (0, 0):
                metrics["adjustment"] = {
                    "character_canvas": list(character_canvas_offset),
                    "local_output": list(local_offset),
                    "effective_output": [
                        round(character_template_offset[0] * output_size[0] / badge_canvas_size[0]),
                        round(character_template_offset[1] * output_size[1] / badge_canvas_size[1]),
                    ],
                }
                if character_scale != 1.0:
                    metrics["adjustment"]["character_scale"] = character_scale
            if cast_shadow_declaration:
                metrics["cast_shadow_lasso"] = {
                    "method": "authored-coordinate-lasso",
                    "coordinate_space": list(
                        cast_shadow_declaration["coordinate_space"]
                    ),
                    "polygons": list(cast_shadow_declaration["polygons"]),
                    "merged_into": "badge_template.base",
                    "selected_pixels": (
                        cast_shadow_mask.width * cast_shadow_mask.height
                        - cast_shadow_mask.histogram()[0]
                    ),
                }
            outputs[slot] = rendered
            output_metadata[slot] = metrics
            continue
        if renderer != "layers":
            raise ValueError(f"Unsupported renderer for {slot}: {renderer}")
        size = tuple(int(value) for value in output_recipe["size"])
        layers = output_recipe.get("layers") or []
        dependencies = list(output_recipe.get("depends_on") or [])
        active_layers = []
        for declared_layer in layers:
            if (
                declared_layer.get("optional")
                and declared_layer.get("input") not in loaded_inputs
            ):
                continue
            layer = dict(declared_layer)
            conditional_overrides = layer.pop("when_input_present", {})
            for condition_input, overrides in conditional_overrides.items():
                if condition_input in loaded_inputs:
                    layer.update(overrides)
            active_layers.append(layer)
        active_dependencies = [
            dependency
            for dependency in dependencies
            if dependency in input_paths or dependency == "badge_template"
        ]
        declared_layer_dependencies = {
            "character" if layer.get("input") == "character_shadow" else layer.get("input")
            for layer in active_layers
        }
        if not active_dependencies or declared_layer_dependencies != set(active_dependencies):
            raise ValueError(f"{slot} must declare exact input dependencies and layers.")
        rendered = Image.new("RGBA", size, (0, 0, 0, 0))
        effective_character_offset = (0, 0)
        for layer in active_layers:
            input_name = layer["input"]
            fit = layer.get("fit")
            if input_name == "character":
                layer_source = foreground
                fit_reference = foreground_fit_reference
            elif input_name == "character_shadow":
                if character_shadow is None:
                    # Generic hero recipes inherit the Dooley layer stack, but
                    # most heroes deliberately clear Dooley's authored shadow
                    # lasso.  In that case the shadow layer is an optional
                    # transparent no-op, matching LivePreviewRenderer.
                    continue
                layer_source = character_shadow
                fit_reference = foreground_fit_reference
            else:
                layer_source = loaded_inputs[input_name]
                fit_reference = None
            if layer.get("flip_x"):
                layer_source = layer_source.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
                if fit_reference is not None:
                    fit_reference = fit_reference.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            if fit == "cover":
                effective_background_offset = (
                    round(background_offset[0] * size[0] / 1024),
                    round(background_offset[1] * size[1] / 1024),
                )
                fitted = fit_cover(
                    layer_source,
                    size=size,
                    zoom=background_scale if input_name == "background" else 1.0,
                    offset=(
                        effective_background_offset
                        if input_name == "background"
                        else (0, 0)
                    ),
                )
            elif fit == "alpha_contain":
                target_bounds = tuple(
                    int(value)
                    for value in layer.get(
                        "target_alpha_bounds",
                        output_recipe["target_alpha_bounds"],
                    )
                )
                anchor = tuple(
                    float(value)
                    for value in layer.get(
                        "anchor",
                        output_recipe.get("anchor", [0.5, 1.0]),
                    )
                )
                if input_name in {"character", "character_shadow"}:
                    target_bounds = scaled_target_bounds(
                        target_bounds,
                        character_scale,
                        anchor=anchor,
                    )
                fitted = fit_alpha_contain(
                    layer_source,
                    size=size,
                    target_bounds=target_bounds,
                    anchor=anchor,
                    fit_reference=fit_reference,
                )
            else:
                raise ValueError(f"Unsupported fit mode for {slot}: {fit}")
            if input_name in {"character", "character_shadow"}:
                scale = alpha_contain_scale(
                    fit_reference if fit_reference is not None else layer_source,
                    target_bounds=target_bounds,
                )
                effective_character_offset = (
                    round(character_canvas_offset[0] * scale) + local_offset[0],
                    round(character_canvas_offset[1] * scale) + local_offset[1],
                )
                fitted = translate_rgba(fitted, effective_character_offset)
            elif input_name == "small_icon" and local_offset != (0, 0):
                fitted = translate_rgba(fitted, local_offset)
            fitted = apply_declared_clip_mask(fitted, layer.get("clip_mask"))
            rendered.alpha_composite(fitted)
        metrics = image_metrics(rendered)
        _validate_metrics(slot, metrics, output_recipe)
        metrics["depends_on"] = active_dependencies
        metrics["layers"] = active_layers
        if "background" in active_dependencies and (
            background_scale != 1.0 or background_offset != (0, 0)
        ):
            metrics["background_adjustment"] = {
                "reference_offset": list(background_offset),
                "reference_size": [1024, 1024],
                "effective_offset": [
                    round(background_offset[0] * size[0] / 1024),
                    round(background_offset[1] * size[1] / 1024),
                ],
                "scale": background_scale,
                "fit": "cover",
                "empty_edges": "clamped",
            }
        if (
            "character" in active_dependencies
            and (character_scale != 1.0 or character_canvas_offset != (0, 0))
        ) or local_offset != (0, 0):
            metrics["adjustment"] = {
                "character_canvas": list(character_canvas_offset),
                "local_output": list(local_offset),
                "effective_output": list(
                    effective_character_offset
                    if "character" in active_dependencies
                    else local_offset
                ),
            }
            if character_scale != 1.0:
                metrics["adjustment"]["character_scale"] = character_scale
        outputs[slot] = rendered
        output_metadata[slot] = metrics

    for slot, output_recipe in output_recipes.items():
        alias = output_recipe.get("alias_of")
        if alias and slot not in outputs:
            if alias not in outputs:
                if allow_partial and alias in skipped_outputs:
                    skipped_outputs[slot] = list(skipped_outputs[alias])
                    continue
                raise ValueError(f"Alias {slot} references missing output {alias}.")
            outputs[slot] = outputs[alias].copy()
            output_metadata[slot] = dict(
                output_metadata[alias],
                alias_of=alias,
                depends_on=list(output_recipe.get("depends_on") or []),
            )

    for slot, rendered in sorted(outputs.items()):
        destination = workspace.directory / "assets" / f"{slot}.png"
        _save_png(rendered, destination)
        workspace.state["visual_slots"][slot] = destination.relative_to(workspace.directory).as_posix()
        output_metadata[slot]["sha256"] = sha256_file(destination)
        output_metadata[slot]["bytes"] = destination.stat().st_size

    badge_template_aigc = any(
        bool((metadata.get("template") or {}).get("aigc"))
        for metadata in output_metadata.values()
    )
    workspace.state["authoring"] = {
        "generator": {
            "id": recipe["id"],
            "version": recipe["version"],
            "adapter_id": adapter.adapter_id,
            "adapter_version": adapter.adapter_version,
        },
        "asset_policy": {
            "aigc_allowed": badge_template_aigc,
            "declaration": (
                "Creator inputs are non-AIGC. The local completed badge template "
                + (
                    "contains a declared ImageGen reconstruction."
                    if badge_template_aigc
                    else "contains no AIGC pixels."
                )
            ),
        },
        "inputs": {
            input_name: {
                "sha256": sha256_file(input_path),
                "bytes": input_path.stat().st_size,
                "image_size": list(loaded_inputs[input_name].size),
                "workspace_file": archived_inputs[input_name]
                .relative_to(workspace.directory)
                .as_posix(),
                **input_metadata[input_name],
            }
            for input_name, input_path in input_paths.items()
        },
        "foreground": image_metrics(foreground),
        "outputs": output_metadata,
        "skipped_outputs": skipped_outputs,
    }
    if (
        character_scale != 1.0
        or character_canvas_offset != (0, 0)
        or background_scale != 1.0
        or background_offset != (0, 0)
        or output_offsets
    ):
        workspace.state["authoring"]["adjustments"] = {
            "character_canvas": list(character_canvas_offset),
            "per_output": {
                slot: list(offset)
                for slot, offset in sorted(requested_output_offsets.items())
            },
            "canonical_assets": {
                slot: canonical_slot(slot)
                for slot in sorted(requested_output_offsets)
            },
        }
        if character_scale != 1.0:
            workspace.state["authoring"]["adjustments"]["character_scale"] = character_scale
        if background_scale != 1.0 or background_offset != (0, 0):
            workspace.state["authoring"]["adjustments"]["background"] = {
                "offset": list(background_offset),
                "scale": background_scale,
                "fit": "cover",
            }
    if transparency_declaration:
        workspace.state["authoring"]["foreground"]["authored_transparency"] = {
            "method": "authored-coordinate-lasso",
            "coordinate_space": list(transparency_declaration["coordinate_space"]),
            "polygons": list(transparency_declaration["polygons"]),
            "selected_pixels": (
                authored_transparency_mask.width * authored_transparency_mask.height
                - authored_transparency_mask.histogram()[0]
            ),
        }
    if authoritative_alpha:
        workspace.state["authoring"]["foreground"]["authoritative_alpha"] = {
            "declared": True,
            "method": character_metadata.get(
                "alpha_method",
                "pack-author-supplied alpha is used verbatim",
            ),
            "background_removal": False,
            "authored_lasso_postprocessing": False,
        }
    if cast_shadow_declaration:
        workspace.state["authoring"]["foreground"]["cast_shadow"] = {
            "method": "authored-coordinate-lasso",
            "semantic": "character cast shadow on the room-corner background",
            "coordinate_space": list(cast_shadow_declaration["coordinate_space"]),
            "polygons": list(cast_shadow_declaration["polygons"]),
            "selected_pixels": (
                cast_shadow_mask.width * cast_shadow_mask.height
                - cast_shadow_mask.histogram()[0]
            ),
            "composition": "background -> character_shadow -> character",
        }
    workspace.save()
    errors = workspace.validation_errors()
    if errors:
        raise ValueError("Generated pack failed validation: " + "; ".join(errors))
    output_zip = workspace.export_zip(output_zip)
    return {
        "pack_id": workspace.state["pack"]["id"],
        "adapter_id": adapter.adapter_id,
        "target": target,
        "workspace": str(workspace.directory),
        "zip": str(output_zip),
        "zip_sha256": sha256_file(output_zip),
        "outputs": output_metadata,
        "skipped_outputs": skipped_outputs,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter", required=True)
    parser.add_argument("--character", required=True, type=Path)
    parser.add_argument("--background", required=True, type=Path)
    icon_group = parser.add_mutually_exclusive_group(required=True)
    icon_group.add_argument("--small-icon", type=Path)
    icon_group.add_argument("--derive-small-icon-output", type=Path)
    parser.add_argument(
        "--small-icon-region",
        nargs=4,
        type=float,
        metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"),
        default=(0.20, 0.07, 0.94, 0.76),
    )
    parser.add_argument(
        "--small-icon-preset",
        choices=SMALL_ICON_PRESETS,
        default="outline",
    )
    parser.add_argument("--input-metadata", type=Path)
    parser.add_argument(
        "--supplemental-input",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="Optional adapter-declared input. Repeat for multiple supplemental layers.",
    )
    parser.add_argument(
        "--badge-template-root",
        type=Path,
        default=PROJECT_ROOT / "manager" / "assets",
        help="Root containing locally extracted badge-templates/ (official art is not bundled).",
    )
    parser.add_argument("--workspace-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--pack-id", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--version", default="0.1.0")
    args = parser.parse_args()
    input_metadata = None
    if args.input_metadata:
        input_metadata = json.loads(args.input_metadata.read_text(encoding="utf-8"))
    supplemental_inputs: dict[str, Path] = {}
    for declaration in args.supplemental_input:
        input_name, separator, input_path = declaration.partition("=")
        if not separator or not input_name.strip() or not input_path.strip():
            parser.error("--supplemental-input must use NAME=PATH")
        input_name = input_name.strip()
        if input_name in supplemental_inputs:
            parser.error(f"duplicate --supplemental-input name: {input_name}")
        supplemental_inputs[input_name] = Path(input_path.strip())
    small_icon = args.small_icon
    if args.derive_small_icon_output:
        small_icon = derive_small_icon_file(
            args.character,
            args.derive_small_icon_output,
            normalized_region=tuple(args.small_icon_region),
            preset=args.small_icon_preset,
        )
        input_metadata = dict(input_metadata or {})
        small_icon_metadata = dict(input_metadata.get("small_icon") or {})
        small_icon_metadata["preset"] = args.small_icon_preset
        input_metadata["small_icon"] = small_icon_metadata
    result = generate_pack(
        adapter_id=args.adapter,
        character=args.character,
        background=args.background,
        small_icon=small_icon,
        workspace_root=args.workspace_root,
        output_zip=args.output,
        pack_id=args.pack_id,
        name=args.name,
        version=args.version,
        input_metadata=input_metadata,
        supplemental_inputs=supplemental_inputs,
        badge_template_root=args.badge_template_root,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
