"""침입자 추적 미니게임 — 순수 규칙 엔진 (Discord 의존성 없음).

라운드 정산 순서 (docs/fugitive_game_design.md §3.5):
 1. 도주자 퀵핵 선효과   2. 헌터 이동   3. 도주자 이동   4. 체포 판정
 5. 추적(스캔) 응답      6. 추적률/RAM  7. 보고 생성    8. 안전장치   9. 커밋

헌터 정산 순서는 매 라운드 무작위로 섞는다 (스캔 예산, 체포 동순위, 이벤트 순서).
"""
from __future__ import annotations

import random
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

from . import config as cfgmod
from .strings import DEVICE_KO, HACK_DEVICE, HACK_KO

# ---------------------------------------------------------------------------
# 상태 값
# ---------------------------------------------------------------------------
IDLE, LOBBY, ROUND_OPEN, RESOLVING, PAUSED, CAPTURED = (
    "IDLE", "LOBBY", "ROUND_OPEN", "RESOLVING", "PAUSED", "CAPTURED",
)
HUNTER_ORDERS = ("move", "search", "scan", "stay")
HACKS = ("ping", "doorlock", "distract", "blackout", "hide", "overload")


class RuleError(ValueError):
    """플레이어에게 그대로 보여줄 수 있는 규칙 위반."""


def edge_key(a: str, b: str) -> str:
    return "-".join(sorted((a, b)))


# ---------------------------------------------------------------------------
# 맵
# ---------------------------------------------------------------------------
@dataclass
class Room:
    id: str
    device: str
    neighbors: List[str]
    col: int
    row: int


class GameMap:
    def __init__(self, map_id: str, rooms: Dict[str, Room]):
        self.map_id = map_id
        self.rooms = rooms
        self._dist: Dict[Tuple[str, str], int] = {}
        self.validate()

    @classmethod
    def from_rows(cls, map_id: str, rows: List[Dict[str, Any]]) -> "GameMap":
        rooms: Dict[str, Room] = {}
        for r in rows:
            if str(r.get("map_id", map_id)) != map_id:
                continue
            rid = str(r["room"]).strip()
            nb = [x for x in str(r.get("neighbors", "")).replace(" ", "").split(",") if x]
            rooms[rid] = Room(rid, str(r["device"]).strip(), nb, int(r.get("col") or 0), int(r.get("row") or 0))
        if not rooms:
            raise cfgmod.ConfigError(f"맵 {map_id} 에 방이 없습니다")
        return cls(map_id, rooms)

    @classmethod
    def builtin(cls, map_id: str) -> "GameMap":
        return cls.from_rows(map_id, cfgmod.map_rows(map_id))

    def validate(self) -> None:
        for rid, room in self.rooms.items():
            if room.device not in cfgmod.DEVICES:
                raise cfgmod.ConfigError(f"{rid}: 알 수 없는 장치 {room.device!r}")
            for n in room.neighbors:
                if n not in self.rooms:
                    raise cfgmod.ConfigError(f"{rid}: 존재하지 않는 인접 방 {n}")
                if rid not in self.rooms[n].neighbors:
                    raise cfgmod.ConfigError(f"{rid}-{n}: 인접 관계가 대칭이 아닙니다")
                if self.rooms[n].device == room.device:
                    raise cfgmod.ConfigError(f"{rid}-{n}: 같은 장치({room.device})의 방이 인접해 있습니다")
        start = next(iter(self.rooms))
        seen = {start}
        dq = deque([start])
        while dq:
            cur = dq.popleft()
            for n in self.rooms[cur].neighbors:
                if n not in seen:
                    seen.add(n)
                    dq.append(n)
        if len(seen) != len(self.rooms):
            raise cfgmod.ConfigError("맵이 연결되어 있지 않습니다")

    def adjacent(self, a: str, b: str) -> bool:
        return b in self.rooms[a].neighbors

    def edges(self) -> List[str]:
        out = set()
        for rid, room in self.rooms.items():
            for n in room.neighbors:
                out.add(edge_key(rid, n))
        return sorted(out)

    def has_edge(self, edge: str) -> bool:
        parts = edge.split("-")
        return len(parts) == 2 and parts[0] in self.rooms and parts[1] in self.rooms[parts[0]].neighbors

    def distance(self, a: str, b: str) -> int:
        key = (a, b)
        if key in self._dist:
            return self._dist[key]
        dist = {a: 0}
        dq = deque([a])
        while dq:
            cur = dq.popleft()
            for n in self.rooms[cur].neighbors:
                if n not in dist:
                    dist[n] = dist[cur] + 1
                    dq.append(n)
        for k, v in dist.items():
            self._dist[(a, k)] = v
            self._dist[(k, a)] = v
        return dist[b]

    def shortest_path(self, a: str, b: str, avoid: Optional[set] = None,
                      rng: Optional[random.Random] = None) -> List[str]:
        """a→b 최단 경로 (방 목록, a 포함). avoid 의 중간 방은 가능하면 피한다."""
        avoid = avoid or set()
        best: Optional[List[str]] = None
        for penalise in (True, False):
            prev = {a: None}
            dq = deque([a])
            while dq:
                cur = dq.popleft()
                if cur == b:
                    break
                nbs = list(self.rooms[cur].neighbors)
                if rng:
                    rng.shuffle(nbs)
                for n in nbs:
                    if n in prev or (penalise and n in avoid and n != b):
                        continue
                    prev[n] = cur
                    dq.append(n)
            if b in prev:
                path = [b]
                while prev[path[-1]] is not None:
                    path.append(prev[path[-1]])
                best = path[::-1]
                break
        return best or [a]

    def rooms_with_device(self, device: str) -> List[str]:
        return [rid for rid, r in self.rooms.items() if r.device == device]

    def to_rows(self) -> List[Dict[str, Any]]:
        return [
            {"map_id": self.map_id, "room": r.id, "device": r.device,
             "neighbors": ",".join(r.neighbors), "col": r.col, "row": r.row}
            for r in self.rooms.values()
        ]


# ---------------------------------------------------------------------------
# 상태 데이터클래스
# ---------------------------------------------------------------------------
@dataclass
class HunterOrder:
    type: str                 # move | search | scan | stay
    target: Optional[str] = None
    ts: float = 0.0


@dataclass
class FugitiveOrder:
    move: Optional[str] = None       # None = 대기
    hack: Optional[str] = None
    target: Optional[str] = None     # doorlock: edge, distract: room
    ts: float = 0.0


@dataclass
class Hunter:
    user_id: str
    name: str
    room: str
    order: Optional[HunterOrder] = None
    dazed_until: int = -1            # 이 라운드 번호까지 행동 불가 (포함)
    joined_ts: float = 0.0


@dataclass
class Fugitive:
    room: str
    ram: int
    trace: int = 0
    order: Optional[FugitiveOrder] = None
    hidden_until: int = -1
    frozen: bool = False
    hacks_disabled: bool = False
    traced_at: Optional[int] = None
    ping_round: int = -1             # 이 라운드에 핑 사용
    ping_ts: float = 0.0


@dataclass
class GameState:
    status: str = IDLE
    round_no: int = 0
    config: Dict[str, Any] = field(default_factory=dict)
    map_id: str = ""
    map_rows: List[Dict[str, Any]] = field(default_factory=list)
    hunters: Dict[str, Hunter] = field(default_factory=dict)
    fugitive: Optional[Fugitive] = None
    locks: List[Dict[str, Any]] = field(default_factory=list)     # {edge, round}
    decoys: List[Dict[str, Any]] = field(default_factory=list)    # {room, round}
    steam: List[Dict[str, Any]] = field(default_factory=list)     # {room, round}
    blackout_round: int = -1
    position_history: List[str] = field(default_factory=list)     # index = 라운드 종료 시점 (0 = 시작)
    log: List[Dict[str, Any]] = field(default_factory=list)
    captured_by: Optional[str] = None
    capture_how: Optional[str] = None
    last_report: Optional[Dict[str, Any]] = None
    # Discord 바인딩 (엔진은 사용하지 않음)
    guild_id: int = 0
    channel_id: int = 0
    control_guild_id: int = 0
    control_channel_id: int = 0
    table_message_id: int = 0          # 마지막으로 게시한 현황판 메시지 (편집/고정하지 않음)
    round_message_id: int = 0
    deadline_ts: float = 0.0
    paused_remaining: float = 0.0
    created_ts: float = 0.0
    seed: int = 0

    # --- 직렬화 -----------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "GameState":
        d = dict(d)
        hunters = {}
        for uid, h in d.get("hunters", {}).items():
            h = dict(h)
            if h.get("order"):
                h["order"] = HunterOrder(**h["order"])
            hunters[uid] = Hunter(**h)
        d["hunters"] = hunters
        f = d.get("fugitive")
        if f:
            f = dict(f)
            if f.get("order"):
                f["order"] = FugitiveOrder(**f["order"])
            d["fugitive"] = Fugitive(**f)
        return cls(**d)

    # --- 파생 -------------------------------------------------------------
    @property
    def game_map(self) -> GameMap:
        cache = getattr(self, "_map_cache", None)
        if cache is None or cache.map_id != self.map_id:
            cache = GameMap.from_rows(self.map_id, self.map_rows)
            object.__setattr__(self, "_map_cache", cache)
        return cache

    def hunter_order_of(self, uid: str) -> Optional[HunterOrder]:
        h = self.hunters.get(uid)
        return h.order if h else None

    def all_hunters_submitted(self) -> bool:
        return all(h.order is not None or h.dazed_until >= self.round_no for h in self.hunters.values())

    def trace_tier(self) -> int:
        t = self.fugitive.trace
        c = self.config
        if t >= c["trace_t3"]:
            return 3
        if t >= c["trace_t2"]:
            return 2
        if t >= c["trace_t1"]:
            return 1
        return 0

    def is_locked(self, edge: str, round_no: int) -> bool:
        return any(l["edge"] == edge and l["round"] == round_no for l in self.locks)

    def steam_rooms(self, round_no: int) -> List[str]:
        return [s["room"] for s in self.steam if s["round"] == round_no]


# ---------------------------------------------------------------------------
# 게임 생성
# ---------------------------------------------------------------------------
def _auto_spawns(gmap: GameMap, fugitive_start: str, n: int) -> List[str]:
    """도주자 시작점에서 가장 먼 방 순으로 헌터 배치 (여러 명이면 순환)."""
    ranked = sorted(gmap.rooms, key=lambda r: (-gmap.distance(fugitive_start, r), r))
    far = ranked[: max(1, min(n, len(ranked) // 2))]
    return [far[i % len(far)] for i in range(n)]


def new_game(
    gmap: GameMap,
    config: Dict[str, Any],
    hunters: List[Tuple[str, str]],
    fugitive_start: Optional[str] = None,
    rng: Optional[random.Random] = None,
    now: Optional[float] = None,
) -> GameState:
    rng = rng or random.Random()
    now = now or time.time()
    if not 1 <= len(hunters) <= 12:
        raise RuleError("헌터는 1~12명이어야 합니다")
    start = fugitive_start or config.get("fugitive_start") or "auto"
    if start == "auto":
        # 가장 중심(편심 최소)인 방
        start = min(gmap.rooms, key=lambda r: (max(gmap.distance(r, o) for o in gmap.rooms), r))
    if start not in gmap.rooms:
        raise RuleError(f"시작 구역 {start} 이(가) 맵에 없습니다")
    spawn_cfg = str(config.get("hunter_spawn_rooms", "auto"))
    if spawn_cfg == "auto":
        spawns = _auto_spawns(gmap, start, len(hunters))
    else:
        pool = [x for x in spawn_cfg.replace(" ", "").split(",") if x]
        bad = [x for x in pool if x not in gmap.rooms]
        if bad or not pool:
            raise RuleError(f"hunter_spawn_rooms 오류: {bad or '비어 있음'}")
        spawns = [pool[i % len(pool)] for i in range(len(hunters))]
    st = GameState(
        status=ROUND_OPEN,
        round_no=1,
        config=dict(config),
        map_id=gmap.map_id,
        map_rows=gmap.to_rows(),
        fugitive=Fugitive(room=start, ram=int(config["ram_max"])),
        created_ts=now,
        seed=rng.randrange(1 << 30),
    )
    for (uid, name), room in zip(hunters, spawns):
        st.hunters[str(uid)] = Hunter(user_id=str(uid), name=name, room=room, joined_ts=now)
    st.position_history = [start]
    return st


# ---------------------------------------------------------------------------
# 명령 제출
# ---------------------------------------------------------------------------
def submit_hunter_order(st: GameState, uid: str, order_type: str, target: Optional[str] = None,
                        now: Optional[float] = None) -> HunterOrder:
    if st.status != ROUND_OPEN:
        raise RuleError("NOT_OPEN")
    h = st.hunters.get(str(uid))
    if h is None:
        raise RuleError("NOT_HUNTER")
    if h.dazed_until >= st.round_no:
        raise RuleError("DAZED")
    if order_type not in HUNTER_ORDERS:
        raise RuleError(f"알 수 없는 명령: {order_type}")
    gmap = st.game_map
    if order_type == "move":
        if not target or target not in gmap.rooms:
            raise RuleError("UNKNOWN_ROOM")
        if not gmap.adjacent(h.room, target):
            raise RuleError("NOT_ADJACENT")
    else:
        target = None
    h.order = HunterOrder(order_type, target, now or time.time())
    return h.order


def hack_available(st: GameState, hack: str) -> Tuple[bool, str]:
    """(가능 여부, 사유). 라운드 시작 시점의 도주자 위치 기준."""
    f = st.fugitive
    c = st.config
    if hack not in HACKS:
        return False, f"알 수 없는 퀵핵: {hack}"
    if f.hacks_disabled or f.frozen:
        return False, "덱이 봉인되어 퀵핵을 사용할 수 없습니다"
    if hack == "ping" and not c["ping_enabled"]:
        return False, "핑이 비활성화되어 있습니다"
    dev = HACK_DEVICE[hack]
    cur = st.game_map.rooms[f.room].device
    if dev and cur != dev:
        where = ", ".join(sorted(st.game_map.rooms_with_device(dev))) or "없음"
        return False, (f"{HACK_KO.get(hack, hack)} 은(는) {DEVICE_KO.get(dev, dev)} 장치가 있는 구역에서만 사용할 수 있습니다 "
                       f"(현재 {f.room}: {DEVICE_KO.get(cur, cur)} / 가능 구역: {where})")
    cost = int(c[f"{hack}_cost"])
    if f.ram < cost:
        return False, f"RAM 부족 ({f.ram}/{cost})"
    return True, ""


def lockable_edges(st: GameState) -> List[str]:
    """도주자의 현재 위치에서 잠글 수 있는 문 목록 (doorlock_range 기준)."""
    rng_ = int(st.config["doorlock_range"])
    gmap = st.game_map
    if rng_ < 0:
        return gmap.edges()
    here = st.fugitive.room
    return [e for e in gmap.edges()
            if min(gmap.distance(here, a) for a in e.split("-")) <= rng_]


def submit_fugitive_order(st: GameState, move: Optional[str], hack: Optional[str] = None,
                          target: Optional[str] = None, now: Optional[float] = None) -> FugitiveOrder:
    if st.status != ROUND_OPEN:
        raise RuleError("지금은 명령을 제출할 수 없습니다")
    f = st.fugitive
    gmap = st.game_map
    if move is not None:
        if f.frozen:
            raise RuleError("동결 상태 — 이동할 수 없습니다")
        if move not in gmap.rooms:
            raise RuleError(f"{move} 은(는) 존재하지 않는 구역입니다")
        speed = int(st.config.get("fugitive_speed", 1))
        if gmap.distance(f.room, move) > speed:
            reach = sorted(r for r in gmap.rooms if 0 < gmap.distance(f.room, r) <= speed)
            raise RuleError(f"{move} 은(는) {f.room} 에서 갈 수 없습니다 (가능: {', '.join(reach)})")
    if hack is not None:
        if hack == "ping":
            raise RuleError("핑은 `/도주 핑` 으로 즉시 사용합니다")
        ok, why = hack_available(st, hack)
        if not ok:
            raise RuleError(why)
        if hack == "doorlock":
            if not target or not gmap.has_edge(target):
                raise RuleError(f"문 잠금 대상은 `A1-A2` 형식의 실제 문이어야 합니다")
            target = edge_key(*target.split("-"))
            rng_ = int(st.config["doorlock_range"])
            if rng_ >= 0:
                allowed = lockable_edges(st)
                if target not in allowed:
                    scope = f"현재 구역 {f.room} 에 접한 문만" if rng_ == 0 else f"현재 구역 {f.room} 에서 {rng_}칸 이내의 문만"
                    raise RuleError(f"문 잠금 범위 초과: {scope} 잠글 수 있습니다 (가능: {', '.join(allowed) or '없음'})")
        elif hack == "distract":
            if not target or target not in gmap.rooms:
                raise RuleError("교란 대상 구역을 지정하세요")
        else:
            target = None
    f.order = FugitiveOrder(move=move, hack=hack, target=target, ts=now or time.time())
    return f.order


def cast_ping(st: GameState, now: Optional[float] = None) -> List[Tuple[str, Optional[HunterOrder]]]:
    """핑: 즉시 RAM/추적률 지불, 현재까지 제출된 헌터 명령을 반환 (라운드당 1회)."""
    if st.status != ROUND_OPEN:
        raise RuleError("지금은 사용할 수 없습니다")
    f = st.fugitive
    if f.ping_round == st.round_no:
        raise RuleError("이번 라운드에 이미 핑을 사용했습니다")
    ok, why = hack_available(st, "ping")
    if not ok:
        raise RuleError(why)
    f.ram -= int(st.config["ping_cost"])
    f.trace = min(100, f.trace + int(st.config["ping_trace"]))
    f.ping_round = st.round_no
    f.ping_ts = now or time.time()
    return [(h.name, h.order) for h in st.hunters.values()]


# ---------------------------------------------------------------------------
# 라운드 정산
# ---------------------------------------------------------------------------
def signal_band(dist: int, c: Dict[str, Any]) -> str:
    a, b = (int(x) for x in str(c.get("signal_bands", "1,3")).split(","))
    return "strong" if dist <= a else "medium" if dist <= b else "weak"


def _bar(pct: int, width: int = 10) -> str:
    filled = round(pct / 100 * width)
    return "▓" * filled + "░" * (width - filled)


def resolve_round(st: GameState, rng: Optional[random.Random] = None, timed_out: bool = False) -> Dict[str, Any]:
    """라운드를 정산하고 공개 보고(dict)를 반환한다. 상태는 제자리에서 갱신된다."""
    if st.status not in (ROUND_OPEN, PAUSED, RESOLVING):
        raise RuleError("정산할 라운드가 없습니다")
    rng = rng or random.Random()
    gmap = st.game_map
    c = st.config
    f = st.fugitive
    R = st.round_no
    st.status = RESOLVING

    order_ids = list(st.hunters)
    rng.shuffle(order_ids)
    events: List[Dict[str, Any]] = []
    hidden_events: List[str] = []

    # 0. 기본값
    for h in st.hunters.values():
        if h.dazed_until >= R:
            h.order = HunterOrder("stay", None, 0.0)
        elif h.order is None:
            h.order = HunterOrder("stay", None, 0.0)
    forder = f.order or FugitiveOrder()
    f_old = f.room

    # 1. 퀵핵 선효과
    signature: Optional[str] = None
    hack = forder.hack
    if hack:
        ok, why = hack_available(st, hack)
        if not ok:
            hidden_events.append(f"퀵핵 {hack} 무효: {why}")
            hack = None
    if hack:
        f.ram -= int(c[f"{hack}_cost"])
        f.trace = min(100, max(0, f.trace + int(c[f"{hack}_trace"])))
        dev = HACK_DEVICE[hack]
        if dev and c["signature_reveal"]:
            signature = dev
        if hack == "doorlock":
            st.locks.append({"edge": forder.target, "round": R})
        elif hack == "hide":
            f.hidden_until = R + int(c["hide_duration"]) - 1
            forder.move = None
        elif hack == "blackout":
            st.blackout_round = R
        elif hack == "distract":
            st.decoys.append({"room": forder.target, "round": R})
        elif hack == "overload":
            st.steam.append({"room": f.room, "round": R})
        hidden_events.append(f"퀵핵 {hack}" + (f" → {forder.target}" if forder.target else ""))
    if f.ping_round == R and c["ping_signature"] and c["signature_reveal"] and signature is None:
        signature = "ping"

    steam_now = st.steam_rooms(R)
    blackout = st.blackout_round == R

    # 2. 헌터 이동
    old_rooms = {uid: st.hunters[uid].room for uid in order_ids}
    new_rooms = dict(old_rooms)
    for uid in order_ids:
        h = st.hunters[uid]
        if h.order.type == "move":
            e = edge_key(h.room, h.order.target)
            if st.is_locked(e, R):
                events.append({"type": "door_locked", "uid": uid, "edge": e})
            else:
                new_rooms[uid] = h.order.target

    # 3. 도주자 이동 (경로: 잠긴 문 앞에서 멈춘다)
    f_path = [f_old]
    if forder.move and not f.frozen:
        avoid = set(old_rooms.values()) | set(new_rooms.values())
        planned = gmap.shortest_path(f_old, forder.move, avoid=avoid, rng=rng)
        for nxt in planned[1:]:
            if st.is_locked(edge_key(f_path[-1], nxt), R):
                hidden_events.append(f"잠긴 문({edge_key(f_path[-1], nxt)}) 앞에서 정지")
                break
            f_path.append(nxt)
    f_new = f_path[-1]
    hidden = f.hidden_until >= R
    contact_mode = str(c.get("capture_mode", "contact")) == "contact"
    path_edges = {edge_key(f_path[i], f_path[i + 1]) for i in range(len(f_path) - 1)}
    through = set(f_path[1:-1])

    # 4. 체포 판정
    captured: Optional[Tuple[str, str]] = None   # (uid, how)
    for how in ("search", "swap", "colocate"):
        for uid in order_ids:
            h = st.hunters[uid]
            if how == "search":
                if h.order.type == "search" and old_rooms[uid] == f_new and old_rooms[uid] not in steam_now:
                    captured = (uid, how)
            elif how == "swap":
                moved = old_rooms[uid] != new_rooms[uid]
                if moved and edge_key(old_rooms[uid], new_rooms[uid]) in path_edges:
                    captured = (uid, how)
                elif old_rooms[uid] in through or new_rooms[uid] in through:
                    captured = (uid, how)
            else:
                if contact_mode and new_rooms[uid] == f_new and not hidden:
                    stayed = old_rooms[uid] == new_rooms[uid]
                    chance = float(c.get("contact_capture_chance", 1.0))
                    if stayed or chance >= 1.0 or rng.random() < chance:
                        captured = (uid, how)
                    else:
                        events.append({"type": "contact_miss", "uid": uid, "room": f_new})
            if captured:
                break
        if captured:
            break

    # 4b. 수색 결과 / 증기
    for uid in order_ids:
        h = st.hunters[uid]
        if h.order.type == "search" and not (captured and captured[0] == uid):
            if old_rooms[uid] in steam_now:
                events.append({"type": "search_steam", "uid": uid, "room": old_rooms[uid]})
            else:
                events.append({"type": "search_empty", "uid": uid, "room": old_rooms[uid]})
    for uid in order_ids:
        if new_rooms[uid] in steam_now:
            st.hunters[uid].dazed_until = R + int(c["overload_daze_rounds"])
            events.append({"type": "dazed", "uid": uid, "room": new_rooms[uid]})

    # 5. 스캔
    budget = int(c["scan_budget"])
    used = 0
    for uid in order_ids:
        h = st.hunters[uid]
        if h.order.type != "scan":
            continue
        if used >= budget:
            events.append({"type": "scan_busy", "uid": uid})
        elif blackout and c["blackout_blocks_scan"]:
            events.append({"type": "scan_noise", "uid": uid})
            used += 1
        else:
            scan_room = f_old if int(c.get("scan_lag", 1)) >= 1 else f_new
            events.append({"type": "scan", "uid": uid, "device": gmap.rooms[scan_room].device})
            f.trace = min(100, f.trace + int(c.get("scan_trace", 0)))
            used += 1

    # 6. 추적률 / RAM
    f.trace = min(100, f.trace + int(c["trace_passive"]))
    f.ram = min(int(c["ram_max"]), f.ram + int(c["ram_regen"]))
    if f.trace >= int(c["trace_t3"]) and f.traced_at is None:
        f.traced_at = R
        f.hacks_disabled = True

    # 8. 안전장치
    frozen_now = False
    if not f.frozen:
        if R >= int(c["max_rounds"]) or (f.traced_at is not None and R - f.traced_at >= int(c["overheat_rounds"])):
            f.frozen = True
            frozen_now = True

    # 9. 커밋
    for uid in order_ids:
        st.hunters[uid].room = new_rooms[uid]
    f.room = f_new
    st.position_history.append(f_new)

    # 7. 보고 (커밋 후 위치 기준)
    tier = st.trace_tier()
    lag = {0: None, 1: 2, 2: 1, 3: 0}[tier]
    if f.frozen:
        lag = 0            # 동결된 신호는 항상 실시간으로 잡힌다
    markers: List[str] = []
    if lag is not None and not blackout:
        idx = len(st.position_history) - 1 - lag
        if idx >= 0:
            markers.append(st.position_history[idx])
    for d in st.decoys:
        if d["round"] == R and not blackout:
            markers.append(d["room"])
    rng.shuffle(markers)
    markers = list(dict.fromkeys(markers))

    if blackout or c["signal_mode"] == "off":
        band, dist = "noise" if blackout else "none", None
    else:
        dist = min(gmap.distance(st.hunters[u].room, f.room) for u in order_ids)
        band = signal_band(dist, c)

    report = {
        "round": R,
        "trace": f.trace,
        "bar": _bar(f.trace),
        "tier": tier,
        "signal": band,
        "distance": dist if c["signal_mode"] == "exact" else None,
        "signature": None if blackout else signature,
        "markers": markers,
        "locks": [l["edge"] for l in st.locks if l["round"] == R],
        "steam": steam_now,
        "blackout": blackout,
        "events": events,
        "order": order_ids,
        "timed_out": timed_out,
        "traced": f.traced_at == R,
        "frozen": frozen_now,
        "captured_by": captured[0] if captured else None,
        "capture_how": captured[1] if captured else None,
        "hunters": {uid: {"name": st.hunters[uid].name, "room": st.hunters[uid].room} for uid in st.hunters},
    }
    st.log.append({
        "round": R,
        "fugitive_room_before": f_old,
        "fugitive_room_after": f_new,
        "fugitive_order": asdict(forder),
        "hunter_orders": {uid: asdict(st.hunters[uid].order) for uid in order_ids},
        "hunter_rooms": {uid: st.hunters[uid].room for uid in order_ids},
        "hidden_events": hidden_events,
        "ram": f.ram,
        "trace": f.trace,
        "report": report,
    })
    st.last_report = report

    if captured:
        st.captured_by, st.capture_how = captured
        st.status = CAPTURED
    else:
        st.round_no = R + 1
        for h in st.hunters.values():
            h.order = None
        f.order = None
        st.status = ROUND_OPEN
    return report


# ---------------------------------------------------------------------------
# 공개 뷰 (도주자 정보를 구조적으로 포함할 수 없다)
# ---------------------------------------------------------------------------
@dataclass
class PublicView:
    round_no: int
    status: str
    trace: int
    bar: str
    signal: str
    hunters: List[Dict[str, Any]]           # {name, room, tag, submitted}
    locks: List[str]
    markers: List[str]
    steam: List[str]
    map_rows: List[Dict[str, Any]]
    map_id: str


def public_view(st: GameState) -> PublicView:
    rep = st.last_report or {}
    show_round = rep.get("round", 0)
    hunters = []
    for i, (uid, h) in enumerate(st.hunters.items(), start=1):
        hunters.append({"name": h.name, "room": h.room, "tag": str(i),
                        "submitted": h.order is not None, "dazed": h.dazed_until >= st.round_no})
    return PublicView(
        round_no=show_round,
        status=st.status,
        trace=int(rep.get("trace", 0)),
        bar=rep.get("bar", _bar(0)),
        signal=rep.get("signal", "none"),
        hunters=hunters,
        locks=list(rep.get("locks", [])),
        markers=list(rep.get("markers", [])),
        steam=list(rep.get("steam", [])),
        map_rows=st.map_rows,
        map_id=st.map_id,
    )
