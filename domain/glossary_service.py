# c:\Users\Hyunwoo_Room\Downloads\Neo_Batch_Translator\glossary_service.py
import json
import random
import re
import time
import os
import asyncio
import copy # Added for deepcopy
from pathlib import Path
from pydantic import BaseModel, Field as PydanticField # Field 이름 충돌 방지
from typing import Dict, Any, Optional, List, Union, Tuple, Callable

try:
    from infrastructure.gemini_client import GeminiClient, GeminiContentSafetyException, GeminiRateLimitException, GeminiApiException, GeminiAllApiKeysExhaustedException
    from infrastructure.file_handler import write_json_file, ensure_dir_exists, delete_file, read_json_file
    from infrastructure.logger_config import setup_logger
    from utils.chunk_service import ChunkService
    from core.exceptions import BtgBusinessLogicException, BtgApiClientException, BtgFileHandlerException
    from core.dtos import GlossaryExtractionProgressDTO, GlossaryEntryDTO
    # genai types 임포트 추가 (TranslationService와 동일)
    from google.genai import types as genai_types
except ImportError:
    # 단독 실행 또는 다른 경로에서의 import를 위한 fallback
    from infrastructure.gemini_client import GeminiClient, GeminiContentSafetyException, GeminiRateLimitException, GeminiApiException, GeminiAllApiKeysExhaustedException # type: ignore
    from infrastructure.file_handler import write_json_file, ensure_dir_exists, delete_file, read_json_file # type: ignore
    from utils.chunk_service import ChunkService # type: ignore
    from infrastructure.logger_config import setup_logger # type: ignore
    from core.exceptions import BtgBusinessLogicException, BtgApiClientException, BtgFileHandlerException # type: ignore
    from core.dtos import GlossaryExtractionProgressDTO, GlossaryEntryDTO # type: ignore
    from google.genai import types as genai_types # type: ignore

logger = setup_logger(__name__)

class ApiGlossaryTerm(BaseModel):
    """Pydantic 모델: API로부터 직접 받을 용어집 항목의 스키마"""
    keyword: str = PydanticField(description="The original term found in the text.")
    translated_keyword: str = PydanticField(description="The translation of the keyword.")
    target_language: str = PydanticField(description="The BCP-47 language code of the translated_keyword.")
    occurrence_count: int = PydanticField(description="Estimated number of times the keyword appears in the segment.")

def _inject_slots_into_history(
    history: List[genai_types.Content], 
    replacements: Dict[str, str]
) -> tuple[List[genai_types.Content], bool]:
    """
    히스토리 내의 Content 객체들을 순회하며 슬롯({{slot}} 등)을 실제 값으로 치환합니다.
    반환값: (수정된 히스토리, 치환 발생 여부)
    """
    new_history = copy.deepcopy(history)
    replacement_occurred = False

    for content in new_history:
        if not hasattr(content, 'parts'):
            continue
            
        for part in content.parts:
            if hasattr(part, 'text') and part.text:
                original_text = part.text
                modified_text = original_text
                
                for key, value in replacements.items():
                    if key in modified_text:
                        modified_text = modified_text.replace(key, value)
                        replacement_occurred = True
                
                if original_text != modified_text:
                    part.text = modified_text
    
    return new_history, replacement_occurred

class SimpleGlossaryService:
    """
    텍스트에서 간단한 용어집 항목(원본 용어, 번역된 용어, 출발/도착 언어, 등장 횟수)을
    추출하고 관리하는 비즈니스 로직을 담당합니다. (경량화 버전)
    """
    def __init__(self, gemini_client: GeminiClient, config: Dict[str, Any]):
        """
        SimpleGlossaryService를 초기화합니다.

        Args:
            gemini_client (GeminiClient): Gemini API와 통신하기 위한 클라이언트.
            config (Dict[str, Any]): 애플리케이션 설정 (주로 파일명 접미사 등).
        """
        self.gemini_client = gemini_client
        self.config = config
        self.chunk_service = ChunkService() # ChunkService 인스턴스화
    
    def _get_glossary_extraction_prompt(self, segment_text: str, user_override_glossary_prompt: Optional[str] = None) -> str:
        """용어집 항목 추출을 위한 프롬프트를 생성합니다."""
        if user_override_glossary_prompt and user_override_glossary_prompt.strip():
            base_template = user_override_glossary_prompt
            logger.info("사용자 재정의 용어집 추출 프롬프트를 사용합니다.")
        else:
            base_template = self.config.get("simple_glossary_extraction_prompt_template") or \
                ("Analyze the following text. Identify key terms, focusing specifically on "
                 "**people (characters), proper nouns (e.g., unique items, titles, artifacts), "
                 "place names (locations, cities, countries, specific buildings), and organization names (e.g., companies, groups, factions, schools)**. "
                 "For each identified term, provide its translation into {target_lang_name} (BCP-47: {target_lang_code}), "
                 "and estimate their occurrence count in this segment.\n"
                 "The response should be a list of these term objects, conforming to the provided schema.\n"
                 "Text: ```\n{novelText}\n```\n"
                 "Ensure your response is a list of objects, where each object has 'keyword', 'translated_keyword', 'target_language', and 'occurrence_count' fields.")

        # 경량화된 서비스에서는 사용자가 번역 목표 언어를 명시적으로 제공한다고 가정
        # 또는 설정에서 가져올 수 있음. 여기서는 예시로 "ko" (한국어)를 사용.
        # 실제 구현에서는 이 부분을 동적으로 설정해야 함.
        target_lang_code = self.config.get("glossary_target_language_code", "ko")
        target_lang_name = self.config.get("glossary_target_language_name", "Korean")

        prompt = base_template.replace("{target_lang_code}", target_lang_code)
        prompt = prompt.replace("{target_lang_name}", target_lang_name)
        
        # [Strict Mode] 필수 플레이스홀더 검증
        if "{novelText}" not in prompt:
            raise BtgBusinessLogicException("용어집 추출 프롬프트에 필수 플레이스홀더 '{novelText}'가 누락되었습니다. 작업을 중단합니다.")

        prompt = prompt.replace("{novelText}", segment_text) # 수정: base_template 대신 prompt 사용
        return prompt

    def _parse_api_glossary_terms_to_dto(
        self,
        api_terms: List[ApiGlossaryTerm]
    ) -> List[GlossaryEntryDTO]: # 반환 타입 변경
        """
        API 응답 등으로 받은 원시 용어집 항목 딕셔너리 리스트를 GlossaryEntryDTO 리스트로 변환합니다.
        """
        glossary_entries: List[GlossaryEntryDTO] = [] # 변수명 변경
        if not isinstance(api_terms, list): # raw_item_list -> api_terms
            logger.warning(f"API 용어집 항목 데이터가 리스트가 아닙니다: {type(api_terms)}. 원본: {str(api_terms)[:200]}")
            return glossary_entries

        for term in api_terms:
            if isinstance(term, ApiGlossaryTerm):
                try:
                    entry = GlossaryEntryDTO(
                        keyword=term.keyword,
                        translated_keyword=term.translated_keyword,
                        target_language=term.target_language,
                        occurrence_count=term.occurrence_count
                    )
                    glossary_entries.append(entry)
                except Exception as e: # Catch potential errors during DTO creation
                    logger.warning(f"ApiGlossaryTerm을 GlossaryEntryDTO로 변환 중 오류: {term}, 오류: {e}")
                    continue
            else:
                logger.warning(f"잘못된 API 용어집 항목 형식 건너뜀: {term}")
        return glossary_entries

    def _parse_dict_list_to_dto(self, raw_item_list: List[Dict[str, Any]]) -> List[GlossaryEntryDTO]:
        glossary_entries: List[GlossaryEntryDTO] = []
        for item_dict in raw_item_list:
            try:
                # GlossaryEntryDTO expects specific fields.
                # Ensure item_dict has them or handle missing keys gracefully.
                glossary_entries.append(GlossaryEntryDTO(**item_dict))
            except TypeError as e:
                logger.warning(f"딕셔너리를 GlossaryEntryDTO로 변환 중 오류: {item_dict}, 오류: {e}")
        return glossary_entries

    # _get_conflict_resolution_prompt, _group_similar_keywords_via_api 메서드는 경량화로 인해 제거 또는 대폭 단순화.
    # 여기서는 제거하는 것으로 가정. 필요하다면 매우 단순한 형태로 재구현.

    # _extract_glossary_entries_from_segment_via_api (동기 버전) 제거됨.
    # _extract_glossary_entries_from_segment_via_api_async 사용 권장.

    def _select_sample_segments(self, all_segments: List[str]) -> List[str]:
        """전체 세그먼트 리스트에서 표본 세그먼트를 선택합니다."""
        # 샘플링 방식 설정 (uniform, random, importance-based 등)
        sampling_method = self.config.get("glossary_sampling_method", "uniform") # 설정 키 변경
        sample_ratio = self.config.get("glossary_sampling_ratio", 10.0) / 100.0 # 기본 샘플링 비율 낮춤 (경량화)
        
        if not (0 < sample_ratio <= 1.0):
            logger.warning(f"잘못된 lorebook_sampling_ratio 값: {sample_ratio*100}%. 25%로 조정합니다.")
            sample_ratio = 0.25
        
        total_segments = len(all_segments)
        if total_segments == 0:
            return []
        
        sample_size = max(1, int(total_segments * sample_ratio))
        
        if sample_size >= total_segments: 
            return all_segments
        
        if sampling_method == "random":
            selected_indices = sorted(random.sample(range(total_segments), sample_size))
        elif sampling_method == "uniform": # 균등 샘플링
            step = total_segments / sample_size
            selected_indices = sorted(list(set(int(i * step) for i in range(sample_size)))) # 중복 제거 및 정렬
            # sample_size보다 적게 선택될 수 있으므로, 부족분은 랜덤으로 채우거나 앞부분에서 채움
            if len(selected_indices) < sample_size:
                additional_needed = sample_size - len(selected_indices)
                remaining_indices = [i for i in range(total_segments) if i not in selected_indices]
                if len(remaining_indices) >= additional_needed:
                    selected_indices.extend(random.sample(remaining_indices, additional_needed))
                else: # 남은 인덱스가 부족하면 모두 추가
                    selected_indices.extend(remaining_indices)
                selected_indices = sorted(list(set(selected_indices)))

        # TODO: "importance-based" 샘플링 구현 (예: 특정 키워드 포함 세그먼트 우선)
        else: # 기본은 랜덤
            selected_indices = sorted(random.sample(range(total_segments), sample_size))
            
        return [all_segments[i] for i in selected_indices]

    def _get_lorebook_output_path(self, input_file_path: Union[str, Path]) -> Path:
        """입력 파일 경로를 기반으로 로어북 JSON 파일 경로를 생성합니다."""
        p_input = Path(input_file_path)
        base_name = p_input.stem
        output_dir = p_input.parent
        suffix = self.config.get("glossary_output_json_filename_suffix", "_glossary.json") # 설정 키 변경
        return output_dir / f"{base_name}{suffix}" # 파일명 변경

    def _save_glossary_to_json(self, glossary_entries: List[GlossaryEntryDTO], output_path: Path): # 함수명 및 DTO 변경
        """용어집 항목 리스트를 JSON 파일로 저장합니다."""
        # dataclass 객체를 dict 리스트로 변환
        data_to_save = [entry.__dict__ for entry in glossary_entries]
        try:
            write_json_file(output_path, data_to_save, indent=4) # file_handler 사용
            logger.info(f"용어집이 {output_path}에 저장되었습니다. 총 {len(glossary_entries)}개 항목.")
        except Exception as e:
            logger.error(f"용어집 JSON 파일 저장 중 오류 ({output_path}): {e}")
            raise BtgFileHandlerException(f"용어집 JSON 파일 저장 실패: {output_path}", original_exception=e) from e

    def _resolve_glossary_conflicts(self, all_extracted_entries: List[GlossaryEntryDTO]) -> List[GlossaryEntryDTO]: # 함수명 및 DTO 변경
        """
        추출된 용어집 항목들의 충돌을 해결합니다. (경량화 버전: 중복 제거 및 등장 횟수 합산)
        
        같은 원본 용어(keyword)에 대해 여러 번역이 있을 경우:
        - 리스트에서 먼저 등장한 번역(translated_keyword)을 유지
        - 등장 횟수(occurrence_count)는 모두 합산
        
        따라서 시드 용어집의 번역을 우선하려면 시드 항목을 리스트 앞에 배치해야 합니다.
        """
        if not all_extracted_entries:
            return []

        logger.info(f"용어집 충돌 해결 시작. 총 {len(all_extracted_entries)}개 항목 검토 중...")
        
        # (keyword, target_language)를 키로 사용하여 그룹화 및 등장 횟수 합산       
        # translated_keyword는 첫 번째 등장한 것을 사용 (시드 우선을 위해 시드를 먼저 넣어야 함)
        final_entries_map: Dict[Tuple[str, str], GlossaryEntryDTO] = {} # 키에서 source_language 제거

        for entry in all_extracted_entries:
            key_tuple = (entry.keyword.lower(), entry.target_language.lower().split('-')[0]) # 키에서 source_language 제거
            if key_tuple not in final_entries_map:
                # 첫 번째 등장: 이 번역을 최종 번역으로 사용
                final_entries_map[key_tuple] = entry
            else:
                # 이미 존재하는 키: 번역은 유지하고 등장 횟수만 합산
                final_entries_map[key_tuple].occurrence_count += entry.occurrence_count
        
        final_glossary = list(final_entries_map.values())
        # 최종 용어집 정렬 (예: 키워드, 도착언어 순)
        final_glossary.sort(key=lambda x: (x.keyword.lower(), x.target_language.lower())) # 정렬 키에서 source_language 제거
              
        logger.info(f"용어집 충돌 해결 완료. 최종 {len(final_glossary)}개 항목.")
        return final_glossary

    def _select_best_entry_from_group(self, entry_group: List[GlossaryEntryDTO]) -> GlossaryEntryDTO: # DTO 변경
        """주어진 용어집 항목 그룹에서 가장 좋은 항목을 선택합니다 (예: 가장 긴 설명, 가장 높은 중요도)."""
        if not entry_group:
            raise ValueError("빈 용어집 항목 그룹에서 최선 항목을 선택할 수 없습니다.")
        # 경량화 버전에서는 복잡한 선택 로직 대신 첫 번째 항목 반환 또는 등장 횟수 많은 것 선택 등
        entry_group.sort(key=lambda e: (-e.occurrence_count, e.keyword.lower())) # 등장 횟수 많은 순, 같으면 키워드 순
        return entry_group[0]

    # =====================================================================
    # 비동기 메서드 (Async Methods)
    # =====================================================================

    async def _extract_glossary_entries_from_segment_via_api_async(
        self,
        segment_text: str,
        user_override_glossary_prompt: Optional[str] = None,
        stop_check: Optional[Callable[[], bool]] = None
    ) -> List[GlossaryEntryDTO]:
        """
        단일 텍스트 세그먼트에서 Gemini API를 사용하여 용어집 항목들을 추출합니다. (비동기 버전)
        프리필(Prefill) 및 구조화된 출력(Structured Output)을 지원합니다.
        
        Args:
            segment_text: 분석할 텍스트 세그먼트
            user_override_glossary_prompt: 사용자 정의 프롬프트 (옵션)
            stop_check: 중단 요청 확인 콜백
            
        Returns:
            추출된 용어집 항목 리스트
            
        Raises:
            BtgApiClientException: API 호출 실패 시
            BtgBusinessLogicException: 내부 오류 시
            asyncio.CancelledError: 작업 취소 시
        """
        # 📍 중단 체크 1: 작업 시작 전
        if stop_check and stop_check():
            logger.info("용어집 추출이 중단되었습니다 (작업 시작 전)")
            raise asyncio.CancelledError("용어집 추출 중단 요청됨")
        
        model_name = self.config.get("model_name", "gemini-2.0-flash")
        generation_config_params = { 
            "temperature": self.config.get("glossary_extraction_temperature", 0.3),
            "response_mime_type": "application/json",
            "response_schema": list[ApiGlossaryTerm],
            "thinking_level": self.config.get("thinking_level", "high")
        }

        api_prompt_for_gemini_client: Union[str, List[genai_types.Content]]
        api_system_instruction: Optional[str] = None

        # --- 프리필(Prefill) 모드 확인 ---
        if self.config.get("enable_glossary_prefill", False):
            logger.info("용어집 추출 프리필 모드 활성화됨.")
            
            # 1. 시스템 지침 설정
            api_system_instruction = self.config.get("glossary_prefill_system_instruction", "")
            
            # 2. 캐시된 히스토리 로드 및 변환
            prefill_history_raw = self.config.get("glossary_prefill_cached_history", [])
            base_history: List[genai_types.Content] = []
            
            if isinstance(prefill_history_raw, list):
                for item in prefill_history_raw:
                    if isinstance(item, dict) and "role" in item and "parts" in item:
                        sdk_parts = []
                        for part_item in item.get("parts", []):
                            if isinstance(part_item, str):
                                sdk_parts.append(genai_types.Part.from_text(text=part_item))
                        if sdk_parts:
                            base_history.append(genai_types.Content(role=item["role"], parts=sdk_parts))
            
            # 3. 슬롯 주입 ({novelText})
            replacements = {
                "{novelText}": segment_text
            }
            
            injected_history, injected = _inject_slots_into_history(base_history, replacements)
            
            if injected:
                logger.debug("히스토리 내 슬롯이 감지되어 세그먼트 텍스트를 주입했습니다.")
                api_prompt_for_gemini_client = injected_history
                
                # [Trigger Logic] 마지막 메시지가 Model이면 이어쓰기를 위한 빈 User 메시지 추가
                if api_prompt_for_gemini_client and api_prompt_for_gemini_client[-1].role == "model":
                    logger.debug("마지막 메시지가 Model이므로 이어쓰기를 위한 빈 User 트리거를 추가합니다.")
                    api_prompt_for_gemini_client.append(
                        genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=" ")])
                    )
            else:
                # 슬롯이 없으면 기존 템플릿 방식 프롬프트를 유저 메시지로 추가
                logger.info("히스토리 내부에 슬롯이 없습니다. 표준 프롬프트를 추가합니다.")
                prompt_str = self._get_glossary_extraction_prompt(segment_text, user_override_glossary_prompt)
                api_prompt_for_gemini_client = injected_history
                api_prompt_for_gemini_client.append(
                    genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=prompt_str)])
                )
        else:
            # --- 표준 모드 ---
            api_prompt_for_gemini_client = self._get_glossary_extraction_prompt(segment_text, user_override_glossary_prompt)

        # 📍 중단 체크 2: API 호출 직전
        if stop_check and stop_check():
            logger.info("용어집 추출이 중단되었습니다 (API 호출 직전)")
            raise asyncio.CancelledError("용어집 추출 중단 요청됨")

        try:
            # 비동기 API 호출
            response_data = await self.gemini_client.generate_text_async(
                prompt=api_prompt_for_gemini_client,
                model_name=model_name,
                generation_config_dict=generation_config_params,
                thinking_budget=self.config.get("thinking_budget", None),
                system_instruction_text=api_system_instruction
            )

            # 📍 중단 체크 3: API 응답 후
            if stop_check and stop_check():
                logger.info("용어집 추출이 중단되었습니다 (API 응답 후)")
                raise asyncio.CancelledError("용어집 추출 중단 요청됨")

            # --- 응답 처리 로직 (기존과 동일) ---
            if isinstance(response_data, list) and all(isinstance(item, ApiGlossaryTerm) for item in response_data):
                logger.debug("GeminiClient가 ApiGlossaryTerm 객체 리스트를 반환했습니다.")
                return self._parse_api_glossary_terms_to_dto(response_data)
            elif isinstance(response_data, list) and all(isinstance(item, dict) for item in response_data):
                logger.warning("GeminiClient가 dict 리스트를 반환했습니다.")
                return self._parse_dict_list_to_dto(response_data)
            elif isinstance(response_data, dict):
                logger.warning(f"GeminiClient가 예상치 못한 딕셔너리를 반환했습니다: {str(response_data)[:200]}")
                raw_terms_fallback = response_data.get("terms")
                if isinstance(raw_terms_fallback, list):
                    return self._parse_dict_list_to_dto(raw_terms_fallback)
                else:
                    logger.error(f"API 응답이 유효한 형식이 아닙니다: {response_data}")
                    return []
            elif response_data is None:
                logger.warning(f"용어집 추출 API로부터 응답을 받지 못했습니다.")
                return []
            elif isinstance(response_data, str):
                logger.warning(f"GeminiClient가 문자열을 반환했습니다 (JSON 파싱 실패 추정): {response_data[:200]}...")
                return []
            else:
                logger.warning(f"GeminiClient로부터 예상치 않은 타입의 응답 ({type(response_data)})을 받았습니다.")
                return []
            
        except asyncio.CancelledError:
            logger.info("용어집 추출이 취소되었습니다.")
            raise
        except GeminiAllApiKeysExhaustedException as e_keys:
            logger.critical(f"모든 API 키 소진으로 용어집 추출 중단: {e_keys}")
            raise BtgApiClientException(f"모든 API 키 소진: {e_keys}", original_exception=e_keys) from e_keys
        except GeminiApiException as e_api:
            logger.error(f"용어집 추출 API 호출 최종 실패: {e_api}. 세그먼트: {segment_text[:50]}...")
            raise BtgApiClientException(f"용어집 추출 API 호출 최종 실패: {e_api}", original_exception=e_api) from e_api       
        except Exception as e:
            logger.error(f"용어집 추출 중 예상치 못한 내부 오류: {e}.", exc_info=True)
            raise BtgBusinessLogicException(f"용어집 추출 중 내부 오류: {e}", original_exception=e) from e

    async def extract_and_save_glossary_async(
        self,
        novel_text_content: str,
        input_file_path_for_naming: Union[str, Path],
        progress_callback: Optional[Callable[[GlossaryExtractionProgressDTO], None]] = None,
        seed_glossary_path: Optional[Union[str, Path]] = None,
        user_override_glossary_extraction_prompt: Optional[str] = None,
        stop_check: Optional[Callable[[], bool]] = None,
        max_workers: int = 4,
        rpm: int = 60
    ) -> Path:
        """
        주어진 텍스트 내용에서 로어북을 추출하고 JSON 파일에 저장합니다. (비동기 버전)
        
        Args:
            novel_text_content: 분석할 전체 텍스트 내용
            input_file_path_for_naming: 출력 JSON 파일 이름 생성에 사용될 원본 입력 파일 경로
            progress_callback: 진행 상황을 알리기 위한 콜백 함수
            seed_glossary_path: 참고할 기존 용어집 JSON 파일 경로
            user_override_glossary_extraction_prompt: 용어집 추출 시 사용할 사용자 정의 프롬프트
            stop_check: 중지 요청을 확인하는 콜백 함수
            
        Returns:
            생성된 로어북 JSON 파일의 경로
            
        Raises:
            BtgBusinessLogicException: 용어집 추출 또는 저장 과정에서 심각한 오류 발생 시
            asyncio.CancelledError: 작업 취소 시
        """
        all_extracted_entries_from_segments: List[GlossaryEntryDTO] = []
        seed_entries: List[GlossaryEntryDTO] = []

        # 시드 용어집 로드
        if seed_glossary_path:
            seed_path_obj = Path(seed_glossary_path)
            if seed_path_obj.exists() and seed_path_obj.is_file():
                try:
                    logger.info(f"시드 용어집 파일 로드 중: {seed_path_obj}")
                    raw_seed_data = read_json_file(seed_path_obj)
                    if isinstance(raw_seed_data, list):
                        for item_dict in raw_seed_data:
                            if isinstance(item_dict, dict) and "keyword" in item_dict and \
                               "translated_keyword" in item_dict and \
                               "target_language" in item_dict:
                                try:
                                    entry = GlossaryEntryDTO(
                                        keyword=item_dict.get("keyword", ""),
                                        translated_keyword=item_dict.get("translated_keyword", ""),
                                        target_language=item_dict.get("target_language", ""),
                                        occurrence_count=int(item_dict.get("occurrence_count", 0))
                                    )
                                    if entry.keyword and entry.translated_keyword:
                                        seed_entries.append(entry)
                                except (TypeError, ValueError) as e_dto:
                                    logger.warning(f"시드 용어집 항목 DTO 변환 중 오류: {item_dict}, 오류: {e_dto}")
                        logger.info(f"{len(seed_entries)}개의 시드 용어집 항목 로드 완료.")
                except Exception as e_seed:
                    logger.error(f"시드 용어집 파일 로드 중 오류 ({seed_path_obj}): {e_seed}", exc_info=True)
            else:
                logger.warning(f"제공된 시드 용어집 경로를 찾을 수 없거나 파일이 아닙니다: {seed_glossary_path}")
        
        # ChunkService를 사용하여 텍스트를 세그먼트로 분할
        glossary_segment_size = self.config.get("glossary_chunk_size", self.config.get("chunk_size", 8000))
        all_text_segments = self.chunk_service.create_chunks_from_file_content(novel_text_content, glossary_segment_size)

        sample_segments = self._select_sample_segments(all_text_segments)
        num_sample_segments = len(sample_segments)

        # 진행률 표시를 위한 유효 총 세그먼트 수 계산
        effective_total_segments_for_progress = num_sample_segments
        if num_sample_segments == 0 and seed_entries:
            effective_total_segments_for_progress = 1
        elif num_sample_segments == 0 and not novel_text_content.strip() and not seed_entries:
            effective_total_segments_for_progress = 0

        # 빈 입력 처리
        if not novel_text_content.strip() and not sample_segments and not seed_entries:
            logger.info("입력 텍스트가 비어있고, 표본 세그먼트 및 시드 용어집도 없습니다. 빈 용어집을 생성합니다.")
            lorebook_output_path = self._get_lorebook_output_path(input_file_path_for_naming)
            self._save_glossary_to_json([], lorebook_output_path)
            if progress_callback:
                progress_callback(GlossaryExtractionProgressDTO(
                    total_segments=effective_total_segments_for_progress,
                    processed_segments=0,
                    current_status_message="입력 텍스트 및 시드 없음",
                    extracted_entries_count=0
                ))
            return lorebook_output_path
        elif not novel_text_content.strip() and not sample_segments and seed_entries:
            logger.info("입력 텍스트가 비어있고 표본 세그먼트가 없습니다. 시드 용어집만으로 처리합니다.")
            all_extracted_entries_from_segments.extend(seed_entries)
            if progress_callback:
                progress_callback(GlossaryExtractionProgressDTO(
                    total_segments=effective_total_segments_for_progress,
                    processed_segments=0,
                    current_status_message="시드 용어집 처리 중...", 
                    extracted_entries_count=len(seed_entries)
                ))
        elif sample_segments:
            logger.info(f"총 {len(all_text_segments)}개 세그먼트 중 {num_sample_segments}개의 표본 세그먼트로 용어집 추출 시작...")
            logger.info(f"동시 작업 수: {max_workers}, RPM 제한: {rpm}/분")
        
            processed_segments_count = 0
            if progress_callback:
                progress_callback(GlossaryExtractionProgressDTO(
                    total_segments=effective_total_segments_for_progress,
                    processed_segments=processed_segments_count,
                    current_status_message="추출 시작 중...",
                    extracted_entries_count=len(seed_entries)
                ))

            # 세마포어로 동시 실행 수 제한
            semaphore = asyncio.Semaphore(max_workers)
            
            # RPM 제한을 위한 속도 제한 장치 (시간 간격 계산)
            request_interval = 60.0 / rpm if rpm > 0 else 0
            last_request_time = 0
            
            async def rate_limited_extract(segment_text: str) -> List[GlossaryEntryDTO]:
                """RPM 제한을 고려한 추출 함수"""
                nonlocal last_request_time
                
                # 📍 취소 확인 1: API 호출 전
                if stop_check and stop_check():
                    raise asyncio.CancelledError("용어집 추출이 취소되었습니다 (작업 시작 전)")
                
                # 세마포어로 동시 실행 제한
                async with semaphore:
                    # 📍 취소 확인 2: 세마포어 획득 후
                    if stop_check and stop_check():
                        raise asyncio.CancelledError("용어집 추출이 취소되었습니다 (세마포어 획득 후)")
                    
                    # RPM 속도 제한 적용
                    elapsed = asyncio.get_event_loop().time() - last_request_time
                    if elapsed < request_interval:
                        await asyncio.sleep(request_interval - elapsed)
                    
                    # 📍 취소 확인 3: RPM 대기 후
                    if stop_check and stop_check():
                        raise asyncio.CancelledError("용어집 추출이 취소되었습니다 (RPM 대기 후)")
                    
                    last_request_time = asyncio.get_event_loop().time()
                    
                    return await self._extract_glossary_entries_from_segment_via_api_async(
                        segment_text,
                        user_override_glossary_extraction_prompt,
                        stop_check  # stop_check 전달
                    )

            # 작업을 순차적으로 생성하고 처리 (동시성은 semaphore로 제어)
            tasks = []
            for segment_idx, segment in enumerate(sample_segments):
                # 📍 취소 확인: 작업 생성 전
                if stop_check and stop_check():
                    logger.warning(f"사용자 요청으로 용어집 추출을 중단합니다. {segment_idx}/{num_sample_segments}개 세그먼트 처리 중 중단.")
                    break
                
                task = asyncio.create_task(rate_limited_extract(segment))
                tasks.append((task, segment))
            
            # 생성된 작업들을 완료 처리
            for task, segment in tasks:
                try:
                    # GeminiClient의 http_options timeout에 의존
                    # (기본값: _TIMEOUT_SECONDS = 500초)
                    extracted_entries_for_segment = await task
                    if extracted_entries_for_segment:
                        all_extracted_entries_from_segments.extend(extracted_entries_for_segment)
                except asyncio.CancelledError:
                    logger.info("용어집 추출이 취소되었습니다.")
                    # 나머지 진행 중인 작업들도 취소
                    for remaining_task, _ in tasks:
                        if not remaining_task.done():
                            remaining_task.cancel()
                    raise  # 상위로 전파하여 즉시 종료
                except BtgApiClientException as e_api:
                    if isinstance(e_api.original_exception, GeminiAllApiKeysExhaustedException):
                        logger.critical("모든 API 키가 소진되어 용어집 추출 작업을 중단합니다.")
                        for remaining_task, _ in tasks:
                            if not remaining_task.done():
                                remaining_task.cancel()
                        raise e_api
                    logger.error(f"표본 세그먼트 처리 중 API 오류 발생 (세그먼트: {segment[:50]}...): {e_api}")
                except Exception as exc:
                    logger.error(f"표본 세그먼트 처리 중 예외 발생 (세그먼트: {segment[:50]}...): {exc}")
                finally:
                    processed_segments_count += 1
                    if progress_callback:
                        status_msg = f"표본 세그먼트 {processed_segments_count}/{len(tasks)} 처리 완료"
                        if processed_segments_count == len(tasks):
                            status_msg = "모든 표본 세그먼트 처리 완료, 충돌 해결 및 저장 중..."
                        progress_callback(GlossaryExtractionProgressDTO(
                            total_segments=effective_total_segments_for_progress,
                            processed_segments=processed_segments_count,
                            current_status_message=status_msg,
                            extracted_entries_count=len(all_extracted_entries_from_segments) + len(seed_entries)
                        ))

        # 시드 항목이 있고, 새로운 추출도 있었다면 병합
        if seed_entries and (novel_text_content.strip() and sample_segments):
            logger.info(f"{len(seed_entries)}개의 시드 항목을 새로 추출된 항목과 병합합니다. (시드 항목 우선)")
            all_extracted_entries_from_segments = seed_entries + all_extracted_entries_from_segments

        # 충돌 해결
        final_glossary = self._resolve_glossary_conflicts(all_extracted_entries_from_segments)
        
        # 중요도(등장 횟수)에 따라 정렬
        final_glossary.sort(key=lambda x: (-x.occurrence_count, x.keyword.lower()))
        logger.info(f"최종 용어집을 등장 횟수 순으로 정렬했습니다. (상위 3개: {[e.keyword for e in final_glossary[:3]]})")

        # 로어북 최대 항목 수 제한
        max_total_glossary_entries = self.config.get("glossary_max_total_entries", 500)
        if len(final_glossary) > max_total_glossary_entries:
            logger.info(f"정렬된 용어집 항목({len(final_glossary)}개)이 최대 제한({max_total_glossary_entries}개)을 초과하여 상위 항목만 저장합니다.")
            final_glossary = final_glossary[:max_total_glossary_entries]

        # 최종 로어북 저장
        glossary_output_path = self._get_lorebook_output_path(input_file_path_for_naming)
        self._save_glossary_to_json(final_glossary, glossary_output_path)
        
        logger.info(f"용어집 추출 및 저장 완료. 결과: {glossary_output_path}")

        # 최종 진행률 콜백
        if progress_callback:
            final_processed_segments = processed_segments_count if sample_segments else (1 if seed_entries else 0)
            progress_callback(GlossaryExtractionProgressDTO(
                total_segments=effective_total_segments_for_progress,
                processed_segments=final_processed_segments,
                current_status_message=f"추출 완료: {glossary_output_path.name}",
                extracted_entries_count=len(final_glossary)
            ))
        return glossary_output_path

