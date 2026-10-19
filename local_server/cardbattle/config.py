"""카드 전투 밸런스 기본값과 값 변환.

숫자는 전부 여기에 둔다. `Card_Config` 시트나 `--set key=value` 로 덮어쓸 수 있도록
`coerce()` 가 DEFAULTS 의 타입에 맞춰 변환한다 (fugitive/config.py 와 같은 규칙).
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional


class ConfigError(ValueError):
    """사용자에게 그대로 보여줄 수 있는 설정 오류."""


DEFAULTS: Dict[str, Any] = {
    "hand_size": 3,               # 턴마다 뽑는 카드 수
    "damage_variance": 4,         # 피해에 더해지는 0..N 난수
    "stat_divisor": 4,            # 스탯 보정 = stat // stat_divisor
    "ai_noise": 0.15,             # 적 AI 점수에 섞는 난수 폭
    "max_players": 8,
    "enemy_turn_delay_sec": 1.0,  # 아군 정산 게시 후 적 턴까지 대기
    "use_current_hp": False,      # True 면 시트의 current_hp 로 시작
    "allow_unregistered": False,  # 캐릭터 시트에 없는 사람도 기본 스탯으로 참가 허용
    "admin_sees_all": True,       # 주최자는 전체 카드에서 고른다
    "default_hp": 30,             # allow_unregistered 참가자 HP
    "default_stat": 10,
    "redirect_includes_self": False,
}


def coerce(key: str, raw: Any) -> Any:
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
    return str(raw).strip()


def build_config(overrides: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """기본값 ⊕ 덮어쓰기. 알 수 없는 키는 ConfigError."""
    cfg = dict(DEFAULTS)
    for k, v in (overrides or {}).items():
        cfg[k] = coerce(k, v)
    return cfg
