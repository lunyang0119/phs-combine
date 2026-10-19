"""카드 전투 Cog — 가짜 채널/상호작용으로 명령 흐름과 버튼 흐름을 돌린다."""
import asyncio
import random
from types import SimpleNamespace

from discord import app_commands

from cardbattle import engine as E
from cardbattle import strings as S
from cardbattle.cog import CardBattleCog
from cardbattle.views import HandView, TargetView, TurnView


class FakeMessage:
    _next_id = 1000

    def __init__(self, channel, content, view=None, embed=None):
        FakeMessage._next_id += 1
        self.id = FakeMessage._next_id
        self.channel = channel
        self.content = content
        self.view = view
        self.embed = embed
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
        m = FakeMessage(self, content, kw.get("view"), kw.get("embed"))
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
    rows = {"1": {"max_hp": 39, "physics": 14, "magic": 8, "agility": 12},
            "2": {"max_hp": 30, "physics": 8, "magic": 14, "agility": 9}}

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


async def setup_battle(cog, channel, host=1, players=(1, 2), battle="b01"):
    await cog._cmd_host(interaction(host, channel), None)
    cog.state.config["enemy_turn_delay_sec"] = 0.0
    for uid in players:
        await cog._cmd_join(interaction(uid, channel), None)
    await cog._cmd_start(interaction(host, channel), battle)


def turn_message(cog, channel):
    return channel.messages[cog.state.turn_message_id]


async def pick_any(cog, channel, uid):
    """카드 뽑기 → 첫 카드 → (필요하면) 첫 대상. 뽑은 패를 돌려준다."""
    i = interaction(uid, channel, message=turn_message(cog, channel))
    await cog.handle_draw(i)
    kind, content, ephemeral, view, embed = last(i)
    assert kind == "send" and ephemeral and isinstance(view, HandView) and embed is not None
    hand = E.hand_for(cog.state, str(uid))
    i2 = interaction(uid, channel)
    await cog.handle_card_pick(i2, str(uid), hand[0])
    kind, content, _, view, _embed = last(i2)
    if isinstance(view, TargetView):
        tid = view.children[0].options[0].value
        i3 = interaction(uid, channel)
        await cog.handle_target_pick(i3, str(uid), hand[0], tid)
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
        assert cog.state.players["1"].is_host and cog.state.players["1"].max_hp == 39
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
        assert "찾지 못했습니다" in last(i)[1] and "b01" in last(i)[1]
        assert cog.state.status == E.LOBBY
    asyncio.run(body())


def test_start_posts_turn_message_with_progress_and_button(tmp_path):
    async def body():
        ch = FakeChannel()
        cog = make_cog(ch, tmp_path)
        await setup_battle(cog, ch)
        st = cog.state
        assert st.status == E.ALLY_TURN and st.battle_id == "b01" and len(st.enemies) == 2
        tm = turn_message(cog, ch)
        assert "선택 현황 0/2 완료" in text(tm) and isinstance(tm.view, TurnView)
        assert any("정찰대" in text(m) for m in ch.sent)
        assert "█" in text(tm) and "/39" in text(tm)                        # HP 바
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
        assert len(E.hand_for(cog.state, "1")) == len(cog.state.cards)   # 주최자 전체 카드
        stale = FakeMessage(ch, "old")
        i3 = interaction(2, ch, message=stale)
        await cog.handle_draw(i3)
        assert last(i3)[1] == S.ERR_STALE_MESSAGE
        i4 = interaction(7, ch, message=tm)
        await cog.handle_draw(i4)
        assert last(i4)[1] == S.ERR_NOT_IN_GAME
    asyncio.run(body())


def test_all_chosen_resolves_posts_logs_and_opens_next_turn(tmp_path):
    async def body():
        ch = FakeChannel()
        cog = make_cog(ch, tmp_path)
        await setup_battle(cog, ch)
        first_turn = turn_message(cog, ch)
        await pick_any(cog, ch, 1)
        assert "선택 현황 1/2 완료" in text(first_turn)
        n_before = len(ch.sent)
        await pick_any(cog, ch, 2)
        assert first_turn.edits[-1].get("view", "keep") is None       # 버튼 제거
        texts = [text(m) for m in ch.sent[n_before:]]
        assert any("아군 행동" in t and "님이" in t and "HP" in t for t in texts)
        assert any("적 행동" in t for t in texts)
        st = cog.state
        assert st.status == E.ALLY_TURN and st.turn_no == 3
        assert st.turn_message_id != first_turn.id and st.hands == {} and st.choices == {}
        assert "선택 현황 0/2 완료" in text(turn_message(cog, ch))
    asyncio.run(body())


def test_cancel_and_force_resolve_marks_unchosen_wait(tmp_path):
    async def body():
        ch = FakeChannel()
        cog = make_cog(ch, tmp_path)
        await setup_battle(cog, ch)
        hand = await pick_any(cog, ch, 2)
        assert "2" in cog.state.choices
        i = interaction(2, ch)
        await cog.handle_cancel(i, "2")
        assert "2" not in cog.state.choices and E.hand_for(cog.state, "2") == hand
        assert last(i)[0] == "edit" and isinstance(last(i)[3], HandView)
        await pick_any(cog, ch, 2)
        i = interaction(2, ch)
        await cog._cmd_force(i, None)
        assert last(i)[1] == S.ERR_HOST_ONLY
        n_before = len(ch.sent)
        await cog._cmd_force(interaction(1, ch), None)
        texts = "\n".join(text(m) for m in ch.sent[n_before:])
        assert "**닉1**님은 대기했습니다" in texts and cog.state.turn_no == 3
    asyncio.run(body())


def test_cog_load_restores_state_and_registers_view(tmp_path):
    async def body():
        ch = FakeChannel()
        cog = make_cog(ch, tmp_path)
        await setup_battle(cog, ch)
        await pick_any(cog, ch, 1)
        tm_id = cog.state.turn_message_id
        cog2 = make_cog(ch, tmp_path)
        await cog2.cog_load()
        await asyncio.sleep(0.05)
        assert any(isinstance(v, TurnView) for v in cog2.registered_views)
        assert cog2.state.status == E.ALLY_TURN and "1" in cog2.state.choices
        assert cog2.state.turn_message_id == tm_id                    # 턴 메시지 재게시 없음
        # 정산 직후 크래시 (ENEMY_TURN 저장) → 적 턴 이어서 진행
        E.resolve_ally_turn(cog2.state, cog2.rng)
        cog2.state.turn_message_id = 0
        cog2._save()
        cog3 = make_cog(ch, tmp_path)
        n_before = len(ch.sent)
        await cog3.cog_load()
        await asyncio.sleep(0.05)
        assert cog3.state.status == E.ALLY_TURN and cog3.state.turn_message_id
        assert any("적 행동" in text(m) for m in ch.sent[n_before:])
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
