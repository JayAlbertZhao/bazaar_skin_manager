#!/usr/bin/env python3
"""Verify portrait and SkinEdit ownership routing from a BepInEx log."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


PORTRAIT_ROUTE = re.compile(
    r"Portrait route: "
    r"slot=(?P<slot>\S+) "
    r"pack=(?P<pack>\S+) "
    r"skin=(?P<skin>\S+) "
    r"owner=(?P<owner>\S+) "
    r"callSite=(?P<call_site>\S+) "
    r"action=(?P<action>\S+)"
)


def verify_log(text: str, runtime_version: str) -> dict:
    routes = [match.groupdict() for match in PORTRAIT_ROUTE.finditer(text)]
    unsafe_applied = [
        route
        for route in routes
        if route["action"] == "applied"
        and route["owner"] not in {"local", "preview", "diagnostic"}
    ]

    local_board_applied = any(
        route["call_site"] == "SkinAssetDataSO.GenerateEncounterData"
        and route["owner"] == "local"
        and route["action"] == "applied"
        for route in routes
    )
    opponent_board_retained = any(
        route["call_site"] == "SkinAssetDataSO.GenerateEncounterData"
        and route["owner"] == "opponent"
        and route["action"] == "retained"
        for route in routes
    )

    checks = {
        "runtime_loaded": (
            f"Loading [The Bazaar Skin Manager Runtime {runtime_version}]"
            in text
        ),
        "local_board_portrait_applied": local_board_applied,
        "opponent_board_portrait_retained": opponent_board_retained,
        "pvp_local_owner_resolved": (
            "PvpScreen standing ownership resolved: local player." in text
        ),
        "pvp_opponent_owner_resolved": (
            "PvpScreen standing ownership resolved: opponent." in text
        ),
        "pvp_local_standing_attached": (
            "Attached visible SkinEdit placement PvpScreen:" in text
        ),
        "end_of_day_standing_attached": (
            "Attached visible SkinEdit placement EndOfDayScreen:" in text
            or "Attached visible-frame SkinEdit placement EndOfDayScreen:"
            in text
        ),
        "no_unsafe_portrait_apply": not unsafe_applied,
    }

    runtime_errors = [
        marker
        for marker in (
            "Skipped PvpScreen standing replacement because local ownership "
            "could not be proven.",
            "Visible SkinEdit placement PvpScreen has no enabled renderer",
            "Timed out waiting for visible local SkinEdit placement "
            "EndOfDayScreen",
            "No active camera could project the XZ SkinEdit overlay",
        )
        if marker in text
    ]
    incomplete = [name for name, passed in checks.items() if not passed]
    passed = not incomplete and not runtime_errors
    return {
        "passed": passed,
        "checks": checks,
        "incomplete": incomplete,
        "unsafe_applied_routes": unsafe_applied,
        "runtime_errors": runtime_errors,
        "portrait_routes": routes,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Check local/opponent portrait and SkinEdit routing evidence in "
            "a Bazaar BepInEx log."
        )
    )
    parser.add_argument("log", type=Path)
    parser.add_argument("--runtime-version", default="1.4.14")
    args = parser.parse_args()

    if not args.log.is_file():
        parser.error(f"log does not exist: {args.log}")
    result = verify_log(
        args.log.read_text(encoding="utf-8", errors="replace"),
        args.runtime_version,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["unsafe_applied_routes"] or result["runtime_errors"]:
        return 2
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
