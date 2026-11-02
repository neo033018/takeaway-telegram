import os, json, time, hmac, hashlib, sqlite3, logging, secrets, html
from datetime import datetime, timedelta
from urllib.parse import parse_qsl
from zoneinfo import ZoneInfo

import requests
from flask import Flask, request, jsonify, render_template, Response

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv('BOT_TOKEN', '').strip()
URL = os.getenv(
    'APP_URL',
    'https://takeaway-telegram.onrender.com'
).rstrip('/')

ADMIN = os.getenv('ORDER_CHAT_ID', '').strip()
DB = os.getenv('DATABASE_PATH', 'orders.db')
WEBHOOK_SECRET = os.getenv('WEBHOOK_SECRET', '').strip()
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', '')

TZ = ZoneInfo('Europe/Belgrade')

OPEN_HOUR = 8
CLOSE_HOUR = 17
MIN_PREP_MINUTES = 10

MAX_ITEM_QUANTITY = 10
MAX_ORDER_QUANTITY = 30

MENU = [
    dict(
        id='thai',
        name='Classic Thai Milk Tea',
        price=450,
        category='tea',
        boba=True,
        temperature=True
    ),
    dict(
        id='green',
        name='Thai Green Milk Tea',
        price=450,
        category='tea',
        boba=True,
        temperature=True
    ),
    dict(
        id='espresso',
        name='Espresso',
        price=200,
        category='coffee'
    ),
    dict(
        id='americano',
        name='Americano',
        price=250,
        category='coffee',
        temperature=True,
        iced_surcharge=20
    ),
    dict(
        id='cappuccino',
        name='Cappuccino',
        price=320,
        category='coffee'
    ),
    dict(
        id='latte',
        name='Latte',
        price=350,
        category='coffee',
        temperature=True,
        iced_surcharge=20
    ),
    dict(
        id='flatwhite',
        name='Flat White',
        price=450,
        category='coffee'
    ),
    dict(
        id='americanocream',
        name='Iced Americano with Cream',
        price=450,
        category='coffee'
    ),
    dict(
        id='tonic',
        name='Tonic Coffee',
        price=420,
        category='special'
    ),
    dict(
        id='bumble',
        name='Bumble Coffee',
        price=450,
        category='special'
    ),
    dict(
        id='strawberrymatcha',
        name='Strawberry Matcha Latte',
        price=550,
        category='special'
    ),
    dict(
        id='matchalatte',
        name='Matcha Latte',
        price=480,
        category='special',
        temperature=True,
        iced_surcharge=20
    ),
    dict(
        id='dirtymatcha',
        name='Dirty Matcha Latte',
        price=550,
        category='special',
        temperature=True,
        iced_surcharge=20
    ),
    dict(
        id='pumpkinlatte',
        name='Pumpkin Latte',
        price=480,
        category='special',
        temperature=True,
        iced_surcharge=20,
        tag='AUTUMN SPECIAL'
    ),
    dict(
        id='kuvanovino',
        name='Kuvano Vino',
        price=400,
        category='special',
        tag='WINTER SPECIAL'
    ),
]

MENU_BY_ID = {p['id']: p for p in MENU}

# Keep retired names for historical orders.
RETIRED = {
    'lime': 'Thai Iced Lime Tea',
    'peanutlatte': 'Peanut Latte'
}

COFFEE_IDS = {
    'espresso',
    'americano',
    'cappuccino',
    'latte',
    'flatwhite',
    'americanocream',
    'tonic',
    'bumble',
    'pumpkinlatte',
    'dirtymatcha'
}

SYRUP_ALLOWED_IDS = set(COFFEE_IDS)

SYRUPS = {
    'vanilla': 'Vanilla',
    'salted_caramel': 'Salted Caramel',
    'strawberry': 'Strawberry'
}

TEMPERATURE_IDS = {
    p['id'] for p in MENU if p.get('temperature')
}

LEGACY_ITEM_MAP = {
    'icedamericano': ('americano', 'iced'),
    'icedlatte': ('latte', 'iced')
}


def now_local():
    return datetime.now(TZ)


def now_iso():
    return now_local().isoformat()


def safe_int(v, default=0):
    try:
        return int(v)
    except (ValueError, TypeError):
        return default


def get_db():
    db = sqlite3.connect(
        DB,
        timeout=20,
        check_same_thread=False
    )
    db.row_factory = sqlite3.Row
    return db


def ensure_column(db, table, name, definition):
    existing = {
        r['name']
        for r in db.execute(f'PRAGMA table_info({table})')
    }

    if name not in existing:
        db.execute(
            f'ALTER TABLE {table} ADD COLUMN {name} {definition}'
        )


def init_db():
    db = get_db()

    try:
        db.execute('''
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
        ''')

        for name, definition in [
            ('payment_method', "TEXT DEFAULT 'pickup'"),
            ('payment_status', "TEXT DEFAULT 'pending'"),
            ('reward_used', 'INTEGER DEFAULT 0'),
            ('reward_item_index', 'INTEGER'),
            ('reward_processed', 'INTEGER DEFAULT 0')
        ]:
            ensure_column(
                db,
                'orders',
                name,
                definition
            )

        db.execute('''
            CREATE TABLE IF NOT EXISTS loyalty_accounts (
                telegram_id INTEGER PRIMARY KEY,
                stamps INTEGER NOT NULL DEFAULT 0,
                free_drinks INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
        ''')

        db.execute('''
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
        ''')

        db.execute('''
            CREATE TABLE IF NOT EXISTS analytics_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER,
                event_type TEXT NOT NULL,
                source TEXT,
                created_at TEXT NOT NULL
            )
        ''')

        indexes = [
            ('idx_orders_telegram', 'orders', 'telegram_id'),
            ('idx_orders_created', 'orders', 'created_at'),
            ('idx_orders_status', 'orders', 'status'),
            (
                'idx_analytics_events_created',
                'analytics_events',
                'created_at'
            ),
            (
                'idx_analytics_events_type',
                'analytics_events',
                'event_type'
            )
        ]

        for index, table, column in indexes:
            db.execute(
                f'CREATE INDEX IF NOT EXISTS {index} '
                f'ON {table}({column})'
            )

        db.commit()

    finally:
        db.close()


init_db()


def parse_datetime(value):
    try:
        dt = datetime.fromisoformat(
            str(value).replace('Z', '+00:00')
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

        digest = fields.pop('hash', None)
        auth = safe_int(fields.get('auth_date'))

        if not digest or not auth:
            return None

        if abs(int(time.time()) - auth) > 86400:
            return None

        check = '\n'.join(
            f'{k}={fields[k]}'
            for k in sorted(fields)
        )

        key = hmac.new(
            b'WebAppData',
            TOKEN.encode(),
            hashlib.sha256
        ).digest()

        expected = hmac.new(
            key,
            check.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(expected, digest):
            return None

        user = json.loads(fields['user'])

        return user if user.get('id') else None

    except (ValueError, KeyError, TypeError):
        return None


def request_user():
    data = request.get_json(silent=True) or {}

    return data, verify_init_data(
        data.get('initData', '')
    )


def details(row):
    try:
        return json.loads(row['items'] or '[]')
    except (ValueError, TypeError):
        return []


def ensure_loyalty_account(db, telegram_id):
    db.execute('''
        INSERT OR IGNORE INTO loyalty_accounts
        (telegram_id, stamps, free_drinks, updated_at)
        VALUES (?, 0, 0, ?)
    ''', (
        telegram_id,
        now_iso()
    ))

    return db.execute('''
        SELECT *
        FROM loyalty_accounts
        WHERE telegram_id = ?
    ''', (telegram_id,)).fetchone()


def get_rewards(telegram_id):
    db = get_db()

    try:
        row = ensure_loyalty_account(
            db,
            telegram_id
        )

        db.commit()

        return {
            'stamps': safe_int(row['stamps']),
            'free_drinks': safe_int(row['free_drinks'])
        }

    finally:
        db.close()


def consume_reward(db, telegram_id):
    ensure_loyalty_account(
        db,
        telegram_id
    )

    cur = db.execute('''
        UPDATE loyalty_accounts
        SET free_drinks = free_drinks - 1,
            updated_at = ?
        WHERE telegram_id = ?
          AND free_drinks > 0
    ''', (
        now_iso(),
        telegram_id
    ))

    return cur.rowcount == 1


def restore_reward(db, telegram_id):
    ensure_loyalty_account(
        db,
        telegram_id
    )

    db.execute('''
        UPDATE loyalty_accounts
        SET free_drinks = free_drinks + 1,
            updated_at = ?
        WHERE telegram_id = ?
    ''', (
        now_iso(),
        telegram_id
    ))


def add_loyalty_drinks(db, telegram_id, quantity):
    if quantity <= 0:
        return

    row = ensure_loyalty_account(
        db,
        telegram_id
    )

    total = safe_int(row['stamps']) + quantity

    db.execute('''
        UPDATE loyalty_accounts
        SET stamps = ?,
            free_drinks = ?,
            updated_at = ?
        WHERE telegram_id = ?
    ''', (
        total % 9,
        safe_int(row['free_drinks']) + total // 9,
        now_iso(),
        telegram_id
    ))def get_order(order_id):
    db = get_db()
    try:
        return db.execute(
            'SELECT * FROM orders WHERE id=?',
            (order_id,)
        ).fetchone()
    finally:
        db.close()


def mark_paid_and_completed(order_id):
    db = get_db()
    try:
        db.execute('BEGIN IMMEDIATE')

        row = db.execute(
            'SELECT * FROM orders WHERE id=?',
            (order_id,)
        ).fetchone()

        if not row or row['status'] != 'ready':
            db.rollback()
            return False

        if not safe_int(row['reward_processed']):
            paid = sum(
                safe_int(x.get('qty'))
                for x in details(row)
                if not x.get('reward_free')
            )

            add_loyalty_drinks(
                db,
                safe_int(row['telegram_id']),
                paid
            )

        db.execute(
            """
            UPDATE orders
            SET status='completed',
                payment_status='paid',
                reward_processed=1,
                updated_at=?
            WHERE id=?
            """,
            (now_iso(), order_id)
        )

        db.commit()
        return True

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def calculate_item(raw, reward_free=False):
    raw_id = str(raw.get('id') or '').strip()

    item_id, forced = LEGACY_ITEM_MAP.get(
        raw_id,
        (raw_id, raw.get('temperature'))
    )

    product = MENU_BY_ID.get(item_id)

    if not product:
        raise ValueError(f'Unknown drink: {raw_id}')

    qty = safe_int(raw.get('qty'), 1)

    if qty < 1 or qty > MAX_ITEM_QUANTITY:
        raise ValueError(
            'Invalid drink quantity (1–10).'
        )

    if reward_free and qty != 1:
        raise ValueError(
            'A free reward must be one drink.'
        )

    temperature = (
        str(forced or 'hot').lower()
        if item_id in TEMPERATURE_IDS
        else None
    )

    if temperature not in ('hot', 'iced', None):
        raise ValueError('Invalid temperature')

    extra_shot = (
        item_id in COFFEE_IDS
        and raw.get('extra_shot') is True
    )

    boba = (
        item_id in ('thai', 'green')
        and raw.get('boba') is True
    )

    syrup = (
        str(raw.get('syrup') or '').lower()
        if item_id in SYRUP_ALLOWED_IDS
        else ''
    )

    syrup = syrup if syrup in SYRUPS else None

    iced = (
        safe_int(product.get('iced_surcharge'))
        if temperature == 'iced'
        else 0
    )

    base = product['price']

    extra = (
        (150 if extra_shot else 0)
        + (100 if boba else 0)
        + iced
    )

    unit = base + extra

    return dict(
        id=item_id,
        name=product['name'],
        qty=qty,
        base_price=base,
        unit_price=unit,
        line_total=(
            extra if reward_free else unit
        ) * qty,
        extra_shot=extra_shot,
        boba=boba,
        syrup=syrup,
        temperature=temperature,
        reward_free=bool(reward_free)
    )


def calculate_order_items(
    raw_items,
    reward_item_index=None
):
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError('Order is empty')

    reward_index = None

    if reward_item_index is not None:
        if isinstance(reward_item_index, bool):
            raise ValueError('Invalid reward selection')

        try:
            reward_index = int(reward_item_index)
        except (TypeError, ValueError):
            raise ValueError('Invalid reward selection')

        if reward_index < 0 or reward_index >= len(raw_items):
            raise ValueError('Invalid reward selection')

    items = []

    for i, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise ValueError('Invalid item')

        items.append(
            calculate_item(
                raw,
                reward_free=(i == reward_index)
            )
        )

    if sum(x['qty'] for x in items) > MAX_ORDER_QUANTITY:
        raise ValueError(
            'Maximum 30 drinks per order.'
        )

    total = sum(
        x['line_total']
        for x in items
    )

    return items, total, reward_index


def validate_pickup(value):
    pickup = parse_datetime(value)

    if not pickup:
        return None, 'Invalid pickup time.'

    now = now_local()

    if pickup.date() != now.date():
        return None, 'Pickup is available today only.'

    if pickup < now + timedelta(
        minutes=MIN_PREP_MINUTES - 2
    ):
        return None, (
            'Please allow at least 10 minutes '
            'for preparation.'
        )

    if not OPEN_HOUR <= pickup.hour < CLOSE_HOUR:
        return None, (
            f'Pickup is available between '
            f'{OPEN_HOUR:02d}:00 and '
            f'{CLOSE_HOUR:02d}:00.'
        )

    return pickup, None


def pretty_pickup(value):
    dt = parse_datetime(value)
    return (
        dt.strftime('%H:%M')
        if dt
        else str(value or '')
    )


def pretty_created(value):
    dt = parse_datetime(value)
    return (
        dt.strftime('%d.%m.%Y %H:%M')
        if dt
        else str(value or '')
    )


def serialize_order(row):
    return dict(
        id=row['id'],
        items=details(row),
        total=safe_int(row['total']),
        pickup=pretty_pickup(row['pickup']),
        pickup_iso=row['pickup'],
        status=row['status'],
        payment_method=row['payment_method'],
        payment_status=row['payment_status'],
        reward_used=bool(row['reward_used']),
        created=pretty_created(row['created_at']),
        created_at=row['created_at']
    )


def telegram_api(method, payload=None, timeout=20):
    if not TOKEN:
        return {
            'ok': False,
            'description': 'BOT_TOKEN missing'
        }

    try:
        response = requests.post(
            f'https://api.telegram.org/bot{TOKEN}/{method}',
            json=payload or {},
            timeout=timeout
        )

        result = response.json()

        if not response.ok or not result.get('ok'):
            logging.error(
                'Telegram %s failed: %s',
                method,
                result
            )

        return result

    except Exception:
        logging.exception(
            'Telegram %s failed',
            method
        )
        return {'ok': False}


def send_message(chat_id, text, reply_markup=None):
    if not chat_id:
        return None

    payload = dict(
        chat_id=chat_id,
        text=text,
        parse_mode='HTML',
        disable_web_page_preview=True
    )

    if reply_markup is not None:
        payload['reply_markup'] = reply_markup

    return telegram_api('sendMessage', payload)


def answer_callback(
    callback_id,
    text='',
    show_alert=False
):
    if callback_id:
        telegram_api(
            'answerCallbackQuery',
            dict(
                callback_query_id=callback_id,
                text=text,
                show_alert=show_alert
            )
        )


def admin_keyboard(order_id, status='new'):
    options = {
        'new': [
            ('✅ ACCEPT', 'accept'),
            ('❌ CANCEL', 'cancel')
        ],
        'preparing': [
            ('☕ READY', 'ready'),
            ('❌ CANCEL', 'cancel')
        ],
        'ready': [
            ('💳 PAID / PICKED UP', 'complete'),
            ('❌ CANCEL', 'cancel')
        ]
    }.get(status, [])

    return {
        'inline_keyboard': [
            [{
                'text': label,
                'callback_data': f'{action}:{order_id}'
            }]
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
        ' '.join(
            filter(
                None,
                [
                    user.get('first_name'),
                    user.get('last_name')
                ]
            )
        ) or 'Customer'
    )

    lines = [
        '☕ <b>NEW TAKE AWAY ORDER</b>',
        '',
        f'🧾 <b>Order #{order_id}</b>',
        f'👤 {name}'
    ]

    if user.get('username'):
        lines.append(
            '📱 @' + html.escape(
                str(user['username'])
            )
        )

    lines.extend([
        f'🕒 Pickup: <b>{pretty_pickup(pickup)}</b>',
        '💳 Payment: <b>PAY AT PICKUP</b>',
        '',
        '<b>DRINKS</b>'
    ])

    for item in items:
        icon = '🎁' if item['reward_free'] else '•'

        lines.append(
            f"{icon} <b>{item['qty']} × "
            f"{html.escape(item['name'])}</b> "
            f"— {item['line_total']} RSD"
        )

        if item['temperature']:
            lines.append(
                '☕ HOT'
                if item['temperature'] == 'hot'
                else '🧊 ICED'
            )

        if item['extra_shot']:
            lines.append('➕ Extra shot +150 RSD')

        if item['boba']:
            lines.append('🧋 Boba +100 RSD')

        if item['syrup']:
            lines.append(
                '🍯 ' + SYRUPS[item['syrup']] + ' · FREE'
            )

        if item['reward_free']:
            lines.append('🎁 FREE DRINK REWARD')

    lines.extend([
        '',
        f'💰 <b>TOTAL: {total} RSD</b>',
        'Payment at café by card or cash.'
    ])

    return '\n'.join(lines)


def customer_status_message(order_id, status):
    messages = {
        'preparing': (
            'Your order has been accepted '
            'and is being prepared.'
        ),
        'ready': (
            'Your order is ready! '
            'Pay at the café by card or cash.'
        ),
        'completed': (
            'Thank you for choosing TAKE AWAY!'
        ),
        'cancelled': (
            'Your order was cancelled. '
            'Please contact TAKE AWAY if you need help.'
        )
    }

    return (
        f'☕ <b>Order #{order_id}</b>\n\n'
        + messages.get(status, status)
    )


def set_order_status(
    order_id,
    new_status,
    allowed_from
):
    db = get_db()

    try:
        db.execute('BEGIN IMMEDIATE')

        row = db.execute(
            'SELECT * FROM orders WHERE id=?',
            (order_id,)
        ).fetchone()

        if not row or row['status'] not in allowed_from:
            db.rollback()
            return False

        db.execute(
            """
            UPDATE orders
            SET status=?, updated_at=?
            WHERE id=?
            """,
            (
                new_status,
                now_iso(),
                order_id
            )
        )

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
        db.execute('BEGIN IMMEDIATE')

        row = db.execute(
            'SELECT * FROM orders WHERE id=?',
            (order_id,)
        ).fetchone()

        if not row or row['status'] in (
            'completed',
            'cancelled'
        ):
            db.rollback()
            return False

        if safe_int(row['reward_used']):
            restore_reward(
                db,
                safe_int(row['telegram_id'])
            )

        db.execute(
            """
            UPDATE orders
            SET status='cancelled',
                reward_used=0,
                updated_at=?
            WHERE id=?
            """,
            (now_iso(), order_id)
        )

        db.commit()
        return True

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


def handle_admin_callback(callback):
    callback_id = callback.get('id')
    data = str(callback.get('data') or '')

    message = callback.get('message') or {}
    chat = message.get('chat') or {}

    chat_id = chat.get('id')
    message_id = message.get('message_id')

    if not ADMIN or str(chat_id) != str(ADMIN):
        answer_callback(
            callback_id,
            'Not authorized',
            True
        )
        return

    try:
        action, raw_id = data.split(':', 1)
        order_id = int(raw_id)
    except (ValueError, TypeError):
        answer_callback(
            callback_id,
            'Invalid action',
            True
        )
        return

    if action not in (
        'accept',
        'ready',
        'complete',
        'cancel'
    ):
        answer_callback(
            callback_id,
            'Unknown action',
            True
        )
        return

    order = get_order(order_id)

    if not order:
        answer_callback(
            callback_id,
            'Order not found',
            True
        )
        return

    current = order['status']

    new = {
        'accept': 'preparing',
        'ready': 'ready',
        'complete': 'completed',
        'cancel': 'cancelled'
    }[action]

    allowed = {
        'accept': 'new',
        'ready': 'preparing',
        'complete': 'ready'
    }

    if action in allowed and current != allowed[action]:
        answer_callback(
            callback_id,
            f'Order is {current}',
            True
        )
        return

    if action == 'cancel' and current in (
        'cancelled',
        'completed'
    ):
        answer_callback(
            callback_id,
            f'Order is {current}',
            True
        )
        return

    try:
        if action == 'complete':
            success = mark_paid_and_completed(order_id)

        elif action == 'cancel':
            success = cancel_order(order_id)

        else:
            success = set_order_status(
                order_id,
                new,
                {allowed[action]}
            )

    except Exception:
        logging.exception('Order status failed')

        answer_callback(
            callback_id,
            'Could not update order',
            True
        )
        return

    if not success:
        answer_callback(
            callback_id,
            'Order status changed; refresh',
            True
        )
        return

    send_message(
        order['telegram_id'],
        customer_status_message(order_id, new)
    )

    if chat_id and message_id:
        telegram_api(
            'editMessageReplyMarkup',
            dict(
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=admin_keyboard(
                    order_id,
                    new
                )
            )
        )

    answer_callback(
        callback_id,
        f'Order {new}'
    )
