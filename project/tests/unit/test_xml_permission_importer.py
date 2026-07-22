from __future__ import annotations

from pathlib import Path

from collectors.permission.model import PermissionFactKind
from collectors.permission.xml_permission_importer import (
    XmlDialect,
    detect_xml_dialect,
    is_permission_xml_candidate,
    parse_permission_xml,
)


def parse(path: Path):
    return parse_permission_xml(
        path,
        repository="frameworks/base",
        source_path=f"frameworks/base/{path.name}",
        source_revision="0123456789abcdef0123456789abcdef01234567",
    )


def test_manifest_declarations_requests_and_properties(tmp_path: Path) -> None:
    manifest = tmp_path / "AndroidManifest.xml"
    manifest.write_text(
        """<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    package="com.example">
  <permission android:name=".permission.LOCAL"
      android:protectionLevel="signature|privileged"
      android:permissionGroup="android.permission-group.UNDEFINED"
      android:label="@string/local_label"
      android:description="@string/local_description"
      android:knownSigner="@array/known_signers" />
  <uses-permission android:name="android.permission.CAMERA"
      android:maxSdkVersion="32"
      android:usesPermissionFlags="neverForLocation" />
  <uses-permission-sdk-23 android:name="android.permission.POST_NOTIFICATIONS" />
  <uses-permission-sdk-m android:name="android.permission.POST_NOTIFICATIONS" />
</manifest>
""",
        encoding="utf-8",
    )

    outcome = parse(manifest)

    assert {(fact.kind.value, fact.permission_name) for fact in outcome.facts} == {
        ("DECLARES_PERMISSION", "com.example.permission.LOCAL"),
        ("REQUESTS_PERMISSION", "android.permission.CAMERA"),
        ("REQUESTS_PERMISSION", "android.permission.POST_NOTIFICATIONS"),
    }
    declaration = next(
        fact
        for fact in outcome.facts
        if fact.kind is PermissionFactKind.DECLARES_PERMISSION
    )
    assert declaration.properties == {
        "description": "@string/local_description",
        "known_signer": "@array/known_signers",
        "label": "@string/local_label",
        "permission_group": "android.permission-group.UNDEFINED",
        "protection_level": "signature|privileged",
    }
    camera_request = next(
        fact for fact in outcome.facts if fact.permission_name.endswith("CAMERA")
    )
    assert camera_request.properties["max_sdk_version"] == 32
    assert camera_request.properties["uses_permission_flags"] == "neverForLocation"
    assert outcome.counters["xml_documents_parsed.manifest"] == 1


def test_privapp_allow_and_deny_are_distinct_facts(tmp_path: Path) -> None:
    policy = tmp_path / "privapp-permissions-platform.xml"
    policy.write_text(
        """<permissions><privapp-permissions package="com.example">
  <permission name="android.permission.CAMERA" />
  <deny-permission name="android.permission.RECORD_AUDIO" />
</privapp-permissions></permissions>
""",
        encoding="utf-8",
    )

    outcome = parse(policy)

    assert [(fact.kind, fact.permission_name, fact.package_name) for fact in outcome.facts] == [
        (
            PermissionFactKind.ALLOWLISTS_PRIVILEGED_PERMISSION,
            "android.permission.CAMERA",
            "com.example",
        ),
        (
            PermissionFactKind.DENIES_PRIVILEGED_PERMISSION,
            "android.permission.RECORD_AUDIO",
            "com.example",
        ),
    ]


def test_default_grants_normalize_booleans_and_report_invalid_values(
    tmp_path: Path,
) -> None:
    policy = tmp_path / "default-permissions-platform.xml"
    policy.write_text(
        """<exceptions><exception package="com.example">
  <permission name="android.permission.CAMERA" fixed="true" whitelisted="false" />
  <permission name="android.permission.RECORD_AUDIO" fixed="sometimes" />
</exception></exceptions>
""",
        encoding="utf-8",
    )

    outcome = parse(policy)

    camera = next(fact for fact in outcome.facts if fact.permission_name.endswith("CAMERA"))
    assert camera.kind is PermissionFactKind.DEFAULT_GRANTS_PERMISSION
    assert camera.properties == {"fixed": True, "whitelisted": False}
    assert [item.reason_code for item in outcome.diagnostics] == [
        "invalid_boolean_attribute"
    ]


def test_malformed_candidate_is_reported(tmp_path: Path) -> None:
    manifest = tmp_path / "AndroidManifest.xml"
    manifest.write_text("<manifest><permission", encoding="utf-8")

    outcome = parse(manifest)

    assert outcome.facts == ()
    assert len(outcome.diagnostics) == 1
    assert outcome.diagnostics[0].category == "malformed_xml"
    assert outcome.diagnostics[0].reason_code == "xml_parse_error"


def test_layout_permission_element_is_not_permission_policy(tmp_path: Path) -> None:
    layout = tmp_path / "screen.xml"
    layout.write_text(
        "<LinearLayout><permission name=\"android.permission.CAMERA\" /></LinearLayout>",
        encoding="utf-8",
    )

    assert not is_permission_xml_candidate(layout)
    assert detect_xml_dialect(layout, "LinearLayout") is None
    assert parse(layout).facts == ()


def test_unknown_direct_child_is_counted_without_task_failure(tmp_path: Path) -> None:
    manifest = tmp_path / "AndroidManifest.xml"
    manifest.write_text(
        "<manifest package=\"com.example\"><future-permission /></manifest>",
        encoding="utf-8",
    )

    outcome = parse(manifest)

    assert outcome.counters["unknown_elements.manifest"] == 1
    assert not [item for item in outcome.diagnostics if item.category == "task_failures"]
    assert [(item.category, item.reason_code) for item in outcome.diagnostics] == [
        ("unsupported_constructs", "unknown_element")
    ]


def test_candidate_and_dialect_detection_use_document_context(tmp_path: Path) -> None:
    manifest = tmp_path / "AndroidManifest.xml"
    manifest.write_text("<manifest package=\"com.example\" />", encoding="utf-8")
    privapp = tmp_path / "vendor-policy.xml"
    privapp.write_text("<permissions />", encoding="utf-8")
    default = tmp_path / "exceptions.xml"
    default.write_text("<exceptions />", encoding="utf-8")

    assert is_permission_xml_candidate(manifest)
    assert is_permission_xml_candidate(privapp)
    assert is_permission_xml_candidate(default)
    assert detect_xml_dialect(manifest, "manifest") is XmlDialect.MANIFEST
    assert detect_xml_dialect(privapp, "permissions") is XmlDialect.PRIVAPP
    assert detect_xml_dialect(default, "exceptions") is XmlDialect.DEFAULT
