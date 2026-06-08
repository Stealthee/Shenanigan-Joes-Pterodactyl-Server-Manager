"""Parsing/editing helpers for 7 Days to Die's serverconfig.xml.

The file is a flat list of `<property name="..." value="..."/>` elements,
often with explanatory comments. We don't need a real XML round-trip here --
a targeted regex lets us read and rewrite just the `value="..."` attributes
we care about while leaving comments, ordering, and formatting untouched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PROPERTY_RE = re.compile(r'<property\s+name="(?P<name>[^"]+)"\s+value="(?P<value>[^"]*)"')

PropertyKind = str  # "bool" | "int" | "float" | "string"


@dataclass
class XmlProperty:
    name: str
    value: str
    kind: PropertyKind


def parse_properties(xml_text: str) -> list[XmlProperty]:
    properties = []
    seen = set()
    for match in _PROPERTY_RE.finditer(xml_text):
        name = match["name"]
        if name in seen:
            continue  # duplicate names would make value substitution ambiguous
        seen.add(name)
        properties.append(XmlProperty(name=name, value=match["value"], kind=_infer_kind(match["value"])))
    return properties


def _infer_kind(value: str) -> PropertyKind:
    if value.lower() in ("true", "false"):
        return "bool"
    if re.fullmatch(r"-?\d+", value):
        return "int"
    if re.fullmatch(r"-?\d+\.\d+", value):
        return "float"
    return "string"


def apply_property_changes(xml_text: str, changes: dict[str, str]) -> str:
    def repl(match: re.Match) -> str:
        name = match["name"]
        if name in changes:
            return f'<property name="{name}" value="{changes[name]}"'
        return match.group(0)

    return _PROPERTY_RE.sub(repl, xml_text)
