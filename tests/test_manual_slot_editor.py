from __future__ import annotations

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
)
from asset_generator_core import profile_for_workspace_edit  # noqa: E402
from mod_studio_core import StudioWorkspace  # noqa: E402


class ManualSlotEditorTests(unittest.TestCase):
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
                version="1.3.1",
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
            editor.version_var = mock.Mock(get=mock.Mock(return_value="1.3.1"))
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

    def test_v131_creation_modes_share_the_manager_workspace_cache(self) -> None:
        source = (ROOT / "tools" / "bazaar_skin_manager_ui_v12.py").read_text(encoding="utf-8")
        generator = (ROOT / "tools" / "asset_generator_ui.py").read_text(encoding="utf-8")
        self.assertIn('self.creation_modes.add(automatic_page, text="自动生成模式")', source)
        self.assertIn('self.creation_modes.add(manual_page, text="逐槽位模式")', source)
        self.assertIn('get("mode") == "manual_slots"', source)
        self.assertIn("on_generated=self._generator_draft_generated", source)
        self.assertIn("self.manual_slot_editor.continue_from_automatic_workspace", source)
        self.assertIn("WORKSPACES_ROOT if self.embedded", generator)


if __name__ == "__main__":
    unittest.main()
