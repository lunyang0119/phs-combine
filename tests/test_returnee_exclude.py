"""복귀자 키워드 — '빼고 싶은 키워드' 누적 적용 (결과 안 검색처럼 여러 번 좁히기)."""
from __future__ import annotations

from datetime import date

import mogindex_service as svc


def _service(tmp_path) -> svc.MogIndexService:
    service = svc.MogIndexService(tmp_path / "index.sqlite3", state_db_path=tmp_path / "state.sqlite3")
    service.initialize()
    return service


def _seed_terms(service: svc.MogIndexService, terms: dict[str, int]) -> None:
    today = date.today().isoformat()
    with service.index_open() as conn:
        conn.execute(
            "INSERT INTO sources (source_id, source_kind, guild_id, category_id, parent_channel_id, name) VALUES (?,?,?,?,?,?)",
            ("src1", "channel", "g1", "cat-world", None, "광장"),
        )
        conn.executemany(
            "INSERT INTO daily_terms (source_id, message_date, term, count) VALUES (?,?,?,?)",
            [("src1", today, term, count) for term, count in terms.items()],
        )
        conn.commit()


def _state() -> svc.SearchPanelState:
    st = svc.SearchPanelState(session_id="s1", owner_user_id="u1", guild_id="g1", origin_channel_id="c1")
    st.mode = "returnee"
    st.returnee_limit = 10
    st.page_size = 20
    st.worldmap_category_ids = ["cat-world"]
    return st


def _terms(page: svc.TextPage) -> list[str]:
    return [line.split(". ", 1)[1].split("(")[0] for line in page.lines if line[:1].isdigit()]


def test_exclusions_accumulate_and_apply_to_returnee_list(tmp_path):
    service = _service(tmp_path)
    _seed_terms(service, {"모그텔": 9, "공지사항": 7, "카페테리아": 5, "도서관": 3})
    st = _state()

    assert _terms(service.run_returnee_keywords(st)) == ["모그텔", "공지사항", "카페테리아", "도서관"]

    service.add_not_terms(st, "공지")                     # 부분 일치: '공지사항' 도 빠진다
    assert st.mode == "returnee" and st.keyword_not == "공지"
    page = service.run_returnee_keywords(st)
    assert _terms(page) == ["모그텔", "카페테리아", "도서관"]
    assert page.lines[1] == "제외: 공지"

    service.add_not_terms(st, "도서관, 모그텔")             # 두 번째 사용: 이전 제외어에 누적
    assert st.keyword_not == "공지 도서관 모그텔"
    page = service.run_returnee_keywords(st)
    assert _terms(page) == ["카페테리아"]
    assert page.lines[1] == "제외: 공지, 도서관, 모그텔"

    service.add_not_terms(st, "공지")                     # 중복 입력은 한 번만
    assert st.keyword_not == "공지 도서관 모그텔"

    service.add_not_terms(st, "   ")                      # 빈 제출 = 제외 목록 비우기
    assert st.keyword_not is None
    assert _terms(service.run_returnee_keywords(st)) == ["모그텔", "공지사항", "카페테리아", "도서관"]


def test_all_excluded_gives_hint(tmp_path):
    service = _service(tmp_path)
    _seed_terms(service, {"모그텔": 4})
    st = _state()
    service.add_not_terms(st, "모그")
    page = service.run_returnee_keywords(st)
    assert page.lines == [] and page.total == 0
    assert "빈칸으로 제출" in page.empty_message
