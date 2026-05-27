import discord
import asyncio
# import random
from combat import random_utils
from typing import Dict, Any, Optional
from dmw_data import DMW_FIGURES, DMW_BOSS_FIGURES, FAIL_FIGURE, FAIL_MESSAGES, BOSS_FAIL_MESSAGES
import utils
import logging
import constants

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class DMWSystem:
    def __init__(self, bot, sheet_handler, battle_state: Dict[str, Any], current_player_id: str, boss_mode: bool = False):
        self.bot = bot
        self.sheet_handler = sheet_handler
        self.battle_state = battle_state
        self.player_id = current_player_id
        self.player_data = self.battle_state['participants_cache'].loc[self.player_id]
        self.boss_mode = boss_mode

    async def run_dmw(self, channel: discord.abc.Messageable, debug_mode: bool = False):
        """DMW 시스템을 실행하고 결과 딕셔너리를 반환합니다."""
        charm_stat = int(self.player_data.get('charm', 0))
        dmw_result_value = utils.calculate_dmw_number(charm_stat)

        result_figure_name = None
        is_success = False

        # 보스전투 모드: 당첨 확률 2배 (threshold 낮춤)
        success_threshold = constants.DMW_BOSS_MODE_THRESHOLD if self.boss_mode else constants.DMW_SUCCESS_THRESHOLD

        # 보스전투 모드: 전용 피규어 풀 사용 및 사용된 피규어 제외
        used_figures = self.battle_state.get('used_dmw_figures', []) if self.boss_mode else []
        figure_pool = DMW_BOSS_FIGURES if self.boss_mode else DMW_FIGURES
        available_figures = {name: data for name, data in figure_pool.items() if name not in used_figures}

        # 모든 피규어가 소진된 경우 처리
        if self.boss_mode and not available_figures:
            logger.warning(f"[보스모드] 모든 DMW 피규어 소진! 강제 실패 처리")
            fail_message = "남아 있는 기억이 없다..."
            fail_embed = discord.Embed(
                title="DMW - All Memories Exhausted",
                description=f"**\"{fail_message}\"**",
                color=discord.Color.dark_gray()
            )
            dmw_message = await channel.send(embed=fail_embed)
            return {"success": False, "effect": None}

        if debug_mode:
            # 디버그 모드: 세피로스를 제외한 사용 가능한 피규어 중 랜덤 선택
            debug_figures = {name: data for name, data in available_figures.items() if name != "세피로스"}
            if debug_figures:
                names = list(debug_figures.keys())
                weights = [data['weight'] for data in debug_figures.values()]
                result_figure_name = random_utils.choices(names, weights=weights, k=1)[0]
                is_success = True
                logger.info(f"[디버그 모드] DMW 강제 성공: {result_figure_name}")

        if dmw_result_value == constants.DMW_SEPHIROTH_VALUE and "세피로스" in available_figures:
            result_figure_name = "세피로스"
            is_success = True
        elif dmw_result_value >= success_threshold:
            # 세피로스 제외, 사용 가능한 피규어만
            selectable_figures = {name: data for name, data in available_figures.items() if name != "세피로스"}
            if selectable_figures:
                names = list(selectable_figures.keys())
                weights = [data['weight'] for data in selectable_figures.values()]
                result_figure_name = random_utils.choices(names, weights=weights, k=1)[0]
                is_success = True
                logger.info(f"[보스모드={self.boss_mode}] DMW 당첨: {result_figure_name} (threshold={success_threshold}, value={dmw_result_value}, 남은 피규어: {len(available_figures)})")
        
        # 1. 슬롯머신 연출 시작
        dmw_message = await self._animate_slots(channel, is_success, result_figure_name if result_figure_name else "")
        
        # 2. 결과에 따른 대사 및 최종 메시지 처리
        if is_success and result_figure_name:
            # 보스전투 모드면 보스 전용 피규어 데이터 사용
            figure_data = (DMW_BOSS_FIGURES if self.boss_mode else DMW_FIGURES)[result_figure_name]

            # 플레이어 정보 조회 (player_data에서)
            player_name = self.player_data.get('name', '???')

            # Discord User 객체 가져오기 (아바타용)
            user = None
            if self.player_id.isdigit():
                try:
                    user = await self.bot.fetch_user(int(self.player_id))
                except Exception as e:
                    logger.warning(f"DMW: 유저 정보 가져오기 실패 ({self.player_id}): {e}")

            # 성공 메시지 수정
            success_title = "DMW - Matching Success"
            success_description = f"**[ {result_figure_name} | {result_figure_name} | {result_figure_name} ]**\n{result_figure_name}에 대한 기억이 재생됩니다...."

            # 보스전투 모드: 1회만 발동 안내
            if self.boss_mode:
                remaining_count = 7 - len(self.battle_state.get('used_dmw_figures', []))
                success_description += f"\n\n**라켈의 기억이 흐려진다**.... (남은 기억: {remaining_count}개)"

            success_embed = discord.Embed(
                title=success_title,
                description=success_description,
                color=figure_data['color']
            )

            # 유저 객체가 있으면 아바타 포함, 없으면 이름만
            if user:
                success_embed.set_author(name=player_name, icon_url=user.avatar.url if user.avatar else None)
            else:
                success_embed.set_author(name=player_name)

            await dmw_message.edit(embed=success_embed)
            await asyncio.sleep(1)

            # 대사 순차적 출력
            memories = figure_data.get('memories', [])
            if memories and len(memories) > 0:
                selected = random_utils.choice(memories)
                if selected and len(selected) > 0:
                    for line in selected:
                        memory_embed = discord.Embed(
                            description=f"*\"{line}\"*",
                            color=figure_data['color']
                        )
                        await channel.send(embed=memory_embed)
                        await asyncio.sleep(1.5) # 대사 간 간격

            # 보스전투 모드: 사용된 피규어 기록 후 효과 발동
            if self.boss_mode:
                # 사용된 피규어를 battle_state에 기록
                if 'used_dmw_figures' not in self.battle_state:
                    self.battle_state['used_dmw_figures'] = []
                self.battle_state['used_dmw_figures'].append(result_figure_name)
                logger.info(f"[보스모드] DMW 피규어 '{result_figure_name}' 사용됨 (남은 피규어: {7 - len(self.battle_state['used_dmw_figures'])}개)")

                # 효과는 발동 (최초 1회만)
                return {"success": True, "effect": figure_data['effect']}
            else:
                return {"success": True, "effect": figure_data['effect']}
        else:
            # 실패 메시지 수정 (보스전투 모드면 전용 메시지 사용)
            fail_message_pool = BOSS_FAIL_MESSAGES if self.boss_mode else FAIL_MESSAGES
            fail_message = random_utils.choice(fail_message_pool)
            fail_embed = discord.Embed(
                title="DMW - Matching Failed...",
                description=f"**\"{fail_message}\"**",
                color=discord.Color.dark_gray()
            )
            await dmw_message.edit(embed=fail_embed)

            return {"success": False, "effect": None}

    async def _animate_slots(self, channel: discord.abc.Messageable, is_success: bool, success_figure: Optional[str] = None) -> discord.Message:
        """슬롯머신 연출을 담당하고, 수정할 메시지 객체를 반환"""

        # 보스전투 모드에 따라 올바른 피규어 풀 선택
        figure_pool = DMW_BOSS_FIGURES if self.boss_mode else DMW_FIGURES

        # 최종 슬롯 결정
        if is_success and success_figure:
            final_slots = [success_figure, success_figure, success_figure]
            color = figure_pool[success_figure]['color']
        else:
            # 꽝 패턴: 50% 확률로 캐트시 포함, 50% 일반 불일치
            if random_utils.get_random() < constants.DMW_CATSI_FAIL_CHANCE:
                # 캐트시 포함 패턴
                fail_slots = random_utils.sample(list(figure_pool.keys()), 2)
                fail_slots.append(list(FAIL_FIGURE.keys())[0])  # 캐트시 추가
                random_utils.shuffle(fail_slots)
            else:
                # 일반 불일치 패턴 (3개 모두 다른 피규어)
                fail_slots = random_utils.sample(list(figure_pool.keys()), 3)

            final_slots = fail_slots
            color = discord.Color.dark_gray()

        # 초기 메시지 전송
        slots = ["?", "?", "?"]
        
        # 플레이어 정보 조회
        player_name = self.player_data.get('name', '???')
        
        # Discord User 객체 가져오기 (아바타용)
        user = None
        if self.player_id.isdigit():
            try:
                user = await self.bot.fetch_user(int(self.player_id))
            except Exception as e:
                logger.warning(f"DMW 애니메이션: 유저 정보 가져오기 실패 ({self.player_id}): {e}")
        
        embed = discord.Embed(
            title="DMW - Digital Mind Wave",
            description=f"**[ {slots[0]} | {slots[1]} | {slots[2]} ]**",
            color=discord.Color.light_grey()
        )
        
        
        # 유저 객체가 있으면 아바타 포함, 없으면 이름만
        if user:
            embed.set_author(name=player_name, icon_url=user.avatar.url if user.avatar else None)
        else:
            embed.set_author(name=player_name)
        
        message = await channel.send(embed=embed)

        # 슬롯 애니메이션
        for i in range(3):
            await asyncio.sleep(constants.DMW_SLOT_DELAY) # 슬롯이 바뀌는 시간 (1.5초 → 0.8초로 단축)
            slots[i] = final_slots[i]
            embed.description = f"**[ {slots[0]} | {slots[1]} | {slots[2]} ]**"
            embed.color = color
            await message.edit(embed=embed)

        await asyncio.sleep(constants.DMW_FINAL_DELAY)
        return message