from __future__ import annotations

import logging
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from mogindex_service import (
    DatePreset,
    MogIndexService,
    ResultPage,
    SearchPanelState,
    SearchSessionError,
    SearchSessionExpired,
    SourceScope,
    TextPage,
)


logger = logging.getLogger(__name__)


def clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "..."


class KeywordSearchModal(discord.ui.Modal):
    def __init__(self, cog: "MogIndexCommandsCog", state: SearchPanelState):
        super().__init__(
            title="단어 검색",
            custom_id=f"mogsearch:{state.session_id}:keyword_modal",
        )
        self.cog = cog
        self.session_id = state.session_id
        self.query = discord.ui.TextInput(
            label="검색어",
            placeholder="예: 커피, 미스트렌드, 프릴 앞치마",
            default=state.query or "",
            required=True,
            max_length=120,
        )
        self.add_item(self.query)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_modal_query(interaction, self.session_id, "keyword", self.query.value)


class TopicSearchModal(discord.ui.Modal):
    def __init__(self, cog: "MogIndexCommandsCog", state: SearchPanelState):
        super().__init__(
            title="토픽 검색",
            custom_id=f"mogsearch:{state.session_id}:topic_modal",
        )
        self.cog = cog
        self.session_id = state.session_id
        self.query = discord.ui.TextInput(
            label="토픽 검색어",
            placeholder="예: 음료 취향, 사진 찍기, 기념일",
            default=state.query or "",
            required=True,
            max_length=120,
        )
        self.add_item(self.query)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_modal_query(interaction, self.session_id, "topic", self.query.value)


class DateRangeModal(discord.ui.Modal):
    def __init__(self, cog: "MogIndexCommandsCog", state: SearchPanelState):
        super().__init__(
            title="기간 직접 입력",
            custom_id=f"mogsearch:{state.session_id}:date_modal",
        )
        self.cog = cog
        self.session_id = state.session_id
        self.start_date = discord.ui.TextInput(
            label="시작일",
            placeholder="YYYY-MM-DD",
            default=state.start_date or "",
            required=True,
            max_length=10,
        )
        self.end_date = discord.ui.TextInput(
            label="종료일",
            placeholder="YYYY-MM-DD",
            default=state.end_date or "",
            required=True,
            max_length=10,
        )
        self.add_item(self.start_date)
        self.add_item(self.end_date)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_date_modal(
            interaction,
            self.session_id,
            self.start_date.value,
            self.end_date.value,
        )


class SearchPanelView(discord.ui.View):
    def __init__(
        self,
        cog: "MogIndexCommandsCog",
        state: SearchPanelState,
        page: ResultPage | TextPage | None,
    ):
        super().__init__(timeout=30 * 60)
        self.cog = cog
        self.session_id = state.session_id
        self._add_button("단어 검색", "keyword", discord.ButtonStyle.primary, row=0)
        self._add_button("날짜 요약", "recap", discord.ButtonStyle.secondary, row=0)
        self._add_button("토픽 검색", "topic", discord.ButtonStyle.secondary, row=0)
        self._add_button("참여자 보기", "participants", discord.ButtonStyle.secondary, row=0)

        self._add_button("오늘", "date_today", discord.ButtonStyle.secondary, row=1)
        self._add_button("7일", "date_7d", discord.ButtonStyle.secondary, row=1)
        self._add_button("30일", "date_30d", discord.ButtonStyle.secondary, row=1)
        self._add_button("전체", "date_all", discord.ButtonStyle.secondary, row=1)
        self._add_button("직접 기간", "date_custom", discord.ButtonStyle.secondary, row=1)

        self._add_button("전체 범위", "scope_all", discord.ButtonStyle.secondary, row=2)
        self._add_button("현재 채널", "scope_current", discord.ButtonStyle.secondary, row=2)
        if isinstance(page, ResultPage) and page.results:
            self._add_button(
                "이 스레드로 좁히기",
                "scope_first_result",
                discord.ButtonStyle.secondary,
                row=2,
                extra={"source_id": page.results[0].source_id},
            )
        self._add_button(
            "이전",
            "prev",
            discord.ButtonStyle.secondary,
            row=3,
            disabled=not (page and page.has_previous),
        )
        self._add_button(
            "다음",
            "next",
            discord.ButtonStyle.secondary,
            row=3,
            disabled=not (page and page.has_next),
        )
        self._add_button(
            "공개로 공유",
            "share",
            discord.ButtonStyle.success,
            row=4,
            disabled=state.mode == "hub",
        )
        self._add_button("닫기", "close", discord.ButtonStyle.danger, row=4)

    def _add_button(
        self,
        label: str,
        action: str,
        style: discord.ButtonStyle,
        *,
        row: int,
        disabled: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> None:
        button = discord.ui.Button(
            label=label,
            style=style,
            custom_id=f"mogsearch:{self.session_id}:{action}",
            row=row,
            disabled=disabled,
        )

        async def callback(interaction: discord.Interaction) -> None:
            await self.cog.handle_action(interaction, self.session_id, action, extra or {})

        button.callback = callback
        self.add_item(button)


class MogIndexCommandsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.service = MogIndexService()

    @app_commands.command(name="검색", description="색인된 커뮤 로그를 검색합니다.")
    async def search_panel(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("서버 안에서만 사용할 수 있습니다.", ephemeral=True)
            return
        state = self.service.create_session(interaction)
        embed, view, _page = self.render_panel(state)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def handle_action(
        self,
        interaction: discord.Interaction,
        session_id: str,
        action: str,
        extra: dict[str, Any],
    ) -> None:
        try:
            state = self.service.load_session(session_id, str(interaction.user.id))
        except SearchSessionError as exc:
            await self.send_session_error(interaction, exc)
            return

        if action == "keyword":
            await interaction.response.send_modal(KeywordSearchModal(self, state))
            return
        if action == "topic":
            await interaction.response.send_modal(TopicSearchModal(self, state))
            return
        if action == "date_custom":
            await interaction.response.send_modal(DateRangeModal(self, state))
            return
        if action == "share":
            await self.share_current_page(interaction, state)
            return
        if action == "close":
            self.service.close_session(state)
            await interaction.response.edit_message(content="검색 패널을 닫았습니다.", embed=None, view=None)
            return

        try:
            self.apply_action(state, action, extra)
            embed, view, _page = self.render_panel(state)
            self.service.save_session(state)
            await interaction.response.edit_message(embed=embed, view=view)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
        except Exception as exc:
            logger.error("검색 패널 action 처리 실패: %s", exc, exc_info=True)
            await interaction.response.send_message("검색 패널을 갱신하는 중 오류가 발생했습니다.", ephemeral=True)

    def apply_action(self, state: SearchPanelState, action: str, extra: dict[str, Any]) -> None:
        if action == "recap":
            state.mode = "recap"
            state.page = 0
        elif action == "participants":
            state.mode = "participants"
            state.page = 0
        elif action.startswith("date_"):
            preset = {
                "date_today": "today",
                "date_7d": "7d",
                "date_30d": "30d",
                "date_all": "all",
            }.get(action)
            if not preset:
                raise ValueError("알 수 없는 기간 선택입니다.")
            self.service.set_date_preset(state, preset)  # type: ignore[arg-type]
        elif action == "scope_all":
            self.service.set_scope(state, "all_indexed")
        elif action == "scope_current":
            self.service.set_scope(state, "current_channel")
        elif action == "scope_first_result":
            source_id = str(extra.get("source_id") or "")
            if not source_id:
                raise ValueError("좁힐 스레드 정보를 찾지 못했습니다.")
            self.service.set_scope(state, "selected_sources", [source_id])
        elif action == "prev":
            state.page = max(0, state.page - 1)
        elif action == "next":
            state.page += 1
        else:
            raise ValueError("알 수 없는 검색 패널 동작입니다.")

    async def handle_modal_query(
        self,
        interaction: discord.Interaction,
        session_id: str,
        mode: str,
        query: str,
    ) -> None:
        try:
            state = self.service.load_session(session_id, str(interaction.user.id))
            state.query = query.strip()
            state.mode = "keyword" if mode == "keyword" else "topic"
            state.page = 0
            embed, view, _page = self.render_panel(state)
            self.service.save_session(state)
            await interaction.response.edit_message(embed=embed, view=view)
        except SearchSessionError as exc:
            await self.send_session_error(interaction, exc)
        except Exception as exc:
            logger.error("검색 modal 처리 실패: %s", exc, exc_info=True)
            await interaction.response.send_message("검색어를 처리하는 중 오류가 발생했습니다.", ephemeral=True)

    async def handle_date_modal(
        self,
        interaction: discord.Interaction,
        session_id: str,
        start_date: str,
        end_date: str,
    ) -> None:
        try:
            state = self.service.load_session(session_id, str(interaction.user.id))
            self.service.set_custom_dates(state, start_date, end_date)
            embed, view, _page = self.render_panel(state)
            self.service.save_session(state)
            await interaction.response.edit_message(embed=embed, view=view)
        except (SearchSessionError, ValueError) as exc:
            await self.send_session_error(interaction, exc)
        except Exception as exc:
            logger.error("검색 기간 modal 처리 실패: %s", exc, exc_info=True)
            await interaction.response.send_message("기간을 처리하는 중 오류가 발생했습니다.", ephemeral=True)

    async def share_current_page(self, interaction: discord.Interaction, state: SearchPanelState) -> None:
        if state.mode == "hub":
            await interaction.response.send_message("공유할 검색 결과가 없습니다.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        embed, _view, page = self.render_panel(state, public=True)
        if page is None:
            await interaction.followup.send("공유할 검색 결과가 없습니다.", ephemeral=True)
            return
        channel = interaction.channel
        if channel is None or not hasattr(channel, "send"):
            await interaction.followup.send("이 채널에는 공개 메시지를 보낼 수 없습니다.", ephemeral=True)
            return
        await channel.send(embed=embed)
        await interaction.followup.send("현재 결과 페이지를 공개로 공유했습니다.", ephemeral=True)

    async def send_session_error(self, interaction: discord.Interaction, exc: Exception) -> None:
        message = str(exc)
        if isinstance(exc, SearchSessionExpired):
            message = "검색 패널이 만료되었습니다. `/검색`으로 다시 열어주세요."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    def render_panel(
        self,
        state: SearchPanelState,
        *,
        public: bool = False,
    ) -> tuple[discord.Embed, SearchPanelView | None, ResultPage | TextPage | None]:
        page = self.run_state(state)
        title = "검색 패널" if not public else "검색 결과 공유"
        if page:
            title = f"{title} - {page.title}"
        embed = discord.Embed(title=title, color=discord.Color.dark_teal())
        embed.description = self.make_description(state, page)
        embed.set_footer(text=self.make_footer(state, page))
        view = None if public else SearchPanelView(self, state, page)
        return embed, view, page

    def run_state(self, state: SearchPanelState) -> ResultPage | TextPage | None:
        if state.mode == "keyword":
            return self.service.run_keyword_search(state)
        if state.mode == "recap":
            return self.service.run_recap(state)
        if state.mode == "topic":
            return self.service.run_topic_search(state)
        if state.mode == "participants":
            return self.service.run_participants(state)
        return None

    def make_description(self, state: SearchPanelState, page: ResultPage | TextPage | None) -> str:
        header = self.make_filter_summary(state)
        if page is None:
            return (
                f"{header}\n\n"
                "원하는 기능을 골라주세요.\n"
                "검색 결과는 기본적으로 본인에게만 보이며, 필요할 때 공개로 공유할 수 있습니다."
            )
        if isinstance(page, ResultPage):
            lines = self.format_search_results(page)
        else:
            lines = page.lines
        if not lines:
            return f"{header}\n\n{page.empty_message}"
        body = "\n".join(lines)
        return clip(f"{header}\n\n{body}", 3900)

    def format_search_results(self, page: ResultPage) -> list[str]:
        start = page.page * page.page_size
        lines: list[str] = []
        for offset, result in enumerate(page.results, start=1):
            idx = start + offset
            source = clip(result.source_name, 80)
            author = clip(result.author_name, 40)
            lines.append(
                f"{idx}. [{result.message_date}] {source} / {author}\n{result.jump_url}"
            )
        return lines

    def make_filter_summary(self, state: SearchPanelState) -> str:
        start_date, end_date = self.service_date_bounds(state)
        if start_date and end_date:
            date_text = f"{start_date} .. {end_date}"
        elif start_date:
            date_text = f"{start_date} 이후"
        elif end_date:
            date_text = f"{end_date} 이전"
        else:
            date_text = "전체 기간"

        scope_text = {
            "all_indexed": "전체 색인",
            "current_channel": "현재 채널",
            "selected_sources": "선택한 스레드",
        }.get(state.source_scope, state.source_scope)
        query_text = f"`{state.query}`" if state.query else "없음"
        return f"조건: 기간 {date_text} / 범위 {scope_text} / 검색어 {query_text}"

    def service_date_bounds(self, state: SearchPanelState) -> tuple[str | None, str | None]:
        from mogindex_service import date_bounds

        return date_bounds(state)

    def make_footer(self, state: SearchPanelState, page: ResultPage | TextPage | None) -> str:
        if page is None:
            return "기본 기간은 최근 30일입니다."
        if page.total == 0:
            return "0 results"
        page_count = (page.total - 1) // page.page_size + 1
        return f"{page.page + 1}/{page_count} page, {page.total} results"


async def setup(bot: commands.Bot):
    await bot.add_cog(MogIndexCommandsCog(bot))

