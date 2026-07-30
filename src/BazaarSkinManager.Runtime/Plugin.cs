using System;
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
        public const string PluginVersion = "0.9.1";

        internal static RuntimePack ActivePack;
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

            ActivePack = RuntimePack.LoadFirstEnabled(root, Logger);
            if (ActivePack == null)
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
                "Loaded pack " + ActivePack.Manifest.Id + " " + ActivePack.Manifest.Version);
        }

        private void OnDestroy()
        {
            RuntimeAudioReplacement.Remove();
            if (ActivePack != null)
            {
                ActivePack.DisposeAudio();
            }
            SkinLoaderCoverage.RemoveAll();
            StandingOverlay.RemoveAll();
            if (_harmony != null)
            {
                _harmony.UnpatchSelf();
            }
        }
    }
}
