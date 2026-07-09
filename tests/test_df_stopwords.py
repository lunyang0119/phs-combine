"""Document-frequency stopwords: ubiquitous nouns get suppressed in recaps."""

from __future__ import annotations

import mogindex_backfill
import mogindex_debug


def test_df_stopwords_and_recap_exclusion(db, seed_message, test_userdict):
    conn, db_path = db

    # One source, 16 content-bearing messages spread over 8 dates. A "ubiquitous"
    # dictionary noun (식당) appears in 15 of 16 docs (~94%); a "rare" dictionary
    # noun (도서관) appears in exactly 1. The trailing verb (갔어) yields no term.
    dates = [f"2026-06-0{i}" for i in range(1, 9)]  # 2026-06-01 .. 2026-06-08
    for i in range(15):
        seed_message(
            conn,
            f"u{i}",
            content="식당 갔어",
            message_date=dates[i % len(dates)],
            source_id="src1",
        )
    seed_message(
        conn, "r0", content="도서관 갔어", message_date=dates[0], source_id="src1"
    )
    conn.close()

    # cutoff 25%: 15/16 > 0.25 marks 식당 a stopword; 1/16 leaves 도서관 alone.
    rc = mogindex_backfill.main(
        [
            "--db",
            str(db_path),
            "--df-min-docs",
            "5",
            "--df-cutoff-pct",
            "25",
            "--userdict",
            str(test_userdict),
        ]
    )
    assert rc == 0

    conn = mogindex_debug.connect(db_path)
    try:
        stopwords = {
            row["term"]
            for row in conn.execute(
                "SELECT term FROM df_stopwords WHERE source_id = ?", ("src1",)
            )
        }
        assert "식당" in stopwords
        assert "도서관" not in stopwords

        # Recap keywords exclude the source's df_stopwords (NOT EXISTS) but keep the
        # rare term. Explicit start/end sidestep the last-seen date heuristic.
        _start, _end, _topics, keywords = mogindex_debug.get_recap(
            conn,
            user_id="1001",
            start_date="2026-01-01",
            end_date="2026-12-31",
        )
        recap_terms = {row["term"] for row in keywords}
        assert "도서관" in recap_terms
        assert "식당" not in recap_terms

        assert mogindex_debug.get_meta(conn, "backfill_state") == "done"
        assert (
            conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE tokenized_at IS NULL"
            ).fetchone()["n"]
            == 0
        )
    finally:
        conn.close()
