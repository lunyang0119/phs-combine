# 👹 보스전 완전 가이드

**최종 업데이트**: 2025-10-06

---

## 📖 목차

1. [보스전 개요](#보스전-개요)
2. [보스 스킬 시스템](#보스-스킬-시스템)
3. [보스 스킬 타입 상세](#보스-스킬-타입-상세)
4. [공생 메커니즘](#공생-메커니즘)
5. [난이도별 차이](#난이도별-차이)
6. [보스전 공략 전략](#보스전-공략-전략)
7. [FAQ](#faq)

---

## 보스전 개요

### 보스란?

보스는 일반 몬스터와 다른 특별한 적으로, 다음과 같은 특징을 가집니다:

- **`monster_type = "boss"`** (Google Sheets Monsters 시트)
- **높은 HP**: 일반 몬스터의 3-10배
- **강력한 스탯**: 근력, 마법, 민첩, 매력이 모두 높음
- **보스 스킬**: HP 임계값 도달 시 자동으로 강력한 스킬 발동
- **특수 메커니즘**: 소환, 공생, 페이즈 전환 등

### 보스 데이터 구조

보스는 두 개의 Google Sheets에 데이터가 저장됩니다:

#### 1. Monsters 시트
```
monster_id, monster_name, type, max_hp, physics, magic, agility, charm,
magic_enabled_flag, materia_owned, damage_reaction_messages
```

- `type`: **"boss"**로 설정되어야 함
- `max_hp`: 1000-5000 권장
- `damage_reaction_messages`: JSON 배열 (피격 시 랜덤 대사)

#### 2. Boss_Skills 시트 (MultiIndex)
```
boss_id, skill_id, trigger_hp_percent, effect_type, effect_value,
target, description, embed_message, used_flag
```

- **MultiIndex**: (boss_id, skill_id) - 보스당 여러 스킬 가능
- `trigger_hp_percent`: HP가 이 % 이하로 떨어지면 발동 (예: 80, 50, 20)
- `used_flag`: 0 = 미사용, 1 = 사용됨 (중복 발동 방지)

---

## 보스 스킬 시스템

### 발동 조건

보스 스킬은 **HP 임계값(trigger_hp_percent)**에 도달하면 **자동으로 발동**됩니다.

**예시**:
```
HP 100% → 80% 도달 → Skill A 발동
HP 80% → 50% 도달 → Skill B 발동
HP 50% → 20% 도달 → Skill C 발동 (최종 발악)
```

### 발동 시점

보스 스킬은 다음 두 시점에 체크됩니다:

1. **몬스터 턴 종료 후** (`_handle_monster_turn()` 내부)
2. **라운드 종료 시** (`_handle_round_end()` 내부)

### used_flag 메커니즘

- 스킬이 발동되면 `used_flag = 1`로 설정
- **같은 스킬이 두 번 발동되지 않음**
- 전투 종료 시 모든 보스의 `used_flag`가 0으로 리셋됨 (`reset_boss_skills()`)

---

## 보스 스킬 타입 상세

### 💀 death_sentence (죽음의 선고)

**효과**: 대상에게 `tokura3` 상태이상 부여 → 3턴 후 즉사

**메커니즘**:
1. 대상의 `status`에 `tokura3` 추가
2. 매 라운드 종료 시 카운트다운: `tokura3` → `tokura2` → `tokura1` → `tokura0` (즉사)
3. `tokura0`이 되면 `is_dead = 3` (즉시 전투불능)

**대응 방법**:
- 즉시 회복 마법 사용 (죽음의 선고 해제)
- 3턴 안에 보스를 처치
- 라켈 DMW로 전체 회복 (상태이상 해제)

**예시 설정**:
```
effect_type: death_sentence
effect_value: 3 (턴 수)
target: RANDOM_PLAYER (랜덤 플레이어 1명)
```

---

### 👥 summon (소환)

**효과**: 새로운 몬스터를 전투에 추가

**메커니즘**:
1. `effect_value`에 지정된 `monster_id` 소환
2. 소환된 몬스터는 `participants_cache`에 추가
3. 턴 순서(`order_infos`)에 자동 삽입
4. 소환된 몬스터는 다음 턴부터 행동 가능

**전략적 활용**:
- Phase 전환 시 소환 (HP 50% 이하)
- 여러 웨이브로 소환 (HP 80%, 50%, 20%)
- 공생 메커니즘과 결합 (모체 + 포자)

**예시 설정**:
```
effect_type: summon
effect_value: monster_goblin_minion (소환할 몬스터 ID)
target: SELF
```

**주의사항**:
- 소환 실행 메서드: `BossSkillSystem.execute_summon()`
- 중복 소환 방지를 위해 `used_flag` 필수
- 소환된 몬스터가 이미 존재하면 소환 실패

---

### 💥 damage_all (전체 공격)

**효과**: 모든 플레이어에게 동시 대미지

**계산식**:
```python
damage = effect_value + random.randint(1, 20)
```

**메커니즘**:
1. 모든 살아있는 플레이어(`is_dead = 0`) 선택
2. 각 플레이어에게 대미지 적용
3. 방어/회피 플래그 무시 가능 (설정 가능)

**대응 방법**:
- 사전에 방어 자세 (`defend_flag`)
- HP를 충분히 유지 (최대 HP의 70% 이상)
- 전체 회복 마테리아 준비

**예시 설정**:
```
effect_type: damage_all
effect_value: 50 (기본 대미지)
target: ALL_PLAYERS
```

---

### 🩸 lifesteal (생명력 흡수)

**효과**: 대상에게 대미지 + 보스 HP 회복

**계산식**:
```python
damage = effect_value + random.randint(1, 20)
heal_amount = int(damage * 0.5)  # 50% 회복
```

**메커니즘**:
1. 대상에게 대미지 적용
2. 가한 대미지의 50%만큼 보스 HP 회복
3. 최대 HP 초과 불가

**전략적 의미**:
- 보스가 HP를 회복하는 유일한 수단
- 높은 대미지 = 많은 회복
- 지속 딜링 필요

**대응 방법**:
- 회피 자세로 대미지 무효화
- 빠르게 보스 HP 0으로 만들기
- 생명력 흡수 직후 집중 공격

**예시 설정**:
```
effect_type: lifesteal
effect_value: 60 (기본 대미지)
target: HIGHEST_HP_PLAYER (HP 가장 높은 플레이어)
```

---

### ⚡ damage_mp_drain (MP 흡수)

**효과**: 대상에게 대미지 + MP 감소

**계산식**:
```python
damage = effect_value + random.randint(1, 20)
mp_drain = 30  # 고정 30 MP 감소
```

**메커니즘**:
1. 대상에게 대미지 적용
2. 대상의 현재 MP에서 30 감소
3. MP가 0 이하가 되면 0으로 고정

**전략적 의미**:
- 마법 사용 봉쇄 (특히 마테리아 위버)
- 회복 마법 차단
- 마법 딜러 무력화

**대응 방법**:
- MP 관리 철저히 (마법 신중히 사용)
- 물리 공격 위주로 전환
- 라이프 스트림 폭풍 환경 효과 이용 (MP +3/턴)

**예시 설정**:
```
effect_type: damage_mp_drain
effect_value: 40 (기본 대미지)
target: HIGHEST_MP_PLAYER
```

---

### 📉 stat_debuff (스탯 디버프)

**효과**: 대상의 특정 스탯 영구 감소

**메커니즘**:
1. `effect_value` 형식: `"physics:-5"`, `"magic:-3"`, `"agility:-2"`
2. 대상의 스탯에서 값을 빼기
3. **영구적** (전투 종료까지 유지)
4. HP/MP는 스탯 변화에 따라 재계산 안 됨

**디버프 가능 스탯**:
- `physics`: 물리 공격력, 방어력 감소
- `magic`: 마법 위력 감소
- `agility`: 턴 순서 느려짐, 회피율 감소
- `charm`: DMW 성공률 감소

**대응 방법**:
- 디버프 받은 플레이어는 후방 지원
- 다른 플레이어가 메인 딜러 역할
- 버프 스킬/아이템으로 상쇄 (미구현)

**예시 설정**:
```
effect_type: stat_debuff
effect_value: physics:-5
target: RANDOM_PLAYER
```

---

### 💪 buff_self (자기 강화)

**효과**: 보스 자신의 스탯 증가

**메커니즘**:
1. `effect_value` 형식: `"physics:+10"`, `"magic:+5"`
2. 보스의 스탯 증가
3. **영구적** (전투 종료까지 유지)
4. 다음 공격부터 강화된 스탯 적용

**강화 가능 스탯**:
- `physics`: 물리 공격력 증가 → 더 강한 공격
- `magic`: 마법 위력 증가
- `agility`: 턴 순서 빨라짐
- `charm`: DMW 저항 (미구현)

**전략적 의미**:
- HP 50% 이하에서 발동 → **분노 모드**
- 공격력 1.5배 또는 +10 스탯
- 후반 전투 난이도 급상승

**대응 방법**:
- 강화 전에 HP를 빠르게 깎기
- 방어 자세 적극 활용
- 회피 우선 (민첩 10 이상)

**예시 설정**:
```
effect_type: buff_self
effect_value: physics:+10
target: SELF
```

---

### ☠️ instant_hp_one (즉사 저주)

**효과**: 대상의 HP를 1로 강제 설정

**메커니즘**:
1. 대상의 `current_hp = 1`
2. 방어/회피 무시
3. 즉시 적용, 회복 필요

**전략적 의미**:
- 최종 보스 전용 스킬
- HP가 높아도 순식간에 위기
- 다음 공격에 즉사 가능

**대응 방법**:
- 즉시 회복 (최우선)
- 방어 자세로 다음 턴 버티기
- DMW 라켈 대기

**예시 설정**:
```
effect_type: instant_hp_one
effect_value: 1
target: RANDOM_PLAYER
```

---

## 공생 메커니즘

### 공생이란?

공생(Symbiosis)은 **모체(Host)**와 **포자(Spore)** 몬스터 간의 특수한 관계입니다.

### 공생 관계 설정

**Monsters 시트**에서 설정:
```
monster_id: mother_mushroom
symbiosis_host_id: (비워둠)
symbiosis_role: host

monster_id: spore_minion
symbiosis_host_id: mother_mushroom
symbiosis_role: spore
```

### 공생 메커니즘

#### 1. 자동 치유 (Symbiosis Heal)

**조건**: 포자가 살아있을 때, 매 라운드 종료 시

**효과**:
```python
포자 1마리당 모체 HP +5 회복
```

**예시**:
- 포자 3마리 생존 → 모체 +15 HP/턴
- 포자 전부 제거 → 모체 회복 중단

**구현 위치**: `StatusEffectManager.symbiosis_heal()`

#### 2. 공생 사멸 (Symbiosis Death)

**조건**: 모체가 사망할 때

**효과**:
- 모든 관련 포자가 **즉시 동시 사멸**
- 포자의 `is_dead = 3` (즉시 전투불능)

**전략적 의미**:
- 모체를 처치하면 포자도 자동 제거
- 포자를 먼저 처치하면 모체 회복 차단

**구현 위치**: `StatusEffectManager.symbiosis_death()`

### 공생 보스 공략법

**전략 A: 포자 우선 제거**
1. 모든 포자 처치 → 모체 회복 차단
2. 모체 집중 공격
3. 장점: 안정적, 단점: 시간 소요

**전략 B: 모체 직접 공격**
1. 포자 무시하고 모체 집중 공격
2. 모체 처치 → 포자 자동 사멸
3. 장점: 빠름, 단점: 모체 HP 회복 감수

**권장 전략**:
- 포자 2마리 이하: 모체 직접 공격
- 포자 3마리 이상: 포자 우선 제거

---

## 난이도별 차이

### 난이도 설정

전투 시작 시 난이도 선택:
```
/전투시작 participants:[ID] difficulty:[난이도]
```

- **쉬움** (easy)
- **보통** (normal) - 기본값
- **어려움** (hard)
- **극악** (nightmare)

### 난이도별 몬스터 스탯 배율

`BattleManager.adjust_monster_stats_by_player_count()` 참조:

| 난이도 | HP 배율 | 공격력 배율 | 설명 |
|--------|---------|-------------|------|
| 쉬움 | 0.7x | 0.8x | 초보자 추천 |
| 보통 | 1.0x | 1.0x | 기본 밸런스 |
| 어려움 | 1.3x | 1.2x | 숙련자용 |
| 극악 | 1.8x | 1.5x | 최고 난이도 |

### 플레이어 수 보정

플레이어 수에 따라 몬스터 스탯 추가 보정:

```python
# 2인 파티
HP: 기본 * 1.0
ATK: 기본 * 1.0

# 3인 파티
HP: 기본 * 1.3
ATK: 기본 * 1.1

# 4인+ 파티
HP: 기본 * 1.6
ATK: 기본 * 1.2
```

**최종 스탯**:
```
최종 HP = 기본 HP * 난이도 배율 * 인원 배율
최종 ATK = 기본 ATK * 난이도 배율 * 인원 배율
```

---

## 보스전 공략 전략

### 사전 준비

#### 1. 캐릭터 빌드
```
딜러 (스트라이커):
- 근력 15+ (HP 150+, 강한 물리 공격)
- 민첩 12+ (턴 우선권)
- 매력 10+ (DMW 확률)

힐러 (마테리아 위버):
- 마법 15+ (MP 150+, 강력한 회복)
- 근력 12+ (HP 확보)
- 민첩 10+ (회복 타이밍)
```

#### 2. 마테리아 선택
```
딜러: DAMAGE (ALL_ENEMY 권장)
힐러: HEAL (ALL_ALLY 필수)
솔져: DAMAGE (단일 대상 고화력)
턱스: STUN 또는 DEBUFF
```

#### 3. 팀 구성
```
2인: 딜러 + 힐러
3인: 딜러 2 + 힐러
4인: 딜러 2 + 힐러 + 서포터
```

### 전투 중 전략

#### Phase 1 (HP 100-80%)
- 보스 패턴 파악
- MP 아껴서 사용
- 리미트 게이지 충전
- 환경 효과 확인

#### Phase 2 (HP 80-50%)
- **첫 보스 스킬 발동**
- 소환 스킬 대비 (포자 우선 제거)
- HP 70% 이상 유지
- 방어 자세 적극 활용

#### Phase 3 (HP 50-20%)
- **두 번째 보스 스킬 발동**
- 전체 공격 대비 (전체 HP 확인)
- 리미트 브레이크 사용 시작
- DMW 라켈/잭스 활용

#### Phase 4 (HP 20% 이하)
- **최종 발악 스킬**
- 즉사 스킬 대비 (HP 1 주의)
- 총공격 개시
- 회복 마테리아 아껴두기

### 상황별 대처법

#### 죽음의 선고 (tokura)
```
1. 즉시 회복 마법 시전
2. 라켈 DMW 대기
3. 3턴 안에 보스 처치 시도
```

#### 소환 스킬
```
1. 소환된 몬스터 즉시 확인
2. 포자면 → 포자 우선 처리
3. 일반 몬스터면 → 무시하고 보스 공격
```

#### 전체 공격
```
1. 사전 방어 자세
2. 전체 HP 70% 이상 유지
3. 전체 회복 준비
```

#### MP 흡수
```
1. 마법 사용 자제
2. 물리 공격 위주
3. 라이프 스트림 폭풍 환경 대기
```

### 고급 전략

#### 턴 순서 조작
```
민첩 스탯 배분:
- 힐러 민첩 15+ → 턴 1순위
- 딜러 민첩 12+ → 턴 2-3순위
→ 힐러가 먼저 회복 → 딜러 안전하게 공격
```

#### 연계 공격
```
같은 적 2회 연속 공격: +20% 데미지
같은 적 3회 연속 공격: +50% 데미지

전략: 보스에 집중 공격 → 연계 보너스
```

#### DMW 활용
```
세피로스: 전체 적 공격 (보스 + 포자)
앤질: 리미트 브레이크 재사용 (2회 발동)
잭스: 근력 +1 버프
라켈: 전체 회복 (위기 탈출)
```

---

## FAQ

### Q1. 보스 스킬은 회피/방어로 막을 수 있나요?
**A**: 대부분의 보스 스킬은 **회피/방어 무시**입니다. 단, `damage_all` 등 일부 스킬은 설정에 따라 방어 가능합니다.

### Q2. 보스 스킬을 발동 전에 막을 수 있나요?
**A**: 아니요. HP 임계값에 도달하면 **자동 발동**됩니다. 단, HP를 임계값 아래로 빠르게 깎으면 다음 스킬 발동 전에 처치 가능합니다.

### Q3. 공생 보스는 어떻게 공략하나요?
**A**: 두 가지 전략이 있습니다:
1. **포자 우선 제거** → 모체 회복 차단 → 모체 처치
2. **모체 직접 공격** → 모체 처치 → 포자 자동 사멸

### Q4. 죽음의 선고(tokura)는 해제할 수 있나요?
**A**: 네. **회복 마법** 또는 **라켈 DMW**로 상태이상을 해제할 수 있습니다. 3턴 안에 해제하지 않으면 즉사합니다.

### Q5. 보스를 여러 명이 동시에 싸우면 어떻게 되나요?
**A**: 플레이어 수에 따라 보스 HP/공격력이 자동으로 증가합니다:
- 2인: 기본값
- 3인: HP 1.3배, ATK 1.1배
- 4인: HP 1.6배, ATK 1.2배

### Q6. 보스 스킬 `used_flag`는 언제 리셋되나요?
**A**: **전투 종료 시** 모든 보스의 `used_flag`가 0으로 리셋됩니다 (`BattleManager.handle_battle_end()` → `reset_boss_skills()`).

### Q7. 난이도를 중간에 바꿀 수 있나요?
**A**: 아니요. 난이도는 **전투 시작 시에만** 설정 가능하며, 전투 중에는 변경할 수 없습니다.

### Q8. 보스가 스킬을 사용하지 않아요.
**A**: 다음을 확인하세요:
1. `Monsters` 시트에서 `type = "boss"` 확인
2. `Boss_Skills` 시트에 해당 보스의 스킬 등록 확인
3. `trigger_hp_percent` 임계값에 도달했는지 확인
4. `used_flag = 0`인지 확인 (1이면 이미 사용됨)

### Q9. 보스 스킬을 직접 추가하려면?
**A**: Google Sheets의 `Boss_Skills` 시트에 다음 형식으로 추가:
```
boss_id: boss_dragon
skill_id: skill_1
trigger_hp_percent: 50
effect_type: damage_all
effect_value: 60
target: ALL_PLAYERS
description: 전체 공격 스킬
embed_message: 드래곤이 불을 뿜습니다!
used_flag: 0
```

### Q10. 보스 HP가 0 이하가 되어도 죽지 않아요.
**A**: `is_dead = 3`이 되어야 완전히 사망합니다. `CombatUtils.check_battle_end()`가 호출되는지 확인하세요. 공생 보스의 경우 모체가 죽으면 포자도 자동 사멸되어야 합니다.

---

## 📚 관련 문서

- **CLAUDE.md**: 전체 프로젝트 구조 및 개발 가이드
- **refactory.md**: 리팩토링 상태 및 모듈 설명
- **rule.md**: 전투 규칙 및 계산식
- **combat/boss_skill_system.py**: 보스 스킬 시스템 구현 코드

---

## 📝 버전 정보

- **작성일**: 2025-10-06
- **봇 버전**: Phase 1-4 리팩토링 완료
- **보스 스킬 시스템**: `combat/boss_skill_system.py`
- **상태 효과 시스템**: `combat/status_effect_manager.py`
