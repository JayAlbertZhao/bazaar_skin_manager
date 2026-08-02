from __future__ import annotations

import hashlib
import json
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

    def test_release_workflow_publishes_versioned_changelog_notes(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn('Get-Content "CHANGELOG.md" -Raw', workflow)
        self.assertIn('throw "CHANGELOG.md has no section for $version"', workflow)
        self.assertIn('"--notes-file", $notesPath', workflow)
        self.assertIn("gh release edit $tag", workflow)
        self.assertNotIn('"--generate-notes"', workflow)

    def test_1_0_0_release_is_stable(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn('default: "1.0.0"', workflow)
        self.assertIn('if ($tag.EndsWith("-experimental"))', workflow)
        self.assertNotIn("v1.0.0-experimental", workflow)
        self.assertNotIn("1.0.0 (experimental)", workflow)

    def test_release_workflow_builds_both_software_components(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("build-asset-generator.ps1", workflow)
        self.assertIn("package-asset-generator-portable.ps1", workflow)
        self.assertIn("TheBazaarAssetGenerator-Portable-", workflow)
        self.assertNotIn("DooleyChameleon", workflow)
        self.assertNotIn("KotoneAlchemist", workflow)

    def test_committed_runtime_metadata_matches_binary(self) -> None:
        runtime = ROOT / "manager" / "runtime" / "BazaarSkinManager.Runtime.dll"
        metadata = json.loads(
            (ROOT / "manager" / "runtime" / "runtime-build.json").read_text(
                encoding="utf-8-sig"
            )
        )
        self.assertEqual(metadata["version"], "1.0.0")
        self.assertEqual(metadata["bytes"], runtime.stat().st_size)
        self.assertEqual(
            metadata["sha256"], hashlib.sha256(runtime.read_bytes()).hexdigest()
        )

    def test_0_9_62_excludes_badge_authoring_pipeline(self) -> None:
        self.assertFalse((ROOT / "tools" / "badge_compositor.py").exists())
        studio = (ROOT / "tools" / "mod_studio_core.py").read_text(
            encoding="utf-8"
        )
        ui = (ROOT / "tools" / "bazaar_skin_manager_ui.py").read_text(
            encoding="utf-8"
        )
        adapter = (ROOT / "manager" / "adapters" / "mak-default.json").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("badge_compositor", studio)
        self.assertNotIn("generate_hero_select_badge", studio)
        self.assertNotIn("_browse_badge_portrait", ui)
        self.assertNotIn("hero_select_badge", adapter)

    def test_silent_uninstall_does_not_wait_for_a_dialog(self) -> None:
        installer = (
            ROOT / "installer" / "TheBazaarModManager.iss"
        ).read_text(encoding="utf-8")
        self.assertIn("if UninstallSilent then", installer)
        self.assertIn("Result := True", installer)

    def test_installer_detects_and_upgrades_previous_installation(self) -> None:
        installer = (
            ROOT / "installer" / "TheBazaarModManager.iss"
        ).read_text(encoding="utf-8")
        self.assertIn('AppId={#MyAppId}', installer)
        self.assertIn('UsePreviousAppDir=yes', installer)
        self.assertIn('UsePreviousGroup=yes', installer)
        self.assertIn('function InstalledVersion(var Version: String)', installer)
        self.assertIn("RegQueryStringValue(", installer)
        self.assertIn("Setup will upgrade it in place", installer)

    def test_manager_build_bundles_and_checks_the_fmod_runtime(self) -> None:
        build = (ROOT / "build-manager.ps1").read_text(encoding="utf-8")
        self.assertIn("fmod_toolkit\\libfmod\\Windows\\x64\\fmod.dll", build)
        self.assertIn("--add-binary", build)
        self.assertIn("--collect-all archspec", build)
        self.assertIn("FMOD runtime required by UnityPy is missing", build)
        self.assertIn("--self-test-release-runtime", build)
        self.assertIn("Frozen release runtime self-test failed", build)
        self.assertLess(
            build.index("dist\\runtime\\BazaarSkinManager.Runtime.dll"),
            build.index("manager\\runtime\\BazaarSkinManager.Runtime.dll"),
        )
        self.assertIn("Release runtime metadata SHA-256 does not match", build)

    def test_manager_build_publishes_verified_adapter_capabilities(self) -> None:
        build = (ROOT / "build-manager.ps1").read_text(encoding="utf-8")
        self.assertIn("$adapterCapabilities", build)
        self.assertIn("adapter_version", build)
        self.assertIn("authoring_recipe_version", build)
        self.assertIn("adapters = $adapterCapabilities", build)

    def test_manager_release_ui_is_chinese_and_has_clear_action(self) -> None:
        ui = (ROOT / "tools" / "bazaar_skin_manager_ui.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'self.root.title(f"The Bazaar 皮肤管理器 v{MANAGER_VERSION}")',
            ui,
        )
        self.assertIn('text="清空已加载皮肤"', ui)
        self.assertIn("def _clear_loaded_skin(self)", ui)
        self.assertIn("--self-test-release-runtime", ui)
        self.assertIn("root = RootClass()", ui)
        self.assertIn("root.update_idletasks()", ui)
        self.assertIn("--smoke-import", ui)
        self.assertIn("--smoke-deploy", ui)

    def test_asset_generator_keeps_actions_visible_and_tabs_scrollable(self) -> None:
        ui = (ROOT / "tools" / "asset_generator_ui.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('actions.pack(side="bottom"', ui)
        self.assertIn("_scrollable_notebook_page", ui)
        self.assertIn("both authoring tabs must provide vertical scrolling", ui)


if __name__ == "__main__":
    unittest.main()
