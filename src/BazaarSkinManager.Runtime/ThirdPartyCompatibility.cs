using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using HarmonyLib;

namespace BazaarSkinManager.TheBazaar
{
    internal static class ThirdPartyCompatibility
    {
        private const string BazaarPlusPlusOwner = "BazaarPlusPlus";
        private const string BrokenPatchNamespace =
            "BazaarPlusPlus.Patches.Lobby.RandomHeroSkinPool";

        internal static int ProtectNativeWardrobe(Harmony harmony)
        {
            Type cosmeticItem = AccessTools.TypeByName("TheBazaar.CosmeticItem");
            if (cosmeticItem == null)
            {
                return 0;
            }

            // The current game changed EquipableItem from a public property to
            // a private field. Older BazaarPlusPlus random-skin-pool patches
            // still reference get_EquipableItem and make FetchCosmetics throw,
            // which prevents the game's native wardrobe from opening.
            if (AccessTools.PropertyGetter(cosmeticItem, "EquipableItem") != null)
            {
                return 0;
            }
            if (AccessTools.Field(cosmeticItem, "_equipableItem") == null)
            {
                return 0;
            }

            int removed = 0;
            MethodBase[] originals = Harmony.GetAllPatchedMethods().ToArray();
            foreach (MethodBase original in originals)
            {
                Patches patches = Harmony.GetPatchInfo(original);
                if (patches == null)
                {
                    continue;
                }

                removed += RemoveBrokenPatches(harmony, original, patches.Prefixes);
                removed += RemoveBrokenPatches(harmony, original, patches.Postfixes);
                removed += RemoveBrokenPatches(harmony, original, patches.Transpilers);
                removed += RemoveBrokenPatches(harmony, original, patches.Finalizers);
            }
            return removed;
        }

        private static int RemoveBrokenPatches(
            Harmony harmony,
            MethodBase original,
            IEnumerable<Patch> patches)
        {
            int removed = 0;
            foreach (Patch patch in patches.ToArray())
            {
                MethodInfo method = patch.PatchMethod;
                string declaringType = method == null || method.DeclaringType == null
                    ? string.Empty
                    : method.DeclaringType.FullName;
                if (!string.Equals(
                        patch.owner,
                        BazaarPlusPlusOwner,
                        StringComparison.Ordinal) ||
                    !declaringType.StartsWith(
                        BrokenPatchNamespace,
                        StringComparison.Ordinal))
                {
                    continue;
                }

                harmony.Unpatch(original, method);
                removed++;
            }
            return removed;
        }
    }
}
