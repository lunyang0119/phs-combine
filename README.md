# 들어가기 앞서

- phs 폴더, mogtel(혹은 local_server) 폴더는 각각 phs용 파일과, 로컬에서 돌리기 위한 파일이며, **기본적으로는 루트 디렉토리의 파일들과 combat 폴더만 다운로드 받는 것을 추천드립니다.**
- 또한 서버/카테고리/스레드 ID 관련해서는 질문 받지 않습니다. 설정 가셔서 개발자모드 켜시고 오른쪽 클릭(모바일은 길게 누름)하시면 ID 복사가 보입니다.
- 기본적으로 Python을 사용할 줄 안다는 전제 하에 작성하고 있습니다. 그래도 초보자도 알기 쉽게 해보겠습니다. 다만, 초보자라도 **추후 편집 및 사용의 용이를 위해 vscode라는 프로그램을 다운받는 것을 추천드립니다.**
- Windows 11 네이티브 기반으로 설명합니다.

# 개요

이 스크립트는 Discord(이하 디스코드)에서 소위 커뮤를 운영할 때 쓸 수 있도록 만든 봇입니다.

장기적인 대화를 하는 서버에서도 사용하기 위한 아카이브 기능이 있으며, 디스코드의 한국어 검색이 부실함을 보완하고자 한국어 맞춤 색인 및 검색 엔진을 만들었습니다.

사용한 알고리즘은 n-gram, Stop word(불용어) 제거 등 입니다.

# 전체 구조

```apache
PHS-COMBINE
├─combat
├─local_server
└─phs
```

# 설치 방법

참고로 현재 저장소에 `mogtel` 폴더가 있다면, 그것은 추후 `local_server`로 이름을 바꿀 예정인 로컬 테스트용 폴더입니다.

초기에 봇을 설치하는 사람은 루트 디렉토리 기준으로 먼저 세팅하는 것을 추천드립니다. `phs` 폴더는 phs 서버용 파일이므로, 루트 봇과 설정값이 다를 수 있으며 **사용을 추천드리지 않습니다.**

## 0. 개발 환경 설정

**vscode 설치는 추천이며, Python 설치는 필수입니다.**

파이썬 설치는 알아서 하시면 됩니다.

### 1) 토큰 발급

디스코드 개발자 포털로 들어가신 후, `+만들기` 버튼을 누른 후, `서버나 커뮤니티용 봇 생성하기`를 누릅니다. (날짜에 따라 달라질 수 있습니다.)

이름 입력 후, 앱으로 들어가 왼쪽 탭의 봇으로 들어갑니다. 토큰 초기화를 누르고, 발급된 토큰을 새 메모장에 복붙해둡니다. 잃어버리면 다시 발급하셔야 해요.

참고로 밑에 OAuth2 승인 필요가 on으로 되어있다면 off로 해주시면 좋습니다. (소규모로 쓸 경우)

### 2) 봇 설치

- 설치환경: 길드 설치만 하시는걸 추천드립니다. 서버를 관리하는 봇이니까요.
- 설치 링크: 디스코드 제공 링크 쓰시면 됩니다.
- 기본설치 설정 - 사용자 설치: applications.commands
- 길드설치 - 스코프: applications.commands, bot
- 길드설치 - 권한: 왠만하면 관리자를 추천드립니다. 만약 안된다면, `링크 임베드, 메시지 보내기, 모두 멘션하기, 빗금 명령어 사용, 스레드에서 메시지 보내기, 임베드 활동 사용, 채널 보기` 정도면 됩니다.

이 모든 과정을 끝내셨다면 discord 제공링크 밑에 있는 링크로 들어가셔서, 채널에 초대하시면 됩니다.

## 1. 다운로드

가장 최신 브랜치로 들어가 Download Zip으로 받는 것을 추천드립니다.

현재 최신 브랜치는 `search-engine-260623` 입니다.

추후 바뀔 수 있기에, 업데이트 날짜를 잘 확인하여 다운로드 받아주시기 바랍니다. (보통 브랜치 이름에 날짜가 쓰여져 있습니다. 하루마다 갱신합니다.

Main 브랜치 Pull 작업은 코드가 안정되었다고 생각되었을 때 실행합니다. ~~사실 보통 Pull을 안합니다~~

## 2. 압축 풀기

되도록 **영어 경로**에 두시는걸 추천드립니다.

### 1) vscode를 설치한 경우

설치한 폴더(PHS-COMBINE)에서 오른쪽 클릭하여 vscode로 열기를 누릅니다.

(아니면 vsocde를 실행하여 폴더 열기로 하셔도 좋습니다.)

ctrl+` (혹은 윗 탭의 Terminal)로 cmd를 엽니다.

`python -m pip install discord.py python-dotenv gspread google-auth aiohttp` 를 입력해주세요.

### 2) vscode를 설치하지 않은 분들

윈도우버튼 + R 을 누르신 후, cmd를 입력합니다.

거기에 `python -m pip install discord.py python-dotenv gspread google-auth aiohttp`를 입력합니다.

## 3. .env 설정

루트 디렉토리 (즉, PHS-COMBINE 파일 내)에 `.env`라는 이름의 파일을 만들어주세요. 그리고 환경변수를 입력해주세요.

(<> ← 이 표시는 빼주세요. 설명용입니다.)

```apache
MOG_TOKEN="<디스코드 개발자 플랫폼에서 발급받은 토큰>"
MOGINDEX_ERROR_THREAD_ID="<에러가 났을 때 알림 메세지를 받을 곳의 스레드 혹은 채널 ID>"
MOG_CATEGORY_ID="<기본 카테고리 ID. 보통 색인할 카테고리 중 하나를 넣으면 됩니다>"
MOG_GUILD_ID="<서버 ID>"
MOGINDEX_DB_PATH=mogindex/search_index_live_debug.sqlite3
MOG_INDEX_CATEGORIES_JSON=[{"key":"worldmap","name":"월드 맵","category_id":"<카테고리 ID>","worldmap":true,"parent_channel_ids":["<채널 ID>","<포럼 ID>"]},{"key":"custom","name":"커스텀 모드","category_id":"<카테고리 ID>","worldmap":false,"parent_channel_ids":["<채널 ID>"]}]
```

`MOG_INDEX_CATEGORIES_JSON`은 반드시 한 줄로 써주세요.

**JSON은 반드시 한 줄입니다.**

`.env`는 줄바꿈 JSON을 잘 처리하지 못합니다.

각 항목의 의미는 아래와 같습니다.

- `key`: 카테고리를 식별하기 위한 영어 이름입니다. 다른 카테고리와 겹치면 안 됩니다.
- `name`: 디스코드에서 표시되는 카테고리 이름입니다.
- `category_id`: 디스코드 카테고리 ID입니다.
- `worldmap`: 복귀자 키워드 추천을 위한 값입니다. `true`이면 복귀자가 활동하지 않은 시기를 계산할 때 이 카테고리 활동을 기준으로 삼습니다.
- `parent_channel_ids`: 해당 카테고리에 있는 채널 또는 포럼 ID 목록입니다. 색인하고 싶지 않은 채널은 넣지 않는 것을 추천드립니다.

```apache
GSPREAD_SHEET_NAME = "<아무거나 하시면 됩니다만, 영어로 해주시고, 되도록이면 띄어쓰기 대신 언더바를 사용해주세요. 예시: Discord_Bot"
FULL_INDEX_SLOW_DAY_SECONDS=300
FULL_INDEX_REST_SECONDS=120
FULL_INDEX_DISCORD_RETRY_ATTEMPTS=4
FULL_INDEX_DISCORD_RETRY_SECONDS=15
MOGINDEX_DAILY_ENABLED=true
MOGINDEX_DAILY_HOUR=23
MOGINDEX_DAILY_MINUTE=59
MOGINDEX_DAILY_CATEGORIES=메인 메뉴,월드 맵,커스텀 모드
```

- `FULL_INDEX_SLOW_DAY_SECONDS`는 하루 색인이 이 시간 이상 걸렸을 때 쉬어갈 기준입니다.
- `FULL_INDEX_REST_SECONDS`는 쉬어갈 시간입니다.
- `FULL_INDEX_DISCORD_RETRY_ATTEMPTS`와 `FULL_INDEX_DISCORD_RETRY_SECONDS`는 디스코드 API가 503을 냈을 때 재시도하는 횟수와 대기 시간입니다.

## 4. phs 폴더와 local_server 폴더에 대해

`phs` 폴더는 phs 서버용 파일입니다. 서버 ID, 카테고리 ID, 색인 기간 기본값이 루트 파일과 다릅니다.

`local_server` 폴더는 로컬 테스트용 파일입니다. 현재 저장소에서는 아직 `mogtel`이라는 이름일 수 있습니다. 나중에 `local_server`로 이름을 바꿀 예정입니다.

초기 설치만 하는 사람은 이 두 폴더를 **삭제하시는걸 추천드립니다.**

루트 파일 기준으로 먼저 봇을 세팅하는 것을 추천드립니다.

## 5. 구글 스프레드 시트 API 발급

### 1) 구글 클라우드 공식 홈페이지 접속

https://console.cloud.google.com/

에 접속하여, 프로젝트를 만듭니다.

### 3) 사용자 인증 정보 발급

API 및 서비스 메뉴의 사용자 인증 정보에 들어갑니다.

사용자 인증 정보 만들기 > 서비스 계정 만들기 >  만든 이메일로 들어가 키 추가를 누름 > 새 키를 만들되, JSON으로 만들기

다운로드 받은 JSON은 PHS-COMBINE 폴더에 위치시켜주시고, google_sheets_handler.py의 29번째 줄에 있는 creds_path에 인증파일의 이름을 적어주세요.

예시

```apache
creds_path = "final-fantasy-jom-haera.json"
```

### 4) API 설정

API 및 서비스  > 라이브러리

에서 `Google Sheets API`를 검색합니다. (spread sheet로 검색해도 나옵니다. 수시로 이름이 바뀌나, 스프레드 시트이기만 하면 됩니다.)

(처음이라면) 사용버튼을 누릅니다. 조금 기다립니다.

관리 버튼이 생기면 누른뒤, 사용자 인증정보로 갑니다.

JSON 파일을 엽니다.

**초보자라면 메모장으로 여시면 됩니다만, 추후 봇 설치를 위해 vscode라는 소프트웨어를 미리 다운로드 받는 것을 추천드립니다.**

JSON의 "client_email" 부분에 있는 이메일을 복사해둡니다. 빈 메모장에 복붙해두세요.

### 5) 구글 스프레드 시트 만들기

`.env`에서 만든 `GSPREAD_SHEET_NAME` 의 이름대로 스프레드 시트를 만듭니다.

그리고 스프레드 시트의 공유를 누른 뒤, `사용자, 그룹, 스페이스, 어쩌구 추가` 란에 아까 복사해둔 이메일을 입력합니다.


## 6. 구글 스프레드 시트 설정

일단 `ServerChannel`이라는 이름의 시트를 만들어주세요. 이 시트는 봇이 해당 채널 혹은 스레드로 메세지를 전달하기 위한 기능입니다.

A1열에는 `서버이름`, B1에는 `채널이름`, C1에는 `채널ID`, D1에는 `서버ID`라고 입력해주세요.

그리고 A열의 2번째 행 부터는 자신이 원하는 서버 이름을 아무거나 써도 됩니다. 다만 추후 식별이 쉽게 짧게쓰는 것을 추천드립니다. (2~4글자 정도 추천합니다.)

채널이름도 임의로 씁니다. (이것도 채널의 용도를 알 수 있는 2~8글자 정도를 추천드립니다.)

채널 ID에는 해당 채널의 ID를 입력합니다.

서버 ID에는 해당 서버의 ID를 입력합니다.


## 7. 검색 DB 확인

검색 DB가 제대로 잡혔는지 확인하려면 아래 명령어를 사용합니다.

```cmd
python mogindex_discord_debug.py inspect
```

정상적으로 색인된 DB라면 `sources`, `messages`, `message_terms`, `daily_terms` 등이 0이 아닌 값으로 나옵니다. 아직 색인을 하지 않았다면 0으로 나올 수 있습니다. 이 경우는 오류가 아닙니다.


## 8. 전체색인

디스코드에서 아래 명령어를 사용할 수 있습니다.

```text
/전체색인 작업:상태확인
/전체색인 작업:이어하기
/전체색인 작업:새로시작
```

`이어하기`는 외부 파일을 보지 않습니다. SQLite의 `full_index_runs`, `full_index_days` 기록만 기준으로 삼습니다.

만약 이어갈 전체색인 기록이 없다고 나오면, 현재 봇이 보고 있는 DB에 이어갈 기록이 없는 것입니다. DB 경로를 먼저 확인하세요.


## 9. 봇 기동

### 1) vscode를 설치한 경우

설치한 폴더(PHS-COMBINE)에서 오른쪽 클릭하여 vscode로 열기를 누릅니다. (아니면 vsocde를 실행하여 폴더 열기로 하셔도 좋습니다.)

ctrl+` (혹은 윗 탭의 Terminal)로 cmd를 엽니다.

`python main.py` (안되시면 `python3 main.py`) 를 입력해주세요.

### 2) vscode를 설치하지 않은 분들

윈도우버튼 + R 을 누르신 후, cmd를 입력합니다.

`python main.py` (안되시면 `python3 main.py`) 를 입력해주세요.

이렇게하면 봇 기동은 끝납니다.



# 자주 나는 문제

### 슬래시 명령어가 안 보임

봇 기동 로그에 슬래시 명령어 동기화 성공이 찍혔는지 확인하세요. 길드 ID가 잘못되었거나, 봇이 해당 서버에 설치되어 있지 않으면 명령어가 보이지 않을 수 있습니다.

혹은 브라우저나 어플의 캐시 때문에 나오지 않을 경우가 있습니다. 이럴 경우, 만약 다른 계정이 있다면 다른 계정으로 바꾸어서 로그인 하신 뒤 테스트 해보시는걸 추천드립니다. 만약 다른 계정이 없으시다면, 새로고침이나 어플을 껐다가 다시 시작해보시는 것도 추천드립니다.


### 검색 DB가 0으로 나옴

아직 색인을 하지 않았다면 정상입니다. 이미 색인한 DB를 복사했는데도 0이면 `MOGINDEX_DB_PATH`가 다른 파일을 보고 있을 가능성이 큽니다.


### Unknown interaction

Discord interaction은 3초 안에 응답해야 합니다. 오래 걸리는 상태확인이나 DB 접근은 먼저 defer하고 이후 응답해야 합니다. 색인작업은 한 스레드(혹은 채널) 당 길어봐야 2.38초가 걸립니다. (오라클 무료서버 사양 혹은 RX6600+ Ryzen5 5600X Core 6+32GB DDR4 )


### 503 Service Unavailable

Discord API의 일시 오류입니다. 아래 같은 로그가 나오면 재시도 중입니다.

```text
mogindex full-index transient Discord error; retrying ... attempt=1/4
```

모든 재시도가 실패하면 해당 날짜가 failed로 기록됩니다. 이후 `/전체색인 작업:이어하기`로 다시 진행할 수 있습니다.


### database disk image is malformed

SQLite DB 파일이 복사 중 깨졌거나, SQL 에디터/봇이 DB를 연 상태에서 파일을 건드렸을 가능성이 큽니다. 봇과 SQL 에디터를 끈 뒤 DB를 다시 복사하세요.


# 여담

## 색인의 크기에 대하여

2년 동안 하루동안 쉬지 않고, 메세지가 활발히 오간 경우, 색인 파일이 2GB가 넘어갈 수 있으며, 오래걸릴 수 있습니다.

추후 색인의 속도 문제를 해결하고자 생각 중입니다. 현재는 병렬처리가 아닙니다.
