from __future__ import annotations

from dataclasses import dataclass


class JniNameError(ValueError):
    """Raised when a JNI export name or descriptor is not reversible."""


@dataclass(frozen=True)
class DecodedJniName:
    class_name: str
    method_name: str
    argument_descriptor: str | None
    is_long: bool


def _encode(value: str, *, class_name: bool = False) -> str:
    output: list[str] = []
    for character in value:
        if class_name and character in {".", "/"}:
            output.append("_")
        elif character == "/":
            output.append("_")
        elif character == "_":
            output.append("_1")
        elif character == ";":
            output.append("_2")
        elif character == "[":
            output.append("_3")
        elif character.isascii() and character.isalnum():
            output.append(character)
        else:
            encoded = character.encode("utf-16-be", errors="surrogatepass")
            for index in range(0, len(encoded), 2):
                unit = int.from_bytes(encoded[index:index + 2], "big")
                output.append(f"_0{unit:04x}")
    return "".join(output)


def _unescaped_underscores(value: str) -> list[int]:
    positions: list[int] = []
    index = 0
    while index < len(value):
        if value[index] != "_":
            index += 1
            continue
        if index + 1 >= len(value):
            positions.append(index)
            index += 1
            continue
        escape = value[index + 1]
        if escape in "123":
            index += 2
        elif escape == "0":
            if index + 6 > len(value):
                raise JniNameError("truncated _0xxxx JNI escape")
            digits = value[index + 2:index + 6]
            try:
                int(digits, 16)
            except ValueError as error:
                raise JniNameError("invalid _0xxxx JNI escape") from error
            index += 6
        else:
            positions.append(index)
            index += 1
    return positions


def _decode(value: str, *, separator: str | None) -> str:
    output: list[str] = []
    index = 0
    while index < len(value):
        character = value[index]
        if character != "_":
            output.append(character)
            index += 1
            continue
        if index + 1 >= len(value):
            if separator is None:
                raise JniNameError("dangling JNI underscore")
            output.append(separator)
            index += 1
            continue
        escape = value[index + 1]
        if escape == "1":
            output.append("_")
            index += 2
        elif escape == "2":
            output.append(";")
            index += 2
        elif escape == "3":
            output.append("[")
            index += 2
        elif escape == "0":
            if index + 6 > len(value):
                raise JniNameError("truncated _0xxxx JNI escape")
            digits = value[index + 2:index + 6]
            try:
                output.append(chr(int(digits, 16)))
            except ValueError as error:
                raise JniNameError("invalid _0xxxx JNI escape") from error
            index += 6
        elif separator is not None:
            output.append(separator)
            index += 1
        else:
            raise JniNameError(f"unknown JNI escape _{escape}")
    return "".join(output)


def _field_end(descriptor: str, start: int, *, allow_void: bool = False) -> int:
    if start >= len(descriptor):
        raise JniNameError("incomplete descriptor")
    value = descriptor[start]
    if value in "BCDFIJSZ" or (allow_void and value == "V"):
        return start + 1
    if value == "[":
        return _field_end(descriptor, start + 1)
    if value == "L":
        end = descriptor.find(";", start + 1)
        if end < 0 or end == start + 1:
            raise JniNameError("invalid object descriptor")
        name = descriptor[start + 1:end]
        if any(character in name for character in ".;[()"):
            raise JniNameError("invalid object descriptor")
        return end + 1
    raise JniNameError(f"invalid descriptor type: {value!r}")


def _validate_arguments(arguments: str) -> None:
    index = 0
    while index < len(arguments):
        index = _field_end(arguments, index)


def _arguments(method_descriptor: str) -> str:
    if not method_descriptor.startswith("(") or ")" not in method_descriptor:
        raise JniNameError("invalid method descriptor")
    close = method_descriptor.find(")")
    arguments = method_descriptor[1:close]
    _validate_arguments(arguments)
    return_end = _field_end(method_descriptor, close + 1, allow_void=True)
    if return_end != len(method_descriptor):
        raise JniNameError("invalid method descriptor")
    return arguments


def _valid_java_name(value: str) -> bool:
    return bool(value) and (value[0].isalpha() or value[0] in "_$") and all(
        character.isalnum() or character in "_$" for character in value
    )


def encode_jni_name(
    class_name: str,
    method_name: str,
    descriptor: str | None,
) -> str:
    if not class_name or not _valid_java_name(method_name):
        raise JniNameError("class and method names must be valid and non-empty")
    symbol = f"Java_{_encode(class_name, class_name=True)}_{_encode(method_name)}"
    if descriptor is not None:
        symbol += "__" + _encode(_arguments(descriptor))
    return symbol


def decode_jni_symbol(symbol: str) -> DecodedJniName:
    if not symbol.startswith("Java_"):
        raise JniNameError("JNI symbol must start with Java_")
    body = symbol.removeprefix("Java_")
    separators = _unescaped_underscores(body)
    long_pairs = [
        (left, right)
        for left, right in zip(separators, separators[1:])
        if right == left + 1
    ]
    if len(long_pairs) > 1:
        raise JniNameError("JNI symbol has multiple long-name separators")
    is_long = bool(long_pairs)
    if is_long:
        method_separator, descriptor_separator = long_pairs[0]
        before_method = [item for item in separators if item < method_separator]
        if not before_method:
            raise JniNameError("JNI symbol is missing class/method separator")
        class_separator = before_method[-1]
        class_part = body[:class_separator]
        method_part = body[class_separator + 1:method_separator]
        descriptor_part = body[descriptor_separator + 1:]
        arguments = _decode(descriptor_part, separator="/")
        _validate_arguments(arguments)
    else:
        if not separators:
            raise JniNameError("JNI symbol is missing class/method separator")
        class_separator = separators[-1]
        class_part = body[:class_separator]
        method_part = body[class_separator + 1:]
        arguments = None
    class_name = _decode(class_part, separator=".")
    method_name = _decode(method_part, separator=None)
    if not class_name or not _valid_java_name(method_name):
        raise JniNameError("decoded JNI class or method name is invalid")
    return DecodedJniName(class_name, method_name, arguments, is_long)
