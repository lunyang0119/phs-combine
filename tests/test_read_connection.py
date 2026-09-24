"""검색 읽기 연결 재사용 + 배포 파일 통계(ANALYZE)."""
from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path

import pytest

import mogindex_backfill
import mogindex_debug
import mogindex_service as svc


def _service(tmp_path: Path, name: str = "index.sqlite3") -> svc.MogIndexService:
    service = svc.MogIndexService(tmp_path / name, state_db_path=tmp_path / "state.sqlite3")
    service.initialize()
    return service


def _seed_source(db_path: Path, source_id: str) -> None:
    conn = mogindex_debug.connect(db_path)
    mogindex_debug.initialize_schema(conn)
    conn.execute(
        "INSERT INTO sources (source_id, source_kind, guild_id, name) VALUES (?,?,?,?)",
        (source_id, "channel", "g", source_id),
    )
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()


def test_index_open_reuses_connection_per_thread_with_tuning(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "READ_CACHE_KIB", 4096)
    monkeypatch.setattr(svc, "READ_MMAP_MIB", 0)
    service = _service(tmp_path)
    with service.index_open() as a:
        assert a.execute("PRAGMA cache_size").fetchone()[0] == -4096
        assert a.execute("PRAGMA query_only").fetchone()[0] == 1
    with service.index_open() as b:
        assert b is a                                   # 같은 스레드 → 같은 연결 (캐시 유지)
        with pytest.raises(sqlite3.Error):              # 읽기 전용 잠금
            b.execute("INSERT INTO sources (source_id, source_kind, guild_id, name) VALUES ('x','channel','g','x')")

    seen: list[sqlite3.Connection] = []

    def worker():
        with service.index_open() as c:
            seen.append(c)

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert seen and seen[0] is not a                    # 다른 스레드 → 다른 연결

    # 쓰기 경로(index_connect)는 여전히 새 연결이고 쓸 수 있다
    w = service.index_connect()
    w.execute("INSERT INTO sources (source_id, source_kind, guild_id, name) VALUES ('y','channel','g','y')")
    w.commit()
    w.close()
    with service.index_open() as c:
        assert c.execute("SELECT COUNT(*) FROM sources WHERE source_id='y'").fetchone()[0] == 1
    service.close_read_connection()


def test_index_open_reopens_when_db_file_replaced(tmp_path):
    service = _service(tmp_path)
    _seed_source(service.index_db_path, "old")
    with service.index_open() as a:
        assert a.execute("SELECT source_id FROM sources").fetchone()[0] == "old"
    service.close_read_connection()

    fresh = tmp_path / "fresh.sqlite3"
    _seed_source(fresh, "new")
    os.replace(fresh, service.index_db_path)            # 배포: 백필 결과 파일로 교체
    for side in ("-wal", "-shm"):
        try:
            os.remove(str(service.index_db_path) + side)
        except FileNotFoundError:
            pass

    with service.index_open() as b:
        assert b is not a
        assert b.execute("SELECT source_id FROM sources").fetchone()[0] == "new"
    service.close_read_connection()


def test_index_open_drops_connection_after_sqlite_error(tmp_path):
    service = _service(tmp_path)
    with service.index_open() as a:
        pass
    with pytest.raises(sqlite3.OperationalError):
        with service.index_open() as c:
            c.execute("SELECT * FROM no_such_table")
    with service.index_open() as b:
        assert b is not a                               # 오류 후에는 새로 연다
    service.close_read_connection()


def test_export_clean_copy_ships_planner_stats(tmp_path, capsys):
    db_path = tmp_path / "work.sqlite3"
    _seed_source(db_path, "s1")
    conn = mogindex_debug.connect(db_path)
    out = tmp_path / "deploy.sqlite3"
    assert mogindex_backfill.export_clean_copy(conn, out)
    conn.close()
    exported = sqlite3.connect(out)
    assert exported.execute("SELECT name FROM sqlite_master WHERE name='sqlite_stat1'").fetchone()
    assert exported.execute("SELECT COUNT(*) FROM sqlite_stat1").fetchone()[0] > 0
    exported.close()
    assert "ANALYZE 완료" in capsys.readouterr().out
