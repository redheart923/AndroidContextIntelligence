from __future__ import annotations

import re


def normalize_cpp_scope(value: str | None) -> str:
    if not value:
        return ""
    parts = [part.strip() for part in value.replace(".", "::").split("::")]
    return "::".join(part for part in parts if part)


def normalize_signature(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value.strip())


def qualified_native_name(
    name: str,
    scope: str | None,
    signature: str | None,
    *,
    include_signature: bool,
) -> str:
    normalized_scope = normalize_cpp_scope(scope)
    qualified = f"{normalized_scope}::{name}" if normalized_scope else name
    if include_signature:
        qualified += normalize_signature(signature)
    return qualified


def logical_symbol_identity(language: str, category: str, qualified: str) -> str:
    return f"{language}:{category}:{qualified}"
