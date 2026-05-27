"""
채팅 요약 명령어 Cog
지정한 채널의 특정 시간대 채팅을 AI로 요약
"""

import discord
from discord import app_commands
from discord.ext import commands
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Tuple
import os
from dotenv import load_dotenv
from google import genai

from google_sheets_handler import SheetsHandler
from view import ChannelSelectView

logger = logging.getLogger(__name__)

load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")


class SummaryCommandsCog(commands.Cog):
    """채팅 요약 명령어"""

    def __init__(self, bot, sheet_handler: SheetsHandler):
        self.bot = bot
        self.sheet_handler = sheet_handler
        
        # Gemini 클라이언트 초기화
        if GEMINI_API_KEY:
            self.genai_client = genai.Client(api_key=GEMINI_API_KEY)
        else:
            self.genai_client = None
            logger.warning("GEMINI_API_KEY가 설정되지 않았습니다.")

        logger.info("SummaryCommandsCog 초기화 완료")

    # ==================== 요약 디버깅 명령어 ====================

    @app_commands.command(name="요약디버깅", description="지정한 채널의 특정 시간대 채팅을 txt 파일로 저장합니다 (디버깅용).")
    @app_commands.describe(
        날짜="요약할 날짜 (YY-MM-DD 형식, 예: 26-01-20)",
        언제부터="시작 시간 (0-23, 예: 23)",
        언제까지="종료 시간 (0-23, 예: 1, 시작시간으로부터 최대 4시간)"
    )
    async def summary_debug(
        self,
        interaction: discord.Interaction,
        날짜: str,
        언제부터: app_commands.Range[int, 0, 23],
        언제까지: app_commands.Range[int, 0, 23]
    ):
        """채팅 내용을 txt 파일로 저장 (디버깅용)"""
        
        # 1. 날짜 파싱
        try:
            base_date = datetime.strptime(날짜, "%y-%m-%d")
        except ValueError:
            await interaction.response.send_message(
                "❌ 날짜 형식이 올바르지 않습니다. (예: 26-01-20)",
                ephemeral=True
            )
            return

        # 2. 시간 유효성 검사
        if 언제까지 <= 언제부터:
            # 자정을 넘기는 경우 (예: 23시 ~ 1시)
            hour_diff = (24 - 언제부터) + 언제까지
        else:
            hour_diff = 언제까지 - 언제부터

        if hour_diff > 4:
            await interaction.response.send_message(
                "❌ 최대 4시간까지만 조회할 수 있습니다.",
                ephemeral=True
            )
            return

        # 3. ServerChannel 시트에서 채널 목록 가져오기
        try:
            all_channels = await asyncio.to_thread(
                self.sheet_handler.server_channel_sheet.get_all_records
            )
        except Exception as e:
            logger.error(f"ServerChannel 시트 조회 실패: {e}")
            await interaction.response.send_message(
                "❌ 채널 목록을 불러오는 데 실패했습니다.",
                ephemeral=True
            )
            return

        if not all_channels:
            await interaction.response.send_message(
                "❌ 등록된 채널이 없습니다. ServerChannel 시트를 확인해주세요.",
                ephemeral=True
            )
            return

        # 4. 드롭다운용 데이터 준비
        channel_choices = [
            (
                f"[{rec.get('서버이름', '')}] {rec.get('채널이름', '')}",
                str(rec.get('채널ID', ''))
            )
            for rec in all_channels
            if rec.get('채널ID')
        ]

        if not channel_choices:
            await interaction.response.send_message(
                "❌ 유효한 채널이 없습니다.",
                ephemeral=True
            )
            return

        # 5. 드롭다운 View 표시
        view = ChannelSelectView(channels=channel_choices)
        await interaction.response.send_message(
            f"📅 **{날짜}** {언제부터}시 ~ {언제까지}시 채팅을 가져올 채널을 선택하세요:",
            view=view,
            ephemeral=True
        )
        await view.wait()

        if not view.selected_channel_id:
            return  # 타임아웃 또는 선택 안함

        # 6. 채널 가져오기
        try:
            channel_id = int(view.selected_channel_id)
            target_channel = self.bot.get_channel(channel_id)

            if not target_channel:
                target_channel = await self.bot.fetch_channel(channel_id)

        except discord.NotFound:
            await interaction.followup.send(
                "❌ 채널을 찾을 수 없습니다.",
                ephemeral=True
            )
            return
        except Exception as e:
            logger.error(f"채널 조회 실패: {e}")
            await interaction.followup.send(
                f"❌ 채널 조회 실패: {e}",
                ephemeral=True
            )
            return

        # 7. 권한 확인
        if not target_channel.permissions_for(interaction.guild.me).read_message_history:
            await interaction.followup.send(
                "❌ 해당 채널의 메시지 기록을 읽을 권한이 없습니다.",
                ephemeral=True
            )
            return

        # 8. 처리 중 메시지
        await interaction.followup.send("⏳ 메시지를 수집 중입니다...", ephemeral=True)

        try:
            # 9. 시간 범위 계산
            start_time = base_date.replace(hour=언제부터, minute=0, second=0, microsecond=0)
            
            # 자정 넘기는 경우: 종료 시간은 다음 날
            if 언제까지 <= 언제부터:
                end_time = (base_date + timedelta(days=1)).replace(
                    hour=언제까지, minute=0, second=0, microsecond=0
                )
            else:
                end_time = base_date.replace(hour=언제까지, minute=0, second=0, microsecond=0)

            # 10. 메시지 수집
            messages = []
            async for message in target_channel.history(
                after=start_time,
                before=end_time,
                limit=500,
                oldest_first=True
            ):
                # 봇 메시지 제외
                if message.author.bot:
                    continue
                    
                # 시스템 메시지 제외
                if message.type != discord.MessageType.default:
                    continue

                timestamp = message.created_at.strftime("%Y-%m-%d %H:%M:%S")
                author = message.author.display_name
                content = message.content or "[미디어/임베드]"
                
                # 너무 긴 메시지 자르기
                if len(content) > 500:
                    content = content[:500] + "..."
                
                messages.append(f"[{timestamp}] {author}: {content}")

            if not messages:
                await interaction.followup.send(
                    f"❌ **{target_channel.name}** 채널에서 {날짜} {언제부터}시 ~ {언제까지}시 사이에 메시지가 없습니다.",
                    ephemeral=True
                )
                return

            # 11. txt 파일 내용 구성
            header = f"""========================================
채널: #{target_channel.name}
기간: {start_time.strftime('%Y-%m-%d %H:%M')} ~ {end_time.strftime('%Y-%m-%d %H:%M')}
메시지 수: {len(messages)}개
생성 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
========================================

"""
            chat_log = "\n".join(messages)
            full_content = header + chat_log

            # 12. 파일명 생성 및 저장
            filename = f"chat_log_{target_channel.name}_{start_time.strftime('%y%m%d_%H%M')}_{end_time.strftime('%H%M')}.txt"
            
            # Discord 파일로 전송
            file = discord.File(
                fp=__import__('io').BytesIO(full_content.encode('utf-8')),
                filename=filename
            )

            # 13. 결과 전송
            embed = discord.Embed(
                title=f"📄 채팅 로그 - #{target_channel.name}",
                description=f"총 **{len(messages)}개** 메시지를 수집했습니다.",
                color=discord.Color.green(),
                timestamp=datetime.now()
            )
            embed.add_field(
                name="📅 조회 범위",
                value=f"{start_time.strftime('%Y-%m-%d %H:%M')} ~ {end_time.strftime('%H:%M')}",
                inline=True
            )
            embed.add_field(
                name="📁 파일명",
                value=filename,
                inline=True
            )
            embed.set_footer(text=f"요청자: {interaction.user.display_name}")

            await interaction.followup.send(embed=embed, file=file, ephemeral=True)
            logger.info(f"채팅 로그 저장: {target_channel.name} ({len(messages)}개 메시지)")

        except discord.Forbidden:
            await interaction.followup.send("❌ 채널 접근 권한이 없습니다.", ephemeral=True)
        except Exception as e:
            logger.error(f"채팅 로그 저장 실패: {e}")
            await interaction.followup.send(f"❌ 오류가 발생했습니다: {e}", ephemeral=True)

    # ==================== 채팅 요약 명령어 ====================

    @app_commands.command(name="요약", description="지정한 채널의 특정 시간대 채팅을 요약합니다.")
    @app_commands.describe(
        날짜="요약할 날짜 (YY-MM-DD 형식, 예: 26-01-20). 미입력시 오늘",
        언제부터="시작 시간 (0-23, 예: 14)",
        언제까지="종료 시간 (0-23, 예: 18, 시작시간으로부터 최대 4시간)"
    )
    async def summarize(
        self,
        interaction: discord.Interaction,
        언제부터: app_commands.Range[int, 0, 23],
        언제까지: app_commands.Range[int, 0, 23],
        날짜: str = None
    ):
        """채팅 요약 명령어"""
        
        # 1. 날짜 파싱
        if 날짜:
            try:
                base_date = datetime.strptime(날짜, "%y-%m-%d")
            except ValueError:
                await interaction.response.send_message(
                    "❌ 날짜 형식이 올바르지 않습니다. (예: 26-01-20)",
                    ephemeral=True
                )
                return
        else:
            base_date = datetime.now()

        # 2. 시간 유효성 검사
        if 언제까지 <= 언제부터:
            # 자정을 넘기는 경우 (예: 23시 ~ 1시)
            hour_diff = (24 - 언제부터) + 언제까지
        else:
            hour_diff = 언제까지 - 언제부터

        if hour_diff > 4:
            await interaction.response.send_message(
                "❌ 최대 4시간까지만 요약할 수 있습니다.",
                ephemeral=True
            )
            return

        # 3. ServerChannel 시트에서 채널 목록 가져오기
        try:
            all_channels = await asyncio.to_thread(
                self.sheet_handler.server_channel_sheet.get_all_records
            )
        except Exception as e:
            logger.error(f"ServerChannel 시트 조회 실패: {e}")
            await interaction.response.send_message(
                "❌ 채널 목록을 불러오는 데 실패했습니다.",
                ephemeral=True
            )
            return

        if not all_channels:
            await interaction.response.send_message(
                "❌ 등록된 채널이 없습니다. ServerChannel 시트를 확인해주세요.",
                ephemeral=True
            )
            return

        # 4. 드롭다운용 데이터 준비
        channel_choices = [
            (
                f"[{rec.get('서버이름', '')}] {rec.get('채널이름', '')}",
                str(rec.get('채널ID', ''))
            )
            for rec in all_channels
            if rec.get('채널ID')
        ]

        if not channel_choices:
            await interaction.response.send_message(
                "❌ 유효한 채널이 없습니다.",
                ephemeral=True
            )
            return

        # 5. 드롭다운 View 표시
        date_str = 날짜 if 날짜 else base_date.strftime("%y-%m-%d")
        view = ChannelSelectView(channels=channel_choices)
        await interaction.response.send_message(
            f"📅 **{date_str}** {언제부터}시 ~ {언제까지}시 채팅을 요약할 채널을 선택하세요:",
            view=view,
            ephemeral=True
        )
        await view.wait()

        if not view.selected_channel_id:
            return

        # 6. 채널 가져오기
        try:
            channel_id = int(view.selected_channel_id)
            target_channel = self.bot.get_channel(channel_id)

            if not target_channel:
                target_channel = await self.bot.fetch_channel(channel_id)

        except discord.NotFound:
            await interaction.followup.send(
                "❌ 채널을 찾을 수 없습니다.",
                ephemeral=True
            )
            return
        except Exception as e:
            logger.error(f"채널 조회 실패: {e}")
            await interaction.followup.send(
                f"❌ 채널 조회 실패: {e}",
                ephemeral=True
            )
            return

        # 7. 권한 확인
        if not target_channel.permissions_for(interaction.guild.me).read_message_history:
            await interaction.followup.send(
                "❌ 해당 채널의 메시지 기록을 읽을 권한이 없습니다.",
                ephemeral=True
            )
            return

        # 8. 처리 중 메시지
        await interaction.followup.send("⏳ 메시지를 수집하고 요약 중입니다...", ephemeral=True)

        try:
            # 9. 시간 범위 계산
            start_time = base_date.replace(hour=언제부터, minute=0, second=0, microsecond=0)
            
            # 자정 넘기는 경우: 종료 시간은 다음 날
            if 언제까지 <= 언제부터:
                end_time = (base_date + timedelta(days=1)).replace(
                    hour=언제까지, minute=0, second=0, microsecond=0
                )
            else:
                end_time = base_date.replace(hour=언제까지, minute=0, second=0, microsecond=0)

            # 10. 메시지 수집 (요약디버깅과 동일한 형식)
            messages = []
            async for message in target_channel.history(
                after=start_time,
                before=end_time,
                limit=500,
                oldest_first=True
            ):
                # 봇 메시지 제외
                if message.author.bot:
                    continue
                    
                # 시스템 메시지 제외
                if message.type != discord.MessageType.default:
                    continue

                timestamp = message.created_at.strftime("%Y-%m-%d %H:%M:%S")
                author = message.author.display_name
                content = message.content or "[미디어/임베드]"
                
                # 너무 긴 메시지 자르기
                if len(content) > 500:
                    content = content[:500] + "..."
                
                messages.append(f"[{timestamp}] {author}: {content}")

            if not messages:
                await interaction.followup.send(
                    f"❌ **{target_channel.name}** 채널에서 {언제부터}시 ~ {언제까지}시 사이에 메시지가 없습니다.",
                    ephemeral=True
                )
                return

            # 11. txt 형식으로 채팅 로그 구성 (요약디버깅과 동일)
            header = f"""========================================
채널: #{target_channel.name}
기간: {start_time.strftime('%Y-%m-%d %H:%M')} ~ {end_time.strftime('%Y-%m-%d %H:%M')}
메시지 수: {len(messages)}개
========================================

"""
            chat_log = header + "\n".join(messages)

            # 12. AI 요약 요청
            summary, token_info = await self._summarize_with_gemini(chat_log, target_channel.name)

            if not summary:
                await interaction.followup.send(
                    "❌ 요약 생성에 실패했습니다. 잠시 후 다시 시도해주세요.",
                    ephemeral=True
                )
                return

            # 13. 결과 전송
            embed = discord.Embed(
                title=f"📝 채팅 요약 - #{target_channel.name}",
                description=summary,
                color=discord.Color.blue(),
                timestamp=datetime.now()
            )
            embed.add_field(
                name="📅 요약 범위",
                value=f"{start_time.strftime('%Y-%m-%d %H:%M')} ~ {end_time.strftime('%H:%M')}",
                inline=True
            )
            embed.add_field(
                name="💬 메시지 수",
                value=f"{len(messages)}개",
                inline=True
            )
            embed.add_field(
                name="🔢 토큰 사용량",
                value=token_info,
                inline=False
            )
            embed.set_footer(text=f"요청자: {interaction.user.display_name}")

            await interaction.followup.send(embed=embed)
            logger.info(f"채팅 요약 완료: {target_channel.name} ({len(messages)}개 메시지)")

        except discord.Forbidden:
            await interaction.followup.send("❌ 채널 접근 권한이 없습니다.")
        except Exception as e:
            logger.error(f"채팅 요약 실패: {e}")
            await interaction.followup.send(f"❌ 오류가 발생했습니다: {e}")

    async def _summarize_with_gemini(self, chat_log: str, channel_name: str) -> Tuple[Optional[str], str]:
        """Gemini API로 채팅 요약
        
        Returns:
            Tuple[Optional[str], str]: (요약 텍스트, 토큰 정보 문자열)
        """
        
        if not self.genai_client:
            logger.error("Gemini 클라이언트가 초기화되지 않았습니다.")
            return None, "N/A"

        prompt = f"""다음은 디스코드 채널 '#{channel_name}'의 채팅 기록입니다.
이 대화를 한국어로 최대한 자세히 요약해주세요.

요약 형식:
1. 주요 주제/화제
2. 주요 참여자의 의견 교환 과정
3. 결론

{chat_log}

요약:"""

        try:
            # 동기 함수를 비동기로 실행
            response = await asyncio.to_thread(
                self.genai_client.models.generate_content,
                model="gemini-2.0-flash",
                contents=prompt
            )
            
            # 토큰 사용량 추출
            usage = response.usage_metadata
            token_info = f"입력: {usage.prompt_token_count} / 출력: {usage.candidates_token_count} / 총: {usage.total_token_count}"
            
            return response.text, token_info
            
        except Exception as e:
            logger.error(f"Gemini API 요청 실패: {e}")
            return None, "N/A"


async def setup(bot: commands.Bot):
    await bot.add_cog(SummaryCommandsCog(bot, bot.sheet_handler))  # type: ignore