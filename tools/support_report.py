"""Privacy-bounded diagnostic reports for user-initiated bug feedback."""

from __future__ import annotations

import json
import os
import platform
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


MAX_ERROR_LOG_CHARS = 48 * 1024
MAX_REPORT_CHARS = 64 * 1024
MAX_STORED_LOG_CHARS = 256 * 1024


def sanitize_diagnostic_text(text: object) -> str:
    """Remove common local identifiers and credentials without hiding stack frames."""
    value = str(text or "")
    replacements: list[tuple[str, str]] = []
    for environment_name, replacement in (
        ("LOCALAPPDATA", "%LOCALAPPDATA%"),
        ("APPDATA", "%APPDATA%"),
        ("USERPROFILE", "%USERPROFILE%"),
    ):
        raw = os.environ.get(environment_name)
        if raw:
            replacements.append((str(Path(raw)), replacement))
    replacements.append((str(Path.home()), "%USERPROFILE%"))
    for raw, replacement in sorted(replacements, key=lambda item: len(item[0]), reverse=True):
        value = re.sub(re.escape(raw), replacement, value, flags=re.IGNORECASE)
    value = re.sub(r"(?i)C:\\Users\\[^\\\s]+", r"C:\\Users\\<user>", value)
    value = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+", r"\1<redacted>", value)
    value = re.sub(
        r"(?i)((?:password|passwd|token|secret|authorization|cookie)\s*[:=]\s*)[^\s,;]+",
        r"\1<redacted>",
        value,
    )
    value = re.sub(
        r"(?i)([?&](?:access_token|token|key|secret)=)[^&#\s]+",
        r"\1<redacted>",
        value,
    )
    value = re.sub(r"\b7656119\d{10}\b", "<steam-id>", value)
    value = re.sub(
        r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b",
        "<email>",
        value,
        flags=re.IGNORECASE,
    )
    return value


def _sanitize_payload(value):
    if isinstance(value, dict):
        return {str(key): _sanitize_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_payload(item) for item in value]
    if isinstance(value, str):
        return sanitize_diagnostic_text(value)
    return value


def append_error_log(path: Path, title: str, details: str) -> Path:
    """Append a bounded, timestamped error entry instead of losing the previous one."""
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = ""
    if path.is_file():
        previous = path.read_text(encoding="utf-8-sig", errors="replace")
    entry = (
        f"\n=== {datetime.now(timezone.utc).isoformat()} · {title} ===\n"
        f"{details.rstrip()}\n"
    )
    combined = previous + entry
    if len(combined) > MAX_STORED_LOG_CHARS:
        combined = "[older entries truncated]\n" + combined[-MAX_STORED_LOG_CHARS:]
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(combined, encoding="utf-8")
    temporary.replace(path)
    return path


def build_diagnostic_report(
    *,
    manager_version: str,
    error_log: Path | None = None,
    game: dict | None = None,
    loader: dict | None = None,
    deployment: dict | None = None,
) -> str:
    """Build a text report users can inspect before pasting into a public issue."""
    error_text = "(没有已记录的错误日志)"
    if error_log and Path(error_log).is_file():
        error_text = Path(error_log).read_text(
            encoding="utf-8-sig", errors="replace"
        )[-MAX_ERROR_LOG_CHARS:]
    payload = {
        "manager_version": manager_version,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "frozen_build": bool(getattr(sys, "frozen", False)),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "game": game or {},
        "bepinex": loader or {},
        "deployment": deployment or {},
    }
    report = (
        "The Bazaar Skin Manager diagnostic report\n"
        "提交前请检查内容；路径、Steam ID、邮箱与常见凭据已自动脱敏。\n\n"
        "[Environment]\n"
        + json.dumps(_sanitize_payload(payload), ensure_ascii=False, indent=2, default=str)
        + "\n\n[Recent errors]\n"
        + error_text
    )
    sanitized = sanitize_diagnostic_text(report)
    if len(sanitized) > MAX_REPORT_CHARS:
        sanitized = sanitized[:MAX_REPORT_CHARS] + "\n[report truncated]\n"
    return sanitized.rstrip() + "\n"
