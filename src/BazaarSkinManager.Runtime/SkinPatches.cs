using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using HarmonyLib;
using UnityEngine;
using UnityEngine.UI;

namespace BazaarSkinManager.TheBazaar
{
    internal static class SkinPatchTargets
    {
        public const string SkinTypeName =
            "TheBazaar.Assets.Scripts.ScriptableObjectsScripts.SkinAssetDataSO";

        public static Type SkinType()
        {
            return AccessTools.TypeByName(SkinTypeName);
        }

        public static bool ShouldReplace(object instance)
        {
            return Plugin.ActivePack != null && Plugin.ActivePack.IsTargetSkin(instance);
        }
    }

    [HarmonyPatch]
    internal static class DisableAnimatedPortraitPatch
    {
        private static bool Prepare()
        {
            return TargetMethod() != null;
        }

        private static MethodBase TargetMethod()
        {
            Type type = SkinPatchTargets.SkinType();
            if (type == null)
            {
                return null;
            }

            return AccessTools.GetDeclaredMethods(type).FirstOrDefault(
                method => method.ReturnType == typeof(bool) &&
                    method.Name.EndsWith("get_UseAnimatedPortrait", StringComparison.Ordinal));
        }

        private static void Postfix(object __instance, ref bool __result)
        {
            if (SkinPatchTargets.ShouldReplace(__instance) &&
                VisualOwnership.IsLocalHeroPortraitLoad())
            {
                __result = false;
            }
        }
    }

    [HarmonyPatch]
    internal static class GameplayPortraitSpritePatch
    {
        private static bool Prepare()
        {
            return TargetMethod() != null;
        }

        private static MethodBase TargetMethod()
        {
            Type type = SkinPatchTargets.SkinType();
            return type == null
                ? null
                : AccessTools.Method(
                    type,
                    "LoadPortraitSpriteAsync",
                    new[] { typeof(CancellationToken) });
        }

        private static void Postfix(object __instance, ref Task<Sprite> __result)
        {
            if (!SkinPatchTargets.ShouldReplace(__instance) ||
                !VisualOwnership.IsLocalHeroPortraitLoad())
            {
                return;
            }

            if (Plugin.ActivePack.UsesPreloadedDeployment(
                "portrait_gameplay"))
            {
                return;
            }

            Sprite sprite = Plugin.ActivePack.Sprite("portrait_gameplay");
            if (sprite != null)
            {
                __result = RuntimeAssetProbe.GameplayPortraitRequested
                    ? RuntimeAssetProbe.CaptureGameplayPortraitAsync(
                        __result,
                        sprite)
                    : Task.FromResult(sprite);
                RuntimeDiagnostics.ReportReplacement(
                    "portrait_gameplay",
                    "LoadPortraitSpriteAsync");
            }
        }
    }

    [HarmonyPatch]
    internal static class TextureTaskPatch
    {
        private static bool Prepare()
        {
            return TargetMethods().Any();
        }

        private static IEnumerable<MethodBase> TargetMethods()
        {
            Type type = SkinPatchTargets.SkinType();
            if (type == null)
            {
                return Enumerable.Empty<MethodBase>();
            }

            string[] names =
            {
                "LoadPortrait",
                "LoadStoreImageAsync",
                "LoadDailyWeeklyImageAssetAsync"
            };
            return AccessTools.GetDeclaredMethods(type)
                .Where(method => names.Contains(method.Name) &&
                    method.ReturnType == typeof(Task<Texture2D>));
        }

        [HarmonyPriority(Priority.First)]
        private static void Postfix(
            object __instance,
            MethodBase __originalMethod,
            object[] __args,
            ref Task<Texture2D> __result)
        {
            if (!SkinPatchTargets.ShouldReplace(__instance))
            {
                return;
            }

            string slot;
            switch (__originalMethod.Name)
            {
                case "LoadStoreImageAsync":
                    slot = "store_image";
                    break;
                case "LoadDailyWeeklyImageAssetAsync":
                    slot = "daily_weekly";
                    break;
                case "LoadPortrait":
                    // The game's argument is named isSmallImage. The false
                    // path is shared by the hero-select skin preview and the
                    // local in-match portrait sprite. Keep portrait_gameplay
                    // transparent: the hero-select preview supplies its own
                    // panel, while GenerateEncounterData assigns the separate
                    // portrait_background behind the in-match sprite. Baking a
                    // background into portrait_gameplay places that rectangle
                    // above the native portrait frame and breaks its occlusion.
                    // Calls passing true use the self-contained portrait_small
                    // asset instead.
                    slot = __args != null && __args.Length > 0 &&
                        __args[0] is bool && (bool)__args[0]
                        ? "portrait_small"
                        : "portrait_gameplay";
                    break;
                default:
                    return;
            }
            // LoadPortrait(bool) ignores its flag in the current game build
            // and always reads storePortraitTextureReference. That native
            // Texture2D is shared by collection/daily surfaces, so the two
            // dedicated portrait slots must remain runtime overrides.
            if (__originalMethod.Name != "LoadPortrait" &&
                Plugin.ActivePack.UsesPreloadedDeployment(slot))
            {
                return;
            }
            Texture2D texture = Plugin.ActivePack.Texture(slot);
            if (texture != null)
            {
                __result = Task.FromResult(texture);
                RuntimeDiagnostics.ReportReplacement(slot, __originalMethod.Name);
            }
        }
    }

    [HarmonyPatch]
    internal static class ObjectTaskPatch
    {
        private static bool Prepare()
        {
            return TargetMethods().Any();
        }

        private static IEnumerable<MethodBase> TargetMethods()
        {
            Type type = SkinPatchTargets.SkinType();
            if (type == null)
            {
                return Enumerable.Empty<MethodBase>();
            }

            string[] names =
            {
                "LoadCollectionListAssetAsync",
                "LoadMarketplaceDetailsAssetAsync",
                "LoadMarketplaceListAssetAsync"
            };
            return AccessTools.GetDeclaredMethods(type)
                .Where(method => names.Contains(method.Name) &&
                    method.ReturnType == typeof(Task<UnityEngine.Object>));
        }

        private static void Postfix(
            object __instance,
            MethodBase __originalMethod,
            ref Task<UnityEngine.Object> __result)
        {
            if (!SkinPatchTargets.ShouldReplace(__instance))
            {
                return;
            }

            string slot;
            switch (__originalMethod.Name)
            {
                case "LoadCollectionListAssetAsync":
                    slot = "collection_list";
                    break;
                case "LoadMarketplaceDetailsAssetAsync":
                    slot = "marketplace_details";
                    break;
                case "LoadMarketplaceListAssetAsync":
                    slot = "marketplace_list";
                    break;
                default:
                    return;
            }
            if (Plugin.ActivePack.UsesPreloadedDeployment(slot))
            {
                return;
            }
            Texture2D texture = Plugin.ActivePack.Texture(slot);
            if (texture != null)
            {
                __result = Task.FromResult<UnityEngine.Object>(texture);
                RuntimeDiagnostics.ReportReplacement(slot, __originalMethod.Name);
            }
        }
    }

    [HarmonyPatch]
    internal static class CollectionDetailsPrefabPatch
    {
        private static bool Prepare()
        {
            return TargetMethod() != null;
        }

        private static MethodBase TargetMethod()
        {
            Type type = SkinPatchTargets.SkinType();
            return type == null
                ? null
                : AccessTools.Method(type, "LoadCollectionDetailsAssetAsync");
        }

        private static void Postfix(
            object __instance,
            ref Task<UnityEngine.Object> __result)
        {
            if (!SkinPatchTargets.ShouldReplace(__instance) || __result == null)
            {
                return;
            }

            __result = AddOverlay(__result);
        }

        private static async Task<UnityEngine.Object> AddOverlay(
            Task<UnityEngine.Object> original)
        {
            UnityEngine.Object result = await original;
            GameObject root = result as GameObject;
            Sprite sprite = Plugin.ActivePack.Sprite("collection_details");
            if (root != null && sprite != null)
            {
                StandingOverlay.AttachToWorld(
                    root,
                    sprite,
                    "collection_details",
                    "LoadCollectionDetailsAssetAsync");
            }
            return result;
        }
    }

    [HarmonyPatch]
    internal static class SkinEditPresentationPrefabPatch
    {
        private static bool Prepare()
        {
            return TargetMethod() != null;
        }

        private static MethodBase TargetMethod()
        {
            Type type = SkinPatchTargets.SkinType();
            return type == null
                ? null
                : AccessTools.Method(type, "LoadSkinEditSkinAsync");
        }

        private static void Postfix(
            object __instance,
            object[] __args,
            ref Task<UnityEngine.Object> __result)
        {
            if (!SkinPatchTargets.ShouldReplace(__instance) ||
                __result == null)
            {
                return;
            }

            string placementName =
                __args != null && __args.Length > 0 && __args[0] != null
                    ? __args[0].ToString()
                    : string.Empty;
            __result = PrepareBeforeDisplay(__result, placementName);
        }

        private static async Task<UnityEngine.Object> PrepareBeforeDisplay(
            Task<UnityEngine.Object> original,
            string placementName)
        {
            UnityEngine.Object result = await original;
            GameObject root = result as GameObject;
            if (root == null || string.IsNullOrEmpty(placementName))
            {
                return result;
            }

            // LoadSkinEditSkinAsync has no player/opponent argument. For the
            // PvP transition, wait until HeroSelectDisplay.AnimateMaterialsIn
            // where the owning HeroViewTransition can be resolved exactly.
            if (string.Equals(
                    placementName,
                    "PvpScreen",
                    StringComparison.Ordinal))
            {
                return result;
            }

            Transform requestedPlacement =
                root.transform.Find(placementName);
            Renderer renderer = requestedPlacement == null
                ? null
                : requestedPlacement
                    .GetComponentsInChildren<Renderer>(true)
                    .Where(candidate => candidate != null &&
                        candidate.enabled)
                    .OrderBy(candidate => candidate.bounds.size.y)
                    .LastOrDefault();
            if (renderer != null)
            {
                SkinEditVisibleOverlayAttacher attacher =
                    root.GetComponent<SkinEditVisibleOverlayAttacher>();
                if (attacher == null)
                {
                    attacher =
                        root.AddComponent<SkinEditVisibleOverlayAttacher>();
                }
                attacher.Configure(
                    placementName,
                    Plugin.ActivePack.Sprite("standing_overlay"));
                Plugin.Log.LogInfo(
                    "Resolved exact SkinEdit placement " + placementName +
                    " before parenting: renderer=" + renderer.name +
                    " layer=" + renderer.gameObject.layer +
                    " activeSelf=" + requestedPlacement.gameObject.activeSelf +
                    " activeInHierarchy=" +
                    requestedPlacement.gameObject.activeInHierarchy + ".");
            }
            else
            {
                Plugin.Log.LogWarning(
                    "Could not prepare exact SkinEdit placement " +
                    placementName + " before display on " + root.name + ".");
            }
            return result;
        }
    }

    [HarmonyPatch]
    internal static class SkinEditVisiblePresentationPatch
    {
        private static Type _displayType;
        private static FieldInfo _loadedAssetField;
        private static FieldInfo _skinEditActiveSkinField;
        private static FieldInfo _placementField;

        private static bool Prepare()
        {
            _displayType = AccessTools.TypeByName("HeroSelectDisplay");
            if (_displayType == null)
            {
                return false;
            }

            _loadedAssetField =
                AccessTools.Field(_displayType, "_loadedAsset");
            _skinEditActiveSkinField =
                AccessTools.Field(_displayType, "_skinEditActiveSkin");
            _placementField =
                AccessTools.Field(_displayType, "screenToDisplaySkinFor");
            return TargetMethod() != null &&
                _loadedAssetField != null &&
                _skinEditActiveSkinField != null &&
                _placementField != null;
        }

        private static MethodBase TargetMethod()
        {
            return _displayType == null
                ? null
                : AccessTools.Method(_displayType, "AnimateMaterialsIn");
        }

        [HarmonyPriority(Priority.Last)]
        private static void Postfix(object __instance)
        {
            object loadedAsset = _loadedAssetField.GetValue(__instance);
            if (!SkinPatchTargets.ShouldReplace(loadedAsset))
            {
                return;
            }

            GameObject root =
                _skinEditActiveSkinField.GetValue(__instance) as GameObject;
            object placement = _placementField.GetValue(__instance);
            Sprite sprite = Plugin.ActivePack.Sprite("standing_overlay");
            if (root == null || placement == null || sprite == null)
            {
                return;
            }

            string placementName = placement.ToString();
            if (!VisualOwnership.ShouldReplaceSkinEditDisplay(
                    __instance,
                    placementName))
            {
                return;
            }
            Transform requestedPlacement =
                root.transform.Find(placementName);
            Renderer renderer = requestedPlacement == null
                ? null
                : requestedPlacement
                    .GetComponentsInChildren<Renderer>(true)
                    .Where(candidate => candidate != null &&
                        candidate.enabled)
                    .OrderBy(candidate => candidate.bounds.size.y)
                    .LastOrDefault();
            if (renderer == null)
            {
                Plugin.Log.LogWarning(
                    "Visible SkinEdit placement " + placementName +
                    " has no enabled renderer on " + root.name + ".");
                return;
            }

            // AnimateMaterialsIn has now parented, layered and activated the
            // exact placement. Attaching here avoids both the original-frame
            // flash and the zero/stale pre-parenting renderer bounds that
            // produced a blank PvP presentation.
            if (!StandingOverlay.HasWorldOverlay(renderer.gameObject))
            {
                StandingOverlay.AttachToWorld(
                    renderer.gameObject,
                    sprite,
                    "standing_overlay",
                    "HeroSelectDisplay.AnimateMaterialsIn exact visible " +
                    placementName);
            }

            WorldStandingOverlayMarker marker =
                renderer.gameObject
                    .GetComponentInChildren<WorldStandingOverlayMarker>(true);
            SpriteRenderer overlay = marker == null
                ? null
                : marker.GetComponent<SpriteRenderer>();
            Plugin.Log.LogInfo(
                "Attached visible SkinEdit placement " + placementName +
                ": rendererBounds=" + renderer.bounds.size +
                " rendererLayer=" + renderer.gameObject.layer +
                " overlayLayer=" +
                (overlay == null ? -1 : overlay.gameObject.layer) +
                " overlayScale=" +
                (overlay == null
                    ? "<null>"
                    : overlay.transform.localScale.ToString()) + ".");
        }
    }

    [HarmonyPatch]
    internal static class HeroSelectStandingPatch
    {
        private const string HeroSelectDisplayTypeName = "HeroSelectDisplay";

        private static bool Prepare()
        {
            return TargetMethod() != null;
        }

        private static MethodBase TargetMethod()
        {
            Type type = AccessTools.TypeByName(HeroSelectDisplayTypeName);
            return type == null
                ? null
                : AccessTools.Method(type, "UpdateSelectedSkin");
        }

        private static void Postfix(
            object __instance,
            ref Task __result)
        {
            if (__instance == null || __result == null)
            {
                return;
            }

            __result = ReconcileAfterUpdate(__result, __instance);
        }

        private static async Task ReconcileAfterUpdate(
            Task original,
            object display)
        {
            await original;
            HeroSelectStandingState.Reconcile(display);
        }
    }

    [HarmonyPatch]
    internal static class HeroSelectButtonPatch
    {
        private static Type _type;

        private static bool Prepare()
        {
            return TargetMethod() != null;
        }

        private static MethodBase TargetMethod()
        {
            _type = AccessTools.TypeByName("HeroItemView");
            if (_type == null)
            {
                return null;
            }

            return AccessTools.Method(_type, "UpdateView");
        }

        private static void Postfix(object __instance)
        {
            HeroSelectIconReconciler.Reconcile(__instance);
        }
    }
}
