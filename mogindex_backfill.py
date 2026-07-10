"""
개발 PC용 Kiwi 형태소 색인 백필 CLI (일회성 실행).

DB에 이미 저장된 원문(messages.content)을 형태소 분석기(Kiwi)로 다시 토큰화해
용어 색인(message_terms / daily_terms)을 통째로 재구축한다. 이어서 문서빈도(DF)
기반 말뭉치 불용어(df_stopwords)를 산출하고, 필요하면 서버 이관용 단일 파일 DB를
내보낸다. (원문을 채우는 Discord 재수집 단계는 이 스크립트의 책임이 아니다.)

토큰화 자체는 병렬 개발 중인 mogindex_kiwi 모듈에 위임한다(지연 import).

사용 예:
    python mogindex_backfill.py
    python mogindex_backfill.py --restart --chunk-size 2000
    python mogindex_backfill.py --df-cutoff-pct 30 --df-min-docs 200
    python mogindex_backfill.py --export mogindex/search_index_server.sqlite3
    python mogindex_backfill.py --export-only --export mogindex/search_index_server.sqlite3
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from mogindex_batch import acquire_batch_lock
from mogindex_debug import connect, initialize_schema, set_meta

MODULE_DIR = Path(__file__).resolve().parent


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def resolve_db_path(raw: str | os.PathLike[str]) -> Path:
    """mogindex_service.py 와 동일한 규칙: 상대경로면 이 모듈 디렉터리 기준으로 해석."""
    db_path = Path(raw)
    if not db_path.is_absolute():
        db_path = MODULE_DIR / db_path
    return db_path


def format_hms(seconds: float) -> str:
    total = int(max(seconds, 0))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Kiwi 형태소 색인 백필 CLI (개발 PC 일회성 실행)."
    )
    parser.add_argument(
        "--db",
        default=os.getenv("MOGINDEX_DB_PATH", "mogindex/search_index_live_debug.sqlite3"),
        help="SQLite DB 경로. 상대경로면 이 모듈 기준. 기본값 env MOGINDEX_DB_PATH",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=5000,
        help="트랜잭션/커밋 단위 메시지 수 (기본 5000)",
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        help="이어서 진행하지 않고 색인을 처음부터 다시 만든다",
    )
    parser.add_argument(
        "--df-cutoff-pct",
        type=float,
        default=env_float("MOGINDEX_DF_CUTOFF_PCT", 25.0),
        help="DF 비율(퍼센트)이 이 값을 초과하는 용어를 소스별 불용어로 지정",
    )
    parser.add_argument(
        "--df-min-docs",
        type=int,
        default=env_int("MOGINDEX_DF_MIN_DOCS", 100),
        help="DF 산출에서 제외할 소스의 최소 (토큰화된, 원문 있는) 문서 수",
    )
    parser.add_argument(
        "--userdict",
        type=Path,
        default=None,
        help="Kiwi 사용자 사전 경로 override (미지정 시 mogindex_kiwi 기본값)",
    )
    parser.add_argument(
        "--export",
        type=Path,
        default=None,
        help="색인 후 클린 단일 파일 DB로 내보낼 경로 (VACUUM INTO)",
    )
    parser.add_argument(
        "--export-only",
        action="store_true",
        help="색인/DF 산출 없이 내보내기만 수행 (--export 필요)",
    )
    return parser


def wipe_index(conn: sqlite3.Connection) -> None:
    """용어 색인을 초기화하고 모든 메시지를 재토큰화 대상으로 되돌린다."""
    term_count = conn.execute("SELECT COUNT(*) AS n FROM message_terms").fetchone()["n"]
    daily_count = conn.execute("SELECT COUNT(*) AS n FROM daily_terms").fetchone()["n"]
    stamped = conn.execute(
        "SELECT COUNT(*) AS n FROM messages WHERE tokenized_at IS NOT NULL"
    ).fetchone()["n"]
    print(
        "기존 색인을 초기화합니다: "
        f"message_terms={term_count}, daily_terms={daily_count}, "
        f"재토큰화 대상 messages={stamped}"
    )
    conn.execute("DELETE FROM message_terms")
    conn.execute("DELETE FROM daily_terms")
    conn.execute("UPDATE messages SET tokenized_at = NULL, tokenizer_version = NULL")


def derive_df_stopwords(conn: sqlite3.Connection, cutoff_ratio: float, min_docs: int) -> int:
    """소스별 문서빈도(DF)가 cutoff 를 초과하는 용어를 df_stopwords 로 산출한다.

    분자와 분모 모두 '원문이 있는(content != '') 메시지' 기준으로 센다:
    message_terms PK 가 (term, message_pk) 이므로 messages 와 조인해 세면
    '그 용어를 포함한 원문 메시지 수' 가 된다. (분자만 전체 message_terms 로 세면
    search_context 전용 메시지 때문에 df_ratio 가 1.0 을 넘을 수 있다.)
    min_docs 미만인 소스는 통째로 제외한다(작은 소스는 거의 모든 용어가 불용어가 됨).
    """
    conn.execute("DELETE FROM df_stopwords")
    conn.execute(
        """
        INSERT INTO df_stopwords (source_id, term, df_ratio)
        SELECT mt.source_id, mt.term, CAST(COUNT(*) AS REAL) / totals.doc_count
        FROM message_terms mt
        JOIN messages m
            ON m.message_pk = mt.message_pk
            AND m.content IS NOT NULL AND m.content != ''
        JOIN (
            SELECT source_id, COUNT(*) AS doc_count
            FROM messages
            WHERE tokenized_at IS NOT NULL AND content IS NOT NULL AND content != ''
            GROUP BY source_id
            HAVING COUNT(*) >= :min_docs
        ) totals ON totals.source_id = mt.source_id
        GROUP BY mt.source_id, mt.term
        HAVING CAST(COUNT(*) AS REAL) / totals.doc_count > :cutoff_ratio
        """,
        {"min_docs": min_docs, "cutoff_ratio": cutoff_ratio},
    )
    conn.commit()

    total = conn.execute("SELECT COUNT(*) AS n FROM df_stopwords").fetchone()["n"]
    top_sources = conn.execute(
        """
        SELECT df.source_id AS source_id,
               COALESCE(s.name, df.source_id) AS name,
               COUNT(*) AS n
        FROM df_stopwords df
        LEFT JOIN sources s ON s.source_id = df.source_id
        GROUP BY df.source_id
        ORDER BY n DESC, df.source_id
        LIMIT 10
        """
    ).fetchall()

    print(f"DF 불용어 {total}개 산출 (상위 소스):")
    if not top_sources:
        print("  (조건을 만족하는 불용어가 없습니다. --df-min-docs / --df-cutoff-pct 를 확인하세요.)")
    for row in top_sources:
        print(f"  {row['name']} ({row['source_id']}): {row['n']}")
    return total


def print_transfer_instructions(export_path: Path) -> None:
    print()
    print("=== 서버 이관 안내 ===")
    print("1. 서버에서 봇을 정지합니다.")
    print("2. 내보낸 파일을 서버의 MOGINDEX_DB_PATH 위치로 복사합니다:")
    print(f"     scp \"{export_path}\" <서버>:<MOGINDEX_DB_PATH>")
    print("3. 서버에 남아있는 오래된 사이드카 파일(-wal / -shm)을 삭제합니다.")
    print("   (VACUUM INTO 결과물에는 사이드카가 없으므로, 남아있으면 안 됩니다.)")
    print("     rm -f <MOGINDEX_DB_PATH>-wal <MOGINDEX_DB_PATH>-shm")
    print("4. 사용자 사전(mogindex_userdict.txt)을 같은 위치에 함께 복사합니다.")
    print("5. 봇을 다시 시작합니다.")
    print("6. 검증을 위해 서버에서 배치를 한 번 수동 실행합니다:")
    print("     python mogindex_batch.py")


def export_clean_copy(conn: sqlite3.Connection, export_path: Path) -> bool:
    """WAL 을 본체로 합친 뒤 VACUUM INTO 로 사이드카 없는 단일 파일 DB를 만든다."""
    if export_path.exists():
        print(f"[오류] 내보내기 대상이 이미 존재합니다: {export_path}")
        print("       VACUUM INTO 는 기존 파일을 덮어쓰지 않습니다. 다른 경로를 쓰거나 기존 파일을 지워주세요.")
        return False
    export_path.parent.mkdir(parents=True, exist_ok=True)
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.execute("VACUUM INTO ?", (str(export_path),))
    print(f"클린 DB를 내보냈습니다: {export_path}")
    print_transfer_instructions(export_path)
    return True


def run_backfill(
    conn: sqlite3.Connection,
    args: argparse.Namespace,
    db_path: Path,
    started: float,
) -> int:
    export_path: Path | None = args.export
    print(f"DB: {db_path}")

    if args.export_only:
        # --export-only: 색인/DF 를 건너뛰고 내보내기만. kiwipiepy 없이도 동작한다.
        assert export_path is not None  # main 에서 --export 동반을 검증함
        print("내보내기 전용 모드입니다. 색인과 DF 산출을 건너뜁니다.")
        return 0 if export_clean_copy(conn, export_path) else 1

    # 1) 재개/와이프 판단 (mogindex_kiwi 모듈 import 는 가볍다 —
    #    kiwipiepy 자체는 load_kiwi() 안에서만 import 된다)
    import mogindex_kiwi

    # 와이프 생략(이어하기) 안전 조건 — backfill_state 메타가 아니라 데이터 자체로
    # 판단한다: (1) 다른 토크나이저 버전으로 토큰화된 행이 없고, (2) 토큰화 도장
    # 없이 용어만 있는 행(도장 없이 용어를 쓰던 구버전 휴리스틱 색인의 잔재)도
    # 없어야 한다. 잔재가 있으면 daily_terms 도 함께 오염돼 있으므로(용어-도장
    # 동일 트랜잭션 규칙 이전 데이터) 전체 와이프가 필요하다. 이 조건이면 중단된
    # 백필 재개든 증분 배치가 미리 해둔 작업이든 안전하게 이어받는다.
    # 사용자 사전을 바꿔서 전부 다시 만들어야 하면 --restart 를 쓴다.
    resuming = False
    if not args.restart:
        stale = conn.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE tokenized_at IS NOT NULL"
            " AND (tokenizer_version IS NULL OR tokenizer_version != ?)",
            (mogindex_kiwi.TOKENIZER_VERSION,),
        ).fetchone()["n"]
        # idx_message_terms_message 덕에 이 검사는 빠르다.
        legacy_orphan = conn.execute(
            "SELECT 1 FROM messages m JOIN message_terms mt ON mt.message_pk = m.message_pk"
            " WHERE m.tokenized_at IS NULL LIMIT 1"
        ).fetchone()
        if stale == 0 and legacy_orphan is None:
            resuming = True
            done_rows = conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE tokenized_at IS NOT NULL"
            ).fetchone()["n"]
            if done_rows:
                print(
                    f"현재 버전({mogindex_kiwi.TOKENIZER_VERSION})으로 이미 토큰화된 "
                    f"{done_rows}건은 유지하고, 남은 대기분만 처리합니다."
                )

    if not resuming:
        wipe_index(conn)
    set_meta(conn, "backfill_state", "in_progress")
    conn.commit()

    # 2) 대기분 확인 — 없으면 Kiwi 로드(~470MB) 자체를 건너뛴다.
    pending = conn.execute(
        "SELECT COUNT(*) AS n FROM messages WHERE tokenized_at IS NULL"
    ).fetchone()["n"]

    processed = 0
    if pending == 0:
        print("토큰화 대기 메시지가 없습니다 — 색인 단계를 건너뜁니다.")
    else:
        # 3) Kiwi 로드 + 색인 (청크 단위 커밋 + 진행률 출력)
        userdict_path: Path | None = args.userdict
        effective_userdict = userdict_path or mogindex_kiwi.resolve_userdict_path()
        print(f"사용자 사전: {effective_userdict}")
        load_started = time.monotonic()
        kiwi = mogindex_kiwi.load_kiwi(userdict_path)
        print(f"Kiwi 로드 완료 ({time.monotonic() - load_started:.1f}s)")

        index_started = time.monotonic()

        def on_progress(done: int, total: int) -> None:
            elapsed = time.monotonic() - index_started
            rate = done / elapsed if elapsed > 0 else 0.0
            remaining = max(total - done, 0)
            eta = remaining / rate if rate > 0 else 0.0
            pct = (done / total * 100.0) if total else 100.0
            print(
                f"  {done}/{total} ({pct:.1f}%) | {rate:.0f} msg/s | ETA {format_hms(eta)}",
                flush=True,
            )

        print(f"색인 시작 (chunk_size={args.chunk_size}) ...")
        processed = mogindex_kiwi.index_pending_messages(
            conn, kiwi, chunk_size=args.chunk_size, progress=on_progress
        )
        print(
            f"색인 완료: 이번 실행에서 {processed}건 처리 "
            f"({format_hms(time.monotonic() - index_started)})"
        )

    # 4) DF 불용어 산출
    cutoff_ratio = args.df_cutoff_pct / 100.0
    print(
        f"DF 불용어 산출 중 "
        f"(cutoff={args.df_cutoff_pct}퍼센트 → ratio>{cutoff_ratio:.4f}, min_docs={args.df_min_docs}) ..."
    )
    stopword_count = derive_df_stopwords(conn, cutoff_ratio, args.df_min_docs)

    # 5) 메타 갱신
    set_meta(conn, "backfill_state", "done")
    set_meta(conn, "last_backfill_at", datetime.now(timezone.utc).isoformat())
    set_meta(conn, "tokenizer_version", mogindex_kiwi.TOKENIZER_VERSION)
    conn.commit()

    # 6) 선택적 내보내기
    export_ok = True
    if export_path is not None:
        export_ok = export_clean_copy(conn, export_path)

    # 7) 최종 요약
    tokenized = conn.execute(
        "SELECT COUNT(*) AS n FROM messages WHERE tokenized_at IS NOT NULL"
    ).fetchone()["n"]
    print()
    print("=== 백필 요약 ===")
    print(f"토큰화된 메시지: {tokenized}")
    print(f"DF 불용어: {stopword_count}")
    print(f"총 소요: {format_hms(time.monotonic() - started)}")
    return 0 if export_ok else 1


def main(argv: list[str] | None = None) -> int:
    # 봇이 파이프로 스폰하면 stdout이 블록 버퍼링돼 진행 로그가 한참 뒤에 몰려 나온다.
    # 라인 버퍼링으로 강제해 매 print가 즉시 봇 로그/상태확인에 반영되게 한다.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.export_only and not args.export:
        parser.error("--export-only 를 쓰려면 --export PATH 가 필요합니다.")

    db_path = resolve_db_path(args.db)
    started = time.monotonic()
    lock_conn: sqlite3.Connection | None = None
    conn: sqlite3.Connection | None = None
    try:
        # 증분 배치(mogindex_batch)와 같은 락을 잡아 동시 실행을 막는다.
        # 와이프 도중 배치가 daily_terms 를 누적하면 이중 집계가 되므로 필수.
        lock_conn = acquire_batch_lock(db_path)
        if lock_conn is None:
            print(
                "[오류] 증분 색인 배치(mogindex_batch)가 실행 중입니다. "
                "배치가 끝난 뒤 다시 시도해주세요."
            )
            return 1
        conn = connect(db_path)
        initialize_schema(conn)
        return run_backfill(conn, args, db_path, started)
    except KeyboardInterrupt:
        # 완료된 청크는 이미 커밋됨. backfill_state 는 "in_progress" 로 남아 재실행 시 이어짐.
        print("\n중단됨 — 다시 실행하면 이어서 진행됩니다.")
        return 130
    except Exception:
        # backfill_state 는 "in_progress" 로 남으므로 재실행하면 남은 부분만 처리한다.
        traceback.print_exc()
        return 1
    finally:
        if conn is not None:
            conn.close()
        if lock_conn is not None:
            lock_conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
