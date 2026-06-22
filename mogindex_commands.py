from __future__ import annotations

import asyncio
import logging
import os
import time
import traceback
import uuid
from datetime import date, timedelta
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from mogindex_debug import (
    FULL_INDEX_REST_SECONDS,
    FULL_INDEX_SLOW_DAY_SECONDS,
    connect as index_connect,
    initialize_schema,
)
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
ERROR_REPORT_THREAD_ID = os.getenv("MOGINDEX_ERROR_THREAD_ID")


def clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "..."


def format_duration(seconds: int) -> str:
    if seconds % 60 == 0:
        return f"{seconds // 60}분"
    return f"{seconds}초"


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


class SourceNameModal(discord.ui.Modal):
    def __init__(self, cog: "MogIndexCommandsCog", state: SearchPanelState):
        super().__init__(
            title="채널 이름 지정",
            custom_id=f"mogsearch:{state.session_id}:source_name_modal",
        )
        self.cog = cog
        self.session_id = state.session_id
        self.query = discord.ui.TextInput(
            label="채널 또는 스레드 이름",
            placeholder="예: 차원의 틈새, 차원의-틈새, 카페테리아",
            required=True,
            max_length=80,
        )
        self.add_item(self.query)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_source_name_modal(interaction, self.session_id, self.query.value)


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
        self._add_button("단어 검색", "keyword", discord.ButtonStyle.primary, row=0, active=state.mode == "keyword")
        self._add_button("날짜 요약", "recap", discord.ButtonStyle.secondary, row=0, active=state.mode == "recap")
        self._add_button("토픽 검색", "topic", discord.ButtonStyle.secondary, row=0, active=state.mode == "topic")
        self._add_button("참여자 보기", "participants", discord.ButtonStyle.secondary, row=0, active=state.mode == "participants")

        self._add_button("오늘", "date_today", discord.ButtonStyle.secondary, row=1, active=state.date_preset == "today")
        self._add_button("7일", "date_7d", discord.ButtonStyle.secondary, row=1, active=state.date_preset == "7d")
        self._add_button("30일", "date_30d", discord.ButtonStyle.secondary, row=1, active=state.date_preset == "30d")
        self._add_button("전체", "date_all", discord.ButtonStyle.secondary, row=1, active=state.date_preset == "all")
        self._add_button("직접 기간", "date_custom", discord.ButtonStyle.secondary, row=1, active=state.date_preset == "custom")

        self._add_button("채널 보기", "recent_current", discord.ButtonStyle.primary, row=2, active=state.mode == "recent" and state.source_scope == "current_channel")
        self._add_button("전체 범위", "scope_all", discord.ButtonStyle.secondary, row=2, active=state.source_scope == "all_indexed")
        self._add_button("현재 채널", "scope_current", discord.ButtonStyle.secondary, row=2, active=state.source_scope == "current_channel")
        self._add_button("채널 ID", "scope_source_id", discord.ButtonStyle.secondary, row=2, active=state.source_scope == "selected_sources" and state.source_selector == "id")
        self._add_button("채널 이름", "scope_source_name", discord.ButtonStyle.secondary, row=2, active=state.source_scope == "selected_sources" and state.source_selector == "name")

        if isinstance(page, ResultPage) and page.results:
            self._add_button(
                "이 스레드로 좁히기",
                "scope_first_result",
                discord.ButtonStyle.secondary,
                row=3,
                active=state.source_scope == "selected_sources" and state.source_selector == "result",
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
        self._add_button("필터 리셋", "reset_filters", discord.ButtonStyle.secondary, row=4)
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
        active: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> None:
        button = discord.ui.Button(
            label=label,
            style=discord.ButtonStyle.success if active and not disabled else style,
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
        if action == "scope_source_name":
            await interaction.response.send_modal(SourceNameModal(self, state))
            return
        if action == "share":
            await self.share_current_page(interaction, state)
            return

        try:
            await self.defer_panel_update(interaction)
            if action == "close":
                self.service.close_session(state)
                await interaction.edit_original_response(content="검색 패널을 닫았습니다.", embed=None, view=None)
                return

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
            await interaction.edit_original_response(embed=embed, view=view)
        except ValueError as exc:
            await self.send_session_error(interaction, exc)
        except Exception as exc:
            logger.error("검색 패널 action 처리 실패: %s", exc, exc_info=True)
            await self.report_exception("검색 패널 action 처리 실패", exc, interaction=interaction, state=state, details={"action": action})
            await self.send_user_message(interaction, "검색 패널을 갱신하는 중 오류가 발생했습니다.")

    def apply_action(self, state: SearchPanelState, action: str, extra: dict[str, Any]) -> None:
        if action == "recap":
            state.mode = "recap"
            state.page = 0
        elif action == "participants":
            state.mode = "participants"
            state.page = 0
        elif action in ("recent", "recent_current"):
            if action == "recent_current":
                self.service.set_scope(state, "current_channel")
            state.mode = "recent"
            state.page = 0
        elif action == "reset_filters":
            self.service.reset_filters(state)
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
            state.source_selector = "result"
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
            await self.defer_panel_update(interaction)
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
            await interaction.edit_original_response(embed=embed, view=view)
        except SearchSessionError as exc:
            await self.send_session_error(interaction, exc)
        except Exception as exc:
            logger.error("검색 modal 처리 실패: %s", exc, exc_info=True)
            await self.report_exception("검색 modal 처리 실패", exc, interaction=interaction, details={"mode": mode})
            await self.send_user_message(interaction, "검색어를 처리하는 중 오류가 발생했습니다.")

    async def handle_source_modal(
        self,
        interaction: discord.Interaction,
        session_id: str,
        source_id: str,
    ) -> None:
        try:
            await self.defer_panel_update(interaction)
            clean_source_id = source_id.strip()
            if not clean_source_id.isdigit():
                raise ValueError("채널/스레드 ID는 숫자로 입력해주세요.")
            state = self.service.load_session(session_id, str(interaction.user.id))
            previous_mode = state.mode
            self.service.set_scope(state, "selected_sources", [clean_source_id])
            state.source_selector = "id"
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
            self.service.save_session(state)
            await interaction.edit_original_response(embed=embed, view=view)
        except (SearchSessionError, ValueError) as exc:
            await self.send_session_error(interaction, exc)
        except Exception as exc:
            logger.error("검색 채널 ID modal 처리 실패: %s", exc, exc_info=True)
            await self.report_exception("검색 채널 ID modal 처리 실패", exc, interaction=interaction, details={"source_id": source_id})
            await self.send_user_message(interaction, "채널 ID를 처리하는 중 오류가 발생했습니다.")

    async def handle_source_name_modal(
        self,
        interaction: discord.Interaction,
        session_id: str,
        query: str,
    ) -> None:
        try:
            await self.defer_panel_update(interaction)
            clean_query = query.strip()
            state = self.service.load_session(session_id, str(interaction.user.id))
            matches = self.service.find_sources_by_name(clean_query)
            if not matches:
                raise ValueError(f"'{clean_query}'와 일치하는 색인 채널/스레드를 찾지 못했습니다.")
            previous_mode = state.mode
            source_ids = [match.source_id for match in matches]
            self.service.set_scope(state, "selected_sources", source_ids)
            state.source_selector = "name"
            logger.info(
                "mogindex source name modal user=%s session=%s query=%r matches=%s names=%s previous_mode=%s",
                interaction.user.id,
                session_id,
                clean_query,
                len(matches),
                [match.name for match in matches[:5]],
                previous_mode,
            )
            if previous_mode == "hub":
                state.mode = "recent"
                if state.date_preset == "30d":
                    self.service.set_date_preset(state, "all")
            state.page = 0
            embed, view, _page = self.render_panel(state)
            if len(matches) > 1:
                embed.add_field(
                    name="채널 이름 매칭",
                    value=f"{len(matches)}개 source를 범위로 선택했습니다. 너무 넓으면 더 긴 이름으로 다시 지정해주세요.",
                    inline=False,
                )
            self.service.save_session(state)
            await interaction.edit_original_response(embed=embed, view=view)
        except (SearchSessionError, ValueError) as exc:
            await self.send_session_error(interaction, exc)
        except Exception as exc:
            logger.error("검색 채널 이름 modal 처리 실패: %s", exc, exc_info=True)
            await self.report_exception("검색 채널 이름 modal 처리 실패", exc, interaction=interaction, details={"query": query})
            await self.send_user_message(interaction, "채널 이름을 처리하는 중 오류가 발생했습니다.")

    async def handle_date_modal(
        self,
        interaction: discord.Interaction,
        session_id: str,
        start_date: str,
        end_date: str,
    ) -> None:
        try:
            await self.defer_panel_update(interaction)
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
            await interaction.edit_original_response(embed=embed, view=view)
        except (SearchSessionError, ValueError) as exc:
            await self.send_session_error(interaction, exc)
        except Exception as exc:
            logger.error("검색 기간 modal 처리 실패: %s", exc, exc_info=True)
            await self.report_exception("검색 기간 modal 처리 실패", exc, interaction=interaction, details={"start_date": start_date, "end_date": end_date})
            await self.send_user_message(interaction, "기간을 처리하는 중 오류가 발생했습니다.")

    async def share_current_page(self, interaction: discord.Interaction, state: SearchPanelState) -> None:
        if state.mode == "hub":
            await self.send_user_message(interaction, "공유할 검색 결과가 없습니다.")
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
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
        except Exception as exc:
            logger.error("검색 결과 공유 실패: %s", exc, exc_info=True)
            await self.report_exception("검색 결과 공유 실패", exc, interaction=interaction, state=state)
            await self.send_user_message(interaction, "검색 결과를 공유하는 중 오류가 발생했습니다.")

    async def defer_panel_update(self, interaction: discord.Interaction) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer(thinking=False)

    async def send_user_message(self, interaction: discord.Interaction, message: str) -> None:
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.NotFound:
            logger.warning("mogindex interaction response expired while sending message=%r", message)

    async def send_session_error(self, interaction: discord.Interaction, exc: Exception) -> None:
        message = str(exc)
        if isinstance(exc, SearchSessionExpired):
            message = "검색 패널이 만료되었습니다. `/검색`으로 다시 열어주세요."
        await self.send_user_message(interaction, message)

    async def report_exception(
        self,
        title: str,
        exc: Exception,
        *,
        interaction: discord.Interaction | None = None,
        state: SearchPanelState | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        if not ERROR_REPORT_THREAD_ID:
            return
        try:
            target = self.bot.get_channel(ERROR_REPORT_THREAD_ID)
            if target is None:
                target = await self.bot.fetch_channel(ERROR_REPORT_THREAD_ID)
            if target is None or not hasattr(target, "send"):
                logger.warning("mogindex error report target unavailable thread_id=%s", ERROR_REPORT_THREAD_ID)
                return

            context_lines = [f"title={title}", f"error={type(exc).__name__}: {exc}"]
            if interaction is not None:
                context_lines.append(f"user={getattr(interaction.user, 'id', None)}")
                context_lines.append(f"channel={interaction.channel_id}")
                context_lines.append(f"guild={interaction.guild_id}")
            if state is not None:
                context_lines.append(
                    "state="
                    f"session={state.session_id} mode={state.mode} scope={state.source_scope} "
                    f"sources={state.source_ids} preset={state.date_preset} page={state.page}"
                )
            if details:
                context_lines.append(f"details={details}")
            trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            content = "**mogindex 오류 보고**\n" + "\n".join(context_lines)
            content += "\n```py\n" + clip(trace, 1300) + "\n```"
            await target.send(clip(content, 1900))
        except Exception:
            logger.error("mogindex error report failed", exc_info=True)

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
        matching_sources = None
        if state.source_scope != "all_indexed":
            try:
                matching_sources = self.service.count_matching_sources(state)
                if matching_sources == 0:
                    embed.add_field(
                        name="범위 확인",
                        value="현재 범위와 일치하는 색인 source가 없습니다. 채널/스레드 ID가 맞는지, 해당 범위가 backfill되었는지 확인해주세요.",
                        inline=False,
                    )
            except Exception as exc:
                logger.warning("mogindex source count failed session=%s error=%s", state.session_id, exc)
        logger.info(
            "mogindex render session=%s public=%s mode=%s scope=%s sources=%s preset=%s page_title=%s total=%s page=%s matching_sources=%s",
            state.session_id,
            public,
            state.mode,
            state.source_scope,
            state.source_ids,
            state.date_preset,
            getattr(page, "title", None),
            getattr(page, "total", None),
            getattr(page, "page", None),
            matching_sources,
        )
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
        note = ""
        if state.mode == "recent":
            note = "\n이 모드는 검색어를 사용하지 않고 현재 기간/범위의 색인 메시지를 보여줍니다."
        if not lines:
            return f"{header}{note}\n\n{page.empty_message}"
        body = "\n".join(lines)
        return clip(f"{header}{note}\n\n{body}", 3900)

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
            "selected_sources": "선택한 채널/스레드",
        }.get(state.source_scope, state.source_scope)
        mode_text = {
            "hub": "기능 선택",
            "keyword": "단어 검색",
            "topic": "토픽 검색",
            "recap": "날짜 요약",
            "participants": "참여자 보기",
            "recent": "채널 보기",
        }.get(state.mode, state.mode)
        if state.mode in ("keyword", "topic"):
            query_text = f"`{state.query}`" if state.query else "없음"
        else:
            query_text = "사용 안 함"
        return f"조건: 모드 {mode_text} / 기간 {date_text} / 범위 {scope_text} / 검색어 {query_text}"

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

    @app_commands.command(name="전체색인", description="[관리자] 전체색인 상태를 확인하거나 천천히 이어서 실행합니다.")
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.describe(action="전체색인 작업")
    @app_commands.rename(action="작업")
    @app_commands.choices(
        action=[
            app_commands.Choice(name="상태확인", value="status"),
            app_commands.Choice(name="새로시작", value="start"),
            app_commands.Choice(name="이어하기", value="resume"),
        ]
    )
    async def full_index(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str] = None,
    ) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("서버 안에서만 사용할 수 있습니다.", ephemeral=True)
            return

        action_value = action.value if action else "status"
        if self.full_index_task and not self.full_index_task.done():
            if action_value == "status":
                await interaction.response.send_message(self.format_full_index_status(), ephemeral=True)
                return
            await interaction.response.send_message("전체색인이 이미 실행 중입니다. 상태확인을 사용해주세요.", ephemeral=True)
            return

        self.mark_interrupted_full_index_runs()
        if action_value == "status":
            await interaction.response.send_message(self.format_full_index_status(), ephemeral=True)
            return
        if action_value == "resume":
            await self.start_full_index_resume(interaction)
            return
        await self.start_full_index_new(interaction)

    async def start_full_index_new(self, interaction: discord.Interaction) -> None:
        start_date = now_kst().date()
        end_date = date(2024, 6, 5)
        parent_ids = list(DEFAULT_PARENT_CHANNEL_IDS)
        run_id = self.create_full_index_run(
            guild_id=str(interaction.guild_id),
            requested_by=str(interaction.user.id),
            start_date=start_date,
            end_date=end_date,
            notify_channel_id=str(interaction.channel_id),
            notify_user_id=str(interaction.user.id),
        )
        self.full_index_task = asyncio.create_task(
            self.run_full_index_job(
                run_id=run_id,
                guild_id=str(interaction.guild_id),
                requested_by=str(interaction.user.id),
                start_date=start_date,
                end_date=end_date,
                parent_ids=parent_ids,
                notify_channel_id=str(interaction.channel_id),
                notify_user_id=str(interaction.user.id),
            )
        )
        logger.info(
            "mogindex full-index command started run=%s by=%s guild=%s start=%s end=%s parents=%s",
            run_id,
            interaction.user.id,
            interaction.guild_id,
            start_date,
            end_date,
            parent_ids,
        )
        await interaction.response.send_message(
            f"전체색인을 백그라운드에서 새로 시작했습니다. run={run_id[:8]} / {start_date}부터 {end_date}까지 진행합니다. "
            f"하루 처리 시간이 {format_duration(FULL_INDEX_SLOW_DAY_SECONDS)} 이상이면 "
            f"{format_duration(FULL_INDEX_REST_SECONDS)} 쉬고, 더 빠르면 바로 다음 날짜로 넘어갑니다.",
            ephemeral=True,
        )

    async def start_full_index_resume(self, interaction: discord.Interaction) -> None:
        resume = self.get_full_index_resume_plan(
            requested_by=str(interaction.user.id),
            notify_channel_id=str(interaction.channel_id),
            notify_user_id=str(interaction.user.id),
        )
        if resume is None:
            await interaction.response.send_message("이어갈 전체색인 기록이 없습니다.", ephemeral=True)
            return
        run_id, start_date, end_date, guild_id, requested_by, notify_channel_id, notify_user_id = resume
        if start_date < end_date:
            await interaction.response.send_message("이미 완료된 색인입니다.", ephemeral=True)
            return
        parent_ids = list(DEFAULT_PARENT_CHANNEL_IDS)
        self.full_index_task = asyncio.create_task(
            self.run_full_index_job(
                run_id=run_id,
                guild_id=guild_id,
                requested_by=requested_by,
                start_date=start_date,
                end_date=end_date,
                parent_ids=parent_ids,
                notify_channel_id=notify_channel_id,
                notify_user_id=notify_user_id,
            )
        )
        logger.info(
            "mogindex full-index command resumed run=%s by=%s guild=%s start=%s end=%s parents=%s",
            run_id,
            interaction.user.id,
            guild_id,
            start_date,
            end_date,
            parent_ids,
        )
        await interaction.response.send_message(
            f"전체색인을 이어서 시작했습니다. run={run_id[:8]} / {start_date}부터 {end_date}까지 진행합니다.",
            ephemeral=True,
        )

    def create_full_index_run(
        self,
        *,
        guild_id: str,
        requested_by: str,
        start_date: date,
        end_date: date,
        notify_channel_id: str | None,
        notify_user_id: str | None,
    ) -> str:
        run_id = uuid.uuid4().hex
        now = now_kst().isoformat()
        with self.service.open() as conn:
            conn.execute(
                """
                INSERT INTO full_index_runs (
                    run_id, guild_id, requested_by, status, start_date, end_date,
                    current_date, last_completed_date, notify_channel_id, notify_user_id,
                    created_at, updated_at, finished_at
                )
                VALUES (?, ?, ?, 'running', ?, ?, ?, NULL, ?, ?, ?, ?, NULL)
                """,
                (
                    run_id,
                    guild_id,
                    requested_by,
                    start_date.isoformat(),
                    end_date.isoformat(),
                    start_date.isoformat(),
                    notify_channel_id,
                    notify_user_id,
                    now,
                    now,
                ),
            )
            conn.commit()
        return run_id

    def mark_interrupted_full_index_runs(self) -> None:
        now = now_kst().isoformat()
        with self.service.open() as conn:
            conn.execute(
                """
                UPDATE full_index_days
                SET status = 'interrupted', finished_at = ?,
                    error_text = COALESCE(error_text, 'bot stopped before completion')
                WHERE status = 'running'
                """,
                (now,),
            )
            conn.execute(
                """
                UPDATE full_index_runs
                SET status = 'interrupted', updated_at = ?
                WHERE status = 'running'
                """,
                (now,),
            )
            conn.commit()

    def get_latest_full_index_run(self, *, incomplete_only: bool = False) -> dict[str, Any] | None:
        with self.service.open() as conn:
            where = "WHERE status IN ('running', 'interrupted', 'failed')" if incomplete_only else ""
            row = conn.execute(
                f"""
                SELECT *
                FROM full_index_runs
                {where}
                ORDER BY created_at DESC
                LIMIT 1
                """
            ).fetchone()
            return dict(row) if row else None

    def get_full_index_resume_plan(
        self,
        *,
        requested_by: str,
        notify_channel_id: str | None,
        notify_user_id: str | None,
    ) -> tuple[str, date, date, str, str, str | None, str | None] | None:
        now = now_kst().isoformat()
        with self.service.open() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM full_index_runs
                WHERE status IN ('running', 'interrupted', 'failed')
                ORDER BY created_at DESC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            resume_date = self.resolve_full_index_resume_date(conn, row)
            if resume_date is None:
                return None
            end_date = date.fromisoformat(row["end_date"])
            conn.execute(
                """
                UPDATE full_index_runs
                SET status = 'running', requested_by = ?, current_date = ?,
                    notify_channel_id = ?, notify_user_id = ?, updated_at = ?, finished_at = NULL
                WHERE run_id = ?
                """,
                (requested_by, resume_date.isoformat(), notify_channel_id, notify_user_id, now, row["run_id"]),
            )
            conn.commit()
            return (
                row["run_id"],
                resume_date,
                end_date,
                row["guild_id"],
                requested_by,
                notify_channel_id,
                notify_user_id,
            )

    def resolve_full_index_resume_date(self, conn: Any, run_row: Any) -> date | None:
        if run_row["status"] == "completed":
            return None
        blocked = conn.execute(
            """
            SELECT index_date
            FROM full_index_days
            WHERE run_id = ? AND status IN ('failed', 'interrupted', 'running')
            ORDER BY index_date DESC
            LIMIT 1
            """,
            (run_row["run_id"],),
        ).fetchone()
        if blocked:
            return date.fromisoformat(blocked["index_date"])
        if run_row["last_completed_date"]:
            return date.fromisoformat(run_row["last_completed_date"]) - timedelta(days=1)
        if run_row["current_date"]:
            return date.fromisoformat(run_row["current_date"])
        return date.fromisoformat(run_row["start_date"])

    def format_full_index_status(self) -> str:
        with self.service.open() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM full_index_runs
                ORDER BY created_at DESC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return "이어갈 전체색인 기록이 없습니다. 새로 시작하려면 `/전체색인 작업:새로시작`을 사용해주세요."
            counts = conn.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM full_index_days
                WHERE run_id = ?
                GROUP BY status
                """,
                (row["run_id"],),
            ).fetchall()
            resume_date = self.resolve_full_index_resume_date(conn, row)
        count_map = {item["status"]: item["count"] for item in counts}
        status_names = {
            "running": "실행 중",
            "interrupted": "중단됨",
            "failed": "실패",
            "completed": "완료",
        }
        next_text = resume_date.isoformat() if resume_date else "완료 또는 이어갈 날짜 없음"
        count_text = ", ".join(
            f"{status_names.get(status, status)} {count}일" for status, count in sorted(count_map.items())
        ) or "처리 기록 없음"
        return (
            "전체색인 상태\n"
            f"run: {row['run_id'][:8]}\n"
            f"상태: {status_names.get(row['status'], row['status'])}\n"
            f"범위: {row['start_date']} .. {row['end_date']}\n"
            f"현재 날짜: {row['current_date'] or '-'}\n"
            f"마지막 완료: {row['last_completed_date'] or '-'}\n"
            f"다음 이어하기: {next_text}\n"
            f"일자 기록: {count_text}"
        )

    def mark_full_index_day_started(self, run_id: str, index_date: date) -> None:
        now = now_kst().isoformat()
        with self.service.open() as conn:
            conn.execute(
                """
                INSERT INTO full_index_days (
                    run_id, index_date, status, targets, scanned, indexed,
                    elapsed_seconds, error_text, started_at, finished_at
                )
                VALUES (?, ?, 'running', 0, 0, 0, 0, NULL, ?, NULL)
                ON CONFLICT(run_id, index_date) DO UPDATE SET
                    status = 'running', targets = 0, scanned = 0, indexed = 0,
                    elapsed_seconds = 0, error_text = NULL,
                    started_at = excluded.started_at, finished_at = NULL
                """,
                (run_id, index_date.isoformat(), now),
            )
            conn.execute(
                """
                UPDATE full_index_runs
                SET status = 'running', current_date = ?, updated_at = ?, finished_at = NULL
                WHERE run_id = ?
                """,
                (index_date.isoformat(), now, run_id),
            )
            conn.commit()

    def mark_full_index_day_completed(
        self,
        run_id: str,
        index_date: date,
        *,
        next_date: date | None,
        targets: int,
        scanned: int,
        indexed: int,
        elapsed_seconds: float,
    ) -> None:
        now = now_kst().isoformat()
        with self.service.open() as conn:
            conn.execute(
                """
                INSERT INTO full_index_days (
                    run_id, index_date, status, targets, scanned, indexed,
                    elapsed_seconds, error_text, started_at, finished_at
                )
                VALUES (?, ?, 'completed', ?, ?, ?, ?, NULL, ?, ?)
                ON CONFLICT(run_id, index_date) DO UPDATE SET
                    status = 'completed', targets = excluded.targets,
                    scanned = excluded.scanned, indexed = excluded.indexed,
                    elapsed_seconds = excluded.elapsed_seconds, error_text = NULL,
                    finished_at = excluded.finished_at
                """,
                (run_id, index_date.isoformat(), targets, scanned, indexed, elapsed_seconds, now, now),
            )
            conn.execute(
                """
                UPDATE full_index_runs
                SET status = 'running', last_completed_date = ?, current_date = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (index_date.isoformat(), next_date.isoformat() if next_date else None, now, run_id),
            )
            conn.commit()

    def mark_full_index_day_failed(
        self,
        run_id: str,
        index_date: date,
        *,
        status: str,
        targets: int,
        scanned: int,
        indexed: int,
        elapsed_seconds: float,
        error_text: str,
    ) -> None:
        now = now_kst().isoformat()
        error_text = error_text[:1000]
        with self.service.open() as conn:
            conn.execute(
                """
                INSERT INTO full_index_days (
                    run_id, index_date, status, targets, scanned, indexed,
                    elapsed_seconds, error_text, started_at, finished_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, index_date) DO UPDATE SET
                    status = excluded.status, targets = excluded.targets,
                    scanned = excluded.scanned, indexed = excluded.indexed,
                    elapsed_seconds = excluded.elapsed_seconds,
                    error_text = excluded.error_text, finished_at = excluded.finished_at
                """,
                (run_id, index_date.isoformat(), status, targets, scanned, indexed, elapsed_seconds, error_text, now, now),
            )
            conn.execute(
                """
                UPDATE full_index_runs
                SET status = ?, current_date = ?, updated_at = ?, finished_at = ?
                WHERE run_id = ?
                """,
                (status, index_date.isoformat(), now, now, run_id),
            )
            conn.commit()

    def mark_full_index_run_completed(self, run_id: str) -> None:
        now = now_kst().isoformat()
        with self.service.open() as conn:
            conn.execute(
                """
                UPDATE full_index_runs
                SET status = 'completed', current_date = NULL, updated_at = ?, finished_at = ?
                WHERE run_id = ?
                """,
                (now, now, run_id),
            )
            conn.commit()

    async def run_full_index_job(
        self,
        *,
        run_id: str,
        guild_id: str,
        requested_by: str,
        start_date: date,
        end_date: date,
        parent_ids: list[str],
        notify_channel_id: str | None = None,
        notify_user_id: str | None = None,
    ) -> None:
        current = start_date
        while current >= end_date:
            day_started = time.perf_counter()
            day_elapsed = 0.0
            total_scanned = 0
            total_indexed = 0
            target_elapsed_total = 0.0
            targets_count = 0
            self.mark_full_index_day_started(run_id, current)
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
                targets_count = len(targets)
                for target in targets:
                    target_started = time.perf_counter()
                    conn = self.service.connect()
                    try:
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
                    finally:
                        conn.close()
                    target_elapsed = time.perf_counter() - target_started
                    target_elapsed_total += target_elapsed
                    total_scanned += scanned
                    total_indexed += indexed
                    logger.info(
                        "mogindex full-index target run=%s day=%s source=%s name=%s scanned=%s indexed=%s elapsed=%.2fs",
                        run_id,
                        current,
                        getattr(target, "id", None),
                        getattr(target, "name", None),
                        scanned,
                        indexed,
                        target_elapsed,
                    )
                    await asyncio.sleep(0)
                day_elapsed = time.perf_counter() - day_started
                avg_target_elapsed = target_elapsed_total / targets_count if targets_count else 0.0
                logger.info(
                    "mogindex full-index day run=%s day=%s targets=%s scanned=%s indexed=%s elapsed=%.2fs avg_target=%.2fs requested_by=%s",
                    run_id,
                    current,
                    targets_count,
                    total_scanned,
                    total_indexed,
                    day_elapsed,
                    avg_target_elapsed,
                    requested_by,
                )
            except asyncio.CancelledError:
                day_elapsed = time.perf_counter() - day_started
                self.mark_full_index_day_failed(
                    run_id,
                    current,
                    status="interrupted",
                    targets=targets_count,
                    scanned=total_scanned,
                    indexed=total_indexed,
                    elapsed_seconds=day_elapsed,
                    error_text="task cancelled",
                )
                logger.warning("mogindex full-index cancelled run=%s day=%s requested_by=%s", run_id, current, requested_by)
                raise
            except Exception as exc:
                day_elapsed = time.perf_counter() - day_started
                self.mark_full_index_day_failed(
                    run_id,
                    current,
                    status="failed",
                    targets=targets_count,
                    scanned=total_scanned,
                    indexed=total_indexed,
                    elapsed_seconds=day_elapsed,
                    error_text=f"{type(exc).__name__}: {exc}",
                )
                logger.error("mogindex full-index day failed run=%s day=%s elapsed=%.2fs error=%s", run_id, current, day_elapsed, exc, exc_info=True)
                await self.report_exception(
                    "전체색인 하루 처리 실패",
                    exc,
                    details={"run_id": run_id, "day": str(current), "elapsed_seconds": round(day_elapsed, 2), "requested_by": requested_by},
                )
                return

            next_date = None if current == end_date else current - timedelta(days=1)
            self.mark_full_index_day_completed(
                run_id,
                current,
                next_date=next_date,
                targets=targets_count,
                scanned=total_scanned,
                indexed=total_indexed,
                elapsed_seconds=day_elapsed,
            )
            if current == end_date:
                break
            completed_day = current
            current = next_date
            rest_seconds = FULL_INDEX_REST_SECONDS if day_elapsed >= FULL_INDEX_SLOW_DAY_SECONDS else 0
            logger.info(
                "mogindex full-index pacing run=%s completed_day=%s next_day=%s elapsed=%.2fs threshold=%ss rest=%ss",
                run_id,
                completed_day,
                current,
                day_elapsed,
                FULL_INDEX_SLOW_DAY_SECONDS,
                rest_seconds,
            )
            if rest_seconds > 0:
                await asyncio.sleep(rest_seconds)
        self.mark_full_index_run_completed(run_id)
        await self.send_full_index_completion_notice(
            notify_channel_id=notify_channel_id,
            notify_user_id=notify_user_id,
            start_date=start_date,
            end_date=end_date,
        )
        logger.info("mogindex full-index finished run=%s requested_by=%s", run_id, requested_by)

    async def send_full_index_completion_notice(
        self,
        *,
        notify_channel_id: str | None,
        notify_user_id: str | None,
        start_date: date,
        end_date: date,
    ) -> None:
        if not notify_channel_id or not notify_user_id:
            return
        try:
            channel = self.bot.get_channel(int(notify_channel_id))
            if channel is None:
                channel = await self.bot.fetch_channel(int(notify_channel_id))
            if channel is None or not hasattr(channel, "send"):
                logger.warning(
                    "mogindex full-index completion channel unavailable channel_id=%s",
                    notify_channel_id,
                )
                return
            await channel.send(
                f"<@{notify_user_id}> 전체색인이 완료되었습니다. "
                f"범위: {start_date} .. {end_date}"
            )
        except Exception as exc:
            logger.error("mogindex full-index completion notice failed: %s", exc, exc_info=True)
            await self.report_exception(
                "전체색인 완료 알림 실패",
                exc,
                details={
                    "notify_channel_id": notify_channel_id,
                    "notify_user_id": notify_user_id,
                    "start_date": str(start_date),
                    "end_date": str(end_date),
                },
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(MogIndexCommandsCog(bot))

