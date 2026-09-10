from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

from collectors.facts.model import (
    CandidateFact,
    DiagnosticFact,
    Evidence,
    EvidenceKind,
    Fact,
    SourceRange,
    SymbolFact,
)
from collectors.interop.jni_name_codec import JniNameError, encode_jni_name
from collectors.native.cpp_syntax import parse_cpp_file


SEMANTIC_PROFILE = "native-static-v0.1"
EXTRACTOR_VERSION = "0.1"


@dataclass(frozen=True)
class RegistrationParseResult:
    facts: tuple[Fact, ...]
    registrations: tuple[CandidateFact, ...]
    diagnostics: tuple[DiagnosticFact, ...]
    source_path: str
    grammar_name: str
    grammar_fingerprint: str


def _workspace_path(path: Path, repository: str) -> str:
    raw = path.as_posix()
    marker = f"/{repository.strip('/')}/"
    searchable = f"/{raw.lstrip('/')}"
    if marker in searchable:
        return repository.strip("/") + "/" + searchable.split(marker, 1)[1]
    return PurePosixPath(raw).as_posix()


def _line(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _without_comments(text: str) -> str:
    def preserve_lines(match: re.Match[str]) -> str:
        return "".join("\n" if value == "\n" else " " for value in match.group(0))

    return re.sub(r"//[^\n]*|/\*.*?\*/", preserve_lines, text, flags=re.DOTALL)


def _unquote(value: str) -> str:
    return bytes(value[1:-1], "utf-8").decode("unicode_escape")


def _evidence(repository: str, revision: str | None, content: bytes) -> Evidence:
    return Evidence(
        repository=repository,
        extractor="jni-cpp-structural",
        extractor_version=EXTRACTOR_VERSION,
        evidence_kind=EvidenceKind.SOURCE_DECLARATION,
        source_revision=revision,
        content_fingerprint=hashlib.sha256(content).hexdigest(),
        platform_identity="unknown",
        semantic_profile_version=SEMANTIC_PROFILE,
    )


def _registered_arrays(text: str) -> dict[str, tuple[tuple[str, str, str, int], ...]]:
    arrays: dict[str, tuple[tuple[str, str, str, int], ...]] = {}
    declaration = re.compile(
        r"\bJNINativeMethod\s+(?P<name>[A-Za-z_]\w*)\s*\[\s*\]\s*=\s*\{(?P<body>.*?)\}\s*;",
        re.DOTALL,
    )
    entry = re.compile(
        r"\{\s*(?P<method>\"(?:\\.|[^\"])*\")\s*,\s*"
        r"(?P<descriptor>\"(?:\\.|[^\"])*\")\s*,\s*"
        r"(?P<target>.*?)\}",
        re.DOTALL,
    )
    for match in declaration.finditer(text):
        values: list[tuple[str, str, str, int]] = []
        for item in entry.finditer(match.group("body")):
            identifiers = re.findall(r"[A-Za-z_]\w*", item.group("target"))
            excluded = {"void", "reinterpret_cast", "static_cast", "const_cast"}
            targets = [value for value in identifiers if value not in excluded]
            if not targets:
                continue
            values.append(
                (
                    _unquote(item.group("method")),
                    _unquote(item.group("descriptor")),
                    targets[-1],
                    _line(text, match.start("body") + item.start()),
                )
            )
        arrays[match.group("name")] = tuple(values)
    return arrays


def _registration_calls(text: str) -> tuple[tuple[str, str, str, int], ...]:
    calls: list[tuple[str, str, str, int]] = []
    helper = re.compile(
        r"(?P<api>jniRegisterNativeMethods|AndroidRuntime\s*::\s*registerNativeMethods)\s*"
        r"\(\s*[^,]+,\s*(?P<class>\"(?:\\.|[^\"])*\")\s*,\s*"
        r"(?P<array>[A-Za-z_]\w*)\s*,",
        re.DOTALL,
    )
    for match in helper.finditer(text):
        api = re.sub(r"\s+", "", match.group("api"))
        calls.append((api, _unquote(match.group("class")), match.group("array"), _line(text, match.start())))

    classes = {
        match.group("variable"): _unquote(match.group("class"))
        for match in re.finditer(
            r"\bjclass\s+(?P<variable>[A-Za-z_]\w*)\s*=.*?"
            r"(?:->|\.)\s*FindClass\s*\(\s*(?P<class>\"(?:\\.|[^\"])*\")\s*\)\s*;",
            text,
            re.DOTALL,
        )
    }
    direct = re.compile(
        r"(?:[A-Za-z_]\w*)\s*->\s*RegisterNatives\s*\(\s*"
        r"(?P<classvar>[A-Za-z_]\w*)\s*,\s*(?P<array>[A-Za-z_]\w*)\s*,",
        re.DOTALL,
    )
    for match in direct.finditer(text):
        class_name = classes.get(match.group("classvar"))
        if class_name:
            calls.append(("JNIEnv::RegisterNatives", class_name, match.group("array"), _line(text, match.start())))
    return tuple(calls)


def scan_jni_cpp_file(
    path: Path,
    repository: str,
    revision: str | None,
) -> RegistrationParseResult:
    content = path.read_bytes()
    source = content.decode("utf-8", errors="replace")
    structural = _without_comments(source)
    source_path = _workspace_path(path, repository)
    base = parse_cpp_file(path, "cpp", repository, revision)
    evidence = _evidence(repository, revision, content)
    arrays = _registered_arrays(structural)
    calls = _registration_calls(structural)
    facts: list[Fact] = list(base.facts)
    registrations: list[CandidateFact] = []

    native_symbols = sorted({entry[2] for entries in arrays.values() for entry in entries})
    for symbol in native_symbols:
        match = re.search(rf"\b{re.escape(symbol)}\s*\(", structural)
        if match is None:
            continue
        line = _line(structural, match.start())
        facts.append(
            SymbolFact(
                language="cpp",
                fact_kind="NATIVE_FUNCTION",
                logical_identity=f"cpp:function:{symbol}",
                source_range=SourceRange(source_path, line, line),
                evidence=evidence,
                properties={"export_name": symbol, "registration_target": True},
            )
        )

    for api, raw_class, array_name, call_line in calls:
        class_name = raw_class.replace("/", ".")
        for method, descriptor, symbol, entry_line in arrays.get(array_name, ()):
            try:
                encode_jni_name(class_name, method, descriptor)
            except JniNameError:
                continue
            managed_identity = f"managed-native:java:{class_name}#{method}{descriptor}"
            location = SourceRange(source_path, call_line, call_line)
            registrations.append(
                CandidateFact(
                    language="jni",
                    fact_kind="EXTRACTION_CANDIDATE",
                    logical_identity=(
                        f"candidate:explicit_jni_registration:{source_path}:"
                        f"{call_line}:{api}:{array_name}:{entry_line}:{method}"
                    ),
                    candidate_kind="explicit_jni_registration",
                    subject_identity=f"cpp:function:{symbol}",
                    proposed_identity=managed_identity,
                    source_range=location,
                    evidence=Evidence(**{**evidence.__dict__, "evidence_kind": EvidenceKind.EXPLICIT_REGISTRATION}),
                    properties={
                        "class_name": class_name,
                        "method_name": method,
                        "descriptor": descriptor,
                        "native_symbol": symbol,
                        "registration_api": api,
                        "registration_array": array_name,
                    },
                )
            )

    facts.sort(key=lambda item: item.logical_identity)
    registrations.sort(key=lambda item: item.logical_identity)
    return RegistrationParseResult(
        facts=tuple(facts),
        registrations=tuple(registrations),
        diagnostics=base.diagnostics,
        source_path=source_path,
        grammar_name=base.grammar_name,
        grammar_fingerprint=base.grammar_fingerprint,
    )


def scan_native_registrations(
    parse_results: Iterable[RegistrationParseResult],
) -> tuple[CandidateFact, ...]:
    result = [item for parsed in parse_results for item in parsed.registrations]
    return tuple(sorted(result, key=lambda item: item.logical_identity))
