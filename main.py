import asyncio
import os
import re
import sys
import time
from datetime import datetime
from html import unescape
from zoneinfo import ZoneInfo

import requests
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

SGT = ZoneInfo("Asia/Singapore")
WATCH_URL = os.getenv("WATCH_URL", "https://s.lazada.sg/s.TpxxV?c=s").strip()
POLL_SECONDS = max(8.0, float(os.getenv("POLL_SECONDS", "15")))
PAGE_TIMEOUT_MS = int(os.getenv("PAGE_TIMEOUT_MS", "25000"))
ALERT_COOLDOWN_SECONDS = int(os.getenv("ALERT_COOLDOWN_SECONDS", "180"))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

OUT_OF_STOCK_TEXT = ("out of stock", "sold out", "temporarily unavailable", "currently unavailable")
BLOCK_TEXT = ("captcha", "verify you are human", "access denied", "too many requests", "unusual traffic")

def stamp():
    return datetime.now(SGT).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " SGT"

def tg_api(method, payload=None, timeout=20):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/{method}"
    r = requests.post(url, json=payload or {}, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data}")
    return data

def discover_chat_id():
    if TELEGRAM_CHAT_ID:
        return TELEGRAM_CHAT_ID
    print(f"[{stamp()}] TELEGRAM_CHAT_ID not set. Looking for a message sent to the bot...")
    data = tg_api("getUpdates", {"timeout": 1, "allowed_updates": ["message"]}, timeout=10)
    for update in reversed(data.get("result", [])):
        msg = update.get("message") or {}
        chat_id = (msg.get("chat") or {}).get("id")
        if chat_id is not None:
            print(f"[{stamp()}] Auto-detected Telegram chat ID: {chat_id}")
            return str(chat_id)
    raise RuntimeError("No Telegram message found. Send /start or hello to the bot, then restart the Railway service.")

def send_telegram(chat_id, text):
    tg_api("sendMessage", {"chat_id": chat_id, "text": text, "disable_web_page_preview": False})

async def visible_purchase_signal(page):
    for label in ("Add to Cart", "Buy Now"):
        try:
            loc = page.get_by_role("button", name=re.compile(rf"^\s*{re.escape(label)}\s*$", re.I))
            for i in range(await loc.count()):
                item = loc.nth(i)
                if await item.is_visible() and await item.is_enabled():
                    return label
        except Exception:
            pass

    for label in ("Add to Cart", "Buy Now"):
        try:
            loc = page.get_by_text(label, exact=True)
            for i in range(await loc.count()):
                item = loc.nth(i)
                if not await item.is_visible():
                    continue
                aria_disabled = (await item.get_attribute("aria-disabled") or "").lower()
                classes = (await item.get_attribute("class") or "").lower()
                disabled_attr = await item.get_attribute("disabled")
                if aria_disabled != "true" and "disabled" not in classes and disabled_attr is None:
                    return label
        except Exception:
            pass
    return None

async def classify(page):
    title = (await page.title()).strip()
    try:
        body = await page.locator("body").inner_text(timeout=7000)
    except Exception:
        body = ""
    normalized = " ".join(unescape(body).lower().split())

    for phrase in BLOCK_TEXT:
        if phrase in normalized:
            return "blocked", title, None, phrase

    for phrase in OUT_OF_STOCK_TEXT:
        if phrase in normalized:
            return "out_of_stock", title, None, phrase

    signal = await visible_purchase_signal(page)
    if signal:
        return "in_stock", title, signal, None

    return "unknown", title, None, None

async def run():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing in Railway Variables.")

    chat_id = discover_chat_id()
    send_telegram(
        chat_id,
        "✅ MKALERTS is online\n"
        f"⏱ {stamp()}\n"
        f"Watching: {WATCH_URL}\n"
        f"Check interval: {POLL_SECONDS:g}s",
    )

    previous_state = None
    last_alert = 0.0
    consecutive_errors = 0

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--disable-dev-shm-usage", "--no-sandbox"])
        context = await browser.new_context(
            locale="en-SG",
            timezone_id="Asia/Singapore",
            viewport={"width": 1280, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/127.0.0.0 Safari/537.36",
        )
        page = await context.new_page()
        page.set_default_timeout(PAGE_TIMEOUT_MS)

        while True:
            cycle_started = time.monotonic()
            try:
                if page.url in ("", "about:blank"):
                    await page.goto(WATCH_URL, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)
                else:
                    await page.reload(wait_until="domcontentloaded", timeout=PAGE_TIMEOUT_MS)

                await page.wait_for_timeout(1400)
                state, title, signal, detail = await classify(page)
                resolved_url = page.url
                consecutive_errors = 0

                if state != previous_state:
                    print(f"[{stamp()}] STATE {previous_state!r} -> {state!r} title={title!r} signal={signal!r} detail={detail!r}")
                else:
                    print(f"[{stamp()}] {state} | {title!r}")

                if state == "in_stock":
                    now = time.monotonic()
                    if previous_state != "in_stock" or now - last_alert >= ALERT_COOLDOWN_SECONDS:
                        send_telegram(
                            chat_id,
                            "🚨 STOCK FOUND 🚨\n"
                            f"⏱ {stamp()}\n"
                            f"📦 {title or 'Lazada product'}\n"
                            f"✅ Signal: {signal or 'Purchasable'}\n"
                            f"👉 {resolved_url}",
                        )
                        last_alert = now

                elif state == "blocked" and previous_state != "blocked":
                    send_telegram(
                        chat_id,
                        "⚠️ MKALERTS needs attention\n"
                        f"⏱ {stamp()}\n"
                        "Lazada appears to be showing a traffic/CAPTCHA block.\n"
                        f"👉 {resolved_url}",
                    )

                previous_state = state

            except PlaywrightTimeoutError as e:
                consecutive_errors += 1
                print(f"[{stamp()}] Timeout #{consecutive_errors}: {e}")
            except Exception as e:
                consecutive_errors += 1
                print(f"[{stamp()}] Error #{consecutive_errors}: {type(e).__name__}: {e}")

            delay = POLL_SECONDS
            if consecutive_errors >= 3:
                delay = max(delay, 45)
            if consecutive_errors >= 6:
                delay = max(delay, 120)

            elapsed = time.monotonic() - cycle_started
            await asyncio.sleep(max(1.0, delay - elapsed))

if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        sys.exit(1)
