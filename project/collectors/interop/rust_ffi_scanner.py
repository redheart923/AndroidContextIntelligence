from __future__ import annotations

from collections.abc import Iterable

from collectors.facts.model import RelationFact, SymbolFact


def scan_rust_exports(parse_results: Iterable[object]) -> tuple[SymbolFact, ...]:
    exports: list[SymbolFact] = []
    for parsed in parse_results:
        facts = tuple(getattr(parsed, "facts", ()))
        symbols = {
            item.logical_identity: item
            for item in facts
            if isinstance(item, SymbolFact)
        }
        for relation in facts:
            if not isinstance(relation, RelationFact):
                continue
            if relation.fact_kind != "EXPORTS_C_ABI_SYMBOL":
                continue
            source = symbols.get(relation.from_identity)
            if source is None:
                continue
            exports.append(
                SymbolFact(
                    language="rust",
                    fact_kind="NATIVE_FUNCTION",
                    logical_identity=source.logical_identity,
                    source_range=source.source_range,
                    evidence=source.evidence,
                    properties={
                        **source.properties,
                        "export_name": relation.to_identity,
                        "c_abi_export": True,
                    },
                )
            )
    return tuple(sorted(exports, key=lambda item: item.logical_identity))
