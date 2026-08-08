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
        public const string PluginVersion = "1.2.10";

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
            if (!RuntimeCompatibility.ValidateCurrent(
                out compatibilityFailure))
            {
                Logger.LogError(
                    "Skin Manager runtime disabled before loading its pack or " +
                    "installing hooks: " + compatibilityFailure + " Run the " +
                    "mod manager doctor/plan-install flow after confirming " +
                    "support for the current Steam build.");
                return;
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
            _harmony.PatchAll();
            SkinLoaderCoverage.Install(_harmony);
            RuntimeAudioReplacement.Initialize(gameObject);
            RuntimeAudioReplacement.Install(_harmony);
            RuntimeAudioTrace.Install(_harmony);

            UiReplacementScanner scanner = gameObject.AddComponent<UiReplacementScanner>();
            scanner.RescanSeconds = Math.Max(0.5f, rescanSeconds.Value);
            gameObject.AddComponent<HeroSelectIconReconciler>();
            gameObject.AddComponent<HeroSelectStandingState>();
            gameObject.AddComponent<RuntimeAssetProbeScanner>();
            gameObject.AddComponent<RuntimeSkinAuditScanner>();
            gameObject.AddComponent<RuntimeDiagnostics>();
            DontDestroyOnLoad(gameObject);

            Logger.LogInfo(
                "Loaded " + ActivePacks.Count + " enabled skin pack(s)." );
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
