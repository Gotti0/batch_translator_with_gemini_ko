# translation_service.py
import time
import random
import re
import csv
import asyncio
from pathlib import Path
from typing import Dict, Any, Optional, List, Union, Callable
import os
import copy # Moved here

try:
    from infrastructure.gemini_client import (
        GeminiClient,
        GeminiContentSafetyException,
        GeminiRateLimitException,
        GeminiApiException,
        GeminiInvalidRequestException,
        GeminiAllApiKeysExhaustedException 
    )
    from infrastructure.file_handler import read_json_file
    from infrastructure.logger_config import setup_logger
    from core.exceptions import BtgTranslationException, BtgApiClientException
    from utils.chunk_service import ChunkService
    # types 모듈은 gemini_client에서 사용되므로, 여기서는 직접적인 의존성이 없을 수 있습니다. # 로어북 -> 용어집
    # 만약 이 파일 내에서 types.Part 등을 직접 사용한다면, 아래와 같이 임포트가 필요합니다. # 로어북 -> 용어집
    from google.genai import types as genai_types
    from core.dtos import GlossaryEntryDTO
except ImportError:
    from infrastructure.gemini_client import (  # type: ignore
        GeminiClient,
        GeminiContentSafetyException,
        GeminiRateLimitException,
        GeminiApiException,
        GeminiInvalidRequestException,
        GeminiAllApiKeysExhaustedException 
    )
    from infrastructure.file_handler import read_json_file  # type: ignore
    from infrastructure.logger_config import setup_logger  # type: ignore
    from core.exceptions import BtgTranslationException, BtgApiClientException  # type: ignore
    from utils.chunk_service import ChunkService  # type: ignore
    from core.dtos import GlossaryEntryDTO # type: ignore
    from google.genai import types as genai_types # Fallback import

logger = setup_logger(__name__)

def _format_glossary_for_prompt( # 함수명 변경
    glossary_entries: List[GlossaryEntryDTO], # DTO는 GlossaryEntryDTO (경량화된 버전)
    max_entries: int,
    max_chars: int
) -> str:
    if not glossary_entries:
        return "용어집 컨텍스트 없음" # 메시지 변경

    selected_entries_str = []
    current_chars = 0
    entries_count = 0

    # 등장 횟수 많은 순, 같으면 키워드 가나다 순으로 정렬
    sorted_entries = sorted(glossary_entries, key=lambda x: (-x.occurrence_count, x.keyword.lower()))

    for entry in sorted_entries:
        if entries_count >= max_entries:
            break
        
        # 현재 항목 추가 시 최대 글자 수 초과하면 중단 (단, 최소 1개는 포함되도록)
        # DTO에서 source_language가 제거되었으므로 해당 부분 포맷팅에서 제외
        entry_str = (f"- {entry.keyword} "
                     f"-> {entry.translated_keyword} ({entry.target_language}) "
                     f"(등장: {entry.occurrence_count}회)")
        if current_chars + len(entry_str) > max_chars and entries_count > 0:
            break
        
        selected_entries_str.append(entry_str)
        current_chars += len(entry_str) + 1 # +1 for newline
        entries_count += 1

    if not selected_entries_str:
        return "용어집 컨텍스트 없음 (제한으로 인해 선택된 항목 없음)" # 메시지 변경
        
    return "\n".join(selected_entries_str)

def _inject_slots_into_history(
    history: List[genai_types.Content], 
    replacements: Dict[str, str]
) -> tuple[List[genai_types.Content], bool]:
    """
    히스토리 내의 Content 객체들을 순회하며 슬롯({{slot}} 등)을 실제 값으로 치환합니다.
    반환값: (수정된 히스토리, 치환 발생 여부)
    """
    # 깊은 복사로 원본 오염 방지
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

class TranslationService:
    def __init__(self, gemini_client: GeminiClient, config: Dict[str, Any]):
        self.gemini_client = gemini_client
        self.config = config
        self.chunk_service = ChunkService()
        self.glossary_entries_for_injection: List[GlossaryEntryDTO] = [] # Renamed and type changed
        self.stop_check_callback: Optional[Callable[[], bool]] = None  # 중단 요청 확인용 콜백

        if self.config.get("enable_dynamic_glossary_injection", False): # Key changed
            self._load_glossary_data() # 함수명 변경
            logger.info("동적 용어집 주입 활성화됨. 용어집 데이터 로드 시도.") # 메시지 변경
        else:
            logger.info("동적 용어집 주입 비활성화됨. 용어집 컨텍스트 없이 번역합니다.") # 메시지 변경

    def _load_glossary_data(self): # 함수명 변경
        # 데이터를 로드하기 전에 항상 목록을 초기화합니다.
        self.glossary_entries_for_injection = []
        
        # 통합된 용어집 경로 사용
        lorebook_json_path_str = self.config.get("glossary_json_path")
        if lorebook_json_path_str and os.path.exists(lorebook_json_path_str):
            lorebook_json_path = Path(lorebook_json_path_str)
            try:
                raw_data = read_json_file(lorebook_json_path)
                if isinstance(raw_data, list):
                    for item_dict in raw_data:
                        if isinstance(item_dict, dict) and \
                           "keyword" in item_dict and \
                           "translated_keyword" in item_dict and \
                           "target_language" in item_dict:
                            try:
                                entry = GlossaryEntryDTO( # Explicitly use GlossaryEntryDTO
                                    keyword=item_dict.get("keyword", ""),
                                    translated_keyword=item_dict.get("translated_keyword", ""),
                                    target_language=item_dict.get("target_language", ""),
                                    occurrence_count=int(item_dict.get("occurrence_count", 0))
                                )
                                if all([entry.keyword, entry.translated_keyword, entry.target_language]): # 필수 필드 확인 (source_language 제거)
                                    self.glossary_entries_for_injection.append(entry)
                                else:
                                    logger.warning(f"경량 용어집 항목에 필수 필드 누락: {item_dict}")
                            except (TypeError, ValueError) as e_dto:
                                logger.warning(f"용어집 항목 DTO 변환 중 오류: {item_dict}, 오류: {e_dto}") # 메시지 변경
                        else:
                            logger.warning(f"잘못된 용어집 항목 형식 (딕셔너리가 아니거나 필수 키 'keyword' 또는 'translated_keyword' 누락) 건너뜀: {item_dict}") # 메시지 변경
                    logger.info(f"{len(self.glossary_entries_for_injection)}개의 용어집 항목을 로드했습니다: {lorebook_json_path}") # 메시지 변경
                else: # type: ignore
                    logger.error(f"용어집 JSON 파일이 리스트 형식이 아닙니다: {lorebook_json_path}, 타입: {type(raw_data)}") # 메시지 변경
            except Exception as e:
                logger.error(f"용어집 JSON 파일 처리 중 예상치 못한 오류 ({lorebook_json_path}): {e}", exc_info=True) # 메시지 변경
                self.glossary_entries_for_injection = []
        else:
            logger.info(f"용어집 JSON 파일({lorebook_json_path_str})이 설정되지 않았거나 존재하지 않습니다. 동적 주입을 위해 용어집을 사용하지 않습니다.") # 메시지 변경
            self.glossary_entries_for_injection = []

    def _construct_prompt(self, chunk_text: str) -> str:
        prompt_template = self.config.get("prompts", "Translate to Korean: {{slot}}")
        if isinstance(prompt_template, (list, tuple)):
            prompt_template = prompt_template[0] if prompt_template else "Translate to Korean: {{slot}}"

        final_prompt = prompt_template

        # Determine the source language for the current chunk to filter glossary entries
        config_source_lang = self.config.get("novel_language") # 통합된 설정 사용
        # Fallback language from config, with a hardcoded default if the config key itself is missing
        config_fallback_lang = self.config.get("novel_language_fallback", "ja") # 통합된 폴백 설정 사용

        # "auto" 모드일 때, LLM이 언어를 감지하고 용어집을 필터링하도록 프롬프트가 구성됩니다.
        # Python 단에서 current_source_lang_for_translation을 확정하지 않습니다.
        # 로깅이나 특정 조건부 로직을 위해선 여전히 필요할 수 있으나, 용어집 필터링은 LLM으로 넘어갑니다.
        current_source_lang_for_glossary_filtering: Optional[str] = None

        if config_source_lang == "auto":
            logger.info(f"번역 출발 언어 설정: 'auto'. LLM이 프롬프트 내에서 언어를 감지하고 용어집을 적용하도록 합니다.") # 메시지 변경
            # current_source_lang_for_glossary_filtering는 None으로 유지하거나 "auto"로 설정.
            # 용어집 필터링은 LLM의 역할이 됩니다.
        elif config_source_lang and isinstance(config_source_lang, str) and config_source_lang.strip(): # Specific language code provided
            current_source_lang_for_glossary_filtering = config_source_lang
            logger.info(f"명시적 번역 출발 언어 '{current_source_lang_for_glossary_filtering}' 사용. 용어집도 이 언어 기준으로 필터링됩니다.") # 메시지 변경
        else: # config_source_lang is None, empty string, or not "auto"
            current_source_lang_for_glossary_filtering = config_fallback_lang
            logger.warning(f"번역 출발 언어가 유효하게 설정되지 않았거나 'auto'가 아닙니다. 폴백 언어 '{current_source_lang_for_glossary_filtering}'를 용어집 필터링에 사용.")

        # 1. Dynamic Glossary Injection
        if self.config.get("enable_dynamic_glossary_injection", False) and \
           self.glossary_entries_for_injection and \
           "{{glossary_context}}" in final_prompt: # Placeholder changed
            
            relevant_entries_for_chunk: List[GlossaryEntryDTO] = []
            chunk_text_lower = chunk_text.lower() # For case-insensitive keyword matching
            # 최종 번역 목표 언어 (예: "ko")
            # 이 설정은 config.json 또는 다른 방식으로 제공되어야 합니다.
            final_target_lang = self.config.get("target_translation_language", "ko").lower()

            if config_source_lang == "auto":
                # "auto" 모드: 청크의 언어는 LLM이 감지.
                # 용어집 항목의 target_language가 최종 번역 목표 언어와 일치하는 것만 고려.
                # source_language 필터링은 LLM의 문맥 이해에 맡기거나, 여기서 간단한 키워드 매칭만 수행.
                logger.info(f"자동 언어 감지 모드: 용어집은 키워드 일치 및 최종 목표 언어({final_target_lang}) 일치로 필터링 후 LLM에 전달.") # 메시지 변경
                for entry in self.glossary_entries_for_injection:
                    if entry.target_language.lower() == final_target_lang and \
                       entry.keyword.lower() in chunk_text_lower:
                        relevant_entries_for_chunk.append(entry)
            else:
                # 명시적 언어 설정 모드: Python에서 언어 및 키워드 기반으로 필터링.
                logger.info(f"명시적 언어 모드 ('{current_source_lang_for_glossary_filtering}'): 용어집을 출발어/도착어 및 키워드 기준으로 필터링.") # 메시지 변경
                for entry in self.glossary_entries_for_injection:
                    # source_language 필터링 제거. DTO에 해당 필드가 없으므로.
                    if entry.target_language.lower() == final_target_lang and \
                       entry.keyword.lower() in chunk_text_lower:
                        relevant_entries_for_chunk.append(entry)
                    # source_language 관련 로깅 제거
                    elif not (entry.target_language.lower() == final_target_lang): # target_language 불일치 로깅은 유지
                        logger.debug(f"용어집 항목 '{entry.keyword}' 건너뜀: 도착 언어 불일치 (용어집TL: {entry.target_language}, 최종TL: {final_target_lang}).")
                        continue
            
            logger.debug(f"현재 청크에 대해 {len(relevant_entries_for_chunk)}개의 관련 용어집 항목 발견.") # 메시지 변경

            # 1.b. Format the relevant entries for the prompt
            max_entries = self.config.get("max_glossary_entries_per_chunk_injection", 3) # Key changed
            max_chars = self.config.get("max_glossary_chars_per_chunk_injection", 500) # Key changed
            
            formatted_glossary_context = _format_glossary_for_prompt( # 함수명 변경
                relevant_entries_for_chunk, max_entries, max_chars # Pass only relevant entries
            )
            
            # Check if actual content was formatted (not just "없음" messages)
            final_prompt = final_prompt.replace("{{glossary_context}}", formatted_glossary_context) # Placeholder changed
        else:
            if "{{glossary_context}}" in final_prompt: # Placeholder changed
                 final_prompt = final_prompt.replace("{{glossary_context}}", "용어집 컨텍스트 없음 (주입 비활성화 또는 해당 항목 없음)") # Placeholder changed
                 logger.debug("동적 용어집 주입 비활성화 또는 플레이스홀더 부재로 '컨텍스트 없음' 메시지 사용.")
        
        # 3. Main content slot - This should be done *after* all other placeholders are processed.
        final_prompt = final_prompt.replace("{{slot}}", chunk_text)
        return final_prompt

    def translate_text(self, text_chunk: str, stream: bool = False) -> str:
        """
        주어진 텍스트 청크를 번역합니다.
        슬롯 주입(Slot Injection) 방식을 지원하도록 리팩토링되었습니다.
        """
        if not text_chunk.strip():
            logger.debug("Translate_text: 입력 텍스트가 비어 있어 빈 문자열 반환.")
            return ""
        
        # 1. 용어집 및 프롬프트 준비
        # 기존 _construct_prompt 로직 중 용어집 생성 부분만 가져옵니다.
        # (단, Slot Injection 모드에서는 프롬프트 템플릿 전체를 가져오는게 아니라 용어집 문자열만 필요함)
        glossary_context_str = "용어집 컨텍스트 없음"
        
        # 용어집 로직 수행 (기존 _construct_prompt 참조하여 문자열만 추출)
        if self.config.get("enable_dynamic_glossary_injection", False) and self.glossary_entries_for_injection:
             logger.info("용어집 컨텍스트 주입 활성화됨 (청크 내 관련 키워드 체크).")
             # ... (기존 용어집 필터링 로직과 동일하게 수행하여 glossary_context_str 생성) ...
             # 코드 간결화를 위해 핵심 로직만 요약:
             chunk_text_lower = text_chunk.lower()
             final_target_lang = self.config.get("target_translation_language", "ko").lower()
             relevant_entries = []
             
             # 용어집 필터링 로직 (기존과 동일)
             config_source_lang = self.config.get("novel_language", "auto")
             for entry in self.glossary_entries_for_injection:
                if entry.target_language.lower() == final_target_lang and entry.keyword.lower() in chunk_text_lower:
                    relevant_entries.append(entry)
             
             max_entries = self.config.get("max_glossary_entries_per_chunk_injection", 3)
             max_chars = self.config.get("max_glossary_chars_per_chunk_injection", 500)
             glossary_context_str = _format_glossary_for_prompt(relevant_entries, max_entries, max_chars)

             if not glossary_context_str.startswith("용어집 컨텍스트 없음"):
                logger.info(f"API 요청에 주입할 용어집 컨텍스트 생성됨. 내용 일부: {glossary_context_str[:100]}...")
             else:
                logger.debug("현재 청크에 주입할 관련 용어집 항목을 찾지 못함.")
        
        # 2. 치환 데이터 맵 준비
        replacements = {
            "{{slot}}": text_chunk,
            "{{glossary_context}}": glossary_context_str
        }

        api_prompt_for_gemini_client: List[genai_types.Content] = []
        api_system_instruction: Optional[str] = None

        # 3. 프리필 및 히스토리 구성 로직 (핵심 변경 사항)
        if self.config.get("enable_prefill_translation", False):
            logger.info("프리필 번역 모드 활성화됨 (Slot Injection 체크).")
            
            # 시스템 지침 설정
            api_system_instruction = self.config.get("prefill_system_instruction", "")
            
            # 캐시된 히스토리 로드 및 Content 객체 변환
            prefill_cached_history_raw = self.config.get("prefill_cached_history", [])
            base_history: List[genai_types.Content] = []
            
            if isinstance(prefill_cached_history_raw, list):
                for item in prefill_cached_history_raw:
                    if isinstance(item, dict) and "role" in item and "parts" in item:
                        sdk_parts = []
                        for part_item in item.get("parts", []):
                            if isinstance(part_item, str):
                                sdk_parts.append(genai_types.Part.from_text(text=part_item))
                        if sdk_parts:
                            base_history.append(genai_types.Content(role=item["role"], parts=sdk_parts))

            # [Slot Injection] 히스토리 내 슬롯 치환 시도
            injected_history, injected = _inject_slots_into_history(base_history, replacements)

            if injected:
                logger.info("히스토리 내부에서 '{{slot}}'이 감지되어 원문을 주입했습니다 (Jailbreak 모드).")
                api_prompt_for_gemini_client = injected_history
                
                # [Trigger Logic] 마지막 메시지 확인
                if api_prompt_for_gemini_client:
                    last_msg = api_prompt_for_gemini_client[-1]
                    
                    if last_msg.role == "model":
                        # 마지막이 모델(프리필)이면, 이어 쓰기를 유도하기 위해 빈 유저 메시지(Trigger) 추가
                        logger.debug("마지막 메시지가 Model이므로 이어쓰기를 위한 빈 User 트리거를 추가합니다.")
                        api_prompt_for_gemini_client.append(
                            genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=" ")])
                        )
                    # 마지막이 User라면 그대로 전송 (User 메시지가 Trigger가 됨)
            else:
                # 슬롯이 없으면 기존 방식대로: 히스토리 + (프롬프트 + 원문)
                logger.info("히스토리 내부에 슬롯이 없습니다. 표준 프리필 방식으로 원문을 끝에 추가합니다.")
                api_prompt_for_gemini_client = injected_history # 치환된게 없으면 원본과 같음
                
                # 기존 방식의 프롬프트 생성 (여기서 {{slot}} 처리됨)
                user_prompt_str = self._construct_prompt(text_chunk)
                api_prompt_for_gemini_client.append(
                    genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=user_prompt_str)])
                )
        else:
            # 표준 모드 (프리필 끔)
            logger.info("표준 번역 모드 (프리필 Off).")
            user_prompt_str = self._construct_prompt(text_chunk)
            api_prompt_for_gemini_client = [
                genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=user_prompt_str)])
            ]

        try:
            # Gemini Client 호출
            translated_text_from_api = self.gemini_client.generate_text(
                prompt=api_prompt_for_gemini_client, # 이제 항상 List[Content]
                model_name=self.config.get("model_name", "gemini-2.0-flash"),
                generation_config_dict={
                    "temperature": self.config.get("temperature", 0.7),
                    "top_p": self.config.get("top_p", 0.9),
                    "thinking_level": self.config.get("thinking_level", "high")
                },
                thinking_budget=self.config.get("thinking_budget", None),
                system_instruction_text=api_system_instruction,
                stream=stream
            )

            if translated_text_from_api is None:
                logger.error("GeminiClient.generate_text가 None을 반환했습니다.")
                raise GeminiContentSafetyException("API로부터 응답을 받지 못했습니다 (None 반환).")

            # 빈 문자열 응답도 콘텐츠 안전 문제로 간주하여 재시도 로직을 타도록 수정
            if not translated_text_from_api.strip() and text_chunk.strip():
                logger.warning(f"API가 비어있지 않은 입력에 대해 빈 문자열을 반환했습니다. 원본: '{text_chunk[:100]}...'")
                raise GeminiContentSafetyException("API가 비어있지 않은 입력에 대해 빈 번역 결과를 반환했습니다.")

            logger.debug(f"Gemini API 호출 성공. 번역된 텍스트 (일부): {translated_text_from_api[:100]}...")
            return translated_text_from_api.strip()

        except GeminiContentSafetyException as e_safety:
            logger.warning(f"콘텐츠 안전 문제로 번역 실패: {e_safety}")
            raise BtgTranslationException(f"콘텐츠 안전 문제로 번역할 수 없습니다. ({e_safety})", original_exception=e_safety) from e_safety
        except GeminiAllApiKeysExhaustedException as e_keys:
            logger.error(f"API 키 회전 실패: 모든 API 키 소진 또는 유효하지 않음. 원본 오류: {e_keys}")
            raise BtgApiClientException(f"모든 API 키를 사용했으나 요청에 실패했습니다. API 키 설정을 확인하세요. ({e_keys})", original_exception=e_keys) from e_keys
        except GeminiRateLimitException as e_rate:
            logger.error(f"API 사용량 제한 초과 (키 회전 후에도 발생): {e_rate}")
            raise BtgApiClientException(f"API 사용량 제한을 초과했습니다. 잠시 후 다시 시도해주세요. ({e_rate})", original_exception=e_rate) from e_rate
        except GeminiInvalidRequestException as e_invalid:
            logger.error(f"잘못된 API 요청: {e_invalid}")
            raise BtgApiClientException(f"잘못된 API 요청입니다: {e_invalid}", original_exception=e_invalid) from e_invalid
        except GeminiApiException as e_api:
            logger.error(f"Gemini API 호출 중 일반 오류 발생: {e_api}")
            raise BtgApiClientException(f"API 호출 중 오류가 발생했습니다: {e_api}", original_exception=e_api) from e_api
        except Exception as e:
            logger.error(f"번역 중 예상치 못한 오류 발생: {e}", exc_info=True)
            raise BtgTranslationException(f"번역 중 알 수 없는 오류가 발생했습니다: {e}", original_exception=e) from e

    def translate_text_with_content_safety_retry(
        self, 
        text_chunk: str, 
        max_split_attempts: int = 3,
        min_chunk_size: int = 100
    ) -> str:
        """
        콘텐츠 안전 오류 발생시 청크를 분할하여 재시도하는 번역 메서드
        
        Args:
            text_chunk: 번역할 텍스트
            max_split_attempts: 최대 분할 시도 횟수
            min_chunk_size: 최소 청크 크기
            
        Returns:
            번역된 텍스트 (실패한 부분은 오류 메시지로 대체)
        """
        try:
            # 1차 시도: 전체 청크 번역
            return self.translate_text(text_chunk)
        except BtgTranslationException as e:
            # 콘텐츠 안전 문제가 아닌 경우, 그대로 예외 발생
            if not ("콘텐츠 안전 문제" in str(e)):
                raise e
            
            error_type_for_log = "콘텐츠 안전 문제"
            logger.warning(f"{error_type_for_log} 감지. 청크 분할 재시도 시작: {str(e)}")
            return self._translate_with_recursive_splitting(
                text_chunk, max_split_attempts, min_chunk_size, current_attempt=1
            )

    def _translate_with_recursive_splitting(
        self,
        text_chunk: str,
        max_split_attempts: int,
        min_chunk_size: int,
        current_attempt: int = 1
    ) -> str:
    
        if current_attempt > max_split_attempts:
            logger.error(f"최대 분할 시도 횟수({max_split_attempts})에 도달. 번역 실패.")
            return f"[번역 오류로 인한 실패: 최대 분할 시도 초과]" # 메시지 일반화

        if len(text_chunk.strip()) <= min_chunk_size:
            logger.warning(f"최소 청크 크기에 도달했지만 여전히 오류 발생: {text_chunk[:50]}...")
            return f"[번역 오류로 인한 실패: {text_chunk[:30]}...]" # 메시지 일반화

        logger.info(f"📊 청크 분할 시도 #{current_attempt} (깊이: {current_attempt-1})")
        logger.info(f"   📏 원본 크기: {len(text_chunk)} 글자")
        logger.info(f"   🎯 목표: 정확히 2개 청크로 분할 (이진 분할)")

        
        # Strict 이진 분할 (정확히 2개 청크)
        sub_chunks = self.chunk_service.split_chunk_into_two_halves(
            text_chunk,
            target_size=len(text_chunk) // 2,
            min_chunk_ratio=0.3  # 마지막 청크가 30% 미만이면 병합
        )
        
        # 분할이 안된 경우 문장 기반 분할 시도
        if len(sub_chunks) <= 1:
            logger.info("크기 기반 분할 실패. 문장 기반 분할 시도.")
            sub_chunks = self.chunk_service.split_chunk_by_sentences(
                text_chunk, max_sentences_per_chunk=1
            )
        
        if len(sub_chunks) <= 1:
            logger.error("청크 분할 실패. 번역 포기.")
            return f"[분할 불가능한 오류 발생 콘텐츠: {text_chunk[:30]}...]" # 메시지 일반화
        
        # 각 서브 청크 개별 번역 시도
        translated_parts = []
        total_sub_chunks = len(sub_chunks)
        successful_sub_chunks = 0
        failed_sub_chunks = 0
        
        logger.info(f"🔄 분할 완료: {total_sub_chunks}개 서브 청크 생성")
        
        for i, sub_chunk in enumerate(sub_chunks):
            # 빈 청크 스킵 (공백만 있는 경우 포함)
            if not sub_chunk.strip():
                logger.warning(f"   ⚠️ 서브 청크 {i+1}/{total_sub_chunks} 빈 청크 감지. 스킵.")
                translated_parts.append("")  # 빈 문자열 유지
                continue
            
            sub_chunk_info = f"서브 청크 {i+1}/{total_sub_chunks}"
            sub_chunk_size = len(sub_chunk.strip())
            sub_chunk_preview = sub_chunk.strip()[:50].replace('\n', ' ') + '...'
            
            logger.info(f"   🚀 {sub_chunk_info} 번역 시작")
            logger.debug(f"      📏 크기: {sub_chunk_size} 글자")
            logger.debug(f"      📝 내용: {sub_chunk_preview}")
            
            start_time = time.time()
            
            try:
                if self.stop_check_callback and self.stop_check_callback():
                    logger.info(f"중단 요청 감지됨. 서브 청크 {i+1}/{total_sub_chunks} 번역 중단.")
                    raise BtgTranslationException("번역 중단 요청됨.")
                # 재귀 분할 시 스트리밍 사용
                translated_part = self.translate_text(sub_chunk.strip(), stream=True)
                processing_time = time.time() - start_time
                
                translated_parts.append(translated_part)
                successful_sub_chunks += 1
                
                logger.info(f"   ✅ {sub_chunk_info} 번역 성공 (소요: {processing_time:.2f}초, 깊이: {current_attempt-1})")
                logger.debug(f"      📊 결과 길이: {len(translated_part)} 글자")
                logger.debug(f"      📈 진행률: {(i+1)/total_sub_chunks*100:.1f}% ({i+1}/{total_sub_chunks})")
                logger.debug(f"      📝 번역된 내용 (일부): {translated_part[:50].replace(chr(10), ' ')}...")
                
            except BtgTranslationException as sub_e:
                processing_time = time.time() - start_time
                  # 콘텐츠 안전 문제인 경우 재귀 시도
                if "콘텐츠 안전 문제" in str(sub_e):
                    error_type_for_log_sub = "콘텐츠 안전 문제"
                    logger.warning(f"   🛡️ {sub_chunk_info} {error_type_for_log_sub} 발생 (소요: {processing_time:.2f}초)")
                    logger.info(f"   🔄 재귀 분할 시도 (깊이: {current_attempt} → {current_attempt+1})")
                    
                    # 재귀적으로 더 작게 분할 시도
                    recursive_result = self._translate_with_recursive_splitting(
                        sub_chunk, max_split_attempts, min_chunk_size, current_attempt + 1
                    )
                    translated_parts.append(recursive_result)
                    if "[번역 오류로 인한 실패" in recursive_result or "[분할 불가능한 오류 발생 콘텐츠" in recursive_result: # 오류 메시지 확인 강화
                        failed_sub_chunks += 1
                        logger.warning(f"   ❌ {sub_chunk_info} 최종 실패 (재귀 분할 후에도 검열됨)")
                    else:
                        successful_sub_chunks += 1
                        logger.info(f"   ✅ {sub_chunk_info} 재귀 분할 후 성공")
                else:
                    # 다른 번역 오류인 경우
                    failed_sub_chunks += 1
                    
                    # API로부터 받은 실제 오류 메시지에 가까운 내용을 추출 시도
                    actual_api_error_str = str(sub_e) # 기본값: 잡힌 예외의 전체 메시지
                    if hasattr(sub_e, 'original_exception') and sub_e.original_exception:
                        orig_exc = sub_e.original_exception
                        # BtgApiClientException -> Gemini*Exception 체인 확인
                        if isinstance(orig_exc, BtgApiClientException) and \
                           hasattr(orig_exc, 'original_exception') and orig_exc.original_exception:
                            # orig_exc.original_exception이 Gemini*Exception 객체임
                            actual_api_error_str = str(orig_exc.original_exception)
                        else:
                            # 직접적인 원인 예외의 메시지 사용
                            actual_api_error_str = str(orig_exc)
                    
                    logger.error(f"   ❌ {sub_chunk_info} 번역 실패 (소요: {processing_time:.2f}초, 예외: {type(sub_e).__name__})")
                    logger.error(f"     API 실제 오류 응답 (추정): {actual_api_error_str}") # 상세 오류 로깅
                    translated_parts.append(f"[번역 실패: {str(sub_e)[:100]}]") # 번역 결과에는 간략한 오류 메시지 유지
                
                logger.debug(f"      📈 진행률: {(i+1)/total_sub_chunks*100:.1f}% ({i+1}/{total_sub_chunks})")

        
        # 번역된 부분들을 결합
        final_result = " ".join(translated_parts)
        
        # 분할 번역 완료 요약
        logger.info(f"📋 분할 번역 완료 요약 (깊이: {current_attempt-1})")
        logger.info(f"   📊 총 서브 청크: {total_sub_chunks}개")
        logger.info(f"   ✅ 성공: {successful_sub_chunks}개")
        logger.info(f"   ❌ 실패: {failed_sub_chunks}개")
        logger.info(f"   📏 최종 결과 길이: {len(final_result)} 글자")
        
        if successful_sub_chunks > 0:
            success_rate = (successful_sub_chunks / total_sub_chunks) * 100
            logger.info(f"   📈 성공률: {success_rate:.1f}%")
        
        return final_result
    
    def set_stop_check_callback(self, callback: Optional[Callable[[], bool]]) -> None:
        """
        중단 요청을 확인하는 콜백 함수를 설정합니다.
        
        Args:
            callback: 중단 요청 여부를 반환하는 콜백 함수
        """
        self.stop_check_callback = callback

    # ============================================================================
    # 비동기 메서드 (Phase 2: asyncio 마이그레이션)
    # ============================================================================

    async def translate_chunk_async(
        self,
        chunk_text: str,
        stream: bool = False,
        timeout: Optional[float] = None
    ) -> str:
        """
        비동기 청크 번역 메서드 (진정한 비동기 구현)
        
        Args:
            chunk_text: 번역할 텍스트
            stream: 스트리밍 여부
            timeout: 타임아웃 시간(초)
            
        Returns:
            번역된 텍스트
            
        Raises:
            asyncio.TimeoutError: 타임아웃 초과
            asyncio.CancelledError: 작업 취소됨
            BtgTranslationException: 번역 실패
        """
        # 📍 중단 체크: 작업 시작 전
        if self.stop_check_callback and self.stop_check_callback():
            logger.info("translate_chunk_async: 중단 요청 감지됨 (작업 시작 전)")
            raise asyncio.CancelledError("번역 중단 요청됨")
        
        if not chunk_text.strip():
            logger.debug("translate_chunk_async: 입력 텍스트가 비어 있어 빈 문자열 반환.")
            return ""
        
        # 소설 본문 미리보기 로깅
        text_preview = chunk_text[:100].replace('\n', ' ')
        logger.info(f"비동기 청크 번역 요청: \"{text_preview}{'...' if len(chunk_text) > 100 else ''}\"")
        
        try:
            # 진정한 비동기 메서드 호출 (run_in_executor 제거)
            if timeout:
                result = await asyncio.wait_for(
                    self.translate_text_with_content_safety_retry_async(chunk_text),
                    timeout=timeout
                )
            else:
                result = await self.translate_text_with_content_safety_retry_async(chunk_text)
            
            # 📍 중단 체크: API 응답 후
            if self.stop_check_callback and self.stop_check_callback():
                logger.info("translate_chunk_async: 중단 요청 감지됨 (응답 후)")
                raise asyncio.CancelledError("번역 중단 요청됨")
            
            return result
        except asyncio.TimeoutError:
            logger.error(f"비동기 번역 타임아웃 ({timeout}초)")
            raise
        except asyncio.CancelledError:
            logger.info("비동기 번역이 취소됨")
            raise
        except Exception as e:
            logger.error(f"비동기 번역 중 오류: {type(e).__name__} - {e}", exc_info=True)
            if isinstance(e, BtgTranslationException):
                raise
            raise BtgTranslationException(f"비동기 번역 중 오류: {e}", original_exception=e) from e

    async def translate_text_with_content_safety_retry_async(
        self,
        chunk_text: str,
        max_split_attempts: int = 3,
        min_chunk_size: int = 100,
        timeout: Optional[float] = None
    ) -> str:
        """
        비동기 콘텐츠 안전성 재시도와 함께 청크 번역
        
        Args:
            chunk_text: 번역할 텍스트
            max_split_attempts: 최대 분할 시도 횟수
            min_chunk_size: 최소 청크 크기
            timeout: 타임아웃 시간(초)
            
        Returns:
            번역된 텍스트
            
        Raises:
            asyncio.TimeoutError: 타임아웃 초과
            BtgTranslationException: 번역 실패
        """
        try:
            loop = asyncio.get_event_loop()
            
            def _sync_translate_with_retry():
                return self.translate_text_with_content_safety_retry(
                    chunk_text,
                    max_split_attempts,
                    min_chunk_size
                )
            
            if timeout:
                result = await asyncio.wait_for(
                    loop.run_in_executor(None, _sync_translate_with_retry),
                    timeout=timeout
                )
            else:
                result = await loop.run_in_executor(None, _sync_translate_with_retry)
            
            return result
        except asyncio.TimeoutError:
            logger.error(f"비동기 번역(재시도) 타임아웃 ({timeout}초)")
            raise
        except asyncio.CancelledError:
            logger.info("비동기 번역(재시도)이 취소됨")
            raise
        except Exception as e:
            logger.error(f"비동기 번역(재시도) 중 오류: {type(e).__name__} - {e}", exc_info=True)
            if isinstance(e, BtgTranslationException):
                raise
            raise BtgTranslationException(f"비동기 번역(재시도) 중 오류: {e}", original_exception=e) from e

if __name__ == '__main__':
    # MockGeminiClient에서 types를 사용하므로, 이 블록 내에서 임포트합니다.
    from google.genai import types as genai_types # Ensure types is imported for hints

    print("--- TranslationService 테스트 ---")
    class MockGeminiClient(GeminiClient):
        def __init__(self, auth_credentials, project=None, location=None, requests_per_minute: Optional[int] = None):
            try:
                super().__init__(auth_credentials=auth_credentials, project=project, location=location, requests_per_minute=requests_per_minute)
            except Exception as e:
                print(f"Warning: MockGeminiClient super().__init__ failed: {e}. This might be okay for some mock scenarios.")
                # If super init fails (e.g. dummy API key validation),
                # the mock might still function if it overrides all necessary methods
                # and doesn't rely on base class state initialized by __init__.
                # For Pylance, inheritance is the main fix.

            self.mock_auth_credentials = auth_credentials
            self.current_model_name_for_test: Optional[str] = None
            self.mock_api_keys_list: List[str] = []
            self.mock_current_api_key: Optional[str] = None

            if isinstance(auth_credentials, list):
                self.mock_api_keys_list = auth_credentials
                if self.mock_api_keys_list: self.mock_current_api_key = self.mock_api_keys_list[0]
            elif isinstance(auth_credentials, str) and not auth_credentials.startswith('{'): # Assuming API key string
                self.mock_api_keys_list = [auth_credentials]
                self.mock_current_api_key = auth_credentials
            print(f"MockGeminiClient initialized. Mock API Keys: {self.mock_api_keys_list}, Mock Current Key: {self.mock_current_api_key}")

        def generate_text(
            self,
            prompt: Union[str, List[Union[str, genai_types.Part]]],
            model_name: str,
            generation_config_dict: Optional[Dict[str, Any]] = None,
            safety_settings_list_of_dicts: Optional[List[Dict[str, Any]]] = None,
            system_instruction_text: Optional[str] = None,
            max_retries: int = 5,
            initial_backoff: float = 2.0,
            max_backoff: float = 60.0,
            stream: bool = False
        ) -> Optional[Union[str, Any]]:
            self.current_model_name_for_test = model_name

            prompt_text_for_mock = ""
            if isinstance(prompt, str):
                prompt_text_for_mock = prompt
            elif isinstance(prompt, list):
                temp_parts = []
                for item in prompt:
                    if isinstance(item, str):
                        temp_parts.append(item)
                    elif hasattr(item, 'text'): # Duck typing for Part-like objects
                        temp_parts.append(item.text)
                    else:
                        temp_parts.append(str(item))
                prompt_text_for_mock = "".join(temp_parts)

            print(f"  MockGeminiClient.generate_text 호출됨 (모델: {model_name}). Mock 현재 키: {self.mock_current_api_key[:5] if self.mock_current_api_key else 'N/A'}")

            if "안전 문제" in prompt_text_for_mock:
                raise GeminiContentSafetyException("Mock 콘텐츠 안전 문제")
            if "사용량 제한" in prompt_text_for_mock: # Simplified logic for mock
                raise GeminiRateLimitException("Mock API 사용량 제한")
            if "잘못된 요청" in prompt_text_for_mock:
                raise GeminiInvalidRequestException("Mock 잘못된 요청")

            text_to_be_translated = prompt_text_for_mock
            if "번역할 텍스트:\n" in prompt_text_for_mock:
                text_to_be_translated = prompt_text_for_mock.split("번역할 텍스트:\n")[-1].strip()
            elif "Translate to Korean:" in prompt_text_for_mock:
                 text_to_be_translated = prompt_text_for_mock.split("Translate to Korean:")[-1].strip()

            mock_translation = f"[번역됨] {text_to_be_translated[:50]}..."

            is_json_response_expected = generation_config_dict and \
                                        generation_config_dict.get("response_mime_type") == "application/json"

            if is_json_response_expected:
                return {"translated_text": mock_translation, "mock_json": True}
            else:
                return mock_translation

        def list_models(self) -> List[Dict[str, Any]]:
            print("  MockGeminiClient.list_models 호출됨")
            # Return a structure similar to what GeminiClient.list_models would return
            return [
                {"name": "models/mock-gemini-flash", "short_name": "mock-gemini-flash", "display_name": "Mock Gemini Flash", "description": "A mock flash model.", "input_token_limit": 1000, "output_token_limit": 1000},
                {"name": "models/mock-gemini-pro", "short_name": "mock-gemini-pro", "display_name": "Mock Gemini Pro", "description": "A mock pro model.", "input_token_limit": 2000, "output_token_limit": 2000},
            ]

    sample_config_base = {
        "model_name": "gemini-1.5-flash", "temperature": 0.7, "top_p": 0.9,
        "prompts": "다음 텍스트를 한국어로 번역해주세요. 용어집 컨텍스트: {{glossary_context}}\n\n번역할 텍스트:\n{{slot}}",
        "enable_dynamic_glossary_injection": True, # 테스트를 위해 활성화
        "glossary_json_path": "test_glossary.json", # 통합된 용어집 경로
        "max_glossary_entries_per_chunk_injection": 3,
        "max_glossary_chars_per_chunk_injection": 200,
    }

    # 1. 용어집 주입 테스트
    print("\n--- 1. 용어집 주입 번역 테스트 ---")
    config1 = sample_config_base.copy()
    
    test_glossary_data = [
    {"keyword": "Alice", "translated_keyword": "앨리스", "target_language": "ko", "occurrence_count": 10},
    {"keyword": "Bob", "translated_keyword": "밥", "target_language": "ko", "occurrence_count": 8}
]
    from infrastructure.file_handler import write_json_file, delete_file
    test_glossary_file = Path(config1["glossary_json_path"]) # Use path from config
    if test_glossary_file.exists(): delete_file(test_glossary_file)
    write_json_file(test_glossary_file, test_glossary_data)

    gemini_client_instance = MockGeminiClient(auth_credentials="dummy_api_key")
    translation_service1 = TranslationService(gemini_client_instance, config1)
    text_to_translate1 = "Hello Alice, how are you Bob?"
    try:
        translated1 = translation_service1.translate_text(text_to_translate1)
        print(f"  원본: {text_to_translate1}")
        print(f"  번역 결과: {translated1}")
    except Exception as e:
        print(f"  테스트 1 오류: {e}")
    finally:
        if test_glossary_file.exists(): delete_file(test_glossary_file)

    # 2. 로어북 비활성화 테스트
    print("\n--- 2. 로어북 비활성화 테스트 ---")
    config2 = sample_config_base.copy()
    config2["enable_dynamic_glossary_injection"] = False
    translation_service2 = TranslationService(gemini_client_instance, config2)
    text_to_translate2 = "This is a test sentence."
    try:
        translated2 = translation_service2.translate_text(text_to_translate2)
        print(f"원본: {text_to_translate2}")
        print(f"번역 결과: {translated2}")
    except Exception as e:
        print(f"테스트 2 오류: {e}")

    # 3. 콘텐츠 안전 문제 테스트
    print("\n--- 3. 콘텐츠 안전 문제 테스트 ---")
    config3 = sample_config_base.copy()
    translation_service3 = TranslationService(gemini_client_instance, config3)
    text_unsafe = "안전 문제 테스트용 텍스트"
    try:
        translation_service3.translate_text(text_unsafe)
    except BtgTranslationException as e:
        print(f"예상된 예외 발생 (콘텐츠 안전): {e}")
    except Exception as e:
        print(f"테스트 3 오류: {type(e).__name__} - {e}")

    print("\n--- TranslationService 테스트 종료 ---")

    # ============================================================================
    # 비동기 메서드 (Phase 2: asyncio 마이그레이션)
    # ============================================================================

    async def translate_text_async(self, text_chunk: str, stream: bool = False) -> str:
        """
        비동기 텍스트 번역 메서드 (translate_text의 비동기 버전)
        
        Args:
            text_chunk: 번역할 텍스트
            stream: 스트리밍 여부
            
        Returns:
            번역된 텍스트
            
        Raises:
            asyncio.CancelledError: 작업이 취소된 경우
            BtgTranslationException: 번역 실패
        """
        if not text_chunk.strip():
            logger.debug("translate_text_async: 입력 텍스트가 비어 있어 빈 문자열 반환.")
            return ""
        
        # 📍 중단 체크: 작업 시작 전 (asyncio.CancelledError 발생)
        if self.stop_check_callback and self.stop_check_callback():
            logger.info("translate_text_async: 중단 요청 감지됨 (작업 시작 전)")
            raise asyncio.CancelledError("번역 중단 요청됨")
        
        text_preview = text_chunk[:100].replace('\n', ' ')
        logger.info(f"비동기 번역 요청: \"{text_preview}{'...' if len(text_chunk) > 100 else ''}\"")
        
        # 용어집 및 프롬프트 준비 (동기 메서드와 동일)
        glossary_context_str = "용어집 컨텍스트 없음"
        
        if self.config.get("enable_dynamic_glossary_injection", False) and self.glossary_entries_for_injection:
            logger.info("용어집 컨텍스트 주입 활성화됨 (청크 내 관련 키워드 체크).")
            chunk_text_lower = text_chunk.lower()
            final_target_lang = self.config.get("target_translation_language", "ko").lower()
            relevant_entries = []
            
            for entry in self.glossary_entries_for_injection:
                if entry.target_language.lower() == final_target_lang and entry.keyword.lower() in chunk_text_lower:
                    relevant_entries.append(entry)
            
            max_entries = self.config.get("max_glossary_entries_per_chunk_injection", 3)
            max_chars = self.config.get("max_glossary_chars_per_chunk_injection", 500)
            glossary_context_str = _format_glossary_for_prompt(relevant_entries, max_entries, max_chars)
            
            if relevant_entries:
                logger.info(f"API 요청에 주입할 용어집 컨텍스트 생성됨. 내용 일부: {glossary_context_str[:100]}...")
        
        replacements = {
            "{{slot}}": text_chunk,
            "{{glossary_context}}": glossary_context_str
        }

        api_prompt_for_gemini_client: List[genai_types.Content] = []
        api_system_instruction: Optional[str] = None

        if self.config.get("enable_prefill_translation", False):
            logger.info("프리필 번역 모드 활성화됨 (Slot Injection 체크).")
            api_system_instruction = self.config.get("prefill_system_instruction", "")
            prefill_cached_history_raw = self.config.get("prefill_cached_history", [])
            base_history: List[genai_types.Content] = []
            
            if isinstance(prefill_cached_history_raw, list):
                for item in prefill_cached_history_raw:
                    if isinstance(item, dict) and "role" in item and "parts" in item:
                        sdk_parts = []
                        for part_item in item.get("parts", []):
                            if isinstance(part_item, str):
                                sdk_parts.append(genai_types.Part.from_text(text=part_item))
                        if sdk_parts:
                            base_history.append(genai_types.Content(role=item["role"], parts=sdk_parts))

            injected_history, injected = _inject_slots_into_history(base_history, replacements)

            if injected:
                logger.info("히스토리 내부에서 '{{slot}}'이 감지되어 원문을 주입했습니다 (Jailbreak 모드).")
                api_prompt_for_gemini_client = injected_history
                if api_prompt_for_gemini_client and api_prompt_for_gemini_client[-1].role == "model":
                    api_prompt_for_gemini_client.append(
                        genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=" ")])
                    )
            else:
                api_prompt_for_gemini_client = injected_history
                user_prompt_str = self._construct_prompt(text_chunk)
                api_prompt_for_gemini_client.append(
                    genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=user_prompt_str)])
                )
        else:
            user_prompt_str = self._construct_prompt(text_chunk)
            api_prompt_for_gemini_client = [
                genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=user_prompt_str)])
            ]

        try:
            translated_text_from_api = await self.gemini_client.generate_text_async(
                prompt=api_prompt_for_gemini_client,
                model_name=self.config.get("model_name", "gemini-2.0-flash"),
                generation_config_dict={
                    "temperature": self.config.get("temperature", 0.7),
                    "top_p": self.config.get("top_p", 0.9),
                    "thinking_level": self.config.get("thinking_level", "high")
                },
                thinking_budget=self.config.get("thinking_budget", None),
                system_instruction_text=api_system_instruction,
                stream=stream
            )

            if translated_text_from_api is None:
                raise GeminiContentSafetyException("API로부터 응답을 받지 못했습니다 (None 반환).")

            if not translated_text_from_api.strip() and text_chunk.strip():
                raise GeminiContentSafetyException("API가 비어있지 않은 입력에 대해 빈 번역 결과를 반환했습니다.")

            return translated_text_from_api.strip()

        except asyncio.CancelledError:
            logger.info("비동기 번역이 취소되었습니다")
            raise
        except GeminiContentSafetyException as e_safety:
            raise BtgTranslationException(f"콘텐츠 안전 문제로 번역할 수 없습니다. ({e_safety})", original_exception=e_safety) from e_safety
        except GeminiAllApiKeysExhaustedException as e_keys:
            raise BtgApiClientException(f"모든 API 키를 사용했으나 요청에 실패했습니다. ({e_keys})", original_exception=e_keys) from e_keys
        except GeminiRateLimitException as e_rate:
            raise BtgApiClientException(f"API 사용량 제한을 초과했습니다. ({e_rate})", original_exception=e_rate) from e_rate
        except GeminiInvalidRequestException as e_invalid:
            raise BtgApiClientException(f"잘못된 API 요청입니다: {e_invalid}", original_exception=e_invalid) from e_invalid
        except GeminiApiException as e_api:
            raise BtgApiClientException(f"API 호출 중 오류가 발생했습니다: {e_api}", original_exception=e_api) from e_api
        except Exception as e:
            raise BtgTranslationException(f"번역 중 알 수 없는 오류가 발생했습니다: {e}", original_exception=e) from e

    async def translate_text_with_content_safety_retry_async(
        self, 
        text_chunk: str, 
        max_split_attempts: int = 3,
        min_chunk_size: int = 100
    ) -> str:
        """
        비동기 버전: 콘텐츠 안전 오류 발생시 청크를 분할하여 재시도하는 번역 메서드
        
        Args:
            text_chunk: 번역할 텍스트
            max_split_attempts: 최대 분할 시도 횟수
            min_chunk_size: 최소 청크 크기
            
        Returns:
            번역된 텍스트 (실패한 부분은 오류 메시지로 대체)
        """
        try:
            return await self.translate_text_async(text_chunk)
        except BtgTranslationException as e:
            if not ("콘텐츠 안전 문제" in str(e)):
                raise e
            
            logger.warning(f"콘텐츠 안전 문제 감지. 비동기 청크 분할 재시도 시작: {str(e)}")
            return await self._translate_with_recursive_splitting_async(
                text_chunk, max_split_attempts, min_chunk_size, current_attempt=1
            )

    async def _translate_with_recursive_splitting_async(
        self,
        text_chunk: str,
        max_split_attempts: int,
        min_chunk_size: int,
        current_attempt: int = 1
    ) -> str:
        if current_attempt > max_split_attempts:
            logger.error(f"최대 분할 시도 횟수({max_split_attempts})에 도달. 번역 실패.")
            return f"[번역 오류로 인한 실패: 최대 분할 시도 초과]"

        if len(text_chunk.strip()) <= min_chunk_size:
            logger.warning(f"최소 청크 크기에 도달했지만 여전히 오류 발생: {text_chunk[:50]}...")
            return f"[번역 오류로 인한 실패: {text_chunk[:30]}...]"

        logger.info(f"📊 청크 분할 시도 #{current_attempt} (깊이: {current_attempt-1})")
        logger.info(f"   📏 원본 크기: {len(text_chunk)} 글자")
        logger.info(f"   🎯 목표: 정확히 2개 청크로 분할 (이진 분할)")

        # Strict 이진 분할 (정확히 2개 청크)
        sub_chunks = self.chunk_service.split_chunk_into_two_halves(
            text_chunk,
            target_size=len(text_chunk) // 2,
            min_chunk_ratio=0.3  # 마지막 청크가 30% 미만이면 병합
        )
        
        if len(sub_chunks) <= 1:
            sub_chunks = self.chunk_service.split_chunk_by_sentences(
                text_chunk, max_sentences_per_chunk=1
            )
        
        if len(sub_chunks) <= 1:
            logger.error("청크 분할 실패. 번역 포기.")
            return f"[분할 불가능한 오류 발생 콘텐츠: {text_chunk[:30]}...]"
        
        logger.info(f"   🔄 {len(sub_chunks)}개 서브 청크를 병렬 처리합니다 (비동기).")
        
        # 비동기 작업 래퍼 함수
        async def translate_sub_chunk_with_check(sub_chunk: str, idx: int) -> tuple[int, str]:
            """개별 서브 청크 번역 (취소 확인 포함)"""
            # 📍 취소 확인 1: 작업 시작 전
            if self.stop_check_callback and self.stop_check_callback():
                raise asyncio.CancelledError(f"서브 청크 {idx+1} 번역 중단 요청됨 (작업 시작 전)")
            
            if not sub_chunk.strip():
                logger.warning(f"   ⚠️ 서브 청크 {idx+1}/{len(sub_chunks)} 빈 청크 감지. 스킵.")
                return (idx, "")
            
            try:
                # 📍 취소 확인 2: API 호출 직전
                if self.stop_check_callback and self.stop_check_callback():
                    raise asyncio.CancelledError(f"서브 청크 {idx+1} 번역 중단 요청됨 (API 호출 직전)")
                
                translated = await self.translate_text_async(sub_chunk)
                logger.info(f"   ✅ 서브 청크 {idx+1}/{len(sub_chunks)} 번역 완료")
                return (idx, translated)
                
            except asyncio.CancelledError:
                logger.info(f"   🛑 서브 청크 {idx+1} 취소됨")
                raise
            except BtgTranslationException as e_sub:
                if "콘텐츠 안전 문제" in str(e_sub) and current_attempt < max_split_attempts:
                    logger.warning(f"   🛡️ 서브 청크 {idx+1} 콘텐츠 안전 오류. 재귀 분할 시도.")
                    recursive_result = await self._translate_with_recursive_splitting_async(
                        sub_chunk, max_split_attempts, min_chunk_size, current_attempt + 1
                    )
                    return (idx, recursive_result)
                else:
                    error_marker = f"[서브 청크 {idx+1} 번역 실패: {str(e_sub)[:50]}]"
                    logger.error(f"   ❌ 서브 청크 {idx+1} 번역 실패: {str(e_sub)[:100]}")
                    return (idx, error_marker)
            except Exception as e_general:
                logger.error(f"   ❌ 서브 청크 {idx+1} 예상치 못한 오류: {e_general}")
                return (idx, f"[서브 청크 {idx+1} 번역 오류]")
        
        # 작업 생성 (순차적으로 취소 확인하며 생성)
        tasks = []
        for i, sub_chunk in enumerate(sub_chunks):
            # 📍 취소 확인: 작업 생성 전
            if self.stop_check_callback and self.stop_check_callback():
                logger.warning(f"중단 요청 감지됨. {i}/{len(sub_chunks)}개 서브 청크 작업 생성 중 중단.")
                break
            
            task = asyncio.create_task(translate_sub_chunk_with_check(sub_chunk, i))
            tasks.append(task)
        
        # 생성된 작업들을 병렬 처리
        results = []
        for task in tasks:
            try:
                idx, translated = await task
                results.append((idx, translated))
            except asyncio.CancelledError:
                logger.info("서브 청크 번역 취소됨. 나머지 작업 취소 중...")
                # 나머지 작업들도 취소
                for remaining_task in tasks:
                    if not remaining_task.done():
                        remaining_task.cancel()
                raise BtgTranslationException("서브 청크 번역이 취소되었습니다.")
        
        # 결과를 원래 순서대로 정렬하여 결합
        results.sort(key=lambda x: x[0])
        translated_parts = [text for _, text in results]
        
        logger.info(f"   📊 병렬 처리 완료: {len(results)}/{len(sub_chunks)}개 서브 청크 처리됨")
        
        return "\n\n".join(translated_parts)

