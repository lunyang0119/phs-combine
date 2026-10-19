"""카드 전투 Cog — `/카드` 단일 명령 + 턴 메시지 버튼.

세션은 전체에서 하나(`self.state`). 상태 변경은 전부 `self.lock` 안에서 하고 `_save()` 한다.
흐름: 주최(LOBBY) → 참가(로비 메시지 편집) → 시작(ALLY_TURN, 턴 메시지 + 카드 뽑기 버튼)
      → 전원 선택 또는 /카드 턴종료 → 아군 정산 게시 → 적 턴 정산 게시 → 다음 턴 메시지 … → ENDED
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
from . import store
from . import strings as S
from .views import ChosenView, HandView, TargetView, TurnView

logger = logging.getLogger(__name__)

OPTION_CHOICES = [
    app_commands.Choice(name="주최", value="host"),
    app_commands.Choice(name="참가", value="join"),
    app_commands.Choice(name="시작", value="start"),
    app_commands.Choice(name="턴종료", value="force"),
    app_commands.Choice(name="종료", value="end"),
    app_commands.Choice(name="현황", value="status"),
]
MAX_AUTO_RESOLVE = 20


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
        self.admin_ids = _id_set("CARDBATTLE_ADMIN_IDS", "FUGITIVE_ADMIN_IDS")

    # ------------------------------------------------------------------ 생명주기
    async def cog_load(self):
        self.bot.add_view(TurnView())
        self.state = store.load(self.store_path)
        if self.state and self.state.status in (E.ALLY_TURN, E.ENEMY_TURN, E.RESOLVING):
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

    async def _reply(self, interaction: discord.Interaction, content: str, ephemeral: bool = True, **kw):
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
                out.append(app_commands.Choice(name=f"{b.id} — {b.name}"[:100], value=b.id))
        return out[:25]

    @app_commands.command(name="카드", description="카드 전투 미니게임 — 주최 / 참가 / 시작 / 턴종료 / 종료 / 현황")
    @app_commands.describe(옵션="실행할 동작", 전투id="시작할 전투 ID (옵션:시작 때 필요)", 도움말=help_option.HELP_OPTION_DESC)
    @app_commands.choices(옵션=OPTION_CHOICES)
    @app_commands.autocomplete(전투id=_battle_autocomplete)
    async def card(self, interaction: discord.Interaction,
                   옵션: Optional[app_commands.Choice[str]] = None,
                   전투id: Optional[str] = None,
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
        await handler(interaction, 전투id)

    async def _cmd_host(self, interaction: discord.Interaction, _):
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

    async def _cmd_join(self, interaction: discord.Interaction, _):
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

    async def _cmd_start(self, interaction: discord.Interaction, battle_id: Optional[str]):
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
            try:
                new = E.new_battle(list(st.players.values()), E.enemy_combatants(bdef, gd.enemies),
                                   gd.cards, st.config, host_id=st.host_id, battle_id=bdef.id, battle_name=bdef.name)
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
            await self._open_turn(ch)
        await self._reply(interaction, S.OK_STARTED.format(battle=bdef.name))
        await self._resolve_turn()   # 행동 가능한 참가자가 없으면 바로 넘어간다

    async def _cmd_force(self, interaction: discord.Interaction, _):
        st = self.state
        if st is None or st.status == E.ENDED:
            await self._reply(interaction, S.ERR_NO_SESSION)
            return
        if not (self._is_host(interaction) or self._is_admin(interaction)):
            await self._reply(interaction, S.ERR_HOST_ONLY)
            return
        if st.status != E.ALLY_TURN:
            await self._reply(interaction, S.ERR_NOT_ALLY_TURN)
            return
        await self._reply(interaction, S.OK_FORCED)
        await self._resolve_turn(force=True)

    async def _cmd_end(self, interaction: discord.Interaction, _):
        async with self.lock:
            st = self.state
            if st is None:
                await self._reply(interaction, S.ERR_NO_SESSION)
                return
            if not (self._is_host(interaction) or self._is_admin(interaction)):
                await self._reply(interaction, S.ERR_HOST_ONLY)
                return
            await self._strip_turn_view()
            self.state = None
            self._save()
        await self._reply(interaction, S.OK_ENDED)

    async def _cmd_status(self, interaction: discord.Interaction, _):
        st = self.state
        if st is None:
            await self._reply(interaction, S.ERR_NO_SESSION)
            return
        if st.status == E.LOBBY:
            await self._reply(interaction, None, embed=embeds.lobby([p.name for p in st.players.values()]))
            return
        await self._reply(interaction, None, embed=embeds.status(st))

    # ------------------------------------------------------------------ 턴 메시지
    async def _open_turn(self, ch):
        st = self.state
        msg = await ch.send(embed=embeds.turn(st), view=TurnView())
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
            await msg.edit(embed=embeds.turn(st))   # view 인자를 주지 않으면 버튼 유지, content 없으니 "(수정됨)" 도 안 붙음
        except (discord.HTTPException, AttributeError):
            pass

    async def _strip_turn_view(self):
        st = self.state
        if not st or not st.turn_message_id:
            return
        ch = await self._channel(st.channel_id)
        if not ch:
            return
        try:
            msg = await ch.fetch_message(st.turn_message_id)
            await msg.edit(embed=embeds.turn(st), view=None)
        except (discord.HTTPException, AttributeError):
            pass

    async def _post_report(self, ch, report: Dict[str, Any]):
        for emb in embeds.reports(self.state, report):
            await ch.send(embed=emb)

    async def _resolve_turn(self, force: bool = False) -> bool:
        """아군 턴 정산 → 적 턴 → 다음 턴 열기. 전원 선택이 아니고 force 도 아니면 아무것도 안 한다."""
        async with self.lock:
            st = self.state
            if st is None or st.status != E.ALLY_TURN:
                return False
            if not force and not E.all_chosen(st):
                return False
            ch = await self._channel(st.channel_id)
            if ch is None:
                return False
            for _ in range(MAX_AUTO_RESOLVE):
                await self._strip_turn_view()
                st.turn_message_id = 0
                rep = E.resolve_ally_turn(st, self.rng)
                st.pending_reports = [rep]
                self._save()
                await self._post_report(ch, rep)
                st.pending_reports = []
                self._save()
                await self._run_enemy_and_continue(ch)
                if st.status != E.ALLY_TURN or not E.all_chosen(st):
                    break   # 선택을 기다리거나 끝났다 (all_chosen 참 = 행동 가능 인원 0 → 자동 진행)
            return True

    async def _run_enemy_and_continue(self, ch):
        """lock 보유 상태에서 호출. ENEMY_TURN 이면 적 턴을 돌리고, 끝이면 결과·아니면 다음 턴을 연다."""
        st = self.state
        if st.status == E.ENEMY_TURN:
            delay = float(st.config.get("enemy_turn_delay_sec", 1.0))
            if delay > 0:
                await asyncio.sleep(delay)
            rep = E.run_enemy_turn(st, self.rng)
            st.pending_reports = [rep]
            self._save()
            await self._post_report(ch, rep)
            st.pending_reports = []
            self._save()
        if st.status == E.ENDED:
            await ch.send(embed=embeds.winner(st))
            self._save()
        elif st.status == E.ALLY_TURN:
            await self._open_turn(ch)

    async def _resume(self):
        """재시작 복원: 게시 못 한 보고 → 적 턴 → 턴 메시지."""
        try:
            async with self.lock:
                st = self.state
                if st is None:
                    return
                ch = await self._channel(st.channel_id)
                if ch is None:
                    return
                for rep in list(st.pending_reports):
                    await self._post_report(ch, rep)
                st.pending_reports = []
                if st.status == E.RESOLVING:   # 아군 정산 도중 죽은 경우: 선택을 다시 받는다
                    st.status = E.ALLY_TURN
                    st.turn_message_id = 0
                if st.status == E.ENEMY_TURN:
                    await self._run_enemy_and_continue(ch)
                elif st.status == E.ALLY_TURN and not st.turn_message_id:
                    await self._open_turn(ch)
                self._save()
            await self._resolve_turn()
        except Exception:  # noqa: BLE001
            logger.exception("카드 전투 복원 실패")

    # ------------------------------------------------------------------ 버튼/드롭다운
    def _turn_guard(self, interaction: discord.Interaction, uid: str, check_message: bool = False) -> Optional[str]:
        st = self.state
        if st is None or st.status == E.ENDED or st.status == E.LOBBY:
            return S.ERR_NO_SESSION
        if st.status != E.ALLY_TURN:
            return S.ERR_NOT_ALLY_TURN
        if check_message and interaction.message is not None and interaction.message.id != st.turn_message_id:
            return S.ERR_STALE_MESSAGE
        p = st.players.get(uid)
        if p is None:
            return S.ERR_NOT_IN_GAME
        if not p.alive():
            return S.RULE_ERRORS["DEAD"]
        if E.is_stunned(p):
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
                                                    view=TargetView(uid, card_id, targets))
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
        await self._resolve_turn()

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


async def setup(bot: commands.Bot):
    await bot.add_cog(CardBattleCog(bot))
