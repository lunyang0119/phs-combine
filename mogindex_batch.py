"""서버측 증분 색인 배치(단명 서브프로세스).

2단계 색인 아키텍처:
  1) 봇 프로세스: 메시지 원문(content/search_context)만 SQLite에 저장한다.
     Kiwi(~470MB)를 절대 import 하지 않는다(1GB RAM 서버 보호).
  2) 이 배치: 봇이 N분마다 짧게 스폰하는 별도 프로세스. Kiwi를 로드해
     tokenized_at IS NULL 인 대기 행만 토큰화/색인하고 즉시 종료한다.

즉, 무거운 형태소 분석 비용은 봇 상주 메모리와 분리된 단명 프로세스에서만 지불한다.

사용법:
    python mogindex_batch.py [--db PATH] [--chunk-size N] [--userdict PATH]
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import traceback
from datetime import datetime, timezone
from pathlib import Path

from mogindex_debug import connect, initialize_schema, set_meta

MODULE_DIR = Path(__file__).resolve().parent

DEFAULT_DB_RELATIVE = "mogindex/search_index_live_debug.sqlite3"


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def resolve_under_module(path: Path) -> Path:
    """상대경로는 이 모듈 디렉터리 기준으로 해석한다(mogindex_service.py와 동일한 관례)."""
    return path if path.is_absolute() else MODULE_DIR / path


def acquire_batch_lock(db_path: Path) -> sqlite3.Connection | None:
    """단일 인스턴스 실행 락을 잡는다.

    색인 DB와 분리된 별도 락 파일(``<db>.batchlock``)에 BEGIN IMMEDIATE로 쓰기 락을
    건다. 이미 다른 배치가 잡고 있으면 (busy_timeout=0이라) 즉시 실패하고 None을 돌려준다.
    반환된 커넥션은 프로세스 종료 시점까지 열어 두어야 락이 유지된다.

    isolation_level=None으로 자동 트랜잭션 관리를 끄고 BEGIN IMMEDIATE를 직접 제어한다.
    """
    lock_path = Path(str(db_path) + ".batchlock")
    # 락 파일을 만들기 전에 부모 디렉터리를 보장한다. 없으면 connect 자체가 실패해
    # "이미 실행 중"으로 오인될 수 있다.
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_conn = sqlite3.connect(lock_path, isolation_level=None)
    lock_conn.execute("PRAGMA busy_timeout = 0")
    try:
        lock_conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError:
        lock_conn.close()
        return None
    return lock_conn


def main(argv: list[str] | None = None) -> int:
    lock_conn: sqlite3.Connection | None = None
    conn: sqlite3.Connection | None = None
    try:
        # 1) OOM 실드(Linux 전용): 메모리가 바닥나면 커널이 봇이 아니라 이 배치를 죽이도록.
        #    Windows 등에는 /proc가 없으므로 조용히 무시한다.
        try:
            with open("/proc/self/oom_score_adj", "w") as handle:
                handle.write("1000")
        except OSError:
            pass

        # 2) 인자 파싱.
        parser = argparse.ArgumentParser(
            description="Mogtel 증분 색인 배치(대기 중인 메시지를 Kiwi로 토큰화)."
        )
        parser.add_argument(
            "--db",
            type=Path,
            default=None,
            help="색인 SQLite 경로(기본: env MOGINDEX_DB_PATH 또는 mogindex/search_index_live_debug.sqlite3)",
        )
        parser.add_argument(
            "--chunk-size",
            type=int,
            default=env_int("MOGINDEX_BATCH_CHUNK_SIZE", 500),
            help="한 트랜잭션에 처리할 메시지 수(기본: env MOGINDEX_BATCH_CHUNK_SIZE 또는 500)",
        )
        parser.add_argument(
            "--userdict",
            type=Path,
            default=None,
            help="Kiwi 사용자 사전 경로 재정의(선택)",
        )
        args = parser.parse_args(argv)

        db_raw = args.db if args.db is not None else Path(
            os.getenv("MOGINDEX_DB_PATH", DEFAULT_DB_RELATIVE)
        )
        db_path = resolve_under_module(db_raw)
        chunk_size = args.chunk_size
        userdict_path = (
            resolve_under_module(args.userdict) if args.userdict is not None else None
        )

        # 3) 단일 인스턴스 락. 이미 실행 중이면 정상 종료(에러 아님).
        lock_conn = acquire_batch_lock(db_path)
        if lock_conn is None:
            print("[mogindex_batch] 이미 실행 중인 배치가 있어 종료합니다.")
            return 0

        # 4) 색인 DB 연결 + 스키마 보장(마이그레이션 포함, 멱등).
        conn = connect(db_path)
        initialize_schema(conn)

        # 5) 빠른 no-op 경로: 대기 행이 없으면 Kiwi/kiwipiepy를 import 하지 않고 끝낸다.
        #    이 배치는 20분마다 돈다 — 처리할 게 없을 때 470MB를 지불하지 않는다.
        pending = conn.execute(
            "SELECT COUNT(*) AS count FROM messages WHERE tokenized_at IS NULL"
        ).fetchone()["count"]
        if pending == 0:
            set_meta(conn, "last_batch_run", datetime.now(timezone.utc).isoformat())
            conn.commit()
            print("[mogindex_batch] 처리 0건 (대기 중인 메시지 없음).")
            return 0

        # 6) 대기 행이 있을 때만 Kiwi를 로드해 실제 색인을 수행한다.
        import mogindex_kiwi

        kiwi = mogindex_kiwi.load_kiwi(userdict_path)

        def report(done: int, total: int) -> None:
            print(f"[mogindex_batch] {done}/{total}")

        processed = mogindex_kiwi.index_pending_messages(
            conn, kiwi, chunk_size=chunk_size, progress=report
        )

        # 7) 메타 갱신 후 커밋.
        set_meta(conn, "last_batch_run", datetime.now(timezone.utc).isoformat())
        set_meta(conn, "tokenizer_version", mogindex_kiwi.TOKENIZER_VERSION)
        conn.commit()
        print(f"[mogindex_batch] 처리 완료: {processed}건")
        return 0

    except Exception:
        # 8) 예기치 못한 예외는 트레이스백을 남기고 종료코드 1(봇이 로그로 확인).
        traceback.print_exc()
        return 1
    finally:
        if conn is not None:
            conn.close()
        if lock_conn is not None:
            lock_conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
