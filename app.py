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
        "price": 370,
        "category": "coffee",
        "boba": False
    },
    {
        "id": "icedamericano",
        "name": "Iced Americano",
        "price": 270,
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

MENU_BY_ID = {
    product["id"]: product
    for product in MENU
}

COFFEE_IDS = {
    "espresso",
    "americano",
    "cappuccino",
    "latte",
    "flatwhite",
    "icedlatte",
    "icedamericano",
    "americanocream",
    "tonic",
    "bumble",
    "peanutlatte"
}

SYRUPS = {
    "vanilla": "Vanilla",
    "salted_caramel": "Salted Caramel",
    "strawberry": "Strawberry"
}


# =========================================================
# DATABASE
# =========================================================

def get_db():

    db = sqlite3.connect(
        DB,
        timeout=20
    )

    db.row_factory = sqlite3.Row

    db.execute(
        "PRAGMA busy_timeout=20000"
    )

    return db


def init_db():

    db = get_db()

    try:

        db.execute(
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

        cols = {
            row[1]
            for row in db.execute(
                "PRAGMA table_info(orders)"
            )
        }

        extra_columns = {
            "payment_status": "TEXT DEFAULT 'unpaid'",
            "payment_provider": "TEXT DEFAULT 'pickup'",
            "payment_method": "TEXT DEFAULT 'pickup'",
            "reward_item_index": "INTEGER",
            "reward_reserved": "INTEGER DEFAULT 0",
            "reward_redeemed": "INTEGER DEFAULT 0",
            "reward_earned": "INTEGER DEFAULT 0"
        }

        for name, definition in extra_columns.items():

            if name not in cols:

                db.execute(
                    f"""
                    ALTER TABLE orders
                    ADD COLUMN {name} {definition}
                    """
                )

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS loyalty_accounts (
                telegram_id TEXT PRIMARY KEY,
                stamps INTEGER NOT NULL DEFAULT 0,
                free_drinks INTEGER NOT NULL DEFAULT 0,
                earned_total INTEGER NOT NULL DEFAULT 0,
                redeemed_total INTEGER NOT NULL DEFAULT 0
            )
            """
        )

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS analytics_visitors (
                telegram_id TEXT PRIMARY KEY,
                first_name TEXT DEFAULT '',
                last_name TEXT DEFAULT '',
                username TEXT DEFAULT '',
                language_code TEXT DEFAULT '',
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                open_count INTEGER NOT NULL DEFAULT 0,
                directions_clicks INTEGER NOT NULL DEFAULT 0,
                first_source TEXT DEFAULT '',
                last_source TEXT DEFAULT ''
            )
            """
        )

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS analytics_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                source TEXT DEFAULT '',
                created TEXT NOT NULL
            )
            """
        )

        db.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_orders_telegram_id
            ON orders(telegram_id)
            """
        )

        db.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_analytics_events_user
            ON analytics_events(telegram_id)
            """
        )

        db.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_analytics_events_type
            ON analytics_events(event_type)
            """
        )

        db.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_analytics_events_created
            ON analytics_events(created)
            """
        )

        db.commit()

    finally:
        db.close()


init_db()


# =========================================================
# ORDER HELPERS
# =========================================================

def get_order(order_id):

    db = get_db()

    try:

        row = db.execute(
            """
            SELECT *
            FROM orders
            WHERE id=?
            """,
            (order_id,)
        ).fetchone()

        return dict(row) if row else None

    finally:
        db.close()


def details(order):

    try:

        data = json.loads(
            order.get("details") or "[]"
        )

        return data if isinstance(data, list) else []

    except (ValueError, TypeError):
        return []


def qty_in_order(order):

    return sum(
        max(
            0,
            int(item.get("qty", 0))
        )
        for item in details(order)
    )


def customer_orders(telegram_id, limit=50):

    db = get_db()

    try:

        rows = db.execute(
            """
            SELECT *
            FROM orders
            WHERE telegram_id=?
            ORDER BY id DESC
            LIMIT ?
            """,
            (
                str(telegram_id),
                limit
            )
        )

        return [
            dict(row)
            for row in rows
        ]

    finally:
        db.close()


# =========================================================
# ANALYTICS HELPERS
# =========================================================

def clean_source(value):

    if not isinstance(value, str):
        return ""

    return value.strip()[:100]


def record_app_open(user, source=""):

    if not isinstance(user, dict) or not user.get("id"):
        return

    telegram_id = str(user["id"])
    source = clean_source(source)
    now = datetime.now(TZ).isoformat()

    first_name = str(user.get("first_name", "") or "")[:100]
    last_name = str(user.get("last_name", "") or "")[:100]
    username = str(user.get("username", "") or "")[:100]
    language_code = str(user.get("language_code", "") or "")[:30]

    db = get_db()

    try:

        db.execute("BEGIN IMMEDIATE")

        existing = db.execute(
            """
            SELECT *
            FROM analytics_visitors
            WHERE telegram_id=?
            """,
            (telegram_id,)
        ).fetchone()

        if existing:

            db.execute(
                """
                UPDATE analytics_visitors
                SET
                    first_name=?,
                    last_name=?,
                    username=?,
                    language_code=?,
                    last_seen=?,
                    open_count=open_count+1,
                    last_source=CASE
                        WHEN ? != ''
                        THEN ?
                        ELSE last_source
                    END
                WHERE telegram_id=?
                """,
                (
                    first_name,
                    last_name,
                    username,
                    language_code,
                    now,
                    source,
                    source,
                    telegram_id
                )
            )

        else:

            db.execute(
                """
                INSERT INTO analytics_visitors (
                    telegram_id,
                    first_name,
                    last_name,
                    username,
                    language_code,
                    first_seen,
                    last_seen,
                    open_count,
                    directions_clicks,
                    first_source,
                    last_source
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?)
                """,
                (
                    telegram_id,
                    first_name,
                    last_name,
                    username,
                    language_code,
                    now,
                    now,
                    source,
                    source
                )
            )

        db.execute(
            """
            INSERT INTO analytics_events (
                telegram_id,
                event_type,
                source,
                created
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                telegram_id,
                "open",
                source,
                now
            )
        )

        db.commit()

    except Exception:

        db.rollback()
        logging.exception("Analytics open error")

    finally:
        db.close()


def record_directions_click(user, source=""):

    if not isinstance(user, dict) or not user.get("id"):
        return

    telegram_id = str(user["id"])
    source = clean_source(source)
    now = datetime.now(TZ).isoformat()

    db = get_db()

    try:

        db.execute("BEGIN IMMEDIATE")

        existing = db.execute(
            """
            SELECT telegram_id
            FROM analytics_visitors
            WHERE telegram_id=?
            """,
            (telegram_id,)
        ).fetchone()

        if not existing:

            db.execute(
                """
                INSERT INTO analytics_visitors (
                    telegram_id,
                    first_name,
                    last_name,
                    username,
                    language_code,
                    first_seen,
                    last_seen,
                    open_count,
                    directions_clicks,
                    first_source,
                    last_source
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1, ?, ?)
                """,
                (
                    telegram_id,
                    str(user.get("first_name", "") or "")[:100],
                    str(user.get("last_name", "") or "")[:100],
                    str(user.get("username", "") or "")[:100],
                    str(user.get("language_code", "") or "")[:30],
                    now,
                    now,
                    source,
                    source
                )
            )

        else:

            db.execute(
                """
                UPDATE analytics_visitors
                SET
                    directions_clicks=directions_clicks+1,
                    last_seen=?,
                    last_source=CASE
                        WHEN ? != ''
                        THEN ?
                        ELSE last_source
                    END
                WHERE telegram_id=?
                """,
                (
                    now,
                    source,
                    source,
                    telegram_id
                )
            )

        db.execute(
            """
            INSERT INTO analytics_events (
                telegram_id,
                event_type,
                source,
                created
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                telegram_id,
                "directions",
                source,
                now
            )
        )

        db.commit()

    except Exception:

        db.rollback()
        logging.exception("Analytics directions error")

    finally:
        db.close()


def analytics_data():

    db = get_db()

    try:

        total_visitors = db.execute(
            "SELECT COUNT(*) FROM analytics_visitors"
        ).fetchone()[0]

        total_opens = db.execute(
            """
            SELECT COALESCE(SUM(open_count), 0)
            FROM analytics_visitors
            """
        ).fetchone()[0]

        returning_visitors = db.execute(
            """
            SELECT COUNT(*)
            FROM analytics_visitors
            WHERE open_count > 1
            """
        ).fetchone()[0]

        directions_clicks = db.execute(
            """
            SELECT COALESCE(SUM(directions_clicks), 0)
            FROM analytics_visitors
            """
        ).fetchone()[0]

        unique_customers = db.execute(
            """
            SELECT COUNT(DISTINCT telegram_id)
            FROM orders
            """
        ).fetchone()[0]

        total_orders = db.execute(
            "SELECT COUNT(*) FROM orders"
        ).fetchone()[0]

        completed_orders = db.execute(
            """
            SELECT COUNT(*)
            FROM orders
            WHERE status='completed'
            """
        ).fetchone()[0]

        completed_revenue = db.execute(
            """
            SELECT COALESCE(SUM(total), 0)
            FROM orders
            WHERE status='completed'
            """
        ).fetchone()[0]

        conversion = (
            round(unique_customers / total_visitors * 100, 1)
            if total_visitors
            else 0
        )

        returning_rate = (
            round(returning_visitors / total_visitors * 100, 1)
            if total_visitors
            else 0
        )

        today = datetime.now(TZ).date().isoformat()

        visitors_today = db.execute(
            """
            SELECT COUNT(*)
            FROM analytics_visitors
            WHERE substr(last_seen, 1, 10)=?
            """,
            (today,)
        ).fetchone()[0]

        opens_today = db.execute(
            """
            SELECT COUNT(*)
            FROM analytics_events
            WHERE event_type='open'
            AND substr(created, 1, 10)=?
            """,
            (today,)
        ).fetchone()[0]

        directions_today = db.execute(
            """
            SELECT COUNT(*)
            FROM analytics_events
            WHERE event_type='directions'
            AND substr(created, 1, 10)=?
            """,
            (today,)
        ).fetchone()[0]

        orders_today = db.execute(
            """
            SELECT COUNT(*)
            FROM orders
            WHERE substr(created, 1, 10)=?
            """,
            (today,)
        ).fetchone()[0]

        source_rows = db.execute(
            """
            SELECT
                CASE
                    WHEN first_source IS NULL OR first_source=''
                    THEN 'direct'
                    ELSE first_source
                END AS source,
                COUNT(*) AS visitors
            FROM analytics_visitors
            GROUP BY
                CASE
                    WHEN first_source IS NULL OR first_source=''
                    THEN 'direct'
                    ELSE first_source
                END
            ORDER BY visitors DESC
            LIMIT 20
            """
        ).fetchall()

        recent_rows = db.execute(
            """
            SELECT
                v.telegram_id,
                v.first_name,
                v.last_name,
                v.username,
                v.first_seen,
                v.last_seen,
                v.open_count,
                v.directions_clicks,
                v.first_source,
                (
                    SELECT COUNT(*)
                    FROM orders o
                    WHERE o.telegram_id=v.telegram_id
                ) AS order_count
            FROM analytics_visitors v
            ORDER BY v.last_seen DESC
            LIMIT 100
            """
        ).fetchall()

        completed_rows = db.execute(
            """
            SELECT details
            FROM orders
            WHERE status='completed'
            """
        ).fetchall()

        drink_counts = {}

        for row in completed_rows:

            try:
                items = json.loads(row["details"] or "[]")
            except Exception:
                items = []

            if not isinstance(items, list):
                continue

            for item in items:

                item_id = item.get("id")

                if item_id not in MENU_BY_ID:
                    continue

                try:
                    quantity = int(item.get("qty", 0))
                except Exception:
                    quantity = 0

                drink_counts[item_id] = (
                    drink_counts.get(item_id, 0)
                    + quantity
                )

        top_drinks = sorted(
            [
                {
                    "id": item_id,
                    "name": MENU_BY_ID[item_id]["name"],
                    "quantity": quantity
                }
                for item_id, quantity in drink_counts.items()
            ],
            key=lambda item: item["quantity"],
            reverse=True
        )[:10]

        return {
            "total_visitors": total_visitors,
            "total_opens": total_opens,
            "returning_visitors": returning_visitors,
            "returning_rate": returning_rate,
            "unique_customers": unique_customers,
            "total_orders": total_orders,
            "completed_orders": completed_orders,
            "completed_revenue": completed_revenue,
            "conversion": conversion,
            "directions_clicks": directions_clicks,
            "visitors_today": visitors_today,
            "opens_today": opens_today,
            "orders_today": orders_today,
            "directions_today": directions_today,
            "sources": [dict(row) for row in source_rows],
            "recent_visitors": [dict(row) for row in recent_rows],
            "top_drinks": top_drinks
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
        "Authentication required.",
        401,
        {
            "WWW-Authenticate":
                'Basic realm="TAKE AWAY Analytics"'
        }
    )


# =========================================================
# LOYALTY / REWARDS
# =========================================================

def loyalty(db, telegram_id):

    telegram_id = str(telegram_id)

    db.execute(
        """
        INSERT OR IGNORE INTO loyalty_accounts
        (telegram_id)
        VALUES (?)
        """,
        (telegram_id,)
    )

    row = db.execute(
        """
        SELECT *
        FROM loyalty_accounts
        WHERE telegram_id=?
        """,
        (telegram_id,)
    ).fetchone()

    return dict(row)


def loyalty_data(telegram_id):

    db = get_db()

    try:

        account = loyalty(db, telegram_id)
        db.commit()

        return {
            "stamps": account["stamps"],
            "free_drinks": account["free_drinks"],
            "earned_total": account["earned_total"],
            "redeemed_total": account["redeemed_total"],
            "target": 9
        }

    finally:
        db.close()


def mark_paid_and_completed(order_id):

    db = get_db()

    try:

        db.execute("BEGIN IMMEDIATE")

        row = db.execute(
            """
            SELECT *
            FROM orders
            WHERE id=?
            """,
            (order_id,)
        ).fetchone()

        if not row:
            db.rollback()
            return False, "Order not found."

        order = dict(row)

        if order["status"] != "ready":
            db.rollback()
            return False, "Order is not ready or already completed."

        if order["payment_status"] == "paid":
            db.rollback()
            return False, "Order already paid."

        account = loyalty(
            db,
            order["telegram_id"]
        )

        reward_used = int(
            order.get("reward_reserved") or 0
        )

        earned_qty = qty_in_order(order) - reward_used

        if earned_qty < 0:
            earned_qty = 0

        stamps = int(account["stamps"]) + earned_qty

        gained_free_drinks, remaining_stamps = divmod(
            stamps,
            9
        )

        db.execute(
            """
            UPDATE orders
            SET
                status='completed',
                payment_status='paid',
                reward_earned=?,
                reward_redeemed=?
            WHERE id=? AND status='ready'
            """,
            (
                earned_qty,
                reward_used,
                order_id
            )
        )

        db.execute(
            """
            UPDATE loyalty_accounts
            SET
                stamps=?,
                free_drinks=free_drinks+?,
                earned_total=earned_total+?,
                redeemed_total=redeemed_total+?
            WHERE telegram_id=?
            """,
            (
                remaining_stamps,
                gained_free_drinks,
                earned_qty,
                reward_used,
                str(order["telegram_id"])
            )
        )

        db.commit()

        return (
            True,
            f"Paid! +{earned_qty} stamps, "
            f"+{gained_free_drinks} free drinks."
        )

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


# =========================================================
# STATUS
# =========================================================

def change_status(order_id, expected, new):

    db = get_db()

    try:

        cursor = db.execute(
            """
            UPDATE orders
            SET status=?
            WHERE id=? AND status=?
            """,
            (
                new,
                order_id,
                expected
            )
        )

        db.commit()

        return cursor.rowcount == 1

    finally:
        db.close()


def cancel_order(order_id):

    db = get_db()

    try:

        db.execute("BEGIN IMMEDIATE")

        row = db.execute(
            """
            SELECT *
            FROM orders
            WHERE id=?
            AND status IN ('new','preparing')
            """,
            (order_id,)
        ).fetchone()

        if not row:
            db.rollback()
            return False

        order = dict(row)

        reserved = int(
            order.get("reward_reserved") or 0
        )

        if reserved:

            loyalty(
                db,
                order["telegram_id"]
            )

            db.execute(
                """
                UPDATE loyalty_accounts
                SET free_drinks=free_drinks+?
                WHERE telegram_id=?
                """,
                (
                    reserved,
                    str(order["telegram_id"])
                )
            )

        db.execute(
            """
            UPDATE orders
            SET
                status='cancelled',
                reward_reserved=0
            WHERE id=?
            """,
            (order_id,)
        )

        db.commit()
        return True

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


# =========================================================
# TELEGRAM
# =========================================================

def tg(method, payload):

    if not TOKEN:
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
                "Telegram %s: %s",
                method,
                result
            )

        return result

    except Exception:

        logging.exception("Telegram API error")
        return None


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


def barista_keyboard(order):

    order_id = order["id"]
    status = order["status"]

    if status == "new":

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

    if status == "preparing":

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

    if status == "ready":

        return {
            "inline_keyboard": [
                [
                    {
                        "text": "💵 PAID & PICKED UP",
                        "callback_data": f"picked:{order_id}"
                    }
                ]
            ]
        }

    return None


# =========================================================
# DATE / SERIALIZATION
# =========================================================

def time_text(value):

    try:

        dt = datetime.fromisoformat(value)

        if dt.tzinfo:
            dt = dt.astimezone(TZ)

        return dt.strftime("%H:%M")

    except Exception:
        return str(value or "")


def date_text(value):

    try:
        return datetime.fromisoformat(value).strftime("%d.%m.%Y")
    except Exception:
        return ""


def serialize_order(order):

    return {
        "id": order["id"],
        "pickup": time_text(order["pickup"]),
        "pickup_iso": order["pickup"],
        "created": date_text(order["created"]),
        "created_iso": order["created"],
        "total": order["total"],
        "status": order["status"],
        "payment_method": order.get("payment_method") or "pickup",
        "payment_status": order.get("payment_status") or "unpaid",
        "reward_redeemed": int(order.get("reward_redeemed") or 0),
        "reward_reserved": int(order.get("reward_reserved") or 0),
        "reward_earned": int(order.get("reward_earned") or 0),
        "items": details(order)
    }


# =========================================================
# BARISTA MESSAGE
# =========================================================

def barista_text(order):

    labels = {
        "new": "🔔 NEW ORDER",
        "preparing": "🟡 PREPARING",
        "ready": "🟢 READY",
        "completed": "✅ COMPLETED",
        "cancelled": "❌ CANCELLED"
    }

    lines = []

    for item in details(order):

        addons = []

        if item.get("extra_shot"):
            addons.append("extra shot")

        if item.get("boba"):
            addons.append("boba")

        if item.get("syrup_name"):
            addons.append(
                "FREE " + item["syrup_name"] + " syrup"
            )

        free = (
            " 🎁 FREE REWARD"
            if item.get("reward_free")
            else ""
        )

        addon_text = (
            " + " + ", ".join(addons)
            if addons
            else ""
        )

        lines.append(
            f"{item.get('qty', 1)}x "
            f"{item.get('name', 'Drink')}"
            f"{free}"
            f"{addon_text}"
            f" — "
            f"{item.get('line_total', 0)} RSD"
        )

    paid = order.get("payment_status") == "paid"

    payment_text = (
        "💚 PAID"
        if paid
        else "💵 UNPAID — PAY AT PICKUP"
    )

    reward_text = ""

    if order.get("reward_reserved"):

        reward_text = (
            "\n🎁 Reward drink reserved: "
            f"{order['reward_reserved']}"
        )

    return (
        "☕ TAKE AWAY — "
        f"{labels.get(order['status'], 'ORDER')}"
        "\n\n"
        f"🧾 Order #{order['id']}\n"
        f"👤 {order['customer']}\n"
        f"🕐 Pickup: {time_text(order['pickup'])}"
        "\n\n"
        + "\n".join(lines)
        + "\n\n"
        f"💰 TOTAL: {order['total']} RSD\n"
        f"{payment_text}"
        f"{reward_text}"
    )


# =========================================================
# TELEGRAM INIT DATA VERIFICATION
# =========================================================

def verify(init_data):

    if (
        not TOKEN
        or not isinstance(init_data, str)
        or not init_data
        or len(init_data) > 8192
    ):
        return None

    try:

        data = dict(
            parse_qsl(
                init_data,
                keep_blank_values=True
            )
        )

        provided_hash = data.pop(
            "hash",
            None
        )

        if not provided_hash:
            return None

        check_string = "\n".join(
            f"{key}={value}"
            for key, value in sorted(data.items())
        )

        secret_key = hmac.new(
            b"WebAppData",
            TOKEN.encode(),
            hashlib.sha256
        ).digest()

        expected_hash = hmac.new(
            secret_key,
            check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(
            expected_hash,
            provided_hash
        ):
            return None

        age = (
            int(time.time())
            - int(data.get("auth_date", "0"))
        )

        if age > 86400 or age < -300:
            return None

        user = json.loads(
            data.get("user", "{}")
        )

        if isinstance(user, dict) and user.get("id"):
            return user

        return None

    except Exception:
        return None


def verified_json():

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return None, {}

    user = verify(
        data.get("initData", "")
    )

    return user, data


# =========================================================
# ROUTES
# =========================================================

@app.route("/")
def home():

    return render_template(
        "index.html"
    )


@app.route("/health")
def health():

    return jsonify({
        "ok": True,
        "bot_token": bool(TOKEN),
        "order_chat": bool(ADMIN),
        "app_url": URL,
        "menu_items": len(MENU),
        "payment_mode": "pickup",
        "rewards": True,
        "analytics": True,
        "admin_password": bool(ADMIN_PASSWORD)
    })


@app.route("/api/menu")
def menu():

    return jsonify(MENU)


# =========================================================
# ANALYTICS API
# =========================================================

@app.route(
    "/api/analytics/open",
    methods=["POST"]
)
def analytics_open():

    user, data = verified_json()

    if not user:

        return jsonify({
            "error":
                "Telegram session could not be verified."
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

    user, data = verified_json()

    if not user:

        return jsonify({
            "error":
                "Telegram session could not be verified."
        }), 401

    record_directions_click(
        user,
        data.get("source", "")
    )

    return jsonify({
        "success": True
    })


# =========================================================
# ADMIN ANALYTICS
# =========================================================

@app.route("/admin/analytics")
def admin_analytics():

    if not admin_authorized():
        return admin_login_required()

    data = analytics_data()

    def safe(value):
        return html.escape(
            str(
                value
                if value is not None
                else ""
            )
        )

    source_rows = ""

    for row in data["sources"]:
        source_rows += f"""
        <tr>
            <td>{safe(row["source"])}</td>
            <td>{safe(row["visitors"])}</td>
        </tr>
        """

    if not source_rows:
        source_rows = """
        <tr><td colspan="2">No traffic data yet</td></tr>
        """

    drink_rows = ""

    for row in data["top_drinks"]:
        drink_rows += f"""
        <tr>
            <td>{safe(row["name"])}</td>
            <td>{safe(row["quantity"])}</td>
        </tr>
        """

    if not drink_rows:
        drink_rows = """
        <tr><td colspan="2">No completed orders yet</td></tr>
        """

    visitor_rows = ""

    for row in data["recent_visitors"]:

        name = " ".join(
            filter(
                None,
                [
                    row["first_name"],
                    row["last_name"]
                ]
            )
        ).strip()

        if not name:
            name = "Telegram user"

        username = (
            "@" + row["username"]
            if row["username"]
            else "—"
        )

        source = row["first_source"] or "direct"
        first_seen = row["first_seen"] or ""
        last_seen = row["last_seen"] or ""

        visitor_rows += f"""
        <tr>
            <td>
                <strong>{safe(name)}</strong>
                <br>
                <small>{safe(username)}</small>
            </td>
            <td>{safe(source)}</td>
            <td>{safe(row["open_count"])}</td>
            <td>{safe(row["order_count"])}</td>
            <td>{safe(row["directions_clicks"])}</td>
            <td>{safe(first_seen[:16].replace("T", " "))}</td>
            <td>{safe(last_seen[:16].replace("T", " "))}</td>
        </tr>
        """

    if not visitor_rows:
        visitor_rows = """
        <tr><td colspan="7">No visitors yet</td></tr>
        """

    page = f"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#043923">

<title>TAKE AWAY Analytics</title>

<style>
:root {{
    --green:#043923;
    --dark:#032c1b;
    --gold:#d6b36a;
    --white:#ffffff;
    --muted:rgba(255,255,255,.62);
}}

* {{
    box-sizing:border-box;
}}

body {{
    margin:0;
    min-height:100vh;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif;
    color:white;
    background:
        radial-gradient(circle at 100% 0%,#116342 0%,transparent 30%),
        linear-gradient(180deg,#063d27,#032c1b);
}}

.shell {{
    max-width:1200px;
    margin:auto;
    padding:30px 16px 70px;
}}

.brand {{
    margin-bottom:28px;
}}

.brand h1 {{
    margin:0;
    font:600 31px Georgia,serif;
    letter-spacing:5px;
}}

.brand p {{
    margin:8px 0 0;
    color:var(--gold);
    font-size:12px;
    letter-spacing:2px;
}}

.cards {{
    display:grid;
    grid-template-columns:repeat(auto-fit,minmax(175px,1fr));
    gap:11px;
}}

.card {{
    min-height:120px;
    padding:18px;
    border:1px solid rgba(255,255,255,.18);
    border-radius:20px;
    background:linear-gradient(145deg,rgba(255,255,255,.15),rgba(255,255,255,.04));
    box-shadow:inset 0 1px 0 rgba(255,255,255,.15),0 12px 30px rgba(0,0,0,.12);
}}

.card-label {{
    color:var(--muted);
    font-size:10px;
    font-weight:800;
    letter-spacing:.7px;
}}

.card-number {{
    margin-top:11px;
    font:31px Georgia,serif;
}}

.gold {{
    color:var(--gold);
}}

.today {{
    margin-top:18px;
    padding:17px;
    border:1px solid rgba(214,179,106,.4);
    border-radius:18px;
    background:rgba(214,179,106,.1);
    line-height:1.8;
    font-size:13px;
}}

.today strong {{
    color:var(--gold);
}}

.section {{
    margin-top:31px;
}}

.section h2 {{
    margin:0 0 13px;
    font:23px Georgia,serif;
}}

.table-wrap {{
    overflow:auto;
    border:1px solid rgba(255,255,255,.15);
    border-radius:18px;
    background:rgba(255,255,255,.05);
}}

table {{
    width:100%;
    min-width:620px;
    border-collapse:collapse;
}}

th {{
    color:var(--gold);
    font-size:10px;
    letter-spacing:.6px;
    text-transform:uppercase;
}}

th,td {{
    padding:13px 14px;
    border-bottom:1px solid rgba(255,255,255,.09);
    text-align:left;
}}

td {{
    font-size:12px;
}}

small {{
    color:var(--muted);
}}

.note {{
    margin-top:30px;
    color:var(--muted);
    font-size:11px;
    line-height:1.6;
}}

@media(max-width:600px) {{
    .shell {{
        padding-top:22px;
    }}

    .brand h1 {{
        font-size:26px;
    }}

    .cards {{
        grid-template-columns:repeat(2,1fr);
    }}

    .card {{
        min-height:105px;
        padding:15px;
    }}

    .card-number {{
        font-size:27px;
    }}
}}
</style>
</head>

<body>

<div class="shell">

<div class="brand">
<h1>TAKE AWAY</h1>
<p>CUSTOMER ANALYTICS</p>
</div>

<div class="cards">

<div class="card">
<div class="card-label">UNIQUE VISITORS</div>
<div class="card-number">{data["total_visitors"]}</div>
</div>

<div class="card">
<div class="card-label">APP OPENS</div>
<div class="card-number">{data["total_opens"]}</div>
</div>

<div class="card">
<div class="card-label">RETURNING VISITORS</div>
<div class="card-number">{data["returning_visitors"]}</div>
<small>{data["returning_rate"]}%</small>
</div>

<div class="card">
<div class="card-label">CUSTOMERS WHO ORDERED</div>
<div class="card-number">{data["unique_customers"]}</div>
</div>

<div class="card">
<div class="card-label">CONVERSION</div>
<div class="card-number gold">{data["conversion"]}%</div>
</div>

<div class="card">
<div class="card-label">TOTAL ORDERS</div>
<div class="card-number">{data["total_orders"]}</div>
</div>

<div class="card">
<div class="card-label">COMPLETED ORDERS</div>
<div class="card-number">{data["completed_orders"]}</div>
</div>

<div class="card">
<div class="card-label">GET DIRECTIONS</div>
<div class="card-number">{data["directions_clicks"]}</div>
</div>

<div class="card">
<div class="card-label">COMPLETED REVENUE</div>
<div class="card-number">{data["completed_revenue"]} RSD</div>
</div>

</div>

<div class="today">
<strong>TODAY</strong>
&nbsp; · &nbsp;
Visitors: {data["visitors_today"]}
&nbsp; · &nbsp;
Opens: {data["opens_today"]}
&nbsp; · &nbsp;
Orders: {data["orders_today"]}
&nbsp; · &nbsp;
Directions: {data["directions_today"]}
</div>

<section class="section">
<h2>Traffic Sources</h2>
<div class="table-wrap">
<table>
<thead>
<tr>
<th>Source</th>
<th>Visitors</th>
</tr>
</thead>
<tbody>
{source_rows}
</tbody>
</table>
</div>
</section>

<section class="section">
<h2>Popular Drinks</h2>
<div class="table-wrap">
<table>
<thead>
<tr>
<th>Drink</th>
<th>Completed quantity</th>
</tr>
</thead>
<tbody>
{drink_rows}
</tbody>
</table>
</div>
</section>

<section class="section">
<h2>Recent Visitors</h2>
<div class="table-wrap">
<table>
<thead>
<tr>
<th>Customer</th>
<th>Source</th>
<th>Opens</th>
<th>Orders</th>
<th>Directions</th>
<th>First seen</th>
<th>Last seen</th>
</tr>
</thead>
<tbody>
{visitor_rows}
</tbody>
</table>
</div>
</section>

<div class="note">
Analytics starts collecting Mini App visitor data after this version is deployed.
Visitors from the old version cannot be reconstructed automatically.
</div>

</div>
</body>
</html>
"""

    return Response(
        page,
        mimetype="text/html"
    )


# =========================================================
# REWARDS API
# =========================================================

@app.route(
    "/api/rewards",
    methods=["POST"]
)
def rewards():

    user, _ = verified_json()

    if not user:

        return jsonify({
            "error":
                "Telegram session could not be verified."
        }), 401

    return jsonify(
        loyalty_data(user["id"])
    )


# =========================================================
# ORDERS API
# =========================================================

@app.route(
    "/api/orders",
    methods=["POST"]
)
def orders_endpoint():

    user, _ = verified_json()

    if not user:

        return jsonify({
            "error":
                "Telegram session could not be verified."
        }), 401

    orders = customer_orders(
        user["id"]
    )

    return jsonify({
        "orders": [
            serialize_order(order)
            for order in orders
        ]
    })


# =========================================================
# CUSTOMER CANCEL ORDER
# =========================================================

@app.route(
    "/api/order/cancel",
    methods=["POST"]
)
def customer_cancel_order():

    user, data = verified_json()

    if not user:

        return jsonify({
            "error":
                "Telegram session could not be verified."
        }), 401

    try:
        order_id = int(
            data.get("order_id")
        )
    except (TypeError, ValueError):

        return jsonify({
            "error":
                "Invalid order."
        }), 400

    telegram_id = str(
        user["id"]
    )

    db = get_db()

    try:

        db.execute(
            "BEGIN IMMEDIATE"
        )

        row = db.execute(
            """
            SELECT *
            FROM orders
            WHERE id=?
            AND telegram_id=?
            """,
            (
                order_id,
                telegram_id
            )
        ).fetchone()

        if not row:

            db.rollback()

            return jsonify({
                "error":
                    "Order not found."
            }), 404

        order = dict(row)

        # Customer may cancel ONLY before barista accepts.
        if order["status"] != "new":

            db.rollback()

            return jsonify({
                "error":
                    "This order can no longer be cancelled."
            }), 409

        reserved = int(
            order.get("reward_reserved") or 0
        )

        if reserved:

            loyalty(
                db,
                telegram_id
            )

            db.execute(
                """
                UPDATE loyalty_accounts
                SET free_drinks=free_drinks+?
                WHERE telegram_id=?
                """,
                (
                    reserved,
                    telegram_id
                )
            )

        cursor = db.execute(
            """
            UPDATE orders
            SET
                status='cancelled',
                reward_reserved=0
            WHERE id=?
            AND telegram_id=?
            AND status='new'
            """,
            (
                order_id,
                telegram_id
            )
        )

        if cursor.rowcount != 1:

            db.rollback()

            return jsonify({
                "error":
                    "This order can no longer be cancelled."
            }), 409

        db.commit()

    except Exception:

        db.rollback()
        raise

    finally:
        db.close()

    updated = get_order(
        order_id
    )

    if ADMIN:

        tg(
            "sendMessage",
            {
                "chat_id":
                    ADMIN,

                "text":
                    (
                        "❌ CUSTOMER CANCELLED ORDER\n\n"
                        f"🧾 Order #{order_id}\n"
                        f"👤 {updated['customer']}\n"
                        f"🕐 Pickup: {time_text(updated['pickup'])}"
                    )
            }
        )

    tg(
        "sendMessage",
        {
            "chat_id":
                telegram_id,

            "text":
                (
                    "❌ TAKE AWAY — ORDER CANCELLED\n\n"
                    f"🧾 #{order_id}\n"
                    "Your order has been cancelled."
                )
        }
    )

    return jsonify({
        "success":
            True,

        "order_id":
            order_id,

        "status":
            "cancelled"
    })


# =========================================================
# PROFILE API
# =========================================================

@app.route(
    "/api/profile",
    methods=["POST"]
)
def profile():

    user, _ = verified_json()

    if not user:

        return jsonify({
            "error":
                "Telegram session could not be verified."
        }), 401

    orders = customer_orders(
        user["id"],
        10000
    )

    completed = [
        order
        for order in orders
        if order["status"] == "completed"
    ]

    counts = {}

    for order in completed:

        for item in details(order):

            item_id = item.get("id")

            if item_id:

                counts[item_id] = (
                    counts.get(item_id, 0)
                    + int(item.get("qty", 0))
                )

    favorite_id = (
        max(counts, key=counts.get)
        if counts
        else None
    )

    name = " ".join(
        filter(
            None,
            [
                user.get("first_name", ""),
                user.get("last_name", "")
            ]
        )
    ).strip()

    favorite = None

    if favorite_id in MENU_BY_ID:

        favorite = {
            "id": favorite_id,
            "name": MENU_BY_ID[favorite_id]["name"]
        }

    return jsonify({
        "telegram_id": str(user["id"]),
        "name": name or "TAKE AWAY Member",
        "first_name": user.get("first_name", ""),
        "username": user.get("username", ""),
        "orders": len(orders),
        "completed_orders": len(completed),
        "drinks": sum(counts.values()),
        "favorite": favorite,
        "first_order": (
            date_text(orders[-1]["created"])
            if orders
            else ""
        ),
        "rewards": loyalty_data(user["id"])
    })


# =========================================================
# CREATE ORDER
# =========================================================

@app.route(
    "/api/order",
    methods=["POST"]
)
def create_order():

    user, data = verified_json()

    if not user:

        return jsonify({
            "error":
                "Open TAKE AWAY from the Telegram bot ORDER button."
        }), 401

    if data.get("payment_method", "pickup") != "pickup":

        return jsonify({
            "error":
                "Online payment is coming soon. Choose Pay at pickup."
        }), 503

    items = data.get("items")

    if (
        not isinstance(items, list)
        or not items
        or len(items) > 20
    ):

        return jsonify({
            "error":
                "Add at least one drink."
        }), 400

    try:

        pickup = datetime.fromisoformat(
            str(
                data.get("pickup", "")
            ).replace(
                "Z",
                "+00:00"
            )
        )

        if pickup.tzinfo is None:
            pickup = pickup.replace(tzinfo=TZ)
        else:
            pickup = pickup.astimezone(TZ)

    except (ValueError, TypeError):

        return jsonify({
            "error":
                "Invalid pickup time."
        }), 400

    now = datetime.now(TZ)

    minimum_pickup = (
        now
        + timedelta(
            minutes=MIN_PREP_MINUTES,
            seconds=-30
        )
    )

    if pickup < minimum_pickup:

        return jsonify({
            "error":
                "Minimum preparation time is 10 minutes."
        }), 400

    if (
        pickup.date() != now.date()
        or not (
            OPEN_HOUR
            <= pickup.hour
            < CLOSE_HOUR
        )
    ):

        return jsonify({
            "error":
                "Pickup is available today between 07:00 and 17:00."
        }), 400

    reward_index = data.get(
        "reward_item_index",
        None
    )

    if reward_index is not None:

        if (
            isinstance(reward_index, bool)
            or not isinstance(reward_index, int)
            or reward_index < 0
            or reward_index >= len(items)
        ):

            return jsonify({
                "error":
                    "Invalid reward selection."
            }), 400

    clean = []
    total = 0
    count = 0

    for index, item in enumerate(items):

        if (
            not isinstance(item, dict)
            or item.get("id") not in MENU_BY_ID
        ):

            return jsonify({
                "error":
                    "Invalid drink."
            }), 400

        product = MENU_BY_ID[
            item["id"]
        ]

        qty = item.get(
            "qty",
            1
        )

        if (
            isinstance(qty, bool)
            or not isinstance(qty, int)
            or not (
                1
                <= qty
                <= MAX_ITEM_QUANTITY
            )
        ):

            return jsonify({
                "error":
                    "Invalid quantity."
            }), 400

        count += qty

        if count > MAX_ORDER_QUANTITY:

            return jsonify({
                "error":
                    "Maximum 30 drinks."
            }), 400

        extra_shot = (
            item.get("extra_shot") is True
            and product["id"] in COFFEE_IDS
        )

        boba = (
            item.get("boba") is True
            and product["boba"]
        )

        # -----------------------------------------
        # FREE SYRUP
        # No "no syrup" value is required.
        # Missing/empty syrup simply means no syrup.
        # -----------------------------------------

        syrup = item.get("syrup")

        if syrup in (None, ""):

            syrup = None

        elif syrup not in SYRUPS:

            return jsonify({
                "error":
                    "Invalid syrup."
            }), 400

        # Syrup is only available for coffee drinks.
        if (
            syrup is not None
            and product["id"] not in COFFEE_IDS
        ):

            return jsonify({
                "error":
                    "Syrup is only available for coffee drinks."
            }), 400

        unit_price = (
            product["price"]
            + (
                150
                if extra_shot
                else 0
            )
            + (
                100
                if boba
                else 0
            )
        )

        is_reward = (
            reward_index == index
        )

        line_total = (
            unit_price * qty
        )

        if is_reward:

            line_total -= product["price"]

        total += line_total

        clean.append({
            "id":
                product["id"],

            "name":
                product["name"],

            "qty":
                qty,

            "extra_shot":
                extra_shot,

            "boba":
                boba,

            "syrup":
                syrup,

            "syrup_name":
                SYRUPS.get(
                    syrup,
                    ""
                ),

            "unit_price":
                unit_price,

            "line_total":
                line_total,

            "reward_free":
                is_reward
        })

    telegram_id = str(
        user["id"]
    )

    customer = " ".join(
        filter(
            None,
            [
                user.get("first_name", ""),
                user.get("last_name", "")
            ]
        )
    ).strip()

    if not customer:

        if user.get("username"):
            customer = "@" + user["username"]
        else:
            customer = telegram_id

    db = get_db()

    try:

        db.execute("BEGIN IMMEDIATE")

        account = loyalty(
            db,
            telegram_id
        )

        reward_reserved = 0

        if reward_index is not None:

            if int(account["free_drinks"]) < 1:

                db.rollback()

                return jsonify({
                    "error":
                        "No free drink available yet. Refresh Rewards."
                }), 409

            db.execute(
                """
                UPDATE loyalty_accounts
                SET free_drinks=free_drinks-1
                WHERE telegram_id=?
                """,
                (telegram_id,)
            )

            reward_reserved = 1

        cursor = db.execute(
            """
            INSERT INTO orders (
                telegram_id,
                customer,
                pickup,
                details,
                total,
                status,
                created,
                payment_status,
                payment_provider,
                payment_method,
                reward_item_index,
                reward_reserved
            )
            VALUES (
                ?, ?, ?, ?, ?,
                'new',
                ?,
                'unpaid',
                'pickup',
                'pickup',
                ?,
                ?
            )
            """,
            (
                telegram_id,
                customer,
                pickup.isoformat(),
                json.dumps(
                    clean,
                    ensure_ascii=False
                ),
                total,
                now.isoformat(),
                reward_index,
                reward_reserved
            )
        )

        order_id = cursor.lastrowid

        db.commit()

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()

    order = get_order(
        order_id
    )

    if ADMIN:

        result = tg(
            "sendMessage",
            {
                "chat_id":
                    ADMIN,

                "text":
                    barista_text(order),

                "reply_markup":
                    barista_keyboard(order)
            }
        )

        if (
            not result
            or not result.get("ok")
        ):

            logging.error(
                "Order %s saved but barista notification failed",
                order_id
            )

    else:

        logging.warning(
            "ORDER_CHAT_ID missing; order %s saved",
            order_id
        )

    customer_message = (
        "✅ TAKE AWAY — ORDER RECEIVED\n\n"
        f"🧾 #{order_id}\n"
        f"🕐 {time_text(order['pickup'])}\n"
        f"💰 {total} RSD\n"
        "💵 Pay at pickup by card or cash."
    )

    if reward_index is not None:

        customer_message += (
            "\n🎁 Free drink applied!"
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
        "success":
            True,

        "order_id":
            order_id,

        "total":
            total,

        "pickup":
            time_text(order["pickup"]),

        "payment_method":
            "pickup",

        "payment_status":
            "unpaid",

        "reward_used":
            reward_reserved
    })


# =========================================================
# CALLBACKS FROM BARISTA CHAT
# =========================================================

def handle_callback(callback):

    callback_id = callback.get("id")
    message = callback.get("message") or {}

    chat_id = str(
        (
            message.get("chat")
            or {}
        ).get("id", "")
    )

    def answer(text):

        if callback_id:

            tg(
                "answerCallbackQuery",
                {
                    "callback_query_id":
                        callback_id,

                    "text":
                        str(text)[:200]
                }
            )

    if not ADMIN or chat_id != str(ADMIN):

        answer("Not allowed.")
        return

    try:

        action, raw_order_id = str(
            callback.get("data", "")
        ).split(
            ":",
            1
        )

        order_id = int(
            raw_order_id
        )

    except (ValueError, TypeError):

        answer("Invalid action.")
        return

    order = get_order(
        order_id
    )

    if not order:

        answer("Order not found.")
        return

    if action == "accept":

        changed = change_status(
            order_id,
            "new",
            "preparing"
        )

        response = "Order accepted."

        customer_note = (
            "✅ TAKE AWAY — ORDER ACCEPTED\n\n"
            f"🧾 #{order_id}\n"
            "We are preparing your drinks. ☕"
        )

    elif action == "ready":

        changed = change_status(
            order_id,
            "preparing",
            "ready"
        )

        response = "Customer notified."

        customer_note = (
            "☕ TAKE AWAY — YOUR ORDER IS READY!\n\n"
            f"🧾 #{order_id}\n"
            "💵 Please pay at pickup."
        )

    elif action == "picked":

        changed, response = mark_paid_and_completed(
            order_id
        )

        customer_note = (
            "💚 TAKE AWAY — THANK YOU!\n\n"
            f"🧾 #{order_id}\n"
            "Payment confirmed.\n"
            "Your Rewards have been updated. ✦"
        )

    elif action == "cancel":

        changed = cancel_order(
            order_id
        )

        response = "Order cancelled."

        customer_note = (
            "❌ TAKE AWAY — ORDER CANCELLED\n\n"
            f"🧾 #{order_id}\n"
            "Sorry for the inconvenience."
        )

    else:

        answer("Unknown action.")
        return

    if not changed:

        if action == "picked":
            answer(response)
        else:
            answer("Order already updated.")

        return

    updated = get_order(
        order_id
    )

    payload = {
        "chat_id":
            chat_id,

        "message_id":
            message.get("message_id"),

        "text":
            barista_text(updated)
    }

    keyboard = barista_keyboard(
        updated
    )

    if keyboard:
        payload["reply_markup"] = keyboard

    tg(
        "editMessageText",
        payload
    )

    tg(
        "sendMessage",
        {
            "chat_id":
                updated["telegram_id"],

            "text":
                customer_note
        }
    )

    answer(response)


# =========================================================
# TELEGRAM WEBHOOK
# =========================================================

@app.route(
    "/api/bot-webhook",
    methods=["POST"]
)
def bot_webhook():

    if WEBHOOK_SECRET:

        provided_secret = request.headers.get(
            "X-Telegram-Bot-Api-Secret-Token",
            ""
        )

        if not hmac.compare_digest(
            provided_secret,
            WEBHOOK_SECRET
        ):

            return "", 403

    update = (
        request.get_json(silent=True)
        or {}
    )

    if not isinstance(update, dict):
        return "", 200

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

    message = update.get("message") or {}
    chat = message.get("chat") or {}

    if chat.get("type") != "private":
        return "", 200

    chat_id = chat.get("id")

    command = str(
        message.get("text", "")
    ).strip()

    command = (
        command
        .split(" ")[0]
        .split("@")[0]
    )

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

        tg(
            "sendMessage",
            {
                "chat_id":
                    chat_id,

                "text":
                    (
                        "☕ WELCOME TO TAKE AWAY\n\n"
                        "THAI TEA & COFFEE\n\n"
                        "Choose drinks, select pickup time "
                        "and pay at the café.\n\n"
                        "✦ Buy 9 paid drinks, get one free!\n\n"
                        "👇 ORDER / PORUČI"
                    ),

                "reply_markup":
                    order_keyboard()
            }
        )

    elif command == "/help":

        tg(
            "sendMessage",
            {
                "chat_id":
                    chat_id,

                "text":
                    (
                        "☕ TAKE AWAY\n"
                        "Pickup 07:00–17:00\n"
                        "Payment at pickup (cash/card).\n"
                        "✦ 9 paid drinks = 1 free drink."
                    ),

                "reply_markup":
                    order_keyboard()
            }
        )

    return "", 200


# =========================================================
# START
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
