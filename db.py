"""SQLite ma'lumotlar bazasi bilan ishlash yordamchilari.

Jarayon: menyuni ODAM chiqaradi (bot emas). Bot menyuni avtomatik aniqlab,
ovqatlarni ro'yxatga oladi, keyin "+"/"-" xabarlarini sanaydi.

Jadvallar:
  sessions(id, chat_id, date, created_at, is_open,
           menu_message_id, summary_message_id)
  items(id, session_id, name, full_norm, first_norm)  -- ovqatlar
  votes(item_id, user_id, user_name)  -- PK(item_id,user_id): bir odam = bir ovoz
"""

import sqlite3
from datetime import datetime
from pathlib import Path

import match

DB_PATH = Path(__file__).with_name("data.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id            INTEGER NOT NULL,
                date               TEXT    NOT NULL,
                created_at         TEXT    NOT NULL,
                is_open            INTEGER NOT NULL DEFAULT 1,
                menu_message_id    INTEGER,
                summary_message_id INTEGER
            );

            CREATE TABLE IF NOT EXISTS items (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                name       TEXT    NOT NULL,
                full_norm  TEXT    NOT NULL,
                first_norm TEXT    NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS votes (
                item_id   INTEGER NOT NULL,
                user_id   INTEGER NOT NULL,
                user_name TEXT    NOT NULL,
                PRIMARY KEY (item_id, user_id),
                FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_open
                ON sessions(chat_id, is_open);

            CREATE TABLE IF NOT EXISTS members (
                chat_id  INTEGER NOT NULL,
                user_id  INTEGER NOT NULL,
                name     TEXT    NOT NULL,
                username TEXT,
                PRIMARY KEY (chat_id, user_id)
            );
            """
        )


def create_menu(chat_id: int, menu_message_id: int, names: list[str]) -> int:
    """Avvalgi sessionni yopib, yangi menyu bilan yangi session ochadi.

    Yangi session id'sini qaytaradi.
    """
    now = datetime.now()
    with _connect() as conn:
        conn.execute(
            "UPDATE sessions SET is_open = 0 WHERE chat_id = ? AND is_open = 1",
            (chat_id,),
        )
        cur = conn.execute(
            "INSERT INTO sessions "
            "(chat_id, date, created_at, is_open, menu_message_id) "
            "VALUES (?, ?, ?, 1, ?)",
            (
                chat_id,
                now.strftime("%Y-%m-%d"),
                now.isoformat(timespec="seconds"),
                menu_message_id,
            ),
        )
        session_id = cur.lastrowid
        for name in names:
            full, first = match.item_keys(name)
            conn.execute(
                "INSERT INTO items (session_id, name, full_norm, first_norm) "
                "VALUES (?, ?, ?, ?)",
                (session_id, name, full, first),
            )
        return session_id


def get_open_session(chat_id: int) -> sqlite3.Row | None:
    with _connect() as conn:
        return conn.execute(
            "SELECT * FROM sessions WHERE chat_id = ? AND is_open = 1 "
            "ORDER BY id DESC LIMIT 1",
            (chat_id,),
        ).fetchone()


def get_all_open_sessions() -> list[sqlite3.Row]:
    """Barcha chatlardagi ochiq sessiyalarni qaytaradi (kunlik hisobot uchun)."""
    with _connect() as conn:
        return conn.execute("SELECT * FROM sessions WHERE is_open = 1").fetchall()


def close_session(chat_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE sessions SET is_open = 0 WHERE chat_id = ? AND is_open = 1",
            (chat_id,),
        )
        return cur.rowcount > 0


def set_summary_message(session_id: int, message_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE sessions SET summary_message_id = ? WHERE id = ?",
            (message_id, session_id),
        )


def get_items(session_id: int) -> list[dict]:
    """Sessiondagi ovqatlarni match.match_items uchun qulay ko'rinishda qaytaradi."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, name, full_norm, first_norm FROM items "
            "WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "full": r["full_norm"],
                "first": r["first_norm"],
            }
            for r in rows
        ]


def add_vote(item_id: int, user_id: int, user_name: str) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO votes (item_id, user_id, user_name) "
            "VALUES (?, ?, ?)",
            (item_id, user_id, user_name),
        )
        return cur.rowcount > 0


def remove_vote(item_id: int, user_id: int) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM votes WHERE item_id = ? AND user_id = ?",
            (item_id, user_id),
        )
        return cur.rowcount > 0


def remove_all_votes(session_id: int, user_id: int) -> int:
    """Foydalanuvchining shu sessiondagi barcha ovozlarini o'chiradi ("-" = kerak emas)."""
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM votes WHERE user_id = ? AND item_id IN "
            "(SELECT id FROM items WHERE session_id = ?)",
            (user_id, session_id),
        )
        return cur.rowcount


def upsert_member(chat_id: int, user_id: int, name: str, username: str | None) -> None:
    """Chatda ko'ringan a'zoni saqlaydi/yangilaydi (menyu chiqqanda teglash uchun)."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO members (chat_id, user_id, name, username) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(chat_id, user_id) DO UPDATE SET name=excluded.name, "
            "username=excluded.username",
            (chat_id, user_id, name, username),
        )


def get_members(chat_id: int) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT user_id, name, username FROM members WHERE chat_id = ? "
            "ORDER BY rowid",
            (chat_id,),
        ).fetchall()
        return [
            {"user_id": r["user_id"], "name": r["name"], "username": r["username"]}
            for r in rows
        ]


def get_counts(session_id: int) -> list[dict]:
    """Har ovqat uchun {name, count, voters(list of names)} qaytaradi."""
    with _connect() as conn:
        items = conn.execute(
            "SELECT id, name FROM items WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        result = []
        for it in items:
            voters = conn.execute(
                "SELECT user_name FROM votes WHERE item_id = ? ORDER BY rowid",
                (it["id"],),
            ).fetchall()
            names = [v["user_name"] for v in voters]
            result.append({"name": it["name"], "count": len(names), "voters": names})
        return result
