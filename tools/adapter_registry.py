#!/usr/bin/env python3
"""Verified adapter registry and installed skin discovery."""

from __future__ import annotations

import json
import re
import sys
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(
    getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1])
)
DEFAULT_ADAPTER_DIRECTORY = PROJECT_ROOT / "manager" / "adapters"
CATALOG_RELATIVE = (
    Path("TheBazaar_Data") / "StreamingAssets" / "aa" / "catalog.bin"
)
SKIN_TOKEN = re.compile(rb"Skin_([A-Z]{3})_([0-9]{2})/([A-Z])")


@dataclass(frozen=True)
class AdapterRecord:
    adapter_id: str
    adapter_version: int
    hero: str
    skin: str
    skin_name_contains: str
    supported_builds: tuple[str, ...]
    path: Path
    payload: dict

    def supports_build(self, build_id: str | None) -> bool:
        return bool(build_id and build_id in self.supported_builds)


class AdapterRegistry:
    def __init__(self, records: list[AdapterRecord]):
        self.records = tuple(records)
        self._by_target: dict[tuple[str, str], AdapterRecord] = {}
        self._by_id: dict[str, AdapterRecord] = {}
        for record in records:
            target_key = (record.hero.casefold(), record.skin.casefold())
            id_key = record.adapter_id.casefold()
            if target_key in self._by_target:
                raise ValueError(
                    f"Duplicate adapter target: {record.hero} / {record.skin}"
                )
            if id_key in self._by_id:
                raise ValueError(f"Duplicate adapter id: {record.adapter_id}")
            self._by_target[target_key] = record
            self._by_id[id_key] = record

    @classmethod
    def load(cls, directory: Path = DEFAULT_ADAPTER_DIRECTORY) -> "AdapterRegistry":
        def merge_recipe(base: dict, override: dict) -> dict:
            merged = deepcopy(base)
            for key, value in override.items():
                if (
                    isinstance(value, dict)
                    and isinstance(merged.get(key), dict)
                ):
                    merged[key] = merge_recipe(merged[key], value)
                else:
                    merged[key] = deepcopy(value)
            return merged

        raw_payloads: list[tuple[Path, dict]] = []
        for path in sorted(directory.glob("*.json")):
            raw_payloads.append(
                (path, json.loads(path.read_text(encoding="utf-8")))
            )
        payloads_by_id = {
            str(payload.get("id") or "").casefold(): payload
            for _path, payload in raw_payloads
        }

        def resolve_recipe(payload: dict, stack: tuple[str, ...] = ()) -> dict:
            recipe = payload.get("authoring_recipe") or {}
            parent_id = str(recipe.get("inherits_adapter") or "").strip()
            if not parent_id:
                return recipe
            key = parent_id.casefold()
            if key in stack:
                raise ValueError(
                    "Circular authoring recipe inheritance: "
                    + " -> ".join((*stack, key))
                )
            parent = payloads_by_id.get(key)
            if parent is None:
                raise ValueError(
                    f"Unknown inherited authoring adapter: {parent_id}"
                )
            inherited = resolve_recipe(parent, (*stack, key))
            resolved = merge_recipe(inherited, recipe.get("overrides") or {})
            # Identity remains explicit in every child adapter so build
            # metadata can advertise capabilities without interpreting the
            # complete rendering recipe.
            resolved["id"] = recipe.get("id") or inherited.get("id")
            resolved["version"] = int(
                recipe.get("version") or inherited.get("version") or 0
            )
            return resolved

        records: list[AdapterRecord] = []
        for path, raw_payload in raw_payloads:
            payload = deepcopy(raw_payload)
            if payload.get("authoring_recipe"):
                payload["authoring_recipe"] = resolve_recipe(raw_payload)
            target = payload.get("target") or {}
            adapter_id = str(payload.get("id") or "").strip()
            hero = str(target.get("hero") or "").strip()
            skin = str(target.get("skin") or "").strip()
            name_contains = str(target.get("skin_name_contains") or "").strip()
            builds = tuple(str(value) for value in payload.get("supported_builds") or [])
            if not all((adapter_id, hero, skin, name_contains)):
                raise ValueError(f"Incomplete adapter identity: {path}")
            if not builds:
                raise ValueError(f"Adapter has no verified game builds: {path}")
            records.append(
                AdapterRecord(
                    adapter_id=adapter_id,
                    adapter_version=int(payload.get("adapter_version") or 1),
                    hero=hero,
                    skin=skin,
                    skin_name_contains=name_contains,
                    supported_builds=builds,
                    path=path,
                    payload=payload,
                )
            )
        if not records:
            raise ValueError(f"No adapters found in {directory}")
        return cls(records)

    def find(self, hero: str, skin: str) -> AdapterRecord | None:
        return self._by_target.get((hero.casefold(), skin.casefold()))

    def find_by_id(self, adapter_id: str) -> AdapterRecord | None:
        return self._by_id.get(adapter_id.casefold())

    def default(self) -> AdapterRecord:
        marked = [
            record
            for record in self.records
            if record.payload.get("default_authoring") is True
        ]
        if len(marked) > 1:
            raise ValueError("Multiple adapters are marked default_authoring.")
        return marked[0] if marked else self.records[0]

    def support_status(
        self,
        hero: str,
        skin: str,
        build_id: str | None,
    ) -> str:
        adapter = self.find(hero, skin)
        if adapter is None:
            return "detected_unmapped"
        if not adapter.supports_build(build_id):
            return "game_update_required"
        return "supported"


def discover_installed_skin_ids(game_dir: Path) -> dict[str, list[str]]:
    """Read exact Skin_XXX_00/A tokens from the installed Addressables catalog."""
    catalog = game_dir.resolve() / CATALOG_RELATIVE
    if not catalog.is_file():
        return {}
    matches: dict[str, set[str]] = {}
    for code_bytes, number_bytes, variant_bytes in SKIN_TOKEN.findall(
        catalog.read_bytes()
    ):
        code = code_bytes.decode("ascii")
        number = number_bytes.decode("ascii")
        variant = variant_bytes.decode("ascii")
        matches.setdefault(code, set()).add(f"Skin_{code}_{number}/{variant}")
    return {code: sorted(values) for code, values in sorted(matches.items())}


def skin_name_contains(skin_id: str) -> str:
    match = re.fullmatch(r"Skin_([A-Z]{3})_([0-9]{2})/([A-Z])", skin_id)
    if not match:
        raise ValueError(f"Unsupported discovered skin id: {skin_id}")
    code, number, variant = match.groups()
    return f"{code}_{number}{variant.casefold()}"


def enrich_catalog(
    base_catalog: dict,
    registry: AdapterRegistry,
    *,
    game_dir: Path | None,
    build_id: str | None,
) -> dict:
    """Merge installed catalog skins with verified adapter support states."""
    payload = json.loads(json.dumps(base_catalog))
    discovered = discover_installed_skin_ids(game_dir) if game_dir else {}
    for hero in payload.get("heroes") or []:
        code = str(hero.get("asset_code") or "").upper()
        static = {item["id"]: item for item in hero.get("skins") or []}
        installed_ids = discovered.get(code) or []
        skin_ids = installed_ids or sorted(static)
        skins = []
        for skin_id in skin_ids:
            fallback = static.get(skin_id) or {}
            name_contains = fallback.get("name_contains") or skin_name_contains(skin_id)
            adapter = registry.find(hero["id"], skin_id)
            status = registry.support_status(hero["id"], skin_id, build_id)
            skins.append(
                {
                    "id": skin_id,
                    "display_name": fallback.get("display_name") or skin_id.split("_", 2)[-1],
                    "name_contains": name_contains,
                    "detected_from_game": skin_id in installed_ids,
                    "adapter_id": adapter.adapter_id if adapter else None,
                    "deployment_status": status,
                }
            )
        hero["skins"] = skins
        hero["runtime_supported"] = any(
            item["deployment_status"] == "supported" for item in skins
        )
        hero["audio_supported"] = any(
            (
                (adapter := registry.find(hero["id"], item["id"])) is not None
                and bool(
                    adapter.payload.get("audio_template")
                    or adapter.payload.get("audio_template_ref")
                )
                and item["deployment_status"] == "supported"
            )
            for item in skins
        )
    payload["skin_discovery"] = {
        "source": "installed_addressables_catalog" if discovered else "offline_fallback",
        "build_id": build_id,
    }
    return payload
