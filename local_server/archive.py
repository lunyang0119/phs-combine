"""
채팅 아카이브 명령어 Cog
지정한 채널의 특정 기간 채팅을 txt 파일로 저장
"""

import discord
from discord import app_commands
from discord.ext import commands
import logging
import asyncio
import io
from datetime import datetime, timedelta
from typing import Optional, List
import os
from dotenv import load_dotenv

from google_sheets_handler import SheetsHandler
from view import ChannelSelectView

logger = logging.getLogger(__name__)

# 파일 크기 제한 (25MB)
MAX_FILE_SIZE = 25 * 1024 * 1024


def split_content_by_size(messages: List[str], header: str, max_bytes: int = MAX_FILE_SIZE) -> List[str]:
    """
    메시지 리스트를 25MB 단위로 분할하여 파일 내용 리스트 반환
    각 파일에는 헤더가 포함됨
    """
    if not messages:
        return []
    
    # 전체 내용이 한 파일에 들어가는지 확인
    full_content = header + "\n".join(messages)
    if len(full_content.encode('utf-8')) <= max_bytes:
        return [full_content]
    
    # 분할 필요
    chunks = []
    current_messages = []
    current_size = len(header.encode('utf-8'))
    
    for msg in messages:
        msg_size = len((msg + "\n").encode('utf-8'))
        
        if current_size + msg_size > max_bytes:
            # 현재 청크 저장
            if current_messages:
                chunks.append(header + "\n".join(current_messages))
            current_messages = [msg]
            current_size = len(header.encode('utf-8')) + msg_size
        else:
            current_messages.append(msg)
            current_size += msg_size
    
    # 마지막 청크
    if current_messages:
        chunks.append(header + "\n".join(current_messages))
    
    return chunks


class ArchiveCommandsCog(commands.Cog):
    """아카이브"""

    def __init__(self, bot, sheet_handler: SheetsHandler):
        self.bot = bot
        self.sheet_handler = sheet_handler
        logger.info("ArchiveCommandsCog 초기화 완료")

    # ==================== 아카이브 명령어 ====================

    @app_commands.command(name="아카이브", description="지정한 채널의 특정 기간 채팅을 txt 파일로 저장합니다.")
    @app_commands.describe(
        시작일="시작 날짜 (YY-MM-DD 형식, 예: 26-01-20)",
        종료일="[선택] 종료 날짜 (YY-MM-DD 형식). 미입력 시 시작일 하루만 저장",
        시작시간="[선택] 시작 시간 (0-23). 기본값: 0. 단일 날짜에서만 적용",
        종료시간="[선택] 종료 시간 (0-24). 기본값: 24(=23:59). 단일 날짜에서만 적용. 시작시간보다 작으면 다음날까지 저장"
    )
    async def archive(
        self,
        interaction: discord.Interaction,
        시작일: str,
        종료일: Optional[str] = None,
        시작시간: Optional[app_commands.Range[int, 0, 23]] = None,
        종료시간: Optional[app_commands.Range[int, 0, 24]] = None
    ):
        """채팅 내용을 txt 파일로 저장"""
        
        # 1. 시작일 파싱
        try:
            start_date = datetime.strptime(시작일, "%y-%m-%d")
        except ValueError:
            await interaction.response.send_message(
                "❌ 시작일 형식이 올바르지 않습니다. (예: 26-01-20)",
                ephemeral=True
            )
            return

        # 2. 종료일 파싱 및 검증
        end_date = None
        is_date_range = False
        
        if 종료일:
            try:
                end_date = datetime.strptime(종료일, "%y-%m-%d")
            except ValueError:
                await interaction.response.send_message(
                    "❌ 종료일 형식이 올바르지 않습니다. (예: 26-01-22)",
                    ephemeral=True
                )
                return
            
            if end_date < start_date:
                await interaction.response.send_message(
                    "❌ 종료일이 시작일보다 이전입니다.",
                    ephemeral=True
                )
                return
            
            is_date_range = True

        # 3. 시간 설정
        start_hour = 시작시간 if 시작시간 is not None else 0
        end_hour = 종료시간 if 종료시간 is not None else 24
        
        # 날짜 범위 지정 시 시간 파라미터 무시 경고
        time_warning = ""
        if is_date_range and (시작시간 is not None or 종료시간 is not None):
            time_warning = "\n⚠️ 날짜 범위 지정 시 시간 설정은 무시됩니다."
            start_hour = 0
            end_hour = 24

        # 4. 시간 범위 계산
        if is_date_range:
            # 날짜 범위: 시작일 00:00 ~ 종료일 23:59:59
            start_time = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
            end_time = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)
        else:
            # 단일 날짜: 시작시간 ~ 종료시간
            start_time = start_date.replace(hour=start_hour, minute=0, second=0, microsecond=0)
            
            if end_hour == 24:
                # 24시 = 23:59:59
                end_time = start_date.replace(hour=23, minute=59, second=59, microsecond=999999)
            elif end_hour <= start_hour:
                # 자정 넘김: 다음날 종료시간까지
                end_time = (start_date + timedelta(days=1)).replace(
                    hour=end_hour, minute=0, second=0, microsecond=0
                )
            else:
                end_time = start_date.replace(hour=end_hour, minute=0, second=0, microsecond=0)

        # 5. ServerChannel 시트에서 채널 목록 가져오기
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

        # 6. 드롭다운용 데이터 준비
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

        # 7. 조회 범위 표시 문자열
        if is_date_range:
            range_display = f"{시작일} ~ {종료일} (전체)"
        else:
            start_display = f"{start_hour}시"
            end_display = "23:59" if end_hour == 24 else f"{end_hour}시"
            range_display = f"{시작일} {start_display} ~ {end_display}"

        # 8. 드롭다운 View 표시
        view = ChannelSelectView(channels=channel_choices)
        await interaction.response.send_message(
            f"📅 **{range_display}** 채팅을 가져올 채널을 선택하세요:{time_warning}",
            view=view,
            ephemeral=True
        )
        await view.wait()

        if not view.selected_channel_id:
            return  # 타임아웃 또는 선택 안함

        # 9. 채널 가져오기
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

        # 10. 권한 확인
        if not target_channel.permissions_for(interaction.guild.me).read_message_history:
            await interaction.followup.send(
                "❌ 해당 채널의 메시지 기록을 읽을 권한이 없습니다.",
                ephemeral=True
            )
            return

        # 11. 처리 중 메시지
        await interaction.followup.send("⏳ 메시지를 수집 중입니다... (대용량일 경우 시간이 걸릴 수 있습니다)", ephemeral=True)

        try:
            # 12. 메시지 수집 (limit 제거)
            messages = []
            async for message in target_channel.history(
                after=start_time,
                before=end_time,
                limit=None,
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
                    f"❌ **{target_channel.name}** 채널에서 해당 기간에 메시지가 없습니다.",
                    ephemeral=True
                )
                return

            # 13. 헤더 구성
            header = f"""========================================
채널: #{target_channel.name}
기간: {start_time.strftime('%Y-%m-%d %H:%M')} ~ {end_time.strftime('%Y-%m-%d %H:%M')}
메시지 수: {len(messages)}개
생성 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
========================================

"""

            # 14. 파일 분할
            content_chunks = split_content_by_size(messages, header)
            total_parts = len(content_chunks)

            # 15. 파일명 기본 형식
            if is_date_range:
                base_filename = f"chat_log_{target_channel.name}_{start_date.strftime('%y%m%d')}_{end_date.strftime('%y%m%d')}"
            else:
                base_filename = f"chat_log_{target_channel.name}_{start_time.strftime('%y%m%d_%H%M')}_{end_time.strftime('%H%M')}"

            # 16. Discord 파일 객체 생성
            files: List[discord.File] = []
            for i, chunk in enumerate(content_chunks, 1):
                if total_parts == 1:
                    filename = f"{base_filename}.txt"
                else:
                    filename = f"{base_filename}_part{i}.txt"
                
                files.append(discord.File(
                    fp=io.BytesIO(chunk.encode('utf-8')),
                    filename=filename
                ))

            # 17. 결과 Embed
            embed = discord.Embed(
                title=f"📄 채팅 로그 - #{target_channel.name}",
                description=f"총 **{len(messages)}개** 메시지를 수집했습니다.",
                color=discord.Color.green(),
                timestamp=datetime.now()
            )
            embed.add_field(
                name="📅 조회 범위",
                value=f"{start_time.strftime('%Y-%m-%d %H:%M')} ~ {end_time.strftime('%Y-%m-%d %H:%M')}",
                inline=True
            )
            
            if total_parts > 1:
                embed.add_field(
                    name="📁 파일",
                    value=f"{total_parts}개로 분할됨 (25MB 제한)",
                    inline=True
                )
            else:
                embed.add_field(
                    name="📁 파일명",
                    value=files[0].filename,
                    inline=True
                )

            # 18. 파일 전송 (Discord는 한 번에 최대 10개)
            await interaction.channel.send(embed=embed)
            
            for i in range(0, len(files), 10):
                batch = files[i:i+10]
                await interaction.channel.send(files=batch)

            logger.info(f"채팅 로그 저장: {target_channel.name} ({len(messages)}개 메시지, {total_parts}개 파일)")

        except discord.Forbidden:
            await interaction.followup.send("❌ 채널 접근 권한이 없습니다.", ephemeral=True)
        except Exception as e:
            logger.error(f"채팅 로그 저장 실패: {e}")
            await interaction.followup.send(f"❌ 오류가 발생했습니다: {e}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ArchiveCommandsCog(bot, bot.sheet_handler))  # type: ignore