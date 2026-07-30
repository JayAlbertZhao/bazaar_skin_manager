from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATCHES = (
    ROOT / "src" / "BazaarSkinManager.Runtime" / "SkinPatches.cs"
).read_text(encoding="utf-8")
SCANNER = (
    ROOT / "src" / "BazaarSkinManager.Runtime" / "UiReplacementScanner.cs"
).read_text(encoding="utf-8")
STANDING_STATE = (
    ROOT / "src" / "BazaarSkinManager.Runtime" / "HeroSelectStandingState.cs"
).read_text(encoding="utf-8")
ICON_RECONCILER = (
    ROOT / "src" / "BazaarSkinManager.Runtime" / "HeroSelectIconReconciler.cs"
).read_text(encoding="utf-8")
LOADER_COVERAGE = (
    ROOT / "src" / "BazaarSkinManager.Runtime" / "SkinLoaderCoverage.cs"
).read_text(encoding="utf-8")
SKIN_AUDIT = (
    ROOT / "src" / "BazaarSkinManager.Runtime" / "RuntimeSkinAudit.cs"
).read_text(encoding="utf-8")
DIAGNOSTICS = (
    ROOT / "src" / "BazaarSkinManager.Runtime" / "RuntimeDiagnostics.cs"
).read_text(encoding="utf-8")
STANDING_OVERLAY = (
    ROOT / "src" / "BazaarSkinManager.Runtime" / "StandingOverlay.cs"
).read_text(encoding="utf-8")
PLUGIN = (
    ROOT / "src" / "BazaarSkinManager.Runtime" / "Plugin.cs"
).read_text(encoding="utf-8")
COMPATIBILITY = (
    ROOT / "src" / "BazaarSkinManager.Runtime" / "RuntimeCompatibility.cs"
).read_text(encoding="utf-8")
RUNTIME_ASSETS = (
    ROOT / "src" / "BazaarSkinManager.Runtime" / "RuntimeAssets.cs"
).read_text(encoding="utf-8")
ADAPTER = json.loads(
    (
        ROOT / "manager" / "adapters" / "mak-default.json"
    ).read_text(encoding="utf-8")
)


class RuntimeStructureTests(unittest.TestCase):
    def test_hero_button_patch_changes_only_content_icon(self) -> None:
        self.assertIn(
            "HeroSelectIconReconciler.Reconcile(__instance);",
            PATCHES,
        )
        self.assertIn('transform.Find("Content/Icon")', ICON_RECONCILER)
        self.assertIn("icon.sprite = replacement;", ICON_RECONCILER)
        self.assertNotIn('AccessTools.Field(_type, "HeroItemImage")', PATCHES)
        self.assertNotIn(
            'AccessTools.Field(_type, "SelectedHeroItemImage")',
            PATCHES,
        )

    def test_existing_mak_icon_is_reconciled_without_update_callback(self) -> None:
        self.assertIn(
            "Resources.FindObjectsOfTypeAll(_heroItemType)",
            ICON_RECONCILER,
        )
        self.assertIn('"Mak"', ICON_RECONCILER)
        self.assertIn('"Content/Icon"', ICON_RECONCILER)
        self.assertNotIn("MatchHierarchy", ICON_RECONCILER)
        self.assertNotIn("SelectedHeroItemImage", ICON_RECONCILER)
        self.assertIn('"already applied"', ICON_RECONCILER)
        self.assertIn('"not loaded"', ICON_RECONCILER)

    def test_generic_scanner_does_not_match_parent_hierarchies(self) -> None:
        self.assertNotIn("MatchHierarchy", SCANNER)
        self.assertNotIn("HeroItemView_MAK", SCANNER)

    def test_preloaded_native_slots_are_not_replaced_at_runtime(
        self,
    ) -> None:
        preloaded = {
            item["slot"]
            for item in ADAPTER["visual_replacements"]
            if (item.get("deployment") or {}).get("mode")
            == "preload_unity_texture2d"
        }
        self.assertEqual(
            preloaded,
            {
                "portrait_gameplay",
                "store_image",
                "collection_list",
                "marketplace_list",
                "marketplace_details",
                "daily_weekly",
                "hero_select",
                "hero_icon_small",
            },
        )
        self.assertIn('"preload_unity_texture2d"', RUNTIME_ASSETS)
        self.assertIn("replacement.Deployment.Mode", RUNTIME_ASSETS)
        self.assertIn("UsesPreloadedDeployment", RUNTIME_ASSETS)
        self.assertIn(
            "Plugin.ActivePack.UsesPreloadedDeployment(slot)",
            PATCHES,
        )
        self.assertIn(
            '__originalMethod.Name != "LoadPortrait"',
            PATCHES,
        )
        self.assertIn(
            "always reads storePortraitTextureReference",
            PATCHES,
        )
        self.assertIn(
            'Plugin.ActivePack.UsesPreloadedDeployment("hero_select")',
            ICON_RECONCILER,
        )

    def test_standing_state_is_exact_and_reversible(self) -> None:
        for field_name in (
            '"MAK"',
            '"_loadedAsset"',
            '"_selectedHero"',
            '"_skinEditActiveSkin"',
        ):
            self.assertIn(field_name, STANDING_STATE)
        self.assertIn("SkinPatchTargets.ShouldReplace(loadedAsset)", STANDING_STATE)
        self.assertIn("component.gameObject.activeInHierarchy", STANDING_STATE)
        self.assertIn("makGraphic.gameObject.activeInHierarchy", STANDING_STATE)
        self.assertIn('"Common"', STANDING_STATE)
        self.assertIn("selectedMak || selectedCommon", STANDING_STATE)
        self.assertIn("StandingOverlay.AttachToGraphic", STANDING_STATE)
        self.assertIn("StandingOverlay.AttachToWorld", STANDING_STATE)
        self.assertIn("StandingOverlay.RemoveFromGraphic", STANDING_STATE)
        self.assertIn("StandingOverlay.RemoveFromWorld", STANDING_STATE)

    def test_runtime_version_is_0_9_1(self) -> None:
        plugin = (
            ROOT / "src" / "BazaarSkinManager.Runtime" / "Plugin.cs"
        ).read_text(encoding="utf-8")
        assembly = (
            ROOT / "src" / "BazaarSkinManager.Runtime" / "AssemblyInfo.cs"
        ).read_text(encoding="utf-8")

        self.assertIn('PluginVersion = "0.9.1"', plugin)
        self.assertIn('AssemblyVersion("0.9.1.0")', assembly)

    def test_audio_replacement_is_exact_predecoded_and_fail_open(self) -> None:
        root = ROOT / "src" / "BazaarSkinManager.Runtime"
        replacement = (root / "RuntimeAudioReplacement.cs").read_text(
            encoding="utf-8"
        )
        audio_pack = (root / "RuntimeAudioPack.cs").read_text(encoding="utf-8")
        decoder = (root / "PcmWaveDecoder.cs").read_text(encoding="utf-8")

        self.assertIn("RuntimeAudioReplacement.Install(_harmony);", PLUGIN)
        self.assertIn('"MakAudioSO"', replacement)
        self.assertIn('"MakMerchantAudioSO"', replacement)
        self.assertIn("CanonicalGuid(guid)", replacement)
        self.assertIn("ReadSelectors(__args[2])", replacement)
        self.assertIn("if (!Play(route, __instance, true))", replacement)
        self.assertIn("return true;", replacement)
        self.assertIn("return false;", replacement)
        self.assertIn("CanPlayByPercentage", replacement)
        self.assertIn("IsTutorialVOPlaying", replacement)
        self.assertIn("IsReplayActive()", replacement)
        self.assertIn("LateCardAudioLoad", replacement)
        self.assertIn("PLAYBACK_STATE.PLAYING", replacement)
        self.assertIn('"Menu.CharacterSelect"', replacement)
        self.assertIn('"Menu.EquipMusic"', replacement)

        self.assertIn("SHA-256 mismatch.", audio_pack)
        self.assertIn("PcmWaveDecoder.CreateClip", audio_pack)
        self.assertIn("Audio route has no usable variants", audio_pack)
        self.assertIn("Only integer PCM WAV is supported.", decoder)
        self.assertIn("Voice WAV must be mono.", decoder)
        self.assertIn("Voice WAV must be 16-bit PCM.", decoder)

    def test_exact_skin_edit_placement_is_attached_when_visible(self) -> None:
        self.assertIn("class SkinEditPresentationPrefabPatch", PATCHES)
        self.assertIn("class SkinEditVisiblePresentationPatch", PATCHES)
        self.assertIn('"LoadSkinEditSkinAsync"', PATCHES)
        self.assertIn(
            "PrepareBeforeDisplay(__result, placementName)",
            PATCHES,
        )
        self.assertIn(
            'AccessTools.Method(_displayType, "AnimateMaterialsIn")',
            PATCHES,
        )
        self.assertIn("root.transform.Find(placementName)", PATCHES)
        self.assertIn("object[] __args", PATCHES)
        self.assertIn(
            '"HeroSelectDisplay.AnimateMaterialsIn exact visible "',
            PATCHES,
        )
        self.assertIn("SkinEditVisibleOverlayAttacher", PATCHES)
        self.assertIn(
            "class SkinEditVisibleOverlayAttacher",
            STANDING_OVERLAY,
        )
        self.assertIn(
            "FindBestRenderCamera",
            STANDING_OVERLAY,
        )
        self.assertIn("Camera.allCameras", STANDING_OVERLAY)
        self.assertIn("WorldToScreenPoint", STANDING_OVERLAY)
        self.assertIn("ProjectedHeight", STANDING_OVERLAY)
        self.assertIn("renderCamera.pixelHeight * 0.82f", STANDING_OVERLAY)
        self.assertIn("renderCamera.pixelHeight * 0.17f", STANDING_OVERLAY)
        self.assertIn("spriteRenderer.bounds", STANDING_OVERLAY)
        self.assertIn(
            "root.transform.localToWorldMatrix.determinant < 0f",
            STANDING_OVERLAY,
        )
        self.assertIn(
            "transform.rotation = renderCamera.transform.rotation",
            STANDING_OVERLAY,
        )
        self.assertIn(
            '"SkinEdit visible-frame exact "',
            STANDING_OVERLAY,
        )
        self.assertIn("overlay.layer = root.layer;", STANDING_OVERLAY)

    def test_hero_select_is_exclusive_to_direct_content_icon_patch(self) -> None:
        hero_select = next(
            replacement
            for replacement in ADAPTER["visual_replacements"]
            if replacement["slot"] == "hero_select"
        )
        self.assertTrue(hero_select["direct_only"])
        self.assertEqual(hero_select["match_names"], [])
        runtime_assets = (
            ROOT / "src" / "BazaarSkinManager.Runtime" / "RuntimeAssets.cs"
        ).read_text(encoding="utf-8")
        self.assertIn("replacement.DirectOnly ||", runtime_assets)

    def test_gameplay_portrait_uses_exact_runtime_name_matching(self) -> None:
        portrait = next(
            replacement
            for replacement in ADAPTER["visual_replacements"]
            if replacement["slot"] == "portrait_gameplay"
        )
        self.assertEqual(portrait["match_mode"], "exact")
        self.assertEqual(
            portrait["match_names"],
            ["Skin_MAK_01a_Portrait"],
        )

        matcher = (
            ROOT / "src" / "BazaarSkinManager.Runtime" / "AssetNameMatcher.cs"
        ).read_text(encoding="utf-8")
        runtime_assets = (
            ROOT / "src" / "BazaarSkinManager.Runtime" / "RuntimeAssets.cs"
        ).read_text(encoding="utf-8")
        self.assertIn("ExactMode = \"exact\"", matcher)
        self.assertIn("return string.Equals(", matcher)
        self.assertIn("AssetNameMatcher.Matches(", runtime_assets)

    def test_runtime_compatibility_gate_precedes_every_activation_hook(
        self,
    ) -> None:
        gate = PLUGIN.index("RuntimeCompatibility.ValidateCurrent(")
        pack_load = PLUGIN.index("RuntimePack.LoadFirstEnabled(")
        hook_calls = (
            "_harmony = new Harmony(PluginGuid);",
            "_harmony.PatchAll();",
            "SkinLoaderCoverage.Install(_harmony);",
            "RuntimeAudioTrace.Install(_harmony);",
            "gameObject.AddComponent<UiReplacementScanner>()",
        )
        self.assertLess(gate, pack_load)
        for hook in hook_calls:
            self.assertGreater(PLUGIN.index(hook), gate, hook)
        fail_closed_block = PLUGIN[
            gate:PLUGIN.index("string root =", gate)
        ]
        self.assertIn("Logger.LogError(", fail_closed_block)
        self.assertIn("return;", fail_closed_block)
        self.assertIn("before loading its pack or ", fail_closed_block)
        self.assertIn('"installing hooks: "', fail_closed_block)
        self.assertIn('"runtime-compatibility.json"', COMPATIBILITY)
        self.assertIn("Game file fingerprint changed:", COMPATIBILITY)

    def test_all_safe_object_and_data_loaders_preserve_required_types(self) -> None:
        for loader in (
            '"LoadChestRewardAsync"',
            '"LoadCollectionDetailsAssetAsync"',
            '"LoadAnimatedPortraitAsync"',
            '"LoadCollectibleInspectionAssetAsync"',
            '"GenerateEncounterData"',
        ):
            self.assertIn(loader, LOADER_COVERAGE)
        self.assertIn("UpdateMaterial", LOADER_COVERAGE)
        self.assertIn("StandingOverlay.AttachToWorld", LOADER_COVERAGE)
        self.assertIn('"LoadedCollectibleInstance"', LOADER_COVERAGE)
        self.assertIn('"HeroSkinBackgroundImage"', LOADER_COVERAGE)
        self.assertIn('"portraitTextureReference"', LOADER_COVERAGE)
        self.assertIn('"backgroundTextureReference"', LOADER_COVERAGE)
        self.assertIn(
            "return ValueTypeResultMutation.Rebox<T>(boxed);",
            LOADER_COVERAGE,
        )
        self.assertNotIn("return result;", LOADER_COVERAGE)

    def test_unsupported_gameplay_loader_fails_narrowly(self) -> None:
        self.assertIn('"LoadGameplayAssetAsync"', LOADER_COVERAGE)
        self.assertIn(
            "The game implementation explicitly returns null",
            LOADER_COVERAGE,
        )

    def test_skin_edit_loader_reports_exact_resolved_placement(self) -> None:
        self.assertIn('"LoadSkinEditSkinAsync"', LOADER_COVERAGE)
        self.assertIn(
            "exact requested SkinEdit placement was resolved",
            LOADER_COVERAGE,
        )

    def test_runtime_audit_is_request_gated_and_metadata_only(self) -> None:
        self.assertIn('"skin-audit"', SKIN_AUDIT)
        self.assertIn('"request.json"', SKIN_AUDIT)
        self.assertIn("if (!File.Exists(RequestPath))", SKIN_AUDIT)
        self.assertIn('"loader_outcomes"', SKIN_AUDIT)
        self.assertIn('"loaded_objects"', SKIN_AUDIT)
        self.assertIn('"runtime_key"', SKIN_AUDIT)
        self.assertNotIn("EncodeToPNG", SKIN_AUDIT)
        self.assertNotIn("File.WriteAllBytes", SKIN_AUDIT)

    def test_requested_runtime_audit_invokes_safe_surface_loaders(self) -> None:
        for request_flag in (
            '"invoke_safe_loaders"',
            '"exercise_central_standing"',
            '"test_callbacks"',
        ):
            self.assertIn(request_flag, SKIN_AUDIT)
        for loader in (
            '"LoadPortrait"',
            '"LoadPortraitSpriteAsync"',
            '"LoadDailyWeeklyImageAssetAsync"',
            '"LoadCollectionListAssetAsync"',
            '"LoadCollectionDetailsAssetAsync"',
            '"LoadStoreImageAsync"',
            '"LoadMarketplaceListAssetAsync"',
            '"LoadMarketplaceDetailsAssetAsync"',
            '"LoadAnimatedPortraitAsync"',
            '"LoadCollectibleInspectionAssetAsync"',
        ):
            self.assertIn("await InvokeLoader", SKIN_AUDIT)
            self.assertIn(loader, SKIN_AUDIT)
        self.assertIn("CleanupInstantiatedResult(result)", SKIN_AUDIT)
        self.assertIn("StandingOverlay.RemoveFromWorld(root)", SKIN_AUDIT)
        self.assertNotIn('InvokeLoader(target, "LoadChestRewardAsync"', SKIN_AUDIT)
        self.assertNotIn('InvokeLoader(target, "GenerateEncounterData"', SKIN_AUDIT)

    def test_central_standing_diagnostic_is_bounded_and_reversible(self) -> None:
        self.assertIn("TryRunDiagnostic", STANDING_STATE)
        self.assertIn("_loadedAssetField.SetValue(candidate, target)", STANDING_STATE)
        self.assertIn("_loadedAssetField.SetValue(candidate, null)", STANDING_STATE)
        self.assertIn("StandingOverlay.HasGraphicOverlay(graphic)", STANDING_STATE)
        self.assertIn("originalLoaded", STANDING_STATE)
        self.assertIn("originalSelected", STANDING_STATE)
        self.assertIn("originalEnabled", STANDING_STATE)
        self.assertIn("originalColor", STANDING_STATE)
        self.assertIn("for (int attempt = 0; attempt < 60", SKIN_AUDIT)

    def test_chest_material_and_image_state_are_restored(self) -> None:
        for captured_state in (
            "OriginalSharedMaterial",
            "OriginalSharedMaterialMainTexture",
            "OriginalInstancedMaterial",
            "OriginalInstancedMaterialMainTexture",
            "OriginalRendererEnabled",
            "OriginalImageMaterials",
            "OriginalTextures",
            "OriginalImageEnabled",
            "OriginalImageColors",
            "OriginalImageActive",
        ):
            self.assertIn(captured_state, LOADER_COVERAGE)
        self.assertIn("private void OnDestroy()", LOADER_COVERAGE)
        self.assertIn("marker.Restore();", LOADER_COVERAGE)
        self.assertIn("RunRestorationSelfTest", LOADER_COVERAGE)
        self.assertIn("SkinLoaderCoverage.RemoveAll();", PLUGIN)

    def test_chest_self_test_models_both_real_material_mutations(self) -> None:
        self.assertIn(
            "controller.UpdateMaterial(",
            LOADER_COVERAGE,
        )
        self.assertIn(
            "instancedMaterial.mainTexture = texture;",
            LOADER_COVERAGE,
        )
        self.assertIn(
            "image.material = defaultImageMaterial;",
            LOADER_COVERAGE,
        )
        self.assertIn(
            "OriginalInstancedMaterial.mainTexture =",
            LOADER_COVERAGE,
        )
        self.assertIn(
            "OriginalInstancedMaterialMainTexture;",
            LOADER_COVERAGE,
        )
        self.assertIn(
            "image.material = OriginalImageMaterials[index];",
            LOADER_COVERAGE,
        )
        self.assertIn(
            "instancedMaterial.mainTexture,\n                        originalTexture",
            LOADER_COVERAGE,
        )
        self.assertIn(
            "image.material,\n                        originalImageMaterial",
            LOADER_COVERAGE,
        )

    def test_chest_mutable_state_is_captured_before_replacement_update(self) -> None:
        update = LOADER_COVERAGE.index(
            "updateMaterial.Invoke(controller, new object[] { texture });"
        )
        for capture in (
            "marker.OriginalSharedMaterialMainTexture =",
            "marker.OriginalInstancedMaterialMainTexture =",
            "marker.OriginalImageMaterials =",
        ):
            self.assertLess(LOADER_COVERAGE.index(capture), update, capture)

    def test_diagnostics_distinguish_required_states_and_are_deduplicated(self) -> None:
        for state in (
            '"applied"',
            '"already applied"',
            '"not loaded"',
            '"wrong hero/skin"',
            '"unsupported type"',
        ):
            self.assertTrue(
                state in LOADER_COVERAGE
                or state in ICON_RECONCILER
                or state in STANDING_STATE,
                state,
            )
        self.assertIn("ReportedLoaderStates", DIAGNOSTICS)
        self.assertIn("if (!ReportedLoaderStates.Add(key))", DIAGNOSTICS)

    def test_overlay_reversibility_paths_remain_present(self) -> None:
        self.assertIn("marker.OriginalEnabled", STANDING_OVERLAY)
        self.assertIn("marker.OriginalColor", STANDING_OVERLAY)
        self.assertIn("marker.OriginalEnabled", STANDING_OVERLAY)
        self.assertIn("renderer.enabled = false;", STANDING_OVERLAY)
        self.assertIn("OriginalEnabled[index]", STANDING_OVERLAY)
        self.assertIn("public static void RemoveFromWorld", STANDING_OVERLAY)
        self.assertIn("public static void RemoveAll", STANDING_OVERLAY)
        self.assertIn("StandingOverlay.RemoveFromGraphic", STANDING_STATE)


if __name__ == "__main__":
    unittest.main()
