using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Threading.Tasks;
using HarmonyLib;
using UnityEngine;
using UnityEngine.UI;

namespace BazaarSkinManager.TheBazaar
{
    internal static class ValueTypeResultMutation
    {
        public static T Rebox<T>(object boxed)
        {
            return (T)boxed;
        }
    }

    internal sealed class ChestRestoreSelfTestController : MonoBehaviour
    {
        private Material instancedMaterial;

        public Material CurrentMaterial
        {
            get { return instancedMaterial; }
        }

        public void Initialize(Material material)
        {
            instancedMaterial = material;
        }

        public void UpdateMaterial(
            Texture2D texture,
            Renderer assetRenderer)
        {
            if (instancedMaterial == null)
            {
                instancedMaterial =
                    new Material(assetRenderer.sharedMaterial);
            }
            instancedMaterial.mainTexture = texture;
            assetRenderer.sharedMaterial = instancedMaterial;
        }
    }

    internal sealed class SkinLoaderAppliedMarker : MonoBehaviour
    {
        public string Loader;
        public string Slot;
        public Component Controller;
        public Renderer AssetRenderer;
        public Material OriginalSharedMaterial;
        public Texture OriginalSharedMaterialMainTexture;
        public Material OriginalInstancedMaterial;
        public Texture OriginalInstancedMaterialMainTexture;
        public Material ReplacementInstancedMaterial;
        public bool OriginalRendererEnabled;
        public RawImage[] PreservedImages;
        public Material[] OriginalImageMaterials;
        public Texture[] OriginalTextures;
        public bool[] OriginalImageEnabled;
        public Color[] OriginalImageColors;
        public bool[] OriginalImageActive;

        private bool _restored;

        private void OnDestroy()
        {
            Restore();
        }

        public void Restore()
        {
            if (_restored)
            {
                return;
            }
            _restored = true;

            if (OriginalSharedMaterial != null)
            {
                OriginalSharedMaterial.mainTexture =
                    OriginalSharedMaterialMainTexture;
            }
            if (OriginalInstancedMaterial != null)
            {
                OriginalInstancedMaterial.mainTexture =
                    OriginalInstancedMaterialMainTexture;
            }
            if (AssetRenderer != null)
            {
                AssetRenderer.sharedMaterial = OriginalSharedMaterial;
                AssetRenderer.enabled = OriginalRendererEnabled;
            }
            if (Controller != null)
            {
                FieldInfo instancedMaterialField = AccessTools.Field(
                    Controller.GetType(),
                    "instancedMaterial");
                if (instancedMaterialField != null)
                {
                    instancedMaterialField.SetValue(
                        Controller,
                        OriginalInstancedMaterial);
                }
            }
            if (ReplacementInstancedMaterial != null &&
                !object.ReferenceEquals(
                    ReplacementInstancedMaterial,
                    OriginalInstancedMaterial) &&
                !object.ReferenceEquals(
                    ReplacementInstancedMaterial,
                    OriginalSharedMaterial))
            {
                UnityEngine.Object.Destroy(ReplacementInstancedMaterial);
            }

            if (PreservedImages == null)
            {
                return;
            }
            for (int index = 0; index < PreservedImages.Length; index++)
            {
                RawImage image = PreservedImages[index];
                if (image == null)
                {
                    continue;
                }
                if (OriginalTextures != null &&
                    index < OriginalTextures.Length)
                {
                    image.texture = OriginalTextures[index];
                }
                if (OriginalImageMaterials != null &&
                    index < OriginalImageMaterials.Length)
                {
                    image.material = OriginalImageMaterials[index];
                }
                if (OriginalImageEnabled != null &&
                    index < OriginalImageEnabled.Length)
                {
                    image.enabled = OriginalImageEnabled[index];
                }
                if (OriginalImageColors != null &&
                    index < OriginalImageColors.Length)
                {
                    image.color = OriginalImageColors[index];
                }
                if (OriginalImageActive != null &&
                    index < OriginalImageActive.Length)
                {
                    image.gameObject.SetActive(
                        OriginalImageActive[index]);
                }
            }
        }
    }

    internal static class SkinLoaderCoverage
    {
        private static readonly HashSet<string> LoaderNames =
            new HashSet<string>(StringComparer.Ordinal)
            {
                "LoadDailyWeeklyImageAssetAsync",
                "LoadChestRewardAsync",
                "LoadCollectionDetailsAssetAsync",
                "LoadSkinEditSkinAsync",
                "LoadCollectionListAssetAsync",
                "LoadGameplayAssetAsync",
                "LoadMarketplaceDetailsAssetAsync",
                "LoadMarketplaceListAssetAsync",
                "LoadPortrait",
                "LoadPortraitSpriteAsync",
                "LoadAnimatedPortraitAsync",
                "LoadCollectibleInspectionAssetAsync",
                "LoadStoreImageAsync",
                "GenerateEncounterData"
            };

        public static void Install(Harmony harmony)
        {
            Type skinType = SkinPatchTargets.SkinType();
            MethodInfo genericPostfix = AccessTools.Method(
                typeof(SkinLoaderCoverage),
                "Postfix");
            if (skinType == null || genericPostfix == null)
            {
                RuntimeDiagnostics.ReportLoaderState(
                    "SkinAssetDataSO",
                    "unsupported type",
                    "Loader coverage binding is unavailable.");
                return;
            }

            foreach (MethodInfo method in
                AccessTools.GetDeclaredMethods(skinType))
            {
                if (!LoaderNames.Contains(method.Name) ||
                    !method.ReturnType.IsGenericType ||
                    method.ReturnType.GetGenericTypeDefinition() !=
                        typeof(Task<>))
                {
                    continue;
                }

                Type resultType =
                    method.ReturnType.GetGenericArguments()[0];
                MethodInfo closedPostfix =
                    genericPostfix.MakeGenericMethod(resultType);
                var patch = new HarmonyMethod(closedPostfix)
                {
                    priority = Priority.Last
                };
                harmony.Patch(method, postfix: patch);
            }
        }

        public static void RemoveAll()
        {
            foreach (SkinLoaderAppliedMarker marker in
                Resources.FindObjectsOfTypeAll<SkinLoaderAppliedMarker>())
            {
                if (marker != null)
                {
                    marker.Restore();
                    UnityEngine.Object.Destroy(marker);
                }
            }
        }

        public static bool RunRestorationSelfTest(out string detail)
        {
            GameObject root = null;
            Texture2D originalTexture = null;
            Texture2D replacementTexture = null;
            Material instancedMaterial = null;
            Material originalImageMaterial = null;
            Material defaultImageMaterial = null;
            try
            {
                root = new GameObject(
                    "BazaarSkinManagerChestRestoreSelfTest");
                SpriteRenderer renderer =
                    root.AddComponent<SpriteRenderer>();
                ChestRestoreSelfTestController controller =
                    root.AddComponent<ChestRestoreSelfTestController>();
                Material originalMaterial = renderer.sharedMaterial;
                if (originalMaterial == null)
                {
                    detail =
                        "SpriteRenderer did not expose an original material.";
                    return false;
                }

                instancedMaterial =
                    new Material(originalMaterial);
                controller.Initialize(instancedMaterial);
                renderer.sharedMaterial = instancedMaterial;
                renderer.enabled = false;

                var imageObject = new GameObject(
                    "PreservedRawImage",
                    typeof(RectTransform),
                    typeof(CanvasRenderer),
                    typeof(RawImage));
                imageObject.transform.SetParent(root.transform, false);
                RawImage image = imageObject.GetComponent<RawImage>();
                originalTexture = new Texture2D(1, 1);
                replacementTexture = new Texture2D(1, 1);
                instancedMaterial.mainTexture = originalTexture;
                originalImageMaterial =
                    new Material(originalMaterial);
                defaultImageMaterial =
                    new Material(originalMaterial);
                Color originalColor =
                    new Color(0.25f, 0.5f, 0.75f, 0.4f);
                image.texture = originalTexture;
                image.material = originalImageMaterial;
                image.enabled = false;
                image.color = originalColor;
                imageObject.SetActive(false);

                SkinLoaderAppliedMarker marker =
                    root.AddComponent<SkinLoaderAppliedMarker>();
                marker.Controller = controller;
                marker.AssetRenderer = renderer;
                marker.OriginalSharedMaterial = instancedMaterial;
                marker.OriginalSharedMaterialMainTexture =
                    instancedMaterial.mainTexture;
                marker.OriginalInstancedMaterial =
                    instancedMaterial;
                marker.OriginalInstancedMaterialMainTexture =
                    instancedMaterial.mainTexture;
                marker.OriginalRendererEnabled = false;
                marker.ReplacementInstancedMaterial =
                    instancedMaterial;
                marker.PreservedImages = new[] { image };
                marker.OriginalImageMaterials =
                    new[] { originalImageMaterial };
                marker.OriginalTextures =
                    new Texture[] { originalTexture };
                marker.OriginalImageEnabled = new[] { false };
                marker.OriginalImageColors =
                    new[] { originalColor };
                marker.OriginalImageActive = new[] { false };

                // Model ChestRewardController.UpdateMaterial: it reuses the
                // existing private instancedMaterial, changes mainTexture in
                // place, and reassigns that same reference to the renderer.
                controller.UpdateMaterial(
                    replacementTexture,
                    renderer);
                renderer.enabled = true;

                // Model the ImageLazyLoader alternate/default-material branch.
                image.material = defaultImageMaterial;
                image.texture = replacementTexture;
                image.enabled = true;
                image.color = Color.white;
                imageObject.SetActive(true);

                marker.Restore();
                bool restored =
                    object.ReferenceEquals(
                        renderer.sharedMaterial,
                        instancedMaterial) &&
                    object.ReferenceEquals(
                        controller.CurrentMaterial,
                        instancedMaterial) &&
                    object.ReferenceEquals(
                        instancedMaterial.mainTexture,
                        originalTexture) &&
                    !renderer.enabled &&
                    object.ReferenceEquals(
                        image.material,
                        originalImageMaterial) &&
                    object.ReferenceEquals(
                        image.texture,
                        originalTexture) &&
                    !image.enabled &&
                    image.color == originalColor &&
                    !imageObject.activeSelf;
                detail = restored
                    ? "Synthetic chest marker restored in-place instancedMaterial.mainTexture, renderer references/state, RawImage.material/texture, enabled, color, and active state."
                    : "Synthetic chest marker did not restore the in-place renderer material mutation and ImageLazyLoader default-material branch.";
                return restored;
            }
            catch (Exception exception)
            {
                detail = exception.GetType().FullName +
                    ": " + exception.Message;
                return false;
            }
            finally
            {
                if (root != null)
                {
                    UnityEngine.Object.Destroy(root);
                }
                if (originalTexture != null)
                {
                    UnityEngine.Object.Destroy(originalTexture);
                }
                if (replacementTexture != null)
                {
                    UnityEngine.Object.Destroy(replacementTexture);
                }
                if (instancedMaterial != null)
                {
                    UnityEngine.Object.Destroy(instancedMaterial);
                }
                if (originalImageMaterial != null)
                {
                    UnityEngine.Object.Destroy(originalImageMaterial);
                }
                if (defaultImageMaterial != null)
                {
                    UnityEngine.Object.Destroy(defaultImageMaterial);
                }
            }
        }

        private static void Postfix<T>(
            object __instance,
            MethodBase __originalMethod,
            object[] __args,
            ref Task<T> __result)
        {
            string loader = __originalMethod == null
                ? "<unknown>"
                : __originalMethod.Name;
            if (!SkinPatchTargets.ShouldReplace(__instance))
            {
                if (RuntimeSkinAudit.Enabled)
                {
                    RuntimeSkinAudit.RecordLoader(
                        loader,
                        "wrong hero/skin",
                        "Loader call did not target Mak default Skin_MAK_01a.",
                        null);
                }
                return;
            }

            if (__result == null)
            {
                string status = loader == "LoadGameplayAssetAsync"
                    ? "unsupported type"
                    : "not loaded";
                RuntimeSkinAudit.RecordLoader(
                    loader,
                    status,
                    loader == "LoadGameplayAssetAsync"
                        ? "The game implementation explicitly returns null for direct gameplay loading."
                        : "Loader returned a null task.",
                    null);
                return;
            }

            __result = Process(loader, __args, __result);
        }

        private static async Task<T> Process<T>(
            string loader,
            object[] arguments,
            Task<T> original)
        {
            T result;
            try
            {
                result = await original;
            }
            catch (Exception exception)
            {
                RuntimeSkinAudit.RecordLoader(
                    loader,
                    "not loaded",
                    "Loader task failed: " +
                    exception.GetType().FullName + ": " +
                    exception.Message,
                    null);
                throw;
            }

            object boxed = result;
            string status;
            string detail;
            ApplySafeCoverage(
                loader,
                arguments,
                boxed,
                out status,
                out detail);
            RuntimeSkinAudit.RecordLoader(
                loader,
                status,
                detail,
                boxed);
            return ValueTypeResultMutation.Rebox<T>(boxed);
        }

        private static void ApplySafeCoverage(
            string loader,
            object[] arguments,
            object result,
            out string status,
            out string detail)
        {
            if (loader == "LoadGameplayAssetAsync")
            {
                status = "unsupported type";
                detail =
                    "The game implementation does not support direct skin gameplay loading.";
                return;
            }
            if (loader == "LoadSkinEditSkinAsync")
            {
                GameObject root = result as GameObject;
                if (root == null)
                {
                    status = result == null
                        ? "not loaded"
                        : "unsupported type";
                    detail =
                        "Expected the original skin-edit presentation GameObject.";
                }
                else
                {
                    string placementName =
                        arguments != null && arguments.Length > 0 &&
                        arguments[0] != null
                            ? arguments[0].ToString()
                            : string.Empty;
                    Transform placement = string.IsNullOrEmpty(placementName)
                        ? null
                        : root.transform.Find(placementName);
                    status = placement == null
                        ? "not loaded"
                        : "applied";
                    detail = placement == null
                        ? "The requested SkinEdit placement was not present."
                        : "The exact requested SkinEdit placement was resolved; " +
                            "its overlay is attached after parenting and activation.";
                }
                return;
            }
            if (result == null)
            {
                status = "not loaded";
                detail = "Loader completed with a null result.";
                return;
            }

            switch (loader)
            {
                case "LoadChestRewardAsync":
                    ApplyChestReward(result, out status, out detail);
                    return;
                case "LoadAnimatedPortraitAsync":
                    ApplyGameObjectOverlay(
                        result,
                        "standing_overlay",
                        loader,
                        out status,
                        out detail);
                    return;
                case "LoadCollectibleInspectionAssetAsync":
                    ApplyCollectibleInspection(
                        result,
                        out status,
                        out detail);
                    return;
                case "GenerateEncounterData":
                    ApplyEncounterData(result, out status, out detail);
                    return;
                case "LoadCollectionDetailsAssetAsync":
                    GameObject details = result as GameObject;
                    if (details == null)
                    {
                        status = "unsupported type";
                        detail =
                            "Expected the original collection-details GameObject.";
                    }
                    else if (StandingOverlay.HasWorldOverlay(details))
                    {
                        status = "already applied";
                        detail =
                            "Original GameObject retained with reversible collection overlay.";
                    }
                    else
                    {
                        Sprite detailsSprite =
                            Plugin.ActivePack.Sprite("collection_details");
                        StandingOverlay.AttachToWorld(
                            details,
                            detailsSprite,
                            "collection_details",
                            loader);
                        status = StandingOverlay.HasWorldOverlay(details)
                            ? "applied"
                            : "not loaded";
                        detail = StandingOverlay.HasWorldOverlay(details)
                            ? "Original GameObject retained and reversible overlay attached."
                            : "Collection overlay sprite was unavailable.";
                    }
                    return;
            }

            string expectedSlot = ExpectedSlot(loader, arguments);
            UnityEngine.Object unityResult = result as UnityEngine.Object;
            if (expectedSlot == null)
            {
                status = "unsupported type";
                detail =
                    "No safe default-skin coverage rule exists for this result.";
                return;
            }
            if (unityResult == null)
            {
                status = "unsupported type";
                detail =
                    "Expected a Unity texture or sprite result for slot " +
                    expectedSlot + ".";
                return;
            }

            string expectedName = "BazaarSkinManager/" + expectedSlot;
            if (Plugin.ActivePack.UsesPreloadedDeployment(expectedSlot))
            {
                status = "applied";
                detail = expectedSlot +
                    " resolves from the deploy-time patched Unity bundle.";
                return;
            }
            if (string.Equals(
                unityResult.name,
                expectedName,
                StringComparison.Ordinal))
            {
                status = "applied";
                detail = expectedSlot + " resolved through the exact loader.";
            }
            else
            {
                status = "not loaded";
                detail = "Exact loader returned " + unityResult.name +
                    " instead of " + expectedName + ".";
            }
        }

        private static string ExpectedSlot(
            string loader,
            object[] arguments)
        {
            switch (loader)
            {
                case "LoadDailyWeeklyImageAssetAsync":
                    return "daily_weekly";
                case "LoadCollectionListAssetAsync":
                    return "collection_list";
                case "LoadMarketplaceDetailsAssetAsync":
                    return "marketplace_details";
                case "LoadMarketplaceListAssetAsync":
                    return "marketplace_list";
                case "LoadPortraitSpriteAsync":
                    return "portrait_gameplay";
                case "LoadStoreImageAsync":
                    return "store_image";
                case "LoadPortrait":
                    return arguments != null &&
                        arguments.Length > 0 &&
                        arguments[0] is bool &&
                        (bool)arguments[0]
                        ? "portrait_small"
                        : "portrait_gameplay";
                default:
                    return null;
            }
        }

        private static void ApplyChestReward(
            object result,
            out string status,
            out string detail)
        {
            if (Plugin.ActivePack.UsesPreloadedDeployment("collection_list"))
            {
                status = "applied";
                detail =
                    "Chest reward retains its original loader and resolves " +
                    "the preloaded Mak texture from the patched Unity bundle.";
                return;
            }

            GameObject root = result as GameObject;
            Type controllerType =
                AccessTools.TypeByName("TheBazaar.ChestRewardController");
            Component controller = root == null || controllerType == null
                ? null
                : root.GetComponent(controllerType);
            MethodInfo updateMaterial = controllerType == null
                ? null
                : AccessTools.Method(
                    controllerType,
                    "UpdateMaterial",
                    new[] { typeof(Texture2D) });
            Texture2D texture =
                Plugin.ActivePack.Texture("collection_list");
            if (root == null || controller == null ||
                updateMaterial == null || texture == null)
            {
                status = "unsupported type";
                detail =
                    "Chest reward GameObject/controller/material hook is unavailable.";
                return;
            }

            SkinLoaderAppliedMarker marker =
                root.GetComponent<SkinLoaderAppliedMarker>();
            if (marker != null &&
                marker.Loader == "LoadChestRewardAsync")
            {
                status = "already applied";
                detail =
                    "Original chest holder retained with replacement material.";
                return;
            }

            FieldInfo rendererField =
                AccessTools.Field(controllerType, "assetRenderer");
            FieldInfo imageLoaderField =
                AccessTools.Field(controllerType, "imageLoader");
            FieldInfo instancedMaterialField =
                AccessTools.Field(controllerType, "instancedMaterial");
            Renderer assetRenderer = rendererField == null
                ? null
                : rendererField.GetValue(controller) as Renderer;
            object imageLoader = imageLoaderField == null
                ? null
                : imageLoaderField.GetValue(controller);
            RawImage[] preservedImages =
                CaptureImageLoaderImages(imageLoader);

            marker = root.AddComponent<SkinLoaderAppliedMarker>();
            marker.Loader = "LoadChestRewardAsync";
            marker.Slot = "collection_list";
            marker.Controller = controller;
            marker.AssetRenderer = assetRenderer;
            marker.OriginalSharedMaterial = assetRenderer == null
                ? null
                : assetRenderer.sharedMaterial;
            marker.OriginalSharedMaterialMainTexture =
                marker.OriginalSharedMaterial == null
                    ? null
                    : marker.OriginalSharedMaterial.mainTexture;
            marker.OriginalRendererEnabled = assetRenderer != null &&
                assetRenderer.enabled;
            marker.OriginalInstancedMaterial =
                instancedMaterialField == null
                    ? null
                    : instancedMaterialField.GetValue(controller) as Material;
            marker.OriginalInstancedMaterialMainTexture =
                marker.OriginalInstancedMaterial == null
                    ? null
                    : marker.OriginalInstancedMaterial.mainTexture;
            marker.PreservedImages = preservedImages;
            marker.OriginalImageMaterials = preservedImages
                .Select(image => image == null ? null : image.material)
                .ToArray();
            marker.OriginalTextures = preservedImages
                .Select(image => image == null ? null : image.texture)
                .ToArray();
            marker.OriginalImageEnabled = preservedImages
                .Select(image => image != null && image.enabled)
                .ToArray();
            marker.OriginalImageColors = preservedImages
                .Select(image => image == null ? Color.white : image.color)
                .ToArray();
            marker.OriginalImageActive = preservedImages
                .Select(image => image != null && image.gameObject.activeSelf)
                .ToArray();

            updateMaterial.Invoke(controller, new object[] { texture });
            marker.ReplacementInstancedMaterial =
                instancedMaterialField == null
                    ? null
                    : instancedMaterialField.GetValue(controller) as Material;
            RuntimeDiagnostics.ReportReplacement(
                "collection_list",
                "LoadChestRewardAsync -> ChestRewardController.UpdateMaterial");
            status = "applied";
            detail =
                "Original chest holder retained; only its skin material texture changed.";
        }

        private static RawImage[] CaptureImageLoaderImages(
            object imageLoader)
        {
            if (imageLoader == null)
            {
                return new RawImage[0];
            }

            var images = new List<RawImage>();
            foreach (string fieldName in new[]
            {
                "Image",
                "PlaceHolderImage",
                "SpinnerImage"
            })
            {
                FieldInfo field = AccessTools.Field(
                    imageLoader.GetType(),
                    fieldName);
                RawImage image = field == null
                    ? null
                    : field.GetValue(imageLoader) as RawImage;
                if (image != null && !images.Contains(image))
                {
                    images.Add(image);
                }
            }
            return images.ToArray();
        }

        private static void ApplyGameObjectOverlay(
            object result,
            string slot,
            string loader,
            out string status,
            out string detail)
        {
            GameObject root = result as GameObject;
            Sprite sprite = Plugin.ActivePack.Sprite(slot);
            if (root == null || sprite == null)
            {
                status = "unsupported type";
                detail =
                    "Expected original GameObject and available overlay sprite.";
                return;
            }
            if (StandingOverlay.HasWorldOverlay(root))
            {
                status = "already applied";
                detail =
                    "Original GameObject already has the reversible overlay.";
                return;
            }

            StandingOverlay.AttachToWorld(root, sprite, slot, loader);
            status = StandingOverlay.HasWorldOverlay(root)
                ? "applied"
                : "not loaded";
            detail = StandingOverlay.HasWorldOverlay(root)
                ? "Original GameObject retained and reversible overlay attached."
                : "Overlay was not attached.";
        }

        private static void ApplyCollectibleInspection(
            object result,
            out string status,
            out string detail)
        {
            FieldInfo instanceField = AccessTools.Field(
                result.GetType(),
                "LoadedCollectibleInstance");
            FieldInfo backgroundField = AccessTools.Field(
                result.GetType(),
                "HeroSkinBackgroundImage");
            GameObject root = instanceField == null
                ? null
                : instanceField.GetValue(result) as GameObject;
            bool preloadedBackground =
                Plugin.ActivePack.UsesPreloadedDeployment("store_image");
            Texture2D background = preloadedBackground
                ? null
                : Plugin.ActivePack.Texture("store_image");
            if (root == null || backgroundField == null ||
                (!preloadedBackground && background == null))
            {
                status = "unsupported type";
                detail =
                    "CollectibleInspectorData instance/background fields are unavailable.";
                return;
            }

            bool overlayPresent = StandingOverlay.HasWorldOverlay(root);
            bool backgroundPresent =
                preloadedBackground ||
                object.ReferenceEquals(backgroundField.GetValue(result), background);
            if (overlayPresent && backgroundPresent)
            {
                status = "already applied";
                detail =
                    "Inspector data already contains the replacement background and overlay.";
                return;
            }

            if (!preloadedBackground)
            {
                backgroundField.SetValue(result, background);
            }
            if (!overlayPresent)
            {
                StandingOverlay.AttachToWorld(
                    root,
                    Plugin.ActivePack.Sprite("collection_details"),
                    "collection_details",
                    "LoadCollectibleInspectionAssetAsync");
            }
            if (!preloadedBackground)
            {
                RuntimeDiagnostics.ReportReplacement(
                    "store_image",
                    "LoadCollectibleInspectionAssetAsync -> HeroSkinBackgroundImage");
            }
            status = "applied";
            detail =
                "Inspector data type retained; background and instance overlay updated.";
        }

        private static void ApplyEncounterData(
            object result,
            out string status,
            out string detail)
        {
            FieldInfo portraitField = AccessTools.Field(
                result.GetType(),
                "portraitTextureReference");
            FieldInfo backgroundField = AccessTools.Field(
                result.GetType(),
                "backgroundTextureReference");
            bool preloadedPortrait =
                Plugin.ActivePack.UsesPreloadedDeployment(
                    "portrait_gameplay");
            bool preloadedBackground =
                Plugin.ActivePack.UsesPreloadedDeployment("store_image");
            Sprite portrait = preloadedPortrait
                ? null
                : Plugin.ActivePack.Sprite("portrait_gameplay");
            Texture2D background = preloadedBackground
                ? null
                : Plugin.ActivePack.Texture("store_image");
            if (portraitField == null || backgroundField == null ||
                (!preloadedPortrait && portrait == null) ||
                (!preloadedBackground && background == null))
            {
                status = "unsupported type";
                detail =
                    "Encounter data portrait/background fields are unavailable.";
                return;
            }

            bool already =
                (preloadedPortrait ||
                 object.ReferenceEquals(
                     portraitField.GetValue(result),
                     portrait)) &&
                (preloadedBackground ||
                 object.ReferenceEquals(
                     backgroundField.GetValue(result),
                     background));
            if (already)
            {
                status = "applied";
                detail =
                    "Encounter data retains its native references; preloaded " +
                    "textures resolve from the patched Unity bundle.";
                return;
            }

            if (!preloadedPortrait)
            {
                portraitField.SetValue(result, portrait);
                RuntimeDiagnostics.ReportReplacement(
                    "portrait_gameplay",
                    "GenerateEncounterData -> portraitTextureReference");
            }
            if (!preloadedBackground)
            {
                backgroundField.SetValue(result, background);
                RuntimeDiagnostics.ReportReplacement(
                    "store_image",
                    "GenerateEncounterData -> backgroundTextureReference");
            }
            status = "applied";
            detail =
                "EncounterAssetDataSO and Cleanup retained; visual fields updated.";
        }
    }
}
