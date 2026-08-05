from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

os.environ.setdefault(
    "BAZAAR_SPINE_MANAGER_HOME",
    str(Path(tempfile.gettempdir()) / "bazaar-spine-manager-tests"),
)

from spine_manager_core import (  # noqa: E402
    SpinePackage,
    SpinePlacement,
    SpineTarget,
    _record_original_backups,
    _restore_recorded_originals,
    _stage,
    _prepared_json,
    _rewrite_atlas,
    import_spine_package,
    prepare_spine_native_patches,
    targets,
)
from bazaar_skin_manager import GameInstall  # noqa: E402
from bazaar_spine_manager_ui import APP_VERSION, LOG_PATH, configure_logging  # noqa: E402
from spine_static_preview import render_setup_pose  # noqa: E402


class SpineManagerTests(unittest.TestCase):
    def create_package(self, root: Path, version: str = "4.2.43") -> Path:
        source = root / "source"
        source.mkdir()
        payload = {
            "skeleton": {
                "spine": version,
                "x": -10,
                "y": -20,
                "width": 100,
                "height": 200,
            },
            "bones": [{"name": "root", "x": 2, "y": -3}],
            "slots": [{"name": "body", "bone": "root", "attachment": "hero"}],
            "skins": [
                {
                    "name": "default",
                    "attachments": {
                        "body": {"hero": {"width": 32, "height": 32}}
                    },
                }
            ],
            "animations": {"walk": {"bones": {}}, "wave": {"slots": {}}},
        }
        (source / "hero.json").write_text(json.dumps(payload), encoding="utf-8")
        (source / "hero.atlas").write_text(
            "hero.png\nsize:32,32\nfilter:Linear,Linear\nscale:1\n"
            "hero\nbounds:0,0,32,32\n",
            encoding="utf-8",
        )
        Image.new("RGBA", (32, 32), (255, 0, 128, 255)).save(source / "hero.png")
        archive = root / "hero.zip"
        with zipfile.ZipFile(archive, "w") as output:
            for path in source.iterdir():
                output.write(path, path.name)
        return archive

    def test_imports_safe_spine_42_single_page_package(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with mock.patch("spine_manager_core._convert_spine_json") as converter:
                package = import_spine_package(
                    self.create_package(root), root / "workspace"
                )
            converter.assert_not_called()
            self.assertEqual(package.version, "4.2.43")
            self.assertIsNone(package.source_version)
            self.assertEqual(package.animations, ("walk", "wave"))
            self.assertEqual(package.skins, ("default",))
            self.assertEqual((package.width, package.height), (32, 32))

    def test_routes_spine_35_through_40_through_converter(self) -> None:
        for version in ("3.5.51", "3.6.53", "3.7.94", "3.8.99", "4.0.64"):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)

                def convert(source_json: Path, output_json: Path) -> dict:
                    converted = json.loads(source_json.read_text(encoding="utf-8"))
                    converted["skeleton"]["spine"] = "4.2.43"
                    output_json.parent.mkdir(parents=True, exist_ok=True)
                    output_json.write_text(json.dumps(converted), encoding="utf-8")
                    return converted

                with mock.patch(
                    "spine_manager_core._convert_spine_json", side_effect=convert
                ) as converter:
                    package = import_spine_package(
                        self.create_package(root, version), root / "workspace"
                    )
                converter.assert_called_once()
                self.assertEqual(package.version, "4.2.43")
                self.assertEqual(package.source_version, version)

    def test_imports_spine_41_multi_page_atlas_and_ignores_source_images(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            (source / "images").mkdir(parents=True)
            payload = {
                "skeleton": {"spine": "4.1.24", "width": 64, "height": 64},
                "bones": [
                    {"name": "root", "transform": "noRotationOrReflection"}
                ],
                "slots": [{"name": "body", "bone": "root", "attachment": "second"}],
                "skins": [
                    {
                        "name": "default",
                        "attachments": {
                            "body": {"second": {"width": 8, "height": 8}}
                        },
                    }
                ],
                "animations": {"idle-source": {}},
            }
            (source / "hero.json").write_text(json.dumps(payload), encoding="utf-8")
            (source / "hero.atlas").write_text(
                "page1.png\nsize:8,8\nfilter:Linear,Linear\nscale:0.5\n"
                "first\nbounds:0,1,8,7\n\n"
                "page2.png\nsize:8,6\nfilter:Linear,Linear\nscale:0.5\n"
                "second\nbounds:0,2,8,4\n",
                encoding="utf-8",
            )
            Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(source / "page1.png")
            Image.new("RGBA", (8, 6), (0, 255, 0, 255)).save(source / "page2.png")
            Image.new("RGBA", (4, 4), (0, 0, 255, 255)).save(source / "images" / "source.png")

            def convert(source_json: Path, output_json: Path) -> dict:
                converted = json.loads(source_json.read_text(encoding="utf-8"))
                converted["skeleton"]["spine"] = "4.2.43"
                for bone in converted["bones"]:
                    if "transform" in bone:
                        bone["inherit"] = bone.pop("transform")
                output_json.parent.mkdir(parents=True, exist_ok=True)
                output_json.write_text(json.dumps(converted), encoding="utf-8")
                return converted

            with mock.patch(
                "spine_manager_core._convert_spine_json", side_effect=convert
            ) as converter:
                package = import_spine_package(source, root / "workspace")

            converter.assert_called_once()
            self.assertEqual(package.version, "4.2.43")
            self.assertEqual(package.source_version, "4.1.24")
            converted = json.loads(package.json_path.read_text(encoding="utf-8"))
            self.assertEqual(
                converted["bones"][0]["inherit"], "noRotationOrReflection"
            )
            self.assertNotIn("transform", converted["bones"][0])
            self.assertEqual((package.width, package.height), (8, 14))
            self.assertEqual(package.atlas_scale, 0.5)
            atlas = package.atlas_path.read_text(encoding="utf-8")
            self.assertEqual(atlas.splitlines()[0], "atlas.png")
            self.assertIn("size:8,14", atlas)
            self.assertIn("bounds:0,10,8,4", atlas)
            self.assertNotIn("page2.png", atlas)
            with Image.open(package.texture_path) as image:
                self.assertEqual(image.getpixel((0, 0)), (255, 0, 0, 255))
                self.assertEqual(image.getpixel((0, 13)), (0, 255, 0, 255))

            pose = render_setup_pose(package)
            self.assertLessEqual(max(pose.image.size), 4096)

            text, prepared = _prepared_json(
                package, SpinePlacement(animation="idle-source")
            )
            self.assertEqual(prepared["skeleton"]["spine"], "4.2.43")
            self.assertEqual(json.loads(text)["skeleton"]["spine"], "4.2.43")

    def test_rejects_versions_outside_supported_conversion_range(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.create_package(root)
            source = root / "source"
            payload = json.loads((source / "hero.json").read_text(encoding="utf-8"))
            payload["skeleton"]["spine"] = "4.3.00"
            (source / "hero.json").write_text(json.dumps(payload), encoding="utf-8")
            with (
                mock.patch("spine_manager_core._convert_spine_json") as converter,
                self.assertRaisesRegex(ValueError, "版本不兼容.*3.5.*4.2"),
            ):
                import_spine_package(source, root / "workspace")
            converter.assert_not_called()

    def test_prepared_json_applies_root_offsets_and_idle_alias(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = import_spine_package(self.create_package(root), root / "workspace")
            text, payload = _prepared_json(
                package,
                SpinePlacement(
                    animation="wave",
                    root_x_offset=5,
                    root_y_offset=100,
                ),
            )
            self.assertEqual(payload["bones"][0]["x"], 7)
            self.assertEqual(payload["bones"][0]["y"], 97)
            self.assertEqual(payload["animations"]["idle"], payload["animations"]["wave"])
            self.assertEqual(json.loads(text)["bones"][0]["y"], 97)

    def test_rewrite_atlas_matches_unity_texture_and_pma(self) -> None:
        atlas = "hero.png\nsize:32,32\nfilter:Linear,Linear\nscale:1\nregion\n"
        rewritten = _rewrite_atlas(atlas, "Skin_MAK_01a.png")
        self.assertEqual(rewritten.splitlines()[0], "Skin_MAK_01a.png")
        self.assertIn("pma:true", rewritten.splitlines()[:8])

    def test_spine_patch_composes_after_existing_native_texture_patch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            game_root = root / "game"
            relative = "TheBazaar_Data/StreamingAssets/aa/skin_test.bundle"
            target_path = game_root / relative
            target_path.parent.mkdir(parents=True)
            target_path.write_bytes(b"original")
            staged_texture = root / "texture-patched.bundle"
            staged_texture.write_bytes(b"texture-patched")
            package = SpinePackage(
                root=root,
                json_path=root / "hero.json",
                atlas_path=root / "hero.atlas",
                texture_path=root / "hero.png",
                version="4.2.43",
                animations=("idle",),
                skins=("default",),
                atlas_scale=1.0,
                width=32,
                height=32,
            )
            target = SpineTarget(
                adapter_id="test-default",
                hero="Test",
                skin="Skin_TEST_01/A",
                prefix="Skin_TEST_01a",
                bundle_relative=relative,
                unity_version="6000.3.11f1",
                supported_original_sha256=(),
                supported_builds=(),
            )
            placement = SpinePlacement(animation="idle")
            prepared = [
                {
                    "slot": "store_image",
                    "slots": ["store_image"],
                    "target": str(target_path.resolve()),
                    "backup": str(root / "backup.bundle"),
                    "original_sha256": "a" * 64,
                    "patched_sha256": "b" * 64,
                    "original_crc32": "11111111",
                    "patched_crc32": "22222222",
                    "staged": str(staged_texture),
                    "asset_names": ["StoreImage"],
                    "mode": "preload_unity_texture2d",
                }
            ]

            def fake_patch(source, output, *_args, **_kwargs):
                self.assertEqual(source, staged_texture)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"texture-and-spine")
                return {
                    "source_crc32": 0x22222222,
                    "output_crc32": 0x33333333,
                    "automatic_scale": 1.0,
                    "final_scale": 1.0,
                    "output_sha256": "c" * 64,
                }

            game = GameInstall(
                game_dir=game_root,
                manifest=None,
                build_id=None,
                complete=True,
            )
            with (
                mock.patch(
                    "spine_manager_core._load_spine_request",
                    return_value=(package, target, placement),
                ),
                mock.patch(
                    "spine_manager_core.serialize_spine_request",
                    return_value={"schema_version": 1, "adapter_id": "test-default"},
                ),
                mock.patch(
                    "spine_manager_core._resolve_contract_prefix",
                    return_value=target.prefix,
                ),
                mock.patch("spine_manager_core.patch_bundle", side_effect=fake_patch),
            ):
                combined, normalized = prepare_spine_native_patches(
                    [{"schema_version": 1}],
                    game,
                    root / "staging",
                    prepared,
                )
            self.assertEqual(len(combined), 1)
            self.assertEqual(combined[0]["original_crc32"], "11111111")
            self.assertEqual(combined[0]["patched_crc32"], "33333333")
            self.assertEqual(combined[0]["mode"], "composed_native_bundle")
            self.assertEqual(normalized[0]["deployed_bundles"][0]["prefix"], target.prefix)

    def test_recorded_original_allows_direct_b_to_c_redeployment(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            game_root = root / "game"
            relative = "TheBazaar_Data/StreamingAssets/aa/StandaloneWindows64/skin_test.bundle"
            bundle = game_root / relative
            catalog = game_root / "TheBazaar_Data/StreamingAssets/aa/catalog.bin"
            bundle.parent.mkdir(parents=True)
            catalog.parent.mkdir(parents=True, exist_ok=True)
            original_bundle = b"spine-a"
            deployed_bundle = b"spine-b"
            replacement_bundle = b"spine-c"
            original_catalog = b"catalog-a"
            deployed_catalog = b"catalog-b"
            replacement_catalog = b"catalog-c"
            bundle.write_bytes(deployed_bundle)
            catalog.write_bytes(deployed_catalog)
            manager_bundle_backup = root / "manager-backups/skin_test.bundle"
            manager_catalog_backup = root / "manager-backups/catalog.bin"
            manager_bundle_backup.parent.mkdir(parents=True)
            manager_bundle_backup.write_bytes(original_bundle)
            manager_catalog_backup.write_bytes(original_catalog)
            digest = lambda value: hashlib.sha256(value).hexdigest()
            target = SpineTarget(
                adapter_id="test-default",
                hero="Test",
                skin="Skin_TEST_01/A",
                prefix="Skin_TEST_01a",
                bundle_relative=relative,
                unity_version="6000.3.11f1",
                supported_original_sha256=(digest(original_bundle),),
                supported_builds=(),
            )
            game = GameInstall(
                game_dir=game_root,
                manifest=None,
                build_id=None,
                complete=True,
            )
            result = {
                "native_patches": [
                    {
                        "target": str(bundle),
                        "backup": str(manager_bundle_backup),
                        "original_sha256": digest(original_bundle),
                        "patched_sha256": digest(deployed_bundle),
                        "spine": [{"adapter_id": target.adapter_id}],
                    }
                ],
                "native_catalog_patch": {
                    "target": str(catalog),
                    "backup": str(manager_catalog_backup),
                    "original_sha256": digest(original_catalog),
                    "patched_sha256": digest(deployed_catalog),
                },
            }
            fixed_backups = root / "fixed-backups"
            with mock.patch("spine_manager_core.ORIGINAL_BACKUP_ROOT", fixed_backups):
                directory = _record_original_backups(result, game, target)
                self.assertEqual((directory / bundle.name).read_bytes(), original_bundle)
                self.assertEqual((directory / "catalog.bin").read_bytes(), original_catalog)
                self.assertTrue((directory / "backup-record.json").is_file())
                with _restore_recorded_originals(
                    game,
                    target,
                    root / "redeploy-staging",
                ):
                    self.assertEqual(bundle.read_bytes(), original_bundle)
                    self.assertEqual(catalog.read_bytes(), original_catalog)
                    bundle.write_bytes(replacement_bundle)
                    catalog.write_bytes(replacement_catalog)
            self.assertEqual(bundle.read_bytes(), replacement_bundle)
            self.assertEqual(catalog.read_bytes(), replacement_catalog)

    def test_verified_default_targets_are_available(self) -> None:
        available = targets()
        self.assertEqual(
            {item.hero for item in available},
            {"Mak", "Vanessa", "Pygmalien", "Dooley", "Jules", "Stelle", "Karnok"},
        )
        mak = next(item for item in available if item.hero == "Mak")
        self.assertEqual(mak.prefix, "Skin_MAK_01a")
        self.assertTrue(mak.bundle_relative.endswith("skin_mak_01_assets_all.bundle"))
        karnok = next(item for item in available if item.hero == "Karnok")
        self.assertEqual(len(karnok.additional_bundles), 1)
        self.assertTrue(
            karnok.additional_bundles[0].bundle_relative.endswith(
                "skin_kar_01_creature_assets_all.bundle"
            )
        )

    def test_build_script_creates_requested_executable_name(self) -> None:
        build = (ROOT / "build-spine-manager.ps1").read_text(encoding="utf-8")
        self.assertIn('"--name", "TheBazaarSpineManager"', build)
        self.assertIn('"--self-test"', build)
        self.assertIn("spine-manager-build.json", build)
        self.assertIn('"--hidden-import", "spine_static_preview"', build)
        self.assertIn("manager\\spine-preview", build)
        self.assertIn("SpineSkeletonDataConverter.exe", build)
        self.assertIn("b2ca82e46f1f4ca463abf0ccfab32e3c01eb0dd89fc7289b6478f728ca8ed68a", build)
        self.assertIn('"--add-binary", "$converterExe;spine-converter"', build)

    def test_offline_static_preview_renders_without_web_player(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = import_spine_package(self.create_package(root), root / "workspace")
            pose = render_setup_pose(package)
            self.assertGreater(pose.image.width, 10)
            self.assertGreater(pose.image.height, 10)
            self.assertIsNotNone(pose.image.getbbox())
        source = (TOOLS / "bazaar_spine_manager_ui.py").read_text(encoding="utf-8")
        self.assertNotIn("SpinePreviewServer", source)
        self.assertNotIn("spine_preview_server", source)

    def test_hero_select_background_is_bundled(self) -> None:
        background = ROOT / "manager" / "spine-preview" / "hero-select-background.jpg"
        self.assertTrue(background.is_file())
        with Image.open(background) as image:
            self.assertEqual(image.size, (1920, 1080))

    def test_stage_progress_and_rotating_log_are_available(self) -> None:
        messages = []
        with _stage(messages.append, "测试阶段"):
            pass
        self.assertEqual(messages[0], "测试阶段…")
        self.assertTrue(messages[-1].startswith("测试阶段完成（"))
        configure_logging()
        self.assertTrue(LOG_PATH.is_file())
        self.assertEqual(APP_VERSION, "1.1.4")

    def test_background_exception_is_bound_before_tk_callback(self) -> None:
        source = (TOOLS / "bazaar_spine_manager_ui.py").read_text(encoding="utf-8")
        self.assertIn("lambda caught=error, stack=details", source)


if __name__ == "__main__":
    unittest.main()
