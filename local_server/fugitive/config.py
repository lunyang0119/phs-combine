"""침입자 추적 미니게임 — 설정 기본값, 맵 정의, 시트 로더.

모든 값은 런타임에 `/추적기 설정` 으로 변경 가능하다.
시트(Fugitive_Map / Fugitive_Config / Fugitive_Scaling)는 게임 시작 시 한 번만 읽고
절대 쓰지 않는다. 시트가 없으면 아래 기본값으로 동작한다.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEVICES = ("curtain", "coffeepot", "light", "speaker")

# ---------------------------------------------------------------------------
# 기본 튜너블 (타입은 값의 타입으로 강제된다)
# ---------------------------------------------------------------------------
DEFAULTS: Dict[str, Any] = {
    # 라운드 진행
    "round_timer_sec": 43200,      # 비동기 플레이: 12시간
    "fugitive_grace_sec": 1800,    # 마지막 헌터 제출 후 도주자에게 주는 유예
    "early_resolve": True,         # 전원 제출 시 즉시 정산
    # 도주자 기동
    "fugitive_speed": 1,           # 라운드당 이동 가능한 문 수 (1 또는 2)
    "capture_mode": "search",      # search = 수색/문 조우만 체포 | contact = 같은 방이면 체포 판정
    "contact_capture_chance": 0.5, # contact 모드: 이동해 들어간 방에서 붙잡을 확률 (실패 시 방 공개)
    # 자원
    "ram_max": 10,
    "ram_regen": 1,
    # 추적률
    "trace_passive": 5,
    "trace_t1": 40,                # 지연 2 표식 자동 갱신
    "trace_t2": 70,                # 지연 1
    "trace_t3": 100,               # 실시간 + 퀵핵 봉인
    "overheat_rounds": 3,          # TRACED 이후 이 라운드 수가 지나면 동결
    "max_rounds": 24,              # 하드 캡 (동결)
    # 추적기
    "scan_budget": 1,
    "scan_trace": 5,               # 성공한 추적 1회당 추적률 상승
    "scan_lag": 1,                 # 0 = 이동 후 방, 1 = 라운드 시작 시점 방의 장치
    "signal_mode": "banded",       # banded | exact | off
    "signal_bands": "1,3",         # 강 ≤ a, 중 ≤ b, 약 > b
    "signature_reveal": True,
    "reveal_start": False,
    # 퀵핵
    "ping_enabled": True,
    "ping_cost": 1,
    "ping_trace": 5,
    "ping_signature": False,
    "doorlock_cost": 2,
    "doorlock_trace": 10,
    "doorlock_range": 0,           # 0 = 도주자 방에 접한 문만, -1 = 어디든
    "distract_cost": 3,
    "distract_trace": 15,
    "blackout_cost": 3,
    "blackout_trace": 15,
    "blackout_blocks_scan": True,
    "hide_cost": 4,
    "hide_trace": 20,
    "hide_duration": 2,
    "overload_cost": 2,
    "overload_trace": 15,
    "overload_daze_rounds": 1,
    # 표시
    "render_mode": "text",         # text = 코드 블록 | image = PNG (Pillow + 한글 글꼴 필요)
    # 배치
    "hunter_spawn_rooms": "auto",
    "fugitive_start": "auto",
    # 추후 과제(§11-8): 격리 프로토콜
    "lockdown_enabled": False,
    "lockdown_start": 8,
    "lockdown_every": 3,
}

# 인원수 → 맵/난이도 오버라이드. 목표: 4인 중앙값 15라운드, 인원이 늘수록 감소.
SCALING: Dict[int, Dict[str, Any]] = {
    2: {"map_id": "car2077_9",  "ram_max": 8,  "ram_regen": 1, "trace_passive": 3, "scan_budget": 1, "max_rounds": 22},
    3: {"map_id": "car2077_9",  "ram_max": 8,  "ram_regen": 1, "trace_passive": 4, "scan_budget": 1, "max_rounds": 20},
    4: {"map_id": "car2077_12", "ram_max": 10, "ram_regen": 1, "trace_passive": 4, "scan_budget": 1, "max_rounds": 18},
    5: {"map_id": "car2077_16", "ram_max": 12, "ram_regen": 1, "trace_passive": 6, "scan_budget": 2, "max_rounds": 16},
    6: {"map_id": "car2077_16", "ram_max": 12, "ram_regen": 2, "trace_passive": 7, "scan_budget": 2, "max_rounds": 15},
}

# ---------------------------------------------------------------------------
# 내장 맵. rows: room -> (device, neighbors, col, row)
# ---------------------------------------------------------------------------
_V0 = {
    "A1": ("curtain",   "A2,B1",    1, 1), "A2": ("coffeepot", "A1,A3,B2", 2, 1),
    "A3": ("light",     "A2,A4,B3", 3, 1), "A4": ("curtain",   "A3,B4",    4, 1),
    "B1": ("speaker",   "A1,C1",    1, 2), "B2": ("curtain",   "A2,B3,C2", 2, 2),
    "B3": ("speaker",   "A3,B2,C3", 3, 2), "B4": ("light",     "A4,C4",    4, 2),
    "C1": ("coffeepot", "B1,C2",    1, 3), "C2": ("light",     "B2,C1,C3", 2, 3),
    "C3": ("coffeepot", "B3,C2,C4", 3, 3), "C4": ("speaker",   "B4,C3",    4, 3),
}
_ROW_D = {
    "D1": ("speaker",   "C1,D2",    1, 4), "D2": ("curtain",   "C2,D1,D3", 2, 4),
    "D3": ("light",     "C3,D2,D4", 3, 4), "D4": ("coffeepot", "C4,D3",    4, 4),
}


def _with_row_d() -> Dict[str, tuple]:
    m = dict(_V0)
    m.update(_ROW_D)
    for r, extra in (("C1", "D1"), ("C2", "D2"), ("C3", "D3"), ("C4", "D4")):
        dev, nb, c, rw = m[r]
        m[r] = (dev, nb + "," + extra, c, rw)
    return m


def _nine() -> Dict[str, tuple]:
    keep = {k for k in _V0 if not k.endswith("4")}
    out = {}
    for k in keep:
        dev, nb, c, rw = _V0[k]
        nb2 = ",".join(x for x in nb.split(",") if x in keep)
        out[k] = (dev, nb2, c, rw)
    return out


BUILTIN_MAPS: Dict[str, Dict[str, tuple]] = {
    "car2077_9": _nine(),
    "car2077_12": dict(_V0),
    "car2077_16": _with_row_d(),
}


def map_rows(map_id: str, maps: Optional[Dict[str, Dict[str, tuple]]] = None) -> List[Dict[str, Any]]:
    """맵을 시트 행 형식(list of dict)으로 반환."""
    src = (maps or BUILTIN_MAPS)[map_id]
    return [
        {"map_id": map_id, "room": room, "device": dev, "neighbors": nb, "col": c, "row": r}
        for room, (dev, nb, c, r) in src.items()
    ]


# ---------------------------------------------------------------------------
# 설정 값 변환/검증
# ---------------------------------------------------------------------------
class ConfigError(ValueError):
    pass


def coerce(key: str, raw: Any) -> Any:
    """DEFAULTS 의 타입에 맞춰 문자열/값을 변환. 알 수 없는 키는 거부."""
    if key not in DEFAULTS:
        raise ConfigError(f"알 수 없는 설정 키: {key}")
    default = DEFAULTS[key]
    if isinstance(default, bool):
        if isinstance(raw, bool):
            return raw
        s = str(raw).strip().lower()
        if s in ("1", "true", "t", "yes", "y", "on", "참"):
            return True
        if s in ("0", "false", "f", "no", "n", "off", "거짓"):
            return False
        raise ConfigError(f"{key}: 참/거짓 값이어야 합니다 ({raw!r})")
    if isinstance(default, int):
        try:
            return int(str(raw).strip())
        except ValueError:
            raise ConfigError(f"{key}: 정수여야 합니다 ({raw!r})")
    if isinstance(default, float):
        try:
            return float(str(raw).strip())
        except ValueError:
            raise ConfigError(f"{key}: 숫자여야 합니다 ({raw!r})")
    s = str(raw).strip()
    if key == "signal_mode" and s not in ("banded", "exact", "off"):
        raise ConfigError("signal_mode 는 banded | exact | off 중 하나여야 합니다")
    if key == "render_mode" and s not in ("text", "image"):
        raise ConfigError("render_mode 는 text | image 중 하나여야 합니다")
    if key == "capture_mode" and s not in ("contact", "search"):
        raise ConfigError("capture_mode 는 contact | search 중 하나여야 합니다")
    return s


def build_config(
    hunter_count: int,
    sheet_config: Optional[Dict[str, Any]] = None,
    sheet_scaling: Optional[Dict[int, Dict[str, Any]]] = None,
) -> Tuple[Dict[str, Any], str]:
    """기본값 ⊕ 시트 Config ⊕ (시트 또는 내장) Scaling → (config, map_id)."""
    cfg = dict(DEFAULTS)
    for k, v in (sheet_config or {}).items():
        if k in DEFAULTS:
            cfg[k] = coerce(k, v)
        else:
            logger.warning("Fugitive_Config: 알 수 없는 키 무시: %s", k)
    scaling = sheet_scaling or SCALING
    n = max(min(hunter_count, max(scaling)), min(scaling))
    row = dict(scaling[n])
    map_id = str(row.pop("map_id", "car2077_12"))
    for k, v in row.items():
        if k in DEFAULTS and v not in ("", None):
            cfg[k] = coerce(k, v)
    return cfg, map_id


# ---------------------------------------------------------------------------
# 시트 로더 (gspread Spreadsheet 객체를 받는다; 없으면 None 반환)
# ---------------------------------------------------------------------------
def load_sheet_data(spreadsheet) -> Dict[str, Any]:
    """Fugitive_* 시트를 한 번 읽어 dict 로 반환. 각 항목은 없으면 None."""
    out: Dict[str, Any] = {"maps": None, "config": None, "scaling": None}
    if spreadsheet is None:
        return out
    try:
        import gspread  # noqa: F401
        from gspread.exceptions import WorksheetNotFound
    except ImportError:  # pragma: no cover
        return out

    def _records(name: str) -> Optional[List[Dict[str, Any]]]:
        try:
            return spreadsheet.worksheet(name).get_all_records()
        except WorksheetNotFound:
            logger.info("시트 %s 없음 — 내장 기본값 사용", name)
            return None

    rows = _records("Fugitive_Map")
    if rows:
        maps: Dict[str, Dict[str, tuple]] = {}
        for r in rows:
            mid = str(r.get("map_id", "")).strip()
            room = str(r.get("room", "")).strip()
            if not mid or not room:
                continue
            maps.setdefault(mid, {})[room] = (
                str(r.get("device", "")).strip(),
                str(r.get("neighbors", "")).replace(" ", ""),
                int(r.get("col") or 0),
                int(r.get("row") or 0),
            )
        out["maps"] = maps

    rows = _records("Fugitive_Config")
    if rows:
        out["config"] = {str(r.get("key", "")).strip(): r.get("value") for r in rows if str(r.get("key", "")).strip()}

    rows = _records("Fugitive_Scaling")
    if rows:
        scaling: Dict[int, Dict[str, Any]] = {}
        for r in rows:
            try:
                n = int(r.get("hunters"))
            except (TypeError, ValueError):
                continue
            row = {k: v for k, v in r.items() if k != "hunters" and v not in ("", None)}
            extra = row.pop("extra", None)
            if extra:
                try:
                    row.update(json.loads(str(extra)))
                except json.JSONDecodeError:
                    logger.warning("Fugitive_Scaling extra JSON 파싱 실패 (hunters=%s)", n)
            scaling[n] = row
        out["scaling"] = scaling or None
    return out
