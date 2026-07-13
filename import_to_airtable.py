"""
One-time script to import all existing violations from
violations_found.csv and hpd_found.csv into Airtable.
Run once: python import_to_airtable.py
"""
import os, csv, requests
from dotenv import load_dotenv

load_dotenv()

AIRTABLE_TOKEN   = os.getenv("AIRTABLE_TOKEN")
AIRTABLE_BASE_ID = os.getenv("AIRTABLE_BASE_ID")
AIRTABLE_TABLE   = "Violations"
URL     = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}/{AIRTABLE_TABLE}"
HEADERS = {"Authorization": f"Bearer {AIRTABLE_TOKEN}", "Content-Type": "application/json"}

def get_existing_tickets():
    existing = set()
    offset = None
    while True:
        params = {"fields[]": "Ticket #", "pageSize": 100}
        if offset: params["offset"] = offset
        r = requests.get(URL, headers=HEADERS, params=params, timeout=15)
        data = r.json()
        for rec in data.get("records", []):
            t = rec.get("fields", {}).get("Ticket #", "")
            if t: existing.add(t.strip())
        offset = data.get("offset")
        if not offset: break
    return existing

def push_batch(batch):
    r = requests.post(URL, headers=HEADERS, json={"records": batch}, timeout=15)
    if r.status_code != 200:
        print(f"   ❌ Error: {r.text[:200]}")
        return 0
    return len(batch)

def import_file(fname, agency):
    if not os.path.exists(fname):
        print(f"   ⚠️  {fname} not found — skipping.")
        return 0

    existing = get_existing_tickets()
    print(f"   📋 {len(existing)} records already in Airtable.")

    records = []
    with open(fname, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ticket = row.get("Ticket #", row.get("ID", "")).strip()
            if not ticket or ticket in existing:
                continue
            amt_str = row.get("Amount Due", "0").replace("$","").replace(",","").strip()
            try:    amt = float(amt_str)
            except: amt = 0.0
            records.append({"fields": {
                "Ticket #":    ticket,
                "Property":    row.get("Property", ""),
                "Agency":      agency,
                "Date":        row.get("Date", ""),
                "Description": row.get("Description", ""),
                "Amount Due":  amt,
                "Respondent":  row.get("Respondent", ""),
                "Raw Status":  row.get("Status", row.get("Raw Status", "")),
                "Status":      "New",
            }})

    if not records:
        print(f"   ✅ Nothing new to import from {fname}.")
        return 0

    pushed = 0
    for i in range(0, len(records), 10):
        pushed += push_batch(records[i:i+10])
    return pushed

print("\n=== Airtable Import ===\n")
print(f"Importing ECB violations...")
ecb = import_file("violations_found.csv", "ECB")
print(f"   ✅ {ecb} ECB records pushed.\n")

print(f"Importing HPD violations...")
hpd = import_file("hpd_found.csv", "HPD")
print(f"   ✅ {hpd} HPD records pushed.\n")

print(f"=== Done! {ecb + hpd} total records imported to Airtable ===\n")
