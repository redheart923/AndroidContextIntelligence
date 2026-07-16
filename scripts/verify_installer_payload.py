from __future__ import annotations

import re
from pathlib import Path


class PayloadError(ValueError):
    pass


_QUOTED_HEREDOC = re.compile(
    r"^\s*cat\s*>\s*(?P<target>\S+)\s+<<'(?P<delimiter>[^']+)'\s*$"
)
_UNQUOTED_HEREDOC = re.compile(
    r"^\s*cat\s*>\s*(?P<target>\S+)\s+<<(?P<delimiter>[^\s'\"]+)\s*$"
)


def extract_payload(installer: Path, target: str) -> str:
    lines = installer.read_text(encoding="utf-8").splitlines(keepends=True)
    matches: list[str] = []
    index = 0
    while index < len(lines):
        header = lines[index].rstrip("\r\n")
        unquoted = _UNQUOTED_HEREDOC.match(header)
        if unquoted and unquoted.group("target") == target:
            raise PayloadError(f"unquoted heredoc delimiter for payload: {target}")

        quoted = _QUOTED_HEREDOC.match(header)
        if not quoted:
            index += 1
            continue

        delimiter = quoted.group("delimiter")
        payload_target = quoted.group("target")
        payload_start = index + 1
        payload_end = payload_start
        while payload_end < len(lines):
            if lines[payload_end].rstrip("\r\n") == delimiter:
                break
            payload_end += 1
        if payload_end == len(lines):
            if payload_target == target:
                raise PayloadError(f"unterminated payload: {target}")
            raise PayloadError(f"unterminated payload: {payload_target}")
        if payload_target == target:
            matches.append("".join(lines[payload_start:payload_end]))
        index = payload_end + 1

    if not matches:
        raise PayloadError(f"missing payload: {target}")
    if len(matches) > 1:
        raise PayloadError(f"duplicate payload: {target}")
    return matches[0]
