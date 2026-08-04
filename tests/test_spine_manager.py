from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

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
    SpinePlacement,
    _stage,
    _prepared_json,
    _rewrite_atlas,
    import_spine_package,
    targets,
)
from bazaar_spine_manager_ui import APP_VERSION, LOG_PATH, configure_logging  # noqa: E402
from spine_static_preview import render_setup_pose  # noqa: E402


class SpineManagerTests(unittest.TestCase):
    def create_package(self, root: Path) -> Path:
        source = root / "source"
        source.mkdir()
        payload = {
            "skeleton": {
                "spine": "4.2.43",
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
            package = import_spine_package(self.create_package(root), root / "workspace")
            self.assertEqual(package.version, "4.2.43")
            self.assertEqual(package.animations, ("walk", "wave"))
            self.assertEqual(package.skins, ("default",))
            self.assertEqual((package.width, package.height), (32, 32))

    def test_imports_spine_41_multi_page_atlas_and_ignores_source_images(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            (source / "images").mkdir(parents=True)
            payload = {
                "skeleton": {"spine": "4.1.24", "width": 64, "height": 64},
                "bones": [{"name": "root"}],
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

            package = import_spine_package(source, root / "workspace")

            self.assertEqual(package.version, "4.1.24")
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

    def test_verified_default_targets_are_available(self) -> None:
        available = targets()
        self.assertEqual({item.hero for item in available}, {"Mak", "Vanessa", "Pygmalien", "Dooley", "Jules"})
        mak = next(item for item in available if item.hero == "Mak")
        self.assertEqual(mak.prefix, "Skin_MAK_01a")
        self.assertTrue(mak.bundle_relative.endswith("skin_mak_01_assets_all.bundle"))

    def test_build_script_creates_requested_executable_name(self) -> None:
        build = (ROOT / "build-spine-manager.ps1").read_text(encoding="utf-8")
        self.assertIn('"--name", "bazaar_spine_manager"', build)
        self.assertIn('"--self-test"', build)
        self.assertIn("spine-manager-build.json", build)
        self.assertIn('"--hidden-import", "spine_static_preview"', build)
        self.assertIn("manager\\spine-preview", build)

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
        self.assertEqual(APP_VERSION, "1.1.2")

    def test_background_exception_is_bound_before_tk_callback(self) -> None:
        source = (TOOLS / "bazaar_spine_manager_ui.py").read_text(encoding="utf-8")
        self.assertIn("lambda caught=error, stack=details", source)


if __name__ == "__main__":
    unittest.main()
