"""카드 전투 미니게임 — 순수 규칙 엔진 (Discord 의존성 없음).

라운드 단위로 진행한다. 라운드가 열리면(open_round) 적은 먼저 카드를 정하고 그 **종류만** 공개된다
(intents). 플레이어가 전원 고르면(또는 주최자가 강제하면) resolve_round 가 양편을 한 번에 정산한다.

정산 순서: 단계(해킹 0 → 방어 1 → 공격/특수 2) → 민첩 내림차순 → 난수. 양편이 섞여서 정산되므로
빠른 해커의 쇼크는 느린 적의 공격을 같은 라운드에 막는다. ("준비 동작이 먼저, 한 방은 마지막")

수정자(Modifier):
  - shield 는 라운드가 끝날 때 1 감소한다 (만든 라운드 포함). 즉 `shield:0.5:1` 은 이번 라운드의 공격 단계만 막는다.
  - atk_down / misfire / redirect_random / stun 은 사용될 때 소모되고 라운드로는 줄지 않는다 (ONE_SHOT).
    "방어는 이번 라운드, 해킹은 상대의 다음 행동" 한 문장으로 설명되도록 글리치도 다음 공격 1회다.
  - daemon(감염) 은 스택(value)을 더해 가며, 걸린 다음 라운드 끝까지 남는다 (TICKING, daemon_turns).
    감염된 채로 카드를 내면 스택당 daemon_damage 를 스스로 받는다 (_daemon_bite).
  - grapple(붙잡힘) 은 떨쳐내기, 아군의 명중, 방화벽 재부팅으로만 풀린다.

기믹(gimmicks, Battles.csv): nonlethal(과잉 제압 판정) / ncpd(경계도 → 도주 단계) / drag(쓰러진 아군 끌고 가기) /
doll(칩 안정도·모드) / terminal(단말기 소지자·액정) / first_strike(선제 라운드) / daemon(감염) /
firewall_duel(적이 방화벽을 깎는다 — 효과만으로 동작, 기믹은 안내용) / life_support(생명 유지 시계) /
ice(블랙 ICE 층) / tutorial(대본 전투).
팀 자원 '보느와의 방화벽' 은 항상 켜져 있다 (use_firewall). 옵션은 `firewall_options` 로 전투별 공개.

준비 단계(PREP): prep_enabled 면 new_battle 이 무기를 나눠 주고 금고를 균등 분배한 뒤 PREP 상태로 멈춘다.
gig.finish_prep → begin_combat 이 (선제 라운드를 치른 뒤) 1라운드를 연다.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------
IDLE, LOBBY, PREP, ROUND_OPEN, RESOLVING, ESCAPE, ENDED = (
    "IDLE", "LOBBY", "PREP", "ROUND_OPEN", "RESOLVING", "ESCAPE", "ENDED",
)
ALLY, ENEMY = "ally", "enemy"
ESCAPED, CAUGHT = "escaped", "caught"          # winner 값 (도주 단계 결과)
KINDS = ("attack", "defend", "hack", "special")
TARGETS = ("enemy", "ally", "self", "all_enemy", "all_ally")
STATS = ("physics", "magic", "none")
POOLS = ("ally", "enemy", "both", "fixed")     # fixed: 규칙이 손에 쥐어주는 카드 (드로우 안 됨)
EFFECT_KINDS = ("damage", "heal", "shield", "self_damage", "atk_down", "misfire", "redirect_random", "stun",
                "grapple", "break_free", "pass_terminal", "chip", "noop",
                "scatter_damage", "daemon", "cleanse", "firewall_drain", "firewall_lock", "ice", "expose", "field_mod")
COUNT_KINDS = ("scatter_damage", "daemon")     # 셋째 슬롯이 '지속'이 아니라 '대상 수'
MODIFIER_KINDS = ("shield", "atk_down", "misfire", "redirect_random", "stun", "grapple", "daemon")
ONE_SHOT = ("atk_down", "misfire", "redirect_random", "stun")
TICKING = ("shield", "daemon")
PHASE = {"hack": 0, "defend": 1, "attack": 2, "special": 2}
CARD_TAGS = ("loud", "lethal", "nonlethal")
COMBO_CONDS = ("target_has", "self_has")
COMBO_BONUSES = ("dmg_x", "dmg_add", "chip_add")
GIMMICKS = ("nonlethal", "ncpd", "drag", "doll", "terminal",
            "first_strike", "daemon", "firewall_duel", "life_support", "ice", "tutorial")
TRAITS = ("cheap_chrome", "evade", "doll", "fast", "regen", "modes")
DOLL_MODES = ("cling", "rampage", "reboot")
MODES = DOLL_MODES + ("seize", "breach", "overload")
FIREWALL_OPTIONS = ("cover", "reboot", "aim", "scan", "maxdoc")
ESCAPE_ACTIONS = ("hide", "move", "dead")           # 숨기 / 이동 / 죽은 척
POLICE_ACTIONS = ("light", "thermal", "callout")   # 서치라이트 / 열감지 / 확성기 검문
# 플레이어 행동 → 이기는 경찰 행동 / 지는 경찰 행동 (나머지는 무승부)
ESCAPE_BEATS = {"hide": "light", "move": "thermal", "dead": "callout"}
ESCAPE_LOSES = {"hide": "thermal", "move": "callout", "dead": "light"}
PASS_CARD, BREAK_CARD = "pass_terminal", "break_free"
FIELD_MOD_CARD = "field_mod"
BOSS_STATES = ("fine", "hurt", "critical")
TIP_KEYS = ("hack_cancel", "shield", "combo", "misfire", "redirect", "evade", "firewall_ready", "overkill",
            "drag", "grapple", "chip", "daemon", "daemon_bite", "ice_strip", "exposed", "life_low", "field_mod")


class RuleError(ValueError):
    """플레이어에게 그대로 보여줄 수 있는 규칙 위반. args[0] 이 코드."""

    @property
    def code(self) -> str:
        return str(self.args[0]) if self.args else ""


def split_list(text: Any) -> List[str]:
    """`;` 또는 `,` 로 나눈 목록. Battles.csv overrides 는 `;` 로 쪼개지므로 그 안의 목록은 `,` 로 쓴다."""
    out: List[str] = []
    for chunk in str(text or "").replace(",", ";").split(";"):
        chunk = chunk.strip()
        if chunk:
            out.append(chunk)
    return out


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
    count: int = 1          # scatter_damage / daemon: 무작위 대상 수


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

    def deals_damage(self) -> bool:
        return self.has("damage") or self.has("scatter_damage")


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
    weapon_grade: int = 0        # 0 = 무기 없음 (준비 단계가 없는 전투)
    weapon_name: str = ""

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
    prep: Dict[str, Any] = field(default_factory=dict)        # 준비 단계 (분배금·행동 수·준비 완료·굴림 기록)
    script: List[Dict[str, Any]] = field(default_factory=list) # 튜토리얼 대본 스냅샷
    prep_message_id: int = 0

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

    def mode_owner(self) -> Optional[Combatant]:
        """모드로 움직이는 적 (돌 또는 traits 에 modes 가 있는 적). 전투당 하나만 지원."""
        for e in self.enemies.values():
            if (e.has_trait("doll") and self.has("doll")) or e.has_trait("modes"):
                return e
        return None

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
        st.prep = dict(d.get("prep") or {})
        st.script = list(d.get("script") or [])
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


# ---------------------------------------------------------------------------
# 무기 (준비 단계에서 줍고, 전투가 끝나면 부서진다)
# ---------------------------------------------------------------------------
def weapon_bonus_table(config: Mapping[str, Any]) -> List[int]:
    out: List[int] = []
    for s in split_list(config.get("weapon_bonus", "0;3;6;10")):
        try:
            out.append(int(float(s)))
        except ValueError:
            out.append(0)
    return out or [0]


def weapon_bonus(c: Combatant, config: Mapping[str, Any]) -> int:
    """내 피해 카드에 더해지는 무기 등급 보너스 (아군 전용, 물리·RAM 공통)."""
    if c.side != ALLY or c.weapon_grade <= 0:
        return 0
    table = weapon_bonus_table(config)
    return table[min(c.weapon_grade, len(table)) - 1]


def weapon_label(c: Combatant) -> str:
    if c.weapon_grade <= 0:
        return ""
    return f"mk.{c.weapon_grade} {c.weapon_name}".strip()


def assign_weapon(c: Combatant, rng: Rng, names: Sequence[str], config: Mapping[str, Any]) -> None:
    start_max = max(1, int(config.get("weapon_start_max", 2)))
    c.weapon_grade = rng.randint(1, start_max)
    c.weapon_name = rng.choice(list(names)) if names else "무기"


def roll_upgrade(grade: int, rng: Rng, config: Mapping[str, Any], allow_down: bool = True) -> Dict[str, Any]:
    """뒷골목 개조 굴림. mk.1→2 는 무조건 성공, 그 위는 d10: ≤gamble_fail_max 실패(1 은 두 단계 하락), 10 은 성공+환불."""
    wmax = int(config.get("weapon_max", 4))
    if grade >= wmax:
        raise RuleError("WEAPON_MAX")
    if grade < 2:
        return {"roll": 0, "delta": 1, "refund": False, "success": True, "auto": True}
    roll = int(rng.randint(1, 10))
    fail_max = int(config.get("gamble_fail_max", 3))
    if roll <= fail_max:
        drop = int(config.get("gamble_fumble_drop", 2)) if roll == 1 else 1
        return {"roll": roll, "delta": -drop if allow_down else 0, "refund": False, "success": False, "auto": False}
    refund = roll == 10 and bool(config.get("gamble_crit_refund", True))
    return {"roll": roll, "delta": 1, "refund": refund, "success": True, "auto": False}


def clamp_grade(grade: int, config: Mapping[str, Any]) -> int:
    return max(1, min(int(config.get("weapon_max", 4)), grade))


def _names_for(p: Combatant, weapon_names: Optional[Mapping[str, Sequence[str]]]) -> Sequence[str]:
    if not weapon_names:
        return []
    return weapon_names.get(p.job) or weapon_names.get("") or []


def _start_prep(st: BattleState, rng: Rng, weapon_names: Optional[Mapping[str, Sequence[str]]], pot: Optional[int]) -> None:
    cfg = st.config
    for p in st.players.values():
        assign_weapon(p, rng, _names_for(p, weapon_names), cfg)
    n = max(1, len(st.players))
    if pot is None:
        pot = int(cfg.get("pot_start_per_player", 100)) * n
    pot = max(0, int(pot))
    share = pot // n
    st.meters["pot"] = pot - share * n
    st.prep = {
        "allowance": {pid: share for pid in st.players},
        "spent": {pid: 0 for pid in st.players},
        "actions": {pid: 0 for pid in st.players},
        "ready": [],
        "rolls": [],
        "share": share,
        "pot_start": pot,
    }
    st.status = PREP


# ---------------------------------------------------------------------------
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
    pot: Optional[int] = None,
    script: Optional[Sequence[Dict[str, Any]]] = None,
    weapon_names: Optional[Mapping[str, Sequence[str]]] = None,
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
        created_ts=now if now is not None else time.time(), script=[dict(s) for s in (script or [])],
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
    if st.has("first_strike") and not [c for c in split_list(config.get("first_strike_cards", "")) if c in cards]:
        raise RuleError("NO_FIXED_CARD")
    if st.has("tutorial") and not st.script:
        raise RuleError("NO_SCRIPT")
    st.meters = {"firewall": 0, "firewall_acc": 0.0, "tips_seen": []}
    if st.has("ncpd"):
        st.meters["ncpd"] = 0.0
    if st.has("drag"):
        st.meters["drag"] = {}
    if st.has("doll"):
        st.meters["chip"] = int(config.get("chip_stability", 5))
        st.meters["chip_max"] = st.meters["chip"]
    if st.mode_owner() is not None:
        st.meters["doll_mode"] = ""
    if st.has("terminal"):
        hid = str(host_id) if str(host_id) in st.players else next(iter(st.players))
        st.meters["terminal_holder"] = hid
        st.meters["screen"] = int(config.get("screen_durability", 3))
        st.meters["screen_max"] = st.meters["screen"]
    if st.has("ice"):
        st.meters["ice"] = int(config.get("ice_layers", 3))
        st.meters["ice_max"] = st.meters["ice"]
    if st.has("life_support"):
        st.meters["life"] = int(config.get("life_support_rounds", 8))
        st.meters["life_max"] = st.meters["life"]
    if bool(config.get("prep_enabled", True)):
        _start_prep(st, rng, weapon_names, pot)
        return st
    begin_combat(st, rng)
    return st


def begin_combat(st: BattleState, rng: Rng = DEFAULT_RNG, chooser: Optional[Chooser] = None) -> Optional[Dict[str, Any]]:
    """준비 단계 뒤(또는 준비 단계가 없으면 new_battle 에서) 전투를 연다.
    first_strike 면 선제 라운드를 먼저 치르고 그 보고를 pending_reports 에 넣어 돌려준다."""
    if st.has("first_strike"):
        return resolve_ambush(st, rng, chooser)
    st.round = 1
    open_round(st, rng, chooser)
    return None


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


def can_field_mod(st: BattleState, c: Combatant) -> bool:
    if FIELD_MOD_CARD not in st.cards or c.side != ALLY or c.weapon_grade <= 0:
        return False
    if c.weapon_grade >= int(st.config.get("weapon_max", 4)):
        return False
    return int(st.meters.get("pot", 0)) >= int(st.config.get("field_mod_cost", 50))


def fixed_cards(st: BattleState, c: Combatant) -> List[str]:
    """규칙이 쥐어주는 카드. 붙잡혔으면 떨쳐내기만, 단말기 소지자는 넘기기, 금고가 남았으면 현장 개조."""
    if is_grappled(c):
        return [BREAK_CARD] if BREAK_CARD in st.cards else []
    out: List[str] = []
    if st.has("terminal") and st.meters.get("terminal_holder") == c.id and PASS_CARD in st.cards:
        if len(st.alive_units(ALLY)) > 1:
            out.append(PASS_CARD)
    if can_field_mod(st, c):
        out.append(FIELD_MOD_CARD)
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
        hand = draw_from_pool(pool_for(st, c), int(st.config.get("hand_size", 3)), rng)
        if st.has("tutorial"):
            from . import tutorial  # 순환 import 회피
            hand = tutorial.guaranteed(st, uid, hand)
        st.hands[uid] = hand
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


def _pick_mode(st: BattleState, enemy: Combatant, rng: Rng) -> Tuple[str, str]:
    """config modes(traits 에 modes 가 있는 적) 또는 doll_modes(돌) = `mode:card:weight;...` → (mode, card_id).
    modes_norepeat 에 적힌 모드는 연속으로 나오지 않는다 (기본: reboot)."""
    key = "doll_modes"
    if enemy.has_trait("modes") and str(st.config.get("modes", "")).strip():
        key = "modes"
    entries: List[Tuple[str, str, float]] = []
    for chunk in split_list(st.config.get(key, "")):
        parts = [p.strip() for p in chunk.split(":")]
        if len(parts) >= 2 and parts[0] in MODES and parts[1] in st.cards:
            w = float(parts[2]) if len(parts) > 2 and parts[2] else 1.0
            entries.append((parts[0], parts[1], w))
    if not entries:
        return "", ""
    prev = st.meters.get("doll_mode", "")
    norepeat = set(split_list(st.config.get("modes_norepeat", "reboot")))
    cands = [e for e in entries if not (e[0] in norepeat and prev == e[0])] or entries
    pick = rng.choices(cands, weights=[e[2] for e in cands], k=1)[0]
    return pick[0], pick[1]


def _mode_target(st: BattleState, me: Combatant, card: Card, rng: Rng) -> Optional[str]:
    """모드 카드·선제 카드의 대상: 자신 / 단말기 소지자 / 무작위 생존 아군."""
    if card.target == "self":
        return me.id
    if card.target != "enemy":
        return None
    h = st.holder()
    if h is not None and h.alive():
        return h.id
    alive = st.alive_units(ALLY)
    return rng.choice(alive).id if alive else None


def _default_chooser(st: BattleState) -> Chooser:
    if st.has("tutorial") and st.script:
        from . import tutorial  # 순환 import 회피
        return tutorial.chooser(st)
    from . import ai  # 순환 import 회피
    return ai.choose


def open_round(st: BattleState, rng: Rng = DEFAULT_RNG, chooser: Optional[Chooser] = None) -> None:
    """적의 선택을 정하고(의도 공개) 플레이어 선택을 받기 시작한다."""
    if chooser is None:
        chooser = _default_chooser(st)
    st.hands, st.choices, st.enemy_choices, st.intents = {}, {}, {}, {}
    st.round_flags = {}
    st.round_events = []
    if st.has("tutorial") and st.script:
        from . import tutorial  # 순환 import 회피
        tutorial.apply_setup(st)
    k = int(st.config.get("hand_size", 3))
    for e in st.enemies.values():
        if not e.alive():
            continue
        if is_stunned(e):
            st.intents[e.id] = {"kind": "stunned", "label": "stunned", "target": None, "card_id": ""}
            continue
        if (e.has_trait("doll") and st.has("doll")) or e.has_trait("modes"):
            mode, cid = _pick_mode(st, e, rng)
            if cid:
                st.meters["doll_mode"] = mode
                st.meters["mode_owner"] = e.name
                card = st.cards[cid]
                st.enemy_choices[e.id] = Choice(e.id, cid, _mode_target(st, e, card, rng))
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


def enabled_firewall_options(st: BattleState) -> List[str]:
    """이 전투에서 열린 옵션 (config firewall_options, 비어 있으면 전부)."""
    want = set(split_list(st.config.get("firewall_options", "")))
    if not want:
        return list(FIREWALL_OPTIONS)
    return [o for o in FIREWALL_OPTIONS if o in want]


def firewall_locked(st: BattleState) -> bool:
    return int(st.meters.get("firewall_lock_round", 0)) == st.round and st.round > 0


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
    if option not in enabled_firewall_options(st):
        raise RuleError("FIREWALL_OFF")
    if firewall_locked(st):
        raise RuleError("FIREWALL_LOCKED")
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
    """UI 용 예상 피해 (수정자 미반영, 무기 보너스 반영). damage 효과가 없으면 (0, 0)."""
    e = card.effect("damage") or card.effect("scatter_damage")
    if e is None:
        return 0, 0
    base = int(e.value) + stat_bonus(actor, card.stat, config) + weapon_bonus(actor, config)
    return base, base + int(config.get("damage_variance", 4))


def expected_damage(card: Card, actor: Combatant, target: Optional[Combatant], config: Dict[str, Any]) -> float:
    """AI 용 기대 피해 (난수 평균, 수정자 반영)."""
    e = card.effect("damage") or card.effect("scatter_damage")
    if e is None:
        return 0.0
    dmg = e.value + stat_bonus(actor, card.stat, config) + weapon_bonus(actor, config) + int(config.get("damage_variance", 4)) / 2.0
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


def ice_multiplier(st: BattleState) -> float:
    if not st.has("ice"):
        return 1.0
    layers = int(st.meters.get("ice", 0))
    if layers <= 0:
        return 1.0
    return max(0.1, 1.0 - float(st.config.get("ice_reduction", 0.3)) * layers)


def overkill_risk(st: BattleState, actor: Combatant, card: Card, target: Combatant) -> bool:
    """⚠ 과잉 위험: 비살상 전투에서 이 카드의 최대 피해가 대상의 남은 HP 를 overkill_margin 이상 초과할 때.
    살상 태그는 쓰러뜨릴 수 있기만 하면 위험, 비살상 태그는 절대 아님."""
    if not st.has("nonlethal") or target.side != ENEMY or not card.deals_damage():
        return False
    if card.tagged("nonlethal"):
        return False
    if target.has_trait("doll") and bool(st.config.get("doll_hp_overkill_ok")):
        return False
    lo, hi = damage_range(card, actor, st.config)
    if actor.side == ALLY and st.round_flags.get("steady_aim"):
        hi = lo
    if card.combo is not None and combo_applies(st, actor, card, target):
        if card.combo.bonus == "dmg_x":
            hi = int(hi * float(card.combo.value))
        elif card.combo.bonus == "dmg_add":
            hi = hi + int(card.combo.value)
    hi = int(hi * ice_multiplier(st))
    if card.tagged("lethal"):
        return hi >= target.hp
    return hi - target.hp >= int(st.config.get("overkill_margin", 15))


def add_modifier(target: Combatant, m: Modifier) -> None:
    old = target.mod(m.kind)
    if old is not None:
        if m.kind == "daemon":
            m.value = old.value + m.value          # 중복 감염 = 스택
        else:
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


def tutorial_invulnerable(st: BattleState) -> bool:
    """튜토리얼 대본의 마지막 라운드 전에는 적 HP 가 1 아래로 내려가지 않는다 (수업을 끝까지 하기 위해)."""
    if not st.has("tutorial") or not st.script:
        return False
    return st.round < max(int(s.get("round", 0)) for s in st.script)


def _ice_strip(st: BattleState, line: Dict[str, Any]) -> None:
    """아군 해킹이 적에게 맞으면 ICE 한 층을 벗긴다 (라운드 상한 ice_round_cap)."""
    cap = int(st.config.get("ice_round_cap", 2))
    used = int(st.round_flags.get("ice_used", 0))
    before = int(st.meters.get("ice", 0))
    if before <= 0:
        return
    dealt = 1 if (cap <= 0 or used < cap) else 0
    st.meters["ice"] = before - dealt
    st.round_flags["ice_used"] = used + dealt
    line["ice"] = {"amount": dealt, "value": st.meters["ice"], "max": int(st.meters.get("ice_max", 0)), "capped": dealt == 0}


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
    elif card.has("heal") and not card.deals_damage():
        line["result"] = "heal"
    elif card.has("noop") and len(card.effects) == 1:
        line["result"] = "noop"
    elif card.has(BREAK_CARD):
        line["result"] = "break_free"
    elif card.has(PASS_CARD):
        line["result"] = "pass"
    elif card.has(FIELD_MOD_CARD):
        line["result"] = "field_mod"

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

    if card.deals_damage():
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
                    line["redirected"] = True
        # 회피 (물리 공격 한정, 스마트 탄 유도 중엔 무시)
        if len(targets) == 1 and card.stat == "physics" and not (actor.side == ALLY and st.round_flags.get("steady_aim")):
            ev = targets[0].traits.get("evade") if targets[0].traits else None
            if ev and rng.random() < float(ev):
                line["result"] = "evade"
                line["targets"].append(_tinfo(targets[0], "evade", 0))
                return line
        # 무기 글리치: 다음 공격 1회에 소모 (불발·회피된 공격은 소모하지 않는다)
        ad = actor.mod("atk_down")
        if ad is not None:
            actor.remove_mod("atk_down")
            line["atk_down"] = ad.value

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
    # 블랙 ICE: 아군 해킹이 적에게 맞으면 한 층
    if st.has("ice") and card.kind == "hack" and actor.side == ALLY and any(t.side == ENEMY for t in targets):
        _ice_strip(st, line)
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
        dmg = eff.value + stat_bonus(actor, card.stat, cfg) + weapon_bonus(actor, cfg) + var
        if line.get("combo") and card.combo is not None:
            if card.combo.bonus == "dmg_x":
                dmg *= float(card.combo.value)
            elif card.combo.bonus == "dmg_add":
                dmg += float(card.combo.value)
        ad = line.get("atk_down")
        if ad:
            dmg *= max(0.0, 1.0 - float(ad))
        info_flags: Dict[str, Any] = {}
        sh = t.mod("shield")
        if sh:
            dmg *= max(0.0, 1.0 - sh.value)
            info_flags["shielded"] = True
        if t.side == ENEMY and st.has("ice") and int(st.meters.get("ice", 0)) > 0:
            dmg *= ice_multiplier(st)
            info_flags["iced"] = int(st.meters.get("ice", 0))
        dmg = max(0, int(dmg))
        before = t.hp
        floor = 1 if (t.side == ENEMY and tutorial_invulnerable(st)) else 0   # 튜토리얼: 마지막 수업 전엔 쓰러지지 않는다
        t.hp = max(floor, t.hp - dmg)
        info = _tinfo(t, "damage", dmg)
        info.update(info_flags)
        # 과잉 제압 (비살상 전투, 적 대상)
        if st.has("nonlethal") and t.side == ENEMY and actor.side == ALLY and before > 0 and t.hp == 0:
            lethal_card = card.tagged("lethal")
            doll_ok = t.has_trait("doll") and bool(cfg.get("doll_hp_overkill_ok"))
            if (lethal_card or dmg - before >= int(cfg.get("overkill_margin", 15))) and not doll_ok \
                    and not card.tagged("nonlethal"):
                info["overkill"] = True
                _ncpd_add(st, float(cfg.get("ncpd_overkill", 1.0)), events, "overkill", actor.name)
        if t.side == ENEMY and actor.side == ALLY and before > 0 and t.hp == 0:
            st.meters["last_ko"] = {"card": card.id, "nonlethal": card.tagged("nonlethal"),
                                    "overkill": bool(info.get("overkill")), "by": actor.id, "target": t.id}
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


def _h_scatter(st, actor, card, eff, targets, line, rng, events):
    """상대편 생존자 중 count 명을 무작위로 골라 damage 와 같은 경로로 때린다."""
    remaining = list(st.alive_units(opposite(actor.side)))
    picked: List[Combatant] = []
    for _ in range(max(1, int(eff.count))):
        if not remaining:
            break
        u = rng.choice(remaining)
        remaining.remove(u)
        picked.append(u)
    if picked:
        _h_damage(st, actor, card, Effect("damage", eff.value), picked, line, rng, events)


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


def infected(st: BattleState, side: str = ALLY) -> List[Combatant]:
    return [u for u in st.alive_units(side) if u.mod("daemon") is not None]


def _h_daemon(st, actor, card, eff, targets, line, rng, events):
    """데몬 감염: 단일 대상 카드는 고른 대상, 전체 카드는 상대편 중 count 명 무작위.
    동시 감염 상한(daemon_max_infected)에 닿으면 이미 감염된 사람에게 겹친다(스택)."""
    cfg = st.config
    opp = opposite(actor.side)
    cap = int(cfg.get("daemon_max_infected", 4))
    stacks = max(1, int(eff.value))
    turns = max(1, int(cfg.get("daemon_turns", 2)) - (1 if st.round == 0 else 0))   # 선제 라운드는 틱이 없다
    if card.target in ("enemy", "ally", "self"):
        chosen = [t for t in targets if t.side == opp]
    else:
        remaining = list(st.alive_units(opp))
        chosen = []
        for _ in range(max(1, int(eff.count))):
            if not remaining:
                break
            u = rng.choice(remaining)
            remaining.remove(u)
            chosen.append(u)
    for t in chosen:
        if t.mod("daemon") is None and len(infected(st, opp)) >= cap:
            pool = infected(st, opp)
            if not pool:
                continue
            t = rng.choice(pool)
        add_modifier(t, Modifier("daemon", float(stacks), turns, st.round, source=actor.id))
        info = _tinfo(t, "daemon", int(t.mod("daemon").value))
        info["turns"] = turns
        line["targets"].append(info)


def _h_cleanse(st, actor, card, eff, targets, line, rng, events):
    for t in targets:
        d = t.mod("daemon")
        if d is None:
            continue
        t.remove_mod("daemon")
        line["targets"].append(_tinfo(t, "cleanse", int(d.value)))


def _h_firewall_drain(st, actor, card, eff, targets, line, rng, events):
    n = max(0, int(eff.value))
    before = int(st.meters.get("firewall", 0))
    st.meters["firewall"] = max(0, before - n)
    events.append({"kind": "firewall_drain", "by": actor.name, "amount": before - st.meters["firewall"],
                   "value": st.meters["firewall"], "max": int(st.config.get("firewall_max", 9))})


def _h_firewall_lock(st, actor, card, eff, targets, line, rng, events):
    st.meters["firewall_lock_round"] = st.round + 1
    events.append({"kind": "firewall_lock", "by": actor.name, "round": st.round + 1})


def _h_ice(st, actor, card, eff, targets, line, rng, events):
    if not st.has("ice"):
        return
    mx = int(st.meters.get("ice_max", int(st.config.get("ice_layers", 3))))
    before = int(st.meters.get("ice", 0))
    st.meters["ice"] = min(mx, before + max(0, int(eff.value)))
    if st.meters["ice"] != before:
        events.append({"kind": "ice", "by": actor.name, "amount": st.meters["ice"] - before, "value": st.meters["ice"], "max": mx})


def _h_expose(st, actor, card, eff, targets, line, rng, events):
    ev: Dict[str, Any] = {"kind": "expose", "by": actor.name}
    if st.has("ice"):
        st.meters["ice"] = 0
        ev["ice"] = 0
    if st.has("life_support"):
        st.meters["life"] = max(0, int(st.meters.get("life", 0)) - 1)
        ev["life"] = st.meters["life"]
        ev["life_max"] = int(st.meters.get("life_max", 0))
    events.append(ev)


def _h_field_mod(st, actor, card, eff, targets, line, rng, events):
    """현장 개조: 금고에서 비용을 빼고 d10. 성공 +1 등급, 실패는 강등 없이 다음 공격 불발."""
    cfg = st.config
    cost = int(cfg.get("field_mod_cost", 50))
    pot = int(st.meters.get("pot", 0))
    wmax = int(cfg.get("weapon_max", 4))
    if actor.side != ALLY or actor.weapon_grade <= 0 or actor.weapon_grade >= wmax or pot < cost:
        line["result"] = "field_mod_none"
        return
    st.meters["pot"] = pot - cost
    r = roll_upgrade(actor.weapon_grade, rng, cfg, allow_down=False)
    before = actor.weapon_grade
    if r["delta"] > 0:
        actor.weapon_grade = clamp_grade(actor.weapon_grade + 1, cfg)
    else:
        add_modifier(actor, Modifier("misfire", 1.0, 1, st.round, source="field_mod"))
    if r.get("refund"):
        st.meters["pot"] = int(st.meters["pot"]) + cost
    info = {"roll": r["roll"], "success": r["delta"] > 0, "before": before, "after": actor.weapon_grade,
            "weapon": weapon_label(actor), "cost": cost, "refund": bool(r.get("refund")), "pot": int(st.meters["pot"])}
    line["field_mod"] = info
    line["targets"].append(_tinfo(actor, "field_mod", actor.weapon_grade))
    events.append({"kind": "field_mod", "by": actor.name, **info})


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
    "scatter_damage": _h_scatter,
    "self_damage": _h_self_damage,
    "heal": _h_heal,
    "shield": _h_modifier,
    "atk_down": _h_modifier,
    "misfire": _h_modifier,
    "redirect_random": _h_modifier,
    "stun": _h_modifier,
    "daemon": _h_daemon,
    "cleanse": _h_cleanse,
    "grapple": _h_grapple,
    "break_free": _h_break_free,
    "pass_terminal": _h_pass_terminal,
    "chip": _h_chip,
    "firewall_drain": _h_firewall_drain,
    "firewall_lock": _h_firewall_lock,
    "ice": _h_ice,
    "expose": _h_expose,
    "field_mod": _h_field_mod,
    "noop": _h_noop,
}


def _daemon_bite(st: BattleState, u: Combatant, line: Dict[str, Any], events: List[Dict[str, Any]]) -> None:
    """감염된 채로 카드를 냈다 (불발·회피·대상 없음도 행동이다) → 스택당 daemon_damage 자해."""
    d = u.mod("daemon")
    if d is None or not u.alive() or d.created_turn >= st.round:
        return                              # 걸린 라운드에는 물지 않는다 (감염을 보고 고른 행동만 대가를 치른다)
    dmg = int(st.config.get("daemon_damage", 5)) * max(1, int(d.value))
    u.hp = max(0, u.hp - dmg)
    line["daemon"] = {"amount": dmg, "stacks": int(d.value), "hp": u.hp, "max_hp": u.max_hp, "killed": not u.alive()}


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


def _tick_life(st: BattleState, events: List[Dict[str, Any]]) -> None:
    if not st.has("life_support") or st.round < 1:
        return
    before = int(st.meters.get("life", 0))
    st.meters["life"] = max(0, before - 1)
    events.append({"kind": "life", "value": st.meters["life"], "max": int(st.meters.get("life_max", 0))})


def _boss_state(st: BattleState, reason: str) -> str:
    if reason == "collapse":
        return "critical"
    ko = st.meters.get("last_ko") or {}
    if ko.get("overkill"):
        return "critical"
    if ko.get("nonlethal"):
        return "fine"
    return "hurt"


def _collect_tips(st: BattleState, lines: List[Dict[str, Any]], events: List[Dict[str, Any]]) -> List[str]:
    """이 전투에서 처음 일어난 일의 키 목록 (문장은 strings.TIPS). meters.tips_seen 에 기록."""
    if not bool(st.config.get("tips", True)):
        return []
    found: List[str] = []

    def hit(key: str, cond: bool) -> None:
        if cond and key not in found:
            found.append(key)

    for ln in lines:
        res = ln.get("result")
        hit("hack_cancel", res == "stunned" and ln.get("side") == ENEMY
            and st.intents.get(ln["actor_id"], {}).get("kind") in ("attack", "special", "mode"))
        hit("combo", bool(ln.get("combo")))
        hit("misfire", res == "misfire")
        hit("redirect", bool(ln.get("redirected")))
        hit("evade", res == "evade")
        hit("chip", bool(ln.get("chip")))
        hit("daemon_bite", bool(ln.get("daemon")))
        hit("ice_strip", bool(ln.get("ice")) and ln["ice"].get("amount", 0) > 0)
        hit("exposed", bool(ln.get("ice")) and ln["ice"].get("value", 1) == 0)
        hit("field_mod", bool(ln.get("field_mod")))
        for t in ln.get("targets") or []:
            hit("shield", bool(t.get("shielded")))
            hit("overkill", bool(t.get("overkill")))
            hit("grapple", t.get("kind") == "grapple")
            hit("daemon", t.get("kind") == "daemon")
    for ev in events:
        k = ev.get("kind")
        hit("drag", k == "drag_start")
        hit("exposed", k == "expose")
    opts = enabled_firewall_options(st)
    if opts:
        hit("firewall_ready", int(st.meters.get("firewall", 0)) >= min(firewall_cost(st, o) for o in opts))
    hit("life_low", st.has("life_support") and 0 < int(st.meters.get("life", 0)) <= 2)
    seen = list(st.meters.get("tips_seen") or [])
    out = [k for k in found if k not in seen]
    st.meters["tips_seen"] = seen + out
    return out


def _snapshot(st: BattleState, lines, events, w, reason, **extra) -> Dict[str, Any]:
    rep = {
        "round_no": st.round, "lines": lines, "events": events,
        "hp": {u.id: [u.hp, u.max_hp] for u in list(st.players.values()) + list(st.enemies.values())},
        "meters": dict(st.meters), "winner": w, "reason": reason,
    }
    rep.update(extra)
    return rep


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

    announced: List[Combatant] = []        # 기절은 라운드 끝에 풀린다 → 같은 라운드의 '기절한 적에게' 콤보가 성립
    for phase, _, _, u in ordered:
        if not u.alive():
            lines.append(_plain_line(u, "down"))
            continue
        if is_stunned(u):
            announced.append(u)
            lines.append(_plain_line(u, "stunned"))
            continue
        ch = actions[u.id]
        assert ch is not None
        if u.side == ENEMY and u.has_trait("doll") and st.has("doll") and st.meters.get("doll_mode") == "reboot":
            lines.append(_plain_line(u, "reboot"))
            continue
        line = _apply_card(st, u, st.cards[ch.card_id], ch.target_id, rng, events)
        _daemon_bite(st, u, line, events)
        lines.append(line)
    for u in waits:
        if u.alive():                      # 정산 중 쓰러진 유닛의 '대기' 는 알리지 않는다
            lines.append(_plain_line(u, "wait"))
    for u in announced:
        u.remove_mod("stun")

    tick_modifiers(st)
    _regen(st, events)
    _advance_drag(st, rng, events)
    _tick_life(st, events)

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
        elif st.has("life_support") and int(st.meters.get("life", 1)) <= 0:
            w, reason = ALLY, "collapse"
        elif st.has("tutorial") and st.script and st.round >= max(int(s.get("round", 0)) for s in st.script):
            w, reason = ALLY, "tutorial"
    if w == ALLY and st.has("life_support"):
        st.meters["boss_state"] = _boss_state(st, reason)

    extra: Dict[str, Any] = {"tips": _collect_tips(st, lines, events)}
    if st.has("tutorial") and st.script:
        from . import tutorial  # 순환 import 회피
        extra["tutorial"] = tutorial.evaluate(st, lines, events)
    report = _snapshot(st, lines, events, w, reason, **extra)
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


def resolve_ambush(st: BattleState, rng: Rng = DEFAULT_RNG, chooser: Optional[Chooser] = None) -> Dict[str, Any]:
    """선제 라운드(0라운드): 적만 first_strike_cards 를 낸다. 대기 줄·틱·종료 판정·시계 감소 없음.
    보고는 st.log 와 st.pending_reports 에 넣고 1라운드를 연다."""
    st.round = 0
    st.status = RESOLVING
    events: List[Dict[str, Any]] = []
    lines: List[Dict[str, Any]] = []
    cards = [c for c in split_list(st.config.get("first_strike_cards", "")) if c in st.cards]
    for e in st.alive_units(ENEMY):
        for cid in cards:
            card = st.cards[cid]
            lines.append(_apply_card(st, e, card, _mode_target(st, e, card, rng), rng, events))
    report = _snapshot(st, lines, events, None, "", ambush=True, tips=_collect_tips(st, lines, events))
    st.log.append(report)
    st.pending_reports.append(report)
    st.round = 1
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
