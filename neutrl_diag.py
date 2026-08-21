"""
Diagnostic only - no Google Sheets access, no writes.

Stage 1 first run failed with net::ERR_TUNNEL_CONNECTION_FAILED on BOTH the
httpbin IP check and the target - i.e. the CONNECT to the proxy itself failed,
which is upstream of any Vercel bot-block. This version isolates the cause:

  1. Safely prints the PARSED structure of each proxy secret (scheme, port,
     whether user/pass are present) WITHOUT printing host or credentials.
  2. A/B tests a plain tunnel (httpbin.org/ip) through PROXY2_HTTP and, for
     comparison, the old PROXY_HTTP - to tell whether PROXY2_HTTP specifically
     is broken/misformatted vs the runner egress being refused everywhere.
  3. Only if a tunnel succeeds does it try the real Neutrl page.

Reference sizes on a real desktop Chrome:
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

MARKERS = ["seasonProgram", "totalPoints", "participantCount",
           "queryResult", "totalSupplyUsd", "Security Checkpoint"]


def describe(name):
    """Report a proxy secret's parsed shape without leaking host/creds."""
    raw = os.environ.get(name)
    if not raw:
        print(f"   {name}: NOT SET")
        return None
    p = urlparse(raw)
    has_scheme = bool(p.scheme)
    print(f"   {name}: set, len={len(raw)}, scheme={p.scheme or '(none)'}, "
          f"host_present={bool(p.hostname)}, port={p.port}, "
          f"user_present={bool(p.username)}, pass_present={bool(p.password)}")
    if not (has_scheme and p.hostname and p.port):
        print(f"      ^ WARNING: {name} does not parse as scheme://user:pass@host:port")
    return raw


def proxy_dict(raw):
    p = urlparse(raw)
    return {
        "server": f"{p.scheme}://{p.hostname}:{p.port}",
        "username": p.username,
        "password": p.password,
    }


async def tunnel_test(p, label, raw):
    """Return egress IP text if the tunnel works, else None."""
    if not raw:
        return None
    kwargs = dict(
        headless=True, ignore_https_errors=True,
        viewport={"width": 1920, "height": 1080}, locale="en-US",
        user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
        args=['--disable-blink-features=AutomationControlled',
              '--disable-dev-shm-usage', '--no-sandbox'],
        ignore_default_args=['--enable-automation'],
        proxy=proxy_dict(raw),
    )
    with TemporaryDirectory() as tmp:
        ctx = await p.chromium.launch_persistent_context(user_data_dir=tmp, **kwargs)
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            try:
                await page.goto("https://httpbin.org/ip",
                                wait_until="domcontentloaded", timeout=20000)
                ip = (await page.inner_text("body")).strip().replace("\n", " ")
                print(f"   [{label}] tunnel OK -> egress {ip}")
                return raw
            except Exception as e:
                print(f"   [{label}] tunnel FAILED: {str(e).splitlines()[0]}")
                return None
        finally:
            await ctx.close()


async def fetch_page(p, raw):
    kwargs = dict(
        headless=True, ignore_https_errors=True,
        viewport={"width": 1920, "height": 1080}, locale="en-US",
        user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
        args=['--disable-blink-features=AutomationControlled',
              '--disable-dev-shm-usage', '--no-sandbox'],
        ignore_default_args=['--enable-automation'],
        proxy=proxy_dict(raw),
    )
    with TemporaryDirectory() as tmp:
        ctx = await p.chromium.launch_persistent_context(user_data_dir=tmp, **kwargs)
        try:
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
                Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
                window.chrome = {runtime: {}};
            """)
            print(f"\n📍 GET {REWARDS_URL}")
            await page.goto(REWARDS_URL, wait_until="networkidle", timeout=90000)
            await page.wait_for_timeout(3000)
            html = await page.content()
            print(f"📊 {len(html)} chars   (want ~466K, 166K = blocked)")
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


async def main():
    print("🔎 Proxy secret shapes (no host/creds shown):")
    raw2 = describe("PROXY2_HTTP")
    raw1 = describe("PROXY_HTTP")

    async with async_playwright() as p:
        print("\n🧪 Tunnel A/B (httpbin.org/ip):")
        working = await tunnel_test(p, "PROXY2_HTTP", raw2)
        old_ok = await tunnel_test(p, "PROXY_HTTP", raw1)

        if working:
            print("\n➡️  PROXY2_HTTP tunnels - fetching the real page through it.")
            await fetch_page(p, working)
        elif old_ok:
            print("\n⚠️  PROXY2_HTTP tunnel failed but the OLD PROXY_HTTP works.")
            print("    => The PROXY2_HTTP secret itself is the problem "
                  "(wrong creds/port/host or the endpoint is down).")
            print("    Fetching the real page through PROXY_HTTP only to confirm "
                  "the runner egress can reach Neutrl at all:")
            await fetch_page(p, old_ok)
        else:
            print("\n❌ Neither proxy tunnels from this runner. Egress to the "
                  "proxy provider is being refused (ERR_TUNNEL_CONNECTION_FAILED). "
                  "This is a proxy connectivity/credential problem, not a Vercel "
                  "bot-block. Stopping.")


asyncio.run(main())
