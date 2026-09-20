# mogindex 검색 엔진 구조

디스코드 커뮤 로그 검색 기능(mogindex)의 색인·저장·질의 구조 정리.
2026-07 Kiwi 형태소 분석기 전환 작업 기준이며, 비교를 위해 구버전 기법도 함께 기록한다.

## 0. 한눈에 보는 구조

```
[디스코드 메시지]
     │  ① 수집 (on_message 실시간 + 매일 23:59 일일 수집 + /전체색인)
     ▼
[messages 테이블]  ←  원문(content)과 문맥(search_context)만 저장. 토큰화 안 함.
     │  ② 토큰화 (별도 프로세스: 서버는 20분 배치, PC는 백필)
     ▼
[message_terms / daily_terms]  ←  Kiwi 명사 색인
[message_fts]                  ←  트라이그램 FTS (수집 시점에 별도 생성)
[df_stopwords]                 ←  백필 때 문서빈도(DF)로 산출
     │  ③ 질의 (/검색, /고오급검색 — Kiwi 없이 동작)
     ▼
[정확 일치 → 접두 일치 → 부분 문자열(FTS)] 3단 매칭
```

핵심 설계 제약: **운영 서버는 RAM 1GB**(오라클 VM)인데 Kiwi는 상주 ~470MB다.
그래서 봇 프로세스는 kiwipiepy를 절대 import하지 않고, 토큰화는 단명
서브프로세스(배치)나 개발 PC(백필)에서만 수행하는 **2단계 아키텍처**를 쓴다.

## 1. 저장 구조 (SQLite)

파일 하나(`search_index_live_debug.sqlite3`), WAL 모드, `busy_timeout=30000`.

| 테이블 | 내용 | 만들어지는 시점 |
|---|---|---|
| `sources` | 채널/스레드 메타 (이름, 카테고리, 길드) | 수집 시 upsert |
| `messages` | 메시지 본체. `content`(원문), `search_context`(채널·스레드명), `tokenized_at`(토큰화 도장), `tokenizer_version` | 수집 시 |
| `message_fts` | FTS5 가상 테이블, `tokenize='trigram'`, contentless. 정규화된 원문 저장 | 수집 시 (신규 행만) |
| `message_terms` | 메시지 단위 용어 색인 `(term, message_pk, source_id, message_date, count)` | 토큰화 시 |
| `daily_terms` | 일자·소스 단위 용어 집계 `(source_id, message_date, term, count)` — ON CONFLICT로 누적 | 토큰화 시 |
| `df_stopwords` | 소스별 말뭉치 불용어 `(source_id, term, df_ratio)` | 백필 시 |
| `mogindex_meta` | key-value 메타 (`tokenizer_version`, `backfill_state`, `last_batch_run` 등) | 수시 |

`message_terms`와 `daily_terms`의 비대칭은 의도된 설계다:
- `message_terms` = 원문 + search_context 용어 → 개별 메시지 검색에서 채널명도 맥락으로 잡힘
- `daily_terms` = 원문 용어만 → 날짜 요약/복귀자 키워드가 채널명에 오염되지 않음

주요 인덱스:
- `idx_message_terms_lookup (term, message_date, source_id)` — 질의용
- `idx_message_terms_message (message_pk)` — 재토큰화 시 메시지 단위 DELETE용.
  PK가 `(term, message_pk)`라 이 인덱스가 없으면 메시지당 전체 풀스캔이 된다
  (실제로 1,770만 행 레거시 색인에서 백필이 사실상 정지하는 사고로 확인, 2026-07-10)
- `idx_messages_pending_tokenize (tokenized_at) WHERE tokenized_at IS NULL` —
  부분 인덱스. "토큰화 대기 행" 스캔이 대기 건수에만 비례

## 2. 색인 기법의 변천

### 구버전 (휴리스틱, ~2026-07)

토큰화가 **수집과 같은 프로세스**에서 즉시 일어났다:

1. `normalize_text` — 소문자화, URL/멘션 제거
2. `TOKEN_RE` 정규식으로 토큰 분리 (한글/영숫자 연속열)
3. 조사 제거 — `PARTICLE_SUFFIXES` 목록(은/는/이/가/을/를/에서/에게…)을 꼬리에서 잘라냄
4. **문자 n-gram (2~5그램)** — 부분 문자열 검색을 위해 각 토큰의 조각을 전부 색인
5. **정적 불용어 737개** — 수작업 목록으로 필터

문제점:
- n-gram이 색인을 폭발시킴 (메시지 51만 건 → `message_terms` 1,770만 행)
- 조사 제거 휴리스틱이 불완전해 "거기서", "했는데" 같은 문법 조각이 색인에 유입
- 정적 불용어는 서버 은어/커뮤 특성을 못 따라감

### 신버전 (Kiwi 형태소 분석, v1)

토큰화를 **수집에서 분리**하고 형태소 분석기로 교체:

1. 수집은 원문만 저장 (`tokenized_at IS NULL`로 대기 표시)
2. 별도 프로세스가 Kiwi(kiwipiepy 0.21.0, `model_type='knlm'` — 로드 ~1초)로 분석
3. 분석 전 전처리는 디스코드 잡음 제거만: 메시지 링크, `<@…>` 멘션, URL을 공백 치환.
   (소문자화·문장부호 제거는 하지 않음 — Kiwi가 문장 구조를 쓰기 때문)
4. **품사(POS) 필터**로 색인 대상 선정:
   - `NNG`(일반명사), `NNP`(고유명사) — 원형 그대로
   - `SL`(라틴 문자열) — 2자 이상만, 소문자화
   - 동사/형용사(VV/VA), 수사, 조사, 어미 등은 v1에서 색인하지 않음
5. **사용자 사전** (`mogindex_userdict.txt`, 탭 구분 `단어\tNNP`) — 캐릭터 이름,
   서버 은어 등 Kiwi 기본 사전에 없는 고유명사 등록. DB와 한 몸으로 취급
   (사전 바꾸면 전체 재색인 필요)
6. **n-gram 폐지** — 부분 문자열 검색은 `message_fts`(트라이그램)가 전담.
   결과: 같은 51만 건이 `message_terms` 약 350만 행 수준으로 줄고 잡음 조각이 사라짐
7. `tokenizer_version`(`kiwi-0.21.0-knlm-v1`)을 메시지마다 도장 — 버전이 바뀌면
   구버전 색인을 감지해 전체 재구축

### Kiwi의 동작 원리

Kiwi(Korean Intelligent Word Identifier)는 오픈소스 한국어 **형태소 분석기**다
(C++ 코어, 파이썬 바인딩이 kiwipiepy). 왜 정규식이 아니라 형태소 분석이 필요한가:
한국어는 교착어라서 어절이 `어간+조사/어미`로 붙어 다니고, 띄어쓰기가 의미 단위를
보장하지 않는다. "카페테리아에서"를 검색 가능한 "카페테리아"로 만들려면 어절을
형태소로 **분해**하고 각각에 **품사를 판정**해야 한다.

분석은 3단계로 일어난다:

1. **후보 생성 (사전 매칭)** — 문장의 각 위치에서 내장 사전(+사용자 사전)에 있는
   모든 형태소 후보를 나열해 격자(lattice)를 만든다. 같은 구간도 여러 분할이 가능하다:
   `존이` → [존/NNP + 이/JKS] vs [존이/NNG?] vs [조/NNG + 니/EF] …
2. **경로 점수화 (통계 언어 모델)** — 각 분할 경로에 "이 형태소 열이 실제 한국어에서
   나올 확률"을 매긴다. 우리가 쓰는 `knlm`은 형태소 단위 n-gram 언어 모델
   (Kneser-Ney 스무딩)이다. 조사 제거를 목록 매칭이 아니라 **확률 문제**로 푸는 것 —
   "존이 왔다"에서 `존/NNP + 이/JKS`가 이기는 이유는 그 열의 확률이 가장 높기 때문이다.
3. **최적 경로 탐색** — 동적 계획법(Viterbi)으로 격자에서 최고 확률 경로 하나를 고른다.
   결과가 `(형태소 원형, 품사 태그, 위치)` 목록이고, 우리는 여기서 NNG/NNP/SL만 취한다.

이 구조에서 나오는 성질들이 우리 설계 결정의 근거다:

- **활용형의 원형 복원**: "재밌었어" → `재밌/VA + 었/EP + 어/EF`. 동사·형용사는
  어간까지 복원되지만 v1에서는 색인하지 않으므로, '근데 그거 진짜 재밌었어'는
  색인 용어 0개가 된다(명사가 없으니까). 구버전이라면 "재밌었어"의 n-gram 조각들이
  전부 색인에 들어갔을 것이다.
- **미등록어(OOV)의 불안정성**: 사전에 없는 단어는 통계로 추측하는데, 캐릭터 이름
  같은 창작 고유명사는 조각나기 쉽다 ("게쉬틴안나" → 게/쉬/틴/안나 식으로 분해될
  수 있음). **사용자 사전에 `게쉬틴안나\tNNP`를 넣으면 1단계 격자에 온전한 후보가
  생기고 경로 탐색이 그걸 선택한다.** 사전이 바뀌면 같은 문장의 분석 결과가 바뀌므로,
  사전을 DB와 한 몸으로 취급하고(서버에 함께 배포) 사전 변경 시 전체 재색인하는 이유다.
- **결정론적 출력**: 같은 텍스트 + 같은 모델 + 같은 사전 = 항상 같은 결과.
  그래서 `tokenizer_version` 도장 하나로 "이 행의 색인이 지금 규칙과 같은가"를
  판정할 수 있다 (증분 배치와 백필이 안전하게 이어받는 근거).
- **모델 선택 `knlm` vs `cong`**: kiwipiepy 0.21의 최신 모델(CoNg)은 문맥을 더 넓게
  보는 대신 로드가 ~12초, knlm은 ~1초다. 20분마다 새로 뜨는 단명 서브프로세스 구조라
  로드 시간이 반복 비용이므로 knlm으로 고정했다. 정확도 차이는 명사 추출 용도에선
  체감이 작다.

### 불용어: 정적 목록 → 문서빈도(DF) 기반

백필이 색인 완료 후 소스(채널)별로 산출한다:

```
df_ratio = (소스에서 그 용어를 포함한 원문 메시지 수) / (소스의 토큰화된 원문 메시지 수)
```

- `df_ratio > 25%` (`MOGINDEX_DF_CUTOFF_PCT`) → 그 소스의 불용어로 등록
- 원문 메시지 100건 미만 소스는 제외 (`MOGINDEX_DF_MIN_DOCS` — 작은 소스는 전부 불용어가 돼버림)
- 분자·분모 모두 "원문 있는 메시지" 기준 (search_context 전용 행을 세면 비율이 1.0을 넘는 버그가 있었음)

날짜 요약·복귀자 키워드 질의에서 `NOT EXISTS (… df_stopwords …)`로 걸러진다.
채널마다 불용어가 다르다는 게 요점 — 전투 채널의 "공격"은 불용어지만 잡담 채널에선 아니다.

## 3. 질의 경로 (봇 프로세스 — Kiwi 없이)

검색 질의는 가벼운 휴리스틱만 쓴다 (`derive_query_terms`):
`normalize_text` → `TOKEN_RE` 분리 → 조사/어미 꼬리 제거 → 숫자만인 토큰 제거 → 중복 제거

매칭은 3단:

1. **정확 일치** — `term IN (…)` (`message_terms`)
2. **접두 일치** — 정확 일치가 없었던 토큰(2자 이상)만
   `term >= ? AND term < ? || chr(0xFFFF)` 범위 스캔
3. **부분 문자열** — `message_fts MATCH` (트라이그램)

알려진 한계(의도): 활용이 심한 동사 질의("달렸었는데")는 1·2단을 빗나가고 3단이 받는다.
명사 질의가 주 사용 패턴이라는 전제.

## 4. 토큰화 파이프라인 (어떻게 SQL 파일에 채워지는가)

### 공용 엔진 — `mogindex_kiwi.index_pending_messages()`

배치와 백필이 같은 코드를 쓴다. 청크(배치 500 / 백필 5000건) 단위로:

1. `WHERE tokenized_at IS NULL` 행을 LIMIT으로 가져옴
2. 메시지마다: 기존 용어 DELETE(재토큰화 안전장치) → Kiwi 분석 →
   `message_terms` INSERT + `daily_terms` ON CONFLICT 누적 → `tokenized_at` 도장
3. **청크 전체를 한 트랜잭션으로 커밋** — 용어 기록과 도장이 같은 트랜잭션이므로
   중간에 죽어도 롤백되고, `daily_terms` 누적이 정확히 1회만 반영된다 (exactly-once)
4. 원문이 없는 행(`content IS NULL`)은 용어 없이 도장만 — 영원히 재스캔되지 않게

### 서버 증분 배치 — `mogindex_batch.py` (20분마다)

봇의 `tasks.loop`가 서브프로세스로 스폰. 전투 중/전체색인 중/이전 배치 실행 중이면 스킵.

1. OOM 실드: `/proc/self/oom_score_adj=1000` — 메모리가 바닥나면 커널이 봇 대신 배치를 죽임
2. 단일 인스턴스 락: `<db>.batchlock` 파일에 `BEGIN IMMEDIATE`(busy_timeout=0) — 중복 실행 즉시 종료
3. **빠른 no-op**: 대기 0건이면 kiwipiepy를 import하지 않고 종료 (470MB를 지불하지 않음)
4. 대기분 있으면 Kiwi 로드 → 공용 엔진 실행 → 메타 갱신

### PC 전체 백필 — `mogindex_backfill.py` (`/재색인 백필시작`)

1. 배치와 같은 락 획득 (동시 실행 시 daily_terms 이중 집계 방지)
2. `PRAGMA foreign_keys = OFF` — FK가 켜져 있으면 아래 와이프의 `DELETE FROM`이
   truncate 최적화를 못 받아 수천만 행을 한 행씩 지운다 (몇 초 vs 수십 분)
3. **와이프 판단** (데이터 기준, 메타 신뢰 안 함): ① 다른 tokenizer_version 도장이 있거나
   ② 도장 없이 용어만 있는 행(구버전 색인 잔재)이 있으면 → `message_terms`/`daily_terms`
   전체 삭제 + 도장 리셋. 현재 버전 작업만 있으면 와이프 없이 이어받음
4. 공용 엔진으로 색인 (진행률/ETA 출력)
5. DF 불용어 산출 (위 2절)
6. **내보내기**: `PRAGMA wal_checkpoint(TRUNCATE)` → `VACUUM INTO '배포파일'` —
   WAL 사이드카 없는 단일 파일. 이걸 서버 DB 경로에 덮어쓰는 게 배포다

### 배포 사이클

```
PC: /전체색인(원문 수집) → /재색인 백필시작 → /재색인 내보내기
        ↓ scp (deploy_*.sqlite3 + mogindex_userdict.txt)
서버: 봇 정지 → DB 교체(-wal/-shm 삭제) → 봇 시작
        ↓ 이후
서버: 20분 증분 배치가 신규 유입분만 처리 (한 주기 수십~수백 건, 몇 초)
```

## 5. 사고에서 나온 규칙들 (2026-07-10)

- **`DELETE WHERE message_pk=?`에는 반드시 `idx_message_terms_message`가 필요** —
  없으면 레거시 1,770만 행 테이블에서 메시지당 풀스캔 = 사실상 정지
- **대량 `DELETE FROM`은 FK OFF 연결에서** — truncate 최적화 조건
- **와이프 생략 판단은 메타(backfill_state)가 아니라 데이터로** — 구버전 색인은
  도장 없이 용어를 남기므로, "도장 없는 행에 붙은 용어" 존재 여부로 오염을 감지
- **서브프로세스 stdout은 라인 버퍼링 강제** (`sys.stdout.reconfigure(line_buffering=True)`)
  — 파이프 스폰 시 블록 버퍼링 때문에 진행 로그가 안 보이면 "멈춤"과 구분 불가
- **51만 건 백로그를 1GB 서버에서 돌리지 말 것** — 그 상황 자체를 막는 게 이 구조의 존재 이유.
  PC용 `.env`는 `MOGINDEX_BATCH_ENABLED=false`로 배치 선점을 차단
