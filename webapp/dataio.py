"""Shared data loading/writing for the webapp.

Reads properties.csv and violations_found.csv from the project root,
cleans scraping artifacts, and can snapshot both into webapp/data.js
so the page also works when opened directly as a file.
"""
import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = Path(__file__).resolve().parent
PROPS_CSV = ROOT / "properties.csv"
VIO_CSV = ROOT / "violations_found.csv"
HPD_CSV = ROOT / "hpd_found.csv"
DATA_JS = WEB / "data.js"


def clean(text):
    # Collapse embedded newlines and stray "?" artifacts from scraping;
    # repair the common UTF-8-as-cp1252 mojibake for section signs (Â§ -> §)
    text = re.sub(r"\s+", " ", (text or "").strip()).replace("Â§", "§")
    return text.rstrip(" ?").strip()


def parse_amount(text):
    text = (text or "").replace("$", "").replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return 0.0


def load_properties():
    properties = []
    if not PROPS_CSV.exists():
        return properties
    with open(PROPS_CSV, newline="", encoding="utf-8-sig") as f:
        for row in csv.reader(f):
            if not row or row[0] == "property_name":
                continue
            name, bbl, house, street, borough = (row + [""] * 5)[:5]
            properties.append({
                "name": clean(name),
                "bbl": clean(bbl),
                "house": clean(house),
                "street": clean(street),
                "borough": clean(borough),
            })
    return properties


def load_violations():
    violations = []
    if not VIO_CSV.exists():
        return violations
    with open(VIO_CSV, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            violations.append({
                "property": clean(row.get("Property")),
                "url": clean(row.get("Portal URL")),
                "ticket": clean(row.get("Ticket #")),
                "respondent": clean(row.get("Respondent")),
                "date": clean(row.get("Date")),
                "description": clean(row.get("Description")),
                "agency": clean(row.get("Agency")),
                "status": clean(row.get("Status")),
                "amount": parse_amount(row.get("Amount Due")),
            })
    return violations


def load_hpd():
    items = []
    if not HPD_CSV.exists():
        return items
    with open(HPD_CSV, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            items.append({
                "property": clean(row.get("Property")),
                "type": clean(row.get("Type")),
                "id": clean(row.get("ID")),
                "cls": clean(row.get("Class")),
                "status": clean(row.get("Status")),
                "date": clean(row.get("Date")),
                "description": clean(row.get("Description")),
                "apt": clean(row.get("Apt")),
                "story": clean(row.get("Story")),
            })
    return items


def data_version():
    """Cheap change token: mtime+size of the source CSVs."""
    parts = []
    for p in (PROPS_CSV, VIO_CSV, HPD_CSV):
        try:
            st = p.stat()
            parts.append(f"{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            parts.append("missing")
    return "|".join(parts)


def append_properties(rows):
    """Append validated property rows to properties.csv.

    rows: list of dicts with name/bbl/house/street/borough.
    Returns (added, skipped) where skipped is a list of reasons.
    """
    existing = {(p["house"] + " " + p["street"]).upper() for p in load_properties()}
    added, skipped = [], []
    for r in rows:
        house = clean(r.get("house", ""))
        street = clean(r.get("street", ""))
        if not house or not street:
            skipped.append({"row": r, "reason": "missing house number or street"})
            continue
        key = f"{house} {street}".upper()
        if key in existing:
            skipped.append({"row": r, "reason": "already in portfolio"})
            continue
        existing.add(key)
        name = clean(r.get("name", "")) or f"{house} {street}"
        added.append([name, clean(r.get("bbl", "")), house, street, clean(r.get("borough", ""))])
    if added:
        with open(PROPS_CSV, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerows(added)
    return len(added), skipped


def write_datajs():
    """Snapshot both CSVs into data.js for offline (file://) use."""
    DATA_JS.write_text(
        "// Generated snapshot - do not edit by hand.\n"
        f"const PROPERTIES = {json.dumps(load_properties(), indent=1)};\n"
        f"const VIOLATIONS = {json.dumps(load_violations(), indent=1)};\n"
        f"const HPD = {json.dumps(load_hpd(), indent=1)};\n",
        encoding="utf-8",
    )
