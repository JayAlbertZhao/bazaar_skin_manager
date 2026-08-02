using System;
using System.Reflection;
using HarmonyLib;
using UnityEngine;
using UnityEngine.UI;

namespace BazaarSkinManager.TheBazaar
{
    internal sealed class HeroSelectIconReconciler : MonoBehaviour
    {
        private static Type _heroItemType;
        private static FieldInfo _heroSoField;
        private static FieldInfo _heroIdField;

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

        public static void Reconcile(object candidate)
        {
            if (!EnsureBindings() || candidate == null ||
                Plugin.ActivePack == null)
            {
                return;
            }

            Sprite replacement = Plugin.ActivePack.Sprite("hero_select");
            if (replacement == null ||
                Plugin.ActivePack.UsesPreloadedDeployment("hero_select"))
            {
                return;
            }

            object heroSo = _heroSoField.GetValue(candidate);
            if (heroSo == null)
            {
                RuntimeSkinAudit.RecordLoader(
                    "HeroItemView.Content.Icon",
                    "not loaded",
                    "HeroSO is null.",
                    null);
                return;
            }

            _heroIdField = _heroIdField ??
                AccessTools.Field(heroSo.GetType(), "HeroID");
            object heroId = _heroIdField == null
                ? null
                : _heroIdField.GetValue(heroSo);
            if (heroId == null ||
                !Plugin.ActivePack.IsTargetHero(heroId.ToString()))
            {
                return;
            }

            Component component = candidate as Component;
            Transform iconTransform = component == null
                ? null
                : component.transform.Find("Content/Icon");
            Image icon = iconTransform == null
                ? null
                : iconTransform.GetComponent<Image>();
            if (icon == null || replacement == null)
            {
                RuntimeSkinAudit.RecordLoader(
                    "HeroItemView.Content.Icon",
                    "not loaded",
                    icon == null
                        ? "Exact target-hero Content/Icon Image is not loaded."
                        : "hero_select pack sprite is unavailable.",
                    icon);
                return;
            }

            RuntimeAssetProbe.CaptureHeroButton(
                heroId.ToString(),
                icon.sprite,
                component.transform,
                icon.rectTransform);

            if (icon.sprite == replacement ||
                (icon.sprite != null &&
                 icon.sprite.name == replacement.name))
            {
                RuntimeSkinAudit.RecordLoader(
                    "HeroItemView.Content.Icon",
                    "already applied",
                    HierarchyName(iconTransform),
                    icon);
                return;
            }

            icon.sprite = replacement;
            RuntimeDiagnostics.ReportReplacement(
                "hero_select",
                "Target-hero direct reconcile -> " +
                HierarchyName(iconTransform));
            RuntimeSkinAudit.RecordLoader(
                "HeroItemView.Content.Icon",
                "applied",
                HierarchyName(iconTransform),
                icon);
        }

        private static void ReconcileAll()
        {
            if (!EnsureBindings())
            {
                RuntimeDiagnostics.ReportLoaderState(
                    "HeroItemView.Content.Icon",
                    "unsupported type",
                    "HeroItemView/HeroSO/HeroID binding is unavailable.");
                return;
            }

            foreach (UnityEngine.Object candidate in
                Resources.FindObjectsOfTypeAll(_heroItemType))
            {
                Reconcile(candidate);
            }
        }

        private static bool EnsureBindings()
        {
            if (_heroItemType != null)
            {
                return _heroSoField != null;
            }

            _heroItemType = AccessTools.TypeByName("HeroItemView");
            _heroSoField = _heroItemType == null
                ? null
                : AccessTools.Field(_heroItemType, "HeroSO");
            Type heroSoType = AccessTools.TypeByName("TheBazaar.HeroSO");
            _heroIdField = heroSoType == null
                ? null
                : AccessTools.Field(heroSoType, "HeroID");
            return _heroSoField != null;
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
