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
# NYC assesses Class 2 rentals at ~45% of market value.  This is the fallback
# when no recent recorded sale exists.
ASSESS_RATIO = 0.45
# Prefer a recorded ACRIS sale price if one exists within this many years.
SALE_LOOKBACK_YEARS = 8
# Ignore obviously non-arm's-length "sales" below this price (e.g. $0 / $10
# transfers between related LLCs, estate transfers).
MIN_REAL_SALE_PRICE = 100_000

# Target economics
MAX_PER_DOOR = 70_000
# First-pass gate is run in the PLUTO query on assessed value alone (before we
# know the sale price).  Cushion widens it so a building whose eventual
# sale-based value lands just under the cap isn't dropped prematurely.
DOOR_GATE_CUSHION = 1.15

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
# A loan is "maturing soon" (refi/sale pressure) if its estimated maturity
# falls between MONTHS_BACK ago and MONTHS_AHEAD from today.  Already-matured
# loans (negative months) are *more* distressed, so we look back a bit too.
MATURITY_WINDOW_MONTHS_BACK = 18
MATURITY_WINDOW_MONTHS_AHEAD = 36

TODAY = date.today()

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
