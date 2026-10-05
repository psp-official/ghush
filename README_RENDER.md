# Telegram Stars Bot — Render deployment

This is a small Render-ready Telegram bot built around the supplied `fragment-stars-api` Python client.

## What it does

Owner-only commands:

- `.star @username 50`
- `.star @username 137`
- `.star @username 500`
- `.price`
- `.wallet`
- `.status`
- `.help`

Stars amount must be an integer from 50 to 1,000,000.

The bot uses a Telegram webhook, so Render can host it as a Web Service.

## Security warning

The upstream client used here requires a wallet seed for its purchase request. The bot therefore reads `FRAGMENT_WALLET_SEED` from Render Environment Variables and passes it to the configured Fragment API endpoint. This is sensitive financial credential material.

- Never put the seed in source code.
- Never send the seed to Telegram.
- Never commit `.env`.
- Use a dedicated payment wallet with only the amount you are willing to risk.
- `ENABLE_PURCHASES=false` is the safe default. Turn it on only after testing.
- If a seed has been exposed, move funds to a fresh wallet before using it for production.

This package does not collect or display a user's recovery phrase.

## Render setup

### 1. Create the Telegram bot

Use Telegram's BotFather and copy the bot token.

### 2. Upload this project to GitHub

Create a new GitHub repository and upload all files in this folder.

Do NOT upload `.env` or any real seed/private key.

### 3. Create a Render Web Service

In Render:

1. New → Web Service
2. Select your GitHub repository
3. Runtime: Python
4. Build Command:

```bash
pip install -r requirements.txt
```

5. Start Command:

```bash
uvicorn bot:app --host 0.0.0.0 --port $PORT
```

6. Deploy.

### 4. Add Render Environment Variables

Add these variables in Render → Environment:

```text
TELEGRAM_BOT_TOKEN=your_bot_token
OWNER_TELEGRAM_ID=your_numeric_telegram_user_id
PUBLIC_URL=https://YOUR-SERVICE.onrender.com
TELEGRAM_WEBHOOK_SECRET=a-long-random-secret
FRAGMENT_WALLET_SEED=your_base64_wallet_seed
FRAGMENT_WALLET_ADDRESS=your_public_ton_address
FRAGMENT_ACCOUNT_INDEX=0
PAYMENT_METHOD=ton
FRAGMENT_API_URL=https://api.fragment-api.space
ENABLE_PURCHASES=false
MAX_STARS=1000000
```

`PUBLIC_URL` must be the exact HTTPS URL of your Render service, with no trailing slash.

### 5. Deploy again

On startup the app calls Telegram `setWebhook` with:

```text
https://YOUR-SERVICE.onrender.com/telegram/webhook
```

Check Render logs. You should see the service start without errors.

### 6. Test health

Open:

```text
https://YOUR-SERVICE.onrender.com/health
```

You should receive JSON similar to:

```json
{"ok":true,"service":"telegram-stars-bot"}
```

### 7. Keep purchases disabled while testing

With:

```text
ENABLE_PURCHASES=false
```

`.star` will not spend money.

Test `.help`, `.status`, `.wallet`, and `.price` first.

### 8. Enable real purchases

Only after the configuration is correct, change:

```text
ENABLE_PURCHASES=true
```

Then redeploy.

Now the owner can send:

```text
.star @username 500
```

The bot checks the amount, starts the purchase in the background, and sends the final result including transaction ID/hash when the upstream API returns one.

## Important: OWNER_TELEGRAM_ID

The bot is intentionally owner-only for `.star` because the payment wallet belongs to the bot owner. Do not make the purchase command public unless you add your own authentication, billing, and abuse controls.

## Wallet seed format

The supplied SDK documentation says a 12-word BIP39 seed should be Base64-encoded before being sent to the API. Do not paste the recovery phrase into the bot or source code. Generate the Base64 value locally and put only the resulting value into Render's secret environment variable.

Example local conversion (do not run this in Telegram):

```python
import base64
seed = "word1 word2 ... word12"
print(base64.b64encode(seed.encode()).decode())
```

Never paste the real seed into this README or GitHub.

## Limitations

- This bot does not implement TON balance lookup. The upstream purchase API is responsible for validating wallet funds.
- The bot does not use Tonkeeper/Ton Connect popups. It uses the server-side payment credential required by the upstream API.
- The upstream API is a third-party service and may change availability, pricing, limits, or behavior.
- Render free instances may sleep. For a production payment bot, use an always-on service and monitor failed jobs.
