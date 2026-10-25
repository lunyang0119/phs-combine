"""카드 전투 밸런스 기본값과 값 변환.

숫자는 전부 여기에 둔다. `Card_Config` 시트나 `--set key=value`, Battles.csv 의 `overrides` 열로
덮어쓸 수 있도록 `coerce()` 가 DEFAULTS 의 타입에 맞춰 변환한다 (fugitive/config.py 와 같은 규칙).
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional


class ConfigError(ValueError):
    """사용자에게 그대로 보여줄 수 있는 설정 오류."""


DEFAULTS: Dict[str, Any] = {
    # -- 기본 전투 --------------------------------------------------------
    "hand_size": 3,               # 라운드마다 뽑는 카드 수
    "damage_variance": 4,         # 피해에 더해지는 0..N 난수
    "stat_divisor": 2,            # 스탯 보정 = stat // stat_divisor
    "ai_noise": 0.15,             # 적 AI 점수에 섞는 난수 폭
    "max_players": 8,
    "report_delay_sec": 1.0,      # 정산 보고 게시 후 다음 라운드 메시지까지 대기
    "use_current_hp": False,      # True 면 시트의 current_hp 로 시작
    "allow_unregistered": False,  # 캐릭터 시트에 없는 사람도 기본 스탯으로 참가 허용
    "admin_sees_all": True,       # 주최자는 전체 카드에서 고른다
    "default_hp": 30,             # allow_unregistered 참가자 HP
    "default_stat": 10,
    "redirect_includes_self": False,
    "enemy_hp_mult": 1.0,         # 전투별 적 HP 배율 (Battles.csv overrides 로 인원수 보정)
    "show_intent_target": False,  # True 면 적 의도에 대상까지 공개 (기본: 종류만)
    "render_mode": "image",       # image | text — 현황판 PNG (Pillow+글꼴 없으면 자동 text)
    # -- 비살상 / NCPD ----------------------------------------------------
    "overkill_margin": 10,        # 남은 HP 를 이만큼 이상 초과해 깎으면 과잉 제압 (nonlethal 태그 카드는 면제)
    "ncpd_max": 3.0,              # 경계도가 이 값에 닿으면 드론 출동 → 도주 단계
    "ncpd_overkill": 1.0,         # 과잉 제압 1회당 경계도
    "ncpd_loud": 0.5,             # loud 태그 카드 사용 1회당 경계도
    "escape_turns": 5,            # 도주 단계 턴 수
    "escape_wins_needed": 3,      # 탈출에 필요한 승리 턴 수 (= 시작 경계도)
    "escape_hint": True,          # 매 턴 경찰이 쓰지 않을 행동 하나를 공개 (안전한 선택이 항상 존재)
    # -- 스캐빈저 ---------------------------------------------------------
    "drag_turns": 2,              # 쓰러진 아군을 이 라운드 수 동안 끌고 가면 약탈
    "hack_weak_mult": 1.5,        # cheap_chrome 특성: 해킹 수치 배율
    # -- 돌 ---------------------------------------------------------------
    "chip_stability": 5,          # 인격 칩 안정도 (해킹 명중마다 감소, 0 이면 정지)
    "chip_per_hack": 1,
    "chip_reboot_mult": 2,        # 리부트 모드 중 해킹 피해 배율
    "chip_round_cap": 2,          # 한 라운드에 깎을 수 있는 칩 안정도 상한 (리부트 중엔 2배)
    "chip_stun_bonus": 1,         # 돌은 기절하지 않는다. 대신 시스템 리셋이 칩을 이만큼 더 깎는다
    "screen_durability": 3,       # 단말기 액정 내구도
    "doll_modes": "cling:doll_cling:4;rampage:doll_claws:4;reboot:doll_reboot:2",   # 모드:카드:가중치
    "doll_hp_overkill_ok": False, # True 면 돌을 HP 로 쓰러뜨려도 과잉 제압 판정 안 함
    # -- 보느와의 방화벽 (팀 방어 게이지) ------------------------------------
    "firewall_per_damage": 10,    # 아군이 받은 피해 N 당 +1
    "firewall_per_defend": 1,     # 아군이 방어 카드를 낼 때마다
    "firewall_per_down": 2,       # 아군이 쓰러질 때마다
    "firewall_max": 9,
    "firewall_cost_cover": 3,     # 팀 엄폐: 아군 전원 이번 라운드 피해 감소
    "firewall_cost_reboot": 2,    # 긴급 재부팅: 아군 디버프·붙잡힘 해제
    "firewall_cost_aim": 2,       # 스마트 탄 유도: 이번 라운드 아군 피해 난수 0, 회피 무시
    "firewall_cost_scan": 1,      # 적 스캔: 이번 라운드 적 의도의 대상 공개
    "firewall_cost_maxdoc": 4,    # 맥스닥: 가장 위험한 아군 회복 (쓰러진 아군도 일으킴)
    "firewall_cover_shield": 0.5,
    "firewall_maxdoc_ratio": 0.3,
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
            return int(float(str(raw).strip()))
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


def parse_overrides(text: str) -> Dict[str, str]:
    """Battles.csv `overrides` 열: `key=value;key=value`."""
    out: Dict[str, str] = {}
    for chunk in str(text or "").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        k, sep, v = chunk.partition("=")
        if not sep:
            raise ConfigError(f"overrides 형식 오류 (key=value): {chunk!r}")
        out[k.strip()] = v.strip()
    return out
