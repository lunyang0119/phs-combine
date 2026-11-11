# `combat/` vs `cardbattle/` — Mechanism Comparison

This document compares the two battle systems in this repository:

- **Combat** — the original FF7-style RPG battle. The `combat/` package holds its helpers, but the turn loop and every action live in `combat_commands.py`, with formulas in `utils.py`, `character_models.py` and `constants.py`. Those files are covered here too, because `combat/` alone does not describe the mechanism.
- **Card battle** — the Cyberpunk-themed card minigame in `cardbattle/`, with its rules in `cardbattle/engine.py`.

Everything below is taken from the code as it stands. Where the code disagrees with `combat/combat_rule.md`, the code is described and the difference is listed in [section 12](#12-code-observations).

---

## 1. Summary

| Aspect | Combat | Card battle |
|---|---|---|
| Genre | Initiative-based turn RPG | Simultaneous-commit card game |
| Unit of play | One actor's turn | One round for everyone |
| Player choice | Fixed action menu (attack, magic, defend, evade, limit break) | One card from a random hand of 3 |
| Enemy behaviour | Weighted random action, random target | Scoring AI with personalities; intent shown before players choose |
| Information | Enemy action unknown until it happens | Enemy card *kind* is public, target hidden unless scanned |
| Resources | HP, MP, limit flag, materia | HP, team firewall gauge, team pot (에디), weapon grade |
| Luck sources | 2d10 damage dice, crit, evasion, DMW slot | 0–4 damage variance, card draw, effect chances |
| Extra win/lose paths | None (wipe one side, or 100-round cap) | Chip shutdown, life-support collapse, NCPD escape minigame, tutorial end |
| Rules location | Inside the Discord cog | Pure-Python engine, no Discord import |
| Content definition | Google Sheets rows + hardcoded branches | CSV rows with an effect mini-language |
| State | `dict` + pandas DataFrame, per channel | Dataclasses, one session, JSON snapshot |
| Tests / sim | None | Six test modules + Monte Carlo `sim.py` |

---

## 2. Architecture

### Combat
- `CombatCog` (`combat_commands.py`, ~3.3k lines) owns the rules. `battle_loop` is one long coroutine per channel that awaits Discord views for each player's turn.
- `combat/` supplies static helper classes: `BattleManager` (state init, turn order, turn start/end, battle end, scaling), `CombatUtils` (HP/status writes, crit, end check), `StatusEffectManager` (duration ticks, symbiosis), `BossSkillSystem` (HP-threshold skills).
- Rules and Discord I/O are interleaved: most helpers take an `interaction` or `channel` and send embeds while mutating state.
- Concurrency is guarded by one `asyncio.Lock` per channel (`battle_locks`), plus a pause/resume `asyncio.Event`.

### Card battle
- `engine.py` is a state machine with no Discord dependency: `IDLE → LOBBY → PREP → ROUND_OPEN → RESOLVING → (ESCAPE) → ENDED`.
- `cog.py` is a thin layer: it calls `submit_choice`, `use_firewall`, `resolve_round` and renders the returned report through `render.py` → `embeds.py` (and `image_render.py` for the PNG board).
- It is event-driven. Nothing blocks waiting for a player; persistent buttons with fixed `custom_id`s call into the engine.
- Rule violations are `RuleError` codes that the cog maps to user-facing strings.

---

## 3. Battle state and persistence

| | Combat | Card battle |
|---|---|---|
| Container | `battle_state` dict; participants in a `participants_cache` DataFrame copied from `Combat_Status` | `BattleState` dataclass with `Combatant`, `Card`, `Modifier`, `Choice` |
| Concurrent battles | One per channel (`active_battles[channel_id]`) | One session for the whole bot (`CardBattleCog.state`) |
| Side detection | ID is numeric → player, otherwise monster | Explicit `side` field (`ally` / `enemy`) |
| Persistence | `Combat_Status` sheet synced every `SYNC_INTERVAL` (2) rounds; sheet cleared at battle end; boss `used_flag` written to `Boss_Skills` | `data/cardbattle_state.json` snapshot; team pot in `data/cardbattle_gig.json` |
| Restart | Live battle is lost (`active_battles` is memory only) | State is reloaded and buttons keep working |
| Character data | Reads and writes HP/MP/status on sheet-backed rows | Reads stats from `Characters` once at join; starts at full HP unless `use_current_hp` |

---

## 4. Turn structure

### Combat — sequential initiative
1. `determine_turn_order` shuffles once, then sorts by `agility` descending (so ties are random). The order is kept for the whole battle and only recomputed when an intruder joins or the `time_warp` environment reshuffles it each round.
2. Each actor acts in order and the result applies immediately, so later actors see the changed board.
3. At the start of an actor's own turn: `defend_flag` / `evasion_flag` drop by 1 and that actor's status durations tick.
4. Round end (`_handle_round_end`): boss-skill triggers, symbiosis, downed-player countdown, lava damage, sheet sync, round counter +1.
5. Players have a view timeout (`ACTION_VIEW_TIMEOUT` 600 s); on timeout the turn is skipped. The battle auto-ends after `MAX_COMBAT_ROUNDS` (100).

### Card battle — simultaneous rounds
1. `open_round`: every living enemy picks a card first. Only the card **kind** is published (`intents`).
2. Players pick one card each from a private hand. There is no timer; a player who has not chosen when the host ends the turn simply waits.
3. `resolve_round` resolves both sides together, sorted by **phase** (hack 0 → defend 1 → attack/special 2), then agility descending, then random.
4. End of round: modifiers tick, `regen`, drag progress, life-support clock, then the end checks.

Consequences:
- In Combat, agility decides who acts first overall. In Card battle the **card type** decides first, and agility only breaks ties inside a phase.
- In Card battle a fast hack can stun an enemy and cancel the attack it already committed to in the same round. Combat has no equivalent because the monster's action is not chosen until its turn.

---

## 5. Actions

| | Combat | Card battle |
|---|---|---|
| Choice set | Always the same menu; magic needs an equipped materia and MP; limit break needs `limit_flag == 0` | 3 cards drawn without replacement, weighted by `weight`, from the ally pool, redrawn every round |
| Forced options | — | `fixed` cards handed out by rules: `break_free` (only card while grappled), `pass_terminal`, `field_mod` |
| Team action | — | Firewall option, once per round, does not use the player's card |
| Job effect | Mechanical: Striker/Soldier ×1.5 physical, Weaver/Turks +1d10 magic, a limit break per job | Display only (name mapping and weapon name list) |
| Per-turn bonus roll | DMW slot runs after every player turn (4d10 + charm bonus vs threshold 35, or 18 in boss mode) and can grant buffs, reset limit flags, apply death sentence, etc. | None |
| Admin tools | `/cheat`, pause/resume, debug command | `옵션:관리자` shows the full card pool for one battle |

---

## 6. Damage, defence and healing

### Combat
```
bonus(stat)   = round(max(0, (stat - 10) / 2))
physical      = bonus(STR) + 2d10 + 5      ×1.2 with zack_buff      ×1.5 for 스트라이커 / 솔져
magic         = bonus(MAG) + P + 1d(P) + 4, P = max(5, bonus(materia power))   +1d10 for 마테리아 위버 / 턱스
```
Applied to the target through `Character.take_damage`:
- `geshtinn_invincible` → no damage.
- `evasion_flag > 0` → dodge chance `(floor((AGI-10)/2) + 1d10) / 200`, halved under `gravity_anomaly`; impossible below AGI 10.
- `defend_flag > 0` → flat reduction `floor((STR-10)/2) + 1d11 + 4`.
- `isolde_protection` → ×0.5.

For a player's physical attack, the same-target combo multiplier (+10 % per consecutive hit, up to +80 % at 9 hits) and the critical multiplier (×1.5) are then applied on top, with a floor of `STR − 10`. The combo counter is shared by the whole party, resets when the target changes or a player defends/evades, and refunds MP when it breaks (5 MP after 5–8 hits, 10 MP after exceeding 9).

Healing uses the magic formula and is forced to 0 on a target whose `is_dead` counter is above 0.

### Card battle
```
damage = card value + stat // stat_divisor(2) + weapon bonus + rand(0..damage_variance(4))
         → combo (×value or +value) → ×(1 − atk_down) → ×(1 − target shield) → ×ICE multiplier → int()
heal   = card value + stat // 2          (no variance; can revive a downed, un-looted ally)
```
- No critical hits and no dice pools; the spread is 0–4 points.
- Defence is a percentage `shield` modifier that lasts the current round, not a flat reduction.
- Evasion exists only as an enemy trait (`evade`) against single-target physical cards; the `aim` firewall option ignores it.
- `misfire` (chance to do nothing) and `redirect_random` (random new target, possibly an ally of the attacker's target side) have no Combat equivalent.
- Combos are per card and conditional on a modifier (`target_has:stun:dmg_x:1.5`), not on consecutive hits.

### Stat usage
| Stat | Combat | Card battle |
|---|---|---|
| Strength (`physics`) | Physical damage, defend mitigation, max HP at creation | `+physics // 2` on physics cards |
| Magic (`magic`) | Magic/heal power, max MP | `+magic // 2` on magic ("RAM") cards |
| Agility | Turn order, evasion chance, crit chance | Order within a phase only |
| Charm | DMW roll bonus | Loaded but not used by the resolution code read for this document |
| Scaling | `(stat − 10) / 2`: stats under 10 give nothing | `stat // 2`: every 2 points count |

---

## 7. Status effects

| | Combat | Card battle |
|---|---|---|
| Storage | String on the row: `name:duration[:value]` joined with `\|` | `Modifier(kind, value, turns_left, created_turn, source)` list |
| Detection | Substring checks such as `'petrified' in status` | `Combatant.mod(kind)` |
| Tick | At the start of the affected unit's own turn | End of round, and only for `TICKING` kinds (`shield`, `daemon`) |
| Consumption | Duration only | `ONE_SHOT` kinds (`atk_down`, `misfire`, `redirect_random`, `stun`) are consumed when they take effect |
| Stacking | Appended as another segment | Same kind merges: max value and max turns; `daemon` adds stacks |
| Effects available | Petrify, stun, death sentence, stat buffs/debuffs, crit buff, invincibility, damage halving, triple attack | Shield, attack down, misfire, redirect, stun, grapple, daemon infection |
| Stun | Multi-turn (`turks_stun`, 3) | One round; announced first and removed at round end so "vs stunned" combos work all round |

---

## 8. Being downed

| | Combat | Card battle |
|---|---|---|
| Marker | `current_hp == 0` plus an `is_dead` countdown (`DEAD_REVIVAL_TURNS` = 4) | `hp == 0`; all modifiers are cleared |
| Recovery | Countdown drops when the downed player's slot is skipped and again at round end; reaching 0 at round end revives at 1 HP. Heals do nothing while the counter is above 0 | Any heal card or the `maxdoc` firewall option revives immediately |
| Permanent loss | None for players; monsters never return | `drag` gimmick: an enemy drags the downed player and after `drag_turns` rounds sets `looted`, which blocks revival |
| Team benefit | — | A downed ally adds `firewall_per_down` to the firewall gauge |

---

## 9. Enemy behaviour and boss design

### Combat
- Per turn: a weighted random pick — 70 / 20 / 10 (attack / defend / evade), or 40 / 40 / 10 / 10 with magic enabled. Targets are random living players.
- `special_pattern` on the monster row: `charge_attack` (all-party ×2 hit every `CHARGE_ATTACK_INTERVAL` rounds), `steal_mp` (30 % chance), `rage_mode`.
- Bosses: `BossSkillSystem` checks HP thresholds from the `Boss_Skills` sheet at round end and fires each skill once (`summon`, `damage_all`, `damage_mp_drain`, `lifesteal`, `stat_debuff`, `buff_self`, `death_sentence`, `instant_hp_one`, `rage`).
- Hardcoded specials: symbiosis for `mid_boss_mother_flora` and its spores; chat keywords that buff or debuff the boss (`BOSS_KEYWORD_GOOD` / `BOSS_KEYWORD_BAD`).
- Boss mode only changes the DMW threshold and the death-sentence timer.

### Card battle
- Enemies draw a hand from their own pool and `ai.choose` scores each card: situation score × personality weight (`aggressive` / `defensive` / `hacker`) + noise. It targets players it can finish, or the strongest player for stuns.
- Mode-driven enemies (`doll` or `modes` trait) follow a weighted mode table with no-repeat rules instead of the AI.
- The tutorial replaces the AI with a scripted chooser from `Tutorial.csv`.
- Boss fights are built from composable **gimmicks** listed per battle in `Battles.csv`: `nonlethal`, `ncpd`, `drag`, `doll`, `terminal`, `first_strike`, `daemon`, `ice`, `firewall_duel`, `life_support`, `tutorial`.
- Enemies use the same card and modifier rules as players, so players can be hacked, stunned, grappled or infected.

---

## 10. Difficulty, environment and end conditions

| | Combat | Card battle |
|---|---|---|
| Headcount scaling | Automatic: monster HP, STR, MAG and AGI × difficulty (0.7–1.6) × player-count factor (0.6 / 0.8 / 1.0, +0.1 per player above 3) | Authored: one battle id per headcount (`scav3`…`scav7`) with `overrides` such as `enemy_hp_mult`, `ncpd_max`, `chip_stability` |
| Difficulty setting | Chosen at `/전투시작` | None; tuned per battle row |
| Random battlefield modifier | One environment effect rolled at start: `lava_zone`, `mana_storm`, `gravity_anomaly`, `time_warp` | None; every modifier is a declared gimmick |
| Win | All monsters at 0 HP | All enemies down (`ko`), chip at 0 (`chip`), life-support clock at 0 (`collapse`), tutorial script finished |
| Loss | All players at 0 HP | All players down (`wipe`), or caught in the escape phase |
| Other endings | 100-round cap, manual `/전투종료` | NCPD alert full → 5-turn rock-paper-scissors escape (`escaped` / `caught`); host or admin can end |
| Outcome quality | Win or lose | Graded: overkill and noise penalties, boss state `fine` / `hurt` / `critical`, payout ratio |
| How you win matters | No | Yes: `nonlethal` punishes overkill (damage exceeding remaining HP by 15+, or any `lethal` card), `loud` cards raise the police alert |

---

## 11. Economy and meta-progression

- **Combat** has no in-battle economy. MP is the only spendable resource, limit breaks are gated by `limit_flag` (re-armed by a DMW effect), and nothing is paid out by the battle code.
- **Card battle** wraps each fight in a gig loop (`gig.py`): a persistent team pot is split into allowances in the `PREP` phase, players upgrade a random weapon (safe upgrade vs d10 gamble), unspent money returns to the pot, `field_mod` can spend it mid-fight, and the payout at the end depends on headcount, result ratio and penalties. Weapons are discarded after the battle; only the pot persists.
- The **firewall gauge** is a comeback mechanic with no Combat counterpart: it fills when allies take damage, defend or go down, and is spent on team effects (`cover`, `reboot`, `aim`, `scan`, `maxdoc`).

---

## 12. Code observations

Found while reading; none of these were changed.

1. **`random_utils.get_random()` raises.** `RandomManager.get_random` calls `self._rng.get_random()`, which does not exist on `random.Random` (verified: `AttributeError`). Combat calls it for critical rolls, evasion (`utils.calculate_evasion_chance`), the `steal_mp` pattern and monster hit reactions. Card battle avoids it: its `RandomUtilsRng` adapter uses `uniform(0, 1)`.
2. **`BattleManager.initialize_battle_state` compares the function itself**: `random_utils.get_random < constants.ENV_EFFECT_CHANCE` has no call parentheses and raises `TypeError` in Python 3 (verified in isolation; a full battle start was not run).
3. **Rule doc vs constants.** `combat_rule.md` says the environment chance is 20 % and the downed counter is 3; `constants.py` has `ENV_EFFECT_CHANCE = 0.8` and `DEAD_REVIVAL_TURNS = 4`, while `Character.take_damage`, the death sentence and symbiosis code still write a literal `3`.
4. **Crit bonus unit.** The comment says `0.05 %` per agility bonus point, but the code adds `0.05` to a 0–1 probability, i.e. 5 percentage points (AGI 20 → 10 % + 25 %).
5. **Rounding.** The rule doc writes `floor((STR-10)/2)` for physical damage; `apply_damage_rounding` uses `round()`. Only evasion and defence use `floor`.
6. **Randomness injection.** Card battle takes an `rng` argument everywhere, so tests and `sim.py` run seeded. Combat calls the global `random_utils` singleton directly and has no tests.

---

## 13. Which properties each design gives

- **Combat** is swingy and character-driven: wide dice ranges, crits, job multipliers and the DMW slot make individual turns dramatic, and the character sheet (job, materia, stats above 10) matters a lot. Encounters are quick to author (pick monsters and a difficulty) but special behaviour needs new code in the cog.
- **Card battle** is low-variance and coordination-driven: narrow damage spread, visible enemy intent and phase ordering reward planning the round as a team, and the constraints (non-lethal, noise, clocks) make *how* the team wins part of the score. New content is mostly CSV rows, and balance can be checked offline with `python -m cardbattle.sim`.
