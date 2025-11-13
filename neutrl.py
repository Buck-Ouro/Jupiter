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
async def scrape_neutrl_stats():
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
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--no-sandbox',
                ],
                ignore_default_args=['--enable-automation'],
            )
            page = context.pages[0] if context.pages else await context.new_page()
            
            # Add stealth scripts
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                window.chrome = {runtime: {}};
            """)

            try:
                await page.goto("https://httpbin.org/ip", wait_until="domcontentloaded")
                print("🌐 Proxy IP content:")
                print(await page.inner_text("body"))

                print("📍 Navigating to Neutrl rewards page...")
                await page.goto("https://app.neutrl.fi/rewards", wait_until="networkidle", timeout=60000)
                
                print("⏳ Waiting for initial content to load...")
                await page.wait_for_timeout(3000)

                # Scroll to ensure all content is loaded
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(2000)
                await page.evaluate("window.scrollTo(0, 0)")
                await page.wait_for_timeout(2000)

                print("📄 Extracting rewards page content...")
                rewards_text = await page.inner_text("body")
                
                # Now navigate to metrics page for TVL/NUSD Supply
                print("\n📍 Navigating to Neutrl metrics page for NUSD Supply...")
                await page.goto("https://app.neutrl.fi/metrics", wait_until="networkidle", timeout=60000)
                
                print("⏳ Waiting for metrics to load...")
                await page.wait_for_timeout(5000)
                
                # Scroll metrics page
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(2000)
                await page.evaluate("window.scrollTo(0, 0)")
                await page.wait_for_timeout(2000)

                print("📄 Extracting metrics page content...")
                metrics_text = await page.inner_text("body")
                print(f"📊 Retrieved {len(rewards_text)} characters from rewards page")
                print(f"📊 Retrieved {len(metrics_text)} characters from metrics page")
                print("=" * 80)
                print("REWARDS PAGE (first 1000 chars):")
                print("=" * 80)
                print(rewards_text[:1000])
                print("=" * 80)
                print("METRICS PAGE (first 1000 chars):")
                print("=" * 80)
                print(metrics_text[:1000])
                print("=" * 80)
                
                return rewards_text, metrics_text

            except Exception as e:
                print(f"❌ Error during scraping: {e}")
                raise
            finally:
                await context.close()

# Run scraper
print("🚀 Starting Neutrl scraper...")
rewards_text, metrics_text = asyncio.get_event_loop().run_until_complete(scrape_neutrl_stats())

# Initialize lines from both pages
rewards_lines = rewards_text.splitlines()
metrics_lines = metrics_text.splitlines()
print(f"\n📝 Rewards page: {len(rewards_lines)} lines")
print(f"📝 Metrics page: {len(metrics_lines)} lines")

# Step 4: Extract Data
def extract_value_after_keyword(keyword, lines, lookahead=10):
    """
    Find the keyword and look forward for a number.
    lookahead: how many lines to search forward
    """
    for i, line in enumerate(lines):
        if keyword.upper() in line.upper():
            print(f"   Found keyword '{keyword}' at line {i}: {line}")
            # Look forward for a number
            for j in range(i + 1, min(len(lines), i + lookahead + 1)):
                next_line = lines[j].strip()
                # Remove commas and look for numbers (including decimals like 64.40B)
                # Check for patterns like "64.40B" or "1966" or "63520224569.86"
                cleaned = next_line.replace(",", "")
                
                # Match numbers with optional B/M/K suffix and dollar signs
                match = re.match(r"^[\$]?([\d.]+)([BMK]?)$", cleaned)
                if match:
                    number_str = match.group(1)
                    suffix = match.group(2)
                    print(f"   Found value: {next_line} (number: {number_str}, suffix: {suffix})")
                    return next_line, number_str, suffix
    return None, None, None

def extract_value_before_keyword(keyword, lines, lookback=10):
    """
    Find the keyword and look backwards for a number.
    lookback: how many lines to search backwards
    """
    for i, line in enumerate(lines):
        if keyword.upper() in line.upper():
            print(f"   Found keyword '{keyword}' at line {i}: {line}")
            # Look backwards for a number
            for j in range(max(0, i - lookback), i):
                prev_line = lines[j].strip()
                # Remove commas and dollar signs, look for numbers
                cleaned = prev_line.replace(",", "").replace("$", "")
                
                # Match numbers with optional B/M/K suffix (e.g., $123M or 123M)
                match = re.match(r"^([\d.]+)([BMK]?)$", cleaned)
                if match:
                    number_str = match.group(1)
                    suffix = match.group(2)
                    print(f"   Found value: {prev_line} (number: {number_str}, suffix: {suffix})")
                    return prev_line, number_str, suffix
    return None, None, None

def convert_to_number(value_str, number_str, suffix):
    """Convert string with suffix (B/M/K) to actual number"""
    if not number_str:
        return 0
    
    num = float(number_str)
    
    # Apply multiplier based on suffix
    if suffix == 'B':
        num *= 1_000_000_000
    elif suffix == 'M':
        num *= 1_000_000
    elif suffix == 'K':
        num *= 1_000
    
    return num

print("\n🔍 Searching for data fields...")

# Extract from REWARDS page
# Extract S1 Rewards Issued (Total Points) - look AFTER the keyword
rewards_str, rewards_num, rewards_suffix = extract_value_after_keyword("S1 REWARDS ISSUED", rewards_lines, lookahead=5)
print(f"   S1 Rewards Issued (B): {rewards_str if rewards_str else 'NOT FOUND'}")

# Extract Total Participants - look AFTER the keyword
participants_str, participants_num, participants_suffix = extract_value_after_keyword("TOTAL PARTICIPANTS", rewards_lines, lookahead=5)
print(f"   Total Participants (C): {participants_str if participants_str else 'NOT FOUND'}")

# Extract from METRICS page
# Extract NUSD Supply - look AFTER the keyword (format: NUSD Supply \n $123.81M)
nusd_str, nusd_num, nusd_suffix = extract_value_after_keyword("NUSD SUPPLY", metrics_lines, lookahead=5)
print(f"   NUSD Supply/TVL (D): {nusd_str if nusd_str else 'NOT FOUND'}")

# Step 5: Convert to numbers
total_points = convert_to_number(rewards_str, rewards_num, rewards_suffix)
participants = int(float(participants_num)) if participants_num else 0
nusd_supply = convert_to_number(nusd_str, nusd_num, nusd_suffix)

print(f"\n📊 Calculated values:")
print(f"   Total Points (B): {total_points:,.2f}")
print(f"   Participants (C): {participants:,}")
print(f"   NUSD Supply (D): {nusd_supply:,.2f}")

# Step 6: Write to Sheet
print(f"\n💾 Writing to sheet row {row_idx}...")

# Write Total Points to column B
sheet.update(
    values=[[total_points]],
    range_name=f"B{row_idx}:B{row_idx}",
    value_input_option="USER_ENTERED"
)

# Write Participants to column C
sheet.update(
    values=[[participants]],
    range_name=f"C{row_idx}:C{row_idx}",
    value_input_option="USER_ENTERED"
)

# Write NUSD Supply to column D
sheet.update(
    values=[[nusd_supply]],
    range_name=f"D{row_idx}:D{row_idx}",
    value_input_option="USER_ENTERED"
)

print(f"✅ Row {row_idx} updated successfully!")
print(f"   Date: {today_str}")
print(f"   Total Points: {total_points:,.2f}")
print(f"   Participants: {participants:,}")
print(f"   NUSD Supply: {nusd_supply:,.2f}")
