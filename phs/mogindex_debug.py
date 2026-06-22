"""
Standalone debug harness for the Mogtel Korean search/index prototype.

It does not import discord.py. Use it to test the SQLite schema, Korean n-gram
indexing, topic-list parsing, and return-recap query shape before wiring the
feature into bot commands.

Examples:
    python mogindex_debug.py --db .tmp/mogindex_debug.sqlite3 seed --reset
    python mogindex_debug.py --db .tmp/mogindex_debug.sqlite3 search 유죄
    python mogindex_debug.py --db .tmp/mogindex_debug.sqlite3 search 아저씨
    python mogindex_debug.py --db .tmp/mogindex_debug.sqlite3 recap --user-id 1001
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sqlite3
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
import os
from dotenv import load_dotenv

load_dotenv()


DISCORD_EPOCH_MS = 1420070400000
DEFAULT_DB_PATH = Path("/home/ubuntu/mogtel/mogindex/search_index_phs.sqlite3")

DEBUG_GUILD_ID = os.getenv("PHS_INDEX_ERROR_THREAD_ID")
CATEGORY_ID = os.getenv("PHS_CATEGORY_ID")

INDEX_SOURCE_SEEDS = [
    ("1399959589121953875", "channel", "미딜 폐연구소 1층", None),
    ("1422784351627644968", "channel", "미딜 폐연구소 지하 1층", None),
    ("1422784292680896534", "channel", "미딜 폐연구소 지하 2층", None),
    ("1431917788557213726", "channel", "미딜 폐연구소 지하 3층", None),
    ("1399959232610177047", "channel", "차원점검열차", None),
    ("1399957143091806239", "chaannel", "라이프 스트림", None)
]

PARTICLE_SUFFIXES = (
    "가",
    "가까스로",
    "가령",
    "각",
    "각각",
    "각자",
    "각종",
    "갖고말하자면",
    "같다",
    "같이",
    "개의치않고",
    "거니와",
    "거바",
    "거의",
    "것",
    "것과 같이",
    "것들",
    "게다가",
    "게우다",
    "겨우",
    "견지에서",
    "결과에 이르다",
    "결국",
    "결론을 낼 수 있다",
    "겸사겸사",
    "고려하면",
    "고로",
    "곧",
    "공동으로",
    "과",
    "과연",
    "관계가 있다",
    "관계없이",
    "관련이 있다",
    "관하여",
    "관한",
    "관해서는",
    "구",
    "구체적으로",
    "구토하다",
    "그",
    "그들",
    "그때",
    "그래",
    "그래도",
    "그래서",
    "그러나",
    "그러니",
    "그러니까",
    "그러면",
    "그러므로",
    "그러한즉",
    "그런 까닭에",
    "그런데",
    "그런즉",
    "그럼",
    "그럼에도 불구하고",
    "그렇게 함으로써",
    "그렇지",
    "그렇지 않다면",
    "그렇지 않으면",
    "그렇지만",
    "그렇지않으면",
    "나",
    "나머지는",
    "남들",
    "남짓",
    "너",
    "너희",
    "너희들",
    "네",
    "넷",
    "년",
    "논하지 않다",
    "놀라다",
    "누가 알겠는가",
    "누구",
    "다른",
    "다른 방면으로",
    "다만",
    "다섯",
    "다소",
    "다수",
    "다시 말하자면",
    "다시말하면",
    "다음",
    "다음에",
    "다음으로",
    "단지",
    "답다",
    "당신",
    "당장",
    "대로 하다",
    "대하면",
    "대하여",
    "대해 말하자면",
    "대해서",
    "댕그",
    "더구나",
    "더군다나",
    "더라도",
    "더불어",
    "더욱더",
    "더욱이는",
    "도달하다",
    "도착하다",
    "동시에",
    "동안",
    "된바에야",
    "된이상",
    "두번째로",
    "둘",
    "둥둥",
    "뒤따라",
    "뒤이어",
    "든간에",
    "들",
    "등",
    "등등",
    "딩동",
    "따라",
    "따라서",
    "따위",
    "따지지 않다",
    "딱",
    "때",
    "때가 되어",
    "때문에",
    "또",
    "또한",
    "뚝뚝",
    "라 해도",
    "령",
    "로",
    "로 인하여",
    "로부터",
    "로써",
    "륙",
    "를",
    "마음대로",
    "마저",
    "마저도",
    "마치",
    "막론하고",
    "만 못하다",
    "만약",
    "만약에",
    "만은 아니다",
    "만이 아니다",
    "만일",
    "만큼",
    "말하자면",
    "말할것도 없고",
    "매",
    "매번",
    "메쓰겁다",
    "몇",
    "모",
    "모두",
    "무렵",
    "무릎쓰고",
    "무슨",
    "무엇",
    "무엇때문에",
    "물론",
    "및",
    "바꾸어말하면",
    "바꾸어말하자면",
    "바꾸어서 말하면",
    "바꾸어서 한다면",
    "바꿔 말하면",
    "바로",
    "바와같이",
    "밖에 안된다",
    "반대로",
    "반대로 말하자면",
    "반드시",
    "버금",
    "보는데서",
    "보다더",
    "보드득",
    "본대로",
    "봐",
    "봐라",
    "부류의 사람들",
    "부터",
    "불구하고",
    "불문하고",
    "붕붕",
    "비걱거리다",
    "비교적",
    "비길수 없다",
    "비로소",
    "비록",
    "비슷하다",
    "비추어 보아",
    "비하면",
    "뿐만 아니라",
    "뿐만아니라",
    "뿐이다",
    "삐걱",
    "삐걱거리다",
    "사",
    "삼",
    "상대적으로 말하자면",
    "생각한대로",
    "설령",
    "설마",
    "설사",
    "셋",
    "소생",
    "소인",
    "솨",
    "쉿",
    "습니까",
    "습니다",
    "시각",
    "시간",
    "시작하여",
    "시초에",
    "시키다",
    "실로",
    "심지어",
    "아",
    "아니",
    "아니나다를가",
    "아니라면",
    "아니면",
    "아니었다면",
    "아래윗",
    "아무거나",
    "아무도",
    "아야",
    "아울러",
    "아이",
    "아이고",
    "아이구",
    "아이야",
    "아이쿠",
    "아하",
    "아홉",
    "안 그러면",
    "않기 위하여",
    "않기 위해서",
    "알 수 있다",
    "알았어",
    "앗",
    "앞에서",
    "앞의것",
    "야",
    "약간",
    "양자",
    "어",
    "어기여차",
    "어느",
    "어느 년도",
    "어느것",
    "어느곳",
    "어느때",
    "어느쪽",
    "어느해",
    "어디",
    "어때",
    "어떠한",
    "어떤",
    "어떤것",
    "어떤것들",
    "어떻게",
    "어떻해",
    "어이",
    "어째서",
    "어쨋든",
    "어쩔수 없다",
    "어찌",
    "어찌됏든",
    "어찌됏어",
    "어찌하든지",
    "어찌하여",
    "언제",
    "언젠가",
    "얼마",
    "얼마 안 되는 것",
    "얼마간",
    "얼마나",
    "얼마든지",
    "얼마만큼",
    "얼마큼",
    "엉엉",
    "에",
    "에 가서",
    "에 달려 있다",
    "에 대해",
    "에 있다",
    "에 한하다",
    "에게",
    "에서",
    "여",
    "여기",
    "여덟",
    "여러분",
    "여보시오",
    "여부",
    "여섯",
    "여전히",
    "여차",
    "연관되다",
    "연이서",
    "영",
    "영차",
    "옆사람",
    "예",
    "예를 들면",
    "예를 들자면",
    "예컨대",
    "예하면",
    "오",
    "오로지",
    "오르다",
    "오자마자",
    "오직",
    "오호",
    "오히려",
    "와",
    "와 같은 사람들",
    "와르르",
    "와아",
    "왜",
    "왜냐하면",
    "외에도",
    "요만큼",
    "요만한 것",
    "요만한걸",
    "요컨대",
    "우르르",
    "우리",
    "우리들",
    "우선",
    "우에 종합한것과같이",
    "운운",
    "월",
    "위에서 서술한바와같이",
    "위하여",
    "위해서",
    "윙윙",
    "육",
    "으로",
    "으로 인하여",
    "으로서",
    "으로써",
    "을",
    "응",
    "응당",
    "의",
    "의거하여",
    "의지하여",
    "의해",
    "의해되다",
    "의해서",
    "이",
    "이 되다",
    "이 때문에",
    "이 밖에",
    "이 외에",
    "이 정도의",
    "이것",
    "이곳",
    "이때",
    "이라면",
    "이래",
    "이러이러하다",
    "이러한",
    "이런",
    "이럴정도로",
    "이렇게 많은 것",
    "이렇게되면",
    "이렇게말하자면",
    "이렇구나",
    "이로 인하여",
    "이르기까지",
    "이리하여",
    "이만큼",
    "이번",
    "이봐",
    "이상",
    "이어서",
    "이었다",
    "이와 같다",
    "이와 같은",
    "이와 반대로",
    "이와같다면",
    "이외에도",
    "이용하여",
    "이유만으로",
    "이젠",
    "이지만",
    "이쪽",
    "이천구",
    "이천육",
    "이천칠",
    "이천팔",
    "인 듯하다",
    "인젠",
    "일",
    "일것이다",
    "일곱",
    "일단",
    "일때",
    "일반적으로",
    "일지라도",
    "임에 틀림없다",
    "입각하여",
    "입장에서",
    "잇따라",
    "있다",
    "자",
    "자기",
    "자기집",
    "자마자",
    "자신",
    "잠깐",
    "잠시",
    "저",
    "저것",
    "저것만큼",
    "저기",
    "저쪽",
    "저희",
    "전부",
    "전자",
    "전후",
    "점에서 보아",
    "정도에 이르다",
    "제",
    "제각기",
    "제외하고",
    "조금",
    "조차",
    "조차도",
    "졸졸",
    "좀",
    "좋아",
    "좍좍",
    "주룩주룩",
    "주저하지 않고",
    "줄은 몰랏다",
    "줄은모른다",
    "중에서",
    "중의하나",
    "즈음하여",
    "즉",
    "즉시",
    "지든지",
    "지만",
    "지말고",
    "진짜로",
    "쪽으로",
    "차라리",
    "참",
    "참나",
    "첫번째로",
    "쳇",
    "총적으로",
    "총적으로 말하면",
    "총적으로 보면",
    "칠",
    "콸콸",
    "쾅쾅",
    "쿵",
    "타다",
    "타인",
    "탕탕",
    "토하다",
    "통하여",
    "툭",
    "퉤",
    "틈타",
    "팍",
    "팔",
    "퍽",
    "펄렁",
    "하",
    "하게될것이다",
    "하게하다",
    "하겠는가",
    "하고 있다",
    "하고있었다",
    "하곤하였다",
    "하구나",
    "하기 때문에",
    "하기 위하여",
    "하기는한데",
    "하기만 하면",
    "하기보다는",
    "하기에",
    "하나",
    "하느니",
    "하는 김에",
    "하는 편이 낫다",
    "하는것도",
    "하는것만 못하다",
    "하는것이 낫다",
    "하는바",
    "하더라도",
    "하도다",
    "하도록시키다",
    "하도록하다",
    "하든지",
    "하려고하다",
    "하마터면",
    "하면 할수록",
    "하면된다",
    "하면서",
    "하물며",
    "하여금",
    "하여야",
    "하자마자",
    "하지 않는다면",
    "하지 않도록",
    "하지마",
    "하지마라",
    "하지만",
    "하하",
    "한 까닭에",
    "한 이유는",
    "한 후",
    "한다면",
    "한다면 몰라도",
    "한데",
    "한마디",
    "한적이있다",
    "한켠으로는",
    "한항목",
    "할 따름이다",
    "할 생각이다",
    "할 줄 안다",
    "할 지경이다",
    "할 힘이 있다",
    "할때",
    "할만하다",
    "할망정",
    "할뿐",
    "할수있다",
    "할수있어",
    "할줄알다",
    "할지라도",
    "할지언정",
    "함께",
    "해도된다",
    "해도좋다",
    "해봐요",
    "해서는 안된다",
    "해야한다",
    "해요",
    "했어요",
    "향하다",
    "향하여",
    "향해서",
    "허",
    "허걱",
    "허허",
    "헉",
    "헉헉",
    "헐떡헐떡",
    "형식으로 쓰여",
    "혹시",
    "혹은",
    "혼자",
    "훨씬",
    "휘익",
    "휴",
    "흐흐",
    "흥",
    "힘입어",
)

STOP_TERMS = {
    "그리고",
    "그리하여",
    "그만이다",
    "그에 따르는",
    "그위에",
    "그저",
    "그중에서",
    "그치지 않다",
    "근거로",
    "근거하여",
    "기대여",
    "기점으로",
    "기준으로",
    "기타",
    "까닭으로",
    "까악",
    "까지",
    "까지 미치다",
    "까지도",
    "꽈당",
    "끙끙",
    "끼익",
    "그런데",
    "하지만",
    "그래서",
    "오늘",
    "내일",
    "어제",
    "진짜",
    "약간",
    "너무",
    "그냥",
    "이거",
    "저거",
    "그거",
    "여기",
    "저기",
    "거기",
}

DISCORD_MESSAGE_URL_RE = re.compile(
    r"https?://(?:ptb\.|canary\.)?discord(?:app)?\.com/channels/"
    r"(?P<guild_id>\d+)/(?P<channel_id>\d+)/(?P<message_id>\d+)"
)
TOKEN_RE = re.compile(r"[0-9A-Za-z가-힣]+")


@dataclass(frozen=True)
class TopicLine:
    title: str
    jump_url: str
    guild_id: str
    channel_id: str
    message_id: str
    source_line: str


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def initialize_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sources (
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

        CREATE TABLE IF NOT EXISTS messages (
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

        CREATE INDEX IF NOT EXISTS idx_messages_source_date
            ON messages(source_id, message_date);
        CREATE INDEX IF NOT EXISTS idx_messages_author_date
            ON messages(author_id, message_date);

        CREATE TABLE IF NOT EXISTS message_terms (
            term TEXT NOT NULL,
            message_pk INTEGER NOT NULL REFERENCES messages(message_pk) ON DELETE CASCADE,
            source_id TEXT NOT NULL,
            message_date TEXT NOT NULL,
            count INTEGER NOT NULL,
            PRIMARY KEY (term, message_pk)
        );

        CREATE INDEX IF NOT EXISTS idx_message_terms_lookup
            ON message_terms(term, message_date, source_id);

        CREATE TABLE IF NOT EXISTS daily_terms (
            source_id TEXT NOT NULL,
            message_date TEXT NOT NULL,
            term TEXT NOT NULL,
            count INTEGER NOT NULL,
            PRIMARY KEY (source_id, message_date, term)
        );

        CREATE INDEX IF NOT EXISTS idx_daily_terms_date
            ON daily_terms(message_date, count DESC);

        CREATE TABLE IF NOT EXISTS manual_topics (
            topic_id INTEGER PRIMARY KEY,
            topic_thread_id TEXT NOT NULL,
            topic_line_hash TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            linked_message_id TEXT NOT NULL,
            source_id TEXT,
            topic_date TEXT NOT NULL,
            jump_url TEXT NOT NULL,
            source_line TEXT NOT NULL,
            synced_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_manual_topics_date
            ON manual_topics(topic_date, source_id);
        """
    )
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS message_fts "
        "USING fts5(index_text, content='', tokenize='trigram')"
    )
    conn.commit()


def reset_debug_data(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS message_fts;
        DELETE FROM manual_topics;
        DELETE FROM message_terms;
        DELETE FROM daily_terms;
        DELETE FROM messages;
        DELETE FROM sources;
        """
    )
    conn.execute(
        "CREATE VIRTUAL TABLE message_fts "
        "USING fts5(index_text, content='', tokenize='trigram')"
    )
    conn.commit()


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower()
    text = DISCORD_MESSAGE_URL_RE.sub(" ", text)
    text = re.sub(r"<[@#&!]*\d+>", " ", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^0-9a-z가-힣]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def strip_particle(token: str) -> str:
    for suffix in PARTICLE_SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 2:
            return token[: -len(suffix)]
    return token


def iter_ngrams(token: str, min_n: int = 2, max_n: int = 5) -> Iterable[str]:
    max_n = min(max_n, len(token))
    for size in range(min_n, max_n + 1):
        for idx in range(0, len(token) - size + 1):
            yield token[idx : idx + size]


def extract_terms(text: str) -> Counter[str]:
    terms: Counter[str] = Counter()
    for raw_token in TOKEN_RE.findall(normalize_text(text)):
        token = strip_particle(raw_token)
        if len(token) < 2 or token.isdigit() or token in STOP_TERMS:
            continue
        terms[token] += 2
        for ngram in iter_ngrams(token):
            if ngram not in STOP_TERMS:
                terms[ngram] += 1
    return terms


def discord_snowflake_datetime(snowflake: str) -> datetime:
    timestamp_ms = (int(snowflake) >> 22) + DISCORD_EPOCH_MS
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)


def make_debug_snowflake(dt: datetime, low_bits: int = 0) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    timestamp_ms = int(dt.timestamp() * 1000)
    return str(((timestamp_ms - DISCORD_EPOCH_MS) << 22) | (low_bits & 0x3FFFFF))


def jump_url(guild_id: str, channel_id: str, message_id: str) -> str:
    return f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"


def upsert_source(
    conn: sqlite3.Connection,
    source_id: str,
    source_kind: str,
    name: str,
    parent_channel_id: str | None,
    guild_id: str = DEBUG_GUILD_ID,
    category_id: str | None = CATEGORY_ID,
) -> None:
    conn.execute(
        """
        INSERT INTO sources (
            source_id, source_kind, guild_id, category_id, parent_channel_id, name
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_id) DO UPDATE SET
            source_kind = excluded.source_kind,
            guild_id = excluded.guild_id,
            category_id = excluded.category_id,
            parent_channel_id = excluded.parent_channel_id,
            name = excluded.name,
            updated_at = CURRENT_TIMESTAMP
        """,
        (source_id, source_kind, guild_id, category_id, parent_channel_id, name),
    )


def insert_message_for_index(
    conn: sqlite3.Connection,
    *,
    message_id: str,
    source_id: str,
    channel_id: str,
    author_id: str,
    author_name: str,
    created_at: datetime,
    content: str,
    search_context: str = "",
    guild_id: str = DEBUG_GUILD_ID,
    thread_id: str | None = None,
) -> bool:
    message_date = created_at.date().isoformat()
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO messages (
            message_id, source_id, guild_id, channel_id, thread_id,
            author_id, author_name, created_at, message_date, jump_url
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            message_id,
            source_id,
            guild_id,
            channel_id,
            thread_id,
            author_id,
            author_name,
            created_at.isoformat(),
            message_date,
            jump_url(guild_id, channel_id, message_id),
        ),
    )
    if cursor.rowcount == 0:
        return False

    message_pk = conn.execute(
        "SELECT message_pk FROM messages WHERE message_id = ?", (message_id,)
    ).fetchone()["message_pk"]

    normalized = normalize_text(f"{content} {search_context}".strip())
    if normalized:
        conn.execute(
            "INSERT INTO message_fts(rowid, index_text) VALUES (?, ?)",
            (message_pk, normalized),
        )

    message_terms = extract_terms(content)
    for term, count in extract_terms(search_context).items():
        message_terms[term] += count

    content_terms = extract_terms(content)
    for term, count in message_terms.items():
        conn.execute(
            """
            INSERT INTO message_terms(term, message_pk, source_id, message_date, count)
            VALUES (?, ?, ?, ?, ?)
            """,
            (term, message_pk, source_id, message_date, count),
        )

    for term, count in content_terms.items():
        conn.execute(
            """
            INSERT INTO daily_terms(source_id, message_date, term, count)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(source_id, message_date, term)
            DO UPDATE SET count = count + excluded.count
            """,
            (source_id, message_date, term, count),
        )
    return True


def parse_topic_lines(text: str) -> list[TopicLine]:
    topics: list[TopicLine] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = DISCORD_MESSAGE_URL_RE.search(line)
        if not match:
            continue
        title = line[: match.start()].strip(" -\t*•")
        topics.append(
            TopicLine(
                title=title or "(제목 없음)",
                jump_url=match.group(0),
                guild_id=match.group("guild_id"),
                channel_id=match.group("channel_id"),
                message_id=match.group("message_id"),
                source_line=line,
            )
        )
    return topics


def sync_topic_lines(
    conn: sqlite3.Connection, text: str, topic_thread_id: str = "debug-topic-thread"
) -> int:
    inserted = 0
    for topic in parse_topic_lines(text):
        linked_message = conn.execute(
            "SELECT source_id, message_date FROM messages WHERE message_id = ?",
            (topic.message_id,),
        ).fetchone()
        if linked_message:
            source_id = linked_message["source_id"]
            topic_date = linked_message["message_date"]
        else:
            source_id = topic.channel_id
            topic_date = discord_snowflake_datetime(topic.message_id).date().isoformat()

        line_hash = hashlib.sha1(
            f"{topic_thread_id}\n{topic.source_line}".encode("utf-8")
        ).hexdigest()
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO manual_topics (
                topic_thread_id, topic_line_hash, title, linked_message_id,
                source_id, topic_date, jump_url, source_line
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                topic_thread_id,
                line_hash,
                topic.title,
                topic.message_id,
                source_id,
                topic_date,
                topic.jump_url,
                topic.source_line,
            ),
        )
        inserted += cursor.rowcount
    conn.commit()
    return inserted


def seed_debug_data(conn: sqlite3.Connection, *, reset: bool = False) -> None:
    if reset:
        reset_debug_data(conn)

    for source_id, kind, name, parent_id in INDEX_SOURCE_SEEDS:
        upsert_source(conn, source_id, kind, name, parent_id)

    samples = [
        (
            "1347082174347874406",
            None,
            "1001",
            "라피",
            datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc),
            "프릴 앞치마를 입으면 전투복 위에도 어울릴까?",
        ),
        (
            "1347082174347874406",
            None,
            "1002",
            "시온",
            datetime(2026, 6, 15, 12, 8, tzinfo=timezone.utc),
            "당신은 유죄입니다. 그리고 아저씨가 아니라 선생님이라고 불러주세요.",
        ),
        (
            "1480185936456188079",
            None,
            "1002",
            "시온",
            datetime(2026, 6, 16, 9, 10, tzinfo=timezone.utc),
            "음료 취향 이야기를 하자. 단 음료보다 씁쓸한 커피가 좋아.",
        ),
        (
            "1480185936456189001",
            "1480185936456188079",
            "1003",
            "노아",
            datetime(2026, 6, 17, 18, 35, tzinfo=timezone.utc),
            "사진 찍기랑 폰꾸 이야기는 이 스레드에서 이어가자.",
        ),
        (
            "1496445431964504064",
            None,
            "1004",
            "아인",
            datetime(2026, 6, 18, 14, 20, tzinfo=timezone.utc),
            "외근 중에 기념일 생일 챙기기 얘기가 나왔어.",
        ),
        (
            "1322066437409341442",
            None,
            "1005",
            "유리",
            datetime(2026, 6, 19, 22, 5, tzinfo=timezone.utc),
            "웃으면서 다가오는 다정 외향인이라는 표현이 너무 강하다.",
        ),
    ]

    topic_lines: list[str] = []
    for idx, (source_id, parent_id, author_id, author_name, created_at, content) in enumerate(
        samples, start=1
    ):
        message_id = make_debug_snowflake(created_at, idx)
        insert_message_for_index(
            conn,
            message_id=message_id,
            source_id=source_id,
            channel_id=source_id,
            thread_id=source_id if parent_id else None,
            author_id=author_id,
            author_name=author_name,
            created_at=created_at,
            content=content,
        )
        if idx <= 5:
            title = content.split(".")[0]
            topic_lines.append(
                f"- {title} {jump_url(DEBUG_GUILD_ID, source_id, message_id)}"
            )

    conn.commit()
    inserted_topics = sync_topic_lines(conn, "\n".join(topic_lines))
    print(f"Seeded {len(samples)} messages and {inserted_topics} manual topics.")


def search_messages(
    conn: sqlite3.Connection,
    query: str,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    source_id: str | None = None,
    limit: int = 10,
) -> list[sqlite3.Row]:
    normalized = normalize_text(query)
    query_terms = extract_terms(query)
    filters = ["1 = 1"]
    params: list[object] = []
    if start_date:
        filters.append("m.message_date >= ?")
        params.append(start_date)
    if end_date:
        filters.append("m.message_date <= ?")
        params.append(end_date)
    if source_id:
        filters.append("m.source_id = ?")
        params.append(source_id)

    ranked: Counter[int] = Counter()
    if len(normalized.replace(" ", "")) >= 3:
        for row in conn.execute(
            """
            SELECT rowid AS message_pk
            FROM message_fts
            WHERE message_fts MATCH ?
            LIMIT 200
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
            """,
            tuple(query_terms.keys()),
        ):
            ranked[int(row["message_pk"])] += int(row["score"])

    if not ranked:
        return []

    message_placeholders = ",".join("?" for _ in ranked)
    rows = conn.execute(
        f"""
        SELECT
            m.message_pk, m.source_id, s.name AS source_name,
            m.author_name, m.message_date, m.created_at, m.jump_url
        FROM messages m
        JOIN sources s ON s.source_id = m.source_id
        WHERE m.message_pk IN ({message_placeholders})
          AND {" AND ".join(filters)}
        ORDER BY m.message_date DESC, m.created_at DESC
        LIMIT ?
        """,
        tuple(ranked.keys()) + tuple(params) + (limit,),
    ).fetchall()
    return sorted(rows, key=lambda row: (-ranked[row["message_pk"]], row["created_at"]))


def get_recap(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
    source_id: str | None = None,
) -> tuple[str, str, list[sqlite3.Row], list[sqlite3.Row]]:
    if not end_date:
        end_date = date.today().isoformat()
    if not start_date:
        last_seen = conn.execute(
            "SELECT MAX(message_date) AS last_seen FROM messages WHERE author_id = ?",
            (user_id,),
        ).fetchone()["last_seen"]
        if last_seen:
            start_date = (date.fromisoformat(last_seen) + timedelta(days=1)).isoformat()
        else:
            start_date = conn.execute(
                "SELECT MIN(message_date) AS first_seen FROM messages"
            ).fetchone()["first_seen"]

    topic_filters = ["topic_date BETWEEN ? AND ?"]
    topic_params: list[object] = [start_date, end_date]
    term_filters = ["d.message_date BETWEEN ? AND ?"]
    term_params: list[object] = [start_date, end_date]
    if source_id:
        topic_filters.append("source_id = ?")
        topic_params.append(source_id)
        term_filters.append("d.source_id = ?")
        term_params.append(source_id)

    topics = conn.execute(
        f"""
        SELECT topic_date, source_id, title, jump_url
        FROM manual_topics
        WHERE {" AND ".join(topic_filters)}
        ORDER BY topic_date, source_id, topic_id
        """,
        tuple(topic_params),
    ).fetchall()

    keywords = conn.execute(
        f"""
        SELECT d.message_date, d.source_id, s.name AS source_name, d.term, d.count
        FROM daily_terms d
        JOIN sources s ON s.source_id = d.source_id
        WHERE {" AND ".join(term_filters)}
          AND LENGTH(d.term) BETWEEN 2 AND 8
          AND d.term NOT IN ({",".join("?" for _ in STOP_TERMS)})
        ORDER BY d.message_date, d.source_id, d.count DESC, d.term
        """,
        tuple(term_params) + tuple(STOP_TERMS),
    ).fetchall()
    return start_date, end_date, topics, keywords


def print_search_results(rows: list[sqlite3.Row]) -> None:
    if not rows:
        print("No search results.")
        return
    for idx, row in enumerate(rows, start=1):
        print(
            f"{idx}. [{row['message_date']}] {row['source_name']} "
            f"/ {row['author_name']} -> {row['jump_url']}"
        )


def print_recap(
    start_date: str,
    end_date: str,
    topics: list[sqlite3.Row],
    keywords: list[sqlite3.Row],
    keyword_limit_per_source: int = 5,
) -> None:
    print(f"Recap range: {start_date} .. {end_date}")
    if not topics and not keywords:
        print("No indexed activity in range.")
        return

    topics_by_date: dict[str, list[sqlite3.Row]] = {}
    for topic in topics:
        topics_by_date.setdefault(topic["topic_date"], []).append(topic)

    keyword_rows: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for keyword in keywords:
        key = (keyword["message_date"], keyword["source_id"])
        rows = keyword_rows.setdefault(key, [])
        if len(rows) < keyword_limit_per_source:
            rows.append(keyword)

    all_dates = sorted(
        set(topics_by_date)
        | {message_date for message_date, _source_id in keyword_rows.keys()}
    )
    for current_date in all_dates:
        print(f"\n[{current_date}]")
        date_topics = topics_by_date.get(current_date, [])
        if date_topics:
            for topic in date_topics:
                print(f"- {topic['title']} -> {topic['jump_url']}")
            continue

        for (message_date, _source_id), rows in keyword_rows.items():
            if message_date != current_date:
                continue
            terms = ", ".join(f"{row['term']}({row['count']})" for row in rows)
            print(f"- {rows[0]['source_name']}: {terms}")


def print_inspect(conn: sqlite3.Connection) -> None:
    for table in ("sources", "messages", "message_terms", "daily_terms", "manual_topics"):
        count = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"]
        print(f"{table}: {count}")
    count = conn.execute("SELECT COUNT(*) AS count FROM message_fts").fetchone()["count"]
    print(f"message_fts: {count}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Debug Mogtel search/index storage.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH, help="SQLite DB path")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init", help="Create the debug DB schema")

    seed_parser = subparsers.add_parser("seed", help="Insert deterministic sample data")
    seed_parser.add_argument("--reset", action="store_true")

    search_parser = subparsers.add_parser("search", help="Search indexed sample data")
    search_parser.add_argument("query")
    search_parser.add_argument("--start-date")
    search_parser.add_argument("--end-date")
    search_parser.add_argument("--source-id")
    search_parser.add_argument("--limit", type=int, default=10)

    recap_parser = subparsers.add_parser("recap", help="Print a return recap")
    recap_parser.add_argument("--user-id", required=True)
    recap_parser.add_argument("--start-date")
    recap_parser.add_argument("--end-date")
    recap_parser.add_argument("--source-id")

    parse_parser = subparsers.add_parser("parse-topics", help="Parse and sync topic lines")
    parse_parser.add_argument("--file", type=Path, required=True)
    parse_parser.add_argument("--topic-thread-id", default="debug-topic-thread")

    subparsers.add_parser("inspect", help="Print row counts")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    with connect(args.db) as conn:
        initialize_schema(conn)
        if args.command == "init":
            print(f"Initialized {args.db}")
        elif args.command == "seed":
            seed_debug_data(conn, reset=args.reset)
        elif args.command == "search":
            rows = search_messages(
                conn,
                args.query,
                start_date=args.start_date,
                end_date=args.end_date,
                source_id=args.source_id,
                limit=args.limit,
            )
            print_search_results(rows)
        elif args.command == "recap":
            start, end, topics, keywords = get_recap(
                conn,
                user_id=args.user_id,
                start_date=args.start_date,
                end_date=args.end_date,
                source_id=args.source_id,
            )
            print_recap(start, end, topics, keywords)
        elif args.command == "parse-topics":
            text = args.file.read_text(encoding="utf-8")
            inserted = sync_topic_lines(conn, text, topic_thread_id=args.topic_thread_id)
            print(f"Synced {inserted} new topic lines from {args.file}.")
        elif args.command == "inspect":
            print_inspect(conn)


if __name__ == "__main__":
    main()
