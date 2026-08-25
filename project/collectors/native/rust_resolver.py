from __future__ import annotations


def rust_path(crate_name: str, modules: tuple[str, ...], name: str) -> str:
    parts = (crate_name, *modules, name)
    return "::".join(part for part in parts if part)


def rust_identity(
    category: str,
    crate_name: str,
    modules: tuple[str, ...],
    name: str,
) -> str:
    return f"rust:{category}:{rust_path(crate_name, modules, name)}"


def relative_rust_identity(
    category: str,
    crate_name: str,
    modules: tuple[str, ...],
    reference: str,
) -> str:
    if reference.startswith("crate::"):
        qualified = f"{crate_name}::{reference.removeprefix('crate::')}"
    elif "::" in reference:
        qualified = reference
    else:
        qualified = rust_path(crate_name, modules, reference)
    return f"rust:{category}:{qualified}"
