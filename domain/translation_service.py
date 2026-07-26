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
    from utils.lang_utils import normalize_language_code # Added
    from google.genai import types as genai_types
    from core.dtos import (
        GlossaryEntryDTO,
        TranslationUnit,
        TranslatedUnit,
        EpubNode,
        EpubChapter,
        NodeType,
        TranslationJobProgressDTO
    )
    from utils.epub_processor import EpubProcessor
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
    from utils.lang_utils import normalize_language_code # type: ignore
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
                                # target_language 정규화 로드 시 미리 수행
                                raw_lang = item_dict.get("target_language", "")
                                normalized_lang = normalize_language_code(raw_lang)
                                
                                entry = GlossaryEntryDTO( # Explicitly use GlossaryEntryDTO
                                    keyword=item_dict.get("keyword", ""),
                                    translated_keyword=item_dict.get("translated_keyword", ""),
                                    target_language=normalized_lang, # 정규화된 코드 사용
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

        # [Strict Mode] 필수 플레이스홀더 검증
        if "{{slot}}" not in prompt_template:
            raise BtgTranslationException("번역 프롬프트 템플릿에 필수 플레이스홀더 '{{slot}}'이 누락되었습니다. 작업을 중단합니다.")

        # [Strict Mode] 용어집 주입 활성화 시 플레이스홀더 검증
        if self.config.get("enable_dynamic_glossary_injection", False) and "{{glossary_context}}" not in prompt_template:
            raise BtgTranslationException("동적 용어집 주입이 활성화되었으나, 프롬프트 템플릿에 '{{glossary_context}}' 플레이스홀더가 없습니다. 작업을 중단합니다.")

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
            # final_target_lang 설정 시 정규화 적용
            final_target_lang = normalize_language_code(self.config.get("target_translation_language", "ko"))

            if config_source_lang == "auto":
                # "auto" 모드: 청크의 언어는 LLM이 감지.
                # 용어집 항목의 target_language가 최종 번역 목표 언어와 일치하는 것만 고려.
                # source_language 필터링은 LLM의 문맥 이해에 맡기거나, 여기서 간단한 키워드 매칭만 수행.
                logger.info(f"자동 언어 감지 모드: 용어집은 키워드 일치 및 최종 목표 언어({final_target_lang}) 일치로 필터링 후 LLM에 전달.") # 메시지 변경
                for entry in self.glossary_entries_for_injection:
                    # entry.target_language는 _load_glossary_data에서 이미 정규화됨
                    if entry.target_language == final_target_lang and \
                       entry.keyword.lower() in chunk_text_lower:
                        relevant_entries_for_chunk.append(entry)
            else:
                # 명시적 언어 설정 모드: Python에서 언어 및 키워드 기반으로 필터링.
                logger.info(f"명시적 언어 모드 ('{current_source_lang_for_glossary_filtering}'): 용어집을 출발어/도착어 및 키워드 기준으로 필터링.") # 메시지 변경
                for entry in self.glossary_entries_for_injection:
                    # source_language 필터링 제거. DTO에 해당 필드가 없으므로.
                    if entry.target_language == final_target_lang and \
                       entry.keyword.lower() in chunk_text_lower:
                        relevant_entries_for_chunk.append(entry)
                    # source_language 관련 로깅 제거
                    elif not (entry.target_language == final_target_lang): # target_language 불일치 로깅은 유지
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
        stream: bool = False
    ) -> str:
        """
        비동기 청크 번역 메서드 (진정한 비동기 구현)
        
        Timeout은 GeminiClient의 http_options에 설정되어 모든 API 호출에 자동 적용됩니다.
        
        Args:
            chunk_text: 번역할 텍스트
            stream: 스트리밍 여부
            
        Returns:
            번역된 텍스트
            
        Raises:
            asyncio.CancelledError: 작업 취소됨
            BtgTranslationException: 번역 실패
        """
        # 📍 중단 체크: 작업 시작 전
        if self.stop_check_callback and self.stop_check_callback():
            logger.info("translate_chunk_async: 중단 요청 감지됨 (작업 시작 전)")
            raise asyncio.CancelledError("번역 중단 요청됨")
        
        # ✨ 방어적 체크포인트: asyncio 취소 확인 강제
        await asyncio.sleep(0)
        
        if not chunk_text.strip():
            logger.debug("translate_chunk_async: 입력 텍스트가 비어 있어 빈 문자열 반환.")
            return ""
        
        # 소설 본문 미리보기 로깅
        text_preview = chunk_text[:100].replace('\n', ' ')
        logger.info(f"비동기 청크 번역 요청: \"{text_preview}{'...' if len(chunk_text) > 100 else ''}\"")
        
        try:
            # 콘텐츠 안전 재시도 설정 확인
            use_content_safety_retry = self.config.get("use_content_safety_retry", True)
            max_split_attempts = self.config.get("max_content_safety_split_attempts", 3)
            min_chunk_size = self.config.get("min_content_safety_chunk_size", 100)
            
            # 설정에 따라 재시도 로직 분기
            if use_content_safety_retry:
                result = await self.translate_text_with_content_safety_retry_async(
                    chunk_text, max_split_attempts, min_chunk_size
                )
            else:
                # 재시도 없이 직접 번역 (OFF 설정 시)
                result = await self.translate_text_async(chunk_text)
            
            # 📍 중단 체크: API 응답 후
            if self.stop_check_callback and self.stop_check_callback():
                logger.info("translate_chunk_async: 중단 요청 감지됨 (응답 후)")
                raise asyncio.CancelledError("번역 중단 요청됨")
            
            return result
        except asyncio.CancelledError:
            logger.info("비동기 번역이 취소됨")
            raise
        except BtgApiClientException as e_api:
            if isinstance(e_api.original_exception, GeminiAllApiKeysExhaustedException):
                logger.critical(f"모든 API 키 소진으로 번역 중단: {e_api}")
                raise # Re-raise BtgApiClientException to stop the process
            
            logger.error(f"비동기 번역 중 API 오류: {type(e_api).__name__} - {e_api}", exc_info=True)
            raise BtgTranslationException(f"비동기 번역 중 API 오류: {e_api}", original_exception=e_api) from e_api
        except Exception as e:
            logger.error(f"비동기 번역 중 오류: {type(e).__name__} - {e}", exc_info=True)
            if isinstance(e, BtgTranslationException):
                raise
            raise BtgTranslationException(f"비동기 번역 중 오류: {e}", original_exception=e) from e

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
        
        # ✨ 방어적 체크포인트: asyncio 취소 확인 강제
        await asyncio.sleep(0)
        
        text_preview = text_chunk[:100].replace('\n', ' ')
        logger.info(f"비동기 번역 요청: \"{text_preview}{'...' if len(text_chunk) > 100 else ''}\"")
        
        # 용어집 및 프롬프트 준비 (동기 메서드와 동일)
        glossary_context_str = "용어집 컨텍스트 없음"
        
        if self.config.get("enable_dynamic_glossary_injection", False) and self.glossary_entries_for_injection:
            logger.info("용어집 컨텍스트 주입 활성화됨 (청크 내 관련 키워드 체크).")
            chunk_text_lower = text_chunk.lower()
            # target_language 정규화 적용
            final_target_lang = normalize_language_code(self.config.get("target_translation_language", "ko"))
            relevant_entries = []
            
            for entry in self.glossary_entries_for_injection:
                # entry.target_language는 _load_glossary_data에서 이미 정규화됨
                if entry.target_language == final_target_lang and entry.keyword.lower() in chunk_text_lower:
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

    async def translate_text_force_split_async(
        self, 
        text_chunk: str, 
        max_split_attempts: int = 3,
        min_chunk_size: int = 100,
        split_level: int = 1
    ) -> str:
        """
        강제 분할 번역: Depth 0(전체) 시도를 생략하고 바로 지정된 레벨의 분할부터 시작합니다.
        주로 누락 의심 청크나 실패 청크의 재번역에 사용됩니다.
        """
        logger.info(f"⚡ 강제 분할 번역 시작 (Level {split_level} 진입): {len(text_chunk)} 글자")
        return await self._translate_with_recursive_splitting_async(
            text_chunk, max_split_attempts, min_chunk_size, current_attempt=1, split_level=split_level
        )

    async def _translate_with_recursive_splitting_async(
        self,
        text_chunk: str,
        max_split_attempts: int,
        min_chunk_size: int,
        current_attempt: int = 1,
        split_level: int = 1
    ) -> str:
        if current_attempt > max_split_attempts:
            logger.error(f"최대 분할 시도 횟수({max_split_attempts})에 도달. 번역 실패.")
            return f"[번역 오류로 인한 실패: 최대 분할 시도 초과]"

        if len(text_chunk.strip()) <= min_chunk_size:
            logger.warning(f"최소 청크 크기에 도달했지만 여전히 오류 발생: {text_chunk[:50]}...")
            return f"[번역 오류로 인한 실패: {text_chunk[:30]}...]"

        logger.info(f"📊 청크 분할 시도 #{current_attempt} (깊이: {current_attempt-1})")
        logger.info(f"   📏 원본 크기: {len(text_chunk)} 글자")
        logger.info(f"   🎯 목표: 분할 레벨 {split_level}에 따른 서브 청크 분할")

        if split_level == 3:
            target_size = 1000
            sub_chunks = self.chunk_service.split_text_into_chunks(text_chunk, max_chunk_size=target_size)
        elif split_level == 2:
            target_size = max(min_chunk_size, len(text_chunk) // 4)
            sub_chunks = self.chunk_service.split_text_into_chunks(text_chunk, max_chunk_size=target_size)
        else:
            # 기본 1레벨: Strict 이진 분할 (정확히 2개 청크)
            target_size = max(min_chunk_size, len(text_chunk) // 2)
            sub_chunks = self.chunk_service.split_chunk_into_two_halves(
                text_chunk,
                target_size=target_size,
                min_chunk_ratio=0.3
            )
        
        if len(sub_chunks) <= 1:
            sub_chunks = self.chunk_service.split_chunk_by_sentences(
                text_chunk, max_sentences_per_chunk=1
            )
        
        if len(sub_chunks) <= 1:
            logger.error("청크 분할 실패. 번역 포기.")
            return f"[분할 불가능한 오류 발생 콘텐츠: {text_chunk[:30]}...]"
        
        logger.info(f"   🔄 {len(sub_chunks)}개 서브 청크를 병렬 처리합니다 (비동기).")
        
        # 어플리케이션 전역 설정의 max_workers를 준수하여 동시 요청 수 제어
        max_parallel = self.config.get("max_workers", 3) if self.config else 3
        semaphore = asyncio.Semaphore(max_parallel)
        
        # 비동기 작업 래퍼 함수
        async def translate_sub_chunk_with_check(sub_chunk: str, idx: int) -> tuple[int, str]:
            """개별 서브 청크 번역 (취소 확인 포함)"""
            async with semaphore:
                # 📍 취소 확인 1: 작업 시작 전
                if self.stop_check_callback and self.stop_check_callback():
                    raise asyncio.CancelledError(f"서브 청크 {idx+1} 번역 중단 요청됨 (작업 시작 전)")
                # ✨ 방어적 체크포인트
                await asyncio.sleep(0)
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

    async def translate_text_integrity(
        self, 
        text: str, 
        output_path_for_progress: Optional[Union[str, Path]] = None,
        progress_callback: Optional[Callable[[TranslationJobProgressDTO], None]] = None,
        status_callback: Optional[Callable[[str], None]] = None,
        tqdm_file_stream: Optional[Any] = None
    ) -> str:
        """
        무결성 번역: 줄 단위로 분리하여 JSON 형태로 번역하고 누락을 검사합니다.
        
        Args:
            text: 번역할 전체 텍스트
            output_path_for_progress: 진행 상황 저장을 위한 임시 폴더 생성 기준 경로
            progress_callback: 전체 작업 진행률 콜백
            status_callback: 현재 상세 상태 메시지 콜백
            tqdm_file_stream: tqdm 프로그레스 바 출력을 위한 스트림 객체
            
        Returns:
            번역된 텍스트
        """
        import json
        import shutil

        if not text.strip():
            logger.debug("translate_text_integrity: 입력 텍스트가 비어 있음.")
            return ""

        # 1. 전처리 (Parsing)
        lines = text.splitlines()
        units = []
        for i, line in enumerate(lines):
            # 빈 줄도 컨텍스트 보존을 위해 포함 (단, 번역 대상에서는 제외하거나 마킹 가능)
            units.append(TranslationUnit(id=str(i), text=line))

        # 2. 청크 분할
        max_chunk_size = self.config.get("chunk_size", 6000)
        max_items = self.config.get("integrity_max_items", 200) # 무결성 모드 기본값 200
        chunks = self.chunk_service.split_nodes_into_chunks(units, max_chunk_size, max_items)
        total_chunks = len(chunks)

        translated_map: Dict[str, str] = {}
        
        temp_dir = None
        translated_chunk_indices = set()
        
        if output_path_for_progress:
            out_p = Path(output_path_for_progress)
            temp_dir = out_p.parent / f"{out_p.stem}_integrity_temp"
            temp_dir.mkdir(parents=True, exist_ok=True)
            
            # 기존 저장된 임시 청크 파일들로부터 진행 상태 복원
            if temp_dir.exists():
                for f in temp_dir.glob("chunk_*.json"):
                    try:
                        chunk_idx = int(f.stem.split("_")[1])
                        translated_chunk_indices.add(chunk_idx)
                    except (IndexError, ValueError):
                        pass
                if translated_chunk_indices:
                    logger.info(f"기존 무결성 번역 진행 상태 로드됨: {len(translated_chunk_indices)}개 청크 완료")

        if progress_callback:
            progress_callback(TranslationJobProgressDTO(
                total_chunks=total_chunks,
                processed_chunks=len(translated_chunk_indices),
                successful_chunks=len(translated_chunk_indices),
                failed_chunks=0,
                current_status_message=f"무결성 번역 시작 ({len(translated_chunk_indices)}/{total_chunks} 청크 완료됨)"
            ))

        pbar = None
        if tqdm_file_stream:
            try:
                from tqdm import tqdm
                pbar = tqdm(
                    total=total_chunks,
                    initial=len(translated_chunk_indices),
                    desc="무결성 번역",
                    unit="청크",
                    file=tqdm_file_stream,
                    ncols=100,
                    bar_format='{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]'
                )
            except Exception as pbar_e:
                logger.debug(f"tqdm 초기화 실패: {pbar_e}")

        try:
            for i, chunk in enumerate(chunks):
                # 📍 중단 체크
                if self.stop_check_callback and self.stop_check_callback():
                    raise asyncio.CancelledError(f"무결성 번역 중단 요청됨 (청크 {i+1} 시작 전)")

                status_msg = f"무결성 번역 청크 {i+1}/{total_chunks} 처리 중..."
                if status_callback:
                    status_callback(status_msg)

                logger.info(f"📦 무결성 번역 청크 {i+1}/{total_chunks} 처리 중 (항목: {len(chunk)}개)")
                
                if i in translated_chunk_indices and temp_dir:
                    try:
                        chunk_file = temp_dir / f"chunk_{i}.json"
                        with open(chunk_file, 'r', encoding='utf-8') as f:
                            chunk_results = json.load(f)
                        translated_map.update(chunk_results)
                        logger.info(f"  ⏭️ 이미 번역된 청크 건너뜀")
                        if progress_callback:
                            progress_callback(TranslationJobProgressDTO(
                                total_chunks=total_chunks,
                                processed_chunks=i + 1,
                                successful_chunks=len(translated_chunk_indices),
                                failed_chunks=0,
                                current_status_message=f"무결성 번역 청크 {i+1}/{total_chunks} 건너뜀",
                                current_chunk_processing=i + 1
                            ))
                        continue
                    except Exception as e:
                        logger.warning(f"  ⚠️ 저장된 청크 읽기 실패, 재번역 시도: {e}")

                # 3. API 요청 및 검증 (재시도 포함)
                chunk_results = await self._translate_integrity_chunk_with_retry(chunk)
                translated_map.update(chunk_results)
                
                if temp_dir:
                    try:
                        chunk_file = temp_dir / f"chunk_{i}.json"
                        with open(chunk_file, 'w', encoding='utf-8') as f:
                            json.dump(chunk_results, f, ensure_ascii=False)
                        translated_chunk_indices.add(i)
                    except Exception as e:
                        logger.error(f"  ❌ 무결성 임시 청크 저장 실패: {e}")

                if pbar:
                    pbar.update(1)

                if progress_callback:
                    progress_callback(TranslationJobProgressDTO(
                        total_chunks=total_chunks,
                        processed_chunks=i + 1,
                        successful_chunks=len(translated_chunk_indices),
                        failed_chunks=0,
                        current_status_message=f"무결성 번역 청크 {i+1}/{total_chunks} 완료",
                        current_chunk_processing=i + 1
                    ))
        finally:
            if pbar:
                pbar.close()

        # 4. 조립
        result_lines = []
        for i in range(len(lines)):
            # 번역이 없으면 원문 사용
            result_lines.append(translated_map.get(str(i), lines[i]))
            
        if output_path_for_progress:
            with open(output_path_for_progress, "w", encoding="utf-8") as f:
                f.write("\n".join(result_lines))

        if temp_dir and temp_dir.exists():
            logger.info(f"무결성 검수 캐시 및 진행 상태 파일 보존: {temp_dir}")

        return "\n".join(result_lines)

    async def _translate_integrity_chunk_with_retry(
        self, 
        chunk: List[TranslationUnit], 
        depth: int = 0
    ) -> Dict[str, str]:
        """
        무결성 청크 번역 (Binary Split 및 Targeted Retry 포함)
        """
        if not chunk:
            return {}

        # 📍 중단 체크
        if self.stop_check_callback and self.stop_check_callback():
            raise asyncio.CancelledError("무결성 청크 번역 중단 요청됨")

        try:
            import json
            chunk_json_str = json.dumps([unit.model_dump() for unit in chunk], ensure_ascii=False)

            # 1. 시스템 지침 준비 (사용자 시스템 지침 + JSON 강제 지침)
            sys_instr_base = self.config.get("prefill_system_instruction", "") if self.config.get("enable_prefill_translation", False) else ""
            json_instruction = "You are a professional translator. Respond ONLY with a valid JSON array of objects, each containing 'id' and 'translated_text' keys. Do NOT wrap in markdown code blocks."
            sys_instr = f"{sys_instr_base}\n\n{json_instruction}".strip()

            # 2. 용어집 및 프롬프트 준비
            glossary_context_str = "용어집 컨텍스트 없음"
            if self.config.get("enable_dynamic_glossary_injection", False) and self.glossary_entries_for_injection:
                chunk_text_lower = chunk_json_str.lower()
                final_target_lang = normalize_language_code(self.config.get("target_translation_language", "ko"))
                relevant_entries = [e for e in self.glossary_entries_for_injection if e.target_language == final_target_lang and e.keyword.lower() in chunk_text_lower]
                
                max_entries = self.config.get("max_glossary_entries_per_chunk_injection", 3)
                max_chars = self.config.get("max_glossary_chars_per_chunk_injection", 500)
                glossary_context_str = _format_glossary_for_prompt(relevant_entries, max_entries, max_chars)

            replacements = {
                "{{slot}}": chunk_json_str,
                "{{glossary_context}}": glossary_context_str
            }

            api_prompt_for_gemini_client: List[genai_types.Content] = []
            
            integrity_prompt_suffix = "\n\nTranslate each item in the following JSON array. Keep the 'id' exactly as given. Return ONLY a valid JSON array."

            if self.config.get("enable_prefill_translation", False):
                prefill_cached_history_raw = self.config.get("prefill_cached_history", [])
                base_history: List[genai_types.Content] = []
                
                if isinstance(prefill_cached_history_raw, list):
                    for item in prefill_cached_history_raw:
                        if isinstance(item, dict) and "role" in item and "parts" in item:
                            sdk_parts = [genai_types.Part.from_text(text=p) for p in item.get("parts", []) if isinstance(p, str)]
                            if sdk_parts:
                                base_history.append(genai_types.Content(role=item["role"], parts=sdk_parts))

                injected_history, injected = _inject_slots_into_history(base_history, replacements)

                if injected:
                    api_prompt_for_gemini_client = injected_history
                    # Jailbreak 주입 모드일 경우 마지막에 무결성 지침만 덧붙임
                    if api_prompt_for_gemini_client and api_prompt_for_gemini_client[-1].role == "model":
                        api_prompt_for_gemini_client.append(
                            genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=integrity_prompt_suffix)])
                        )
                    else:
                        # 히스토리의 마지막이 user일 경우 (또는 비어있을 경우)
                        api_prompt_for_gemini_client.append(
                            genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=integrity_prompt_suffix)])
                        )
                else:
                    api_prompt_for_gemini_client = injected_history
                    user_prompt_str = self._construct_prompt(chunk_json_str)
                    if integrity_prompt_suffix not in user_prompt_str:
                        user_prompt_str += integrity_prompt_suffix
                    api_prompt_for_gemini_client.append(
                        genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=user_prompt_str)])
                    )
            else:
                user_prompt_str = self._construct_prompt(chunk_json_str)
                if integrity_prompt_suffix not in user_prompt_str:
                    user_prompt_str += integrity_prompt_suffix
                api_prompt_for_gemini_client = [
                    genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=user_prompt_str)])
                ]

            # 3. API 호출 (Structured Output 모드)
            gen_config = {
                "temperature": self.config.get("temperature", 0.3), # 유저 설정값 우선, 없으면 0.3
                "top_p": self.config.get("top_p", 0.9),
                "response_mime_type": "application/json",
                "thinking_level": self.config.get("thinking_level", "high")
            }

            raw_response = await self.gemini_client.generate_text_async(
                prompt=api_prompt_for_gemini_client,
                model_name=self.config.get("model_name", "gemini-2.0-flash"),
                generation_config_dict=gen_config,
                thinking_budget=self.config.get("thinking_budget", None),
                system_instruction_text=sys_instr
            )

            # 3. 응답 파싱 및 검증
            if not raw_response or not isinstance(raw_response, list):
                # JSON 파싱 실패 또는 빈 응답 -> Binary Split
                logger.warning(f"무결성 번역 JSON 파싱 실패 (depth {depth}). Binary Split 시도.")
                return await self._binary_split_integrity_retry(chunk, depth)

            # 4. 누락 검사 및 Targeted Retry
            translated_units = []
            for item in raw_response:
                try:
                    translated_units.append(TranslatedUnit(**item))
                except Exception:
                    continue
            
            translated_map = {
                u.id: (u.translated_text if u.translated_text else "")
                for u in translated_units
            }
            requested_ids = {u.id for u in chunk}
            received_ids = set(translated_map.keys())
            missing_ids = requested_ids - received_ids

            if missing_ids and depth < self.config.get("max_integrity_retry_depth", 2):
                logger.warning(f"무결성 번역 누락 감지: {len(missing_ids)}개. Targeted Retry 시도.")
                missing_units = [u for u in chunk if u.id in missing_ids]
                additional_results = await self._translate_integrity_chunk_with_retry(missing_units, depth + 1)
                translated_map.update(additional_results)

            return translated_map

        except Exception as e:
            logger.error(f"무결성 청크 번역 중 오류 발생: {e}")
            if depth < self.config.get("max_integrity_retry_depth", 2):
                return await self._binary_split_integrity_retry(chunk, depth)
            else:
                # 최후의 수단: 실패 시 원문 유지
                return {u.id: u.text for u in chunk}

    async def _binary_split_integrity_retry(self, chunk: List[TranslationUnit], depth: int) -> Dict[str, str]:
        """청크를 반으로 나누어 재귀적으로 번역 시도"""
        if len(chunk) <= 1:
            logger.error("더 이상 나눌 수 없는 단일 항목 무결성 번역 실패.")
            return {chunk[0].id: chunk[0].text} if chunk else {}

        mid = len(chunk) // 2
        left_chunk = chunk[:mid]
        right_chunk = chunk[mid:]
        
        logger.info(f"🔄 Binary Split: {len(chunk)} -> {len(left_chunk)}, {len(right_chunk)}")
        
        results = {}
        results.update(await self._translate_integrity_chunk_with_retry(left_chunk, depth + 1))
        results.update(await self._translate_integrity_chunk_with_retry(right_chunk, depth + 1))
        return results

    async def translate_epub(self, epub_path: Union[str, Path], output_path: Union[str, Path]) -> None:
        """
        EPUB 번역 파이프라인: 구조를 유지하며 내용을 번역합니다.
        임시 디렉토리를 사용한 파일 기반 진행도 저장 및 재개(Resume)를 지원합니다.
        """
        import zipfile
        import json
        import shutil
        
        processor = EpubProcessor()
        epub_path = Path(epub_path)
        output_path = Path(output_path)

        logger.info(f"🚀 EPUB 번역 시작: {epub_path.name}")
        
        temp_dir = epub_path.parent / f"{epub_path.stem}_epub_temp"
        metadata_path = temp_dir / "epub_metadata.json"
        
        metadata = {"translated_files": []}
        if temp_dir.exists() and metadata_path.exists():
            try:
                with open(metadata_path, 'r', encoding='utf-8') as f:
                    metadata = json.load(f)
                logger.info(f"기존 EPUB 진행 상태 로드됨: {len(metadata.get('translated_files', []))}개 챕터 번역 완료")
            except Exception as e:
                logger.warning(f"EPUB 메타데이터 읽기 실패: {e}. 임시 폴더를 초기화합니다.")
                shutil.rmtree(temp_dir, ignore_errors=True)
                temp_dir.mkdir(parents=True, exist_ok=True)
        else:
            temp_dir.mkdir(parents=True, exist_ok=True)
            
        translated_files = set(metadata.get("translated_files", []))

        try:
            with zipfile.ZipFile(epub_path, 'r') as zin:
                with zipfile.ZipFile(output_path, 'w') as zout:
                    # 1. mimetype 보존 (EPUB 표준: 압축 없이 첫 번째 파일)
                    if 'mimetype' in zin.namelist():
                        zout.writestr('mimetype', zin.read('mimetype'), compress_type=zipfile.ZIP_STORED)

                    # 2. 모든 파일 순회
                    for item in zin.infolist():
                        if item.filename == 'mimetype':
                            continue
                        
                        # 중단 체크
                        if self.stop_check_callback and self.stop_check_callback():
                            raise asyncio.CancelledError("EPUB 번역 중단 요청됨")
                            
                        # 진행도 저장 체크
                        temp_file_path = temp_dir / item.filename.replace("/", "_")
                        if item.filename in translated_files and temp_file_path.exists():
                            logger.info(f"  ⏭️ 이미 번역된 챕터 건너뜀: {item.filename}")
                            with open(temp_file_path, "rb") as f:
                                zout.writestr(item.filename, f.read(), compress_type=zipfile.ZIP_DEFLATED)
                            continue

                        content = zin.read(item.filename)
                        
                        # HTML/XHTML 파일만 번역
                        if item.filename.lower().endswith(('.xhtml', '.html', '.htm')):
                            logger.info(f"  📄 챕터 처리 중: {item.filename}")
                            try:
                                chapter = processor.process_chapter(content, item.filename)
                                translatable_nodes = [n for n in chapter.nodes if n.type == NodeType.TEXT]
                                
                                if translatable_nodes:
                                    # 무결성 모드와 동일한 로직으로 노드 리스트 번역
                                    # 번역 단위로 변환 (id와 text만 필요)
                                    units = [TranslationUnit(id=n.id, text=n.content or "") for n in translatable_nodes]
                                    
                                    # 청크 분할 및 번역 요청
                                    max_chunk_size = self.config.get("chunk_size", 6000)
                                    max_items = self.config.get("integrity_max_items", 200)
                                    chunks = self.chunk_service.split_nodes_into_chunks(units, max_chunk_size, max_items)
                                    
                                    translated_map: Dict[str, str] = {}
                                    for i, chunk in enumerate(chunks):
                                        logger.info(f"    📦 EPUB 노드 청크 {i+1}/{len(chunks)} 번역 중")
                                        chunk_results = await self._translate_integrity_chunk_with_retry(chunk)
                                        translated_map.update(chunk_results)
                                    
                                    # 번역된 내용으로 HTML 재조립
                                    translated_html = processor.reconstruct_chapter(chapter, translated_map)
                                    translated_bytes = translated_html.encode('utf-8')
                                    
                                    # 임시 디렉토리에 저장 및 메타데이터 업데이트
                                    with open(temp_file_path, "wb") as f:
                                        f.write(translated_bytes)
                                        
                                    translated_files.add(item.filename)
                                    metadata["translated_files"] = list(translated_files)
                                    with open(metadata_path, "w", encoding="utf-8") as f:
                                        json.dump(metadata, f, ensure_ascii=False)
                                        
                                    zout.writestr(item.filename, translated_bytes, compress_type=zipfile.ZIP_DEFLATED)
                                else:
                                    zout.writestr(item.filename, content, compress_type=zipfile.ZIP_DEFLATED)
                                    
                            except Exception as e_chapter:
                                logger.error(f"챕터 {item.filename} 처리 중 오류 (원본 보존): {e_chapter}")
                                zout.writestr(item.filename, content, compress_type=zipfile.ZIP_DEFLATED)
                        else:
                            # 이미지, CSS, 기타 리소스는 그대로 복사
                            if item.filename.lower().endswith('.opf'):
                                try:
                                    opf_text = content.decode('utf-8')
                                    opf_text = re.sub(r'page-progression-direction\s*=\s*["\']rtl["\']', 'page-progression-direction="ltr"', opf_text, flags=re.IGNORECASE)
                                    zout.writestr(item, opf_text.encode('utf-8'), compress_type=zipfile.ZIP_DEFLATED)
                                except Exception as e_opf:
                                    logger.error(f"OPF 방향 수정 중 오류: {e_opf}")
                                    zout.writestr(item, content, compress_type=zipfile.ZIP_DEFLATED)
                            else:
                                zout.writestr(item, content, compress_type=zipfile.ZIP_DEFLATED)

            # 성공적으로 완료되면 임시 디렉토리 삭제
            shutil.rmtree(temp_dir, ignore_errors=True)
            logger.info(f"✅ EPUB 번역 완료: {output_path.name}")

        except Exception as e:
            logger.error(f"EPUB 번역 작업 중 치명적 오류: {e}")
            raise BtgTranslationException(f"EPUB 번역 실패: {e}")

