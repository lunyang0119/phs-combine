"""전투 데이터 로더 — 로컬 CSV 가 기본, 환경변수로 구글 시트 탭으로 전환.

두 출처 모두 `List[Dict[str, Any]]` 행을 돌려주므로 파서는 출처를 모른다.
    CARDBATTLE_SOURCE=csv|sheet   (기본 csv)
    CARDBATTLE_DATA_DIR=<dir>     (기본 <repo>/data/cardbattle)
sheet 를 골랐는데 스프레드시트가 없거나 탭이 없으면 경고 후 CSV 로 폴백한다.
"""
from __future__ import annotations

import csv
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Protocol

from . import engine as E

logger = logging.getLogger(__name__)

TABLE_FILES = {"cards": "Cards.csv", "enemies": "Enemies.csv", "battles": "Battles.csv"}
TABLE_TABS = {"cards": "Card_List", "enemies": "Card_Enemies", "battles": "Card_Battles"}
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


def parse_effects(text: str) -> List[E.Effect]:
    """`kind[:value[:turns[:chance]]]` 를 `;` 로 연결한 문자열."""
    out: List[E.Effect] = []
    for chunk in str(text or "").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = [p.strip() for p in chunk.split(":")]
        kind = parts[0]
        if kind not in E.EFFECT_KINDS:
            raise DataError(f"알 수 없는 효과: {kind!r} (가능: {', '.join(E.EFFECT_KINDS)})")
        try:
            value = float(parts[1]) if len(parts) > 1 and parts[1] else 0.0
            turns = int(float(parts[2])) if len(parts) > 2 and parts[2] else 1
            chance = float(parts[3]) if len(parts) > 3 and parts[3] else 1.0
        except ValueError:
            raise DataError(f"효과 형식 오류: {chunk!r}")
        out.append(E.Effect(kind=kind, value=value, turns=max(1, turns), chance=min(1.0, max(0.0, chance))))
    return out


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
    return E.Card(id=cid, name=_s(r, "name", cid), kind=kind, target=target, stat=stat, pool=pool,
                  weight=_f(r, "weight", 1.0), effects=effects, desc=_s(r, "desc"))


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
    return E.EnemyDef(id=eid, name=_s(r, "name", eid), max_hp=max_hp,
                      physics=_i(r, "physics", 10), magic=_i(r, "magic", 10),
                      agility=_i(r, "agility", 10), charm=_i(r, "charm", 10),
                      personality=personality, pool=pool)


def parse_enemy_list(text: str, known: Mapping[str, Any]) -> List[str]:
    """`grunt*3;guard` → ['grunt','grunt','grunt','guard']"""
    out: List[str] = []
    for chunk in str(text or "").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
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
    return E.BattleDef(id=bid, name=_s(r, "name", bid), enemies=enemies, desc=_s(r, "desc"))


@dataclass
class GameData:
    cards: Dict[str, E.Card] = field(default_factory=dict)
    enemies: Dict[str, E.EnemyDef] = field(default_factory=dict)
    battles: Dict[str, E.BattleDef] = field(default_factory=dict)


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
    if not gd.cards:
        raise DataError("카드가 하나도 없습니다")
    for side in (E.ALLY, E.ENEMY):
        if not [c for c in gd.cards.values() if c.pool in ("both", side)]:
            raise DataError(f"{side} 쪽이 뽑을 카드가 없습니다 (pool 확인)")
    if not gd.battles:
        raise DataError("전투가 하나도 없습니다")
    return gd
