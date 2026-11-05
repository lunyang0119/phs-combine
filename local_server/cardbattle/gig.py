"""긱(보스전 1회) 경제 — 준비 단계의 개조와 금고, 전투 뒤의 보수. Discord 의존성 없음.

금고(에디)는 세션 전체에서 하나뿐인 지속 상태로 `data/cardbattle_gig.json` 에 둔다. 무기는 전투 상태에만
있고 전투가 끝나면 부서진다(저장하지 않는다).

흐름: engine.new_battle(prep_enabled) → PREP (무기 배정, 금고 균등 분배)
      → prep_upgrade / prep_ready … → finish_prep (미사용 분배금 반환 → engine.begin_combat)
      → … ENDED → apply_payout (보수 + 남은 금고를 파일에 더한다, 멱등)
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from . import engine as E

logger = logging.getLogger(__name__)
DEFAULT_PATH = os.path.join("data", "cardbattle_gig.json")
UPGRADE_KINDS = ("normal", "black")


# ---------------------------------------------------------------------------
# 금고 파일
# ---------------------------------------------------------------------------
def load_gig(path: str = DEFAULT_PATH) -> Optional[Dict[str, Any]]:
    """{"pot": int, "history": [...]} 또는 파일이 없으면 None."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        return {"pot": int(d.get("pot", 0)), "history": list(d.get("history") or [])}
    except Exception as e:  # noqa: BLE001
        logger.error("금고 파일을 읽지 못했습니다: %s", e)
        return None


def load_pot(path: str = DEFAULT_PATH) -> Optional[int]:
    g = load_gig(path)
    return None if g is None else int(g["pot"])


def save_gig(data: Dict[str, Any], path: str = DEFAULT_PATH) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"pot": int(data.get("pot", 0)), "history": list(data.get("history") or [])}, fh, ensure_ascii=False)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def set_pot(amount: int, path: str = DEFAULT_PATH) -> int:
    g = load_gig(path) or {"pot": 0, "history": []}
    g["pot"] = max(0, int(amount))
    save_gig(g, path)
    return g["pot"]


# ---------------------------------------------------------------------------
# 준비 단계
# ---------------------------------------------------------------------------
def _player(st: E.BattleState, uid: str) -> E.Combatant:
    if st.status != E.PREP:
        raise E.RuleError("NOT_PREP")
    p = st.players.get(uid)
    if p is None:
        raise E.RuleError("NOT_PLAYER")
    return p


def price_of(st: E.BattleState, kind: str) -> int:
    return int(st.config.get("price_normal" if kind == "normal" else "price_black", 0))


def prep_upgrade(st: E.BattleState, uid: str, kind: str, rng: E.Rng = E.DEFAULT_RNG) -> Dict[str, Any]:
    """정식(normal) / 뒷골목(black) 개조 한 번. 결과 항목을 prep.rolls 에 남기고 돌려준다."""
    p = _player(st, uid)
    if kind not in UPGRADE_KINDS:
        raise E.RuleError("BAD_OPTION")
    cfg = st.config
    prep = st.prep
    if uid in prep.get("ready", []):
        raise E.RuleError("PREP_READY")
    if int(prep["actions"].get(uid, 0)) >= int(cfg.get("prep_actions", 2)):
        raise E.RuleError("PREP_NO_ACTIONS")
    price = price_of(st, kind)
    allowance = int(prep["allowance"].get(uid, 0))
    if allowance < price:
        raise E.RuleError("PREP_POOR")
    wmax = int(cfg.get("weapon_max", 4))
    before = p.weapon_grade
    if before >= wmax:
        raise E.RuleError("WEAPON_MAX")
    if kind == "normal" and before >= int(cfg.get("weapon_normal_max", 3)):
        raise E.RuleError("WEAPON_NORMAL_MAX")
    if kind == "normal":
        roll = {"roll": None, "delta": 1, "refund": False, "success": True, "auto": True}
    else:
        roll = E.roll_upgrade(before, rng, cfg, allow_down=True)
    after = E.clamp_grade(before + int(roll["delta"]), cfg)
    p.weapon_grade = after
    paid = 0 if roll.get("refund") else price
    prep["allowance"][uid] = allowance - paid
    prep["spent"][uid] = int(prep["spent"].get(uid, 0)) + paid
    prep["actions"][uid] = int(prep["actions"].get(uid, 0)) + 1
    entry = {
        "uid": uid, "name": p.name, "kind": kind, "roll": roll["roll"], "before": before, "after": after,
        "success": bool(roll["success"]), "refund": bool(roll.get("refund")), "price": price, "paid": paid,
        "weapon": E.weapon_label(p), "allowance": prep["allowance"][uid],
        "actions_left": int(cfg.get("prep_actions", 2)) - prep["actions"][uid],
    }
    prep["rolls"].append(entry)
    return entry


def prep_ready(st: E.BattleState, uid: str) -> bool:
    """준비 완료 표시. 이미 표시했으면 False."""
    _player(st, uid)
    ready: List[str] = st.prep.setdefault("ready", [])
    if uid in ready:
        return False
    ready.append(uid)
    return True


def prep_all_ready(st: E.BattleState) -> bool:
    if st.status != E.PREP:
        return False
    ready = set(st.prep.get("ready", []))
    return all(p.id in ready for p in st.players.values() if p.alive())


def finish_prep(st: E.BattleState, rng: E.Rng = E.DEFAULT_RNG, chooser: Optional[E.Chooser] = None) -> Optional[Dict[str, Any]]:
    """준비 단계를 닫는다. 쓰지 않은 분배금은 금고(meters.pot)로 돌아가 현장 개조 재원이 된다.
    first_strike 면 선제 라운드 보고를 돌려준다 (pending_reports 에도 들어 있다)."""
    if st.status != E.PREP:
        raise E.RuleError("NOT_PREP")
    unspent = sum(int(v) for v in st.prep.get("allowance", {}).values())
    st.meters["pot"] = int(st.meters.get("pot", 0)) + unspent
    st.prep["allowance"] = {k: 0 for k in st.prep.get("allowance", {})}
    st.prep["returned"] = unspent
    st.prep["done"] = True
    return E.begin_combat(st, rng, chooser)


# ---------------------------------------------------------------------------
# 보수
# ---------------------------------------------------------------------------
def count_penalties(st: E.BattleState) -> Dict[str, int]:
    overkills = 0
    loud = 0
    for rep in st.log:
        for line in rep.get("lines") or []:
            if line.get("side") != E.ALLY:
                continue
            overkills += sum(1 for t in line.get("targets") or [] if t.get("overkill"))
            card = st.cards.get(line.get("card_id", ""))
            if card is not None and card.tagged("loud") and line.get("result") not in ("misfire", "no_target"):
                loud += 1
    return {"overkills": overkills, "loud": loud}


def payout(st: E.BattleState) -> Dict[str, Any]:
    cfg = st.config
    n = max(1, len(st.players))
    base = int(cfg.get("reward_per_player", 150)) * n
    if st.winner in (E.ALLY, E.ESCAPED):
        ratio = float(cfg.get("reward_collapse_ratio", 0.5)) if st.end_reason == "collapse" else 1.0
    else:
        ratio = float(cfg.get("reward_fail_ratio", 0.0))
    pen = count_penalties(st)
    penalty = pen["overkills"] * int(cfg.get("penalty_overkill", 50)) + pen["loud"] * int(cfg.get("penalty_loud", 20))
    reward = max(0, int(round(base * ratio)) - penalty)
    return {
        "base": base, "ratio": ratio, "overkills": pen["overkills"], "loud": pen["loud"], "penalty": penalty,
        "reward": reward, "leftover": int(st.meters.get("pot", 0)),
        "sandbox": bool(cfg.get("prep_sandbox")), "enabled": bool(cfg.get("prep_enabled", True)),
    }


def apply_payout(st: E.BattleState, path: str = DEFAULT_PATH) -> Dict[str, Any]:
    """전투가 끝났을 때 한 번만 금고에 더한다 (meters.payout 이 있으면 그대로 돌려준다)."""
    if st.status != E.ENDED:
        raise E.RuleError("NOT_ENDED")
    if isinstance(st.meters.get("payout"), dict):
        return st.meters["payout"]
    p = payout(st)
    if p["enabled"] and not p["sandbox"]:
        g = load_gig(path) or {"pot": 0, "history": []}
        g["pot"] = int(g["pot"]) + p["reward"] + p["leftover"]
        g["history"].append({"battle": st.battle_id, "winner": st.winner, "reason": st.end_reason,
                             "reward": p["reward"], "leftover": p["leftover"], "pot": g["pot"],
                             "players": len(st.players)})
        save_gig(g, path)
        p["pot_after"] = g["pot"]
    else:
        p["pot_after"] = None
    st.meters["payout"] = p
    return p
