from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from mogindex_debug import connect as index_connect, initialize_schema
from mogindex_discord_debug import (
    CATEGORY_ID,
    DEFAULT_PARENT_CHANNEL_IDS,
    collect_source,
    date_bounds as discord_date_bounds,
    gather_targets,
)
from mogindex_service import (
    DatePreset,
    MogIndexService,
    ResultPage,
    SearchPanelState,
    SearchSessionError,
    SearchSessionExpired,
    SourceScope,
    TextPage,
    now_kst,
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



class SourceIdModal(discord.ui.Modal):
    def __init__(self, cog: "MogIndexCommandsCog", state: SearchPanelState):
        super().__init__(
            title="채널/스레드 ID 지정",
            custom_id=f"mogsearch:{state.session_id}:source_modal",
        )
        self.cog = cog
        self.session_id = state.session_id
        self.source_id = discord.ui.TextInput(
            label="채널 또는 스레드 ID",
            placeholder="예: 1485663318000799744",
            required=True,
            max_length=32,
        )
        self.add_item(self.source_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_source_modal(interaction, self.session_id, self.source_id.value)


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
        self._add_button("채널 보기", "recent", discord.ButtonStyle.primary, row=0)
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
        self._add_button("채널 ID", "scope_source_id", discord.ButtonStyle.secondary, row=2)
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
        self.full_index_task: asyncio.Task | None = None

    @app_commands.command(name="검색", description="색인된 커뮤 로그를 검색합니다.")
    async def search_panel(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("서버 안에서만 사용할 수 있습니다.", ephemeral=True)
            return
        state = self.service.create_session(interaction)
        logger.info(
            "mogindex panel opened session=%s user=%s guild=%s channel=%s",
            state.session_id,
            state.owner_user_id,
            state.guild_id,
            state.origin_channel_id,
        )
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
            logger.info(
                "mogindex action user=%s session=%s action=%s mode=%s scope=%s sources=%s preset=%s page=%s extra=%s",
                interaction.user.id,
                session_id,
                action,
                state.mode,
                state.source_scope,
                state.source_ids,
                state.date_preset,
                state.page,
                extra,
            )
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
        if action == "scope_source_id":
            await interaction.response.send_modal(SourceIdModal(self, state))
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
            logger.info(
                "mogindex action applied session=%s action=%s mode=%s scope=%s sources=%s preset=%s page=%s",
                state.session_id,
                action,
                state.mode,
                state.source_scope,
                state.source_ids,
                state.date_preset,
                state.page,
            )
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
        elif action == "recent":
            state.mode = "recent"
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
            if state.mode == "hub":
                state.mode = "recent"
        elif action == "scope_current":
            self.service.set_scope(state, "current_channel")
            if state.mode == "hub":
                state.mode = "recent"
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
            logger.info(
                "mogindex modal query user=%s session=%s mode=%s query=%r",
                interaction.user.id,
                session_id,
                mode,
                state.query,
            )
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


    async def handle_source_modal(
        self,
        interaction: discord.Interaction,
        session_id: str,
        source_id: str,
    ) -> None:
        try:
            clean_source_id = source_id.strip()
            if not clean_source_id.isdigit():
                raise ValueError("채널/스레드 ID는 숫자로 입력해주세요.")
            state = self.service.load_session(session_id, str(interaction.user.id))
            previous_mode = state.mode
            self.service.set_scope(state, "selected_sources", [clean_source_id])
            matching_sources = self.service.count_matching_sources(state)
            logger.info(
                "mogindex source modal user=%s session=%s source_id=%s matching_sources=%s previous_mode=%s",
                interaction.user.id,
                session_id,
                clean_source_id,
                matching_sources,
                previous_mode,
            )
            if previous_mode == "hub":
                state.mode = "recent"
                if state.date_preset == "30d":
                    self.service.set_date_preset(state, "all")
            state.page = 0
            embed, view, _page = self.render_panel(state)
            if matching_sources == 0:
                embed.add_field(
                    name="범위 확인",
                    value="이 ID와 일치하는 색인 source가 아직 없습니다. 먼저 해당 채널/스레드를 backfill했는지 확인해주세요.",
                    inline=False,
                )
            self.service.save_session(state)
            await interaction.response.edit_message(embed=embed, view=view)
        except (SearchSessionError, ValueError) as exc:
            await self.send_session_error(interaction, exc)
        except Exception as exc:
            logger.error("검색 채널 ID modal 처리 실패: %s", exc, exc_info=True)
            await interaction.response.send_message("채널 ID를 처리하는 중 오류가 발생했습니다.", ephemeral=True)

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
            logger.info(
                "mogindex date modal user=%s session=%s start=%s end=%s",
                interaction.user.id,
                session_id,
                state.start_date,
                state.end_date,
            )
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
        logger.info(
            "mogindex shared session=%s user=%s mode=%s channel=%s",
            state.session_id,
            interaction.user.id,
            state.mode,
            getattr(channel, "id", None),
        )
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
        if state.mode == "recent":
            return self.service.run_recent(state)
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

    @app_commands.command(name="전체색인", description="[관리자] 오늘부터 2024-06-13까지 하루씩 천천히 색인합니다.")
    @app_commands.default_permissions(manage_roles=True)
    async def full_index(self, interaction: discord.Interaction) -> None:
        if self.full_index_task and not self.full_index_task.done():
            await interaction.response.send_message("전체색인이 이미 실행 중입니다. 서버 로그를 확인해주세요.", ephemeral=True)
            return
        if not interaction.guild_id:
            await interaction.response.send_message("서버 안에서만 사용할 수 있습니다.", ephemeral=True)
            return
        start_date = now_kst().date()
        end_date = date(2024, 6, 13)
        parent_ids = list(DEFAULT_PARENT_CHANNEL_IDS)
        self.full_index_task = asyncio.create_task(
            self.run_full_index_job(
                guild_id=str(interaction.guild_id),
                requested_by=str(interaction.user.id),
                start_date=start_date,
                end_date=end_date,
                parent_ids=parent_ids,
            )
        )
        logger.info(
            "mogindex full-index command started by=%s guild=%s start=%s end=%s parents=%s",
            interaction.user.id,
            interaction.guild_id,
            start_date,
            end_date,
            parent_ids,
        )
        await interaction.response.send_message(
            f"전체색인을 백그라운드에서 시작했습니다. {start_date}부터 {end_date}까지 10분에 하루씩 진행합니다.",
            ephemeral=True,
        )

    async def run_full_index_job(
        self,
        *,
        guild_id: str,
        requested_by: str,
        start_date: date,
        end_date: date,
        parent_ids: list[str],
    ) -> None:
        current = start_date
        while current >= end_date:
            try:
                after, before = discord_date_bounds(current, current, "Asia/Seoul")
                targets = await gather_targets(
                    self.bot,
                    parent_ids,
                    category_id=CATEGORY_ID,
                    include_threads=True,
                    include_private_archived=False,
                    thread_after=after,
                    thread_before=before,
                )
                conn = self.service.connect()
                try:
                    initialize_schema(conn)
                    total_scanned = 0
                    total_indexed = 0
                    for target in targets:
                        scanned, indexed = await collect_source(
                            conn,
                            target,
                            guild_id=guild_id,
                            category_id=CATEGORY_ID,
                            after=after,
                            before=before,
                            limit=None,
                            dry_run=False,
                        )
                        total_scanned += scanned
                        total_indexed += indexed
                    logger.info(
                        "mogindex full-index day=%s targets=%s scanned=%s indexed=%s requested_by=%s",
                        current,
                        len(targets),
                        total_scanned,
                        total_indexed,
                        requested_by,
                    )
                finally:
                    conn.close()
            except asyncio.CancelledError:
                logger.warning("mogindex full-index cancelled day=%s requested_by=%s", current, requested_by)
                raise
            except Exception as exc:
                logger.error("mogindex full-index day failed day=%s error=%s", current, exc, exc_info=True)
            if current == end_date:
                break
            current -= timedelta(days=1)
            await asyncio.sleep(600)
        logger.info("mogindex full-index finished requested_by=%s", requested_by)


async def setup(bot: commands.Bot):
    await bot.add_cog(MogIndexCommandsCog(bot))

