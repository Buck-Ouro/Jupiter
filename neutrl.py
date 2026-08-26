import os
import json
import time
import datetime
import re
import httpx
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# ---------------------------------------------------------------------------
# Neutrl scraper — Bright Data Web Unlocker edition (geo-aware).
#
# History of the break (Aug 2026):
#   1. The old Playwright + rotating-residential-proxy approach died: Vercel now
#      challenges EVERY residential exit ("Security Checkpoint | Code 21").
#   2. Web Unlocker clears the Vercel checkpoint, but Neutrl GEO-BLOCKS restricted
#      regions ("Not available here yet ... under current regulations",
#      RouteGeoRestrictedBoundary). Web Unlocker's default exit (US/GB/AE ... )
#      lands in a blocked region -> a 166K "Unavailable | Neutrl" shell with no
#      season data. render:true does NOT help (geo block is server-side) and is
#      flaky besides.
#
# The fix: pin Web Unlocker's egress to an ALLOWED country. In an allowed region
# the raw HTML is the full ~442K SSR page and already carries the season payload
# (seasonProgram), so NO browser render is needed. Verified allowed: de, sg, ch,
# jp, nl, fr, ro, pl, br, za, hk, vn, id, in, tr. Blocked: us, gb, ae.
# We try a short fallback list in case one country's pool is unavailable.
# ---------------------------------------------------------------------------

REWARDS_URLS = [
    "https://app.neutrl.finance/rewards/season-2",   # current & final points season (to mid-Sept 2026)
    "https://app.neutrl.finance/rewards",            # fallback
]

WU_ENDPOINT = "https://api.brightdata.com/request"
WU_ZONE     = "web_unlocker1"
# Egress countries Neutrl allows. First that returns a parseable season wins.
ALLOWED_COUNTRIES = ["de", "sg", "ch", "jp", "nl"]

DRY_RUN = bool(os.environ.get("DRY_RUN"))

# --- env ---
sa_json          = os.environ.get("GOOGLEAPI")
sheet_id         = os.environ.get("SHEET_ID")
unlocker_key     = os.environ.get("UNLOCKER_KEY")

if not unlocker_key:
    raise ValueError("Missing environment variable: UNLOCKER_KEY")
if not DRY_RUN and (not sa_json or not sheet_id):
    raise ValueError("Missing environment variables: GOOGLEAPI or SHEET_ID")


def fetch_via_unlocker(url, country):
    """Fetch a URL through Web Unlocker from a specific egress country (no render).
    Returns HTML str, or None on failure / geo-block / missing payload."""
    payload = {"url": url, "method": "GET", "format": "raw",
               "zone": WU_ZONE, "country": country}
    headers = {"Authorization": f"Bearer {unlocker_key}",
               "Content-Type": "application/json"}
    try:
        resp = httpx.post(WU_ENDPOINT, json=payload, headers=headers, timeout=90.0)
    except Exception as e:
        print(f"   ⚠️ Web Unlocker request error ({country}): {str(e).splitlines()[0]}")
        return None
    if resp.status_code != 200:
        # 401/402/403 => zone/token/billing; else transient
        print(f"   ⚠️ Web Unlocker HTTP {resp.status_code} ({country}): {resp.text[:200]}")
        return None
    html = resp.text
    if "RouteGeoRestrictedBoundary" in html or "isn't offered in your region" in html:
        print(f"   ⛔ geo-blocked from {country} ({len(html)} chars) — trying next region")
        return None
    print(f"   retrieved {len(html)} chars from {country}")
    if "seasonProgram" not in html:
        print(f"   ❌ no season payload from {country} (shell?)")
        return None
    return html


def extract_active_season(text):
    """
    Active ethereum season. Anchored on endTimestamp:null rather than the season
    name, so it follows S3, S4... with no code change. Ignores the plasma-9745
    season present in the same array.
    """
    best = None
    for m in re.finditer(r"ethereum-1-seasonProgram-([A-Za-z0-9_]+)", text):
        win = text[m.start():m.start() + 600]
        end = re.search(r'endTimestamp\\?"?\s*:\s*(null|\d+)', win)
        pts = re.search(r'totalPoints\\?"?\s*:\s*([0-9.eE+-]+)', win)
        cnt = re.search(r'participantCount\\?"?\s*:\s*([0-9]+)', win)
        if not (end and pts and cnt) or end.group(1) != "null":
            continue
        cand = {"name": m.group(1),
                "points": float(pts.group(1)),
                "participants": int(cnt.group(1))}
        if best is None or cand["points"] > best["points"]:
            best = cand
    return best


def extract_nusd_supply(text):
    """NUSD totalSupplyUsd from the same payload. None if absent."""
    m = re.search(r'\\?"Nusd\\?"[\s\S]{0,600}?totalSupplyUsd\\?"?\s*:\s*([0-9.eE+-]+)',
                  text)
    return float(m.group(1)) if m else None


def scrape_neutrl():
    """Try each URL from each allowed country until one yields a parseable season.
    Returns (season, nusd_supply)."""
    for country in ALLOWED_COUNTRIES:
        for url in REWARDS_URLS:
            print(f"📍 {url}  via {country}")
            html = fetch_via_unlocker(url, country)
            if not html:
                continue
            season = extract_active_season(html)
            if season:
                print(f"✅ Season parsed from {country}: {season['name']}")
                return season, extract_nusd_supply(html)
            print(f"   page had data but no active ethereum season matched ({country})")
        time.sleep(2)
    raise RuntimeError(
        "No parseable Neutrl season from any allowed country. If HTTP was "
        "401/402/403 the UNLOCKER_KEY/zone/billing is the problem; if every "
        "region was geo-blocked, Neutrl may have widened restrictions; if pages "
        "loaded but nothing parsed, the 'seasonProgram' anchors changed."
    )


# ------------------------------------------------------------------ DRY RUN
if DRY_RUN:
    print("🧪 DRY_RUN — scrape + parse only, no Google Sheets.")
    season, nusd = scrape_neutrl()
    print(f"\n📊 {season['name']}")
    print(f"   Total Points (B): {season['points']:,.2f}")
    print(f"   Participants (C): {season['participants']:,}")
    print(f"   NUSD Supply (D): {f'{nusd:,.2f}' if nusd is not None else 'N/A'}")
    raise SystemExit(0)


# ------------------------------------------------------------------ Sheets auth
creds = ServiceAccountCredentials.from_json_keyfile_dict(
    json.loads(sa_json),
    ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
)
client = gspread.authorize(creds)
sheet = client.open_by_key(sheet_id).worksheet("Neutrl")

# find or create today's row
today = datetime.date.today()
today_str = today.strftime("%d/%m/%Y")
col_a = sheet.col_values(1)

if today_str in col_a:
    row_idx = col_a.index(today_str) + 1
    if sheet.cell(row_idx, 2).value:
        print("✅ Today's row already filled; exiting.")
        raise SystemExit(0)
else:
    row_idx = len(col_a) + 1
    sheet.update(
        values=[[today_str]],
        range_name=f"A{row_idx}:A{row_idx}",
        value_input_option="USER_ENTERED"
    )

# scrape
print("🚀 Starting Neutrl scraper (Web Unlocker, geo-aware)...")
season, nusd_supply = scrape_neutrl()

total_points = season["points"]
participants = season["participants"]

print(f"\n📊 Extracted ({season['name']}):")
print(f"   Total Points (B): {total_points:,.2f}")
print(f"   Participants (C): {participants:,}")
print(f"   NUSD Supply (D): "
      f"{f'{nusd_supply:,.2f}' if nusd_supply is not None else 'N/A'}")

# write to sheet
print(f"\n💾 Writing to sheet row {row_idx}...")
sheet.update(values=[[total_points]], range_name=f"B{row_idx}:B{row_idx}",
             value_input_option="USER_ENTERED")
sheet.update(values=[[participants]], range_name=f"C{row_idx}:C{row_idx}",
             value_input_option="USER_ENTERED")

# TVL is optional: write N/A rather than 0, and never overwrite a good value.
if nusd_supply is not None:
    sheet.update(values=[[nusd_supply]], range_name=f"D{row_idx}:D{row_idx}",
                 value_input_option="USER_ENTERED")
elif not sheet.cell(row_idx, 4).value:
    sheet.update(values=[["N/A"]], range_name=f"D{row_idx}:D{row_idx}",
                 value_input_option="USER_ENTERED")

print(f"✅ Row {row_idx} updated successfully!")
