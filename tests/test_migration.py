"""Idempotent migration of a pre-Kiwi (legacy) DB onto the new schema."""

from __future__ import annotations

import mogindex_debug

# The pre-migration schema: messages WITHOUT the four Kiwi columns
# (content / search_context / tokenized_at / tokenizer_version) and no
# df_stopwords / mogindex_meta tables. Reconstructed from initialize_schema by
# omitting exactly those additions, so migrate_schema() has real work to do.
OLD_SCHEMA_SQL = """
CREATE TABLE sources (
    source_id TEXT PRIMARY KEY,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('channel', 'thread')),
    guild_id TEXT NOT NULL,
    category_id TEXT,
    parent_channel_id TEXT,
    name TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE messages (
    message_pk INTEGER PRIMARY KEY,
    message_id TEXT NOT NULL UNIQUE,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    guild_id TEXT NOT NULL,
    channel_id TEXT NOT NULL,
    thread_id TEXT,
    author_id TEXT NOT NULL,
    author_name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    message_date TEXT NOT NULL,
    jump_url TEXT NOT NULL,
    indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE message_terms (
    term TEXT NOT NULL,
    message_pk INTEGER NOT NULL REFERENCES messages(message_pk) ON DELETE CASCADE,
    source_id TEXT NOT NULL,
    message_date TEXT NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (term, message_pk)
);

CREATE TABLE daily_terms (
    source_id TEXT NOT NULL,
    message_date TEXT NOT NULL,
    term TEXT NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (source_id, message_date, term)
);
"""


def _column_names(conn):
    return [row[1] for row in conn.execute("PRAGMA table_info(messages)")]


def _table_names(conn):
    return {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }


def test_migrate_schema_is_idempotent(tmp_path):
    db_path = tmp_path / "legacy.sqlite3"
    conn = mogindex_debug.connect(db_path)
    try:
        # Build the legacy DB by hand (no initialize_schema / migrate yet).
        conn.executescript(OLD_SCHEMA_SQL)
        conn.execute(
            "INSERT INTO sources(source_id, source_kind, guild_id, category_id, "
            "parent_channel_id, name) VALUES (?, ?, ?, ?, ?, ?)",
            ("src1", "channel", "guild1", None, None, "레거시채널"),
        )
        conn.execute(
            "INSERT INTO messages(message_id, source_id, guild_id, channel_id, "
            "thread_id, author_id, author_name, created_at, message_date, jump_url) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "m1",
                "src1",
                "guild1",
                "chan1",
                None,
                "1001",
                "tester",
                "2026-06-01T12:00:00+00:00",
                "2026-06-01",
                "https://discord.com/channels/guild1/chan1/m1",
            ),
        )
        pk = conn.execute(
            "SELECT message_pk FROM messages WHERE message_id = ?", ("m1",)
        ).fetchone()["message_pk"]
        conn.execute(
            "INSERT INTO message_terms(term, message_pk, source_id, message_date, count) "
            "VALUES (?, ?, ?, ?, ?)",
            ("옛날말", pk, "src1", "2026-06-01", 2),
        )
        conn.commit()

        # Sanity: the four Kiwi columns are absent before migrating.
        assert "content" not in _column_names(conn)

        mogindex_debug.migrate_schema(conn)

        cols = _column_names(conn)
        for new_col in ("content", "search_context", "tokenized_at", "tokenizer_version"):
            assert new_col in cols
        assert {"df_stopwords", "mogindex_meta"} <= _table_names(conn)

        # Legacy rows intact; the new columns default to NULL on existing rows.
        assert conn.execute("SELECT COUNT(*) AS n FROM sources").fetchone()["n"] == 1
        assert conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"] == 1
        assert conn.execute("SELECT COUNT(*) AS n FROM message_terms").fetchone()["n"] == 1
        legacy = conn.execute(
            "SELECT content, tokenized_at FROM messages WHERE message_id = ?", ("m1",)
        ).fetchone()
        assert legacy["content"] is None
        assert legacy["tokenized_at"] is None

        # Second migrate: no error, no duplicated columns.
        mogindex_debug.migrate_schema(conn)
        cols_again = _column_names(conn)
        assert cols_again.count("content") == 1
        assert cols_again.count("tokenized_at") == 1

        # Full initialize_schema on the same DB is also safe (creates message_fts /
        # manual_topics, re-runs migrate). Still exactly one of each new column.
        mogindex_debug.initialize_schema(conn)
        cols_final = _column_names(conn)
        assert cols_final.count("content") == 1
        assert cols_final.count("tokenizer_version") == 1
        assert "message_fts" in _table_names(conn)
    finally:
        conn.close()
