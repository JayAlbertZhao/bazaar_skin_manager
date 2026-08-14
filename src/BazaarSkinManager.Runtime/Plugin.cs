using System;
using System.Collections.Generic;
using BepInEx;
using BepInEx.Configuration;
using HarmonyLib;
using UnityEngine;

namespace BazaarSkinManager.TheBazaar
{
    [BepInPlugin(PluginGuid, PluginName, PluginVersion)]
    public sealed class Plugin : BaseUnityPlugin
    {
        public const string PluginGuid = "bazaar-skin-manager.the-bazaar.runtime";
        public const string PluginName = "The Bazaar Skin Manager Runtime";
        public const string PluginVersion = "1.4.14";

        internal static RuntimePack ActivePack;
        internal static List<RuntimePack> ActivePacks = new List<RuntimePack>();
        internal static BepInEx.Logging.ManualLogSource Log;

        private Harmony _harmony;

        private void Awake()
        {
            Log = Logger;
            ConfigEntry<bool> enabled = Config.Bind(
                "General",
                "Enabled",
                true,
                "Enable the external skin-pack runtime.");
            ConfigEntry<string> modsRoot = Config.Bind(
                "General",
                "ModsRoot",
                string.Empty,
                "Optional override for the external data-only mod directory.");
            ConfigEntry<float> rescanSeconds = Config.Bind(
                "General",
                "UiRescanSeconds",
                2.0f,
                "Interval used to replace newly loaded UI sprites.");

            if (!enabled.Value)
            {
                Logger.LogInfo("Runtime disabled by configuration.");
                return;
            }

            string compatibilityFailure;
            bool compatibilityVerified = RuntimeCompatibility.ValidateCurrent(
                out compatibilityFailure);
            if (!compatibilityVerified)
            {
                Logger.LogWarning(
                    "Runtime compatibility changed; continuing in best-effort " +
                    "mode. Exact hooks that still exist will run and missing " +
                    "surfaces will keep the game's originals: " +
                    compatibilityFailure);
            }

            string root = string.IsNullOrWhiteSpace(modsRoot.Value)
                ? RuntimePack.DefaultModsRoot()
                : Environment.ExpandEnvironmentVariables(modsRoot.Value);

            ActivePacks = RuntimePack.LoadAllEnabled(root, Logger);
            ActivePack = ActivePacks.Count == 0 ? null : ActivePacks[0];
            if (ActivePacks.Count == 0)
            {
                Logger.LogWarning("No compatible enabled external skin pack was found.");
                return;
            }

            _harmony = new Harmony(PluginGuid);
            TryFeature("visual hooks", delegate { _harmony.PatchAll(); });
            TryFeature(
                "skin-loader coverage",
                delegate { SkinLoaderCoverage.Install(_harmony); });
            TryFeature(
                "audio host",
                delegate { RuntimeAudioReplacement.Initialize(gameObject); });
            TryFeature(
                "audio replacement hooks",
                delegate { RuntimeAudioReplacement.Install(_harmony); });
            TryFeature(
                "audio diagnostics",
                delegate { RuntimeAudioTrace.Install(_harmony); });

            TryFeature("UI scanner", delegate
            {
                UiReplacementScanner scanner =
                    gameObject.AddComponent<UiReplacementScanner>();
                scanner.RescanSeconds = Math.Max(0.5f, rescanSeconds.Value);
            });
            TryFeature(
                "hero-select icon reconciler",
                delegate { gameObject.AddComponent<HeroSelectIconReconciler>(); });
            TryFeature(
                "hero-select standing state",
                delegate { gameObject.AddComponent<HeroSelectStandingState>(); });
            TryFeature(
                "asset probe",
                delegate { gameObject.AddComponent<RuntimeAssetProbeScanner>(); });
            TryFeature(
                "runtime skin audit",
                delegate { gameObject.AddComponent<RuntimeSkinAuditScanner>(); });
            TryFeature(
                "runtime diagnostics",
                delegate { gameObject.AddComponent<RuntimeDiagnostics>(); });
            DontDestroyOnLoad(gameObject);

            Logger.LogInfo(
                "Loaded " + ActivePacks.Count + " enabled skin pack(s); " +
                (compatibilityVerified
                    ? "compatibility verified."
                    : "best-effort compatibility mode active."));
        }

        private void TryFeature(string name, Action install)
        {
            try
            {
                install();
            }
            catch (Exception exception)
            {
                Logger.LogWarning(
                    "Skipped incompatible " + name + "; remaining skin " +
                    "features continue and this surface keeps the original: " +
                    exception);
            }
        }

        private void OnDestroy()
        {
            RuntimeAudioReplacement.Remove();
            foreach (RuntimePack pack in ActivePacks)
            {
                pack.DisposeAudio();
            }
            ActivePacks.Clear();
            ActivePack = null;
            SkinLoaderCoverage.RemoveAll();
            StandingOverlay.RemoveAll();
            if (_harmony != null)
            {
                _harmony.UnpatchSelf();
            }
        }

        internal static RuntimePack PackForSkin(object instance)
        {
            foreach (RuntimePack pack in ActivePacks)
            {
                if (pack.IsTargetSkin(instance))
                {
                    return pack;
                }
            }
            return null;
        }

        internal static RuntimePack PackForHero(string heroName)
        {
            foreach (RuntimePack pack in ActivePacks)
            {
                if (pack.IsTargetHero(heroName) ||
                    string.Equals(
                        pack.TargetHeroCode(),
                        heroName,
                        StringComparison.OrdinalIgnoreCase))
                {
                    return pack;
                }
            }
            return null;
        }

        internal static RuntimePack PackMatchingAsset(
            string assetName,
            out VisualReplacement replacement)
        {
            foreach (RuntimePack pack in ActivePacks)
            {
                replacement = pack.Match(assetName);
                if (replacement != null)
                {
                    return pack;
                }
            }
            replacement = null;
            return null;
        }
    }
}
