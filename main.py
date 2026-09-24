import discord
from discord import app_commands
from discord.ext import commands
import os
from dotenv import load_dotenv
import logging

from google_sheets_handler import SheetsHandler


# 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s:%(levelname)s:%(name)s: %(message)s')

load_dotenv()
DISCORD_TOKEN = os.getenv("MOG_TOKEN")
GSPREAD_SHEET_NAME = os.getenv("GSPREAD_SHEET_NAME") or "Discord_Bot"
# 예전 버전은 이 길드에 전역 명령의 사본(copy_global_to)을 등록했다. 지금은 사본을 만들지 않고,
# 시작 시 이 길드의 등록 목록을 트리와 맞춰(=사본 삭제) "목록에는 보이는데 CommandNotFound" 상태를 푼다.
GUILD_ID = os.getenv("MOG_GUILD_ID")
# 접속 후 추가로 길드 범위를 맞출 길드. 비우면 위의 명시된 길드(관제 서버, MOG_GUILD_ID)만 건드린다.
#   MOG_SWEEP_GUILD_IDS=all            → 봇이 들어가 있는 모든 길드 (예전 사본을 전부 청소)
#   MOG_SWEEP_GUILD_IDS=123,456        → 이 길드들만
SWEEP_GUILD_IDS = os.getenv("MOG_SWEEP_GUILD_IDS", "").strip()

# 검색 엔진(mogindex)과 침입자 추적(fugitive)은 둘 다 로드한다. 하나라도 빠지면 그 Cog 의 명령어는
# 디스코드 목록에서 사라지거나(동기화 후) 남아 있어도 CommandNotFound 가 난다.
COGS_TO_LOAD = [
    'character_commands', 'combat_commands', 'utility_commands', 'shop_commands', 'archive',
    'mogindex_commands', 'fugitive.cog',
]

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.guilds = True
intents.reactions = True  # 반응 이벤트 처리에 필요


class CommandTree(app_commands.CommandTree):
    """봇 트리에 없는 명령(예전 동기화가 남긴 잔재)을 눌렀을 때 사용자에게 안내한다."""

    async def on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CommandNotFound):
            logging.warning("등록되지 않은 명령어 호출: /%s (guild=%s) — 봇 재시작 시 목록이 정리됩니다",
                            error.name, interaction.guild_id)
            try:
                await interaction.response.send_message(
                    f"`/{error.name}` 은(는) 지금 봇에 등록되어 있지 않은 명령어입니다. "
                    "봇이 재시작되면 목록이 정리되니 잠시 후 다시 시도해 주세요.",
                    ephemeral=True,
                )
            except discord.HTTPException:
                pass
            return
        await super().on_error(interaction, error)


class BattleManager(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents, tree_cls=CommandTree)
        self.sheet_handler = SheetsHandler(GSPREAD_SHEET_NAME)
        self.failed_cogs: list[str] = []
        self._guild_sweep_done = False

    async def setup_hook(self):
        """봇이 시작될 때 Cogs를 로드하고 슬래시 커맨드를 동기화함"""
        for cog_name in COGS_TO_LOAD:
            try:
                await self.load_extension(cog_name)
                logging.info(f"Cog '{cog_name}' 로드 성공")
            except Exception:
                self.failed_cogs.append(cog_name)
                logging.exception(f"Cog '{cog_name}' 로드 실패")
        if self.failed_cogs:
            logging.error("로드 실패한 Cog: %s — 이 Cog 의 슬래시 명령어는 등록되지 않습니다", self.failed_cogs)
        logging.info("슬래시 명령어 로컬 등록 목록: %s", sorted(c.name for c in self.tree.get_commands()))
        await self.sync_commands()

    def _explicit_guild_ids(self) -> list[int]:
        """.env 로 지정된 길드: 관제 서버 + (예전 사본이 남았을 수 있는) MOG_GUILD_ID."""
        ids: list[int] = []
        for raw in (os.getenv("FUGITIVE_CONTROL_GUILD_ID"), GUILD_ID):
            raw = (raw or "").strip()
            if raw.isdigit() and int(raw) not in ids:
                ids.append(int(raw))
        return ids

    async def sync_commands(self, extra_guild_ids: list[int] | None = None) -> dict:
        """전역 명령어를 동기화하고, 길드 범위는 트리에 있는 것만 남긴다.

        tree.sync(guild=g) 는 그 길드에 대해 트리가 아는 길드 전용 명령어 목록 전체를 올린다.
        관제 서버는 /추적기·/도주 가 올라가고, 다른 길드는 빈 목록이 올라가 예전 사본이 지워진다.
        """
        result = {"global": None, "guilds": {}}
        try:
            synced = await self.tree.sync()
            result["global"] = sorted(c.name for c in synced)
            logging.info("✅ 전역 슬래시 명령어 동기화 성공 commands=%s", result["global"])
        except Exception:
            logging.exception("전역 슬래시 명령어 동기화 실패")
        guild_ids = self._explicit_guild_ids()
        for gid in extra_guild_ids or []:
            if gid not in guild_ids:
                guild_ids.append(gid)
        for gid in guild_ids:
            try:
                synced = await self.tree.sync(guild=discord.Object(id=gid))
                names = sorted(c.name for c in synced)
                result["guilds"][gid] = names
                logging.info("✅ 길드 명령어 동기화 guild=%s commands=%s", gid, names)
            except Exception:
                logging.exception("길드 명령어 동기화 실패 guild=%s", gid)
        return result

    async def on_ready(self):
        """봇이 연결될 때 호출"""
        logging.info(f'{self.user}(ID: {self.user.id})봇 연결 성공')
        if self._guild_sweep_done:
            return
        self._guild_sweep_done = True
        # setup_hook 시점에는 길드 목록을 모른다. MOG_SWEEP_GUILD_IDS 로 지정한 길드에 대해 길드 범위를 한 번 맞춰
        # 예전 사본을 청소한다 (트리에 길드 명령이 없는 길드는 빈 목록이 올라간다). 비어 있으면 아무 길드도 건드리지 않는다.
        explicit = set(self._explicit_guild_ids())
        if not SWEEP_GUILD_IDS:
            others = [f"{g.id}({g.name})" for g in self.guilds if g.id not in explicit]
            if others:
                logging.info("길드 범위 정리 건너뜀 (MOG_SWEEP_GUILD_IDS 미설정): %s", ", ".join(others))
            return
        if SWEEP_GUILD_IDS.lower() == "all":
            targets = [g for g in self.guilds if g.id not in explicit]
        else:
            wanted = {int(x) for x in SWEEP_GUILD_IDS.replace(" ", "").split(",") if x.isdigit()}
            targets = [g for g in self.guilds if g.id in wanted and g.id not in explicit]
        for guild in targets:
            try:
                synced = await self.tree.sync(guild=guild)
                logging.info("길드 범위 정리 guild=%s(%s) commands=%s", guild.id, guild.name, sorted(c.name for c in synced))
            except Exception:
                logging.exception("길드 범위 정리 실패 guild=%s", guild.id)


bot = BattleManager()


@bot.command(name="동기화")
@commands.is_owner()
async def resync(ctx: commands.Context):
    """(봇 소유자) 재시작 없이 슬래시 명령어를 다시 동기화한다. 현재 길드의 잔재도 함께 정리한다."""
    extra = [ctx.guild.id] if ctx.guild else []
    result = await bot.sync_commands(extra_guild_ids=extra)
    lines = [f"전역: {', '.join(result['global']) if result['global'] else '실패'}"]
    for gid, names in result["guilds"].items():
        lines.append(f"길드 {gid}: {', '.join(names) if names else '(길드 전용 없음 — 잔재 삭제됨)'}")
    if bot.failed_cogs:
        lines.append(f"⚠ 로드 실패한 Cog: {', '.join(bot.failed_cogs)} (로그 확인)")
    await ctx.reply("\n".join(lines))


if __name__ == "__main__":
    if not DISCORD_TOKEN or not GSPREAD_SHEET_NAME:
        logging.critical("토큰 또는 스프레드 시트 환경 변수가 설정되어 있지 않습니다.")
    else:
        bot.run(DISCORD_TOKEN)
