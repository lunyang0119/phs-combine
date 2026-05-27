"""
게임 시스템 상수 정의
모든 매직 넘버와 설정값을 중앙 관리
"""

# ========== 디버그 모드 ==========
DEBUG_MODE = False  # 전역 디버그 모드 (기본값: False)

# ========== 난이도 ==========
DIFFICULTY_MULTIPLIERS = {
    "쉬움": 0.7,
    "보통": 1.0,
    "어려움": 1.3,
    "극악": 1.6
}
PLAYER_COUNT_MULTIPLIERS = {
    1: 0.6, 
    2: 0.8, 
    3: 1.0
}

# ========== 확률 관련 ==========
ENV_EFFECT_CHANCE = 0.8  # 환경 효과 발동 확률
MP_STEAL_CHANCE = 0.3    # MP 흡수 확률
MONSTER_REACTION_CHANCE = 0.5  # 몬스터 피격 반응 확률
DMW_CATSI_FAIL_CHANCE = 0.2     # DMW 실패 시 캐트시 포함 확률

# ========== 전투 시스템 ==========
MAX_COMBAT_ROUNDS = 100   # 최대 전투 라운드
SYNC_INTERVAL = 2        # 홀수 라운드마다 동기화 (2 = 매 2턴마다)
CACHE_RELOAD_INTERVAL = 10  # 캐시 재로드 주기 (현재는 사용 안 함 - 추후 삭제 예정)
DEFENSE_DURATION = 2     # 방어 플래그 지속 턴
EVASION_DURATION = 2     # 회피 플래그 지속 턴
DEAD_REVIVAL_TURNS = 4   # 전투불능 부활 가능 턴 수

# ========== 환경 효과 ==========
LAVA_DAMAGE_PERCENT = 0.03    # 마황 지대 데미지 (HP의 5%)
MANA_STORM_MP_GAIN = 5      # 라이프 스트림 폭풍 MP 회복량
GRAVITY_EVASION_PENALTY = 0.5  # 중력 이상 회피율 감소 (50%)

# ========== 크리티컬 시스템 ==========
BASE_CRIT_CHANCE = 0.10   # 기본 크리티컬 확률 
CRIT_MULTIPLIER = 1.5     # 크리티컬 데미지 배율 (1.5배)

# ========== 연계 공격 ==========
COMBO_2HIT_BONUS = 0.1   # 2연타 데미지 보너스 (10%)
COMBO_3HIT_BONUS = 0.2   # 3연타 데미지 보너스 (20%)
COMBO_4HIT_BONUS = 0.3   # 4연타 데미지 보너스 (30%)
COMBO_5HIT_BONUS = 0.4   # 5연타 데미지 보너스 (40%)
COMBO_6HIT_BONUS = 0.5   # 6연타 데미지 보너스 (50%)
COMBO_7HIT_BONUS = 0.6   # 7연타 데미지 보너스 (60%)
COMBO_8HIT_BONUS = 0.7   # 8연타 데미지 보너스 (70%)
COMBO_9HIT_BONUS = 0.8   # 9연타 데미지 보너스 (80%)
COMBO_MAX_COUNT = 9      # 최대 연계 횟수
COMBO_BREAK_HEAT_MP = 10  # 9연타 초과 시 MP 회복량 (열기)
COMBO_BREAK_WARM_MP = 5   # 5-8연타 끊김 시 MP 회복량 (잔열)

# ========== DMW 시스템 ==========
DMW_SEPHIROTH_VALUE = 7    # 세피로스 발동 값
DMW_SUCCESS_THRESHOLD = 35 # 성공 임계값 (일반 전투)
DMW_BOSS_MODE_THRESHOLD = 18 # 보스전투 모드 성공 임계값 (약 2배 확률)
DMW_SLOT_DELAY = 0.8       # 슬롯 변경 딜레이 (초)
DMW_MEMORY_DELAY = 1.5     # 대사 출력 간격 (초)
DMW_FINAL_DELAY = 0.5      # 최종 슬롯 후 대기 시간 (초)
DMW_TOKURA_DEATH_NORMAL = 3  # 토쿠라 즉사 턴 (일반 전투)
DMW_TOKURA_DEATH_BOSS = 10   # 토쿠라 즉사 턴 (보스 전투)

# ========== 리미트 브레이크 ==========
STRIKER_LIMIT_HP_COST = 0.3    # 스트라이커 HP 소모율 (30%)
STRIKER_LIMIT_DAMAGE = 0.4     # 스트라이커 데미지 (최대 HP의 40%)
WEAVER_LIMIT_MP_COST = 0.3     # 위버 MP 소모율 (40%)
WEAVER_LIMIT_HEAL = 0.5        # 위버 힐량 (최대 HP의 50%)
SOLDIER_LIMIT_HP_COST = 0.3    # 솔져 HP 소모율 (20%)
SOLDIER_LIMIT_MIN_HITS = 2     # 솔져 최소 타격 횟수
SOLDIER_LIMIT_MAX_HITS = 8     # 솔져 최대 타격 횟수
TURKS_LIMIT_MP_COST = 30       # 턱스 MP 소모
TURKS_STUN_DURATION = 3        # 턱스 스턴 지속 시간

# ========== 보스 스킬 ==========
RAGE_MODE_HP_THRESHOLD = 30    # Rage 모드 발동 HP %
RAGE_MODE_DAMAGE_MULTIPLIER = 1.5  # Rage 모드 데미지 배율
CHARGE_ATTACK_INTERVAL = 5     # Charge Attack 발동 주기 (3턴마다)
CHARGE_ATTACK_MULTIPLIER = 2.0 # Charge Attack 데미지 배율
BOSS_KEYWORD_RAGE_DEFAULT = 0.3 # 대사에 따른 기본 분노 버프 비율

# ========== 기본 스탯 ==========
BASE_HP = 80           # 기본 HP
BASE_MP = 40            # 기본 MP (마테리아 위버는 60)
STAT_BONUS_POINTS = 10  # 캐릭터 생성 시 보너스 포인트
HP_PER_PHYSICS = 10     # 근력 1당 HP 증가량
MP_PER_MAGIC = 5       # 마법 1당 MP 증가량
MATERIA_LIST_DEFAULT = '없음'
MATERIA_LIST_ALL = '["파이어", "파이라", "파이가", "블리자드", "블리자라", "블리자가", "선더", "선더라", "선더가", "알테마", "케알", "케알라", "케알가", "풀케어"]'

# ========== 몬스터 기본값 ==========
MONSTER_MAX_MP = 1000000  # 몬스터 기본 MP (무한)

# ========== UI 타임아웃 ==========
ACTION_VIEW_TIMEOUT = 600.0  # 행동 선택 UI 타임아웃 (초)
TARGET_SELECT_TIMEOUT = 600.0  # 대상 선택 UI 타임아웃 (초)

# ========== 색상 (Discord Embed) ==========
COLOR_DAMAGE = 0xEA1D1E      # 데미지 표시 색상 (빨강)
COLOR_HEAL = 0x2EE179        # 힐 표시 색상 (초록)
COLOR_BUFF = 0xFFD700        # 버프 색상 (금색)
COLOR_DEBUFF = 0x808080      # 디버프 색상 (회색)
COLOR_DMW_FAIL = 0x808080    # DMW 실패 색상 (회색)

# ========== 환경 효과 목록 ==========
ENVIRONMENT_EFFECTS = [
    'lava_zone',        # 마황 지대
    'mana_storm',       # 라이프스트림 폭풍
    'gravity_anomaly',  # 중력 이상
    'time_warp'         # 시간 왜곡
]

# ========== 몬스터 행동 가중치 ==========
MONSTER_ACTION_WEIGHTS_NO_MAGIC = {
    'physic_atk': 70,
    'defend': 20,
    'evasion': 10
}

MONSTER_ACTION_WEIGHTS_WITH_MAGIC = {
    'physic_atk': 40,
    'magic': 40,
    'defend': 10,
    'evasion': 10
}

# ========== 상태 효과 한국어 매핑 ==========
STATUS_EFFECT_KOREAN_NAMES = {
    'petrified': '석화',
    'stunned': '기절',
    'turks_stun': '전격 제압(기절)',
    'tokura_death': '죽음의 선고',
    'physics_debuff': '근력 감소',
    'magic_debuff': '마법 감소',
    'agility_debuff': '민첩 감소',
    'charm_debuff': '매력 감소',
    'hp_reduce': 'HP 감소',
    'crit_buff': '크리티컬 확률 증가',
    'physics_buff': '근력 증가'
}

# ========== 보스 대화 키워드 효과 ==========
# 좋은 키워드 (몬스터에게 디버프)
BOSS_KEYWORD_GOOD = {
    '친구': {'type': 'hp_reduce', 'value': 0.05, 'description': 'HP -5%'},
    '함께': {'type': 'physics_debuff', 'value': 0.10, 'duration': 999, 'description': '공격력 -10%'},
    '같이': {'type': 'physics_debuff', 'value': 0.10, 'duration': 999, 'description': '공격력 -10%'},
    '이름': {'type': 'agility_debuff', 'value': 0.10, 'duration': 999, 'description': '회피 -10%'}
}

# 나쁜 키워드 (몬스터에게 버프)
BOSS_KEYWORD_BAD = {
    '세피로스': {'type': 'physics_buff', 'value': 0.20, 'duration': 4, 'description': '공격력 +20% (3턴)'},
    '괴물': {'type': 'rage_trigger', 'value': 0.30, 'description': '분노 스킬 발동 (공격력 +30%, 영구)'},
    '증명': {'type': 'crit_buff', 'value': 0.05, 'duration': 999, 'description': '크리티컬 +5% (영구)'}
}

# ========== UI/UX 딜레이 (초) ==========
BOSS_KEYWORD_MESSAGE_DELAY = 1.5  # 보스 키워드 효과 메시지 후 대기
MONSTER_REACTION_DELAY = 0.5      # 몬스터 피격 반응 간격
ACTION_MESSAGE_DELAY = 1.0        # 일반 행동 메시지 후 대기
DMW_RESULT_DELAY = 0.7            # DMW 결과 표시 후 대기
MONSTER_TURN_END_DELAY = 2.0      # 몬스터 턴 종료 대기
BATTLE_END_DELAY = 1.0            # 전투 종료 메시지 대기
DEAD_MESSAGE_DELAY = 1.0          # 전투불능 메시지 대기