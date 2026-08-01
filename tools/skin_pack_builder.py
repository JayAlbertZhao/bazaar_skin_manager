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

from PIL import Image

from adapter_registry import AdapterRegistry, DEFAULT_ADAPTER_DIRECTORY
from mod_studio_core import StudioWorkspace, sha256_file


GENERATOR_ID = "deterministic-raster-v1"
ALPHA_THRESHOLD = 8


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
    workspace_root: Path,
    output_zip: Path,
    pack_id: str,
    name: str,
    version: str,
    adapter_directory: Path = DEFAULT_ADAPTER_DIRECTORY,
) -> dict:
    registry = AdapterRegistry.load(adapter_directory)
    adapter = registry.find_by_id(adapter_id)
    if adapter is None:
        raise ValueError(f"Unknown adapter: {adapter_id}")
    recipe = adapter.payload.get("authoring_recipe") or {}
    if recipe.get("id") != GENERATOR_ID or int(recipe.get("version") or 0) != 1:
        raise ValueError(f"Adapter {adapter_id} has no supported deterministic recipe.")

    character = character.resolve()
    if not character.is_file():
        raise FileNotFoundError(character)
    accepted = {
        str(value).casefold()
        for value in ((recipe.get("inputs") or {}).get("character") or {}).get("accepted_extensions", [])
    }
    if accepted and character.suffix.casefold() not in accepted:
        raise ValueError(f"Unsupported character image type: {character.suffix}")
    with Image.open(character) as loaded:
        original = loaded.convert("RGBA")

    removal = (recipe.get("foreground") or {}).get("remove_background") or {}
    if removal.get("method") != "edge_connected":
        raise ValueError("Only edge_connected background removal is supported.")
    foreground = remove_edge_connected_background(
        original,
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
    archived_input = (
        workspace.directory
        / "authoring"
        / "inputs"
        / f"character{character.suffix.casefold()}"
    )
    archived_input.parent.mkdir(parents=True, exist_ok=True)
    archived_input.write_bytes(character.read_bytes())
    outputs: dict[str, Image.Image] = {}
    output_metadata: dict[str, dict] = {}
    output_recipes = recipe.get("outputs") or {}
    for slot, output_recipe in output_recipes.items():
        alias = output_recipe.get("alias_of")
        if alias:
            continue
        size = tuple(int(value) for value in output_recipe["size"])
        fit = output_recipe.get("fit")
        if fit != "alpha_contain":
            raise ValueError(f"Unsupported fit mode for {slot}: {fit}")
        rendered = fit_alpha_contain(
            foreground,
            size=size,
            target_bounds=tuple(int(value) for value in output_recipe["target_alpha_bounds"]),
            anchor=tuple(float(value) for value in output_recipe.get("anchor", [0.5, 1.0])),
        )
        metrics = image_metrics(rendered)
        _validate_metrics(slot, metrics, output_recipe)
        outputs[slot] = rendered
        output_metadata[slot] = metrics

    for slot, output_recipe in output_recipes.items():
        alias = output_recipe.get("alias_of")
        if alias:
            if alias not in outputs:
                raise ValueError(f"Alias {slot} references missing output {alias}.")
            outputs[slot] = outputs[alias].copy()
            output_metadata[slot] = dict(output_metadata[alias], alias_of=alias)

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
        "inputs": {
            "character": {
                "sha256": sha256_file(character),
                "bytes": character.stat().st_size,
                "image_size": list(original.size),
                "workspace_file": archived_input.relative_to(workspace.directory).as_posix(),
            }
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
    parser.add_argument("--workspace-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--pack-id", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--version", default="0.1.0")
    args = parser.parse_args()
    result = generate_pack(
        adapter_id=args.adapter,
        character=args.character,
        workspace_root=args.workspace_root,
        output_zip=args.output,
        pack_id=args.pack_id,
        name=args.name,
        version=args.version,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
