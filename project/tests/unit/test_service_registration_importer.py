from pathlib import Path

from collectors.service.service_registration_importer import (
    ConstantResolver,
    JavaSource,
    SourceScanMetrics,
    candidate_source_paths,
    find_registration_calls,
    scan_sources,
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


def test_candidate_index_excludes_unrelated_java_before_model_parsing(
    tmp_path: Path,
) -> None:
    (tmp_path / "Service.java").write_text(
        'class Service { void run() { publishBinderService("x", this); } }',
        encoding="utf-8",
    )
    (tmp_path / "Constants.java").write_text(
        'class Constants { static final String NAME = "x"; }',
        encoding="utf-8",
    )
    (tmp_path / "Unrelated.java").write_text(
        "class Unrelated { int value; }",
        encoding="utf-8",
    )

    candidates, scanned = candidate_source_paths(tmp_path)

    assert scanned == 3
    assert [path.name for path in candidates] == [
        "Constants.java",
        "Service.java",
    ]


def test_file_local_parse_cache_is_keyed_by_content_digest(
    tmp_path: Path,
) -> None:
    source = tmp_path / "Service.java"
    source.write_text(
        'package demo; class Service { static final String NAME = "x"; '
        'void run() { publishBinderService(NAME, this); } }',
        encoding="utf-8",
    )
    cache = tmp_path / "cache"

    first_metrics = SourceScanMetrics()
    first = scan_sources(tmp_path, tmp_path, cache, first_metrics)
    second_metrics = SourceScanMetrics()
    second = scan_sources(tmp_path, tmp_path, cache, second_metrics)

    assert len(first) == len(second) == 1
    assert first_metrics.cache_misses == 1
    assert first_metrics.cache_hits == 0
    assert second_metrics.cache_hits == 1
    assert second_metrics.cache_misses == 0
    assert first[0].package_name == second[0].package_name == "demo"
    assert first[0].constant_definitions == second[0].constant_definitions
    assert find_registration_calls(first[0]) == find_registration_calls(second[0])
    assert len(second[0].constant_definitions) == 1
    assert len(find_registration_calls(second[0])) == 1

    source.write_text(
        'package changed; class Service { void run() { '
        'publishBinderService("x", this); } }',
        encoding="utf-8",
    )
    changed_metrics = SourceScanMetrics()
    changed = scan_sources(tmp_path, tmp_path, cache, changed_metrics)

    assert changed_metrics.cache_misses == 1
    assert changed[0].package_name == "changed"


def test_path_filter_runs_before_source_model_and_cache_creation(
    tmp_path: Path,
) -> None:
    included = tmp_path / "included"
    excluded = tmp_path / "excluded"
    included.mkdir()
    excluded.mkdir()
    for directory in (included, excluded):
        (directory / "Service.java").write_text(
            'class Service { void run() { publishBinderService("x", this); } }',
            encoding="utf-8",
        )
    cache = tmp_path / "cache"
    metrics = SourceScanMetrics()

    sources = scan_sources(
        tmp_path,
        tmp_path,
        cache,
        metrics,
        path_filter=lambda path: excluded not in path.parents,
    )

    assert [source.path for source in sources] == [included / "Service.java"]
    assert metrics.scanned_files == 2
    assert metrics.candidate_files == 1
    assert metrics.excluded_candidates == 1
    assert metrics.cache_misses == 1
    assert len(list(cache.rglob("*.json"))) == 1
