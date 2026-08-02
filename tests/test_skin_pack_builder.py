from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

from skin_pack_builder import (  # noqa: E402
    _load_badge_template,
    derive_small_icon_binary,
    derive_small_icon_file,
    fit_alpha_contain,
    generate_pack,
    image_metrics,
    remove_edge_connected_background,
    split_authored_underlay,
    _validate_metrics,
)
from badge_pipeline import (  # noqa: E402
    REFERENCE_SIZE,
    build_template_from_completed_base,
    compose_badge,
    extract_template,
)
from mod_studio_core import sha256_file  # noqa: E402


def write_test_badge_template(root: Path) -> Path:
    """Create non-game fixture pixels with the production template contract."""
    directory = root / "badge-templates" / "hero-select-gold"
    directory.mkdir(parents=True, exist_ok=True)
    base = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    ImageDraw.Draw(base).polygon(
        [(120, 120), (392, 120), (440, 320), (256, 485), (72, 320)],
        fill=(72, 31, 9, 255),
    )
    upper = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(upper).line(
        [(120, 120), (72, 320), (256, 485), (440, 320), (392, 120)],
        fill=(220, 150, 40, 255),
        width=20,
    )
    lower = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(lower).line(
        [(72, 320), (256, 485), (440, 320)],
        fill=(255, 205, 70, 255),
        width=28,
    )
    lower_occlusion = Image.new("L", base.size, 0)
    ImageDraw.Draw(lower_occlusion).polygon(
        [(0, 350), (72, 350), (256, 485), (440, 350), (512, 350), (512, 512), (0, 512)],
        fill=255,
    )
    layers = {
        "base": base,
        "frame_upper": upper,
        "frame_lower": lower,
        "frame_lower_occlusion": lower_occlusion,
    }
    outputs = {}
    for name, image in layers.items():
        path = directory / f"{name}.png"
        image.save(path, "PNG", optimize=True)
        outputs[name] = {"file": path.name, "sha256": sha256_file(path)}
    ledger = {
        "schema_version": 2,
        "method": "synthetic unit-test fixture",
        "aigc": False,
        "colour_inference": False,
        "size": [512, 512],
        "layer_order_back_to_front": [
            "base",
            "frame_upper",
            "character",
            "frame_lower",
        ],
        "outputs": outputs,
    }
    (directory / "template.json").write_text(
        json.dumps(ledger, indent=2) + "\n",
        encoding="utf-8",
    )
    return root


class SkinPackBuilderTests(unittest.TestCase):
    def test_sparse_binary_icon_is_not_rejected_as_too_small(self) -> None:
        adapter = json.loads(
            (ROOT / "manager" / "adapters" / "dooley-default.json").read_text(
                encoding="utf-8"
            )
        )
        recipe = adapter["authoring_recipe"]["outputs"]["hero_icon_small"]
        icon = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
        draw = ImageDraw.Draw(icon)
        draw.ellipse((38, 28, 218, 228), outline=(255, 255, 255, 255), width=18)
        draw.line((58, 192, 196, 64), fill=(255, 255, 255, 255), width=18)
        metrics = image_metrics(icon)
        self.assertGreater(metrics["alpha_coverage"], 0.08)
        self.assertLess(metrics["alpha_coverage"], 0.2)
        _validate_metrics("hero_icon_small", metrics, recipe)

    def test_partial_pack_skips_outputs_whose_author_material_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            character = root / "character.png"
            image = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
            ImageDraw.Draw(image).rectangle((38, 8, 57, 92), fill=(25, 190, 90, 255))
            image.save(character)
            result = generate_pack(
                adapter_id="dooley-default",
                character=character,
                background=None,
                small_icon=None,
                workspace_root=root / "work",
                output_zip=root / "pack.zip",
                pack_id="test.partial-authoring",
                name="Partial Authoring Test",
                version="0.1.0",
                input_metadata={
                    "character": {"aigc": False, "authoritative_alpha": True},
                },
                badge_template_root=write_test_badge_template(root / "templates"),
                allow_partial=True,
            )
            self.assertEqual(
                set(result["outputs"]),
                {"hero_select", "portrait_gameplay", "standing_overlay"},
            )
            self.assertNotIn("portrait_gameplay", result["skipped_outputs"])
            self.assertIn("hero_icon_small", result["skipped_outputs"])
            manifest = json.loads(
                (Path(result["workspace"]) / "mod.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                {item["slot"] for item in manifest["visual_replacements"]},
                {"hero_select", "portrait_gameplay", "standing_overlay"},
            )

    def test_completed_badge_base_is_registered_and_split_by_alpha_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            completed_path = root / "completed.png"
            completed = Image.new("RGBA", REFERENCE_SIZE, (0, 0, 0, 0))
            polygon = [(256, 42), (424, 165), (424, 345), (256, 488), (88, 345), (88, 165)]
            ImageDraw.Draw(completed).polygon(polygon, fill=(90, 42, 12, 255))
            ImageDraw.Draw(completed).line(
                polygon + [polygon[0]],
                fill=(235, 180, 35, 255),
                width=20,
                joint="curve",
            )
            completed.save(completed_path)
            official_mask_path = root / "official-frame-mask.png"
            official_mask = Image.new("L", REFERENCE_SIZE, 0)
            ImageDraw.Draw(official_mask).line(
                polygon[1:] + [polygon[0]],
                fill=255,
                width=20,
                joint="curve",
            )
            ImageDraw.Draw(official_mask).rectangle((0, 0, 511, 109), fill=0)
            official_mask.save(official_mask_path)
            output = root / "template"
            metadata = build_template_from_completed_base(
                completed_path,
                official_mask_path,
                output,
            )
            self.assertTrue(metadata["aigc"])
            self.assertFalse(metadata["colour_inference"])
            self.assertGreater(metadata["registration"]["dice"], 0.65)
            for name in (
                "base",
                "frame_upper",
                "frame_lower",
                "frame_lower_occlusion",
            ):
                self.assertTrue((output / metadata["outputs"][name]["file"]).is_file())
            with Image.open(output / "base.png") as base:
                self.assertIsNotNone(base.convert("RGBA").getchannel("A").getbbox())
            with Image.open(output / "frame_lower_occlusion.png") as knockout:
                self.assertIsNotNone(knockout.convert("L").getbbox())

    def test_alpha_contain_can_intentionally_clip_at_canvas_edge(self) -> None:
        source = Image.new("RGBA", (20, 40), (255, 120, 20, 255))
        rendered = fit_alpha_contain(
            source,
            size=(100, 100),
            target_bounds=(80, 10, 120, 90),
            anchor=(0.0, 0.0),
        )
        self.assertEqual(rendered.getbbox(), (80, 10, 100, 90))

    def test_authoritative_alpha_source_bypasses_legacy_cutout_lassos(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "authoritative.png"
            source_image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            ImageDraw.Draw(source_image).rectangle(
                (40, 10, 63, 63),
                fill=(20, 210, 90, 255),
            )
            source_image.save(source)
            background = root / "background.png"
            Image.new("RGBA", (128, 128), (30, 40, 60, 255)).save(background)
            icon = root / "icon.png"
            icon_image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            ImageDraw.Draw(icon_image).rectangle(
                (20, 8, 43, 55),
                fill=(255, 255, 255, 255),
            )
            icon_image.save(icon)
            result = generate_pack(
                adapter_id="dooley-default",
                character=source,
                background=background,
                small_icon=icon,
                workspace_root=root / "work",
                output_zip=root / "pack.zip",
                pack_id="test.authoritative-alpha",
                name="Authoritative Alpha Test",
                version="0.1.0",
                input_metadata={
                    "character": {
                        "aigc": False,
                        "authoritative_alpha": True,
                        "alpha_method": "test-authored alpha",
                    },
                    "background": {"aigc": False},
                    "small_icon": {"aigc": False},
                },
                badge_template_root=write_test_badge_template(root / "templates"),
            )
            workspace = Path(result["workspace"])
            manifest = json.loads((workspace / "mod.json").read_text(encoding="utf-8"))
            foreground = manifest["authoring"]["foreground"]
            self.assertEqual(
                foreground["authoritative_alpha"],
                {
                    "declared": True,
                    "method": "test-authored alpha",
                    "background_removal": False,
                    "authored_lasso_postprocessing": False,
                },
            )
            self.assertNotIn("authored_transparency", foreground)
            self.assertNotIn("cast_shadow", foreground)
            expected = fit_alpha_contain(
                source_image,
                size=(1086, 1448),
                target_bounds=(12, 12, 1074, 1436),
                anchor=(0.5, 1.0),
            )
            with Image.open(workspace / "assets" / "standing_overlay.png") as actual:
                self.assertEqual(actual.convert("RGBA").tobytes(), expected.tobytes())

    def test_lower_badge_transparency_knocks_out_character(self) -> None:
        size = (512, 512)
        base = Image.new("RGBA", size, (60, 30, 10, 255))
        clear = Image.new("RGBA", size, (0, 0, 0, 0))
        character = Image.new("RGBA", size, (220, 20, 30, 255))
        lower_occlusion = Image.new("L", size, 0)
        ImageDraw.Draw(lower_occlusion).rectangle((0, 350, 511, 511), fill=255)
        rendered = compose_badge(
            character,
            base=base,
            frame_upper=clear,
            frame_lower=clear,
            frame_lower_occlusion=lower_occlusion,
            target_bounds=(0, 0, 512, 512),
            output_size=size,
        )
        self.assertEqual(rendered.getpixel((256, 100)), (220, 20, 30, 255))
        self.assertEqual(rendered.getpixel((256, 400)), (60, 30, 10, 255))

    def test_authored_underlay_is_a_lossless_hard_partition(self) -> None:
        source = Image.new("RGBA", (12, 10), (0, 0, 0, 0))
        draw = ImageDraw.Draw(source)
        draw.rectangle((1, 1, 10, 8), fill=(215, 30, 75, 255))
        draw.rectangle((7, 2, 10, 8), fill=(8, 8, 8, 210))
        foreground, underlay, mask = split_authored_underlay(
            source,
            {
                "coordinate_space": [12, 10],
                "polygons": [[[7, 0], [11, 0], [11, 9], [7, 9]]],
            },
        )
        recombined = Image.new("RGBA", source.size, (0, 0, 0, 0))
        recombined.alpha_composite(underlay)
        recombined.alpha_composite(foreground)
        self.assertEqual(recombined.tobytes(), source.tobytes())
        self.assertEqual(mask.getextrema(), (0, 255))

    def test_badge_common_region_preserves_bright_rgb_without_colour_filtering(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_paths = [root / "profession-a.png", root / "profession-b.png"]
            frame_points = [(120, 150), (85, 230), (90, 350), (256, 490), (422, 350), (427, 230), (392, 150)]
            for path, background in zip(
                source_paths,
                ((31, 17, 9, 255), (53, 29, 13, 255)),
            ):
                source = Image.new("RGBA", REFERENCE_SIZE, background)
                ImageDraw.Draw(source).line(
                    frame_points,
                    fill=(255, 255, 255, 255),
                    width=24,
                    joint="curve",
                )
                source.save(path)
            coordinate = (86, 250)
            template = root / "template"
            extract_template(source_paths, template)
            layer_name = "frame_upper.png" if coordinate[1] < 450 else "frame_lower.png"
            with Image.open(template / layer_name) as loaded:
                self.assertEqual(
                    loaded.convert("RGBA").getpixel(coordinate),
                    (255, 255, 255, 255),
                )
            ledger = json.loads((template / "template.json").read_text(encoding="utf-8"))
            self.assertFalse(ledger["colour_inference"])
            self.assertEqual(ledger["common_pixel_minimum_support"], 2)
            self.assertEqual(ledger["split_y"], 350)

    def test_badge_template_hash_tampering_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = write_test_badge_template(Path(temp))
            template = {"directory": "badge-templates/hero-select-gold"}
            _load_badge_template(template, template_root=root)
            layer = root / template["directory"] / "frame_lower.png"
            layer.write_bytes(layer.read_bytes() + b"tampered")
            with self.assertRaisesRegex(ValueError, "hash mismatch: frame_lower"):
                _load_badge_template(template, template_root=root)

    def test_outline_preset_inverts_dark_ink(self) -> None:
        source = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
        draw = ImageDraw.Draw(source)
        draw.rectangle((16, 16, 63, 111), fill=(190, 0, 45, 255))
        draw.rectangle((65, 16, 112, 111), fill=(10, 190, 90, 255))
        draw.rectangle((60, 20, 68, 107), fill=(8, 8, 8, 255))

        output = derive_small_icon_binary(
            source,
            normalized_region=(0.0, 0.0, 1.0, 1.0),
            size=(128, 128),
            padding_fraction=0.0,
            preset="outline",
            boundary_width=2,
        )

        self.assertEqual(output.getpixel((30, 64)), (255, 255, 255, 255))
        self.assertEqual(output.getpixel((98, 64)), (255, 255, 255, 255))
        self.assertEqual(output.getpixel((64, 64))[3], 0)

    def test_block_gap_preset_insets_every_colour_block(self) -> None:
        source = Image.new("RGBA", (128, 128), (190, 0, 45, 255))
        draw = ImageDraw.Draw(source)
        draw.rectangle((40, 0, 79, 127), fill=(8, 8, 8, 255))
        draw.rectangle((80, 0, 127, 127), fill=(10, 190, 90, 255))

        output = derive_small_icon_binary(
            source,
            normalized_region=(0.0, 0.0, 1.0, 1.0),
            size=(128, 128),
            padding_fraction=0.0,
            preset="block-gaps",
            block_gap_width=3,
        )

        self.assertEqual(output.getpixel((20, 64)), (255, 255, 255, 255))
        self.assertEqual(output.getpixel((60, 64)), (255, 255, 255, 255))
        self.assertEqual(output.getpixel((104, 64)), (255, 255, 255, 255))
        self.assertEqual(output.getpixel((40, 64))[3], 0)
        self.assertEqual(output.getpixel((80, 64))[3], 0)

    def test_silhouette_preset_keeps_dark_regions_filled(self) -> None:
        source = Image.new("RGBA", (64, 64), (8, 8, 8, 255))
        output = derive_small_icon_binary(
            source,
            normalized_region=(0.0, 0.0, 1.0, 1.0),
            size=(64, 64),
            padding_fraction=0.0,
            preset="silhouette",
        )
        self.assertEqual(output.getpixel((32, 32)), (255, 255, 255, 255))

    def test_unknown_small_icon_preset_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported small-icon preset"):
            derive_small_icon_binary(
                Image.new("RGBA", (32, 32), "white"),
                normalized_region=(0.0, 0.0, 1.0, 1.0),
                preset="unknown",
            )

    def test_edge_connected_removal_preserves_enclosed_white(self) -> None:
        source = Image.new("RGB", (64, 64), "white")
        draw = ImageDraw.Draw(source)
        draw.rectangle((14, 12, 50, 52), fill="black")
        draw.rectangle((18, 16, 46, 48), fill="white")
        draw.rectangle((22, 30, 42, 44), fill=(20, 180, 80))

        output = remove_edge_connected_background(
            source,
            tolerance=20,
            feather=60,
        )

        self.assertEqual(output.getpixel((0, 0))[3], 0)
        self.assertEqual(output.getpixel((32, 20))[3], 255)
        self.assertEqual(output.getpixel((14, 20))[3], 255)
        self.assertEqual(output.getpixel((32, 36))[3], 255)

    def test_dooley_recipe_is_deterministic_and_uses_three_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "character.png"
            image = Image.new("RGB", (320, 400), "white")
            draw = ImageDraw.Draw(image)
            draw.rounded_rectangle(
                (55, 35, 265, 365),
                radius=50,
                fill=(10, 190, 90),
                outline="black",
                width=12,
            )
            draw.rectangle((95, 155, 235, 285), fill=(190, 0, 45), outline="black", width=8)
            draw.ellipse((105, 70, 150, 115), fill="white", outline="black", width=7)
            image.save(source)
            background = root / "background.png"
            Image.new("RGB", (480, 720), (35, 44, 70)).save(background)
            icon = derive_small_icon_file(
                source,
                root / "small-icon.png",
                normalized_region=(0.1, 0.05, 0.9, 0.55),
                tolerance=20,
                feather=60,
            )
            with Image.open(icon) as loaded_icon:
                rgba_icon = loaded_icon.convert("RGBA")
                flattened = getattr(
                    rgba_icon,
                    "get_flattened_data",
                    rgba_icon.getdata,
                )
                visible_rgb = {
                    pixel[:3]
                    for pixel in flattened()
                    if pixel[3] > 8
                }
            self.assertEqual(visible_rgb, {(255, 255, 255)})
            metadata = {
                "character": {"origin": "user_supplied", "aigc": False},
                "background": {
                    "origin": "third_party_open_license",
                    "aigc": False,
                    "license": "CC0-1.0",
                    "source_url": "https://example.invalid/background",
                },
                "small_icon": {
                    "origin": "deterministic_derivative",
                    "aigc": False,
                    "derived_from": "character",
                },
            }
            badge_template_root = write_test_badge_template(root / "templates")

            first = generate_pack(
                adapter_id="dooley-default",
                character=source,
                background=background,
                small_icon=icon,
                workspace_root=root / "first-work",
                output_zip=root / "first.zip",
                pack_id="test.dooley.generated",
                name="Generated Dooley Test",
                version="0.1.0",
                input_metadata=metadata,
                badge_template_root=badge_template_root,
            )
            second = generate_pack(
                adapter_id="dooley-default",
                character=source,
                background=background,
                small_icon=icon,
                workspace_root=root / "second-work",
                output_zip=root / "second.zip",
                pack_id="test.dooley.generated",
                name="Generated Dooley Test",
                version="0.1.0",
                input_metadata=metadata,
                badge_template_root=badge_template_root,
            )

            self.assertEqual(first["zip_sha256"], second["zip_sha256"])
            self.assertEqual(
                {slot: item["sha256"] for slot, item in first["outputs"].items()},
                {slot: item["sha256"] for slot, item in second["outputs"].items()},
            )
            self.assertEqual(
                first["outputs"]["store_image"]["sha256"],
                first["outputs"]["marketplace_list"]["sha256"],
            )
            self.assertEqual(
                first["outputs"]["collection_list"]["sha256"],
                first["outputs"]["daily_weekly"]["sha256"],
            )

            adjusted = generate_pack(
                adapter_id="dooley-default",
                character=source,
                background=background,
                small_icon=icon,
                workspace_root=root / "adjusted-work",
                output_zip=root / "adjusted.zip",
                pack_id="test.dooley.adjusted",
                name="Adjusted Dooley Test",
                version="0.1.0",
                input_metadata=metadata,
                badge_template_root=badge_template_root,
                character_canvas_offset=(10, -5),
                output_offsets={"portrait_small": (9, 4)},
            )
            self.assertNotEqual(
                adjusted["outputs"]["standing_overlay"]["sha256"],
                first["outputs"]["standing_overlay"]["sha256"],
            )
            self.assertEqual(
                adjusted["outputs"]["portrait_background"]["sha256"],
                first["outputs"]["portrait_background"]["sha256"],
            )
            self.assertEqual(
                adjusted["outputs"]["hero_icon_small"]["sha256"],
                first["outputs"]["hero_icon_small"]["sha256"],
            )
            self.assertEqual(
                adjusted["outputs"]["portrait_small"]["sha256"],
                adjusted["outputs"]["collection_list"]["sha256"],
            )
            self.assertEqual(
                adjusted["outputs"]["daily_weekly"]["sha256"],
                adjusted["outputs"]["collection_list"]["sha256"],
            )
            adjusted_manifest = json.loads(
                (Path(adjusted["workspace"]) / "mod.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                adjusted_manifest["authoring"]["adjustments"],
                {
                    "character_canvas": [10, -5],
                    "per_output": {"portrait_small": [9, 4]},
                    "canonical_assets": {
                        "portrait_small": "collection_list"
                    },
                },
            )
            self.assertEqual(
                adjusted_manifest["authoring"]["outputs"]["portrait_small"]["alias_of"],
                "collection_list",
            )

            workspace = Path(first["workspace"])
            manifest = json.loads((workspace / "mod.json").read_text(encoding="utf-8"))
            slots = {item["slot"] for item in manifest["visual_replacements"]}
            self.assertEqual(
                slots,
                {
                    "portrait_gameplay",
                    "portrait_background",
                    "portrait_small",
                    "store_image",
                    "marketplace_list",
                    "marketplace_details",
                    "collection_list",
                    "collection_details",
                    "daily_weekly",
                    "hero_select",
                    "hero_icon_small",
                    "standing_overlay",
                },
            )
            self.assertEqual(manifest["adapter"], {"id": "dooley-default", "version": 13})
            self.assertEqual(
                manifest["authoring"]["generator"]["id"],
                "deterministic-raster-v1",
            )
            self.assertFalse(manifest["authoring"]["asset_policy"]["aigc_allowed"])
            self.assertEqual(
                set(manifest["authoring"]["inputs"]),
                {"character", "background", "small_icon"},
            )
            self.assertEqual(
                manifest["authoring"]["outputs"]["portrait_gameplay"]["depends_on"],
                ["character"],
            )
            gameplay_portrait = Image.open(
                workspace / "assets" / "portrait_gameplay.png"
            ).convert("RGBA")
            self.assertNotEqual(
                gameplay_portrait.getchannel("A").getbbox(),
                (0, 0, *gameplay_portrait.size),
            )
            self.assertEqual(gameplay_portrait.getpixel((0, 0))[3], 0)
            self.assertEqual(
                manifest["authoring"]["outputs"]["hero_icon_small"]["depends_on"],
                ["small_icon"],
            )
            self.assertEqual(
                manifest["authoring"]["outputs"]["portrait_background"]["depends_on"],
                ["background"],
            )
            self.assertEqual(
                manifest["authoring"]["outputs"]["standing_overlay"]["depends_on"],
                ["character"],
            )
            standing = next(
                item
                for item in manifest["visual_replacements"]
                if item["slot"] == "standing_overlay"
            )
            self.assertEqual(standing["scale_multiplier"], 1.5)
            self.assertEqual(
                manifest["authoring"]["outputs"]["collection_details"]["depends_on"],
                ["background", "character"],
            )
            badge = manifest["authoring"]["outputs"]["hero_select"]
            self.assertEqual(
                badge["depends_on"],
                ["character", "badge_template"],
            )
            self.assertEqual(
                [layer["input"] for layer in badge["layers"]],
                [
                    "badge_template.base",
                    "character_shadow",
                    "badge_template.frame_upper",
                    "character",
                    "badge_template.frame_lower",
                ],
            )
            self.assertEqual(
                manifest["authoring"]["foreground"]["cast_shadow"]["composition"],
                "background -> character_shadow -> character",
            )
            self.assertEqual(badge["size"], [256, 256])
            self.assertEqual(
                badge["template"]["layer_order_back_to_front"],
                ["base", "frame_upper", "character", "frame_lower"],
            )
            self.assertFalse(badge["template"]["aigc"])
            self.assertFalse(
                manifest["authoring"]["asset_policy"]["aigc_allowed"]
            )

            standing_prop = root / "standing-prop.png"
            prop_image = Image.new("RGBA", (120, 240), (0, 0, 0, 0))
            ImageDraw.Draw(prop_image).rounded_rectangle(
                (90, 4, 112, 236),
                radius=12,
                fill=(225, 120, 30, 255),
            )
            prop_image.save(standing_prop)
            metadata_with_prop = {
                **metadata,
                "standing_prop": {
                    "origin": "user_supplied",
                    "aigc": False,
                },
            }
            with_prop = generate_pack(
                adapter_id="dooley-default",
                character=source,
                background=background,
                small_icon=icon,
                supplemental_inputs={"standing_prop": standing_prop},
                workspace_root=root / "prop-work",
                output_zip=root / "with-prop.zip",
                pack_id="test.dooley.generated.prop",
                name="Generated Dooley Prop Test",
                version="0.1.0",
                input_metadata=metadata_with_prop,
                badge_template_root=badge_template_root,
            )
            prop_manifest = json.loads(
                (Path(with_prop["workspace"]) / "mod.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                set(prop_manifest["authoring"]["inputs"]),
                {"character", "background", "small_icon", "standing_prop"},
            )
            self.assertEqual(
                prop_manifest["authoring"]["outputs"]["standing_overlay"]["depends_on"],
                ["character", "standing_prop"],
            )
            self.assertEqual(
                [
                    layer["input"]
                    for layer in prop_manifest["authoring"]["outputs"]["standing_overlay"]["layers"]
                ],
                ["character", "standing_prop"],
            )
            self.assertTrue(
                prop_manifest["authoring"]["outputs"]["standing_overlay"]["layers"][1]["flip_x"]
            )
            self.assertNotEqual(
                first["outputs"]["standing_overlay"]["sha256"],
                with_prop["outputs"]["standing_overlay"]["sha256"],
            )

            with zipfile.ZipFile(first["zip"]) as archive:
                names = set(archive.namelist())
            self.assertEqual(
                names,
                {
                    "mod.json",
                    "asset-index.json",
                    "assets/portrait_gameplay.png",
                    "assets/portrait_background.png",
                    "assets/portrait_small.png",
                    "assets/store_image.png",
                    "assets/marketplace_list.png",
                    "assets/marketplace_details.png",
                    "assets/collection_list.png",
                    "assets/collection_details.png",
                    "assets/daily_weekly.png",
                    "assets/hero_select.png",
                    "assets/hero_icon_small.png",
                    "assets/standing_overlay.png",
                },
            )

    def test_aigc_input_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("character.png", "background.png", "small-icon.png"):
                Image.new("RGBA", (64, 64), (20, 180, 80, 255)).save(root / name)
            with self.assertRaisesRegex(ValueError, "AIGC input is forbidden"):
                generate_pack(
                    adapter_id="dooley-default",
                    character=root / "character.png",
                    background=root / "background.png",
                    small_icon=root / "small-icon.png",
                    workspace_root=root / "work",
                    output_zip=root / "pack.zip",
                    pack_id="test.no-aigc",
                    name="No AIGC",
                    version="0.1.0",
                    input_metadata={"background": {"aigc": True}},
                )


if __name__ == "__main__":
    unittest.main()
