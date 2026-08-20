from __future__ import annotations

import argparse
import hashlib
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


MIGRATION_PATTERN = re.compile(r"^(?P<version>[0-9]{4})_(?P<name>[a-z0-9_]+)\.sql$")


class MigrationError(RuntimeError):
    pass


def _statements(sql: str) -> tuple[str, ...]:
    statements: list[str] = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statement = buffer.strip()
            if statement:
                statements.append(statement)
            buffer = ""
    if buffer.strip():
        raise MigrationError("migration contains an incomplete SQL statement")
    return tuple(statements)


def _migration_files(migrations_dir: Path) -> tuple[tuple[int, str, Path], ...]:
    discovered: list[tuple[int, str, Path]] = []
    versions: set[int] = set()
    for path in sorted(migrations_dir.glob("*.sql")):
        match = MIGRATION_PATTERN.fullmatch(path.name)
        if match is None:
            raise MigrationError(f"invalid migration filename: {path.name}")
        version = int(match.group("version"))
        if version in versions:
            raise MigrationError(f"duplicate migration version: {version}")
        versions.add(version)
        discovered.append((version, path.stem, path))
    return tuple(discovered)


def apply_migrations(
    database: Path,
    migrations_dir: Path,
) -> tuple[str, ...]:
    if not database.is_file():
        raise MigrationError(f"database does not exist: {database}")
    if not migrations_dir.is_dir():
        raise MigrationError(f"migrations directory does not exist: {migrations_dir}")

    migrations = _migration_files(migrations_dir)
    connection = sqlite3.connect(database, isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migration (
              version INTEGER PRIMARY KEY,
              name TEXT NOT NULL UNIQUE,
              sha256 TEXT NOT NULL,
              applied_at TEXT NOT NULL
            )
            """
        )
        applied = {
            int(version): (str(name), str(digest))
            for version, name, digest in connection.execute(
                "SELECT version, name, sha256 FROM schema_migration"
            )
        }
        executed: list[str] = []
        for version, name, path in migrations:
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            previous = applied.get(version)
            if previous is not None:
                if previous != (name, digest):
                    raise MigrationError(
                        f"migration digest mismatch for {name}: "
                        f"recorded={previous[1]} current={digest}"
                    )
                continue
            sql = raw.decode("utf-8")
            for statement in _statements(sql):
                connection.execute(statement)
            connection.execute(
                """
                INSERT INTO schema_migration(version, name, sha256, applied_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    version,
                    name,
                    digest,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            connection.execute(f"PRAGMA user_version={version}")
            executed.append(name)
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise MigrationError(
                f"foreign-key violations after migrations: {violations[:5]}"
            )
        connection.execute("COMMIT")
        return tuple(executed)
    except (OSError, UnicodeDecodeError, sqlite3.Error, MigrationError) as error:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        if isinstance(error, MigrationError):
            raise
        name = locals().get("name", "migration")
        raise MigrationError(f"failed migration {name}: {error}") from error
    finally:
        connection.close()


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply staged graph migrations")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--migrations", type=Path, required=True)
    args = parser.parse_args(arguments)
    try:
        applied = apply_migrations(args.db, args.migrations)
    except MigrationError as error:
        print(f"ERROR: {error}")
        return 1
    print("Schema migrations: " + (", ".join(applied) if applied else "current"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
