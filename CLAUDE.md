# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Bot

```bash
python main.py
```

There is no `requirements.txt`, linter, or CI. Install dependencies by hand:
`discord.py` (2.x), `python-dotenv`, `gspread`, `oauth2client`, `pandas`, `numpy`, `pytz`, `google-genai` (for `summary.py` and the fugitive debug auto-play; needs `httpx` ≥ 0.24 on Python 3.13), and optionally `Pillow` (fugitive image renderer). Python 3.11+.

```bash
python -m pytest tests -q                 # fugitive engine + cog tests (the only tests)
python -m pytest tests -q -k doorlock     # one test
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests -q   # if a globally installed pytest plugin breaks collection
python -m fugitive.sim --all --games 400  # Monte Carlo pacing check, or --hunters 4 --set key=value
```

**Environment (`.env`, see `.env.example`)**
- `PHS_TOKEN` — Discord bot token (required)
- `GEMINI_API_KEY` — used by `summary.py` and by `/추적기 개설 디버그:True` (Gemini auto-hunters); `FUGITIVE_GEMINI_MODEL` overrides the model (default `gemini-3.8-flash`); on 503/429 the bot retries with backoff (`FUGITIVE_GEMINI_RETRIES`, `FUGITIVE_GEMINI_RETRY_BASE_SEC`) then falls back through `FUGITIVE_GEMINI_FALLBACK_MODELS` (default `gemini-2.5-flash,gemini-2.0-flash`)
- `FUGITIVE_CONTROL_GUILD_ID`, `FUGITIVE_ADMIN_IDS` — control server and admin allowlist for the fugitive minigame; without them its admin commands are not registered
- `FUGITIVE_FONT_PATH` — optional CJK font for the fugitive PNG renderer

**Credential file:** `dogwood-method-448216-f4-4023cd31106c.json` (Google service account) must sit in the repo root; `SheetsHandler.__init__` raises if it is missing. Both it and `.env` are gitignored.

**Google Sheet:** the spreadsheet name is hardcoded as `"Discord_Bot"` in `main.py`. Boot fails unless every worksheet below exists (`Boss_Answer` is optional). The `Project_T - *.csv` files in the root are exports of the sheet tabs, useful as schema references.

## Architecture Overview

A Final Fantasy VII–themed Discord RPG bot on `discord.py`, with Google Sheets as the persistent store and pandas DataFrames as the in-memory cache. All slash-command names, user-facing strings, log messages, and most code comments are Korean.

### Entry point

[main.py](main.py) defines `BattleManager(commands.Bot)`. `setup_hook` creates one `SheetsHandler`, loads the Cogs `character_commands`, `combat_commands`, `utility_commands`, `shop_commands`, `archive`, `fugitive.cog` by module name (the first five live in the repo root, not a `cogs/` folder), then calls `tree.sync()` globally and, if `FUGITIVE_CONTROL_GUILD_ID` is set, `tree.sync(guild=...)` for the control server.

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

| File | Purpose |
|---|---|
| [character_commands.py](character_commands.py) | Registration, stats, materia equip/unequip, cache inspection, sheet sync |
| [combat_commands.py](combat_commands.py) | Battle start/loop/end, actions, DMW, environment effects (3.6k lines; the heart of the bot) |
| [utility_commands.py](utility_commands.py) | Dice, roleplay action rolls, fishing/dance/book mini-games, ServerChannel registration, out-of-combat DMW |
| [shop_commands.py](shop_commands.py) | Shop items and shop-point currency |
| [fugitive/cog.py](fugitive/cog.py) | 침입자 추적 hidden-movement minigame (see below) |
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

- `engine.py` is pure Python (no Discord): `GameMap`, `GameState`, `submit_*`, `resolve_round()`, `public_view()`. Everything the public channel sees comes from `PublicView`, which structurally has no fugitive fields. Keep it that way.
- `config.py` holds `DEFAULTS`, per-hunter-count `SCALING`, the built-in maps and the loader for the optional `Fugitive_Map` / `Fugitive_Config` / `Fugitive_Scaling` sheets. Sheets are read once at `/추적기 시작` and never written.
- Hidden state lives only in memory and `data/fugitive_state.json` (gitignored). `store.py` writes it atomically after every accepted order.
- `cog.py` registers public hunter commands globally and the `/추적기` and `/도주` groups only on the control guild, guarded by the `FUGITIVE_ADMIN_IDS` allowlist. `views.py` is a persistent `RoundView` (fixed `custom_id`s) so buttons survive restarts.
- The capture message is fixed text in `strings.py`; the bot posts nothing after it. All player-facing strings live in `strings.py` and never describe the intruder.
- `bot_player.py` is the debug auto-play: `/추적기 개설 디버그:True 인원:4` fills the lobby with `bot:N` hunters, and each time a round opens the cog asks Gemini for every bot's order (built only from `PublicView`, the round message and the bot's own position, so the intruder never leaks into the prompt). Bots act one at a time: "판단 중" notice → Gemini call → the chosen order and its reason are posted to the control channel → the order is submitted (a rule-rejected choice becomes 대기 with a follow-up notice). Reasons are also kept in `GameState.bot_reasons` to feed the next prompt. `/추적기 디버그 재요청` re-asks for unsubmitted bots. It is for UI/flow testing only; balance still comes from `fugitive.sim`.
- The cog's round timer resolves rounds inside its own task, so `_cancel_timer` must never cancel `asyncio.current_task()` (that bug silently swallowed reports and the capture line). `tests/test_cog_timer.py` drives the real timer path with a fake channel.

## Known Artifacts

- `combat/편집충돌_로컬___init__.py` and `combat/편집충돌_서버___init__.py` — leftover merge-conflict copies; safe to ignore or delete.
- `combat/test.py` — three-line stub, not a test.
