#!/usr/bin/env python3
"""Reversible runtime and external skin-pack manager for The Bazaar."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


APP_ID = "1617400"
MANAGER_VERSION = "0.9.5"
PROJECT_ROOT = Path(
    getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])
)
DEFAULT_RUNTIME = PROJECT_ROOT / "dist" / "runtime" / "BazaarSkinManager.Runtime.dll"
DEFAULT_RUNTIME_METADATA = (
    PROJECT_ROOT / "dist" / "runtime" / "runtime-build.json"
)
PRELOAD_TEXTURE_MODE = "preload_unity_texture2d"


def _load_bundle_patcher():
    try:
        from unity_bundle_texture_patch import patch_texture_bundle_many

        return patch_texture_bundle_many
    except ModuleNotFoundError:
        module_path = Path(__file__).resolve().with_name(
            "unity_bundle_texture_patch.py"
        )
        if not module_path.is_file():
            raise
        specification = importlib.util.spec_from_file_location(
            "unity_bundle_texture_patch",
            module_path,
        )
        if specification is None or specification.loader is None:
            raise RuntimeError(f"Cannot load Unity bundle patcher: {module_path}")
        module = importlib.util.module_from_spec(specification)
        sys.modules[specification.name] = module
        specification.loader.exec_module(module)
        return module.patch_texture_bundle_many


@dataclass
class GameInstall:
    game_dir: Path
    manifest: Path | None
    build_id: str | None
    complete: bool


def game_is_complete(game_dir: Path) -> bool:
    return (
        (game_dir / "TheBazaar.exe").is_file()
        and (game_dir / "TheBazaar_Data").is_dir()
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def runtime_release_info(runtime: Path) -> dict:
    digest = sha256_file(runtime)
    metadata_path = runtime.with_name("runtime-build.json")
    if not metadata_path.is_file() and runtime.resolve() == DEFAULT_RUNTIME.resolve():
        metadata_path = DEFAULT_RUNTIME_METADATA

    version = None
    if metadata_path.is_file():
        payload = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        expected_hash = payload.get("sha256")
        if expected_hash and expected_hash.casefold() != digest.casefold():
            raise RuntimeError(
                "runtime metadata SHA-256 does not match the selected DLL"
            )
        version = payload.get("version")

    return {
        "version": version,
        "source_path": str(runtime.resolve()),
        "sha256": digest,
    }


def local_app_data() -> Path:
    value = os.environ.get("LOCALAPPDATA")
    if value:
        return Path(value)
    return Path.home() / "AppData" / "Local"


def mods_root() -> Path:
    return local_app_data() / "BazaarSkinManager" / "TheBazaar" / "mods"


def manager_root() -> Path:
    return local_app_data() / "BazaarSkinManager" / "TheBazaar" / "manager"


def runtime_compatibility_path() -> Path:
    return manager_root() / "runtime-compatibility.json"


def logical_drive_roots() -> list[Path]:
    """Return mounted Windows drive roots without requiring WMI or PowerShell."""
    if sys.platform != "win32":
        return []
    try:
        import ctypes

        mask = ctypes.windll.kernel32.GetLogicalDrives()
    except (AttributeError, OSError):
        return []
    return [
        Path(f"{chr(ord('A') + index)}:\\")
        for index in range(26)
        if mask & (1 << index)
    ]


def common_steam_locations(
    drive_roots: Iterable[Path] | None = None,
) -> list[Path]:
    """Enumerate conventional Steam roots on every mounted drive."""
    locations: list[Path] = []
    for drive in drive_roots if drive_roots is not None else logical_drive_roots():
        locations.extend(
            [
                drive / "SteamLibrary",
                drive / "Steam",
                drive / "Program Files (x86)" / "Steam",
                drive / "Program Files" / "Steam",
            ]
        )
    return locations


def steam_roots() -> list[Path]:
    roots: list[Path] = []
    if sys.platform == "win32":
        try:
            import winreg

            for hive, key_path in (
                (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
            ):
                try:
                    with winreg.OpenKey(hive, key_path) as key:
                        for name in ("SteamPath", "InstallPath"):
                            try:
                                roots.append(Path(winreg.QueryValueEx(key, name)[0]))
                            except OSError:
                                pass
                except OSError:
                    pass
        except ImportError:
            pass

    roots.extend(common_steam_locations())
    # Retain these paths when running detection in a non-Windows test host.
    roots.extend(
        (Path(r"C:\Program Files (x86)\Steam"), Path(r"C:\Program Files\Steam"))
    )

    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def find_steam_executable(roots: Iterable[Path] | None = None) -> Path | None:
    for root in roots if roots is not None else steam_roots():
        executable = root / "steam.exe"
        if executable.is_file():
            return executable.resolve()
    return None


def parse_libraryfolders(text: str) -> list[Path]:
    libraries: list[Path] = []
    for match in re.finditer(r'"path"\s*"([^"]+)"', text, re.IGNORECASE):
        libraries.append(Path(match.group(1).replace(r"\\", "\\")))
    return libraries


def parse_manifest(text: str) -> tuple[str | None, str | None]:
    install_match = re.search(r'"installdir"\s*"([^"]+)"', text, re.IGNORECASE)
    build_match = re.search(r'"buildid"\s*"([^"]+)"', text, re.IGNORECASE)
    return (
        install_match.group(1) if install_match else None,
        build_match.group(1) if build_match else None,
    )


def library_roots(roots: Iterable[Path] | None = None) -> list[Path]:
    result: list[Path] = []
    for steam_root in roots or steam_roots():
        result.append(steam_root)
        vdf = steam_root / "steamapps" / "libraryfolders.vdf"
        if vdf.is_file():
            result.extend(parse_libraryfolders(vdf.read_text(encoding="utf-8", errors="replace")))

    unique: list[Path] = []
    seen: set[str] = set()
    for root in result:
        key = str(root).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def detect_installs(roots: Iterable[Path] | None = None) -> list[GameInstall]:
    installs: list[GameInstall] = []
    for library in library_roots(roots):
        manifest = library / "steamapps" / f"appmanifest_{APP_ID}.acf"
        build_id = None
        if manifest.is_file():
            text = manifest.read_text(encoding="utf-8", errors="replace")
            install_dir, build_id = parse_manifest(text)
            if not install_dir:
                continue
            game_dir = library / "steamapps" / "common" / install_dir
            manifest_value: Path | None = manifest
        else:
            # Steam can retain app 1617400 in libraryfolders.vdf while its
            # appmanifest is temporarily absent. Keep the known directory as
            # an incomplete candidate so diagnostics explain the real state.
            game_dir = library / "steamapps" / "common" / "The Bazaar"
            if not game_dir.exists():
                continue
            manifest_value = None
        installs.append(
            GameInstall(
                game_dir=game_dir,
                manifest=manifest_value,
                build_id=build_id,
                complete=game_is_complete(game_dir),
            )
        )

    unique: list[GameInstall] = []
    seen: set[str] = set()
    for install in installs:
        key = str(install.game_dir.resolve()).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(install)
    return unique


def explicit_install(game_dir: Path) -> GameInstall:
    game_dir = game_dir.resolve()
    steamapps = game_dir.parent.parent
    manifest = steamapps / f"appmanifest_{APP_ID}.acf"
    build_id = None
    if manifest.is_file():
        _, build_id = parse_manifest(
            manifest.read_text(encoding="utf-8", errors="replace")
        )
    return GameInstall(
        game_dir=game_dir,
        manifest=manifest if manifest.is_file() else None,
        build_id=build_id,
        complete=game_is_complete(game_dir),
    )


def recorded_install(record: dict) -> GameInstall:
    """Rehydrate an install record without discarding Steam build metadata."""
    game_record = record.get("game") or {}
    game_dir = Path(game_record["game_dir"]).resolve()

    for detected in detect_installs():
        if detected.game_dir.resolve() == game_dir:
            return detected

    manifest_value = game_record.get("manifest")
    manifest = Path(manifest_value) if manifest_value else None
    build_id = game_record.get("build_id")
    if manifest and manifest.is_file():
        _, current_build_id = parse_manifest(
            manifest.read_text(encoding="utf-8", errors="replace")
        )
        build_id = current_build_id

    return GameInstall(
        game_dir=game_dir,
        manifest=manifest if manifest and manifest.is_file() else None,
        build_id=build_id,
        complete=game_is_complete(game_dir),
    )


def validate_pack(pack_dir: Path) -> list[str]:
    errors: list[str] = []
    manifest_path = pack_dir / "mod.json"
    if not manifest_path.is_file():
        return [f"missing {manifest_path}"]

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if not manifest.get("id"):
        errors.append("id is required")
    target = manifest.get("target") or {}
    if target.get("hero") != "Mak":
        errors.append("target.hero must be Mak")
    if target.get("skin") != "Skin_MAK_01/A":
        errors.append("target.skin must be Skin_MAK_01/A")

    pack_prefix = pack_dir.resolve()
    native_targets: dict[str, tuple[str, tuple[str, ...]]] = {}
    native_assets: dict[tuple[str, str], str] = {}
    for replacement in manifest.get("visual_replacements", []):
        relative = replacement.get("file", "")
        asset = (pack_dir / relative).resolve()
        try:
            asset.relative_to(pack_prefix)
        except ValueError:
            errors.append(f"asset escapes pack: {relative}")
            continue
        if not asset.is_file():
            errors.append(f"missing asset: {relative}")
        if not replacement.get("match_names") and not replacement.get("direct_only"):
            errors.append(f"missing match_names for {replacement.get('slot')}")
        deployment = replacement.get("deployment")
        if not deployment:
            continue
        slot = replacement.get("slot")
        if deployment.get("mode") != PRELOAD_TEXTURE_MODE:
            errors.append(f"unsupported deployment mode for {slot}")
            continue
        target = str(deployment.get("target") or "").replace("\\", "/")
        target_parts = Path(target).parts
        if (
            not target
            or Path(target).is_absolute()
            or ".." in target_parts
            or ":" in target
        ):
            errors.append(f"unsafe native patch target for {slot}: {target}")
        target_key = target.casefold()
        if not deployment.get("asset_name"):
            errors.append(f"native patch asset_name is required for {slot}")
        if not deployment.get("unity_version"):
            errors.append(f"native patch unity_version is required for {slot}")
        size = deployment.get("target_size")
        if (
            not isinstance(size, list)
            or len(size) != 2
            or any(not isinstance(value, int) or value <= 0 for value in size)
        ):
            errors.append(f"native patch target_size is invalid for {slot}")
        supported = deployment.get("supported_original_sha256")
        if (
            not isinstance(supported, list)
            or not supported
            or any(
                not isinstance(value, str)
                or not re.fullmatch(r"[0-9a-fA-F]{64}", value)
                for value in supported
            )
        ):
            errors.append(
                f"native patch supported_original_sha256 is invalid for {slot}"
            )
        elif target:
            signature = (
                str(deployment.get("unity_version")),
                tuple(sorted(value.casefold() for value in supported)),
            )
            previous_signature = native_targets.get(target_key)
            if previous_signature is not None and previous_signature != signature:
                errors.append(
                    f"inconsistent native patch target contract: {target}"
                )
            native_targets[target_key] = signature

            asset_key = (
                target_key,
                str(deployment.get("asset_name")).casefold(),
            )
            asset_hash = sha256_file(asset) if asset.is_file() else ""
            previous_hash = native_assets.get(asset_key)
            if previous_hash is not None and previous_hash != asset_hash:
                errors.append(
                    f"conflicting native patch images for "
                    f"{deployment.get('asset_name')}"
                )
            native_assets[asset_key] = asset_hash

    audio_manifest_relative = manifest.get("audio_manifest")
    if audio_manifest_relative:
        audio_manifest_path = (pack_dir / audio_manifest_relative).resolve()
        try:
            audio_manifest_path.relative_to(pack_prefix)
        except ValueError:
            errors.append(f"audio manifest escapes pack: {audio_manifest_relative}")
        else:
            if not audio_manifest_path.is_file():
                errors.append(f"missing audio manifest: {audio_manifest_relative}")
            else:
                try:
                    audio_manifest = json.loads(
                        audio_manifest_path.read_text(encoding="utf-8")
                    )
                except (OSError, UnicodeError, json.JSONDecodeError) as exception:
                    errors.append(f"invalid audio manifest: {exception}")
                else:
                    if audio_manifest.get("schema_version") != 1:
                        errors.append("audio schema_version must be 1")
                    if audio_manifest.get("fallback") != "original":
                        errors.append("audio fallback must be original")
                    audio_target = audio_manifest.get("target") or {}
                    if audio_target.get("hero") != "Mak":
                        errors.append("audio target.hero must be Mak")
                    if audio_target.get("steam_build") != "24001960":
                        errors.append("audio target.steam_build must be 24001960")
                    identities: set[tuple] = set()
                    for route in audio_manifest.get("routes") or []:
                        selectors = tuple(
                            (
                                selector.get("parameter"),
                                selector.get("label"),
                            )
                            for selector in route.get("selectors") or []
                        )
                        identity = (route.get("event_guid"), selectors)
                        if identity in identities:
                            errors.append(
                                "duplicate audio route identity: "
                                f"{route.get('logical_slot')}"
                            )
                        identities.add(identity)
                        if not route.get("variants"):
                            errors.append(
                                "audio route has no variants: "
                                f"{route.get('logical_slot')}"
                            )
                        for variant in route.get("variants") or []:
                            relative = variant.get("file", "")
                            asset = (pack_dir / relative).resolve()
                            try:
                                asset.relative_to(pack_prefix)
                            except ValueError:
                                errors.append(f"audio asset escapes pack: {relative}")
                                continue
                            if not asset.is_file():
                                errors.append(f"missing audio asset: {relative}")
                            elif sha256_file(asset) != variant.get("sha256"):
                                errors.append(f"audio hash mismatch: {relative}")

    index_path = pack_dir / "asset-index.json"
    if not index_path.is_file():
        errors.append("missing asset-index.json")
    else:
        index = json.loads(index_path.read_text(encoding="utf-8"))
        for relative, expected in (index.get("files") or {}).items():
            asset = (pack_dir / relative).resolve()
            try:
                asset.relative_to(pack_prefix)
            except ValueError:
                errors.append(f"indexed asset escapes pack: {relative}")
                continue
            if not asset.is_file():
                errors.append(f"missing indexed asset: {relative}")
            elif sha256_file(asset) != expected.get("sha256"):
                errors.append(f"hash mismatch: {relative}")
    return errors


def fingerprint(install: GameInstall) -> dict:
    result = {
        "app_id": APP_ID,
        "game_dir": str(install.game_dir),
        "build_id": install.build_id,
        "complete": install.complete,
        "files": {},
    }
    candidates = [
        install.game_dir / "TheBazaar.exe",
        install.game_dir / "TheBazaar_Data" / "Managed" / "TheBazaarRuntime.dll",
        install.game_dir / "TheBazaar_Data" / "StreamingAssets" / "aa" / "catalog.hash",
    ]
    for path in candidates:
        if path.is_file():
            result["files"][str(path.relative_to(install.game_dir))] = sha256_file(path)
    return result


def runtime_compatibility_payload(install: GameInstall) -> dict:
    current = fingerprint(install)
    files = current.get("files") or {}
    if "TheBazaar.exe" not in files:
        raise RuntimeError(
            "TheBazaar.exe is missing; cannot authorize runtime startup"
        )
    return {
        "schema_version": 1,
        "app_id": APP_ID,
        "game_dir": str(install.game_dir.resolve()),
        "build_id": install.build_id,
        "files": [
            {
                "path": relative.replace("\\", "/"),
                "sha256": digest,
            }
            for relative, digest in sorted(files.items())
        ],
    }


def runtime_compatibility_errors(
    payload: dict,
    install: GameInstall,
) -> list[str]:
    errors: list[str] = []
    if payload.get("schema_version") != 1:
        errors.append("unsupported runtime compatibility schema")
    if payload.get("app_id") != APP_ID:
        errors.append("runtime compatibility app_id mismatch")
    try:
        recorded_game_dir = Path(payload.get("game_dir", "")).resolve()
    except (OSError, TypeError):
        recorded_game_dir = Path()
    if str(recorded_game_dir).casefold() != str(
        install.game_dir.resolve()
    ).casefold():
        errors.append("runtime compatibility game directory mismatch")
    if payload.get("build_id") != install.build_id:
        errors.append("runtime compatibility Steam build mismatch")

    recorded_files: dict[str, str] = {}
    for item in payload.get("files") or []:
        if not isinstance(item, dict):
            errors.append("malformed runtime compatibility file entry")
            continue
        relative = str(item.get("path", "")).replace("\\", "/")
        digest = item.get("sha256")
        if not relative or not isinstance(digest, str):
            errors.append("malformed runtime compatibility file entry")
            continue
        if relative in recorded_files:
            errors.append(f"duplicate runtime compatibility file: {relative}")
            continue
        recorded_files[relative] = digest

    current_files = {
        relative.replace("\\", "/"): digest
        for relative, digest in (fingerprint(install).get("files") or {}).items()
    }
    if "TheBazaar.exe" not in recorded_files:
        errors.append("runtime compatibility record lacks TheBazaar.exe")
    if recorded_files != current_files:
        errors.append("runtime compatibility file fingerprint mismatch")
    return errors


def serialized_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        delete=False,
    ) as stream:
        stream.write(text)
        temporary = Path(stream.name)
    os.replace(temporary, path)


def atomic_copy_tree(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temp:
        staged = Path(temp) / destination.name
        shutil.copytree(source, staged)
        if destination.exists():
            shutil.rmtree(destination)
        shutil.move(str(staged), str(destination))


def atomic_copy_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb",
        dir=destination.parent,
        delete=False,
    ) as stream:
        temporary = Path(stream.name)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def native_patch_specs(pack: Path) -> list[dict]:
    manifest = json.loads((pack / "mod.json").read_text(encoding="utf-8"))
    return [
        replacement
        for replacement in manifest.get("visual_replacements") or []
        if (replacement.get("deployment") or {}).get("mode")
        == PRELOAD_TEXTURE_MODE
    ]


def native_patch_target(game: GameInstall, deployment: dict) -> Path:
    relative = str(deployment["target"]).replace("/", os.sep)
    target = (game.game_dir / relative).resolve()
    game_root = game.game_dir.resolve()
    try:
        target.relative_to(game_root)
    except ValueError as error:
        raise RuntimeError(
            f"Native patch target escapes the game directory: {relative}"
        ) from error
    return target


def addressables_catalog_path(game: GameInstall) -> Path:
    return (
        game.game_dir
        / "TheBazaar_Data"
        / "StreamingAssets"
        / "aa"
        / "catalog.bin"
    )


def existing_install_record() -> dict | None:
    path = manager_root() / "install-manifest.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def native_patch_plan_issues(pack: Path, game: GameInstall) -> list[str]:
    issues: list[str] = []
    current_record = existing_install_record() or {}
    recorded_by_target = {
        str(item.get("target", "")).casefold(): item
        for item in current_record.get("native_patches") or []
    }
    grouped: dict[str, list[dict]] = {}
    for replacement in native_patch_specs(pack):
        deployment = replacement["deployment"]
        target = native_patch_target(game, deployment)
        grouped.setdefault(str(target).casefold(), []).append(replacement)
    if grouped and not addressables_catalog_path(game).is_file():
        issues.append(
            "Addressables catalog is missing for deploy-time bundle "
            f"replacement: {addressables_catalog_path(game)}"
        )
    for replacements in grouped.values():
        replacement = replacements[0]
        slot_names = ", ".join(item["slot"] for item in replacements)
        deployment = replacement["deployment"]
        target = native_patch_target(game, deployment)
        if not target.is_file():
            issues.append(
                f"native patch target is missing for {slot_names}: {target}"
            )
            continue
        current_hash = sha256_file(target)
        supported = {
            value.casefold()
            for value in deployment["supported_original_sha256"]
        }
        if current_hash.casefold() in supported:
            continue
        recorded = recorded_by_target.get(str(target).casefold())
        backup = Path(recorded.get("backup", "")) if recorded else None
        if (
            recorded
            and current_hash == recorded.get("patched_sha256")
            and backup is not None
            and backup.is_file()
            and sha256_file(backup) == recorded.get("original_sha256")
        ):
            continue
        issues.append(
            f"native patch target hash is unsupported for {slot_names}: "
            f"{current_hash}"
        )
    return issues


def prepare_native_patches(
    pack: Path,
    game: GameInstall,
    staging: Path,
) -> list[dict]:
    patch_texture_bundle_many = _load_bundle_patcher()
    prepared: list[dict] = []
    grouped: dict[str, list[dict]] = {}
    for replacement in native_patch_specs(pack):
        target = native_patch_target(game, replacement["deployment"])
        grouped.setdefault(str(target).casefold(), []).append(replacement)

    for replacements in grouped.values():
        replacement = replacements[0]
        slots = [item["slot"] for item in replacements]
        deployment = replacement["deployment"]
        target = native_patch_target(game, deployment)
        if not target.is_file():
            raise RuntimeError(
                f"Native patch target is missing for {', '.join(slots)}: "
                f"{target}"
            )
        original_sha256 = sha256_file(target)
        supported = {
            value.casefold()
            for value in deployment["supported_original_sha256"]
        }
        if original_sha256.casefold() not in supported:
            raise RuntimeError(
                f"Native patch target hash is unsupported for "
                f"{', '.join(slots)}: "
                f"{original_sha256}. Steam may have updated the game."
            )

        output = staging / f"native-patch-{len(prepared):02d}.bundle"
        texture_replacements = []
        for item in replacements:
            item_deployment = item["deployment"]
            texture_replacements.append(
                {
                    "replacement_image": (pack / item["file"]).resolve(),
                    "asset_name": item_deployment["asset_name"],
                    "target_size": tuple(
                        int(value)
                        for value in item_deployment["target_size"]
                    ),
                }
            )
        result = patch_texture_bundle_many(
            target,
            output,
            texture_replacements,
            unity_version=deployment["unity_version"],
        )
        source_crc32 = str(result.get("source_crc32") or "").casefold()
        output_crc32 = str(result.get("output_crc32") or "").casefold()
        if not re.fullmatch(r"[0-9a-f]{8}", source_crc32):
            raise RuntimeError(
                f"Bundle patcher did not return a valid source CRC for "
                f"{target.name}."
            )
        if not re.fullmatch(r"[0-9a-f]{8}", output_crc32):
            raise RuntimeError(
                f"Bundle patcher did not return a valid output CRC for "
                f"{target.name}."
            )
        backup = (
            manager_root()
            / "native-backups"
            / original_sha256
            / target.name
        )
        prepared.append(
            {
                "slot": slots[0],
                "slots": slots,
                "target": str(target),
                "backup": str(backup),
                "original_sha256": original_sha256,
                "patched_sha256": result["output_sha256"],
                "original_crc32": source_crc32,
                "patched_crc32": output_crc32,
                "staged": str(output),
                "asset_names": [
                    item["deployment"]["asset_name"]
                    for item in replacements
                ],
                "mode": PRELOAD_TEXTURE_MODE,
            }
        )
    return prepared


def prepare_native_catalog_patch(
    prepared_patches: list[dict],
    game: GameInstall,
    staging: Path,
) -> dict | None:
    if not prepared_patches:
        return None
    catalog = addressables_catalog_path(game)
    if not catalog.is_file():
        raise RuntimeError(f"Addressables catalog is missing: {catalog}")

    original = catalog.read_bytes()
    patched = bytearray(original)
    entries: list[dict] = []
    for item in prepared_patches:
        bundle_name = Path(item["target"]).name
        encoded_name = bundle_name.encode("utf-8")
        name_positions = [
            match.start()
            for match in re.finditer(re.escape(encoded_name), original)
        ]
        if len(name_positions) != 1:
            raise RuntimeError(
                f"Expected exactly one catalog entry for {bundle_name}; "
                f"found {len(name_positions)}."
            )
        search_start = name_positions[0] + len(encoded_name)
        search_end = min(len(original), search_start + 128)
        original_crc = int(item["original_crc32"], 16)
        patched_crc = int(item["patched_crc32"], 16)
        encoded_crc = struct.pack("<I", original_crc)
        crc_positions = []
        cursor = search_start
        while True:
            position = original.find(encoded_crc, cursor, search_end)
            if position < 0:
                break
            crc_positions.append(position)
            cursor = position + 1
        if len(crc_positions) != 1:
            raise RuntimeError(
                f"Expected one CRC field near catalog entry {bundle_name}; "
                f"found {len(crc_positions)}."
            )
        crc_position = crc_positions[0]
        patched[crc_position : crc_position + 4] = struct.pack(
            "<I",
            patched_crc,
        )
        entries.append(
            {
                "bundle": bundle_name,
                "offset": crc_position,
                "original_crc32": item["original_crc32"],
                "patched_crc32": item["patched_crc32"],
            }
        )

    staged = staging / "catalog.bin"
    staged.write_bytes(patched)
    original_sha256 = hashlib.sha256(original).hexdigest()
    patched_sha256 = hashlib.sha256(patched).hexdigest()
    backup = (
        manager_root()
        / "native-backups"
        / original_sha256
        / "catalog.bin"
    )
    return {
        "target": str(catalog),
        "backup": str(backup),
        "original_sha256": original_sha256,
        "patched_sha256": patched_sha256,
        "staged": str(staged),
        "entries": entries,
    }


def apply_native_patches(prepared: list[dict]) -> list[dict]:
    applied: list[dict] = []
    try:
        for item in prepared:
            target = Path(item["target"])
            backup = Path(item["backup"])
            staged = Path(item["staged"])
            if backup.is_file():
                if sha256_file(backup) != item["original_sha256"]:
                    raise RuntimeError(
                        f"Native backup hash mismatch: {backup}"
                    )
            else:
                atomic_copy_file(target, backup)
            if sha256_file(backup) != item["original_sha256"]:
                raise RuntimeError(
                    f"Native backup verification failed: {backup}"
                )
            atomic_copy_file(staged, target)
            if sha256_file(target) != item["patched_sha256"]:
                raise RuntimeError(
                    f"Native patch verification failed: {target}"
                )
            applied.append(item)
    except Exception:
        for item in reversed(applied):
            backup = Path(item["backup"])
            target = Path(item["target"])
            if backup.is_file():
                atomic_copy_file(backup, target)
        raise
    return [
        {key: value for key, value in item.items() if key != "staged"}
        for item in applied
    ]


def apply_native_catalog_patch(prepared: dict | None) -> dict | None:
    if prepared is None:
        return None
    target = Path(prepared["target"])
    backup = Path(prepared["backup"])
    staged = Path(prepared["staged"])
    if backup.is_file():
        if sha256_file(backup) != prepared["original_sha256"]:
            raise RuntimeError(
                f"Addressables catalog backup hash mismatch: {backup}"
            )
    else:
        atomic_copy_file(target, backup)
    if sha256_file(backup) != prepared["original_sha256"]:
        raise RuntimeError(
            f"Addressables catalog backup verification failed: {backup}"
        )
    try:
        atomic_copy_file(staged, target)
        if sha256_file(target) != prepared["patched_sha256"]:
            raise RuntimeError(
                f"Addressables catalog patch verification failed: {target}"
            )
    except Exception:
        atomic_copy_file(backup, target)
        raise
    return {
        key: value
        for key, value in prepared.items()
        if key != "staged"
    }


def install(runtime: Path, pack: Path, game: GameInstall) -> dict:
    if not game.complete:
        raise RuntimeError(f"incomplete game installation: {game.game_dir}")
    if not runtime.is_file():
        raise RuntimeError(f"runtime DLL not built: {runtime}")
    if not (game.game_dir / "BepInEx" / "core" / "BepInEx.dll").is_file():
        raise RuntimeError(
            "BepInEx is missing; install or repair BazaarPlusPlus before this skin runtime"
        )
    errors = validate_pack(pack)
    if errors:
        raise RuntimeError("invalid pack: " + "; ".join(errors))

    if (manager_root() / "install-manifest.json").is_file():
        uninstall()

    compatibility_path = runtime_compatibility_path()
    if compatibility_path.exists():
        compatibility_path.unlink()

    plugin_dir = game.game_dir / "BepInEx" / "plugins" / "BazaarSkinManagerRuntime"
    plugin_dir.mkdir(parents=True, exist_ok=True)
    plugin_dest = plugin_dir / runtime.name
    shutil.copy2(runtime, plugin_dest)

    manifest = json.loads((pack / "mod.json").read_text(encoding="utf-8"))
    pack_dest = mods_root() / manifest["id"]
    atomic_copy_tree(pack, pack_dest)

    applied_native_patches: list[dict] = []
    applied_catalog_patch: dict | None = None
    try:
        with tempfile.TemporaryDirectory() as temp:
            prepared = prepare_native_patches(pack, game, Path(temp))
            prepared_catalog = prepare_native_catalog_patch(
                prepared,
                game,
                Path(temp),
            )
            applied_native_patches = apply_native_patches(prepared)
            applied_catalog_patch = apply_native_catalog_patch(
                prepared_catalog
            )

        compatibility = runtime_compatibility_payload(game)
        compatibility_text = serialized_json(compatibility)
        compatibility_sha256 = hashlib.sha256(
            compatibility_text.encode("utf-8")
        ).hexdigest()
        runtime_release = runtime_release_info(runtime)
        record = {
            "schema_version": 2,
            "manager": {
                "version": MANAGER_VERSION,
            },
            "game": asdict(game),
            "game_fingerprint": fingerprint(game),
            "plugin": {
                "path": str(plugin_dest),
                "sha256": sha256_file(plugin_dest),
            },
            "runtime": {
                "version": runtime_release["version"],
                "path": str(plugin_dest),
                "source_path": runtime_release["source_path"],
                "sha256": runtime_release["sha256"],
            },
            "pack": {
                "path": str(pack_dest),
                "id": manifest["id"],
                "version": manifest["version"],
                "manifest_sha256": sha256_file(pack / "mod.json"),
            },
            "runtime_compatibility": {
                "path": str(compatibility_path),
                "sha256": compatibility_sha256,
            },
            "native_patches": applied_native_patches,
            "native_catalog_patch": applied_catalog_patch,
        }
    except Exception:
        if applied_catalog_patch:
            backup = Path(applied_catalog_patch["backup"])
            target = Path(applied_catalog_patch["target"])
            if backup.is_file():
                atomic_copy_file(backup, target)
        for item in reversed(applied_native_patches):
            backup = Path(item["backup"])
            target = Path(item["target"])
            if backup.is_file():
                atomic_copy_file(backup, target)
        if plugin_dest.is_file():
            plugin_dest.unlink()
        if pack_dest.is_dir():
            shutil.rmtree(pack_dest)
        raise

    record["game"]["game_dir"] = str(record["game"]["game_dir"])
    record["game"]["manifest"] = (
        str(record["game"]["manifest"]) if record["game"]["manifest"] else None
    )
    manager_root().mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        manager_root() / "install-manifest.json",
        serialized_json(record),
    )
    atomic_write_text(compatibility_path, compatibility_text)
    return record


def plan_install(runtime: Path, pack: Path, game: GameInstall) -> dict:
    issues: list[str] = []
    if not game.complete:
        missing = [
            str(path)
            for path in (
                game.game_dir / "TheBazaar.exe",
                game.game_dir / "TheBazaar_Data",
            )
            if not path.exists()
        ]
        issues.append("incomplete game installation; missing: " + ", ".join(missing))
    if not runtime.is_file():
        issues.append(f"runtime DLL not built: {runtime}")
    pack_errors = validate_pack(pack)
    issues.extend(f"invalid pack: {error}" for error in pack_errors)
    if game.complete and not pack_errors:
        issues.extend(native_patch_plan_issues(pack, game))
    if not (game.game_dir / "BepInEx" / "core" / "BepInEx.dll").is_file():
        issues.append("BepInEx core is missing; repair BazaarPlusPlus first")

    manifest = None
    manifest_path = pack / "mod.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    pack_id = (manifest or {}).get("id", "external.skin.pack")
    return {
        "ready": not issues,
        "issues": issues,
        "game": asdict(game),
        "source_runtime": str(runtime),
        "source_pack": str(pack),
        "compatibility_fingerprint": (
            runtime_compatibility_payload(game)
            if game.complete
            else None
        ),
        "destinations": {
            "plugin": str(
                game.game_dir
                / "BepInEx"
                / "plugins"
                / "BazaarSkinManagerRuntime"
                / runtime.name
            ),
            "pack": str(mods_root() / pack_id),
            "record": str(manager_root() / "install-manifest.json"),
            "runtime_compatibility": str(runtime_compatibility_path()),
            "native_patches": list(
                dict.fromkeys(
                    str(native_patch_target(game, item["deployment"]))
                    for item in native_patch_specs(pack)
                )
            )
            if game.complete and not pack_errors
            else [],
            "addressables_catalog": (
                str(addressables_catalog_path(game))
                if (
                    game.complete
                    and not pack_errors
                    and native_patch_specs(pack)
                )
                else None
            ),
        },
    }


def plan_uninstall() -> dict:
    record_path = manager_root() / "install-manifest.json"
    if not record_path.is_file():
        compatibility = runtime_compatibility_path()
        return {
            "installed": compatibility.is_file(),
            "record": str(record_path),
            "targets": [str(compatibility)] if compatibility.is_file() else [],
        }
    record = json.loads(record_path.read_text(encoding="utf-8"))
    targets = [
        entry["path"]
        for entry in (
            record.get("plugin"),
            record.get("pack"),
            record.get("runtime_compatibility"),
        )
        if entry and entry.get("path")
    ]
    targets.extend(
        item["target"]
        for item in record.get("native_patches") or []
        if item.get("target")
    )
    return {
        "installed": True,
        "record": str(record_path),
        "targets": targets,
    }


def installation_diagnostics() -> dict:
    record_path = manager_root() / "install-manifest.json"
    if not record_path.is_file():
        return {
            "installed": False,
            "healthy": False,
            "reason": "manager install record not found",
        }

    record = json.loads(record_path.read_text(encoding="utf-8"))
    game = recorded_install(record)
    game_dir = game.game_dir
    current = fingerprint(game)
    previous = record.get("game_fingerprint") or {}
    plugin = Path(record["plugin"]["path"])
    pack = Path(record["pack"]["path"])
    plugin_hash_matches = (
        plugin.is_file() and sha256_file(plugin) == record["plugin"].get("sha256")
    )
    previous_files = previous.get("files") or {}
    current_files = current.get("files") or {}
    update_detected = (
        previous.get("build_id") != current.get("build_id")
        or previous_files != current_files
    )
    compatibility_entry = record.get("runtime_compatibility") or {}
    compatibility = Path(
        compatibility_entry.get("path") or runtime_compatibility_path()
    )
    compatibility_hash_matches = (
        compatibility.is_file()
        and sha256_file(compatibility)
        == compatibility_entry.get("sha256")
    )
    compatibility_errors: list[str] = []
    if compatibility.is_file():
        try:
            compatibility_payload = json.loads(
                compatibility.read_text(encoding="utf-8")
            )
            compatibility_errors = runtime_compatibility_errors(
                compatibility_payload,
                game,
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            compatibility_errors = [
                f"runtime compatibility record is malformed: {error}"
            ]
    else:
        compatibility_errors = ["runtime compatibility record is missing"]
    native_patches = record.get("native_patches") or []
    native_targets_patched = all(
        Path(item["target"]).is_file()
        and sha256_file(Path(item["target"])) == item.get("patched_sha256")
        for item in native_patches
    )
    native_backups_valid = all(
        Path(item["backup"]).is_file()
        and sha256_file(Path(item["backup"])) == item.get("original_sha256")
        for item in native_patches
    )
    catalog_patch = record.get("native_catalog_patch")
    catalog_target_patched = (
        not catalog_patch
        or (
            Path(catalog_patch["target"]).is_file()
            and sha256_file(Path(catalog_patch["target"]))
            == catalog_patch.get("patched_sha256")
        )
    )
    catalog_backup_valid = (
        not catalog_patch
        or (
            Path(catalog_patch["backup"]).is_file()
            and sha256_file(Path(catalog_patch["backup"]))
            == catalog_patch.get("original_sha256")
        )
    )
    checks = {
        "game_complete": game.complete,
        "bepinex_present": (
            game_dir / "BepInEx" / "core" / "BepInEx.dll"
        ).is_file(),
        "plugin_present": plugin.is_file(),
        "plugin_hash_matches": plugin_hash_matches,
        "pack_present": pack.is_dir(),
        "pack_valid": pack.is_dir() and not validate_pack(pack),
        "runtime_compatibility_present": compatibility.is_file(),
        "runtime_compatibility_hash_matches": compatibility_hash_matches,
        "runtime_compatibility_matches_current": not compatibility_errors,
        "native_patch_targets_patched": native_targets_patched,
        "native_patch_backups_valid": native_backups_valid,
        "addressables_catalog_patched": catalog_target_patched,
        "addressables_catalog_backup_valid": catalog_backup_valid,
        "game_update_detected": update_detected,
    }
    positive_checks = {
        key: value
        for key, value in checks.items()
        if key != "game_update_detected"
    }
    healthy = all(positive_checks.values()) and not update_detected
    update_required = (
        update_detected
        or not checks["runtime_compatibility_matches_current"]
    )
    return {
        "installed": True,
        "healthy": healthy,
        "components": {
            "manager": record.get("manager") or {"version": None},
            "runtime": record.get("runtime") or {
                "version": None,
                "path": record["plugin"].get("path"),
                "sha256": record["plugin"].get("sha256"),
            },
            "pack": {
                "id": record["pack"].get("id"),
                "version": record["pack"].get("version"),
                "manifest_sha256": record["pack"].get(
                    "manifest_sha256"
                ),
            },
        },
        "state": (
            "healthy"
            if healthy
            else "update_required"
            if update_required
            else "repair_required"
        ),
        "update_required": update_required,
        "checks": checks,
        "compatibility_errors": compatibility_errors,
        "previous_fingerprint": previous,
        "current_fingerprint": current,
        "repair_command": (
            "python tools/bazaar_skin_manager.py plan-install"
            if update_required
            else "python tools/bazaar_skin_manager.py install"
            if not healthy
            else None
        ),
        "action_required": (
            "Game files differ from the authorized fingerprint. The runtime "
            "will stay disabled. Review plan-install, then run install only "
            "after confirming this runtime supports the current Steam build."
            if update_required
            else "Repair the owned runtime/pack files with install."
            if not healthy
            else None
        ),
    }


def uninstall() -> list[str]:
    record_path = manager_root() / "install-manifest.json"
    if not record_path.is_file():
        compatibility = runtime_compatibility_path()
        if compatibility.is_file():
            compatibility.unlink()
            return [str(compatibility)]
        return []
    record = json.loads(record_path.read_text(encoding="utf-8"))
    removed: list[str] = []
    catalog_patch = record.get("native_catalog_patch")
    if catalog_patch:
        target = Path(catalog_patch["target"])
        backup = Path(catalog_patch["backup"])
        current_hash = sha256_file(target) if target.is_file() else None
        if (
            backup.is_file()
            and sha256_file(backup) == catalog_patch.get("original_sha256")
            and (
                current_hash is None
                or current_hash == catalog_patch.get("patched_sha256")
            )
        ):
            atomic_copy_file(backup, target)
            removed.append(str(target))
        if backup.is_file():
            backup.unlink()
            removed.append(str(backup))
    for item in reversed(record.get("native_patches") or []):
        target = Path(item["target"])
        backup = Path(item["backup"])
        current_hash = sha256_file(target) if target.is_file() else None
        if (
            backup.is_file()
            and sha256_file(backup) == item.get("original_sha256")
            and (
                current_hash is None
                or current_hash == item.get("patched_sha256")
            )
        ):
            atomic_copy_file(backup, target)
            removed.append(str(target))
        if backup.is_file():
            backup.unlink()
            removed.append(str(backup))
    for entry in (
        record.get("plugin"),
        record.get("pack"),
        record.get("runtime_compatibility"),
    ):
        if not entry or not entry.get("path"):
            continue
        path = Path(entry["path"])
        if path.is_file():
            path.unlink()
            removed.append(str(path))
        elif path.is_dir():
            shutil.rmtree(path)
            removed.append(str(path))
    record_path.unlink()
    compatibility = runtime_compatibility_path()
    if compatibility.is_file() and str(compatibility) not in removed:
        compatibility.unlink()
        removed.append(str(compatibility))
    return removed


def choose_install(args: argparse.Namespace) -> GameInstall:
    if args.game_dir:
        return explicit_install(args.game_dir)
    installs = detect_installs()
    if not installs:
        raise RuntimeError("Steam appmanifest_1617400.acf was not found")
    complete = [item for item in installs if item.complete]
    return complete[0] if complete else installs[0]


def choose_pack(args: argparse.Namespace) -> Path:
    if args.pack is None:
        raise ValueError(
            "--pack is required for validate-pack, plan-install, and install."
        )
    return args.pack.resolve()


def preferred_game_install(game_dir: Path | None = None) -> GameInstall:
    if game_dir is not None:
        selected = explicit_install(game_dir)
        if not selected.complete:
            raise RuntimeError(f"Incomplete game installation: {selected.game_dir}")
        return selected

    record_path = manager_root() / "install-manifest.json"
    if record_path.is_file():
        try:
            selected = recorded_install(
                json.loads(record_path.read_text(encoding="utf-8"))
            )
            if selected.complete:
                return selected
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            pass

    complete = [item for item in detect_installs() if item.complete]
    if not complete:
        raise RuntimeError(
            "No complete Steam installation of The Bazaar was detected."
        )
    return complete[0]


def launch_game(game_dir: Path | None = None) -> dict:
    """Start The Bazaar through Steam so Steam authentication is preserved."""
    selected = preferred_game_install(game_dir)
    steam = find_steam_executable()
    if steam:
        command = [str(steam), "-applaunch", APP_ID]
        subprocess.Popen(command, cwd=str(steam.parent))
        return {
            "launched": True,
            "method": "steam_executable",
            "game_dir": str(selected.game_dir),
            "command": command,
        }

    if sys.platform != "win32" or not hasattr(os, "startfile"):
        raise RuntimeError(
            "Steam was not found. Start Steam once or select the game through Steam."
        )
    uri = f"steam://rungameid/{APP_ID}"
    os.startfile(uri)
    return {
        "launched": True,
        "method": "steam_protocol",
        "game_dir": str(selected.game_dir),
        "uri": uri,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--game-dir", type=Path)
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--pack", type=Path)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("detect")
    subparsers.add_parser("validate-pack")
    subparsers.add_parser("status")
    subparsers.add_parser("doctor")
    subparsers.add_parser("fingerprint")
    subparsers.add_parser("plan-install")
    subparsers.add_parser("install")
    subparsers.add_parser("plan-uninstall")
    subparsers.add_parser("uninstall")
    subparsers.add_parser("launch")
    args = parser.parse_args()

    try:
        if args.command == "detect":
            print(json.dumps([asdict(item) for item in detect_installs()], default=str, indent=2))
        elif args.command == "validate-pack":
            errors = validate_pack(choose_pack(args))
            print(json.dumps({"valid": not errors, "errors": errors}, indent=2))
            return 1 if errors else 0
        elif args.command == "status":
            record = manager_root() / "install-manifest.json"
            payload = {
                "installed": record.is_file(),
                "record": (
                    json.loads(record.read_text(encoding="utf-8"))
                    if record.is_file()
                    else None
                ),
                "detected": [asdict(item) for item in detect_installs()],
            }
            print(json.dumps(payload, default=str, indent=2))
        elif args.command == "doctor":
            print(json.dumps(installation_diagnostics(), default=str, indent=2))
        elif args.command == "fingerprint":
            print(json.dumps(fingerprint(choose_install(args)), indent=2))
        elif args.command == "plan-install":
            print(
                json.dumps(
                    plan_install(
                        args.runtime.resolve(),
                        choose_pack(args),
                        choose_install(args),
                    ),
                    default=str,
                    indent=2,
                )
            )
        elif args.command == "install":
            print(
                json.dumps(
                    install(args.runtime.resolve(), choose_pack(args), choose_install(args)),
                    indent=2,
                )
            )
        elif args.command == "plan-uninstall":
            print(json.dumps(plan_uninstall(), indent=2))
        elif args.command == "uninstall":
            print(json.dumps({"removed": uninstall()}, indent=2))
        elif args.command == "launch":
            print(
                json.dumps(
                    launch_game(args.game_dir.resolve() if args.game_dir else None),
                    indent=2,
                )
            )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
