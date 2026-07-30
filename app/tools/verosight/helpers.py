"""Helper utilities for Verosight tools."""
from __future__ import annotations

from typing import Any


def summarize_verosight_response(obj: Any) -> Any:
    """Summarize Verosight response payload for LLM consumption.

    - Removes empty `media_urls` fields (None, empty list, empty string, empty dict).
    - Truncates any list key starting with `sample_` to at most 5 items.
    - Preserves aggregate numbers, metrics, and pagination structures.
    """
    if isinstance(obj, dict):
        result: dict[str, Any] = {}
        for k, v in obj.items():
            # Skip empty media_urls
            if k == "media_urls" and (
                v is None or (isinstance(v, (list, str, dict)) and len(v) == 0)
            ):
                continue

            # Truncate sample_* lists to max 5 items
            if isinstance(k, str) and k.startswith("sample_") and isinstance(v, list):
                result[k] = [summarize_verosight_response(item) for item in v[:5]]
            else:
                result[k] = summarize_verosight_response(v)
        return result

    if isinstance(obj, list):
        return [summarize_verosight_response(item) for item in obj]

    return obj
