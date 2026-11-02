import os
import json
import hmac
import hashlib
import sqlite3
import logging
import requests

from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import parse_qsl
from zoneinfo import ZoneInfo

from flask import (
    Flask,
    request,
    jsonify,
    render_template,
    Response
)

# ============================================================
# TAKE AWAY — THAI TEA & COFFEE
# TELEGRAM MINI APP BACKEND
# ============================================================

app = Flask(__name__)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("takeaway")

# ============================================================
# CONFIGURATION
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
APP_URL = os.getenv("APP_URL", "").rstrip("/")
ORDER_CHAT_ID = os.getenv("ORDER_CHAT_ID", "")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

DATABASE_PATH = os.getenv("DATABASE_PATH", "orders.db")

TIMEZONE = ZoneInfo("Europe/Belgrade")

# CONFIRMED OPENING HOURS
OPEN_HOUR = 8
CLOSE_HOUR = 17

MIN_PREP_MINUTES = 10

BOBA_PRICE = 100
EXTRA_SHOT_PRICE = 150
SYRUP_PRICE = 0

LOYALTY_REQUIRED = 9

MAX_ITEM_QUANTITY = 10
MAX_ORDER_QUANTITY = 30

# ============================================================
# MENU
# ============================================================

MENU = [
    {
        "id": "thai",
        "name": "Classic Thai Milk Tea",
        "price": 450,
        "category": "tea",
        "boba": True,
        "temperature": True
    },
    {
        "id": "green",
        "name": "Thai Green Milk Tea",
        "price": 450,
        "category": "tea",
        "boba": True,
        "temperature": True
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
        "boba": False,
        "temperature": True,
        "iced_surcharge": 20
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
        "boba": False,
        "temperature": True,
        "iced_surcharge": 20
    },
    {
        "id": "flatwhite",
        "name": "Flat White",
        "price": 450,
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
        "price": 550,
        "category": "special",
        "boba": False
    },
    {
        "id": "matchalatte",
        "name": "Matcha Latte",
        "price": 480,
        "category": "special",
        "boba": False,
        "temperature": True,
        "iced_surcharge": 20
    },
    {
        "id": "dirtymatcha",
        "name": "Dirty Matcha Latte",
        "price": 550,
        "category": "special",
        "boba": False,
        "temperature": True,
        "iced_surcharge": 20
    },
    {
        "id": "pumpkinlatte",
        "name": "Pumpkin Latte",
        "price": 480,
        "category": "special",
        "boba": False,
        "temperature": True,
        "iced_surcharge": 20
    },
    {
        "id": "kuvanovino",
        "name": "Kuvano Vino",
        "price": 400,
        "category": "special",
        "boba": False
    }
]

MENU_BY_ID = {
    item["id"]: item
    for item in MENU
}

# ============================================================
# DRINK OPTIONS
# ============================================================

COFFEE_IDS = {
    "espresso",
    "americano",
    "cappuccino",
    "latte",
    "flatwhite",
    "americanocream",
    "tonic",
    "bumble",
    "dirtymatcha",
    "pumpkinlatte"
}

SYRUP_ALLOWED_IDS = COFFEE_IDS.copy()

TEMPERATURE_IDS = {
    "thai",
    "green",
    "americano",
    "latte",
    "matchalatte",
    "dirtymatcha",
    "pumpkinlatte"
}

SYRUPS = {
    "vanilla",
    "salted_caramel",
    "strawberry"
}

# ============================================================
# UTILITIES
# ============================================================

def now_local():
    return datetime.now(TIMEZONE)


def safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def json_response(data, status=200):
    return jsonify(data), status


def database():
    connection = sqlite3.connect(
        DATABASE_PATH,
        timeout=30
    )

    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 30000")

    return connection


def db_columns(connection, table):
    rows = connection.execute(
        f"PRAGMA table_info({table})"
    ).fetchall()

    return {row["name"] for row in rows}


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def init_db():
    connection = database()

    try:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER,
                customer_name TEXT,
                username TEXT,
                items TEXT,
                total INTEGER,
                pickup TEXT,
                status TEXT DEFAULT 'pending',
                created_at TEXT,
                payment_method TEXT DEFAULT 'pickup',
                payment_status TEXT DEFAULT 'unpaid',
                reward_used INTEGER DEFAULT 0,
                reward_item_index INTEGER DEFAULT -1,
                reward_processed INTEGER DEFAULT 0
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS loyalty_accounts (
                telegram_id INTEGER PRIMARY KEY,
                stamps INTEGER DEFAULT 0,
                rewards INTEGER DEFAULT 0,
                total_drinks INTEGER DEFAULT 0
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS analytics_visitors (
                telegram_id INTEGER PRIMARY KEY,
                first_seen TEXT,
                last_seen TEXT
            )
        """)

        connection.execute("""
            CREATE TABLE IF NOT EXISTS analytics_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER,
                event_type TEXT,
                created_at TEXT
            )
        """)

        columns = db_columns(connection, "orders")

        migrations = {
            "payment_method": "TEXT DEFAULT 'pickup'",
            "payment_status": "TEXT DEFAULT 'unpaid'",
            "reward_used": "INTEGER DEFAULT 0",
            "reward_item_index": "INTEGER DEFAULT -1",
            "reward_processed": "INTEGER DEFAULT 0"
        }

        for column, definition in migrations.items():
            if column not in columns:
                connection.execute(
                    f"ALTER TABLE orders "
                    f"ADD COLUMN {column} {definition}"
                )

        connection.commit()

    finally:
        connection.close()


# ============================================================
# TELEGRAM AUTHENTICATION
# ============================================================

def verify_telegram_init_data(init_data):
    if not BOT_TOKEN or not init_data:
        return None

    try:
        values = dict(
            parse_qsl(init_data, keep_blank_values=True)
        )

        received_hash = values.pop("hash", None)

        if not received_hash:
            return None

        auth_date = safe_int(values.get("auth_date"))
        current_timestamp = int(datetime.now().timestamp())

        if (
            auth_date <= 0
            or auth_date > current_timestamp + 60
            or current_timestamp - auth_date > 86400
        ):
            return None

        data_check_string = "\n".join(
            f"{key}={value}"
            for key, value in sorted(values.items())
        )

        secret_key = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode(),
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
            return None

        user = json.loads(values.get("user", "{}"))

        if not user.get("id"):
            return None

        return user

    except Exception:
        logger.exception("Telegram authentication failed")
        return None


def authenticated_user():
    data = request.get_json(silent=True) or {}

    return verify_telegram_init_data(
        data.get("initData", "")
    )


# ============================================================
# TELEGRAM API
# ============================================================

def telegram_api(method, payload):
    if not BOT_TOKEN:
        return None

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
            json=payload,
            timeout=15
        )

        response.raise_for_status()

        result = response.json()

        if not result.get("ok"):
            logger.warning(
                "Telegram API error: %s",
                result
            )

        return result

    except Exception:
        logger.exception("Telegram API request failed")
        return None


def send_message(chat_id, text, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "text": text
    }

    if reply_markup:
        payload["reply_markup"] = reply_markup

    return telegram_api("sendMessage", payload)


def answer_callback(callback_id, text=""):
    telegram_api(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_id,
            "text": text
        }
    )


# ============================================================
# CUSTOMER LOYALTY
# ============================================================

def ensure_loyalty(connection, telegram_id):
    connection.execute("""
        INSERT OR IGNORE INTO loyalty_accounts
        (telegram_id, stamps, rewards, total_drinks)
        VALUES (?, 0, 0, 0)
    """, (telegram_id,))


def get_rewards(telegram_id):
    connection = database()

    try:
        ensure_loyalty(connection, telegram_id)
        connection.commit()

        row = connection.execute("""
            SELECT *
            FROM loyalty_accounts
            WHERE telegram_id = ?
        """, (telegram_id,)).fetchone()

        return {
            "stamps": row["stamps"],
            "rewards": row["rewards"],
            "total_drinks": row["total_drinks"],
            "needed": LOYALTY_REQUIRED
        }

    finally:
        connection.close()


def consume_reward(connection, telegram_id):
    ensure_loyalty(connection, telegram_id)

    cursor = connection.execute("""
        UPDATE loyalty_accounts
        SET rewards = rewards - 1
        WHERE telegram_id = ?
          AND rewards > 0
    """, (telegram_id,))

    return cursor.rowcount == 1


def restore_reward(connection, telegram_id):
    ensure_loyalty(connection, telegram_id)

    connection.execute("""
        UPDATE loyalty_accounts
        SET rewards = rewards + 1
        WHERE telegram_id = ?
    """, (telegram_id,))


def add_loyalty_drinks(connection, telegram_id, quantity):
    ensure_loyalty(connection, telegram_id)

    row = connection.execute("""
        SELECT stamps
        FROM loyalty_accounts
        WHERE telegram_id = ?
    """, (telegram_id,)).fetchone()

    total_stamps = row["stamps"] + quantity
    new_rewards = total_stamps // LOYALTY_REQUIRED
    remaining_stamps = total_stamps % LOYALTY_REQUIRED

    connection.execute("""
        UPDATE loyalty_accounts
        SET
            stamps = ?,
            rewards = rewards + ?,
            total_drinks = total_drinks + ?
        WHERE telegram_id = ?
    """, (
        remaining_stamps,
        new_rewards,
        quantity,
        telegram_id
    ))


# ============================================================
# PRODUCT CALCULATIONS
# ============================================================

def calculate_item(raw, reward_free=False):
    if not isinstance(raw, dict):
        raise ValueError("Invalid product")

    item_id = str(raw.get("id", "")).strip()
    legacy_temperature = None

    if item_id == "icedamericano":
        item_id = "americano"
        legacy_temperature = "iced"

    elif item_id == "icedlatte":
        item_id = "latte"
        legacy_temperature = "iced"

    product = MENU_BY_ID.get(item_id)

    if not product:
        raise ValueError("Product unavailable")

    quantity = safe_int(raw.get("qty", 1))

    if quantity < 1 or quantity > MAX_ITEM_QUANTITY:
        raise ValueError("Invalid quantity")

    temperature = (
        legacy_temperature
        or raw.get("temperature")
    )

    if item_id in TEMPERATURE_IDS:
        if temperature not in ("hot", "iced"):
            temperature = "hot"
    else:
        temperature = None

    boba = (
        bool(raw.get("boba"))
        and bool(product.get("boba"))
    )

    extra_shot = (
        bool(raw.get("extra_shot"))
        and item_id in COFFEE_IDS
    )

    syrup = raw.get("syrup")

    if (
        item_id not in SYRUP_ALLOWED_IDS
        or syrup not in SYRUPS
    ):
        syrup = None

    iced_surcharge = 0

    if temperature == "iced":
        iced_surcharge = safe_int(
            product.get("iced_surcharge", 0)
        )

    base_price = product["price"]

    options_price = (
        (BOBA_PRICE if boba else 0)
        + (EXTRA_SHOT_PRICE if extra_shot else 0)
        + (SYRUP_PRICE if syrup else 0)
        + iced_surcharge
    )

    unit_price = base_price + options_price

    # Free reward covers the base drink.
    # Any paid extras remain chargeable.
    discount = base_price if reward_free else 0

    total = unit_price * quantity - discount

    return {
        "id": item_id,
        "name": product["name"],
        "category": product["category"],
        "qty": quantity,
        "temperature": temperature,
        "boba": boba,
        "extra_shot": extra_shot,
        "syrup": syrup,
        "unit_price": unit_price,
        "price": unit_price,
        "discount": discount,
        "total": total,
        "reward_free": reward_free
    }


def calculate_order_items(raw_items, reward_item_index=-1):
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("Cart is empty")

    if len(raw_items) > MAX_ORDER_QUANTITY:
        raise ValueError("Too many items")

    if reward_item_index >= len(raw_items):
        raise ValueError("Invalid reward selection")

    items = []
    total = 0
    total_quantity = 0

    for index, raw in enumerate(raw_items):
        reward_free = index == reward_item_index

        item = calculate_item(raw, reward_free)

        items.append(item)
        total += item["total"]
        total_quantity += item["qty"]

    if total_quantity > MAX_ORDER_QUANTITY:
        raise ValueError("Maximum 30 drinks per order")

    return items, total


# ============================================================
# PICKUP VALIDATION — 08:00 TO 17:00
# ============================================================

def validate_pickup(pickup):
    if not pickup:
        raise ValueError("Select pickup time")

    try:
        pickup_time = datetime.fromisoformat(
            str(pickup).replace("Z", "+00:00")
        )
    except ValueError:
        raise ValueError("Invalid pickup time")

    if pickup_time.tzinfo is None:
        pickup_time = pickup_time.replace(tzinfo=TIMEZONE)

    pickup_time = pickup_time.astimezone(TIMEZONE)

    current_time = now_local()

    earliest = current_time + timedelta(
        minutes=MIN_PREP_MINUTES
    )

    if pickup_time < earliest:
        raise ValueError("Pickup time is too soon")

    if pickup_time.date() != current_time.date():
        raise ValueError("Pickup must be today")

    opening = pickup_time.replace(
        hour=OPEN_HOUR,
        minute=0,
        second=0,
        microsecond=0
    )

    closing = pickup_time.replace(
        hour=CLOSE_HOUR,
        minute=0,
        second=0,
        microsecond=0
    )

    if not (opening <= pickup_time <= closing):
        raise ValueError(
            "Pickup is available from 08:00 to 17:00"
        )

    return pickup_time.isoformat()


# ============================================================
# ORDER FORMATTING
# ============================================================

def order_items_text(items):
    lines = []

    for item in items:
        lines.append(
            f"{item['qty']} × {item['name']}"
        )

        if item.get("temperature"):
            lines.append(
                f"  {item['temperature'].upper()}"
            )

        if item.get("boba"):
            lines.append("  + BOBA")

        if item.get("extra_shot"):
            lines.append("  + EXTRA SHOT")

        if item.get("syrup"):
            lines.append(
                f"  + {item['syrup']}"
            )

        if item.get("reward_free"):
            lines.append("  FREE DRINK REWARD")

        lines.append(
            f"  {item['total']} RSD"
        )

    return "\n".join(lines)


def barista_text(order):
    items = json.loads(order["items"])

    return (
        "☕ NEW TAKE AWAY ORDER\n\n"
        f"Order #{order['id']}\n"
        f"Customer: {order['customer_name']}\n"
        f"Pickup: {order['pickup']}\n\n"
        f"{order_items_text(items)}\n\n"
        f"TOTAL: {order['total']} RSD\n"
        f"Payment: {order['payment_method']}\n"
        f"Status: {order['status']}"
    )


def order_keyboard(order_id):
    return {
        "inline_keyboard": [
            [
                {
                    "text": "✅ ACCEPT",
                    "callback_data": f"accept:{order_id}"
                },
                {
                    "text": "❌ CANCEL",
                    "callback_data": f"cancel:{order_id}"
                }
            ],
            [
                {
                    "text": "☕ READY",
                    "callback_data": f"ready:{order_id}"
                }
            ],
            [
                {
                    "text": "💰 PAID / PICKED UP",
                    "callback_data": f"complete:{order_id}"
                }
            ]
        ]
    }


# ============================================================
# ORDER CREATION
# ============================================================

def create_order(data, user):
    telegram_id = int(user["id"])

    customer_name = user.get("first_name") or "Customer"
    username = user.get("username") or ""

    raw_items = data.get("items", [])
    pickup = validate_pickup(data.get("pickup"))

    payment_method = data.get(
        "payment_method",
        "pickup"
    )

    if payment_method != "pickup":
        raise ValueError(
            "Only payment at pickup is available"
        )

    reward_index = safe_int(
        data.get("reward_item_index", -1),
        -1
    )

    if reward_index < -1:
        raise ValueError("Invalid reward selection")

    items, total = calculate_order_items(
        raw_items,
        reward_index
    )

    reward_used = reward_index >= 0

    connection = database()

    try:
        connection.execute("BEGIN IMMEDIATE")

        if reward_used:
            if not consume_reward(
                connection,
                telegram_id
            ):
                raise ValueError(
                    "No free drink rewards available"
                )

        cursor = connection.execute("""
            INSERT INTO orders (
                telegram_id,
                customer_name,
                username,
                items,
                total,
                pickup,
                status,
                created_at,
                payment_method,
                payment_status,
                reward_used,
                reward_item_index,
                reward_processed
            )
            VALUES (
                ?, ?, ?, ?, ?, ?,
                'pending', ?,
                'pickup', 'unpaid',
                ?, ?, 0
            )
        """, (
            telegram_id,
            customer_name,
            username,
            json.dumps(items, ensure_ascii=False),
            total,
            pickup,
            now_local().isoformat(),
            int(reward_used),
            reward_index
        ))

        order_id = cursor.lastrowid

        connection.commit()

        order = connection.execute("""
            SELECT *
            FROM orders
            WHERE id = ?
        """, (order_id,)).fetchone()

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()

    if ORDER_CHAT_ID:
        send_message(
            ORDER_CHAT_ID,
            barista_text(order),
            order_keyboard(order_id)
        )

    send_message(
        telegram_id,
        (
            "☕ TAKE AWAY\n\n"
            f"Order #{order_id} received!\n"
            f"Total: {total} RSD\n\n"
            "We'll notify you when your drinks are ready."
        )
    )

    return {
        "ok": True,
        "order_id": order_id,
        "total": total,
        "status": "pending"
    }


# ============================================================
# ORDER STATUS MANAGEMENT
# ============================================================

def update_order_status(order_id, new_status):
    connection = database()

    try:
        connection.execute("BEGIN IMMEDIATE")

        order = connection.execute("""
            SELECT *
            FROM orders
            WHERE id = ?
        """, (order_id,)).fetchone()

        if not order:
            connection.rollback()
            return None

        old_status = order["status"]

        if old_status in ("cancelled", "completed"):
            connection.rollback()
            return None

        allowed = {
            "pending": {"accepted", "cancelled"},
            "accepted": {
                "ready",
                "cancelled",
                "completed"
            },
            "ready": {"completed", "cancelled"}
        }

        if new_status not in allowed.get(
            old_status,
            set()
        ):
            connection.rollback()
            return None

        connection.execute("""
            UPDATE orders
            SET status = ?
            WHERE id = ?
        """, (
            new_status,
            order_id
        ))

        if new_status == "cancelled":
            if order["reward_used"]:
                restore_reward(
                    connection,
                    order["telegram_id"]
                )

        if new_status == "completed":
            if not order["reward_processed"]:
                items = json.loads(order["items"])

                paid_drinks = sum(
                    item["qty"]
                    for item in items
                )

                if order["reward_used"]:
                    paid_drinks -= 1

                paid_drinks = max(0, paid_drinks)

                add_loyalty_drinks(
                    connection,
                    order["telegram_id"],
                    paid_drinks
                )

                connection.execute("""
                    UPDATE orders
                    SET
                        reward_processed = 1,
                        payment_status = 'paid'
                    WHERE id = ?
                """, (order_id,))

        connection.commit()

        return dict(order)

    except Exception:
        connection.rollback()
        raise

    finally:
        connection.close()


# ============================================================
# TELEGRAM CALLBACKS
# ============================================================

def handle_callback(callback):
    callback_id = callback.get("id")

    message = callback.get("message", {})

    chat_id = str(
        message.get("chat", {}).get("id", "")
    )

    if (
        not ORDER_CHAT_ID
        or chat_id != str(ORDER_CHAT_ID)
    ):
        answer_callback(callback_id, "Not authorized")
        return

    callback_data = callback.get("data", "")

    try:
        action, order_id_text = callback_data.split(
            ":",
            1
        )
        order_id = int(order_id_text)

    except (ValueError, TypeError):
        answer_callback(callback_id, "Invalid action")
        return

    actions = {
        "accept": "accepted",
        "cancel": "cancelled",
        "ready": "ready",
        "complete": "completed"
    }

    new_status = actions.get(action)

    if not new_status:
        answer_callback(callback_id, "Unknown action")
        return

    order = update_order_status(
        order_id,
        new_status
    )

    if not order:
        answer_callback(
            callback_id,
            "Order already processed or invalid status"
        )
        return

    answer_callback(callback_id, "Updated")

    customer_messages = {
        "accepted": (
            f"✅ Order #{order_id} accepted!\n"
            "We're preparing your drinks."
        ),
        "ready": (
            f"☕ Order #{order_id} is READY!\n"
            "Please collect your drinks."
        ),
        "completed": (
            f"💚 Thank you!\n"
            f"Order #{order_id} completed.\n"
            "Your loyalty progress has been updated."
        ),
        "cancelled": (
            f"❌ Order #{order_id} cancelled."
        )
    }

    send_message(
        order["telegram_id"],
        customer_messages[new_status]
    )

    send_message(
        ORDER_CHAT_ID,
        (
            f"Order #{order_id}\n"
            f"Status: {new_status.upper()}"
        )
    )


# ============================================================
# BOT START
# ============================================================

def bot_start_text():
    return (
        "☕ TAKE AWAY\n"
        "Thai Tea & Coffee\n\n"
        "Welcome!\n\n"
        "Order your favorite drinks "
        "directly through our Mini App.\n\n"
        "Every 10th drink is FREE! 💚\n\n"
        "Opening hours: 08:00–17:00"
    )


def handle_telegram_message(message):
    text = message.get("text", "")

    chat_id = message.get(
        "chat",
        {}
    ).get("id")

    if not chat_id:
        return

    if text.startswith("/start"):
        keyboard = None

        if APP_URL:
            keyboard = {
                "inline_keyboard": [
                    [
                        {
                            "text": "☕ OPEN MENU",
                            "web_app": {
                                "url": APP_URL
                            }
                        }
                    ]
                ]
            }

        send_message(
            chat_id,
            bot_start_text(),
            keyboard
        )


# ============================================================
# FRONTEND
# ============================================================

@app.route("/")
def index():
    return render_template("index.html")


# ============================================================
# API — MENU
# ============================================================

@app.route("/api/menu", methods=["GET"])
def api_menu():
    return jsonify({
        "ok": True,
        "menu": MENU
    })


# ============================================================
# API — CREATE ORDER
# ============================================================

@app.route("/api/order", methods=["POST"])
def api_order():
    data = request.get_json(silent=True) or {}
    user = authenticated_user()

    if not user:
        return json_response(
            {
                "ok": False,
                "error": "Telegram authentication required"
            },
            401
        )

    try:
        return jsonify(create_order(data, user))

    except ValueError as error:
        return json_response(
            {
                "ok": False,
                "error": str(error)
            },
            400
        )

    except Exception:
        logger.exception("Order creation failed")

        return json_response(
            {
                "ok": False,
                "error": "Unable to create order"
            },
            500
        )


# ============================================================
# API — CUSTOMER ORDERS
# ============================================================

@app.route("/api/orders", methods=["POST"])
def api_orders():
    user = authenticated_user()

    if not user:
        return json_response(
            {
                "ok": False,
                "error": "Unauthorized"
            },
            401
        )

    connection = database()

    try:
        rows = connection.execute("""
            SELECT *
            FROM orders
            WHERE telegram_id = ?
            ORDER BY id DESC
            LIMIT 100
        """, (user["id"],)).fetchall()

        orders = []

        for row in rows:
            order = dict(row)

            try:
                order["items"] = json.loads(
                    order["items"]
                )
            except Exception:
                order["items"] = []

            orders.append(order)

        return jsonify({
            "ok": True,
            "orders": orders
        })

    finally:
        connection.close()


# ============================================================
# API — REWARDS
# ============================================================

@app.route("/api/rewards", methods=["POST"])
def api_rewards():
    user = authenticated_user()

    if not user:
        return json_response(
            {
                "ok": False,
                "error": "Unauthorized"
            },
            401
        )

    return jsonify({
        "ok": True,
        **get_rewards(user["id"])
    })


# ============================================================
# API — PROFILE
# ============================================================

@app.route("/api/profile", methods=["POST"])
def api_profile():
    user = authenticated_user()

    if not user:
        return json_response(
            {
                "ok": False,
                "error": "Unauthorized"
            },
            401
        )

    telegram_id = user["id"]
    rewards = get_rewards(telegram_id)

    connection = database()

    try:
        rows = connection.execute("""
            SELECT *
            FROM orders
            WHERE telegram_id = ?
            ORDER BY id DESC
        """, (telegram_id,)).fetchall()

        drink_counts = {}
        total_spent = 0

        for row in rows:
            if row["status"] == "cancelled":
                continue

            total_spent += row["total"] or 0

            try:
                items = json.loads(row["items"])
            except Exception:
                continue

            for item in items:
                drink_id = item.get("id")

                if not drink_id:
                    continue

                drink_counts[drink_id] = (
                    drink_counts.get(drink_id, 0)
                    + safe_int(item.get("qty", 1))
                )

        favorites = sorted(
            drink_counts.items(),
            key=lambda x: x[1],
            reverse=True
        )

        return jsonify({
            "ok": True,
            "user": {
                "id": telegram_id,
                "first_name": user.get("first_name", ""),
                "username": user.get("username", "")
            },
            "rewards": rewards,
            "total_orders": len(rows),
            "total_spent": total_spent,
            "favorites": [
                {
                    "id": item_id,
                    "name": MENU_BY_ID.get(
                        item_id,
                        {}
                    ).get("name", item_id),
                    "count": count
                }
                for item_id, count in favorites[:5]
            ]
        })

    finally:
        connection.close()


# ============================================================
# ANALYTICS AUTHENTICATION
# ============================================================

def require_admin(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        auth = request.authorization

        if (
            not ADMIN_PASSWORD
            or not auth
            or not hmac.compare_digest(
                auth.password or "",
                ADMIN_PASSWORD
            )
        ):
            return Response(
                "Authentication required",
                401,
                {
                    "WWW-Authenticate":
                    'Basic realm="TAKE AWAY Admin"'
                }
            )

        return function(*args, **kwargs)

    return wrapper


# ============================================================
# ANALYTICS
# ============================================================

@app.route("/api/analytics/open", methods=["POST"])
def analytics_open():
    user = authenticated_user()

    if not user:
        return json_response(
            {
                "ok": False,
                "error": "Unauthorized"
            },
            401
        )

    connection = database()

    try:
        timestamp = now_local().isoformat()

        connection.execute("""
            INSERT OR IGNORE
            INTO analytics_visitors (
                telegram_id,
                first_seen,
                last_seen
            )
            VALUES (?, ?, ?)
        """, (
            user["id"],
            timestamp,
            timestamp
        ))

        connection.execute("""
            UPDATE analytics_visitors
            SET last_seen = ?
            WHERE telegram_id = ?
        """, (
            timestamp,
            user["id"]
        ))

        connection.execute("""
            INSERT INTO analytics_events (
                telegram_id,
                event_type,
                created_at
            )
            VALUES (?, 'open', ?)
        """, (
            user["id"],
            timestamp
        ))

        connection.commit()

        return jsonify({"ok": True})

    finally:
        connection.close()


@app.route("/api/analytics/directions", methods=["POST"])
def analytics_directions():
    user = authenticated_user()

    if not user:
        return json_response(
            {
                "ok": False,
                "error": "Unauthorized"
            },
            401
        )

    connection = database()

    try:
        connection.execute("""
            INSERT INTO analytics_events (
                telegram_id,
                event_type,
                created_at
            )
            VALUES (?, 'directions', ?)
        """, (
            user["id"],
            now_local().isoformat()
        ))

        connection.commit()

        return jsonify({"ok": True})

    finally:
        connection.close()


@app.route("/admin/analytics", methods=["GET"])
@require_admin
def admin_analytics():
    connection = database()

    try:
        total_orders = connection.execute("""
            SELECT COUNT(*) AS count
            FROM orders
        """).fetchone()["count"]

        completed_orders = connection.execute("""
            SELECT COUNT(*) AS count
            FROM orders
            WHERE status = 'completed'
        """).fetchone()["count"]

        revenue = connection.execute("""
            SELECT COALESCE(
                SUM(total),
                0
            ) AS revenue
            FROM orders
            WHERE status = 'completed'
        """).fetchone()["revenue"]

        visitors = connection.execute("""
            SELECT COUNT(*) AS count
            FROM analytics_visitors
        """).fetchone()["count"]

        return jsonify({
            "ok": True,
            "total_orders": total_orders,
            "completed_orders": completed_orders,
            "revenue": revenue,
            "unique_visitors": visitors
        })

    finally:
        connection.close()


# ============================================================
# TELEGRAM WEBHOOK
# ============================================================

@app.route("/webhook", methods=["POST"])
def telegram_webhook():
    if not WEBHOOK_SECRET:
        return json_response(
            {
                "ok": False,
                "error": "Webhook not configured"
            },
            503
        )

    received_secret = request.headers.get(
        "X-Telegram-Bot-Api-Secret-Token",
        ""
    )

    if not hmac.compare_digest(
        received_secret,
        WEBHOOK_SECRET
    ):
        return json_response(
            {
                "ok": False,
                "error": "Unauthorized"
            },
            403
        )

    update = request.get_json(silent=True) or {}

    try:
        if "callback_query" in update:
            handle_callback(update["callback_query"])

        elif "message" in update:
            handle_telegram_message(update["message"])

    except Exception:
        logger.exception("Telegram webhook error")

        return json_response(
            {"ok": False},
            500
        )

    return jsonify({"ok": True})


# ============================================================
# WEBHOOK SETUP
# ============================================================

@app.route("/setup-webhook", methods=["POST"])
@require_admin
def setup_webhook():
    if not BOT_TOKEN or not APP_URL or not WEBHOOK_SECRET:
        return json_response(
            {
                "ok": False,
                "error": "Missing Telegram configuration"
            },
            400
        )

    result = telegram_api(
        "setWebhook",
        {
            "url": APP_URL + "/webhook",
            "secret_token": WEBHOOK_SECRET,
            "drop_pending_updates": False
        }
    )

    return jsonify(result or {"ok": False})


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "ok": True,
        "service": "TAKE AWAY",
        "time": now_local().isoformat(),
        "opening_hours": "08:00–17:00"
    })


# ============================================================
# ERROR HANDLERS
# ============================================================

@app.errorhandler(404)
def not_found(error):
    return jsonify({
        "ok": False,
        "error": "Not found"
    }), 404


@app.errorhandler(500)
def internal_error(error):
    logger.exception("Internal server error")

    return jsonify({
        "ok": False,
        "error": "Internal server error"
    }), 500


# ============================================================
# START APPLICATION
# ============================================================

init_db()

if __name__ == "__main__":
    port = safe_int(
        os.getenv("PORT", "10000"),
        10000
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
