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
# APP / SETTINGS
# =========================================================

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

TOKEN = os.environ.get("BOT_TOKEN", "").strip()
URL = os.environ.get("APP_URL", "").strip().rstrip("/")
ADMIN = os.environ.get("ADMIN_CHAT_ID", "").strip()

DB = os.environ.get(
    "DATABASE_PATH",
    "orders.db"
)

TZ = ZoneInfo("Europe/Belgrade")

MIN_PREP_MINUTES = 10

# TAKE AWAY opening hours
OPEN_HOUR = 7
CLOSE_HOUR = 17


# =========================================================
# MENU
# =========================================================

MENU = [

    # -----------------------------------------------------
    # THAI TEA
    # -----------------------------------------------------

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


    # -----------------------------------------------------
    # COFFEE
    # -----------------------------------------------------

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


    # -----------------------------------------------------
    # SPECIALS
    # -----------------------------------------------------

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


# =========================================================
# EXTRA ESPRESSO SHOT
# =========================================================

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

    connection = sqlite3.connect(
        DB,
        timeout=10
    )

    connection.row_factory = sqlite3.Row

    return connection


def init_db():

    connection = get_db()

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            telegram_id TEXT NOT NULL,

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
    connection.close()


init_db()


# =========================================================
# TELEGRAM API
# =========================================================

def tg(method, payload):

    if not TOKEN:

        logging.warning(
            "BOT_TOKEN is missing"
        )

        return None

    try:

        response = requests.post(

            f"https://api.telegram.org/bot{TOKEN}/{method}",

            json=payload,

            timeout=15
        )

        logging.info(
            "Telegram %s status=%s response=%s",
            method,
            response.status_code,
            response.text
        )

        try:

            return response.json()

        except Exception:

            return None

    except requests.RequestException:

        logging.exception(
            "Telegram API request failed"
        )

        return None


# =========================================================
# VERIFY TELEGRAM MINI APP
# =========================================================

def verify(init_data):

    if not TOKEN:

        logging.error(
            "Cannot verify Telegram user: BOT_TOKEN missing"
        )

        return None


    if not init_data:

        logging.warning(
            "Telegram initData is empty"
        )

        return None


    if not isinstance(init_data, str):

        logging.warning(
            "Telegram initData is not a string"
        )

        return None


    if len(init_data) > 8192:

        logging.warning(
            "Telegram initData is too large"
        )

        return None


    try:

        parsed_pairs = parse_qsl(
            init_data,
            keep_blank_values=True,
            strict_parsing=False
        )

        data = dict(parsed_pairs)

    except Exception:

        logging.exception(
            "Could not parse Telegram initData"
        )

        return None


    received_hash = data.pop(
        "hash",
        None
    )


    if not received_hash:

        logging.warning(
            "Telegram initData has no hash"
        )

        return None


    # Telegram may include signature.
    # It stays in data_check_string.
    # Only hash is removed.

    data_check_string = "\n".join(

        f"{key}={value}"

        for key, value in sorted(
            data.items()
        )
    )


    # Telegram Web App validation:
    #
    # secret_key =
    # HMAC_SHA256(
    #     key="WebAppData",
    #     msg=BOT_TOKEN
    # )

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
    # CHECK AUTH DATE
    # -----------------------------------------------------

    try:

        auth_date = int(
            data.get(
                "auth_date",
                "0"
            )
        )

    except (TypeError, ValueError):

        logging.warning(
            "Invalid Telegram auth_date"
        )

        return None


    if auth_date <= 0:

        logging.warning(
            "Telegram auth_date missing"
        )

        return None


    now_timestamp = int(
        time.time()
    )


    age = (
        now_timestamp -
        auth_date
    )


    # Allow Telegram session for 24 hours.
    # Also tolerate a small clock difference.

    if age > 86400:

        logging.warning(
            "Telegram initData expired. age=%s",
            age
        )

        return None


    if age < -300:

        logging.warning(
            "Telegram initData auth_date is in future. age=%s",
            age
        )

        return None


    # -----------------------------------------------------
    # USER
    # -----------------------------------------------------

    raw_user = data.get(
        "user",
        ""
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

    except Exception:

        logging.exception(
            "Could not parse Telegram user"
        )

        return None


    if not user.get("id"):

        logging.warning(
            "Telegram user ID missing"
        )

        return None


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
# HEALTH CHECK
# =========================================================

@app.route("/health")
def health():

    return jsonify({

        "ok": True,

        "bot_token":
            bool(TOKEN),

        "admin_chat":
            bool(ADMIN),

        "app_url":
            URL,

        "database":
            DB,

        "menu_items":
            len(MENU),

        "timezone":
            "Europe/Belgrade",

        "opening":
            f"{OPEN_HOUR:02d}:00",

        "closing":
            f"{CLOSE_HOUR:02d}:00"

    })


# =========================================================
# MENU API
# =========================================================

@app.route("/api/menu")
def menu_api():

    response = jsonify(
        MENU
    )

    response.headers[
        "Cache-Control"
    ] = "no-store, no-cache, must-revalidate, max-age=0"

    return response


# =========================================================
# CREATE ORDER
# =========================================================

@app.route(
    "/api/order",
    methods=["POST"]
)
def create_order():

    # -----------------------------------------------------
    # JSON
    # -----------------------------------------------------

    data = request.get_json(
        silent=True
    )


    if not isinstance(
        data,
        dict
    ):

        return jsonify({

            "error":
                "Invalid request."

        }), 400


    # -----------------------------------------------------
    # TELEGRAM SESSION
    # -----------------------------------------------------

    init_data = data.get(
        "initData",
        ""
    )


    logging.info(
        "Order request received. initData present=%s length=%s",
        bool(init_data),
        len(init_data) if isinstance(init_data, str) else 0
    )


    user = verify(
        init_data
    )


    if not user:

        return jsonify({

            "error":
                "Telegram session could not be verified. "
                "Please close the ordering window and "
                "open it again from the TAKE AWAY bot."

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
        or len(items) == 0
    ):

        return jsonify({

            "error":
                "Please add at least one drink."

        }), 400


    if len(items) > 20:

        return jsonify({

            "error":
                "Too many different items in one order."

        }), 400


    # -----------------------------------------------------
    # PICKUP
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

            str(pickup_raw).replace(
                "Z",
                "+00:00"
            )
        )


        # If browser somehow sends time
        # without timezone, assume Belgrade.

        if pickup.tzinfo is None:

            pickup = pickup.replace(
                tzinfo=TZ
            )

        else:

            pickup = pickup.astimezone(
                TZ
            )

    except (TypeError, ValueError):

        return jsonify({

            "error":
                "Invalid pickup time."

        }), 400


    now = datetime.now(
        TZ
    )


    minimum_pickup = (

        now +

        timedelta(
            minutes=MIN_PREP_MINUTES
        )
    )


    # Small tolerance because the request
    # may take several seconds to reach server.

    if pickup < (
        minimum_pickup -
        timedelta(seconds=45)
    ):

        return jsonify({

            "error":
                f"Minimum preparation time is "
                f"{MIN_PREP_MINUTES} minutes."

        }), 400


    # Only today's orders for now.

    if pickup.date() != now.date():

        return jsonify({

            "error":
                "Pickup is available for today only."

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
                f"Pickup starts at "
                f"{OPEN_HOUR:02d}:00."

        }), 400


    if pickup >= closing:

        return jsonify({

            "error":
                f"Last pickup must be before "
                f"{CLOSE_HOUR:02d}:00."

        }), 400


    # =====================================================
    # CALCULATE ORDER
    # =====================================================

    menu_by_id = {

        product["id"]:
            product

        for product in MENU

    }


    total = 0

    total_quantity = 0

    clean_items = []

    order_lines = []


    for item in items:

        # -------------------------------------------------
        # ITEM FORMAT
        # -------------------------------------------------

        if not isinstance(
            item,
            dict
        ):

            return jsonify({

                "error":
                    "Invalid order item."

            }), 400


        item_id = str(

            item.get(
                "id",
                ""
            )

        ).strip()


        product = menu_by_id.get(
            item_id
        )


        if not product:

            return jsonify({

                "error":
                    f"Unknown drink: {item_id}"

            }), 400


        # -------------------------------------------------
        # QUANTITY
        # -------------------------------------------------

        try:

            qty = int(

                item.get(
                    "qty",
                    1
                )

            )

        except (TypeError, ValueError):

            return jsonify({

                "error":
                    "Invalid quantity."

            }), 400


        if qty < 1 or qty > 10:

            return jsonify({

                "error":
                    "Quantity must be between 1 and 10."

            }), 400


        total_quantity += qty


        if total_quantity > 30:

            return jsonify({

                "error":
                    "Maximum 30 drinks per order."

            }), 400


        # -------------------------------------------------
        # OPTIONS
        # -------------------------------------------------

        extra_shot = bool(
            item.get(
                "extra_shot",
                False
            )
        )


        boba = bool(
            item.get(
                "boba",
                False
            )
        )


        # Do not trust the browser.
        # Server decides which extras are allowed.

        if (
            extra_shot
            and item_id not in COFFEE_IDS
        ):

            extra_shot = False


        if (
            boba
            and not product.get(
                "boba",
                False
            )
        ):

            boba = False


        # -------------------------------------------------
        # PRICE
        # -------------------------------------------------

        unit_price = int(
            product["price"]
        )


        extras = []


        if extra_shot:

            unit_price += 150

            extras.append(
                "Extra espresso shot"
            )


        if boba:

            unit_price += 100

            extras.append(
                "Boba"
            )


        line_total = (
            unit_price *
            qty
        )


        total += line_total


        # -------------------------------------------------
        # ORDER LINE
        # -------------------------------------------------

        options_text = ""


        if extras:

            options_text = (

                "\n   + " +

                ", ".join(
                    extras
                )
            )


        order_lines.append(

            f"• {qty} × "
            f"{product['name']}"
            f"{options_text}\n"
            f"   {line_total} RSD"

        )


        clean_items.append({

            "id":
                item_id,

            "name":
                product["name"],

            "qty":
                qty,

            "extra_shot":
                extra_shot,

            "boba":
                boba,

            "unit_price":
                unit_price,

            "line_total":
                line_total

        })


    # =====================================================
    # CUSTOMER
    # =====================================================

    telegram_id = str(
        user.get(
            "id",
            ""
        )
    )


    first_name = str(
        user.get(
            "first_name",
            ""
        )
    ).strip()


    last_name = str(
        user.get(
            "last_name",
            ""
        )
    ).strip()


    username = str(
        user.get(
            "username",
            ""
        )
    ).strip()


    customer_name = (

        f"{first_name} "
        f"{last_name}"

    ).strip()


    if not customer_name:

        if username:

            customer_name = (
                f"@{username}"
            )

        else:

            customer_name = (
                f"Telegram {telegram_id}"
            )


    customer_display = (
        customer_name
    )


    if username and (
        f"@{username}" not in customer_display
    ):

        customer_display += (
            f" (@{username})"
        )


    # =====================================================
    # TIME
    # =====================================================

    pickup_text = pickup.strftime(
        "%H:%M"
    )


    created = now.isoformat()


    # =====================================================
    # SAVE ORDER
    # =====================================================

    try:

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

            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,

            (

                telegram_id,

                customer_name,

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
        connection.close()


    except Exception:

        logging.exception(
            "Could not save order"
        )


        try:

            connection.close()

        except Exception:

            pass


        return jsonify({

            "error":
                "Could not save the order. "
                "Please try again."

        }), 500


    # =====================================================
    # BARISTA / ADMIN MESSAGE
    # =====================================================

    lines_text = "\n\n".join(
        order_lines
    )


    admin_message = (

        "🔔 NEW TAKE AWAY ORDER\n\n"

        f"🧾 Order #{order_id}\n"
        f"👤 {customer_display}\n"
        f"🕐 PICKUP: {pickup_text}\n\n"

        "──────────────\n\n"

        f"{lines_text}\n\n"

        "──────────────\n\n"

        f"💰 TOTAL: {total} RSD\n\n"

        "💳 Payment: at pickup"

    )


    if ADMIN:

        admin_result = tg(

            "sendMessage",

            {

                "chat_id":
                    ADMIN,

                "text":
                    admin_message

            }

        )


        if not (
            isinstance(admin_result, dict)
            and admin_result.get("ok")
        ):

            logging.error(
                "Could not send order #%s to ADMIN_CHAT_ID",
                order_id
            )

    else:

        logging.warning(
            "ADMIN_CHAT_ID is not configured. "
            "Order #%s saved but barista notification was not sent.",
            order_id
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


    customer_result = tg(

        "sendMessage",

        {

            "chat_id":
                telegram_id,

            "text":
                customer_message

        }

    )


    if not (
        isinstance(customer_result, dict)
        and customer_result.get("ok")
    ):

        logging.warning(
            "Order #%s created, but Telegram confirmation "
            "could not be sent to customer.",
            order_id
        )


    # =====================================================
    # RESPONSE TO MINI APP
    # =====================================================

    return jsonify({

        "success":
            True,

        "order_id":
            order_id,

        "total":
            total,

        "pickup":
            pickup_text

    }), 200


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


    text = str(
        message.get(
            "text",
            ""
        )
    ).strip()


    if not chat_id:

        return "", 200


    # -----------------------------------------------------
    # /START
    # -----------------------------------------------------

    if text.startswith(
        "/start"
    ):

        if not URL:

            tg(

                "sendMessage",

                {

                    "chat_id":
                        chat_id,

                    "text":
                        "TAKE AWAY ordering is temporarily unavailable."

                }

            )

            return "", 200


        tg(

            "sendMessage",

            {

                "chat_id":
                    chat_id,

                "text":
                    "Welcome to TAKE AWAY ☕\n\n"
                    "Thai Tea & Coffee\n\n"
                    "Choose your drinks, select a pickup time "
                    "and we will prepare everything for you.",

                "reply_markup": {

                    "keyboard": [

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

                    ],

                    "resize_keyboard":
                        True,

                    "is_persistent":
                        True

                }

            }

        )


    return "", 200


# =========================================================
# ERROR HANDLERS
# =========================================================

@app.errorhandler(404)
def not_found(error):

    return jsonify({

        "error":
            "Not found."

    }), 404


@app.errorhandler(500)
def internal_error(error):

    logging.exception(
        "Internal server error: %s",
        error
    )

    return jsonify({

        "error":
            "Internal server error."

    }), 500


# =========================================================
# LOCAL / RENDER
# =========================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )


    logging.info(
        "Starting TAKE AWAY on port %s",
        port
    )


    logging.info(
        "BOT_TOKEN configured: %s",
        bool(TOKEN)
    )


    logging.info(
        "ADMIN_CHAT_ID configured: %s",
        bool(ADMIN)
    )


    logging.info(
        "APP_URL: %s",
        URL
    )


    app.run(

        host="0.0.0.0",

        port=port,

        debug=False

    )
