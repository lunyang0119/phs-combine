"""카드 전투 — 플레이어에게 보이는 문자열 전부."""
from __future__ import annotations

KIND_KO = {"attack": "공격", "defend": "방어", "hack": "해킹", "special": "특수"}
TARGET_KO = {"enemy": "적 1명", "ally": "아군 1명", "self": "자신", "all_enemy": "적 전체", "all_ally": "아군 전체"}
SIDE_KO = {"ally": "아군", "enemy": "적"}
# 시트 열 이름은 그대로, 화면에만 사이버펑크식 이름을 쓴다.
STAT_KO = {"physics": "근력", "magic": "RAM", "agility": "민첩", "charm": "매력", "none": "-"}
JOB_KO = {"스트라이커": "엣지러너", "마테리아 위버": "넷러너", "솔져": "솔져", "턱스": "턱스"}
MODE_KO = {"cling": "접객 모드", "rampage": "폭주 모드", "reboot": "리부트"}
MODE_DESC = {
    "cling": "단말기 소지자에게 매달린다 (붙잡힘 + 긁힘)",
    "rampage": "손톱으로 두 번 긋는다",
    "reboot": "행동하지 않는다. 이번 라운드 해킹은 칩 안정도를 2배로 깎는다",
}
INTENT_KO = {"attack": "공격 준비", "defend": "방어 준비", "hack": "해킹 준비", "special": "특수 준비",
             "stunned": "기절 (행동 불가)", "wait": "대기", "mode": "{label}"}
TAG_KO = {"loud": "소음", "lethal": "살상", "nonlethal": "비살상"}
COMBO_MOD_KO = {"stun": "기절한", "atk_down": "무기 글리치 상태의", "misfire": "사이버웨어 고장 상태의",
                "redirect_random": "적대 인식 상태의", "grapple": "붙잡고 있는", "shield": "엄폐 중인"}
COMBO_TEXT = {"dmg_x": "피해 ×{v}", "dmg_add": "피해 +{v}", "chip_add": "칩 안정도 추가 -{v}"}
COMBO_LINE = "▶ 콤보: {cond}에게 {bonus}"
COMBO_SELF_LINE = "▶ 콤보: 자신이 {cond} 상태면 {bonus}"

HELP = (
    "**카드 전투 흐름**\n"
    "1. `/카드 옵션:주최` — 세션을 엽니다 (한 번에 하나). 주최자는 전체 카드에서 고를 수 있습니다.\n"
    "2. `/카드 옵션:참가` — 모집 중에 참가합니다. 캐릭터 시트의 HP/스탯을 씁니다.\n"
    "3. `/카드 옵션:시작 전투id:scav4` — 주최자가 전투를 시작합니다 (인원수에 맞는 id 를 고르세요).\n"
    "4. 라운드마다 적의 **행동 종류**가 먼저 공개됩니다. **카드 뽑기** 버튼 → 3장 중 한 장과 대상을 고릅니다. "
    "전원 선택 시 양편이 함께 정산됩니다 (해킹 → 방어 → 공격, 같은 단계는 민첩 순). 주최자는 `/카드 옵션:턴종료` 로 강제 정산.\n"
    "5. **보느와의 방화벽**: 아군이 맞거나 방어할 때 차는 팀 게이지. 턴 메시지의 버튼으로 라운드에 한 번 씁니다.\n"
    "6. 비살상 전투에서는 남은 HP 를 크게 초과해 쓰러뜨리면 **과잉 제압**으로 NCPD 경계도가 오르고, "
    "가득 차면 드론이 와서 **도주 단계**(5턴 가위바위보)로 넘어갑니다.\n"
    "카드 종류: 공격 / 방어(이번 라운드 피해 감소) / 해킹(공격력 감소·불발·적대 인식·기절) / 특수. "
    "카드 설명의 ▶ 콤보 조건을 맞추면 보너스가 붙습니다."
)

# -- 명령 응답 ---------------------------------------------------------------
OK_HOSTED = "카드 전투 세션을 열었습니다. 참가자는 `/카드 옵션:참가`, 시작은 `/카드 옵션:시작 전투id:<id>`.\n(주최자인 당신은 패 대신 전체 카드에서 고를 수 있습니다.)"
OK_JOINED = "{name}님 참가 완료 ({n}명)."
OK_STARTED = "전투 시작: {battle}"
OK_FORCED = "라운드를 강제 정산했습니다."
OK_ENDED = "카드 전투 세션을 종료했습니다."
OK_CHOSEN = "선택 완료: **{card}**{target}\n정산 전까지 아래 버튼으로 바꾸거나 취소할 수 있습니다."
OK_CANCELLED = "선택을 취소했습니다. 다시 고르세요."
OK_FIREWALL = "방화벽 사용: **{option}** (−{cost}, 남은 게이지 {value})"
OK_ESCAPE_CHOSEN = "선택 완료: **{action}**. 정산 전까지 다른 버튼으로 바꿀 수 있습니다."

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
ERR_NOT_ALLY_TURN = "지금은 카드를 고르는 단계가 아닙니다."
ERR_NOT_ESCAPE = "지금은 도주 단계가 아닙니다."
ERR_STALE_MESSAGE = "지난 라운드의 메시지입니다. 최신 메시지의 버튼을 쓰세요."
ERR_NOT_IN_GAME = "이 전투의 참가자가 아닙니다."
ERR_MODULE_OFF = "카드 전투 모듈이 꺼져 있습니다."
ERR_NOT_YOURS = "본인의 선택창만 조작할 수 있습니다."

RULE_ERRORS = {
    "NOT_OPEN": "이미 정산된 라운드입니다.",
    "NOT_ESCAPE": "지금은 도주 단계가 아닙니다.",
    "NOT_PLAYER": "이 전투의 참가자가 아닙니다.",
    "DEAD": "쓰러진 상태라 행동할 수 없습니다.",
    "STUNNED": "기절 상태라 이번 라운드에는 행동할 수 없습니다.",
    "GRAPPLED": "붙잡힌 상태입니다. **떨쳐내기**만 쓸 수 있습니다.",
    "NOT_IN_HAND": "손에 없는 카드입니다.",
    "BAD_TARGET": "고를 수 없는 대상입니다.",
    "NO_TARGET": "대상을 골라야 합니다.",
    "BAD_OPTION": "알 수 없는 선택입니다.",
    "FIREWALL_USED": "방화벽은 라운드에 한 번만 쓸 수 있습니다.",
    "FIREWALL_POOR": "방화벽 게이지가 부족합니다.",
    "NO_PLAYERS": "참가자가 없습니다.",
    "NO_ENEMIES": "적이 없습니다.",
    "EMPTY_POOL": "한쪽이 뽑을 카드가 없습니다 (Cards.csv 의 pool 확인).",
    "NO_FIXED_CARD": "기믹에 필요한 고정 카드(pass_terminal / break_free)가 Cards.csv 에 없습니다.",
    "BAD_GIMMICK": "알 수 없는 기믹입니다.",
}

# -- 공개 메시지 -------------------------------------------------------------
LOBBY = "## 🃏 카드 전투 — 참가 모집 중\n`/카드 옵션:참가` 로 참가하세요. 주최자가 `/카드 옵션:시작 전투id:<id>` 로 시작합니다.\n**참가자 ({n}명)**: {names}"
LOBBY_EMPTY = "아직 없음"
INTRO = "## ⚔️ {battle}\n{desc}\n**적**: {enemies}\n**아군**: {players}"
TURN_OPEN = "## 🃏 {round}라운드\n아래 **카드 뽑기** 버튼으로 패를 확인하고 카드를 고르세요.\n**선택 현황: {done}/{total} 완료**\n{marks}\n{intents}\n{board}"
MARK_DONE = "✅"
MARK_TODO = "⬜"
MARK_OUT = "💀"
MARK_STUN = "💫"
MARK_GRAPPLED = "🪢"
REPORT_HEADER = "### ⚔️ {round}라운드 정산"
BOARD_ALLY = "🟢 아군: {units}"
BOARD_ENEMY = "🔴 적: {units}"
BOARD_UNIT = "{name} {hp}/{max}{tags}"
BOARD_DEAD = "{name} 💀"
WIN_ALLY = "## 🏆 승리! 적을 모두 제압했습니다."
WIN_ALLY_CHIP = "## 🏆 돌의 인격 칩이 리부트되어 멈췄습니다. 비살상 승리!"
WIN_ALLY_DOLL_HP = "## 🏆 돌을 쓰러뜨렸습니다… 보느와: \"야, 사람이야! 좀!\""
WIN_ENEMY = "## 💀 패배… 아군이 모두 쓰러졌습니다."
WIN_ESCAPED = "## 🚨 → 🌃 탈출 성공! NCPD 드론이 골목을 지나쳐 갔습니다."
WIN_CAUGHT = "## 🚨 드론의 서치라이트가 당신들을 비춥니다. (RP 로 이어집니다)"
NCPD_TRIGGER = "## 🚨 NCPD 드론 출동!\n경계도가 가득 찼습니다. 스캐빈저들이 흩어지고, 여러분도 흩어져야 합니다.\n**도주 단계**: {turns}턴 동안 경계도를 {alert}에서 0 으로 내리세요. 매 턴 **과반수**가 경찰 행동을 이기면 경계도 −1."
STATUS = "**상태**: {status} / {round}라운드\n**선택 현황**: {done}/{total}\n{board}"

INTENT_HEADER = "**적 의도**"
INTENT_LINE = "• {name}: {intent}{target}"
INTENT_TARGET = " → {target}"
METER_FIREWALL = "🧱 방화벽 {value}/{max}"
METER_NCPD = "🚨 NCPD 경계도 {value:g}/{max:g}"
METER_CHIP = "💾 칩 안정도 {value}/{max}"
METER_SCREEN = "📱 액정 {value}/{max} (소지자: {holder})"
METER_MODE = "🎭 돌: {mode}"

# -- 로그 줄 -----------------------------------------------------------------
# HP 는 대상의 것. 대상이 자신이면 이름을 생략한다.
LOG_USE = "{actor}{josa} **{card}**{eul} 사용했습니다."
LOG_TARGET = " {target} HP {hp}/{max}{detail}"
LOG_SELF = " HP {hp}/{max}{detail}"
LOG_MISFIRE = "{actor}{josa} **{card}**{eul} 사용했지만 불발되었습니다."
LOG_EVADE = "{actor}{josa} **{card}**{eul} 사용했지만 {target}{eun2} 피했습니다."
LOG_NO_TARGET = "{actor}{josa} **{card}**{eul} 사용했지만 대상이 없었습니다."
LOG_WAIT = "{actor}{eun} 대기했습니다."
LOG_STUNNED = "{actor}{eun} 기절해서 행동하지 못했습니다."
LOG_DOWN = "{actor}{eun} 쓰러져 있습니다."
LOG_REBOOT = "{actor}{eun} 리부트 중입니다… (칩 슬롯에서 스파크가 튑니다)"
LOG_REDIRECT_PREFIX = "(적대 인식!) "
LOG_COMBO_PREFIX = "⚡콤보! "
LOG_KILLED = " 💀"
LOG_OVERKILL = " ⚠과잉 제압"
LOG_WEAK = " (싼 의체!)"
LOG_REVIVED = " ✨일어남"
LOG_SCREEN = " 📱액정 {screen}"
LOG_RELEASED = " 🪢{names} 풀려남"
LOG_CHIP = " 💾칩 −{amount} ({value}/{max})"
LOG_CHIP_CAPPED = " (라운드 상한)"
DETAIL = {
    "damage": " (-{amount})",
    "self_damage": " (반동 -{amount})",
    "heal": " (+{amount})",
    "shield": " (이번 라운드 피해 {pct}% 감소)",
    "atk_down": " (공격력 {pct}% 감소, {turns}라운드)",
    "misfire": " (다음 공격 {pct}% 불발)",
    "redirect_random": " (다음 공격 적대 인식)",
    "stun": " (기절)",
    "grapple": " (붙잡힘!)",
    "break_free": " (떨쳐냄)",
    "pass_terminal": " (단말기를 받았습니다)",
    "evade": "",
    "stun_immune": " (돌은 기절하지 않습니다 — 리셋 명령이 칩에 직접 들어갑니다)",
}
TAG = {"shield": "🛡", "atk_down": "⬇", "misfire": "❓", "redirect_random": "🔀", "stun": "💫", "grapple": "🪢"}

# 이벤트 (방화벽·경계도·끌고 가기)
EVENT_FIREWALL_USE = "🧱 {by}님이 방화벽 **{option}** 사용 (−{cost}){detail}"
EVENT_FIREWALL_DETAIL = {
    "cover": " — 아군 전원 이번 라운드 피해 감소",
    "reboot": " — 디버프 해제: {targets}",
    "aim": " — 이번 라운드 아군 공격 피해 고정, 회피 무시",
    "scan": " — 적 의도의 대상 공개",
    "maxdoc": " — {targets} +{amount}{revived}",
}
EVENT_NCPD = "🚨 경계도 +{amount:g} ({why}: {who}) → {value:g}/{max:g}"
NCPD_WHY = {"overkill": "과잉 제압", "loud": "소음"}
EVENT_DRAG_START = "🩸 {by}{josa} 쓰러진 {player}님을 끌고 가기 시작합니다! ({need}라운드 안에 막으세요)"
EVENT_DRAG = "🩸 {by}{josa} {player}님을 끌고 갑니다… ({turns}/{need})"
EVENT_DRAG_STOP = "🩸 {player}님을 끌고 가던 {by}{josa} 쓰러져 멈췄습니다."
EVENT_REGEN = "🩹 {name}의 의체가 자가 복구합니다 (+{amount}) HP {hp}/{max_hp}"
EVENT_LOOTED = "💔 {player}님이 골목 안으로 끌려갔습니다. 의체를 뜯길 겁니다… (약탈)"

# -- 도주 단계 -------------------------------------------------------------
ESCAPE_ACTION_KO = {"hide": "숨기", "move": "이동하기", "dead": "죽은 척하기"}
POLICE_KO = {"light": "서치라이트", "thermal": "열감지 스캔", "callout": "확성기 검문"}
ESCAPE_RULE = ("숨기 ▶ 서치라이트 / 이동하기 ▶ 열감지 / 죽은 척 ▶ 확성기 검문 (▶ = 이김). "
               "숨기 ◀ 열감지 / 이동 ◀ 확성기 / 죽은 척 ◀ 서치라이트 (◀ = 짐). 나머지는 무승부.")
ESCAPE_OPEN = "## 🚨 도주 {turn}/{max}턴 — 경계도 {alert}/{alert_max}\n아래 버튼으로 행동을 고르세요. **과반수**가 이기면 경계도가 1 내려갑니다.\n{rule}\n{hint}\n**선택 현황: {done}/{total}**\n{marks}"
ESCAPE_REPORT_HEADER = "### 🚨 도주 {turn}턴 — 경찰: **{police}**"
ESCAPE_HINT = "📡 보느와: \"드론 통신을 엿들었어. 이번엔 **{hint}**은(는) 안 쓸 거야.\""
ESCAPE_NO_HINT = "📡 보느와: \"통신 감청 실패. 감으로 가자.\""
ESCAPE_LINE = "{mark} {name}: {action} ({outcome})"
ESCAPE_OUTCOME_KO = {"win": "이김", "lose": "짐", "tie": "무승부", "none": "선택 없음"}
ESCAPE_OUTCOME_MARK = {"win": "🟢", "lose": "🔴", "tie": "⚪", "none": "⬜"}
ESCAPE_TURN_WON = "**턴 승리!** ({wins}/{n}) 경계도 {alert}/{alert_max}"
ESCAPE_TURN_LOST = "턴 실패 ({wins}/{n}) 경계도 {alert}/{alert_max}"

# -- ephemeral 선택창 -------------------------------------------------------
HAND_TITLE = "**{round}라운드 — 당신의 패**{admin}\n{cards}"
HAND_ADMIN = " (주최자: 전체 카드)"
HAND_CARD = "• **{name}** [{kind}·{target}] {desc}"
HAND_DMG = "약 {lo}~{hi}"
HAND_PLACEHOLDER = "카드를 고르세요"
HAND_MORE = "다음 ▶ ({page}/{pages})"
TARGET_TITLE = "**{card}** — 대상을 고르세요"
TARGET_PLACEHOLDER = "대상"
TARGET_OPTION = "{name} (HP {hp}/{max})"
FIREWALL_TITLE = "🧱 보느와의 방화벽 — 게이지 {value}/{max}. 라운드에 한 번."
FIREWALL_PLACEHOLDER = "사용할 지원"
FIREWALL_OPTION_KO = {"cover": "팀 엄폐", "reboot": "긴급 재부팅", "aim": "스마트 탄 유도", "scan": "적 스캔", "maxdoc": "맥스닥"}
FIREWALL_OPTION_DESC = {
    "cover": "아군 전원 이번 라운드 피해 {pct}% 감소",
    "reboot": "아군 전원의 기절·불발·적대 인식·글리치·붙잡힘 해제",
    "aim": "이번 라운드 아군 공격 피해 난수 0, 회피 무시 (비살상 마무리용)",
    "scan": "이번 라운드 적 의도의 대상 공개",
    "maxdoc": "가장 위험한 아군 HP {pct}% 회복 (쓰러진 아군도 일으킴)",
}
FIREWALL_COST = " (비용 {cost})"
BTN_BACK = "◀ 뒤로"
BTN_REPICK = "다시 고르기"
BTN_CANCEL = "선택 취소"
BTN_DRAW = "카드 뽑기"
BTN_FIREWALL = "방화벽"
