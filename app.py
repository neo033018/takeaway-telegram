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

from flask import (
    Flask,
    request,
    jsonify,
    render_template,
    Response
)


# ============================================================
# TAKE AWAY — THAI TEA & COFFEE
# Telegram Mini App Backend
# ============================================================

app = Flask(__name__)

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("BOT_TOKEN", "").strip()

URL = os.getenv(
    "APP_URL",
    "https://takeaway-telegram.onrender.com"
).rstrip("/")

ADMIN = os.getenv("ORDER_CHAT_ID", "").strip()

DB = os.getenv("DATABASE_PATH", "orders.db")

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

TZ = ZoneInfo("Europe/Belgrade")

OPEN_HOUR = 8
CLOSE_HOUR = 17

MIN_PREP_MINUTES = 10

MAX_ITEM_QUANTITY = 10
MAX_ORDER_QUANTITY = 30


# ============================================================
# MENU
# ============================================================

MENU = [
    dict(
        id="thai",
        name="Classic Thai Milk Tea",
        price=450,
        category="tea",
        boba=True,
        temperature=True
    ),
    dict(
        id="green",
        name="Thai Green Milk Tea",
        price=450,
        category="tea",
        boba=True,
        temperature=True
    ),
    dict(
        id="espresso",
        name="Espresso",
        price=200,
        category="coffee"
    ),
    dict(
        id="americano",
        name="Americano",
        price=250,
        category="coffee",
        temperature=True,
        iced_surcharge=20
    ),
    dict(
        id="cappuccino",
        name="Cappuccino",
        price=320,
        category="coffee"
    ),
    dict(
        id="latte",
        name="Latte",
        price=350,
        category="coffee",
        temperature=True,
        iced_surcharge=20
    ),
    dict(
        id="flatwhite",
        name="Flat White",
        price=450,
        category="coffee"
    ),
    dict(
        id="americanocream",
        name="Iced Americano with Cream",
        price=450,
        category="coffee"
    ),
    dict(
        id="tonic",
        name="Tonic Coffee",
        price=420,
        category="special"
    ),
    dict(
        id="bumble",
        name="Bumble Coffee",
        price=450,
        category="special"
    ),
    dict(
        id="strawberrymatcha",
        name="Strawberry Matcha Latte",
        price=550,
        category="special"
    ),
    dict(
        id="matchalatte",
        name="Matcha Latte",
        price=480,
        category="special",
        temperature=True,
        iced_surcharge=20
    ),
    dict(
        id="dirtymatcha",
        name="Dirty Matcha Latte",
        price=550,
        category="special",
        temperature=True,
        iced_surcharge=20
    ),
    dict(
        id="pumpkinlatte",
        name="Pumpkin Latte",
        price=480,
        category="special",
        temperature=True,
        iced_surcharge=20,
        tag="AUTUMN SPECIAL"
    ),
    dict(
        id="kuvanovino",
        name="Kuvano Vino",
        price=400,
        category="special",
        tag="WINTER SPECIAL"
    )
]

MENU_BY_ID = {
    product["id"]: product
    for product in MENU
}

RETIRED = {
    "lime": "Thai Iced Lime Tea",
    "peanutlatte": "Peanut Latte"
}

COFFEE_IDS = {
    "espresso",
    "americano",
    "cappuccino",
    "latte",
    "flatwhite",
    "americanocream",
    "tonic",
    "bumble",
    "pumpkinlatte",
    "dirtymatcha"
}

SYRUP_ALLOWED_IDS = set(COFFEE_IDS)

SYRUPS = {
    "vanilla": "Vanilla",
    "salted_caramel": "Salted Caramel",
    "strawberry": "Strawberry"
}

TEMPERATURE_IDS = {
    product["id"]
    for product in MENU
    if product.get("temperature")
}

LEGACY_ITEM_MAP = {
    "icedamericano": ("americano", "iced"),
    "icedlatte": ("latte", "iced")
}


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
    except (ValueError, TypeError):
        return default


# ============================================================
# DATABASE
# ============================================================

def get_db():
    db = sqlite3.connect(
        DB,
        timeout=20,
        check_same_thread=False
    )

    db.row_factory = sqlite3.Row

    return db


def ensure_column(db, table, name, definition):
    columns = {
        row["name"]
        for row in db.execute(
            f"PRAGMA table_info({table})"
        )
    }

    if name not in columns:
        db.execute(
            f"ALTER TABLE {table} "
            f"ADD COLUMN {name} {definition}"
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

        columns = [
            (
                "payment_method",
                "TEXT DEFAULT 'pickup'"
            ),
            (
                "payment_status",
                "TEXT DEFAULT 'pending'"
            ),
            (
                "reward_used",
                "INTEGER DEFAULT 0"
            ),
            (
                "reward_item_index",
                "INTEGER"
            ),
            (
                "reward_processed",
                "INTEGER DEFAULT 0"
            )
        ]

        for name, definition in columns:
            ensure_column(
                db,
                "orders",
                name,
                definition
            )

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

        indexes = [
            (
                "idx_orders_telegram",
                "orders",
                "telegram_id"
            ),
            (
                "idx_orders_created",
                "orders",
                "created_at"
            ),
            (
                "idx_orders_status",
                "orders",
                "status"
            ),
            (
                "idx_analytics_events_created",
                "analytics_events",
                "created_at"
            ),
            (
                "idx_analytics_events_type",
                "analytics_events",
                "event_type"
            )
        ]

        for index, table, column in indexes:
            db.execute(
                f"CREATE INDEX IF NOT EXISTS {index} "
                f"ON {table}({column})"
            )

        db.commit()

    finally:
        db.close()


init_db()


# ============================================================
# TELEGRAM AUTHENTICATION
# ============================================================

def parse_datetime(value):
    try:
        dt = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )

        if not dt.tzinfo:
            dt = dt.replace(tzinfo=TZ)

        return dt.astimezone(TZ)

    except (ValueError, TypeError):
        return None


def verify_init_data(init_data):
    if not TOKEN or not init_data:
        return None

    try:
        fields = dict(
            parse_qsl(
                init_data,
                keep_blank_values=True
            )
        )

        digest = fields.pop("hash", None)

        auth = safe_int(
            fields.get("auth_date")
        )

        if (
            not digest
            or not auth
            or abs(int(time.time()) - auth) > 86400
        ):
            return None

        check = "\n".join(
            f"{key}={fields[key]}"
            for key in sorted(fields)
        )

        secret_key = hmac.new(
            b"WebAppData",
            TOKEN.encode(),
            hashlib.sha256
        ).digest()

        expected = hmac.new(
            secret_key,
            check.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(
            expected,
            digest
        ):
            return None

        user = json.loads(
            fields["user"]
        )

        return user if user.get("id") else None

    except (ValueError, KeyError, TypeError):
        return None


def request_user():
    data = request.get_json(
        silent=True
    ) or {}

    user = verify_init_data(
        data.get("initData", "")
    )

    return data, user


# ============================================================
# LOYALTY REWARDS
# ============================================================

def details(row):
    try:
        return json.loads(
            row["items"] or "[]"
        )
    except (ValueError, TypeError):
        return []


def ensure_loyalty_account(db, telegram_id):
    db.execute("""
        INSERT OR IGNORE INTO loyalty_accounts
        (
            telegram_id,
            stamps,
            free_drinks,
            updated_at
        )
        VALUES (?, 0, 0, ?)
    """, (
        telegram_id,
        now_iso()
    ))

    return db.execute("""
        SELECT *
        FROM loyalty_accounts
        WHERE telegram_id = ?
    """, (
        telegram_id,
    )).fetchone()


def get_rewards(telegram_id):
    db = get_db()

    try:
        row = ensure_loyalty_account(
            db,
            telegram_id
        )

        db.commit()

        return {
            "stamps": safe_int(
                row["stamps"]
            ),
            "free_drinks": safe_int(
                row["free_drinks"]
            )
        }

    finally:
        db.close()


def consume_reward(db, telegram_id):
    ensure_loyalty_account(
        db,
        telegram_id
    )

    cursor = db.execute("""
        UPDATE loyalty_accounts
        SET
            free_drinks = free_drinks - 1,
            updated_at = ?
        WHERE
            telegram_id = ?
            AND free_drinks > 0
    """, (
        now_iso(),
        telegram_id
    ))

    return cursor.rowcount == 1


def restore_reward(db, telegram_id):
    ensure_loyalty_account(
        db,
        telegram_id
    )

    db.execute("""
        UPDATE loyalty_accounts
        SET
            free_drinks = free_drinks + 1,
            updated_at = ?
        WHERE telegram_id = ?
    """, (
        now_iso(),
        telegram_id
    ))


def add_loyalty_drinks(
    db,
    telegram_id,
    quantity
):
    if quantity <= 0:
        return

    row = ensure_loyalty_account(
        db,
        telegram_id
    )

    total = (
        safe_int(row["stamps"])
        + quantity
    )

    db.execute("""
        UPDATE loyalty_accounts
        SET
            stamps = ?,
            free_drinks = ?,
            updated_at = ?
        WHERE telegram_id = ?
    """, (
        total % 9,
        safe_int(row["free_drinks"])
        + total // 9,
        now_iso(),
        telegram_id
    ))


# ============================================================
# ORDER MANAGEMENT
# ============================================================

def get_order(order_id):
    db = get_db()

    try:
        return db.execute("""
            SELECT *
            FROM orders
            WHERE id = ?
        """, (
            order_id,
        )).fetchone()

    finally:
        db.close()


def mark_paid_and_completed(order_id):
    db = get_db()

    try:
        db.execute(
            "BEGIN IMMEDIATE"
        )

        row = db.execute("""
            SELECT *
            FROM orders
            WHERE id = ?
        """, (
            order_id,
        )).fetchone()

        if not row or row["status"] != "ready":
            db.rollback()
            return False

        if not safe_int(
            row["reward_processed"]
        ):
            paid = sum(
                safe_int(item.get("qty"))
                for item in details(row)
                if not item.get("reward_free")
            )

            add_loyalty_drinks(
                db,
                safe_int(row["telegram_id"]),
                paid
            )

        db.execute("""
            UPDATE orders
            SET
                status = 'completed',
                payment_status = 'paid',
                reward_processed = 1,
                updated_at = ?
            WHERE id = ?
        """, (
            now_iso(),
            order_id
        ))

        db.commit()

        return True

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


# ============================================================
# PRICE CALCULATION
# ============================================================

def calculate_item(
    raw,
    reward_free=False
):
    raw_id = str(
        raw.get("id") or ""
    ).strip()

    item_id, forced = LEGACY_ITEM_MAP.get(
        raw_id,
        (
            raw_id,
            raw.get("temperature")
        )
    )

    product = MENU_BY_ID.get(
        item_id
    )

    if not product:
        raise ValueError(
            f"Unknown drink: {raw_id}"
        )

    qty = safe_int(
        raw.get("qty"),
        1
    )

    if qty < 1 or qty > MAX_ITEM_QUANTITY:
        raise ValueError(
            "Invalid drink quantity (1–10)."
        )

    if reward_free and qty != 1:
        raise ValueError(
            "A free reward must be one drink."
        )

    temperature = (
        str(
            forced or "hot"
        ).lower()
        if item_id in TEMPERATURE_IDS
        else None
    )

    if temperature not in (
        "hot",
        "iced",
        None
    ):
        raise ValueError(
            "Invalid temperature"
        )

    extra_shot = (
        item_id in COFFEE_IDS
        and raw.get("extra_shot") is True
    )

    boba = (
        item_id in ("thai", "green")
        and raw.get("boba") is True
    )

    syrup = (
        str(
            raw.get("syrup") or ""
        ).lower()
        if item_id in SYRUP_ALLOWED_IDS
        else ""
    )

    syrup = (
        syrup
        if syrup in SYRUPS
        else None
    )

    iced = (
        safe_int(
            product.get("iced_surcharge")
        )
        if temperature == "iced"
        else 0
    )

    base = product["price"]

    extra = (
        (150 if extra_shot else 0)
        + (100 if boba else 0)
        + iced
    )

    unit = base + extra

    return {
        "id": item_id,
        "name": product["name"],
        "qty": qty,
        "base_price": base,
        "unit_price": unit,
        "line_total": (
            extra if reward_free else unit
        ) * qty,
        "extra_shot": extra_shot,
        "boba": boba,
        "syrup": syrup,
        "temperature": temperature,
        "reward_free": bool(reward_free)
    }


def calculate_order_items(
    raw_items,
    reward_item_index=None
):
    if (
        not isinstance(raw_items, list)
        or not raw_items
    ):
        raise ValueError(
            "Order is empty"
        )

    reward_index = None

    if reward_item_index is not None:
        if isinstance(
            reward_item_index,
            bool
        ):
            raise ValueError(
                "Invalid reward selection"
            )

        try:
            reward_index = int(
                reward_item_index
            )

        except (TypeError, ValueError):
            raise ValueError(
                "Invalid reward selection"
            )

        if (
            reward_index < 0
            or reward_index >= len(raw_items)
        ):
            raise ValueError(
                "Invalid reward selection"
            )

    items = []

    for index, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise ValueError(
                "Invalid item"
            )

        item = calculate_item(
            raw,
            index == reward_index
        )

        items.append(item)

    if sum(
        item["qty"]
        for item in items
    ) > MAX_ORDER_QUANTITY:
        raise ValueError(
            "Maximum 30 drinks per order."
        )

    total = sum(
        item["line_total"]
        for item in items
    )

    return items, total, reward_index


# ============================================================
# PICKUP VALIDATION
# ============================================================

def validate_pickup(value):
    pickup = parse_datetime(value)

    if not pickup:
        return None, "Invalid pickup time."

    now = now_local()

    if pickup.date() != now.date():
        return (
            None,
            "Pickup is available today only."
        )

    if pickup < now + timedelta(
        minutes=MIN_PREP_MINUTES - 2
    ):
        return (
            None,
            "Please allow at least 10 minutes "
            "for preparation."
        )

    if not (
        OPEN_HOUR <= pickup.hour < CLOSE_HOUR
    ):
        return (
            None,
            f"Pickup is available between "
            f"{OPEN_HOUR:02d}:00 and "
            f"{CLOSE_HOUR:02d}:00."
        )

    return pickup, None


def pretty_pickup(value):
    dt = parse_datetime(value)

    return (
        dt.strftime("%H:%M")
        if dt
        else str(value or "")
    )


def pretty_created(value):
    dt = parse_datetime(value)

    return (
        dt.strftime("%d.%m.%Y %H:%M")
        if dt
        else str(value or "")
    )


def serialize_order(row):
    return {
        "id": row["id"],
        "items": details(row),
        "total": safe_int(row["total"]),
        "pickup": pretty_pickup(
            row["pickup"]
        ),
        "pickup_iso": row["pickup"],
        "status": row["status"],
        "payment_method": row["payment_method"],
        "payment_status": row["payment_status"],
        "reward_used": bool(
            row["reward_used"]
        ),
        "created": pretty_created(
            row["created_at"]
        ),
        "created_at": row["created_at"]
    }


# ============================================================
# TELEGRAM API
# ============================================================

def telegram_api(
    method,
    payload=None,
    timeout=20
):
    if not TOKEN:
        return {
            "ok": False,
            "description": "BOT_TOKEN missing"
        }

    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TOKEN}/{method}",
            json=payload or {},
            timeout=timeout
        )

        result = response.json()

        if (
            not response.ok
            or not result.get("ok")
        ):
            logging.error(
                "Telegram %s failed: %s",
                method,
                result
            )

        return result

    except Exception:
        logging.exception(
            "Telegram %s failed",
            method
        )

        return {
            "ok": False
        }


def send_message(
    chat_id,
    text,
    reply_markup=None
):
    if not chat_id:
        return None

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    if reply_markup is not None:
        payload["reply_markup"] = reply_markup

    return telegram_api(
        "sendMessage",
        payload
    )


def answer_callback(
    callback_id,
    text="",
    show_alert=False
):
    if callback_id:
        telegram_api(
            "answerCallbackQuery",
            {
                "callback_query_id": callback_id,
                "text": text,
                "show_alert": show_alert
            }
        )


# ============================================================
# BARISTA BUTTONS
# ============================================================

def admin_keyboard(
    order_id,
    status="new"
):
    options = {
        "new": [
            ("✅ ACCEPT", "accept"),
            ("❌ CANCEL", "cancel")
        ],
        "preparing": [
            ("☕ READY", "ready"),
            ("❌ CANCEL", "cancel")
        ],
        "ready": [
            ("💳 PAID / PICKED UP", "complete"),
            ("❌ CANCEL", "cancel")
        ]
    }.get(status, [])

    return {
        "inline_keyboard": [
            [
                {
                    "text": label,
                    "callback_data": (
                        f"{action}:{order_id}"
                    )
                }
            ]
            for label, action in options
        ]
    }


def barista_text(
    order_id,
    user,
    items,
    total,
    pickup
):
    name = html.escape(
        " ".join(
            filter(
                None,
                [
                    user.get("first_name"),
                    user.get("last_name")
                ]
            )
        ) or "Customer"
    )

    lines = [
        "☕ <b>NEW TAKE AWAY ORDER</b>",
        "",
        f"🧾 <b>Order #{order_id}</b>",
        f"👤 {name}"
    ]

    if user.get("username"):
        lines.append(
            "📱 @"
            + html.escape(
                str(user["username"])
            )
        )

    lines += [
        f"🕒 Pickup: <b>{pretty_pickup(pickup)}</b>",
        "💳 Payment: <b>PAY AT PICKUP</b>",
        "",
        "<b>DRINKS</b>"
    ]

    for item in items:
        icon = (
            "🎁"
            if item["reward_free"]
            else "•"
        )

        lines.append(
            f"{icon} <b>{item['qty']} × "
            f"{html.escape(item['name'])}</b>"
            f" — {item['line_total']} RSD"
        )

        if item["temperature"]:
            if item["temperature"] == "hot":
                lines.append(
                    "☕ HOT"
                )
            else:
                surcharge = MENU_BY_ID[
                    item["id"]
                ].get(
                    "iced_surcharge",
                    0
                )

                text = "🧊 ICED"

                if surcharge:
                    text += (
                        f" +{surcharge} RSD"
                    )

                lines.append(text)

        if item["extra_shot"]:
            lines.append(
                "➕ Extra shot +150 RSD"
            )

        if item["boba"]:
            lines.append(
                "🧋 Boba +100 RSD"
            )

        if item["syrup"]:
            lines.append(
                "🍯 "
                + SYRUPS[item["syrup"]]
                + " · FREE"
            )

        if item["reward_free"]:
            lines.append(
                "🎁 FREE DRINK REWARD"
            )

    lines += [
        "",
        f"💰 <b>TOTAL: {total} RSD</b>",
        "Payment at café by card or cash."
    ]

    return "\n".join(lines)


def customer_status_message(
    order_id,
    status
):
    messages = {
        "preparing": (
            "Your order has been accepted "
            "and is being prepared."
        ),
        "ready": (
            "Your order is ready! "
            "Pay at the café by card or cash."
        ),
        "completed": (
            "Thank you for choosing TAKE AWAY!"
        ),
        "cancelled": (
            "Your order was cancelled. "
            "Please contact TAKE AWAY "
            "if you need help."
        )
    }

    return (
        f"☕ <b>Order #{order_id}</b>\n\n"
        + messages.get(status, status)
    )


# ============================================================
# ORDER STATUS
# ============================================================

def set_order_status(
    order_id,
    new_status,
    allowed_from
):
    db = get_db()

    try:
        db.execute(
            "BEGIN IMMEDIATE"
        )

        row = db.execute("""
            SELECT *
            FROM orders
            WHERE id = ?
        """, (
            order_id,
        )).fetchone()

        if (
            not row
            or row["status"] not in allowed_from
        ):
            db.rollback()
            return False

        db.execute("""
            UPDATE orders
            SET
                status = ?,
                updated_at = ?
            WHERE id = ?
        """, (
            new_status,
            now_iso(),
            order_id
        ))

        db.commit()

        return True

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


def cancel_order(order_id):
    db = get_db()

    try:
        db.execute(
            "BEGIN IMMEDIATE"
        )

        row = db.execute("""
            SELECT *
            FROM orders
            WHERE id = ?
        """, (
            order_id,
        )).fetchone()

        if (
            not row
            or row["status"] in (
                "completed",
                "cancelled"
            )
        ):
            db.rollback()
            return False

        if safe_int(
            row["reward_used"]
        ):
            restore_reward(
                db,
                safe_int(
                    row["telegram_id"]
                )
            )

        db.execute("""
            UPDATE orders
            SET
                status = 'cancelled',
                reward_used = 0,
                updated_at = ?
            WHERE id = ?
        """, (
            now_iso(),
            order_id
        ))

        db.commit()

        return True

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


def handle_admin_callback(callback):
    callback_id = callback.get("id")

    data = str(
        callback.get("data") or ""
    )

    message = (
        callback.get("message")
        or {}
    )

    chat = (
        message.get("chat")
        or {}
    )

    chat_id = chat.get("id")

    message_id = message.get(
        "message_id"
    )

    if (
        not ADMIN
        or str(chat_id) != str(ADMIN)
    ):
        answer_callback(
            callback_id,
            "Not authorized",
            True
        )
        return

    try:
        action, raw_id = data.split(
            ":",
            1
        )

        order_id = int(raw_id)

    except (ValueError, TypeError):
        answer_callback(
            callback_id,
            "Invalid action",
            True
        )
        return

    if action not in (
        "accept",
        "ready",
        "complete",
        "cancel"
    ):
        answer_callback(
            callback_id,
            "Unknown action",
            True
        )
        return

    order = get_order(order_id)

    if not order:
        answer_callback(
            callback_id,
            "Order not found",
            True
        )
        return

    current = order["status"]

    new = {
        "accept": "preparing",
        "ready": "ready",
        "complete": "completed",
        "cancel": "cancelled"
    }[action]

    allowed = {
        "accept": "new",
        "ready": "preparing",
        "complete": "ready"
    }

    if (
        action in allowed
        and current != allowed[action]
    ):
        answer_callback(
            callback_id,
            f"Order is {current}",
            True
        )
        return

    if (
        action == "cancel"
        and current in (
            "cancelled",
            "completed"
        )
    ):
        answer_callback(
            callback_id,
            f"Order is {current}",
            True
        )
        return

    try:
        if action == "complete":
            success = mark_paid_and_completed(
                order_id
            )

        elif action == "cancel":
            success = cancel_order(
                order_id
            )

        else:
            success = set_order_status(
                order_id,
                new,
                {allowed[action]}
            )

    except Exception:
        logging.exception(
            "Order status failed"
        )

        answer_callback(
            callback_id,
            "Could not update order",
            True
        )

        return

    if not success:
        answer_callback(
            callback_id,
            "Order status changed; refresh",
            True
        )
        return

    send_message(
        order["telegram_id"],
        customer_status_message(
            order_id,
            new
        )
    )

    if chat_id and message_id:
        telegram_api(
            "editMessageReplyMarkup",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "reply_markup": admin_keyboard(
                    order_id,
                    new
                )
            }
        )

    answer_callback(
        callback_id,
        f"Order {new}"
    )


# ============================================================
# MAIN PAGE
# ============================================================

@app.route("/")
def index():
    return render_template(
        "index.html"
    )


@app.route("/health")
def health():
    return jsonify(
        ok=True,
        service="TAKE AWAY",
        payment_mode="pickup",
        bot_configured=bool(TOKEN),
        admin_configured=bool(ADMIN),
        app_url=URL,
        time=now_iso()
    )


@app.route("/api/menu")
def api_menu():
    return jsonify(MENU)


# ============================================================
# ANALYTICS
# ============================================================

def clean_source(value):
    return (
        str(value or "").strip()
        or "direct"
    )[:100]


@app.route(
    "/api/analytics/open",
    methods=["POST"]
)
def analytics_open():
    data, user = request_user()

    if not user:
        return jsonify(
            error="Invalid Telegram session"
        ), 401

    uid = safe_int(
        user["id"]
    )

    source = clean_source(
        data.get("source")
    )

    now = now_iso()

    db = get_db()

    try:
        db.execute("""
            INSERT INTO analytics_visitors
            (
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
            uid,
            user.get("username"),
            user.get("first_name"),
            user.get("last_name"),
            now,
            now,
            source
        ))

        db.execute("""
            INSERT INTO analytics_events
            (
                telegram_id,
                event_type,
                source,
                created_at
            )
            VALUES (?, 'open', ?, ?)
        """, (
            uid,
            source,
            now
        ))

        db.commit()

    finally:
        db.close()

    return jsonify(
        success=True
    )


@app.route(
    "/api/analytics/directions",
    methods=["POST"]
)
def analytics_directions():
    data, user = request_user()

    if not user:
        return jsonify(
            error="Invalid Telegram session"
        ), 401

    db = get_db()

    try:
        db.execute("""
            INSERT INTO analytics_events
            (
                telegram_id,
                event_type,
                source,
                created_at
            )
            VALUES (?, 'directions', ?, ?)
        """, (
            safe_int(user["id"]),
            clean_source(
                data.get("source")
            ),
            now_iso()
        ))

        db.commit()

    finally:
        db.close()

    return jsonify(
        success=True
    )


# ============================================================
# ADMIN AUTHENTICATION
# ============================================================

def admin_authorized():
    auth = request.authorization

    return bool(
        ADMIN_PASSWORD
        and auth
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


# ============================================================
# ANALYTICS DATA
# ============================================================

def analytics_data():
    db = get_db()

    try:
        def scalar(query, args=()):
            return db.execute(
                query,
                args
            ).fetchone()[0]

        today = (
            now_local()
            .date()
            .isoformat()
        )

        visitors = scalar("""
            SELECT COUNT(*)
            FROM analytics_visitors
        """)

        unique = scalar("""
            SELECT COUNT(DISTINCT telegram_id)
            FROM orders
        """)

        popular = {}

        for row in db.execute("""
            SELECT items
            FROM orders
            WHERE status = 'completed'
        """):
            for item in details(row):
                pid = item.get("id")

                pid = LEGACY_ITEM_MAP.get(
                    pid,
                    (pid, None)
                )[0]

                name = (
                    (
                        MENU_BY_ID.get(pid)
                        or {}
                    ).get("name")
                    or RETIRED.get(pid)
                    or item.get("name")
                )

                if name:
                    popular[name] = (
                        popular.get(name, 0)
                        + safe_int(
                            item.get("qty")
                        )
                    )

        return {
            "visitors": visitors,

            "opens": scalar("""
                SELECT COALESCE(
                    SUM(opens),
                    0
                )
                FROM analytics_visitors
            """),

            "returning_visitors": scalar("""
                SELECT COUNT(*)
                FROM analytics_visitors
                WHERE opens > 1
            """),

            "unique_customers": unique,

            "conversion": (
                round(
                    100 * unique / visitors,
                    1
                )
                if visitors
                else 0
            ),

            "orders": scalar("""
                SELECT COUNT(*)
                FROM orders
            """),

            "completed": scalar("""
                SELECT COUNT(*)
                FROM orders
                WHERE status = 'completed'
            """),

            "directions": scalar("""
                SELECT COUNT(*)
                FROM analytics_events
                WHERE event_type = 'directions'
            """),

            "revenue": scalar("""
                SELECT COALESCE(
                    SUM(total),
                    0
                )
                FROM orders
                WHERE status = 'completed'
            """),

            "today": {
                "visitors": scalar("""
                    SELECT COUNT(*)
                    FROM analytics_visitors
                    WHERE substr(last_seen, 1, 10) = ?
                """, (
                    today,
                )),

                "orders": scalar("""
                    SELECT COUNT(*)
                    FROM orders
                    WHERE substr(created_at, 1, 10) = ?
                """, (
                    today,
                )),

                "revenue": scalar("""
                    SELECT COALESCE(
                        SUM(total),
                        0
                    )
                    FROM orders
                    WHERE
                        substr(created_at, 1, 10) = ?
                        AND status = 'completed'
                """, (
                    today,
                ))
            },

            "sources": [
                {
                    "source": row[0] or "direct",
                    "count": row[1]
                }
                for row in db.execute("""
                    SELECT
                        COALESCE(
                            NULLIF(source, ''),
                            'direct'
                        ),
                        COUNT(*)
                    FROM analytics_visitors
                    GROUP BY 1
                    ORDER BY 2 DESC
                    LIMIT 20
                """)
            ],

            "popular_drinks": [
                {
                    "name": name,
                    "quantity": quantity
                }
                for name, quantity in sorted(
                    popular.items(),
                    key=lambda item: -item[1]
                )[:10]
            ],

            "recent_visitors": [
                dict(row)
                for row in db.execute("""
                    SELECT
                        telegram_id,
                        username,
                        first_name,
                        last_name,
                        first_seen,
                        last_seen,
                        opens,
                        source
                    FROM analytics_visitors
                    ORDER BY last_seen DESC
                    LIMIT 30
                """)
            ]
        }

    finally:
        db.close()


@app.route("/api/admin/analytics")
def api_admin_analytics():
    if not admin_authorized():
        return admin_login_required()

    return jsonify(
        analytics_data()
    )


# ============================================================
# ADMIN ANALYTICS DASHBOARD
# ============================================================

@app.route("/admin/analytics")
def admin_analytics():
    if not admin_authorized():
        return admin_login_required()

    data = analytics_data()

    esc = lambda value: html.escape(
        str(value)
    )

    metrics = [
        ("Visitors", data["visitors"]),
        ("Opens", data["opens"]),
        (
            "Returning",
            data["returning_visitors"]
        ),
        (
            "Customers",
            data["unique_customers"]
        ),
        (
            "Conversion %",
            data["conversion"]
        ),
        ("Orders", data["orders"]),
        ("Completed", data["completed"]),
        ("Directions", data["directions"]),
        ("Revenue RSD", data["revenue"]),
        (
            "Today visitors",
            data["today"]["visitors"]
        ),
        (
            "Today orders",
            data["today"]["orders"]
        ),
        (
            "Today revenue",
            data["today"]["revenue"]
        )
    ]

    cards = "".join(
        (
            '<div class="card">'
            f"<b>{esc(value)}</b>"
            f"<small>{esc(label)}</small>"
            "</div>"
        )
        for label, value in metrics
    )

    def table(title, headings, rows):
        result = (
            "<h2>"
            + esc(title)
            + "</h2><table><tr>"
        )

        result += "".join(
            "<th>"
            + esc(heading)
            + "</th>"
            for heading in headings
        )

        result += "</tr>"

        for row in rows:
            result += "<tr>"

            for value in row:
                result += (
                    "<td>"
                    + esc(value)
                    + "</td>"
                )

            result += "</tr>"

        return result + "</table>"

    body = table(
        "Traffic sources",
        ["Source", "Visitors"],
        [
            (
                item["source"],
                item["count"]
            )
            for item in data["sources"]
        ]
    )

    body += table(
        "Popular drinks",
        ["Drink", "Quantity"],
        [
            (
                item["name"],
                item["quantity"]
            )
            for item in data["popular_drinks"]
        ]
    )

    body += table(
        "Recent visitors",
        [
            "Name",
            "Username",
            "Opens",
            "Source",
            "Last seen"
        ],
        [
            (
                item["first_name"],
                item["username"],
                item["opens"],
                item["source"],
                item["last_seen"]
            )
            for item in data["recent_visitors"]
        ]
    )

    page = """
    <!doctype html>
    <html>
    <head>
      <meta name="viewport"
            content="width=device-width,initial-scale=1">
      <title>TAKE AWAY Analytics</title>
      <style>
        body {
          font-family: Arial;
          background: #032c1b;
          color: white;
          padding: 24px;
        }
        h1 {font-family: Georgia}
        .grid {
          display: grid;
          grid-template-columns:
            repeat(auto-fit,minmax(160px,1fr));
          gap: 12px;
        }
        .card {
          padding: 20px;
          background: #ffffff18;
          border-radius: 15px;
        }
        .card b {
          font-size: 26px;
          display: block;
        }
        .card small {color:#d6b36a}
        table {
          width: 100%;
          border-collapse: collapse;
          margin-bottom: 30px;
        }
        td,th {
          padding: 10px;
          text-align: left;
          border-bottom: 1px solid #ffffff25;
        }
      </style>
    </head>
    <body>
      <h1>TAKE AWAY · Analytics</h1>
      <div class="grid">
    """

    page += cards
    page += "</div>"
    page += body
    page += "</body></html>"

    return Response(
        page,
        mimetype="text/html"
    )


# ============================================================
# CUSTOMER REWARDS API
# ============================================================

@app.route(
    "/api/rewards",
    methods=["POST"]
)
def api_rewards():
    _, user = request_user()

    if not user:
        return jsonify(
            error="Invalid Telegram session"
        ), 401

    return jsonify(
        get_rewards(
            safe_int(user["id"])
        )
    )


# ============================================================
# CUSTOMER ORDERS API
# ============================================================

@app.route(
    "/api/orders",
    methods=["POST"]
)
def api_orders():
    _, user = request_user()

    if not user:
        return jsonify(
            error="Invalid Telegram session"
        ), 401

    db = get_db()

    try:
        rows = db.execute("""
            SELECT *
            FROM orders
            WHERE telegram_id = ?
            ORDER BY id DESC
            LIMIT 50
        """, (
            safe_int(user["id"]),
        ))

        return jsonify(
            orders=[
                serialize_order(row)
                for row in rows
            ]
        )

    finally:
        db.close()


# ============================================================
# CUSTOMER PROFILE API
# ============================================================

@app.route(
    "/api/profile",
    methods=["POST"]
)
def api_profile():
    _, user = request_user()

    if not user:
        return jsonify(
            error="Invalid Telegram session"
        ), 401

    db = get_db()

    try:
        rows = db.execute("""
            SELECT *
            FROM orders
            WHERE telegram_id = ?
            ORDER BY id ASC
        """, (
            safe_int(user["id"]),
        )).fetchall()

        completed = [
            row
            for row in rows
            if row["status"] == "completed"
        ]

        favorites = {}

        for row in completed:
            for item in details(row):
                pid = LEGACY_ITEM_MAP.get(
                    item.get("id"),
                    (
                        item.get("id"),
                        None
                    )
                )[0]

                if (
                    pid in MENU_BY_ID
                    or pid in RETIRED
                ):
                    favorites[pid] = (
                        favorites.get(pid, 0)
                        + safe_int(
                            item.get("qty")
                        )
                    )

        favorite_id = (
            max(
                favorites,
                key=favorites.get
            )
            if favorites
            else None
        )

        first = (
            user.get("first_name")
            or ""
        )

        last = (
            user.get("last_name")
            or ""
        )

        drinks = sum(
            sum(
                safe_int(
                    item.get("qty")
                )
                for item in details(row)
                if not item.get("reward_free")
            )
            for row in completed
        )

        first_order = (
            pretty_created(
                rows[0]["created_at"]
            ).split(" ")[0]
            if rows
            else None
        )

        return jsonify(
            telegram_id=user["id"],
            first_name=first,
            last_name=last,
            name=(
                first + " " + last
            ).strip() or "TAKE AWAY Member",
            username=user.get("username"),
            orders=len(rows),
            drinks=drinks,
            favorite=(
                {
                    "id": favorite_id,
                    "quantity": favorites[
                        favorite_id
                    ]
                }
                if favorite_id
                else None
            ),
            first_order=first_order
        )

    finally:
        db.close()


# ============================================================
# CREATE CUSTOMER ORDER
# ============================================================

@app.route(
    "/api/order",
    methods=["POST"]
)
def create_order():
    data, user = request_user()

    if not user:
        return jsonify(
            success=False,
            error=(
                "Please open TAKE AWAY "
                "inside Telegram."
            )
        ), 401

    if str(
        data.get(
            "payment_method",
            "pickup"
        )
    ).lower() != "pickup":
        return jsonify(
            success=False,
            error="Choose Pay at pickup."
        ), 400

    try:
        items, total, reward_index = (
            calculate_order_items(
                data.get("items") or [],
                data.get(
                    "reward_item_index"
                )
            )
        )

    except ValueError as error:
        return jsonify(
            success=False,
            error=str(error)
        ), 400

    pickup, error = validate_pickup(
        data.get("pickup")
    )

    if error:
        return jsonify(
            success=False,
            error=error
        ), 400

    uid = safe_int(
        user["id"]
    )

    db = get_db()

    try:
        db.execute(
            "BEGIN IMMEDIATE"
        )

        if reward_index is not None:
            if not consume_reward(
                db,
                uid
            ):
                db.rollback()

                return jsonify(
                    success=False,
                    error=(
                        "Your free drink is no longer "
                        "available. Please refresh Rewards."
                    )
                ), 409

        now = now_iso()

        cursor = db.execute("""
            INSERT INTO orders
            (
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
                'new',
                ?, ?,
                'pickup',
                'pending',
                ?, ?,
                0
            )
        """, (
            uid,
            user.get("username"),
            user.get("first_name"),
            user.get("last_name"),
            json.dumps(
                items,
                ensure_ascii=False
            ),
            total,
            pickup.isoformat(),
            now,
            now,
            (
                1
                if reward_index is not None
                else 0
            ),
            reward_index
        ))

        order_id = cursor.lastrowid

        db.commit()

    except Exception:
        db.rollback()

        logging.exception(
            "Create order failed"
        )

        return jsonify(
            success=False,
            error=(
                "Could not create order. "
                "Please try again."
            )
        ), 500

    finally:
        db.close()

    if ADMIN:
        send_message(
            ADMIN,
            barista_text(
                order_id,
                user,
                items,
                total,
                pickup.isoformat()
            ),
            admin_keyboard(
                order_id,
                "new"
            )
        )

    else:
        logging.error(
            "ORDER_CHAT_ID missing "
            "for order #%s",
            order_id
        )

    send_message(
        uid,
        (
            f"☕ <b>TAKE AWAY order #{order_id}</b>\n\n"
            f"Pickup: <b>"
            f"{pretty_pickup(pickup.isoformat())}"
            f"</b>\n"
            f"Total: <b>{total} RSD</b>\n\n"
            "Your order has been received. "
            "Pay at pickup by card or cash."
        )
    )

    return jsonify(
        success=True,
        order_id=order_id,
        total=total,
        pickup=pickup.isoformat(),
        payment_method="pickup",
        payment_status="pending",
        reward_used=(
            reward_index is not None
        )
    )


# ============================================================
# TELEGRAM BOT
# ============================================================

def bot_start_text():
    return (
        "☕ <b>WELCOME TO TAKE AWAY</b>\n"
        "<i>Thai Tea & Coffee · Belgrade</i>\n\n"
        "🫖 Authentic Thai Tea\n"
        "☕ Specialty Coffee\n"
        "✦ Signature Drinks\n\n"
        "📍 Savski trg 1a\n\n"
        "<b>Order ahead & skip the wait ↓</b>\n\n"
        "🇷🇸 <b>DOBRO DOŠLI U TAKE AWAY</b>\n"
        "Poruči unapred, bez čekanja ↓"
    )


def web_app_keyboard():
    return {
        "inline_keyboard": [
            [
                {
                    "text": "☕ OPEN TAKE AWAY",
                    "web_app": {
                        "url": URL
                    }
                }
            ]
        ]
    }


def handle_bot_message(message):
    chat_id = (
        message.get("chat")
        or {}
    ).get("id")

    raw = str(
        message.get("text") or ""
    ).strip()

    cmd = (
        raw.split(None, 1)[0]
        .split("@", 1)[0]
        .lower()
        if raw.startswith("/")
        else ""
    )

    if not chat_id:
        return

    if cmd in (
        "/start",
        "/menu"
    ):
        send_message(
            chat_id,
            bot_start_text(),
            web_app_keyboard()
        )

    elif cmd == "/help":
        send_message(
            chat_id,
            (
                "☕ <b>TAKE AWAY Help</b>\n"
                "Open the ordering app, "
                "choose drinks and pickup time. "
                "Pay at the café."
            ),
            web_app_keyboard()
        )


# ============================================================
# TELEGRAM WEBHOOK
# ============================================================

@app.route(
    "/webhook",
    methods=["POST"]
)
def webhook():
    if WEBHOOK_SECRET:
        provided_secret = request.headers.get(
            "X-Telegram-Bot-Api-Secret-Token",
            ""
        )

        if not secrets.compare_digest(
            provided_secret,
            WEBHOOK_SECRET
        ):
            return jsonify(
                ok=False,
                error="Invalid webhook secret"
            ), 403

    update = request.get_json(
        silent=True
    ) or {}

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
        logging.exception(
            "Webhook processing failed"
        )

    return jsonify(
        ok=True
    )


# ============================================================
# WEBHOOK STATUS
# ============================================================

@app.route("/webhook-status")
def webhook_status():
    if not admin_authorized():
        return admin_login_required()

    return jsonify(
        success=True,
        expected_webhook=(
            f"{URL}/webhook"
        ),
        bot=telegram_api(
            "getMe"
        ),
        webhook=telegram_api(
            "getWebhookInfo"
        ),
        admin_chat_id_configured=bool(
            ADMIN
        ),
        webhook_secret_configured=bool(
            WEBHOOK_SECRET
        )
    )


# ============================================================
# SETUP WEBHOOK
# ============================================================

@app.route("/setup-webhook")
def setup_webhook():
    if not admin_authorized():
        return admin_login_required()

    if not TOKEN:
        return jsonify(
            success=False,
            error="BOT_TOKEN missing"
        ), 500

    payload = {
        "url": f"{URL}/webhook",
        "allowed_updates": [
            "message",
            "callback_query"
        ],
        "drop_pending_updates": False,
        "max_connections": 40
    }

    if WEBHOOK_SECRET:
        payload["secret_token"] = (
            WEBHOOK_SECRET
        )

    result = telegram_api(
        "setWebhook",
        payload
    )

    return jsonify(
        success=bool(
            result.get("ok")
        ),
        set_webhook=result,
        webhook_info=telegram_api(
            "getWebhookInfo"
        )
    )


# ============================================================
# TELEGRAM TEST
# ============================================================

@app.route("/telegram-test")
def telegram_test():
    if not admin_authorized():
        return admin_login_required()

    info = telegram_api(
        "getMe"
    )

    test = (
        send_message(
            ADMIN,
            (
                "🧪 <b>TAKE AWAY BOT TEST</b>\n"
                "Telegram connection is working."
            )
        )
        if ADMIN
        else None
    )

    return jsonify(
        success=bool(
            info.get("ok")
        ),
        bot=info,
        webhook=telegram_api(
            "getWebhookInfo"
        ),
        admin_test=test
    )


# ============================================================
# START APPLICATION
# ============================================================

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=safe_int(
            os.getenv("PORT", "10000"),
            10000
        ),
        debug=False
    )
