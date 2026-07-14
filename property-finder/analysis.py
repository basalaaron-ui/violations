"""Valuation, maturity estimation, and scoring for one candidate building.

Everything here is deliberately transparent: each number a candidate is ranked
on can be traced back to a recorded document or a clearly-labelled proxy.  See
the README for the fact-vs-proxy breakdown.
"""
from datetime import date

import config as C


# ---------------------------------------------------------------------------
# small date helpers
# ---------------------------------------------------------------------------
def parse_date(s):
    """ACRIS dates look like '2015-01-30T00:00:00.000'."""
    if not s or len(s) < 10:
        return None
    try:
        y, m, d = int(s[0:4]), int(s[5:7]), int(s[8:10])
        return date(y, m, d)
    except (ValueError, TypeError):
        return None


def add_years(d, n):
    try:
        return d.replace(year=d.year + n)
    except ValueError:  # Feb 29
        return d.replace(year=d.year + n, day=28)


def months_between(earlier, later):
    """Signed whole months from `earlier` to `later` (later-earlier)."""
    return (later.year - earlier.year) * 12 + (later.month - earlier.month)


def to_float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# valuation
# ---------------------------------------------------------------------------
def estimate_value(pluto, sales):
    """Return market-value estimate + basis for a building.

    Prefer the most recent arm's-length recorded sale within the lookback
    window; fall back to assessed value / ASSESS_RATIO.  Guards against
    bulk/portfolio deeds whose price covers many parcels at once.
    """
    units = int(float(pluto["unitsres"]))
    assess = to_float(pluto.get("assesstot")) or 0.0
    assessed_value = assess / C.ASSESS_RATIO if assess else 0.0

    cutoff = add_years(C.TODAY, -C.SALE_LOOKBACK_YEARS)
    recent = [s for s in sales
              if s["date"] and s["date"] >= cutoff
              and (s["amount"] or 0) >= C.MIN_REAL_SALE_PRICE]
    recent.sort(key=lambda s: s["date"], reverse=True)

    bulk_suspected = False
    if recent:
        sale = recent[0]
        sale_per_door = sale["amount"] / units
        # A price >2.5x the assessment-implied value on a per-door basis is
        # almost certainly a multi-building/portfolio deed, not this parcel.
        if assessed_value and sale["amount"] > 2.5 * assessed_value:
            bulk_suspected = True
            market_value = assessed_value
            basis = f"assessed/{C.ASSESS_RATIO:g} (sale ${sale['amount']:,.0f} looks like bulk)"
            sale_price, sale_date = sale["amount"], sale["date"]
        else:
            market_value = sale["amount"]
            basis = f"recorded sale {sale['date']:%Y-%m-%d}"
            sale_price, sale_date = sale["amount"], sale["date"]
    else:
        market_value = assessed_value
        basis = f"assessed/{C.ASSESS_RATIO:g}"
        sale_price, sale_date = None, None

    per_door = market_value / units if units else None
    return {
        "market_value": round(market_value) if market_value else None,
        "per_door": round(per_door) if per_door else None,
        "value_basis": basis,
        "last_sale_price": round(sale_price) if sale_price else None,
        "last_sale_date": sale_date,
        "assessed_value_est": round(assessed_value) if assessed_value else None,
        "bulk_sale_suspected": bulk_suspected,
    }


# ---------------------------------------------------------------------------
# mortgage timing
# ---------------------------------------------------------------------------
def analyze_mortgages(mortgages, deeds, satisfactions):
    """Pick the operative low-rate-era mortgage and estimate its maturity.

    mortgages/deeds/satisfactions: lists of {date, amount, document_id}.
    """
    mtges = sorted([m for m in mortgages if m["date"]], key=lambda m: m["date"])
    last_mortgage = mtges[-1] if mtges else None

    # Most recent mortgage recorded inside the low-rate window.
    low = [m for m in mtges
           if C.LOW_RATE_START_YEAR <= m["date"].year <= C.LOW_RATE_END_YEAR]
    low_rate_mtge = low[-1] if low else None

    result = {
        "last_mortgage_date": last_mortgage["date"] if last_mortgage else None,
        "last_mortgage_amt": last_mortgage["amount"] if last_mortgage else None,
        "low_rate_mtge_date": None,
        "low_rate_mtge_amt": None,
        "low_rate_mtge_docid": None,
        "assumed_maturities": {},       # term -> date
        "primary_maturity": None,       # under PRIMARY_TERM_YEARS
        "months_to_maturity": None,     # signed; negative = already matured
        "maturing_soon": False,
        "refinanced_since": False,
        "satisfied_since": False,
        "has_low_rate_mortgage": False,
    }
    if not low_rate_mtge:
        return result

    d = low_rate_mtge["date"]
    result.update({
        "low_rate_mtge_date": d,
        "low_rate_mtge_amt": low_rate_mtge["amount"],
        "low_rate_mtge_docid": low_rate_mtge["document_id"],
        "has_low_rate_mortgage": True,
    })

    maturities = {t: add_years(d, t) for t in C.ASSUMED_TERMS_YEARS}
    result["assumed_maturities"] = maturities
    primary = maturities[C.PRIMARY_TERM_YEARS]
    result["primary_maturity"] = primary
    result["months_to_maturity"] = months_between(C.TODAY, primary)

    # Maturing soon if ANY assumed term matures inside the pressure window.
    window_lo = -C.MATURITY_WINDOW_MONTHS_BACK
    window_hi = C.MATURITY_WINDOW_MONTHS_AHEAD
    result["maturing_soon"] = any(
        window_lo <= months_between(C.TODAY, mat) <= window_hi
        for mat in maturities.values())

    # Signals the low-rate loan may already be gone: a newer mortgage, a real
    # (non-$0, arm's-length) sale, or a satisfaction recorded after it.  A $0
    # deed is usually an LLC/estate restructure, not a refinancing event.
    result["refinanced_since"] = any(
        m["date"] > d for m in mtges) or any(
        s["date"] and s["date"] > d and (s["amount"] or 0) >= C.MIN_REAL_SALE_PRICE
        for s in deeds)
    result["satisfied_since"] = any(
        s["date"] and s["date"] > d for s in satisfactions)
    return result


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------
def score(value, mort):
    """0-100 refinance-pressure score + human-readable flags.

    Weighting (documented so you can tune in config/here):
      * up to 45 pts — cheaper $/door (the core value screen)
      * up to 40 pts — estimated maturity near/at today (refi pressure)
      * up to 15 pts — low-rate loan still apparently outstanding
    """
    flags = []
    pts = 0.0

    # --- value component -----------------------------------------------------
    per_door = value["per_door"]
    if per_door is not None and per_door <= C.MAX_PER_DOOR:
        pts += 45 * (C.MAX_PER_DOOR - per_door) / C.MAX_PER_DOOR
    if value["bulk_sale_suspected"]:
        flags.append("bulk-sale price ignored")

    # --- maturity component --------------------------------------------------
    if mort["has_low_rate_mortgage"]:
        m = mort["months_to_maturity"]
        # Peak score at maturity ~0-12 months out; taper on both sides.
        if -C.MATURITY_WINDOW_MONTHS_BACK <= m <= C.MATURITY_WINDOW_MONTHS_AHEAD:
            # distance from the "hot" center (6 months out)
            dist = abs(m - 6)
            span = max(C.MATURITY_WINDOW_MONTHS_AHEAD, C.MATURITY_WINDOW_MONTHS_BACK + 6)
            pts += 40 * max(0.0, 1 - dist / span)
        if mort["maturing_soon"]:
            flags.append("maturing soon (est.)")
        if m is not None and m < 0:
            flags.append("est. already matured")
    else:
        flags.append("no low-rate-era mortgage found")

    # --- still-outstanding component ----------------------------------------
    if mort["has_low_rate_mortgage"] and not mort["refinanced_since"]:
        pts += 15
    if mort["refinanced_since"]:
        flags.append("financed/sold since (may be refinanced)")
    if mort["satisfied_since"]:
        flags.append("satisfaction recorded since")

    return round(pts, 1), flags
