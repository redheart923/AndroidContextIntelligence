from __future__ import annotations

from pathlib import Path
from typing import Literal, Mapping, Sequence


NativeLanguage = Literal["c", "cpp"]
LanguageContexts = Mapping[str, Sequence[NativeLanguage] | NativeLanguage]


def _values(
    contexts: LanguageContexts,
    path: str,
) -> tuple[NativeLanguage, ...]:
    value = contexts.get(path)
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(value)


def resolve_header_language(
    path: Path,
    compile_db: LanguageContexts,
    owning_modules: LanguageContexts,
) -> NativeLanguage | None:
    normalized = path.as_posix()
    observed = set(_values(compile_db, normalized)) | set(
        _values(owning_modules, normalized)
    )
    if len(observed) != 1:
        return None
    return observed.pop()
