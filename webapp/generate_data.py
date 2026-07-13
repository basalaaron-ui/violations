"""Regenerate webapp/data.js from properties.csv and violations_found.csv.

Run from the project root:  python webapp/generate_data.py
(The server does this automatically; this is for manual refreshes.)
"""
import dataio

dataio.write_datajs()
print(f"Wrote {dataio.DATA_JS} - "
      f"{len(dataio.load_properties())} properties, "
      f"{len(dataio.load_violations())} violations")
