"""침입자 추적 미니게임 — Discord 코그.

공개 명령(헌터, 게임 서버): /탑승 /이동 /수색 /추적 /대기 /현황 /추적기도움말
관제 명령(어드민, 관제 서버 + 사용자 허용 목록): /추적기 … , /도주 …

숨겨진 상태(도주자 위치·RAM·명령)는 메모리와 data/fugitive_state.json 에만 있다.
공개 채널에는 PublicView 로 만든 내용만 나간다.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from typing import Dict, List, Optional

import discord
from discord import app_commands
from discord.ext import commands

import utils
from . import config as cfgmod
from . import engine as E
from . import bot_player, image_render, render, store
from . import strings as S
from .views import RoomSelectView, RoundView

logger = logging.getLogger(__name__)

MIN_HUNTERS, MAX_HUNTERS = 2, 6


def _env_int(name: str) -> int:
    try:
        return int(os.getenv(name, "0") or 0)
    except ValueError:
        return 0


def capture_line(name: str) -> str:
    if utils.ends_with_hangul(name):
        particle = utils.get_korean_particle(name, "이", "가")
    else:
        particle = "이(가)"
    return S.CAPTURE_LINE.format(name=name, particle=particle)


class FugitiveCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sheet_handler = getattr(bot, "sheet_handler", None)
        self.state: Optional[E.GameState] = None
        self.lock = asyncio.Lock()
        self.timer_task: Optional[asyncio.Task] = None
        self.bot_task: Optional[asyncio.Task] = None
        self.fugitive_task: Optional[asyncio.Task] = None
        self.bot_player = bot_player.GeminiPlayer()
        self.sheet_map_ids: List[str] = []       # 마지막 /추적기 시작 때 시트에서 읽은 맵 ID (자동완성용)
        self.rng = random.Random()
        self.control_guild_id = _env_int("FUGITIVE_CONTROL_GUILD_ID")
        self.admin_ids = {
            int(x) for x in os.getenv("FUGITIVE_ADMIN_IDS", "").replace(";", ",").split(",") if x.strip().isdigit()
        }
        self.admin_group = self._build_admin_group()
        self.fugitive_group = self._build_fugitive_group()

    # ------------------------------------------------------------------ 수명
    async def cog_load(self):
        self.bot.add_view(RoundView())
        if self.control_guild_id:
            guild = discord.Object(id=self.control_guild_id)
            self.bot.tree.add_command(self.admin_group, guild=guild)
            self.bot.tree.add_command(self.fugitive_group, guild=guild)
        else:
            logger.warning("FUGITIVE_CONTROL_GUILD_ID 미설정 — 관제 명령을 등록하지 않습니다")
        self.state = store.load()
        if self.state and self.state.status == E.ROUND_OPEN:
            remaining = max(15.0, self.state.deadline_ts - time.time())
            self._arm_timer(remaining)
            logger.info("추적기 상태 복원: R%s, 남은 시간 %.0fs", self.state.round_no, remaining)
            self._schedule_bot_turn()
            self._schedule_fugitive_turn()
        elif self.state and self.state.status == E.PAUSED:
            logger.info("추적기 상태 복원: 일시정지 상태")

    async def cog_unload(self):
        self._cancel_timer()
        for t in (self.bot_task, self.fugitive_task):
            if t and not t.done():
                t.cancel()
        if self.control_guild_id:
            guild = discord.Object(id=self.control_guild_id)
            self.bot.tree.remove_command(self.admin_group.name, guild=guild)
            self.bot.tree.remove_command(self.fugitive_group.name, guild=guild)

    # ------------------------------------------------------------------ 도우미
    def _is_admin(self, interaction: discord.Interaction) -> bool:
        return (interaction.guild_id == self.control_guild_id and self.control_guild_id != 0
                and interaction.user.id in self.admin_ids)

    def _save(self):
        store.save(self.state)

    async def _char_name(self, user: discord.abc.User, guild: Optional[discord.Guild]) -> str:
        if self.sheet_handler is not None:
            try:
                row = self.sheet_handler.get_char_data(str(user.id))
                if row is not None:
                    name = str(row.get("name", "")).strip()
                    if name and name.lower() != "nan":
                        return name
            except Exception as e:  # noqa: BLE001
                logger.warning("캐릭터 이름 조회 실패: %s", e)
        if guild:
            member = guild.get_member(user.id)
            if member:
                return member.display_name
        return user.display_name

    def _char_color(self, user_id: str) -> str:
        """Characters 시트의 color 열 (#RRGGBB). 열이 없거나 비어 있으면 ''."""
        if self.sheet_handler is None:
            return ""
        try:
            row = self.sheet_handler.get_char_data(str(user_id))
            if row is None:
                return ""
            for key in ("color", "colour", "색상", "색"):
                v = row.get(key) if hasattr(row, "get") else None
                if v is not None and str(v).strip() and str(v).strip().lower() != "nan":
                    return str(v).strip()
        except Exception as e:  # noqa: BLE001
            logger.warning("캐릭터 색 조회 실패 (%s): %s", user_id, e)
        return ""

    def _apply_char_colors(self, st: E.GameState) -> None:
        for uid, h in st.hunters.items():
            if not bot_player.is_bot(uid):
                c = self._char_color(uid)
                if c:
                    h.color = c

    async def _channel(self, channel_id: int) -> Optional[discord.abc.Messageable]:
        ch = self.bot.get_channel(channel_id)
        if ch is None:
            try:
                ch = await self.bot.fetch_channel(channel_id)
            except discord.HTTPException:
                return None
        return ch  # type: ignore[return-value]

    async def _control_send(self, content: str):
        if not self.state or not self.state.control_channel_id:
            return
        ch = await self._channel(self.state.control_channel_id)
        if ch:
            try:
                await ch.send(content)
            except discord.HTTPException as e:
                logger.warning("관제 채널 전송 실패: %s", e)

    def _names(self) -> Dict[str, str]:
        return {uid: h.name for uid, h in self.state.hunters.items()} if self.state else {}

    def _render_mode(self) -> str:
        mode = str(self.state.config.get("render_mode", "text")) if self.state else "text"
        if mode != "image":
            return "text"
        why = image_render.unavailable_reason()
        if why is None:
            return "image"
        if why != getattr(self, "_render_warned", None):
            logger.warning("render_mode=image 이지만 텍스트로 대체: %s", why)
            self._render_warned = why
        return "text"

    async def _update_table(self, channel: discord.abc.Messageable):
        st = self.state
        pv = E.public_view(st)
        text = render.table_text(pv)
        kwargs: Dict = {"content": text}
        if self._render_mode() == "image":
            buf = image_render.render(pv)
            if buf:
                kwargs = {"content": f"**{S.TABLE_TITLE}**", "attachments": [discord.File(buf, "tracker.png")]}
        # 현황판은 라운드마다 새 메시지로 게시한다 (고정/편집 없음).
        if "attachments" in kwargs:
            kwargs["files"] = kwargs.pop("attachments")
        msg = await channel.send(**kwargs)
        st.table_message_id = msg.id

    # ------------------------------------------------------------------ 타이머
    def _arm_timer(self, seconds: float):
        self._cancel_timer()
        self.timer_task = asyncio.create_task(self._timer(seconds))

    def _cancel_timer(self):
        task = self.timer_task
        self.timer_task = None
        if task is None or task.done():
            return
        # 타이머 태스크 자신이 정산 중에 호출하는 경우 스스로를 취소하면 안 된다.
        # (다음 await 에서 CancelledError 가 터져 보고/체포 메시지가 조용히 사라진다)
        if task is asyncio.current_task():
            return
        task.cancel()

    async def _timer(self, seconds: float):
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            return
        try:
            await self._close_round(timed_out=True)
        except Exception:  # noqa: BLE001
            logger.exception("추적기 타이머 정산 중 예외")

    def _maybe_shorten_deadline(self):
        """전원 제출 시: 도주자도 제출했으면 즉시, 아니면 유예 후 정산."""
        st = self.state
        if not st or st.status != E.ROUND_OPEN or not st.config.get("early_resolve", True):
            return
        if not st.all_hunters_submitted():
            return
        now = time.time()
        if st.fugitive.order is not None or st.fugitive.frozen:
            st.deadline_ts = now
            self._arm_timer(0.5)
            return
        grace = float(st.config.get("fugitive_grace_sec", 0))
        new_deadline = min(st.deadline_ts, now + grace)
        if new_deadline < st.deadline_ts:
            st.deadline_ts = new_deadline
            self._arm_timer(max(0.5, new_deadline - now))

    # ------------------------------------------------------------------ 라운드 진행
    async def _open_round(self, channel: discord.abc.Messageable):
        st = self.state
        st.status = E.ROUND_OPEN
        st.deadline_ts = time.time() + float(st.config["round_timer_sec"])
        msg = await channel.send(render.round_open_text(st, int(st.deadline_ts)), view=RoundView())
        st.round_message_id = msg.id
        self._save()
        self._arm_timer(st.deadline_ts - time.time())
        self._schedule_bot_turn()
        self._schedule_fugitive_turn()

    # ------------------------------------------------------------------ 디버그 자동 헌터
    def _schedule_bot_turn(self):
        """라운드가 열릴 때 자동 헌터들의 명령을 백그라운드로 받는다 (락 밖에서 실행)."""
        st = self.state
        if not st or not st.debug_bots or st.status != E.ROUND_OPEN:
            return
        pending = [uid for uid in st.hunters if bot_player.is_bot(uid)
                   and st.hunters[uid].order is None and st.hunters[uid].dazed_until < st.round_no]
        if not pending:
            return
        if self.bot_task and not self.bot_task.done():
            return
        self.bot_task = asyncio.create_task(self._run_bot_turn(st.round_no, pending))

    async def _run_bot_turn(self, round_no: int, uids: List[str]):
        """봇 한 명씩: Gemini 요청 → 판단 → 선택을 관제 채널에 게시 → 행동 제출."""
        st = self.state
        for uid in uids:
            if self.state is not st or st.status != E.ROUND_OPEN or st.round_no != round_no:
                logger.info("자동 헌터 R%d 진행 중단 (라운드가 이미 넘어감)", round_no)
                return
            if st.hunters[uid].order is not None:
                continue
            name = st.hunters[uid].name
            await self._control_send(S.BOT_THINKING.format(name=name, round=round_no))
            try:
                d = await self.bot_player.decide(st, uid)
            except Exception:  # noqa: BLE001
                logger.exception("자동 헌터 %s R%d 판단 중 예외", name, round_no)
                continue
            # 선택을 먼저 관제 채널에 알리고, 그 다음 실제로 행동한다
            await self._control_send(bot_player.decision_text(name, d))
            note = ""
            accepted = True
            async with self.lock:
                if self.state is not st or st.status != E.ROUND_OPEN or st.round_no != round_no:
                    logger.info("자동 헌터 %s R%d 판단 폐기 (라운드가 이미 넘어감)", name, round_no)
                    return
                if st.hunters[uid].order is not None:
                    continue
                try:
                    E.submit_hunter_order(st, uid, d.order, d.target)
                except E.RuleError as e:
                    accepted, note = False, str(e)
                    try:
                        E.submit_hunter_order(st, uid, "stay", None)
                    except E.RuleError:
                        pass
                bot_player.record_reason(st, uid, d, accepted, note)
                self._save()
            if not accepted:
                await self._control_send(S.BOT_REJECTED.format(name=name, note=note))
            await self._refresh_round_message()
        async with self.lock:
            if self.state is st and st.status == E.ROUND_OPEN and st.round_no == round_no:
                self._maybe_shorten_deadline()
                self._save()

    def _schedule_fugitive_turn(self):
        """도주자 AI 모드: 라운드가 열리면 Gemini 에게 도주자 명령을 받아 제출한다."""
        st = self.state
        if not st or not st.debug_fugitive_ai or st.status != E.ROUND_OPEN or st.fugitive is None:
            return
        if st.fugitive.order is not None:
            return
        if self.fugitive_task and not self.fugitive_task.done():
            return
        self.fugitive_task = asyncio.create_task(self._run_fugitive_turn(st.round_no))

    async def _run_fugitive_turn(self, round_no: int):
        st = self.state
        await self._control_send(S.BOT_THINKING.format(name=bot_player.FUGITIVE_NAME, round=round_no))
        try:
            d = await bot_player.decide_fugitive(self.bot_player, st)
        except Exception:  # noqa: BLE001
            logger.exception("도주자 AI R%d 판단 중 예외", round_no)
            return
        await self._control_send(bot_player.fugitive_decision_text(d))
        note, accepted = "", True
        async with self.lock:
            if self.state is not st or st.status != E.ROUND_OPEN or st.round_no != round_no:
                logger.info("도주자 AI R%d 판단 폐기 (라운드가 이미 넘어감)", round_no)
                return
            if st.fugitive.order is not None:      # 관리자가 /도주 로 먼저 제출한 경우 존중
                logger.info("도주자 AI R%d 판단 폐기 (이미 제출됨)", round_no)
                return
            try:
                E.submit_fugitive_order(st, d.move, d.hack, d.target)
            except E.RuleError as e:
                accepted, note = False, str(e)
                try:                                # 퀵핵만 문제였으면 이동은 살린다
                    E.submit_fugitive_order(st, d.move, None, None)
                    note += " → 퀵핵 없이 이동만 제출"
                except E.RuleError:
                    E.submit_fugitive_order(st, None, None, None)
                    note += " → 대기"
            bot_player.record_fugitive_reason(st, d, accepted, note)
            self._maybe_shorten_deadline()
            self._save()
        if not accepted:
            await self._control_send(S.BOT_REJECTED.format(name=bot_player.FUGITIVE_NAME, note=note))
        await self._control_send(render.true_state_text(st))

    async def _refresh_round_message(self):
        st = self.state
        if not st or not st.round_message_id:
            return
        ch = await self._channel(st.channel_id)
        if not ch:
            return
        try:
            msg = await ch.fetch_message(st.round_message_id)  # type: ignore[attr-defined]
            await msg.edit(content=render.round_open_text(st, int(st.deadline_ts)))
        except discord.HTTPException:
            pass

    async def _close_round(self, timed_out: bool = False):
        async with self.lock:
            st = self.state
            if not st or st.status != E.ROUND_OPEN:
                return
            self._cancel_timer()
            round_no = st.round_no
            logger.info("추적기 R%d 정산 시작 (timed_out=%s)", round_no, timed_out)
            try:
                rep = E.resolve_round(st, self.rng, timed_out=timed_out)
            except E.RuleError as e:
                logger.error("정산 실패: %s", e)
                st.status = E.ROUND_OPEN
                return
            self._save()
            entry = st.log[-1] if st.log else {}
            logger.info(
                "추적기 R%d 정산 완료: 도주자 %s→%s 명령=%s | 헌터=%s | 숨김이벤트=%s | 체포=%s(%s) | 추적률=%s RAM=%s",
                round_no, entry.get("fugitive_room_before"), entry.get("fugitive_room_after"),
                entry.get("fugitive_order"),
                {self._names().get(uid, uid): (o.get("type"), o.get("target"), st.hunters[uid].room)
                 for uid, o in entry.get("hunter_orders", {}).items()},
                entry.get("hidden_events"), rep.get("captured_by"), rep.get("capture_how"), rep.get("trace"), entry.get("ram"),
            )
            ch = await self._channel(st.channel_id)
            if ch is None:
                logger.error("게임 채널 %s 을 찾을 수 없습니다 — R%d 보고를 게시하지 못했습니다", st.channel_id, round_no)
                return
            # 이전 라운드 메시지의 버튼 제거
            if st.round_message_id:
                try:
                    old = await ch.fetch_message(st.round_message_id)  # type: ignore[attr-defined]
                    await old.edit(view=None)
                except discord.HTTPException as e:
                    logger.warning("R%d 라운드 메시지 버튼 제거 실패: %s", round_no, e)
            try:
                await ch.send(render.report_text(rep, self._names()))
            except discord.HTTPException as e:
                logger.error("R%d 보고 게시 실패: %s", round_no, e)
            try:
                await self._update_table(ch)
            except discord.HTTPException as e:
                logger.error("R%d 추적 테이블 갱신 실패: %s", round_no, e)
            await self._control_send(render.true_state_text(st))
            if st.status == E.CAPTURED:
                name = st.hunters[st.captured_by].name
                logger.info("추적기 종료: R%d 에서 %s 이(가) 체포 (%s)", round_no, name, st.capture_how)
                try:
                    await ch.send(capture_line(name))
                except discord.HTTPException as e:
                    logger.error("체포 메시지 게시 실패: %s", e)
                st.round_message_id = 0
                self._save()
                return
            await self._open_round(ch)
            logger.info("추적기 R%d 개시, 마감 %s", st.round_no, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.deadline_ts)))

    # ------------------------------------------------------------------ 헌터 입력 공통
    async def _reply(self, interaction: discord.Interaction, content: str, edit: bool = False):
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=True)
        elif edit:
            await interaction.response.edit_message(content=content, view=None)
        else:
            await interaction.response.send_message(content, ephemeral=True)

    def _hunter_guard(self, interaction: discord.Interaction) -> Optional[str]:
        st = self.state
        if st is None or st.status in (E.IDLE, E.LOBBY, E.CAPTURED):
            return S.ERR_NO_GAME
        if interaction.guild_id != st.guild_id:
            return S.ERR_WRONG_GUILD
        if str(interaction.user.id) not in st.hunters:
            return S.ERR_NOT_HUNTER
        if st.status != E.ROUND_OPEN:
            return S.ERR_NOT_OPEN
        return None

    async def handle_button(self, interaction: discord.Interaction, kind: str):
        err = self._hunter_guard(interaction)
        if err:
            await self._reply(interaction, err)
            return
        if kind == "move":
            h = self.state.hunters[str(interaction.user.id)]
            if h.dazed_until >= self.state.round_no:
                await self._reply(interaction, S.ERR_DAZED)
                return
            rooms = list(self.state.game_map.rooms[h.room].neighbors)
            await interaction.response.send_message(
                f"현재 위치 **{h.room}**. 이동할 구역을 고르세요.", view=RoomSelectView(rooms, h.room), ephemeral=True)
            return
        await self.handle_order(interaction, kind, None)

    async def handle_order(self, interaction: discord.Interaction, kind: str, target: Optional[str], edit: bool = False):
        err = self._hunter_guard(interaction)
        if err:
            await self._reply(interaction, err, edit)
            return
        st = self.state
        uid = str(interaction.user.id)
        async with self.lock:
            if st.status != E.ROUND_OPEN:
                await self._reply(interaction, S.ERR_NOT_OPEN, edit)
                return
            try:
                order = E.submit_hunter_order(st, uid, kind, target)
            except E.RuleError as e:
                h = st.hunters[uid]
                code = str(e)
                if code == "DAZED":
                    msg = S.ERR_DAZED
                elif code == "NOT_ADJACENT":
                    msg = S.ERR_NOT_ADJACENT.format(room=target, here=h.room, options=", ".join(st.game_map.rooms[h.room].neighbors))
                elif code == "UNKNOWN_ROOM":
                    msg = S.ERR_UNKNOWN_ROOM.format(room=target)
                elif code == "NOT_OPEN":
                    msg = S.ERR_NOT_OPEN
                else:
                    msg = code
                await self._reply(interaction, msg, edit)
                return
            all_in = st.all_hunters_submitted()
            self._maybe_shorten_deadline()
            self._save()
        label = render.order_label(order)
        await self._reply(interaction, (S.OK_ORDER_ALL_IN if all_in and st.fugitive.order else S.OK_ORDER).format(order=label), edit)
        await self._refresh_round_message()
        if st.fugitive.ping_round == st.round_no:
            await self._control_send(f"📡 핑: {st.hunters[uid].name} → {label}")

    # ------------------------------------------------------------------ 공개 슬래시 명령
    @app_commands.command(name="탑승", description="침입자 추적에 참가합니다 (참가 모집 중에만).")
    async def join(self, interaction: discord.Interaction):
        st = self.state
        if st is None or st.status != E.LOBBY:
            await interaction.response.send_message(S.ERR_LOBBY_ONLY, ephemeral=True)
            return
        if interaction.guild_id != st.guild_id:
            await interaction.response.send_message(S.ERR_WRONG_GUILD, ephemeral=True)
            return
        uid = str(interaction.user.id)
        if uid in st.hunters:
            await interaction.response.send_message(S.ERR_ALREADY_JOINED, ephemeral=True)
            return
        if len(st.hunters) >= MAX_HUNTERS:
            await interaction.response.send_message(S.ERR_LOBBY_FULL.format(max=MAX_HUNTERS), ephemeral=True)
            return
        name = await self._char_name(interaction.user, interaction.guild)
        st.hunters[uid] = E.Hunter(user_id=uid, name=name, room="", joined_ts=time.time())
        self._save()
        await interaction.response.send_message(S.OK_JOINED.format(name=name, n=len(st.hunters)), ephemeral=True)
        await self._refresh_lobby_message()

    async def _refresh_lobby_message(self):
        st = self.state
        if not st or not st.round_message_id:
            return
        ch = await self._channel(st.channel_id)
        if not ch:
            return
        try:
            msg = await ch.fetch_message(st.round_message_id)  # type: ignore[attr-defined]
            await msg.edit(content=render.lobby_text([h.name for h in st.hunters.values()]))
        except discord.HTTPException:
            pass

    async def _room_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        st = self.state
        if not st or st.status != E.ROUND_OPEN:
            return []
        h = st.hunters.get(str(interaction.user.id))
        if not h:
            return []
        rooms = [r for r in st.game_map.rooms[h.room].neighbors if current.lower() in r.lower()]
        return [app_commands.Choice(name=f"{r} ({S.DEVICE_KO[st.game_map.rooms[r].device]})", value=r) for r in rooms][:25]

    @app_commands.command(name="이동", description="인접한 구역으로 이동 명령을 제출합니다.")
    @app_commands.describe(구역="이동할 구역 (예: B2)")
    @app_commands.autocomplete(구역=_room_autocomplete)
    async def move(self, interaction: discord.Interaction, 구역: str):
        await self.handle_order(interaction, "move", 구역.strip().upper())

    @app_commands.command(name="수색", description="현재 구역을 수색합니다. 숨은 침입자를 잡는 유일한 방법.")
    async def search(self, interaction: discord.Interaction):
        await self.handle_order(interaction, "search", None)

    @app_commands.command(name="추적", description="추적기를 조작해 침입자가 있는 구역의 장치 계열을 알아냅니다.")
    async def scan(self, interaction: discord.Interaction):
        await self.handle_order(interaction, "scan", None)

    @app_commands.command(name="대기", description="이번 라운드는 제자리에 머뭅니다.")
    async def stay(self, interaction: discord.Interaction):
        await self.handle_order(interaction, "stay", None)

    @app_commands.command(name="현황", description="추적기 현황판을 다시 보여줍니다.")
    async def status(self, interaction: discord.Interaction):
        st = self.state
        if st is None or st.status in (E.IDLE, E.LOBBY):
            await interaction.response.send_message(S.ERR_NO_GAME, ephemeral=True)
            return
        if interaction.guild_id == self.control_guild_id and self.control_guild_id:
            await interaction.response.send_message(S.ERR_WRONG_GUILD, ephemeral=True)
            return
        pv = E.public_view(st)
        if self._render_mode() == "image":
            buf = image_render.render(pv)
            if buf:
                await interaction.response.send_message(file=discord.File(buf, "tracker.png"), ephemeral=True)
                return
        await interaction.response.send_message(render.table_text(pv), ephemeral=True)

    @app_commands.command(name="추적기도움말", description="침입자 추적 미니게임 규칙 안내.")
    async def help_cmd(self, interaction: discord.Interaction):
        budget = self.state.config["scan_budget"] if self.state and self.state.config else cfgmod.DEFAULTS["scan_budget"]
        await interaction.response.send_message(S.HELP.format(scan_budget=budget), ephemeral=True)

    # ------------------------------------------------------------------ 관제 명령: /추적기
    def _build_admin_group(self) -> app_commands.Group:
        cog = self
        grp = app_commands.Group(name="추적기", description="[관제] 침입자 추적 미니게임 운영")

        async def admin_only(interaction: discord.Interaction) -> bool:
            return cog._is_admin(interaction)

        @grp.error
        async def on_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
            if isinstance(error, app_commands.CheckFailure):
                msg = S.ERR_WRONG_GUILD
            else:
                logger.error("관제 명령 오류: %s", error, exc_info=error)
                msg = f"오류: {error}"
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)

        @grp.command(name="개설", description="게임 채널을 지정하고 참가 모집을 시작합니다.")
        @app_commands.describe(채널id="공개 게임 채널 ID", 디버그="Gemini 자동 헌터로 채웁니다 (테스트용)", 인원="디버그 시 자동 헌터 수 (기본 4)",
                               도주자ai="도주자를 Gemini 가 조종합니다 (추적자는 사람)")
        @app_commands.check(admin_only)
        async def open_lobby(interaction: discord.Interaction, 채널id: str, 디버그: bool = False, 인원: int = 4, 도주자ai: bool = False):
            if cog.state and cog.state.status in (E.ROUND_OPEN, E.PAUSED, E.RESOLVING):
                await interaction.response.send_message("진행 중인 게임이 있습니다. 먼저 `/추적기 종료` 하세요.", ephemeral=True)
                return
            try:
                cid = int(채널id)
            except ValueError:
                await interaction.response.send_message("채널 ID는 숫자여야 합니다.", ephemeral=True)
                return
            if (디버그 or 도주자ai) and not cog.bot_player.available:
                await interaction.response.send_message("GEMINI_API_KEY 가 설정되어 있지 않아 AI 플레이를 열 수 없습니다.", ephemeral=True)
                return
            if 디버그 and not MIN_HUNTERS <= 인원 <= MAX_HUNTERS:
                await interaction.response.send_message(f"자동 헌터 수는 {MIN_HUNTERS}~{MAX_HUNTERS}명이어야 합니다.", ephemeral=True)
                return
            ch = await cog._channel(cid)
            if ch is None or not isinstance(ch, (discord.TextChannel, discord.Thread)):
                await interaction.response.send_message("채널을 찾을 수 없습니다.", ephemeral=True)
                return
            st = E.GameState(status=E.LOBBY, guild_id=ch.guild.id, channel_id=cid,
                             control_guild_id=interaction.guild_id or 0, control_channel_id=interaction.channel_id or 0,
                             created_ts=time.time(), debug_bots=디버그, debug_fugitive_ai=도주자ai)
            if 디버그:
                for uid, name in bot_player.bot_hunters(인원):
                    st.hunters[uid] = E.Hunter(user_id=uid, name=name, room="", joined_ts=time.time())
            cog.state = st
            msg = await ch.send(render.lobby_text([h.name for h in st.hunters.values()]))
            st.round_message_id = msg.id
            cog._save()
            extra = f" — 디버그: 자동 헌터 {인원}명 탑승, `/추적기 시작` 으로 바로 시작할 수 있습니다" if 디버그 else ""
            if 도주자ai:
                extra += " — 도주자 AI: 라운드마다 Gemini 가 도주자 명령을 제출합니다 (`/도주` 로 덮어쓸 수 있음)"
            await interaction.response.send_message(f"참가 모집 시작: <#{cid}>{extra}", ephemeral=True)

        @grp.command(name="ai추가", description="로비의 빈 자리를 Gemini 자동 헌터로 채웁니다 (사람과 함께 플레이).")
        @app_commands.describe(목표인원="채운 뒤의 총 인원 (생략 시 4). 이미 그 이상이면 아무것도 하지 않습니다")
        @app_commands.check(admin_only)
        async def add_ai_hunters(interaction: discord.Interaction, 목표인원: int = 4):
            st = cog.state
            if st is None or st.status != E.LOBBY:
                await interaction.response.send_message("참가 모집 중에만 가능합니다.", ephemeral=True)
                return
            if not cog.bot_player.available:
                await interaction.response.send_message("GEMINI_API_KEY 가 설정되어 있지 않아 AI 헌터를 추가할 수 없습니다.", ephemeral=True)
                return
            if not MIN_HUNTERS <= 목표인원 <= MAX_HUNTERS:
                await interaction.response.send_message(f"목표 인원은 {MIN_HUNTERS}~{MAX_HUNTERS}명이어야 합니다.", ephemeral=True)
                return
            need = 목표인원 - len(st.hunters)
            if need <= 0:
                await interaction.response.send_message(f"이미 {len(st.hunters)}명이라 추가할 자리가 없습니다.", ephemeral=True)
                return
            existing = sum(1 for uid in st.hunters if bot_player.is_bot(uid))
            added = []
            for uid, name in bot_player.bot_hunters(need, start=existing + 1):
                st.hunters[uid] = E.Hunter(user_id=uid, name=name, room="", joined_ts=time.time())
                added.append(name)
            st.debug_bots = True          # 라운드마다 봇 명령을 받는 스위치 (이유는 관제 채널에만 게시)
            cog._save()
            await cog._refresh_lobby_message()
            await interaction.response.send_message(
                f"AI 헌터 {len(added)}명 추가: {', '.join(added)} — 현재 {len(st.hunters)}명. "
                f"AI 의 판단 이유는 관제 채널에만 올라갑니다.", ephemeral=True)

        @grp.command(name="참가자", description="참가자를 수동으로 추가/제거합니다.")
        @app_commands.describe(동작="추가 또는 제거", 유저id="디스코드 사용자 ID", 이름="표시 이름 (추가 시, 생략하면 자동)")
        @app_commands.choices(동작=[app_commands.Choice(name="추가", value="add"), app_commands.Choice(name="제거", value="remove")])
        @app_commands.check(admin_only)
        async def manage_hunter(interaction: discord.Interaction, 동작: app_commands.Choice[str], 유저id: str, 이름: Optional[str] = None):
            st = cog.state
            if st is None or st.status != E.LOBBY:
                await interaction.response.send_message("참가 모집 중에만 가능합니다.", ephemeral=True)
                return
            uid = 유저id.strip()
            if 동작.value == "remove":
                st.hunters.pop(uid, None)
            else:
                name = 이름
                if not name:
                    try:
                        user = await cog.bot.fetch_user(int(uid))
                        guild = cog.bot.get_guild(st.guild_id)
                        name = await cog._char_name(user, guild)
                    except (discord.HTTPException, ValueError):
                        name = uid
                st.hunters[uid] = E.Hunter(user_id=uid, name=name, room="", joined_ts=time.time())
            cog._save()
            await cog._refresh_lobby_message()
            await interaction.response.send_message(f"참가자 {len(st.hunters)}명: " + ", ".join(h.name for h in st.hunters.values()), ephemeral=True)

        async def _map_autocomplete(interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
            ids = list(cfgmod.BUILTIN_MAPS) + [m for m in cog.sheet_map_ids if m not in cfgmod.BUILTIN_MAPS]
            cur = cfgmod.normalize_map_id(current)
            return [app_commands.Choice(name=f"{m} ({len(cfgmod.BUILTIN_MAPS.get(m, {})) or '시트'}칸)", value=m)
                    for m in ids if cur in m][:25]

        @grp.command(name="시작", description="시트를 한 번 읽고 게임을 시작합니다.")
        @app_commands.describe(시작구역="도주자 시작 구역 (생략 시 자동)", 맵="맵 ID 강제 지정 (선택, 내장: car2077_9 / car2077_12 / car2077_16)")
        @app_commands.autocomplete(맵=_map_autocomplete)
        @app_commands.check(admin_only)
        async def start_game(interaction: discord.Interaction, 시작구역: Optional[str] = None, 맵: Optional[str] = None):
            st = cog.state
            if st is None or st.status != E.LOBBY:
                await interaction.response.send_message("먼저 `/추적기 개설` 로 참가를 모집하세요.", ephemeral=True)
                return
            n = len(st.hunters)
            if not MIN_HUNTERS <= n <= MAX_HUNTERS:
                await interaction.response.send_message(f"참가자는 {MIN_HUNTERS}~{MAX_HUNTERS}명이어야 합니다 (현재 {n}명).", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            spreadsheet = getattr(cog.sheet_handler, "spreadsheet", None)
            try:
                sheet = await asyncio.to_thread(cfgmod.load_sheet_data, spreadsheet)
            except Exception as e:  # noqa: BLE001
                logger.warning("Fugitive 시트 읽기 실패, 기본값 사용: %s", e)
                sheet = {"maps": None, "config": None, "scaling": None}
            try:
                config, map_id = cfgmod.build_config(n, sheet["config"], sheet["scaling"])
                if st.config:   # 로비에서 /추적기 설정 으로 미리 넣은 값이 시트/기본값보다 우선
                    config.update(st.config)
                    logger.info("추적기 시작: 로비 설정 적용 %s", st.config)
                maps = dict(cfgmod.BUILTIN_MAPS)
                if sheet["maps"]:
                    maps.update(sheet["maps"])
                cog.sheet_map_ids = list(sheet["maps"] or {})
                wanted = cfgmod.normalize_map_id(맵 or map_id)
                by_norm = {cfgmod.normalize_map_id(k): k for k in maps}
                if wanted not in by_norm:
                    raise cfgmod.ConfigError(f"맵 {맵 or map_id!r} 없음 (사용 가능: {', '.join(maps)})")
                map_id = by_norm[wanted]
                logger.info("추적기 시작: 맵 %s (입력 %r), 참가 %d명", map_id, 맵, n)
                gmap = E.GameMap.from_rows(map_id, cfgmod.map_rows(map_id, maps))
                hunters = [(uid, h.name) for uid, h in st.hunters.items()]
                new = E.new_game(gmap, config, hunters, fugitive_start=(시작구역 or None), rng=cog.rng)
                for uid, h in new.hunters.items():
                    h.color = st.hunters[uid].color
                cog._apply_char_colors(new)     # 시작 시점의 시트 값이 최종
            except (cfgmod.ConfigError, E.RuleError) as e:
                await interaction.followup.send(f"시작 실패: {e}", ephemeral=True)
                return
            new.guild_id, new.channel_id = st.guild_id, st.channel_id
            new.control_guild_id, new.control_channel_id = st.control_guild_id, st.control_channel_id
            new.debug_bots = st.debug_bots
            new.debug_fugitive_ai = st.debug_fugitive_ai
            cog.state = new
            ch = await cog._channel(new.channel_id)
            if ch is None:
                await interaction.followup.send("게임 채널을 찾을 수 없습니다.", ephemeral=True)
                return
            await ch.send(f"**{S.COMBAT_MODE_ON}**\n{S.GAME_START_INTRO}")
            await ch.send(S.TUTORIAL.format(scan_budget=new.config["scan_budget"]))
            new.table_message_id = 0
            await cog._update_table(ch)
            await cog._open_round(ch)
            await cog._control_send(render.true_state_text(new))
            note = ""
            if str(config.get("render_mode")) == "image":
                why = image_render.unavailable_reason()
                note = f"\n⚠ render_mode=image 이지만 텍스트로 대체합니다: {why}" if why else f"\n🖼 이미지 현황판 (글꼴: {image_render.font_path()})"
            await interaction.followup.send(f"시작: 맵 {map_id}, 참가 {n}명, 라운드 타이머 {config['round_timer_sec']}초{note}", ephemeral=True)

        @grp.command(name="상태", description="실제 상태(도주자 위치 포함)를 봅니다.")
        @app_commands.check(admin_only)
        async def true_state(interaction: discord.Interaction):
            if cog.state is None:
                await interaction.response.send_message(S.ERR_NO_GAME, ephemeral=True)
                return
            await interaction.response.send_message(render.true_state_text(cog.state), ephemeral=True)

        dbg = app_commands.Group(name="디버그", description="[관제] 자동 플레이 디버그", parent=grp)

        @dbg.command(name="재요청", description="이번 라운드 자동 헌터 판단을 다시 받습니다 (미제출자만).")
        @app_commands.check(admin_only)
        async def bot_rerun(interaction: discord.Interaction):
            st = cog.state
            if st is None or not (st.debug_bots or st.debug_fugitive_ai) or st.status != E.ROUND_OPEN:
                await interaction.response.send_message("자동 플레이 라운드가 열려 있지 않습니다.", ephemeral=True)
                return
            cog._schedule_bot_turn()
            cog._schedule_fugitive_turn()
            await interaction.response.send_message("자동 헌터 판단을 요청했습니다.", ephemeral=True)

        @grp.command(name="설정", description="튜너블 값을 즉시 바꿉니다.")
        @app_commands.describe(키="설정 키", 값="새 값")
        @app_commands.check(admin_only)
        async def set_config(interaction: discord.Interaction, 키: str, 값: str):
            st = cog.state
            if st is None or (not st.config and st.status != E.LOBBY):
                await interaction.response.send_message(S.ERR_NO_GAME, ephemeral=True)
                return
            # 로비 단계에서는 st.config 가 '시작 시 적용할 오버라이드' 역할을 한다
            try:
                v = cfgmod.coerce(키.strip(), 값)
            except cfgmod.ConfigError as e:
                await interaction.response.send_message(str(e), ephemeral=True)
                return
            st.config[키.strip()] = v
            if 키.strip() == "round_timer_sec" and st.status == E.ROUND_OPEN:
                pass  # 다음 라운드부터 적용; 현재 라운드는 /추적기 연장 으로
            cog._save()
            await interaction.response.send_message(f"`{키}` = `{v}`", ephemeral=True)

        @set_config.autocomplete("키")
        async def key_ac(interaction: discord.Interaction, current: str):
            keys = [k for k in cfgmod.DEFAULTS if current.lower() in k.lower()]
            return [app_commands.Choice(name=k, value=k) for k in keys[:25]]

        @grp.command(name="설정보기", description="현재 설정 전체를 봅니다.")
        @app_commands.check(admin_only)
        async def show_config(interaction: discord.Interaction):
            cfg = cog.state.config if cog.state and cog.state.config else cfgmod.DEFAULTS
            await interaction.response.send_message(render.config_text(cfg), ephemeral=True)

        @grp.command(name="라운드종료", description="타이머를 기다리지 않고 지금 정산합니다.")
        @app_commands.check(admin_only)
        async def force_close(interaction: discord.Interaction):
            if cog.state is None or cog.state.status != E.ROUND_OPEN:
                await interaction.response.send_message("정산할 라운드가 없습니다.", ephemeral=True)
                return
            await interaction.response.send_message("정산합니다.", ephemeral=True)
            await cog._close_round(timed_out=False)

        @grp.command(name="연장", description="현재 라운드 마감을 연장합니다 (초).")
        @app_commands.check(admin_only)
        async def extend(interaction: discord.Interaction, 초: int):
            st = cog.state
            if st is None or st.status != E.ROUND_OPEN:
                await interaction.response.send_message("진행 중인 라운드가 없습니다.", ephemeral=True)
                return
            st.deadline_ts = max(st.deadline_ts, time.time()) + 초
            cog._arm_timer(st.deadline_ts - time.time())
            cog._save()
            await cog._refresh_round_message()
            await interaction.response.send_message(f"마감: <t:{int(st.deadline_ts)}:f>", ephemeral=True)

        @grp.command(name="일시정지", description="타이머를 멈춥니다.")
        @app_commands.check(admin_only)
        async def pause(interaction: discord.Interaction):
            st = cog.state
            if st is None or st.status != E.ROUND_OPEN:
                await interaction.response.send_message("일시정지할 라운드가 없습니다.", ephemeral=True)
                return
            cog._cancel_timer()
            st.paused_remaining = max(0.0, st.deadline_ts - time.time())
            st.status = E.PAUSED
            cog._save()
            await interaction.response.send_message(f"일시정지 (남은 시간 {int(st.paused_remaining)}초)", ephemeral=True)

        @grp.command(name="재개", description="일시정지를 해제합니다.")
        @app_commands.check(admin_only)
        async def resume(interaction: discord.Interaction):
            st = cog.state
            if st is None or st.status != E.PAUSED:
                await interaction.response.send_message("일시정지 상태가 아닙니다.", ephemeral=True)
                return
            st.status = E.ROUND_OPEN
            st.deadline_ts = time.time() + max(15.0, st.paused_remaining)
            cog._arm_timer(st.deadline_ts - time.time())
            cog._save()
            await cog._refresh_round_message()
            await interaction.response.send_message("재개", ephemeral=True)

        @grp.command(name="위치", description="헌터 또는 도주자의 위치를 수동으로 바꿉니다.")
        @app_commands.describe(대상="유저 ID 또는 'fugitive'", 구역="구역 ID")
        @app_commands.check(admin_only)
        async def set_pos(interaction: discord.Interaction, 대상: str, 구역: str):
            st = cog.state
            if st is None or st.fugitive is None:
                await interaction.response.send_message(S.ERR_NO_GAME, ephemeral=True)
                return
            room = 구역.strip().upper()
            if room not in st.game_map.rooms:
                await interaction.response.send_message(S.ERR_UNKNOWN_ROOM.format(room=room), ephemeral=True)
                return
            if 대상.strip().lower() == "fugitive":
                st.fugitive.room = room
                st.position_history[-1] = room
            elif 대상.strip() in st.hunters:
                st.hunters[대상.strip()].room = room
            else:
                await interaction.response.send_message("대상을 찾을 수 없습니다.", ephemeral=True)
                return
            cog._save()
            await interaction.response.send_message(f"{대상} → {room}", ephemeral=True)

        @grp.command(name="요약", description="[종료 후] 라운드별 기록을 게시합니다.")
        @app_commands.describe(채널id="게시할 채널 ID (생략 시 게임 채널)")
        @app_commands.check(admin_only)
        async def summary(interaction: discord.Interaction, 채널id: Optional[str] = None):
            st = cog.state
            if st is None or not st.log:
                await interaction.response.send_message("기록이 없습니다.", ephemeral=True)
                return
            if st.status != E.CAPTURED:
                await interaction.response.send_message("게임이 끝난 뒤에만 게시할 수 있습니다.", ephemeral=True)
                return
            cid = int(채널id) if 채널id and 채널id.strip().isdigit() else st.channel_id
            ch = await cog._channel(cid)
            if ch is None:
                await interaction.response.send_message("채널을 찾을 수 없습니다.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True)
            for chunk in render.summary_chunks(st):
                await ch.send(chunk)
            await interaction.followup.send("게시 완료", ephemeral=True)

        @grp.command(name="종료", description="게임을 중단하고 상태를 지웁니다.")
        @app_commands.describe(확인="'확인' 을 입력해야 실행됩니다")
        @app_commands.check(admin_only)
        async def end_game(interaction: discord.Interaction, 확인: str):
            if 확인.strip() != "확인":
                await interaction.response.send_message("`확인` 을 입력해야 합니다.", ephemeral=True)
                return
            cog._cancel_timer()
            st = cog.state
            if st and st.round_message_id and st.status in (E.ROUND_OPEN, E.PAUSED):
                ch = await cog._channel(st.channel_id)
                if ch:
                    try:
                        msg = await ch.fetch_message(st.round_message_id)  # type: ignore[attr-defined]
                        await msg.edit(view=None)
                    except discord.HTTPException:
                        pass
            cog.state = None
            store.save(None)
            await interaction.response.send_message("종료했습니다.", ephemeral=True)

        return grp

    # ------------------------------------------------------------------ 관제 명령: /도주
    def _build_fugitive_group(self) -> app_commands.Group:
        cog = self
        grp = app_commands.Group(name="도주", description="[관제] 도주자 명령")

        async def admin_only(interaction: discord.Interaction) -> bool:
            return cog._is_admin(interaction)

        @grp.error
        async def on_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
            msg = S.ERR_WRONG_GUILD if isinstance(error, app_commands.CheckFailure) else f"오류: {error}"
            if not isinstance(error, app_commands.CheckFailure):
                logger.error("도주 명령 오류: %s", error, exc_info=error)
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)

        HACK_CHOICES = [app_commands.Choice(name=S.HACK_KO[h], value=h) for h in ("doorlock", "distract", "blackout", "hide", "overload")]

        async def _submit(interaction: discord.Interaction, move: Optional[str], hack: Optional[str], target: Optional[str]):
            st = cog.state
            if st is None or st.fugitive is None:
                await interaction.response.send_message(S.ERR_NO_GAME, ephemeral=True)
                return
            async with cog.lock:
                try:
                    order = E.submit_fugitive_order(st, move, hack, target)
                except E.RuleError as e:
                    await interaction.response.send_message(f"거부: {e}", ephemeral=True)
                    return
                cog._maybe_shorten_deadline()
                cog._save()
            desc = f"이동 {order.move or '대기'}" + (f" + {S.HACK_KO[order.hack]} {order.target or ''}" if order.hack else "")
            await interaction.response.send_message(f"도주자 명령 접수: {desc}\n(마감 전까지 다시 제출하면 덮어씁니다)", ephemeral=True)

        @grp.command(name="이동", description="도주자를 이동시킵니다 (퀵핵 동시 사용 가능).")
        @app_commands.describe(구역="목적지 구역", 핵="퀵핵", 대상="문 잠금: A1-A2 / 교란: 구역")
        @app_commands.choices(핵=HACK_CHOICES)
        @app_commands.check(admin_only)
        async def f_move(interaction: discord.Interaction, 구역: str, 핵: Optional[app_commands.Choice[str]] = None, 대상: Optional[str] = None):
            await _submit(interaction, 구역.strip().upper(), 핵.value if 핵 else None, 대상.strip().upper() if 대상 else None)

        @grp.command(name="대기", description="도주자가 제자리에 머뭅니다 (퀵핵 동시 사용 가능).")
        @app_commands.describe(핵="퀵핵", 대상="문 잠금: A1-A2 / 교란: 구역")
        @app_commands.choices(핵=HACK_CHOICES)
        @app_commands.check(admin_only)
        async def f_stay(interaction: discord.Interaction, 핵: Optional[app_commands.Choice[str]] = None, 대상: Optional[str] = None):
            await _submit(interaction, None, 핵.value if 핵 else None, 대상.strip().upper() if 대상 else None)

        @grp.command(name="핑", description="즉시 핑: 지금까지 제출된 헌터 명령을 봅니다.")
        @app_commands.check(admin_only)
        async def f_ping(interaction: discord.Interaction):
            st = cog.state
            if st is None or st.fugitive is None:
                await interaction.response.send_message(S.ERR_NO_GAME, ephemeral=True)
                return
            try:
                orders = E.cast_ping(st)
            except E.RuleError as e:
                await interaction.response.send_message(f"거부: {e}", ephemeral=True)
                return
            cog._save()
            lines = [S.ORDER_LINE.format(name=n, order=render.order_label(o)) for n, o in orders]
            await interaction.response.send_message("📡 핑 결과 (이후 제출도 관제 채널로 전달)\n" + "\n".join(lines), ephemeral=True)

        @grp.command(name="취소", description="이번 라운드에 제출한 도주자 명령을 취소합니다.")
        @app_commands.check(admin_only)
        async def f_cancel(interaction: discord.Interaction):
            st = cog.state
            if st is None or st.fugitive is None:
                await interaction.response.send_message(S.ERR_NO_GAME, ephemeral=True)
                return
            st.fugitive.order = None
            cog._save()
            await interaction.response.send_message("취소했습니다 (미제출 시 대기).", ephemeral=True)

        return grp


async def setup(bot: commands.Bot):
    await bot.add_cog(FugitiveCog(bot))
