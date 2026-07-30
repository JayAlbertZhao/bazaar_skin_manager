from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "dist" / "runtime" / "BazaarSkinManager.Runtime.dll"
CSC = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")


class RuntimeBehaviorTests(unittest.TestCase):
    def test_value_type_mutation_is_returned_from_compiled_runtime(self) -> None:
        self.assertTrue(RUNTIME.is_file(), "run build.ps1 before the test suite")
        self.assertTrue(CSC.is_file())
        source = textwrap.dedent(
            r"""
            using System;
            using System.Reflection;

            public struct InspectorData
            {
                public object HeroSkinBackgroundImage;
            }

            public static class Program
            {
                public static int Main(string[] args)
                {
                    Assembly runtime = Assembly.LoadFrom(args[0]);
                    Type helper = runtime.GetType(
                        "BazaarSkinManager.TheBazaar.ValueTypeResultMutation",
                        true);
                    MethodInfo rebox = helper.GetMethod(
                        "Rebox",
                        BindingFlags.Public | BindingFlags.Static);

                    object expected = new object();
                    object boxed = new InspectorData();
                    boxed.GetType().GetField("HeroSkinBackgroundImage")
                        .SetValue(boxed, expected);
                    InspectorData returned = (InspectorData)rebox
                        .MakeGenericMethod(typeof(InspectorData))
                        .Invoke(null, new object[] { boxed });
                    return Object.ReferenceEquals(
                        returned.HeroSkinBackgroundImage,
                        expected) ? 0 : 9;
                }
            }
            """
        )
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            source_path = temp_path / "ReboxHarness.cs"
            executable = temp_path / "ReboxHarness.exe"
            source_path.write_text(source, encoding="utf-8")
            compile_result = subprocess.run(
                [
                    str(CSC),
                    "/nologo",
                    f"/out:{executable}",
                    str(source_path),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                compile_result.returncode,
                0,
                compile_result.stdout + compile_result.stderr,
            )
            run_result = subprocess.run(
                [str(executable), str(RUNTIME)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                run_result.returncode,
                0,
                run_result.stdout + run_result.stderr,
            )

    def test_compiled_exact_match_accepts_portrait_and_rejects_background(
        self,
    ) -> None:
        self.assertTrue(RUNTIME.is_file(), "run build.ps1 before the test suite")
        source = textwrap.dedent(
            r"""
            using System;
            using System.Reflection;

            public static class Program
            {
                public static int Main(string[] args)
                {
                    Assembly runtime = Assembly.LoadFrom(args[0]);
                    Type matcher = runtime.GetType(
                        "BazaarSkinManager.TheBazaar.AssetNameMatcher",
                        true);
                    MethodInfo matches = matcher.GetMethod(
                        "Matches",
                        BindingFlags.Public | BindingFlags.Static);

                    object[] positive = {
                        "exact",
                        "Skin_MAK_01a_Portrait",
                        "Skin_MAK_01a_Portrait"
                    };
                    object[] negative = {
                        "exact",
                        "Skin_MAK_01a_PortraitBG",
                        "Skin_MAK_01a_Portrait"
                    };
                    object[] fuzzy = {
                        "contains",
                        "prefix_Skin_MAK_01a_StoreImage_suffix",
                        "Skin_MAK_01a_StoreImage"
                    };
                    bool acceptsPortrait = (bool)matches.Invoke(null, positive);
                    bool rejectsBackground = !(bool)matches.Invoke(null, negative);
                    bool preservesFuzzy = (bool)matches.Invoke(null, fuzzy);
                    return acceptsPortrait && rejectsBackground && preservesFuzzy
                        ? 0
                        : 11;
                }
            }
            """
        )
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            source_path = temp_path / "ExactMatchHarness.cs"
            executable = temp_path / "ExactMatchHarness.exe"
            source_path.write_text(source, encoding="utf-8")
            compile_result = subprocess.run(
                [
                    str(CSC),
                    "/nologo",
                    f"/out:{executable}",
                    str(source_path),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                compile_result.returncode,
                0,
                compile_result.stdout + compile_result.stderr,
            )
            run_result = subprocess.run(
                [str(executable), str(RUNTIME)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                run_result.returncode,
                0,
                run_result.stdout + run_result.stderr,
            )

    def test_compiled_runtime_compatibility_rejects_unknown_game_bytes(
        self,
    ) -> None:
        self.assertTrue(RUNTIME.is_file(), "run build.ps1 before the test suite")
        source = textwrap.dedent(
            r"""
            using System;
            using System.Reflection;

            public static class Program
            {
                public static int Main(string[] args)
                {
                    Assembly runtime = Assembly.LoadFrom(args[0]);
                    Type compatibility = runtime.GetType(
                        "BazaarSkinManager.TheBazaar.RuntimeCompatibility",
                        true);
                    MethodInfo validate = compatibility.GetMethod(
                        "Validate",
                        BindingFlags.Public | BindingFlags.Static);
                    object[] parameters = {
                        args[1],
                        args[2],
                        null
                    };
                    bool actual = (bool)validate.Invoke(null, parameters);
                    bool expected = Boolean.Parse(args[3]);
                    Console.WriteLine(parameters[2] ?? "compatible");
                    return actual == expected ? 0 : 13;
                }
            }
            """
        )
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            steamapps = temp_path / "steamapps"
            game = steamapps / "common" / "The Bazaar"
            game.mkdir(parents=True)
            steam_manifest = steamapps / "appmanifest_1617400.acf"
            steam_manifest.write_text(
                '"AppState" { "buildid" "24001960" }',
                encoding="utf-8",
            )
            executable_path = game / "TheBazaar.exe"
            executable_path.write_bytes(b"authorized-game")
            record_path = temp_path / "runtime-compatibility.json"
            record = {
                "schema_version": 1,
                "app_id": "1617400",
                "game_dir": str(game.resolve()),
                "build_id": "24001960",
                "files": [
                    {
                        "path": "TheBazaar.exe",
                        "sha256": hashlib.sha256(
                            executable_path.read_bytes()
                        ).hexdigest(),
                    }
                ],
            }
            record_path.write_text(
                json.dumps(record),
                encoding="utf-8",
            )

            source_path = temp_path / "CompatibilityHarness.cs"
            executable = temp_path / "CompatibilityHarness.exe"
            source_path.write_text(source, encoding="utf-8")
            compile_result = subprocess.run(
                [
                    str(CSC),
                    "/nologo",
                    f"/out:{executable}",
                    str(source_path),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                compile_result.returncode,
                0,
                compile_result.stdout + compile_result.stderr,
            )

            valid = subprocess.run(
                [
                    str(executable),
                    str(RUNTIME),
                    str(record_path),
                    str(game),
                    "true",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)

            steam_manifest.write_text(
                '"AppState" { "buildid" "24001961" }',
                encoding="utf-8",
            )
            build_mismatch = subprocess.run(
                [
                    str(executable),
                    str(RUNTIME),
                    str(record_path),
                    str(game),
                    "false",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(
                build_mismatch.returncode,
                0,
                build_mismatch.stdout + build_mismatch.stderr,
            )
            self.assertIn("build ID changed", build_mismatch.stdout)
            steam_manifest.write_text(
                '"AppState" { "buildid" "24001960" }',
                encoding="utf-8",
            )

            executable_path.write_bytes(b"unknown-steam-update")
            mismatch = subprocess.run(
                [
                    str(executable),
                    str(RUNTIME),
                    str(record_path),
                    str(game),
                    "false",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(
                mismatch.returncode,
                0,
                mismatch.stdout + mismatch.stderr,
            )
            self.assertIn("fingerprint changed", mismatch.stdout)

            record_path.write_text("{", encoding="utf-8")
            malformed = subprocess.run(
                [
                    str(executable),
                    str(RUNTIME),
                    str(record_path),
                    str(game),
                    "false",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(
                malformed.returncode,
                0,
                malformed.stdout + malformed.stderr,
            )
            self.assertIn("validation failed", malformed.stdout)

            missing = subprocess.run(
                [
                    str(executable),
                    str(RUNTIME),
                    str(temp_path / "missing.json"),
                    str(game),
                    "false",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.assertEqual(
                missing.returncode,
                0,
                missing.stdout + missing.stderr,
            )
            self.assertIn("record is missing", missing.stdout)


if __name__ == "__main__":
    unittest.main()
