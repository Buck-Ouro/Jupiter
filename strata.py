import os
import asyncio
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import nest_asyncio
import datetime
import httpx
from urllib.parse import urlparse

nest_asyncio.apply()

# Configuration
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds between retries

# Step 1: Authenticate with Google Sheets
sa_json = os.environ.get("GOOGLEAPI")
sheet_id = os.environ.get("SHEET_ID")
proxy_url = os.environ.get("PROXY2_HTTP")  # Residential proxy
wallet_address = os.environ.get("Y_WALLET_ADD")

if not sa_json or not sheet_id or not wallet_address:
    raise ValueError("Missing environment variables: GOOGLEAPI, SHEET_ID, or Y_WALLET_ADD")

creds = ServiceAccountCredentials.from_json_keyfile_dict(
    json.loads(sa_json),
    ['https://spreadsheets.google.com/feeds','https://www.googleapis.com/auth/drive']
)
client = gspread.authorize(creds)
sheet = client.open_by_key(sheet_id).worksheet("Strata")

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

# Step 3: Retry wrapper function
async def with_retries(func, *args, **kwargs):
    """Execute function with retry logic"""
    last_exception = None

    for attempt in range(MAX_RETRIES):
        try:
            print(f"🔄 Attempt {attempt + 1}/{MAX_RETRIES}")
            return await func(*args, **kwargs)
        except Exception as e:
            last_exception = e
            print(f"❌ Attempt {attempt + 1} failed: {str(e)}")

            if attempt < MAX_RETRIES - 1:
                print(f"⏳ Waiting {RETRY_DELAY}s before retry...")
                await asyncio.sleep(RETRY_DELAY)
            else:
                print("🚫 All retries exhausted")

    raise last_exception

# Step 4: Fetch Strata stats via HTTP (no browser needed)
async def fetch_strata_stats():
    api_url = f"https://api.strata.money/points/stats?accountAddress={wallet_address}&season=1&chainId=1"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }

    # Try direct first, then with proxy
    async def try_fetch():
        # First try direct (no proxy)
        try:
            print("📡 Trying direct connection...")
            async with httpx.AsyncClient(timeout=15, headers=headers) as client:
                resp = await client.get(api_url)
                if resp.status_code == 200:
                    data = resp.json()
                    if "data" in data:
                        print("✅ Direct connection worked!")
                        return data
                print(f"⚠️ Direct failed with status {resp.status_code}, trying proxy...")
        except Exception as e:
            print(f"⚠️ Direct failed: {e}, trying proxy...")

        # Fall back to residential proxy
        if not proxy_url:
            raise Exception("Direct connection failed and no PROXY2_HTTP configured")

        parsed = urlparse(proxy_url)
        proxy_str = f"{parsed.scheme}://{parsed.username}:{parsed.password}@{parsed.hostname}:{parsed.port}"

        print(f"🌐 Using proxy fallback...")
        async with httpx.AsyncClient(timeout=30, headers=headers, proxy=proxy_str, verify=False) as client:
            resp = await client.get(api_url)
            if resp.status_code != 200:
                raise Exception(f"API returned status {resp.status_code}: {resp.text[:200]}")

            data = resp.json()
            if "data" not in data:
                raise Exception(f"Unexpected response format: {str(data)[:200]}")

            print("✅ Proxy connection worked!")
            return data

    data = await with_retries(try_fetch)

    global_points = data.get("data", {}).get("info", {}).get("points")
    account_points = data.get("data", {}).get("account", {}).get("points", {}).get("total")

    if global_points is None or account_points is None:
        raise Exception("Missing points data in response")

    print(f"📊 Fetched stats: Global points: {global_points:,}, Account points: {account_points:,}")
    return global_points, account_points

# Step 5: Run and write to sheet
async def main():
    global_points, account_points = await fetch_strata_stats()

    sheet.update(
        values=[[global_points, account_points]],
        range_name=f"B{row_idx}:C{row_idx}",
        value_input_option="USER_ENTERED"
    )

    print(f"✅ Row {row_idx} updated with {global_points:,} global points and {account_points:,} account points.")

asyncio.get_event_loop().run_until_complete(main())
