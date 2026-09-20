"""라운드 명령 버튼 (영구 View) 과 이동 구역 선택."""
from __future__ import annotations

from typing import List

import discord


class RoundView(discord.ui.View):
    """timeout=None + 고정 custom_id → 봇 재시작 후에도 동작한다."""

    def __init__(self):
        super().__init__(timeout=None)

    async def _dispatch(self, interaction: discord.Interaction, kind: str):
        cog = interaction.client.get_cog("FugitiveCog")
        if cog is None:
            await interaction.response.send_message("추적기 모듈이 꺼져 있습니다.", ephemeral=True)
            return
        await cog.handle_button(interaction, kind)

    @discord.ui.button(label="이동", style=discord.ButtonStyle.primary, custom_id="fugitive:move", emoji="🚪")
    async def move(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._dispatch(interaction, "move")

    @discord.ui.button(label="수색", style=discord.ButtonStyle.success, custom_id="fugitive:search", emoji="🔍")
    async def search(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._dispatch(interaction, "search")

    @discord.ui.button(label="추적", style=discord.ButtonStyle.secondary, custom_id="fugitive:scan", emoji="📶")
    async def scan(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._dispatch(interaction, "scan")

    @discord.ui.button(label="대기", style=discord.ButtonStyle.secondary, custom_id="fugitive:stay", emoji="⏸")
    async def stay(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._dispatch(interaction, "stay")


class RoomSelect(discord.ui.Select):
    def __init__(self, rooms: List[str], here: str):
        options = [discord.SelectOption(label=r, value=r) for r in rooms]
        super().__init__(placeholder=f"{here} 에서 이동할 구역", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("FugitiveCog")
        if cog is None:
            await interaction.response.send_message("추적기 모듈이 꺼져 있습니다.", ephemeral=True)
            return
        await cog.handle_order(interaction, "move", self.values[0], edit=True)
        if self.view:
            self.view.stop()


class RoomSelectView(discord.ui.View):
    def __init__(self, rooms: List[str], here: str, timeout: float = 600):
        super().__init__(timeout=timeout)
        self.add_item(RoomSelect(rooms, here))
