using System;
using System.Linq;
using UnityEngine;
using UnityEngine.UI;

namespace BazaarSkinManager.TheBazaar
{
    internal sealed class UiStandingOverlayMarker : MonoBehaviour
    {
        public Graphic OriginalGraphic;
        public bool OriginalEnabled;
        public Color OriginalColor;

        private void OnDestroy()
        {
            if (OriginalGraphic != null)
            {
                OriginalGraphic.enabled = OriginalEnabled;
                OriginalGraphic.color = OriginalColor;
            }
        }
    }

    internal sealed class WorldStandingOverlayMarker : MonoBehaviour
    {
        public Renderer[] PreservedRenderers;
        public bool[] OriginalEnabled;

        private void OnDestroy()
        {
            Restore();
        }

        public void Restore()
        {
            if (PreservedRenderers == null)
            {
                return;
            }

            for (int index = 0; index < PreservedRenderers.Length; index++)
            {
                Renderer renderer = PreservedRenderers[index];
                if (renderer != null)
                {
                    renderer.enabled = OriginalEnabled != null &&
                        index < OriginalEnabled.Length
                        ? OriginalEnabled[index]
                        : true;
                }
            }
        }
    }

    internal sealed class SkinEditVisibleOverlayAttacher : MonoBehaviour
    {
        private string _placementName;
        private Sprite _sprite;
        private RuntimePack _pack;
        private int _remainingFrames;

        public void Configure(
            string placementName,
            Sprite sprite,
            RuntimePack pack = null)
        {
            _placementName = placementName;
            _sprite = sprite;
            _pack = pack;
            _remainingFrames = 120;
            enabled = true;
        }

        private void LateUpdate()
        {
            if (string.IsNullOrEmpty(_placementName) || _sprite == null)
            {
                enabled = false;
                return;
            }
            if (StandingOverlay.HasWorldOverlay(gameObject))
            {
                enabled = false;
                return;
            }

            Transform placement = transform.Find(_placementName);
            Renderer renderer = placement == null
                ? null
                : placement
                    .GetComponentsInChildren<Renderer>(true)
                    .Where(candidate => candidate != null &&
                        candidate.enabled &&
                        candidate.gameObject.activeInHierarchy)
                    .OrderBy(candidate =>
                        Math.Max(
                            candidate.bounds.size.y,
                            candidate.bounds.size.z))
                    .LastOrDefault();
            if (renderer != null &&
                Math.Max(renderer.bounds.size.y, renderer.bounds.size.z) >
                    0.001f)
            {
                StandingOverlay.AttachToWorld(
                    renderer.gameObject,
                    _sprite,
                    "standing_overlay",
                    "SkinEdit visible-frame exact " + _placementName,
                    _pack);
                Plugin.Log.LogInfo(
                    "Attached visible-frame SkinEdit placement " +
                    _placementName + ": rendererBounds=" +
                    renderer.bounds.size + " layer=" +
                    renderer.gameObject.layer + ".");
                enabled = false;
                return;
            }

            CountDown();
        }

        private void CountDown()
        {
            _remainingFrames--;
            if (_remainingFrames > 0)
            {
                return;
            }
            Plugin.Log.LogWarning(
                "Timed out waiting for visible local SkinEdit placement " +
                _placementName + " on " + gameObject.name + ".");
            enabled = false;
        }
    }

    internal static class StandingOverlay
    {
        private const string UiObjectName = "BazaarSkinManagerStandingOverlayUI";
        private const string WorldObjectName = "BazaarSkinManagerStandingOverlayWorld";

        public static void AttachToGraphic(
            Graphic graphic,
            Sprite sprite,
            string source,
            RuntimePack pack = null)
        {
            if (graphic == null || sprite == null)
            {
                return;
            }

            Transform existing = graphic.transform.Find(UiObjectName);
            if (existing != null)
            {
                Image existingImage = existing.GetComponent<Image>();
                if (existingImage != null)
                {
                    existingImage.sprite = sprite;
                }
                existing.gameObject.SetActive(true);
                graphic.enabled = false;
                return;
            }

            var overlay = new GameObject(
                UiObjectName,
                typeof(RectTransform),
                typeof(CanvasRenderer),
                typeof(Image),
                typeof(UiStandingOverlayMarker));
            RectTransform rect = overlay.GetComponent<RectTransform>();
            rect.SetParent(graphic.rectTransform, false);
            rect.anchorMin = Vector2.zero;
            rect.anchorMax = Vector2.one;
            rect.offsetMin = Vector2.zero;
            rect.offsetMax = Vector2.zero;
            rect.localScale = Vector3.one;
            rect.SetAsLastSibling();

            Image image = overlay.GetComponent<Image>();
            image.sprite = sprite;
            image.preserveAspect = true;
            image.raycastTarget = false;

            UiStandingOverlayMarker marker =
                overlay.GetComponent<UiStandingOverlayMarker>();
            marker.OriginalGraphic = graphic;
            marker.OriginalEnabled = graphic.enabled;
            marker.OriginalColor = graphic.color;
            graphic.enabled = false;

            RuntimeDiagnostics.ReportReplacement(
                "standing_overlay",
                source + " -> " + HierarchyName(graphic.transform));
        }

        public static void RemoveFromGraphic(Graphic graphic)
        {
            if (graphic == null)
            {
                return;
            }

            Transform existing = graphic.transform.Find(UiObjectName);
            if (existing == null)
            {
                return;
            }

            UiStandingOverlayMarker marker =
                existing.GetComponent<UiStandingOverlayMarker>();
            if (marker != null && marker.OriginalGraphic != null)
            {
                marker.OriginalGraphic.enabled = marker.OriginalEnabled;
                marker.OriginalGraphic.color = marker.OriginalColor;
            }
            UnityEngine.Object.Destroy(existing.gameObject);
        }

        public static bool HasGraphicOverlay(Graphic graphic)
        {
            return graphic != null &&
                graphic.transform.Find(UiObjectName) != null;
        }

        public static bool TryGetGraphicOriginalState(
            Graphic graphic,
            out bool enabled,
            out Color color)
        {
            enabled = graphic != null && graphic.enabled;
            color = graphic == null ? Color.white : graphic.color;
            if (graphic == null)
            {
                return false;
            }

            Transform existing = graphic.transform.Find(UiObjectName);
            UiStandingOverlayMarker marker = existing == null
                ? null
                : existing.GetComponent<UiStandingOverlayMarker>();
            if (marker == null)
            {
                return false;
            }

            enabled = marker.OriginalEnabled;
            color = marker.OriginalColor;
            return true;
        }

        public static void AttachToWorld(
            GameObject root,
            Sprite sprite,
            string slot,
            string source,
            RuntimePack pack = null)
        {
            if (root == null || sprite == null ||
                root.transform.Find(WorldObjectName) != null)
            {
                return;
            }

            Renderer[] renderers = root.GetComponentsInChildren<Renderer>(true);
            Renderer[] visibleRenderers = renderers
                .Where(renderer => renderer != null)
                .ToArray();

            Bounds bounds = new Bounds(root.transform.position, Vector3.zero);
            bool hasBounds = false;
            foreach (Renderer renderer in visibleRenderers)
            {
                if (!hasBounds)
                {
                    bounds = renderer.bounds;
                    hasBounds = true;
                }
                else
                {
                    bounds.Encapsulate(renderer.bounds);
                }
            }

            var overlay = new GameObject(
                WorldObjectName,
                typeof(SpriteRenderer),
                typeof(WorldStandingOverlayMarker));
            overlay.layer = root.layer;
            Transform transform = overlay.transform;
            transform.SetParent(root.transform, false);

            SpriteRenderer spriteRenderer = overlay.GetComponent<SpriteRenderer>();
            spriteRenderer.sprite = sprite;
            RuntimePack resolvedPack = pack ?? Plugin.ActivePack;
            float scaleMultiplier = resolvedPack == null
                ? 1f
                : resolvedPack.ScaleMultiplier(slot);
            if (visibleRenderers.Length > 0)
            {
                Renderer top = visibleRenderers
                    .OrderBy(renderer => renderer.sortingOrder)
                    .Last();
                spriteRenderer.sortingLayerID = top.sortingLayerID;
                spriteRenderer.sortingOrder = top.sortingOrder + 1;
            }

            if (hasBounds && bounds.size.y > 0.001f)
            {
                transform.position = bounds.center;
                float inheritedScale = Math.Abs(root.transform.lossyScale.y);
                if (inheritedScale < 0.001f)
                {
                    inheritedScale = 1f;
                }
                float scale = bounds.size.y /
                    Math.Max(0.001f, sprite.bounds.size.y * inheritedScale);
                scale *= scaleMultiplier;
                transform.localScale = Vector3.one * scale;

                // Native Spine bounds differ substantially between heroes.
                // The optional pack multiplier keeps the existing bounds-fit
                // algorithm and changes only the authored composition scale.
                // Anchor the enlarged replacement to the native lower edge so
                // it grows upward instead of sinking into the menu controls.
                float replacementHeight =
                    sprite.bounds.size.y * inheritedScale * scale;
                transform.position += Vector3.up *
                    ((replacementHeight - bounds.size.y) * 0.5f);
            }
            else if (hasBounds && bounds.size.z > 0.001f)
            {
                // PvpScreen's Spine mesh has zero world-space Y extent, but
                // its final upright appearance is produced by the transition
                // camera projection rather than by a reusable XY transform.
                // Face the flat replacement directly at the camera that
                // renders this layer and preserve the native mesh's projected
                // screen height.
                float screenHeight;
                Camera renderCamera =
                    FindBestRenderCamera(bounds, root.layer, out screenHeight);
                if (renderCamera != null && screenHeight > 0.5f)
                {
                    // Keep this object parented to the exact Spine placement.
                    // The verified 0.4.5-0.4.7 implementation relies on that
                    // hierarchy for transition/vortex occlusion and cleanup.
                    // Detaching it makes the sprite render above the vortex
                    // and survive into the board as a screen-sized overlay.
                    transform.position = bounds.center;
                    transform.rotation = renderCamera.transform.rotation;
                    transform.localScale = Vector3.one;

                    // Measure the replacement after its real parent transform
                    // and billboard rotation have been applied. Dividing by a
                    // single root lossy-scale component made flat Spine roots
                    // several times too small because their scale is strongly
                    // anisotropic.
                    float replacementScreenHeight;
                    ProjectedHeight(
                        renderCamera,
                        spriteRenderer.bounds,
                        out replacementScreenHeight);
                    float targetScreenHeight = Math.Min(
                        screenHeight * scaleMultiplier,
                        renderCamera.pixelHeight * 0.82f);
                    float scale = targetScreenHeight /
                        Math.Max(0.5f, replacementScreenHeight);
                    bool mirroredParent =
                        root.transform.localToWorldMatrix.determinant < 0f;
                    transform.localScale = new Vector3(
                        mirroredParent ? -scale : scale,
                        scale,
                        scale);

                    // The native Spine pivot sits substantially above the
                    // geometric bounds center used by the flat replacement.
                    // Apply the measured PvP/new-day composition offset in
                    // screen space so it remains stable across resolutions.
                    Vector3 projectedCenter =
                        renderCamera.WorldToScreenPoint(transform.position);
                    Vector3 shiftedCenter = projectedCenter;
                    shiftedCenter.y += renderCamera.pixelHeight * 0.17f;
                    Vector3 worldOffset =
                        renderCamera.ScreenToWorldPoint(shiftedCenter) -
                        renderCamera.ScreenToWorldPoint(projectedCenter);
                    transform.position += worldOffset;
                    Plugin.Log.LogInfo(
                        "Billboarded XZ SkinEdit overlay through camera " +
                        renderCamera.name + ": nativeScreenHeight=" +
                        screenHeight.ToString("F2") + " targetScreenHeight=" +
                        targetScreenHeight.ToString("F2") +
                        " replacementUnitHeight=" +
                        replacementScreenHeight.ToString("F2") + " scale=" +
                        scale.ToString("F3") + " screenYOffset=" +
                        (renderCamera.pixelHeight * 0.17f).ToString("F2") +
                        " mirrorCompensated=" + mirroredParent + ".");
                }
                else
                {
                    transform.position = bounds.center;
                    transform.localScale = Vector3.one;
                    Plugin.Log.LogWarning(
                        "No active camera could project the XZ SkinEdit " +
                        "overlay on layer " + root.layer + ".");
                }
            }
            else
            {
                transform.localPosition = Vector3.zero;
                transform.localScale = Vector3.one;
            }

            WorldStandingOverlayMarker marker =
                overlay.GetComponent<WorldStandingOverlayMarker>();
            marker.PreservedRenderers = visibleRenderers;
            marker.OriginalEnabled = visibleRenderers
                .Select(renderer => renderer.enabled)
                .ToArray();
            foreach (Renderer renderer in visibleRenderers)
            {
                renderer.enabled = false;
            }

            RuntimeDiagnostics.ReportReplacement(
                slot,
                source + " -> " + HierarchyName(root.transform));
        }

        public static bool HasWorldOverlay(GameObject root)
        {
            return root != null &&
                root.GetComponentInChildren<WorldStandingOverlayMarker>(true) != null;
        }

        public static void RemoveFromWorld(GameObject root)
        {
            if (root == null)
            {
                return;
            }

            WorldStandingOverlayMarker marker =
                root.GetComponentInChildren<WorldStandingOverlayMarker>(true);
            if (marker == null)
            {
                return;
            }

            marker.Restore();
            UnityEngine.Object.Destroy(marker.gameObject);
        }

        public static void RemoveAll()
        {
            foreach (UiStandingOverlayMarker marker in
                Resources.FindObjectsOfTypeAll<UiStandingOverlayMarker>())
            {
                if (marker != null)
                {
                    RemoveFromGraphic(marker.OriginalGraphic);
                }
            }

            foreach (WorldStandingOverlayMarker marker in
                Resources.FindObjectsOfTypeAll<WorldStandingOverlayMarker>())
            {
                if (marker != null)
                {
                    marker.Restore();
                    UnityEngine.Object.Destroy(marker.gameObject);
                }
            }
        }

        private static string HierarchyName(Transform transform)
        {
            if (transform == null)
            {
                return "<null>";
            }

            string value = transform.name;
            Transform current = transform.parent;
            int remaining = 3;
            while (current != null && remaining-- > 0)
            {
                value = current.name + "/" + value;
                current = current.parent;
            }
            return value;
        }

        private static Camera FindBestRenderCamera(
            Bounds bounds,
            int layer,
            out float bestHeight)
        {
            Camera best = null;
            bestHeight = 0f;
            foreach (Camera camera in Camera.allCameras)
            {
                if (camera == null || !camera.enabled ||
                    (camera.cullingMask & (1 << layer)) == 0)
                {
                    continue;
                }

                float height;
                if (ProjectedHeight(camera, bounds, out height) &&
                    height > bestHeight)
                {
                    best = camera;
                    bestHeight = height;
                }
            }
            return best;
        }

        private static bool ProjectedHeight(
            Camera camera,
            Bounds bounds,
            out float height)
        {
            Vector3 min = bounds.min;
            Vector3 max = bounds.max;
            Vector3[] corners =
            {
                new Vector3(min.x, min.y, min.z),
                new Vector3(min.x, min.y, max.z),
                new Vector3(min.x, max.y, min.z),
                new Vector3(min.x, max.y, max.z),
                new Vector3(max.x, min.y, min.z),
                new Vector3(max.x, min.y, max.z),
                new Vector3(max.x, max.y, min.z),
                new Vector3(max.x, max.y, max.z)
            };
            float minY = float.PositiveInfinity;
            float maxY = float.NegativeInfinity;
            int visibleCorners = 0;
            foreach (Vector3 corner in corners)
            {
                Vector3 screen = camera.WorldToScreenPoint(corner);
                if (screen.z <= 0f)
                {
                    continue;
                }
                visibleCorners++;
                minY = Math.Min(minY, screen.y);
                maxY = Math.Max(maxY, screen.y);
            }
            height = visibleCorners == 0 ? 0f : maxY - minY;
            return visibleCorners > 0 && height > 0f;
        }
    }
}
