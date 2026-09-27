"""슬래시 명령 공통 `도움말` 옵션.

모든 비관제 명령은 마지막 옵션으로 ``도움말: bool = False`` 를 받고, 콜백 첫 줄에서
``if await help_option.maybe_help(interaction, 도움말, extra): return`` 로 빠져나간다.
도움말 본문은 discord.py 가 이미 알고 있는 명령 설명과 옵션 설명(``@app_commands.describe``)
에서 자동으로 만들고, ``extra`` 에 계열 규칙 요약을 덧붙인다.

필수였다가 선택으로 바뀐 옵션은 ``require()`` 로 검사한다.
"""
from __future__ import annotations

from typing import Any, Optional

import discord
from discord import app_commands

HELP_OPTION_NAME = "도움말"
HELP_OPTION_DESC = "이 명령의 사용법만 보고 끝냅니다 (다른 옵션은 무시)."


def _choices_text(param: app_commands.Parameter) -> str:
    names = [c.name for c in (param.choices or [])]
    return f" (선택: {' / '.join(names)})" if names else ""


def render_help(command: Any, extra: Optional[str] = None) -> str:
    """명령 객체(`interaction.command`)로 도움말 본문을 만든다."""
    name = getattr(command, "qualified_name", None) or getattr(command, "name", "?")
    desc = getattr(command, "description", "") or ""
    lines = [f"## /{name}", desc.strip()]
    params = [p for p in (getattr(command, "parameters", None) or []) if p.name != HELP_OPTION_NAME]
    lines.append("**옵션**")
    if not params:
        lines.append("• 없음")
    for p in params:
        tag = "필수" if p.required else "선택"
        pdesc = (p.description or "").strip()
        if pdesc in ("", "…"):
            pdesc = "설명 없음"
        lines.append(f"• `{p.display_name}` [{tag}] — {pdesc}{_choices_text(p)}")
    lines.append(f"• `{HELP_OPTION_NAME}` [선택] — {HELP_OPTION_DESC}")
    if extra:
        lines.append("")
        lines.append(extra.strip())
    text = "\n".join(lines)
    return text if len(text) <= 2000 else text[:1997] + "…"


async def _send(interaction: discord.Interaction, content: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(content, ephemeral=True)
    else:
        await interaction.response.send_message(content, ephemeral=True)


async def maybe_help(interaction: discord.Interaction, 도움말: bool, extra: Optional[str] = None) -> bool:
    """도움말 옵션이 켜져 있으면 ephemeral 로 도움말을 보내고 True. 호출자는 곧바로 return 한다."""
    if not 도움말:
        return False
    await _send(interaction, render_help(interaction.command, extra))
    return True


async def require(interaction: discord.Interaction, **values: Any) -> bool:
    """선택으로 바뀐 옛 필수 옵션 검사. 빠진 것이 있으면 안내를 보내고 False."""
    missing = [k for k, v in values.items() if v is None]
    if not missing:
        return True
    cmd = interaction.command
    name = getattr(cmd, "qualified_name", None) or getattr(cmd, "name", "?")
    opts = ", ".join(f"`{m}`" for m in missing)
    await _send(interaction, f"{opts} 옵션이 필요합니다. 사용법은 `/{name} {HELP_OPTION_NAME}:True`.")
    return False
