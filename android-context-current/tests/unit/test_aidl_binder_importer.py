from pathlib import Path

from collectors.binder.aidl_binder_importer import (
    parse_aidl,
    parse_java_binder_implementations,
)


def test_parse_aidl_interface_and_methods(tmp_path: Path) -> None:
    aidl = tmp_path / "ITestService.aidl"
    aidl.write_text(
        """
        package android.example;

        import android.os.Bundle;

        interface ITestService {
            int ping(int value);
            void send(in Bundle data);
        }
        """,
        encoding="utf-8",
    )

    parsed = parse_aidl(aidl)

    assert parsed.package_name == "android.example"
    assert parsed.interface_name == "ITestService"
    assert parsed.qualified_name == "android.example.ITestService"
    assert [method.name for method in parsed.methods] == [
        "ping",
        "send",
    ]


def test_parse_multiline_aidl_method(tmp_path: Path) -> None:
    aidl = tmp_path / "IMultiline.aidl"
    aidl.write_text(
        """
        package android.example;

        interface IMultiline {
            void execute(
                int userId,
                String packageName
            );
        }
        """,
        encoding="utf-8",
    )

    parsed = parse_aidl(aidl)

    assert len(parsed.methods) == 1
    assert parsed.methods[0].name == "execute"
    assert "int userId" in parsed.methods[0].signature
    assert "String packageName" in parsed.methods[0].signature


def test_parse_java_extends_aidl_stub(tmp_path: Path) -> None:
    java = tmp_path / "ExampleService.java"
    java.write_text(
        """
        package com.android.server.example;

        import android.example.ITestService;

        public class ExampleService extends ITestService.Stub {
        }
        """,
        encoding="utf-8",
    )

    relations = parse_java_binder_implementations(
        java,
        known_aidl_by_simple_name={
            "ITestService": {"android.example.ITestService"}
        },
    )

    assert len(relations) == 1
    assert (
        relations[0].implementation_qname
        == "com.android.server.example.ExampleService"
    )
    assert (
        relations[0].aidl_qname
        == "android.example.ITestService"
    )


def test_parse_java_multiline_class_declaration(tmp_path: Path) -> None:
    java = tmp_path / "ExampleService.java"
    java.write_text(
        """
        package com.android.server.example;

        import android.example.ITestService;

        public final class ExampleService
                extends ITestService.Stub
                implements Runnable {
            public void run() {}
        }
        """,
        encoding="utf-8",
    )

    relations = parse_java_binder_implementations(
        java,
        known_aidl_by_simple_name={
            "ITestService": {"android.example.ITestService"}
        },
    )

    assert len(relations) == 1
    assert relations[0].aidl_qname == "android.example.ITestService"
