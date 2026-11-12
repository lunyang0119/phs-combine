"""현황판 PNG 렌더러 (선택 사항). fugitive.image_render 의 글꼴 탐색·색 유틸을 그대로 쓴다.

Pillow 또는 한글 글꼴이 없으면 available() 가 False 이고 코그는 임베드만 쓴다.
그림: 상단 게이지 띠 → 왼쪽 아군 패널(시트 color 열 또는 팔레트) / 오른쪽 적 패널(Enemies.csv color) →
각 패널에 HP 바, 상태, 이번 라운드 행동 한 줄(배경을 편 색으로 칠함), 적은 다음 라운드 의도.
"""
from __future__ import annotations

import io
from typing import Any, Dict, List, Optional, Tuple

from fugitive.image_render import available, font_path, parse_color, unavailable_reason, _ink_for  # noqa: F401

from . import engine as E
from . import render
from . import strings as S

ALLY_PALETTE = ["#2EC4B6", "#4F9DFF", "#A77BFF", "#5BD95B", "#FFB84F", "#FF7AB6", "#9ED8FF", "#C8FF6B"]
ENEMY_FALLBACK = "#D9534F"
BG, FG, DIM = (18, 20, 26), (230, 232, 240), (120, 124, 138)
ALLY_TINT, ENEMY_TINT = (28, 64, 48), (76, 32, 36)
PANEL_BG = (28, 30, 38)
GOOD, BAD, WARN = (80, 230, 140), (255, 90, 90), (255, 190, 90)
TAG_KO = {"shield": "엄폐", "atk_down": "글리치", "misfire": "고장", "redirect_random": "적대인식", "stun": "기절",
          "grapple": "붙잡힘", "daemon": "데몬", "rage": "분노", "boost": "플래시백"}
RESULT_KO = {"wait": "대기", "stunned": "기절", "down": "쓰러짐", "reboot": "리부트 중", "misfire": "불발",
             "evade": "회피당함", "no_target": "대상 없음", "field_mod_none": "현장 개조 불가"}


def _strip_md(s: str) -> str:
    return s.replace("**", "").replace("~~", "")


def _action_text(line: Dict[str, Any]) -> str:
    res = line.get("result", "")
    if res in RESULT_KO:
        return RESULT_KO[res]
    if res == "field_mod" and line.get("field_mod"):
        fm = line["field_mod"]
        return f"현장 개조 d10={fm.get('roll')} " + ("성공 " if fm.get("success") else "실패 ") + str(fm.get("weapon", ""))
    card = line.get("card_name", "")
    bits = []
    if line.get("combo"):
        bits.append("콤보!")
    bits.append(card)
    tails = []
    for t in line.get("targets") or []:
        k = t.get("kind")
        if k == "damage":
            tails.append(f"{t['name']} -{int(t['amount'])}" + (" KO" if t.get("killed") else "") + (" 과잉!" if t.get("overkill") else ""))
        elif k == "heal":
            tails.append(f"{t['name']} +{int(t['amount'])}")
        elif k == "shield":
            tails.append(f"{t['name']} 방어 {int(round(t['amount'] * 100))}%")
        elif k == "daemon":
            tails.append(f"{t['name']} 데몬 ×{int(t['amount'])}")
        elif k == "cleanse":
            tails.append(f"{t['name']} 감염 제거")
        elif k in TAG_KO:
            tails.append(f"{t['name']} {TAG_KO[k]}")
        elif k == "pass_terminal":
            tails.append(f"단말기 → {t['name']}")
        elif k == "break_free":
            tails.append("떨쳐냄")
        elif k == "talk":
            tails.append(f"{t['name']} 설득 {t.get('total', 0)}/{t.get('dc', 0)} " + ("성공" if t.get("success") else "실패"))
    if line.get("chip"):
        tails.append(f"칩 -{line['chip']['amount']}")
    if line.get("ice") and line["ice"].get("amount"):
        tails.append(f"ICE -{line['ice']['amount']}")
    if line.get("daemon"):
        tails.append(f"데몬 자해 -{line['daemon']['amount']}")
    text = " ".join(bits)
    if tails:
        text += ": " + ", ".join(tails)
    return text


def _unit_color(u: E.Combatant, idx: int) -> Tuple[int, int, int]:
    if u.side == E.ALLY:
        return parse_color(u.color, parse_color(ALLY_PALETTE[idx % len(ALLY_PALETTE)], (0, 220, 190)))
    return parse_color(u.color, parse_color(ENEMY_FALLBACK, (217, 83, 79)))


def _fit(d, text: str, font, max_w: int) -> str:
    """패널 폭에 맞게 자르고 말줄임표를 붙인다."""
    if d.textlength(text, font=font) <= max_w:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if d.textlength(text[:mid] + "…", font=font) <= max_w:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + "…"


def _bar(d, x, y, w, h, ratio: float, color, outline=(70, 74, 90)):
    d.rectangle([x, y, x + w, y + h], outline=outline, fill=(40, 42, 52))
    if ratio > 0:
        d.rectangle([x, y, x + int(w * min(1.0, ratio)), y + h], fill=color)


def _meters(st: E.BattleState) -> List[Tuple[str, float, Tuple[int, int, int]]]:
    out = []
    m = st.meters
    fmax = max(1, int(st.config.get("firewall_max", 9)))
    label = f"방화벽 {int(m.get('firewall', 0))}/{fmax}" + (" (잠김)" if E.firewall_locked(st) else "")
    out.append((label, int(m.get("firewall", 0)) / fmax, (90, 160, 255)))
    if st.has("ncpd"):
        nmax = max(0.1, float(st.config.get("ncpd_max", 3.0)))
        v = float(m.get("ncpd", 0.0))
        out.append((f"NCPD 경계도 {v:g}/{nmax:g}", v / nmax, BAD if v / nmax >= 0.66 else WARN))
    if st.has("doll"):
        cmax = max(1, int(m.get("chip_max", 1)))
        out.append((f"칩 안정도 {int(m.get('chip', 0))}/{cmax}", int(m.get("chip", 0)) / cmax, (255, 120, 220)))
    if st.has("ice"):
        imax = max(1, int(m.get("ice_max", 1)))
        out.append((f"블랙 ICE {int(m.get('ice', 0))}/{imax}층", int(m.get("ice", 0)) / imax, (120, 200, 255)))
    if st.has("life_support"):
        lmax = max(1, int(m.get("life_max", 1)))
        v = int(m.get("life", 0))
        out.append((f"생명 유지 {v}/{lmax}", v / lmax, BAD if v <= 2 else WARN if v <= 4 else GOOD))
    if st.has("terminal"):
        smax = max(1, int(m.get("screen_max", 1)))
        h = st.holder()
        out.append((f"액정 {int(m.get('screen', 0))}/{smax}  소지자: {h.name if h else '-'}", int(m.get("screen", 0)) / smax, (120, 220, 255)))
    if "pot" in m and bool(st.config.get("prep_enabled", True)):
        out.append((f"금고 {int(m.get('pot', 0))} 에디", 0.0, (255, 215, 90)))
    return out


def render_board(st: E.BattleState, report: Optional[Dict[str, Any]] = None) -> Optional[io.BytesIO]:
    """정산 직후(report) 또는 라운드 시작 상태의 현황판. 못 그리면 None."""
    if not available():
        return None
    from PIL import Image, ImageDraw, ImageFont

    fp = font_path()
    f_big = ImageFont.truetype(fp, 24)
    f_mid = ImageFont.truetype(fp, 18)
    f_small = ImageFont.truetype(fp, 15)

    pad, col_w, panel_h, gap = 20, 430, 96, 10
    allies = list(st.players.values())
    enemies = list(st.enemies.values())
    meters = _meters(st)
    show_mode = bool(st.meters.get("doll_mode") and st.status == E.ROUND_OPEN)
    header_h = 44 + 22 * len(meters) + 10 + (22 if show_mode else 0)
    rows = max(len(allies), len(enemies), 1)
    W = pad * 2 + col_w * 2 + 40
    H = header_h + rows * (panel_h + gap) + pad + 40
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # 헤더
    title = f"{st.battle_name or st.battle_id}   R{st.round_no():02d}"
    if report and report.get("ambush"):
        title = f"{st.battle_name or st.battle_id}   기습!"
    elif st.status == E.ESCAPE:
        title += "   도주 단계"
    elif st.status == E.ENDED:
        title += "   종료"
    d.text((pad, pad - 6), title, font=f_big, fill=(0, 220, 190))
    y = pad + 32
    for label, ratio, color in meters:
        d.text((pad, y), label, font=f_small, fill=FG)
        if ratio > 0 or "금고" not in label:
            _bar(d, pad + 300, y + 4, W - pad * 2 - 300, 10, ratio, color)
        y += 22
    mode = st.meters.get("doll_mode", "")
    if show_mode:
        owner = "돌" if st.has("doll") else str(st.meters.get("mode_owner", "?"))
        d.text((pad, y), _fit(d, f"{owner}: {S.MODE_KO.get(mode, mode)} - {S.MODE_DESC.get(mode, '')}", f_small, W - pad * 2),
               font=f_small, fill=(255, 120, 220))
    top = header_h

    actions: Dict[str, Dict[str, Any]] = {}
    if report:
        for line in report.get("lines") or []:
            actions[line["actor_id"]] = line
    intents = {it["id"]: it for it in E.public_intents(st)} if st.status == E.ROUND_OPEN else {}
    centers: Dict[str, Tuple[int, int, int, int]] = {}

    def panel(u: E.Combatant, idx: int, x: int, yy: int):
        col = _unit_color(u, idx)
        tint = ALLY_TINT if u.side == E.ALLY else ENEMY_TINT
        d.rectangle([x, yy, x + col_w, yy + panel_h], fill=PANEL_BG, outline=col, width=3)
        d.rectangle([x, yy, x + 10, yy + panel_h], fill=col)
        name = u.name + (f"  {render.job_label(u)}" if u.side == E.ALLY and render.job_label(u) else "")
        if u.weapon_grade > 0:
            name += f"  [{E.weapon_label(u)}]"
        if not u.alive():
            name += "  (쓰러짐)" + ("  약탈당함" if u.looted else "")
        if st.has("terminal") and st.meters.get("terminal_holder") == u.id:
            name += "  [단말기]"
        d.text((x + 18, yy + 6), _fit(d, name, f_mid, col_w - 30), font=f_mid, fill=FG if u.alive() else DIM)
        hp_ratio = u.hp / u.max_hp if u.max_hp else 0
        _bar(d, x + 18, yy + 32, 250, 12, hp_ratio, GOOD if hp_ratio > 0.5 else WARN if hp_ratio > 0.25 else BAD)
        d.text((x + 276, yy + 28), f"{u.hp}/{u.max_hp}", font=f_small, fill=FG)
        tags = " ".join(TAG_KO.get(m.kind, m.kind) + (f"×{int(m.value)}" if m.kind == "daemon" and m.value > 1 else "")
                        for m in u.modifiers)
        if u.side == E.ENEMY and u.traits:
            tr = []
            if u.has_trait("cheap_chrome"):
                tr.append("싼 의체")
            if u.has_trait("evade"):
                tr.append(f"회피 {int(round(u.traits['evade'] * 100))}%")
            if u.has_trait("regen"):
                tr.append(f"자가복구 {int(u.traits['regen'])}")
            tags = (" ".join(tr) + ("  " + tags if tags else "")).strip()
        if tags:
            d.text((x + 18, yy + 48), _fit(d, tags, f_small, col_w - 30), font=f_small, fill=WARN)
        # 행동 줄 (배경을 편 색으로)
        line_y = yy + 68
        d.rectangle([x + 12, line_y, x + col_w - 4, yy + panel_h - 4], fill=tint)
        text = ""
        if u.id in actions:
            text = _action_text(actions[u.id])
        elif u.id in intents:
            it = intents[u.id]
            text = "다음: " + _strip_md(render.intent_text(it).split(": ", 1)[-1])
        if text:
            d.text((x + 18, line_y + 4), _fit(d, text, f_small, col_w - 30), font=f_small, fill=FG)
        centers[u.id] = (x, yy + panel_h // 2, x + col_w, yy + panel_h // 2)

    for i, u in enumerate(allies):
        panel(u, i, pad, top + i * (panel_h + gap))
    for i, u in enumerate(enemies):
        panel(u, i, pad + col_w + 40, top + i * (panel_h + gap))

    # 화살표: 이번 라운드 상대편을 겨냥한 행동
    if report:
        for line in report.get("lines") or []:
            a = line["actor_id"]
            if a not in centers:
                continue
            for t in line.get("targets") or []:
                tid = t.get("id")
                if tid not in centers or st.unit(tid) is None or st.unit(tid).side == line.get("side"):
                    continue
                ax = centers[a][2] if line["side"] == E.ALLY else centers[a][0]
                ay = centers[a][1]
                tx = centers[tid][0] if line["side"] == E.ALLY else centers[tid][2]
                ty = centers[tid][1]
                col = GOOD if line["side"] == E.ALLY else BAD
                d.line([(ax, ay), (tx, ty)], fill=col, width=4 if line.get("combo") else 2)
                d.ellipse([tx - 5, ty - 5, tx + 5, ty + 5], fill=col)

    d.text((pad, H - 30), "초록 = 아군 행동   빨강 = 적 행동   굵은 선 = 콤보", font=f_small, fill=DIM)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf
