#!/usr/bin/env python3
"""Apply a hand-authored geometric lasso to an image.

This tool deliberately does not inspect RGB, HSV, luminance, or pixel
similarity. The JSON geometry is the complete extraction decision.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw


def render_mask(recipe: dict, output_size: tuple[int, int]) -> Image.Image:
    coordinate_space = tuple(int(v) for v in recipe["coordinate_space"])
    if len(coordinate_space) != 2 or min(coordinate_space) <= 0:
        raise ValueError("coordinate_space must be [width, height].")
    supersampling = int(recipe.get("supersampling", 4))
    if not 1 <= supersampling <= 16:
        raise ValueError("supersampling must be between 1 and 16.")
    mask = Image.new(
        "L",
        (coordinate_space[0] * supersampling, coordinate_space[1] * supersampling),
        0,
    )
    draw = ImageDraw.Draw(mask)

    def scaled(point: list[float]) -> tuple[int, int]:
        return (
            round(float(point[0]) * supersampling),
            round(float(point[1]) * supersampling),
        )

    for shape in recipe.get("shapes") or []:
        shape_type = shape.get("type")
        if shape_type == "polygon":
            points = shape.get("points") or []
            if len(points) < 3:
                raise ValueError("Every polygon needs at least three points.")
            draw.polygon([scaled(point) for point in points], fill=255)
        elif shape_type == "ellipse":
            box = shape.get("box") or []
            if len(box) != 4:
                raise ValueError("Every ellipse needs box [l, t, r, b].")
            draw.ellipse(
                (
                    round(float(box[0]) * supersampling),
                    round(float(box[1]) * supersampling),
                    round(float(box[2]) * supersampling),
                    round(float(box[3]) * supersampling),
                ),
                fill=255,
            )
        else:
            raise ValueError(f"Unsupported authored shape: {shape_type}")
    return mask.resize(output_size, Image.Resampling.LANCZOS)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--recipe", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    recipe = json.loads(args.recipe.read_text(encoding="utf-8"))
    with Image.open(args.source) as loaded:
        source = loaded.convert("RGBA")
    mask = render_mask(recipe, source.size)
    output = source.copy()
    output.putalpha(ImageChops.multiply(source.getchannel("A"), mask))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    output.save(args.output, "PNG", optimize=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
