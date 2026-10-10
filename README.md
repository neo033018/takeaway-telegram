# TAKE AWAY — Telegram pre-order mini app

Flask + SQLite starter for @TAKEAWAYBG_BOT. Customers select drinks, choose a pickup delay, pay at pickup, receive a confirmation, and the configured shop chat receives a notification.

## Security
The bot token previously posted in chat should be revoked in BotFather. Do **not** paste any token into this repository or commit it to Git. Put the replacement in your hosting provider's environment variables.

## Run locally
```bash
pip install -r requirements.txt
export BOT_TOKEN='YOUR_NEW_SECRET_TOKEN'
export APP_URL='https://YOUR_PUBLIC_HTTPS_URL'
export ADMIN_CHAT_ID='YOUR_PRIVATE_TELEGRAM_CHAT_ID'
python app.py
```
Local preview: http://localhost:5000 . Telegram checkout only works after opening from the real bot on HTTPS.

## Deploy (Render or similar Python host)
1. Upload this folder to a **private** GitHub repository.
2. Create a Python Web Service; build command `pip install -r requirements.txt`; start command `gunicorn --workers 1 --threads 4 --bind 0.0.0.0:$PORT app:app`.
3. Set `BOT_TOKEN`, `APP_URL` (your actual HTTPS domain), `ADMIN_CHAT_ID`, and `WEBHOOK_SECRET` environment variables. Generate WEBHOOK_SECRET as a long random alphanumeric string.
4. Attach persistent storage and set `DATABASE_PATH` to its mounted location, e.g. `/var/data/orders.db`. **Without persistent storage orders may disappear after redeploy.**
5. Configure the webhook (execute locally with your secrets set, without printing them):
```python
import os,requests
base='https://api.telegram.org/bot'+os.environ['BOT_TOKEN']
r=requests.post(base+'/setWebhook',json={'url':os.environ['APP_URL'].rstrip('/')+'/api/bot-webhook','secret_token':os.environ['WEBHOOK_SECRET']},timeout=15)
print(r.status_code,r.json().get('ok'))
```
6. In BotFather, select @TAKEAWAYBG_BOT → Bot Settings → Menu Button (or `/setmenubutton`) and enter the deployed HTTPS URL. You can also configure the Main Mini App to show a Launch button.
7. Send `/start` to your bot and use its **Order drinks** button. Place a test order and verify the shop chat receives it.

## Finding the shop chat ID
Start a private chat with your bot and send `/start`. Then retrieve the chat ID via Telegram's `getUpdates` **before setting a webhook**; alternatively use a trusted chat-ID helper bot. Set ADMIN_CHAT_ID to that numeric ID. For a group, add the bot to the group and use its negative chat ID. Never send the token to chat-ID helper services.

## Important before accepting real customers
- Menu and prices are starter values; verify against your current shop menu. Operating hours, stock, prep capacity, and order cancellation are **not** enforced yet.
- `notification_sent` in the API response is informational; if Telegram delivery fails, the order is still saved, so monitor failures and add retry handling before production.
- The initial implementation does not prevent duplicate submissions across retries. Add an idempotency key before public launch.
- For higher-volume production, replace SQLite with managed PostgreSQL, and add an admin order-management dashboard, status notifications, monitoring, and rate limiting.
- The `pickup` selector is relative to the customer's device time; backend validates the timestamp against Belgrade local time.
