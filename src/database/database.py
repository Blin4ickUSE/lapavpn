from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import aiosqlite

DB_PATH = Path(__file__).resolve().parents[2] / "lapavpn.db"

TRIAL_DAYS = 3
TRIAL_DEVICES_LIMIT = 3
TRIAL_TRAFFIC_GB = 50.0
GRACE_DAYS = 7
REFERRAL_L1_RATE = 0.20
REFERRAL_L2_RATE = 0.05

_db: aiosqlite.Connection | None = None


async def init_db() -> None:
    global _db
    _db = await aiosqlite.connect(DB_PATH)
    _db.row_factory = aiosqlite.Row
    await _db.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            balance REAL NOT NULL DEFAULT 0,
            referral_balance REAL NOT NULL DEFAULT 0,
            total_spent REAL NOT NULL DEFAULT 0,
            trial_used INTEGER NOT NULL DEFAULT 0,
            referrer_id INTEGER,
            pending_referrer_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (referrer_id) REFERENCES users(telegram_id),
            FOREIGN KEY (pending_referrer_id) REFERENCES users(telegram_id)
        )
        """
    )
    await _db.execute(
        """
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('trial', 'paid')),
            expires_at TEXT NOT NULL,
            devices_limit INTEGER NOT NULL DEFAULT 3,
            devices_used INTEGER NOT NULL DEFAULT 0,
            traffic_total_gb REAL NOT NULL DEFAULT 50,
            traffic_used_gb REAL NOT NULL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
        )
        """
    )
    for migration in (
        "ALTER TABLE users ADD COLUMN trial_used INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN referrer_id INTEGER",
        "ALTER TABLE users ADD COLUMN referral_balance REAL NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN total_spent REAL NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN pending_referrer_id INTEGER",
    ):
        try:
            await _db.execute(migration)
        except aiosqlite.OperationalError:
            pass
    await _db.commit()


async def close_db() -> None:
    global _db
    if _db is not None:
        await _db.close()
        _db = None


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _subscription_dict(row: aiosqlite.Row) -> dict:
    data = dict(row)
    data["expires_at"] = _parse_dt(data["expires_at"])
    data["is_active"] = bool(data["is_active"])
    return data


def subscription_purge_date(subscription: dict) -> datetime:
    return subscription["expires_at"] + timedelta(days=GRACE_DAYS)


async def cleanup_expired_subscriptions(telegram_id: int) -> None:
    assert _db is not None

    cursor = await _db.execute(
        """
        SELECT id, expires_at FROM subscriptions
        WHERE telegram_id = ? AND is_active = 1
        """,
        (telegram_id,),
    )
    rows = await cursor.fetchall()
    now = datetime.now()
    for row in rows:
        expires_at = _parse_dt(row["expires_at"])
        if now >= expires_at + timedelta(days=GRACE_DAYS):
            await _db.execute(
                "UPDATE subscriptions SET is_active = 0 WHERE id = ?",
                (row["id"],),
            )
    await _db.commit()


async def get_subscription_status(telegram_id: int) -> tuple[str, dict | None]:
    assert _db is not None

    await cleanup_expired_subscriptions(telegram_id)

    cursor = await _db.execute(
        """
        SELECT * FROM subscriptions
        WHERE telegram_id = ? AND is_active = 1
        ORDER BY expires_at DESC
        """,
        (telegram_id,),
    )
    rows = await cursor.fetchall()
    now = datetime.now()
    for row in rows:
        sub = _subscription_dict(row)
        if sub["expires_at"] > now:
            return "active", sub
        if now < subscription_purge_date(sub):
            return "expired", sub

    return "none", None


async def _resolve_referrer_id(
    telegram_id: int,
    referrer_id: int | None,
) -> int | None:
    if referrer_id is None or referrer_id == telegram_id:
        return None

    assert _db is not None
    cursor = await _db.execute(
        "SELECT telegram_id FROM users WHERE telegram_id = ?",
        (referrer_id,),
    )
    if await cursor.fetchone() is None:
        return None
    return referrer_id


async def get_or_create_user(
    telegram_id: int,
    username: str | None = None,
    first_name: str | None = None,
) -> dict:
    assert _db is not None

    cursor = await _db.execute(
        "SELECT * FROM users WHERE telegram_id = ?",
        (telegram_id,),
    )
    row = await cursor.fetchone()
    if row:
        if username != row["username"] or first_name != row["first_name"]:
            await _db.execute(
                """
                UPDATE users
                SET username = ?, first_name = ?
                WHERE telegram_id = ?
                """,
                (username, first_name, telegram_id),
            )
            await _db.commit()
            cursor = await _db.execute(
                "SELECT * FROM users WHERE telegram_id = ?",
                (telegram_id,),
            )
            row = await cursor.fetchone()
        return dict(row)

    await _db.execute(
        """
        INSERT INTO users (telegram_id, username, first_name, balance, trial_used)
        VALUES (?, ?, ?, 0, 0)
        """,
        (telegram_id, username, first_name),
    )
    await _db.commit()

    cursor = await _db.execute(
        "SELECT * FROM users WHERE telegram_id = ?",
        (telegram_id,),
    )
    row = await cursor.fetchone()
    return dict(row)


async def try_set_pending_referrer(
    telegram_id: int,
    referrer_id: int | None,
    *,
    username: str | None = None,
    first_name: str | None = None,
) -> bool:
    if referrer_id is None or referrer_id == telegram_id:
        return False

    valid_referrer = await _resolve_referrer_id(telegram_id, referrer_id)
    if valid_referrer is None:
        return False

    await get_or_create_user(telegram_id, username=username, first_name=first_name)

    assert _db is not None
    cursor = await _db.execute(
        """
        SELECT referrer_id, pending_referrer_id
        FROM users WHERE telegram_id = ?
        """,
        (telegram_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return False

    if row["referrer_id"] is not None:
        return False

    if row["pending_referrer_id"] is not None:
        return False

    await _db.execute(
        """
        UPDATE users
        SET pending_referrer_id = ?
        WHERE telegram_id = ?
        """,
        (valid_referrer, telegram_id),
    )
    await _db.commit()
    return True


async def confirm_pending_referrer(telegram_id: int) -> bool:
    assert _db is not None

    cursor = await _db.execute(
        """
        SELECT referrer_id, pending_referrer_id
        FROM users WHERE telegram_id = ?
        """,
        (telegram_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return False

    if row["referrer_id"] is not None:
        if row["pending_referrer_id"] is not None:
            await _db.execute(
                """
                UPDATE users
                SET pending_referrer_id = NULL
                WHERE telegram_id = ?
                """,
                (telegram_id,),
            )
            await _db.commit()
        return False

    pending = row["pending_referrer_id"]
    if pending is None:
        return False

    if pending == telegram_id:
        await _db.execute(
            """
            UPDATE users
            SET pending_referrer_id = NULL
            WHERE telegram_id = ?
            """,
            (telegram_id,),
        )
        await _db.commit()
        return False

    cursor = await _db.execute(
        "SELECT telegram_id FROM users WHERE telegram_id = ?",
        (pending,),
    )
    if await cursor.fetchone() is None:
        await _db.execute(
            """
            UPDATE users
            SET pending_referrer_id = NULL
            WHERE telegram_id = ?
            """,
            (telegram_id,),
        )
        await _db.commit()
        return False

    await _db.execute(
        """
        UPDATE users
        SET referrer_id = ?, pending_referrer_id = NULL
        WHERE telegram_id = ?
        """,
        (pending, telegram_id),
    )
    await _db.commit()
    return True


async def record_payment(telegram_id: int, amount: float) -> None:
    assert _db is not None
    if amount <= 0:
        return

    await _db.execute(
        "UPDATE users SET total_spent = total_spent + ? WHERE telegram_id = ?",
        (amount, telegram_id),
    )
    await _db.commit()
    await process_referral_commission(telegram_id, amount)


async def process_referral_commission(payer_id: int, amount: float) -> None:
    assert _db is not None
    if amount <= 0:
        return

    cursor = await _db.execute(
        "SELECT referrer_id FROM users WHERE telegram_id = ?",
        (payer_id,),
    )
    row = await cursor.fetchone()
    if row is None or row["referrer_id"] is None:
        return

    l1_id = row["referrer_id"]
    l1_bonus = amount * REFERRAL_L1_RATE
    await _db.execute(
        """
        UPDATE users
        SET balance = balance + ?, referral_balance = referral_balance + ?
        WHERE telegram_id = ?
        """,
        (l1_bonus, l1_bonus, l1_id),
    )

    cursor = await _db.execute(
        "SELECT referrer_id FROM users WHERE telegram_id = ?",
        (l1_id,),
    )
    l1_row = await cursor.fetchone()
    if l1_row is not None and l1_row["referrer_id"] is not None:
        l2_bonus = amount * REFERRAL_L2_RATE
        await _db.execute(
            """
            UPDATE users
            SET balance = balance + ?, referral_balance = referral_balance + ?
            WHERE telegram_id = ?
            """,
            (l2_bonus, l2_bonus, l1_row["referrer_id"]),
        )

    await _db.commit()


async def get_referral_balance(telegram_id: int) -> float:
    assert _db is not None

    cursor = await _db.execute(
        "SELECT referral_balance FROM users WHERE telegram_id = ?",
        (telegram_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return 0.0
    return float(row["referral_balance"])


async def get_referrals_count(referrer_id: int) -> int:
    assert _db is not None

    cursor = await _db.execute(
        "SELECT COUNT(*) AS cnt FROM users WHERE referrer_id = ?",
        (referrer_id,),
    )
    row = await cursor.fetchone()
    return int(row["cnt"]) if row else 0


async def get_referrals_page(
    referrer_id: int,
    page: int,
    per_page: int = 15,
) -> list[dict]:
    assert _db is not None

    cursor = await _db.execute(
        """
        SELECT telegram_id, username, first_name, total_spent
        FROM users
        WHERE referrer_id = ?
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
        """,
        (referrer_id, per_page, page * per_page),
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def get_balance(telegram_id: int) -> float:
    assert _db is not None

    cursor = await _db.execute(
        "SELECT balance FROM users WHERE telegram_id = ?",
        (telegram_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return 0.0
    return float(row["balance"])


async def get_active_subscription(telegram_id: int) -> dict | None:
    status, sub = await get_subscription_status(telegram_id)
    if status == "active":
        return sub
    return None


async def create_trial_subscription(telegram_id: int) -> dict | None:
    assert _db is not None

    status, _ = await get_subscription_status(telegram_id)
    if status != "none":
        return None

    cursor = await _db.execute(
        "SELECT trial_used FROM users WHERE telegram_id = ?",
        (telegram_id,),
    )
    row = await cursor.fetchone()
    if row is None or row["trial_used"]:
        return None

    expires_at = datetime.now() + timedelta(days=TRIAL_DAYS)
    await _db.execute(
        """
        INSERT INTO subscriptions (
            telegram_id, type, expires_at,
            devices_limit, devices_used,
            traffic_total_gb, traffic_used_gb, is_active
        )
        VALUES (?, 'trial', ?, ?, 0, ?, 0, 1)
        """,
        (
            telegram_id,
            expires_at.isoformat(timespec="seconds"),
            TRIAL_DEVICES_LIMIT,
            TRIAL_TRAFFIC_GB,
        ),
    )
    await _db.execute(
        "UPDATE users SET trial_used = 1 WHERE telegram_id = ?",
        (telegram_id,),
    )
    await _db.commit()
    _, sub = await get_subscription_status(telegram_id)
    return sub


async def deactivate_subscription(telegram_id: int) -> bool:
    assert _db is not None

    status, sub = await get_subscription_status(telegram_id)
    if sub is None:
        return False

    await _db.execute(
        "UPDATE subscriptions SET is_active = 0 WHERE id = ?",
        (sub["id"],),
    )
    await _db.commit()
    return True
