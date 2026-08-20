from __future__ import annotations
import tomllib
from pathlib import Path
from .models import ParserSpec

CAPABILITY_QUALITIES = {"semantic", "heuristic", "tags_only"}

BUILTINS = {
    "java": ParserSpec("java", "java_symbol_importer", True,
        ("symbols", "inheritance", "service_registration", "permission_semantics")),
    "aidl": ParserSpec("aidl", "aidl_binder_importer", True, ("symbols", "binder")),
}


class ParserRegistry(dict[str, ParserSpec]):
    def parser_for(self, language: str, capability: str) -> ParserSpec | None:
        value = self.get(language)
        if not value or value.implementation_for(capability) is None:
            return None
        return value


def load_parser_registry(path: Path) -> ParserRegistry:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    result = ParserRegistry(BUILTINS)
    for language, item in data.get("parsers", {}).items():
        capabilities = item.get("capabilities", [])
        if not isinstance(capabilities, list) or not all(isinstance(x, str) for x in capabilities):
            raise ValueError(f"invalid capabilities for {language}")
        quality = item.get("capability_quality", {})
        if not isinstance(quality, dict):
            raise ValueError(f"invalid capability quality for {language}")
        unknown = sorted(set(quality) - set(capabilities))
        invalid = sorted(
            key for key, value in quality.items()
            if not isinstance(value, str) or value not in CAPABILITY_QUALITIES
        )
        if unknown:
            raise ValueError(
                f"quality declared for unsupported capability in {language}: "
                + ", ".join(unknown)
            )
        if invalid:
            raise ValueError(
                f"invalid capability quality for {language}: " + ", ".join(invalid)
            )
        evidence = item.get("capability_evidence", {})
        if not isinstance(evidence, dict):
            raise ValueError(f"invalid capability evidence for {language}")
        unknown_evidence = sorted(set(evidence) - set(capabilities))
        invalid_evidence = sorted(
            key for key, value in evidence.items()
            if not isinstance(value, list)
            or not value
            or not all(isinstance(entry, str) and entry for entry in value)
        )
        if unknown_evidence:
            raise ValueError(
                f"evidence declared for unsupported capability in {language}: "
                + ", ".join(unknown_evidence)
            )
        if invalid_evidence:
            raise ValueError(
                f"invalid capability evidence for {language}: "
                + ", ".join(invalid_evidence)
            )
        implementations = item.get("capability_implementations", {})
        if not isinstance(implementations, dict):
            raise ValueError(
                f"invalid capability implementations for {language}"
            )
        unknown_implementations = sorted(
            set(implementations) - set(capabilities)
        )
        invalid_implementations = sorted(
            key
            for key, value in implementations.items()
            if not isinstance(value, str) or not value
        )
        if unknown_implementations:
            raise ValueError(
                f"implementation declared for unsupported capability in "
                f"{language}: " + ", ".join(unknown_implementations)
            )
        if invalid_implementations:
            raise ValueError(
                f"invalid capability implementation for {language}: "
                + ", ".join(invalid_implementations)
            )
        result[language] = ParserSpec(language=language,
            implementation=str(item.get("implementation", "")),
            enabled=bool(item.get("enabled", False)), capabilities=tuple(capabilities),
            capability_quality=tuple(sorted(quality.items())),
            capability_evidence=tuple(
                sorted((key, tuple(value)) for key, value in evidence.items())
            ),
            capability_implementations=tuple(
                sorted(implementations.items())
            ))
    return result
