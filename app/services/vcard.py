"""Reading contacts from a vCard (.vcf) file, as phones and Outlook export them (vCard 2.1, 3.0, 4.0)."""

import quopri
import re
from dataclasses import dataclass, field


@dataclass
class Contact:
    name: str = ""
    notes: str = ""
    phones: list[tuple[str | None, str]] = field(default_factory=list)  # (label, number as written)
    addresses: list[tuple[str | None, str]] = field(default_factory=list)  # (label, address)


TYPE_LABELS = {"cell": "Mobile", "mobile": "Mobile", "iphone": "Mobile", "home": "Home", "work": "Work", "main": "Main"}
# Numbers nobody calls a customer on
SKIPPED_PHONE_TYPES = {"fax", "pager"}


def is_vcard(content: bytes, filename: str) -> bool:
    return filename.lower().endswith((".vcf", ".vcard")) or content.lstrip(b"\xef\xbb\xbf \t\r\n")[:11].upper() == b"BEGIN:VCARD"


def _lines(text: str) -> list[str]:
    """Logical lines: folded lines (starting with a space or tab) joined back, and quoted-printable
    values (vCard 2.1, older Android) continued after their "=" soft line break."""
    lines: list[str] = []
    for raw in text.splitlines():
        if lines and raw[:1] in (" ", "\t"):
            lines[-1] += raw[1:]
        elif lines and lines[-1].endswith("=") and "QUOTED-PRINTABLE" in lines[-1].split(":", 1)[0].upper():
            lines[-1] = lines[-1][:-1] + raw
        else:
            lines.append(raw)
    return lines


def _property(line: str) -> tuple[str, str, set[str], str] | None:
    """"item1.TEL;TYPE=CELL,pref:+44 7911" -> ("item1", "TEL", {"cell", "pref"}, "+44 7911")."""
    head, colon, value = line.partition(":")
    if not colon:
        return None
    group, _, name = head.split(";")[0].rpartition(".")
    types: set[str] = set()
    params: dict[str, str] = {}
    for param in head.split(";")[1:]:
        key, eq, val = param.partition("=")
        if not eq:
            types.add(key.strip().lower())  # vCard 2.1: TEL;CELL;VOICE
        elif key.strip().upper() == "TYPE":
            types.update(v.strip().strip('"').lower() for v in val.split(","))
        else:
            params[key.strip().upper()] = val.strip().strip('"')
    if "PREF" in params:  # vCard 4.0: PREF=1
        types.add("pref")
    if params.get("ENCODING", "").upper() == "QUOTED-PRINTABLE":
        charset = params.get("CHARSET", "utf-8")
        try:
            value = quopri.decodestring(value.encode("utf-8")).decode(charset, errors="replace")
        except LookupError:
            value = quopri.decodestring(value.encode("utf-8")).decode("utf-8", errors="replace")
    return group.lower(), name.upper(), types, value


def _unescape(value: str) -> str:
    return re.sub(r"\\(.)", lambda m: "\n" if m.group(1) in "nN" else m.group(1), value).strip()


def _parts(value: str) -> list[str]:
    """A structured value (N, ADR) split on its unescaped semicolons."""
    return [_unescape(p) for p in re.split(r"(?<!\\);", value)]


def _apple_label(value: str) -> str:
    """iPhone labels: "_$!<Mobile>!$_" for built-in ones, the text itself for custom ones."""
    m = re.fullmatch(r"_\$!<(.*)>!\$_", value.strip())
    return (m.group(1) if m else _unescape(value))[:50]


def _label(group: str, types: set[str], apple_labels: dict[str, str]) -> str | None:
    if group and group in apple_labels:
        return apple_labels[group]
    return next((TYPE_LABELS[t] for t in sorted(types) if t in TYPE_LABELS), None)


def _contact(props: list[tuple[str, str, set[str], str]]) -> Contact:
    apple_labels = {g: _apple_label(v) for g, n, _, v in props if n == "X-ABLABEL" and g}
    first = lambda name: next((v for _, n, _, v in props if n == name), "")  # noqa: E731
    contact = Contact()

    contact.name = _unescape(first("FN"))
    if not contact.name and (n := first("N")):
        family, given, middle, *_ = _parts(n) + ["", "", ""]
        contact.name = " ".join(p for p in (given, middle, family) if p)
    if not contact.name:
        contact.name = _parts(first("ORG"))[0] if first("ORG") else ""
    contact.notes = "\n".join(_unescape(v) for _, n, _, v in props if n == "NOTE" and v.strip())[:1000]

    # The preferred number and address come first
    for group, name, types, value in sorted(props, key=lambda p: "pref" not in p[2]):
        if name == "TEL" and not types & SKIPPED_PHONE_TYPES:
            number = _unescape(value)
            number = number[4:] if number.lower().startswith("tel:") else number  # vCard 4.0: tel:+44...
            if number:
                contact.phones.append((_label(group, types, apple_labels), number))
        elif name == "ADR":
            # PO box; extended address; street; city; region; postcode; country
            parts = (_parts(value) + [""] * 7)[:7]
            address = ", ".join(p.replace("\n", ", ") for p in parts[1:] if p) or parts[0]
            if address:
                contact.addresses.append((_label(group, types, apple_labels), address))
    return contact


def parse(text: str) -> list[Contact]:
    contacts: list[Contact] = []
    card: list | None = None
    for line in _lines(text):
        prop = _property(line.strip())
        if prop is None:
            continue
        _, name, _, value = prop
        if name == "BEGIN" and value.strip().upper() == "VCARD":
            card = []
        elif name == "END" and value.strip().upper() == "VCARD":
            if card is not None:
                contacts.append(_contact(card))
            card = None
        elif card is not None:
            card.append(prop)
    return contacts
