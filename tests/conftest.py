"""Shared fixtures for the mogindex Kiwi test suite.

Isolation rules honoured here:
  * Every DB lives under pytest ``tmp_path``; tests always pass ``--db`` explicitly
    to the batch/backfill ``main()``s so the real DB referenced by the repo-root
    ``.env`` (a large production index) is never touched.
  * Kiwi determinism comes from a session-scoped test userdict that registers the
    proper nouns used by tests. In production these character names / server slang
    belong in ``mogindex_userdict.txt``; here we keep a throwaway copy.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

# The modules under test are script-style files at the repo root, not an installed
# package. Put the repo root on sys.path before importing them so the suite works
# regardless of how pytest is invoked (``pytest`` vs ``python -m pytest``) or the
# active import mode. conftest.py is imported before any test module in this dir.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import mogindex_debug  # noqa: E402  (import after sys.path shim)
import mogindex_kiwi  # noqa: E402


@pytest.fixture(scope="session")
def test_userdict(tmp_path_factory) -> Path:
    """A Kiwi user dictionary with the proper nouns tests rely on.

    Only ``아르딘`` is strictly required (it makes segmentation of the character
    name deterministic as NNP). ``카페테리아`` / ``식당`` / ``도서관`` used by tests are
    plain dictionary nouns and are intentionally NOT listed here, so those tests
    exercise Kiwi's built-in dictionary rather than the userdict.
    """
    path = tmp_path_factory.mktemp("userdict") / "mogindex_userdict_test.txt"
    path.write_text("아르딘\tNNP\n", encoding="utf-8")
    return path


@pytest.fixture(scope="session")
def kiwi(test_userdict):
    """One Kiwi instance for the whole session (loads in ~1s)."""
    return mogindex_kiwi.load_kiwi(test_userdict)


@pytest.fixture
def db(tmp_path):
    """A fresh, fully-migrated index DB under tmp_path.

    Yields ``(conn, db_path)``. Tests that invoke a ``main()`` (which opens its own
    connection) should commit and ``conn.close()`` this connection first, then
    re-open a fresh connection for assertions.
    """
    db_path = tmp_path / "index.sqlite3"
    conn = mogindex_debug.connect(db_path)
    mogindex_debug.initialize_schema(conn)
    try:
        yield conn, db_path
    finally:
        # Safe even if the test already closed it (sqlite3 allows repeated close()).
        try:
            conn.close()
        except Exception:
            pass


@pytest.fixture
def seed_message():
    """Return a helper that inserts one raw (untokenized) message.

    The helper upserts the source (idempotent) and stores the message via the real
    ingest path (``insert_message_for_index``), so the row lands with
    ``tokenized_at`` NULL and no term rows — exactly what the bot process writes.
    ``created_at`` is built at noon UTC on ``message_date`` so the stored
    ``message_date`` equals the argument. Returns the insert's bool result.
    """

    def _seed(
        conn,
        message_id,
        *,
        content,
        message_date="2026-07-01",
        source_id="src1",
        author_id="1001",
        author_name="tester",
        channel_id=None,
        search_context="",
        guild_id="guild1",
        source_kind="channel",
        source_name="테스트채널",
        parent_channel_id=None,
        thread_id=None,
    ):
        if channel_id is None:
            channel_id = source_id
        mogindex_debug.upsert_source(
            conn,
            source_id,
            source_kind,
            source_name,
            parent_channel_id,
            guild_id=guild_id,
        )
        created_at = datetime.fromisoformat(f"{message_date}T12:00:00+00:00")
        inserted = mogindex_debug.insert_message_for_index(
            conn,
            message_id=message_id,
            source_id=source_id,
            channel_id=channel_id,
            author_id=author_id,
            author_name=author_name,
            created_at=created_at,
            content=content,
            search_context=search_context,
            guild_id=guild_id,
            thread_id=thread_id,
        )
        conn.commit()
        return inserted

    return _seed
