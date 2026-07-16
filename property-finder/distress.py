"""Operational / financial distress signals, joined by BBL.

Two independent "motivated seller" tells that don't depend on the mortgage:
  * open HPD violations, especially class C ("immediately hazardous")
  * appearance on the DOF tax-lien-sale notice list (unpaid taxes / water)

Both are strong evidence of a landlord who is stretched or tired and may want
out even if the building isn't underwater.
"""
import config as C
import nyc_api as api


def fetch_hpd_open(bbls, use_cache=True, log=print):
    """bbl -> {'open': int, 'class_c': int} for OPEN violations."""
    rows = api.fetch_in(
        C.HPD_VIOLATIONS, "bbl", bbls,
        select="bbl,class,count(*)", group="bbl,class",
        extra_where="violationstatus='Open'", use_cache=use_cache, log=log)
    out = {}
    for r in rows:
        bbl = r.get("bbl")
        if not bbl:
            continue
        n = int(r.get("count", 0) or 0)
        d = out.setdefault(bbl, {"open": 0, "class_c": 0})
        d["open"] += n
        if (r.get("class") or "").upper() == "C":
            d["class_c"] += n
    return out


def fetch_liens(boroughs, use_cache=True, log=print):
    """bbl -> {'cycle','month','water_only'} for the most-recent recent lien
    notice.  The lien list keys on borough/block/lot, so we rebuild the BBL."""
    codes = [C.BORO_ABBR_TO_CODE[b] for b in boroughs]
    in_b = ",".join(f"'{c}'" for c in codes)
    where = f"month >= '{C.LIEN_SINCE_YEAR}-01-01' AND borough in ({in_b})"
    rows = api.soda_get_all(
        C.TAX_LIEN, where=where,
        select="borough,block,lot,cycle,month,water_debt_only",
        use_cache=use_cache, log=log)
    out = {}
    for r in rows:
        try:
            bbl = (r["borough"] + str(int(r["block"])).zfill(5)
                   + str(int(r["lot"])).zfill(4))
        except (KeyError, ValueError):
            continue
        info = {
            "cycle": (r.get("cycle") or "").strip(),
            "month": (r.get("month") or "")[:10],
            "water_only": (r.get("water_debt_only") or "").upper() == "YES",
        }
        prev = out.get(bbl)
        if not prev or info["month"] > prev["month"]:
            out[bbl] = info
    return out


def fetch_all(bbls, boroughs, use_cache=True, log=print):
    """Return {bbl: {'open','class_c','lien'}} for the candidate set."""
    log(f"  HPD open violations for {len(bbls)} parcels…")
    hpd = fetch_hpd_open(bbls, use_cache=use_cache, log=log)
    log(f"  tax-lien notices ({C.LIEN_SINCE_YEAR}+)…")
    liens = fetch_liens(boroughs, use_cache=use_cache, log=log)
    lien_hits = sum(1 for b in bbls if b in liens)
    log(f"  {len(hpd)} parcels with open violations, {lien_hits} on the lien list")
    out = {}
    for bbl in bbls:
        h = hpd.get(bbl, {"open": 0, "class_c": 0})
        out[bbl] = {"open": h["open"], "class_c": h["class_c"],
                    "lien": liens.get(bbl)}
    return out
