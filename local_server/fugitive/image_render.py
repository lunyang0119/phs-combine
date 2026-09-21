"""현황판 이미지 렌더러 (선택 사항). Pillow + 한글 글꼴이 있을 때만 동작한다.

글꼴 탐색 순서: FUGITIVE_FONT_PATH 환경 변수 → 흔한 CJK 글꼴 경로.
없으면 available() 가 False 를 반환하고 코그는 코드 블록으로 대체한다.
"""
from __future__ import annotations

import io
import os
from typing import Dict, List, Optional

from . import strings as S
from .engine import PublicView

_CANDIDATES = [
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/unifont/unifont_jp.otf",
    "C:/Windows/Fonts/malgun.ttf",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
]


_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT_DIR = os.path.dirname(_PKG_DIR)


def _local_fonts() -> List[str]:
    """저장소 루트 / fugitive 패키지 / 현재 디렉터리에 놓인 글꼴 파일 (서버 경로가 달라도 잡힌다)."""
    out: List[str] = []
    for d in (_ROOT_DIR, _PKG_DIR, os.getcwd()):
        try:
            for name in sorted(os.listdir(d)):
                if name.lower().endswith((".ttf", ".otf", ".ttc")):
                    out.append(os.path.join(d, name))
        except OSError:
            pass
    return out


def font_path() -> Optional[str]:
    env = os.getenv("FUGITIVE_FONT_PATH", "")
    candidates = [env] if env else []
    if env and not os.path.exists(env):
        # 다른 OS 에서 쓰던 절대 경로여도 파일 이름만 같으면 로컬에서 찾아 준다
        candidates += [os.path.join(d, os.path.basename(env)) for d in (_ROOT_DIR, _PKG_DIR, os.getcwd())]
    candidates += _local_fonts() + _CANDIDATES
    for p in candidates:
        if p and os.path.exists(p):
            return p
    return None


def unavailable_reason() -> Optional[str]:
    """이미지 렌더를 못 쓰는 이유. 쓸 수 있으면 None."""
    try:
        import PIL  # noqa: F401
    except ImportError:
        return "Pillow 가 설치되어 있지 않습니다 (pip install Pillow)"
    if font_path() is None:
        env = os.getenv("FUGITIVE_FONT_PATH", "")
        hint = f"FUGITIVE_FONT_PATH={env!r} 도 존재하지 않습니다" if env else "FUGITIVE_FONT_PATH 가 비어 있습니다"
        return f"한글 글꼴(.ttf/.otf)을 찾지 못했습니다 — {hint}. 저장소 루트나 fugitive/ 에 글꼴 파일을 두면 자동 인식합니다"
    return None


def available() -> bool:
    return unavailable_reason() is None


def parse_color(s: str, fallback: tuple) -> tuple:
    """'#RGB' / '#RRGGBB' / 'RRGGBB' → (r, g, b). 이상하면 fallback."""
    t = str(s or "").strip().lstrip("#")
    if len(t) == 3:
        t = "".join(ch * 2 for ch in t)
    if len(t) != 6:
        return fallback
    try:
        return tuple(int(t[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return fallback


def _ink_for(bg_rgb: tuple) -> tuple:
    r, g, b = bg_rgb
    return (18, 20, 26) if (0.299 * r + 0.587 * g + 0.114 * b) > 140 else (240, 240, 245)


def render(pv: PublicView) -> Optional[io.BytesIO]:
    if not available():
        return None
    from PIL import Image, ImageDraw, ImageFont

    fp = font_path()
    cell_w, cell_h, pad = 150, 100, 24
    cols = max(int(r["col"]) for r in pv.map_rows)
    rows = max(int(r["row"]) for r in pv.map_rows)
    W = cols * cell_w + pad * 2
    H = rows * cell_h + pad * 2 + 70 + 60
    bg, fg, dim, accent, warn = (18, 20, 26), (230, 232, 240), (110, 114, 128), (0, 220, 190), (255, 90, 90)
    captured_fill, captured_edge = (26, 120, 70), (80, 230, 140)
    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    f_big = ImageFont.truetype(fp, 26)
    f_mid = ImageFont.truetype(fp, 20)
    f_small = ImageFont.truetype(fp, 16)

    band = S.SIGNAL_KO.get(pv.signal, pv.signal)
    d.text((pad, pad - 6), f"R{pv.round_no:02d}   추적률 {pv.trace}%   신호: {band}", font=f_big, fill=accent)
    # 추적률 바
    bx, by, bw, bh = pad, pad + 34, W - pad * 2, 10
    d.rectangle([bx, by, bx + bw, by + bh], outline=dim)
    d.rectangle([bx, by, bx + int(bw * pv.trace / 100), by + bh], fill=warn if pv.trace >= 70 else accent)

    top = pad + 60
    by_room = {r["room"]: r for r in pv.map_rows}
    marks: Dict[str, List[str]] = {}
    color_of: Dict[str, tuple] = {}
    for h in pv.hunters:
        marks.setdefault(h["room"], []).append(h["tag"])
        color_of[h["tag"]] = parse_color(h.get("color", ""), accent)
    # 방
    for r in pv.map_rows:
        x = pad + (int(r["col"]) - 1) * cell_w
        y = top + (int(r["row"]) - 1) * cell_h
        is_cap = pv.capture_room == r["room"]
        d.rectangle([x + 4, y + 4, x + cell_w - 4, y + cell_h - 4],
                    outline=captured_edge if is_cap else (70, 74, 90), width=3 if is_cap else 2,
                    fill=captured_fill if is_cap else (28, 30, 38) if r["room"] not in pv.steam else (60, 48, 30))
        if is_cap:
            d.text((x + cell_w - 40, y + 8), "★", font=f_big, fill=captured_edge)
        d.text((x + 12, y + 10), r["room"], font=f_mid, fill=fg)
        d.text((x + 12, y + 36), S.DEVICE_KO.get(r["device"], r["device"]), font=f_small, fill=dim)
        tags = marks.get(r["room"], [])
        for i, t in enumerate(tags):
            cx, cy = x + cell_w - 30 - i * 30, y + cell_h - 32
            col = color_of.get(t, accent)
            d.ellipse([cx - 13, cy - 13, cx + 13, cy + 13], fill=col)
            d.text((cx - 6, cy - 11), t, font=f_small, fill=_ink_for(col))
        if r["room"] in pv.markers:
            d.text((x + cell_w - 40, y + 8), "◎", font=f_big, fill=warn)
        if r["room"] in pv.steam:
            d.text((x + 12, y + 62), "증기", font=f_small, fill=(255, 190, 90))
    # 문: 인접한 방 사이 벽에 통로 표시
    def center(r):
        return (pad + (int(r["col"]) - 1) * cell_w + cell_w // 2, top + (int(r["row"]) - 1) * cell_h + cell_h // 2)

    drawn = set()
    for r in pv.map_rows:
        for n in str(r.get("neighbors", "")).split(","):
            key = tuple(sorted((r["room"], n)))
            if n not in by_room or key in drawn:
                continue
            drawn.add(key)
            (ax, ay), (bx2, by2) = center(r), center(by_room[n])
            mx, my = (ax + bx2) // 2, (ay + by2) // 2
            if ay == by2:
                d.rectangle([mx - 8, my - 16, mx + 8, my + 16], fill=(28, 30, 38), outline=(70, 74, 90))
            else:
                d.rectangle([mx - 16, my - 8, mx + 16, my + 8], fill=(28, 30, 38), outline=(70, 74, 90))
    # 잠긴 문: 두 방의 중심을 잇는 선 위에 X
    for e in pv.locks:
        a, b = e.split("-")
        ra, rb = by_room.get(a), by_room.get(b)
        if not ra or not rb:
            continue
        ax = pad + (int(ra["col"]) - 1) * cell_w + cell_w // 2
        ay = top + (int(ra["row"]) - 1) * cell_h + cell_h // 2
        bx2 = pad + (int(rb["col"]) - 1) * cell_w + cell_w // 2
        by2 = top + (int(rb["row"]) - 1) * cell_h + cell_h // 2
        mx, my = (ax + bx2) // 2, (ay + by2) // 2
        d.rectangle([mx - 14, my - 14, mx + 14, my + 14], fill=warn)
        d.text((mx - 8, my - 12), "X", font=f_mid, fill=bg)
    # 범례: 번호는 각자 색으로, 이름은 기본색으로
    lx = pad
    for h in pv.hunters:
        col = color_of.get(h["tag"], accent)
        d.ellipse([lx, H - 50, lx + 18, H - 32], fill=col)
        d.text((lx + 5, H - 49), h["tag"], font=f_small, fill=_ink_for(col))
        label = " " + h["name"]
        d.text((lx + 20, H - 50), label, font=f_small, fill=fg)
        lx += 20 + int(d.textlength(label, font=f_small))
        if h["submitted"]:   # 제출 완료 표시: 글꼴에 ✓ 가 없을 수 있어 작은 점으로 그린다
            d.ellipse([lx + 4, H - 44, lx + 11, H - 37], fill=accent)
            lx += 14
        lx += 14
    d.text((pad, H - 28), "◎ 신호 감지   X 잠긴 문" + ("   ★ 체포 지점" if pv.capture_room else ""), font=f_small, fill=dim)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf
