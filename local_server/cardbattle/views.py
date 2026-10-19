"""카드 전투 UI.

TurnView  — 공개 턴 메시지의 영구 버튼 (timeout=None + 고정 custom_id → 재시작 후에도 동작)
HandView  — 본인에게만 보이는 패 드롭다운 (주최자는 전체 카드, 24장 + '다음' 옵션으로 페이징)
TargetView — 카드 선택 후 대상 드롭다운
ChosenView — 선택 완료 후 '다시 고르기 / 선택 취소'
ephemeral 메시지는 그 메시지의 컴포넌트 상호작용에서만 response.edit_message 로 고칠 수 있다.
"""
from __future__ import annotations

from typing import List, Optional

import discord

from . import engine as E
from . import strings as S

COG_NAME = "CardBattleCog"
PAGE_SIZE = 24
MORE_VALUE = "__more__"


def _cog(interaction: discord.Interaction):
    return interaction.client.get_cog(COG_NAME)


async def _dispatch(interaction: discord.Interaction, method: str, *args):
    cog = _cog(interaction)
    if cog is None:
        await interaction.response.send_message(S.ERR_MODULE_OFF, ephemeral=True)
        return
    await getattr(cog, method)(interaction, *args)


class TurnView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label=S.BTN_DRAW, style=discord.ButtonStyle.primary, custom_id="cardbattle:draw", emoji="🃏")
    async def draw(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _dispatch(interaction, "handle_draw")


class _Owned(discord.ui.View):
    """본인만 조작 가능한 ephemeral 뷰."""

    def __init__(self, uid: str, timeout: float = 1800):
        super().__init__(timeout=timeout)
        self.uid = uid

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.uid:
            await interaction.response.send_message(S.ERR_NOT_YOURS, ephemeral=True)
            return False
        return True


class HandSelect(discord.ui.Select):
    def __init__(self, st: E.BattleState, uid: str, hand: List[str], page: int):
        self.uid = uid
        self.page = page
        pages = max(1, (len(hand) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = page % pages
        chunk = hand[page * PAGE_SIZE:(page + 1) * PAGE_SIZE] if len(hand) > PAGE_SIZE else hand
        options = []
        for cid in chunk:
            c = st.cards.get(cid)
            if c is None:
                continue
            desc = f"[{S.KIND_KO.get(c.kind, c.kind)}·{S.TARGET_KO.get(c.target, c.target)}] {c.desc}"
            options.append(discord.SelectOption(label=c.name[:100], value=cid, description=desc[:100]))
        if len(hand) > PAGE_SIZE:
            options.append(discord.SelectOption(label=S.HAND_MORE.format(page=page + 1, pages=pages)[:100],
                                                value=MORE_VALUE, emoji="▶"))
        super().__init__(placeholder=S.HAND_PLACEHOLDER, options=options or [discord.SelectOption(label="-", value="-")],
                         min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        v = self.values[0]
        if v == MORE_VALUE:
            await _dispatch(interaction, "handle_hand_page", self.uid, self.page + 1)
        else:
            await _dispatch(interaction, "handle_card_pick", self.uid, v)


class HandView(_Owned):
    def __init__(self, st: E.BattleState, uid: str, hand: List[str], page: int = 0):
        super().__init__(uid)
        self.add_item(HandSelect(st, uid, hand, page))
        if uid in st.choices:
            self.add_item(CancelButton(uid))


class CancelButton(discord.ui.Button):
    def __init__(self, uid: str):
        super().__init__(label=S.BTN_CANCEL, style=discord.ButtonStyle.danger)
        self.uid = uid

    async def callback(self, interaction: discord.Interaction):
        await _dispatch(interaction, "handle_cancel", self.uid)


class TargetSelect(discord.ui.Select):
    def __init__(self, uid: str, card_id: str, targets: List[E.Combatant]):
        self.uid = uid
        self.card_id = card_id
        options = [discord.SelectOption(label=S.TARGET_OPTION.format(name=t.name, hp=t.hp, max=t.max_hp)[:100],
                                        value=t.id) for t in targets[:25]]
        super().__init__(placeholder=S.TARGET_PLACEHOLDER, options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        await _dispatch(interaction, "handle_target_pick", self.uid, self.card_id, self.values[0])


class BackButton(discord.ui.Button):
    def __init__(self, uid: str, label: str = S.BTN_BACK):
        super().__init__(label=label, style=discord.ButtonStyle.secondary)
        self.uid = uid

    async def callback(self, interaction: discord.Interaction):
        await _dispatch(interaction, "handle_hand_page", self.uid, 0)


class TargetView(_Owned):
    def __init__(self, uid: str, card_id: str, targets: List[E.Combatant]):
        super().__init__(uid)
        self.add_item(TargetSelect(uid, card_id, targets))
        self.add_item(BackButton(uid))


class ChosenView(_Owned):
    def __init__(self, uid: str):
        super().__init__(uid)
        self.add_item(BackButton(uid, S.BTN_REPICK))
        self.add_item(CancelButton(uid))
