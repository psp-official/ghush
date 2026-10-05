import html
import os
import re
import threading
from typing import Optional

import requests
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from fragment_api import FragmentAPIClient

app = FastAPI(title="Telegram Stars Bot", version="1.0.0")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
OWNER_ID = os.getenv("OWNER_TELEGRAM_ID", "").strip()
PUBLIC_URL = os.getenv("PUBLIC_URL", "").strip().rstrip("/")
WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()
FRAGMENT_BASE_URL = os.getenv("FRAGMENT_API_URL", "https://api.fragment-api.space").strip().rstrip("/")
WALLET_SEED = os.getenv("FRAGMENT_WALLET_SEED", "").strip()
WALLET_ADDRESS = os.getenv("FRAGMENT_WALLET_ADDRESS", "").strip() or None
ACCOUNT_INDEX_RAW = os.getenv("FRAGMENT_ACCOUNT_INDEX", "").strip()
PAYMENT_METHOD = os.getenv("PAYMENT_METHOD", "ton").strip()
ENABLE_PURCHASES = os.getenv("ENABLE_PURCHASES", "false").lower() == "true"
MAX_STARS = int(os.getenv("MAX_STARS", "1000000"))

ACCOUNT_INDEX: Optional[int] = int(ACCOUNT_INDEX_RAW) if ACCOUNT_INDEX_RAW else None

_purchase_lock = threading.Lock()


def tg(method: str, payload: dict) -> dict:
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
        json=payload,
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(str(data))
    return data


def send_message(chat_id: int, text: str) -> None:
    tg("sendMessage", {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})


def is_owner(user_id: int) -> bool:
    return bool(OWNER_ID) and str(user_id) == OWNER_ID


def normalize_username(value: str) -> str:
    value = value.strip()
    if not value.startswith("@"):
        value = "@" + value
    if not re.fullmatch(r"@[A-Za-z0-9_]{5,32}", value):
        raise ValueError("Username မမှန်ပါ။ ဥပမာ @username")
    return value


def parse_star_command(text: str):
    # Supports: .star @username 500  and  /star @username 500
    parts = text.strip().split()
    if len(parts) != 3 or parts[0].lower() not in {".star", "/star"}:
        return None
    username = normalize_username(parts[1])
    try:
        amount = int(parts[2])
    except ValueError as exc:
        raise ValueError("Stars amount က integer ဖြစ်ရပါမယ်။") from exc
    if amount < 50:
        raise ValueError("အနည်းဆုံး 50 Stars ကနေ စဝယ်နိုင်ပါတယ်။")
    if amount > MAX_STARS:
        raise ValueError(f"အများဆုံး {MAX_STARS:,} Stars အထိပဲ ခွင့်ပြုထားပါတယ်။")
    return username, amount


def format_result(username: str, amount: int, result) -> str:
    if getattr(result, "success", False):
        tx = getattr(result, "transaction_hash", None) or getattr(result, "transaction_id", None) or "N/A"
        cost = getattr(result, "cost_ton", None)
        cost_text = f"\n💰 Cost: <code>{html.escape(str(cost))} TON</code>" if cost else ""
        return (
            f"✅ <b>Stars ဝယ်ပြီးပါပြီ</b>\n\n"
            f"👤 Recipient: <code>{html.escape(username)}</code>\n"
            f"⭐ Amount: <b>{amount:,}</b> Stars"
            f"{cost_text}\n"
            f"🔗 TX: <code>{html.escape(str(tx))}</code>"
        )
    return f"❌ Purchase မအောင်မြင်ပါ။\n\n<code>{html.escape(str(getattr(result, 'error', 'Unknown error')))}</code>"


def purchase_job(chat_id: int, username: str, amount: int) -> None:
    if not _purchase_lock.acquire(blocking=False):
        send_message(chat_id, "⏳ အခြား purchase တစ်ခု လုပ်နေပါတယ်။ ခဏစောင့်ပြီး ထပ်စမ်းပါ။")
        return
    try:
        client = FragmentAPIClient(base_url=FRAGMENT_BASE_URL, poll_timeout=300)
        result = client.buy_stars(
            username=username,
            amount=amount,
            seed=WALLET_SEED,
            payment_method=PAYMENT_METHOD,
            wait=True,
            wallet_address=WALLET_ADDRESS,
            account_index=ACCOUNT_INDEX,
        )
        send_message(chat_id, format_result(username, amount, result))
        client.close()
    except Exception as exc:
        send_message(chat_id, f"❌ Purchase error:\n<code>{html.escape(str(exc))}</code>")
    finally:
        _purchase_lock.release()


@app.get("/")
def root():
    return {"ok": True, "service": "telegram-stars-bot"}


@app.get("/health")
def health():
    return {
        "ok": True,
        "purchase_enabled": ENABLE_PURCHASES,
        "fragment_api": FRAGMENT_BASE_URL,
    }


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks, x_telegram_bot_api_secret_token: Optional[str] = Header(default=None)):
    if WEBHOOK_SECRET and x_telegram_bot_api_secret_token != WEBHOOK_SECRET:
        raise HTTPException(status_code=403, detail="Invalid webhook secret")

    update = await request.json()
    message = update.get("message") or update.get("edited_message")
    if not message:
        return JSONResponse({"ok": True})

    chat = message.get("chat") or {}
    user = message.get("from") or {}
    chat_id = chat.get("id")
    user_id = user.get("id")
    text = (message.get("text") or "").strip()
    if not chat_id or not user_id or not text:
        return JSONResponse({"ok": True})

    if text in {"/start", ".start", "/help", ".help"}:
        send_message(
            chat_id,
            "🤖 <b>Telegram Stars Bot</b>\n\n"
            "အသုံးပြုရန်:\n<code>.star @username 500</code>\n\n"
            "အနည်းဆုံး 50 Stars ပါ။",
        )
        return JSONResponse({"ok": True})

    if text.lower() in {".wallet", "/wallet"}:
        if not is_owner(user_id):
            send_message(chat_id, "⛔ ဒီ command ကို owner ပဲ အသုံးပြုနိုင်ပါတယ်။")
        else:
            send_message(chat_id, f"💳 Payment wallet:\n<code>{html.escape(WALLET_ADDRESS or 'Not configured')}</code>")
        return JSONResponse({"ok": True})

    if text.lower() in {".price", "/price"}:
        try:
            client = FragmentAPIClient(base_url=FRAGMENT_BASE_URL)
            prices = client.get_prices()
            client.close()
            send_message(chat_id, f"💵 Current API prices:\n<pre>{html.escape(str(prices))}</pre>")
        except Exception as exc:
            send_message(chat_id, f"❌ Price error:\n<code>{html.escape(str(exc))}</code>")
        return JSONResponse({"ok": True})

    if text.lower() in {".status", "/status"}:
        if not is_owner(user_id):
            send_message(chat_id, "⛔ ဒီ command ကို owner ပဲ အသုံးပြုနိုင်ပါတယ်။")
        else:
            send_message(chat_id, f"⚙️ Purchases: <b>{'ON' if ENABLE_PURCHASES else 'OFF'}</b>\n💳 Wallet: <code>{html.escape(WALLET_ADDRESS or 'Not configured')}</code>")
        return JSONResponse({"ok": True})

    if text.lower().startswith((".star", "/star")):
        if not is_owner(user_id):
            send_message(chat_id, "⛔ ဒီ Bot ရဲ့ payment command ကို owner ပဲ အသုံးပြုနိုင်ပါတယ်။")
            return JSONResponse({"ok": True})
        if not ENABLE_PURCHASES:
            send_message(chat_id, "🛑 Purchase ကို လောလောဆယ် ပိတ်ထားပါတယ်။ Render Environment မှာ ENABLE_PURCHASES=true ထည့်ပြီး redeploy လုပ်ပါ။")
            return JSONResponse({"ok": True})
        if not WALLET_SEED:
            send_message(chat_id, "❌ FRAGMENT_WALLET_SEED မသတ်မှတ်ရသေးပါ။")
            return JSONResponse({"ok": True})
        try:
            parsed = parse_star_command(text)
            if parsed is None:
                raise ValueError("အသုံးပြုပုံ: .star @username 500")
            username, amount = parsed
        except ValueError as exc:
            send_message(chat_id, f"❌ {html.escape(str(exc))}")
            return JSONResponse({"ok": True})

        send_message(chat_id, f"⏳ <b>Purchase စနေပါပြီ...</b>\n👤 {html.escape(username)}\n⭐ {amount:,} Stars")
        background_tasks.add_task(purchase_job, chat_id, username, amount)
        return JSONResponse({"ok": True})

    return JSONResponse({"ok": True})


@app.on_event("startup")
def configure_webhook():
    if not BOT_TOKEN or not PUBLIC_URL:
        return
    url = f"{PUBLIC_URL}/telegram/webhook"
    payload = {"url": url}
    if WEBHOOK_SECRET:
        payload["secret_token"] = WEBHOOK_SECRET
    try:
        tg("setWebhook", payload)
    except Exception as exc:
        print(f"Webhook setup failed: {exc}")
