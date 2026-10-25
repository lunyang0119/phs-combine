# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Bot

```bash
python main.py
```

There is no linter or CI. Install dependencies with `pip install -r requirements.txt`:
`discord.py` (2.x), `python-dotenv`, `gspread`, `oauth2client`, `pandas`, `numpy`, `pytz`, `google-genai` (for `summary.py` and the fugitive debug auto-play; needs `httpx` ≥ 0.24 on Python 3.13), and optionally `Pillow` (fugitive image renderer). Python 3.11+.

```bash
python -m pytest tests -q                 # fugitive engine + cog tests (the only tests)
python -m pytest tests -q -k doorlock     # one test
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests -q   # if a globally installed pytest plugin breaks collection
python -m fugitive.sim --all --games 400  # Monte Carlo pacing check, or --hunters 4 --set key=value
```

**Environment (`.env`, see `.env.example`)**
- `MOG_TOKEN` — Discord bot token (required; `local_server/main.py` reads `TEST_TOKEN` instead)
- `GSPREAD_SHEET_NAME` — spreadsheet name (defaults to `Discord_Bot`)
- `MOG_GUILD_ID` — optional. Older builds registered guild-scoped copies of every command here; the current `main.py` no longer copies, it only re-syncs that guild so the stale copies get removed
- `MOG_SWEEP_GUILD_IDS` — optional. After connecting, `on_ready` also pushes the tree's guild-scoped list to these guilds (`all` = every guild the bot is in, or a comma-separated id list) to clear stale copies. Unset = only the control guild and `MOG_GUILD_ID` are touched; other guilds are listed in the log as skipped
- `GEMINI_API_KEY` — used by `summary.py` and by `/추적기 개설 디버그:True` (Gemini auto-hunters); `FUGITIVE_GEMINI_MODEL` overrides the model (default `gemini-3.8-flash`); on 503/429 the bot retries with backoff (`FUGITIVE_GEMINI_RETRIES`, `FUGITIVE_GEMINI_RETRY_BASE_SEC`) then falls back through `FUGITIVE_GEMINI_FALLBACK_MODELS` (default `gemini-2.5-flash,gemini-2.0-flash`)
- `FUGITIVE_CONTROL_GUILD_ID`, `FUGITIVE_ADMIN_IDS` — control server and admin allowlist for the fugitive minigame; without them its admin commands are not registered
- `FUGITIVE_FONT_PATH` — optional CJK font for the fugitive PNG renderer
- `CARDBATTLE_SOURCE` (`csv`, default, or `sheet`), `CARDBATTLE_DATA_DIR` (default `data/cardbattle`), `CARDBATTLE_ADMIN_IDS` (falls back to `FUGITIVE_ADMIN_IDS`; may force-resolve or end any card battle) — card-battle minigame data source and override admins. With `sheet`, tabs `Card_List` / `Card_Enemies` / `Card_Battles` (same columns as the CSVs) are read, falling back per table to the CSVs with a warning
- `MOGINDEX_READ_CACHE_KIB` (default 65536), `MOGINDEX_READ_MMAP_MIB` (default 256) — SQLite page cache / mmap for the search read connection. `MogIndexService.index_open()` keeps one read-only (`query_only`) connection per thread and reopens it if the DB file's inode changes or a query raises; anything that writes to the index DB must use `index_connect()` instead. `mogindex_backfill.py` runs `ANALYZE` before `VACUUM INTO` so the deployed file carries planner stats, and each batch run ends with `PRAGMA optimize`.
- `MOGINDEX_RETURNEE_MAX_DAYS` (default 180), `MOGINDEX_RETURNEE_CACHE_TTL_SEC` (default 600) — 복귀자 키워드 aggregates `daily_terms` over the period since the user's last worldmap message (cost grows with the period: measured 1.3 s for the full history, 0.03 s for 90 days), so users with no activity or a longer absence are capped to the last N days (the header says so), and the top-500 term list is cached per (period, scope) so paging, exclusions (`add_not_terms`, applied in Python) and the pick menu never re-aggregate. Keyword search with all three fields empty, or the `둘러보기` button, switches to the `recent` browse mode; the returnee page carries a `Select` (`TextPage.options`) whose pick runs a keyword search restricted to the period the list was rendered with (`SearchPanelState.returnee_start/returnee_end`, `apply_action("returnee_pick")`); with more than 25 terms the menu shows 24 at a time and the 25th option (`TextPage.pick_more`, value `RETURNEE_PICK_MORE`) advances `returnee_pick_page` to the next chunk, wrapping to the first. The term cache key includes the index DB file identity, so a replaced deploy file misses the cache immediately.

**Credential file:** `dogwood-method-448216-f4-4023cd31106c.json` (Google service account) must sit in the repo root; `SheetsHandler.__init__` raises if it is missing. Both it and `.env` are gitignored.

**Google Sheet:** the spreadsheet name comes from `GSPREAD_SHEET_NAME` (default `"Discord_Bot"`) in `main.py`. Boot fails unless every worksheet below exists (`Boss_Answer` is optional). The `Project_T - *.csv` files in the root are exports of the sheet tabs, useful as schema references.

## Architecture Overview

A Final Fantasy VII–themed Discord RPG bot on `discord.py`, with Google Sheets as the persistent store and pandas DataFrames as the in-memory cache. All slash-command names, user-facing strings, log messages, and most code comments are Korean.

### Entry point

[main.py](main.py) defines `BattleManager(commands.Bot)`. `setup_hook` creates one `SheetsHandler`, loads the Cogs in `COGS_TO_LOAD` (`character_commands`, `combat_commands`, `utility_commands`, `shop_commands`, `archive`, `mogindex_commands`, `cardbattle.cog`; all but the last live in the repo root, not a `cogs/` folder). `fugitive.cog` is **commented out** of that list in both `main.py` and `local_server/main.py`: the 침입자 추적 commands are disabled while the package, its tests and `python -m fugitive.sim` stay intact. Re-enable by uncommenting and running `!동기화` (the control guild is still synced on boot, so the stale `/추적기`·`/도주` copies are removed while it is off), then runs `sync_commands()`: a global `tree.sync()` plus `tree.sync(guild=...)` for `FUGITIVE_CONTROL_GUILD_ID` and `MOG_GUILD_ID`. `on_ready` additionally syncs the guilds named by `MOG_SWEEP_GUILD_IDS` once (see Environment), so a guild that only has stale copies gets an empty list pushed and the copies disappear. A command that Discord still lists but the tree does not know raises `CommandNotFound`; the custom `CommandTree.on_error` answers the user ephemerally instead of only logging. The owner-only prefix command `!동기화` re-runs the sync without a restart and reports Cogs that failed to load (`bot.failed_cogs`). Never call `copy_global_to` again: it is what left the orphaned guild copies behind.

`summary.py` (Gemini-backed chat summariser, `SummaryCommandsCog`) is a complete Cog that is **not** in the load list; add it to `cogs_to_load` to enable it.

Note the name collision: `main.BattleManager` is the bot, while `combat/battle_manager.BattleManager` is a class of static helpers for battle flow.

### Data layer — `google_sheets_handler.py`

`SheetsHandler` opens every worksheet at construction and exposes them two ways:

| Worksheet | Attribute | Cached DataFrame (index) |
|---|---|---|
| Characters | `characters_sheet` | `characters_sheet_cache` (`discord_id`) |
| Monsters | `monsters_sheet` | `monsters_sheet_cache` (`monster_id`) |
| Combat_Status | `battle_status_sheet` | `battle_stat_sheet_cache` (`id`) |
| Materia_List | `materia_list_sheet` | `materia_list_sheet_cache` (`materia_name`) |
| Boss_Skills | `boss_skills_sheet` | `boss_skills_sheet_cache` (MultiIndex `boss_id, skill_id`, sorted) |
| Musics | `ost_sheet` | `ost_sheet_cache` (`music_no`) |
| Boss_Answer (optional) | `boss_answer_sheet` | `boss_answer_sheet_cache` (`keyword`) |
| ShopData, UserPurchases, BookLog, FishingLog, ForbiddenLog, ServerChannel, DancingLog | `*_sheet` | not cached — read with `get_all_records()` on demand |

Conventions that matter:
- Index keys are always strings (`_sheet_to_dataframe` casts them). Look up with `str(discord_id)`.
- Reads go to the cache. Writes update the cache first, then push whole sheets back with `clear()` + `update()` (see `update_battle_status_cache_to_sheet`, `ShopCog._sync_characters_to_sheet`). Combat syncs `Combat_Status` every `constants.SYNC_INTERVAL` turns, not every action.
- `gspread` calls block; wrap them in `asyncio.to_thread(...)` inside Cogs.
- `/시트갱신` (`character_commands.py`) forces either direction of the sync.

### Cogs

Each Cog receives `bot` and reads `bot.sheet_handler`. Slash commands are declared with `@app_commands.command(name="한글이름", ...)`; admin-only ones use `@app_commands.default_permissions(...)`. There is no user-ID allowlist anywhere yet.

**Help convention:** there are no standalone help commands. Every non-admin command (not `/추적기 *`, `/도주 *`, or `[관리자]`/`[개발자]` ones) takes a trailing `도움말: bool = False` option and starts with `if await help_option.maybe_help(interaction, 도움말, extra): return`, which answers ephemerally with help auto-rendered from the command's description and `@app_commands.describe` texts (`help_option.render_help`) plus a family summary from `help_texts.py` (or `fugitive/strings.py` `HELP`). Because Discord cannot send `도움말:True` alone when other options are required, none of these commands has a required option; formerly required ones are `Optional[...] = None` and checked with `help_option.require(interaction, name=value)` right after the help guard. `tests/test_help_option.py` enforces both rules, so a new public command must follow the pattern.

| File | Purpose |
|---|---|
| [character_commands.py](character_commands.py) | Registration, stats, materia equip/unequip, cache inspection, sheet sync |
| [combat_commands.py](combat_commands.py) | Battle start/loop/end, actions, DMW, environment effects (3.6k lines; the heart of the bot) |
| [utility_commands.py](utility_commands.py) | Dice, roleplay action rolls, fishing/dance/book mini-games, ServerChannel registration, out-of-combat DMW |
| [shop_commands.py](shop_commands.py) | Shop items and shop-point currency |
| [fugitive/cog.py](fugitive/cog.py) | 침입자 추적 hidden-movement minigame (see below) |
| [cardbattle/cog.py](cardbattle/cog.py) | 카드 전투 minigame: `/카드 옵션:주최|참가|시작|턴종료|종료|현황` (see below) |
| [mogindex_commands.py](mogindex_commands.py) | 검색 engine: `/검색 [검색어]` (ephemeral panel, `SearchPanelView` rows = mode / period / scope+refine / paging+share / reset+close; active state is green, action buttons blue), `/고오급검색`, raw-message capture into SQLite and the Kiwi batch/backfill pipeline (see [DEPLOY.md](DEPLOY.md)); helpers in `mogindex_service.py` (`make_snippet` + `attach_snippets` fill `SearchResult.snippet` for the page's rows only), `mogindex_debug.py`, `mogindex_discord_debug.py`. UI tests: `tests/test_search_panel_ui.py` |
| [archive.py](archive.py) | Dump a channel's chat for a date range to a txt file |
| [summary.py](summary.py) | Same, plus Gemini summarisation (not loaded by default) |

### Combat state

`CombatCog` keeps all live battles in memory: `self.active_battles: Dict[channel_id, battle_state]` and `self.battle_locks: Dict[channel_id, asyncio.Lock]`. `battle_state` is a plain dict built by `combat/battle_manager.BattleManager.initialize_battle_state()`; it holds a `participants_cache` DataFrame (a per-battle copy of Combat_Status rows), turn order, combo tracking, difficulty, boss mode, environment effect, and pause flags. State is lost on restart.

Supporting modules:
- [combat/battle_manager.py](combat/battle_manager.py) — turn order, turn start/end, battle end, intruder join, monster scaling by player count
- [combat/boss_skill_system.py](combat/boss_skill_system.py) — HP-threshold boss skills read from `Boss_Skills`; `used_flag` is written back to the sheet
- [combat/status_effect_manager.py](combat/status_effect_manager.py) — status durations and the symbiosis mechanic
- [combat/combat_utils.py](combat/combat_utils.py) — HP/status mutation on `participants_cache`, crit rolls, battle-end check
- [dmw_system.py](dmw_system.py) / [dmw_data.py](dmw_data.py) — the Digital Mind Wave slot animation and figure tables
- [character_models.py](character_models.py) — `Character` / `Player` objects that wrap a participant row for attack/damage math
- [utils.py](utils.py) — damage, evasion, DMW roll formulas, `get_korean_particle()` (이/가, 을/를)
- [view.py](view.py) — every `discord.ui.View` (turn start, action select, target select, materia, channel select)

Status effects are stored on the row as `"name:duration:value"` strings. Boss skills fire automatically when HP crosses their threshold.

### Randomness

Never `import random`. Use `from combat import random_utils` (`randint`, `choice`, `roll_dice`, `get_random`, …). It is a seedable singleton (`set_seed`, `set_debug_mode`) so battles can be replayed; the commented-out `# import random` lines across the codebase are deliberate.

### Constants

[constants.py](constants.py) holds every balance number (difficulty and player-count multipliers, combo bonuses, DMW thresholds 35/18, limit-break costs, status name maps, UI delays, view timeouts). Add new magic numbers there. Full combat rules are in [combat/combat_rule.md](combat/combat_rule.md).

### Fugitive minigame (`fugitive/`)

Design and rules: [docs/fugitive_game_design.md](docs/fugitive_game_design.md). Read it before touching anything named 추적기/도주.

- `engine.py` is pure Python (no Discord): `GameMap`, `GameState`, `submit_*`, `resolve_round()`, `public_view()`. Everything the public channel sees comes from `PublicView`, which structurally has no fugitive fields while a game is running. The one exception is `capture_room`, which `public_view` fills only when the status is `CAPTURED` (the final table paints that room green / marks it ★). Keep it that way.
- `config.py` holds `DEFAULTS`, per-hunter-count `SCALING`, the built-in maps and the loader for the optional `Fugitive_Map` / `Fugitive_Config` / `Fugitive_Scaling` sheets. Sheets are read once at `/추적기 시작` and never written.
- Hidden state lives only in memory and `data/fugitive_state.json` (gitignored). `store.py` writes it atomically after every accepted order.
- `cog.py` registers public hunter commands globally and the `/추적기` and `/도주` groups only on the control guild, guarded by the `FUGITIVE_ADMIN_IDS` allowlist. `views.py` is a persistent `RoundView` (fixed `custom_id`s) so buttons survive restarts.
- The capture message is fixed text in `strings.py`; the bot posts nothing after it. All player-facing strings live in `strings.py` and never describe the intruder.
- `bot_player.py` is the debug auto-play: `/추적기 개설 디버그:True 인원:4` fills the lobby with `bot:N` hunters, and each time a round opens the cog asks Gemini for every bot's order (built only from `PublicView`, the round message and the bot's own position, so the intruder never leaks into the prompt). Bots act one at a time: "판단 중" notice → Gemini call → the chosen order and its reason are posted to the control channel → the order is submitted (a rule-rejected choice becomes 대기 with a follow-up notice). Reasons are also kept in `GameState.bot_reasons` to feed the next prompt. `/추적기 디버그 재요청` re-asks for unsubmitted bots. In a normal lobby, `/추적기 ai추가 목표인원:N` tops the roster up with `bot:N` hunters so humans and Gemini play together (it sets the same `debug_bots` switch; bot reasons still go only to the control channel, the public channel sees just their orders like anyone else's). `/추적기 개설 도주자ai:True` instead lets Gemini play the fugitive (prompt built from the true state plus what hunters see; decision posted to the control channel, then submitted; a manual `/도주` order submitted first wins). Both flags can be combined for a fully unattended game.
- Image tracker colours: `hunter_colors` (comma-separated hex, in join order, cycles) is an ordinary tunable, so it can be set in the `Fugitive_Config` sheet, in the lobby or in-game with `/추적기 설정`. A per-character fixed colour wins over the palette: add a `color` column (`#RRGGBB`) to the `Characters` sheet; the cog copies it onto `Hunter.color` at `/추적기 시작` (`_apply_char_colors`, via `SheetsHandler.get_char_data`, so `/시트갱신` first if the sheet was edited). `PublicView.hunters[*]["color"]` carries the final value to `image_render.py`. It is for UI/flow testing only; balance still comes from `fugitive.sim`.
- The cog's round timer resolves rounds inside its own task, so `_cancel_timer` must never cancel `asyncio.current_task()` (that bug silently swallowed reports and the capture line). `tests/test_cog_timer.py` drives the real timer path with a fake channel.

### Card battle minigame (`cardbattle/`)

Player/host/data/developer manual: [docs/cardbattle_guide.md](docs/cardbattle_guide.md). Same shape as the fugitive package: `engine.py` is pure Python (no Discord), `cog.py` is the thin Discord layer, `store.py` keeps `data/cardbattle_state.json`, `sim.py` is the balance check (`python -m cardbattle.sim --all --games 200`, or `--battle scav4 --players 4 --set ncpd_max=4`). The shipped data is a Cyberpunk 2077 TRPG session: two scripted fights, 스캐빈저 (`scav3`…`scav7`, one id per headcount) and 고장난 돌 (`doll3`…`doll7`), plus a gimmick-free practice `b01`. Sheet columns keep the FF7 names; only the display strings are Cyberpunk (`strings.STAT_KO` 마력→RAM, `JOB_KO` 스트라이커→엣지러너 / 마테리아 위버→넷러너).

- One session at a time (`CardBattleCog.state`). `/카드 옵션:주최` replies ephemerally and makes the caller the session's host, who picks from **all** cards instead of a random hand (`admin_sees_all`). `옵션:참가` edits the single lobby message; player stats come from the Characters sheet via `get_char_data` (mapping lives only in `engine.player_combatant`, which also reads `job` and `color`), the displayed name is the Discord server nickname. `옵션:시작 전투id:X` loads `data/cardbattle/{Cards,Enemies,Battles}.csv` through `data.make_source` (or sheet tabs, see Environment), applies the battle's `overrides` column on top of the session config (`enemy_hp_mult`, `ncpd_max`, `chip_stability` … — this is how headcount is balanced), and snapshots the cards into the state.
- **Rounds, not half-turns.** `open_round` makes every enemy choose first (`enemy_choices`) and publishes only the card **kind** in `intents` (`public_intents` adds the target only with `show_intent_target` or the round's `scan` flag). Players then choose; `resolve_round` resolves **both sides together**: phase (hack 0 → defend 1 → attack/special 2) → agility desc → random, so a fast hacker's stun cancels a slower enemy's attack in the same round. `shield`/`atk_down` tick at the end of every round including the creation round (`shield:0.5:1` = this round only); `misfire`/`redirect_random`/`stun` are consumed on use and never tick; `grapple` is removed only by 떨쳐내기, an ally hit on the doll, or the firewall reboot. Unchosen players "대기". There is no timer.
- The public round message carries two persistent buttons (`cardbattle:draw`, `cardbattle:firewall`) and, when Pillow + a CJK font are present (`render_mode=image`, checked by `cog._image_mode()`), the `image_render.render_board` PNG as the embed image (`attachment://board.png`; editing the embed alone keeps the attachment). `image_render` reuses `fugitive.image_render`'s font lookup and colour parsing, so the fugitive package must stay importable even while its cog is off. Ally panel colours come from the Characters `color` column, enemy colours from `Enemies.csv`.
- **Gimmicks** (`Battles.csv` `gimmicks`, constants in `engine.GIMMICKS`): `nonlethal` (downing an enemy with `dmg - hp >= overkill_margin`, or with a `lethal`-tagged card, is 과잉 제압; `nonlethal`-tagged cards are exempt), `ncpd` (과잉 제압 +1, ally `loud` cards +0.5; at `ncpd_max` the fight ends and `start_escape` begins the 5-turn rock-paper-scissors: `ESCAPE_BEATS`/`ESCAPE_LOSES`, police action pre-rolled per turn with one excluded action published as a hint when `escape_hint`, a turn is won when `wins*2 > n` living players, alert starts at `escape_wins_needed` and hits 0 = `ESCAPED`, out of turns = `CAUGHT`), `drag` (a downed player is dragged by a random living enemy; after `drag_turns` rounds `Combatant.looted` is set unless the dragger is downed or the player revived), `doll` (enemy with trait `doll`: acts by mode from `doll_modes` instead of the AI, never stunned (stun → extra chip damage), `meters["chip"]` drops `chip_per_hack` per ally hack hit capped by `chip_round_cap` per round (×`chip_reboot_mult` while rebooting); chip 0 = ally win with `end_reason="chip"`), `terminal` (`meters["terminal_holder"]` is the doll's only target; the holder always has the `fixed`-pool card `pass_terminal`; `meters["screen"]` drops when the holder takes damage). Enemy `traits` (`cheap_chrome` hack values ×`hack_weak_mult`, `evade` vs physical single-target damage, `regen` per round) live in `Enemies.csv`.
- **보느와의 방화벽** (`meters["firewall"]`, always on): +1 per `firewall_per_damage` ally damage taken, +`firewall_per_defend` per ally defend card, +`firewall_per_down`; `use_firewall(st, uid, option)` once per round (`round_flags["firewall_used"]`), options in `engine.FIREWALL_OPTIONS` (`cover` team shield, `reboot` clears debuffs/grapple/stun, `aim` zero variance + ignores evade this round, `scan` reveals intent targets, `maxdoc` heals/revives the worst ally). Round-scoped flags live in `round_flags` and are wiped by `open_round`.
- Every public message is a `discord.Embed` built in `embeds.py` from the sentences `render.py` produces; `render.py` stays Discord-free. The round report is one embed with 🟢/🔴 per line in true resolution order, then event lines (`render.event_line`: firewall use, NCPD, drag, looted, regen). Log line format is `strings.LOG_USE` + `LOG_TARGET`: `<닉네임>님이 <카드>을(를) 사용했습니다. <대상> HP x/y (-n)`; the HP is the **target's**, the target name is omitted when it is the actor; `⚡콤보!` / `(적대 인식!)` prefixes, `⚠과잉 제압` / `💾칩 −n` / `🪢… 풀려남` suffixes. Enemy AI (`ai.py`) scores each drawn card with a personality weight table plus `ai_noise`, damage scaled by `DMG_SCALE` so attacks and hacks share one scale; no LLM.
- Cards are data: `effects` is `kind[:value[:turns[:chance]]]` joined by `;` (kinds in `engine.EFFECT_KINDS`), `tags` is `loud`/`lethal`/`nonlethal`, `combo` is `target_has|self_has:<modifier>:dmg_x|dmg_add|chip_add:<value>` and is rendered automatically into the card description (`render.combo_text`) and the log. `pool=fixed` cards are never drawn; `engine.fixed_cards` hands them out (`pass_terminal`, `break_free`, the doll's mode cards). New balance numbers go in `cardbattle/config.py` `DEFAULTS` (also the only place `Battles.csv` `overrides` keys are validated).
- `sim.py` uses the real job templates (엣지러너 HP 110 근력 16 / 넷러너 HP 80 RAM 16 / 솔져) and always prints `greedy`, `attack_only` and `random` side by side: if `attack_only` matches `greedy`, non-attack cards are traps. Battle `overrides` are applied automatically; `--set` overrides them.
- The engine takes an `rng` argument (any object with `random/randint/choice/choices`); the default adapter wraps `combat.random_utils` lazily. Note `random_utils.get_random()` itself is broken (`_rng.get_random`), so the adapter uses `uniform`.
- Tests: `tests/test_cardbattle_engine.py` (round order, modifiers, every gimmick, firewall, escape, combo), `test_cardbattle_ai.py`, `test_cardbattle_cog.py` (fake channel/interaction harness incl. firewall menu, scan, NCPD → escape buttons, restart). `/카드` must stay a single class-level command with no required options so `tests/test_help_option.py` keeps passing. Copy `cardbattle/` and `data/cardbattle/` to `local_server/` after changes.

## Known Artifacts

- `combat/편집충돌_로컬___init__.py` and `combat/편집충돌_서버___init__.py` — leftover merge-conflict copies; safe to ignore or delete.
- `combat/test.py` — three-line stub, not a test.
