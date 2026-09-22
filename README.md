# MKALERTS

Railway-ready personal Lazada SG Pokémon restock monitor.

## Phone-only setup

1. Upload `main.py`, `requirements.txt`, `Dockerfile`, and `.gitignore` to your GitHub repo.
2. In Railway, create a project and choose **Deploy from GitHub repo**.
3. Select your `-mk-restock-alert` repo.
4. In the Railway service, open **Variables** and add:
   - `TELEGRAM_BOT_TOKEN` = your BotFather token
5. Deploy.

The script already watches:
`https://s.lazada.sg/s.TpxxV?c=s`

It checks every 15 seconds by default.

Because you already pressed Start on the Telegram bot, the first deployment will try to auto-detect your chat ID and send:

`✅ MKALERTS is online`

If Railway says no Telegram message was found, send `/start` or `hello` to your bot and restart the service.

After the first successful launch, Railway logs will show:

`Auto-detected Telegram chat ID: 123456789`

For reliable restarts, add that number as another Railway variable:

`TELEGRAM_CHAT_ID=123456789`

## What it does

- Watches the public Lazada product page
- Detects a visible, enabled **Add to Cart** or **Buy Now**
- Sends a Telegram stock alert with the resolved Lazada URL
- Reports obvious traffic/CAPTCHA blocks
- Suppresses duplicate alerts
- Backs off after repeated Lazada failures

It does not log in, bypass CAPTCHAs, add to cart, or buy anything.
