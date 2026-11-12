"""카드 전투 밸런스 기본값과 값 변환.

숫자는 전부 여기에 둔다. `Card_Config` 시트나 `--set key=value`, Battles.csv 의 `overrides` 열로
덮어쓸 수 있도록 `coerce()` 가 DEFAULTS 의 타입에 맞춰 변환한다 (fugitive/config.py 와 같은 규칙).
목록 값(firewall_options, modes, weapon_bonus, first_strike_cards …)은 `;` 로 적되, Battles.csv 의
overrides 안에서는 `;` 가 구분자이므로 `,` 로 적는다 (engine.split_list 가 둘 다 받는다).
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
    "admin_sees_all": False,      # True 면 주최자가 항상 전체 카드에서 고른다. 기본은 모두 무작위 패 (`/카드 옵션:관리자` 로 사람별 전환)
    "default_hp": 30,             # allow_unregistered 참가자 HP
    "default_stat": 10,
    "redirect_includes_self": False,
    "enemy_hp_mult": 1.0,         # 전투별 적 HP 배율 (Battles.csv overrides 로 인원수 보정)
    "show_intent_target": False,  # True 면 적 의도에 대상까지 공개 (기본: 종류만)
    "render_mode": "image",       # image | text — 현황판 PNG (Pillow+글꼴 없으면 자동 text)
    "tips": True,                 # 전투에서 처음 일어난 일을 정산 보고에 💡 한 줄로 설명
    # -- 비살상 / NCPD ----------------------------------------------------
    "overkill_margin": 15,        # 남은 HP 를 이만큼 이상 초과해 깎으면 과잉 제압 (nonlethal 태그 카드는 면제)
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
    # -- 모드 일반화 (traits 에 modes 가 있는 적) ----------------------------
    "modes": "",                  # `mode:card:weight;…` (비어 있으면 doll_modes 를 쓴다)
    "modes_norepeat": "reboot",   # 연속으로 나오지 않는 모드 목록
    # -- 몸의 보느와 --------------------------------------------------------
    "first_strike_cards": "bw_ambush",   # 선제 라운드에 적이 내는 카드 (first_strike 기믹)
    "daemon_damage": 5,           # 감염된 채 행동하면 스택당 자해
    "daemon_turns": 2,            # 감염 지속 (걸린 다음 라운드 끝까지)
    "daemon_max_infected": 4,     # 동시 감염 상한 (넘으면 기감염자에게 겹친다)
    "ice_layers": 3,              # 블랙 ICE 층 수 (ice 기믹)
    "ice_reduction": 0.3,         # 층당 피해 감소 (배율 하한 0.1)
    "ice_round_cap": 2,           # 한 라운드에 벗길 수 있는 층
    "life_support_rounds": 8,     # 생명 유지 시계 (라운드마다 −1, 0 이면 발작 종료)
    # -- 보느와의 방화벽 (팀 방어 게이지) ------------------------------------
    "firewall_per_damage": 10,    # 아군이 받은 피해 N 당 +1
    "firewall_per_defend": 1,     # 아군이 방어 카드를 낼 때마다
    "firewall_per_down": 2,       # 아군이 쓰러질 때마다
    "firewall_max": 9,
    "firewall_options": "cover;reboot;maxdoc",   # 이 전투에서 열린 옵션 (점진 공개). 비어 있으면 전부
    "firewall_cost_cover": 3,     # 팀 엄폐: 아군 전원 이번 라운드 피해 감소
    "firewall_cost_reboot": 2,    # 긴급 재부팅: 아군 디버프·붙잡힘·감염 해제
    "firewall_cost_aim": 2,       # 스마트 탄 유도: 이번 라운드 아군 피해 난수 0, 회피 무시
    "firewall_cost_scan": 1,      # 적 스캔: 이번 라운드 적 의도의 대상 공개
    "firewall_cost_maxdoc": 4,    # 맥스닥: 가장 위험한 아군 회복 (쓰러진 아군도 일으킴)
    "firewall_cover_shield": 0.5,
    "firewall_maxdoc_ratio": 0.3,
    # -- 준비 단계 / 무기 / 금고 (긱 경제) ----------------------------------
    "prep_enabled": True,         # 전투 전에 무기를 줍고 에디로 개조하는 준비 단계
    "prep_actions": 2,            # 1인당 개조 횟수
    "prep_sandbox": False,        # True 면 임시 금고 (파일을 읽지도 쓰지도 않음, 보수 없음) — 튜토리얼용
    "price_normal": 100,          # 정식 개조 (확정, weapon_normal_max 까지)
    "price_black": 50,            # 뒷골목 개조 (d10 도박, weapon_max 의 유일한 길)
    "pot_start_per_player": 100,  # 금고 파일이 없을 때 첫 금고 = 인원 × 이 값
    "weapon_bonus": "0;3;6;10",   # mk.1..mk.4 의 피해 보너스
    "weapon_start_max": 2,        # 주운 무기의 시작 등급 상한 (mk.1~2)
    "weapon_normal_max": 3,       # 정식 개조로 오를 수 있는 등급
    "weapon_max": 4,
    "gamble_fail_max": 3,         # d10 이 이 값 이하면 실패 (한 단계 하락)
    "gamble_crit_refund": True,   # 10 이면 성공 + 환불
    "gamble_fumble_drop": 2,      # 1 이면 이만큼 하락 (바닥 mk.1)
    "field_mod_cost": 50,         # 전투 중 현장 개조 비용 (실패해도 강등 없음, 다음 공격 불발)
    "reward_per_player": 150,     # 긱 보수 = 인원 × 이 값 × 결과 비율 − 벌금
    "penalty_overkill": 50,       # 과잉 제압 1회당 벌금
    "penalty_loud": 20,           # 소음 카드 1회당 벌금
    "reward_fail_ratio": 0.0,     # 전멸·잡힘의 보수 비율
    "reward_collapse_ratio": 0.5, # 보느와가 발작으로 쓰러진(collapse) 결말의 보수 비율
    # -- 전투 브리핑 (1라운드 전에 그 전투의 특수 규칙을 자세히 설명하는 임베드) -------------
    "briefing": True,
    # -- 리미트 (limit 기믹: 직업별 전투당 1회 카드) ----------------------------------
    "limit_cards": "스트라이커:limit_edge;마테리아 위버:limit_net;솔져:limit_soldier;턱스:limit_turks;*:limit_any",
    # -- 플래시백 (flash 기믹: 라운드마다 4d10 + 매력 보정, DMW 의 카드판) ----------------
    "flash_threshold": 29,        # 이 값 이상이면 발동 (매력 10 이하 ≈ 13%, 매력 16 ≈ 27%)
    "flash_per_round": 1,         # 한 라운드에 터질 수 있는 인원 (굴림이 높은 순)
    "flash_effects": "",          # 나올 수 있는 효과 (비어 있으면 engine.FLASH_EFFECTS 전부)
    "flash_extra_cards": 1,       # extra_card: 이번 라운드 패 +N
    "flash_boost": 6,             # boost: 다음 피해 카드 +N
    "flash_guard": 0.5,           # guard: 이번 라운드 받는 피해 감소
    "flash_firewall": 2,          # firewall: 방화벽 게이지 +N
    "flash_heal_ratio": 0.2,      # heal: 최대 HP 비율 회복
    # -- 팀 연계 (chain 기믹: 같은 라운드에 같은 적을 여럿이 때리면) -----------------------
    "chain_bonus": 0.1,           # 앞서 맞힌 아군 1명당 피해 +10% (과잉 제압 판정에는 넣지 않는다)
    "chain_max": 4,               # 보너스 단계 상한
    "chain_firewall_at": 3,       # 한 적에게 이만큼 이어지면 방화벽 충전
    "chain_firewall": 1,
    # -- 국면 전환 (phases 기믹) -----------------------------------------------------
    "phases": "",                 # `enemy_id:hp%:card_id` 목록. 문턱 아래로 내려가면 다음 라운드에 그 카드를 낸다 (1회)
    # -- 말 걸기 (talk 기믹: 2d10 + 매력 보정) ---------------------------------------
    "talk_dc": 16,                # 목표값
    "talk_dc_step": 2,            # 같은 상대에게 걸 때마다 목표값 +N
    "talk_crit_margin": 8,        # 목표값을 이만큼 넘기면 대성공 (기절)
    "talk_atk_down": 0.5,         # 성공: 상대의 다음 공격 감소
    "talk_life": 1,               # 성공: 생명 유지 시계 +N (life_support)
    "talk_ncpd": 0.5,             # 성공: 경계도 −N (ncpd)
    "talk_chip": 1,               # 성공: 돌의 칩 안정도 −N (doll)
    # -- 환경 (env 기믹: 전투 시작 때 하나를 뽑는다) -----------------------------------
    "env_pool": "blackout;emp;acid;glitch",
    "env_chance": 1.0,
    "env_emp_firewall": 1,        # 전자기 폭풍: 라운드마다 방화벽 +N
    "env_acid_ratio": 0.03,       # 산성비: 라운드 끝 최대 HP 비율 피해 (HP 1 아래로는 안 내려감)
    # -- 튜토리얼 ----------------------------------------------------------
    "tutorial_script": "tut1",    # Tutorial.csv 의 script_id
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
    """Battles.csv `overrides` 열: `key=value;key=value` (목록 값은 `,` 로)."""
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
