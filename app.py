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
URL = os.environ.get("APP_URL", "").rstrip("/")
ADMIN = os.environ.get("ADMIN_CHAT_ID", "")
DB = os.environ.get("DATABASE_PATH", "orders.db")

TZ = ZoneInfo("Europe/Belgrade")

OPEN_HOUR = 7
CLOSE_HOUR = 17
MIN_PREP_MINUTES = 10


# =========================================================
# MENU
# =========================================================

MENU = {
    "espresso": {
        "name": "Espresso",
        "price": 200,
        "category": "coffee",
        "boba": False,
    },
    "americano": {
        "name": "Americano",
        "price": 250,
        "category": "coffee",
        "boba": False,
    },
    "cappuccino": {
        "name": "Cappuccino",
        "price": 320,
        "category": "coffee",
        "boba": False,
    },
    "latte": {
        "name": "Latte",
        "price": 350,
        "category": "coffee",
        "boba": False,
    },
    "flatwhite": {
        "name": "Flat White",
        "price": 400,
        "category": "coffee",
        "boba": False,
    },
    "icedlatte": {
        "name": "Iced Latte",
        "price": 380,
        "category": "coffee",
        "boba": False,
    },
    "thai": {
        "name": "Thai Classic Milk Tea",
        "price": 450,
        "category": "tea",
        "boba": True,
    },
    "green": {
        "name": "Thai Green Milk Tea",
        "price": 450,
        "category": "tea",
        "boba": True,
    },
    "lime": {
        "name": "Iced Lime Tea",
        "price": 480,
        "category": "tea",
        "boba": False,
    },
    "tonic": {
        "name": "Espresso Tonic",
        "price": 420,
        "category": "special",
        "boba": False,
    },
    "bumble": {
        "name": "Bumble Coffee",
        "price": 450,
        "category": "special",
        "boba": False,
    },
}


COFFEE_IDS = {
    "espresso",
    "americano",
    "cappuccino",
    "latte",
    "flatwhite",
    "icedlatte",
    "tonic",
    "bumble",
}


# =========================================================
# DATABASE
# =========================================================

def db():
    connection = sqlite3.connect(DB, timeout=15)

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            customer TEXT NOT NULL,
            pickup TEXT NOT NULL,
            details TEXT NOT NULL,
            total INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'new',
            created TEXT NOT NULL
        )
        """
    )

    connection.commit()
    return connection


# =========================================================
# TELEGRAM API
# =========================================================

def tg(method, payload):
    if not TOKEN:
        logging.error("BOT_TOKEN is missing")
        return None

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/{method}",
            json=payload,
            timeout=15,
        )

        response.raise_for_status()

        result = response.json()

        logging.info(
            "Telegram %s response: %s",
            method,
            result,
        )

        return result

    except Exception:
        logging.exception("Telegram API failure")
        return None


# =========================================================
# VERIFY TELEGRAM MINI APP USER
# =========================================================

def verify(init_data):
    if not TOKEN or not init_data:
        logging.warning("Missing TOKEN or initData")
        return None

    if len(init_data) > 8192:
        logging.warning("initData is too large")
        return None

    try:
        data = dict(
            parse_qsl(
                init_data,
                keep_blank_values=True,
            )
        )

        received_hash = data.pop("hash", "")

        if not received_hash:
            logging.warning(
                "Telegram initData has no hash"
            )
            return None

        secret_key = hmac.new(
            b"WebAppData",
            TOKEN.encode(),
            hashlib.sha256,
        ).digest()

        check_string = "\n".join(
            f"{key}={value}"
            for key, value in sorted(data.items())
        )

        calculated_hash = hmac.new(
            secret_key,
            check_string.encode(),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(
            calculated_hash,
            received_hash,
        ):
            logging.warning(
                "Telegram initData hash mismatch"
            )
            return None

        # Telegram Desktop may keep the Mini App open
        # for a while. Accept initData for 24 hours.
        auth_date = int(
            data.get("auth_date", "0")
        )

        if auth_date <= 0:
            logging.warning(
                "Invalid Telegram auth_date"
            )
            return None

        age = time.time() - auth_date

        if age < -300 or age > 86400:
            logging.warning(
                "Telegram initData expired. Age: %s",
                age,
            )
            return None

        user_raw = data.get("user")

        if not user_raw:
            logging.warning(
                "Telegram initData has no user"
            )
            return None

        user = json.loads(user_raw)

        if not isinstance(
            user.get("id"),
            int,
        ):
            logging.warning(
                "Invalid Telegram user ID"
            )
            return None

        return user

    except Exception:
        logging.exception(
            "Telegram initData verification failed"
        )
        return None


# =========================================================
# WEBSITE
# =========================================================

@app.get("/")
def index():
    return render_template("index.html")


@app.get("/health")
def health():
    return jsonify(
        status="ok",
        telegram_token=bool(TOKEN),
        app_url=bool(URL),
        admin_chat=bool(ADMIN),
    )


# =========================================================
# MENU API
# =========================================================

@app.get("/api/menu")
def menu():
    result = []

    for item_id, item in MENU.items():
        result.append(
            {
                "id": item_id,
                "name": item["name"],
                "price": item["price"],
                "category": item["category"],
                "boba": item["boba"],
            }
        )

    return jsonify(result)


# =========================================================
# ORDER API
# =========================================================

@app.post("/api/order")
def order():
    data = request.get_json(silent=True) or {}

    init_data = data.get("initData", "")

    logging.info(
        "Order request received. initData present: %s",
        bool(init_data),
    )

    user = verify(init_data)

    if not user:
        return jsonify(
            error=(
                "Telegram session could not be verified. "
                "Close the ordering window and open it "
                "again from the bot."
            )
        ), 401

    # -----------------------------------------------------
    # ITEMS
    # -----------------------------------------------------

    items = data.get("items", [])

    if not isinstance(items, list):
        return jsonify(
            error="Invalid order."
        ), 400

    if not 1 <= len(items) <= 20:
        return jsonify(
            error="Select between 1 and 20 drinks."
        ), 400

    # -----------------------------------------------------
    # PICKUP TIME
    # -----------------------------------------------------

    now = datetime.now(TZ)

    try:
        pickup = datetime.fromisoformat(
            data.get("pickup", "")
        )

    except (ValueError, TypeError):
        return jsonify(
            error="Choose a pickup time."
        ), 400

    if pickup.tzinfo is None:
        return jsonify(
            error="Invalid pickup time."
        ), 400

    pickup = pickup.astimezone(TZ)

    minimum_pickup = (
        now + timedelta(
            minutes=MIN_PREP_MINUTES
        )
    )

    if pickup < minimum_pickup:
        return jsonify(
            error=(
                "Pickup must be at least "
                f"{MIN_PREP_MINUTES} minutes "
                "from now."
            )
        ), 400

    # Orders are for today only.
    if pickup.date() != now.date():
        return jsonify(
            error=(
                "Please choose a pickup time "
                "for today."
            )
        ), 400

    opening = now.replace(
        hour=OPEN_HOUR,
        minute=0,
        second=0,
        microsecond=0,
    )

    closing = now.replace(
        hour=CLOSE_HOUR,
        minute=0,
        second=0,
        microsecond=0,
    )

    if pickup < opening:
        return jsonify(
            error="Pickup starts from 07:00."
        ), 400

    if pickup >= closing:
        return jsonify(
            error=(
                "Last pickup must be "
                "before 17:00."
            )
        ), 400

    # -----------------------------------------------------
    # CALCULATE ORDER
    # -----------------------------------------------------

    total = 0
    lines = []

    for selected in items:

        if not isinstance(selected, dict):
            return jsonify(
                error="Invalid drink."
            ), 400

        item_id = selected.get("id")

        if item_id not in MENU:
            return jsonify(
                error="Invalid drink."
            ), 400

        item = MENU[item_id]

        name = item["name"]
        price = item["price"]
        allow_boba = item["boba"]

        qty = selected.get("qty", 1)

        if type(qty) is not int:
            return jsonify(
                error="Invalid quantity."
            ), 400

        if not 1 <= qty <= 10:
            return jsonify(
                error="Invalid quantity."
            ), 400

        boba = selected.get(
            "boba",
            False,
        )

        if type(boba) is not bool:
            return jsonify(
                error="Invalid topping."
            ), 400

        if boba and not allow_boba:
            return jsonify(
                error=(
                    "Boba is not available "
                    "for this drink."
                )
            ), 400

        extra_shot = selected.get(
            "extra_shot",
            False,
        )

        if type(extra_shot) is not bool:
            return jsonify(
                error="Invalid extra shot."
            ), 400

        if (
            extra_shot
            and item_id not in COFFEE_IDS
        ):
            return jsonify(
                error=(
                    "Extra shot is not available "
                    "for this drink."
                )
            ), 400

        unit_price = price
        extras = []

        if boba:
            unit_price += 100
            extras.append("Boba")

        if extra_shot:
            unit_price += 150
            extras.append("Extra shot")

        item_total = unit_price * qty
        total += item_total

        description = (
            f"{qty}× {name}"
        )

        if extras:
            description += (
                " + "
                + ", ".join(extras)
            )

        description += (
            f" — {item_total} RSD"
        )

        lines.append(description)

    if total > 100000:
        return jsonify(
            error="Order is too large."
        ), 400

    # -----------------------------------------------------
    # CUSTOMER
    # -----------------------------------------------------

    customer = (
        (
            user.get("first_name", "")
            + " "
            + user.get("last_name", "")
        )
        .strip()[:80]
        or "Telegram customer"
    )

    # -----------------------------------------------------
    # SAVE ORDER
    # -----------------------------------------------------

    connection = db()

    try:
        cursor = connection.execute(
            """
            INSERT INTO orders (
                telegram_id,
                customer,
                pickup,
                details,
                total,
                created
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user["id"],
                customer,
                pickup.isoformat(),
                json.dumps(
                    lines,
                    ensure_ascii=False,
                ),
                total,
                now.isoformat(),
            ),
        )

        connection.commit()

        order_id = cursor.lastrowid

    finally:
        connection.close()

    # -----------------------------------------------------
    # TELEGRAM MESSAGES
    # -----------------------------------------------------

    username = ""

    if user.get("username"):
        username = (
            f' (@{user["username"]})'
        )

    admin_message = (
        f"🔔 NEW ORDER #{order_id}\n\n"
        f"👤 {customer}{username}\n"
        f"🕒 Pickup: {pickup:%H:%M}\n\n"
        + "\n".join(lines)
        + f"\n\n💵 TOTAL: {total} RSD"
        + "\n💳 PAY AT PICKUP"
    )

    if ADMIN:
        tg(
            "sendMessage",
            {
                "chat_id": ADMIN,
                "text": admin_message,
            },
        )
    else:
        logging.warning(
            "ADMIN_CHAT_ID is not configured"
        )

    tg(
        "sendMessage",
        {
            "chat_id": user["id"],
            "text": (
                f"✅ Take Away order "
                f"#{order_id} confirmed!\n\n"
                f"🕒 Pickup: "
                f"{pickup:%H:%M}\n"
                f"💵 Total: "
                f"{total} RSD\n\n"
                f"Pay when you collect "
                f"your order. ☕"
            ),
        },
    )

    return jsonify(
        success=True,
        order_id=order_id,
        total=total,
        pickup=pickup.strftime("%H:%M"),
    )


# =========================================================
# TELEGRAM WEBHOOK
# =========================================================

@app.post("/api/bot-webhook")
def webhook():

    data = request.get_json(
        silent=True
    ) or {}

    logging.info(
        "Telegram update received: %s",
        data,
    )

    message = (
        data.get("message")
        or {}
    )

    chat = (
        message.get("chat")
        or {}
    )

    chat_id = chat.get("id")

    text = message.get(
        "text",
        "",
    )

    if not chat_id:
        return "", 200

    if text.startswith("/start"):

        if URL.startswith("https://"):

            tg(
                "sendMessage",
                {
                    "chat_id": chat_id,

                    "text": (
                        "Welcome to Take Away ☕\n\n"
                        "Pre-order your drinks "
                        "and pay when you "
                        "collect them."
                    ),

                    "reply_markup": {
                        "keyboard": [
                            [
                                {
                                    "text":
                                        "☕ Order drinks",

                                    "web_app": {
                                        "url": URL
                                    },
                                }
                            ]
                        ],

                        "resize_keyboard": True,
                        "is_persistent": True,
                    },
                },
            )

        else:

            tg(
                "sendMessage",
                {
                    "chat_id": chat_id,

                    "text": (
                        "Take Away ordering "
                        "app is being "
                        "configured. ☕"
                    ),
                },
            )

    return "", 200


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    db().close()

    port = int(
        os.environ.get(
            "PORT",
            5000,
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
    )
