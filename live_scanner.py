"""
NYC CityPay ECB Violations Scanner — OPTIMIZED
Uses concurrent browser pages instead of sequential
5-10x speedup from parallelization
Expected runtime: 5-15 minutes for 200+ properties (vs 60+ minutes)
"""

import asyncio
import argparse
import threading
import json
import os
import sys
import smtplib
import urllib.parse
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from playwright.async_api import async_playwright
from typing import List, Dict, Tuple
import logging

load_dotenv()

# --- SETTINGS ---
PROPERTIES_FILE = "properties.csv"
EMAIL_FROM = os.getenv("EMAIL_FROM")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_TO = os.getenv("EMAIL_TO")
PORTAL_BASE = "https://a836-citypay.nyc.gov/citypay/ecb"

CONCURRENT_PAGES = 5  # Number of simultaneous browser pages
SLOW_MO = 0
DAYS_BACK = 180

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

_fh = logging.FileHandler("citypay_scanner_fast.log")
_fh.setLevel(logging.DEBUG)
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(_fh)

# Console only shows warnings/errors — clean progress via print()
_ch = logging.StreamHandler(sys.stdout)
_ch.setLevel(logging.WARNING)
_ch.setFormatter(logging.Formatter("  ⚠  %(message)s"))
logger.addHandler(_ch)


def build_portal_url(house_num, street_name, borough):
    """Build direct portal URL for property"""
    params = urllib.parse.urlencode({
        "house_number": house_num,
        "street_name": street_name,
        "borough": borough
    })
    return f"{PORTAL_BASE}?{params}"


async def scrape_property(page, house_num, street_name, borough):
    """Scrape violations for a single property"""
    prop_key = f"{house_num} {street_name}"
    
    try:
        logger.debug(f"Scanning: {prop_key}")
        
        await page.goto(PORTAL_BASE, wait_until="load", timeout=20000)

        # Click "By Name and Address" tab
        try:
            await page.wait_for_selector("a:has-text('By Name and Address'), li:has-text('By Name and Address')", timeout=5000)
            await page.click("a:has-text('By Name and Address'), li:has-text('By Name and Address')")
        except:
            try:
                await page.click("text=By Name and Address")
            except:
                logger.warning(f"{prop_key} — Could not find tab")
                return []

        await page.wait_for_load_state("networkidle", timeout=5000)

        # Fill house number
        for _ in range(3):
            await page.keyboard.press("Tab")
        await page.keyboard.type(str(house_num))

        # Fill street name
        await page.keyboard.press("Tab")
        await page.keyboard.type(street_name)

        # Select first autocomplete suggestion
        try:
            first_suggestion = page.locator("ul.ui-autocomplete li:first-child, [role='option']:first-child, .autocomplete-suggestion:first-child").first
            await first_suggestion.wait_for(timeout=3000)
            await first_suggestion.click()
        except:
            try:
                await page.keyboard.press("ArrowDown")
                await page.keyboard.press("Enter")
            except:
                logger.debug(f"{prop_key} — Autocomplete failed")

        # Click Search
        try:
            await page.get_by_role("button", name="Search").click()
        except:
            await page.click("button:has-text('Search')")

        # Wait for results table (up to 15s) instead of fixed 6s sleep
        try:
            await page.wait_for_selector("table, .dataTables_wrapper, .dataTables_empty", timeout=15000)
        except:
            pass

        # Try to expand to show all entries
        try:
            await page.select_option("select[name*='length'], select[name*='entries'], .dataTables_length select", "-1")
            await page.wait_for_load_state("networkidle", timeout=5000)
            logger.info(f"   📋 Expanded to show all entries")
        except:
            try:
                selects = await page.query_selector_all("select")
                for sel in selects:
                    options = await sel.query_selector_all("option")
                    for opt in options:
                        val = await opt.get_attribute("value")
                        txt = await opt.inner_text()
                        if val == "-1" or "all" in txt.lower():
                            await sel.select_option(val or txt)
                            await page.wait_for_load_state("networkidle", timeout=5000)
                            logger.info(f"   📋 Expanded (fallback)")
                            break
            except:
                logger.debug(f"{prop_key} — Could not expand entries")

        # Scroll to bottom
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")

        # Extract table data
        table = await page.query_selector("table")
        if not table:
            logger.info(f"   ✅ No violations found")
            return []

        rows = await table.query_selector_all("tr")
        results = []
        cutoff = datetime.now() - timedelta(days=DAYS_BACK)
        portal_url = build_portal_url(house_num, street_name, borough)

        for row in rows[1:]:  # Skip header row
            cols = await row.query_selector_all("td")
            
            if len(cols) < 6:
                continue
            
            col_texts = [await c.inner_text() for c in cols]

            # Parse date (column 4)
            raw_date = col_texts[4].strip()
            try:
                parsed_date = datetime.strptime(raw_date[:10], '%Y-%m-%d')
                clean_date = parsed_date.strftime('%m/%d/%Y')
            except:
                parsed_date = None
                clean_date = raw_date[:10]

            # Skip if older than 180 days
            if parsed_date is None or parsed_date < cutoff:
                continue

            results.append({
                "Property": f"{house_num} {street_name}",
                "Portal URL": portal_url,
                "Ticket #": col_texts[1].strip(),
                "Respondent": col_texts[2].strip(),
                "Date": clean_date,
                "Description": col_texts[5].strip(),
                "Agency": col_texts[6].strip() if len(cols) > 6 else "N/A",
                "Status": col_texts[7].strip() if len(cols) > 7 else "N/A",
                "Amount Due": col_texts[8].strip() if len(cols) > 8 else "N/A"
            })

        logger.debug(f"{prop_key} — {len(results)} result(s)")
        return results

    except Exception as e:
        logger.error(f"{prop_key} — {e}")
        return []


def _start_stop_listener(stop_event: asyncio.Event, loop: asyncio.AbstractEventLoop):
    """Background thread: sets stop_event when user presses Enter."""
    def _listen():
        try:
            input()
            print("\n  ⏹  Stopping after current batch — will still send email...\n")
            loop.call_soon_threadsafe(stop_event.set)
        except Exception:
            pass
    threading.Thread(target=_listen, daemon=True).start()


async def scan_properties_concurrent(
    browser,
    properties_queue: List[Tuple[int, str, str, str]],
    stop_event: asyncio.Event = None,
) -> List[Dict]:
    """Scan properties concurrently using multiple pages"""
    
    results_all = []
    total = len(properties_queue)
    n_batches = (total + CONCURRENT_PAGES - 1) // CONCURRENT_PAGES
    found_count = 0
    processed_count = 0

    print(f"  Scanning {total} properties in {n_batches} batches...")
    print(f"  Press Enter at any time to stop early and still get an email.\n")

    for batch_start in range(0, len(properties_queue), CONCURRENT_PAGES):
        if stop_event and stop_event.is_set():
            print("  ⏹  Scan stopped early.\n")
            break

        batch = properties_queue[batch_start : batch_start + CONCURRENT_PAGES]

        pages = [await browser.new_page() for _ in range(len(batch))]

        tasks = [
            scrape_property(page, h_num, s_name, boro)
            for page, (i, h_num, s_name, boro) in zip(pages, batch)
        ]

        batch_results = await asyncio.gather(*tasks, return_exceptions=True)

        for (i, h_num, s_name, boro), result in zip(batch, batch_results):
            if isinstance(result, Exception):
                print(f"  ❌  {h_num} {s_name} — {result}")
                logger.error(f"{h_num} {s_name}: {result}")
            else:
                if result:
                    print(f"  ✨  {h_num} {s_name}  —  {len(result)} violation(s)")
                    found_count += len(result)
                results_all.extend(result)

        for page in pages:
            try:
                await page.close()
            except:
                pass

        processed_count += len(batch)
        pct = int(processed_count / total * 100)
        bar = "█" * (pct // 5) + "░" * (20 - pct // 5)
        print(f"  [{bar}] {processed_count}/{total}  ({pct}%)  —  {found_count} violations found")

    return results_all, processed_count


def _scan_coverage_note(scan_info: dict) -> str:
    """Render a small scan-coverage banner for the email."""
    if not scan_info:
        return ""
    start    = scan_info.get("start", 0)
    end      = scan_info.get("end", 0)
    total    = scan_info.get("total", 0)
    count    = end - start
    from_lbl = f"#{start + 1}" if start else "#1"
    range_lbl = f"{from_lbl}–#{end}" if count > 1 else from_lbl
    of_lbl   = f" of {total}" if total else ""
    return f"""
    <div style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;
                padding:12px 18px;margin-bottom:20px;font-size:13px;color:#1e40af;">
      <strong>Scan coverage:</strong>&nbsp; {count} propert{'y' if count==1 else 'ies'} scanned
      &nbsp;({range_lbl}{of_lbl} in portfolio)
    </div>"""


def build_email_html(final_report, scan_info=None):
    """Build formatted HTML email report"""
    df = pd.DataFrame(final_report)
    now = datetime.now().strftime("%B %d, %Y at %I:%M %p")
    total_amount = 0.0

    property_blocks = ""
    for prop, group in df.groupby("Property"):
        rows_html = ""
        prop_total = 0.0
        portal_url = group.iloc[0]["Portal URL"]

        for _, row in group.iterrows():
            amt_str = row["Amount Due"].replace("$", "").replace(",", "").strip()
            try:
                amt_val = float(amt_str)
                total_amount += amt_val
                prop_total += amt_val
                amt_display = f"${amt_val:,.2f}"
            except:
                amt_display = row["Amount Due"] if row["Amount Due"] not in ("N/A", "") else "—"

            status = row.get("Status", "N/A")
            status_color = "#dc2626" if any(x in status.upper() for x in ("OPEN", "PENDING", "ACTIVE")) else "#6b7280"

            rows_html += f"""
            <tr style="border-bottom:1px solid #f3f4f6;">
              <td style="padding:10px 12px;font-size:13px;color:#374151;font-weight:500;">{row['Ticket #']}</td>
              <td style="padding:10px 12px;font-size:13px;color:#374151;">{row['Date']}</td>
              <td style="padding:10px 12px;font-size:13px;color:#374151;">{row['Respondent']}</td>
              <td style="padding:10px 12px;font-size:13px;color:#374151;">{row['Agency']}</td>
              <td style="padding:10px 12px;font-size:13px;color:#374151;">{row['Description']}</td>
              <td style="padding:10px 12px;font-size:13px;font-weight:600;color:{status_color};">{status}</td>
              <td style="padding:10px 12px;font-size:13px;font-weight:700;color:#15803d;text-align:right;">{amt_display}</td>
            </tr>"""

        property_blocks += f"""
        <div style="margin-bottom:32px;">
          <div style="background:#1e3a5f;color:white;padding:12px 20px;border-radius:8px 8px 0 0;">
            <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;">
              <div>
                <span style="font-size:16px;font-weight:700;">📍 {prop}</span>
                <a href="{portal_url}" style="margin-left:12px;font-size:12px;color:#93c5fd;text-decoration:none;
                   border:1px solid #93c5fd;padding:2px 10px;border-radius:20px;">
                  🔗 View on NYC Portal
                </a>
              </div>
              <span style="font-size:13px;opacity:0.85;">
                {len(group)} violation{'s' if len(group)!=1 else ''} &nbsp;|&nbsp; Subtotal: <strong>${prop_total:,.2f}</strong>
              </span>
            </div>
          </div>
          <table width="100%" cellpadding="0" cellspacing="0"
                 style="border-collapse:collapse;border:1px solid #e5e7eb;border-top:none;">
            <thead>
              <tr style="background:#f8fafc;border-bottom:2px solid #e5e7eb;">
                <th style="padding:10px 12px;text-align:left;font-size:12px;color:#6b7280;font-weight:600;">TICKET #</th>
                <th style="padding:10px 12px;text-align:left;font-size:12px;color:#6b7280;font-weight:600;">DATE</th>
                <th style="padding:10px 12px;text-align:left;font-size:12px;color:#6b7280;font-weight:600;">RESPONDENT</th>
                <th style="padding:10px 12px;text-align:left;font-size:12px;color:#6b7280;font-weight:600;">AGENCY</th>
                <th style="padding:10px 12px;text-align:left;font-size:12px;color:#6b7280;font-weight:600;">DESCRIPTION</th>
                <th style="padding:10px 12px;text-align:left;font-size:12px;color:#6b7280;font-weight:600;">STATUS</th>
                <th style="padding:10px 12px;text-align:right;font-size:12px;color:#6b7280;font-weight:600;">AMOUNT DUE</th>
              </tr>
            </thead>
            <tbody>{rows_html}</tbody>
          </table>
        </div>"""

    html = f"""
    <html><body style="font-family:Arial,sans-serif;max-width:960px;margin:0 auto;padding:24px;background:#f9fafb;">
      <div style="background:#1e3a5f;color:white;padding:24px 28px;border-radius:10px;margin-bottom:24px;">
        <h1 style="margin:0;font-size:22px;font-weight:700;">🏙️ NYC ECB Violations Report</h1>
        <p style="margin:6px 0 0;opacity:0.8;font-size:14px;">Generated {now} &nbsp;·&nbsp; Violations from the last 6 months</p>
      </div>
      {_scan_coverage_note(scan_info)}
      <div style="display:flex;gap:16px;margin-bottom:28px;">
        <div style="flex:1;background:white;border:1px solid #e5e7eb;border-radius:8px;padding:16px 20px;text-align:center;">
          <div style="font-size:28px;font-weight:700;color:#1e3a5f;">{df['Property'].nunique()}</div>
          <div style="font-size:12px;color:#6b7280;margin-top:4px;">PROPERTIES WITH FINDINGS</div>
        </div>
        <div style="flex:1;background:white;border:1px solid #e5e7eb;border-radius:8px;padding:16px 20px;text-align:center;">
          <div style="font-size:28px;font-weight:700;color:#dc2626;">{len(df)}</div>
          <div style="font-size:12px;color:#6b7280;margin-top:4px;">OPEN VIOLATIONS</div>
        </div>
        <div style="flex:1;background:white;border:1px solid #e5e7eb;border-radius:8px;padding:16px 20px;text-align:center;">
          <div style="font-size:28px;font-weight:700;color:#15803d;">${total_amount:,.2f}</div>
          <div style="font-size:12px;color:#6b7280;margin-top:4px;">TOTAL AMOUNT DUE</div>
        </div>
      </div>
      {property_blocks}
      <p style="color:#9ca3af;font-size:11px;text-align:center;margin-top:24px;">
        Source: NYC CityPay ECB Portal &nbsp;·&nbsp; Violations from the last 6 months only
      </p>
    </body></html>"""

    return html, total_amount


def send_email(final_report, scan_info=None):
    """Send HTML email report"""
    logger.info(f"\n📧 Sending report to {EMAIL_TO}...")
    html, total = build_email_html(final_report, scan_info)
    n = len(final_report)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🚨 NYC Violations Report — {n} violation{'s' if n!=1 else ''} | ${total:,.2f} due | {datetime.now().strftime('%m/%d/%Y')}"
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP("smtp.aol.com", 587) as s:
            s.starttls()
            s.login(EMAIL_FROM, EMAIL_PASSWORD)
            s.send_message(msg)
        logger.info(f"   ✅ Report sent to {EMAIL_TO}")
    except Exception as e:
        logger.error(f"   ❌ Email failed: {e}")


def print_summary(final_report):
    """Print formatted terminal summary"""
    df = pd.DataFrame(final_report)
    logger.info("\n" + "═" * 70)
    logger.info("  NYC ECB VIOLATIONS REPORT  —  " + datetime.now().strftime("%m/%d/%Y %I:%M %p"))
    logger.info("═" * 70)

    total_amount = 0.0
    for prop, group in df.groupby("Property"):
        portal_url = group.iloc[0]["Portal URL"]
        logger.info(f"\n📍 {prop}  ({len(group)} violation{'s' if len(group) != 1 else ''})")
        logger.info(f"   🔗 {portal_url}")
        logger.info("─" * 70)
        for _, row in group.iterrows():
            amt_str = row["Amount Due"].replace("$", "").replace(",", "").strip()
            try:
                total_amount += float(amt_str)
                amt_display = f"${float(amt_str):,.2f}"
            except:
                amt_display = row["Amount Due"] if row["Amount Due"] not in ("N/A", "") else "—"

            logger.info(f"  🎫 Ticket:  {row['Ticket #']}")
            logger.info(f"     Date:    {row['Date']}")
            logger.info(f"     Who:     {row['Respondent']}")
            logger.info(f"     Agency:  {row.get('Agency', 'N/A')}")
            logger.info(f"     Issue:   {row['Description']}")
            logger.info(f"     Status:  {row.get('Status', 'N/A')}")
            logger.info(f"     💰 Due:  {amt_display}")

    logger.info("═" * 70)
    if total_amount > 0:
        logger.info(f"  TOTAL AMOUNT DUE: ${total_amount:,.2f}")
    logger.info(f"  {len(df)} violation(s) across {df['Property'].nunique()} property/properties")
    logger.info("═" * 70 + "\n")


async def main():
    """Main entry point"""
    global DAYS_BACK
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=DAYS_BACK)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--start", type=int, default=0)
    args = parser.parse_args()
    DAYS_BACK = args.days
    limit = args.limit
    start = args.start

    start_time = datetime.now()

    try:
        df_props = pd.read_csv(PROPERTIES_FILE)
    except Exception as e:
        print(f"  ❌  Could not read {PROPERTIES_FILE}: {e}")
        return

    total = len(df_props)
    print(f"\n  CityPay Scanner  ·  {datetime.now().strftime('%B %d, %Y')}  ·  Last {DAYS_BACK} days")
    print(f"  Portfolio: {total} properties\n")

    MASTER_FILE = "violations_found.csv"
    CHECKPOINT_FILE = "violations_checkpoint.csv"
    SCANNED_FILE = "scanned_properties.txt"

    # Load master log
    if os.path.exists(MASTER_FILE):
        df_master = pd.read_csv(MASTER_FILE, dtype=str)
        known_tickets = set(df_master["Ticket #"].str.strip().tolist())
        print(f"  Master log: {len(known_tickets):,} known tickets\n")
    else:
        df_master = pd.DataFrame()
        known_tickets = set()

    # Load checkpoint
    if os.path.exists(CHECKPOINT_FILE):
        try:
            existing = pd.read_csv(CHECKPOINT_FILE, dtype=str)
            new_this_run = existing.to_dict("records")
            print(f"  Resuming from checkpoint — {len(new_this_run)} ticket(s) already found")
        except pd.errors.EmptyDataError:
            new_this_run = []
    else:
        new_this_run = []

    # Load already scanned
    if os.path.exists(SCANNED_FILE):
        with open(SCANNED_FILE, "r") as f:
            already_scanned = set(line.strip() for line in f.readlines())
        print(f"  Skipping {len(already_scanned)} already-scanned properties\n")
    else:
        already_scanned = set()

    # Build queue of properties to scan
    properties_queue = []
    for i, row in df_props.iterrows():
        h_num = str(row['house_number']).strip()
        s_name = str(row['street_name']).strip()
        boro = str(row['borough']).strip()
        prop_key = f"{h_num} {s_name}"

        if prop_key in already_scanned:
            continue

        properties_queue.append((i, h_num, s_name, boro))

    if start:
        properties_queue = properties_queue[start:]
        print(f"  (Starting from property #{start + 1})\n")

    if limit:
        properties_queue = properties_queue[:limit]
        print(f"  (Limited to {limit} properties)\n")

    # Set up early-exit listener
    stop_event = asyncio.Event()
    _start_stop_listener(stop_event, asyncio.get_event_loop())

    # Launch browser and scan concurrently
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, slow_mo=SLOW_MO)

        found_all, processed_count = await scan_properties_concurrent(browser, properties_queue, stop_event=stop_event)

    end_position = start + processed_count

    # Process results — index by property for O(n+m) dedup
    found_by_prop = {}
    for v in found_all:
        found_by_prop.setdefault(v["Property"], []).append(v)

    skipped_clean = 0
    for i, h_num, s_name, boro in properties_queue:
        prop_key = f"{h_num} {s_name}"
        prop_found = found_by_prop.get(prop_key, [])

        if prop_found:
            truly_new = [v for v in prop_found if str(v.get("Ticket #", "")).strip() not in known_tickets]
            if truly_new:
                new_this_run.extend(truly_new)
                for v in truly_new:
                    known_tickets.add(str(v.get("Ticket #", "")).strip())
            else:
                skipped_clean += 1
        else:
            skipped_clean += 1

    # Checkpoint once after processing all results
    if new_this_run:
        pd.DataFrame(new_this_run).to_csv(CHECKPOINT_FILE, index=False)
    with open(SCANNED_FILE, "a") as f:
        f.writelines(f"{h_num} {s_name}\n" for _, h_num, s_name, _ in properties_queue)

    # Append to master log
    if new_this_run:
        df_new = pd.DataFrame(new_this_run)
        df_master = pd.concat([df_master, df_new], ignore_index=True) if not df_master.empty else df_new
        df_master.to_csv(MASTER_FILE, index=False)

    # Cleanup
    for f in [CHECKPOINT_FILE, SCANNED_FILE]:
        if os.path.exists(f):
            os.remove(f)

    # Email report
    cutoff = datetime.now() - timedelta(days=DAYS_BACK)
    if not df_master.empty and "Date" in df_master.columns:
        def parse_date(d):
            try:
                return datetime.strptime(str(d).strip(), '%m/%d/%Y')
            except:
                return None
        df_master["_parsed"] = df_master["Date"].apply(parse_date)
        df_filtered = df_master[df_master["_parsed"].apply(lambda d: d is not None and d >= cutoff)].drop(columns=["_parsed"])
        email_report = df_filtered.to_dict("records")
    else:
        email_report = []

    # Save resume position
    PROGRESS_FILE = "scan_progress.json"
    progress = {}
    if os.path.exists(PROGRESS_FILE):
        try:
            with open(PROGRESS_FILE) as f:
                progress = json.load(f)
        except Exception:
            pass
    progress["citypay"] = end_position
    with open(PROGRESS_FILE, "w") as f:
        json.dump(progress, f)

    elapsed = (datetime.now() - start_time).total_seconds()

    print(f"\n  ─────────────────────────────────────────")
    if new_this_run:
        print(f"  {len(new_this_run)} new ticket(s) added to master log")
    else:
        print(f"  No new tickets this run")
    if email_report:
        total_props = len(set(r["Property"] for r in email_report))
        print(f"  Report: {len(email_report)} violation(s) across {total_props} properties")
    print(f"  Stopped at property #{end_position}  (resume option will start from #{end_position + 1})")
    print(f"  Time: {elapsed:.0f}s ({elapsed/60:.1f} min)")
    print(f"  ─────────────────────────────────────────\n")

    scan_info = {"start": start, "end": end_position, "total": total}

    if email_report:
        print_summary(email_report)
        send_email(email_report, scan_info)
    else:
        print("  Nothing to report in the selected date range.")


if __name__ == "__main__":
    asyncio.run(main())
