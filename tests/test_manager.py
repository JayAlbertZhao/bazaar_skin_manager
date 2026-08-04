from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "bazaar_skin_manager.py"
SPEC = importlib.util.spec_from_file_location("bazaar_skin_manager", MODULE_PATH)
manager = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = manager
SPEC.loader.exec_module(manager)


def write_pack(root: Path) -> Path:
    pack = root / "pack"
    assets = pack / "assets"
    assets.mkdir(parents=True)
    hero_select = assets / "hero_select.png"
    small_icon = assets / "hero_icon_small.png"
    Image.new("RGBA", (32, 32), (10, 20, 30, 255)).save(hero_select)
    Image.new("RGBA", (16, 16), (30, 20, 10, 255)).save(small_icon)
    manifest = {
        "schema_version": 1,
        "id": "example.mak.default",
        "name": "Example external skin",
        "version": "1.0.0",
        "enabled": True,
        "target": {
            "game": "the-bazaar",
            "hero": "Mak",
            "skin": "Skin_MAK_01/A",
            "skin_name_contains": "MAK_01a",
        },
        "visual_replacements": [
            {
                "slot": "hero_select",
                "file": "assets/hero_select.png",
                "direct_only": True,
                "match_names": [],
            },
            {
                "slot": "hero_icon_small",
                "file": "assets/hero_icon_small.png",
                "match_names": ["Icon_FlatRough_MAK_TUI"],
            },
        ],
    }
    (pack / "mod.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    index = {
        "schema_version": 1,
        "files": {
            str(path.relative_to(pack)).replace("\\", "/"): {
                "sha256": manager.sha256_file(path),
                "bytes": path.stat().st_size,
            }
            for path in (hero_select, small_icon)
        },
    }
    (pack / "asset-index.json").write_text(
        json.dumps(index, indent=2) + "\n",
        encoding="utf-8",
    )
    return pack


def write_game(root: Path) -> Path:
    game = root / "game"
    (game / "TheBazaar_Data" / "Managed").mkdir(parents=True)
    (game / "TheBazaar.exe").write_bytes(b"fixture")
    core = game / "BepInEx" / "core"
    core.mkdir(parents=True)
    (core / "BepInEx.dll").write_bytes(b"fixture")
    return game


class ManagerTests(unittest.TestCase):
    def test_install_many_keeps_two_professions_active_and_reversible(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            mak_pack = write_pack(root / "mak")
            dooley_pack = write_pack(root / "dooley")
            dooley_manifest_path = dooley_pack / "mod.json"
            dooley_manifest = json.loads(
                dooley_manifest_path.read_text(encoding="utf-8")
            )
            dooley_manifest["id"] = "example.dooley.default"
            dooley_manifest["target"].update(
                hero="Dooley",
                skin="Skin_DOO_01/A",
                skin_name_contains="DOO_01a",
            )
            dooley_manifest["visual_replacements"] = []
            dooley_manifest_path.write_text(
                json.dumps(dooley_manifest, indent=2) + "\n",
                encoding="utf-8",
            )
            game = write_game(root)
            runtime = root / "BazaarSkinManager.Runtime.dll"
            runtime.write_bytes(b"runtime")
            local = root / "local"
            with (
                mock.patch.dict("os.environ", {"LOCALAPPDATA": str(local)}),
                mock.patch.object(manager, "native_patch_specs", return_value=[]),
            ):
                record = manager.install_many(
                    runtime,
                    [mak_pack, dooley_pack],
                    manager.explicit_install(game),
                )
                self.assertEqual(record["schema_version"], 4)
                self.assertEqual(
                    {item["id"] for item in record["packs"]},
                    {"example.mak.default", "example.dooley.default"},
                )
                self.assertTrue(manager.installation_diagnostics()["healthy"])
                deployed = [Path(item["path"]) for item in record["packs"]]
                removed = manager.uninstall()
                self.assertTrue(all(str(path) in removed for path in deployed))
                self.assertTrue(all(not path.exists() for path in deployed))

    def test_install_many_rejects_duplicate_profession_skin_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = write_pack(root / "first")
            second = write_pack(root / "second")
            manifest_path = second / "mod.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["id"] = "example.mak.alternate"
            manifest_path.write_text(
                json.dumps(manifest, indent=2) + "\n",
                encoding="utf-8",
            )
            game = write_game(root)
            runtime = root / "BazaarSkinManager.Runtime.dll"
            runtime.write_bytes(b"runtime")
            with (
                mock.patch.dict(
                    "os.environ",
                    {"LOCALAPPDATA": str(root / "local")},
                ),
                mock.patch.object(manager, "native_patch_specs", return_value=[]),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "duplicate enabled skin target",
                ):
                    manager.install_many(
                        runtime,
                        [first, second],
                        manager.explicit_install(game),
                    )

    def test_install_many_records_spine_requests_in_same_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = write_pack(root)
            game = write_game(root)
            runtime = root / "BazaarSkinManager.Runtime.dll"
            runtime.write_bytes(b"runtime")
            request = {"schema_version": 1, "adapter_id": "mak-default"}
            normalized = {
                **request,
                "deployed_bundles": [{"target": "skin_mak_01_assets_all.bundle"}],
            }
            spine = mock.Mock()
            spine.spine_patch_plan_issues.return_value = []
            spine.prepare_spine_native_patches.side_effect = (
                lambda requests, _game, _staging, prepared: (
                    prepared,
                    [normalized],
                )
            )
            with (
                mock.patch.dict(
                    "os.environ",
                    {"LOCALAPPDATA": str(root / "local")},
                ),
                mock.patch.object(manager, "native_patch_specs", return_value=[]),
                mock.patch.object(manager, "_spine_module", return_value=spine),
            ):
                record = manager.install_many(
                    runtime,
                    [pack],
                    manager.explicit_install(game),
                    spine_requests=[request],
                )
            self.assertEqual(record["schema_version"], 4)
            self.assertEqual(record["spine_replacements"], [normalized])
            spine.prepare_spine_native_patches.assert_called_once()

    def test_redeploying_skin_packs_preserves_existing_spine_requests(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = write_pack(root)
            game = write_game(root)
            runtime = root / "BazaarSkinManager.Runtime.dll"
            runtime.write_bytes(b"runtime")
            request = {"schema_version": 1, "adapter_id": "mak-default"}
            normalized = {
                **request,
                "deployed_bundles": [{"target": "skin_mak_01_assets_all.bundle"}],
            }
            spine = mock.Mock()
            spine.spine_patch_plan_issues.return_value = []
            spine.prepare_spine_native_patches.side_effect = (
                lambda requests, _game, _staging, prepared: (
                    prepared,
                    [normalized],
                )
            )
            with (
                mock.patch.dict(
                    "os.environ",
                    {"LOCALAPPDATA": str(root / "local")},
                ),
                mock.patch.object(manager, "native_patch_specs", return_value=[]),
                mock.patch.object(manager, "_spine_module", return_value=spine),
            ):
                first = manager.install_many(
                    runtime,
                    [pack],
                    manager.explicit_install(game),
                    spine_requests=[request],
                )
                deployed_pack = Path(first["packs"][0]["path"])
                redeploy_pack = root / "redeploy-pack"
                shutil.copytree(deployed_pack, redeploy_pack)
                second = manager.install_many(
                    runtime,
                    [redeploy_pack],
                    manager.explicit_install(game),
                )

            self.assertEqual(second["spine_replacements"], [normalized])
            self.assertEqual(spine.prepare_spine_native_patches.call_count, 2)

    def test_missing_target_adapter_reports_one_actionable_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            pack = write_pack(Path(temp))
            manifest_path = pack / "mod.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["target"]["hero"] = "UnknownHero"
            manifest["target"]["skin"] = "Skin_UNK_01/A"
            manifest["adapter"] = {"id": "unknown-default", "version": 1}
            manifest_path.write_text(
                json.dumps(manifest, indent=2) + "\n",
                encoding="utf-8",
            )

            errors = manager.validate_pack(pack)

            self.assertEqual(
                sum("no verified adapter for target" in item for item in errors),
                1,
            )
            self.assertTrue(any("update Skin Manager" in item for item in errors))
            self.assertFalse(any("slot is not declared" in item for item in errors))

    def test_adapter_claim_binds_visual_contract_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            pack = write_pack(Path(temp))
            manifest_path = pack / "mod.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["adapter"] = {"id": "mak-default", "version": 1}
            manifest_path.write_text(
                json.dumps(manifest, indent=2) + "\n",
                encoding="utf-8",
            )
            errors = manager.validate_pack(pack)
            self.assertTrue(
                any("deployment does not match verified adapter" in item for item in errors)
            )

    def test_native_patch_specs_expand_additional_deployments(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            pack = write_pack(Path(temp))
            manifest_path = pack / "mod.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            replacement = next(
                item
                for item in manifest["visual_replacements"]
                if item["slot"] == "hero_icon_small"
            )
            replacement["deployment"] = {
                "mode": manager.PRELOAD_TEXTURE_MODE,
                "target": "TheBazaar_Data/first-icon.bundle",
                "asset_name": "Icon_FlatRough_MAK_TUI",
                "unity_version": "6000.3.11f1",
                "target_size": [256, 256],
                "supported_original_sha256": ["0" * 64],
            }
            replacement["additional_deployments"] = [
                {
                    **replacement["deployment"],
                    "target": "TheBazaar_Data/second-icon.bundle",
                    "asset_name": "SecondIcon",
                }
            ]
            manifest_path.write_text(
                json.dumps(manifest, indent=2) + "\n",
                encoding="utf-8",
            )

            specs = [
                item
                for item in manager.native_patch_specs(pack)
                if item["slot"] == "hero_icon_small"
            ]

            self.assertEqual(len(specs), 2)
            self.assertEqual(
                {item["deployment"]["asset_name"] for item in specs},
                {"Icon_FlatRough_MAK_TUI", "SecondIcon"},
            )
            self.assertTrue(
                all("additional_deployments" not in item for item in specs)
            )

    def _native_patch_fixture(self, root: Path) -> tuple[Path, Path, Path]:
        pack = write_pack(root)
        manifest_path = pack / "mod.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        replacement = next(
            item
            for item in manifest["visual_replacements"]
            if item["slot"] == "hero_icon_small"
        )
        target_relative = Path("TheBazaar_Data") / "native-icon.bundle"
        original = b"original-unity-bundle"
        replacement["deployment"] = {
            "mode": "preload_unity_texture2d",
            "target": target_relative.as_posix(),
            "asset_name": "Icon_FlatRough_MAK_TUI",
            "unity_version": "6000.3.11f1",
            "target_size": [256, 256],
            "supported_original_sha256": [
                hashlib.sha256(original).hexdigest()
            ],
        }
        manifest_path.write_text(
            json.dumps(manifest, indent=2) + "\n",
            encoding="utf-8",
        )
        game = write_game(root)
        target = game / target_relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(original)
        catalog = (
            game
            / "TheBazaar_Data"
            / "StreamingAssets"
            / "aa"
            / "catalog.bin"
        )
        catalog.parent.mkdir(parents=True, exist_ok=True)
        catalog.write_bytes(
            target.name.encode("utf-8")
            + (b"\x00" * 59)
            + bytes.fromhex("11111111")[::-1]
        )
        runtime = root / "BazaarSkinManager.Runtime.dll"
        runtime.write_bytes(b"runtime")
        return pack, game, runtime

    def test_parse_libraryfolders(self) -> None:
        text = r'''
        "libraryfolders"
        {
            "0" { "path" "C:\\Program Files (x86)\\Steam" }
            "1" { "path" "D:\\SteamLibrary" }
        }
        '''
        self.assertEqual(
            manager.parse_libraryfolders(text)[-1],
            Path(r"D:\SteamLibrary"),
        )

    def test_common_steam_locations_cover_every_drive_layout(self) -> None:
        locations = manager.common_steam_locations(
            [Path("C:/"), Path("E:/")]
        )
        self.assertIn(Path(r"C:\SteamLibrary"), locations)
        self.assertIn(Path(r"E:\Program Files (x86)\Steam"), locations)

    def test_detect_install_from_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            steamapps = root / "steamapps"
            game = steamapps / "common" / "The Bazaar"
            game.mkdir(parents=True)
            (game / "TheBazaar.exe").write_bytes(b"fixture")
            (game / "TheBazaar_Data").mkdir()
            (steamapps / f"appmanifest_{manager.APP_ID}.acf").write_text(
                '"AppState" { "installdir" "The Bazaar" "buildid" "12345" }',
                encoding="utf-8",
            )
            installs = manager.detect_installs([root])
            self.assertEqual(len(installs), 1)
            self.assertTrue(installs[0].complete)
            self.assertEqual(installs[0].build_id, "12345")

    def test_launch_game_uses_steam_applaunch(self) -> None:
        install = manager.GameInstall(
            Path(r"E:\SteamLibrary\steamapps\common\The Bazaar"),
            None,
            "12345",
            True,
        )
        steam = Path(r"C:\Program Files (x86)\Steam\steam.exe")
        with (
            mock.patch.object(
                manager,
                "preferred_game_install",
                return_value=install,
            ),
            mock.patch.object(
                manager,
                "find_steam_executable",
                return_value=steam,
            ),
            mock.patch.object(manager.subprocess, "Popen") as popen,
        ):
            result = manager.launch_game()
        popen.assert_called_once_with(
            [str(steam), "-applaunch", manager.APP_ID],
            cwd=str(steam.parent),
        )
        self.assertEqual(result["method"], "steam_executable")

    def test_external_pack_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            self.assertEqual(manager.validate_pack(write_pack(Path(temp))), [])

    def test_pack_hash_tampering_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            pack = write_pack(Path(temp))
            (pack / "assets" / "hero_icon_small.png").write_bytes(b"changed")
            self.assertIn(
                "hash mismatch: assets/hero_icon_small.png",
                manager.validate_pack(pack),
            )

    def test_direct_only_slot_requires_explicit_marker(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            pack = write_pack(Path(temp))
            manifest_path = pack / "mod.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["visual_replacements"][0].pop("direct_only")
            manifest_path.write_text(
                json.dumps(manifest, indent=2) + "\n",
                encoding="utf-8",
            )
            self.assertIn(
                "missing match_names for hero_select",
                manager.validate_pack(pack),
            )

    def test_cli_requires_an_external_pack_for_pack_commands(self) -> None:
        with self.assertRaisesRegex(ValueError, "--pack is required"):
            manager.choose_pack(argparse.Namespace(pack=None))

    def test_install_and_uninstall_are_reversible(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = write_pack(root)
            game = write_game(root)
            runtime = root / "BazaarSkinManager.Runtime.dll"
            runtime.write_bytes(b"runtime")
            local = root / "local"
            with (
                mock.patch.dict("os.environ", {"LOCALAPPDATA": str(local)}),
                mock.patch.object(manager, "native_patch_specs", return_value=[]),
            ):
                record = manager.install(
                    runtime,
                    pack,
                    manager.explicit_install(game),
                )
                self.assertEqual(record["schema_version"], 4)
                self.assertEqual(len(record["packs"]), 1)
                self.assertEqual(
                    record["manager"]["version"],
                    manager.MANAGER_VERSION,
                )
                self.assertIsNone(record["runtime"]["version"])
                self.assertEqual(record["pack"]["version"], "1.0.0")
                self.assertEqual(
                    record["runtime"]["sha256"],
                    manager.sha256_file(runtime),
                )
                plugin = Path(record["plugin"]["path"])
                deployed_pack = Path(record["pack"]["path"])
                compatibility = Path(record["runtime_compatibility"]["path"])
                runtime_config = Path(record["runtime_config"]["path"])
                self.assertTrue(plugin.is_file())
                self.assertTrue(deployed_pack.is_dir())
                self.assertTrue(compatibility.is_file())
                self.assertTrue(runtime_config.is_file())
                self.assertIn(
                    f"ModsRoot = {(local / 'BazaarSkinManager' / 'TheBazaar' / 'mods').resolve()}",
                    runtime_config.read_text(encoding="utf-8"),
                )
                removed = manager.uninstall()
                self.assertIn(str(plugin), removed)
                self.assertIn(str(deployed_pack), removed)
                self.assertIn(str(compatibility), removed)
                self.assertFalse(plugin.exists())
                self.assertFalse(deployed_pack.exists())

    def test_runtime_metadata_version_is_independent_from_pack(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = write_pack(root)
            game = write_game(root)
            runtime = root / "BazaarSkinManager.Runtime.dll"
            runtime.write_bytes(b"runtime")
            (root / "runtime-build.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "component": "runtime-adapter",
                        "version": "7.4.2",
                        "sha256": manager.sha256_file(runtime),
                    }
                ),
                encoding="utf-8",
            )
            with (
                mock.patch.dict(
                    "os.environ",
                    {"LOCALAPPDATA": str(root / "local")},
                ),
                mock.patch.object(
                    manager,
                    "native_patch_specs",
                    return_value=[],
                ),
            ):
                record = manager.install(
                    runtime,
                    pack,
                    manager.explicit_install(game),
                )
                diagnostics = manager.installation_diagnostics()
            self.assertEqual(record["manager"]["version"], manager.MANAGER_VERSION)
            self.assertEqual(record["runtime"]["version"], "7.4.2")
            self.assertEqual(record["pack"]["version"], "1.0.0")
            self.assertEqual(
                diagnostics["components"]["runtime"]["version"],
                "7.4.2",
            )

    def test_native_texture_patch_is_backed_up_and_restored(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack, game, runtime = self._native_patch_fixture(root)
            target = game / "TheBazaar_Data" / "native-icon.bundle"
            original = target.read_bytes()
            local = root / "local"

            def fake_patcher(_source, output, _image, **_kwargs):
                output.write_bytes(b"prepatched-unity-bundle")
                return {
                    "output_sha256": manager.sha256_file(output),
                    "source_crc32": "11111111",
                    "output_crc32": "22222222",
                }

            with (
                mock.patch.dict("os.environ", {"LOCALAPPDATA": str(local)}),
                mock.patch.object(
                    manager,
                    "_load_bundle_patcher",
                    return_value=fake_patcher,
                ),
            ):
                record = manager.install(
                    runtime,
                    pack,
                    manager.explicit_install(game),
                )
                patch = record["native_patches"][0]
                catalog_patch = record["native_catalog_patch"]
                self.assertEqual(
                    target.read_bytes(),
                    b"prepatched-unity-bundle",
                )
                self.assertEqual(Path(patch["backup"]).read_bytes(), original)
                self.assertEqual(
                    Path(catalog_patch["target"]).read_bytes()[-4:],
                    bytes.fromhex("22222222")[::-1],
                )
                self.assertTrue(manager.installation_diagnostics()["healthy"])
                manager.uninstall()
            self.assertEqual(target.read_bytes(), original)
            self.assertFalse(Path(patch["backup"]).exists())
            self.assertEqual(
                Path(catalog_patch["target"]).read_bytes()[-4:],
                bytes.fromhex("11111111")[::-1],
            )
            self.assertFalse(Path(catalog_patch["backup"]).exists())

    def test_uninstall_does_not_overwrite_a_steam_updated_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack, game, runtime = self._native_patch_fixture(root)
            target = game / "TheBazaar_Data" / "native-icon.bundle"
            local = root / "local"

            def fake_patcher(_source, output, _image, **_kwargs):
                output.write_bytes(b"prepatched-unity-bundle")
                return {
                    "output_sha256": manager.sha256_file(output),
                    "source_crc32": "11111111",
                    "output_crc32": "22222222",
                }

            with (
                mock.patch.dict("os.environ", {"LOCALAPPDATA": str(local)}),
                mock.patch.object(
                    manager,
                    "_load_bundle_patcher",
                    return_value=fake_patcher,
                ),
            ):
                record = manager.install(
                    runtime,
                    pack,
                    manager.explicit_install(game),
                )
                backup = Path(record["native_patches"][0]["backup"])
                target.write_bytes(b"steam-updated-bundle")
                manager.uninstall()
            self.assertEqual(target.read_bytes(), b"steam-updated-bundle")
            self.assertFalse(backup.exists())

    def test_install_preserves_unrelated_bepinex_plugins(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = write_pack(root)
            game = write_game(root)
            other = (
                game
                / "BepInEx"
                / "plugins"
                / "UnrelatedPlugin"
                / "UnrelatedPlugin.dll"
            )
            other.parent.mkdir(parents=True)
            other.write_bytes(b"community-plugin")
            runtime = root / "BazaarSkinManager.Runtime.dll"
            runtime.write_bytes(b"runtime")
            with (
                mock.patch.dict(
                    "os.environ",
                    {"LOCALAPPDATA": str(root / "local")},
                ),
                mock.patch.object(manager, "native_patch_specs", return_value=[]),
            ):
                manager.install(
                    runtime,
                    pack,
                    manager.explicit_install(game),
                )
                manager.uninstall()
            self.assertEqual(other.read_bytes(), b"community-plugin")

    def test_doctor_detects_game_update_and_recovers_after_redeploy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            pack = write_pack(root)
            game = write_game(root)
            executable = game / "TheBazaar.exe"
            runtime = root / "BazaarSkinManager.Runtime.dll"
            runtime.write_bytes(b"runtime")
            install = manager.GameInstall(game, None, "24001960", True)
            with (
                mock.patch.dict(
                    "os.environ",
                    {"LOCALAPPDATA": str(root / "local")},
                ),
                mock.patch.object(manager, "detect_installs", return_value=[]),
                mock.patch.object(manager, "native_patch_specs", return_value=[]),
            ):
                manager.install(runtime, pack, install)
                self.assertTrue(manager.installation_diagnostics()["healthy"])
                executable.write_bytes(b"unknown-steam-update")
                changed = manager.installation_diagnostics()
                self.assertTrue(changed["update_required"])
                self.assertFalse(changed["healthy"])
                self.assertIn("plan-install", changed["repair_command"])
                manager.install(runtime, pack, install)
                repaired = manager.installation_diagnostics()
                self.assertTrue(repaired["healthy"])
                self.assertFalse(
                    repaired["checks"]["game_update_detected"]
                )

    def test_manager_storage_and_plugin_paths_are_generic(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with mock.patch.dict(
                "os.environ",
                {"LOCALAPPDATA": str(root)},
            ):
                self.assertEqual(
                    manager.manager_root(),
                    root / "BazaarSkinManager" / "TheBazaar" / "manager",
                )
                self.assertEqual(
                    manager.mods_root(),
                    root / "BazaarSkinManager" / "TheBazaar" / "mods",
                )


if __name__ == "__main__":
    unittest.main()
