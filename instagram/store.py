"""Agent xotirasi — SQLite (instagram/instagram.db).

Nima saqlanadi:
  posts        — yaratilgan/joylangan postlar (draft, kutilmoqda, joylangan)
  replies      — javob berilgan kommentlar (ikki marta javob bermaslik uchun)
  used_sources — internetdan olingan manbalar (mavzuni takrorlamaslik uchun)
  state        — oddiy kalit/qiymat (oxirgi post vaqti va h.k.)
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable

from .config import DB_PATH

STATUS_DRAFT = "draft"          # yaratildi, hali yuborilmagan
STATUS_PENDING = "pending"      # tasdiq kutmoqda
STATUS_PUBLISHING = "publishing"  # joylanmoqda (ikki marta joylanmasligi uchun)
STATUS_PUBLISHED = "published"
STATUS_REJECTED = "rejected"
STATUS_FAILED = "failed"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS posts (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at    TEXT NOT NULL,
                published_at  TEXT,
                status        TEXT NOT NULL,
                caption       TEXT NOT NULL DEFAULT '',
                alt_text      TEXT NOT NULL DEFAULT '',
                image_path    TEXT NOT NULL DEFAULT '',
                image_url     TEXT NOT NULL DEFAULT '',
                media_type    TEXT NOT NULL DEFAULT 'IMAGE',
                video_url     TEXT NOT NULL DEFAULT '',
                source_url    TEXT NOT NULL DEFAULT '',
                source_title  TEXT NOT NULL DEFAULT '',
                media_id      TEXT NOT NULL DEFAULT '',
                permalink     TEXT NOT NULL DEFAULT '',
                approve_token TEXT NOT NULL DEFAULT '',
                error         TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS replies (
                comment_id   TEXT PRIMARY KEY,
                media_id     TEXT NOT NULL DEFAULT '',
                username     TEXT NOT NULL DEFAULT '',
                comment_text TEXT NOT NULL DEFAULT '',
                reply_text   TEXT NOT NULL DEFAULT '',
                action       TEXT NOT NULL DEFAULT '',
                created_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS used_sources (
                url     TEXT PRIMARY KEY,
                title   TEXT NOT NULL DEFAULT '',
                used_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS state (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status);
            CREATE INDEX IF NOT EXISTS idx_posts_created ON posts(created_at);
            """
        )
        # eskiroq baza bo'lsa — yetishmayotgan ustunlarni qo'shamiz
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(posts)")}
        for column, ddl in (
            ("media_type", "TEXT NOT NULL DEFAULT 'IMAGE'"),
            ("video_url", "TEXT NOT NULL DEFAULT ''"),
        ):
            if column not in existing:
                conn.execute(f"ALTER TABLE posts ADD COLUMN {column} {ddl}")


# --------------------------------------------------------------------- #
# Postlar
# --------------------------------------------------------------------- #
def create_post(caption: str, image_path: str = "", image_url: str = "",
                alt_text: str = "", source_url: str = "", source_title: str = "",
                status: str = STATUS_DRAFT, media_type: str = "IMAGE",
                video_url: str = "") -> int:
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO posts (created_at, status, caption, alt_text,
                                  image_path, image_url, source_url,
                                  source_title, approve_token, media_type,
                                  video_url)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (_now(), status, caption, alt_text, image_path, image_url,
             source_url, source_title, secrets.token_urlsafe(24), media_type,
             video_url),
        )
        return int(cur.lastrowid)


def get_post(post_id: int) -> dict | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone()
        return dict(row) if row else None


def get_post_by_token(token: str) -> dict | None:
    if not token:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM posts WHERE approve_token = ?", (token,)
        ).fetchone()
        return dict(row) if row else None


def update_post(post_id: int, **fields: Any) -> None:
    if not fields:
        return
    allowed = {"status", "caption", "alt_text", "image_path", "image_url",
               "media_id", "permalink", "published_at", "error", "source_url",
               "source_title", "approve_token", "media_type", "video_url"}
    sets, values = [], []
    for key, value in fields.items():
        if key in allowed:
            sets.append(f"{key} = ?")
            values.append(value)
    if not sets:
        return
    values.append(post_id)
    with _connect() as conn:
        conn.execute(f"UPDATE posts SET {', '.join(sets)} WHERE id = ?", values)


def mark_published(post_id: int, media_id: str, permalink: str) -> None:
    update_post(post_id, status=STATUS_PUBLISHED, media_id=media_id,
                permalink=permalink, published_at=_now(), error="")


def mark_failed(post_id: int, error: str) -> None:
    update_post(post_id, status=STATUS_FAILED, error=error[:500])


def list_posts(status: str | None = None, limit: int = 20) -> list[dict]:
    query = "SELECT * FROM posts"
    params: list[Any] = []
    if status:
        query += " WHERE status = ?"
        params.append(status)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with _connect() as conn:
        return [dict(r) for r in conn.execute(query, params).fetchall()]


def recent_captions(limit: int = 8) -> list[str]:
    """Oxirgi joylangan captionlar — takrorlanmaslik uchun promptga beriladi."""
    with _connect() as conn:
        rows = conn.execute(
            """SELECT caption FROM posts
               WHERE status = ? AND caption <> ''
               ORDER BY id DESC LIMIT ?""",
            (STATUS_PUBLISHED, limit),
        ).fetchall()
    return [r["caption"] for r in rows]


def count_published_since(iso_since: str) -> int:
    with _connect() as conn:
        row = conn.execute(
            """SELECT COUNT(*) AS c FROM posts
               WHERE status = ? AND published_at >= ?""",
            (STATUS_PUBLISHED, iso_since),
        ).fetchone()
    return int(row["c"] if row else 0)


# --------------------------------------------------------------------- #
# Kommentlar
# --------------------------------------------------------------------- #
def is_handled(comment_id: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM replies WHERE comment_id = ?", (comment_id,)
        ).fetchone()
    return row is not None


def handled_ids(comment_ids: Iterable[str]) -> set[str]:
    ids = [c for c in comment_ids if c]
    if not ids:
        return set()
    placeholders = ",".join("?" * len(ids))
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT comment_id FROM replies WHERE comment_id IN ({placeholders})",
            ids,
        ).fetchall()
    return {r["comment_id"] for r in rows}


def save_reply(comment_id: str, media_id: str, username: str,
               comment_text: str, reply_text: str, action: str) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO replies
               (comment_id, media_id, username, comment_text, reply_text,
                action, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (comment_id, media_id, username, comment_text[:2000],
             reply_text[:2000], action, _now()),
        )


def count_replies() -> int:
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM replies").fetchone()
    return int(row["c"] if row else 0)


# --------------------------------------------------------------------- #
# Internet manbalari
# --------------------------------------------------------------------- #
def is_source_used(url: str) -> bool:
    with _connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM used_sources WHERE url = ?", (url,)
        ).fetchone()
    return row is not None


def mark_source_used(url: str, title: str = "") -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO used_sources (url, title, used_at) VALUES (?, ?, ?)",
            (url, title[:300], _now()),
        )


# --------------------------------------------------------------------- #
# Oddiy holat (key/value)
# --------------------------------------------------------------------- #
def get_state(key: str, default: str = "") -> str:
    with _connect() as conn:
        row = conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_state(key: str, value: str) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)", (key, value)
        )


def get_json_state(key: str, default: Any = None) -> Any:
    raw = get_state(key)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except ValueError:
        return default


def set_json_state(key: str, value: Any) -> None:
    set_state(key, json.dumps(value, ensure_ascii=False))
