from __future__ import annotations

from collectors.permission.model import (
    ParseOutcome,
    PermissionDiagnostic,
    PermissionEvidence,
    PermissionFact,
    PermissionFactKind,
)
from collectors.permission.report import PermissionReport, REQUIRED_REPORT_KEYS


def evidence(path: str = "frameworks/base/AndroidManifest.xml") -> PermissionEvidence:
    return PermissionEvidence(
        repository="frameworks/base",
        source_path=path,
        source_dialect="android_manifest",
        source_expression="android.permission.MANAGE_USB",
        line_start=10,
        line_end=12,
        source_revision="0123456789abcdef0123456789abcdef01234567",
        parser="xml_permission_importer",
        parser_version="permission-semantics-v0.1",
    )


def test_allow_and_deny_have_different_identities() -> None:
    values = []
    for kind in (
        PermissionFactKind.ALLOWLISTS_PRIVILEGED_PERMISSION,
        PermissionFactKind.DENIES_PRIVILEGED_PERMISSION,
    ):
        values.append(
            PermissionFact(
                kind=kind,
                permission_name="android.permission.MANAGE_USB",
                package_name="com.android.systemui",
                owner_node_id=None,
                properties={},
                evidence=evidence(
                    "frameworks/base/data/etc/privapp-permissions-platform.xml"
                ),
            ).identity
        )

    assert values[0] != values[1]


def test_fact_identity_includes_properties_and_all_evidence() -> None:
    base = PermissionFact(
        kind=PermissionFactKind.DEFAULT_GRANTS_PERMISSION,
        permission_name="android.permission.CAMERA",
        package_name="com.example",
        owner_node_id=None,
        properties={"fixed": False},
        evidence=evidence(),
    )
    changed = PermissionFact(
        kind=base.kind,
        permission_name=base.permission_name,
        package_name=base.package_name,
        owner_node_id=base.owner_node_id,
        properties={"fixed": True},
        evidence=base.evidence,
    )

    assert base.identity != changed.identity


def test_report_is_stable_when_outcomes_arrive_in_reverse_order() -> None:
    requested = PermissionFact(
        kind=PermissionFactKind.REQUESTS_PERMISSION,
        permission_name="android.permission.CAMERA",
        package_name="com.example",
        owner_node_id=None,
        properties={"max_sdk_version": 35},
        evidence=evidence(),
    )
    enforced = PermissionFact(
        kind=PermissionFactKind.ENFORCES_PERMISSION,
        permission_name="android.permission.MANAGE_USB",
        package_name=None,
        owner_node_id="JAVA_METHOD:example.Service#open()",
        properties={"api_name": "enforceCallingPermission"},
        evidence=evidence("frameworks/base/example/Service.java"),
    )
    diagnostic = PermissionDiagnostic(
        category="unresolved_permission_expressions",
        reason_code="unsupported_expression",
        repository="frameworks/base",
        source_path="frameworks/base/example/Service.java",
        line_start=20,
        line_end=22,
        expression="permissionProvider()",
        message="computed permission expressions are not supported",
    )
    first = ParseOutcome(
        facts=(requested,),
        diagnostics=(diagnostic,),
        counters={"files_scanned.java": 1},
    )
    second = ParseOutcome(facts=(enforced,), counters={"files_scanned.java": 1})

    forward = PermissionReport()
    forward.add_outcome(first)
    forward.add_outcome(second)
    reverse = PermissionReport()
    reverse.add_outcome(second)
    reverse.add_outcome(first)

    assert forward.to_dict() == reverse.to_dict()
    assert set(forward.to_dict()) == REQUIRED_REPORT_KEYS
    assert forward.to_dict()["files_scanned_by_language"] == {"java": 2}


def test_report_deduplicates_facts_and_records_declaration_conflicts() -> None:
    declaration = PermissionFact(
        kind=PermissionFactKind.DECLARES_PERMISSION,
        permission_name="android.permission.DEMO",
        package_name=None,
        owner_node_id=None,
        properties={"protection_level": "signature"},
        evidence=evidence("frameworks/base/core/AndroidManifest.xml"),
    )
    conflicting = PermissionFact(
        kind=declaration.kind,
        permission_name=declaration.permission_name,
        package_name=None,
        owner_node_id=None,
        properties={"protection_level": "dangerous"},
        evidence=evidence("vendor/demo/AndroidManifest.xml"),
    )
    report = PermissionReport()

    report.add_outcome(ParseOutcome(facts=(declaration, declaration, conflicting)))
    payload = report.to_dict()

    assert payload["duplicate_facts"] == 1
    assert len(payload["declaration_conflicts"]) == 1
    assert payload["facts_and_edges_by_type"] == {"DECLARES_PERMISSION": 2}
