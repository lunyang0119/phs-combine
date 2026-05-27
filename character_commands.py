import discord
from discord import app_commands
from discord.ext import commands
# import random
from combat import random_utils
import asyncio
from typing import Optional, List, Dict, Any
import logging
from google_sheets_handler import SheetsHandler
import utils
import json
import numpy as np
import constants

logger = logging.getLogger(__name__)

JOB_STATS: Dict[str, Dict[str, Any]] = {
    "스트라이커": {
        "description": "강력한 물리 공격에 특화된 전사입니다.",
        "stats": {"physics": 16, "magic": 8, "agility": 10, "charm": 8}
    },
    "마테리아 위버": {
        "description": "마테리아를 사용한 마법으로 다방면에서 활용가능한 마법사입니다.",
        "stats": {"physics": 8, "magic": 16, "agility": 8, "charm": 10}
    },
    "솔져": {
        "description": "[NPC 전용] 신라의 엘리트 전사. 균형잡힌 능력치를 보유합니다.",
        "stats": {"physics": 16, "magic": 14, "agility": 12, "charm": 8},
        "is_npc_only": True
    },
    "턱스": {
        "description": "[NPC 전용] 신라 정보부 요원. 민첩과 매력이 특화되어 있습니다.",
        "stats": {"physics": 10, "magic": 14, "agility": 16, "charm": 14},
        "is_npc_only": True
    }
}

class JobSelect(discord.ui.Select):
    def __init__(self, sheet_handler: SheetsHandler):
        self.sheet_handler = sheet_handler

        options = [
            discord.SelectOption(
                label="스트라이커",
                value='스트라이커',
                description=JOB_STATS["스트라이커"]["description"]
            ),
            discord.SelectOption(
                label="마테리아 위버", 
                value='마테리아 위버',
                description=JOB_STATS["마테리아 위버"]["description"]
            )
        ]
        
        super().__init__(placeholder="직업을 선택해주세요.", options=options)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        user_id = interaction.user.id
        user_name = interaction.user.display_name
        selected_job = self.values[0]

        # 1. 중복 확인 (등록 전)
        if self.sheet_handler.get_char_data(str(user_id)) is not None:
            await interaction.followup.send(
                f"이미 등록된 캐릭터가 있습니다. 프로필을 확인하려면 `/내상태`를 이용하거나 아래 ID와 함께 관리자에게 문의해주세요.\ndiscord ID: {user_id}",
                ephemeral=True
            )
            return
        
        # 2. 직업 정보 검증
        job_info = JOB_STATS.get(selected_job)
        if job_info is None:
            await interaction.followup.send("❌ 선택한 직업 정보가 없습니다. 다시 시도해주세요.", ephemeral=True)
            return
        
        stats = job_info.get('stats')
        if stats is None:
            await interaction.followup.send("❌ 직업의 스탯 정보를 찾을 수 없습니다. 관리자에게 문의하세요.", ephemeral=True)
            return

        # 3. 캐릭터 데이터 생성
        max_hp = constants.BASE_HP + utils.apply_damage_rounding(stats['physics']) * constants.HP_PER_PHYSICS
        base_mp = 60 if selected_job == '마테리아 위버' or '턱스' else constants.BASE_MP
        max_mp = base_mp + utils.apply_damage_rounding(stats['magic']) * constants.MP_PER_MAGIC
        current_shop_points = 0

        # Google Sheets 컬럼 순서: discord_id, character_name, job, max_hp, max_mp,
        # physics, magic, agility, charm, materia_owned, bonus_points_left,
        # materia_inventory, current_hp, current_mp, current_shop_points
        new_char_row = [
            str(user_id),                      # discord_id
            user_name,                         # character_name
            selected_job,                      # job
            max_hp,                            # max_hp
            max_mp,                            # max_mp
            stats['physics'],                  # physics
            stats['magic'],                    # magic
            stats['agility'],                  # agility
            stats['charm'],                    # charm
            '없음',                             # materia_owned
            constants.STAT_BONUS_POINTS,       # bonus_points_left
            constants.MATERIA_LIST_DEFAULT,    # materia_inventory
            max_hp,                            # current_hp (초기값 = max_hp)
            max_mp,                             # current_mp (초기값 = max_mp)
            current_shop_points
        ]

        # 4. 등록 실행
        success = self.sheet_handler.add_new_character(new_char_row)

        if success:
            # 5. 등록 성공 확인 (선택적 - 디버깅용)
            verification = self.sheet_handler.get_char_data(str(user_id))
            if verification is None:
                logger.error(f"캐릭터 등록 성공했으나 캐시에서 조회 실패: {user_id}")
                await interaction.followup.send(
                    "⚠️ 캐릭터가 등록되었으나 캐시 동기화에 문제가 발생했습니다. `/시트갱신 option:g2cache`를 실행해주세요.",
                    ephemeral=True
                )
                return
            
            embed = discord.Embed(
                title="✅ 캐릭터 등록 완료!",
                description=f"**{user_name}**님이 **{selected_job}**(으)로 모험에 참가합니다.\n`/스탯분배` 명령어를 사용하여 보너스 포인트 10점을 분배해주세요.",
                color=discord.Color.green()
            )
            await interaction.followup.send(embed=embed, ephemeral=False) 
        else:
            await interaction.followup.send("❌ 캐릭터 등록 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.", ephemeral=True)

class JobSelectView(discord.ui.View):
    def __init__(self, sheet_handler: SheetsHandler):
        super().__init__(timeout=constants.ACTION_VIEW_TIMEOUT)
        self.add_item(JobSelect(sheet_handler))

class CharacterCog(commands.Cog):
    def __init__(self, bot: commands.Bot, sheet_handler: SheetsHandler):
        self.bot = bot
        self.sheet_handler = sheet_handler
        self.combat_cog = None  # CombatCog 참조 (나중에 설정됨)

    @app_commands.command(name="등록", description="새로운 캐릭터를 생성하고 시트에 등록합니다.")
    async def register(self, interaction: discord.Interaction):
        # 중복 등록을 한 번 더 확인
        if self.sheet_handler.get_char_data(str(interaction.user.id)) is not None:
            await interaction.response.send_message("이미 등록된 캐릭터가 있습니다. 자신의 상태를 확인하려면 `/내상태`를 이용해주세요.", ephemeral=True)
            return

        view = JobSelectView(self.sheet_handler)
        await interaction.response.send_message("직업을 선택해주세요.", view=view, ephemeral=True)        
    
    @app_commands.command(name="내상태", description="상태를 확인합니다.")
    async def 내상태(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user_id = str(interaction.user.id)
        char_data = self.sheet_handler.get_char_data(user_id)
        if char_data is None:
            await interaction.followup.send("캐릭터 정보를 찾을 수 없습니다.\n`/등록`을 먼저 진행하거나 `/시트갱신`으로 캐시를 갱신한 후 다시 시도해주세요.", ephemeral=True)
            return

        # 전투 중인지 확인
        combat_cog = self.bot.get_cog('CombatCog')
        in_combat = False
        combat_hp = None
        combat_mp = None

        if combat_cog and hasattr(combat_cog, 'active_battles'):
            channel_id = interaction.channel_id
            if channel_id in combat_cog.active_battles:
                battle_state = combat_cog.active_battles[channel_id]
                participants_cache = battle_state.get('participants_cache')

                if participants_cache is not None and user_id in participants_cache.index:
                    in_combat = True
                    combat_hp = int(participants_cache.loc[user_id, 'current_hp'])
                    combat_mp = int(participants_cache.loc[user_id, 'current_mp'])

        embed = discord.Embed(
            title=f"{char_data.get('character_name', '이름없음')}의 상태",
            description=f"직업: **{char_data.get('job', '백수')}** | 남은 보너스 포인트: **{char_data.get('bonus_points_left', 0)}** | 사용 가능한 매점 포인트: {char_data.get('current_shop_points', 0)}",
            color=discord.Color.red() if in_combat else discord.Color.dark_green()
        )
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.avatar)

        # HP, MP (전투 중이면 전투 캐시 사용)
        if in_combat:
            embed.add_field(name="HP", value=f"{combat_hp} / {char_data.get('max_hp', 0)}")
            embed.add_field(name="MP", value=f"{combat_mp} / {char_data.get('max_mp', 0)}")
        else:
            embed.add_field(name="HP", value=f"{char_data.get('current_hp', 0)} / {char_data.get('max_hp', 0)}")
            embed.add_field(name="MP", value=f"{char_data.get('current_mp', 0)} / {char_data.get('max_mp', 0)}")

        # 4대 스탯
        embed.add_field(name="근력", value=char_data.get('physics', 0), inline=True)
        embed.add_field(name="마법", value=char_data.get('magic', 0), inline=True)
        embed.add_field(name="민첩", value=char_data.get('agility', 0), inline=True)
        embed.add_field(name="매력", value=char_data.get('charm', 0), inline=True)

        # 마테리아
        embed.add_field(name="장착 마테리아", value=char_data.get('materia_owned', '없음'), inline=False)
        embed.set_footer(text=f"Discord ID: {user_id}")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="스탯분배", description="남은 보너스 포인트를 스탯에 투자합니다.")
    @app_commands.describe(
        stat="포인트를 투자할 스탯을 선택하세요.",
        points="투자할 포인트의 양을 입력하세요."
    )
    @app_commands.choices(stat=[
        app_commands.Choice(name="근력", value="근력"),
        app_commands.Choice(name="마법", value="마법"),
        app_commands.Choice(name="민첩", value="민첩"),
        app_commands.Choice(name="매력", value="매력"),
        app_commands.Choice(name="리셋", value="리셋"),
    ])
    async def distribute_stats(self, interaction: discord.Interaction, stat: app_commands.Choice[str], points: int):
        await interaction.response.defer(ephemeral=True)
        user_id = str(interaction.user.id)

        # 전투 중인지 확인
        combat_cog = self.bot.get_cog('CombatCog')
        if combat_cog and hasattr(combat_cog, 'active_battles'):
            for channel_id, battle_state in combat_cog.active_battles.items():
                participants_cache = battle_state.get('participants_cache')
                if participants_cache is not None and user_id in participants_cache.index:
                    await interaction.followup.send(
                        "❌ 전투 중에는 스탯을 분배할 수 없습니다. 전투 종료 후 다시 시도해주세요.",
                        ephemeral=True
                    )
                    return

        try:
            char_data = self.sheet_handler.get_char_data(user_id)
        except ValueError:
            char_data = None
        if char_data is None or char_data.empty:
            await interaction.followup.send("❌ 캐릭터 정보가 없습니다. 먼저 `/등록`을 해주세요.", ephemeral=True)
            return
        
            # 리셋 처리
        if stat.value == "리셋":
            current_job = char_data.get('job', '백수')
            job_info = JOB_STATS.get(current_job)
            
            if job_info is None:
                await interaction.followup.send("❌ 직업 정보를 찾을 수 없습니다. 관리자에게 문의하세요.", ephemeral=True)
                return
            
            default_stats = job_info.get('stats')
            if default_stats is None:
                await interaction.followup.send("❌ 직업의 기본 스탯 정보를 찾을 수 없습니다.", ephemeral=True)
                return

            # 기본 스탯으로 초기화
            new_physics = default_stats['physics']
            new_magic = default_stats['magic']
            new_agility = default_stats['agility']
            new_charm = default_stats['charm']

            # HP/MP 재계산 (솔져는 basehp 150, 마테리아 위버는 BASE_MP 100)
            base_hp = 150 if current_job == '솔져' else constants.BASE_HP
            new_max_hp = base_hp + utils.apply_damage_rounding(new_physics) * constants.HP_PER_PHYSICS
            base_mp = 60 if current_job == '마테리아 위버' or '턱스' else constants.BASE_MP
            new_max_mp = base_mp + utils.apply_damage_rounding(new_magic) * constants.MP_PER_MAGIC

            # 캐시 업데이트
            self.sheet_handler.characters_sheet_cache.at[user_id, 'physics'] = new_physics
            self.sheet_handler.characters_sheet_cache.at[user_id, 'magic'] = new_magic
            self.sheet_handler.characters_sheet_cache.at[user_id, 'agility'] = new_agility
            self.sheet_handler.characters_sheet_cache.at[user_id, 'charm'] = new_charm
            self.sheet_handler.characters_sheet_cache.at[user_id, 'max_hp'] = new_max_hp
            self.sheet_handler.characters_sheet_cache.at[user_id, 'max_mp'] = new_max_mp
            self.sheet_handler.characters_sheet_cache.at[user_id, 'bonus_points_left'] = constants.STAT_BONUS_POINTS

            # 봇 캐시를 시트에 덮어쓰기 (강제 동기화)
            try:
                df_to_write = self.sheet_handler.characters_sheet_cache.reset_index()
                df_to_write = df_to_write.replace({np.nan: None})
                data_to_write = [df_to_write.columns.values.tolist()] + df_to_write.values.tolist()
                
                self.sheet_handler.characters_sheet.clear()
                self.sheet_handler.characters_sheet.update(data_to_write, raw=False)
                logger.info(f"스탯 리셋 후 캐시 -> 시트 동기화 완료: {user_id}")
            except Exception as e:
                logger.error(f"스탯 리셋 후 시트 동기화 실패: {e}", exc_info=True)
                await interaction.followup.send("⚠️ 스탯은 리셋되었으나 시트 동기화에 실패했습니다. `/시트갱신 option:cache2g`를 실행해주세요.", ephemeral=True)
                return

            embed = discord.Embed(
                title="🔄 스탯 리셋 완료!",
                description=f"**{current_job}**의 기본 스탯으로 초기화되었습니다.\n보너스 포인트가 **{constants.STAT_BONUS_POINTS}**점으로 복구되었습니다.",
                color=discord.Color.blue()
            )
            embed.add_field(name="근력", value=f"`{new_physics}`", inline=True)
            embed.add_field(name="마법", value=f"`{new_magic}`", inline=True)
            embed.add_field(name="민첩", value=f"`{new_agility}`", inline=True)
            embed.add_field(name="매력", value=f"`{new_charm}`", inline=True)
            embed.add_field(name="최대 HP", value=f"`{new_max_hp}`", inline=True)
            embed.add_field(name="최대 MP", value=f"`{new_max_mp}`", inline=True)
            embed.set_footer(text="구글 시트에 자동으로 동기화되었습니다.")
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        
        #기존 스탯 분배 로직
        points_left = int(char_data.get('bonus_points_left', 0))
        if points <= 0:
            await interaction.followup.send("❌ 최소 1 이상의 포인트를 투자해야 합니다.", ephemeral=True)
            return
        if points > points_left:
            await interaction.followup.send(f"❌ 포인트가 부족합니다. 현재 남은 포인트: {points_left}", ephemeral=True)
            return

        stat_column_map = {
            '근력': 'physics',
            '마법': 'magic',
            '민첩': 'agility',
            '매력': 'charm'
        }

        target_stat_col = stat_column_map.get(stat.value)
        if not target_stat_col:
            await interaction.followup.send("❌ 유효하지 않은 스탯입니다.", ephemeral=True)
            return

        current_stat_value = int(char_data.get(target_stat_col, 0))
        new_stat_value = current_stat_value + points
        new_points_left = points_left - points

        # 캐시 업데이트 (update_char_stat은 캐시를 자동으로 업데이트함)
        self.sheet_handler.characters_sheet_cache.at[user_id, target_stat_col] = new_stat_value
        self.sheet_handler.characters_sheet_cache.at[user_id, 'bonus_points_left'] = new_points_left
        current_job = str(char_data.get('job', ''))

        # HP/MP는 업데이트된 스탯 값으로 재계산
        if target_stat_col == 'physics':
            base_hp = 150 if current_job == '솔져' else constants.BASE_HP
            new_max_hp = base_hp + utils.apply_damage_rounding(new_stat_value) * constants.HP_PER_PHYSICS
            self.sheet_handler.characters_sheet_cache.at[user_id, 'max_hp'] = new_max_hp
            self.sheet_handler.characters_sheet_cache.at[user_id, 'current_hp'] = new_max_hp
        elif target_stat_col == 'magic':
            base_mp = 60 if current_job == '마테리아 위버' or '턱스' else constants.BASE_MP
            new_max_mp = base_mp + utils.apply_damage_rounding(new_stat_value) * constants.MP_PER_MAGIC
            self.sheet_handler.characters_sheet_cache.at[user_id, 'max_mp'] = new_max_mp
            self.sheet_handler.characters_sheet_cache.at[user_id, 'current_mp'] = new_max_mp

        # ❌ 제거: 불필요한 전체 캐시 재로드
        # self.sheet_handler.update_all_force_google_to_cache()

        logger.info(f"캐시 업데이트 완료: {user_id}의 {target_stat_col} = {new_stat_value}")

        embed = discord.Embed(
            title="✅ 스탯 분배 완료!",
            description=f"**{stat.value}**에 **{points}** 포인트를 투자했습니다.",
            color=discord.Color.green()
        )
        embed.add_field(name=f"{stat.value} (변경 후)", value=f"`{new_stat_value}`", inline=True)
        embed.add_field(name="남은 포인트", value=f"`{new_points_left}`", inline=True)
        embed.add_field(name="Tip", value="`/시트갱신`명령어에서 봇 -> 시트를 사용하여 갱신하는 것을 추천드립니다.")
        
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="시트갱신", description="구글 시트와 봇의 캐시를 강제로 동기화합니다.")
    @app_commands.choices(option=[
        app_commands.Choice(name="구글시트 -> 봇 캐시 (g2cache)", value="g2cache"),
        app_commands.Choice(name="봇 캐시 -> 구글시트 (cache2g)", value="cache2g"),
    ])
    async def sync_sheet(self, interaction: discord.Interaction, option: app_commands.Choice[str]):
        await interaction.response.defer(ephemeral=True)

        if option.value == "g2cache":
            success = self.sheet_handler.update_all_force_google_to_cache()
            if success:
                await interaction.followup.send("✅ 구글 시트의 데이터를 봇 캐시로 성공적으로 덮어썼습니다.", ephemeral=True)
            else:
                await interaction.followup.send("❌ 동기화 중 오류가 발생했습니다.", ephemeral=True)
        
        elif option.value == "cache2g":
            try:
                # 1. Combat_Status 캐시 -> 시트
                if not self.sheet_handler.battle_stat_sheet_cache.empty:
                    self.sheet_handler.update_battle_status_cache_to_sheet()
                    combat_msg = "✅ Combat_Status 캐시를 시트에 반영했습니다.\n"
                else:
                    combat_msg = "⚠️ Combat_Status 캐시가 비어있어 건너뜁니다.\n"
                
                # 2. Characters 캐시 -> 시트
                if not self.sheet_handler.characters_sheet_cache.empty:
                    # Characters 캐시를 시트에 덮어쓰기
                    # 인덱스가 이미 리셋되어 있는지 확인
                    if 'discord_id' in self.sheet_handler.characters_sheet_cache.columns:
                        df_to_write = self.sheet_handler.characters_sheet_cache.copy()
                    else:
                        # 인덱스 이름 확인 및 보정
                        cache_df = self.sheet_handler.characters_sheet_cache.copy()
                        if cache_df.index.name != 'discord_id':
                            logger.warning(f"Characters 캐시 인덱스 이름이 '{cache_df.index.name}'로 되어있어 'discord_id'로 수정합니다.")
                            cache_df.index.name = 'discord_id'
                        df_to_write = cache_df.reset_index()  # discord_id를 컬럼으로
                    df_to_write = df_to_write.replace({np.nan: None})
                    
                    # 헤더 + 데이터
                    data_to_write = [df_to_write.columns.values.tolist()] + df_to_write.values.tolist()
                    
                    # Characters 시트 업데이트
                    self.sheet_handler.characters_sheet.clear()
                    self.sheet_handler.characters_sheet.update(data_to_write, raw=False)
                    
                    char_msg = "✅ Characters 캐시를 시트에 반영했습니다.\n"
                    logger.info("Characters 캐시 -> 구글 시트 동기화 완료")
                else:
                    char_msg = "⚠️ Characters 캐시가 비어있어 건너뜁니다.\n"
                
                # 결과 메시지
                embed = discord.Embed(
                    title="📤 캐시 → 구글 시트 동기화 완료",
                    description=combat_msg + char_msg,
                    color=discord.Color.green()
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                
            except Exception as e:
                logger.error(f"cache2g 동기화 중 오류 발생: {e}", exc_info=True)
                await interaction.followup.send(f"❌ 전투 캐시 동기화 중 오류가 발생했습니다: {e}", ephemeral=True)

    @app_commands.command(name="마테리아목록", description="소유한 마테리아 목록을 확인합니다.")
    async def 마테리아목록(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        user_id = str(interaction.user.id)
        char_data = self.sheet_handler.get_char_data(user_id)
        
        if char_data is None:
            await interaction.followup.send("❌ 캐릭터 정보를 찾을 수 없습니다.", ephemeral=True)
            return
        
        # JSON 파싱
        inventory_str = char_data.get('materia_inventory', '[]')
        try:
            inventory = json.loads(inventory_str) if inventory_str else []
        except json.JSONDecodeError:
            inventory = []
        
        equipped = char_data.get('materia_owned', '없음')
        embed = discord.Embed(
            title=f"{char_data.get('character_name', '???')}의 마테리아 인벤토리",
            color=discord.Color.blue()
        )
        embed.add_field(name="💎 장착 중", value=f"**{equipped}**", inline=False)
        
        if inventory:
            materia_list = "\n".join([f"• {m}" for m in inventory])
            embed.add_field(name="📦 보유 중", value=materia_list, inline=False)
        else:
            embed.add_field(name="📦 보유 중", value="*보유한 마테리아가 없습니다.*", inline=False)
        
        embed.set_footer(text="Tip: /마테리아 명령어로 마테리아를 장착/해제할 수 있습니다.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="마테리아", description="마테리아를 장착하거나 해제합니다.")
    async def materia(self, interaction: discord.Interaction):
        """통합 마테리아 관리 명령어: 장착/해제 선택 후 처리"""
        from view import MateriaActionView, MateriaSelectView

        await interaction.response.defer(ephemeral=True)
        user_id = str(interaction.user.id)
        char_data = self.sheet_handler.get_char_data(user_id)

        if char_data is None:
            await interaction.followup.send("❌ 캐릭터 정보를 찾을 수 없습니다.", ephemeral=True)
            return

        # 현재 장착 정보 및 인벤토리 파싱
        inventory_str = char_data.get('materia_inventory', '[]')
        try:
            inventory = json.loads(inventory_str) if inventory_str else []
        except json.JSONDecodeError:
            inventory = []

        current_equipped = char_data.get('materia_owned', '없음')

        # 액션 선택 (장착/해제)
        action_view = MateriaActionView()
        await interaction.followup.send("마테리아를 **장착**하시겠습니까, **해제**하시겠습니까?", view=action_view, ephemeral=True)
        await action_view.wait()

        if not action_view.action:
            await interaction.followup.send("⏱️ 시간이 초과되었습니다.", ephemeral=True)
            return

        # 장착 처리
        if action_view.action == 'equip':
            if not inventory:
                await interaction.followup.send("❌ 인벤토리에 마테리아가 없습니다.", ephemeral=True)
                return

            # 드롭다운으로 마테리아 선택
            select_view = MateriaSelectView(inventory=inventory)
            await interaction.followup.send("장착할 마테리아를 선택하세요:", view=select_view, ephemeral=True)
            await select_view.wait()

            if not select_view.selected_materia:
                await interaction.followup.send("⏱️ 선택 시간이 초과되었습니다.", ephemeral=True)
                return

            materia_name = select_view.selected_materia

            # 기존 장착 마테리아를 인벤토리로 복귀
            if current_equipped != '없음':
                inventory.append(current_equipped)

            # 새 마테리아 장착 (인벤토리에서 제거)
            inventory.remove(materia_name)
            new_inventory_str = json.dumps(inventory, ensure_ascii=False)

            # 캐시 먼저 업데이트
            self.sheet_handler.characters_sheet_cache.at[user_id, 'materia_owned'] = materia_name
            self.sheet_handler.characters_sheet_cache.at[user_id, 'materia_inventory'] = new_inventory_str

            # 시트 업데이트
            self.sheet_handler.update_char_stat(user_id, 'materia_owned', materia_name)
            self.sheet_handler.update_char_stat(user_id, 'materia_inventory', new_inventory_str)

            embed = discord.Embed(
                title="✅ 마테리아 장착 완료!",
                description=f"**{materia_name}**를 장착했습니다!",
                color=discord.Color.green()
            )
            if current_equipped != '없음':
                embed.add_field(name="교체됨", value=f"{current_equipped} → {materia_name}")

            await interaction.followup.send(embed=embed, ephemeral=True)

        # 해제 처리
        elif action_view.action == 'unequip':
            if current_equipped == '없음':
                await interaction.followup.send("❌ 장착 중인 마테리아가 없습니다.", ephemeral=True)
                return

            # 인벤토리로 복귀
            inventory.append(current_equipped)
            new_inventory_str = json.dumps(inventory, ensure_ascii=False)

            # 캐시 먼저 업데이트
            self.sheet_handler.characters_sheet_cache.at[user_id, 'materia_owned'] = '없음'
            self.sheet_handler.characters_sheet_cache.at[user_id, 'materia_inventory'] = new_inventory_str

            # 시트 업데이트
            self.sheet_handler.update_char_stat(user_id, 'materia_owned', '없음')
            self.sheet_handler.update_char_stat(user_id, 'materia_inventory', new_inventory_str)

            embed = discord.Embed(
                title="✅ 마테리아 해제 완료!",
                description=f"**{current_equipped}**를 해제하여 인벤토리로 복귀시켰습니다.",
                color=discord.Color.blue()
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

    # ===== 레거시 명령어 (하위 호환성 유지, /마테리아 사용 권장) =====
    # @app_commands.command(name="마테리아장착", description="인벤토리의 마테리아를 장착합니다.")
    # @app_commands.describe(materia_name="장착할 마테리아 이름")
    # async def equip_materia(self, interaction: discord.Interaction, materia_name: str):
    #     await interaction.response.defer(ephemeral=True)
    #     user_id = str(interaction.user.id)
    #     char_data = self.sheet_handler.get_char_data(user_id)
    #
    #     if char_data is None:
    #         await interaction.followup.send("❌ 캐릭터 정보를 찾을 수 없습니다.", ephemeral=True)
    #         return
    #
    #     # 인벤토리 확인
    #     inventory_str = char_data.get('materia_inventory', '[]')
    #     try:
    #         inventory = json.loads(inventory_str) if inventory_str else []
    #     except json.JSONDecodeError:
    #         inventory = []
    #
    #     if materia_name not in inventory:
    #         await interaction.followup.send(f"❌ '{materia_name}'를 보유하고 있지 않습니다.", ephemeral=True)
    #         return
    #
    #     # 기존 장착 마테리아를 인벤토리로 복귀
    #     current_equipped = char_data.get('materia_owned', '없음')
    #     if current_equipped != '없음':
    #         inventory.append(current_equipped)
    #
    #     # 새 마테리아 장착 (인벤토리에서 제거)
    #     inventory.remove(materia_name)
    #     new_inventory_str = json.dumps(inventory, ensure_ascii=False)
    #
    #     # 캐시 먼저 업데이트
    #     self.sheet_handler.characters_sheet_cache.at[user_id, 'materia_owned'] = materia_name
    #     self.sheet_handler.characters_sheet_cache.at[user_id, 'materia_inventory'] = new_inventory_str
    #
    #     # 시트 업데이트
    #     self.sheet_handler.update_char_stat(user_id, 'materia_owned', materia_name)
    #     self.sheet_handler.update_char_stat(user_id, 'materia_inventory', new_inventory_str)
    #
    #     embed = discord.Embed(
    #         title="✅ 마테리아 장착 완료!",
    #         description=f"**{materia_name}**를 장착했습니다!",
    #         color=discord.Color.green()
    #     )
    #     if current_equipped != '없음':
    #         embed.add_field(name="교체됨", value=f"{current_equipped} → {materia_name}")
    #
    #     await interaction.followup.send(embed=embed, ephemeral=True)

    # @app_commands.command(name="마테리아해제", description="장착 중인 마테리아를 해제합니다.")
    # async def unequip_materia(self, interaction: discord.Interaction):
    #     await interaction.response.defer(ephemeral=True)
    #     user_id = str(interaction.user.id)
    #     char_data = self.sheet_handler.get_char_data(user_id)
    #
    #     if char_data is None:
    #         await interaction.followup.send("❌ 캐릭터 정보를 찾을 수 없습니다.", ephemeral=True)
    #         return
    #
    #     current_equipped = char_data.get('materia_owned', '없음')
    #     if current_equipped == '없음':
    #         await interaction.followup.send("❌ 장착 중인 마테리아가 없습니다.", ephemeral=True)
    #         return
    #
    #     # 인벤토리로 복귀
    #     inventory_str = char_data.get('materia_inventory', '[]')
    #     try:
    #         inventory = json.loads(inventory_str) if inventory_str else []
    #     except json.JSONDecodeError:
    #         inventory = []
    #
    #     inventory.append(current_equipped)
    #     new_inventory_str = json.dumps(inventory, ensure_ascii=False)
    #
    #     # 캐시 먼저 업데이트
    #     self.sheet_handler.characters_sheet_cache.at[user_id, 'materia_owned'] = '없음'
    #     self.sheet_handler.characters_sheet_cache.at[user_id, 'materia_inventory'] = new_inventory_str
    #
    #     # 시트 업데이트
    #     self.sheet_handler.update_char_stat(user_id, 'materia_owned', '없음')
    #     self.sheet_handler.update_char_stat(user_id, 'materia_inventory', new_inventory_str)
    #
    #     embed = discord.Embed(
    #         title="✅ 마테리아 해제 완료!",
    #         description=f"**{current_equipped}**를 해제하여 인벤토리로 복귀시켰습니다.",
    #         color=discord.Color.blue()
    #     )
    #     await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="리스트", description="지정한 채널 또는 스레드의 모든 멤버 ID를 조회합니다.")
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.describe(
        channel_id="조회할 채널 또는 스레드의 ID (입력하지 않으면 현재 채널)",
        filter_type="조회 대상 필터"
    )
    @app_commands.choices(filter_type=[
        app_commands.Choice(name="전체", value="all"),
        app_commands.Choice(name="플레이어만", value="players"),
        app_commands.Choice(name="몬스터만", value="monsters"),
    ])
    async def list_members(self, interaction: discord.Interaction, channel_id: str = None, filter_type: app_commands.Choice[str] = None):
        await interaction.response.defer(ephemeral=True)
        
        selected_filter = filter_type.value if filter_type else "all"
        
        # 대상 채널 결정
        target_channel_id = int(channel_id) if channel_id else interaction.channel_id
        target_channel = self.bot.get_channel(target_channel_id)
        
        if not target_channel:
            await interaction.followup.send(f"❌ ID `{target_channel_id}`에 해당하는 채널을 찾을 수 없습니다.", ephemeral=True)
            return
        
        member_ids = []
        channel_name = target_channel.name if hasattr(target_channel, 'name') else str(target_channel_id)
        
        # 채널 타입에 따른 멤버 수집
        if isinstance(target_channel, discord.Thread):
            # 스레드 멤버 가져오기 (fetch_members()는 coroutine 반환)
            try:
                members = await target_channel.fetch_members()
                for member in members:
                    member_ids.append(str(member.id))
            except discord.errors.HTTPException:
                # 멤버를 가져올 수 없는 경우 (권한 문제 등)
                await interaction.followup.send("스레드 멤버를 가져올 수 없습니다. 봇에 필요한 권한이 있는지 확인해주세요.", ephemeral=True)
                return
            
            embed = discord.Embed(
                title=f"스레드 멤버 목록",
                description=f"**스레드**: {channel_name}\n**스레드 ID**: `{target_channel.id}`",
                color=discord.Color.purple()
            )
        else:
            # 일반 채널의 경우 길드 멤버 기준
            if hasattr(target_channel, 'guild'):
                for member in target_channel.guild.members:
                    member_ids.append(str(member.id))
            
            embed = discord.Embed(
                title=f"채널 멤버 목록",
                description=f"**채널**: {channel_name}\n**채널 ID**: `{target_channel.id}`",
                color=discord.Color.blue()
            )
        
        if not member_ids:
            embed.add_field(
                name="멤버 정보",
                value="*이 채널/스레드에 멤버가 없습니다.*",
                inline=False
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return
        
        # 필터링 및 정보 수집
        players_info = []
        monsters_info = []
        
        for member_id in member_ids:
            # 플레이어 확인
            if member_id.isdigit():
                char_data = self.sheet_handler.get_char_data(member_id)
                if char_data is not None:
                    char_name = char_data.get('character_name', '이름없음')
                    job = char_data.get('job', '백수')
                    
                    # Discord 유저 정보
                    try:
                        user = await self.bot.fetch_user(int(member_id))
                        discord_name = user.name
                    except:
                        discord_name = "알 수 없음"
                    
                    players_info.append({
                        'id': member_id,
                        'name': char_name,
                        'discord_name': discord_name,
                        'job': job
                    })
            else:
                # 몬스터 확인
                monster_data = self.sheet_handler.get_monster_data(member_id)
                if monster_data is not None:
                    monster_name = monster_data.get('monster_name', '이름없음')
                    monster_type = monster_data.get('type', 'normal')
                    
                    monsters_info.append({
                        'id': member_id,
                        'name': monster_name,
                        'type': monster_type
                    })
        
        # 필터 적용 및 표시
        if selected_filter in ["all", "players"] and players_info:
            player_text = ""
            for i, p in enumerate(players_info, 1):
                player_text += f"`{i}.` **{p['name']}** ({p['job']})\n"
                player_text += f"   ID: `{p['id']}`\n\n"
            
            # 긴 목록은 분할
            if len(player_text) > 1024:
                chunks = [player_text[i:i+1024] for i in range(0, len(player_text), 1024)]
                for idx, chunk in enumerate(chunks):
                    field_name = f"플레이어 ({len(players_info)}명)" if idx == 0 else f"플레이어 (계속 {idx+1})"
                    embed.add_field(name=field_name, value=chunk, inline=False)
            else:
                embed.add_field(
                    name=f"플레이어 ({len(players_info)}명)",
                    value=player_text or "*플레이어 없음*",
                    inline=False
                )
        
        if selected_filter in ["all", "monsters"] and monsters_info:
            monster_text = ""
            for i, m in enumerate(monsters_info, 1):
                type_icon = "👑" if m['type'] == 'boss' else "⚔️"
                monster_text += f"`{i}.` {type_icon} **{m['name']}**\n"
                monster_text += f"   ID: `{m['id']}`\n\n"
            
            if len(monster_text) > 1024:
                chunks = [monster_text[i:i+1024] for i in range(0, len(monster_text), 1024)]
                for idx, chunk in enumerate(chunks):
                    field_name = f"몬스터 ({len(monsters_info)}개)" if idx == 0 else f"몬스터 (계속 {idx+1})"
                    embed.add_field(name=field_name, value=chunk, inline=False)
            else:
                embed.add_field(
                    name=f"몬스터 ({len(monsters_info)}개)",
                    value=monster_text or "*몬스터 없음*",
                    inline=False
                )
        
        # 요약 정보
        summary = f"총 {len(players_info)}명의 플레이어, {len(monsters_info)}개의 몬스터"
        embed.set_footer(text=summary)
        
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="캐시확인", description="현재 봇 메모리에 저장된 캐시 데이터를 확인합니다.")
    @app_commands.describe(
        sheet_name="확인할 시트",
        user_id="특정 사용자 ID (선택사항)"
    )
    @app_commands.choices(sheet_name=[
        app_commands.Choice(name="캐릭터 (Characters)", value="characters"),
        app_commands.Choice(name="몬스터 (Monsters)", value="monsters"),
        app_commands.Choice(name="마테리아 목록 (Materia_List)", value="materia"),
        app_commands.Choice(name="보스 스킬 (Boss_Skills)", value="boss_skills"),
        app_commands.Choice(name="전투 상태 (Combat_Status)", value="combat"),
        app_commands.Choice(name="음악 (Musics)", value="music"),
    ])
    async def check_cache(self, interaction: discord.Interaction, sheet_name: app_commands.Choice[str], user_id: str = None):
        """현재 캐시 상태를 확인합니다."""
        await interaction.response.defer(ephemeral=True)
        
        cache_map = {
            "characters": self.sheet_handler.characters_sheet_cache,
            "monsters": self.sheet_handler.monsters_sheet_cache,
            "materia": self.sheet_handler.materia_list_sheet_cache,
            "boss_skills": self.sheet_handler.boss_skills_sheet_cache,
            "combat": self.sheet_handler.battle_stat_sheet_cache,
            "music": self.sheet_handler.ost_sheet_cache,
        }
        
        selected_cache = cache_map.get(sheet_name.value)
        
        if selected_cache is None or selected_cache.empty:
            await interaction.followup.send(f"❌ **{sheet_name.name}** 캐시가 비어있습니다.", ephemeral=True)
            return
        
        embed = discord.Embed(
            title=f"📊 {sheet_name.name} 캐시 상태",
            color=discord.Color.blue()
        )
        
        # 특정 사용자 조회
        if user_id:
            user_id = user_id.strip()
            if user_id in selected_cache.index:
                user_data = selected_cache.loc[user_id]
                
                # Series를 딕셔너리로 변환
                data_dict = user_data.to_dict()
                
                # 데이터를 필드로 추가
                field_count = 0
                current_field = ""
                for key, value in data_dict.items():
                    line = f"**{key}**: {value}\n"
                    
                    # Discord Embed 필드 길이 제한 (1024자)
                    if len(current_field) + len(line) > 1000:
                        embed.add_field(
                            name=f"데이터 ({field_count + 1})",
                            value=current_field,
                            inline=False
                        )
                        current_field = line
                        field_count += 1
                    else:
                        current_field += line
                
                # 남은 데이터 추가
                if current_field:
                    embed.add_field(
                        name=f"데이터 ({field_count + 1})" if field_count > 0 else "데이터",
                        value=current_field,
                        inline=False
                    )
                
                embed.set_footer(text=f"ID: {user_id}")
            else:
                await interaction.followup.send(f"❌ ID `{user_id}`를 캐시에서 찾을 수 없습니다.", ephemeral=True)
                return
        
        # 전체 캐시 요약
        else:
            total_count = len(selected_cache)
            
            # 캐시 컬럼 정보
            columns = list(selected_cache.columns)
            columns_text = ", ".join(columns[:10])  # 처음 10개만
            if len(columns) > 10:
                columns_text += f"... (총 {len(columns)}개)"
            
            embed.add_field(
                name="📈 통계",
                value=f"총 **{total_count}**개 항목",
                inline=False
            )
            
            embed.add_field(
                name="컬럼 목록",
                value=columns_text,
                inline=False
            )
            
            # 인덱스 샘플 (처음 10개)
            sample_indices = list(selected_cache.index[:10])
            sample_text = "\n".join([f"• `{idx}`" for idx in sample_indices])
            if total_count > 10:
                sample_text += f"\n... 외 {total_count - 10}개"
            
            embed.add_field(
                name="🔍 ID 샘플",
                value=sample_text if sample_text else "*없음*",
                inline=False
            )
            
            embed.set_footer(text="특정 항목을 확인하려면 user_id 매개변수를 사용하세요.")
        
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="스탯주사위", description="자신의 캐릭터의 스탯을 조회하여 주사위를 굴립니다.")
    @app_commands.choices(option=[
        app_commands.Choice(name="근력", value="physics"),
        app_commands.Choice(name="마법", value="magic"),
        app_commands.Choice(name="민첩", value="agility"),
        app_commands.Choice(name="매력", value="charm"),
    ])
    async def stat_die(self, interaction: discord.Interaction, option: app_commands.Choice[str] = None):
        """캐릭터의 스탯을 조회하여 주사위를 굴림(dnd)"""
        await interaction.response.defer(ephemeral=False)
        
        user_name = interaction.user.display_name
        user_id = interaction.user.id

        # 기본 Embed 생성
        embed = discord.Embed(
            title=f"🎲 {interaction.user.display_name}의 결과",
            color=discord.Color.blue()
        )

        selected = option.value if option else "physics"

        char_data = self.sheet_handler.get_char_data(str(user_id))

        if char_data is None:
            await interaction.followup.send("❌ 캐릭터 정보를 찾을 수 없습니다. 먼저 `/등록`을 해주세요.", ephemeral=True)
            return

        # 빠른 시작 가이드
        if selected == "physics":
            raw_stat = char_data.get('physics', 10)
            stat_name = "근력"
        elif selected == "magic":
            raw_stat = char_data.get('magic', 10)
            stat_name = "마법"
        elif selected == "agility":
            raw_stat = char_data.get('agility', 10)
            stat_name = "민첩"
        elif selected == "charm":
            raw_stat = char_data.get('charm', 10)
            stat_name = "매력"

        if (raw_stat - 10) % 2 == 1:
            initiative = (raw_stat - 11) / 2
        else:
            initiative = (raw_stat - 10) / 2

        die_die = random_utils.randint(1,10)
        
        embed = discord.Embed(
            title=f"🎲 {stat_name} 주사위 결과",
            description=f"{stat_name}보너스: {int(initiative)}\n주사위 값: {die_die}\n\n**최종 결과**\n→**{int(initiative+die_die)}**",
            color=discord.Color.green()
        )
        embed.set_author(name= user_name, icon_url=interaction.user.display_avatar)
        embed.set_footer(text=f"Discord ID: {user_id}")

        await interaction.followup.send(embed=embed, ephemeral=False)

    @app_commands.command(name="리셋", description="자신의 캐릭터 정보를 완전히 삭제합니다.")
    async def reset_character(self, interaction: discord.Interaction):
        """캐릭터 정보를 시트와 캐시에서 완전히 삭제"""
        user_id = str(interaction.user.id)

        # 1. 캐릭터 존재 확인
        char_data = self.sheet_handler.get_char_data(user_id)
        if char_data is None:
            await interaction.response.send_message(
                "❌ 삭제할 캐릭터 정보를 찾을 수 없습니다.\n`/등록`을 먼저 진행하거나 `/시트갱신`으로 캐시를 갱신한 후 다시 시도해주세요.",
                ephemeral=True
            )
            return

        # 2. 확인 Embed 및 View 표시
        from view import ConfirmDeleteView

        embed = discord.Embed(
            title="⚠️ 캐릭터 삭제 확인",
            description="현재 캐릭터의 정보가 모두 지워집니다. 그래도 괜찮으시겠습니까?",
            color=discord.Color.orange()
        )
        embed.add_field(
            name="캐릭터 정보",
            value=f"이름: **{char_data.get('character_name', '이름없음')}**\n직업: **{char_data.get('job', '백수')}**",
            inline=False
        )
        embed.set_footer(text="이 작업은 되돌릴 수 없습니다!")

        view = ConfirmDeleteView()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

        # 3. 사용자 응답 대기
        await view.wait()

        # 4. 확인 처리
        if view.confirmed:
            # 삭제 실행
            success = self.sheet_handler.delete_character(user_id)

            if success:
                success_embed = discord.Embed(
                    title="✅ 삭제 완료",
                    description="삭제완료 되었습니다. `/등록`을 사용하여 다시 만들어주세요!",
                    color=discord.Color.green()
                )
                await interaction.followup.send(embed=success_embed, ephemeral=True)
            else:
                error_embed = discord.Embed(
                    title="❌ 삭제 실패",
                    description="캐릭터 삭제 중 오류가 발생했습니다. 관리자에게 문의해주세요.",
                    color=discord.Color.red()
                )
                await interaction.followup.send(embed=error_embed, ephemeral=True)

    @app_commands.command(name="hp_mp_재계산", description="[관리자] 전체 캐릭터의 HP/MP를 근력/마법 스탯 기준으로 재계산합니다")
    @app_commands.default_permissions(manage_roles=True)
    async def recalculate_hp_mp(self, interaction: discord.Interaction):
        """관리자 전용: 모든 캐릭터의 max_hp와 max_mp를 현재 스탯 기준으로 재계산"""
        await interaction.response.defer(ephemeral=True)

        try:
            # 캐릭터 캐시 가져오기
            characters_cache = self.sheet_handler.characters_sheet_cache

            if characters_cache.empty:
                await interaction.followup.send("❌ 등록된 캐릭터가 없습니다.", ephemeral=True)
                return

            updated_count = 0
            update_log = []

            # 모든 캐릭터 순회
            for discord_id in characters_cache.index:
                char_data = characters_cache.loc[discord_id]

                # 현재 스탯 읽기
                physics = int(char_data.get('physics', 10))
                magic = int(char_data.get('magic', 10))
                job = str(char_data.get('job', ''))
                name = str(char_data.get('name', 'Unknown'))

                # HP 재계산 (솔져는 BASE_HP 150)
                base_hp = 150 if job == '솔져' else constants.BASE_HP
                new_max_hp = base_hp + utils.apply_damage_rounding(physics) * constants.HP_PER_PHYSICS

                # MP 재계산 (마테리아 위버와 턱스는 BASE_MP 100)
                base_mp = 100 if job in ['마테리아 위버', '턱스'] else constants.BASE_MP
                new_max_mp = base_mp + utils.apply_damage_rounding(magic) * constants.MP_PER_MAGIC

                # 캐시 업데이트 (max_hp, max_mp, current_hp, current_mp 모두 업데이트)
                self.sheet_handler.characters_sheet_cache.at[discord_id, 'max_hp'] = new_max_hp
                self.sheet_handler.characters_sheet_cache.at[discord_id, 'max_mp'] = new_max_mp
                self.sheet_handler.characters_sheet_cache.at[discord_id, 'current_hp'] = new_max_hp
                self.sheet_handler.characters_sheet_cache.at[discord_id, 'current_mp'] = new_max_mp

                updated_count += 1
                update_log.append(
                    f"**{name}** (근력 {physics}, 마법 {magic})\n"
                    f"  HP: **{new_max_hp}** | MP: **{new_max_mp}**"
                )

            # 변경사항이 없으면 종료
            if updated_count == 0:
                embed = discord.Embed(
                    title="✅ HP/MP 재계산 완료",
                    description="모든 캐릭터의 HP/MP가 이미 올바른 값입니다.\n변경된 캐릭터가 없습니다.",
                    color=discord.Color.green()
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            # 시트에 동기화
            try:
                df_to_write = self.sheet_handler.characters_sheet_cache.reset_index()
                df_to_write = df_to_write.replace({np.nan: None})
                data_to_write = [df_to_write.columns.values.tolist()] + df_to_write.values.tolist()

                self.sheet_handler.characters_sheet.clear()
                self.sheet_handler.characters_sheet.update(data_to_write, raw=False)
                logger.info(f"HP/MP 재계산 완료: {updated_count}명 업데이트")
            except Exception as e:
                logger.error(f"HP/MP 재계산 후 시트 동기화 실패: {e}", exc_info=True)
                await interaction.followup.send(
                    "⚠️ HP/MP는 재계산되었으나 시트 동기화에 실패했습니다.\n"
                    "`/시트갱신 option:cache2g`를 실행해주세요.",
                    ephemeral=True
                )
                return

            # 결과 리포트
            embed = discord.Embed(
                title="✅ HP/MP 재계산 완료!",
                description=f"**{updated_count}명**의 캐릭터 HP/MP가 업데이트되었습니다.",
                color=discord.Color.green()
            )

            # 변경 로그 (최대 10개까지만 표시)
            if update_log:
                log_text = "\n\n".join(update_log[:10])
                if len(update_log) > 10:
                    log_text += f"\n\n... 외 {len(update_log) - 10}명"
                embed.add_field(name="변경 내역", value=log_text, inline=False)

            embed.set_footer(text="구글 시트에 자동으로 동기화되었습니다.")
            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            logger.error(f"HP/MP 재계산 중 오류: {e}", exc_info=True)
            error_embed = discord.Embed(
                title="❌ 오류 발생",
                description=f"HP/MP 재계산 중 오류가 발생했습니다.\n```{str(e)}```",
                color=discord.Color.red()
            )
            await interaction.followup.send(embed=error_embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(CharacterCog(bot, bot.sheet_handler)) # type: ignore