from collectors.source.java_inheritance_importer import (
    build_child_qname,
    split_inherits,
)


def test_split_inherits_removes_generics() -> None:
    assert split_inherits(
        "Base<T>, First, Comparable<Child<T>>"
    ) == ("Base", "First", "Comparable")


def test_split_inherits_handles_single_parent() -> None:
    assert split_inherits("IPackageManagerBase") == (
        "IPackageManagerBase",
    )


def test_build_top_level_qname() -> None:
    assert build_child_qname(
        package_name="com.example",
        scope=None,
        name="Child",
    ) == "com.example.Child"


def test_build_nested_qname() -> None:
    assert build_child_qname(
        package_name="com.example",
        scope="Outer",
        name="Inner",
    ) == "com.example.Outer.Inner"


def test_qualified_scope_is_not_duplicated() -> None:
    assert build_child_qname(
        package_name="com.example",
        scope="com.example.Outer",
        name="Inner",
    ) == "com.example.Outer.Inner"
