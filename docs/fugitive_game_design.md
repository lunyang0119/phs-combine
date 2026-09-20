# 침입자 추적 (Fugitive) — 디자인 문서 v0.2

Status: **IMPLEMENTED (v1)** — decisions from the review are folded in (§11). Tunables and sim results in §7.3 / §9.

A 1-vs-N hidden-movement minigame for the train RP. The bot plays the crew's
handheld signal tracker; hunters (2–6 players) chase an unidentified intruder
through car #2077; the intruder is piloted by hand from a private control
server. The ending is fixed: the intruder is always caught. Everything below is
tuned for *how long* and *how* that happens, not *whether*.

---

## 0. Codebase findings (what the design builds on)

| Area | Finding | Consequence for this feature |
|---|---|---|
| Framework | `discord.py` 2.x, `commands.Bot` subclass in `main.py`, slash commands via `app_commands` in Cogs, `self.tree.sync()` global only | New Cog `fugitive.cog`; admin commands need a separate `tree.sync(guild=control_guild)` |
| State pattern | `combat_commands.py` keeps `self.active_battles: Dict[channel_id, dict]` + `asyncio.Lock` per channel, all in memory, lost on restart | Same shape, plus a JSON snapshot on disk (§8) |
| Sheets | `SheetsHandler.__init__` opens every worksheet by name at boot and caches most as DataFrames; uncached sheets are read with `get_all_records()` inside `asyncio.to_thread` | Add 3 optional worksheets, opened with `try/except WorksheetNotFound`; read **once** in `/추적기 시작`, never written |
| UI | `view.py` uses `discord.ui.View` with buttons/selects, `constants.ACTION_VIEW_TIMEOUT = 600` | Round message uses a persistent View (timeout `None`, fixed `custom_id`s) so it survives restarts |
| RNG | `combat/random_utils.py` singleton with `set_seed` | Engine takes an injectable `random.Random`; sim seeds it |
| Korean particles | `shop_commands.get_korean_particle()` already exists | Move to `utils.py`, reuse for the capture line |
| Admin gating | Only `@app_commands.default_permissions(administrator=True)` today; no user allowlist anywhere | New: guild-scope + `FUGITIVE_ADMIN_IDS` env allowlist (§6) |
| Tests / CI | None (`combat/test.py` is a stub; `.github/` holds only a prompt file) | Engine is pure Python so it can carry `pytest` tests and the Monte Carlo sim without Discord |

---

## 1. Design goals and non-goals

**Goals**
1. Fully automated for hunters; the fugitive side is manual admin input.
2. Same *feel* and roughly the same round count for 2 and for 6 hunters.
3. Capture is inevitable by construction (three independent fuses, §3.6).
4. Every tunable is a runtime value changeable mid-game from the control server.
5. Hidden state never touches Google Sheets or the public channel.
6. Bot text is diegetic (the tracker talking) and never describes the intruder's appearance. Only "신호", "침입자 신호", "미확인 신호".

**Non-goals**
- No win/lose scoring for hunters. No leaderboard.
- No aftermath narration; the bot's last line is the capture line.
- No multi-game concurrency (one game per bot process).

---

## 2. Challenges to the proposed decisions

| Proposal | Verdict | Reasoning / replacement |
|---|---|---|
| Simultaneous submission + timer, no turn rotation | **Keep** (async, 12 h default) | Hunters submit in any order; the bot shuffles the **resolution order** each round, which decides scan slots, capture ties and event order. |
| Fugitive commits first, hunters after | **Drop the ordering rule** | Ordering only matters if the fugitive can *see* hunter orders. Make that a quickhack (핑, §3.4) instead of a fixed rule. Round closes when everyone has committed or the timer expires; the fugitive may commit at any time in the window. A `fugitive_grace_sec` gives you a short window after the last hunter commits. |
| Fugitive timeout = stays in place | **Keep** | Also the correct default for a dropped connection. |
| Edge crossing = capture | **Keep**, and make it one of only two ways to catch | Sim (§9) showed that if merely *entering* the fugitive's room captures, 4 hunters clear a 12-room car in ~6 rounds even with all tracker information switched off — the grid is too small for guess-capture. Default `capture_mode=search`: capture by 수색 in the fugitive's room, by a doorway collision (edge swap or running through an occupied room), and by the freeze finale. `capture_mode=contact` (+ `contact_capture_chance`) remains available live for a short, tense variant. |
| Room device = usable quickhack AND queryable trace | **Keep, split into two channels** | (a) **Passive signature**: any hack publicly broadcasts its *device type* (narrows to 3 rooms). (b) **Active scan** (`추적`): reveals the device type of the fugitive's *current* room. Both are 3-room narrowings, so all information is "which of three". |
| Door lock blocks an edge for 1 round | **Keep**, make range a tunable | `doorlock_range=0` (edges touching the fugitive's room only — a strong tell) vs `-1` (any edge — a bluff tool). Tune live. |
| RAM + trace gauge | **Keep**, add passive trace | A fugitive who never hacks must still get caught. `trace_passive` per round is the fuse. |
| 9 rooms for 2–3 hunters; scale by map and resources, not hunter actions | **Keep**, and 12 rooms are **not enough for 5–6** | 12 rooms / 6 hunters = 2 rooms per hunter; expected capture in ~3 rounds with plain co-location. Proposal: 16-room map (row D) for 5–6. Draft in §7.2. |
| Bot = handheld tracker, "Activating Combat Mode" | **Keep** | The tracker also owns the 추적률 gauge, which becomes the visible clock. |
| One pinned public table, true state only in control server | **Keep** | Plus a per-round message with buttons (mobile). |
| Guild-scoped admin commands + allowlist | **Keep** | §6. |
| Local persistence | **Keep — JSON, not SQLite** | One game at a time, <20 KB of state; atomic write (tmp + rename). SQLite adds nothing here. |
| Monte Carlo tuning | **Keep** | §9. Bots give a lower bound on round count; a human fugitive lasts longer. |
| Correct 이/가 | **Do it** | Hangul names get the right particle; non-Hangul names fall back to the literal `이(가)`. |

**One added mechanic to consider (optional, off by default): 격리 프로토콜 (lockdown).**
From round `lockdown_start`, the tracker announces a section that seals permanently each `lockdown_every` rounds (a column at a time, farthest from the hunters' median). A battle-royale-style shrinking zone is the most reliable pacing tool in this genre and fully diegetic (the crew is sealing the car). It is the strongest lever for "similar round count regardless of player count". Asked in §11.

---

## 3. Rules specification

### 3.1 Board
- A directed-free graph of rooms. Each room has exactly one **device** type: `curtain`(커튼) / `coffeepot`(커피포트) / `light`(조명) / `speaker`(스피커).
- Edges are doors. Same-device rooms are never adjacent (validated at load).
- Map v0 (12 rooms) is the one in the brief; 9-room and 16-room variants in §7.2.

### 3.2 Round loop
1. **Round opens.** Bot posts/edits the round message (`R{n}`, timer end as a Discord `<t:…:R>` timestamp, submission counter `제출 3/5`). Pinned table already reflects the previous resolution.
2. **Orders.** Each hunter submits one order: 이동 (move to an adjacent room) / 수색 (search current room) / 추적 (active scan) / 대기 (stay). Re-submitting overwrites. The fugitive submits move (or stay) + at most one quickhack from the control server.
3. **Close.** When all hunters and the fugitive have committed, or the timer expires (missing orders → 대기, fugitive → stay, no hack). After the last hunter commits, the fugitive gets `fugitive_grace_sec` extra seconds if not yet committed.
4. **Resolve** (§3.5) → post the tracker report → edit pinned table → open next round, or post the capture line and go silent.

### 3.3 Hunter actions
| Order | Effect |
|---|---|
| 이동 `room` | Move along one edge. If the edge is locked this round, the hunter stays and is told `문이 잠겨 있다`. |
| 수색 | Stay and search the current room. Catches the fugitive if they end the round here (hidden or not). In `search` mode this is the main way to capture. Fails in a steam-filled room. |
| 추적 | Stay and operate the tracker: learn the device type of the room the fugitive was in at the **start** of the round (`scan_lag=1`; 0 = after moving). Only the first `scan_budget` scans (in the shuffled order) succeed; later ones return `채널 혼선`. During blackout returns `노이즈`. Each successful scan adds `scan_trace` to the gauge — hunters have a hand on the clock. |
| 대기 | Nothing (also the timeout default). |

Hunters' orders are visible to each other on the round message (they all carry crew communicators); the intruder can only read them via 핑. A hunter dazed by steam is skipped for that round.

### 3.4 Fugitive actions
Per round: **one movement** (up to `fugitive_speed` doors, default 1; the path stops at a locked door and running through a room a hunter starts or ends in is a collision) and **at most one quickhack**. Quickhacks cost RAM immediately and add to the trace gauge. The device-bound ones require the fugitive to be in a room with that device *at the start of the round* (you hack what is around you, then run).

| Quickhack | Device needed | Cost (RAM / trace) | Effect |
|---|---|---|---|
| 핑 Ping | none | 1 / +5 | Instant at cast time (not at resolution): the control channel shows all hunter orders submitted so far this round and updates live. This is the "can I see hunter submissions" lever. Config `ping_enabled`, `ping_cost`. |
| 문 잠금 Door lock | none | 2 / +10 | Lock one edge for this round's resolution. Within `doorlock_range` of the fugitive's room (0 = touching, -1 = anywhere). Shown as ⛔ on the public table after resolution. Also blocks the fugitive. |
| 교란 Distract | speaker | 3 / +15 | Plant a decoy: this round's report shows a `◎ 신호 감지` marker on a room of your choice. Indistinguishable from a real last-seen marker. |
| 광학 재부팅 Blackout | light | 3 / +15 | The tracker report for this round is suppressed (no signal strength, no signature, no marker update); scans this round return 노이즈. Config `blackout_blocks_scan`. |
| 은신 Hide | curtain | 4 / +20 | Movement becomes "stay". Immune to co-location capture this round and the next (`hide_duration=2`). 수색 in the room still catches. |
| 과부하 Overload | coffeepot | 2 / +15 | The pot bursts into steam in the fugitive's current room for this round: hunters who end the round there are dazed (`overload_daze_rounds`, their next order is forced to 대기) and 수색 there fails. Broadcasts a coffeepot signature. |

- Every quickhack (except 핑, config `ping_signature=false`) broadcasts its device type as a **signature** in this round's report: `스피커 계열 장치에서 비정상 접근 감지`. That narrows the fugitive to the 3 rooms of that type at cast time.
- RAM: `ram_max`, `ram_regen` per round, starts full. Trace: 0–100, `trace_passive` per round regardless of hacks, plus `scan_trace` per successful hunter scan.

### 3.5 Resolution order (deterministic)
```
1. fugitive hack pre-effects: door lock placed; hide → move=stay; blackout flag; decoy queued; overheat applied
2. hunter moves            (locked edge → stay)
3. fugitive move           (path of ≤ fugitive_speed doors; stops at a locked door; frozen → stay)
4. capture check, priority: (a) 수색 in fugitive's final room  > (b) doorway collision (edge swap or a hunter
                          starts/ends in a room the fugitive ran through)  > (c) [contact mode only] co-location,
                          rolled against contact_capture_chance unless the hunter stayed; a miss reveals the room
                          tie-break inside a class: this round's shuffled hunter order
5. scans answered (shuffled order, up to scan_budget; each adds scan_trace)
6. trace += trace_passive; tier effects (§3.6); ram += ram_regen
7. build tracker report; last-seen marker = fugitive position as of (round − lag(tier)); decoys merged in
8. failsafe check (§3.6)
9. commit: positions, round += 1, expire locks/hide/blackout, save snapshot
```

### 3.6 Why capture is inevitable — three fuses
| Fuse | Mechanism |
|---|---|
| Trace tiers | `trace ≥ t1 (40)`: last-seen marker auto-updates each round with lag 2. `≥ t2 (70)`: lag 1. `≥ t3 (100)` "TRACED": live position every round and all quickhacks disabled. |
| Overheat lock | `overheat_rounds (3)` rounds after reaching 100, the deck burns out: the fugitive is **frozen** (cannot move) and shown live on the tracker until a hunter reaches the room and searches. This is the canonical finale in most games. |
| Round cap | At `max_rounds (20)` the fugitive is frozen regardless. |

With the 4-hunter defaults (`trace_passive=4`, `scan_trace=5`) a hack-heavy fugitive is TRACED around round 11 and frozen by 14; a silent one runs into `max_rounds=18`. All tunables.

### 3.7 Tracker report (public, each round, in this order)
1. `R{n}` header and 추적률 bar (trace as a percentage — the visible clock).
2. **신호 강도**: `강 / 중 / 약` = minimum hop distance from any hunter to the fugitive, banded by `signal_bands="1,3"` (강 ≤1, 중 ≤3, 약 farther); `판독 불가` under blackout. One value per round regardless of hunter count, which is what keeps information per round from scaling with N. Config `signal_mode = banded | exact | off`.
3. **시그니처**: device type of any quickhack this round (or nothing).
4. **표식**: `◎` markers (last-seen per tier lag, plus any decoy).
5. Locked doors `⛔`, hunters told `문이 잠겨 있다`.
6. Per-scan results are DM'd/ephemeral to the scanning hunter **and** echoed in the report (team info).

### 3.8 Start and end
- Start (`/추적기 시작`): read config once, build map, place fugitive at `fugitive_start` (default `auto` = most central room), spread hunters over the rooms farthest from it (`hunter_spawn_rooms=auto`), post `Activating Combat Mode`, pin the table, open round 1. The fugitive's position is never shown until captured (`reveal_start=false`).
- End: bot posts exactly `수수께끼의 인영을 {name}{이|가} 잡았다! Conflict Resolved.` and stops. Nothing else is posted in the game channel until `/추적기 요약` is invoked from the control server.
- Capturer name: Characters-sheet `name` for the hunter's discord_id if registered, else guild display name.

---

## 4. State machine

```
IDLE ──/추적기 개설──▶ LOBBY ──/추적기 시작──▶ ROUND_OPEN ──close──▶ RESOLVING ─┬─▶ ROUND_OPEN (next)
  ▲                     │ (hunters join)          │  ▲                          │
  │                     └──/추적기 종료──▶ IDLE   │  └──/추적기 재개── PAUSED ◀──/추적기 일시정지
  │                                              │
  └────────────────/추적기 종료──────────────── CAPTURED (silent; /추적기 요약 allowed)
```
- `ROUND_OPEN` owns one `asyncio.Task` timer; the deadline is persisted so a restart re-arms the remaining time.
- `RESOLVING` is guarded by an `asyncio.Lock`; late orders are rejected with `이미 처리 중`.
- Admin may force-close a round (`/추적기 라운드종료`), extend the timer, or edit any tunable in any state.

---

## 5. Commands

### 5.1 Public (game guild, hunters)
The round message carries buttons **[이동] [수색] [추적] [대기]**; [이동] opens an ephemeral select listing only the rooms adjacent to *that* hunter. Slash equivalents exist for people who prefer typing:

| Command | Notes |
|---|---|
| `/탑승` | Join the lobby (LOBBY only). Admin can also add/remove players. |
| `/이동 <room>` | Autocomplete offers only adjacent rooms. |
| `/수색` `/추적` `/대기` | As §3.3 |
| `/현황` | Re-post the current public table (if the pin scrolled off on mobile). |
| `/추적기 도움말` | Rules in Korean. |

### 5.2 Admin (control guild only + user allowlist)
| Command | Notes |
|---|---|
| `/추적기 개설 <channel_id>` | Create lobby bound to the public channel. |
| `/추적기 참가자 <추가|제거> <user_id> [이름]` | Manual roster edits while in the lobby. |
| `/추적기 시작 [fugitive_start]` | Read sheets once, start round 1. |
| `/도주 <이동|대기> [핵] [대상]` | The fugitive's whole order in one command; re-issue to overwrite until the round closes. `대상` = edge (`A2-B2`) for 문 잠금, room for 교란. |
| `/도주 핑` / `/도주 취소` | Cast 핑 immediately / withdraw this round's fugitive order. |
| `/추적기 상태` | True state: fugitive room, RAM, trace, all pending orders, decoys, history tail. |
| `/추적기 설정 <key> <value>` / `/추적기 설정보기` | Mutate/show any tunable live. Validated by type/range. |
| `/추적기 라운드종료` / `/추적기 연장 <sec>` | Force-resolve now / extend the timer. |
| `/추적기 일시정지` / `/추적기 재개` | Freeze the timer. |
| `/추적기 위치 <hunter|fugitive> <room>` | Manual override (RP corrections). |
| `/추적기 요약 [channel]` | Post the post-game summary (round-by-round log, capture details). Only after CAPTURED. |
| `/추적기 종료` | Abort/reset to IDLE (asks for confirmation). |

Gating: commands are registered with `app_commands.guilds(CONTROL_GUILD_ID)` **and** checked against `FUGITIVE_ADMIN_IDS`. Both come from `.env`. Public commands refuse to run in the control guild and vice versa.

---

## 6. Security / information boundaries
- Hidden state lives only in process memory and `data/fugitive_state.json` (gitignored, mode 600).
- Nothing about the fugitive is ever written to Sheets. Sheets are read exactly once per game, in `/추적기 시작`.
- Public-channel renders are produced from a `PublicView` projection that structurally cannot contain the fugitive's room, RAM, or orders (separate dataclass; the renderer never receives the full state).
- Logs at INFO must not print the fugitive's room; DEBUG may.

---

## 7. Data model and sheet schema

### 7.1 In-memory / JSON state
```
GameState
  game_id, status, round_no, deadline_ts, created_ts
  guild_id, channel_id, table_message_id, round_message_id, control_channel_id
  map_id, rooms: {id: {device, neighbors[], col, row}}
  config: {key: value}                      # effective tunables (defaults ⊕ sheet ⊕ scaling ⊕ admin edits)
  hunters: {user_id: {name, room, order: {type, target, ts}|null}}
  fugitive: {room, ram, trace, order: {move, hack, target}|null,
             hidden_until, blackout_until, frozen, hacks_disabled, overheat_uses_left,
             traced_at_round}
  locks: [{edge, round}]
  decoys: [{room, round}]
  position_history: [room per round]        # hidden; feeds last-seen with lag
  log: [RoundLog{round, hunter_orders, fugitive_order, events[], report}]
  captured_by: user_id | null
```
`PublicView` = round_no, trace, hunters{name, room}, locks, markers[], signal band, signature, scan results.

### 7.2 Sheets (public, config only)

**`Fugitive_Map`**
| map_id | room | device | neighbors | col | row |
|---|---|---|---|---|---|
| car2077_12 | A1 | curtain | A2,B1 | 1 | 1 |
| car2077_12 | A2 | coffeepot | A1,A3,B2 | 2 | 1 |
| … | | | | | |

Loader validates: symmetric adjacency, connected graph, no same-device neighbours, unique room ids.

Draft variants (to confirm):
- `car2077_9` = v0 columns 1–3 (A1..C3). Device counts 2/3/2/2.
- `car2077_16` = v0 + row D: `D1 speaker: C1,D2 | D2 curtain: C2,D1,D3 | D3 light: C3,D2,D4 | D4 coffeepot: C4,D3`. Each device type has 4 rooms.

**`Fugitive_Config`** — `key | value | note`, one row per tunable (defaults below).

**`Fugitive_Scaling`** — one row per hunter count, columns override Config:
| hunters | map_id | ram_max | ram_regen | trace_passive | scan_budget | max_rounds |
|---|---|---|---|---|---|---|
| 2 | car2077_9 | 8 | 1 | 3 | 1 | 22 |
| 3 | car2077_9 | 8 | 1 | 4 | 1 | 20 |
| 4 | car2077_12 | 10 | 1 | 4 | 1 | 18 |
| 5 | car2077_16 | 12 | 1 | 6 | 2 | 16 |
| 6 | car2077_16 | 12 | 2 | 7 | 2 | 15 |

An `extra` column holding a JSON object is merged on top (any tunable).

Missing sheets → built-in defaults in `fugitive/config.py`, so the game runs even before the sheet exists.

### 7.3 Tunables (all `/추적기 설정`-mutable; defaults in `fugitive/config.py`)
```
round_timer_sec=43200  fugitive_grace_sec=1800  early_resolve=true
fugitive_speed=1  capture_mode=search  contact_capture_chance=0.5
ram_max=10  ram_regen=1
trace_passive=5  trace_t1=40  trace_t2=70  trace_t3=100  overheat_rounds=3  max_rounds=24
scan_budget=1  scan_trace=5  scan_lag=1  signal_mode=banded  signal_bands=1,3
signature_reveal=true  reveal_start=false
ping_enabled=true ping_cost=1 ping_trace=5 ping_signature=false
doorlock_cost=2 doorlock_trace=10 doorlock_range=0
distract_cost=3 distract_trace=15
blackout_cost=3 blackout_trace=15 blackout_blocks_scan=true
hide_cost=4 hide_trace=20 hide_duration=2
overload_cost=2 overload_trace=15 overload_daze_rounds=1
render_mode=text            # image = PNG via Pillow when a CJK font is available
hunter_spawn_rooms=auto  fugitive_start=auto
lockdown_enabled=false lockdown_start=8 lockdown_every=3   # TODO, not implemented (§11-8)
```

---

## 8. Persistence and restart
- `fugitive/store.py`: `save(state)` → write `data/fugitive_state.json.tmp`, `os.replace`. Called after every transition and every accepted order.
- On `cog_load`: if a snapshot exists with status `ROUND_OPEN`/`PAUSED`/`CAPTURED`, restore, re-register the persistent View, re-arm the timer with `deadline_ts − now` (min 15 s), and edit the round message with `재접속 완료`.

---

## 9. Tuning: Monte Carlo sim
`python -m fugitive.sim --hunters 4 --map car2077_12 --games 5000 --seed 1`

- Uses the pure engine with an injected RNG (no Discord).
- Hunter bots: `random`, `greedy` (move toward the centroid of the current candidate set = rooms consistent with all signatures/scans/markers; search when the candidate set ≤ 2 and a curtain signature is live; scan when the candidate set > 4).
- Fugitive bots: `stealth` (maximise distance, never hack), `hacker` (lock when a hunter is adjacent, hide when ≥2 hunters within 1 hop, overheat when trace ≥ 60, distract otherwise when RAM allows).
- Output: p10/p50/p90 rounds-to-capture and capture-cause breakdown (search / swap / co-location / frozen) per (hunters, map, config). The scaling table in §7.2 is what we adjust until p50 lines up across hunter counts.

Results with the §7.2 defaults (400 games each, seed 1):

| hunters | hacker fugitive p50 (p10–p90) | stealth fugitive p50 | ends by freeze |
|---|---|---|---|
| 2 | 17 (15–19) | 25 | ~99% |
| 3 | 14 (8–15) | 21 | ~80% |
| 4 | 15 (7–17) | 8 | ~60% |
| 5 | 12 (8–14) | 15 | ~80% |
| 6 | 12 (7–13) | 14 | ~80% |

Why `contact` capture was rejected as the default: with `capture_mode=contact` and every tracker signal turned **off**, 4 greedy hunters still caught the fugitive in a median of 8 rounds on 12 rooms and 9 on 16 rooms; with signals on, 6. No information tuning reaches 15 on a train car, so the clock (trace + freeze) has to carry the pacing and hunters' direct captures are the bonus.

---

## 10. Module layout
```
fugitive/
  __init__.py
  engine.py      pure rules: Map, GameState, submit_order(), resolve_round(), PublicView
  config.py      defaults, sheet loader, validation, scaling merge
  strings.py     every Korean string, one place
  render.py      pinned table + round report (code block)
  store.py       JSON snapshot
  views.py       persistent round View, room select
  cog.py         FugitiveCog: public commands, timer task, restart restore
  admin.py       control-server commands (same Cog file or split; guild-scoped)
  sim.py         Monte Carlo
tests/test_engine.py
docs/fugitive_game_design.md   (this file)
```
`main.py`: add `fugitive.cog` to `cogs_to_load`; after the global sync, `tree.sync(guild=discord.Object(CONTROL_GUILD_ID))`. `.env` gains `FUGITIVE_CONTROL_GUILD_ID`, `FUGITIVE_ADMIN_IDS` (comma-separated). `.gitignore` gains `/data/`.

Public table draft (code block; ASCII inside the grid because mixed-width Hangul breaks monospace alignment on mobile):
```
R07  추적률 ▓▓▓▓▓░░░░░ 48%   신호: 중
+------+------+------+------+
| A1 c | A2 k | A3 l | A4 c |
|  1   |      |  ◎   |      |
+------+--⛔--+------+------+
| B1 s | B2 c | B3 s | B4 l |
|      | 2 4  |      |      |
+------+------+------+------+
| C1 k | C2 l | C3 k | C4 s |
|  3   |      |      |      |
+------+------+------+------+
c=커튼 k=커피포트 l=조명 s=스피커  ◎ 신호 감지  ⛔ 잠김
1 철수  2 영희  3 민수  4 지우
```
An image renderer (Pillow) can be added later behind `render_mode=image` if the code block proves unreadable; it needs a CJK font on the host.

---

## 11. Decisions (from review of v0.1)

1. **Pacing**: median 15 rounds at 4 hunters, fewer as hunters increase → `max_rounds` 22/20/18/16/15 and `trace_passive` scale with hunter count (§7.2).
2. **Timer**: async, 12 h per round by default, resolve early when everyone has submitted.
3. **Quickhacks**: list kept; coffeepot 과부하 is a steam distraction (daze + blocks 수색), not a trace dump.
4. **Numbers**: §7.3 defaults are the sim starting point.
5. **Hunter actions**: one action per hunter per round; the bot shuffles the resolution order each round.
6. **Tracker**: banded minimum distance (one value per round).
7. **Maps**: 9 / 12 / 16 by hunter count, row-D draft confirmed, no physical end-to-end loop.
8. **격리 프로토콜 (shrinking zone)**: TODO for later; config keys reserved, not implemented.
9. **Rendering**: code block by default; `render_mode=image` PNG renderer implemented behind a font check (see `fugitive/image_render.py`) for you to evaluate on Discord.
10. **Orders**: hunters see each other's orders (communicators).
11. **Capturer name**: Characters sheet `name`, falling back to the guild display name.
12. **Opening**: fully hidden; nothing about the fugitive is shown until capture (tier markers still appear as the trace gauge climbs — that is the fuse).

Added during implementation, all live-tunable: `capture_mode` (default `search`, see §2), `contact_capture_chance`, `fugitive_speed`, `scan_lag`, `scan_trace`, `signal_bands`, `render_mode`; a frozen fugitive is always shown live.
