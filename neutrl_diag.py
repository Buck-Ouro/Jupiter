"""
Diagnostic only - no Google Sheets access, no writes.

Answers one question: does a GitHub runner + PROXY2_HTTP receive the full
Neutrl page, or the bot-stripped shell?

Reference sizes measured on a real desktop Chrome:
    ~32K   Vercel challenge page
    ~166K  complete HTML, rewards data subtree stripped  (blocked)
    ~466K  full SSR payload, contains "seasonProgram"    (success)
"""

import os
import re
import asyncio
from urllib.parse import urlparse
from tempfile import TemporaryDirectory

from playwright.async_api import async_playwright

REWARDS_URL = "https://app.neutrl.finance/rewards/season-2"
proxy_url = os.environ.get("PROXY2_HTTP")

MARKERS = ["seasonProgram", "totalPoints", "participantCount",
           "queryResult", "totalSupplyUsd", "Security Checkpoint"]


async def main():
    async with async_playwright() as p:
        launch_kwargs = dict(
            headless=True,
            ignore_https_errors=True,
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
            args=['--disable-blink-features=AutomationControlled',
                  '--disable-dev-shm-usage', '--no-sandbox'],
            ignore_default_args=['--enable-automation'],
        )

        if proxy_url:
            parsed = urlparse(proxy_url)
            launch_kwargs["proxy"] = {
                "server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}",
                "username": parsed.username,
                "password": parsed.password,
            }
            print("🔌 Using PROXY2_HTTP")
        else:
            print("⚠️ PROXY2_HTTP not set - going direct from the runner IP.")

        with TemporaryDirectory() as tmp:
            ctx = await p.chromium.launch_persistent_context(user_data_dir=tmp,
                                                             **launch_kwargs)
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
                Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
                window.chrome = {runtime: {}};
            """)
            try:
                try:
                    await page.goto("https://httpbin.org/ip",
                                    wait_until="domcontentloaded", timeout=15000)
                    print(f"🌐 Egress IP: {(await page.inner_text('body')).strip()}")
                except Exception as e:
                    print(f"⚠️ IP check failed (non-critical): {e}")

                print(f"📍 GET {REWARDS_URL}")
                await page.goto(REWARDS_URL, wait_until="networkidle", timeout=90000)
                await page.wait_for_timeout(3000)

                html = await page.content()
                print(f"\n📊 {len(html)} chars   (want ~466K, 166K = blocked)")
                print(f"📄 title: {await page.title()}")
                for mk in MARKERS:
                    print(f"   {mk:22s} -> {'found' if mk in html else 'NOT FOUND'}")

                if "seasonProgram" in html:
                    print("\n✅ SUCCESS - season payload present.")
                    best = None
                    for m in re.finditer(r"ethereum-1-seasonProgram-([A-Za-z0-9_]+)", html):
                        w = html[m.start():m.start() + 600]
                        e = re.search(r'endTimestamp\\?"?\s*:\s*(null|\d+)', w)
                        pt = re.search(r'totalPoints\\?"?\s*:\s*([0-9.eE+-]+)', w)
                        c = re.search(r'participantCount\\?"?\s*:\s*([0-9]+)', w)
                        if not (e and pt and c) or e.group(1) != "null":
                            continue
                        cand = (m.group(1), float(pt.group(1)), int(c.group(1)))
                        if best is None or cand[1] > best[1]:
                            best = cand
                    print(f"   season       : {best[0] if best else None}")
                    print(f"   totalPoints  : {best[1] if best else None}")
                    print(f"   participants : {best[2] if best else None}")
                    sup = re.search(
                        r'\\?"Nusd\\?"[\s\S]{0,600}?totalSupplyUsd\\?"?\s*:\s*([0-9.eE+-]+)',
                        html)
                    print(f"   nusdSupplyUsd: {sup.group(1) if sup else None}")
                else:
                    print("\n❌ BLOCKED - no season payload. Body preview:")
                    print("-" * 60)
                    print((await page.inner_text("body"))[:1200])
                    print("-" * 60)
            finally:
                await ctx.close()


asyncio.run(main())
