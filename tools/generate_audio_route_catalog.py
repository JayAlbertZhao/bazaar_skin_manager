#!/usr/bin/env python3
"""Generate the public all-hero voice routing catalog from owned-game metadata.

The input catalog contains only route GUIDs and sample metadata. No game audio
payload is copied into the repository. The generated catalog is the compact,
runtime-authoring contract consumed by the skin manager.
"""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "research" / "high-score-go" / "hero-voice-catalog.json"
DEFAULT_OUTPUT = ROOT / "manager" / "audio-route-catalog.json"
BASE_ADAPTER = ROOT / "manager" / "adapters" / "mak-default.json"

HERO_IDENTITIES = {
    # source id: (manager id, FMOD folder, FMOD event-name prefix)
    "Vanessa": ("Vanessa", "Vanessa", "Vanessa"),
    "Pygmalien": ("Pygmalien", "Pyg", "Pyg"),
    "Dooley": ("Dooley", "Dooley", "Dooley"),
    "Mak": ("Mak", "Mak", "Mak"),
    "Stelle": ("Stelle", "Stelle", "Stelle"),
    "Jules": ("Jules", "Jules", "Jules"),
    # The shipped bank uses the historical `Karnnok` folder typo while the
    # event names and manager identity use `Karnok`.
    "Karnok": ("Karnok", "Karnnok", "Karnok"),
    "TheDragons": ("Hero8", "TheDragons", "TheDragons"),
}

HOOK_BY_LOGICAL_SLOT = {
    "Hit_React.default": "Hit React",
    "Idle.default": "Idle",
    "Last_Life.default": "Last Life",
    "Level_Up.default": "Level Up",
    "MultiClick.default": "MultiClick",
    "No_Buy_Gold.default": "No Buy Gold",
    "No_Buy_Space.default": "No Buy Space",
    "PvE_VictoryDefeat.Defeat": "PvE VictoryDefeat",
    "PvE_VictoryDefeat.Victory": "PvE VictoryDefeat",
    "PvP_Intro.Left": "PvP Intro",
    "PvP_Intro.Right": "PvP Intro",
    "PvP_VictoryDefeat.Defeat": "PvP VictoryDefeat",
    "PvP_VictoryDefeat.Victory": "PvP VictoryDefeat",
    "Run_VictoryDefeat.Defeat": "Run VictoryDefeat",
    "Run_VictoryDefeat.Perfect": "Run VictoryDefeat",
    "Run_VictoryDefeat.Victory": "Run VictoryDefeat",
    "Upgrade.default": "Upgrade",
}


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def build_catalog(source: dict, base_adapter: dict) -> dict:
    base = base_adapter["audio_template"]
    base_routes = base["routes"]
    menu_routes = [
        deepcopy(route)
        for route in base_routes
        if route.get("category") == "menu_voice"
    ]
    heroes = []
    for source_hero in source.get("heroes") or []:
        source_id = str(source_hero.get("hero_id") or "")
        if source_id not in HERO_IDENTITIES:
            continue
        manager_hero, event_folder, event_prefix = HERO_IDENTITIES[source_id]
        hooks = {
            str(hook.get("name")): hook
            for hook in source_hero.get("hooks") or []
            if hook.get("name")
        }
        routes = []
        for base_route in base_routes:
            logical_slot = str(base_route.get("logical_slot") or "")
            hook_name = HOOK_BY_LOGICAL_SLOT.get(logical_slot)
            if hook_name is None:
                continue
            hook = hooks.get(hook_name)
            if hook is None:
                raise ValueError(f"{source_id} is missing hook {hook_name}")
            route = deepcopy(base_route)
            route["event_guid"] = str(hook["event_guid"]).lower()
            mak_marker = "/VO_Mak_"
            suffix = str(base_route["event_path"]).split(mak_marker, 1)[1]
            route["event_path"] = (
                f"event:/VO/Hero/{event_folder}/VO_{event_prefix}_{suffix}"
            )
            route["variants"] = []
            routes.append(route)
        if len(routes) != 17:
            raise ValueError(
                f"{source_id} generated {len(routes)} hero routes, expected 17"
            )
        heroes.append(
            {
                "hero": manager_hero,
                "source_hero": source_id,
                "audio_object_names": [source_hero["audio_object"]],
                "event_folder": event_folder,
                "event_prefix": event_prefix,
                "routes": routes,
            }
        )
    if len(heroes) != len(HERO_IDENTITIES):
        raise ValueError(
            f"generated {len(heroes)} heroes, expected {len(HERO_IDENTITIES)}"
        )
    return {
        "schema_version": 1,
        "id": "hero-standard-v1",
        "source": {
            "steam_build": source["source"]["steam_build"],
            "hero_bank_bundle_sha256": source["source"][
                "hero_bank_bundle_sha256"
            ],
            "payload_policy": "route_metadata_only_no_game_audio",
        },
        "supported_builds": base["target"]["supported_builds"],
        "audio_format": base["audio_format"],
        "playback": base["playback"],
        "menu_routes": menu_routes,
        "heroes": heroes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = build_catalog(load_json(args.source), load_json(BASE_ADAPTER))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
