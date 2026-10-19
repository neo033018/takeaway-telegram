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


# =========================================================
# APP / SETTINGS
# =========================================================

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

TOKEN = os.environ.get("BOT_TOKEN", "")

URL = os.environ.get(
    "APP_URL",
    "https://takeaway-telegram.onrender.com"
).rstrip("/")

ADMIN = os.environ.get("ORDER_CHAT_ID", "")

DB = os.environ.get(
    "DATABASE_PATH",
    "orders.db"
)

WEBHOOK_SECRET = os.environ.get(
    "WEBHOOK_SECRET",
    ""
)

ADMIN_PASSWORD = os.environ.get(
    "ADMIN_PASSWORD",
    ""
)

TZ = ZoneInfo("Europe/Belgrade")

OPEN_HOUR = 7
CLOSE_HOUR = 17
MIN_PREP_MINUTES = 10

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
        "id": "lime",
        "name": "Thai Iced Lime Tea",
        "price": 420,
        "category": "tea",
        "boba": False,
    },

    {
        "id": "espresso",
        "name": "Espresso",
        "price": 200,
        "category": "coffee",
        "boba": False,
    },
    {
        "id": "americano",
        "name": "Americano",
        "price": 250,
        "category": "coffee",
        "boba": False,
        "temperature": True,
        "iced_surcharge": 20,
    },
    {
        "id": "cappuccino",
        "name": "Cappuccino",
        "price": 320,
        "category": "coffee",
        "boba": False,
    },
    {
        "id": "latte",
        "name": "Latte",
        "price": 350,
        "category": "coffee",
        "boba": False,
        "temperature": True,
        "iced_surcharge": 20,
    },
    {
        "id": "flatwhite",
        "name": "Flat White",
        "price": 450,
        "category": "coffee",
        "boba": False,
    },
    {
        "id": "americanocream",
        "name": "Iced Americano with Cream",
        "price": 450,
        "category": "coffee",
        "boba": False,
    },

    {
        "id": "tonic",
        "name": "Tonic Coffee",
        "price": 420,
        "category": "special",
        "boba": False,
    },
    {
        "id": "bumble",
        "name": "Bumble Coffee",
        "price": 450,
        "category": "special",
        "boba": False,
    },
    {
        "id": "strawberrymatcha",
        "name": "Strawberry Matcha Latte",
        "price": 480,
        "category": "special",
        "boba": False,
    },
    {
        "id": "peanutlatte",
        "name": "Peanut Latte",
        "price": 550,
        "category": "special",
        "boba": False,
    },
]

MENU_BY_ID = {item["id"]: item for item in MENU}

COFFEE_IDS = {
    "espresso",
    "americano",
    "cappuccino",
    "latte",
    "flatwhite",
    "americanocream",
    "tonic",
    "bumble",
    "peanutlatte",
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
    "peanutlatte",
}

SYRUPS = {
    "vanilla": "Vanilla",
    "salted_caramel": "Salted Caramel",
    "strawberry": "Strawberry",
}

TEMPERATURE_IDS = {
    "americano",
    "latte",
    "thai",
    "green",
}

LEGACY_ITEM_MAP = {
    "icedamericano": ("americano", "iced"),
    "icedlatte": ("latte", "iced"),
}


# =========================================================
# DATABASE
# =========================================================

def get_db():
    connection = sqlite3.connect(
        DB,
        timeout=20,
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    return connection


def table_columns(db, table):
    try:
        rows = db.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()
        return {row["name"] for row in rows}
    except Exception:
        return set()


def ensure_column(db, table, name, definition):
    columns = table_columns(db, table)

    if name not in columns:
        db.execute(
            f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
        )


def init_db():
    db = get_db()

    try:
        db.execute(
            """
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
            """
        )

        ensure_column(
            db,
            "orders",
            "payment_method",
            "TEXT DEFAULT 'pickup'"
        )

        ensure_column(
            db,
            "orders",
            "payment_status",
            "TEXT DEFAULT 'pending'"
        )

        ensure_column(
            db,
            "orders",
            "reward_used",
            "INTEGER DEFAULT 0"
        )

        ensure_column(
            db,
            "orders",
            "reward_item_index",
            "INTEGER"
        )

        ensure_column(
            db,
            "orders",
            "reward_processed",
            "INTEGER DEFAULT 0"
        )

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS loyalty_accounts (
                telegram_id INTEGER PRIMARY KEY,
                stamps INTEGER NOT NULL DEFAULT 0,
                free_drinks INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
            """
        )

        db.execute(
            """
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
            """
        )

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS analytics_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER,
                event_type TEXT NOT NULL,
                source TEXT,
                created_at TEXT NOT NULL
            )
            """
        )

        db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_orders_telegram
            ON orders(telegram_id)
            """
        )

        db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_orders_created
            ON orders(created_at)
            """
        )

        db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_orders_status
            ON orders(status)
            """
        )

        db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_analytics_events_created
            ON analytics_events(created_at)
            """
        )

        db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_analytics_events_type
            ON analytics_events(event_type)
            """
        )

        db.commit()

    finally:
        db.close()


init_db()


# =========================================================
# GENERAL HELPERS
# =========================================================

def now_local():
    return datetime.now(TZ)


def now_iso():
    return now_local().isoformat()


def parse_datetime(value):
    if not value:
        return None

    try:
        dt = datetime.fromisoformat(
            str(value).replace("Z", "+00:00")
        )

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)

        return dt.astimezone(TZ)

    except Exception:
        return None


def safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def get_order(order_id):
    db = get_db()

    try:
        return db.execute(
            "SELECT * FROM orders WHERE id = ?",
            (order_id,)
        ).fetchone()
    finally:
        db.close()


def details(row):
    try:
        return json.loads(row["items"] or "[]")
    except Exception:
        return []


def qty_in_order(row):
    return sum(
        safe_int(item.get("qty"), 0)
        for item in details(row)
    )


# =========================================================
# TELEGRAM INIT DATA
# =========================================================

def verify_init_data(init_data):
    if not TOKEN or not init_data:
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

        auth_date = safe_int(
            parsed.get("auth_date"),
            0
        )

        if not auth_date:
            return None

        # Allow 24 hours. Telegram Mini App can remain open for a while.
        if abs(int(time.time()) - auth_date) > 86400:
            return None

        data_check_string = "\n".join(
            f"{key}={parsed[key]}"
            for key in sorted(parsed.keys())
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
            return None

        user_raw = parsed.get("user")

        if not user_raw:
            return None

        user = json.loads(user_raw)

        if not user.get("id"):
            return None

        return user

    except Exception:
        logging.exception(
            "Telegram initData verification failed"
        )
        return None


def request_user():
    data = request.get_json(silent=True) or {}

    init_data = data.get("initData", "")

    user = verify_init_data(init_data)

    return data, user


# =========================================================
# ANALYTICS
# =========================================================

def clean_source(value):
    value = str(value or "").strip()

    if not value:
        return "direct"

    return value[:100]


def record_app_open(user, source=""):
    if not user:
        return

    telegram_id = safe_int(user.get("id"))
    source = clean_source(source)

    db = get_db()

    try:
        existing = db.execute(
            """
            SELECT telegram_id
            FROM analytics_visitors
            WHERE telegram_id = ?
            """,
            (telegram_id,)
        ).fetchone()

        current = now_iso()

        if existing:
            db.execute(
                """
                UPDATE analytics_visitors
                SET username = ?,
                    first_name = ?,
                    last_name = ?,
                    last_seen = ?,
                    opens = opens + 1,
                    source = CASE
                        WHEN ? != 'direct' THEN ?
                        ELSE source
                    END
                WHERE telegram_id = ?
                """,
                (
                    user.get("username"),
                    user.get("first_name"),
                    user.get("last_name"),
                    current,
                    source,
                    source,
                    telegram_id,
                )
            )
        else:
            db.execute(
                """
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
                """,
                (
                    telegram_id,
                    user.get("username"),
                    user.get("first_name"),
                    user.get("last_name"),
                    current,
                    current,
                    source,
                )
            )

        db.execute(
            """
            INSERT INTO analytics_events (
                telegram_id,
                event_type,
                source,
                created_at
            )
            VALUES (?, 'open', ?, ?)
            """,
            (
                telegram_id,
                source,
                current,
            )
        )

        db.commit()

    finally:
        db.close()


def record_directions_click(user, source=""):
    if not user:
        return

    db = get_db()

    try:
        db.execute(
            """
            INSERT INTO analytics_events (
                telegram_id,
                event_type,
                source,
                created_at
            )
            VALUES (?, 'directions', ?, ?)
            """,
            (
                safe_int(user.get("id")),
                clean_source(source),
                now_iso(),
            )
        )

        db.commit()

    finally:
        db.close()


def analytics_data():
    db = get_db()

    try:
        today = now_local().date().isoformat()

        visitors = db.execute(
            """
            SELECT COUNT(*) AS c
            FROM analytics_visitors
            """
        ).fetchone()["c"]

        opens = db.execute(
            """
            SELECT COALESCE(SUM(opens), 0) AS c
            FROM analytics_visitors
            """
        ).fetchone()["c"]

        returning = db.execute(
            """
            SELECT COUNT(*) AS c
            FROM analytics_visitors
            WHERE opens > 1
            """
        ).fetchone()["c"]

        unique_customers = db.execute(
            """
            SELECT COUNT(DISTINCT telegram_id) AS c
            FROM orders
            """
        ).fetchone()["c"]

        orders = db.execute(
            """
            SELECT COUNT(*) AS c
            FROM orders
            """
        ).fetchone()["c"]

        completed = db.execute(
            """
            SELECT COUNT(*) AS c
            FROM orders
            WHERE status = 'completed'
            """
        ).fetchone()["c"]

        directions = db.execute(
            """
            SELECT COUNT(*) AS c
            FROM analytics_events
            WHERE event_type = 'directions'
            """
        ).fetchone()["c"]

        completed_revenue = db.execute(
            """
            SELECT COALESCE(SUM(total), 0) AS c
            FROM orders
            WHERE status = 'completed'
            """
        ).fetchone()["c"]

        today_orders = db.execute(
            """
            SELECT COUNT(*) AS c
            FROM orders
            WHERE substr(created_at, 1, 10) = ?
            """,
            (today,)
        ).fetchone()["c"]

        today_revenue = db.execute(
            """
            SELECT COALESCE(SUM(total), 0) AS c
            FROM orders
            WHERE substr(created_at, 1, 10) = ?
            AND status = 'completed'
            """,
            (today,)
        ).fetchone()["c"]

        today_visitors = db.execute(
            """
            SELECT COUNT(*) AS c
            FROM analytics_visitors
            WHERE substr(last_seen, 1, 10) = ?
            """,
            (today,)
        ).fetchone()["c"]

        source_rows = db.execute(
            """
            SELECT
                COALESCE(NULLIF(source, ''), 'direct') AS source,
                COUNT(*) AS c
            FROM analytics_visitors
            GROUP BY COALESCE(NULLIF(source, ''), 'direct')
            ORDER BY c DESC
            LIMIT 20
            """
        ).fetchall()

        recent_rows = db.execute(
            """
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
            """
        ).fetchall()

        completed_rows = db.execute(
            """
            SELECT items
            FROM orders
            WHERE status = 'completed'
            """
        ).fetchall()

        popular = {}

        for row in completed_rows:
            try:
                items = json.loads(row["items"] or "[]")
            except Exception:
                items = []

            for item in items:
                item_id = str(item.get("id") or "")

                if item_id == "icedlatte":
                    item_id = "latte"

                if item_id == "icedamericano":
                    item_id = "americano"

                product = MENU_BY_ID.get(item_id)

                if not product:
                    continue

                popular[product["name"]] = (
                    popular.get(product["name"], 0)
                    + safe_int(item.get("qty"), 0)
                )

        popular_drinks = sorted(
            (
                {
                    "name": name,
                    "quantity": quantity,
                }
                for name, quantity in popular.items()
            ),
            key=lambda x: x["quantity"],
            reverse=True
        )[:10]

        conversion = (
            round(
                unique_customers / visitors * 100,
                1
            )
            if visitors
            else 0
        )

        return {
            "visitors": visitors,
            "opens": opens,
            "returning_visitors": returning,
            "unique_customers": unique_customers,
            "conversion": conversion,
            "orders": orders,
            "completed": completed,
            "directions": directions,
            "revenue": completed_revenue,
            "today": {
                "visitors": today_visitors,
                "orders": today_orders,
                "revenue": today_revenue,
            },
            "sources": [
                {
                    "source": row["source"],
                    "count": row["c"],
                }
                for row in source_rows
            ],
            "popular_drinks": popular_drinks,
            "recent_visitors": [
                dict(row)
                for row in recent_rows
            ],
        }

    finally:
        db.close()


# =========================================================
# ADMIN AUTH
# =========================================================

def admin_authorized():
    if not ADMIN_PASSWORD:
        return False

    auth = request.authorization

    if not auth:
        return False

    supplied_password = auth.password or ""

    return secrets.compare_digest(
        supplied_password,
        ADMIN_PASSWORD
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


# =========================================================
# LOYALTY / REWARDS
# =========================================================

def ensure_loyalty_account(
    db,
    telegram_id
):
    row = db.execute(
        """
        SELECT *
        FROM loyalty_accounts
        WHERE telegram_id = ?
        """,
        (telegram_id,)
    ).fetchone()

    if row:
        return row

    db.execute(
        """
        INSERT INTO loyalty_accounts (
            telegram_id,
            stamps,
            free_drinks,
            updated_at
        )
        VALUES (?, 0, 0, ?)
        """,
        (
            telegram_id,
            now_iso(),
        )
    )

    return db.execute(
        """
        SELECT *
        FROM loyalty_accounts
        WHERE telegram_id = ?
        """,
        (telegram_id,)
    ).fetchone()


def get_rewards(telegram_id):
    db = get_db()

    try:
        row = ensure_loyalty_account(
            db,
            telegram_id
        )

        db.commit()

        return {
            "stamps": safe_int(row["stamps"]),
            "free_drinks": safe_int(
                row["free_drinks"]
            ),
        }

    finally:
        db.close()


def consume_reward(
    db,
    telegram_id
):
    row = ensure_loyalty_account(
        db,
        telegram_id
    )

    free_drinks = safe_int(
        row["free_drinks"]
    )

    if free_drinks < 1:
        return False

    db.execute(
        """
        UPDATE loyalty_accounts
        SET free_drinks = free_drinks - 1,
            updated_at = ?
        WHERE telegram_id = ?
        """,
        (
            now_iso(),
            telegram_id,
        )
    )

    return True


def restore_reward(
    db,
    telegram_id
):
    ensure_loyalty_account(
        db,
        telegram_id
    )

    db.execute(
        """
        UPDATE loyalty_accounts
        SET free_drinks = free_drinks + 1,
            updated_at = ?
        WHERE telegram_id = ?
        """,
        (
            now_iso(),
            telegram_id,
        )
    )


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

    stamps = safe_int(row["stamps"])
    free_drinks = safe_int(
        row["free_drinks"]
    )

    total = stamps + quantity

    new_free = total // 9
    new_stamps = total % 9

    db.execute(
        """
        UPDATE loyalty_accounts
        SET stamps = ?,
            free_drinks = ?,
            updated_at = ?
        WHERE telegram_id = ?
        """,
        (
            new_stamps,
            free_drinks + new_free,
            now_iso(),
            telegram_id,
        )
    )


def mark_paid_and_completed(
    order_id
):
    db = get_db()

    try:
        order = db.execute(
            """
            SELECT *
            FROM orders
            WHERE id = ?
            """,
            (order_id,)
        ).fetchone()

        if not order:
            return False

        if order["status"] == "cancelled":
            return False

        telegram_id = safe_int(
            order["telegram_id"]
        )

        already_processed = safe_int(
            order["reward_processed"]
        ) == 1

        db.execute(
            """
            UPDATE orders
            SET status = 'completed',
                payment_status = 'paid',
                updated_at = ?
            WHERE id = ?
            """,
            (
                now_iso(),
                order_id,
            )
        )

        if not already_processed:
            paid_quantity = 0

            for item in details(order):
                qty = safe_int(
                    item.get("qty"),
                    0
                )

                if item.get("reward_free"):
                    continue

                paid_quantity += qty

            add_loyalty_drinks(
                db,
                telegram_id,
                paid_quantity
            )

            db.execute(
                """
                UPDATE orders
                SET reward_processed = 1
                WHERE id = ?
                """,
                (order_id,)
            )

        db.commit()
        return True

    finally:
        db.close()


# =========================================================
# ORDER ITEM NORMALIZATION / PRICING
# =========================================================

def normalize_item_id(
    raw_id,
    temperature=None
):
    raw_id = str(raw_id or "").strip()

    if raw_id in LEGACY_ITEM_MAP:
        new_id, forced_temperature = (
            LEGACY_ITEM_MAP[raw_id]
        )

        return (
            new_id,
            forced_temperature
        )

    return (
        raw_id,
        temperature
    )


def normalize_temperature(
    item_id,
    value
):
    if item_id not in TEMPERATURE_IDS:
        return None

    value = str(
        value or "hot"
    ).lower().strip()

    if value not in {
        "hot",
        "iced",
    }:
        value = "hot"

    return value


def normalize_syrup(
    item_id,
    value
):
    if item_id not in SYRUP_ALLOWED_IDS:
        return None

    value = str(
        value or ""
    ).lower().strip()

    if value in SYRUPS:
        return value

    return None


def calculate_item(
    raw,
    reward_free=False
):
    raw_id = raw.get("id")

    raw_temperature = raw.get(
        "temperature"
    )

    item_id, forced_temperature = (
        normalize_item_id(
            raw_id,
            raw_temperature
        )
    )

    product = MENU_BY_ID.get(item_id)

    if not product:
        raise ValueError(
            f"Unknown drink: {raw_id}"
        )

    qty = safe_int(
        raw.get("qty"),
        1
    )

    if qty < 1:
        qty = 1

    if qty > MAX_ITEM_QUANTITY:
        qty = MAX_ITEM_QUANTITY

    temperature = normalize_temperature(
        item_id,
        forced_temperature
        or raw_temperature
    )

    extra_shot = bool(
        raw.get("extra_shot")
    )

    boba = bool(
        raw.get("boba")
    ) and bool(
        product.get("boba")
    )

    syrup = normalize_syrup(
        item_id,
        raw.get("syrup")
    )

    iced_surcharge = 0

    if (
        temperature == "iced"
        and item_id in {
            "americano",
            "latte",
        }
    ):
        iced_surcharge = 20

    extra_price = (
        150
        if extra_shot
        else 0
    )

    boba_price = (
        100
        if boba
        else 0
    )

    base_price = safe_int(
        product["price"]
    )

    unit_price = (
        base_price
        + iced_surcharge
        + extra_price
        + boba_price
    )

    if reward_free:
        # Reward covers the normal/base drink.
        # Paid extras remain paid.
        unit_total = (
            iced_surcharge
            + extra_price
            + boba_price
        )

        qty = 1
    else:
        unit_total = unit_price

    line_total = (
        unit_total * qty
    )

    item = {
        "id": item_id,
        "name": product["name"],
        "qty": qty,
        "base_price": base_price,
        "unit_price": unit_price,
        "line_total": line_total,
        "extra_shot": extra_shot,
        "boba": boba,
        "syrup": syrup,
        "temperature": temperature,
        "reward_free": bool(
            reward_free
        ),
    }

    return item


def calculate_order_items(
    raw_items,
    reward_item_index=None
):
    if not isinstance(
        raw_items,
        list
    ):
        raise ValueError(
            "Invalid items"
        )

    if not raw_items:
        raise ValueError(
            "Order is empty"
        )

    total_quantity = 0
    normalized = []

    reward_index = None

    if reward_item_index is not None:
        try:
            reward_index = int(
                reward_item_index
            )
        except Exception:
            reward_index = None

    for index, raw in enumerate(
        raw_items
    ):
        if not isinstance(
            raw,
            dict
        ):
            raise ValueError(
                "Invalid item"
            )

        reward_free = (
            reward_index is not None
            and index == reward_index
        )

        item = calculate_item(
            raw,
            reward_free=reward_free
        )

        total_quantity += safe_int(
            item["qty"]
        )

        if (
            total_quantity
            > MAX_ORDER_QUANTITY
        ):
            raise ValueError(
                "Maximum 30 drinks per order."
            )

        normalized.append(item)

    total = sum(
        safe_int(item["line_total"])
        for item in normalized
    )

    return (
        normalized,
        total,
        reward_index,
    )


# =========================================================
# PICKUP VALIDATION
# =========================================================

def validate_pickup(
    value
):
    pickup = parse_datetime(value)

    if not pickup:
        return (
            None,
            "Invalid pickup time."
        )

    current = now_local()

    if (
        pickup.date()
        != current.date()
    ):
        return (
            None,
            "Pickup is available today only."
        )

    earliest = (
        current
        + timedelta(
            minutes=MIN_PREP_MINUTES
        )
    )

    # Small tolerance for network / UI delay.
    if pickup < (
        earliest
        - timedelta(
            minutes=2
        )
    ):
        return (
            None,
            "Please allow at least 10 minutes for preparation."
        )

    opening = pickup.replace(
        hour=OPEN_HOUR,
        minute=0,
        second=0,
        microsecond=0,
    )

    closing = pickup.replace(
        hour=CLOSE_HOUR,
        minute=0,
        second=0,
        microsecond=0,
    )

    if not (
        opening
        <= pickup
        < closing
    ):
        return (
            None,
            "Pickup is available between 07:00 and 17:00."
        )

    return (
        pickup,
        None
    )


# =========================================================
# ORDER SERIALIZATION
# =========================================================

def pretty_pickup(value):
    dt = parse_datetime(value)

    if not dt:
        return str(value or "")

    return dt.strftime("%H:%M")


def pretty_created(value):
    dt = parse_datetime(value)

    if not dt:
        return str(value or "")

    return dt.strftime(
        "%d.%m.%Y %H:%M"
    )


def serialize_order(row):
    items = details(row)

    return {
        "id": row["id"],
        "items": items,
        "total": safe_int(
            row["total"]
        ),
        "pickup": pretty_pickup(
            row["pickup"]
        ),
        "pickup_iso": row["pickup"],
        "status": row["status"],
        "payment_method":
            row["payment_method"],
        "payment_status":
            row["payment_status"],
        "reward_used": bool(
            row["reward_used"]
        ),
        "created": pretty_created(
            row["created_at"]
        ),
        "created_at":
            row["created_at"],
    }


# =========================================================
# BARISTA MESSAGE
# =========================================================

def item_option_lines(item):
    lines = []

    temperature = item.get(
        "temperature"
    )

    if temperature == "hot":
        lines.append("☕ HOT")

    elif temperature == "iced":
        lines.append("🧊 ICED")

        if item.get("id") in {
            "americano",
            "latte",
        }:
            lines.append(
                "   +20 RSD iced"
            )

    if item.get("extra_shot"):
        lines.append(
            "➕ Extra shot +150 RSD"
        )

    if item.get("boba"):
        lines.append(
            "🧋 Boba +100 RSD"
        )

    syrup = item.get("syrup")

    if syrup in SYRUPS:
        lines.append(
            f"🍯 Syrup: {SYRUPS[syrup]} · FREE"
        )

    if item.get("reward_free"):
        lines.append(
            "🎁 FREE DRINK REWARD"
        )

    return lines


def barista_text(
    order_id,
    user,
    items,
    total,
    pickup,
    payment_method="pickup"
):
    first_name = (
        user.get("first_name")
        or "Customer"
    )

    last_name = (
        user.get("last_name")
        or ""
    )

    customer = (
        f"{first_name} {last_name}"
    ).strip()

    username = user.get(
        "username"
    )

    lines = [
        "☕ <b>NEW TAKE AWAY ORDER</b>",
        "",
        f"🧾 <b>Order #{order_id}</b>",
        f"👤 {html.escape(customer)}",
    ]

    if username:
        lines.append(
            f"📱 @{html.escape(username)}"
        )

    lines.extend([
        f"🕒 Pickup: <b>{html.escape(pretty_pickup(pickup))}</b>",
        "💳 Payment: <b>PAY AT PICKUP</b>",
        "",
        "<b>DRINKS</b>",
    ])

    for item in items:
        qty = safe_int(
            item.get("qty"),
            1
        )

        name = html.escape(
            str(
                item.get("name")
                or item.get("id")
                or "Drink"
            )
        )

        line_total = safe_int(
            item.get("line_total")
        )

        if item.get("reward_free"):
            lines.append(
                f"🎁 <b>{qty} × {name}</b> — {line_total} RSD"
            )
        else:
            lines.append(
                f"• <b>{qty} × {name}</b> — {line_total} RSD"
            )

        for option in item_option_lines(
            item
        ):
            lines.append(
                html.escape(option)
            )

    lines.extend([
        "",
        f"💰 <b>TOTAL: {total} RSD</b>",
        "",
        "Payment at café by card or cash.",
    ])

    return "\n".join(lines)


# =========================================================
# TELEGRAM HELPERS
# =========================================================

def telegram_api(
    method,
    payload=None
):
    if not TOKEN:
        return None

    endpoint = (
        f"https://api.telegram.org/"
        f"bot{TOKEN}/{method}"
    )

    try:
        response = requests.post(
            endpoint,
            json=payload or {},
            timeout=15,
        )

        if not response.ok:
            logging.error(
                "Telegram API %s failed: %s",
                method,
                response.text,
            )
            return None

        return response.json()

    except Exception:
        logging.exception(
            "Telegram API request failed"
        )
        return None


def send_message(
    chat_id,
    text,
    reply_markup=None
):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    if reply_markup:
        payload["reply_markup"] = (
            reply_markup
        )

    return telegram_api(
        "sendMessage",
        payload
    )


def edit_message_markup(
    chat_id,
    message_id,
    reply_markup
):
    return telegram_api(
        "editMessageReplyMarkup",
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup":
                reply_markup,
        }
    )


def admin_keyboard(
    order_id,
    status="new"
):
    if status == "new":
        buttons = [
            [
                {
                    "text": "✅ ACCEPT",
                    "callback_data":
                        f"accept:{order_id}",
                }
            ],
            [
                {
                    "text": "❌ CANCEL",
                    "callback_data":
                        f"cancel:{order_id}",
                }
            ],
        ]

    elif status == "preparing":
        buttons = [
            [
                {
                    "text": "☕ READY",
                    "callback_data":
                        f"ready:{order_id}",
                }
            ],
            [
                {
                    "text": "❌ CANCEL",
                    "callback_data":
                        f"cancel:{order_id}",
                }
            ],
        ]

    elif status == "ready":
        buttons = [
            [
                {
                    "text": "💳 PAID / PICKED UP",
                    "callback_data":
                        f"complete:{order_id}",
                }
            ]
        ]

    else:
        buttons = []

    return {
        "inline_keyboard": buttons
    }


def customer_status_message(
    order_id,
    status
):
    if status == "preparing":
        return (
            f"☕ <b>Order #{order_id}</b>\n\n"
            "Your order has been accepted and is being prepared."
        )

    if status == "ready":
        return (
            f"✅ <b>Order #{order_id} is ready!</b>\n\n"
            "You can come and collect your drinks.\n"
            "Pay at the café by card or cash."
        )

    if status == "completed":
        return (
            f"💚 <b>Order #{order_id} completed.</b>\n\n"
            "Thank you for choosing TAKE AWAY!"
        )

    if status == "cancelled":
        return (
            f"❌ <b>Order #{order_id} was cancelled.</b>\n\n"
            "Please contact TAKE AWAY if you need help."
        )

    return (
        f"Order #{order_id}: {status}"
    )


# =========================================================
# ORDER STATUS
# =========================================================

def set_order_status(
    order_id,
    new_status
):
    db = get_db()

    try:
        order = db.execute(
            """
            SELECT *
            FROM orders
            WHERE id = ?
            """,
            (order_id,)
        ).fetchone()

        if not order:
            return None

        old_status = order["status"]

        if old_status == "completed":
            return order

        if old_status == "cancelled":
            return order

        db.execute(
            """
            UPDATE orders
            SET status = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                new_status,
                now_iso(),
                order_id,
            )
        )

        db.commit()

        return db.execute(
            """
            SELECT *
            FROM orders
            WHERE id = ?
            """,
            (order_id,)
        ).fetchone()

    finally:
        db.close()


def cancel_order(
    order_id
):
    db = get_db()

    try:
        order = db.execute(
            """
            SELECT *
            FROM orders
            WHERE id = ?
            """,
            (order_id,)
        ).fetchone()

        if not order:
            return None

        if order["status"] in {
            "completed",
            "cancelled",
        }:
            return order

        if safe_int(
            order["reward_used"]
        ) == 1:
            restore_reward(
                db,
                safe_int(
                    order["telegram_id"]
                )
            )

            db.execute(
                """
                UPDATE orders
                SET reward_used = 0
                WHERE id = ?
                """,
                (order_id,)
            )

        db.execute(
            """
            UPDATE orders
            SET status = 'cancelled',
                updated_at = ?
            WHERE id = ?
            """,
            (
                now_iso(),
                order_id,
            )
        )

        db.commit()

        return db.execute(
            """
            SELECT *
            FROM orders
            WHERE id = ?
            """,
            (order_id,)
        ).fetchone()

    finally:
        db.close()


# =========================================================
# ROUTES
# =========================================================

@app.route("/")
def index():
    return render_template(
        "index.html"
    )


@app.route("/health")
def health():
    return jsonify({
        "ok": True,
        "service": "TAKE AWAY",
        "payment_mode": "pickup",
        "time": now_iso(),
    })


@app.route("/api/menu")
def api_menu():
    return jsonify(MENU)


# =========================================================
# ANALYTICS ROUTES
# =========================================================

@app.route(
    "/api/analytics/open",
    methods=["POST"]
)
def analytics_open():
    data, user = request_user()

    if not user:
        return jsonify({
            "success": False,
            "error": "Invalid Telegram session",
        }), 401

    record_app_open(
        user,
        data.get("source", "")
    )

    return jsonify({
        "success": True
    })


@app.route(
    "/api/analytics/directions",
    methods=["POST"]
)
def analytics_directions():
    data, user = request_user()

    if not user:
        return jsonify({
            "success": False,
            "error": "Invalid Telegram session",
        }), 401

    record_directions_click(
        user,
        data.get("source", "")
    )

    return jsonify({
        "success": True
    })


@app.route(
    "/api/admin/analytics"
)
def api_admin_analytics():
    if not admin_authorized():
        return admin_login_required()

    return jsonify(
        analytics_data()
    )


@app.route(
    "/admin/analytics"
)
def admin_analytics():
    if not admin_authorized():
        return admin_login_required()

    data = analytics_data()

    def rows(items):
        return "".join(
            f"""
            <tr>
                <td>{html.escape(str(item[0]))}</td>
                <td>{html.escape(str(item[1]))}</td>
            </tr>
            """
            for item in items
        )

    sources = rows([
        (
            source["source"],
            source["count"],
        )
        for source in data["sources"]
    ])

    popular = rows([
        (
            drink["name"],
            drink["quantity"],
        )
        for drink in data[
            "popular_drinks"
        ]
    ])

    recent = "".join(
        f"""
        <tr>
            <td>{html.escape(str(v.get("first_name") or ""))}</td>
            <td>{html.escape("@" + str(v.get("username")) if v.get("username") else "—")}</td>
            <td>{html.escape(str(v.get("opens") or 0))}</td>
            <td>{html.escape(str(v.get("source") or "direct"))}</td>
            <td>{html.escape(str(v.get("last_seen") or ""))}</td>
        </tr>
        """
        for v in data[
            "recent_visitors"
        ]
    )

    page = f"""
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>TAKE AWAY Analytics</title>

<style>
*{{box-sizing:border-box}}
body{{
    margin:0;
    padding:30px;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
    background:#032c1b;
    color:white;
}}
.container{{
    max-width:1200px;
    margin:auto;
}}
h1{{
    font-family:Georgia,serif;
    font-weight:500;
    letter-spacing:3px;
}}
.grid{{
    display:grid;
    grid-template-columns:repeat(auto-fit,minmax(170px,1fr));
    gap:12px;
    margin:25px 0;
}}
.card{{
    padding:20px;
    border:1px solid #ffffff25;
    border-radius:18px;
    background:#ffffff10;
}}
.value{{
    font-size:30px;
    font-weight:800;
}}
.label{{
    margin-top:6px;
    color:#ffffff99;
    font-size:12px;
}}
section{{
    margin-top:28px;
    padding:20px;
    border:1px solid #ffffff20;
    border-radius:18px;
    background:#ffffff0c;
}}
table{{
    width:100%;
    border-collapse:collapse;
}}
th,td{{
    padding:11px 8px;
    text-align:left;
    border-bottom:1px solid #ffffff15;
    font-size:13px;
}}
th{{
    color:#d6b36a;
}}
.gold{{
    color:#d6b36a;
}}
</style>
</head>

<body>
<div class="container">

<h1>TAKE AWAY · Analytics</h1>

<div class="grid">
    <div class="card">
        <div class="value">{data["visitors"]}</div>
        <div class="label">Unique visitors</div>
    </div>

    <div class="card">
        <div class="value">{data["opens"]}</div>
        <div class="label">App opens</div>
    </div>

    <div class="card">
        <div class="value">{data["returning_visitors"]}</div>
        <div class="label">Returning visitors</div>
    </div>

    <div class="card">
        <div class="value">{data["unique_customers"]}</div>
        <div class="label">Customers who ordered</div>
    </div>

    <div class="card">
        <div class="value">{data["conversion"]}%</div>
        <div class="label">Visitor → customer</div>
    </div>

    <div class="card">
        <div class="value">{data["orders"]}</div>
        <div class="label">Orders</div>
    </div>

    <div class="card">
        <div class="value">{data["completed"]}</div>
        <div class="label">Completed</div>
    </div>

    <div class="card">
        <div class="value">{data["directions"]}</div>
        <div class="label">Directions clicks</div>
    </div>

    <div class="card">
        <div class="value gold">{data["revenue"]:,} RSD</div>
        <div class="label">Completed revenue</div>
    </div>
</div>

<section>
<h2>Today</h2>

<div class="grid">
    <div class="card">
        <div class="value">{data["today"]["visitors"]}</div>
        <div class="label">Visitors today</div>
    </div>

    <div class="card">
        <div class="value">{data["today"]["orders"]}</div>
        <div class="label">Orders today</div>
    </div>

    <div class="card">
        <div class="value gold">{data["today"]["revenue"]:,} RSD</div>
        <div class="label">Revenue today</div>
    </div>
</div>
</section>

<section>
<h2>Traffic sources</h2>
<table>
<thead>
<tr>
<th>Source</th>
<th>Visitors</th>
</tr>
</thead>
<tbody>
{sources or '<tr><td colspan="2">No data yet</td></tr>'}
</tbody>
</table>
</section>

<section>
<h2>Popular drinks</h2>
<table>
<thead>
<tr>
<th>Drink</th>
<th>Quantity</th>
</tr>
</thead>
<tbody>
{popular or '<tr><td colspan="2">No completed orders yet</td></tr>'}
</tbody>
</table>
</section>

<section>
<h2>Recent visitors</h2>
<table>
<thead>
<tr>
<th>Name</th>
<th>Username</th>
<th>Opens</th>
<th>Source</th>
<th>Last seen</th>
</tr>
</thead>
<tbody>
{recent or '<tr><td colspan="5">No visitors yet</td></tr>'}
</tbody>
</table>
</section>

</div>
</body>
</html>
"""

    return Response(
        page,
        mimetype="text/html"
    )


# =========================================================
# REWARDS / ORDERS / PROFILE
# =========================================================

@app.route(
    "/api/rewards",
    methods=["POST"]
)
def api_rewards():
    data, user = request_user()

    if not user:
        return jsonify({
            "error": "Invalid Telegram session"
        }), 401

    return jsonify(
        get_rewards(
            safe_int(user["id"])
        )
    )


@app.route(
    "/api/orders",
    methods=["POST"]
)
def api_orders():
    data, user = request_user()

    if not user:
        return jsonify({
            "error": "Invalid Telegram session"
        }), 401

    telegram_id = safe_int(
        user["id"]
    )

    db = get_db()

    try:
        rows = db.execute(
            """
            SELECT *
            FROM orders
            WHERE telegram_id = ?
            ORDER BY id DESC
            LIMIT 50
            """,
            (telegram_id,)
        ).fetchall()

        return jsonify({
            "orders": [
                serialize_order(row)
                for row in rows
            ]
        })

    finally:
        db.close()


@app.route(
    "/api/profile",
    methods=["POST"]
)
def api_profile():
    data, user = request_user()

    if not user:
        return jsonify({
            "error": "Invalid Telegram session"
        }), 401

    telegram_id = safe_int(
        user["id"]
    )

    db = get_db()

    try:
        rows = db.execute(
            """
            SELECT *
            FROM orders
            WHERE telegram_id = ?
            ORDER BY id ASC
            """,
            (telegram_id,)
        ).fetchall()

        completed_rows = [
            row
            for row in rows
            if row["status"] == "completed"
        ]

        drink_count = sum(
            qty_in_order(row)
            for row in completed_rows
        )

        favorites = {}

        for row in completed_rows:
            for item in details(row):
                item_id = str(
                    item.get("id") or ""
                )

                if item_id == "icedlatte":
                    item_id = "latte"

                if item_id == "icedamericano":
                    item_id = "americano"

                if item_id not in MENU_BY_ID:
                    continue

                favorites[item_id] = (
                    favorites.get(
                        item_id,
                        0
                    )
                    + safe_int(
                        item.get("qty"),
                        0
                    )
                )

        favorite = None

        if favorites:
            favorite_id = max(
                favorites,
                key=favorites.get
            )

            favorite = {
                "id": favorite_id,
                "quantity":
                    favorites[favorite_id],
            }

        first_order = (
            pretty_created(
                rows[0]["created_at"]
            ).split(" ")[0]
            if rows
            else None
        )

        first_name = (
            user.get("first_name")
            or ""
        )

        last_name = (
            user.get("last_name")
            or ""
        )

        full_name = (
            f"{first_name} {last_name}"
        ).strip()

        return jsonify({
            "telegram_id":
                telegram_id,
            "first_name":
                first_name,
            "last_name":
                last_name,
            "name":
                full_name
                or "TAKE AWAY Member",
            "username":
                user.get("username"),
            "orders":
                len(rows),
            "drinks":
                drink_count,
            "favorite":
                favorite,
            "first_order":
                first_order,
        })

    finally:
        db.close()


# =========================================================
# CREATE ORDER
# =========================================================

@app.route(
    "/api/order",
    methods=["POST"]
)
def create_order():
    data, user = request_user()

    if not user:
        return jsonify({
            "success": False,
            "error":
                "Please open TAKE AWAY inside Telegram."
        }), 401

    payment_method = str(
        data.get(
            "payment_method",
            "pickup"
        )
    ).strip().lower()

    if payment_method != "pickup":
        return jsonify({
            "success": False,
            "error":
                "Online payment is coming soon. Choose Pay at pickup."
        }), 503

    raw_items = data.get(
        "items"
    ) or []

    reward_item_index = data.get(
        "reward_item_index"
    )

    try:
        items, total, reward_index = (
            calculate_order_items(
                raw_items,
                reward_item_index
            )
        )

    except ValueError as exc:
        return jsonify({
            "success": False,
            "error": str(exc),
        }), 400

    reward_used = (
        reward_index is not None
    )

    if reward_used:
        if (
            reward_index < 0
            or reward_index >= len(items)
        ):
            return jsonify({
                "success": False,
                "error":
                    "Invalid free drink selection."
            }), 400

        if not items[
            reward_index
        ].get("reward_free"):
            return jsonify({
                "success": False,
                "error":
                    "Invalid free drink selection."
            }), 400

    pickup, pickup_error = (
        validate_pickup(
            data.get("pickup")
        )
    )

    if pickup_error:
        return jsonify({
            "success": False,
            "error": pickup_error,
        }), 400

    telegram_id = safe_int(
        user["id"]
    )

    db = get_db()

    reward_consumed = False

    try:
        if reward_used:
            reward_consumed = (
                consume_reward(
                    db,
                    telegram_id
                )
            )

            if not reward_consumed:
                db.rollback()

                return jsonify({
                    "success": False,
                    "error":
                        "Your free drink is no longer available. Please refresh Rewards."
                }), 409

        current = now_iso()

        cursor = db.execute(
            """
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
                'pickup',
                'pending',
                ?, ?, 0
            )
            """,
            (
                telegram_id,
                user.get("username"),
                user.get("first_name"),
                user.get("last_name"),
                json.dumps(
                    items,
                    ensure_ascii=False
                ),
                total,
                pickup.isoformat(),
                current,
                current,
                1 if reward_used else 0,
                reward_index,
            )
        )

        order_id = cursor.lastrowid

        db.commit()

    except Exception:
        db.rollback()
        logging.exception(
            "Could not create order"
        )

        return jsonify({
            "success": False,
            "error":
                "Could not create order. Please try again."
        }), 500

    finally:
        db.close()

    # Send order to barista/admin Telegram.
    if ADMIN:
        message = barista_text(
            order_id,
            user,
            items,
            total,
            pickup.isoformat(),
            payment_method,
        )

        send_message(
            ADMIN,
            message,
            admin_keyboard(
                order_id,
                "new"
            )
        )

    # Customer confirmation.
    send_message(
        telegram_id,
        (
            f"☕ <b>TAKE AWAY order #{order_id}</b>\n\n"
            f"Pickup: <b>{html.escape(pretty_pickup(pickup.isoformat()))}</b>\n"
            f"Total: <b>{total} RSD</b>\n\n"
            "Your order has been received.\n"
            "Pay at pickup by card or cash."
        )
    )

    return jsonify({
        "success": True,
        "order_id": order_id,
        "total": total,
        "pickup":
            pickup.isoformat(),
        "payment_method":
            "pickup",
        "payment_status":
            "pending",
        "reward_used":
            reward_used,
    })


# =========================================================
# TELEGRAM CALLBACKS
# =========================================================

def answer_callback(
    callback_id,
    text=""
):
    if not callback_id:
        return

    telegram_api(
        "answerCallbackQuery",
        {
            "callback_query_id":
                callback_id,
            "text": text,
        }
    )


def handle_admin_callback(
    callback
):
    callback_id = callback.get(
        "id"
    )

    data = str(
        callback.get("data")
        or ""
    )

    message = callback.get(
        "message"
    ) or {}

    chat = message.get(
        "chat"
    ) or {}

    chat_id = chat.get(
        "id"
    )

    message_id = message.get(
        "message_id"
    )

    try:
        action, raw_order_id = (
            data.split(
                ":",
                1
            )
        )

        order_id = int(
            raw_order_id
        )

    except Exception:
        answer_callback(
            callback_id,
            "Invalid action"
        )
        return

    order = get_order(
        order_id
    )

    if not order:
        answer_callback(
            callback_id,
            "Order not found"
        )
        return

    telegram_id = safe_int(
        order["telegram_id"]
    )

    if action == "accept":
        updated = set_order_status(
            order_id,
            "preparing"
        )

        if updated:
            send_message(
                telegram_id,
                customer_status_message(
                    order_id,
                    "preparing"
                )
            )

            if (
                chat_id
                and message_id
            ):
                edit_message_markup(
                    chat_id,
                    message_id,
                    admin_keyboard(
                        order_id,
                        "preparing"
                    )
                )

            answer_callback(
                callback_id,
                "Order accepted"
            )
            return

    elif action == "ready":
        updated = set_order_status(
            order_id,
            "ready"
        )

        if updated:
            send_message(
                telegram_id,
                customer_status_message(
                    order_id,
                    "ready"
                )
            )

            if (
                chat_id
                and message_id
            ):
                edit_message_markup(
                    chat_id,
                    message_id,
                    admin_keyboard(
                        order_id,
                        "ready"
                    )
                )

            answer_callback(
                callback_id,
                "Order ready"
            )
            return

    elif action == "complete":
        if mark_paid_and_completed(
            order_id
        ):
            send_message(
                telegram_id,
                customer_status_message(
                    order_id,
                    "completed"
                )
            )

            if (
                chat_id
                and message_id
            ):
                edit_message_markup(
                    chat_id,
                    message_id,
                    {
                        "inline_keyboard": []
                    }
                )

            answer_callback(
                callback_id,
                "Completed"
            )
            return

    elif action == "cancel":
        updated = cancel_order(
            order_id
        )

        if updated:
            send_message(
                telegram_id,
                customer_status_message(
                    order_id,
                    "cancelled"
                )
            )

            if (
                chat_id
                and message_id
            ):
                edit_message_markup(
                    chat_id,
                    message_id,
                    {
                        "inline_keyboard": []
                    }
                )

            answer_callback(
                callback_id,
                "Cancelled"
            )
            return

    answer_callback(
        callback_id,
        "Action unavailable"
    )


# =========================================================
# TELEGRAM BOT MESSAGES
# =========================================================

def bot_start_text():
    return (
        "☕ <b>TAKE AWAY · THAI TEA & COFFEE</b>\n\n"
        "Order your drinks directly inside Telegram.\n\n"
        "🧋 Thai Tea\n"
        "☕ Coffee\n"
        "✦ Specials\n"
        "🎁 Rewards\n\n"
        "Payment is made at pickup by card or cash."
    )


def web_app_keyboard():
    return {
        "inline_keyboard": [
            [
                {
                    "text":
                        "☕ OPEN TAKE AWAY",
                    "web_app": {
                        "url": URL
                    },
                }
            ]
        ]
    }


def handle_bot_message(
    message
):
    chat = message.get(
        "chat"
    ) or {}

    chat_id = chat.get(
        "id"
    )

    text = str(
        message.get("text")
        or ""
    ).strip()

    if not chat_id:
        return

    if (
        text.startswith("/start")
        or text.startswith("/menu")
    ):
        send_message(
            chat_id,
            bot_start_text(),
            web_app_keyboard()
        )
        return

    if text.startswith("/help"):
        send_message(
            chat_id,
            (
                "☕ <b>TAKE AWAY Help</b>\n\n"
                "Open the ordering app using the button below.\n"
                "Choose your drinks and pickup time.\n"
                "Pay at the café by card or cash."
            ),
            web_app_keyboard()
        )


# =========================================================
# WEBHOOK
# =========================================================

@app.route(
    "/webhook",
    methods=["POST"]
)
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
                "ok": False
            }), 403

    update = request.get_json(
        silent=True
    ) or {}

    try:
        if update.get(
            "callback_query"
        ):
            handle_admin_callback(
                update[
                    "callback_query"
                ]
            )

        elif update.get(
            "message"
        ):
            handle_bot_message(
                update[
                    "message"
                ]
            )

    except Exception:
        logging.exception(
            "Webhook processing failed"
        )

    return jsonify({
        "ok": True
    })


# =========================================================
# OPTIONAL WEBHOOK SETUP
# =========================================================

@app.route(
    "/setup-webhook",
    methods=["GET"]
)
def setup_webhook():
    if not TOKEN:
        return jsonify({
            "success": False,
            "error":
                "BOT_TOKEN is not configured"
        }), 500

    webhook_url = (
        f"{URL}/webhook"
    )

    payload = {
        "url": webhook_url,
        "allowed_updates": [
            "message",
            "callback_query",
        ],
    }

    if WEBHOOK_SECRET:
        payload[
            "secret_token"
        ] = WEBHOOK_SECRET

    result = telegram_api(
        "setWebhook",
        payload
    )

    return jsonify({
        "success": bool(
            result
            and result.get("ok")
        ),
        "webhook":
            webhook_url,
        "telegram":
            result,
    })


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    port = safe_int(
        os.environ.get(
            "PORT",
            "10000"
        ),
        10000
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
    )
