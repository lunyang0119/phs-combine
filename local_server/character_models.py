import discord
from discord.ext import commands
import os
from dotenv import load_dotenv
import logging
import utils
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from typing import List, Dict, Optional, Any
import pandas as pd
from functools import cache
import utils
# import random
from combat import random_utils
import constants

class Character:
    def __init__(self, data: pd.Series):
        self.id: str = str(data.name)
        self.name: str = data.get('name', '이름없음')
        self.job: str = data.get('job', '일반')  # 직업 속성 추가 (몬스터/플레이어 공통)
        self.max_hp: int = int(data.get('max_hp', 0))
        self.current_hp: int = int(data.get('current_hp', 0))
        self.materia_owned = data.get('materia_owned', '없음')
        self.stats: Dict[str, int] = {
            'physics': int(data.get('physics', 0)),
            'magic': int(data.get('magic', 0)),
            'agility': int(data.get('agility', 0)),
            'charisma': int(data.get('charisma', 0)),
            'status': data.get('status', '정상')  # status를 stats에 포함시켜 take_damage에서 접근 가능
        }
        # 전투 중 변경될 수 있는 상태 플래그
        self.is_dead: int = int(data.get('is_dead', 0))
        self.evasion_flag: int = int(data.get('evasion_flag', 0))
        self.defend_flag: int = int(data.get('defend_flag', 0))
        self.limit_flag: int = int(data.get('limit_flag', 0))
        self.status: str = data.get('status', '정상')

    def take_damage(self, amount: int, limit_break: bool = False, environment_penalty: float = 1.0) -> int:
        """defend_flag계산 포함. 최종 hp가 음수가 되지 않도록 만드는 함수. 스트라이커 리밋 사용하여 자신이 데미지 입을 경우, defend_flag가 1이라도 방어 안됨

        Args:
            amount: 데미지 양
            limit_break: True시 방어 무시
            environment_penalty: 환경 효과 회피 페널티 (gravity_anomaly일 경우 0.5)
        """

        # 게쉬틴안나 무적 상태 체크
        status = str(self.stats.get('status', '정상'))
        if 'geshtinn_invincible' in status:
            return self.current_hp  # 무적 상태면 데미지 0

        if self.evasion_flag > 0 and utils.calculate_evasion_chance(self.stats['agility'], environment_penalty):
            return self.current_hp # 회피 성공 시 데미지 0, 현재 HP를 그대로 반환

        if self.defend_flag > 0 and not limit_break:
            ph_stat = self.stats['physics']
            mitigation = utils.calculate_def_mitigation(ph_stat)
            damage = max(0, amount - mitigation)
        else:
            damage = amount

        # 이졸데 보호 효과 체크 (데미지 50% 감소)
        if 'isolde_protection' in status:
            damage = int(damage * 0.5)

        final_damage = max(0, self.current_hp-damage)

        if final_damage == 0:
            self.is_dead = 3

        return final_damage
    
    def heal(self, amount: int, is_dead: int = 0) -> int:
        """
        HP를 회복합니다.

        Args:
            amount: 회복량
            is_dead: 전투불능 카운트 (0이 아니면 힐량 강제로 0)

        Returns:
            회복 후 현재 HP

        Note:
            rule.md에 따라 is_dead > 0일 때 힐량이 강제로 0이 됩니다.
            단, DMW 에단 효과 등으로 is_dead를 0으로 만든 후에는 힐이 가능합니다.
        """
        # 전투 불능 상태에서는 힐량을 0으로 강제 (rule.md 기준)
        if is_dead > 0:
            amount = 0  # 힐량을 0으로 강제

        cure_hp = self.current_hp + amount
        new_hp = min(cure_hp, self.max_hp)
        self.current_hp = new_hp
        return new_hp
    
    
    def is_alive(self) -> bool:
        """생존 여부에 따라 t/f 반환"""
        if self.is_dead <= 0 and self.current_hp >= 1:
            return True
        else:
            return False
        
    def change_flag(self, ):
        """플래그 변환"""
        #TODO: 플래그를  자유자재로 변환할 수 있는 커맨드(필요없으면 삭제 가능)
        self.defend_flag = 1
        

class Player(Character):
    """플레이어 고유 기능 정의"""
    def __init__(self, data: pd.Series):
        super().__init__(data) # 부모클래스(character)의 init 먼저 실행
        self.id: str = data.get('id', '없음')
        self.job: str = data.get('job', '백수')
        self.max_mp: int = int(data.get('max_mp', 0))
        self.current_mp: int = int(data.get('current_mp', 0))
        self.current_shop_points: int = int(data.get('current_shop_points',0))

    def physical_attack(self, target: Character, status: str = '정상', environment_penalty: float = 1.0, attack_count: int = 1) -> Dict[str, Any]:
        """단일대상 물리공격 데미지 계산(방어계산 포함됨)

        Args:
            target: 공격 대상
            status: 공격자 상태
            environment_penalty: 환경 페널티
            attack_count: 공격 횟수 (클로드 효과 시 3)
        """
        total_damage = 0
        for _ in range(attack_count):
            damage = utils.calculate_physical_damage(self.stats['physics'], self.job, status)
            initial_hp = target.current_hp
            final_hp = target.take_damage(damage, environment_penalty=environment_penalty)
            actual_damage = initial_hp - final_hp
            total_damage += actual_damage

            # 대상이 죽으면 추가 공격 중단
            if final_hp == 0:
                break

        return {
            'target_name': target.name,
            'damage': total_damage,
            'final_hp': target.current_hp,
            'action': '물리공격',
            'attack_count': attack_count
        }

    def limit_break_kind(self) -> Dict[str, Any]:
        """리밋별 효과 계산. 실제 적용은 호출부에서 처리."""
        if self.job == '스트라이커':
            hp_cost = round(self.current_hp * constants.STRIKER_LIMIT_HP_COST)
            damage = round(self.max_hp * constants.STRIKER_LIMIT_DAMAGE)
            return {
                'type': 'damage',
                'hp_cost': hp_cost,
                'value': damage,
                'targets': 'enemies'
            }
        elif self.job == '마테리아 위버':
            mp_cost = round(self.current_mp * constants.WEAVER_LIMIT_MP_COST)
            heal_percentage = constants.WEAVER_LIMIT_HEAL
            return {
                'type': 'heal',
                'mp_cost': mp_cost,
                'value': heal_percentage,
                'targets': 'allies'
            }
        elif self.job == '솔져':
            # 팔도일섬 (八刀一閃): 1~8회 연속 베기
            hp_cost = round(self.current_hp * constants.SOLDIER_LIMIT_HP_COST)
            slash_count = random_utils.randint(constants.SOLDIER_LIMIT_MIN_HITS, constants.SOLDIER_LIMIT_MAX_HITS)
            base_damage_per_slash = utils.apply_damage_rounding(self.stats['physics']) + random_utils.randint(1, 10) + random_utils.randint(1, 6)
            
            return {
                'type': 'soldier_octoslash',
                'hp_cost': hp_cost,
                'slash_count': slash_count,
                'damage_per_slash': base_damage_per_slash,
                'total_damage': base_damage_per_slash * slash_count,
                'targets': 'single_enemy',
                'description': f'HP {hp_cost} 소모하여 단일 적에게 {slash_count}연타 공격! (총 데미지: {base_damage_per_slash * slash_count})'
            }
        elif self.job == '턱스':
            # 전격 제압: 단일 적을 3턴간 스턴
            return {
                'type': 'turks_stun',
                'mp_cost': constants.TURKS_LIMIT_MP_COST,
                'stun_duration': constants.TURKS_STUN_DURATION,  
                'targets': 'single_enemy',
                'description': f'MP {constants.TURKS_LIMIT_MP_COST} 소모하여 단일 적을 {constants.TURKS_STUN_DURATION - 1}턴간 기절시킵니다.'
            }
        else:
            return {}
        
