from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import Counter
from enum import StrEnum
from pathlib import Path
from typing import Callable

from collectors.permission.model import (
    PARSER_VERSION,
    ParseOutcome,
    PermissionDiagnostic,
    PermissionEvidence,
    PermissionFact,
    PermissionFactKind,
)


EXTRACTOR_NAME = "xml_permission_importer"
ANDROID_NAMESPACE = "http://schemas.android.com/apk/res/android"
ANDROID = f"{{{ANDROID_NAMESPACE}}}"


class XmlDialect(StrEnum):
    MANIFEST = "manifest"
    PRIVAPP = "privapp_permissions"
    DEFAULT = "default_permissions"


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def detect_xml_dialect(
    path: Path,
    root_tag: str | None = None,
) -> XmlDialect | None:
    name = path.name.lower()
    root = local_name(root_tag).lower() if root_tag else ""
    if name == "androidmanifest.xml" or root == "manifest":
        return XmlDialect.MANIFEST
    if "privapp-permissions" in name or root == "permissions":
        return XmlDialect.PRIVAPP
    if "default-permissions" in name or root == "exceptions":
        return XmlDialect.DEFAULT
    return None


def _probe_root_tag(path: Path) -> str | None:
    try:
        for _event, element in ET.iterparse(path, events=("start",)):
            return element.tag
    except (ET.ParseError, OSError):
        return None
    return None


def is_permission_xml_candidate(path: Path) -> bool:
    if path.suffix.lower() != ".xml":
        return False
    if detect_xml_dialect(path) is not None:
        return True
    return detect_xml_dialect(path, _probe_root_tag(path)) is not None


def _evidence(
    *,
    repository: str,
    source_path: str,
    source_revision: str,
    dialect: XmlDialect,
    expression: str | None,
) -> PermissionEvidence:
    return PermissionEvidence(
        repository=repository,
        source_path=source_path,
        source_dialect=dialect.value,
        source_expression=expression,
        line_start=None,
        line_end=None,
        source_revision=source_revision,
        parser=EXTRACTOR_NAME,
        parser_version=PARSER_VERSION,
    )


def _diagnostic(
    *,
    category: str,
    reason_code: str,
    repository: str,
    source_path: str,
    expression: str | None,
    message: str,
    dialect: XmlDialect | None = None,
) -> PermissionDiagnostic:
    properties = {"source_dialect": dialect.value} if dialect else {}
    return PermissionDiagnostic(
        category=category,
        reason_code=reason_code,
        repository=repository,
        source_path=source_path,
        line_start=None,
        line_end=None,
        expression=expression,
        message=message,
        properties=properties,
    )


def _resolve_manifest_name(name: str, package_name: str | None) -> str | None:
    if name.startswith("."):
        return f"{package_name}{name}" if package_name else None
    if "." not in name:
        return f"{package_name}.{name}" if package_name else None
    return name


def _manifest_properties(element: ET.Element) -> dict[str, object]:
    names = {
        "protection_level": "protectionLevel",
        "permission_group": "permissionGroup",
        "label": "label",
        "description": "description",
        "known_signer": "knownSigner",
    }
    return {
        key: value
        for key, attribute in names.items()
        if (value := element.get(ANDROID + attribute)) is not None
    }


def _parse_optional_integer(
    raw: str | None,
    *,
    repository: str,
    source_path: str,
    expression: str,
    dialect: XmlDialect,
    diagnostics: list[PermissionDiagnostic],
) -> int | str | None:
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        diagnostics.append(
            _diagnostic(
                category="unsupported_constructs",
                reason_code="invalid_integer_attribute",
                repository=repository,
                source_path=source_path,
                expression=expression,
                message=f"expected integer attribute, found {raw!r}",
                dialect=dialect,
            )
        )
        return raw


def _parse_manifest(
    root: ET.Element,
    *,
    repository: str,
    source_path: str,
    source_revision: str,
) -> ParseOutcome:
    facts: list[PermissionFact] = []
    diagnostics: list[PermissionDiagnostic] = []
    counters: Counter[str] = Counter()
    package_name = root.get("package")
    request_tags = {
        "uses-permission",
        "uses-permission-sdk-23",
        "uses-permission-sdk-m",
    }
    known_non_permission_tags = {
        "application", "uses-sdk", "uses-feature", "queries",
        "instrumentation", "compatible-screens", "supports-screens",
        "uses-configuration", "uses-library", "uses-native-library",
        "overlay", "key-sets", "protected-broadcast", "original-package",
        "adopt-permissions", "uses-split", "profileable",
    }

    for child in root:
        tag = local_name(child.tag)
        if tag == "permission":
            raw_name = child.get(ANDROID + "name")
            resolved = _resolve_manifest_name(raw_name, package_name) if raw_name else None
            if not resolved:
                diagnostics.append(
                    _diagnostic(
                        category="unresolved_permission_expressions",
                        reason_code="missing_manifest_package_or_name",
                        repository=repository,
                        source_path=source_path,
                        expression=raw_name,
                        message="relative permission name requires a manifest package",
                        dialect=XmlDialect.MANIFEST,
                    )
                )
                continue
            facts.append(
                PermissionFact(
                    kind=PermissionFactKind.DECLARES_PERMISSION,
                    permission_name=resolved,
                    package_name=None,
                    owner_node_id=None,
                    properties=_manifest_properties(child),
                    evidence=_evidence(
                        repository=repository,
                        source_path=source_path,
                        source_revision=source_revision,
                        dialect=XmlDialect.MANIFEST,
                        expression=f"{tag}:{raw_name}",
                    ),
                )
            )
            continue

        if tag in request_tags:
            raw_name = child.get(ANDROID + "name")
            resolved = _resolve_manifest_name(raw_name, package_name) if raw_name else None
            if not resolved:
                diagnostics.append(
                    _diagnostic(
                        category="unresolved_permission_expressions",
                        reason_code="missing_manifest_package_or_name",
                        repository=repository,
                        source_path=source_path,
                        expression=raw_name,
                        message="permission request has no resolvable name",
                        dialect=XmlDialect.MANIFEST,
                    )
                )
                continue
            expression = f"{tag}:{raw_name}"
            max_sdk = _parse_optional_integer(
                child.get(ANDROID + "maxSdkVersion"),
                repository=repository,
                source_path=source_path,
                expression=expression,
                dialect=XmlDialect.MANIFEST,
                diagnostics=diagnostics,
            )
            properties: dict[str, object] = {}
            if max_sdk is not None:
                properties["max_sdk_version"] = max_sdk
            flags = child.get(ANDROID + "usesPermissionFlags")
            if flags is not None:
                properties["uses_permission_flags"] = flags
            facts.append(
                PermissionFact(
                    kind=PermissionFactKind.REQUESTS_PERMISSION,
                    permission_name=resolved,
                    package_name=package_name,
                    owner_node_id=None,
                    properties=properties,
                    evidence=_evidence(
                        repository=repository,
                        source_path=source_path,
                        source_revision=source_revision,
                        dialect=XmlDialect.MANIFEST,
                        expression=expression,
                    ),
                )
            )
            continue

        if tag not in known_non_permission_tags:
            counters["unknown_elements.manifest"] += 1
            diagnostics.append(
                _diagnostic(
                    category="unsupported_constructs",
                    reason_code="unknown_element",
                    repository=repository,
                    source_path=source_path,
                    expression=tag,
                    message=f"unsupported direct manifest child: {tag}",
                    dialect=XmlDialect.MANIFEST,
                )
            )

    counters["xml_documents_parsed.manifest"] += 1
    return ParseOutcome(tuple(facts), tuple(diagnostics), dict(counters))


def _parse_privapp(
    root: ET.Element,
    *,
    repository: str,
    source_path: str,
    source_revision: str,
) -> ParseOutcome:
    facts: list[PermissionFact] = []
    diagnostics: list[PermissionDiagnostic] = []
    counters: Counter[str] = Counter()
    for group in root:
        if local_name(group.tag) != "privapp-permissions":
            counters["unknown_elements.privapp_permissions"] += 1
            diagnostics.append(
                _diagnostic(
                    category="unsupported_constructs",
                    reason_code="unknown_element",
                    repository=repository,
                    source_path=source_path,
                    expression=local_name(group.tag),
                    message="unsupported privapp policy element",
                    dialect=XmlDialect.PRIVAPP,
                )
            )
            continue
        package_name = group.get("package")
        for child in group:
            tag = local_name(child.tag)
            kinds = {
                "permission": PermissionFactKind.ALLOWLISTS_PRIVILEGED_PERMISSION,
                "deny-permission": PermissionFactKind.DENIES_PRIVILEGED_PERMISSION,
            }
            kind = kinds.get(tag)
            permission_name = child.get("name")
            if kind is None:
                counters["unknown_elements.privapp_permissions"] += 1
                diagnostics.append(
                    _diagnostic(
                        category="unsupported_constructs",
                        reason_code="unknown_element",
                        repository=repository,
                        source_path=source_path,
                        expression=tag,
                        message="unsupported privapp package child",
                        dialect=XmlDialect.PRIVAPP,
                    )
                )
                continue
            if not package_name or not permission_name:
                continue
            facts.append(
                PermissionFact(
                    kind=kind,
                    permission_name=permission_name,
                    package_name=package_name,
                    owner_node_id=None,
                    properties={},
                    evidence=_evidence(
                        repository=repository,
                        source_path=source_path,
                        source_revision=source_revision,
                        dialect=XmlDialect.PRIVAPP,
                        expression=f"{tag}:{permission_name}",
                    ),
                )
            )
    counters["xml_documents_parsed.privapp_permissions"] += 1
    return ParseOutcome(tuple(facts), tuple(diagnostics), dict(counters))


def _parse_boolean(
    raw: str | None,
    *,
    attribute: str,
    repository: str,
    source_path: str,
    permission_name: str,
    diagnostics: list[PermissionDiagnostic],
) -> bool | None:
    if raw is None:
        return None
    normalized = raw.strip().lower()
    if normalized in {"true", "false"}:
        return normalized == "true"
    diagnostics.append(
        _diagnostic(
            category="unsupported_constructs",
            reason_code="invalid_boolean_attribute",
            repository=repository,
            source_path=source_path,
            expression=f"{attribute}={raw}",
            message=f"invalid Boolean value for {permission_name}: {raw!r}",
            dialect=XmlDialect.DEFAULT,
        )
    )
    return None


def _parse_default(
    root: ET.Element,
    *,
    repository: str,
    source_path: str,
    source_revision: str,
) -> ParseOutcome:
    facts: list[PermissionFact] = []
    diagnostics: list[PermissionDiagnostic] = []
    counters: Counter[str] = Counter()
    for exception in root:
        if local_name(exception.tag) != "exception":
            counters["unknown_elements.default_permissions"] += 1
            diagnostics.append(
                _diagnostic(
                    category="unsupported_constructs",
                    reason_code="unknown_element",
                    repository=repository,
                    source_path=source_path,
                    expression=local_name(exception.tag),
                    message="unsupported default-permissions element",
                    dialect=XmlDialect.DEFAULT,
                )
            )
            continue
        package_name = exception.get("package")
        for child in exception:
            if local_name(child.tag) != "permission":
                counters["unknown_elements.default_permissions"] += 1
                diagnostics.append(
                    _diagnostic(
                        category="unsupported_constructs",
                        reason_code="unknown_element",
                        repository=repository,
                        source_path=source_path,
                        expression=local_name(child.tag),
                        message="unsupported default-permissions package child",
                        dialect=XmlDialect.DEFAULT,
                    )
                )
                continue
            permission_name = child.get("name")
            if not package_name or not permission_name:
                continue
            properties: dict[str, object] = {}
            for attribute in ("fixed", "whitelisted"):
                value = _parse_boolean(
                    child.get(attribute),
                    attribute=attribute,
                    repository=repository,
                    source_path=source_path,
                    permission_name=permission_name,
                    diagnostics=diagnostics,
                )
                if value is not None:
                    properties[attribute] = value
            facts.append(
                PermissionFact(
                    kind=PermissionFactKind.DEFAULT_GRANTS_PERMISSION,
                    permission_name=permission_name,
                    package_name=package_name,
                    owner_node_id=None,
                    properties=properties,
                    evidence=_evidence(
                        repository=repository,
                        source_path=source_path,
                        source_revision=source_revision,
                        dialect=XmlDialect.DEFAULT,
                        expression=f"permission:{permission_name}",
                    ),
                )
            )
    counters["xml_documents_parsed.default_permissions"] += 1
    return ParseOutcome(tuple(facts), tuple(diagnostics), dict(counters))


Parser = Callable[..., ParseOutcome]
PARSERS: dict[XmlDialect, Parser] = {
    XmlDialect.MANIFEST: _parse_manifest,
    XmlDialect.PRIVAPP: _parse_privapp,
    XmlDialect.DEFAULT: _parse_default,
}


def parse_permission_xml(
    path: Path,
    *,
    repository: str,
    source_path: str,
    source_revision: str,
) -> ParseOutcome:
    path_dialect = detect_xml_dialect(path)
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as error:
        if path_dialect is None and not is_permission_xml_candidate(path):
            return ParseOutcome(counters={"files_scanned.xml": 1})
        return ParseOutcome(
            diagnostics=(
                _diagnostic(
                    category="malformed_xml",
                    reason_code="xml_parse_error",
                    repository=repository,
                    source_path=source_path,
                    expression=None,
                    message=str(error),
                    dialect=path_dialect,
                ),
            ),
            counters={"files_scanned.xml": 1},
        )

    dialect = detect_xml_dialect(path, root.tag)
    if dialect is None:
        return ParseOutcome(counters={"files_scanned.xml": 1})
    outcome = PARSERS[dialect](
        root,
        repository=repository,
        source_path=source_path,
        source_revision=source_revision,
    )
    counters = Counter(outcome.counters)
    counters["files_scanned.xml"] += 1
    counters[f"xml_candidates.{dialect.value}"] += 1
    return ParseOutcome(outcome.facts, outcome.diagnostics, dict(counters))
