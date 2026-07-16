"""Tunable constants for the NYC maturing-mortgage property finder.

Everything a user might reasonably want to adjust lives here so the pipeline
code stays about *how* and this file stays about *what*. Read the README for
which of these are hard facts vs. proxies.
"""
from datetime import date

# ---------------------------------------------------------------------------
# NYC Open Data (Socrata / SODA) dataset ids
# ---------------------------------------------------------------------------
PLUTO = "64uk-42ks"          # MapPLUTO — parcels, units, year built, assessed value
ACRIS_MASTER = "bnx9-e6tj"   # Real Property Master — doc date / amount / type
ACRIS_LEGALS = "8h5j-fqxa"   # Real Property Legals — links document_id -> BBL
ACRIS_PARTIES = "636b-3b5g"  # Real Property Parties — lender / borrower names
HPD_VIOLATIONS = "wvxf-dwi5"  # HPD Housing Maintenance Code Violations
TAX_LIEN = "9rz4-mjek"        # DOF tax-lien-sale notice list (arrears)

SODA_BASE = "https://data.cityofnewyork.us/resource/{}.json"

# ---------------------------------------------------------------------------
# Borough code maps.  PLUTO uses a 2-letter `borough` plus numeric `borocode`;
# ACRIS uses the numeric borough code as a string ("1".."5").
# ---------------------------------------------------------------------------
BORO_CODE_TO_ABBR = {"1": "MN", "2": "BX", "3": "BK", "4": "QN", "5": "SI"}
BORO_ABBR_TO_CODE = {v: k for k, v in BORO_CODE_TO_ABBR.items()}
BORO_CODE_TO_NAME = {
    "1": "Manhattan", "2": "Bronx", "3": "Brooklyn",
    "4": "Queens", "5": "Staten Island",
}

# ---------------------------------------------------------------------------
# Building-selection filters  (HARD FACTS from PLUTO)
# ---------------------------------------------------------------------------
UNITS_MIN = 20
UNITS_MAX = 50
# Rent-stabilization research proxy: built before 1974, 6+ units, not condo/coop.
# Building classes C (walk-up apartments) and D (elevator apartments) capture
# rentals and exclude class R condos.  yearbuilt==0 means "unknown" in PLUTO.
YEAR_BUILT_MAX = 1974
BLDG_CLASS_PREFIXES = ("C", "D")
# Cooperative building classes — a co-op can't be bought as a rental building
# (the shareholders own it), so exclude them even though they're class C/D.
EXCLUDE_BLDG_CLASSES = ("C6", "C8", "D0", "D4")

# ---------------------------------------------------------------------------
# Valuation  (ESTIMATES / PROXIES)
# ---------------------------------------------------------------------------
# NYC assesses Class 2 rentals at ~45% of market value *on paper*, but for
# rent-stabilized buildings DOF's own market value is set off regulated income
# and runs well below real trading prices.  Empirically (from 335 arm's-length
# ACRIS sales in our own candidate set) actual sale price ≈ 3.9x TOTAL assessed
# value, not the 2.2x that assesstot/0.45 implies.  So when there's no recent
# sale we estimate market value as assesstot * (per-borough multiplier below).
# Re-derive these with tools/calibrate.py after a fresh pull.
ASSESS_RATIO = 0.45  # retained only for the PLUTO pre-filter gate
BOROUGH_MARKET_MULTIPLIER = {   # median(sale_price / assesstot), by borough
    "Manhattan": 4.74,          # calibrated on 1,302 arm's-length sales across
    "Bronx": 4.71,              # the full (un-gated) candidate pool — see
    "Brooklyn": 4.54,           # tools/calibrate.py
    "Queens": 4.75,
    "Staten Island": 4.71,      # too few SI sales; uses citywide median
}
DEFAULT_MARKET_MULTIPLIER = 4.71  # citywide median fallback
# Prefer a recorded ACRIS sale price if one exists within this many years.
SALE_LOOKBACK_YEARS = 8
# Ignore obviously non-arm's-length "sales" below this price (e.g. $0 / $10
# transfers between related LLCs, estate transfers).
MIN_REAL_SALE_PRICE = 100_000

# $/door is kept as an informational column but is NOT used to gate or rank —
# the screen is centered on value-loss / refinance pressure instead.  Set a
# value via --max-door on the CLI if you ever want to re-impose a cap.
MAX_PER_DOOR = 70_000

# Value-loss / underwater thesis.  Small-balance multifamily loans are
# typically underwritten near this loan-to-value at origination, so
# (recorded loan / today's value) is a proxy for how leveraged — and how
# stuck — the owner is now.  Above ~0.90 a refinance gets very hard; above
# 1.0 the owner is underwater and effectively forced to sell.
ASSUMED_LTV_AT_ORIGINATION = 0.70
LTV_REFI_HARD = 0.90        # refinancing becomes difficult
LTV_UNDERWATER = 1.00       # debt exceeds value
# A single mortgage can cover a whole portfolio ("blanket loan"); its full
# amount then can't be pinned to one building.  We detect those when one
# document covers multiple parcels, and as a backstop when the amount per unit
# is implausibly high for a 20-50 unit building.
MAX_PLAUSIBLE_LOAN_PER_UNIT = 400_000
# No lender originates far above 100% LTV; an implied LTV above this means the
# recorded loan almost certainly spans more than this one parcel (a
# consolidated/portfolio loan), so we don't trust it as per-building leverage.
MAX_PLAUSIBLE_LTV = 1.5

# ---------------------------------------------------------------------------
# Mortgage timing  (PROXIES — actual rate & maturity are not public)
# ---------------------------------------------------------------------------
# "Low-rate era": mortgages recorded in this window carried historically low
# multifamily rates.
LOW_RATE_START_YEAR = 2011
LOW_RATE_END_YEAR = 2021
# Common terms for a 20-50 unit loan.  The 10yr Fannie/Freddie Small Balance
# Loan is the most common, so it's weighted first.
ASSUMED_TERMS_YEARS = (10, 7, 5)
PRIMARY_TERM_YEARS = 10

# Owner-tenure signals for "likely to sell".  A long-held building carries a
# big embedded capital gain and often an owner ready to exit; someone who just
# bought is very unlikely to sell, so we penalize recent purchases.
LONG_TENURE_YEARS = 12       # held this long -> full tenure points
RECENT_PURCHASE_YEARS = 4    # bought within this -> "unlikely seller" penalty
# A loan is "maturing soon" (refi/sale pressure) if its estimated maturity
# falls between MONTHS_BACK ago and MONTHS_AHEAD from today.  Already-matured
# loans (negative months) are *more* distressed, so we look back a bit too.
MATURITY_WINDOW_MONTHS_BACK = 18
MATURITY_WINDOW_MONTHS_AHEAD = 36

TODAY = date.today()

# ---------------------------------------------------------------------------
# Operational / financial distress  (independent "motivated seller" signals)
# ---------------------------------------------------------------------------
# Open HPD class-C violations are "immediately hazardous" — a tired-landlord
# tell.  Appearing on the DOF tax-lien-sale notice list means unpaid property
# taxes or water charges.  Both are joined by BBL and add to sell-pressure.
LIEN_SINCE_YEAR = 2023          # only count reasonably-current lien notices
HAZARD_VIOL_FULL = 15           # this many open class-C violations -> full pts
OPEN_VIOL_FULL = 40             # this many total open violations -> full minor pts

# ACRIS document types
DOC_MORTGAGE = "MTGE"
DOC_DEED = ("DEED", "DEEDO")   # DEEDO = deed, other consideration
DOC_SATISFACTION = "SAT"
PARTY_TYPE_LENDER = "2"

# ---------------------------------------------------------------------------
# API client behaviour
# ---------------------------------------------------------------------------
BBL_BATCH_SIZE = 40      # exact-BBL OR clauses per Legals request
DOCID_BATCH_SIZE = 400   # document_id IN(...) values per Master/Parties request
REQUEST_PAUSE_SEC = 0.15  # polite pause between requests (no app token)
MAX_RETRIES = 5
