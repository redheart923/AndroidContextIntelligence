from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from collectors.build.blueprint_parser import parse_blueprint
from collectors.build.compile_commands import load_compile_commands
from collectors.build.materializer import materialize_build_facts
from collectors.build.module_info import load_module_info
from collectors.build.ninja_parser import parse_ninja
from collectors.build.rust_project import load_rust_project
from collectors.build.soong_resolver import resolve_soong
from collectors.facts.codec import write_facts
from collectors.facts.materializer import MaterializationReport
from collectors.facts.model import (
    BuildModuleFact,
    DiagnosticFact,
    Evidence,
    EvidenceKind,
    Fact,
    SourceRange,
    SymbolFact,
)
from collectors.interop.linker import link_interop
from collectors.interop.managed_native_scanner import scan_managed_native
from collectors.interop.materializer import materialize_interop_facts
from collectors.native.materializer import materialize_native_facts
from collectors.native.header_language import resolve_header_language
from workspace.native_validation import ValidationReport, validate_native_graph
from workspace.build_inputs import BuildInputError, inspect_build_inputs
from workspace.languages import SUFFIXES, _excluded, source_paths
from workspace.revisions import source_inventory_digest


class NativePipelineError(RuntimeError):
    pass


def parse_cpp_file(*args, **kwargs):
    from collectors.native.cpp_syntax import parse_cpp_file as implementation

    return implementation(*args, **kwargs)


def scan_jni_cpp_file(*args, **kwargs):
    from collectors.interop.jni_cpp_scanner import scan_jni_cpp_file as implementation

    return implementation(*args, **kwargs)


def scan_native_registrations(*args, **kwargs):
    from collectors.interop.jni_cpp_scanner import (
        scan_native_registrations as implementation,
    )

    return implementation(*args, **kwargs)


def parse_rust_file(*args, **kwargs):
    from collectors.native.rust_syntax import parse_rust_file as implementation

    return implementation(*args, **kwargs)


def scan_rust_exports(*args, **kwargs):
    from collectors.interop.rust_ffi_scanner import scan_rust_exports as implementation

    return implementation(*args, **kwargs)


@dataclass(frozen=True)
class RepositoryScan:
    name: str
    root: Path
    revision: str | None
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()


@dataclass(frozen=True)
class NativePipelineReport:
    status: str
    semantic_fingerprint: str
    reports: tuple[MaterializationReport, ...]
    validation: ValidationReport
    candidate_count: int
    degraded_capabilities: tuple[str, ...]
    optional_inputs_missing: tuple[str, ...]
    parser_identities: tuple[tuple[str, str], ...]
    build_input_identities: tuple[tuple[str, str, str], ...]


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name, dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _semantic_fingerprint(database: Path) -> str:
    with sqlite3.connect(database) as connection:
        payload = [
            ("node", str(identity), str(content_hash or ""))
            for identity, content_hash in connection.execute(
                "SELECT node_id, content_hash FROM effective_node ORDER BY node_id"
            )
        ]
        payload.extend(
            ("edge", str(identity), str(content_hash or ""))
            for identity, content_hash in connection.execute(
                "SELECT edge_id, content_hash FROM effective_edge ORDER BY edge_id"
            )
        )
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _backup_database(source: Path, target: Path) -> None:
    """Create one consistent snapshot, including committed WAL pages."""
    with sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True) as origin:
        with sqlite3.connect(target) as destination:
            origin.backup(destination)


def _checkpoint(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _repository_source(path: Path, repository: str, item: Path) -> str:
    return f"{repository.strip('/')}/{item.relative_to(path).as_posix()}"


def _scan_files(scan: RepositoryScan) -> tuple[Path, ...]:
    selected: set[Path] = set()
    for root in source_paths(scan.root, scan.include):
        for current, directories, names in os.walk(root):
            parent = Path(current)
            directories[:] = sorted(
                name for name in directories
                if not _excluded((parent / name).relative_to(scan.root), scan.exclude)
            )
            for name in sorted(names):
                path = parent / name
                if _excluded(path.relative_to(scan.root), scan.exclude):
                    continue
                language = "blueprint" if name == "Android.bp" else SUFFIXES.get(path.suffix.lower())
                if language and (not scan.languages or language in scan.languages):
                    selected.add(path)
    return tuple(sorted(selected))


def _native_export_from_cpp(fact: Fact) -> SymbolFact | None:
    if not isinstance(fact, SymbolFact) or fact.fact_kind not in {"CPP_FUNCTION", "C_FUNCTION"}:
        return None
    prefix = f"{fact.language}:function:"
    if not fact.logical_identity.startswith(prefix):
        return None
    qualified = fact.logical_identity[len(prefix):]
    export = qualified.split("(", 1)[0].rsplit("::", 1)[-1]
    if not export.startswith("Java_"):
        return None
    return SymbolFact(
        language=fact.language,
        fact_kind="NATIVE_FUNCTION",
        logical_identity=f"{fact.language}:function:{export}",
        source_range=fact.source_range,
        evidence=fact.evidence,
        properties={"export_name": export, "direct_jni_export": True},
    )


def _unique(facts: Iterable[Fact]) -> tuple[Fact, ...]:
    selected: dict[str, Fact] = {}
    for fact in sorted(facts, key=lambda item: item.logical_identity):
        selected.setdefault(fact.logical_identity, fact)
    return tuple(selected.values())


def _workspace_path(scan: RepositoryScan, path: Path) -> str:
    return _repository_source(scan.root, scan.name, path)


def _path_from_workspace(scan: RepositoryScan, source_path: str) -> Path | None:
    prefix = scan.name.strip("/") + "/"
    if not source_path.startswith(prefix):
        return None
    return scan.root / source_path.removeprefix(prefix)


def _compile_header_contexts(
    repositories: tuple[RepositoryScan, ...],
    build_facts: Iterable[Fact],
) -> dict[str, tuple[str, ...]]:
    contexts: dict[str, set[str]] = {}
    by_name = {scan.name: scan for scan in repositories}
    include_expression = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]', re.MULTILINE)
    for fact in build_facts:
        if not isinstance(fact, SymbolFact) or fact.fact_kind != "TRANSLATION_UNIT":
            continue
        source_path = fact.logical_identity.removeprefix("translation-unit:")
        scan = by_name.get(fact.evidence.repository)
        source = _path_from_workspace(scan, source_path) if scan is not None else None
        if source is None or not source.is_file():
            continue
        language = "c" if source.suffix.lower() == ".c" else "cpp"
        compile_directory = Path(str(fact.properties.get("compile_directory", scan.root)))
        include_roots = [source.parent]
        for raw in fact.properties.get("include_paths", []):
            candidate = Path(str(raw))
            include_roots.append(candidate if candidate.is_absolute() else compile_directory / candidate)
        text = source.read_text(encoding="utf-8", errors="replace")
        for target in include_expression.findall(text):
            matches = []
            for root in include_roots:
                candidate = (root / target).resolve()
                try:
                    candidate.relative_to(scan.root.resolve())
                except ValueError:
                    continue
                if candidate.is_file():
                    matches.append(candidate)
            for header in matches:
                contexts.setdefault(_workspace_path(scan, header), set()).add(language)
    return {key: tuple(sorted(values)) for key, values in contexts.items()}


def _soong_header_contexts(
    repositories: tuple[RepositoryScan, ...],
    build_facts: Iterable[Fact],
) -> dict[str, tuple[str, ...]]:
    contexts: dict[str, set[str]] = {}
    by_name = {scan.name: scan for scan in repositories}
    include_expression = re.compile(r'^\s*#\s*include\s*[<"]([^>"]+)[>"]', re.MULTILINE)
    for fact in build_facts:
        if not isinstance(fact, BuildModuleFact) or fact.fact_kind != "SOONG_MODULE":
            continue
        scan = by_name.get(fact.evidence.repository)
        if scan is None:
            continue
        properties = fact.properties.get("resolved_properties", {})
        if not isinstance(properties, dict):
            continue
        blueprint = _path_from_workspace(scan, fact.source_range.source_path)
        if blueprint is None:
            continue
        module_directory = blueprint.parent
        raw_sources = properties.get("srcs", [])
        sources = [str(item) for item in raw_sources] if isinstance(raw_sources, list) else []
        translation_units: list[tuple[Path, str]] = []
        module_languages: set[str] = set()
        for raw in sources:
            source = module_directory / raw
            suffix = source.suffix.lower()
            if suffix == ".c":
                translation_units.append((source, "c"))
                module_languages.add("c")
            elif suffix in {".cc", ".cpp", ".cxx"}:
                translation_units.append((source, "cpp"))
                module_languages.add("cpp")
        for raw in sources:
            header = module_directory / raw
            if header.suffix.lower() in {".h", ".hh", ".hpp"}:
                contexts.setdefault(_workspace_path(scan, header), set()).update(
                    module_languages
                )
        local_includes = properties.get("local_include_dirs", [])
        global_includes = properties.get("include_dirs", [])
        include_roots = [module_directory]
        if isinstance(local_includes, list):
            include_roots.extend(module_directory / str(item) for item in local_includes)
        if isinstance(global_includes, list):
            include_roots.extend(scan.root / str(item) for item in global_includes)
        for source, language in translation_units:
            if not source.is_file():
                continue
            text = source.read_text(encoding="utf-8", errors="replace")
            for target in include_expression.findall(text):
                for root in (source.parent, *include_roots):
                    header = (root / target).resolve()
                    try:
                        header.relative_to(scan.root.resolve())
                    except ValueError:
                        continue
                    if header.is_file():
                        contexts.setdefault(_workspace_path(scan, header), set()).add(
                            language
                        )
    return {key: tuple(sorted(values)) for key, values in contexts.items()}


def _rust_contexts(
    repositories: tuple[RepositoryScan, ...],
    build_facts: Iterable[Fact],
) -> dict[str, tuple[dict[str, object], ...]]:
    by_name = {scan.name: scan for scan in repositories}
    contexts: dict[str, list[dict[str, object]]] = {}
    for fact in build_facts:
        if not isinstance(fact, BuildModuleFact) or fact.fact_kind != "RUST_CRATE_METADATA":
            continue
        scan = by_name.get(fact.evidence.repository)
        if scan is None:
            continue
        root_module = str(fact.properties.get("root_module", ""))
        source = _path_from_workspace(scan, root_module)
        if source is None:
            continue
        context = {
            "crate_name": fact.module_name,
            "known_cfg": tuple(fact.properties.get("cfg", ())),
            "edition": fact.properties.get("edition"),
            "root_module": root_module,
        }
        contexts.setdefault(source.parent.resolve().as_posix(), []).append(context)
    return {key: tuple(value) for key, value in contexts.items()}


def _rust_context_for(
    path: Path,
    contexts: dict[str, tuple[dict[str, object], ...]],
    fallback_crate: str,
) -> dict[str, object]:
    resolved = path.resolve()
    matching_roots = [
        Path(root)
        for root in contexts
        if resolved == Path(root) or Path(root) in resolved.parents
    ]
    if not matching_roots:
        return {"crate_name": fallback_crate, "known_cfg": ()}
    closest = max(matching_roots, key=lambda item: len(item.parts))
    candidates = {
        json.dumps(item, sort_keys=True, default=list): item
        for item in contexts[closest.as_posix()]
    }
    if len(candidates) == 1:
        return next(iter(candidates.values()))
    return {"crate_name": fallback_crate, "known_cfg": ()}


def _ambiguous_header_fact(
    scan: RepositoryScan,
    path: Path,
) -> DiagnosticFact:
    source_path = _workspace_path(scan, path)
    return DiagnosticFact(
        language="native-header",
        fact_kind="DIAGNOSTIC",
        logical_identity=f"diagnostic:ambiguous_header_language:{source_path}",
        category="language_resolution",
        reason_code="ambiguous_header_language",
        message="header has no unique C or C++ build context",
        source_range=SourceRange(source_path=source_path, line_start=1, line_end=1),
        evidence=Evidence(
            repository=scan.name,
            extractor="native-pipeline",
            extractor_version="0.1",
            evidence_kind=EvidenceKind.UNRESOLVED_REFERENCE,
            source_revision=scan.revision,
            content_fingerprint=hashlib.sha256(path.read_bytes()).hexdigest(),
            platform_identity="android-current",
            semantic_profile_version="native-static-v0.1",
        ),
        properties={},
    )


def _insert_native_run(
    database: Path,
    repositories: tuple[RepositoryScan, ...],
    fact_fingerprints: tuple[str, ...],
    source_inventories: tuple[tuple[str, int], ...],
    parser_identities: tuple[tuple[str, str], ...],
    build_input_identities: tuple[tuple[str, str, str], ...],
) -> str:
    source_payload = {
        "repositories": [
            (
                scan.name,
                scan.revision or "unknown",
                scan.include,
                scan.exclude,
                scan.languages,
                inventory,
            )
            for scan, inventory in zip(
                repositories, source_inventories, strict=True
            )
        ],
        "parser_identities": parser_identities,
        "build_input_identities": build_input_identities,
        "semantic_profile": "native-static-v0.1",
    }
    source_fingerprint = hashlib.sha256(
        json.dumps(source_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    database_fingerprint = hashlib.sha256(
        "".join(sorted(fact_fingerprints)).encode()
    ).hexdigest()
    run_id = f"native-static:{source_fingerprint}"
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            INSERT INTO extraction_run VALUES(
              ?, 'native_static_graph', ?, ?, 'android', 'partial', '[]',
              'not_applicable', 'native-static-v0.1', ?, 0, 0,
              'complete', ?, ?, '{}'
            )
            ON CONFLICT(run_id) DO UPDATE SET
              database_fingerprint=excluded.database_fingerprint,
              status='complete', completed_at=excluded.completed_at
            """,
            (
                run_id,
                database_fingerprint,
                source_fingerprint,
                hashlib.sha256(b"native-static-v0.1").hexdigest(),
                now,
                now,
            ),
        )
    return run_id


def run_native_pipeline(
    database: Path,
    *,
    repositories: Iterable[RepositoryScan | tuple[str, Path, str | None]],
    raw_root: Path,
    build_inputs: Path | None = None,
    strict_capabilities: Iterable[str] = (),
) -> NativePipelineReport:
    if not database.is_file():
        raise NativePipelineError(f"database does not exist: {database}")
    database.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=database.name + ".native-", suffix=".db", dir=database.parent
    )
    os.close(descriptor)
    staging = Path(temporary_name)
    _backup_database(database, staging)
    repositories = tuple(
        item if isinstance(item, RepositoryScan) else RepositoryScan(*item)
        for item in repositories
    )
    source_inventories = tuple(
        source_inventory_digest(scan.root, scan.include, scan.exclude, scan.languages)
        for scan in repositories
    )
    native_facts: list[Fact] = []
    build_facts: list[Fact] = []
    managed_facts: list[Fact] = []
    native_exports: list[SymbolFact] = []
    registration_results = []
    blueprint_documents = []
    repository_metadata: dict[str, dict[str, object]] = {}
    missing_optional: list[str] = []
    parser_identities: set[tuple[str, str]] = set()
    build_input_identities: set[tuple[str, str, str]] = set()
    try:
        inspection = inspect_build_inputs(build_inputs) if build_inputs is not None else None
        missing_optional.extend(inspection.optional_missing if inspection else ())
        for item in inspection.inputs if inspection else ():
            repository = item.repository or "build-artifacts"
            matching_scan = next(
                (scan for scan in repositories if scan.name == repository), None
            )
            planned_revision = matching_scan.revision if matching_scan is not None else None
            if (
                item.source_revision
                and planned_revision
                and item.source_revision != planned_revision
            ):
                raise NativePipelineError(
                    f"build input revision mismatch for {item.path}: "
                    f"expected {planned_revision}, got {item.source_revision}"
                )
            revision = item.source_revision or planned_revision
            platform_identity = "android-current"
            build_input_identities.add(
                (item.kind, repository, item.content_fingerprint)
            )
            if item.kind == "ninja":
                parsed = parse_ninja(
                    item.path.read_text(encoding="utf-8"),
                    item.path.as_posix(),
                    repository,
                    revision,
                    platform_identity,
                )
            elif item.kind == "compile_commands":
                parsed = load_compile_commands(
                    item.path, repository, revision, platform_identity
                )
            elif item.kind == "rust_project":
                parsed = load_rust_project(
                    item.path, repository, revision, platform_identity
                )
            elif item.kind == "module_info":
                parsed = load_module_info(
                    item.path, repository, revision, platform_identity
                )
            else:
                raise NativePipelineError(
                    f"unsupported build input kind: {item.kind}"
                )
            build_facts.extend(
                (*parsed.facts, *parsed.relations, *parsed.candidates, *parsed.diagnostics)
            )

        scanned_files = {
            scan.name: _scan_files(scan)
            for scan in repositories
        }
        for scan in repositories:
            repository, root, revision = scan.name, scan.root, scan.revision
            if not root.is_dir():
                raise NativePipelineError(f"repository root does not exist: {root}")
            repository_metadata[repository] = {
                "revision": revision,
                "platform_identity": "android-current",
            }
            for path in scanned_files[scan.name]:
                if path.name == "Android.bp":
                    blueprint_documents.append(
                        parse_blueprint(
                            path.read_text(encoding="utf-8"),
                            _repository_source(root, repository, path),
                        )
                    )

        if blueprint_documents:
            resolved = resolve_soong(blueprint_documents, repository_metadata)
            build_facts.extend(
                (*resolved.modules, *resolved.relations, *resolved.candidates, *resolved.diagnostics)
            )

        compile_header_contexts = _compile_header_contexts(repositories, build_facts)
        soong_header_contexts = _soong_header_contexts(repositories, build_facts)
        rust_contexts = _rust_contexts(repositories, build_facts)
        for scan in repositories:
            repository, root, revision = scan.name, scan.root, scan.revision
            for path in scanned_files[scan.name]:
                suffix = path.suffix.lower()
                if suffix == ".c":
                    parsed = parse_cpp_file(path, "c", repository, revision)
                    parser_identities.add(
                        (parsed.grammar_name, parsed.grammar_fingerprint)
                    )
                    native_facts.extend((*parsed.facts, *parsed.diagnostics))
                    native_exports.extend(
                        item for fact in parsed.facts if (item := _native_export_from_cpp(fact))
                    )
                elif suffix in {".cc", ".cpp", ".cxx"}:
                    parsed = scan_jni_cpp_file(path, repository, revision)
                    parser_identities.add(
                        (parsed.grammar_name, parsed.grammar_fingerprint)
                    )
                    registration_results.append(parsed)
                    native_facts.extend((*parsed.facts, *parsed.diagnostics))
                    native_exports.extend(
                        item for item in parsed.facts
                        if isinstance(item, SymbolFact) and item.fact_kind == "NATIVE_FUNCTION"
                    )
                    native_exports.extend(
                        item for fact in parsed.facts if (item := _native_export_from_cpp(fact))
                    )
                elif suffix in {".h", ".hh", ".hpp"}:
                    language = resolve_header_language(
                        Path(_workspace_path(scan, path)),
                        compile_header_contexts,
                        soong_header_contexts,
                    )
                    if language is None:
                        native_facts.append(_ambiguous_header_fact(scan, path))
                    else:
                        parsed = parse_cpp_file(path, language, repository, revision)
                        parser_identities.add(
                            (parsed.grammar_name, parsed.grammar_fingerprint)
                        )
                        native_facts.extend((*parsed.facts, *parsed.diagnostics))
                elif suffix == ".rs":
                    parsed = parse_rust_file(
                        path,
                        _rust_context_for(
                            path,
                            rust_contexts,
                            repository.replace("/", "_"),
                        ),
                        repository,
                        revision,
                    )
                    parser_identities.add(
                        (parsed.grammar_name, parsed.grammar_fingerprint)
                    )
                    native_facts.extend((*parsed.facts, *parsed.diagnostics))
                    native_exports.extend(scan_rust_exports((parsed,)))
                elif suffix in {".java", ".kt", ".kts"}:
                    language = "java" if suffix == ".java" else "kotlin"
                    parsed = scan_managed_native(path, language, repository, revision)
                    parser_identities.add(
                        (parsed.grammar_name, parsed.grammar_fingerprint)
                    )
                    managed_facts.extend((*parsed.facts, *parsed.diagnostics))

        if registration_results:
            registrations = scan_native_registrations(registration_results)
        else:
            registrations = ()
        managed_symbols = tuple(item for item in managed_facts if isinstance(item, SymbolFact))
        native_symbols = tuple(_unique(native_exports))
        linked = link_interop(managed_symbols, native_symbols, registrations, {})
        interop_facts: tuple[Fact, ...] = _unique(
            (*managed_facts, *native_symbols, *registrations, *linked.bindings, *linked.candidates, *linked.diagnostics)
        )
        native_facts = list(_unique(native_facts))
        build_facts = list(_unique(build_facts))

        raw_root.mkdir(parents=True, exist_ok=True)
        fact_fingerprints = (
            write_facts(raw_root / "native/facts.jsonl", native_facts),
            write_facts(raw_root / "build/facts.jsonl", build_facts),
            write_facts(raw_root / "interop/facts.jsonl", interop_facts),
        )

        if source_inventories != tuple(
            source_inventory_digest(scan.root, scan.include, scan.exclude, scan.languages)
            for scan in repositories
        ):
            raise NativePipelineError("source changed during native extraction")
        _insert_native_run(
            staging,
            repositories,
            fact_fingerprints,
            source_inventories,
            tuple(sorted(parser_identities)),
            tuple(sorted(build_input_identities)),
        )

        reports = (
            materialize_native_facts(staging, native_facts),
            materialize_build_facts(staging, build_facts),
            materialize_interop_facts(staging, interop_facts),
        )
        strict = tuple(sorted(set(strict_capabilities)))
        validation = validate_native_graph(staging, reports, strict)
        if not validation.valid:
            raise NativePipelineError("native graph validation failed: " + "; ".join(validation.errors))
        ambiguous = [
            item for item in (*native_facts, *build_facts, *interop_facts)
            if getattr(item, "fact_kind", "") == "EXTRACTION_CANDIDATE"
            and getattr(item, "candidate_kind", "") != "explicit_jni_registration"
        ]
        degraded = set()
        if any(getattr(item, "language", "") == "blueprint" for item in ambiguous):
            degraded.add("soong_build_graph")
        if any(getattr(item, "language", "") == "jni" for item in ambiguous):
            degraded.add("jni_bindings")
        fingerprint = _semantic_fingerprint(staging)
        report = NativePipelineReport(
            "published",
            fingerprint,
            reports,
            validation,
            len(ambiguous),
            tuple(sorted(degraded)),
            tuple(sorted(
                str(Path(item).relative_to(build_inputs.parent))
                if build_inputs is not None and Path(item).is_relative_to(build_inputs.parent)
                else item
                for item in missing_optional
            )),
            tuple(sorted(parser_identities)),
            tuple(sorted(build_input_identities)),
        )
        _atomic_json(
            raw_root / "native-pipeline-report.json",
            {
                **asdict(report),
                "reports": [asdict(item) for item in report.reports],
                "validation": asdict(report.validation),
            },
        )
        _checkpoint(staging)
        os.replace(staging, database)
        Path(str(database) + "-wal").unlink(missing_ok=True)
        Path(str(database) + "-shm").unlink(missing_ok=True)
        return report
    except Exception as error:
        staging.unlink(missing_ok=True)
        Path(str(staging) + "-wal").unlink(missing_ok=True)
        Path(str(staging) + "-shm").unlink(missing_ok=True)
        if isinstance(error, (NativePipelineError, BuildInputError)):
            if isinstance(error, BuildInputError):
                raise NativePipelineError(str(error)) from error
            raise
        raise NativePipelineError(str(error)) from error


def _repositories_from_plan(plan: dict[str, object]) -> tuple[RepositoryScan, ...]:
    aosp = Path(str(plan["aosp_root"]))
    result = []
    for raw in plan.get("repositories", []):
        item = dict(raw)
        if not item.get("enabled") or item.get("status") != "available":
            continue
        raw_path = Path(str(item["path"]))
        path = raw_path if raw_path.is_absolute() else aosp / raw_path
        result.append(RepositoryScan(
            str(item["name"]), path, item.get("revision"),
            tuple(item.get("include", ())),
            tuple(plan.get("default_exclude", ())) + tuple(item.get("exclude", ())),
            tuple(item.get("languages", ())),
        ))
    return tuple(result)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import native, JNI and build facts")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--build-inputs", type=Path)
    parser.add_argument("--strict-capability", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        report = run_native_pipeline(
            args.db,
            repositories=_repositories_from_plan(plan),
            raw_root=args.raw_root,
            build_inputs=args.build_inputs,
            strict_capabilities=args.strict_capability,
        )
    except (OSError, json.JSONDecodeError, NativePipelineError) as error:
        print(f"ERROR: {error}")
        return 1
    print(
        f"Native pipeline: {report.status}; candidates: {report.candidate_count}; "
        f"fingerprint: {report.semantic_fingerprint}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
