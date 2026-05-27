"""
보스 스킬 시스템 모듈 (Phase 3)

보스의 HP 트리거 기반 스킬 발동 및 효과 적용을 관리합니다.
"""

import discord
import asyncio
# import random
from combat import random_utils
import json
import logging
from typing import Dict, Any, List, Optional
import pandas as pd
from character_models import Character
import utils

logger = logging.getLogger(__name__)


class BossSkillSystem:
    """보스 HP 트리거 기반 스킬 시스템 관리 클래스"""

    @staticmethod
    async def check_boss_skill_trigger(
        monster_id: str,
        channel: discord.TextChannel,
        active_battles: Dict[int, Dict[str, Any]],
        sheet_handler
    ) -> None:
        """보스 HP 조건 확인 후 Boss_Skills 시트의 스킬 발동 (레거시 함수)

        Args:
            monster_id: 몬스터 ID
            channel: Discord 채널
            active_battles: 활성 전투 딕셔너리
            sheet_handler: Google Sheets 핸들러
        """
        channel_id = channel.id
        if channel_id not in active_battles:
            return

        battle_state = active_battles[channel_id]
        participants_cache = battle_state['participants_cache']

        # 몬스터 데이터 확인
        if monster_id not in participants_cache.index:
            return

        monster_data = participants_cache.loc[monster_id]

        # 보스가 아니면 스킵
        monster_type = str(monster_data.get('monster_type', '')).lower()
        if monster_type != 'boss':
            return

        # 현재 HP % 계산
        current_hp = int(monster_data['current_hp'])
        max_hp = int(monster_data['max_hp'])

        if max_hp == 0:
            return

        current_hp_percent = (current_hp / max_hp) * 100

        # 해당 보스의 스킬 목록 조회
        boss_skills = sheet_handler.get_boss_skills(monster_id)

        if boss_skills.empty:
            return

        # 발동 가능한 스킬 체크 (HP % 높은 순으로 정렬되어 있음)
        for idx, skill_row in boss_skills.iterrows():
            await BossSkillSystem._try_trigger_skill(
                skill_row, current_hp_percent, monster_data,
                channel, channel_id, sheet_handler
            )

    @staticmethod
    async def _try_trigger_skill(
        skill: pd.Series,
        current_hp_percent: float,
        monster_data: pd.Series,
        channel: discord.TextChannel,
        channel_id: int,
        sheet_handler
    ) -> None:
        """개별 스킬 발동 조건 체크 및 실행 (레거시 함수)

        Args:
            skill: 스킬 데이터
            current_hp_percent: 현재 HP 퍼센트
            monster_data: 몬스터 데이터
            channel: Discord 채널
            channel_id: 채널 ID
            sheet_handler: Google Sheets 핸들러
        """
        trigger_hp = float(skill.get('trigger_hp_percent', 0))
        used_flag_raw = skill.get('used_flag', 0)
        if used_flag_raw == '' or used_flag_raw is None or pd.isna(used_flag_raw):
            used_flag = 0
        else:
            used_flag = int(used_flag_raw)

        # skill.name은 MultiIndex의 두 번째 요소 (skill_id)
        if isinstance(skill.name, tuple):
            boss_id, skill_id = skill.name
        else:
            # 단일 인덱스인 경우 (스킬 1개만 있는 경우)
            boss_id = str(monster_data.name)
            skill_id = str(skill.name)

        # 발동 조건: HP가 트리거 이하 & 아직 사용 안 함
        if current_hp_percent <= trigger_hp and used_flag == 0:
            skill_name = skill.get('skill_name', '???')
            monster_name = monster_data.get('name', '???')

            # 스킬 발동 알림
            embed = discord.Embed(
                title=f"🔥 보스 특수 스킬 발동!",
                description=f"**{monster_name}**이(가) **{skill_name}**을(를) 사용합니다!\n\n*{skill.get('description', '')}*",
                color=discord.Color.dark_red()
            )
            await channel.send(embed=embed)

            await asyncio.sleep(1.5)  # 연출 대기

            # 스킬 효과 적용 (레거시 방식)
            await BossSkillSystem._apply_boss_skill_effect_legacy(
                skill, monster_data, channel, channel_id, sheet_handler
            )

            # used_flag 업데이트
            sheet_handler.update_boss_skill_used_flag(boss_id, skill_id)

            # 캐시도 즉시 업데이트
            if (boss_id, skill_id) in sheet_handler.boss_skills_sheet_cache.index:
                sheet_handler.boss_skills_sheet_cache.at[(boss_id, skill_id), 'used_flag'] = 1

    @staticmethod
    async def _apply_boss_skill_effect_legacy(
        skill: pd.Series,
        monster_data: pd.Series,
        channel: discord.TextChannel,
        channel_id: int,
        sheet_handler
    ) -> None:
        """보스 스킬의 실제 효과를 적용 (레거시 함수 - 이전 버전)

        Args:
            skill: 스킬 데이터
            monster_data: 몬스터 데이터
            channel: Discord 채널
            channel_id: 채널 ID
            sheet_handler: Google Sheets 핸들러
        """
        effect_type = skill.get('effect_type', 'none')
        target_type = skill.get('target_type', 'none')

        # active_battles에서 전투 상태 가져오기
        # 이 함수는 레거시 함수이므로 동작하지 않을 수 있음
        logger.warning("_apply_boss_skill_effect_legacy 호출됨 - 레거시 함수로 정상 동작하지 않을 수 있습니다.")

        # 새로운 시스템을 사용하도록 경고 메시지 전송
        await channel.send("⚠️ 보스 스킬 시스템이 업데이트되었습니다. 관리자에게 문의하세요.")

    @staticmethod
    async def check_boss_skill_triggers(
        interaction: discord.Interaction,
        battle_state: Dict[str, Any],
        sheet_handler
    ) -> None:
        """모든 보스 몬스터의 HP 트리거 체크 및 스킬 발동 (Phase 3)

        Args:
            interaction: Discord 인터랙션
            battle_state: 현재 전투 상태
            sheet_handler: Google Sheets 핸들러
        """
        participants_cache = battle_state['participants_cache']

        # 보스 몬스터만 필터링
        bosses = participants_cache[
            (participants_cache['monster_type'] == 'boss') |
            (participants_cache['monster_type'] == 'Boss')
        ]

        for boss_id in bosses.index:
            # 이미 죽은 보스는 스킬 발동 안 함
            if int(bosses.loc[boss_id, 'is_dead']) > 0:
                continue

            # 보스 스킬 조회
            boss_skills = sheet_handler.get_boss_skills(boss_id)
            if boss_skills.empty:
                continue

            current_hp = int(bosses.loc[boss_id, 'current_hp'])
            max_hp = int(bosses.loc[boss_id, 'max_hp'])
            current_hp_percent = (current_hp / max_hp) * 100

            # 각 스킬의 트리거 조건 확인 (HP가 높은 것부터 = trigger_hp_percent 내림차순)
            for skill_id in boss_skills.index:
                skill_data = boss_skills.loc[skill_id]

                # used_flag 체크 (이미 사용한 스킬은 스킵)
                if int(skill_data.get('used_flag', 0)) == 1:
                    continue

                trigger_hp_percent = float(skill_data['trigger_hp_percent'])

                # HP가 트리거 이하로 떨어졌는지 확인
                if current_hp_percent <= trigger_hp_percent:
                    # Phase 3.3: 트리거 조건 만족 로깅
                    skill_name = skill_data.get('skill_name', '알 수 없음')
                    logger.info(
                        f"[보스 스킬 트리거] {boss_id}의 {skill_name}({skill_id}) 조건 충족 | "
                        f"현재 HP: {current_hp_percent:.1f}% ≤ 트리거: {trigger_hp_percent}%"
                    )

                    # 스킬 발동!
                    await BossSkillSystem.execute_boss_skill(
                        interaction,
                        battle_state,
                        boss_id,
                        skill_id,
                        skill_data,
                        sheet_handler
                    )

                    # used_flag 업데이트
                    sheet_handler.update_boss_skill_used_flag(boss_id, skill_id)

                    # 한 턴에 하나의 스킬만 발동
                    break

    @staticmethod
    def create_boss_skill_embed(
        boss_name: str,
        skill_name: str,
        description: str,
        effect_type: str,
        skill_data: pd.Series
    ) -> discord.Embed:
        """보스 스킬용 임베드 생성 (Phase 3.1)

        Args:
            boss_name: 보스 이름
            skill_name: 스킬 이름
            description: 스킬 설명
            effect_type: 효과 타입
            skill_data: 스킬 데이터

        Returns:
            discord.Embed: 생성된 임베드
        """
        # effect_type별 색상
        effect_config = {
            'summon': {'color': discord.Color.purple(), 'type_name': '소환'},
            'damage_mp_drain': {'color': discord.Color.blue(), 'type_name': 'MP 흡수'},
            'lifesteal': {'color': discord.Color.dark_red(), 'type_name': '생명력 흡수'},
            'stat_debuff': {'color': discord.Color.dark_gray(), 'type_name': '디버프'},
            'buff_self': {'color': discord.Color.gold(), 'type_name': '버프'},
            'damage_all': {'color': discord.Color.red(), 'type_name': '광역 공격'},
            'death_sentence': {'color': discord.Color.dark_purple(), 'type_name': '죽음의 선고'},
            'instant_hp_one': {'color': discord.Color.dark_red(), 'type_name': '최후의 저주'},
            'rage': {'color': discord.Color.orange(), 'type_name': '분노'}
        }

        config = effect_config.get(effect_type, {'color': discord.Color.dark_red(), 'type_name': '특수 스킬'})

        embed = discord.Embed(
            title=f"보스 스킬 발동: {boss_name}의 {skill_name}",
            description=description,
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

    @staticmethod
    async def execute_boss_skill(
        interaction: discord.Interaction,
        battle_state: Dict[str, Any],
        boss_id: str,
        skill_id: str,
        skill_data: pd.Series,
        sheet_handler,
        combat_utils_class=None
    ) -> None:
        """보스 스킬 실행 (Phase 3)

        Args:
            interaction: Discord 인터랙션
            battle_state: 현재 전투 상태
            boss_id: 보스 ID
            skill_id: 스킬 ID
            skill_data: 스킬 데이터 (Series)
            sheet_handler: Google Sheets 핸들러
            combat_utils_class: CombatUtils 클래스 (apply_damage_to_target, calculate_turn_order 사용)
        """
        participants_cache = battle_state['participants_cache']
        boss_name = participants_cache.loc[boss_id, 'name']
        skill_name = skill_data.get('skill_name', '알 수 없는 스킬')
        description = skill_data.get('description', '')
        effect_type = skill_data['effect_type']
        target_type = skill_data['target_type']

        # 대상 선택
        targets = BossSkillSystem.select_targets_by_type(battle_state, target_type, boss_id)

        if not targets:
            await interaction.channel.send(f"{skill_name}의 대상을 찾을 수 없습니다.")
            return

        # effect_type별 처리
        result_message = ""

        if effect_type == 'summon':
            result_message = await BossSkillSystem.execute_summon(
                battle_state, skill_data, boss_id, sheet_handler, combat_utils_class
            )

        elif effect_type == 'damage_mp_drain':
            result_message = await BossSkillSystem.execute_damage_mp_drain(
                battle_state, skill_data, targets, boss_id, combat_utils_class
            )

        elif effect_type == 'lifesteal':
            result_message = await BossSkillSystem.execute_lifesteal(
                battle_state, skill_data, targets, boss_id, combat_utils_class
            )

        elif effect_type == 'stat_debuff':
            result_message = await BossSkillSystem.execute_stat_debuff(
                battle_state, skill_data, targets, boss_id
            )

        elif effect_type == 'buff_self':
            result_message = await BossSkillSystem.execute_buff_self(
                battle_state, skill_data, targets, boss_id
            )

        elif effect_type == 'damage_all':
            result_message = await BossSkillSystem.execute_damage_all(
                battle_state, skill_data, targets, boss_id, sheet_handler, combat_utils_class
            )

        elif effect_type == 'death_sentence':
            result_message = await BossSkillSystem.execute_death_sentence(
                battle_state, skill_data, targets, boss_id
            )

        elif effect_type == 'instant_hp_one':
            result_message = await BossSkillSystem.execute_instant_hp_one(
                battle_state, skill_data, targets, boss_id
            )

        elif effect_type == 'rage':
            result_message = await BossSkillSystem.execute_rage(
                battle_state, skill_data, targets, boss_id
            )

        else:
            result_message = f"알 수 없는 effect_type: {effect_type}"

        # 스킬 발동 + 결과를 하나의 임베드로 통합
        embed = BossSkillSystem.create_boss_skill_embed(
            boss_name,
            skill_name,
            description,
            effect_type,
            skill_data
        )

        # 결과 메시지를 임베드 필드로 추가
        if result_message:
            embed.add_field(
                name="효과",
                value=result_message,
                inline=False
            )

        await interaction.channel.send(embed=embed)

        # Phase 3.3: 향상된 로깅
        logger.info(
            f"[보스 스킬] {boss_name}({boss_id})의 {skill_name}({skill_id}) 발동 | "
            f"타입: {effect_type} | 대상: {target_type} | 영향받은 대상: {len(targets)}명"
        )

    @staticmethod
    def select_targets_by_type(
        battle_state: Dict[str, Any],
        target_type: str,
        skill_user_id: str
    ) -> List[str]:
        """target_type에 따라 대상 ID 리스트 반환 (Phase 3)

        Args:
            battle_state: 전투 상태
            target_type: 대상 타입
            skill_user_id: 스킬 사용자 ID

        Returns:
            대상 ID 리스트
        """
        participants = battle_state['participants_cache']

        if target_type == 'highest_hp_player':
            # HP가 가장 높은 생존 플레이어
            players = participants[participants['monster_type'] == 'player']
            alive_players = players[players['is_dead'] == 0]
            if alive_players.empty:
                return []
            target_id = alive_players['current_hp'].idxmax()
            return [target_id]

        elif target_type == 'lowest_hp_player':
            # HP가 가장 낮은 생존 플레이어
            players = participants[participants['monster_type'] == 'player']
            alive_players = players[players['is_dead'] == 0]
            if alive_players.empty:
                return []
            target_id = alive_players['current_hp'].idxmin()
            return [target_id]

        elif target_type == 'all_players':
            # 모든 생존 플레이어
            players = participants[participants['monster_type'] == 'player']
            alive_players = players[players['is_dead'] == 0]
            return alive_players.index.tolist()

        elif target_type == 'single_player':
            # 랜덤 플레이어 1명
            players = participants[participants['monster_type'] == 'player']
            alive_players = players[players['is_dead'] == 0]
            if alive_players.empty:
                return []
            return [random_utils.choice(alive_players.index.tolist())]

        elif target_type == 'all_enemies':
            # 몬스터 진영 전체
            monsters = participants[
                (participants['monster_type'] != 'player') &
                (participants['is_dead'] == 0)
            ]
            return monsters.index.tolist()

        elif target_type == 'self':
            # 스킬 사용자 본인
            return [skill_user_id]

        else:
            logger.warning(f"알 수 없는 target_type: {target_type}")
            return []

    # ===== 스킬 효과 실행 메서드들 =====

    @staticmethod
    async def execute_summon(
        battle_state: Dict[str, Any],
        skill_data: pd.Series,
        boss_id: str,
        sheet_handler,
        combat_utils_class
    ) -> str:
        """몬스터 소환 실행 (Phase 3)"""
        value = json.loads(skill_data['value'])
        monster_ids = value.get('monster_ids', [])

        summoned_names = []
        for monster_id in monster_ids:
            # 이미 전투에 참여 중인지 확인
            if monster_id in battle_state['participants_cache'].index:
                continue

            # 전투에 몬스터 추가
            new_participant = sheet_handler.add_participant_to_battle(monster_id)
            if new_participant is not None:
                summoned_names.append(new_participant['name'])
                battle_state['participants_cache'] = sheet_handler.battle_stat_sheet_cache

        # 턴 순서 재계산 (소환된 몬스터를 턴 순서에 포함)
        if summoned_names:
            from combat.battle_manager import BattleManager
            BattleManager.determine_turn_order(battle_state)
            logger.info(f"[보스 스킬 - 소환] 턴 순서 재계산 완료 (소환된 몬스터 {len(summoned_names)}마리 추가)")

        # 결과 메시지
        if summoned_names:
            # Phase 3.3: 소환 성공 로깅
            logger.info(f"[보스 스킬 - 소환] {boss_id}가 {len(summoned_names)}마리 소환: {', '.join(monster_ids)}")
            return f"**소환!** {', '.join(summoned_names)}이(가) 전투에 참여했다!"
        else:
            logger.warning(f"[보스 스킬 - 소환] {boss_id}의 소환 실패: {', '.join(monster_ids)}")
            return "소환 실패... (이미 참여 중이거나 존재하지 않는 몬스터)"

    @staticmethod
    async def execute_damage_mp_drain(
        battle_state: Dict[str, Any],
        skill_data: pd.Series,
        targets: List[str],
        boss_id: str,
        combat_utils_class
    ) -> str:
        """데미지 + MP 흡수 실행 (Phase 3)"""
        value = json.loads(skill_data['value'])
        mp_drain_percent = value.get('mp_drain_percent', 0) / 100.0

        participants = battle_state['participants_cache']
        damage = int(skill_data['damage'])

        results = []
        reaction_messages = []

        for target_id in targets:
            # 데미지 적용
            if combat_utils_class:
                actual_damage, final_hp = combat_utils_class.apply_damage_to_target(
                    battle_state, target_id, damage
                )
            else:
                # 폴백: Character 클래스 사용
                target_char = Character(participants.loc[target_id])
                final_hp = target_char.take_damage(damage)
                participants.loc[target_id, 'current_hp'] = final_hp
                actual_damage = damage

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

    @staticmethod
    async def execute_lifesteal(
        battle_state: Dict[str, Any],
        skill_data: pd.Series,
        targets: List[str],
        boss_id: str,
        combat_utils_class
    ) -> str:
        """생명력 흡수 실행 (Phase 3)"""
        value = json.loads(skill_data['value'])
        heal_percent = value.get('heal_percent', 0) / 100.0

        participants = battle_state['participants_cache']
        damage = int(skill_data['damage'])

        total_heal = 0
        results = []
        reaction_messages = []

        for target_id in targets:
            # 데미지 적용
            if combat_utils_class:
                actual_damage, final_hp = combat_utils_class.apply_damage_to_target(
                    battle_state, target_id, damage
                )
            else:
                # 폴백: Character 클래스 사용
                target_char = Character(participants.loc[target_id])
                final_hp = target_char.take_damage(damage)
                participants.loc[target_id, 'current_hp'] = final_hp
                actual_damage = damage

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

    @staticmethod
    async def execute_stat_debuff(
        battle_state: Dict[str, Any],
        skill_data: pd.Series,
        targets: List[str],
        boss_id: str
    ) -> str:
        """스탯 디버프 실행 (Phase 3)"""
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

        # 원본 스탯 백업
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
            value = json.loads(value_str) if value_str and value_str != '{}' else {}
            debuff_type = "석화" if value.get('petrified') or stat_name == 'petrified' else f"{stat_name} 감소"
            logger.info(f"[보스 스킬 - 디버프] {boss_id}가 {len(targets)}명에게 {debuff_type} 부여 ({duration}턴)")

        return '\n'.join(results)

    @staticmethod
    async def execute_buff_self(
        battle_state: Dict[str, Any],
        skill_data: pd.Series,
        targets: List[str],
        boss_id: str
    ) -> str:
        """자기 강화 실행 (Phase 3)"""
        value = json.loads(skill_data['value'])
        stat_name = value.get('stat', '')
        increase = value.get('increase', 0)
        duration = int(skill_data['duration'])

        participants = battle_state['participants_cache']

        # 원본 스탯 백업
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

    @staticmethod
    async def execute_damage_all(
        battle_state: Dict[str, Any],
        skill_data: pd.Series,
        targets: List[str],
        boss_id: str,
        sheet_handler,
        combat_utils_class
    ) -> str:
        """전체 공격 실행 (Phase 3)"""
        damage = int(skill_data['damage'])
        results = []
        reaction_messages = []

        for target_id in targets:
            if combat_utils_class:
                actual_damage, final_hp = combat_utils_class.apply_damage_to_target(
                    battle_state, target_id, damage
                )
            else:
                # 폴백: Character 클래스 사용
                participants = battle_state['participants_cache']
                target_char = Character(participants.loc[target_id])
                final_hp = target_char.take_damage(damage)
                participants.loc[target_id, 'current_hp'] = final_hp
                actual_damage = damage

            target_name = battle_state['participants_cache'].loc[target_id, 'name']
            results.append(f"**{target_name}**에게 {actual_damage} 데미지! (현재 HP: {final_hp})")

            # Phase 3.2: 피격 반응 메시지 (플레이어의 피격대사 필요 없음)
            job = str(battle_state['participants_cache'].loc[target_id, 'job'])
            # if job == 'player':
            #     reaction = utils.get_monster_damage_reaction(target_id, sheet_handler.char_sheet_cache)
            #     if reaction:
            #         reaction_messages.append(f"💭 *{target_name}: {reaction['message']}*")

        full_result = '\n'.join(results)
        if reaction_messages:
            full_result += '\n\n' + '\n'.join(reaction_messages)

        return full_result

    @staticmethod
    async def execute_death_sentence(
        battle_state: Dict[str, Any],
        skill_data: pd.Series,
        targets: List[str],
        boss_id: str
    ) -> str:
        """죽음의 선고: N턴 후 즉사 (Phase 3)"""
        duration = int(skill_data.get('duration', 3))
        participants = battle_state['participants_cache']

        results = []
        for target_id in targets:
            target_name = participants.loc[target_id, 'name']

            # 상태 추가
            current_status = str(participants.loc[target_id, 'status'])
            if current_status and current_status not in ['정상', 'nan', '']:
                participants.loc[target_id, 'status'] = f'{current_status}|death_sentence:{duration}'
            else:
                participants.loc[target_id, 'status'] = f'death_sentence:{duration}'

            results.append(f"💀 **{target_name}**에게 죽음의 선고가 내려졌습니다! **{duration}턴 후 즉사**합니다.")

        return '\n'.join(results)

    @staticmethod
    async def execute_instant_hp_one(
        battle_state: Dict[str, Any],
        skill_data: pd.Series,
        targets: List[str],
        boss_id: str
    ) -> str:
        """HP를 1로 만드는 즉사급 공격 (Phase 3)"""
        participants = battle_state['participants_cache']
        results = []

        for target_id in targets:
            participants.loc[target_id, 'current_hp'] = 1
            target_name = participants.loc[target_id, 'name']
            results.append(f"💀 **{target_name}**이(가) 저주에 걸려 HP가 1이 되었습니다!")

        return '\n'.join(results)

    # ===== 레거시 보스 스킬 헬퍼 함수들 (Phase 3 이전 버전) =====

    @staticmethod
    def select_skill_targets(target_type: str, participants_cache: pd.DataFrame) -> list:
        """스킬 대상 선택 (레거시 함수)

        DEPRECATED: select_targets_by_type() 사용을 권장합니다.
        """
        all_ids = list(participants_cache.index)
        player_ids = [pid for pid in all_ids if pid.isdigit() and int(participants_cache.loc[pid, 'current_hp']) > 0]
        monster_ids = [mid for mid in all_ids if not mid.isdigit() and int(participants_cache.loc[mid, 'current_hp']) > 0]

        if target_type == 'single_player':
            return [random_utils.choice(player_ids)] if player_ids else []

        elif target_type == 'all_players':
            return player_ids

        elif target_type == 'highest_hp_player':
            if not player_ids:
                return []
            highest_hp_player = max(player_ids, key=lambda pid: int(participants_cache.loc[pid, 'current_hp']))
            return [highest_hp_player]

        elif target_type == 'lowest_hp_player':
            if not player_ids:
                return []
            lowest_hp_player = min(player_ids, key=lambda pid: int(participants_cache.loc[pid, 'current_hp']))
            return [lowest_hp_player]

        elif target_type == 'self':
            return []  # 자기 자신 (monster_data에서 처리)

        elif target_type == 'all_enemies':
            return monster_ids

        else:
            return []

    @staticmethod
    def add_status_to_participant(participants_cache: pd.DataFrame, target_id: str, new_status: str) -> None:
        """참여자에게 상태이상 추가 (기존 상태 유지) - 레거시 헬퍼 함수

        DEPRECATED: StatusEffectManager 사용을 권장합니다.
        """
        current_status = str(participants_cache.loc[target_id, 'status'])

        if current_status and current_status not in ['정상', 'nan', '']:
            participants_cache.loc[target_id, 'status'] = f'{current_status}|{new_status}'
        else:
            participants_cache.loc[target_id, 'status'] = new_status

    # ===== 레거시 스킬 효과 구현 메서드들 (Phase 3 이전 버전) =====

    @staticmethod
    async def effect_death_sentence(
        skill: pd.Series,
        targets: list,
        participants_cache: pd.DataFrame,
        channel: discord.TextChannel
    ):
        """죽음의 선고: N턴 후 즉사 (레거시 함수)

        DEPRECATED: execute_death_sentence() 사용을 권장합니다.
        """
        duration = int(skill.get('duration', 3))

        for target_id in targets:
            target_name = participants_cache.loc[target_id, 'name']

            # 헬퍼 함수로 상태 추가
            BossSkillSystem.add_status_to_participant(participants_cache, target_id, f'death_sentence:{duration}')

            # CombatUtils 사용
            from combat.combat_utils import CombatUtils
            await CombatUtils.send_skill_embed(
                channel,
                "💀 죽음의 선고",
                f"**{target_name}**에게 죽음의 선고가 내려졌습니다!\n**{duration}턴 후 즉사**합니다.",
                discord.Color.dark_purple()
            )

    @staticmethod
    async def effect_stat_debuff(
        skill: pd.Series,
        targets: list,
        participants_cache: pd.DataFrame,
        channel: discord.TextChannel
    ):
        """스탯 감소 디버프 (레거시 함수)

        DEPRECATED: execute_stat_debuff() 사용을 권장합니다.
        """
        duration = int(skill.get('duration', 3))
        value_json = skill.get('value', '{}')

        try:
            value_data = json.loads(value_json)
            stat = value_data.get('stat', 'accuracy')
            decrease = value_data.get('decrease', 30)
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"스킬 데이터 파싱 실패, 기본값 사용: {e}")
            stat = 'accuracy'
            decrease = 30

        desc = ""
        for target_id in targets:
            target_name = participants_cache.loc[target_id, 'name']

            # 헬퍼 함수로 상태 추가
            BossSkillSystem.add_status_to_participant(participants_cache, target_id, f'debuff_{stat}:{duration}:{decrease}')

            desc += f"**{target_name}**의 {stat} -{decrease}% ({duration}턴)\n"

        from combat.combat_utils import CombatUtils
        await CombatUtils.send_skill_embed(channel, "🌑 디버프", desc, discord.Color.dark_gray())

    @staticmethod
    async def effect_damage_mp_drain(
        skill: pd.Series,
        targets: list,
        participants_cache: pd.DataFrame,
        channel: discord.TextChannel
    ):
        """데미지 + MP 감소 (레거시 함수)

        DEPRECATED: execute_damage_mp_drain() 사용을 권장합니다.
        """
        damage = int(skill.get('damage', 35))
        value_json = skill.get('value', '{}')

        try:
            value_data = json.loads(value_json)
            mp_drain_percent = value_data.get('mp_drain_percent', 30)
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"MP 드레인 데이터 파싱 실패, 기본값 사용: {e}")
            mp_drain_percent = 30

        desc = ""
        for target_id in targets:
            target_char = Character(participants_cache.loc[target_id])
            final_hp = target_char.take_damage(damage)
            participants_cache.loc[target_id, 'current_hp'] = final_hp

            # MP 감소
            current_mp = int(participants_cache.loc[target_id, 'current_mp'])
            mp_drain = round(current_mp * (mp_drain_percent / 100))
            new_mp = max(0, current_mp - mp_drain)
            participants_cache.loc[target_id, 'current_mp'] = new_mp

            target_name = participants_cache.loc[target_id, 'name']
            desc += f"**{target_name}**: 데미지 `{damage}`, MP -{mp_drain} (HP: {final_hp}, MP: {new_mp})\n"

            if final_hp == 0:
                participants_cache.loc[target_id, 'is_dead'] = 3

        from combat.combat_utils import CombatUtils
        await CombatUtils.send_skill_embed(channel, "⚡ 고통의 울부짖음", desc, discord.Color.dark_orange())

    @staticmethod
    async def effect_lifesteal(
        skill: pd.Series,
        targets: list,
        monster_data: pd.Series,
        participants_cache: pd.DataFrame,
        channel: discord.TextChannel
    ):
        """생명력 흡수 (레거시 함수)

        DEPRECATED: execute_lifesteal() 사용을 권장합니다.
        """
        damage_percent = int(skill.get('damage', 20))
        value_json = skill.get('value', '{}')

        try:
            value_data = json.loads(value_json)
            heal_percent = value_data.get('heal_percent', 15)
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"생명력 흡수 데이터 파싱 실패, 기본값 사용: {e}")
            heal_percent = 15

        total_drained_hp = 0
        desc = ""

        for target_id in targets:
            current_hp = int(participants_cache.loc[target_id, 'current_hp'])
            max_hp = int(participants_cache.loc[target_id, 'max_hp'])
            drain_amount = round(max_hp * (damage_percent / 100))

            target_char = Character(participants_cache.loc[target_id])
            final_hp = target_char.take_damage(drain_amount)
            participants_cache.loc[target_id, 'current_hp'] = final_hp

            actual_drained = current_hp - final_hp
            total_drained_hp += actual_drained

            target_name = participants_cache.loc[target_id, 'name']
            desc += f"**{target_name}**: HP {actual_drained} 흡수당함 (HP: {final_hp})\n"

            if final_hp == 0:
                participants_cache.loc[target_id, 'is_dead'] = 3

        # 보스 회복
        boss_id = monster_data.name
        boss_current_hp = int(participants_cache.loc[boss_id, 'current_hp'])
        boss_max_hp = int(participants_cache.loc[boss_id, 'max_hp'])
        heal_amount = round(boss_max_hp * (heal_percent / 100))
        new_boss_hp = min(boss_max_hp, boss_current_hp + heal_amount)
        participants_cache.loc[boss_id, 'current_hp'] = new_boss_hp

        desc += f"\n**{monster_data.get('name')}**의 HP `{heal_amount}` 회복! (HP: {new_boss_hp})"

        embed = discord.Embed(
            title="🩸 영혼 흡수",
            description=desc,
            color=discord.Color.dark_purple()
        )
        await channel.send(embed=embed)

    @staticmethod
    async def effect_instant_hp_one(
        skill: pd.Series,
        targets: list,
        participants_cache: pd.DataFrame,
        channel: discord.TextChannel
    ):
        """HP를 1로 만드는 즉사급 공격 (레거시 함수)

        DEPRECATED: execute_instant_hp_one() 사용을 권장합니다.
        """
        for target_id in targets:
            participants_cache.loc[target_id, 'current_hp'] = 1
            target_name = participants_cache.loc[target_id, 'name']

            embed = discord.Embed(
                title="💀 최후의 저주",
                description=f"**{target_name}**이(가) 저주에 걸려 HP가 1이 되었습니다!",
                color=discord.Color.dark_red()
            )
            await channel.send(embed=embed)

    @staticmethod
    async def effect_summon(
        skill: pd.Series,
        monster_data: pd.Series,
        battle_state: dict,
        channel: discord.TextChannel,
        sheet_handler
    ):
        """몬스터 소환 (레거시 함수)

        DEPRECATED: execute_summon() 사용을 권장합니다.
        """
        value_json = skill.get('value', '{}')

        try:
            value_data = json.loads(value_json)
            summon_ids = value_data.get('monster_ids', [])
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"소환 데이터 파싱 실패, 기본값 사용: {e}")
            summon_ids = []

        if not summon_ids:
            await channel.send("❌ 소환할 몬스터 정보가 없습니다.")
            return

        desc = f"**{monster_data.get('name')}**이(가) 원군을 소환했습니다!\n\n"

        for summon_id in summon_ids:
            # 몬스터 추가
            new_participant = sheet_handler.add_participant_to_battle(summon_id)

            if new_participant is not None:
                battle_state['participants_cache'] = sheet_handler.battle_stat_sheet_cache
                battle_state['pending_intruders'].append(summon_id)
                desc += f"• **{new_participant.get('name', summon_id)}** 등장!\n"

        embed = discord.Embed(
            title="🌀 소환",
            description=desc,
            color=discord.Color.blue()
        )
        await channel.send(embed=embed)

    @staticmethod
    async def effect_buff_self(
        skill: pd.Series,
        monster_data: pd.Series,
        participants_cache: pd.DataFrame,
        channel: discord.TextChannel
    ):
        """자기 강화 (레거시 함수)

        DEPRECATED: execute_buff_self() 사용을 권장합니다.
        """
        duration = int(skill.get('duration', 3))
        value_json = skill.get('value', '{}')

        try:
            value_data = json.loads(value_json)
            buff_type = value_data.get('buff_type', 'damage')
            increase = value_data.get('increase', 30)
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"버프 데이터 파싱 실패, 기본값 사용: {e}")
            buff_type = 'damage'
            increase = 30

        boss_id = monster_data.name
        current_status = str(participants_cache.loc[boss_id, 'status'])

        # 기존 상태에 버프 추가
        new_buff = f'buff_{buff_type}:{duration}:{increase}'
        if current_status and current_status != '정상' and current_status != 'nan':
            participants_cache.loc[boss_id, 'status'] = f'{current_status}|{new_buff}'
        else:
            participants_cache.loc[boss_id, 'status'] = new_buff

        embed = discord.Embed(
            title="⚡ 각성",
            description=f"**{monster_data.get('name')}**의 {buff_type} +{increase}% ({duration}턴 지속)!",
            color=discord.Color.gold()
        )
        await channel.send(embed=embed)

    @staticmethod
    async def effect_damage_all(
        skill: pd.Series,
        targets: list,
        participants_cache: pd.DataFrame,
        channel: discord.TextChannel
    ):
        """전체 공격 (레거시 함수)

        DEPRECATED: execute_damage_all() 사용을 권장합니다.
        """
        damage = int(skill.get('damage', 50))

        desc = ""
        for target_id in targets:
            target_char = Character(participants_cache.loc[target_id])
            final_hp = target_char.take_damage(damage)
            participants_cache.loc[target_id, 'current_hp'] = final_hp

            target_name = participants_cache.loc[target_id, 'name']
            desc += f"**{target_name}**: `{damage}` 데미지 (HP: {final_hp})\n"

            if final_hp == 0:
                participants_cache.loc[target_id, 'is_dead'] = 3

        embed = discord.Embed(
            title="💥 광역 공격",
            description=desc,
            color=discord.Color.red()
        )
        await channel.send(embed=embed)

    @staticmethod
    async def execute_rage(
        battle_state: Dict[str, Any],
        skill_data: pd.Series,
        targets: List[str],
        boss_id: str
    ) -> str:
        """분노 스킬 실행: 보스의 공격력을 영구적으로 증가시킵니다.

        Args:
            battle_state: 전투 상태 딕셔너리
            skill_data: 스킬 데이터 (Boss_Skills 시트 row)
            targets: 대상 ID 리스트 (self)
            boss_id: 보스 ID

        Returns:
            str: 실행 결과 메시지
        """
        participants = battle_state['participants_cache']

        if boss_id not in participants.index:
            return f"⚠️ 보스 '{boss_id}'를 찾을 수 없습니다."

        # value 파싱: "physics:+20" 또는 "30" (기본값: physics +30)
        value_str = str(skill_data.get('value', '30'))

        if ':' in value_str:
            # "physics:+20" 형식
            stat_name, amount_str = value_str.split(':')
            stat_name = stat_name.strip()
            amount = int(amount_str.replace('+', '').strip())
        else:
            # "30" 형식 (기본: physics)
            stat_name = 'physics'
            amount = int(value_str)

        # 현재 스탯 가져오기
        current_stat = int(participants.loc[boss_id, stat_name])
        new_stat = current_stat + amount
        participants.loc[boss_id, stat_name] = new_stat

        boss_name = participants.loc[boss_id, 'name']

        # 상태에 분노 플래그 추가 (시각적 표시용)
        current_status = str(participants.loc[boss_id, 'status'])
        rage_tag = f"rage:999"  # 영구 버프
        if current_status in ['정상', 'nan', '', 'None']:
            new_status = rage_tag
        else:
            new_status = f"{current_status}|{rage_tag}"
        participants.loc[boss_id, 'status'] = new_status

        logger.info(f"[보스 스킬 - 분노] {boss_id}가 {stat_name} +{amount} 획득 (영구)")

        return (
            f"😡 **{boss_name}**이(가) 분노했다!\n"
            f"📈 {stat_name.upper()} **{current_stat} → {new_stat}** (+{amount}, 영구)"
        )
