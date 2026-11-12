"""상태 → discord.Embed. 문장은 render.py 가 만들고 여기서는 배치와 색만 정한다.

턴 메시지는 content 없이 embed 만 편집하므로 Discord 의 "(수정됨)" 표시가 붙지 않는다.
현황판 PNG(image_render)가 있으면 정산 임베드의 HP 표 대신 그림이 붙는다.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

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
COLOR_PREP = 0x1ABC9C     # teal
COLOR_COACH = 0x2ECC71    # green
COLOR_AMBUSH = 0xFF2BD6   # magenta
COLOR_BRIEF = 0x00B8D4    # cyan

BAR_WIDTH = 10
DESC_LIMIT = 4000
FIELD_LIMIT = 1000
MESSAGE_LIMIT = 5800      # 한 메시지의 임베드 글자 합 (Discord 6000)


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
    if u.weapon_grade > 0:
        name += f" 🔫mk.{u.weapon_grade}"
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
    if rows:                                   # 첫 줄(방화벽)은 HP 처럼 막대로
        fmax = int(st.config.get("firewall_max", 9))
        fval = int(st.meters.get("firewall", 0))
        rows[0] = S.METER_FIREWALL_BAR.format(bar=hp_bar(fval, fmax), value=fval, max=fmax) \
            + (S.METER_FIREWALL_LOCKED if E.firewall_locked(st) else "")
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
    rules = render.intro_rules(st)
    e.add_field(name="규칙", value="\n".join(f"• {r}" for r in rules)[:FIELD_LIMIT], inline=False)
    special = [t for t, _ in render.briefing_sections(st)]
    if special and bool(st.config.get("briefing", True)):
        e.add_field(name=S.INTRO_SPECIAL_FIELD, value=S.INTRO_SPECIAL.format(names=" · ".join(special))[:FIELD_LIMIT],
                    inline=False)
    e.set_footer(text=S.INTRO_FOOTER)
    return e


def briefing(st: E.BattleState) -> Optional[discord.Embed]:
    """1라운드 직전에 한 번: 이 전투에 붙은 특수 규칙을 기믹마다 한 칸씩 자세히."""
    sections = render.briefing_sections(st)
    if not sections:
        return None
    e = discord.Embed(title=S.BRIEFING_TITLE, color=COLOR_BRIEF, description=S.BRIEFING_DESC)
    for title, body in sections:
        e.add_field(name=title, value=body[:FIELD_LIMIT + 24], inline=False)
    return e


def flash(st: E.BattleState) -> List[discord.Embed]:
    """이번 라운드에 터진 플래시백: 사람마다 임베드 하나 (인물 · 대사 · 굴림 · 효과)."""
    out = []
    for f in render.flash_entries(st):
        e = discord.Embed(title=f["title"], color=f["color"], description="\n".join(f["lines"])[:DESC_LIMIT] or None)
        e.set_author(name=f["player"])
        e.add_field(name="굴림", value=f["roll"], inline=False)
        e.add_field(name="효과", value=f"**{f['player']}** — {f['effect']}"[:FIELD_LIMIT], inline=False)
        out.append(e)
    return out


def _marks(st: E.BattleState) -> str:
    return "\n".join(f"{render.mark_for(st, p)} {p.name}" for p in st.players.values()) or "-"


def turn(st: E.BattleState, with_board: bool = True) -> discord.Embed:
    """턴 메시지. 현황판 PNG 를 붙일 때는 with_board=False 로 글자 HP 표를 뺀다 (그림과 겹쳐 보기 어렵다)."""
    done, total = render.progress(st)
    e = discord.Embed(title=f"🃏 {st.round_no()}라운드", color=COLOR_TURN, description=S.TURN_DESC)
    _intents_field(e, st)
    e.add_field(name=f"선택 현황 {done}/{total} 완료", value=_marks(st)[:FIELD_LIMIT], inline=False)
    _meters_field(e, st)
    if with_board:
        _board_fields(e, st)
    return e


def coach(st: E.BattleState) -> discord.Embed:
    from . import tutorial  # 순환 import 회피
    return discord.Embed(title=S.COACH_TITLE.format(round=st.round_no()), color=COLOR_COACH,
                         description=tutorial.coach_text(st)[:DESC_LIMIT])


def report(st: E.BattleState, rep: Dict[str, Any], with_board: bool = True) -> List[discord.Embed]:
    """라운드 보고 → 임베드 목록 (설명이 길면 나눈다). with_board 면 마지막 임베드에 HP 표."""
    rnd = rep.get("round_no", 0)
    ambush = bool(rep.get("ambush"))
    title = S.AMBUSH_TITLE if ambush else f"⚔️ {rnd}라운드 정산"
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
        out.append(discord.Embed(title=title if i == 0 else f"{title} (계속)", color=COLOR_AMBUSH if ambush else COLOR_ROUND,
                                 description=c))
    if with_board:
        _meters_field(out[-1], st)
        _board_fields(out[-1], st, rep.get("hp"))
    # 💡 팁과 튜토리얼 피드백은 현황판 아래, 별도 임베드로 (눈에 띄게)
    extra = render.tip_lines(rep.get("tips") or [])
    tl = render.tutorial_line(rep.get("tutorial"))
    if tl:
        extra.append(tl)
    if extra:
        out.append(discord.Embed(title=S.TIPS_TITLE, color=COLOR_COACH, description="\n".join(extra)[:DESC_LIMIT]))
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
    if st.has("life_support") and st.meters.get("boss_state"):
        e.add_field(name=S.BOSS_STATE_FIELD, value=S.BOSS_STATE_KO.get(str(st.meters["boss_state"]), "?"), inline=True)
        e.add_field(name="생명 유지", value=f"{st.meters.get('life', 0)}/{st.meters.get('life_max', 0)}", inline=True)
    looted = [p.name for p in st.players.values() if p.looted]
    if looted:
        e.add_field(name="약탈당함", value=", ".join(looted), inline=False)
    if st.has("terminal"):
        e.add_field(name="단말기 액정", value=f"{st.meters.get('screen', 0)}/{st.meters.get('screen_max', 0)}", inline=True)
    if st.has("ncpd"):
        e.add_field(name="NCPD 경계도", value=f"{float(st.meters.get('ncpd', 0)):g}/{float(st.config.get('ncpd_max', 3)):g}", inline=True)
    pay = render.payout_text(st)
    if pay:
        e.add_field(name=S.PAYOUT_FIELD, value=pay[:FIELD_LIMIT], inline=False)
    weapons = [f"{p.name}: {E.weapon_label(p)}" for p in st.players.values() if p.weapon_grade > 0]
    if weapons:
        e.add_field(name=S.WEAPONS_FIELD, value="\n".join(weapons)[:FIELD_LIMIT], inline=False)
    _board_fields(e, st)
    return e


def resolved(st: E.BattleState) -> discord.Embed:
    """결과 임베드 뒤에 따로 올리는 초록 배너: 전투가 확실히 끝났음을 알린다."""
    desc = S.END_DESC_TUTORIAL if st.end_reason == "tutorial" else S.END_DESC
    return discord.Embed(title=S.END_TITLE, color=COLOR_ALLY, description=desc)


def prep(st: E.BattleState) -> discord.Embed:
    cfg = st.config
    pr = st.prep
    e = discord.Embed(title=S.PREP_TITLE, color=COLOR_PREP,
                      description=S.PREP_DESC.format(price_normal=int(cfg.get("price_normal", 100)),
                                                     price_black=int(cfg.get("price_black", 50)),
                                                     normal_max=int(cfg.get("weapon_normal_max", 3)),
                                                     wmax=int(cfg.get("weapon_max", 4)),
                                                     actions=int(cfg.get("prep_actions", 2))))
    e.add_field(name=S.PREP_POT_FIELD,
                value=S.PREP_POT_VALUE.format(pot_start=int(pr.get("pot_start", 0)), share=int(pr.get("share", 0)),
                                              pot=int(st.meters.get("pot", 0)), field_cost=int(cfg.get("field_mod_cost", 50))),
                inline=False)
    e.add_field(name=S.PREP_PLAYERS_FIELD, value="\n".join(render.prep_rows(st))[:FIELD_LIMIT] or "-", inline=False)
    rolls = [render.prep_roll_line(r) for r in (pr.get("rolls") or [])[-12:]]
    if rolls:
        e.add_field(name=S.PREP_ROLLS_FIELD, value="\n".join(rolls)[:FIELD_LIMIT], inline=False)
    if pr.get("done") and pr.get("returned"):
        e.set_footer(text=S.PREP_RETURNED.format(returned=int(pr.get("returned", 0))))
    return e


def status(st: E.BattleState) -> discord.Embed:
    if st.status == E.PREP:
        return prep(st)
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
        foot = f"{p.name} · HP {p.hp}/{p.max_hp}"
        if p.weapon_grade > 0:
            foot += f" · {E.weapon_label(p)}"
        e.set_footer(text=foot)
    return e


def firewall_menu(st: E.BattleState) -> discord.Embed:
    e = discord.Embed(title=S.FIREWALL_TITLE.format(value=int(st.meters.get("firewall", 0)),
                                                    max=int(st.config.get("firewall_max", 9))), color=COLOR_TURN)
    rows = []
    for opt in E.enabled_firewall_options(st):
        pct = int(round(float(st.config.get("firewall_cover_shield", 0.5)) * 100)) if opt == "cover" else \
            int(round(float(st.config.get("firewall_maxdoc_ratio", 0.3)) * 100))
        rows.append(f"**{S.FIREWALL_OPTION_KO[opt]}**{S.FIREWALL_COST.format(cost=E.firewall_cost(st, opt))} — "
                    f"{S.FIREWALL_OPTION_DESC[opt].format(pct=pct)}")
    if E.firewall_locked(st):
        rows.append(S.FIREWALL_LOCKED_NOTE)
    e.description = "\n".join(rows)
    return e


def rules_sections() -> List[discord.Embed]:
    out = []
    for i, (title, body) in enumerate(S.HELP_SECTIONS):
        out.append(discord.Embed(title=f"📖 {title}", color=COLOR_TURN if i % 2 == 0 else COLOR_HAND,
                                 description=body[:DESC_LIMIT]))
    return out


def chunk_embeds(embs: List[discord.Embed], limit: int = MESSAGE_LIMIT, max_per: int = 10) -> List[List[discord.Embed]]:
    """한 메시지의 임베드 수(10)와 글자 합(6000) 제한에 맞춰 묶는다."""
    batches: List[List[discord.Embed]] = []
    cur: List[discord.Embed] = []
    size = 0
    for e in embs:
        n = len(e.title or "") + len(e.description or "")
        if cur and (size + n > limit or len(cur) >= max_per):
            batches.append(cur)
            cur, size = [], 0
        cur.append(e)
        size += n
    if cur:
        batches.append(cur)
    return batches
