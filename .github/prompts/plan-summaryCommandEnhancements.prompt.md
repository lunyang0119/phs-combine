# 채팅 요약 명령어 개선 계획

## 요청 사항

### 1. 날짜 지정 기능 추가
- **현재**: `/요약 채널:스토리모드 언제부터:14 언제까지:16` (오늘 기준)
- **변경**: `/요약 채널:스토리모드 날짜:26-01-20 언제부터:23 언제까지:1`
  - 날짜 형식: `YY-MM-DD` (예: 26-01-20 = 2026년 1월 20일)
  - 자정 넘기는 경우 자동 처리: 20일 23시 → 21일 1시

### 2. 대화 내용 txt 저장 기능 (테스트용)
- AI에 전달되는 채팅 로그 형식 확인용
- 요약 전에 txt 파일로 저장하여 input 형식 검증

---

## 구현 계획

### 파일: `summary.py`

#### 변경 1: 명령어 파라미터 추가
```python
@app_commands.command(name="요약", description="지정한 채널의 특정 시간대 채팅을 요약합니다.")
@app_commands.describe(
    채널="요약할 채널을 선택하세요",
    날짜="요약할 날짜 (YY-MM-DD 형식, 예: 26-01-20). 미입력시 오늘",
    언제부터="시작 시간 (0-23, 예: 14)",
    언제까지="종료 시간 (0-23, 예: 18, 시작시간으로부터 최대 4시간)"
)
async def summarize(
    self,
    interaction: discord.Interaction,
    채널: discord.TextChannel,
    언제부터: app_commands.Range[int, 0, 23],
    언제까지: app_commands.Range[int, 0, 23],
    날짜: str = None  # Optional, 기본값 오늘
):
```

#### 변경 2: 날짜 파싱 및 시간 범위 계산 로직
```python
# 날짜 파싱
if 날짜:
    try:
        base_date = datetime.strptime(날짜, "%y-%m-%d")
    except ValueError:
        await interaction.response.send_message(
            "❌ 날짜 형식이 올바르지 않습니다. (예: 26-01-20)",
            ephemeral=True
        )
        return
else:
    base_date = datetime.now()

# 시간 범위 계산
start_time = base_date.replace(hour=언제부터, minute=0, second=0, microsecond=0)

# 자정 넘기는 경우: 종료 시간은 다음 날
if 언제까지 <= 언제부터:
    end_time = (base_date + timedelta(days=1)).replace(
        hour=언제까지, minute=0, second=0, microsecond=0
    )
else:
    end_time = base_date.replace(hour=언제까지, minute=0, second=0, microsecond=0)
```

#### 변경 3: txt 저장 기능 추가 (테스트용)
```python
# 디버그용 txt 저장 (테스트 후 제거 가능)
debug_filename = f"chat_log_{채널.name}_{start_time.strftime('%Y%m%d_%H%M')}.txt"
with open(debug_filename, "w", encoding="utf-8") as f:
    f.write(f"채널: #{채널.name}\n")
    f.write(f"기간: {start_time} ~ {end_time}\n")
    f.write(f"메시지 수: {len(messages)}개\n")
    f.write("="*50 + "\n\n")
    f.write(chat_log)
```

---

## 예상 채팅 로그 형식 (txt 출력 예시)

```
채널: #스토리모드
기간: 2026-01-20 23:00:00 ~ 2026-01-21 01:00:00
메시지 수: 47개
==================================================

[23:05] 철수: 안녕하세요, 오늘 던전 도전하실 분?
[23:07] 영희: 저요! 힐러로 참여할게요
[23:08] 민수: 저도 탱커로 가겠습니다
[23:12] 철수: 좋아요, 그럼 출발하죠
[23:15] 영희: 이 던전 보스가 뭐였죠?
[23:16] 민수: 드래곤이에요. 화염 공격 조심해야 해요
...
[00:45] 철수: 드디어 클리어!
[00:46] 영희: 수고하셨어요~
[00:48] 민수: 레어 아이템 나왔다!
```

---

## 체크리스트

- [ ] `날짜` 파라미터 추가 (Optional, 기본값 오늘)
- [ ] 날짜 형식 검증 (`YY-MM-DD`)
- [ ] 자정 넘기는 시간 범위 계산 수정
- [ ] 미래 날짜 방지 로직 유지
- [ ] txt 파일 저장 기능 추가 (디버그용)
- [ ] 저장 경로: 프로젝트 루트 또는 지정 폴더

---

## 참고: 현재 메시지 수집 형식

```python
messages.append(f"[{timestamp}] {author}: {content}")
# 결과: "[14:30] 홍길동: 안녕하세요!"
```

- `timestamp`: `HH:MM` 형식 (24시간제)
- `author`: 사용자 display_name
- `content`: 메시지 내용 (200자 초과시 자름)
- 봇 메시지 및 시스템 메시지 제외
