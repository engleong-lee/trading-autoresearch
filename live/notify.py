"""
notify.py — Telegram broadcast + subscriber auto-capture.

A Telegram bot can only message users who have pressed Start on it. We capture
chat_ids via getUpdates on every run and store them in telegram_subscribers, then
broadcast to all active subscribers. Long messages are chunked to Telegram's limit.
"""

import logging
import time

import requests

from live.config import TELEGRAM_BOT_TOKEN, TELEGRAM_SEED_CHAT_IDS
from live import db

log = logging.getLogger("trader.notify")

API = "https://api.telegram.org/bot{token}/{method}"
MAX_LEN = 3900  # Telegram hard limit is 4096; leave headroom


def _api(method, **params):
    if not TELEGRAM_BOT_TOKEN:
        log.warning("TELEGRAM_BOT_TOKEN not set — skipping Telegram %s", method)
        return None
    url = API.format(token=TELEGRAM_BOT_TOKEN, method=method)
    try:
        resp = requests.post(url, json=params, timeout=20)
        data = resp.json()
        if not data.get("ok"):
            log.warning("Telegram %s failed: %s", method, data)
        return data
    except Exception as e:
        log.error("Telegram %s error: %s", method, e)
        return None


def sync_subscribers(conn):
    """Poll getUpdates and upsert any chat_ids that have messaged the bot."""
    db.ensure_subscriber_table(conn)
    # Seed chat ids from config (idempotent)
    for cid in [c.strip() for c in TELEGRAM_SEED_CHAT_IDS.split(",") if c.strip()]:
        try:
            db.upsert_subscriber(conn, int(cid), username="(seed)")
        except ValueError:
            pass

    data = _api("getUpdates", timeout=0)
    if not data or not data.get("ok"):
        return
    for upd in data.get("result", []):
        msg = upd.get("message") or upd.get("edited_message") or upd.get("channel_post")
        if not msg:
            continue
        chat = msg.get("chat", {})
        cid = chat.get("id")
        if cid is None:
            continue
        username = chat.get("username")
        first = chat.get("first_name") or chat.get("title")
        db.upsert_subscriber(conn, cid, username, first)
        log.info("Registered/updated Telegram subscriber: %s (%s)", cid, username)


def broadcast(conn, text):
    """Send `text` to all active subscribers (Markdown, chunked)."""
    subs = db.get_active_subscribers(conn)
    if not subs:
        log.warning("No Telegram subscribers to notify.")
        return 0
    chunks = _chunk(text)
    sent = 0
    for cid in subs:
        ok = True
        for part in chunks:
            data = _api("sendMessage", chat_id=cid, text=part,
                        parse_mode="Markdown", disable_web_page_preview=True)
            if data and data.get("ok"):
                continue
            # 403 => user blocked the bot; deactivate
            if data and data.get("error_code") == 403:
                db.deactivate_subscriber(conn, cid)
            ok = False
            time.sleep(0.3)
        if ok:
            sent += 1
        time.sleep(0.2)
    log.info("Telegram broadcast sent to %d/%d subscribers", sent, len(subs))
    return sent


def _chunk(text, size=MAX_LEN):
    if len(text) <= size:
        return [text]
    parts, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > size:
            parts.append(cur)
            cur = ""
        cur += line + "\n"
    if cur:
        parts.append(cur)
    return parts
