"""NYC Open Data HPD fetcher — violations and complaints.

API-based replacement for hpd_scanner_optimized.py, in the same style as
api_fetcher.py (the ECB fetcher): batched queries, BBL matching where
available, and in-place status updates of the master log.

- Violations:  wvxf-dwi5  (Housing Maintenance Code Violations)
- Complaints:  ygpa-z7cr  (Complaints and Problems — replaces the retired
               m6en-6e26 dataset the old scanner pointed at, which is why
               the log never contained complaints)

Behavior:
- New OPEN violations/complaint-problems within --days are appended to
  hpd_found.csv.
- Existing rows get their Status refreshed by ID (Open -> Close when the
  city closes them — the old scanner never did this).

Usage:  python hpd_api_fetcher.py [--days 180] [--dry-run]
Also importable: run_fetch(days=180, dry_run=False) -> summary dict
(webapp/server.py calls this on the same schedule as the ECB fetcher).
"""
import argparse
import csv
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "webapp"))
import dataio
from api_fetcher import norm_street, street_query_base, soql_str

VIOLATIONS_API = "https://data.cityofnewyork.us/resource/wvxf-dwi5.json"
COMPLAINTS_API = "https://data.cityofnewyork.us/resource/ygpa-z7cr.json"
BATCH_SIZE = 15
ID_BATCH_SIZE = 100
CSV_HEADER = ["Property", "Type", "ID", "Class", "Status", "Date",
              "Description", "Apt", "Story"]


def api_get(url, params):
    req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params))
    token = os.environ.get("NYC_APP_TOKEN")
    if token:
        req.add_header("X-App-Token", token)
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def clean_bbl(bbl):
    digits = re.sub(r"\D", "", bbl or "")
    return digits if len(digits) == 10 else None


def iso_date(raw):
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", raw or "")
    return f"{m.group(2)}/{m.group(3)}/{m.group(1)}" if m else ""


def build_clauses(properties, house_field, street_field):
    """Per-property SoQL clauses plus indexes to map API rows back."""
    clauses, bbl_index, addr_index = [], {}, {}
    for p in properties:
        house = p["house"].strip().upper()
        street = p["street"].strip()
        if not house or not street:
            continue
        bbl = clean_bbl(p["bbl"])
        if bbl:
            clauses.append(f"(bbl={soql_str(bbl)})")
            bbl_index[bbl] = p
        else:
            base = street_query_base(street)
            clauses.append(
                f"({house_field}={soql_str(house)}"
                f" AND upper({street_field}) like {soql_str(base + '%')})")
        addr_index[(house, norm_street(street))] = p
    return clauses, bbl_index, addr_index


def match_property(row, house_field, street_field, bbl_index, addr_index):
    p = bbl_index.get(clean_bbl(row.get("bbl")))
    if p is not None:
        return p
    key = ((row.get(house_field) or "").strip().upper(),
           norm_street(row.get(street_field)))
    return addr_index.get(key)


def fetch_open(url, properties, house_field, street_field, extra_where, log):
    clauses, bbl_index, addr_index = build_clauses(properties, house_field, street_field)
    matched, seen = [], set()
    total_batches = (len(clauses) - 1) // BATCH_SIZE + 1
    for i in range(0, len(clauses), BATCH_SIZE):
        where = "(" + " OR ".join(clauses[i:i + BATCH_SIZE]) + ") AND " + extra_where
        rows = api_get(url, {"$where": where, "$limit": "50000"})
        for row in rows:
            prop = match_property(row, house_field, street_field, bbl_index, addr_index)
            if prop is not None:
                matched.append((row, prop))
        log(f"  {url.rsplit('/', 1)[-1].split('.')[0]} batch {i // BATCH_SIZE + 1}/{total_batches}"
            f" — {len(matched)} open items so far")
    return matched


def fetch_status_by_ids(url, id_field, status_field, ids):
    """Current status for known IDs -> {id: status}."""
    statuses = {}
    ids = [i for i in ids if i]
    for i in range(0, len(ids), ID_BATCH_SIZE):
        batch = ids[i:i + ID_BATCH_SIZE]
        where = f"{id_field} in({','.join(soql_str(x) for x in batch)})"
        rows = api_get(url, {"$select": f"{id_field},{status_field}",
                             "$where": where, "$limit": "50000"})
        for row in rows:
            statuses[(row.get(id_field) or "").strip()] = (row.get(status_field) or "").strip()
    return statuses


def violation_row(row, prop):
    return {
        "Property": f'{prop["house"]} {prop["street"]}',
        "Type": "VIOLATION",
        "ID": (row.get("violationid") or "").strip(),
        "Class": (row.get("class") or "").strip(),
        "Status": (row.get("violationstatus") or "").strip().title(),
        "Date": iso_date(row.get("inspectiondate")),
        "Description": dataio.clean(row.get("novdescription") or row.get("ordernumber") or ""),
        "Apt": (row.get("apartment") or "").strip(),
        "Story": (row.get("story") or "").strip(),
    }


def complaint_row(row, prop):
    desc = " - ".join(x for x in [(row.get("major_category") or "").strip(),
                                  (row.get("minor_category") or "").strip()] if x)
    code = (row.get("problem_code") or "").strip()
    space = (row.get("space_type") or "").strip()
    if code:
        desc += f": {code}"
    if space:
        desc += f" ({space})"
    return {
        "Property": f'{prop["house"]} {prop["street"]}',
        "Type": "COMPLAINT",
        "ID": (row.get("problem_id") or "").strip(),
        "Class": (row.get("type") or "").strip(),
        "Status": (row.get("problem_status") or "").strip().title(),
        "Date": iso_date(row.get("received_date")),
        "Description": desc,
        "Apt": (row.get("apartment") or "").strip(),
        "Story": "",
    }


def run_fetch(days=180, dry_run=False, log=print):
    properties = dataio.load_properties()
    cutoff_iso = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%dT00:00:00")
    log(f"  Querying NYC Open Data (HPD) for {len(properties)} properties…")

    open_violations = fetch_open(
        VIOLATIONS_API, properties, "housenumber", "streetname",
        f"violationstatus='Open' AND inspectiondate > {soql_str(cutoff_iso)}", log)
    open_complaints = fetch_open(
        COMPLAINTS_API, properties, "house_number", "street_name",
        f"problem_status='OPEN' AND problem_duplicate_flag='N'"
        f" AND received_date > {soql_str(cutoff_iso)}", log)

    # Existing master log
    existing_rows, fieldnames = [], CSV_HEADER
    if dataio.HPD_CSV.exists():
        with open(dataio.HPD_CSV, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or CSV_HEADER
            existing_rows = list(reader)
    by_key = {((r.get("Type") or "").strip(), (r.get("ID") or "").strip()): r
              for r in existing_rows}

    # Refresh statuses of everything already in the log
    vio_ids = [k[1] for k in by_key if k[0] == "VIOLATION"]
    comp_ids = [k[1] for k in by_key if k[0] == "COMPLAINT"]
    current = {}
    if vio_ids:
        found = fetch_status_by_ids(VIOLATIONS_API, "violationid", "violationstatus", vio_ids)
        current.update({("VIOLATION", k): v for k, v in found.items()})
    if comp_ids:
        found = fetch_status_by_ids(COMPLAINTS_API, "problem_id", "problem_status", comp_ids)
        current.update({("COMPLAINT", k): v for k, v in found.items()})

    updated = []
    for key, status in current.items():
        old = by_key[key]
        new_status = status.title()
        if new_status and new_status != (old.get("Status") or "").strip().title():
            updated.append((key[1], old.get("Status"), new_status))
            if not dry_run:
                old["Status"] = new_status

    # Append new open items
    added = []
    for rows, to_row in ((open_violations, violation_row), (open_complaints, complaint_row)):
        for row, prop in rows:
            r = to_row(row, prop)
            if r["ID"] and (r["Type"], r["ID"]) not in by_key:
                by_key[(r["Type"], r["ID"])] = r
                added.append(r)

    if not dry_run and (updated or added):
        with open(dataio.HPD_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(existing_rows)
            writer.writerows({k: r.get(k, "") for k in fieldnames} for r in added)

    summary = {
        "when": datetime.now().isoformat(timespec="seconds"),
        "days": days,
        "dry_run": dry_run,
        "tickets_fetched": len(open_violations) + len(open_complaints),
        "existing_refound": len(current),
        "existing_total": len(existing_rows),
        "updated": len(updated),
        "added": len(added),
        "changed": bool(updated or added) and not dry_run,
    }

    log(f"\n  Open now: {len(open_violations)} violations, {len(open_complaints)} complaint problems"
        f"  ·  re-found {len(current)}/{len(existing_rows)} logged items")
    for i, (old_s, new_s) in [(t[0], (t[1], t[2])) for t in updated][:15]:
        log(f"  {'would update' if dry_run else 'updated'}  {i}:  {old_s}  ->  {new_s}")
    if len(updated) > 15:
        log(f"  … and {len(updated) - 15} more updates")
    for r in added[:15]:
        log(f"  {'would add' if dry_run else 'added'}  {r['Type'][:4]} {r['ID']}  {r['Property']}"
            f"  {r['Date']}  [{r['Class']}]  {r['Description'][:60]}")
    if len(added) > 15:
        log(f"  … and {len(added) - 15} more new items")
    log(f"\n  Summary: {summary['updated']} updated · {summary['added']} new"
        + ("  (dry run — nothing written)" if dry_run else ""))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch HPD violations & complaints from NYC Open Data")
    parser.add_argument("--days", type=int, default=180,
                        help="only add NEW items received in the last N days (default 180)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change without writing")
    args = parser.parse_args()
    try:
        run_fetch(days=args.days, dry_run=args.dry_run)
    except urllib.error.URLError as e:
        print(f"  API request failed: {e}")
        sys.exit(1)
