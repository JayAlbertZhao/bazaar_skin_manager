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
    generate_pack,
    remove_edge_connected_background,
)


class SkinPackBuilderTests(unittest.TestCase):
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

    def test_dooley_recipe_is_deterministic_and_excludes_badges(self) -> None:
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

            first = generate_pack(
                adapter_id="dooley-default",
                character=source,
                workspace_root=root / "first-work",
                output_zip=root / "first.zip",
                pack_id="test.dooley.generated",
                name="Generated Dooley Test",
                version="0.1.0",
            )
            second = generate_pack(
                adapter_id="dooley-default",
                character=source,
                workspace_root=root / "second-work",
                output_zip=root / "second.zip",
                pack_id="test.dooley.generated",
                name="Generated Dooley Test",
                version="0.1.0",
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

            workspace = Path(first["workspace"])
            manifest = json.loads((workspace / "mod.json").read_text(encoding="utf-8"))
            slots = {item["slot"] for item in manifest["visual_replacements"]}
            self.assertEqual(
                slots,
                {
                    "portrait_gameplay",
                    "store_image",
                    "marketplace_list",
                    "marketplace_details",
                    "collection_list",
                    "daily_weekly",
                },
            )
            self.assertNotIn("hero_select", slots)
            self.assertNotIn("hero_icon_small", slots)
            self.assertEqual(manifest["adapter"], {"id": "dooley-default", "version": 1})
            self.assertEqual(
                manifest["authoring"]["generator"]["id"],
                "deterministic-raster-v1",
            )

            with zipfile.ZipFile(first["zip"]) as archive:
                names = set(archive.namelist())
            self.assertEqual(
                names,
                {
                    "mod.json",
                    "asset-index.json",
                    "assets/portrait_gameplay.png",
                    "assets/store_image.png",
                    "assets/marketplace_list.png",
                    "assets/marketplace_details.png",
                    "assets/collection_list.png",
                    "assets/daily_weekly.png",
                },
            )


if __name__ == "__main__":
    unittest.main()
