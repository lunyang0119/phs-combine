"""몬테카를로 시뮬레이션 — 라운드 수 분포 확인용.

    python -m fugitive.sim --hunters 4 --games 2000 --seed 1
    python -m fugitive.sim --all            # 2~6인 전부

헌터 봇: greedy (후보 구역 집합을 향해 이동, 후보가 좁으면 수색, 넓으면 추적)
도주자 봇: hacker (인접 위협 시 문 잠금, 포위 시 은신, 그 외 교란/정전) / stealth (핵 없음)
사람 도주자는 봇보다 오래 버티므로 결과는 하한선으로 본다.
"""
from __future__ import annotations

import argparse
import random
import statistics
from collections import Counter
from typing import Dict, List, Optional, Set

from . import config as cfg
from . import engine as E
from .strings import HACK_DEVICE


# ---------------------------------------------------------------------------
# 헌터 봇 지식: 보고를 바탕으로 도주자 후보 집합 유지
# ---------------------------------------------------------------------------
class HunterBrain:
    def __init__(self, gmap: E.GameMap, hunters: List[str]):
        self.gmap = gmap
        self.candidates: Set[str] = set(gmap.rooms)
        self.hunters = hunters
        self.hide_suspect = 0

    def observe(self, st: E.GameState, rep: Dict):
        g = self.gmap
        rooms = set(g.rooms)
        # 도주자는 최대 speed 칸 이동
        speed = int(st.config.get("fugitive_speed", 1))
        grown = set(self.candidates)
        for _ in range(speed):
            for r in list(grown):
                grown.update(g.rooms[r].neighbors)
        cand = grown & rooms
        if rep["blackout"]:
            self.candidates = cand
            return
        # 시그니처: 핵 시점(라운드 시작) 방의 장치 → 이동 전 후보를 제한하고 다시 확장
        if rep["signature"] and rep["signature"] != "ping":
            before = {r for r in self.candidates if g.rooms[r].device == rep["signature"]}
            cand = set()
            for r in before:
                cand.add(r)
                cand.update(g.rooms[r].neighbors)
            for _ in range(speed - 1):
                for r in list(cand):
                    cand.update(g.rooms[r].neighbors)
            if rep["signature"] == "curtain":
                cand = before            # 은신 = 제자리
                self.hide_suspect = 2
        # 접촉 실패: 그 방에 있었다 → 이번 라운드 이동 후보
        for ev in rep["events"]:
            if ev["type"] == "contact_miss":
                cand = {ev["room"]} | set(g.rooms[ev["room"]].neighbors)
        # 스캔: scan_lag=1 이면 라운드 시작 방의 장치 → 이동 전 후보 제한 후 확장
        for ev in rep["events"]:
            if ev["type"] == "scan":
                if int(st.config.get("scan_lag", 1)) >= 1:
                    before = {r for r in self.candidates if g.rooms[r].device == ev["device"]}
                    grown2 = set(before)
                    for _ in range(speed):
                        for r in list(grown2):
                            grown2.update(g.rooms[r].neighbors)
                    cand &= grown2
                else:
                    cand = {r for r in cand if g.rooms[r].device == ev["device"]}
        # 신호 강도
        hrooms = [h["room"] for h in rep["hunters"].values()]
        if rep["signal"] in ("strong", "medium", "weak"):
            def band(r):
                return E.signal_band(min(g.distance(r, hr) for hr in hrooms), st.config)
            cand = {r for r in cand if band(r) == rep["signal"]}
        # 표식 (미끼 가능성 때문에 가중치만)
        if rep["tier"] >= 3 and rep["markers"]:
            cand = set(rep["markers"]) & rooms or cand
        elif rep["markers"] and rep["tier"] >= 1:
            near = set()
            for m in rep["markers"]:
                near.add(m)
                near.update(g.rooms[m].neighbors)
                if rep["tier"] == 1:
                    for n in list(near):
                        near.update(g.rooms[n].neighbors)
            if cand & near:
                cand &= near
        # contact 모드: 헌터가 서 있는 방(숨지 않았다면)에는 없다
        if self.hide_suspect <= 0 and str(st.config.get("capture_mode", "contact")) == "contact":
            cand -= set(hrooms)
        elif str(st.config.get("capture_mode", "contact")) == "search":
            # 수색한 방에는 없다
            for ev in rep["events"]:
                if ev["type"] == "search_empty":
                    cand.discard(ev["room"])
        self.hide_suspect -= 1
        self.candidates = cand or rooms

    def orders(self, st: E.GameState, rng: random.Random) -> Dict[str, tuple]:
        g = self.gmap
        out = {}
        cand = list(self.candidates)
        budget = int(st.config["scan_budget"])
        scans = 0
        claimed: Set[str] = set()
        for uid in self.hunters:
            h = st.hunters[uid]
            if h.dazed_until >= st.round_no:
                continue
            if h.room in self.candidates and (self.hide_suspect > 0 or len(cand) <= 2):
                out[uid] = ("search", None)
                continue
            if len(cand) > 4 and scans < budget and rng.random() < 0.5:
                out[uid] = ("scan", None)
                scans += 1
                continue
            # 후보 중 가장 가까운(미배정) 방으로 한 칸
            targets = [r for r in cand if r not in claimed] or cand
            goal = min(targets, key=lambda r: (g.distance(h.room, r), rng.random()))
            claimed.add(goal)
            if goal == h.room:
                out[uid] = ("search", None)
                continue
            step = min(g.rooms[h.room].neighbors, key=lambda n: (g.distance(n, goal), rng.random()))
            out[uid] = ("move", step)
        return out


# ---------------------------------------------------------------------------
# 도주자 봇
# ---------------------------------------------------------------------------
def fugitive_order(st: E.GameState, style: str, rng: random.Random) -> tuple:
    g = st.game_map
    f = st.fugitive
    hrooms = [h.room for h in st.hunters.values()]
    if f.frozen:
        return None, None, None
    speed = int(st.config.get("fugitive_speed", 1))
    contact = str(st.config.get("capture_mode", "contact")) == "contact"

    def threat(room):
        return min(g.distance(room, hr) for hr in hrooms)

    def safe_area(room):
        """헌터 인접 방을 피해 도달 가능한 방 수 (막다른 곳 회피)."""
        seen = {room}
        dq = [room]
        while dq:
            cur = dq.pop()
            for n in g.rooms[cur].neighbors:
                if n not in seen and threat(n) >= 2:
                    seen.add(n)
                    dq.append(n)
        return len(seen)

    def score(r):
        t = threat(r)
        if r in hrooms:
            return -100
        # 헌터가 다음 라운드 들어올 수 있는 방(거리1)은 contact 모드에서 위험
        return t * 3 + safe_area(r) * 0.5 + len(g.rooms[r].neighbors) * 0.3 + rng.random()

    options = [r for r in g.rooms if g.distance(f.room, r) <= speed]
    best = max(options, key=score)
    move = None if best == f.room else best
    hack = target = None
    if style == "hacker" and not f.hacks_disabled:
        dev = g.rooms[f.room].device
        adjacent_threat = sum(1 for hr in hrooms if g.distance(f.room, hr) <= 1)
        cost = lambda h: int(st.config[f"{h}_cost"])
        if dev == "curtain" and adjacent_threat >= 2 and f.ram >= cost("hide") and contact:
            hack, move = "hide", None
        elif adjacent_threat >= 1 and f.ram >= cost("doorlock"):
            hr = min(hrooms, key=lambda r: g.distance(f.room, r))
            if g.adjacent(hr, f.room):
                hack, target = "doorlock", E.edge_key(hr, f.room)
        elif dev == "light" and threat(f.room) <= 2 and f.ram >= cost("blackout"):
            hack = "blackout"
        elif dev == "speaker" and f.ram >= cost("distract") and rng.random() < 0.6:
            far = max(g.rooms, key=lambda r: (g.distance(r, f.room), rng.random()))
            hack, target = "distract", far
        elif dev == "coffeepot" and adjacent_threat >= 1 and f.ram >= cost("overload"):
            hack = "overload"
    return move, hack, target


# ---------------------------------------------------------------------------
def play(hunters: int, style: str, rng: random.Random, map_id: Optional[str] = None,
         overrides: Optional[Dict] = None) -> tuple:
    c, mid = cfg.build_config(hunters)
    c["round_timer_sec"] = 1
    if overrides:
        c.update(overrides)
    gmap = E.GameMap.builtin(map_id or mid)
    st = E.new_game(gmap, c, [(str(i), f"h{i}") for i in range(hunters)], rng=rng)
    brain = HunterBrain(gmap, list(st.hunters))
    brain.candidates = set(gmap.rooms)
    while st.status != E.CAPTURED:
        mv, hk, tg = fugitive_order(st, style, rng)
        try:
            E.submit_fugitive_order(st, mv, hk, tg)
        except E.RuleError:
            E.submit_fugitive_order(st, mv)
        for uid, (t, tgt) in brain.orders(st, rng).items():
            E.submit_hunter_order(st, uid, t, tgt)
        rep = E.resolve_round(st, rng)
        brain.observe(st, rep)
        if st.round_no > 200:
            break
    return len(st.log), st.capture_how, st.fugitive.frozen


def run(hunters: int, games: int, seed: int, style: str, map_id=None, overrides=None) -> Dict:
    rng = random.Random(seed)
    rounds, hows, frozen = [], Counter(), 0
    for _ in range(games):
        n, how, fz = play(hunters, style, rng, map_id, overrides)
        rounds.append(n)
        hows[how] += 1
        frozen += fz
    rounds.sort()
    q = lambda p: rounds[min(len(rounds) - 1, int(p * len(rounds)))]
    return {"hunters": hunters, "style": style, "games": games, "p10": q(0.1), "p50": statistics.median(rounds),
            "p90": q(0.9), "mean": round(statistics.mean(rounds), 1), "how": dict(hows), "frozen": frozen}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hunters", type=int, default=4)
    ap.add_argument("--games", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--style", default="hacker", choices=["hacker", "stealth"])
    ap.add_argument("--map", default=None)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--set", action="append", default=[], help="key=value 오버라이드")
    a = ap.parse_args()
    overrides = {}
    for kv in a.set:
        k, v = kv.split("=", 1)
        overrides[k] = cfg.coerce(k, v)
    ns = range(2, 7) if a.all else [a.hunters]
    print(f"{'N':>2} {'style':>7} {'p10':>4} {'p50':>5} {'p90':>4} {'mean':>5}  how / frozen")
    for n in ns:
        for style in (["hacker", "stealth"] if a.all else [a.style]):
            r = run(n, a.games, a.seed, style, a.map, overrides)
            print(f"{n:>2} {style:>7} {r['p10']:>4} {r['p50']:>5} {r['p90']:>4} {r['mean']:>5}  {r['how']} / {r['frozen']}")


if __name__ == "__main__":
    main()
