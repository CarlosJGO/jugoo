"""Minimal parser for Valve's text KeyValues format (``.vdf`` / ``.acf``)."""

from __future__ import annotations

from typing import Any

VdfNode = dict[str, Any]

_ESCAPES = {"n": "\n", "t": "\t", "\\": "\\", '"': '"'}


class VdfError(ValueError):
    """Raised when a KeyValues document is malformed."""


def parse_vdf(text: str) -> VdfNode:
    """Return nested dicts of strings. Duplicate keys keep the last value."""
    tokens = _tokenize(text)
    root: VdfNode = {}
    stack: list[VdfNode] = [root]
    index = 0
    while index < len(tokens):
        kind, value = tokens[index]
        if kind == "close":
            if len(stack) == 1:
                raise VdfError("unexpected '}'")
            stack.pop()
            index += 1
            continue
        if kind != "string":
            raise VdfError("expected a key")
        index += 1
        if index >= len(tokens):
            raise VdfError(f"key {value!r} has no value")
        next_kind, next_value = tokens[index]
        if next_kind == "open":
            child: VdfNode = {}
            stack[-1][value] = child
            stack.append(child)
        elif next_kind == "string":
            stack[-1][value] = next_value
        else:
            raise VdfError(f"key {value!r} has no value")
        index += 1
    if len(stack) != 1:
        raise VdfError("unbalanced '{'")
    return root


def vdf_get(node: object, key: str) -> Any:
    """Case-insensitive lookup; Steam does not keep key casing consistent."""
    if not isinstance(node, dict):
        return None
    if key in node:
        return node[key]
    wanted = key.casefold()
    for candidate, value in node.items():
        if candidate.casefold() == wanted:
            return value
    return None


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    length = len(text)
    index = 0
    while index < length:
        char = text[index]
        if char.isspace():
            index += 1
        elif char == "/" and text.startswith("//", index):
            newline = text.find("\n", index)
            index = length if newline < 0 else newline + 1
        elif char == "{":
            tokens.append(("open", ""))
            index += 1
        elif char == "}":
            tokens.append(("close", ""))
            index += 1
        elif char == "[":
            # Platform conditionals such as ``[$WIN32]`` follow a value.
            end = text.find("]", index)
            if end < 0:
                raise VdfError("unterminated conditional")
            index = end + 1
        elif char == '"':
            value, index = _read_quoted(text, index + 1)
            tokens.append(("string", value))
        else:
            start = index
            while index < length and not text[index].isspace() and text[index] not in '{}"':
                index += 1
            tokens.append(("string", text[start:index]))
    return tokens


def _read_quoted(text: str, index: int) -> tuple[str, int]:
    parts: list[str] = []
    length = len(text)
    while index < length:
        char = text[index]
        if char == '"':
            return "".join(parts), index + 1
        if char == "\\" and index + 1 < length:
            escaped = text[index + 1]
            parts.append(_ESCAPES.get(escaped, "\\" + escaped))
            index += 2
            continue
        parts.append(char)
        index += 1
    raise VdfError("unterminated string")
