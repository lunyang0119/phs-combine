# 카드 전투 미니게임 설명서

디스코드 봇의 `/카드` 명령으로 진행하는 카드 드로우 전투입니다. 참가자 전원이 한 편(아군)이 되어 봇이 조종하는 적과 싸웁니다. 사이버펑크 2077 TRPG 세션용으로 만들어져 있어 화면 표시는 그 세계관의 이름(RAM, 엣지러너, 넷러너, 보느와의 방화벽, 에디 …)을 쓰지만, 캐릭터 시트의 열 이름은 그대로입니다.

1. [플레이어 안내](#1-플레이어-안내) — 참가해서 카드를 내는 방법, 라운드 규칙, 준비 단계(무기·에디), 기믹
2. [주최자 안내](#2-주최자-안내) — 세션을 열고, 시작하고, 끝내는 방법, 금고
3. [데이터 편집 안내](#3-데이터-편집-안내) — 카드·적·전투·튜토리얼을 CSV(또는 시트)로 고치는 방법
4. [개발자 안내](#4-개발자-안내) — 코드 구조, 규칙 엔진, 테스트, 시뮬레이션

부외자용 짧은 규칙은 디스코드에서 `/카드 옵션:규칙`으로 볼 수 있습니다(섹션별 임베드, 본문은 `cardbattle/strings.py`의 `HELP_SECTIONS`).

---

## 1. 플레이어 안내

### 1.1 한 판의 흐름

```
주최자: /카드 옵션:주최                 ← 세션 열림 (한 번에 하나)
모두  : /카드 옵션:참가                 ← 로비 메시지에 이름이 추가됨
주최자: /카드 옵션:시작 전투id:scav4     ← 전투 소개(규칙 요약) + 준비 단계 메시지
─── 준비 단계 (3~5분) ────────────────────────────────────
모두  : 주운 무기(mk.1~2) 확인 → [정식 개조] / [뒷골목 개조] (1인 2회) → [준비 완료]
       전원 준비 완료 또는 주최자의 /카드 옵션:턴종료 → 전투 시작
       (몸의 보느와 전투는 여기서 '기습' 보고가 먼저 올라옵니다)
─── 라운드 ─────────────────────────────────────────────
봇   : 적의 **행동 종류**를 먼저 공개 (공격 준비 / 해킹 준비 / 방어 준비 / 모드 …, 대상은 비공개)
모두  : [카드 뽑기] → 카드 고르기 → (필요하면) 대상 고르기 (⚠ 과잉 위험 표시)
       [방화벽] → 팀 게이지로 지원 사용 (라운드에 한 번, 누구나)
       전원 선택 → 자동 정산 (또는 주최자의 /카드 옵션:턴종료)
봇   : 라운드 정산 로그 (🟢 아군 / 🔴 적 줄이 섞여서 실제 순서대로) + 💡 팁 → 다음 라운드 메시지
… 한쪽이 모두 쓰러지거나 기믹 조건이 끝날 때까지 반복 …
봇   : 결과 + 보수 (금고에 들어감). 무기는 부서집니다
```

### 1.2 참가하기

- 모집 중(로비)일 때만 `/카드 옵션:참가`가 됩니다. 주최자도 참가할 수 있습니다.
- HP와 능력치는 **캐릭터 시트**에서 가져옵니다. 시트에 없으면 참가가 거절됩니다(`/등록` 후 다시, 시트를 방금 고쳤다면 `/시트갱신`).
- 로그와 로비에 표시되는 이름은 **디스코드 서버 닉네임**입니다. 직업은 시트의 `job` 열을 바꿔 보여줍니다: 스트라이커 → **엣지러너**, 마테리아 위버 → **넷러너**, 솔져 → 솔져. 시트의 `color` 열(`#RRGGBB`)이 있으면 현황판 그림의 패널 색으로 씁니다.

### 1.3 준비 단계 — 무기와 에디

전투가 시작되면 먼저 **준비 단계** 메시지가 올라옵니다(튜토리얼·연습 전투 포함, `prep_enabled`).

- 각자 **무기를 하나 줍습니다.** 직업별 이름(엣지러너는 총·칼, 넷러너는 사이버덱, 솔져는 소총·렌치)이 붙고, 등급은 mk.1 또는 mk.2 입니다. 등급은 **내가 내는 피해 카드**(물리·RAM 공통)에 더해집니다: mk.1 +0 / mk.2 +3 / mk.3 +6 / mk.4 +10. 전투가 끝나면 부서집니다(다음 전투에서 다시 줍습니다).
- 팀 **금고**의 에디가 인원수로 **똑같이** 나뉩니다. 금고 파일이 없으면 첫 전투는 인원 × 100 으로 시작합니다.
- 버튼 셋: **정식 개조**(100, 확정, mk.3까지) / **뒷골목 개조**(50, 도박, mk.4의 유일한 길) / **준비 완료**. 1인당 개조 2회.
- 뒷골목 개조 규칙: mk.1→2는 무조건 성공. 그 위는 d10을 굴려 **4 이상 성공(+1), 3 이하 실패(−1), 1은 두 단계 하락(바닥 mk.1), 10은 성공에 환불**. 굴림은 공개 채널에 올라옵니다.
- 쓰지 않은 에디는 전투가 시작될 때 금고로 돌아가고, 전투 중 **현장 개조**의 재원이 됩니다.
- 전원이 준비 완료를 누르거나 주최자가 `/카드 옵션:턴종료`를 쓰면 전투가 열립니다.

### 1.4 카드 내기

1. 라운드 메시지의 **🃏 카드 뽑기** 버튼을 누릅니다. 본인에게만 보이는 메시지로 이번 라운드의 패 3장이 옵니다. 카드마다 **예상 피해 범위**("약 16~20 (무기 +3)"), **⚠ 과잉 위험** 대상, **▶ 콤보** 조건이 적혀 있습니다.
2. 카드를 고릅니다. 대상이 둘 이상이면 대상 드롭다운이 이어서 뜹니다(⚠ 과잉 위험인 대상은 라벨에 표시). 대상이 하나뿐이면 자동으로 정해집니다.
3. "선택 완료"가 뜨면 끝. 정산 전까지는 **다시 고르기 / 선택 취소**로 바꿀 수 있습니다.

알아둘 점
- 패는 라운드마다 **한 번만** 뽑힙니다. 버튼을 다시 눌러도 같은 3장입니다.
- 금고에 에디가 남아 있고 내 무기가 mk.4 미만이면 패에 **현장 개조**(50)가 하나 더 들어 있습니다. 이번 라운드 행동을 포기하고 d10을 굴립니다. 성공 +1 등급, **실패해도 등급은 그대로**이고 다음 공격 한 번이 불발됩니다.
- 지난 라운드 메시지의 버튼은 거절됩니다. 항상 가장 아래 메시지를 쓰세요.
- 쓰러졌거나(HP 0) 기절(💫) 상태면 행동할 수 없고 선택 현황 인원에서도 빠집니다. 붙잡힌(🪢) 상태면 **떨쳐내기**만 낼 수 있습니다.
- 선택을 끝내지 않은 채 주최자가 강제 정산하면 "대기"로 처리됩니다.

### 1.5 라운드 정산 규칙

- 양편이 **같은 라운드에 함께** 정산됩니다. 순서는 **해킹 → 방어 → 공격/특수**, 같은 단계 안에서는 **민첩이 높은 쪽이 먼저**입니다. 한마디로 **"준비 동작이 먼저, 한 방은 마지막."**
- 그래서 빠른 넷러너의 **시스템 리셋**(기절)은 느린 적의 공격을 **같은 라운드에** 막고, 느린 적보다 먼저 공격하면 적이 때리기 전에 쓰러뜨릴 수 있습니다. 반대로 적 해커가 더 빠르면 내 공격이 먼저 끊깁니다.
- **방어는 이번 라운드까지, 해킹은 상대의 다음 행동까지.** 방어 카드는 이번 라운드의 공격 단계에만 유효하고, **무기 글리치**(다음 공격 30% 감소)·**사이버웨어 고장**(다음 공격 50% 불발)·**적대 인식**(다음 공격 무작위 대상)·**시스템 리셋**(다음 행동 불가)은 상대의 다음 그 행동에서 소모됩니다. 기절한 적은 그 라운드가 끝날 때까지 💫 상태이므로 "기절한 적에게" 콤보가 성립합니다.
- 피해 = 카드 값 + 능력치 보정 + 무기 보너스 + 0~4 난수. 보정은 물리 카드 `근력 ÷ 2`, RAM 카드 `RAM ÷ 2`. 엣지러너(근력 16)의 근접 타격은 16~20, 넷러너(RAM 16)의 쇼트 서킷은 16~20, 서로 바꿔 들면 12~16, mk.3 무기면 +6.

### 1.6 카드 종류

| 종류 | 하는 일 | 카드 |
|---|---|---|
| 공격 | 적에게 피해 | 근접 타격(물리 8, 기절한 적에게 ×1.5), 쇼트 서킷(RAM 8, 글리치 적에게 +5), 관절 치기(물리 5, **비살상**), 제압 사격(적 전체 4, **소음**) |
| 방어 | 이번 라운드 받는 피해 감소 | 엄폐(자신 50%), 보느와의 ICE(아군 한 명 70% + 데몬 감염 제거) |
| 해킹 | 적에게 디버프 (돌에게는 칩 피해, 보느와에게는 ICE 한 층) | 무기 글리치(다음 공격 30%↓), 사이버웨어 고장(다음 공격 50% 불발), 적대 인식(다음 공격 무작위 대상), 시스템 리셋(다음 행동 불가) |
| 특수 | 아군만 뽑는 강한 카드와 회복 | 산데비스탄 돌격(물리 14 + 반동 3, **살상**, 붙잡고 있는 적에게 ×1.5), 과부하 핵(RAM 12, 고장 상태 적에게 ×1.5), 맥스닥 흡입기(아군 HP 6 + RAM 보정, 쓰러진 아군도 일으킴), 현장 개조(고정) |

**콤보**: 카드 설명의 "▶ 콤보: … 적에게 피해 ×1.5" 조건이 맞으면 보너스가 붙고 로그에 ⚡콤보! 가 찍힙니다. 해킹이 공격보다 먼저 정산되므로, 같은 라운드에 한 사람이 걸고 다른 사람이 때리면 바로 터집니다.

### 1.7 보느와의 방화벽 (팀 게이지)

아군이 피해를 **10 받을 때마다 +1**, **방어 카드를 낼 때마다 +1**, **아군이 쓰러지면 +2** 찹니다(최대 9). 라운드 메시지의 **🧱 방화벽** 버튼으로 **라운드에 한 번**, 살아 있는 누구나 쓸 수 있습니다. 어떤 지원이 열려 있는지는 **전투마다 다릅니다**(소개문의 규칙 칸과 버튼 메뉴에 그 전투의 것만 나옵니다): 연습·튜토리얼은 셋, 스캐빈저에서 적 스캔, 돌에서 스마트 탄 유도가 열리고, 몸의 보느와에서는 다섯 전부입니다.

| 지원 | 비용 | 효과 |
|---|---|---|
| 팀 엄폐 | 3 | 아군 전원 이번 라운드 피해 50% 감소 |
| 긴급 재부팅 | 2 | 아군 전원의 기절·불발·적대 인식·글리치·붙잡힘·데몬 감염 해제 (기절한 사람도 이 라운드에 행동 가능) |
| 스마트 탄 유도 | 2 | 이번 라운드 아군 공격의 난수 0, 회피 무시 (비살상 마무리 계산용) |
| 적 스캔 | 1 | 이번 라운드 적 의도의 **대상** 공개 |
| 맥스닥 | 4 | 가장 위험한 아군 HP 30% 회복 (쓰러진 아군도 일으킴) |

몸의 보느와 전투에서는 적이 방화벽을 깎거나 **한 라운드 잠글** 수 있습니다(게이지 옆 🔒).

### 1.8 기믹 — 전투별로 켜지는 규칙

전투 시작 소개문의 "규칙" 칸에 그 전투에 켜진 것만 나옵니다. 문구는 `strings.INTRO_RULES`.

**비살상 (nonlethal)** — 적을 쓰러뜨릴 때 남은 HP를 **15 이상 초과**해서 깎으면 **⚠과잉 제압**입니다. **살상** 태그 카드(산데비스탄 돌격)로 쓰러뜨리면 무조건, **비살상** 태그 카드(관절 치기)는 절대 아닙니다. 외울 필요 없이 패와 대상 선택창에 **⚠ 과잉 위험**이 표시됩니다(콤보·무기 보너스·스마트 탄 유도까지 계산한 최대 피해 기준). 과잉 제압은 보수에서 50씩 깎입니다.

**NCPD 경계도 (ncpd)** — 과잉 제압 1회 +1, **소음** 태그 카드(제압 사격) 사용 1회 +0.5. 가득 차면(기본 3) 드론이 출동해 전투가 끊기고 **도주 단계**로 넘어갑니다. 적이 몇 명 남았든 상관없습니다. 소음은 보수에서 20씩 깎입니다.

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

**몸의 보느와** — 시나리오 4장의 마지막 보스. 로비와 준비 단계는 평소와 같고, 전투가 열리는 순간 **선제 라운드**(first_strike)가 먼저 정산됩니다: 그만 움직여 아군 셋에게 기습 퀵핵 피해, 한 명에게 데몬 감염. 그다음 1라운드가 평소처럼 열립니다. 함께 켜지는 기믹:
- **데몬 감염 (daemon)** 🦠 — 감염된 채로 카드를 내면 스택당 5 자해(걸린 라운드에는 물지 않고, 그다음 라운드의 행동부터). 걸린 다음 라운드 끝에 저절로 사라지고, 또 걸리면 스택이 쌓입니다. 동시에 최대 4명(넘으면 이미 감염된 사람에게 겹칩니다). 보느와의 ICE(한 명)·긴급 재부팅(전원)으로 지웁니다.
- **블랙 ICE (ice)** 🧊 — 그를 감싼 ICE 층마다 받는 피해 −30%(배율 하한 10%). 아군 해킹이 맞을 때마다 한 층(라운드에 두 층까지). 물리 공격은 30% 빗나갑니다. 해킹으로 열고 공격으로 끝내는 전투입니다.
- **방화벽 공방 (firewall_duel)** — 그의 침입 모드가 단말기 보느와의 방화벽을 2 깎고(적대 인식도 겹칩니다), 다음 라운드 버튼을 잠급니다.
- **생명 유지 시계 (life_support)** 💓 — 라운드마다 −1, 과부하 모드가 나오면 −1 더. 0이 되면 그가 발작으로 쓰러지며 전투가 끝납니다(이기지만 **위독**, 보수 절반).
- **모드** — 장악(ICE 한 층 복구 + 아군 둘 감염), 침입(방화벽 공격 + 적대 인식), 과부하(전원에게 RAM 피해, 대신 ICE 전부 내려감 + 시계 −1. 연속으로는 안 나옴). 과부하 다음 라운드가 공격 기회입니다. 그는 기절합니다(기절하면 그 라운드의 모드 행동이 취소).
- **상태** — 끝났을 때 그의 상태가 기록됩니다: 관절 치기(비살상 카드)로 끝내면 **멀쩡**, 그냥 쓰러뜨리면 **부상**, 과잉 제압이나 발작이면 **위독**. 4장의 대사는 이걸 보고 고르면 됩니다.

**튜토리얼 (tutorial)** — `전투id:tut`. 여섯 라운드 대본으로, 라운드마다 **📘 코치** 임베드가 과제를 내고 정산 보고 끝에 ✅/❌ 피드백이 붙습니다(의도 읽기 → 엄폐 → 시스템 리셋으로 공격 취소 → 기절 콤보 → 방화벽 버튼 → ⚠ 보고 관절 치기로 마무리). 과제에 필요한 카드는 패에 반드시 들어 있고, 마지막 수업 전에는 적이 쓰러지지 않습니다. 금고는 임시라 보수가 없고 주최자도 보통 패를 받습니다.

### 1.9 결과 로그 읽기

- 라운드 정산 임베드(보라 띠, 기습은 자홍 띠)에 양편 행동이 실제 순서대로 섞여 나옵니다. 줄 앞의 🟢 = 아군, 🔴 = 적. 줄 끝의 HP는 **대상**의 HP, 대상이 자신이면 이름을 생략합니다.
- 접두어: `⚡콤보!` 콤보 발동, `(적대 인식!)` 원래 대상이 아닌 곳으로 감. 꼬리표: 💀 쓰러짐, ⚠과잉 제압, (싼 의체!) 해킹 강화, ✨일어남, 📱액정 n, 🪢… 풀려남, 💾칩 −n, 🧊ICE −n, 🦠데몬 −n(자해), 🧊(ICE 에 막힘).
- 그 아래 이벤트 줄: 🧱 방화벽 사용/깎임/잠김, 🚨 경계도 변화, 🩸 끌고 가기, 💔 약탈, 🩹 자가 복구, 🧊 ICE 복구·과부하, 💓 생명 유지.
- 그 아래 💡 **팁**: 그 전투에서 **처음** 일어난 일(기절로 공격 취소, 방어로 피해 감소, 콤보, 불발, 회피, 방화벽 사용 가능, 과잉 제압, 감염, ICE …)을 한 줄로 설명합니다. 한 전투에 종류별로 한 번만 나옵니다(`tips=false`로 끌 수 있음).
- 상태 아이콘: 🛡 방어 중, ⬇ 다음 공격 약화, ❓ 불발 위험, 🔀 적대 인식, 💫 기절, 🪢 붙잡힘, 🦠 감염.
- 라운드 메시지에는 **현황판 그림**이 붙습니다(왼쪽 아군 패널 = 각자의 색 + 무기 등급, 오른쪽 적 패널 = 붉은 계열, 패널 아래 줄 = 이번 라운드 행동 또는 다음 라운드 의도, 초록/빨강 선 = 공격 방향, 굵은 선 = 콤보, 위쪽 띠 = 게이지). 서버에 Pillow 또는 한글 글꼴이 없으면 그림 없이 임베드만 옵니다.
- 결과 임베드에는 **보수** 내역(기본 × 결과 비율 − 벌금 = 보수, 남은 금고 반환 → 팀 금고), 각자의 무기(부서짐), 보느와 상태가 붙습니다.

---

## 2. 주최자 안내

| 명령 | 조건 | 하는 일 |
|---|---|---|
| `/카드 옵션:주최` | 진행 중인 세션이 없을 때 | 로비 메시지를 올리고 세션을 엽니다. **실행한 사람이 그 세션의 주최자** |
| `/카드 옵션:시작 전투id:<id>` | 주최자, 로비, 참가자 1명 이상 | 전투 데이터를 읽고 준비 단계(또는 1라운드)를 엽니다. `전투id`는 자동완성되며 `[n인]` 표시가 붙습니다. **참가 인원에 맞는 id**를 고르세요 (`scav3`~`scav7`, `doll3`~`doll7`, `bonewa3`~`bonewa7`, 튜토리얼 `tut`, 연습용 `b01`) |
| `/카드 옵션:턴종료` | 주최자, 준비 단계·라운드·도주 단계 | 준비 단계면 바로 전투를 열고, 라운드면 아직 고르지 않은 사람을 "대기"로 두고 정산합니다 |
| `/카드 옵션:현황` | 누구나 | 상태·라운드·적 의도·게이지·HP 표(준비 단계면 준비 현황)를 본인에게만 |
| `/카드 옵션:금고` | 누구나 | 팀 금고의 에디. `금액:<n>`을 붙이면 주최자·관리자가 금고를 그 값으로 맞춥니다 (세션 시작 전 잔액 조정용) |
| `/카드 옵션:규칙` | 누구나 | 부외자용 긴 도움말 (섹션별 임베드, 본인에게만) |
| `/카드 옵션:관리자` | 주최자·관리자 (참가자) | 이번 전투에 한해 전체 카드 목록에서 고르기 (토글) |
| `/카드 옵션:종료` | 주최자 | 세션을 지웁니다 (보수는 지급되지 않음) |
| `/카드 도움말:True` | 누구나 | 명령 사용법과 30초 설명 |

주최자의 특권
- 주최자도 보통은 다른 참가자와 똑같이 무작위 패 3장을 받습니다. `/카드 옵션:관리자`를 쓰면 **그 사람만, 이번 전투에 한해** 카드 뽑기가 **전체 카드 목록**을 보여줍니다(24장이 넘으면 "다음 ▶"으로 페이징). 다시 쓰면 해제. 주최자·관리자만 쓸 수 있고 참가자여야 합니다. 로비에서 켜 두면 시작한 전투로 이어집니다. 단말기 전투에서는 주최자가 기본 소지자입니다.
- 환경변수 `CARDBATTLE_ADMIN_IDS`(없으면 `FUGITIVE_ADMIN_IDS`)에 적힌 사람은 주최자가 아니어도 `턴종료`·`종료`·`금고 금액:`을 쓸 수 있습니다.

금고와 보수
- 금고는 `data/cardbattle_gig.json` 하나로, 세션(보스 2~3회) 내내 이어집니다. 무기는 전투마다 새로 줍고 끝나면 부서집니다.
- 보수 = 인원 × `reward_per_player`(스캐빈저 150, 돌 200, 보느와 250) × 결과 비율(승리 1, 발작 결말 0.5, 전멸·잡힘 0) − 과잉 제압 × 50 − 소음 × 20. 바닥 0. 여기에 전투 중 안 쓴 금고가 더해져 다음 전투의 분배금이 됩니다.
- 첫 전투 전에 `/카드 옵션:금고 금액:300` 처럼 시작 잔액을 정해 두면 좋습니다(없으면 인원 × 100).

제한
- 세션은 봇 전체에서 **하나**입니다. 다른 채널·서버에서 진행 중이면 `주최`가 거절됩니다.
- 턴 제한 시간은 없습니다. 잠수하는 사람이 있으면 `턴종료`로 넘기세요.
- 봇이 재시작되어도 진행 중인 전투(준비 단계·도주 단계 포함)는 복원되고, 기존 메시지의 버튼이 그대로 동작합니다. 끝났는데 보수를 못 받은 상태였다면 복원 때 지급됩니다.

세션 운영 팁
- 처음 하는 사람이 있으면 `tut`로 10분 연습을 먼저 돌리세요. 코치 문구는 `Tutorial.csv`에서 바꿀 수 있습니다.
- 스캐빈저 전투는 "비살상"이 핵심입니다. ⚠ 표시를 보고 관절 치기·스마트 탄 유도로 마무리하게 유도하세요. 경계도가 차서 도주 단계로 가는 것도 설계된 분기입니다.
- 돌 전투는 해킹이 해법입니다. 첫 라운드 모드 공개와 "칩 안정도" 게이지를 보고 플레이어가 스스로 알아차리게 두세요.
- 몸의 보느와 전투는 "해킹으로 ICE를 열고 약한 카드로 끝내는" 전투입니다. 결과 임베드의 **상태**(멀쩡/부상/위독)로 4장 대사를 고르세요. 전투 중 문구(기믹 설명·팁·모드 설명)는 `strings.py`에서 보느와 말투로 바꾸면 됩니다.

---

## 3. 데이터 편집 안내

전투 데이터는 리포지토리의 `data/cardbattle/` 아래 CSV 네 개입니다. UTF-8(BOM 허용), 쉼표 구분. 수정은 **다음 `/카드 옵션:시작`부터** 반영됩니다. `local_server/data/cardbattle/`에도 같은 파일을 복사하세요.

### 3.1 Cards.csv — 카드

```
card_id,name,kind,target,stat,pool,weight,effects,tags,combo,desc
atk_slash,근접 타격,attack,enemy,physics,both,10,damage:8,,target_has:stun:dmg_x:1.5,기본 물리 공격. 기절한 적에게 1.5배
hack_jam,무기 글리치,hack,enemy,magic,both,5,atk_down:0.3,,,대상의 다음 공격 30% 감소
field_mod,현장 개조,special,self,none,fixed,0,field_mod:1,,,금고의 에디로 무기를 즉석 개조한다
bw_ambush,기습 퀵핵,hack,all_enemy,magic,fixed,0,scatter_damage:4:3;daemon:1:1,,,셋이 다치고 하나가 감염된다
```

| 열 | 값 | 뜻 |
|---|---|---|
| `card_id` | 영문 식별자 | 내부 이름. `pass_terminal` / `break_free` / `field_mod` 는 규칙이 찾는 고정 id |
| `name` | 문자열 | 표시 이름 |
| `kind` | `attack` `defend` `hack` `special` | 정산 단계(해킹 0 → 방어 1 → 공격·특수 2)와 AI 성향 가중치 |
| `target` | `enemy` `ally` `self` `all_enemy` `all_ally` | `enemy`/`ally`는 대상 선택이 뜸 |
| `stat` | `physics` `magic` `none` | 피해·회복에 더할 능력치(÷`stat_divisor`). 화면에는 근력 / RAM |
| `pool` | `both` `ally` `enemy` `fixed` | 누가 뽑는지. `fixed` 는 드로우되지 않고 규칙이 손에 쥐어줌 (단말기 넘기기, 떨쳐내기, 현장 개조, 돌·보느와의 모드 카드, 기습 카드) |
| `weight` | 숫자 | 드로우 가중치 (0 이면 AI 도 뽑지 않음 — 튜토리얼 전용 `e_training`) |
| `effects` | 아래 문법 | 효과 목록 |
| `tags` | `loud` `lethal` `nonlethal` 을 `;`로 | 소음(경계도 +0.5, 벌금) / 살상(제압 시 무조건 과잉) / 비살상(과잉 면제, 보느와 '멀쩡') |
| `combo` | `조건:수정자:보너스:값` | 비우면 없음. 설명과 로그에 자동 표기 |
| `desc` | 문자열 | 드롭다운·패 설명 (100자 이내 권장) |

`effects` 문법: `종류[:값[:지속[:확률]]]`을 `;`로 이어 씁니다. **`scatter_damage` 와 `daemon` 은 셋째 자리가 지속이 아니라 무작위 대상 수**입니다.

| 종류 | 값 | 셋째 자리 | 설명 |
|---|---|---|---|
| `damage` | 기본 피해 | - | 대상에게 피해 (보정 + 무기 + 난수, 콤보·공격력 감소·방어·ICE·회피 적용). 한 카드에 두 번 쓰면 두 번 때림 |
| `scatter_damage` | 기본 피해 | 대상 수 | 상대편 생존자 중 N명을 무작위로 골라 `damage`와 같은 경로로 |
| `heal` | 회복량 | - | 대상 HP 회복. 쓰러진 아군도 대상 가능(일으킴) |
| `self_damage` | 피해 | - | 사용자 본인에게 고정 피해 |
| `shield` | 감소 비율 0~1 | 라운드 수 | 받는 피해 감소. 만든 라운드 포함해 라운드 끝에 1 감소 (`:1` = 이번 라운드만) |
| `atk_down` | 감소 비율 | (무시) | 다음 공격 1회의 피해 감소. 사용 시 소모 (불발·회피된 공격은 소모하지 않음) |
| `misfire` | 불발 확률 | (무시) | 다음 공격 시 확률 판정 후 **소모** |
| `redirect_random` | (무시) | (무시) | 다음 단일 대상 공격이 자신을 뺀 양편 생존자 중 무작위 대상에게. 사용 시 소모 |
| `stun` | (무시) | (무시) | 다음 행동 불가. 라운드 끝에 소모(그 라운드 동안 💫 — 콤보 조건). 돌에게는 칩 피해 +`chip_stun_bonus` 로 바뀜 |
| `daemon` | 스택 | 대상 수 | 데몬 감염. 단일 대상 카드는 고른 대상, 전체 카드는 무작위 N명. 상한에 닿으면 기감염자에게 겹침 |
| `cleanse` | - | - | 대상의 데몬 감염 제거 |
| `grapple` | (무시) | - | 대상을 붙잡음 (떨쳐내기·아군 명중·재부팅으로 해제) |
| `break_free` | - | - | 자신의 붙잡힘 해제 |
| `pass_terminal` | - | - | 단말기 소지자를 대상으로 바꿈 |
| `chip` | 칩 피해 | - | 해킹 카드가 아니어도 돌의 칩을 깎음 |
| `firewall_drain` | 양 | - | 아군 방화벽 게이지 감소 (적 전용) |
| `firewall_lock` | - | - | 다음 라운드 방화벽 버튼 잠금 |
| `ice` | 층 수 | - | ICE 층 복구 (`ice` 기믹) |
| `expose` | - | - | ICE 전부 제거 + 생명 유지 −1 |
| `field_mod` | - | - | 현장 개조 (금고에서 비용, d10, 실패 시 불발) |
| `noop` | - | - | 아무것도 안 함 (돌의 리부트) |

확률(4번째 값)은 그 효과가 발동할 확률입니다. 같은 종류의 수정자가 이미 있으면 값과 지속 중 큰 쪽으로 합쳐집니다(데몬은 스택을 더함). `cheap_chrome` 특성의 적에게는 해킹의 `atk_down`/`misfire` 값이 `hack_weak_mult`배(최대 0.9)가 됩니다.

`combo` 문법: 조건 `target_has`(대상이 그 수정자를 가짐) / `self_has`, 수정자 `stun` `atk_down` `misfire` `redirect_random` `grapple` `shield` `daemon`, 보너스 `dmg_x`(피해 배율) / `dmg_add`(피해 가산) / `chip_add`(칩 추가 피해).

### 3.2 Enemies.csv — 적

```
enemy_id,name,max_hp,physics,magic,agility,charm,personality,pool,traits,color
scav_pipe,스캐빈저 (쇠파이프),40,14,6,9,6,aggressive,enemy,cheap_chrome,#C84B4B
doll,고장난 돌,120,16,10,18,16,aggressive,enemy,doll;evade:0.5;regen:8,#FF4FA3
bonewa_body,몸의 보느와,110,8,18,16,14,hacker,enemy,modes;evade:0.3,#FF2BD6
```

`personality`는 AI 성향(`aggressive` / `defensive` / `hacker`, 가중치는 `ai.PERSONALITIES`). `traits`는 `;`로 잇습니다.

| 특성 | 값 | 뜻 |
|---|---|---|
| `cheap_chrome` | - | 해킹 수치 강화 대상 |
| `evade` | 0~1 | 물리(`stat=physics`) 단일 공격 회피 확률. 스마트 탄 유도 중엔 무시 |
| `regen` | HP | 라운드 끝에 회복 |
| `doll` | - | 돌 기믹의 주인공: `doll_modes`로 행동, 칩 트랙, 기절 면역, 단말기 소지자 조준 |
| `modes` | - | 모드로 행동하는 보스(몸의 보느와): 전투의 `modes` 설정에서 모드를 뽑음. 전투당 하나만 |
| `fast` | - | 표시용 (현재 효과 없음. 민첩 숫자로 순서가 정해짐) |

`color`는 현황판 그림의 패널 색입니다.

### 3.3 Battles.csv — 전투

```
battle_id,name,players,enemies,gimmicks,overrides,desc
scav4,퍼시피카 뒷골목 (4인),4,scav_pipe*2;scav_gun;scav_boss,nonlethal;ncpd;drag,"enemy_hp_mult=1.15;ncpd_max=3;firewall_options=cover,reboot,scan,maxdoc",…
bonewa4,몸의 보느와 (4인),4,bonewa_body,nonlethal;first_strike;daemon;firewall_duel;life_support;ice,"enemy_hp_mult=1.2;life_support_rounds=8;ice_layers=4;firewall_options=cover,reboot,aim,scan,maxdoc;reward_per_player=250;modes=seize:bw_seize:4,breach:bw_breach:4,overload:bw_overload:2;modes_norepeat=overload",…
tut,튜토리얼: 뒷골목 연습,1-7,scav_pipe;scav_gun,tutorial;nonlethal,"enemy_hp_mult=1.5;prep_sandbox=true;admin_sees_all=false;tutorial_script=tut1;firewall_options=cover,reboot,maxdoc",…
```

- `players`: 표시용 권장 인원(자동완성에 `[4인]`으로 붙고, 시뮬레이터의 기본 인원). `1-3` 같은 범위도 됩니다.
- `enemies`: `enemy_id` 또는 `enemy_id*개수`를 `;`로. 같은 적이 여럿이면 이름 뒤에 A, B, C…
- `gimmicks`: `nonlethal` `ncpd` `drag` `doll` `terminal` `first_strike` `daemon` `firewall_duel` `life_support` `ice` `tutorial` 을 `;`로. `terminal`은 `pass_terminal` 카드, `doll`은 `break_free` 카드와 `doll` 특성 적, `first_strike`는 `first_strike_cards`의 카드, `tutorial`은 `Tutorial.csv`의 `tutorial_script`가 있어야 시작됩니다.
- **숨기기**: `battle_id`(또는 `card_id`/`enemy_id`/`script_id`) 앞에 `#`을 붙이면 그 행은 주석이 되어 봇이 읽지 않습니다(자동완성에도 안 나옴). 보스전을 세션 당일까지 숨겨 두고 연습 전투만 열어 두는 용도입니다. 테스트와 `sim.py`는 숨긴 행도 읽어서 검증합니다(`--visible-only`로 제외).
- `overrides`: `key=value;key=value` 로 그 전투에서만 설정을 덮어씁니다(3.6의 키). **목록 값(`firewall_options`, `modes`, `first_strike_cards`, `weapon_bonus`)은 `;` 대신 `,`로 적습니다.** 인원수별 난이도는 여기서 조정합니다.

### 3.4 Tutorial.csv — 튜토리얼 대본

```
script_id,round,coach,enemy_script,guaranteed,setup,goal,success,fail
tut1,3,"해킹은 공격보다 먼저…",scav_pipe:e_training;scav_gun:e_training,hack_stun,,card:hack_stun,"적이 기절해서…","시스템 리셋을 쓴 사람이…"
tut1,4,"쇠파이프 스캐빈저가 기절해…",scav_gun:e_training,atk_slash,enemy_mod:scav_pipe:stun,combo,"⚡콤보!…","콤보가 나오지…"
```

- `round`는 1부터 빈틈없이. 마지막 라운드가 끝나면 적이 남아 있어도 전투가 끝납니다(`tutorial`). 그 전에는 적 HP가 1 아래로 내려가지 않습니다.
- `enemy_script`: `enemy_id:card_id;…` (적 id 접두 일치. 적히지 않은 적은 AI). `guaranteed`: 모든 플레이어의 패에 반드시 넣을 카드. `setup`: 라운드가 열리기 전에 `enemy_hp:<id>:<hp>` / `enemy_mod:<id>:<수정자>` / `firewall:<n>`.
- `goal`: `all_submitted` / `kind:<attack|defend|hack|special>` / `card:<card_id>` / `combo` / `firewall_used` / `finish_nonlethal`. 과제 결과에 따라 `success` 또는 `fail` 문구가 보고에 붙습니다. 실패해도 진행됩니다.
- 시트 탭 이름은 `Card_Tutorial`. 파일이 없어도 다른 전투는 시작됩니다.

### 3.5 검증과 흔한 오류

시작 시 데이터 전체를 검사하고, 문제가 있으면 주최자에게만 오류를 보여주고 시작하지 않습니다.

- 알 수 없는 `kind` / `target` / `stat` / `pool` / 효과 / 태그 / 특성 / 기믹 / 설정 키
- `effects`가 비어 있음, 숫자 자리에 문자, `combo` 형식 오류
- `Battles.csv`의 `enemies`에 없는 `enemy_id`, 적 0명(`g*0`)
- `card_id` / `enemy_id` / `battle_id` 중복, 한쪽이 뽑을 카드가 하나도 없음
- 기믹에 필요한 고정 카드·특성 적·대본이 없음, `modes`/`doll_modes`/`first_strike_cards`에 없는 카드
- `overrides` 안에서 목록 값을 `;`로 적음 (→ `key=value` 형식 오류)

### 3.6 구글 시트로 옮기기

`.env`에 `CARDBATTLE_SOURCE=sheet`를 쓰면 봇 스프레드시트의 탭 `Card_List`, `Card_Enemies`, `Card_Battles`, `Card_Tutorial`을 읽습니다. 열 이름은 CSV와 **완전히 같게** 두세요. 탭이 없거나 읽기에 실패한 표는 경고 로그를 남기고 그 표만 CSV로 되돌아갑니다. 봇은 이 탭에 절대 쓰지 않습니다.

### 3.7 밸런스 숫자 (`cardbattle/config.py` `DEFAULTS`)

| 키 | 기본값 | 뜻 |
|---|---|---|
| `hand_size` / `damage_variance` / `stat_divisor` | 3 / 4 / 2 | 패 장 수 / 피해 난수 상한 / 능력치 보정 나눗수 |
| `ai_noise` | 0.15 | 적 AI 점수에 섞는 난수 폭 |
| `max_players` | 8 | 참가 정원 |
| `report_delay_sec` | 1.0 | 정산 보고 후 다음 라운드 메시지까지 대기 |
| `use_current_hp` / `allow_unregistered` / `default_hp` / `default_stat` | False / False / 30 / 10 | 시트 HP 사용 / 미등록 참가 허용 / 그때의 기본값 |
| `admin_sees_all` | False | True면 주최자가 항상 전체 카드 선택 (기본은 `/카드 옵션:관리자`로 사람별) |
| `enemy_hp_mult` | 1.0 | 적 HP 배율 (전투 `overrides`로 인원수 보정) |
| `show_intent_target` | False | True면 적 의도의 대상까지 항상 공개 |
| `render_mode` | image | `image` / `text` (현황판 그림) |
| `tips` | True | 💡 문맥 팁 |
| `overkill_margin` | 15 | 과잉 제압 판정 초과량 (⚠ 표시 기준과 같음) |
| `ncpd_max` / `ncpd_overkill` / `ncpd_loud` | 3 / 1 / 0.5 | 경계도 상한 / 과잉 제압당 / 소음당 |
| `escape_turns` / `escape_wins_needed` / `escape_hint` | 5 / 3 / True | 도주 턴 수 / 필요한 승리 턴(= 시작 경계도) / 보느와 힌트 |
| `drag_turns` | 2 | 약탈까지 라운드 수 |
| `hack_weak_mult` | 1.5 | 싼 의체 해킹 배율 |
| `chip_stability` / `chip_per_hack` / `chip_round_cap` / `chip_reboot_mult` / `chip_stun_bonus` | 5 / 1 / 2 / 2 / 1 | 칩 트랙 |
| `screen_durability` | 3 | 액정 내구도 |
| `doll_modes` | `cling:doll_cling:4;rampage:doll_claws:4;reboot:doll_reboot:2` | 돌의 모드:카드:가중치 |
| `modes` / `modes_norepeat` | (빈) / `reboot` | `modes` 특성 적의 모드 표 (비면 `doll_modes`) / 연속 금지 모드 |
| `first_strike_cards` | `bw_ambush` | 선제 라운드 카드 |
| `daemon_damage` / `daemon_turns` / `daemon_max_infected` | 5 / 2 / 4 | 데몬 자해(스택당) / 지속 / 동시 감염 상한 |
| `ice_layers` / `ice_reduction` / `ice_round_cap` | 3 / 0.3 / 2 | ICE 층 수 / 층당 감소 / 라운드당 벗기는 상한 |
| `life_support_rounds` | 8 | 생명 유지 시계 |
| `firewall_per_damage` / `_per_defend` / `_per_down` / `firewall_max` | 10 / 1 / 2 / 9 | 게이지 충전 |
| `firewall_options` | `cover;reboot;maxdoc` | 열린 지원 (전투 `overrides`에서는 `,`로) |
| `firewall_cost_cover` / `_reboot` / `_aim` / `_scan` / `_maxdoc` | 3 / 2 / 2 / 1 / 4 | 지원 비용 |
| `firewall_cover_shield` / `firewall_maxdoc_ratio` | 0.5 / 0.3 | 팀 엄폐 감소율 / 맥스닥 회복 비율 |
| `prep_enabled` / `prep_actions` / `prep_sandbox` | True / 2 / False | 준비 단계 / 1인 개조 횟수 / 임시 금고(보수 없음) |
| `price_normal` / `price_black` / `pot_start_per_player` | 100 / 50 / 100 | 정식·뒷골목 개조 가격 / 금고 파일이 없을 때 1인 시작 에디 |
| `weapon_bonus` / `weapon_start_max` / `weapon_normal_max` / `weapon_max` | `0;3;6;10` / 2 / 3 / 4 | 등급별 피해 보너스 / 주운 무기 시작 상한 / 정식 개조 상한 / 최고 등급 |
| `gamble_fail_max` / `gamble_crit_refund` / `gamble_fumble_drop` | 3 / True / 2 | d10 ≤ 이 값 실패 / 10 환불 / 1 의 하락 폭 |
| `field_mod_cost` | 50 | 현장 개조 비용 |
| `reward_per_player` / `penalty_overkill` / `penalty_loud` / `reward_fail_ratio` / `reward_collapse_ratio` | 150 / 50 / 20 / 0 / 0.5 | 보수 계산 |
| `tutorial_script` | `tut1` | Tutorial.csv 의 script_id |

바꾸기 전에 시뮬레이션으로 확인하세요(4.5절).

---

## 4. 개발자 안내

### 4.1 파일 구조

```
cardbattle/
  engine.py        순수 규칙 엔진 (Discord 모름). 데이터클래스, 무기·준비 단계 초기화, 드로우, 제출, 라운드 열기/정산, 선제 라운드,
                   기믹, 방화벽, 💡 팁 탐지, 도주 단계, 직렬화
  gig.py           긱 경제: 금고 파일(data/cardbattle_gig.json), 준비 단계 개조·준비 완료·마감, 보수 계산·지급 (엔진 import)
  tutorial.py      Tutorial.csv 대본: 파싱·검증, 대본 chooser, 보장 카드, setup, 과제 평가 (엔진 import)
  data.py          CSV/시트 로더 + 검증 (tags, combo, traits, gimmicks, overrides, modes, first_strike, tutorial 포함)
  ai.py            적 AI 점수 함수와 성향 표 (피해 20 ≈ 1점 눈금)
  config.py        DEFAULTS, coerce, build_config, parse_overrides
  render.py        상태 → 문장 (로그 줄, 이벤트 줄, 팁, 의도, 게이지, 콤보 설명, 소개 규칙, 준비 단계, 보수, 도주 보고). Discord 모름
  embeds.py        render 의 문장을 discord.Embed 로 배치. 공개 메시지는 전부 여기서 (+ 준비 단계·코치·규칙 섹션)
  image_render.py  현황판 PNG (fugitive/image_render 의 글꼴·색 유틸 재사용). 없으면 None
  strings.py       유저 노출 문자열 전부 (표시명 매핑, HELP / HELP_SECTIONS, INTRO_RULES, TIPS, WEAPON_NAMES, 보느와 문구)
  store.py         data/cardbattle_state.json 원자적 저장/복원
  views.py         TurnView(카드 뽑기·방화벽), PrepView(개조·준비 완료), EscapeView(도주 버튼 3개), HandView, TargetView(⚠), ChosenView, FirewallView
  cog.py           CardBattleCog: /카드 명령(주최·참가·시작·턴종료·종료·현황·금고·규칙), 상태기계, 락, 메시지 편집, 재시작 복원, 보수 지급
  sim.py           몬테카를로 시뮬레이션 (실제 직업 템플릿, greedy / attack_only / random, 준비 단계 정책 포함)
data/cardbattle/*.csv          전투 데이터 (git 추적) — Cards / Enemies / Battles / Tutorial
data/cardbattle_state.json     진행 중 세션 (git 무시)
data/cardbattle_gig.json       팀 금고 (git 무시)
tests/test_cardbattle_{engine,ai,cog,gig,boss,cog2}.py
```

`local_server/`는 손으로 복사하는 미러입니다. 패키지와 CSV를 고치면 거기에도 복사하세요.

### 4.2 상태와 라운드

`BattleState.status`: `LOBBY → PREP → ROUND_OPEN → RESOLVING → ROUND_OPEN … → ENDED`, 경계도가 차면 `→ ESCAPE → ENDED`. `prep_enabled=false`면 `PREP`을 건너뜁니다. `first_strike`면 `PREP` 뒤(또는 `new_battle` 안)에서 `resolve_ambush`가 0라운드를 치르고 그 보고를 `pending_reports`에 넣은 뒤 1라운드를 엽니다.
`round`는 1부터 시작하는 라운드 번호(선제 라운드는 0)입니다. `winner`는 `ally` / `enemy` / `escaped` / `caught`, `end_reason`은 `ko` / `chip` / `collapse` / `tutorial` / `wipe` / `escaped` / `caught` / `caught_down`.

저장되는 상태는 `LOBBY`, `PREP`, `ROUND_OPEN`, `ESCAPE`, `ENDED`입니다. 정산 보고를 게시하기 전에 죽었다면 `cog_load → _resume()`이 `pending_reports`를 올리고 턴/도주/준비 메시지를 다시 엽니다(`turn_message_id` / `prep_message_id` 가 0이면 새로 연다). `ENDED`인데 `meters["payout"]`이 없으면 복원 때 `gig.apply_payout`을 호출합니다(멱등).

무기는 `Combatant.weapon_grade` / `weapon_name` 에만 있고 `prep` dict 가 분배금·행동 수·준비 완료·굴림 기록을 가집니다. 금고의 작업본은 `meters["pot"]`(현장 개조 재원), 지속 상태는 `data/cardbattle_gig.json` 뿐입니다.

### 4.3 라운드 규칙 (engine.open_round / resolve_round)

1. `open_round`: (튜토리얼이면 대본 `setup` 적용 후) 생존한 적마다 패를 뽑아 chooser(`ai.choose`, 튜토리얼이면 `tutorial.chooser`)로 선택을 정하고 `enemy_choices`에 둡니다. `intents[eid] = {kind, label, target, card_id}`. `public_intents()`는 `show_intent_target` 또는 이번 라운드 `scan` 플래그가 있을 때만 대상을 넣습니다. `doll` 특성(돌) 또는 `modes` 특성 적은 `_pick_mode`가 `doll_modes` / `modes` 표에서 모드를 뽑아(`modes_norepeat` 연속 금지) 단말기 소지자(없으면 무작위 아군)를 노립니다. 모드 키는 `meters["doll_mode"]`(이름은 돌 때문이고 보느와도 씁니다), 주인은 `meters["mode_owner"]`.
2. `resolve_round`: 양편 생존자를 **단계(해킹 0 → 방어 1 → 공격·특수 2) → 민첩 내림차순 → 난수**로 정렬합니다. 이미 기절한 유닛은 맨 앞에서 `stunned` 줄을 남기고, 정산 중에 기절당한 유닛은 자기 차례에 같은 처리를 받습니다. **기절 수정자는 라운드 끝에 풀립니다**(그래서 라운드 내내 콤보 조건). 선택이 없는 유닛은 맨 뒤에 `wait`(정산 중 쓰러졌으면 생략). 리부트 모드의 돌은 `reboot`.
3. 카드마다 `_apply_card`: 대상 확정(죽었으면 생존자 중 무작위, `redirect`) → `misfire`/`redirect_random` 소모 → `evade` 판정(물리 단일, 스마트 탄 유도 중엔 생략) → `atk_down` 소모(카드당 1회) → 콤보 판정 → 효과 핸들러 → (아군 해킹이 적에게 맞았으면) `_ice_strip`. `damage`는 `값 + 보정 + 무기 + randint(0, variance)`(유도 중 난수 0)에 콤보, 공격력 감소, 방어, ICE 배율을 곱하고 내림. 비살상 전투의 과잉 제압, `meters["last_ko"]`(보느와 상태용), 액정, 방화벽 충전, 돌 붙잡힘 해제도 여기서 처리합니다. 튜토리얼의 마지막 라운드 전에는 적 HP 바닥이 1 입니다. `loud` 태그는 아군이 쓸 때만 경계도를 올립니다. 카드가 끝나면 `_daemon_bite`: 감염된 채 행동한 유닛(걸린 라운드 제외)에게 스택당 `daemon_damage` 자해.
4. 라운드 끝: `tick_modifiers`(`shield`/`daemon`만 1 감소, 만든 라운드 포함), `_regen`, `_advance_drag`, `_tick_life`(생명 유지 −1).
5. 종료 판정 순서: 칩 0(`chip`) → 경계도 가득(`ncpd` → `start_escape`) → 적 전멸(`ko`) → 아군 전멸(`wipe`) → 생명 유지 0(`collapse`, 아군 승) → 튜토리얼 마지막 라운드(`tutorial`, 아군 승). `life_support` 전투의 아군 승리는 `meters["boss_state"]`(fine/hurt/critical)를 남깁니다. 그 뒤 `_collect_tips`(처음 일어난 일 키 → `report["tips"]`, `meters["tips_seen"]`)와 튜토리얼 `evaluate`(→ `report["tutorial"]`). 아니면 `round += 1` 후 바로 `open_round`.

보고(`report`)는 `{"round_no","lines","events","hp","meters","winner","reason","tips"[, "tutorial", "ambush"]}`. `lines[*]`는 `actor_id/actor_name/side/card_id/card_name/kind/result/targets` 에 선택적으로 `combo`, `chip`, `ice`, `daemon`(자해), `field_mod`, `released`, `redirected`, `atk_down`. `result`는 `hit | heal | shield | hack | redirect | misfire | evade | no_target | field_mod | field_mod_none | wait | stunned | down | reboot | break_free | pass | noop`. `targets[*]`에는 `overkill`, `weak`, `revived`, `screen`, `turns`, `shielded`, `iced`가 붙을 수 있습니다. `events[*].kind`는 `firewall_use | firewall_gain | firewall_drain | firewall_lock | ncpd | drag_start | drag | drag_stop | looted | regen | ice | expose | life | field_mod`. 렌더는 이 dict만 보고 문장을 만듭니다.

선제 라운드(`resolve_ambush`)는 `resolve_round`를 거치지 않습니다: ROUND_OPEN 이 아니어도 되고, 대기 줄·틱·종료 판정·시계 감소가 없으며, `round_no 0`, `ambush: True` 보고를 `st.log`와 `st.pending_reports`에 넣습니다. 0라운드에 걸린 데몬은 틱이 없으므로 지속을 1 줄여 넣습니다.

도주 단계: `start_escape`가 `escape = {turn, max_turns, alert, alert_max, choices, history, police, hint}`를 만들고, `_escape_prepare_turn`이 경찰 행동을 미리 정한 뒤 `escape_hint`면 다른 행동 하나를 `hint`로 공개합니다. `safe_escape_action(hint)`는 그 턴에 지지 않는 행동입니다. `resolve_escape`는 `wins*2 > n`이면 `alert -= 1`, 0이면 `ESCAPED`, `turn >= max_turns`면 `CAUGHT`.

### 4.4 Discord 층에서 지킬 것

- 상태 변경은 전부 `self.lock` 안에서, 끝나면 `_save()`.
- `TurnView`(카드 뽑기·방화벽), `PrepView`(개조·준비 완료), `EscapeView`(버튼 3개)는 `timeout=None` + 고정 `custom_id`. 상태를 갖지 않으므로 `cog_load`의 `add_view` 세 번으로 모든 메시지 버튼이 복원됩니다. `_turn_guard`가 `interaction.message.id == st.turn_message_id`로, `handle_prep`이 `prep_message_id`로 옛 메시지를 거절합니다.
- 라운드 메시지는 `embed + file(board.png) + view`로 보내고 임베드의 `set_image("attachment://board.png")`로 그림을 안에 넣습니다. 진행률을 갱신할 때는 `msg.edit(embed=...)`만 호출해야 첨부와 버튼이 남습니다. `_image_mode()`가 `render_mode`와 Pillow/글꼴 가용성을 검사하고, 못 쓰면 한 번만 경고 로그를 남깁니다.
- 전투가 열릴 때(`_begin_messages`)는 `pending_reports`(선제 라운드 보고)를 먼저 올리고, 튜토리얼이면 코치 임베드, 그다음 턴 메시지입니다. 끝나면(`_after_round`) `gig.apply_payout` → 결과 임베드.
- ephemeral 패 메시지는 **그 메시지의 컴포넌트 상호작용**에서만 `response.edit_message`로 고칠 수 있습니다. 버튼 클릭(공개 메시지)에서는 반드시 `response.send_message(ephemeral=True)`.
- Select는 옵션 25개·라벨 100자 제한. 주최자 전체 카드는 24개 + "다음 ▶" 옵션으로 페이징. 방화벽 메뉴는 `enabled_firewall_options`만.
- `/카드 옵션:규칙`은 섹션 임베드를 `embeds.chunk_embeds`로 묶어(메시지당 10개·6000자) 첫 묶음은 response, 나머지는 followup 으로 보냅니다.
- `/카드`는 `tests/test_help_option.py` 규칙을 따릅니다: 클래스 속성의 단일 `app_commands.Command`, 필수 옵션 없음(`금액`도 선택), 마지막 옵션 `도움말`. `S.HELP`는 옵션 목록과 합쳐 2000자 안에 들어야 합니다.
- 캐릭터 시트 열 매핑은 `engine.player_combatant` 한 곳뿐입니다(`max_hp current_hp physics magic agility charm job color`). 무기 이름 표는 `strings.WEAPON_NAMES`(시트 직업명 키)이고 cog/sim 이 `new_battle(weapon_names=)`로 넘깁니다. 엔진은 strings 를 import 하지 않습니다.

### 4.5 테스트와 시뮬레이션

```
PYTHONIOENCODING=utf-8 py -m pytest tests -q -k cardbattle
PYTHONIOENCODING=utf-8 py -m cardbattle.sim --all --games 200
PYTHONIOENCODING=utf-8 py -m cardbattle.sim --battle bonewa4 --players 4 --games 500 --seed 1
PYTHONIOENCODING=utf-8 py -m cardbattle.sim --battle scav5 --set ncpd_max=4 --policy greedy
PYTHONIOENCODING=utf-8 py -m cardbattle.sim --battle doll4 --set prep_enabled=false     # 무기 없이
```

- 엔진 테스트는 시드 고정 `random.Random`과 `damage_variance=0`, `stat_divisor=1000`, `prep_enabled=False`, 방화벽 옵션 전부로 숫자를 결정적으로 만듭니다(`flat_cfg()`, `mk_cards()`, `always(card_id)`, `play()` 도우미). 굴림이 필요한 테스트는 `SeqRng`(randint 순서 지정)를 씁니다. `test_cardbattle_gig.py`(무기·준비·현장 개조·보수·⚠·방화벽 옵션·기절 콤보), `test_cardbattle_boss.py`(데몬·ICE·방화벽 공방·생명 유지·기습·모드·팁·튜토리얼).
- cog 테스트는 `FakeChannel`/`FakeMessage`/`FakeInteraction`으로 명령과 버튼 흐름 전체(주최 → 참가 → 시작 → 준비 단계 버튼 → 뽑기 → 선택 → 정산 → 다음 라운드, 방화벽, 도주 단계, 강제 종료, 재시작 복원, 금고·규칙 명령, 보느와 기습, 튜토리얼 코치, ⚠ 대상 라벨)를 돌립니다. 하네스는 기본으로 `prep_enabled=False`를 넣고 `gig_path`를 임시 파일로 바꿉니다.
- sim의 플레이어 봇은 실제 직업 템플릿(엣지러너 HP 110 근력 16 / 넷러너 HP 80 RAM 16 / 솔져 HP 110 16·14)을 돌려 쓰고, 정책 세 개를 **항상 나란히** 출력합니다. 준비 단계도 정책대로(greedy 정식 개조, attack_only 뒷골목, random 무작위) 치르고 금고는 판마다 새로 시작합니다. `attack_only`가 `greedy`와 승률이 같거나 높으면 공격 외 카드가 함정이라는 신호입니다. 출력은 승률, 결과 분포(`ko` / `chip` / `collapse` / `tutorial` / `escaped` / `caught` / `wipe`), 라운드 p25/p50/p75, 사망·약탈·과잉 제압 횟수, 평균 무기 등급, 보수, 보느와 상태 분포, 많이 쓴 카드입니다. 사람은 봇보다 잘 하므로 하한선으로 봅니다.
- 기본 인원은 `Battles.csv`의 `players` 열이고, 전투의 `overrides`가 자동 적용됩니다(`--set`이 그 위를 덮습니다).

### 4.6 확장 포인트

| 하고 싶은 것 | 손댈 곳 |
|---|---|
| 새 카드 / 콤보 / 태그 조합 | `Cards.csv` 한 줄 (코드 수정 없음) |
| 튜토리얼 수업 바꾸기 | `Tutorial.csv` (코치 문구·보장 카드·setup·goal) |
| 전투 중 문구를 보느와 말투로 | `strings.py`의 `INTRO_RULES`, `TIPS`, `MODE_DESC`, `WIN_ALLY_BOSS`, `COACH_*`, `HELP_SECTIONS` |
| 새 효과 종류 | `engine.EFFECT_KINDS`에 추가, `HANDLERS`에 핸들러, 지속형이면 `MODIFIER_KINDS`/`TICKING`/`ONE_SHOT`(대상 수를 받으면 `COUNT_KINDS`), 로그 문구는 `strings.DETAIL`/`TAG`, 그림은 `image_render.TAG_KO` |
| 새 특성 | `engine.TRAITS`에 추가하고 쓰는 곳(`_apply_card`/`_h_modifier`/`_regen`)에 분기 |
| 새 기믹 | `engine.GIMMICKS`에 추가, `new_battle`에서 게이지 초기화, `resolve_round` 종료 판정, `render.meters_text`, `strings.INTRO_RULES` 규칙 줄, 필요하면 `_collect_tips` 탐지기 + `strings.TIPS` |
| 새 방화벽 지원 | `engine.FIREWALL_OPTIONS` + `use_firewall` 분기 + `config` 비용 키 + `strings.FIREWALL_OPTION_*` (전투별 공개는 `firewall_options`) |
| 새 보스 모드 | `engine.MODES`에 이름, `Cards.csv`에 `pool=fixed` 카드, 전투 `overrides`의 `modes`, `strings.MODE_KO/MODE_DESC` |
| 새 AI 성향 | `ai.PERSONALITIES`에 가중치 한 줄 |
| 턴 제한 시간 | fugitive의 `_arm_timer`/`_cancel_timer` 패턴을 `cog.py`에 옮기고 만료 시 `_resolve_round(force=True)` |
