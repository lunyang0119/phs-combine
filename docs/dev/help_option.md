# Help convention (`도움말` option)

> Split out of [CLAUDE.md](../../CLAUDE.md). Keep this file up to date when the code changes.

There are no standalone help commands. Every non-admin command (not `/추적기 *`, `/도주 *`, or `[관리자]`/`[개발자]` ones) takes a trailing `도움말: bool = False` option and starts with `if await help_option.maybe_help(interaction, 도움말, extra): return`, which answers ephemerally with help auto-rendered from the command's description and `@app_commands.describe` texts (`help_option.render_help`) plus a family summary from `help_texts.py` (or `fugitive/strings.py` `HELP`). Because Discord cannot send `도움말:True` alone when other options are required, none of these commands has a required option; formerly required ones are `Optional[...] = None` and checked with `help_option.require(interaction, name=value)` right after the help guard. `tests/test_help_option.py` enforces both rules, so a new public command must follow the pattern.
