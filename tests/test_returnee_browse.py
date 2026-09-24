"""복귀자 키워드 — 기간 상한, 집계 캐시, 단어 선택 → 검색, 검색어 없는 둘러보기."""
from __future__ import annotations

import asyncio
from datetime import date, timedelta
from types import SimpleNamespace

import discord

import mogindex_commands as cmd
import mogindex_service as svc


def _service(tmp_path):
    service = svc.MogIndexService(tmp_path / "index.sqlite3", state_db_path=tmp_path / "state.sqlite3")
    service.initialize()
    return service


def _seed(service, terms: dict[str, int], *, days_ago: int = 0, author_msgs: list[str] = ()):
    day = (date.today() - timedelta(days=days_ago)).isoformat()
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
    assert capped and start == (date.today() - timedelta(days=30)).isoformat() and today == date.today().isoformat()

    _seed(service, {}, author_msgs=[(date.today() - timedelta(days=10)).isoformat()])
    start, _today, capped = service.returnee_period(_state())
    assert not capped and start == (date.today() - timedelta(days=9)).isoformat()   # 마지막 활동 다음 날부터

    _seed(service, {"오래된": 5}, days_ago=45)
    _seed(service, {"최근": 3}, days_ago=5)
    page = service.run_returnee_keywords(_state())
    assert "오래된" not in " ".join(page.lines) and "최근" in " ".join(page.lines)
    assert "(최근 30일로 제한)" not in page.lines[0]
    page = service.run_returnee_keywords(_state(owner_user_id="nobody"))
    assert "(최근 30일로 제한)" in page.lines[0]


def test_terms_are_cached_and_exclusions_filter_in_python(tmp_path, monkeypatch):
    service = _service(tmp_path)
    _seed(service, {"모그텔": 9, "공지사항": 7, "카페테리아": 5})
    st = _state()
    page = service.run_returnee_keywords(st)
    assert page.options == ["모그텔", "공지사항", "카페테리아"]

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


def test_pick_term_switches_to_keyword_search_within_returnee_period(tmp_path):
    service = _service(tmp_path)
    _seed(service, {"커피": 4}, author_msgs=[(date.today() - timedelta(days=20)).isoformat()])
    cog = SimpleNamespace(service=service)
    st = _state(keyword_not="공지")
    cmd.MogIndexCommandsCog.apply_action(cog, st, "returnee_pick", {"term": "커피"})
    assert st.mode == "keyword" and st.keyword_any == "커피" and st.keyword_not == "공지"
    assert st.date_preset == "custom" and st.start_date == (date.today() - timedelta(days=19)).isoformat()
    assert st.end_date == date.today().isoformat()


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
