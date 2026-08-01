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

from PIL import Image, ImageChops, ImageFilter

from adapter_registry import AdapterRegistry, DEFAULT_ADAPTER_DIRECTORY
from mod_studio_core import StudioWorkspace, sha256_file


GENERATOR_ID = "deterministic-raster-v1"
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
) -> Image.Image:
    source = source.convert("RGBA")
    bounds = alpha_bounds(source)
    if bounds is None:
        raise ValueError("Foreground has no visible pixels after background removal.")
    left, top, right, bottom = target_bounds
    if not (0 <= left < right <= size[0] and 0 <= top < bottom <= size[1]):
        raise ValueError(f"Invalid target alpha bounds {target_bounds} for {size}.")
    cropped = source.crop(bounds)
    scale = min((right - left) / cropped.width, (bottom - top) / cropped.height)
    fitted_size = (
        max(1, int(round(cropped.width * scale))),
        max(1, int(round(cropped.height * scale))),
    )
    fitted = cropped.resize(fitted_size, Image.Resampling.LANCZOS)
    x = int(round(left + ((right - left) - fitted.width) * anchor[0]))
    y = int(round(top + ((bottom - top) - fitted.height) * anchor[1]))
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    canvas.alpha_composite(fitted, (x, y))
    return canvas


def fit_cover(source: Image.Image, *, size: tuple[int, int]) -> Image.Image:
    """Resize and center-crop an image to cover a canvas deterministically."""
    source = source.convert("RGBA")
    scale = max(size[0] / source.width, size[1] / source.height)
    resized = source.resize(
        (
            max(1, int(round(source.width * scale))),
            max(1, int(round(source.height * scale))),
        ),
        Image.Resampling.LANCZOS,
    )
    left = (resized.width - size[0]) // 2
    top = (resized.height - size[1]) // 2
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


def generate_pack(
    *,
    adapter_id: str,
    character: Path,
    background: Path,
    small_icon: Path,
    workspace_root: Path,
    output_zip: Path,
    pack_id: str,
    name: str,
    version: str,
    input_metadata: dict | None = None,
    adapter_directory: Path = DEFAULT_ADAPTER_DIRECTORY,
) -> dict:
    registry = AdapterRegistry.load(adapter_directory)
    adapter = registry.find_by_id(adapter_id)
    if adapter is None:
        raise ValueError(f"Unknown adapter: {adapter_id}")
    recipe = adapter.payload.get("authoring_recipe") or {}
    if recipe.get("id") != GENERATOR_ID or int(recipe.get("version") or 0) != 2:
        raise ValueError(f"Adapter {adapter_id} has no supported deterministic recipe.")

    input_paths = {
        "character": character.resolve(),
        "background": background.resolve(),
        "small_icon": small_icon.resolve(),
    }
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

    removal = (recipe.get("foreground") or {}).get("remove_background") or {}
    if removal.get("method") != "edge_connected":
        raise ValueError("Only edge_connected background removal is supported.")
    foreground = remove_edge_connected_background(
        loaded_inputs["character"],
        tolerance=int(removal.get("tolerance", 34)),
        feather=int(removal.get("feather", 30)),
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
    output_recipes = recipe.get("outputs") or {}
    for slot, output_recipe in output_recipes.items():
        alias = output_recipe.get("alias_of")
        if alias:
            continue
        size = tuple(int(value) for value in output_recipe["size"])
        layers = output_recipe.get("layers") or []
        dependencies = list(output_recipe.get("depends_on") or [])
        if not dependencies or {layer.get("input") for layer in layers} != set(dependencies):
            raise ValueError(f"{slot} must declare exact input dependencies and layers.")
        rendered = Image.new("RGBA", size, (0, 0, 0, 0))
        for layer in layers:
            input_name = layer["input"]
            fit = layer.get("fit")
            if input_name == "character":
                layer_source = foreground
            else:
                layer_source = loaded_inputs[input_name]
            if fit == "cover":
                fitted = fit_cover(layer_source, size=size)
            elif fit == "alpha_contain":
                fitted = fit_alpha_contain(
                    layer_source,
                    size=size,
                    target_bounds=tuple(
                        int(value) for value in output_recipe["target_alpha_bounds"]
                    ),
                    anchor=tuple(
                        float(value)
                        for value in output_recipe.get("anchor", [0.5, 1.0])
                    ),
                )
            else:
                raise ValueError(f"Unsupported fit mode for {slot}: {fit}")
            rendered.alpha_composite(fitted)
        metrics = image_metrics(rendered)
        _validate_metrics(slot, metrics, output_recipe)
        metrics["depends_on"] = dependencies
        metrics["layers"] = layers
        outputs[slot] = rendered
        output_metadata[slot] = metrics

    for slot, output_recipe in output_recipes.items():
        alias = output_recipe.get("alias_of")
        if alias:
            if alias not in outputs:
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

    workspace.state["authoring"] = {
        "generator": {
            "id": recipe["id"],
            "version": recipe["version"],
            "adapter_id": adapter.adapter_id,
            "adapter_version": adapter.adapter_version,
        },
        "asset_policy": {
            "aigc_allowed": False,
            "declaration": "All three inputs are non-AIGC assets or deterministic derivatives.",
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
    parser.add_argument("--workspace-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--pack-id", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--version", default="0.1.0")
    args = parser.parse_args()
    input_metadata = None
    if args.input_metadata:
        input_metadata = json.loads(args.input_metadata.read_text(encoding="utf-8"))
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
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
