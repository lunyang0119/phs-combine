"""
전투 관리자 모듈 (Phase 4)

전투 초기화, 턴 순서 결정, 턴 시작/종료 처리, 전투 종료 등
전투의 전반적인 흐름을 관리하는 static utility class입니다.

작성일: 2025-10-06
"""

import discord
# import random
from combat import random_utils
import logging
import pandas as pd
import asyncio
from typing import Dict, List, Any, Tuple, Optional
import constants

from combat import random_utils

logger = logging.getLogger(__name__)


class BattleManager:
    """전투 흐름 관리 static utility class

    전투 초기화, 턴 순서 결정, 턴 시작/종료 처리, 전투 종료 등을 담당합니다.
    모든 메서드는 static 메서드이며, 인스턴스화가 필요 없습니다.
    """

    @staticmethod
    def initialize_battle_state(
        participants_cache: pd.DataFrame,
        difficulty: str = "보통",
        boss_mode: bool = False
    ) -> Dict[str, Any]:
        """전투 상태 딕셔너리 초기화

        Args:
            participants_cache: 참여자 데이터 캐시 (DataFrame)
            difficulty: 난이도 ("쉬움"/"보통"/"어려움"/"극악")
            boss_mode: 보스전투 모드 (DMW 당첨률 2배)

        Returns:
            Dict[str, Any]: 초기화된 전투 상태 딕셔너리
        """
        # 환경 효과 결정 (20% 확률)
        environment_effect = None
        if random_utils.get_random < constants.ENV_EFFECT_CHANCE:
            environment_effect = random_utils.choice(constants.ENVIRONMENT_EFFECTS)

        battle_state = {
            'participants_cache': participants_cache,
            'order_infos': [],
            'current_turn_index': 0,
            'pending_intruders': [],  # 난입 대기
            'order_count': 1,
            'environment_effect': environment_effect,  # 환경 효과 저장
            'combo_target_id': None,  # 연계 공격 추적용
            'combo_count': 0,  # 연계 카운트
            'difficulty': difficulty,  # 난이도 저장
            'boss_mode': boss_mode,  # 보스전투 모드 플래그
            'used_dmw_figures': [],  # 보스전투 모드: 사용된 DMW 피규어 목록
            'loop_in_progress': False,  # 전투 루프 진행 플래그
            'paused': False,  # 일시정지 상태
            'pause_event': None  # 일시정지 이벤트 (나중에 asyncio.Event()로 초기화)
        }

        logger.info(f"전투 상태 초기화 완료 - 난이도: {difficulty}, 보스모드: {boss_mode}, 환경 효과: {environment_effect}")
        return battle_state

    @staticmethod
    def determine_turn_order(
        battle_state: Dict[str, Any],
        force_shuffle: bool = False
    ) -> List[str]:
        """민첩에 따라 턴 순서 결정 (동점자는 무작위)

        Args:
            battle_state: 현재 전투 상태 딕셔너리
            force_shuffle: True시 순서를 강제로 섞음 (시간 왜곡 등)

        Returns:
            List[str]: 참여자 ID 리스트 (행동 순서)
        """
        participants_cache = battle_state['participants_cache']

        # 1. 참여자 ID 리스트 가져오기
        participants = list(participants_cache.index)

        # 2. 동점자 처리를 위해 순서를 미리 섞음 (전투 시작 시 또는 force_shuffle=True일 때만)
        if force_shuffle or not battle_state.get('order_infos'):
            random_utils.shuffle(participants)

        # 3. 각 참여자의 'agility' 스탯을 기준으로 내림차순 정렬
        sorted_participants = sorted(
            participants,
            key=lambda p_id: int(participants_cache.loc[p_id, 'agility']),
            reverse=True
        )

        # 4. 결정된 순서를 battle_state에 저장
        battle_state['order_infos'] = sorted_participants
        battle_state['current_turn_index'] = 0

        logger.info(f"턴 순서 결정 완료: {sorted_participants}")
        return sorted_participants

    @staticmethod
    async def handle_turn_start(
        battle_state: Dict[str, Any],
        channel: discord.abc.Messageable,
        channel_id: int,
        sheet_handler: Any  # SheetsHandler 타입, 순환 import 방지
    ) -> None:
        """턴 시작 시 처리 (전투 현황, 환경 효과, 난입자 처리)

        Args:
            battle_state: 현재 전투 상태 딕셔너리
            channel: 메시지를 전송할 채널
            channel_id: 전투 채널 ID
            sheet_handler: Google Sheets 핸들러 인스턴스
        """
        participants_cache = battle_state['participants_cache']
        all_ids = list(participants_cache.index)
        player_ids = [p_id for p_id in all_ids if p_id.isdigit()]
        monster_ids = [p_id for p_id in all_ids if not p_id.isdigit()]

        # 전체 참여자 상태 표시
        status_embed = discord.Embed(
            title=f"제{battle_state['order_count']}턴 - 전투 현황",
            color=discord.Color.blue()
        )

        # 플레이어 상태
        player_status = ""
        for p_id in player_ids:
            p_data = participants_cache.loc[p_id]
            name = p_data['name']
            hp = int(p_data['current_hp'])
            max_hp = int(p_data['max_hp'])
            mp = int(p_data['current_mp'])
            max_mp = int(p_data['max_mp'])
            is_dead = int(p_data['is_dead'])

            if is_dead > 0:
                player_status += f"**{name}**: HP ***{hp}/{max_hp}***, MP ***{mp}/{max_mp}*** (전투불능 ***{is_dead}***턴)\n"
            else:
                player_status += f"**{name}**: HP ***{hp}/{max_hp}***, MP ***{mp}/{max_mp}***\n"

        status_embed.add_field(name="아군", value=player_status or "없음", inline=False)

        # 몬스터 상태
        monster_status = ""
        for m_id in monster_ids:
            m_data = participants_cache.loc[m_id]
            name = m_data['name']
            hp = int(m_data['current_hp'])
            max_hp = int(m_data['max_hp'])

            if hp <= 0:
                monster_status += f"**{name}**: HP 0/{max_hp} (전투불능)\n"
            else:
                monster_status += f"**{name}**: HP {hp}/{max_hp}\n"

        status_embed.add_field(name="적", value=monster_status or "없음", inline=False)

        await channel.send(embed=status_embed)

        # 환경 효과 적용 (라이프 스트림 폭풍 - 턴 시작 시)
        environment_effect = battle_state.get('environment_effect')
        if environment_effect == 'mana_storm':
            mp_list = []
            for p_id in list(participants_cache.index):
                if int(participants_cache.loc[p_id, 'current_hp']) > 0:
                    max_mp = int(participants_cache.loc[p_id, 'max_mp'])
                    current_mp = int(participants_cache.loc[p_id, 'current_mp'])
                    new_mp = min(max_mp, current_mp + constants.MANA_STORM_MP_GAIN)
                    battle_state['participants_cache'].loc[p_id, 'current_mp'] = new_mp
                    name = participants_cache.loc[p_id, 'name']
                    mp_list.append(f"{name} +{constants.MANA_STORM_MP_GAIN} MP ({new_mp}/{max_mp})")

            if mp_list:
                mp_text = " | ".join(mp_list)
                await channel.send(f"**라이프 스트림 폭풍**\n{mp_text}")

        # 난입자 처리
        if battle_state['pending_intruders']:
            # 순서 재결정
            BattleManager.determine_turn_order(battle_state)
            new_order_infos_names = []
            for p_id in battle_state['order_infos']:
                participant_data = battle_state['participants_cache'].loc[p_id]
                name = participant_data.get('name')
                new_order_infos_names.append(name)

            battle_state['pending_intruders'].clear()
            embed = discord.Embed(
                title="Entering New Intruder",
                description="새로운 참여자를 포함하여 행동 순서를 다시 정합니다.\n"
                f"새로운 순서: {' -> '.join(new_order_infos_names)}",
                color=discord.Color.purple()
            )
            await channel.send(embed=embed)

        # 홀수 라운드마다 구글 시트 동기화
        if battle_state['order_count'] % constants.SYNC_INTERVAL == 1:
            sheet_handler.update_battle_status_cache_to_sheet()
            logger.info(f"제{battle_state['order_count']}턴 - 구글 시트 동기화 완료")

    @staticmethod
    async def handle_turn_end(
        battle_state: Dict[str, Any],
        channel: discord.abc.Messageable,
        channel_id: int
    ) -> None:
        """턴 종료 시 처리 (환경 효과, 턴 카운트 증가)

        Args:
            battle_state: 현재 전투 상태 딕셔너리
            channel: 메시지를 전송할 채널
            channel_id: 전투 채널 ID
        """
        participants_cache = battle_state['participants_cache']

        # 환경 효과 적용 (마황 지대 - 턴 종료 시)
        environment_effect = battle_state.get('environment_effect')
        if environment_effect == 'lava_zone':
            env_embed = discord.Embed(
                title="마황 지대 효과!",
                description=f"모든 참여자가 HP의 {int(constants.LAVA_DAMAGE_PERCENT * 100)}% 피해를 입습니다.",
                color=discord.Color.dark_red()
            )
            for p_id in list(participants_cache.index):
                current_hp = int(participants_cache.loc[p_id, 'current_hp'])
                if current_hp > 0:
                    max_hp = int(participants_cache.loc[p_id, 'max_hp'])
                    damage = int(max_hp * constants.LAVA_DAMAGE_PERCENT)
                    new_hp = max(0, current_hp - damage)
                    battle_state['participants_cache'].loc[p_id, 'current_hp'] = new_hp

                    # 전투불능 처리
                    if new_hp == 0:
                        battle_state['participants_cache'].loc[p_id, 'is_dead'] = 3

                    env_embed.add_field(
                        name=participants_cache.loc[p_id, 'name'],
                        value=f"-{damage} HP (현재: {new_hp})",
                        inline=True
                    )
            await channel.send(embed=env_embed)

        # 턴 카운트 증가 (다음 라운드로)
        battle_state['order_count'] += 1
        battle_state['current_turn_index'] = 0

        # 다음 라운드 메시지
        embed = discord.Embed(
            title=f"제{battle_state['order_count']}턴 시작",
            description="다음 라운드가 시작됩니다.",
            color=discord.Color.gold()
        )
        await channel.send(embed=embed)
        await asyncio.sleep(1)

        logger.info(f"제{battle_state['order_count']-1}턴 종료 → 제{battle_state['order_count']}턴 시작")

    @staticmethod
    async def handle_battle_end(
        channel: discord.abc.Messageable,
        reason: str,
        channel_id: int,
        active_battles: Dict[int, Any],
        sheet_handler: Any  # SheetsHandler 타입
    ) -> None:
        """전투 종료 처리

        Args:
            channel: 메시지를 전송할 채널
            reason: 종료 사유 ('victory' or 'defeat')
            channel_id: 전투 채널 ID
            active_battles: 활성 전투 딕셔너리
            sheet_handler: Google Sheets 핸들러 인스턴스
        """
        if reason == 'victory':
            embed = discord.Embed(
                title="Conflict Resolved",
                description="모든 적이 쓰러졌습니다!\n전투에서 승리했습니다.",
                color=discord.Color.blue()
            )
        elif reason == 'defeat':
            embed = discord.Embed(
                title="Mission Failed...",
                description="모든 아군이 쓰러졌습니다... 전투에서 패배했습니다.",
                color=discord.Color.darker_grey()
            )
        else:
            return

        await channel.send(embed=embed)

        # 전투 종료 시 모든 보스 스킬 초기화
        battle_state = active_battles.get(channel_id)
        if battle_state:
            participants_cache = battle_state['participants_cache']

            # 전투에 참여한 모든 보스 찾기
            for participant_id in participants_cache.index:
                monster_type = str(participants_cache.loc[participant_id, 'monster_type']).lower()
                if monster_type == 'boss':
                    sheet_handler.reset_boss_skills(participant_id)
                    logger.info(f"보스 '{participant_id}' 스킬 초기화 완료")

        # Combat_Status 시트 초기화
        try:
            sheet_handler.battle_status_sheet.clear()
            headers = sheet_handler.battle_stat_headers
            sheet_handler.battle_status_sheet.update('A1', [headers])

            # 캐시도 초기화
            sheet_handler.battle_stat_sheet_cache = pd.DataFrame(columns=headers[1:])
            sheet_handler.battle_stat_sheet_cache.index.name = 'id'

            logger.info(f"전투 {'승리' if reason == 'victory' else '패배'} - Combat_Status 초기화 완료")
        except Exception as e:
            logger.error(f"Combat_Status 초기화 실패: {e}")

        # 활성 전투 목록에서 제거
        if channel_id in active_battles:
            del active_battles[channel_id]
            logger.info(f"채널 {channel_id}의 전투가 종료되었습니다.")

    @staticmethod
    def add_intruder(
        battle_state: Dict[str, Any],
        intruder_id: str
    ) -> None:
        """난입자를 전투에 추가

        Args:
            battle_state: 현재 전투 상태 딕셔너리
            intruder_id: 난입자 ID
        """
        battle_state['pending_intruders'].append(intruder_id)
        logger.info(f"난입자 '{intruder_id}' 추가 - 다음 턴 시작 시 순서 재결정 예정")

    @staticmethod
    def adjust_monster_stats_by_player_count(
        battle_state: Dict[str, Any],
        difficulty: str = "보통"
    ) -> Tuple[int, float]:
        """플레이어 수에 따라 몬스터 스탯 자동 조정

        Args:
            battle_state: 현재 전투 상태 딕셔너리
            difficulty: 난이도 ("쉬움"/"보통"/"어려움"/"극악")

        Returns:
            Tuple[int, float]: (플레이어 수, 스탯 배율)
        """
        participants_cache = battle_state['participants_cache']
        all_ids = list(participants_cache.index)
        player_ids = [p_id for p_id in all_ids if p_id.isdigit()]
        monster_ids = [p_id for p_id in all_ids if not p_id.isdigit()]

        player_count = len(player_ids)

        # 난이도별 기본 배율
        difficulty_multipliers = constants.DIFFICULTY_MULTIPLIERS

        base_multiplier = difficulty_multipliers.get(difficulty, 1.0)

        # 플레이어 수에 따른 추가 배율
        player_count_multipliers = constants.PLAYER_COUNT_MULTIPLIERS

        count_multiplier = player_count_multipliers.get(
            player_count,
            1.0 + (player_count - 3) * 0.1  # 4명 이상은 0.1씩 증가 #TODO: 여기 원래 playercount - 2였음
        )

        # 최종 배율
        final_multiplier = base_multiplier * count_multiplier

        # 몬스터 스탯 조정
        for m_id in monster_ids:
            for stat in ['max_hp', 'current_hp', 'physics', 'magic', 'agility']:
                original_value = int(participants_cache.loc[m_id, stat])
                adjusted_value = int(original_value * final_multiplier)
                battle_state['participants_cache'].loc[m_id, stat] = adjusted_value

        logger.info(f"몬스터 스탯 조정 완료 - 플레이어: {player_count}명, 난이도: {difficulty}, 배율: {final_multiplier:.2f}x")
        return player_count, final_multiplier
