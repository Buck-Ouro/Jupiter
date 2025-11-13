import os
import asyncio
import json
import datetime
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

# Step 3: Scraper with Network Interception
async def scrape_neutrl_stats():
    from tempfile import TemporaryDirectory
    
    # Variable to store intercepted data
    captured_data = None

    async def handle_route(route):
        nonlocal captured_data
        
        # Let the request proceed normally
        response = await route.fetch()
        
        # Get the response body BEFORE it's consumed
        try:
            body_text = await response.text()
            body = json.loads(body_text)
            
            print(f"🎯 Intercepted sentio request [Status: {response.status}]")
            print(f"📦 Response body keys: {list(body.keys())}")
            
            # Check if this has the correct structure
            if (body.get("data") and 
                "seasonPrograms" in body["data"]):
                
                print(f"✅ Found seasonPrograms data!")
                print(f"   Programs: {len(body['data']['seasonPrograms'])}")
                print(f"   User: {body['data'].get('user')}")
                
                # Store the data
                captured_data = body
                print(f"✅ Data captured successfully!")
            else:
                print(f"⚠️ Response doesn't have seasonPrograms")
            
            # Continue with the response to the page
            await route.fulfill(response=response, body=body_text)
            
        except Exception as e:
            print(f"⚠️ Error intercepting response: {e}")
            # If something fails, just continue normally
            await route.fulfill(response=response)

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
                # Add more stealth options
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

            # CRITICAL: Attach route interceptor IMMEDIATELY before any navigation
            # This intercepts the request/response and lets us read the body
            await page.route("**/api/sentio", handle_route)
            print("🎧 Route interceptor attached for /api/sentio")

            try:
                # Verify proxy first
                await page.goto("https://httpbin.org/ip", wait_until="domcontentloaded")
                print("🌐 Proxy IP content:")
                print(await page.inner_text("body"))

                # IMPORTANT: Visit homepage first to establish session (listener already active)
                print("🏠 Visiting homepage to establish session...")
                await page.goto("https://app.neutrl.fi/", wait_until="networkidle", timeout=60000)
                await page.wait_for_timeout(3000)
                print("✅ Homepage loaded, cookies/session established")

                # Navigate to rewards page - listener will catch API calls AS THEY HAPPEN
                print("📍 Navigating to rewards page (listener is capturing)...")
                await page.goto("https://app.neutrl.fi/rewards", wait_until="domcontentloaded", timeout=60000)
                
                # Wait for network activity to settle
                print("⏳ Waiting for API calls to complete...")
                await page.wait_for_load_state("networkidle", timeout=30000)
                
                # Wait for API calls to complete
                print("⏳ Waiting for API responses...")
                await page.wait_for_timeout(8000)

                # Check if data was captured
                if captured_data:
                    print("✅ Successfully captured data!")
                    return captured_data

                # If not captured, try various interactions
                print("🔄 Data not captured yet, trying interactions...")
                
                # Try clicking elements that might trigger the API
                try:
                    # Wait for any button or interactive element
                    await page.wait_for_selector("button, [role='button'], a", timeout=5000)
                    print("   Found interactive elements")
                except:
                    print("   No interactive elements found")
                
                # Scroll multiple times
                for i in range(3):
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await page.wait_for_timeout(2000)
                    if captured_data:
                        print(f"✅ Data captured after scroll {i+1}!")
                        return captured_data
                
                # Scroll back to top
                await page.evaluate("window.scrollTo(0, 0)")
                await page.wait_for_timeout(3000)
                
                if captured_data:
                    print("✅ Data captured after scroll to top!")
                    return captured_data

                # Last resort: reload the page
                print("🔄 Reloading page...")
                await page.reload(wait_until="networkidle", timeout=60000)
                await page.wait_for_timeout(5000)

                if captured_data:
                    print("✅ Successfully captured data after reload!")
                    return captured_data
                else:
                    # Print page content for debugging
                    print("\n📄 Page content (first 2000 chars):")
                    body_text = await page.inner_text("body")
                    print(body_text[:2000])
                    print("\n📄 Page URL:", page.url)
                    
                    raise Exception("❌ Failed to capture API response after all attempts")

            except Exception as e:
                print(f"❌ Error during scraping: {e}")
                # Take screenshot for debugging
                try:
                    await page.screenshot(path="neutrl_error.png")
                    print("📸 Screenshot saved as neutrl_error.png")
                except:
                    pass
                raise
            finally:
                await context.close()

# Run scraper
print("🚀 Starting Neutrl scraper...")
api_data = asyncio.get_event_loop().run_until_complete(scrape_neutrl_stats())

# Step 4: Extract Data
print("\n🔍 Extracting data from API response...")

total_points = None
participant_count = None

if api_data and "data" in api_data and "seasonPrograms" in api_data["data"]:
    season_programs = api_data["data"]["seasonPrograms"]
    
    # Find the Ethereum program
    for program in season_programs:
        if "ethereum-1" in program.get("id", ""):
            state = program.get("state", {})
            total_points = state.get("totalPoints")
            participant_count = state.get("participantCount")
            
            print(f"📊 Found Ethereum-1 program:")
            print(f"   ID: {program.get('id')}")
            print(f"   Total Points: {total_points}")
            print(f"   Participant Count: {participant_count}")
            break
    
    if not total_points:
        print("⚠️ Ethereum-1 program not found, showing all programs:")
        for program in season_programs:
            print(f"   - {program.get('id')}: {program.get('state', {})}")

else:
    raise Exception("Invalid API data structure")

if not total_points or not participant_count:
    raise Exception("Could not find Ethereum-1 data in response")

# Convert to numbers (they come as strings)
points_float = float(total_points)
participants_int = int(participant_count)

print(f"\n📊 Extracted values:")
print(f"   Total Points (B): {points_float:,.2f}")
print(f"   Participants (C): {participants_int:,}")

# Step 5: Write to Sheet
print(f"\n💾 Writing to Neutrl sheet row {row_idx}...")

# Write Total Points to column B
sheet.update(
    values=[[points_float]],
    range_name=f"B{row_idx}:B{row_idx}",
    value_input_option="USER_ENTERED"
)

# Write Participants to column C
sheet.update(
    values=[[participants_int]],
    range_name=f"C{row_idx}:C{row_idx}",
    value_input_option="USER_ENTERED"
)

print(f"✅ Row {row_idx} updated successfully!")
print(f"   Date: {today_str}")
print(f"   Total Points: {points_float:,.2f}")
print(f"   Participants: {participants_int:,}")
