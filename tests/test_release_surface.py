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

    def test_manager_build_bundles_and_checks_the_fmod_runtime(self) -> None:
        build = (ROOT / "build-manager.ps1").read_text(encoding="utf-8")
        self.assertIn("fmod_toolkit\\libfmod\\Windows\\x64\\fmod.dll", build)
        self.assertIn("--add-binary", build)
        self.assertIn("FMOD runtime required by UnityPy is missing", build)

    def test_manager_release_ui_is_chinese_and_has_clear_action(self) -> None:
        ui = (ROOT / "tools" / "bazaar_skin_manager_ui.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('self.root.title("The Bazaar 皮肤管理器")', ui)
        self.assertIn('text="一键清空已加载皮肤"', ui)
        self.assertIn("def _clear_loaded_skin(self)", ui)
        self.assertIn("--self-test-fmod", ui)
        self.assertIn("--smoke-import", ui)


if __name__ == "__main__":
    unittest.main()
