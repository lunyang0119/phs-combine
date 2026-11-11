# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

It is an index: each mechanism has its own document under [docs/dev/](docs/dev/). **Read the linked document before touching that area, and update it (not this file) when the mechanism changes.**

## Running the Bot

```bash
python main.py
```

There is no linter or CI. Install dependencies with `pip install -r requirements.txt`:
`discord.py` (2.x), `python-dotenv`, `gspread`, `oauth2client`, `pandas`, `numpy`, `pytz`, `google-genai` (for `summary.py` and the fugitive debug auto-play; needs `httpx` ≥ 0.24 on Python 3.13), and optionally `Pillow` (fugitive / card-battle image renderer). Python 3.11+.

```bash
python -m pytest tests -q                 # all tests
python -m pytest tests -q -k doorlock     # one test
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests -q   # if a globally installed pytest plugin breaks collection
python -m fugitive.sim --all --games 400  # fugitive pacing check
python -m cardbattle.sim --all --games 200  # card-battle balance check
```

`.env` needs `MOG_TOKEN` (`local_server/main.py` reads `TEST_TOKEN`); the Google service-account JSON must sit in the repo root. Every other variable (guild sync, Gemini, fugitive, card battle, mogindex tuning) → [docs/dev/environment.md](docs/dev/environment.md).

## Where to read

| To learn about… | Read |
|---|---|
| Environment variables (`.env`) | [docs/dev/environment.md](docs/dev/environment.md) |
| `main.py`, Cog load list, slash-command sync, `!동기화`, stale guild copies | [docs/dev/entry_point_and_sync.md](docs/dev/entry_point_and_sync.md) |
| Google Sheets store, worksheets, DataFrame caches, credential file | [docs/dev/data_layer.md](docs/dev/data_layer.md) |
| The `도움말` option every public command must have | [docs/dev/help_option.md](docs/dev/help_option.md) |
| FF7 combat: battle state, supporting modules, status effects | [docs/dev/combat.md](docs/dev/combat.md), rules in [combat/combat_rule.md](combat/combat_rule.md) |
| 침입자 추적 minigame (`fugitive/`, `/추적기`, `/도주`) | [docs/dev/fugitive.md](docs/dev/fugitive.md), design in [docs/fugitive_game_design.md](docs/fugitive_game_design.md) |
| 카드 전투 minigame (`cardbattle/`, `/카드`) | [docs/dev/cardbattle.md](docs/dev/cardbattle.md), manual in [docs/cardbattle_guide.md](docs/cardbattle_guide.md) |
| 검색 engine (`mogindex_*`, `/검색`, `/고오급검색`) | [docs/dev/mogindex.md](docs/dev/mogindex.md), deploy in [DEPLOY.md](DEPLOY.md) |
| Combat vs card battle comparison | [COMBAT_VS_CARDBATTLE.md](COMBAT_VS_CARDBATTLE.md) |

## Architecture in brief

A Final Fantasy VII–themed Discord RPG bot on `discord.py`, with Google Sheets as the persistent store and pandas DataFrames as the in-memory cache. All slash-command names, user-facing strings, log messages, and most code comments are Korean.

[main.py](main.py) defines `BattleManager(commands.Bot)` (not to be confused with `combat/battle_manager.BattleManager`, a class of static battle helpers). It creates one `SheetsHandler` and loads the Cogs in `COGS_TO_LOAD`. Each Cog receives `bot` and reads `bot.sheet_handler`. Cogs live in the repo root, not a `cogs/` folder.

| File | Purpose | Loaded |
|---|---|---|
| [character_commands.py](character_commands.py) | Registration, stats, materia equip/unequip, cache inspection, sheet sync | yes |
| [combat_commands.py](combat_commands.py) | Battle start/loop/end, actions, DMW, environment effects (3.6k lines; the heart of the bot) | yes |
| [utility_commands.py](utility_commands.py) | Dice, roleplay action rolls, fishing/dance/book mini-games, ServerChannel registration, out-of-combat DMW | yes |
| [shop_commands.py](shop_commands.py) | Shop items and shop-point currency | yes |
| [archive.py](archive.py) | Dump a channel's chat for a date range to a txt file | yes |
| [mogindex_commands.py](mogindex_commands.py) | 검색 engine | yes |
| [cardbattle/cog.py](cardbattle/cog.py) | 카드 전투 minigame | yes |
| [fugitive/cog.py](fugitive/cog.py) | 침입자 추적 minigame | **no** (commented out; package, tests and sim stay intact) |
| [summary.py](summary.py) | Archive plus Gemini summarisation | **no** (not in the load list) |

## Rules that apply everywhere

- **Randomness:** never `import random`. Use `from combat import random_utils` (`randint`, `choice`, `roll_dice`, …). It is a seedable singleton (`set_seed`, `set_debug_mode`) so battles can be replayed; the commented-out `# import random` lines across the codebase are deliberate. `random_utils.get_random()` itself is broken; use `uniform`.
- **Constants:** [constants.py](constants.py) holds every combat balance number; add new magic numbers there (card battle: `cardbattle/config.py` `DEFAULTS`; fugitive: `fugitive/config.py`).
- **Sheets:** index keys are strings (`str(discord_id)`); `gspread` calls block, so wrap them in `asyncio.to_thread(...)` inside Cogs. Details → [docs/dev/data_layer.md](docs/dev/data_layer.md).
- **Slash commands:** declared with `@app_commands.command(name="한글이름", ...)`; admin-only ones use `@app_commands.default_permissions(...)`. Every public command takes `도움말: bool = False` and has no required option (`tests/test_help_option.py` enforces it) → [docs/dev/help_option.md](docs/dev/help_option.md).
- **Command sync:** never call `copy_global_to` again → [docs/dev/entry_point_and_sync.md](docs/dev/entry_point_and_sync.md).
- **Two copies:** the repo root deploys to the server and `local_server/` is the local test copy; copy changed files (e.g. `cardbattle/`, `data/cardbattle/`) there too.

## Known Artifacts

- `combat/편집충돌_로컬___init__.py` and `combat/편집충돌_서버___init__.py` — leftover merge-conflict copies; safe to ignore or delete.
- `combat/test.py` — three-line stub, not a test.
