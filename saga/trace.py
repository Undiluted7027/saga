"""Versioned, conservative serialization for selected test executions."""

from __future__ import annotations

import math
import re
from typing import Any


TRACE_SCHEMA_VERSION = "0.1"
MAX_ITEMS = 16
MAX_DEPTH = 3
SENSITIVE_NAME = re.compile(r"(?:pass(word)?|secret|token|api[_-]?key|credential|private[_-]?key)", re.IGNORECASE)


def _type_name(value: Any) -> str:
    """Return a stable type label without exposing an object's representation."""
    value_type = type(value)
    return f"{value_type.__module__}.{value_type.__qualname__}"


def serialize_value(value: Any, field_name: str | None = None, depth: int = 0) -> Any:
    """Serialize only bounded, non-sensitive value structure for trace storage."""
    if field_name and SENSITIVE_NAME.search(field_name):
        return {"kind": "redacted"}
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else {"kind": "non_finite", "type": "float"}
    if callable(value):
        return {"kind": "unsupported", "type": _type_name(value)}
    if depth >= MAX_DEPTH:
        return {"kind": "truncated", "type": _type_name(value)}
    if isinstance(value, (list, tuple)):
        return {"kind": "sequence", "type": _type_name(value), "items": [serialize_value(item, depth=depth + 1) for item in value[:MAX_ITEMS]], "truncated": len(value) > MAX_ITEMS}
    if isinstance(value, (set, frozenset)):
        items = sorted((serialize_value(item, depth=depth + 1) for item in value), key=repr)
        return {"kind": "set", "type": _type_name(value), "items": items[:MAX_ITEMS], "truncated": len(items) > MAX_ITEMS}
    if isinstance(value, dict):
        items = []
        for key, item in list(value.items())[:MAX_ITEMS]:
            key_text = str(key)
            items.append([serialize_value(key, depth=depth + 1), serialize_value(item, key_text, depth + 1)])
        return {"kind": "mapping", "type": _type_name(value), "items": items, "truncated": len(value) > MAX_ITEMS}
    try:
        attributes = vars(value)
    except TypeError:
        return {"kind": "unsupported", "type": _type_name(value)}
    names = sorted(attributes)[:MAX_ITEMS]
    return {"kind": "object", "type": _type_name(value), "attributes": {name: serialize_value(attributes[name], name, depth + 1) for name in names}, "truncated": len(attributes) > MAX_ITEMS}


def contains_unusable_value(value: Any) -> bool:
    """Return whether a serialized value cannot safely support distinct-input evidence."""
    if isinstance(value, dict):
        if value.get("kind") in {"unsupported", "redacted", "truncated", "non_finite"}:
            return True
        return any(contains_unusable_value(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_unusable_value(item) for item in value)
    return False
