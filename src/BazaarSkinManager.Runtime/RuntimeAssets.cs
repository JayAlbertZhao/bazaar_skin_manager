using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.Serialization.Json;
using BepInEx.Logging;
using UnityEngine;

namespace BazaarSkinManager.TheBazaar
{
    internal sealed class RuntimePack
    {
        private const float DefaultPixelsPerUnit = 100f;
        private const float MaximumPixelsPerUnit = 4096f;

        private readonly Dictionary<string, Texture2D> _textures;
        private readonly Dictionary<string, Sprite> _sprites;

        public ModPackManifest Manifest { get; private set; }
        public string PackDirectory { get; private set; }
        public RuntimeAudioPack Audio { get; private set; }

        private RuntimePack(
            ModPackManifest manifest,
            string packDirectory,
            Dictionary<string, Texture2D> textures,
            Dictionary<string, Sprite> sprites)
        {
            Manifest = manifest;
            PackDirectory = packDirectory;
            _textures = textures;
            _sprites = sprites;
        }

        public static string DefaultModsRoot()
        {
            return Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "BazaarSkinManager",
                "TheBazaar",
                "mods");
        }

        public static RuntimePack LoadFirstEnabled(string modsRoot, ManualLogSource log)
        {
            if (!Directory.Exists(modsRoot))
            {
                log.LogWarning("Mods root does not exist: " + modsRoot);
                return null;
            }

            string[] manifests = Directory.GetFiles(modsRoot, "mod.json", SearchOption.AllDirectories);
            Array.Sort(manifests, StringComparer.OrdinalIgnoreCase);
            foreach (string manifestPath in manifests)
            {
                try
                {
                    RuntimePack pack = Load(manifestPath);
                    if (pack.Manifest.Enabled &&
                        pack.Manifest.Target != null &&
                        string.Equals(pack.Manifest.Target.Hero, "Mak", StringComparison.OrdinalIgnoreCase))
                    {
                        try
                        {
                            pack.Audio = RuntimeAudioPack.Load(
                                pack.PackDirectory,
                                pack.Manifest.AudioManifest,
                                log);
                            if (pack.Audio != null)
                            {
                                log.LogInfo(
                                    "Predecoded " +
                                    pack.Audio.RouteCount +
                                    " exact external voice routes.");
                            }
                        }
                        catch (Exception exception)
                        {
                            pack.Audio = null;
                            log.LogWarning(
                                "Audio UGC was disabled; all original FMOD " +
                                "events remain active: " + exception.Message);
                        }
                        return pack;
                    }
                }
                catch (Exception exception)
                {
                    log.LogError("Failed to load " + manifestPath + ": " + exception);
                }
            }

            return null;
        }

        private static RuntimePack Load(string manifestPath)
        {
            ModPackManifest manifest;
            DataContractJsonSerializer serializer =
                new DataContractJsonSerializer(typeof(ModPackManifest));
            using (FileStream stream = File.OpenRead(manifestPath))
            {
                manifest = (ModPackManifest)serializer.ReadObject(stream);
            }

            if (manifest.SchemaVersion != 1)
            {
                throw new InvalidDataException("Unsupported schema version.");
            }
            if (manifest.VisualReplacements == null || manifest.Target == null)
            {
                throw new InvalidDataException("Pack target or replacements are missing.");
            }

            string packDirectory = Path.GetDirectoryName(Path.GetFullPath(manifestPath));
            string safePrefix = packDirectory.TrimEnd(Path.DirectorySeparatorChar) +
                Path.DirectorySeparatorChar;
            var textures = new Dictionary<string, Texture2D>(StringComparer.OrdinalIgnoreCase);
            var sprites = new Dictionary<string, Sprite>(StringComparer.OrdinalIgnoreCase);

            foreach (VisualReplacement replacement in manifest.VisualReplacements)
            {
                if (replacement == null ||
                    string.IsNullOrEmpty(replacement.Slot) ||
                    string.IsNullOrEmpty(replacement.File))
                {
                    throw new InvalidDataException("Replacement slot or file is missing.");
                }
                if (!AssetNameMatcher.IsValidMode(replacement.MatchMode))
                {
                    throw new InvalidDataException(
                        "Unsupported match_mode for " + replacement.Slot +
                        ": " + replacement.MatchMode);
                }

                string assetPath = Path.GetFullPath(Path.Combine(packDirectory, replacement.File));
                if (!assetPath.StartsWith(safePrefix, StringComparison.OrdinalIgnoreCase))
                {
                    throw new InvalidDataException("Asset path escapes the pack: " + replacement.File);
                }

                byte[] data = File.ReadAllBytes(assetPath);
                Texture2D texture = new Texture2D(2, 2, TextureFormat.RGBA32, false);
                if (!texture.LoadImage(data, false))
                {
                    throw new InvalidDataException("Unity failed to decode " + replacement.File);
                }

                texture.name = "BazaarSkinManager/" + replacement.Slot;
                texture.wrapMode = TextureWrapMode.Clamp;
                texture.filterMode = FilterMode.Bilinear;
                UnityEngine.Object.DontDestroyOnLoad(texture);

                float pixelsPerUnit = ResolvePixelsPerUnit(replacement);
                UnityEngine.Sprite sprite = UnityEngine.Sprite.Create(
                    texture,
                    new Rect(0, 0, texture.width, texture.height),
                    new Vector2(0.5f, 0.5f),
                    pixelsPerUnit,
                    0,
                    SpriteMeshType.FullRect);
                sprite.name = "BazaarSkinManager/" + replacement.Slot;
                UnityEngine.Object.DontDestroyOnLoad(sprite);

                textures[replacement.Slot] = texture;
                sprites[replacement.Slot] = sprite;
            }

            return new RuntimePack(manifest, packDirectory, textures, sprites);
        }

        private static float ResolvePixelsPerUnit(VisualReplacement replacement)
        {
            float pixelsPerUnit = replacement.PixelsPerUnit;
            if (pixelsPerUnit == 0f)
            {
                return DefaultPixelsPerUnit;
            }
            if (float.IsNaN(pixelsPerUnit) ||
                float.IsInfinity(pixelsPerUnit) ||
                pixelsPerUnit < 1f ||
                pixelsPerUnit > MaximumPixelsPerUnit)
            {
                throw new InvalidDataException(
                    "Replacement pixels_per_unit must be between 1 and " +
                    MaximumPixelsPerUnit + ": " + replacement.Slot);
            }

            return pixelsPerUnit;
        }

        public bool IsTargetSkin(object instance)
        {
            UnityEngine.Object unityObject = instance as UnityEngine.Object;
            if (unityObject == null || Manifest.Target == null)
            {
                return false;
            }

            string needle = Manifest.Target.SkinNameContains;
            return !string.IsNullOrEmpty(needle) &&
                unityObject.name.IndexOf(needle, StringComparison.OrdinalIgnoreCase) >= 0;
        }

        public Texture2D Texture(string slot)
        {
            Texture2D value;
            return _textures.TryGetValue(slot, out value) ? value : null;
        }

        public Sprite Sprite(string slot)
        {
            Sprite value;
            return _sprites.TryGetValue(slot, out value) ? value : null;
        }

        public bool UsesPreloadedDeployment(string slot)
        {
            if (string.IsNullOrEmpty(slot) ||
                Manifest == null ||
                Manifest.VisualReplacements == null)
            {
                return false;
            }

            foreach (VisualReplacement replacement in
                Manifest.VisualReplacements)
            {
                if (replacement != null &&
                    string.Equals(
                        replacement.Slot,
                        slot,
                        StringComparison.OrdinalIgnoreCase) &&
                    replacement.Deployment != null &&
                    string.Equals(
                        replacement.Deployment.Mode,
                        "preload_unity_texture2d",
                        StringComparison.OrdinalIgnoreCase))
                {
                    return true;
                }
            }

            return false;
        }

        public VisualReplacement Match(string assetName)
        {
            if (string.IsNullOrEmpty(assetName))
            {
                return null;
            }

            VisualReplacement bestContains = null;
            int bestContainsLength = -1;
            foreach (VisualReplacement replacement in Manifest.VisualReplacements)
            {
                if (replacement.DirectOnly ||
                    UsesPreloadedDeployment(replacement.Slot))
                {
                    continue;
                }

                foreach (string needle in replacement.MatchNames ?? new List<string>())
                {
                    if (!AssetNameMatcher.Matches(
                        replacement.MatchMode,
                        assetName,
                        needle))
                    {
                        continue;
                    }

                    // Exact routes always win over fuzzy routes regardless of
                    // manifest ordering. A broad name such as
                    // "Skin_MAK_01a_P" must never steal
                    // "Skin_MAK_01a_Portrait" from its dedicated slot.
                    if (string.Equals(
                        replacement.MatchMode,
                        "exact",
                        StringComparison.OrdinalIgnoreCase))
                    {
                        return replacement;
                    }
                    if (!string.IsNullOrEmpty(needle) &&
                        needle.Length > bestContainsLength)
                    {
                        bestContains = replacement;
                        bestContainsLength = needle.Length;
                    }
                }
            }

            return bestContains;
        }

        public void DisposeAudio()
        {
            if (Audio != null)
            {
                Audio.Dispose();
                Audio = null;
            }
        }
    }
}
