"""
Package the violations scanner into a zip file ready to
send to another computer (e.g. a MacBook).

Run from the violations folder:
    python package.py
"""
import os
import zipfile
from datetime import datetime

# Files that are always included
REQUIRED = [
    "hpd_scanner_optimized.py",
    "live_scanner.py",
    "run.py",
    "run.sh",
    "run.bat",
    "requirements.txt",
    "properties.csv",
    ".env",
]

# Optional — included only if they exist
OPTIONAL = [
    "hpd_found.csv",           # master HPD violation log
    "violations_found.csv",    # master CityPay violation log
    "hpd_street_variants.json",# street name cache (speeds up HPD scans)
    "scan_progress.json",      # resume positions
]

# Never included (logs, mid-run checkpoints)
EXCLUDE = {
    "hpd_scanner_fast.log",
    "citypay_scanner_fast.log",
    "hpd_checkpoint.csv",
    "violations_checkpoint.csv",
    "hpd_scanned.txt",
    "scanned_properties.txt",
    "package.py",
}

W = 58

def rule():
    print("━" * W)

def main():
    rule()
    print("  Violations Scanner — Packager")
    rule()
    print()

    zip_name = f"violations_scanner_{datetime.now().strftime('%Y%m%d')}.zip"
    added, skipped, missing = [], [], []

    with zipfile.ZipFile(zip_name, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in REQUIRED:
            if os.path.exists(f):
                zf.write(f)
                added.append(f)
            else:
                missing.append(f)

        for f in OPTIONAL:
            if os.path.exists(f):
                zf.write(f)
                added.append(f)
            else:
                skipped.append(f)

    print("  Included:\n")
    for f in added:
        size = os.path.getsize(f)
        size_str = f"{size/1024:.1f} KB" if size >= 1024 else f"{size} B"
        print(f"    ✓  {f:<40} {size_str}")

    if missing:
        print(f"\n  ⚠  Missing (required but not found):\n")
        for f in missing:
            print(f"    ✗  {f}")

    if skipped:
        print(f"\n  —  Not found (optional, skipped):\n")
        for f in skipped:
            print(f"    —  {f}")

    zip_size = os.path.getsize(zip_name) / 1024
    print(f"\n  Output: {zip_name}  ({zip_size:.1f} KB)")
    rule()

    if missing:
        print()
        print("  ⚠  Some required files were missing.")
        print("     The zip was still created but may not work on the other machine.")
    else:
        print()
        print("  To run on a Mac:")
        print()
        print("    1. Copy the zip to the Mac and unzip it")
        print("    2. Open Terminal and run:")
        print()
        print("         bash run.sh")
        print()
        print("    That's it — run.sh installs everything automatically.")
    print()

if __name__ == "__main__":
    main()
