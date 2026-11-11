# Data layer — `google_sheets_handler.py`

> Split out of [CLAUDE.md](../../CLAUDE.md). Keep this file up to date when the code changes.

**Credential file:** `dogwood-method-448216-f4-4023cd31106c.json` (Google service account) must sit in the repo root; `SheetsHandler.__init__` raises if it is missing. Both it and `.env` are gitignored.

**Google Sheet:** the spreadsheet name comes from `GSPREAD_SHEET_NAME` (default `"Discord_Bot"`) in `main.py`. Boot fails unless every worksheet below exists (`Boss_Answer` is optional). The `Project_T - *.csv` files in the root are exports of the sheet tabs, useful as schema references.

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
