"""
유틸리티 명령어 Cog
레거시 코드에서 이관된 TRPG 세션용 유틸리티 명령어들
(주사위, 책찾기, 낚시, 보존서고, 행동 판정 등)
"""

import discord
from discord import app_commands
from discord.ext import commands
# import random
from combat import random_utils
import logging
import asyncio
from google_sheets_handler import SheetsHandler
from view import ChannelSelectView

logger = logging.getLogger(__name__)


class UtilityCommandsCog(commands.Cog):
    """TRPG 세션용 유틸리티 명령어 모음"""

    def __init__(self, bot, sheet_handler: SheetsHandler):
        self.bot = bot
        self.sheet_handler = sheet_handler
        logger.info("UtilityCommandsCog 초기화 완료")

    # ==================== 주사위 명령어 ====================

    @app_commands.command(name="주사위", description="원하는 주사위를 출력합니다. 예시: 1d20 -> 20면체 주사위 하나")
    @app_commands.describe(dice="주사위 표기법 (예: 1d20, 3d6)")
    async def custom_dice(self, interaction: discord.Interaction, dice: str = "1d20"):
        # 주사위 표기법 파싱 (예: "1d20" -> amount=1, sides=20)
        try:
            dice = dice.lower().strip()
            if 'd' not in dice:
                await interaction.response.send_message("❌ 주사위 형식이 올바르지 않습니다. 예: 1d20, 3d6", ephemeral=True)
                return

            parts = dice.split('d')
            if len(parts) != 2:
                await interaction.response.send_message("❌ 주사위 형식이 올바르지 않습니다. 예: 1d20, 3d6", ephemeral=True)
                return

            amount = int(parts[0]) if parts[0] else 1
            sides = int(parts[1])

        except ValueError:
            await interaction.response.send_message("❌ 주사위 형식이 올바르지 않습니다. 예: 1d20, 3d6", ephemeral=True)
            return

        if amount <= 0 or sides <= 0:
            await interaction.response.send_message("❌ 주사위 개수와 면 수는 1 이상이어야 합니다.", ephemeral=True)
            return

        if amount > 20:
            await interaction.response.send_message("❌ 주사위는 최대 20개까지만 굴릴 수 있습니다.", ephemeral=True)
            return

        rolls = [random_utils.randint(1, sides) for _ in range(amount)]
        total = sum(rolls)

        if amount == 1:
            embed = discord.Embed(
                title="**주사위 결과**",
                description=f"{amount}d{sides}\n→ **{rolls[0]}**",
                colour=0x00FF00
            )
        else:
            if amount <= 10:
                dice_results = " + ".join([f"**{roll}**" for roll in rolls])
                embed = discord.Embed(
                    title="**주사위 결과**",
                    description=f"{amount}d{sides}\n{dice_results}\n\n**총합: {total}**",
                    colour=0x00FF00
                )
            else:
                max_roll = max(rolls)
                min_roll = min(rolls)
                embed = discord.Embed(
                    title="**주사위 결과**",
                    description=f"{amount}d{sides}\n**총합: {total}**\n최고: **{max_roll}** | 최저: **{min_roll}**",
                    colour=0x00FF00
                )
                for i in range(0, len(rolls), 5):
                    batch = rolls[i:i+5]
                    batch_str = ", ".join([str(roll) for roll in batch])
                    embed.add_field(
                        name=f"주사위 {i+1}-{min(i+5, len(rolls))}",
                        value=batch_str,
                        inline=True
                    )

        await interaction.response.send_message(embed=embed)

    # ==================== 행동 판정 명령어 ====================

    @app_commands.command(name="행동", description="효과가 없다 / 있다의 결과가 나오는 1d2 주사위를 굴립니다.")
    async def action(self, interaction: discord.Interaction):
        result = random_utils.choice(["효과가 있어보여 쿠뽀!", "아무 일도 일어나지지 않았어 쿠뽀...."])
        embed = discord.Embed(
            title=f"{interaction.user.display_name}이(가) 행동했어, 쿠뽀!",
            description=f"{result}",
            colour=0x00FF00
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="일반행동", description="일반 행동을 수행합니다.")
    @app_commands.describe(option="응답에서 하나만 무작위로 선택할지 여부 (y: 하나만 선택, n: 전체 문자열; 기본값은 n)")
    async def general_action(self, interaction: discord.Interaction, option: str = "n"):
        roll = random_utils.randint(1, 10)
        responses = {
            1: "기대보다 효과적이었다 / 사람이 더 필요하다 / 사라졌다",
            2: "예상치 못한 문제가 발생했다 / 만족스러웠다 / 힘만 뺐다",
            3: "아무 일도 일어나지 않았다 / 불쾌해졌다 / 기억났다",
            4: "성공한 건지 잘 모르겠다 / 난장판이 되었다 / 찾아냈다",
            5: "그럭저럭 해냈다 / 원래대로 돌아왔다 / 위험했다",
            6: "시간이 더 필요하다 / 뜻밖의 수확이 있었다 / 고통을 유발했다",
            7: "반쯤은 해냈다 / 사소한 문제가 생겼다 / 망가졌다",
            8: "목표를 이루었다 / 막대한 손실을 입었다 / 바뀌었다",
            9: "약간은 진척이 있었다 / 자원만 낭비했다 / 만들었다",
            10: "시간만 낭비했다 / 감정이 격해졌다 / 미묘했다"
        }
        resp_text = responses[roll]
        if option.lower() == "y":
            parts = [part.strip() for part in resp_text.split("/")]
            chosen_resp = random_utils.choice(parts)
        else:
            chosen_resp = resp_text

        embed = discord.Embed(
            title=f"{interaction.user.display_name}이(가) 행동했어, 쿠뽀!",
            description=f"**{roll}** → **{chosen_resp}**",
            colour=0x00FF00
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="전투행동", description="전투 행동을 수행합니다.")
    @app_commands.describe(option="응답에서 하나만 무작위로 선택할지 여부 (y: 하나만 선택, n: 전체 문자열; 기본값은 n)")
    async def combat_action(self, interaction: discord.Interaction, option: str = "n"):
        roll = random_utils.randint(1, 10)
        responses = {
            1: "적중했다 / 숨겼다 / 떨어졌다",
            2: "빗나갔다 / 바꾸었다 / 무너뜨렸다",
            3: "피했다 / 빼앗았다 / 늦었다",
            4: "붙잡았다 / 넘어졌다 / 접근했다",
            5: "막았다 / 노렸다 / 살폈다",
            6: "들켰다 / 쫓았다 / 터졌다",
            7: "아슬아슬했다 / 멈추었다 / 던졌다",
            8: "고통스럽다 / 밀었다 / 물러났다",
            9: "흔들렸다 / 기다렸다 / 부딪쳤다",
            10: "일어났다 / 버텼다 / 돌진했다"
        }
        resp_text = responses[roll]
        if option.lower() == "y":
            parts = [part.strip() for part in resp_text.split("/")]
            chosen_resp = random_utils.choice(parts)
        else:
            chosen_resp = resp_text

        embed = discord.Embed(
            title=f"{interaction.user.display_name}이(가) 행동했어, 쿠뽀!",
            description=f"**{roll}** → **{chosen_resp}**",
            colour=0x00FF00
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="대인행동", description="대인행동을 수행합니다.")
    @app_commands.describe(option="응답에서 하나만 무작위로 선택할지 여부 (y: 하나만 선택, n: 전체 문자열; 기본값은 n)")
    async def interpersonal_action(self, interaction: discord.Interaction, option: str = "n"):
        roll = random_utils.randint(1, 10)
        responses = {
            1: "협력한다 / 애원한다 / 웃는다",
            2: "굴복한다 / 안도한다 / 협상한다",
            3: "망설인다 / 무시한다 / 화를 낸다",
            4: "우호적이다 / 거짓말한다 / 두려워한다",
            5: "경계한다 / 위로한다 / 당황한다",
            6: "신뢰한다 / 시큰둥해 한다 / 침묵한다",
            7: "대가를 요구한다 / 약속한다 / 의심한다",
            8: "적대한다 / 알려준다 / 슬퍼한다",
            9: "만족해 한다 / 협박한다 / 허락한다",
            10: "반응이 없다 / 비웃는다 / 사과한다"
        }
        resp_text = responses[roll]
        if option.lower() == "y":
            parts = [part.strip() for part in resp_text.split("/")]
            chosen_resp = random_utils.choice(parts)
        else:
            chosen_resp = resp_text

        embed = discord.Embed(
            title=f"{interaction.user.display_name}이(가) 행동했어, 쿠뽀!",
            description=f"**{roll}** → **{chosen_resp}**",
            colour=0x00FF00
        )
        await interaction.response.send_message(embed=embed)

    # ==================== 책찾기 명령어 ====================

    @app_commands.command(name="책찾기", description="랜덤 책을 뽑습니다.")
    async def book_search(self, interaction: discord.Interaction):
        display_name = interaction.user.display_name
        roll = random_utils.randint(1, 10)
        responses = {
            1: "수기: 누군가가 자신의 경험에 대해 직접 쓴 수기를 발견했습니다.",
            2: "머리 조심: 높은 곳에 있던 책이 당신을 향해 떨어집니다!",
            3: "특별 레시피: 무언가를 만들 수 있는 제작법이 적힌 쪽지를 발견했습니다. 과연 재료는…?",
            4: "비밀 금고: 책 가운데가 파여 있습니다. 안에 든 것은…?",
            5: "긴급 추방: 책을 펼치자, 안에 있던 심연이 당신을 집어삼킵니다! 차원점검열차 내의 아무 장소로 이동합니다.",
            6: "자료 조사: 당신이 찾고 있었거나 마침 궁금했던 것을 알아낼 수 있을지도 모르는 책을 발견했습니다.",
            7: "해독 불가: 읽을 수 없는 문자로 된 책을 발견했습니다.",
            8: "괜찮아: 당신에게 건네는 듯한 따뜻한 말이 적힌 책을 발견했습니다.",
            9: "잡학 상식: 이 책을 보지 않았더라면 당신이 평생 궁금해하지 않았을 정보를 알려주는 책을 발견했습니다.",
            10: "이것도 책?: 특이한 재료나 형식으로 만들어진 책을 발견했습니다."
        }
        resp_text = responses[roll]
        parts = [part.strip() for part in resp_text.split(":")]
        resp1 = parts[0] if len(parts) > 0 else "알 수 없음"
        resp2 = parts[1] if len(parts) > 1 else ""
        embed = discord.Embed(
            title=f"**{interaction.user.display_name}이(가) 책을 뽑았어, 쿠뽀!**",
            description=f"**{roll}** → **{resp1 or '알 수 없음'}**\n{resp2 or ''}",
            colour=0x00FF00
        )
        await interaction.response.send_message(embed=embed)
        # 로그 기록
        await asyncio.to_thread(
            self.sheet_handler.book_log_sheet.append_row,
            [display_name, roll, responses[roll]]
        )


    # ==================== 낚시 명령어 ====================

    @app_commands.command(name="낚시", description="낚시를 위한 명령어입니다.")
    async def fishing(self, interaction: discord.Interaction):
        view = FishingView(self.sheet_handler)
        embed = discord.Embed(
            title="🎣 낚시터 선택",
            description="어느 바다에서 낚시를 하시겠습니까?",
            colour=0x00FF00
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # ==================== 보존서고 명령어 ====================

    @app_commands.command(name="보존서고", description="위험한 책이 가득한 보존서고에서 책을 뽑을 수 있는 명령어.")
    async def forbidden_library(self, interaction: discord.Interaction):
        view = ForbiddenView(self.sheet_handler)
        embed = discord.Embed(
            title="📕 보존서고",
            description="위험도를 골라주세요",
            colour=0x00FF00
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="보존서고책찾기", description="보존서고에서 원하는 책을 찾을 수 있는 기능")
    async def forbidden_library_cheat(self, interaction: discord.Interaction):
        view = ForbiddenCheatView()
        embed = discord.Embed(
            title="📕 보존서고",
            description="위험도를 골라주세요",
            colour=0x00FF00
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # ==================== 연회 명령어 ====================

    @app_commands.command(name="연회", description="무도회를 위한 명령어입니다.")
    async def dancing(self, interaction: discord.Interaction):
        view = DancingView(self.sheet_handler)
        embed = discord.Embed(
            title="🩰 연회 옷차림",
            description="옷차림을 골라주세요.",
            colour=0x00FF00
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # ==================== 랜덤 선택 명령어 ====================

    @app_commands.command(name="골라", description="공백으로 구분된 선택지 중 하나를 랜덤으로 골라줍니다")
    @app_commands.describe(choices="선택지들을 공백으로 구분하여 입력 (예: 짜장 짬뽕)")
    async def random_choice(self, interaction: discord.Interaction, choices: str):
        # 공백으로 분리
        options = choices.split()

        if len(options) == 0:
            await interaction.response.send_message("❌ 선택지를 입력해주세요!", ephemeral=True)
            return

        if len(options) == 1:
            await interaction.response.send_message("❌ 최소 2개 이상의 선택지를 입력해주세요!", ephemeral=True)
            return

        # 랜덤으로 하나 선택
        selected = random_utils.choice(options)

        # 임베드 생성
        embed = discord.Embed(
            title="🎲 랜덤 선택",
            description=f"선택지: {', '.join([f'`{opt}`' for opt in options])}",
            colour=0xFFD700  # 금색
        )
        embed.add_field(
            name="선택 결과",
            value=f"**{selected}**",
            inline=False
        )
        await interaction.response.send_message(embed=embed)

    # ==================== 채널 추가 명령어 ====================

    @app_commands.command(name="채널추가", description="현재 채널을 ServerChannel 시트에 등록합니다.")
    @app_commands.describe(이름="채널의 표시 이름 (예: 일반, 공지)")
    async def add_channel(self, interaction: discord.Interaction, 이름: str):
        """현재 채널/스레드 정보를 ServerChannel 시트에 추가"""
        try:
            # 현재 채널/스레드 정보 수집
            channel = interaction.channel
            guild_id = interaction.guild_id
            server_name = interaction.guild.name if interaction.guild else "알 수 없음"
            if server_name == "망연님의 서버":
                server_name = "개인"
            elif server_name == "크리스탈의 안식처":
                server_name = "크순"
            
            channel_id = str(channel.id)
            guild_id_str = str(guild_id)  # 시트 저장용 문자열 변환

            # ServerChannel 시트에서 기존 데이터 확인
            existing_channels = await asyncio.to_thread(
                self.sheet_handler.server_channel_sheet.get_all_records
            )

            # 같은 채널 ID가 이미 있는지 확인
            for row in existing_channels:
                if str(row.get('채널ID', '')) == channel_id:
                    existing_name = row.get('채널이름', '알 수 없음')
                    existing_server = row.get('서버이름', '알 수 없음')
                    
                    embed = discord.Embed(
                        title="⚠️ 이미 등록된 채널",
                        description=f"이 채널은 이미 등록되어 있습니다.",
                        colour=0xFFAA00
                    )
                    embed.add_field(name="등록된 서버", value=existing_server, inline=True)
                    embed.add_field(name="등록된 이름", value=existing_name, inline=True)
                    embed.add_field(name="채널 ID", value=channel_id, inline=True)
                    
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                    logger.info(f"채널 추가 실패 (중복): {channel_id} - 기존 이름: {existing_name}")
                    return
            
            
            # ServerChannel 시트에 추가
            new_row = [server_name, 이름, channel_id, guild_id_str]
            
            await asyncio.to_thread(
                self.sheet_handler.server_channel_sheet.append_row,
                new_row,
                value_input_option='USER_ENTERED'
            )
            
            # 성공 메시지
            embed = discord.Embed(
                title="✅ 채널 등록 완료",
                description=f"**{이름}** 채널이 등록되었습니다.",
                colour=0x00FF00
            )
            embed.add_field(name="서버", value=server_name, inline=True)
            embed.add_field(name="채널 ID", value=channel_id, inline=True)
            embed.add_field(name="서버 ID", value=guild_id, inline=True)
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
            logger.info(f"채널 추가: {server_name} - {이름} - {channel_id}")
            
        except Exception as e:
            logger.error(f"채널 추가 중 오류: {e}")
            await interaction.response.send_message(
                f"❌ 채널 추가 실패: {e}",
                ephemeral=True
            )

    # ==================== 채널 메시지 전송 명령어 ====================

    @app_commands.command(name="챗", description="지정한 채널에 메시지를 전송합니다.")
    @app_commands.describe(메세지="전송할 메시지 내용")
    async def 챗(self, interaction: discord.Interaction, 메세지: str):
        """ServerChannel 시트에서 채널을 선택하여 메시지 전송"""
        # 1. ServerChannel 시트에서 채널 목록 가져오기
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

        # 2. 드롭다운용 데이터 준비: (표시이름, 채널ID)
        channel_choices = [
            (
                f"[{rec.get('서버이름', '')}] {rec.get('채널이름', '')}",
                str(rec.get('채널ID', ''))
            )
            for rec in all_channels
            if rec.get('채널ID')  # 채널ID가 있는 항목만
        ]

        if not channel_choices:
            await interaction.response.send_message(
                "❌ 유효한 채널이 없습니다.",
                ephemeral=True
            )
            return

        # 3. 드롭다운 View 표시
        view = ChannelSelectView(channels=channel_choices)
        await interaction.response.send_message(
            "메시지를 전송할 채널을 선택하세요:",
            view=view,
            ephemeral=True
        )
        await view.wait()

        if not view.selected_channel_id:
            return  # 타임아웃 또는 선택 안함

        # 4. 메시지 전송
        try:
            channel_id = int(view.selected_channel_id)
            target_channel = self.bot.get_channel(channel_id)

            if not target_channel:
                # 캐시에 없으면 fetch 시도
                target_channel = await self.bot.fetch_channel(channel_id)

            await target_channel.send(메세지)

            # 성공 메시지
            await interaction.followup.send(
                f"✅ **{target_channel.name}** 채널에 메시지를 전송했습니다.",
                ephemeral=True
            )
            logger.info(f"채널 메시지 전송: {interaction.user.display_name} -> {target_channel.name}")

        except discord.Forbidden:
            await interaction.followup.send(
                "❌ 해당 채널에 메시지를 보낼 권한이 없습니다.",
                ephemeral=True
            )
        except discord.NotFound:
            await interaction.followup.send(
                "❌ 채널을 찾을 수 없습니다. 채널 ID를 확인해주세요.",
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"채널 메시지 전송 실패: {e}")
            await interaction.followup.send(
                f"❌ 메시지 전송 실패: {e}",
                ephemeral=True
            )

    # ==================== DMW 명령어 (전투 외 실행) ====================

    @app_commands.command(name="dmw", description="전투 외에 DMW (Digital Mind Wave)를 실행합니다. 버프 효과는 없습니다.")
    async def dmw_standalone(self, interaction: discord.Interaction):
        """전투 외 DMW 실행 (연출만, 버프 없음)"""
        user_id = str(interaction.user.id)

        # 캐릭터 존재 여부 확인
        if user_id not in self.sheet_handler.characters_sheet_cache.index:
            await interaction.response.send_message("❌ 캐릭터를 먼저 생성하거나 시트를 갱신해주세요! (`/캐릭터생성`, `/시트갱신 sheet2cache`)", ephemeral=True)
            return

        # 먼저 ephemeral 응답을 보내서 Discord에게 "응답했다"고 알림
        await interaction.response.send_message("🎰 DMW를 실행합니다...", ephemeral=True, delete_after=0.1)

        # 캐릭터 정보 가져오기
        char_data = self.sheet_handler.get_char_data(user_id)
        player_name = char_data.get('character_name', interaction.user.display_name)
        charm_stat = int(char_data.get('charm', 0))

        # DMW 롤 계산
        from dmw_data import DMW_FIGURES, FAIL_MESSAGES
        import utils
        import constants

        dmw_result_value = utils.calculate_dmw_number(charm_stat)

        result_figure_name = None
        is_success = False

        # 성공 여부 판정
        if dmw_result_value == constants.DMW_SEPHIROTH_VALUE:
            result_figure_name = "세피로스"
            is_success = True
        elif dmw_result_value >= constants.DMW_SUCCESS_THRESHOLD:
            figures = {name: data for name, data in DMW_FIGURES.items() if name != "세피로스"}
            names = list(figures.keys())
            weights = [data['weight'] for data in figures.values()]
            result_figure_name = random_utils.choices(names, weights=weights, k=1)[0]
            is_success = True

        # 슬롯머신 연출 (채널에 공개 메시지로 전송)
        dmw_message = await self._animate_dmw_slots(
            interaction.channel,
            is_success,
            result_figure_name,
            player_name,
            interaction.user
        )

        # 결과 메시지
        if is_success and result_figure_name:
            figure_data = DMW_FIGURES[result_figure_name]

            # 성공 메시지
            success_embed = discord.Embed(
                title=f"DMW - Matching Success",
                description=f"**[ {result_figure_name} | {result_figure_name} | {result_figure_name} ]**\n{result_figure_name}에 대한 기억이 재생됩니다....",
                color=figure_data['color']
            )
            success_embed.set_author(
                name=player_name,
                icon_url=interaction.user.avatar.url if interaction.user.avatar else None
            )
            await dmw_message.edit(embed=success_embed)
            await asyncio.sleep(1)

            # 대사 순차 출력
            memories = figure_data.get('memories', [])
            if memories and len(memories) > 0:
                selected = random_utils.choice(memories)
                if selected and len(selected) > 0:
                    for line in selected:
                        memory_embed = discord.Embed(
                            description=f"*\"{line}\"*",
                            color=figure_data['color']
                        )
                        await interaction.channel.send(embed=memory_embed)
                        await asyncio.sleep(1.5)

            # 전투 외 실행임을 알림
            notice_embed = discord.Embed(
                description="⚠️ 전투 외 DMW 실행으로 버프 효과는 적용되지 않습니다.",
                color=discord.Color.light_gray()
            )
            await interaction.channel.send(embed=notice_embed)

        else:
            # 실패 메시지
            fail_message = random_utils.choice(FAIL_MESSAGES)
            fail_embed = discord.Embed(
                title="DMW - Matching Failed...",
                description=f"**\"{fail_message}\"**",
                color=discord.Color.dark_gray()
            )
            await dmw_message.edit(embed=fail_embed)

    async def _animate_dmw_slots(
        self,
        channel: discord.abc.Messageable,
        is_success: bool,
        success_figure: str,
        player_name: str,
        user: discord.User
    ) -> discord.Message:
        """DMW 슬롯머신 연출 (전투 외 버전)"""
        from dmw_data import DMW_FIGURES, FAIL_FIGURE
        import constants

        # 최종 슬롯 결정
        if is_success and success_figure:
            final_slots = [success_figure, success_figure, success_figure]
            color = DMW_FIGURES[success_figure]['color']
        else:
            # 꽝 패턴
            if random_utils.get_random() < constants.DMW_CATSI_FAIL_CHANCE:
                fail_slots = random_utils.sample(list(DMW_FIGURES.keys()), 2)
                fail_slots.append(list(FAIL_FIGURE.keys())[0])
                random_utils.shuffle(fail_slots)
            else:
                fail_slots = random_utils.sample(list(DMW_FIGURES.keys()), 3)

            final_slots = fail_slots
            color = discord.Color.dark_gray()

        # 초기 메시지
        slots = ["?", "?", "?"]
        embed = discord.Embed(
            title="DMW - Digital Mind Wave",
            description=f"**[ {slots[0]} | {slots[1]} | {slots[2]} ]**",
            color=discord.Color.light_grey()
        )
        embed.set_author(
            name=player_name,
            icon_url=user.avatar.url if user.avatar else None
        )
        dmw_message = await channel.send(embed=embed)

        # 슬롯 애니메이션
        for i in range(3):
            await asyncio.sleep(constants.DMW_SLOT_DELAY)
            slots[i] = final_slots[i]
            embed.description = f"**[ {slots[0]} | {slots[1]} | {slots[2]} ]**"
            embed.color = color if i == 2 else discord.Color.light_grey()
            await dmw_message.edit(embed=embed)

        return dmw_message



# ==================== Discord UI Views ====================

class DancingView(discord.ui.View):
    """연회 View"""
    def __init__(self, sheet_handler):
        super().__init__(timeout=60)
        self.sheet_handler = sheet_handler

    @discord.ui.select(
        placeholder="입고 갈 옷차림을 선택해줘 쿠뽀!",
        options=[
            discord.SelectOption(label="맞지 않는 옷차림", value="맞지 않는 옷차림", emoji="🫤"),
            discord.SelectOption(label="연회 참석자 옷차림", value="연회 참석자 옷차림", emoji="🙂"),
            discord.SelectOption(label="가면무도회", value="가면무도회", emoji="🎭")
        ],
    )
    async def dancing_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        option = select.values[0]
        roll = random_utils.randint(1, 10)

        if option == "맞지 않는 옷차림":
            responses = {
                1: "실수: 누군가가 음료를 엎질렀습니다.",
                2: "분실물: 누군가가 소지품을 떨어뜨리고 지나가는 것을 목격했습니다.",
                3: "부주의: 누군가와 부딪혔습니다.",
                4: "발 조심: 누군가가 당신의 발을 밟고 지나갑니다.",
                5: "다음 음악: 음악이 새로운 분위기로 바뀝니다.",
                6: "웃음소리: 당신 근처에 있던 사람들이 당신 쪽을 흘끗하며 웃음을 터뜨립니다.",
                7: "수상쩍음: 연회장 구석에 서 있던 누군가가 당신과 눈이 마주치자 급히 자리를 뜨는 것이 보입니다.",
                8: "작은 친구: 테이블 밑에 숨어 있는 작은 동물을 발견했습니다.",
                9: "길 안내: 누군가가 당신에게 길을 묻습니다.",
                10: "정보 획득: 누군가가 대상에 대해 이야기하는 것을 엿들었습니다."
            }
            b_name = "맞지 않는 옷차림"
        elif option == "연회 참석자 옷차림":
            responses = {
                1: "패셔니스타: 누군가가 당신의 옷차림을 칭찬합니다.",
                2: "고향 이야기: 누군가가 당신의 고향에 대해 묻습니다.",
                3: "관심사 이야기: 누군가가 당신의 최근 관심사에 대해 묻습니다.",
                4: "어려운 이야기: 누군가가 당신이 잘 모르는 화제에 대해 묻습니다.",
                5: "댄스 파트너: 누군가가 당신에게 춤을 신청합니다.",
                6: "만찬: 누군가가 당신에게 식사 테이블에 앉자고 제안합니다.",
                7: "논쟁: 누군가가 옆 사람과 논쟁이 붙어 언성을 높이기 시작합니다.",
                8: "카드 게임: 누군가가 당신에게 카드 게임을 신청합니다.",
                9: "정보 획득: 누군가기 당신에게 대상에 대한 정보를 알려주었습니다.",
                10: "정보 획득: 누군가가 대상에 대해 이야기하는 것을 엿들었습니다."
            }
            b_name = "연회 참석자 옷차림"
        elif option == "가면무도회":
            responses = {
                1: "구면: 당신이 알고 있는 사람의 얼굴 또는 목소리와 비슷하게 느껴지는 이가 있습니다.",
                2: "뒷담: 뒤에서 당신에 대해 은밀히 속삭이는 대화 소리가 들렸습니다.",
                3: "밀회: 누군가가 단둘이 테라스로 나가자고 제안합니다",
                4: "전언: 누군가가 당신에게 쪽지를 몰래 건넸습니다.",
                5: "오해: 누군가가 당신을 다른 사람으로 착각한 채로 말합니다.",
                6: "미담: 누군가가 당신의 어느 과거 행적에 대한 칭찬 같은 말을 합니다.",
                7: "의미심장: 누군가가 당신의 미래를 위한 경고나 조언 같은 말을 합니다.",
                8: "의혹: 누군가가 당신의 정체나 속내를 의심하는 것처럼 말합니다.",
                9: "정색: 갑자기 주위가 조용해졌습니다. 가면들의 시선이 모두 당신을 향해 있는 것 같기도 합니다.",
                10: "조사: 누군가가 당신이 알고 있는 사람에 대한 정보를 넌지시 묻습니다."
            }
            b_name = "가면무도회"
        text_name = b_name 
        if option == "가면무도회":
            text_name = "가면무도회 복장"

        resp_text = responses[roll]
        parts = [part.strip() for part in resp_text.split(":")]
        resp_1 = parts[0]
        resp_2 = parts[1] if len(parts) > 1 else ""

        embed = discord.Embed(
            title=f"{interaction.user.display_name}이(가) {text_name}을(를) 하고 연회장에 들어갔어 쿠뽀!",
            description=f"*{roll}* → **{resp_1}**\n{resp_2}",
            colour=0x00FF00
        )
        try:
            for item in self.children:
                item.disabled = True
        except:
            pass

        await interaction.response.edit_message(view=self)
        await interaction.followup.send(embed=embed)
        # 로그 기록
        # await asyncio.to_thread(
        #     self.sheet_handler.dancing_log_sheet.append_row,
        #     [interaction.user.display_name, b_name, roll]
        # )

    async def on_timeout(self):
        try:
            for item in self.children:
                item.disabled = True
        except:
            pass

class FishingView(discord.ui.View):
    """낚시 바다 선택 View"""
    def __init__(self, sheet_handler):
        super().__init__(timeout=60)
        self.sheet_handler = sheet_handler

    @discord.ui.select(
        placeholder="낚시할 바다를 선택해주세요!",
        options=[
            discord.SelectOption(label="에메랄드빛 바다", value="에메랄드", emoji="💚"),
            discord.SelectOption(label="황금빛 바다", value="황금", emoji="💛"),
            discord.SelectOption(label="자수정빛 바다", value="자수정", emoji="💜")
        ],
    )
    async def fishing_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        option = select.values[0]
        roll = random_utils.randint(1, 10)

        if option == "에메랄드":
            responses = {
                1: "흐물흐물: 긴 해조류가 걸려 올라왔습니다.",
                2: "물보라: 큰 물고기가 튀어오르더니 물보라를 일으켰습니다! 피하거나 막지 못하면 물을 맞을지도 모릅니다.",
                3: "보물: 예쁜 빛갈의 돌멩이 또는 작은 보석이 박힌 조개껍질을 발견했습니다.",
                4: "민첩 겨루기: 움직임이 정말 재빠른 놈입니다! 민첩하게 낚싯대를 당기지 못하면 물고기가 미끼만 먹고 도망칠지도 모릅니다.",
                5: "평온: 잔잔한 물결과 함께 작은 물고기가 조용히 낚였습니다.",
                6: "무관심: 물고기들이 놀라울 정도로 당신이 던진 미끼에는 관심을 보이지 않습니다.",
                7: "어부지리: 새가 날아와 물고기를 낚아채 갔습니다.",
                8: "회귀: 아까부터 똑같이 생긴 물고기만 계속 낚이고 있습니다.",
                9: "매스게임: 큰 규모의 물고기들이 무리지어 헤엄쳐 지나가며 특이한 문양을 만들어 냅니다.",
                10: "고래의 인사: 멀리서 고래가 지느러미와 꼬리를 흔들며 지나가는 것이 보입니다. 마치 인사를 하는 것 같습니다."
            }
            b_name = "에메랄드빛 바다"
        elif option == "황금":
            responses = {
                1: "힘 겨루기: 힘이 정말 센 놈입니다! 낚싯대를 놓지 않고 계속 당기면 줄이 끊어지거나, 이쪽에서 끌려가 물에 빠질지도 모릅니다.",
                2: "상자: 꽉 닫힌 상자를 낚았습니다. 안에 뭐가 들어있을까요?",
                3: "언어: 물고기가 당신이 알아들을 수 있는 말로 한 마디를 건넵니다.",
                4: "휭: 소지품이나 근처에 있던 물건 가벼운 하나가 바람에 날아가 물에 빠졌습니다.",
                5: "몸통 박치기: 수면을 박차고 뛰어오른 물고기가 당신을 들이받으려 합니다!",
                6: "횡재: 먹어도 안전한 식용 해산물 하나가 걸렸습니다!",
                7: "대물: 먹어도 안전한 식용 해산물 하나가 걸렸습니다! 크기가 굉장합니다!",
                8: "해초: 낚싯줄이 해초에 걸렸습니다! 복잡하게 얽혔는지, 직접 가서 풀어야 할 것 같습니다!",
                9: "뭐야: 어떻게 봐도 물고기가 아닌 것이 낚였습니다. 바닷속에 이런 게 왜 있었을까요?",
                10: "상어의 순찰: 멀리서 상어가 지느러미와 꼬리를 흔들며 지나가는 것이 보입니다. 주변 물고기들이 모두 숨었습니다."
            }
            b_name = "황금빛 바다"
        elif option == "자수정":
            responses = {
                1: "해파리: 투명하고 거대한 해파리가 올라왔습니다. 촉수에 닿으면 마비독에 걸릴지도 모릅니다!",
                2: "촉수: 거대한 두족류의 촉수가 올라왔습니다. 당신을 휘감아 조이려고 합니다!",
                3: "집게: 거대한 갑각류의 집게발이 올라왔습니다. 당신을 물려고 합니다!",
                4: "투영: 물 속에서 언뜻 당신이 아는 사람의 얼굴이 비친 것 같습니다.",
                5: "독가시: 독가시를 가진 물고기가 올라왔습니다! 독침에 닿으면 중독될지도 모릅니다.",
                6: "사냥: 날카로운 이빨을 가진 물고기 떼가 당신을 포위합니다. 공격당하면 상처를 입을지도 모릅니다!",
                7: "시선: 깊은 곳에서 무언가가 당신을 응시하는 것만 같은 기분이 느껴집니다.",
                8: "그림자: 물에 비친 당신의 그림자가 어쩐지 당신보다 조금 느리게 움직이는 것이 보였습니다.",
                9: "줄행랑: 무언가로부터 도망치기라도 하듯 큰 규모의 물고기 떼가 빠르게 지나갔습니다. 주변이 고요해졌습니다.",
                10: "스켈레톤: 살점 없이 뼈만 남은 물고기가 올라왔습니다. 어째서인지 살아있는 것처럼 움직입니다."
            }
            b_name = "자수정빛 바다"

        resp_text = responses[roll]
        parts = [part.strip() for part in resp_text.split(":")]
        resp_1 = parts[0]
        resp_2 = parts[1] if len(parts) > 1 else ""

        embed = discord.Embed(
            title=f"{b_name}에서 \n{interaction.user.display_name}이(가) 무얼 낚아 올렸을까 쿠뽀?",
            description=f"*{roll}* → **{resp_1}**\n{resp_2}",
            colour=0x00FF00
        )
        try:
            for item in self.children:
                item.disabled = True
        except:
            pass

        await interaction.response.edit_message(view=self)
        await interaction.followup.send(embed=embed)
        # 로그 기록
        await asyncio.to_thread(
            self.sheet_handler.fishing_log_sheet.append_row,
            [interaction.user.display_name, b_name, roll]
        )

    async def on_timeout(self):
        try:
            for item in self.children:
                item.disabled = True
        except:
            pass


class ForbiddenView(discord.ui.View):
    """보존서고 위험도 선택 View"""
    def __init__(self, sheet_handler):
        super().__init__(timeout=60)
        self.sheet_handler = sheet_handler

    @discord.ui.select(
        placeholder="조심조심.... 어디에서 책을 고를거야 쿠뽀?",
        options=[
            discord.SelectOption(label="위험도1", value="danger1", emoji="❗"),
            discord.SelectOption(label="위험도2", value="danger2", emoji="⚠️"),
            discord.SelectOption(label="위험도3", value="danger3", emoji="☠️")
        ],
    )
    async def forbidden_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        option = select.values[0]
        f_dice = 10

        if option == "danger1":
            f_dice = 20
            forbidden_num = "1"
        elif option == "danger2":
            f_dice = 30
            forbidden_num = "2"
        elif option == "danger3":
            f_dice = 40
            forbidden_num = "3"

        responses = self._get_forbidden_responses()
        roll = random_utils.randint(1, f_dice)

        resp_text = responses[roll]
        parts = [part.strip() for part in resp_text.split(":")]
        resp_1 = parts[0]
        resp_2 = parts[1] if len(parts) > 1 else ""

        embed = discord.Embed(
            title=f"위험도 {forbidden_num}인 서고에서 \n{interaction.user.display_name}이(가) 책을 꺼냈어 쿠뽀!",
            description=f"*{roll}* → **{resp_1}**\n{resp_2}",
            colour=0x00FF00
        )
        try:
            for item in self.children:
                item.disabled = True
        except:
            pass

        await interaction.response.edit_message(view=self)
        await interaction.followup.send(embed=embed)
        # 로그 기록
        await asyncio.to_thread(
            self.sheet_handler.forbidden_log_sheet.append_row,
            [interaction.user.display_name, forbidden_num, roll]
        )


    async def on_timeout(self):
        try:
            for item in self.children:
                item.disabled = True
        except:
            pass

    @staticmethod
    def _get_forbidden_responses():
        """보존서고 응답 데이터"""
        return {
            1: "베스트셀러: 인기가 많았는지 유독 때가 타고 낡은 책을 발견했습니다.",
            2: "초이스 노벨: 당신이 주인공의 행동을 선택할 때마다 책의 내용이 바뀝니다.",
            3: "백과사전: 온갖 정보가 제각기 다른 글씨체로 쓰여 있습니다. 맨 뒷부분의 빈 공간에는 당신이 알고 있는 정보를 적어넣어도 될 것 같습니다.",
            4: "종언의 계시: 어느 세계의 종말을 예언하는 내용이 적혀 있습니다.",
            5: "필기 습관: 이전의 독자가 책을 읽으면서 써 놓은 듯한 메모를 발견했습니다.",
            6: "기밀 문서: 온통 백지로 되어 있는 책을 발견했습니다. 어떤 조치를 하면 내용이 나타날지도 모르겠습니다.",
            7: "비밀 편지: 누군가가 대상에게 은밀히 전하려고 했던 편지가 들어있습니다.",
            8: "잊힌 지식: 이름 없는 요리나 약 레시피, 또는 도구 설계도 또는 악보를 발견했습니다.",
            9: "특이 취향: 이 책에는 좀처럼 이해하기 힘든 누군가의 수필이 적혀 있습니다.",
            10: "사진첩: 어딘가의 풍경, 또는 어떤 인물이 사실처럼 정교하게 그려진 그림이 있는 책을 발견했습니다.",
            11: "소등: 조명이 깜박이더니 한순간 근처의 빛이 사라졌습니다. 일시적으로 주변이 캄캄해집니다.",
            12: "미로: 책꽂이 위치가 조금 전과 달라졌습니다. 길이 바뀌어 있습니다.",
            13: "속닥속닥: 책꽂이 너머에서 누군가가 속삭이는 듯한 소리가 들려 옵니다.",
            14: "그림자: 책장 건너편에서 검은 그림자가 어른거리는 것이 보였습니다.",
            15: "오물: 책 안쪽에 묻어  있던 정체를 알 수 없는 액체가 당신의 손에 묻었습니다.",
            16: "괴리감: 당신에게 필요했던 정보가 적혀 있나 싶었는데, 잘 읽어보니 어딘가 이상합니다.",
            17: "팔랑: 바람이 불지도 않았는데 책장이 저절로 넘어갔습니다.",
            18: "취급주의: 열지 못하도록 묶여 있고, 읽지 말라는 경고 표시가 수도 없이 붙어 있는 책을 발견했습니다.",
            19: "형언할 수 없는 삽화: 이 책에는 도저히 이해하기 힘든 그림이 그려져 있습니다. 불길하고 섬뜩한 느낌이 들지도 모릅니다.",
            20: "뜬소문: 당신이 알고 있는 사람들 중 누군가에 대해 몰랐던 정보가 적혀 있는 책을 발견했습니다.",
            21: "고통: 이 책을 보다 보니 어쩐지 타는 듯한, 또는 찌르는 듯한, 또는 차갑게 얼어붙는 고통이 느껴집니다.",
            22: "증강 현실: 어쩐지 책을 보고 있는 동안 이 책에 적힌 시각, 후각 또는 청각적 자극이 현실에 있는 것처럼 생생하게 느껴집니다.",
            23: "수면제: 이 책을 읽고 있다 보니 점점 나른해지고 눈이 감기는 것 같습니다.",
            24: "기시감: 이 책은 전에도 읽은 것 같습니다. 어쩌면 두 번…? 아니, 그보다 더…?",
            25: " 에너지 흡수: 이 책을 보다 보니 어쩐지 목이 마르고 피곤해지는 것만 같은 기분이 듭니다.",
            26: "에너지 충전: 이 책을 보고 있으니 왠지 기력이 돌고 힘이 붙는 듯한 기분이 듭니다.",
            27: "놓칠 수 없는: 이 책을 보다 보니 어쩐지 손에서 놓지 않고 끝까지 계속 읽고 싶어지는 충동이 듭니다.",
            28: "시간 삭제: 이 책을 보기 시작한 뒤로 시간이 예상보다 많이 지나가 있었습니다.",
            29: "감정 이입: 책 속에 등장하는 인물의  감정이 당신에게 생생하게 전해집니다.",
            30: "불길한 직감: 당장 책 찾기를 그만두고 이 장소에서 빠져나가야만 한다는 기분이 듭니다.",
            31: "책지피티: 당신이 지금 가장 듣고싶어 하는 말이 적혀 있는 책을 발견했습니다.",
            32: "해결의 열쇠: 당신이 고민하던 일이나 당신의 상황에 도움이 될 만한 조언이 적힌 책을 발견했습니다.",
            33: "잊을 수 없는: 이 책을 보다 보니 어쩐지 다시 떠올리고 싶지 않은 과거의 단편이 떠오릅니다.",
            34: "돌이킬 수 없는: 이 책을 보다 보니 어쩐지 후회되거나 죄책감이 느껴지는 과거의 단편이 떠오릅니다.",
            35: "돌아갈 수 없는: 이 책을 보다 보니 어쩐지 한때의 행복하거나 즐거웠던 기억, 혹은 지금보다 나았던 시절의 단편이 떠오릅니다.",
            36: "밝은 미래: 이 책을 보다 보니 어쩐지 자신이 가장 바라는 긍정적인 미래의 단편이 떠오릅니다.",
            37: "어두운 미래: 이 책을 보다 보니 어쩐지 자신에게 일어날 수 있는 가장 끔찍한 미래의 단편이 떠오릅니다.",
            38: "다른 길: 이 책을 보다 보니 당신이 과거의 어떤 시점에 다른 선택을 했다면 어떻게 되었을지 생각해보게 됩니다.",
            39: "동명이인: 이 책의 저자 이름이 당신과 같습니다.",
            40: "역사의 주인공: 당신이 실제로 겪었던 일이 쓰여 있는 책을 찾았습니다."
        }


class ForbiddenCheatView(discord.ui.View):
    """보존서고 책찾기 (치트) - 원하는 결과 선택 가능"""
    def __init__(self):
        super().__init__(timeout=60)
        self.responses = ForbiddenView._get_forbidden_responses()

    @discord.ui.select(
        placeholder="조심조심.... 어디에서 책을 고를거야 쿠뽀?",
        options=[
            discord.SelectOption(label="위험도1", value="danger1", emoji="❗"),
            discord.SelectOption(label="위험도2", value="danger2", emoji="⚠️"),
            discord.SelectOption(label="위험도3", value="danger3", emoji="☠️")
        ],
    )
    async def forbidden_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        option = select.values[0]

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="위험도를 선택했어, 쿠뽀! 이제 아래에서 원하는 결과를 골라봐, 쿠뽀!", view=self)

        if option == "danger1":
            followup_view = Danger1FollowupView(self.responses)
        elif option == "danger2":
            followup_view = Danger2FollowupView(self.responses)
        elif option == "danger3":
            followup_view = Danger3FollowupView(self.responses)

        await interaction.followup.send("어떤 책을 꺼낼지 선택해줘 쿠뽀!", view=followup_view, ephemeral=True)

    async def on_timeout(self):
        try:
            for item in self.children:
                item.disabled = True
        except:
            pass


class Danger1FollowupView(discord.ui.View):
    """위험도1 결과 선택 View (1-20)"""
    def __init__(self, responses):
        super().__init__(timeout=60)
        self.responses = responses

        options = []
        for i in range(1, 21):
            label_text = self.responses[i].split(":")[0]
            options.append(discord.SelectOption(label=f"{i}. {label_text}", value=str(i)))

        select = discord.ui.Select(placeholder="결과를 선택하세요.", options=options, custom_id="danger1_select")
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        selected_value = int(interaction.data['values'][0])

        resp_text = self.responses[selected_value]
        parts = [part.strip() for part in resp_text.split(":")]
        resp_1 = parts[0]
        resp_2 = parts[1] if len(parts) > 1 else ""

        embed = discord.Embed(
            title=f"위험도 1인 서고에서 \n{interaction.user.display_name}이(가) 책을 꺼냈어 쿠뽀!",
            description=f"*{selected_value}* → **{resp_1}**\n{resp_2}",
            colour=0x00FF00
        )

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(embed=embed)
        logger.info(f"보존서고책찾기: {interaction.user.display_name} - 위험도1 - {selected_value}")


class Danger2FollowupView(discord.ui.View):
    """위험도2 결과 선택 View (21-30)"""
    def __init__(self, responses):
        super().__init__(timeout=60)
        self.responses = responses

        options = []
        for i in range(21, 31):
            label_text = self.responses[i].split(":")[0]
            options.append(discord.SelectOption(label=f"{i}. {label_text}", value=str(i)))

        select = discord.ui.Select(placeholder="결과를 선택하세요.", options=options, custom_id="danger2_select")
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        selected_value = int(interaction.data['values'][0])

        resp_text = self.responses[selected_value]
        parts = [part.strip() for part in resp_text.split(":")]
        resp_1 = parts[0]
        resp_2 = parts[1] if len(parts) > 1 else ""

        embed = discord.Embed(
            title=f"위험도 2인 서고에서 \n{interaction.user.display_name}이(가) 책을 꺼냈어 쿠뽀!",
            description=f"*{selected_value}* → **{resp_1}**\n{resp_2}",
            colour=0x00FF00
        )

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(embed=embed)
        logger.info(f"보존서고책찾기: {interaction.user.display_name} - 위험도2 - {selected_value}")


class Danger3FollowupView(discord.ui.View):
    """위험도3 결과 선택 View (31-40)"""
    def __init__(self, responses):
        super().__init__(timeout=60)
        self.responses = responses

        options = []
        for i in range(31, 41):
            label_text = self.responses[i].split(":")[0]
            options.append(discord.SelectOption(label=f"{i}. {label_text}", value=str(i)))

        select = discord.ui.Select(placeholder="결과를 선택하세요.", options=options, custom_id="danger3_select")
        select.callback = self.select_callback
        self.add_item(select)

    async def select_callback(self, interaction: discord.Interaction):
        selected_value = int(interaction.data['values'][0])

        resp_text = self.responses[selected_value]
        parts = [part.strip() for part in resp_text.split(":")]
        resp_1 = parts[0]
        resp_2 = parts[1] if len(parts) > 1 else ""

        embed = discord.Embed(
            title=f"위험도 3인 서고에서 \n{interaction.user.display_name}이(가) 책을 꺼냈어 쿠뽀!",
            description=f"*{selected_value}* → **{resp_1}**\n{resp_2}",
            colour=0x00FF00
        )

        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(view=self)
        await interaction.followup.send(embed=embed)
        logger.info(f"보존서고책찾기: {interaction.user.display_name} - 위험도3 - {selected_value}")


# Cog 로드 함수
async def setup(bot: commands.Bot):
    await bot.add_cog(UtilityCommandsCog(bot, bot.sheet_handler)) # type: ignore