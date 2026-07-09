"""Raw-store ingest path (insert_message_for_index): store content, no tokenizing."""

from __future__ import annotations


def test_ingest_stores_raw_and_writes_fts(db, seed_message):
    conn, _db_path = db
    result = seed_message(conn, "m1", content="아르딘 왔어", search_context="카페테리아")
    assert result is True

    row = conn.execute(
        "SELECT content, search_context, tokenized_at "
        "FROM messages WHERE message_id = ?",
        ("m1",),
    ).fetchone()
    assert row["content"] == "아르딘 왔어"
    assert row["search_context"] == "카페테리아"
    assert row["tokenized_at"] is None  # bot process never tokenizes

    # A trigram FTS row is written, but no morphological term rows (raw-store only).
    assert conn.execute("SELECT COUNT(*) AS n FROM message_fts").fetchone()["n"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM message_terms").fetchone()["n"] == 0


def test_ingest_duplicate_message_id_is_ignored(db, seed_message):
    conn, _db_path = db
    assert seed_message(conn, "dup", content="식당") is True
    # Same id, content already present -> insert is a no-op, returns False.
    assert seed_message(conn, "dup", content="식당") is False
    assert (
        conn.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE message_id = ?", ("dup",)
        ).fetchone()["n"]
        == 1
    )


def test_ingest_refills_legacy_null_content(db, seed_message):
    conn, _db_path = db
    seed_message(conn, "leg", content="식당 원문")
    # Simulate a legacy row that lost its content and was already stamped.
    conn.execute(
        "UPDATE messages SET content = NULL, tokenized_at = 'X' WHERE message_id = ?",
        ("leg",),
    )
    conn.commit()

    # Re-ingesting refills content and resets it as a re-tokenize candidate.
    result = seed_message(conn, "leg", content="식당 다시")
    assert result is True
    row = conn.execute(
        "SELECT content, tokenized_at FROM messages WHERE message_id = ?", ("leg",)
    ).fetchone()
    assert row["content"] == "식당 다시"
    assert row["tokenized_at"] is None
