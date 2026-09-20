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
    os.getenv("FUGITIVE_FONT_PATH", ""),
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/unifont/unifont_jp.otf",
    "C:/Windows/Fonts/malgun.ttf",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
]


def font_path() -> Optional[str]:
    for p in _CANDIDATES:
        if p and os.path.exists(p):
            return p
    return None


def available() -> bool:
    try:
        import PIL  # noqa: F401
    except ImportError:
        return False
    return font_path() is not None


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
    for h in pv.hunters:
        marks.setdefault(h["room"], []).append(h["tag"])
    # 방
    for r in pv.map_rows:
        x = pad + (int(r["col"]) - 1) * cell_w
        y = top + (int(r["row"]) - 1) * cell_h
        d.rectangle([x + 4, y + 4, x + cell_w - 4, y + cell_h - 4], outline=(70, 74, 90), width=2,
                    fill=(28, 30, 38) if r["room"] not in pv.steam else (60, 48, 30))
        d.text((x + 12, y + 10), r["room"], font=f_mid, fill=fg)
        d.text((x + 12, y + 36), S.DEVICE_KO.get(r["device"], r["device"]), font=f_small, fill=dim)
        tags = marks.get(r["room"], [])
        for i, t in enumerate(tags):
            cx, cy = x + cell_w - 30 - i * 30, y + cell_h - 32
            d.ellipse([cx - 13, cy - 13, cx + 13, cy + 13], fill=accent)
            d.text((cx - 6, cy - 11), t, font=f_small, fill=bg)
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
    legend = "  ".join(f"{h['tag']} {h['name']}" + ("✓" if h["submitted"] else "") for h in pv.hunters)
    d.text((pad, H - 50), legend, font=f_small, fill=fg)
    d.text((pad, H - 28), "◎ 신호 감지   X 잠긴 문", font=f_small, fill=dim)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf
