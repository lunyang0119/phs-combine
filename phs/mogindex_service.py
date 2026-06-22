from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import time
import uuid
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Iterator, Literal
from zoneinfo import ZoneInfo

from mogindex_debug import STOP_TERMS, connect, extract_terms, initialize_schema, normalize_text


DEFAULT_DB_PATH = Path(
    os.getenv("MOGINDEX_DB_PATH", "/home/ubuntu/mogtel/mogindex/search_index_live_phs_debug.sqlite3")
)
SESSION_TTL_MINUTES = 30
KST = ZoneInfo("Asia/Seoul")
logger = logging.getLogger(__name__)
LOCK_RETRY_DELAYS = (0.0, 0.1, 0.25, 0.5)

Mode = Literal["hub", "keyword", "recap", "topic", "participants", "recent"]
Visibility = Literal["private", "shared"]
DatePreset = Literal["today", "7d", "30d", "custom", "all"]
SourceScope = Literal["all_indexed", "current_channel", "selected_sources"]
SortMode = Literal["relevance", "newest", "oldest"]


class SearchSessionError(Exception):
    pass


class SearchSessionNotFound(SearchSessionError):
    pass


class SearchSessionForbidden(SearchSessionError):
    pass


class SearchSessionExpired(SearchSessionError):
    pass


@dataclass
class SearchPanelState:
    session_id: str
    owner_user_id: str
    guild_id: str
    origin_channel_id: str
    mode: Mode = "hub"
    visibility: Visibility = "private"
    query: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    date_preset: DatePreset = "30d"
    source_scope: SourceScope = "all_indexed"
    source_ids: list[str] = field(default_factory=list)
    source_selector: str | None = None
    author_ids: list[str] = field(default_factory=list)
    sort: SortMode = "relevance"
    page: int = 0
    page_size: int = 5
    last_result_kind: str | None = None
    last_result_ids: list[int] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    expires_at: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> "SearchPanelState":
        data = json.loads(raw)
        return cls(**data)


@dataclass
class SearchResult:
    message_pk: int
    source_id: str
    source_name: str
    author_id: str
    author_name: str
    message_date: str
    created_at: str
    jump_url: str
    score: int = 0


@dataclass
class TopicResult:
    topic_id: int
    topic_date: str
    source_id: str | None
    source_name: str
    title: str
    jump_url: str


@dataclass
class ParticipantsResult:
    message_date: str
    source_id: str
    source_name: str
    author_id: str
    author_name: str
    message_count: int


@dataclass
class SourceMatch:
    source_id: str
    source_kind: str
    parent_channel_id: str | None
    name: str


@dataclass
class TextPage:
    title: str
    lines: list[str]
    page: int
    page_size: int
    total: int
    empty_message: str = "색인된 결과가 없습니다."

    @property
    def has_previous(self) -> bool:
        return self.page > 0

    @property
    def has_next(self) -> bool:
        return (self.page + 1) * self.page_size < self.total


@dataclass
class ResultPage:
    title: str
    results: list[SearchResult]
    page: int
    page_size: int
    total: int
    empty_message: str = "색인된 결과가 없습니다."

    @property
    def has_previous(self) -> bool:
        return self.page > 0

    @property
    def has_next(self) -> bool:
        return (self.page + 1) * self.page_size < self.total


def now_kst() -> datetime:
    return datetime.now(KST)


def now_iso() -> str:
    return now_kst().isoformat(timespec="seconds")


def parse_iso(raw: str) -> datetime:
    return datetime.fromisoformat(raw)


def validate_date(raw: str) -> str:
    try:
        return date.fromisoformat(raw.strip()).isoformat()
    except ValueError as exc:
        raise ValueError("날짜는 YYYY-MM-DD 형식으로 입력해주세요.") from exc


def source_lookup_key(text: str) -> str:
    return re.sub(r"\s+", "", normalize_text(text))


def initialize_service_schema(conn: sqlite3.Connection) -> None:
    initialize_schema(conn)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS search_sessions (
            session_id TEXT PRIMARY KEY,
            owner_user_id TEXT NOT NULL,
            guild_id TEXT NOT NULL,
            origin_channel_id TEXT NOT NULL,
            state_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_search_sessions_expires_at ON search_sessions(expires_at)"
    )
    conn.commit()


def date_bounds(state: SearchPanelState) -> tuple[str | None, str | None]:
    today = now_kst().date()
    if state.date_preset == "today":
        day = today.isoformat()
        return day, day
    if state.date_preset == "7d":
        return (today - timedelta(days=6)).isoformat(), today.isoformat()
    if state.date_preset == "30d":
        return (today - timedelta(days=29)).isoformat(), today.isoformat()
    if state.date_preset == "all":
        return None, None
    return state.start_date, state.end_date


def scope_source_ids(state: SearchPanelState) -> list[str]:
    if state.source_scope == "all_indexed":
        return []
    if state.source_scope == "current_channel":
        return [state.origin_channel_id]
    return list(dict.fromkeys(state.source_ids))


def make_filter_sql(
    state: SearchPanelState,
    *,
    date_column: str,
    source_column: str,
    source_parent_column: str | None = None,
    author_column: str | None = None,
) -> tuple[list[str], list[Any]]:
    filters: list[str] = []
    params: list[Any] = []
    start_date, end_date = date_bounds(state)
    if start_date:
        filters.append(f"{date_column} >= ?")
        params.append(start_date)
    if end_date:
        filters.append(f"{date_column} <= ?")
        params.append(end_date)
    source_ids = scope_source_ids(state)
    if source_ids:
        placeholders = ",".join("?" for _ in source_ids)
        if source_parent_column:
            filters.append(
                f"({source_column} IN ({placeholders}) OR {source_parent_column} IN ({placeholders}))"
            )
            params.extend(source_ids)
            params.extend(source_ids)
        else:
            filters.append(f"{source_column} IN ({placeholders})")
            params.extend(source_ids)
    if author_column and state.author_ids:
        filters.append(f"{author_column} IN ({','.join('?' for _ in state.author_ids)})")
        params.extend(state.author_ids)
    return filters, params


class MogIndexService:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path

    def connect(self) -> sqlite3.Connection:
        conn = connect(self.db_path)
        try:
            conn.execute("PRAGMA busy_timeout = 5000")
            initialize_service_schema(conn)
            return conn
        except Exception:
            conn.close()
            raise

    @contextmanager
    def open(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
        finally:
            conn.close()

    def create_session(self, interaction: Any) -> SearchPanelState:
        current = now_kst()
        state = SearchPanelState(
            session_id=uuid.uuid4().hex[:16],
            owner_user_id=str(interaction.user.id),
            guild_id=str(interaction.guild_id or ""),
            origin_channel_id=str(interaction.channel_id),
            created_at=current.isoformat(timespec="seconds"),
            updated_at=current.isoformat(timespec="seconds"),
            expires_at=(current + timedelta(minutes=SESSION_TTL_MINUTES)).isoformat(timespec="seconds"),
        )
        self.save_session(state)
        return state

    def _write_with_retry(self, label: str, callback: Any) -> Any:
        last_exc: sqlite3.OperationalError | None = None
        for attempt, delay in enumerate(LOCK_RETRY_DELAYS, start=1):
            if delay:
                time.sleep(delay)
            try:
                with self.open() as conn:
                    result = callback(conn)
                    conn.commit()
                    return result
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower():
                    raise
                last_exc = exc
                logger.warning(
                    "mogindex db locked during %s attempt=%s/%s",
                    label,
                    attempt,
                    len(LOCK_RETRY_DELAYS),
                )
        if last_exc:
            raise last_exc
        return None

    def save_session(self, state: SearchPanelState) -> None:
        state.updated_at = now_iso()

        def write(conn: sqlite3.Connection) -> None:
            conn.execute(
                """
                INSERT INTO search_sessions (
                    session_id, owner_user_id, guild_id, origin_channel_id,
                    state_json, created_at, updated_at, expires_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    state_json = excluded.state_json,
                    updated_at = excluded.updated_at,
                    expires_at = excluded.expires_at
                """,
                (
                    state.session_id,
                    state.owner_user_id,
                    state.guild_id,
                    state.origin_channel_id,
                    state.to_json(),
                    state.created_at,
                    state.updated_at,
                    state.expires_at,
                ),
            )

        self._write_with_retry("save_session", write)

    def load_session(self, session_id: str, user_id: str) -> SearchPanelState:
        with self.open() as conn:
            row = conn.execute(
                "SELECT state_json FROM search_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if not row:
            raise SearchSessionNotFound("검색 세션을 찾을 수 없습니다.")
        state = SearchPanelState.from_json(row["state_json"])
        if state.owner_user_id != str(user_id):
            raise SearchSessionForbidden("이 검색 패널은 실행한 사람만 사용할 수 있습니다.")
        if parse_iso(state.expires_at) < now_kst():
            raise SearchSessionExpired("검색 패널이 만료되었습니다. `/검색`으로 다시 열어주세요.")
        return state

    def close_session(self, state: SearchPanelState) -> None:
        def write(conn: sqlite3.Connection) -> None:
            conn.execute("DELETE FROM search_sessions WHERE session_id = ?", (state.session_id,))

        self._write_with_retry("close_session", write)

    def reset_filters(self, state: SearchPanelState) -> SearchPanelState:
        state.mode = "hub"
        state.visibility = "private"
        state.query = None
        state.start_date = None
        state.end_date = None
        state.date_preset = "30d"
        state.source_scope = "all_indexed"
        state.source_ids = []
        state.source_selector = None
        state.author_ids = []
        state.sort = "relevance"
        state.page = 0
        state.last_result_kind = None
        state.last_result_ids = []
        return state

    def set_date_preset(self, state: SearchPanelState, preset: DatePreset) -> SearchPanelState:
        state.date_preset = preset
        if preset != "custom":
            state.start_date = None
            state.end_date = None
        state.page = 0
        return state

    def set_custom_dates(self, state: SearchPanelState, start_date: str, end_date: str) -> SearchPanelState:
        start = validate_date(start_date)
        end = validate_date(end_date)
        if start > end:
            raise ValueError("시작일은 종료일보다 늦을 수 없습니다.")
        state.date_preset = "custom"
        state.start_date = start
        state.end_date = end
        state.page = 0
        return state

    def set_scope(self, state: SearchPanelState, scope: SourceScope, source_ids: Iterable[str] = ()) -> SearchPanelState:
        state.source_scope = scope
        state.source_ids = list(dict.fromkeys(str(source_id) for source_id in source_ids if source_id))
        if scope != "selected_sources":
            state.source_selector = None
        state.page = 0
        logger.info(
            "mogindex scope updated session=%s scope=%s source_ids=%s",
            state.session_id,
            state.source_scope,
            state.source_ids,
        )
        return state

    def count_matching_sources(self, state: SearchPanelState) -> int:
        source_ids = scope_source_ids(state)
        if not source_ids:
            return 0
        placeholders = ",".join("?" for _ in source_ids)
        with self.open() as conn:
            row = conn.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM sources
                WHERE source_id IN ({placeholders}) OR parent_channel_id IN ({placeholders})
                """,
                tuple(source_ids) + tuple(source_ids),
            ).fetchone()
        return int(row["count"] if row else 0)

    def find_sources_by_name(self, query: str, *, limit: int = 25) -> list[SourceMatch]:
        needle = source_lookup_key(query)
        if not needle:
            return []
        with self.open() as conn:
            rows = conn.execute(
                """
                SELECT source_id, source_kind, parent_channel_id, name
                FROM sources
                WHERE active = 1
                ORDER BY
                    CASE source_kind WHEN 'channel' THEN 0 ELSE 1 END,
                    updated_at DESC,
                    name
                """
            ).fetchall()
        matches: list[SourceMatch] = []
        for row in rows:
            if needle not in source_lookup_key(row["name"]):
                continue
            matches.append(
                SourceMatch(
                    source_id=row["source_id"],
                    source_kind=row["source_kind"],
                    parent_channel_id=row["parent_channel_id"],
                    name=row["name"],
                )
            )
            if len(matches) >= limit:
                break
        return matches

    def _keyword_rankings(self, conn: sqlite3.Connection, query: str) -> Counter[int]:
        normalized = normalize_text(query)
        query_terms = extract_terms(query)
        ranked: Counter[int] = Counter()
        if len(normalized.replace(" ", "")) >= 3:
            for row in conn.execute(
                """
                SELECT rowid AS message_pk
                FROM message_fts
                WHERE message_fts MATCH ?
                LIMIT 500
                """,
                (normalized,),
            ):
                ranked[int(row["message_pk"])] += 10

        if query_terms:
            placeholders = ",".join("?" for _ in query_terms)
            for row in conn.execute(
                f"""
                SELECT message_pk, SUM(count) AS score
                FROM message_terms
                WHERE term IN ({placeholders})
                GROUP BY message_pk
                LIMIT 1000
                """,
                tuple(query_terms.keys()),
            ):
                ranked[int(row["message_pk"])] += int(row["score"] or 0)
        return ranked

    def run_keyword_search(self, state: SearchPanelState) -> ResultPage:
        query = (state.query or "").strip()
        if not query:
            state.last_result_kind = "keyword"
            state.last_result_ids = []
            logger.info(
                "mogindex keyword session=%s query=%r scope=%s sources=%s date=%s..%s total=0 reason=empty_query",
                state.session_id,
                query,
                state.source_scope,
                state.source_ids,
                date_bounds(state)[0],
                date_bounds(state)[1],
            )
            return ResultPage("단어 검색", [], state.page, state.page_size, 0, "검색어를 입력해주세요.")

        with self.open() as conn:
            ranked = self._keyword_rankings(conn, query)
            if not ranked:
                state.last_result_kind = "keyword"
                state.last_result_ids = []
                logger.info(
                    "mogindex keyword session=%s query=%r scope=%s sources=%s date=%s..%s total=0 reason=no_ranked_terms",
                    state.session_id,
                    query,
                    state.source_scope,
                    state.source_ids,
                    date_bounds(state)[0],
                    date_bounds(state)[1],
                )
                return ResultPage("단어 검색", [], state.page, state.page_size, 0, "현재 기간/범위에서 색인된 결과가 없습니다. 기간을 전체로 넓히거나 직접 기간을 지정해보세요.")

            filters, params = make_filter_sql(
                state,
                date_column="m.message_date",
                source_column="m.source_id",
                source_parent_column="s.parent_channel_id",
                author_column="m.author_id",
            )
            placeholders = ",".join("?" for _ in ranked)
            where_sql = " AND ".join(["m.message_pk IN (" + placeholders + ")"] + filters)
            rows = conn.execute(
                f"""
                SELECT
                    m.message_pk, m.source_id, s.name AS source_name,
                    m.author_id, m.author_name, m.message_date, m.created_at, m.jump_url
                FROM messages m
                JOIN sources s ON s.source_id = m.source_id
                WHERE {where_sql}
                """,
                tuple(ranked.keys()) + tuple(params),
            ).fetchall()

        results = [
            SearchResult(
                message_pk=int(row["message_pk"]),
                source_id=row["source_id"],
                source_name=row["source_name"],
                author_id=row["author_id"],
                author_name=row["author_name"],
                message_date=row["message_date"],
                created_at=row["created_at"],
                jump_url=row["jump_url"],
                score=ranked[int(row["message_pk"])],
            )
            for row in rows
        ]
        if state.sort == "newest":
            results.sort(key=lambda item: item.created_at, reverse=True)
        elif state.sort == "oldest":
            results.sort(key=lambda item: item.created_at)
        else:
            results.sort(key=lambda item: (-item.score, item.created_at))

        state.last_result_kind = "keyword"
        state.last_result_ids = [item.message_pk for item in results]
        page = self._slice_results("단어 검색", results, state)
        logger.info(
            "mogindex keyword session=%s query=%r scope=%s sources=%s date=%s..%s total=%s page=%s",
            state.session_id,
            query,
            state.source_scope,
            state.source_ids,
            date_bounds(state)[0],
            date_bounds(state)[1],
            page.total,
            page.page,
        )
        return page

    def _slice_results(self, title: str, results: list[SearchResult], state: SearchPanelState) -> ResultPage:
        total = len(results)
        max_page = max((total - 1) // state.page_size, 0)
        state.page = min(max(state.page, 0), max_page)
        start = state.page * state.page_size
        end = start + state.page_size
        return ResultPage(title, results[start:end], state.page, state.page_size, total)

    def run_recap(self, state: SearchPanelState) -> TextPage:
        with self.open() as conn:
            topic_filters, topic_params = make_filter_sql(
                state,
                date_column="mt.topic_date",
                source_column="mt.source_id",
                source_parent_column="s.parent_channel_id",
            )
            term_filters, term_params = make_filter_sql(
                state,
                date_column="d.message_date",
                source_column="d.source_id",
                source_parent_column="s.parent_channel_id",
            )
            topics = conn.execute(
                f"""
                SELECT mt.topic_date, mt.source_id, COALESCE(s.name, mt.source_id) AS source_name,
                       mt.title, mt.jump_url
                FROM manual_topics mt
                LEFT JOIN sources s ON s.source_id = mt.source_id
                WHERE {" AND ".join(topic_filters) if topic_filters else "1 = 1"}
                ORDER BY mt.topic_date DESC, mt.topic_id DESC
                LIMIT 50
                """,
                tuple(topic_params),
            ).fetchall()
            keywords = conn.execute(
                f"""
                SELECT d.message_date, d.source_id, s.name AS source_name, d.term, d.count
                FROM daily_terms d
                JOIN sources s ON s.source_id = d.source_id
                WHERE {" AND ".join(term_filters) if term_filters else "1 = 1"}
                  AND LENGTH(d.term) BETWEEN 2 AND 8
                  AND d.term NOT IN ({",".join("?" for _ in STOP_TERMS)})
                ORDER BY d.message_date DESC, d.source_id, d.count DESC, d.term
                LIMIT 300
                """,
                tuple(term_params) + tuple(STOP_TERMS),
            ).fetchall()

        lines: list[str] = []
        for row in topics[:20]:
            lines.append(f"[{row['topic_date']}] {row['title']} -> {row['jump_url']}")

        grouped: dict[tuple[str, str], list[Any]] = defaultdict(list)
        for row in keywords:
            key = (row["message_date"], row["source_id"])
            if len(grouped[key]) < 5:
                grouped[key].append(row)

        for (message_date, _source_id), rows in grouped.items():
            terms = ", ".join(f"{row['term']}({row['count']})" for row in rows)
            lines.append(f"[{message_date}] {rows[0]['source_name']}: {terms}")

        state.last_result_kind = "recap"
        state.last_result_ids = []
        page = self._slice_lines("날짜 요약", lines, state, "이 기간에 요약할 색인 활동이 없습니다.")
        logger.info(
            "mogindex recap session=%s scope=%s sources=%s date=%s..%s total=%s page=%s",
            state.session_id,
            state.source_scope,
            state.source_ids,
            date_bounds(state)[0],
            date_bounds(state)[1],
            page.total,
            page.page,
        )
        return page

    def run_topic_search(self, state: SearchPanelState) -> TextPage:
        query = normalize_text(state.query or "")
        query_terms = set(extract_terms(query).keys()) if query else set()
        with self.open() as conn:
            filters, params = make_filter_sql(
                state,
                date_column="mt.topic_date",
                source_column="mt.source_id",
                source_parent_column="s.parent_channel_id",
            )
            rows = conn.execute(
                f"""
                SELECT mt.topic_id, mt.topic_date, mt.source_id,
                       COALESCE(s.name, mt.source_id) AS source_name,
                       mt.title, mt.jump_url
                FROM manual_topics mt
                LEFT JOIN sources s ON s.source_id = mt.source_id
                WHERE {" AND ".join(filters) if filters else "1 = 1"}
                ORDER BY mt.topic_date DESC, mt.topic_id DESC
                LIMIT 300
                """,
                tuple(params),
            ).fetchall()

        topics: list[TopicResult] = []
        for row in rows:
            normalized_title = normalize_text(row["title"])
            if query:
                title_terms = set(extract_terms(row["title"]).keys())
                if query not in normalized_title and not (query_terms & title_terms):
                    continue
            topics.append(
                TopicResult(
                    topic_id=int(row["topic_id"]),
                    topic_date=row["topic_date"],
                    source_id=row["source_id"],
                    source_name=row["source_name"],
                    title=row["title"],
                    jump_url=row["jump_url"],
                )
            )

        lines = [
            f"[{topic.topic_date}] {topic.title} ({topic.source_name}) -> {topic.jump_url}"
            for topic in topics
        ]
        state.last_result_kind = "topic"
        state.last_result_ids = [topic.topic_id for topic in topics]
        page = self._slice_lines("토픽 검색", lines, state, "조건에 맞는 수동 토픽이 없습니다.")
        logger.info(
            "mogindex topic session=%s query=%r scope=%s sources=%s date=%s..%s total=%s page=%s",
            state.session_id,
            state.query,
            state.source_scope,
            state.source_ids,
            date_bounds(state)[0],
            date_bounds(state)[1],
            page.total,
            page.page,
        )
        return page

    def run_recent(self, state: SearchPanelState) -> ResultPage:
        with self.open() as conn:
            filters, params = make_filter_sql(
                state,
                date_column="m.message_date",
                source_column="m.source_id",
                source_parent_column="s.parent_channel_id",
                author_column="m.author_id",
            )
            order_sql = "m.created_at ASC" if state.sort == "oldest" else "m.created_at DESC"
            rows = conn.execute(
                f"""
                SELECT
                    m.message_pk, m.source_id, s.name AS source_name,
                    m.author_id, m.author_name, m.message_date, m.created_at, m.jump_url
                FROM messages m
                JOIN sources s ON s.source_id = m.source_id
                WHERE {" AND ".join(filters) if filters else "1 = 1"}
                ORDER BY {order_sql}
                LIMIT 300
                """,
                tuple(params),
            ).fetchall()

        results = [
            SearchResult(
                message_pk=int(row["message_pk"]),
                source_id=row["source_id"],
                source_name=row["source_name"],
                author_id=row["author_id"],
                author_name=row["author_name"],
                message_date=row["message_date"],
                created_at=row["created_at"],
                jump_url=row["jump_url"],
                score=0,
            )
            for row in rows
        ]
        state.last_result_kind = "recent"
        state.last_result_ids = [item.message_pk for item in results]
        page = self._slice_results("채널 보기", results, state)
        page.empty_message = "현재 기간/범위에 표시할 색인 메시지가 없습니다. 기간을 전체로 넓히거나 다른 채널을 선택해보세요."
        logger.info(
            "mogindex recent session=%s scope=%s sources=%s date=%s..%s total=%s page=%s",
            state.session_id,
            state.source_scope,
            state.source_ids,
            date_bounds(state)[0],
            date_bounds(state)[1],
            page.total,
            page.page,
        )
        return page

    def run_participants(self, state: SearchPanelState) -> TextPage:
        with self.open() as conn:
            filters, params = make_filter_sql(
                state,
                date_column="m.message_date",
                source_column="m.source_id",
                source_parent_column="s.parent_channel_id",
                author_column="m.author_id",
            )
            rows = conn.execute(
                f"""
                SELECT m.message_date, m.source_id, s.name AS source_name,
                       m.author_id, m.author_name, COUNT(*) AS message_count
                FROM messages m
                JOIN sources s ON s.source_id = m.source_id
                WHERE {" AND ".join(filters) if filters else "1 = 1"}
                GROUP BY m.message_date, m.source_id, m.author_id
                ORDER BY m.message_date DESC, s.name, message_count DESC, m.author_name
                LIMIT 300
                """,
                tuple(params),
            ).fetchall()

        participants = [
            ParticipantsResult(
                message_date=row["message_date"],
                source_id=row["source_id"],
                source_name=row["source_name"],
                author_id=row["author_id"],
                author_name=row["author_name"],
                message_count=int(row["message_count"]),
            )
            for row in rows
        ]
        lines = [
            f"[{item.message_date}] {item.source_name} / {item.author_name}: {item.message_count} messages"
            for item in participants
        ]
        state.last_result_kind = "participants"
        state.last_result_ids = []
        page = self._slice_lines("참여자 보기", lines, state, "이 기간에 참여자 기록이 없습니다.")
        logger.info(
            "mogindex participants session=%s scope=%s sources=%s date=%s..%s total=%s page=%s",
            state.session_id,
            state.source_scope,
            state.source_ids,
            date_bounds(state)[0],
            date_bounds(state)[1],
            page.total,
            page.page,
        )
        return page

    def _slice_lines(
        self,
        title: str,
        lines: list[str],
        state: SearchPanelState,
        empty_message: str,
    ) -> TextPage:
        total = len(lines)
        max_page = max((total - 1) // state.page_size, 0)
        state.page = min(max(state.page, 0), max_page)
        start = state.page * state.page_size
        end = start + state.page_size
        return TextPage(title, lines[start:end], state.page, state.page_size, total, empty_message)

