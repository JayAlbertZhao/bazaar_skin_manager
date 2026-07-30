#!/usr/bin/env python3
"""Patch one Texture2D inside a UnityFS bundle and verify the saved result."""

from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
from pathlib import Path

from PIL import Image, ImageChops


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_image(source: Path, size: tuple[int, int]) -> Image.Image:
    with Image.open(source) as loaded:
        image = loaded.convert("RGBA")
    if image.size != size:
        image = image.resize(size, Image.Resampling.LANCZOS)
    return image


def _load_unitypy(unity_version: str):
    try:
        import UnityPy
    except ImportError as error:
        raise RuntimeError(
            "Unity bundle patching requires UnityPy. Use the packaged Mod "
            "Manager or install the project manager dependencies."
        ) from error
    UnityPy.config.FALLBACK_UNITY_VERSION = unity_version
    return UnityPy


def _texture(environment, asset_name: str):
    matches = []
    for item in environment.objects:
        if item.type.name != "Texture2D":
            continue
        texture = item.read()
        if texture.m_Name == asset_name:
            matches.append(texture)
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected exactly one Texture2D named {asset_name!r}; "
            f"found {len(matches)}."
        )
    return matches[0]


def patch_texture_bundle(
    source_bundle: Path,
    output_bundle: Path,
    replacement_image: Path,
    *,
    asset_name: str,
    unity_version: str,
    target_size: tuple[int, int],
) -> dict:
    """Patch one texture. Kept as a convenience wrapper for tooling/tests."""
    return patch_texture_bundle_many(
        source_bundle,
        output_bundle,
        [
            {
                "replacement_image": replacement_image,
                "asset_name": asset_name,
                "target_size": target_size,
            }
        ],
        unity_version=unity_version,
    )


def patch_texture_bundle_many(
    source_bundle: Path,
    output_bundle: Path,
    replacements: list[dict],
    *,
    unity_version: str,
) -> dict:
    """Patch one or more Texture2D objects in one bundle transaction."""
    source_bundle = source_bundle.resolve()
    output_bundle = output_bundle.resolve()
    if not source_bundle.is_file():
        raise FileNotFoundError(source_bundle)
    if not replacements:
        raise ValueError("At least one Texture2D replacement is required.")

    UnityPy = _load_unitypy(unity_version)
    expected_by_asset: dict[str, Image.Image] = {}
    for replacement in replacements:
        replacement_image = Path(replacement["replacement_image"]).resolve()
        if not replacement_image.is_file():
            raise FileNotFoundError(replacement_image)
        asset_name = str(replacement["asset_name"])
        target_size = tuple(int(value) for value in replacement["target_size"])
        expected = normalized_image(replacement_image, target_size)
        existing = expected_by_asset.get(asset_name)
        if existing is not None:
            if ImageChops.difference(existing, expected).getbbox() is not None:
                raise RuntimeError(
                    f"Conflicting replacement images target {asset_name!r}."
                )
            continue
        expected_by_asset[asset_name] = expected

    # Load from bytes so UnityPy cannot retain a Windows file handle that
    # would block the manager's later atomic replace or temp cleanup.
    environment = UnityPy.load(source_bundle.read_bytes())
    for asset_name, expected in expected_by_asset.items():
        target_size = expected.size
        texture = _texture(environment, asset_name)
        if (int(texture.m_Width), int(texture.m_Height)) != target_size:
            raise RuntimeError(
                f"Texture {asset_name!r} is "
                f"{texture.m_Width}x{texture.m_Height}; expected "
                f"{target_size[0]}x{target_size[1]}."
            )
        texture.image = expected
        texture.save()

    output_bundle.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=output_bundle.parent,
        delete=False,
    ) as stream:
        stream.write(environment.file.save())
        temporary = Path(stream.name)
    os.replace(temporary, output_bundle)

    verification = UnityPy.load(output_bundle.read_bytes())
    measurements = []
    for asset_name, expected in expected_by_asset.items():
        target_size = expected.size
        actual = _texture(verification, asset_name).image.convert("RGBA")
        if actual.size != target_size:
            raise RuntimeError(
                f"Saved Texture2D {asset_name!r} is "
                f"{actual.width}x{actual.height}; expected "
                f"{target_size[0]}x{target_size[1]}."
            )
        # BC7 is lossy. A pixel-exact comparison would reject a valid bundle,
        # so bound the maximum per-channel difference and mean absolute error.
        # Transparent RGB is not rendered, and BC7 may legitimately assign
        # very different RGB values to fully transparent texels. Compare
        # premultiplied colour plus alpha so verification measures the pixels
        # the game can actually display.
        expected_channels = expected.split()
        actual_channels = actual.split()
        expected_rendered = Image.merge(
            "RGBA",
            (
                ImageChops.multiply(expected_channels[0], expected_channels[3]),
                ImageChops.multiply(expected_channels[1], expected_channels[3]),
                ImageChops.multiply(expected_channels[2], expected_channels[3]),
                expected_channels[3],
            ),
        )
        actual_rendered = Image.merge(
            "RGBA",
            (
                ImageChops.multiply(actual_channels[0], actual_channels[3]),
                ImageChops.multiply(actual_channels[1], actual_channels[3]),
                ImageChops.multiply(actual_channels[2], actual_channels[3]),
                actual_channels[3],
            ),
        )
        difference = ImageChops.difference(expected_rendered, actual_rendered)
        extrema = difference.getextrema()
        maximum_error = max(channel[1] for channel in extrema)
        histogram = difference.histogram()
        absolute_sum = sum(
            count * (index % 256) for index, count in enumerate(histogram)
        )
        mean_error = absolute_sum / (target_size[0] * target_size[1] * 4)
        if maximum_error > 224 or mean_error > 8.0:
            output_bundle.unlink(missing_ok=True)
            raise RuntimeError(
                "Saved Texture2D verification exceeded the BC7 error bound "
                f"for {asset_name!r}: max={maximum_error}, "
                f"mean={mean_error:.3f}."
            )
        measurements.append(
            {
                "asset_name": asset_name,
                "width": target_size[0],
                "height": target_size[1],
                "maximum_channel_error": maximum_error,
                "mean_absolute_error": round(mean_error, 6),
            }
        )

    return {
        "source_sha256": sha256_file(source_bundle),
        "output_sha256": sha256_file(output_bundle),
        "textures": measurements,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_bundle", type=Path)
    parser.add_argument("output_bundle", type=Path)
    parser.add_argument("replacement_image", type=Path)
    parser.add_argument("--asset-name", required=True)
    parser.add_argument("--unity-version", default="6000.3.11f1")
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    args = parser.parse_args()
    result = patch_texture_bundle(
        args.source_bundle,
        args.output_bundle,
        args.replacement_image,
        asset_name=args.asset_name,
        unity_version=args.unity_version,
        target_size=(args.width, args.height),
    )
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
