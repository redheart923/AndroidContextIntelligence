from __future__ import annotations

from collections import Counter, defaultdict

from collectors.permission.model import (
    PARSER_VERSION,
    ParseOutcome,
    PermissionDiagnostic,
    PermissionFact,
    PermissionFactKind,
    canonical_json,
    json_value,
)


REQUIRED_REPORT_KEYS = {
    "schema_version",
    "parser_version",
    "source_revisions",
    "repositories_scanned",
    "files_scanned_by_language",
    "xml_candidates_by_dialect",
    "xml_documents_parsed_by_dialect",
    "facts_and_edges_by_type",
    "duplicate_facts",
    "declaration_conflicts",
    "malformed_xml",
    "unresolved_permission_expressions",
    "unresolved_method_owners",
    "unsupported_constructs",
    "task_failures",
}

DIAGNOSTIC_CATEGORIES = (
    "malformed_xml",
    "unresolved_permission_expressions",
    "unresolved_method_owners",
    "unsupported_constructs",
    "task_failures",
)


class PermissionReport:
    def __init__(self) -> None:
        self._facts: dict[str, PermissionFact] = {}
        self._diagnostics: dict[str, PermissionDiagnostic] = {}
        self._counters: Counter[str] = Counter()
        self._duplicate_facts = 0

    def add_outcome(self, outcome: ParseOutcome) -> None:
        for fact in outcome.facts:
            if fact.identity in self._facts:
                self._duplicate_facts += 1
            else:
                self._facts[fact.identity] = fact
        for diagnostic in outcome.diagnostics:
            self._diagnostics.setdefault(diagnostic.identity, diagnostic)
        self._counters.update(outcome.counters)

    def sorted_facts(self) -> tuple[PermissionFact, ...]:
        return tuple(self._facts[key] for key in sorted(self._facts))

    def _counter_group(self, prefix: str) -> dict[str, int]:
        marker = prefix + "."
        return {
            key[len(marker):]: value
            for key, value in sorted(self._counters.items())
            if key.startswith(marker) and value
        }

    def _diagnostics_by_category(self) -> dict[str, list[dict[str, object]]]:
        grouped: dict[str, list[PermissionDiagnostic]] = defaultdict(list)
        for diagnostic in self._diagnostics.values():
            grouped[diagnostic.category].append(diagnostic)
        return {
            category: [
                item.to_dict()
                for item in sorted(
                    grouped.get(category, ()),
                    key=lambda value: value.identity,
                )
            ]
            for category in DIAGNOSTIC_CATEGORIES
        }

    def _declaration_conflicts(self) -> list[dict[str, object]]:
        declarations: dict[str, list[PermissionFact]] = defaultdict(list)
        for fact in self._facts.values():
            if fact.kind is PermissionFactKind.DECLARES_PERMISSION:
                declarations[fact.permission_name].append(fact)

        conflicts: list[dict[str, object]] = []
        for permission_name, facts in sorted(declarations.items()):
            variants = {canonical_json(json_value(fact.properties)) for fact in facts}
            if len(variants) < 2:
                continue
            conflicts.append(
                {
                    "permission_name": permission_name,
                    "facts": [
                        fact.to_dict()
                        for fact in sorted(facts, key=lambda item: item.identity)
                    ],
                }
            )
        return conflicts

    def to_dict(self) -> dict[str, object]:
        facts = self.sorted_facts()
        diagnostics = self._diagnostics_by_category()
        revisions = {
            fact.evidence.repository: fact.evidence.source_revision or "unknown"
            for fact in facts
        }
        repositories = sorted(
            {
                *(fact.evidence.repository for fact in facts),
                *(item.repository for item in self._diagnostics.values()),
            }
        )
        fact_counts = Counter(fact.kind.value for fact in facts)

        payload: dict[str, object] = {
            "schema_version": "1.0",
            "parser_version": PARSER_VERSION,
            "source_revisions": dict(sorted(revisions.items())),
            "repositories_scanned": repositories,
            "files_scanned_by_language": self._counter_group("files_scanned"),
            "xml_candidates_by_dialect": self._counter_group("xml_candidates"),
            "xml_documents_parsed_by_dialect": self._counter_group(
                "xml_documents_parsed"
            ),
            "facts_and_edges_by_type": dict(sorted(fact_counts.items())),
            "duplicate_facts": self._duplicate_facts,
            "declaration_conflicts": self._declaration_conflicts(),
            **diagnostics,
        }
        assert set(payload) == REQUIRED_REPORT_KEYS
        return payload
