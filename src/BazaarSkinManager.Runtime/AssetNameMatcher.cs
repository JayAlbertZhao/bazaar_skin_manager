using System;

namespace BazaarSkinManager.TheBazaar
{
    internal static class AssetNameMatcher
    {
        public const string ContainsMode = "contains";
        public const string ExactMode = "exact";

        public static bool IsValidMode(string matchMode)
        {
            return string.IsNullOrEmpty(matchMode) ||
                string.Equals(
                    matchMode,
                    ContainsMode,
                    StringComparison.OrdinalIgnoreCase) ||
                string.Equals(
                    matchMode,
                    ExactMode,
                    StringComparison.OrdinalIgnoreCase);
        }

        public static bool Matches(
            string matchMode,
            string assetName,
            string needle)
        {
            if (string.IsNullOrEmpty(assetName) || string.IsNullOrEmpty(needle))
            {
                return false;
            }

            if (string.Equals(
                matchMode,
                ExactMode,
                StringComparison.OrdinalIgnoreCase))
            {
                return string.Equals(
                    assetName,
                    needle,
                    StringComparison.OrdinalIgnoreCase);
            }

            return assetName.IndexOf(
                needle,
                StringComparison.OrdinalIgnoreCase) >= 0;
        }
    }
}
