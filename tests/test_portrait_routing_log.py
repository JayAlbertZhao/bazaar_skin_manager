import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "verify_portrait_routing_log.py"
SPEC = importlib.util.spec_from_file_location("portrait_log_verifier", MODULE_PATH)
VERIFIER = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(VERIFIER)


class PortraitRoutingLogVerifierTests(unittest.TestCase):
    def test_complete_local_and_opponent_route_passes(self) -> None:
        text = "\n".join(
            [
                "Loading [The Bazaar Skin Manager Runtime 1.3.1]",
                "Portrait route: slot=portrait_gameplay+portrait_background "
                "pack=local.pyg skin=Skin_PYG_01/A owner=local "
                "callSite=SkinAssetDataSO.GenerateEncounterData action=applied",
                "Portrait route: slot=portrait_gameplay+portrait_background "
                "pack=local.van skin=Skin_VAN_01/A owner=opponent "
                "callSite=SkinAssetDataSO.GenerateEncounterData action=retained",
                "PvpScreen standing ownership resolved: local player.",
                "Attached visible SkinEdit placement PvpScreen:",
                "PvpScreen standing ownership resolved: opponent.",
                "Attached visible-frame SkinEdit placement EndOfDayScreen:",
            ]
        )
        result = VERIFIER.verify_log(text, "1.3.1")
        self.assertTrue(result["passed"])
        self.assertEqual([], result["incomplete"])

    def test_opponent_apply_is_a_hard_failure(self) -> None:
        text = (
            "Portrait route: slot=portrait_gameplay pack=local.van "
            "skin=Skin_VAN_01/A owner=opponent "
            "callSite=SkinAssetDataSO.GenerateEncounterData action=applied"
        )
        result = VERIFIER.verify_log(text, "1.3.1")
        self.assertFalse(result["passed"])
        self.assertEqual(1, len(result["unsafe_applied_routes"]))

    def test_unknown_owner_is_retained_but_run_remains_incomplete(self) -> None:
        text = (
            "Loading [The Bazaar Skin Manager Runtime 1.3.1]\n"
            "Portrait route: slot=portrait_gameplay pack=local.van "
            "skin=Skin_VAN_01/A owner=unknown "
            "callSite=SkinAssetDataSO.GenerateEncounterData action=retained"
        )
        result = VERIFIER.verify_log(text, "1.3.1")
        self.assertFalse(result["passed"])
        self.assertTrue(result["checks"]["no_unsafe_portrait_apply"])
        self.assertFalse(result["checks"]["opponent_board_portrait_retained"])

    def test_mount_failure_marker_is_a_hard_failure(self) -> None:
        result = VERIFIER.verify_log(
            "No active camera could project the XZ SkinEdit overlay",
            "1.3.1",
        )
        self.assertFalse(result["passed"])
        self.assertEqual(1, len(result["runtime_errors"]))


if __name__ == "__main__":
    unittest.main()
