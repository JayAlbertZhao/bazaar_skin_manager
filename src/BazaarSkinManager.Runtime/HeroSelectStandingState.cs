using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using HarmonyLib;
using UnityEngine;
using UnityEngine.UI;

namespace BazaarSkinManager.TheBazaar
{
    internal sealed class HeroSelectStandingState : MonoBehaviour
    {
        private const string DisplayTypeName = "HeroSelectDisplay";

        private static Type _displayType;
        private static FieldInfo _makGraphicField;
        private static FieldInfo _loadedAssetField;
        private static FieldInfo _selectedHeroField;
        private static FieldInfo _skinEditActiveSkinField;

        private float _nextScan;

        private void Update()
        {
            if (Time.unscaledTime < _nextScan || Plugin.ActivePack == null)
            {
                return;
            }

            _nextScan = Time.unscaledTime + 0.5f;
            ReconcileAll();
        }

        private void OnDestroy()
        {
            RemoveAll();
        }

        public static void Reconcile(object display)
        {
            if (!EnsureBindings() || display == null)
            {
                return;
            }

            Component component = display as Component;
            Graphic makGraphic = _makGraphicField.GetValue(display) as Graphic;
            object loadedAsset = _loadedAssetField.GetValue(display);
            object selectedHero = _selectedHeroField.GetValue(display);
            GameObject skinEditActiveSkin =
                _skinEditActiveSkinField.GetValue(display) as GameObject;
            bool displayActive = component != null &&
                component.gameObject.activeInHierarchy;
            bool supportedCentralDisplay =
                IsSupportedCentralDisplay(component);
            bool makGraphicActive = makGraphic != null &&
                makGraphic.gameObject.activeInHierarchy;
            bool skinEditActive = skinEditActiveSkin != null &&
                skinEditActiveSkin.activeInHierarchy;
            string selectedName = selectedHero == null
                ? "<null>"
                : selectedHero.ToString();
            bool selectedMak = string.Equals(
                selectedName,
                "Mak",
                StringComparison.OrdinalIgnoreCase);
            bool selectedCommon = string.Equals(
                selectedName,
                "Common",
                StringComparison.OrdinalIgnoreCase);
            bool selectedAllowsMak = selectedMak || selectedCommon;
            bool isTargetDefault = SkinPatchTargets.ShouldReplace(loadedAsset);
            bool shouldAttachGraphic = displayActive &&
                supportedCentralDisplay &&
                makGraphicActive &&
                selectedAllowsMak &&
                isTargetDefault;
            bool shouldAttachSkinEdit = displayActive &&
                supportedCentralDisplay &&
                skinEditActive &&
                selectedAllowsMak &&
                isTargetDefault;
            bool shouldAttach = shouldAttachGraphic ||
                shouldAttachSkinEdit;
            UnityEngine.Object loadedUnity = loadedAsset as UnityEngine.Object;

            RuntimeDiagnostics.ReportCentralState(
                displayActive,
                makGraphicActive,
                skinEditActive,
                selectedName,
                loadedUnity == null ? "<null>" : loadedUnity.name,
                isTargetDefault,
                shouldAttach);

            // This reconciler owns only the main HeroSelect placeholder.
            // PvpScreen, EndOfDayScreen and the other SkinEdit placements
            // share HeroSelectDisplay, but their overlays are owned by the
            // exact placement patches. Removing a world overlay here when
            // one of those displays becomes inactive restores the native
            // Mak renderer immediately before the versus screen is shown.
            if (!supportedCentralDisplay)
            {
                RuntimeSkinAudit.RecordLoader(
                    "HeroSelectDisplay.MAK",
                    "unsupported type",
                    "Non-central HeroSelectDisplay is owned by its exact " +
                        "SkinEdit placement patch; no overlay state changed.",
                    skinEditActiveSkin == null
                        ? (UnityEngine.Object)makGraphic
                        : skinEditActiveSkin);
                return;
            }

            if (!shouldAttach)
            {
                StandingOverlay.RemoveFromGraphic(makGraphic);
                StandingOverlay.RemoveFromWorld(skinEditActiveSkin);
                string state = !displayActive ||
                    (!makGraphicActive && !skinEditActive) ||
                    loadedAsset == null
                    ? "not loaded"
                    : !selectedAllowsMak || !isTargetDefault
                        ? "wrong hero/skin"
                        : "unsupported type";
                RuntimeSkinAudit.RecordLoader(
                    "HeroSelectDisplay.MAK",
                    state,
                    "displayActive=" + displayActive +
                    " supportedCentralDisplay=" +
                    supportedCentralDisplay +
                    " makGraphicActive=" + makGraphicActive +
                    " skinEditActive=" + skinEditActive +
                    " selected=" + selectedName +
                    " targetDefault=" + isTargetDefault,
                    skinEditActiveSkin == null
                        ? (UnityEngine.Object)makGraphic
                        : skinEditActiveSkin);
                return;
            }

            bool alreadyApplied;
            UnityEngine.Object auditTarget;
            Sprite overlay = Plugin.ActivePack.Sprite("standing_overlay");
            if (shouldAttachSkinEdit)
            {
                GameObject visualRoot =
                    FindVisibleSkinRendererRoot(skinEditActiveSkin);
                alreadyApplied =
                    StandingOverlay.HasWorldOverlay(skinEditActiveSkin);
                if (!alreadyApplied && visualRoot != null)
                {
                    StandingOverlay.AttachToWorld(
                        visualRoot,
                        overlay,
                        "standing_overlay",
                        "HeroSelectDisplay active SkinEdit placement " +
                        skinEditActiveSkin.name + "/_loadedAsset=" +
                        loadedUnity.name);
                }
                auditTarget = visualRoot == null
                    ? (UnityEngine.Object)skinEditActiveSkin
                    : visualRoot;
            }
            else
            {
                alreadyApplied =
                    StandingOverlay.HasGraphicOverlay(makGraphic);
                StandingOverlay.AttachToGraphic(
                    makGraphic,
                    overlay,
                    "HeroSelectDisplay active MAK/_selectedHero=" +
                    selectedName + "/_loadedAsset=" + loadedUnity.name);
                auditTarget = makGraphic;
            }
            RuntimeSkinAudit.RecordLoader(
                "HeroSelectDisplay.MAK",
                alreadyApplied ? "already applied" : "applied",
                "Active Mak visual with target default asset, selected=" +
                selectedName + ", mode=" +
                (shouldAttachSkinEdit ? "skin-edit-prefab" : "graphic") +
                ".",
                auditTarget);
        }

        public static bool TryRunDiagnostic(
            object target,
            out string detail)
        {
            detail = "HeroSelectDisplay binding is unavailable.";
            if (!EnsureBindings() || target == null)
            {
                return false;
            }

            foreach (UnityEngine.Object candidate in
                Resources.FindObjectsOfTypeAll(_displayType))
            {
                Component component = candidate as Component;
                Graphic graphic =
                    _makGraphicField.GetValue(candidate) as Graphic;
                if (component == null || graphic == null)
                {
                    continue;
                }
                if (!IsSupportedCentralDisplay(component))
                {
                    continue;
                }

                object originalLoaded =
                    _loadedAssetField.GetValue(candidate);
                object originalSelected =
                    _selectedHeroField.GetValue(candidate);
                bool displayActive =
                    component.gameObject.activeSelf;
                bool graphicActive = graphic.gameObject.activeSelf;
                bool originalEnabled;
                Color originalColor;
                bool hadOverlay =
                    StandingOverlay.TryGetGraphicOriginalState(
                        graphic,
                        out originalEnabled,
                        out originalColor);
                if (!hadOverlay)
                {
                    originalEnabled = graphic.enabled;
                    originalColor = graphic.color;
                }

                var activated = new List<GameObject>();
                try
                {
                    StandingOverlay.RemoveFromGraphic(graphic);
                    ActivateChain(component.transform, activated);
                    ActivateChain(graphic.transform, activated);
                    graphic.enabled = originalEnabled;
                    graphic.color = originalColor;

                    object common = Enum.Parse(
                        _selectedHeroField.FieldType,
                        "Common",
                        true);
                    _loadedAssetField.SetValue(candidate, target);
                    _selectedHeroField.SetValue(candidate, common);

                    Reconcile(candidate);
                    bool applied =
                        StandingOverlay.HasGraphicOverlay(graphic);
                    Reconcile(candidate);
                    bool alreadyApplied =
                        StandingOverlay.HasGraphicOverlay(graphic);

                    _loadedAssetField.SetValue(candidate, null);
                    Reconcile(candidate);
                    bool restored =
                        graphic.enabled == originalEnabled &&
                        graphic.color == originalColor;

                    detail =
                        "targetApplied=" + applied +
                        " targetAlreadyApplied=" + alreadyApplied +
                        " nonTargetStateRestored=" + restored +
                        " overlayDestroyQueued=" +
                        StandingOverlay.HasGraphicOverlay(graphic) +
                        " display=" + component.gameObject.name +
                        " graphic=" + graphic.gameObject.name;
                    return applied && alreadyApplied && restored;
                }
                catch (Exception exception)
                {
                    detail = exception.GetType().FullName +
                        ": " + exception.Message;
                    return false;
                }
                finally
                {
                    StandingOverlay.RemoveFromGraphic(graphic);
                    _loadedAssetField.SetValue(
                        candidate,
                        originalLoaded);
                    _selectedHeroField.SetValue(
                        candidate,
                        originalSelected);
                    graphic.enabled = originalEnabled;
                    graphic.color = originalColor;
                    for (int index = activated.Count - 1;
                        index >= 0;
                        index--)
                    {
                        activated[index].SetActive(false);
                    }
                    component.gameObject.SetActive(displayActive);
                    graphic.gameObject.SetActive(graphicActive);
                    if (hadOverlay)
                    {
                        Reconcile(candidate);
                    }
                }
            }

            detail = "No reachable HeroSelectDisplay/MAK graphic pair exists.";
            return false;
        }

        private static void ActivateChain(
            Transform transform,
            List<GameObject> activated)
        {
            Transform current = transform;
            while (current != null)
            {
                if (!current.gameObject.activeSelf)
                {
                    current.gameObject.SetActive(true);
                    activated.Add(current.gameObject);
                }
                current = current.parent;
            }
        }

        private static void ReconcileAll()
        {
            if (!EnsureBindings())
            {
                RuntimeDiagnostics.ReportLoaderState(
                    "HeroSelectDisplay.MAK",
                    "unsupported type",
                    "MAK/_loadedAsset/_selectedHero binding is unavailable.");
                return;
            }

            foreach (UnityEngine.Object display in
                Resources.FindObjectsOfTypeAll(_displayType))
            {
                Reconcile(display);
            }
        }

        private static void RemoveAll()
        {
            if (!EnsureBindings())
            {
                return;
            }

            foreach (UnityEngine.Object display in
                Resources.FindObjectsOfTypeAll(_displayType))
            {
                Graphic graphic = _makGraphicField.GetValue(display) as Graphic;
                StandingOverlay.RemoveFromGraphic(graphic);
                GameObject skinEditActiveSkin =
                    _skinEditActiveSkinField.GetValue(display) as GameObject;
                StandingOverlay.RemoveFromWorld(skinEditActiveSkin);
            }
        }

        private static GameObject FindVisibleSkinRendererRoot(GameObject root)
        {
            if (root == null)
            {
                return null;
            }

            Renderer renderer = root
                .GetComponentsInChildren<Renderer>(true)
                .Where(candidate => candidate != null &&
                    candidate.enabled &&
                    candidate.gameObject.activeInHierarchy)
                .OrderBy(candidate => candidate.bounds.size.y)
                .LastOrDefault();
            return renderer == null ? null : renderer.gameObject;
        }

        private static bool IsSupportedCentralDisplay(Component component)
        {
            if (component == null ||
                !string.Equals(
                    component.gameObject.name,
                    "Hero_Placeholder",
                    StringComparison.Ordinal))
            {
                return false;
            }

            Transform current = component.transform.parent;
            while (current != null)
            {
                if (string.Equals(
                    current.gameObject.name,
                    "HeroSelect",
                    StringComparison.Ordinal))
                {
                    return true;
                }
                current = current.parent;
            }
            return false;
        }

        private static bool EnsureBindings()
        {
            if (_displayType != null)
            {
                return _makGraphicField != null &&
                    _loadedAssetField != null &&
                    _selectedHeroField != null &&
                    _skinEditActiveSkinField != null;
            }

            _displayType = AccessTools.TypeByName(DisplayTypeName);
            if (_displayType == null)
            {
                return false;
            }

            _makGraphicField = AccessTools.Field(_displayType, "MAK");
            _loadedAssetField = AccessTools.Field(_displayType, "_loadedAsset");
            _selectedHeroField =
                AccessTools.Field(_displayType, "_selectedHero");
            _skinEditActiveSkinField =
                AccessTools.Field(_displayType, "_skinEditActiveSkin");
            return _makGraphicField != null &&
                _loadedAssetField != null &&
                _selectedHeroField != null &&
                _skinEditActiveSkinField != null;
        }
    }
}
