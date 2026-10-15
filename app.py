import os
import json
import time
import hmac
import hashlib
import sqlite3
import logging

from datetime import datetime, timedelta
from urllib.parse import parse_qsl
from zoneinfo import ZoneInfo

import requests
from flask import Flask, request, jsonify, render_template


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
DB = os.environ.get("DATABASE_PATH", "orders.db")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

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
    "americanocream",
    "tonic",
    "bumble",
    "peanutlatte"
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

            "payment_status":
                "TEXT DEFAULT 'unpaid'",

            "payment_provider":
                "TEXT DEFAULT 'pickup'",

            "payment_method":
                "TEXT DEFAULT 'pickup'",

            "reward_item_index":
                "INTEGER",

            "reward_reserved":
                "INTEGER DEFAULT 0",

            "reward_redeemed":
                "INTEGER DEFAULT 0",

            "reward_earned":
                "INTEGER DEFAULT 0"
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

        account = loyalty(
            db,
            telegram_id
        )

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

    """
    Final payment confirmation.

    Rewards:
    - every PAID normal drink gives 1 stamp
    - 9 stamps = 1 free drink
    - a free reward drink does NOT earn a stamp
    - reward can only be processed once
    """

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

            return (
                False,
                "Order not found."
            )

        order = dict(row)

        if order["status"] != "ready":

            db.rollback()

            return (
                False,
                "Order is not ready or already completed."
            )

        if order["payment_status"] == "paid":

            db.rollback()

            return (
                False,
                "Order already paid."
            )

        account = loyalty(
            db,
            order["telegram_id"]
        )

        reward_used = int(
            order.get("reward_reserved") or 0
        )

        earned_qty = (
            qty_in_order(order)
            - reward_used
        )

        if earned_qty < 0:
            earned_qty = 0

        stamps = (
            int(account["stamps"])
            + earned_qty
        )

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
            WHERE
                id=?
                AND status='ready'
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
            (
                f"Paid! +{earned_qty} stamps, "
                f"+{gained_free_drinks} free drinks."
            )
        )

    except Exception:

        db.rollback()
        raise

    finally:
        db.close()


# =========================================================
# STATUS
# =========================================================

def change_status(
    order_id,
    expected,
    new
):

    db = get_db()

    try:

        cursor = db.execute(
            """
            UPDATE orders
            SET status=?
            WHERE id=?
            AND status=?
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

        db.execute(
            "BEGIN IMMEDIATE"
        )

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

        # If a customer used a reward but
        # the café cancels the order,
        # give the free drink back.
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


def customer_orders(
    telegram_id,
    limit=50
):

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

        logging.exception(
            "Telegram API error"
        )

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
                        "callback_data":
                            f"accept:{order_id}"
                    }
                ],
                [
                    {
                        "text": "❌ CANCEL",
                        "callback_data":
                            f"cancel:{order_id}"
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
                        "callback_data":
                            f"ready:{order_id}"
                    }
                ],
                [
                    {
                        "text": "❌ CANCEL",
                        "callback_data":
                            f"cancel:{order_id}"
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
                        "callback_data":
                            f"picked:{order_id}"
                    }
                ]
            ]
        }

    return None


# =========================================================
# DATE / ORDER SERIALIZATION
# =========================================================

def time_text(value):

    try:

        dt = datetime.fromisoformat(
            value
        )

        if dt.tzinfo:

            dt = dt.astimezone(TZ)

        return dt.strftime(
            "%H:%M"
        )

    except Exception:

        return str(
            value or ""
        )


def date_text(value):

    try:

        return datetime.fromisoformat(
            value
        ).strftime(
            "%d.%m.%Y"
        )

    except Exception:

        return ""


def serialize_order(order):

    return {
        "id": order["id"],

        "pickup":
            time_text(order["pickup"]),

        "pickup_iso":
            order["pickup"],

        "created":
            date_text(order["created"]),

        "created_iso":
            order["created"],

        "total":
            order["total"],

        "status":
            order["status"],

        "payment_method":
            order.get("payment_method")
            or "pickup",

        "payment_status":
            order.get("payment_status")
            or "unpaid",

        "reward_redeemed":
            int(
                order.get(
                    "reward_redeemed"
                ) or 0
            ),

        "reward_reserved":
            int(
                order.get(
                    "reward_reserved"
                ) or 0
            ),

        "reward_earned":
            int(
                order.get(
                    "reward_earned"
                ) or 0
            ),

        "items":
            details(order)
    }


# =========================================================
# BARISTA MESSAGE
# =========================================================

def barista_text(order):

    labels = {
        "new":
            "🔔 NEW ORDER",

        "preparing":
            "🟡 PREPARING",

        "ready":
            "🟢 READY",

        "completed":
            "✅ COMPLETED",

        "cancelled":
            "❌ CANCELLED"
    }

    lines = []

    for item in details(order):

        addons = []

        if item.get("extra_shot"):
            addons.append(
                "extra shot"
            )

        if item.get("boba"):
            addons.append(
                "boba"
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
            (
                f"{item.get('qty', 1)}x "
                f"{item.get('name', 'Drink')}"
                f"{free}"
                f"{addon_text}"
                f" — "
                f"{item.get('line_total', 0)} RSD"
            )
        )

    paid = (
        order.get("payment_status")
        == "paid"
    )

    payment_text = (
        "💚 PAID"
        if paid
        else
        "💵 UNPAID — PAY AT PICKUP"
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
            for key, value
            in sorted(data.items())
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
            - int(
                data.get(
                    "auth_date",
                    "0"
                )
            )
        )

        if age > 86400 or age < -300:
            return None

        user = json.loads(
            data.get(
                "user",
                "{}"
            )
        )

        if (
            isinstance(user, dict)
            and user.get("id")
        ):
            return user

        return None

    except Exception:

        return None


def verified_json():

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
        "rewards": True
    })


@app.route("/api/menu")
def menu():

    return jsonify(
        MENU
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
        loyalty_data(
            user["id"]
        )
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
                    counts.get(
                        item_id,
                        0
                    )
                    + int(
                        item.get(
                            "qty",
                            0
                        )
                    )
                )

    favorite_id = (
        max(
            counts,
            key=counts.get
        )
        if counts
        else None
    )

    name = " ".join(
        filter(
            None,
            [
                user.get(
                    "first_name",
                    ""
                ),
                user.get(
                    "last_name",
                    ""
                )
            ]
        )
    ).strip()

    favorite = None

    if favorite_id in MENU_BY_ID:

        favorite = {
            "id":
                favorite_id,

            "name":
                MENU_BY_ID[
                    favorite_id
                ]["name"]
        }

    return jsonify({

        "telegram_id":
            str(user["id"]),

        "name":
            name
            or "TAKE AWAY Member",

        "first_name":
            user.get(
                "first_name",
                ""
            ),

        "username":
            user.get(
                "username",
                ""
            ),

        "orders":
            len(orders),

        "completed_orders":
            len(completed),

        "drinks":
            sum(
                counts.values()
            ),

        "favorite":
            favorite,

        "first_order":
            (
                date_text(
                    orders[-1]["created"]
                )
                if orders
                else ""
            ),

        "rewards":
            loyalty_data(
                user["id"]
            )
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

    if data.get(
        "payment_method",
        "pickup"
    ) != "pickup":

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

    # -----------------------------------------
    # PICKUP TIME
    # -----------------------------------------

    try:

        pickup = datetime.fromisoformat(
            str(
                data.get(
                    "pickup",
                    ""
                )
            ).replace(
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

    # -----------------------------------------
    # REWARD SELECTION
    # -----------------------------------------

    reward_index = data.get(
        "reward_item_index",
        None
    )

    if reward_index is not None:

        if (
            isinstance(
                reward_index,
                bool
            )
            or not isinstance(
                reward_index,
                int
            )
            or reward_index < 0
            or reward_index >= len(items)
        ):

            return jsonify({
                "error":
                    "Invalid reward selection."
            }), 400

    # -----------------------------------------
    # VALIDATE CART
    # -----------------------------------------

    clean = []
    total = 0
    count = 0

    for index, item in enumerate(items):

        if (
            not isinstance(item, dict)
            or item.get("id")
            not in MENU_BY_ID
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
            item.get(
                "extra_shot"
            ) is True
            and product["id"]
            in COFFEE_IDS
        )

        boba = (
            item.get(
                "boba"
            ) is True
            and product["boba"]
        )

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

        # Only ONE base drink is free.
        # Add-ons are still paid.
        line_total = (
            unit_price * qty
        )

        if is_reward:

            line_total -= product[
                "price"
            ]

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
                user.get(
                    "first_name",
                    ""
                ),
                user.get(
                    "last_name",
                    ""
                )
            ]
        )
    ).strip()

    if not customer:

        if user.get("username"):

            customer = (
                "@"
                + user["username"]
            )

        else:

            customer = telegram_id

    # -----------------------------------------
    # SAVE ORDER + RESERVE REWARD
    # -----------------------------------------

    db = get_db()

    try:

        db.execute(
            "BEGIN IMMEDIATE"
        )

        account = loyalty(
            db,
            telegram_id
        )

        reward_reserved = 0

        if reward_index is not None:

            if (
                int(
                    account[
                        "free_drinks"
                    ]
                )
                < 1
            ):

                db.rollback()

                return jsonify({
                    "error":
                        "No free drink available yet. Refresh Rewards."
                }), 409

            # Reserve immediately so the same
            # reward cannot be used twice.
            db.execute(
                """
                UPDATE loyalty_accounts
                SET free_drinks=free_drinks-1
                WHERE telegram_id=?
                """,
                (
                    telegram_id,
                )
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

    # -----------------------------------------
    # BARISTA NOTIFICATION
    # -----------------------------------------

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
                    barista_text(
                        order
                    ),

                "reply_markup":
                    barista_keyboard(
                        order
                    )
            }
        )

        if (
            not result
            or not result.get(
                "ok"
            )
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

    # -----------------------------------------
    # CUSTOMER CONFIRMATION
    # -----------------------------------------

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
            time_text(
                order["pickup"]
            ),

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

    callback_id = callback.get(
        "id"
    )

    message = (
        callback.get("message")
        or {}
    )

    chat_id = str(
        (
            message.get("chat")
            or {}
        ).get(
            "id",
            ""
        )
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

    if (
        not ADMIN
        or chat_id != str(ADMIN)
    ):

        answer(
            "Not allowed."
        )

        return

    try:

        action, raw_order_id = str(
            callback.get(
                "data",
                ""
            )
        ).split(
            ":",
            1
        )

        order_id = int(
            raw_order_id
        )

    except (ValueError, TypeError):

        answer(
            "Invalid action."
        )

        return

    order = get_order(
        order_id
    )

    if not order:

        answer(
            "Order not found."
        )

        return

    # -----------------------------------------
    # ACCEPT
    # -----------------------------------------

    if action == "accept":

        changed = change_status(
            order_id,
            "new",
            "preparing"
        )

        response = (
            "Order accepted."
        )

        customer_note = (
            "✅ TAKE AWAY — ORDER ACCEPTED\n\n"
            f"🧾 #{order_id}\n"
            "We are preparing your drinks. ☕"
        )

    # -----------------------------------------
    # READY
    # -----------------------------------------

    elif action == "ready":

        changed = change_status(
            order_id,
            "preparing",
            "ready"
        )

        response = (
            "Customer notified."
        )

        customer_note = (
            "☕ TAKE AWAY — YOUR ORDER IS READY!\n\n"
            f"🧾 #{order_id}\n"
            "💵 Please pay at pickup."
        )

    # -----------------------------------------
    # PAID + PICKED UP
    # -----------------------------------------

    elif action == "picked":

        changed, response = (
            mark_paid_and_completed(
                order_id
            )
        )

        customer_note = (
            "💚 TAKE AWAY — THANK YOU!\n\n"
            f"🧾 #{order_id}\n"
            "Payment confirmed.\n"
            "Your Rewards have been updated. ✦"
        )

    # -----------------------------------------
    # CANCEL
    # -----------------------------------------

    elif action == "cancel":

        changed = cancel_order(
            order_id
        )

        response = (
            "Order cancelled."
        )

        customer_note = (
            "❌ TAKE AWAY — ORDER CANCELLED\n\n"
            f"🧾 #{order_id}\n"
            "Sorry for the inconvenience."
        )

    else:

        answer(
            "Unknown action."
        )

        return

    if not changed:

        if action == "picked":

            answer(
                response
            )

        else:

            answer(
                "Order already updated."
            )

        return

    updated = get_order(
        order_id
    )

    payload = {

        "chat_id":
            chat_id,

        "message_id":
            message.get(
                "message_id"
            ),

        "text":
            barista_text(
                updated
            )
    }

    keyboard = barista_keyboard(
        updated
    )

    if keyboard:

        payload[
            "reply_markup"
        ] = keyboard

    tg(
        "editMessageText",
        payload
    )

    tg(
        "sendMessage",
        {
            "chat_id":
                updated[
                    "telegram_id"
                ],

            "text":
                customer_note
        }
    )

    answer(
        response
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

    # -----------------------------------------
    # CALLBACK
    # -----------------------------------------

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

    # -----------------------------------------
    # NORMAL MESSAGE
    # -----------------------------------------

    message = (
        update.get("message")
        or {}
    )

    chat = (
        message.get("chat")
        or {}
    )

    if chat.get(
        "type"
    ) != "private":

        return "", 200

    chat_id = chat.get(
        "id"
    )

    command = str(
        message.get(
            "text",
            ""
        )
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

                "reply_markup":
                    {
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
