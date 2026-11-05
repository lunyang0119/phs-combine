"""카드 전투 — 플레이어에게 보이는 문자열 전부.

전투 중 안내(INTRO_RULES, TIPS, 코치·보느와 전투 문구)는 이 파일만 고치면 말투를 바꿀 수 있다.
"""
from __future__ import annotations

KIND_KO = {"attack": "공격", "defend": "방어", "hack": "해킹", "special": "특수"}
TARGET_KO = {"enemy": "적 1명", "ally": "아군 1명", "self": "자신", "all_enemy": "적 전체", "all_ally": "아군 전체"}
SIDE_KO = {"ally": "아군", "enemy": "적"}
# 시트 열 이름은 그대로, 화면에만 사이버펑크식 이름을 쓴다.
STAT_KO = {"physics": "근력", "magic": "RAM", "agility": "민첩", "charm": "매력", "none": "-"}
JOB_KO = {"스트라이커": "엣지러너", "마테리아 위버": "넷러너", "솔져": "솔져", "턱스": "턱스"}
MODE_KO = {"cling": "접객 모드", "rampage": "폭주 모드", "reboot": "리부트",
           "seize": "장악 모드", "breach": "침입 모드", "overload": "과부하 모드"}
MODE_DESC = {
    "cling": "단말기 소지자에게 매달린다 (붙잡힘 + 긁힘)",
    "rampage": "손톱으로 두 번 긋는다",
    "reboot": "행동하지 않는다. 이번 라운드 해킹은 칩 안정도를 2배로 깎는다",
    "seize": "ICE 한 층을 다시 세우고 아군 둘에게 데몬을 심는다",
    "breach": "단말기 보느와의 방화벽을 깎고 한 사람의 다음 공격을 적대 인식으로 돌린다",
    "overload": "전원에게 RAM 피해. 대신 ICE 가 전부 내려가고 생명 유지 시계가 하나 더 줄어든다",
}
INTENT_KO = {"attack": "공격 준비", "defend": "방어 준비", "hack": "해킹 준비", "special": "특수 준비",
             "stunned": "기절 (행동 불가)", "wait": "대기", "mode": "{label}"}
TAG_KO = {"loud": "소음", "lethal": "살상", "nonlethal": "비살상"}
COMBO_MOD_KO = {"stun": "기절한", "atk_down": "무기 글리치 상태의", "misfire": "사이버웨어 고장 상태의",
                "redirect_random": "적대 인식 상태의", "grapple": "붙잡고 있는", "shield": "엄폐 중인", "daemon": "감염된"}
COMBO_TEXT = {"dmg_x": "피해 ×{v}", "dmg_add": "피해 +{v}", "chip_add": "칩 안정도 추가 -{v}"}
COMBO_LINE = "▶ 콤보: {cond}에게 {bonus}"
COMBO_SELF_LINE = "▶ 콤보: 자신이 {cond} 상태면 {bonus}"
BOSS_STATE_KO = {"fine": "멀쩡", "hurt": "부상", "critical": "위독"}

# -- 짧은 도움말 (`/카드 도움말:True`, 옵션 목록과 합쳐 2000자 이내) -------------------
HELP = (
    "**30초 설명**: 여러분은 한 팀, 적도 한 팀. 매 라운드 **모두가 동시에 카드 한 장**을 냅니다. "
    "적이 뭘 하려는지는 **종류만** 미리 보입니다.\n"
    "**흐름**: `/카드 옵션:주최` → 모두 `옵션:참가` → 주최자 `옵션:시작 전투id:<id>` → (준비 단계: 무기 줍기·개조) → "
    "라운드마다 [카드 뽑기] 버튼 → 3장 중 한 장 + 대상 → 전원 고르면 자동 정산.\n"
    "**규칙 셋**: 해킹 → 방어 → 공격 순으로 정산 (준비 동작이 먼저, 한 방은 마지막). "
    "방어는 이번 라운드까지, 해킹은 적의 다음 행동까지. 콤보는 카드 설명의 ▶ 줄.\n"
    "**방화벽**: 맞거나 방어하면 차는 팀 게이지. [방화벽] 버튼으로 라운드에 한 번.\n"
    "자세한 규칙은 `/카드 옵션:규칙`."
)

# -- 긴 도움말 (`/카드 옵션:규칙`, 섹션마다 임베드 하나) ----------------------------
HELP_SECTIONS = [
    ("30초 설명",
     "여러분은 한 팀입니다. 적도 한 팀입니다. 매 라운드 **모두가 동시에 카드 한 장**을 냅니다. "
     "적이 뭘 하려는지는 **종류만** 미리 보입니다. 공격인지, 해킹인지, 방어인지는 알지만 누구를 노리는지는 모릅니다. "
     "그걸 보고 패 3장 중 한 장을 고르면 됩니다. 끝입니다."),
    ("한 라운드 흐름",
     "1. 적의 의도가 뜹니다. 예: `스캐빈저 A: 공격 준비`, `스캐빈저 B: 해킹 준비`\n"
     "2. **[카드 뽑기]** 버튼을 누르면 나만 보이는 패 3장이 뜹니다.\n"
     "3. 한 장을 고르고, 필요하면 대상을 찍습니다.\n"
     "4. 모두 고르면 자동으로 정산됩니다. 결과는 공개 채널에 한 줄씩 올라옵니다.\n"
     "5. 다음 라운드. 적 HP가 0이 되거나 우리가 쓰러지면 끝.\n\n"
     "안 고르면 그 라운드는 \"대기\"입니다. 아무 일도 안 합니다. 시간 제한은 없지만 다들 기다립니다."),
    ("카드는 네 종류",
     "⚔ **공격**: 적 HP를 깎습니다. 카드에 `예상 피해 16~20` 같은 숫자가 적혀 있습니다.\n"
     "🛡 **방어**: 이번 라운드에 받는 피해를 줄입니다. 나 또는 아군에게.\n"
     "💻 **해킹**: 적을 방해합니다. 무기 글리치(다음 공격 약화), 사이버웨어 고장(다음 공격 불발), 적대 인식(아군끼리 공격), "
     "시스템 리셋(기절). 넷러너의 퀵핵이 이겁니다.\n"
     "✨ **특수**: 강한 한 방, 회복(맥스닥 흡입기) 등.\n\n"
     "내 직업에 따라 잘 나오는 카드가 다릅니다. 엣지러너·솔져는 근력 카드가, 넷러너는 RAM 카드가 셉니다. "
     "남의 카드를 들어도 되는데 약합니다."),
    ("기억할 규칙은 세 개",
     "1. **준비 동작이 먼저, 한 방은 마지막.** 한 라운드 안에서 해킹이 가장 먼저, 그다음 방어, 공격은 맨 끝에 들어갑니다. "
     "그래서 적이 \"공격\"을 예고했을 때 내가 시스템 리셋으로 기절시키면 그 공격은 나오지 않습니다. "
     "방어도 같은 라운드 공격보다 먼저 서니까 예고를 보고 막을 수 있습니다. 같은 단계 안에서는 민첩이 높은 쪽이 먼저입니다.\n"
     "2. **방어는 이번 라운드까지, 해킹은 적의 다음 행동까지.** 미리 걸어두면 다음 라운드 첫 행동에서 터집니다.\n"
     "3. **콤보**는 카드 설명에 적혀 있습니다. 예: 기절한 적을 근접 타격하면 피해 1.5배. "
     "누가 기절시키고 누가 때릴지 채팅으로 짜면 됩니다."),
    ("보느와의 방화벽 (팀 게이지)",
     "우리가 맞을수록, 방어 카드를 낼수록 게이지가 찹니다. 라운드에 한 번, 아무나 **[방화벽]** 버튼으로 쓸 수 있습니다. "
     "어떤 옵션이 있는지는 전투마다 다르고, 버튼을 누르면 지금 쓸 수 있는 것만 보입니다. 처음엔 세 개입니다.\n\n"
     "• 팀 엄폐: 이번 라운드 전원 피해 절반\n"
     "• 긴급 재부팅: 아군의 나쁜 상태 전부 해제 (데몬 감염도)\n"
     "• 맥스닥: 제일 위험한 아군 회복, 쓰러진 사람도 일으킴\n"
     "• (나중에 열림) 적 스캔: 적 의도의 대상 공개 / 스마트 탄 유도: 이번 라운드 아군 피해 난수 0, 회피 무시"),
    ("전투 전 준비 단계 (3~5분)",
     "• 각자 **무기를 하나 줍습니다.** 낡았습니다. mk.1 아니면 mk.2. 등급이 높을수록 내 공격 카드 피해가 올라갑니다 "
     "(mk.1 +0 / mk.2 +3 / mk.3 +6 / mk.4 +10). 전투가 끝나면 부서집니다.\n"
     "• 팀 금고의 **에디**가 똑같이 나뉘어 옵니다.\n"
     "• 버튼 두 개 중 고릅니다. 1인당 2번까지.\n"
     "　**정식 개조** (100): 비쌉니다. 무조건 성공. mk.3까지.\n"
     "　**뒷골목 개조** (50): 반값. 주사위를 굴립니다. mk.1→2는 무조건 성공, 그 위는 10면체로 4 이상이면 성공, "
     "3 이하면 **한 단계 내려갑니다** (1이면 두 단계, 10이면 성공에 환불). mk.4는 이 길밖에 없습니다.\n"
     "• 굴림 결과는 모두가 봅니다. 구경하세요.\n"
     "• 전투 중에도 금고에 돈이 남았으면 패에 **현장 개조** 카드가 들어옵니다. 이번 라운드 행동을 포기하고 굴립니다. "
     "실패해도 등급은 안 깎이고 다음 라운드 내 무기가 불발입니다.\n"
     "• 전투가 끝나면 보수가 금고에 들어옵니다. 다음 보스전 준비 자금입니다. 과잉 제압과 소음만큼 깎입니다."),
    ("보스전 1: 스캐빈저 (퍼시피카 뒷골목)",
     "보느와의 부탁: **죽이지 마.** 그래서 규칙이 하나 붙습니다.\n"
     "• 남은 HP보다 **훨씬 세게** 때려서 쓰러뜨리면 \"과잉 제압\"입니다. NCPD 경계도가 오르고 보수가 깎입니다. "
     "걱정 마세요. 카드 고를 때 위험하면 **⚠ 과잉 위험** 표시가 뜹니다. 그 카드 대신 다른 카드나 관절 치기(절대 과잉이 안 됨)를 쓰면 됩니다.\n"
     "• 제압 사격처럼 시끄러운 카드도 경계도를 조금 올립니다.\n"
     "• 경계도가 꽉 차면 드론이 옵니다. 그때 규칙은 그때 화면에 뜹니다. 보통은 안 옵니다.\n"
     "• 아군이 쓰러지면 스캐빈저가 **끌고 갑니다.** 두 라운드 안에 그놈을 쓰러뜨리거나 아군을 일으키세요. 못 하면 의체를 털립니다."),
    ("보스전 2: 고장난 돌",
     "적은 한 명인데 아주 셉니다. 그리고 단말기를 든 사람만 노립니다.\n"
     "• 돌 위에 **칩 안정도**가 있습니다. 해킹이 맞을 때마다 내려가고, 0이 되면 돌이 멈춥니다. HP를 깎아 쓰러뜨려도 이기지만, 보느와가 싫어합니다.\n"
     "• 돌은 라운드마다 **모드**를 공개합니다. 매달리기(단말기 든 사람을 붙잡음), 폭주(세게 두 번 때림), "
     "리부트(가만히 있지만 이때 해킹이 **두 배**로 먹힘). 리부트 때 해킹을 몰아넣으세요.\n"
     "• 붙잡힌 사람은 **떨쳐내기**만 할 수 있습니다. 다른 아군이 돌을 때려도 풀립니다.\n"
     "• 단말기 든 사람의 패에는 항상 **단말기 넘기기**가 있습니다. 위험하면 넘기세요. 단말기 든 사람이 맞으면 액정이 깨집니다. 액정은 세 번 버팁니다.\n"
     "• 돌은 기절하지 않습니다. 대신 시스템 리셋이 칩을 더 깎습니다. 물리 공격은 절반이 빗나갑니다. 해킹이 답입니다."),
    ("보스전 3: 몸의 보느와",
     "방에 들어서자마자 싸움이 시작됩니다. 그는 올 줄 알고 있었습니다. **첫 라운드는 그만 움직입니다.** 셋이 다치고 하나가 감염된 채 시작합니다.\n"
     "• **데몬 감염** : 감염된 채로 카드를 내면 스택당 5 자해. 다음 라운드 끝까지 남고, 겹치면 스택이 쌓입니다. "
     "보느와의 ICE(한 명)나 긴급 재부팅(전원)으로 지웁니다. 한 번에 넷까지만 감염됩니다.\n"
     "• **블랙 ICE**: 그를 감싼 ICE 층마다 받는 피해가 30% 줄어듭니다. 아군 해킹이 맞을 때마다 한 층 벗겨집니다 (라운드에 두 층까지). "
     "물리 공격은 30% 빗나갑니다. 해킹으로 열고, 공격으로 끝냅니다.\n"
     "• **방화벽 공방**: 그의 해킹이 단말기 보느와의 방화벽을 깎거나 한 라운드 잠급니다. 아끼다 뺏기지 말고 쓰세요.\n"
     "• **생명 유지 시계**: 라운드마다 하나씩 줍니다. 0이 되면 그가 발작으로 쓰러지고 전투가 끝납니다. 이기긴 하지만 \"위독\"이고 보수가 반입니다.\n"
     "• **모드**: 장악(ICE 복구 + 감염 둘), 침입(방화벽 공격 + 적대 인식), 과부하(전원 RAM 피해. 대신 ICE 전부 내려가고 시계 −1). 과부하 다음이 기회입니다.\n"
     "• 그는 약합니다. ⚠ 과잉 위험이 뜨는 카드로 끝내면 \"위독\", 그냥 쓰러뜨리면 \"부상\", 관절 치기로 끝내면 \"멀쩡\". 4장은 그가 살아 있어야 이어집니다."),
    ("막히면",
     "• 패에서 카드를 누르면 상세 설명이 뜹니다. 예상 피해, 콤보, ⚠ 경고가 거기 있습니다.\n"
     "• 뭐가 왜 일어났는지는 정산 보고의 💡 줄을 보세요. 그 전투에서 처음 일어난 일은 한 줄로 설명해 줍니다.\n"
     "• 처음이면 `/카드 옵션:시작 전투id:tut` 튜토리얼 전투로 여섯 라운드 연습할 수 있습니다.\n"
     "• 그래도 모르겠으면 주최자에게 물으세요. 주최자는 `/카드 옵션:턴종료` 로 라운드를 넘기거나 `옵션:종료` 로 끝낼 수 있습니다."),
]

# -- 무기 이름 (시트 직업명 키, "" 는 기본) ------------------------------------------
WEAPON_NAMES = {
    "스트라이커": ["녹슨 마체테", "테크 리볼버", "낡은 산탄총", "접이식 카타나", "중고 파워 피스톨"],
    "마테리아 위버": ["구형 사이버덱", "불법 개조 덱", "중고 모노와이어", "땜질한 서브덱", "해적판 퀵핵 칩"],
    "솔져": ["낡은 돌격소총", "녹슨 파이프 렌치", "폐품 기관단총", "군용 리볼버", "찌그러진 방패"],
    "": ["주운 권총", "녹슨 쇠파이프", "싸구려 나이프"],
}

# -- 전투 소개의 규칙 칸 (기믹별 한 줄) ------------------------------------------------
INTRO_RULES = {
    "nonlethal": "비살상: 남은 HP 를 {margin} 이상 초과해 쓰러뜨리면 과잉 제압. 카드를 고를 때 ⚠ 과잉 위험 표시를 봐!",
    "ncpd": "NCPD 경계도가 {ncpd_max:g} 에 닿으면 드론이 출동해! 도주하기 싫으면 조심하라고!",
    "drag": "{drag_turns}라운드 안에 끌고 가는 적을 제압해야해! 그렇지 않으면 쓰러진 아군이 약탈당하니까.",
    "doll": "돌의 인격 칩: 해킹 명중마다 안정도 −1 (리부트 중 ×2). 0 이면 비살상 승리야. 물리 공격은 50%의 확률로 회피한다고, 잘 노려!",
    "terminal": "이런 씨발! 나 들고 있는 ({holder})(이)가 돌의 표적이야. 소지자가 맞으면 액정 −1 되니까, 공격 받기 전에 '단말기 넘기기'로 넘기라고!!",
    "first_strike": "선제 라운드: ...쟤가 먼저 움직였어...! 셋이 다치고 하나가 감염된 채 1라운드가 시작될거야.",
    "daemon": "감염: 감염된 채 행동하면 다음 라운드 끝까지 스택당 {daemon_damage} 피해를 받아. ICE·긴급 재부팅으로 해제가 가능해! (동시 최대 {daemon_max}명)",
    "firewall_duel": "방화벽 공방: 적의 해킹이 단말기 저새끼의 방화벽을 깎거나 한 라운드 잠궈",
    "life_support": "생명 유지 시계 {life}: 라운드마다 −1. 0 이면 저 녀석이 발작으로 쓰러져. 보수도 절반으로 줄어드니까 조심하라고.",
    "ice": "블랙 ICE {ice_layers}층: 층마다 받는 피해가 −{ice_pct}% 줄어. 아군 해킹 명중마다 한 층이 줄어들어. 참고로, 라운드 당 {ice_cap}층까지만 줄어들어!",
    "tutorial": "튜토리얼: 라운드마다 코치가 과제를 냅니다. 실패해도 다음 라운드로 넘어갑니다",
    "prep": "준비 단계: 주운 무기(mk.{wmin}~{wmax_start})를 에디로 개조해. 정식 {price_normal} 에디 / 뒷골목 {price_black} 에디고, 1인 당 {actions}회까지. 전투 중 현장 개조는  {field_cost}에디.",
    "firewall": "보느와의 방화벽: 맞거나 방어하면 차는 팀 게이지야. 턴 메시지의 버튼으로 라운드에 한 번만. {options}",
}
INTRO_FOOTER = "준비 동작이 먼저, 한 방은 마지막: 해킹 → 방어 → 공격 순, 같은 단계는 민첩 순으로 움직여."

# -- 💡 문맥 팁 (전투에서 처음 일어났을 때 한 번) ------------------------------------------
TIPS = {
    "hack_cancel": "💡 기절이 공격보다 먼저 정산돼서 예고된 공격이 취소됐네. 해킹은 늘 먼저 해.",
    "shield": "💡 방어가 같은 라운드 공격보다 먼저 서서 피해가 줄었어. 방어는 이번 라운드까지만이야.",
    "combo": "💡 ⚡콤보! 카드 설명의 ▶ 조건이 맞으면 보너스가 붙거든.",
    "misfire": "💡 사이버웨어 고장 상태 때문에 공격이 불발됐네. 고장은 다음에 공격 맞으면 고쳐져.",
    "redirect": "💡 적대 인식에 걸린 공격이 엉뚱한 대상에게 갔네. 아군끼리 맞을 수도 있겠다. 내가 다치지 않게 조심하라고!",
    "evade": "💡 물리 공격이 빗나갔네. 회피하는 적에게는 해킹이나 스마트 탄 유도가 답이지.",
    "firewall_ready": "💡 방화벽 게이지가 쓸 만큼 찼어. 턴 메시지의 [방화벽] 버튼으로 써봐. 라운드에 한 번만 사용 가능하니까 조심하고.",
    "overkill": "💡 ⚠과잉 제압. 남은 HP 를 크게 초과해 쓰러뜨렸어. 카드 상세의 ⚠ 표시를 보고 약한 카드로 끝내면 좋겠지. NCPD가 오니까!.",
    "drag": "💡 쓰러진 아군을 끌고 가고 있어! 끌고 가는 적을 제압하거나 맥스닥으로 일으키라고!",
    "grapple": "💡 붙잡혔네. 붙잡힌 사람은 떨쳐내기만 할 수 있어. 아, 아군이 돌을 맞혀도 풀려.",
    "chip": "💡 해킹이 돌의 칩 안정도를 깎았어! 0 이 되면 비살상 승리야 (리부트 중엔 두 배).",
    "daemon": "💡 데몬 감염: 감염된 채 행동하면 스택당 피해를 입어. 나의 ICE 나 긴급 재부팅으로 지워.",
    "daemon_bite": "💡 감염된 채 행동해서 데몬이 물었어. 다음 라운드 끝에 저절로 사라질거야.",
    "ice_strip": "💡 해킹이 ICE 한 층을 벗겼어. 층이 남아 있는 동안 공격은 거의 안 들어가!.",
    "exposed": "💡 ICE 가 전부 내려갔어. 지금이 공격할 때야! 아, 근데 ⚠ 과잉 위험은 꼭 보고!",
    "life_low": "💡 생명 유지 시계가 얼마 안 남았어. 0 이 되면 저 놈이 쓰러지고 '위독'으로 끝나!",
    "field_mod": "💡 현장 개조는 행동 하나를 쓰거든. 실패해도 등급은 그대로지만, 다음 공격은 불발되니까 조심하고.",
}

# -- 명령 응답 ---------------------------------------------------------------
OK_HOSTED = "카드 전투 세션을 열었습니다. 참가자는 `/카드 옵션:참가`, 시작은 `/카드 옵션:시작 전투id:<id>`.\n(주최자인 당신은 패 대신 전체 카드에서 고를 수 있습니다.)"
OK_JOINED = "{name}님 참가 완료 ({n}명)."
OK_STARTED = "전투 시작: {battle}"
OK_STARTED_PREP = "전투 시작: {battle}\n준비 단계가 열렸습니다. 전원이 **준비 완료**를 누르거나 `/카드 옵션:턴종료` 로 넘어갑니다."
OK_FORCED = "라운드를 강제 정산했습니다."
OK_FORCED_PREP = "준비 단계를 닫고 전투를 시작합니다."
OK_ENDED = "카드 전투 세션을 종료했습니다."
OK_CHOSEN = "선택 완료: **{card}**{target}\n정산 전까지 아래 버튼으로 바꾸거나 취소할 수 있습니다."
OK_CANCELLED = "선택을 취소했습니다. 다시 고르세요."
OK_FIREWALL = "방화벽 사용: **{option}** (−{cost}, 남은 게이지 {value})"
OK_ESCAPE_CHOSEN = "선택 완료: **{action}**. 정산 전까지 다른 버튼으로 바꿀 수 있습니다."
OK_PREP_NORMAL = "정식 개조 완료: **{weapon}** (mk.{before} → mk.{after}). 남은 에디 {allowance}, 남은 횟수 {actions_left}"
OK_PREP_BLACK_WIN = "뒷골목 개조 d10 = **{roll}** → 성공! **{weapon}** (mk.{before} → mk.{after}){refund}. 남은 에디 {allowance}, 남은 횟수 {actions_left}"
OK_PREP_BLACK_AUTO = "뒷골목 개조: mk.1 → mk.2 는 굴림 없이 성공. **{weapon}**. 남은 에디 {allowance}, 남은 횟수 {actions_left}"
OK_PREP_BLACK_FAIL = "뒷골목 개조 d10 = **{roll}** → 실패… **{weapon}** (mk.{before} → mk.{after}). 남은 에디 {allowance}, 남은 횟수 {actions_left}"
OK_PREP_REFUND = " (10! 환불)"
OK_PREP_READY = "준비 완료. 전원이 누르면 전투가 시작됩니다."
OK_PREP_ALREADY = "이미 준비 완료 상태입니다."
PREP_PUBLIC_ROLL = "🎲 {name}님 뒷골목 개조 d10 = **{roll}** → {result} **{weapon}**"
PREP_PUBLIC_WIN = "성공!"
PREP_PUBLIC_FAIL = "실패…"
OK_VAULT = "💰 팀 금고: **{pot}** 에디{battle}"
OK_VAULT_BATTLE = " (진행 중인 전투의 남은 금고 {left})"
OK_VAULT_SET = "💰 팀 금고를 **{pot}** 에디로 맞췄습니다."

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
ERR_NOT_PREP = "지금은 준비 단계가 아닙니다."
ERR_STALE_MESSAGE = "지난 라운드의 메시지입니다. 최신 메시지의 버튼을 쓰세요."
ERR_NOT_IN_GAME = "이 전투의 참가자가 아닙니다."
ERR_MODULE_OFF = "카드 전투 모듈이 꺼져 있습니다."
ERR_NOT_YOURS = "본인의 선택창만 조작할 수 있습니다."
ERR_VAULT_AMOUNT = "금액은 0 이상의 정수여야 합니다."

RULE_ERRORS = {
    "NOT_OPEN": "이미 정산된 라운드입니다.",
    "NOT_ESCAPE": "지금은 도주 단계가 아닙니다.",
    "NOT_PREP": "지금은 준비 단계가 아닙니다.",
    "NOT_ENDED": "아직 전투가 끝나지 않았습니다.",
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
    "FIREWALL_OFF": "이 전투에서는 열리지 않은 지원입니다.",
    "FIREWALL_LOCKED": "방화벽이 잠겼습니다. 이번 라운드에는 쓸 수 없습니다.",
    "PREP_READY": "이미 준비 완료를 눌렀습니다.",
    "PREP_NO_ACTIONS": "개조 횟수를 다 썼습니다.",
    "PREP_POOR": "에디가 부족합니다.",
    "WEAPON_MAX": "이미 최고 등급입니다.",
    "WEAPON_NORMAL_MAX": "정식 개조로는 더 올릴 수 없습니다. mk.4 는 뒷골목 개조뿐입니다.",
    "NO_PLAYERS": "참가자가 없습니다.",
    "NO_ENEMIES": "적이 없습니다.",
    "EMPTY_POOL": "한쪽이 뽑을 카드가 없습니다 (Cards.csv 의 pool 확인).",
    "NO_FIXED_CARD": "기믹에 필요한 고정 카드(pass_terminal / break_free / first_strike_cards)가 Cards.csv 에 없습니다.",
    "NO_SCRIPT": "튜토리얼 대본(Tutorial.csv)이 없습니다.",
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
AMBUSH_TITLE = "⚡ 기습!"
BOARD_ALLY = "🟢 아군: {units}"
BOARD_ENEMY = "🔴 적: {units}"
BOARD_UNIT = "{name} {hp}/{max}{tags}"
BOARD_DEAD = "{name} 💀"
WIN_ALLY = "## 🏆 승리! 적을 모두 제압했습니다."
WIN_ALLY_CHIP = "## 🏆 돌의 인격 칩이 리부트되어 멈췄습니다. 비살상 승리!"
WIN_ALLY_DOLL_HP = "## 🏆 돌을 쓰러뜨렸습니다…"
WIN_ALLY_BOSS = {
    "fine": "## 🏆 몸의 보느와가 주저앉았습니다. 숨은 고릅니다. (멀쩡)",
    "hurt": "## 🏆 몸의 보느와가 쓰러졌습니다. 피가 좀 납니다. (부상)",
    "critical": "## 🏆 몸의 보느와가 쓰러졌습니다… 생명 유지 장치의 경고등이 빨갛게 바뀝니다. (위독)",
}
WIN_ALLY_COLLAPSE = "## ⚠️ 몸의 보느와가 발작을 일으키며 쓰러졌습니다. 경고등이 빨갛습니다. (위독)"
WIN_TUTORIAL = "## 🎓 튜토리얼 끝. 이제 실전입니다."
WIN_ENEMY = "## 💀 패배… 아군이 모두 쓰러졌습니다."
WIN_ESCAPED = "## 🚨 → 🌃 탈출 성공! NCPD 드론이 골목을 지나쳐 갔습니다."
WIN_CAUGHT = "## 🚨 드론의 서치라이트가 당신들을 비춥니다. (RP 로 이어집니다)"
NCPD_TRIGGER = "## 🚨 NCPD 드론 출동!\n경계도가 가득 찼습니다. 스캐빈저들이 흩어지고, 여러분도 흩어져야 합니다.\n**도주 단계**: {turns}턴 동안 경계도를 {alert}에서 0 으로 내리세요. 매 턴 **과반수**가 경찰 행동을 이기면 경계도 −1."
STATUS = "**상태**: {status} / {round}라운드\n**선택 현황**: {done}/{total}\n{board}"

INTENT_HEADER = "**적 의도**"
INTENT_LINE = "• {name}: {intent}{target}"
INTENT_TARGET = " → {target}"
METER_FIREWALL = "🧱 방화벽 {value}/{max}"
METER_FIREWALL_LOCKED = " 🔒잠김"
METER_NCPD = "🚨 NCPD 경계도 {value:g}/{max:g}"
METER_CHIP = "💾 칩 안정도 {value}/{max}"
METER_SCREEN = "📱 액정 {value}/{max} (소지자: {holder})"
METER_MODE = "🎭 돌: {mode}"
METER_MODE_GENERIC = "🎭 {owner}: {mode}"
METER_ICE = "🧊 블랙 ICE {value}/{max}층"
METER_LIFE = "💓 생명 유지 {value}/{max}"
METER_POT = "💰 금고 {value} 에디"

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
LOG_FIELD_MOD_WIN = "{actor}{josa} **현장 개조** d10 = {roll} → 성공! {weapon} (−{cost} 에디{refund}, 금고 {pot})"
LOG_FIELD_MOD_FAIL = "{actor}{josa} **현장 개조** d10 = {roll} → 실패… {weapon} 그대로, 다음 공격 불발 (−{cost} 에디, 금고 {pot})"
LOG_FIELD_MOD_NONE = "{actor}{josa} **현장 개조**를 시도했지만 금고가 비었거나 더 올릴 수 없습니다."
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
LOG_ICE = " 🧊ICE −{amount} ({value}/{max})"
LOG_ICE_CAPPED = " 🧊ICE (라운드 상한)"
LOG_ICED = " 🧊"
LOG_DAEMON_BITE = " 데몬 −{amount} (HP {hp}/{max})"
DETAIL = {
    "damage": " (-{amount})",
    "self_damage": " (반동 -{amount})",
    "heal": " (+{amount})",
    "shield": " (이번 라운드 피해 {pct}% 감소)",
    "atk_down": " (다음 공격 {pct}% 감소)",
    "misfire": " (다음 공격 {pct}% 불발)",
    "redirect_random": " (다음 공격 적대 인식)",
    "stun": " (기절)",
    "grapple": " (붙잡힘!)",
    "break_free": " (떨쳐냄)",
    "pass_terminal": " (단말기를 받았습니다)",
    "daemon": " (감염 ×{amount})",
    "cleanse": " (감염 제거)",
    "field_mod": "",
    "evade": "",
    "stun_immune": " (돌은 기절하지 않습니다 — 리셋 명령이 칩에 직접 들어갑니다)",
}
TAG = {"shield": "🛡", "atk_down": "⬇", "misfire": "❓", "redirect_random": "🔀", "stun": "💫", "grapple": "🪢", "daemon": ""}

# 이벤트 (방화벽·경계도·끌고 가기·보느와)
EVENT_FIREWALL_USE = "🧱 {by}님이 방화벽 **{option}** 사용 (−{cost}){detail}"
EVENT_FIREWALL_DETAIL = {
    "cover": " — 아군 전원 이번 라운드 피해 감소",
    "reboot": " — 디버프·감염 해제: {targets}",
    "aim": " — 이번 라운드 아군 공격 피해 고정, 회피 무시",
    "scan": " — 적 의도의 대상 공개",
    "maxdoc": " — {targets} +{amount}{revived}",
}
EVENT_FIREWALL_DRAIN = "🧱💥 {by}{josa} 방화벽을 깎았습니다 (−{amount}) → {value}/{max}"
EVENT_FIREWALL_LOCK = "🧱🔒 {by}{josa} 방화벽을 잠갔습니다. 다음 라운드에는 쓸 수 없습니다."
EVENT_ICE = "🧊 {by}{josa} ICE 를 다시 세웁니다 (+{amount}) → {value}/{max}층"
EVENT_EXPOSE = "🧊💥 과부하! ICE 가 전부 내려갔습니다.{life}"
EVENT_EXPOSE_LIFE = " 생명 유지 {life}/{life_max}"
EVENT_LIFE = "💓 생명 유지 {value}/{max}"
EVENT_LIFE_LOW = "💓 생명 유지 {value}/{max} — 경고등이 노랗게 깜빡입니다"
EVENT_FIELD_MOD = ""   # 로그 줄에서 보여주므로 이벤트 줄은 비운다
EVENT_NCPD = "🚨 경계도 +{amount:g} ({why}: {who}) → {value:g}/{max:g}"
NCPD_WHY = {"overkill": "과잉 제압", "loud": "소음"}
EVENT_DRAG_START = "🩸 {by}{josa} 쓰러진 {player}님을 끌고 가기 시작합니다! ({need}라운드 안에 막으세요)"
EVENT_DRAG = "🩸 {by}{josa} {player}님을 끌고 갑니다… ({turns}/{need})"
EVENT_DRAG_STOP = "🩸 {player}님을 끌고 가던 {by}{josa} 쓰러져 멈췄습니다."
EVENT_REGEN = "🩹 {name}의 의체가 자가 복구합니다 (+{amount}) HP {hp}/{max_hp}"
EVENT_LOOTED = "💔 {player}님이 골목 안으로 끌려갔습니다. 의체를 뜯길 겁니다… (약탈)"

# -- 튜토리얼 코치 -------------------------------------------------------------
COACH_TITLE = "📘 코치 — {round}라운드"
TUTORIAL_OK = "✅ 과제 성공: {text}"
TUTORIAL_FAIL = "❌ 과제 실패: {text}"

# -- 준비 단계 ---------------------------------------------------------------
PREP_TITLE = "🔧 준비 단계 — 주운 무기와 에디"
PREP_DESC = ("각자 주운 무기를 에디로 개조합니다. **정식 개조** {price_normal} (확정, mk.{normal_max}까지) / "
             "**뒷골목 개조** {price_black} (d10: 4 이상 성공, 3 이하 한 단계 하락, 1은 두 단계, 10은 환불. mk.{wmax}의 유일한 길). "
             "1인 {actions}회. 전원 **준비 완료**를 누르면 시작합니다.")
PREP_POT_FIELD = "💰 금고"
PREP_POT_VALUE = "시작 {pot_start} 에디 → 1인당 {share} 분배, 남은 금고 {pot} (현장 개조 {field_cost})"
PREP_PLAYERS_FIELD = "무기"
PREP_ROW = "{mark} **{name}** — {weapon} · 에디 {allowance} · 남은 횟수 {actions_left}"
PREP_ROLLS_FIELD = "굴림 기록"
PREP_ROLL_NORMAL = "🔩 {name}: 정식 개조 mk.{before} → mk.{after}"
PREP_ROLL_BLACK = "🎲 {name}: d10 {roll} → mk.{before} → mk.{after}{refund}"
PREP_ROLL_AUTO = "🎲 {name}: mk.1 → mk.2 (자동 성공)"
PREP_RETURNED = "미사용 에디 {returned} 가 금고로 돌아갔습니다."
BTN_PREP_NORMAL = "정식 개조"
BTN_PREP_BLACK = "뒷골목 개조"
BTN_PREP_READY = "준비 완료"
PAYOUT_FIELD = "💰 보수"
PAYOUT_VALUE = "기본 {base} × {ratio:g} − 벌금 {penalty} (과잉 제압 {overkills}, 소음 {loud}) = **{reward}**\n남은 금고 {leftover} 반환 → 팀 금고 {pot_after}"
PAYOUT_SANDBOX = "(연습 전투: 보수 없음)"
WEAPONS_FIELD = "🔫 무기 (전투 뒤 부서짐)"
BOSS_STATE_FIELD = "몸의 보느와 상태"

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
HAND_RISK = "⚠ 과잉 위험: {names}"
HAND_WEAPON = " (무기 +{bonus})"
HAND_PLACEHOLDER = "카드를 고르세요"
HAND_MORE = "다음 ▶ ({page}/{pages})"
TARGET_TITLE = "**{card}** — 대상을 고르세요"
TARGET_PLACEHOLDER = "대상"
TARGET_OPTION = "{name} (HP {hp}/{max})"
TARGET_OPTION_RISK = "⚠ {name} (HP {hp}/{max}) 과잉 위험"
FIREWALL_TITLE = "🧱 보느와의 방화벽 — 게이지 {value}/{max}. 라운드에 한 번."
FIREWALL_LOCKED_NOTE = "🔒 이번 라운드에는 잠겨 있습니다."
FIREWALL_PLACEHOLDER = "사용할 지원"
FIREWALL_OPTION_KO = {"cover": "팀 엄폐", "reboot": "긴급 재부팅", "aim": "스마트 탄 유도", "scan": "적 스캔", "maxdoc": "맥스닥"}
FIREWALL_OPTION_DESC = {
    "cover": "아군 전원 이번 라운드 피해 {pct}% 감소",
    "reboot": "아군 전원의 기절·불발·적대 인식·글리치·붙잡힘·데몬 감염 해제",
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
