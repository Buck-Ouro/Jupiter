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

async def apply_stealth_techniques(page):
    await page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => false });

        Object.defineProperty(navigator, 'plugins', {
            get: () => [
                {name: 'Chrome PDF Plugin', description: 'Portable Document Format', filename: 'internal-pdf-viewer'},
                {name: 'Chrome PDF Viewer', description: '', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai'},
                {name: 'Native Client', description: '', filename: 'internal-nacl-plugin'}
            ]
        });

        Object.defineProperty(navigator, 'languages', {
            get: () => ['en-US', 'en']
        });

        window.navigator.chrome = {
            runtime: {},
            loadTimes: function() {},
            csi: function() {},
            app: {}
        };

        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications'
                ? Promise.resolve({ state: Notification.permission })
                : originalQuery(parameters)
        );

        const getParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function(parameter) {
            if (parameter === 37445) return 'Intel Inc.';
            if (parameter === 37446) return 'Intel Iris OpenGL Engine';
            return getParameter.call(this, parameter);
        };

        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
        delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;

        Object.defineProperty(screen, 'availWidth',  { get: () => 1920 });
        Object.defineProperty(screen, 'availHeight', { get: () => 1080 });

        const originalToString = Function.prototype.toString;
        Function.prototype.toString = function() {
            if (this === navigator.permissions.query) {
                return 'function query() { [native code] }';
            }
            return originalToString.call(this);
        };

        Object.defineProperty(navigator, 'connection', {
            get: () => ({ effectiveType: '4g', downlink: 10, rtt: 50 })
        });

        Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
        Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
    """)

def get_realistic_user_agents():
    return [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ]

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

        user_agent = random.choice(get_realistic_user_agents())

        browser = await p.chromium.launch(headless=True, proxy=proxy_config)
        page = await browser.new_page(user_agent=user_agent)

        await apply_stealth_techniques(page)

        try:
            await page.goto("https://app.infinifi.xyz/lock", wait_until="networkidle", timeout=60000)
            await page.wait_for_timeout(5000)

            content = await page.inner_text("body")

            liusd = {}
            for week in ["1 week", "4 week", "8 week"]:
                pattern = rf"{week}.*?([\d.]+)%"
                match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
                liusd[week] = round(float(match.group(1)), 2) if match else None

            return liusd

        finally:
            await browser.close()

# --- FETCH Infinifi siUSD APY ---
async def fetch_infinifi_siusd():
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
            # Fetch the JSON directly from the API endpoint
            await page.goto("https://eth-api.infinifi.xyz/api/protocol/data", wait_until="networkidle", timeout=60000)
            content = await page.content()
            
            # Sometimes inner_text on "body" can truncate JSON, safer to use response text
            response = await page.evaluate("() => document.body.innerText")
            data = json.loads(response)

            # Extract staked average7dAPY
            average7dAPY = data.get("data", {}).get("stats", {}).get("staked", {}).get("average7dAPY")
            if average7dAPY is not None:
                return round(float(average7dAPY) * 100, 2)  # Convert to percentage
            return None
        except Exception as e:
            print(f"❌ Error fetching Infinifi siUSD APY: {e}")
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
    infinifi_siusd = asyncio.get_event_loop().run_until_complete(fetch_infinifi_siusd())
    infinifi_liusd = asyncio.get_event_loop().run_until_complete(scrape_infinifi_liusd())
    send_telegram_message(reservoir_apy, avant_apys, mhyper_apy, yieldfi_apys, infinifi_siusd, infinifi_liusd)
