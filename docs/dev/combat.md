# Combat (`combat_commands.py`, `combat/`)

> Split out of [CLAUDE.md](../../CLAUDE.md). Keep this file up to date when the code changes.

`CombatCog` keeps all live battles in memory: `self.active_battles: Dict[channel_id, battle_state]` and `self.battle_locks: Dict[channel_id, asyncio.Lock]`. `battle_state` is a plain dict built by `combat/battle_manager.BattleManager.initialize_battle_state()`; it holds a `participants_cache` DataFrame (a per-battle copy of Combat_Status rows), turn order, combo tracking, difficulty, boss mode, environment effect, and pause flags. State is lost on restart.

Supporting modules:
- [combat/battle_manager.py](../../combat/battle_manager.py) — turn order, turn start/end, battle end, intruder join, monster scaling by player count
- [combat/boss_skill_system.py](../../combat/boss_skill_system.py) — HP-threshold boss skills read from `Boss_Skills`; `used_flag` is written back to the sheet
- [combat/status_effect_manager.py](../../combat/status_effect_manager.py) — status durations and the symbiosis mechanic
- [combat/combat_utils.py](../../combat/combat_utils.py) — HP/status mutation on `participants_cache`, crit rolls, battle-end check
- [dmw_system.py](../../dmw_system.py) / [dmw_data.py](../../dmw_data.py) — the Digital Mind Wave slot animation and figure tables
- [character_models.py](../../character_models.py) — `Character` / `Player` objects that wrap a participant row for attack/damage math
- [utils.py](../../utils.py) — damage, evasion, DMW roll formulas, `get_korean_particle()` (이/가, 을/를)
- [view.py](../../view.py) — every `discord.ui.View` (turn start, action select, target select, materia, channel select)

Status effects are stored on the row as `"name:duration:value"` strings. Boss skills fire automatically when HP crosses their threshold.

Full combat rules: [combat/combat_rule.md](../../combat/combat_rule.md). Comparison with the card battle: [COMBAT_VS_CARDBATTLE.md](../../COMBAT_VS_CARDBATTLE.md).
