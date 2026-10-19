"""카드 전투 — 플레이어에게 보이는 문자열 전부."""
from __future__ import annotations

KIND_KO = {"attack": "공격", "defend": "방어", "hack": "해킹", "special": "특수"}
TARGET_KO = {"enemy": "적 1명", "ally": "아군 1명", "self": "자신", "all_enemy": "적 전체", "all_ally": "아군 전체"}
SIDE_KO = {"ally": "아군", "enemy": "적"}

HELP = (
    "**카드 전투 흐름**\n"
    "1. `/카드 옵션:주최` — 세션을 엽니다 (한 번에 하나). 주최자는 전체 카드에서 고를 수 있습니다.\n"
    "2. `/카드 옵션:참가` — 모집 중에 참가합니다. 캐릭터 시트의 HP/스탯을 씁니다.\n"
    "3. `/카드 옵션:시작 전투id:b01` — 주최자가 전투를 시작합니다.\n"
    "4. 아군 턴마다 턴 메시지의 **카드 뽑기** 버튼 → 3장 중 한 장과 대상을 고릅니다. "
    "전원 선택 시 자동 정산, 주최자는 `/카드 옵션:턴종료` 로 강제 정산.\n"
    "5. 정산 후 적이 행동하고 다음 턴이 열립니다. 한쪽 HP 가 모두 0이 되면 끝.\n"
    "카드 종류: 공격 / 방어(다음 상대 턴 피해 감소) / 해킹(공격력 감소·불발·표적 교란·기절) / 특수."
)

# -- 명령 응답 ---------------------------------------------------------------
OK_HOSTED = "카드 전투 세션을 열었습니다. 참가자는 `/카드 옵션:참가`, 시작은 `/카드 옵션:시작 전투id:<id>`.\n(주최자인 당신은 패 대신 전체 카드에서 고를 수 있습니다.)"
OK_JOINED = "{name}님 참가 완료 ({n}명)."
OK_STARTED = "전투 시작: {battle}"
OK_FORCED = "턴을 강제 정산했습니다."
OK_ENDED = "카드 전투 세션을 종료했습니다."
OK_CHOSEN = "선택 완료: **{card}**{target}\n정산 전까지 아래 버튼으로 바꾸거나 취소할 수 있습니다."
OK_CANCELLED = "선택을 취소했습니다. 다시 고르세요."

ERR_SESSION_EXISTS = "이미 세션이 있습니다 (주최: {host}). `/카드 옵션:종료` 후 다시 여세요."
ERR_NO_SESSION = "열린 세션이 없습니다. `/카드 옵션:주최` 로 먼저 여세요."
ERR_LOBBY_ONLY = "지금은 참가할 수 없습니다 (모집 중이 아님)."
ERR_NOT_LOBBY = "모집 중일 때만 시작할 수 있습니다."
ERR_WRONG_GUILD = "이 서버의 세션이 아닙니다."
ERR_ALREADY_JOINED = "이미 참가했습니다."
ERR_LOBBY_FULL = "정원({max}명)이 찼습니다."
ERR_NO_CHARACTER = "캐릭터 시트에 없는 사용자입니다. `/등록` 후 참가하세요. (시트를 방금 고쳤다면 `/시트갱신`)"
ERR_HOST_ONLY = "주최자만 쓸 수 있는 옵션입니다."
ERR_NO_PLAYERS = "참가자가 없습니다."
ERR_BATTLE_NOT_FOUND = "전투 id `{id}` 를 찾지 못했습니다. 가능한 id: {ids}"
ERR_DATA = "전투 데이터를 읽지 못했습니다: {err}"
ERR_NOT_ALLY_TURN = "지금은 아군 턴이 아닙니다."
ERR_STALE_MESSAGE = "지난 턴의 메시지입니다. 최신 턴 메시지의 버튼을 쓰세요."
ERR_NOT_IN_GAME = "이 전투의 참가자가 아닙니다."
ERR_MODULE_OFF = "카드 전투 모듈이 꺼져 있습니다."
ERR_NOT_YOURS = "본인의 선택창만 조작할 수 있습니다."

RULE_ERRORS = {
    "NOT_OPEN": "이미 정산된 턴입니다.",
    "NOT_PLAYER": "이 전투의 참가자가 아닙니다.",
    "DEAD": "쓰러진 상태라 행동할 수 없습니다.",
    "STUNNED": "기절 상태라 이번 턴에는 행동할 수 없습니다.",
    "NOT_IN_HAND": "손에 없는 카드입니다.",
    "BAD_TARGET": "고를 수 없는 대상입니다.",
    "NO_TARGET": "대상을 골라야 합니다.",
    "NO_PLAYERS": "참가자가 없습니다.",
    "NO_ENEMIES": "적이 없습니다.",
    "EMPTY_POOL": "한쪽이 뽑을 카드가 없습니다 (Cards.csv 의 pool 확인).",
}

# -- 공개 메시지 -------------------------------------------------------------
LOBBY = "## 🃏 카드 전투 — 참가 모집 중\n`/카드 옵션:참가` 로 참가하세요. 주최자가 `/카드 옵션:시작 전투id:<id>` 로 시작합니다.\n**참가자 ({n}명)**: {names}"
LOBBY_EMPTY = "아직 없음"
INTRO = "## ⚔️ {battle}\n{desc}\n**적**: {enemies}\n**아군**: {players}"
TURN_OPEN = "## 🃏 {round}라운드 — 아군 턴\n아래 **카드 뽑기** 버튼으로 패를 확인하고 카드를 고르세요.\n**선택 현황: {done}/{total} 완료**\n{marks}\n{board}"
MARK_DONE = "✅"
MARK_TODO = "⬜"
MARK_OUT = "💀"
MARK_STUN = "💫"
REPORT_HEADER_ALLY = "### 🟢 {round}라운드 — 아군 행동"
REPORT_HEADER_ENEMY = "### 🔴 {round}라운드 — 적 행동"
BOARD_ALLY = "🟢 아군: {units}"
BOARD_ENEMY = "🔴 적: {units}"
BOARD_UNIT = "{name} {hp}/{max}{tags}"
BOARD_DEAD = "{name} 💀"
WIN_ALLY = "## 🏆 승리! 적을 모두 쓰러뜨렸습니다."
WIN_ENEMY = "## 💀 패배… 아군이 모두 쓰러졌습니다."
STATUS = "**상태**: {status} / {round}라운드 ({side} 턴)\n**선택 현황**: {done}/{total}\n{board}"

# -- 로그 줄 -----------------------------------------------------------------
# HP 는 대상의 것. 대상이 자신이면 이름을 생략한다.
LOG_USE = "{actor}{josa} **{card}**{eul} 사용했습니다."
LOG_TARGET = " {target} HP {hp}/{max}{detail}"
LOG_SELF = " HP {hp}/{max}{detail}"
LOG_MISFIRE = "{actor}{josa} **{card}**{eul} 사용했지만 불발되었습니다."
LOG_NO_TARGET = "{actor}{josa} **{card}**{eul} 사용했지만 대상이 없었습니다."
LOG_WAIT = "{actor}{eun} 대기했습니다."
LOG_STUNNED = "{actor}{eun} 기절해서 행동하지 못했습니다."
LOG_REDIRECT_PREFIX = "(표적 교란!) "
LOG_KILLED = " 💀"
DETAIL = {
    "damage": " (-{amount})",
    "self_damage": " (반동 -{amount})",
    "heal": " (+{amount})",
    "shield": " (다음 상대 턴 피해 {pct}% 감소)",
    "atk_down": " (공격력 {pct}% 감소, {turns}회)",
    "misfire": " (다음 공격 {pct}% 불발)",
    "redirect_random": " (다음 공격 표적 교란)",
    "stun": " (기절)",
}
TAG = {"shield": "🛡", "atk_down": "⬇", "misfire": "❓", "redirect_random": "🔀", "stun": "💫"}

# -- ephemeral 선택창 -------------------------------------------------------
HAND_TITLE = "**{round}라운드 — 당신의 패**{admin}\n{cards}"
HAND_ADMIN = " (주최자: 전체 카드)"
HAND_CARD = "• **{name}** [{kind}·{target}] {desc}"
HAND_PLACEHOLDER = "카드를 고르세요"
HAND_MORE = "다음 ▶ ({page}/{pages})"
TARGET_TITLE = "**{card}** — 대상을 고르세요"
TARGET_PLACEHOLDER = "대상"
TARGET_OPTION = "{name} (HP {hp}/{max})"
BTN_BACK = "◀ 뒤로"
BTN_REPICK = "다시 고르기"
BTN_CANCEL = "선택 취소"
BTN_DRAW = "카드 뽑기"
