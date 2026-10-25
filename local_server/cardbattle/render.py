"""상태 → 텍스트. Discord 객체는 모른다."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from utils import get_korean_particle

from . import engine as E
from . import strings as S

MAX_LEN = 1900


def _josa(word: str, a: str, b: str) -> str:
    """받침 유무로 조사 선택. 한글로 끝나지 않으면(영문·숫자·기호) '이(가)' 꼴로 묶어 쓴다."""
    if word and "가" <= word[-1] <= "힣":
        return get_korean_particle(word, a, b)
    return f"{a}({b})"


def _actor(line: Dict[str, Any]) -> Dict[str, str]:
    name = line["actor_name"]
    if line.get("side") == E.ALLY:
        shown = f"**{name}**님"
        return {"actor": shown, "josa": "이", "eun": "은"}
    shown = f"**{name}**"
    return {"actor": shown, "josa": _josa(name, "이", "가"), "eun": _josa(name, "은", "는")}


def _detail(t: Dict[str, Any]) -> str:
    kind = t.get("kind", "")
    fmt = S.DETAIL.get(kind, "")
    if not fmt:
        return ""
    amount = t.get("amount", 0)
    pct = int(round(float(amount) * 100)) if kind in ("shield", "atk_down", "misfire") else 0
    turns = t.get("turns", "")
    try:
        out = fmt.format(amount=int(amount) if kind in ("damage", "self_damage", "heal") else amount,
                         pct=pct, turns=turns or "")
    except (KeyError, ValueError):
        return ""
    if t.get("weak"):
        out += S.LOG_WEAK
    if t.get("overkill"):
        out += S.LOG_OVERKILL
    if t.get("revived"):
        out += S.LOG_REVIVED
    if "screen" in t:
        out += S.LOG_SCREEN.format(screen=t["screen"])
    return out


def log_lines(line: Dict[str, Any]) -> List[str]:
    """정산 로그 한 항목 → 출력 줄들 (전체 공격은 대상별 줄)."""
    a = _actor(line)
    card = line.get("card_name", "")
    eul = _josa(card, "을", "를")
    res = line.get("result")
    if res == "wait":
        return [S.LOG_WAIT.format(**a)]
    if res == "stunned":
        return [S.LOG_STUNNED.format(**a)]
    if res == "down":
        return [S.LOG_DOWN.format(**a)]
    if res == "reboot":
        return [S.LOG_REBOOT.format(**a)]
    if res == "misfire":
        return [S.LOG_MISFIRE.format(card=card, eul=eul, **a)]
    if res == "no_target":
        return [S.LOG_NO_TARGET.format(card=card, eul=eul, **a)]
    if res == "evade":
        tname = (line.get("targets") or [{}])[0].get("name", "?")
        return [S.LOG_EVADE.format(card=card, eul=eul, target=tname, eun2=_josa(tname, "은", "는"), **a)]
    head = S.LOG_USE.format(card=card, eul=eul, **a)
    if res == "redirect":
        head = S.LOG_REDIRECT_PREFIX + head
    if line.get("combo"):
        head = S.LOG_COMBO_PREFIX + head
    tail_extra = ""
    if line.get("chip"):
        c = line["chip"]
        tail_extra += S.LOG_CHIP.format(amount=c["amount"], value=c["value"], max=c["max"])
        if c.get("capped"):
            tail_extra += S.LOG_CHIP_CAPPED
    if line.get("released"):
        tail_extra += S.LOG_RELEASED.format(names=", ".join(line["released"]))
    targets = line.get("targets") or []
    if not targets:
        return [head + tail_extra]
    out: List[str] = []
    for i, t in enumerate(targets):
        detail = _detail(t) + (S.LOG_KILLED if t.get("killed") and t.get("kind") in ("damage", "self_damage") else "")
        if t["id"] == line["actor_id"]:
            tail = S.LOG_SELF.format(hp=t["hp"], max=t["max_hp"], detail=detail)
        else:
            tail = S.LOG_TARGET.format(target=t["name"], hp=t["hp"], max=t["max_hp"], detail=detail)
        out.append((head if i == 0 else "　↳") + tail + (tail_extra if i == len(targets) - 1 else ""))
    return out


def event_line(ev: Dict[str, Any]) -> Optional[str]:
    k = ev.get("kind")
    if k == "firewall_use":
        opt = ev["option"]
        detail_fmt = S.EVENT_FIREWALL_DETAIL.get(opt, "")
        detail = detail_fmt.format(targets=", ".join(ev.get("targets") or []) or "-", amount=ev.get("amount", 0),
                                   revived=S.LOG_REVIVED if ev.get("revived") else "")
        return S.EVENT_FIREWALL_USE.format(by=ev["by"], option=S.FIREWALL_OPTION_KO.get(opt, opt),
                                           cost=ev["cost"], detail=detail)
    if k == "ncpd":
        return S.EVENT_NCPD.format(amount=ev["amount"], why=S.NCPD_WHY.get(ev["why"], ev["why"]),
                                   who=ev.get("who", ""), value=ev["value"], max=ev["max"])
    if k == "drag_start":
        return S.EVENT_DRAG_START.format(by=ev["by"], josa=_josa(ev["by"], "이", "가"), player=ev["player"], need=ev["need"])
    if k == "drag":
        return S.EVENT_DRAG.format(by=ev["by"], josa=_josa(ev["by"], "이", "가"), player=ev["player"],
                                   turns=ev["turns"], need=ev["need"])
    if k == "drag_stop":
        return S.EVENT_DRAG_STOP.format(by=ev["by"], josa=_josa(ev["by"], "이", "가"), player=ev["player"])
    if k == "looted":
        return S.EVENT_LOOTED.format(player=ev["player"])
    if k == "regen":
        return S.EVENT_REGEN.format(name=ev["name"], amount=ev["amount"], hp=ev["hp"], max_hp=ev["max_hp"])
    return None    # firewall_gain 은 게이지 표시로 충분


def event_lines(events: Iterable[Dict[str, Any]]) -> List[str]:
    out = []
    for ev in events:
        t = event_line(ev)
        if t:
            out.append(t)
    return out


# ---------------------------------------------------------------------------
def _unit_text(u: E.Combatant) -> str:
    if not u.alive():
        return S.BOARD_DEAD.format(name=u.name)
    tags = "".join(S.TAG.get(m.kind, "") for m in u.modifiers)
    return S.BOARD_UNIT.format(name=u.name, hp=u.hp, max=u.max_hp, tags=(" " + tags) if tags else "")


def hp_board(st: E.BattleState) -> str:
    ally = " | ".join(_unit_text(u) for u in st.players.values()) or "-"
    enemy = " | ".join(_unit_text(u) for u in st.enemies.values()) or "-"
    return S.BOARD_ALLY.format(units=ally) + "\n" + S.BOARD_ENEMY.format(units=enemy)


def job_label(u: E.Combatant) -> str:
    return S.JOB_KO.get(u.job, u.job) if u.job else ""


def intent_text(it: Dict[str, Any]) -> str:
    kind = it.get("kind", "")
    if kind == "mode":
        label = S.MODE_KO.get(it.get("label", ""), it.get("label", ""))
    else:
        label = S.INTENT_KO.get(kind, kind).format(label=it.get("label", ""))
    tgt = S.INTENT_TARGET.format(target=it["target"]) if it.get("target") else ""
    return S.INTENT_LINE.format(name=it["name"], intent=label, target=tgt)


def intents_text(st: E.BattleState) -> str:
    rows = [intent_text(it) for it in E.public_intents(st)]
    if not rows:
        return ""
    return S.INTENT_HEADER + "\n" + "\n".join(rows)


def meters_text(st: E.BattleState) -> List[str]:
    m = st.meters
    out = [S.METER_FIREWALL.format(value=int(m.get("firewall", 0)), max=int(st.config.get("firewall_max", 9)))]
    if st.has("ncpd"):
        out.append(S.METER_NCPD.format(value=float(m.get("ncpd", 0.0)), max=float(st.config.get("ncpd_max", 3.0))))
    if st.has("doll"):
        out.append(S.METER_CHIP.format(value=int(m.get("chip", 0)), max=int(m.get("chip_max", 0))))
        mode = m.get("doll_mode", "")
        if mode and st.status == E.ROUND_OPEN:
            out.append(S.METER_MODE.format(mode=S.MODE_KO.get(mode, mode)))
    if st.has("terminal"):
        h = st.holder()
        out.append(S.METER_SCREEN.format(value=int(m.get("screen", 0)), max=int(m.get("screen_max", 0)),
                                         holder=h.name if h else "-"))
    return out


def combo_text(card: E.Card) -> str:
    cb = card.combo
    if cb is None:
        return ""
    v = cb.value
    vs = f"{v:g}"
    bonus = S.COMBO_TEXT.get(cb.bonus, cb.bonus).format(v=vs)
    cond = S.COMBO_MOD_KO.get(cb.mod, cb.mod)
    if cb.cond == "self_has":
        return S.COMBO_SELF_LINE.format(cond=cond, bonus=bonus)
    return S.COMBO_LINE.format(cond=cond + " 적", bonus=bonus)


def card_summary(st: E.BattleState, card: E.Card, actor: Optional[E.Combatant]) -> str:
    """드롭다운 설명용 한 줄: [종류·대상] 예상 피해 · 설명"""
    parts = [f"[{S.KIND_KO.get(card.kind, card.kind)}·{S.TARGET_KO.get(card.target, card.target)}]"]
    if actor is not None:
        lo, hi = E.damage_range(card, actor, st.config)
        if hi > 0:
            parts.append(S.HAND_DMG.format(lo=lo, hi=hi))
    if card.tags:
        parts.append("·".join(S.TAG_KO.get(t, t) for t in card.tags))
    parts.append(card.desc)
    return " ".join(p for p in parts if p)


def card_detail(st: E.BattleState, card: E.Card, actor: Optional[E.Combatant]) -> str:
    """패 임베드 필드 본문: 설명 + 예상 피해 + 콤보 줄."""
    lines = [card.desc or "-"]
    if actor is not None:
        lo, hi = E.damage_range(card, actor, st.config)
        if hi > 0:
            lines.append(f"예상 피해 {S.HAND_DMG.format(lo=lo, hi=hi)}" + (" (대상당)" if card.target == "all_enemy" else ""))
    cb = combo_text(card)
    if cb:
        lines.append(cb)
    return "\n".join(lines)


def lobby_text(names: Iterable[str]) -> str:
    names = list(names)
    return S.LOBBY.format(n=len(names), names=", ".join(names) if names else S.LOBBY_EMPTY)


def intro_text(st: E.BattleState, desc: str = "") -> str:
    return S.INTRO.format(
        battle=st.battle_name or st.battle_id, desc=desc,
        enemies=", ".join(f"{e.name} (HP {e.max_hp})" for e in st.enemies.values()),
        players=", ".join(f"{p.name} (HP {p.hp}/{p.max_hp})" for p in st.players.values()),
    ).replace("\n\n", "\n")


def progress(st: E.BattleState):
    if st.status == E.ESCAPE:
        acting = E.escape_players(st)
        done = sum(1 for p in acting if p.id in st.escape.get("choices", {}))
        return done, len(acting)
    acting = E.acting_players(st)
    done = sum(1 for p in acting if p.id in st.choices)
    return done, len(acting)


def mark_for(st: E.BattleState, p: E.Combatant) -> str:
    if not p.alive():
        return S.MARK_OUT
    if st.status == E.ESCAPE:
        return S.MARK_DONE if p.id in st.escape.get("choices", {}) else S.MARK_TODO
    if E.is_stunned(p):
        return S.MARK_STUN
    if p.id in st.choices:
        return S.MARK_DONE
    if E.is_grappled(p):
        return S.MARK_GRAPPLED
    return S.MARK_TODO


def _marks(st: E.BattleState) -> str:
    return "  ".join(f"{mark_for(st, p)} {p.name}" for p in st.players.values())


def turn_open_text(st: E.BattleState) -> str:
    done, total = progress(st)
    return S.TURN_OPEN.format(round=st.round_no(), done=done, total=total, marks=_marks(st),
                              intents=intents_text(st), board=hp_board(st))


def report_text(report: Dict[str, Any]) -> str:
    lines = [S.REPORT_HEADER.format(round=report.get("round_no", 0))]
    for line in report["lines"]:
        prefix = "🟢 " if line.get("side") == E.ALLY else "🔴 "
        for i, text in enumerate(log_lines(line)):
            lines.append((prefix + text) if i == 0 else text)
    lines.extend(event_lines(report.get("events") or []))
    return "\n".join(lines)


def winner_title(st: E.BattleState) -> str:
    if st.winner == E.ESCAPED:
        return S.WIN_ESCAPED
    if st.winner == E.CAUGHT:
        return S.WIN_CAUGHT
    if st.winner == E.ALLY:
        if st.end_reason == "chip":
            return S.WIN_ALLY_CHIP
        if st.has("doll"):
            return S.WIN_ALLY_DOLL_HP
        return S.WIN_ALLY
    return S.WIN_ENEMY


def winner_text(st: E.BattleState) -> str:
    return winner_title(st) + "\n" + hp_board(st)


def status_text(st: E.BattleState) -> str:
    done, total = progress(st)
    return S.STATUS.format(status=st.status, round=st.round_no(), done=done, total=total, board=hp_board(st))


def hand_text(st: E.BattleState, uid: str, hand: List[str]) -> str:
    p = st.players.get(uid)
    admin = S.HAND_ADMIN if p is not None and E.admin_sees_all(st, p) else ""
    cards = "\n".join(
        S.HAND_CARD.format(name=c.name, kind=S.KIND_KO.get(c.kind, c.kind),
                           target=S.TARGET_KO.get(c.target, c.target), desc=card_detail(st, c, p).replace("\n", " / "))
        for c in (st.cards[cid] for cid in hand if cid in st.cards)
    )
    text = S.HAND_TITLE.format(round=st.round_no(), admin=admin, cards=cards)
    return text if len(text) <= MAX_LEN else text[:MAX_LEN - 1] + "…"


# -- 도주 단계 -------------------------------------------------------------
def escape_hint_text(st: E.BattleState) -> str:
    hint = st.escape.get("hint", "")
    if hint:
        return S.ESCAPE_HINT.format(hint=S.POLICE_KO.get(hint, hint))
    return S.ESCAPE_NO_HINT


def escape_open_text(st: E.BattleState) -> str:
    es = st.escape
    done, total = progress(st)
    return S.ESCAPE_OPEN.format(turn=es.get("turn", 1), max=es.get("max_turns", 5), alert=es.get("alert", 0),
                                alert_max=es.get("alert_max", 0), rule=S.ESCAPE_RULE, hint=escape_hint_text(st),
                                done=done, total=total, marks=_marks(st))


def escape_report_lines(rep: Dict[str, Any]) -> List[str]:
    out = [S.ESCAPE_REPORT_HEADER.format(turn=rep["turn"], police=S.POLICE_KO.get(rep["police"], rep["police"]))]
    for r in rep["results"]:
        out.append(S.ESCAPE_LINE.format(mark=S.ESCAPE_OUTCOME_MARK.get(r["outcome"], "⬜"), name=r["name"],
                                        action=S.ESCAPE_ACTION_KO.get(r["action"], "-"),
                                        outcome=S.ESCAPE_OUTCOME_KO.get(r["outcome"], r["outcome"])))
    fmt = S.ESCAPE_TURN_WON if rep["turn_won"] else S.ESCAPE_TURN_LOST
    out.append(fmt.format(wins=rep["wins"], n=rep["n"], alert=rep["alert"], alert_max=rep["alert_max"]))
    return out


def chunks(text: str, limit: int = MAX_LEN) -> List[str]:
    out: List[str] = []
    cur = ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit and cur:
            out.append(cur)
            cur = ""
        cur = line if not cur else cur + "\n" + line
    if cur:
        out.append(cur)
    return out
