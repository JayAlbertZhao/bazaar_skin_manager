using System;
using System.Diagnostics;
using System.Reflection;
using HarmonyLib;
using UnityEngine;

namespace BazaarSkinManager.TheBazaar
{
    internal static class VisualOwnership
    {
        private const string PvpPlacementName = "PvpScreen";

        [ThreadStatic]
        private static int _diagnosticLocalPortraitDepth;

        private static Type _heroViewTransitionType;
        private static FieldInfo _selectedHeroField;
        private static FieldInfo _isHeroField;

        internal enum PortraitLoadOwnership
        {
            Unknown,
            Local,
            Opponent,
            Preview,
            Diagnostic
        }

        public static bool IsLocalHeroPortraitLoad()
        {
            PortraitLoadOwnership ownership = ClassifyPortraitLoad();
            return ownership == PortraitLoadOwnership.Local ||
                ownership == PortraitLoadOwnership.Diagnostic;
        }

        public static PortraitLoadOwnership ClassifyPortraitLoad()
        {
            if (_diagnosticLocalPortraitDepth > 0)
            {
                return PortraitLoadOwnership.Diagnostic;
            }

            PortraitLoadOwnership best = PortraitLoadOwnership.Unknown;
            StackFrame[] frames = new StackTrace(false).GetFrames();
            if (frames == null)
            {
                return best;
            }

            foreach (StackFrame frame in frames)
            {
                MethodBase method = frame.GetMethod();
                Type declaringType = method == null
                    ? null
                    : method.DeclaringType;
                string typeName = declaringType == null
                    ? string.Empty
                    : declaringType.FullName;
                if (!string.IsNullOrEmpty(typeName) &&
                    typeName.IndexOf(
                        "BoardBuilder+<LoadHeroPortraitAsync>",
                        StringComparison.Ordinal) >= 0)
                {
                    return PortraitLoadOwnership.Local;
                }
                if (!string.IsNullOrEmpty(typeName) &&
                    typeName.IndexOf(
                        "EncounterController+<<Setup>g__CreateOpponentSkinData",
                        StringComparison.Ordinal) >= 0)
                {
                    best = PortraitLoadOwnership.Opponent;
                    continue;
                }
                if (IsPreviewPortraitCallSite(typeName))
                {
                    best = PortraitLoadOwnership.Preview;
                }
            }
            return best;
        }

        public static bool IsPreviewPortraitLoad(
            PortraitLoadOwnership ownership)
        {
            return ownership == PortraitLoadOwnership.Preview ||
                ownership == PortraitLoadOwnership.Diagnostic;
        }

        public static string PortraitOwnerName(
            PortraitLoadOwnership ownership)
        {
            return ownership.ToString().ToLowerInvariant();
        }

        public static IDisposable AssumeLocalPortraitForDiagnostic()
        {
            _diagnosticLocalPortraitDepth++;
            return new DiagnosticLocalPortraitScope();
        }

        public static bool IsOpponentHierarchy(Component component)
        {
            Transform current = component == null
                ? null
                : component.transform;
            while (current != null)
            {
                string name = current.gameObject.name ?? string.Empty;
                if (string.Equals(
                        name,
                        "OpponentBoardAnchor",
                        StringComparison.Ordinal) ||
                    name.IndexOf(
                        "OpponentPortraitSocket",
                        StringComparison.Ordinal) >= 0 ||
                    name.IndexOf(
                        "OpponentPortraitAnchor",
                        StringComparison.Ordinal) >= 0)
                {
                    return true;
                }
                current = current.parent;
            }
            return false;
        }

        public static bool ShouldReplaceSkinEditDisplay(
            object display,
            string placementName)
        {
            if (!string.Equals(
                    placementName,
                    PvpPlacementName,
                    StringComparison.Ordinal))
            {
                return true;
            }

            object transition = FindHeroViewTransition(display);
            if (transition == null || _isHeroField == null)
            {
                Plugin.Log.LogWarning(
                    "Skipped PvpScreen standing replacement because local " +
                    "ownership could not be proven.");
                return false;
            }

            bool isHero = Convert.ToBoolean(_isHeroField.GetValue(transition));
            Plugin.Log.LogInfo(
                "PvpScreen standing ownership resolved: " +
                (isHero ? "local player" : "opponent") + ".");
            return isHero;
        }

        public static GameObject SkinEditGameObject(object value)
        {
            GameObject gameObject = value as GameObject;
            if (gameObject != null)
            {
                return gameObject;
            }
            Component component = value as Component;
            return component == null ? null : component.gameObject;
        }

        private static object FindHeroViewTransition(object display)
        {
            if (!EnsureHeroViewBindings() || display == null)
            {
                return null;
            }

            Component component = display as Component;
            if (component != null)
            {
                Component parent = component.GetComponentInParent(
                    _heroViewTransitionType) as Component;
                if (parent != null &&
                    object.ReferenceEquals(
                        _selectedHeroField.GetValue(parent),
                        display))
                {
                    return parent;
                }
            }

            foreach (UnityEngine.Object candidate in
                Resources.FindObjectsOfTypeAll(_heroViewTransitionType))
            {
                if (candidate != null &&
                    object.ReferenceEquals(
                        _selectedHeroField.GetValue(candidate),
                        display))
                {
                    return candidate;
                }
            }
            return null;
        }

        private static bool EnsureHeroViewBindings()
        {
            if (_heroViewTransitionType != null)
            {
                return _selectedHeroField != null && _isHeroField != null;
            }

            _heroViewTransitionType =
                AccessTools.TypeByName("HeroViewTransition");
            _selectedHeroField = _heroViewTransitionType == null
                ? null
                : AccessTools.Field(
                    _heroViewTransitionType,
                    "_selectedHero");
            _isHeroField = _heroViewTransitionType == null
                ? null
                : AccessTools.Field(_heroViewTransitionType, "_isHero");
            return _selectedHeroField != null && _isHeroField != null;
        }

        private static bool IsPreviewPortraitCallSite(string typeName)
        {
            if (string.IsNullOrEmpty(typeName))
            {
                return false;
            }

            // Current build 24570932 calls SkinAssetDataSO.LoadPortrait only
            // from these non-match collection/store/career surfaces. Keep the
            // whitelist explicit: a new board or opponent call site must not
            // inherit preview permission merely because it requests the same
            // SkinAssetDataSO.
            string[] previewCallers =
            {
                "CosmeticButton",
                "CosmeticItem",
                "ProfileCareerHeroInfosController",
                "TheBazaar.Store.SpecialContainer",
                "BattlePassTierPaid",
                "SkinAssetDataSO+<LoadDailyWeeklyImageAssetAsync>"
            };
            foreach (string caller in previewCallers)
            {
                if (typeName.IndexOf(caller, StringComparison.Ordinal) >= 0)
                {
                    return true;
                }
            }
            return false;
        }

        private sealed class DiagnosticLocalPortraitScope : IDisposable
        {
            private bool _disposed;

            public void Dispose()
            {
                if (_disposed)
                {
                    return;
                }
                _disposed = true;
                _diagnosticLocalPortraitDepth = Math.Max(
                    0,
                    _diagnosticLocalPortraitDepth - 1);
            }
        }
    }
}
