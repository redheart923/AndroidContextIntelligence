from __future__ import annotations

from pathlib import Path
from typing import Iterable

from collectors.facts.materializer import MaterializationReport, materialize_typed_facts
from collectors.facts.model import Fact


def materialize_build_facts(database: Path, facts: Iterable[Fact]) -> MaterializationReport:
    return materialize_typed_facts(database, facts, domain="build")
