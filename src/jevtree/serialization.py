from __future__ import annotations

import dataclasses
import json
import math
from pathlib import Path
from typing import Any


def jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return {field.name: jsonable(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [jsonable(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, Path):
        return str(value)
    return value


def dumps(value: Any, *, indent: int | None = 2) -> str:
    return json.dumps(jsonable(value), ensure_ascii=False, indent=indent, sort_keys=True)
