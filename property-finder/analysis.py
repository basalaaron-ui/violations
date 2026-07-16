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
def borough_multiplier(pluto):
    name = C.BORO_CODE_TO_NAME.get(C.BORO_ABBR_TO_CODE.get(pluto.get("borough", "")))
    return C.BOROUGH_MARKET_MULTIPLIER.get(name, C.DEFAULT_MARKET_MULTIPLIER)


def estimate_value(pluto, sales):
    """Return market-value estimate + basis for a building.

    Prefer the most recent arm's-length recorded sale within the lookback
    window; fall back to assessed value calibrated to real sale prices by a
    per-borough multiplier (see config).  Guards against bulk/portfolio deeds
    whose price covers many parcels at once.
    """
    units = int(float(pluto["unitsres"]))
    assess = to_float(pluto.get("assesstot")) or 0.0
    mult = borough_multiplier(pluto)
    assessed_value = assess * mult if assess else 0.0

    cutoff = add_years(C.TODAY, -C.SALE_LOOKBACK_YEARS)
    recent = [s for s in sales
              if s["date"] and s["date"] >= cutoff
              and (s["amount"] or 0) >= C.MIN_REAL_SALE_PRICE]
    recent.sort(key=lambda s: s["date"], reverse=True)

    bulk_suspected = False
    if recent:
        sale = recent[0]
        # A price >2.5x the calibrated assessment-based value is almost
        # certainly a multi-building/portfolio deed, not this parcel.
        if assessed_value and sale["amount"] > 2.5 * assessed_value:
            bulk_suspected = True
            market_value = assessed_value
            basis = f"assessed x{mult:g} (sale ${sale['amount']:,.0f} looks like bulk)"
            sale_price, sale_date = sale["amount"], sale["date"]
        else:
            market_value = sale["amount"]
            basis = f"recorded sale {sale['date']:%Y-%m-%d}"
            sale_price, sale_date = sale["amount"], sale["date"]
    else:
        market_value = assessed_value
        basis = f"assessed x{mult:g} (calibrated)"
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
    """Characterize the operative low-rate-era loan: its size, its estimated
    maturity, whether it's still outstanding, and how long the owner has held.

    mortgages/deeds/satisfactions: lists of {date, amount, document_id}.
    """
    mtges = sorted([m for m in mortgages if m["date"]], key=lambda m: m["date"])
    last_mortgage = mtges[-1] if mtges else None

    low = [m for m in mtges
           if C.LOW_RATE_START_YEAR <= m["date"].year <= C.LOW_RATE_END_YEAR]
    # Latest low-rate mortgage drives the maturity (most recent financing sets
    # the current term); the largest low-rate mortgage drives the balance
    # (avoids picking a tiny CEMA "new money" gap over the real senior loan).
    low_rate_mtge = low[-1] if low else None
    biggest_low = max(low, key=lambda m: m["amount"]) if low else None

    # Owner tenure: most recent real (non-$0) purchase.
    real_purchases = sorted(
        [s for s in deeds if s["date"] and (s["amount"] or 0) >= C.MIN_REAL_SALE_PRICE],
        key=lambda s: s["date"])
    last_purchase = real_purchases[-1] if real_purchases else None
    years_owned = ((C.TODAY - last_purchase["date"]).days / 365.25
                   if last_purchase else None)

    result = {
        "last_mortgage_date": last_mortgage["date"] if last_mortgage else None,
        "low_rate_mtge_date": None,
        "low_rate_mtge_amt": None,          # size of the *senior* low-rate loan
        "low_rate_mtge_nparcels": 1,        # >1 => blanket/portfolio loan
        "low_rate_mtge_docid": None,
        "primary_maturity": None,
        "months_to_maturity": None,
        "maturing_soon": False,
        "refinanced_since": False,
        "satisfied_since": False,
        "has_low_rate_mortgage": False,
        "acq_purchase_price": None,         # purchase near the loan (acquisition)
        "last_purchase_date": last_purchase["date"] if last_purchase else None,
        "years_owned": years_owned,
        "recently_acquired": (years_owned is not None
                              and years_owned < C.RECENT_PURCHASE_YEARS),
    }
    if not low_rate_mtge:
        return result

    d = low_rate_mtge["date"]
    result.update({
        "low_rate_mtge_date": d,
        "low_rate_mtge_amt": biggest_low["amount"],
        "low_rate_mtge_nparcels": biggest_low.get("n_parcels", 1),
        "low_rate_mtge_docid": biggest_low["document_id"],
        "has_low_rate_mortgage": True,
    })

    maturities = {t: add_years(d, t) for t in C.ASSUMED_TERMS_YEARS}
    result["primary_maturity"] = maturities[C.PRIMARY_TERM_YEARS]
    result["months_to_maturity"] = months_between(C.TODAY, maturities[C.PRIMARY_TERM_YEARS])
    result["maturing_soon"] = any(
        -C.MATURITY_WINDOW_MONTHS_BACK <= months_between(C.TODAY, mat)
        <= C.MATURITY_WINDOW_MONTHS_AHEAD for mat in maturities.values())

    result["refinanced_since"] = any(m["date"] > d for m in mtges) or any(
        s["date"] and s["date"] > d and (s["amount"] or 0) >= C.MIN_REAL_SALE_PRICE
        for s in deeds)
    result["satisfied_since"] = any(
        s["date"] and s["date"] > d for s in satisfactions)

    # Acquisition loan? a real purchase within ~18 months of the mortgage.
    near = [p for p in real_purchases
            if abs(months_between(p["date"], d)) <= 18]
    if near:
        result["acq_purchase_price"] = min(
            near, key=lambda p: abs(months_between(p["date"], d)))["amount"]
    return result


def compute_pressure(value, mort, units):
    """Turn the loan + current value into value-loss / leverage metrics.

    implied_ltv_now = senior low-rate loan / today's estimated value.  As value
    falls this rises; past ~0.90 a refinance is hard, past 1.0 the owner is
    underwater — the core "forced to sell" signal.  Blanket/portfolio loans
    (one document over many parcels, or an implausible per-unit amount) can't be
    pinned to one building, so we don't derive a per-building LTV from them.
    """
    cur = value.get("market_value")
    assessed = value.get("assessed_value_est")
    loan = mort.get("low_rate_mtge_amt")
    purchase = mort.get("acq_purchase_price")
    out = {"implied_ltv_now": None, "origination_value": None,
           "origination_basis": None, "value_change_pct": None,
           "loan_maybe_understated": False, "loan_blanket": False}
    if not cur:
        return out

    # Is the recorded loan a blanket/portfolio loan (can't pin to one building)?
    # Tell-tales: one document over several parcels, an implausible per-unit
    # amount, or an implied LTV no lender would ever originate.
    blanket = bool(loan) and (
        mort.get("low_rate_mtge_nparcels", 1) > 1
        or (units and loan / units > C.MAX_PLAUSIBLE_LOAN_PER_UNIT)
        or (loan / cur > C.MAX_PLAUSIBLE_LTV))
    out["loan_blanket"] = blanket
    usable_loan = loan if (loan and not blanket) else None
    if usable_loan:
        out["implied_ltv_now"] = round(usable_loan / cur, 3)

    # Reject a "purchase" anchor that's really a below-market/intra-family
    # transfer: one that dwarfs the assessment (portfolio deed), or that is
    # smaller than its own acquisition loan (you don't borrow more than you pay).
    purchase_bulk = bool(purchase) and assessed and purchase > 2.5 * assessed
    purchase_toolow = bool(purchase) and usable_loan and purchase < usable_loan
    usable_purchase = (purchase if (purchase and not purchase_bulk
                                    and not purchase_toolow) else None)

    # "Then" value: prefer a clean per-building purchase price; else back it out
    # of a usable (non-blanket) loan at a typical origination LTV.
    if usable_purchase:
        orig = usable_purchase
        out["origination_basis"] = "purchase price"
        if usable_loan and usable_loan < 0.5 * orig:
            out["loan_maybe_understated"] = True  # CEMA — true leverage higher
        # Value change is only meaningful vs. a real purchase price; from a loan
        # it's just a restatement of LTV (0.70/LTV−1) and blows up for CEMA gaps.
        out["value_change_pct"] = round(cur / orig - 1, 3)
    elif usable_loan:
        orig = usable_loan / C.ASSUMED_LTV_AT_ORIGINATION
        out["origination_basis"] = "loan ÷ assumed LTV"
    else:
        orig = None
    out["origination_value"] = round(orig) if orig else None
    return out


# ---------------------------------------------------------------------------
# scoring — refinance / value-loss pressure ("likely to sell")
# ---------------------------------------------------------------------------
def score(value, mort, pressure, distress=None):
    """0-100 sell-pressure score + human-readable flags.

    Weighting (tunable here):
      * up to 45 pts — leverage / value loss (implied current LTV)
      * up to 30 pts — estimated maturity near/at today (the trigger)
      * up to 10 pts — loan still outstanding + long owner tenure
      * up to 15 pts — operational/financial distress (HPD hazards, tax liens)
    A recent purchase strongly dampens the whole score (unlikely seller).
    """
    distress = distress or {}
    flags = []
    pts = 0.0

    # --- leverage / value-loss (the core signal) ----------------------------
    # Leverage (implied current LTV) is what actually forces a sale, so it's the
    # primary signal.  A value decline with unknown leverage is a weaker,
    # secondary signal (the owner may still have plenty of equity).
    ltv = pressure["implied_ltv_now"]
    vc = pressure["value_change_pct"]
    if ltv is not None:
        pts += 45 * _clamp((ltv - 0.55) / (1.05 - 0.55))
        if ltv >= C.LTV_UNDERWATER:
            flags.append("underwater: est. LTV ≥ 100%")
        elif ltv >= C.LTV_REFI_HARD:
            flags.append("refi hard: est. LTV ≥ 90%")
        if vc is not None and vc <= -0.10:
            flags.append(f"value down ~{abs(round(vc*100))}% since financing (est.)")
        if pressure["loan_maybe_understated"]:
            flags.append("loan may be understated (CEMA) — real leverage higher")
    elif vc is not None:                         # value fell, leverage unknown
        pts += 27 * _clamp(-vc / 0.50)
        if vc <= -0.10:
            flags.append(f"value down ~{abs(round(vc*100))}% since purchase, but leverage unknown")
    else:
        flags.append("no clean per-building loan/value basis")
    if pressure["loan_blanket"]:
        flags.append("blanket/portfolio loan — per-building leverage N/A")

    # --- maturity timing (the trigger) --------------------------------------
    if mort["has_low_rate_mortgage"]:
        m = mort["months_to_maturity"]
        if -C.MATURITY_WINDOW_MONTHS_BACK <= m <= C.MATURITY_WINDOW_MONTHS_AHEAD:
            dist = abs(m - 6)
            span = max(C.MATURITY_WINDOW_MONTHS_AHEAD, C.MATURITY_WINDOW_MONTHS_BACK + 6)
            pts += 30 * max(0.0, 1 - dist / span)
        if mort["maturing_soon"]:
            flags.append("maturing soon (est.)")
        if m is not None and m < 0:
            flags.append("est. already matured")
    else:
        flags.append("no low-rate-era mortgage found")

    # --- outstanding + tenure -----------------------------------------------
    if mort["has_low_rate_mortgage"] and not mort["refinanced_since"]:
        pts += 5
    if mort["years_owned"] is None:
        pts += 5  # no recorded arm's-length purchase -> long/held; likely willing
    else:
        pts += 5 * _clamp(mort["years_owned"] / C.LONG_TENURE_YEARS)
    if mort["refinanced_since"]:
        flags.append("financed/sold since (may be refinanced)")
    if mort["satisfied_since"]:
        flags.append("satisfaction recorded since")

    # --- operational / financial distress (independent seller motivation) ---
    cc = distress.get("class_c", 0)
    opn = distress.get("open", 0)
    lien = distress.get("lien")
    dp = 10 * _clamp(cc / C.HAZARD_VIOL_FULL) + 2 * _clamp(opn / C.OPEN_VIOL_FULL)
    if lien:
        dp += 3 if lien.get("water_only") else 5
    pts += min(dp, 15)
    if cc > 0:
        flags.append(f"{cc} open hazardous (class C) HPD violation{'s' if cc!=1 else ''}")
    elif opn > 0:
        flags.append(f"{opn} open HPD violations")
    if lien:
        what = "water arrears" if lien.get("water_only") else "tax/water arrears"
        flags.append(f"on lien-sale list ({lien.get('cycle','')}, {lien.get('month','')[:7]}) — {what}")

    # --- unlikely-seller dampener -------------------------------------------
    if mort["recently_acquired"]:
        pts *= 0.35
        flags.append(f"recently acquired ~{mort['years_owned']:.0f}y ago (unlikely seller)")

    if value["bulk_sale_suspected"]:
        flags.append("bulk-sale price ignored")

    return round(pts, 1), flags


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))
