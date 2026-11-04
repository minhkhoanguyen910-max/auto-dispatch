"""Customer helpers: phone numbers and importing a customer list from CSV, Excel or vCard (.vcf)."""

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Customer, CustomerAddress, CustomerPhone
from app.schemas import ImportProblem, ImportResult
from app.services import vcard

MAX_IMPORT_ROWS = 5000


def normalize_phone(raw: str | None, country: str | None = None) -> str | None:
    """The number in international form, so every way of writing it is one customer. With
    country 44: "07911 123456", "7911123456" (Excel drops the leading 0), "447911123456",
    "+44 (0)7911 123456" and "0044 7911 123456" all give "+447911123456".

    Empty -> None. ValueError if it isn't a phone number."""
    country = country or settings.phone_country_code
    text = (raw or "").strip()
    if not text:
        return None
    digits = re.sub(r"\D", "", text.replace("(0)", ""))
    if text.startswith("+"):
        pass
    elif digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = country + digits[1:]
    # Already has the country code but no "+"; national numbers are never that long
    elif not (digits.startswith(country) and len(digits) > len(country) + 8):
        digits = country + digits
    # "+44 07911 123456": the national 0 doesn't belong after the country code
    if digits.startswith(country + "0"):
        digits = country + digits[len(country) + 1 :]
    if not 8 <= len(digits) <= 15:
        raise ValueError(f"“{text}” isn't a phone number")
    return "+" + digits


def same_address(a: str, b: str) -> bool:
    """Ignores case and spacing, so re-importing a list doesn't add the same address twice."""
    squash = lambda s: " ".join(s.lower().split())  # noqa: E731
    return squash(a) == squash(b)


# ---------------------------------------------------------------- reading the file

class ImportFileError(ValueError):
    pass


def _cell(value) -> str:
    if value is None:
        return ""
    # Excel stores phone numbers typed without "+" as numbers: 380671234567.0
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value).strip()


def _decode(content: bytes) -> str:
    # Excel's "CSV" on Windows isn't always UTF-8; latin-1 accepts any bytes, so it always returns
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("latin-1")


def _read_rows(content: bytes, filename: str) -> list[list[str]]:
    name = filename.lower()
    if name.endswith(".xls"):
        raise ImportFileError("Old .xls files aren't supported. In Excel, use Save As → .xlsx or CSV.")
    if name.endswith((".xlsx", ".xlsm")) or content.startswith(b"PK\x03\x04"):
        from openpyxl import load_workbook

        try:
            sheet = load_workbook(io.BytesIO(content), read_only=True, data_only=True).worksheets[0]
        except Exception:
            raise ImportFileError("Couldn't open this Excel file")
        return [[_cell(v) for v in row] for row in sheet.iter_rows(values_only=True)]

    text = _decode(content)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    return [[c.strip() for c in row] for row in csv.reader(io.StringIO(text), dialect)]


NAME_HEADERS = {"name", "customer", "customer name", "full name"}
# Any number of phone columns: "phone", "phone 2", "mobile", "work phone", ... The kind of number
# becomes its label.
PHONE_HEADER = re.compile(
    r"^(phone|phone number|telephone|tel|mobile|mobile phone|cell|cell phone|home phone|work phone|office phone)\s*\d*$"
)
PHONE_LABELS = {"mobile": "Mobile", "cell": "Mobile", "home": "Home", "work": "Work", "office": "Work"}
NOTES_HEADERS = {"notes", "note", "comment", "comments"}
# "address", "address 2", "label", "label 2", ...: label N names address N
ADDRESS_HEADER = re.compile(r"^(address|label)\s*(\d*)$")


@dataclass
class _Row:
    number: int
    name: str
    notes: str
    where: str = ""  # how problems refer to it
    phones: list[tuple[str | None, str]] = field(default_factory=list)  # (label, number as written)
    addresses: list[tuple[str | None, str]] = field(default_factory=list)  # (label, address)


def _parse(rows: list[list[str]]) -> list[_Row]:
    while rows and not any(rows[0]):
        rows = rows[1:]
    if not rows:
        raise ImportFileError("The file is empty")
    columns: dict[str, int] = {}
    phone_cols: list[tuple[int, str | None]] = []  # (column, label)
    address_cols: dict[int, dict[str, int]] = {}
    for i, header in enumerate(rows[0]):
        h = " ".join(header.lower().replace("_", " ").split())
        if h in NAME_HEADERS:
            columns.setdefault("name", i)
        elif PHONE_HEADER.match(h):
            phone_cols.append((i, PHONE_LABELS.get(h.split()[0])))
        elif h in NOTES_HEADERS:
            columns.setdefault("notes", i)
        elif m := ADDRESS_HEADER.match(h):
            address_cols.setdefault(int(m.group(2) or 1), {}).setdefault(m.group(1), i)
    if "name" not in columns and not phone_cols:
        raise ImportFileError("The first row must be column names, with at least “name” or “phone”")
    if len(rows) - 1 > MAX_IMPORT_ROWS:
        raise ImportFileError(f"At most {MAX_IMPORT_ROWS} rows at a time")

    get = lambda row, i: row[i] if i is not None and i < len(row) else ""  # noqa: E731
    parsed = []
    for n, row in enumerate(rows[1:], start=2):
        if not any(row):
            continue
        r = _Row(n, get(row, columns.get("name")), get(row, columns.get("notes")), f"Row {n}")
        r.phones = [(label, get(row, i)) for i, label in phone_cols if get(row, i)]
        for k in sorted(address_cols):
            address = get(row, address_cols[k].get("address"))
            if address:
                r.addresses.append((get(row, address_cols[k].get("label")) or None, address))
        parsed.append(r)
    return parsed


def _contacts(content: bytes) -> list[_Row]:
    """A .vcf file's contacts, as import rows."""
    contacts = vcard.parse(_decode(content))
    if not contacts:
        raise ImportFileError("No contacts found in this file")
    if len(contacts) > MAX_IMPORT_ROWS:
        raise ImportFileError(f"At most {MAX_IMPORT_ROWS} contacts at a time")
    return [
        _Row(n, c.name, c.notes, f"Contact {n}" + (f" ({c.name})" if c.name else ""), c.phones, c.addresses)
        for n, c in enumerate(contacts, start=1)
    ]


# ---------------------------------------------------------------- applying it

def _problem(row: _Row) -> str | None:
    if len(row.name) > 200:
        return "Name is longer than 200 characters"
    if len(row.notes) > 1000:
        return "Notes are longer than 1000 characters"
    for label, address in row.addresses:
        if len(address) > 500:
            return "An address is longer than 500 characters"
        if label and len(label) > 50:
            return "An address label is longer than 50 characters"
    return None


def _numbers(row: _Row) -> dict[str, str | None]:
    """The row's phone numbers in international form -> label. ValueError for one that isn't a number."""
    numbers: dict[str, str | None] = {}
    for label, raw in row.phones:
        numbers.setdefault(normalize_phone(raw), label)
    return numbers


async def import_customers(session: AsyncSession, content: bytes, filename: str, dry_run: bool) -> ImportResult:
    """Adds the file's customers. A row with a phone number an existing customer has (or, without
    any phone, that customer's name) adds its other phones and addresses to that customer instead,
    so several rows can give one customer several numbers and addresses, and importing the same
    file twice changes nothing.

    With `dry_run`, nothing is saved: the result says what importing would do."""
    rows = _contacts(content) if vcard.is_vcard(content, filename) else _parse(_read_rows(content, filename))
    problems: list[ImportProblem] = []
    numbers: dict[int, dict[str, str | None]] = {}
    for row in rows:
        try:
            numbers[row.number] = _numbers(row)
        except ValueError as err:
            problems.append(ImportProblem(row=row.number, where=row.where, message=str(err)))

    all_numbers = {n for ns in numbers.values() for n in ns}
    by_phone: dict[str, Customer] = {}
    for c in await session.scalars(select(Customer).where(Customer.phones.any(CustomerPhone.phone.in_(all_numbers)))):
        by_phone.update({p.phone: c for p in c.phones})
    # Customers without a phone are matched by name, when exactly one has it
    by_name: dict[str, list[Customer]] = {}
    names = {r.name.lower() for r in rows if r.name and not numbers.get(r.number)}
    for c in await session.scalars(select(Customer).where(~Customer.phones.any(), func.lower(Customer.name).in_(names))):
        by_name.setdefault(c.name.lower(), []).append(c)

    created: set[int] = set()
    updated: set[int] = set()
    phones_added = addresses_added = 0
    for row in rows:
        if row.number not in numbers:
            continue  # bad phone, already reported
        if message := _problem(row):
            problems.append(ImportProblem(row=row.number, where=row.where, message=message))
            continue
        row_numbers = numbers[row.number]
        if row_numbers:
            matches = list({id(c): c for n in row_numbers if (c := by_phone.get(n))}.values())
        else:
            matches = by_name.get(row.name.lower(), []) if row.name else []
        if len(matches) > 1 and row_numbers:
            names_ = " and ".join(c.name for c in matches)
            problems.append(ImportProblem(row=row.number, where=row.where, message=f"Its phone numbers belong to different customers ({names_})"))
            continue
        customer = matches[0] if len(matches) == 1 else None
        if customer is None:
            if not row.name:
                problems.append(ImportProblem(row=row.number, where=row.where, message="No name, and no phone number that matches a customer"))
                continue
            customer = Customer(name=row.name, notes=row.notes or None, is_active=True, phones=[], addresses=[])
            session.add(customer)
            created.add(id(customer))
            if not row_numbers:
                by_name.setdefault(row.name.lower(), []).append(customer)
        changed = False
        if row.notes and not customer.notes:
            customer.notes, changed = row.notes, True
        for number, label in row_numbers.items():
            if number in by_phone:
                continue  # already this customer's
            customer.phones.append(CustomerPhone(phone=number, label=label, position=len(customer.phones)))
            by_phone[number] = customer
            phones_added += 1
            changed = True
        for label, address in row.addresses:
            if any(same_address(address, a.address) for a in customer.addresses):
                continue
            customer.addresses.append(CustomerAddress(label=label, address=address, is_default=not customer.addresses))
            addresses_added += 1
            changed = True
        if changed and id(customer) not in created:
            updated.add(id(customer))

    if dry_run:
        await session.rollback()
    else:
        await session.commit()
    problems.sort(key=lambda p: p.row)
    return ImportResult(
        dry_run=dry_run,
        customers_created=len(created),
        customers_updated=len(updated),
        phones_added=phones_added,
        addresses_added=addresses_added,
        problems=problems,
    )
