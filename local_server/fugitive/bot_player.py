"""디버그용 자동 헌터 — Gemini 가 헌터 명령을 결정한다.

헌터가 실제로 볼 수 있는 정보(현황판, 라운드 메시지, 지난 보고, 자기 위치)만 프롬프트에 넣는다.
도주자 정보는 PublicView 를 거치므로 구조적으로 들어갈 수 없다.
결정마다 이유(reason)를 함께 받아 관제 채널에 게시하고 GameState.bot_reasons 에 남긴다 (다음 프롬프트의 '이전 판단'용).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from . import engine as E
from . import render
from . import strings as S

logger = logging.getLogger(__name__)

BOT_PREFIX = "bot:"
DEFAULT_MODEL = os.getenv("FUGITIVE_GEMINI_MODEL", "gemini-3.7-flash")
# 기본 모델이 과부하(503)/할당량 초과(429)면 이 순서로 넘어간다
FALLBACK_MODELS = [m.strip() for m in os.getenv("FUGITIVE_GEMINI_FALLBACK_MODELS", "gemini-3.5-flash-lite,gemini-3.8-flash").split(",") if m.strip()]
RETRY_ATTEMPTS = int(os.getenv("FUGITIVE_GEMINI_RETRIES", "3"))
RETRY_BASE_SEC = float(os.getenv("FUGITIVE_GEMINI_RETRY_BASE_SEC", "2"))
ORDER_TYPES = ("move", "search", "scan", "stay")


def _is_transient(e: Exception) -> bool:
    s = str(e)
    return any(k in s for k in ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "high demand", "overloaded", "timed out"))


def is_bot(uid: str) -> bool:
    return uid.startswith(BOT_PREFIX)


def bot_hunters(n: int, start: int = 1) -> List[tuple]:
    """bot:N 헌터 n명. start 는 첫 번호 (이미 있는 봇 뒤에 이어 붙일 때)."""
    return [(f"{BOT_PREFIX}{i}", f"제미나이-{i}") for i in range(start, start + n)]


@dataclass
class Decision:
    order: str
    target: Optional[str]
    reason: str
    raw: str = ""
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# 프롬프트
# ---------------------------------------------------------------------------
def build_prompt(st: E.GameState, uid: str, past: List[Dict[str, Any]]) -> str:
    h = st.hunters[uid]
    gmap = st.game_map
    pv = E.public_view(st)
    table = render.table_text(pv)
    names = {u: x.name for u, x in st.hunters.items()}
    last = render.report_text(st.last_report, names) if st.last_report else "(아직 보고 없음 — 1라운드)"
    neighbors = ", ".join(f"{r}({S.DEVICE_KO[gmap.rooms[r].device]})" for r in gmap.rooms[h.room].neighbors)
    others = "\n".join(
        f"- {x.name}: {x.room}" + (f" → 제출: {render.order_label(x.order)}" if x.order else " (미제출)")
        for u, x in st.hunters.items() if u != uid
    ) or "(없음)"
    history = "\n".join(
        f"- R{p['round']}: {p['order']} {p.get('target') or ''} — {p['reason']}" for p in past[-4:]
    ) or "(없음)"
    tutorial = S.TUTORIAL.format(scan_budget=st.config["scan_budget"])
    return f"""당신은 열차 안에서 보이지 않는 침입자를 쫓는 승무원 '{h.name}' 입니다.
다른 승무원들과 협력해 침입자를 최대한 빨리 체포하세요.

[규칙]
{tutorial}

[현재 현황판]
{table}

[지난 라운드 보고]
{last}

[당신]
- 현재 위치: {h.room} ({S.DEVICE_KO[gmap.rooms[h.room].device]})
- 이동 가능한 인접 구역: {neighbors}
- 이번 라운드: R{st.round_no}

[다른 승무원]
{others}

[당신의 이전 판단]
{history}

이번 라운드의 행동을 하나 고르고, 왜 그렇게 판단했는지 한두 문장으로 설명하세요.
반드시 아래 JSON 형식으로만 답하세요.
{{"order": "move|search|scan|stay", "target": "이동할 구역 ID 또는 null", "reason": "판단 이유 (한국어)"}}
"""


# ---------------------------------------------------------------------------
# 응답 파싱
# ---------------------------------------------------------------------------
def parse_decision(text: str) -> Decision:
    raw = (text or "").strip()
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return Decision("stay", None, "응답에서 JSON 을 찾지 못해 대기", raw, "no_json")
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        return Decision("stay", None, f"JSON 파싱 실패로 대기 ({e})", raw, "bad_json")
    order = str(d.get("order", "stay")).strip().lower()
    if order not in ORDER_TYPES:
        return Decision("stay", None, f"알 수 없는 행동 '{order}' 로 대기", raw, "bad_order")
    target = d.get("target")
    target = str(target).strip().upper() if target not in (None, "", "null") else None
    reason = str(d.get("reason", "")).strip() or "(이유 없음)"
    return Decision(order, target if order == "move" else None, reason, raw)


# ---------------------------------------------------------------------------
# 플레이어
# ---------------------------------------------------------------------------
class GeminiPlayer:
    """헌터 한 명의 결정을 Gemini 에 묻는다. client 를 주입하면 테스트에서 네트워크 없이 쓸 수 있다."""

    def __init__(self, client: Any = None, model: str = DEFAULT_MODEL, api_key: Optional[str] = None):
        self.model = model
        self._client = client
        self._api_key = api_key if api_key is not None else os.getenv("GEMINI_API_KEY", "")

    @property
    def available(self) -> bool:
        return self._client is not None or bool(self._api_key)

    def _get_client(self):
        if self._client is None:
            from google import genai  # 지연 임포트: 키가 없거나 패키지가 없어도 cog 는 뜬다
            self._client = genai.Client(api_key=self._api_key)
        return self._client

    def _generate(self, prompt: str, model: str) -> str:
        client = self._get_client()
        try:
            from google.genai import types
            cfg = types.GenerateContentConfig(response_mime_type="application/json", temperature=0.8)
        except Exception:  # noqa: BLE001
            cfg = None
        resp = client.models.generate_content(model=model, contents=prompt, config=cfg)
        return resp.text or ""

    async def generate_with_retry(self, prompt: str, uid: str = "") -> str:
        """기본 모델 → 대체 모델 순으로, 일시적 오류(503/429)는 지수 백오프로 재시도한다."""
        last: Optional[Exception] = None
        for model in [self.model] + [m for m in FALLBACK_MODELS if m != self.model]:
            for attempt in range(RETRY_ATTEMPTS):
                try:
                    return await asyncio.to_thread(self._generate, prompt, model)
                except Exception as e:  # noqa: BLE001
                    last = e
                    if not _is_transient(e):
                        raise
                    wait = RETRY_BASE_SEC * (2 ** attempt)
                    logger.warning("Gemini %s 일시 오류 (%s, %d/%d) — %.0fs 후 재시도: %s",
                                   model, uid, attempt + 1, RETRY_ATTEMPTS, wait, str(e)[:120])
                    await asyncio.sleep(wait)
            logger.warning("Gemini %s 포기 (%s) — 대체 모델로 전환", model, uid)
        raise last if last else RuntimeError("Gemini 응답 없음")

    async def decide(self, st: E.GameState, uid: str) -> Decision:
        past = [r for r in st.bot_reasons if r["uid"] == uid]
        prompt = build_prompt(st, uid, past)
        try:
            text = await self.generate_with_retry(prompt, uid)
        except Exception as e:  # noqa: BLE001
            logger.warning("Gemini 호출 실패 (%s): %s", uid, e)
            return Decision("stay", None, f"Gemini 호출 실패로 대기: {str(e)[:200]}", "", "api_error")
        d = parse_decision(text)
        logger.info("자동 헌터 %s R%d → %s %s | %s", st.hunters[uid].name, st.round_no, d.order, d.target or "", d.reason)
        return d


def record_reason(st: E.GameState, uid: str, d: Decision, accepted: bool, note: str = "") -> None:
    st.bot_reasons.append({
        "round": st.round_no,
        "uid": uid,
        "name": st.hunters[uid].name,
        "order": d.order,
        "target": d.target,
        "reason": d.reason,
        "accepted": accepted,
        "note": note,
        "error": d.error,
    })


def decision_text(name: str, d: Decision) -> str:
    """관제 채널에 게시하는 선택 메시지 (행동 제출 직전에 보낸다)."""
    order = S.ORDER_KO.get(d.order, d.order) + (f" {d.target}" if d.target else "")
    return S.BOT_DECISION.format(name=name, order=order, reason=d.reason)


# ---------------------------------------------------------------------------
# 도주자 AI
# ---------------------------------------------------------------------------
FUGITIVE_UID = "fugitive"
FUGITIVE_NAME = "도주자-AI"


@dataclass
class FugitiveDecision:
    move: Optional[str]          # None = 대기
    hack: Optional[str]
    target: Optional[str]
    reason: str
    raw: str = ""
    error: Optional[str] = None


def build_fugitive_prompt(st: E.GameState, past: List[Dict[str, Any]]) -> str:
    f = st.fugitive
    gmap = st.game_map
    c = st.config
    pv = E.public_view(st)
    table = render.table_text(pv)
    names = {u: x.name for u, x in st.hunters.items()}
    last = render.report_text(st.last_report, names) if st.last_report else "(아직 보고 없음 — 1라운드)"
    speed = int(c.get("fugitive_speed", 1))
    reach = sorted(r for r in gmap.rooms if 0 < gmap.distance(f.room, r) <= speed)
    reach_s = ", ".join(f"{r}({S.DEVICE_KO[gmap.rooms[r].device]})" for r in reach)
    hunters = "\n".join(
        f"- {x.name}: {x.room} (거리 {gmap.distance(f.room, x.room)})"
        + (f" → 제출: {render.order_label(x.order)}" if x.order else " (미제출)")
        for x in st.hunters.values()
    )
    hacks = []
    for hk in ("doorlock", "distract", "blackout", "hide", "overload"):
        ok, why = E.hack_available(st, hk)
        cost, trace = int(c[f"{hk}_cost"]), int(c[f"{hk}_trace"])
        dev = S.HACK_DEVICE[hk]
        need = f", {S.DEVICE_KO[dev]} 방에서만" if dev else ""
        hacks.append(f"- {hk} ({S.HACK_KO[hk]}): RAM {cost}, 추적률 +{trace}{need} — " + ("사용 가능" if ok else f"불가: {why}"))
    locks = ", ".join(E.lockable_edges(st)) or "없음"
    history = "\n".join(
        f"- R{p['round']}: 이동 {p.get('target') or '대기'}"
        + (f" + {p['order']}" if p.get('order') not in (None, '', 'stay') else "")
        + f" — {p['reason']}" for p in past[-4:]
    ) or "(없음)"
    flags = []
    if f.hidden_until >= st.round_no:
        flags.append(f"은신 중 (~R{f.hidden_until})")
    if f.frozen:
        flags.append("동결 (이동 불가)")
    if f.hacks_disabled:
        flags.append("덱 봉인 (퀵핵 불가)")
    return f"""당신은 열차에 숨어든 침입자(도주자)입니다. 승무원들에게 잡히지 않고 최대한 오래 버티는 것이 목표입니다.
승무원이 당신과 같은 구역에서 라운드를 끝내거나(수색 중이면 확실히), 같은 문을 엇갈려 지나면 체포됩니다.
추적률이 오를수록 승무원의 추적기에 당신의 최근 위치가 정확히 표시됩니다. 퀵핵은 RAM 을 쓰고 추적률을 올리며, 사용한 장치 계열이 승무원에게 알려집니다.

[퀵핵 설명]
- doorlock: 문 하나를 이번 라운드 동안 잠금 (target = 'A1-A2' 형식). 잠글 수 있는 문: {locks}
- distract: 지정 구역(target)에 가짜 신호를 띄움
- blackout: 이번 라운드 승무원 보고를 잡음으로 덮음
- hide: 이번 라운드 이동 없이 은신 ({c['hide_duration']}라운드), 같은 구역에 있어도 수색이 아니면 안 잡힘
- overload: 현재 구역에 증기를 채워 들어오는 승무원을 다음 라운드 행동 불가로 만듦

[승무원이 보는 현황판]
{table}

[지난 라운드 보고 (승무원에게 간 내용)]
{last}

[당신의 실제 상태]
- 현재 위치: {f.room} ({S.DEVICE_KO[gmap.rooms[f.room].device]}) {' '.join(flags)}
- RAM {f.ram}/{c['ram_max']} (라운드마다 +{c['ram_regen']}), 추적률 {f.trace}% (라운드마다 +{c['trace_passive']})
- 이동 가능 구역 (이동 속도 {speed}): {reach_s or '없음'}
- 라운드 R{st.round_no} / 최대 {c['max_rounds']}

[퀵핵 가능 여부]
{chr(10).join(hacks)}

[승무원 위치와 제출 상황]
{hunters}

[당신의 이전 판단]
{history}

이번 라운드의 이동(또는 대기)과 퀵핵(선택)을 정하고, 왜 그렇게 판단했는지 한두 문장으로 설명하세요.
반드시 아래 JSON 형식으로만 답하세요.
{{"move": "이동할 구역 ID 또는 null(대기)", "hack": "doorlock|distract|blackout|hide|overload 또는 null", "target": "doorlock 이면 'A1-A2', distract 면 구역 ID, 그 외 null", "reason": "판단 이유 (한국어)"}}
"""


def parse_fugitive_decision(text: str) -> FugitiveDecision:
    raw = (text or "").strip()
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return FugitiveDecision(None, None, None, "응답에서 JSON 을 찾지 못해 대기", raw, "no_json")
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        return FugitiveDecision(None, None, None, f"JSON 파싱 실패로 대기 ({e})", raw, "bad_json")

    def _clean(v: Any) -> Optional[str]:
        return None if v in (None, "", "null", "none") else str(v).strip().upper()

    move = _clean(d.get("move"))
    if move in ("STAY", "대기"):
        move = None
    hack = d.get("hack")
    hack = None if hack in (None, "", "null", "none") else str(hack).strip().lower()
    if hack is not None and hack not in E.HACKS:
        hack = None
    target = _clean(d.get("target")) if hack in ("doorlock", "distract") else None
    reason = str(d.get("reason", "")).strip() or "(이유 없음)"
    return FugitiveDecision(move, hack, target, reason, raw)


async def decide_fugitive(player: "GeminiPlayer", st: E.GameState) -> FugitiveDecision:
    past = [r for r in st.bot_reasons if r["uid"] == FUGITIVE_UID]
    prompt = build_fugitive_prompt(st, past)
    try:
        text = await player.generate_with_retry(prompt, FUGITIVE_UID)
    except Exception as e:  # noqa: BLE001
        logger.warning("Gemini 호출 실패 (도주자): %s", e)
        return FugitiveDecision(None, None, None, f"Gemini 호출 실패로 대기: {str(e)[:200]}", "", "api_error")
    d = parse_fugitive_decision(text)
    logger.info("도주자 AI R%d → 이동 %s 핵 %s %s | %s", st.round_no, d.move or "대기", d.hack or "-", d.target or "", d.reason)
    return d


def record_fugitive_reason(st: E.GameState, d: FugitiveDecision, accepted: bool, note: str = "") -> None:
    st.bot_reasons.append({
        "round": st.round_no,
        "uid": FUGITIVE_UID,
        "name": FUGITIVE_NAME,
        "order": d.hack or "stay",
        "target": d.move,
        "hack_target": d.target,
        "reason": d.reason,
        "accepted": accepted,
        "note": note,
        "error": d.error,
    })


def fugitive_decision_text(d: FugitiveDecision) -> str:
    order = f"이동 {d.move}" if d.move else "대기"
    if d.hack:
        order += f" + {S.HACK_KO.get(d.hack, d.hack)}" + (f" {d.target}" if d.target else "")
    return S.BOT_DECISION.format(name=FUGITIVE_NAME, order=order, reason=d.reason)
