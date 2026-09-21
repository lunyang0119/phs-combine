"""텍스트 렌더링 — 공개 현황판(코드 블록), 라운드 보고, 관제 서버용 실제 상태, 요약."""
from __future__ import annotations

from typing import Any, Dict, List

from . import strings as S
from .engine import GameState, PublicView, edge_key


def _grid(map_rows: List[Dict[str, Any]], cell_marks: Dict[str, str], width: int = 6) -> str:
    """방 격자. 인접한 방 사이의 벽에는 문(빈칸)을 뚫는다."""
    cols = max(int(r["col"]) for r in map_rows)
    rows = max(int(r["row"]) for r in map_rows)
    by_pos = {(int(r["col"]), int(r["row"])): r for r in map_rows}
    nb = {r["room"]: set(str(r.get("neighbors", "")).split(",")) for r in map_rows}

    def door(a, b) -> bool:
        return a is not None and b is not None and b["room"] in nb[a["room"]]

    def hsep(y: int) -> str:
        # y 행과 y+1 행 사이 (y=0: 맨 위, y=rows: 맨 아래)
        out = "+"
        for x in range(1, cols + 1):
            up, dn = by_pos.get((x, y)), by_pos.get((x, y + 1))
            if door(up, dn):
                gap = "-" * ((width - 2) // 2) + "  " + "-" * (width - 2 - (width - 2) // 2)
                out += gap + "+"
            else:
                out += "-" * width + "+"
        return out

    lines = [hsep(0)]
    for y in range(1, rows + 1):
        top, bottom = "|", "|"
        for x in range(1, cols + 1):
            r = by_pos.get((x, y))
            right = by_pos.get((x + 1, y))
            wall = " " if door(r, right) else "|"
            if r is None:
                top += " " * width + wall
                bottom += " " * width + wall
                continue
            label = f"{r['room']} {S.DEVICE_ABBR.get(r['device'], '?')}"
            top += f" {label:<{width - 1}}" + wall
            mark = cell_marks.get(r["room"], "")
            bottom += f" {mark:<{width - 1}}" + wall
        lines += [top, bottom, hsep(y)]
    return "\n".join(lines)


def table_text(pv: PublicView) -> str:
    marks: Dict[str, List[str]] = {}
    for h in pv.hunters:
        marks.setdefault(h["room"], []).append(h["tag"])
    for m in pv.markers:
        marks.setdefault(m, []).append("◎")
    for s in pv.steam:
        marks.setdefault(s, []).append("☕")
    if pv.capture_room:
        marks.setdefault(pv.capture_room, []).insert(0, "★")
    cell = {k: "".join(v)[:5] for k, v in marks.items()}
    if pv.round_no == 0:
        header = f"R00  추적률 {pv.bar}   0%   {S.TABLE_WAITING}"
    else:
        header = S.TABLE_HEADER.format(round=pv.round_no, bar=pv.bar, pct=pv.trace, band=S.SIGNAL_KO.get(pv.signal, pv.signal))
    body = _grid(pv.map_rows, cell)
    locks = ("⛔ " + "  ".join(pv.locks)) if pv.locks else ""
    names = "  ".join(f"{h['tag']} {h['name']}" + ("✓" if h["submitted"] else "") + ("☕" if h.get("dazed") else "") for h in pv.hunters)
    parts = [header, body, S.TABLE_LEGEND + (S.TABLE_LEGEND_CAPTURE if pv.capture_room else ""), names]
    if locks:
        parts.insert(2, locks)
    return f"**{S.TABLE_TITLE}**\n```\n" + "\n".join(parts) + "\n```"


def report_text(rep: Dict[str, Any], names: Dict[str, str]) -> str:
    lines = [S.REPORT_HEADER.format(round=rep["round"])]
    if rep.get("timed_out"):
        lines.append(S.REPORT_TIMEOUT)
    lines.append(S.REPORT_TRACE.format(bar=rep["bar"], pct=rep["trace"]))
    if rep["blackout"]:
        lines.append(S.REPORT_BLACKOUT)
    else:
        band = S.SIGNAL_KO.get(rep["signal"], rep["signal"])
        if rep.get("distance") is not None:
            lines.append(S.REPORT_SIGNAL_EXACT.format(band=band, dist=rep["distance"]))
        else:
            lines.append(S.REPORT_SIGNAL.format(band=band))
        if rep.get("signature") and rep["signature"] != "ping":
            lines.append(S.REPORT_SIGNATURE.format(device=S.DEVICE_KO.get(rep["signature"], rep["signature"])))
        if rep["markers"]:
            lines.append(S.REPORT_MARKER.format(rooms=", ".join(rep["markers"])))
    if rep["locks"]:
        lines.append(S.REPORT_LOCKS.format(edges=", ".join(rep["locks"])))
    for room in rep.get("steam", []):
        lines.append(S.REPORT_STEAM_ROOM.format(room=room))
    for ev in rep["events"]:
        n = names.get(ev.get("uid", ""), "?")
        t = ev["type"]
        if t == "door_locked":
            lines.append(S.REPORT_DOOR_LOCKED.format(name=n, edge=ev["edge"]))
        elif t == "dazed":
            lines.append(S.REPORT_DAZED.format(name=n))
        elif t == "search_steam":
            lines.append(S.REPORT_SEARCH_STEAM.format(name=n, room=ev["room"]))
        elif t == "search_empty":
            lines.append(S.REPORT_SEARCH_EMPTY.format(name=n, room=ev["room"]))
        elif t == "contact_miss":
            lines.append(S.REPORT_CONTACT_MISS.format(name=n, room=ev["room"]))
        elif t == "scan":
            lines.append(S.REPORT_SCAN.format(name=n, device=S.DEVICE_KO.get(ev["device"], ev["device"])))
        elif t == "scan_noise":
            lines.append(S.REPORT_SCAN_NOISE.format(name=n))
        elif t == "scan_busy":
            lines.append(S.REPORT_SCAN_BUSY.format(name=n))
    if rep.get("traced"):
        lines.append(S.REPORT_TRACED)
    if rep.get("frozen"):
        lines.append(S.REPORT_FROZEN)
    return "\n".join(lines)


def order_label(o) -> str:
    if o is None:
        return "—"
    if o.type == "move":
        return S.ORDER_MOVE.format(room=o.target)
    return S.ORDER_KO.get(o.type, o.type)


def round_open_text(st: GameState, deadline_unix: int) -> str:
    orders = "\n".join(
        S.ORDER_LINE.format(name=h.name, order=("☕ 행동 불가" if h.dazed_until >= st.round_no else order_label(h.order)))
        for h in st.hunters.values()
    ) or S.ROUND_OPEN_NO_ORDERS
    submitted = sum(1 for h in st.hunters.values() if h.order is not None or h.dazed_until >= st.round_no)
    return S.ROUND_OPEN.format(round=st.round_no, deadline=f"<t:{deadline_unix}:R> (<t:{deadline_unix}:f>)",
                               submitted=submitted, total=len(st.hunters), orders=orders)


def true_state_text(st: GameState) -> str:
    f = st.fugitive
    gmap = st.game_map
    lines = [f"### 🕶 실제 상태 — R{st.round_no} [{st.status}]"]
    if f:
        dev = gmap.rooms[f.room].device
        flags = []
        if f.hidden_until >= st.round_no:
            flags.append(f"은신(~R{f.hidden_until})")
        if f.frozen:
            flags.append("동결")
        if f.hacks_disabled:
            flags.append("덱 봉인")
        lines.append(f"도주자: **{f.room}** ({S.DEVICE_KO[dev]})  RAM {f.ram}/{st.config['ram_max']}  추적률 {f.trace}%  {' '.join(flags)}")
        speed = int(st.config.get("fugitive_speed", 1))
        reach = sorted(r for r in gmap.rooms if 0 < gmap.distance(f.room, r) <= speed)
        lines.append(f"이동 가능: {', '.join(reach)}")
        hacks = []
        from .engine import hack_available
        for hk in ("ping", "doorlock", "distract", "blackout", "hide", "overload"):
            ok, why = hack_available(st, hk)
            hacks.append(f"{S.HACK_KO[hk]}{'✓' if ok else '✗'}")
        lines.append("퀵핵: " + "  ".join(hacks))
        if f.order:
            lines.append(f"제출된 명령: 이동 {f.order.move or '대기'}" + (f", {S.HACK_KO[f.order.hack]} {f.order.target or ''}" if f.order.hack else ""))
        else:
            lines.append("제출된 명령: 없음")
    for h in st.hunters.values():
        lines.append(S.ORDER_LINE.format(name=f"{h.name} @{h.room or '-'}", order=order_label(h.order)))
    if st.last_report and st.last_report.get("locks"):
        lines.append("잠긴 문: " + ", ".join(st.last_report["locks"]))
    return "\n".join(lines)


def lobby_text(names: List[str]) -> str:
    return S.LOBBY_OPEN.format(n=len(names), names="\n".join(f"• {n}" for n in names))


def config_text(cfg: Dict[str, Any]) -> str:
    items = [f"{k}={v}" for k, v in cfg.items()]
    return "```\n" + "\n".join(items) + "\n```"


def summary_chunks(st: GameState) -> List[str]:
    names = {uid: h.name for uid, h in st.hunters.items()}
    lines = [S.SUMMARY_HEADER.format(rounds=len(st.log))]
    for entry in st.log:
        fo = entry["fugitive_order"]
        fdesc = f"{entry['fugitive_room_before']}→{entry['fugitive_room_after']}"
        if fo.get("hack"):
            fdesc += f" [{S.HACK_KO.get(fo['hack'], fo['hack'])}{' ' + fo['target'] if fo.get('target') else ''}]"
        orders = ", ".join(f"{names[u]}:{S.ORDER_KO.get(o['type'], o['type'])}{'→' + o['target'] if o.get('target') else ''}"
                           for u, o in entry["hunter_orders"].items())
        evs = []
        for ev in entry["report"]["events"]:
            if ev["type"] in ("scan", "contact_miss", "door_locked", "dazed"):
                evs.append(f"{names.get(ev.get('uid'), '?')}:{ev['type']}")
        if entry["report"].get("signature"):
            evs.append(f"sig={entry['report']['signature']}")
        lines.append(S.SUMMARY_ROUND.format(round=entry["round"], froom=fdesc, orders=orders, events=" ".join(evs) or "-"))
    if st.captured_by:
        lines.append(S.SUMMARY_CAPTURE.format(name=names.get(st.captured_by, "?"), how=S.CAPTURE_HOW.get(st.capture_how, st.capture_how), round=len(st.log)))
    chunks, cur = [], ""
    for ln in lines:
        if len(cur) + len(ln) + 1 > 1900:
            chunks.append(cur)
            cur = ""
        cur += ln + "\n"
    if cur:
        chunks.append(cur)
    return chunks
