"""NYC Open Data OATH/ECB violations fetcher.

API-based replacement for the CityPay Playwright scanner: pulls the same
OATH-adjudicated ECB tickets from NYC Open Data (dataset jz4z-kudi) in a few
seconds instead of minutes, with no browser.

- Properties with a BBL are queried by block/lot (catches tickets recorded
  under any address variant of the parcel); the rest by house + street.
- New tickets within --days are appended to violations_found.csv.
- Existing tickets get their Status and Amount Due refreshed in place
  (e.g. a DEFAULTED ticket that was since paid flips to PAID IN FULL / $0).

Usage:  python api_fetcher.py [--days 180] [--dry-run]
Also importable: run_fetch(days=180, dry_run=False) -> summary dict
(webapp/server.py calls this on a schedule for live updates).

Set NYC_APP_TOKEN in .env / environment for higher API rate limits (optional).
"""
import argparse
import csv
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "webapp"))
import dataio

API_URL = "https://data.cityofnewyork.us/resource/jz4z-kudi.json"
PORTAL_BASE = "https://a836-citypay.nyc.gov/citypay/ecb"
BATCH_SIZE = 15          # properties per API request
CSV_HEADER = ["Property", "Portal URL", "Ticket #", "Respondent", "Date",
              "Description", "Agency", "Status", "Amount Due"]

SELECT_FIELDS = ",".join([
    "ticket_number", "violation_date", "issuing_agency", "respondent_last_name",
    "hearing_status", "balance_due", "charge_1_code_description",
    "violation_location_house", "violation_location_street_name",
    "violation_location_borough", "violation_location_block_no",
    "violation_location_lot_no",
])

BOROUGH_NAMES = {"1": "MANHATTAN", "2": "BRONX", "3": "BROOKLYN",
                 "4": "QUEENS", "5": "STATEN ISLAND"}

# Map OATH agency names onto the CityPay-style names already in the log,
# so the website's agency filter doesn't split one agency into two labels.
AGENCY_MAP = {
    "SANITATION OTHERS": "DSNY Other",
    "SANITATION ENFORCEMENT AGENTS": "DSNY Enf Agt",
    "SANITATION RECYCLING": "DSNY Recyc",
    "SANITATION POLICE": "DSNY Police",
    "SANITATION PD": "DSNY Police",
    "PCS - DOHMH": "DOHMH - PCS",
    "BUREAU BED BUG - DOHMH": "DOHMH - BBugs",
    "DEPT. OF BUILDINGS": "Dept of Bldgs",
    "DEPARTMENT OF BUILDINGS": "Dept of Bldgs",
    "POLICE DEPARTMENT": "NYPD",
    "NYPD - OTHERS": "NYPD",
}

STREET_SUFFIXES = {
    "ST": "STREET", "STREET": "STREET",
    "AV": "AVENUE", "AVE": "AVENUE", "AVENUE": "AVENUE",
    "BLVD": "BOULEVARD", "BOULV": "BOULEVARD", "BL": "BOULEVARD", "BOULEVARD": "BOULEVARD",
    "RD": "ROAD", "ROAD": "ROAD",
    "PL": "PLACE", "PLACE": "PLACE",
    "DR": "DRIVE", "DRIVE": "DRIVE",
    "LN": "LANE", "LANE": "LANE",
    "CT": "COURT", "COURT": "COURT",
    "TER": "TERRACE", "TERR": "TERRACE", "TERRACE": "TERRACE",
    "PKWY": "PARKWAY", "PKY": "PARKWAY", "PARKWAY": "PARKWAY",
    "HWY": "HIGHWAY", "HIGHWAY": "HIGHWAY",
    "SQ": "SQUARE", "SQUARE": "SQUARE",
    "EXPY": "EXPRESSWAY", "EXPWY": "EXPRESSWAY", "EXPRESSWAY": "EXPRESSWAY",
    "CONC": "CONCOURSE", "CONCOURSE": "CONCOURSE",
}


def norm_street(s):
    """Uppercase, collapse spaces, canonicalize the trailing suffix word."""
    words = re.sub(r"\s+", " ", (s or "").strip().upper()).split(" ")
    if words and words[-1] in STREET_SUFFIXES:
        words[-1] = STREET_SUFFIXES[words[-1]]
    return " ".join(words)


def street_query_base(s):
    """Street name minus its suffix word — used for a LIKE 'BASE%' query."""
    words = re.sub(r"\s+", " ", (s or "").strip().upper()).split(" ")
    if len(words) > 1 and words[-1] in STREET_SUFFIXES:
        words = words[:-1]
    return " ".join(words)


def soql_str(s):
    return "'" + s.replace("'", "''") + "'"


def parse_bbl(bbl):
    """'2026760049' -> ('BRONX', '02676', '0049') or None."""
    bbl = re.sub(r"\D", "", bbl or "")
    if len(bbl) != 10 or bbl[0] not in BOROUGH_NAMES:
        return None
    return BOROUGH_NAMES[bbl[0]], bbl[1:6], bbl[6:10]


def api_get(where):
    params = urllib.parse.urlencode({
        "$select": SELECT_FIELDS,
        "$where": where,
        "$limit": "50000",
    })
    req = urllib.request.Request(f"{API_URL}?{params}")
    token = os.environ.get("NYC_APP_TOKEN")
    if token:
        req.add_header("X-App-Token", token)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def build_indexes(properties):
    """Query clauses plus lookup indexes to map API rows back to properties."""
    clauses, bbl_index, addr_index = [], {}, {}
    for p in properties:
        house = p["house"].strip().upper()
        street = p["street"].strip()
        if not house or not street:
            continue
        bbl = parse_bbl(p["bbl"])
        if bbl:
            boro, block, lot = bbl
            clauses.append(
                f"(violation_location_block_no={soql_str(block)}"
                f" AND violation_location_lot_no={soql_str(lot)}"
                f" AND upper(violation_location_borough)={soql_str(boro)})")
            bbl_index[(block, lot, boro)] = p
        else:
            base = street_query_base(street)
            clauses.append(
                f"(violation_location_house={soql_str(house)}"
                f" AND upper(violation_location_street_name) like {soql_str(base + '%')})")
        addr_index[(house, norm_street(street))] = p
    return clauses, bbl_index, addr_index


def match_property(row, bbl_index, addr_index):
    key = (row.get("violation_location_block_no", ""),
           row.get("violation_location_lot_no", ""),
           (row.get("violation_location_borough") or "").strip().upper())
    if key in bbl_index:
        return bbl_index[key]
    akey = ((row.get("violation_location_house") or "").strip().upper(),
            norm_street(row.get("violation_location_street_name")))
    return addr_index.get(akey)


def fetch_portfolio(properties, log=print):
    clauses, bbl_index, addr_index = build_indexes(properties)
    seen, matched = set(), []
    for i in range(0, len(clauses), BATCH_SIZE):
        batch = clauses[i:i + BATCH_SIZE]
        rows = api_get(" OR ".join(batch))
        for row in rows:
            t = (row.get("ticket_number") or "").strip()
            if not t or t in seen:
                continue
            seen.add(t)
            prop = match_property(row, bbl_index, addr_index)
            if prop is not None:
                matched.append((row, prop))
        log(f"  API batch {i // BATCH_SIZE + 1}/{(len(clauses) - 1) // BATCH_SIZE + 1}"
            f" — {len(seen)} tickets so far")
    return matched


def fmt_amount(v):
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"


def to_csv_row(row, prop):
    date = ""
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", row.get("violation_date") or "")
    if m:
        date = f"{m.group(2)}/{m.group(3)}/{m.group(1)}"
    respondent = (row.get("respondent_last_name") or "").strip()
    agency_raw = (row.get("issuing_agency") or "").strip()
    borough = prop["borough"] or (row.get("violation_location_borough") or "").strip().title()
    portal = PORTAL_BASE + "?" + urllib.parse.urlencode({
        "house_number": prop["house"], "street_name": prop["street"], "borough": borough})
    return {
        "Property": f'{prop["house"]} {prop["street"]}',
        "Portal URL": portal,
        "Ticket #": (row.get("ticket_number") or "").strip(),
        "Respondent": respondent,
        "Date": date,
        "Description": (row.get("charge_1_code_description") or "").strip(),
        "Agency": AGENCY_MAP.get(agency_raw.upper(), agency_raw),
        "Status": (row.get("hearing_status") or "").strip().upper(),
        "Amount Due": fmt_amount(row.get("balance_due")),
    }


def run_fetch(days=180, dry_run=False, log=print):
    properties = dataio.load_properties()
    log(f"  Querying NYC Open Data (OATH/ECB) for {len(properties)} properties…")
    fetched = fetch_portfolio(properties, log=log)

    # Existing master log, raw rows preserved
    existing_rows, fieldnames = [], CSV_HEADER
    if dataio.VIO_CSV.exists():
        with open(dataio.VIO_CSV, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or CSV_HEADER
            existing_rows = list(reader)
    by_ticket = {(r.get("Ticket #") or "").strip(): r for r in existing_rows}

    cutoff = datetime.now() - timedelta(days=days)
    updated, added, refound = [], [], 0

    for row, prop in fetched:
        new = to_csv_row(row, prop)
        ticket = new["Ticket #"]
        old = by_ticket.get(ticket)
        if old is not None:
            refound += 1
            old_status = dataio.clean(old.get("Status") or "")
            old_amount = fmt_amount(dataio.parse_amount(old.get("Amount Due")))
            if (new["Status"] and new["Status"] != old_status.upper()) or new["Amount Due"] != old_amount:
                updated.append((ticket, f'{old_status} / {old_amount}',
                                f'{new["Status"]} / {new["Amount Due"]}'))
                if not dry_run:
                    old["Status"] = new["Status"]
                    old["Amount Due"] = new["Amount Due"]
        else:
            try:
                is_recent = datetime.strptime(new["Date"], "%m/%d/%Y") >= cutoff
            except ValueError:
                is_recent = False
            if is_recent:
                added.append(new)

    if not dry_run and (updated or added):
        with open(dataio.VIO_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(existing_rows)
            writer.writerows({k: r.get(k, "") for k in fieldnames} for r in added)

    summary = {
        "when": datetime.now().isoformat(timespec="seconds"),
        "days": days,
        "dry_run": dry_run,
        "tickets_fetched": len(fetched),
        "existing_refound": refound,
        "existing_total": len(existing_rows),
        "updated": len(updated),
        "added": len(added),
        "changed": bool(updated or added) and not dry_run,
    }

    log(f"\n  Tickets on file for these parcels: {len(fetched)}"
        f"  ·  re-found {refound}/{len(existing_rows)} known tickets")
    for t, old, new in updated[:20]:
        log(f"  {'would update' if dry_run else 'updated'}  {t}:  {old}  ->  {new}")
    if len(updated) > 20:
        log(f"  … and {len(updated) - 20} more updates")
    for r in added[:20]:
        log(f"  {'would add' if dry_run else 'added'}  {r['Ticket #']}  {r['Property']}"
            f"  {r['Date']}  {r['Status']}  {r['Amount Due']}")
    if len(added) > 20:
        log(f"  … and {len(added) - 20} more new tickets")
    log(f"\n  Summary: {summary['updated']} updated · {summary['added']} new"
        + ("  (dry run — nothing written)" if dry_run else ""))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch OATH/ECB violations from NYC Open Data")
    parser.add_argument("--days", type=int, default=180,
                        help="only add NEW tickets issued in the last N days (default 180)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change without writing")
    args = parser.parse_args()
    try:
        run_fetch(days=args.days, dry_run=args.dry_run)
    except urllib.error.URLError as e:
        print(f"  API request failed: {e}")
        sys.exit(1)
