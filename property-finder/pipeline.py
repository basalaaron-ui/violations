"""Orchestrator: PLUTO screen -> ACRIS join -> value/maturity/score -> outputs.

Run:  python pipeline.py --boroughs BX BK QN            # default: all five
      python pipeline.py --boroughs BX --limit 200      # quick sample
      python pipeline.py --no-cache                      # force fresh API pulls

Writes output/candidates.csv and output/targets.html.
"""
import argparse
import csv
from collections import defaultdict
from pathlib import Path

import config as C
import nyc_api as api
import analysis as A
from build_html import build_html

OUT_DIR = Path(__file__).resolve().parent / "output"
OUT_DIR.mkdir(exist_ok=True)

RELEVANT_DOC_TYPES = ("DEED", "DEEDO", "MTGE", "SAT", "PSAT")


def norm(x):
    """PLUTO/ACRIS block & lot -> canonical integer-as-string join key."""
    try:
        return str(int(float(x)))
    except (TypeError, ValueError):
        return str(x).strip()


def bbl_clean(bbl):
    return norm(bbl)  # e.g. "5011330013.00000000" -> "5011330013"


def docid_year(docid):
    if len(docid) >= 8 and docid[:8].isdigit():
        y = int(docid[:4])
        if 1990 <= y <= 2035:
            return y
    return None


def acris_doc_url(docid):
    return f"https://a836-acris.nyc.gov/DS/DocumentSearch/DocumentDetail?doc_id={docid}"


def acris_parcel_url(boro, block, lot):
    return (f"https://a836-acris.nyc.gov/bblsearch/bblsearch.asp?"
            f"borough={boro}&block={block}&lot={lot}")


def pluto_url(bbl):
    return f"https://zola.planning.nyc.gov/l/lot/{bbl[0]}/{int(bbl[1:6])}/{int(bbl[6:10])}"


def fetch_pluto(boroughs, limit, use_cache, log):
    excl = ",".join(f"'{c}'" for c in C.EXCLUDE_BLDG_CLASSES)
    gate = int(31500 * C.DOOR_GATE_CUSHION)
    rows = []
    for boro in boroughs:
        where = (
            f"borough='{boro}' "
            f"AND unitsres between {C.UNITS_MIN} and {C.UNITS_MAX} "
            f"AND yearbuilt>0 AND yearbuilt<{C.YEAR_BUILT_MAX} "
            f"AND (starts_with(bldgclass,'C') OR starts_with(bldgclass,'D')) "
            f"AND bldgclass not in ({excl}) "
            f"AND assesstot < {gate}*unitsres")
        got = api.soda_get_all(C.PLUTO, where=where, use_cache=use_cache, log=log)
        log(f"  PLUTO {boro}: {len(got)} candidates")
        rows.extend(got)
    if limit:
        rows = rows[:limit]
    return rows


def build_legals_index(pluto, use_cache, log):
    """Return (docid -> [bbl,...], bbl -> pluto_row).  Uses exact-BBL OR
    batches against ACRIS Legals."""
    by_key, clauses = {}, []
    for p in pluto:
        boro = C.BORO_ABBR_TO_CODE[p["borough"]]
        block, lot = norm(p["block"]), norm(p["lot"])
        key = (boro, block, lot)
        by_key[key] = p
        clauses.append(f"(borough='{boro}' AND block='{block}' AND lot='{lot}')")

    log(f"  querying ACRIS Legals for {len(clauses)} parcels…")
    legals = api.batched_or(C.ACRIS_LEGALS, clauses,
                            select="document_id,borough,block,lot",
                            batch_size=C.BBL_BATCH_SIZE, use_cache=use_cache, log=log)

    docid_to_bbls = defaultdict(list)
    bbl_to_pluto = {}
    for L in legals:
        key = (L["borough"], norm(L["block"]), norm(L["lot"]))
        p = by_key.get(key)
        if not p:
            continue
        bbl = bbl_clean(p["bbl"])
        bbl_to_pluto[bbl] = p
        did = L["document_id"]
        if bbl not in docid_to_bbls[did]:
            docid_to_bbls[did].append(bbl)
    log(f"  {len(docid_to_bbls)} documents map to {len(bbl_to_pluto)} of our parcels")
    return docid_to_bbls, bbl_to_pluto


def fetch_master(docid_to_bbls, use_cache, log):
    # Drop pre-low-rate-era documents by their date-prefixed id (no request).
    wanted = [d for d in docid_to_bbls
              if (docid_year(d) is None or docid_year(d) >= C.LOW_RATE_START_YEAR)]
    log(f"  {len(wanted)}/{len(docid_to_bbls)} docs are {C.LOW_RATE_START_YEAR}+ "
        f"(fetching Master for those)")
    where = "doc_type in (" + ",".join(f"'{t}'" for t in RELEVANT_DOC_TYPES) + ")"
    master = api.fetch_by_document_ids(
        C.ACRIS_MASTER, wanted,
        select="document_id,doc_type,document_date,document_amt",
        extra_where=where, use_cache=use_cache, log=log)
    return master


def group_docs_by_bbl(master, docid_to_bbls):
    """bbl -> {'sales':[], 'mortgages':[], 'sats':[]} with parsed rows."""
    per_bbl = defaultdict(lambda: {"sales": [], "mortgages": [], "sats": []})
    for m in master:
        rec = {
            "date": A.parse_date(m.get("document_date")),
            "amount": A.to_float(m.get("document_amt")) or 0.0,
            "document_id": m["document_id"],
            "doc_type": m["doc_type"],
        }
        bucket = ("mortgages" if m["doc_type"] == C.DOC_MORTGAGE
                  else "sats" if m["doc_type"] in (C.DOC_SATISFACTION, "PSAT")
                  else "sales")  # DEED / DEEDO
        for bbl in docid_to_bbls.get(m["document_id"], ()):
            per_bbl[bbl][bucket].append(rec)
    return per_bbl


def fetch_lenders(records, use_cache, log):
    """Populate 'lender' on records that have a low-rate mortgage docid."""
    docids = [r["low_rate_mtge_docid"] for r in records if r["low_rate_mtge_docid"]]
    if not docids:
        return
    log(f"  fetching lender names for {len(set(docids))} mortgages…")
    parties = api.fetch_by_document_ids(
        C.ACRIS_PARTIES, docids,
        select="document_id,name,party_type",
        extra_where=f"party_type='{C.PARTY_TYPE_LENDER}'",
        use_cache=use_cache, log=log)
    lender_by_doc = {}
    for p in parties:                       # first lender name per doc
        lender_by_doc.setdefault(p["document_id"], p.get("name", "").strip())
    for r in records:
        r["lender"] = lender_by_doc.get(r["low_rate_mtge_docid"], "")


def build_records(bbl_to_pluto, per_bbl, log):
    records = []
    for bbl, p in bbl_to_pluto.items():
        docs = per_bbl.get(bbl, {"sales": [], "mortgages": [], "sats": []})
        value = A.estimate_value(p, docs["sales"])
        mort = A.analyze_mortgages(docs["mortgages"], docs["sales"], docs["sats"])
        sc, flags = A.score(value, mort)

        boro = C.BORO_ABBR_TO_CODE[p["borough"]]
        block, lot = norm(p["block"]), norm(p["lot"])
        units = int(float(p["unitsres"]))
        records.append({
            "score": sc,
            "flags": "; ".join(flags),
            "address": p.get("address", ""),
            "borough": C.BORO_CODE_TO_NAME[boro],
            "zip": p.get("zipcode", ""),
            "units": units,
            "year_built": int(float(p.get("yearbuilt", 0))),
            "bldg_class": p.get("bldgclass", ""),
            "owner": p.get("ownername", ""),
            "per_door": value["per_door"],
            "market_value": value["market_value"],
            "value_basis": value["value_basis"],
            "assessed_value_est": value["assessed_value_est"],
            "last_sale_price": value["last_sale_price"],
            "last_sale_date": value["last_sale_date"].isoformat() if value["last_sale_date"] else "",
            "under_70k_door": bool(value["per_door"] and value["per_door"] <= C.MAX_PER_DOOR),
            "low_rate_mtge_date": mort["low_rate_mtge_date"].isoformat() if mort["low_rate_mtge_date"] else "",
            "low_rate_mtge_amt": mort["low_rate_mtge_amt"],
            "low_rate_mtge_docid": mort["low_rate_mtge_docid"] or "",
            "est_maturity_10yr": mort["primary_maturity"].isoformat() if mort["primary_maturity"] else "",
            "months_to_maturity": mort["months_to_maturity"],
            "maturing_soon": mort["maturing_soon"],
            "lender": "",
            "bbl": bbl,
            "acris_mortgage_url": acris_doc_url(mort["low_rate_mtge_docid"]) if mort["low_rate_mtge_docid"] else "",
            "acris_parcel_url": acris_parcel_url(boro, block, lot),
            "pluto_url": pluto_url(bbl),
            "lat": p.get("latitude", ""),
            "lon": p.get("longitude", ""),
        })
    records.sort(key=lambda r: r["score"], reverse=True)
    log(f"  built {len(records)} scored records")
    return records


CSV_FIELDS = [
    "score", "flags", "address", "borough", "zip", "units", "year_built",
    "bldg_class", "owner", "per_door", "market_value", "value_basis",
    "assessed_value_est", "last_sale_price", "last_sale_date", "under_70k_door",
    "low_rate_mtge_date", "low_rate_mtge_amt", "est_maturity_10yr",
    "months_to_maturity", "maturing_soon", "lender", "bbl",
    "acris_mortgage_url", "acris_parcel_url", "pluto_url", "lat", "lon",
]


def write_csv(records, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in records:
            w.writerow(r)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--boroughs", nargs="+", default=["MN", "BX", "BK", "QN", "SI"],
                    help="borough abbreviations (MN BX BK QN SI)")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap number of PLUTO candidates (for quick tests)")
    ap.add_argument("--no-cache", action="store_true", help="force fresh API pulls")
    ap.add_argument("--top", type=int, default=None,
                    help="only keep the top N by score in the outputs")
    args = ap.parse_args()
    use_cache = not args.no_cache
    log = print

    log(f"App token: {'yes' if api.has_app_token() else 'no (rate-limited)'}")
    log("1) PLUTO screen")
    pluto = fetch_pluto(args.boroughs, args.limit, use_cache, log)
    log(f"   total candidates: {len(pluto)}")

    log("2) ACRIS Legals join")
    docid_to_bbls, bbl_to_pluto = build_legals_index(pluto, use_cache, log)

    log("3) ACRIS Master (deeds/mortgages/satisfactions)")
    master = fetch_master(docid_to_bbls, use_cache, log)
    per_bbl = group_docs_by_bbl(master, docid_to_bbls)

    log("4) score & rank")
    records = build_records(bbl_to_pluto, per_bbl, log)
    if args.top:
        records = records[:args.top]

    log("5) ACRIS Parties (lender names)")
    fetch_lenders(records, use_cache, log)

    csv_path = OUT_DIR / "candidates.csv"
    html_path = OUT_DIR / "targets.html"
    write_csv(records, csv_path)
    build_html(records, html_path)
    log(f"\nDone. {len(records)} candidates")
    log(f"  CSV : {csv_path}")
    log(f"  HTML: {html_path}")


if __name__ == "__main__":
    main()
