# 카드 전투 미니게임 설명서

디스코드 봇의 `/카드` 명령으로 진행하는 카드 드로우 전투입니다. 참가자 전원이 한 편(아군)이 되어 봇이 조종하는 적과 싸웁니다. 이 문서는 네 부분으로 나뉩니다.

1. [플레이어 안내](#1-플레이어-안내) — 참가해서 카드를 내는 방법
2. [주최자 안내](#2-주최자-안내) — 세션을 열고, 시작하고, 끝내는 방법
3. [데이터 편집 안내](#3-데이터-편집-안내) — 카드·적·전투를 CSV(또는 시트)로 고치는 방법
4. [개발자 안내](#4-개발자-안내) — 코드 구조, 규칙 엔진, 테스트, 시뮬레이션

---

## 1. 플레이어 안내

### 1.1 한 판의 흐름

```
주최자: /카드 옵션:주최            ← 세션 열림 (한 번에 하나)
모두  : /카드 옵션:참가            ← 로비 메시지에 이름이 추가됨
주최자: /카드 옵션:시작 전투id:b01  ← 전투 소개 + 1라운드 턴 메시지
─── 아군 턴 ───────────────────────────────
모두  : [카드 뽑기] 버튼 → 카드 고르기 → (필요하면) 대상 고르기
       턴 메시지의 "선택 현황: 2/4 완료"가 올라감
       전원 선택 → 자동 정산 (또는 주최자의 /카드 옵션:턴종료)
봇   : 아군 행동 로그 게시
─── 적 턴 ─────────────────────────────────
봇   : 적 행동 로그 게시 → 다음 라운드 턴 메시지
… 한쪽 HP가 모두 0이 될 때까지 반복 …
```

### 1.2 참가하기

- 모집 중(로비)일 때만 `/카드 옵션:참가`가 됩니다. 주최자도 참가할 수 있습니다.
- HP와 능력치(physics·magic·agility·charm)는 **캐릭터 시트**에서 가져옵니다. 시트에 없으면 참가가 거절됩니다(`/등록` 후 다시, 시트를 방금 고쳤다면 `/시트갱신`).
- 로그와 로비에 표시되는 이름은 **디스코드 서버 닉네임**입니다.

### 1.3 카드 내기

1. 턴 메시지의 **🃏 카드 뽑기** 버튼을 누릅니다. 본인에게만 보이는(ephemeral) 메시지로 이번 턴의 패 3장이 드롭다운으로 옵니다.
2. 카드를 고릅니다. 대상이 둘 이상이면(예: 적이 여럿) 대상 드롭다운이 이어서 뜹니다. 대상이 하나뿐이면 자동으로 정해집니다.
3. "선택 완료" 메시지가 뜨면 끝. 턴이 정산되기 전까지는 **다시 고르기 / 선택 취소** 버튼으로 바꿀 수 있습니다.

알아둘 점
- 패는 턴마다 **한 번만** 뽑힙니다. 버튼을 다시 눌러도 같은 3장이 보입니다.
- 지난 턴 메시지의 버튼은 "지난 턴의 메시지입니다"로 거절됩니다. 항상 가장 아래 턴 메시지를 쓰세요.
- 쓰러졌거나(HP 0) 기절(💫) 상태면 이번 턴에는 행동할 수 없고, 선택 현황 인원에서도 빠집니다.
- 선택을 끝내지 않은 채 주최자가 턴을 강제 종료하면 "대기"로 처리됩니다.

### 1.4 카드 종류

| 종류 | 하는 일 | 기본 카드 |
|---|---|---|
| 공격 | 적에게 피해. 피해 = 카드 값 + 능력치 보정 + 0~4 난수 | 베기(물리 8), 볼트(마법 10), 휩쓸기(적 전체 5) |
| 방어 | **다음 적 턴 동안** 받는 피해 감소. 그 턴에 공격받지 않으면 낭비 | 가드(자신 50%), 방벽(아군 한 명 70%) |
| 해킹 | 적에게 디버프 | 재밍(공격력 30%↓, 2회), 오작동(다음 공격 50% 불발), 표적 교란(다음 공격이 무작위 대상에게), 쇼크(다음 행동 불가) |
| 특수 | 아군만 뽑는 강한 카드와 회복 | 오버드라이브(물리 14 + 반동 3), 버스트 샷(마법 12), 응급 키트(아군 HP 8 회복) |

능력치 보정: 물리 카드는 `physics ÷ 4`, 마법 카드는 `magic ÷ 4`(소수점 버림)를 더합니다. 민첩(agility)은 같은 단계 안에서 누가 먼저 행동하는지만 정합니다.

### 1.5 결과 로그 읽기

모든 공개 메시지는 임베드(색 띠가 있는 카드)로 옵니다. 초록 띠 = 아군 행동, 빨강 띠 = 적 행동, 파랑 띠 = 턴 메시지, 금색 = 승리.

```
🟢 1라운드 — 아군 행동
▸ **영희**님이 **가드**를 사용했습니다. HP 30/30 (다음 상대 턴 피해 50% 감소)
▸ **철수**님이 **베기**를 사용했습니다. 신라 보병 A HP 16/30 (-14)
┌ 🟢 아군 ──────────┐ ┌ 🔴 적 ────────────┐
│ 철수 🛡            │ │ 신라 보병 A        │
│ ██████████ 39/39  │ │ █████░░░░░ 16/30  │
```

- 줄 끝의 HP는 **대상**의 HP입니다. 대상이 자기 자신이면(가드 등) 이름을 생략합니다.
- `(표적 교란!)`이 앞에 붙으면 공격이 원래 대상이 아닌 곳으로 갔다는 뜻입니다.
- 💀는 그 공격으로 쓰러졌다는 표시입니다. HP 표에서는 이름에 취소선이 그어집니다.
- 임베드 아래 HP 표는 10칸 막대(`█` 남은 HP, `░` 잃은 HP)와 숫자를 함께 보여주고, 이름 옆에 상태 아이콘이 붙습니다: 🛡 방어 중, ⬇ 공격력 감소, ❓ 불발 위험, 🔀 표적 교란, 💫 기절.
- 턴 메시지는 본문 없이 임베드만 고치므로 "(수정됨)" 표시가 붙지 않습니다.

---

## 2. 주최자 안내

| 명령 | 조건 | 하는 일 |
|---|---|---|
| `/카드 옵션:주최` | 진행 중인 세션이 없을 때 | 로비 메시지를 올리고 세션을 엽니다. 응답은 본인에게만 보입니다. **실행한 사람이 그 세션의 주최자** |
| `/카드 옵션:시작 전투id:<id>` | 주최자, 로비, 참가자 1명 이상 | 전투 데이터를 읽고 1라운드를 엽니다. `전투id`는 자동완성됩니다 |
| `/카드 옵션:턴종료` | 주최자, 아군 턴 | 아직 고르지 않은 사람을 "대기"로 두고 바로 정산합니다 |
| `/카드 옵션:현황` | 누구나 | 상태·라운드·선택 현황·HP 표를 본인에게만 보여줍니다 |
| `/카드 옵션:종료` | 주최자 | 세션을 지웁니다. 전투가 끝난 뒤에도 새 세션을 열려면 먼저 종료하거나, 끝난 세션 위에 바로 `주최`해도 됩니다 |
| `/카드 도움말:True` | 누구나 | 명령 사용법 |

주최자의 특권
- 주최자가 참가하면 **카드 뽑기** 버튼이 패 3장 대신 **전체 카드 목록**을 보여줍니다(관리자 모드). 24장이 넘으면 드롭다운 마지막의 "다음 ▶" 항목으로 넘깁니다. 이 목록은 저장되지 않으므로 턴마다 같습니다.
- 환경변수 `CARDBATTLE_ADMIN_IDS`(없으면 `FUGITIVE_ADMIN_IDS`)에 적힌 사람은 주최자가 아니어도 `턴종료`·`종료`를 쓸 수 있습니다.

제한
- 세션은 봇 전체에서 **하나**입니다. 다른 채널·서버에서 진행 중이면 `주최`가 거절됩니다.
- 턴 제한 시간은 없습니다. 잠수하는 사람이 있으면 `턴종료`로 넘기세요.
- 봇이 재시작되어도 진행 중인 전투는 복원되고, 기존 턴 메시지의 버튼이 그대로 동작합니다.

---

## 3. 데이터 편집 안내

전투 데이터는 리포지토리의 `data/cardbattle/` 아래 CSV 세 개입니다. UTF-8(BOM 허용), 쉼표 구분. 첫 줄은 열 이름입니다. 수정은 **다음 `/카드 옵션:시작`부터** 반영되며, 진행 중인 전투는 시작 시점의 카드를 그대로 씁니다.

### 3.1 Cards.csv — 카드

```
card_id,name,kind,target,stat,pool,weight,effects,desc
atk_slash,베기,attack,enemy,physics,both,10,damage:8,기본 물리 공격
def_guard,가드,defend,self,none,both,8,shield:0.5:1,다음 상대 턴 동안 받는 피해 절반
hack_jam,재밍,hack,enemy,magic,both,5,atk_down:0.3:2,대상 공격력 30% 감소 (2턴)
sp_overdrive,오버드라이브,special,enemy,physics,ally,2,damage:14;self_damage:3,강타 + 반동 피해 3
```

| 열 | 값 | 뜻 |
|---|---|---|
| `card_id` | 영문 식별자, 중복 불가 | 내부 이름 |
| `name` | 아무 문자열 | 표시 이름 |
| `kind` | `attack` `defend` `hack` `special` | 정산 단계와 AI 성향 가중치를 결정 |
| `target` | `enemy` `ally` `self` `all_enemy` `all_ally` | `enemy`/`ally`는 대상 선택이 뜸, 나머지는 자동 |
| `stat` | `physics` `magic` `none` | 피해·회복에 더할 능력치(÷4) |
| `pool` | `both` `ally` `enemy` | 누가 뽑을 수 있는지. `ally`는 아군 전용 카드 |
| `weight` | 숫자 | 드로우 가중치. 클수록 자주 나옴 |
| `effects` | 아래 문법 | 효과 목록 |
| `desc` | 문자열 | 드롭다운과 패 설명에 표시 (100자 이내 권장) |

`effects` 문법: `종류[:값[:지속[:확률]]]`을 `;`로 이어 씁니다.

| 종류 | 값 | 지속 | 설명 |
|---|---|---|---|
| `damage` | 기본 피해 | - | 대상에게 피해. 능력치 보정과 0~`damage_variance` 난수가 더해짐 |
| `heal` | 회복량 | - | 대상 HP 회복(최대치까지) |
| `self_damage` | 피해 | - | 사용자 본인에게 고정 피해(반동) |
| `shield` | 감소 비율 0~1 | 상대 턴 수 | 받는 피해 감소. 사용한 반턴에는 안 깎이고 **상대의 반턴**이 끝날 때 1 감소 |
| `atk_down` | 감소 비율 | 행동 횟수 | 주는 피해 감소. 보유자의 반턴이 끝날 때 1 감소 |
| `misfire` | 불발 확률 | 유효 턴 | 다음 공격 시 확률 판정 후 **소모** |
| `redirect_random` | (무시) | 유효 턴 | 다음 단일 대상 공격이 자신을 뺀 양편 생존자 중 무작위 대상에게. 사용 시 소모 |
| `stun` | (무시) | - | 다음 행동 불가. 행동 차례에 소모 |

확률(4번째 값)은 그 효과가 발동할 확률입니다(기본 1 = 항상). 같은 종류의 수정자가 이미 있으면 값과 지속 중 큰 쪽으로 합쳐집니다.

### 3.2 Enemies.csv — 적

```
enemy_id,name,max_hp,physics,magic,agility,charm,personality,pool
grunt,신라 보병,30,12,8,10,8,aggressive,enemy
guard,경비 로봇,45,10,8,6,6,defensive,enemy
netrunner,해커,25,8,14,12,10,hacker,enemy
```

`personality`는 AI 성향입니다.

| 성향 | 공격 | 방어 | 해킹 | 특수 | 성격 |
|---|---|---|---|---|---|
| `aggressive` | 1.4 | 0.6 | 0.8 | 1.3 | 가장 약한 적부터 때림, 처치 가능하면 우선 |
| `defensive` | 0.8 | 1.5 | 1.0 | 0.7 | HP가 낮거나 직전 턴에 공격이 많았으면 방어 |
| `hacker` | 0.8 | 0.8 | 1.6 | 0.9 | 디버프 없는 상대에게 해킹 |

`pool`은 보통 `enemy`입니다. 적 전용 카드를 만들려면 `Cards.csv`에서 `pool=enemy`로 두면 됩니다.

### 3.3 Battles.csv — 전투

```
battle_id,name,enemies,desc
b01,정찰대,grunt;grunt,신라 보병 2명
b03,대부대,grunt*3;guard;netrunner,보병 3 + 로봇 + 해커
```

`enemies`는 `enemy_id` 또는 `enemy_id*개수`를 `;`로 잇습니다. 같은 적이 여럿이면 이름 뒤에 A, B, C…가 붙습니다. `desc`는 전투 시작 소개문에 들어갑니다.

### 3.4 검증과 흔한 오류

시작 시 데이터 전체를 검사하고, 문제가 있으면 주최자에게만 오류를 보여주고 시작하지 않습니다.

- 알 수 없는 `kind` / `target` / `stat` / `pool` / 효과 이름
- `effects`가 비어 있음, 숫자 자리에 문자
- `Battles.csv`의 `enemies`에 없는 `enemy_id`, 적 0명(`g*0`)
- `card_id` / `enemy_id` / `battle_id` 중복
- 한쪽(`ally` 또는 `enemy`)이 뽑을 카드가 하나도 없음

### 3.5 구글 시트로 옮기기

`.env`에 `CARDBATTLE_SOURCE=sheet`를 쓰면 봇 스프레드시트의 탭 `Card_List`, `Card_Enemies`, `Card_Battles`를 읽습니다. 열 이름은 CSV와 **완전히 같게** 두세요. 탭이 없거나 읽기에 실패한 표는 경고 로그를 남기고 그 표만 CSV로 되돌아갑니다. `CARDBATTLE_DATA_DIR`로 CSV 폴더 위치를 바꿀 수 있습니다. 봇은 이 탭에 절대 쓰지 않습니다.

### 3.6 밸런스 숫자

카드·적 숫자 외의 상수는 `cardbattle/config.py`의 `DEFAULTS`에 있습니다.

| 키 | 기본값 | 뜻 |
|---|---|---|
| `hand_size` | 3 | 턴마다 뽑는 장 수 |
| `damage_variance` | 4 | 피해에 더해지는 난수 상한 |
| `stat_divisor` | 4 | 능력치 보정 = 능력치 ÷ 이 값 |
| `ai_noise` | 0.15 | 적 AI 점수에 섞는 난수 폭(0이면 항상 같은 선택) |
| `max_players` | 8 | 참가 정원 |
| `enemy_turn_delay_sec` | 1.0 | 아군 로그 게시 후 적 턴까지 대기 |
| `use_current_hp` | False | True면 시트의 `current_hp`로 시작 |
| `allow_unregistered` | False | True면 시트에 없는 사람도 기본 스탯으로 참가 |
| `admin_sees_all` | True | 주최자 전체 카드 선택 |
| `default_hp` / `default_stat` | 30 / 10 | 시트에 값이 없을 때 |
| `redirect_includes_self` | False | 표적 교란이 자기 자신도 맞출 수 있는지 |

바꾸기 전에 시뮬레이션으로 확인하세요(4.5절).

---

## 4. 개발자 안내

### 4.1 파일 구조

```
cardbattle/
  engine.py   순수 규칙 엔진 (Discord 모름). 데이터클래스, 드로우, 제출, 정산, 승패, 직렬화
  data.py     CSV/시트 로더 + 검증 (DataSource 프로토콜, CsvSource, SheetSource, FallbackSource)
  ai.py       적 AI 점수 함수와 성향 표
  config.py   DEFAULTS, coerce, build_config
  render.py   상태 → 문장 (로그 줄, 조사, 진행률; Discord 모름 → 테스트가 쉬움)
  embeds.py   render 의 문장을 discord.Embed 로 배치 (색, 필드, HP 바). 공개 메시지는 전부 여기서
  strings.py  유저 노출 문자열 전부
  store.py    data/cardbattle_state.json 원자적 저장/복원
  views.py    TurnView(영구 버튼), HandView, TargetView, ChosenView
  cog.py      CardBattleCog: /카드 명령, 상태기계, 락, 메시지 편집, 재시작 복원
  sim.py      몬테카를로 시뮬레이션
data/cardbattle/*.csv          전투 데이터 (git 추적)
data/cardbattle_state.json     진행 중 세션 (git 무시)
tests/test_cardbattle_{engine,ai,cog}.py
```

`local_server/`는 손으로 복사하는 미러입니다. 패키지와 CSV를 고치면 거기에도 복사하세요.

### 4.2 상태와 턴 번호

`BattleState.status`: `LOBBY → ALLY_TURN → RESOLVING → ENEMY_TURN → ALLY_TURN … → ENDED`.
`turn_no`는 **반턴** 카운터입니다. 홀수 = 아군, 짝수 = 적. 표시용 라운드는 `(turn_no + 1) // 2`.

저장되는 상태는 보통 `LOBBY`, `ALLY_TURN`, `ENDED`입니다. `RESOLVING`/`ENEMY_TURN` 상태로 저장된 채 죽었다면 `cog_load → _resume()`이 게시 못 한 보고(`pending_reports`)를 올리고, 적 턴을 이어 돌리고, 턴 메시지를 다시 엽니다. 정산을 시작하면 `turn_message_id`를 0으로 두므로 복원 시 "턴 메시지를 새로 열어야 하는지"를 그 값으로 판단합니다.

### 4.3 정산 규칙 (engine._resolve)

1. 그 편의 생존자 중 기절자는 `stunned` 줄을 남기고 기절을 소모합니다. 선택이 없는 사람은 맨 뒤에 `wait`.
2. 나머지를 **단계 → 민첩 내림차순 → 난수** 순으로 정렬합니다. 단계: `hack`(0) → `defend`(1) → `attack`/`special`(2).
3. 카드마다 `_apply_card`:
   - 대상 확정. 고른 대상이 죽었으면 생존자 중 무작위로 바꾸고 `result="redirect"`.
   - `damage` 효과가 있으면 사용자의 `misfire`를 소모하고 판정(실패 시 `misfire`, 아무 효과 없음), `redirect_random`을 소모하고 단일 대상이면 양편 생존자 중 무작위로 교체.
   - 효과를 순서대로 `HANDLERS[kind]`에 넘김. `damage`는 `값 + 능력치 보정 + randint(0, variance)` 에 사용자의 `atk_down`, 대상의 `shield` 비율을 곱하고 내림.
4. `tick_modifiers`: `created_turn != turn_no`인 수정자만 깎습니다. `shield`는 보유자와 **다른** 편의 반턴이 끝날 때, 나머지(`atk_down` `misfire` `redirect_random` `stun`)는 **같은** 편의 반턴이 끝날 때. 이 규칙 덕에 아군 턴의 가드가 정확히 다음 적 턴만 막습니다.
5. `winner()`: 적 전멸을 먼저 확인하므로 동시 전멸은 아군 승리.

보고(`TurnReport`)는 `{"turn_no","round_no","side","lines","hp","winner"}`이며 `lines[*]`는 `actor_id/actor_name/side/card_id/card_name/kind/result/targets`입니다. `result`는 `hit | heal | shield | hack | redirect | misfire | no_target | wait | stunned`. 렌더는 이 dict만 보고 문장을 만듭니다.

### 4.4 Discord 층에서 지킬 것

- 상태 변경은 전부 `self.lock` 안에서, 끝나면 `_save()`. Discord 전송은 락 안에서 해도 됩니다(fugitive와 같은 방식).
- `TurnView`는 `timeout=None` + 고정 `custom_id="cardbattle:draw"`. 상태를 갖지 않으므로 `cog_load`의 `bot.add_view(TurnView())` 한 번으로 모든 턴 메시지 버튼이 복원됩니다. 대신 `handle_draw`가 `interaction.message.id == st.turn_message_id`를 검사해 옛 메시지를 거절합니다.
- ephemeral 패 메시지는 **그 메시지의 컴포넌트 상호작용**에서만 `response.edit_message`로 고칠 수 있습니다. 버튼 클릭(공개 메시지)에서는 반드시 `response.send_message(ephemeral=True)`를 쓰세요. 공개 턴 메시지를 갱신할 때는 `msg.edit(content=...)`만 호출해야 버튼이 남고, 버튼을 떼려면 `view=None`을 명시합니다.
- Select는 옵션 25개·라벨 100자 제한. 주최자 전체 카드는 24개 + "다음 ▶" 옵션으로 페이징하고, 페이지 번호는 View 인스턴스에만 둡니다.
- `/카드`는 `tests/test_help_option.py` 규칙을 따릅니다: 클래스 속성의 단일 `app_commands.Command`, 필수 옵션 없음(`help_option.require`), 마지막 옵션 `도움말`. 하위 명령 그룹으로 바꾸면 테스트가 실패합니다.
- 캐릭터 시트 열 매핑은 `engine.player_combatant` 한 곳뿐입니다. 시트 구조가 바뀌면 거기만 고치세요.

### 4.5 테스트와 시뮬레이션

```
PYTHONIOENCODING=utf-8 py -m pytest tests -q -k cardbattle
PYTHONIOENCODING=utf-8 py -m cardbattle.sim --all --games 200
PYTHONIOENCODING=utf-8 py -m cardbattle.sim --battle b02 --players 3 --games 500 --seed 1
PYTHONIOENCODING=utf-8 py -m cardbattle.sim --battle b03 --set damage_variance=2 --policy random
```

- 엔진 테스트는 시드 고정 `random.Random`과 `damage_variance=0`, `stat_divisor=1000`으로 숫자를 결정적으로 만듭니다(`flat_cfg()`, `mk_cards()`, `always(card_id)` 도우미).
- cog 테스트는 `FakeChannel`/`FakeMessage`/`FakeInteraction`으로 명령과 버튼 흐름 전체(주최 → 참가 → 시작 → 뽑기 → 선택 → 정산 → 다음 턴, 강제 종료, 재시작 복원, 종료)를 돌립니다.
- sim의 플레이어 봇은 `greedy`(HP 35% 미만이면 방어, 강한 적에게 해킹, 아니면 가장 약한 적 공격)와 `random`이 있습니다. 사람은 봇보다 잘 하므로 결과는 하한선입니다. 출력은 승률, 라운드 p25/p50/p75, 판당 아군 사망, 많이 쓴 카드입니다.
- 엔진의 모든 함수는 `rng` 인자를 받습니다. 기본값 `DEFAULT_RNG`는 `combat.random_utils`를 지연 import 하는 어댑터입니다(`random_utils.get_random()`은 내부 버그가 있어 `uniform`을 씁니다).

### 4.6 확장 포인트

| 하고 싶은 것 | 손댈 곳 |
|---|---|
| 새 카드 | `Cards.csv` 한 줄 (코드 수정 없음) |
| 새 효과 종류 | `engine.EFFECT_KINDS`에 추가, `HANDLERS`에 핸들러, 지속형이면 `MODIFIER_KINDS`/`OWN_TURN_MODS`, 로그 문구는 `strings.DETAIL`/`TAG` |
| 새 AI 성향 | `ai.PERSONALITIES`에 가중치 한 줄 |
| 무기별 특수 카드 | 시트에 무기 열이 생기면 `engine.player_combatant`에서 읽어 `Combatant`에 싣고 `pool_for`에서 조건 추가 |
| 턴 제한 시간 | fugitive의 `_arm_timer`/`_cancel_timer` 패턴을 `cog.py`에 옮기고 만료 시 `_resolve_turn(force=True)` |
| 보스 임계값 스킬 | 적 HP 비율이 임계값을 넘을 때 `run_enemy_turn`의 손패에 특정 카드를 강제로 넣기 |
