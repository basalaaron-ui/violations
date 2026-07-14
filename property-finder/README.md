# NYC Maturing-Mortgage Property Finder

Screens NYC public records for **20–50 unit, presumptively rent-stabilized
apartment buildings** that were financed during the low-rate era and whose
mortgage is estimated to be **maturing soon** — i.e. owners who will likely
face a refinance-or-sell decision at a much higher rate.

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
   excluding co-op classes, and cheap enough on assessed value to *possibly*
   clear the $/door cap. This first-pass value gate is what makes the ACRIS
   join tractable.
2. **ACRIS Legals** (`8h5j-fqxa`) — for each parcel (matched by
   borough+block+lot), get every recorded document id.
3. **ACRIS Master** (`bnx9-e6tj`) — pull those documents' type / date / amount,
   keeping deeds, mortgages, and satisfactions from 2011 on.
4. **Value, date, score** — estimate market value & $/door, pick the operative
   low-rate-era mortgage, estimate its maturity, and score refinance pressure.
5. **ACRIS Parties** (`636b-3b5g`) — look up the lender name for each
   building's low-rate mortgage.

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
| **Rent-stabilized?** | *Proxy* | **Assumption** | Built <1974 + 6+ units + not condo/co-op. **Not a legal determination.** Verify via the building's DHCR registration / actual rent roll. Post-1974 421-a/J-51 stabilization and buildings that fully deregulated are *not* captured. |
| **Market value / $/door** | *Estimate* | **Estimate** | If no recent arm's-length sale, value = assessed value ÷ 0.45. The 0.45 ratio is a class-wide average; any single building can be off by a lot. |
| **"Low-rate era"** | *Proxy* | **Assumption** | Defined as recorded 2011–2021. The *actual rate* is in the scanned doc, not in any field. |
| **Estimated maturity** | *Estimate* | **Assumption** | recording date + assumed term (10/7/5 yr; 10yr weighted first). The *actual maturity* is in the scanned doc. A building can be interest-only, have extension options, or already have refinanced. |
| **"Maturing soon"** | *Estimate* | **Assumption** | True if any assumed term matures within ~18 months back to ~36 months ahead of today. |
| **Score / flags** | *Derived* | **Heuristic** | A ranking aid, not a valuation. See weights below. |

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

## The score (0–100)

Transparent and tunable in [`analysis.py`](analysis.py) / [`config.py`](config.py):

- **up to 45 pts — cheaper $/door** (the core value screen)
- **up to 40 pts — estimated maturity near/at today** (peak ≈ 0–12 months out;
  recently-matured loans score high too — they're the most distressed)
- **up to 15 pts — low-rate loan still apparently outstanding** (no newer
  mortgage/deed and no satisfaction recorded since)

Flags surface the caveats per row: `maturing soon (est.)`, `est. already
matured`, `financed/sold since (may be refinanced)`, `satisfaction recorded
since`, `bulk-sale price ignored`, `no low-rate-era mortgage found`.

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
| `pipeline.py` | Orchestrates PLUTO → ACRIS → score → CSV/HTML. CLI entry point. |
| `build_html.py` | Renders the self-contained target board. |
| `output/candidates.csv` | Full scored spreadsheet. |
| `output/targets.html` | Offline sortable/filterable target board (your notes live here). |

## Known limitations / things to improve

- **CEMA / consolidated loans** understate the true mortgage balance (recorded
  amount = new money only).
- **Bulk deeds** are detected heuristically (sale > 2.5× assessed-implied
  value → ignored); a genuine high sale could be misflagged, and a bulk deed
  under that ratio could slip through.
- The **assessed-value gate** (first pass) can miss a building whose eventual
  sale-based value would land under $70k/door but whose assessment is high; a
  1.15× cushion softens this but doesn't eliminate it.
- **Rate and maturity are assumptions** — always open the mortgage doc.
- Older parcels with **sparse ACRIS coverage** (common on Staten Island) get a
  "no low-rate-era mortgage found" flag; that may reflect missing data, not a
  free-and-clear building.
