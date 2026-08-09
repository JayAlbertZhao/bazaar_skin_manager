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
)


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

    def test_v13_creation_surface_has_two_authoring_modes(self) -> None:
        source = (ROOT / "tools" / "bazaar_skin_manager_ui_v12.py").read_text(encoding="utf-8")
        self.assertIn('self.creation_modes.add(automatic_page, text="自动生成模式")', source)
        self.assertIn('self.creation_modes.add(manual_page, text="逐槽位模式")', source)
        self.assertIn('get("mode") == "manual_slots"', source)


if __name__ == "__main__":
    unittest.main()
