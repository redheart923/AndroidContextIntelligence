from __future__ import annotations
import os
from collections import Counter
from pathlib import Path
from .models import LanguageInventory

SUFFIXES = {".java": "java", ".aidl": "aidl", ".kt": "kotlin", ".kts": "kotlin",
            ".c": "c", ".h": "c", ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp",
            ".hpp": "cpp", ".hh": "cpp", ".rs": "rust", ".hal": "hidl",
            ".py": "python", ".proto": "proto", ".mk": "make", ".xml": "xml"}


def _excluded(relative: Path, patterns: tuple[str, ...]) -> bool:
    parts = set(relative.parts)
    for pattern in patterns:
        if pattern in parts or relative.match(pattern) or relative.match(f"**/{pattern}"):
            return True
    return False


def source_paths(repository: Path, include: tuple[str, ...]) -> list[Path]:
    if not include:
        return [repository]
    return [repository / item for item in include if (repository / item).exists()]


def detect_languages(repository: Path, include: tuple[str, ...], exclude: tuple[str, ...], whitelist: tuple[str, ...]) -> LanguageInventory:
    counts: Counter[str] = Counter()
    for root in source_paths(repository, include):
        for current, dirs, files in os.walk(root):
            current_path = Path(current)
            dirs[:] = [d for d in dirs if not _excluded((current_path / d).relative_to(repository), exclude)]
            for name in files:
                path = current_path / name
                relative = path.relative_to(repository)
                if _excluded(relative, exclude):
                    continue
                if name == "Android.bp": language = "blueprint"
                elif name == "Android.mk": language = "make"
                else: language = SUFFIXES.get(path.suffix.lower())
                if language and (not whitelist or language in whitelist):
                    counts[language] += 1
    return LanguageInventory(repository=repository.name, counts=dict(sorted(counts.items())))
