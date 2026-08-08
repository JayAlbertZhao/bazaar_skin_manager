using System;
using System.Collections.Generic;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using HarmonyLib;
using UnityEngine;

namespace BazaarSkinManager.TheBazaar
{
    internal sealed class RuntimeDiagnostics : MonoBehaviour
    {
        private static readonly HashSet<string> ReportedReplacements =
            new HashSet<string>(StringComparer.Ordinal);
        private static readonly HashSet<string> ReportedUnmatchedMakVisuals =
            new HashSet<string>(StringComparer.Ordinal);
        private static readonly HashSet<string> ReportedCentralStates =
            new HashSet<string>(StringComparer.Ordinal);
        private static readonly HashSet<string> ReportedLoaderStates =
            new HashSet<string>(StringComparer.Ordinal);
        private static readonly HashSet<string> ReportedPortraitDecisions =
            new HashSet<string>(StringComparer.Ordinal);

        private float _nextScan;
        private bool _selfTestRunning;
        private bool _selfTestComplete;

        public static void ReportReplacement(string slot, string source)
        {
            lock (ReportedReplacements)
            {
                if (!ReportedReplacements.Add(slot))
                {
                    return;
                }
            }

            Plugin.Log.LogInfo("Applied " + slot + " replacement via " + source);
        }

        public static void ReportPortraitDecision(
            string slot,
            RuntimePack pack,
            VisualOwnership.PortraitLoadOwnership ownership,
            string callSite,
            string action)
        {
            string packId = pack == null || pack.Manifest == null
                ? "<none>"
                : pack.Manifest.Id ?? "<unknown>";
            string skin = pack == null || pack.Manifest == null ||
                pack.Manifest.Target == null
                    ? "<unknown>"
                    : pack.Manifest.Target.Skin ?? "<unknown>";
            string owner = VisualOwnership.PortraitOwnerName(ownership);
            string detail = "slot=" + slot +
                " pack=" + packId +
                " skin=" + skin +
                " owner=" + owner +
                " callSite=" + callSite +
                " action=" + action;
            lock (ReportedPortraitDecisions)
            {
                if (!ReportedPortraitDecisions.Add(detail))
                {
                    return;
                }
            }
            Plugin.Log.LogInfo("Portrait route: " + detail);
        }

        public static void ReportUnmatchedMakVisual(
            string componentType,
            string objectName,
            string assetName)
        {
            string description = componentType + " " + objectName +
                " asset=" + assetName;
            string hero = Plugin.ActivePack == null ||
                Plugin.ActivePack.Manifest == null ||
                Plugin.ActivePack.Manifest.Target == null
                ? string.Empty
                : Plugin.ActivePack.Manifest.Target.Hero ?? string.Empty;
            string heroCode = Plugin.ActivePack == null
                ? string.Empty
                : Plugin.ActivePack.TargetHeroCode();
            if ((string.IsNullOrEmpty(hero) ||
                 description.IndexOf(hero, StringComparison.OrdinalIgnoreCase) < 0) &&
                (string.IsNullOrEmpty(heroCode) ||
                 description.IndexOf(heroCode, StringComparison.OrdinalIgnoreCase) < 0))
            {
                return;
            }

            lock (ReportedUnmatchedMakVisuals)
            {
                if (!ReportedUnmatchedMakVisuals.Add(description))
                {
                    return;
                }
            }

            Plugin.Log.LogInfo(
                "Unmatched visible target-hero visual for coverage diagnostics: " +
                description);
        }

        public static void ReportCentralState(
            bool displayActive,
            bool targetGraphicActive,
            bool skinEditActive,
            string selectedHero,
            string loadedAsset,
            bool targetDefault,
            bool attach)
        {
            string state = "displayActive=" + displayActive +
                " targetGraphicActive=" + targetGraphicActive +
                " skinEditActive=" + skinEditActive +
                " selected=" + selectedHero +
                " loadedAsset=" + loadedAsset +
                " targetDefault=" + targetDefault +
                " attach=" + attach;
            lock (ReportedCentralStates)
            {
                if (!ReportedCentralStates.Add(state))
                {
                    return;
                }
            }

            Plugin.Log.LogInfo(
                "HeroSelect central standing state: " + state);
        }

        public static void ReportLoaderState(
            string loader,
            string state,
            string detail)
        {
            string key = loader + "|" + state + "|" + detail;
            lock (ReportedLoaderStates)
            {
                if (!ReportedLoaderStates.Add(key))
                {
                    return;
                }
            }

            Plugin.Log.LogInfo(
                "Skin loader " + loader + ": " + state + " - " + detail);
        }

        private void Update()
        {
            if (_selfTestComplete || _selfTestRunning ||
                Time.unscaledTime < _nextScan || Plugin.ActivePack == null)
            {
                return;
            }

            _nextScan = Time.unscaledTime + 5f;
            Type skinType = SkinPatchTargets.SkinType();
            if (skinType == null)
            {
                return;
            }

            foreach (UnityEngine.Object candidate in
                Resources.FindObjectsOfTypeAll(skinType))
            {
                if (SkinPatchTargets.ShouldReplace(candidate))
                {
                    VerifyGameplayPortrait(candidate, skinType);
                    return;
                }
            }
        }

        private async void VerifyGameplayPortrait(
            UnityEngine.Object target,
            Type skinType)
        {
            _selfTestRunning = true;
            try
            {
                Plugin.Log.LogInfo(
                    "Found target skin asset for runtime self-test: " + target.name);
                MethodInfo method = AccessTools.Method(
                    skinType,
                    "LoadPortraitSpriteAsync",
                    new[] { typeof(CancellationToken) });
                if (method == null)
                {
                    throw new MissingMethodException(
                        skinType.FullName,
                        "LoadPortraitSpriteAsync(CancellationToken)");
                }

                Task<Sprite> task;
                // The diagnostic invokes the loader outside BoardBuilder's
                // normal local-player call stack. Mark only this synchronous
                // invocation as local so the exact same ownership-gated
                // postfix exercised in real gameplay can replace the result.
                // Without this scope the diagnostic always observed the
                // untouched loader (often <null>) and reported a false
                // failure even though the runtime route was healthy.
                using (VisualOwnership.AssumeLocalPortraitForDiagnostic())
                {
                    task = method.Invoke(
                        target,
                        new object[] { CancellationToken.None }) as Task<Sprite>;
                }
                if (task == null)
                {
                    throw new InvalidOperationException(
                        "Portrait loader did not return Task<Sprite>.");
                }

                Sprite sprite = await task;
                bool preloaded =
                    Plugin.ActivePack.UsesPreloadedDeployment(
                        "portrait_gameplay");
                bool replaced = sprite != null &&
                    (preloaded ||
                     sprite.name == "BazaarSkinManager/portrait_gameplay");
                if (replaced)
                {
                    Plugin.Log.LogInfo(
                        "Runtime self-test passed: target portrait resolves " +
                        (preloaded
                            ? "from the deploy-time patched bundle as "
                            : "to ") +
                        sprite.name + " (" + sprite.texture.width + "x" +
                        sprite.texture.height + ").");
                }
                else
                {
                    Plugin.Log.LogWarning(
                        "Runtime self-test failed: target portrait resolved to " +
                        (sprite == null ? "<null>" : sprite.name) + ".");
                }
                _selfTestComplete = true;
            }
            catch (Exception exception)
            {
                Plugin.Log.LogError("Runtime self-test failed: " + exception);
                _selfTestComplete = true;
            }
            finally
            {
                _selfTestRunning = false;
            }
        }
    }
}
