"""카드 전투 Cog — `/카드` 단일 명령 + 턴 메시지 버튼.

세션은 전체에서 하나(`self.state`). 상태 변경은 전부 `self.lock` 안에서 하고 `_save()` 한다.
흐름: 주최(LOBBY) → 참가(로비 메시지 편집) → 시작
      → (prep_enabled) PREP: 준비 메시지의 버튼 3개(정식 개조/뒷골목 개조/준비 완료) → 전원 준비 또는 /카드 턴종료
      → (first_strike) 기습 보고 → ROUND_OPEN: 턴 메시지 + 카드 뽑기/방화벽 버튼
      → 전원 선택 또는 /카드 턴종료 → 라운드 정산 게시 → 다음 턴 메시지 … → ENDED (보수를 금고에 더함)
      → (NCPD 경계도가 차면) ESCAPE: 도주 메시지의 버튼 3개 → 전원 선택 → 도주 정산 … → ENDED
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

import discord
from discord import app_commands
from discord.ext import commands

import help_option

from . import config as cfgmod
from . import data as D
from . import embeds
from . import engine as E
from . import gig
from . import image_render
from . import render
from . import store
from . import strings as S
from .views import ChosenView, EscapeView, FirewallView, HandView, PrepView, TargetView, TurnView

logger = logging.getLogger(__name__)

OPTION_CHOICES = [
    app_commands.Choice(name="주최", value="host"),
    app_commands.Choice(name="참가", value="join"),
    app_commands.Choice(name="시작", value="start"),
    app_commands.Choice(name="턴종료", value="force"),
    app_commands.Choice(name="종료", value="end"),
    app_commands.Choice(name="현황", value="status"),
    app_commands.Choice(name="금고", value="vault"),
    app_commands.Choice(name="규칙", value="rules"),
]
MAX_AUTO_RESOLVE = 20
BOARD_FILENAME = "board.png"


def _id_set(*names: str) -> set:
    for n in names:
        raw = os.getenv(n, "")
        if raw.strip():
            return {int(x) for x in raw.replace(";", ",").split(",") if x.strip().isdigit()}
    return set()


class CardBattleCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sheet_handler = getattr(bot, "sheet_handler", None)
        self.state: Optional[E.BattleState] = None
        self.lock = asyncio.Lock()
        self.rng: E.Rng = E.DEFAULT_RNG
        self.data: Optional[D.GameData] = None
        self.store_path = store.DEFAULT_PATH
        self.gig_path = gig.DEFAULT_PATH
        self.admin_ids = _id_set("CARDBATTLE_ADMIN_IDS", "FUGITIVE_ADMIN_IDS")
        self._render_warned: Optional[str] = None

    # ------------------------------------------------------------------ 생명주기
    async def cog_load(self):
        self.bot.add_view(TurnView())
        self.bot.add_view(EscapeView())
        self.bot.add_view(PrepView())
        self.state = store.load(self.store_path)
        if self.state and self.state.status in (E.PREP, E.ROUND_OPEN, E.RESOLVING, E.ESCAPE, E.ENDED):
            asyncio.create_task(self._resume())

    def _save(self):
        store.save(self.state, self.store_path)

    async def _channel(self, channel_id: int):
        ch = self.bot.get_channel(channel_id)
        if ch is None:
            try:
                ch = await self.bot.fetch_channel(channel_id)
            except (discord.HTTPException, AttributeError):
                return None
        return ch

    async def _reply(self, interaction: discord.Interaction, content: Optional[str], ephemeral: bool = True, **kw):
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=ephemeral, **kw)
        else:
            await interaction.response.send_message(content, ephemeral=ephemeral, **kw)

    def _is_host(self, interaction: discord.Interaction) -> bool:
        st = self.state
        return bool(st and str(interaction.user.id) == st.host_id)

    def _is_admin(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id in self.admin_ids

    @staticmethod
    def _rule_msg(e: E.RuleError) -> str:
        return S.RULE_ERRORS.get(e.code, f"규칙 위반: {e.code}")

    def _spreadsheet(self):
        return getattr(self.sheet_handler, "spreadsheet", None)

    def _load_data(self) -> D.GameData:
        """블로킹. asyncio.to_thread 로 부른다."""
        gd = D.load_game_data(D.make_source(self._spreadsheet()))
        self.data = gd
        return gd

    def _image_mode(self) -> bool:
        st = self.state
        if not st or str(st.config.get("render_mode", "image")) != "image":
            return False
        why = image_render.unavailable_reason()
        if why is None:
            return True
        if why != self._render_warned:
            logger.warning("카드 전투 render_mode=image 이지만 임베드만 씁니다: %s", why)
            self._render_warned = why
        return False

    def _board_file(self, report: Optional[Dict[str, Any]] = None) -> Optional[discord.File]:
        if not self._image_mode():
            return None
        try:
            buf = image_render.render_board(self.state, report)
        except Exception:  # noqa: BLE001
            logger.exception("현황판 렌더 실패")
            return None
        return discord.File(buf, BOARD_FILENAME) if buf else None

    # ------------------------------------------------------------------ 명령
    async def _battle_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        try:
            gd = self.data or await asyncio.to_thread(self._load_data)
        except Exception:  # noqa: BLE001
            return []
        cur = (current or "").lower()
        out = []
        for b in gd.battles.values():
            if cur in b.id.lower() or cur in b.name.lower():
                label = f"{b.id} — {b.name}" + (f" [{b.players}인]" if b.players else "")
                out.append(app_commands.Choice(name=label[:100], value=b.id))
        return out[:25]

    @app_commands.command(name="카드", description="카드 전투 미니게임 — 주최 / 참가 / 시작 / 턴종료 / 종료 / 현황 / 금고 / 규칙")
    @app_commands.describe(옵션="실행할 동작", 전투id="시작할 전투 ID (옵션:시작 때 필요. 인원수에 맞는 id)",
                           금액="옵션:금고 에서 팀 금고를 이 값으로 맞춤 (주최자·관리자)", 도움말=help_option.HELP_OPTION_DESC)
    @app_commands.choices(옵션=OPTION_CHOICES)
    @app_commands.autocomplete(전투id=_battle_autocomplete)
    async def card(self, interaction: discord.Interaction,
                   옵션: Optional[app_commands.Choice[str]] = None,
                   전투id: Optional[str] = None,
                   금액: Optional[int] = None,
                   도움말: bool = False):
        if await help_option.maybe_help(interaction, 도움말, S.HELP):
            return
        if not await help_option.require(interaction, 옵션=옵션):
            return
        value = 옵션.value if isinstance(옵션, app_commands.Choice) else str(옵션)
        handler = getattr(self, f"_cmd_{value}", None)
        if handler is None:
            await self._reply(interaction, f"알 수 없는 옵션: {value}")
            return
        await handler(interaction, 전투id, 금액)

    async def _cmd_host(self, interaction: discord.Interaction, _, amount=None):
        async with self.lock:
            st = self.state
            if st is not None and st.status != E.ENDED:
                host = st.unit(st.host_id).name if st.unit(st.host_id) else st.host_id
                await self._reply(interaction, S.ERR_SESSION_EXISTS.format(host=host))
                return
            new = E.BattleState(status=E.LOBBY, host_id=str(interaction.user.id),
                                guild_id=interaction.guild_id or 0, channel_id=interaction.channel_id or 0,
                                config=cfgmod.build_config())
            msg = await interaction.channel.send(embed=embeds.lobby([]))
            new.lobby_message_id = msg.id
            self.state = new
            self._save()
        await self._reply(interaction, S.OK_HOSTED)

    def _char_row(self, uid: str):
        sh = self.sheet_handler
        if sh is None:
            return None
        try:
            return sh.get_char_data(str(uid))
        except Exception:  # noqa: BLE001
            return None

    async def _cmd_join(self, interaction: discord.Interaction, _, amount=None):
        async with self.lock:
            st = self.state
            if st is None or st.status == E.ENDED:
                await self._reply(interaction, S.ERR_NO_SESSION)
                return
            if st.status != E.LOBBY:
                await self._reply(interaction, S.ERR_LOBBY_ONLY)
                return
            if interaction.guild_id != st.guild_id:
                await self._reply(interaction, S.ERR_WRONG_GUILD)
                return
            uid = str(interaction.user.id)
            if uid in st.players:
                await self._reply(interaction, S.ERR_ALREADY_JOINED)
                return
            if len(st.players) >= int(st.config.get("max_players", 8)):
                await self._reply(interaction, S.ERR_LOBBY_FULL.format(max=st.config.get("max_players", 8)))
                return
            row = self._char_row(uid)
            if row is None and not st.config.get("allow_unregistered"):
                await self._reply(interaction, S.ERR_NO_CHARACTER)
                return
            name = getattr(interaction.user, "display_name", None) or interaction.user.name
            st.players[uid] = E.player_combatant(uid, row, name, st.config, is_host=(uid == st.host_id))
            self._save()
            n = len(st.players)
        await self._reply(interaction, S.OK_JOINED.format(name=name, n=n))
        await self._refresh_lobby_message()

    async def _refresh_lobby_message(self):
        st = self.state
        if not st:
            return
        ch = await self._channel(st.channel_id)
        if not ch:
            return
        emb = embeds.lobby([p.name for p in st.players.values()])
        try:
            msg = await ch.fetch_message(st.lobby_message_id)
            await msg.edit(embed=emb)
        except (discord.HTTPException, AttributeError, LookupError):
            try:
                msg = await ch.send(embed=emb)
                st.lobby_message_id = msg.id
                self._save()
            except discord.HTTPException:
                pass

    async def _cmd_start(self, interaction: discord.Interaction, battle_id: Optional[str], amount=None):
        st = self.state
        if st is None or st.status == E.ENDED:
            await self._reply(interaction, S.ERR_NO_SESSION)
            return
        if not self._is_host(interaction):
            await self._reply(interaction, S.ERR_HOST_ONLY)
            return
        if st.status != E.LOBBY:
            await self._reply(interaction, S.ERR_NOT_LOBBY)
            return
        if not await help_option.require(interaction, 전투id=battle_id):
            return
        if not st.players:
            await self._reply(interaction, S.ERR_NO_PLAYERS)
            return
        await interaction.response.defer(ephemeral=True)
        try:
            gd = await asyncio.to_thread(self._load_data)
        except Exception as e:  # noqa: BLE001
            logger.exception("카드 전투 데이터 로드 실패")
            await self._reply(interaction, S.ERR_DATA.format(err=e))
            return
        bid = battle_id.strip()
        bdef = gd.battles.get(bid)
        if bdef is None:
            await self._reply(interaction, S.ERR_BATTLE_NOT_FOUND.format(id=bid, ids=", ".join(gd.battles) or "-"))
            return
        async with self.lock:
            st = self.state
            if st is None or st.status != E.LOBBY:
                await self._reply(interaction, S.ERR_NOT_LOBBY)
                return
            cfg = dict(st.config)
            try:
                for k, v in bdef.overrides.items():
                    cfg[k] = cfgmod.coerce(k, v)
                enemies = E.enemy_combatants(bdef, gd.enemies, hp_mult=float(cfg.get("enemy_hp_mult", 1.0)))
                pot = None if bool(cfg.get("prep_sandbox")) else gig.load_pot(self.gig_path)
                script = gd.tutorials.get(str(cfg.get("tutorial_script", ""))) if "tutorial" in bdef.gimmicks else None
                new = E.new_battle(list(st.players.values()), enemies, gd.cards, cfg, host_id=st.host_id,
                                   battle_id=bdef.id, battle_name=bdef.name, gimmicks=bdef.gimmicks, rng=self.rng,
                                   pot=pot, script=script, weapon_names=S.WEAPON_NAMES)
            except cfgmod.ConfigError as e:
                await self._reply(interaction, S.ERR_DATA.format(err=e))
                return
            except E.RuleError as e:
                await self._reply(interaction, self._rule_msg(e))
                return
            new.guild_id, new.channel_id, new.lobby_message_id = st.guild_id, st.channel_id, st.lobby_message_id
            self.state = new
            self._save()
            ch = await self._channel(new.channel_id)
            if ch is None:
                await self._reply(interaction, "채널을 찾지 못했습니다.")
                return
            await ch.send(embed=embeds.intro(new, bdef.desc))
            if new.status == E.PREP:
                await self._open_prep(ch)
            else:
                await self._begin_messages(ch)
        if self.state is not None and self.state.status == E.PREP:
            await self._reply(interaction, S.OK_STARTED_PREP.format(battle=bdef.name))
            return
        await self._reply(interaction, S.OK_STARTED.format(battle=bdef.name))
        await self._resolve_round()   # 행동 가능한 참가자가 없으면 바로 넘어간다

    async def _cmd_force(self, interaction: discord.Interaction, _, amount=None):
        st = self.state
        if st is None or st.status == E.ENDED:
            await self._reply(interaction, S.ERR_NO_SESSION)
            return
        if not (self._is_host(interaction) or self._is_admin(interaction)):
            await self._reply(interaction, S.ERR_HOST_ONLY)
            return
        if st.status == E.PREP:
            await self._reply(interaction, S.OK_FORCED_PREP)
            async with self.lock:
                ch = await self._channel(st.channel_id)
                if ch is not None:
                    await self._finish_prep(ch)
            await self._resolve_round()
            return
        if st.status == E.ESCAPE:
            await self._reply(interaction, S.OK_FORCED)
            await self._resolve_escape(force=True)
            return
        if st.status != E.ROUND_OPEN:
            await self._reply(interaction, S.ERR_NOT_ALLY_TURN)
            return
        await self._reply(interaction, S.OK_FORCED)
        await self._resolve_round(force=True)

    async def _cmd_end(self, interaction: discord.Interaction, _, amount=None):
        async with self.lock:
            st = self.state
            if st is None:
                await self._reply(interaction, S.ERR_NO_SESSION)
                return
            if not (self._is_host(interaction) or self._is_admin(interaction)):
                await self._reply(interaction, S.ERR_HOST_ONLY)
                return
            await self._strip_turn_view()
            await self._strip_prep_view()
            self.state = None
            self._save()
        await self._reply(interaction, S.OK_ENDED)

    async def _cmd_status(self, interaction: discord.Interaction, _, amount=None):
        st = self.state
        if st is None:
            await self._reply(interaction, S.ERR_NO_SESSION)
            return
        if st.status == E.LOBBY:
            await self._reply(interaction, None, embed=embeds.lobby([p.name for p in st.players.values()]))
            return
        await self._reply(interaction, None, embed=embeds.status(st))

    async def _cmd_vault(self, interaction: discord.Interaction, _, amount: Optional[int] = None):
        st = self.state
        if amount is None:
            pot = gig.load_pot(self.gig_path)
            battle = ""
            if st is not None and st.status not in (E.LOBBY, E.ENDED) and "pot" in st.meters:
                battle = S.OK_VAULT_BATTLE.format(left=int(st.meters.get("pot", 0)))
            await self._reply(interaction, S.OK_VAULT.format(pot=pot if pot is not None else 0, battle=battle))
            return
        if not (self._is_host(interaction) or self._is_admin(interaction)):
            await self._reply(interaction, S.ERR_HOST_ONLY)
            return
        if int(amount) < 0:
            await self._reply(interaction, S.ERR_VAULT_AMOUNT)
            return
        pot = await asyncio.to_thread(gig.set_pot, int(amount), self.gig_path)
        await self._reply(interaction, S.OK_VAULT_SET.format(pot=pot))

    async def _cmd_rules(self, interaction: discord.Interaction, _, amount=None):
        batches = embeds.chunk_embeds(embeds.rules_sections())
        for i, batch in enumerate(batches):
            if i == 0 and not interaction.response.is_done():
                await interaction.response.send_message(embeds=batch, ephemeral=True)
            else:
                await interaction.followup.send(embeds=batch, ephemeral=True)

    # ------------------------------------------------------------------ 준비 단계
    async def _open_prep(self, ch):
        st = self.state
        msg = await ch.send(embed=embeds.prep(st), view=PrepView())
        st.prep_message_id = msg.id
        self._save()

    async def _refresh_prep_message(self, strip: bool = False):
        st = self.state
        if not st or not st.prep_message_id:
            return
        ch = await self._channel(st.channel_id)
        if not ch:
            return
        try:
            msg = await ch.fetch_message(st.prep_message_id)
            if strip:
                await msg.edit(embed=embeds.prep(st), view=None)
            else:
                await msg.edit(embed=embeds.prep(st))
        except (discord.HTTPException, AttributeError, LookupError):
            pass

    async def _strip_prep_view(self):
        st = self.state
        if st and st.prep_message_id:
            await self._refresh_prep_message(strip=True)

    async def _finish_prep(self, ch):
        """lock 보유 상태에서 호출. 준비 단계를 닫고 (기습 보고 →) 1라운드 메시지를 연다."""
        st = self.state
        if st is None or st.status != E.PREP:
            return
        gig.finish_prep(st, self.rng)
        self._save()
        await self._strip_prep_view()
        await self._begin_messages(ch)

    async def _begin_messages(self, ch):
        """전투가 열린 직후: 밀린 보고(기습) 게시 → 첫 턴 메시지."""
        await self._flush_reports(ch)
        await self._open_turn(ch)

    async def _flush_reports(self, ch):
        st = self.state
        for rep in list(st.pending_reports):
            await self._post_report(ch, rep)
        st.pending_reports = []
        self._save()

    async def handle_prep(self, interaction: discord.Interaction, action: str):
        uid = str(interaction.user.id)
        public: Optional[str] = None
        all_ready = False
        async with self.lock:
            st = self.state
            if st is None or st.status != E.PREP:
                await self._reply(interaction, S.ERR_NOT_PREP)
                return
            if interaction.message is not None and interaction.message.id != st.prep_message_id:
                await self._reply(interaction, S.ERR_STALE_MESSAGE)
                return
            if uid not in st.players:
                await self._reply(interaction, S.ERR_NOT_IN_GAME)
                return
            try:
                if action == "ready":
                    text = S.OK_PREP_READY if gig.prep_ready(st, uid) else S.OK_PREP_ALREADY
                else:
                    entry = gig.prep_upgrade(st, uid, action, self.rng)
                    text = render.prep_result_text(entry)
                    if action == "black" and entry.get("roll"):
                        public = render.prep_public_line(entry)
            except E.RuleError as e:
                await self._reply(interaction, self._rule_msg(e))
                return
            self._save()
            all_ready = gig.prep_all_ready(st)
        await self._reply(interaction, text)
        await self._refresh_prep_message()
        if public:
            ch = await self._channel(self.state.channel_id) if self.state else None
            if ch is not None:
                try:
                    await ch.send(public)
                except discord.HTTPException:
                    pass
        if all_ready:
            async with self.lock:
                st = self.state
                if st is not None and st.status == E.PREP:
                    ch = await self._channel(st.channel_id)
                    if ch is not None:
                        await self._finish_prep(ch)
            await self._resolve_round()

    # ------------------------------------------------------------------ 턴 메시지
    def _turn_embed(self) -> discord.Embed:
        st = self.state
        emb = embeds.escape_turn(st) if st.status == E.ESCAPE else embeds.turn(st)
        if st.status != E.ESCAPE and self._image_mode():
            emb.set_image(url=f"attachment://{BOARD_FILENAME}")
        return emb

    async def _open_turn(self, ch, report: Optional[Dict[str, Any]] = None):
        """ROUND_OPEN 이면 (코치 →) 카드 턴 메시지(현황판 PNG 첨부), ESCAPE 면 도주 메시지."""
        st = self.state
        if st.status == E.ESCAPE:
            msg = await ch.send(embed=embeds.escape_turn(st), view=EscapeView())
        else:
            if st.has("tutorial") and st.script:
                coach = embeds.coach(st)
                if coach.description:
                    await ch.send(embed=coach)
            kw: Dict[str, Any] = {"embed": self._turn_embed(), "view": TurnView()}
            f = self._board_file(report)
            if f is not None:
                kw["file"] = f
            else:
                kw["embed"] = embeds.turn(st)
            msg = await ch.send(**kw)
        st.turn_message_id = msg.id
        self._save()

    async def _refresh_turn_message(self):
        st = self.state
        if not st or not st.turn_message_id:
            return
        ch = await self._channel(st.channel_id)
        if not ch:
            return
        try:
            msg = await ch.fetch_message(st.turn_message_id)
            await msg.edit(embed=self._turn_embed())   # view·attachments 를 주지 않으면 그대로 유지, "(수정됨)" 도 안 붙음
        except (discord.HTTPException, AttributeError, LookupError):
            pass

    async def _strip_turn_view(self):
        st = self.state
        if not st or not st.turn_message_id or st.status in (E.LOBBY, E.PREP):
            return
        ch = await self._channel(st.channel_id)
        if not ch:
            return
        try:
            msg = await ch.fetch_message(st.turn_message_id)
            await msg.edit(embed=self._turn_embed(), view=None)
        except (discord.HTTPException, AttributeError, LookupError):
            pass

    async def _post_report(self, ch, report: Dict[str, Any]):
        if "escape" in report:
            await ch.send(embed=embeds.escape_report(report["escape"]))
            return
        for emb in embeds.report(self.state, report):
            await ch.send(embed=emb)

    async def _resolve_round(self, force: bool = False) -> bool:
        """라운드 정산 → 다음 라운드(또는 도주/종료). 전원 선택이 아니고 force 도 아니면 아무것도 안 한다."""
        async with self.lock:
            st = self.state
            if st is None or st.status != E.ROUND_OPEN:
                return False
            if not force and not E.all_chosen(st):
                return False
            ch = await self._channel(st.channel_id)
            if ch is None:
                return False
            for _ in range(MAX_AUTO_RESOLVE):
                await self._strip_turn_view()
                st.turn_message_id = 0
                rep = E.resolve_round(st, self.rng)
                st.pending_reports = [rep]
                self._save()
                await self._post_report(ch, rep)
                st.pending_reports = []
                self._save()
                await self._after_round(ch, rep)
                if st.status != E.ROUND_OPEN or not E.all_chosen(st):
                    break   # 선택을 기다리거나 끝났다 (all_chosen 참 = 행동 가능 인원 0 → 자동 진행)
            return True

    async def _after_round(self, ch, rep: Optional[Dict[str, Any]] = None):
        """lock 보유 상태에서 호출. 정산 결과에 따라 종료·도주·다음 라운드 메시지를 연다."""
        st = self.state
        if st.status == E.ENDED:
            try:
                await asyncio.to_thread(gig.apply_payout, st, self.gig_path)
            except Exception:  # noqa: BLE001
                logger.exception("보수 지급 실패")
            self._save()
            await ch.send(embed=embeds.winner(st))
            return
        if st.status == E.ESCAPE:
            if not st.escape.get("history"):
                await ch.send(embed=embeds.ncpd_trigger(st))
            await self._open_turn(ch)
            return
        delay = float(st.config.get("report_delay_sec", 1.0))
        if delay > 0:
            await asyncio.sleep(delay)
        await self._open_turn(ch, rep)

    async def _resolve_escape(self, force: bool = False) -> bool:
        async with self.lock:
            st = self.state
            if st is None or st.status != E.ESCAPE:
                return False
            if not force and not E.all_escape_chosen(st):
                return False
            ch = await self._channel(st.channel_id)
            if ch is None:
                return False
            await self._strip_turn_view()
            st.turn_message_id = 0
            rep = E.resolve_escape(st, self.rng)
            st.pending_reports = [{"escape": rep}]
            self._save()
            await self._post_report(ch, {"escape": rep})
            st.pending_reports = []
            self._save()
            await self._after_round(ch)
            return True

    async def _resume(self):
        """재시작 복원: 게시 못 한 보고 → 턴/도주/준비 메시지. 끝났는데 보수를 못 줬으면 지급."""
        try:
            async with self.lock:
                st = self.state
                if st is None:
                    return
                ch = await self._channel(st.channel_id)
                if ch is None:
                    return
                if st.status == E.ENDED:
                    if not isinstance(st.meters.get("payout"), dict):
                        gig.apply_payout(st, self.gig_path)
                        self._save()
                    return
                for rep in list(st.pending_reports):
                    await self._post_report(ch, rep)
                st.pending_reports = []
                if st.status == E.PREP and not st.prep_message_id:
                    await self._open_prep(ch)
                if st.status == E.RESOLVING:   # 정산 도중 죽은 경우: 선택을 다시 받는다
                    st.status = E.ROUND_OPEN
                    st.turn_message_id = 0
                if st.status in (E.ROUND_OPEN, E.ESCAPE) and not st.turn_message_id:
                    await self._open_turn(ch)
                self._save()
            await self._resolve_round()
            await self._resolve_escape()
        except Exception:  # noqa: BLE001
            logger.exception("카드 전투 복원 실패")

    # ------------------------------------------------------------------ 버튼/드롭다운
    def _turn_guard(self, interaction: discord.Interaction, uid: str, check_message: bool = False,
                    want: str = E.ROUND_OPEN) -> Optional[str]:
        st = self.state
        if st is None or st.status == E.ENDED or st.status == E.LOBBY:
            return S.ERR_NO_SESSION
        if st.status != want:
            return S.ERR_NOT_ESCAPE if want == E.ESCAPE else S.ERR_NOT_ALLY_TURN
        if check_message and interaction.message is not None and interaction.message.id != st.turn_message_id:
            return S.ERR_STALE_MESSAGE
        p = st.players.get(uid)
        if p is None:
            return S.ERR_NOT_IN_GAME
        if not p.alive():
            return S.RULE_ERRORS["DEAD"]
        if want == E.ROUND_OPEN and E.is_stunned(p):
            return S.RULE_ERRORS["STUNNED"]
        return None

    async def handle_draw(self, interaction: discord.Interaction):
        uid = str(interaction.user.id)
        async with self.lock:
            err = self._turn_guard(interaction, uid, check_message=True)
            if err:
                await self._reply(interaction, err)
                return
            st = self.state
            hand = E.draw_hand(st, uid, self.rng)
            self._save()
        await interaction.response.send_message(embed=embeds.hand(st, uid, hand), view=HandView(st, uid, hand, 0),
                                                ephemeral=True)

    async def handle_hand_page(self, interaction: discord.Interaction, uid: str, page: int):
        err = self._turn_guard(interaction, uid)
        if err:
            await self._reply(interaction, err)
            return
        st = self.state
        hand = E.hand_for(st, uid)
        await interaction.response.edit_message(content=None, embed=embeds.hand(st, uid, hand),
                                                view=HandView(st, uid, hand, page))

    async def handle_card_pick(self, interaction: discord.Interaction, uid: str, card_id: str):
        err = self._turn_guard(interaction, uid)
        if err:
            await self._reply(interaction, err)
            return
        st = self.state
        card = st.cards.get(card_id)
        if card is None or card_id not in E.hand_for(st, uid):
            await self._reply(interaction, S.RULE_ERRORS["NOT_IN_HAND"])
            return
        targets = E.valid_targets(st, st.players[uid], card)
        if E.needs_target(card) and len(targets) > 1:
            await interaction.response.edit_message(content=S.TARGET_TITLE.format(card=card.name), embed=None,
                                                    view=TargetView(st, uid, card_id, targets))
            return
        await self._submit(interaction, uid, card_id, None)

    async def handle_target_pick(self, interaction: discord.Interaction, uid: str, card_id: str, target_id: str):
        await self._submit(interaction, uid, card_id, target_id)

    async def _submit(self, interaction: discord.Interaction, uid: str, card_id: str, target_id: Optional[str]):
        async with self.lock:
            err = self._turn_guard(interaction, uid)
            if err:
                await self._reply(interaction, err)
                return
            st = self.state
            try:
                ch = E.submit_choice(st, uid, card_id, target_id)
            except E.RuleError as e:
                await self._reply(interaction, self._rule_msg(e))
                return
            self._save()
            card = st.cards[ch.card_id]
            tgt = st.unit(ch.target_id) if ch.target_id else None
            tail = f" → {tgt.name}" if tgt is not None and tgt.id != uid else ""
            text = S.OK_CHOSEN.format(card=card.name, target=tail)
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True, view=ChosenView(uid))
        else:
            await interaction.response.edit_message(content=text, embed=None, view=ChosenView(uid))
        await self._refresh_turn_message()
        await self._resolve_round()

    async def handle_cancel(self, interaction: discord.Interaction, uid: str):
        async with self.lock:
            err = self._turn_guard(interaction, uid)
            if err:
                await self._reply(interaction, err)
                return
            st = self.state
            try:
                E.cancel_choice(st, uid)
            except E.RuleError as e:
                await self._reply(interaction, self._rule_msg(e))
                return
            self._save()
            hand = E.hand_for(st, uid)
        await interaction.response.edit_message(content=S.OK_CANCELLED, embed=embeds.hand(st, uid, hand),
                                                view=HandView(st, uid, hand, 0))
        await self._refresh_turn_message()

    # -- 방화벽 --------------------------------------------------------------
    async def handle_firewall_menu(self, interaction: discord.Interaction):
        uid = str(interaction.user.id)
        err = self._turn_guard(interaction, uid, check_message=True)
        if err and err != S.RULE_ERRORS["STUNNED"]:    # 기절한 사람도 게이지는 쓸 수 있다 (재부팅)
            await self._reply(interaction, err)
            return
        st = self.state
        await interaction.response.send_message(embed=embeds.firewall_menu(st), view=FirewallView(st, uid), ephemeral=True)

    async def handle_firewall_pick(self, interaction: discord.Interaction, uid: str, option: str):
        async with self.lock:
            st = self.state
            if st is None or st.status != E.ROUND_OPEN:
                await self._reply(interaction, S.ERR_NOT_ALLY_TURN)
                return
            try:
                ev = E.use_firewall(st, uid, option, self.rng)
            except E.RuleError as e:
                await self._reply(interaction, self._rule_msg(e))
                return
            self._save()
            text = S.OK_FIREWALL.format(option=S.FIREWALL_OPTION_KO.get(option, option), cost=ev["cost"], value=ev["value"])
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.edit_message(content=text, embed=None, view=None)
        await self._refresh_turn_message()
        await self._resolve_round()

    # -- 도주 단계 -------------------------------------------------------------
    async def handle_escape(self, interaction: discord.Interaction, action: str):
        uid = str(interaction.user.id)
        async with self.lock:
            err = self._turn_guard(interaction, uid, check_message=True, want=E.ESCAPE)
            if err:
                await self._reply(interaction, err)
                return
            st = self.state
            try:
                E.submit_escape(st, uid, action)
            except E.RuleError as e:
                await self._reply(interaction, self._rule_msg(e))
                return
            self._save()
        await self._reply(interaction, S.OK_ESCAPE_CHOSEN.format(action=S.ESCAPE_ACTION_KO.get(action, action)))
        await self._refresh_turn_message()
        await self._resolve_escape()


async def setup(bot: commands.Bot):
    await bot.add_cog(CardBattleCog(bot))
