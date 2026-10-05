import base64
import html
import os
import re
import threading
from typing import Optional

import requests
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from fragment_api import FragmentAPIClient, FragmentAPIError

app = FastAPI(title="Telegram Stars Bot", version="2.0-fixed")

# Telegram settings (both old and new variable names are accepted)
BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") or "").strip()
OWNER_ID = (os.getenv("OWNER_TELEGRAM_ID") or os.getenv("ADMIN_USER_ID") or "").strip()
PUBLIC_URL = os.getenv("PUBLIC_URL", "").strip().rstrip("/")
WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "").strip()

# Fragment settings
FRAGMENT_BASE_URL = os.getenv("FRAGMENT_API_URL", "https://api.fragment-api.space").strip().rstrip("/")
# Accept both names used by the two compared ZIPs.
RAW_SEED = (os.getenv("FRAGMENT_WALLET_SEED") or os.getenv("FRAGMENT_WALLET_MNEMONIC") or "").strip()
PAYMENT_METHOD = (os.getenv("FRAGMENT_PAYMENT_METHOD") or os.getenv("PAYMENT_METHOD") or "ton").strip().lower()
API_MODE = os.getenv("FRAGMENT_API_MODE", "no_kyc").strip().lower()
COOKIES_BASE64 = os.getenv("FRAGMENT_COOKIES_BASE64", "").strip() or None
WALLET_ADDRESS = os.getenv("FRAGMENT_WALLET_ADDRESS", "").strip() or None
ACCOUNT_INDEX_RAW = os.getenv("FRAGMENT_ACCOUNT_INDEX", "").strip()
ENABLE_PURCHASES = os.getenv("ENABLE_PURCHASES", "false").strip().lower() == "true"
MAX_STARS = int(os.getenv("MAX_STARS", "1000000"))

if API_MODE not in {"kyc", "no_kyc"}:
    API_MODE = "no_kyc"
if PAYMENT_METHOD not in {"ton", "usdt_ton"}:
    PAYMENT_METHOD = "ton"

ACCOUNT_INDEX: Optional[int] = None
if ACCOUNT_INDEX_RAW:
    ACCOUNT_INDEX = int(ACCOUNT_INDEX_RAW)
    if ACCOUNT_INDEX < 0:
        raise RuntimeError("FRAGMENT_ACCOUNT_INDEX must be >= 0")

_purchase_lock = threading.Lock()


def normalize_seed_for_api(seed: str) -> str:
    """Return the complete 12/24-word mnemonic as Base64, without logging it."""
    seed = seed.strip()
    if not seed:
        raise RuntimeError("FRAGMENT_WALLET_SEED / FRAGMENT_WALLET_MNEMONIC is empty")

    # If it is already Base64 containing a 12/24-word mnemonic, preserve it.
    try:
        decoded = base64.b64decode(seed, validate=True).decode("utf-8").strip()
        if len(decoded.split()) in {12, 24}:
            return seed
    except Exception:
        pass

    # Otherwise treat the environment value as the raw mnemonic and encode it.
    words = seed.split()
    if len(words) not in {12, 24}:
        raise RuntimeError(
            "Wallet seed must be a complete 12-word or 24-word mnemonic, "
            "or its Base64 encoding."
        )
    return base64.b64encode(seed.encode("utf-8")).decode("ascii")


def get_seed() -> str:
    return normalize_seed_for_api(RAW_SEED)


def tg(method: str, payload: dict) -> dict:
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN/BOT_TOKEN is not configured")
    response = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(str(data))
    return data


def send_message(chat_id: int, text: str) -> None:
    tg(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
    )


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


def purchase_job(chat_id: int, username: str, amount: int) -> None:
    if not _purchase_lock.acquire(blocking=False):
        send_message(chat_id, "⏳ အခြား purchase တစ်ခု လုပ်နေပါတယ်။ ခဏစောင့်ပြီး ထပ်စမ်းပါ။")
        return

    client = None
    try:
        seed = get_seed()
        client = FragmentAPIClient(base_url=FRAGMENT_BASE_URL, poll_timeout=300)

        # IMPORTANT FIX:
        # Do not send wallet_address/account_index unless the operator explicitly
        # configured them. This avoids forcing a wrong selector into the request.
        kwargs = {
            "username": username,
            "amount": amount,
            "seed": seed,
            "payment_method": PAYMENT_METHOD,
            "wait": True,
        }
        if API_MODE == "kyc" and COOKIES_BASE64:
            kwargs["cookies"] = COOKIES_BASE64
        if WALLET_ADDRESS:
            kwargs["wallet_address"] = WALLET_ADDRESS
        if ACCOUNT_INDEX is not None:
            kwargs["account_index"] = ACCOUNT_INDEX

        result = client.buy_stars(**kwargs)
        success = getattr(result, "success", None)
        if success is False:
            error = getattr(result, "error", None) or "Fragment API returned success=false"
            raise RuntimeError(str(error))

        tx = (
            getattr(result, "transaction_hash", None)
            or getattr(result, "transaction_id", None)
            or getattr(result, "request_id", None)
            or getattr(result, "id", None)
            or "N/A"
        )
        send_message(
            chat_id,
            "✅ <b>Stars ဝယ်ပြီးပါပြီ</b>\n\n"
            f"👤 Recipient: <code>{html.escape(username)}</code>\n"
            f"⭐ Amount: <b>{amount:,}</b> Stars\n"
            f"🔗 TX/Request: <code>{html.escape(str(tx))}</code>",
        )
    except FragmentAPIError as exc:
        send_message(chat_id, f"❌ Purchase error:\n<code>{html.escape(str(exc))}</code>")
    except Exception as exc:
        # Never include seed, cookies, or other secret environment values here.
        send_message(chat_id, f"❌ Purchase error:\n<code>{html.escape(str(exc))}</code>")
    finally:
        try:
            if client:
                client.close()
        except Exception:
            pass
        _purchase_lock.release()


@app.get("/")
def root():
    return {"ok": True, "service": "telegram-stars-bot", "version": "2.0-fixed"}


@app.get("/health")
def health():
    return {
        "ok": True,
        "purchase_enabled": ENABLE_PURCHASES,
        "fragment_api": FRAGMENT_BASE_URL,
        "api_mode": API_MODE,
        "payment_method": PAYMENT_METHOD,
        "wallet_selector": "address+index" if WALLET_ADDRESS and ACCOUNT_INDEX is not None else "address" if WALLET_ADDRESS else "index" if ACCOUNT_INDEX is not None else "seed-only",
        "seed_configured": bool(RAW_SEED),
    }


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_telegram_bot_api_secret_token: Optional[str] = Header(default=None),
):
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

    if text.lower() in {"/start", ".start", "/help", ".help"}:
        send_message(
            chat_id,
            "🤖 <b>Telegram Stars Bot</b>\n\n"
            "အသုံးပြုရန်:\n<code>.star @username 50</code>\n\n"
            "အနည်းဆုံး 50 Stars ပါ။",
        )
        return JSONResponse({"ok": True})

    if text.lower() in {".status", "/status"}:
        if not is_owner(user_id):
            send_message(chat_id, "⛔ Owner only")
        else:
            send_message(
                chat_id,
                "⚙️ <b>Status</b>\n"
                f"Purchases: <b>{'ON' if ENABLE_PURCHASES else 'OFF'}</b>\n"
                f"API mode: <b>{html.escape(API_MODE)}</b>\n"
                f"Payment: <b>{html.escape(PAYMENT_METHOD)}</b>\n"
                f"Wallet selector: <b>{'configured' if (WALLET_ADDRESS or ACCOUNT_INDEX is not None) else 'seed-only'}</b>",
            )
        return JSONResponse({"ok": True})

    if text.lower() in {".price", "/price"}:
        if not is_owner(user_id):
            send_message(chat_id, "⛔ Owner only")
            return JSONResponse({"ok": True})
        client = None
        try:
            client = FragmentAPIClient(base_url=FRAGMENT_BASE_URL)
            prices = client.get_prices()
            send_message(chat_id, f"💵 <b>Current API prices</b>\n<pre>{html.escape(str(prices))}</pre>")
        except Exception as exc:
            send_message(chat_id, f"❌ Price error:\n<code>{html.escape(str(exc))}</code>")
        finally:
            try:
                if client:
                    client.close()
            except Exception:
                pass
        return JSONResponse({"ok": True})

    if text.lower().startswith((".star", "/star")):
        if not is_owner(user_id):
            send_message(chat_id, "⛔ ဒီ Bot ရဲ့ payment command ကို owner ပဲ အသုံးပြုနိုင်ပါတယ်။")
            return JSONResponse({"ok": True})
        if not ENABLE_PURCHASES:
            send_message(chat_id, "🛑 Purchase ပိတ်ထားပါတယ်။ Render မှာ ENABLE_PURCHASES=true ထည့်ပြီး redeploy လုပ်ပါ။")
            return JSONResponse({"ok": True})
        if not RAW_SEED:
            send_message(chat_id, "❌ FRAGMENT_WALLET_SEED / FRAGMENT_WALLET_MNEMONIC မသတ်မှတ်ရသေးပါ။")
            return JSONResponse({"ok": True})
        try:
            parsed = parse_star_command(text)
            if parsed is None:
                raise ValueError("အသုံးပြုပုံ: .star @username 50")
            username, amount = parsed
            # Validate/normalize the seed before starting the real purchase.
            get_seed()
        except (ValueError, RuntimeError) as exc:
            send_message(chat_id, f"❌ {html.escape(str(exc))}")
            return JSONResponse({"ok": True})

        send_message(
            chat_id,
            "⏳ <b>Purchase စနေပါပြီ...</b>\n"
            f"👤 {html.escape(username)}\n"
            f"⭐ {amount:,} Stars",
        )
        background_tasks.add_task(purchase_job, chat_id, username, amount)
        return JSONResponse({"ok": True})

    return JSONResponse({"ok": True})


@app.on_event("startup")
def configure_webhook():
    if not BOT_TOKEN or not PUBLIC_URL:
        return
    payload = {"url": f"{PUBLIC_URL}/telegram/webhook"}
    if WEBHOOK_SECRET:
        payload["secret_token"] = WEBHOOK_SECRET
    try:
        tg("setWebhook", payload)
    except Exception as exc:
        print(f"Webhook setup failed: {exc}")
