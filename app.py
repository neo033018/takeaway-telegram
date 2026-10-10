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

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:%(name)s:%(message)s"
)

TOKEN = os.environ.get("BOT_TOKEN", "")
URL = os.environ.get("APP_URL", "").rstrip("/")
ADMIN = os.environ.get("ADMIN_CHAT_ID", "")
DB = os.environ.get("DATABASE_PATH", "orders.db")

TZ = ZoneInfo("Europe/Belgrade")


# =========================================================
# SHOP SETTINGS
# =========================================================

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

def get_db():

    connection = sqlite3.connect(DB)

    connection.row_factory = sqlite3.Row

    return connection


def init_db():

    connection = get_db()

    connection.execute(
        """
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
        """
    )

    connection.commit()

    connection.close()


init_db()


# =========================================================
# TELEGRAM API
# =========================================================

def tg(method, payload):

    if not TOKEN:

        logging.error(
            "BOT_TOKEN is missing"
        )

        return None


    api_url = (
        f"https://api.telegram.org/"
        f"bot{TOKEN}/{method}"
    )


    try:

        response = requests.post(
            api_url,
            json=payload,
            timeout=15
        )


        try:

            result = response.json()

        except Exception:

            result = {
                "ok": False,
                "text": response.text
            }


        logging.info(
            "Telegram %s response: %s",
            method,
            result
        )


        return result


    except Exception as e:

        logging.exception(
            "Telegram API error: %s",
            e
        )

        return None


# =========================================================
# TELEGRAM MINI APP VERIFICATION
# =========================================================

def verify(init_data):

    if not TOKEN:

        logging.warning(
            "Missing BOT_TOKEN"
        )

        return None


    if not init_data:

        logging.warning(
            "Missing Telegram initData"
        )

        return None


    if len(init_data) > 8192:

        logging.warning(
            "initData is too long"
        )

        return None


    try:

        data = dict(
            parse_qsl(
                init_data,
                keep_blank_values=True
            )
        )

    except Exception as e:

        logging.warning(
            "Could not parse initData: %s",
            e
        )

        return None


    received_hash = data.pop(
        "hash",
        None
    )


    if not received_hash:

        logging.warning(
            "initData has no hash"
        )

        return None


    data_check_string = "\n".join(

        f"{key}={value}"

        for key, value
        in sorted(data.items())

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

        logging.warning(
            "Telegram initData hash mismatch"
        )

        return None


    # -----------------------------------------------------
    # AUTH DATE
    # -----------------------------------------------------

    try:

        auth_date = int(
            data.get(
                "auth_date",
                "0"
            )
        )


        now = int(
            time.time()
        )


        age = now - auth_date


        # Accept up to 24 hours.
        # Also allow small clock difference.

        if (
            age > 86400
            or
            age < -300
        ):

            logging.warning(
                "Telegram initData expired. "
                "Age: %s seconds",
                age
            )

            return None


    except Exception as e:

        logging.warning(
            "Invalid auth_date: %s",
            e
        )

        return None


    # -----------------------------------------------------
    # USER
    # -----------------------------------------------------

    raw_user = data.get(
        "user"
    )


    if not raw_user:

        logging.warning(
            "Telegram initData has no user"
        )

        return None


    try:

        user = json.loads(
            raw_user
        )

    except Exception as e:

        logging.warning(
            "Could not parse Telegram user: %s",
            e
        )

        return None


    logging.info(
        "Telegram user verified: %s",
        user.get("id")
    )


    return user


# =========================================================
# HOME
# =========================================================

@app.route("/")
def home():

    return render_template(
        "index.html"
    )


# =========================================================
# HEALTH
# =========================================================

@app.route("/health")
def health():

    return jsonify({

        "ok": True,

        "bot_token":
            bool(TOKEN),

        "app_url":
            URL,

        "admin_chat":
            bool(ADMIN),

    })


# =========================================================
# MENU API
# =========================================================

@app.route("/api/menu")
def api_menu():

    result = []


    for item_id, item in MENU.items():

        result.append({

            "id":
                item_id,

            "name":
                item["name"],

            "price":
                item["price"],

            "category":
                item["category"],

            "boba":
                item["boba"],

        })


    return jsonify(
        result
    )


# =========================================================
# ORDER API
# =========================================================

@app.route(
    "/api/order",
    methods=["POST"]
)
def api_order():

    data = request.get_json(
        silent=True
    ) or {}


    init_data = data.get(
        "initData",
        ""
    )


    logging.info(
        "Order request received. "
        "initData present: %s",
        bool(init_data)
    )


    user = verify(
        init_data
    )


    if not user:

        return jsonify({

            "error":
                "Telegram session could not be verified. "
                "Close the ordering window and open it "
                "again from the bot."

        }), 401


    # -----------------------------------------------------
    # ITEMS
    # -----------------------------------------------------

    items = data.get(
        "items",
        []
    )


    if (
        not isinstance(items, list)
        or
        len(items) < 1
        or
        len(items) > 20
    ):

        return jsonify({

            "error":
                "Invalid order."

        }), 400


    # -----------------------------------------------------
    # PICKUP TIME
    # -----------------------------------------------------

    pickup_raw = data.get(
        "pickup"
    )


    if not pickup_raw:

        return jsonify({

            "error":
                "Please choose a pickup time."

        }), 400


    try:

        pickup = datetime.fromisoformat(
            pickup_raw.replace(
                "Z",
                "+00:00"
            )
        )


        if pickup.tzinfo is None:

            pickup = pickup.replace(
                tzinfo=TZ
            )


        pickup = pickup.astimezone(
            TZ
        )


    except Exception:

        return jsonify({

            "error":
                "Invalid pickup time."

        }), 400


    now = datetime.now(
        TZ
    )


    minimum_pickup = (
        now
        +
        timedelta(
            minutes=MIN_PREP_MINUTES
        )
    )


    # Small tolerance because a few seconds
    # can pass between choosing 10 min
    # and sending the request.

    if pickup < (
        minimum_pickup
        -
        timedelta(seconds=30)
    ):

        return jsonify({

            "error":
                "Pickup must be at least "
                f"{MIN_PREP_MINUTES} minutes "
                "from now."

        }), 400


    if pickup.date() != now.date():

        return jsonify({

            "error":
                "Pickup is available today only."

        }), 400


    opening = pickup.replace(
        hour=OPEN_HOUR,
        minute=0,
        second=0,
        microsecond=0
    )


    closing = pickup.replace(
        hour=CLOSE_HOUR,
        minute=0,
        second=0,
        microsecond=0
    )


    if pickup < opening:

        return jsonify({

            "error":
                f"We open at "
                f"{OPEN_HOUR:02d}:00."

        }), 400


    if pickup >= closing:

        return jsonify({

            "error":
                f"Pickup is available before "
                f"{CLOSE_HOUR:02d}:00."

        }), 400


    # -----------------------------------------------------
    # CALCULATE ORDER
    # -----------------------------------------------------

    total = 0

    lines = []


    for raw_item in items:

        item_id = raw_item.get(
            "id"
        )


        if item_id not in MENU:

            return jsonify({

                "error":
                    "Unknown menu item."

            }), 400


        try:

            qty = int(
                raw_item.get(
                    "qty",
                    1
                )
            )

        except Exception:

            return jsonify({

                "error":
                    "Invalid quantity."

            }), 400


        if qty < 1 or qty > 10:

            return jsonify({

                "error":
                    "Invalid quantity."

            }), 400


        product = MENU[
            item_id
        ]


        price = int(
            product["price"]
        )


        extras = []


        # -------------------------------------------------
        # EXTRA SHOT
        # -------------------------------------------------

        extra_shot = bool(
            raw_item.get(
                "extra_shot",
                False
            )
        )


        if extra_shot:

            if item_id not in COFFEE_IDS:

                return jsonify({

                    "error":
                        "Extra shot is not available "
                        "for this drink."

                }), 400


            price += 150

            extras.append(
                "extra shot"
            )


        # -------------------------------------------------
        # BOBA
        # -------------------------------------------------

        boba = bool(
            raw_item.get(
                "boba",
                False
            )
        )


        if boba:

            if not product["boba"]:

                return jsonify({

                    "error":
                        "Boba is not available "
                        "for this drink."

                }), 400


            price += 100

            extras.append(
                "boba"
            )


        line_total = (
            price * qty
        )


        total += line_total


        line = (
            f"{qty}× "
            f"{product['name']}"
        )


        if extras:

            line += (
                " ("
                +
                ", ".join(extras)
                +
                ")"
            )


        line += (
            f" — {line_total} RSD"
        )


        lines.append(
            line
        )


    # -----------------------------------------------------
    # CUSTOMER
    # -----------------------------------------------------

    telegram_id = str(
        user.get(
            "id",
            ""
        )
    )


    first_name = (
        user.get(
            "first_name"
        )
        or
        ""
    )


    last_name = (
        user.get(
            "last_name"
        )
        or
        ""
    )


    username = (
        user.get(
            "username"
        )
        or
        ""
    )


    customer = (
        f"{first_name} {last_name}"
    ).strip()


    if not customer:

        customer = (
            f"Telegram {telegram_id}"
        )


    if username:

        customer_display = (
            f"{customer} "
            f"(@{username})"
        )

    else:

        customer_display = customer


    # -----------------------------------------------------
    # SAVE DATABASE
    # -----------------------------------------------------

    created = datetime.now(
        TZ
    ).isoformat()


    pickup_iso = pickup.isoformat()


    connection = get_db()


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
        VALUES
        (
            ?, ?, ?, ?, ?, ?, ?
        )
        """,

        (
            telegram_id,
            customer,
            pickup_iso,
            json.dumps(
                items,
                ensure_ascii=False
            ),
            total,
            "new",
            created,
        )
    )


    connection.commit()


    order_id = (
        cursor.lastrowid
    )


    connection.close()


    # -----------------------------------------------------
    # PICKUP DISPLAY
    # -----------------------------------------------------

    pickup_display = (
        pickup.strftime(
            "%H:%M"
        )
    )


    # -----------------------------------------------------
    # ADMIN / BARISTA MESSAGE
    # -----------------------------------------------------

    if ADMIN:

        admin_text = (
            "🔔 NEW ORDER\n\n"
            f"Order #{order_id}\n"
            f"Customer: {customer_display}\n"
            f"Pickup: {pickup_display}\n\n"
            +
            "\n".join(lines)
            +
            f"\n\nTOTAL: {total} RSD"
        )


        tg(
            "sendMessage",
            {
                "chat_id":
                    ADMIN,

                "text":
                    admin_text,
            }
        )


    # -----------------------------------------------------
    # CUSTOMER CONFIRMATION
    # -----------------------------------------------------

    customer_text = (
        "✅ Order confirmed!\n\n"
        f"Order #{order_id}\n"
        f"Pickup: {pickup_display}\n\n"
        +
        "\n".join(lines)
        +
        f"\n\nTotal: {total} RSD\n\n"
        "Pay when you collect. ☕"
    )


    tg(
        "sendMessage",
        {
            "chat_id":
                telegram_id,

            "text":
                customer_text,
        }
    )


    logging.info(
        "Order #%s created for Telegram user %s",
        order_id,
        telegram_id
    )


    return jsonify({

        "success":
            True,

        "order_id":
            order_id,

        "total":
            total,

        "pickup":
            pickup_display,

    })


# =========================================================
# TELEGRAM WEBHOOK
# =========================================================

@app.route(
    "/api/bot-webhook",
    methods=["POST"]
)
def bot_webhook():

    update = request.get_json(
        silent=True
    ) or {}


    logging.info(
        "Telegram update received: %s",
        update
    )


    message = update.get(
        "message"
    ) or {}


    chat = message.get(
        "chat"
    ) or {}


    chat_id = chat.get(
        "id"
    )


    text = (
        message.get(
            "text"
        )
        or
        ""
    ).strip()


    # -----------------------------------------------------
    # /START
    # -----------------------------------------------------

    if (
        chat_id
        and
        text.startswith(
            "/start"
        )
    ):

        if not URL:

            tg(
                "sendMessage",
                {
                    "chat_id":
                        chat_id,

                    "text":
                        "The ordering app is not configured yet."
                }
            )


            return "", 200


        tg(
            "sendMessage",
            {
                "chat_id":
                    chat_id,

                "text":
                    (
                        "Welcome to Take Away ☕\n\n"
                        "Order your drinks and choose "
                        "when you want to collect them."
                    ),

                # IMPORTANT:
                # INLINE keyboard, not reply keyboard.
                #
                # This button is attached directly
                # under the Telegram message.

                "reply_markup": {

                    "inline_keyboard": [

                        [
                            {
                                "text":
                                    "☕ Order drinks",

                                "web_app": {
                                    "url":
                                        URL
                                }
                            }
                        ]

                    ]

                }
            }
        )


    return "", 200


# =========================================================
# RUN LOCAL
# =========================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )


    app.run(
        host="0.0.0.0",
        port=port
    )
