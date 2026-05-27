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
import numpy as np
import json
# import random
from combat import random_utils
import constants

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SheetsHandler:
    def __init__(self, sheet_name):
        SCOPE = [
                "https://spreadsheets.google.com/feeds",
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive.file",
                "https://www.googleapis.com/auth/drive"
        ]
        creds_path = "dogwood-method-448216-f4-4023cd31106c.json" 
        if not os.path.exists(creds_path):
            raise FileNotFoundError(f"'{creds_path}' 인증 파일을 찾을 수 없습니다. 프로젝트 루트에 있는지 확인해주세요.")

        creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scopes=SCOPE) # type: ignore
        gc = gspread.authorize(creds) # type: ignore
        self.spreadsheet = gc.open(sheet_name)

        self.characters_sheet = self.spreadsheet.worksheet("Characters")
        self.monsters_sheet = self.spreadsheet.worksheet("Monsters")
        self.battle_status_sheet = self.spreadsheet.worksheet("Combat_Status")
        self.materia_list_sheet = self.spreadsheet.worksheet("Materia_List")
        self.boss_skills_sheet = self.spreadsheet.worksheet("Boss_Skills")
        self.ost_sheet = self.spreadsheet.worksheet("Musics")

        # 캐싱하지 않는 시트들 (사용 빈도 낮음, 즉시 반영 필요)
        self.shop_sheet = self.spreadsheet.worksheet("ShopData")
        self.user_purchases_sheet = self.spreadsheet.worksheet("UserPurchases")
        self.book_log_sheet = self.spreadsheet.worksheet("BookLog")
        self.fishing_log_sheet = self.spreadsheet.worksheet("FishingLog")
        self.forbidden_log_sheet = self.spreadsheet.worksheet("ForbiddenLog")
        self.server_channel_sheet = self.spreadsheet.worksheet("ServerChannel")
        self.dancing_log_sheet = self.spreadsheet.worksheet("DancingLog")

        # Boss_Answer 시트 (옵션널 - 없으면 None)
        try:
            self.boss_answer_sheet = self.spreadsheet.worksheet("Boss_Answer")
        except gspread.exceptions.WorksheetNotFound:
            self.boss_answer_sheet = None
            logger.warning("Boss_Answer 시트가 없습니다. 보스 대화 키워드 기능이 비활성화됩니다.")  

        print("캐싱 시작")

        self.characters_sheet_cache = self._sheet_to_dataframe(self.characters_sheet, index_col='discord_id')
        self.monsters_sheet_cache = self._sheet_to_dataframe(self.monsters_sheet, index_col = 'monster_id')
        self.battle_stat_sheet_cache = self._sheet_to_dataframe(self.battle_status_sheet, index_col = 'id')
        self.materia_list_sheet_cache = self._sheet_to_dataframe(self.materia_list_sheet, index_col = 'materia_name')
        
        # Boss_Skills는 MultiIndex로 별도 처리
        boss_skills_data = self.boss_skills_sheet.get_all_records()
        if boss_skills_data:
            boss_skills_df = pd.DataFrame(boss_skills_data)
            # boss_id와 skill_id를 문자열로 변환
            boss_skills_df['boss_id'] = boss_skills_df['boss_id'].astype(str)
            boss_skills_df['used_flag'] = boss_skills_df['used_flag'].replace('', 0).fillna(0).astype(int)
            boss_skills_df.set_index(['boss_id', 'skill_id'], inplace=True)
            # MultiIndex 정렬 (성능 및 .loc[] 접근을 위해 필수)
            # sort_index()는 정렬된 복사본을 반환하므로 재할당 필요
            self.boss_skills_sheet_cache = boss_skills_df.sort_index()
        else:
            self.boss_skills_sheet_cache = pd.DataFrame()
        
        # self.turn_order_sheet_cache = self._sheet_to_dataframe(self.turn_order_sheet, index_col = 'id')
        self.ost_sheet_cache = self._sheet_to_dataframe(self.ost_sheet, index_col='music_no')

        # Boss_Answer 시트 캐싱
        if self.boss_answer_sheet:
            self.boss_answer_sheet_cache = self._sheet_to_dataframe(self.boss_answer_sheet, index_col='keyword')
        else:
            self.boss_answer_sheet_cache = pd.DataFrame()

        print("캐싱 완료")

        self.characters_sheet_headers = self.characters_sheet.row_values(1)
        self.monsters_headers = self.monsters_sheet.row_values(1)
        self.battle_stat_headers = [
            'id', 'name', 'job', 'max_hp', 'current_hp', 'max_mp', 'current_mp',
            'physics', 'magic', 'agility', 'charm', 'materia_owned', 'monster_type',
            'is_dead', 'defend_flag', 'evasion_flag', 'limit_flag', 'status'
        ]
        
        # Boss_Skills 헤더
        if not self.boss_skills_sheet_cache.empty:
            self.boss_skills_headers = self.boss_skills_sheet.row_values(1)
        else:
            self.boss_skills_headers = []


    def _sheet_to_dataframe(self, worksheet: gspread.Worksheet, index_col: str) -> pd.DataFrame:
        """지정된 시트를 판다 프레임으로 변환하고 인덱스 설정"""
        data = worksheet.get_all_records()
        df = pd.DataFrame(data)
        # 숫자 형태의 ID를 문자열로 변환
        if index_col in df.columns:
            df[index_col] = df[index_col].astype(str)
            df.set_index(index_col, inplace=True)
            # 인덱스 이름 명시적으로 설정 (reset_index()에서 컬럼 이름으로 사용됨)
            df.index.name = index_col

        # 안전장치: 인덱스로 설정된 컬럼이 여전히 컬럼에 남아있다면 제거
        if index_col in df.columns:
            df = df.drop(columns=[index_col])
            logger.warning(f"중복된 인덱스 컬럼 '{index_col}'를 제거했습니다.")

        return df 
    
    def add_new_character(self, char_data_list: List[Any]) -> bool:
        """구글 시트에 새로운 캐릭터 등록 및 characters 구글 시트 -> 캐시 동기화"""
        try:
            sheet_list = list(char_data_list)
            sheet_list[0] = str(char_data_list[0])
            self.characters_sheet.append_row(sheet_list, value_input_option='USER_ENTERED') # type: ignore

            # discord_id를 추출 (첫 번째 값)
            discord_id = str(char_data_list[0])

            # discord_id를 제외한 나머지 컬럼과 데이터로 Series 생성
            # characters_sheet_headers[1:]는 discord_id를 제외한 컬럼들
            # char_data_list[1:]는 discord_id를 제외한 데이터들
            new_row_series = pd.Series(data=char_data_list[1:], index=self.characters_sheet_headers[1:])

            # Series의 name을 인덱스로 사용할 ID로 설정
            new_row_series.name = discord_id

            # Series를 DataFrame으로 변환하여 기존 캐시에 추가
            new_row_df = new_row_series.to_frame().T

            # 새로 추가된 DataFrame의 인덱스 이름을 기존 캐시와 맞춤
            new_row_df.index.name = self.characters_sheet_cache.index.name

            self.characters_sheet_cache = pd.concat([self.characters_sheet_cache, new_row_df])

            return True
        except Exception as e:
            logger.error(f"캐릭터 추가 중 오류 발생: {e}")
            return False

    def get_char_data(self, char_id: str):
        """캐시에서 id로 캐릭터 정보(행) 조회"""
        char_id = str(char_id)
        try: 
            result = self.characters_sheet_cache.loc[char_id]
            if isinstance(result, pd.DataFrame):
                # If multiple rows are returned, select the first one
                return result.iloc[0]
            return result
        except KeyError:
            return None
        
    def get_monster_data(self, mon_id: str):
        """캐시에서 id로 캐릭터 정보(행) 조회"""
        mon_id = str(mon_id)
        try: 
            result = self.monsters_sheet_cache.loc[mon_id]
            if isinstance(result, pd.DataFrame):
                # If multiple rows are returned, select the first one
                return result.iloc[0]
            return result
        except KeyError:
            return None
        
    def update_char_stat(self, user_id: str, stat: str, amount):
        """캐릭터의 특정 스탯을 업데이트합니다. (문자열 값도 지원)"""
        try:
            if user_id not in self.characters_sheet_cache.index:
                logger.warning(f"ID '{user_id}'가 캐시에 없습니다.")
                return False
            
            # 문자열 값인 경우 (materia_inventory, materia_owned 등)
            if isinstance(amount, str):
                self.characters_sheet_cache.at[user_id, stat] = amount
            else:
                # 기존 숫자 값 처리
                current_value = int(self.characters_sheet_cache.at[user_id, stat])
                new_value = current_value + amount
                self.characters_sheet_cache.at[user_id, stat] = new_value
            
            logger.info(f"'{user_id}'의 '{stat}' 업데이트 완료")
            return True
        except Exception as e:
            logger.error(f"스탯 업데이트 실패: {e}")
            return False
        
    def prepare_battle(self, participant_ids: list):
        """전투 시작을 위해 참여자 데이터를 새로운 Combat_Status 구조에 맞게 가공하여 전투 캐시를 생성합니다."""
        try:
            # 중복 ID 제거 (순서 유지)
            seen = set()
            unique_ids = []
            for p_id in participant_ids:
                p_id_str = str(p_id).strip()
                if p_id_str not in seen:
                    seen.add(p_id_str)
                    unique_ids.append(p_id_str)
            
            if len(unique_ids) < len(participant_ids):
                logger.warning(f"중복 ID가 제거되었습니다. 원본: {len(participant_ids)}개 → 고유: {len(unique_ids)}개")
            
            all_participants_data = []
            for p_id in unique_ids:
                participant_dict = {}
                
                # 플레이어 데이터 처리
                char_data = self.get_char_data(p_id)
                if char_data is not None:
                    participant_dict = {
                        'id': p_id,
                        'name': char_data.get('character_name', '이름없음'),
                        'job': char_data.get('job', '백수'),
                        'max_hp': int(char_data.get('max_hp', 0)),
                        'current_hp': int(char_data.get('max_hp', 0)),
                        'max_mp': int(char_data.get('max_mp', 0)),
                        'current_mp': int(char_data.get('max_mp', 0)),
                        'physics': int(char_data.get('physics', 0)),
                        'magic': int(char_data.get('magic', 0)),
                        'agility': int(char_data.get('agility', 0)),
                        'charm': int(char_data.get('charm', 0)),
                        'materia_owned': char_data.get('materia_owned', '없음'),
                        'monster_type': 'player'
                    }
                else:
                    # 몬스터 데이터 처리
                    mon_data = self.get_monster_data(p_id)
                    if mon_data is not None:
                        magic_enabled = int(mon_data.get('magic_enabled_flag', 0)) == 1
                        participant_dict = {
                            'id': p_id,
                            'name': mon_data.get('monster_name', '이름없는 몬스터'),
                            'job': None,
                            'max_hp': int(mon_data.get('max_hp', 0)),
                            'current_hp': int(mon_data.get('max_hp', 0)),
                            'max_mp': constants.MONSTER_MAX_MP, 
                            'current_mp': constants.MONSTER_MAX_MP,
                            'physics': int(mon_data.get('physics', 0)),
                            'magic': int(mon_data.get('magic', 0)),
                            'agility': int(mon_data.get('agility', 0)),
                            'charm': int(mon_data.get('charm', 0)),
                            'materia_owned': mon_data.get('materia_owned', '없음') if magic_enabled else '없음',
                            'monster_type': mon_data.get('type', 'normal') # 'normal' 또는 'boss'
                        }
                
                if participant_dict:
                    # 공통 전투 플래그 초기화
                    participant_dict.update({
                        'is_dead': 0,
                        'defend_flag': 0,
                        'evasion_flag': 0,
                        'limit_flag': 0, # 1이 사용 불가
                        'status': '정상'
                    })
                    all_participants_data.append(participant_dict)

            if not all_participants_data:
                logger.error("전투 참여자 데이터를 찾을 수 없습니다.")
                return False

            # 표준화된 딕셔너리 리스트로부터 데이터프레임 생성
            battle_df = pd.DataFrame(all_participants_data)
            battle_df.set_index('id', inplace=True)
            
            # 최종 전투 캐시를 self.battle_stat_sheet_cache에 할당
            # 정의된 헤더 순서대로 컬럼을 재정렬
            self.battle_stat_sheet_cache = battle_df.reindex(columns=self.battle_stat_headers[1:]) # id는 인덱스이므로 제외

            logger.info("전투 준비 및 캐시 초기화 완료.")
            return True
        except Exception as e:
            logger.error(f"전투 준비 중 오류 발생: {e}", exc_info=True)
            return False
        
    def update_battle_status_cache_to_sheet(self):
        """현재 전투 캐시(battle_stat_sheet_cache)의 내용을 Combat_Status 시트에 덮어씀"""
        if self.battle_stat_sheet_cache.empty:
            logger.warning("업데이트할 전투 캐시가 비어있습니다.")
            return

        try:
            # NaN 값을 None으로 변환 (JSON 호환)
            df_to_write = self.battle_stat_sheet_cache.reset_index() # 인덱스를 다시 컬럼으로
            df_to_write = df_to_write.replace({np.nan: None})

            # 헤더를 포함한 전체 데이터를 리스트의 리스트 형태로 변환
            data_to_write = [df_to_write.columns.values.tolist()] + df_to_write.values.tolist()
            
            # 시트 전체를 업데이트
            self.battle_status_sheet.clear()
            self.battle_status_sheet.update(data_to_write, raw=False)
            logger.info("전투 상태 캐시를 구글 시트에 성공적으로 업데이트했습니다.")
        except Exception as e:
            logger.error(f"전투 캐시를 시트에 업데이트하는 중 오류 발생: {e}")
            raise

    def update_all_force_google_to_cache(self):
        """구글 시트의 데이터를 다시 읽어와 메모리의 캐시를 강제로 덮어 씌움"""
        try: 
            print("update_all_force_google_to_cache 작동 중")
            self.characters_sheet_cache = self._sheet_to_dataframe(self.characters_sheet, index_col='discord_id')
            self.monsters_sheet_cache = self._sheet_to_dataframe(self.monsters_sheet, index_col = 'monster_id')
            self.battle_stat_sheet_cache = self._sheet_to_dataframe(self.battle_status_sheet, index_col = 'id')
            self.materia_list_sheet_cache = self._sheet_to_dataframe(self.materia_list_sheet, index_col = 'materia_name')
            
            # Boss_Skills MultiIndex 캐싱
            boss_skills_data = self.boss_skills_sheet.get_all_records()
            if boss_skills_data:
                boss_skills_df = pd.DataFrame(boss_skills_data)
                boss_skills_df['boss_id'] = boss_skills_df['boss_id'].astype(str)
                boss_skills_df['skill_id'] = boss_skills_df['skill_id'].astype(str)
                boss_skills_df.set_index(['boss_id', 'skill_id'], inplace=True)
                self.boss_skills_sheet_cache = boss_skills_df
            else:
                self.boss_skills_sheet_cache = pd.DataFrame()
            
            # self.turn_order_sheet_cache = self._sheet_to_dataframe(self.turn_order_sheet, index_col = 'id')
            self.ost_sheet_cache = self._sheet_to_dataframe(self.ost_sheet, index_col='music_no')

            # Boss_Answer 시트 캐싱
            if self.boss_answer_sheet:
                self.boss_answer_sheet_cache = self._sheet_to_dataframe(self.boss_answer_sheet, index_col='keyword')
            else:
                self.boss_answer_sheet_cache = pd.DataFrame()

            # self.battle_stat_headers = self.spreadsheet.worksheet("Combat_Status").row_values(1)

            print("✅ 캐시가 성공적으로 동기화되었습니다.")
            return True
        except Exception as e:
            print(f"❌ 캐시 동기화 중 오류 발생: {e}")
            return False

    def update_str_cache_to_google(self):
        """플레이어의 physics 스탯 데이터를 시트의 F열에 모두 업데이트함"""
        # 헤더 포함 행 개수
        gs_row_count = self.characters_sheet.row_count
        try:
            print("physics 캐시 -> 구글 시트 동기화 시작")
            ph_data = self.characters_sheet_cache['physics']
            updata_values = [[value] for value in ph_data]
            if gs_row_count - 1 <= 0 :
                print(f"update_str_cache_to_google 취소됨: 구글 시트에 헤더가 없거나 헤더만 있음")
                return
            update_range = f'F2:f{gs_row_count}'
            self.characters_sheet.update(range_name=update_range, values=updata_values)
            print(f"physics 캐시 -> 구글 동기화 성공")
        except Exception as e:
            print(f"update_str_cache_to_google에서 오류 발생: {e}")

    def add_participant_to_battle(self, p_id: str) -> Optional[pd.Series]:
        """기존 전투 캐시에 새로운 참여자를 추가하고 초기화함."""
        p_id = str(p_id)
        participant_dict = {}

        # 플레이어 데이터 처리
        char_data = self.get_char_data(p_id)
        if char_data is not None:
            participant_dict = {
                'id': p_id, 'name': char_data.get('character_name', '이름없음'), 'job': char_data.get('job', '백수'),
                'max_hp': int(char_data.get('max_hp', constants.BASE_HP)), 'current_hp': int(char_data.get('max_hp', constants.BASE_HP)),
                'max_mp': int(char_data.get('max_mp', constants.BASE_MP)), 'current_mp': int(char_data.get('max_mp', constants.BASE_MP)),
                'physics': int(char_data.get('physics', 10)), 'magic': int(char_data.get('magic', 10)),
                'agility': int(char_data.get('agility', 10)), 'charm': int(char_data.get('charm', 10)),
                'materia_owned': char_data.get('materia_owned', '없음'), 'monster_type': 'player'
            }
        else:
            # 몬스터 데이터 처리
            mon_data = self.get_monster_data(p_id)
            if mon_data is not None:
                magic_enabled = int(mon_data.get('magic_enabled_flag', 0)) == 1
                participant_dict = {
                    'id': p_id, 'name': mon_data.get('monster_name', '이름없는 몬스터'), 'job': None,
                    'max_hp': int(mon_data.get('max_hp', 0)), 'current_hp': int(mon_data.get('max_hp', 0)),
                    'max_mp': 1000000, 'current_mp': 1000000,
                    'physics': int(mon_data.get('physics', 0)), 'magic': int(mon_data.get('magic', 0)),
                    'agility': int(mon_data.get('agility', 0)), 'charm': int(mon_data.get('charm', 0)),
                    'materia_owned': mon_data.get('materia_owned', '없음') if magic_enabled else '없음',
                    'monster_type': mon_data.get('type', 'normal')
                }

        if not participant_dict:
            logger.warning(f"ID '{p_id}'에 해당하는 캐릭터나 몬스터를 찾을 수 없습니다.")
            return None

        # 공통 전투 플래그 초기화
        participant_dict.update({
            'is_dead': 0, 'evasion_flag': 0, 'defend_flag': 0, 'limit_flag': 0, 'status': '정상', 'is_dead': 0
        })

        # Series로 변환하여 캐시에 추가
        new_participant = pd.Series(participant_dict)
        new_participant.name = p_id # Series의 이름을 id로 설정
        
        # 기존 캐시에 새 행을 추가 (DataFrame으로 변환 후 concat)
        new_row_df = new_participant.to_frame().T.set_index('id')
        self.battle_stat_sheet_cache = pd.concat([self.battle_stat_sheet_cache, new_row_df])
        
        # 누락된 열을 채우고 정렬 (새로운 참여자로 인해 NaN이 생길 수 있으므로)
        self.battle_stat_sheet_cache = self.battle_stat_sheet_cache.reindex(columns=self.battle_stat_headers[1:])

        logger.info(f"'{p_id}'가 전투에 난입했습니다. 캐시가 업데이트되었습니다.")
        return new_participant
    
    def yt_url_extract(self, music_no: int):
        """Musics 시트의 캐시된 데이터에서 url 가지고 옴"""
        try:
            # music_no를 문자열로 변환하여 캐시에서 해당 행을 찾습니다.
            music_data = self.ost_sheet_cache.loc[str(music_no)]
            # 'combat_ost' 열에 있는 URL 문자열을 반환합니다.
            return music_data['combat_ost']
        except (KeyError, TypeError):
            logger.warning(f"Music sheet에서 music_no '{music_no}'에 해당하는 URL을 찾을 수 없습니다.")
            return None
        
    def get_monster_reaction(self, monster_id: str) -> Optional[str]:
        """Monsters 시트의 캐시된 데이터에서 특정 몬스터의 대사 추출"""
        if monster_id in self.monsters_sheet_cache.index:
            reactions_str = self.monsters_sheet_cache.loc[monster_id, 'damage_reaction_message']
            try:
                reactions_list = json.loads(reactions_str)
                if reactions_list and isinstance(reactions_list, list):
                    return random_utils.choice(reactions_list)
            except json.JSONDecodeError:
                logger.warning(f"몬스터 '{monster_id}'의 대사 JSON 파싱 실패")
        return None
    
    def get_boss_keyword_counts(self) -> Dict[str, int]:
        """Boss_Answer 시트에서 키워드별 카운트를 딕셔너리로 반환

        Returns:
            Dict[str, int]: {keyword: count} 형태의 딕셔너리
        """
        if self.boss_answer_sheet_cache.empty:
            return {}

        keyword_counts = {}
        for keyword in self.boss_answer_sheet_cache.index:
            count = int(self.boss_answer_sheet_cache.loc[keyword, 'count'])
            keyword_counts[keyword] = count

        return keyword_counts

    def get_boss_skills(self, boss_id: str) -> pd.DataFrame:
        """특정 보스의 모든 스킬 정보를 반환"""
        if self.boss_skills_sheet_cache.empty:
            return pd.DataFrame()

        try:
            # 강제 문자열 변환 (MultiIndex KeyError 방지)
            boss_id = str(boss_id)

            # MultiIndex로 boss_id에 해당하는 모든 스킬 조회
            if boss_id in self.boss_skills_sheet_cache.index.get_level_values(0):
                boss_skills = self.boss_skills_sheet_cache.loc[boss_id]
                
                # Series인 경우 (스킬 1개) DataFrame으로 변환
                if isinstance(boss_skills, pd.Series):
                    boss_skills = pd.DataFrame([boss_skills])
                
                # trigger_hp_percent 내림차순 정렬 (높은 HP부터)
                return boss_skills.sort_values('trigger_hp_percent', ascending=False)
            else:
                return pd.DataFrame()
        except Exception as e:
            logger.error(f"보스 스킬 조회 실패 ({boss_id}): {e}")
            return pd.DataFrame()

    def update_boss_skill_used_flag(self, boss_id: str, skill_id: str):
        """보스 스킬의 used_flag를 1로 업데이트 (캐시 + 시트)"""
        try:
            # 강제 문자열 변환
            boss_id = str(boss_id)
            skill_id = str(skill_id)

            # MultiIndex 정렬 보장 (PerformanceWarning 방지)
            if not self.boss_skills_sheet_cache.index.is_monotonic_increasing:
                self.boss_skills_sheet_cache = self.boss_skills_sheet_cache.sort_index()

            # 캐시 업데이트 - MultiIndex용 .loc 사용
            if (boss_id, skill_id) in self.boss_skills_sheet_cache.index:
                self.boss_skills_sheet_cache.loc[(boss_id, skill_id), 'used_flag'] = 1
                logger.info(f"보스 스킬 캐시 업데이트: {boss_id} - {skill_id}")
            else:
                logger.warning(f"캐시에서 보스 스킬을 찾을 수 없음: {boss_id} - {skill_id}")
                return False
            
            # 시트 업데이트
            if not self.boss_skills_sheet:
                logger.warning("Boss_Skills 시트가 없습니다.")
                return False
            
            all_records = self.boss_skills_sheet.get_all_records()
            for idx, record in enumerate(all_records, start=2):  # 헤더 제외 (row 2부터)
                if record['boss_id'] == boss_id and record['skill_id'] == skill_id:
                    # used_flag는 마지막 컬럼 (11번째: K열)
                    self.boss_skills_sheet.update_cell(idx, 11, 1)
                    logger.info(f"보스 스킬 시트 업데이트 완료: {boss_id} - {skill_id}")
                    return True
            
            logger.warning(f"시트에서 보스 스킬을 찾을 수 없음: {boss_id} - {skill_id}")
            return False
            
        except Exception as e:
            logger.error(f"보스 스킬 used_flag 업데이트 실패: {e}", exc_info=True)
            return False

    def reset_boss_skills(self, boss_id: str):
        """특정 보스의 모든 스킬 used_flag를 0으로 초기화 (전투 종료 시 사용)"""
        try:
            # 강제 문자열 변환
            boss_id = str(boss_id)

            # MultiIndex 정렬 보장 (PerformanceWarning 방지)
            if not self.boss_skills_sheet_cache.index.is_monotonic_increasing:
                self.boss_skills_sheet_cache = self.boss_skills_sheet_cache.sort_index()

            # 캐시 초기화
            if boss_id in self.boss_skills_sheet_cache.index.get_level_values(0):
                # boss_id에 해당하는 모든 스킬의 used_flag를 0으로
                for skill_id in self.boss_skills_sheet_cache.loc[boss_id].index:
                    self.boss_skills_sheet_cache.loc[(boss_id, skill_id), 'used_flag'] = 0
                logger.info(f"보스 '{boss_id}' 스킬 캐시 초기화 완료")
            
            # 시트 초기화
            if not self.boss_skills_sheet:
                return False
            
            all_records = self.boss_skills_sheet.get_all_records()
            updates = []
            for idx, record in enumerate(all_records, start=2):
                if record['boss_id'] == boss_id:
                    updates.append({
                        'range': f'K{idx}',  # K열: used_flag
                        'values': [[0]]
                    })
            
            if updates:
                # batch_update는 gspread의 메서드
                for update in updates:
                    row = int(update['range'][1:])
                    self.boss_skills_sheet.update_cell(row, 11, 0)
                logger.info(f"보스 '{boss_id}'의 모든 스킬 플래그 시트 초기화 완료")
            
            return True
        except Exception as e:
            logger.error(f"보스 스킬 초기화 실패: {e}", exc_info=True)
            return False

    def delete_character(self, discord_id: str) -> bool:
        """캐릭터 정보를 시트와 캐시에서 완전히 삭제

        Args:
            discord_id: 삭제할 캐릭터의 Discord ID

        Returns:
            bool: 삭제 성공 여부
        """
        try:
            discord_id = str(discord_id)

            # 1. 캐시에서 삭제
            if discord_id not in self.characters_sheet_cache.index:
                logger.warning(f"캐시에서 ID '{discord_id}'를 찾을 수 없습니다.")
                return False

            self.characters_sheet_cache = self.characters_sheet_cache.drop(discord_id)
            logger.info(f"캐시에서 ID '{discord_id}' 삭제 완료")

            # 2. Google 시트에서 삭제
            all_records = self.characters_sheet.get_all_values()

            # 헤더 행 제외하고 해당 ID의 행 번호 찾기
            row_to_delete = None
            for idx, row in enumerate(all_records[1:], start=2):  # 2번째 행부터 시작 (1은 헤더)
                if str(row[0]) == discord_id:  # 첫 번째 열이 discord_id
                    row_to_delete = idx
                    break

            if row_to_delete:
                self.characters_sheet.delete_rows(row_to_delete)
                logger.info(f"Google 시트에서 ID '{discord_id}' (행 {row_to_delete}) 삭제 완료")
                return True
            else:
                logger.warning(f"Google 시트에서 ID '{discord_id}'를 찾을 수 없습니다.")
                return False

        except Exception as e:
            logger.error(f"캐릭터 삭제 중 오류 발생: {e}", exc_info=True)
            return False
        
    def add_new_row(self, sheet_name: str, content: list):
        """시트에 새로운 행을 추가하는 명령어."""
        try:
            if sheet_name == "fishing":
                self.fishing_log_sheet.append_rows(content)
            elif sheet_name == "book":
                self.book_log_sheet.append_rows(content)
            elif sheet_name == "forbidden": 
                self.forbidden_log_sheet.append_rows(content)
            return True
        except Exception as e:
            logger.error(f"add_new_row 오류 발생: {e}")
            return False
            




