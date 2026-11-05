"""전투 데이터 로더 — 로컬 CSV 가 기본, 환경변수로 구글 시트 탭으로 전환.

두 출처 모두 `List[Dict[str, Any]]` 행을 돌려주므로 파서는 출처를 모른다.
    CARDBATTLE_SOURCE=csv|sheet   (기본 csv)
    CARDBATTLE_DATA_DIR=<dir>     (기본 <repo>/data/cardbattle)
sheet 를 골랐는데 스프레드시트가 없거나 탭이 없으면 경고 후 CSV 로 폴백한다.
Tutorial.csv / Card_Tutorial 탭은 선택 사항이다 (없으면 튜토리얼 전투만 시작할 수 없다).
"""
from __future__ import annotations

import csv
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Protocol

from . import config as cfgmod
from . import engine as E
from . import tutorial as T

logger = logging.getLogger(__name__)

TABLE_FILES = {"cards": "Cards.csv", "enemies": "Enemies.csv", "battles": "Battles.csv", "tutorial": "Tutorial.csv"}
TABLE_TABS = {"cards": "Card_List", "enemies": "Card_Enemies", "battles": "Card_Battles", "tutorial": "Card_Tutorial"}
OPTIONAL_TABLES = ("tutorial",)
DEFAULT_DIR = Path(__file__).resolve().parent.parent / "data" / "cardbattle"
PERSONALITIES = ("aggressive", "defensive", "hacker")


class DataError(ValueError):
    """운영자에게 그대로 보여줄 수 있는 데이터 오류."""


class DataSource(Protocol):
    def rows(self, table: str) -> List[Dict[str, Any]]: ...


class CsvSource:
    def __init__(self, directory: Optional[os.PathLike] = None):
        self.directory = Path(directory) if directory else DEFAULT_DIR

    def rows(self, table: str) -> List[Dict[str, Any]]:
        path = self.directory / TABLE_FILES[table]
        if not path.exists():
            raise DataError(f"CSV 파일이 없습니다: {path}")
        with open(path, encoding="utf-8-sig", newline="") as fh:
            return [dict(r) for r in csv.DictReader(fh)]

    def __repr__(self) -> str:
        return f"CsvSource({self.directory})"


class SheetSource:
    def __init__(self, spreadsheet: Any):
        self.spreadsheet = spreadsheet

    def rows(self, table: str) -> List[Dict[str, Any]]:
        tab = TABLE_TABS[table]
        try:
            ws = self.spreadsheet.worksheet(tab)
        except Exception as e:  # gspread.WorksheetNotFound 등
            raise DataError(f"시트 탭이 없습니다: {tab} ({e})")
        return [dict(r) for r in ws.get_all_records()]

    def __repr__(self) -> str:
        return "SheetSource"


class FallbackSource:
    """primary 가 DataError 를 내면 경고 후 secondary 를 쓴다 (표 단위)."""

    def __init__(self, primary: DataSource, secondary: DataSource):
        self.primary = primary
        self.secondary = secondary

    def rows(self, table: str) -> List[Dict[str, Any]]:
        try:
            return self.primary.rows(table)
        except DataError as e:
            logger.warning("카드 전투 데이터 %s: %s — %r 로 폴백", table, e, self.secondary)
            return self.secondary.rows(table)


def make_source(spreadsheet: Any = None, env: Mapping[str, str] = os.environ) -> DataSource:
    mode = (env.get("CARDBATTLE_SOURCE") or "csv").strip().lower()
    directory = env.get("CARDBATTLE_DATA_DIR") or None
    csv_src = CsvSource(directory)
    if mode == "sheet":
        if spreadsheet is None:
            logger.warning("CARDBATTLE_SOURCE=sheet 이지만 스프레드시트가 없어 CSV 를 사용합니다")
            return csv_src
        return FallbackSource(SheetSource(spreadsheet), csv_src)
    if mode != "csv":
        logger.warning("CARDBATTLE_SOURCE=%r 알 수 없음 — csv 사용", mode)
    return csv_src


# ---------------------------------------------------------------------------
# 파서
# ---------------------------------------------------------------------------
def _s(r: Mapping[str, Any], key: str, default: str = "") -> str:
    v = r.get(key)
    if v is None:
        return default
    s = str(v).strip()
    return s if s and s.lower() != "nan" else default


def _i(r: Mapping[str, Any], key: str, default: int) -> int:
    s = _s(r, key)
    if not s:
        return default
    try:
        return int(float(s))
    except ValueError:
        raise DataError(f"{key} 는 정수여야 합니다: {s!r}")


def _f(r: Mapping[str, Any], key: str, default: float) -> float:
    s = _s(r, key)
    if not s:
        return default
    try:
        return float(s)
    except ValueError:
        raise DataError(f"{key} 는 숫자여야 합니다: {s!r}")


def _list(text: str) -> List[str]:
    return [p.strip() for p in str(text or "").split(";") if p.strip()]


def parse_effects(text: str) -> List[E.Effect]:
    """`kind[:value[:turns[:chance]]]` 를 `;` 로 연결한 문자열.
    scatter_damage / daemon (COUNT_KINDS) 은 셋째 슬롯이 지속이 아니라 **무작위 대상 수**다."""
    out: List[E.Effect] = []
    for chunk in _list(text):
        parts = [p.strip() for p in chunk.split(":")]
        kind = parts[0]
        if kind not in E.EFFECT_KINDS:
            raise DataError(f"알 수 없는 효과: {kind!r} (가능: {', '.join(E.EFFECT_KINDS)})")
        try:
            value = float(parts[1]) if len(parts) > 1 and parts[1] else 0.0
            third = int(float(parts[2])) if len(parts) > 2 and parts[2] else 1
            chance = float(parts[3]) if len(parts) > 3 and parts[3] else 1.0
        except ValueError:
            raise DataError(f"효과 형식 오류: {chunk!r}")
        if kind in E.COUNT_KINDS:
            out.append(E.Effect(kind=kind, value=value, turns=1, chance=min(1.0, max(0.0, chance)), count=max(1, third)))
        else:
            out.append(E.Effect(kind=kind, value=value, turns=max(1, third), chance=min(1.0, max(0.0, chance))))
    return out


def parse_tags(text: str) -> List[str]:
    tags = _list(text)
    for t in tags:
        if t not in E.CARD_TAGS:
            raise DataError(f"알 수 없는 태그: {t!r} (가능: {', '.join(E.CARD_TAGS)})")
    return tags


def parse_combo(text: str) -> Optional[E.Combo]:
    """`target_has:stun:dmg_x:1.5` → Combo. 비어 있으면 None."""
    s = str(text or "").strip()
    if not s:
        return None
    parts = [p.strip() for p in s.split(":")]
    if len(parts) != 4:
        raise DataError(f"combo 형식 오류 (조건:수정자:보너스:값): {s!r}")
    cond, mod, bonus, value = parts
    if cond not in E.COMBO_CONDS:
        raise DataError(f"combo 조건 {cond!r} (가능: {', '.join(E.COMBO_CONDS)})")
    if mod not in E.MODIFIER_KINDS:
        raise DataError(f"combo 수정자 {mod!r} (가능: {', '.join(E.MODIFIER_KINDS)})")
    if bonus not in E.COMBO_BONUSES:
        raise DataError(f"combo 보너스 {bonus!r} (가능: {', '.join(E.COMBO_BONUSES)})")
    try:
        v = float(value)
    except ValueError:
        raise DataError(f"combo 값은 숫자여야 합니다: {value!r}")
    return E.Combo(cond=cond, mod=mod, bonus=bonus, value=v)


def parse_traits(text: str) -> Dict[str, float]:
    """`cheap_chrome;evade:0.5;doll` → {"cheap_chrome": 1.0, "evade": 0.5, "doll": 1.0}"""
    out: Dict[str, float] = {}
    for chunk in _list(text):
        name, _, val = chunk.partition(":")
        name = name.strip()
        if name not in E.TRAITS:
            raise DataError(f"알 수 없는 특성: {name!r} (가능: {', '.join(E.TRAITS)})")
        try:
            out[name] = float(val) if val.strip() else 1.0
        except ValueError:
            raise DataError(f"특성 값은 숫자여야 합니다: {chunk!r}")
    return out


def parse_gimmicks(text: str) -> List[str]:
    gs = _list(text)
    for g in gs:
        if g not in E.GIMMICKS:
            raise DataError(f"알 수 없는 기믹: {g!r} (가능: {', '.join(E.GIMMICKS)})")
    return gs


def card_from_row(r: Mapping[str, Any]) -> E.Card:
    cid = _s(r, "card_id")
    if not cid:
        raise DataError("card_id 가 비어 있습니다")
    kind = _s(r, "kind")
    if kind not in E.KINDS:
        raise DataError(f"[{cid}] kind={kind!r} (가능: {', '.join(E.KINDS)})")
    target = _s(r, "target")
    if target not in E.TARGETS:
        raise DataError(f"[{cid}] target={target!r} (가능: {', '.join(E.TARGETS)})")
    stat = _s(r, "stat", "none") or "none"
    if stat not in E.STATS:
        raise DataError(f"[{cid}] stat={stat!r} (가능: {', '.join(E.STATS)})")
    pool = _s(r, "pool", "both") or "both"
    if pool not in E.POOLS:
        raise DataError(f"[{cid}] pool={pool!r} (가능: {', '.join(E.POOLS)})")
    effects = parse_effects(_s(r, "effects"))
    if not effects:
        raise DataError(f"[{cid}] effects 가 비어 있습니다")
    try:
        tags = parse_tags(_s(r, "tags"))
        combo = parse_combo(_s(r, "combo"))
    except DataError as e:
        raise DataError(f"[{cid}] {e}")
    return E.Card(id=cid, name=_s(r, "name", cid), kind=kind, target=target, stat=stat, pool=pool,
                  weight=_f(r, "weight", 1.0), effects=effects, desc=_s(r, "desc"), tags=tags, combo=combo)


def enemy_from_row(r: Mapping[str, Any]) -> E.EnemyDef:
    eid = _s(r, "enemy_id")
    if not eid:
        raise DataError("enemy_id 가 비어 있습니다")
    personality = _s(r, "personality", "aggressive") or "aggressive"
    if personality not in PERSONALITIES:
        logger.warning("[%s] 알 수 없는 personality=%r — aggressive 로 처리", eid, personality)
        personality = "aggressive"
    pool = _s(r, "pool", E.ENEMY) or E.ENEMY
    if pool not in E.POOLS:
        raise DataError(f"[{eid}] pool={pool!r}")
    max_hp = _i(r, "max_hp", 0)
    if max_hp <= 0:
        raise DataError(f"[{eid}] max_hp 는 1 이상이어야 합니다")
    try:
        traits = parse_traits(_s(r, "traits"))
    except DataError as e:
        raise DataError(f"[{eid}] {e}")
    return E.EnemyDef(id=eid, name=_s(r, "name", eid), max_hp=max_hp,
                      physics=_i(r, "physics", 10), magic=_i(r, "magic", 10),
                      agility=_i(r, "agility", 10), charm=_i(r, "charm", 10),
                      personality=personality, pool=pool, traits=traits, color=_s(r, "color"))


def parse_enemy_list(text: str, known: Mapping[str, Any]) -> List[str]:
    """`grunt*3;guard` → ['grunt','grunt','grunt','guard']"""
    out: List[str] = []
    for chunk in _list(text):
        eid, _, n = chunk.partition("*")
        eid = eid.strip()
        if eid not in known:
            raise DataError(f"알 수 없는 enemy_id: {eid!r}")
        try:
            count = int(n.strip()) if n.strip() else 1
        except ValueError:
            raise DataError(f"적 수 형식 오류: {chunk!r}")
        out.extend([eid] * max(0, count))
    return out


def battle_from_row(r: Mapping[str, Any], known: Mapping[str, Any]) -> E.BattleDef:
    bid = _s(r, "battle_id")
    if not bid:
        raise DataError("battle_id 가 비어 있습니다")
    enemies = parse_enemy_list(_s(r, "enemies"), known)
    if not enemies:
        raise DataError(f"[{bid}] 적이 없습니다")
    try:
        gimmicks = parse_gimmicks(_s(r, "gimmicks"))
        overrides = cfgmod.parse_overrides(_s(r, "overrides"))
        cfgmod.build_config(overrides)          # 키·타입 검증
    except (DataError, cfgmod.ConfigError) as e:
        raise DataError(f"[{bid}] {e}")
    return E.BattleDef(id=bid, name=_s(r, "name", bid), enemies=enemies, desc=_s(r, "desc"),
                       gimmicks=gimmicks, overrides=overrides, players=_s(r, "players"))


@dataclass
class GameData:
    cards: Dict[str, E.Card] = field(default_factory=dict)
    enemies: Dict[str, E.EnemyDef] = field(default_factory=dict)
    battles: Dict[str, E.BattleDef] = field(default_factory=dict)
    tutorials: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)


def _mode_cards(cfg: Mapping[str, Any], key: str) -> List[str]:
    out = []
    for chunk in E.split_list(cfg.get(key, "")):
        parts = [p.strip() for p in chunk.split(":")]
        if len(parts) >= 2:
            out.append(parts[1])
    return out


def load_game_data(source: DataSource) -> GameData:
    gd = GameData()
    for r in source.rows("cards"):
        if not _s(r, "card_id"):
            continue
        c = card_from_row(r)
        if c.id in gd.cards:
            raise DataError(f"card_id 중복: {c.id}")
        gd.cards[c.id] = c
    for r in source.rows("enemies"):
        if not _s(r, "enemy_id"):
            continue
        e = enemy_from_row(r)
        if e.id in gd.enemies:
            raise DataError(f"enemy_id 중복: {e.id}")
        gd.enemies[e.id] = e
    for r in source.rows("battles"):
        if not _s(r, "battle_id"):
            continue
        b = battle_from_row(r, gd.enemies)
        if b.id in gd.battles:
            raise DataError(f"battle_id 중복: {b.id}")
        gd.battles[b.id] = b
    try:
        trows = source.rows("tutorial")
    except DataError:
        trows = []
    try:
        gd.tutorials = T.parse_script(trows, gd.cards)
    except T.ScriptError as e:
        raise DataError(f"Tutorial: {e}")
    if not gd.cards:
        raise DataError("카드가 하나도 없습니다")
    for side in (E.ALLY, E.ENEMY):
        if not [c for c in gd.cards.values() if c.pool in ("both", side)]:
            raise DataError(f"{side} 쪽이 뽑을 카드가 없습니다 (pool 확인)")
    if not gd.battles:
        raise DataError("전투가 하나도 없습니다")
    for b in gd.battles.values():
        cfg = cfgmod.build_config(b.overrides)
        if "terminal" in b.gimmicks and E.PASS_CARD not in gd.cards:
            raise DataError(f"[{b.id}] terminal 기믹에는 card_id={E.PASS_CARD} 카드(pool=fixed)가 필요합니다")
        if "doll" in b.gimmicks:
            if E.BREAK_CARD not in gd.cards:
                raise DataError(f"[{b.id}] doll 기믹에는 card_id={E.BREAK_CARD} 카드(pool=fixed)가 필요합니다")
            if not any("doll" in gd.enemies[e].traits for e in b.enemies):
                raise DataError(f"[{b.id}] doll 기믹인데 traits 에 doll 이 있는 적이 없습니다")
            for cid in _mode_cards(cfg, "doll_modes"):
                if cid not in gd.cards:
                    raise DataError(f"[{b.id}] doll_modes 의 카드 {cid!r} 가 없습니다")
        if any("modes" in gd.enemies[e].traits for e in b.enemies):
            key = "modes" if str(cfg.get("modes", "")).strip() else "doll_modes"
            for cid in _mode_cards(cfg, key):
                if cid not in gd.cards:
                    raise DataError(f"[{b.id}] {key} 의 카드 {cid!r} 가 없습니다")
        if "first_strike" in b.gimmicks:
            cards = [c for c in E.split_list(cfg.get("first_strike_cards", "")) if c]
            missing = [c for c in cards if c not in gd.cards]
            if not cards or missing:
                raise DataError(f"[{b.id}] first_strike_cards 의 카드가 없습니다: {missing or '(비어 있음)'}")
        if "tutorial" in b.gimmicks and str(cfg.get("tutorial_script", "")) not in gd.tutorials:
            raise DataError(f"[{b.id}] tutorial 기믹인데 Tutorial.csv 에 script_id={cfg.get('tutorial_script')!r} 가 없습니다")
        if bool(cfg.get("prep_enabled", True)) and E.FIELD_MOD_CARD not in gd.cards:
            logger.warning("[%s] prep_enabled 인데 card_id=%s 카드(pool=fixed)가 없어 현장 개조가 나오지 않습니다", b.id, E.FIELD_MOD_CARD)
    return gd
