import json
import re
from typing import Any, Iterator

SCRIPT_JSON_PATTERN = re.compile(r'<script type="application/json"[^>]*>(.*?)</script>', re.DOTALL)


def walk_json(payload: Any) -> Iterator[dict]:
    if isinstance(payload, dict):
        yield payload
        for child_value in payload.values():
            yield from walk_json(child_value)
    elif isinstance(payload, list):
        for child_value in payload:
            yield from walk_json(child_value)


def extract_embedded_json_blobs(html_text: str) -> list[Any]:
    parsed_blobs = []
    for raw_blob in SCRIPT_JSON_PATTERN.findall(html_text):
        try:
            parsed_blobs.append(json.loads(raw_blob))
        except json.JSONDecodeError:
            continue
    return parsed_blobs


def unwrap_logged_out_gate(payload: Any) -> Any:
    if isinstance(payload, dict) and "if_not_gated_logged_out" in payload:
        return payload["if_not_gated_logged_out"]
    return payload
