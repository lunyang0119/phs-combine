import discord
from discord import app_commands
from discord.ext import commands
# import random
from combat import random_utils
import asyncio
import re
from google_sheets_handler import SheetsHandler
from typing import Optional, List, Dict, Any, Tuple
import logging
from view import ActionView, TargetSelectView, TurnStartView
from character_models import Player, Character
import utils
from dmw_system import DMWSystem
import json
import pandas as pd
import numpy as np
import constants

# 리팩토링: 유틸리티 함수 모듈화 (Phase 1, 2, 3, 4 완료)
from combat.combat_utils import CombatUtils
from combat.status_effect_manager import StatusEffectManager
from combat.boss_skill_system import BossSkillSystem
from combat.battle_manager import BattleManager

logger = logging.getLogger(__name__)


def parse_mentions(mention_str: str) -> List[str]:
    """멘션 문자열에서 Discord ID 추출

    Args:
        mention_str: 멘션 문자열 (예: "<@123456789> <@!987654321>")

    Returns:
        추출된 Discord ID 리스트
    """
    # <@123456789> 또는 <@!123456789> 형식 파싱
    pattern = r'<@!?(\d+)>'
    return re.findall(pattern, mention_str)


class CombatCog(commands.Cog):
    def __init__(self, bot: commands.Bot, sheet_handler: SheetsHandler):
        self.bot = bot
        self.sheet_handler = sheet_handler
        self.active_battles: Dict[int, Any] = {} # 채널 ID별로 전투상태 관리
        self.battle_locks: Dict[int, asyncio.Lock] = {} # 채널별 전투 루프 Lock

    async def _check_pause(
        self,
        battle_state: Dict[str, Any],
        channel: discord.abc.Messageable,
        channel_id: int,
        participant_id: str,
        is_player: bool
    ) -> None:
        """일시정지 상태 체크 및 대기

        Args:
            battle_state: 현재 전투 상태 딕셔너리
            channel: 메시지를 전송할 채널
            channel_id: 전투 채널 ID
            participant_id: 현재 참여자 ID
            is_player: 플레이어 턴 여부 (True: 플레이어, False: 몬스터)
        """
        paused_status = battle_state.get('paused', False)
        turn_type = "플레이어" if is_player else "몬스터"
        logger.info(f"[PAUSE_CHECK_{turn_type.upper()}] 채널 {channel_id}: {turn_type} 턴 시작 전 체크. paused={paused_status}, current_participant={participant_id}")

        if paused_status:
            logger.info(f"[PAUSE_TRIGGERED_{turn_type.upper()}] 채널 {channel_id}: 일시정지 감지! 대기 시작.")

            # pause_event가 set될 때까지 대기
            await battle_state['pause_event'].wait()
            logger.info(f"[PAUSE_RESUMED_{turn_type.upper()}] 채널 {channel_id}: 일시정지 해제됨. 전투 재개.")

    def _get_environment_penalty(self, battle_state: Dict[str, Any]) -> float:
        """환경 효과에 따른 회피율 페널티 계산

        Args:
            battle_state: 현재 전투 상태

        Returns:
            float: 회피율 페널티 (1.0 = 페널티 없음, 0.5 = 50% 감소)
        """
        environment_effect = battle_state.get('environment_effect')
        if environment_effect == 'gravity_anomaly':
            return constants.GRAVITY_EVASION_PENALTY
        return 1.0

    async def _apply_boss_keyword_effects(
        self,
        interaction: discord.Interaction,
        battle_state: Dict[str, Any]
    ) -> None:
        """보스 대화 키워드에 따라 몬스터 스탯 조정

        Args:
            interaction: Discord 인터랙션
            battle_state: 현재 전투 상태
        """
        keyword_counts = self.sheet_handler.get_boss_keyword_counts()
        if not keyword_counts:
            logger.info("Boss_Answer 시트가 비어있거나 없습니다. 키워드 효과를 적용하지 않습니다.")
            return

        participants_cache = battle_state['participants_cache']
        monster_ids = [m_id for m_id in participants_cache.index if not m_id.isdigit()]

        effect_messages = []

        # 좋은 키워드 처리 (디버프)
        for keyword, effect_data in constants.BOSS_KEYWORD_GOOD.items():
            count = keyword_counts.get(keyword, 0)
            if count == 0:
                continue

            effect_type = effect_data['type']
            description = effect_data['description']

            for monster_id in monster_ids:
                if effect_type == 'hp_reduce':
                    # HP 감소
                    max_hp = int(participants_cache.loc[monster_id, 'max_hp'])
                    current_hp = int(participants_cache.loc[monster_id, 'current_hp'])
                    reduce_amount = int(max_hp * effect_data['value'] * count)
                    new_hp = max(1, current_hp - reduce_amount)
                    participants_cache.loc[monster_id, 'current_hp'] = new_hp
                    participants_cache.loc[monster_id, 'max_hp'] = max(1, max_hp - reduce_amount)
                    effect_messages.append(f"**'{keyword}'** x{count} → {description} ({reduce_amount} HP 감소)")

                elif effect_type == 'physics_debuff':
                    # 공격력 감소
                    physics = int(participants_cache.loc[monster_id, 'physics'])
                    reduce_amount = int(physics * effect_data['value'] * count)
                    new_physics = max(1, physics - reduce_amount)
                    participants_cache.loc[monster_id, 'physics'] = new_physics
                    effect_messages.append(f"**'{keyword}'** x{count} → {description} (근력 {physics} → {new_physics})")

                elif effect_type == 'agility_debuff':
                    # 민첩 감소
                    agility = int(participants_cache.loc[monster_id, 'agility'])
                    reduce_amount = int(agility * effect_data['value'] * count)
                    new_agility = max(1, agility - reduce_amount)
                    participants_cache.loc[monster_id, 'agility'] = new_agility
                    effect_messages.append(f"**'{keyword}'** x{count} → {description} (민첩 {agility} → {new_agility})")

        # 나쁜 키워드 처리 (버프)
        for keyword, effect_data in constants.BOSS_KEYWORD_BAD.items():
            count = keyword_counts.get(keyword, 0)
            if count == 0:
                continue

            effect_type = effect_data['type']
            description = effect_data['description']

            for monster_id in monster_ids:
                if effect_type == 'physics_buff':
                    # 공격력 증가 (1턴)
                    physics = int(participants_cache.loc[monster_id, 'physics'])
                    buff_amount = int(physics * effect_data['value'] * count)
                    new_physics = physics + buff_amount
                    participants_cache.loc[monster_id, 'physics'] = new_physics
                    # 상태 이상에 버프 추가 (1턴)
                    current_status = str(participants_cache.loc[monster_id, 'status'])
                    if current_status in ['정상', 'nan', '', 'None']:
                        participants_cache.loc[monster_id, 'status'] = f'physics_buff:1'
                    else:
                        participants_cache.loc[monster_id, 'status'] = f'{current_status}|physics_buff:1'
                    effect_messages.append(f"**'{keyword}'** x{count} → {description} (근력 {physics} → {new_physics})")

                elif effect_type == 'rage_trigger':
                    # 분노 스킬 발동: physics 영구 증가
                    physics = int(participants_cache.loc[monster_id, 'physics'])
                    buff_amount = int(physics * effect_data.get('value', constants.BOSS_KEYWORD_RAGE_DEFAULT) * count)  # 기본 30%
                    new_physics = physics + buff_amount
                    participants_cache.loc[monster_id, 'physics'] = new_physics

                    # 상태에 분노 플래그 추가 (영구)
                    current_status = str(participants_cache.loc[monster_id, 'status'])
                    rage_tag = f'rage:999'
                    if current_status in ['정상', 'nan', '', 'None']:
                        participants_cache.loc[monster_id, 'status'] = rage_tag
                    else:
                        participants_cache.loc[monster_id, 'status'] = f'{current_status}|{rage_tag}'

                    effect_messages.append(
                        f"**'{keyword}'** x{count} → {description} "
                        f"(근력 {physics} → {new_physics}, 영구)"
                    )

                elif effect_type == 'crit_buff':
                    # 크리티컬 확률 버프 (영구)
                    crit_increase = int(effect_data['value'] * 100 * count)  # 0.05 * 100 = 5%

                    # 상태에 크리티컬 버프 추가
                    current_status = str(participants_cache.loc[monster_id, 'status'])
                    crit_tag = f'crit_buff:{crit_increase}'  # crit_buff:5
                    if current_status in ['정상', 'nan', '', 'None']:
                        participants_cache.loc[monster_id, 'status'] = crit_tag
                    else:
                        participants_cache.loc[monster_id, 'status'] = f'{current_status}|{crit_tag}'

                    effect_messages.append(
                        f"**'{keyword}'** x{count} → {description} "
                        f"(크리티컬 확률 +{crit_increase}%, 영구)"
                    )

        # 효과 메시지 출력
        if effect_messages:
            embed = discord.Embed(
                title="📢 플레이어의 대화가 보스에게 영향을 미쳤다!",
                description="\n".join(set(effect_messages)),  # 중복 제거
                color=discord.Color.gold()
            )
            await interaction.channel.send(embed=embed)
            await asyncio.sleep(1.5)  # 1.5초 대기

    async def _handle_round_end(
        self,
        interaction: discord.Interaction,
        battle_state: Dict[str, Any],
        channel_id: int
    ) -> None:
        """라운드 종료 처리 (환경 효과, 동기화, 순서 갱신, 보스 스킬 체크)
        Args:
            interaction: Discord 인터랙션 객체
            battle_state: 현재 전투 상태 딕셔너리
            channel_id: 전투 채널 ID
        """
        order_infos = battle_state['order_infos']
        participants_cache = battle_state['participants_cache']

        embed = discord.Embed(
            title=f"제{battle_state['order_count']}턴 종료."
        )
        await interaction.channel.send(embed=embed)

        # ===== 보스 스킬 트리거 체크 (Phase 1.1) =====
        await BossSkillSystem.check_boss_skill_triggers(interaction, battle_state, sheet_handler=self.sheet_handler)

        # ===== status duration 업데이트는 각 참여자의 턴 시작 시 처리됨 (Phase 1.4) =====
        # (라운드 종료 시 중복 감소 방지를 위해 여기서는 처리하지 않음)

        # ===== 공생 메커니즘 (Phase 2.1 + 2.2) =====
        await StatusEffectManager.check_symbiosis(interaction, battle_state)

        # ===== 전투불능 카운트다운 (플레이어 전용) =====
        player_ids = [p_id for p_id in participants_cache.index if p_id.isdigit()]
        revived_players = []
        for p_id in player_ids:
            is_dead = int(participants_cache.loc[p_id, 'is_dead'])
            if is_dead > 0:
                new_is_dead = max(0, is_dead - 1)
                participants_cache.loc[p_id, 'is_dead'] = new_is_dead

                player_name = participants_cache.loc[p_id, 'name']
                if new_is_dead == 0:
                    # 부활! HP를 1로 설정
                    participants_cache.loc[p_id, 'current_hp'] = 1
                    revived_players.append(player_name)
                    await interaction.channel.send(f"**{player_name}**이(가) 의식을 되찾았다! (HP 1로 부활)")
                else:
                    # 아직 전투불능 상태
                    await interaction.channel.send(f"**{player_name}**의 전투불능 카운트: {new_is_dead}턴 남음")

        # 환경 효과 적용 (턴 종료 시)
        environment_effect = battle_state.get('environment_effect')
        if environment_effect == 'lava_zone':
            # 마황 지대: 모든 참여자 HP 5% 감소
            damage_list = []
            for p_id in list(participants_cache.index):
                if int(participants_cache.loc[p_id, 'current_hp']) > 0:
                    max_hp = int(participants_cache.loc[p_id, 'max_hp'])
                    damage = max(1, int(max_hp * constants.LAVA_DAMAGE_PERCENT))
                    current_hp = int(participants_cache.loc[p_id, 'current_hp'])
                    new_hp = max(0, current_hp - damage)
                    CombatUtils.update_participant_hp(battle_state, p_id, new_hp)
                    name = participants_cache.loc[p_id, 'name']
                    damage_list.append(f"{name} -{damage} HP ({new_hp}/{max_hp})")

            if damage_list:
                damage_text = " | ".join(damage_list)
                await interaction.channel.send(f"**마황 지대 피해** (최대 HP의 {int(constants.LAVA_DAMAGE_PERCENT * 100)}%)\n{damage_text}")

        # 홀수 라운드가 끝날 때마다 동기화
        if battle_state['order_count'] % constants.SYNC_INTERVAL == 1:
            self.sheet_handler.update_battle_status_cache_to_sheet()
            await interaction.channel.send("`[SYSTEM] 데이터 동기화 완료.`", delete_after=3)

        # 다음 라운드로
        battle_state['current_turn_index'] = 0
        battle_state['order_count'] += 1

        # 시간 왜곡 환경 효과 시 순서 재결정
        if environment_effect == 'time_warp':
            BattleManager.determine_turn_order(battle_state, force_shuffle=True)  # 수정된 부분
            new_order_names = [battle_state['participants_cache'].loc[p_id, 'name'] for p_id in battle_state['order_infos']]
            await interaction.channel.send(f"⏰ **시간 왜곡 발동!** 행동 순서가 뒤섞입니다...\n새 순서: {' → '.join(new_order_names)}")

    async def apply_monster_special_pattern(self, monster_id: str, monster_data: Any, battle_state: Dict, channel: discord.abc.Messageable) -> Optional[Dict]:
        """
        몬스터 특수 패턴 처리 (rage_mode, charge_attack, steal_mp, counter_stance)

        Returns:
            패턴이 발동된 경우 행동 정보 딕셔너리, 발동되지 않은 경우 None
        """
        special_pattern = str(monster_data.get('special_pattern', 'none')).strip()
        if special_pattern == 'none' or not special_pattern:
            return None

        participants_cache = battle_state['participants_cache']
        current_hp = int(monster_data.get('current_hp', 0))
        max_hp = int(monster_data.get('max_hp', 1))
        hp_percent = (current_hp / max_hp) * 100
        turn_count = battle_state.get('order_count', 1)

        # 1. Rage Mode: HP 50% 이하 시 공격력 1.5배
        if special_pattern == 'rage_mode' and hp_percent <= constants.RAGE_MODE_HP_THRESHOLD:
            embed = discord.Embed(
                title="🔥 분노 모드 발동!",
                description=f"**{monster_data.get('name')}**의 공격력이 {constants.RAGE_MODE_DAMAGE_MULTIPLIER}배로 증가합니다!",
                color=discord.Color.dark_red()
            )
            await channel.send(embed=embed)
            return None

        # 2. Charge Attack: 3턴마다 전체 공격 (2배 데미지)
        elif special_pattern == 'charge_attack' and turn_count % constants.CHARGE_ATTACK_INTERVAL == 0:
            return {'action': 'charge_attack'}

        # 3. Steal MP: 30% 확률로 MP 흡수
        elif special_pattern == 'steal_mp' and random_utils.get_random() < constants.MP_STEAL_CHANCE:
            return {'action': 'steal_mp'}

        # 4. Counter Stance: 패시브 (피격 시 반격) - 피격 처리에서 확인
        elif special_pattern == 'counter_stance':
            return None

        return None


    @app_commands.command(name="전투시작", description="전투를 시작합니다.")
    @app_commands.describe(
        players="참여할 플레이어들을 멘션하세요 (예: @유저1 @유저2)",
        monsters="몬스터 ID를 쉼표로 구분하여 입력하세요 (예: monster_id1, monster_id2)",
        m_no="음악의 번호를 입력하세요",
        difficulty="난이도를 선택하세요 (쉬움/보통/어려움/극악)"
    )
    @app_commands.choices(difficulty=[
        app_commands.Choice(name="쉬움", value="쉬움"),
        app_commands.Choice(name="보통", value="보통"),
        app_commands.Choice(name="어려움", value="어려움"),
        app_commands.Choice(name="극악", value="극악")
    ])
    async def start_battle(self, interaction: discord.Interaction, players: str, monsters: str, m_no: int = 1, difficulty: str = "보통") -> None:
        await interaction.response.defer()

        channel_id = interaction.channel_id

        # 플레이어 멘션 파싱
        player_ids = parse_mentions(players)
        if not player_ids:
            await interaction.followup.send("❌ 최소 1명의 플레이어를 멘션해주세요. (예: @유저1 @유저2)")
            return

        # 몬스터 ID 파싱
        monster_ids = [m_id.strip() for m_id in monsters.split(',') if m_id.strip()]
        if not monster_ids:
            await interaction.followup.send("❌ 최소 1개의 몬스터 ID를 입력해주세요. (예: monster_id1, monster_id2)")
            return

        # 참여자 ID 목록 생성 (플레이어 + 몬스터)
        participant_ids = player_ids + monster_ids
        
        # 1. sheet_handler에서 전투 데이터를 준비합니다.
        success = self.sheet_handler.prepare_battle(participant_ids)
        if not success:
            await interaction.followup.send("전투를 시작하는 데 실패했습니다. ID를 확인해주세요.")
            return

        # 2. 준비된 캐시를 현재 채널의 battle_state에 저장
        if channel_id is None:
            raise ValueError("channel_id cannot be None")

        # 2. BattleManager로 전투 상태 초기화 (Phase 4)
        battle_state = BattleManager.initialize_battle_state(
            self.sheet_handler.battle_stat_sheet_cache,
            difficulty
        )
        self.active_battles[channel_id] = battle_state

        # 2.5. 플레이어 수에 따른 몬스터 스탯 자동 조정 (Phase 4)
        player_count, stat_multiplier = BattleManager.adjust_monster_stats_by_player_count(
            battle_state,
            difficulty
        )

        # 3. BattleManager로 turn order 결정 (Phase 4)
        order_infos = BattleManager.determine_turn_order(battle_state)
        
        order_infos_names = []
        for p_id in order_infos:
            participant_data = self.active_battles[channel_id]['participants_cache'].loc[p_id]
            name = participant_data.get('name')
            order_infos_names.append(name)
        
        # 4. 구글 시트에 현재 상태를 1차 반영
        self.sheet_handler.update_battle_status_cache_to_sheet()

        # 5. 디버그 모드 확인 및 전투루프
        if constants.DEBUG_MODE:
            title = "⚙️ [디버그 모드] Activating Combat Mode"
            description = f"**🔧 모든 DMW가 자동 성공합니다!**\n\n**행동 순서**: {' -> '.join(order_infos_names)}"
            color = discord.Color.orange()
        else:
            title = "Activating Combat Mode"
            description = f"**행동 순서**: {' -> '.join(order_infos_names)}"
            color = discord.Color.red()

        embed = discord.Embed(
            title=title,
            description=description,
            color=color
        )

        # 난이도 및 스탯 조정 정보 표시 (Phase 2.3)
        difficulty_emoji = {
            "쉬움": "🟢",
            "보통": "🟡",
            "어려움": "🔴",
            "극악": "💀"
        }
        # embed.add_field(
        #     name="난이도 설정",
        #     value=f"{difficulty_emoji.get(difficulty, '🟡')} **{difficulty}** (플레이어 {player_count}명 / 적 스탯 배율 {stat_multiplier:.2f}x)",
        #     inline=False
        # )

        # 환경 효과 표시
        environment_effect = battle_state.get('environment_effect')
        if environment_effect:
            env_descriptions = {
                'lava_zone': '☢️ **마황 지대**\n매 턴 종료 시 모든 참여자의 HP가 5% 감소합니다.',
                'mana_storm': '⚡ **라이프 스트림 폭풍**\n매 턴 시작 시 모든 참여자의 MP가 3씩 회복됩니다.',
                'gravity_anomaly': '🌀 **중력 이상**\n회피 확률이 50% 감소합니다.',
                'time_warp': '⏰ **시간 왜곡**\n민첩 스탯이 무시되고 행동 순서가 매 턴 무작위로 결정됩니다.'
            }
            embed.add_field(
                name="환경 효과 발동!",
                value=env_descriptions.get(environment_effect, '알 수 없는 효과'),
                inline=False
            )

        yt_url = ""
        raw_url_data = None
        m_no_str = str(m_no)
        if m_no_str != "0" :
            raw_url_data = self.sheet_handler.yt_url_extract(m_no)
        else:
            rand_no = random_utils.randint(2,self.sheet_handler.ost_sheet.row_count)
            raw_url_data = self.sheet_handler.yt_url_extract(rand_no)

        if raw_url_data and isinstance(raw_url_data, str):
            yt_url = raw_url_data

        await interaction.followup.send(embed=embed)

        # yt_url이 유효한 문자열일 경우에만 메시지로 전송
        if yt_url:
            await interaction.followup.send(yt_url)

        await self.battle_loop(interaction)

    @app_commands.command(name="보스전투", description="DMW 당첨률 2배")
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.describe(
        players="참여할 플레이어들을 멘션하세요 (예: @유저1 @유저2)",
        monsters="몬스터 ID를 쉼표로 구분하여 입력하세요 (예: boss_id1, monster_id2)",
        m_no="음악의 번호를 입력하세요",
        difficulty="난이도를 선택하세요 (쉬움/보통/어려움/극악)"
    )
    @app_commands.choices(difficulty=[
        app_commands.Choice(name="쉬움", value="쉬움"),
        app_commands.Choice(name="보통", value="보통"),
        app_commands.Choice(name="어려움", value="어려움"),
        app_commands.Choice(name="극악", value="극악")
    ])
    async def start_boss_battle(self, interaction: discord.Interaction, players: str, monsters: str, m_no: int = 1, difficulty: str = "보통") -> None:
        await interaction.response.defer()

        channel_id = interaction.channel_id

        # 플레이어 멘션 파싱
        player_ids = parse_mentions(players)
        if not player_ids:
            await interaction.followup.send("❌ 최소 1명의 플레이어를 멘션해주세요. (예: @유저1 @유저2)")
            return

        # 몬스터 ID 파싱
        monster_ids = [m_id.strip() for m_id in monsters.split(',') if m_id.strip()]
        if not monster_ids:
            await interaction.followup.send("❌ 최소 1개의 몬스터 ID를 입력해주세요. (예: boss_id1, monster_id2)")
            return

        # 참여자 ID 목록 생성 (플레이어 + 몬스터)
        participant_ids = player_ids + monster_ids

        # 1. sheet_handler에서 전투 데이터를 준비합니다.
        success = self.sheet_handler.prepare_battle(participant_ids)
        if not success:
            await interaction.followup.send("전투를 시작하는 데 실패했습니다. ID를 확인해주세요.")
            return

        # 2. 준비된 캐시를 현재 채널의 battle_state에 저장
        if channel_id is None:
            raise ValueError("channel_id cannot be None")

        # 2. BattleManager로 전투 상태 초기화 (보스전투 모드 활성화)
        battle_state = BattleManager.initialize_battle_state(
            self.sheet_handler.battle_stat_sheet_cache,
            difficulty,
            boss_mode=True  # 보스전투 모드 플래그
        )
        self.active_battles[channel_id] = battle_state

        # 2.5. 플레이어 수에 따른 몬스터 스탯 자동 조정
        player_count, stat_multiplier = BattleManager.adjust_monster_stats_by_player_count(
            battle_state,
            difficulty
        )

        # 2.6. 보스 대화 키워드 효과 적용
        await self._apply_boss_keyword_effects(interaction, battle_state)

        # 3. BattleManager로 turn order 결정
        order_infos = BattleManager.determine_turn_order(battle_state)

        order_infos_names = []
        for p_id in order_infos:
            participant_data = self.active_battles[channel_id]['participants_cache'].loc[p_id]
            name = participant_data.get('name')
            order_infos_names.append(name)

        # 4. 구글 시트에 현재 상태를 1차 반영
        self.sheet_handler.update_battle_status_cache_to_sheet()

        # 5. 보스전투 모드 임베드
        title = "Activating Combat Mode"
        description = f"**DMW 당첨 확률 2배!**\n\n**행동 순서**: {' -> '.join(order_infos_names)}"
        color = discord.Color.dark_red()

        embed = discord.Embed(
            title=title,
            description=description,
            color=color
        )

        # 환경 효과 표시
        environment_effect = battle_state.get('environment_effect')
        if environment_effect:
            env_descriptions = {
                'lava_zone': '☢️ **마황 지대**\n매 턴 종료 시 모든 참여자의 HP가 5% 감소합니다.',
                'mana_storm': '⚡ **라이프 스트림 폭풍**\n매 턴 시작 시 모든 참여자의 MP가 3씩 회복됩니다.',
                'gravity_anomaly': '🌀 **중력 이상**\n회피 확률이 50% 감소합니다.',
                'time_warp': '⏰ **시간 왜곡**\n민첩 스탯이 무시되고 행동 순서가 매 턴 무작위로 결정됩니다.'
            }
            embed.add_field(
                name="환경 효과 발동!",
                value=env_descriptions.get(environment_effect, '알 수 없는 효과'),
                inline=False
            )

        yt_url = ""
        raw_url_data = None
        m_no_str = str(m_no)
        if m_no_str != "0":
            raw_url_data = self.sheet_handler.yt_url_extract(m_no)
        else:
            rand_no = random_utils.randint(2, self.sheet_handler.ost_sheet.row_count)
            raw_url_data = self.sheet_handler.yt_url_extract(rand_no)

        if raw_url_data and isinstance(raw_url_data, str):
            yt_url = raw_url_data

        await interaction.followup.send(embed=embed)

        # yt_url이 유효한 문자열일 경우에만 메시지로 전송
        if yt_url:
            await interaction.followup.send(yt_url)

        await self.battle_loop(interaction)

    @app_commands.command(name="난입", description="진행 중인 전투에 난입합니다.")
    @app_commands.describe(intruder_id="난입할 캐릭터 또는 몬스터의 ID")
    async def join_battle(self, interaction: discord.Interaction, intruder_id: str) -> None:
        await interaction.response.defer()
        channel_id = interaction.channel_id
        intruder_id = intruder_id.strip()

        if channel_id not in self.active_battles:
            await interaction.followup.send("이 채널에서 진행 중인 전투가 없습니다.")
            return

        battle_state = self.active_battles[channel_id]
        if intruder_id in battle_state['participants_cache'].index:
            await interaction.followup.send(f"'{intruder_id}'는 이미 전투에 참여하고 있습니다.")
            return

        # 핸들러를 통해 새 참여자를 캐시에 추가
        new_participant = self.sheet_handler.add_participant_to_battle(intruder_id)
        if new_participant is None:
            await interaction.followup.send(f"ID '{intruder_id}'에 해당하는 캐릭터나 몬스터를 찾을 수 없습니다.")
            return

        # 전투 상태 업데이트
        battle_state['participants_cache'] = self.sheet_handler.battle_stat_sheet_cache
        battle_state['pending_intruders'].append(intruder_id)

        # 구글 시트 동기화
        self.sheet_handler.update_battle_status_cache_to_sheet()

        intruder_name = new_participant.get('name', '나나시')
        await interaction.followup.send(f"**{intruder_name}** (이)가 전투에 난입했습니다! 다음 턴부터 행동할 수 있습니다.")

    @app_commands.command(name="전투종료", description="현재 채널의 전투를 강제로 종료합니다.")
    async def end_battle(self, interaction: discord.Interaction):
        """현재 채널의 전투를 강제로 종료합니다."""
        await interaction.response.defer(ephemeral=False)

        channel_id = interaction.channel_id
        if channel_id in self.active_battles:
            # 보스 스킬 초기화 추가
            battle_state = self.active_battles[channel_id]
            participants_cache = battle_state['participants_cache']

            # 현재 활성화된 View가 있다면 강제로 중단
            if 'active_view' in battle_state and battle_state['active_view'] is not None:
                try:
                    battle_state['active_view'].stop()
                    logger.info(f"채널 {channel_id}: 활성 View 중단 완료")
                except Exception as e:
                    logger.warning(f"채널 {channel_id}: View 중단 실패 - {e}")

            # 보스 ID 찾기
            for participant_id in participants_cache.index:
                monster_type = str(participants_cache.loc[participant_id, 'monster_type']).lower()
                if monster_type == 'boss':
                    self.sheet_handler.reset_boss_skills(participant_id)
                    logger.info(f"보스 '{participant_id}' 스킬 초기화 완료")

            # Combat_Status 시트 초기화 (헤더만 남기고 모두 삭제)
            try:
                self.sheet_handler.battle_status_sheet.clear()
                # 헤더만 다시 작성
                headers = self.sheet_handler.battle_stat_headers
                self.sheet_handler.battle_status_sheet.update('A1', [headers])

                # 캐시도 초기화
                self.sheet_handler.battle_stat_sheet_cache = pd.DataFrame(columns=headers[1:])
                self.sheet_handler.battle_stat_sheet_cache.index.name = 'id'

                logger.info("Combat_Status 시트 및 캐시 초기화 완료")
            except Exception as e:
                logger.error(f"Combat_Status 초기화 실패: {e}")

            del self.active_battles[channel_id]
            embed = discord.Embed(
                title="⏹️ 전투 종료",
                description="현재 채널의 전투가 강제로 종료되었습니다.",
                color=discord.Color.dark_grey()
            )
            await interaction.followup.send(embed=embed)
        else:
            await interaction.followup.send("❌ 이 채널에서 진행 중인 전투가 없습니다.")

    @app_commands.command(name="치트", description="GM 전용: 전투 중 치트 명령어")
    @app_commands.default_permissions(manage_roles=True)  # 역할 관리 권한이 있는 사람에게만 보임
    @app_commands.describe(
        action="수행할 작업",
        target_id="대상 ID (지정 작업 시 필요)",
        amount="힐량 또는 데미지량"
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="지정ID 힐", value="heal_target"),
        app_commands.Choice(name="전체 힐", value="heal_all"),
        app_commands.Choice(name="전체 살리기", value="revive_all"),
        app_commands.Choice(name="지정ID 살리기", value="revive_target"),
        app_commands.Choice(name="데미지 주기", value="damage_target")
    ])
    async def cheat(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        target_id: str = None,
        amount: int = None
    ):
        """GM 전용 치트 명령어 - 전투 중 HP 조작"""
        await interaction.response.defer(ephemeral=True)

        # "진행" 역할 체크
        if not isinstance(interaction.user, discord.Member):
            await interaction.followup.send("❌ 이 명령어는 서버에서만 사용할 수 있습니다.", ephemeral=True)
            return

        has_role = discord.utils.get(interaction.user.roles, name='진행')
        if not has_role:
            await interaction.followup.send("❌ 이 명령어는 '진행' 역할을 가진 사람만 사용할 수 있습니다.", ephemeral=True)
            return

        # 전투 중인지 확인
        channel_id = interaction.channel_id
        if channel_id not in self.active_battles:
            await interaction.followup.send("❌ 이 채널에서 진행 중인 전투가 없습니다.", ephemeral=True)
            return

        battle_state = self.active_battles[channel_id]
        participants_cache = battle_state['participants_cache']
        action_value = action.value

        try:
            # ===== 지정ID 힐 =====
            if action_value == "heal_target":
                if not target_id:
                    await interaction.followup.send("❌ 대상 ID를 입력해주세요.", ephemeral=True)
                    return
                if not amount or amount <= 0:
                    await interaction.followup.send("❌ 힐량을 입력해주세요. (양수)", ephemeral=True)
                    return

                target_id = str(target_id)
                if target_id not in participants_cache.index:
                    await interaction.followup.send(f"❌ ID '{target_id}'를 전투 참가자에서 찾을 수 없습니다.", ephemeral=True)
                    return

                current_hp = int(participants_cache.loc[target_id, 'current_hp'])
                max_hp = int(participants_cache.loc[target_id, 'max_hp'])
                new_hp = min(current_hp + amount, max_hp)
                participants_cache.loc[target_id, 'current_hp'] = new_hp

                target_name = participants_cache.loc[target_id, 'name']
                await interaction.channel.send(
                    f"{target_name}의 HP를 {amount} 회복시켰습니다! "
                    f"({current_hp} → {new_hp}/{max_hp})"
                )
                await interaction.followup.send(f"✅ {target_name}을(를) {amount} 힐했습니다.", ephemeral=True)

            # ===== 전체 힐 =====
            elif action_value == "heal_all":
                if not amount or amount <= 0:
                    await interaction.followup.send("❌ 힐량을 입력해주세요. (양수)", ephemeral=True)
                    return

                healed_list = []
                for pid in participants_cache.index:
                    current_hp = int(participants_cache.loc[pid, 'current_hp'])
                    max_hp = int(participants_cache.loc[pid, 'max_hp'])
                    new_hp = min(current_hp + amount, max_hp)
                    participants_cache.loc[pid, 'current_hp'] = new_hp

                    name = participants_cache.loc[pid, 'name']
                    healed_list.append(f"{name}: {current_hp} → {new_hp}/{max_hp}")

                healed_text = "\n".join(healed_list)
                await interaction.channel.send(
                    f"모든 사람의 HP를 {amount}씩 회복시켰습니다!\n{healed_text}"
                )
                await interaction.followup.send(f"✅ 전체를 {amount} 힐했습니다.", ephemeral=True)

            # ===== 전체 살리기 =====
            elif action_value == "revive_all":
                revived_list = []
                for pid in participants_cache.index:
                    is_dead = int(participants_cache.loc[pid, 'is_dead'])
                    current_hp = int(participants_cache.loc[pid, 'current_hp'])
                    max_hp = int(participants_cache.loc[pid, 'max_hp'])

                    if is_dead > 0 or current_hp <= 0:
                        participants_cache.loc[pid, 'is_dead'] = 0
                        participants_cache.loc[pid, 'current_hp'] = max_hp
                        name = participants_cache.loc[pid, 'name']
                        revived_list.append(f"{name}: HP {max_hp}로 부활")

                if revived_list:
                    revived_text = "\n".join(revived_list)
                    await interaction.channel.send(
                        f"전투불능 참가자들을 모두 부활시켰습니다!\n{revived_text}"
                    )
                    await interaction.followup.send(f"✅ {len(revived_list)}명을 부활시켰습니다.", ephemeral=True)
                else:
                    await interaction.followup.send("ℹ️ 전투불능 상태인 참가자가 없습니다.", ephemeral=True)

            # ===== 지정ID 살리기 =====
            elif action_value == "revive_target":
                if not target_id:
                    await interaction.followup.send("❌ 대상 ID를 입력해주세요.", ephemeral=True)
                    return

                target_id = str(target_id)
                if target_id not in participants_cache.index:
                    await interaction.followup.send(f"❌ ID '{target_id}'를 전투 참가자에서 찾을 수 없습니다.", ephemeral=True)
                    return

                max_hp = int(participants_cache.loc[target_id, 'max_hp'])
                participants_cache.loc[target_id, 'is_dead'] = 0
                participants_cache.loc[target_id, 'current_hp'] = max_hp

                target_name = participants_cache.loc[target_id, 'name']
                await interaction.channel.send(
                    f"{target_name}을(를) 부활시켰습니다! (HP {max_hp})"
                )
                await interaction.followup.send(f"✅ {target_name}을(를) 부활시켰습니다.", ephemeral=True)

            # ===== 데미지 주기 =====
            elif action_value == "damage_target":
                if not target_id:
                    await interaction.followup.send("❌ 대상 ID를 입력해주세요.", ephemeral=True)
                    return
                if not amount or amount <= 0:
                    await interaction.followup.send("❌ 데미지량을 입력해주세요. (양수)", ephemeral=True)
                    return

                target_id = str(target_id)
                if target_id not in participants_cache.index:
                    await interaction.followup.send(f"❌ ID '{target_id}'를 전투 참가자에서 찾을 수 없습니다.", ephemeral=True)
                    return

                current_hp = int(participants_cache.loc[target_id, 'current_hp'])
                max_hp = int(participants_cache.loc[target_id, 'max_hp'])
                new_hp = max(0, current_hp - amount)
                participants_cache.loc[target_id, 'current_hp'] = new_hp

                # 사망 처리
                if new_hp <= 0:
                    participants_cache.loc[target_id, 'is_dead'] = 3

                target_name = participants_cache.loc[target_id, 'name']
                death_text = " → 전투불능!" if new_hp <= 0 else ""
                await interaction.channel.send(
                    f"{target_name}에게 {amount} 데미지를 입혔습니다! "
                    f"({current_hp} → {new_hp}/{max_hp}){death_text}"
                )
                await interaction.followup.send(f"✅ {target_name}에게 {amount} 데미지를 입혔습니다.", ephemeral=True)

            # 변경사항을 구글 시트에 동기화
            self.sheet_handler.update_battle_status_cache_to_sheet()
            logger.info(f"[치트] {interaction.user.name}이(가) '{action.name}' 실행 완료 (대상: {target_id}, 수치: {amount})")

        except Exception as e:
            logger.error(f"치트 명령어 실행 실패: {e}", exc_info=True)
            await interaction.followup.send(f"❌ 치트 명령어 실행 중 오류 발생: {e}", ephemeral=True)

    async def _handle_player_turn(
        self,
        interaction: discord.Interaction,
        battle_state: Dict[str, Any],
        current_participant_id: str,
        participant_data: pd.Series,
        all_ids: List[str]
    ) -> bool:
        """플레이어 턴 처리

        Args:
            interaction: Discord 인터랙션
            battle_state: 전투 상태 딕셔너리
            current_participant_id: 현재 플레이어 ID
            participant_data: 참여자 데이터 (Series)
            all_ids: 전체 참여자 ID 목록

        Returns:
            bool: True=턴 정상 진행, False=턴 스킵 필요
        """
        channel_id = interaction.channel_id
        participants_cache = battle_state['participants_cache']
        player_ids = [p_id for p_id in all_ids if p_id.isdigit()]
        monster_ids = [p_id for p_id in all_ids if not p_id.isdigit()]

        # ===== HP 0 체크 (is_dead == 0이어도 HP가 0이면 행동 불가) =====
        current_hp = int(participant_data.get('current_hp', 0))
        is_dead_value = int(participant_data.get('is_dead', 0))
        if is_dead_value == 0 and current_hp <= 0:
            participant_name = participant_data.get('name', '알 수 없음')
            await interaction.channel.send(f"**{participant_name}**은(는) HP가 0이라 행동할 수 없다!")
            return True  # 턴 소모하고 다음으로

        # ===== 석화 상태 체크 (Phase 1: 석화 상태 행동 불가) =====
        status = str(participant_data.get('status', '정상'))
        if 'petrified' in status:
            participant_name = participant_data.get('name', '알 수 없음')
            await interaction.channel.send(f"**{participant_name}**은(는) 석화 상태라 행동할 수 없다!")
            return True  # 턴 소모하고 다음으로

        # 턴 시작 버튼
        turn_start_view = TurnStartView()

        # battle_state에 현재 View 저장 (전투종료 시 중단을 위해)
        battle_state['active_view'] = turn_start_view

        turn_message = await interaction.channel.send(
            f"<@{current_participant_id}>\n행동을 시작하시려면 아래 버튼을 눌러주세요.",
            view=turn_start_view
        )
        await turn_start_view.wait()

        # View 사용 완료 후 제거
        battle_state['active_view'] = None

        action_interaction = turn_start_view.interaction

        if not action_interaction:
            # 전투가 종료되었는지 확인
            channel_id = interaction.channel_id
            if channel_id not in self.active_battles:
                logger.info(f"채널 {channel_id}: 전투가 이미 종료됨 (타임아웃 무시)")
                return False

            await interaction.channel.send(f"순서 대기 시간이 경과하였습니다. 다음 순서로 진행합니다.", delete_after=3)
            return False

        if str(action_interaction.user.id) != current_participant_id:
            await action_interaction.response.send_message("당신의 턴이 아닙니다.\n순서가 올 때까지 기다려주세요.", ephemeral=True)
            return False

        # 뒤로가기를 위한 루프
        chosen_action = None
        target_id = None
        first_iteration = True
        action_confirmed = False

        while not action_confirmed:
            # 행동 선택
            has_materia = participant_data.get('materia_owned') != '없음'
            can_use_limit = int(participant_data.get('limit_flag', 1)) == 0
            action_view = ActionView(materia_owned=has_materia, limit_flag=can_use_limit)
            action_view._update_button_states()  # 버튼 상태 업데이트

            if first_iteration:
                await action_interaction.response.send_message("행동을 선택하세요.", view=action_view, ephemeral=True)
                first_iteration = False
            else:
                await action_interaction.followup.send("행동을 선택하세요.", view=action_view, ephemeral=True)

            await action_view.wait()

            if not action_view.action:
                await interaction.channel.send(f"{action_interaction.user.display_name}의 행동 선택 시간이 초과되었습니다. 다음 턴으로 진행합니다.", delete_after=3)
                return False

            chosen_action = action_view.action
            target_id = action_view.target_id

            # logger.info(f"[PLAYER_ACTION] {current_participant_id}: chosen_action={chosen_action}")

            # 대상 선택이 필요없는 행동은 바로 확정
            if chosen_action in ['char_defend', 'char_evasion']:
                action_confirmed = True
            else:
                # 대상 선택이 필요한 행동은 일단 루프 탈출 (각 행동 처리에서 뒤로가기 처리)
                action_confirmed = True

        # 방어
        if chosen_action == 'char_defend':
            # current_user = await interaction.guild.fetch_member(int(current_participant_id))
            current_user = await interaction.guild.fetch_member(int(current_participant_id))
            
            embed = discord.Embed(
                title=f"{participant_data['name']}이(가) 방어 태세를 갖춥니다.",
                description="다음 턴까지 지속됩니다.",
                color=discord.Color.blue()
            )
            embed.set_author(
                name=participant_data['name'],
                icon_url=current_user.display_avatar.url if current_user else None
            )
            battle_state['participants_cache'].loc[current_participant_id, 'defend_flag'] = constants.DEFENSE_DURATION
            battle_state['combo_target_id'] = None
            battle_state['combo_count'] = 0
            await interaction.channel.send(embed=embed)

        # 회피
        elif chosen_action == 'char_evasion':
            # current_user = await interaction.guild.fetch_member(int(current_participant_id))
            current_user = await interaction.guild.fetch_member(int(current_participant_id))

            embed = discord.Embed(
                title=f"{participant_data['name']}이(가) 회피할 준비를 합니다.",
                description="다음 턴까지 지속됩니다.",
                color=discord.Color.blue()
            )
            embed.set_author(
                name=participant_data['name'],
                icon_url=current_user.display_avatar.url if current_user else None
            )
            battle_state['participants_cache'].loc[current_participant_id, 'evasion_flag'] = constants.EVASION_DURATION
            battle_state['combo_target_id'] = None
            battle_state['combo_count'] = 0
            await interaction.channel.send(embed=embed)


        # 물리 공격
        elif chosen_action == 'physic_atk':
            monster_ids_alive = [mid for mid in all_ids if not mid.isdigit() and int(participants_cache.loc[mid, 'current_hp']) > 0]
            if not monster_ids_alive:
                await interaction.channel.send("공격할 대상이 없습니다.", delete_after=3)
                return True

            targets = [(participants_cache.loc[mid, 'name'], mid) for mid in monster_ids_alive]

            attacker = Player(participant_data)
            power = int(participant_data.get('physics', 10))
            zack_status = participant_data.get('status', '정상')
            damage = utils.calculate_physical_damage(power, attacker.job, zack_status)

            # 뒤로가기 처리를 위한 루프
            target_selected = False
            while not target_selected:
                target_view = TargetSelectView(targets=targets)
                await action_interaction.followup.send("공격할 대상을 선택하세요.", view=target_view, ephemeral=True)
                await target_view.wait()

                # 뒤로가기 버튼을 눌렀다면
                if target_view.back_to_action:
                    # 행동 선택으로 돌아가기 위해 재귀적으로 플레이어 턴 재시작
                    return await self._handle_player_turn(interaction, battle_state, current_participant_id, participant_data, all_ids)

                target_selected = True

            if target_view.target_id:
                target_id = target_view.target_id

                # 연계 공격 체크
                combo_bonus = 1.0
                combo_message = ""
                previous_combo_count = battle_state.get('combo_count', 0)

                if battle_state.get('combo_target_id') == target_id:
                    # 먼저 증가시키고 증가된 값을 사용
                    battle_state['combo_count'] += 1
                    combo_count = battle_state['combo_count']

                    # 9연타 초과 시 연계 끊김 + 열기 보너스
                    if combo_count > constants.COMBO_MAX_COUNT:
                        # 전체 플레이어 MP 회복
                        player_ids = [p_id for p_id in all_ids if p_id.isdigit()]
                        for p_id in player_ids:
                            current_mp = int(participants_cache.loc[p_id, 'current_mp'])
                            max_mp = int(participants_cache.loc[p_id, 'max_mp'])
                            new_mp = min(current_mp + constants.COMBO_BREAK_HEAT_MP, max_mp)
                            participants_cache.loc[p_id, 'current_mp'] = new_mp

                        heat_embed = discord.Embed(
                            title="💥 연계가 끊어졌다! 하지만 열기가 남아있다....",
                            description=f"전체 플레이어가 **MP {constants.COMBO_BREAK_HEAT_MP}** 회복!",
                            color=discord.Color.gold()
                        )
                        await interaction.channel.send(embed=heat_embed)

                        # 연계 초기화 (새 시작)
                        battle_state['combo_target_id'] = target_id
                        battle_state['combo_count'] = 1
                        combo_count = 1

                    # 연계 보너스 적용 (2~9연타)
                    combo_bonuses = {
                        2: constants.COMBO_2HIT_BONUS,
                        3: constants.COMBO_3HIT_BONUS,
                        4: constants.COMBO_4HIT_BONUS,
                        5: constants.COMBO_5HIT_BONUS,
                        6: constants.COMBO_6HIT_BONUS,
                        7: constants.COMBO_7HIT_BONUS,
                        8: constants.COMBO_8HIT_BONUS,
                        9: constants.COMBO_9HIT_BONUS
                    }

                    if combo_count in combo_bonuses:
                        combo_bonus = 1.0 + combo_bonuses[combo_count]
                        fire_icons = "🔥" * min(combo_count, 5)
                        combo_message = f"\n***연계 공격 보너스!*** {fire_icons} **{combo_count}연타** (+{int(combo_bonuses[combo_count] * 100)}%)"
                else:
                    # 타겟이 바뀜 -> 연계 끊김
                    # 5-8연타 상태였다면 잔열 보너스
                    if 5 <= previous_combo_count <= 8:
                        player_ids = [p_id for p_id in all_ids if p_id.isdigit()]
                        for p_id in player_ids:
                            current_mp = int(participants_cache.loc[p_id, 'current_mp'])
                            max_mp = int(participants_cache.loc[p_id, 'max_mp'])
                            new_mp = min(current_mp + constants.COMBO_BREAK_WARM_MP, max_mp)
                            participants_cache.loc[p_id, 'current_mp'] = new_mp

                        warm_embed = discord.Embed(
                            title="🔥 연계가 끊어졌다! 하지만 잔열이 아른거린다....",
                            description=f"전체 플레이어가 **MP {constants.COMBO_BREAK_WARM_MP}** 회복!",
                            color=discord.Color.orange()
                        )
                        await interaction.channel.send(embed=warm_embed)

                    battle_state['combo_target_id'] = target_id
                    battle_state['combo_count'] = 1

                attacker = Player(participant_data)
                target = Character(participants_cache.loc[target_id])
                env_penalty = self._get_environment_penalty(battle_state)

                # 클로드 효과 체크 (3회 공격)
                attack_count = 1
                attacker_status = str(participant_data.get('status', '정상'))
                if 'claude_triple_attack' in attacker_status:
                    attack_count = 3

                result = attacker.physical_attack(target, status=participant_data.get('status', '정상'), environment_penalty=env_penalty, attack_count=attack_count)

                # 크리티컬 체크
                crit_chance = CombatUtils.calculate_critical_chance(current_participant_id, participants_cache)
                is_critical = random_utils.get_random() < crit_chance

                # 연계 보너스 적용
                base_damage = round(result['damage'] * combo_bonus)

                # 크리티컬 적용
                final_damage, is_critical = CombatUtils.apply_critical_damage(base_damage, is_critical)

                # 최소 데미지 보장
                min_damage_phs = participant_data.get('physics', '8')
                min_damage = round(min_damage_phs-10)
                final_damage = max(min_damage, final_damage)

                # 현재 HP에서 직접 계산 (캐시에서 가져오기)
                current_hp = int(participants_cache.loc[target_id, 'current_hp'])
                final_hp = max(0, current_hp - final_damage)
                
                # HP 업데이트 (CombatUtils 사용)
                CombatUtils.update_participant_hp(battle_state, target_id, final_hp)


                crit_message = "\n⚡ **크리티컬 히트!**" if is_critical else ""

                embed = discord.Embed(
                    title=f"{'⚡ ' if is_critical else ''}{attacker.name}의 물리 공격",
                    description=f"**{attacker.name}** → **{result['target_name']}**{combo_message}{crit_message}",
                    color=discord.Color.gold() if is_critical else (discord.Color.red() if combo_bonus == 1.0 else discord.Color.orange())
                )
                if attack_count > 1:
                    embed.add_field(name="공격 횟수", value=f"`{attack_count}회`", inline=True)
                embed.add_field(name="데미지", value=f"`{final_damage}`", inline=True)
                embed.add_field(name="남은 HP", value=f"`{final_hp}`", inline=True)
                if is_critical:
                    embed.set_footer(text=f"크리티컬 확률: {crit_chance*100:.1f}% | 데미지 배율: {constants.CRIT_MULTIPLIER}x")
                await interaction.channel.send(embed=embed)
                await self.send_monster_damage_reaction(target_id, interaction.channel, final_damage)

        # 마법
        elif chosen_action == 'magic':
            # 연계 끊김 체크 (5-8연타였다면 잔열 보너스)
            previous_combo_count = battle_state.get('combo_count', 0)
            if 5 <= previous_combo_count <= 8:
                player_ids = [p_id for p_id in all_ids if p_id.isdigit()]
                for p_id in player_ids:
                    current_mp = int(participants_cache.loc[p_id, 'current_mp'])
                    max_mp = int(participants_cache.loc[p_id, 'max_mp'])
                    new_mp = min(current_mp + constants.COMBO_BREAK_WARM_MP, max_mp)
                    participants_cache.loc[p_id, 'current_mp'] = new_mp

                warm_embed = discord.Embed(
                    title="🔥 연계가 끊어졌다! 하지만 잔열이 아른거린다....",
                    description=f"전체 플레이어가 **MP {constants.COMBO_BREAK_WARM_MP}** 회복!",
                    color=discord.Color.orange()
                )
                await interaction.channel.send(embed=warm_embed)

            battle_state['combo_target_id'] = None
            battle_state['combo_count'] = 0
            player_materia_name = participant_data.get('materia_owned')
            if not has_materia:
                await interaction.channel.send("장착한 마테리아가 없습니다.", delete_after=3)
                return True

            materia_info = self.sheet_handler.materia_list_sheet_cache.loc[player_materia_name]
            mp_cost = int(materia_info.get('mp_cost', 0))

            if int(participant_data['current_mp']) < mp_cost:
                await interaction.channel.send(f"MP가 부족하여 **{player_materia_name}**을(를) 사용할 수 없습니다. (필요 MP: {mp_cost})")
                return True

            caster = Player(participant_data)
            materia_target_type = materia_info['target']
            materia_type = materia_info['type']
            power = int(materia_info.get('power', 10))
            magic_effect_value = utils.calculate_magic_power(caster.stats['magic'], power, caster.job)

            target_ids = []
            # 광역 마법
            if materia_target_type in ['ALL_ENEMY', 'ALL_ALLY']:
                if materia_target_type == 'ALL_ENEMY':
                    target_ids = [mid for mid in all_ids if not mid.isdigit() and int(participants_cache.loc[mid, 'current_hp']) > 0]
                else:
                    target_ids = [pid for pid in all_ids if pid.isdigit() and int(participants_cache.loc[pid, 'current_hp']) >= 0 and int(participants_cache.loc[pid,'is_dead']) == 0]

                if not target_ids:
                    await interaction.channel.send("마법을 사용할 대상이 없습니다.", delete_after=3)
                    return True

                results_desc = ""
                damaged_monsters = []  # Phase 3.2: 피격 반응을 위한 몬스터 ID 수집
                env_penalty = self._get_environment_penalty(battle_state)

                # 크리티컬 체크 (공격 마법만)
                crit_chance = CombatUtils.calculate_critical_chance(current_participant_id, participants_cache)
                is_critical = random_utils.get_random() < crit_chance if materia_type == 'DAMAGE' else False

                for t_id in target_ids:
                    target_char = Character(participants_cache.loc[t_id])
                    if materia_type == 'DAMAGE':
                        # 크리티컬 적용
                        actual_damage, _ = CombatUtils.apply_critical_damage(magic_effect_value, is_critical)
                        # 최소 데미지 보장
                        actual_damage = max(1, actual_damage)
                        
                        # 현재 HP에서 직접 계산
                        current_hp = int(participants_cache.loc[t_id, 'current_hp'])
                        final_hp = max(0, current_hp - actual_damage)
                        
                        # HP 업데이트
                        CombatUtils.update_participant_hp(battle_state, t_id, final_hp)
                        
                        crit_icon = "⚡ " if is_critical else ""
                        results_desc += f"{crit_icon}**{target_char.name}**에게 `{actual_damage}`의 데미지! (HP: {final_hp})\n"
                        # Phase 3.2: 몬스터가 피해를 받은 경우 기록
                        if not t_id.isdigit():
                            damaged_monsters.append((t_id, actual_damage))
                    else:
                        is_dead = int(participants_cache.loc[t_id, 'is_dead'])
                        current_hp = int(participants_cache.loc[t_id, 'current_hp'])
                        max_hp = int(participants_cache.loc[t_id, 'max_hp'])
                        
                        if is_dead > 0:
                            results_desc += f"**{target_char.name}**은(는) 전투불능 상태라 회복할 수 없습니다!\n"
                        else:
                            final_hp = min(max_hp, current_hp + magic_effect_value)
                            CombatUtils.update_participant_hp(battle_state, t_id, final_hp)
                            results_desc += f"**{target_char.name}**의 HP `{magic_effect_value}` 회복 (HP: {final_hp})\n"

                embed_color = discord.Color.gold() if is_critical else (discord.Color.red() if materia_type == 'DAMAGE' else discord.Color.green())
                crit_title = f"⚡ {caster.name}이(가) {player_materia_name}을(를) 사용했다!" if is_critical else f"{caster.name}이(가) {player_materia_name}을(를) 사용했다!"
                embed = discord.Embed(
                    title=crit_title,
                    description=materia_info.get('description', '마법 설명이 없습니다.'),
                    color=embed_color
                )
                # embed.set_author(name=caster.name, icon_url=caster.dis)
                embed.set_author(name= action_interaction.user.display_name, icon_url=action_interaction.user.display_avatar)
                embed.add_field(name="효과 적용 결과", value=results_desc, inline=False)
                if is_critical:
                    embed.set_footer(text=f"⚡ 크리티컬 히트! | 확률: {crit_chance*100:.1f}% | 배율: {constants.CRIT_MULTIPLIER}x")
                await interaction.channel.send(embed=embed)
                # Phase 3.2: 피격 반응 메시지 출력
                for monster_id, dmg in damaged_monsters:
                    await self.send_monster_damage_reaction(monster_id, interaction.channel, dmg)
                    await asyncio.sleep(0.5)

            # 단일 대상 마법
            elif materia_target_type in ['ENEMY', 'ALLY']:
                potential_targets = []
                if materia_target_type == 'ENEMY':
                    potential_targets = [(participants_cache.loc[mid, 'name'], mid) for mid in monster_ids if int(participants_cache.loc[mid, 'current_hp']) > 0]
                else:
                    potential_targets = [(participants_cache.loc[pid, 'name'], pid) for pid in player_ids if int(participants_cache.loc[pid, 'current_hp']) > 0]

                if not potential_targets:
                    await interaction.channel.send("마법을 사용할 대상이 없습니다.", delete_after=3)
                    return True

                # 뒤로가기 처리를 위한 루프
                target_selected = False
                while not target_selected:
                    target_view = TargetSelectView(targets=potential_targets)
                    await action_interaction.followup.send("마법을 사용할 대상을 선택하세요.", view=target_view, ephemeral=True)
                    await target_view.wait()

                    # 뒤로가기 버튼을 눌렀다면
                    if target_view.back_to_action:
                        # 행동 선택으로 돌아가기 위해 재귀적으로 플레이어 턴 재시작
                        return await self._handle_player_turn(interaction, battle_state, current_participant_id, participant_data, all_ids)

                    if not target_view.target_id:
                        await interaction.channel.send("❌ 대상 선택이 취소되었습니다.", delete_after=3)
                        return True

                    target_selected = True

                target_id = target_view.target_id
                if not target_id:  # 타입 체커를 위한 추가 안전 검사
                    await interaction.channel.send("❌ 대상 선택 오류가 발생했습니다.", delete_after=3)
                    return True

                target_char = Character(participants_cache.loc[target_id])
                env_penalty = self._get_environment_penalty(battle_state)

                # 크리티컬 체크 (공격 마법만)
                crit_chance = CombatUtils.calculate_critical_chance(current_participant_id, participants_cache)
                is_critical = random_utils.get_random() < crit_chance if materia_type == 'DAMAGE' else False

                if materia_type == 'DAMAGE':
                    # 크리티컬 적용
                    actual_damage, _ = CombatUtils.apply_critical_damage(magic_effect_value, is_critical)
                    # 최소 데미지 보장
                    actual_damage = max(1, actual_damage)
                    
                    # 현재 HP에서 직접 계산
                    current_hp = int(participants_cache.loc[target_id, 'current_hp'])
                    final_hp = max(0, current_hp - actual_damage)
                    
                    # HP 업데이트
                    CombatUtils.update_participant_hp(battle_state, target_id, final_hp)
                    
                    embed_color = discord.Color.from_rgb(234, 29, 30)
                    title = f"{player_materia_name}"
                    field_name = "데미지"
                    display_damage = actual_damage
                else:
                    is_dead = int(participants_cache.loc[target_id, 'is_dead'])
                    current_hp = int(participants_cache.loc[target_id, 'current_hp'])
                    max_hp = int(participants_cache.loc[target_id, 'max_hp'])
                    
                    embed_color = discord.Color.from_rgb(46, 225, 121)
                    title = f"{player_materia_name}"
                    field_name = "회복량"
                    display_damage = magic_effect_value
                    
                    if is_dead > 0:
                        field_name = "❌ 회복 실패"
                        display_damage = "전투불능 상태"
                        final_hp = current_hp
                    else:
                        final_hp = min(max_hp, current_hp + magic_effect_value)
                        CombatUtils.update_participant_hp(battle_state, target_id, final_hp)

                embed_color = discord.Color.gold() if is_critical else (discord.Color.red() if materia_type == 'DAMAGE' else discord.Color.green())
                crit_title = f"⚡ {caster.name}이(가) {player_materia_name}을(를) 사용했다!" if is_critical else f"{caster.name}이(가) {player_materia_name}을(를) 사용했다!"
                embed = discord.Embed(
                    title=crit_title,
                    description=materia_info.get('description', '마법 설명이 없습니다.'),
                    color=embed_color
                )
                # embed.set_author(name=caster.name)
                embed.set_author(name= action_interaction.user.display_name, icon_url=action_interaction.user.display_avatar)
                embed.add_field(name="대상", value=target_char.name, inline=False)
                embed.add_field(name=field_name, value=f"`{display_damage}`")
                embed.add_field(name="대상의 남은 HP", value=f"`{final_hp}`")
                if is_critical:
                    embed.set_footer(text=f"⚡ 크리티컬 히트! | 확률: {crit_chance*100:.1f}% | 배율: {constants.CRIT_MULTIPLIER}x")
                await interaction.channel.send(embed=embed)
                # Phase 3.2: 피격 반응 메시지 출력 (몬스터가 피해를 받은 경우)
                if materia_type == 'DAMAGE' and not target_id.isdigit():
                    await self.send_monster_damage_reaction(target_id, interaction.channel, actual_damage if is_critical else magic_effect_value)
                    await asyncio.sleep(0.5)


            new_mp = max(0, caster.current_mp - mp_cost)
            battle_state['participants_cache'].loc[current_participant_id, 'current_mp'] = new_mp

        # 리미트 브레이크
        elif chosen_action == 'limit_break':
            # 연계 끊김 체크 (5-8연타였다면 잔열 보너스)
            previous_combo_count = battle_state.get('combo_count', 0)
            if 5 <= previous_combo_count <= 8:
                player_ids = [p_id for p_id in all_ids if p_id.isdigit()]
                for p_id in player_ids:
                    current_mp = int(participants_cache.loc[p_id, 'current_mp'])
                    max_mp = int(participants_cache.loc[p_id, 'max_mp'])
                    new_mp = min(current_mp + constants.COMBO_BREAK_WARM_MP, max_mp)
                    participants_cache.loc[p_id, 'current_mp'] = new_mp

                warm_embed = discord.Embed(
                    title="🔥 연계가 끊어졌다! 하지만 잔열이 아른거린다....",
                    description=f"전체 플레이어가 **MP {constants.COMBO_BREAK_WARM_MP}** 회복!",
                    color=discord.Color.orange()
                )
                await interaction.channel.send(embed=warm_embed)

            battle_state['combo_target_id'] = None
            battle_state['combo_count'] = 0
            caster = Player(participant_data)
            job = caster.job

            if job == "스트라이커":
                hp_cost = round(caster.current_hp * constants.STRIKER_LIMIT_HP_COST)
                caster_final_hp = caster.take_damage(hp_cost, limit_break=True)
                CombatUtils.update_participant_hp(battle_state, current_participant_id, caster_final_hp)

                damage = round(caster.max_hp * constants.STRIKER_LIMIT_DAMAGE)
                alive_monsters = [mid for mid in monster_ids if int(participants_cache.loc[mid, 'current_hp']) > 0]

                results_desc = f"자신의 HP `{hp_cost}`를 소모하여 모든 적에게 강력한 공격을 가합니다!\n\n"
                damaged_monsters = []  # Phase 3.2: 피격 반응을 위한 몬스터 ID 수집
                env_penalty = self._get_environment_penalty(battle_state)
                for m_id in alive_monsters:
                    target_monster = Character(participants_cache.loc[m_id])
                    monster_final_hp = target_monster.take_damage(damage, environment_penalty=env_penalty)
                    CombatUtils.update_participant_hp(battle_state, m_id, monster_final_hp)
                    results_desc += f"**{target_monster.name}**에게 `{damage}`의 데미지! (남은 HP: {monster_final_hp})\n"
                    damaged_monsters.append((m_id, damage))  # Phase 3.2

                embed = discord.Embed(
                    title="*Limit Break: Vitality Burst*",
                    description=results_desc,
                    color=discord.Color.orange()
                )
                # embed.set_author(name=caster.name)
                embed.set_author(name= action_interaction.user.display_name, icon_url=action_interaction.user.display_avatar)
                await interaction.channel.send(embed=embed)

                # Phase 3.2: 피격 반응 메시지 출력
                for monster_id, dmg in damaged_monsters:
                    await self.send_monster_damage_reaction(monster_id, interaction.channel, dmg)
                    await asyncio.sleep(0.5)

                battle_state['participants_cache'].loc[current_participant_id, 'limit_flag'] = 1

            elif job == "마테리아 위버":
                mp_cost = round(caster.current_mp * constants.WEAVER_LIMIT_MP_COST)
                caster_final_mp = max(0, caster.current_mp - mp_cost)
                battle_state['participants_cache'].loc[current_participant_id, 'current_mp'] = caster_final_mp

                alive_players = [pid for pid in player_ids if int(participants_cache.loc[pid, 'current_hp']) > 0]

                results_desc = f"자신의 MP `{mp_cost}`를 소모하여 모든 아군의 생명력을 회복시킵니다!\n\n"
                for p_id in alive_players:
                    target_player = Player(participants_cache.loc[p_id])
                    heal_amount = round(target_player.max_hp * constants.WEAVER_LIMIT_HEAL)
                    is_dead = int(participants_cache.loc[p_id, 'is_dead'])
                    player_final_hp = target_player.heal(heal_amount, is_dead=is_dead)

                    CombatUtils.update_participant_hp(battle_state, p_id, player_final_hp)

                    if is_dead > 0:
                        results_desc += f"**{target_player.name}**: 전투불능 상태라 회복 불가 (HP: {player_final_hp})\n"
                    else:
                        results_desc += f"**{target_player.name}**의 HP `{heal_amount}` 회복! (HP: {player_final_hp})\n"

                embed = discord.Embed(
                    title="*Limit Break: Sanctuary of Life*",
                    description=results_desc,
                    color=discord.Color.magenta()
                )
                # embed.set_author(name=caster.name)
                embed.set_author(name= action_interaction.user.display_name, icon_url=action_interaction.user.display_avatar)
                await interaction.channel.send(embed=embed)
                battle_state['participants_cache'].loc[current_participant_id, 'limit_flag'] = 1

            elif job == "솔져":
                limit_info = caster.limit_break_kind()

                hp_cost = int(constants.SOLDIER_LIMIT_HP_COST*caster.current_hp)
                caster_final_hp = caster.take_damage(hp_cost, limit_break=True)
                CombatUtils.update_participant_hp(battle_state, current_participant_id, caster_final_hp)

                alive_monsters = [mid for mid in monster_ids if int(participants_cache.loc[mid, 'current_hp']) > 0]
                if not alive_monsters:
                    await interaction.channel.send("❌ 공격할 대상이 없습니다.", delete_after=3)
                    return True

                potential_targets = [(participants_cache.loc[mid, 'name'], mid) for mid in alive_monsters]

                # 뒤로가기 처리를 위한 루프
                target_selected = False
                while not target_selected:
                    target_view = TargetSelectView(targets=potential_targets)
                    await action_interaction.followup.send("팔도일섬의 대상을 선택하세요.", view=target_view, ephemeral=True)
                    await target_view.wait()

                    # 뒤로가기 버튼을 눌렀다면
                    if target_view.back_to_action:
                        # 행동 선택으로 돌아가기 위해 재귀적으로 플레이어 턴 재시작
                        return await self._handle_player_turn(interaction, battle_state, current_participant_id, participant_data, all_ids)

                    if not target_view.target_id:
                        await interaction.channel.send("❌ 대상 선택이 취소되었습니다.", delete_after=3)
                        return True

                    target_selected = True

                target_id = target_view.target_id
                target_monster = Character(participants_cache.loc[target_id])

                slash_count = limit_info['slash_count']
                damage_per_slash = limit_info['damage_per_slash']

                embed = discord.Embed(
                    title="*Limit Break: 八刀一閃*",
                    description=f"**{caster.name}**이(가) HP `{hp_cost}`를 소모하여 **{target_monster.name}**에게 **팔도일섬**을 시전합니다!\n\n`{slash_count}연타 공격 시작...`",
                    color=discord.Color.dark_gold()
                )
                # embed.set_author(name=caster.name)
                embed.set_author(name= action_interaction.user.display_name, icon_url=action_interaction.user.display_avatar)
                slash_message = await interaction.channel.send(embed=embed)

                await asyncio.sleep(1.0)

                total_actual_damage = 0
                for i in range(slash_count):
                    damage_per_slash = utils.apply_damage_rounding(caster.stats['physics']) + random_utils.randint(1, 10) + random_utils.randint(1, 6)

                    current_hp = int(participants_cache.loc[target_id, 'current_hp'])
                    if current_hp <= 0:
                        embed.add_field(
                            name=f"{i+1}번째 베기",
                            value="대상이 이미 전투불능!",
                            inline=False
                        )
                        await slash_message.edit(embed=embed)
                        break

                    damaged_hp = max(0, current_hp - damage_per_slash)
                    actual_damage = current_hp - damaged_hp
                    total_actual_damage += actual_damage

                    participants_cache.loc[target_id, 'current_hp'] = damaged_hp
                    if damaged_hp == 0:
                        participants_cache.loc[target_id, 'is_dead'] = 3

                    embed.add_field(
                        name=f"{i+1}번째 베기",
                        value=f"**데미지**: `{actual_damage}`\n**남은 HP**: `{damaged_hp}`",
                        inline=True
                    )

                    await slash_message.edit(embed=embed)
                    await asyncio.sleep(0.7)

                embed.add_field(
                    name="최종 결과",
                    value=f"**총 연타 횟수**: {slash_count}회\n**총 데미지**: `{total_actual_damage}`",
                    inline=False
                )

                embed.color = discord.Color.gold()
                await slash_message.edit(embed=embed)

                battle_state['participants_cache'].loc[current_participant_id, 'limit_flag'] = 1
                await self.send_monster_damage_reaction(target_id, interaction.channel, total_actual_damage)

            elif job == "턱스":
                limit_info = caster.limit_break_kind()
                mp_cost = constants.TURKS_LIMIT_MP_COST

                if caster.current_mp < mp_cost:
                    await action_interaction.followup.send(f"❌ MP가 부족합니다. (필요 MP: {mp_cost})", ephemeral=True)
                    return True

                new_mp = max(0, caster.current_mp - mp_cost)
                battle_state['participants_cache'].loc[current_participant_id, 'current_mp'] = new_mp

                alive_monsters = [
                    mid for mid in monster_ids
                    if int(participants_cache.loc[mid, 'current_hp']) > 0
                    and participants_cache.loc[mid, 'monster_type'] != 'boss'
                ]

                if not alive_monsters:
                    await interaction.channel.send("❌ 스턴 가능한 대상이 없습니다. (보스는 면역)", delete_after=3)
                    return True

                potential_targets = [(participants_cache.loc[mid, 'name'], mid) for mid in alive_monsters]

                # 뒤로가기 처리를 위한 루프
                target_selected = False
                while not target_selected:
                    target_view = TargetSelectView(targets=potential_targets)
                    await action_interaction.followup.send("전격 제압의 대상을 선택하세요. (보스 제외)", view=target_view, ephemeral=True)
                    await target_view.wait()

                    # 뒤로가기 버튼을 눌렀다면
                    if target_view.back_to_action:
                        # 행동 선택으로 돌아가기 위해 재귀적으로 플레이어 턴 재시작
                        return await self._handle_player_turn(interaction, battle_state, current_participant_id, participant_data, all_ids)

                    if not target_view.target_id:
                        await interaction.channel.send("❌ 대상 선택이 취소되었습니다.", delete_after=3)
                        return True

                    target_selected = True

                target_id = target_view.target_id
                target_monster_data = participants_cache.loc[target_id]
                target_name = target_monster_data['name']

                stun_duration = constants.TURKS_STUN_DURATION
                battle_state['participants_cache'].loc[target_id, 'status'] = f'turks_stun:{stun_duration}'

                embed = discord.Embed(
                    title="*Limit Break: Blackout Pulse*",
                    description=f"**{caster.name}**이(가) MP `{mp_cost}`를 소모하여 **{target_name}**을(를) 기절시켰습니다!\n\n**{stun_duration - 1}턴간 행동 불가!**",
                    color=discord.Color.dark_purple()
                )
                # embed.set_author(name=caster.name)
                embed.set_author(name= action_interaction.user.display_name, icon_url=action_interaction.user.display_avatar)
                await interaction.channel.send(embed=embed)

                battle_state['participants_cache'].loc[current_participant_id, 'limit_flag'] = 1

            else:
                await action_interaction.followup.send("리미트 브레이크를 위한 직업 정보가 없습니다.", ephemeral=True)
                return True
        else:
            pass

        # DMW 시스템 발동 (보스전투 모드 플래그 전달)
        boss_mode = battle_state.get('boss_mode', False)
        dmw_system = DMWSystem(self.bot, self.sheet_handler, battle_state, current_participant_id, boss_mode=boss_mode)
        dmw_result = await dmw_system.run_dmw(interaction.channel, debug_mode=constants.DEBUG_MODE)

        # DMW 효과 적용
        if dmw_result["success"]:
            effect = dmw_result["effect"]
            effect_embed = discord.Embed(title="DMW 발동!", color=discord.Color.gold())

            if effect == "sephiroth_damage":
                damage = 10 + random_utils.randint(1, 10) + random_utils.randint(1, 10)
                alive_monsters = [mid for mid in monster_ids if int(participants_cache.loc[mid, 'current_hp']) > 0]
                desc = "모든 적에게 데미지를 줍니다"
                if not alive_monsters:
                    desc = "공격할 대상이 없습니다."
                else:
                    env_penalty = self._get_environment_penalty(battle_state)
                    for m_id in alive_monsters:
                        target_monster = Character(participants_cache.loc[m_id])
                        final_hp = target_monster.take_damage(damage, environment_penalty=env_penalty)
                        participants_cache.loc[m_id, 'current_hp'] = final_hp
                        if final_hp == 0:
                            participants_cache.loc[m_id, 'is_dead'] = 3
                        desc += f"**{target_monster.name}**에게 `{damage}`의 데미지!\n"
                effect_embed.description = desc
                await interaction.channel.send(embed=effect_embed)

            elif effect == "angeal_limit_reset":
                for p_id in player_ids:
                    participants_cache.loc[p_id, 'limit_flag'] = 0
                effect_embed.description = "모든 아군의 리미트 브레이크가 초기화됩니다!"
                await interaction.channel.send(embed=effect_embed)

            elif effect == "genesis_mp_heal":
                heal_amount = 10 + random_utils.randint(1, 10) + random_utils.randint(1, 5)
                desc = "모든 아군의 MP가 회복됩니다.\n"
                for p_id in player_ids:
                    player = Player(participants_cache.loc[p_id])
                    final_mp = min(player.max_mp, player.current_mp + heal_amount)
                    participants_cache.loc[p_id, 'current_mp'] = final_mp
                    desc += f"**{player.name}**의 MP `{heal_amount}` 회복!\n"
                effect_embed.description = desc
                await interaction.channel.send(embed=effect_embed)

            elif effect == "zack_str_up":
                desc = "3턴 간 데미지가 20% 증가합니다!"
                for p_id in player_ids:
                    participants_cache.loc[p_id, 'status'] = 'zack_buff:3'
                    player_name = participants_cache.loc[p_id, 'name']
                    desc += f"**{player_name}**의 힘이 강해집니다! (3턴 지속)\n"
                effect_embed.description = desc
                await interaction.channel.send(embed=effect_embed)

            elif effect == "ethan_revive":
                revived_players = []
                for p_id in player_ids:
                    if int(participants_cache.loc[p_id, 'is_dead']) > 0:
                        participants_cache.loc[p_id, 'is_dead'] = 0
                        participants_cache.loc[p_id, 'current_hp'] = 1
                        revived_players.append(participants_cache.loc[p_id, 'name'])

                if revived_players:
                    effect_embed.description = f"**{', '.join(revived_players)}**이(가) HP 1로 전투에 복귀합니다!"
                else:
                    effect_embed.description = "모든 아군이 생존해 있어 아무 일도 일어나지 않았습니다."
                await interaction.channel.send(embed=effect_embed)

            elif effect == "tokura_death_sentence":
                desc = ""
                alive_monsters = [mid for mid in monster_ids if int(participants_cache.loc[mid, 'current_hp']) > 0]
                if not alive_monsters:
                    desc = "죽음의 선고를 내릴 대상이 없습니다."
                else:
                    # 보스전투 모드: 10턴, 일반전투: 3턴
                    is_boss_mode = battle_state.get('boss_mode', False)
                    death_turns = constants.DMW_TOKURA_DEATH_BOSS if is_boss_mode else constants.DMW_TOKURA_DEATH_NORMAL
                    for m_id in alive_monsters:
                        participants_cache.loc[m_id, 'status'] = f'tokura_death:{death_turns}'
                        monster_name = participants_cache.loc[m_id, 'name']
                        desc += f"**{monster_name}**에게 죽음의 선고가 내려집니다... ({death_turns}턴 후 즉사)\n"
                effect_embed.description = desc
                await interaction.channel.send(embed=effect_embed)

            elif effect == "rachel_party_heal":
                heal_amount = 10 + random_utils.randint(1, 10) + random_utils.randint(1, 10)
                desc = ""
                for p_id in player_ids:
                    p_data = participants_cache.loc[p_id]
                    player_name = p_data['name']
                    is_dead = int(p_data['is_dead'])

                    if is_dead > 0:
                        desc += f"**{player_name}**: 전투불능 상태라 회복 불가\n"
                    else:
                        current_hp = int(p_data['current_hp'])
                        max_hp = int(p_data['max_hp'])
                        new_hp = min(max_hp, current_hp + heal_amount)

                        CombatUtils.update_participant_hp(battle_state, p_id, new_hp)

                        desc += f"**{player_name}**의 HP가 `{heal_amount}` 회복되었습니다 (HP: {new_hp})\n"

                effect_embed.description = desc
                await interaction.channel.send(embed=effect_embed)

        await asyncio.sleep(2)

        return True

    async def _handle_monster_turn(
        self,
        interaction: discord.Interaction,
        battle_state: Dict[str, Any],
        channel_id: int,
        current_participant_id: str,
        participant_data: pd.Series,
        all_ids: List[str]
    ) -> None:
        """몬스터 턴 처리

        Args:
            interaction: Discord 인터랙션
            battle_state: 전투 상태 딕셔너리
            channel_id: 채널 ID
            current_participant_id: 현재 몬스터 ID
            participant_data: 참여자 데이터 (Series)
            all_ids: 전체 참여자 ID 목록
        """
        participants_cache = battle_state['participants_cache']
        player_ids = [p_id for p_id in all_ids if p_id.isdigit()]
        monster_ids = [p_id for p_id in all_ids if not p_id.isdigit()]

        # 일시정지 체크
        await self._check_pause(battle_state, interaction.channel, channel_id, current_participant_id, is_player=False)

        monster_data = participant_data
        attacker = Character(monster_data)
        magic_enabled = int(monster_data.get('magic_enabled_flag', 0)) == 1 and monster_data.get('materia_owned') != '없음'

        # 스턴 상태 확인
        monster_status = str(monster_data.get('status', '정상'))
        if 'turks_stun' in monster_status:
            embed = discord.Embed(
                title=f"⚡ {attacker.name}의 턴",
                description=f"**{attacker.name}**은(는) 기절 상태입니다! (행동 불가)",
                color=discord.Color.light_grey()
            )
            embed.set_footer(text="턱스의 전격 제압 효과")
            await interaction.channel.send(embed=embed)
            return

        # ===== 석화 상태 체크 (Phase 1: 석화 상태 행동 불가) =====
        if 'petrified' in monster_status:
            embed = discord.Embed(
                title="💎 석화 상태!",
                description=f"**{attacker.name}**은(는) 석화 상태라 행동할 수 없다!",
                color=discord.Color.light_grey()
            )
            await interaction.channel.send(embed=embed)
            return

        # 특수 패턴 체크
        special_action = await self.apply_monster_special_pattern(current_participant_id, monster_data, battle_state, interaction.channel)

        if special_action and special_action.get('action') == 'charge_attack':
            chosen_action = 'charge_attack'
        elif special_action and special_action.get('action') == 'steal_mp':
            chosen_action = 'steal_mp'
        else:
            if magic_enabled:
                actions = list(constants.MONSTER_ACTION_WEIGHTS_WITH_MAGIC.keys())
                weights = list(constants.MONSTER_ACTION_WEIGHTS_WITH_MAGIC.values())
            else:
                actions = list(constants.MONSTER_ACTION_WEIGHTS_NO_MAGIC.keys())
                weights = list(constants.MONSTER_ACTION_WEIGHTS_NO_MAGIC.values())

            chosen_action = random_utils.choices(actions, weights=weights, k=1)[0]

        # 행동 실행
        participant_name = participant_data.get('name')

        if chosen_action == 'physic_atk':
            player_ids_alive = [pid for pid in all_ids if pid.isdigit() and int(participants_cache.loc[pid, 'current_hp']) > 0]
            if not player_ids_alive:
                await interaction.channel.send(f"**{participant_name}**이(가) 공격할 대상이 없습니다.", delete_after=3)
                return

            target_id = random_utils.choice(player_ids_alive)
            target = Player(participants_cache.loc[target_id])

            damage = utils.calculate_physical_damage(
                attacker.stats['physics'],
                attacker.job,
                status=monster_data.get('status', '정상')
            )

            if monster_data.get('rage_activated', False):
                damage = int(damage * constants.RAGE_MODE_DAMAGE_MULTIPLIER)

            # 크리티컬 체크
            crit_chance = CombatUtils.calculate_critical_chance(current_participant_id, participants_cache)
            is_critical = random_utils.get_random() < crit_chance

            # 크리티컬 적용
            damage, is_critical = CombatUtils.apply_critical_damage(damage, is_critical)

            # 최소 데미지 보장
            damage = max(1, damage)

            initial_hp = target.current_hp
            env_penalty = self._get_environment_penalty(battle_state)
            final_hp = target.take_damage(damage, environment_penalty=env_penalty)
            actual_damage = initial_hp - final_hp

            CombatUtils.update_participant_hp(battle_state, target_id, final_hp)

            crit_message = "\n⚡ **크리티컬 히트!**" if is_critical else ""

            embed = discord.Embed(
                title=f"{'⚡ ' if is_critical else ''}적의 공격: {attacker.name}",
                description=f"**{attacker.name}** → **{target.name}**{crit_message}",
                color = discord.Color.gold() if is_critical else discord.Color.red()
            )
            embed.add_field(name="데미지", value=f"`{actual_damage}`")
            embed.add_field(name="남은 HP", value=f"`{final_hp}`")
            if is_critical:
                embed.set_footer(text=f"크리티컬 확률: {crit_chance*100:.1f}% | 데미지 배율: {constants.CRIT_MULTIPLIER}x")
            await interaction.channel.send(embed=embed)

        elif chosen_action == 'charge_attack':
            player_ids_alive = [pid for pid in all_ids if pid.isdigit() and int(participants_cache.loc[pid, 'current_hp']) > 0]
            if not player_ids_alive:
                await interaction.channel.send(f"**{participant_name}**이(가) 공격할 대상이 없습니다.", delete_after=3)
                return

            base_damage = utils.calculate_physical_damage(attacker.stats['physics'], attacker.job, status='정상')
            charge_damage = int(base_damage * constants.CHARGE_ATTACK_MULTIPLIER)

            embed = discord.Embed(
                title=f"차지 어택!",
                description=f"**{attacker.name}**이(가) 힘을 모아 전체 공격을 가합니다!",
                color=discord.Color.orange()
            )

            env_penalty = self._get_environment_penalty(battle_state)
            for pid in player_ids_alive:
                target = Player(participants_cache.loc[pid])
                initial_hp = target.current_hp
                final_hp = target.take_damage(charge_damage, environment_penalty=env_penalty)
                actual_damage = initial_hp - final_hp

                CombatUtils.update_participant_hp(battle_state, pid, final_hp)
                
                embed.add_field(name=f"{target.name}", value=f"데미지: `{actual_damage}` | HP: `{final_hp}`", inline=False)
                
            await interaction.channel.send(embed=embed)

        elif chosen_action == 'steal_mp':
            player_ids_alive = [pid for pid in all_ids if pid.isdigit() and int(participants_cache.loc[pid, 'current_hp']) > 0]
            if not player_ids_alive:
                await interaction.channel.send(f"**{participant_name}**이(가) 공격할 대상이 없습니다.", delete_after=3)
                return

            target_id = random_utils.choice(player_ids_alive)
            target = Player(participants_cache.loc[target_id])

            damage = utils.calculate_physical_damage(attacker.stats['physics'], attacker.job, status='정상')
            stolen_mp = random_utils.randint(1, 10)

            initial_hp = target.current_hp
            env_penalty = self._get_environment_penalty(battle_state)
            final_hp = target.take_damage(damage, environment_penalty=env_penalty)
            actual_damage = initial_hp - final_hp

            new_target_mp = max(0, target.current_mp - stolen_mp)
            battle_state['participants_cache'].loc[target_id, 'current_mp'] = new_target_mp
            CombatUtils.update_participant_hp(battle_state, target_id, final_hp)
            
            embed = discord.Embed(
                title=f"MP 흡수 공격!",
                description=f"**{attacker.name}** → **{target.name}**\n{target.name}의 MP가 흡수되었습니다!",
                color=discord.Color.purple()
            )
            embed.add_field(name="데미지", value=f"`{actual_damage}`")
            embed.add_field(name="흡수된 MP", value=f"`{stolen_mp}`")
            embed.add_field(name="남은 HP", value=f"`{final_hp}`")
            await interaction.channel.send(embed=embed)
            
        elif chosen_action == 'magic':
            monster_materia_name = attacker.materia_owned
            materia_info = self.sheet_handler.materia_list_sheet_cache.loc[monster_materia_name]

            materia_target_type = materia_info['target']
            materia_type = materia_info['type']
            power = int(materia_info.get('power', 10))
            magic_effect_value = utils.calculate_magic_power(attacker.stats['magic'], power, attacker.job)
        
            targets_to_process = []
            if materia_target_type in ['ENEMY', 'ALL_ENEMY']:
                alive_players = [pid for pid in player_ids if int(participants_cache.loc[pid, 'current_hp']) > 0]
                if not alive_players:
                    await interaction.channel.send(f"**{attacker.name}**이(가) 마법을 사용하려 했지만 대상이 없습니다.", delete_after=3)
                    return
                if materia_target_type == 'ENEMY':
                    targets_to_process.append(random_utils.choice(alive_players))
                else:
                    targets_to_process = alive_players

            elif materia_target_type in ['ALLY', 'ALL_ALLY']:
                alive_monsters = [mid for mid in monster_ids if int(participants_cache.loc[mid, 'current_hp']) > 0]
                if not alive_monsters:
                    await interaction.channel.send(f"**{attacker.name}**이(가) 마법을 사용하려 했지만 대상이 없습니다.", delete_after=3)
                    return
                if materia_target_type == 'ALLY':
                    targets_to_process.append(random_utils.choice(alive_monsters))
                else:
                    targets_to_process = alive_monsters

            results_desc = ""
            env_penalty = self._get_environment_penalty(battle_state)
            for t_id in targets_to_process:
                target_char = Character(participants_cache.loc[t_id])
                if materia_type == 'DAMAGE':
                    # 최소 데미지 보장
                    actual_damage = max(1, magic_effect_value)
                    final_hp = target_char.take_damage(actual_damage, environment_penalty=env_penalty)
                    results_desc += f"**{target_char.name}**에게 `{actual_damage}`의 데미지! (HP: {final_hp})\n"
                else:
                    is_dead = int(participants_cache.loc[t_id, 'is_dead'])
                    final_hp = target_char.heal(magic_effect_value, is_dead=is_dead)

                    results_desc += f"**{target_char.name}**의 HP `{magic_effect_value}` 회복! (HP: {final_hp})\n"

                CombatUtils.update_participant_hp(battle_state, t_id, final_hp)
                
            embed_color = discord.Color.red() if materia_type == 'DAMAGE' else discord.Color.green()
            embed = discord.Embed(
                title=f"{attacker.name}이(가) {monster_materia_name}을(를) 사용했다!",
                description=materia_info.get('description', '마법 설명이 없습니다.'),
                color=embed_color
            )
            # embed.set_author(name=attacker.name)
            embed.set_author(name= interaction.user.display_name, icon_url=interaction.user.display_avatar)
            embed.add_field(name="효과 적용 결과", value=results_desc, inline=False)
            await interaction.channel.send(embed=embed)
            embed_color = discord.Color.red() if materia_type == 'DAMAGE' else discord.Color.green()
            embed = discord.Embed(
                title=f"{attacker.name}이(가) {monster_materia_name}을(를) 사용했다!",
                description=materia_info.get('description', '마법 설명이 없습니다.'),
                color=embed_color
            )

        elif chosen_action == 'defend':
            # 몬스터의 방어는 연타를 끊지 않음 (연타 리셋 제거)
            battle_state['participants_cache'].loc[current_participant_id, 'defend_flag'] = constants.DEFENSE_DURATION
            embed = discord.Embed(
                title=f"{attacker.name}이(가) 방어 태세를 갖춥니다.",
                description="다음 턴까지 지속됩니다.",
                color=discord.Color.light_grey()
            )
            await interaction.channel.send(embed=embed)

        elif chosen_action == 'evasion':
            # 몬스터의 회피는 연타를 끊지 않음 (연타 리셋 제거)
            battle_state['participants_cache'].loc[current_participant_id, 'evasion_flag'] = constants.EVASION_DURATION
            embed = discord.Embed(
                title=f"{attacker.name}이(가) 회피할 준비를 합니다.",
                description="다음 턴까지 지속됩니다.",
                color=discord.Color.light_grey()
            )
            await interaction.channel.send(embed=embed)

        # DEPRECATED: 이 기능은 라운드 종료 시 _handle_round_end에서 일괄 처리됩니다.
        # 해당 함수는 레거시 코드를 호출하므로 주석 처리합니다.
        # await self.check_boss_skill_trigger(current_participant_id, interaction.channel)
        await asyncio.sleep(1)

        logger.info(f"[MONSTER_ACTION_END] 채널 {channel_id}: 몬스터 {current_participant_id}({attacker.name}) 행동 완료. paused={battle_state.get('paused', False)}")

    async def battle_loop(self, interaction: discord.Interaction):
        """전투의 메인 루프를 관리"""
        channel_id = interaction.channel_id
        battle_state = self.active_battles.get(channel_id)

        if not isinstance(interaction.channel, discord.abc.Messageable):
            logger.error("메시지를 보낼 수 없는 채널에서 전투 루프가 호출되었습니다.")
            if interaction.response.is_done():
                await interaction.followup.send("이 채널에서는 전투를 진행할 수 없습니다.", ephemeral=True)
            else:
                await interaction.response.send_message("이 채널에서는 전투를 진행할 수 없습니다.", ephemeral=True)
            return

        if not battle_state:
            return

        # 동시성 제어: Lock을 사용하여 원자적으로 체크 및 설정
        if channel_id not in self.battle_locks:
            self.battle_locks[channel_id] = asyncio.Lock()

        # Lock 획득 시도 (non-blocking)
        if self.battle_locks[channel_id].locked():
            logger.warning(f"채널 {channel_id}: 이미 전투 루프가 진행 중입니다.")
            if interaction.response.is_done():
                await interaction.followup.send("⚠️ 전투가 이미 진행 중입니다. 잠시만 기다려주세요.", ephemeral=True)
            else:
                await interaction.response.send_message("⚠️ 전투가 이미 진행 중입니다. 잠시만 기다려주세요.", ephemeral=True)
            return

        async with self.battle_locks[channel_id]:
            # 루프 진행 플래그 설정
            battle_state['loop_in_progress'] = True

            if 'pause_event' not in battle_state:
                battle_state['pause_event'] = asyncio.Event()
                battle_state['pause_event'].set()

            if 'paused' not in battle_state:
                battle_state['paused'] = False

            # 전투 루프 시작 (동시성 제어를 위한 try-finally)
            try:
                # 전투 종료 조건이 될 때까지 루프
                while channel_id in self.active_battles:
                    battle_state = self.active_battles.get(channel_id)
                    if not battle_state:
                        return

                    # --- 전투 종료 조건 확인 ---
                    is_end, reason = CombatUtils.check_battle_end(battle_state)
                    if is_end:
                        await BattleManager.handle_battle_end(interaction.channel, reason, channel_id, self.active_battles, self.sheet_handler)
                        return

                    # 참여자 정보 (이후 로직에서 사용)
                    participants_cache = battle_state['participants_cache']
                    all_ids = list(participants_cache.index)
                    player_ids = [p_id for p_id in all_ids if p_id.isdigit()]
                    monster_ids = [p_id for p_id in all_ids if not p_id.isdigit()]

                    # --- 라운드 시작 시 처리 ---
                    if battle_state['current_turn_index'] == 0:
                        await BattleManager.handle_turn_start(battle_state, interaction.channel, channel_id, sheet_handler=self.sheet_handler)

                    order_infos = battle_state['order_infos']
                    current_index = battle_state['current_turn_index']

                    if not order_infos:
                        logger.error(f"채널 {channel_id}: order_infos가 비어있습니다. 순서를 재결정합니다.")
                        BattleManager.determine_turn_order(channel_id)
                        order_infos = battle_state['order_infos']

                        if not order_infos:
                            await interaction.channel.send("❌ 전투 참여자가 없어 전투를 종료합니다.", delete_after=3)
                            del self.active_battles[channel_id]
                            return
                    if current_index >= len(order_infos):
                        logger.info(f"채널 {channel_id}: current_index({current_index})가 order_infos 길이({len(order_infos)})에 도달. 라운드 종료 처리 시작")
                        await self._handle_round_end(interaction, battle_state, channel_id)
                        continue

                    # --- 턴 시작 시 플래그 관리 ---
                    # 현재 턴인 캐릭터의 방어/회피 플래그를 1 감소
                    current_participant_id_for_flag = order_infos[current_index]
                    # 일시정지 체크 (플레이어 턴 시작 전)
                    await self._check_pause(battle_state, interaction.channel, channel_id, current_participant_id_for_flag, is_player=True)

                    for flag_name in ['defend_flag', 'evasion_flag']:
                        current_flag = int(participants_cache.loc[current_participant_id_for_flag, flag_name])
                        if current_flag > 0:
                            participants_cache.loc[current_participant_id_for_flag, flag_name] = max(0, current_flag - 1)
                            # 디버깅 또는 확인용 메시지
                            # await interaction.channel.send(f"`[SYSTEM] {participants_cache.loc[current_participant_id_for_flag, 'character_name']}`의 {flag_name}이 {current_flag_value - 1}로 변경되었습니다.")

                    # --- 상태(status) 지속시간 관리 (Phase 2: StatusEffectManager로 위임) ---
                    await StatusEffectManager.update_status_durations(
                        interaction,
                        battle_state,
                        current_participant_id_for_flag
                    )

                    # 순서가 비어있으면 루프 중단
                    if not order_infos:
                        await interaction.channel.send("전투 참여자가 없어 전투를 종료합니다.", delete_after=3)
                        del self.active_battles[channel_id]
                        return

                    current_participant_id = order_infos[current_index]

                    # 안전하게 참여자 데이터 가져오기
                    participant_data = CombatUtils.safe_get_participant(participants_cache, current_participant_id)
                    if participant_data is None:
                        logger.error(f"채널 {channel_id}: 참여자 '{current_participant_id}'를 찾을 수 없습니다. 턴 스킵.")
                        battle_state['current_turn_index'] += 1
                        continue

                    participant_name = participant_data.get('name')

                    # --- 전투 불능자 순서 스킵 ---
                    old_dead_count = int(participant_data.get('is_dead', 0))
                    if old_dead_count > 0:
                        if current_participant_id.isdigit(): # 플레이어인 경우
                            new_dead_count = max(0, int(participant_data['is_dead']) - 1)
                            battle_state['participants_cache'].loc[current_participant_id, 'is_dead'] = new_dead_count

                            # 개선된 전투불능 메시지
                            if new_dead_count == 0:
                                embed = discord.Embed(
                                    title="💫 부활 가능!",
                                    description=f"**{participant_name}**의 전투불능 상태가 해제되었습니다!\n아군의 힐로 HP를 회복하면 다시 행동할 수 있습니다.",
                                    color=discord.Color.blue()
                                )
                            else:
                                embed = discord.Embed(
                                    title="💀 전투불능",
                                    description=f"**{participant_name}**은(는) 전투불능 상태입니다.\n\n**남은 턴: {new_dead_count}**",
                                    color=discord.Color.dark_gray()
                                )
                        else: # 몬스터인 경우
                            embed = discord.Embed(
                                title="💀 전투불능",
                                description=f"**{participant_name}**은(는) 전투불능 상태입니다.",
                                color=discord.Color.dark_gray()
                            )

                        await interaction.channel.send(embed=embed)
                        battle_state['current_turn_index'] += 1
                        logger.info(f"[SKIP_DEAD] 채널 {channel_id}: {participant_name}({current_participant_id}) 전투불능으로 스킵, index={battle_state['current_turn_index']}/{len(order_infos)}")
                        await asyncio.sleep(1)
                        continue

                    # --- 행동 처리 ---
                    # embed = discord.Embed(
                    #     title=f"제{battle_state['order_count']}턴",
                    #     description=f"**{participant_name}**의 차례입니다."
                    # )
                    # await interaction.channel.send(embed=embed)

                    # 플레이어 vs 몬스터 턴 처리
                    if current_participant_id.isdigit():
                        # 플레이어 턴 처리 (분리된 함수 호출)
                        turn_success = await self._handle_player_turn(
                            interaction,
                            battle_state,
                            current_participant_id,
                            participant_data,
                            all_ids
                        )

                        if not turn_success:
                            battle_state['current_turn_index'] += 1
                            continue


                    # 몬스터 순서 처리
                    else:
                        # 몬스터 턴 처리 (분리된 함수 호출)
                        await self._handle_monster_turn(
                            interaction,
                            battle_state,
                            channel_id,
                            current_participant_id,
                            participant_data,
                            all_ids
                        )

                    # --- 다음 순서로 ---
                    battle_state['current_turn_index'] += 1
                    logger.info(f"[TURN_INDEX_UPDATE] 채널 {channel_id}: current_turn_index 업데이트 -> {battle_state['current_turn_index']}")

                    # --- 라운드 종료 및 동기화 처리 ---
                    if battle_state['current_turn_index'] >= len(order_infos):
                        await self._handle_round_end(interaction, battle_state, channel_id)

                    if battle_state.get('order_count', 1) > constants.MAX_COMBAT_ROUNDS:
                        await interaction.channel.send(f"전투가 {constants.MAX_COMBAT_ROUNDS}라운드를 초과하여 자동으로 종료됩니다.")
                        del self.active_battles[channel_id]
                        return

                # else:
                # while 루프가 정상적으로 종료되지 않고, active_battles에서 채널 ID가 삭제되어 종료된 경우
                # (예: /전투종료 커맨드)

            finally:
                # 전투 루프 종료 시 플래그 해제
                if battle_state:
                    battle_state['loop_in_progress'] = False
                    logger.info(f"채널 {channel_id}: 전투 루프 종료, 동시성 플래그 해제")

    @app_commands.command(name="전투일시정지", description="현재 또는 지정한 채널의 전투를 일시정지합니다.")
    @app_commands.describe(channel_id="일시정지할 전투가 진행 중인 채널/스레드 ID (선택사항)")
    async def pause_battle(self, interaction: discord.Interaction, channel_id: str = None):
        """
        전투를 일시정지합니다.
        channel_id를 지정하지 않으면 현재 채널의 전투를 정지합니다.
        """
        # 대상 채널 ID 결정
        target_channel_id = int(channel_id) if channel_id else interaction.channel_id
        
        # 전투 진행 여부 확인
        if target_channel_id not in self.active_battles:
            await interaction.response.send_message(
                f"❌ 채널 ID `{target_channel_id}`에서 진행 중인 전투가 없습니다.",
                ephemeral=True
            )
            return
        
        battle_state = self.active_battles[target_channel_id]
        
        # ✅ pause_event 존재 여부 확인 (방어 코드)
        if 'pause_event' not in battle_state or battle_state['pause_event'] is None:
            battle_state['pause_event'] = asyncio.Event()
            battle_state['pause_event'].set()
            logger.warning(f"채널 {target_channel_id}: pause_event가 없어 새로 생성함")
        
        # 이미 일시정지 상태인지 확인
        if battle_state.get('paused', False):
            await interaction.response.send_message(
                f"⚠️ 채널 ID `{target_channel_id}`의 전투는 이미 일시정지 상태입니다.",
                ephemeral=True
            )
            return
        
        # 일시정지 플래그 설정
        battle_state['paused'] = True
        battle_state['pause_event'].clear()  # pause_event를 clear 상태로 변경 (wait 대기 상태)
        logger.info(f"[PAUSE] 채널 {target_channel_id}: 일시정지 요청됨. paused={battle_state['paused']}, event_cleared=True")
        
        await interaction.response.send_message(content="---", ephemeral= True)

        
    @app_commands.command(name="전투재개", description="일시정지된 전투를 재개합니다.")
    @app_commands.describe(channel_id="재개할 전투가 진행 중인 채널/스레드 ID (선택사항)")
    async def resume_battle(self, interaction: discord.Interaction, channel_id: str = None):
        """
        일시정지된 전투를 재개합니다.
        channel_id를 지정하지 않으면 현재 채널의 전투를 재개합니다.
        """
        # 대상 채널 ID 결정
        target_channel_id = int(channel_id) if channel_id else interaction.channel_id
        
        # 전투 진행 여부 확인
        if target_channel_id not in self.active_battles:
            await interaction.response.send_message(
                f"❌ 채널 ID `{target_channel_id}`에서 진행 중인 전투가 없습니다.",
                ephemeral=True
            )
            return
        
        battle_state = self.active_battles[target_channel_id]
        
        # 일시정지 상태가 아닌 경우
        if not battle_state.get('paused', False):
            await interaction.response.send_message(
                f"❌ 채널 ID `{target_channel_id}`의 전투는 일시정지 상태가 아닙니다.",
                ephemeral=True
            )
            return
        
        # 일시정지 해제
        await interaction.response.send_message(content="---", ephemeral= True)
        battle_state['paused'] = False
        battle_state['pause_event'].set()  # asyncio.Event를 set 상태로 변경 (대기 해제)
        logger.info(f"[RESUME] 채널 {target_channel_id}: 전투 재개됨. paused={battle_state['paused']}, event_set=True")

    @app_commands.command(name="전투목록", description="현재 진행 중인 모든 전투를 조회합니다.")
    async def list_battles(self, interaction: discord.Interaction):
        """진행 중인 전투 목록을 표시합니다."""
        if not self.active_battles:
            await interaction.response.send_message("현재 진행 중인 전투가 없습니다.", ephemeral=True)
            return
        
        embed = discord.Embed(
            title="진행 중인 전투 목록",
            color=discord.Color.blue()
        )
        
        for channel_id, battle_state in self.active_battles.items():
            channel = self.bot.get_channel(channel_id)
            channel_name = channel.id if channel else f"알 수 없는 채널 (ID: {channel_id})"

            participants = list(battle_state['participants_cache'].index)
            participant_names = [battle_state['participants_cache'].loc[p_id, 'name'] for p_id in participants[:5]]
            
            status = "⏸️ 일시정지" if battle_state.get('paused', False) else "▶️ 진행 중"
            
            embed.add_field(
                name=f"{status} | {channel_name}",
                value=f"**채널 ID**: `{channel_id}`\n**참여자**: {', '.join(participant_names)}{'...' if len(participants) > 5 else ''}\n**라운드**: {battle_state.get('order_count', 1)}",
                inline=False
            )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def send_monster_damage_reaction(self, monster_id: str, channel: discord.abc.Messageable, damage: int):
        """
        몬스터의 피격 반응 메시지를 전송합니다 (45% 확률). Phase 3.2
        일반 텍스트로 간소화

        Args:
            monster_id: 몬스터 ID
            channel: 메시지를 전송할 채널
            damage: 받은 데미지 (메시지에 표시용)
        """
        # 45% 확률 체크는 utils.get_monster_damage_reaction 내부에서 처리됨
        reaction = utils.get_monster_damage_reaction(monster_id, self.sheet_handler.monsters_sheet_cache)

        if not reaction:
            return


        # 채널이 TextChannel인지 확인
        if not isinstance(channel, discord.TextChannel):
            logger.warning(f"채널 타입 오류: {type(channel).__name__}")
            return

        battle_state = self.active_battles.get(channel.id)
        if not battle_state:
            return


        participants_cache = battle_state['participants_cache']
        if monster_id not in participants_cache.index:
            return

        monster_data = participants_cache.loc[monster_id]
        monster_name = monster_data.get('name', '???')

        # 일반 텍스트로 간소화
        await channel.send(f"**{monster_name}**: {reaction['message']}")

    async def check_boss_skill_trigger(self, monster_id: str, channel: discord.TextChannel):
        """보스 HP 조건 확인 후 Boss_Skills 시트의 스킬 발동

        DEPRECATED: BossSkillSystem.check_boss_skill_trigger() 사용을 권장합니다.
        """
        await BossSkillSystem.check_boss_skill_trigger(
            monster_id,
            channel,
            self.active_battles,
            self.sheet_handler
        )

    async def help_command(self, interaction: discord.Interaction, option: app_commands.Choice[str] = None):
        """모든 명령어의 사용법을 표시합니다."""
        await interaction.response.defer(ephemeral=True)

        # 기본 Embed 생성
        embed = discord.Embed(
            title="명령어 도움말",
            color=discord.Color.blue()
        )

        selected = option.value if option else "quickstart"

        # 빠른 시작 가이드
        if selected == "quickstart":
            embed.title = "🏁 빠른 시작 가이드"
            embed.description = "라이프 스트림을 여행하는 히치하이커를 위한 PHS"

            embed.add_field(
                name="1️⃣ 캐릭터 만들기",
                value=(
                    "**`/등록`** 명령어로 캐릭터를 생성합니다.\n"
                    "• **스트라이커**: 물리 공격 특화 (근력 높음)\n"
                    "• **마테리아 위버**: 마법 사용 특화 (마법 높음)\n\n"
                    "등록 후 **보너스 포인트 10점**을 받습니다."
                ),
                inline=False
            )

            embed.add_field(
                name="2️⃣ 스탯 분배하기",
                value=(
                    "**`/스탯분배`** 명령어로 보너스 포인트를 분배하세요.\n"
                    "• **근력**: HP 증가, 물리 공격력 상승\n"
                    "• **마법**: MP 증가, 마법 위력 상승\n"
                    "• **민첩**: 턴 순서 우선, 회피율 증가\n"
                    "• **매력**: DMW(슬롯머신) 성공률 상승"
                ),
                inline=False
            )

            embed.add_field(
                name="3️⃣ 전투 준비하기",
                value=(
                    "**`/리스트`**로 채널의 플레이어/몬스터 ID를 확인하세요.\n"
                    "**`/전투시작 participants:[ID들]`**로 전투를 시작합니다.\n"
                    "예시: `/전투시작 participants:123456, 789012, monster_goblin`"
                ),
                inline=False
            )

            embed.add_field(
                name="4️⃣ 전투 중 행동",
                value=(
                    "자신의 턴이 되면 행동 버튼이 표시됩니다:\n"
                    "⚔️ **물리공격**: 근력 기반 단일 공격\n"
                    "✨ **마법**: 마테리아 필요, 다양한 효과\n"
                    "💥 **리미트 브레이크**: 강력한 필살기\n"
                    "🛡️ **방어**: 데미지 경감\n"
                    "💨 **회피**: 민첩 10 이상 필요, 공격 회피"
                ),
                inline=False
            )

            embed.add_field(
                name="5️⃣ DMW 시스템",
                value=(
                    "매 턴마다 **DMW(Digital Mind Wave)** 슬롯이 돌아갑니다!\n"
                    "• 3개가 일치하면 특수 효과 발동\n"
                    "• **세피로스**: 전체 적 공격\n"
                    "• **앤질**: 리미트 브레이크 재사용 가능\n"
                    "• **라켈**: 전체 아군 회복\n"
                    "• 매력 스탯이 높을수록 성공 확률 증가\n"
                    "이외에도 여러 DMW가 있습니다."
                ),
                inline=False
            )

            embed.add_field(
                name="유용한 팁",
                value=(
                    "• **`/내상태`**로 현재 HP/MP 확인\n"
                    "• **`/인벤토리`**로 마테리아 관리\n"
                    "• **`/전투현황`**로 현재 전투 상태 확인\n"
                    "• 자세한 설명: `/도움말 option:[카테고리]`"
                ),
                inline=False
            )

        # 캐릭터 관리 명령어
        elif selected == "character":
            embed.title = "캐릭터 관리 명령어"
            embed.description = "캐릭터 생성, 스탯 관리와 관련된 명령어입니다."

            embed.add_field(
                name="캐릭터 생성 & 조회",
                value=(
                    "**`/등록`**\n"
                    "• 새로운 캐릭터를 생성합니다\n"
                    "• 직업 선택: 스트라이커 또는 마테리아 위버\n\n"
                    "**`/내상태`**\n"
                    "• 현재 캐릭터의 모든 정보를 확인합니다\n"
                    "• HP, MP, 스탯, 장착 마테리아 등"
                ),
                inline=False
            )

            embed.add_field(
                name="스탯 관리",
                value=(
                    "**`/스탯분배 stat:[스탯] points:[숫자]`**\n"
                    "• 보너스 포인트를 스탯에 분배합니다\n"
                    "• 스탯 종류: 근력, 마법, 민첩, 매력\n"
                    "• 등록 시 10점의 보너스 포인트 지급"
                ),
                inline=False
            )

            embed.add_field(
                name="스탯 효과",
                value=(
                    "**근력**: HP 증가 (+3 근력 = +10 HP), 물리 공격력 ↑\n"
                    "**마법**: MP 증가 (+1 마법 = +10 MP), 마법 위력 ↑\n"
                    "**민첩**: 턴 순서 우선, 회피율 ↑\n"
                    "**매력**: DMW 성공률 ↑"
                ),
                inline=False
            )

        # 전투 시스템
        elif selected == "combat":
            embed.title = "전투 시스템 명령어"
            embed.description = "전투 시작, 관리와 관련된 명령어입니다."

            embed.add_field(
                name="전투 관리",
                value=(
                    "**`/전투시작 participants:[ID] m_no:[음악번호]`**\n"
                    "• 전투를 시작합니다\n"
                    "• participants: 쉼표로 구분된 ID\n"
                    "• m_no: 0(랜덤) 또는 1~N(지정)\n\n"
                    "**`/난입 intruder_id:[ID]`**\n"
                    "• 진행 중인 전투에 난입합니다\n\n"
                    "**`/전투종료`** | **`/전투일시정지`** | **`/전투재개`**\n"
                    "• 전투를 강제 종료/일시정지/재개합니다\n\n"
                    "**`/전투목록`** | **`/전투현황`**\n"
                    "• 진행 중 전투 조회 및 상세 현황 확인"
                ),
                inline=False
            )

            embed.add_field(
                name="전투 중 행동",
                value=(
                    "⚔️ **물리공격**: (근력-10)/2 + 1d10 데미지\n"
                    "✨ **마법**: 마테리아 장착 필요, MP 소모\n"
                    "💥 **리미트 브레이크**: 직업별 강력한 필살기\n"
                    "🛡️ **방어**: (근력-10)/2 + 1d10 데미지 경감\n"
                    "💨 **회피**: 민첩 10 이상 필요, 완전 회피"
                ),
                inline=False
            )

            embed.add_field(
                name="DMW 시스템",
                value=(
                    "매 턴마다 **슬롯머신**이 돌아갑니다!\n"
                    "• 계산식: (매력-10)/2 + 4d10\n"
                    "• 7 = 세피로스 (전체 공격)\n"
                    "• 30 이상 = 다른 피규어 랜덤\n\n"
                    "**효과**: 세피로스(전체 공격), 앤질(리미트 재사용), 잭스(근력+1), 라켈(전체 회복) 등"
                ),
                inline=False
            )

            embed.add_field(
                name="🎲 고급 전투 시스템",
                value=(
                    "**환경 효과** (20% 확률)\n"
                    "☢️ 마황 지대: HP -5%/턴\n"
                    "⚡ 라이프스트림: MP +3/턴\n"
                    "🌀 중력 이상: 회피율 -50%\n"
                    "⏰ 시간 왜곡: 턴 순서 무작위\n\n"
                    "**연계 공격**: 2연타 +20%, 3연타 +50% 데미지\n"
                    "**보스전**: `/도움말 option: 보스전 가이드` 참조"
                ),
                inline=False
            )

        # 보스전 가이드
        elif selected == "boss":
            embed.title = "보스전 가이드"
            embed.description = (
                "보스는 일반 몬스터보다 강력하며, 특수한 스킬과 메커니즘을 가지고 있습니다.\n"
                "자세한 내용은 `boss_battle.md` 문서를 참조하세요."
            )

            embed.add_field(
                name="보스 특징",
                value=(
                    "• **높은 HP/스탯**: 일반 몬스터의 수배\n"
                    "• **보스 스킬**: HP 조건 달성 시 자동 발동\n"
                    "• **페이즈 전환**: HP가 일정 이하로 떨어지면 패턴 변화\n"
                    "• **특수 메커니즘**: 소환, 공생, 버프/디버프 등\n"
                    "• **강력한 보상**: 경험치, 레어 마테리아 획득 가능"
                ),
                inline=False
            )

            embed.add_field(
                name="보스 스킬 시스템",
                value=(
                    "보스는 **HP 임계값**에 도달하면 강력한 스킬을 사용합니다:\n\n"
                    "**트리거 방식**\n"
                    "• HP 80% 이하: Phase 1 스킬 발동\n"
                    "• HP 50% 이하: Phase 2 스킬 발동\n"
                    "• HP 20% 이하: 최종 발악 스킬\n\n"
                    "**주요 스킬 타입**\n"
                    "💀 **죽음의 선고**: 3턴 후 즉사 (tokura 상태)\n"
                    "👥 **소환**: 새로운 몬스터 추가 소환\n"
                    "💥 **전체 공격**: 모든 플레이어에게 대미지\n"
                    "🩸 **생명력 흡수**: 데미지 + HP 회복\n"
                    "⚡ **MP 흡수**: 데미지 + MP 감소\n"
                    "📉 **스탯 디버프**: 근력/마법/민첩 감소\n"
                    "💪 **자기 강화**: 공격력/방어력 증가"
                ),
                inline=False
            )

        # 마테리아
        elif selected == "materia":
            embed.title = "마테리아 시스템"
            embed.description = "마테리아 장착 및 관리 명령어입니다."

            embed.add_field(
                name="마테리아 관리",
                value=(
                    "**`/인벤토리`**\n"
                    "• 소유한 마테리아 목록을 확인합니다\n\n"
                    "**`/마테리아`**\n"
                    "• 마테리아를 장착하거나 해제합니다\n"
                    "• 장착: 인벤토리에서 드롭다운으로 선택\n"
                    "• 자동 교체: 이미 장착된 경우 자동으로 인벤토리로 복귀\n"
                    "• 해제: 장착 중인 마테리아를 인벤토리로 복귀"
                ),
                inline=False
            )

            embed.add_field(
                name="마테리아 사용법",
                value=(
                    "• 한 번에 **하나의 마테리아만** 장착 가능\n"
                    "• 전투 중에는 장착/해제 불가\n"
                    "• 마법 사용 시 **MP 소모**\n"
                    "• 마테리아 위버는 마법 위력 +1d5 보너스"
                ),
                inline=False
            )

            embed.add_field(
                name="마테리아 종류",
                value=(
                    "**DAMAGE**: 적 대상 공격 마법\n"
                    "• ENEMY: 단일 적\n"
                    "• ALL_ENEMY: 모든 적\n\n"
                    "**HEAL**: 아군 대상 회복 마법\n"
                    "• ALLY: 단일 아군\n"
                    "• ALL_ALLY: 모든 아군\n\n"
                    "위력: (마법-10)/2 + 1dPower + (위버시 +1d5)"
                ),
                inline=False
            )

        # 유틸리티
        elif selected == "utility":
            embed.title = "유틸리티 명령어"
            embed.description = "편의 기능 및 관리 명령어입니다."

            embed.add_field(
                name="데이터 동기화",
                value=(
                    "**`/시트갱신 option:[g2cache/cache2g]`**\n"
                    "• 구글 시트 ↔ 봇 캐시 동기화\n"
                    "• **g2cache**: 시트 → 봇 (데이터 불러오기)\n"
                    "• **cache2g**: 봇 → 시트 (데이터 저장)\n\n"
                    "**`/캐시확인 sheet_name:[시트] user_id:[ID]`**\n"
                    "• 현재 캐시 데이터를 확인합니다"
                ),
                inline=False
            )
        # 전체 명령어
        else:
            embed.description = (
                "**PHS 명령어 목록**\n"
                "자세한 설명을 보려면 `/도움말 option:[카테고리]`를 사용하세요.\n\n"
                "처음 사용하신다면 **🏁 빠른 시작 가이드**를 확인하세요!"
            )

            embed.add_field(
                name="👤 캐릭터 관리",
                value=(
                    "`/등록` `/내상태` `/스탯분배`"
                ),
                inline=False
            )


            embed.add_field(
                name="⚔️ 전투 시스템",
                value=(
                    "`/전투시작` `/난입` `/전투종료`\n"
                    "`/전투일시정지` `/전투재개` `/전투목록` `/전투현황`"
                ),
                inline=False
            )


            embed.add_field(
                name="💎 마테리아",
                value=(
                    "`/인벤토리` `/마테리아장착` `/마테리아해제`"
                ),
                inline=False
            )

            embed.add_field(
                name="🔧 유틸리티",
                value=(
                    "`/시트갱신` `/캐시확인` `/리스트` `/프렐류드`"
                ),
                inline=False
            )

            embed.add_field(
                name="📚 카테고리별 상세 가이드",
                value=(
                    "🏁 `/도움말 option:빠른 시작 가이드` - 처음 사용자용\n"
                    "👤 `/도움말 option:캐릭터 관리` - 캐릭터 생성/관리\n"
                    "⚔️ `/도움말 option:전투 시스템` - 전투 룰 & DMW\n"
                    "💎 `/도움말 option:마테리아` - 마테리아 사용법\n"
                    "🔧 `/도움말 option:유틸리티` - 편의 기능"
                ),
                inline=False
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="디버그", description="[개발자] 시스템 상태를 확인합니다.")
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.describe(target="확인할 대상")
    @app_commands.choices(target=[
        app_commands.Choice(name="보스 스킬 캐시", value="boss_skills"),
        app_commands.Choice(name="전투 상태", value="battles"),
        app_commands.Choice(name="캐시 전체", value="all_cache"),
        app_commands.Choice(name="역할 확인", value="role_check"),
    ])
    async def debug_command(self, interaction: discord.Interaction, target: app_commands.Choice[str]):
        """개발자용 디버그 명령어"""
        await interaction.response.defer(ephemeral=True)
        
        if target.value == "boss_skills":
            cache = self.sheet_handler.boss_skills_sheet_cache
            
            if cache.empty:
                await interaction.followup.send("❌ Boss_Skills 캐시가 비어있습니다.", ephemeral=True)
                return
            
            # DataFrame을 문자열로 변환
            cache_info = f"```\n{cache.to_string()}\n```"
            
            # Discord 메시지 길이 제한 (2000자)
            if len(cache_info) > 1900:
                cache_info = f"```\n{cache.head(10).to_string()}\n... (총 {len(cache)}개 행)\n```"
            
            embed = discord.Embed(
                title="🔍 Boss_Skills 캐시",
                description=cache_info,
                color=discord.Color.blue()
            )
            embed.add_field(name="총 스킬 수", value=str(len(cache)), inline=True)
            embed.add_field(name="보스 수", value=str(len(cache.index.get_level_values(0).unique())), inline=True)
            
            await interaction.followup.send(embed=embed, ephemeral=True)
        
        elif target.value == "battles":
            if not self.active_battles:
                await interaction.followup.send("❌ 진행 중인 전투가 없습니다.", ephemeral=True)
                return
            
            embed = discord.Embed(
                title="🔍 전투 상태",
                color=discord.Color.green()
            )
            
            for channel_id, battle_state in self.active_battles.items():
                participants = list(battle_state['participants_cache'].index)
                embed.add_field(
                    name=f"채널 ID: {channel_id}",
                    value=f"참여자: {len(participants)}명\n라운드: {battle_state.get('order_count', 1)}",
                    inline=False
                )
            
            await interaction.followup.send(embed=embed, ephemeral=True)
        
        elif target.value == "all_cache":
            embed = discord.Embed(
                title="🔍 전체 캐시 상태",
                color=discord.Color.purple()
            )
            
            embed.add_field(
                name="Characters",
                value=f"{len(self.sheet_handler.characters_sheet_cache)}명",
                inline=True
            )
            embed.add_field(
                name="Monsters",
                value=f"{len(self.sheet_handler.monsters_sheet_cache)}개",
                inline=True
            )
            embed.add_field(
                name="Materia",
                value=f"{len(self.sheet_handler.materia_list_sheet_cache)}개",
                inline=True
            )
            embed.add_field(
                name="Boss Skills",
                value=f"{len(self.sheet_handler.boss_skills_sheet_cache)}개",
                inline=True
            )
            embed.add_field(
                name="Music",
                value=f"{len(self.sheet_handler.ost_sheet_cache)}개",
                inline=True
            )
            
            await interaction.followup.send(embed=embed, ephemeral=True)

        elif target.value == "role_check":
            await interaction.followup.send(f"{interaction.user.guild.roles}", ephemeral=True)


    @app_commands.command(name="전투현황", description="현재 전투의 상세 정보를 확인합니다.")
    async def battle_status(self, interaction: discord.Interaction):
        """현재 채널의 전투 상태를 상세히 표시합니다."""
        await interaction.response.defer(ephemeral=True)
        
        channel_id = interaction.channel_id
        if channel_id not in self.active_battles:
            await interaction.followup.send("❌ 이 채널에서 진행 중인 전투가 없습니다.", ephemeral=True)
            return
        
        battle_state = self.active_battles[channel_id]
        participants_cache = battle_state['participants_cache']
        
        all_ids = list(participants_cache.index)
        player_ids = [p_id for p_id in all_ids if p_id.isdigit()]
        monster_ids = [p_id for p_id in all_ids if not p_id.isdigit()]
        
        embed = discord.Embed(
            title=f"전투 현황 - 제{battle_state.get('order_count', 1)}턴",
            color=discord.Color.gold()
        )
        
        # 환경 효과
        env_effect = battle_state.get('environment_effect')
        if env_effect:
            env_names = {
                'lava_zone': '☢️ 마황 지대',
                'mana_storm': '⚡ 라이프 스트림 폭풍',
                'gravity_anomaly': '🌀 중력 이상',
                'time_warp': '⏰ 시간 왜곡'
            }
            embed.add_field(
                name="🌍 환경 효과",
                value=env_names.get(env_effect, '알 수 없음'),
                inline=False
            )
        
        # 아군 상태
        player_info = ""
        for p_id in player_ids:
            p_data = participants_cache.loc[p_id]
            name = p_data['name']
            hp = int(p_data['current_hp'])
            max_hp = int(p_data['max_hp'])
            mp = int(p_data['current_mp'])
            max_mp = int(p_data['max_mp'])
            limit_flag = int(p_data.get('limit_flag', 1))
            is_dead = int(p_data['is_dead'])
            materia = p_data.get('materia_owned', '없음')
            
            status_icon = "💀" if is_dead > 0 else "💚"
            limit_status = "🔓" if limit_flag == 0 else "🔒"
            
            player_info += f"{status_icon} **{name}**\n"
            player_info += f"  HP: `{hp}/{max_hp}` | MP: `{mp}/{max_mp}`\n"
            player_info += f"  마테리아: {materia} | 리미트: {limit_status}\n"
            if is_dead > 0:
                player_info += f"  ⚠️ 전투불능 {is_dead}턴\n"
            player_info += "\n"
        
        embed.add_field(name="👥 아군 상태", value=player_info or "없음", inline=False)
        
        # 적 상태
        monster_info = ""
        for m_id in monster_ids:
            m_data = participants_cache.loc[m_id]
            name = m_data['name']
            hp = int(m_data['current_hp'])
            max_hp = int(m_data['max_hp'])
            monster_type = m_data.get('monster_type', 'normal')
            
            type_icon = "👑" if monster_type == 'boss' else "⚔️"
            status_icon = "💀" if hp <= 0 else "🔴"
            hp_percent = (hp / max_hp * 100) if max_hp > 0 else 0
            
            monster_info += f"{type_icon} {status_icon} **{name}**\n"
            monster_info += f"  HP: `{hp}/{max_hp}` ({hp_percent:.1f}%)\n"
            
            # 보스 스킬 상태 확인
            if monster_type == 'boss':
                boss_skills = self.sheet_handler.get_boss_skills(m_id)
                if not boss_skills.empty:
                    skills_status = ""
                    for idx, skill in boss_skills.iterrows():
                        skill_name = skill.get('skill_name', '???')
                        trigger_hp = skill.get('trigger_hp_percent', 0)
                        used_flag = int(skill.get('used_flag', 0)) if skill.get('used_flag', 0) != '' else 0
                        
                        if used_flag == 1:
                            skills_status += f"    ✅ {skill_name} (사용됨)\n"
                        elif hp_percent <= trigger_hp:
                            skills_status += f"    ⚠️ {skill_name} (발동 가능)\n"
                        else:
                            skills_status += f"    ⏳ {skill_name} (HP {trigger_hp}% 이하)\n"
                    
                    if skills_status:
                        monster_info += f"  📜 보스 스킬:\n{skills_status}"
            
            monster_info += "\n"
        
        embed.add_field(name=" 적 상태", value=monster_info or "없음", inline=False)
        
        # 행동 순서
        order_names = []
        for p_id in battle_state['order_infos']:
            p_name = participants_cache.loc[p_id, 'name']
            if battle_state['order_infos'][battle_state['current_turn_index']] == p_id:
                order_names.append(f"**[{p_name}]** ←")
            else:
                order_names.append(p_name)
        
        embed.add_field(
            name="🔄 행동 순서",
            value=" → ".join(order_names),
            inline=False
        )
        
        await interaction.followup.send(embed=embed, ephemeral=True)

    # ==================== Phase 1: 보스 스킬 시스템 ====================

    async def _check_boss_skill_triggers(
        self,
        interaction: discord.Interaction,
        battle_state: Dict[str, Any]
    ) -> None:
        """모든 보스 몬스터의 HP 트리거 체크 및 스킬 발동

        DEPRECATED: BossSkillSystem.check_boss_skill_triggers() 사용을 권장합니다.

        Args:
            interaction: Discord 인터랙션
            battle_state: 현재 전투 상태
        """
        await BossSkillSystem.check_boss_skill_triggers(
            interaction,
            battle_state,
            self.sheet_handler
        )

    def _create_boss_skill_embed(
        self,
        boss_name: str,
        skill_name: str,
        description: str,
        effect_type: str,
        skill_data: pd.Series
    ) -> discord.Embed:
        """보스 스킬용 임베드 생성

        DEPRECATED: BossSkillSystem.create_boss_skill_embed() 사용을 권장합니다.

        Args:
            boss_name: 보스 이름
            skill_name: 스킬 이름
            description: 스킬 설명
            effect_type: 효과 타입
            skill_data: 스킬 데이터

        Returns:
            discord.Embed: 생성된 임베드
        """
        # effect_type별 아이콘과 색상
        effect_config = {
            'summon': {'emoji': '🌀', 'color': discord.Color.purple(), 'type_name': '소환'},
            'damage_mp_drain': {'emoji': '🔮', 'color': discord.Color.blue(), 'type_name': 'MP 흡수'},
            'lifesteal': {'emoji': '🩸', 'color': discord.Color.dark_red(), 'type_name': '생명력 흡수'},
            'stat_debuff': {'emoji': '💀', 'color': discord.Color.dark_gray(), 'type_name': '디버프'},
            'buff_self': {'emoji': '⚡', 'color': discord.Color.gold(), 'type_name': '버프'},
            'damage_all': {'emoji': '💥', 'color': discord.Color.red(), 'type_name': '광역 공격'}
        }

        config = effect_config.get(effect_type, {'emoji': '⚔️', 'color': discord.Color.dark_red(), 'type_name': '특수 스킬'})

        embed = discord.Embed(
            title=f"{config['emoji']} {boss_name}의 {skill_name}!",
            description=f">>> {description}",
            color=config['color']
        )

        # 스킬 타입 표시
        embed.add_field(
            name="스킬 타입",
            value=f"`{config['type_name']}`",
            inline=True
        )

        # 추가 정보 표시
        if effect_type == 'summon':
            import json
            value = json.loads(skill_data.get('value', '{}'))
            monster_ids = value.get('monster_ids', [])
            embed.add_field(
                name="소환 수",
                value=f"`{len(monster_ids)}마리`",
                inline=True
            )
        elif effect_type in ['damage_all', 'damage_mp_drain', 'lifesteal']:
            damage = int(skill_data.get('damage', 0))
            if damage > 0:
                embed.add_field(
                    name="피해량",
                    value=f"`{damage} DMG`",
                    inline=True
                )
        elif effect_type == 'stat_debuff':
            duration = int(skill_data.get('duration', 0))
            if duration > 0:
                embed.add_field(
                    name="지속 시간",
                    value=f"`{duration}턴`",
                    inline=True
                )
        elif effect_type == 'buff_self':
            duration = int(skill_data.get('duration', 0))
            import json
            value = json.loads(skill_data.get('value', '{}'))
            stat = value.get('stat', '알 수 없음')
            stat_names = {
                'physics': '근력',
                'magic': '마법',
                'agility': '민첩',
                'charm': '매력'
            }
            embed.add_field(
                name="강화 스탯",
                value=f"`{stat_names.get(stat, stat)}`",
                inline=True
            )
            if duration > 0:
                embed.add_field(
                    name="지속 시간",
                    value=f"`{duration}턴`" if duration < 100 else "`영구`",
                    inline=True
                )

        embed.set_footer(text="⚠️ 보스 스킬 발동!")

        return embed

    async def _execute_boss_skill(
        self,
        interaction: discord.Interaction,
        battle_state: Dict[str, Any],
        boss_id: str,
        skill_id: str,
        skill_data: pd.Series
    ) -> None:
        """보스 스킬 실행

        DEPRECATED: BossSkillSystem.execute_boss_skill() 사용을 권장합니다.

        Args:
            interaction: Discord 인터랙션
            battle_state: 현재 전투 상태
            boss_id: 보스 ID
            skill_id: 스킬 ID
            skill_data: 스킬 데이터 (Series)
        """
        await BossSkillSystem.execute_boss_skill(
            interaction,
            battle_state,
            boss_id,
            skill_id,
            skill_data,
            self.sheet_handler,
            CombatUtils
        )

    def _select_targets_by_type(
        self,
        battle_state: Dict[str, Any],
        target_type: str,
        skill_user_id: str
    ) -> List[str]:
        """target_type에 따라 대상 ID 리스트 반환

        DEPRECATED: BossSkillSystem.select_targets_by_type() 사용을 권장합니다.

        Args:
            battle_state: 전투 상태
            target_type: 대상 타입
            skill_user_id: 스킬 사용자 ID

        Returns:
            대상 ID 리스트
        """
        return BossSkillSystem.select_targets_by_type(battle_state, target_type, skill_user_id)

    async def _execute_summon(
        self,
        battle_state: Dict[str, Any],
        skill_data: pd.Series,
        boss_id: str
    ) -> str:
        """몬스터 소환 실행

        DEPRECATED: BossSkillSystem.execute_summon() 사용을 권장합니다.
        """
        return await BossSkillSystem.execute_summon(
            battle_state, skill_data, boss_id, self.sheet_handler, CombatUtils
        )

    async def _execute_summon_legacy(
        self,
        battle_state: Dict[str, Any],
        skill_data: pd.Series,
        boss_id: str
    ) -> str:
        """몬스터 소환 실행 (레거시 구현)"""
        value = json.loads(skill_data['value'])
        monster_ids = value.get('monster_ids', [])

        summoned_names = []
        for monster_id in monster_ids:
            # 이미 전투에 참여 중인지 확인
            if monster_id in battle_state['participants_cache'].index:
                continue

            # 전투에 몬스터 추가
            new_participant = self.sheet_handler.add_participant_to_battle(monster_id)
            if new_participant is not None:
                summoned_names.append(new_participant['name'])
                battle_state['participants_cache'] = self.sheet_handler.battle_stat_sheet_cache

        # 턴 순서 재계산
        if summoned_names:
            battle_state['order_infos'] = self.calculate_turn_order(
                battle_state['participants_cache']
            )

        # 결과 메시지
        if summoned_names:
            # Phase 3.3: 소환 성공 로깅
            logger.info(f"[보스 스킬 - 소환] {boss_id}가 {len(summoned_names)}마리 소환: {', '.join(monster_ids)}")
            return f"**소환!** {', '.join(summoned_names)}이(가) 전투에 참여했다!"
        else:
            logger.warning(f"[보스 스킬 - 소환] {boss_id}의 소환 실패: {', '.join(monster_ids)}")
            return "소환 실패... (이미 참여 중이거나 존재하지 않는 몬스터)"

    async def _execute_damage_mp_drain(
        self,
        battle_state: Dict[str, Any],
        skill_data: pd.Series,
        targets: List[str],
        boss_id: str
    ) -> str:
        """데미지 + MP 흡수 실행

        DEPRECATED: BossSkillSystem.execute_damage_mp_drain() 사용을 권장합니다.
        """
        return await BossSkillSystem.execute_damage_mp_drain(
            battle_state, skill_data, targets, boss_id, CombatUtils
        )

    async def _execute_damage_mp_drain_legacy(
        self,
        battle_state: Dict[str, Any],
        skill_data: pd.Series,
        targets: List[str],
        boss_id: str
    ) -> str:
        """데미지 + MP 흡수 실행 (레거시 구현)"""
        value = json.loads(skill_data['value'])
        mp_drain_percent = value.get('mp_drain_percent', 0) / 100.0

        participants = battle_state['participants_cache']
        damage = int(skill_data['damage'])

        results = []
        reaction_messages = []

        for target_id in targets:
            # 데미지 적용
            actual_damage, final_hp = CombatUtils.apply_damage_to_target(
                battle_state, target_id, damage
            )

            # MP 흡수
            current_mp = int(participants.loc[target_id, 'current_mp'])
            drain_amount = round(current_mp * mp_drain_percent)
            new_mp = max(0, current_mp - drain_amount)
            participants.loc[target_id, 'current_mp'] = new_mp

            target_name = participants.loc[target_id, 'name']
            results.append(
                f"**{target_name}**에게 {actual_damage} 데미지! "
                f"MP {drain_amount} 흡수! (남은 MP: {new_mp})"
            )

            # Phase 3.2: 피격 반응 메시지 (플레이어가 피해를 받은 경우)
            monster_type = str(participants.loc[target_id, 'monster_type'])
            if monster_type == 'player':
                # 플레이어는 현재 피격 반응 메시지 없음 (추후 추가 가능)
                pass

        full_result = '\n'.join(results)
        if reaction_messages:
            full_result += '\n\n' + '\n'.join(reaction_messages)

        return full_result

    async def _execute_lifesteal(
        self,
        battle_state: Dict[str, Any],
        skill_data: pd.Series,
        targets: List[str],
        boss_id: str
    ) -> str:
        """생명력 흡수 실행

        DEPRECATED: BossSkillSystem.execute_lifesteal() 사용을 권장합니다.
        """
        return await BossSkillSystem.execute_lifesteal(
            battle_state, skill_data, targets, boss_id, CombatUtils
        )

    async def _execute_lifesteal_legacy(
        self,
        battle_state: Dict[str, Any],
        skill_data: pd.Series,
        targets: List[str],
        boss_id: str
    ) -> str:
        """생명력 흡수 실행 (레거시 구현)"""
        value = json.loads(skill_data['value'])
        heal_percent = value.get('heal_percent', 0) / 100.0

        participants = battle_state['participants_cache']
        damage = int(skill_data['damage'])

        total_heal = 0
        results = []
        reaction_messages = []

        for target_id in targets:
            # 데미지 적용
            actual_damage, final_hp = CombatUtils.apply_damage_to_target(
                battle_state, target_id, damage
            )

            # 실제 가한 데미지의 N% 회복
            heal_amount = round(actual_damage * heal_percent)
            total_heal += heal_amount

            target_name = participants.loc[target_id, 'name']
            results.append(f"**{target_name}**에게 {actual_damage} 데미지!")

            # Phase 3.2: 피격 반응 메시지 (플레이어가 피해를 받은 경우)
            monster_type = str(participants.loc[target_id, 'monster_type'])
            if monster_type == 'player':
                # 플레이어는 현재 피격 반응 메시지 없음 (추후 추가 가능)
                pass

        # 보스 HP 회복
        boss_hp = int(participants.loc[boss_id, 'current_hp'])
        boss_max_hp = int(participants.loc[boss_id, 'max_hp'])
        new_boss_hp = min(boss_hp + total_heal, boss_max_hp)
        participants.loc[boss_id, 'current_hp'] = new_boss_hp

        boss_name = participants.loc[boss_id, 'name']
        results.append(f"**{boss_name}**이(가) {total_heal} HP 회복! (현재 HP: {new_boss_hp})")

        full_result = '\n'.join(results)
        if reaction_messages:
            full_result += '\n\n' + '\n'.join(reaction_messages)

        return full_result

    async def _execute_stat_debuff(
        self,
        battle_state: Dict[str, Any],
        skill_data: pd.Series,
        targets: List[str],
        boss_id: str
    ) -> str:
        """스탯 디버프 실행

        DEPRECATED: BossSkillSystem.execute_stat_debuff() 사용을 권장합니다.
        """
        return await BossSkillSystem.execute_stat_debuff(
            battle_state, skill_data, targets, boss_id
        )

    async def _execute_stat_debuff_legacy(
        self,
        battle_state: Dict[str, Any],
        skill_data: pd.Series,
        targets: List[str],
        boss_id: str
    ) -> str:
        """스탯 디버프 실행 (레거시 구현)"""
        participants = battle_state['participants_cache']
        duration = int(skill_data['duration'])

        # value 파싱
        value_str = skill_data['value']
        if value_str and value_str != '{}':
            value = json.loads(value_str)
            stat_name = value.get('stat', '')
            decrease = value.get('decrease', 0)
        else:
            # 석화 (value가 빈 경우)
            stat_name = 'petrified'
            decrease = 0

        # 원본 스탯 백업 (Phase 1.5)
        if 'original_stats' not in battle_state:
            battle_state['original_stats'] = {}

        results = []
        for target_id in targets:
            if stat_name == 'petrified':
                # 석화: 행동 불가 상태
                current_status = str(participants.loc[target_id, 'status'])
                if current_status in ['정상', 'nan', '', 'None']:
                    new_status = f"petrified:{duration}"
                else:
                    new_status = f"{current_status}|petrified:{duration}"
                participants.loc[target_id, 'status'] = new_status

                target_name = participants.loc[target_id, 'name']
                results.append(f"💎 **{target_name}**이(가) 석화되었다! ({duration}턴간 행동 불가)")

            else:
                # 스탯 감소
                current_stat = int(participants.loc[target_id, stat_name])

                # 원본 스탯 백업
                if (target_id, stat_name) not in battle_state['original_stats']:
                    battle_state['original_stats'][(target_id, stat_name)] = current_stat

                new_stat = max(0, current_stat - decrease)
                participants.loc[target_id, stat_name] = new_stat

                # status에 디버프 기록
                current_status = str(participants.loc[target_id, 'status'])
                debuff_tag = f"{stat_name}_debuff:{duration}"
                if current_status in ['정상', 'nan', '', 'None']:
                    new_status = debuff_tag
                else:
                    new_status = f"{current_status}|{debuff_tag}"
                participants.loc[target_id, 'status'] = new_status

                target_name = participants.loc[target_id, 'name']
                results.append(
                    f"📉 **{target_name}**의 {stat_name} -{decrease}! "
                    f"({current_stat} → {new_stat}, {duration}턴간)"
                )

        # Phase 3.3: 디버프 로깅
        if results:
            debuff_type = "석화" if value.get('petrified') else f"{stat_name} 감소"
            logger.info(f"[보스 스킬 - 디버프] {boss_id}가 {len(targets)}명에게 {debuff_type} 부여 ({duration}턴)")

        return '\n'.join(results)

    async def _execute_buff_self(
        self,
        battle_state: Dict[str, Any],
        skill_data: pd.Series,
        targets: List[str],
        boss_id: str
    ) -> str:
        """자기 강화 실행

        DEPRECATED: BossSkillSystem.execute_buff_self() 사용을 권장합니다.
        """
        return await BossSkillSystem.execute_buff_self(
            battle_state, skill_data, targets, boss_id
        )

    async def _execute_buff_self_legacy(
        self,
        battle_state: Dict[str, Any],
        skill_data: pd.Series,
        targets: List[str],
        boss_id: str
    ) -> str:
        """자기 강화 실행 (레거시 구현)"""
        value = json.loads(skill_data['value'])
        stat_name = value.get('stat', '')
        increase = value.get('increase', 0)
        duration = int(skill_data['duration'])

        participants = battle_state['participants_cache']

        # 원본 스탯 백업 (Phase 1.5)
        if 'original_stats' not in battle_state:
            battle_state['original_stats'] = {}

        results = []

        for target_id in targets:
            # 원본 스탯 백업
            current_stat = int(participants.loc[target_id, stat_name])
            if (target_id, stat_name) not in battle_state['original_stats']:
                battle_state['original_stats'][(target_id, stat_name)] = current_stat

            # 스탯 증가
            new_stat = current_stat + increase
            participants.loc[target_id, stat_name] = new_stat

            # status에 버프 기록
            current_status = str(participants.loc[target_id, 'status'])
            buff_tag = f"{stat_name}_buff:{duration}"
            if current_status in ['정상', 'nan', '', 'None']:
                new_status = buff_tag
            else:
                new_status = f"{current_status}|{buff_tag}"
            participants.loc[target_id, 'status'] = new_status

            target_name = participants.loc[target_id, 'name']
            results.append(
                f"📈 **{target_name}**의 {stat_name} +{increase}! "
                f"({current_stat} → {new_stat}, {duration}턴간)"
            )

        # Phase 3.3: 버프 로깅
        if results:
            duration_text = f"{duration}턴" if duration < 100 else "영구"
            logger.info(f"[보스 스킬 - 버프] {boss_id}가 {stat_name} +{increase} 획득 ({duration_text})")

        return '\n'.join(results)

    async def _execute_damage_all(
        self,
        battle_state: Dict[str, Any],
        skill_data: pd.Series,
        targets: List[str],
        boss_id: str
    ) -> str:
        """전체 공격 실행

        DEPRECATED: BossSkillSystem.execute_damage_all() 사용을 권장합니다.
        """
        return await BossSkillSystem.execute_damage_all(
            battle_state, skill_data, targets, boss_id, self.sheet_handler, CombatUtils
        )

    async def _execute_damage_all_legacy(
        self,
        battle_state: Dict[str, Any],
        skill_data: pd.Series,
        targets: List[str],
        boss_id: str
    ) -> str:
        """전체 공격 실행 (레거시 구현)"""
        damage = int(skill_data['damage'])
        results = []
        reaction_messages = []

        for target_id in targets:
            actual_damage, final_hp = CombatUtils.apply_damage_to_target(
                battle_state, target_id, damage
            )

            target_name = battle_state['participants_cache'].loc[target_id, 'name']
            results.append(f"**{target_name}**에게 {actual_damage} 데미지! (현재 HP: {final_hp})")

            # Phase 3.2: 피격 반응 메시지 (플레이어의 피격반응 필요 없음)
            job = str(battle_state['participants_cache'].loc[target_id, 'job'])
            # if job == 'player':
            #       reaction = utils.get_monster_damage_reaction(target_id, self.sheet_handler.characters_sheet_cache)
            #   if reaction:
            #         reaction_messages.append(f"💭 *{target_name}: {reaction['message']}*")

        full_result = '\n'.join(results)
        if reaction_messages:
            full_result += '\n\n' + '\n'.join(reaction_messages)

        return full_result

    async def _update_status_durations(
        self,
        interaction: discord.Interaction,
        battle_state: Dict[str, Any]
    ) -> None:
        """모든 참여자의 상태 duration 업데이트 (Phase 1.4)

        Args:
            interaction: Discord 인터랙션
            battle_state: 현재 전투 상태
        """
        participants = battle_state['participants_cache']

        for participant_id in participants.index:
            status = str(participants.loc[participant_id, 'status'])
            if status in ['정상', 'nan', '', 'None']:
                continue

            # status를 |로 분리
            status_effects = status.split('|')
            new_effects = []

            for effect in status_effects:
                if ':' not in effect:
                    # duration이 없는 상태 (예: '정상')
                    new_effects.append(effect)
                    continue

                effect_name, duration_str = effect.split(':', 1)
                try:
                    duration = int(duration_str) - 1
                except ValueError:
                    # 파싱 실패 시 그대로 유지
                    new_effects.append(effect)
                    continue

                if duration > 0:
                    # 아직 지속 중
                    new_effects.append(f"{effect_name}:{duration}")
                else:
                    # duration 0 → 효과 해제
                    await self._remove_status_effect(
                        interaction,
                        battle_state,
                        participant_id,
                        effect_name
                    )

            # 새로운 status 저장
            new_status = '|'.join(new_effects) if new_effects else '정상'
            participants.loc[participant_id, 'status'] = new_status

    async def _remove_status_effect(
        self,
        interaction: discord.Interaction,
        battle_state: Dict[str, Any],
        participant_id: str,
        effect_name: str
    ) -> None:
        """특정 상태 효과 해제 (스탯 복원 포함) (Phase 1.4 + 1.5)

        Args:
            interaction: Discord 인터랙션
            battle_state: 전투 상태
            participant_id: 참여자 ID
            effect_name: 효과 이름 (예: 'physics_buff', 'petrified')
        """
        participants = battle_state['participants_cache']
        participant_name = participants.loc[participant_id, 'name']

        if effect_name.endswith('_buff') or effect_name.endswith('_debuff'):
            # 스탯 버프/디버프 해제 → 원본 스탯 복원
            stat_name_org = effect_name.replace('_buff', '').replace('_debuff', '')
            stat_list = {
                'zack': '잭스 DMW',
                'physics': '근력',
                'magic': '마법',
                'agility': '민첩',
                'charm': '매력'
            }
            stat_name = stat_list.get(stat_name_org, stat_name_org)

            # original_stats에서 원본 값 가져와서 복원
            if 'original_stats' in battle_state:
                original_value = battle_state['original_stats'].get((participant_id, stat_name))
                if original_value is not None:
                    current_stat = int(participants.loc[participant_id, stat_name])
                    participants.loc[participant_id, stat_name] = original_value

                    # 버프/디버프 해제 메시지
                    if effect_name.endswith('_buff'):
                        await interaction.channel.send(
                            f"✨ **{participant_name}**의 {stat_name} 버프가 해제되었다! "
                            f"({current_stat} → {original_value})"
                        )
                    else:
                        await interaction.channel.send(
                            f"🩹 **{participant_name}**의 {stat_name} 디버프가 해제되었다! "
                            f"({current_stat} → {original_value})"
                        )

                    # 복원 후 백업 삭제
                    del battle_state['original_stats'][(participant_id, stat_name)]

        elif effect_name == 'petrified':
            # 석화 해제
            await interaction.channel.send(
                f"**{participant_name}**의 석화가 풀렸다! 이제 행동할 수 있다."
            )

        # 기타 상태 효과는 메시지만 출력
        else:
            logger.info(f"{participant_name}의 {effect_name} 효과 해제")

    # ==================== Phase 2: 공생 메커니즘 + 자동 조정 ====================

    async def _check_symbiosis(
        self,
        interaction: discord.Interaction,
        battle_state: Dict[str, Any]
    ) -> None:
        """공생 메커니즘 체크 (Phase 2.1 + 2.2)

        DEPRECATED: StatusEffectManager.check_symbiosis() 사용을 권장합니다.

        Args:
            interaction: Discord 인터랙션
            battle_state: 전투 상태
        """
        # 리팩토링: StatusEffectManager로 위임
        await StatusEffectManager.check_symbiosis(interaction, battle_state)

    async def _symbiosis_heal(
        self,
        interaction: discord.Interaction,
        battle_state: Dict[str, Any]
    ) -> None:
        """공생 자동 치유 - 포자 생존 시 모체 회복 (Phase 2.1)

        DEPRECATED: StatusEffectManager.symbiosis_heal() 사용을 권장합니다.

        Args:
            interaction: Discord 인터랙션
            battle_state: 전투 상태
        """
        # 리팩토링: StatusEffectManager로 위임
        await StatusEffectManager.symbiosis_heal(interaction, battle_state)

    async def _symbiosis_death(
        self,
        interaction: discord.Interaction,
        battle_state: Dict[str, Any]
    ) -> None:
        """공생 사멸 - 모체 사망 시 포자 동시 사멸 (Phase 2.2)

        DEPRECATED: StatusEffectManager.symbiosis_death() 사용을 권장합니다.

        Args:
            interaction: Discord 인터랙션
            battle_state: 전투 상태
        """
        # 리팩토링: StatusEffectManager로 위임
        await StatusEffectManager.symbiosis_death(interaction, battle_state)

    # ===== Phase 2.3: 플레이어 수별 자동 조정 =====

    def adjust_monster_stats_by_player_count(
        self,
        battle_state: Dict[str, Any],
        difficulty: str = "보통"
    ) -> tuple[int, float]:
        """플레이어 수에 따라 몬스터 스탯 자동 조정

        DEPRECATED: BattleManager.adjust_monster_stats_by_player_count() 사용을 권장합니다.

        Args:
            battle_state: 전투 상태
            difficulty: 난이도 ("쉬움", "보통", "어려움", "극악")

        Returns:
            (플레이어 수, 최종 배율)
        """
        # 리팩토링: BattleManager로 위임
        return BattleManager.adjust_monster_stats_by_player_count(battle_state, difficulty)

async def setup(bot: commands.Bot):
    await bot.add_cog(CombatCog(bot, bot.sheet_handler)) # type: ignore