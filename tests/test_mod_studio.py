import json
import sys
import tempfile
import unittest
import wave
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

from bazaar_skin_manager import validate_pack  # noqa: E402
from mod_studio_core import (  # noqa: E402
    PREVIEW_SIZE,
    StudioWorkspace,
    _adapter_for_target,
    _original_visual_deployment,
    _verified_original_bundle,
    compose_image_preview,
    materialized_pack_id,
    remove_color_screen,
    sha256_file,
)


def write_runtime_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(22050)
        output.writeframes(b"\x00\x00" * 220)


class ModStudioTests(unittest.TestCase):
    def test_original_reference_prefers_verified_native_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            game = root / "game"
            relative = Path("bundles") / "skin.bundle"
            live = game / relative
            backup = root / "native.bundle"
            live.parent.mkdir(parents=True)
            live.write_bytes(b"deployed skin")
            backup.write_bytes(b"native game bundle")
            original_hash = sha256_file(backup)
            record = {
                "native_patches": [
                    {
                        "target": str(live),
                        "backup": str(backup),
                        "original_sha256": original_hash,
                    }
                ]
            }

            with mock.patch(
                "mod_studio_core.existing_install_record", return_value=record
            ):
                resolved = _verified_original_bundle(
                    game,
                    {
                        "target": relative.as_posix(),
                        "supported_original_sha256": [original_hash],
                    },
                )

            self.assertEqual(backup, resolved)

    def test_runtime_portrait_can_use_verified_skin_bundle_for_reference(self):
        adapter = _adapter_for_target(
            {"hero": "Dooley", "skin": "Skin_DOO_01/A"}
        )

        portrait = _original_visual_deployment(adapter, "portrait_gameplay")

        self.assertEqual("Skin_DOO_01a_Portrait", portrait["asset_name"])
        self.assertTrue(portrait["target"].endswith("skin_doo_01_assets_all.bundle"))
        with self.assertRaisesRegex(ValueError, "no static original image"):
            _original_visual_deployment(adapter, "standing_overlay")

    def test_original_preview_uses_verified_read_only_texture_export(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = StudioWorkspace.create("test.original", root=root / "work")

            def fake_export(_bundle, output, **_kwargs):
                output.parent.mkdir(parents=True, exist_ok=True)
                Image.new("RGBA", (512, 512), (9, 8, 7, 255)).save(output)
                return {}

            game = SimpleNamespace(game_dir=root / "game", build_id="24001960")
            with mock.patch("mod_studio_core.preferred_game_install", return_value=game):
                with (
                    mock.patch(
                        "mod_studio_core._verified_original_bundle",
                        return_value=root / "native.bundle",
                    ),
                    mock.patch(
                        "mod_studio_core.export_texture_bundle",
                        side_effect=fake_export,
                    ) as exporter,
                ):
                    output = workspace.export_original_visual("hero_select")
            self.assertTrue(output.is_file())
            with Image.open(output) as loaded:
                self.assertEqual(loaded.size, (512, 512))
            exporter.assert_called_once()

    def test_preview_is_fixed_size_centered_and_not_cropped(self):
        source = Image.new("RGBA", (100, 200), (220, 80, 40, 255))
        preview = compose_image_preview(source)
        self.assertEqual(preview.size, PREVIEW_SIZE)
        self.assertEqual(preview.getpixel((79, 0)), (220, 80, 40, 255))
        self.assertEqual(preview.getpixel((79, 119)), (220, 80, 40, 255))
        self.assertNotEqual(preview.getpixel((0, 60)), (220, 80, 40, 255))
        self.assertNotEqual(preview.getpixel((159, 60)), (220, 80, 40, 255))

    def test_preview_rejects_invalid_geometry(self):
        source = Image.new("RGBA", (1, 1), (255, 255, 255, 255))
        with self.assertRaisesRegex(ValueError, "dimensions"):
            compose_image_preview(source, (0, 120))
        with self.assertRaisesRegex(ValueError, "checker"):
            compose_image_preview(source, checker_size=0)

    def test_empty_workspace_is_valid_and_means_original_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = StudioWorkspace.create(
                "test.empty",
                root=Path(temp),
            )
            workspace.build_pack()
            manifest = json.loads(
                (workspace.directory / "mod.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["visual_replacements"], [])
            self.assertNotIn("audio_manifest", manifest)
            self.assertEqual(validate_pack(workspace.directory), [])

    def test_single_visual_slot_and_chroma_key(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "green.png"
            image = Image.new("RGBA", (8, 8), (0, 255, 0, 255))
            for x in range(2, 6):
                for y in range(2, 6):
                    image.putpixel((x, y), (255, 0, 0, 255))
            image.save(source)
            workspace = StudioWorkspace.create("test.visual", root=root)
            destination = workspace.import_visual(
                "portrait_gameplay",
                source,
                chroma_color="#00FF00",
                tolerance=0,
            )
            output = Image.open(destination).convert("RGBA")
            self.assertEqual(output.getpixel((0, 0))[3], 0)
            self.assertEqual(output.getpixel((4, 4))[3], 255)
            workspace.build_pack()
            manifest = json.loads(
                (workspace.directory / "mod.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                [item["slot"] for item in manifest["visual_replacements"]],
                ["portrait_gameplay"],
            )
            self.assertEqual(validate_pack(workspace.directory), [])

    def test_clipboard_image_path_uses_same_slot_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = StudioWorkspace.create(
                "test.clipboard",
                root=Path(temp),
            )
            workspace.import_pil_image(
                "standing_overlay",
                Image.new("RGBA", (6, 9), (20, 40, 60, 255)),
            )
            with Image.open(workspace.visual_path("standing_overlay")) as output:
                self.assertEqual(output.size, (6, 9))

    def test_audio_line_import_is_runtime_ready_and_partial(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "line.wav"
            write_runtime_wav(source)
            workspace = StudioWorkspace.create("test.audio", root=root)
            workspace.import_audio("Idle.default", source)
            workspace.build_pack()
            audio = json.loads(
                (workspace.directory / "audio-manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(len(audio["routes"]), 1)
            self.assertEqual(audio["routes"][0]["logical_slot"], "Idle.default")
            self.assertEqual(validate_pack(workspace.directory), [])

    def test_voice_production_zip_is_converted_to_runtime_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_root = root / "voice-source"
            wav = source_root / "audio" / "MakIdle1.wav"
            write_runtime_wav(wav)
            source_manifest = {
                "schema_version": "example-voice-assets/v1",
                "version": "0.9.0",
                "target": {
                    "game": "The Bazaar",
                    "steam_build": "24001960",
                    "hero": "Mak",
                },
                "assets": [
                    {
                        "row_number": 1,
                        "logical_pool": "Idle.default",
                        "sample_name": "MakIdle1",
                        "event_path": "event:/VO/Hero/Mak/VO_Mak_Idle",
                        "event_guid": "c6f1a013-0da1-49dc-bba2-3d82c0845557",
                        "selector": None,
                        "audio_file": "audio/MakIdle1.wav",
                        "audio_sha256": sha256_file(wav),
                        "asset_action": "replace_candidate",
                    }
                ],
            }
            (source_root / "example-voice-assets.json").write_text(
                json.dumps(source_manifest),
                encoding="utf-8",
            )
            archive = root / "voice.zip"
            with zipfile.ZipFile(archive, "w") as output:
                for path in source_root.rglob("*"):
                    if path.is_file():
                        output.write(
                            path,
                            (Path("Voice-0.9.0") / path.relative_to(source_root)).as_posix(),
                        )
            workspace = StudioWorkspace.create(
                "test.voice-package",
                root=root / "workspaces",
            )
            summary = workspace.import_zip(archive)
            self.assertEqual(summary.audio_routes, ["Idle.default"])
            workspace.build_pack()
            self.assertEqual(validate_pack(workspace.directory), [])

    def test_mak_voice_source_package_retargets_to_jules_exact_routes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_root = root / "voice-source"
            wav = source_root / "audio" / "MakIdle1.wav"
            write_runtime_wav(wav)
            source_manifest = {
                "schema_version": "example-voice-assets/v1",
                "version": "1.0.0",
                "target": {
                    "game": "The Bazaar",
                    "steam_build": "24570932",
                    "hero": "Mak",
                },
                "assets": [
                    {
                        "row_number": 1,
                        "logical_pool": "Idle.default",
                        "sample_name": "MakIdle1",
                        "event_path": "event:/VO/Hero/Mak/VO_Mak_Idle",
                        "event_guid": "c6f1a013-0da1-49dc-bba2-3d82c0845557",
                        "selector": None,
                        "audio_file": "audio/MakIdle1.wav",
                        "audio_sha256": sha256_file(wav),
                        "asset_action": "replace_candidate",
                    }
                ],
            }
            (source_root / "example-voice-assets.json").write_text(
                json.dumps(source_manifest),
                encoding="utf-8",
            )
            archive = root / "voice.zip"
            with zipfile.ZipFile(archive, "w") as output:
                for path in source_root.rglob("*"):
                    if path.is_file():
                        output.write(path, path.relative_to(source_root).as_posix())
            workspace = StudioWorkspace.create(
                "test.voice-jules",
                root=root / "workspaces",
                hero="Jules",
                skin="Skin_JUL_01/A",
                skin_name_contains="JUL_01a",
            )
            summary = workspace.import_zip(archive)
            self.assertEqual(summary.audio_routes, ["Idle.default"])
            manifest = workspace.audio_manifest()
            self.assertEqual(manifest["target"]["hero"], "Jules")
            self.assertEqual(manifest["source_package"]["hero"], "Mak")
            self.assertEqual(
                manifest["routes"][0]["event_guid"],
                "c6caadc5-a8db-4296-81fb-f15cd62b0086",
            )
            self.assertEqual(
                manifest["routes"][0]["event_path"],
                "event:/VO/Hero/Jules/VO_Jules_Idle",
            )
            workspace.build_pack()
            self.assertEqual(validate_pack(workspace.directory), [])

    def test_complete_pack_round_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = StudioWorkspace.create(
                "test.roundtrip.source",
                root=root / "one",
            )
            source.import_pil_image(
                "hero_select",
                Image.new("RGBA", (32, 32), (10, 20, 30, 255)),
            )
            authoring_input = source.directory / "authoring" / "inputs" / "character.png"
            authoring_input.parent.mkdir(parents=True)
            Image.new("RGBA", (48, 64), (30, 40, 50, 255)).save(authoring_input)
            source.state["authoring"] = {
                "generator": {"id": "test-generator", "version": 1},
                "inputs": {
                    "character": {
                        "sha256": "abc",
                        "workspace_file": "authoring/inputs/character.png",
                    }
                },
            }
            source.save()
            archive = source.export_zip(root / "complete.zip")
            with zipfile.ZipFile(archive) as package:
                self.assertIn("authoring/inputs/character.png", package.namelist())
            destination = StudioWorkspace.create(
                "test.roundtrip.destination",
                root=root / "two",
            )
            summary = destination.import_zip(archive)
            self.assertEqual(summary.kind, "complete_pack")
            self.assertEqual(summary.visual_slots, ["hero_select"])
            self.assertEqual(
                destination.state["pack"]["id"],
                "test.roundtrip.source",
            )
            self.assertTrue(
                (destination.directory / "authoring" / "inputs" / "character.png").is_file()
            )
            destination.build_pack()
            rebuilt = json.loads(
                (destination.directory / "mod.json").read_text(encoding="utf-8")
            )
            self.assertEqual(rebuilt["authoring"], source.state["authoring"])

    def test_zip_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "unsafe.zip"
            with zipfile.ZipFile(archive, "w") as output:
                output.writestr("../escape.txt", "no")
            workspace = StudioWorkspace.create("test.safe", root=root / "work")
            with self.assertRaisesRegex(ValueError, "path traversal"):
                workspace.import_zip(archive)

    def test_animation_sources_are_carried_but_not_claimed_runtime_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            skeleton = root / "character.skel"
            atlas = root / "character.atlas"
            skeleton.write_bytes(b"spine")
            atlas.write_text("atlas", encoding="utf-8")
            workspace = StudioWorkspace.create("test.animation", root=root / "work")
            workspace.import_animation([skeleton, atlas], "spine_source")
            workspace.build_pack()
            manifest = json.loads(
                (workspace.directory / "mod.json").read_text(encoding="utf-8")
            )
            self.assertFalse(manifest["animation"]["runtime_ready"])
            self.assertEqual(len(manifest["animation"]["files"]), 2)

    def test_clear_loaded_assets_preserves_metadata_and_removes_all_payloads(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = StudioWorkspace.create(
                "test.clear",
                root=root / "work",
                name="Keep this name",
                version="7.8.9",
            )
            workspace.import_pil_image(
                "hero_select",
                Image.new("RGBA", (16, 16), (1, 2, 3, 255)),
            )
            wav = root / "line.wav"
            write_runtime_wav(wav)
            workspace.import_audio("Idle.default", wav)
            animation = root / "character.skel"
            animation.write_bytes(b"spine")
            workspace.import_animation([animation], "spine_source")
            workspace.build_pack()
            (workspace.directory / "authoring-notes.json").write_text(
                "{}",
                encoding="utf-8",
            )

            removed = workspace.clear_loaded_assets()

            self.assertEqual(
                removed,
                {
                    "visual_slots": 1,
                    "audio_routes": 1,
                    "animation_files": 1,
                },
            )
            self.assertEqual(workspace.state["pack"]["id"], "test.clear")
            self.assertEqual(workspace.state["pack"]["name"], "Keep this name")
            self.assertEqual(workspace.state["pack"]["version"], "7.8.9")
            self.assertEqual(workspace.state["visual_slots"], {})
            self.assertIsNone(workspace.state["audio_manifest"])
            self.assertEqual(workspace.state["animation"]["mode"], "none")
            self.assertFalse((workspace.directory / "assets").exists())
            self.assertFalse((workspace.directory / "audio").exists())
            self.assertFalse((workspace.directory / "animation").exists())
            self.assertFalse((workspace.directory / "mod.json").exists())
            self.assertFalse((workspace.directory / "asset-index.json").exists())
            self.assertFalse(
                (workspace.directory / "authoring-notes.json").exists()
            )

    def test_remove_color_screen_validates_hex(self):
        image = Image.new("RGBA", (1, 1), (0, 255, 0, 255))
        with self.assertRaisesRegex(ValueError, "RRGGBB"):
            remove_color_screen(image, "green")

    def test_one_library_pack_materializes_for_two_professions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = StudioWorkspace.create("test.reusable", root=root / "library")
            source.import_pil_image(
                "hero_select",
                Image.new("RGBA", (512, 512), (10, 20, 30, 255)),
            )
            voice = root / "voice.wav"
            write_runtime_wav(voice)
            source.import_audio("Idle.default", voice)
            original_target = dict(source.state["target"])
            targets = [
                {
                    "game": "the-bazaar",
                    "hero": "Dooley",
                    "skin": "Skin_DOO_01/A",
                    "skin_name_contains": "DOO_01a",
                },
                {
                    "game": "the-bazaar",
                    "hero": "Jules",
                    "skin": "Skin_JUL_01/A",
                    "skin_name_contains": "JUL_01a",
                },
            ]
            captured = []

            def inspect(workspaces, game_dir=None):
                for workspace in workspaces:
                    workspace.build_pack()
                    manifest = json.loads(
                        (workspace.directory / "mod.json").read_text(
                            encoding="utf-8"
                        )
                    )
                    audio = json.loads(
                        (workspace.directory / "audio-manifest.json").read_text(
                            encoding="utf-8"
                        )
                    )
                    manifest["_test_audio"] = audio
                    captured.append(manifest)
                return {"packs": captured}

            with mock.patch.object(
                StudioWorkspace,
                "deploy_many",
                side_effect=inspect,
            ):
                StudioWorkspace.deploy_assignments(
                    [(source, targets[0]), (source, targets[1])]
                )

            self.assertEqual(source.state["target"], original_target)
            self.assertEqual(
                {manifest["target"]["hero"] for manifest in captured},
                {"Dooley", "Jules"},
            )
            self.assertEqual(len({manifest["id"] for manifest in captured}), 2)
            for manifest in captured:
                self.assertEqual(manifest["source_pack"]["id"], "test.reusable")
                self.assertEqual(
                    manifest["_test_audio"]["target"]["hero"],
                    manifest["target"]["hero"],
                )
                self.assertEqual(
                    manifest["_test_audio"]["routes"][0]["event_path"],
                    (
                        "event:/VO/Hero/Dooley/VO_Dooley_Idle"
                        if manifest["target"]["hero"] == "Dooley"
                        else "event:/VO/Hero/Jules/VO_Jules_Idle"
                    ),
                )
                self.assertIn(
                    "hero_select",
                    {
                        replacement["slot"]
                        for replacement in manifest["visual_replacements"]
                    },
                )

    def test_materialized_pack_id_preserves_target_for_long_source_ids(self):
        source_id = "a" * 96
        dooley = {
            "game": "the-bazaar",
            "hero": "Dooley",
            "skin": "Skin_DOO_01/A",
            "skin_name_contains": "DOO_01a",
        }
        jules = {
            "game": "the-bazaar",
            "hero": "Jules",
            "skin": "Skin_JUL_01/A",
            "skin_name_contains": "JUL_01a",
        }
        dooley_id = materialized_pack_id(source_id, dooley)
        jules_id = materialized_pack_id(source_id, jules)
        self.assertLessEqual(len(dooley_id), 96)
        self.assertLessEqual(len(jules_id), 96)
        self.assertNotEqual(dooley_id, jules_id)
        self.assertTrue(dooley_id.endswith(".for.dooley-skin_doo_01-a"))
        self.assertTrue(jules_id.endswith(".for.jules-skin_jul_01-a"))


if __name__ == "__main__":
    unittest.main()
