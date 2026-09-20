"""
전투 관련 유틸리티 함수

데미지/힐 계산, HP 업데이트 등 재사용 가능한 헬퍼 함수들을 제공합니다.
"""

import discord
import logging
from typing import Dict, Any, Tuple, Optional
import pandas as pd
import sys
import os

# 상위 디렉토리의 모듈을 import하기 위한 경로 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from character_models import Character
import constants

logger = logging.getLogger(__name__)


class CombatUtils:
    """전투 관련 유틸리티 함수 모음"""

    @staticmethod
    def update_participant_hp(
        battle_state: Dict[str, Any],
        participant_id: str,
        new_hp: int
    ) -> None:
        """참여자 HP 업데이트 및 전투불능 처리

        Args:
            battle_state: 현재 전투 상태 딕셔너리
            participant_id: 참여자 ID (플레이어 또는 몬스터)
            new_hp: 새로운 HP 값
        """
        participants_cache = battle_state['participants_cache']
        participants_cache.loc[participant_id, 'current_hp'] = new_hp
    
        # 전투불능 처리
        if new_hp <= 0:
            participants_cache.loc[participant_id, 'is_dead'] = constants.DEAD_REVIVAL_TURNS
            participant_name = participants_cache.loc[participant_id, 'name']
            logger.info(f"{participant_name}({participant_id}) 전투불능 처리 완료")

    @staticmethod
    def apply_damage_to_target(
        battle_state: Dict[str, Any],
        target_id: str,
        damage: int,
        ignore_defense: bool = False
    ) -> Tuple[int, int]:
        """대상에게 데미지 적용 (방어/회피 계산 포함)

        Args:
            battle_state: 현재 전투 상태
            target_id: 대상 ID
            damage: 기본 데미지
            ignore_defense: True시 방어력 무시 (리미트 브레이크용)

        Returns:
            (실제_데미지, 최종_HP) 튜플
        """
        participants_cache = battle_state['participants_cache']
        target = Character(participants_cache.loc[target_id])

        initial_hp = target.current_hp

        if ignore_defense:
            # 방어 무시 공격 (리미트 브레이크)
            new_hp = max(0, initial_hp - damage)
            actual_damage = initial_hp - new_hp
        else:
            # 일반 공격 (방어력 적용)
            new_hp = target.take_damage(damage)
            actual_damage = initial_hp - new_hp

        CombatUtils.update_participant_hp(battle_state, target_id, new_hp)

        return actual_damage, new_hp

    @staticmethod
    def apply_heal_to_target(
        battle_state: Dict[str, Any],
        target_id: str,
        heal_amount: int
    ) -> int:
        """대상에게 힐 적용

        Args:
            battle_state: 현재 전투 상태
            target_id: 대상 ID
            heal_amount: 회복량

        Returns:
            최종 HP
        """
        participants_cache = battle_state['participants_cache']
        current_hp = int(participants_cache.loc[target_id, 'current_hp'])
        max_hp = int(participants_cache.loc[target_id, 'max_hp'])

        new_hp = min(max_hp, current_hp + heal_amount)

        CombatUtils.update_participant_hp(battle_state, target_id, new_hp)

        return new_hp

    @staticmethod
    def update_participant_status(
        battle_state: Dict[str, Any],
        participant_id: str,
        new_status: str,
        append: bool = False
    ) -> None:
        """참여자 상태이상 업데이트

        Args:
            battle_state: 현재 전투 상태
            participant_id: 참여자 ID
            new_status: 새로운 상태 (예: 'zack_buff:3', 'stun:2')
            append: True시 기존 상태에 추가 (|로 구분)
        """
        participants_cache = battle_state['participants_cache']
        current_status = str(participants_cache.loc[participant_id, 'status'])

        if append and current_status not in ['정상', 'nan', '', 'None']:
            # 기존 상태에 추가
            participants_cache.loc[participant_id, 'status'] = f'{current_status}|{new_status}'
        else:
            # 새로운 상태로 덮어쓰기
            participants_cache.loc[participant_id, 'status'] = new_status

        logger.info(f"{participant_id} 상태 변경: {current_status} → {new_status}")

    @staticmethod
    async def send_skill_embed(
        channel: discord.TextChannel,
        title: str,
        description: str,
        color: discord.Color
    ) -> None:
        """스킬 효과 결과 임베드 전송

        Args:
            channel: 메시지를 전송할 채널
            title: 임베드 제목
            description: 임베드 설명
            color: 임베드 색상
        """
        embed = discord.Embed(title=title, description=description, color=color)
        await channel.send(embed=embed)

    @staticmethod
    def check_battle_end(
        battle_state: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """전투 종료 조건 확인

        Args:
            battle_state: 현재 전투 상태 딕셔너리

        Returns:
            Tuple[bool, str]: (종료여부, 종료사유)
                - 종료사유: 'victory', 'defeat', 'continue'
        """
        participants_cache = battle_state['participants_cache']
        all_ids = list(participants_cache.index)

        player_ids = [p_id for p_id in all_ids if p_id.isdigit()]
        monster_ids = [p_id for p_id in all_ids if not p_id.isdigit()]

        player_hps = participants_cache.loc[player_ids, 'current_hp'].astype(int)
        monster_hps = participants_cache.loc[monster_ids, 'current_hp'].astype(int)

        # 승리 조건: 모든 몬스터 처치
        if not monster_ids or all(hp <= 0 for hp in monster_hps):
            return True, 'victory'

        # 패배 조건: 모든 플레이어 전투불능
        if not player_ids or all(hp <= 0 for hp in player_hps):
            return True, 'defeat'

        # 전투 계속
        return False, 'continue'

    @staticmethod
    def calculate_critical_chance(participant_id: str, participants_cache: pd.DataFrame) -> float:
        """크리티컬 확률 계산

        Args:
            participant_id: 공격자 ID
            participants_cache: 참여자 데이터프레임

        Returns:
            float: 크리티컬 확률 (0.0 ~ 1.0)
        """
        # 기본 크리티컬 확률: 5%
        base_crit_chance = constants.BASE_CRIT_CHANCE

        # 민첩 보너스: (민첩-10)/2 * 0.05% (소수점 첫째자리 내림)
        agility_raw = participants_cache.loc[participant_id, 'agility']
        # pandas Scalar을 Python int로 변환 (type: ignore for type checker)
        agility = int(agility_raw)  # type: ignore

        agility_bonus_raw = (agility - 10) / 2.0
        agility_bonus_floored = max(0, int(agility_bonus_raw))  # 0 이상의 자연수로 내림
        agility_crit_bonus = agility_bonus_floored * 0.05 

        # 크리티컬 버프 확인 (status에서)
        current_status = str(participants_cache.loc[participant_id, 'status'])
        crit_buff = 0.0

        if 'crit_buff' in current_status:
            # crit_buff:5 형식 파싱
            for status_part in current_status.split('|'):
                if 'crit_buff:' in status_part:
                    try:
                        buff_value = int(status_part.split(':')[1])
                        crit_buff = buff_value / 100.0  # 5 -> 0.05
                    except (IndexError, ValueError):
                        pass

        total_crit_chance = base_crit_chance + agility_crit_bonus + crit_buff
        return min(total_crit_chance, 1.0)  # 최대 100%

    @staticmethod
    def apply_critical_damage(base_damage: int, is_critical: bool) -> Tuple[int, bool]:
        """크리티컬 데미지 적용

        Args:
            base_damage: 기본 데미지
            is_critical: 크리티컬 여부

        Returns:
            (최종_데미지, 크리티컬_여부) 튜플
        """
        if is_critical:
            critical_damage = int(base_damage * constants.CRIT_MULTIPLIER)
            return critical_damage, True
        return base_damage, False

    @staticmethod
    def safe_get_participant(
        participants_cache: pd.DataFrame,
        participant_id: str,
        default=None
    ) -> Optional[pd.Series]:
        """안전하게 참여자 데이터 가져오기 (KeyError 방지)

        Args:
            participants_cache: 참여자 데이터프레임
            participant_id: 참여자 ID
            default: ID가 없을 때 반환할 기본값

        Returns:
            참여자 데이터 Series 또는 default
        """
        if participant_id not in participants_cache.index:
            logger.warning(f"참여자 '{participant_id}'를 participants_cache에서 찾을 수 없습니다.")
            return default
        return participants_cache.loc[participant_id]

    @staticmethod
    def participant_exists(
        participants_cache: pd.DataFrame,
        participant_id: str
    ) -> bool:
        """참여자 존재 여부 확인

        Args:
            participants_cache: 참여자 데이터프레임
            participant_id: 참여자 ID

        Returns:
            존재 여부
        """
        return participant_id in participants_cache.index
