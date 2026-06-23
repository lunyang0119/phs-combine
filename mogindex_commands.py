from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from mogindex_debug import (
    FULL_INDEX_DISCORD_RETRY_ATTEMPTS,
    FULL_INDEX_DISCORD_RETRY_SECONDS,
    FULL_INDEX_PARALLEL_SOURCES,
    FULL_INDEX_REST_SECONDS,
    FULL_INDEX_SLOW_DAY_SECONDS,
    FULL_INDEX_SOURCE_TIMEOUT_SECONDS,
    connect as index_connect,
    initialize_schema,
)
from mogindex_discord_debug import (
    CATEGORY_ID,
    DEFAULT_PARENT_CHANNEL_IDS,
    collect_source,
    date_bounds as discord_date_bounds,
    gather_targets,
    thread_might_have_messages,
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


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw.strip())
    except ValueError:
        logger.warning("mogindex invalid integer env %s=%r; using %s", name, raw, default)
        return default


def env_csv(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None:
        return default
    values = tuple(part.strip() for part in raw.split(",") if part.strip())
    return values or default


ERROR_REPORT_THREAD_ID = os.getenv("MOGINDEX_ERROR_THREAD_ID")
INDEX_CATEGORY_ENV = "MOG_INDEX_CATEGORIES_JSON"
DAILY_INDEX_ENABLED = env_bool("MOGINDEX_DAILY_ENABLED", True)
DAILY_INDEX_HOUR = max(0, min(23, env_int("MOGINDEX_DAILY_HOUR", 23)))
DAILY_INDEX_MINUTE = max(0, min(59, env_int("MOGINDEX_DAILY_MINUTE", 59)))
DAILY_INDEX_CATEGORY_NAMES = env_csv("MOGINDEX_DAILY_CATEGORIES", ("메인 메뉴", "월드 맵", "커스텀 모드"))
DAILY_INDEX_GUILD_ID = os.getenv("MOG_GUILD_ID")
DAILY_INDEX_RUN_KIND = "daily"
DAILY_INDEX_SCHEDULE_KEY = "daily-2359"
FULL_INDEX_DEFAULT_OLDEST_DATE = date(2024, 6, 5)


def clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "..."


def format_duration(seconds: int) -> str:
    if seconds % 60 == 0:
        return f"{seconds // 60}분"
    return f"{seconds}초"


@dataclass
class DbWriteRequest:
    callback: Any
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    future: asyncio.Future


class DbWriteQueue:
    def __init__(self, label: str):
        self.label = label
        self._queue: asyncio.Queue[DbWriteRequest] = asyncio.Queue()
        self._worker_task: asyncio.Task | None = None

    def ensure_worker(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker())

    async def run(self, callback: Any, *args: Any, **kwargs: Any) -> Any:
        self.ensure_worker()
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        await self._queue.put(DbWriteRequest(callback, args, kwargs, future))
        return await future

    async def _worker(self) -> None:
        while True:
            request = await self._queue.get()
            try:
                result = await self._run_with_retry(request.callback, *request.args, **request.kwargs)
                if not request.future.cancelled():
                    request.future.set_result(result)
            except Exception as exc:
                if not request.future.cancelled():
                    request.future.set_exception(exc)
            finally:
                self._queue.task_done()

    async def _run_with_retry(self, callback: Any, *args: Any, **kwargs: Any) -> Any:
        for attempt in range(6):
            try:
                return await asyncio.to_thread(callback, *args, **kwargs)
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt >= 5:
                    raise
                logger.warning(
                    "%s locked during write attempt=%s/6 callback=%s",
                    self.label,
                    attempt + 1,
                    getattr(callback, "__name__", repr(callback)),
                )
                await asyncio.sleep(0.25 * (attempt + 1))

    def close(self) -> None:
        if self._worker_task and not self._worker_task.done():
            self._worker_task.cancel()


@dataclass(frozen=True)
class IndexCategoryConfig:
    key: str
    name: str
    category_id: str
    parent_channel_ids: tuple[str, ...]
    worldmap: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "category_id": self.category_id,
            "parent_channel_ids": list(self.parent_channel_ids),
            "worldmap": self.worldmap,
        }


@dataclass(frozen=True)
class FullIndexTarget:
    category: IndexCategoryConfig
    target: Any


@dataclass(frozen=True)
class FullIndexSourceResult:
    category_key: str
    source_id: str
    source_name: str
    scanned: int
    indexed: int
    elapsed_seconds: float
    error: str | None = None


def parse_index_categories() -> list[IndexCategoryConfig]:
    raw = os.getenv(INDEX_CATEGORY_ENV)
    categories: list[IndexCategoryConfig] = []
    if raw:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                data = data.get("categories", [])
            for item in data:
                parent_ids = item.get("parent_channel_ids") or item.get("parent_ids") or []
                if isinstance(parent_ids, str):
                    parent_ids = [part.strip() for part in re.split(r"[,\s]+", parent_ids) if part.strip()]
                category_id = str(item.get("category_id") or "").strip()
                key = str(item.get("key") or category_id or len(categories)).strip()
                name = str(item.get("name") or key).strip()
                if category_id and parent_ids:
                    categories.append(
                        IndexCategoryConfig(
                            key=key,
                            name=name,
                            category_id=category_id,
                            parent_channel_ids=tuple(str(parent_id) for parent_id in parent_ids),
                            worldmap=bool(item.get("worldmap")),
                        )
                    )
        except Exception:
            logger.error("mogindex category config parse failed env=%s", INDEX_CATEGORY_ENV, exc_info=True)
    if not categories:
        categories.append(
            IndexCategoryConfig(
                key="default",
                name="기본 카테고리",
                category_id=str(CATEGORY_ID),
                parent_channel_ids=tuple(str(parent_id) for parent_id in DEFAULT_PARENT_CHANNEL_IDS),
                worldmap=False,
            )
        )
    return categories


def parse_full_index_date(raw: str, default: date) -> date:
    value = (raw or "").strip()
    if not value:
        return default
    if re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", value):
        year, month, day = (int(part) for part in value.split("-"))
        return date(year, month, day)
    match = re.fullmatch(r"(\d{2})\.(\d{1,2})\.(\d{1,2})", value)
    if match:
        year, month, day = (int(part) for part in match.groups())
        return date(2000 + year, month, day)
    raise ValueError("날짜는 YY.M.D, YY.MM.DD 또는 YYYY-MM-DD 형식으로 입력해주세요.")



def parse_optional_search_date(raw: str | None) -> str | None:
    value = (raw or "").strip()
    if not value:
        return None
    return parse_full_index_date(value, now_kst().date()).isoformat()
class KeywordSearchModal(discord.ui.Modal):
    def __init__(self, cog: "MogIndexCommandsCog", state: SearchPanelState):
        super().__init__(
            title="키워드 검색",
            custom_id=f"mogsearch:{state.session_id}:keyword_modal",
        )
        self.cog = cog
        self.session_id = state.session_id
        self.all_terms = discord.ui.TextInput(
            label="반드시 포함",
            placeholder="모두 포함해야 하는 단어. 예: 개발실 살려줘",
            default=state.keyword_all or "",
            required=False,
            max_length=160,
        )
        self.any_terms = discord.ui.TextInput(
            label="하나라도 포함",
            placeholder="하나라도 있으면 되는 단어. 예: 커피, 미스트렌드",
            default=state.keyword_any or state.query or "",
            required=False,
            max_length=160,
        )
        self.not_terms = discord.ui.TextInput(
            label="빼고 싶은 키워드",
            placeholder="제외할 단어. 예: 공지 봇",
            default=state.keyword_not or "",
            required=False,
            max_length=160,
        )
        self.add_item(self.all_terms)
        self.add_item(self.any_terms)
        self.add_item(self.not_terms)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_keyword_modal(
            interaction,
            self.session_id,
            self.all_terms.value,
            self.any_terms.value,
            self.not_terms.value,
        )


class TopicSearchModal(discord.ui.Modal):
    def __init__(self, cog: "MogIndexCommandsCog", state: SearchPanelState):
        super().__init__(
            title="환장도서관",
            custom_id=f"mogsearch:{state.session_id}:topic_modal",
        )
        self.cog = cog
        self.session_id = state.session_id
        self.query = discord.ui.TextInput(
            label="환장도서관 검색어",
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


class ExcludeKeywordModal(discord.ui.Modal):
    def __init__(self, cog: "MogIndexCommandsCog", state: SearchPanelState):
        super().__init__(
            title="빼고 싶은 키워드",
            custom_id=f"mogsearch:{state.session_id}:exclude_modal",
        )
        self.cog = cog
        self.session_id = state.session_id
        self.not_terms = discord.ui.TextInput(
            label="제외할 키워드",
            placeholder="예: 공지, 시스템, 봇",
            default=state.keyword_not or "",
            required=False,
            max_length=160,
        )
        self.add_item(self.not_terms)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_exclude_modal(interaction, self.session_id, self.not_terms.value)


class ReturneeLimitModal(discord.ui.Modal):
    def __init__(self, cog: "MogIndexCommandsCog", state: SearchPanelState):
        super().__init__(
            title="복귀자 키워드",
            custom_id=f"mogsearch:{state.session_id}:returnee_modal",
        )
        self.cog = cog
        self.session_id = state.session_id
        self.limit = discord.ui.TextInput(
            label="표시할 키워드 수",
            placeholder="기본 5, 최대 100",
            default=str(state.returnee_limit or 5),
            required=False,
            max_length=3,
        )
        self.add_item(self.limit)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_returnee_modal(interaction, self.session_id, self.limit.value)


class FullIndexDateRangeModal(discord.ui.Modal):
    def __init__(self, cog: "MogIndexCommandsCog", category_key: str | None, title: str):
        super().__init__(title=title, custom_id="mogindex:full_index_date")
        self.cog = cog
        self.category_key = category_key
        self.start_bound = discord.ui.TextInput(
            label="언제부터",
            placeholder=FULL_INDEX_DEFAULT_OLDEST_DATE.strftime("%y.%m.%d"),
            required=False,
            max_length=10,
        )
        self.end_bound = discord.ui.TextInput(
            label="언제까지",
            placeholder=now_kst().date().strftime("%y.%m.%d"),
            required=False,
            max_length=10,
        )
        self.add_item(self.start_bound)
        self.add_item(self.end_bound)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_full_index_date_modal(
            interaction,
            self.category_key,
            self.start_bound.value,
            self.end_bound.value,
        )


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


class CategorySelectView(discord.ui.View):
    def __init__(self, cog: "MogIndexCommandsCog", state: SearchPanelState):
        super().__init__(timeout=30 * 60)
        self.cog = cog
        self.session_id = state.session_id
        options = [
            discord.SelectOption(label=category.name[:100], value=category.key)
            for category in cog.index_categories[:24]
        ]
        options.append(discord.SelectOption(label="카테고리 이름 모름", value="__unknown__"))
        select = discord.ui.Select(
            placeholder="카테고리를 선택하세요",
            min_values=1,
            max_values=1,
            options=options,
            custom_id=f"mogsearch:{state.session_id}:category_select",
        )

        async def callback(interaction: discord.Interaction) -> None:
            await cog.handle_category_select(interaction, self.session_id, select.values[0])

        select.callback = callback
        self.add_item(select)


class ChannelSelectView(discord.ui.View):
    def __init__(
        self,
        cog: "MogIndexCommandsCog",
        state: SearchPanelState,
        category: IndexCategoryConfig,
        channel_options: list[discord.SelectOption],
    ):
        super().__init__(timeout=30 * 60)
        select = discord.ui.Select(
            placeholder=f"{category.name}의 채널/포럼을 선택하세요",
            min_values=1,
            max_values=1,
            options=[discord.SelectOption(label="해당 카테고리 전체", value="__category_all__")] + channel_options[:24],
            custom_id=f"mogsearch:{state.session_id}:channel_select",
        )

        async def callback(interaction: discord.Interaction) -> None:
            await cog.handle_channel_select(interaction, state.session_id, category.key, select.values[0])

        select.callback = callback
        self.add_item(select)


class ThreadSelectView(discord.ui.View):
    def __init__(
        self,
        cog: "MogIndexCommandsCog",
        state: SearchPanelState,
        category_key: str,
        channel_id: str,
        channel_name: str,
        thread_options: list[discord.SelectOption],
    ):
        super().__init__(timeout=30 * 60)
        select = discord.ui.Select(
            placeholder=f"{channel_name}의 스레드/게시글을 선택하세요",
            min_values=1,
            max_values=1,
            options=[discord.SelectOption(label="해당 채널 전체", value="__channel_all__")] + thread_options[:24],
            custom_id=f"mogsearch:{state.session_id}:thread_select",
        )

        async def callback(interaction: discord.Interaction) -> None:
            await cog.handle_thread_select(interaction, state.session_id, category_key, channel_id, select.values[0])

        select.callback = callback
        self.add_item(select)


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
        self._add_button("키워드 검색", "keyword", discord.ButtonStyle.primary, row=0, active=state.mode == "keyword")
        self._add_button("카테고리·채널·스레드", "scope_location", discord.ButtonStyle.primary, row=0, active=state.source_scope in ("selected_categories", "selected_sources"))
        self._add_button("환장도서관", "topic", discord.ButtonStyle.secondary, row=0, active=state.mode == "topic")

        self._add_button("오늘", "date_today", discord.ButtonStyle.secondary, row=1, active=state.date_preset == "today")
        self._add_button("7일", "date_7d", discord.ButtonStyle.secondary, row=1, active=state.date_preset == "7d")
        self._add_button("30일", "date_30d", discord.ButtonStyle.secondary, row=1, active=state.date_preset == "30d")
        self._add_button("전체", "date_all", discord.ButtonStyle.secondary, row=1, active=state.date_preset == "all")
        self._add_button("기간 입력", "date_custom", discord.ButtonStyle.secondary, row=1, active=state.date_preset == "custom")

        self._add_button("전체 카테고리", "scope_all", discord.ButtonStyle.secondary, row=2, active=state.source_scope == "all_indexed")
        self._add_button("현재 카테고리", "scope_current_category", discord.ButtonStyle.secondary, row=2, active=state.source_scope == "current_category", disabled=not state.origin_category_id)
        self._add_button("현재 채널", "scope_current", discord.ButtonStyle.secondary, row=2, active=state.source_scope == "current_channel")
        self._add_button("빼고 싶은 키워드", "exclude", discord.ButtonStyle.secondary, row=2, active=bool(state.keyword_not))
        self._add_button("복귀자 키워드", "returnee", discord.ButtonStyle.secondary, row=2, active=state.mode == "returnee")

        self._add_button("결과 안 검색", "within_results", discord.ButtonStyle.secondary, row=3, disabled=not state.last_result_ids)
        self._add_button("이전", "prev", discord.ButtonStyle.secondary, row=3, disabled=not (page and page.has_previous))
        self._add_button("다음", "next", discord.ButtonStyle.secondary, row=3, disabled=not (page and page.has_next))
        self._add_button(f"정렬:{self._sort_label(state.sort)}", "sort_toggle", discord.ButtonStyle.secondary, row=3)
        self._add_button("명령어 내보내기", "export_command", discord.ButtonStyle.secondary, row=3, disabled=state.mode == "hub")

        self._add_button("필터 리셋", "reset_filters", discord.ButtonStyle.secondary, row=4)
        self._add_button("공개 공유", "share", discord.ButtonStyle.success, row=4, disabled=state.mode == "hub")
        self._add_button("닫기", "close", discord.ButtonStyle.danger, row=4)

    def _sort_label(self, sort: str) -> str:
        return {"relevance": "관련", "newest": "최신", "oldest": "오래된"}.get(sort, sort)

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
        self.daily_index_task: asyncio.Task | None = None
        self.db_write_queue = DbWriteQueue("mogindex-db")
        self.index_categories = parse_index_categories()
        self.category_by_key = {category.key: category for category in self.index_categories}
        logger.info(
            "mogindex categories loaded env=%s categories=%s",
            INDEX_CATEGORY_ENV,
            [(category.key, category.name, category.category_id, len(category.parent_channel_ids)) for category in self.index_categories],
        )

    async def cog_load(self) -> None:
        await asyncio.to_thread(self.service.initialize)
        logger.info(
            "mogindex DB initialized index=%s state=%s",
            self.service.index_db_path,
            self.service.state_db_path,
        )
        if DAILY_INDEX_ENABLED:
            self.daily_index_task = asyncio.create_task(self.daily_index_scheduler())
            logger.info(
                "mogindex daily index scheduler enabled time=%02d:%02d categories=%s",
                DAILY_INDEX_HOUR,
                DAILY_INDEX_MINUTE,
                DAILY_INDEX_CATEGORY_NAMES,
            )
        else:
            logger.info("mogindex daily index scheduler disabled")

    def cog_unload(self) -> None:
        if self.daily_index_task and not self.daily_index_task.done():
            self.daily_index_task.cancel()
        if self.full_index_task and not self.full_index_task.done():
            self.full_index_task.cancel()
        self.db_write_queue.close()

    def get_category(self, key: str | None) -> IndexCategoryConfig | None:
        if not key or key == "all":
            return None
        return self.category_by_key.get(key)

    def selected_categories(self, key: str | None) -> list[IndexCategoryConfig]:
        category = self.get_category(key)
        if category:
            return [category]
        return list(self.index_categories)

    def category_match_key(self, value: str) -> str:
        return re.sub(r"[\s\-_]+", "", value).lower()

    def daily_index_categories(self) -> list[IndexCategoryConfig]:
        wanted = {self.category_match_key(name) for name in DAILY_INDEX_CATEGORY_NAMES}
        selected = []
        for category in self.index_categories:
            keys = {
                self.category_match_key(category.key),
                self.category_match_key(category.name),
                self.category_match_key(category.category_id),
            }
            if keys & wanted:
                selected.append(category)
        return selected

    def scheduler_guild_id(self) -> str | None:
        if DAILY_INDEX_GUILD_ID:
            return DAILY_INDEX_GUILD_ID
        if len(self.bot.guilds) == 1:
            return str(self.bot.guilds[0].id)
        return None

    def target_config_json(self, categories: list[IndexCategoryConfig]) -> str:
        return json.dumps([category.to_dict() for category in categories], ensure_ascii=False, separators=(",", ":"))

    def categories_from_target_json(self, raw: str | None) -> list[IndexCategoryConfig]:
        if not raw:
            return list(self.index_categories)
        try:
            data = json.loads(raw)
            categories = []
            for item in data:
                categories.append(
                    IndexCategoryConfig(
                        key=str(item["key"]),
                        name=str(item["name"]),
                        category_id=str(item["category_id"]),
                        parent_channel_ids=tuple(str(parent_id) for parent_id in item.get("parent_channel_ids", [])),
                        worldmap=bool(item.get("worldmap")),
                    )
                )
            return categories or list(self.index_categories)
        except Exception:
            logger.warning("mogindex target config parse failed; using current categories", exc_info=True)
            return list(self.index_categories)

    @app_commands.command(name="검색", description="색인된 커뮤 로그를 검색합니다.")
    async def search_panel(self, interaction: discord.Interaction) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("서버 안에서만 사용할 수 있습니다.", ephemeral=True)
            return
        state = self.service.create_session(interaction, persist=False)
        state.worldmap_category_ids = [category.category_id for category in self.index_categories if category.worldmap]
        await self.run_full_index_db_write(self.service.save_session, state)
        logger.info(
            "mogindex panel opened session=%s user=%s guild=%s channel=%s",
            state.session_id,
            state.owner_user_id,
            state.guild_id,
            state.origin_channel_id,
        )
        embed, view, _page = self.render_panel(state)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="고오급검색", description="옵션을 직접 입력해 색인 검색을 실행합니다.")
    @app_commands.describe(
        keyword="하나라도 포함할 검색어",
        all_terms="반드시 모두 포함할 검색어",
        not_terms="결과에서 제외할 검색어",
        location="채널/스레드 ID 또는 이름",
        category="카테고리 key, 이름 또는 ID",
        period="검색 기간 preset",
        start_date="시작일: YY.M.D, YY.MM.DD, YYYY-MM-DD",
        end_date="종료일: YY.M.D, YY.MM.DD, YYYY-MM-DD",
        sort="정렬 방식",
        result_set="검색 결과 안 검색 세션 ID",
        public="결과를 공개 메시지로 표시할지 여부",
    )
    @app_commands.rename(
        keyword="키워드",
        all_terms="반드시포함",
        not_terms="제외",
        location="위치",
        category="카테고리",
        period="기간",
        start_date="시작일",
        end_date="종료일",
        sort="정렬",
        result_set="결과세트",
        public="공개",
    )
    @app_commands.choices(
        period=[
            app_commands.Choice(name="오늘", value="today"),
            app_commands.Choice(name="7일", value="7d"),
            app_commands.Choice(name="30일", value="30d"),
            app_commands.Choice(name="전체", value="all"),
        ],
        sort=[
            app_commands.Choice(name="관련도", value="relevance"),
            app_commands.Choice(name="최신순", value="newest"),
            app_commands.Choice(name="오래된순", value="oldest"),
        ],
    )
    async def advanced_search(
        self,
        interaction: discord.Interaction,
        keyword: str | None = None,
        all_terms: str | None = None,
        not_terms: str | None = None,
        location: str | None = None,
        category: str | None = None,
        period: app_commands.Choice[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        sort: app_commands.Choice[str] | None = None,
        result_set: str | None = None,
        public: bool = False,
    ) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("서버 안에서만 사용할 수 있습니다.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=not public, thinking=True)
        state: SearchPanelState | None = None
        try:
            state = self.service.create_session(interaction, persist=False)
            state.worldmap_category_ids = [category_config.category_id for category_config in self.index_categories if category_config.worldmap]

            has_keyword = bool((keyword or "").strip() or (all_terms or "").strip() or (not_terms or "").strip())
            if has_keyword:
                self.service.set_keywords(state, all_terms=all_terms, any_terms=keyword, not_terms=not_terms)
            else:
                state.mode = "recent"

            base_result_ids = self.resolve_result_set_ids(result_set, str(interaction.user.id))
            if base_result_ids:
                state.panel_kind = "detail"
                self.service.set_scope(state, "result_set", base_result_ids=base_result_ids)

            category_ids = self.resolve_search_category_ids(category)
            source_ids = self.resolve_search_source_ids(location)
            if source_ids:
                state.source_ids = source_ids
                state.source_selector = "advanced"
                if state.source_scope != "result_set":
                    self.service.set_scope(state, "selected_sources", source_ids)
            elif category_ids:
                state.category_ids = category_ids
                if state.source_scope != "result_set":
                    self.service.set_scope(state, "selected_categories", category_ids=category_ids)

            parsed_start = parse_optional_search_date(start_date)
            parsed_end = parse_optional_search_date(end_date)
            if parsed_start or parsed_end:
                if parsed_start and parsed_end and parsed_start > parsed_end:
                    raise ValueError("시작일은 종료일보다 늦을 수 없습니다.")
                state.date_preset = "custom"
                state.start_date = parsed_start
                state.end_date = parsed_end
                state.page = 0
            elif period:
                self.service.set_date_preset(state, period.value)  # type: ignore[arg-type]

            if sort:
                state.sort = sort.value  # type: ignore[assignment]
            state.page = 0
            await self.run_full_index_db_write(self.service.save_session, state)

            embed, view, page = self.render_panel(state, public=public)
            await interaction.followup.send(embed=embed, view=None if public else view, ephemeral=not public)
            logger.info(
                "mogindex advanced search user=%s session=%s mode=%s category_ids=%s source_ids=%s period=%s sort=%s public=%s total=%s",
                interaction.user.id,
                state.session_id,
                state.mode,
                state.category_ids,
                state.source_ids,
                state.date_preset,
                state.sort,
                public,
                getattr(page, "total", None),
            )
        except ValueError as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
        except Exception as exc:
            logger.error("고오급검색 처리 실패: %s", exc, exc_info=True)
            await self.report_exception("고오급검색 처리 실패", exc, interaction=interaction, state=state)
            await interaction.followup.send("고오급검색을 처리하는 중 오류가 발생했습니다.", ephemeral=True)

    async def handle_action(
        self,
        interaction: discord.Interaction,
        session_id: str,
        action: str,
        extra: dict[str, Any],
    ) -> None:
        modal_actions = {"keyword", "topic", "date_custom", "exclude", "returnee"}
        state: SearchPanelState | None = None

        if action not in modal_actions:
            if not await self.defer_panel_update(interaction):
                logger.warning(
                    "mogindex interaction expired before ack user=%s session=%s action=%s",
                    getattr(interaction.user, "id", None),
                    session_id,
                    action,
                )
                return

        try:
            state = self.service.load_session(session_id, str(interaction.user.id))
            logger.info(
                "mogindex action user=%s session=%s action=%s mode=%s scope=%s categories=%s sources=%s preset=%s page=%s extra=%s",
                interaction.user.id,
                session_id,
                action,
                state.mode,
                state.source_scope,
                state.category_ids,
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
        if action == "exclude":
            await interaction.response.send_modal(ExcludeKeywordModal(self, state))
            return
        if action == "returnee":
            await interaction.response.send_modal(ReturneeLimitModal(self, state))
            return

        try:
            if action == "close":
                await self.run_full_index_db_write(self.service.close_session, state)
                await self.safe_edit_original_response(interaction, content="검색 패널을 닫았습니다.", embed=None, view=None)
                return
            if action == "scope_location":
                embed = discord.Embed(title="카테고리·채널·스레드 검색", color=discord.Color.dark_teal())
                embed.description = "범위를 고르세요. 포럼 게시글은 포럼 안의 스레드로 처리됩니다."
                await self.safe_edit_original_response(interaction, embed=embed, view=CategorySelectView(self, state))
                return
            if action == "within_results":
                if not state.last_result_ids:
                    await self.send_user_message(interaction, "검색 결과가 있어야 상세 검색 패널을 열 수 있습니다.")
                    return
                detail_state = self.service.create_detail_session(state, persist=False)
                await self.run_full_index_db_write(self.service.save_session, detail_state)
                embed, view, _page = self.render_panel(detail_state)
                embed.title = embed.title.replace("검색 패널", "상세 검색 패널")
                await interaction.followup.send(embed=embed, view=view, ephemeral=True)
                return
            if action == "share":
                await self.share_current_page(interaction, state)
                return
            if action == "export_command":
                await interaction.followup.send(self.export_search_command(state), ephemeral=True)
                return

            self.apply_action(state, action, extra)
            embed, view, _page = self.render_panel(state)
            await self.run_full_index_db_write(self.service.save_session, state)
            logger.info(
                "mogindex action applied session=%s action=%s mode=%s scope=%s categories=%s sources=%s preset=%s page=%s",
                state.session_id,
                action,
                state.mode,
                state.source_scope,
                state.category_ids,
                state.source_ids,
                state.date_preset,
                state.page,
            )
            await self.safe_edit_original_response(interaction, embed=embed, view=view)
        except discord.NotFound:
            logger.warning("mogindex interaction original response unavailable session=%s action=%s", session_id, action)
        except ValueError as exc:
            await self.send_session_error(interaction, exc)
        except Exception as exc:
            logger.error("검색 패널 action 처리 실패: %s", exc, exc_info=True)
            await self.report_exception("검색 패널 action 처리 실패", exc, interaction=interaction, state=state, details={"action": action})
            await self.send_user_message(interaction, "검색 패널을 갱신하는 중 오류가 발생했습니다.")

    def apply_action(self, state: SearchPanelState, action: str, extra: dict[str, Any]) -> None:
        if action == "participants":
            state.mode = "participants"
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
        elif action == "scope_current_category":
            if not state.origin_category_id:
                raise ValueError("현재 채널의 카테고리를 확인하지 못했습니다.")
            self.service.set_scope(state, "current_category")
            if state.mode == "hub":
                state.mode = "recent"
        elif action == "scope_current":
            self.service.set_scope(state, "current_channel")
            if state.mode == "hub":
                state.mode = "recent"
        elif action == "sort_toggle":
            state.sort = {"relevance": "newest", "newest": "oldest", "oldest": "relevance"}.get(state.sort, "relevance")  # type: ignore[assignment]
            state.page = 0
        elif action == "prev":
            state.page = max(0, state.page - 1)
        elif action == "next":
            state.page += 1
        else:
            raise ValueError("알 수 없는 검색 패널 동작입니다.")

    async def handle_keyword_modal(
        self,
        interaction: discord.Interaction,
        session_id: str,
        all_terms: str,
        any_terms: str,
        not_terms: str,
    ) -> None:
        try:
            if not await self.defer_panel_update(interaction):
                return
            state = self.service.load_session(session_id, str(interaction.user.id))
            self.service.set_keywords(state, all_terms=all_terms, any_terms=any_terms, not_terms=not_terms)
            logger.info(
                "mogindex keyword modal user=%s session=%s all=%r any=%r not=%r",
                interaction.user.id,
                session_id,
                state.keyword_all,
                state.keyword_any,
                state.keyword_not,
            )
            embed, view, _page = self.render_panel(state)
            await self.run_full_index_db_write(self.service.save_session, state)
            await self.safe_edit_original_response(interaction, embed=embed, view=view)
        except SearchSessionError as exc:
            await self.send_session_error(interaction, exc)
        except Exception as exc:
            logger.error("검색 keyword modal 처리 실패: %s", exc, exc_info=True)
            await self.report_exception("검색 keyword modal 처리 실패", exc, interaction=interaction)
            await self.send_user_message(interaction, "검색어를 처리하는 중 오류가 발생했습니다.")

    async def handle_exclude_modal(self, interaction: discord.Interaction, session_id: str, not_terms: str) -> None:
        try:
            if not await self.defer_panel_update(interaction):
                return
            state = self.service.load_session(session_id, str(interaction.user.id))
            self.service.set_keywords(
                state,
                all_terms=state.keyword_all,
                any_terms=state.keyword_any or state.query,
                not_terms=not_terms,
            )
            embed, view, _page = self.render_panel(state)
            await self.run_full_index_db_write(self.service.save_session, state)
            await self.safe_edit_original_response(interaction, embed=embed, view=view)
        except SearchSessionError as exc:
            await self.send_session_error(interaction, exc)
        except Exception as exc:
            logger.error("검색 exclude modal 처리 실패: %s", exc, exc_info=True)
            await self.report_exception("검색 exclude modal 처리 실패", exc, interaction=interaction)
            await self.send_user_message(interaction, "제외 키워드를 처리하는 중 오류가 발생했습니다.")

    async def handle_returnee_modal(self, interaction: discord.Interaction, session_id: str, limit: str) -> None:
        try:
            if not await self.defer_panel_update(interaction):
                return
            state = self.service.load_session(session_id, str(interaction.user.id))
            clean_limit = (limit or "").strip()
            state.returnee_limit = min(max(int(clean_limit or "5"), 1), 100)
            state.mode = "returnee"
            state.page = 0
            embed, view, _page = self.render_panel(state)
            await self.run_full_index_db_write(self.service.save_session, state)
            await self.safe_edit_original_response(interaction, embed=embed, view=view)
        except ValueError:
            await self.send_user_message(interaction, "표시할 키워드 수는 1부터 100 사이 숫자로 입력해주세요.")
        except SearchSessionError as exc:
            await self.send_session_error(interaction, exc)
        except Exception as exc:
            logger.error("검색 returnee modal 처리 실패: %s", exc, exc_info=True)
            await self.report_exception("검색 returnee modal 처리 실패", exc, interaction=interaction)
            await self.send_user_message(interaction, "복귀자 키워드를 처리하는 중 오류가 발생했습니다.")

    async def handle_modal_query(
        self,
        interaction: discord.Interaction,
        session_id: str,
        mode: str,
        query: str,
    ) -> None:
        try:
            if not await self.defer_panel_update(interaction):
                return
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
            await self.run_full_index_db_write(self.service.save_session, state)
            await self.safe_edit_original_response(interaction, embed=embed, view=view)
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
            if not await self.defer_panel_update(interaction):
                return
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
            await self.run_full_index_db_write(self.service.save_session, state)
            await self.safe_edit_original_response(interaction, embed=embed, view=view)
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
            if not await self.defer_panel_update(interaction):
                return
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
            await self.run_full_index_db_write(self.service.save_session, state)
            await self.safe_edit_original_response(interaction, embed=embed, view=view)
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
            if not await self.defer_panel_update(interaction):
                return
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
            await self.run_full_index_db_write(self.service.save_session, state)
            await self.safe_edit_original_response(interaction, embed=embed, view=view)
        except (SearchSessionError, ValueError) as exc:
            await self.send_session_error(interaction, exc)
        except Exception as exc:
            logger.error("검색 기간 modal 처리 실패: %s", exc, exc_info=True)
            await self.report_exception("검색 기간 modal 처리 실패", exc, interaction=interaction, details={"start_date": start_date, "end_date": end_date})
            await self.send_user_message(interaction, "기간을 처리하는 중 오류가 발생했습니다.")

    async def handle_category_select(self, interaction: discord.Interaction, session_id: str, category_key: str) -> None:
        try:
            state = self.service.load_session(session_id, str(interaction.user.id))
            if category_key == "__unknown__":
                await interaction.response.send_modal(SourceNameModal(self, state))
                return
            if not await self.defer_panel_update(interaction):
                return
            category = self.category_by_key.get(category_key)
            if not category:
                raise ValueError("카테고리 설정을 찾지 못했습니다.")
            options = await self.channel_options_for_category(category)
            embed = discord.Embed(title="카테고리·채널·스레드 검색", color=discord.Color.dark_teal())
            embed.description = f"선택한 카테고리: {category.name}"
            await self.safe_edit_original_response(interaction, embed=embed, view=ChannelSelectView(self, state, category, options))
        except (SearchSessionError, ValueError) as exc:
            await self.send_session_error(interaction, exc)
        except Exception as exc:
            logger.error("검색 category select 처리 실패: %s", exc, exc_info=True)
            await self.report_exception("검색 category select 처리 실패", exc, interaction=interaction, details={"category_key": category_key})
            await self.send_user_message(interaction, "카테고리를 처리하는 중 오류가 발생했습니다.")

    async def handle_channel_select(self, interaction: discord.Interaction, session_id: str, category_key: str, value: str) -> None:
        try:
            if not await self.defer_panel_update(interaction):
                return
            state = self.service.load_session(session_id, str(interaction.user.id))
            category = self.category_by_key.get(category_key)
            if not category:
                raise ValueError("카테고리 설정을 찾지 못했습니다.")
            if value == "__category_all__":
                self.service.set_scope(state, "selected_categories", category_ids=[category.category_id])
                if state.mode == "hub":
                    state.mode = "recent"
                embed, view, _page = self.render_panel(state)
                await self.run_full_index_db_write(self.service.save_session, state)
                await self.safe_edit_original_response(interaction, embed=embed, view=view)
                return
            channel = await self.fetch_channel_for_ui(value)
            channel_name = getattr(channel, "name", value)
            thread_options = await self.thread_options_for_channel(channel)
            embed = discord.Embed(title="카테고리·채널·스레드 검색", color=discord.Color.dark_teal())
            embed.description = f"선택한 채널/포럼: {channel_name}"
            await self.safe_edit_original_response(
                interaction,
                embed=embed,
                view=ThreadSelectView(self, state, category.key, value, str(channel_name), thread_options),
            )
        except (SearchSessionError, ValueError) as exc:
            await self.send_session_error(interaction, exc)
        except Exception as exc:
            logger.error("검색 channel select 처리 실패: %s", exc, exc_info=True)
            await self.report_exception("검색 channel select 처리 실패", exc, interaction=interaction, details={"category_key": category_key, "value": value})
            await self.send_user_message(interaction, "채널을 처리하는 중 오류가 발생했습니다.")

    async def handle_thread_select(self, interaction: discord.Interaction, session_id: str, category_key: str, channel_id: str, value: str) -> None:
        try:
            if not await self.defer_panel_update(interaction):
                return
            state = self.service.load_session(session_id, str(interaction.user.id))
            if value == "__channel_all__":
                self.service.set_scope(state, "selected_sources", [channel_id])
                state.source_selector = "location"
            else:
                self.service.set_scope(state, "selected_sources", [value])
                state.source_selector = "location"
            if state.mode == "hub":
                state.mode = "recent"
            embed, view, _page = self.render_panel(state)
            await self.run_full_index_db_write(self.service.save_session, state)
            await self.safe_edit_original_response(interaction, embed=embed, view=view)
        except SearchSessionError as exc:
            await self.send_session_error(interaction, exc)
        except Exception as exc:
            logger.error("검색 thread select 처리 실패: %s", exc, exc_info=True)
            await self.report_exception("검색 thread select 처리 실패", exc, interaction=interaction, details={"channel_id": channel_id, "value": value})
            await self.send_user_message(interaction, "스레드를 처리하는 중 오류가 발생했습니다.")

    async def fetch_channel_for_ui(self, channel_id: str) -> Any:
        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            channel = await self.bot.fetch_channel(int(channel_id))
        return channel

    async def channel_options_for_category(self, category: IndexCategoryConfig) -> list[discord.SelectOption]:
        options: list[discord.SelectOption] = []
        for channel_id in category.parent_channel_ids:
            try:
                channel = await self.fetch_channel_for_ui(channel_id)
                label = str(getattr(channel, "name", channel_id))[:100]
            except Exception:
                label = str(channel_id)
            options.append(discord.SelectOption(label=label, value=str(channel_id)))
        return options

    async def thread_options_for_channel(self, channel: Any) -> list[discord.SelectOption]:
        options: list[discord.SelectOption] = []
        for thread in list(getattr(channel, "threads", []) or [])[:24]:
            options.append(discord.SelectOption(label=str(getattr(thread, "name", thread.id))[:100], value=str(thread.id)))
        return options

    def export_period_label(self, preset: str) -> str:
        return {
            "today": "오늘",
            "7d": "7일",
            "30d": "30일",
            "all": "전체",
        }.get(preset, preset)

    def export_sort_label(self, sort: str) -> str:
        return {
            "relevance": "관련도",
            "newest": "최신순",
            "oldest": "오래된순",
        }.get(sort, sort)

    def export_search_command(self, state: SearchPanelState) -> str:
        parts = ["/고오급검색"]
        if state.source_scope == "result_set" and state.base_result_ids:
            parts.append(f"결과세트:{state.session_id}")
        if state.keyword_all:
            parts.append(f"반드시포함:{state.keyword_all}")
        if state.keyword_any or state.query:
            parts.append(f"키워드:{state.keyword_any or state.query}")
        if state.keyword_not:
            parts.append(f"제외:{state.keyword_not}")
        if state.source_ids:
            parts.append(f"위치:{','.join(state.source_ids)}")
        if state.category_ids:
            parts.append(f"카테고리:{','.join(state.category_ids)}")
        if state.date_preset == "custom":
            parts.append(f"시작일:{state.start_date or ''}")
            parts.append(f"종료일:{state.end_date or ''}")
        elif state.date_preset:
            parts.append(f"기간:{self.export_period_label(state.date_preset)}")
        parts.append(f"정렬:{self.export_sort_label(state.sort)}")
        newline = chr(10)
        fence = chr(96) * 3
        return "검색 명령어 내보내기" + newline + fence + newline + " ".join(parts) + newline + fence

    def resolve_result_set_ids(self, raw: str | None, user_id: str) -> list[int]:
        value = (raw or "").strip()
        if not value:
            return []
        try:
            source_state = self.service.load_session(value, user_id)
        except SearchSessionError as exc:
            raise ValueError("결과세트가 만료되었거나 접근할 수 없습니다. `/검색`으로 다시 결과 안 검색을 열어주세요.") from exc
        result_ids = source_state.base_result_ids if source_state.source_scope == "result_set" else source_state.last_result_ids
        if not result_ids:
            raise ValueError("결과세트에 저장된 검색 결과가 없습니다. 결과가 있는 패널에서 명령어를 다시 내보내주세요.")
        return result_ids

    def resolve_search_category_ids(self, raw: str | None) -> list[str]:
        value = (raw or "").strip()
        if not value or value in ("전체", "전체 카테고리", "all"):
            return []
        resolved: list[str] = []
        missing: list[str] = []
        parts = [part.strip() for part in value.split(",") if part.strip()] or [value]
        for part in parts:
            matched = None
            if part in self.category_by_key:
                matched = self.category_by_key[part]
            else:
                for category in self.index_categories:
                    if category.name == part or category.category_id == part:
                        matched = category
                        break
            if matched:
                resolved.append(matched.category_id)
            elif part.isdigit():
                resolved.append(part)
            else:
                missing.append(part)
        if missing:
            raise ValueError("카테고리를 찾지 못했습니다: " + ", ".join(missing))
        return list(dict.fromkeys(resolved))

    def resolve_search_source_ids(self, raw: str | None) -> list[str]:
        value = (raw or "").strip()
        if not value:
            return []
        id_parts = [part.strip() for part in re.split(r"[,\s]+", value) if part.strip()]
        if id_parts and all(part.isdigit() for part in id_parts):
            return list(dict.fromkeys(id_parts))

        resolved: list[str] = []
        missing: list[str] = []
        parts = [part.strip() for part in value.split(",") if part.strip()] or [value]
        for part in parts:
            if part.isdigit():
                resolved.append(part)
                continue
            matches = self.service.find_sources_by_name(part)
            if not matches:
                missing.append(part)
                continue
            resolved.extend(match.source_id for match in matches)
        if missing:
            raise ValueError("위치와 일치하는 색인 채널/스레드를 찾지 못했습니다: " + ", ".join(missing))
        return list(dict.fromkeys(resolved))

    async def share_current_page(self, interaction: discord.Interaction, state: SearchPanelState) -> None:
        if state.mode == "hub":
            await self.send_user_message(interaction, "공유할 검색 결과가 없습니다.")
            return
        if not interaction.response.is_done():
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

    async def defer_panel_update(self, interaction: discord.Interaction) -> bool:
        if interaction.response.is_done():
            return True
        try:
            await interaction.response.defer(thinking=False)
            return True
        except discord.NotFound:
            logger.warning("mogindex interaction expired before defer user=%s channel=%s", getattr(interaction.user, "id", None), interaction.channel_id)
            return False

    async def safe_edit_original_response(self, interaction: discord.Interaction, **kwargs: Any) -> bool:
        try:
            await interaction.edit_original_response(**kwargs)
            return True
        except discord.NotFound:
            logger.warning("mogindex original response unavailable user=%s channel=%s", getattr(interaction.user, "id", None), interaction.channel_id)
            return False

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
        if state.source_scope not in ("all_indexed", "result_set"):
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
        if state.mode == "returnee":
            return self.service.run_returnee_keywords(state)
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
            "current_category": "현재 카테고리",
            "selected_categories": "선택한 카테고리",
            "selected_sources": "선택한 채널/스레드",
            "result_set": "검색 결과 안",
        }.get(state.source_scope, state.source_scope)
        mode_text = {
            "hub": "기능 선택",
            "keyword": "키워드 검색",
            "topic": "환장도서관",
            "recap": "날짜 요약",
            "returnee": "복귀자 키워드",
            "participants": "참여자 보기",
            "recent": "채널 보기",
        }.get(state.mode, state.mode)
        if state.mode == "keyword":
            query_bits = []
            if state.keyword_all:
                query_bits.append(f"반드시 {state.keyword_all}")
            if state.keyword_any or state.query:
                query_bits.append(f"포함 {state.keyword_any or state.query}")
            if state.keyword_not:
                query_bits.append(f"제외 {state.keyword_not}")
            query_text = " / ".join(query_bits) if query_bits else "없음"
        elif state.mode == "topic":
            query_text = f"`{state.query}`" if state.query else "없음"
        else:
            query_text = "사용 안 함"
        extra = ""
        if state.category_ids:
            extra += f" / 카테고리 {','.join(state.category_ids)}"
        if state.source_ids:
            extra += f" / 위치 {','.join(state.source_ids)}"
        return f"조건: 모드 {mode_text} / 기간 {date_text} / 범위 {scope_text}{extra} / 검색어 {query_text}"

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
    @app_commands.describe(action="전체색인 작업", category="카테고리 key 또는 이름", period="기간 입력 모달을 열지 여부")
    @app_commands.rename(action="작업", category="카테고리", period="기간지정")
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
        category: str | None = None,
        period: bool = False,
    ) -> None:
        if not interaction.guild_id:
            await interaction.response.send_message("서버 안에서만 사용할 수 있습니다.", ephemeral=True)
            return

        action_value = action.value if action else "status"
        if action_value == "status":
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                status_text = await self.run_full_index_db_read(self.format_full_index_status)
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower():
                    raise
                status_text = "색인 DB가 현재 쓰기 작업 중이라 상태를 읽지 못했습니다. 잠시 후 다시 확인해주세요."
            await interaction.followup.send(status_text, ephemeral=True)
            return
        if self.full_index_task and not self.full_index_task.done():
            await interaction.response.send_message("전체색인이 이미 실행 중입니다. 상태확인을 사용해주세요.", ephemeral=True)
            return

        await self.run_full_index_db_write(self.mark_interrupted_full_index_runs)
        if action_value == "resume":
            await self.start_full_index_resume(interaction)
            return
        category_key = self.resolve_category_key(category)
        if period:
            title = "해당 카테고리 색인 기간 지정" if self.get_category(category_key) else "전체색인 기간 지정"
            await interaction.response.send_modal(FullIndexDateRangeModal(self, category_key, title))
            return
        await self.start_full_index_new(interaction, category_key=category_key)

    def resolve_category_key(self, raw: str | None) -> str | None:
        value = (raw or "").strip()
        if not value or value in ("전체", "전체 카테고리", "all"):
            return None
        if value in self.category_by_key:
            return value
        for category in self.index_categories:
            if category.name == value:
                return category.key
        return value

    async def handle_full_index_date_modal(self, interaction: discord.Interaction, category_key: str | None, oldest_raw: str, newest_raw: str) -> None:
        try:
            oldest_date = parse_full_index_date(oldest_raw, FULL_INDEX_DEFAULT_OLDEST_DATE)
            newest_date = parse_full_index_date(newest_raw, now_kst().date())
            if newest_date < oldest_date:
                raise ValueError("언제까지 날짜는 언제부터 날짜보다 빠를 수 없습니다.")
            await self.start_full_index_new(
                interaction,
                category_key=category_key,
                start_date=newest_date,
                end_date=oldest_date,
            )
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)

    async def start_full_index_new(
        self,
        interaction: discord.Interaction,
        *,
        category_key: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> None:
        start_date = start_date or now_kst().date()
        end_date = end_date or date(2024, 6, 5)
        target_categories = self.selected_categories(category_key)
        run_id = await self.run_full_index_db_write(
            self.create_full_index_run,
            guild_id=str(interaction.guild_id),
            requested_by=str(interaction.user.id),
            start_date=start_date,
            end_date=end_date,
            notify_channel_id=str(interaction.channel_id),
            notify_user_id=str(interaction.user.id),
            categories=target_categories,
        )
        self.full_index_task = asyncio.create_task(
            self.run_full_index_job(
                run_id=run_id,
                guild_id=str(interaction.guild_id),
                requested_by=str(interaction.user.id),
                start_date=start_date,
                end_date=end_date,
                target_categories=target_categories,
                notify_channel_id=str(interaction.channel_id),
                notify_user_id=str(interaction.user.id),
            )
        )
        logger.info(
            "mogindex full-index command started run=%s by=%s guild=%s start=%s end=%s categories=%s",
            run_id,
            interaction.user.id,
            interaction.guild_id,
            start_date,
            end_date,
            [category.key for category in target_categories],
        )
        await interaction.response.send_message(
            f"전체색인을 백그라운드에서 새로 시작했습니다. run={run_id[:8]} / {start_date}부터 {end_date}까지 진행합니다. 대상: {', '.join(category.name for category in target_categories)}. "
            f"하루 처리 시간이 {format_duration(FULL_INDEX_SLOW_DAY_SECONDS)} 이상이면 "
            f"{format_duration(FULL_INDEX_REST_SECONDS)} 쉬고, 더 빠르면 바로 다음 날짜로 넘어갑니다. "
            f"하루 안 source 병렬 수: {self.full_index_parallel_sources()}.",
            ephemeral=True,
        )

    async def start_full_index_resume(self, interaction: discord.Interaction) -> None:
        resume = await self.run_full_index_db_write(
            self.get_full_index_resume_plan,
            requested_by=str(interaction.user.id),
            notify_channel_id=str(interaction.channel_id),
            notify_user_id=str(interaction.user.id),
        )
        if resume is None:
            await interaction.response.send_message("이어갈 전체색인 기록이 없습니다.", ephemeral=True)
            return
        run_id, start_date, end_date, guild_id, requested_by, notify_channel_id, notify_user_id, target_categories = resume
        if start_date < end_date:
            await interaction.response.send_message("이미 완료된 색인입니다.", ephemeral=True)
            return
        self.full_index_task = asyncio.create_task(
            self.run_full_index_job(
                run_id=run_id,
                guild_id=guild_id,
                requested_by=requested_by,
                start_date=start_date,
                end_date=end_date,
                target_categories=target_categories,
                notify_channel_id=notify_channel_id,
                notify_user_id=notify_user_id,
            )
        )
        logger.info(
            "mogindex full-index command resumed run=%s by=%s guild=%s start=%s end=%s categories=%s",
            run_id,
            interaction.user.id,
            guild_id,
            start_date,
            end_date,
            [category.key for category in target_categories],
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
        categories: list[IndexCategoryConfig],
        run_kind: str = "manual",
        schedule_key: str | None = None,
    ) -> str:
        run_id = uuid.uuid4().hex
        now = now_kst().isoformat()
        with self.service.state_open() as conn:
            conn.execute(
                """
                INSERT INTO full_index_runs (
                    run_id, guild_id, requested_by, status, start_date, end_date,
                    current_date, last_completed_date, notify_channel_id, notify_user_id,
                    category_key, category_id, category_name, target_config_json,
                    run_kind, schedule_key, created_at, updated_at, finished_at
                )
                VALUES (?, ?, ?, 'running', ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
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
                    "all" if len(categories) != 1 else categories[0].key,
                    None if len(categories) != 1 else categories[0].category_id,
                    "전체 카테고리" if len(categories) != 1 else categories[0].name,
                    self.target_config_json(categories),
                    run_kind,
                    schedule_key,
                    now,
                    now,
                ),
            )
            conn.commit()
        return run_id

    def mark_interrupted_full_index_runs(self) -> None:
        now = now_kst().isoformat()
        with self.service.state_open() as conn:
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
                UPDATE full_index_source_days
                SET status = 'interrupted', finished_at = ?,
                    error_text = COALESCE(error_text, 'bot stopped before source completion')
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
        with self.service.state_open() as conn:
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
        run_kind: str = "manual",
        schedule_key: str | None = None,
    ) -> tuple[str, date, date, str, str, str | None, str | None, list[IndexCategoryConfig]] | None:
        now = now_kst().isoformat()
        where = ["status IN ('running', 'interrupted', 'failed')", "COALESCE(run_kind, 'manual') = ?"]
        params: list[Any] = [run_kind]
        if schedule_key is not None:
            where.append("schedule_key = ?")
            params.append(schedule_key)
        with self.service.state_open() as conn:
            row = conn.execute(
                f"""
                SELECT *
                FROM full_index_runs
                WHERE {' AND '.join(where)}
                ORDER BY created_at DESC
                LIMIT 1
                """,
                params,
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
                self.categories_from_target_json(row["target_config_json"] if "target_config_json" in row.keys() else None),
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
        with self.service.state_open() as conn:
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
            source_failures = conn.execute(
                """
                SELECT index_date, source_id, source_name, error_text
                FROM full_index_source_days
                WHERE run_id = ? AND status IN ('failed', 'interrupted')
                ORDER BY COALESCE(finished_at, started_at) DESC
                LIMIT 3
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
        failure_text = ""
        if source_failures:
            failure_lines = []
            for item in source_failures:
                source_name = item["source_name"] or item["source_id"]
                error = clip(item["error_text"] or "-", 120)
                failure_lines.append(f"- {item['index_date']} {source_name}: {error}")
            failure_text = "\n최근 source 실패:\n" + "\n".join(failure_lines)
        return (
            "전체색인 상태\n"
            f"run: {row['run_id'][:8]}\n"
            f"상태: {status_names.get(row['status'], row['status'])}\n"
            f"범위: {row['start_date']} .. {row['end_date']}\n"
            f"대상: {row['category_name'] if 'category_name' in row.keys() and row['category_name'] else '-'}\n"
            f"현재 날짜: {row['current_date'] or '-'}\n"
            f"마지막 완료: {row['last_completed_date'] or '-'}\n"
            f"다음 이어하기: {next_text}\n"
            f"일자 기록: {count_text}"
            f"{failure_text}"
        )

    def mark_full_index_day_started(self, run_id: str, index_date: date) -> None:
        now = now_kst().isoformat()
        with self.service.state_open() as conn:
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
        with self.service.state_open() as conn:
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
        with self.service.state_open() as conn:
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
        with self.service.state_open() as conn:
            conn.execute(
                """
                UPDATE full_index_runs
                SET status = 'completed', current_date = NULL, updated_at = ?, finished_at = ?
                WHERE run_id = ?
                """,
                (now, now, run_id),
            )
            conn.commit()

    def completed_full_index_source_rows(self, run_id: str, index_date: date) -> dict[str, Any]:
        with self.service.state_open() as conn:
            rows = conn.execute(
                """
                SELECT source_id, scanned, indexed, elapsed_seconds
                FROM full_index_source_days
                WHERE run_id = ? AND index_date = ? AND status = 'completed'
                """,
                (run_id, index_date.isoformat()),
            ).fetchall()
        return {row["source_id"]: row for row in rows}

    def mark_full_index_source_started(
        self,
        *,
        run_id: str,
        index_date: date,
        source_id: str,
        category_key: str,
        source_name: str,
    ) -> None:
        now = now_kst().isoformat()
        with self.service.state_open() as conn:
            conn.execute(
                """
                INSERT INTO full_index_source_days (
                    run_id, index_date, source_id, status, category_key, source_name,
                    scanned, indexed, elapsed_seconds, error_text, started_at, finished_at
                )
                VALUES (?, ?, ?, 'running', ?, ?, 0, 0, 0, NULL, ?, NULL)
                ON CONFLICT(run_id, index_date, source_id) DO UPDATE SET
                    status = 'running', category_key = excluded.category_key,
                    source_name = excluded.source_name, scanned = 0, indexed = 0,
                    elapsed_seconds = 0, error_text = NULL,
                    started_at = excluded.started_at, finished_at = NULL
                """,
                (run_id, index_date.isoformat(), source_id, category_key, source_name, now),
            )
            conn.commit()

    def mark_full_index_source_completed(
        self,
        *,
        run_id: str,
        index_date: date,
        source_id: str,
        category_key: str,
        source_name: str,
        scanned: int,
        indexed: int,
        elapsed_seconds: float,
    ) -> None:
        now = now_kst().isoformat()
        with self.service.state_open() as conn:
            conn.execute(
                """
                INSERT INTO full_index_source_days (
                    run_id, index_date, source_id, status, category_key, source_name,
                    scanned, indexed, elapsed_seconds, error_text, started_at, finished_at
                )
                VALUES (?, ?, ?, 'completed', ?, ?, ?, ?, ?, NULL, ?, ?)
                ON CONFLICT(run_id, index_date, source_id) DO UPDATE SET
                    status = 'completed', category_key = excluded.category_key,
                    source_name = excluded.source_name, scanned = excluded.scanned,
                    indexed = excluded.indexed, elapsed_seconds = excluded.elapsed_seconds,
                    error_text = NULL, finished_at = excluded.finished_at
                """,
                (
                    run_id,
                    index_date.isoformat(),
                    source_id,
                    category_key,
                    source_name,
                    scanned,
                    indexed,
                    elapsed_seconds,
                    now,
                    now,
                ),
            )
            conn.commit()

    def mark_full_index_source_failed(
        self,
        *,
        run_id: str,
        index_date: date,
        source_id: str,
        category_key: str,
        source_name: str,
        status: str,
        scanned: int,
        indexed: int,
        elapsed_seconds: float,
        error_text: str,
    ) -> None:
        now = now_kst().isoformat()
        with self.service.state_open() as conn:
            conn.execute(
                """
                INSERT INTO full_index_source_days (
                    run_id, index_date, source_id, status, category_key, source_name,
                    scanned, indexed, elapsed_seconds, error_text, started_at, finished_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, index_date, source_id) DO UPDATE SET
                    status = excluded.status, category_key = excluded.category_key,
                    source_name = excluded.source_name, scanned = excluded.scanned,
                    indexed = excluded.indexed, elapsed_seconds = excluded.elapsed_seconds,
                    error_text = excluded.error_text, finished_at = excluded.finished_at
                """,
                (
                    run_id,
                    index_date.isoformat(),
                    source_id,
                    status,
                    category_key,
                    source_name,
                    scanned,
                    indexed,
                    elapsed_seconds,
                    error_text[:1000],
                    now,
                    now,
                ),
            )
            conn.commit()

    def is_transient_discord_error(self, exc: Exception) -> bool:
        if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError)):
            return True
        if isinstance(exc, discord.HTTPException):
            status = getattr(exc, "status", None)
            code = getattr(exc, "code", None)
            return code == 0 or status in {500, 502, 503, 504}
        return False

    async def collect_source_with_retry(
        self,
        target: Any,
        *,
        guild_id: str,
        category_id: str,
        after: Any,
        before: Any,
    ) -> tuple[int, int]:
        attempts = max(1, FULL_INDEX_DISCORD_RETRY_ATTEMPTS)
        for attempt in range(1, attempts + 1):
            conn = None
            try:
                conn = await asyncio.to_thread(self.service.index_connect)
                return await collect_source(
                    conn,
                    target,
                    guild_id=guild_id,
                    category_id=category_id,
                    after=after,
                    before=before,
                    limit=None,
                    dry_run=False,
                    db_write_lock=self.db_write_queue,
                    fetch_timeout=FULL_INDEX_SOURCE_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                if not self.is_transient_discord_error(exc) or attempt >= attempts:
                    raise
                delay = FULL_INDEX_DISCORD_RETRY_SECONDS * attempt
                logger.warning(
                    "mogindex full-index transient Discord error; retrying source=%s name=%s attempt=%s/%s delay=%ss error=%s",
                    getattr(target, "source_id", None),
                    getattr(target, "name", None),
                    attempt,
                    attempts,
                    delay,
                    exc,
                    exc_info=True,
                )
                if conn is not None:
                    await asyncio.to_thread(conn.close)
                    conn = None
                await asyncio.sleep(delay)
            finally:
                if conn is not None:
                    await asyncio.to_thread(conn.close)
        raise RuntimeError("unreachable full-index retry state")

    def seconds_until_daily_index(self) -> float:
        now = now_kst()
        target = now.replace(hour=DAILY_INDEX_HOUR, minute=DAILY_INDEX_MINUTE, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return max(1.0, (target - now).total_seconds())

    async def daily_index_scheduler(self) -> None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            delay = self.seconds_until_daily_index()
            logger.info("mogindex daily index scheduler sleeping %.0fs", delay)
            try:
                await asyncio.sleep(delay)
                await self.start_daily_index_if_possible()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("mogindex daily index scheduler failed: %s", exc, exc_info=True)
                await self.report_exception("자동 일일 색인 스케줄러 실패", exc)

    async def start_daily_index_if_possible(self) -> None:
        if self.full_index_task and not self.full_index_task.done():
            logger.info("mogindex daily index skipped because full-index task is already running")
            return
        target_categories = self.daily_index_categories()
        if not target_categories:
            logger.warning("mogindex daily index skipped because no configured categories matched %s", DAILY_INDEX_CATEGORY_NAMES)
            return
        guild_id = self.scheduler_guild_id()
        if not guild_id:
            logger.warning("mogindex daily index skipped because guild id is unavailable")
            return

        await self.run_full_index_db_write(self.mark_interrupted_full_index_runs)
        resume = await self.run_full_index_db_write(
            self.get_full_index_resume_plan,
            requested_by="auto-daily",
            notify_channel_id=None,
            notify_user_id=None,
            run_kind=DAILY_INDEX_RUN_KIND,
            schedule_key=DAILY_INDEX_SCHEDULE_KEY,
        )
        if resume is not None:
            run_id, start_date, end_date, resume_guild_id, requested_by, notify_channel_id, notify_user_id, resume_categories = resume
            if start_date >= end_date:
                self.full_index_task = asyncio.create_task(
                    self.run_full_index_job(
                        run_id=run_id,
                        guild_id=resume_guild_id,
                        requested_by=requested_by,
                        start_date=start_date,
                        end_date=end_date,
                        target_categories=resume_categories,
                        notify_channel_id=notify_channel_id,
                        notify_user_id=notify_user_id,
                    )
                )
                logger.info(
                    "mogindex daily index resumed run=%s start=%s end=%s categories=%s",
                    run_id,
                    start_date,
                    end_date,
                    [category.key for category in resume_categories],
                )
                return

        index_date = now_kst().date()
        run_id = await self.run_full_index_db_write(
            self.create_full_index_run,
            guild_id=guild_id,
            requested_by="auto-daily",
            start_date=index_date,
            end_date=index_date,
            notify_channel_id=None,
            notify_user_id=None,
            categories=target_categories,
            run_kind=DAILY_INDEX_RUN_KIND,
            schedule_key=DAILY_INDEX_SCHEDULE_KEY,
        )
        self.full_index_task = asyncio.create_task(
            self.run_full_index_job(
                run_id=run_id,
                guild_id=guild_id,
                requested_by="auto-daily",
                start_date=index_date,
                end_date=index_date,
                target_categories=target_categories,
                notify_channel_id=None,
                notify_user_id=None,
            )
        )
        logger.info(
            "mogindex daily index started run=%s date=%s categories=%s",
            run_id,
            index_date,
            [category.key for category in target_categories],
        )

    def full_index_parallel_sources(self) -> int:
        return max(1, int(FULL_INDEX_PARALLEL_SOURCES or 1))

    async def run_full_index_db_read(self, callback: Any, *args: Any, **kwargs: Any) -> Any:
        for attempt in range(6):
            try:
                return await asyncio.to_thread(callback, *args, **kwargs)
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt >= 5:
                    raise
                await asyncio.sleep(0.2 * (attempt + 1))

    async def run_full_index_db_write(self, callback: Any, *args: Any, **kwargs: Any) -> Any:
        return await self.db_write_queue.run(callback, *args, **kwargs)

    async def build_full_index_target_inventory(
        self,
        *,
        start_date: date,
        end_date: date,
        target_categories: list[IndexCategoryConfig],
    ) -> list[FullIndexTarget]:
        run_after, run_before = discord_date_bounds(end_date, start_date, "Asia/Seoul")
        inventory: list[FullIndexTarget] = []
        for category in target_categories:
            targets = await gather_targets(
                self.bot,
                category.parent_channel_ids,
                category_id=category.category_id,
                include_threads=True,
                include_private_archived=False,
                thread_after=run_after,
                thread_before=run_before,
            )
            inventory.extend(FullIndexTarget(category=category, target=target) for target in targets)
            logger.info(
                "mogindex full-index inventory category=%s targets=%s range=%s..%s",
                category.key,
                len(targets),
                end_date,
                start_date,
            )
        logger.info(
            "mogindex full-index inventory total_targets=%s categories=%s",
            len(inventory),
            [category.key for category in target_categories],
        )
        return inventory

    def full_index_targets_for_day(
        self,
        inventory: list[FullIndexTarget],
        *,
        after: Any,
        before: Any,
    ) -> list[FullIndexTarget]:
        selected: list[FullIndexTarget] = []
        for item in inventory:
            target = item.target
            if getattr(target, "source_kind", None) == "thread":
                try:
                    if not thread_might_have_messages(target.channel, after, before):
                        continue
                except Exception:
                    logger.debug(
                        "mogindex thread metadata filter failed source=%s name=%s; keeping target",
                        getattr(target, "source_id", None),
                        getattr(target, "name", None),
                        exc_info=True,
                    )
            selected.append(item)
        return selected

    async def collect_full_index_target(
        self,
        item: FullIndexTarget,
        *,
        run_id: str,
        index_date: date,
        guild_id: str,
        after: Any,
        before: Any,
        semaphore: asyncio.Semaphore,
    ) -> FullIndexSourceResult:
        async with semaphore:
            category = item.category
            target = item.target
            source_id = str(getattr(target, "source_id", ""))
            source_name = str(getattr(target, "name", source_id))
            target_started = time.perf_counter()
            try:
                await self.run_full_index_db_write(
                    self.mark_full_index_source_started,
                    run_id=run_id,
                    index_date=index_date,
                    source_id=source_id,
                    category_key=category.key,
                    source_name=source_name,
                )
                collect_coro = self.collect_source_with_retry(
                    target,
                    guild_id=guild_id,
                    category_id=category.category_id,
                    after=after,
                    before=before,
                )
                scanned, indexed = await collect_coro
                elapsed = time.perf_counter() - target_started
                await self.run_full_index_db_write(
                    self.mark_full_index_source_completed,
                    run_id=run_id,
                    index_date=index_date,
                    source_id=source_id,
                    category_key=category.key,
                    source_name=source_name,
                    scanned=scanned,
                    indexed=indexed,
                    elapsed_seconds=elapsed,
                )
                logger.info(
                    "mogindex full-index target run=%s day=%s category=%s source=%s name=%s scanned=%s indexed=%s elapsed=%.2fs",
                    run_id,
                    index_date,
                    category.key,
                    source_id,
                    source_name,
                    scanned,
                    indexed,
                    elapsed,
                )
                await asyncio.sleep(0)
                return FullIndexSourceResult(category.key, source_id, source_name, scanned, indexed, elapsed)
            except asyncio.CancelledError:
                elapsed = time.perf_counter() - target_started
                await self.run_full_index_db_write(
                    self.mark_full_index_source_failed,
                    run_id=run_id,
                    index_date=index_date,
                    source_id=source_id,
                    category_key=category.key,
                    source_name=source_name,
                    status="interrupted",
                    scanned=0,
                    indexed=0,
                    elapsed_seconds=elapsed,
                    error_text="task cancelled",
                )
                raise
            except Exception as exc:
                elapsed = time.perf_counter() - target_started
                error_text = f"{type(exc).__name__}: {exc}"
                await self.run_full_index_db_write(
                    self.mark_full_index_source_failed,
                    run_id=run_id,
                    index_date=index_date,
                    source_id=source_id,
                    category_key=category.key,
                    source_name=source_name,
                    status="failed",
                    scanned=0,
                    indexed=0,
                    elapsed_seconds=elapsed,
                    error_text=error_text,
                )
                logger.error(
                    "mogindex full-index source failed run=%s day=%s category=%s source=%s name=%s elapsed=%.2fs error=%s",
                    run_id,
                    index_date,
                    category.key,
                    source_id,
                    source_name,
                    elapsed,
                    exc,
                    exc_info=True,
                )
                return FullIndexSourceResult(category.key, source_id, source_name, 0, 0, elapsed, error_text)

    async def run_full_index_job(
        self,
        *,
        run_id: str,
        guild_id: str,
        requested_by: str,
        start_date: date,
        end_date: date,
        target_categories: list[IndexCategoryConfig],
        notify_channel_id: str | None = None,
        notify_user_id: str | None = None,
    ) -> None:
        current = start_date
        target_inventory: list[FullIndexTarget] | None = None
        while current >= end_date:
            day_started = time.perf_counter()
            day_elapsed = 0.0
            total_scanned = 0
            total_indexed = 0
            target_elapsed_total = 0.0
            targets_count = 0
            zero_targets = 0
            nonzero_targets = 0
            await self.run_full_index_db_write(self.mark_full_index_day_started, run_id, current)
            try:
                after, before = discord_date_bounds(current, current, "Asia/Seoul")
                if target_inventory is None:
                    target_inventory = await self.build_full_index_target_inventory(
                        start_date=start_date,
                        end_date=end_date,
                        target_categories=target_categories,
                    )
                day_targets = self.full_index_targets_for_day(target_inventory, after=after, before=before)
                targets_count = len(day_targets)
                completed_rows = await self.run_full_index_db_read(self.completed_full_index_source_rows, run_id, current)
                pending_targets: list[FullIndexTarget] = []
                for item in day_targets:
                    source_id = str(getattr(item.target, "source_id", ""))
                    completed = completed_rows.get(source_id)
                    if completed:
                        scanned = int(completed["scanned"])
                        indexed = int(completed["indexed"])
                        elapsed = float(completed["elapsed_seconds"] or 0)
                        total_scanned += scanned
                        total_indexed += indexed
                        target_elapsed_total += elapsed
                        if scanned == 0 and indexed == 0:
                            zero_targets += 1
                        else:
                            nonzero_targets += 1
                        logger.info(
                            "mogindex full-index target skipped completed run=%s day=%s category=%s source=%s name=%s scanned=%s indexed=%s elapsed=%.2fs",
                            run_id,
                            current,
                            item.category.key,
                            source_id,
                            getattr(item.target, "name", source_id),
                            scanned,
                            indexed,
                            elapsed,
                        )
                    else:
                        pending_targets.append(item)

                parallel_sources = self.full_index_parallel_sources()
                semaphore = asyncio.Semaphore(parallel_sources)
                logger.info(
                    "mogindex full-index day start run=%s day=%s targets=%s pending=%s skipped=%s parallel_sources=%s",
                    run_id,
                    current,
                    targets_count,
                    len(pending_targets),
                    targets_count - len(pending_targets),
                    parallel_sources,
                )
                tasks = [
                    asyncio.create_task(
                        self.collect_full_index_target(
                            item,
                            run_id=run_id,
                            index_date=current,
                            guild_id=guild_id,
                            after=after,
                            before=before,
                            semaphore=semaphore,
                        )
                    )
                    for item in pending_targets
                ]
                results = await asyncio.gather(*tasks) if tasks else []
                failed_results = [result for result in results if result.error]
                for result in results:
                    total_scanned += result.scanned
                    total_indexed += result.indexed
                    target_elapsed_total += result.elapsed_seconds
                    if result.scanned == 0 and result.indexed == 0:
                        zero_targets += 1
                    else:
                        nonzero_targets += 1
                if failed_results:
                    first = failed_results[0]
                    raise RuntimeError(
                        f"{len(failed_results)} source(s) failed; first={first.source_id} {first.source_name}: {first.error}"
                    )

                day_elapsed = time.perf_counter() - day_started
                avg_target_elapsed = target_elapsed_total / targets_count if targets_count else 0.0
                logger.info(
                    "mogindex full-index day run=%s day=%s targets=%s zero_targets=%s nonzero_targets=%s scanned=%s indexed=%s elapsed=%.2fs target_elapsed_sum=%.2fs avg_target=%.2fs parallel_sources=%s requested_by=%s",
                    run_id,
                    current,
                    targets_count,
                    zero_targets,
                    nonzero_targets,
                    total_scanned,
                    total_indexed,
                    day_elapsed,
                    target_elapsed_total,
                    avg_target_elapsed,
                    parallel_sources,
                    requested_by,
                )
            except asyncio.CancelledError:
                day_elapsed = time.perf_counter() - day_started
                await self.run_full_index_db_write(
                    self.mark_full_index_day_failed,
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
                await self.run_full_index_db_write(
                    self.mark_full_index_day_failed,
                    run_id,
                    current,
                    status="failed",
                    targets=targets_count,
                    scanned=total_scanned,
                    indexed=total_indexed,
                    elapsed_seconds=day_elapsed,
                    error_text=f"{type(exc).__name__}: {exc}",
                )
                logger.error(
                    "mogindex full-index day failed run=%s day=%s targets=%s zero_targets=%s nonzero_targets=%s elapsed=%.2fs target_elapsed_sum=%.2fs error=%s",
                    run_id,
                    current,
                    targets_count,
                    zero_targets,
                    nonzero_targets,
                    day_elapsed,
                    target_elapsed_total,
                    exc,
                    exc_info=True,
                )
                await self.report_exception(
                    "전체색인 하루 처리 실패",
                    exc,
                    details={
                        "run_id": run_id,
                        "day": str(current),
                        "elapsed_seconds": round(day_elapsed, 2),
                        "target_elapsed_sum": round(target_elapsed_total, 2),
                        "zero_targets": zero_targets,
                        "nonzero_targets": nonzero_targets,
                        "requested_by": requested_by,
                    },
                )
                return

            next_date = None if current == end_date else current - timedelta(days=1)
            await self.run_full_index_db_write(
                self.mark_full_index_day_completed,
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
        await self.run_full_index_db_write(self.mark_full_index_run_completed, run_id)
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

