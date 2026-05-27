# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running the Bot

```bash
python main.py
```

**Required environment variables** (`.env` file):
- `PHS_TOKEN` — Discord bot token
- Google Sheets name is hardcoded as `"Discord_Bot"` in `main.py`

**Required credential file:** `dogwood-method-448216-f4-4023cd31106c.json` (Google Service Account)

**Key dependencies:** `discord.py`, `python-dotenv`, `gspread`, `oauth2client`, `pandas`, `numpy`

## Architecture Overview

This is a **Final Fantasy VII-themed Discord RPG bot** built on `discord.py` with Google Sheets as the primary data store.

### Entry Point & Bot Class

[main.py](main.py) instantiates a `BattleManager(commands.Bot)` that:
1. Creates a `SheetsHandler` for Google Sheets access
2. Loads 5 Cogs: `character_commands`, `combat_commands`, `utility_commands`, `shop_commands`, `archive`
3. Syncs all slash commands globally on startup

### Data Layer — Google Sheets

[google_sheets_handler.py](google_sheets_handler.py) wraps `gspread` and maintains **in-memory DataFrames** as a caching layer over Google Sheets. All Cogs receive a reference to the bot's `sheet_handler` and interact with these cached DataFrames. The key sheets are:

| Sheet | Index Key | Purpose |
|---|---|---|
| Characters | `discord_id` | Player stats & state |
| Monsters | `monster_id` | Monster definitions |
| Combat_Status | `id` | Active battle state |
| Materia_List | `materia_name` | Spell definitions |
| Boss_Skills | `(boss_id, skill_id)` | Boss ability triggers |
| Shop / UserPurchases | — | Marketplace data |

### Command Cogs

| File | Purpose |
|---|---|
| [character_commands.py](character_commands.py) | Player registration, stat management, materia equip/unequip |
| [combat_commands.py](combat_commands.py) | Battle initiation, turn execution, DMW system |
| [utility_commands.py](utility_commands.py) | Dancing & fishing mini-games, forbidden spell system |
| [shop_commands.py](shop_commands.py) | Item marketplace, shop points currency |
| [archive.py](archive.py) | Data persistence, battle history, statistics |

### Combat Subsystem (`combat/`)

The combat system is split across several modules:

- [combat/battle_manager.py](combat/battle_manager.py) — Turn order, battle flow orchestration
- [combat/boss_skill_system.py](combat/boss_skill_system.py) — HP-threshold boss skill triggers
- [combat/status_effect_manager.py](combat/status_effect_manager.py) — Status ailment lifecycle
- [combat/combat_utils.py](combat/combat_utils.py) — Damage calculation helpers
- [combat/random_utils.py](combat/random_utils.py) — Dice roll abstractions

### Character Models

[character_models.py](character_models.py) defines:
- `Character` (base) — name, job, HP, stats (`physics`, `magic`, `agility`, `charisma`), combat flags
- `Player(Character)` — adds `discord_id`, MP, `shop_points`, job-specific limit break logic

**Jobs:** Striker (physical DPS), Materia Weaver (magic/support), Soldier (NPC balanced), Turks (NPC speed/utility)

### Discord UI

[view.py](view.py) contains all `discord.ui.View` subclasses used for interactive battle buttons (turn start, action selection, target selection, materia management, etc.).

### Constants & Game Balance

[constants.py](constants.py) holds all numeric game balance values — difficulty multipliers, combo bonuses, DMW thresholds, limit break costs, status effect name mappings, and UI timeout (600s).

Full combat rule documentation is in [combat/combat_rule.md](combat/combat_rule.md).

## Key Design Patterns

- **Cog pattern**: Each feature area is a `commands.Cog`. Cogs access shared state via `self.bot.sheet_handler`.
- **Cache-first reads**: Always read from the in-memory DataFrame cache; writes go to both cache and Google Sheets.
- **Status format**: Character status effects are stored as `"status_name:duration_turns:value"` strings in the Sheets row.
- **Boss skills trigger on HP thresholds**: When a boss's HP crosses a defined percentage, `boss_skill_system.py` fires the corresponding ability automatically.
- **DMW (Digital Mind Wave)**: Luck-based system triggered during combat. Success threshold is 35 (normal) or 18 (boss mode). Roll = `round((charisma-10)/2) + 4d10`.

## Known Artifacts

- `combat/편집충돌_로컬___init__.py` and `combat/편집충돌_서버___init__.py` — unresolved merge conflict files, safe to ignore or delete.
- `combat/test.py` — minimal test stub, not part of the active system.
