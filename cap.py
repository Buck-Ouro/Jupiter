import os
import asyncio
import json
from playwright.async_api import async_playwright
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import nest_asyncio
import datetime
from urllib.parse import urlparse
import time

nest_asyncio.apply()

# Configuration
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds between retries

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
sheet = client.open_by_key(sheet_id).worksheet("Cap")

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

# Step 4: Enhanced scraper with better error handling
async def scrape_cap_points():
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

                # Step 6: Get total pages with retry
                async def get_total_pages():
                    await page.goto(
                        "https://api.cap.app/v1/caps/leaderboard?page=1",
                        wait_until="domcontentloaded",
                        timeout=30000  # Increased timeout
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
                    total_pages = data.get("pagination", {}).get("total", 1)
                    print(f"📊 Detected {total_pages} total pages from API")
                    return total_pages
                
                total_pages = await with_retries(get_total_pages)

                # Step 7: Fetch all pages with enhanced error handling
                grand_total = 0
                processed_pages = 0
                failed_pages = []
                
                MAX_CONCURRENT = 6
                BATCH_SIZE = 18

                async def fetch_single_page(page_number, page_instance):
                    """Fetch single page with built-in retry for failed requests"""
                    for attempt in range(2):  # 2 attempts per page
                        try:
                            url = f'https://api.cap.app/v1/caps/leaderboard?page={page_number}'
                            response = await page_instance.goto(
                                url, wait_until='domcontentloaded', timeout=25000
                            )

                            if response.status != 200:
                                if attempt == 0:
                                    await asyncio.sleep(1)
                                    continue
                                return page_number, None, f"HTTP {response.status}"

                            await asyncio.sleep(0.5)
                            pre_element = await page_instance.query_selector('pre')
                            if not pre_element:
                                if attempt == 0:
                                    await asyncio.sleep(1)
                                    continue
                                return page_number, None, "No <pre> element found"

                            json_text = await pre_element.inner_text()
                            data = json.loads(json_text.strip())

                            if 'entries' not in data:
                                return page_number, None, "No entries in response"

                            page_total = sum(int(entry.get('caps', 0)) for entry in data['entries'])
                            return page_number, page_total, None

                        except Exception as e:
                            if attempt == 0:
                                await asyncio.sleep(1)
                                continue
                            return page_number, None, str(e)
                    
                    return page_number, None, "Max retries exceeded"

                # Process all pages in batches
                for batch_start in range(1, total_pages + 1, BATCH_SIZE):
                    batch_end = min(batch_start + BATCH_SIZE - 1, total_pages)
                    batch_pages = list(range(batch_start, batch_end + 1))

                    page_instances = [
                        await context.new_page()
                        for _ in range(min(MAX_CONCURRENT, len(batch_pages)))
                    ]

                    for i in range(0, len(batch_pages), MAX_CONCURRENT):
                        chunk = batch_pages[i:i + MAX_CONCURRENT]
                        tasks = []
                        for j, page_num in enumerate(chunk):
                            page_instance = page_instances[j % len(page_instances)]
                            tasks.append(fetch_single_page(page_num, page_instance))

                        results = await asyncio.gather(*tasks, return_exceptions=True)

                        for result in results:
                            if isinstance(result, Exception):
                                print(f"⚠️ Task exception: {result}")
                                continue
                            page_num, page_total, error = result
                            if error:
                                failed_pages.append(page_num)
                                print(f"⚠️ Page {page_num} failed: {error}")
                            else:
                                grand_total += page_total
                                processed_pages += 1

                    for page_instance in page_instances:
                        await page_instance.close()

                    print(f"📈 Progress: {processed_pages}/{total_pages} pages processed")

                print(f"🏆 Scraping done: {grand_total:,} caps (processed {processed_pages} pages)")
                
                if failed_pages:
                    print(f"⚠️ Failed pages: {len(failed_pages)} - {failed_pages[:10]}{'...' if len(failed_pages) > 10 else ''}")
                    
                    # If too many failures, consider it a failed run
                    failure_rate = len(failed_pages) / total_pages
                    if failure_rate > 0.1:  # More than 10% failure rate
                        raise Exception(f"High failure rate: {failure_rate:.1%} ({len(failed_pages)}/{total_pages} pages failed)")
                else:
                    print("✅ All pages processed successfully")

                return grand_total

            finally:
                await context.close()

# Step 8: Run scraper with retries
async def main():
    total_caps = await with_retries(scrape_cap_points)
    
    # Step 9: Write to Sheet
    sheet.update(
        values=[[total_caps]],
        range_name=f"B{row_idx}:B{row_idx}",
        value_input_option="USER_ENTERED"
    )
    
    print(f"✅ Row {row_idx} updated with {total_caps:,} caps.")

# Run the main function
asyncio.get_event_loop().run_until_complete(main())
