"""Re-derive the per-borough assessment->market multipliers from real sales.

Run after a fresh pipeline run:  python tools/calibrate.py
Prints a BOROUGH_MARKET_MULTIPLIER dict to paste into config.py.  The
multiplier is median(actual arm's-length sale price / total assessed value)
across candidates that have a recent recorded sale.
"""
import csv
import statistics as st
from collections import defaultdict
from pathlib import Path

CSV = Path(__file__).resolve().parent.parent / "output" / "candidates.csv"


def main():
    rows = list(csv.DictReader(open(CSV, encoding="utf-8")))
    by_boro = defaultdict(list)
    for r in rows:
        if not r["value_basis"].startswith("recorded sale"):
            continue
        try:
            sale = float(r["last_sale_price"])
            assesstot = float(r["assesstot"])
        except (ValueError, KeyError):
            continue
        if sale > 0 and assesstot > 0:
            by_boro[r["borough"]].append(sale / assesstot)

    allr = [x for v in by_boro.values() for x in v]
    citywide = st.median(allr) if allr else None
    print(f"# calibrated from {len(allr)} arm's-length sales")
    print("BOROUGH_MARKET_MULTIPLIER = {")
    for b in ["Manhattan", "Bronx", "Brooklyn", "Queens", "Staten Island"]:
        v = by_boro.get(b, [])
        if len(v) >= 10:
            print(f'    "{b}": {st.median(v):.2f},   # n={len(v)}')
        elif citywide:
            print(f'    "{b}": {citywide:.2f},   # too few sales (n={len(v)}); citywide')
    print("}")
    if citywide:
        print(f"DEFAULT_MARKET_MULTIPLIER = {citywide:.2f}")


if __name__ == "__main__":
    main()
