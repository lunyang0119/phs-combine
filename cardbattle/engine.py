"""카드 전투 미니게임 — 순수 규칙 엔진 (Discord 의존성 없음).

라운드 단위로 진행한다. 라운드가 열리면(open_round) 적은 먼저 카드를 정하고 그 **종류만** 공개된다
(intents). 플레이어가 전원 고르면(또는 주최자가 강제하면) resolve_round 가 양편을 한 번에 정산한다.

정산 순서: 단계(해킹 0 → 방어 1 → 공격/특수 2) → 민첩 내림차순 → 난수. 양편이 섞여서 정산되므로
빠른 해커의 쇼크는 느린 적의 공격을 같은 라운드에 막는다.

수정자(Modifier):
  - shield / atk_down 은 라운드가 끝날 때 1 감소한다 (만든 라운드 포함). 즉 `shield:0.5:1` 은
    이번 라운드의 공격 단계만 막는다. `atk_down:0.3:2` 는 이번·다음 라운드의 공격을 약화한다.
  - misfire / redirect_random / stun 은 사용될 때 소모되고 라운드로는 줄지 않는다 (ONE_SHOT).
  - grapple(붙잡힘) 은 떨쳐내기, 아군의 명중, 방화벽 재부팅으로만 풀린다.

기믹(gimmicks, Battles.csv): nonlethal(과잉 제압 판정) / ncpd(경계도 → 도주 단계) /
drag(쓰러진 아군 끌고 가기) / doll(칩 안정도·모드) / terminal(단말기 소지자·액정).
팀 자원 '보느와의 방화벽' 은 항상 켜져 있다 (use_firewall).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence, Tuple

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------
IDLE, LOBBY, ROUND_OPEN, RESOLVING, ESCAPE, ENDED = (
    "IDLE", "LOBBY", "ROUND_OPEN", "RESOLVING", "ESCAPE", "ENDED",
)
ALLY, ENEMY = "ally", "enemy"
ESCAPED, CAUGHT = "escaped", "caught"          # winner 값 (도주 단계 결과)
KINDS = ("attack", "defend", "hack", "special")
TARGETS = ("enemy", "ally", "self", "all_enemy", "all_ally")
STATS = ("physics", "magic", "none")
POOLS = ("ally", "enemy", "both", "fixed")     # fixed: 규칙이 손에 쥐어주는 카드 (드로우 안 됨)
EFFECT_KINDS = ("damage", "heal", "shield", "self_damage", "atk_down", "misfire", "redirect_random", "stun",
                "grapple", "break_free", "pass_terminal", "chip", "noop")
MODIFIER_KINDS = ("shield", "atk_down", "misfire", "redirect_random", "stun", "grapple")
ONE_SHOT = ("misfire", "redirect_random", "stun")
TICKING = ("shield", "atk_down")
PHASE = {"hack": 0, "defend": 1, "attack": 2, "special": 2}
CARD_TAGS = ("loud", "lethal", "nonlethal")
COMBO_CONDS = ("target_has", "self_has")
COMBO_BONUSES = ("dmg_x", "dmg_add", "chip_add")
GIMMICKS = ("nonlethal", "ncpd", "drag", "doll", "terminal")
TRAITS = ("cheap_chrome", "evade", "doll", "fast", "regen")
DOLL_MODES = ("cling", "rampage", "reboot")
FIREWALL_OPTIONS = ("cover", "reboot", "aim", "scan", "maxdoc")
ESCAPE_ACTIONS = ("hide", "move", "dead")           # 숨기 / 이동 / 죽은 척
POLICE_ACTIONS = ("light", "thermal", "callout")   # 서치라이트 / 열감지 / 확성기 검문
# 플레이어 행동 → 이기는 경찰 행동 / 지는 경찰 행동 (나머지는 무승부)
ESCAPE_BEATS = {"hide": "light", "move": "thermal", "dead": "callout"}
ESCAPE_LOSES = {"hide": "thermal", "move": "callout", "dead": "light"}
PASS_CARD, BREAK_CARD = "pass_terminal", "break_free"


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
class Combo:
    cond: str          # target_has | self_has
    mod: str           # 수정자 종류
    bonus: str         # dmg_x | dmg_add | chip_add
    value: float = 0.0


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
    tags: List[str] = field(default_factory=list)
    combo: Optional[Combo] = None

    def has(self, effect_kind: str) -> bool:
        return any(e.kind == effect_kind for e in self.effects)

    def effect(self, effect_kind: str) -> Optional[Effect]:
        for e in self.effects:
            if e.kind == effect_kind:
                return e
        return None

    def tagged(self, tag: str) -> bool:
        return tag in self.tags


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
    traits: Dict[str, float] = field(default_factory=dict)
    color: str = ""


@dataclass
class BattleDef:
    id: str
    name: str
    enemies: List[str]          # 펼쳐진 enemy_id 목록 (중복 포함)
    desc: str = ""
    gimmicks: List[str] = field(default_factory=list)
    overrides: Dict[str, str] = field(default_factory=dict)
    players: str = ""           # 표시용 (권장 인원)


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
    job: str = ""
    color: str = ""
    traits: Dict[str, float] = field(default_factory=dict)
    looted: bool = False
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

    def has_trait(self, name: str) -> bool:
        return name in self.traits


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
    round: int = 0
    host_id: str = ""
    players: Dict[str, Combatant] = field(default_factory=dict)
    enemies: Dict[str, Combatant] = field(default_factory=dict)
    cards: Dict[str, Card] = field(default_factory=dict)
    config: Dict[str, Any] = field(default_factory=dict)
    gimmicks: List[str] = field(default_factory=list)
    hands: Dict[str, List[str]] = field(default_factory=dict)
    choices: Dict[str, Choice] = field(default_factory=dict)
    enemy_choices: Dict[str, Choice] = field(default_factory=dict)
    intents: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    meters: Dict[str, Any] = field(default_factory=dict)
    round_flags: Dict[str, Any] = field(default_factory=dict)
    round_events: List[Dict[str, Any]] = field(default_factory=list)
    escape: Dict[str, Any] = field(default_factory=dict)
    last_player_kinds: List[str] = field(default_factory=list)
    log: List[Dict[str, Any]] = field(default_factory=list)
    pending_reports: List[Dict[str, Any]] = field(default_factory=list)
    winner: Optional[str] = None
    end_reason: str = ""
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
        return self.round

    def has(self, gimmick: str) -> bool:
        return gimmick in self.gimmicks

    def holder(self) -> Optional[Combatant]:
        hid = self.meters.get("terminal_holder")
        return self.players.get(str(hid)) if hid else None

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
        st.enemy_choices = {k: Choice(**v) for k, v in (d.get("enemy_choices") or {}).items()}
        st.hands = {k: list(v) for k, v in (d.get("hands") or {}).items()}
        return st


def _combatant_from(d: Dict[str, Any]) -> Combatant:
    d = dict(d)
    d["modifiers"] = [Modifier(**m) for m in d.get("modifiers") or []]
    return Combatant(**d)


def card_from_dict(d: Dict[str, Any]) -> Card:
    d = dict(d)
    d["effects"] = [Effect(**e) for e in d.get("effects") or []]
    d["combo"] = Combo(**d["combo"]) if d.get("combo") else None
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


def _text(row: Any, key: str) -> str:
    if row is None:
        return ""
    try:
        v = row.get(key) if hasattr(row, "get") else row[key]
    except (KeyError, IndexError, TypeError):
        return ""
    s = "" if v is None else str(v).strip()
    return "" if s.lower() == "nan" else s


def player_combatant(uid: str, row: Any, display_name: str, config: Dict[str, Any], is_host: bool = False) -> Combatant:
    """Characters 시트 행 → 전투원. 시트 열 매핑은 여기 한 곳에만 둔다.

    row 는 pd.Series / dict / None. 이름은 디스코드 서버 닉네임(display_name)을 쓴다.
    `job` 과 `color`(#RRGGBB) 열은 표시용으로만 싣는다.
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
        pool=ALLY, is_host=is_host, job=_text(row, "job"), color=_text(row, "color"),
    )


_SUFFIX = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def enemy_combatants(bdef: BattleDef, defs: Dict[str, EnemyDef], hp_mult: float = 1.0) -> List[Combatant]:
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
        hp = max(1, int(round(d.max_hp * float(hp_mult))))
        out.append(Combatant(
            id=f"{eid}#{n + 1}", name=name, side=ENEMY, max_hp=hp, hp=hp,
            physics=d.physics, magic=d.magic, agility=d.agility, charm=d.charm,
            personality=d.personality, pool=d.pool or ENEMY, traits=dict(d.traits), color=d.color,
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
    gimmicks: Sequence[str] = (),
    now: Optional[float] = None,
    rng: Rng = DEFAULT_RNG,
) -> BattleState:
    if not players:
        raise RuleError("NO_PLAYERS")
    if not enemies:
        raise RuleError("NO_ENEMIES")
    for g in gimmicks:
        if g not in GIMMICKS:
            raise RuleError("BAD_GIMMICK")
    st = BattleState(
        status=ROUND_OPEN, battle_id=battle_id, battle_name=battle_name, round=1,
        host_id=str(host_id), cards=dict(cards), config=dict(config), gimmicks=list(gimmicks),
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
    if st.has("terminal") and PASS_CARD not in cards:
        raise RuleError("NO_FIXED_CARD")
    if st.has("doll") and BREAK_CARD not in cards:
        raise RuleError("NO_FIXED_CARD")
    st.meters = {"firewall": 0, "firewall_acc": 0.0}
    if st.has("ncpd"):
        st.meters["ncpd"] = 0.0
    if st.has("drag"):
        st.meters["drag"] = {}
    if st.has("doll"):
        st.meters["chip"] = int(config.get("chip_stability", 5))
        st.meters["chip_max"] = st.meters["chip"]
        st.meters["doll_mode"] = ""
    if st.has("terminal"):
        hid = str(host_id) if str(host_id) in st.players else next(iter(st.players))
        st.meters["terminal_holder"] = hid
        st.meters["screen"] = int(config.get("screen_durability", 3))
        st.meters["screen_max"] = st.meters["screen"]
    open_round(st, rng)
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


def is_grappled(c: Combatant) -> bool:
    return c.mod("grapple") is not None


def fixed_cards(st: BattleState, c: Combatant) -> List[str]:
    """규칙이 쥐어주는 카드. 붙잡혔으면 떨쳐내기만, 단말기 소지자는 넘기기가 추가된다."""
    if is_grappled(c):
        return [BREAK_CARD] if BREAK_CARD in st.cards else []
    out: List[str] = []
    if st.has("terminal") and st.meters.get("terminal_holder") == c.id and PASS_CARD in st.cards:
        if len(st.alive_units(ALLY)) > 1:
            out.append(PASS_CARD)
    return out


def draw_hand(st: BattleState, uid: str, rng: Rng = DEFAULT_RNG) -> List[str]:
    """이번 라운드의 패. 이미 뽑았으면 그대로. 주최자는 풀 전체(저장하지 않음)."""
    c = st.players.get(uid)
    if c is None:
        raise RuleError("NOT_PLAYER")
    if is_grappled(c):
        return fixed_cards(st, c)
    if admin_sees_all(st, c):
        return [card.id for card in pool_for(st, c)] + fixed_cards(st, c)
    if uid not in st.hands:
        st.hands[uid] = draw_from_pool(pool_for(st, c), int(st.config.get("hand_size", 3)), rng)
    return list(st.hands[uid]) + fixed_cards(st, c)


def hand_for(st: BattleState, uid: str) -> List[str]:
    c = st.players.get(uid)
    if c is None:
        return []
    if is_grappled(c):
        return fixed_cards(st, c)
    if admin_sees_all(st, c):
        return [card.id for card in pool_for(st, c)] + fixed_cards(st, c)
    return list(st.hands.get(uid, [])) + fixed_cards(st, c)


# ---------------------------------------------------------------------------
# 대상과 제출
# ---------------------------------------------------------------------------
def needs_target(card: Card) -> bool:
    return card.target in ("enemy", "ally")


def opposite(side: str) -> str:
    return ENEMY if side == ALLY else ALLY


def can_revive(card: Card) -> bool:
    return card.has("heal")


def valid_targets(st: BattleState, actor: Combatant, card: Card) -> List[Combatant]:
    if card.target == "enemy":
        return st.alive_units(opposite(actor.side))
    if card.target == "ally":
        if card.has(PASS_CARD):
            return [u for u in st.alive_units(actor.side) if u.id != actor.id]
        units = st.alive_units(actor.side)
        if can_revive(card) and actor.side == ALLY:
            units = units + [u for u in st.side_units(actor.side) if not u.alive() and not u.looted]
        return units
    if card.target == "self":
        return [actor]
    return []


def is_stunned(c: Combatant) -> bool:
    return c.mod("stun") is not None


def submit_choice(st: BattleState, uid: str, card_id: str, target_id: Optional[str] = None,
                  now: Optional[float] = None) -> Choice:
    if st.status != ROUND_OPEN:
        raise RuleError("NOT_OPEN")
    actor = st.players.get(uid)
    if actor is None:
        raise RuleError("NOT_PLAYER")
    if not actor.alive():
        raise RuleError("DEAD")
    if is_stunned(actor):
        raise RuleError("STUNNED")
    if card_id not in hand_for(st, uid) or card_id not in st.cards:
        raise RuleError("GRAPPLED" if is_grappled(actor) else "NOT_IN_HAND")
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
    if st.status != ROUND_OPEN:
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
# 라운드 열기 — 적이 먼저 정하고 종류를 공개한다
# ---------------------------------------------------------------------------
Chooser = Callable[[BattleState, Combatant, List[str], Rng], Optional[Choice]]


def _doll_pick_mode(st: BattleState, rng: Rng) -> Tuple[str, str]:
    """config doll_modes = `mode:card:weight;...` → (mode, card_id). 리부트는 연속으로 나오지 않는다."""
    entries: List[Tuple[str, str, float]] = []
    for chunk in str(st.config.get("doll_modes", "")).split(";"):
        parts = [p.strip() for p in chunk.split(":")]
        if len(parts) >= 2 and parts[0] in DOLL_MODES and parts[1] in st.cards:
            w = float(parts[2]) if len(parts) > 2 and parts[2] else 1.0
            entries.append((parts[0], parts[1], w))
    if not entries:
        return "", ""
    prev = st.meters.get("doll_mode", "")
    cands = [e for e in entries if not (e[0] == "reboot" and prev == "reboot")] or entries
    pick = rng.choices(cands, weights=[e[2] for e in cands], k=1)[0]
    return pick[0], pick[1]


def _doll_target(st: BattleState, me: Combatant, card: Card, rng: Rng) -> Optional[str]:
    if card.target == "self":
        return me.id
    if card.target != "enemy":
        return None
    h = st.holder()
    if h is not None and h.alive():
        return h.id
    alive = st.alive_units(ALLY)
    return rng.choice(alive).id if alive else None


def open_round(st: BattleState, rng: Rng = DEFAULT_RNG, chooser: Optional[Chooser] = None) -> None:
    """적의 선택을 정하고(의도 공개) 플레이어 선택을 받기 시작한다."""
    if chooser is None:
        from . import ai  # 순환 import 회피
        chooser = ai.choose
    st.hands, st.choices, st.enemy_choices, st.intents = {}, {}, {}, {}
    st.round_flags = {}
    st.round_events = []
    k = int(st.config.get("hand_size", 3))
    for e in st.enemies.values():
        if not e.alive():
            continue
        if is_stunned(e):
            st.intents[e.id] = {"kind": "stunned", "label": "stunned", "target": None, "card_id": ""}
            continue
        if e.has_trait("doll") and st.has("doll"):
            mode, cid = _doll_pick_mode(st, rng)
            if cid:
                st.meters["doll_mode"] = mode
                card = st.cards[cid]
                st.enemy_choices[e.id] = Choice(e.id, cid, _doll_target(st, e, card, rng))
                st.intents[e.id] = {"kind": "mode", "label": mode, "target": st.enemy_choices[e.id].target_id,
                                    "card_id": cid}
                continue
        hand = draw_from_pool(pool_for(st, e), k, rng)
        ch = chooser(st, e, hand, rng) if hand else None
        if ch is None:
            st.intents[e.id] = {"kind": "wait", "label": "wait", "target": None, "card_id": ""}
            continue
        st.enemy_choices[e.id] = ch
        st.intents[e.id] = {"kind": st.cards[ch.card_id].kind, "label": st.cards[ch.card_id].kind,
                            "target": ch.target_id, "card_id": ch.card_id}
    st.status = ROUND_OPEN


def public_intents(st: BattleState) -> List[Dict[str, Any]]:
    """턴 메시지에 보여줄 적 의도. 대상은 스캔했거나 show_intent_target 일 때만."""
    show_t = bool(st.config.get("show_intent_target")) or bool(st.round_flags.get("scan"))
    out = []
    for eid, it in st.intents.items():
        e = st.enemies.get(eid)
        if e is None or not e.alive():
            continue
        t = st.unit(it.get("target")) if it.get("target") else None
        out.append({"id": eid, "name": e.name, "kind": it["kind"], "label": it["label"],
                    "target": (t.name if (t is not None and show_t) else None)})
    return out


# ---------------------------------------------------------------------------
# 방화벽 (팀 방어 게이지)
# ---------------------------------------------------------------------------
def firewall_cost(st: BattleState, option: str) -> int:
    return int(st.config.get(f"firewall_cost_{option}", 99))


def _firewall_add(st: BattleState, amount: float, events: Optional[List[Dict[str, Any]]] = None, why: str = "") -> None:
    mx = int(st.config.get("firewall_max", 9))
    before = int(st.meters.get("firewall", 0))
    acc = float(st.meters.get("firewall_acc", 0.0)) + float(amount)
    gained = int(acc)
    st.meters["firewall_acc"] = acc - gained
    st.meters["firewall"] = min(mx, before + gained)
    if events is not None and st.meters["firewall"] != before:
        events.append({"kind": "firewall_gain", "amount": st.meters["firewall"] - before, "why": why,
                       "value": st.meters["firewall"]})


def use_firewall(st: BattleState, uid: str, option: str, rng: Rng = DEFAULT_RNG) -> Dict[str, Any]:
    if st.status != ROUND_OPEN:
        raise RuleError("NOT_OPEN")
    actor = st.players.get(uid)
    if actor is None:
        raise RuleError("NOT_PLAYER")
    if not actor.alive():
        raise RuleError("DEAD")
    if option not in FIREWALL_OPTIONS:
        raise RuleError("BAD_OPTION")
    if st.round_flags.get("firewall_used"):
        raise RuleError("FIREWALL_USED")
    cost = firewall_cost(st, option)
    if int(st.meters.get("firewall", 0)) < cost:
        raise RuleError("FIREWALL_POOR")
    ev: Dict[str, Any] = {"kind": "firewall_use", "option": option, "cost": cost, "by": actor.name, "targets": []}
    if option == "cover":
        v = float(st.config.get("firewall_cover_shield", 0.5))
        for p in st.alive_units(ALLY):
            add_modifier(p, Modifier("shield", v, 1, st.round, source="firewall"))
            ev["targets"].append(p.name)
    elif option == "reboot":
        for p in st.alive_units(ALLY):
            before = len(p.modifiers)
            p.modifiers = [m for m in p.modifiers if m.kind == "shield"]
            if len(p.modifiers) != before:
                ev["targets"].append(p.name)
    elif option == "aim":
        st.round_flags["steady_aim"] = True
    elif option == "scan":
        st.round_flags["scan"] = True
    elif option == "maxdoc":
        cands = [p for p in st.players.values() if not p.looted and p.hp < p.max_hp]
        if not cands:
            raise RuleError("NO_TARGET")
        t = min(cands, key=lambda p: p.hp_ratio())
        amt = max(1, int(round(t.max_hp * float(st.config.get("firewall_maxdoc_ratio", 0.3)))))
        revived = not t.alive()
        t.hp = min(t.max_hp, t.hp + amt)
        if revived:
            _stop_drag(st, t.id)
        ev["targets"].append(t.name)
        ev["amount"] = amt
        ev["revived"] = revived
    st.meters["firewall"] = int(st.meters.get("firewall", 0)) - cost
    st.round_flags["firewall_used"] = True
    ev["value"] = st.meters["firewall"]
    st.round_events.append(ev)
    return ev


# ---------------------------------------------------------------------------
# 정산
# ---------------------------------------------------------------------------
def stat_bonus(actor: Combatant, stat: str, config: Dict[str, Any]) -> int:
    div = max(1, int(config.get("stat_divisor", 2)))
    if stat == "physics":
        return actor.physics // div
    if stat == "magic":
        return actor.magic // div
    return 0


def damage_range(card: Card, actor: Combatant, config: Dict[str, Any]) -> Tuple[int, int]:
    """UI 용 예상 피해 (수정자 미반영). damage 효과가 없으면 (0, 0)."""
    e = card.effect("damage")
    if e is None:
        return 0, 0
    base = int(e.value) + stat_bonus(actor, card.stat, config)
    return base, base + int(config.get("damage_variance", 4))


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
        ev = target.traits.get("evade") if target.traits else None
        if ev and card.stat == "physics":
            dmg *= max(0.0, 1.0 - float(ev))
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


def combo_applies(st: BattleState, actor: Combatant, card: Card, target: Optional[Combatant]) -> bool:
    cb = card.combo
    if cb is None:
        return False
    if cb.cond == "target_has":
        return target is not None and target.mod(cb.mod) is not None
    if cb.cond == "self_has":
        return actor.mod(cb.mod) is not None
    return False


def _release_grapple(st: BattleState, source_id: str, line: Dict[str, Any]) -> None:
    for p in st.players.values():
        g = p.mod("grapple")
        if g is not None and g.source == source_id:
            p.remove_mod("grapple")
            line.setdefault("released", []).append(p.name)


def _stop_drag(st: BattleState, pid: str) -> None:
    drag = st.meters.get("drag")
    if isinstance(drag, dict):
        drag.pop(pid, None)


def _apply_card(st: BattleState, actor: Combatant, card: Card, chosen_target: Optional[str],
                rng: Rng, events: List[Dict[str, Any]]) -> Dict[str, Any]:
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
    elif card.has("noop") and len(card.effects) == 1:
        line["result"] = "noop"
    elif card.has(BREAK_CARD):
        line["result"] = "break_free"
    elif card.has(PASS_CARD):
        line["result"] = "pass"

    if actor.side == ALLY and card.kind == "defend":
        _firewall_add(st, float(cfg.get("firewall_per_defend", 1)), events, "defend")
    if card.tagged("loud") and st.has("ncpd") and actor.side == ALLY:
        _ncpd_add(st, float(cfg.get("ncpd_loud", 0.5)), events, "loud", actor.name)

    # 대상 확정 (고른 대상이 죽었으면 생존자 중 무작위)
    if card.target in ("enemy", "ally"):
        cands = valid_targets(st, actor, card)
        t = st.unit(chosen_target) if chosen_target else None
        if t is None or t not in cands:
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
        # 회피 (물리 공격 한정, 스마트 탄 유도 중엔 무시)
        if len(targets) == 1 and card.stat == "physics" and not (actor.side == ALLY and st.round_flags.get("steady_aim")):
            ev = targets[0].traits.get("evade") if targets[0].traits else None
            if ev and rng.random() < float(ev):
                line["result"] = "evade"
                line["targets"].append(_tinfo(targets[0], "evade", 0))
                return line

    single = targets[0] if len(targets) == 1 else None
    if combo_applies(st, actor, card, single):
        line["combo"] = True

    for eff in card.effects:
        if eff.chance < 1.0 and rng.random() >= eff.chance:
            continue
        handler = HANDLERS.get(eff.kind)
        if handler is None:
            continue
        handler(st, actor, card, eff, targets, line, rng, events)
    return line


def _ncpd_add(st: BattleState, amount: float, events: List[Dict[str, Any]], why: str, who: str = "") -> None:
    if not st.has("ncpd") or amount <= 0:
        return
    mx = float(st.config.get("ncpd_max", 3.0))
    before = float(st.meters.get("ncpd", 0.0))
    st.meters["ncpd"] = min(mx, before + amount)
    events.append({"kind": "ncpd", "amount": amount, "why": why, "who": who, "value": st.meters["ncpd"], "max": mx})


def _h_damage(st, actor, card, eff, targets, line, rng, events):
    cfg = st.config
    steady = actor.side == ALLY and bool(st.round_flags.get("steady_aim"))
    for t in targets:
        var = 0 if steady else rng.randint(0, int(cfg.get("damage_variance", 4)))
        dmg = eff.value + stat_bonus(actor, card.stat, cfg) + var
        if line.get("combo") and card.combo is not None:
            if card.combo.bonus == "dmg_x":
                dmg *= float(card.combo.value)
            elif card.combo.bonus == "dmg_add":
                dmg += float(card.combo.value)
        ad = actor.mod("atk_down")
        if ad:
            dmg *= max(0.0, 1.0 - ad.value)
        sh = t.mod("shield")
        if sh:
            dmg *= max(0.0, 1.0 - sh.value)
        dmg = max(0, int(dmg))
        before = t.hp
        t.hp = max(0, t.hp - dmg)
        info = _tinfo(t, "damage", dmg)
        # 과잉 제압 (비살상 전투, 적 대상)
        if st.has("nonlethal") and t.side == ENEMY and actor.side == ALLY and before > 0 and t.hp == 0:
            lethal_card = card.tagged("lethal")
            doll_ok = t.has_trait("doll") and bool(cfg.get("doll_hp_overkill_ok"))
            if (lethal_card or dmg - before >= int(cfg.get("overkill_margin", 10))) and not doll_ok \
                    and not card.tagged("nonlethal"):
                info["overkill"] = True
                _ncpd_add(st, float(cfg.get("ncpd_overkill", 1.0)), events, "overkill", actor.name)
        if t.side == ALLY and dmg > 0:
            _firewall_add(st, dmg / max(1.0, float(cfg.get("firewall_per_damage", 10))), events, "damage")
            if before > 0 and t.hp == 0:
                _firewall_add(st, float(cfg.get("firewall_per_down", 2)), events, "down")
            if st.has("terminal") and st.meters.get("terminal_holder") == t.id and int(st.meters.get("screen", 0)) > 0:
                st.meters["screen"] = int(st.meters["screen"]) - 1
                info["screen"] = st.meters["screen"]
        if t.side == ENEMY and actor.side == ALLY and dmg > 0 and t.has_trait("doll"):
            _release_grapple(st, t.id, line)
        line["targets"].append(info)


def _h_self_damage(st, actor, card, eff, targets, line, rng, events):
    dmg = max(0, int(eff.value))
    actor.hp = max(0, actor.hp - dmg)
    line["targets"].append(_tinfo(actor, "self_damage", dmg))


def _h_heal(st, actor, card, eff, targets, line, rng, events):
    for t in targets:
        amt = int(eff.value + stat_bonus(actor, card.stat, st.config))
        was_down = not t.alive()
        healed = min(t.max_hp, t.hp + amt) - t.hp
        t.hp += healed
        info = _tinfo(t, "heal", healed)
        if was_down and t.alive():
            info["revived"] = True
            _stop_drag(st, t.id)
        line["targets"].append(info)


def _hack_value(st: BattleState, target: Combatant, eff: Effect) -> float:
    v = float(eff.value)
    if target.has_trait("cheap_chrome") and eff.kind in ("atk_down", "misfire"):
        v = min(0.9, v * float(st.config.get("hack_weak_mult", 1.5)))
    return v


def _h_modifier(st, actor, card, eff, targets, line, rng, events):
    stun_bonus = 0
    for t in targets:
        turns = max(1, int(eff.turns))
        value = _hack_value(st, t, eff) if card.kind == "hack" else eff.value
        if eff.kind == "stun" and t.has_trait("doll") and st.has("doll"):
            stun_bonus += int(st.config.get("chip_stun_bonus", 1))      # 돌은 기절 대신 칩이 더 깎인다
            line["targets"].append(_tinfo(t, "stun_immune", 0))
            continue
        add_modifier(t, Modifier(kind=eff.kind, value=value, turns_left=turns,
                                 created_turn=st.round, source=actor.id))
        info = _tinfo(t, eff.kind, value)
        info["turns"] = turns
        if t.has_trait("cheap_chrome") and value != eff.value:
            info["weak"] = True
        line["targets"].append(info)
    if card.kind == "hack":
        _chip_hit(st, actor, card, targets, line, events, extra=stun_bonus)


def _chip_damage(st: BattleState, n: int) -> int:
    """라운드 상한(chip_round_cap, 리부트 중 ×chip_reboot_mult)을 적용해 칩을 깎고 실제 감소량을 돌려준다."""
    cap = int(st.config.get("chip_round_cap", 2))
    if st.meters.get("doll_mode") == "reboot":
        cap *= int(st.config.get("chip_reboot_mult", 2))
    used = int(st.round_flags.get("chip_used", 0))
    n = max(0, min(n, cap - used)) if cap > 0 else n
    before = int(st.meters.get("chip", 0))
    st.meters["chip"] = max(0, before - n)
    st.round_flags["chip_used"] = used + (before - st.meters["chip"])
    return before - st.meters["chip"]


def _chip_hit(st, actor, card, targets, line, events, extra: int = 0):
    """해킹 카드가 돌에 명중 → 칩 안정도 감소."""
    if not st.has("doll") or actor.side != ALLY:
        return
    for t in targets:
        if not t.has_trait("doll"):
            continue
        n = int(st.config.get("chip_per_hack", 1)) + extra
        if st.meters.get("doll_mode") == "reboot":
            n *= int(st.config.get("chip_reboot_mult", 2))
        if line.get("combo") and card.combo is not None and card.combo.bonus == "chip_add":
            n += int(card.combo.value)
        dealt = _chip_damage(st, n)
        line["chip"] = {"amount": dealt, "value": st.meters["chip"], "max": st.meters.get("chip_max", 0),
                        "capped": dealt < n}


def _h_chip(st, actor, card, eff, targets, line, rng, events):
    """명시적 chip 효과 (해킹 카드가 아니어도 칩을 깎는다)."""
    if not st.has("doll"):
        return
    for t in targets:
        if not t.has_trait("doll"):
            continue
        dealt = _chip_damage(st, int(eff.value))
        line["chip"] = {"amount": dealt, "value": st.meters["chip"], "max": st.meters.get("chip_max", 0),
                        "capped": dealt < int(eff.value)}


def _h_grapple(st, actor, card, eff, targets, line, rng, events):
    for t in targets:
        if t.side == actor.side:
            continue
        add_modifier(t, Modifier("grapple", 1.0, 99, st.round, source=actor.id))
        info = _tinfo(t, "grapple", 1.0)
        line["targets"].append(info)


def _h_break_free(st, actor, card, eff, targets, line, rng, events):
    had = actor.mod("grapple") is not None
    actor.remove_mod("grapple")
    line["targets"].append(_tinfo(actor, "break_free", 1.0 if had else 0.0))


def _h_pass_terminal(st, actor, card, eff, targets, line, rng, events):
    if not st.has("terminal"):
        return
    for t in targets:
        if t.side != actor.side or not t.alive():
            continue
        st.meters["terminal_holder"] = t.id
        line["targets"].append(_tinfo(t, "pass_terminal", 0))
        # 소지자가 바뀌면 돌의 매달리기 대상도 바뀐다 (붙잡힘은 유지)
        break


def _h_noop(st, actor, card, eff, targets, line, rng, events):
    return


HANDLERS: Dict[str, Callable[..., None]] = {
    "damage": _h_damage,
    "self_damage": _h_self_damage,
    "heal": _h_heal,
    "shield": _h_modifier,
    "atk_down": _h_modifier,
    "misfire": _h_modifier,
    "redirect_random": _h_modifier,
    "stun": _h_modifier,
    "grapple": _h_grapple,
    "break_free": _h_break_free,
    "pass_terminal": _h_pass_terminal,
    "chip": _h_chip,
    "noop": _h_noop,
}


def tick_modifiers(st: BattleState) -> None:
    for u in st.players.values():
        _tick_unit(u)
    for u in st.enemies.values():
        _tick_unit(u)


def _tick_unit(u: Combatant) -> None:
    kept: List[Modifier] = []
    for m in u.modifiers:
        if m.kind in TICKING:
            m.turns_left -= 1
        if m.turns_left > 0:
            kept.append(m)
    u.modifiers = kept
    if not u.alive():
        u.modifiers = []


def _advance_drag(st: BattleState, rng: Rng, events: List[Dict[str, Any]]) -> None:
    if not st.has("drag"):
        return
    drag: Dict[str, Any] = st.meters.setdefault("drag", {})
    alive_enemies = st.alive_units(ENEMY)
    need = int(st.config.get("drag_turns", 2))
    for pid, info in list(drag.items()):
        p = st.players.get(pid)
        by = st.enemies.get(info.get("by", ""))
        if p is None or p.alive() or p.looted:
            drag.pop(pid, None)
            continue
        if by is None or not by.alive():
            drag.pop(pid, None)
            events.append({"kind": "drag_stop", "player": p.name, "by": by.name if by else "?"})
            continue
        info["turns"] = int(info.get("turns", 0)) + 1
        if info["turns"] >= need:
            p.looted = True
            drag.pop(pid, None)
            events.append({"kind": "looted", "player": p.name, "by": by.name})
        else:
            events.append({"kind": "drag", "player": p.name, "by": by.name, "turns": info["turns"], "need": need})
    if not alive_enemies:
        return
    for p in st.players.values():
        if p.alive() or p.looted or p.id in drag:
            continue
        by = rng.choice(alive_enemies)
        drag[p.id] = {"by": by.id, "turns": 0}
        events.append({"kind": "drag_start", "player": p.name, "by": by.name, "need": need})


def resolve_round(st: BattleState, rng: Rng = DEFAULT_RNG, chooser: Optional[Chooser] = None) -> Dict[str, Any]:
    """양편 동시 정산. 끝나지 않았으면 다음 라운드를 바로 연다 (open_round)."""
    if st.status != ROUND_OPEN:
        raise RuleError("NOT_OPEN")
    st.status = RESOLVING
    events: List[Dict[str, Any]] = list(st.round_events)
    lines: List[Dict[str, Any]] = []
    st.last_player_kinds = [st.cards[c.card_id].kind for c in st.choices.values() if c.card_id in st.cards]

    actions: Dict[str, Optional[Choice]] = {}
    for p in st.players.values():
        actions[p.id] = st.choices.get(p.id)
    for e in st.enemies.values():
        actions[e.id] = st.enemy_choices.get(e.id)

    ordered: List[Tuple[int, int, float, Combatant]] = []
    waits: List[Combatant] = []
    for u in st.alive_units():
        ch = actions.get(u.id)
        if is_stunned(u):
            ordered.append((-1, -u.agility, rng.random(), u))     # 기절은 맨 앞에 알린다
            continue
        if ch is None or ch.card_id not in st.cards:
            waits.append(u)
            continue
        ordered.append((PHASE.get(st.cards[ch.card_id].kind, 2), -u.agility, rng.random(), u))
    ordered.sort(key=lambda x: (x[0], x[1], x[2]))

    acted: set = set()
    for phase, _, _, u in ordered:
        if not u.alive():
            lines.append(_plain_line(u, "down"))
            continue
        if is_stunned(u):
            u.remove_mod("stun")
            lines.append(_plain_line(u, "stunned"))
            continue
        ch = actions[u.id]
        assert ch is not None
        if u.side == ENEMY and u.has_trait("doll") and st.has("doll") and st.meters.get("doll_mode") == "reboot":
            lines.append(_plain_line(u, "reboot"))
            acted.add(u.id)
            continue
        lines.append(_apply_card(st, u, st.cards[ch.card_id], ch.target_id, rng, events))
        acted.add(u.id)
    for u in waits:
        if u.alive():                      # 정산 중 쓰러진 유닛의 '대기' 는 알리지 않는다
            lines.append(_plain_line(u, "wait"))

    tick_modifiers(st)
    _regen(st, events)
    _advance_drag(st, rng, events)

    # 종료 판정
    w: Optional[str] = None
    reason = ""
    if st.has("doll") and int(st.meters.get("chip", 1)) <= 0:
        w, reason = ALLY, "chip"
    elif st.has("ncpd") and float(st.meters.get("ncpd", 0.0)) >= float(st.config.get("ncpd_max", 3.0)):
        reason = "ncpd"
    else:
        w = winner(st)
        if w == ALLY:
            reason = "ko"
        elif w == ENEMY:
            reason = "wipe"

    report = {
        "round_no": st.round, "lines": lines, "events": events,
        "hp": {u.id: [u.hp, u.max_hp] for u in list(st.players.values()) + list(st.enemies.values())},
        "meters": dict(st.meters), "winner": w, "reason": reason,
    }
    st.log.append(report)
    st.round_events = []
    if reason == "ncpd":
        start_escape(st, rng)
    elif w:
        st.winner = w
        st.end_reason = reason
        st.status = ENDED
    else:
        st.round += 1
        open_round(st, rng, chooser)
    return report


def _regen(st: BattleState, events: List[Dict[str, Any]]) -> None:
    """regen 특성: 라운드 끝에 HP 회복 (쓰러진 적은 회복하지 않는다)."""
    for u in st.alive_units(ENEMY):
        r = int(u.traits.get("regen", 0)) if u.traits else 0
        if r > 0 and u.hp < u.max_hp:
            healed = min(u.max_hp, u.hp + r) - u.hp
            u.hp += healed
            events.append({"kind": "regen", "name": u.name, "amount": healed, "hp": u.hp, "max_hp": u.max_hp})


def _plain_line(u: Combatant, result: str) -> Dict[str, Any]:
    return {"actor_id": u.id, "actor_name": u.name, "side": u.side, "card_id": "", "card_name": "",
            "kind": "", "result": result, "targets": []}


# ---------------------------------------------------------------------------
# 도주 단계 — 가위바위보 (숨기 / 이동 / 죽은 척 vs 서치라이트 / 열감지 / 확성기)
# ---------------------------------------------------------------------------
def start_escape(st: BattleState, rng: Rng = DEFAULT_RNG) -> None:
    st.status = ESCAPE
    st.hands, st.choices, st.enemy_choices, st.intents = {}, {}, {}, {}
    st.escape = {
        "turn": 1,
        "max_turns": int(st.config.get("escape_turns", 5)),
        "alert": int(st.config.get("escape_wins_needed", 3)),
        "alert_max": int(st.config.get("escape_wins_needed", 3)),
        "choices": {},
        "history": [],
        "police": "",
        "hint": "",
    }
    if not st.alive_units(ALLY):          # 전원 쓰러진 상태로 드론이 오면 바로 잡힌다
        st.winner, st.end_reason, st.status = CAUGHT, "caught_down", ENDED
        return
    _escape_prepare_turn(st, rng)


def _escape_prepare_turn(st: BattleState, rng: Rng) -> None:
    """경찰 행동을 미리 정하고, escape_hint 면 쓰지 않을 행동 하나를 공개한다."""
    es = st.escape
    es["police"] = rng.choice(POLICE_ACTIONS)
    if st.config.get("escape_hint", True):
        others = [a for a in POLICE_ACTIONS if a != es["police"]]
        es["hint"] = rng.choice(others)
    else:
        es["hint"] = ""


def safe_escape_action(hint: str) -> Optional[str]:
    """힌트(쓰지 않을 경찰 행동)가 있을 때 절대 지지 않는 플레이어 행동."""
    if not hint:
        return None
    remaining = [a for a in POLICE_ACTIONS if a != hint]
    for act in ESCAPE_ACTIONS:
        if ESCAPE_LOSES[act] not in remaining:
            return act
    return None


def escape_players(st: BattleState) -> List[Combatant]:
    return [p for p in st.players.values() if p.alive()]


def submit_escape(st: BattleState, uid: str, action: str) -> None:
    if st.status != ESCAPE:
        raise RuleError("NOT_ESCAPE")
    p = st.players.get(uid)
    if p is None:
        raise RuleError("NOT_PLAYER")
    if not p.alive():
        raise RuleError("DEAD")
    if action not in ESCAPE_ACTIONS:
        raise RuleError("BAD_OPTION")
    st.escape["choices"][uid] = action


def all_escape_chosen(st: BattleState) -> bool:
    return all(p.id in st.escape.get("choices", {}) for p in escape_players(st))


def escape_outcome(action: str, police: str) -> str:
    if ESCAPE_BEATS.get(action) == police:
        return "win"
    if ESCAPE_LOSES.get(action) == police:
        return "lose"
    return "tie"


def resolve_escape(st: BattleState, rng: Rng = DEFAULT_RNG, police: Optional[str] = None) -> Dict[str, Any]:
    if st.status != ESCAPE:
        raise RuleError("NOT_ESCAPE")
    es = st.escape
    police = police or es.get("police") or rng.choice(POLICE_ACTIONS)
    players = escape_players(st)
    results = []
    wins = 0
    for p in players:
        a = es["choices"].get(p.id)
        out = escape_outcome(a, police) if a else "none"
        wins += 1 if out == "win" else 0
        results.append({"id": p.id, "name": p.name, "action": a, "outcome": out})
    turn_won = wins * 2 > len(players) if players else False
    if turn_won:
        es["alert"] = max(0, int(es["alert"]) - 1)
    report = {
        "turn": es["turn"], "max_turns": es["max_turns"], "police": police, "hint": es.get("hint", ""), "results": results,
        "wins": wins, "n": len(players), "turn_won": turn_won, "alert": es["alert"], "alert_max": es["alert_max"],
        "winner": None,
    }
    es["history"].append(report)
    if es["alert"] <= 0:
        st.winner, st.end_reason, st.status = ESCAPED, "escaped", ENDED
        report["winner"] = ESCAPED
    elif es["turn"] >= es["max_turns"]:
        st.winner, st.end_reason, st.status = CAUGHT, "caught", ENDED
        report["winner"] = CAUGHT
    else:
        es["turn"] += 1
        es["choices"] = {}
        _escape_prepare_turn(st, rng)
    st.log.append({"escape": report})
    return report
