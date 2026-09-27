"""복귀자 키워드 — 기간 상한, 집계 캐시, 단어 선택 → 검색, 검색어 없는 둘러보기."""
from __future__ import annotations

import asyncio
import os
from datetime import timedelta
from types import SimpleNamespace

import discord

import mogindex_commands as cmd
import mogindex_service as svc


def _today():
    # 서비스는 KST 날짜를 쓴다 — 서버(UTC) 저녁에는 로컬 날짜와 다르므로 같은 시계를 쓴다
    return svc.now_kst().date()


def _service(tmp_path, name="index.sqlite3"):
    service = svc.MogIndexService(tmp_path / name, state_db_path=tmp_path / "state.sqlite3")
    service.initialize()
    return service


def _seed(service, terms: dict[str, int], *, days_ago: int = 0, author_msgs: list[str] = ()):
    day = (_today() - timedelta(days=days_ago)).isoformat()
    conn = service.index_connect()
    try:
        conn.execute("INSERT OR IGNORE INTO sources (source_id, source_kind, guild_id, category_id, name) VALUES ('src1','channel','g1','cat-world','광장')")
        conn.executemany("INSERT OR REPLACE INTO daily_terms (source_id, message_date, term, count) VALUES (?,?,?,?)",
                         [("src1", day, t, c) for t, c in terms.items()])
        for i, d in enumerate(author_msgs):
            conn.execute(
                "INSERT INTO messages (message_id, source_id, guild_id, channel_id, author_id, author_name, created_at, message_date, jump_url) VALUES (?,?,?,?,?,?,?,?,?)",
                (f"m{i}-{d}", "src1", "g1", "c1", "u1", "나", f"{d}T00:00:00", d, "https://x"),
            )
        conn.commit()
    finally:
        conn.close()


def _state(**kw):
    st = svc.SearchPanelState(session_id="s1", owner_user_id="u1", guild_id="g1", origin_channel_id="c1")
    st.mode = "returnee"
    st.returnee_limit = 10
    st.page_size = 20
    st.worldmap_category_ids = ["cat-world"]
    for k, v in kw.items():
        setattr(st, k, v)
    return st


def test_period_caps_users_without_activity(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "RETURNEE_MAX_DAYS", 30)
    service = _service(tmp_path)
    start, today, capped = service.returnee_period(_state())
    assert capped and start == (_today() - timedelta(days=30)).isoformat() and today == _today().isoformat()

    _seed(service, {}, author_msgs=[(_today() - timedelta(days=10)).isoformat()])
    start, _today_s, capped = service.returnee_period(_state())
    assert not capped and start == (_today() - timedelta(days=9)).isoformat()   # 마지막 활동 다음 날부터

    _seed(service, {"오래된": 5}, days_ago=45)
    _seed(service, {"최근": 3}, days_ago=5)
    page = service.run_returnee_keywords(_state())
    assert "오래된" not in " ".join(page.lines) and "최근" in " ".join(page.lines)
    assert "(최근 30일로 제한)" not in page.lines[0]
    page = service.run_returnee_keywords(_state(owner_user_id="nobody"))
    assert "(최근 30일로 제한)" in page.lines[0]


def test_period_never_starts_after_today(tmp_path):
    # 오늘 이미 월드맵에 글을 썼으면 '마지막 활동 다음 날' 은 내일 — 오늘 하루로 잘라야 set_custom_dates 가 안 터진다
    service = _service(tmp_path)
    _seed(service, {}, author_msgs=[_today().isoformat()])
    start, today, _capped = service.returnee_period(_state())
    assert start == today == _today().isoformat()


def test_terms_are_cached_and_exclusions_filter_in_python(tmp_path, monkeypatch):
    service = _service(tmp_path)
    _seed(service, {"모그텔": 9, "공지사항": 7, "카페테리아": 5})
    st = _state()
    page = service.run_returnee_keywords(st)
    assert page.options == ["모그텔", "공지사항", "카페테리아"] and page.pick_more is None

    calls = []
    orig = service.index_open
    monkeypatch.setattr(service, "index_open", lambda: (calls.append(1), orig())[1])
    service.add_not_terms(st, "공지")
    page = service.run_returnee_keywords(st)
    assert page.options == ["모그텔", "카페테리아"] and page.lines[1] == "제외: 공지"
    # returnee_period 의 last_seen 조회 1번뿐 — 집계 쿼리는 캐시에서 온다
    assert len(calls) == 1

    monkeypatch.setattr(svc, "RETURNEE_CACHE_TTL_SEC", 0)
    _seed(service, {"새단어": 8})
    page = service.run_returnee_keywords(st)
    assert "새단어" in page.options                                       # TTL 지나면 다시 집계


def test_cache_misses_when_index_file_is_replaced(tmp_path):
    # 배포 파일이 교체되면(VACUUM INTO 산출물 배포) TTL 이 남아 있어도 옛 집계를 주면 안 된다
    service = _service(tmp_path)
    _seed(service, {"옛단어": 9})
    st = _state()
    assert service.run_returnee_keywords(st).options == ["옛단어"]

    fresh = _service(tmp_path, "fresh.sqlite3")
    _seed(fresh, {"새단어": 9})
    fresh.close_read_connection()
    service.close_read_connection()                     # 윈도우에서는 열린 파일을 바꿔치기할 수 없다
    os.replace(tmp_path / "fresh.sqlite3", tmp_path / "index.sqlite3")

    assert service.run_returnee_keywords(_state()).options == ["새단어"]


def test_pick_menu_is_chunked_with_a_more_option(tmp_path):
    service = _service(tmp_path)
    _seed(service, {f"단어{i:02d}": 100 - i for i in range(30)})
    st = _state(returnee_limit=30)

    page = service.run_returnee_keywords(st)
    assert page.options == [f"단어{i:02d}" for i in range(24)]
    assert page.pick_more == "여기 없음 → 다음 단어 보기 (25~30번)"

    st.returnee_pick_page = 1
    page = service.run_returnee_keywords(st)
    assert page.options == [f"단어{i:02d}" for i in range(24, 30)]
    assert page.pick_more == "여기 없음 → 처음 단어로 (1~24번)"

    st.returnee_pick_page = 2                            # 끝을 넘기면 처음으로
    page = service.run_returnee_keywords(st)
    assert st.returnee_pick_page == 0 and page.options[0] == "단어00"

    cog = SimpleNamespace(service=service)
    cmd.MogIndexCommandsCog.apply_action(cog, st, "returnee_pick_more", {})
    assert st.returnee_pick_page == 1
    service.add_not_terms(st, "단어2")                   # 목록이 바뀌면 첫 묶음으로
    assert st.returnee_pick_page == 0
    page = service.run_returnee_keywords(st)
    assert len(page.options) == 20 and page.pick_more is None


def test_pick_term_switches_to_keyword_search_within_returnee_period(tmp_path):
    service = _service(tmp_path)
    _seed(service, {"커피": 4}, author_msgs=[(_today() - timedelta(days=20)).isoformat()])
    cog = SimpleNamespace(service=service)
    st = _state(keyword_not="공지")
    service.run_returnee_keywords(st)                    # 목록을 그리면 기간이 상태에 남는다
    assert st.returnee_start == (_today() - timedelta(days=19)).isoformat() and st.returnee_end == _today().isoformat()

    # 목록을 본 뒤 오늘 월드맵에 글을 썼어도, 선택 검색은 목록에 적힌 기간을 그대로 쓴다 (기간 재계산이면 시작일이 내일이 돼 실패했다)
    _seed(service, {}, author_msgs=[_today().isoformat()])
    cmd.MogIndexCommandsCog.apply_action(cog, st, "returnee_pick", {"term": "커피"})
    assert st.mode == "keyword" and st.keyword_any == "커피" and st.keyword_not == "공지"
    assert st.date_preset == "custom" and st.start_date == (_today() - timedelta(days=19)).isoformat()
    assert st.end_date == _today().isoformat()

    # 저장된 기간이 없으면(옛 세션) 지금 계산한다
    st2 = _state()
    cmd.MogIndexCommandsCog.apply_action(cog, st2, "returnee_pick", {"term": "커피"})
    assert st2.start_date == st2.end_date == _today().isoformat()


def test_browse_action_and_empty_keyword_modal_go_to_recent():
    cog = SimpleNamespace(service=svc.MogIndexService.__new__(svc.MogIndexService))
    st = _state(mode="hub", page=3)
    cmd.MogIndexCommandsCog.apply_action(cog, st, "browse", {})
    assert st.mode == "recent" and st.page == 0


def test_returnee_panel_has_pick_select_and_compact_rows():
    async def make():
        st = _state()
        page = svc.TextPage("복귀자 키워드", ["기간: a .. b", "1. 커피(4)"], 0, 5, 2, options=["커피", "빵"])
        return cmd.SearchPanelView(SimpleNamespace(), st, page)

    view = asyncio.run(make())
    rows: dict[int, list] = {}
    for item in view.children:
        rows.setdefault(item.row, []).append(item)
    assert [b.label for b in rows[3]] == ["◀ 이전", "다음 ▶", "공개 공유", "필터 리셋", "닫기"]
    select = rows[4][0]
    assert isinstance(select, discord.ui.Select) and [o.value for o in select.options] == ["커피", "빵"]
    assert select.custom_id == "mogsearch:s1:returnee_pick"


def test_returnee_panel_select_adds_more_option_as_25th():
    async def make():
        terms = [f"단어{i:02d}" for i in range(24)]
        page = svc.TextPage("복귀자 키워드", ["기간: a .. b"], 0, 5, 1, options=terms, pick_more="여기 없음 → 다음 단어 보기 (25~30번)")
        return cmd.SearchPanelView(SimpleNamespace(), _state(), page)

    view = asyncio.run(make())
    select = next(item for item in view.children if isinstance(item, discord.ui.Select))
    assert len(select.options) == 25
    assert select.options[-1].value == cmd.RETURNEE_PICK_MORE and select.options[-1].label.startswith("여기 없음")
    assert [o.value for o in select.options[:-1]] == [f"단어{i:02d}" for i in range(24)]
