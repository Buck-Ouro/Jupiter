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
today_str = today.strftime("%Y-%m-%d")
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
                # Step 4: Verify proxy connection
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto("https://httpbin.org/ip", wait_until="domcontentloaded")
                print("🌐 Proxy IP content:")
                print(await page.inner_text("body"))

                # Step 5: Get total pages from first API call
                await page.goto(
                    "https://api.cap.app/v1/caps/leaderboard?page=1",
                    wait_until="domcontentloaded",
                    timeout=20000
                )
                await page.wait_for_timeout(1000)
                
                pre_element = await page.query_selector("pre")
                if not pre_element:
                    raise Exception("No <pre> element found on page 1")
                    
                json_text = await pre_element.inner_text()
                data = json.loads(json_text.strip())
                total_pages = data.get("pagination", {}).get("total", 1)
                
                print(f"📊 Detected {total_pages} total pages from API")

                # Step 6: Fetch all pages and calculate total
                grand_total = 0
                processed_pages = 0
                failed_pages = []
                
                MAX_CONCURRENT = 6
                BATCH_SIZE = 18

                async def fetch_single_page(page_number, page_instance):
                    try:
                        url = f'https://api.cap.app/v1/caps/leaderboard?page={page_number}'
                        response = await page_instance.goto(
                            url, wait_until='domcontentloaded', timeout=20000
                        )

                        if response.status != 200:
                            return page_number, None, f"HTTP {response.status}"

                        await asyncio.sleep(0.5)
                        pre_element = await page_instance.query_selector('pre')
                        if not pre_element:
                            return page_number, None, "No <pre> element found"

                        json_text = await pre_element.inner_text()
                        data = json.loads(json_text.strip())

                        if 'entries' not in data:
                            return page_number, None, "No entries in response"

                        page_total = sum(int(entry.get('caps', 0)) for entry in data['entries'])
                        return page_number, page_total, None

                    except Exception as e:
                        return page_number, None, str(e)

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
                                continue
                            page_num, page_total, error = result
                            if error:
                                failed_pages.append(page_num)
                            else:
                                grand_total += page_total
                                processed_pages += 1

                    for page_instance in page_instances:
                        await page_instance.close()

                print(f"🏆 Scraping done: {grand_total:,} caps (processed {processed_pages} pages)")
                if failed_pages:
                    print(f"⚠️ Failed pages: {len(failed_pages)}")
                else:
                    print("✅ All pages processed successfully")

                return grand_total

            finally:
                await context.close()

# Step 7: Run scraper
total_caps = asyncio.get_event_loop().run_until_complete(scrape_cap_points())

# Step 8: Write to Sheet
sheet.update(
    values=[[total_caps]],
    range_name=f"B{row_idx}:B{row_idx}",
    value_input_option="USER_ENTERED"
)

print(f"✅ Row {row_idx} updated with {total_caps:,} caps.")
