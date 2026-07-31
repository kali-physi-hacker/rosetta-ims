"""Recover a JSON array from a model response that did not finish cleanly.

Lifted out of the deleted `services/extraction_service.py` (the retired v1
extraction path) because it is the one piece of that module still in use, and
it is not extraction-specific: any caller parsing a model's JSON array wants
this behaviour. Used by `tagging_service` and `collection_service`.
"""

from __future__ import annotations

import json
import re


def loads_json_array(raw: str) -> list[dict]:
    """Parse a JSON array of objects, tolerating markdown fences and truncation.

    A model occasionally hits its output-token cap and returns a JSON array cut
    off mid-object. Rather than lose the entire chunk to a JSONDecodeError, we
    salvage every complete top-level {...} object it did manage to emit.
    """
    raw = raw.strip()
    raw = re.sub(r'^```(?:json)?\s*', '', raw)
    raw = re.sub(r'\s*```$', '', raw)
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        pass
    # Salvage: walk the array and collect each balanced {...} object.
    start = raw.find('[')
    if start == -1:
        return []
    objs: list[dict] = []
    depth = 0
    obj_start: int | None = None
    in_str = esc = False
    for i in range(start + 1, len(raw)):
        c = raw[i]
        if in_str:
            if esc:            esc = False
            elif c == '\\':    esc = True
            elif c == '"':     in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == '{':
            if depth == 0:
                obj_start = i
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0 and obj_start is not None:
                try:
                    objs.append(json.loads(raw[obj_start:i + 1]))
                except json.JSONDecodeError:
                    pass
                obj_start = None
    return objs
