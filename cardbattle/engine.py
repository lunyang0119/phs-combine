"""카드 전투 미니게임 — 순수 규칙 엔진 (Discord 의존성 없음).

반턴(half-turn) 단위로 진행한다. turn_no 홀수 = 아군 턴, 짝수 = 적 턴.
반턴 정산 순서: 해킹 → 방어 → 공격/특수. 같은 단계 안에서는 민첩 내림차순(동점은 난수).
수정자(Modifier) 만료 규칙:
  - 자기 턴에 의미 있는 것(atk_down, misfire, redirect_random, stun)은 보유자의 반턴이 끝날 때 1 감소
  - 상대 턴에 의미 있는 것(shield)은 상대의 반턴이 끝날 때 1 감소
  - 생성된 반턴에는 감소하지 않는다 → 아군 턴에 낸 방어는 바로 다음 적 턴 동안 유효
  - misfire / redirect_random / stun 은 발동 시 소모된다
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence, Tuple

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------
IDLE, LOBBY, ALLY_TURN, RESOLVING, ENEMY_TURN, ENDED = (
    "IDLE", "LOBBY", "ALLY_TURN", "RESOLVING", "ENEMY_TURN", "ENDED",
)
ALLY, ENEMY = "ally", "enemy"
KINDS = ("attack", "defend", "hack", "special")
TARGETS = ("enemy", "ally", "self", "all_enemy", "all_ally")
STATS = ("physics", "magic", "none")
POOLS = ("ally", "enemy", "both")
EFFECT_KINDS = ("damage", "heal", "shield", "self_damage", "atk_down", "misfire", "redirect_random", "stun")
MODIFIER_KINDS = ("shield", "atk_down", "misfire", "redirect_random", "stun")
ONE_SHOT = ("misfire", "redirect_random", "stun")
OWN_TURN_MODS = ("atk_down", "misfire", "redirect_random", "stun")   # 보유자가 행동할 때 의미 있음
PHASE = {"hack": 0, "defend": 1, "attack": 2, "special": 2}


class RuleError(ValueError):
    """플레이어에게 그대로 보여줄 수 있는 규칙 위반. args[0] 이 코드."""

    @property
    def code(self) -> str:
        return str(self.args[0]) if self.args else ""


# ---------------------------------------------------------------------------
# 난수 — 기본은 combat.random_utils, 테스트/sim 은 시드 고정 random.Random
# ---------------------------------------------------------------------------
class Rng(Protocol):
    def random(self) -> float: ...
    def randint(self, a: int, b: int) -> int: ...
    def choice(self, seq): ...
    def choices(self, population, weights=None, k=1): ...


class RandomUtilsRng:
    """combat.random_utils 어댑터 (지연 import: combat 패키지가 discord 를 끌어오므로).

    random_utils.get_random() 은 내부 버그(`_rng.get_random`)로 예외를 내므로 uniform 을 쓴다.
    """

    @staticmethod
    def _ru():
        from combat import random_utils
        return random_utils

    def random(self) -> float:
        return self._ru().uniform(0.0, 1.0)

    def randint(self, a: int, b: int) -> int:
        return self._ru().randint(a, b)

    def choice(self, seq):
        return self._ru().choice(seq)

    def choices(self, population, weights=None, k=1):
        return self._ru().choices(population, weights=weights, k=k)


DEFAULT_RNG: Rng = RandomUtilsRng()


# ---------------------------------------------------------------------------
# 데이터
# ---------------------------------------------------------------------------
@dataclass
class Effect:
    kind: str
    value: float = 0.0
    turns: int = 1
    chance: float = 1.0


@dataclass
class Card:
    id: str
    name: str
    kind: str
    target: str
    stat: str = "none"
    pool: str = "both"
    weight: float = 1.0
    effects: List[Effect] = field(default_factory=list)
    desc: str = ""

    def has(self, effect_kind: str) -> bool:
        return any(e.kind == effect_kind for e in self.effects)

    def effect(self, effect_kind: str) -> Optional[Effect]:
        for e in self.effects:
            if e.kind == effect_kind:
                return e
        return None


@dataclass
class EnemyDef:
    id: str
    name: str
    max_hp: int
    physics: int = 10
    magic: int = 10
    agility: int = 10
    charm: int = 10
    personality: str = "aggressive"
    pool: str = ENEMY


@dataclass
class BattleDef:
    id: str
    name: str
    enemies: List[str]          # 펼쳐진 enemy_id 목록 (중복 포함)
    desc: str = ""


@dataclass
class Modifier:
    kind: str
    value: float
    turns_left: int
    created_turn: int
    source: str = ""


@dataclass
class Combatant:
    id: str
    name: str
    side: str
    max_hp: int
    hp: int
    physics: int = 10
    magic: int = 10
    agility: int = 10
    charm: int = 10
    personality: str = "aggressive"
    pool: str = ""
    is_host: bool = False
    modifiers: List[Modifier] = field(default_factory=list)

    def alive(self) -> bool:
        return self.hp > 0

    def mod(self, kind: str) -> Optional[Modifier]:
        for m in self.modifiers:
            if m.kind == kind:
                return m
        return None

    def remove_mod(self, kind: str) -> None:
        self.modifiers = [m for m in self.modifiers if m.kind != kind]

    def hp_ratio(self) -> float:
        return self.hp / self.max_hp if self.max_hp > 0 else 0.0


@dataclass
class Choice:
    actor_id: str
    card_id: str
    target_id: Optional[str] = None
    ts: float = 0.0


@dataclass
class BattleState:
    status: str = IDLE
    battle_id: str = ""
    battle_name: str = ""
    turn_no: int = 0
    side: str = ALLY
    host_id: str = ""
    players: Dict[str, Combatant] = field(default_factory=dict)
    enemies: Dict[str, Combatant] = field(default_factory=dict)
    cards: Dict[str, Card] = field(default_factory=dict)
    config: Dict[str, Any] = field(default_factory=dict)
    hands: Dict[str, List[str]] = field(default_factory=dict)
    choices: Dict[str, Choice] = field(default_factory=dict)
    last_player_kinds: List[str] = field(default_factory=list)
    log: List[Dict[str, Any]] = field(default_factory=list)
    pending_reports: List[Dict[str, Any]] = field(default_factory=list)
    winner: Optional[str] = None
    guild_id: int = 0
    channel_id: int = 0
    lobby_message_id: int = 0
    turn_message_id: int = 0
    created_ts: float = 0.0

    # -- 조회 --------------------------------------------------------------
    def unit(self, uid: str) -> Optional[Combatant]:
        return self.players.get(uid) or self.enemies.get(uid)

    def side_units(self, side: str) -> List[Combatant]:
        return list(self.players.values()) if side == ALLY else list(self.enemies.values())

    def alive_units(self, side: Optional[str] = None) -> List[Combatant]:
        units = (self.side_units(ALLY) + self.side_units(ENEMY)) if side is None else self.side_units(side)
        return [u for u in units if u.alive()]

    def round_no(self) -> int:
        return (self.turn_no + 1) // 2

    # -- 직렬화 ------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "BattleState":
        st = cls()
        for k, v in d.items():
            if hasattr(st, k):
                setattr(st, k, v)
        st.players = {k: _combatant_from(v) for k, v in (d.get("players") or {}).items()}
        st.enemies = {k: _combatant_from(v) for k, v in (d.get("enemies") or {}).items()}
        st.cards = {k: card_from_dict(v) for k, v in (d.get("cards") or {}).items()}
        st.choices = {k: Choice(**v) for k, v in (d.get("choices") or {}).items()}
        st.hands = {k: list(v) for k, v in (d.get("hands") or {}).items()}
        return st


def _combatant_from(d: Dict[str, Any]) -> Combatant:
    d = dict(d)
    d["modifiers"] = [Modifier(**m) for m in d.get("modifiers") or []]
    return Combatant(**d)


def card_from_dict(d: Dict[str, Any]) -> Card:
    d = dict(d)
    d["effects"] = [Effect(**e) for e in d.get("effects") or []]
    return Card(**d)


# ---------------------------------------------------------------------------
# 전투원 생성
# ---------------------------------------------------------------------------
def _num(row: Any, key: str, default: int) -> int:
    if row is None:
        return default
    try:
        v = row.get(key) if hasattr(row, "get") else row[key]
    except (KeyError, IndexError, TypeError):
        return default
    if v is None:
        return default
    s = str(v).strip()
    if s == "" or s.lower() == "nan":
        return default
    try:
        return int(float(s))
    except ValueError:
        return default


def player_combatant(uid: str, row: Any, display_name: str, config: Dict[str, Any], is_host: bool = False) -> Combatant:
    """Characters 시트 행 → 전투원. 시트 열 매핑은 여기 한 곳에만 둔다.

    row 는 pd.Series / dict / None. 이름은 디스코드 서버 닉네임(display_name)을 쓴다.
    """
    ds = int(config.get("default_stat", 10))
    max_hp = max(1, _num(row, "max_hp", int(config.get("default_hp", 30))))
    hp = max_hp
    if config.get("use_current_hp"):
        hp = min(max_hp, max(1, _num(row, "current_hp", max_hp)))
    return Combatant(
        id=str(uid), name=display_name, side=ALLY, max_hp=max_hp, hp=hp,
        physics=_num(row, "physics", ds), magic=_num(row, "magic", ds),
        agility=_num(row, "agility", ds), charm=_num(row, "charm", ds),
        pool=ALLY, is_host=is_host,
    )


_SUFFIX = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def enemy_combatants(bdef: BattleDef, defs: Dict[str, EnemyDef]) -> List[Combatant]:
    counts: Dict[str, int] = {}
    for eid in bdef.enemies:
        counts[eid] = counts.get(eid, 0) + 1
    seen: Dict[str, int] = {}
    out: List[Combatant] = []
    for eid in bdef.enemies:
        d = defs[eid]
        n = seen.get(eid, 0)
        seen[eid] = n + 1
        name = d.name if counts[eid] == 1 else f"{d.name} {_SUFFIX[n % len(_SUFFIX)]}"
        out.append(Combatant(
            id=f"{eid}#{n + 1}", name=name, side=ENEMY, max_hp=d.max_hp, hp=d.max_hp,
            physics=d.physics, magic=d.magic, agility=d.agility, charm=d.charm,
            personality=d.personality, pool=d.pool or ENEMY,
        ))
    return out


def new_battle(
    players: Sequence[Combatant],
    enemies: Sequence[Combatant],
    cards: Dict[str, Card],
    config: Dict[str, Any],
    host_id: str,
    battle_id: str = "",
    battle_name: str = "",
    now: Optional[float] = None,
) -> BattleState:
    if not players:
        raise RuleError("NO_PLAYERS")
    if not enemies:
        raise RuleError("NO_ENEMIES")
    st = BattleState(
        status=ALLY_TURN, battle_id=battle_id, battle_name=battle_name, turn_no=1, side=ALLY,
        host_id=str(host_id), cards=dict(cards), config=dict(config),
        created_ts=now if now is not None else time.time(),
    )
    for p in players:
        p.side = ALLY
        p.pool = p.pool or ALLY
        p.is_host = p.id == str(host_id)
        st.players[p.id] = p
    for e in enemies:
        e.side = ENEMY
        e.pool = e.pool or ENEMY
        st.enemies[e.id] = e
    for side in (ALLY, ENEMY):
        if not [c for c in cards.values() if c.pool in ("both", side)]:
            raise RuleError("EMPTY_POOL")
    return st


# ---------------------------------------------------------------------------
# 드로우
# ---------------------------------------------------------------------------
def pool_for(st: BattleState, c: Combatant) -> List[Card]:
    return [card for card in st.cards.values() if card.pool in ("both", c.pool or c.side)]


def draw_from_pool(pool: Sequence[Card], k: int, rng: Rng) -> List[str]:
    """가중치 비복원 추출."""
    remaining = list(pool)
    out: List[str] = []
    while remaining and len(out) < k:
        weights = [max(0.0, float(c.weight)) for c in remaining]
        if sum(weights) <= 0:
            pick = rng.choice(remaining)
        else:
            pick = rng.choices(remaining, weights=weights, k=1)[0]
        out.append(pick.id)
        remaining.remove(pick)
    return out


def admin_sees_all(st: BattleState, c: Combatant) -> bool:
    return bool(c.is_host and st.config.get("admin_sees_all", True))


def draw_hand(st: BattleState, uid: str, rng: Rng = DEFAULT_RNG) -> List[str]:
    """이번 턴의 패. 이미 뽑았으면 그대로 돌려준다. 주최자는 풀 전체(저장하지 않음)."""
    c = st.players.get(uid)
    if c is None:
        raise RuleError("NOT_PLAYER")
    if admin_sees_all(st, c):
        return [card.id for card in pool_for(st, c)]
    if uid not in st.hands:
        st.hands[uid] = draw_from_pool(pool_for(st, c), int(st.config.get("hand_size", 3)), rng)
    return list(st.hands[uid])


def hand_for(st: BattleState, uid: str) -> List[str]:
    c = st.players.get(uid)
    if c is None:
        return []
    if admin_sees_all(st, c):
        return [card.id for card in pool_for(st, c)]
    return list(st.hands.get(uid, []))


# ---------------------------------------------------------------------------
# 대상과 제출
# ---------------------------------------------------------------------------
def needs_target(card: Card) -> bool:
    return card.target in ("enemy", "ally")


def opposite(side: str) -> str:
    return ENEMY if side == ALLY else ALLY


def valid_targets(st: BattleState, actor: Combatant, card: Card) -> List[Combatant]:
    if card.target == "enemy":
        return st.alive_units(opposite(actor.side))
    if card.target == "ally":
        return st.alive_units(actor.side)
    if card.target == "self":
        return [actor]
    return []


def is_stunned(c: Combatant) -> bool:
    return c.mod("stun") is not None


def submit_choice(st: BattleState, uid: str, card_id: str, target_id: Optional[str] = None,
                  now: Optional[float] = None) -> Choice:
    if st.status != ALLY_TURN:
        raise RuleError("NOT_OPEN")
    actor = st.players.get(uid)
    if actor is None:
        raise RuleError("NOT_PLAYER")
    if not actor.alive():
        raise RuleError("DEAD")
    if is_stunned(actor):
        raise RuleError("STUNNED")
    if card_id not in hand_for(st, uid) or card_id not in st.cards:
        raise RuleError("NOT_IN_HAND")
    card = st.cards[card_id]
    targets = valid_targets(st, actor, card)
    if needs_target(card):
        if target_id is None:
            if len(targets) == 1:
                target_id = targets[0].id
            else:
                raise RuleError("NO_TARGET")
        elif target_id not in {t.id for t in targets}:
            raise RuleError("BAD_TARGET")
    elif card.target == "self":
        target_id = actor.id
    else:
        target_id = None
    ch = Choice(actor_id=uid, card_id=card_id, target_id=target_id, ts=now if now is not None else time.time())
    st.choices[uid] = ch
    return ch


def cancel_choice(st: BattleState, uid: str) -> bool:
    if st.status != ALLY_TURN:
        raise RuleError("NOT_OPEN")
    return st.choices.pop(uid, None) is not None


def acting_players(st: BattleState) -> List[Combatant]:
    return [p for p in st.players.values() if p.alive() and not is_stunned(p)]


def all_chosen(st: BattleState) -> bool:
    return all(p.id in st.choices for p in acting_players(st))


def winner(st: BattleState) -> Optional[str]:
    if not st.alive_units(ENEMY):
        return ALLY
    if not st.alive_units(ALLY):
        return ENEMY
    return None


# ---------------------------------------------------------------------------
# 정산
# ---------------------------------------------------------------------------
def stat_bonus(actor: Combatant, stat: str, config: Dict[str, Any]) -> int:
    div = max(1, int(config.get("stat_divisor", 4)))
    if stat == "physics":
        return actor.physics // div
    if stat == "magic":
        return actor.magic // div
    return 0


def expected_damage(card: Card, actor: Combatant, target: Optional[Combatant], config: Dict[str, Any]) -> float:
    """AI 용 기대 피해 (난수 평균, 수정자 반영)."""
    e = card.effect("damage")
    if e is None:
        return 0.0
    dmg = e.value + stat_bonus(actor, card.stat, config) + int(config.get("damage_variance", 4)) / 2.0
    ad = actor.mod("atk_down")
    if ad:
        dmg *= max(0.0, 1.0 - ad.value)
    if target is not None:
        sh = target.mod("shield")
        if sh:
            dmg *= max(0.0, 1.0 - sh.value)
    return max(0.0, dmg)


def add_modifier(target: Combatant, m: Modifier) -> None:
    old = target.mod(m.kind)
    if old is not None:
        m.value = max(old.value, m.value)
        m.turns_left = max(old.turns_left, m.turns_left)
        target.remove_mod(m.kind)
    target.modifiers.append(m)


def _tinfo(t: Combatant, kind: str, amount: float) -> Dict[str, Any]:
    return {"id": t.id, "name": t.name, "kind": kind, "amount": amount,
            "hp": t.hp, "max_hp": t.max_hp, "killed": not t.alive()}


def _apply_card(st: BattleState, actor: Combatant, card: Card, chosen_target: Optional[str],
                rng: Rng) -> Dict[str, Any]:
    cfg = st.config
    line: Dict[str, Any] = {
        "actor_id": actor.id, "actor_name": actor.name, "side": actor.side,
        "card_id": card.id, "card_name": card.name, "kind": card.kind,
        "result": "hit", "targets": [],
    }
    if card.kind == "defend":
        line["result"] = "shield"
    elif card.kind == "hack":
        line["result"] = "hack"
    elif card.has("heal") and not card.has("damage"):
        line["result"] = "heal"

    # 대상 확정 (선택한 대상이 죽었으면 생존자 중 무작위)
    if card.target in ("enemy", "ally"):
        cands = valid_targets(st, actor, card)
        t = st.unit(chosen_target) if chosen_target else None
        if t is None or not t.alive() or t not in cands:
            if not cands:
                line["result"] = "no_target"
                return line
            t = rng.choice(cands)
            line["result"] = "redirect"
        targets = [t]
    elif card.target == "self":
        targets = [actor]
    elif card.target == "all_enemy":
        targets = st.alive_units(opposite(actor.side))
    else:  # all_ally
        targets = st.alive_units(actor.side)

    if card.has("damage"):
        mis = actor.mod("misfire")
        if mis is not None:
            actor.remove_mod("misfire")
            if rng.random() < mis.value:
                line["result"] = "misfire"
                return line
        red = actor.mod("redirect_random")
        if red is not None:
            actor.remove_mod("redirect_random")
            if card.target in ("enemy", "ally"):
                cands = [u for u in st.alive_units() if cfg.get("redirect_includes_self") or u.id != actor.id]
                if cands:
                    targets = [rng.choice(cands)]
                    line["result"] = "redirect"

    for eff in card.effects:
        if eff.chance < 1.0 and rng.random() >= eff.chance:
            continue
        handler = HANDLERS.get(eff.kind)
        if handler is None:
            continue
        handler(st, actor, card, eff, targets, line, rng)
    return line


def _h_damage(st, actor, card, eff, targets, line, rng):
    cfg = st.config
    for t in targets:
        dmg = eff.value + stat_bonus(actor, card.stat, cfg) + rng.randint(0, int(cfg.get("damage_variance", 4)))
        ad = actor.mod("atk_down")
        if ad:
            dmg *= max(0.0, 1.0 - ad.value)
        sh = t.mod("shield")
        if sh:
            dmg *= max(0.0, 1.0 - sh.value)
        dmg = max(0, int(dmg))
        t.hp = max(0, t.hp - dmg)
        line["targets"].append(_tinfo(t, "damage", dmg))


def _h_self_damage(st, actor, card, eff, targets, line, rng):
    dmg = max(0, int(eff.value))
    actor.hp = max(0, actor.hp - dmg)
    line["targets"].append(_tinfo(actor, "self_damage", dmg))


def _h_heal(st, actor, card, eff, targets, line, rng):
    for t in targets:
        amt = int(eff.value + stat_bonus(actor, card.stat, st.config))
        healed = min(t.max_hp, t.hp + amt) - t.hp
        t.hp += healed
        line["targets"].append(_tinfo(t, "heal", healed))


def _h_modifier(st, actor, card, eff, targets, line, rng):
    for t in targets:
        turns = max(1, int(eff.turns))
        add_modifier(t, Modifier(kind=eff.kind, value=eff.value, turns_left=turns,
                                 created_turn=st.turn_no, source=actor.id))
        info = _tinfo(t, eff.kind, eff.value)
        info["turns"] = turns
        line["targets"].append(info)


HANDLERS: Dict[str, Callable[..., None]] = {
    "damage": _h_damage,
    "self_damage": _h_self_damage,
    "heal": _h_heal,
    "shield": _h_modifier,
    "atk_down": _h_modifier,
    "misfire": _h_modifier,
    "redirect_random": _h_modifier,
    "stun": _h_modifier,
}


def tick_modifiers(st: BattleState) -> None:
    for u in st.alive_units():
        kept: List[Modifier] = []
        for m in u.modifiers:
            relevant = (st.side == u.side) if m.kind in OWN_TURN_MODS else (st.side != u.side)
            if relevant and m.created_turn != st.turn_no:
                m.turns_left -= 1
            if m.turns_left > 0:
                kept.append(m)
        u.modifiers = kept


def _resolve(st: BattleState, side: str, choices: Dict[str, Optional[Choice]], rng: Rng) -> Dict[str, Any]:
    """한 편의 반턴 정산. choices 는 actor_id → Choice|None(대기)."""
    lines: List[Dict[str, Any]] = []
    units = [u for u in st.side_units(side) if u.alive()]

    ordered: List[Tuple[int, int, float, Combatant]] = []
    waits: List[Combatant] = []
    for u in units:
        ch = choices.get(u.id)
        if is_stunned(u):
            u.remove_mod("stun")
            lines.append({"actor_id": u.id, "actor_name": u.name, "side": u.side, "card_id": "",
                          "card_name": "", "kind": "", "result": "stunned", "targets": []})
            continue
        if ch is None or ch.card_id not in st.cards:
            waits.append(u)
            continue
        ordered.append((PHASE.get(st.cards[ch.card_id].kind, 2), -u.agility, rng.random(), u))
    ordered.sort(key=lambda x: (x[0], x[1], x[2]))

    for _, _, _, u in ordered:
        if not u.alive():
            continue
        ch = choices[u.id]
        assert ch is not None
        lines.append(_apply_card(st, u, st.cards[ch.card_id], ch.target_id, rng))
    for u in waits:
        lines.append({"actor_id": u.id, "actor_name": u.name, "side": u.side, "card_id": "",
                      "card_name": "", "kind": "", "result": "wait", "targets": []})

    tick_modifiers(st)
    w = winner(st)
    report = {
        "turn_no": st.turn_no, "round_no": st.round_no(), "side": side, "lines": lines,
        "hp": {u.id: [u.hp, u.max_hp] for u in list(st.players.values()) + list(st.enemies.values())},
        "winner": w,
    }
    st.log.append(report)
    return report


def resolve_ally_turn(st: BattleState, rng: Rng = DEFAULT_RNG) -> Dict[str, Any]:
    if st.status != ALLY_TURN:
        raise RuleError("NOT_OPEN")
    st.status = RESOLVING
    choices: Dict[str, Optional[Choice]] = {p.id: st.choices.get(p.id) for p in st.players.values()}
    st.last_player_kinds = [st.cards[c.card_id].kind for c in choices.values() if c and c.card_id in st.cards]
    report = _resolve(st, ALLY, choices, rng)
    st.hands = {}
    st.choices = {}
    st.turn_no += 1
    st.side = ENEMY
    if report["winner"]:
        st.winner = report["winner"]
        st.status = ENDED
    else:
        st.status = ENEMY_TURN
    return report


Chooser = Callable[[BattleState, Combatant, List[str], Rng], Optional[Choice]]


def run_enemy_turn(st: BattleState, rng: Rng = DEFAULT_RNG, chooser: Optional[Chooser] = None) -> Dict[str, Any]:
    if st.status != ENEMY_TURN:
        raise RuleError("NOT_OPEN")
    if chooser is None:
        from . import ai  # 순환 import 회피
        chooser = ai.choose
    st.status = RESOLVING
    choices: Dict[str, Optional[Choice]] = {}
    k = int(st.config.get("hand_size", 3))
    for e in st.enemies.values():
        if not e.alive() or is_stunned(e):
            choices[e.id] = None
            continue
        hand = draw_from_pool(pool_for(st, e), k, rng)
        choices[e.id] = chooser(st, e, hand, rng) if hand else None
    report = _resolve(st, ENEMY, choices, rng)
    st.turn_no += 1
    st.side = ALLY
    if report["winner"]:
        st.winner = report["winner"]
        st.status = ENDED
    else:
        st.status = ALLY_TURN
    return report
