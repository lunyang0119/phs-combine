"""모든 비관제 슬래시 명령이 `도움말` 옵션을 갖고 필수 옵션이 없는지, 그리고 help_option 헬퍼 동작."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord
from discord import app_commands

import help_option

import archive
import character_commands
import combat_commands
import mogindex_commands
import shop_commands
import utility_commands
from fugitive import cog as fugitive_cog

COGS = [
    character_commands.CharacterCog,
    combat_commands.CombatCog,
    utility_commands.UtilityCommandsCog,
    shop_commands.ShopCog,
    archive.ArchiveCommandsCog,
    mogindex_commands.MogIndexCommandsCog,
    fugitive_cog.FugitiveCog,
]
# 관제/관리자 명령 (도움말 옵션 없음)
EXCLUDED = {
    "점수", "리스트", "서버원등록", "hp_mp_재계산",
    "보스전투", "치트", "디버그",
    "매점초기화", "매점숨김갱신",
    "재색인", "전체색인",
}
REMOVED = {"추적기도움말", "매점도움말", "도움말"}


def _commands():
    for cog in COGS:
        for attr in vars(cog).values():
            if isinstance(attr, app_commands.Command):
                yield cog.__name__, attr


def test_no_standalone_help_commands_remain():
    names = {c.name for _, c in _commands()}
    assert not (names & REMOVED)
    assert not hasattr(combat_commands.CombatCog, "help_command")


def test_every_public_command_has_trailing_help_option():
    seen = set()
    for cog_name, c in _commands():
        seen.add(c.name)
        params = c.parameters
        if c.name in EXCLUDED:
            assert all(p.name != "도움말" for p in params), (cog_name, c.name)
            continue
        assert params and params[-1].name == "도움말", (cog_name, c.name)
        last = params[-1]
        assert last.type is discord.AppCommandOptionType.boolean and not last.required
        assert last.description == help_option.HELP_OPTION_DESC
        assert not any(p.required for p in params), (cog_name, c.name, [p.name for p in params if p.required])
    assert EXCLUDED <= seen
    assert {"이동", "구매", "포인트", "전투시작", "아카이브", "검색", "고오급검색", "챗"} <= seen


def test_admin_groups_untouched():
    grp_names = {g.name for g in vars(fugitive_cog.FugitiveCog).values() if isinstance(g, app_commands.Group)}
    assert grp_names == set() or grp_names <= {"추적기", "도주"}


class _Response:
    def __init__(self):
        self.sent = None
        self.done = False

    def is_done(self):
        return self.done

    async def send_message(self, content, ephemeral=False):
        self.sent = (content, ephemeral)
        self.done = True


class _Followup:
    def __init__(self):
        self.sent = None

    async def send(self, content, ephemeral=False):
        self.sent = (content, ephemeral)


def _interaction(command):
    return SimpleNamespace(command=command, response=_Response(), followup=_Followup())


def test_render_help_lists_options_and_choices():
    cmd = shop_commands.ShopCog.구매
    text = help_option.render_help(cmd, "EXTRA")
    assert text.startswith("## /구매\n매점 상품을 구매합니다.")
    assert "`item_no` [선택]" in text and "`종류` [선택]" in text and "(선택: 일반 / 특수)" in text
    assert text.count("`도움말`") == 1 and text.rstrip().endswith("EXTRA")
    assert "## /탑승" in help_option.render_help(fugitive_cog.FugitiveCog.join)


def test_render_help_uses_display_name_and_qualified_name():
    text = help_option.render_help(mogindex_commands.MogIndexCommandsCog.search_panel)
    assert "`검색어`" in text and "`keyword`" not in text
    sub = SimpleNamespace(qualified_name="추적기 개설", name="개설", description="d", parameters=[])
    assert help_option.render_help(sub).startswith("## /추적기 개설\nd\n**옵션**\n• 없음")


def test_maybe_help_sends_ephemeral_only_when_flag_set():
    it = _interaction(shop_commands.ShopCog.매점)
    assert asyncio.run(help_option.maybe_help(it, False)) is False and it.response.sent is None
    assert asyncio.run(help_option.maybe_help(it, True, "X")) is True
    content, ephemeral = it.response.sent
    assert ephemeral and content.startswith("## /매점") and content.endswith("X")
    # 이미 응답한 뒤라면 followup
    it2 = _interaction(shop_commands.ShopCog.매점)
    it2.response.done = True
    asyncio.run(help_option.maybe_help(it2, True))
    assert it2.followup.sent[1] is True and it2.response.sent is None


def test_require_reports_missing_options():
    it = _interaction(shop_commands.ShopCog.포인트)
    assert asyncio.run(help_option.require(it, user=1, amount=2)) is True and it.response.sent is None
    assert asyncio.run(help_option.require(it, user=None, amount=None)) is False
    content, ephemeral = it.response.sent
    assert ephemeral and "`user`, `amount` 옵션이 필요합니다" in content and "/포인트 도움말:True" in content
