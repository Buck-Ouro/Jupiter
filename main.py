import os
import asyncio
from playwright.async_api import async_playwright
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import nest_asyncio
import datetime
import re
import json

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

# Step 2: Find or create today’s row
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
    async with async_playwright() as p:
        # Configure browser to look more human-like
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--start-maximized"
            ]
        )
        
        # Set up context with proxy and stealth settings
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            viewport={"width": 1366, "height": 768},
            proxy={
                "server": proxy_url,
                "username": proxy_url.split('://')[1].split('@')[0].split(':')[0],
                "password": proxy_url.split('://')[1].split('@')[0].split(':')[1]
            } if proxy_url else None,
            java_script_enabled=True,
            bypass_csp=True
        )

        # Disable WebDriver detection
        await context.add_init_script("""
            delete navigator.__proto__.webdriver;
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        """)

        page = await context.new_page()
        
        try:
            # Randomize mouse movements and delays
            await page.goto("https://jup.ag/perps-earn", 
                          wait_until="networkidle",
                          timeout=60000,
                          referer="https://www.google.com/")
            
            # Human-like interaction pattern
            await page.wait_for_timeout(random.uniform(1000, 3000))
            await page.mouse.move(random.randint(0, 500), random.randint(0, 300))
            await page.wait_for_timeout(random.uniform(500, 1500))
            
            # Try finding APR toggle with multiple selectors
            apr_toggle = await page.query_selector("p.cursor-pointer:has-text('%'), .apr-toggle, [data-testid='apr-button']")
            if apr_toggle:
                await apr_toggle.click()
            
            await page.wait_for_timeout(random.uniform(1000, 2000))
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight/2)")
            await page.wait_for_timeout(random.uniform(1000, 2000))
            
            return await page.inner_text("body")
            
        finally:
            await context.close()
            await browser.close()
            
text = asyncio.get_event_loop().run_until_complete(scrape_jupiter_apr())
lines = text.splitlines()

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

# Step 5: Extract Data
B_str = extract_after("Total Value Locked", lines, must_prefix="$")
C_str = extract_after("Wrapped SOL", lines, must_prefix="$")
E_str = extract_after("Ether (Portal)", lines, must_prefix="$")
G_str = extract_after("Wrapped BTC (Portal)", lines, must_prefix="$")
I_str = extract_after("USD Coin", lines, must_prefix="$")
K_str = extract_usdt_value(lines)
M_str = extract_after("Total Supply", lines)
N_str = extract_after("JLP Price", lines, must_prefix="$")
O_str = extract_after("APR", lines)

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
M = float(M_str.replace("JLP","")) if M_str else 0.0
N = float(N_str) if N_str else 0.0
O = float(O_str) if O_str else 0.0

# Step 7: Write to Sheet
col_map = {
    2: B,  3: C,  4: D,  5: E,  6: F,
    7: G,  8: H,  9: I, 10: J, 11: K,
   12: L, 13: M, 14: N, 15: f"{O}%" if O else "",
}

for col_idx, val in col_map.items():
    cell = f"{chr(64+col_idx)}{row_idx}"
    sheet.update(
        values=[[val]],
        range_name=f"{cell}:{cell}",
        value_input_option="USER_ENTERED"
    )

print(f"✅ Row {row_idx} updated.")
