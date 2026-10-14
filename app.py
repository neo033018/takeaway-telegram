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

# Telegram group where you + barista receive orders
ADMIN = os.environ.get("ORDER_CHAT_ID", "")

DB = os.environ.get("DATABASE_PATH", "orders.db")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

TZ = ZoneInfo("Europe/Belgrade")

MIN_PREP_MINUTES = 10
OPEN_HOUR = 7
CLOSE_HOUR = 17

MAX_ITEM_QUANTITY = 10
MAX_ORDER_QUANTITY = 30


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

    connection = sqlite3.connect(
        DB,
        timeout=15
    )

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

        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(orders)"
            ).fetchall()
        }

        # Existing installations may not have these columns.
        if "payment_status" not in columns:

            connection.execute(
                """
                ALTER TABLE orders
                ADD COLUMN payment_status TEXT DEFAULT 'unpaid'
                """
            )

        if "payment_provider" not in columns:

            connection.execute(
                """
                ALTER TABLE orders
                ADD COLUMN payment_provider TEXT DEFAULT 'pickup'
                """
            )

        if "payment_method" not in columns:

            connection.execute(
                """
                ALTER TABLE orders
                ADD COLUMN payment_method TEXT DEFAULT 'pickup'
                """
            )

        connection.commit()

    finally:

        connection.close()


init_db()


# =========================================================
# DATABASE HELPERS
# =========================================================

def get_order(order_id):

    connection = get_db()

    try:

        row = connection.execute(
            """
            SELECT *
            FROM orders
            WHERE id = ?
            """,
            (order_id,)
        ).fetchone()

        if row:
            return dict(row)

        return None

    finally:

        connection.close()


def transition_order_status(
    order_id,
    expected_status,
    new_status
):

    connection = get_db()

    try:

        cursor = connection.execute(
            """
            UPDATE orders
            SET status = ?
            WHERE id = ?
            AND status = ?
            """,
            (
                new_status,
                order_id,
                expected_status
            )
        )

        connection.commit()

        return cursor.rowcount == 1

    finally:

        connection.close()


def cancel_order_status(order_id):

    connection = get_db()

    try:

        cursor = connection.execute(
            """
            UPDATE orders
            SET status = 'cancelled'
            WHERE id = ?
            AND status IN ('new', 'preparing')
            """,
            (order_id,)
        )

        connection.commit()

        return cursor.rowcount == 1

    finally:

        connection.close()


def get_customer_orders(
    telegram_id,
    limit=50
):

    connection = get_db()

    try:

        rows = connection.execute(
            """
            SELECT *
            FROM orders
            WHERE telegram_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (
                str(telegram_id),
                int(limit)
            )
        ).fetchall()

        return [
            dict(row)
            for row in rows
        ]

    finally:

        connection.close()


# =========================================================
# TELEGRAM API
# =========================================================

def tg(method, payload):

    if not TOKEN:

        logging.warning(
            "BOT_TOKEN missing"
        )

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

        logging.exception(
            "Telegram API error"
        )

        return None


# =========================================================
# CUSTOMER ORDER BUTTON
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
# BARISTA BUTTONS
# =========================================================

def new_order_keyboard(order_id):

    return {
        "inline_keyboard": [
            [
                {
                    "text": "✅ ACCEPT ORDER",
                    "callback_data": f"accept:{order_id}"
                }
            ],
            [
                {
                    "text": "❌ CANCEL",
                    "callback_data": f"cancel:{order_id}"
                }
            ]
        ]
    }


def preparing_keyboard(order_id):

    return {
        "inline_keyboard": [
            [
                {
                    "text": "☕ ORDER READY",
                    "callback_data": f"ready:{order_id}"
                }
            ],
            [
                {
                    "text": "❌ CANCEL",
                    "callback_data": f"cancel:{order_id}"
                }
            ]
        ]
    }


def ready_keyboard(order_id):

    return {
        "inline_keyboard": [
            [
                {
                    "text": "✅ PICKED UP",
                    "callback_data": f"picked:{order_id}"
                }
            ]
        ]
    }


# =========================================================
# ORDER HELPERS
# =========================================================

def parse_order_details(order):

    try:

        details = json.loads(
            order.get(
                "details",
                "[]"
            )
        )

        if isinstance(details, list):
            return details

    except Exception:
        pass

    return []


def order_details_text(order):

    details = parse_order_details(order)

    lines = []

    for item in details:

        qty = item.get("qty", 1)
        name = item.get("name", "Drink")
        line_total = item.get("line_total", 0)

        extras = []

        if item.get("extra_shot"):
            extras.append("extra espresso shot")

        if item.get("boba"):
            extras.append("boba")

        extra_text = ""

        if extras:
            extra_text = " + " + ", ".join(extras)

        lines.append(
            f"{qty}x "
            f"{name}"
            f"{extra_text} - "
            f"{line_total} RSD"
        )

    return "\n".join(lines)


def pickup_time_text(order):

    try:

        pickup = datetime.fromisoformat(
            order["pickup"]
        )

        if pickup.tzinfo is not None:

            pickup = pickup.astimezone(TZ)

        return pickup.strftime("%H:%M")

    except Exception:

        return str(
            order.get(
                "pickup",
                ""
            )
        )


def created_date_text(order):

    try:

        created = datetime.fromisoformat(
            order["created"]
        )

        if created.tzinfo is not None:

            created = created.astimezone(TZ)

        return created.strftime(
            "%d.%m.%Y"
        )

    except Exception:

        return ""


def serialize_order(order):

    return {
        "id": order["id"],
        "pickup": pickup_time_text(order),
        "pickup_iso": order.get("pickup"),
        "created": created_date_text(order),
        "created_iso": order.get("created"),
        "total": order.get("total", 0),
        "status": order.get(
            "status",
            "new"
        ),
        "payment_method": order.get(
            "payment_method",
            "pickup"
        ),
        "payment_status": order.get(
            "payment_status",
            "unpaid"
        ),
        "items": parse_order_details(order)
    }


# =========================================================
# BARISTA MESSAGE
# =========================================================

def barista_order_text(
    order,
    status
):

    pickup_text = pickup_time_text(order)
    details_text = order_details_text(order)

    if status == "new":
        title = "🔔 TAKE AWAY - NEW ORDER"

    elif status == "preparing":
        title = "🟡 TAKE AWAY - PREPARING"

    elif status == "ready":
        title = "🟢 TAKE AWAY - READY"

    elif status == "completed":
        title = "✅ TAKE AWAY - COMPLETED"

    elif status == "cancelled":
        title = "❌ TAKE AWAY - CANCELLED"

    else:
        title = "☕ TAKE AWAY"

    text = (
        f"{title}\n\n"
        f"🧾 Order #{order['id']}\n"
        f"👤 Customer: {order['customer']}\n"
        f"🕐 Pickup: {pickup_text}\n\n"
        f"{details_text}\n\n"
        f"💰 TOTAL: {order['total']} RSD\n"
        f"💵 Payment: PAY AT PICKUP - UNPAID"
    )

    if status == "ready":

        text += (
            "\n\n"
            "📲 Customer notified"
        )

    if status == "completed":

        text += (
            "\n\n"
            "✅ Order completed"
        )

    if status == "cancelled":

        text += (
            "\n\n"
            "📲 Customer notified"
        )

    return text


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

        logging.warning(
            "Missing or invalid initData"
        )

        return None

    try:

        data = dict(
            parse_qsl(
                init_data,
                keep_blank_values=True
            )
        )

        received_hash = data.pop(
            "hash",
            None
        )

        if not received_hash:

            logging.warning(
                "Missing Telegram hash"
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
                "Telegram hash mismatch"
            )

            return None

        auth_date = int(
            data.get(
                "auth_date",
                "0"
            )
        )

        age = int(time.time()) - auth_date

        if (
            age > 86400
            or age < -300
        ):

            logging.warning(
                "Telegram session expired"
            )

            return None

        user = json.loads(
            data.get(
                "user",
                "{}"
            )
        )

        if not isinstance(user, dict):
            return None

        if not user.get("id"):
            return None

        return user

    except Exception:

        logging.exception(
            "Telegram verification error"
        )

        return None


# =========================================================
# GET USER FROM REQUEST
# =========================================================

def verified_user_from_request():

    data = request.get_json(
        silent=True
    )

    if not isinstance(data, dict):
        return None, {}

    user = verify(
        data.get(
            "initData",
            ""
        )
    )

    return user, data


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
        "bot_token": bool(TOKEN),
        "order_chat": bool(ADMIN),
        "app_url": URL,
        "menu_items": len(MENU),
        "payment_mode": "pickup"
    })


# =========================================================
# MENU
# =========================================================

@app.route("/api/menu")
def menu():

    return jsonify(MENU)


# =========================================================
# PROFILE
# =========================================================

@app.route(
    "/api/profile",
    methods=["POST"]
)
def profile():

    user, data = verified_user_from_request()

    if not user:

        return jsonify({
            "error":
                "Telegram session "
                "could not be verified."
        }), 401

    telegram_id = str(
        user["id"]
    )

    orders = get_customer_orders(
        telegram_id,
        500
    )

    completed_orders = [
        order
        for order in orders
        if order.get("status") == "completed"
    ]

    drinks = 0
    drink_counts = {}

    for order in completed_orders:

        for item in parse_order_details(order):

            qty = int(
                item.get(
                    "qty",
                    1
                )
            )

            drinks += qty

            item_id = item.get(
                "id",
                ""
            )

            if item_id:

                drink_counts[item_id] = (
                    drink_counts.get(
                        item_id,
                        0
                    )
                    + qty
                )

    favorite = None

    if drink_counts:

        favorite_id = max(
            drink_counts,
            key=drink_counts.get
        )

        product = MENU_BY_ID.get(
            favorite_id
        )

        if product:

            favorite = {
                "id": favorite_id,
                "name": product["name"]
            }

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

    name = (
        f"{first_name} "
        f"{last_name}"
    ).strip()

    username = str(
        user.get(
            "username",
            ""
        )
    ).strip()

    first_order = ""

    if orders:

        oldest = orders[-1]

        first_order = created_date_text(
            oldest
        )

    return jsonify({
        "telegram_id": telegram_id,
        "name": name or "TAKE AWAY Member",
        "first_name": first_name,
        "username": username,
        "orders": len(orders),
        "completed_orders": len(completed_orders),
        "drinks": drinks,
        "favorite": favorite,
        "first_order": first_order
    })


# =========================================================
# CUSTOMER ORDERS
# =========================================================

@app.route(
    "/api/orders",
    methods=["POST"]
)
def customer_orders():

    user, data = verified_user_from_request()

    if not user:

        return jsonify({
            "error":
                "Telegram session "
                "could not be verified."
        }), 401

    orders = get_customer_orders(
        str(user["id"]),
        50
    )

    return jsonify({
        "orders": [
            serialize_order(order)
            for order in orders
        ]
    })


# =========================================================
# CREATE ORDER - PAY AT PICKUP
# =========================================================

@app.route(
    "/api/order",
    methods=["POST"]
)
def create_order():

    data = request.get_json(
        silent=True
    )

    if not isinstance(data, dict):

        return jsonify({
            "error": "Invalid request."
        }), 400

    # -----------------------------------------------------
    # PAYMENT METHOD
    # -----------------------------------------------------

    payment_method = str(
        data.get(
            "payment_method",
            ""
        )
    ).strip().lower()

    # For now only real pickup orders are enabled.
    # Online payment does NOT create an order.
    if payment_method != "pickup":

        return jsonify({
            "error":
                "Online payment is coming soon. "
                "Please choose Pay at pickup.",
            "payment_pending": True
        }), 503

    # -----------------------------------------------------
    # VERIFY TELEGRAM
    # -----------------------------------------------------

    user = verify(
        data.get(
            "initData",
            ""
        )
    )

    if not user:

        return jsonify({
            "error":
                "Telegram session could "
                "not be verified. "
                "Please close the app "
                "and reopen it using "
                "the ORDER button "
                "in the bot."
        }), 401

    items = data.get(
        "items",
        []
    )

    pickup_raw = data.get(
        "pickup"
    )

    if (
        not isinstance(items, list)
        or not items
        or len(items) > 20
    ):

        return jsonify({
            "error":
                "Please add at least "
                "one drink."
        }), 400

    if not isinstance(
        pickup_raw,
        str
    ):

        return jsonify({
            "error":
                "Please choose a "
                "pickup time."
        }), 400

    # -----------------------------------------------------
    # PICKUP TIME
    # -----------------------------------------------------

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

        else:

            pickup = pickup.astimezone(
                TZ
            )

    except (
        ValueError,
        TypeError
    ):

        return jsonify({
            "error":
                "Invalid pickup time."
        }), 400

    now = datetime.now(TZ)

    minimum_pickup = (
        now
        + timedelta(
            minutes=MIN_PREP_MINUTES
        )
    )

    if pickup < (
        minimum_pickup
        - timedelta(seconds=30)
    ):

        return jsonify({
            "error":
                f"Minimum preparation "
                f"time is "
                f"{MIN_PREP_MINUTES} "
                f"minutes."
        }), 400

    if pickup.date() != now.date():

        return jsonify({
            "error":
                "Pickup is available "
                "today only."
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
                f"Last pickup must be "
                f"before "
                f"{CLOSE_HOUR:02d}:00."
        }), 400

    # -----------------------------------------------------
    # VALIDATE ITEMS AND CALCULATE PRICE SERVER-SIDE
    # -----------------------------------------------------

    total = 0
    total_quantity = 0
    clean_items = []

    for item in items:

        if not isinstance(
            item,
            dict
        ):

            return jsonify({
                "error":
                    "Invalid order item."
            }), 400

        item_id = item.get(
            "id"
        )

        if not isinstance(
            item_id,
            str
        ):

            return jsonify({
                "error":
                    "Invalid drink ID."
            }), 400

        product = MENU_BY_ID.get(
            item_id
        )

        if not product:

            return jsonify({
                "error":
                    f"Unknown drink: "
                    f"{item_id}"
            }), 400

        qty = item.get(
            "qty",
            1
        )

        if (
            isinstance(qty, bool)
            or not isinstance(qty, int)
        ):

            return jsonify({
                "error":
                    "Invalid quantity."
            }), 400

        if (
            qty < 1
            or qty > MAX_ITEM_QUANTITY
        ):

            return jsonify({
                "error":
                    "Quantity must be "
                    "between 1 and 10."
            }), 400

        total_quantity += qty

        if total_quantity > MAX_ORDER_QUANTITY:

            return jsonify({
                "error":
                    "Maximum 30 drinks "
                    "per order."
            }), 400

        extra_shot = (
            item.get("extra_shot") is True
            and item_id in COFFEE_IDS
        )

        boba = (
            item.get("boba") is True
            and product.get(
                "boba",
                False
            )
        )

        unit_price = int(
            product["price"]
        )

        if extra_shot:
            unit_price += 150

        if boba:
            unit_price += 100

        line_total = (
            unit_price *
            qty
        )

        total += line_total

        clean_items.append({
            "id": item_id,
            "name": product["name"],
            "qty": qty,
            "extra_shot": extra_shot,
            "boba": boba,
            "unit_price": unit_price,
            "line_total": line_total
        })

    # -----------------------------------------------------
    # CUSTOMER
    # -----------------------------------------------------

    telegram_id = str(
        user["id"]
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

    customer = (
        f"{first_name} "
        f"{last_name}"
    ).strip()

    if not customer:

        customer = (
            f"@{username}"
            if username
            else telegram_id
        )

    pickup_text = pickup.strftime(
        "%H:%M"
    )

    # -----------------------------------------------------
    # SAVE ORDER
    # -----------------------------------------------------

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
                created,
                payment_status,
                payment_provider,
                payment_method
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                now.isoformat(),
                "unpaid",
                "pickup",
                "pickup"
            )
        )

        order_id = cursor.lastrowid

        connection.commit()

    finally:

        connection.close()

    # -----------------------------------------------------
    # SEND ORDER TO BARISTA GROUP
    # -----------------------------------------------------

    order = get_order(
        order_id
    )

    if ADMIN and order:

        result = tg(
            "sendMessage",
            {
                "chat_id": ADMIN,

                "text":
                    barista_order_text(
                        order,
                        "new"
                    ),

                "reply_markup":
                    new_order_keyboard(
                        order_id
                    )
            }
        )

        if (
            not result
            or not result.get("ok")
        ):

            logging.error(
                "Could not send order #%s "
                "to ORDER_CHAT_ID %s",
                order_id,
                ADMIN
            )

    else:

        logging.warning(
            "Order #%s saved but "
            "ORDER_CHAT_ID is missing.",
            order_id
        )

    # -----------------------------------------------------
    # CUSTOMER CONFIRMATION
    # -----------------------------------------------------

    customer_message = (
        "✅ TAKE AWAY - ORDER RECEIVED\n\n"
        f"🧾 Order #{order_id}\n"
        f"🕐 Pickup: {pickup_text}\n"
        f"💰 Total: {total} RSD\n\n"
        "We received your order.\n"
        "Please wait for confirmation. ☕\n\n"
        "💵 PAY AT PICKUP\n"
        "Pay by card or cash at the cafe.\n\n"
        "🇷🇸 Porudžbina je primljena.\n"
        "Sačekajte potvrdu.\n"
        "Plaćanje pri preuzimanju."
    )

    tg(
        "sendMessage",
        {
            "chat_id":
                telegram_id,
            "text":
                customer_message
        }
    )

    return jsonify({
        "success": True,
        "order_id": order_id,
        "total": total,
        "pickup": pickup_text,
        "payment_method": "pickup",
        "payment_status": "unpaid"
    })


# =========================================================
# HANDLE BARISTA CALLBACK
# =========================================================

def handle_callback(callback):

    callback_id = callback.get(
        "id"
    )

    callback_data = str(
        callback.get(
            "data",
            ""
        )
    )

    message = (
        callback.get(
            "message"
        )
        or {}
    )

    chat = (
        message.get(
            "chat"
        )
        or {}
    )

    callback_chat_id = str(
        chat.get(
            "id",
            ""
        )
    )

    message_id = message.get(
        "message_id"
    )

    # -----------------------------------------------------
    # ONLY BARISTA GROUP
    # -----------------------------------------------------

    if (
        not ADMIN
        or callback_chat_id != str(ADMIN)
    ):

        if callback_id:

            tg(
                "answerCallbackQuery",
                {
                    "callback_query_id":
                        callback_id,
                    "text":
                        "Not allowed.",
                    "show_alert":
                        True
                }
            )

        return

    try:

        action, order_id_raw = (
            callback_data.split(
                ":",
                1
            )
        )

        order_id = int(
            order_id_raw
        )

    except Exception:

        if callback_id:

            tg(
                "answerCallbackQuery",
                {
                    "callback_query_id":
                        callback_id,
                    "text":
                        "Invalid action."
                }
            )

        return

    order = get_order(
        order_id
    )

    if not order:

        tg(
            "answerCallbackQuery",
            {
                "callback_query_id":
                    callback_id,
                "text":
                    "Order not found.",
                "show_alert":
                    True
            }
        )

        return

    # -----------------------------------------------------
    # ACCEPT
    # -----------------------------------------------------

    if action == "accept":

        changed = transition_order_status(
            order_id,
            "new",
            "preparing"
        )

        if not changed:

            tg(
                "answerCallbackQuery",
                {
                    "callback_query_id":
                        callback_id,
                    "text":
                        "Order already updated."
                }
            )

            return

        order = get_order(
            order_id
        )

        tg(
            "editMessageText",
            {
                "chat_id":
                    callback_chat_id,

                "message_id":
                    message_id,

                "text":
                    barista_order_text(
                        order,
                        "preparing"
                    ),

                "reply_markup":
                    preparing_keyboard(
                        order_id
                    )
            }
        )

        tg(
            "sendMessage",
            {
                "chat_id":
                    order["telegram_id"],

                "text":
                    "✅ TAKE AWAY - "
                    "ORDER ACCEPTED\n\n"
                    f"🧾 Order #{order_id}\n"
                    f"🕐 Pickup: "
                    f"{pickup_time_text(order)}"
                    "\n\n"
                    "We're preparing "
                    "your drinks. ☕\n\n"
                    "💵 Pay at pickup.\n\n"
                    "🇷🇸 Porudžbina je "
                    "prihvaćena i "
                    "pripremamo vaša pića.\n"
                    "Plaćanje pri preuzimanju."
            }
        )

        tg(
            "answerCallbackQuery",
            {
                "callback_query_id":
                    callback_id,
                "text":
                    "Order accepted ✅"
            }
        )

        return

    # -----------------------------------------------------
    # READY
    # -----------------------------------------------------

    if action == "ready":

        changed = transition_order_status(
            order_id,
            "preparing",
            "ready"
        )

        if not changed:

            tg(
                "answerCallbackQuery",
                {
                    "callback_query_id":
                        callback_id,
                    "text":
                        "Order is not "
                        "being prepared."
                }
            )

            return

        order = get_order(
            order_id
        )

        tg(
            "editMessageText",
            {
                "chat_id":
                    callback_chat_id,

                "message_id":
                    message_id,

                "text":
                    barista_order_text(
                        order,
                        "ready"
                    ),

                "reply_markup":
                    ready_keyboard(
                        order_id
                    )
            }
        )

        tg(
            "sendMessage",
            {
                "chat_id":
                    order["telegram_id"],

                "text":
                    "☕ TAKE AWAY - "
                    "YOUR ORDER IS READY!\n\n"
                    f"🧾 Order #{order_id}\n"
                    f"💰 {order['total']} RSD\n\n"
                    "Your drinks are ready "
                    "for pickup. 🤍\n\n"
                    "💵 Please pay by card "
                    "or cash at pickup.\n\n"
                    "🇷🇸 Vaša porudžbina "
                    "je spremna za "
                    "preuzimanje.\n"
                    "Plaćanje pri preuzimanju."
            }
        )

        tg(
            "answerCallbackQuery",
            {
                "callback_query_id":
                    callback_id,
                "text":
                    "Customer notified ☕"
            }
        )

        return

    # -----------------------------------------------------
    # PICKED UP
    # -----------------------------------------------------

    if action == "picked":

        changed = transition_order_status(
            order_id,
            "ready",
            "completed"
        )

        if not changed:

            tg(
                "answerCallbackQuery",
                {
                    "callback_query_id":
                        callback_id,
                    "text":
                        "Order is not "
                        "ready or was "
                        "already completed."
                }
            )

            return

        order = get_order(
            order_id
        )

        tg(
            "editMessageText",
            {
                "chat_id":
                    callback_chat_id,

                "message_id":
                    message_id,

                "text":
                    barista_order_text(
                        order,
                        "completed"
                    )
            }
        )

        tg(
            "answerCallbackQuery",
            {
                "callback_query_id":
                    callback_id,
                "text":
                    "Order completed ✅"
            }
        )

        return

    # -----------------------------------------------------
    # CANCEL
    # -----------------------------------------------------

    if action == "cancel":

        changed = cancel_order_status(
            order_id
        )

        if not changed:

            tg(
                "answerCallbackQuery",
                {
                    "callback_query_id":
                        callback_id,
                    "text":
                        "This order cannot "
                        "be cancelled now."
                }
            )

            return

        order = get_order(
            order_id
        )

        tg(
            "editMessageText",
            {
                "chat_id":
                    callback_chat_id,

                "message_id":
                    message_id,

                "text":
                    barista_order_text(
                        order,
                        "cancelled"
                    )
            }
        )

        tg(
            "sendMessage",
            {
                "chat_id":
                    order["telegram_id"],

                "text":
                    "❌ TAKE AWAY - "
                    "ORDER CANCELLED\n\n"
                    f"🧾 Order #{order_id}\n\n"
                    "Unfortunately, "
                    "we cannot prepare "
                    "this order.\n\n"
                    "🇷🇸 Nažalost, "
                    "ne možemo pripremiti "
                    "ovu porudžbinu.\n\n"
                    "Sorry for the "
                    "inconvenience."
            }
        )

        tg(
            "answerCallbackQuery",
            {
                "callback_query_id":
                    callback_id,
                "text":
                    "Order cancelled"
            }
        )

        return

    tg(
        "answerCallbackQuery",
        {
            "callback_query_id":
                callback_id,
            "text":
                "Unknown action."
        }
    )


# =========================================================
# TELEGRAM WEBHOOK
# =========================================================

@app.route(
    "/api/bot-webhook",
    methods=["POST"]
)
def bot_webhook():

    if WEBHOOK_SECRET:

        received_secret = (
            request.headers.get(
                "X-Telegram-Bot-Api-Secret-Token",
                ""
            )
        )

        if not hmac.compare_digest(
            received_secret,
            WEBHOOK_SECRET
        ):

            return "", 403

    update = (
        request.get_json(
            silent=True
        )
        or {}
    )

    if not isinstance(
        update,
        dict
    ):

        return "", 200

    # CALLBACKS

    callback = update.get(
        "callback_query"
    )

    if callback:

        try:

            handle_callback(
                callback
            )

        except Exception:

            logging.exception(
                "Callback error"
            )

        return "", 200

    # NORMAL MESSAGE

    message = (
        update.get(
            "message"
        )
        or {}
    )

    chat = (
        message.get(
            "chat"
        )
        or {}
    )

    chat_id = chat.get(
        "id"
    )

    chat_type = chat.get(
        "type"
    )

    message_text = str(
        message.get(
            "text",
            ""
        )
    ).strip()

    if (
        not chat_id
        or chat_type != "private"
    ):

        return "", 200

    command = (
        message_text
        .split()[0]
        .split("@")[0]
        if message_text
        else ""
    )

    # -----------------------------------------------------
    # START
    # -----------------------------------------------------

    if command == "/start":

        tg(
            "sendMessage",
            {
                "chat_id":
                    chat_id,

                "text":
                    "☕ TAKE AWAY",

                "reply_markup": {
                    "remove_keyboard":
                        True
                }
            }
        )

        welcome_message = (
            "☕ WELCOME TO TAKE AWAY\n\n"
            "THAI TEA & COFFEE\n\n"
            "Choose your drinks, "
            "select a pickup time "
            "and we'll prepare "
            "everything for you.\n\n"
            "💵 Pay at pickup "
            "by card or cash.\n\n"
            "🇷🇸 DOBRODOŠLI U "
            "TAKE AWAY\n\n"
            "Izaberite piće i vreme "
            "preuzimanja, a mi ćemo "
            "pripremiti porudžbinu.\n\n"
            "💵 Plaćanje pri "
            "preuzimanju.\n\n"
            "👇 ORDER / PORUČI"
        )

        tg(
            "sendMessage",
            {
                "chat_id":
                    chat_id,

                "text":
                    welcome_message,

                "reply_markup":
                    order_keyboard()
            }
        )

        return "", 200

    # -----------------------------------------------------
    # HELP
    # -----------------------------------------------------

    if command == "/help":

        tg(
            "sendMessage",
            {
                "chat_id":
                    chat_id,

                "text":
                    "☕ TAKE AWAY\n\n"
                    "Choose your drinks "
                    "and pickup time.\n"
                    "Pickup: 07:00-17:00\n"
                    "Payment: at pickup "
                    "by card or cash.\n\n"
                    "🇷🇸 Izaberite piće "
                    "i vreme preuzimanja.\n"
                    "Preuzimanje: "
                    "07:00-17:00\n"
                    "Plaćanje pri "
                    "preuzimanju.",

                "reply_markup":
                    order_keyboard()
            }
        )

        return "", 200

    return "", 200


# =========================================================
# LOCAL RUN
# =========================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=int(
            os.environ.get(
                "PORT",
                10000
            )
        )
    )
