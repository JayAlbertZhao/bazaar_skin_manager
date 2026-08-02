#!/usr/bin/env python3
"""Build and compose faithful layered hero-select badge assets.

The template extractor aligns the official profession hero buttons, finds the
maximum connected region whose RGBA pixels recur exactly in at least two
buttons, restores disconnected exterior antialias/shadow pixels from that
same exact-pixel consensus, and geometrically closes only tiny gaps. It never
classifies a pixel by colour. The completed empty badge is split into the
four-layer contract used by the builder:

    base -> upper frame -> clipped character -> lower frame

The lower frame begins at the two lower corners, remains in front of the
character, and carries a geometric knockout mask for its visually transparent
outside area. All generated character pixels come from the supplied cutout;
the compositor does not synthesize or repaint artwork.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, deque
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter


REFERENCE_SIZE = (512, 512)
GAME_BADGE_REFERENCES = (
    {"asset_name": "Jules", "bundle_suffix": "jules"},
    {"asset_name": "Mak", "bundle_suffix": "mak"},
    {"asset_name": "Pygmalien", "bundle_suffix": "pygmalien"},
    {"asset_name": "Stelle", "bundle_suffix": "stelle"},
    {"asset_name": "Vanessa", "bundle_suffix": "vanessa"},
)
HERO_BUTTON_BUNDLE_PREFIX = (
    "defaultlocalgroup_assets_assets_thebazaar_art_mainmenu_heroselect_hero_button_"
)
# Explicitly selected from a character-free patch of the official Mak badge.
# This rectangle is provenance, not a colour-search seed.
BACKING_PATCH_BOX = (125, 190, 150, 220)

BASE_LASSO_POLYGON = (
    (143, 151), (121, 165), (106, 190), (99, 220), (99, 323),
    (101, 344), (112, 364), (256, 478), (400, 364), (411, 344),
    (413, 323), (413, 220), (406, 190), (391, 165), (369, 151),
)
DEFAULT_LOWER_CORNER_Y = 350
DEFAULT_COMPLETED_FRAME_WIDTH = 20
REGISTRATION_SIZE = 128
REGISTRATION_OBSERVED_TOP = 110


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _polygon_mask(
    size: tuple[int, int],
    polygons: tuple[tuple[tuple[int, int], ...], ...],
) -> Image.Image:
    if size != REFERENCE_SIZE:
        raise ValueError("The verified badge lasso geometry is 512x512.")
    scale = 4
    mask = Image.new("L", (size[0] * scale, size[1] * scale), 0)
    draw = ImageDraw.Draw(mask)
    for polygon in polygons:
        draw.polygon([(x * scale, y * scale) for x, y in polygon], fill=255)
    return mask.resize(size, Image.Resampling.LANCZOS)


def _apply_alpha_mask(source: Image.Image, mask: Image.Image) -> Image.Image:
    source = source.convert("RGBA")
    if source.size != mask.size:
        raise ValueError("Source and alpha mask sizes differ.")
    output = source.copy()
    output.putalpha(ImageChops.multiply(source.getchannel("A"), mask))
    return output


def _exact_common_pixels(
    images: list[Image.Image],
    *,
    minimum_support: int = 2,
) -> Image.Image:
    """Keep every repeated RGBA value equally, without colour classification."""
    if len(images) < minimum_support:
        raise ValueError(
            f"At least {minimum_support} aligned badge sources are required."
        )
    if any(image.size != REFERENCE_SIZE for image in images):
        raise ValueError("Official badge sources must all be 512x512.")
    sources = [image.convert("RGBA") for image in images]
    source_pixels = [image.load() for image in sources]
    output = Image.new("RGBA", REFERENCE_SIZE, (0, 0, 0, 0))
    output_pixels = output.load()
    for y in range(REFERENCE_SIZE[1]):
        for x in range(REFERENCE_SIZE[0]):
            values = [pixels[x, y] for pixels in source_pixels]
            value, support = Counter(values).most_common(1)[0]
            if support >= minimum_support and value[3] > 0:
                output_pixels[x, y] = value
    return output


def _maximum_connected_region(source: Image.Image) -> Image.Image:
    """Return the largest 8-connected visible region from an RGBA image."""
    source = source.convert("RGBA")
    width, height = source.size
    alpha = source.getchannel("A")
    visited = bytearray(width * height)
    largest: list[tuple[int, int]] = []
    for y in range(height):
        for x in range(width):
            index = y * width + x
            if visited[index] or alpha.getpixel((x, y)) == 0:
                continue
            visited[index] = 1
            queue: deque[tuple[int, int]] = deque([(x, y)])
            component: list[tuple[int, int]] = []
            while queue:
                current_x, current_y = queue.popleft()
                component.append((current_x, current_y))
                for next_y in range(current_y - 1, current_y + 2):
                    for next_x in range(current_x - 1, current_x + 2):
                        if next_x == current_x and next_y == current_y:
                            continue
                        if not (0 <= next_x < width and 0 <= next_y < height):
                            continue
                        next_index = next_y * width + next_x
                        if visited[next_index] or alpha.getpixel((next_x, next_y)) == 0:
                            continue
                        visited[next_index] = 1
                        queue.append((next_x, next_y))
            if len(component) > len(largest):
                largest = component
    if not largest:
        raise ValueError("No connected common badge region was found.")
    mask = Image.new("L", source.size, 0)
    mask_pixels = mask.load()
    source_alpha = source.getchannel("A")
    for x, y in largest:
        mask_pixels[x, y] = source_alpha.getpixel((x, y))
    return _apply_alpha_mask(source, mask)


def _complete_small_gaps(source: Image.Image) -> Image.Image:
    """Close one-pixel Alpha gaps and copy RGB from the nearest common pixel."""
    source = source.convert("RGBA")
    alpha = source.getchannel("A")
    binary = alpha.point(lambda value: 255 if value > 0 else 0)
    completed = binary.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))
    missing = ImageChops.subtract(completed, binary)
    if missing.getbbox() is None:
        return source

    width, height = source.size
    output = source.copy()
    output_pixels = output.load()
    complete_pixels = completed.load()
    queue: deque[tuple[int, int]] = deque()
    visited = bytearray(width * height)
    for y in range(height):
        for x in range(width):
            if alpha.getpixel((x, y)) > 0:
                visited[y * width + x] = 1
                queue.append((x, y))
    while queue:
        x, y = queue.popleft()
        donor = output_pixels[x, y]
        for next_x, next_y in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
            if not (0 <= next_x < width and 0 <= next_y < height):
                continue
            index = next_y * width + next_x
            if visited[index] or complete_pixels[next_x, next_y] == 0:
                continue
            visited[index] = 1
            output_pixels[next_x, next_y] = donor
            queue.append((next_x, next_y))
    output.putalpha(completed)
    return output


def _recover_external_fringe(
    common_pixels: Image.Image,
    connected_frame: Image.Image,
    base_mask: Image.Image,
    *,
    radius: int = 8,
) -> Image.Image:
    """Restore the official frame's disconnected exterior fringe.

    The outer antialias and drop-shadow pixels are separated from the gold
    frame by transparent gaps, so selecting only the largest connected region
    makes both sides several pixels too narrow. Recovery is deliberately
    geometric: only exact-common official pixels outside the authored backing
    lasso and within ``radius`` pixels of the connected frame are admitted.
    No RGB, luminance, or colour-distance test is involved.
    """
    if radius < 1:
        raise ValueError("Frame fringe radius must be positive.")
    common_pixels = common_pixels.convert("RGBA")
    connected_frame = connected_frame.convert("RGBA")
    if common_pixels.size != connected_frame.size or base_mask.size != common_pixels.size:
        raise ValueError("Badge fringe inputs must share one canvas size.")
    common_alpha = common_pixels.getchannel("A")
    connected_alpha = connected_frame.getchannel("A")
    nearby = connected_alpha.filter(ImageFilter.MaxFilter(radius * 2 + 1)).point(
        lambda value: 255 if value > 0 else 0
    )
    outside_base = base_mask.convert("L").point(
        lambda value: 255 if value == 0 else 0
    )
    common_membership = common_alpha.point(lambda value: 255 if value > 0 else 0)
    fringe_mask = ImageChops.multiply(common_membership, nearby)
    fringe_mask = ImageChops.multiply(fringe_mask, outside_base)
    recovered = connected_frame.copy()
    recovered.alpha_composite(_apply_alpha_mask(common_pixels, fringe_mask))
    return recovered


def _stack_preview(images: list[Image.Image]) -> Image.Image:
    preview = images[0].convert("RGBA")
    for count, image in enumerate(images[1:], start=2):
        preview = Image.blend(preview, image.convert("RGBA"), 1.0 / count)
    return preview


def _lower_occlusion_mask(frame: Image.Image, *, corner_y: int) -> Image.Image:
    """Build the lower layer's transparent knockout from frame geometry.

    Starting at the two lower corners, the topmost visible frame pixel in each
    column defines the interior/exterior boundary. Pixels below that boundary
    belong to the transparent part of the lower overlay and must erase the
    character before the visible lower frame is composited.
    """
    alpha = frame.convert("RGBA").getchannel("A")
    width, height = alpha.size
    mask = Image.new("L", alpha.size, 0)
    pixels = mask.load()
    for x in range(width):
        visible_y = [
            y for y in range(corner_y, height) if alpha.getpixel((x, y)) > 0
        ]
        cutoff = min(visible_y) if visible_y else corner_y
        for y in range(cutoff, height):
            pixels[x, y] = 255
    return mask


def _frame_ring_from_alpha(source: Image.Image, *, width: int) -> Image.Image:
    """Partition a completed badge by Alpha geometry, never by pixel colour."""
    if width < 1:
        raise ValueError("Completed badge frame width must be positive.")
    alpha = source.convert("RGBA").getchannel("A").point(
        lambda value: 255 if value > 8 else 0
    )
    eroded = alpha.filter(ImageFilter.MinFilter(width * 2 + 1))
    return ImageChops.subtract(alpha, eroded)


def _centered_transform(
    source: Image.Image,
    *,
    scale_x: float,
    scale_y: float,
    translate_x: int,
    translate_y: int,
    resampling: Image.Resampling,
) -> Image.Image:
    width, height = source.size
    scaled = source.resize(
        (max(1, round(width * scale_x)), max(1, round(height * scale_y))),
        resampling,
    )
    canvas = Image.new(source.mode, source.size, 0)
    x = round((width - scaled.width) / 2) + translate_x
    y = round((height - scaled.height) / 2) + translate_y
    if source.mode == "RGBA":
        canvas.alpha_composite(scaled, (x, y))
    else:
        canvas.paste(scaled, (x, y))
    return canvas


def _register_completed_base(
    completed_base: Image.Image,
    official_frame_mask: Image.Image,
    *,
    frame_width: int,
) -> tuple[Image.Image, Image.Image, dict]:
    """Match an ImageGen completion to the extracted partial official frame.

    Registration examines only Alpha-mask overlap. RGB, HSV, luminance, and
    colour distance do not participate. The unobserved upper area is excluded
    from the score so a newly completed top rim is neither rewarded nor
    penalized.
    """
    completed_base = completed_base.convert("RGBA")
    official = official_frame_mask.convert("L").point(
        lambda value: 255 if value > 8 else 0
    )
    if completed_base.size != REFERENCE_SIZE or official.size != REFERENCE_SIZE:
        raise ValueError("Completed badge registration inputs must be 512x512.")
    generated_ring = _frame_ring_from_alpha(completed_base, width=frame_width)
    size = REGISTRATION_SIZE
    generated_small = generated_ring.resize((size, size), Image.Resampling.NEAREST)
    official_small = official.resize((size, size), Image.Resampling.NEAREST)
    evaluation = Image.new("L", (size, size), 0)
    observed_top = round(REGISTRATION_OBSERVED_TOP * size / REFERENCE_SIZE[1])
    ImageDraw.Draw(evaluation).rectangle((0, observed_top, size, size), fill=255)
    official_small = ImageChops.multiply(official_small, evaluation)
    official_count = official_small.histogram()[255]
    if official_count == 0:
        raise ValueError("Official partial frame mask has no observed pixels.")

    best: tuple[float, int, int, float, float, int, int, int] | None = None
    for scale_x_step in range(96, 109):
        scale_x = scale_x_step / 100
        for scale_y_step in range(98, 113):
            scale_y = scale_y_step / 100
            scaled = generated_small.resize(
                (round(size * scale_x), round(size * scale_y)),
                Image.Resampling.NEAREST,
            )
            for translate_x in range(-4, 5):
                for translate_y in range(-4, 9):
                    candidate = Image.new("L", (size, size), 0)
                    candidate.paste(
                        scaled,
                        (
                            round((size - scaled.width) / 2) + translate_x,
                            round((size - scaled.height) / 2) + translate_y,
                        ),
                    )
                    candidate = ImageChops.multiply(candidate, evaluation)
                    candidate_count = candidate.histogram()[255]
                    intersection = ImageChops.multiply(
                        candidate,
                        official_small,
                    ).histogram()[255]
                    dice = 2 * intersection / max(
                        1,
                        official_count + candidate_count,
                    )
                    score = (
                        dice,
                        intersection,
                        -abs(candidate_count - official_count),
                        scale_x,
                        scale_y,
                        translate_x,
                        translate_y,
                        candidate_count,
                    )
                    if best is None or score > best:
                        best = score
    if best is None:
        raise ValueError("Completed badge registration found no candidate.")
    (
        dice,
        intersection,
        _count_delta,
        scale_x,
        scale_y,
        translate_x_small,
        translate_y_small,
        candidate_count,
    ) = best
    coordinate_scale = REFERENCE_SIZE[0] // size
    translate_x = translate_x_small * coordinate_scale
    translate_y = translate_y_small * coordinate_scale
    registered = _centered_transform(
        completed_base,
        scale_x=scale_x,
        scale_y=scale_y,
        translate_x=translate_x,
        translate_y=translate_y,
        resampling=Image.Resampling.LANCZOS,
    )
    registered_ring = _frame_ring_from_alpha(registered, width=frame_width)
    overlap = Image.new(
        "RGBA",
        REFERENCE_SIZE,
        (35, 40, 50, 255),
    )
    registered_data = getattr(
        registered_ring,
        "get_flattened_data",
        registered_ring.getdata,
    )()
    official_data = getattr(
        official,
        "get_flattened_data",
        official.getdata,
    )()
    overlap.putdata(
        [
            (255, 255, 255, 255)
            if generated > 0 and observed > 0
            else (255, 0, 190, 255)
            if generated > 0
            else (0, 220, 255, 255)
            if observed > 0
            else (35, 40, 50, 255)
            for generated, observed in zip(registered_data, official_data)
        ]
    )
    metadata = {
        "method": "alpha-mask Dice overlap against extracted partial official frame",
        "evaluation_size": [size, size],
        "observed_top_y": REGISTRATION_OBSERVED_TOP,
        "scale_x": scale_x,
        "scale_y": scale_y,
        "translate_x": translate_x,
        "translate_y": translate_y,
        "dice": round(dice, 8),
        "intersection_pixels_at_evaluation_size": intersection,
        "official_pixels_at_evaluation_size": official_count,
        "candidate_pixels_at_evaluation_size": candidate_count,
    }
    return registered, overlap, metadata


def build_template_from_completed_base(
    completed_base_path: Path,
    official_frame_mask_path: Path,
    output_directory: Path,
    *,
    split_y: int = DEFAULT_LOWER_CORNER_Y,
    frame_width: int = DEFAULT_COMPLETED_FRAME_WIDTH,
) -> dict:
    """Build deterministic layers from an ImageGen-completed empty badge."""
    with Image.open(completed_base_path) as loaded:
        completed_base = loaded.convert("RGBA")
    with Image.open(official_frame_mask_path) as loaded:
        official_frame_mask = loaded.convert("L")
    if not 1 <= split_y < REFERENCE_SIZE[1]:
        raise ValueError(f"Invalid frame split y: {split_y}")
    registered, overlap, registration = _register_completed_base(
        completed_base,
        official_frame_mask,
        frame_width=frame_width,
    )
    frame_mask = _frame_ring_from_alpha(registered, width=frame_width)
    frame = _apply_alpha_mask(registered, frame_mask)
    upper = Image.new("RGBA", REFERENCE_SIZE, (0, 0, 0, 0))
    lower = Image.new("RGBA", REFERENCE_SIZE, (0, 0, 0, 0))
    upper.alpha_composite(frame.crop((0, 0, 512, split_y)), (0, 0))
    lower.alpha_composite(frame.crop((0, split_y, 512, 512)), (0, split_y))
    lower_occlusion = _lower_occlusion_mask(frame, corner_y=split_y)

    output_directory.mkdir(parents=True, exist_ok=True)
    paths = {
        "base": output_directory / "base.png",
        "frame_upper": output_directory / "frame_upper.png",
        "frame_lower": output_directory / "frame_lower.png",
        "frame_lower_occlusion": output_directory / "frame_lower_occlusion.png",
        "frame_source_mask": output_directory / "frame_source_mask.png",
        "registration_overlap": output_directory / "registration_overlap.png",
        "empty_preview": output_directory / "empty_preview.png",
    }
    registered.save(paths["base"], "PNG", optimize=True)
    upper.save(paths["frame_upper"], "PNG", optimize=True)
    lower.save(paths["frame_lower"], "PNG", optimize=True)
    lower_occlusion.save(paths["frame_lower_occlusion"], "PNG", optimize=True)
    frame_mask.save(paths["frame_source_mask"], "PNG", optimize=True)
    overlap.save(paths["registration_overlap"], "PNG", optimize=True)
    registered.save(paths["empty_preview"], "PNG", optimize=True)
    metadata = {
        "schema_version": 2,
        "method": (
            "ImageGen completion from exact official overlap, Alpha-only overlap "
            "registration, and geometric Alpha-ring layer partition"
        ),
        "aigc": True,
        "colour_inference": False,
        "size": list(REFERENCE_SIZE),
        "split_y": split_y,
        "frame_width": frame_width,
        "lower_boundary": "topmost lower-frame alpha per column from the authored corners",
        "layer_order_back_to_front": [
            "base",
            "frame_upper",
            "character",
            "frame_lower",
        ],
        "completed_base_source": {
            "file": str(completed_base_path.resolve()),
            "sha256": sha256_file(completed_base_path),
        },
        "official_frame_mask": {
            "file": str(official_frame_mask_path.resolve()),
            "sha256": sha256_file(official_frame_mask_path),
        },
        "registration": registration,
        "outputs": {
            name: {"file": path.name, "sha256": sha256_file(path)}
            for name, path in paths.items()
        },
    }
    metadata_path = output_directory / "template.json"
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return metadata


def extract_template(
    source_paths: list[Path],
    output_directory: Path,
    *,
    split_y: int = DEFAULT_LOWER_CORNER_Y,
) -> dict:
    resolved_sources = [path.resolve() for path in source_paths]
    if len(resolved_sources) < 2:
        raise ValueError("At least two aligned official badge sources are required.")
    for source_path in resolved_sources:
        if not source_path.is_file():
            raise FileNotFoundError(source_path)
    images: list[Image.Image] = []
    for source_path in resolved_sources:
        with Image.open(source_path) as loaded:
            image = loaded.convert("RGBA")
        if image.size != REFERENCE_SIZE:
            raise ValueError(
                f"Official badge source {source_path} is {image.size}; "
                f"expected {REFERENCE_SIZE}."
            )
        images.append(image)
    size = REFERENCE_SIZE
    if not 1 <= split_y < size[1]:
        raise ValueError(f"Invalid frame split y: {split_y}")

    # Expand the backing beneath the official frame so antialiased edge pixels
    # never reveal transparent seams. The extension stays under the frame; it
    # is not a colour-derived selection.
    base_mask = _polygon_mask(size, (BASE_LASSO_POLYGON,)).filter(
        ImageFilter.MaxFilter(11)
    )
    common_pixels = _exact_common_pixels(images, minimum_support=2)
    common_region = _maximum_connected_region(common_pixels)
    frame = _recover_external_fringe(common_pixels, common_region, base_mask)
    frame = _complete_small_gaps(frame)
    frame_mask = frame.getchannel("A")

    # The backing texture comes from one explicitly authored, character-free
    # rectangle in Mak's official source. Selection is coordinate-based; no
    # code examines or classifies its colour.
    mak_index = next(
        (
            index
            for index, path in enumerate(resolved_sources)
            if path.stem.casefold() == "mak"
        ),
        0,
    )
    patch = images[mak_index].crop(BACKING_PATCH_BOX)
    if patch.getchannel("A").getextrema()[0] < 250:
        raise ValueError("Authored backing patch contains transparent pixels.")
    backing = patch.resize(size, Image.Resampling.BICUBIC)
    base = _apply_alpha_mask(backing, base_mask)
    # Recover the official wood grain and the dark contact shadow immediately
    # inside the frame from exact-common source pixels. The flat authored patch
    # remains only as deterministic fill for character-occluded holes.
    base_detail_mask = ImageChops.multiply(
        common_pixels.getchannel("A").point(lambda value: 255 if value > 0 else 0),
        base_mask,
    )
    base_detail = _apply_alpha_mask(common_pixels, base_detail_mask)
    base.alpha_composite(base_detail)
    upper = Image.new("RGBA", size, (0, 0, 0, 0))
    lower = Image.new("RGBA", size, (0, 0, 0, 0))
    upper.alpha_composite(frame.crop((0, 0, size[0], split_y)), (0, 0))
    lower.alpha_composite(
        frame.crop((0, split_y, size[0], size[1])),
        (0, split_y),
    )
    lower_occlusion = _lower_occlusion_mask(frame, corner_y=split_y)

    output_directory.mkdir(parents=True, exist_ok=True)
    paths = {
        "base": output_directory / "base.png",
        "frame_upper": output_directory / "frame_upper.png",
        "frame_lower": output_directory / "frame_lower.png",
        "frame_lower_occlusion": output_directory / "frame_lower_occlusion.png",
        "base_mask": output_directory / "base_mask.png",
        "base_detail": output_directory / "base_detail.png",
        "frame_source_mask": output_directory / "frame_source_mask.png",
        "common_region": output_directory / "common_region.png",
        "stack_preview": output_directory / "stack_preview.png",
        "empty_preview": output_directory / "empty_preview.png",
    }
    base.save(paths["base"], "PNG", optimize=True)
    upper.save(paths["frame_upper"], "PNG", optimize=True)
    lower.save(paths["frame_lower"], "PNG", optimize=True)
    lower_occlusion.save(paths["frame_lower_occlusion"], "PNG", optimize=True)
    base_mask.save(paths["base_mask"], "PNG", optimize=True)
    base_detail.save(paths["base_detail"], "PNG", optimize=True)
    frame_mask.save(paths["frame_source_mask"], "PNG", optimize=True)
    common_pixels.save(paths["common_region"], "PNG", optimize=True)
    _stack_preview(images).save(paths["stack_preview"], "PNG", optimize=True)
    empty_preview = base.copy()
    empty_preview.alpha_composite(upper)
    empty_preview.alpha_composite(lower)
    empty_preview.save(paths["empty_preview"], "PNG", optimize=True)

    metadata = {
        "schema_version": 2,
        "method": (
            "maximum connected exact-common RGBA region across official hero "
            "buttons, exact-common exterior fringe recovery, and geometric "
            "one-pixel gap completion"
        ),
        "aigc": False,
        "colour_inference": False,
        "common_pixel_minimum_support": 2,
        "exterior_fringe_radius": 8,
        "size": list(size),
        "split_y": split_y,
        "lower_boundary": "topmost frame alpha per column from the authored corners",
        "backing_patch_box": list(BACKING_PATCH_BOX),
        "layer_order_back_to_front": [
            "base",
            "frame_upper",
            "character",
            "frame_lower",
        ],
        "sources": [
            {"file": str(source_path), "sha256": sha256_file(source_path)}
            for source_path in resolved_sources
        ],
        "outputs": {
            name: {"file": path.name, "sha256": sha256_file(path)}
            for name, path in paths.items()
        },
    }
    metadata_path = output_directory / "template.json"
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return metadata


def extract_game_template(
    game_directory: Path,
    output_directory: Path,
    *,
    split_y: int = DEFAULT_LOWER_CORNER_Y,
    unity_version: str = "6000.3.11f1",
) -> dict:
    """Export reference buttons from an installed game, then derive a template.

    The extracted official pixels stay below the caller-selected local output
    directory. They are intentionally absent from this repository and from the
    generated skin ZIP.
    """
    from unity_bundle_texture_patch import export_texture_bundle

    bundle_directory = (
        game_directory.resolve()
        / "TheBazaar_Data"
        / "StreamingAssets"
        / "aa"
        / "StandaloneWindows64"
    )
    source_directory = output_directory.resolve() / "sources"
    source_directory.mkdir(parents=True, exist_ok=True)
    exports: list[dict] = []
    source_paths: list[Path] = []
    for declaration in GAME_BADGE_REFERENCES:
        asset_name = declaration["asset_name"]
        bundle_suffix = declaration["bundle_suffix"]
        bundle = bundle_directory / f"{HERO_BUTTON_BUNDLE_PREFIX}{bundle_suffix}.png.bundle"
        output = source_directory / f"{bundle_suffix}.png"
        export = export_texture_bundle(
            bundle,
            output,
            asset_name=asset_name,
            unity_version=unity_version,
            target_size=REFERENCE_SIZE,
        )
        export["bundle"] = str(bundle)
        exports.append(export)
        source_paths.append(output)
    metadata = extract_template(source_paths, output_directory, split_y=split_y)
    metadata["game_exports"] = exports
    metadata_path = output_directory / "template.json"
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return metadata


def compose_badge(
    character: Image.Image,
    *,
    shadow: Image.Image | None = None,
    fit_reference: Image.Image | None = None,
    base: Image.Image,
    frame_upper: Image.Image,
    frame_lower: Image.Image,
    frame_lower_occlusion: Image.Image,
    target_bounds: tuple[int, int, int, int],
    output_size: tuple[int, int],
) -> Image.Image:
    from skin_pack_builder import alpha_bounds

    canvas_size = base.size
    if (
        frame_upper.size != canvas_size
        or frame_lower.size != canvas_size
        or frame_lower_occlusion.size != canvas_size
    ):
        raise ValueError("Badge template layers must share one canvas size.")
    reference = (character if fit_reference is None else fit_reference).convert("RGBA")
    bounds = alpha_bounds(reference)
    if bounds is None:
        raise ValueError("Badge foreground has no visible pixels.")
    left, top, right, bottom = target_bounds
    cropped_reference = reference.crop(bounds)
    scale = min(
        (right - left) / cropped_reference.width,
        (bottom - top) / cropped_reference.height,
    )
    fitted_size = (
        max(1, int(round(cropped_reference.width * scale))),
        max(1, int(round(cropped_reference.height * scale))),
    )
    x = int(round(left + ((right - left) - fitted_size[0]) * 0.5))
    y = int(round(bottom - fitted_size[1]))

    def fit_shared(layer: Image.Image) -> Image.Image:
        if layer.size != reference.size:
            raise ValueError("Badge character layers must share one source canvas.")
        fitted = layer.convert("RGBA").crop(bounds).resize(
            fitted_size,
            Image.Resampling.LANCZOS,
        )
        canvas = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
        canvas.alpha_composite(fitted, (x, y))
        return canvas

    fitted_character = fit_shared(character)
    output = base.copy()
    if shadow is not None:
        fitted_shadow = fit_shared(shadow)
        fitted_shadow.putalpha(
            ImageChops.multiply(
                fitted_shadow.getchannel("A"),
                base.getchannel("A"),
            )
        )
        output.alpha_composite(fitted_shadow)
    output.alpha_composite(frame_upper)
    fitted_character.putalpha(
        ImageChops.multiply(
            fitted_character.getchannel("A"),
            ImageChops.invert(frame_lower_occlusion.convert("L")),
        )
    )
    output.alpha_composite(fitted_character)
    output.alpha_composite(frame_lower)
    if output.size != output_size:
        output = output.resize(output_size, Image.Resampling.LANCZOS)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    extract = subparsers.add_parser("extract-template")
    extract.add_argument("--source", action="append", required=True, type=Path)
    extract.add_argument("--output-dir", required=True, type=Path)
    extract.add_argument("--split-y", default=DEFAULT_LOWER_CORNER_Y, type=int)
    extract_game = subparsers.add_parser("extract-game-template")
    extract_game.add_argument("--game-dir", required=True, type=Path)
    extract_game.add_argument("--output-dir", required=True, type=Path)
    extract_game.add_argument("--split-y", default=DEFAULT_LOWER_CORNER_Y, type=int)
    extract_game.add_argument("--unity-version", default="6000.3.11f1")
    completed = subparsers.add_parser("build-completed-base-template")
    completed.add_argument("--completed-base", required=True, type=Path)
    completed.add_argument("--official-frame-mask", required=True, type=Path)
    completed.add_argument("--output-dir", required=True, type=Path)
    completed.add_argument("--split-y", default=DEFAULT_LOWER_CORNER_Y, type=int)
    completed.add_argument(
        "--frame-width",
        default=DEFAULT_COMPLETED_FRAME_WIDTH,
        type=int,
    )
    args = parser.parse_args()
    if args.command == "extract-template":
        result = extract_template(
            args.source,
            args.output_dir,
            split_y=args.split_y,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "extract-game-template":
        result = extract_game_template(
            args.game_dir,
            args.output_dir,
            split_y=args.split_y,
            unity_version=args.unity_version,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "build-completed-base-template":
        result = build_template_from_completed_base(
            args.completed_base,
            args.official_frame_mask,
            args.output_dir,
            split_y=args.split_y,
            frame_width=args.frame_width,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
