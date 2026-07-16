from pathlib import Path

from collectors.service.service_registration_importer import (
    ConstantResolver,
    JavaSource,
    find_registration_calls,
    split_arguments,
)


def make_source(tmp_path: Path, text: str) -> JavaSource:
    path = tmp_path / "Example.java"
    path.write_text(text, encoding="utf-8")
    return JavaSource.load(path, Path(tmp_path))


def test_split_arguments_handles_nested_calls() -> None:
    assert split_arguments(
        '"activity", createService(foo, bar), true'
    ) == [
        '"activity"',
        "createService(foo, bar)",
        "true",
    ]


def test_find_service_manager_registration(tmp_path: Path) -> None:
    source = make_source(
        tmp_path,
        """
        package com.example;

        class Example {
            void register() {
                ServiceManager.addService(
                    Context.ACTIVITY_SERVICE,
                    this
                );
            }
        }
        """,
    )

    calls = find_registration_calls(source)

    assert len(calls) == 1
    assert calls[0].api == "ServiceManager.addService"
    assert calls[0].key_expression == (
        "Context.ACTIVITY_SERVICE"
    )
    assert calls[0].instance_expression == "this"


def test_find_publish_binder_service(tmp_path: Path) -> None:
    source = make_source(
        tmp_path,
        """
        package com.example;

        class Example {
            void register() {
                publishBinderService("demo", mService);
            }
        }
        """,
    )

    calls = find_registration_calls(source)

    assert len(calls) == 1
    assert calls[0].api == "publishBinderService"
    assert calls[0].key_expression == '"demo"'
    assert calls[0].instance_expression == "mService"


def test_find_local_service_registration(tmp_path: Path) -> None:
    source = make_source(
        tmp_path,
        """
        package com.example;

        class Example {
            void register() {
                LocalServices.addService(
                    ExampleInternal.class,
                    new ExampleInternalImpl()
                );
            }
        }
        """,
    )

    calls = find_registration_calls(source)

    assert len(calls) == 1
    assert calls[0].api == "LocalServices.addService"
    assert calls[0].key_expression == (
        "ExampleInternal.class"
    )
    assert calls[0].instance_expression == (
        "new ExampleInternalImpl()"
    )


def test_constant_resolver_follows_reference_chain(
    tmp_path: Path,
) -> None:
    source = make_source(
        tmp_path,
        """
        package com.example;

        class Example {
            static final String BASE = "demo";
            static final String SERVICE = BASE;
        }
        """,
    )

    resolver = ConstantResolver([source])

    assert resolver.resolve(
        "Example.SERVICE",
        source,
        source.text.find("SERVICE"),
    ) == "demo"


def test_resolve_direct_new_instance(tmp_path: Path) -> None:
    source = make_source(
        tmp_path,
        """
        package com.example;

        class ExampleService {
        }

        class Example {
            void register() {
                ServiceManager.addService(
                    "demo",
                    new ExampleService()
                );
            }
        }
        """,
    )

    call = find_registration_calls(source)[0]
    result = source.resolve_instance_type(
        call.instance_expression,
        call.offset,
    )

    assert result == "com.example.ExampleService"


def test_resolve_this_instance(tmp_path: Path) -> None:
    source = make_source(
        tmp_path,
        """
        package com.example;

        class Example {
            void register() {
                ServiceManager.addService("demo", this);
            }
        }
        """,
    )

    call = find_registration_calls(source)[0]

    assert source.resolve_instance_type(
        call.instance_expression,
        call.offset,
    ) == "com.example.Example"


def test_resolve_local_variable_instance(tmp_path: Path) -> None:
    source = make_source(
        tmp_path,
        """
        package com.example;

        class ExampleService {
        }

        class Example {
            void register() {
                ExampleService service =
                    new ExampleService();
                ServiceManager.addService(
                    "demo",
                    service
                );
            }
        }
        """,
    )

    call = find_registration_calls(source)[0]

    assert source.resolve_instance_type(
        call.instance_expression,
        call.offset,
    ) == "com.example.ExampleService"
