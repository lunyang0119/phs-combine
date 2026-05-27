import re
# import random
from combat import random_utils
import math
import numpy as np
import pandas as pd
import constants

def should_trigger_damage_reaction() -> bool:
    """45% 확률로 True를 반환합니다."""
    return random_utils.get_random() < constants.MONSTER_REACTION_CHANCE

def get_monster_damage_reaction(monster_id: str, monsters_cache: pd.DataFrame) -> dict:
    """
    몬스터의 피격 반응 메시지를 반환합니다.

    Returns:
        dict: {"message": str} 또는 None (반응 없음)
    """
    # 45% 확률 체크
    if not should_trigger_damage_reaction():
        return None

    # Google Sheets에서 데이터 가져오기
    if monster_id in monsters_cache.index:
        monster_data = monsters_cache.loc[monster_id]
        reaction_messages_str = monster_data.get('damage_reaction_messages', '[]')

        try:
            import json
            reaction_messages = json.loads(reaction_messages_str)
            if reaction_messages:
                return {
                    "message": random_utils.choice(reaction_messages)
                }
        except json.JSONDecodeError:
            pass

    return None

def apply_damage_rounding(stat: int) -> int:
    """(stat - 10) /2
    데미지/힐 계산시 사용하는 반올림 함수. 음수일시 0 반환(randint not included)"""
    bonus = max(0, (stat-10)/2)
    return round(bonus)

def apply_evasion_flooring(stat: int) -> int:
    """(stat - 10) /2
    회피/방어 계산 시 사용하는 반내림 함수. 음수일시 0 반환(randint not included)"""
    bonus = max(0, (stat-10)/2)
    return math.floor(bonus)

def calculate_physical_damage(physics_stat: int, job: str, status: str = '정상') -> int:
    """물리 데미지를 계산합니다. 직업과 상태(status)를 반영합니다.

    Args:
        physics_stat: 근력 스탯
        job: 직업 (스트라이커, 솔져, 마테리아 위버, 턱스 등)
        status: 상태 이상

    Returns:
        최종 물리 데미지
    """
    # 기본 데미지: (근력-10)/2 + 2d10 + 5
    dice_roll = random_utils.randint(1, 10) + random_utils.randint(1, 10)
    base_damage = apply_damage_rounding(physics_stat) + dice_roll + 5

    # 잭스 버프
    if 'zack_buff' in status:
        base_damage = round(base_damage * 1.2)

    # 직업 보너스 (스트라이커, 솔져: 1.5배)
    if job in ['스트라이커', '솔져']:
        return round(base_damage * 1.5)
    else:
        return base_damage

        

def calculate_magic_power(magic_stat: int, power: int, job: str = '') -> int:
    """마법 데미지/힐 위력 계산

    Args:
        magic_stat: 마법 스탯
        power: 마테리아 파워
        job: 직업 (마테리아 위버, 턱스 등)

    Returns:
        최종 마법 데미지/힐량
    """
    power_half = max(5, apply_damage_rounding(power))  # 최소값 5
    base_magic = apply_damage_rounding(magic_stat) + power_half + random_utils.randint(1, power_half) + 4

    # 직업 보너스 (마테리아 위버, 턱스: +1d10)
    if job in ['마테리아 위버', '턱스']:
        final_magic = base_magic + random_utils.randint(1, 10)
    else:
        final_magic = base_magic

    return final_magic

def calculate_def_mitigation(physics_stat: int) -> int:
    """방어 데미지 감소량 계산"""
    return apply_evasion_flooring(physics_stat) + random_utils.randint(1, 11) + 4


def calculate_evasion_chance(agility_stat: int, environment_penalty: float = 1.0) -> bool:
    """민첩 스탯을 입력하면 자동으로 True/False로 회피 성공 여부 뱉음

    Args:
        agility_stat: 민첩 스탯
        environment_penalty: 환경 효과 페널티 (예: gravity_anomaly일 경우 0.5)

    Returns:
        bool: 회피 성공 여부
    """
    if agility_stat < 10:
        return False
    else:
        evasion_value = apply_evasion_flooring(agility_stat) + random_utils.randint(1, 10)
        base_chance = evasion_value / 200.0
        # 환경 효과 페널티 적용 (중력 이상일 경우 50% 감소)
        final_chance = base_chance * environment_penalty
        if random_utils.get_random() < final_chance:
            return True
        else:
            return False

def calculate_dmw_number(charm_stat: int) -> int:
    """DMW 결과를 계산 (매력 스탯 - 10) / 2 + 4d10"""
    bonus = apply_damage_rounding(charm_stat)
    dice_rolls = sum(random_utils.randint(1, 10) for _ in range(4))
    return bonus + dice_rolls