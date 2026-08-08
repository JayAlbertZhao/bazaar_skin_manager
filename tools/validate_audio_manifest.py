#!/usr/bin/env python3
"""Inventory-backed semantic validator for The Bazaar audio UGC.

JSON Schema owns structural shape. This validator owns cross-field identity,
exact-set coverage, normalized-path, file, and digest checks that JSON Schema
cannot express against a build-specific routing inventory.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import posixpath
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY = (
    ROOT
    / "manager"
    / "adapters"
    / "mak-default.json"
)
SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")
SUPPORTED_EXTENSIONS = {".wav", ".ogg"}
HOOK_TYPES = {
    "Idle.default": "Idle",
    "Last_Life.default": "LastLife",
    "MultiClick.default": "MultiClick",
    "No_Buy_Gold.default": "NoBuyGold",
    "No_Buy_Space.default": "NoBuySpace",
    "Level_Up.default": "LevelUp",
    "Upgrade.default": "Upgrade",
    "Hit_React.default": "HitReact",
    "PvP_Intro.Left": "PvP_Intro",
    "PvP_Intro.Right": "PvP_Intro",
    "PvP_VictoryDefeat.Defeat": "PvP_VictoryDefeat",
    "PvP_VictoryDefeat.Victory": "PvP_VictoryDefeat",
    "PvE_VictoryDefeat.Defeat": "PvE_VictoryDefeat",
    "PvE_VictoryDefeat.Victory": "PvE_VictoryDefeat",
    "Run_VictoryDefeat.Defeat": "Run_VictoryDefeat",
    "Run_VictoryDefeat.Perfect": "Run_VictoryDefeat",
    "Run_VictoryDefeat.Victory": "Run_VictoryDefeat",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def issue(code: str, message: str, location: str = "$") -> dict[str, str]:
    return {"code": code, "location": location, "message": message}


def selector_identity(value: Any) -> tuple[tuple[str, str], ...] | None:
    if not isinstance(value, list):
        return None
    result: list[tuple[str, str]] = []
    for selector in value:
        if not isinstance(selector, dict):
            return None
        parameter = selector.get("parameter")
        label = selector.get("label")
        if not isinstance(parameter, str) or not isinstance(label, str):
            return None
        result.append((parameter, label))
    return tuple(result)


def logical_identity(slot: dict[str, Any]) -> tuple[Any, ...]:
    return (
        slot.get("hook_type"),
        slot.get("event_guid"),
        slot.get("event_path"),
        selector_identity(slot.get("selectors")),
    )


def normalized_variant_path(raw: Any) -> tuple[str | None, str | None]:
    if not isinstance(raw, str) or not raw:
        return None, "variant path must be a non-empty string"
    if "\\" in raw:
        return None, "backslashes are not allowed in UGC paths"
    if PurePosixPath(raw).is_absolute() or PureWindowsPath(raw).drive:
        return None, "absolute or drive-qualified paths are not allowed"
    parts = raw.split("/")
    if (
        parts[0] != "audio"
        or any(part in {"", ".", ".."} for part in parts)
        or len(parts) < 2
    ):
        return None, "path must be canonical and contained under audio/"
    normalized = posixpath.normpath(raw)
    if normalized != raw:
        return None, "path is not in canonical normalized form"
    if PurePosixPath(normalized).suffix.lower() not in SUPPORTED_EXTENSIONS:
        return None, "only .wav and .ogg variants are supported"
    return normalized, None


def inventory_slots(
    inventory: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    problems: list[dict[str, str]] = []
    legacy = inventory.get("mak_logical_voice_slots")
    adapter_audio = inventory.get("audio_template")
    if isinstance(legacy, dict):
        values = legacy.get("slots")
        minimum = legacy.get("minimum_complete_slot_count")
    elif isinstance(adapter_audio, dict):
        routes = adapter_audio.get("routes")
        values = (
            [
                route
                for route in routes
                if isinstance(route, dict)
                and route.get("category") == "hero_voice"
            ]
            if isinstance(routes, list)
            else None
        )
        minimum = len(HOOK_TYPES)
    else:
        values = None
        minimum = None
    if not isinstance(values, list) or minimum != 17:
        return {}, [
            issue(
                "inventory_shape",
                "inventory must expose the build's 17 hero voice routes",
            )
        ]

    result: dict[str, dict[str, Any]] = {}
    identities: set[tuple[Any, ...]] = set()
    for index, value in enumerate(values):
        location = f"inventory.slots[{index}]"
        if not isinstance(value, dict):
            problems.append(
                issue("inventory_shape", "slot must be an object", location)
            )
            continue
        name = value.get("logical_slot")
        if not isinstance(name, str) or not name:
            problems.append(
                issue("inventory_shape", "slot name is missing", location)
            )
            continue
        if name in result:
            problems.append(
                issue(
                    "inventory_duplicate_name",
                    f"duplicate inventory slot name: {name}",
                    location,
                )
            )
        expected = {
            "logical_slot": name,
            "hook_type": (
                value.get("manifest_hook_type")
                or value.get("hook_type")
                or HOOK_TYPES.get(name)
            ),
            "event_guid": value.get("event_guid"),
            "event_path": value.get("event_path"),
            "selectors": copy.deepcopy(value.get("selectors")),
        }
        identity = logical_identity(expected)
        if identity in identities:
            problems.append(
                issue(
                    "inventory_duplicate_identity",
                    f"duplicate inventory logical identity: {name}",
                    location,
                )
            )
        identities.add(identity)
        result[name] = expected
    if len(result) != minimum:
        problems.append(
            issue(
                "inventory_exact_set",
                f"inventory has {len(result)} unique slots, expected {minimum}",
            )
        )
    return result, problems


def validate_manifest(
    manifest: dict[str, Any],
    inventory: dict[str, Any],
    pack_root: Path,
) -> dict[str, Any]:
    """Validate one manifest against one exact build inventory."""

    problems: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    expected, inventory_problems = inventory_slots(inventory)
    problems.extend(inventory_problems)

    if not isinstance(manifest, dict):
        problems.append(issue("manifest_shape", "manifest must be an object"))
        return {
            "valid": False,
            "issues": problems,
            "warnings": warnings,
            "checked_slots": 0,
            "checked_files": 0,
        }

    enabled = manifest.get("enabled")
    coverage = manifest.get("coverage")
    slots = manifest.get("logical_slots")
    target = manifest.get("target")
    if not isinstance(enabled, bool):
        problems.append(issue("manifest_shape", "enabled must be boolean"))
        enabled = False
    if coverage not in {"partial", "complete_hero"}:
        problems.append(
            issue(
                "manifest_shape",
                "coverage must be partial or complete_hero",
            )
        )
    if not isinstance(slots, list):
        problems.append(
            issue("manifest_shape", "logical_slots must be an array")
        )
        slots = []
    if manifest.get("fallback") != "original":
        problems.append(
            issue("fallback", "fallback must be exactly 'original'")
        )
    if not isinstance(target, dict):
        problems.append(issue("target", "target must be an object"))
        target = {}
    expected_build = inventory.get("steam_build")
    supported_builds = inventory.get("supported_builds")
    if expected_build is None and isinstance(inventory.get("target"), dict):
        expected_build = inventory["target"].get("steam_build")
        supported_builds = inventory["target"].get("supported_builds")
    if expected_build is None and isinstance(
        inventory.get("audio_template"), dict
    ):
        audio_target = inventory["audio_template"].get("target")
        if isinstance(audio_target, dict):
            expected_build = audio_target.get("steam_build")
            supported_builds = audio_target.get("supported_builds")
    allowed_builds = {
        str(value)
        for value in (supported_builds or [expected_build])
        if value is not None
    }
    if str(target.get("steam_build") or "") not in allowed_builds:
        problems.append(
            issue(
                "target_build",
                "manifest steam_build does not match the routing inventory",
                "$.target.steam_build",
            )
        )
    if target.get("hero") != "Mak" or target.get("game") != "The Bazaar":
        problems.append(
            issue(
                "target",
                "semantic validator only accepts The Bazaar / Mak manifests",
                "$.target",
            )
        )

    names: set[str] = set()
    identities: set[tuple[Any, ...]] = set()
    normalized_paths: set[str] = set()
    checked_files = 0
    root = pack_root.resolve()

    for index, raw_slot in enumerate(slots):
        location = f"$.logical_slots[{index}]"
        if not isinstance(raw_slot, dict):
            problems.append(
                issue("manifest_shape", "slot must be an object", location)
            )
            continue
        name = raw_slot.get("logical_slot")
        if not isinstance(name, str) or not name:
            problems.append(
                issue("manifest_shape", "logical_slot is missing", location)
            )
            continue
        if name in names:
            problems.append(
                issue(
                    "duplicate_logical_name",
                    f"duplicate logical_slot name: {name}",
                    location,
                )
            )
        names.add(name)

        identity = logical_identity(raw_slot)
        if identity in identities:
            problems.append(
                issue(
                    "duplicate_logical_identity",
                    f"duplicate hook/GUID/path/selector identity: {name}",
                    location,
                )
            )
        identities.add(identity)

        exact = expected.get(name)
        if exact is None:
            problems.append(
                issue(
                    "unknown_logical_slot",
                    f"logical slot is absent from build inventory: {name}",
                    location,
                )
            )
        else:
            for field in (
                "hook_type",
                "event_guid",
                "event_path",
                "selectors",
            ):
                if raw_slot.get(field) != exact[field]:
                    problems.append(
                        issue(
                            f"identity_mismatch_{field}",
                            (
                                f"{name} {field} does not exactly match the "
                                "ordered build inventory identity"
                            ),
                            f"{location}.{field}",
                        )
                    )

        variants = raw_slot.get("variants")
        if not isinstance(variants, list):
            problems.append(
                issue(
                    "manifest_shape",
                    "variants must be an array",
                    f"{location}.variants",
                )
            )
            continue
        if enabled and not variants:
            problems.append(
                issue(
                    "missing_variant",
                    "every enabled logical slot requires a variant",
                    f"{location}.variants",
                )
            )
        for variant_index, variant in enumerate(variants):
            variant_location = (
                f"{location}.variants[{variant_index}]"
            )
            if not isinstance(variant, dict):
                problems.append(
                    issue(
                        "manifest_shape",
                        "variant must be an object",
                        variant_location,
                    )
                )
                continue
            normalized, path_error = normalized_variant_path(
                variant.get("file")
            )
            if path_error:
                problems.append(
                    issue("unsafe_variant_path", path_error, variant_location)
                )
                continue
            assert normalized is not None
            duplicate_key = normalized.casefold()
            if duplicate_key in normalized_paths:
                problems.append(
                    issue(
                        "duplicate_variant_path",
                        f"duplicate normalized variant path: {normalized}",
                        variant_location,
                    )
                )
            normalized_paths.add(duplicate_key)

            declared_hash = variant.get("sha256")
            if (
                not isinstance(declared_hash, str)
                or SHA256_PATTERN.fullmatch(declared_hash) is None
            ):
                problems.append(
                    issue(
                        "invalid_sha256",
                        "sha256 must be 64 lowercase hexadecimal characters",
                        f"{variant_location}.sha256",
                    )
                )
                continue
            if not enabled:
                continue

            candidate = (root / Path(*normalized.split("/"))).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                problems.append(
                    issue(
                        "unsafe_variant_path",
                        "resolved variant escapes pack root",
                        variant_location,
                    )
                )
                continue
            if not candidate.is_file():
                problems.append(
                    issue(
                        "missing_variant_file",
                        f"variant file is missing: {normalized}",
                        variant_location,
                    )
                )
                continue
            checked_files += 1
            actual_hash = sha256_file(candidate)
            if actual_hash != declared_hash:
                problems.append(
                    issue(
                        "variant_sha256_mismatch",
                        f"SHA-256 mismatch for {normalized}",
                        f"{variant_location}.sha256",
                    )
                )

    if enabled and not slots:
        problems.append(
            issue(
                "enabled_empty",
                "an enabled manifest must contain at least one logical slot",
            )
        )
    if coverage == "complete_hero":
        present = names
        required = set(expected)
        missing = sorted(required - present)
        extra = sorted(present - required)
        if missing or extra or len(slots) != len(required):
            details = []
            if missing:
                details.append("missing=" + ",".join(missing))
            if extra:
                details.append("extra=" + ",".join(extra))
            if len(slots) != len(required):
                details.append(
                    f"declared_count={len(slots)} expected={len(required)}"
                )
            problems.append(
                issue(
                    "incomplete_exact_set",
                    "complete_hero must equal the exact inventory set; "
                    + "; ".join(details),
                    "$.logical_slots",
                )
            )

    if not enabled:
        warnings.append(
            issue(
                "disabled_files_not_checked",
                (
                    "identity and path semantics were checked, but disabled "
                    "manifest files and hashes were not required to exist"
                ),
            )
        )
    return {
        "valid": not problems,
        "issues": problems,
        "warnings": warnings,
        "checked_slots": len(slots),
        "checked_files": checked_files,
        "inventory_build": expected_build,
        "inventory_builds": sorted(allowed_builds),
        "inventory_exact_slot_count": len(expected),
    }


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Cross-check an audio UGC manifest against an exact routing "
            "inventory. Run JSON Schema validation separately first."
        )
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument(
        "--inventory",
        default=str(DEFAULT_INVENTORY),
    )
    parser.add_argument(
        "--pack-root",
        help="Asset root; defaults to the manifest directory.",
    )
    parser.add_argument("--json-output")
    args = parser.parse_args()

    manifest_path = Path(args.manifest).resolve()
    inventory_path = Path(args.inventory).resolve()
    pack_root = (
        Path(args.pack_root).resolve()
        if args.pack_root
        else manifest_path.parent
    )
    result = validate_manifest(
        load_json(manifest_path),
        load_json(inventory_path),
        pack_root,
    )
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.json_output:
        output = Path(args.json_output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if result["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
