# 카드 전투 미니게임 설명서

디스코드 봇의 `/카드` 명령으로 진행하는 카드 드로우 전투입니다. 참가자 전원이 한 편(아군)이 되어 봇이 조종하는 적과 싸웁니다. 사이버펑크 2077 TRPG 세션용으로 만들어져 있어 화면 표시는 그 세계관의 이름(RAM, 엣지러너, 넷러너, 보느와의 방화벽 …)을 쓰지만, 캐릭터 시트의 열 이름은 그대로입니다.

1. [플레이어 안내](#1-플레이어-안내) — 참가해서 카드를 내는 방법, 라운드 규칙, 기믹
2. [주최자 안내](#2-주최자-안내) — 세션을 열고, 시작하고, 끝내는 방법
3. [데이터 편집 안내](#3-데이터-편집-안내) — 카드·적·전투를 CSV(또는 시트)로 고치는 방법
4. [개발자 안내](#4-개발자-안내) — 코드 구조, 규칙 엔진, 테스트, 시뮬레이션

---

## 1. 플레이어 안내

### 1.1 한 판의 흐름

```
주최자: /카드 옵션:주최                 ← 세션 열림 (한 번에 하나)
모두  : /카드 옵션:참가                 ← 로비 메시지에 이름이 추가됨
주최자: /카드 옵션:시작 전투id:scav4     ← 전투 소개(규칙 요약) + 1라운드 메시지 (현황판 그림 첨부)
─── 라운드 ─────────────────────────────────────────────
봇   : 적의 **행동 종류**를 먼저 공개 (공격 준비 / 해킹 준비 / 방어 준비 …, 대상은 비공개)
모두  : [카드 뽑기] → 카드 고르기 → (필요하면) 대상 고르기
       [방화벽] → 팀 게이지로 지원 사용 (라운드에 한 번, 누구나)
       전원 선택 → 자동 정산 (또는 주최자의 /카드 옵션:턴종료)
봇   : 라운드 정산 로그 (🟢 아군 / 🔴 적 줄이 섞여서 실제 순서대로) → 다음 라운드 메시지
… 한쪽이 모두 쓰러지거나 기믹 조건이 끝날 때까지 반복 …
```

### 1.2 참가하기

- 모집 중(로비)일 때만 `/카드 옵션:참가`가 됩니다. 주최자도 참가할 수 있습니다.
- HP와 능력치는 **캐릭터 시트**에서 가져옵니다. 시트에 없으면 참가가 거절됩니다(`/등록` 후 다시, 시트를 방금 고쳤다면 `/시트갱신`).
- 로그와 로비에 표시되는 이름은 **디스코드 서버 닉네임**입니다. 직업은 시트의 `job` 열을 바꿔 보여줍니다: 스트라이커 → **엣지러너**, 마테리아 위버 → **넷러너**, 솔져 → 솔져. 시트의 `color` 열(`#RRGGBB`)이 있으면 현황판 그림의 패널 색으로 씁니다.

### 1.3 카드 내기

1. 라운드 메시지의 **🃏 카드 뽑기** 버튼을 누릅니다. 본인에게만 보이는 메시지로 이번 라운드의 패 3장이 옵니다. 카드마다 **예상 피해 범위**("약 16~20")와 **▶ 콤보** 조건이 적혀 있습니다.
2. 카드를 고릅니다. 대상이 둘 이상이면 대상 드롭다운이 이어서 뜹니다. 대상이 하나뿐이면 자동으로 정해집니다.
3. "선택 완료"가 뜨면 끝. 정산 전까지는 **다시 고르기 / 선택 취소**로 바꿀 수 있습니다.

알아둘 점
- 패는 라운드마다 **한 번만** 뽑힙니다. 버튼을 다시 눌러도 같은 3장입니다.
- 지난 라운드 메시지의 버튼은 거절됩니다. 항상 가장 아래 메시지를 쓰세요.
- 쓰러졌거나(HP 0) 기절(💫) 상태면 행동할 수 없고 선택 현황 인원에서도 빠집니다. 붙잡힌(🪢) 상태면 **떨쳐내기**만 낼 수 있습니다.
- 선택을 끝내지 않은 채 주최자가 강제 정산하면 "대기"로 처리됩니다.

### 1.4 라운드 정산 규칙

- 양편이 **같은 라운드에 함께** 정산됩니다. 순서는 **해킹 → 방어 → 공격/특수**, 같은 단계 안에서는 **민첩이 높은 쪽이 먼저**입니다.
- 그래서 빠른 넷러너의 **시스템 리셋**(기절)은 느린 적의 공격을 **같은 라운드에** 막고, 느린 적보다 먼저 공격하면 적이 때리기 전에 쓰러뜨릴 수 있습니다. 반대로 적 해커가 더 빠르면 내 공격이 먼저 끊깁니다.
- **방어 카드는 이번 라운드의 공격 단계에만** 유효합니다 (적의 "공격 준비"를 보고 쓰는 카드입니다). **무기 글리치**(공격력 감소)는 이번·다음 라운드 2라운드 동안 유효하고, **사이버웨어 고장**(불발)·**적대 인식**(표적 무작위)·**시스템 리셋**(기절)은 다음 번 그 행동에서 소모됩니다.
- 피해 = 카드 값 + 능력치 보정 + 0~4 난수. 보정은 물리 카드 `근력 ÷ 2`, RAM 카드 `RAM ÷ 2`. 엣지러너(근력 16)의 근접 타격은 16~20, 넷러너(RAM 16)의 쇼트 서킷은 16~20, 서로 바꿔 들면 12~16입니다.

### 1.5 카드 종류

| 종류 | 하는 일 | 카드 |
|---|---|---|
| 공격 | 적에게 피해 | 근접 타격(물리 8, 기절한 적에게 ×1.5), 쇼트 서킷(RAM 8, 글리치 적에게 +5), 관절 치기(물리 5, **비살상**), 제압 사격(적 전체 4, **소음**) |
| 방어 | 이번 라운드 받는 피해 감소 | 엄폐(자신 50%), 보느와의 ICE(아군 한 명 70%) |
| 해킹 | 적에게 디버프 (돌에게는 칩 피해) | 무기 글리치(공격력 30%↓ 2라운드), 사이버웨어 고장(다음 공격 50% 불발), 적대 인식(다음 공격 무작위 대상), 시스템 리셋(다음 행동 불가) |
| 특수 | 아군만 뽑는 강한 카드와 회복 | 샌디스탄 돌격(물리 14 + 반동 3, **살상**, 붙잡고 있는 적에게 ×1.5), 과부하 핵(RAM 12, 고장 상태 적에게 ×1.5), 맥스닥 흡입기(아군 HP 6 + RAM 보정, 쓰러진 아군도 일으킴) |

**콤보**: 카드 설명의 "▶ 콤보: … 적에게 피해 ×1.5" 조건이 맞으면 보너스가 붙고 로그에 ⚡콤보! 가 찍힙니다. 해킹이 공격보다 먼저 정산되므로, 같은 라운드에 한 사람이 걸고 다른 사람이 때리면 바로 터집니다.

### 1.6 보느와의 방화벽 (팀 게이지)

아군이 피해를 **10 받을 때마다 +1**, **방어 카드를 낼 때마다 +1**, **아군이 쓰러지면 +2** 찹니다(최대 9). 인원이 적을수록 한 사람이 더 많이 맞으니 더 빨리 찹니다. 라운드 메시지의 **🧱 방화벽** 버튼으로 **라운드에 한 번**, 살아 있는 누구나 쓸 수 있습니다.

| 지원 | 비용 | 효과 |
|---|---|---|
| 팀 엄폐 | 3 | 아군 전원 이번 라운드 피해 50% 감소 |
| 긴급 재부팅 | 2 | 아군 전원의 기절·불발·적대 인식·글리치·붙잡힘 해제 (기절한 사람도 이 라운드에 행동 가능) |
| 스마트 탄 유도 | 2 | 이번 라운드 아군 공격의 난수 0, 회피 무시 (비살상 마무리 계산용) |
| 적 스캔 | 1 | 이번 라운드 적 의도의 **대상** 공개 |
| 맥스닥 | 4 | 가장 위험한 아군 HP 30% 회복 (쓰러진 아군도 일으킴) |

### 1.7 기믹 — 전투별로 켜지는 규칙

전투 시작 소개문의 "규칙" 칸에 그 전투에 켜진 것만 나옵니다.

**비살상 (nonlethal)** — 적을 쓰러뜨릴 때 남은 HP를 **10 이상 초과**해서 깎으면 **⚠과잉 제압**입니다. **살상** 태그 카드(샌디스탄 돌격)로 쓰러뜨리면 무조건 과잉 제압, **비살상** 태그 카드(관절 치기)는 절대 과잉 제압이 아닙니다. 예상 피해 범위를 보고 마무리 카드를 고르세요.

**NCPD 경계도 (ncpd)** — 과잉 제압 1회 +1, **소음** 태그 카드(제압 사격) 사용 1회 +0.5. 가득 차면(기본 3) 드론이 출동해 전투가 끊기고 **도주 단계**로 넘어갑니다. 적이 몇 명 남았든 상관없습니다.

**도주 단계** — 5턴 가위바위보. 매 턴 라운드 메시지의 버튼 셋 중 하나를 고릅니다.

| 내 행동 | 이기는 경찰 행동 | 지는 경찰 행동 |
|---|---|---|
| 숨기 | 서치라이트 | 열감지 스캔 |
| 이동하기 | 열감지 스캔 | 확성기 검문 |
| 죽은 척하기 | 확성기 검문 | 서치라이트 |

나머지 조합은 무승부. 경찰은 매 턴 셋 중 하나를 무작위로 고르고, 보느와가 **"이번엔 X는 안 쓸 거야"** 하고 하나를 미리 알려줍니다(그러면 절대 지지 않는 선택이 항상 하나 있습니다). **살아 있는 참가자의 과반수**가 이기면 그 턴을 이긴 것이고 경계도가 1 내려갑니다. 경계도가 0이 되면 탈출, 5턴 안에 못 내리면 잡힙니다(RP로 이어집니다). 쓰러진 사람은 선택하지 않고, 전원이 쓰러진 채 드론이 오면 바로 잡힙니다.

**끌고 가기 (drag)** — 아군이 쓰러지면 살아 있는 적 하나가 그 사람을 골목 안으로 끌고 가기 시작합니다. **2라운드 안에** 끌고 가는 적을 제압하거나 쓰러진 아군을 일으키지 못하면 그 아군은 **약탈당함**(💔)으로 기록되고 전투 뒤 서사에 반영됩니다.

**돌 (doll)** — HP와 별개로 **💾 칩 안정도**가 있습니다. 해킹 카드가 명중할 때마다 1 깎이고(라운드당 최대 2), 0이 되면 돌이 멈추는 **비살상 승리**입니다. 돌은 기절하지 않는 대신 시스템 리셋이 칩을 1 더 깎습니다. 물리 공격은 50% 회피하지만 해킹과 RAM 공격은 피하지 못하고, 라운드마다 의체가 8 자가 복구합니다. 라운드마다 **모드**가 공개됩니다: **접객 모드**(단말기 소지자에게 매달려 붙잡음 + 긁힘 3), **폭주 모드**(손톱으로 두 번), **리부트**(행동하지 않음, 이 라운드 해킹은 칩을 2배로 깎음). HP로 쓰러뜨려도 이기지만 보느와가 한마디 합니다.

**단말기 (terminal)** — 시작할 때 주최자(참가하지 않았으면 첫 참가자)가 보느와의 단말기를 들고 있고, 돌은 **소지자만** 노립니다. 소지자가 피해를 받을 때마다 **📱 액정 내구도**가 1 깎입니다(0이 되어도 패배는 아니고 서사 페널티). 소지자의 패에는 항상 **단말기 넘기기**가 있어 다른 아군에게 넘길 수 있고, 붙잡힌 사람은 **떨쳐내기**만 낼 수 있습니다(아군이 돌을 맞혀도 풀립니다).

### 1.8 결과 로그 읽기

- 라운드 정산 임베드(보라 띠)에 양편 행동이 실제 순서대로 섞여 나옵니다. 줄 앞의 🟢 = 아군, 🔴 = 적. 줄 끝의 HP는 **대상**의 HP, 대상이 자신이면 이름을 생략합니다.
- 접두어: `⚡콤보!` 콤보 발동, `(적대 인식!)` 원래 대상이 아닌 곳으로 감. 꼬리표: 💀 쓰러짐, ⚠과잉 제압, (싼 의체!) 해킹 강화, ✨일어남, 📱액정 n, 🪢… 풀려남, 💾칩 −n.
- 그 아래 이벤트 줄: 🧱 방화벽 사용, 🚨 경계도 변화, 🩸 끌고 가기, 💔 약탈, 🩹 자가 복구.
- 상태 아이콘: 🛡 방어 중, ⬇ 공격력 감소, ❓ 불발 위험, 🔀 적대 인식, 💫 기절, 🪢 붙잡힘.
- 라운드 메시지에는 **현황판 그림**이 붙습니다(왼쪽 아군 패널 = 각자의 색, 오른쪽 적 패널 = 붉은 계열, 패널 아래 줄 = 이번 라운드 행동 또는 다음 라운드 의도, 초록/빨강 선 = 공격 방향, 굵은 선 = 콤보). 서버에 Pillow 또는 한글 글꼴이 없으면 그림 없이 임베드만 옵니다.

---

## 2. 주최자 안내

| 명령 | 조건 | 하는 일 |
|---|---|---|
| `/카드 옵션:주최` | 진행 중인 세션이 없을 때 | 로비 메시지를 올리고 세션을 엽니다. **실행한 사람이 그 세션의 주최자** |
| `/카드 옵션:시작 전투id:<id>` | 주최자, 로비, 참가자 1명 이상 | 전투 데이터를 읽고 1라운드를 엽니다. `전투id`는 자동완성되며 `[n인]` 표시가 붙습니다. **참가 인원에 맞는 id**를 고르세요 (`scav3`~`scav7`, `doll3`~`doll7`, 연습용 `b01`) |
| `/카드 옵션:턴종료` | 주최자, 라운드 또는 도주 단계 | 아직 고르지 않은 사람을 "대기"(도주 단계에서는 "선택 없음")로 두고 바로 정산합니다 |
| `/카드 옵션:현황` | 누구나 | 상태·라운드·적 의도·게이지·HP 표를 본인에게만 보여줍니다 |
| `/카드 옵션:종료` | 주최자 | 세션을 지웁니다 |
| `/카드 도움말:True` | 누구나 | 명령 사용법 |

주최자의 특권
- 주최자가 참가하면 **카드 뽑기**가 패 3장 대신 **전체 카드 목록**을 보여줍니다(관리자 모드, 24장이 넘으면 "다음 ▶"으로 페이징). 단말기 전투에서는 주최자가 기본 소지자입니다.
- 환경변수 `CARDBATTLE_ADMIN_IDS`(없으면 `FUGITIVE_ADMIN_IDS`)에 적힌 사람은 주최자가 아니어도 `턴종료`·`종료`를 쓸 수 있습니다.

제한
- 세션은 봇 전체에서 **하나**입니다. 다른 채널·서버에서 진행 중이면 `주최`가 거절됩니다.
- 턴 제한 시간은 없습니다. 잠수하는 사람이 있으면 `턴종료`로 넘기세요.
- 봇이 재시작되어도 진행 중인 전투(도주 단계 포함)는 복원되고, 기존 메시지의 버튼이 그대로 동작합니다.

세션 운영 팁
- 스캐빈저 전투는 "비살상"이 핵심입니다. 플레이어가 예상 피해 범위를 읽고 관절 치기·스마트 탄 유도로 마무리하게 유도하세요. 경계도가 차서 도주 단계로 가는 것도 설계된 분기입니다.
- 돌 전투는 해킹이 해법입니다. 첫 라운드 모드 공개와 "칩 안정도" 게이지를 보고 플레이어가 스스로 알아차리게 두세요. 단말기 넘기기는 소지자가 두 번쯤 맞은 뒤에 쓰면 액정이 남습니다.

---

## 3. 데이터 편집 안내

전투 데이터는 리포지토리의 `data/cardbattle/` 아래 CSV 세 개입니다. UTF-8(BOM 허용), 쉼표 구분. 수정은 **다음 `/카드 옵션:시작`부터** 반영됩니다. `local_server/data/cardbattle/`에도 같은 파일을 복사하세요.

### 3.1 Cards.csv — 카드

```
card_id,name,kind,target,stat,pool,weight,effects,tags,combo,desc
atk_slash,근접 타격,attack,enemy,physics,both,10,damage:8,,target_has:stun:dmg_x:1.5,기본 물리 공격. 기절한 적에게 1.5배
atk_knee,관절 치기,attack,enemy,physics,ally,6,damage:5,nonlethal,,무릎·어깨만 노리는 비살상 타격
pass_terminal,단말기 넘기기,special,ally,none,fixed,0,pass_terminal:1,,,보느와의 단말기를 다른 아군에게 넘긴다
```

| 열 | 값 | 뜻 |
|---|---|---|
| `card_id` | 영문 식별자 | 내부 이름. `pass_terminal` / `break_free` 는 기믹이 찾는 고정 id |
| `name` | 문자열 | 표시 이름 |
| `kind` | `attack` `defend` `hack` `special` | 정산 단계(해킹 0 → 방어 1 → 공격·특수 2)와 AI 성향 가중치 |
| `target` | `enemy` `ally` `self` `all_enemy` `all_ally` | `enemy`/`ally`는 대상 선택이 뜸 |
| `stat` | `physics` `magic` `none` | 피해·회복에 더할 능력치(÷`stat_divisor`). 화면에는 근력 / RAM |
| `pool` | `both` `ally` `enemy` `fixed` | 누가 뽑는지. `fixed` 는 드로우되지 않고 규칙이 손에 쥐어줌 (단말기 넘기기, 떨쳐내기, 돌의 모드 카드) |
| `weight` | 숫자 | 드로우 가중치 |
| `effects` | 아래 문법 | 효과 목록 |
| `tags` | `loud` `lethal` `nonlethal` 을 `;`로 | 소음(경계도 +0.5) / 살상(제압 시 무조건 과잉) / 비살상(과잉 면제) |
| `combo` | `조건:수정자:보너스:값` | 비우면 없음. 설명과 로그에 자동 표기 |
| `desc` | 문자열 | 드롭다운·패 설명 (100자 이내 권장) |

`effects` 문법: `종류[:값[:지속[:확률]]]`을 `;`로 이어 씁니다.

| 종류 | 값 | 지속 | 설명 |
|---|---|---|---|
| `damage` | 기본 피해 | - | 대상에게 피해 (보정 + 난수, 콤보·공격력 감소·방어·회피 적용). 한 카드에 두 번 쓰면 두 번 때림 |
| `heal` | 회복량 | - | 대상 HP 회복. 쓰러진 아군도 대상 가능(일으킴) |
| `self_damage` | 피해 | - | 사용자 본인에게 고정 피해 |
| `shield` | 감소 비율 0~1 | 라운드 수 | 받는 피해 감소. 만든 라운드 포함해 라운드 끝에 1 감소 (`:1` = 이번 라운드만) |
| `atk_down` | 감소 비율 | 라운드 수 | 주는 피해 감소. 같은 규칙 (`:2` = 이번·다음 라운드) |
| `misfire` | 불발 확률 | (무시) | 다음 공격 시 확률 판정 후 **소모** |
| `redirect_random` | (무시) | (무시) | 다음 단일 대상 공격이 자신을 뺀 양편 생존자 중 무작위 대상에게. 사용 시 소모 |
| `stun` | (무시) | (무시) | 다음 행동 불가. 행동 차례에 소모. 돌에게는 칩 피해 +`chip_stun_bonus` 로 바뀜 |
| `grapple` | (무시) | - | 대상을 붙잡음 (떨쳐내기·아군 명중·재부팅으로 해제) |
| `break_free` | - | - | 자신의 붙잡힘 해제 |
| `pass_terminal` | - | - | 단말기 소지자를 대상으로 바꿈 |
| `chip` | 칩 피해 | - | 해킹 카드가 아니어도 돌의 칩을 깎음 |
| `noop` | - | - | 아무것도 안 함 (돌의 리부트) |

확률(4번째 값)은 그 효과가 발동할 확률입니다. 같은 종류의 수정자가 이미 있으면 값과 지속 중 큰 쪽으로 합쳐집니다. `cheap_chrome` 특성의 적에게는 해킹의 `atk_down`/`misfire` 값이 `hack_weak_mult`배(최대 0.9)가 됩니다.

`combo` 문법: 조건 `target_has`(대상이 그 수정자를 가짐) / `self_has`, 수정자 `stun` `atk_down` `misfire` `redirect_random` `grapple` `shield`, 보너스 `dmg_x`(피해 배율) / `dmg_add`(피해 가산) / `chip_add`(칩 추가 피해).

### 3.2 Enemies.csv — 적

```
enemy_id,name,max_hp,physics,magic,agility,charm,personality,pool,traits,color
scav_pipe,스캐빈저 (쇠파이프),40,14,6,9,6,aggressive,enemy,cheap_chrome,#C84B4B
doll,고장난 돌,120,16,10,18,16,aggressive,enemy,doll;evade:0.5;regen:8,#FF4FA3
```

`personality`는 AI 성향(`aggressive` / `defensive` / `hacker`, 가중치는 `ai.PERSONALITIES`). `traits`는 `;`로 잇습니다.

| 특성 | 값 | 뜻 |
|---|---|---|
| `cheap_chrome` | - | 해킹 수치 강화 대상 |
| `evade` | 0~1 | 물리(`stat=physics`) 단일 공격 회피 확률. 스마트 탄 유도 중엔 무시 |
| `regen` | HP | 라운드 끝에 회복 |
| `doll` | - | 돌 기믹의 주인공: 모드로 행동, 칩 트랙, 기절 면역, 단말기 소지자 조준 |
| `fast` | - | 표시용 (현재 효과 없음. 민첩 숫자로 순서가 정해짐) |

`color`는 현황판 그림의 패널 색입니다.

### 3.3 Battles.csv — 전투

```
battle_id,name,players,enemies,gimmicks,overrides,desc
scav4,퍼시피카 뒷골목 (4인),4,scav_pipe*2;scav_gun;scav_boss,nonlethal;ncpd;drag,enemy_hp_mult=1.0;ncpd_max=3,…
doll4,고장난 돌 (4인),4,doll,nonlethal;doll;terminal,chip_stability=9;enemy_hp_mult=1.15,…
```

- `players`: 표시용 권장 인원(자동완성에 `[4인]`으로 붙고, 시뮬레이터의 기본 인원). `1-3` 같은 범위도 됩니다.
- `enemies`: `enemy_id` 또는 `enemy_id*개수`를 `;`로. 같은 적이 여럿이면 이름 뒤에 A, B, C…
- `gimmicks`: `nonlethal` `ncpd` `drag` `doll` `terminal` 을 `;`로. `terminal`은 `pass_terminal` 카드, `doll`은 `break_free` 카드와 `doll` 특성 적, `doll_modes`에 적힌 카드가 있어야 시작됩니다.
- `overrides`: `key=value;key=value` 로 그 전투에서만 설정을 덮어씁니다(3.4의 키). 인원수별 난이도는 여기서 조정합니다.

### 3.4 검증과 흔한 오류

시작 시 데이터 전체를 검사하고, 문제가 있으면 주최자에게만 오류를 보여주고 시작하지 않습니다.

- 알 수 없는 `kind` / `target` / `stat` / `pool` / 효과 / 태그 / 특성 / 기믹 / 설정 키
- `effects`가 비어 있음, 숫자 자리에 문자, `combo` 형식 오류
- `Battles.csv`의 `enemies`에 없는 `enemy_id`, 적 0명(`g*0`)
- `card_id` / `enemy_id` / `battle_id` 중복, 한쪽이 뽑을 카드가 하나도 없음
- 기믹에 필요한 고정 카드·특성 적이 없음

### 3.5 구글 시트로 옮기기

`.env`에 `CARDBATTLE_SOURCE=sheet`를 쓰면 봇 스프레드시트의 탭 `Card_List`, `Card_Enemies`, `Card_Battles`를 읽습니다. 열 이름은 CSV와 **완전히 같게** 두세요. 탭이 없거나 읽기에 실패한 표는 경고 로그를 남기고 그 표만 CSV로 되돌아갑니다. 봇은 이 탭에 절대 쓰지 않습니다.

### 3.6 밸런스 숫자 (`cardbattle/config.py` `DEFAULTS`)

| 키 | 기본값 | 뜻 |
|---|---|---|
| `hand_size` / `damage_variance` / `stat_divisor` | 3 / 4 / 2 | 패 장 수 / 피해 난수 상한 / 능력치 보정 나눗수 |
| `ai_noise` | 0.15 | 적 AI 점수에 섞는 난수 폭 |
| `max_players` | 8 | 참가 정원 |
| `report_delay_sec` | 1.0 | 정산 보고 후 다음 라운드 메시지까지 대기 |
| `use_current_hp` / `allow_unregistered` / `default_hp` / `default_stat` | False / False / 30 / 10 | 시트 HP 사용 / 미등록 참가 허용 / 그때의 기본값 |
| `admin_sees_all` | True | 주최자 전체 카드 선택 |
| `enemy_hp_mult` | 1.0 | 적 HP 배율 (전투 `overrides`로 인원수 보정) |
| `show_intent_target` | False | True면 적 의도의 대상까지 항상 공개 |
| `render_mode` | image | `image` / `text` (현황판 그림) |
| `overkill_margin` | 10 | 과잉 제압 판정 초과량 |
| `ncpd_max` / `ncpd_overkill` / `ncpd_loud` | 3 / 1 / 0.5 | 경계도 상한 / 과잉 제압당 / 소음당 |
| `escape_turns` / `escape_wins_needed` / `escape_hint` | 5 / 3 / True | 도주 턴 수 / 필요한 승리 턴(= 시작 경계도) / 보느와 힌트 |
| `drag_turns` | 2 | 약탈까지 라운드 수 |
| `hack_weak_mult` | 1.5 | 싼 의체 해킹 배율 |
| `chip_stability` / `chip_per_hack` / `chip_round_cap` / `chip_reboot_mult` / `chip_stun_bonus` | 5 / 1 / 2 / 2 / 1 | 칩 트랙 |
| `screen_durability` | 3 | 액정 내구도 |
| `doll_modes` | `cling:doll_cling:4;rampage:doll_claws:4;reboot:doll_reboot:2` | 모드:카드:가중치 (리부트는 연속으로 안 나옴) |
| `firewall_per_damage` / `_per_defend` / `_per_down` / `firewall_max` | 10 / 1 / 2 / 9 | 게이지 충전 |
| `firewall_cost_cover` / `_reboot` / `_aim` / `_scan` / `_maxdoc` | 3 / 2 / 2 / 1 / 4 | 지원 비용 |
| `firewall_cover_shield` / `firewall_maxdoc_ratio` | 0.5 / 0.3 | 팀 엄폐 감소율 / 맥스닥 회복 비율 |

바꾸기 전에 시뮬레이션으로 확인하세요(4.5절).

---

## 4. 개발자 안내

### 4.1 파일 구조

```
cardbattle/
  engine.py        순수 규칙 엔진 (Discord 모름). 데이터클래스, 드로우, 제출, 라운드 열기/정산, 기믹, 방화벽, 도주 단계, 직렬화
  data.py          CSV/시트 로더 + 검증 (tags, combo, traits, gimmicks, overrides 포함)
  ai.py            적 AI 점수 함수와 성향 표 (피해 20 ≈ 1점 눈금)
  config.py        DEFAULTS, coerce, build_config, parse_overrides
  render.py        상태 → 문장 (로그 줄, 이벤트 줄, 의도, 게이지, 콤보 설명, 도주 보고). Discord 모름
  embeds.py        render 의 문장을 discord.Embed 로 배치. 공개 메시지는 전부 여기서
  image_render.py  현황판 PNG (fugitive/image_render 의 글꼴·색 유틸 재사용). 없으면 None
  strings.py       유저 노출 문자열 전부 (표시명 매핑 STAT_KO / JOB_KO / MODE_KO 포함)
  store.py         data/cardbattle_state.json 원자적 저장/복원
  views.py         TurnView(카드 뽑기·방화벽), EscapeView(도주 버튼 3개), HandView, TargetView, ChosenView, FirewallView
  cog.py           CardBattleCog: /카드 명령, 상태기계, 락, 메시지 편집, 재시작 복원
  sim.py           몬테카를로 시뮬레이션 (실제 직업 템플릿, greedy / attack_only / random)
data/cardbattle/*.csv          전투 데이터 (git 추적)
data/cardbattle_state.json     진행 중 세션 (git 무시)
tests/test_cardbattle_{engine,ai,cog}.py
```

`local_server/`는 손으로 복사하는 미러입니다. 패키지와 CSV를 고치면 거기에도 복사하세요.

### 4.2 상태와 라운드

`BattleState.status`: `LOBBY → ROUND_OPEN → RESOLVING → ROUND_OPEN … → ENDED`, 경계도가 차면 `→ ESCAPE → ENDED`.
`round`는 1부터 시작하는 라운드 번호입니다(반턴 없음). `winner`는 `ally` / `enemy` / `escaped` / `caught`, `end_reason`은 `ko` / `chip` / `wipe` / `escaped` / `caught` / `caught_down`.

저장되는 상태는 `LOBBY`, `ROUND_OPEN`, `ESCAPE`, `ENDED`입니다. 정산 보고를 게시하기 전에 죽었다면 `cog_load → _resume()`이 `pending_reports`를 올리고 턴/도주 메시지를 다시 엽니다. 정산을 시작하면 `turn_message_id`를 0으로 두므로 복원 시 "메시지를 새로 열어야 하는지"를 그 값으로 판단합니다.

### 4.3 라운드 규칙 (engine.open_round / resolve_round)

1. `open_round`: 생존한 적마다 패를 뽑아 `ai.choose`로 선택을 정하고 `enemy_choices`에 둡니다. `intents[eid] = {kind, label, target, card_id}`. `public_intents()`는 `show_intent_target` 또는 이번 라운드 `scan` 플래그가 있을 때만 대상을 넣습니다. `doll` 특성 적은 `doll_modes`에서 모드를 뽑아(리부트 연속 금지) 단말기 소지자를 노립니다.
2. `resolve_round`: 양편 생존자를 **단계(해킹 0 → 방어 1 → 공격·특수 2) → 민첩 내림차순 → 난수**로 정렬합니다. 이미 기절한 유닛은 맨 앞에서 `stunned` 줄을 남기며 기절을 소모하고, 정산 중에 기절당한 유닛은 자기 차례에 같은 처리를 받습니다. 선택이 없는 유닛은 맨 뒤에 `wait`(정산 중 쓰러졌으면 생략). 리부트 모드의 돌은 `reboot`.
3. 카드마다 `_apply_card`: 대상 확정(죽었으면 생존자 중 무작위, `redirect`) → `misfire`/`redirect_random` 소모 → `evade` 판정(물리 단일, 스마트 탄 유도 중엔 생략) → 콤보 판정 → 효과 핸들러. `damage`는 `값 + 보정 + randint(0, variance)`(유도 중 난수 0)에 콤보, 공격력 감소, 방어를 곱하고 내림. 비살상 전투의 과잉 제압, 액정, 방화벽 충전, 돌 붙잡힘 해제도 여기서 처리합니다. `loud` 태그는 아군이 쓸 때만 경계도를 올립니다.
4. 라운드 끝: `tick_modifiers`(`shield`/`atk_down`만 1 감소, 만든 라운드 포함), `_regen`, `_advance_drag`.
5. 종료 판정 순서: 칩 0(`chip`) → 경계도 가득(`ncpd` → `start_escape`) → 적 전멸(`ko`) → 아군 전멸(`wipe`). 아니면 `round += 1` 후 바로 `open_round`.

보고(`report`)는 `{"round_no","lines","events","hp","meters","winner","reason"}`. `lines[*]`는 `actor_id/actor_name/side/card_id/card_name/kind/result/targets` 에 선택적으로 `combo`, `chip`, `released`. `result`는 `hit | heal | shield | hack | redirect | misfire | evade | no_target | wait | stunned | down | reboot | break_free | pass | noop`. `targets[*]`에는 `overkill`, `weak`, `revived`, `screen`, `turns`가 붙을 수 있습니다. `events[*].kind`는 `firewall_use | firewall_gain | ncpd | drag_start | drag | drag_stop | looted | regen`. 렌더는 이 dict만 보고 문장을 만듭니다.

도주 단계: `start_escape`가 `escape = {turn, max_turns, alert, alert_max, choices, history, police, hint}`를 만들고, `_escape_prepare_turn`이 경찰 행동을 미리 정한 뒤 `escape_hint`면 다른 행동 하나를 `hint`로 공개합니다. `safe_escape_action(hint)`는 그 턴에 지지 않는 행동입니다. `resolve_escape`는 `wins*2 > n`이면 `alert -= 1`, 0이면 `ESCAPED`, `turn >= max_turns`면 `CAUGHT`.

### 4.4 Discord 층에서 지킬 것

- 상태 변경은 전부 `self.lock` 안에서, 끝나면 `_save()`.
- `TurnView`(카드 뽑기·방화벽)와 `EscapeView`(버튼 3개)는 `timeout=None` + 고정 `custom_id`. 상태를 갖지 않으므로 `cog_load`의 `add_view` 두 번으로 모든 메시지 버튼이 복원됩니다. `_turn_guard`가 `interaction.message.id == st.turn_message_id`로 옛 메시지를 거절합니다(`turn_message_id`는 도주 메시지에도 같이 씁니다).
- 라운드 메시지는 `embed + file(board.png) + view`로 보내고 임베드의 `set_image("attachment://board.png")`로 그림을 안에 넣습니다. 진행률을 갱신할 때는 `msg.edit(embed=...)`만 호출해야 첨부와 버튼이 남습니다. `_image_mode()`가 `render_mode`와 Pillow/글꼴 가용성을 검사하고, 못 쓰면 한 번만 경고 로그를 남깁니다.
- ephemeral 패 메시지는 **그 메시지의 컴포넌트 상호작용**에서만 `response.edit_message`로 고칠 수 있습니다. 버튼 클릭(공개 메시지)에서는 반드시 `response.send_message(ephemeral=True)`.
- Select는 옵션 25개·라벨 100자 제한. 주최자 전체 카드는 24개 + "다음 ▶" 옵션으로 페이징.
- `/카드`는 `tests/test_help_option.py` 규칙을 따릅니다: 클래스 속성의 단일 `app_commands.Command`, 필수 옵션 없음, 마지막 옵션 `도움말`.
- 캐릭터 시트 열 매핑은 `engine.player_combatant` 한 곳뿐입니다(`max_hp current_hp physics magic agility charm job color`).

### 4.5 테스트와 시뮬레이션

```
PYTHONIOENCODING=utf-8 py -m pytest tests -q -k cardbattle
PYTHONIOENCODING=utf-8 py -m cardbattle.sim --all --games 200
PYTHONIOENCODING=utf-8 py -m cardbattle.sim --battle doll4 --players 4 --games 500 --seed 1
PYTHONIOENCODING=utf-8 py -m cardbattle.sim --battle scav5 --set ncpd_max=4 --policy greedy
```

- 엔진 테스트는 시드 고정 `random.Random`과 `damage_variance=0`, `stat_divisor=1000`으로 숫자를 결정적으로 만듭니다(`flat_cfg()`, `mk_cards()`, `always(card_id)`, `play()` 도우미).
- cog 테스트는 `FakeChannel`/`FakeMessage`/`FakeInteraction`으로 명령과 버튼 흐름 전체(주최 → 참가 → 시작 → 뽑기 → 선택 → 정산 → 다음 라운드, 방화벽, 도주 단계, 강제 종료, 재시작 복원, 종료)를 돌립니다.
- sim의 플레이어 봇은 실제 직업 템플릿(엣지러너 HP 110 근력 16 / 넷러너 HP 80 RAM 16 / 솔져 HP 110 16·14)을 돌려 쓰고, 정책 세 개를 **항상 나란히** 출력합니다. `attack_only`(무조건 가장 센 공격)가 `greedy`와 승률이 같거나 높으면 공격 외 카드가 함정이라는 신호입니다. 출력은 승률, 결과 분포(`ko` / `chip` / `escaped` / `caught` / `wipe`), 라운드 p25/p50/p75, 사망·약탈·과잉 제압 횟수, 많이 쓴 카드입니다. 사람은 봇보다 잘 하므로 하한선으로 봅니다.
- 기본 인원은 `Battles.csv`의 `players` 열이고, 전투의 `overrides`가 자동 적용됩니다(`--set`이 그 위를 덮습니다).

### 4.6 확장 포인트

| 하고 싶은 것 | 손댈 곳 |
|---|---|
| 새 카드 / 콤보 / 태그 조합 | `Cards.csv` 한 줄 (코드 수정 없음) |
| 새 효과 종류 | `engine.EFFECT_KINDS`에 추가, `HANDLERS`에 핸들러, 지속형이면 `MODIFIER_KINDS`/`TICKING`/`ONE_SHOT`, 로그 문구는 `strings.DETAIL`/`TAG`, 그림은 `image_render.TAG_KO` |
| 새 특성 | `engine.TRAITS`에 추가하고 쓰는 곳(`_apply_card`/`_h_modifier`/`_regen`)에 분기 |
| 새 기믹 | `engine.GIMMICKS`에 추가, `new_battle`에서 게이지 초기화, `resolve_round` 종료 판정, `render.meters_text`/`embeds.intro` 규칙 줄 |
| 새 방화벽 지원 | `engine.FIREWALL_OPTIONS` + `use_firewall` 분기 + `config` 비용 키 + `strings.FIREWALL_OPTION_*` |
| 새 AI 성향 | `ai.PERSONALITIES`에 가중치 한 줄 |
| 턴 제한 시간 | fugitive의 `_arm_timer`/`_cancel_timer` 패턴을 `cog.py`에 옮기고 만료 시 `_resolve_round(force=True)` |
