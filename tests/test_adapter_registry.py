import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

from adapter_registry import (  # noqa: E402
    AdapterRegistry,
    discover_installed_skin_ids,
    enrich_catalog,
)


def write_adapter(path: Path, adapter_id: str, hero: str, skin: str) -> None:
    code_variant = skin.split("Skin_", 1)[1].replace("/", "").casefold()
    payload = {
        "schema_version": 1,
        "id": adapter_id,
        "adapter_version": 1,
        "supported_builds": ["24001960"],
        "target": {
            "game": "the-bazaar",
            "hero": hero,
            "skin": skin,
            "skin_name_contains": code_variant,
        },
        "visual_replacements": [],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


class AdapterRegistryTests(unittest.TestCase):
    def test_registry_indexes_multiple_hero_skin_adapters(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_adapter(root / "one.json", "one", "HeroOne", "Skin_ONE_01/A")
            write_adapter(root / "two.json", "two", "HeroTwo", "Skin_TWO_02/B")
            registry = AdapterRegistry.load(root)
            self.assertEqual(registry.find("heroone", "skin_one_01/a").adapter_id, "one")
            self.assertEqual(registry.find("HeroTwo", "Skin_TWO_02/B").adapter_id, "two")
            self.assertIsNone(registry.find("HeroTwo", "Skin_TWO_99/A"))

    def test_catalog_discovery_is_exact_and_reports_unmapped_skins(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            catalog_path = (
                root
                / "TheBazaar_Data"
                / "StreamingAssets"
                / "aa"
                / "catalog.bin"
            )
            catalog_path.parent.mkdir(parents=True)
            catalog_path.write_bytes(
                b"Skin_ONE_01/A\0Skin_ONE_02/B\0"
                b"Skin_ONE_02b_Portrait\0not_a_skin"
            )
            discovered = discover_installed_skin_ids(root)
            self.assertEqual(
                discovered,
                {"ONE": ["Skin_ONE_01/A", "Skin_ONE_02/B"]},
            )

            adapters = root / "adapters"
            adapters.mkdir()
            write_adapter(
                adapters / "one.json",
                "one-default",
                "HeroOne",
                "Skin_ONE_01/A",
            )
            registry = AdapterRegistry.load(adapters)
            base = {
                "heroes": [
                    {
                        "id": "HeroOne",
                        "asset_code": "ONE",
                        "display_name": "One",
                        "skins": [],
                    }
                ]
            }
            result = enrich_catalog(
                base,
                registry,
                game_dir=root,
                build_id="24001960",
            )
            skins = {item["id"]: item for item in result["heroes"][0]["skins"]}
            self.assertEqual(skins["Skin_ONE_01/A"]["deployment_status"], "supported")
            self.assertEqual(
                skins["Skin_ONE_02/B"]["deployment_status"],
                "detected_unmapped",
            )
            self.assertTrue(all(item["detected_from_game"] for item in skins.values()))

    def test_verified_adapter_fails_closed_on_game_build_change(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_adapter(root / "one.json", "one", "HeroOne", "Skin_ONE_01/A")
            registry = AdapterRegistry.load(root)
            self.assertEqual(
                registry.support_status("HeroOne", "Skin_ONE_01/A", "99999999"),
                "game_update_required",
            )


if __name__ == "__main__":
    unittest.main()
