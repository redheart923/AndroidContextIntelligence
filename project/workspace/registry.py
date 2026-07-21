from __future__ import annotations
import tomllib
from pathlib import Path
from .models import ParserSpec


BUILTINS = {
    "java": ParserSpec("java", "java_symbol_importer", True,
        ("symbols", "inheritance", "service_registration", "permission_enforcement")),
    "aidl": ParserSpec("aidl", "aidl_binder_importer", True, ("symbols", "binder")),
}


class ParserRegistry(dict[str, ParserSpec]):
    def parser_for(self, language: str, capability: str) -> ParserSpec | None:
        value = self.get(language)
        if not value or not value.enabled or not value.implementation or capability not in value.capabilities:
            return None
        return value


def load_parser_registry(path: Path) -> ParserRegistry:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    result = ParserRegistry(BUILTINS)
    for language, item in data.get("parsers", {}).items():
        capabilities = item.get("capabilities", [])
        if not isinstance(capabilities, list) or not all(isinstance(x, str) for x in capabilities):
            raise ValueError(f"invalid capabilities for {language}")
        result[language] = ParserSpec(language=language,
            implementation=str(item.get("implementation", "")),
            enabled=bool(item.get("enabled", False)), capabilities=tuple(capabilities))
    return result
