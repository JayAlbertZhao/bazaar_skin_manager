from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from manual_slot_editor import (  # noqa: E402
    LayerState,
    ManualSlotEditor,
    SlotState,
    _fit_layer,
    _resolved_output,
    automatic_draft_slot_states,
    portrait_frame_preview_overlay,
    render_layered_badge,
)
from asset_generator_core import (  # noqa: E402
    GeneratorProfile,
    ensure_local_badge_assets,
    profile_for_workspace_edit,
)
from asset_generator_ui import AssetGeneratorUI  # noqa: E402
from mod_studio_core import StudioWorkspace  # noqa: E402


class ManualSlotEditorTests(unittest.TestCase):
    def test_default_preview_uses_sparse_manual_override_without_regeneration(self) -> None:
        ui = AssetGeneratorUI.__new__(AssetGeneratorUI)
        ui.manual_override_pack_id = "local.dooley.shared"
        override = Image.new("RGBA", (8, 8), (220, 40, 30, 255))
        ui.manual_preview_overrides = {"store_image": override}
        ui.vars = {"pack_id": mock.Mock(get=mock.Mock(return_value="local.dooley.shared"))}
        ui.pending_preview_slots = {"store_image"}
        ui.preview_refresh_job = "scheduled"
        ui.preview_canvases = {
            "store_image": mock.Mock(winfo_width=mock.Mock(return_value=100), winfo_height=mock.Mock(return_value=100))
        }
        ui.live_renderer = mock.Mock()
        ui._render_output_canvas = mock.Mock()

        ui._refresh_live_previews()

        ui._render_output_canvas.assert_called_once_with("store_image", override)
        ui.live_renderer.render.assert_not_called()

    def test_empty_dirty_slot_does_not_hide_generated_default_preview(self) -> None:
        editor = ManualSlotEditor.__new__(ManualSlotEditor)
        editor.current_slot = ""
        editor._loading_controls = False
        editor.dirty_slots = {"portrait_gameplay"}
        editor.slot_states = {"portrait_gameplay": SlotState()}
        editor.slot_sizes = {"portrait_gameplay": (64, 64)}
        editor.slot_preview_cache = {}

        overrides = editor.commit_for_mode_switch({"portrait_gameplay"})

        self.assertEqual({}, overrides)
        self.assertFalse(editor.has_overrides())
        self.assertEqual(0, editor.override_count())

    def test_missing_override_source_falls_back_but_transparent_file_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            transparent = Path(temporary) / "transparent.png"
            Image.new("RGBA", (16, 16), (0, 0, 0, 0)).save(transparent)
            editor = ManualSlotEditor.__new__(ManualSlotEditor)
            editor.current_slot = ""
            editor._loading_controls = False
            editor.dirty_slots = {"missing", "transparent"}
            editor.slot_states = {
                "missing": SlotState(
                    direct=LayerState(str(Path(temporary) / "missing.png"))
                ),
                "transparent": SlotState(direct=LayerState(str(transparent))),
            }
            editor.slot_sizes = {"missing": (16, 16), "transparent": (16, 16)}
            editor.slot_preview_cache = {}

            overrides = editor.commit_for_mode_switch()

            self.assertEqual({"transparent"}, editor.effective_override_slots())

        self.assertNotIn("missing", overrides)
        self.assertIn("transparent", overrides)
        self.assertIsNone(overrides["transparent"].getbbox())

    @staticmethod
    def _create_badge_template(root: Path) -> Path:
        directory = root / "badge-templates" / "hero-select-gold"
        directory.mkdir(parents=True)
        layers = {
            "base": Image.new("RGBA", (512, 512), (70, 35, 15, 255)),
            "frame_upper": Image.new("RGBA", (512, 512), (0, 0, 0, 0)),
            "frame_lower": Image.new("RGBA", (512, 512), (0, 0, 0, 0)),
            "frame_lower_occlusion": Image.new("RGBA", (512, 512), (0, 0, 0, 255)),
        }
        layers["frame_upper"].paste((245, 195, 70, 255), (0, 0, 512, 12))
        layers["frame_lower"].paste((245, 195, 70, 255), (0, 480, 512, 512))
        layers["frame_lower_occlusion"].paste(
            (255, 255, 255, 255), (0, 480, 512, 512)
        )
        outputs = {}
        for name, image in layers.items():
            path = directory / f"{name}.png"
            image.save(path)
            outputs[name] = {
                "file": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        (directory / "template.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "aigc": False,
                    "colour_inference": False,
                    "size": [512, 512],
                    "layer_order_back_to_front": [
                        "base",
                        "frame_upper",
                        "character",
                        "frame_lower",
                    ],
                    "outputs": outputs,
                }
            ),
            encoding="utf-8",
        )
        return root

    def test_public_build_extracts_missing_badge_template_from_installed_game(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "local-assets"
            game_dir = root / "game"

            def fake_extract(selected_game: Path, output: Path) -> None:
                self.assertEqual(game_dir, selected_game)
                output.mkdir(parents=True)
                (output / "template.json").write_text("{}", encoding="utf-8")

            with (
                mock.patch(
                    "asset_generator_core.preferred_game_install",
                    return_value=SimpleNamespace(game_dir=game_dir),
                ) as preferred,
                mock.patch(
                    "asset_generator_core.extract_game_template",
                    side_effect=fake_extract,
                ) as extract,
            ):
                result = ensure_local_badge_assets(destination, strict=True)

            self.assertEqual(destination.resolve(), result)
            preferred.assert_called_once_with(None)
            extract.assert_called_once()

    def test_automatic_draft_fingerprint_changes_with_live_form_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            character = root / "character.png"
            Image.new("RGBA", (64, 64), (255, 0, 0, 255)).save(character)
            profile = GeneratorProfile(
                profile_path=root / "profile.json",
                adapter_id="dooley-default",
                pack_id="local.dooley.fingerprint",
                name="Draft",
                version="0.1.0",
                character=character,
                background=root / "background-not-provided",
                small_icon=root / "small-icon-not-provided",
                input_metadata=root / "metadata.json",
                badge_template_root=root / "badges",
                workspace_root=root / "workspaces",
                output_zip=root / "draft.zip",
            )

            original = AssetGeneratorUI.draft_fingerprint(profile)
            moved = AssetGeneratorUI.draft_fingerprint(
                profile.__class__(**{**profile.__dict__, "character_offset_x": 12})
            )
            renamed = AssetGeneratorUI.draft_fingerprint(
                profile.__class__(**{**profile.__dict__, "name": "Renamed draft"})
            )

            self.assertNotEqual(original, moved)
            self.assertNotEqual(original, renamed)

    def test_every_layer_keeps_independent_transform(self) -> None:
        state = SlotState(mode="layered")
        state.background = LayerState("background.png", x=-12, y=9, scale=1.35)
        state.character = LayerState("character.png", x=31, y=-17, scale=0.8)

        self.assertEqual((-12, 9, 1.35), (state.background.x, state.background.y, state.background.scale))
        self.assertEqual((31, -17, 0.8), (state.character.x, state.character.y, state.character.scale))

    def test_manual_state_round_trip_resolves_workspace_sources(self) -> None:
        workspace = Path("C:/example/workspace")
        state = SlotState.from_dict(
            {
                "mode": "layered",
                "background": {
                    "workspace_file": "authoring/manual_inputs/store_image/background.png",
                    "x": 4,
                    "y": 5,
                    "scale": 1.2,
                },
                "character": {
                    "workspace_file": "authoring/manual_inputs/store_image/character.png",
                    "x": -3,
                    "y": 7,
                    "scale": 0.75,
                },
            },
            workspace,
        )

        self.assertEqual("layered", state.mode)
        self.assertEqual((4, 5, 1.2), (state.background.x, state.background.y, state.background.scale))
        self.assertTrue(state.background.path.endswith("authoring\\manual_inputs\\store_image\\background.png"))

    def test_alias_inherits_size_and_background_contract(self) -> None:
        recipe = {
            "outputs": {
                "base": {"size": [512, 512], "depends_on": ["background", "character"]},
                "alias": {"alias_of": "base"},
            }
        }

        output = _resolved_output(recipe, "alias")

        self.assertEqual([512, 512], output["size"])
        self.assertEqual(["background", "character"], output["depends_on"])

    def test_fit_layer_applies_per_layer_scale_and_offset(self) -> None:
        source = Image.new("RGBA", (20, 40), (255, 0, 0, 255))
        normal = _fit_layer(source, (100, 100), LayerState(scale=1.0), fit="contain")
        smaller = _fit_layer(
            source,
            (100, 100),
            LayerState(x=20, y=-10, scale=0.5),
            fit="contain",
        )

        self.assertGreater(normal.getbbox()[2] - normal.getbbox()[0], smaller.getbbox()[2] - smaller.getbbox()[0])
        self.assertGreater(smaller.getbbox()[0], normal.getbbox()[0])

    def test_build_workspace_preserves_sources_and_separate_portrait_layers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            background = root / "background.png"
            character = root / "character.png"
            Image.new("RGBA", (80, 40), (20, 40, 80, 255)).save(background)
            Image.new("RGBA", (20, 40), (240, 30, 40, 255)).save(character)

            editor = ManualSlotEditor.__new__(ManualSlotEditor)
            editor.current_slot = ""
            editor.current_layer = "direct"
            editor._loading_controls = False
            editor.editing_workspace = None
            editor.pack_id = "local.dooley.manual-test"
            editor.name_var = mock.Mock(get=mock.Mock(return_value="Manual test"))
            editor.version_var = mock.Mock(get=mock.Mock(return_value="1.3.0"))
            editor.slot_names = {
                "portrait_gameplay": "Encounter portrait",
                "portrait_background": "Encounter background",
            }
            editor.slot_sizes = {
                "portrait_gameplay": (64, 64),
                "portrait_background": (64, 64),
            }
            editor.slot_states = {
                "portrait_gameplay": SlotState(
                    mode="layered",
                    background=LayerState(str(background), x=3, scale=1.1),
                    character=LayerState(str(character), y=-2, scale=0.9),
                ),
                "portrait_background": SlotState(),
            }
            editor.status_var = mock.Mock()
            editor._adapter = lambda: SimpleNamespace(
                hero="Dooley",
                skin="Skin_DOO_01/A",
                skin_name_contains="Default",
            )

            with mock.patch("manual_slot_editor.WORKSPACES_ROOT", root / "workspaces"):
                workspace = editor.build_workspace()

            self.assertEqual("manual_slots", workspace.state["authoring"]["mode"])
            self.assertTrue(workspace.visual_path("portrait_gameplay").is_file())
            self.assertTrue(workspace.visual_path("portrait_background").is_file())
            self.assertEqual(
                1.1,
                workspace.state["authoring"]["manual_slots"]["portrait_gameplay"]
                ["background"]["scale"],
            )
            self.assertTrue(
                (
                    workspace.directory
                    / "authoring/manual_inputs/portrait_gameplay/background.png"
                ).is_file()
            )

    def test_automatic_outputs_and_inputs_seed_the_same_per_slot_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = StudioWorkspace.create(
                "local.dooley.shared-cache",
                root=root,
                hero="Dooley",
                skin="Skin_DOO_01/A",
            )
            inputs = workspace.directory / "authoring" / "inputs"
            inputs.mkdir(parents=True)
            character = inputs / "character.png"
            background = inputs / "background.png"
            Image.new("RGBA", (32, 64), (220, 20, 30, 255)).save(character)
            Image.new("RGBA", (64, 64), (10, 20, 40, 255)).save(background)
            workspace.import_pil_image(
                "portrait_gameplay", Image.new("RGBA", (64, 64), (255, 0, 0, 128))
            )
            workspace.import_pil_image(
                "portrait_background", Image.new("RGBA", (64, 64), (0, 0, 80, 255))
            )
            workspace.import_pil_image(
                "store_image", Image.new("RGBA", (64, 64), (20, 80, 30, 255))
            )
            workspace.state["authoring"] = {
                "inputs": {
                    "character": {"workspace_file": "authoring/inputs/character.png"},
                    "background": {"workspace_file": "authoring/inputs/background.png"},
                }
            }
            workspace.save()

            states = automatic_draft_slot_states(
                workspace,
                ("portrait_gameplay", "portrait_background", "store_image"),
                {"portrait_gameplay", "store_image"},
            )

            self.assertEqual("layered", states["portrait_gameplay"].mode)
            self.assertEqual(
                workspace.visual_path("portrait_gameplay"),
                Path(states["portrait_gameplay"].character.path),
            )
            self.assertEqual(
                workspace.visual_path("store_image"),
                Path(states["store_image"].direct.path),
            )
            self.assertEqual(character, Path(states["store_image"].character.path))
            self.assertEqual(background, Path(states["store_image"].background.path))

    def test_automatic_badge_seed_exposes_source_character_layer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = StudioWorkspace.create(
                "local.dooley.badge-cache",
                root=root,
                hero="Dooley",
                skin="Skin_DOO_01/A",
            )
            source = workspace.directory / "authoring" / "inputs" / "character.png"
            source.parent.mkdir(parents=True)
            Image.new("RGBA", (32, 64), (220, 20, 30, 255)).save(source)
            workspace.import_pil_image(
                "hero_select", Image.new("RGBA", (256, 256), (80, 40, 20, 255))
            )
            workspace.state["authoring"] = {
                "inputs": {"character": {"workspace_file": "authoring/inputs/character.png"}}
            }
            workspace.save()

            states = automatic_draft_slot_states(
                workspace,
                ("hero_select",),
                set(),
                {"hero_select"},
            )

            self.assertEqual("layered", states["hero_select"].mode)
            self.assertEqual(source, Path(states["hero_select"].character.path))
            self.assertEqual("", states["hero_select"].background.path)
            self.assertEqual(
                workspace.visual_path("hero_select"),
                Path(states["hero_select"].direct.path),
            )

    def test_layered_badge_moves_only_character_inside_native_frame(self) -> None:
        recipe = {
            "renderer": "layered_badge",
            "template": {"directory": "badge-templates/hero-select-gold"},
            "character_crop": [0.0, 0.0, 1.0, 1.0],
            "target_alpha_bounds": [80, 5, 432, 455],
            "size": [256, 256],
        }
        character = Image.new("RGBA", (100, 200), (0, 0, 0, 0))
        for y in range(20, 200):
            for x in range(20, 80):
                character.putpixel((x, y), (230, 30, 50, 255))

        with tempfile.TemporaryDirectory() as temporary:
            template_root = self._create_badge_template(Path(temporary))
            centered = render_layered_badge(
                character,
                output_recipe=recipe,
                layer=LayerState(scale=1.0),
                template_root=template_root,
            )
            moved = render_layered_badge(
                character,
                output_recipe=recipe,
                layer=LayerState(x=18, y=-9, scale=0.8),
                template_root=template_root,
            )

        self.assertEqual((256, 256), centered.size)
        self.assertNotEqual(centered.tobytes(), moved.tobytes())
        # An immutable frame pixel remains identical while the internal art moves.
        self.assertEqual(centered.getpixel((128, 247)), moved.getpixel((128, 247)))

    def test_portrait_preview_frame_is_open_on_top_and_occludes_three_sides(self) -> None:
        overlay = portrait_frame_preview_overlay(
            (100, 100),
            {
                "reference_size": [100, 100],
                "inner_bounds": [10, 0, 90, 90],
                "bottom_corner_radius": 4,
            },
        )

        self.assertEqual(0, overlay.getpixel((50, 0))[3])
        self.assertGreater(overlay.getpixel((5, 50))[3], 0)
        self.assertGreater(overlay.getpixel((95, 50))[3], 0)
        self.assertGreater(overlay.getpixel((50, 95))[3], 0)

    def test_automatic_profile_can_reopen_after_per_slot_authoring(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = StudioWorkspace.create(
                "local.dooley.round-trip",
                root=root / "workspaces",
                hero="Dooley",
                skin="Skin_DOO_01/A",
            )
            source = workspace.directory / "authoring" / "inputs" / "character.png"
            source.parent.mkdir(parents=True)
            Image.new("RGBA", (40, 80), (255, 30, 40, 255)).save(source)
            workspace.state["authoring"] = {
                "mode": "manual_slots",
                "inputs": {
                    "character": {"workspace_file": "authoring/inputs/character.png"}
                },
                "manual_slots": {},
                "automatic_draft": {
                    "generator": {"adapter_id": "dooley-default"},
                    "inputs": {
                        "character": {
                            "workspace_file": "authoring/inputs/character.png",
                            "origin": "user_supplied",
                            "aigc": False,
                        }
                    },
                    "adjustments": {"character_scale": 1.25},
                },
            }
            workspace.save()

            profile = profile_for_workspace_edit(
                workspace,
                profile_path=root / "profile.json",
                badge_template_root=root / "badges",
                workspace_root=root / "workspaces",
                output_zip=root / "out.zip",
            )

            self.assertTrue(profile.character.is_file())
            self.assertEqual(1.25, profile.character_scale)

    def test_per_slot_save_reuses_generated_workspace_without_losing_pixels(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = StudioWorkspace.create(
                "local.dooley.in-place",
                root=root,
                name="In-place draft",
                version="1.3.2",
                hero="Dooley",
                skin="Skin_DOO_01/A",
            )
            workspace.import_pil_image(
                "store_image", Image.new("RGBA", (64, 64), (70, 140, 210, 255))
            )
            workspace.state["authoring"] = {
                "generator": {"adapter_id": "dooley-default"},
                "inputs": {},
            }
            workspace.save()
            generated = workspace.visual_path("store_image")

            editor = ManualSlotEditor.__new__(ManualSlotEditor)
            editor.current_slot = ""
            editor.current_layer = "direct"
            editor._loading_controls = False
            editor.editing_workspace = workspace
            editor.automatic_authoring = {
                "generator": {"adapter_id": "dooley-default"},
                "inputs": {},
            }
            editor.pack_id = "local.dooley.in-place"
            editor.name_var = mock.Mock(get=mock.Mock(return_value="In-place draft"))
            editor.version_var = mock.Mock(get=mock.Mock(return_value="1.3.2"))
            editor.slot_names = {"store_image": "Store image"}
            editor.slot_sizes = {"store_image": (64, 64)}
            editor.slot_states = {
                "store_image": SlotState(
                    mode="direct", direct=LayerState(str(generated))
                )
            }
            editor.status_var = mock.Mock()
            editor._adapter = lambda: SimpleNamespace(
                hero="Dooley",
                skin="Skin_DOO_01/A",
                skin_name_contains="Default",
            )

            result = editor.build_workspace()

            with Image.open(result.visual_path("store_image")) as rendered:
                self.assertEqual((0, 0, 64, 64), rendered.convert("RGBA").getbbox())
            self.assertIn("automatic_draft", result.state["authoring"])
            self.assertIn(
                "authoring\\manual_inputs\\store_image\\direct.png",
                editor.slot_states["store_image"].direct.path,
            )

    def test_dynamic_generated_source_is_frozen_before_repeated_transform(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = StudioWorkspace.create(
                "local.dooley.idempotent-transform",
                root=root,
                name="Idempotent transform",
                version="1.4.8",
                hero="Dooley",
                skin="Skin_DOO_01/A",
            )
            workspace.import_pil_image(
                "store_image",
                Image.new("RGBA", (64, 64), (20, 80, 220, 255)),
            )
            generated = workspace.visual_path("store_image")
            assert generated is not None

            editor = ManualSlotEditor.__new__(ManualSlotEditor)
            editor.current_slot = ""
            editor.current_layer = "direct"
            editor._loading_controls = False
            editor.editing_workspace = workspace
            editor.automatic_authoring = {}
            editor.pack_id = "local.dooley.idempotent-transform"
            editor.name_var = mock.Mock(get=mock.Mock(return_value="Idempotent transform"))
            editor.version_var = mock.Mock(get=mock.Mock(return_value="1.4.8"))
            editor.slot_names = {"store_image": "Store image"}
            editor.slot_sizes = {"store_image": (64, 64)}
            editor.slot_states = {
                "store_image": SlotState(
                    mode="direct",
                    direct=LayerState(str(generated), x=4, y=-3, scale=0.5),
                )
            }
            editor.dirty_slots = set()
            editor.slot_preview_cache = {}
            editor.status_var = mock.Mock()
            editor._adapter = lambda: SimpleNamespace(
                hero="Dooley",
                skin="Skin_DOO_01/A",
                skin_name_contains="Default",
            )

            editor._mark_dirty("store_image")
            frozen = Path(editor.slot_states["store_image"].direct.path)
            self.assertIn("authoring\\manual_drafts\\store_image", str(frozen))
            self.assertNotEqual(generated, frozen)

            # Simulate automatic mode replacing its mutable generated output.
            Image.new("RGBA", (64, 64), (230, 30, 20, 255)).save(generated)
            first = editor._materialize_workspace(editor._build_snapshot())
            first_pixels = first.visual_path("store_image").read_bytes()
            second = editor._materialize_workspace(editor._build_snapshot())
            second_pixels = second.visual_path("store_image").read_bytes()

            self.assertEqual(first_pixels, second_pixels)
            with Image.open(second.visual_path("store_image")) as rendered:
                rgba = rendered.convert("RGBA")
                self.assertEqual((20, 80, 220, 255), rgba.getpixel((36, 29)))
                self.assertEqual((20, 13, 52, 45), rgba.getbbox())

    def test_v132_creation_modes_switch_live_and_share_the_manager_cache(self) -> None:
        source = (ROOT / "tools" / "bazaar_skin_manager_ui_v12.py").read_text(encoding="utf-8")
        generator = (ROOT / "tools" / "asset_generator_ui.py").read_text(encoding="utf-8")
        self.assertIn('self.creation_modes.add(automatic_page, text="默认 / 草稿模式")', source)
        self.assertIn('self.creation_modes.add(manual_page, text="逐槽位模式")', source)
        self.assertIn('get("mode") == "manual_slots"', source)
        self.assertIn("on_generated=self._generator_draft_generated", source)
        self.assertIn("self.manual_slot_editor.continue_from_automatic_workspace", source)
        self.assertIn("generate_shared_draft", generator)
        self.assertIn('self.pending_embedded_action = "mode-switch"', generator)
        self.assertIn("if self.embedded:", generator)
        self.assertRegex(
            generator,
            r"workspace_root=\(\s*WORKSPACES_ROOT\.resolve\(\)\s*"
            r"if self\.embedded\s*else",
        )


if __name__ == "__main__":
    unittest.main()
