"""Kiwi 형태소 토큰화 공용 모듈.

이 모듈은 오직 ``mogindex_batch.py`` (서버 증분 색인)와 ``mogindex_backfill.py``
(전체 백필)에서만 import 되어야 한다. 봇 프로세스는 절대 이 모듈을 import 하지 않는다.

kiwipiepy(상주 ~470MB)는 반드시 ``load_kiwi`` 내부에서만 지연 import 한다.
따라서 이 모듈을 단순히 import 하는 것만으로는 Kiwi가 로드되지 않으며,
배치의 빠른 no-op 경로가 470MB를 지불하지 않고 조기 종료할 수 있다.

색인 정책(v1): 명사(NNG/NNP)와 2자 이상 라틴어(SL)만 색인한다. 동사/형용사의
활용형이나 부분 문자열 검색은 별도의 트라이그램 FTS가 담당한다.
"""

from __future__ import annotations

import os
import re
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from mogindex_debug import DISCORD_MESSAGE_URL_RE

if TYPE_CHECKING:
    from kiwipiepy import Kiwi


MODULE_DIR = Path(__file__).resolve().parent

# 토크나이저 버전. daily_terms 누적의 재현성/재토큰화 판단 기준이 된다.
# kiwipiepy는 0.21.0으로 고정, knlm 모델, 색인 규칙 v1(명사 중심)을 뜻한다.
TOKENIZER_VERSION = "kiwi-0.21.0-knlm-v1"

# 사용자 사전 기본 경로. 환경변수 MOGINDEX_USERDICT_PATH로 덮어쓸 수 있으며,
# 절대경로가 아니면 이 모듈 디렉터리를 기준으로 해석한다.
DEFAULT_USERDICT_PATH = MODULE_DIR / "mogindex_userdict.txt"

# 멘션/채널 참조(<@123>, <#123>, <@!123>, <@&123> 등) 제거용.
_MENTION_RE = re.compile(r"<[@#&!]*\d+>")
# 남은 일반 URL 제거용.
_URL_RE = re.compile(r"https?://\S+")


def resolve_userdict_path() -> Path:
    """사용자 사전 경로를 결정한다.

    환경변수 MOGINDEX_USERDICT_PATH가 있으면 그 값을, 없으면 기본 경로를 쓴다.
    상대경로면 모듈 디렉터리를 기준으로 절대경로화한다.
    """
    raw = os.getenv("MOGINDEX_USERDICT_PATH")
    if not raw or not raw.strip():
        return DEFAULT_USERDICT_PATH
    path = Path(raw)
    if not path.is_absolute():
        path = MODULE_DIR / path
    return path


def load_kiwi(userdict_path: Path | None = None) -> "Kiwi":
    """Kiwi 인스턴스를 생성한다(무거운 import는 여기서만 발생).

    knlm 모델을 쓰는 이유: 20분마다 스폰되는 서브프로세스 특성상 로딩 시간이 중요한데,
    knlm은 ~1초, 최신 기본값(CoNg)은 ~12초가 걸린다. 1-OCPU 스로틀 서버에서 이 차이가 크다.
    """
    from kiwipiepy import Kiwi  # 지연 import: 모듈 import만으로 470MB를 지불하지 않도록.

    kiwi = Kiwi(model_type="knlm")
    path = userdict_path if userdict_path is not None else resolve_userdict_path()
    if path.exists():
        added = kiwi.load_user_dictionary(str(path))
        print(f"[mogindex_kiwi] 사용자 사전 로드: {added}개 항목 ({path})")
    return kiwi


def extract_kiwi_terms(kiwi: "Kiwi", text: str) -> Counter[str]:
    """텍스트에서 색인 대상 명사/라틴어 용어를 뽑아 Counter로 돌려준다.

    Kiwi는 문장 구조를 이용하므로 소문자화/문장부호 제거 같은 파괴적 전처리는 하지 않는다.
    다만 Discord 특유의 잡음(메시지 링크/멘션/URL)은 분석 전에 공백으로 치환해 없앤다.
    """
    terms: Counter[str] = Counter()
    if not text or not text.strip():
        return terms

    cleaned = DISCORD_MESSAGE_URL_RE.sub(" ", text)
    cleaned = _MENTION_RE.sub(" ", cleaned)
    cleaned = _URL_RE.sub(" ", cleaned)

    for token in kiwi.tokenize(cleaned):
        tag = token.tag
        form = token.form
        if tag == "NNG" or tag == "NNP":
            # 일반명사/고유명사는 원형 그대로 색인한다.
            terms[form] += 1
        elif tag == "SL" and len(form) >= 2:
            # 라틴 문자열은 2자 이상만, 소문자로 정규화해 색인한다.
            terms[form.lower()] += 1
        # 그 외(동사/형용사/수사 SN/의존명사 NNB/조사/어미 등)는 v1에서 색인하지 않는다.
    return terms


def prepare_kiwi_terms(
    kiwi: "Kiwi", content: str, search_context: str
) -> tuple[Counter[str], Counter[str]]:
    """한 메시지에 대한 두 종류의 용어 집계를 만든다.

    반환값은 ``(message_terms, content_terms)`` 순서다.
    - content_terms: 메시지 원문에서만 추출. daily_terms(일자별 집계)에 들어간다.
    - message_terms: 원문 + search_context(채널/스레드명)를 합친 것. 메시지 단위 검색에 쓴다.

    비대칭(search_context를 message_terms에만 포함)은 의도된 설계다: 채널/스레드명이
    일자별 키워드 집계를 오염시키지 않으면서, 개별 메시지 검색에서는 맥락으로 잡히게 한다.
    """
    content_terms = extract_kiwi_terms(kiwi, content)
    message_terms = content_terms + extract_kiwi_terms(kiwi, search_context)
    return message_terms, content_terms


def index_pending_messages(
    conn: sqlite3.Connection,
    kiwi: "Kiwi",
    *,
    chunk_size: int = 500,
    progress: Callable[[int, int], None] | None = None,
) -> int:
    """tokenized_at IS NULL 인 메시지를 청크 단위로 토큰화/색인하는 공용 엔진.

    청크마다 하나의 트랜잭션으로 처리한다: 용어 기록(message_terms/daily_terms)과
    토큰화 도장(tokenized_at)을 같은 트랜잭션에 커밋하므로, 중간에 죽어도 전체가 롤백돼
    daily_terms 누적이 정확히 1회만 반영된다(재실행 시 깨끗하게 다시 처리).
    """
    pending_total = conn.execute(
        "SELECT COUNT(*) AS count FROM messages WHERE tokenized_at IS NULL"
    ).fetchone()["count"]
    if not pending_total:
        return 0

    done = 0
    while True:
        rows = conn.execute(
            """
            SELECT message_pk, source_id, message_date, content, search_context
            FROM messages
            WHERE tokenized_at IS NULL
            ORDER BY message_pk
            LIMIT ?
            """,
            (chunk_size,),
        ).fetchall()
        if not rows:
            break

        stamped_at = datetime.now(timezone.utc).isoformat()
        for row in rows:
            message_pk = int(row["message_pk"])
            content = row["content"]
            if content is None:
                # 원문을 한 번도 못 가져온 레거시 행: 용어 없이 도장만 찍어
                # 이후 스캔에서 영원히 다시 잡히지 않게 한다.
                conn.execute(
                    "UPDATE messages SET tokenized_at = ?, tokenizer_version = ? "
                    "WHERE message_pk = ?",
                    (stamped_at, TOKENIZER_VERSION, message_pk),
                )
                continue

            source_id = row["source_id"]
            message_date = row["message_date"]
            message_terms, content_terms = prepare_kiwi_terms(
                kiwi, content, row["search_context"] or ""
            )

            # 재토큰화 안전장치: 이 메시지의 기존 용어를 먼저 지운다.
            conn.execute(
                "DELETE FROM message_terms WHERE message_pk = ?", (message_pk,)
            )
            if message_terms:
                conn.executemany(
                    """
                    INSERT INTO message_terms(term, message_pk, source_id, message_date, count)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (term, message_pk, source_id, message_date, count)
                        for term, count in message_terms.items()
                    ],
                )
            if content_terms:
                conn.executemany(
                    """
                    INSERT INTO daily_terms(source_id, message_date, term, count)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(source_id, message_date, term)
                    DO UPDATE SET count = count + excluded.count
                    """,
                    [
                        (source_id, message_date, term, count)
                        for term, count in content_terms.items()
                    ],
                )

            conn.execute(
                "UPDATE messages SET tokenized_at = ?, tokenizer_version = ? "
                "WHERE message_pk = ?",
                (stamped_at, TOKENIZER_VERSION, message_pk),
            )

        conn.commit()
        done += len(rows)
        if progress is not None:
            progress(done, pending_total)

    return done
