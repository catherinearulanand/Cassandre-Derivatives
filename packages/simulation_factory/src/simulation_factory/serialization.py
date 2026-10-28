"""Deterministic JSON serialization for generated fixture data.

Non-negotiable per the work item spec: "Never a JSON float for money -- use
decimal strings." This module enforces that at the serialization boundary:
`Decimal` becomes a plain decimal string, `money_types` Pydantic models
serialize through their own `model_dump(mode="json")` (which already renders
`Decimal` fields as strings), and a bare Python `float` is rejected outright
rather than silently coerced, so a generator bug surfaces immediately instead
of leaking a float into a fixture file.

`dump_json_bytes` / `write_json_file` produce byte-identical output for
byte-identical input on every run: keys are sorted, separators are fixed, and
nothing here reads the wall clock.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel


class SerializationError(ValueError):
    pass


def to_jsonable(obj: Any) -> Any:
    """Recursively convert a generated fixture object graph into plain
    JSON-safe Python values (dict/list/str/int/bool/None only -- never a
    float)."""

    if obj is None or isinstance(obj, (str, int, bool)):
        return obj
    if isinstance(obj, float):
        raise SerializationError(
            f"refusing to serialize a bare float ({obj!r}); use a decimal "
            "string, money_types.Money/Quantity, or Decimal instead"
        )
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, dt.datetime):
        if obj.tzinfo is None:
            raise SerializationError(f"datetime {obj!r} must be timezone-aware")
        return obj.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(obj, dt.date):
        return obj.isoformat()
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [to_jsonable(v) for v in obj]
    raise SerializationError(f"do not know how to serialize {type(obj).__name__}: {obj!r}")


def dump_json_bytes(obj: Any) -> bytes:
    """Canonical, deterministic, human-readable JSON bytes (sorted keys,
    fixed indentation, trailing newline, LF line endings) -- identical
    input always produces identical output."""

    payload = to_jsonable(obj)
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return (text + "\n").encode("utf-8")


def write_json_file(path: Path, obj: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = dump_json_bytes(obj)
    path.write_bytes(data)
    return path


def write_text_file(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = text.replace("\r\n", "\n")
    if not normalized.endswith("\n"):
        normalized += "\n"
    path.write_bytes(normalized.encode("utf-8"))
    return path
