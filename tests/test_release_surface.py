from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import bazaar_skin_manager as manager  # noqa: E402
from mod_studio_core import StudioWorkspace  # noqa: E402


class ReleaseSurfaceTests(unittest.TestCase):
    def test_installer_runtime_is_present(self) -> None:
        self.assertTrue(
            (ROOT / "manager" / "runtime" / "BazaarSkinManager.Runtime.dll").is_file()
        )

    def test_workspace_launch_forwards_manual_game_path(self) -> None:
        workspace = StudioWorkspace(ROOT, {})
        game = Path(r"E:\Games\The Bazaar")
        with mock.patch(
            "mod_studio_core.launch_game",
            return_value={"launched": True},
        ) as launch:
            workspace.launch_game(game)
        launch.assert_called_once_with(game)

    def test_release_workflow_does_not_publish_asset_pack(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("-AssetPack", workflow)
        self.assertNotIn("packs/", workflow)
        self.assertIn("TheBazaarModManager-Setup-", workflow)

    def test_silent_uninstall_does_not_wait_for_a_dialog(self) -> None:
        installer = (
            ROOT / "installer" / "TheBazaarModManager.iss"
        ).read_text(encoding="utf-8")
        self.assertIn("if UninstallSilent then", installer)
        self.assertIn("Result := True", installer)


if __name__ == "__main__":
    unittest.main()
