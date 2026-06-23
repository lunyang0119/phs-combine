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
import os
from dotenv import load_dotenv
import re
import sqlite3
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

load_dotenv()

DISCORD_EPOCH_MS = 1420070400000
DEFAULT_DB_PATH = Path("/home/ubuntu/mogtel/mogindex/search_index_debug.sqlite3")


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# Full-index pacing knobs. Override from .env/environment when needed.
# If a single day takes at least this many seconds, rest before the next day.
FULL_INDEX_SLOW_DAY_SECONDS = env_int("FULL_INDEX_SLOW_DAY_SECONDS", 180)
# Rest duration after a slow full-index day.
FULL_INDEX_REST_SECONDS = env_int("FULL_INDEX_REST_SECONDS", 120)
# Retry transient Discord 5xx/network failures while indexing a single source.
FULL_INDEX_DISCORD_RETRY_ATTEMPTS = env_int("FULL_INDEX_DISCORD_RETRY_ATTEMPTS", 4)
# Base delay; actual delay is this value multiplied by the retry number.
FULL_INDEX_DISCORD_RETRY_SECONDS = env_int("FULL_INDEX_DISCORD_RETRY_SECONDS", 15)
# Number of sources/threads to index concurrently inside one day.
# Keep the server default conservative; raise this in local .env when testing.
FULL_INDEX_PARALLEL_SOURCES = env_int("FULL_INDEX_PARALLEL_SOURCES", 1)
# Maximum seconds allowed for one source/thread during full-index fetch.
# Set to 0 to disable the per-source timeout.
FULL_INDEX_SOURCE_TIMEOUT_SECONDS = env_int("FULL_INDEX_SOURCE_TIMEOUT_SECONDS", 300)
# Number of fetched Discord messages to write in one SQLite batch.
MOGINDEX_WRITE_BATCH_SIZE = max(1, env_int("MOGINDEX_WRITE_BATCH_SIZE", 250))

# Fake guild id used only by local seed/debug data. This is not the live server id.
DEBUG_GUILD_ID = os.getenv("MOG_GUILD_ID")
# Default category id used by debug seed/backfill helpers.
CATEGORY_ID = os.getenv("MOG_CATEGORY_ID")

# Sample sources used by local seed tests without connecting to Discord.
INDEX_SOURCE_SEEDS = [
    ("1347082174347874406", "channel", "차원점검열차", None),
    ("1480185936456188079", "channel", "카페테리아", None),
    ("1496445431964504064", "channel", "외근", None),
    ("1322066437409341442", "channel", "환영의 메아리", None),
    ("1480185936456189001", "thread", "카페테리아 / 폰꾸 회의", "1480185936456188079"),
]

PARTICLE_SUFFIXES = (
    "으로부터",
    "에게서",
    "한테서",
    "께서는",
    "에서는",
    "이라도",
    "이라면",
    "으로",
    "로서",
    "로써",
    "에게",
    "한테",
    "께서",
    "부터",
    "까지",
    "처럼",
    "보다",
    "밖에",
    "마저",
    "조차",
    "이나",
    "라도",
    "다면",
    "이며",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "와",
    "과",
    "도",
    "만",
    "에",
    "의",
    "로",
    "랑",
    "야",
    "아",
)

STOP_TERMS = {
    '이',
    '있',
    '하',
    '것',
    '들',
    '그',
    '되',
    '수',
    '보',
    '않',
    '없',
    '나',
    '사람',
    '주',
    '아니',
    '등',
    '같',
    '우리',
    '때',
    '년',
    '가',
    '한',
    '지',
    '대하',
    '오',
    '말',
    '일',
    '그렇',
    '위하',
    '때문',
    '그것',
    '두',
    '말하',
    '알',
    '그러나',
    '받',
    '못하',
    '그런',
    '또',
    '문제',
    '더',
    '사회',
    '많',
    '그리고',
    '좋',
    '크',
    '따르',
    '중',
    '나오',
    '가지',
    '씨',
    '시키',
    '만들',
    '지금',
    '생각하',
    '그러',
    '속',
    '하나',
    '집',
    '살',
    '모르',
    '적',
    '월',
    '데',
    '자신',
    '안',
    '어떤',
    '내',
    '경우',
    '명',
    '생각',
    '시간',
    '그녀',
    '다시',
    '이런',
    '앞',
    '보이',
    '번',
    '다른',
    '어떻',
    '여자',
    '개',
    '전',
    '사실',
    '이렇',
    '점',
    '싶',
    '정도',
    '좀',
    '원',
    '잘',
    '통하',
    '소리',
    '놓',
    '아',
    '휴',
    '아이구',
    '아이쿠',
    '아이고',
    '어',
    '저희',
    '따라',
    '의해',
    '을',
    '를',
    '에',
    '의',
    '으로',
    '로',
    '에게',
    '뿐이다',
    '의거하여',
    '근거하여',
    '입각하여',
    '기준으로',
    '예하면',
    '예를 들면',
    '예를 들자면',
    '저',
    '소인',
    '소생',
    '지말고',
    '하지마',
    '하지마라',
    '물론',
    '또한',
    '비길수 없다',
    '해서는 안된다',
    '뿐만 아니라',
    '만이 아니다',
    '만은 아니다',
    '막론하고',
    '관계없이',
    '그치지 않다',
    '그런데',
    '하지만',
    '든간에',
    '논하지 않다',
    '따지지 않다',
    '설사',
    '비록',
    '더라도',
    '아니면',
    '만 못하다',
    '하는 편이 낫다',
    '불문하고',
    '향하여',
    '향해서',
    '향하다',
    '쪽으로',
    '틈타',
    '이용하여',
    '타다',
    '오르다',
    '제외하고',
    '이 외에',
    '이 밖에',
    '하여야',
    '비로소',
    '한다면 몰라도',
    '외에도',
    '이곳',
    '여기',
    '부터',
    '기점으로',
    '따라서',
    '할 생각이다',
    '하려고하다',
    '이리하여',
    '그리하여',
    '그렇게 함으로써',
    '일때',
    '할때',
    '앞에서',
    '중에서',
    '보는데서',
    '으로써',
    '로써',
    '까지',
    '해야한다',
    '일것이다',
    '반드시',
    '할줄알다',
    '할수있다',
    '할수있어',
    '임에 틀림없다',
    '한다면',
    '등등',
    '제',
    '겨우',
    '단지',
    '다만',
    '할뿐',
    '딩동',
    '댕그',
    '대해서',
    '대하여',
    '대하면',
    '훨씬',
    '얼마나',
    '얼마만큼',
    '얼마큼',
    '남짓',
    '여',
    '얼마간',
    '약간',
    '다소',
    '조금',
    '다수',
    '몇',
    '얼마',
    '지만',
    '하물며',
    '그렇지만',
    '이외에도',
    '대해 말하자면',
    '다음에',
    '반대로',
    '반대로 말하자면',
    '이와 반대로',
    '바꾸어서 말하면',
    '바꾸어서 한다면',
    '만약',
    '그렇지않으면',
    '까악',
    '툭',
    '딱',
    '삐걱거리다',
    '보드득',
    '비걱거리다',
    '꽈당',
    '응당',
    '에 가서',
    '각',
    '각각',
    '여러분',
    '각종',
    '각자',
    '제각기',
    '하도록하다',
    '와',
    '과',
    '그러므로',
    '그래서',
    '고로',
    '한 까닭에',
    '하기 때문에',
    '거니와',
    '이지만',
    '관하여',
    '관한',
    '과연',
    '실로',
    '아니나다를가',
    '생각한대로',
    '진짜로',
    '한적이있다',
    '하곤하였다',
    '하하',
    '허허',
    '아하',
    '거바',
    '왜',
    '어째서',
    '무엇때문에',
    '어찌',
    '하겠는가',
    '무슨',
    '어디',
    '어느곳',
    '더군다나',
    '더욱이는',
    '어느때',
    '언제',
    '야',
    '이봐',
    '어이',
    '여보시오',
    '흐흐',
    '흥',
    '헉헉',
    '헐떡헐떡',
    '영차',
    '여차',
    '어기여차',
    '끙끙',
    '아야',
    '앗',
    '콸콸',
    '졸졸',
    '좍좍',
    '뚝뚝',
    '주룩주룩',
    '솨',
    '우르르',
    '그래도',
    '바꾸어말하면',
    '바꾸어말하자면',
    '혹은',
    '혹시',
    '답다',
    '및',
    '그에 따르는',
    '때가 되어',
    '즉',
    '지든지',
    '설령',
    '가령',
    '하더라도',
    '할지라도',
    '일지라도',
    '거의',
    '하마터면',
    '인젠',
    '이젠',
    '된바에야',
    '된이상',
    '만큼',
    '어찌됏든',
    '그위에',
    '게다가',
    '점에서 보아',
    '비추어 보아',
    '고려하면',
    '하게될것이다',
    '비교적',
    '보다더',
    '비하면',
    '시키다',
    '하게하다',
    '할만하다',
    '의해서',
    '연이서',
    '이어서',
    '잇따라',
    '뒤따라',
    '뒤이어',
    '결국',
    '의지하여',
    '기대여',
    '통하여',
    '자마자',
    '더욱더',
    '불구하고',
    '얼마든지',
    '마음대로',
    '주저하지 않고',
    '곧',
    '즉시',
    '바로',
    '당장',
    '하자마자',
    '밖에 안된다',
    '하면된다',
    '그래',
    '그렇지',
    '요컨대',
    '다시 말하자면',
    '바꿔 말하면',
    '구체적으로',
    '말하자면',
    '시작하여',
    '시초에',
    '이상',
    '허',
    '헉',
    '허걱',
    '바와같이',
    '해도좋다',
    '해도된다',
    '더구나',
    '와르르',
    '팍',
    '퍽',
    '펄렁',
    '동안',
    '이래',
    '하고있었다',
    '이었다',
    '에서',
    '로부터',
    '했어요',
    '해요',
    '함께',
    '같이',
    '더불어',
    '마저',
    '마저도',
    '양자',
    '모두',
    '습니다',
    '가까스로',
    '즈음하여',
    '다른 방면으로',
    '해봐요',
    '습니까',
    '말할것도 없고',
    '무릎쓰고',
    '개의치않고',
    '하는것만 못하다',
    '하는것이 낫다',
    '매',
    '매번',
    '모',
    '어느것',
    '어느',
    '갖고말하자면',
    '어느쪽',
    '어느해',
    '어느 년도',
    '라 해도',
    '언젠가',
    '어떤것',
    '저기',
    '저쪽',
    '저것',
    '그때',
    '그럼',
    '그러면',
    '요만한걸',
    '저것만큼',
    '그저',
    '이르기까지',
    '할 줄 안다',
    '할 힘이 있다',
    '너',
    '너희',
    '당신',
    '설마',
    '차라리',
    '할지언정',
    '할망정',
    '구토하다',
    '게우다',
    '토하다',
    '메쓰겁다',
    '옆사람',
    '퉤',
    '쳇',
    '힘입어',
    '다음',
    '버금',
    '두번째로',
    '기타',
    '첫번째로',
    '나머지는',
    '그중에서',
    '견지에서',
    '형식으로 쓰여',
    '입장에서',
    '위해서',
    '의해되다',
    '하도록시키다',
    '뿐만아니라',
    '전후',
    '전자',
    '앞의것',
    '잠시',
    '잠깐',
    '하면서',
    '그러한즉',
    '그런즉',
    '남들',
    '아무거나',
    '어찌하든지',
    '같다',
    '비슷하다',
    '예컨대',
    '이럴정도로',
    '어떻게',
    '만일',
    '위에서 서술한바와같이',
    '인 듯하다',
    '하지 않는다면',
    '만약에',
    '무엇',
    '아래윗',
    '조차',
    '한데',
    '그럼에도 불구하고',
    '여전히',
    '심지어',
    '까지도',
    '조차도',
    '하지 않도록',
    '않기 위하여',
    '시각',
    '무렵',
    '어때',
    '어떠한',
    '하여금',
    '네',
    '예',
    '우선',
    '누구',
    '누가 알겠는가',
    '아무도',
    '줄은모른다',
    '줄은 몰랏다',
    '하는 김에',
    '겸사겸사',
    '하는바',
    '그런 까닭에',
    '한 이유는',
    '그러니',
    '그러니까',
    '때문에',
    '그들',
    '너희들',
    '타인',
    '것들',
    '위하여',
    '공동으로',
    '동시에',
    '하기 위하여',
    '어찌하여',
    '붕붕',
    '윙윙',
    '엉엉',
    '휘익',
    '오호',
    '어쨋든',
    '하기보다는',
    '놀라다',
    '상대적으로 말하자면',
    '마치',
    '아니라면',
    '쉿',
    '그렇지 않으면',
    '그렇지 않다면',
    '안 그러면',
    '아니었다면',
    '하든지',
    '이라면',
    '좋아',
    '알았어',
    '하는것도',
    '그만이다',
    '어쩔수 없다',
    '일반적으로',
    '일단',
    '한켠으로는',
    '오자마자',
    '이렇게되면',
    '이와같다면',
    '전부',
    '한마디',
    '한항목',
    '근거로',
    '하기에',
    '아울러',
    '않기 위해서',
    '이 되다',
    '로 인하여',
    '까닭으로',
    '이유만으로',
    '이로 인하여',
    '이 때문에',
    '알 수 있다',
    '결론을 낼 수 있다',
    '으로 인하여',
    '있다',
    '관계가 있다',
    '관련이 있다',
    '연관되다',
    '어떤것들',
    '에 대해',
    '여부',
    '하느니',
    '하면 할수록',
    '운운',
    '이러이러하다',
    '하구나',
    '하도다',
    '다시말하면',
    '다음으로',
    '에 있다',
    '에 달려 있다',
    '우리들',
    '오히려',
    '하기는한데',
    '어떻해',
    '어찌됏어',
    '본대로',
    '자',
    '이쪽',
    '이것',
    '이번',
    '이렇게말하자면',
    '이러한',
    '이와 같은',
    '요만큼',
    '요만한 것',
    '얼마 안 되는 것',
    '이만큼',
    '이 정도의',
    '이렇게 많은 것',
    '이와 같다',
    '이때',
    '이렇구나',
    '것과 같이',
    '끼익',
    '삐걱',
    '따위',
    '와 같은 사람들',
    '부류의 사람들',
    '왜냐하면',
    '중의하나',
    '오직',
    '오로지',
    '에 한하다',
    '하기만 하면',
    '도착하다',
    '까지 미치다',
    '도달하다',
    '정도에 이르다',
    '할 지경이다',
    '결과에 이르다',
    '관해서는',
    '하고 있다',
    '한 후',
    '혼자',
    '자기',
    '자기집',
    '우에 종합한것과같이',
    '총적으로 보면',
    '총적으로 말하면',
    '총적으로',
    '대로 하다',
    '으로서',
    '참',
    '할 따름이다',
    '쿵',
    '탕탕',
    '쾅쾅',
    '둥둥',
    '봐',
    '봐라',
    '아이야',
    '와아',
    '응',
    '아이',
    '참나',
    '령',
    '영',
    '삼',
    '사',
    '육',
    '륙',
    '칠',
    '팔',
    '구',
    '이천육',
    '이천칠',
    '이천팔',
    '이천구',
    '둘',
    '셋',
    '넷',
    '다섯',
    '여섯',
    '일곱',
    '여덟',
    '아홉',
    '!',
    '""""',
    '$',
    '%',
    '&',
    "'",
    '(',
    ')',
    '*',
    '+',
    '","',
    '-',
    '.',
    '...',
    '0',
    '1',
    '2',
    '3',
    '4',
    '5',
    '6',
    '7',
    '8',
    '9',
    ';',
    '<',
    '=',
    '>',
    '?',
    '@',
    '\\',
    '^',
    '_',
    '`',
    '|',
    '~',
    '·',
    '—',
    '——',
    '‘',
    '’',
    '“',
    '”',
    '…',
    '、',
    '。',
    '〈',
    '〉',
    '《',
    '》',
    '︿',
    '！',
    '＃',
    '＄',
    '％',
    '＆',
    '（',
    '）',
    '＊',
    '＋',
    '，',
    '：',
    '；',
    '＜',
    '＞',
    '？',
    '＠',
    '［',
    '］',
    '｛',
    '｜',
    '｝',
    '～',
    '￥',
}

ENDING_SUFFIXES = (
    '건가',
    '이었습니다',
    '였습니다',
    '했습니다',
    '입니까',
    '합니까',
    '습니까',
    '입니다',
    '합니다',
    '습니다',
    '니다',
)

ENDING_NGRAM_STOP_TERMS = {
    '니다',
    '습니',
    '습니다',
    '입니',
    '입니다',
    '합니',
    '합니다',
    '했습',
    '했습니',
    '했습니다',
}

PROJECT_KEEP_TERMS = {"사람"}

INDEX_EXCLUDED_TERMS = (STOP_TERMS - PROJECT_KEEP_TERMS) | ENDING_NGRAM_STOP_TERMS

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


@dataclass(frozen=True)
class PreparedIndexedMessage:
    message_id: str
    source_id: str
    channel_id: str
    thread_id: str | None
    guild_id: str
    author_id: str
    author_name: str
    created_at: str
    message_date: str
    jump_url: str
    normalized: str
    message_terms: dict[str, int]
    content_terms: dict[str, int]

def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
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


JONGSEONG_RIEUL = "\u11af"
JONGSEONG_REQUIRED_SUFFIXES = {"이", "은", "을", "과", "아", "으로"}
NO_JONGSEONG_SUFFIXES = {"가", "는", "를", "와", "야"}
RIEUL_OR_NO_JONGSEONG_SUFFIXES = {"로"}


def has_hangul_jongseong(text: str) -> bool:
    if not text:
        return False
    decomposed = unicodedata.normalize("NFD", text[-1])
    if not decomposed:
        return False
    return "JONGSEONG" in unicodedata.name(decomposed[-1], "")


def has_final_rieul(text: str) -> bool:
    if not text:
        return False
    decomposed = unicodedata.normalize("NFD", text[-1])
    return bool(decomposed) and decomposed[-1] == JONGSEONG_RIEUL


def should_strip_particle(stem: str, suffix: str) -> bool:
    if not stem:
        return False
    if suffix in JONGSEONG_REQUIRED_SUFFIXES:
        return has_hangul_jongseong(stem)
    if suffix in NO_JONGSEONG_SUFFIXES:
        return not has_hangul_jongseong(stem)
    if suffix in RIEUL_OR_NO_JONGSEONG_SUFFIXES:
        return not has_hangul_jongseong(stem) or has_final_rieul(stem)
    return True


def strip_particle(token: str) -> str:
    for suffix in sorted(PARTICLE_SUFFIXES, key=len, reverse=True):
        if not token.endswith(suffix):
            continue
        stem = token[: -len(suffix)]
        if len(stem) >= 1 and should_strip_particle(stem, suffix):
            return stem
    return token


def strip_ending_suffix(token: str) -> str:
    for suffix in ENDING_SUFFIXES:
        if not token.endswith(suffix):
            continue
        stem = token[: -len(suffix)]
        if len(stem) >= 2:
            return stem
    return token


def is_index_noise_term(term: str) -> bool:
    return term in INDEX_EXCLUDED_TERMS


def iter_ngrams(token: str, min_n: int = 2, max_n: int = 5) -> Iterable[str]:
    max_n = min(max_n, len(token))
    for size in range(min_n, max_n + 1):
        for idx in range(0, len(token) - size + 1):
            yield token[idx : idx + size]


def extract_terms(text: str) -> Counter[str]:
    terms: Counter[str] = Counter()
    for raw_token in TOKEN_RE.findall(normalize_text(text)):
        particle_token = strip_particle(raw_token)
        token = strip_ending_suffix(particle_token)
        stripped_particle = particle_token != raw_token
        stripped_ending = token != particle_token
        if token.isdigit() or is_index_noise_term(token):
            continue
        if len(token) < 2 and not (stripped_particle or stripped_ending):
            continue
        terms[token] += 2
        for ngram in iter_ngrams(token):
            if not is_index_noise_term(ngram):
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


def prepare_message_for_index(
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
) -> PreparedIndexedMessage:
    message_date = created_at.date().isoformat()
    normalized = normalize_text(f"{content} {search_context}".strip())
    content_terms = extract_terms(content)
    message_terms = Counter(content_terms)
    for term, count in extract_terms(search_context).items():
        message_terms[term] += count
    return PreparedIndexedMessage(
        message_id=message_id,
        source_id=source_id,
        channel_id=channel_id,
        thread_id=thread_id,
        guild_id=guild_id,
        author_id=author_id,
        author_name=author_name,
        created_at=created_at.isoformat(),
        message_date=message_date,
        jump_url=jump_url(guild_id, channel_id, message_id),
        normalized=normalized,
        message_terms=dict(message_terms),
        content_terms=dict(content_terms),
    )


def insert_prepared_message_for_index(conn: sqlite3.Connection, message: PreparedIndexedMessage) -> bool:
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO messages (
            message_id, source_id, guild_id, channel_id, thread_id,
            author_id, author_name, created_at, message_date, jump_url
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            message.message_id,
            message.source_id,
            message.guild_id,
            message.channel_id,
            message.thread_id,
            message.author_id,
            message.author_name,
            message.created_at,
            message.message_date,
            message.jump_url,
        ),
    )
    if cursor.rowcount == 0:
        return False

    message_pk = int(cursor.lastrowid)
    if not message_pk:
        row = conn.execute(
            "SELECT message_pk FROM messages WHERE message_id = ?",
            (message.message_id,),
        ).fetchone()
        message_pk = int(row["message_pk"])

    if message.normalized:
        conn.execute(
            "INSERT INTO message_fts(rowid, index_text) VALUES (?, ?)",
            (message_pk, message.normalized),
        )

    for term, count in message.message_terms.items():
        conn.execute(
            """
            INSERT INTO message_terms(term, message_pk, source_id, message_date, count)
            VALUES (?, ?, ?, ?, ?)
            """,
            (term, message_pk, message.source_id, message.message_date, count),
        )

    for term, count in message.content_terms.items():
        conn.execute(
            """
            INSERT INTO daily_terms(source_id, message_date, term, count)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(source_id, message_date, term)
            DO UPDATE SET count = count + excluded.count
            """,
            (message.source_id, message.message_date, term, count),
        )
    return True


def insert_prepared_messages_for_index(
    conn: sqlite3.Connection,
    messages: Iterable[PreparedIndexedMessage],
) -> int:
    indexed = 0
    for message in messages:
        indexed += int(insert_prepared_message_for_index(conn, message))
    return indexed


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
    message = prepare_message_for_index(
        message_id=message_id,
        source_id=source_id,
        channel_id=channel_id,
        thread_id=thread_id,
        author_id=author_id,
        author_name=author_name,
        created_at=created_at,
        content=content,
        search_context=search_context,
        guild_id=guild_id,
    )
    return insert_prepared_message_for_index(conn, message)

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
    if not query_terms:
        return []
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

    excluded_terms = tuple(INDEX_EXCLUDED_TERMS)
    excluded_placeholders = ",".join("?" for _ in excluded_terms)
    keyword_where = " AND ".join(term_filters)
    keywords = conn.execute(
        f"""
        SELECT d.message_date, d.source_id, s.name AS source_name, d.term, d.count
        FROM daily_terms d
        JOIN sources s ON s.source_id = d.source_id
        WHERE {keyword_where}
          AND LENGTH(d.term) BETWEEN 2 AND 8
          AND d.term NOT IN ({excluded_placeholders})
        ORDER BY d.message_date, d.source_id, d.count DESC, d.term
        """,
        tuple(term_params) + excluded_terms,
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
