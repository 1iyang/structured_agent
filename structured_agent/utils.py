"""
Utility functions for JSON handling and text processing.
"""

import json
from typing import Any, Dict


def compact_json(data: Dict[str, Any]) -> str:
    """
    Serialize data to stable compact JSON format.
    
    Args:
        data: Dictionary to serialize
        
    Returns:
        Compact JSON string with sorted keys
    """
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _close_unmatched_json_structures(text: str) -> str:
    """Close unmatched JSON braces/brackets for a truncated object prefix."""
    stack = []
    in_string = False
    escape = False

    for char in text:
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char == "}" and stack and stack[-1] == "{":
            stack.pop()
        elif char == "]" and stack and stack[-1] == "[":
            stack.pop()

    if in_string:
        return text

    closers = "".join("}" if char == "{" else "]" for char in reversed(stack))
    return text + closers


def _comma_positions_outside_strings(text: str) -> list[int]:
    """Return comma positions that are not inside string literals."""
    positions: list[int] = []
    in_string = False
    escape = False

    for index, char in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == ",":
            positions.append(index)

    return positions


def _load_truncated_json_object(text: str) -> Dict[str, Any]:
    """
    Best-effort recovery for truncated JSON objects.

    Strategy:
    - start from the full candidate
    - if needed, trim back to earlier top-level comma boundaries
    - close any unmatched braces/brackets
    """
    candidate = text.strip()
    attempts = [candidate]
    attempts.extend(candidate[:pos].rstrip() for pos in reversed(_comma_positions_outside_strings(candidate)))

    seen = set()
    for prefix in attempts:
        repaired = prefix.rstrip()
        while repaired.endswith(":") or repaired.endswith(","):
            repaired = repaired[:-1].rstrip()
        repaired = _close_unmatched_json_structures(repaired)
        if not repaired or repaired in seen:
            continue
        seen.add(repaired)
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            continue

    raise json.JSONDecodeError("Unable to recover truncated JSON object", text, 0)


def safe_json_loads(text: str) -> Dict[str, Any]:
    """
    Parse JSON with best-effort extraction.
    
    If the model emits surrounding junk, tries to extract the outermost JSON object.
    If the JSON tail is truncated, tries to salvage a valid prefix conservatively.
    
    Args:
        text: Text potentially containing JSON
        
    Returns:
        Parsed dictionary
        
    Raises:
        json.JSONDecodeError: If JSON cannot be extracted
    """
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1:
            raise
        if end != -1 and end > start:
            candidate = text[start:end + 1]
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass
        candidate = text[start:]
        return _load_truncated_json_object(candidate)


def count_tokens_simple(text: str) -> int:
    """
    Simple token count estimation (approximate).
    
    For accurate counts, use tokenizer from the model.
    
    Args:
        text: Text to count
        
    Returns:
        Approximate token count
    """
    # Rough estimation: ~4 chars per token
    return len(text) // 4
