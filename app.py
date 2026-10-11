import os
import json
import time
import hmac
import hashlib
import sqlite3
import logging

from urllib.parse import parse_qsl
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
from flask import Flask, request, jsonify, render_template

# =========================================================
# SETTINGS
# =========================================================

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

TOKEN = os.environ.get("BOT_TOKEN", "")
URL = os.environ.get(
    "APP_URL",
    "https://takeaway-telegram.onrender.com"
).rstrip("/")

ADMIN = os.environ.get("ADMIN_CHAT_ID", "")
DB = os.environ.get("DATABASE_PATH", "orders.db")

WEBHOOK_SECRET = os.environ.get(
    "WEBHOOK_SECRET", ""
)

TZ = ZoneInfo("Europe/Belgrade")

MIN_PREP_MINUTES = 10
OPEN_HOUR = 7
CLOSE_HOUR = 17

# =========================================================
# MENU
# =========================================================

MENU = [
    {
        "id": "thai",
        "name": "Classic Thai Milk Tea",
        "price": 450,
        "category": "tea",
        "boba": True
    },
    {
        "id": "green",
        "name": "Thai Green Milk Tea",
        "price": 450,
        "category": "tea",
        "boba": True
    },
    {
        "id": "lime",
        "name": "Thai Iced Lime Tea",
        "price": 420,
        "category": "tea",
        "boba": False
    },
    {
        "id": "espresso",
        "name": "Espresso",
        "price": 200,
        "category": "coffee",
        "boba": False
    },
    {
        "id": "americano",
        "name": "Americano",
        "price": 250,
        "category": "coffee",
        "boba": False
    },
    {
        "id": "cappuccino",
        "name": "Cappuccino",
        "price": 320,
        "category": "coffee",
        "boba": False
    },
    {
        "id": "latte",
        "name": "Latte",
        "price": 350,
        "category": "coffee",
        "boba": False
    },
    {
        "id": "flatwhite",
        "name": "Flat White",
        "price": 450,
        "category": "coffee",
        "boba": False
    },
    {
        "id": "icedlatte",
        "name": "Iced Latte",
        "price": 380,
        "category": "coffee",
        "boba": False
    },
    {
        "id": "americanocream",
        "name": "Iced Americano with Cream",
        "price": 450,
        "category": "coffee",
        "boba": False
    },
    {
        "id": "tonic",
        "name": "Tonic Coffee",
        "price": 420,
        "category": "special",
        "boba": False
    },
    {
        "id": "bumble",
        "name": "Bumble Coffee",
        "price": 450,
        "category": "special",
        "boba": False
    },
    {
        "id": "strawberrymatcha",
        "name": "Strawberry Matcha Latte",
        "price": 480,
        "category": "special",
        "boba": False
    },
    {
        "id": "peanutlatte",
        "name": "Peanut Latte",
        "price": 550,
        "category": "special",
        "boba": False
    }
]

COFFEE_IDS = {
    "espresso",
    "americano",
    "cappuccino",
    "latte",
    "flatwhite",
    "icedlatte",
    "americanocream",
    "tonic",
    "bumble",
    "peanutlatte"
}

MENU_BY_ID = {
    product["id"]: product
    for product in MENU
}

# =========================================================
# DATABASE
# =========================================================

def get_db():
    connection = sqlite3.connect(DB, timeout=15)
    connection.row_factory = sqlite3.Row
    return connection

def init_db():
    connection = get_db()

    try:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id TEXT,
                customer TEXT,
                pickup TEXT,
                details TEXT,
                total INTEGER,
                status TEXT DEFAULT 'new',
                created TEXT
            )
        """)

        connection.commit()

    finally:
        connection.close()

init_db()

# =========================================================
# TELEGRAM API
# =========================================================

def tg(method, payload):

    if not TOKEN:
        logging.warning("BOT_TOKEN missing")
        return None

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/{method}",
            json=payload,
            timeout=15
        )

        result = response.json()

        if not result.get("ok"):
            logging.warning(
                "Telegram %s failed: %s",
                method,
                result.get("description")
            )

        return result

    except Exception:
        logging.exception("Telegram API error")
        return None

# =========================================================
# SINGLE INLINE ORDER BUTTON
# =========================================================

def order_keyboard():

    return {
        "inline_keyboard": [
            [
                {
                    "text": "☕ ORDER / PORUČI",
                    "web_app": {
                        "url": URL
                    }
                }
            ]
        ]
    }

# =========================================================
# VERIFY TELEGRAM MINI APP
# =========================================================

def verify(init_data):

    if (
        not TOKEN
        or not isinstance(init_data, str)
        or not init_data
        or len(init_data) > 8192
    ):
        logging.warning("Missing or invalid initData")
        return None

    try:
        data = dict(
            parse_qsl(
                init_data,
                keep_blank_values=True
            )
        )

        received_hash = data.pop("hash", None)

        if not received_hash:
            logging.warning("Missing Telegram hash")
            return None

        data_check_string = "\n".join(
            f"{key}={value}"
            for key, value in sorted(data.items())
        )

        secret_key = hmac.new(
            b"WebAppData",
            TOKEN.encode("utf-8"),
            hashlib.sha256
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(
            calculated_hash,
            received_hash
        ):
            logging.warning("Telegram hash mismatch")
            return None

        auth_date = int(data.get("auth_date", "0"))
        age = int(time.time()) - auth_date

        if age > 86400 or age < -300:
            logging.warning("Telegram session expired")
            return None

        user = json.loads(
            data.get("user", "{}")
        )

        if not isinstance(user, dict):
            return None

        if not user.get("id"):
            return None

        return user

    except Exception:
        logging.exception("Telegram verification error")
        return None

# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():
    return render_template("index.html")

# =========================================================
# HEALTH
# =========================================================

@app.route("/health")
def health():

    return jsonify({
        "ok": True,
        "bot_token": bool(TOKEN),
        "admin_chat": bool(ADMIN),
        "app_url": URL,
        "menu_items": len(MENU)
    })

# =========================================================
# MENU API
# =========================================================

@app.route("/api/menu")
def menu():
    return jsonify(MENU)

# =========================================================
# CREATE ORDER
# =========================================================

@app.route("/api/order", methods=["POST"])
def create_order():

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "error": "Invalid request."
        }), 400

    user = verify(
        data.get("initData", "")
    )

    if not user:
        return jsonify({
            "error":
            "Telegram session could not be verified. "
            "Please close the app and reopen it "
            "using the ORDER button in the bot."
        }), 401

    items = data.get("items", [])
    pickup_raw = data.get("pickup")

    if (
        not isinstance(items, list)
        or not items
        or len(items) > 20
    ):
        return jsonify({
            "error": "Please add at least one drink."
        }), 400

    if not isinstance(pickup_raw, str):
        return jsonify({
            "error": "Please choose a pickup time."
        }), 400

    try:
        pickup = datetime.fromisoformat(
            pickup_raw.replace("Z", "+00:00")
        )

        if pickup.tzinfo is None:
            pickup = pickup.replace(tzinfo=TZ)
        else:
            pickup = pickup.astimezone(TZ)

    except (ValueError, TypeError):
        return jsonify({
            "error": "Invalid pickup time."
        }), 400

    now = datetime.now(TZ)

    minimum_pickup = now + timedelta(
        minutes=MIN_PREP_MINUTES
    )

    if pickup < minimum_pickup - timedelta(seconds=30):
        return jsonify({
            "error":
            f"Minimum preparation time is "
            f"{MIN_PREP_MINUTES} minutes."
        }), 400

    if pickup.date() != now.date():
        return jsonify({
            "error": "Pickup is available today only."
        }), 400

    opening = now.replace(
        hour=OPEN_HOUR,
        minute=0,
        second=0,
        microsecond=0
    )

    closing = now.replace(
        hour=CLOSE_HOUR,
        minute=0,
        second=0,
        microsecond=0
    )

    if pickup < opening:
        return jsonify({
            "error":
            f"Pickup starts at {OPEN_HOUR:02d}:00."
        }), 400

    if pickup >= closing:
        return jsonify({
            "error":
            f"Last pickup must be before "
            f"{CLOSE_HOUR:02d}:00."
        }), 400

    total = 0
    total_quantity = 0
    order_lines = []
    clean_items = []

    for item in items:

        if not isinstance(item, dict):
            return jsonify({
                "error": "Invalid order item."
            }), 400

        item_id = item.get("id")

        if not isinstance(item_id, str):
            return jsonify({
                "error": "Invalid drink ID."
            }), 400

        product = MENU_BY_ID.get(item_id)

        if not product:
            return jsonify({
                "error": f"Unknown drink: {item_id}"
            }), 400

        qty = item.get("qty", 1)

        if isinstance(qty, bool) or not isinstance(qty, int):
            return jsonify({
                "error": "Invalid quantity."
            }), 400

        if qty < 1 or qty > 10:
            return jsonify({
                "error":
                "Quantity must be between 1 and 10."
            }), 400

        total_quantity += qty

        if total_quantity > 30:
            return jsonify({
                "error": "Maximum 30 drinks per order."
            }), 400

        extra_shot = (
            item.get("extra_shot") is True
            and item_id in COFFEE_IDS
        )

        boba = (
            item.get("boba") is True
            and product.get("boba", False)
        )

        unit_price = int(product["price"])
        extras = []

        if extra_shot:
            unit_price += 150
            extras.append("extra espresso shot")

        if boba:
            unit_price += 100
            extras.append("boba")

        line_total = unit_price * qty
        total += line_total

        extras_text = (
            " + " + ", ".join(extras)
            if extras
            else ""
        )

        order_lines.append(
            f"{qty}x {product['name']}"
            f"{extras_text} — {line_total} RSD"
        )

        clean_items.append({
            "id": item_id,
            "name": product["name"],
            "qty": qty,
            "extra_shot": extra_shot,
            "boba": boba,
            "unit_price": unit_price,
            "line_total": line_total
        })

    # =====================================================
    # CUSTOMER
    # =====================================================

    telegram_id = str(user["id"])

    first_name = str(
        user.get("first_name", "")
    ).strip()

    last_name = str(
        user.get("last_name", "")
    ).strip()

    username = str(
        user.get("username", "")
    ).strip()

    customer = f"{first_name} {last_name}".strip()

    if not customer:
        customer = (
            f"@{username}"
            if username
            else telegram_id
        )

    pickup_text = pickup.strftime("%H:%M")

    # =====================================================
    # SAVE ORDER
    # =====================================================

    connection = get_db()

    try:
        cursor = connection.execute(
            """
            INSERT INTO orders
            (
                telegram_id,
                customer,
                pickup,
                details,
                total,
                status,
                created
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                telegram_id,
                customer,
                pickup.isoformat(),
                json.dumps(
                    clean_items,
                    ensure_ascii=False
                ),
                total,
                "new",
                now.isoformat()
            )
        )

        order_id = cursor.lastrowid
        connection.commit()

    finally:
        connection.close()

    # =====================================================
    # BARISTA NOTIFICATION
    # =====================================================

    lines_text = "\n".join(order_lines)

    admin_message = (
        "🔔 TAKE AWAY — NEW ORDER\n\n"
        f"🧾 Order #{order_id}\n"
        f"👤 Customer: {customer}\n"
        f"🕐 Pickup: {pickup_text}\n\n"
        f"{lines_text}\n\n"
        f"💰 TOTAL: {total} RSD\n"
        "💵 Payment at pickup"
    )

    if ADMIN:
        tg(
            "sendMessage",
            {
                "chat_id": ADMIN,
                "text": admin_message
            }
        )
    else:
        logging.warning(
            "ADMIN_CHAT_ID not configured"
        )

    # =====================================================
    # CUSTOMER CONFIRMATION
    # =====================================================

    customer_message = (
        "✅ TAKE AWAY — ORDER CONFIRMED\n\n"
        f"🧾 Order #{order_id}\n"
        f"🕐 Pickup: {pickup_text}\n"
        f"💰 Total: {total} RSD\n\n"
        "Pay when you collect. ☕\n\n"
        "See you soon!"
    )

    tg(
        "sendMessage",
        {
            "chat_id": telegram_id,
            "text": customer_message
        }
    )

    return jsonify({
        "success": True,
        "order_id": order_id,
        "total": total,
        "pickup": pickup_text
    })

# =========================================================
# TELEGRAM WEBHOOK
# =========================================================

@app.route("/api/bot-webhook", methods=["POST"])
def bot_webhook():

    if WEBHOOK_SECRET:
        received_secret = request.headers.get(
            "X-Telegram-Bot-Api-Secret-Token", ""
        )

        if not hmac.compare_digest(
            received_secret,
            WEBHOOK_SECRET
        ):
            return "", 403

    update = request.get_json(silent=True) or {}

    if not isinstance(update, dict):
        return "", 200

    message = update.get("message") or {}
    chat = message.get("chat") or {}

    chat_id = chat.get("id")
    chat_type = chat.get("type")

    message_text = str(
        message.get("text", "")
    ).strip()

    if not chat_id or chat_type != "private":
        return "", 200

    command = (
        message_text.split()[0].split("@")[0]
        if message_text
        else ""
    )

    # =====================================================
    # START
    # =====================================================

    if command == "/start":

        welcome_message = (
            "☕ WELCOME TO TAKE AWAY\n\n"
            "THAI TEA & COFFEE\n\n"
            "Fresh drinks. Good energy. Every day.\n\n"
            "Choose your favourite drinks, "
            "select a pickup time and "
            "we will prepare everything for you.\n\n"
            "💵 Pay at pickup.\n\n"
            "👇 Tap the button below to order!"
        )

        # Remove old reply keyboard first.
        tg(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": "☕ TAKE AWAY",
                "reply_markup": {
                    "remove_keyboard": True
                }
            }
        )

        # Send welcome message with inline Web App button.
        tg(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": welcome_message,
                "reply_markup": order_keyboard()
            }
        )

        return "", 200

    # =====================================================
    # HELP
    # =====================================================

    if command == "/help":

        tg(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text":
                    "☕ TAKE AWAY\n\n"
                    "Choose your drinks and "
                    "select a pickup time.\n\n"
                    "Pickup: 07:00–17:00\n"
                    "Payment: at pickup.",
                "reply_markup": order_keyboard()
            }
        )

        return "", 200

    return "", 200

# =========================================================
# RUN LOCAL
# =========================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(
            os.environ.get("PORT", 10000)
        )
    )
