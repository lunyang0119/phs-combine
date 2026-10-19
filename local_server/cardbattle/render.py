"""상태 → 텍스트. Discord 객체는 모른다."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

from utils import get_korean_particle

from . import engine as E
from . import strings as S

MAX_LEN = 1900


def _josa(word: str, a: str, b: str) -> str:
    """받침 유무로 조사 선택. 한글로 끝나지 않으면(영문·숫자) '이(가)' 꼴로 묶어 쓴다."""
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
        return fmt.format(amount=int(amount) if kind in ("damage", "self_damage", "heal") else amount,
                          pct=pct, turns=turns or "")
    except (KeyError, ValueError):
        return ""


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
    if res == "misfire":
        return [S.LOG_MISFIRE.format(card=card, eul=eul, **a)]
    if res == "no_target":
        return [S.LOG_NO_TARGET.format(card=card, eul=eul, **a)]
    head = S.LOG_USE.format(card=card, eul=eul, **a)
    if res == "redirect":
        head = S.LOG_REDIRECT_PREFIX + head
    targets = line.get("targets") or []
    if not targets:
        return [head]
    out: List[str] = []
    for i, t in enumerate(targets):
        detail = _detail(t) + (S.LOG_KILLED if t.get("killed") and t.get("kind") in ("damage", "self_damage") else "")
        if t["id"] == line["actor_id"]:
            tail = S.LOG_SELF.format(hp=t["hp"], max=t["max_hp"], detail=detail)
        else:
            tail = S.LOG_TARGET.format(target=t["name"], hp=t["hp"], max=t["max_hp"], detail=detail)
        out.append((head if i == 0 else "　↳") + tail)
    return out


def _unit_text(u: E.Combatant) -> str:
    if not u.alive():
        return S.BOARD_DEAD.format(name=u.name)
    tags = "".join(S.TAG.get(m.kind, "") for m in u.modifiers)
    return S.BOARD_UNIT.format(name=u.name, hp=u.hp, max=u.max_hp, tags=(" " + tags) if tags else "")


def hp_board(st: E.BattleState) -> str:
    ally = " | ".join(_unit_text(u) for u in st.players.values()) or "-"
    enemy = " | ".join(_unit_text(u) for u in st.enemies.values()) or "-"
    return S.BOARD_ALLY.format(units=ally) + "\n" + S.BOARD_ENEMY.format(units=enemy)


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
    acting = E.acting_players(st)
    done = sum(1 for p in acting if p.id in st.choices)
    return done, len(acting)


def _marks(st: E.BattleState) -> str:
    parts = []
    for p in st.players.values():
        if not p.alive():
            m = S.MARK_OUT
        elif E.is_stunned(p):
            m = S.MARK_STUN
        elif p.id in st.choices:
            m = S.MARK_DONE
        else:
            m = S.MARK_TODO
        parts.append(f"{m} {p.name}")
    return "  ".join(parts)


def turn_open_text(st: E.BattleState) -> str:
    done, total = progress(st)
    return S.TURN_OPEN.format(round=st.round_no(), done=done, total=total, marks=_marks(st), board=hp_board(st))


def report_text(report: Dict[str, Any]) -> str:
    header = S.REPORT_HEADER_ALLY if report["side"] == E.ALLY else S.REPORT_HEADER_ENEMY
    lines = [header.format(round=report.get("round_no", (report["turn_no"] + 1) // 2))]
    for line in report["lines"]:
        lines.extend(log_lines(line))
    return "\n".join(lines)


def winner_text(st: E.BattleState) -> str:
    return (S.WIN_ALLY if st.winner == E.ALLY else S.WIN_ENEMY) + "\n" + hp_board(st)


def status_text(st: E.BattleState) -> str:
    done, total = progress(st)
    return S.STATUS.format(status=st.status, round=st.round_no(), side=S.SIDE_KO.get(st.side, st.side),
                           done=done, total=total, board=hp_board(st))


def hand_text(st: E.BattleState, uid: str, hand: List[str]) -> str:
    p = st.players.get(uid)
    admin = S.HAND_ADMIN if p is not None and E.admin_sees_all(st, p) else ""
    cards = "\n".join(
        S.HAND_CARD.format(name=c.name, kind=S.KIND_KO.get(c.kind, c.kind),
                           target=S.TARGET_KO.get(c.target, c.target), desc=c.desc)
        for c in (st.cards[cid] for cid in hand if cid in st.cards)
    )
    text = S.HAND_TITLE.format(round=st.round_no(), admin=admin, cards=cards)
    return text if len(text) <= MAX_LEN else text[:MAX_LEN - 1] + "…"


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
