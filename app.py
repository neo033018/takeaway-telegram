
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


# =========================================================
# DATABASE
# =========================================================

def get_db():

    connection = sqlite3.connect(DB, timeout=15)
    connection.row_factory = sqlite3.Row

    return connection


def init_db():

    connection = get_db()

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

        logging.info(
            "Telegram %s status=%s",
            method,
            response.status_code
        )

        if not result.get("ok"):
            logging.warning(
                "Telegram API failed: %s",
                result.get("description")
            )

        return result

    except Exception:
        logging.exception("Telegram API error")
        return None


# =========================================================
# BIG ORDER BUTTON
# =========================================================

def order_keyboard():

    return {
        "keyboard": [
            [
                {
                    "text": "☕ ORDER / PORUČI",
                    "web_app": {
                        "url": URL
                    }
                }
            ]
        ],
        "resize_keyboard": True,
        "is_persistent": True,
        "one_time_keyboard": False
    }


# =========================================================
# VERIFY TELEGRAM MINI APP
# =========================================================

def verify(init_data):

    if not TOKEN or not init_data:
        logging.warning("Missing TOKEN or initData")
        return None

    if not isinstance(init_data, str):
        return None

    if len(init_data) > 8192:
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
            return None

        data_check_string = "\n".join(
            f"{key}={value}"
            for key, value in sorted(data.items())
        )

        secret_key = hmac.new(
            b"WebAppData",
            TOKEN.encode(),
            hashlib.sha256
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(
            calculated_hash,
            received_hash
        ):
            logging.warning("Invalid Telegram hash")
            return None

        auth_date = int(data.get("auth_date", "0"))

        age = int(time.time()) - auth_date

        if age > 86400 or age < -300:
            logging.warning("Telegram session expired")
            return None

        user = json.loads(data.get("user", "{}"))

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

    data = request.get_json(silent=True) or {}

    if not isinstance(data, dict):
        return jsonify({
            "error": "Invalid request."
        }), 400

    init_data = data.get("initData", "")

    user = verify(init_data)

    if not user:

        return jsonify({
            "error":
            "Please open TAKE AWAY from the "
            "ORDER / PORUČI button in Telegram."
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

    if not pickup_raw or not isinstance(pickup_raw, str):

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

    except Exception:

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
            "error": f"Pickup starts at {OPEN_HOUR:02d}:00."
        }), 400

    if pickup >= closing:

        return jsonify({
            "error":
            f"Last pickup must be before {CLOSE_HOUR:02d}:00."
        }), 400

    # =====================================================
    # CALCULATE ORDER
    # =====================================================

    menu_by_id = {
        item["id"]: item
        for item in MENU
    }

    total = 0
    total_quantity = 0
    order_lines = []
    clean_items = []

    for item in items:

        if not isinstance(item, dict):

            return jsonify({
                "error": "Invalid order item."
            }), 400

        item_id = str(item.get("id", ""))

        product = menu_by_id.get(item_id)

        if not product:

            return jsonify({
                "error": f"Unknown drink: {item_id}"
            }), 400

        qty_raw = item.get("qty", 1)

        if (
            isinstance(qty_raw, bool)
            or not isinstance(qty_raw, int)
        ):

            return jsonify({
                "error": "Invalid quantity."
            }), 400

        qty = qty_raw

        if qty < 1 or qty > 10:

            return jsonify({
                "error": "Quantity must be between 1 and 10."
            }), 400

        total_quantity += qty

        if total_quantity > 30:

            return jsonify({
                "error": "Maximum 30 drinks per order."
            }), 400

        extra_shot = item.get("extra_shot", False) is True
        boba = item.get("boba", False) is True

        if item_id not in COFFEE_IDS:
            extra_shot = False

        if not product.get("boba", False):
            boba = False

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

        extras_text = ""

        if extras:
            extras_text = " + " + ", ".join(extras)

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
    created = now.isoformat()

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
                created
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

    update = request.get_json(silent=True) or {}

    if not isinstance(update, dict):
        return "", 200

    message = update.get("message") or {}

    chat = message.get("chat") or {}

    chat_id = chat.get("id")

    text = str(
        message.get("text", "")
    ).strip()

    if not chat_id:
        return "", 200

    # =====================================================
    # START
    # =====================================================

    if text.startswith("/start"):

        welcome_message = (
            "☕ WELCOME TO TAKE AWAY\n\n"
            "Thai Tea & Coffee\n\n"
            "Fresh drinks. Good energy. Every day.\n\n"
            "Choose your favourite drinks, "
            "select a pickup time and "
            "we will prepare everything for you.\n\n"
            "💵 Pay at pickup.\n\n"
            "👇 Tap ORDER / PORUČI below to begin!"
        )

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

    if text.startswith("/help"):

        tg(
            "sendMessage",
            {
                "chat_id": chat_id,
                "text":
                    "☕ TAKE AWAY\n\n"
                    "Tap ORDER / PORUČI below "
                    "to choose your drinks.\n\n"
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
