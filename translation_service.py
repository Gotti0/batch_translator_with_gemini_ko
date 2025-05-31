# translation_service.py
import time
import random
import re
import csv
from pathlib import Path
from typing import Dict, Any, Optional, List, Union
import os

try:
    from .gemini_client import (
        GeminiClient,
        GeminiContentSafetyException,
        GeminiRateLimitException,
        GeminiApiException,
        GeminiInvalidRequestException,
        GeminiAllApiKeysExhaustedException 
    )
    from .file_handler import read_json_file # JSON 로딩을 위해 추가
    from .logger_config import setup_logger
    from .exceptions import BtgTranslationException, BtgApiClientException
    from .chunk_service import ChunkService
    # types 모듈은 gemini_client에서 사용되므로, 여기서는 직접적인 의존성이 없을 수 있습니다.
    # 만약 이 파일 내에서 types.Part 등을 직접 사용한다면, 아래와 같이 임포트가 필요합니다.
    # from google.genai import types as genai_types 
    from .dtos import LorebookEntryDTO # 로어북 DTO 임포트
except ImportError:
    from gemini_client import (
        GeminiClient,
        GeminiContentSafetyException,
        GeminiRateLimitException,
        GeminiApiException,
        GeminiInvalidRequestException,
        GeminiAllApiKeysExhaustedException 
    )
    from file_handler import read_json_file # JSON 로딩을 위해 추가
    from logger_config import setup_logger
    from exceptions import BtgTranslationException, BtgApiClientException
    from chunk_service import ChunkService
    from dtos import LorebookEntryDTO # 로어북 DTO 임포트
    # from google.genai import types as genai_types # Fallback import

logger = setup_logger(__name__)

def _format_lorebook_for_prompt(
    lorebook_entries: List[LorebookEntryDTO],
    max_entries: int,
    max_chars: int
) -> str:
    if not lorebook_entries:
        return "로어북 컨텍스트 없음"

    selected_entries_str = []
    current_chars = 0
    entries_count = 0

    # 중요도 높은 순, 중요도 같으면 키워드 가나다 순으로 정렬
    # isSpoiler가 True인 항목은 낮은 우선순위를 갖도록 조정 (예: 중요도를 낮춤)
    def sort_key(entry: LorebookEntryDTO):
        importance = entry.importance or 0
        if entry.isSpoiler:
            importance -= 100 # 스포일러 항목의 중요도를 크게 낮춤
        return (-importance, entry.keyword.lower())

    sorted_entries = sorted(lorebook_entries, key=sort_key)

    for entry in sorted_entries:
        if entries_count >= max_entries:
            break

        spoiler_text = "예" if entry.isSpoiler else "아니오"
        details_parts = []
        if entry.category:
            details_parts.append(f"카테고리: {entry.category}")
        details_parts.append(f"스포일러: {spoiler_text}")
        
        details_str = ", ".join(details_parts)
        entry_str = f"- {entry.keyword}: {entry.description} ({details_str})"
        
        # 현재 항목 추가 시 최대 글자 수 초과하면 중단 (단, 최소 1개는 포함되도록)
        if current_chars + len(entry_str) > max_chars and entries_count > 0:
            break
        
        selected_entries_str.append(entry_str)
        current_chars += len(entry_str) + 1 # +1 for newline
        entries_count += 1
    
    if not selected_entries_str:
        return "로어북 컨텍스트 없음 (제한으로 인해 선택된 항목 없음)"
        
    return "\n".join(selected_entries_str)

class TranslationService:
    def __init__(self, gemini_client: GeminiClient, config: Dict[str, Any]):
        self.gemini_client = gemini_client
        self.config = config
        self.chunk_service = ChunkService()
        self.lorebook_entries_for_injection: List[LorebookEntryDTO] = [] # For new lorebook injection

        if self.config.get("enable_dynamic_lorebook_injection", False):
            self._load_lorebook_data()
            logger.info("동적 로어북 주입 활성화됨. 로어북 데이터 로드 시도.")
        else:
            logger.info("동적 로어북 주입 비활성화됨. 로어북 컨텍스트 없이 번역합니다.")

    def _load_lorebook_data(self):
        lorebook_json_path_str = self.config.get("lorebook_json_path_for_injection")
        if lorebook_json_path_str and os.path.exists(lorebook_json_path_str):
            lorebook_json_path = Path(lorebook_json_path_str)
            try:
                raw_data = read_json_file(lorebook_json_path)
                if isinstance(raw_data, list):
                    for item_dict in raw_data:
                        if isinstance(item_dict, dict) and "keyword" in item_dict and "description" in item_dict:
                            try:
                                entry = LorebookEntryDTO(
                                    keyword=item_dict.get("keyword", ""),
                                    description=item_dict.get("description", ""),
                                    category=item_dict.get("category"),
                                    importance=int(item_dict.get("importance", 0)) if item_dict.get("importance") is not None else None,
                                    sourceSegmentTextPreview=item_dict.get("sourceSegmentTextPreview"),
                                    isSpoiler=bool(item_dict.get("isSpoiler", False)),
                                    source_language=item_dict.get("source_language") # 로어북 JSON에서 source_language 로드
                                )
                                if entry.keyword and entry.description: # 필수 필드 확인
                                    self.lorebook_entries_for_injection.append(entry)
                                else:
                                    logger.warning(f"로어북 항목에 필수 필드(keyword 또는 description) 누락: {item_dict}")
                            except (TypeError, ValueError) as e_dto:
                                logger.warning(f"로어북 항목 DTO 변환 중 오류: {item_dict}, 오류: {e_dto}")
                        else:
                            logger.warning(f"잘못된 로어북 항목 형식 (딕셔너리가 아니거나 필수 키 누락): {item_dict}")
                    logger.info(f"{len(self.lorebook_entries_for_injection)}개의 로어북 항목을 로드했습니다: {lorebook_json_path}")
                else:
                    logger.error(f"로어북 JSON 파일이 리스트 형식이 아닙니다: {lorebook_json_path}, 타입: {type(raw_data)}")
            except Exception as e:
                logger.error(f"로어북 JSON 파일 처리 중 예상치 못한 오류 ({lorebook_json_path}): {e}", exc_info=True)
                self.lorebook_entries_for_injection = []
        else:
            logger.info("동적 주입용 로어북 JSON 파일이 설정되지 않았거나 존재하지 않습니다.")
            self.lorebook_entries_for_injection = []

    def _construct_prompt(self, chunk_text: str) -> str:
        prompt_template = self.config.get("prompts", "Translate to Korean: {{slot}}")
        if isinstance(prompt_template, (list, tuple)):
            prompt_template = prompt_template[0] if prompt_template else "Translate to Korean: {{slot}}"

        final_prompt = prompt_template

        # Determine the source language for the current chunk to filter lorebook entries
        config_source_lang = self.config.get("source_language_for_translation")
        # Fallback language from config, with a hardcoded default if the config key itself is missing
        config_fallback_lang = self.config.get("source_language_for_translation_fallback", "ja") 

        current_source_lang_for_translation: str # Type hint for clarity

        if config_source_lang == "auto":
            if chunk_text and chunk_text.strip():
                logger.info(f"번역 출발 언어 자동 감지 시도 (설정: 'auto', 청크 일부: '{chunk_text[:30].strip()}...')...")
                try:
                    detected_lang = self.gemini_client.detect_language(chunk_text)
                    if detected_lang:
                        current_source_lang_for_translation = detected_lang
                        logger.info(f"청크 언어 자동 감지 성공: '{current_source_lang_for_translation}'")
                    else:
                        current_source_lang_for_translation = config_fallback_lang
                        logger.warning(f"청크 언어 자동 감지 실패 (API가 None 반환). 폴백 언어 '{current_source_lang_for_translation}' 사용.")
                except Exception as e_detect:
                    current_source_lang_for_translation = config_fallback_lang
                    logger.error(f"청크 언어 자동 감지 중 오류 발생: {e_detect}. 폴백 언어 '{current_source_lang_for_translation}' 사용.")
            else:
                current_source_lang_for_translation = config_fallback_lang
                logger.info(f"청크 텍스트가 비어있어 언어 자동 감지를 건너뛰고 폴백 언어 '{current_source_lang_for_translation}' 사용.")
        elif config_source_lang and isinstance(config_source_lang, str) and config_source_lang.strip(): # Specific language code provided
            current_source_lang_for_translation = config_source_lang
            logger.info(f"명시적 번역 출발 언어 '{current_source_lang_for_translation}' 사용.")
        else: # config_source_lang is None, empty string, or not "auto"
            current_source_lang_for_translation = config_fallback_lang
            logger.warning(f"번역 출발 언어가 유효하게 설정되지 않았거나 'auto'가 아닙니다. 폴백 언어 '{current_source_lang_for_translation}' 사용.")

        # 1. Dynamic Lorebook Injection
        if self.config.get("enable_dynamic_lorebook_injection", False) and \
           self.lorebook_entries_for_injection and \
           "{{lorebook_context}}" in final_prompt:

            logger.debug(f"번역 프롬프트 구성 중: 번역 출발 언어로 '{current_source_lang_for_translation}' 사용.")
            # 1.a. Filter lorebook entries relevant to the current chunk_text
            relevant_entries_for_chunk: List[LorebookEntryDTO] = []
            chunk_text_lower = chunk_text.lower() # For case-insensitive keyword matching
            for entry in self.lorebook_entries_for_injection:
                # 로어북 항목의 언어와 현재 번역 출발 언어가 일치하는지 확인
                if entry.source_language and \
                   current_source_lang_for_translation and \
                   entry.source_language.lower() != current_source_lang_for_translation.lower():
                    logger.debug(f"로어북 항목 '{entry.keyword}' 건너뜀: 언어 불일치 (로어북: {entry.source_language}, 번역 출발: {current_source_lang_for_translation}).")
                    continue

                if entry.keyword.lower() in chunk_text_lower: # 중요: 로어북 키워드는 번역 출발 언어와 일치해야 함
                    relevant_entries_for_chunk.append(entry)
            
            logger.debug(f"현재 청크에 대해 {len(relevant_entries_for_chunk)}개의 관련 로어북 항목 발견.")

            # 1.b. Format the relevant entries for the prompt
            max_entries = self.config.get("max_lorebook_entries_per_chunk_injection", 3)
            max_chars = self.config.get("max_lorebook_chars_per_chunk_injection", 500)
            
            formatted_lorebook_context = _format_lorebook_for_prompt(
                relevant_entries_for_chunk, max_entries, max_chars # Pass only relevant entries
            )
            
            # Check if actual content was formatted (not just "없음" messages)
            if formatted_lorebook_context != "로어북 컨텍스트 없음" and \
               formatted_lorebook_context != "로어북 컨텍스트 없음 (제한으로 인해 선택된 항목 없음)":
                logger.info(f"API 요청에 동적 로어북 컨텍스트 주입됨. 내용 일부: {formatted_lorebook_context[:100]}...")
            else:
                logger.debug(f"동적 로어북 주입 시도했으나, 관련 항목 없거나 제한으로 인해 실제 주입 내용 없음. 사용된 메시지: {formatted_lorebook_context}")
            final_prompt = final_prompt.replace("{{lorebook_context}}", formatted_lorebook_context)
        else:
            if "{{lorebook_context}}" in final_prompt:
                 final_prompt = final_prompt.replace("{{lorebook_context}}", "로어북 컨텍스트 없음 (주입 비활성화 또는 해당 항목 없음)")
                 logger.debug("동적 로어북 주입 비활성화 또는 플레이스홀더 부재로 '컨텍스트 없음' 메시지 사용.")
        
        # 3. Main content slot - This should be done *after* all other placeholders are processed.
        final_prompt = final_prompt.replace("{{slot}}", chunk_text)
        
        return final_prompt

    def translate_text(self, text_chunk: str) -> str:
        """기존 translate_text 메서드 (수정 없음)"""
        if not text_chunk.strip():
            return ""

        processed_text = text_chunk
        prompt = self._construct_prompt(processed_text)

        try:
            logger.debug(f"Gemini API 호출 시작. 모델: {self.config.get('model_name')}")
            
            translated_text = self.gemini_client.generate_text(
                prompt=prompt,
                model_name=self.config.get("model_name", "gemini-1.5-flash-latest"),
                generation_config_dict={
                    "temperature": self.config.get("temperature", 0.7),
                    "top_p": self.config.get("top_p", 0.9)
                },
            )

            if translated_text is None:
                logger.error("GeminiClient.generate_text가 None을 반환했습니다.")
                raise BtgApiClientException("API 호출 결과가 없습니다.")

            logger.debug(f"Gemini API 호출 성공. 번역된 텍스트 (일부): {translated_text[:100]}...")

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
        # 중복된 GeminiContentSafetyException 제거
        except GeminiApiException as e_api:
            logger.error(f"Gemini API 호출 중 일반 오류 발생: {e_api}")
            raise BtgApiClientException(f"API 호출 중 오류가 발생했습니다: {e_api}", original_exception=e_api) from e_api
        except Exception as e:
            logger.error(f"번역 중 예상치 못한 오류 발생: {e}", exc_info=True)
            raise BtgTranslationException(f"번역 중 알 수 없는 오류가 발생했습니다: {e}", original_exception=e) from e
        
        final_text = translated_text 
        return final_text.strip()
    
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
            # 검열 오류가 아닌 경우 그대로 예외 발생
            if "콘텐츠 안전 문제" not in str(e):
                raise e
            
            logger.warning(f"콘텐츠 안전 문제 감지. 청크 분할 재시도 시작: {str(e)}")
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
            return f"[검열로 인한 번역 실패: 최대 분할 시도 초과]"

        if len(text_chunk.strip()) <= min_chunk_size:
            logger.warning(f"최소 청크 크기에 도달했지만 여전히 검열됨: {text_chunk[:50]}...")
            return f"[검열로 인한 번역 실패: {text_chunk[:30]}...]"

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
            return f"[분할 불가능한 검열 콘텐츠: {text_chunk[:30]}...]"
        
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
                translated_part = self.translate_text(sub_chunk.strip())
                processing_time = time.time() - start_time
                
                translated_parts.append(translated_part)
                successful_sub_chunks += 1
                
                logger.info(f"   ✅ {sub_chunk_info} 번역 성공 (소요: {processing_time:.2f}초)")
                logger.debug(f"      📊 결과 길이: {len(translated_part)} 글자")
                logger.debug(f"      📈 진행률: {(i+1)/total_sub_chunks*100:.1f}% ({i+1}/{total_sub_chunks})")
                
            except BtgTranslationException as sub_e:
                processing_time = time.time() - start_time
                
                if "콘텐츠 안전 문제" in str(sub_e):
                    logger.warning(f"   🛡️ {sub_chunk_info} 검열 발생 (소요: {processing_time:.2f}초)")
                    logger.info(f"   🔄 재귀 분할 시도 (깊이: {current_attempt} → {current_attempt+1})")
                    
                    # 재귀적으로 더 작게 분할 시도
                    recursive_result = self._translate_with_recursive_splitting(
                        sub_chunk, max_split_attempts, min_chunk_size, current_attempt + 1
                    )
                    translated_parts.append(recursive_result)
                    
                    if "[검열로 인한 번역 실패" in recursive_result:
                        failed_sub_chunks += 1
                        logger.warning(f"   ❌ {sub_chunk_info} 최종 실패")
                    else:
                        successful_sub_chunks += 1
                        logger.info(f"   ✅ {sub_chunk_info} 재귀 분할 후 성공")
                else:
                    # 다른 번역 오류인 경우
                    failed_sub_chunks += 1
                    logger.error(f"   ❌ {sub_chunk_info} 번역 실패 (소요: {processing_time:.2f}초): {sub_e}")
                    translated_parts.append(f"[번역 실패: {str(sub_e)}]")
                
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
    from google.genai import types # <--- 여기에 types 임포트 추가

    print("--- TranslationService 테스트 ---")
    class MockGeminiClient:
        def __init__(self, auth_credentials, project=None, location=None):
            self.auth_credentials = auth_credentials
            self.api_keys_list = []
            self.current_api_key = None
            self.client = self # 자기 자신을 client로 설정 (실제 Client 객체 대신)

            if isinstance(auth_credentials, list):
                self.api_keys_list = auth_credentials
                if self.api_keys_list: self.current_api_key = self.api_keys_list[0]
            elif isinstance(auth_credentials, str) and not auth_credentials.startswith('{'):
                self.api_keys_list = [auth_credentials]
                self.current_api_key = auth_credentials
            print(f"MockGeminiClient initialized. API Keys: {self.api_keys_list}, Current Key: {self.current_api_key}")

        def generative_model(self, model_name, system_instruction=None): 
            print(f"  MockGeminiClient: generative_model() 호출됨. 모델: {model_name}, 시스템 명령어: {'있음' if system_instruction else '없음'}")
            self.current_model_name_for_test = model_name
            return self 

        def generate_content(self, contents, generation_config, safety_settings, stream): 
            prompt_text_for_mock = ""
            if isinstance(contents, list) and contents and isinstance(contents[0], types.Part): # types 사용
                prompt_text_for_mock = "".join(p.text for p in contents if hasattr(p, "text"))
            elif isinstance(contents, str): 
                prompt_text_for_mock = contents


            print(f"  MockGeminiClient.generate_content 호출됨 (모델: {getattr(self, 'current_model_name_for_test', 'N/A')}). 현재 키: {self.current_api_key[:5] if self.current_api_key else 'N/A'}")
            if "안전 문제" in prompt_text_for_mock:
                raise GeminiContentSafetyException("Mock 콘텐츠 안전 문제")
            if "사용량 제한" in prompt_text_for_mock:
                if self.current_api_key == "rate_limit_key":
                    raise GeminiRateLimitException("Mock API 사용량 제한")
            if "잘못된 요청" in prompt_text_for_mock:
                raise GeminiInvalidRequestException("Mock 잘못된 요청")
            if "잘못된 키" in prompt_text_for_mock and self.current_api_key == "invalid_key":
                 raise GeminiInvalidRequestException("Invalid API key (mock)")
            
            mock_part = types.Part(text=f"[번역됨] {prompt_text_for_mock.split('번역할 텍스트:')[-1].strip()[:50]}...") # types 사용
            mock_candidate = types.Candidate(content=types.Content(parts=[mock_part]), finish_reason=types.FinishReason.STOP) # types 사용
            
            class MockResponse:
                def __init__(self, candidates):
                    self.candidates = candidates
                    self.prompt_feedback = None 
                @property
                def text(self):
                    if self.candidates and self.candidates[0].content and self.candidates[0].content.parts:
                        return "".join(p.text for p in self.candidates[0].content.parts if hasattr(p, "text"))
                    return None

            return MockResponse(candidates=[mock_candidate])


        def list_models(self): return [] 

    sample_config_base = {
        "model_name": "gemini-1.5-flash", "temperature": 0.7, "top_p": 0.9,
        "prompts": "다음 텍스트를 한국어로 번역해주세요. 로어북 컨텍스트: {{lorebook_context}}\n\n번역할 텍스트:\n{{slot}}",
        "enable_dynamic_lorebook_injection": True, # 테스트를 위해 활성화
        "lorebook_json_path_for_injection": "test_lorebook.json", # 테스트용 로어북 경로
        "max_lorebook_entries_per_chunk_injection": 3,
        "max_lorebook_chars_per_chunk_injection": 200,
    }

    # 1. 로어북 주입 테스트
    print("\n--- 1. 로어북 주입 번역 테스트 ---")
    config1 = sample_config_base.copy()
    
    # 테스트용 로어북 파일 생성
    test_lorebook_data = [
        {"keyword": "Alice", "description": "주인공 앨리스", "category": "인물", "importance": 10, "isSpoiler": False},
        {"keyword": "Bob", "description": "앨리스의 친구 밥", "category": "인물", "importance": 8, "isSpoiler": False}
    ]
    from file_handler import write_json_file, delete_file # write_csv_file -> write_json_file
    test_lorebook_file = Path("test_lorebook.json")
    if test_lorebook_file.exists(): delete_file(test_lorebook_file)
    write_json_file(test_lorebook_file, test_lorebook_data)

    gemini_client_instance = MockGeminiClient(auth_credentials="dummy_api_key")
    translation_service1 = TranslationService(gemini_client_instance, config1)
    text_to_translate1 = "Hello Alice, how are you Bob?"
    try:
        translated1 = translation_service1.translate_text(text_to_translate1)
        print(f"원본: {text_to_translate1}")
        print(f"번역 결과: {translated1}")
    except Exception as e:
        print(f"테스트 1 오류: {e}")
    finally:
        if test_lorebook_file.exists(): delete_file(test_lorebook_file)

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
