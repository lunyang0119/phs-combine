"""상태 → discord.Embed. 문장은 render.py 가 만들고 여기서는 배치와 색만 정한다.

턴 메시지는 content 없이 embed 만 편집하므로 Discord 의 "(수정됨)" 표시가 붙지 않는다.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

import discord

from . import engine as E
from . import render
from . import strings as S

COLOR_LOBBY = 0x5865F2    # blurple
COLOR_INTRO = 0xE67E22    # orange
COLOR_ALLY = 0x57F287     # green
COLOR_ENEMY = 0xED4245    # red
COLOR_TURN = 0x3498DB     # blue
COLOR_WIN = 0xF1C40F      # gold
COLOR_LOSE = 0x2C2F33     # dark
COLOR_HAND = 0x9B59B6     # purple

BAR_WIDTH = 10
DESC_LIMIT = 4000
FIELD_LIMIT = 1000


def hp_bar(hp: int, max_hp: int, width: int = BAR_WIDTH) -> str:
    if max_hp <= 0:
        return "░" * width
    filled = 0 if hp <= 0 else max(1, round(hp / max_hp * width))
    return "█" * filled + "░" * (width - filled)


def unit_line(u: E.Combatant, hp: int = None, max_hp: int = None) -> str:
    hp = u.hp if hp is None else hp
    max_hp = u.max_hp if max_hp is None else max_hp
    if hp <= 0:
        return f"💀 ~~{u.name}~~"
    tags = "".join(S.TAG.get(m.kind, "") for m in u.modifiers)
    tag_s = f" {tags}" if tags else ""
    return f"**{u.name}**{tag_s}\n`{hp_bar(hp, max_hp)}` {hp}/{max_hp}"


def _board_fields(e: discord.Embed, st: E.BattleState, hp: Dict[str, Any] = None) -> None:
    def lines(units: Iterable[E.Combatant]) -> str:
        out = []
        for u in units:
            if hp and u.id in hp:
                out.append(unit_line(u, int(hp[u.id][0]), int(hp[u.id][1])))
            else:
                out.append(unit_line(u))
        return "\n".join(out) or "-"
    e.add_field(name="🟢 아군", value=lines(st.players.values())[:FIELD_LIMIT], inline=True)
    e.add_field(name="🔴 적", value=lines(st.enemies.values())[:FIELD_LIMIT], inline=True)


# ---------------------------------------------------------------------------
def lobby(names: Iterable[str]) -> discord.Embed:
    names = list(names)
    e = discord.Embed(title="🃏 카드 전투 — 참가 모집 중", color=COLOR_LOBBY,
                      description="`/카드 옵션:참가` 로 참가하세요.\n주최자가 `/카드 옵션:시작 전투id:<id>` 로 시작합니다.")
    value = "\n".join(f"{i + 1}. {n}" for i, n in enumerate(names)) or S.LOBBY_EMPTY
    e.add_field(name=f"참가자 ({len(names)}명)", value=value[:FIELD_LIMIT], inline=False)
    return e


def intro(st: E.BattleState, desc: str = "") -> discord.Embed:
    e = discord.Embed(title=f"⚔️ {st.battle_name or st.battle_id}", color=COLOR_INTRO, description=desc or None)
    _board_fields(e, st)
    e.set_footer(text="아군 턴 → 적 턴을 반복합니다. 한쪽 HP 가 모두 0 이 되면 끝.")
    return e


def _marks(st: E.BattleState) -> str:
    rows = []
    for p in st.players.values():
        if not p.alive():
            m = S.MARK_OUT
        elif E.is_stunned(p):
            m = S.MARK_STUN
        elif p.id in st.choices:
            m = S.MARK_DONE
        else:
            m = S.MARK_TODO
        rows.append(f"{m} {p.name}")
    return "\n".join(rows) or "-"


def turn(st: E.BattleState) -> discord.Embed:
    done, total = render.progress(st)
    e = discord.Embed(title=f"🃏 {st.round_no()}라운드 — 아군 턴", color=COLOR_TURN,
                      description="아래 **카드 뽑기** 버튼으로 패를 확인하고 카드를 고르세요.\n"
                                  "전원이 고르면 자동으로 정산됩니다.")
    e.add_field(name=f"선택 현황 {done}/{total} 완료", value=_marks(st)[:FIELD_LIMIT], inline=False)
    _board_fields(e, st)
    return e


def reports(st: E.BattleState, report: Dict[str, Any]) -> List[discord.Embed]:
    """반턴 보고 → 임베드 목록 (설명이 길면 나눈다). 마지막 임베드에 HP 표."""
    is_ally = report["side"] == E.ALLY
    rnd = report.get("round_no", (report["turn_no"] + 1) // 2)
    title = f"{'🟢' if is_ally else '🔴'} {rnd}라운드 — {'아군' if is_ally else '적'} 행동"
    color = COLOR_ALLY if is_ally else COLOR_ENEMY
    lines: List[str] = []
    for line in report["lines"]:
        for i, text in enumerate(render.log_lines(line)):
            lines.append(("▸ " + text) if i == 0 else text)
    if not lines:
        lines = ["(행동 없음)"]
    chunks = render.chunks("\n".join(lines), DESC_LIMIT)
    out = []
    for i, c in enumerate(chunks):
        e = discord.Embed(title=title if i == 0 else f"{title} (계속)", color=color, description=c)
        out.append(e)
    _board_fields(out[-1], st, report.get("hp"))
    return out


def last_round(st: E.BattleState) -> int:
    """마지막으로 정산된 라운드 (끝난 뒤에는 turn_no 가 하나 더 올라가 있다)."""
    if st.log:
        return int(st.log[-1].get("round_no") or (int(st.log[-1]["turn_no"]) + 1) // 2)
    return st.round_no()


def winner(st: E.BattleState) -> discord.Embed:
    win = st.winner == E.ALLY
    e = discord.Embed(title=S.WIN_ALLY.replace("## ", "") if win else S.WIN_ENEMY.replace("## ", ""),
                      color=COLOR_WIN if win else COLOR_LOSE,
                      description=f"{st.battle_name or st.battle_id} · {last_round(st)}라운드")
    _board_fields(e, st)
    return e


def status(st: E.BattleState) -> discord.Embed:
    done, total = render.progress(st)
    e = discord.Embed(title=f"📋 현황 — {st.round_no()}라운드 ({S.SIDE_KO.get(st.side, st.side)} 턴)",
                      color=COLOR_TURN, description=f"상태 `{st.status}` · 선택 현황 {done}/{total}")
    if st.status == E.ALLY_TURN:
        e.add_field(name="선택", value=_marks(st)[:FIELD_LIMIT], inline=False)
    _board_fields(e, st)
    return e


def hand(st: E.BattleState, uid: str, hand_ids: List[str]) -> discord.Embed:
    p = st.players.get(uid)
    admin = p is not None and E.admin_sees_all(st, p)
    cards = [st.cards[c] for c in hand_ids if c in st.cards]
    e = discord.Embed(title=f"🃏 {st.round_no()}라운드 — 당신의 패" + (S.HAND_ADMIN if admin else ""),
                      color=COLOR_HAND, description="아래 드롭다운에서 카드를 고르세요.")
    if len(cards) <= 10:
        for c in cards:
            e.add_field(name=f"{c.name}  ·  {S.KIND_KO.get(c.kind, c.kind)} / {S.TARGET_KO.get(c.target, c.target)}",
                        value=c.desc or "-", inline=False)
    else:
        text = "\n".join(f"• **{c.name}** [{S.KIND_KO.get(c.kind, c.kind)}·{S.TARGET_KO.get(c.target, c.target)}] {c.desc}"
                         for c in cards)
        e.description = ("아래 드롭다운에서 카드를 고르세요.\n" + text)[:DESC_LIMIT]
    if p is not None:
        e.set_footer(text=f"{p.name} · HP {p.hp}/{p.max_hp}")
    return e
