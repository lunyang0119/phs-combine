"""디버그 자동 헌터(bot_player) — 파싱, 프롬프트 누출 방지, cog 연동."""
import asyncio
import json
import random
from types import SimpleNamespace

from fugitive import bot_player as BP
from fugitive import config as cfg
from fugitive import engine as E
from fugitive.cog import FugitiveCog

from tests.test_cog_timer import FakeChannel, _make_cog


def test_parse_decision_variants():
    d = BP.parse_decision('{"order": "move", "target": "b2", "reason": "가까워서"}')
    assert (d.order, d.target, d.reason) == ("move", "B2", "가까워서")
    d = BP.parse_decision('```json\n{"order":"search","target":null,"reason":"여기"}\n```')
    assert d.order == "search" and d.target is None
    d = BP.parse_decision("그냥 대기할게요")
    assert d.order == "stay" and d.error == "no_json"
    d = BP.parse_decision('{"order": "fly", "reason": "x"}')
    assert d.order == "stay" and d.error == "bad_order"


def _debug_state(n=4):
    hunters = BP.bot_hunters(n)
    c, _ = cfg.build_config(n)
    st = E.new_game(E.GameMap.builtin("car2077_9"), c, hunters, fugitive_start="A2", rng=random.Random(1))
    st.channel_id, st.round_message_id, st.debug_bots = 1, 10, True
    return st


def test_prompt_never_mentions_fugitive_room():
    st = _debug_state()
    st.fugitive.room = "C3"
    # 헌터가 C3 근처에 없도록 배치해 두고, 프롬프트에 C3 가 도주자 위치로 새지 않는지 본다
    for h in st.hunters.values():
        h.room = "A1"
    p = BP.build_prompt(st, "bot:1", [])
    # true_state_text 에만 있는 문구들이 프롬프트에 없어야 한다
    assert "도주자:" not in p and "RAM" not in p and "실제 상태" not in p
    assert "C3" in p                        # 격자 라벨로는 당연히 나온다 (표식이 아니라 방 이름)
    assert "커피포트" in p and "A1" in p       # 자기 위치와 장치는 들어간다


class FakeGemini:
    """generate_content 를 흉내낸다. 스크립트대로 답하거나 예외를 던진다."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.models = self

    def generate_content(self, model, contents, config=None):
        self.calls += 1
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(text=json.dumps(item, ensure_ascii=False))


def test_bot_turn_submits_orders_and_records_reasons():
    async def body():
        ch = FakeChannel()
        cog = _make_cog(ch)
        cog.state = _debug_state(3)
        cog.bot_player = BP.GeminiPlayer(client=FakeGemini([
            {"order": "search", "target": None, "reason": "제자리 수색"},
            {"order": "move", "target": "Z9", "reason": "엉뚱한 곳"},          # 규칙 위반 → 대기로 대체
            RuntimeError("quota"),                                              # API 오류 → 대기
        ]))
        cog._schedule_bot_turn()
        await asyncio.wait_for(cog.bot_task, 5)
        st = cog.state
        assert st.all_hunters_submitted()
        orders = {uid: h.order.type for uid, h in st.hunters.items()}
        assert orders["bot:1"] == "search" and orders["bot:2"] == "stay" and orders["bot:3"] == "stay"
        rows = st.bot_reasons
        assert [r["accepted"] for r in rows] == [True, False, True]
        assert "제자리 수색" in rows[0]["reason"] and rows[1]["note"]
        assert rows[2]["error"] == "api_error"
        msgs = cog.control_msgs
        # 봇마다: 판단 중 → 선택 게시 (→ 거부 안내) 순서로 관제 채널에 나간다
        assert "판단 중" in msgs[0] and "제미나이-1" in msgs[0]
        assert "제자리 수색" in msgs[1] and "수색" in msgs[1]
        assert any("규칙에 맞지 않아" in m and "제미나이-2" in m for m in msgs)
        assert msgs.index(next(m for m in msgs if "제미나이-1" in m and "→" in m)) < msgs.index(next(m for m in msgs if "제미나이-2" in m and "판단 중" in m))
        # 도주자가 아직 미제출이라 유예 후 정산 예약만 되어 있어야 한다
        assert st.status == E.ROUND_OPEN
        cog._cancel_timer()

    asyncio.run(body())


def test_bot_turn_discarded_if_round_moved_on():
    async def body():
        ch = FakeChannel()
        cog = _make_cog(ch)
        cog.state = _debug_state(2)
        cog.bot_player = BP.GeminiPlayer(client=FakeGemini([
            {"order": "search", "reason": "a"}, {"order": "search", "reason": "b"},
        ]))
        cog._schedule_bot_turn()
        cog.state.round_no = 2           # 판단이 돌아오기 전에 라운드가 넘어간 상황
        await asyncio.wait_for(cog.bot_task, 5)
        assert not cog.state.bot_reasons
        assert all(h.order is None for h in cog.state.hunters.values())

    asyncio.run(body())


def test_state_roundtrip_keeps_bot_reasons():
    st = _debug_state(2)
    st.bot_reasons.append({"round": 1, "uid": "bot:1", "name": "x", "order": "stay", "target": None,
                           "reason": "r", "accepted": True, "note": "", "error": None})
    back = E.GameState.from_dict(json.loads(json.dumps(st.to_dict())))
    assert back.debug_bots and back.bot_reasons[0]["reason"] == "r"


def test_transient_error_retries_then_falls_back(monkeypatch):
    monkeypatch.setattr(BP, "RETRY_ATTEMPTS", 2)
    monkeypatch.setattr(BP, "RETRY_BASE_SEC", 0.0)
    monkeypatch.setattr(BP, "FALLBACK_MODELS", ["fallback-model"])
    fake = FakeGemini([
        RuntimeError("503 UNAVAILABLE high demand"),   # primary, attempt 1
        RuntimeError("503 UNAVAILABLE high demand"),   # primary, attempt 2
        {"order": "scan", "reason": "fallback ok"},     # fallback model answers
    ])
    seen = []
    orig = fake.generate_content
    fake.generate_content = lambda model, contents, config=None: (seen.append(model), orig(model, contents, config))[1]
    player = BP.GeminiPlayer(client=fake, model="primary-model")
    st = _debug_state(1)
    d = asyncio.run(player.decide(st, "bot:1"))
    assert d.order == "scan" and d.error is None
    assert seen == ["primary-model", "primary-model", "fallback-model"]


def test_non_transient_error_is_not_retried():
    fake = FakeGemini([RuntimeError("400 INVALID_ARGUMENT bad key"), {"order": "scan", "reason": "x"}])
    player = BP.GeminiPlayer(client=fake, model="m")
    d = asyncio.run(player.decide(_debug_state(1), "bot:1"))
    assert d.error == "api_error" and fake.calls == 1


def test_parse_fugitive_decision_variants():
    d = BP.parse_fugitive_decision('{"move": "b2", "hack": "doorlock", "target": "a1-a2", "reason": "막기"}')
    assert (d.move, d.hack, d.target) == ("B2", "doorlock", "A1-A2")
    d = BP.parse_fugitive_decision('{"move": null, "hack": "hide", "target": null, "reason": "숨기"}')
    assert d.move is None and d.hack == "hide" and d.target is None
    d = BP.parse_fugitive_decision('{"move": "stay", "hack": "teleport", "reason": "x"}')
    assert d.move is None and d.hack is None
    assert BP.parse_fugitive_decision("...").error == "no_json"


def test_fugitive_prompt_contains_true_state():
    st = _debug_state(2)
    st.debug_fugitive_ai = True
    p = BP.build_fugitive_prompt(st, [])
    assert "현재 위치: A2" in p and "RAM" in p and "overload" in p and "잠글 수 있는 문" in p


def test_fugitive_ai_turn_submits_and_reports():
    async def body():
        ch = FakeChannel()
        cog = _make_cog(ch)
        st = _debug_state(2)
        st.debug_bots = False
        st.debug_fugitive_ai = True
        cog.state = st
        cog.bot_player = BP.GeminiPlayer(client=FakeGemini([
            {"move": "B2", "hack": "blackout", "target": None, "reason": "조명 방 아님 → 거부될 것"},
        ]))
        cog._schedule_fugitive_turn()
        await asyncio.wait_for(cog.fugitive_task, 5)
        assert st.fugitive.order is not None and st.fugitive.order.move == "B2" and st.fugitive.order.hack is None
        row = st.bot_reasons[-1]
        assert row["uid"] == "fugitive" and not row["accepted"] and "이동만 제출" in row["note"]
        msgs = cog.control_msgs
        assert "판단 중" in msgs[0] and "도주자-AI" in msgs[0]
        assert "이동 B2" in msgs[1] and "광학 재부팅" in msgs[1]
        assert any("규칙에 맞지 않아" in m for m in msgs)
        assert "실제 상태" in msgs[-1]
        cog._cancel_timer()

    asyncio.run(body())


def test_fugitive_ai_respects_manual_order():
    async def body():
        ch = FakeChannel()
        cog = _make_cog(ch)
        st = _debug_state(2)
        st.debug_bots = False
        st.debug_fugitive_ai = True
        cog.state = st
        E.submit_fugitive_order(st, "A1")           # 관리자가 먼저 제출
        cog._schedule_fugitive_turn()
        assert cog.fugitive_task is None            # 이미 제출됐으면 요청하지 않는다

    asyncio.run(body())


def test_char_color_read_from_characters_sheet():
    import pandas as pd

    class FakeSheets:
        def get_char_data(self, cid):
            rows = {"10": pd.Series({"name": "철수", "color": "#FF8800"}),
                    "11": pd.Series({"name": "영희"}),                # 열 없음
                    "12": pd.Series({"name": "민수", "color": ""})}
            return rows.get(cid)

    cog = _make_cog(FakeChannel())
    cog.sheet_handler = FakeSheets()
    assert cog._char_color("10") == "#FF8800"
    assert cog._char_color("11") == "" and cog._char_color("12") == "" and cog._char_color("99") == ""
    st = _debug_state(2)
    st.hunters["10"] = E.Hunter(user_id="10", name="철수", room="A1")
    cog._apply_char_colors(st)
    assert st.hunters["10"].color == "#FF8800" and st.hunters["bot:1"].color == ""


def test_bot_hunters_can_continue_numbering():
    assert BP.bot_hunters(2, start=3) == [("bot:3", "제미나이-3"), ("bot:4", "제미나이-4")]
    assert BP.bot_hunters(1) == [("bot:1", "제미나이-1")]


def test_mixed_lobby_only_bots_get_ai_orders():
    """사람 + 봇 혼합: 봇 명령만 자동 제출되고 사람 자리는 비어 있어야 한다."""
    async def body():
        ch = FakeChannel()
        cog = _make_cog(ch)
        c, _ = cfg.build_config(3)
        st = E.new_game(E.GameMap.builtin("car2077_9"), c, [("100", "사람"), ("bot:1", "제미나이-1"), ("bot:2", "제미나이-2")],
                        fugitive_start="A2", rng=random.Random(1))
        st.channel_id, st.round_message_id, st.debug_bots = 1, 10, True
        cog.state = st
        cog.bot_player = BP.GeminiPlayer(client=FakeGemini([
            {"order": "search", "reason": "a"}, {"order": "scan", "reason": "b"},
        ]))
        cog._schedule_bot_turn()
        await asyncio.wait_for(cog.bot_task, 5)
        assert st.hunters["100"].order is None
        assert st.hunters["bot:1"].order.type == "search" and st.hunters["bot:2"].order.type == "scan"
        assert not st.all_hunters_submitted()
        assert all("판단" in m or "→" in m for m in cog.control_msgs)   # 이유는 관제 채널에만
        assert not any("이유" in (m or "") or "→" in (m or "") for m in ch.sent)  # 공개 채널에는 없음

    asyncio.run(body())
