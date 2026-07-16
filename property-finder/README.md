# NYC Rent-Stabilized — Likely Sellers

Screens NYC public records for **20–50 unit, presumptively rent-stabilized
apartment buildings** whose owner is under the most **pressure to sell**: a
loan taken in the low-rate era (2011–2021) that is estimated to be **maturing
soon** into a building that has **lost value** since — so its implied
loan-to-value has blown out and a clean refinance is unlikely. This is the
post-HSTPA / Signature-Bank distress pattern: financed near peak, worth much
less now, loan coming due.

Candidates are ranked by a **sell-pressure score** built mostly on **estimated
current loan-to-value** (the thing that actually forces a sale), with loan
maturity as the trigger and owner tenure as a tie-breaker. Staten Island is
excluded by default; there's no cheap-$/door gate anymore (`$/door` is kept as
an informational column).

Output is a spreadsheet (`output/candidates.csv`) and a **self-contained,
offline HTML target board** (`output/targets.html`) where you can sort,
filter, and track each building's status + notes (saved in your browser).

> ⚠️ **Read the "Hard facts vs. proxies" section before making an offer.**
> Several of the most important columns — interest rate, loan term, maturity
> date, and even rent-stabilization status — are **not published by NYC** and
> are *estimated*. This tool tells you *where to look*; it does not replace
> pulling the actual mortgage document.

---

## Quick start

```bash
cd property-finder
python pipeline.py                       # all 5 boroughs (slow w/o app token)
python pipeline.py --boroughs BX BK      # just the Bronx + Brooklyn
python pipeline.py --boroughs BX --limit 200   # fast sample
```

Then open `output/targets.html` in any browser (double-click it — no server
needed). Or open `output/candidates.csv` in Excel/Sheets.

- **No dependencies** beyond Python 3.8+ standard library.
- **App token (optional but recommended for the full run):** put
  `NYC_APP_TOKEN=your_token` in a `.env` file (this repo's root `.env` is read
  automatically). Get one free at
  <https://data.cityofnewyork.us/profile/app_tokens>. Without it the pull still
  works, just slower (the client auto-throttles and retries).
- All API responses are **cached** under `cache/`, so re-runs are instant and a
  crash mid-pull resumes for free. Delete `cache/` or pass `--no-cache` to
  force fresh data.

---

## How a candidate is found (the pipeline)

1. **PLUTO screen** (`64uk-42ks`) — pull parcels that are 20–50 residential
   units, built before 1974, building class C/D (walk-up / elevator apartment),
   excluding co-op classes, in Manhattan/Bronx/Brooklyn/Queens. (Optional
   `--max-door` re-imposes a cheapness cap; off by default.)
2. **ACRIS Legals** (`8h5j-fqxa`) — for each parcel (matched by
   borough+block+lot), get every recorded document id.
3. **ACRIS Master** (`bnx9-e6tj`) — pull those documents' type / date / amount,
   keeping deeds, mortgages, and satisfactions from 2011 on.
4. **Distress signals** — open **HPD violations** (`wvxf-dwi5`, especially
   class C "immediately hazardous") and appearance on the **DOF tax-lien-sale
   notice list** (`9rz4-mjek`, 2023+), both joined by BBL. These flag a
   stretched/tired landlord independent of the mortgage.
5. **Value, leverage, score** — estimate today's value (calibrated, below),
   size the senior low-rate loan, compute **implied current LTV** and the value
   change since financing, estimate maturity, fold in distress, and score
   sell-pressure.
6. **ACRIS Parties** (`636b-3b5g`) — look up the lender name for each
   building's low-rate mortgage.

### Valuation is calibrated to real sales
NYC's `assesstot ÷ 0.45` badly understates rent-stabilized value (DOF values
these off *regulated* income). Measured against **1,302 arm's-length ACRIS
sales in our candidate pool**, actual price ≈ **4.7× total assessed value**, so
we estimate current value as `assesstot × (per-borough multiplier)`
(Bronx 4.71, Brooklyn 4.54, Manhattan 4.74, Queens 4.75). Re-derive after a
fresh pull with `python tools/calibrate.py`. A recent real sale, when one
exists, is always preferred over the assessment estimate.

---

## Hard facts vs. proxies vs. estimates

| Column | Source | Confidence | What to double-check |
|---|---|---|---|
| Units (`unitsres`) | PLUTO | **Hard fact** | PLUTO can lag renovations/conversions. |
| Year built | PLUTO | **Hard fact** (mostly) | Some `yearbuilt` are estimates; `0` = unknown (excluded). |
| Building class | PLUTO | **Hard fact** | Class is DOF's call; a C-class can still be a de-facto condo. |
| Owner name | PLUTO | **Hard fact** | Usually an LLC; the real principal isn't here. |
| BBL / address | PLUTO | **Hard fact** | — |
| Mortgage **recording date** | ACRIS | **Hard fact** | This is real. It is *not* the same as the loan's start/maturity. |
| Mortgage **recorded amount** | ACRIS | **Hard fact, but** | On a CEMA/consolidation the recorded amount is only the *new money* and understates the true loan. |
| Sale price | ACRIS deed | **Hard fact, but** | A single deed can cover a **portfolio** — the price then isn't this building. Flagged as "bulk-sale price ignored." |
| Lender | ACRIS parties | **Hard fact** | The named lender at recording; loan may have been sold/assigned since. |
| Open HPD violations / class C | HPD | **Hard fact** | Count of *open* violations; class C = "immediately hazardous." Reflects reported conditions, which can lag reality either way. |
| On tax-lien-sale list | DOF | **Hard fact** | On a 2023+ notice list for unpaid property tax or water (`water_only` distinguishes). The debt may since be cured. |
| **Rent-stabilized?** | *Proxy* | **Assumption** | Built <1974 + 6+ units + not condo/co-op. **Not a legal determination.** Verify via the building's DHCR registration / actual rent roll. Post-1974 421-a/J-51 stabilization and buildings that fully deregulated are *not* captured. |
| **Current value / $/door** | *Estimate* | **Estimate** | Recent real sale if available, else `assesstot × per-borough multiplier` (calibrated to real sales, ~3.9×). A class-wide factor; any single building can be off. |
| Est. **origination value** | *Estimate* | **Estimate** | Purchase price if the loan was an acquisition, else `loan ÷ 0.70` (assumed LTV). |
| **Implied current LTV** | *Estimate* | **Core signal, estimate** | `senior low-rate loan ÷ current value`. Suppressed for blanket/portfolio loans and implausible values. CEMA loans understate the balance, so real LTV can be *higher* than shown. |
| **Value change since financing** | *Estimate* | **Estimate** | `current value ÷ origination value − 1`. |
| **"Low-rate era"** | *Proxy* | **Assumption** | Defined as recorded 2011–2021. The *actual rate* is in the scanned doc, not in any field. |
| **Estimated maturity** | *Estimate* | **Assumption** | recording date + assumed term (10/7/5 yr; 10yr weighted first). The *actual maturity* is in the scanned doc. A building can be interest-only, have extension options, or already have refinanced. |
| **"Maturing soon"** | *Estimate* | **Assumption** | True if any assumed term matures within ~18 months back to ~36 months ahead of today. |
| **Sell-pressure score / flags** | *Derived* | **Heuristic** | A ranking aid, not a valuation. See weights below. |

### Why the two big proxies exist
NYC does **not** publish, in any queryable field:
- the **interest rate**, **term**, or **maturity date** of a recorded mortgage
  (that text lives only in the scanned document image), or
- a live **rent-stabilization** status per building.

So this tool uses the same proxies the research community (NYU Furman Center,
ANHD, JustFix) uses for stabilization, and infers loan timing from the
recording date + the loan sizes typical for 20–50 unit buildings (the 10-year
Fannie/Freddie Small Balance Loan is the most common, hence weighted first).

---

## The sell-pressure score (0–100)

Transparent and tunable in [`analysis.py`](analysis.py) / [`config.py`](config.py):

- **up to 45 pts — leverage / value loss** — implied current LTV (the thing
  that actually forces a sale): ramps in from ~55% LTV, maxes past ~105%.
  Where LTV can't be trusted (blanket loan) but a clean purchase price shows a
  big value decline, a smaller value-loss credit (up to 27) applies instead.
- **up to 30 pts — estimated maturity near/at today** (peak ≈ 0–12 months out;
  recently-matured loans score high — they're the most distressed).
- **up to 10 pts — loan still outstanding + long owner tenure.**
- **up to 15 pts — operational/financial distress** — open HPD class-C hazards
  (up to 10), total open violations (up to 2), and on the tax-lien list (5, or
  3 if water-only).
- **× 0.35 dampener** if the building was **recently purchased** (an owner who
  just bought is very unlikely to sell).

Flags surface the caveats per row: `underwater: est. LTV ≥ 100%`, `refi hard:
est. LTV ≥ 90%`, `value down ~X% since financing (est.)`, `blanket/portfolio
loan — per-building leverage N/A`, `loan may be understated (CEMA)`, `maturing
soon (est.)`, `est. already matured`, `recently acquired (unlikely seller)`,
`financed/sold since (may be refinanced)`.

---

## Verify before you act (per building)

Every row links to the primary sources so you can confirm the estimates:

- **"mtge doc"** → the ACRIS document detail for the low-rate mortgage. Open
  the scanned image to read the **actual rate, term, and maturity date**.
- **"all docs"** → the full ACRIS document list for the parcel (check for
  later refinances, assignments, or satisfactions this tool may have missed).
- **"PLUTO"** → the parcel on ZoLa (zoning, units, ownership).

Also worth a manual check before an offer: DHCR rent-stabilization
registration, actual rent roll, open HPD/DOB violations, tax/water arrears, and
any 421-a/J-51 status.

---

## Files

| File | Role |
|---|---|
| `config.py` | All tunable constants (filters, rate era, loan terms, weights). |
| `nyc_api.py` | Stdlib SODA client: caching, pagination, batching, backoff. |
| `analysis.py` | Valuation, maturity estimate, scoring — the transparent math. |
| `distress.py` | HPD-violation and tax-lien joins (the distress signals). |
| `tools/calibrate.py` | Re-derives the per-borough value multipliers from real sales. |
| `pipeline.py` | Orchestrates PLUTO → ACRIS → distress → score → CSV/HTML. CLI entry point. |
| `build_html.py` | Renders the self-contained target board. |
| `output/candidates.csv` | Full scored spreadsheet. |
| `output/targets.html` | Offline sortable/filterable target board (your notes live here). |

## Known limitations / things to improve

- **Rate and maturity are assumptions** — the whole "maturing soon" signal is
  recording-date + assumed term. Always open the mortgage doc.
- **Blanket / portfolio loans** (one mortgage over many buildings) can't be
  pinned to a single parcel. We suppress the per-building LTV when a loan spans
  multiple candidate parcels, exceeds a plausible per-unit amount, or implies an
  LTV > 1.5. Loans that blanket a candidate *plus non-candidate* buildings can
  still slip through and overstate leverage — sanity-check the loan amount.
- **CEMA / consolidated loans** understate the true balance (recorded amount =
  new money only), so real LTV can be *higher* than shown; flagged when a
  purchase price implies it.
- **Value is calibrated but still an estimate** — the per-borough multiplier is
  a median; individual buildings vary, and current assessments lag the market.
- **Bulk deeds** (portfolio sales) are detected heuristically (price > 2.5×
  assessed-implied value → ignored); edge cases can be mis-handled.
- Parcels with **sparse ACRIS coverage** get a "no low-rate-era mortgage found"
  flag; that may reflect missing data, not a free-and-clear building.
- **Distress signals are point-in-time**: an open violation may be in progress
  of repair, and a lien-list debt may since be paid. Treat them as leads, not
  proof. HPD counts reflect *reported* conditions.
