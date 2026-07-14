"""Thin SODA (Socrata) client for NYC Open Data.

Stdlib-only (urllib), matching the rest of this repo — no `requests` dependency.

Features that matter for a ~5k-parcel ACRIS pull with no app token:
  * on-disk JSON cache keyed by the exact query, so re-runs and crashes are free
  * automatic pagination past Socrata's 50k row cap
  * OR-clause and IN(...) batching helpers for the ACRIS joins
  * exponential backoff on 429 / 5xx throttling

Set NYC_APP_TOKEN in the environment (or repo .env) to lift rate limits; the
client works without one, just more slowly.
"""
import hashlib
import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from config import (
    SODA_BASE, REQUEST_PAUSE_SEC, MAX_RETRIES, DOCID_BATCH_SIZE,
)

CACHE_DIR = Path(__file__).resolve().parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

# Socrata app token lifts rate limits.  Accept either name; this repo's root
# .env already carries one as NYC_DATA_TOKEN.  Never committed.
_TOKEN_KEYS = ("NYC_APP_TOKEN", "NYC_DATA_TOKEN")
_APP_TOKEN = next((os.environ[k] for k in _TOKEN_KEYS if os.environ.get(k)), None)
if not _APP_TOKEN:
    for env_path in (Path(__file__).resolve().parent / ".env",
                     Path(__file__).resolve().parent.parent / ".env"):
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                for key in _TOKEN_KEYS:
                    if line.startswith(key + "="):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if val:
                            _APP_TOKEN = val
            if _APP_TOKEN:
                break


def _cache_key(dataset, params):
    blob = dataset + "?" + urllib.parse.urlencode(sorted(params.items()))
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def _request(dataset, params):
    """One raw SODA GET with retry/backoff.  Not cached (see soda_get)."""
    url = SODA_BASE.format(dataset) + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    if _APP_TOKEN:
        req.add_header("X-App-Token", _APP_TOKEN)
    last_err = None
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(min(2 ** attempt, 30) + random.uniform(0, 1))
                continue
            raise
        except OSError as e:
            # URLError, ConnectionResetError (WinError 10054), socket timeouts —
            # all transient under throttling; back off and retry.
            last_err = e
            time.sleep(min(2 ** attempt, 30) + random.uniform(0, 1))
    raise RuntimeError(f"SODA request failed after {MAX_RETRIES} tries: {last_err}")


def soda_get(dataset, where=None, select=None, group=None, order=None,
             limit=50000, offset=0, use_cache=True):
    """Single SODA query (up to 50k rows), cached to disk by exact params."""
    params = {"$limit": str(limit), "$offset": str(offset)}
    if where:
        params["$where"] = where
    if select:
        params["$select"] = select
    if group:
        params["$group"] = group
    if order:
        params["$order"] = order

    key = _cache_key(dataset, params)
    cache_file = CACHE_DIR / f"{dataset}_{key}.json"
    if use_cache and cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))

    time.sleep(REQUEST_PAUSE_SEC)
    rows = _request(dataset, params)
    if use_cache:
        cache_file.write_text(json.dumps(rows), encoding="utf-8")
    return rows


def soda_get_all(dataset, where=None, select=None, order=None,
                 page=50000, use_cache=True, log=print):
    """Paginate a query past the 50k row cap.  Needs a stable $order for
    correct paging; falls back to :id which every Socrata dataset has."""
    order = order or ":id"
    out, offset = [], 0
    while True:
        chunk = soda_get(dataset, where=where, select=select, order=order,
                         limit=page, offset=offset, use_cache=use_cache)
        out.extend(chunk)
        if len(chunk) < page:
            break
        offset += page
        log(f"    …paged {len(out)} rows from {dataset}")
    return out


def soda_count(dataset, where=None):
    rows = soda_get(dataset, where=where, select="count(*)", use_cache=False)
    return int(rows[0]["count"]) if rows else 0


def _soql_str(s):
    return "'" + str(s).replace("'", "''") + "'"


def batched_or(dataset, clauses, select=None, batch_size=40, extra_where=None,
               use_cache=True, log=print):
    """Run OR-clause groups in batches, concatenating rows.  `clauses` is a
    list of pre-built SoQL boolean expressions like
        (borough='2' AND block='2676' AND lot='49')
    """
    out = []
    total_batches = (len(clauses) + batch_size - 1) // batch_size
    for i in range(0, len(clauses), batch_size):
        group = " OR ".join(clauses[i:i + batch_size])
        where = f"({group})"
        if extra_where:
            where = f"{where} AND ({extra_where})"
        rows = soda_get(dataset, where=where, select=select, use_cache=use_cache)
        out.extend(rows)
        log(f"    batch {i // batch_size + 1}/{total_batches} "
            f"({dataset}) -> {len(rows)} rows, {len(out)} total")
    return out


def fetch_by_document_ids(dataset, document_ids, select=None, extra_where=None,
                          use_cache=True, log=print):
    """Fetch rows from an ACRIS table for a set of document_ids using
    `document_id IN (...)` batches."""
    ids = sorted(set(document_ids))
    out = []
    total_batches = (len(ids) + DOCID_BATCH_SIZE - 1) // DOCID_BATCH_SIZE
    for i in range(0, len(ids), DOCID_BATCH_SIZE):
        chunk = ids[i:i + DOCID_BATCH_SIZE]
        in_list = ",".join(_soql_str(d) for d in chunk)
        where = f"document_id in ({in_list})"
        if extra_where:
            where = f"{where} AND ({extra_where})"
        rows = soda_get(dataset, where=where, select=select, use_cache=use_cache)
        out.extend(rows)
        log(f"    docid batch {i // DOCID_BATCH_SIZE + 1}/{total_batches} "
            f"({dataset}) -> {len(rows)} rows, {len(out)} total")
    return out


def has_app_token():
    return bool(_APP_TOKEN)
