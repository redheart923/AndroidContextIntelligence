from __future__ import annotations

import pytest

from collectors.interop.jni_name_codec import (
    JniNameError,
    decode_jni_symbol,
    encode_jni_name,
)


def test_short_jni_name_encodes_package_inner_class_and_underscore() -> None:
    symbol = encode_jni_name(
        "com.example.Demo$Inner_Class",
        "native_run",
        None,
    )

    assert symbol == "Java_com_example_Demo_00024Inner_1Class_native_1run"
    decoded = decode_jni_symbol(symbol)
    assert decoded.class_name == "com.example.Demo$Inner_Class"
    assert decoded.method_name == "native_run"
    assert decoded.argument_descriptor is None
    assert decoded.is_long is False


def test_long_jni_name_encodes_arrays_objects_semicolons_and_unicode() -> None:
    symbol = encode_jni_name(
        "com.example.Demo",
        "méthod",
        "([ILjava/lang/My_Class;)V",
    )

    assert symbol == (
        "Java_com_example_Demo_m_000e9thod__"
        "_3ILjava_lang_My_1Class_2"
    )
    decoded = decode_jni_symbol(symbol)
    assert decoded.class_name == "com.example.Demo"
    assert decoded.method_name == "méthod"
    assert decoded.argument_descriptor == "[ILjava/lang/My_Class;"
    assert decoded.is_long is True


@pytest.mark.parametrize(
    "symbol",
    [
        "NotJava_com_example_Demo_run",
        "Java_com_example_Demo_bad_4escape",
        "Java_com_example_Demo_bad_0ZZZZ",
        "Java_com_example_Demo_dangling_",
        "Java_com_example_Demo_run__bad_2descriptor",
    ],
)
def test_malformed_jni_names_are_rejected(symbol: str) -> None:
    with pytest.raises(JniNameError):
        decode_jni_symbol(symbol)


def test_encode_rejects_invalid_method_descriptor() -> None:
    with pytest.raises(JniNameError, match="method descriptor"):
        encode_jni_name("com.example.Demo", "run", "Ljava/lang/String;")
