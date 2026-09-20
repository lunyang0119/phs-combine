import discord
from discord.ext import commands
from discord import app_commands
# import random
from combat import random_utils
import pytz
from datetime import datetime
import asyncio
import logging
import json
import numpy as np
from collections import Counter
from view import ItemSelectView

logger = logging.getLogger(__name__)

# 티켓 상품명 상수
TICKET_ITEM_NAME = "티켓"


def get_korean_particle(word: str, particle_batchim: str, particle_no_batchim: str) -> str:
    """
    한글 단어의 마지막 글자에 받침이 있는지 확인하여 적절한 조사 반환

    Args:
        word: 조사를 붙일 단어
        particle_batchim: 받침이 있을 때 사용할 조사 (예: '이', '을', '은')
        particle_no_batchim: 받침이 없을 때 사용할 조사 (예: '가', '를', '는')

    Returns:
        적절한 조사 문자열
    """
    if not word:
        return particle_no_batchim

    last_char = word[-1]

    # 한글 유니코드 범위 확인
    if '가' <= last_char <= '힣':
        # 받침 유무 확인: (유니코드 - '가') % 28 == 0이면 받침 없음
        char_code = ord(last_char) - ord('가')
        if char_code % 28 == 0:
            return particle_no_batchim
        else:
            return particle_batchim
    else:
        # 한글이 아닌 경우 기본값
        return particle_no_batchim

def chunked(iterable, n):
    """iterable을 n개씩 잘라서 반환"""
    for i in range(0, len(iterable), n):
        yield iterable[i:i + n]

class ShopCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.sheet_handler = bot.sheet_handler
        self.timezone = pytz.timezone("Asia/Seoul")
        self.selected_items = []

    async def _sync_characters_to_sheet(self):
        """캐릭터 캐시를 Google Sheets에 즉시 동기화"""
        try:
            df_to_write = self.sheet_handler.characters_sheet_cache.reset_index()
            df_to_write = df_to_write.replace({np.nan: None})
            data_to_write = [df_to_write.columns.values.tolist()] + df_to_write.values.tolist()

            await asyncio.to_thread(self.sheet_handler.characters_sheet.clear)
            await asyncio.to_thread(
                self.sheet_handler.characters_sheet.update,
                data_to_write,
                raw=False
            )
            logger.info("매점 시스템: 캐릭터 캐시 -> 시트 동기화 완료")
            return True
        except Exception as e:
            logger.error(f"매점 시스템: 시트 동기화 실패 - {e}", exc_info=True)
            return False

    async def cog_load(self):
        """봇이 시작될 때 현재 상품 목록을 불러옵니다."""
        await self.update_selected_items()
        logger.info("ShopCog 로드 완료")

    async def update_selected_items(self):
        """시트에서 현재 '이번주상품'을 불러와 self.selected_items를 업데이트합니다."""
        all_items = await asyncio.to_thread(self.sheet_handler.shop_sheet.get_all_records)
        self.selected_items = [item for item in all_items if str(item.get("이번주상품", "0")) == "1"]

    def _select_random_items(self):
        """'안나와용'을 제외한 상품 중 5개를 랜덤으로 선정하고 시트를 업데이트합니다."""
        all_items = self.sheet_handler.shop_sheet.get_all_records()

        available_items_with_indices = [
            (i, item) for i, item in enumerate(all_items)
            if item.get("매점에 상품 안나오게 하는 버튼", "").strip() != "안나와용"
        ]

        if len(available_items_with_indices) < 5:
            return False # 선정할 상품이 5개 미만이면 실패 반환

        selected_pairs = random_utils.sample(available_items_with_indices, 5)
        selected_indices = {i for i, item in selected_pairs}

        data = []
        num = 1
        for i in range(len(all_items)):
            row_idx = i + 2
            if i in selected_indices:
                data.append({'range': f'C{row_idx}', 'values': [[1]]})
                data.append({'range': f'E{row_idx}', 'values': [[num]]})
                num += 1
            else:
                data.append({'range': f'C{row_idx}', 'values': [[0]]})
                data.append({'range': f'E{row_idx}', 'values': [[""]]})

        for chunk in chunked(data, 50):
            self.sheet_handler.shop_sheet.batch_update(chunk)

        # 헤더가 없으면 추가
        headers = self.sheet_handler.shop_sheet.row_values(1)
        header_updates = []
        if len(headers) < 3:
            header_updates.append({'range': 'C1', 'values': [["이번주상품"]]})
        if len(headers) < 5:
            header_updates.append({'range': 'E1', 'values': [["이번주상품번호"]]})
        if header_updates:
            self.sheet_handler.shop_sheet.batch_update(header_updates)

        return True

    def reset_shop(self):
        """상품을 새로 뽑고 구매 기록을 초기화합니다."""
        # 상품 새로 뽑기
        success = self._select_random_items()
        if not success:
            return False

        # 구매 기록 시트 초기화
        self.sheet_handler.user_purchases_sheet.clear()
        self.sheet_handler.user_purchases_sheet.append_row(["구매자명", "상품명", "구매일시"])
        return True

    @app_commands.command(name="매점초기화", description="[관리자] 매점 상품을 새로 뽑고 모든 구매기록을 삭제합니다.")
    async def 매점초기화(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        success = await asyncio.to_thread(self.reset_shop)

        if success:
            await self.update_selected_items()
            await interaction.followup.send("매점 상품을 새로 뽑고 구매기록을 초기화했어, 쿠뽀!")
        else:
            await interaction.followup.send("랜덤 선정 가능한 상품이 5개 미만이라 초기화가 불가해, 쿠뽀.\n시트에 상품을 추가하거나 '안나와용' 설정을 확인해줘, 쿠뽀.")

    @app_commands.command(name="매점", description="현재 매점 상품 리스트를 보여줍니다.")
    @app_commands.describe(종류="매점 종류를 선택하세요")
    @app_commands.choices(종류=[
        app_commands.Choice(name="일반", value="일반"),
        app_commands.Choice(name="특수", value="특수")
    ])
    async def 매점(self, interaction: discord.Interaction, 종류: str = "일반"):
        await self.update_selected_items()  # 항상 최신 정보를 반영

        purchases = await asyncio.to_thread(self.sheet_handler.user_purchases_sheet.get_all_records)
        sold_out = set(row.get("상품명") for row in purchases if row.get("상품명"))

        if 종류 == "특수":
            # 특수 매점: 티켓 있음, 포인트 사용, 제한 없음
            all_items = await asyncio.to_thread(self.sheet_handler.shop_sheet.get_all_records)
            ticket_item = next((item for item in all_items if TICKET_ITEM_NAME in item.get("상품명", "")), None)

            embed = discord.Embed(title="특수 매점 상품", colour=0xFFD700)

            # 0번: 티켓 (항상 표시, 상시 구매 가능)
            if ticket_item:
                ticket_name = ticket_item.get("상품명", TICKET_ITEM_NAME)
                ticket_desc = ticket_item.get("설명", "(설명없음)")
                ticket_price = ticket_item.get("가격", 0)
                embed.add_field(
                    name=f"[0] {ticket_name}",
                    value=f"{ticket_desc}\n가격: **{ticket_price}** 포인트\n상태: **상시 구매 가능**",
                    inline=False
                )

            if not self.selected_items:
                embed.description = "이번 주 상품이 없어, 쿠뽀. \n관리자가 `/매점초기화`를 실행해야 해, 쿠뽀."
            else:
                sorted_items = sorted(self.selected_items, key=lambda x: int(x.get("이번주상품번호", 99)))
                for item in sorted_items:
                    번호 = item.get("이번주상품번호", "")
                    name = item.get("상품명", "(이름없음)")
                    desc = item.get("설명", "(설명없음)")
                    price = item.get("가격", 0)
                    embed.add_field(
                        name=f"[{번호}] {name}",
                        value=f"{desc}\n가격: **{price}** 포인트\n상태: **구매 가능**",
                        inline=False
                    )

            embed.set_footer(text="💡 특수 매점: 포인트 사용, 구매 제한 없음")
        else:
            # 일반 매점: 티켓 없음, 포인트 안씀, 1인 1회 제한
            embed = discord.Embed(title="현재 매점 상품", colour=0x00FF00)

            if not self.selected_items:
                embed.description = "현재 매점 상품이 없어, 쿠뽀. \n관리자가 `/매점초기화`를 실행해야 해, 쿠뽀."
            else:
                sorted_items = sorted(self.selected_items, key=lambda x: int(x.get("이번주상품번호", 99)))
                for item in sorted_items:
                    번호 = item.get("이번주상품번호", "")
                    name = item.get("상품명", "(이름없음)")
                    desc = item.get("설명", "(설명없음)")
                    status = "품절" if name in sold_out else "구매 가능"
                    embed.add_field(name=f"[{번호}] {name}", value=f"{desc}\n상태: **{status}**", inline=False)

            embed.set_footer(text="💡 일반 매점: 1인 1회 제한, 선착순")

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="구매", description="매점 상품을 구매합니다.")
    @app_commands.describe(
        item_no="구매할 상품 번호 (특수: 0=티켓, 1-5=상품 / 일반: 1-5=상품)",
        종류="매점 종류를 선택하세요"
    )
    @app_commands.choices(종류=[
        app_commands.Choice(name="일반", value="일반"),
        app_commands.Choice(name="특수", value="특수")
    ])
    async def 구매(self, interaction: discord.Interaction, item_no: int, 종류: str = "일반"):
        display_name = interaction.user.display_name

        await self.update_selected_items()

        if 종류 == "특수":
            # === 특수 매점: 포인트 사용, 제한 없음, 티켓 있음 ===
            await interaction.response.defer(ephemeral=True)

            user_id = str(interaction.user.id)

            # 캐릭터 확인
            char_data = self.sheet_handler.get_char_data(user_id)
            if char_data is None:
                await interaction.followup.send("캐릭터를 먼저 등록해주세요, 쿠뽀!", ephemeral=True)
                return

            current_points = int(char_data.get('current_shop_points', 0))

            # 전체 상품 목록 조회
            all_items = await asyncio.to_thread(self.sheet_handler.shop_sheet.get_all_records)

            # 0번 = 티켓 처리
            if item_no == 0:
                ticket_item = next((item for item in all_items if TICKET_ITEM_NAME in item.get("상품명", "")), None)
                if not ticket_item:
                    await interaction.followup.send("티켓 상품이 매점에 등록되어 있지 않아, 쿠뽀!", ephemeral=True)
                    return
                item = ticket_item
                item_name = item.get("상품명", TICKET_ITEM_NAME)
            else:
                # 이번주 상품 중 해당 번호 찾기
                item = next((i for i in self.selected_items if str(i.get("이번주상품번호", "")) == str(item_no)), None)
                if not item:
                    await interaction.followup.send("해당 번호의 상품이 매점에 없어, 쿠뽀!", ephemeral=True)
                    return
                item_name = item.get("상품명", "")

            price = int(item.get("가격", 0))

            # 포인트 체크
            if current_points < price:
                await interaction.followup.send(
                    f"포인트가 부족해, 쿠뽀!\n현재: **{current_points}** 포인트 / 필요: **{price}** 포인트",
                    ephemeral=True
                )
                return

            # 포인트 차감
            new_points = current_points - price
            self.sheet_handler.characters_sheet_cache.at[user_id, 'current_shop_points'] = new_points

            # 인벤토리에 아이템 추가
            inventory_str = char_data.get('shop_inventory', '[]')
            try:
                inventory = json.loads(inventory_str) if inventory_str else []
            except json.JSONDecodeError:
                inventory = []
            inventory.append(item_name)
            new_inventory_str = json.dumps(inventory, ensure_ascii=False)
            self.sheet_handler.characters_sheet_cache.at[user_id, 'shop_inventory'] = new_inventory_str

            # 시트에 즉시 동기화
            await self._sync_characters_to_sheet()

            # 결과 메시지
            embed = discord.Embed(
                title="[특수] 구매 완료!",
                description=f"**{item_name}** 구매 성공, 쿠뽀!",
                color=discord.Color.gold()
            )
            embed.add_field(name="사용한 포인트", value=f"{price}", inline=True)
            embed.add_field(name="남은 포인트", value=f"{new_points}", inline=True)

            await interaction.followup.send(embed=embed, ephemeral=True)

        else:
            # === 일반 매점: 포인트 안씀, 1인 1회, 품절 있음 ===

            # 0번은 일반 매점에서 사용 불가
            if item_no == 0:
                await interaction.response.send_message(
                    "일반 매점에서는 0번(티켓)을 구매할 수 없어, 쿠뽀! 특수 매점을 이용해줘!",
                    ephemeral=True
                )
                return

            # 이번주 상품 중 해당 번호 찾기
            item = next((i for i in self.selected_items if str(i.get("이번주상품번호", "")) == str(item_no)), None)

            if not item:
                await interaction.response.send_message("해당 번호의 상품이 매점에 없어, 쿠뽀!", ephemeral=True)
                return

            purchases = await asyncio.to_thread(self.sheet_handler.user_purchases_sheet.get_all_records)
            item_name = item.get("상품명", "")

            # 품절 체크
            if any(row.get("상품명", "").strip() == item_name.strip() for row in purchases):
                await interaction.response.send_message("이미 누군가 구매한 상품이야! (품절)", ephemeral=True)
                return

            # 1인 1회 체크
            if any(row.get("구매자명", "") == display_name for row in purchases):
                await interaction.response.send_message("한 번만 구매할 수 있어, 이 욕심쟁이 쿠뽀뽀뽀!", ephemeral=True)
                return

            # 구매 기록 저장
            now = datetime.now(self.timezone).strftime("%Y-%m-%d %H:%M:%S")
            await asyncio.to_thread(self.sheet_handler.user_purchases_sheet.append_row, [display_name, item_name.strip(), now])

            await interaction.response.send_message(f"[{item_no}] {item_name} 구매 완료! (이제 품절쿠뽀)", ephemeral=True)

    @app_commands.command(name="포인트", description="다른 유저에게 매점 포인트를 지급합니다.")
    @app_commands.describe(
        user="포인트를 지급할 유저",
        amount="지급할 포인트 금액"
    )
    async def 포인트(self, interaction: discord.Interaction, user: discord.Member, amount: int):
        await interaction.response.defer(ephemeral=False)

        target_id = str(user.id)
        char_data = self.sheet_handler.get_char_data(target_id)

        if char_data is None:
            await interaction.followup.send(
                f"{user.display_name}님은 캐릭터가 등록되어 있지 않아, 쿠뽀!",
                ephemeral=True
            )
            return

        if amount == 0:
            await interaction.followup.send("0 포인트는 지급할 수 없어, 쿠뽀!", ephemeral=True)
            return

        current_points = int(char_data.get('current_shop_points', 0))
        new_points = current_points + amount

        # 캐시 업데이트
        self.sheet_handler.characters_sheet_cache.at[target_id, 'current_shop_points'] = new_points

        # 시트에 즉시 동기화
        await self._sync_characters_to_sheet()

        if amount > 0:
            embed = discord.Embed(
                title="포인트 지급 완료!",
                description=f"**{user.display_name}**님에게 **{amount}** 포인트를 지급했어, 쿠뽀!",
                color=discord.Color.gold()
            )
        else:
            embed = discord.Embed(
                title="포인트 차감 완료!",
                description=f"**{user.display_name}**님에게서 **{abs(amount)}** 포인트를 차감했어, 쿠뽀!",
                color=discord.Color.red()
            )
        embed.add_field(name="현재 포인트", value=f"{new_points}", inline=True)
        embed.set_footer(text=f"지급자: {interaction.user.display_name}")

        await interaction.followup.send(embed=embed, ephemeral=False)

    @app_commands.command(name="인벤토리", description="구매한 매점 아이템 목록을 확인합니다.")
    async def 인벤토리(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        user_id = str(interaction.user.id)
        char_data = self.sheet_handler.get_char_data(user_id)

        if char_data is None:
            await interaction.followup.send("캐릭터가 등록되어 있지 않아, 쿠뽀!", ephemeral=True)
            return

        # JSON 파싱
        inventory_str = char_data.get('shop_inventory', '[]')
        try:
            inventory = json.loads(inventory_str) if inventory_str else []
        except json.JSONDecodeError:
            inventory = []

        current_points = int(char_data.get('current_shop_points', 0))

        embed = discord.Embed(
            title=f"{char_data.get('character_name', '???')}의 인벤토리",
            color=discord.Color.orange()
        )
        embed.add_field(name="보유 포인트", value=f"**{current_points}**", inline=False)

        if inventory:
            # 아이템별 개수 집계
            item_counts = Counter(inventory)
            items_list = "\n".join([f"• {item} x{count}" for item, count in item_counts.items()])
            embed.add_field(name="보유 아이템", value=items_list, inline=False)
        else:
            embed.add_field(name="보유 아이템", value="*보유한 아이템이 없어, 쿠뽀.*", inline=False)

        embed.set_footer(text="Tip: /사용 명령어로 아이템을 사용할 수 있어, 쿠뽀!")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="사용", description="인벤토리의 아이템을 사용합니다.")
    async def 사용(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        user_id = str(interaction.user.id)
        char_data = self.sheet_handler.get_char_data(user_id)

        if char_data is None:
            await interaction.followup.send("캐릭터가 등록되어 있지 않아, 쿠뽀!", ephemeral=True)
            return

        # 인벤토리 파싱
        inventory_str = char_data.get('shop_inventory', '[]')
        try:
            inventory = json.loads(inventory_str) if inventory_str else []
        except json.JSONDecodeError:
            inventory = []

        if not inventory:
            await interaction.followup.send("사용할 수 있는 아이템이 없어, 쿠뽀!", ephemeral=True)
            return

        # 고유 아이템 목록 (중복 제거)
        unique_items = list(set(inventory))

        # 드롭다운 View 표시
        view = ItemSelectView(items=unique_items)
        await interaction.followup.send("사용할 아이템을 선택해, 쿠뽀:", view=view, ephemeral=True)
        await view.wait()

        if not view.selected_item:
            return  # 타임아웃 또는 선택 안 함

        selected_item = view.selected_item

        # 인벤토리에서 1개 제거 (최신 데이터 다시 읽기)
        char_data = self.sheet_handler.get_char_data(user_id)
        inventory_str = char_data.get('shop_inventory', '[]')
        try:
            inventory = json.loads(inventory_str) if inventory_str else []
        except json.JSONDecodeError:
            inventory = []

        if selected_item not in inventory:
            await interaction.channel.send(f"{interaction.user.mention} 해당 아이템이 인벤토리에 없어, 쿠뽀!")
            return

        inventory.remove(selected_item)
        new_inventory_str = json.dumps(inventory, ensure_ascii=False)

        # 캐시 업데이트
        self.sheet_handler.characters_sheet_cache.at[user_id, 'shop_inventory'] = new_inventory_str

        # 시트에 즉시 동기화
        await self._sync_characters_to_sheet()

        # 한글 조사 처리 (이/가)
        particle = get_korean_particle(selected_item, '이', '가')

        embed = discord.Embed(
            title="아이템 사용",
            description=f"**{selected_item}**{particle} 사용되었습니다.",
            color=discord.Color.green()
        )
        embed.set_footer(text=f"인벤토리에서 {selected_item}{particle} 사라졌습니다.")

        # 채널에 공개 메시지
        await interaction.channel.send(embed=embed)

    @app_commands.command(name="매점도움말", description="매점 시스템 사용법을 안내합니다.")
    async def 매점도움말(self, interaction: discord.Interaction):
        msg = (
            "**매점 시스템 도움말**\n\n"
            "**[일반 매점]** (기본)\n"
            "- `/매점` : 현재 매점 상품을 확인합니다.\n"
            "- `/구매 [상품번호]` : 상품을 1회만 구매할 수 있습니다. (선착순, 품절 있음)\n"
            "- 포인트가 필요하지 않습니다.\n\n"
            "**[특수 매점]**\n"
            "- `/매점 종류:특수` : 특수 매점 상품을 확인합니다.\n"
            "- `/구매 [상품번호] 종류:특수` : 포인트를 사용하여 구매합니다. (제한 없음)\n"
            "  - 0번 '티켓'은 상시 구매 가능합니다.\n"
            "- `/포인트 @유저 금액` : 다른 유저에게 포인트를 지급합니다.\n"
            "- `/인벤토리` : 구매한 아이템 목록과 보유 포인트를 확인합니다.\n"
            "- `/사용` : 인벤토리의 아이템을 사용합니다.\n\n"
            "**[관리자용]**\n"
            "- `/매점초기화` : 상품 5개를 새로 뽑고 모든 구매 기록을 초기화합니다.\n"
        )
        await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(name="매점숨김갱신", description="[관리자] '안나와용'으로 표시된 상품을 매점에서 숨깁니다.")
    @app_commands.default_permissions(administrator=True)
    async def 매점숨김갱신(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        def update_visibility():
            all_items = self.sheet_handler.shop_sheet.get_all_records()
            data = []
            for i, item in enumerate(all_items):
                visibility = item.get("매점에 상품 안나오게 하는 버튼", "").strip()
                if visibility == "안나와용":
                    row_idx = i + 2
                    # '이번주상품' 플래그를 0으로 설정
                    data.append({'range': f'C{row_idx}', 'values': [[0]]})
            if data:
                self.sheet_handler.shop_sheet.batch_update(data)
            return len(data)

        updated_count = await asyncio.to_thread(update_visibility)
        await self.update_selected_items()
        await interaction.followup.send(f"총 {updated_count}개의 상품을 매점에서 숨겼어, 쿠뽀!")

async def setup(bot):
    await bot.add_cog(ShopCog(bot))