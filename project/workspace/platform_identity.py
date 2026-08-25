from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path


_VERSION_ASSIGNMENT = re.compile(
    r"^\s*PLATFORM_VERSION\s*:?=\s*(?P<value>[^\s#]+)",
    re.MULTILINE,
)


@dataclass(frozen=True)
class PlatformIdentity:
    name: str
    source: str
    fingerprint: str


def _fingerprint(name: str, source: str) -> str:
    payload = json.dumps(
        {"name": name, "source": source},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _identity(name: str, source: str) -> PlatformIdentity:
    return PlatformIdentity(name=name, source=source, fingerprint=_fingerprint(name, source))


def detect_platform_identity(
    aosp_root: Path,
    override: str | None,
) -> PlatformIdentity:
    if override is not None:
        value = override.strip()
        if not value:
            raise ValueError("platform identity override must not be empty")
        return _identity(value, "override")

    for relative in (
        "build/make/core/version_defaults.mk",
        "build/core/version_defaults.mk",
    ):
        path = aosp_root / relative
        if not path.is_file():
            continue
        match = _VERSION_ASSIGNMENT.search(path.read_text(encoding="utf-8"))
        if match is not None:
            return _identity(match.group("value"), relative)

    return _identity("unknown", "unresolved")
