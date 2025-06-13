# translation_service.py
import time
import random
import re
import csv
from pathlib import Path
from typing import Dict, Any, Optional, List, Union
import os

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
    from core.exceptions import BtgTranslationException, BtgApiClientException, BtgInvalidTranslationLengthException
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
    from core.exceptions import BtgTranslationException, BtgApiClientException, BtgInvalidTranslationLengthException  # type: ignore
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

class TranslationService:
    def __init__(self, gemini_client: GeminiClient, config: Dict[str, Any]):
        self.gemini_client = gemini_client
        self.config = config
        self.chunk_service = ChunkService()
        self.glossary_entries_for_injection: List[GlossaryEntryDTO] = [] # Renamed and type changed

        if self.config.get("enable_dynamic_glossary_injection", False): # Key changed
            self._load_glossary_data() # 함수명 변경
            logger.info("동적 용어집 주입 활성화됨. 용어집 데이터 로드 시도.") # 메시지 변경
        else:
            logger.info("동적 용어집 주입 비활성화됨. 용어집 컨텍스트 없이 번역합니다.") # 메시지 변경

    def _load_glossary_data(self): # 함수명 변경
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
                                if all([entry.keyword, entry.translated_keyword, entry.source_language, entry.target_language]): # 필수 필드 확인
                                    self.glossary_entries_for_injection.append(entry)
                                else:
                                    logger.warning(f"경량 용어집 항목에 필수 필드 누락: {item_dict}")
                            except (TypeError, ValueError) as e_dto:
                                logger.warning(f"용어집 항목 DTO 변환 중 오류: {item_dict}, 오류: {e_dto}") # 메시지 변경
                        else:
                            logger.warning(f"잘못된 용어집 항목 형식 (딕셔너리가 아니거나 필수 키 'keyword' 또는 'description_ko' 누락) 건너뜀: {item_dict}") # 메시지 변경
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
            if not formatted_glossary_context.startswith("용어집 컨텍스트 없음"): # Check simplified
                logger.info(f"API 요청에 동적 용어집 컨텍스트 주입됨. 내용 일부: {formatted_glossary_context[:100]}...") # 메시지 변경
                # 주입된 용어집 키워드 로깅
                # 상세 로깅은 _format_glossary_for_prompt 내부 또는 호출부에서 처리 가능
            else:
                logger.debug(f"동적 용어집 주입 시도했으나, 관련 항목 없거나 제한으로 인해 실제 주입 내용 없음. 사용된 메시지: {formatted_glossary_context}")
            final_prompt = final_prompt.replace("{{glossary_context}}", formatted_glossary_context) # Placeholder changed
        else:
            if "{{glossary_context}}" in final_prompt: # Placeholder changed
                 final_prompt = final_prompt.replace("{{glossary_context}}", "용어집 컨텍스트 없음 (주입 비활성화 또는 해당 항목 없음)") # Placeholder changed
                 logger.debug("동적 용어집 주입 비활성화 또는 플레이스홀더 부재로 '컨텍스트 없음' 메시지 사용.")
        
        # 3. Main content slot - This should be done *after* all other placeholders are processed.
        final_prompt = final_prompt.replace("{{slot}}", chunk_text)
        
        return final_prompt

    def _validate_translation_length(self, original_text: str, translated_text: str):
        """
        번역된 텍스트의 길이가 원본과 비교하여 적절한지 검사합니다.
        지나치게 짧거나 길 경우 BtgInvalidTranslationLengthException을 발생시킵니다.
        """
        original_len = len(original_text.strip())
        translated_len = len(translated_text.strip())

        if original_len == 0 and translated_len > 0:
            logger.warning(f"원본 텍스트는 비어있으나 번역 결과는 내용이 있습니다. 원본 길이: {original_len}, 번역 길이: {translated_len}")
            return

        if original_len > 0 and translated_len == 0:
            # 이 경우는 translate_text 메서드에서 이미 GeminiContentSafetyException 등으로 처리될 수 있음
            logger.warning(f"원본 텍스트는 내용이 있으나 번역 결과가 비어있습니다. 원본 길이: {original_len}, 번역 길이: {translated_len}")
            # translate_text에서 이미 예외 처리되므로 여기서는 별도 예외 발생 안 함 (또는 다른 예외로 래핑 가능)
            return

        if original_len == 0 and translated_len == 0:
            return # 둘 다 비어있으면 정상

        min_length_ratio = self.config.get("translation_min_length_ratio", 0.15)
        max_length_ratio = self.config.get("translation_max_length_ratio", 2.5)

        ratio = translated_len / original_len

        if ratio < min_length_ratio:
            message = (
                f"번역된 텍스트의 길이가 원본에 비해 너무 짧습니다. "
                f"원본 길이: {original_len}, 번역 길이: {translated_len} (비율: {ratio:.2f}, 최소 허용 비율: {min_length_ratio}). "
                f"원본 미리보기: '{original_text[:50]}...', 번역 미리보기: '{translated_text[:50]}...'"
            )
            logger.error(message)
            raise BtgInvalidTranslationLengthException(message)

        if ratio > max_length_ratio:
            message = (
                f"번역된 텍스트의 길이가 원본에 비해 너무 깁니다. "
                f"원본 길이: {original_len}, 번역 길이: {translated_len} (비율: {ratio:.2f}, 최대 허용 비율: {max_length_ratio}). "
                f"원본 미리보기: '{original_text[:50]}...', 번역 미리보기: '{translated_text[:50]}...'"
            )
            logger.error(message)
            raise BtgInvalidTranslationLengthException(message)

        logger.debug(f"번역 길이 검증 통과: 원본 길이 {original_len}, 번역 길이 {translated_len} (비율: {ratio:.2f})")

    def translate_text(self, text_chunk: str, stream: bool = False) -> str:
        """
        주어진 텍스트 청크를 번역합니다.
        프리필 모드 사용 시 prefill_system_instruction과 prefill_cached_history를 사용합니다.
        chat_prompt (user prompt)는 _construct_prompt를 통해 구성됩니다.
        """
        if not text_chunk.strip():
            logger.debug("Translate_text: 입력 텍스트가 비어 있어 빈 문자열 반환.")
            return ""
        
        api_prompt_for_gemini_client: Union[str, List[genai_types.Content]] # 변경: List[Content] 사용
        api_system_instruction: Optional[str] # 변경: Optional[str]

        if self.config.get("enable_prefill_translation", False):
            logger.info("프리필 번역 모드 활성화됨.")
            api_system_instruction = self.config.get("prefill_system_instruction", "")
            prefill_cached_history_raw = self.config.get("prefill_cached_history", [])

            # prefill_cached_history_raw가 올바른 형식인지 확인 (리스트이며, 각 항목이 딕셔너리인지)
            if not isinstance(prefill_cached_history_raw, list):
                logger.warning(f"잘못된 prefill_cached_history 형식 ({type(prefill_cached_history_raw)}). 빈 리스트로 대체합니다.")
                prefill_cached_history = []
            else:
                prefill_cached_history = []
                for item in prefill_cached_history_raw:
                    if isinstance(item, dict) and "role" in item and "parts" in item:
                        raw_parts = item.get("parts")
                        # parts 내부의 문자열들을 Part 객체로 변환
                        sdk_parts = []
                        if isinstance(raw_parts, list):
                            for part_item in raw_parts:
                                if isinstance(part_item, str):
                                    sdk_parts.append(genai_types.Part.from_text(text=part_item)) # 명시적으로 text= 사용
                                elif isinstance(part_item, genai_types.Part): # 이미 Part 객체인 경우
                                    sdk_parts.append(part_item)
                        if sdk_parts: # 유효한 part가 있는 경우에만 추가
                            prefill_cached_history.append(genai_types.Content(role=item["role"], parts=sdk_parts))
                    else:
                        logger.warning(f"잘못된 prefill_cached_history 항목 건너뜀: {item}")
            
            # 현재 청크에 대한 사용자 프롬프트 (기존 _construct_prompt 결과)
            current_chunk_user_prompt_str = self._construct_prompt(text_chunk)
            
            # API에 전달할 contents 구성: List[Content] 형태
            api_prompt_for_gemini_client = list(prefill_cached_history) # 복사해서 사용
            api_prompt_for_gemini_client.append(
                genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=str(current_chunk_user_prompt_str))]) # 명시적으로 text= 사용 및 str() 변환
            )
            logger.debug(f"프리필 모드: 시스템 지침='{api_system_instruction[:50]}...', contents 개수={len(api_prompt_for_gemini_client)}")

        else:
            logger.info("표준 번역 모드 활성화됨.")
            api_system_instruction = None # 프리필 비활성화 시 시스템 지침 없음
            api_prompt_for_gemini_client = self._construct_prompt(text_chunk) # 문자열
            logger.debug(f"표준 모드: 시스템 지침 없음, 프롬프트 길이={len(api_prompt_for_gemini_client)}")

        try:
            logger.debug(f"Gemini API 호출 시작. 모델: {self.config.get('model_name')}")
            
            translated_text_from_api = self.gemini_client.generate_text( # Renamed variable
                prompt=api_prompt_for_gemini_client, # Union[str, List[Dict]]
                model_name=self.config.get("model_name", "gemini-2.0-flash"),
                generation_config_dict={
                    "temperature": self.config.get("temperature", 0.7),
                    "top_p": self.config.get("top_p", 0.9)
                },
                system_instruction_text=api_system_instruction, # Optional[str] 전달
                stream=stream 
            )

            if translated_text_from_api is None:
                logger.error("GeminiClient.generate_text가 None을 반환했습니다.")
                raise GeminiContentSafetyException("API로부터 응답을 받지 못했습니다 (None 반환).")

            if not translated_text_from_api.strip() and text_chunk.strip():
                logger.warning(f"API가 비어있지 않은 입력에 대해 빈 문자열을 반환했습니다. 원본: '{text_chunk[:100]}...'")
                raise GeminiContentSafetyException("API가 비어있지 않은 입력에 대해 빈 번역 결과를 반환했습니다.")

            logger.debug(f"Gemini API 호출 성공. 번역된 텍스트 (일부): {translated_text_from_api[:100]}...")
            
            # 번역 후 길이 검증
            self._validate_translation_length(text_chunk, translated_text_from_api)
            
            # 문장부호 일관성 검사 로직 제거
        
        except GeminiContentSafetyException as e_safety:
            logger.warning(f"콘텐츠 안전 문제로 번역 실패: {e_safety}")
            # 콘텐츠 안전 문제 발생 시, 분할 재시도 로직을 호출하도록 변경
            # translate_text_with_content_safety_retry가 이 예외를 처리하도록 함
            raise BtgTranslationException(f"콘텐츠 안전 문제로 번역할 수 없습니다. ({e_safety})", original_exception=e_safety) from e_safety
        except BtgInvalidTranslationLengthException: # 새로 추가된 예외 처리
            raise # 이미 로깅되었으므로 그대로 다시 발생시켜 상위에서 처리하도록 함
        except GeminiAllApiKeysExhaustedException as e_keys:
            logger.error(f"API 키 회전 실패: 모든 API 키 소진 또는 유효하지 않음. 원본 오류: {e_keys}")
            raise BtgApiClientException(f"모든 API 키를 사용했으나 요청에 실패했습니다. API 키 설정을 확인하세요. ({e_keys})", original_exception=e_keys) from e_keys
        except GeminiRateLimitException as e_rate:
            logger.error(f"API 사용량 제한 초과 (키 회전 후에도 발생): {e_rate}")
            raise BtgApiClientException(f"API 사용량 제한을 초과했습니다. 잠시 후 다시 시도해주세요. ({e_rate})", original_exception=e_rate) from e_rate
        except GeminiInvalidRequestException as e_invalid:
            logger.error(f"잘못된 API 요청: {e_invalid}")
            raise BtgApiClientException(f"잘못된 API 요청입니다: {e_invalid}", original_exception=e_invalid) from e_invalid
        except GeminiApiException as e_api: # Catches other general API errors from GeminiClient
            logger.error(f"Gemini API 호출 중 일반 오류 발생: {e_api}")
            raise BtgApiClientException(f"API 호출 중 오류가 발생했습니다: {e_api}", original_exception=e_api) from e_api
        # BtgTranslationException for empty string is now caught here if raised from above
        except BtgTranslationException: # Re-raise if it's our specific empty string exception
            raise
        except Exception as e: # Catch-all for other unexpected errors
            logger.error(f"번역 중 예상치 못한 오류 발생: {e}", exc_info=True)
            raise BtgTranslationException(f"번역 중 알 수 없는 오류가 발생했습니다: {e}", original_exception=e) from e
        
        return translated_text_from_api.strip() # Return the stripped translated text
    
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
            # 콘텐츠 안전 문제 또는 문장부호 불일치 문제가 아닌 경우, 그대로 예외 발생
            # BtgInvalidTranslationLengthException도 재시도 대상에 포함
            if not ("콘텐츠 안전 문제" in str(e) or \
                    isinstance(e, BtgInvalidTranslationLengthException)):
                raise e
            
            error_type_for_log = "콘텐츠 안전 문제" if "콘텐츠 안전 문제" in str(e) else "번역 길이 문제"
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
        logger.info(f"   🎯 목표 크기: {len(text_chunk) // 2} 글자")
        logger.info(f"   📝 내용 미리보기: {text_chunk[:100].replace(chr(10), ' ')}...")

        
        # 1단계: 크기 기반 분할
        sub_chunks = self.chunk_service.split_chunk_recursively(
            text_chunk,
            target_size=len(text_chunk) // 2,
            min_chunk_size=min_chunk_size,
            max_split_depth=1,  # 1단계만 분할
            current_depth=0
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
            sub_chunk_info = f"서브 청크 {i+1}/{total_sub_chunks}"
            sub_chunk_size = len(sub_chunk.strip())
            sub_chunk_preview = sub_chunk.strip()[:50].replace('\n', ' ') + '...'
            
            logger.info(f"   🚀 {sub_chunk_info} 번역 시작")
            logger.debug(f"      📏 크기: {sub_chunk_size} 글자")
            logger.debug(f"      📝 내용: {sub_chunk_preview}")
            
            start_time = time.time()
            
            try:
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
                
                # 콘텐츠 안전 문제 또는 문장부호 불일치 문제인 경우 재귀 시도
                if "콘텐츠 안전 문제" in str(sub_e) or \
                   isinstance(sub_e, BtgInvalidTranslationLengthException):
                    error_type_for_log_sub = "콘텐츠 안전 문제" if "콘텐츠 안전 문제" in str(sub_e) else "번역 길이 문제"
                    logger.warning(f"   🛡️ {sub_chunk_info} {error_type_for_log_sub} 발생 (소요: {processing_time:.2f}초)") # 문장부호 관련 메시지 제거
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
    
    # 테스트용 용어집 파일 생성
    test_glossary_data = [
        {"keyword": "Alice", "translated_keyword": "앨리스", "source_language": "en", "target_language": "ko", "occurrence_count": 10},
        {"keyword": "Bob", "translated_keyword": "밥", "source_language": "en", "target_language": "ko", "occurrence_count": 8}
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
    config2["enable_dynamic_lorebook_injection"] = False
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
