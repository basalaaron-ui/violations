"""
NYC Property Violations Scanner — Interactive Launcher
"""
import os
import sys
import json
import subprocess
from datetime import datetime

HISTORY_FILES = [
    "hpd_scanned.txt",
    "scanned_properties.txt",
    "hpd_checkpoint.csv",
    "violations_checkpoint.csv",
    "hpd_street_variants.json",
]

PROGRESS_FILE = "scan_progress.json"
W = 60


def clear():
    os.system("cls" if os.name == "nt" else "clear")


def rule():
    print("━" * W)


def header():
    rule()
    print(f"  NYC Property Violations Scanner")
    print(f"  {datetime.now().strftime('%A, %B %d, %Y')}")
    rule()
    print()


def load_progress() -> dict:
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def pick_days() -> int:
    options = {"1": 30, "2": 90, "3": 180, "4": 365}
    print("  Date range to include in report:\n")
    for k, v in options.items():
        marker = "  ← default" if v == 180 else ""
        print(f"    [{k}]  Last {v} days{marker}")
    print(f"    [5]  Custom")
    print()
    choice = input("  Choose [1-5]:  ").strip()
    if choice in options:
        return options[choice]
    if choice == "5":
        try:
            days = int(input("  Enter number of days:  ").strip())
            if days > 0:
                return days
        except ValueError:
            pass
        print("  Invalid — using 180 days.")
        return 180
    return 180


def pick_range(scanner_key: str):
    """
    Ask which properties to scan.
    Returns (start, limit): start = skip first N rows of CSV, limit = max to scan after that.
    """
    progress = load_progress()

    if scanner_key == "both":
        hpd  = progress.get("hpd", 0)
        city = progress.get("citypay", 0)
        # use whichever is further along (most conservative resume)
        saved = min(p for p in [hpd, city] if p) if (hpd and city) else hpd or city
    else:
        saved = progress.get(scanner_key, 0)

    fixed = {"2": 10, "3": 20, "4": 50, "5": 100}

    print("  Scan range:\n")
    print(f"    [1]  All properties, from the beginning  ← default")
    for k, v in fixed.items():
        print(f"    [{k}]  First {v} properties")
    if saved:
        print(f"    [6]  Resume from #{saved + 1}  (where last scan ended)")
    print(f"    [7]  Custom  (set your own start + limit)")
    print()

    choice = input("  Choose:  ").strip()

    if choice == "1":
        return 0, None
    if choice in fixed:
        return 0, fixed[choice]
    if choice == "6" and saved:
        return saved, None
    if choice == "7":
        try:
            s = input("  Start from property # (Enter = 1):  ").strip()
            start = max(0, int(s) - 1) if s else 0
        except ValueError:
            start = 0
        try:
            l = input("  Scan how many? (Enter = all remaining):  ").strip()
            limit = int(l) if l else None
        except ValueError:
            limit = None
        return start, limit

    return 0, None  # default


def clear_history():
    print()
    found = [f for f in HISTORY_FILES if os.path.exists(f)]
    if not found:
        print("  Nothing to clear.")
        print()
        print("  Checkpoint files are auto-deleted after each completed scan —")
        print("  they only linger if a scan crashed mid-way.")
        print()
        print("  To force a full re-report of all known violations, delete")
        print("  hpd_found.csv and/or violations_found.csv (your master logs).")
        input("\n  Press Enter to go back...")
        return
    print("  Found the following files to clear:\n")
    for f in found:
        print(f"    • {f}")
    print()
    confirm = input("  Confirm? [y/N]:  ").strip().lower()
    if confirm == "y":
        for f in found:
            os.remove(f)
        print(f"\n  ✓ Cleared {len(found)} file(s). Next scan will start fresh.")
    else:
        print("\n  Cancelled.")
    input("\n  Press Enter to go back...")


def build_cmd(script: str, days: int, start: int = 0, limit: int = None) -> list:
    cmd = [sys.executable, script, "--days", str(days)]
    if start:
        cmd += ["--start", str(start)]
    if limit is not None:
        cmd += ["--limit", str(limit)]
    return cmd


def run_scanner(script: str, days: int, start: int = 0, limit: int = None):
    print()
    rule()
    result = subprocess.run(build_cmd(script, days, start, limit))
    rule()
    if result.returncode != 0:
        print(f"\n  ⚠  Scanner exited with an error (code {result.returncode}).")
    input("\n  Press Enter to return to menu...")


def run_both(days: int, start: int = 0, limit: int = None):
    print()
    rule()
    print(f"  [1/2] HPD Scanner")
    rule()
    subprocess.run(build_cmd("hpd_scanner_optimized.py", days, start, limit))
    print()
    rule()
    print(f"  [2/2] CityPay Scanner")
    rule()
    subprocess.run(build_cmd("live_scanner.py", days, start, limit))
    rule()
    input("\n  Press Enter to return to menu...")


def main():
    while True:
        clear()
        header()
        print("  [1]  Run HPD Scanner       (violations & complaints)")
        print("  [2]  Run CityPay Scanner   (ECB fines)")
        print("  [3]  Run Both Scanners")
        print()
        print("  [4]  Clear scan history    (restart from scratch)")
        print("  [5]  Exit")
        print()
        choice = input("  Choose [1-5]:  ").strip()

        if choice == "1":
            clear(); header()
            days = pick_days()
            clear(); header()
            start, limit = pick_range("hpd")
            clear(); header()
            run_scanner("hpd_scanner_optimized.py", days, start, limit)

        elif choice == "2":
            clear(); header()
            days = pick_days()
            clear(); header()
            start, limit = pick_range("citypay")
            clear(); header()
            run_scanner("live_scanner.py", days, start, limit)

        elif choice == "3":
            clear(); header()
            days = pick_days()
            clear(); header()
            start, limit = pick_range("both")
            clear(); header()
            run_both(days, start, limit)

        elif choice == "4":
            clear(); header()
            clear_history()

        elif choice == "5":
            print("\n  Goodbye.\n")
            break


if __name__ == "__main__":
    main()
