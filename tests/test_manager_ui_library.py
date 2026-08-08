from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from bazaar_skin_manager_ui import (  # noqa: E402
    complete_pack_identity,
    deployment_state,
    ellipsize,
)


class ManagerAssetLibraryTests(unittest.TestCase):
    def _archive(self, root: Path, manifests: dict[str, dict]) -> Path:
        archive = root / "pack.zip"
        with zipfile.ZipFile(archive, "w") as package:
            for name, payload in manifests.items():
                package.writestr(name, json.dumps(payload))
        return archive

    def test_complete_pack_identity_reads_nested_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            archive = self._archive(
                Path(temp),
                {
                    "example-pack/mod.json": {
                        "id": "example.skin",
                        "name": "Example Skin",
                    }
                },
            )
            self.assertEqual(
                complete_pack_identity(archive),
                ("example.skin", "Example Skin"),
            )

    def test_complete_pack_identity_rejects_ambiguous_archives(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            archive = self._archive(
                Path(temp),
                {
                    "first/mod.json": {"id": "first"},
                    "second/mod.json": {"id": "second"},
                },
            )
            with self.assertRaisesRegex(ValueError, "只能包含一个"):
                complete_pack_identity(archive)

    def test_control_center_exposes_inventory_and_deployment_pages(self) -> None:
        source = (ROOT / "tools" / "bazaar_skin_manager_ui.py").read_text(
            encoding="utf-8"
        )
        for required in (
            'text="资产包库"',
            'text="部署"',
            'text="资产包导入"',
            'text="导入资产包 ZIP…"',
            'text="编辑内容"',
            'text="应用到…"',
            'text="打开文件夹"',
            'text="清空全部素材"',
            "def _library_delete(self)",
            "def _refresh_library(self)",
            "def _library_cover_path(",
            "def _library_apply_to_targets(self)",
            "self.deployment_assignments",
            "library_actions.grid(row=1",
        ):
            self.assertIn(required, source)
        self.assertNotIn('sections.add(target_panel, text="目标与游戏")', source)

    def test_library_text_is_bounded_before_layout(self) -> None:
        self.assertEqual(ellipsize("abcdef", 5), "abcd…")
        self.assertEqual(ellipsize("abc", 5), "abc")

    def test_deployment_state_distinguishes_plan_from_installed_state(self) -> None:
        installed = {"id": "pack.for.dooley", "version": "1.0.0"}
        self.assertEqual(
            deployment_state(
                enabled=True,
                selected_pack_id="pack",
                selected_version="1.0.0",
                expected_runtime_id="pack.for.dooley",
                installed=installed,
            ),
            "已部署",
        )
        self.assertEqual(
            deployment_state(
                enabled=True,
                selected_pack_id="other",
                selected_version="1.0.0",
                expected_runtime_id="other.for.dooley",
                installed=installed,
            ),
            "有更改（待部署）",
        )
        self.assertEqual(
            deployment_state(
                enabled=False,
                selected_pack_id="pack",
                selected_version="1.0.0",
                expected_runtime_id="pack.for.dooley",
                installed=installed,
            ),
            "已部署（待移除）",
        )


if __name__ == "__main__":
    unittest.main()
