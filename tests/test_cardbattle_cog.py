"""카드 전투 Cog — 가짜 채널/상호작용으로 명령 흐름과 버튼 흐름을 돌린다."""
import asyncio
import random
from types import SimpleNamespace

from discord import app_commands

from cardbattle import engine as E
from cardbattle import strings as S
from cardbattle.cog import CardBattleCog
from cardbattle.views import EscapeView, FirewallView, HandView, TargetView, TurnView


class FakeMessage:
    _next_id = 1000

    def __init__(self, channel, content, view=None, embed=None, file=None):
        FakeMessage._next_id += 1
        self.id = FakeMessage._next_id
        self.channel = channel
        self.content = content
        self.view = view
        self.embed = embed
        self.file = file
        self.edits = []

    async def edit(self, **kw):
        await asyncio.sleep(0)
        self.edits.append(kw)
        if "content" in kw:
            self.content = kw["content"]
        if "view" in kw:
            self.view = kw["view"]
        if "embed" in kw:
            self.embed = kw["embed"]


def flat(embed):
    """임베드의 제목·설명·필드를 한 문자열로."""
    if embed is None:
        return ""
    parts = [embed.title or "", embed.description or ""]
    for f in embed.fields:
        parts += [f.name or "", f.value or ""]
    if embed.footer and embed.footer.text:
        parts.append(embed.footer.text)
    return "\n".join(parts)


def text(m):
    return (m.content or "") + "\n" + flat(m.embed)


class FakeChannel:
    def __init__(self):
        self.sent = []
        self.messages = {}

    async def send(self, content=None, **kw):
        await asyncio.sleep(0)
        m = FakeMessage(self, content, kw.get("view"), kw.get("embed"), kw.get("file"))
        self.sent.append(m)
        self.messages[m.id] = m
        return m

    async def fetch_message(self, mid):
        await asyncio.sleep(0)
        if mid not in self.messages:
            raise LookupError(mid)
        return self.messages[mid]


class FakeResponse:
    def __init__(self):
        self.calls = []
        self._done = False

    def is_done(self):
        return self._done

    async def send_message(self, content=None, ephemeral=False, view=None, embed=None, **kw):
        await asyncio.sleep(0)
        self._done = True
        self.calls.append(("send", content, ephemeral, view, embed))

    async def edit_message(self, content=None, view=None, embed=None, **kw):
        await asyncio.sleep(0)
        self._done = True
        self.calls.append(("edit", content, None, view, embed))

    async def defer(self, ephemeral=False):
        self._done = True


class FakeFollowup:
    def __init__(self, response):
        self.response = response

    async def send(self, content=None, ephemeral=False, view=None, embed=None, **kw):
        await asyncio.sleep(0)
        self.response.calls.append(("followup", content, ephemeral, view, embed))


def interaction(uid, channel, message=None, guild_id=1):
    r = FakeResponse()
    return SimpleNamespace(
        user=SimpleNamespace(id=uid, display_name=f"닉{uid}", name=f"user{uid}"),
        guild_id=guild_id, channel_id=1, channel=channel, message=message,
        response=r, followup=FakeFollowup(r), client=None,
        command=SimpleNamespace(qualified_name="카드", name="카드"),
    )


class FakeSheets:
    rows = {"1": {"max_hp": 39, "physics": 14, "magic": 8, "agility": 12, "job": "스트라이커", "color": "#00FF00"},
            "2": {"max_hp": 30, "physics": 8, "magic": 14, "agility": 9, "job": "마테리아 위버"}}

    def get_char_data(self, uid):
        return self.rows.get(str(uid))


def make_cog(channel, tmp_path):
    views = []
    bot = SimpleNamespace(get_channel=lambda cid: channel, add_view=views.append, sheet_handler=FakeSheets())
    cog = CardBattleCog(bot)
    cog.store_path = str(tmp_path / "state.json")
    cog.rng = random.Random(3)
    cog.registered_views = views
    return cog


def last(i):
    return i.response.calls[-1]


async def setup_battle(cog, channel, host=1, players=(1, 2), battle="b01", **cfg):
    await cog._cmd_host(interaction(host, channel), None)
    cog.state.config["report_delay_sec"] = 0.0
    cog.state.config.update(cfg)
    for uid in players:
        await cog._cmd_join(interaction(uid, channel), None)
    await cog._cmd_start(interaction(host, channel), battle)


def turn_message(cog, channel):
    return channel.messages[cog.state.turn_message_id]


async def pick(cog, channel, uid, card_id=None):
    """카드 뽑기 → 카드(기본: 첫 장) → (필요하면) 첫 대상. 뽑은 패를 돌려준다."""
    i = interaction(uid, channel, message=turn_message(cog, channel))
    await cog.handle_draw(i)
    kind, content, ephemeral, view, embed = last(i)
    assert kind == "send" and ephemeral and isinstance(view, HandView) and embed is not None
    hand = E.hand_for(cog.state, str(uid))
    cid = card_id or hand[0]
    i2 = interaction(uid, channel)
    await cog.handle_card_pick(i2, str(uid), cid)
    kind, content, _, view, _embed = last(i2)
    if isinstance(view, TargetView):
        tid = view.children[0].options[0].value
        i3 = interaction(uid, channel)
        await cog.handle_target_pick(i3, str(uid), cid, tid)
    return hand


# ---------------------------------------------------------------------------
def test_host_is_ephemeral_posts_one_lobby_message_and_join_edits_it(tmp_path):
    async def body():
        ch = FakeChannel()
        cog = make_cog(ch, tmp_path)
        i = interaction(1, ch)
        await cog._cmd_host(i, None)
        assert last(i)[0] == "send" and last(i)[2] is True            # ephemeral
        assert len(ch.sent) == 1 and cog.state.status == E.LOBBY
        lobby = ch.sent[0]
        await cog._cmd_join(interaction(1, ch), None)
        await cog._cmd_join(interaction(2, ch), None)
        assert len(ch.sent) == 1                                       # 새 메시지 없이 편집
        assert "닉1" in text(lobby) and "닉2" in text(lobby) and "(2명)" in text(lobby)
        assert lobby.content is None and lobby.edits[-1].get("content") is None   # 임베드만 편집
        p1 = cog.state.players["1"]
        assert p1.is_host and p1.max_hp == 39 and p1.job == "스트라이커" and p1.color == "#00FF00"
        i = interaction(2, ch)
        await cog._cmd_join(i, None)
        assert last(i)[1] == S.ERR_ALREADY_JOINED
    asyncio.run(body())


def test_duplicate_host_blocked_in_lobby_and_no_character_rejected(tmp_path):
    async def body():
        ch = FakeChannel()
        cog = make_cog(ch, tmp_path)
        await cog._cmd_host(interaction(1, ch), None)
        i = interaction(2, ch)
        await cog._cmd_host(i, None)
        assert "이미 세션" in last(i)[1] and len(ch.sent) == 1 and cog.state.host_id == "1"
        i = interaction(9, ch)
        await cog._cmd_join(i, None)
        assert last(i)[1] == S.ERR_NO_CHARACTER and "9" not in cog.state.players
        i = interaction(2, ch, guild_id=5)
        await cog._cmd_join(i, None)
        assert last(i)[1] == S.ERR_WRONG_GUILD
    asyncio.run(body())


def test_start_requires_host_and_known_battle(tmp_path):
    async def body():
        ch = FakeChannel()
        cog = make_cog(ch, tmp_path)
        await cog._cmd_host(interaction(1, ch), None)
        await cog._cmd_join(interaction(2, ch), None)
        i = interaction(2, ch)
        await cog._cmd_start(i, "b01")
        assert last(i)[1] == S.ERR_HOST_ONLY
        i = interaction(1, ch)
        await cog._cmd_start(i, None)
        assert "전투id" in last(i)[1]
        i = interaction(1, ch)
        await cog._cmd_start(i, "zzz")
        assert "찾지 못했습니다" in last(i)[1] and "scav3" in last(i)[1]
        assert cog.state.status == E.LOBBY
    asyncio.run(body())


def test_start_posts_turn_message_with_intents_progress_button_and_board(tmp_path):
    async def body():
        ch = FakeChannel()
        cog = make_cog(ch, tmp_path)
        await setup_battle(cog, ch)
        st = cog.state
        assert st.status == E.ROUND_OPEN and st.battle_id == "b01" and len(st.enemies) == 2
        tm = turn_message(cog, ch)
        t = text(tm)
        assert "선택 현황 0/2 완료" in t and isinstance(tm.view, TurnView)
        assert "적 의도" in t and "준비" in t and "→" not in t.split("적 의도")[1].split("선택 현황")[0]   # 대상 비공개
        assert "방화벽" in t and "엣지러너" in t and "넷러너" in t
        assert any("연습: 뒷골목 소란" in text(m) and "규칙" in text(m) for m in ch.sent)
        assert "█" in t and "/39" in t                                   # HP 바
        if cog._image_mode():
            assert tm.file is not None and tm.embed.image.url.endswith("board.png")
    asyncio.run(body())


def test_draw_twice_same_hand_and_stale_message_rejected(tmp_path):
    async def body():
        ch = FakeChannel()
        cog = make_cog(ch, tmp_path)
        await setup_battle(cog, ch)
        tm = turn_message(cog, ch)
        i1 = interaction(2, ch, message=tm)
        await cog.handle_draw(i1)
        hand1 = E.hand_for(cog.state, "2")
        i2 = interaction(2, ch, message=tm)
        await cog.handle_draw(i2)
        assert E.hand_for(cog.state, "2") == hand1 and len(hand1) == 3
        assert last(i2)[4].to_dict() == last(i1)[4].to_dict()
        host_i = interaction(1, ch, message=tm)
        await cog.handle_draw(host_i)
        assert "예상 피해" in flat(last(host_i)[4]) or "약 " in flat(last(host_i)[4])   # 공격 카드에 피해 범위 표시
        pool = [c for c in cog.state.cards.values() if c.pool in ("both", "ally")]
        assert len(E.hand_for(cog.state, "1")) == len(pool)             # 주최자 전체 카드 (fixed 제외)
        stale = FakeMessage(ch, "old")
        i3 = interaction(2, ch, message=stale)
        await cog.handle_draw(i3)
        assert last(i3)[1] == S.ERR_STALE_MESSAGE
        i4 = interaction(7, ch, message=tm)
        await cog.handle_draw(i4)
        assert last(i4)[1] == S.ERR_NOT_IN_GAME
    asyncio.run(body())


def test_all_chosen_resolves_posts_round_report_and_opens_next_round(tmp_path):
    async def body():
        ch = FakeChannel()
        cog = make_cog(ch, tmp_path)
        await setup_battle(cog, ch)
        first_turn = turn_message(cog, ch)
        await pick(cog, ch, 1)
        assert "선택 현황 1/2 완료" in text(first_turn)
        n_before = len(ch.sent)
        await pick(cog, ch, 2)
        assert first_turn.edits[-1].get("view", "keep") is None       # 버튼 제거
        texts = [text(m) for m in ch.sent[n_before:]]
        assert any("1라운드 정산" in t and "님이" in t and "HP" in t and "🔴" in t for t in texts)
        st = cog.state
        assert st.status in (E.ROUND_OPEN, E.ENDED)
        if st.status == E.ROUND_OPEN:
            assert st.round == 2 and st.turn_message_id != first_turn.id and st.hands == {} and st.choices == {}
            assert "선택 현황 0/" in text(turn_message(cog, ch)) and "2라운드" in text(turn_message(cog, ch))
    asyncio.run(body())


def test_cancel_and_force_resolve_marks_unchosen_wait(tmp_path):
    async def body():
        ch = FakeChannel()
        cog = make_cog(ch, tmp_path)
        await setup_battle(cog, ch)
        hand = await pick(cog, ch, 2)
        assert "2" in cog.state.choices
        i = interaction(2, ch)
        await cog.handle_cancel(i, "2")
        assert "2" not in cog.state.choices and E.hand_for(cog.state, "2") == hand
        assert last(i)[0] == "edit" and isinstance(last(i)[3], HandView)
        await pick(cog, ch, 2)
        i = interaction(2, ch)
        await cog._cmd_force(i, None)
        assert last(i)[1] == S.ERR_HOST_ONLY
        n_before = len(ch.sent)
        await cog._cmd_force(interaction(1, ch), None)
        texts = "\n".join(text(m) for m in ch.sent[n_before:])
        assert "**닉1**님은 대기했습니다" in texts and cog.state.round == 2
    asyncio.run(body())


def test_firewall_menu_and_scan_reveals_targets(tmp_path):
    async def body():
        ch = FakeChannel()
        cog = make_cog(ch, tmp_path)
        await setup_battle(cog, ch)
        tm = turn_message(cog, ch)
        cog.state.meters["firewall"] = 1
        i = interaction(2, ch, message=tm)
        await cog.handle_firewall_menu(i)
        assert last(i)[0] == "send" and isinstance(last(i)[3], FirewallView) and "방화벽" in flat(last(i)[4])
        i2 = interaction(2, ch)
        await cog.handle_firewall_pick(i2, "2", "cover")
        assert last(i2)[1] == S.RULE_ERRORS["FIREWALL_POOR"]
        i3 = interaction(2, ch)
        await cog.handle_firewall_pick(i3, "2", "scan")
        assert "적 스캔" in last(i3)[1] and cog.state.round_flags["scan"] and cog.state.meters["firewall"] == 0
        assert "→" in text(tm)                                         # 대상 공개됨
        i4 = interaction(1, ch)
        await cog.handle_firewall_pick(i4, "1", "scan")
        assert last(i4)[1] == S.RULE_ERRORS["FIREWALL_USED"]
    asyncio.run(body())


def test_ncpd_escape_flow_with_buttons(tmp_path):
    async def body():
        ch = FakeChannel()
        cog = make_cog(ch, tmp_path)
        await setup_battle(cog, ch, battle="scav3")
        st = cog.state
        st.config.update({"ncpd_max": 0.5, "escape_wins_needed": 1})   # Battles.csv overrides 뒤에 덮어쓴다
        assert st.has("ncpd") and "nonlethal" in st.gimmicks and "NCPD" in text(ch.sent[-2])
        # 주최자(전체 카드)가 소음 카드를 쓰면 경계도 0.5 → 도주 단계
        await pick(cog, ch, 1, "atk_sweep")
        await pick(cog, ch, 2)
        st = cog.state
        assert st.status == E.ESCAPE, st.status
        texts = [text(m) for m in ch.sent]
        assert any("NCPD 드론 출동" in t for t in texts)
        em = turn_message(cog, ch)
        assert isinstance(em.view, EscapeView) and "도주 1/5턴" in text(em) and "보느와" in text(em)
        # 버튼: 지난 메시지 거절, 비참가자 거절
        i = interaction(7, ch, message=em)
        await cog.handle_escape(i, "hide")
        assert last(i)[1] == S.ERR_NOT_IN_GAME
        i = interaction(1, ch, message=FakeMessage(ch, "old"))
        await cog.handle_escape(i, "hide")
        assert last(i)[1] == S.ERR_STALE_MESSAGE
        safe = E.safe_escape_action(st.escape["hint"])
        i = interaction(1, ch, message=em)
        await cog.handle_escape(i, safe)
        assert "선택 완료" in last(i)[1] and "선택 현황 1/2" in text(em)
        n_before = len(ch.sent)
        i = interaction(2, ch, message=em)
        await cog.handle_escape(i, safe)
        texts = [text(m) for m in ch.sent[n_before:]]
        assert any("도주 1턴" in t and "경찰" in t for t in texts)
        st = cog.state
        assert st.status in (E.ESCAPE, E.ENDED)
        if st.status == E.ENDED:
            assert st.winner == E.ESCAPED and any("탈출 성공" in t for t in texts)
        else:
            assert "도주 2/5턴" in text(turn_message(cog, ch))
            # 주최자 강제 정산도 도주 단계에서 동작
            await cog._cmd_force(interaction(1, ch), None)
            assert cog.state.escape["turn"] >= 3 or cog.state.status == E.ENDED
    asyncio.run(body())


def test_cog_load_restores_state_and_registers_views(tmp_path):
    async def body():
        ch = FakeChannel()
        cog = make_cog(ch, tmp_path)
        await setup_battle(cog, ch)
        await pick(cog, ch, 1)
        tm_id = cog.state.turn_message_id
        cog2 = make_cog(ch, tmp_path)
        await cog2.cog_load()
        await asyncio.sleep(0.05)
        assert any(isinstance(v, TurnView) for v in cog2.registered_views)
        assert any(isinstance(v, EscapeView) for v in cog2.registered_views)
        assert cog2.state.status == E.ROUND_OPEN and "1" in cog2.state.choices
        assert cog2.state.turn_message_id == tm_id                    # 턴 메시지 재게시 없음
        # 정산 직후 보고를 올리기 전에 크래시 → 보고 게시 + 턴 메시지 재개
        rep = E.resolve_round(cog2.state, cog2.rng)
        cog2.state.turn_message_id = 0
        cog2.state.pending_reports = [rep]
        cog2._save()
        cog3 = make_cog(ch, tmp_path)
        n_before = len(ch.sent)
        await cog3.cog_load()
        await asyncio.sleep(0.05)
        assert cog3.state.status in (E.ROUND_OPEN, E.ENDED)
        assert any("라운드 정산" in text(m) for m in ch.sent[n_before:])
        if cog3.state.status == E.ROUND_OPEN:
            assert cog3.state.turn_message_id
    asyncio.run(body())


def test_end_clears_state_and_command_dispatch(tmp_path):
    async def body():
        ch = FakeChannel()
        cog = make_cog(ch, tmp_path)
        await setup_battle(cog, ch)
        i = interaction(2, ch)
        await cog._cmd_end(i, None)
        assert last(i)[1] == S.ERR_HOST_ONLY
        i = interaction(1, ch)
        await cog.card.callback(cog, i, 옵션=app_commands.Choice(name="종료", value="end"), 전투id=None)
        assert last(i)[1] == S.OK_ENDED and cog.state is None
        assert not (tmp_path / "state.json").exists()
        i = interaction(1, ch)
        await cog.card.callback(cog, i, 옵션=None)
        assert "옵션" in last(i)[1]
    asyncio.run(body())


def test_status_embed_in_round_and_doll_battle_intro(tmp_path):
    async def body():
        ch = FakeChannel()
        cog = make_cog(ch, tmp_path)
        await setup_battle(cog, ch, battle="doll3")
        st = cog.state
        assert st.has("doll") and st.meters["terminal_holder"] == "1" and st.meters["chip"] == 8
        intro = text(ch.sent[-2])
        assert "단말기 소지자(닉1)" in intro and "인격 칩" in intro
        tm = text(turn_message(cog, ch))
        assert "칩 안정도 8/8" in tm and "액정 3/3" in tm and "모드" in tm
        i = interaction(2, ch)
        await cog._cmd_status(i, None)
        assert "현황" in flat(last(i)[4]) and "칩 안정도" in flat(last(i)[4])
        # 소지자의 패에는 '단말기 넘기기' 가 들어 있다
        await cog.handle_draw(interaction(1, ch, message=turn_message(cog, ch)))
        assert E.PASS_CARD in E.hand_for(st, "1")
    asyncio.run(body())
