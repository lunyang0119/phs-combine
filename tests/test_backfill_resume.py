"""mogindex_backfill.main(): wipe-vs-resume decision.

Regression for the legacy-DB incident: a DB migrated from the old heuristic
pipeline has term rows for messages that carry no ``tokenized_at`` stamp. The
backfill must detect those orphans and wipe (message_terms AND daily_terms are
both contaminated), even when ``backfill_state`` claims ``in_progress``.
Conversely, work already done by the incremental batch (same tokenizer version,
stamped in-transaction) must be preserved, not redone.
"""

from __future__ import annotations

import mogindex_backfill
import mogindex_batch
import mogindex_debug


def _term_count(conn, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]


def test_backfill_wipes_legacy_orphan_terms(db, seed_message, test_userdict):
    """Old-pipeline leftovers (terms without a tokenized_at stamp) force a wipe."""
    conn, db_path = db
    seed_message(conn, "m1", content="아르딘 카페테리아 회의")

    # Simulate the legacy heuristic index: term rows exist, message unstamped.
    pk = conn.execute(
        "SELECT message_pk FROM messages WHERE message_id = 'm1'"
    ).fetchone()["message_pk"]
    conn.execute(
        "INSERT INTO message_terms(term, message_pk, source_id, message_date, count) "
        "VALUES ('레거시엔그램', ?, 'src1', '2026-07-01', 3)",
        (pk,),
    )
    conn.execute(
        "INSERT INTO daily_terms(source_id, message_date, term, count) "
        "VALUES ('src1', '2026-07-01', '레거시엔그램', 3)"
    )
    # The stuck-run scenario: state was already left as in_progress.
    mogindex_debug.set_meta(conn, "backfill_state", "in_progress")
    conn.commit()
    conn.close()

    rc = mogindex_backfill.main(
        ["--db", str(db_path), "--userdict", str(test_userdict), "--df-min-docs", "1"]
    )
    assert rc == 0

    conn = mogindex_debug.connect(db_path)
    try:
        # Legacy rows are gone from BOTH tables; Kiwi terms replaced them.
        for table in ("message_terms", "daily_terms"):
            assert (
                conn.execute(
                    f"SELECT COUNT(*) AS n FROM {table} WHERE term = '레거시엔그램'"
                ).fetchone()["n"]
                == 0
            )
        assert (
            conn.execute(
                "SELECT COUNT(*) AS n FROM message_terms WHERE term = '아르딘'"
            ).fetchone()["n"]
            == 1
        )
        assert mogindex_debug.get_meta(conn, "backfill_state") == "done"
    finally:
        conn.close()


def test_backfill_resumes_after_batch_without_rewipe(db, seed_message, test_userdict):
    """Rows the incremental batch already tokenized are kept, not re-accumulated."""
    conn, db_path = db
    seed_message(conn, "m1", content="아르딘 카페테리아")
    conn.close()

    rc = mogindex_batch.main(["--db", str(db_path), "--userdict", str(test_userdict)])
    assert rc == 0

    conn = mogindex_debug.connect(db_path)
    stamp = conn.execute("SELECT tokenized_at FROM messages").fetchone()["tokenized_at"]
    daily_sum = conn.execute("SELECT SUM(count) AS s FROM daily_terms").fetchone()["s"]
    conn.close()
    assert stamp is not None

    rc = mogindex_backfill.main(
        ["--db", str(db_path), "--userdict", str(test_userdict), "--df-min-docs", "1"]
    )
    assert rc == 0

    conn = mogindex_debug.connect(db_path)
    try:
        # Same stamp (row untouched) and, critically, no daily_terms double count.
        assert (
            conn.execute("SELECT tokenized_at FROM messages").fetchone()["tokenized_at"]
            == stamp
        )
        assert (
            conn.execute("SELECT SUM(count) AS s FROM daily_terms").fetchone()["s"]
            == daily_sum
        )
        assert mogindex_debug.get_meta(conn, "backfill_state") == "done"
    finally:
        conn.close()
