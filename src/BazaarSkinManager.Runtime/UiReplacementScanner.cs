using UnityEngine;
using UnityEngine.UI;

namespace BazaarSkinManager.TheBazaar
{
    internal sealed class UiReplacementScanner : MonoBehaviour
    {
        public float RescanSeconds = 2.0f;

        private float _nextScan;

        private void Update()
        {
            if (Time.unscaledTime < _nextScan || Plugin.ActivePack == null)
            {
                return;
            }

            _nextScan = Time.unscaledTime + RescanSeconds;
            int replaced = ReplaceImages() + ReplaceRawImages() + ReplaceSpriteRenderers();
            if (replaced > 0)
            {
                Plugin.Log.LogDebug("Replaced " + replaced + " newly loaded visual(s).");
            }
        }

        private static int ReplaceImages()
        {
            int count = 0;
            foreach (Image image in Resources.FindObjectsOfTypeAll<Image>())
            {
                if (image == null || image.sprite == null ||
                    image.sprite.name.StartsWith("BazaarSkinManager/"))
                {
                    continue;
                }

                VisualReplacement replacement = Plugin.ActivePack.Match(image.sprite.name);
                if (replacement == null && image.sprite.texture != null)
                {
                    replacement = Plugin.ActivePack.Match(image.sprite.texture.name);
                }
                if (replacement != null)
                {
                    Sprite sprite = Plugin.ActivePack.Sprite(replacement.Slot);
                    if (sprite != null)
                    {
                        RuntimeDiagnostics.ReportReplacement(
                            replacement.Slot,
                            "UI Image " + image.sprite.name);
                        image.sprite = sprite;
                        image.preserveAspect = true;
                        count++;
                    }
                }
                else if (image.enabled && image.gameObject.activeInHierarchy)
                {
                    RuntimeDiagnostics.ReportUnmatchedMakVisual(
                        "Image",
                        HierarchyName(image.transform),
                        image.sprite.name);
                }
            }

            return count;
        }

        private static int ReplaceRawImages()
        {
            int count = 0;
            foreach (RawImage image in Resources.FindObjectsOfTypeAll<RawImage>())
            {
                if (image == null || image.texture == null ||
                    image.texture.name.StartsWith("BazaarSkinManager/"))
                {
                    continue;
                }

                VisualReplacement replacement = Plugin.ActivePack.Match(image.texture.name);
                if (replacement != null)
                {
                    Texture2D texture = Plugin.ActivePack.Texture(replacement.Slot);
                    if (texture != null)
                    {
                        RuntimeDiagnostics.ReportReplacement(
                            replacement.Slot,
                            "UI RawImage " + image.texture.name);
                        image.texture = texture;
                        count++;
                    }
                }
                else if (image.enabled && image.gameObject.activeInHierarchy)
                {
                    RuntimeDiagnostics.ReportUnmatchedMakVisual(
                        "RawImage",
                        HierarchyName(image.transform),
                        image.texture.name);
                }
            }

            return count;
        }

        private static int ReplaceSpriteRenderers()
        {
            int count = 0;
            foreach (SpriteRenderer renderer in Resources.FindObjectsOfTypeAll<SpriteRenderer>())
            {
                if (renderer == null || renderer.sprite == null ||
                    renderer.sprite.name.StartsWith("BazaarSkinManager/"))
                {
                    continue;
                }

                VisualReplacement replacement = Plugin.ActivePack.Match(renderer.sprite.name);
                if (replacement == null && renderer.sprite.texture != null)
                {
                    replacement =
                        Plugin.ActivePack.Match(renderer.sprite.texture.name);
                }
                if (replacement != null)
                {
                    Sprite sprite = Plugin.ActivePack.Sprite(replacement.Slot);
                    if (sprite != null)
                    {
                        RuntimeDiagnostics.ReportReplacement(
                            replacement.Slot,
                            "SpriteRenderer " + renderer.sprite.name);
                        renderer.sprite = sprite;
                        count++;
                    }
                }
                else if (renderer.enabled &&
                    renderer.gameObject.activeInHierarchy)
                {
                    RuntimeDiagnostics.ReportUnmatchedMakVisual(
                        "SpriteRenderer",
                        HierarchyName(renderer.transform),
                        renderer.sprite.name);
                }
            }

            return count;
        }

        private static string HierarchyName(Transform transform)
        {
            string value = transform == null ? "<null>" : transform.name;
            Transform current = transform == null ? null : transform.parent;
            int remaining = 4;
            while (current != null && remaining-- > 0)
            {
                value = current.name + "/" + value;
                current = current.parent;
            }
            return value;
        }
    }
}
