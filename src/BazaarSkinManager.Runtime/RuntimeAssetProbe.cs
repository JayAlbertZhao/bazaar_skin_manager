using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Threading.Tasks;
using HarmonyLib;
using UnityEngine;
using UnityEngine.UI;

namespace BazaarSkinManager.TheBazaar
{
    [DataContract]
    internal sealed class AssetProbeRequest
    {
        [DataMember(Name = "heroes")]
        public string[] Heroes;

        [DataMember(Name = "capture_native_gameplay_portrait")]
        public bool CaptureNativeGameplayPortrait;
    }

    [DataContract]
    internal sealed class NativeIconMeasurement
    {
        [DataMember(Name = "hero")]
        public string Hero;

        [DataMember(Name = "sprite_name")]
        public string SpriteName;

        [DataMember(Name = "texture_name")]
        public string TextureName;

        [DataMember(Name = "texture_size")]
        public int[] TextureSize;

        [DataMember(Name = "sprite_rect")]
        public float[] SpriteRect;

        [DataMember(Name = "texture_rect")]
        public float[] TextureRect;

        [DataMember(Name = "pivot")]
        public float[] Pivot;

        [DataMember(Name = "border")]
        public float[] Border;

        [DataMember(Name = "pixels_per_unit")]
        public float PixelsPerUnit;

        [DataMember(Name = "packed")]
        public bool Packed;

        [DataMember(Name = "packing_rotation")]
        public string PackingRotation;

        [DataMember(Name = "alpha_bounds")]
        public int[] AlphaBounds;

        [DataMember(Name = "icon_rect_size")]
        public float[] IconRectSize;

        [DataMember(Name = "icon_anchors")]
        public float[] IconAnchors;

        [DataMember(Name = "layers")]
        public string[] Layers;

        [DataMember(Name = "png")]
        public string Png;
    }

    [DataContract]
    internal sealed class NativeIconProbeReport
    {
        [DataMember(Name = "schema_version")]
        public int SchemaVersion = 1;

        [DataMember(Name = "source")]
        public string Source =
            "The Bazaar runtime HeroItemView/Content/Icon sprites";

        [DataMember(Name = "steam_build")]
        public string SteamBuild = "24570932";

        [DataMember(Name = "measurements")]
        public List<NativeIconMeasurement> Measurements =
            new List<NativeIconMeasurement>();
    }

    [DataContract]
    internal sealed class NativePortraitProbeReport
    {
        [DataMember(Name = "schema_version")]
        public int SchemaVersion = 1;

        [DataMember(Name = "source")]
        public string Source =
            "SkinAssetDataSO.LoadPortraitSpriteAsync original result";

        [DataMember(Name = "steam_build")]
        public string SteamBuild = "24570932";

        [DataMember(Name = "sprite_name")]
        public string SpriteName;

        [DataMember(Name = "texture_name")]
        public string TextureName;

        [DataMember(Name = "texture_size")]
        public int[] TextureSize;

        [DataMember(Name = "sprite_rect")]
        public float[] SpriteRect;

        [DataMember(Name = "texture_rect")]
        public float[] TextureRect;

        [DataMember(Name = "pivot")]
        public float[] Pivot;

        [DataMember(Name = "border")]
        public float[] Border;

        [DataMember(Name = "pixels_per_unit")]
        public float PixelsPerUnit;

        [DataMember(Name = "alpha_bounds")]
        public int[] AlphaBounds;

        [DataMember(Name = "png")]
        public string Png;
    }

    internal static class RuntimeAssetProbe
    {
        private static readonly string ProbeRoot = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "BazaarSkinManager",
            "TheBazaar",
            "probe");
        private static readonly string RequestPath =
            Path.Combine(ProbeRoot, "request.json");
        private static readonly string ResultRoot =
            Path.Combine(ProbeRoot, "results");

        private static AssetProbeRequest _request;
        private static bool _requestChecked;
        private static readonly NativeIconProbeReport Report =
            new NativeIconProbeReport();
        private static readonly HashSet<string> Captured =
            new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        private static bool _gameplayPortraitCaptured;

        public static bool Enabled
        {
            get
            {
                EnsureRequest();
                return _request != null;
            }
        }

        public static bool GameplayPortraitRequested
        {
            get
            {
                EnsureRequest();
                return _request != null &&
                    _request.CaptureNativeGameplayPortrait &&
                    !_gameplayPortraitCaptured;
            }
        }

        public static async Task<Sprite> CaptureGameplayPortraitAsync(
            Task<Sprite> originalTask,
            Sprite replacement)
        {
            try
            {
                Sprite original = originalTask == null
                    ? null
                    : await originalTask;
                if (GameplayPortraitRequested && original != null)
                {
                    CaptureGameplayPortrait(original);
                }
            }
            catch (Exception exception)
            {
                Plugin.Log.LogError(
                    "Native gameplay portrait probe failed: " + exception);
            }

            return replacement;
        }

        public static void CaptureHeroButton(
            string hero,
            Sprite sprite,
            Transform heroItemRoot,
            RectTransform iconRect)
        {
            if (!IsRequested(hero) || sprite == null ||
                !Captured.Add(hero))
            {
                return;
            }

            try
            {
                Directory.CreateDirectory(ResultRoot);
                Texture2D pixels = ReadSpritePixels(sprite);
                string fileName = SafeFileName(hero) + "-Content-Icon.png";
                string pngPath = Path.Combine(ResultRoot, fileName);
                File.WriteAllBytes(pngPath, pixels.EncodeToPNG());

                NativeIconMeasurement measurement = Measure(
                    hero,
                    sprite,
                    pixels,
                    heroItemRoot,
                    iconRect,
                    fileName);
                Report.Measurements.Add(measurement);
                WriteJson(
                    Path.Combine(ResultRoot, "native-hero-icons.json"),
                    Report,
                    typeof(NativeIconProbeReport));
                UnityEngine.Object.Destroy(pixels);

                Plugin.Log.LogInfo(
                    "Native icon probe " + hero + ": sprite=" +
                    measurement.SpriteName + " texture=" +
                    measurement.TextureSize[0] + "x" +
                    measurement.TextureSize[1] + " rect=" +
                    Join(measurement.SpriteRect) + " pivot=" +
                    Join(measurement.Pivot) + " border=" +
                    Join(measurement.Border) + " ppu=" +
                    measurement.PixelsPerUnit + " alpha=" +
                    Join(measurement.AlphaBounds) + ".");
            }
            catch (Exception exception)
            {
                Plugin.Log.LogError(
                    "Native icon probe failed for " + hero + ": " + exception);
            }
        }

        private static bool IsRequested(string hero)
        {
            EnsureRequest();

            return _request != null &&
                _request.Heroes != null &&
                _request.Heroes.Any(
                    value => string.Equals(
                        value,
                        hero,
                        StringComparison.OrdinalIgnoreCase));
        }

        private static void CaptureGameplayPortrait(Sprite sprite)
        {
            if (_gameplayPortraitCaptured)
            {
                return;
            }

            _gameplayPortraitCaptured = true;
            Texture2D pixels = null;
            try
            {
                Directory.CreateDirectory(ResultRoot);
                pixels = ReadSpritePixels(sprite);
                const string fileName = "Mak-Native-Gameplay-Portrait.png";
                File.WriteAllBytes(
                    Path.Combine(ResultRoot, fileName),
                    pixels.EncodeToPNG());

                Rect spriteRect = sprite.rect;
                Rect textureRect = sprite.textureRect;
                Vector2 pivot = sprite.pivot;
                Vector4 border = sprite.border;
                var report = new NativePortraitProbeReport
                {
                    SpriteName = sprite.name,
                    TextureName = sprite.texture == null
                        ? "<null>"
                        : sprite.texture.name,
                    TextureSize = sprite.texture == null
                        ? new[] { 0, 0 }
                        : new[] { sprite.texture.width, sprite.texture.height },
                    SpriteRect = new[]
                    {
                        spriteRect.x,
                        spriteRect.y,
                        spriteRect.width,
                        spriteRect.height
                    },
                    TextureRect = new[]
                    {
                        textureRect.x,
                        textureRect.y,
                        textureRect.width,
                        textureRect.height
                    },
                    Pivot = new[] { pivot.x, pivot.y },
                    Border = new[] { border.x, border.y, border.z, border.w },
                    PixelsPerUnit = sprite.pixelsPerUnit,
                    AlphaBounds = AlphaBounds(pixels),
                    Png = fileName
                };
                WriteJson(
                    Path.Combine(ResultRoot, "native-gameplay-portrait.json"),
                    report,
                    typeof(NativePortraitProbeReport));

                Plugin.Log.LogInfo(
                    "Native gameplay portrait probe: sprite=" +
                    report.SpriteName + " texture=" +
                    report.TextureSize[0] + "x" +
                    report.TextureSize[1] + " rect=" +
                    Join(report.SpriteRect) + " pivot=" +
                    Join(report.Pivot) + " border=" +
                    Join(report.Border) + " ppu=" +
                    report.PixelsPerUnit + " alpha=" +
                    Join(report.AlphaBounds) + ".");
            }
            finally
            {
                if (pixels != null)
                {
                    UnityEngine.Object.Destroy(pixels);
                }
            }
        }

        private static void EnsureRequest()
        {
            if (_requestChecked)
            {
                return;
            }

            _requestChecked = true;
            if (File.Exists(RequestPath))
            {
                _request = ReadJson<AssetProbeRequest>(RequestPath);
                Plugin.Log.LogInfo(
                    "Native hero icon probe requested at " + RequestPath);
            }
        }

        private static NativeIconMeasurement Measure(
            string hero,
            Sprite sprite,
            Texture2D pixels,
            Transform heroItemRoot,
            RectTransform iconRect,
            string fileName)
        {
            Rect spriteRect = sprite.rect;
            Rect textureRect = sprite.textureRect;
            Vector2 pivot = sprite.pivot;
            Vector4 border = sprite.border;
            Rect iconCanvas = iconRect == null ? default(Rect) : iconRect.rect;
            Vector2 anchorMin = iconRect == null ? Vector2.zero : iconRect.anchorMin;
            Vector2 anchorMax = iconRect == null ? Vector2.zero : iconRect.anchorMax;

            return new NativeIconMeasurement
            {
                Hero = hero,
                SpriteName = sprite.name,
                TextureName = sprite.texture == null
                    ? "<null>"
                    : sprite.texture.name,
                TextureSize = sprite.texture == null
                    ? new[] { 0, 0 }
                    : new[] { sprite.texture.width, sprite.texture.height },
                SpriteRect = new[]
                {
                    spriteRect.x,
                    spriteRect.y,
                    spriteRect.width,
                    spriteRect.height
                },
                TextureRect = new[]
                {
                    textureRect.x,
                    textureRect.y,
                    textureRect.width,
                    textureRect.height
                },
                Pivot = new[] { pivot.x, pivot.y },
                Border = new[] { border.x, border.y, border.z, border.w },
                PixelsPerUnit = sprite.pixelsPerUnit,
                Packed = sprite.packed,
                PackingRotation = sprite.packingRotation.ToString(),
                AlphaBounds = AlphaBounds(pixels),
                IconRectSize = new[] { iconCanvas.width, iconCanvas.height },
                IconAnchors = new[]
                {
                    anchorMin.x,
                    anchorMin.y,
                    anchorMax.x,
                    anchorMax.y
                },
                Layers = DescribeLayers(heroItemRoot).ToArray(),
                Png = fileName
            };
        }

        private static Texture2D ReadSpritePixels(Sprite sprite)
        {
            Texture2D source = sprite.texture;
            Rect rect = sprite.textureRect;
            int width = Math.Max(1, Mathf.RoundToInt(rect.width));
            int height = Math.Max(1, Mathf.RoundToInt(rect.height));
            RenderTexture render = RenderTexture.GetTemporary(
                source.width,
                source.height,
                0,
                RenderTextureFormat.ARGB32,
                RenderTextureReadWrite.Default);
            RenderTexture previous = RenderTexture.active;
            try
            {
                Graphics.Blit(source, render);
                RenderTexture.active = render;
                var result = new Texture2D(
                    width,
                    height,
                    TextureFormat.RGBA32,
                    false,
                    false);
                result.ReadPixels(rect, 0, 0, false);
                result.Apply(false, false);
                result.name = "NativeProbe/" + sprite.name;
                return result;
            }
            finally
            {
                RenderTexture.active = previous;
                RenderTexture.ReleaseTemporary(render);
            }
        }

        private static int[] AlphaBounds(Texture2D pixels)
        {
            Color32[] colors = pixels.GetPixels32();
            int minX = pixels.width;
            int minY = pixels.height;
            int maxX = -1;
            int maxY = -1;
            for (int y = 0; y < pixels.height; y++)
            {
                for (int x = 0; x < pixels.width; x++)
                {
                    if (colors[y * pixels.width + x].a == 0)
                    {
                        continue;
                    }
                    minX = Math.Min(minX, x);
                    minY = Math.Min(minY, y);
                    maxX = Math.Max(maxX, x);
                    maxY = Math.Max(maxY, y);
                }
            }

            return maxX < minX
                ? new[] { 0, 0, 0, 0 }
                : new[] { minX, minY, maxX - minX + 1, maxY - minY + 1 };
        }

        private static IEnumerable<string> DescribeLayers(Transform root)
        {
            if (root == null)
            {
                yield break;
            }

            foreach (Transform transform in root.GetComponentsInChildren<Transform>(true))
            {
                string path = RelativePath(root, transform);
                string components = string.Join(
                    ",",
                    transform.GetComponents<Component>()
                        .Where(component => component != null)
                        .Select(component => component.GetType().FullName)
                        .ToArray());
                Image image = transform.GetComponent<Image>();
                RawImage rawImage = transform.GetComponent<RawImage>();
                string asset = image != null && image.sprite != null
                    ? image.sprite.name
                    : rawImage != null && rawImage.texture != null
                        ? rawImage.texture.name
                        : "<none>";
                yield return path + "|active=" +
                    transform.gameObject.activeInHierarchy + "|components=" +
                    components + "|asset=" + asset;
            }
        }

        private static string RelativePath(Transform root, Transform value)
        {
            if (root == value)
            {
                return root.name;
            }

            var names = new List<string>();
            Transform current = value;
            while (current != null && current != root)
            {
                names.Add(current.name);
                current = current.parent;
            }
            names.Add(root.name);
            names.Reverse();
            return string.Join("/", names.ToArray());
        }

        private static string SafeFileName(string value)
        {
            foreach (char invalid in Path.GetInvalidFileNameChars())
            {
                value = value.Replace(invalid, '_');
            }
            return value;
        }

        private static string Join(IEnumerable<float> values)
        {
            return string.Join(",", values.Select(value => value.ToString("0.###")).ToArray());
        }

        private static string Join(IEnumerable<int> values)
        {
            return string.Join(",", values.Select(value => value.ToString()).ToArray());
        }

        private static T ReadJson<T>(string path)
        {
            var serializer = new DataContractJsonSerializer(typeof(T));
            using (FileStream stream = File.OpenRead(path))
            {
                return (T)serializer.ReadObject(stream);
            }
        }

        private static void WriteJson(string path, object value, Type type)
        {
            var serializer = new DataContractJsonSerializer(type);
            using (FileStream stream = File.Create(path))
            {
                serializer.WriteObject(stream, value);
            }
        }
    }

    internal sealed class RuntimeAssetProbeScanner : MonoBehaviour
    {
        private static Type _heroItemType;
        private static System.Reflection.FieldInfo _heroSoField;
        private static System.Reflection.FieldInfo _heroIdField;
        private float _nextScan;

        private void Update()
        {
            if (Time.unscaledTime < _nextScan)
            {
                return;
            }

            _nextScan = Time.unscaledTime + 2f;
            if (!RuntimeAssetProbe.Enabled)
            {
                return;
            }
            if (!EnsureBindings())
            {
                return;
            }

            foreach (UnityEngine.Object candidate in
                Resources.FindObjectsOfTypeAll(_heroItemType))
            {
                Component component = candidate as Component;
                if (component == null || !component.gameObject.activeInHierarchy)
                {
                    continue;
                }

                object heroSo = _heroSoField.GetValue(candidate);
                object heroId = heroSo == null
                    ? null
                    : _heroIdField.GetValue(heroSo);
                Transform iconTransform =
                    component.transform.Find("Content/Icon");
                Image icon = iconTransform == null
                    ? null
                    : iconTransform.GetComponent<Image>();
                if (heroId != null && icon != null)
                {
                    RuntimeAssetProbe.CaptureHeroButton(
                        heroId.ToString(),
                        icon.sprite,
                        component.transform,
                        icon.rectTransform);
                }
            }
        }

        private static bool EnsureBindings()
        {
            if (_heroItemType != null)
            {
                return _heroSoField != null && _heroIdField != null;
            }

            _heroItemType = AccessTools.TypeByName("HeroItemView");
            _heroSoField = _heroItemType == null
                ? null
                : AccessTools.Field(_heroItemType, "HeroSO");
            Type heroSoType = AccessTools.TypeByName("TheBazaar.HeroSO");
            _heroIdField = heroSoType == null
                ? null
                : AccessTools.Field(heroSoType, "HeroID");
            return _heroSoField != null && _heroIdField != null;
        }
    }
}
