"""mogindex_batch.main(): tokenize pending rows, then a fast no-op second run."""

from __future__ import annotations

import mogindex_batch
import mogindex_debug
import mogindex_kiwi


def _snapshot(conn):
    term_count = conn.execute("SELECT COUNT(*) AS n FROM message_terms").fetchone()["n"]
    daily_sum = conn.execute(
        "SELECT COALESCE(SUM(count), 0) AS s FROM daily_terms"
    ).fetchone()["s"]
    max_tok = conn.execute(
        "SELECT MAX(tokenized_at) AS m FROM messages"
    ).fetchone()["m"]
    return term_count, daily_sum, max_tok


def test_batch_tokenizes_then_noops(db, seed_message, test_userdict):
    conn, db_path = db

    # Three content-bearing rows (userdict + dictionary nouns) ...
    seed_message(conn, "a1", content="아르딘 왔어")
    seed_message(conn, "a2", content="카페테리아 갔어")
    seed_message(conn, "a3", content="식당 도서관")
    # ... plus one legacy row whose content was never captured. content=None is
    # impossible through the ingest path (content is a str param), so simulate it.
    seed_message(conn, "legacy1", content="도서관 회의")
    conn.execute("UPDATE messages SET content = NULL WHERE message_id = ?", ("legacy1",))
    conn.commit()
    # main() opens its own connection; release ours (and its WAL locks) first.
    conn.close()

    rc = mogindex_batch.main(["--db", str(db_path), "--userdict", str(test_userdict)])
    assert rc == 0

    conn = mogindex_debug.connect(db_path)
    try:
        # Every row stamped with the current tokenizer version.
        assert (
            conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE tokenized_at IS NULL"
            ).fetchone()["n"]
            == 0
        )
        versions = {
            row["tokenizer_version"]
            for row in conn.execute("SELECT tokenizer_version FROM messages")
        }
        assert versions == {mogindex_kiwi.TOKENIZER_VERSION}

        # Content-bearing rows produced terms.
        assert conn.execute("SELECT COUNT(*) AS n FROM message_terms").fetchone()["n"] > 0
        assert conn.execute("SELECT COUNT(*) AS n FROM daily_terms").fetchone()["n"] > 0

        # The content-NULL legacy row is stamped but carries zero terms.
        legacy = conn.execute(
            "SELECT message_pk, tokenized_at, tokenizer_version "
            "FROM messages WHERE message_id = ?",
            ("legacy1",),
        ).fetchone()
        assert legacy["tokenized_at"] is not None
        assert legacy["tokenizer_version"] == mogindex_kiwi.TOKENIZER_VERSION
        assert (
            conn.execute(
                "SELECT COUNT(*) AS n FROM message_terms WHERE message_pk = ?",
                (legacy["message_pk"],),
            ).fetchone()["n"]
            == 0
        )

        assert mogindex_debug.get_meta(conn, "last_batch_run") is not None
        snapshot_after_first = _snapshot(conn)
    finally:
        conn.close()

    # Second run: nothing pending -> fast no-op path, identical index state.
    rc2 = mogindex_batch.main(["--db", str(db_path), "--userdict", str(test_userdict)])
    assert rc2 == 0

    conn = mogindex_debug.connect(db_path)
    try:
        assert _snapshot(conn) == snapshot_after_first
    finally:
        conn.close()
