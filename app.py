import os
import json
import time
import hmac
import hashlib
import sqlite3
import logging
import secrets
import html

from datetime import datetime, timedelta
from urllib.parse import parse_qsl
from zoneinfo import ZoneInfo

import requests
from flask import Flask, request, jsonify, render_template, Response


# ============================================================
# TAKE AWAY — TELEGRAM MINI APP BACKEND
# ============================================================

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

APP_URL = os.environ.get(
    "APP_URL",
    "https://takeaway-telegram.onrender.com"
).rstrip("/")

ORDER_CHAT_ID = os.environ.get("ORDER_CHAT_ID", "").strip()

DATABASE_PATH = os.environ.get(
    "DATABASE_PATH",
    "orders.db"
)

WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "").strip()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "").strip()

TZ = ZoneInfo("Europe/Belgrade")

OPEN_HOUR = 8
CLOSE_HOUR = 17

MIN_PREP_MINUTES = 10
MAX_ITEM_QUANTITY = 10
MAX_ORDER_QUANTITY = 30

# Every 10th drink is free:
# 9 paid drinks earn 1 free drink.
STAMPS_PER_REWARD = 9


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
        "temperature": True,
    },
    {
        "id": "green",
        "name": "Thai Green Milk Tea",
        "price": 450,
        "category": "tea",
        "boba": True,
        "temperature": True,
    },
    {
        "id": "espresso",
        "name": "Espresso",
        "price": 200,
        "category": "coffee",
    },
    {
        "id": "americano",
        "name": "Americano",
        "price": 250,
        "category": "coffee",
        "temperature": True,
        "iced_surcharge": 20,
    },
    {
        "id": "cappuccino",
        "name": "Cappuccino",
        "price": 320,
        "category": "coffee",
    },
    {
        "id": "latte",
        "name": "Latte",
        "price": 350,
        "category": "coffee",
        "temperature": True,
        "iced_surcharge": 20,
    },
    {
        "id": "flatwhite",
        "name": "Flat White",
        "price": 450,
        "category": "coffee",
    },
    {
        "id": "americanocream",
        "name": "Iced Americano with Cream",
        "price": 450,
        "category": "coffee",
    },
    {
        "id": "tonic",
        "name": "Tonic Coffee",
        "price": 420,
        "category": "special",
    },
    {
        "id": "bumble",
        "name": "Bumble Coffee",
        "price": 450,
        "category": "special",
    },
    {
        "id": "matcha",
        "name": "Matcha Latte",
        "price": 480,
        "category": "special",
    },
    {
        "id": "dirtymatcha",
        "name": "Dirty Matcha Latte",
        "price": 550,
        "category": "special",
    },
    {
        "id": "strawberrymatcha",
        "name": "Strawberry Matcha Latte",
        "price": 550,
        "category": "special",
    },
    {
        "id": "pumpkinlatte",
        "name": "Pumpkin Latte",
        "price": 480,
        "category": "special",
        "seasonal": True,
    },
    {
        "id": "kuvanovino",
        "name": "Kuvano Vino",
        "price": 400,
        "category": "special",
        "seasonal": True,
    },
    {
        "id": "serbiancoffee",
        "name": "Traditional Serbian Coffee",
        "price": 220,
        "category": "coffee",
    },
]

MENU_BY_ID = {item["id"]: item for item in MENU}

SYRUPS = {
    "vanilla": "Vanilla",
    "salted_caramel": "Salted Caramel",
    "strawberry": "Strawberry",
}

SYRUP_ALLOWED_IDS = {
    "espresso",
    "americano",
    "cappuccino",
    "latte",
    "flatwhite",
    "americanocream",
    "tonic",
    "bumble",
    "pumpkinlatte",
}

EXTRA_SHOT_ALLOWED_IDS = {
    "espresso",
    "americano",
    "cappuccino",
    "latte",
    "flatwhite",
    "americanocream",
    "tonic",
    "bumble",
    "pumpkinlatte",
    "dirtymatcha",
}

LEGACY_ITEM_MAP = {
    "icedamericano": ("americano", "iced"),
    "icedlatte": ("latte", "iced"),
}

# Old products are intentionally excluded from the new menu:
# - Thai Iced Lime Tea
# - Peanut Latte


# ============================================================
# DATABASE
# ============================================================

def get_db():
    db = sqlite3.connect(
        DATABASE_PATH,
        timeout=30
    )

    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout = 30000")

    return db


def table_columns(db, table):
    return {
        row["name"]
        for row in db.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()
    }


def ensure_column(db, table, name, definition):
    if name not in table_columns(db, table):
        db.execute(
            f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
        )


def init_db():
    db = get_db()

    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER NOT NULL,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                items TEXT NOT NULL,
                total INTEGER NOT NULL DEFAULT 0,
                pickup TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'new',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)

        for name, definition in [
            ("payment_method", "TEXT DEFAULT 'pickup'"),
            ("payment_status", "TEXT DEFAULT 'pending'"),
            ("reward_used", "INTEGER DEFAULT 0"),
            ("reward_item_index", "INTEGER"),
            ("reward_processed", "INTEGER DEFAULT 0"),
        ]:
            ensure_column(db, "orders", name, definition)

        db.execute("""
            CREATE TABLE IF NOT EXISTS loyalty_accounts (
                telegram_id INTEGER PRIMARY KEY,
                stamps INTEGER NOT NULL DEFAULT 0,
                free_drinks INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS analytics_visitors (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                opens INTEGER NOT NULL DEFAULT 1,
                source TEXT
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS analytics_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER,
                event_type TEXT NOT NULL,
                source TEXT,
                created_at TEXT NOT NULL
            )
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_orders_telegram
            ON orders(telegram_id)
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_orders_status
            ON orders(status)
        """)

        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_orders_created
            ON orders(created_at)
        """)

        db.commit()

    finally:
        db.close()


init_db()


# ============================================================
# GENERAL HELPERS
# ============================================================

def now_local():
    return datetime.now(TZ)


def now_iso():
    return now_local().isoformat()


def safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_datetime(value):
    try:
        dt = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)

        return dt.astimezone(TZ)

    except (TypeError, ValueError):
        return None


def pretty_pickup(value):
    dt = parse_datetime(value)
    return dt.strftime("%H:%M") if dt else str(value or "")


def pretty_created(value):
    dt = parse_datetime(value)

    return (
        dt.strftime("%d.%m.%Y %H:%M")
        if dt
        else str(value or "")
    )


def get_order(order_id):
    db = get_db()

    try:
        return db.execute(
            "SELECT * FROM orders WHERE id = ?",
            (order_id,)
        ).fetchone()

    finally:
        db.close()


def order_items(row):
    try:
        return json.loads(row["items"] or "[]")
    except (TypeError, ValueError):
        return []


def order_quantity(row):
    return sum(
        safe_int(item.get("qty"))
        for item in order_items(row)
    )


# ============================================================
# TELEGRAM AUTHENTICATION
# ============================================================

def verify_init_data(init_data):
    if not BOT_TOKEN or not init_data:
        return None

    try:
        parsed = dict(
            parse_qsl(
                init_data,
                keep_blank_values=True
            )
        )

        received_hash = parsed.pop("hash", None)

        if not received_hash:
            return None

        auth_date = safe_int(parsed.get("auth_date"))

        if not auth_date:
            return None

        if abs(int(time.time()) - auth_date) > 86400:
            return None

        data_check_string = "\n".join(
            f"{key}={parsed[key]}"
            for key in sorted(parsed)
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

        user = json.loads(parsed.get("user", "{}"))

        return user if user.get("id") else None

    except Exception:
        logging.exception("Telegram authentication failed")
        return None


def request_user():
    data = request.get_json(silent=True) or {}

    user = verify_init_data(
        data.get("initData", "")
    )

    return data, user


def unauthorized():
    return jsonify({
        "success": False,
        "error": "Please open TAKE AWAY inside Telegram."
    }), 401


# ============================================================
# TELEGRAM API
# ============================================================

def telegram_api(method, payload=None):
    if not BOT_TOKEN:
        logging.error("BOT_TOKEN missing")
        return {"ok": False}

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
            json=payload or {},
            timeout=20
        )

        result = response.json()

        if not result.get("ok"):
            logging.error(
                "Telegram %s failed: %s",
                method,
                result
            )

        return result

    except Exception:
        logging.exception("Telegram request failed")
        return {"ok": False}


def send_message(chat_id, text, reply_markup=None):
    if not chat_id:
        return {"ok": False}

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    return telegram_api("sendMessage", payload)


def answer_callback(callback_id, text="", alert=False):
    return telegram_api(
        "answerCallbackQuery",
        {
            "callback_query_id": callback_id,
            "text": text,
            "show_alert": alert,
        }
    )


def edit_message_markup(chat_id, message_id, keyboard):
    return telegram_api(
        "editMessageReplyMarkup",
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": keyboard,
        }
    )


# ============================================================
# LOYALTY ACCOUNTS
# ============================================================

def ensure_loyalty_account(db, telegram_id):
    db.execute("""
        INSERT OR IGNORE INTO loyalty_accounts (
            telegram_id,
            stamps,
            free_drinks,
            updated_at
        )
        VALUES (?, 0, 0, ?)
    """, (telegram_id, now_iso()))

    return db.execute("""
        SELECT *
        FROM loyalty_accounts
        WHERE telegram_id = ?
    """, (telegram_id,)).fetchone()


def get_rewards(telegram_id):
    db = get_db()

    try:
        row = ensure_loyalty_account(db, telegram_id)
        db.commit()

        return {
            "stamps": safe_int(row["stamps"]),
            "free_drinks": safe_int(row["free_drinks"]),
            "required_stamps": STAMPS_PER_REWARD,
        }

    finally:
        db.close()


def consume_reward(db, telegram_id):
    ensure_loyalty_account(db, telegram_id)

    cursor = db.execute("""
        UPDATE loyalty_accounts
        SET free_drinks = free_drinks - 1,
            updated_at = ?
        WHERE telegram_id = ?
          AND free_drinks > 0
    """, (now_iso(), telegram_id))

    return cursor.rowcount == 1


def restore_reward(db, telegram_id):
    ensure_loyalty_account(db, telegram_id)

    db.execute("""
        UPDATE loyalty_accounts
        SET free_drinks = free_drinks + 1,
            updated_at = ?
        WHERE telegram_id = ?
    """, (now_iso(), telegram_id))


def add_loyalty_drinks(db, telegram_id, quantity):
    if quantity <= 0:
        return

    row = ensure_loyalty_account(db, telegram_id)

    total_stamps = safe_int(row["stamps"]) + quantity

    new_rewards = total_stamps // STAMPS_PER_REWARD
    remaining_stamps = total_stamps % STAMPS_PER_REWARD

    db.execute("""
        UPDATE loyalty_accounts
        SET stamps = ?,
            free_drinks = free_drinks + ?,
            updated_at = ?
        WHERE telegram_id = ?
    """, (
        remaining_stamps,
        new_rewards,
        now_iso(),
        telegram_id
    ))


# ============================================================
# MENU PRICING
# ============================================================

def calculate_item(raw, reward_free=False):
    raw_id = str(raw.get("id", "")).strip()
    temperature = raw.get("temperature")

    if raw_id in LEGACY_ITEM_MAP:
        item_id, temperature = LEGACY_ITEM_MAP[raw_id]
    else:
        item_id = raw_id

    product = MENU_BY_ID.get(item_id)

    if not product:
        raise ValueError(f"Unknown drink: {raw_id}")

    qty = safe_int(raw.get("qty"), 1)

    if qty < 1 or qty > MAX_ITEM_QUANTITY:
        raise ValueError("Invalid drink quantity.")

    if reward_free and qty != 1:
        raise ValueError(
            "A free reward must be a separate cart item with quantity 1."
        )

    if product.get("temperature"):
        temperature = str(
            temperature or "hot"
        ).lower()

        if temperature not in {"hot", "iced"}:
            temperature = "hot"
    else:
        temperature = None

    boba = bool(raw.get("boba")) and bool(
        product.get("boba")
    )

    extra_shot = (
        bool(raw.get("extra_shot"))
        and item_id in EXTRA_SHOT_ALLOWED_IDS
    )

    syrup = str(raw.get("syrup") or "").lower()

    if (
        item_id not in SYRUP_ALLOWED_IDS
        or syrup not in SYRUPS
    ):
        syrup = None

    iced_surcharge = (
        safe_int(product.get("iced_surcharge"))
        if temperature == "iced"
        else 0
    )

    extra_price = 150 if extra_shot else 0
    boba_price = 100 if boba else 0

    base_price = safe_int(product["price"])

    unit_price = (
        base_price
        + iced_surcharge
        + extra_price
        + boba_price
    )

    # Reward covers the base drink.
    # Optional extras remain payable.
    if reward_free:
        charged_unit = (
            iced_surcharge
            + extra_price
            + boba_price
        )
    else:
        charged_unit = unit_price

    return {
        "id": item_id,
        "name": product["name"],
        "qty": qty,
        "base_price": base_price,
        "unit_price": unit_price,
        "line_total": charged_unit * qty,
        "temperature": temperature,
        "boba": boba,
        "extra_shot": extra_shot,
        "syrup": syrup,
        "reward_free": bool(reward_free),
    }


def calculate_order_items(raw_items, reward_item_index=None):
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("Your cart is empty.")

    reward_index = None

    if reward_item_index is not None:
        try:
            reward_index = int(reward_item_index)
        except (TypeError, ValueError):
            raise ValueError("Invalid reward selection.")

        if not 0 <= reward_index < len(raw_items):
            raise ValueError("Invalid reward selection.")

    normalized = []
    total_quantity = 0

    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise ValueError("Invalid cart item.")

        item = calculate_item(
            raw,
            reward_free=(index == reward_index)
        )

        total_quantity += item["qty"]

        if total_quantity > MAX_ORDER_QUANTITY:
            raise ValueError("Maximum 30 drinks per order.")

        normalized.append(item)

    total = sum(
        item["line_total"]
        for item in normalized
    )

    return normalized, total, reward_index


# ============================================================
# PICKUP TIMES
# ============================================================

def validate_pickup(value):
    pickup = parse_datetime(value)

    if not pickup:
        return None, "Invalid pickup time."

    current = now_local()

    if pickup.date() != current.date():
        return None, "Pickup is available today only."

    earliest = current + timedelta(
        minutes=MIN_PREP_MINUTES
    )

    if pickup < earliest - timedelta(minutes=2):
        return None, "Please allow 10 minutes for preparation."

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

    if not opening <= pickup < closing:
        return None, "Pickup is available from 08:00 to 17:00."

    return pickup, None


# ============================================================
# ORDER SERIALIZATION
# ============================================================

def serialize_order(row):
    return {
        "id": row["id"],
        "items": order_items(row),
        "total": safe_int(row["total"]),
        "pickup": pretty_pickup(row["pickup"]),
        "pickup_iso": row["pickup"],
        "status": row["status"],
        "payment_method": row["payment_method"],
        "payment_status": row["payment_status"],
        "reward_used": bool(row["reward_used"]),
        "created": pretty_created(row["created_at"]),
        "created_at": row["created_at"],
    }


# ============================================================
# BARISTA NOTIFICATIONS
# ============================================================

def admin_keyboard(order_id, status="new"):
    if status == "new":
        buttons = [
            [{
                "text": "✅ ACCEPT",
                "callback_data": f"accept:{order_id}"
            }],
            [{
                "text": "❌ CANCEL",
                "callback_data": f"cancel:{order_id}"
            }],
        ]

    elif status == "preparing":
        buttons = [
            [{
                "text": "☕ READY",
                "callback_data": f"ready:{order_id}"
            }],
            [{
                "text": "❌ CANCEL",
                "callback_data": f"cancel:{order_id}"
            }],
        ]

    elif status == "ready":
        buttons = [
            [{
                "text": "💳 PAID / PICKED UP",
                "callback_data": f"complete:{order_id}"
            }],
            [{
                "text": "❌ CANCEL",
                "callback_data": f"cancel:{order_id}"
            }],
        ]

    else:
        buttons = []

    return {"inline_keyboard": buttons}


def barista_text(order_id, user, items, total, pickup):
    name = " ".join(
        filter(None, [
            user.get("first_name"),
            user.get("last_name")
        ])
    ) or "Customer"

    lines = [
        "☕ <b>NEW TAKE AWAY ORDER</b>",
        "",
        f"🧾 <b>Order #{order_id}</b>",
        f"👤 {html.escape(name)}",
        f"🕒 Pickup: <b>{pretty_pickup(pickup)}</b>",
        "💳 Payment: <b>PAY AT PICKUP</b>",
        "",
        "<b>DRINKS</b>",
    ]

    username = user.get("username")

    if username:
        lines.insert(
            4,
            f"📱 @{html.escape(str(username))}"
        )

    for item in items:
        name = html.escape(item["name"])

        prefix = (
            "🎁"
            if item["reward_free"]
            else "•"
        )

        lines.append(
            f"{prefix} <b>{item['qty']} × {name}</b>"
            f" — {item['line_total']} RSD"
        )

        if item["temperature"] == "hot":
            lines.append("☕ HOT")

        elif item["temperature"] == "iced":
            lines.append("🧊 ICED")

        if item["extra_shot"]:
            lines.append("➕ Extra shot +150 RSD")

        if item["boba"]:
            lines.append("🧋 Boba +100 RSD")

        if item["syrup"]:
            syrup_name = SYRUPS[item["syrup"]]
            lines.append(
                f"🍯 {html.escape(syrup_name)} syrup · FREE"
            )

        if item["reward_free"]:
            lines.append("🎁 FREE DRINK REWARD")

    lines.extend([
        "",
        f"💰 <b>TOTAL: {total} RSD</b>",
        "",
        "Payment at café by card or cash.",
    ])

    return "\n".join(lines)


def customer_status_message(order_id, status):
    messages = {
        "preparing": (
            f"☕ <b>Order #{order_id}</b>\n\n"
            "Your order has been accepted and is being prepared."
        ),
        "ready": (
            f"✅ <b>Order #{order_id} is ready!</b>\n\n"
            "You can collect your drinks.\n"
            "Pay at the café by card or cash."
        ),
        "completed": (
            f"💚 <b>Order #{order_id} completed.</b>\n\n"
            "Thank you for choosing TAKE AWAY!"
        ),
        "cancelled": (
            f"❌ <b>Order #{order_id} was cancelled.</b>\n\n"
            "Please contact TAKE AWAY if you need help."
        ),
    }

    return messages.get(
        status,
        f"Order #{order_id}: {status}"
    )


# ============================================================
# ORDER STATUS AND REWARDS
# ============================================================

def set_order_status(order_id, new_status, allowed_from):
    db = get_db()

    try:
        db.execute("BEGIN IMMEDIATE")

        row = db.execute(
            "SELECT * FROM orders WHERE id = ?",
            (order_id,)
        ).fetchone()

        if not row or row["status"] not in allowed_from:
            db.rollback()
            return None

        db.execute("""
            UPDATE orders
            SET status = ?, updated_at = ?
            WHERE id = ?
        """, (
            new_status,
            now_iso(),
            order_id
        ))

        db.commit()

        return get_order(order_id)

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


def mark_paid_and_completed(order_id):
    db = get_db()

    try:
        db.execute("BEGIN IMMEDIATE")

        row = db.execute(
            "SELECT * FROM orders WHERE id = ?",
            (order_id,)
        ).fetchone()

        if not row or row["status"] != "ready":
            db.rollback()
            return False

        if not safe_int(row["reward_processed"]):
            paid_quantity = sum(
                safe_int(item.get("qty"))
                for item in order_items(row)
                if not item.get("reward_free")
            )

            add_loyalty_drinks(
                db,
                safe_int(row["telegram_id"]),
                paid_quantity
            )

        db.execute("""
            UPDATE orders
            SET status = 'completed',
                payment_status = 'paid',
                reward_processed = 1,
                updated_at = ?
            WHERE id = ?
        """, (now_iso(), order_id))

        db.commit()
        return True

    except Exception:
        db.rollback()
        logging.exception("Could not complete order")
        return False

    finally:
        db.close()


def cancel_order(order_id, allowed_from=None):
    if allowed_from is None:
        allowed_from = {"new", "preparing", "ready"}

    db = get_db()

    try:
        db.execute("BEGIN IMMEDIATE")

        row = db.execute(
            "SELECT * FROM orders WHERE id = ?",
            (order_id,)
        ).fetchone()

        if not row or row["status"] not in allowed_from:
            db.rollback()
            return None

        if safe_int(row["reward_used"]):
            restore_reward(
                db,
                safe_int(row["telegram_id"])
            )

        db.execute("""
            UPDATE orders
            SET status = 'cancelled',
                reward_used = 0,
                updated_at = ?
            WHERE id = ?
        """, (now_iso(), order_id))

        db.commit()

        return get_order(order_id)

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


# ============================================================
# MAIN ROUTES
# ============================================================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/health")
def health():
    return jsonify({
        "ok": True,
        "service": "TAKE AWAY",
        "time": now_iso(),
        "bot_configured": bool(BOT_TOKEN),
        "admin_configured": bool(ORDER_CHAT_ID),
        "payment_mode": "pickup",
        "opening_hour": OPEN_HOUR,
        "closing_hour": CLOSE_HOUR,
    })


@app.route("/api/menu")
def api_menu():
    return jsonify(MENU)


@app.route("/api/config")
def api_config():
    return jsonify({
        "opening_hour": OPEN_HOUR,
        "closing_hour": CLOSE_HOUR,
        "min_prep_minutes": MIN_PREP_MINUTES,
        "timezone": "Europe/Belgrade",
        "payment_method": "pickup",
        "stamps_per_reward": STAMPS_PER_REWARD,
    })


# ============================================================
# REWARDS API
# ============================================================

@app.route("/api/rewards", methods=["POST"])
def api_rewards():
    _, user = request_user()

    if not user:
        return unauthorized()

    return jsonify(
        get_rewards(safe_int(user["id"]))
    )


# ============================================================
# ORDER HISTORY
# ============================================================

@app.route("/api/orders", methods=["POST"])
def api_orders():
    _, user = request_user()

    if not user:
        return unauthorized()

    db = get_db()

    try:
        rows = db.execute("""
            SELECT *
            FROM orders
            WHERE telegram_id = ?
            ORDER BY id DESC
            LIMIT 50
        """, (safe_int(user["id"]),)).fetchall()

        return jsonify({
            "orders": [
                serialize_order(row)
                for row in rows
            ]
        })

    finally:
        db.close()


# ============================================================
# CUSTOMER PROFILE
# ============================================================

@app.route("/api/profile", methods=["POST"])
def api_profile():
    _, user = request_user()

    if not user:
        return unauthorized()

    telegram_id = safe_int(user["id"])

    db = get_db()

    try:
        rows = db.execute("""
            SELECT *
            FROM orders
            WHERE telegram_id = ?
            ORDER BY id ASC
        """, (telegram_id,)).fetchall()

        completed = [
            row for row in rows
            if row["status"] == "completed"
        ]

        drinks = sum(
            order_quantity(row)
            for row in completed
        )

        favorites = {}

        for row in completed:
            for item in order_items(row):
                item_id = str(item.get("id") or "")

                if item_id in LEGACY_ITEM_MAP:
                    item_id = LEGACY_ITEM_MAP[item_id][0]

                if item_id not in MENU_BY_ID:
                    continue

                favorites[item_id] = (
                    favorites.get(item_id, 0)
                    + safe_int(item.get("qty"))
                )

        favorite = None

        if favorites:
            favorite_id = max(
                favorites,
                key=favorites.get
            )

            favorite = {
                "id": favorite_id,
                "quantity": favorites[favorite_id],
            }

        first_name = user.get("first_name") or ""
        last_name = user.get("last_name") or ""

        name = f"{first_name} {last_name}".strip()

        return jsonify({
            "telegram_id": telegram_id,
            "first_name": first_name,
            "last_name": last_name,
            "name": name or "TAKE AWAY Member",
            "username": user.get("username"),
            "orders": len(rows),
            "drinks": drinks,
            "favorite": favorite,
            "first_order": (
                pretty_created(rows[0]["created_at"]).split(" ")[0]
                if rows else None
            ),
        })

    finally:
        db.close()


# ============================================================
# CREATE ORDER
# ============================================================

@app.route("/api/order", methods=["POST"])
def create_order():
    data, user = request_user()

    if not user:
        return unauthorized()

    payment_method = str(
        data.get("payment_method", "pickup")
    ).lower()

    if payment_method != "pickup":
        return jsonify({
            "success": False,
            "error": "Online payment is not available yet."
        }), 400

    try:
        items, total, reward_index = calculate_order_items(
            data.get("items", []),
            data.get("reward_item_index")
        )

    except ValueError as exc:
        return jsonify({
            "success": False,
            "error": str(exc)
        }), 400

    pickup, error = validate_pickup(
        data.get("pickup")
    )

    if error:
        return jsonify({
            "success": False,
            "error": error
        }), 400

    telegram_id = safe_int(user["id"])
    reward_used = reward_index is not None

    db = get_db()

    try:
        db.execute("BEGIN IMMEDIATE")

        if reward_used:
            if not consume_reward(db, telegram_id):
                db.rollback()

                return jsonify({
                    "success": False,
                    "error": (
                        "Free drink unavailable. "
                        "Please refresh your rewards."
                    )
                }), 409

        current = now_iso()

        cursor = db.execute("""
            INSERT INTO orders (
                telegram_id,
                username,
                first_name,
                last_name,
                items,
                total,
                pickup,
                status,
                created_at,
                updated_at,
                payment_method,
                payment_status,
                reward_used,
                reward_item_index,
                reward_processed
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?,
                'new', ?, ?,
                'pickup', 'pending',
                ?, ?, 0
            )
        """, (
            telegram_id,
            user.get("username"),
            user.get("first_name"),
            user.get("last_name"),
            json.dumps(items, ensure_ascii=False),
            total,
            pickup.isoformat(),
            current,
            current,
            int(reward_used),
            reward_index,
        ))

        order_id = cursor.lastrowid

        db.commit()

    except Exception:
        db.rollback()
        logging.exception("Order creation failed")

        return jsonify({
            "success": False,
            "error": "Could not create order."
        }), 500

    finally:
        db.close()

    if ORDER_CHAT_ID:
        result = send_message(
            ORDER_CHAT_ID,
            barista_text(
                order_id,
                user,
                items,
                total,
                pickup.isoformat()
            ),
            admin_keyboard(order_id, "new")
        )

        if not result.get("ok"):
            logging.error(
                "Barista notification failed for order #%s",
                order_id
            )

    else:
        logging.error(
            "ORDER_CHAT_ID missing for order #%s",
            order_id
        )

    send_message(
        telegram_id,
        (
            f"☕ <b>TAKE AWAY order #{order_id}</b>\n\n"
            f"🕒 Pickup: <b>{pretty_pickup(pickup.isoformat())}</b>\n"
            f"💰 Total: <b>{total} RSD</b>\n\n"
            "Your order has been received.\n"
            "Pay at pickup by cash or card."
        )
    )

    return jsonify({
        "success": True,
        "order_id": order_id,
        "total": total,
        "pickup": pickup.isoformat(),
        "payment_method": "pickup",
        "payment_status": "pending",
        "reward_used": reward_used,
    })


# ============================================================
# CUSTOMER ORDER CANCELLATION
# ============================================================

@app.route("/api/order/cancel", methods=["POST"])
def customer_cancel_order():
    data, user = request_user()

    if not user:
        return unauthorized()

    order_id = safe_int(data.get("order_id"))

    order = get_order(order_id)

    if not order:
        return jsonify({
            "success": False,
            "error": "Order not found."
        }), 404

    if safe_int(order["telegram_id"]) != safe_int(user["id"]):
        return unauthorized()

    if order["status"] != "new":
        return jsonify({
            "success": False,
            "error": "Only new orders can be cancelled."
        }), 409

    updated = cancel_order(
        order_id,
        allowed_from={"new"}
    )

    if not updated:
        return jsonify({
            "success": False,
            "error": "Order can no longer be cancelled."
        }), 409

    if ORDER_CHAT_ID:
        send_message(
            ORDER_CHAT_ID,
            f"❌ <b>Order #{order_id} cancelled by customer.</b>"
        )

    return jsonify({
        "success": True,
        "order_id": order_id,
        "status": "cancelled",
    })


# ============================================================
# ANALYTICS
# ============================================================

def clean_source(value):
    return str(value or "direct").strip()[:100] or "direct"


@app.route("/api/analytics/open", methods=["POST"])
def analytics_open():
    data, user = request_user()

    if not user:
        return unauthorized()

    telegram_id = safe_int(user["id"])
    source = clean_source(data.get("source"))
    current = now_iso()

    db = get_db()

    try:
        db.execute("""
            INSERT INTO analytics_visitors (
                telegram_id,
                username,
                first_name,
                last_name,
                first_seen,
                last_seen,
                opens,
                source
            )
            VALUES (?, ?, ?, ?, ?, ?, 1, ?)
            ON CONFLICT(telegram_id)
            DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_name = excluded.last_name,
                last_seen = excluded.last_seen,
                opens = analytics_visitors.opens + 1,
                source = CASE
                    WHEN excluded.source != 'direct'
                    THEN excluded.source
                    ELSE analytics_visitors.source
                END
        """, (
            telegram_id,
            user.get("username"),
            user.get("first_name"),
            user.get("last_name"),
            current,
            current,
            source,
        ))

        db.execute("""
            INSERT INTO analytics_events (
                telegram_id,
                event_type,
                source,
                created_at
            )
            VALUES (?, 'open', ?, ?)
        """, (
            telegram_id,
            source,
            current
        ))

        db.commit()

        return jsonify({"success": True})

    finally:
        db.close()


@app.route("/api/analytics/directions", methods=["POST"])
def analytics_directions():
    data, user = request_user()

    if not user:
        return unauthorized()

    db = get_db()

    try:
        db.execute("""
            INSERT INTO analytics_events (
                telegram_id,
                event_type,
                source,
                created_at
            )
            VALUES (?, 'directions', ?, ?)
        """, (
            safe_int(user["id"]),
            clean_source(data.get("source")),
            now_iso(),
        ))

        db.commit()

        return jsonify({"success": True})

    finally:
        db.close()


def admin_authorized():
    if not ADMIN_PASSWORD:
        return False

    auth = request.authorization

    return bool(
        auth
        and secrets.compare_digest(
            auth.password or "",
            ADMIN_PASSWORD
        )
    )


def admin_login_required():
    return Response(
        "Authentication required",
        401,
        {
            "WWW-Authenticate":
                'Basic realm="TAKE AWAY Analytics"'
        }
    )


def analytics_data():
    db = get_db()

    try:
        def count(query, params=()):
            return db.execute(
                query,
                params
            ).fetchone()[0]

        today = now_local().date().isoformat()

        visitors = count(
            "SELECT COUNT(*) FROM analytics_visitors"
        )

        opens = count(
            "SELECT COALESCE(SUM(opens),0) FROM analytics_visitors"
        )

        returning = count(
            "SELECT COUNT(*) FROM analytics_visitors WHERE opens > 1"
        )

        unique_customers = count(
            "SELECT COUNT(DISTINCT telegram_id) FROM orders"
        )

        orders = count(
            "SELECT COUNT(*) FROM orders"
        )

        completed = count(
            "SELECT COUNT(*) FROM orders WHERE status = 'completed'"
        )

        revenue = count("""
            SELECT COALESCE(SUM(total),0)
            FROM orders
            WHERE status = 'completed'
        """)

        directions = count("""
            SELECT COUNT(*)
            FROM analytics_events
            WHERE event_type = 'directions'
        """)

        today_visitors = count("""
            SELECT COUNT(*)
            FROM analytics_visitors
            WHERE substr(last_seen,1,10) = ?
        """, (today,))

        today_orders = count("""
            SELECT COUNT(*)
            FROM orders
            WHERE substr(created_at,1,10) = ?
        """, (today,))

        today_revenue = count("""
            SELECT COALESCE(SUM(total),0)
            FROM orders
            WHERE status = 'completed'
              AND substr(created_at,1,10) = ?
        """, (today,))

        sources = [
            dict(row)
            for row in db.execute("""
                SELECT
                    COALESCE(NULLIF(source,''),'direct') AS source,
                    COUNT(*) AS count
                FROM analytics_visitors
                GROUP BY COALESCE(NULLIF(source,''),'direct')
                ORDER BY count DESC
                LIMIT 20
            """).fetchall()
        ]

        recent_visitors = [
            dict(row)
            for row in db.execute("""
                SELECT *
                FROM analytics_visitors
                ORDER BY last_seen DESC
                LIMIT 30
            """).fetchall()
        ]

        popular = {}

        completed_rows = db.execute("""
            SELECT items
            FROM orders
            WHERE status = 'completed'
        """).fetchall()

        for row in completed_rows:
            for item in order_items(row):
                name = item.get("name") or item.get("id")

                if not name:
                    continue

                popular[name] = (
                    popular.get(name, 0)
                    + safe_int(item.get("qty"))
                )

        popular_drinks = [
            {"name": name, "quantity": quantity}
            for name, quantity in sorted(
                popular.items(),
                key=lambda pair: pair[1],
                reverse=True
            )[:10]
        ]

        return {
            "visitors": visitors,
            "opens": opens,
            "returning_visitors": returning,
            "unique_customers": unique_customers,
            "conversion": (
                round(unique_customers / visitors * 100, 1)
                if visitors else 0
            ),
            "orders": orders,
            "completed": completed,
            "revenue": revenue,
            "directions": directions,
            "today": {
                "visitors": today_visitors,
                "orders": today_orders,
                "revenue": today_revenue,
            },
            "sources": sources,
            "popular_drinks": popular_drinks,
            "recent_visitors": recent_visitors,
        }

    finally:
        db.close()


@app.route("/api/admin/analytics")
def api_admin_analytics():
    if not admin_authorized():
        return admin_login_required()

    return jsonify(analytics_data())


@app.route("/admin/analytics")
def admin_analytics():
    if not admin_authorized():
        return admin_login_required()

    data = analytics_data()

    cards = [
        ("Unique visitors", data["visitors"]),
        ("App opens", data["opens"]),
        ("Returning visitors", data["returning_visitors"]),
        ("Customers", data["unique_customers"]),
        ("Conversion", f'{data["conversion"]}%'),
        ("Orders", data["orders"]),
        ("Completed", data["completed"]),
        ("Directions", data["directions"]),
        ("Revenue", f'{data["revenue"]:,} RSD'),
        ("Today's visitors", data["today"]["visitors"]),
        ("Today's orders", data["today"]["orders"]),
        ("Today's revenue", f'{data["today"]["revenue"]:,} RSD'),
    ]

    card_html = "".join(
        (
            '<div class="card">'
            f'<div class="value">{html.escape(str(value))}</div>'
            f'<div class="label">{html.escape(label)}</div>'
            '</div>'
        )
        for label, value in cards
    )

    def make_table(headers, rows):
        header_html = "".join(
            f"<th>{html.escape(str(header))}</th>"
            for header in headers
        )

        body_html = "".join(
            "<tr>"
            + "".join(
                f"<td>{html.escape(str(value))}</td>"
                for value in row
            )
            + "</tr>"
            for row in rows
        )

        return (
            "<table><thead><tr>"
            + header_html
            + "</tr></thead><tbody>"
            + (body_html or "<tr><td>No data yet</td></tr>")
            + "</tbody></table>"
        )

    source_table = make_table(
        ["Source", "Visitors"],
        [
            (row["source"], row["count"])
            for row in data["sources"]
        ]
    )

    popular_table = make_table(
        ["Drink", "Quantity"],
        [
            (row["name"], row["quantity"])
            for row in data["popular_drinks"]
        ]
    )

    visitor_table = make_table(
        ["Name", "Username", "Opens", "Source", "Last seen"],
        [
            (
                row.get("first_name") or "",
                row.get("username") or "",
                row.get("opens") or 0,
                row.get("source") or "direct",
                row.get("last_seen") or "",
            )
            for row in data["recent_visitors"]
        ]
    )

    page = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TAKE AWAY Analytics</title>
<style>
*{box-sizing:border-box}
body{
    margin:0;
    padding:24px;
    background:#032c1b;
    color:#fff;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif
}
.container{max-width:1200px;margin:auto}
h1{font-family:Georgia,serif;font-weight:400;letter-spacing:3px}
h2{font-size:20px;color:#d6b36a}
.grid{
    display:grid;
    grid-template-columns:repeat(auto-fit,minmax(170px,1fr));
    gap:12px;
    margin:25px 0
}
.card,section{
    background:#ffffff0d;
    border:1px solid #ffffff24;
    border-radius:18px;
    padding:20px
}
.value{font-size:28px;font-weight:800}
.label{color:#ffffff99;font-size:12px;margin-top:7px}
section{margin:25px 0;overflow-x:auto}
table{width:100%;border-collapse:collapse}
th,td{
    padding:12px 8px;
    text-align:left;
    border-bottom:1px solid #ffffff18;
    font-size:13px
}
th{color:#d6b36a}
</style>
</head>
<body>
<div class="container">
<h1>TAKE AWAY · Analytics</h1>
<div class="grid">__CARDS__</div>
<section><h2>Traffic sources</h2>__SOURCES__</section>
<section><h2>Popular drinks</h2>__POPULAR__</section>
<section><h2>Recent visitors</h2>__VISITORS__</section>
</div>
</body>
</html>
"""

    page = (
        page.replace("__CARDS__", card_html)
        .replace("__SOURCES__", source_table)
        .replace("__POPULAR__", popular_table)
        .replace("__VISITORS__", visitor_table)
    )

    return Response(page, mimetype="text/html")


# ============================================================
# ADMIN CALLBACKS
# ============================================================

def handle_admin_callback(callback):
    callback_id = callback.get("id")

    message = callback.get("message") or {}
    chat = message.get("chat") or {}

    chat_id = chat.get("id")
    message_id = message.get("message_id")

    if not ORDER_CHAT_ID or str(chat_id) != str(ORDER_CHAT_ID):
        answer_callback(callback_id, "Not authorized", True)
        return

    try:
        action, raw_order_id = str(
            callback.get("data") or ""
        ).split(":", 1)

        order_id = int(raw_order_id)

    except (ValueError, TypeError):
        answer_callback(callback_id, "Invalid action", True)
        return

    if action not in {"accept", "ready", "complete", "cancel"}:
        answer_callback(callback_id, "Unknown action", True)
        return

    order = get_order(order_id)

    if not order:
        answer_callback(callback_id, "Order not found", True)
        return

    current_status = order["status"]
    telegram_id = safe_int(order["telegram_id"])

    if action == "accept":
        if current_status != "new":
            answer_callback(
                callback_id,
                f"Order is {current_status}",
                True
            )
            return

        updated = set_order_status(
            order_id,
            "preparing",
            {"new"}
        )

        new_status = "preparing"

    elif action == "ready":
        if current_status != "preparing":
            answer_callback(
                callback_id,
                f"Order is {current_status}",
                True
            )
            return

        updated = set_order_status(
            order_id,
            "ready",
            {"preparing"}
        )

        new_status = "ready"

    elif action == "complete":
        if current_status != "ready":
            answer_callback(
                callback_id,
                f"Order is {current_status}",
                True
            )
            return

        updated = mark_paid_and_completed(order_id)
        new_status = "completed"

    else:
        updated = cancel_order(order_id)
        new_status = "cancelled"

    if not updated:
        answer_callback(
            callback_id,
            "Order could not be updated",
            True
        )
        return

    send_message(
        telegram_id,
        customer_status_message(
            order_id,
            new_status
        )
    )

    if chat_id and message_id:
        edit_message_markup(
            chat_id,
            message_id,
            admin_keyboard(order_id, new_status)
        )

    answer_callback(
        callback_id,
        f"Order {new_status}"
    )


# ============================================================
# BOT MESSAGES
# ============================================================

def bot_start_text():
    return (
        "☕ <b>WELCOME TO TAKE AWAY</b>\n"
        "<i>Thai Tea & Coffee · Belgrade</i>\n\n"
        "A little taste of Thailand in the heart of Belgrade. 🇹🇭\n\n"
        "🫖 Authentic Thai Tea\n"
        "☕ Specialty Coffee\n"
        "✦ Signature Drinks\n\n"
        "📍 Savski trg 1a\n\n"
        "<b>Order ahead & skip the wait ↓</b>\n\n"
        "━━━━━━━━━━━━━━\n\n"
        "🇷🇸 <b>DOBRO DOŠLI U TAKE AWAY</b>\n\n"
        "Ukus Tajlanda u srcu Beograda. 🇹🇭\n\n"
        "🫖 Tajlandski čaj\n"
        "☕ Specialty kafa\n"
        "✦ Signature napici\n\n"
        "<b>Poruči unapred, bez čekanja ↓</b>"
    )


def web_app_keyboard():
    return {
        "inline_keyboard": [[
            {
                "text": "☕ OPEN TAKE AWAY",
                "web_app": {
                    "url": APP_URL
                }
            }
        ]]
    }


def handle_bot_message(message):
    chat_id = (message.get("chat") or {}).get("id")
    text = str(message.get("text") or "").strip()

    if not chat_id:
        return

    command = ""

    if text.startswith("/"):
        command = (
            text.split(None, 1)[0]
            .split("@", 1)[0]
            .lower()
        )

    if command in {"/start", "/menu"}:
        send_message(
            chat_id,
            bot_start_text(),
            web_app_keyboard()
        )

    elif command == "/help":
        send_message(
            chat_id,
            (
                "☕ <b>TAKE AWAY Help</b>\n\n"
                "Open the app, choose your drinks "
                "and pickup time.\n"
                "Pay at the café by card or cash."
            ),
            web_app_keyboard()
        )


# ============================================================
# TELEGRAM WEBHOOK
# ============================================================

@app.route("/webhook", methods=["POST"])
def webhook():
    if WEBHOOK_SECRET:
        supplied = request.headers.get(
            "X-Telegram-Bot-Api-Secret-Token",
            ""
        )

        if not secrets.compare_digest(
            supplied,
            WEBHOOK_SECRET
        ):
            return jsonify({
                "ok": False,
                "error": "Invalid webhook secret"
            }), 403

    update = request.get_json(silent=True) or {}

    try:
        if update.get("callback_query"):
            handle_admin_callback(
                update["callback_query"]
            )

        elif update.get("message"):
            handle_bot_message(
                update["message"]
            )

    except Exception:
        logging.exception("Webhook processing failed")

    return jsonify({"ok": True})


# ============================================================
# WEBHOOK STATUS
# ============================================================

@app.route("/webhook-status")
def webhook_status():
    if not BOT_TOKEN:
        return jsonify({
            "success": False,
            "error": "BOT_TOKEN missing"
        }), 500

    return jsonify({
        "expected_webhook": f"{APP_URL}/webhook",
        "bot": telegram_api("getMe"),
        "webhook": telegram_api("getWebhookInfo"),
        "admin_chat_id_configured": bool(ORDER_CHAT_ID),
        "webhook_secret_configured": bool(WEBHOOK_SECRET),
    })


# ============================================================
# WEBHOOK SETUP
# ============================================================

@app.route("/setup-webhook")
def setup_webhook():
    if not admin_authorized():
        return admin_login_required()

    if not BOT_TOKEN:
        return jsonify({
            "success": False,
            "error": "BOT_TOKEN missing"
        }), 500

    bot = telegram_api("getMe")

    if not bot.get("ok"):
        return jsonify({
            "success": False,
            "error": "Invalid bot token",
            "telegram": bot,
        }), 500

    payload = {
        "url": f"{APP_URL}/webhook",
        "allowed_updates": [
            "message",
            "callback_query"
        ],
        "drop_pending_updates": False,
    }

    if WEBHOOK_SECRET:
        payload["secret_token"] = WEBHOOK_SECRET

    result = telegram_api("setWebhook", payload)

    return jsonify({
        "success": bool(result.get("ok")),
        "set_webhook": result,
        "webhook_info": telegram_api("getWebhookInfo"),
    })


# ============================================================
# TELEGRAM TEST
# ============================================================

@app.route("/telegram-test")
def telegram_test():
    if not admin_authorized():
        return admin_login_required()

    bot = telegram_api("getMe")

    admin_test = None

    if ORDER_CHAT_ID:
        admin_test = send_message(
            ORDER_CHAT_ID,
            "🧪 <b>TAKE AWAY BOT TEST</b>\n\n"
            "Telegram connection is working."
        )

    return jsonify({
        "success": bool(bot.get("ok")),
        "bot": bot,
        "webhook": telegram_api("getWebhookInfo"),
        "admin_test": admin_test,
    })


# ============================================================
# START APPLICATION
# ============================================================

if __name__ == "__main__":
    port = safe_int(
        os.environ.get("PORT", "10000"),
        10000
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False
    )
