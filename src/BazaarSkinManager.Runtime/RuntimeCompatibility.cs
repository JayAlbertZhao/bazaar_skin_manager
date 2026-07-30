using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.Serialization;
using System.Runtime.Serialization.Json;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;

namespace BazaarSkinManager.TheBazaar
{
    [DataContract]
    internal sealed class RuntimeCompatibilityRecord
    {
        [DataMember(Name = "schema_version")]
        public int SchemaVersion;

        [DataMember(Name = "app_id")]
        public string AppId;

        [DataMember(Name = "game_dir")]
        public string GameDirectory;

        [DataMember(Name = "build_id")]
        public string BuildId;

        [DataMember(Name = "files")]
        public List<RuntimeCompatibilityFile> Files;
    }

    [DataContract]
    internal sealed class RuntimeCompatibilityFile
    {
        [DataMember(Name = "path")]
        public string Path;

        [DataMember(Name = "sha256")]
        public string Sha256;
    }

    internal static class RuntimeCompatibility
    {
        private const string AppId = "1617400";
        private const string ExecutableRelativePath = "TheBazaar.exe";

        public static string DefaultRecordPath()
        {
            return Path.Combine(
                Environment.GetFolderPath(
                    Environment.SpecialFolder.LocalApplicationData),
                "BazaarSkinManager",
                "TheBazaar",
                "manager",
                "runtime-compatibility.json");
        }

        public static bool ValidateCurrent(out string failure)
        {
            string executable;
            try
            {
                executable = Process.GetCurrentProcess().MainModule.FileName;
            }
            catch (Exception exception)
            {
                failure = "Cannot resolve current executable: " +
                    exception.Message;
                return false;
            }

            return Validate(
                DefaultRecordPath(),
                Path.GetDirectoryName(Path.GetFullPath(executable)),
                out failure);
        }

        public static bool Validate(
            string recordPath,
            string gameDirectory,
            out string failure)
        {
            try
            {
                if (string.IsNullOrEmpty(recordPath) ||
                    !File.Exists(recordPath))
                {
                    failure = "Runtime compatibility record is missing.";
                    return false;
                }

                RuntimeCompatibilityRecord record;
                var serializer = new DataContractJsonSerializer(
                    typeof(RuntimeCompatibilityRecord));
                using (FileStream stream = File.OpenRead(recordPath))
                {
                    record = (RuntimeCompatibilityRecord)
                        serializer.ReadObject(stream);
                }
                if (record == null ||
                    record.SchemaVersion != 1 ||
                    !string.Equals(
                        record.AppId,
                        AppId,
                        StringComparison.Ordinal))
                {
                    failure =
                        "Runtime compatibility record schema/app is invalid.";
                    return false;
                }

                string currentGameDirectory = NormalizeDirectory(gameDirectory);
                if (!string.Equals(
                    NormalizeDirectory(record.GameDirectory),
                    currentGameDirectory,
                    StringComparison.OrdinalIgnoreCase))
                {
                    failure =
                        "Runtime compatibility game directory does not match.";
                    return false;
                }

                if (!ValidateBuild(
                    currentGameDirectory,
                    record.BuildId,
                    out failure))
                {
                    return false;
                }

                if (record.Files == null || record.Files.Count == 0)
                {
                    failure =
                        "Runtime compatibility file fingerprints are missing.";
                    return false;
                }

                string safePrefix = currentGameDirectory.TrimEnd(
                    Path.DirectorySeparatorChar,
                    Path.AltDirectorySeparatorChar) +
                    Path.DirectorySeparatorChar;
                var recorded = new Dictionary<string, string>(
                    StringComparer.OrdinalIgnoreCase);
                foreach (RuntimeCompatibilityFile item in record.Files)
                {
                    if (item == null ||
                        string.IsNullOrEmpty(item.Path) ||
                        string.IsNullOrEmpty(item.Sha256))
                    {
                        failure =
                            "Runtime compatibility file entry is malformed.";
                        return false;
                    }

                    string normalizedRelative = item.Path.Replace(
                        Path.AltDirectorySeparatorChar,
                        Path.DirectorySeparatorChar);
                    string fullPath = Path.GetFullPath(
                        Path.Combine(
                            currentGameDirectory,
                            normalizedRelative));
                    if (!fullPath.StartsWith(
                        safePrefix,
                        StringComparison.OrdinalIgnoreCase))
                    {
                        failure =
                            "Runtime compatibility file escapes the game.";
                        return false;
                    }
                    if (recorded.ContainsKey(normalizedRelative))
                    {
                        failure =
                            "Runtime compatibility file entry is duplicated.";
                        return false;
                    }
                    recorded[normalizedRelative] = item.Sha256;

                    if (!File.Exists(fullPath))
                    {
                        failure = "Authorized game file is missing: " +
                            normalizedRelative;
                        return false;
                    }
                    string actual = Sha256File(fullPath);
                    if (!string.Equals(
                        actual,
                        item.Sha256,
                        StringComparison.OrdinalIgnoreCase))
                    {
                        failure = "Game file fingerprint changed: " +
                            normalizedRelative;
                        return false;
                    }
                }

                foreach (string required in CurrentFingerprintInputs(
                    currentGameDirectory))
                {
                    if (!recorded.ContainsKey(required))
                    {
                        failure =
                            "Current game fingerprint input is unauthorized: " +
                            required;
                        return false;
                    }
                }

                if (!recorded.ContainsKey(ExecutableRelativePath))
                {
                    failure =
                        "TheBazaar.exe fingerprint is not authorized.";
                    return false;
                }

                failure = null;
                return true;
            }
            catch (Exception exception)
            {
                failure = "Runtime compatibility validation failed: " +
                    exception.Message;
                return false;
            }
        }

        private static IEnumerable<string> CurrentFingerprintInputs(
            string gameDirectory)
        {
            string[] candidates = {
                ExecutableRelativePath,
                Path.Combine(
                    "TheBazaar_Data",
                    "Managed",
                    "TheBazaarRuntime.dll"),
                Path.Combine(
                    "TheBazaar_Data",
                    "StreamingAssets",
                    "aa",
                    "catalog.hash"),
            };
            foreach (string relative in candidates)
            {
                if (File.Exists(Path.Combine(gameDirectory, relative)))
                {
                    yield return relative;
                }
            }
        }

        private static bool ValidateBuild(
            string gameDirectory,
            string expectedBuildId,
            out string failure)
        {
            if (string.IsNullOrEmpty(expectedBuildId))
            {
                failure = null;
                return true;
            }

            DirectoryInfo game = Directory.GetParent(gameDirectory);
            DirectoryInfo steamApps = game == null ? null : game.Parent;
            string manifest = steamApps == null
                ? null
                : Path.Combine(
                    steamApps.FullName,
                    "appmanifest_" + AppId + ".acf");
            if (string.IsNullOrEmpty(manifest) || !File.Exists(manifest))
            {
                failure =
                    "Steam appmanifest is missing for the authorized build.";
                return false;
            }

            Match match = Regex.Match(
                File.ReadAllText(manifest, Encoding.UTF8),
                "\"buildid\"\\s*\"([^\"]+)\"",
                RegexOptions.IgnoreCase);
            if (!match.Success ||
                !string.Equals(
                    expectedBuildId,
                    match.Groups[1].Value,
                    StringComparison.Ordinal))
            {
                failure = "Steam build ID changed from the authorized build.";
                return false;
            }

            failure = null;
            return true;
        }

        private static string NormalizeDirectory(string path)
        {
            if (string.IsNullOrEmpty(path))
            {
                return string.Empty;
            }
            return Path.GetFullPath(path).TrimEnd(
                Path.DirectorySeparatorChar,
                Path.AltDirectorySeparatorChar);
        }

        private static string Sha256File(string path)
        {
            using (SHA256 sha256 = SHA256.Create())
            using (FileStream stream = File.OpenRead(path))
            {
                byte[] digest = sha256.ComputeHash(stream);
                var builder = new StringBuilder(digest.Length * 2);
                foreach (byte value in digest)
                {
                    builder.Append(value.ToString("x2"));
                }
                return builder.ToString();
            }
        }
    }
}
