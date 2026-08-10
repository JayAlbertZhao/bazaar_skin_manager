from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "validate_audio_manifest.py"
SPEC = importlib.util.spec_from_file_location(
    "validate_audio_manifest",
    MODULE_PATH,
)
validator = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = validator
SPEC.loader.exec_module(validator)

INVENTORY_PATH = (
    ROOT
    / "manager"
    / "adapters"
    / "mak-default.json"
)
EXAMPLE_PATH = ROOT / "docs" / "audio-ugc-manifest.example.json"
INVENTORY = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))
EXAMPLE = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))


def codes(result: dict[str, object]) -> set[str]:
    return {
        item["code"]
        for item in result["issues"]  # type: ignore[index]
    }


def exact_enabled_manifest(pack_root: Path) -> dict[str, object]:
    manifest = copy.deepcopy(EXAMPLE)
    manifest["enabled"] = True
    audio = pack_root / "audio"
    audio.mkdir(parents=True)
    for index, slot in enumerate(manifest["logical_slots"]):
        relative = f"audio/{index:02d}-{slot['logical_slot']}.wav"
        payload = f"logical-slot-{index}".encode("utf-8")
        target = pack_root / Path(*relative.split("/"))
        target.write_bytes(payload)
        slot["variants"] = [
            {
                "file": relative,
                "sha256": hashlib.sha256(payload).hexdigest(),
                "weight": 1,
            }
        ]
    return manifest


class AudioManifestSemanticValidatorTests(unittest.TestCase):
    def validate(
        self,
        manifest: dict[str, object],
        root: Path,
    ) -> dict[str, object]:
        return validator.validate_manifest(manifest, INVENTORY, root)

    def test_valid_exact_17_slot_enabled_manifest_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = exact_enabled_manifest(root)
            result = self.validate(manifest, root)
        self.assertTrue(result["valid"], result["issues"])
        self.assertEqual(result["checked_slots"], 17)
        self.assertEqual(result["checked_files"], 17)
        self.assertEqual(result["inventory_exact_slot_count"], 17)

    def test_seventeen_duplicate_idle_slots_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = exact_enabled_manifest(root)
            idle = copy.deepcopy(manifest["logical_slots"][0])
            manifest["logical_slots"] = [
                copy.deepcopy(idle) for _ in range(17)
            ]
            for index, slot in enumerate(manifest["logical_slots"]):
                slot["variants"][0]["file"] = f"audio/duplicate-{index}.wav"
            result = self.validate(manifest, root)
        self.assertFalse(result["valid"])
        self.assertTrue(
            {
                "duplicate_logical_name",
                "duplicate_logical_identity",
                "incomplete_exact_set",
            }.issubset(codes(result))
        )

    def test_all_zero_guid_and_fake_selector_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = exact_enabled_manifest(root)
            target = manifest["logical_slots"][8]
            target["event_guid"] = "00000000-0000-0000-0000-000000000000"
            target["selectors"] = [
                {"parameter": "FAKE", "label": "Anything"}
            ]
            result = self.validate(manifest, root)
        self.assertFalse(result["valid"])
        self.assertIn("identity_mismatch_event_guid", codes(result))
        self.assertIn("identity_mismatch_selectors", codes(result))

    def test_mismatched_ordered_tuple_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = exact_enabled_manifest(root)
            target = manifest["logical_slots"][8]
            target["selectors"] = [
                {"parameter": "VO_Hero_PvPIntro_Pan", "label": "Right"}
            ]
            result = self.validate(manifest, root)
        self.assertFalse(result["valid"])
        self.assertIn("identity_mismatch_selectors", codes(result))
        self.assertIn("duplicate_logical_identity", codes(result))

    def test_incomplete_complete_hero_exact_set_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = exact_enabled_manifest(root)
            removed = manifest["logical_slots"].pop()
            result = self.validate(manifest, root)
        self.assertFalse(result["valid"])
        self.assertIn("incomplete_exact_set", codes(result))
        exact_issue = next(
            item
            for item in result["issues"]
            if item["code"] == "incomplete_exact_set"
        )
        self.assertIn(removed["logical_slot"], exact_issue["message"])

    def test_unsafe_and_duplicate_normalized_paths_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = exact_enabled_manifest(root)
            manifest["logical_slots"][0]["variants"][0][
                "file"
            ] = "audio/../escape.wav"
            manifest["logical_slots"][1]["variants"][0][
                "file"
            ] = "audio/VOICE.wav"
            manifest["logical_slots"][2]["variants"][0][
                "file"
            ] = "audio/voice.wav"
            result = self.validate(manifest, root)
        self.assertFalse(result["valid"])
        self.assertIn("unsafe_variant_path", codes(result))
        self.assertIn("duplicate_variant_path", codes(result))

    def test_missing_file_and_sha_mismatch_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = exact_enabled_manifest(root)
            missing = manifest["logical_slots"][0]["variants"][0]
            (root / Path(*missing["file"].split("/"))).unlink()
            manifest["logical_slots"][1]["variants"][0][
                "sha256"
            ] = "f" * 64
            result = self.validate(manifest, root)
        self.assertFalse(result["valid"])
        self.assertIn("missing_variant_file", codes(result))
        self.assertIn("variant_sha256_mismatch", codes(result))

    def test_cli_accepts_disabled_identity_example_without_assets(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(MODULE_PATH),
                "--manifest",
                str(EXAMPLE_PATH),
                "--inventory",
                str(INVENTORY_PATH),
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout)
        result = json.loads(completed.stdout)
        self.assertTrue(result["valid"])
        self.assertEqual(result["checked_slots"], 17)
        self.assertEqual(result["checked_files"], 0)
        self.assertEqual(
            result["warnings"][0]["code"],
            "disabled_files_not_checked",
        )

    def test_jules_inventory_reference_validates_exact_hero_routes(self) -> None:
        inventory_path = ROOT / "manager" / "adapters" / "jules-default.json"
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        resolved = validator.resolve_inventory_audio_template(inventory)
        expected, problems = validator.inventory_slots(resolved)
        self.assertEqual(problems, [])
        self.assertEqual(len(expected), 17)
        manifest = copy.deepcopy(EXAMPLE)
        manifest["target"]["hero"] = "Jules"
        manifest["target"]["steam_build"] = "24570932"
        manifest["logical_slots"] = [
            {
                **copy.deepcopy(slot),
                "category": "hero_voice",
                "variants": [],
            }
            for slot in expected.values()
        ]
        with tempfile.TemporaryDirectory() as directory:
            result = validator.validate_manifest(
                manifest,
                inventory,
                Path(directory),
            )
        self.assertTrue(result["valid"], result["issues"])


if __name__ == "__main__":
    unittest.main()
