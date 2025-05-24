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
    from .file_handler import load_pronouns_from_csv # file_handler.py에서 직접 호출하도록 변경
    from .logger_config import setup_logger
    from .exceptions import BtgTranslationException, BtgPronounException, BtgApiClientException
    from .file_handler import PRONOUN_CSV_HEADER
    from .chunk_service import ChunkService
    # types 모듈은 gemini_client에서 사용되므로, 여기서는 직접적인 의존성이 없을 수 있습니다.
    # 만약 이 파일 내에서 types.Part 등을 직접 사용한다면, 아래와 같이 임포트가 필요합니다.
    # from google.genai import types as genai_types 
except ImportError:
    from gemini_client import (
        GeminiClient,
        GeminiContentSafetyException,
        GeminiRateLimitException,
        GeminiApiException,
        GeminiInvalidRequestException,
        GeminiAllApiKeysExhaustedException 
    )
    from file_handler import load_pronouns_from_csv # file_handler.py에서 직접 호출하도록 변경
    from logger_config import setup_logger
    from exceptions import BtgTranslationException, BtgPronounException, BtgApiClientException
    from file_handler import PRONOUN_CSV_HEADER
    from chunk_service import ChunkService
    # from google.genai import types as genai_types # Fallback import

logger = setup_logger(__name__)

def format_pronouns_for_prompt(pronouns: List[Dict[str, str]], max_entries: int = 20) -> str:
    if not pronouns:
        return "고유명사 목록 없음"
    
    def get_count(item):
        try:
            return int(item.get(PRONOUN_CSV_HEADER[2], 0))
        except ValueError:
            return 0

    sorted_pronouns = sorted(pronouns, key=get_count, reverse=True)
    
    limited_pronouns = sorted_pronouns[:max_entries]
    
    formatted_list = []
    for p_dict in limited_pronouns:
        foreign = p_dict.get(PRONOUN_CSV_HEADER[0])
        korean = p_dict.get(PRONOUN_CSV_HEADER[1])
        if foreign and korean: 
             formatted_list.append(f"- {foreign}: {korean}")
    
    if not formatted_list:
        return "유효한 고유명사 항목 없음"
        
    return "\n".join(formatted_list)

class TranslationService:
    def __init__(self, gemini_client: GeminiClient, config: Dict[str, Any]):
        self.gemini_client = gemini_client
        self.config = config
        self.pronouns_map: Dict[str, str] = {}
        self.chunk_service = ChunkService()
        self._load_pronouns()

    def _load_pronouns(self):
        pronoun_csv_path_str = self.config.get("pronouns_csv")
        if pronoun_csv_path_str and os.path.exists(pronoun_csv_path_str):
            pronoun_csv_path = Path(pronoun_csv_path_str)
            try:
                # file_handler.py의 load_pronouns_from_csv 직접 사용
                pronoun_data_list = load_pronouns_from_csv(pronoun_csv_path)
                
                self.pronouns_map = {
                    item[PRONOUN_CSV_HEADER[0]].strip(): item[PRONOUN_CSV_HEADER[1]].strip()
                    for item in pronoun_data_list
                    if PRONOUN_CSV_HEADER[0] in item and PRONOUN_CSV_HEADER[1] in item and item[PRONOUN_CSV_HEADER[0]].strip()
                }
                logger.info(f"{len(self.pronouns_map)}개의 고유명사를 로드했습니다: {pronoun_csv_path}")
            except BtgPronounException as e: 
                logger.error(f"고유명사 파일 로드 중 오류 발생 ({pronoun_csv_path}): {e}")
                self.pronouns_map = {} 
            except Exception as e:
                logger.error(f"고유명사 파일 처리 중 예상치 못한 오류 ({pronoun_csv_path}): {e}", exc_info=True)
                self.pronouns_map = {}
        else:
            logger.info("고유명사 CSV 파일이 설정되지 않았거나 존재하지 않습니다. 고유명사 대체 없이 번역합니다.")
            self.pronouns_map = {}

    def _construct_prompt(self, chunk_text: str) -> str:
        prompt_template = self.config.get("prompts", "Translate to Korean: {{slot}}")
        if isinstance(prompt_template, (list, tuple)):
            prompt_template = prompt_template[0] if prompt_template else "Translate to Korean: {{slot}}"

        pronoun_data_for_prompt: List[Dict[str,str]] = []
        if self.pronouns_map: 
             pronoun_data_for_prompt = [{PRONOUN_CSV_HEADER[0]: f, PRONOUN_CSV_HEADER[1]: k} for f,k in self.pronouns_map.items()]

        max_pronoun_entries = self.config.get("max_pronoun_entries", 20)
        formatted_pronouns = format_pronouns_for_prompt(pronoun_data_for_prompt, max_pronoun_entries)
        
        final_prompt = prompt_template.replace("{{slot}}", chunk_text)
        if "{{pronouns}}" in final_prompt:
            final_prompt = final_prompt.replace("{{pronouns}}", formatted_pronouns)
        
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
        "prompts": "다음 텍스트를 한국어로 번역해주세요. 고유명사 목록: {{pronouns}}\n\n번역할 텍스트:\n{{slot}}",
        "max_pronoun_entries": 10,
    }

    # 1. 일반 번역 테스트
    print("\n--- 1. 일반 번역 테스트 ---")
    config1 = sample_config_base.copy()
    config1["pronouns_csv"] = "test_pronouns.csv" 
    
    test_pronoun_data = [
        {PRONOUN_CSV_HEADER[0]: "Alice", PRONOUN_CSV_HEADER[1]: "앨리스", PRONOUN_CSV_HEADER[2]: "10"},
        {PRONOUN_CSV_HEADER[0]: "Bob", PRONOUN_CSV_HEADER[1]: "밥", PRONOUN_CSV_HEADER[2]: "5"}
    ]
    from file_handler import write_csv_file, delete_file 
    test_pronoun_file = Path("test_pronouns.csv")
    if test_pronoun_file.exists(): delete_file(test_pronoun_file)
    rows_to_write = [[d[PRONOUN_CSV_HEADER[0]], d[PRONOUN_CSV_HEADER[1]], d[PRONOUN_CSV_HEADER[2]]] for d in test_pronoun_data]
    write_csv_file(test_pronoun_file, rows_to_write, header=PRONOUN_CSV_HEADER)


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
        if test_pronoun_file.exists(): delete_file(test_pronoun_file)


    # 2. 고유명사 없는 경우 테스트
    print("\n--- 2. 고유명사 없는 경우 테스트 ---")
    config2 = sample_config_base.copy()
    config2["pronouns_csv"] = None 
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
