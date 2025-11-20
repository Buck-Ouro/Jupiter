import os
import asyncio
import re
import json
from urllib.parse import urlparse
from playwright.async_api import async_playwright
import nest_asyncio
import requests

nest_asyncio.apply()

# --- CONFIG ---
proxy_url = os.environ.get("PROXY_HTTP")
telegram_key = os.environ.get("TELEGRAM_KEY")
chat_id = os.environ.get("CHAT_ID")

if not proxy_url or not telegram_key or not chat_id:
    raise ValueError("Missing environment variables: PROXY_HTTP, TELEGRAM_KEY, or CHAT_ID")

# --- SCRAPER FOR RESERVOIR ---
async def scrape_reservoir_apy():
    async with async_playwright() as p:
        parsed = urlparse(proxy_url)
        proxy_config = {
            "server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}",
            "username": parsed.username,
            "password": parsed.password
        }

        browser = await p.chromium.launch(headless=True, proxy=proxy_config)
        page = await browser.new_page()

        # Stealth
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
            window.chrome = {runtime: {}};
        """)

        try:
            print("🌐 Visiting Reservoir mint page...")
            await page.goto(
                "https://app.reservoir.xyz/mint?from=rUSD&fromNetwork=Ethereum&to=srUSDv2&toNetwork=Ethereum",
                wait_until="networkidle",
                timeout=60000
            )
            await page.wait_for_timeout(5000)

            # Grab all page text
            content = await page.inner_text("body")

            # Extract Current APY (dynamic pattern: e.g., 3%, 3.5%, 3.55%)
            match = re.search(r'Current APY[:\s]*([\d.]+)%', content, re.IGNORECASE)
            if match:
                apy_value = round(float(match.group(1)), 2)
                print(f"✅ Found Reservoir Current APY: {apy_value}%")
            else:
                apy_value = None
                print("❌ Could not find Reservoir APY.")

            return apy_value

        finally:
            await browser.close()

# --- FETCH AVANT APY ---
def fetch_avant_apy():
    urls = {
        "savusd": "https://app.avantprotocol.com/api/apy/savusd",
        "avusdx": "https://app.avantprotocol.com/api/apy/avusdx"
    }
    apys = {}
    for key, url in urls.items():
        try:
            resp = requests.get(url, timeout=10)
            data = resp.json()
            apys[key] = round(float(data.get("apy", 0)), 2)
            print(f"✅ Avant {key} APY: {apys[key]}%")
        except Exception as e:
            apys[key] = None
            print(f"❌ Error fetching Avant {key}: {e}")
    return apys

# --- FETCH mHYPER APY ---
def fetch_mhyper_apy():
    url = "https://api-prod.midas.app/api/data/apys"
    try:
        resp = requests.get(url, timeout=10)
        data = resp.json()
        mhyper_apy = round(float(data.get("mhyper", 0)) * 100, 2)  # Convert to %
        print(f"✅ mHYPER APY: {mhyper_apy}%")
        return mhyper_apy
    except Exception as e:
        print(f"❌ Error fetching mHYPER APY: {e}")
        return None

# --- TELEGRAM MESSAGE ---
def send_telegram_message(reservoir_apy, avant_apys, mhyper_apy):
    lines = ["<b>Competitor Report 📊</b>\n"]

    # Reservoir
    if reservoir_apy is not None:
        lines.append("<u>Reservoir</u>")
        lines.append(f"wsrUSD APY: {reservoir_apy}%\n")
    else:
        lines.append("<u>Reservoir</u>")
        lines.append("wsrUSD APY: ❌ Not found\n")

    # Avant
    lines.append("<u>Avant</u>")
    savusd = f"{avant_apys.get('savusd', '❌')}%"
    avusdx = f"{avant_apys.get('avusdx', '❌')}%"
    lines.append(f"savUSD APY (Daily): {savusd}")
    lines.append(f"avUSDx APY (Weekly): {avusdx}\n")

    # mHYPER
    lines.append("<u>mHyper</u>")
    mh = f"{mhyper_apy if mhyper_apy is not None else '❌'}%"
    lines.append(f"mHyper APY (7 Day): {mh}")

    message = "\n".join(lines)

    url = f"https://api.telegram.org/bot{telegram_key}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML"
    }
    resp = requests.post(url, json=payload)
    if resp.status_code == 200:
        print("✅ Telegram message sent successfully!")
    else:
        print(f"❌ Telegram error: {resp.status_code} {resp.text}")

# --- MAIN ---
if __name__ == "__main__":
    reservoir_apy = asyncio.get_event_loop().run_until_complete(scrape_reservoir_apy())
    avant_apys = fetch_avant_apy()
    mhyper_apy = fetch_mhyper_apy()
    send_telegram_message(reservoir_apy, avant_apys, mhyper_apy)
