"""/검색 패널 QoL — 본문 미리보기, 버튼 배치, 뒤로 버튼, 푸터."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord

import mogindex_commands as cmd
import mogindex_service as svc


def test_make_snippet_windows_around_first_hit():
    text = "앞부분 " * 20 + "여기서 커피 이야기가 나온다 " + "뒷부분 " * 20
    s = svc.make_snippet(text, ["커피"], width=40)
    assert "커피" in s and s.startswith("…") and s.endswith("…") and len(s) <= 44
    assert svc.make_snippet("짧은 글", ["없음"]) == "짧은 글"
    assert svc.make_snippet("가 " * 100, ["없음"], width=20).endswith("…")   # 검색어 없으면 앞부분
    assert svc.make_snippet(None, ["x"]) == ""
    assert svc.make_snippet("줄\n바꿈\t탭", []) == "줄 바꿈 탭"
    s = svc.make_snippet("대문자 Coffee 포함", ["coffee"])
    assert "Coffee" in s                                                  # 대소문자 무시 매칭


def test_format_snippet_escapes_markdown_and_bolds_terms():
    out = cmd.MogIndexCommandsCog.format_snippet("*별표* 커피 그리고 커피우유", ["커피"])
    assert out.startswith("\\*별표\\*")
    assert out.count("**커피**") == 2 and "\n" not in out


def test_search_results_show_snippet_line():
    cog = SimpleNamespace(format_snippet=cmd.MogIndexCommandsCog.format_snippet)
    r = svc.SearchResult(1, "s", "광장", "u", "철수", "2026-09-01", "2026-09-01T00:00:00", "https://x", snippet="커피 한 잔")
    page = svc.ResultPage("키워드 검색", [r], 0, 5, 1)
    lines = cmd.MogIndexCommandsCog.format_search_results(cog, page, ["커피"])
    assert lines == ["1. [2026-09-01] 광장 / 철수\n> **커피** 한 잔\nhttps://x"]
    r.snippet = ""
    assert cmd.MogIndexCommandsCog.format_search_results(cog, page, [])[0] == "1. [2026-09-01] 광장 / 철수\nhttps://x"


def _state(**kw) -> svc.SearchPanelState:
    st = svc.SearchPanelState(session_id="s1", owner_user_id="u", guild_id="g", origin_channel_id="c")
    for k, v in kw.items():
        setattr(st, k, v)
    return st


def test_footer_is_korean_with_expiry():
    st = _state(expires_at="2026-09-24T13:05:00+09:00")
    page = svc.ResultPage("키워드 검색", [], 1, 5, 12)
    assert cmd.MogIndexCommandsCog.make_footer(None, st, page) == "3쪽 중 2쪽 · 12건 · 패널은 13:05까지"
    assert cmd.MogIndexCommandsCog.make_footer(None, st, svc.ResultPage("k", [], 0, 5, 0)) == "결과 0건 · 패널은 13:05까지"
    assert cmd.MogIndexCommandsCog.make_footer(None, _state(), None) == "기본 기간은 최근 30일입니다"


def _build(factory):
    """discord.ui.View 는 실행 중인 이벤트 루프가 필요하다."""
    async def make():
        return factory()

    return asyncio.run(make())


def _buttons(view: discord.ui.View) -> dict[int, list[discord.ui.Button]]:
    rows: dict[int, list[discord.ui.Button]] = {}
    for item in view.children:
        rows.setdefault(item.row, []).append(item)
    return rows


def test_panel_rows_are_grouped_by_role_and_share_is_not_green():
    st = _state(mode="keyword", keyword_any="커피", last_result_ids=[1])
    page = svc.ResultPage("키워드 검색", [], 0, 5, 12)
    rows = _buttons(_build(lambda: cmd.SearchPanelView(SimpleNamespace(), st, page)))
    labels = {row: [b.label for b in items] for row, items in rows.items()}
    assert labels[0] == ["키워드 검색", "둘러보기", "환장도서관", "복귀자 키워드", "범위 선택"]
    assert labels[1] == ["오늘", "7일", "30일", "전체", "기간 입력"]
    assert labels[2][:4] == ["전체 카테고리", "현재 카테고리", "현재 채널", "제외 키워드"] and labels[2][4].startswith("정렬")
    assert labels[3] == ["◀ 이전", "다음 ▶", "결과 안 검색", "내보내기", "공개 공유"]
    assert labels[4] == ["필터 리셋", "닫기"]
    assert all(len(items) <= 5 for items in rows.values())
    by_label = {b.label: b for items in rows.values() for b in items}
    assert by_label["키워드 검색"].style == discord.ButtonStyle.success        # 켜진 상태 = 초록
    assert by_label["공개 공유"].style == discord.ButtonStyle.primary          # 실행 버튼은 파랑
    assert by_label["다음 ▶"].disabled is False and by_label["◀ 이전"].disabled is True
    assert all(len(b.label) <= 10 for items in rows.values() for b in items)   # 모바일에서 잘리지 않게


def test_sort_button_disabled_when_mode_has_no_sort():
    rows = _buttons(_build(lambda: cmd.SearchPanelView(SimpleNamespace(), _state(mode="returnee"), None)))
    sort_btn = next(b for b in rows[2] if b.label.startswith("정렬"))
    assert sort_btn.disabled


def test_scope_select_views_have_back_button():
    cog = SimpleNamespace(index_categories=[])
    st = _state()
    view = _build(lambda: cmd.CategorySelectView(cog, st))
    backs = [i for i in view.children if isinstance(i, discord.ui.Button) and i.custom_id == "mogsearch:s1:back"]
    assert len(backs) == 1 and backs[0].label.endswith("돌아가기")
    cat = SimpleNamespace(key="k", name="월드맵")
    chan = _build(lambda: cmd.ChannelSelectView(cog, st, cat, []))
    thread = _build(lambda: cmd.ThreadSelectView(cog, st, "k", "1", "광장", []))
    assert any(getattr(i, "custom_id", "") == "mogsearch:s1:back" for i in chan.children)
    assert any(getattr(i, "custom_id", "") == "mogsearch:s1:back" for i in thread.children)


def test_search_command_accepts_keyword_argument():
    params = cmd.MogIndexCommandsCog.search_panel.parameters
    assert [p.display_name for p in params] == ["검색어"] and not params[0].required
