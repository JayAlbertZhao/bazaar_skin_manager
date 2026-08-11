"""Verified GitHub Release updates for the frozen Windows manager."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable


GITHUB_REPOSITORY = "JayAlbertZhao/bazaar_skin_manager"
LATEST_RELEASE_API = (
    f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
)
RELEASES_URL = f"https://github.com/{GITHUB_REPOSITORY}/releases"
MAX_INSTALLER_BYTES = 200 * 1024 * 1024
MAX_SIDECAR_BYTES = 4096
USER_AGENT = "TheBazaarSkinManager-Updater"


def version_key(value: str) -> tuple[int, int, int, tuple[object, ...]]:
    """Return a predictable comparison key for the project's release tags."""
    text = str(value or "").strip()
    if text.casefold().startswith("v"):
        text = text[1:]
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?", text)
    if not match:
        raise ValueError(f"invalid release version: {value}")
    prerelease = match.group(4)
    # Stable releases sort after prereleases with the same numeric version.
    suffix: tuple[object, ...] = (1,) if prerelease is None else (0, prerelease)
    return int(match.group(1)), int(match.group(2)), int(match.group(3)), suffix


def is_newer_version(candidate: str, current: str) -> bool:
    return version_key(candidate) > version_key(current)


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


def _read_limited(response, limit: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(min(1024 * 1024, limit + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise RuntimeError("GitHub response exceeded the permitted size")
        chunks.append(chunk)
    return b"".join(chunks)


def fetch_latest_release(
    current_version: str,
    *,
    opener: Callable = urllib.request.urlopen,
    timeout: float = 12.0,
) -> dict:
    """Fetch and validate the latest stable release metadata from GitHub."""
    with opener(_request(LATEST_RELEASE_API), timeout=timeout) as response:
        payload = json.loads(_read_limited(response, 2 * 1024 * 1024).decode("utf-8"))
    if payload.get("draft") or payload.get("prerelease"):
        raise RuntimeError("GitHub latest release is not a stable published release")
    tag = str(payload.get("tag_name") or "").strip()
    version = tag[1:] if tag.casefold().startswith("v") else tag
    version_key(version)
    html_url = str(payload.get("html_url") or "")
    parsed_release_url = urllib.parse.urlparse(html_url)
    if parsed_release_url.scheme != "https" or parsed_release_url.netloc.casefold() != "github.com":
        raise RuntimeError("GitHub release metadata contains an unexpected release URL")
    assets = []
    for item in payload.get("assets") or []:
        name = str(item.get("name") or "")
        url = str(item.get("browser_download_url") or "")
        if not name or not url:
            continue
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or parsed.netloc.casefold() != "github.com":
            raise RuntimeError("GitHub release metadata contains an unexpected asset URL")
        assets.append(
            {
                "name": name,
                "url": url,
                "size": int(item.get("size") or 0),
                "digest": str(item.get("digest") or ""),
            }
        )
    return {
        "version": version,
        "tag": tag,
        "url": html_url,
        "notes": str(payload.get("body") or ""),
        "assets": assets,
        "update_available": is_newer_version(version, current_version),
    }


def _asset(release: dict, name: str) -> dict:
    matches = [item for item in release.get("assets") or [] if item.get("name") == name]
    if len(matches) != 1:
        raise RuntimeError(f"GitHub Release does not contain exactly one {name}")
    return matches[0]


def _expected_installer_hash(
    release: dict,
    installer: dict,
    *,
    opener: Callable,
    timeout: float,
) -> str:
    filename = installer["name"]
    digest = str(installer.get("digest") or "")
    api_hash = digest[7:].casefold() if digest.casefold().startswith("sha256:") else ""
    sidecar = _asset(release, filename + ".sha256")
    with opener(_request(sidecar["url"]), timeout=timeout) as response:
        text = _read_limited(response, MAX_SIDECAR_BYTES).decode("ascii", errors="strict")
    match = re.fullmatch(
        rf"\s*([0-9a-fA-F]{{64}})\s+\*?{re.escape(filename)}\s*",
        text,
    )
    if not match:
        raise RuntimeError("GitHub installer checksum sidecar is malformed")
    sidecar_hash = match.group(1).casefold()
    if api_hash and api_hash != sidecar_hash:
        raise RuntimeError("GitHub asset digest and checksum sidecar disagree")
    return sidecar_hash


def download_release_installer(
    release: dict,
    destination_root: Path,
    *,
    opener: Callable = urllib.request.urlopen,
    timeout: float = 45.0,
) -> dict:
    """Download the version-matched installer and verify its published SHA-256."""
    version = str(release.get("version") or "")
    version_key(version)
    filename = f"TheBazaarModManager-Setup-{version}.exe"
    installer = _asset(release, filename)
    announced_size = int(installer.get("size") or 0)
    if announced_size <= 0 or announced_size > MAX_INSTALLER_BYTES:
        raise RuntimeError("GitHub installer has an invalid or excessive size")
    expected_hash = _expected_installer_hash(
        release,
        installer,
        opener=opener,
        timeout=timeout,
    )
    destination = Path(destination_root).resolve() / f"v{version}" / filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = destination.with_suffix(destination.suffix + ".part")
    digest = hashlib.sha256()
    received = 0
    try:
        with opener(_request(installer["url"]), timeout=timeout) as response, staging.open("wb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > MAX_INSTALLER_BYTES:
                    raise RuntimeError("Downloaded installer exceeded the permitted size")
                digest.update(chunk)
                output.write(chunk)
        if received != announced_size:
            raise RuntimeError(
                f"Downloaded installer size mismatch: expected {announced_size}, got {received}"
            )
        actual_hash = digest.hexdigest()
        if actual_hash != expected_hash:
            raise RuntimeError("Downloaded installer SHA-256 verification failed")
        staging.replace(destination)
    except Exception:
        staging.unlink(missing_ok=True)
        raise
    return {
        "path": str(destination),
        "version": version,
        "sha256": expected_hash,
        "bytes": received,
        "release_url": release.get("url"),
    }


def launch_verified_installer(path: Path) -> subprocess.Popen:
    """Launch the already-verified per-user installer for an in-place update."""
    installer = Path(path).resolve()
    if not installer.is_file() or installer.suffix.casefold() != ".exe":
        raise FileNotFoundError(f"verified installer is unavailable: {installer}")
    return subprocess.Popen(
        [
            str(installer),
            "/SP-",
            "/SILENT",
            "/NORESTART",
            "/CLOSEAPPLICATIONS",
        ],
        close_fds=True,
    )
