"""Single-instance batch lock: a second batch must yield without doing work."""

from __future__ import annotations

import mogindex_batch
import mogindex_debug


def _tokenized_at(db_path, message_id):
    conn = mogindex_debug.connect(db_path)
    try:
        return conn.execute(
            "SELECT tokenized_at FROM messages WHERE message_id = ?", (message_id,)
        ).fetchone()["tokenized_at"]
    finally:
        conn.close()


def test_held_lock_makes_batch_yield(db, seed_message, test_userdict, monkeypatch):
    conn, db_path = db
    seed_message(conn, "m1", content="식당 갔어")  # dictionary noun; no userdict needed
    conn.close()

    # Hold the batch lock, then invoke main(): it must return 0 quickly WITHOUT
    # touching the pending row (it never even loads Kiwi).
    held = mogindex_batch.acquire_batch_lock(db_path)
    assert held is not None
    try:
        rc = mogindex_batch.main(["--db", str(db_path)])
        assert rc == 0
        assert _tokenized_at(db_path, "m1") is None  # still pending
    finally:
        held.close()

    # Lock released: now a real run stamps the row. main() with no --userdict reads
    # MOGINDEX_USERDICT_PATH, so point it at the deterministic test dictionary.
    monkeypatch.setenv("MOGINDEX_USERDICT_PATH", str(test_userdict))
    rc = mogindex_batch.main(["--db", str(db_path)])
    assert rc == 0
    assert _tokenized_at(db_path, "m1") is not None


def test_acquire_batch_lock_is_exclusive(db):
    conn, db_path = db
    conn.close()

    first = mogindex_batch.acquire_batch_lock(db_path)
    assert first is not None
    try:
        second = mogindex_batch.acquire_batch_lock(db_path)
        assert second is None  # already held elsewhere
    finally:
        first.close()
