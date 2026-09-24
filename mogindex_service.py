from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
import uuid
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Iterator, Literal
from zoneinfo import ZoneInfo

from mogindex_debug import (
    INDEX_EXCLUDED_TERMS,
    connect,
    derive_query_terms,
    initialize_schema,
    normalize_text,
)


MODULE_DIR = Path(__file__).resolve().parent


def env_db_path(name: str, default: str) -> Path:
    raw = os.getenv(name, default)
    db_path = Path(raw)
    if not db_path.is_absolute():
        db_path = MODULE_DIR / db_path
    return db_path


DEFAULT_DB_PATH = env_db_path("MOGINDEX_DB_PATH", "mogindex/search_index_live_debug.sqlite3")
DEFAULT_STATE_DB_PATH = env_db_path("MOGINDEX_STATE_DB_PATH", "mogindex/search_state.sqlite3")
SESSION_TTL_MINUTES = 30
KST = ZoneInfo("Asia/Seoul")
logger = logging.getLogger(__name__)
LOCK_RETRY_DELAYS = (0.0, 0.25, 0.5, 1.0, 2.0)
# 읽기 연결 튜닝. 검색 쿼리마다 연결을 새로 열면 SQLite 페이지 캐시(기본 2MB)가 매번 비워지므로
# 스레드별로 연결 하나를 유지하고 캐시/mmap 을 키운다. 단위: cache 는 KiB, mmap 은 MiB.
READ_CACHE_KIB = int(os.getenv("MOGINDEX_READ_CACHE_KIB", "65536"))
READ_MMAP_MIB = int(os.getenv("MOGINDEX_READ_MMAP_MIB", "256"))
# 복귀자 키워드: 집계 비용은 기간 길이에 비례한다 (실측: 전 구간 1.3s, 1년 0.37s, 90일 0.03s).
# 활동 기록이 없거나 아주 오래 비운 사용자는 최근 N일로 제한하고, 집계 결과는 잠시 캐시해
# 페이지 넘기기·제외어 추가가 재집계 없이 즉시 처리되게 한다.
RETURNEE_MAX_DAYS = int(os.getenv("MOGINDEX_RETURNEE_MAX_DAYS", "180"))
RETURNEE_CACHE_TTL_SEC = int(os.getenv("MOGINDEX_RETURNEE_CACHE_TTL_SEC", "600"))
RETURNEE_FETCH_LIMIT = 500
RETURNEE_PICK_OPTIONS = 25      # 디스코드 Select 옵션 상한
STATE_TABLES = ("search_sessions", "full_index_runs", "full_index_days", "full_index_source_days")

Mode = Literal["hub", "keyword", "recap", "topic", "participants", "recent", "returnee"]
Visibility = Literal["private", "shared"]
DatePreset = Literal["today", "7d", "30d", "custom", "all"]
SourceScope = Literal[
    "all_indexed",
    "current_category",
    "current_channel",
    "selected_categories",
    "selected_sources",
    "result_set",
]
SortMode = Literal["relevance", "newest", "oldest"]
PanelKind = Literal["main", "detail"]
MAX_RESULT_ID_CACHE = 1000
RETURNEE_DEFAULT_START_DATE = "2024-06-05"


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
    origin_category_id: str | None = None
    origin_parent_channel_id: str | None = None
    panel_kind: PanelKind = "main"
    parent_session_id: str | None = None
    mode: Mode = "hub"
    visibility: Visibility = "private"
    query: str | None = None
    keyword_all: str | None = None
    keyword_any: str | None = None
    keyword_not: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    date_preset: DatePreset = "30d"
    source_scope: SourceScope = "all_indexed"
    category_ids: list[str] = field(default_factory=list)
    source_ids: list[str] = field(default_factory=list)
    source_selector: str | None = None
    author_ids: list[str] = field(default_factory=list)
    sort: SortMode = "relevance"
    page: int = 0
    page_size: int = 5
    last_result_kind: str | None = None
    last_result_ids: list[int] = field(default_factory=list)
    base_result_ids: list[int] = field(default_factory=list)
    returnee_limit: int = 5
    worldmap_category_ids: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    expires_at: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> "SearchPanelState":
        data = json.loads(raw)
        allowed = set(cls.__dataclass_fields__)
        return cls(**{key: value for key, value in data.items() if key in allowed})


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
    snippet: str = ""          # 본문 미리보기 (마크다운 없음, 한 줄). 페이지에 실린 결과에만 채운다.


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
    category_id: str | None
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
    options: list[str] = field(default_factory=list)   # 복귀자 키워드: 바로 검색할 수 있는 단어 목록 (Select 용)

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
    return re.sub(r"[\s\-_]+", "", normalize_text(text))


def ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def initialize_state_schema(conn: sqlite3.Connection) -> None:
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
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS full_index_runs (
            run_id TEXT PRIMARY KEY,
            guild_id TEXT NOT NULL,
            requested_by TEXT NOT NULL,
            status TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            current_date TEXT,
            last_completed_date TEXT,
            notify_channel_id TEXT,
            notify_user_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            finished_at TEXT
        )
        """
    )
    ensure_column(conn, "full_index_runs", "category_key", "category_key TEXT")
    ensure_column(conn, "full_index_runs", "category_id", "category_id TEXT")
    ensure_column(conn, "full_index_runs", "category_name", "category_name TEXT")
    ensure_column(conn, "full_index_runs", "target_config_json", "target_config_json TEXT")
    ensure_column(conn, "full_index_runs", "run_kind", "run_kind TEXT")
    ensure_column(conn, "full_index_runs", "schedule_key", "schedule_key TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_full_index_runs_status_created "
        "ON full_index_runs(status, created_at DESC)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS full_index_days (
            run_id TEXT NOT NULL,
            index_date TEXT NOT NULL,
            status TEXT NOT NULL,
            targets INTEGER NOT NULL DEFAULT 0,
            scanned INTEGER NOT NULL DEFAULT 0,
            indexed INTEGER NOT NULL DEFAULT 0,
            elapsed_seconds REAL NOT NULL DEFAULT 0,
            error_text TEXT,
            started_at TEXT,
            finished_at TEXT,
            PRIMARY KEY(run_id, index_date)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_full_index_days_status "
        "ON full_index_days(run_id, status, index_date DESC)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS full_index_source_days (
            run_id TEXT NOT NULL,
            index_date TEXT NOT NULL,
            source_id TEXT NOT NULL,
            status TEXT NOT NULL,
            category_key TEXT,
            source_name TEXT,
            scanned INTEGER NOT NULL DEFAULT 0,
            indexed INTEGER NOT NULL DEFAULT 0,
            elapsed_seconds REAL NOT NULL DEFAULT 0,
            error_text TEXT,
            started_at TEXT,
            finished_at TEXT,
            PRIMARY KEY(run_id, index_date, source_id)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_full_index_source_days_status "
        "ON full_index_source_days(run_id, index_date, status)"
    )
    conn.commit()


def initialize_service_schema(conn: sqlite3.Connection) -> None:
    initialize_schema(conn)
    initialize_state_schema(conn)


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
    if state.source_scope in ("all_indexed", "current_category", "selected_categories", "result_set"):
        return []
    if state.source_scope == "current_channel":
        return [state.origin_parent_channel_id or state.origin_channel_id]
    return list(dict.fromkeys(state.source_ids))


def scope_category_ids(state: SearchPanelState) -> list[str]:
    if state.source_scope == "current_category" and state.origin_category_id:
        return [state.origin_category_id]
    if state.source_scope == "selected_categories":
        return list(dict.fromkeys(state.category_ids))
    return []


def cached_result_ids(ids: Iterable[int]) -> list[int]:
    return list(dict.fromkeys(int(item) for item in ids))[:MAX_RESULT_ID_CACHE]


def split_query_parts(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [part for part in re.split(r"[,\s]+", raw.strip()) if part.strip()]


SNIPPET_WIDTH = 90


def make_snippet(content: str | None, terms: Iterable[str], width: int = SNIPPET_WIDTH) -> str:
    """본문에서 검색어가 처음 나오는 곳 주변을 한 줄로 잘라 낸다. 검색어가 없으면 앞부분.

    마크다운 처리는 하지 않는다 (표시 쪽에서 이스케이프하고 검색어를 굵게 만든다).
    """
    text = re.sub(r"\s+", " ", str(content or "")).strip()
    if not text:
        return ""
    lowered = text.lower()
    hit = -1
    for term in terms:
        term = str(term or "").strip().lower()
        if not term:
            continue
        pos = lowered.find(term)
        if pos != -1 and (hit == -1 or pos < hit):
            hit = pos
    if len(text) <= width:
        return text
    if hit == -1:
        return text[:width].rstrip() + "…"
    start = max(0, hit - width // 3)
    end = min(len(text), start + width)
    start = max(0, end - width)
    piece = text[start:end].strip()
    return ("…" if start > 0 else "") + piece + ("…" if end < len(text) else "")


def make_filter_sql(
    state: SearchPanelState,
    *,
    date_column: str,
    source_column: str,
    source_parent_column: str | None = None,
    author_column: str | None = None,
    category_column: str | None = "s.category_id",
    result_column: str | None = None,
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
    category_ids = scope_category_ids(state)
    if category_column and category_ids:
        filters.append(f"{category_column} IN ({','.join('?' for _ in category_ids)})")
        params.extend(category_ids)
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
    if result_column and state.source_scope == "result_set" and state.base_result_ids:
        result_ids = cached_result_ids(state.base_result_ids)
        filters.append(f"{result_column} IN ({','.join('?' for _ in result_ids)})")
        params.extend(result_ids)
    return filters, params


class MogIndexService:
    def __init__(
        self,
        db_path: Path = DEFAULT_DB_PATH,
        *,
        state_db_path: Path = DEFAULT_STATE_DB_PATH,
    ):
        self.db_path = db_path
        self.index_db_path = db_path
        self.state_db_path = state_db_path
        self._schema_lock = threading.Lock()
        self._index_schema_ready = False
        self._state_schema_ready = False
        self._legacy_state_copied = False
        self._read_local = threading.local()
        self._returnee_cache: dict[tuple, tuple[float, list[tuple[str, int]]]] = {}
        self._returnee_cache_lock = threading.Lock()

    def index_connect(self) -> sqlite3.Connection:
        return connect(self.index_db_path)

    @staticmethod
    def _file_identity(path: Path) -> tuple[int, int] | None:
        try:
            st = os.stat(path)
        except OSError:
            return None
        return (st.st_dev, st.st_ino)

    def _read_connection(self) -> sqlite3.Connection:
        """현재 스레드의 색인 읽기 연결. 없거나 DB 파일이 교체되었으면 새로 연다.

        index_open() 경로는 SELECT 만 실행하므로 query_only 로 잠근다.
        열린 트랜잭션을 들고 있지 않아 배치의 WAL 체크포인트를 막지 않는다.
        """
        identity = self._file_identity(self.index_db_path)
        conn = getattr(self._read_local, "conn", None)
        if conn is not None and getattr(self._read_local, "identity", None) == identity:
            return conn
        self.close_read_connection()
        conn = self.index_connect()
        conn.execute(f"PRAGMA cache_size = -{max(READ_CACHE_KIB, 2048)}")
        conn.execute(f"PRAGMA mmap_size = {max(READ_MMAP_MIB, 0) * 1024 * 1024}")
        conn.execute("PRAGMA temp_store = MEMORY")
        conn.execute("PRAGMA query_only = ON")
        self._read_local.conn = conn
        self._read_local.identity = identity
        logger.info(
            "mogindex read connection opened thread=%s cache_kib=%s mmap_mib=%s",
            threading.current_thread().name,
            READ_CACHE_KIB,
            READ_MMAP_MIB,
        )
        return conn

    def close_read_connection(self) -> None:
        """현재 스레드가 들고 있는 읽기 연결을 닫는다 (오류 후 재연결, 종료 시)."""
        conn = getattr(self._read_local, "conn", None)
        self._read_local.conn = None
        self._read_local.identity = None
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass

    def state_connect(self) -> sqlite3.Connection:
        return connect(self.state_db_path)

    def connect(self) -> sqlite3.Connection:
        return self.index_connect()

    def initialize(self) -> None:
        self.ensure_index_schema()
        self.ensure_state_schema()

    def ensure_index_schema(self) -> None:
        if self._index_schema_ready:
            return
        with self._schema_lock:
            if self._index_schema_ready:
                return
            conn = self.index_connect()
            try:
                initialize_schema(conn)
            finally:
                conn.close()
            self._index_schema_ready = True

    def ensure_state_schema(self) -> None:
        if self._state_schema_ready:
            return
        with self._schema_lock:
            if self._state_schema_ready:
                return
            conn = self.state_connect()
            try:
                initialize_state_schema(conn)
                self.copy_legacy_state_if_needed(conn)
            finally:
                conn.close()
            self._state_schema_ready = True

    def copy_legacy_state_if_needed(self, state_conn: sqlite3.Connection) -> None:
        if self._legacy_state_copied or self.index_db_path == self.state_db_path:
            return
        if any(self.table_row_count(state_conn, table) for table in STATE_TABLES):
            self._legacy_state_copied = True
            return
        legacy_conn = self.index_connect()
        try:
            copied = 0
            for table in STATE_TABLES:
                if not self.table_exists(legacy_conn, table):
                    continue
                state_columns = self.table_columns(state_conn, table)
                legacy_columns = self.table_columns(legacy_conn, table)
                columns = [column for column in state_columns if column in legacy_columns]
                if not columns:
                    continue
                rows = legacy_conn.execute(
                    f"SELECT {', '.join(columns)} FROM {table}"
                ).fetchall()
                if not rows:
                    continue
                placeholders = ", ".join("?" for _ in columns)
                state_conn.executemany(
                    f"INSERT OR IGNORE INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                    ([row[column] for column in columns] for row in rows),
                )
                copied += len(rows)
            state_conn.commit()
            if copied:
                logger.info(
                    "mogindex copied legacy state rows=%s from=%s to=%s",
                    copied,
                    self.index_db_path,
                    self.state_db_path,
                )
        finally:
            legacy_conn.close()
            self._legacy_state_copied = True

    def table_exists(self, conn: sqlite3.Connection, table: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        return row is not None

    def table_columns(self, conn: sqlite3.Connection, table: str) -> list[str]:
        return [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]

    def table_row_count(self, conn: sqlite3.Connection, table: str) -> int:
        if not self.table_exists(conn, table):
            return 0
        row = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
        return int(row["count"] if row else 0)

    @contextmanager
    def index_open(self) -> Iterator[sqlite3.Connection]:
        """읽기 전용 색인 연결. 스레드별로 재사용해 페이지 캐시를 유지한다. 쓰기는 index_connect() 로."""
        self.ensure_index_schema()
        conn = self._read_connection()
        try:
            yield conn
        except sqlite3.Error:
            # 손상/교체 등 연결 자체가 문제일 수 있으니 버리고 다음 호출에서 새로 연다
            self.close_read_connection()
            raise

    @contextmanager
    def state_open(self) -> Iterator[sqlite3.Connection]:
        self.ensure_state_schema()
        conn = self.state_connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def open(self) -> Iterator[sqlite3.Connection]:
        with self.index_open() as conn:
            yield conn

    def create_session(self, interaction: Any, *, persist: bool = True) -> SearchPanelState:
        current = now_kst()
        channel = getattr(interaction, "channel", None)
        channel_type = type(channel).__name__.lower() if channel is not None else ""
        is_thread = "thread" in channel_type
        origin_category_id = getattr(channel, "category_id", None)
        origin_parent_channel_id = None
        if is_thread:
            origin_parent_channel_id = getattr(channel, "parent_id", None)
            parent = getattr(channel, "parent", None)
            origin_category_id = origin_category_id or getattr(parent, "category_id", None)
        state = SearchPanelState(
            session_id=uuid.uuid4().hex[:16],
            owner_user_id=str(interaction.user.id),
            guild_id=str(interaction.guild_id or ""),
            origin_channel_id=str(interaction.channel_id),
            origin_category_id=str(origin_category_id) if origin_category_id else None,
            origin_parent_channel_id=str(origin_parent_channel_id) if origin_parent_channel_id else None,
            created_at=current.isoformat(timespec="seconds"),
            updated_at=current.isoformat(timespec="seconds"),
            expires_at=(current + timedelta(minutes=SESSION_TTL_MINUTES)).isoformat(timespec="seconds"),
        )
        if persist:
            self.save_session(state)
        return state

    def _write_with_retry(self, label: str, callback: Any) -> Any:
        last_exc: sqlite3.OperationalError | None = None
        for attempt, delay in enumerate(LOCK_RETRY_DELAYS, start=1):
            if delay:
                time.sleep(delay)
            try:
                with self.state_open() as conn:
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
        with self.state_open() as conn:
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
        state.keyword_all = None
        state.keyword_any = None
        state.keyword_not = None
        state.start_date = None
        state.end_date = None
        state.date_preset = "30d"
        state.source_scope = "all_indexed"
        state.category_ids = []
        state.source_ids = []
        state.source_selector = None
        state.author_ids = []
        state.sort = "relevance"
        state.page = 0
        state.last_result_kind = None
        state.last_result_ids = []
        state.base_result_ids = []
        state.parent_session_id = None
        state.panel_kind = "main"
        state.returnee_limit = 5
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

    def set_scope(
        self,
        state: SearchPanelState,
        scope: SourceScope,
        source_ids: Iterable[str] = (),
        *,
        category_ids: Iterable[str] = (),
        base_result_ids: Iterable[int] = (),
    ) -> SearchPanelState:
        state.source_scope = scope
        state.source_ids = list(dict.fromkeys(str(source_id) for source_id in source_ids if source_id))
        state.category_ids = list(dict.fromkeys(str(category_id) for category_id in category_ids if category_id))
        if scope == "result_set":
            state.base_result_ids = cached_result_ids(base_result_ids or state.last_result_ids)
        elif scope != "result_set" and state.panel_kind != "detail":
            state.base_result_ids = []
        if scope != "selected_sources":
            state.source_selector = None
        state.page = 0
        logger.info(
            "mogindex scope updated session=%s scope=%s category_ids=%s source_ids=%s base_results=%s",
            state.session_id,
            state.source_scope,
            state.category_ids,
            state.source_ids,
            len(state.base_result_ids),
        )
        return state

    def set_keywords(
        self,
        state: SearchPanelState,
        *,
        all_terms: str | None = None,
        any_terms: str | None = None,
        not_terms: str | None = None,
    ) -> SearchPanelState:
        state.keyword_all = (all_terms or "").strip() or None
        state.keyword_any = (any_terms or "").strip() or None
        state.keyword_not = (not_terms or "").strip() or None
        state.query = " ".join(part for part in (state.keyword_all, state.keyword_any) if part) or None
        state.mode = "keyword"
        state.page = 0
        return state

    def add_not_terms(self, state: SearchPanelState, raw: str | None) -> SearchPanelState:
        """제외 키워드를 누적한다 — 결과 안 검색처럼 여러 번 눌러 목록을 계속 좁혀 갈 수 있다.

        모드는 바꾸지 않는다(복귀자 키워드 화면에서 쓰면 그 화면에 그대로 적용). 빈 입력이면 제외 목록을 비운다.
        """
        new_parts = split_query_parts(raw)
        if not new_parts:
            state.keyword_not = None
        else:
            merged = list(dict.fromkeys(split_query_parts(state.keyword_not) + new_parts))
            state.keyword_not = " ".join(merged)
        state.page = 0
        return state

    def create_detail_session(self, parent: SearchPanelState, *, persist: bool = True) -> SearchPanelState:
        current = now_kst()
        state = SearchPanelState.from_json(parent.to_json())
        state.session_id = uuid.uuid4().hex[:16]
        state.parent_session_id = parent.session_id
        state.panel_kind = "detail"
        state.source_scope = "result_set"
        state.base_result_ids = cached_result_ids(parent.last_result_ids)
        state.last_result_ids = []
        state.last_result_kind = None
        state.page = 0
        state.created_at = current.isoformat(timespec="seconds")
        state.updated_at = current.isoformat(timespec="seconds")
        state.expires_at = (current + timedelta(minutes=SESSION_TTL_MINUTES)).isoformat(timespec="seconds")
        if persist:
            self.save_session(state)
        return state

    def count_matching_sources(self, state: SearchPanelState) -> int:
        source_ids = scope_source_ids(state)
        category_ids = scope_category_ids(state)
        filters: list[str] = ["active = 1"]
        params: list[Any] = []
        if source_ids:
            placeholders = ",".join("?" for _ in source_ids)
            filters.append(f"(source_id IN ({placeholders}) OR parent_channel_id IN ({placeholders}))")
            params.extend(source_ids)
            params.extend(source_ids)
        if category_ids:
            filters.append(f"category_id IN ({','.join('?' for _ in category_ids)})")
            params.extend(category_ids)
        if not source_ids and not category_ids:
            return 0
        with self.index_open() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS count FROM sources WHERE {' AND '.join(filters)}",
                tuple(params),
            ).fetchone()
        return int(row["count"] if row else 0)

    def find_sources_by_name(self, query: str, *, limit: int = 25) -> list[SourceMatch]:
        needle = source_lookup_key(query)
        if not needle:
            return []
        with self.index_open() as conn:
            rows = conn.execute(
                """
                SELECT source_id, source_kind, category_id, parent_channel_id, name
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
                    category_id=row["category_id"],
                    parent_channel_id=row["parent_channel_id"],
                    name=row["name"],
                )
            )
            if len(matches) >= limit:
                break
        return matches

    def _keyword_rankings(self, conn: sqlite3.Connection, query: str) -> Counter[int]:
        # 색인은 Kiwi 형태소(명사 중심)지만 질의 경로는 Kiwi를 쓰지 않는다:
        # 정확 일치 → 접두 일치 → 트라이그램 FTS 순으로 매칭한다.
        normalized = normalize_text(query)
        query_terms = derive_query_terms(query)
        ranked: Counter[int] = Counter()
        if not query_terms:
            return ranked
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

        placeholders = ",".join("?" for _ in query_terms)
        exact_terms = {
            row["term"]
            for row in conn.execute(
                f"SELECT DISTINCT term FROM message_terms WHERE term IN ({placeholders})",
                tuple(query_terms),
            )
        }
        for row in conn.execute(
            f"""
            SELECT message_pk, SUM(count) AS score
            FROM message_terms
            WHERE term IN ({placeholders})
            GROUP BY message_pk
            LIMIT 1000
            """,
            tuple(query_terms),
        ):
            ranked[int(row["message_pk"])] += int(row["score"] or 0)

        # 정확 일치가 없었던 토큰만 접두 일치로 보완한다.
        # (LIKE 대신 범위 조건: 한글 접두에서도 term 인덱스를 확실히 타도록)
        for token in query_terms:
            if token in exact_terms or len(token) < 2:
                continue
            for row in conn.execute(
                """
                SELECT message_pk, SUM(count) AS score
                FROM message_terms
                WHERE term >= ? AND term < ?
                GROUP BY message_pk
                LIMIT 500
                """,
                (token, token + chr(0xFFFF)),
            ):
                ranked[int(row["message_pk"])] += int(row["score"] or 0)
        return ranked

    def _filtered_message_rankings(self, conn: sqlite3.Connection, state: SearchPanelState) -> Counter[int]:
        filters, params = make_filter_sql(
            state,
            date_column="m.message_date",
            source_column="m.source_id",
            source_parent_column="s.parent_channel_id",
            author_column="m.author_id",
            result_column="m.message_pk",
        )
        where_sql = " AND ".join(filters) if filters else "1 = 1"
        rows = conn.execute(
            f"""
            SELECT m.message_pk
            FROM messages m
            JOIN sources s ON s.source_id = m.source_id
            WHERE {where_sql}
            LIMIT 5000
            """,
            tuple(params),
        ).fetchall()
        return Counter({int(row["message_pk"]): 0 for row in rows})

    def _combined_keyword_rankings(self, conn: sqlite3.Connection, state: SearchPanelState) -> tuple[str, Counter[int]]:
        all_parts = split_query_parts(state.keyword_all)
        any_parts = split_query_parts(state.keyword_any)
        not_parts = split_query_parts(state.keyword_not)
        if not all_parts and not any_parts and state.query:
            any_parts = [state.query]
        display_query = " / ".join(
            part for part in (
                f"ALL: {state.keyword_all}" if state.keyword_all else "",
                f"ANY: {state.keyword_any}" if state.keyword_any else "",
                f"NOT: {state.keyword_not}" if state.keyword_not else "",
            ) if part
        ) or (state.query or "")
        if not all_parts and not any_parts:
            if not_parts:
                ranked = self._filtered_message_rankings(conn, state)
                excluded_ids: set[int] = set()
                for part in not_parts:
                    excluded_ids.update(self._keyword_rankings(conn, part))
                for message_pk in excluded_ids:
                    ranked.pop(message_pk, None)
                return display_query, ranked
            return display_query, Counter()

        ranked: Counter[int] = Counter()
        candidate_ids: set[int] | None = None
        if any_parts:
            any_ranked: Counter[int] = Counter()
            for part in any_parts:
                any_ranked.update(self._keyword_rankings(conn, part))
            candidate_ids = set(any_ranked)
            ranked.update(any_ranked)

        for part in all_parts:
            part_ranked = self._keyword_rankings(conn, part)
            part_ids = set(part_ranked)
            candidate_ids = part_ids if candidate_ids is None else candidate_ids & part_ids
            ranked.update(part_ranked)

        if candidate_ids is None:
            candidate_ids = set(ranked)

        excluded_ids: set[int] = set()
        for part in not_parts:
            excluded_ids.update(self._keyword_rankings(conn, part))
        candidate_ids -= excluded_ids
        ranked = Counter({message_pk: ranked[message_pk] for message_pk in candidate_ids})
        return display_query, ranked

    def run_keyword_search(self, state: SearchPanelState) -> ResultPage:
        query, ranked = "", Counter()
        with self.index_open() as conn:
            query, ranked = self._combined_keyword_rankings(conn, state)
            if not query.strip():
                state.last_result_kind = "keyword"
                state.last_result_ids = []
                return ResultPage("키워드 검색", [], state.page, state.page_size, 0, "검색어를 입력해주세요.")
            if not ranked:
                state.last_result_kind = "keyword"
                state.last_result_ids = []
                logger.info(
                    "mogindex keyword session=%s query=%r scope=%s categories=%s sources=%s date=%s..%s total=0 reason=no_ranked_terms",
                    state.session_id,
                    query,
                    state.source_scope,
                    state.category_ids,
                    state.source_ids,
                    date_bounds(state)[0],
                    date_bounds(state)[1],
                )
                return ResultPage("키워드 검색", [], state.page, state.page_size, 0, "현재 기간/범위에서 색인된 결과가 없습니다. 기간을 전체로 넓히거나 직접 기간을 지정해보세요.")

            filters, params = make_filter_sql(
                state,
                date_column="m.message_date",
                source_column="m.source_id",
                source_parent_column="s.parent_channel_id",
                author_column="m.author_id",
                result_column="m.message_pk",
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

        positive_query_parts = split_query_parts(state.keyword_all) or split_query_parts(state.keyword_any)
        if not positive_query_parts and state.query:
            positive_query_parts = [state.query]
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
        if state.sort == "newest" or not positive_query_parts:
            results.sort(key=lambda item: item.created_at, reverse=True)
        elif state.sort == "oldest":
            results.sort(key=lambda item: item.created_at)
        else:
            results.sort(key=lambda item: (-item.score, item.created_at))

        state.last_result_kind = "keyword"
        state.last_result_ids = cached_result_ids(item.message_pk for item in results)
        page = self._slice_results("키워드 검색", results, state)
        self.attach_snippets(page, positive_query_parts)
        logger.info(
            "mogindex keyword session=%s query=%r scope=%s categories=%s sources=%s date=%s..%s total=%s page=%s",
            state.session_id,
            query,
            state.source_scope,
            state.category_ids,
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

    def attach_snippets(self, page: ResultPage, terms: Iterable[str]) -> ResultPage:
        """페이지에 실린 결과(≤ page_size)에만 본문 미리보기를 붙인다. 원문이 없는 옛 행은 빈 채로 둔다."""
        if not page.results:
            return page
        terms = [t for t in terms if t]
        pks = [item.message_pk for item in page.results]
        try:
            with self.index_open() as conn:
                rows = conn.execute(
                    f"SELECT message_pk, content FROM messages WHERE message_pk IN ({','.join('?' for _ in pks)})",
                    tuple(pks),
                ).fetchall()
        except sqlite3.Error:
            logger.warning("mogindex snippet fetch failed", exc_info=True)
            return page
        content_by_pk = {int(row["message_pk"]): row["content"] for row in rows}
        for item in page.results:
            item.snippet = make_snippet(content_by_pk.get(item.message_pk), terms)
        return page

    def run_recap(self, state: SearchPanelState) -> TextPage:
        with self.index_open() as conn:
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
                  AND d.term NOT IN ({",".join("?" for _ in INDEX_EXCLUDED_TERMS)})
                  AND NOT EXISTS (
                      SELECT 1 FROM df_stopwords ds
                      WHERE ds.source_id = d.source_id AND ds.term = d.term
                  )
                ORDER BY d.message_date DESC, d.source_id, d.count DESC, d.term
                LIMIT 300
                """,
                tuple(term_params) + tuple(INDEX_EXCLUDED_TERMS),
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

    def returnee_period(self, state: SearchPanelState) -> tuple[str, str, bool]:
        """복귀자 기간 = 월드맵 카테고리에서 마지막으로 활동한 다음 날 .. 오늘.

        활동 기록이 없거나 RETURNEE_MAX_DAYS 보다 오래 비웠으면 최근 RETURNEE_MAX_DAYS 일로 자른다.
        반환: (start, today, capped)
        """
        worldmap_ids = list(dict.fromkeys(state.worldmap_category_ids))
        today_d = now_kst().date()
        today = today_d.isoformat()
        last_seen = None
        if worldmap_ids:
            with self.index_open() as conn:
                placeholders = ",".join("?" for _ in worldmap_ids)
                row = conn.execute(
                    f"""
                    SELECT MAX(m.message_date) AS last_seen
                    FROM messages m
                    JOIN sources s ON s.source_id = m.source_id
                    WHERE m.author_id = ? AND s.category_id IN ({placeholders})
                    """,
                    (state.owner_user_id, *worldmap_ids),
                ).fetchone()
                last_seen = row["last_seen"] if row else None
        start = (date.fromisoformat(last_seen) + timedelta(days=1)).isoformat() if last_seen else RETURNEE_DEFAULT_START_DATE
        capped = False
        if RETURNEE_MAX_DAYS > 0:
            floor = (today_d - timedelta(days=RETURNEE_MAX_DAYS)).isoformat()
            if start < floor:
                start, capped = floor, True
        return start, today, capped

    def _returnee_terms(self, state: SearchPanelState, start: str, today: str) -> list[tuple[str, int]]:
        """기간·범위별 상위 색인어 집계. 제외어는 여기서 적용하지 않고(캐시 공유) 호출 쪽에서 거른다."""
        key = (start, today, state.source_scope, tuple(scope_source_ids(state)), tuple(scope_category_ids(state)),
               state.origin_category_id, state.origin_parent_channel_id, state.origin_channel_id)
        now = time.monotonic()
        with self._returnee_cache_lock:
            hit = self._returnee_cache.get(key)
            if hit and now - hit[0] < RETURNEE_CACHE_TTL_SEC:
                return hit[1]
        filter_state = SearchPanelState.from_json(state.to_json())
        filter_state.date_preset = "custom"
        filter_state.start_date = start
        filter_state.end_date = today
        term_filters, term_params = make_filter_sql(
            filter_state,
            date_column="d.message_date",
            source_column="d.source_id",
            source_parent_column="s.parent_channel_id",
        )
        with self.index_open() as conn:
            rows = conn.execute(
                f"""
                SELECT d.term, SUM(d.count) AS total_count
                FROM daily_terms d
                JOIN sources s ON s.source_id = d.source_id
                WHERE {" AND ".join(term_filters) if term_filters else "1 = 1"}
                  AND LENGTH(d.term) BETWEEN 2 AND 8
                  AND d.term NOT IN ({",".join("?" for _ in INDEX_EXCLUDED_TERMS)})
                  AND NOT EXISTS (
                      SELECT 1 FROM df_stopwords ds
                      WHERE ds.source_id = d.source_id AND ds.term = d.term
                  )
                GROUP BY d.term
                HAVING SUM(d.count) > 1
                ORDER BY total_count DESC, d.term
                LIMIT ?
                """,
                tuple(term_params) + tuple(INDEX_EXCLUDED_TERMS) + (RETURNEE_FETCH_LIMIT,),
            ).fetchall()
        terms = [(str(row["term"]), int(row["total_count"])) for row in rows]
        with self._returnee_cache_lock:
            if len(self._returnee_cache) > 64:
                self._returnee_cache.clear()
            self._returnee_cache[key] = (now, terms)
        return terms

    def run_returnee_keywords(self, state: SearchPanelState) -> TextPage:
        if not list(dict.fromkeys(state.worldmap_category_ids)):
            return TextPage("복귀자 키워드", [], state.page, state.page_size, 0, "월드 맵 카테고리 설정이 없습니다.")
        limit = min(max(int(state.returnee_limit or 5), 1), 100)
        start, today, capped = self.returnee_period(state)
        # '빼고 싶은 키워드': 그 글자를 포함하는 색인어를 목록에서 뺀다 (예: '모그' 는 '모그텔' 도 뺀다).
        not_parts = split_query_parts(state.keyword_not)
        terms = [
            (term, count) for term, count in self._returnee_terms(state, start, today)
            if not any(part in term for part in not_parts)
        ][:limit]

        lines = [f"{idx}. {term}({count})" for idx, (term, count) in enumerate(terms, start=1)]
        header = [f"기간: {start} .. {today}" + (f" (최근 {RETURNEE_MAX_DAYS}일로 제한)" if capped else "")]
        if not_parts:
            header.append(f"제외: {', '.join(not_parts)}")
        state.last_result_kind = "returnee"
        state.last_result_ids = []
        page = self._slice_lines(
            "복귀자 키워드",
            header + lines if lines else [],
            state,
            "복귀자 키워드로 표시할 색인어가 없습니다." + (" 제외 키워드를 비우려면 '빼고 싶은 키워드'를 빈칸으로 제출하세요." if not_parts else ""),
        )
        page.options = [term for term, _ in terms[:RETURNEE_PICK_OPTIONS]]
        logger.info(
            "mogindex returnee session=%s user=%s start=%s end=%s scope=%s total=%s page=%s",
            state.session_id,
            state.owner_user_id,
            start,
            today,
            state.source_scope,
            page.total,
            page.page,
        )
        return page

    def run_topic_search(self, state: SearchPanelState) -> TextPage:
        query = normalize_text(state.query or "")
        query_terms = set(derive_query_terms(query)) if query else set()
        with self.index_open() as conn:
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
                title_terms = set(derive_query_terms(row["title"]))
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
        with self.index_open() as conn:
            filters, params = make_filter_sql(
                state,
                date_column="m.message_date",
                source_column="m.source_id",
                source_parent_column="s.parent_channel_id",
                author_column="m.author_id",
                result_column="m.message_pk",
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
        state.last_result_ids = cached_result_ids(item.message_pk for item in results)
        page = self._slice_results("채널 보기", results, state)
        self.attach_snippets(page, [])
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
        with self.index_open() as conn:
            filters, params = make_filter_sql(
                state,
                date_column="m.message_date",
                source_column="m.source_id",
                source_parent_column="s.parent_channel_id",
                author_column="m.author_id",
                result_column="m.message_pk",
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

