import os
import asyncio
import random
from playwright.async_api import async_playwright
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import nest_asyncio
import datetime
import re
import json
from urllib.parse import urlparse

nest_asyncio.apply()

# Step 1: Authenticate with Google Sheets
sa_json = os.environ.get("GOOGLEAPI")
sheet_id = os.environ.get("SHEET_ID")
proxy_url = os.environ.get("PROXY_HTTP")

if not sa_json or not sheet_id:
    raise ValueError("Missing environment variables: GOOGLEAPI or SHEET_ID")

creds = ServiceAccountCredentials.from_json_keyfile_dict(
    json.loads(sa_json),
    ['https://spreadsheets.google.com/feeds','https://www.googleapis.com/auth/drive']
)
client = gspread.authorize(creds)
sheet = client.open_by_key(sheet_id).worksheet("Jupiter")

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
async def scrape_jupiter_apr():
    from pathlib import Path
    from tempfile import TemporaryDirectory

    async with async_playwright() as p:
        parsed = urlparse(proxy_url)
        proxy_config = {
            "server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}",
            "username": parsed.username,
            "password": parsed.password
        }

        with TemporaryDirectory() as tmp_profile:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=tmp_profile,
                headless=True,
                proxy=proxy_config,
                ignore_https_errors=True,
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            page = context.pages[0] if context.pages else await context.new_page()

            try:
                await page.goto("https://httpbin.org/ip", wait_until="domcontentloaded")
                print("🌐 Proxy IP content:")
                print(await page.inner_text("body"))

                print("📍 Navigating to Jupiter perps-earn...")
                await page.goto("https://jup.ag/perps-earn", wait_until="networkidle", timeout=60000)
                await page.wait_for_timeout(8000)

                print("📄 Checking page content...")
                
                # Try multiple selector strategies
                clicked = False
                selectors = [
                    "button.cursor-pointer",  # New button structure
                    "button:has-text('%')",
                    "p.cursor-pointer",       # Old structure as fallback
                    "p[class*='cursor']",
                    "[role='button']:has-text('%')"
                ]
                
                for selector in selectors:
                    try:
                        print(f"🔍 Trying selector: {selector}")
                        await page.wait_for_selector(selector, timeout=5000)
                        elements = await page.query_selector_all(selector)
                        print(f"   Found {len(elements)} elements")
                        
                        for el in elements:
                            txt = await el.inner_text()
                            if "%" in txt:
                                print(f"   ✅ Clicking element with text: {txt[:50]}")
                                await el.click()
                                clicked = True
                                break
                        if clicked:
                            break
                    except Exception as e:
                        print(f"   ⚠️ Selector failed: {str(e)[:100]}")
                        continue

                if not clicked:
                    print("⚠️ Could not find clickable element, continuing anyway...")

                await page.wait_for_timeout(3000)
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(3000)

                body_text = await page.inner_text("body")
                print(f"📊 Retrieved {len(body_text)} characters")
                print("=" * 80)
                print("FIRST 2000 CHARACTERS OF PAGE:")
                print("=" * 80)
                print(body_text[:2000])
                print("=" * 80)
                
                return body_text

            except Exception as e:
                print(f"❌ Error during scraping: {e}")
                raise
            finally:
                await context.close()

# Run scraper
print("🚀 Starting scraper...")
text = asyncio.get_event_loop().run_until_complete(scrape_jupiter_apr())

# Initialize lines
lines = text.splitlines()
print(f"\n📝 Total lines extracted: {len(lines)}")

# Step 4: Parsing Helpers
def extract_after(keyword, lines, must_prefix=None):
    for i, line in enumerate(lines):
        if keyword in line:
            for l in lines[i+1:]:
                s = l.strip()
                if not s: continue
                if must_prefix and not s.startswith(must_prefix): continue
                m = re.search(r"[\d.]+", s.replace(",", ""))
                if m: return m.group(0)
    return ""

def extract_usdt_value(lines):
    pattern = re.compile(r"^[\d,]+\.\d{2}\s+USDT$")
    for i, line in enumerate(lines):
        if pattern.match(line.strip()):
            for j in range(i-1, -1, -1):
                prev = lines[j].strip()
                if prev.startswith("$"):
                    match = re.search(r"[\d.]+", prev.replace(",", ""))
                    if match:
                        return match.group(0)
    return ""

# Step 5: Extract Data - with debug output
print("\n🔍 Searching for data fields...")

B_str = extract_after("Total Value Locked", lines, must_prefix="$")
print(f"   Total Value Locked (B): {B_str if B_str else 'NOT FOUND'}")

C_str = extract_after("Wrapped SOL", lines, must_prefix="$")
print(f"   Wrapped SOL (C): {C_str if C_str else 'NOT FOUND'}")

E_str = extract_after("Ether (Portal)", lines, must_prefix="$")
print(f"   Ether Portal (E): {E_str if E_str else 'NOT FOUND'}")

G_str = extract_after("Wrapped BTC (Portal)", lines, must_prefix="$")
print(f"   Wrapped BTC (G): {G_str if G_str else 'NOT FOUND'}")

I_str = extract_after("USD Coin", lines, must_prefix="$")
print(f"   USD Coin (I): {I_str if I_str else 'NOT FOUND'}")

K_str = extract_usdt_value(lines)
print(f"   USDT (K): {K_str if K_str else 'NOT FOUND'}")

M_str = extract_after("Total Supply", lines)
print(f"   Total Supply (M): {M_str if M_str else 'NOT FOUND'}")

N_str = extract_after("JLP Price", lines, must_prefix="$")
print(f"   JLP Price (N): {N_str if N_str else 'NOT FOUND'}")

O_str = extract_after("APR", lines)
print(f"   APR (O): {O_str if O_str else 'NOT FOUND'}")

# Step 6: Convert and Calculate Ratios
B = float(B_str) if B_str else 0.0
C = float(C_str) if C_str else 0.0
D = C/B if B else 0.0
E = float(E_str) if E_str else 0.0
F = E/B if B else 0.0
G = float(G_str) if G_str else 0.0
H = G/B if B else 0.0
I = float(I_str) if I_str else 0.0
J = I/B if B else 0.0
K = float(K_str) if K_str else 0.0
L = K/B if B else 0.0
M = float(M_str.replace("JLP","").replace(",","").strip()) if M_str else 0.0
N = float(N_str) if N_str else 0.0
O = float(O_str) if O_str else 0.0

print(f"\n📊 Calculated values:")
print(f"   B (TVL): ${B:,.2f}")
print(f"   C (SOL): ${C:,.2f} | D (ratio): {D:.4f}")
print(f"   E (ETH): ${E:,.2f} | F (ratio): {F:.4f}")
print(f"   G (BTC): ${G:,.2f} | H (ratio): {H:.4f}")
print(f"   I (USDC): ${I:,.2f} | J (ratio): {J:.4f}")
print(f"   K (USDT): ${K:,.2f} | L (ratio): {L:.4f}")
print(f"   M (Supply): {M:,.2f}")
print(f"   N (Price): ${N:.4f}")
print(f"   O (APR): {O}%")

# Step 7: Write to Sheet
col_map = {
    2: B,  3: C,  4: D,  5: E,  6: F,
    7: G,  8: H,  9: I, 10: J, 11: K,
   12: L, 13: M, 14: N, 15: f"{O}%" if O else "",
}

print(f"\n💾 Writing to sheet row {row_idx}...")
for col_idx, val in col_map.items():
    cell = f"{chr(64+col_idx)}{row_idx}"
    sheet.update(
        values=[[val]],
        range_name=f"{cell}:{cell}",
        value_input_option="USER_ENTERED"
    )

print(f"✅ Row {row_idx} updated successfully!")
