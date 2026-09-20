"""타이머 태스크가 라운드를 정산할 때 스스로를 취소해 보고/체포 메시지가 사라지던 회귀 테스트."""
import asyncio
import random
from types import SimpleNamespace

import pytest

from fugitive import config as cfg
from fugitive import engine as E
from fugitive.cog import FugitiveCog


class FakeMessage:
    _next_id = 100

    def __init__(self, channel, content):
        FakeMessage._next_id += 1
        self.id = FakeMessage._next_id
        self.channel = channel
        self.content = content

    async def edit(self, **kw):
        await asyncio.sleep(0)
        self.channel.edits.append(kw)


class FakeChannel:
    def __init__(self):
        self.sent = []
        self.edits = []
        self.messages = {}

    async def send(self, content=None, **kw):
        await asyncio.sleep(0)           # 실제 HTTP 처럼 이벤트 루프에 양보한다
        self.sent.append(content)
        m = FakeMessage(self, content)
        self.messages[m.id] = m
        return m

    async def fetch_message(self, mid):
        await asyncio.sleep(0)
        return self.messages.setdefault(mid, FakeMessage(self, ""))


def _make_cog(channel):
    bot = SimpleNamespace(get_channel=lambda cid: channel)
    cog = FugitiveCog(bot)
    cog._save = lambda: None
    cog.rng = random.Random(3)
    cog.control_msgs = []

    async def _update_table(ch):
        await asyncio.sleep(0)

    async def _control_send(content):
        await asyncio.sleep(0)
        cog.control_msgs.append(content)

    cog._update_table = _update_table
    cog._control_send = _control_send
    return cog


def _make_state(capture: bool):
    hunters = [("1", "철수"), ("2", "영희")]
    c, _ = cfg.build_config(len(hunters))
    c["capture_mode"] = "search"
    st = E.new_game(E.GameMap.builtin("car2077_9"), c, hunters, fugitive_start="A2", rng=random.Random(1))
    st.channel_id = 1
    st.round_message_id = 10
    st.status = E.ROUND_OPEN
    if capture:
        st.hunters["1"].room = "B2"
        E.submit_hunter_order(st, "1", "search")
        E.submit_hunter_order(st, "2", "search")
        E.submit_fugitive_order(st, "B2")
    else:
        E.submit_hunter_order(st, "1", "search")
        E.submit_hunter_order(st, "2", "search")
        E.submit_fugitive_order(st, None)
    return st


async def _run_timer(cog):
    cog._arm_timer(0.01)
    task = cog.timer_task
    await asyncio.wait_for(task, 5)
    assert not task.cancelled(), "타이머 태스크가 정산 도중 취소되었습니다"


def test_timer_close_posts_report_and_opens_next_round():
    async def body():
        ch = FakeChannel()
        cog = _make_cog(ch)
        cog.state = _make_state(capture=False)
        await _run_timer(cog)
        assert cog.state.status == E.ROUND_OPEN and cog.state.round_no == 2
        assert len(ch.sent) == 2, ch.sent                # 라운드 보고 + 다음 라운드 개시
        assert cog.control_msgs, "관제 채널 보고가 전송되지 않았습니다"
        assert cog.timer_task is not None and not cog.timer_task.done()
        cog._cancel_timer()

    asyncio.run(body())


def test_timer_close_posts_capture_line():
    async def body():
        ch = FakeChannel()
        cog = _make_cog(ch)
        cog.state = _make_state(capture=True)
        await _run_timer(cog)
        assert cog.state.status == E.CAPTURED
        assert cog.state.captured_by == "1"
        assert any("철수" in (m or "") and "잡았다" in (m or "") for m in ch.sent), ch.sent
        assert cog.timer_task is None

    asyncio.run(body())
