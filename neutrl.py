import os
import asyncio
import json
import datetime
import re
from playwright.async_api import async_playwright
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import nest_asyncio
from urllib.parse import urlparse

nest_asyncio.apply()

# The app moved from app.neutrl.fi to app.neutrl.finance, and season data now
# lives on a per-season route. Bare /rewards serves a shell with no data.
REWARDS_URL = "https://app.neutrl.finance/rewards/season-2"

# Step 1: Authenticate with Google Sheets
sa_json = os.environ.get("GOOGLEAPI")
sheet_id = os.environ.get("SHEET_ID")
# Wired on PROXY_HTTP: PROXY2_HTTP's credentials are broken, while PROXY_HTTP
# (same residential gateway) tunnels fine from the runner. reservoir.py also
# uses PROXY_HTTP.
proxy_url = os.environ.get("PROXY_HTTP")

if not sa_json or not sheet_id:
    raise ValueError("Missing environment variables: GOOGLEAPI or SHEET_ID")

creds = ServiceAccountCredentials.from_json_keyfile_dict(
    json.loads(sa_json),
    ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
)
client = gspread.authorize(creds)
sheet = client.open_by_key(sheet_id).worksheet("Neutrl")

# Step 2: Find or create today's row
today = datetime.date.today()
today_str = today.strftime("%d/%m/%Y")
col_a = sheet.col_values(1)

if today_str in col_a:
    row_idx = col_a.index(today_str) + 1
    if sheet.cell(row_idx, 2).value:
        print("✅ Today's row already filled; exiting.")
        exit()
else:
    row_idx = len(col_a) + 1
    sheet.update(
        values=[[today_str]],
        range_name=f"A{row_idx}:A{row_idx}",
        value_input_option="USER_ENTERED"
    )


# Step 3: Scraper
async def scrape_neutrl_html():
    """
    Return the raw page HTML, which carries the SSR JSON payload.

    We take page.content() rather than inner_text() because the payload holds
    full-precision values (227588662844.08435) while the rendered UI only shows
    a rounded "227.59B". No /metrics visit is needed either - NUSD supply is in
    the same payload.
    """
    from tempfile import TemporaryDirectory

    async with async_playwright() as p:
        launch_kwargs = dict(
            headless=True,
            ignore_https_errors=True,
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',
                '--no-sandbox',
            ],
            ignore_default_args=['--enable-automation'],
        )

        if proxy_url:
            parsed = urlparse(proxy_url)
            launch_kwargs["proxy"] = {
                "server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}",
                "username": parsed.username,
                "password": parsed.password,
            }
        else:
            print("⚠️ PROXY_HTTP not set - going direct.")

        with TemporaryDirectory() as tmp_profile:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=tmp_profile, **launch_kwargs
            )
            page = context.pages[0] if context.pages else await context.new_page()

            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                window.chrome = {runtime: {}};
            """)

            try:
                if proxy_url:
                    try:
                        print("🌐 Checking proxy IP...")
                        await page.goto("https://httpbin.org/ip",
                                        wait_until="domcontentloaded", timeout=15000)
                        print(f"✅ Proxy IP: {await page.inner_text('body')}")
                    except Exception as e:
                        print(f"⚠️ Could not verify proxy IP (non-critical): {e}")

                print(f"📍 Navigating to {REWARDS_URL} ...")
                await page.goto(REWARDS_URL, wait_until="networkidle", timeout=90000)
                await page.wait_for_timeout(3000)

                html = await page.content()
                print(f"📊 Retrieved {len(html)} chars")

                # Size is a reliable tell: ~32K = challenge page,
                # ~166K = stripped shell (blocked), ~466K = the real thing.
                if "seasonProgram" not in html:
                    print("❌ No season payload. Body preview:")
                    print((await page.inner_text("body"))[:1500])
                    raise RuntimeError(
                        f"Season data absent from {len(html)} char page - "
                        "likely a challenge page or a bot-stripped shell."
                    )
                return html
            finally:
                await context.close()


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


print("🚀 Starting Neutrl scraper...")
html = asyncio.get_event_loop().run_until_complete(scrape_neutrl_html())

season = extract_active_season(html)
if not season:
    raise RuntimeError("Page loaded but no active ethereum season matched.")

total_points = season["points"]
participants = season["participants"]
nusd_supply = extract_nusd_supply(html)   # None -> written as N/A

print(f"\n📊 Extracted ({season['name']}):")
print(f"   Total Points (B): {total_points:,.2f}")
print(f"   Participants (C): {participants:,}")
print(f"   NUSD Supply (D): "
      f"{f'{nusd_supply:,.2f}' if nusd_supply is not None else 'N/A'}")

# Step 4: Write to Sheet
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
