"""Local server for the PropFolio webapp.

Run from the project root:  python webapp/server.py
Then open http://localhost:8642

- Serves the webapp folder as static files
- GET  /api/data       -> full dataset (read fresh from the CSVs)
- GET  /api/version    -> cheap change token the page polls for live updates
- POST /api/properties -> append new properties to properties.csv
- POST /api/refresh    -> pull current violations from NYC Open Data now

Every AUTO_REFRESH_MINUTES (default 20) the server pulls the portfolio's
OATH/ECB tickets from NYC Open Data via api_fetcher.py: new tickets are
appended to violations_found.csv and stale statuses/balances corrected,
and every open page updates itself within seconds. External changes to
the CSVs (e.g. the Playwright scanner) are picked up the same way. It
also keeps webapp/data.js snapshotted so the page still works when
opened directly as a file.
"""
import json
import os
import sys
import threading
import time
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dataio

sys.path.insert(0, str(dataio.ROOT))
import api_fetcher
import hpd_api_fetcher

PORT = 8642
AUTO_REFRESH_MINUTES = float(os.environ.get("AUTO_REFRESH_MINUTES", "20"))
REFRESH_DAYS = int(os.environ.get("REFRESH_DAYS", "180"))

_refresh_lock = threading.Lock()


def run_refresh():
    """Pull from NYC Open Data; returns the fetch summary (or an error)."""
    if not _refresh_lock.acquire(blocking=False):
        return {"error": "a sync is already running"}
    try:
        quiet = lambda *a, **k: None
        ecb = api_fetcher.run_fetch(days=REFRESH_DAYS, log=quiet)
        hpd = hpd_api_fetcher.run_fetch(days=REFRESH_DAYS, log=quiet)
        try:
            dataio.write_datajs()
        except OSError:
            pass
        summary = {
            "when": datetime.now().isoformat(timespec="seconds"),
            "changed": ecb["changed"] or hpd["changed"],
            "updated": ecb["updated"] + hpd["updated"],
            "added": ecb["added"] + hpd["added"],
            "tickets_fetched": ecb["tickets_fetched"] + hpd["tickets_fetched"],
            "ecb": ecb,
            "hpd": hpd,
        }
        stamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{stamp}] NYC Open Data sync — ECB: {ecb['updated']} updated, {ecb['added']} new"
              f" · HPD: {hpd['updated']} updated, {hpd['added']} new", flush=True)
        return summary
    except Exception as e:
        print(f"NYC Open Data sync failed: {e}", flush=True)
        return {"error": str(e)}
    finally:
        _refresh_lock.release()


def auto_refresh_loop():
    time.sleep(20)  # let the server settle before the first pull
    while True:
        run_refresh()
        time.sleep(AUTO_REFRESH_MINUTES * 60)


def payload():
    return {
        "version": dataio.data_version(),
        "properties": dataio.load_properties(),
        "violations": dataio.load_violations(),
        "hpd": dataio.load_hpd(),
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(dataio.WEB), **kwargs)

    def log_message(self, fmt, *args):
        # Keep the console quiet except for API errors
        if "/api/" not in (args[0] if args else ""):
            return
        super().log_message(fmt, *args)

    def send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/version":
            return self.send_json({"version": dataio.data_version()})
        if self.path == "/api/data":
            try:
                dataio.write_datajs()  # keep the offline snapshot fresh
            except OSError:
                pass
            return self.send_json(payload())
        super().do_GET()

    def do_POST(self):
        if self.path == "/api/refresh":
            result = run_refresh()
            return self.send_json(result, 200 if "error" not in result else 500)
        if self.path != "/api/properties":
            return self.send_json({"error": "not found"}, 404)
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            rows = body.get("properties", [])
            if not isinstance(rows, list) or not rows:
                return self.send_json({"error": "no properties given"}, 400)
            if len(rows) > 5000:
                return self.send_json({"error": "too many rows"}, 400)
            added, skipped = dataio.append_properties(rows)
            try:
                dataio.write_datajs()
            except OSError:
                pass
            return self.send_json({
                "added": added,
                "skipped": [s["reason"] for s in skipped],
                "total": len(dataio.load_properties()),
            })
        except (json.JSONDecodeError, ValueError, OSError) as e:
            return self.send_json({"error": str(e)}, 400)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"PropFolio running at http://localhost:{PORT}  (Ctrl+C to stop)")
    print(f"Watching: {dataio.PROPS_CSV.name}, {dataio.VIO_CSV.name}")
    if AUTO_REFRESH_MINUTES > 0:
        threading.Thread(target=auto_refresh_loop, daemon=True).start()
        print(f"Auto-sync with NYC Open Data every {AUTO_REFRESH_MINUTES:g} min "
              f"(set AUTO_REFRESH_MINUTES=0 to disable)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
