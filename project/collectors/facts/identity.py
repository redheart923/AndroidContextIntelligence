from __future__ import annotations

import hashlib
import json

from collectors.facts.model import Fact


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def fact_identity(fact: Fact) -> str:
    from collectors.facts.codec import fact_to_dict

    payload = fact_to_dict(fact)
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
