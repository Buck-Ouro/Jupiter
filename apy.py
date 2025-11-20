import os
import asyncio
from playwright.async_api import async_playwright
from urllib.parse import urlparse
import requests
import json

async def scrape_reservoir_apy():
    proxy_url = os.environ.get("PROXY_HTTP")
    
    if not proxy_url:
        raise ValueError("Missing environment variable: PROXY_HTTP")

    async with async_playwright() as p:
        parsed = urlparse(proxy_url)
        proxy_config = {
            "server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}",
            "username": parsed.username,
            "password": parsed.password
        }

        browser = await p.chromium.launch(
            headless=True,
            proxy=proxy_config,
            ignore_https_errors=True
        )
        
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        
        page = await context.new_page()

        try:
            # Test proxy connection
            await page.goto("https://httpbin.org/ip", wait_until="domcontentloaded")
            print("🌐 Proxy IP content:")
            print(await page.inner_text("body"))

            print("📍 Navigating to Reservoir...")
            await page.goto(
                "https://app.reservoir.xyz/mint?from=rUSD&fromNetwork=Ethereum&to=srUSDv2&toNetwork=Ethereum", 
                wait_until="networkidle", 
                timeout=60000
            )
            await page.wait_for_timeout(8000)

            print("📄 Checking page content...")
            
            # Get all text content
            body_text = await page.inner_text("body")
            print(f"📊 Retrieved {len(body_text)} characters")
            
            # Debug: print first 2000 characters to see what we're working with
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
            await browser.close()

def extract_apy(text):
    """Extract APY percentage from text"""
    import re
    
    # Look for "Current APY" and capture the percentage after it
    # This pattern looks for "Current APY" followed by optional characters and then a percentage pattern
    patterns = [
        r'Current APY[^\d]*([\d]+\.?[\d]*%)',  # Current APY followed by numbers and %
        r'Current APY.*?([\d]+\.[\d]+%)',      # Current APY followed by decimal percentage
        r'Current APY.*?([\d]+%)',              # Current APY followed by whole number percentage
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if match:
            apy_value = match.group(1).strip()
            print(f"✅ Found APY: {apy_value}")
            return apy_value
    
    # Alternative: Look for any percentage near "APY" text
    lines = text.split('\n')
    for i, line in enumerate(lines):
        if 'current apy' in line.lower():
            # Check current line and next few lines for percentage pattern
            for j in range(i, min(i + 3, len(lines))):
                percentage_match = re.search(r'([\d]+\.?[\d]*)%', lines[j])
                if percentage_match:
                    apy_value = f"{percentage_match.group(1)}%"
                    print(f"✅ Found APY in nearby line: {apy_value}")
                    return apy_value
    
    print("❌ Could not find APY in page content")
    return None

def send_telegram_message(apy_value):
    """Send formatted message to Telegram"""
    telegram_token = os.environ.get("TELEGRAM_KEY")
    chat_id = os.environ.get("CHAT_ID")
    
    if not telegram_token or not chat_id:
        raise ValueError("Missing Telegram credentials: TELEGRAM_KEY or CHAT_ID")
    
    # Format the message with Markdown
    message = f"""**Competitor Report 📊**

__Reservoir__
wsrUSD APY: {apy_value}"""
    
    url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': message,
        'parse_mode': 'Markdown'
    }
    
    response = requests.post(url, json=payload)
    
    if response.status_code == 200:
        print("✅ Message sent successfully to Telegram")
    else:
        print(f"❌ Failed to send message: {response.status_code} - {response.text}")

async def main():
    print("🚀 Starting Reservoir APY scraper...")
    
    try:
        # Scrape the page
        text = await scrape_reservoir_apy()
        
        # Extract APY
        apy_value = extract_apy(text)
        
        if apy_value:
            # Send to Telegram
            send_telegram_message(apy_value)
        else:
            print("❌ No APY value found, skipping Telegram notification")
            
    except Exception as e:
        print(f"❌ Script failed: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(main())
