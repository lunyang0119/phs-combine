"""상태 → discord.Embed. 문장은 render.py 가 만들고 여기서는 배치와 색만 정한다.

턴 메시지는 content 없이 embed 만 편집하므로 Discord 의 "(수정됨)" 표시가 붙지 않는다.
현황판 PNG(image_render)가 있으면 정산 임베드의 HP 표 대신 그림이 붙는다.
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
COLOR_ROUND = 0x9B59B6    # purple (양편 혼합 정산)
COLOR_TURN = 0x3498DB     # blue
COLOR_WIN = 0xF1C40F      # gold
COLOR_LOSE = 0x2C2F33     # dark
COLOR_HAND = 0x9B59B6     # purple
COLOR_ALERT = 0xFF0055    # NCPD

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
    job = render.job_label(u)
    name = f"**{u.name}**" + (f" ({job})" if job else "")
    if hp <= 0:
        return f"💀 ~~{u.name}~~" + (" 💔" if u.looted else "")
    tags = "".join(S.TAG.get(m.kind, "") for m in u.modifiers)
    tag_s = f" {tags}" if tags else ""
    return f"{name}{tag_s}\n`{hp_bar(hp, max_hp)}` {hp}/{max_hp}"


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


def _meters_field(e: discord.Embed, st: E.BattleState) -> None:
    rows = render.meters_text(st)
    if rows:
        e.add_field(name="게이지", value="\n".join(rows)[:FIELD_LIMIT], inline=False)


def _intents_field(e: discord.Embed, st: E.BattleState) -> None:
    rows = [render.intent_text(it) for it in E.public_intents(st)]
    if rows:
        e.add_field(name="적 의도 (종류만 공개)", value="\n".join(rows)[:FIELD_LIMIT], inline=False)


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
    rules = []
    if st.has("nonlethal"):
        rules.append(f"비살상: 남은 HP 를 {st.config.get('overkill_margin', 8)} 이상 초과해 쓰러뜨리면 과잉 제압")
    if st.has("ncpd"):
        rules.append(f"NCPD 경계도 {st.config.get('ncpd_max', 3):g} 에 닿으면 드론 출동 → 도주 단계")
    if st.has("drag"):
        rules.append(f"쓰러진 아군은 {st.config.get('drag_turns', 2)}라운드 안에 끌고 가는 적을 제압하지 않으면 약탈당함")
    if st.has("doll"):
        rules.append("돌의 인격 칩: 해킹 명중마다 안정도 −1 (리부트 중 ×2). 0 이면 비살상 승리. 물리 공격은 50% 회피")
    if st.has("terminal"):
        h = st.holder()
        rules.append(f"단말기 소지자({h.name if h else '-'})가 돌의 표적. 소지자가 맞으면 액정 −1. '단말기 넘기기'로 교체")
    rules.append("보느와의 방화벽: 맞거나 방어하면 차는 팀 게이지. 턴 메시지의 버튼으로 라운드에 한 번")
    e.add_field(name="규칙", value="\n".join(f"• {r}" for r in rules)[:FIELD_LIMIT], inline=False)
    e.set_footer(text="라운드마다 적의 행동 종류가 먼저 공개됩니다. 해킹 → 방어 → 공격 순, 같은 단계는 민첩 순.")
    return e


def _marks(st: E.BattleState) -> str:
    return "\n".join(f"{render.mark_for(st, p)} {p.name}" for p in st.players.values()) or "-"


def turn(st: E.BattleState) -> discord.Embed:
    done, total = render.progress(st)
    e = discord.Embed(title=f"🃏 {st.round_no()}라운드", color=COLOR_TURN,
                      description="아래 **카드 뽑기** 버튼으로 패를 확인하고 카드를 고르세요.\n"
                                  "전원이 고르면 양편이 함께 정산됩니다.")
    _intents_field(e, st)
    e.add_field(name=f"선택 현황 {done}/{total} 완료", value=_marks(st)[:FIELD_LIMIT], inline=False)
    _meters_field(e, st)
    _board_fields(e, st)
    return e


def report(st: E.BattleState, rep: Dict[str, Any], with_board: bool = True) -> List[discord.Embed]:
    """라운드 보고 → 임베드 목록 (설명이 길면 나눈다). with_board 면 마지막 임베드에 HP 표."""
    rnd = rep.get("round_no", 0)
    title = f"⚔️ {rnd}라운드 정산"
    lines: List[str] = []
    for line in rep["lines"]:
        prefix = "🟢 " if line.get("side") == E.ALLY else "🔴 "
        for i, text in enumerate(render.log_lines(line)):
            lines.append((prefix + text) if i == 0 else "　" + text)
    ev = render.event_lines(rep.get("events") or [])
    if ev:
        lines.append("")
        lines.extend(ev)
    if not lines:
        lines = ["(행동 없음)"]
    parts = render.chunks("\n".join(lines), DESC_LIMIT)
    out = []
    for i, c in enumerate(parts):
        out.append(discord.Embed(title=title if i == 0 else f"{title} (계속)", color=COLOR_ROUND, description=c))
    if with_board:
        _meters_field(out[-1], st)
        _board_fields(out[-1], st, rep.get("hp"))
    return out


def ncpd_trigger(st: E.BattleState) -> discord.Embed:
    es = st.escape
    e = discord.Embed(title="🚨 NCPD 드론 출동!", color=COLOR_ALERT,
                      description=S.NCPD_TRIGGER.split("\n", 1)[1].format(turns=es.get("max_turns", 5),
                                                                       alert=es.get("alert", 0)))
    return e


def escape_turn(st: E.BattleState) -> discord.Embed:
    es = st.escape
    done, total = render.progress(st)
    e = discord.Embed(title=f"🚨 도주 {es.get('turn', 1)}/{es.get('max_turns', 5)}턴 — 경계도 {es.get('alert', 0)}/{es.get('alert_max', 0)}",
                      color=COLOR_ALERT,
                      description="아래 버튼으로 행동을 고르세요. **과반수**가 경찰 행동을 이기면 경계도가 1 내려갑니다.\n"
                                  + S.ESCAPE_RULE + "\n\n" + render.escape_hint_text(st))
    e.add_field(name=f"선택 현황 {done}/{total}", value=_marks(st)[:FIELD_LIMIT], inline=False)
    return e


def escape_report(rep: Dict[str, Any]) -> discord.Embed:
    lines = render.escape_report_lines(rep)
    title = lines[0].replace("### ", "")
    e = discord.Embed(title=title, color=COLOR_ALERT, description="\n".join(lines[1:])[:DESC_LIMIT])
    return e


def last_round(st: E.BattleState) -> int:
    for rep in reversed(st.log):
        if "round_no" in rep:
            return int(rep["round_no"])
    return st.round_no()


def winner(st: E.BattleState) -> discord.Embed:
    good = st.winner in (E.ALLY, E.ESCAPED)
    title = render.winner_title(st).replace("## ", "")
    e = discord.Embed(title=title, color=COLOR_WIN if good else COLOR_LOSE,
                      description=f"{st.battle_name or st.battle_id} · {last_round(st)}라운드")
    looted = [p.name for p in st.players.values() if p.looted]
    if looted:
        e.add_field(name="약탈당함", value=", ".join(looted), inline=False)
    if st.has("terminal"):
        e.add_field(name="단말기 액정", value=f"{st.meters.get('screen', 0)}/{st.meters.get('screen_max', 0)}", inline=True)
    if st.has("ncpd"):
        e.add_field(name="NCPD 경계도", value=f"{float(st.meters.get('ncpd', 0)):g}/{float(st.config.get('ncpd_max', 3)):g}", inline=True)
    _board_fields(e, st)
    return e


def status(st: E.BattleState) -> discord.Embed:
    done, total = render.progress(st)
    e = discord.Embed(title=f"📋 현황 — {st.round_no()}라운드", color=COLOR_TURN,
                      description=f"상태 `{st.status}` · 선택 현황 {done}/{total}")
    if st.status in (E.ROUND_OPEN, E.ESCAPE):
        e.add_field(name="선택", value=_marks(st)[:FIELD_LIMIT], inline=False)
    if st.status == E.ROUND_OPEN:
        _intents_field(e, st)
    _meters_field(e, st)
    _board_fields(e, st)
    return e


def hand(st: E.BattleState, uid: str, hand_ids: List[str]) -> discord.Embed:
    p = st.players.get(uid)
    admin = p is not None and E.admin_sees_all(st, p)
    cards = [st.cards[c] for c in hand_ids if c in st.cards]
    e = discord.Embed(title=f"🃏 {st.round_no()}라운드 — 당신의 패" + (S.HAND_ADMIN if admin else ""),
                      color=COLOR_HAND, description="아래 드롭다운에서 카드를 고르세요.")
    if p is not None and E.is_grappled(p):
        e.description = "🪢 붙잡힌 상태입니다. **떨쳐내기**만 쓸 수 있습니다 (아군이 돌을 맞혀도 풀립니다)."
    if len(cards) <= 10:
        for c in cards:
            e.add_field(name=f"{c.name}  ·  {S.KIND_KO.get(c.kind, c.kind)} / {S.TARGET_KO.get(c.target, c.target)}",
                        value=render.card_detail(st, c, p)[:FIELD_LIMIT], inline=False)
    else:
        text = "\n".join(f"• **{c.name}** [{S.KIND_KO.get(c.kind, c.kind)}·{S.TARGET_KO.get(c.target, c.target)}] "
                         f"{render.card_detail(st, c, p).replace(chr(10), ' / ')}" for c in cards)
        e.description = (e.description + "\n" + text)[:DESC_LIMIT]
    if p is not None:
        e.set_footer(text=f"{p.name} · HP {p.hp}/{p.max_hp}")
    return e


def firewall_menu(st: E.BattleState) -> discord.Embed:
    e = discord.Embed(title=S.FIREWALL_TITLE.format(value=int(st.meters.get("firewall", 0)),
                                                    max=int(st.config.get("firewall_max", 9))), color=COLOR_TURN)
    rows = []
    for opt in E.FIREWALL_OPTIONS:
        pct = int(round(float(st.config.get("firewall_cover_shield", 0.5)) * 100)) if opt == "cover" else \
            int(round(float(st.config.get("firewall_maxdoc_ratio", 0.3)) * 100))
        rows.append(f"**{S.FIREWALL_OPTION_KO[opt]}**{S.FIREWALL_COST.format(cost=E.firewall_cost(st, opt))} — "
                    f"{S.FIREWALL_OPTION_DESC[opt].format(pct=pct)}")
    e.description = "\n".join(rows)
    return e
