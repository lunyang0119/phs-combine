"""튜토리얼 대본 — Tutorial.csv 한 장으로 라운드마다 적의 행동, 보장 카드, 준비(setup), 과제(goal)를 정한다.

행: script_id, round, coach, enemy_script, guaranteed, setup, goal, success, fail
  enemy_script  `enemy_id:card_id;…`  (적 id 접두 일치. 적히지 않은 적은 AI)
  guaranteed    `card_id;…`           (모든 플레이어의 패에 반드시 들어간다)
  setup         `enemy_hp:<enemy_id>:<hp>;enemy_mod:<enemy_id>:<modifier>;firewall:<n>` (라운드가 열리기 전에 적용)
  goal          all_submitted | kind:<attack|defend|hack|special> | card:<card_id> | combo | firewall_used | finish_nonlethal
대본은 new_battle 에 스냅샷으로 들어가(BattleState.script) 재시작을 견디고, 엔진은 파일을 읽지 않는다.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from . import engine as E

GOALS = ("all_submitted", "kind", "card", "combo", "firewall_used", "finish_nonlethal")
SETUP_KINDS = ("enemy_hp", "enemy_mod", "firewall")


class ScriptError(ValueError):
    """운영자에게 그대로 보여줄 수 있는 대본 오류."""


def _s(r: Mapping[str, Any], key: str) -> str:
    v = r.get(key)
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() == "nan" else s


def parse_script(rows: Sequence[Mapping[str, Any]], cards: Mapping[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """script_id → 라운드 순 step 목록. 라운드는 1부터 빈틈없이 이어져야 한다."""
    out: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        sid = _s(r, "script_id")
        if not sid:
            continue
        try:
            rnd = int(float(_s(r, "round")))
        except ValueError:
            raise ScriptError(f"[{sid}] round 는 정수여야 합니다: {_s(r, 'round')!r}")
        enemies: Dict[str, str] = {}
        for chunk in E.split_list(_s(r, "enemy_script")):
            eid, _, cid = chunk.partition(":")
            eid, cid = eid.strip(), cid.strip()
            if not eid or cid not in cards:
                raise ScriptError(f"[{sid} r{rnd}] enemy_script 의 카드 {cid!r} 가 없습니다")
            enemies[eid] = cid
        guaranteed = E.split_list(_s(r, "guaranteed"))
        for cid in guaranteed:
            if cid not in cards:
                raise ScriptError(f"[{sid} r{rnd}] guaranteed 카드 {cid!r} 가 없습니다")
        setup: List[Dict[str, Any]] = []
        for chunk in E.split_list(_s(r, "setup")):
            parts = [p.strip() for p in chunk.split(":")]
            kind = parts[0]
            if kind not in SETUP_KINDS:
                raise ScriptError(f"[{sid} r{rnd}] 알 수 없는 setup: {kind!r} (가능: {', '.join(SETUP_KINDS)})")
            if kind == "firewall":
                if len(parts) != 2:
                    raise ScriptError(f"[{sid} r{rnd}] setup firewall:<n>")
                setup.append({"kind": kind, "value": int(float(parts[1]))})
            elif kind == "enemy_hp":
                if len(parts) != 3:
                    raise ScriptError(f"[{sid} r{rnd}] setup enemy_hp:<enemy_id>:<hp>")
                setup.append({"kind": kind, "enemy": parts[1], "value": int(float(parts[2]))})
            else:
                if len(parts) != 3 or parts[2] not in E.MODIFIER_KINDS:
                    raise ScriptError(f"[{sid} r{rnd}] setup enemy_mod:<enemy_id>:<{'|'.join(E.MODIFIER_KINDS)}>")
                setup.append({"kind": kind, "enemy": parts[1], "value": parts[2]})
        goal = _s(r, "goal")
        gkind, _, garg = goal.partition(":")
        if gkind and gkind not in GOALS:
            raise ScriptError(f"[{sid} r{rnd}] 알 수 없는 goal: {goal!r} (가능: {', '.join(GOALS)})")
        if gkind == "kind" and garg not in E.KINDS:
            raise ScriptError(f"[{sid} r{rnd}] goal kind:<{'|'.join(E.KINDS)}>")
        if gkind == "card" and garg not in cards:
            raise ScriptError(f"[{sid} r{rnd}] goal 의 카드 {garg!r} 가 없습니다")
        out.setdefault(sid, []).append({
            "round": rnd, "coach": _s(r, "coach"), "enemies": enemies, "guaranteed": guaranteed, "setup": setup,
            "goal": gkind, "goal_arg": garg, "success": _s(r, "success"), "fail": _s(r, "fail"),
        })
    for sid, steps in out.items():
        steps.sort(key=lambda s: s["round"])
        rounds = [s["round"] for s in steps]
        if rounds != list(range(1, len(rounds) + 1)):
            raise ScriptError(f"[{sid}] round 는 1부터 빈틈없이 이어져야 합니다: {rounds}")
    return out


# ---------------------------------------------------------------------------
def step(st: E.BattleState, rnd: Optional[int] = None) -> Optional[Dict[str, Any]]:
    rnd = st.round if rnd is None else rnd
    for s in st.script:
        if int(s.get("round", 0)) == rnd:
            return s
    return None


def last_round(st: E.BattleState) -> int:
    return max((int(s.get("round", 0)) for s in st.script), default=0)


def coach_text(st: E.BattleState) -> str:
    s = step(st)
    return str(s.get("coach", "")) if s else ""


def _matches(enemy: E.Combatant, eid: str) -> bool:
    return enemy.id == eid or enemy.id.startswith(eid + "#")


def apply_setup(st: E.BattleState) -> None:
    """라운드가 열리기 전에 대본의 setup 을 적용한다 (HP 는 쓰러진 적도 되살린다 — 튜토리얼 전용)."""
    s = step(st)
    if not s:
        return
    for item in s.get("setup") or []:
        kind = item.get("kind")
        if kind == "firewall":
            st.meters["firewall"] = min(int(st.config.get("firewall_max", 9)), max(0, int(item["value"])))
            continue
        for e in st.enemies.values():
            if not _matches(e, str(item.get("enemy", ""))):
                continue
            if kind == "enemy_hp":
                e.hp = max(0, min(e.max_hp, int(item["value"])))
            elif kind == "enemy_mod":
                E.add_modifier(e, E.Modifier(str(item["value"]), 1.0, 1, st.round, source="tutorial"))


def guaranteed(st: E.BattleState, uid: str, hand: List[str]) -> List[str]:
    """보장 카드를 패 앞에 넣는다. 패 크기는 유지하되 보장 카드가 더 많으면 그만큼 늘어난다."""
    s = step(st)
    if not s:
        return hand
    g = [c for c in s.get("guaranteed") or [] if c in st.cards]
    if not g:
        return hand
    k = max(len(hand), int(st.config.get("hand_size", 3)))
    rest = [c for c in hand if c not in g]
    return (g + rest)[:max(k, len(g))]


def chooser(st: E.BattleState) -> E.Chooser:
    """대본에 적힌 적은 그 카드를 (무작위 생존 아군에게) 낸다. 나머지는 AI."""
    from . import ai  # 순환 import 회피

    def choose(state: E.BattleState, me: E.Combatant, hand: List[str], rng: E.Rng) -> Optional[E.Choice]:
        s = step(state)
        if s:
            for eid, cid in (s.get("enemies") or {}).items():
                if _matches(me, eid) and cid in state.cards:
                    card = state.cards[cid]
                    targets = E.valid_targets(state, me, card)
                    tid = rng.choice(targets).id if targets else None
                    if E.needs_target(card) and tid is None:
                        return None
                    return E.Choice(me.id, cid, tid if E.needs_target(card) else (me.id if card.target == "self" else None))
        return ai.choose(state, me, hand, rng)

    return choose


def evaluate(st: E.BattleState, lines: List[Dict[str, Any]], events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """이번 라운드 과제 판정 → {"round", "goal", "ok", "text"}. 과제가 없으면 ok=None."""
    s = step(st)
    if not s or not s.get("goal"):
        return {"round": st.round, "goal": "", "ok": None, "text": ""}
    goal, arg = s["goal"], s.get("goal_arg", "")
    ally = [ln for ln in lines if ln.get("side") == E.ALLY]
    ok = False
    if goal == "all_submitted":
        ok = bool(ally) and all(ln.get("result") != "wait" for ln in ally)
    elif goal == "kind":
        ok = any(ln.get("kind") == arg and ln.get("result") not in ("wait",) for ln in ally)
    elif goal == "card":
        ok = any(ln.get("card_id") == arg for ln in ally)
    elif goal == "combo":
        ok = any(ln.get("combo") for ln in ally)
    elif goal == "firewall_used":
        ok = any(ev.get("kind") == "firewall_use" for ev in events)
    elif goal == "finish_nonlethal":
        ko = st.meters.get("last_ko") or {}
        killed_now = any(t.get("killed") and t.get("kind") == "damage" for ln in ally for t in ln.get("targets") or [])
        ok = bool(killed_now and ko.get("nonlethal") and not ko.get("overkill"))
    return {"round": st.round, "goal": goal, "ok": ok, "text": s.get("success" if ok else "fail", "")}
