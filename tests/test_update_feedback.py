from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import support_report
import update_service


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()
        return False


class UpdateServiceTests(unittest.TestCase):
    def test_semantic_versions_are_compared_without_lexical_mistakes(self) -> None:
        self.assertTrue(update_service.is_newer_version("1.10.0", "1.9.9"))
        self.assertTrue(update_service.is_newer_version("v2.0.0", "1.99.99"))
        self.assertFalse(update_service.is_newer_version("1.4.3", "1.4.4"))
        self.assertTrue(update_service.is_newer_version("1.4.4", "1.4.4-preview"))

    def test_latest_release_requires_official_github_urls(self) -> None:
        payload = {
            "tag_name": "v1.4.4",
            "html_url": "https://github.com/JayAlbertZhao/bazaar_skin_manager/releases/tag/v1.4.4",
            "draft": False,
            "prerelease": False,
            "body": "notes",
            "assets": [
                {
                    "name": "TheBazaarModManager-Setup-1.4.4.exe",
                    "browser_download_url": "https://github.com/JayAlbertZhao/bazaar_skin_manager/releases/download/v1.4.4/TheBazaarModManager-Setup-1.4.4.exe",
                    "size": 123,
                    "digest": "sha256:" + "0" * 64,
                }
            ],
        }

        def opener(request, timeout):
            self.assertEqual(request.full_url, update_service.LATEST_RELEASE_API)
            self.assertGreater(timeout, 0)
            return FakeResponse(json.dumps(payload).encode("utf-8"))

        result = update_service.fetch_latest_release("1.4.3", opener=opener)
        self.assertTrue(result["update_available"])
        self.assertEqual(result["version"], "1.4.4")

        payload["assets"][0]["browser_download_url"] = "https://attacker.invalid/update.exe"
        with self.assertRaisesRegex(RuntimeError, "unexpected asset URL"):
            update_service.fetch_latest_release("1.4.3", opener=opener)

    def test_installer_download_requires_matching_published_hash(self) -> None:
        installer_bytes = b"verified release installer"
        digest = hashlib.sha256(installer_bytes).hexdigest()
        filename = "TheBazaarModManager-Setup-1.4.4.exe"
        installer_url = f"https://github.com/example/releases/{filename}"
        sidecar_url = installer_url + ".sha256"
        release = {
            "version": "1.4.4",
            "url": "https://github.com/example/releases/tag/v1.4.4",
            "assets": [
                {
                    "name": filename,
                    "url": installer_url,
                    "size": len(installer_bytes),
                    "digest": f"sha256:{digest}",
                },
                {
                    "name": filename + ".sha256",
                    "url": sidecar_url,
                    "size": 100,
                    "digest": "",
                },
            ],
        }

        def opener(request, timeout):
            if request.full_url == sidecar_url:
                return FakeResponse(f"{digest}  {filename}\n".encode("ascii"))
            if request.full_url == installer_url:
                return FakeResponse(installer_bytes)
            raise AssertionError(request.full_url)

        with tempfile.TemporaryDirectory() as temp:
            result = update_service.download_release_installer(
                release, Path(temp), opener=opener
            )
            output = Path(result["path"])
            self.assertEqual(output.read_bytes(), installer_bytes)
            self.assertEqual(result["sha256"], digest)

            release["assets"][0]["digest"] = "sha256:" + "f" * 64
            with self.assertRaisesRegex(RuntimeError, "disagree"):
                update_service.download_release_installer(
                    release, Path(temp), opener=opener
                )


class SupportReportTests(unittest.TestCase):
    def test_report_redacts_local_identity_and_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            log = root / "ui-error.log"
            log.write_text(
                "C:\\Users\\Alice\\secret.png\n"
                "token=ghp_not_for_public\n"
                "player 76561198012345678\n"
                "alice@example.com\n",
                encoding="utf-8",
            )
            with mock.patch.dict(
                "os.environ",
                {"USERPROFILE": "C:\\Users\\Alice"},
                clear=False,
            ):
                report = support_report.build_diagnostic_report(
                    manager_version="1.4.4",
                    error_log=log,
                    game={"path": "C:\\Users\\Alice\\Games\\The Bazaar"},
                )
            self.assertNotIn("Alice", report)
            self.assertNotIn("ghp_not_for_public", report)
            self.assertNotIn("76561198012345678", report)
            self.assertNotIn("alice@example.com", report)
            self.assertIn("<redacted>", report)
            self.assertIn("<steam-id>", report)

    def test_error_log_is_appended_instead_of_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "ui-error.log"
            support_report.append_error_log(log, "first", "one")
            support_report.append_error_log(log, "second", "two")
            text = log.read_text(encoding="utf-8")
            self.assertIn("first", text)
            self.assertIn("second", text)

    def test_issue_url_prefills_version_but_not_the_log(self) -> None:
        url = support_report.github_issue_url("1.4.4")
        parsed = urllib.parse.urlparse(url)
        query = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(parsed.netloc, "github.com")
        self.assertIn("v1.4.4", query["title"][0])
        self.assertIn("粘贴", query["body"][0])


if __name__ == "__main__":
    unittest.main()
