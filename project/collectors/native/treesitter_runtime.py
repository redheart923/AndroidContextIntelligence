from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from importlib.metadata import version

from tree_sitter import Language, Parser


@dataclass(frozen=True)
class GrammarRuntime:
    name: str
    package: str
    package_version: str
    runtime_version: str
    abi_version: int
    fingerprint: str
    language: Language
    parser: Parser


def load_grammar(language: str) -> GrammarRuntime:
    if language == "c":
        import tree_sitter_c as grammar

        package = "tree-sitter-c"
    elif language == "cpp":
        import tree_sitter_cpp as grammar

        package = "tree-sitter-cpp"
    elif language == "rust":
        import tree_sitter_rust as grammar

        package = "tree-sitter-rust"
    else:
        raise ValueError(f"unsupported Tree-sitter language: {language}")
    tree_sitter_language = Language(grammar.language())
    abi_version = getattr(tree_sitter_language, "abi_version", None)
    if abi_version is None:
        abi_version = tree_sitter_language.version
    package_version = version(package)
    runtime_version = version("tree-sitter")
    name = f"tree-sitter-{language}"
    payload = json.dumps(
        {
            "abi_version": abi_version,
            "grammar": name,
            "package": package,
            "package_version": package_version,
            "runtime_version": runtime_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return GrammarRuntime(
        name=name,
        package=package,
        package_version=package_version,
        runtime_version=runtime_version,
        abi_version=abi_version,
        fingerprint=hashlib.sha256(payload).hexdigest(),
        language=tree_sitter_language,
        parser=Parser(tree_sitter_language),
    )
