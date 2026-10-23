"""Safe XML parsing (defusedxml: no entity expansion, no external DTDs) and small builders."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation

from defusedxml.ElementTree import fromstring as safe_fromstring

from ..services.common import DomainError

MAX_BYTES = 20 * 1024 * 1024


class Node:
    """Namespace-agnostic wrapper: paths use ISO local names, e.g. n.text('GrpHdr/MsgId')."""

    def __init__(self, el: ET.Element):
        self.el = el

    @property
    def tag(self) -> str:
        return self.el.tag

    def find(self, path: str) -> "Node | None":
        el = self.el.find(path)
        return Node(el) if el is not None else None

    def findall(self, path: str) -> list["Node"]:
        return [Node(e) for e in self.el.findall(path)]

    def text(self, path: str = ".", default: str = "") -> str:
        el = self.el.find(path) if path != "." else self.el
        return (el.text or "").strip() if el is not None and el.text else default

    def attr(self, path: str, name: str, default: str = "") -> str:
        el = self.el.find(path)
        return el.get(name, default) if el is not None else default


def parse(xml: str | bytes) -> tuple[str, Node]:
    """Return (message definition id from the namespace, root business element)."""
    raw = xml.encode() if isinstance(xml, str) else xml
    if len(raw) > MAX_BYTES:
        raise DomainError("too_large", "Message exceeds 20 MB")
    try:
        root = safe_fromstring(raw)
    except Exception as exc:  # defusedxml raises on DTDs/entities as well as malformed XML
        raise DomainError("invalid_xml", f"Not well-formed or unsafe XML: {str(exc)[:200]}")
    ns = ""
    m = re.match(r"\{(.*)\}", root.tag)
    if m:
        ns = m.group(1)
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    if root.tag != "Document" or len(root) == 0:
        raise DomainError("invalid_xml", "Expected an ISO 20022 <Document>")
    msg_def = ns.rsplit(":", 1)[-1] if ns else ""
    return msg_def, Node(root[0])


def to_cents(value: str) -> int:
    try:
        d = Decimal(value)
    except (InvalidOperation, TypeError):
        raise DomainError("bad_amount", f"Invalid amount {value!r}")
    if d.as_tuple().exponent < -2 or d <= 0:
        raise DomainError("bad_amount", f"Amount {value} must be positive with at most 2 decimals")
    return int(d * 100)


def amt(cents: int) -> str:
    return f"{cents // 100}.{cents % 100:02d}"


# ------------------------------------------------------------------ building
def E(parent: ET.Element, tag: str, text: str | None = None, **attrs) -> ET.Element:
    el = ET.SubElement(parent, tag, {k: str(v) for k, v in attrs.items()})
    if text is not None:
        el.text = str(text)
    return el


def document(msg_def: str) -> ET.Element:
    return ET.Element("Document", {"xmlns": f"urn:iso:std:iso:20022:tech:xsd:{msg_def}",
                                   "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance"})


def serialize(doc: ET.Element) -> str:
    ET.indent(doc, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(doc, encoding="unicode") + "\n"


def max_text(s: str, n: int) -> str:
    return (s or "")[:n]
