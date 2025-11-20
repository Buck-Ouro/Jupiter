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
            await page.goto(
                "https://app.reservoir.xyz/mint?from=rUSD&fromNetwork=Ethereum&to=srUSDv2&toNetwork=Ethereum",
                wait_until="networkidle",
                timeout=60000
            )
            await page.wait_for_timeout(5000)
            content = await page.inner_text("body")

            match = re.search(r'Current APY[:\s]*([\d.]+)%', content, re.IGNORECASE)
            if match:
                return round(float(match.group(1)), 2)
            return None
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
            data = requests.get(url, timeout=10).json()
            apys[key] = round(float(data.get("apy", 0)), 2)
        except:
            apys[key] = None
    return apys

# --- FETCH mHYPER APY ---
def fetch_mhyper_apy():
    url = "https://api-prod.midas.app/api/data/apys"
    try:
        data = requests.get(url, timeout=10).json()
        return round(float(data.get("mhyper", 0)) * 100, 2)
    except:
        return None

# --- FETCH YieldFi APY ---
def fetch_yieldfi_apy():
    urls = {
        "yusd": "https://ctrl.yield.fi/t/apy/yusd/apyHistory",
        "vyusd": "https://ctrl.yield.fi/t/apy/vyusd/apyHistory"
    }
    apys = {}
    for key, url in urls.items():
        try:
            data = requests.get(url, timeout=10).json()
            apys[key] = round(float(data["apy_history"][0]["apy"]), 2)
        except:
            apys[key] = None
    return apys

# --- SCRAPE Infinifi liUSD APY ---
async def scrape_infinifi_liusd():
    async with async_playwright() as p:
        parsed = urlparse(proxy_url)
        proxy_config = {
            "server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}",
            "username": parsed.username,
            "password": parsed.password
        }
        browser = await p.chromium.launch(headless=True, proxy=proxy_config)
        page = await browser.new_page()
        try:
            await page.goto("https://app.infinifi.xyz/lock", wait_until="networkidle", timeout=60000)
            await page.wait_for_timeout(5000)
            content = await page.inner_text("body")

            liusd = {}
            for week in ["1 week", "4 week", "8 week"]:
                pattern = rf"{week}.*?([\d.]+)%"
                match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
                if match:
                    liusd[week] = round(float(match.group(1)), 2)
                else:
                    liusd[week] = None
            return liusd
        finally:
            await browser.close()

# --- FETCH Infinifi siUSD APY ---
async def fetch_infinifi_siusd():
    from urllib.parse import urlparse
    parsed = urlparse(proxy_url)
    proxy_config = {
        "server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}",
        "username": parsed.username,
        "password": parsed.password
    }

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, proxy=proxy_config)
        page = await browser.new_page()
        try:
            response = await page.goto(
                "https://eth-api.infinifi.xyz/api/protocol/data",
                wait_until="networkidle",
                timeout=60000
            )
            data = await response.json()
            siusd_apy = float(data["data"]["staked"]["siUSD"]["average7dAPY"]) * 100
            return round(siusd_apy, 2)
        except Exception as e:
            print("Error fetching siUSD APY:", e)
            return None
        finally:
            await browser.close()

# --- TELEGRAM MESSAGE ---
def send_telegram_message(reservoir_apy, avant_apys, mhyper_apy, yieldfi_apys, infinifi_siusd, infinifi_liusd):
    lines = ["<b>Competitor Report 📊</b>\n"]

    # Reservoir
    lines.append("<u>Reservoir</u>")
    lines.append(f"wsrUSD APY: {reservoir_apy if reservoir_apy is not None else '❌'}%\n")

    # Avant
    lines.append("<u>Avant</u>")
    lines.append(f"savUSD APY (Daily): {avant_apys.get('savusd', '❌')}%")
    lines.append(f"avUSDx APY (Weekly): {avant_apys.get('avusdx', '❌')}%\n")

    # mHYPER
    lines.append("<u>mHyper</u>")
    lines.append(f"mHyper APY (7 Day): {mhyper_apy if mhyper_apy is not None else '❌'}%\n")

    # YieldFi
    lines.append("<u>YieldFi</u>")
    lines.append(f"yUSD APY (7 Day): {yieldfi_apys.get('yusd', '❌')}%")
    lines.append(f"vyUSD APY (7 Day): {yieldfi_apys.get('vyusd', '❌')}%\n")

    # Infinifi
    lines.append("<u>Infinifi</u>")
    lines.append(f"siUSD APY: {infinifi_siusd if infinifi_siusd is not None else '❌'}%")
    lines.append(f"liUSD 1 Week APY: {infinifi_liusd.get('1 week', '❌')}%")
    lines.append(f"liUSD 4 Week APY: {infinifi_liusd.get('4 week', '❌')}%")
    lines.append(f"liUSD 8 Week APY: {infinifi_liusd.get('8 week', '❌')}%")

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
    yieldfi_apys = fetch_yieldfi_apy()
    infinifi_siusd = fetch_infinifi_siusd()
    infinifi_liusd = asyncio.get_event_loop().run_until_complete(scrape_infinifi_liusd())
    send_telegram_message(reservoir_apy, avant_apys, mhyper_apy, yieldfi_apys, infinifi_siusd, infinifi_liusd)
