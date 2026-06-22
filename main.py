import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import logging

from google_sheets_handler import SheetsHandler


# 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s:%(levelname)s:%(name)s: %(message)s')

load_dotenv()
DISCORD_TOKEN = os.getenv("MOG_TOKEN")
GSPREAD_SHEET_NAME = os.getenv("GSPREAD_SHEET_NAME")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.guilds = True
intents.reactions = True  # 반응 이벤트 처리에 필요

class BattleManager(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.sheet_handler = SheetsHandler(GSPREAD_SHEET_NAME)

    async def setup_hook(self):
        """봇이 시작될 때 Cogs를 로드하고 슬래시 커맨드를 동기화함"""
        # cogs 폴더 내의 모든 .py 파일을 Cog로 로드
        cogs_to_load = ['character_commands', 'combat_commands', 'utility_commands', 'shop_commands', 'archive', 'mogindex_commands']
        for cog_name in cogs_to_load:
            try:
                await self.load_extension(cog_name)
                logging.info(f"Cog '{cog_name}' 로드 성공")
            except Exception as e:
                logging.error(f"Cog '{cog_name}' 로드 실패: {e}")
        # 슬래시 명령어 동기화
        try:
            await self.tree.sync()
            logging.info("✅ 슬래시 명령어 동기화 성공")
        except Exception as e:
            logging.error(f"슬래시 명령어 동기화 실패: {e}")

    async def on_ready(self):
        """봇이 연결될 때 호출"""
        logging.info(f'{self.user}(ID: {self.user.id})봇 연결 성공')

bot = BattleManager()

if __name__ == "__main__":
    if not DISCORD_TOKEN or not GSPREAD_SHEET_NAME:
        logging.critical("토큰 또는 스프레드 시트 환경 변수가 설정되어 있지 않습니다.")
    else:
        bot.run(DISCORD_TOKEN)
