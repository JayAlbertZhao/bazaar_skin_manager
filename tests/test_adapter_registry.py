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
    def test_project_registry_covers_every_catalog_default_skin(self):
        registry = AdapterRegistry.load(ROOT / "manager" / "adapters")
        catalog = json.loads(
            (ROOT / "manager" / "hero-catalog.json").read_text(encoding="utf-8")
        )
        expected = {
            (hero["id"], skin["id"])
            for hero in catalog["heroes"]
            for skin in hero["skins"]
        }
        actual = {(record.hero, record.skin) for record in registry.records}
        self.assertEqual(actual, expected)
        for record in registry.records:
            recipe = record.payload.get("authoring_recipe") or {}
            self.assertEqual(recipe.get("id"), "deterministic-raster-v1")
            self.assertEqual(recipe.get("version"), 2)
            self.assertEqual(
                set(recipe.get("outputs") or {}),
                {item["id"] for item in catalog["visual_slots"]},
            )

    def test_authoring_recipe_inheritance_applies_narrow_overrides(self):
        registry = AdapterRegistry.load(ROOT / "manager" / "adapters")
        dooley = registry.find_by_id("dooley-default")
        vanessa = registry.find_by_id("vanessa-default")
        self.assertIsNotNone(dooley)
        self.assertIsNotNone(vanessa)
        self.assertEqual(
            vanessa.payload["authoring_recipe"]["outputs"]["store_image"],
            dooley.payload["authoring_recipe"]["outputs"]["store_image"],
        )
        self.assertEqual(
            vanessa.payload["authoring_recipe"]["outputs"]["hero_select"]["size"],
            [512, 512],
        )
        self.assertIsNone(
            vanessa.payload["authoring_recipe"]["foreground"]["cast_shadow_lasso"]
        )

    def test_build_24720155_and_the_dragons_default_are_declared(self):
        registry = AdapterRegistry.load(ROOT / "manager" / "adapters")
        for record in registry.records:
            self.assertIn("24720155", record.supported_builds)

        dragons = registry.find("Hero8", "Skin_DRA_01/A")
        self.assertIsNotNone(dragons)
        self.assertEqual(dragons.skin_name_contains, "DRA_01a")
        deployments = {
            item["slot"]: item.get("deployment") or {}
            for item in dragons.payload["visual_replacements"]
        }
        self.assertEqual(
            deployments["hero_select"]["asset_name"],
            "TheDragons",
        )
        self.assertEqual(
            deployments["hero_icon_small"]["asset_name"],
            "Icon_FlatRough_DRA_TUI",
        )
        self.assertEqual(
            deployments["store_image"]["asset_name"],
            "Skin_DRA_01a_StoreImage_TUI",
        )

    def test_all_eight_heroes_have_exact_voice_route_metadata(self):
        registry = AdapterRegistry.load(ROOT / "manager" / "adapters")
        base_catalog = json.loads(
            (ROOT / "manager" / "hero-catalog.json").read_text(encoding="utf-8")
        )
        enriched = enrich_catalog(
            base_catalog,
            registry,
            game_dir=None,
            build_id="24720155",
        )
        self.assertTrue(all(hero["audio_supported"] for hero in enriched["heroes"]))
        route_catalog = json.loads(
            (ROOT / "manager" / "audio-route-catalog.json").read_text(
                encoding="utf-8"
            )
        )
        heroes = {item["hero"]: item for item in route_catalog["heroes"]}
        self.assertEqual(
            set(heroes),
            {"Mak", "Vanessa", "Pygmalien", "Dooley", "Jules", "Stelle", "Karnok", "Hero8"},
        )
        self.assertEqual(len(route_catalog["menu_routes"]), 2)
        self.assertEqual(heroes["Karnok"]["event_folder"], "Karnnok")
        self.assertTrue(
            all(
                route["event_path"].startswith("event:/VO/Hero/Karnnok/")
                for route in heroes["Karnok"]["routes"]
            )
        )
        for hero, entry in heroes.items():
            self.assertEqual(len(entry["routes"]), 17)
            self.assertEqual(
                len(
                    {
                        (
                            route["event_guid"],
                            tuple(
                                (item["parameter"], item["label"])
                                for item in route["selectors"]
                            ),
                        )
                        for route in entry["routes"]
                    }
                ),
                17,
            )
            adapter = next(record for record in registry.records if record.hero == hero)
            self.assertTrue(
                adapter.payload.get("audio_template")
                or adapter.payload.get("audio_template_ref") == route_catalog["id"]
            )

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
