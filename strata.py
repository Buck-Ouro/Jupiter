import os
import asyncio
import json
from playwright.async_api import async_playwright
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import nest_asyncio
import datetime
from urllib.parse import urlparse

nest_asyncio.apply()

# Configuration
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds between retries

# Step 1: Authenticate with Google Sheets
sa_json = os.environ.get("GOOGLEAPI")
sheet_id = os.environ.get("SHEET_ID")
proxy_url = os.environ.get("PROXY_HTTP")
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
            
            if attempt < MAX_RETRIES - 1:  # Don't sleep on last attempt
                print(f"⏳ Waiting {RETRY_DELAY}s before retry...")
                await asyncio.sleep(RETRY_DELAY)
            else:
                print("🚫 All retries exhausted")
    
    raise last_exception

# Step 4: Scraper function
async def scrape_strata_stats():
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
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )
            
            try:
                # Step 5: Verify proxy connection with retry
                async def verify_proxy():
                    page = context.pages[0] if context.pages else await context.new_page()
                    await page.goto("https://httpbin.org/ip", wait_until="domcontentloaded")
                    proxy_info = await page.inner_text("body")
                    print("🌐 Proxy IP content:")
                    print(proxy_info)
                    return page
                
                page = await with_retries(verify_proxy)

                # Step 6: Fetch Strata stats with retry
                async def get_strata_stats():
                    api_url = f"https://api.strata.money/points/stats?accountAddress={wallet_address}&season=1&chainId=1"
                    
                    await page.goto(
                        api_url,
                        wait_until="domcontentloaded",
                        timeout=30000
                    )
                    await page.wait_for_timeout(1000)
                    
                    pre_element = await page.query_selector("pre")
                    if not pre_element:
                        # Debug output
                        page_title = await page.title()
                        page_content = await page.content()
                        print(f"🔍 Page title: {page_title}")
                        print(f"🔍 Page content preview: {page_content[:300]}...")
                        raise Exception("No <pre> element found - API might be blocked or changed")
                        
                    json_text = await pre_element.inner_text()
                    data = json.loads(json_text.strip())
                    
                    # Extract global points from data.info.points
                    global_points = data.get("data", {}).get("info", {}).get("points")
                    
                    # Extract account points from data.account.points.total
                    account_points = data.get("data", {}).get("account", {}).get("points", {}).get("total")
                    
                    if global_points is None or account_points is None:
                        raise Exception("Missing points data in response")
                    
                    print(f"📊 Fetched stats: Global points: {global_points:,}, Account points: {account_points:,}")
                    return global_points, account_points
                
                global_points, account_points = await with_retries(get_strata_stats)
                print(f"✅ Scraping complete: {global_points:,} global, {account_points:,} account")
                
                return global_points, account_points

            finally:
                await context.close()

# Step 7: Run scraper with retries
async def main():
    global_points, account_points = await with_retries(scrape_strata_stats)
    
    # Step 8: Write to Sheet (both columns at once)
    sheet.update(
        values=[[global_points, account_points]],
        range_name=f"B{row_idx}:C{row_idx}",
        value_input_option="USER_ENTERED"
    )
    
    print(f"✅ Row {row_idx} updated with {global_points:,} global points and {account_points:,} account points.")

# Run the main function
asyncio.get_event_loop().run_until_complete(main())
