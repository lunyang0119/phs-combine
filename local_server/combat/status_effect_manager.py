"""
상태이상 관리 모듈

이 모듈은 전투 중 상태이상 효과 및 공생 메커니즘을 관리합니다.

Phase 2에서 combat_commands.py로부터 분리됨 (2025-10-06)
"""

import logging
from typing import Dict, Any
import discord
import constants

logger = logging.getLogger(__name__)


class StatusEffectManager:
    """상태이상 효과 및 공생 메커니즘 관리 클래스"""

    @staticmethod
    async def update_status_durations(
        interaction: discord.Interaction,
        battle_state: Dict[str, Any],
        current_participant_id: str
    ) -> None:
        """상태이상 지속시간 감소 및 효과 처리

        Args:
            interaction: Discord 인터랙션
            battle_state: 현재 전투 상태
            current_participant_id: 현재 턴 참여자 ID
        """
        participants_cache = battle_state['participants_cache']

        status_string = str(participants_cache.loc[current_participant_id, 'status'])
        if status_string and status_string not in ['정상', 'nan', '']:
            status_list = status_string.split('|')
            new_status_list = []

            for single_status in status_list:
                if ':' not in single_status:
                    # ':' 없는 경우 그대로 유지
                    new_status_list.append(single_status)
                    continue

                # 'status_name:duration' 또는 'status_name:duration:value' 형식 처리
                parts = single_status.split(':')
                if len(parts) < 2:
                    new_status_list.append(single_status)
                    continue

                status_name = parts[0]
                duration = int(parts[1])
                additional_values = parts[2:] if len(parts) > 2 else []

                # 토쿠라의 죽음의 선고 효과 처리
                if status_name == 'tokura_death' and duration == 1:
                    participants_cache.loc[current_participant_id, 'current_hp'] = 0
                    participants_cache.loc[current_participant_id, 'is_dead'] = 3
                    participants_cache.loc[current_participant_id, 'status'] = '정상'

                    death_embed = discord.Embed(
                        title="죽음의 선고",
                        description=f"**{participants_cache.loc[current_participant_id, 'name']}**에게 내려진 죽음의 선고가 실현되었습니다...",
                        color=discord.Color.dark_purple()
                    )
                    await interaction.channel.send(embed=death_embed)

                # 일반적인 상태 지속시간 감소
                elif duration > 1:
                    if additional_values:
                        new_status = f"{status_name}:{duration - 1}:{':'.join(additional_values)}"
                    else:
                        new_status = f"{status_name}:{duration - 1}"
                    new_status_list.append(new_status)

                else:  # duration이 1이었을 경우, 이번 턴을 끝으로 효과 종료
                    # 영어 효과명을 한국어로 변환
                    korean_status_name = constants.STATUS_EFFECT_KOREAN_NAMES.get(status_name, status_name)
                    status_end_embed = discord.Embed(
                        description=f"**{participants_cache.loc[current_participant_id, 'name']}**의 **{korean_status_name}** 효과가 사라졌습니다.",
                        color=discord.Color.light_grey()
                    )
                    await interaction.channel.send(embed=status_end_embed)

            if new_status_list:
                participants_cache.loc[current_participant_id, 'status'] = '|'.join(new_status_list)
            else:
                participants_cache.loc[current_participant_id, 'status'] = '정상'

    @staticmethod
    async def check_symbiosis(
        interaction: discord.Interaction,
        battle_state: Dict[str, Any]
    ) -> None:
        """공생 메커니즘 체크 (Phase 2.1 + 2.2)

        Args:
            interaction: Discord 인터랙션
            battle_state: 전투 상태
        """
        # Phase 2.1: 자동 치유
        await StatusEffectManager.symbiosis_heal(interaction, battle_state)

        # Phase 2.2: 공생 사멸
        await StatusEffectManager.symbiosis_death(interaction, battle_state)

    @staticmethod
    async def symbiosis_heal(
        interaction: discord.Interaction,
        battle_state: Dict[str, Any]
    ) -> None:
        """공생 자동 치유 - 포자 생존 시 모체 회복 (Phase 2.1)

        Args:
            interaction: Discord 인터랙션
            battle_state: 전투 상태
        """
        participants = battle_state['participants_cache']

        # 모체 식물 존재 여부 확인
        if 'mid_boss_mother_flora' not in participants.index:
            return

        # 모체가 이미 죽었으면 종료
        mother_is_dead = int(participants.loc['mid_boss_mother_flora', 'is_dead'])
        if mother_is_dead > 0:
            return

        # 생존 중인 포자 개수 세기
        alive_spores = []
        for spore_id in ['spore_spreader_one', 'spore_spreader_two']:
            if spore_id in participants.index:
                if int(participants.loc[spore_id, 'is_dead']) == 0:
                    spore_name = participants.loc[spore_id, 'name']
                    alive_spores.append(spore_name)

        if len(alive_spores) > 0:
            # 포자 1마리당 5 HP 회복
            heal_amount = len(alive_spores) * 5
            mother_hp = int(participants.loc['mid_boss_mother_flora', 'current_hp'])
            mother_max_hp = int(participants.loc['mid_boss_mother_flora', 'max_hp'])
            new_hp = min(mother_hp + heal_amount, mother_max_hp)
            participants.loc['mid_boss_mother_flora', 'current_hp'] = new_hp

            # 메시지 출력
            mother_name = participants.loc['mid_boss_mother_flora', 'name']
            embed = discord.Embed(
                title="🌱 공생 - 자동 치유",
                description=f"{', '.join(alive_spores)}이(가) **{mother_name}**에게 에너지를 전달한다!\n+{heal_amount} HP (현재 HP: {new_hp}/{mother_max_hp})",
                color=discord.Color.green()
            )
            await interaction.channel.send(embed=embed)

            logger.info(f"공생 치유: {mother_name} +{heal_amount} HP (포자 {len(alive_spores)}마리)")

    @staticmethod
    async def symbiosis_death(
        interaction: discord.Interaction,
        battle_state: Dict[str, Any]
    ) -> None:
        """공생 사멸 - 모체 사망 시 포자 동시 사멸 (Phase 2.2)

        Args:
            interaction: Discord 인터랙션
            battle_state: 전투 상태
        """
        participants = battle_state['participants_cache']

        # 모체 식물 존재 여부 확인
        if 'mid_boss_mother_flora' not in participants.index:
            return

        # 모체가 방금 죽었는지 체크 (current_hp == 0 and is_dead == 3)
        mother_hp = int(participants.loc['mid_boss_mother_flora', 'current_hp'])
        mother_is_dead = int(participants.loc['mid_boss_mother_flora', 'is_dead'])

        if mother_hp == 0 and mother_is_dead == 3:
            # 생존 중인 포자들을 모두 죽임
            killed_spores = []
            for spore_id in ['spore_spreader_one', 'spore_spreader_two']:
                if spore_id in participants.index:
                    spore_is_dead = int(participants.loc[spore_id, 'is_dead'])
                    if spore_is_dead == 0:  # 아직 살아있는 포자만
                        participants.loc[spore_id, 'is_dead'] = 3
                        participants.loc[spore_id, 'current_hp'] = 0
                        spore_name = participants.loc[spore_id, 'name']
                        killed_spores.append(spore_name)

            if killed_spores:
                mother_name = participants.loc['mid_boss_mother_flora', 'name']
                embed = discord.Embed(
                    title="공생공사",
                    description=f"**{mother_name}**이(가) 시들자, {', '.join(killed_spores)}도 함께 죽어간다...",
                    color=discord.Color.dark_grey()
                )
                await interaction.channel.send(embed=embed)

                logger.info(f"공생체 사멸: {mother_name} 사망 → 포자 {len(killed_spores)}마리 동시 사멸")
