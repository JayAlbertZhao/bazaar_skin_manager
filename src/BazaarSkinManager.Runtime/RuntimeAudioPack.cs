using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Runtime.Serialization.Json;
using System.Security.Cryptography;
using System.Text;
using BepInEx.Logging;
using UnityEngine;

namespace BazaarSkinManager.TheBazaar
{
    internal sealed class LoadedAudioVariant
    {
        internal AudioClip Clip;
        internal int Weight;
        internal string SampleName;
    }

    internal sealed class LoadedAudioRoute
    {
        internal string LogicalSlot;
        internal string Category;
        internal string EventGuid;
        internal string EventPath;
        internal List<AudioSelector> Selectors;
        internal List<LoadedAudioVariant> Variants;
    }

    internal sealed class RuntimeAudioPack
    {
        private readonly Dictionary<string, LoadedAudioRoute> _routes;
        private readonly List<AudioClip> _ownedClips;
        private readonly System.Random _random = new System.Random();
        private readonly object _randomLock = new object();

        internal float Gain { get; private set; }
        internal int RouteCount { get { return _routes.Count; } }

        private RuntimeAudioPack(
            Dictionary<string, LoadedAudioRoute> routes,
            List<AudioClip> ownedClips,
            float gain)
        {
            _routes = routes;
            _ownedClips = ownedClips;
            Gain = gain;
        }

        internal static RuntimeAudioPack Load(
            string packDirectory,
            string manifestRelative,
            ManualLogSource log)
        {
            if (string.IsNullOrWhiteSpace(manifestRelative))
            {
                return null;
            }

            string packRoot = Path.GetFullPath(packDirectory);
            string safePrefix = packRoot.TrimEnd(
                Path.DirectorySeparatorChar,
                Path.AltDirectorySeparatorChar) +
                Path.DirectorySeparatorChar;
            string manifestPath = SafePath(
                packRoot,
                safePrefix,
                manifestRelative);
            AudioPackManifest manifest;
            DataContractJsonSerializer serializer =
                new DataContractJsonSerializer(typeof(AudioPackManifest));
            using (FileStream stream = File.OpenRead(manifestPath))
            {
                manifest = (AudioPackManifest)serializer.ReadObject(stream);
            }

            if (manifest.SchemaVersion != 1 ||
                !manifest.Enabled ||
                manifest.Target == null ||
                !string.Equals(
                    manifest.Target.Game,
                    "The Bazaar",
                    StringComparison.Ordinal) ||
                !string.Equals(
                    manifest.Target.SteamBuild,
                    "24001960",
                    StringComparison.Ordinal) ||
                !string.Equals(
                    manifest.Target.Hero,
                    "Mak",
                    StringComparison.Ordinal) ||
                !string.Equals(
                    manifest.Fallback,
                    "original",
                    StringComparison.Ordinal))
            {
                throw new InvalidDataException(
                    "Audio manifest target, schema, or fallback is unsupported.");
            }
            if (manifest.AudioFormat == null ||
                !string.Equals(
                    manifest.AudioFormat.Encoding,
                    "wav_pcm16",
                    StringComparison.Ordinal) ||
                manifest.AudioFormat.SampleRateHz != 22050 ||
                manifest.AudioFormat.Channels != 1 ||
                manifest.AudioFormat.SampleWidthBytes != 2)
            {
                throw new InvalidDataException(
                    "Audio manifest must describe 22050 Hz mono PCM16 WAV.");
            }

            float gain = manifest.Playback == null
                ? 0.8f
                : manifest.Playback.Gain;
            if (float.IsNaN(gain) ||
                float.IsInfinity(gain) ||
                gain <= 0f ||
                gain > 1f)
            {
                throw new InvalidDataException(
                    "Audio playback gain must be greater than zero and at most one.");
            }

            Dictionary<string, AudioClip> clips =
                new Dictionary<string, AudioClip>(
                    StringComparer.OrdinalIgnoreCase);
            List<AudioClip> owned = new List<AudioClip>();
            Dictionary<string, LoadedAudioRoute> routes =
                new Dictionary<string, LoadedAudioRoute>(
                    StringComparer.Ordinal);
            try
            {
                foreach (AudioRoute route in manifest.Routes ??
                    new List<AudioRoute>())
                {
                    ValidateRoute(route);
                    string key = RouteKey(
                        route.EventGuid,
                        route.Selectors);
                    if (routes.ContainsKey(key))
                    {
                        throw new InvalidDataException(
                            "Duplicate audio route identity: " +
                            route.LogicalSlot);
                    }

                    List<LoadedAudioVariant> loaded =
                        new List<LoadedAudioVariant>();
                    foreach (AudioVariant variant in route.Variants ??
                        new List<AudioVariant>())
                    {
                        if (variant == null ||
                            string.IsNullOrWhiteSpace(variant.File) ||
                            variant.Weight < 1)
                        {
                            log.LogWarning(
                                "Skipping malformed audio variant for " +
                                route.LogicalSlot + ".");
                            continue;
                        }
                        try
                        {
                            string assetPath = SafePath(
                                packRoot,
                                safePrefix,
                                variant.File);
                            byte[] data = File.ReadAllBytes(assetPath);
                            string digest = Sha256(data);
                            if (!string.Equals(
                                digest,
                                variant.Sha256,
                                StringComparison.OrdinalIgnoreCase))
                            {
                                throw new InvalidDataException(
                                    "SHA-256 mismatch.");
                            }

                            AudioClip clip;
                            if (!clips.TryGetValue(assetPath, out clip))
                            {
                                clip = PcmWaveDecoder.CreateClip(
                                    "BazaarSkinManagerVoice/" +
                                    (variant.SampleName ??
                                        Path.GetFileNameWithoutExtension(
                                            assetPath)),
                                    data);
                                clips[assetPath] = clip;
                                owned.Add(clip);
                            }
                            loaded.Add(
                                new LoadedAudioVariant
                                {
                                    Clip = clip,
                                    Weight = variant.Weight,
                                    SampleName = variant.SampleName
                                });
                        }
                        catch (Exception exception)
                        {
                            log.LogWarning(
                                "Audio variant failed validation/decode; " +
                                "that variant will fall back to another " +
                                "candidate or the original event: " +
                                route.LogicalSlot + "/" +
                                variant.SampleName + ": " +
                                exception.Message);
                        }
                    }
                    if (loaded.Count == 0)
                    {
                        log.LogWarning(
                            "Audio route has no usable variants; original " +
                            "FMOD event remains active: " +
                            route.LogicalSlot);
                        continue;
                    }
                    routes[key] = new LoadedAudioRoute
                    {
                        LogicalSlot = route.LogicalSlot,
                        Category = route.Category,
                        EventGuid = route.EventGuid.ToLowerInvariant(),
                        EventPath = route.EventPath,
                        Selectors = route.Selectors ??
                            new List<AudioSelector>(),
                        Variants = loaded
                    };
                }
            }
            catch
            {
                foreach (AudioClip clip in owned)
                {
                    UnityEngine.Object.Destroy(clip);
                }
                throw;
            }

            if (routes.Count == 0)
            {
                foreach (AudioClip clip in owned)
                {
                    UnityEngine.Object.Destroy(clip);
                }
                throw new InvalidDataException(
                    "Audio manifest has no usable routes.");
            }
            return new RuntimeAudioPack(routes, owned, gain);
        }

        internal bool TryRoute(
            string eventGuid,
            IList<AudioSelector> selectors,
            out LoadedAudioRoute route)
        {
            return _routes.TryGetValue(
                RouteKey(eventGuid, selectors),
                out route);
        }

        internal LoadedAudioVariant Choose(LoadedAudioRoute route)
        {
            if (route == null ||
                route.Variants == null ||
                route.Variants.Count == 0)
            {
                return null;
            }

            int total = 0;
            foreach (LoadedAudioVariant variant in route.Variants)
            {
                total = checked(total + variant.Weight);
            }
            int value;
            lock (_randomLock)
            {
                value = _random.Next(total);
            }
            foreach (LoadedAudioVariant variant in route.Variants)
            {
                if (value < variant.Weight)
                {
                    return variant;
                }
                value -= variant.Weight;
            }
            return route.Variants[route.Variants.Count - 1];
        }

        internal void Dispose()
        {
            foreach (AudioClip clip in _ownedClips)
            {
                if (clip != null)
                {
                    UnityEngine.Object.Destroy(clip);
                }
            }
            _ownedClips.Clear();
            _routes.Clear();
        }

        internal static string RouteKey(
            string eventGuid,
            IList<AudioSelector> selectors)
        {
            Guid parsed;
            if (!Guid.TryParse(eventGuid, out parsed))
            {
                throw new InvalidDataException(
                    "Audio route event GUID is invalid: " + eventGuid);
            }
            StringBuilder key = new StringBuilder(
                parsed.ToString("D", CultureInfo.InvariantCulture));
            foreach (AudioSelector selector in selectors ??
                (IList<AudioSelector>)new List<AudioSelector>())
            {
                key.Append('\u001f');
                key.Append(selector == null ? string.Empty : selector.Parameter);
                key.Append('\u001e');
                key.Append(selector == null ? string.Empty : selector.Label);
            }
            return key.ToString();
        }

        private static void ValidateRoute(AudioRoute route)
        {
            if (route == null ||
                string.IsNullOrWhiteSpace(route.LogicalSlot) ||
                string.IsNullOrWhiteSpace(route.Category) ||
                string.IsNullOrWhiteSpace(route.EventGuid) ||
                string.IsNullOrWhiteSpace(route.EventPath))
            {
                throw new InvalidDataException(
                    "Audio route identity is incomplete.");
            }
            if (route.Category != "hero_voice" &&
                route.Category != "merchant_voice" &&
                route.Category != "menu_voice")
            {
                throw new InvalidDataException(
                    "Audio route category is unsupported: " +
                    route.Category);
            }
            RouteKey(route.EventGuid, route.Selectors);
        }

        private static string SafePath(
            string packRoot,
            string safePrefix,
            string relative)
        {
            if (string.IsNullOrWhiteSpace(relative) ||
                Path.IsPathRooted(relative))
            {
                throw new InvalidDataException(
                    "Audio path must be pack-relative.");
            }
            string path = Path.GetFullPath(
                Path.Combine(packRoot, relative));
            if (!path.StartsWith(
                safePrefix,
                StringComparison.OrdinalIgnoreCase))
            {
                throw new InvalidDataException(
                    "Audio path escapes the pack: " + relative);
            }
            return path;
        }

        private static string Sha256(byte[] data)
        {
            using (SHA256 sha = SHA256.Create())
            {
                byte[] digest = sha.ComputeHash(data);
                StringBuilder value = new StringBuilder(digest.Length * 2);
                foreach (byte item in digest)
                {
                    value.Append(item.ToString(
                        "x2",
                        CultureInfo.InvariantCulture));
                }
                return value.ToString();
            }
        }
    }
}
