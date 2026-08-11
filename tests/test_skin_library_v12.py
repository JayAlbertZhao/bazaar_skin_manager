from __future__ import annotations

import json
import sys
import tempfile
import unittest
import wave
import zipfile
from pathlib import Path
from unittest import mock

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from mod_studio_core import StudioWorkspace
from asset_generator_ui import PREVIEW_SLOTS
from bazaar_skin_manager_ui_v12 import (
    EMBEDDED_LIBRARY_INDEX,
    SkinManagerV12,
    export_pack_with_library_assets,
    import_embedded_library_assets,
)
from skin_library_core import AssetLibrary


class FirstClassAssetLibraryTests(unittest.TestCase):
    @staticmethod
    def _write_wav(path: Path) -> None:
        with wave.open(str(path), "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(22050)
            stream.writeframes(b"\0\0" * 480)

    def test_image_import_is_content_addressed_and_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.png"
            Image.new("RGBA", (64, 48), (12, 34, 56, 255)).save(source)
            library = AssetLibrary(root / "library")

            first = library.import_file(
                source,
                asset_type="character_source",
                name="Hero source",
            )
            second = library.import_file(
                source,
                asset_type="character_source",
                name="Duplicate name",
            )

            self.assertEqual(first["id"], second["id"])
            self.assertEqual(len(library.assets), 1)
            self.assertEqual(first["metadata"]["image_size"], [64, 48])
            self.assertTrue(library.preview_path(first).is_file())

    def test_workspace_migration_records_inputs_and_small_icon(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = StudioWorkspace.create("example.skin", root=root / "packs")
            authoring = workspace.directory / "authoring" / "inputs"
            authoring.mkdir(parents=True)
            character = authoring / "character.png"
            Image.new("RGBA", (80, 100), (1, 2, 3, 255)).save(character)
            workspace.state["authoring"] = {
                "inputs": {"character": {"workspace_file": "authoring/inputs/character.png"}}
            }
            workspace.import_pil_image(
                "hero_icon_small",
                Image.new("RGBA", (32, 32), (255, 255, 255, 255)),
            )
            workspace.save()
            library = AssetLibrary(root / "library")

            library.register_workspace(workspace)

            references = workspace.state["library_assets"]
            self.assertIn("character", references["inputs"])
            self.assertIn("hero_icon_small", references["visual_slots"])
            self.assertEqual(len(library.assets), 2)

    def test_workspace_migration_records_each_manual_slot_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = StudioWorkspace.create("manual.skin", root=root / "packs")
            inputs = workspace.directory / "authoring" / "manual_inputs" / "store_image"
            inputs.mkdir(parents=True)
            background = inputs / "background.png"
            character = inputs / "character.png"
            Image.new("RGBA", (64, 64), (5, 10, 20, 255)).save(background)
            Image.new("RGBA", (32, 64), (200, 100, 50, 255)).save(character)
            workspace.state["authoring"] = {
                "mode": "manual_slots",
                "inputs": {
                    "store_image.background": {
                        "workspace_file": "authoring/manual_inputs/store_image/background.png"
                    },
                    "store_image.character": {
                        "workspace_file": "authoring/manual_inputs/store_image/character.png"
                    },
                },
            }
            workspace.save()
            library = AssetLibrary(root / "library")

            library.register_workspace(workspace)

            references = workspace.state["library_assets"]["inputs"]
            self.assertEqual(
                {"store_image.background", "store_image.character"}, set(references)
            )
            self.assertEqual(
                "background", library.assets[references["store_image.background"]]["type"]
            )
            self.assertEqual(
                "character_source",
                library.assets[references["store_image.character"]]["type"],
            )

    def test_workspace_migration_groups_audio_route_as_first_class_asset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = StudioWorkspace.create("audio.skin", root=root / "packs")
            route_catalog = workspace.audio_route_catalog()
            if not route_catalog:
                self.skipTest("fixture adapter exposes no audio routes")
            source = root / "line.wav"
            self._write_wav(source)
            route = route_catalog[0]["logical_slot"]
            workspace.import_audio(route, source)
            library = AssetLibrary(root / "library")

            library.register_workspace(workspace)

            asset_id = workspace.state["library_assets"]["audio"][route]
            self.assertEqual(library.assets[asset_id]["type"], "audio")
            self.assertEqual(
                library.assets[asset_id]["metadata"]["logical_slot"], route
            )

    def test_referenced_asset_cannot_be_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "icon.png"
            Image.new("RGBA", (32, 32), (255, 255, 255, 255)).save(source)
            library = AssetLibrary(root / "library")
            record = library.import_file(source, asset_type="small_icon")
            workspace = StudioWorkspace.create("ref.skin", root=root / "packs")
            workspace.state["library_assets"] = {
                "inputs": {},
                "visual_slots": {"hero_icon_small": record["id"]},
                "audio": {},
                "animation": None,
            }
            workspace.save()
            references = library.references([workspace])[record["id"]]

            with self.assertRaisesRegex(ValueError, "still used"):
                library.remove(record["id"], references=references)
            self.assertIn(record["id"], library.assets)

    def test_spine_asset_requires_skeleton_atlas_and_texture(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library = AssetLibrary(root / "library")
            skeleton = root / "hero.json"
            skeleton.write_text(
                json.dumps(
                    {
                        "skeleton": {"spine": "4.2.43"},
                        "bones": [{"name": "root"}],
                        "skins": [{"name": "default", "attachments": {}}],
                        "animations": {"idle": {}},
                    }
                ),
                encoding="utf-8",
            )
            atlas = root / "hero.atlas"
            atlas.write_text(
                "hero.png\nsize:16,16\nfilter:Linear,Linear\nscale:1\n",
                encoding="utf-8",
            )
            texture = root / "hero.png"
            Image.new("RGBA", (16, 16), (255, 255, 255, 255)).save(texture)

            record = library.import_spine(
                [skeleton, atlas, texture],
                name="Hero animation",
                runtime_version="4.2",
                target={"hero": "Mak", "skin": "Skin_MAK_01/A"},
            )

            self.assertEqual(record["type"], "spine")
            self.assertEqual(record["metadata"]["runtime_version"], "4.2.43")
            self.assertEqual(record["metadata"]["animations"], ["idle"])
            self.assertEqual(len(library.record_files(record)), 3)

    def test_spine_zip_auto_detects_41_and_normalizes_multi_page_atlas(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            (source / "images").mkdir(parents=True)
            (source / "Velina.json").write_text(
                json.dumps(
                    {
                        "skeleton": {"spine": "4.1.24"},
                        "bones": [{"name": "root"}],
                        "skins": [{"name": "default", "attachments": {}}],
                        "animations": {"loop": {}},
                    }
                ),
                encoding="utf-8",
            )
            (source / "Velina.atlas").write_text(
                "Velina_1.png\nsize:8,8\nfilter:Linear,Linear\nscale:0.33\n"
                "first\nbounds:0,0,8,8\n\n"
                "Velina_2.png\nsize:8,6\nfilter:Linear,Linear\nscale:0.33\n"
                "second\nbounds:0,0,8,6\n",
                encoding="utf-8",
            )
            Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(source / "Velina_1.png")
            Image.new("RGBA", (8, 6), (0, 255, 0, 255)).save(source / "Velina_2.png")
            Image.new("RGBA", (4, 4), (0, 0, 255, 255)).save(source / "images" / "source.png")
            (source / "维琳娜.spine").write_bytes(b"source project is intentionally ignored")
            archive = root / "维琳娜.zip"
            with zipfile.ZipFile(archive, "w") as output:
                for path in source.rglob("*"):
                    if path.is_file():
                        output.write(path, path.relative_to(source).as_posix())

            library = AssetLibrary(root / "library")
            record = library.import_spine(
                [archive],
                name="维琳娜",
                runtime_version="4.2",
            )

            self.assertEqual(record["metadata"]["runtime_version"], "4.1.24")
            self.assertEqual(record["metadata"]["animations"], ["loop"])
            self.assertEqual(record["metadata"]["skins"], ["default"])
            self.assertEqual(record["metadata"]["atlas_scale"], 0.33)
            self.assertEqual(record["metadata"]["image_size"], [8, 14])
            self.assertEqual(record["metadata"]["package_format"], "zip")
            self.assertEqual(record["source"], str(archive.resolve()))
            source_files = record["metadata"]["source_files"]
            self.assertEqual(len(source_files), 1)
            self.assertEqual(source_files[0]["name"], archive.name)
            self.assertRegex(source_files[0]["sha256"], r"^[0-9a-f]{64}$")
            files = {path.suffix.casefold(): path for path in library.record_files(record)}
            self.assertEqual(set(files), {".json", ".atlas", ".png"})
            atlas_text = files[".atlas"].read_text(encoding="utf-8")
            self.assertEqual(atlas_text.splitlines()[0], "skeleton.png")
            self.assertNotIn("Velina_2.png", atlas_text)
            with Image.open(files[".png"]) as image:
                self.assertEqual(image.size, (8, 14))
                self.assertEqual(image.getpixel((0, 13)), (0, 255, 0, 255))

    def test_pack_export_round_trips_first_class_assets_and_references(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.png"
            Image.new("RGBA", (48, 64), (20, 40, 60, 255)).save(source)
            source_library = AssetLibrary(root / "source-library")
            asset = source_library.import_file(
                source,
                asset_type="character_source",
                name="Reusable source",
            )
            workspace = StudioWorkspace.create("portable.skin", root=root / "packs")
            workspace.import_visual("standing_overlay", source)
            workspace.state["library_assets"] = {
                "inputs": {"character": asset["id"]},
                "visual_slots": {"standing_overlay": asset["id"]},
                "audio": {},
                "animation": None,
            }
            workspace.save()
            archive = root / "portable.zip"

            export_pack_with_library_assets(workspace, archive, source_library)

            with zipfile.ZipFile(archive) as package:
                self.assertIn(EMBEDDED_LIBRARY_INDEX, package.namelist())
            imported_workspace = StudioWorkspace.create(
                "portable.imported", root=root / "imported-packs"
            )
            imported_workspace.import_zip(archive)
            destination_library = AssetLibrary(root / "destination-library")
            imported = import_embedded_library_assets(
                imported_workspace, destination_library
            )
            self.assertEqual(imported, 1)
            refs = imported_workspace.state["library_assets"]
            self.assertEqual(
                refs["inputs"]["character"],
                refs["visual_slots"]["standing_overlay"],
            )
            self.assertIn(refs["inputs"]["character"], destination_library.assets)


class ManagerV12SurfaceTests(unittest.TestCase):
    def test_integrated_animation_page_accepts_spine_zip(self) -> None:
        ui = (ROOT / "tools" / "bazaar_skin_manager_ui_v12.py").read_text(
            encoding="utf-8"
        )
        entry = (ROOT / "tools" / "bazaar_skin_manager_ui.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('text="选择 Spine ZIP 或散文件…"', ui)
        self.assertIn('("Spine package", "*.zip")', ui)
        self.assertIn('suffix.casefold() == ".zip"', ui)
        self.assertIn('"--smoke-spine-import"', entry)

    def test_every_catalog_hero_has_an_original_target_preview(self) -> None:
        catalog = json.loads(
            (ROOT / "manager" / "hero-catalog.json").read_text(
                encoding="utf-8"
            )
        )
        sources = (
            ROOT
            / "manager"
            / "assets"
            / "badge-templates"
            / "hero-select-gold"
            / "sources"
        )
        if not sources.is_dir():
            self.skipTest("game-derived target previews are not stored in public source")
        expected = {
            f"{str(hero['id']).casefold()}.png"
            for hero in catalog["heroes"]
        }
        available = {path.name for path in sources.glob("*.png")}
        self.assertEqual(expected, available)
        for name in expected:
            with Image.open(sources / name) as preview:
                self.assertIn(preview.size, {(256, 256), (512, 512)})
                self.assertEqual(preview.mode, "RGBA")

    def test_deployment_targets_are_grouped_by_dynamic_hero_and_default_first(self) -> None:
        manager = SkinManagerV12.__new__(SkinManagerV12)
        manager.catalog = {
            "heroes": [
                {
                    "id": "Dooley",
                    "display_name": "Dooley",
                    "skins": [
                        {
                            "id": "Skin_DOO_02/A",
                            "display_name": "02",
                            "deployment_status": "detected_unmapped",
                        },
                        {
                            "id": "Skin_DOO_01/A",
                            "display_name": "默认",
                            "deployment_status": "supported",
                        },
                    ],
                },
                {
                    "id": "Hero8",
                    "display_name": "The Dragons（Rin & Jin）",
                    "skins": [
                        {
                            "id": "Skin_DRA_01/A",
                            "display_name": "默认皮肤",
                            "deployment_status": "supported",
                        }
                    ],
                },
            ]
        }
        groups = manager._target_groups()
        self.assertEqual([group["hero_id"] for group in groups], ["Dooley", "Hero8"])
        self.assertEqual(groups[0]["targets"][0][0], "Dooley|Skin_DOO_01/A")
        self.assertEqual(groups[1]["targets"][0][0], "Hero8|Skin_DRA_01/A")

    def test_edit_pack_opens_same_workspace_in_creation_page(self) -> None:
        manager = SkinManagerV12.__new__(SkinManagerV12)
        workspace = object()
        manager.selected_pack_path = "selected-pack"
        manager.workspaces = {"selected-pack": workspace}
        manager.pages = mock.Mock()
        manager.creation_page = object()
        manager.root = mock.Mock()
        manager.generator = mock.Mock()
        manager._show_error = mock.Mock()

        manager._edit_pack()

        manager.pages.select.assert_called_once_with(manager.creation_page)
        manager.root.update_idletasks.assert_called_once_with()
        manager.generator.edit_workspace.assert_called_once_with(workspace)
        manager._show_error.assert_not_called()

    def test_generated_draft_becomes_the_shared_manual_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = StudioWorkspace.create(
                "local.dooley.shared-draft",
                root=Path(temporary),
                hero="Dooley",
                skin="Skin_DOO_01/A",
            )
            manager = SkinManagerV12.__new__(SkinManagerV12)
            manager.workspaces = {}
            manager.manual_slot_editor = mock.Mock()
            manager.generator = mock.Mock()
            manager.generator.draft_fingerprint.return_value = "automatic-fingerprint"
            manager.pending_manual_mode_switch = False
            manager._save_settings = mock.Mock()
            result = mock.Mock(generated_workspace=str(workspace.directory))

            profile = object()
            manager._generator_draft_generated(profile, result)

            cached = manager.workspaces[str(workspace.directory)]
            self.assertIs(manager.active_creation_workspace, cached)
            self.assertEqual("automatic-fingerprint", manager.active_automatic_fingerprint)
            self.assertEqual(str(workspace.directory), manager.selected_pack_path)
            manager.generator.draft_fingerprint.assert_called_once_with(profile)
            manager.manual_slot_editor.continue_from_automatic_workspace.assert_not_called()
            manager._save_settings.assert_called_once_with()

    def test_switching_to_manual_keeps_tab_visible_while_sync_runs(self) -> None:
        manager = SkinManagerV12.__new__(SkinManagerV12)
        manager.generator = mock.Mock()
        manager.generator.current_draft_fingerprint.return_value = "changed"
        manager.generator.generate_shared_draft.return_value = True
        manager.active_creation_workspace = object()
        manager.active_automatic_fingerprint = "previous"
        manager.manual_slot_editor = mock.Mock()
        manager._select_creation_mode = mock.Mock()
        manager._show_error = mock.Mock()

        manager._enter_manual_creation_mode()

        self.assertTrue(manager.pending_manual_mode_switch)
        manager._select_creation_mode.assert_not_called()
        manager.manual_slot_editor.show_background_sync.assert_called_once_with()
        manager.generator.generate_shared_draft.assert_called_once_with()
        manager.manual_slot_editor.edit_workspace.assert_not_called()

    def test_switching_to_manual_reuses_unchanged_per_slot_cache(self) -> None:
        manager = SkinManagerV12.__new__(SkinManagerV12)
        workspace = object()
        manager.generator = mock.Mock()
        manager.generator.current_draft_fingerprint.return_value = "same"
        manager.active_creation_workspace = workspace
        manager.active_automatic_fingerprint = "same"
        manager.manual_slot_editor = mock.Mock(editing_workspace=None)
        manager._select_creation_mode = mock.Mock()
        manager._show_error = mock.Mock()

        manager._enter_manual_creation_mode()

        manager.manual_slot_editor.edit_workspace.assert_called_once_with(workspace)
        manager.generator.generate_shared_draft.assert_not_called()

    def test_empty_default_form_keeps_standalone_manual_mode_available(self) -> None:
        manager = SkinManagerV12.__new__(SkinManagerV12)
        manager.generator = mock.Mock()
        manager.generator.has_draft_source.return_value = False
        manager.active_creation_workspace = None
        manager.manual_slot_editor = mock.Mock(editing_workspace=None)
        manager._select_creation_mode = mock.Mock()
        manager._show_error = mock.Mock()

        manager._enter_manual_creation_mode()

        manager.generator.generate_shared_draft.assert_not_called()
        manager._select_creation_mode.assert_not_called()
        manager._show_error.assert_not_called()

    def test_generated_mode_switch_opens_manual_but_direct_publish_does_not(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = StudioWorkspace.create(
                "local.dooley.live-switch",
                root=Path(temporary),
                hero="Dooley",
                skin="Skin_DOO_01/A",
            )
            manager = SkinManagerV12.__new__(SkinManagerV12)
            manager.workspaces = {}
            manager.manual_slot_editor = mock.Mock()
            manager.generator = mock.Mock()
            manager.generator.draft_fingerprint.return_value = "fingerprint"
            manager.pending_manual_mode_switch = True
            manager._select_creation_mode = mock.Mock()
            manager._save_settings = mock.Mock()

            manager._generator_draft_generated(
                object(), mock.Mock(generated_workspace=str(workspace.directory))
            )

            cached = manager.active_creation_workspace
            manager.manual_slot_editor.continue_from_automatic_workspace.assert_called_once_with(
                cached,
                preserve_overrides=True,
            )
            manager._select_creation_mode.assert_not_called()
            manager.generator.set_manual_preview_overrides.assert_called_once_with(
                manager.manual_slot_editor.current_pack_id.return_value,
                manager.manual_slot_editor.commit_for_mode_switch.return_value,
                total_count=manager.manual_slot_editor.override_count.return_value,
            )
            self.assertFalse(manager.pending_manual_mode_switch)

    def test_default_mode_publish_goes_directly_to_library_management(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = StudioWorkspace.create(
                "local.dooley.direct-publish",
                root=Path(temporary),
                hero="Dooley",
                skin="Skin_DOO_01/A",
            )
            manager = SkinManagerV12.__new__(SkinManagerV12)
            manager.workspaces = {}
            manager.asset_library = mock.Mock()
            manager.pages = mock.Mock()
            manager.management_page = object()
            manager.management_tabs = mock.Mock()
            manager.pack_tab = object()
            manager._save_settings = mock.Mock()
            manager._refresh_everything = mock.Mock()

            manager._generator_import_complete(
                None, mock.Mock(generated_workspace=str(workspace.directory))
            )

            manager.pages.select.assert_called_once_with(manager.management_page)
            manager.management_tabs.select.assert_called_once_with(manager.pack_tab)

    def test_leaving_manual_mode_only_commits_an_in_memory_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = StudioWorkspace.create(
                "local.dooley.manual-cache",
                root=Path(temporary),
                hero="Dooley",
                skin="Skin_DOO_01/A",
            )
            manager = SkinManagerV12.__new__(SkinManagerV12)
            manager.workspaces = {}
            manager.manual_slot_editor = mock.Mock(editing_workspace=workspace)
            preview = {"store_image": object()}
            manager.manual_slot_editor.commit_for_mode_switch.return_value = preview
            manager.manual_slot_editor.current_pack_id.return_value = "local.dooley.manual-cache"
            manager.generator = mock.Mock(editing_workspace_id="local.dooley.manual-cache")
            manager._save_settings = mock.Mock()
            manager._select_creation_mode = mock.Mock()
            manager._show_error = mock.Mock()

            manager._enter_automatic_creation_mode()

            manager.manual_slot_editor.commit_for_mode_switch.assert_called_once_with(
                {slot for _title, slot in PREVIEW_SLOTS}
            )
            manager.manual_slot_editor.build_workspace.assert_not_called()
            manager.generator.set_manual_preview_overrides.assert_called_once_with(
                "local.dooley.manual-cache",
                preview,
                total_count=manager.manual_slot_editor.override_count.return_value,
            )
            manager.generator.edit_workspace.assert_not_called()
            manager._save_settings.assert_called_once_with()

    def test_ui_exposes_exact_five_primary_pages_and_visual_mapping(self) -> None:
        source = (ROOT / "tools" / "bazaar_skin_manager_ui_v12.py").read_text(
            encoding="utf-8"
        )
        for title in ("皮肤部署", "皮肤管理", "皮肤制作", "动画导入", "设置"):
            self.assertIn(f'"{title}"', source)
        self.assertIn('text="自定义皮肤"', source)
        self.assertIn('text="被替换皮肤"', source)
        self.assertIn('text="职业"', source)
        self.assertIn('text="→"', source)
        self.assertIn('text="启动游戏"', source)

    def test_management_has_pack_and_first_class_asset_subpages(self) -> None:
        source = (ROOT / "tools" / "bazaar_skin_manager_ui_v12.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('text="皮肤包管理"', source)
        self.assertIn('text="一级素材管理"', source)
        self.assertIn('text="清空全部引用"', source)
        self.assertIn('text="导入普通素材…"', source)
        self.assertIn('text="复制到剪贴板"', source)
        self.assertIn("class AssetChoiceDialog", source)

    def test_embedded_creator_has_library_and_external_outputs(self) -> None:
        source = (ROOT / "tools" / "asset_generator_ui.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"导入到皮肤库"', source)
        self.assertIn('"导出到指定位置…"', source)
        self.assertIn('text="素材库"', source)
        self.assertIn('"未提供时不生成": "none"', source)
        self.assertIn('text="人物缩放"', source)
        self.assertIn('text="背景裁剪"', source)
        self.assertIn('"商店 / 对局头像（背景合成）", "portrait_gameplay"', source)
        self.assertIn("render_portrait_composite", source)


if __name__ == "__main__":
    unittest.main()
