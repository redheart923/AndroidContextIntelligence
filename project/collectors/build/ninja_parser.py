from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Iterable, Mapping

from collectors.facts.model import (
    BuildActionFact,
    CandidateFact,
    DiagnosticFact,
    Evidence,
    EvidenceKind,
    Fact,
    RelationFact,
    SourceRange,
    SymbolFact,
)


SEMANTIC_PROFILE = "native-static-v0.1"


@dataclass(frozen=True)
class NinjaParseResult:
    facts: tuple[Fact, ...]
    relations: tuple[RelationFact, ...]
    candidates: tuple[CandidateFact, ...]
    diagnostics: tuple[DiagnosticFact, ...]


@dataclass
class _Rule:
    name: str
    line: int
    bindings: dict[str, str]


@dataclass
class _Build:
    line: int
    explicit_outputs: list[str]
    implicit_outputs: list[str]
    rule: str
    explicit_inputs: list[str]
    implicit_inputs: list[str]
    order_only_inputs: list[str]
    bindings: dict[str, str]


def _logical_lines(text: str) -> Iterable[tuple[int, str]]:
    physical = text.splitlines()
    index = 0
    while index < len(physical):
        start = index + 1
        value = physical[index]
        while value.rstrip().endswith("$") and index + 1 < len(physical):
            value = value.rstrip()[:-1] + physical[index + 1].lstrip()
            index += 1
        yield start, value
        index += 1


def _split_inputs(value: str) -> tuple[list[str], list[str], list[str]]:
    explicit: list[str] = []
    implicit: list[str] = []
    order_only: list[str] = []
    target = explicit
    for token in value.split():
        if token == "|":
            target = implicit
        elif token == "||":
            target = order_only
        else:
            target.append(token)
    return explicit, implicit, order_only


def _split_outputs(value: str) -> tuple[list[str], list[str]]:
    explicit: list[str] = []
    implicit: list[str] = []
    target = explicit
    for token in value.split():
        if token == "|":
            target = implicit
        else:
            target.append(token)
    return explicit, implicit


def parse_ninja(
    text: str,
    source_path: str,
    repository: str,
    revision: str | None,
    platform_identity: str,
) -> NinjaParseResult:
    fingerprint = hashlib.sha256(text.encode("utf-8")).hexdigest()
    base_evidence = Evidence(
        repository=repository,
        extractor="ninja-state-machine",
        extractor_version="0.1",
        evidence_kind=EvidenceKind.GENERATED_BUILD_ARTIFACT,
        source_revision=revision,
        content_fingerprint=fingerprint,
        platform_identity=platform_identity,
        semantic_profile_version=SEMANTIC_PROFILE,
    )

    def evidence(kind: EvidenceKind) -> Evidence:
        return Evidence(**{**base_evidence.__dict__, "evidence_kind": kind})

    variables: dict[str, str] = {}
    rules: list[_Rule] = []
    builds: list[_Build] = []
    metadata: list[tuple[str, str, int, dict[str, str]]] = []
    current: _Rule | _Build | tuple[str, str, int, dict[str, str]] | None = None

    def parse_error(line: int, message: str) -> NinjaParseResult:
        location = SourceRange(source_path, line, line, 1, 1)
        item = DiagnosticFact(
            language="ninja",
            fact_kind="DIAGNOSTIC",
            logical_identity=f"diagnostic:invalid_ninja_statement:{source_path}:{line}",
            category="parse_error",
            reason_code="invalid_ninja_statement",
            message=message,
            source_range=location,
            evidence=evidence(EvidenceKind.UNRESOLVED_REFERENCE),
            properties={},
        )
        return NinjaParseResult((), (), (), (item,))

    for line_number, raw_line in _logical_lines(text):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if raw_line[0].isspace():
            if current is None or "=" not in raw_line:
                return parse_error(line_number, "orphan or malformed Ninja binding")
            key, value = raw_line.strip().split("=", 1)
            binding = (key.strip(), value.strip())
            if isinstance(current, (_Rule, _Build)):
                current.bindings[binding[0]] = binding[1]
            else:
                current[3][binding[0]] = binding[1]
            continue

        current = None
        line = raw_line.strip()
        if line.startswith("rule "):
            name = line.removeprefix("rule ").strip()
            if not name:
                return parse_error(line_number, "rule requires a name")
            rule = _Rule(name, line_number, {})
            rules.append(rule)
            current = rule
        elif line.startswith("pool "):
            name = line.removeprefix("pool ").strip()
            if not name:
                return parse_error(line_number, "pool requires a name")
            item = ("NINJA_POOL", name, line_number, {})
            metadata.append(item)
            current = item
        elif line.startswith("build "):
            body = line.removeprefix("build ")
            if ":" not in body:
                return parse_error(line_number, "build statement requires ':'")
            output_text, remainder = body.split(":", 1)
            words = remainder.strip().split(maxsplit=1)
            if not words:
                return parse_error(line_number, "build statement requires a rule")
            explicit_outputs, implicit_outputs = _split_outputs(output_text)
            if not explicit_outputs:
                return parse_error(line_number, "build statement requires an output")
            explicit, implicit, order_only = _split_inputs(
                words[1] if len(words) == 2 else ""
            )
            build = _Build(
                line_number,
                explicit_outputs,
                implicit_outputs,
                words[0],
                explicit,
                implicit,
                order_only,
                {},
            )
            builds.append(build)
            current = build
        elif line.startswith("include "):
            metadata.append(("NINJA_INCLUDE", line[8:].strip(), line_number, {}))
        elif line.startswith("subninja "):
            metadata.append(("NINJA_SUBNINJA", line[9:].strip(), line_number, {}))
        elif line.startswith("default "):
            for target in line[8:].split():
                metadata.append(("NINJA_DEFAULT", target, line_number, {}))
        elif "=" in line:
            key, value = line.split("=", 1)
            variables[key.strip()] = value.strip()
        else:
            return parse_error(line_number, f"unsupported Ninja statement: {line}")

    def location(line: int) -> SourceRange:
        return SourceRange(source_path, line, line, 1, 1)

    def expand(value: str) -> str:
        result = value
        for _ in range(10):
            updated = re.sub(
                r"\$(?:\{([A-Za-z0-9_.-]+)\}|([A-Za-z0-9_.-]+))",
                lambda match: variables.get(
                    match.group(1) or match.group(2) or "", match.group(0)
                ),
                result,
            )
            if updated == result:
                break
            result = updated
        return result

    facts: list[Fact] = []
    relations: list[RelationFact] = []
    candidates: list[CandidateFact] = []
    diagnostics: list[DiagnosticFact] = []
    rule_identities: dict[str, str] = {}
    for rule in rules:
        identity = f"build-rule:{source_path}:{rule.name}"
        rule_identities[rule.name] = identity
        command = expand(rule.bindings.get("command", ""))
        properties: dict[str, object] = {
            key: value
            for key, value in rule.bindings.items()
            if key in {"description", "pool", "depfile", "deps", "generator", "restat"}
        }
        properties["command_sha256"] = hashlib.sha256(
            command.encode("utf-8")
        ).hexdigest()
        facts.append(
            SymbolFact(
                language="ninja",
                fact_kind="BUILD_RULE",
                logical_identity=identity,
                source_range=location(rule.line),
                evidence=base_evidence,
                properties=properties,
            )
        )

    for kind, name, line, bindings in metadata:
        facts.append(
            SymbolFact(
                language="ninja",
                fact_kind=kind,
                logical_identity=f"{kind.lower()}:{source_path}:{name}",
                source_range=location(line),
                evidence=base_evidence,
                properties={"value": name, **bindings},
            )
        )

    producers: dict[str, list[_Build]] = {}
    for build in builds:
        for output in (*build.explicit_outputs, *build.implicit_outputs):
            producers.setdefault(output, []).append(build)

    artifact_names = {
        value
        for build in builds
        for value in (*build.explicit_outputs, *build.implicit_outputs)
    }
    artifact_names.update(
        value
        for build in builds
        for value in (*build.explicit_inputs, *build.implicit_inputs, *build.order_only_inputs)
        if value in producers
    )
    for name in sorted(artifact_names):
        facts.append(
            SymbolFact(
                language="ninja",
                fact_kind="BUILD_ARTIFACT",
                logical_identity=f"build-artifact:{name}",
                source_range=location(producers[name][0].line),
                evidence=base_evidence,
                properties={"path": name},
            )
        )

    for output, output_producers in sorted(producers.items()):
        if len(output_producers) <= 1:
            continue
        subject = f"build-artifact:{output}"
        conflict_location = location(output_producers[0].line)
        candidates.append(
            CandidateFact(
                language="ninja",
                fact_kind="EXTRACTION_CANDIDATE",
                logical_identity=f"candidate:duplicate_output_producer:{source_path}:{output}",
                candidate_kind="duplicate_output_producer",
                subject_identity=subject,
                proposed_identity=None,
                source_range=conflict_location,
                evidence=evidence(EvidenceKind.AMBIGUOUS_CANDIDATE),
                properties={"producer_lines": [item.line for item in output_producers]},
            )
        )
        diagnostics.append(
            DiagnosticFact(
                language="ninja",
                fact_kind="DIAGNOSTIC",
                logical_identity=f"diagnostic:duplicate_output_producer:{source_path}:{output}",
                category="build_resolution",
                reason_code="duplicate_output_producer",
                message=f"Ninja output {output!r} has multiple producers",
                source_range=conflict_location,
                evidence=evidence(EvidenceKind.UNRESOLVED_REFERENCE),
                properties={"producer_lines": [item.line for item in output_producers]},
            )
        )

    def relation(
        build: _Build,
        action: str,
        kind: str,
        target: str,
        properties: Mapping[str, object] | None = None,
    ) -> None:
        relations.append(
            RelationFact(
                language="ninja",
                fact_kind=kind,
                logical_identity=f"{kind}:{action}->{target}",
                from_identity=action,
                to_identity=target,
                source_range=location(build.line),
                evidence=evidence(EvidenceKind.RESOLVED_REFERENCE),
                properties=properties or {},
            )
        )

    for build in builds:
        primary = build.explicit_outputs[0]
        action = f"build-action:{source_path}:{build.line}:{primary}"
        inputs = tuple(
            [*build.explicit_inputs, *build.implicit_inputs, *build.order_only_inputs]
        )
        outputs = tuple([*build.explicit_outputs, *build.implicit_outputs])
        facts.append(
            BuildActionFact(
                language="ninja",
                fact_kind="BUILD_ACTION",
                logical_identity=action,
                rule=build.rule,
                inputs=inputs,
                outputs=outputs,
                source_range=location(build.line),
                evidence=base_evidence,
                properties={
                    "bindings": build.bindings,
                    "explicit_inputs": build.explicit_inputs,
                    "implicit_inputs": build.implicit_inputs,
                    "order_only_inputs": build.order_only_inputs,
                    "implicit_outputs": build.implicit_outputs,
                    "phony": build.rule == "phony",
                },
            )
        )
        if build.rule in rule_identities:
            relation(build, action, "USES_RULE", rule_identities[build.rule])
        for input_name in inputs:
            target = (
                f"build-artifact:{input_name}"
                if input_name in producers
                else f"file:{input_name}"
            )
            relation(build, action, "CONSUMES", target)
        for output in outputs:
            if len(producers[output]) == 1:
                relation(build, action, "PRODUCES", f"build-artifact:{output}")

    return NinjaParseResult(
        facts=tuple(sorted(facts, key=lambda item: item.logical_identity)),
        relations=tuple(sorted(relations, key=lambda item: item.logical_identity)),
        candidates=tuple(sorted(candidates, key=lambda item: item.logical_identity)),
        diagnostics=tuple(sorted(diagnostics, key=lambda item: item.logical_identity)),
    )
