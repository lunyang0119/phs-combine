"""침입자 추적 미니게임 — 모든 플레이어 노출 문자열.

원칙: 봇은 승무원의 휴대용 신호 추적기다. 침입자의 외형은 절대 묘사하지 않는다.
침입자는 오직 '신호' / '침입자 신호' / '미확인 신호' 로만 지칭한다.
"""

# 고정 문구 (변경 금지)
COMBAT_MODE_ON = "Activating Combat Mode"
CAPTURE_LINE = "수수께끼의 인영을 {name}{particle} 잡았다! Conflict Resolved."

DEVICE_KO = {
    "curtain": "커튼",
    "coffeepot": "커피포트",
    "light": "조명",
    "speaker": "스피커",
}
DEVICE_ABBR = {"curtain": "c", "coffeepot": "k", "light": "l", "speaker": "s"}

HACK_KO = {
    "ping": "핑",
    "doorlock": "문 잠금",
    "distract": "교란",
    "blackout": "광학 재부팅",
    "hide": "은신",
    "overload": "과부하",
}
HACK_DEVICE = {
    "ping": None,
    "doorlock": None,
    "distract": "speaker",
    "blackout": "light",
    "hide": "curtain",
    "overload": "coffeepot",
}

ORDER_KO = {"move": "이동", "search": "수색", "scan": "추적", "stay": "대기"}
SIGNAL_KO = {"strong": "강", "medium": "중", "weak": "약", "none": "없음", "noise": "판독 불가"}

# 라운드/보고
ROUND_OPEN = "## 📡 라운드 {round}\n제출 마감: {deadline}\n제출 현황: {submitted}/{total}\n{orders}"
ROUND_OPEN_NO_ORDERS = "— 아직 제출된 명령이 없습니다 —"
ORDER_LINE = "• {name}: {order}"
ORDER_MOVE = "이동 → {room}"
REPORT_HEADER = "### 📡 라운드 {round} 판독 결과"
REPORT_TRACE = "추적률 {bar} {pct}%"
REPORT_SIGNAL = "신호 강도: {band}"
REPORT_SIGNAL_EXACT = "신호 강도: {band} (거리 {dist})"
REPORT_SIGNATURE = "⚠ {device} 계열 장치에서 비정상 접근 감지"
REPORT_MARKER = "◎ 신호 감지: {rooms}"
REPORT_LOCKS = "⛔ 잠긴 문: {edges}"
REPORT_BLACKOUT = "▒▒▒ 신호 소실 — 이번 라운드 판독 불가 ▒▒▒"
REPORT_DOOR_LOCKED = "🚪 {name}: 문이 잠겨 있다 ({edge})"
REPORT_DAZED = "☕ {name}: 커피포트가 과부하로 터졌다. 증기에 휩싸여 다음 라운드 행동 불가"
REPORT_SEARCH_STEAM = "☕ {name}: 증기 때문에 {room} 수색 실패"
REPORT_SEARCH_EMPTY = "🔍 {name}: {room} 수색 — 이상 없음"
REPORT_SCAN = "📶 {name}: 침입자 신호가 **{device}** 계열 장치 근처에서 잡힌다"
REPORT_CONTACT_MISS = "💥 {name}: {room} 에서 접촉! 그러나 신호가 손끝을 스쳐 빠져나갔다"
REPORT_SCAN_NOISE = "📶 {name}: 노이즈 — 판독 실패"
REPORT_SCAN_BUSY = "📶 {name}: 채널 혼선 — 이번 라운드 추적 채널이 이미 사용 중"
REPORT_TRACED = "‼ 추적률 100% — 침입자 신호 실시간 추적 중"
REPORT_FROZEN = "‼ 침입자 신호가 한 지점에 고정되었다"
REPORT_TIMEOUT = "⏱ 제출 시간 종료 — 미제출자는 대기 처리"
REPORT_STEAM_ROOM = "☕ {room} 에 증기가 가득하다"

# 상태판
TABLE_HEADER = "R{round:02d}  추적률 {bar} {pct:>3d}%   신호: {band}"
TABLE_LEGEND = "c=커튼 k=커피포트 l=조명 s=스피커  ◎ 신호 감지  ⛔ 잠김  ☕ 증기"
TABLE_TITLE = "📡 신호 추적기 — 현황판"
TABLE_WAITING = "(대기 중)"

# 명령 응답
ERR_NO_GAME = "진행 중인 추적이 없습니다."
ERR_NOT_HUNTER = "이 추적의 참가자가 아닙니다."
ERR_NOT_OPEN = "지금은 명령을 제출할 수 없습니다 (라운드 정산 중이거나 종료됨)."
ERR_NOT_ADJACENT = "{room} 은(는) 현재 위치({here})에서 바로 갈 수 없습니다. 이동 가능: {options}"
ERR_UNKNOWN_ROOM = "{room} 은(는) 존재하지 않는 구역입니다."
ERR_DAZED = "증기에 휩싸여 이번 라운드는 행동할 수 없습니다."
ERR_WRONG_GUILD = "이 명령은 여기서 사용할 수 없습니다."
ERR_LOBBY_ONLY = "탑승은 참가 모집 중에만 가능합니다."
ERR_ALREADY_JOINED = "이미 탑승했습니다."
ERR_LOBBY_FULL = "정원({max})이 가득 찼습니다."
OK_JOINED = "🚃 {name} 탑승 확인. 현재 인원 {n}명."
OK_ORDER = "명령 접수: **{order}**  (마감 전까지 다시 제출하면 덮어씁니다)"
OK_ORDER_ALL_IN = "명령 접수: **{order}** — 전원 제출 완료, 곧 정산합니다."
LOBBY_OPEN = "## 🚃 침입자 추적 — 참가 모집\n`/탑승` 으로 참가하세요. (2~6명)\n현재 인원: {n}명\n{names}"
GAME_START_INTRO = "침입자 신호 포착. 승무원 통신기 연결 완료.\n각자 통신기로 이동/수색/추적 명령을 제출하십시오."
TUTORIAL = (
    "## 📖 통신기 사용법\n"
    "매 라운드 **한 가지 행동**을 제출합니다. 전원이 제출하거나 마감 시간이 되면 모두의 행동이 동시에 처리됩니다.\n"
    "라운드 메시지의 버튼을 누르거나, 아래 명령어를 직접 입력하세요.\n"
    "• `/이동 구역:B2` — 현재 위치와 문으로 이어진 옆 구역으로 이동. 침입자와 같은 구역에 서거나 같은 문을 엇갈려 지나면 체포.\n"
    "• `/수색` — 지금 있는 구역을 샅샅이 뒤짐. 숨어 있는 침입자를 잡는 유일한 방법.\n"
    "• `/추적` — 추적기를 조작해 침입자가 있는 구역의 장치 계열(커튼·커피포트·조명·스피커)을 알아냄. 라운드당 {scan_budget}명만 가능.\n"
    "• `/대기` — 제자리에서 라운드를 넘김.\n"
    "제출한 명령은 마감 전까지 다시 제출해 바꿀 수 있습니다. 접수 확인은 본인에게만 보입니다.\n"
    "현황판이 위로 밀려나면 `/현황`, 규칙이 궁금하면 `/추적기도움말`.\n"
    "현황판 읽는 법: 구역 사이의 틈이 **문**, `1` `2`… 는 승무원 위치, `◎` 신호 감지, `⛔` 잠긴 문, `☕` 증기."
)
HELP = (
    "## 📡 침입자 추적 — 도움말\n"
    "매 라운드 한 가지 행동을 제출합니다. 전원 제출하거나 마감이 되면 동시에 정산됩니다.\n"
    "• **이동** — 인접 구역으로 이동. 침입자와 같은 구역에 서거나 같은 문을 엇갈려 지나면 체포.\n"
    "• **수색** — 현재 구역을 샅샅이 뒤짐. 숨어 있는 침입자를 잡는 유일한 방법.\n"
    "• **추적** — 추적기를 조작해 침입자가 지금 있는 구역의 장치 계열을 알아냄 (라운드당 {scan_budget}회).\n"
    "• **대기** — 제자리.\n"
    "추적기는 매 라운드 신호 강도(가장 가까운 승무원과의 거리), 비정상 접근 시그니처, 신호 감지 표식을 보고합니다.\n"
    "추적률은 라운드마다 오르며, 높아질수록 표식이 정확해집니다."
)
# 디버그 자동 헌터 (관제 채널)
BOT_THINKING = "🤖 **{name}** — R{round} 판단 중…"
BOT_DECISION = "🤖 **{name}** → **{order}**\n> {reason}"
BOT_REJECTED = "⚠ {name} 의 선택이 규칙에 맞지 않아 대기로 처리: {note}"
SUMMARY_HEADER ="## 📼 추적 기록 요약 (총 {rounds}라운드)"
SUMMARY_ROUND = "**R{round}** 침입자 {froom} | {orders} | {events}"
SUMMARY_CAPTURE = "체포: {name} ({how}, R{round})"
CAPTURE_HOW = {"search": "수색", "swap": "문에서 조우", "colocate": "같은 구역"}
