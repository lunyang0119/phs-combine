import discord
from typing import List, Optional, Tuple
import constants

class TurnStartView(discord.ui.View):
    """플레이어의 턴 시작을 알리고 행동 View를 호출하는 버튼을 포함한 View"""
    def __init__(self, *, timeout: Optional[float] = None):
        super().__init__(timeout=timeout or constants.ACTION_VIEW_TIMEOUT)
        self.interaction: Optional[discord.Interaction] = None

    @discord.ui.button(label="▶ Start Combat", style=discord.ButtonStyle.success, custom_id="start_turn_action")
    async def start_turn_action(self, interaction: discord.Interaction, button: discord.ui.Button):
        # 버튼을 누른 사용자의 interaction을 저장하고 View를 중지
        self.interaction = interaction
        self.stop()


class TargetSelect(discord.ui.Select):
    """행동에 따라 아군(힐) 또는 적군(공격)을 선택하는 드롭다운 메뉴"""
    def __init__(self, targets: List[Tuple[str, str]]):
        # targets: [(name, id), (name, id), ...] 형태의 리스트
        options = [
            discord.SelectOption(label=name, value=id) for name, id in targets
        ]
        super().__init__(placeholder="대상을 선택해주세요...", options=options)

    async def callback(self, interaction: discord.Interaction):
        # 선택한 대상의 ID를 부모 View(TargetSelectView)에 저장
        if self.view:
            self.view.target_id = self.values[0]
            # 메시지를 수정하여 선택 완료를 알리고, View를 멈춤
            await interaction.response.edit_message(content=f"**{self.options[self.find_option_by_value(self.values[0])].label}** (을)를 선택했습니다.", view=None)
            self.view.stop()

    def find_option_by_value(self, value: str) -> int:
        """값으로 옵션 인덱스 찾기"""
        for i, option in enumerate(self.options):
            if option.value == value:
                return i
        return -1

class TargetSelectView(discord.ui.View):
    """TargetSelect를 포함하는 View"""
    def __init__(self, *, timeout: Optional[float] = None, targets: List[Tuple[str, str]]):
        super().__init__(timeout=timeout or constants.TARGET_SELECT_TIMEOUT)
        self.target_id: Optional[str] = None
        self.back_to_action: bool = False  # 뒤로가기 플래그
        self.add_item(TargetSelect(targets))

    @discord.ui.button(label="◀ 뒤로가기", style=discord.ButtonStyle.secondary, custom_id="back_to_action", row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """행동 선택 화면으로 돌아가기"""
        self.back_to_action = True
        self.target_id = None
        await interaction.response.edit_message(content="행동 선택으로 돌아갑니다...", view=None)
        self.stop()


class ActionView(discord.ui.View):
    def __init__(self, *, timeout: Optional[float] = None, materia_owned: bool = False, limit_flag: bool = False):
        super().__init__(timeout=timeout or constants.ACTION_VIEW_TIMEOUT)
        self.action: Optional[str] = None
        self.target_id: Optional[str] = None

        # 버튼 상태를 나중에 업데이트하기 위해 저장
        self._materia_owned = materia_owned
        self._limit_flag = limit_flag

    def _update_button_states(self):
        """버튼 상태 업데이트 (View 생성 후 호출)"""
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                if item.custom_id == "magic":
                    item.disabled = not self._materia_owned
                elif item.custom_id == "limit_break":
                    item.disabled = not self._limit_flag 

    @discord.ui.button(label="물리공격", style=discord.ButtonStyle.primary, custom_id="physic_atk")
    async def physic_atk(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.action = 'physic_atk'
        await interaction.response.edit_message(content="물리 공격을 선택했습니다. 대상을 지정해주세요.", view=None)
        self.stop()
    @discord.ui.button(label="마법", style=discord.ButtonStyle.primary, custom_id="magic")
    async def magic(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.action = 'magic'
        await interaction.response.edit_message(view=None)
        self.stop()
    @discord.ui.button(label="리미트 브레이크", style=discord.ButtonStyle.primary, custom_id="limit_break")
    async def limit_break(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.action = 'limit_break'
        await interaction.response.edit_message(content="리미트 브레이크를 선택했습니다.", view=None)
        self.stop()
    @discord.ui.button(label="방어", style=discord.ButtonStyle.primary, custom_id="char_defend")
    async def char_defend(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.action = 'char_defend'
        await interaction.response.edit_message(content="방어를 선택했습니다.", view=None)
        self.stop()
    @discord.ui.button(label="회피", style=discord.ButtonStyle.primary, custom_id="char_evasion")
    async def char_evasion(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.action = 'char_evasion'
        await interaction.response.edit_message(content="회피를 선택했습니다.", view=None)
        self.stop()


class MateriaActionView(discord.ui.View):
    """마테리아 장착/해제 선택 View"""
    def __init__(self, *, timeout: Optional[float] = None):
        super().__init__(timeout=timeout or constants.ACTION_VIEW_TIMEOUT)
        self.action: Optional[str] = None

    @discord.ui.button(label="💎 장착", style=discord.ButtonStyle.primary, custom_id="equip")
    async def equip(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.action = 'equip'
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="📤 해제", style=discord.ButtonStyle.secondary, custom_id="unequip")
    async def unequip(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.action = 'unequip'
        await interaction.response.defer()
        self.stop()


class MateriaSelect(discord.ui.Select):
    """인벤토리에서 마테리아를 선택하는 드롭다운 메뉴"""
    def __init__(self, inventory: List[str]):
        options = [
            discord.SelectOption(label=materia, value=materia) for materia in inventory
        ]
        super().__init__(placeholder="장착할 마테리아를 선택하세요...", options=options, custom_id="materia_select")

    async def callback(self, interaction: discord.Interaction):
        if self.view:
            self.view.selected_materia = self.values[0]
            await interaction.response.edit_message(content=f"**{self.values[0]}** (을)를 선택했습니다.", view=None)
            self.view.stop()


class MateriaSelectView(discord.ui.View):
    """MateriaSelect를 포함하는 View"""
    def __init__(self, *, timeout: Optional[float] = None, inventory: List[str]):
        super().__init__(timeout=timeout or constants.ACTION_VIEW_TIMEOUT)
        self.selected_materia: Optional[str] = None
        self.add_item(MateriaSelect(inventory))


class ConfirmDeleteView(discord.ui.View):
    """캐릭터 삭제 확인 View"""
    def __init__(self, *, timeout: Optional[float] = None):
        super().__init__(timeout=timeout or constants.ACTION_VIEW_TIMEOUT)
        self.confirmed: bool = False

    @discord.ui.button(label="예", style=discord.ButtonStyle.danger, custom_id="confirm_delete")
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """삭제 확인"""
        self.confirmed = True
        await interaction.response.defer()
        self.stop()

    @discord.ui.button(label="아니오", style=discord.ButtonStyle.secondary, custom_id="cancel_delete")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """삭제 취소"""
        self.confirmed = False
        await interaction.response.edit_message(content="캐릭터 삭제가 취소되었습니다.", embed=None, view=None)
        self.stop()


class ItemSelect(discord.ui.Select):
    """매점 인벤토리에서 아이템을 선택하는 드롭다운 메뉴"""
    def __init__(self, items: List[str]):
        options = [
            discord.SelectOption(label=item, value=item) for item in items[:25]  # Discord 제한: 최대 25개
        ]
        super().__init__(placeholder="사용할 아이템을 선택하세요...", options=options, custom_id="item_select")

    async def callback(self, interaction: discord.Interaction):
        if self.view:
            self.view.selected_item = self.values[0]
            await interaction.response.edit_message(content=f"**{self.values[0]}** (을)를 선택했습니다.", view=None)
            self.view.stop()


class ItemSelectView(discord.ui.View):
    """ItemSelect를 포함하는 View - 매점 아이템 사용 시 드롭다운"""
    def __init__(self, *, timeout: Optional[float] = None, items: List[str]):
        super().__init__(timeout=timeout or constants.ACTION_VIEW_TIMEOUT)
        self.selected_item: Optional[str] = None
        self.add_item(ItemSelect(items))


class ChannelSelect(discord.ui.Select):
    """ServerChannel 시트에서 채널을 선택하는 드롭다운 메뉴"""
    def __init__(self, channels: List[Tuple[str, str]]):
        # channels: [(display_name, channel_id), ...]
        options = [
            discord.SelectOption(label=name[:100], value=ch_id)
            for name, ch_id in channels[:25]  # Discord 제한: 최대 25개
        ]
        super().__init__(placeholder="채널을 선택해주세요...", options=options, custom_id="channel_select")

    async def callback(self, interaction: discord.Interaction):
        if self.view:
            self.view.selected_channel_id = self.values[0]
            selected_label = next(
                (opt.label for opt in self.options if opt.value == self.values[0]),
                "선택됨"
            )
            await interaction.response.edit_message(
                content=f"**{selected_label}** 채널을 선택했습니다.",
                view=None
            )
            self.view.stop()


class ChannelSelectView(discord.ui.View):
    """ChannelSelect를 포함하는 View - 채널 선택 드롭다운"""
    def __init__(self, *, timeout: float = 60.0, channels: List[Tuple[str, str]]):
        super().__init__(timeout=timeout)
        self.selected_channel_id: Optional[str] = None
        self.add_item(ChannelSelect(channels))