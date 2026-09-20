# mogindex Kiwi 색인 배포 가이드

두 단계 아키텍처 요약:

- **봇 프로세스(서버)**: 메시지 원문만 SQLite에 저장. kiwipiepy를 절대 import하지 않는다.
- **증분 배치(서버)**: `MOGINDEX_BATCH_INTERVAL_MIN`(기본 20)분마다 봇이
  `mogindex_batch.py` 서브프로세스를 띄워 미토큰화 행을 색인하고 종료한다.
- **백필(PC)**: `mogindex_backfill.py`가 전체 원문을 Kiwi로 재색인하고 DF 불용어를
  산출한 뒤 `VACUUM INTO`로 배포용 단일 파일을 만든다.

## 0. 버전 고정 (PC·서버 동일하게)

```bash
python -m pip install -r requirements.txt        # PC (pytest 포함 전부)
python -m pip install kiwipiepy==0.21.0          # 서버는 이것만 추가하면 됨
```

`kiwipiepy==0.21.0` + `model_type='knlm'` 고정. 버전을 올리면 색인이 구버전과 섞이므로
반드시 PC에서 전체 백필 → 재배포 순서로 진행한다 (`tokenizer_version` 메타로 감지).

## 1. 사용자 사전 (`mogindex_userdict.txt`)

서버 은어·캐릭터 이름 같은 고유명사는 Kiwi 기본 사전에 없으므로 사용자 사전에 등록한다.
저장소 루트(스크립트 옆)에 두면 백필과 배치가 자동으로 로드한다. 형식(탭 구분):

```
아르딘	NNP
카페테리아	NNG
# 주석 가능
```

경로 변경은 `MOGINDEX_USERDICT_PATH`. **DB와 한 몸으로 취급**: PC에서 사전을 바꿔
백필했으면 같은 파일을 서버에도 복사해야 증분 배치가 같은 결과를 낸다.

## 2. PC 백필 + 내보내기 (봇 명령 기반)

PC에서 봇을 직접 돌려서 진행한다 (명령 → 봇 → PC 처리; 서버 대신 PC가 일하는 구조).

**⚠️ PC용 `.env` 필수 수정**: 서버용 `.env`의
`MOGINDEX_DB_PATH="/home/ubuntu/..."` 리눅스 절대경로를 그대로 두면 Windows에서
엉뚱한 경로(`C:\Program Files\Git\home\...` 등)로 풀려 즉시 실패한다. PC `.env`에는
상대경로를 쓴다:

```
MOGINDEX_DB_PATH=mogindex/search_index_live_debug.sqlite3
MOGINDEX_STATE_DB_PATH=mogindex/search_state.sqlite3
MOGINDEX_BATCH_ENABLED=false
```

`MOGINDEX_BATCH_ENABLED=false` 권장(PC 한정): 켜두면 원문 수집 직후 20분 증분
배치가 전체 백로그를 먼저 잡아 `/재색인 백필시작`이 "배치 실행 중" 안내로
미뤄진다. 잡혀도 결과는 동일하다 — 배치와 백필은 같은 토크나이저를 쓰므로
백필이 이어받아 남은 대기분 + DF 산출만 수행한다.

절차 (PC에서 `python main.py`로 봇 실행 후, 디스코드에서):

1. **원문 재수집**: `/전체색인 작업:새로시작` — 전체 기간을 다시 훑으며
   기존 행에는 원문(content)만 채우고, 새 행은 원문과 함께 저장한다.
   (카테고리 인벤토리·재시도·일자별 상태/이어하기 등 기존 전체색인 기능 그대로.)
2. **Kiwi 백필**: `/재색인 작업:백필시작` — `mogindex_backfill.py`를 서브프로세스로
   실행해 용어 색인 재구축 + DF 불용어 산출. 현재 토크나이저 버전으로 이미
   토큰화된 행은 유지하고 남은 대기분만 처리하므로, 중단·배치 선점 어느 쪽이든
   같은 명령으로 이어진다. **사용자 사전을 바꿨으면 `작업:처음부터다시`**(버전
   문자열은 그대로라 자동 감지되지 않음 — 전체 와이프 후 재색인 필요).
   진행 상황은 `/재색인 작업:상태확인`(토큰화 대기 건수)으로 확인.
3. **내보내기**: `/재색인 작업:내보내기` — `VACUUM INTO`로 사이드카 없는 단일 파일
   생성(기본 경로 `mogindex/deploy_YYYYMMDD_HHMM.sqlite3`, `내보내기경로` 옵션으로 변경).

CLI로도 동일 작업 가능(봇 없이): `python mogindex_backfill.py [--restart] [--export ...]`
— 단, 이때도 `--db`를 명시하거나 PC `.env` 경로를 먼저 고쳐야 한다.

환경 변수: `MOGINDEX_DF_CUTOFF_PCT`(기본 25 — 소스 내 메시지의 25% 초과 등장 용어를
불용어 처리), `MOGINDEX_DF_MIN_DOCS`(기본 100 — 이보다 작은 소스는 DF 산출 제외).

## 3. 서버 배포 순서

```bash
# 서버에서
sudo systemctl stop mogbot        # 또는 봇 프로세스 종료
# PC에서
scp deploy.sqlite3 ubuntu@server:/home/ubuntu/mogtel/mogindex/search_index_live_debug.sqlite3
scp mogindex_userdict.txt ubuntu@server:/home/ubuntu/mogtel/
# 서버에서 — 열린 DB를 복사한 적이 있다면 남은 사이드카 제거 (malformed 방지)
rm -f /home/ubuntu/mogtel/mogindex/search_index_live_debug.sqlite3-wal \
      /home/ubuntu/mogtel/mogindex/search_index_live_debug.sqlite3-shm
sudo systemctl start mogbot
```

경로는 서버 `.env`의 `MOGINDEX_DB_PATH`와 일치시켜야 한다.

## 4. 1GB 서버 스왑 2GB 설정 (최초 1회)

Kiwi 배치(~470MB)가 봇과 겹칠 때를 대비한 안전판. 배치는 시작 시
`/proc/self/oom_score_adj=1000`을 스스로 설정하므로 메모리가 바닥나면 커널이
봇이 아니라 배치를 죽인다.

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h   # 확인
```

## 5. 환경 변수 (신규)

| 변수                            | 기본값                    | 설명                                |
| ------------------------------- | ------------------------- | ----------------------------------- |
| `MOGINDEX_BATCH_ENABLED`      | `1`                     | 증분 배치 루프 on/off               |
| `MOGINDEX_BATCH_INTERVAL_MIN` | `20`                    | 배치 주기(분)                       |
| `MOGINDEX_BATCH_CHUNK_SIZE`   | `500`                   | 배치 트랜잭션당 메시지 수           |
| `MOGINDEX_USERDICT_PATH`      | `mogindex_userdict.txt` | 사용자 사전 경로                    |
| `MOGINDEX_DF_CUTOFF_PCT`      | `25`                    | DF 불용어 컷오프(%) — 백필 전용    |
| `MOGINDEX_DF_MIN_DOCS`        | `100`                   | DF 산출 최소 메시지 수 — 백필 전용 |

기존 변수(`MOGINDEX_DB_PATH` 등)는 README 참조.

## 6. 수동 배치 실행 / 첫 배치 검증

```bash
# 수동 실행 (봇이 돌고 있어도 안전: WAL + 단일 인스턴스 락)
python3 mogindex_batch.py --db /home/ubuntu/mogtel/mogindex/search_index_live_debug.sqlite3
```

검증 쿼리:

```bash
sqlite3 /home/ubuntu/mogtel/mogindex/search_index_live_debug.sqlite3 "
SELECT COUNT(*) AS pending FROM messages WHERE tokenized_at IS NULL;
SELECT value FROM mogindex_meta WHERE key='last_batch_run';
SELECT value FROM mogindex_meta WHERE key='tokenizer_version';
SELECT term, count FROM message_terms ORDER BY rowid DESC LIMIT 10;"
```

- `pending`이 배치 후 0(또는 배치 사이 유입분만)이면 정상.
- 봇 로그에서 `mogindex batch finished rc=0` 확인.
- 전투 중이면 `mogindex batch skipped: active combat ...` 로그와 함께 미뤄진다(정상).

## 7. 알려진 한계

- 검색 질의는 Kiwi 없이 조사 제거만 하므로, 활용이 심한 동사형 질의(예: "달렸었는데")는
  용어 색인을 빗나갈 수 있다. 명사 질의는 정확 일치하며, 부분 문자열은 트라이그램 FTS가
  받쳐준다.
- 동사/형용사 어간(VV/VA)은 v1에서 색인하지 않는다 (명사 중심).
- 메시지 수정/삭제는 색인에 반영되지 않는다 (기존과 동일).
