import json
import sys
import tempfile
import unittest
import wave
import zipfile
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))

from bazaar_skin_manager import validate_pack  # noqa: E402
from mod_studio_core import (  # noqa: E402
    PREVIEW_SIZE,
    StudioWorkspace,
    compose_image_preview,
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
            archive = source.export_zip(root / "complete.zip")
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


if __name__ == "__main__":
    unittest.main()
