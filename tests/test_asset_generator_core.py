from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import asset_generator_core as generator  # noqa: E402
from skin_pack_builder import scaled_target_bounds  # noqa: E402


class AssetGeneratorCoreTests(unittest.TestCase):
    def test_character_scale_preserves_bottom_center_anchor(self) -> None:
        self.assertEqual(
            scaled_target_bounds((10, 20, 110, 220), 1.5),
            (-15, -80, 135, 220),
        )

    def _profile(self, root: Path) -> generator.GeneratorProfile:
        inputs = root / "inputs"
        inputs.mkdir()
        for name in ("character.png", "background.png", "small_icon.png"):
            Image.new("RGBA", (96, 96), (40, 90, 130, 255)).save(inputs / name)
        metadata = inputs / "metadata.json"
        metadata.write_text(
            json.dumps(
                {
                    "character": {"aigc": False, "authoritative_alpha": True},
                    "background": {"aigc": False},
                    "small_icon": {"aigc": False},
                }
            ),
            encoding="utf-8",
        )
        badge_root = root / "badge"
        badge_root.mkdir()
        profile_path = root / "profile.json"
        profile_path.write_text("{}", encoding="utf-8")
        return generator.GeneratorProfile(
            profile_path=profile_path,
            adapter_id="dooley-default",
            pack_id="test.generated.pack",
            name="Generated Pack",
            version="1.1.1",
            character=inputs / "character.png",
            background=inputs / "background.png",
            small_icon=inputs / "small_icon.png",
            input_metadata=metadata,
            badge_template_root=badge_root,
            workspace_root=root / "workspaces",
            output_zip=root / "release" / "pack.zip",
            game_dir=None,
        )

    def test_generate_cleans_only_dedicated_pack_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            profile = replace(
                self._profile(Path(temp)),
                background_offset_x=24,
                background_offset_y=-11,
                background_scale=1.3,
            )
            stale = profile.generated_workspace / "assets" / "stale.png"
            stale.parent.mkdir(parents=True)
            stale.write_bytes(b"stale")
            profile.output_zip.parent.mkdir(parents=True)
            profile.output_zip.write_bytes(b"zip")
            with mock.patch.object(
                generator,
                "generate_pack",
                return_value={
                    "workspace": str(profile.generated_workspace),
                    "zip": str(profile.output_zip),
                    "zip_sha256": "abc",
                },
            ) as build:
                result = generator.generate_assets(profile)
            self.assertFalse(stale.exists())
            self.assertEqual(result["zip_sha256"], "abc")
            self.assertEqual(build.call_args.kwargs["character"], profile.character)
            self.assertEqual(build.call_args.kwargs["workspace_root"], profile.workspace_root)
            self.assertEqual(build.call_args.kwargs["background_offset"], (24, -11))
            self.assertEqual(build.call_args.kwargs["background_scale"], 1.3)

    def test_authoring_targets_require_a_deterministic_recipe(self) -> None:
        records = generator.authoring_adapters()
        self.assertEqual(
            {record.adapter_id for record in records},
            {
                "dooley-default",
                "jules-default",
                "karnok-default",
                "mak-default",
                "pygmalien-default",
                "stelle-default",
                "the-dragons-default",
                "vanessa-default",
            },
        )

    def test_workspace_edit_profile_restores_identity_inputs_and_adjustments(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = generator.StudioWorkspace.create(
                "test.edit.pack",
                root=root / "library",
                name="Same Skin Name",
                version="0.7.0",
                hero="Dooley",
                skin="Skin_DOO_01/A",
                skin_name_contains="DOO_01a",
            )
            inputs = workspace.directory / "authoring" / "inputs"
            inputs.mkdir(parents=True)
            character = inputs / "character.png"
            icon_source = inputs / "small_icon_source.png"
            small_icon = inputs / "small_icon.png"
            for path in (character, icon_source, small_icon):
                Image.new("RGBA", (96, 96), (40, 90, 130, 255)).save(path)
            workspace.state["authoring"] = {
                "generator": {"adapter_id": "dooley-default"},
                "inputs": {
                    "character": {
                        "workspace_file": "authoring/inputs/character.png",
                        "sha256": "generated",
                        "bytes": 123,
                        "image_size": [96, 96],
                        "origin": "user_supplied",
                        "aigc": False,
                        "authoritative_alpha": True,
                    },
                    "small_icon": {
                        "workspace_file": "authoring/inputs/small_icon.png",
                        "origin": "deterministic_derivative",
                        "preset": "outline",
                        "aigc": False,
                    },
                    "small_icon_source": {
                        "workspace_file": "authoring/inputs/small_icon_source.png",
                        "origin": "user_supplied",
                        "aigc": False,
                    },
                },
                "adjustments": {
                    "character_canvas": [-12, 66],
                    "character_scale": 1.25,
                    "background": {
                        "offset": [21, -9],
                        "scale": 1.4,
                        "fit": "cover",
                    },
                    "per_output": {"hero_select": [-19, 71]},
                },
            }
            workspace.save()
            badge = root / "badges"
            badge.mkdir()

            profile = generator.profile_for_workspace_edit(
                workspace,
                profile_path=root / "edit" / "profile.json",
                badge_template_root=badge,
                workspace_root=root / "generated",
                output_zip=root / "exports" / "pack.zip",
            )

            self.assertEqual(profile.pack_id, "test.edit.pack")
            self.assertEqual(profile.name, "Same Skin Name")
            self.assertEqual(profile.version, "0.7.0")
            self.assertEqual(profile.adapter_id, "dooley-default")
            self.assertNotEqual(profile.character, character.resolve())
            self.assertEqual(profile.character.parent, (root / "edit" / "inputs").resolve())
            self.assertEqual(profile.character.read_bytes(), character.read_bytes())
            self.assertNotEqual(profile.small_icon_source, icon_source.resolve())
            self.assertEqual(profile.small_icon_source.read_bytes(), icon_source.read_bytes())
            self.assertEqual(profile.small_icon_mode, "outline")
            self.assertEqual((profile.character_offset_x, profile.character_offset_y), (-12, 66))
            self.assertEqual(profile.character_scale, 1.25)
            self.assertEqual((profile.background_offset_x, profile.background_offset_y), (21, -9))
            self.assertEqual(profile.background_scale, 1.4)
            self.assertEqual(profile.output_offsets, {"hero_select": (-19, 71)})
            metadata = json.loads(profile.input_metadata.read_text(encoding="utf-8"))
            self.assertTrue(metadata["character"]["authoritative_alpha"])
            self.assertNotIn("sha256", metadata["character"])

    def test_workspace_edit_profile_keeps_legacy_identity_without_reusing_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = generator.StudioWorkspace.create(
                "legacy.edit.pack",
                root=root / "library",
                name="Legacy Skin",
                hero="Mak",
                skin="Skin_MAK_01/A",
                skin_name_contains="MAK_01a",
            )
            workspace.import_pil_image(
                "portrait_gameplay",
                Image.new("RGBA", (96, 96), (40, 90, 130, 255)),
            )
            badge = root / "badges"
            badge.mkdir()

            profile = generator.profile_for_workspace_edit(
                workspace,
                profile_path=root / "edit" / "profile.json",
                badge_template_root=badge,
                workspace_root=root / "generated",
                output_zip=root / "exports" / "pack.zip",
            )

            self.assertEqual(profile.pack_id, "legacy.edit.pack")
            self.assertEqual(profile.name, "Legacy Skin")
            self.assertEqual(profile.adapter_id, "mak-default")
            self.assertFalse(profile.character.is_file())
            self.assertNotEqual(profile.character, workspace.visual_path("portrait_gameplay"))

    def test_workspace_edit_profile_recovers_archived_input_by_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = generator.StudioWorkspace.create(
                "recover.edit.pack",
                root=root / "library",
                name="Recover Skin",
                hero="Dooley",
                skin="Skin_DOO_01/A",
                skin_name_contains="DOO_01a",
            )
            recovered = (
                root
                / "generated"
                / "recover.edit.pack"
                / "authoring"
                / "inputs"
                / "character.png"
            )
            recovered.parent.mkdir(parents=True)
            Image.new("RGBA", (96, 96), (11, 22, 33, 255)).save(recovered)
            workspace.state["authoring"] = {
                "generator": {"adapter_id": "dooley-default"},
                "inputs": {
                    "character": {
                        "workspace_file": "authoring/inputs/character.png",
                        "sha256": generator.sha256_file(recovered),
                        "origin": "user_supplied",
                        "aigc": False,
                        "authoritative_alpha": True,
                    }
                },
            }
            workspace.save()
            badge = root / "badges"
            badge.mkdir()

            profile = generator.profile_for_workspace_edit(
                workspace,
                profile_path=root / "edit" / "profile.json",
                badge_template_root=badge,
                workspace_root=root / "generated",
                output_zip=root / "exports" / "pack.zip",
                input_search_roots=(root / "generated",),
            )

            self.assertNotEqual(profile.character, recovered.resolve())
            self.assertEqual(profile.character.read_bytes(), recovered.read_bytes())
            self.assertTrue(profile.character.is_file())
            with mock.patch.object(
                generator,
                "generate_pack",
                return_value={
                    "workspace": str(profile.generated_workspace),
                    "zip": str(profile.output_zip),
                    "zip_sha256": "edited",
                },
            ) as build:
                generator.generate_assets(profile)
            self.assertFalse(recovered.exists())
            self.assertTrue(profile.character.is_file())
            self.assertEqual(build.call_args.kwargs["character"], profile.character)

    def test_automatic_pack_id_follows_new_project_target_only(self) -> None:
        self.assertEqual(
            generator.retarget_automatic_pack_id(
                "local.dooley.a1b2c3d4e5", "Dooley", "Pygmalien"
            ),
            "local.pygmalien.a1b2c3d4e5",
        )
        self.assertEqual(
            generator.retarget_automatic_pack_id(
                "local.dooley.custom", "Dooley", "Pygmalien"
            ),
            "local.dooley.custom",
        )
        self.assertEqual(
            generator.retarget_automatic_pack_id(
                "publisher.named.pack", "Dooley", "Pygmalien"
            ),
            "publisher.named.pack",
        )

    def test_installed_manager_capability_accepts_exact_adapter_version(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "manager-build.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "version": "1.1.2",
                        "adapters": [
                            {
                                "id": "dooley-default",
                                "adapter_version": 13,
                                "hero": "Dooley",
                                "skin": "Skin_DOO_01/A",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            capability = generator.require_installed_manager_adapter(
                "dooley-default", install_root=root
            )
            self.assertEqual(capability["manager_version"], "1.1.2")
            self.assertEqual(capability["adapter"]["adapter_version"], 13)

    def test_installed_manager_capability_rejects_legacy_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "manager-build.json").write_text(
                json.dumps({"schema_version": 1, "version": "0.9.63"}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "1.1.2"):
                generator.require_installed_manager_adapter(
                    "dooley-default", install_root=root
                )

    def test_installed_manager_capability_rejects_adapter_version_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "manager-build.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "version": "1.1.2",
                        "adapters": [
                            {"id": "dooley-default", "adapter_version": 11}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeError, "v13"):
                generator.require_installed_manager_adapter(
                    "dooley-default", install_root=root
                )

    def test_generate_omits_missing_optional_author_materials(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            profile = self._profile(Path(temp))
            profile.background.unlink()
            profile.small_icon.unlink()
            with mock.patch.object(
                generator,
                "generate_pack",
                return_value={
                    "workspace": str(profile.generated_workspace),
                    "zip": str(profile.output_zip),
                    "zip_sha256": "partial",
                },
            ) as build:
                generator.generate_assets(profile)
            self.assertIsNone(build.call_args.kwargs["background"])
            self.assertIsNone(build.call_args.kwargs["small_icon"])
            self.assertTrue(build.call_args.kwargs["allow_partial"])

    def test_existing_small_icon_overrides_stale_none_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            profile = replace(self._profile(Path(temp)), small_icon_mode="none")
            profile.save()
            self.assertEqual(
                generator.GeneratorProfile.load(profile.profile_path).small_icon_mode,
                "user",
            )
            with mock.patch.object(
                generator,
                "generate_pack",
                return_value={
                    "workspace": str(profile.generated_workspace),
                    "zip": str(profile.output_zip),
                    "zip_sha256": "no-icon",
                },
            ) as build:
                generator.generate_assets(profile)
            self.assertEqual(build.call_args.kwargs["small_icon"], profile.small_icon)

    def test_portrait_composite_preview_stacks_cropped_background_below_foreground(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            profile = self._profile(Path(temp))
            Image.new("RGBA", (160, 96), (12, 34, 56, 255)).save(profile.background)
            renderer = generator.LivePreviewRenderer(profile)
            preview = renderer.render_portrait_composite(
                background_offset=(200, 0),
                background_scale=1.2,
            )
            self.assertEqual(preview.size, (1024, 1024))
            self.assertEqual(preview.getpixel((0, 0))[:3], (12, 34, 56))
            self.assertEqual(preview.getpixel((512, 512))[:3], (40, 90, 130))

    def test_profile_rejects_fully_transparent_user_small_icon(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            profile = self._profile(Path(temp))
            Image.new("RGBA", (256, 256), (0, 0, 0, 0)).save(profile.small_icon)
            with self.assertRaisesRegex(ValueError, "小图标没有可见像素"):
                profile.validate()

    def test_pipeline_delegates_import_and_deploy_to_manager_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            profile = self._profile(Path(temp))
            workspace = SimpleNamespace(directory=Path(temp) / "manager-workspace")
            generated = {
                "workspace": str(profile.generated_workspace),
                "zip": str(profile.output_zip),
                "zip_sha256": "1234",
            }
            imported = {"kind": "complete_pack"}
            record = {"game": {"game_dir": "D:/The Bazaar"}}
            doctor = {"healthy": True}
            with (
                mock.patch.object(generator, "generate_assets", return_value=generated),
                mock.patch.object(
                    generator,
                    "import_into_manager",
                    return_value=(workspace, imported),
                ),
                mock.patch.object(
                    generator,
                    "deploy_from_manager",
                    return_value=(record, doctor),
                ) as deploy,
            ):
                result = generator.run_pipeline(profile)
            deploy.assert_called_once()
            self.assertEqual(result.imported_kind, "complete_pack")
            self.assertEqual(result.deployed_game, "D:/The Bazaar")
            self.assertTrue(result.doctor_healthy)

    def test_profile_rejects_missing_three_input_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            profile = self._profile(Path(temp))
            profile.input_metadata.write_text(
                json.dumps({"character": {}, "background": {}}), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "small_icon"):
                profile.validate()

    def test_character_offset_translates_and_clips_on_existing_canvas(self) -> None:
        source = Image.new("RGBA", (5, 5), (0, 0, 0, 0))
        source.putpixel((1, 1), (255, 10, 20, 255))
        shifted = generator.apply_character_offset(source, 2, -1)
        self.assertEqual(shifted.size, source.size)
        self.assertEqual(shifted.getpixel((3, 0)), (255, 10, 20, 255))
        self.assertEqual(shifted.getpixel((1, 1)), (0, 0, 0, 0))

    def test_profile_round_trip_preserves_canvas_adjustment(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base_profile = self._profile(root)
            icon_source = root / "inputs" / "icon-source.png"
            Image.new("RGBA", (80, 120), (20, 180, 80, 255)).save(icon_source)
            metadata = json.loads(
                (root / "inputs" / "metadata.json").read_text(encoding="utf-8")
            )
            metadata["small_icon_source"] = {"aigc": False}
            (root / "inputs" / "metadata.json").write_text(
                json.dumps(metadata), encoding="utf-8"
            )
            profile = replace(
                base_profile,
                character_offset_x=17,
                character_offset_y=-23,
                character_scale=1.25,
                background_offset_x=31,
                background_offset_y=-14,
                background_scale=1.35,
                output_offsets={"portrait_small": (8, -5)},
                small_icon_mode="block-gaps",
                small_icon_source=icon_source,
            )
            profile.save()
            loaded = generator.GeneratorProfile.load(profile.profile_path)
            self.assertEqual(loaded.character_offset_x, 17)
            self.assertEqual(loaded.character_offset_y, -23)
            self.assertEqual(loaded.character_scale, 1.25)
            self.assertEqual((loaded.background_offset_x, loaded.background_offset_y), (31, -14))
            self.assertEqual(loaded.background_scale, 1.35)
            self.assertEqual(loaded.output_offsets["portrait_small"], (8, -5))
            self.assertEqual(loaded.small_icon_mode, "block-gaps")
            self.assertEqual(loaded.small_icon_source, icon_source.resolve())
            payload = json.loads(profile.profile_path.read_text(encoding="utf-8"))
            self.assertEqual(
                payload["character_adjustment"],
                {"offset_x": 17, "offset_y": -23, "scale": 1.25},
            )
            self.assertEqual(
                payload["output_adjustments"]["portrait_small"],
                {"offset_x": 8, "offset_y": -5},
            )
            self.assertEqual(
                payload["background_adjustment"],
                {"offset_x": 31, "offset_y": -14, "scale": 1.35, "fit": "cover"},
            )
            self.assertEqual(
                payload["small_icon_generation"],
                {"mode": "block-gaps"},
            )
            self.assertEqual(payload["inputs"]["small_icon_source"], "inputs/icon-source.png")

    def test_generate_archives_separate_small_icon_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            profile = self._profile(root)
            icon_source = root / "inputs" / "icon-source.png"
            Image.new("RGBA", (96, 96), (200, 50, 90, 255)).save(icon_source)
            metadata = json.loads(profile.input_metadata.read_text(encoding="utf-8"))
            metadata["small_icon_source"] = {"aigc": False}
            profile.input_metadata.write_text(json.dumps(metadata), encoding="utf-8")
            profile = replace(profile, small_icon_source=icon_source)
            with mock.patch.object(
                generator,
                "generate_pack",
                return_value={
                    "workspace": str(profile.generated_workspace),
                    "zip": str(profile.output_zip),
                    "zip_sha256": "source",
                },
            ) as build:
                generator.generate_assets(profile)
            self.assertEqual(
                build.call_args.kwargs["supplemental_inputs"],
                {"small_icon_source": icon_source},
            )

    def test_live_preview_leaves_missing_optional_materials_transparent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            profile = self._profile(Path(temp))
            profile.background.unlink()
            profile.small_icon.unlink()
            renderer = generator.LivePreviewRenderer(profile)
            standing = renderer.render("standing_overlay")
            portrait = renderer.render("portrait_gameplay")
            icon = renderer.render("hero_icon_small")
            self.assertIsNotNone(standing.getchannel("A").getbbox())
            self.assertIsNotNone(portrait.getchannel("A").getbbox())
            self.assertIsNone(icon.getchannel("A").getbbox())

    def test_generate_passes_global_and_per_output_adjustments(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            profile = replace(
                self._profile(Path(temp)),
                character_offset_x=3,
                character_offset_y=-2,
                character_scale=1.2,
                output_offsets={"hero_select": (7, 11)},
            )

            def inspect_build(**kwargs):
                self.assertEqual(kwargs["character"], profile.character)
                self.assertEqual(kwargs["character_canvas_offset"], (3, -2))
                self.assertEqual(kwargs["character_scale"], 1.2)
                self.assertEqual(kwargs["output_offsets"], {"hero_select": (7, 11)})
                return {
                    "workspace": str(profile.generated_workspace),
                    "zip": str(profile.output_zip),
                    "zip_sha256": "offset",
                }

            with mock.patch.object(generator, "generate_pack", side_effect=inspect_build):
                result = generator.generate_assets(profile)
            self.assertEqual(result["zip_sha256"], "offset")


if __name__ == "__main__":
    unittest.main()
