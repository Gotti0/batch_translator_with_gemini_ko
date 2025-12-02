# app_service.py
from pathlib import Path
# typing 모듈에서 Tuple을 임포트합니다.
from typing import Dict, Any, Optional, List, Callable, Union, Tuple
import os
import json
import csv
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed, wait # ThreadPoolExecutor를 사용하여 병렬 처리
import time
from tqdm import tqdm # tqdm 임포트 확인
import sys # sys 임포트 확인 (tqdm_file_stream=sys.stdout 에 사용될 수 있음)

try:
    from infrastructure.logger_config import setup_logger
except ImportError:
    from infrastructure.logger_config import setup_logger

try:
    # file_handler에서 필요한 함수들을 import 합니다.
    from infrastructure.file_handler import (
        read_text_file, write_text_file,
        save_chunk_with_index_to_file, get_metadata_file_path, delete_file,
        load_chunks_from_file,
        create_new_metadata, save_metadata, load_metadata,
        update_metadata_for_chunk_completion, update_metadata_for_chunk_failure, # 추가
        _hash_config_for_metadata,
        save_merged_chunks_to_file
    )
    from ..core.config.config_manager import ConfigManager
    from infrastructure.gemini_client import GeminiClient, GeminiAllApiKeysExhaustedException, GeminiInvalidRequestException
    from domain.translation_service import TranslationService
    from domain.glossary_service import SimpleGlossaryService
    from ..utils.chunk_service import ChunkService
    from ..core.exceptions import BtgServiceException, BtgConfigException, BtgFileHandlerException, BtgApiClientException, BtgTranslationException, BtgBusinessLogicException
    from ..core.dtos import TranslationJobProgressDTO, GlossaryExtractionProgressDTO
    from ..utils.post_processing_service import PostProcessingService
    from ..utils.quality_check_service import QualityCheckService
except ImportError:
    # Fallback imports
    from infrastructure.file_handler import (
        read_text_file, write_text_file,
        save_chunk_with_index_to_file, get_metadata_file_path, delete_file,
        load_chunks_from_file,
        create_new_metadata, save_metadata, load_metadata,
        update_metadata_for_chunk_completion, update_metadata_for_chunk_failure, # 추가
        _hash_config_for_metadata,
        save_merged_chunks_to_file
    )
    from core.config.config_manager import ConfigManager
    from infrastructure.gemini_client import GeminiClient, GeminiAllApiKeysExhaustedException, GeminiInvalidRequestException
    from domain.translation_service import TranslationService
    from domain.glossary_service import SimpleGlossaryService
    from utils.chunk_service import ChunkService
    from core.exceptions import BtgServiceException, BtgConfigException, BtgFileHandlerException, BtgApiClientException, BtgTranslationException, BtgBusinessLogicException
    from core.dtos import TranslationJobProgressDTO, GlossaryExtractionProgressDTO
    from utils.post_processing_service import PostProcessingService
    from utils.quality_check_service import QualityCheckService

logger = setup_logger(__name__)

class AppService:
    """
    애플리케이션의 주요 유스케이스를 조정하는 서비스 계층입니다.
    프레젠테이션 계층과 비즈니스 로직/인프라 계층 간의 인터페이스 역할을 합니다.
    """

    def __init__(self, config_file_path: Optional[Union[str, Path]] = None):
        self.config_manager = ConfigManager(config_file_path)
        self.config: Dict[str, Any] = {}
        self.gemini_client: Optional[GeminiClient] = None
        self.translation_service: Optional[TranslationService] = None
        self.glossary_service: Optional[SimpleGlossaryService] = None # Renamed from pronoun_service
        self.chunk_service = ChunkService()

        self.is_translation_running = False
        self.stop_requested = False
        self._translation_lock = threading.Lock()
        self._progress_lock = threading.Lock()
        # 파일 쓰기 동기화 (스레드 세이프)
        self._file_write_lock = threading.Lock()
        self.processed_chunks_count = 0
        self.successful_chunks_count = 0
        self.failed_chunks_count = 0
        self.post_processing_service = PostProcessingService()
        self.quality_check_service = QualityCheckService()

        self.load_app_config()

    def load_app_config(self, runtime_overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        logger.info("애플리케이션 설정 로드 중...")

        try:
            # 1. 파일 및 기본값으로부터 기본 설정 로드
            config_from_manager = self.config_manager.load_config()
            self.config = config_from_manager # 파일/기본값으로 시작

            # 2. 제공된 runtime_overrides가 있다면, self.config에 덮어쓰기
            if runtime_overrides:
                self.config.update(runtime_overrides)
                logger.info(f"런타임 오버라이드 적용: {list(runtime_overrides.keys())}")
            logger.info("애플리케이션 설정 로드 완료.")

            auth_credentials_for_gemini_client: Optional[Union[str, List[str], Dict[str, Any]]] = None
            use_vertex = self.config.get("use_vertex_ai", False)
            gcp_project_from_config = self.config.get("gcp_project")
            gcp_location = self.config.get("gcp_location")
            sa_file_path_str = self.config.get("service_account_file_path")

            # 설정 요약 로깅 (조건부)
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"설정 요약: vertex={use_vertex}, project={gcp_project_from_config}, location={gcp_location}")

            if use_vertex:
                logger.info("Vertex AI 사용 모드로 설정되었습니다.")
                # Vertex AI 모드에서는 auth_credentials_for_gemini_client가 SA JSON 문자열, SA Dict, 또는 None (ADC용)이 될 수 있습니다.
                if sa_file_path_str:
                    sa_file_path = Path(sa_file_path_str)
                    if sa_file_path.is_file():
                        try:
                            auth_credentials_for_gemini_client = read_text_file(sa_file_path)
                            logger.info(f"Vertex AI SA 파일에서 인증 정보 로드됨: {sa_file_path.name}")
                        except Exception as e:
                            logger.error(f"Vertex AI SA 파일 읽기 실패: {e}")
                            auth_credentials_for_gemini_client = None
                    else:
                        logger.warning(f"Vertex AI SA 파일 경로 무효: {sa_file_path_str}")
                        auth_conf_val = self.config.get("auth_credentials")
                        if isinstance(auth_conf_val, (str, dict)) and auth_conf_val:
                            auth_credentials_for_gemini_client = auth_conf_val
                            logger.info("auth_credentials 값을 대체 사용")
                        else:
                            auth_credentials_for_gemini_client = None
                            logger.info("ADC 사용 예정")
                elif self.config.get("auth_credentials"):
                    auth_conf_val = self.config.get("auth_credentials")
                    if isinstance(auth_conf_val, (str, dict)) and auth_conf_val:
                        auth_credentials_for_gemini_client = auth_conf_val
                        logger.info("Vertex AI: auth_credentials 값 사용")
                    else:
                        auth_credentials_for_gemini_client = None
                        logger.info("Vertex AI: ADC 사용 예정")
                else:
                    auth_credentials_for_gemini_client = None
                    logger.info("Vertex AI: ADC 사용")
            else:
                logger.info("Gemini Developer API 모드")
                auth_credentials_for_gemini_client = None

                api_keys_list_val = self.config.get("api_keys", [])
                if isinstance(api_keys_list_val, list):
                    valid_api_keys = [key for key in api_keys_list_val if isinstance(key, str) and key.strip()]
                    if valid_api_keys:
                        auth_credentials_for_gemini_client = valid_api_keys
                        logger.info(f"API 키 {len(valid_api_keys)}개 사용")
                
                if auth_credentials_for_gemini_client is None:
                    api_key_val = self.config.get("api_key")
                    if isinstance(api_key_val, str) and api_key_val.strip():
                        auth_credentials_for_gemini_client = api_key_val
                        logger.info("단일 API 키 사용")

                if auth_credentials_for_gemini_client is None:
                    auth_credentials_conf_val = self.config.get("auth_credentials")
                    if isinstance(auth_credentials_conf_val, str) and auth_credentials_conf_val.strip():
                        auth_credentials_for_gemini_client = auth_credentials_conf_val
                        logger.info("auth_credentials 문자열 사용")
                    elif isinstance(auth_credentials_conf_val, list):
                        valid_keys_from_auth_cred = [k for k in auth_credentials_conf_val if isinstance(k, str) and k.strip()]
                        if valid_keys_from_auth_cred:
                            auth_credentials_for_gemini_client = valid_keys_from_auth_cred
                            logger.info(f"auth_credentials에서 API 키 {len(valid_keys_from_auth_cred)}개 사용")
                    elif isinstance(auth_credentials_conf_val, dict):
                        auth_credentials_for_gemini_client = auth_credentials_conf_val
                        logger.info("auth_credentials SA dict 사용")

                if auth_credentials_for_gemini_client is None:
                    logger.warning("API 키가 설정되지 않음")

            should_initialize_client = False
            if auth_credentials_for_gemini_client:
                if isinstance(auth_credentials_for_gemini_client, str) and auth_credentials_for_gemini_client.strip():
                    should_initialize_client = True
                elif isinstance(auth_credentials_for_gemini_client, list) and auth_credentials_for_gemini_client:
                    should_initialize_client = True
                elif isinstance(auth_credentials_for_gemini_client, dict):
                    should_initialize_client = True
            elif use_vertex and not auth_credentials_for_gemini_client and \
                 (gcp_project_from_config or os.environ.get("GOOGLE_CLOUD_PROJECT")):
                should_initialize_client = True
                logger.info("Vertex AI ADC 모드로 클라이언트 초기화 예정")

            if should_initialize_client:
                try:
                    project_to_pass_to_client = gcp_project_from_config if gcp_project_from_config and gcp_project_from_config.strip() else None
                    rpm_value = self.config.get("requests_per_minute")
                    logger.info(f"GeminiClient 초기화: project={project_to_pass_to_client}, RPM={rpm_value}")
                    self.gemini_client = GeminiClient(
                        auth_credentials=auth_credentials_for_gemini_client,
                        project=project_to_pass_to_client,
                        location=gcp_location,
                        requests_per_minute=rpm_value
                    )
                except GeminiInvalidRequestException as e_inv:
                    logger.error(f"GeminiClient 초기화 실패: {e_inv}")
                    self.gemini_client = None
                except Exception as e_client:
                    logger.error(f"GeminiClient 초기화 오류: {e_client}", exc_info=True)
                    self.gemini_client = None
            else:
                logger.warning("API 키 또는 Vertex AI 설정이 충분하지 않아 Gemini 클라이언트 초기화를 시도하지 않습니다.")
                self.gemini_client = None

            if self.gemini_client:
                self.translation_service = TranslationService(self.gemini_client, self.config)
                self.glossary_service = SimpleGlossaryService(self.gemini_client, self.config) # Changed to SimpleGlossaryService
                logger.info("TranslationService 및 SimpleGlossaryService가 성공적으로 초기화되었습니다.") # Message updated
            else:
                self.translation_service = None
                self.glossary_service = None # Renamed
                logger.warning("Gemini 클라이언트가 초기화되지 않아 번역 및 고유명사 서비스가 비활성화됩니다.")

            return self.config
        except FileNotFoundError as e:
            logger.error(f"설정 파일 찾기 실패: {e}")
            self.config = self.config_manager.get_default_config()
            logger.warning("기본 설정으로 계속 진행합니다. Gemini 클라이언트는 초기화되지 않을 수 있습니다.")
            self.gemini_client = None
            self.translation_service = None # Keep
            self.glossary_service = None # Renamed
            return self.config
        except Exception as e:
            logger.error(f"설정 로드 중 심각한 오류 발생: {e}", exc_info=True)
            raise BtgConfigException(f"설정 로드 오류: {e}", original_exception=e) from e

    def save_app_config(self, config_data: Dict[str, Any]) -> bool:
        logger.info("애플리케이션 설정 저장 중...")
        try:
            success = self.config_manager.save_config(config_data)
            if success:
                logger.info("애플리케이션 설정 저장 완료.")
                # 저장 후에는 파일에서 최신 설정을 로드하므로 runtime_overrides 없이 호출
                # config_data가 최신 상태이므로, 이를 self.config에 반영하고 클라이언트를 재설정할 수도 있지만,
                # load_app_config()를 호출하여 일관된 로직을 따르는 것이 더 간단합니다.
                self.load_app_config() # runtime_overrides=None (기본값)
            return success
        except Exception as e:
            logger.error(f"설정 저장 중 오류 발생: {e}")
            raise BtgConfigException(f"설정 저장 오류: {e}", original_exception=e) from e

    def get_available_models(self) -> List[Dict[str, Any]]:
        if not self.gemini_client:
            logger.error("모델 목록 조회 실패: Gemini 클라이언트가 초기화되지 않았습니다.")
            raise BtgServiceException("Gemini 클라이언트가 초기화되지 않았습니다. API 키 또는 Vertex AI 설정을 확인하세요.")
        logger.info("사용 가능한 모델 목록 조회 서비스 호출됨.")
        try:
            all_models = self.gemini_client.list_models()
            # 모델 필터링 로직 제거됨
            logger.info(f"총 {len(all_models)}개의 모델을 API로부터 직접 반환합니다.")
            return all_models
            
        except BtgApiClientException as e:
            logger.error(f"모델 목록 조회 중 API 오류: {e}")
            raise
        except Exception as e:
            logger.error(f"모델 목록 조회 중 예상치 못한 오류: {e}", exc_info=True) # type: ignore
            raise BtgServiceException(f"모델 목록 조회 중 오류: {e}", original_exception=e) from e

    def extract_glossary(
        self,
        input_file_path: Union[str, Path],
        progress_callback: Optional[Callable[[GlossaryExtractionProgressDTO], None]] = None, # DTO Changed
        novel_language_code: Optional[str] = None, # 명시적 언어 코드 전달
        seed_glossary_path: Optional[Union[str, Path]] = None, # CLI에서 전달된 시드 용어집 경로
        user_override_glossary_extraction_prompt: Optional[str] = None, # 사용자 재정의 프롬프트 추가
        stop_check: Optional[Callable[[], bool]] = None
    ) -> Path:
        if not self.glossary_service: # Changed from pronoun_service
            logger.error("용어집 추출 서비스 실패: 서비스가 초기화되지 않았습니다.") # Message updated
            raise BtgServiceException("용어집 추출 서비스가 초기화되지 않았습니다. 설정을 확인하세요.") # Message updated
        
        logger.info(f"용어집 추출 서비스 시작: {input_file_path}, 시드 파일: {seed_glossary_path}")  
        try:
            file_content = read_text_file(input_file_path)
            if not file_content:
                logger.warning(f"입력 파일이 비어있습니다: {input_file_path}")
                # For lorebook, an empty input means an empty lorebook, unless a seed is provided.
                # SimpleGlossaryService.extract_and_save_lorebook handles empty content.
        
            # 로어북 추출 시 사용할 언어 코드 결정
            # 1. 명시적으로 전달된 novel_language_code
            # 2. 설정 파일의 novel_language (통합됨)
            # 3. None (SimpleGlossaryService에서 자체적으로 처리하거나 언어 특정 기능 비활성화)
            lang_code_for_extraction = novel_language_code or self.config.get("novel_language") # 통합된 설정 사용

            # 사용할 프롬프트 결정: 메서드 인자로 전달된 것이 있으면 그것을 사용, 없으면 설정 파일 값 사용
            prompt_to_use = user_override_glossary_extraction_prompt \
                if user_override_glossary_extraction_prompt is not None \
                else self.config.get("user_override_glossary_extraction_prompt")

            # lang_code_for_extraction은 SimpleGlossaryService.extract_and_save_glossary에서 직접 사용되지 않음.
            result_path = self.glossary_service.extract_and_save_glossary( # type: ignore
                novel_text_content=file_content,
                input_file_path_for_naming=input_file_path,
                progress_callback=progress_callback,
                seed_glossary_path=seed_glossary_path, # 시드 용어집 경로 전달
                user_override_glossary_extraction_prompt=prompt_to_use, # 결정된 프롬프트 전달
                stop_check=stop_check
            )
            logger.info(f"용어집 추출 완료. 결과 파일: {result_path}") # Message updated
        
            return result_path
        except FileNotFoundError as e:
            logger.error(f"용어집 추출을 위한 입력 파일을 찾을 수 없습니다: {input_file_path}") # Message updated
            if progress_callback:
                progress_callback(GlossaryExtractionProgressDTO(0,0,f"오류: 입력 파일 없음 - {e.filename}",0)) # DTO Changed
            raise BtgFileHandlerException(f"입력 파일 없음: {input_file_path}", original_exception=e) from e
        except (BtgBusinessLogicException, BtgApiClientException) as e: # BtgPronounException replaced with BtgBusinessLogicException
            logger.error(f"용어집 추출 중 오류: {e}") # Message updated
            if progress_callback:
                progress_callback(GlossaryExtractionProgressDTO(0,0,f"오류: {e}",0)) # DTO Changed
            raise
        except Exception as e: 
            logger.error(f"용어집 추출 서비스 중 예상치 못한 오류: {e}", exc_info=True)  # Message updated
            if progress_callback:
                progress_callback(GlossaryExtractionProgressDTO(0,0,f"예상치 못한 오류: {e}",0)) # DTO Changed
            raise BtgServiceException(f"용어집 추출 중 오류: {e}", original_exception=e) from e # Message updated


    def _translate_and_save_chunk(self, chunk_index: int, chunk_text: str,
                            chunked_output_file: Path,
                            total_chunks: int,
                            input_file_path_for_metadata: Path,
                            progress_callback: Optional[Callable[[TranslationJobProgressDTO], None]] = None) -> bool:
        current_chunk_info_msg = f"청크 {chunk_index}/{total_chunks}"
        
        # 청크 분석 (로깅 최적화: 통계는 DEBUG 레벨에서만 상세 출력)
        chunk_chars = len(chunk_text)
        start_time = time.time()
        
        # 통합 로그: 시작 정보와 기본 통계를 한 줄로
        logger.info(f"{current_chunk_info_msg} 처리 시작 (길이: {chunk_chars}자)")
        
        # 상세 정보는 DEBUG 레벨에서만 출력
        if logger.isEnabledFor(logging.DEBUG):
            chunk_lines = chunk_text.count('\n') + 1
            chunk_words = len(chunk_text.split())
            chunk_preview = chunk_text[:100].replace('\n', ' ') + '...' if len(chunk_text) > 100 else chunk_text
            logger.debug(f"  📝 미리보기: {chunk_preview}")
            logger.debug(f"  📊 통계: 글자={chunk_chars}, 단어={chunk_words}, 줄={chunk_lines}")
        last_error = None
        success = False

        

        try:
            if self.stop_requested:
                logger.info(f"{current_chunk_info_msg} ⏸️ 처리 중지됨 (사용자 요청)")
                return False

            if not self.translation_service:
                raise BtgServiceException("TranslationService가 초기화되지 않았습니다.")

            # 번역 설정 로드
            use_content_safety_retry = self.config.get("use_content_safety_retry", True)
            max_split_attempts = self.config.get("max_content_safety_split_attempts", 3)
            min_chunk_size = self.config.get("min_content_safety_chunk_size", 100)
            model_name = self.config.get("model_name", "gemini-2.0-flash")
            
            # 번역 설정 상세는 DEBUG에서만 출력
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"  ⚙️ 설정: 모델={model_name}, 안전재시도={use_content_safety_retry}, 최대시도={max_split_attempts}")
            
            translation_start_time = time.time()
                
            if use_content_safety_retry:
                translated_chunk = self.translation_service.translate_text_with_content_safety_retry(
                    chunk_text, max_split_attempts, min_chunk_size
                )
            else:
                translated_chunk = self.translation_service.translate_text(chunk_text)
            
            translation_time = time.time() - translation_start_time
            translated_length = len(translated_chunk) if translated_chunk else 0
            
            if self.stop_requested:
                logger.warning(f"  ⚠️ {current_chunk_info_msg} 번역 완료되었으나 중지됨 - 결과 폐기")
                return False

            # 스레드 세이프하게 청크 파일에 직접 기록
            with self._file_write_lock:
                save_chunk_with_index_to_file(chunked_output_file, chunk_index, translated_chunk)
            
            # 번역 성능 상세는 DEBUG에서만
            if logger.isEnabledFor(logging.DEBUG):
                speed = chunk_chars / translation_time if translation_time > 0 else 0
                logger.debug(f"  ✅ 번역완료: {translated_length}자, {translation_time:.2f}초, {speed:.0f}자/초")
            
            success = True
            
            ratio = len(translated_chunk) / len(chunk_text) if len(chunk_text) > 0 else 0.0
            total_processing_time = time.time() - start_time
            logger.info(f"  🎯 {current_chunk_info_msg} 전체 처리 완료 (총 소요: {total_processing_time:.2f}초, 길이비율: {ratio:.2f})")

        except BtgTranslationException as e_trans:
            if self.stop_requested:
                logger.warning(f"  ⚠️ {current_chunk_info_msg} 중지됨 - 번역 예외 무시")
                return False
            processing_time = time.time() - start_time
            error_type = "콘텐츠 검열" if "콘텐츠 안전 문제" in str(e_trans) else "번역 서비스"
            logger.error(f"  ❌ {current_chunk_info_msg} 실패: {error_type} - {e_trans} ({processing_time:.2f}초)")
            
            with self._file_write_lock:
                save_chunk_with_index_to_file(chunked_output_file, chunk_index, f"[번역 실패: {e_trans}]\n\n--- 원문 내용 ---\n{chunk_text}")
            last_error = str(e_trans)
            success = False

        except BtgApiClientException as e_api:
            if self.stop_requested:
                logger.warning(f"  ⚠️ {current_chunk_info_msg} 중지됨 - API 예외 무시")
                return False
            processing_time = time.time() - start_time
            # API 오류 유형 판별
            error_detail = ""
            if "사용량 제한" in str(e_api) or "429" in str(e_api):
                error_detail = " [사용량 제한]"
            elif "키" in str(e_api).lower() or "인증" in str(e_api):
                error_detail = " [인증 오류]"
            logger.error(f"  ❌ {current_chunk_info_msg} API 오류{error_detail}: {e_api} ({processing_time:.2f}초)")
            
            with self._file_write_lock:
                save_chunk_with_index_to_file(chunked_output_file, chunk_index, f"[API 오류로 번역 실패: {e_api}]\n\n--- 원문 내용 ---\n{chunk_text}")
            last_error = str(e_api)
            success = False

        except Exception as e_gen:
            if self.stop_requested:
                logger.warning(f"  ⚠️ {current_chunk_info_msg} 중지됨 - 예외 무시")
                return False
            processing_time = time.time() - start_time
            logger.error(f"  ❌ {current_chunk_info_msg} 예상치 못한 오류: {type(e_gen).__name__} - {e_gen} ({processing_time:.2f}초)", exc_info=True)
            
            with self._file_write_lock:
                save_chunk_with_index_to_file(chunked_output_file, chunk_index, f"[알 수 없는 오류로 번역 실패: {e_gen}]\n\n--- 원문 내용 ---\n{chunk_text}")
            last_error = str(e_gen)
            success = False
                    

        
        finally:
            total_time = time.time() - start_time
            with self._progress_lock:
                # 시스템이 중지 상태가 아니라면, 진행 상황을 업데이트합니다.
                # 이 검사를 _progress_lock 안에서 수행하여, 플래그 확인과 카운터 업데이트 사이의
                # 경쟁 조건을 완벽하게 방지합니다. 이것이 데이터 정합성을 보장하는 최종 방어선입니다.
                if not self.stop_requested:
                    # 1단계: 먼저 processed_chunks_count 증가
                    self.processed_chunks_count += 1
                    # 2단계: 결과에 따라 성공/실패 카운트 업데이트
                    if success:
                        self.successful_chunks_count += 1
                        # ✅ 메타데이터 업데이트: translated_chunks에 완료된 청크 기록
                        try:
                            metadata_updated = update_metadata_for_chunk_completion(
                                input_file_path_for_metadata, 
                                chunk_index,
                                source_length=len(chunk_text),
                                translated_length=len(translated_chunk)
                            )
                            if metadata_updated:
                                logger.debug(f"  💾 {current_chunk_info_msg} 메타데이터 업데이트 완료")
                            else:
                                logger.warning(f"  ⚠️ {current_chunk_info_msg} 메타데이터 업데이트 실패")
                        except Exception as meta_e:
                            logger.error(f"  ❌ {current_chunk_info_msg} 메타데이터 업데이트 중 오류: {meta_e}")
                    else: # 'success'가 False인 경우, 실패 카운터를 증가시킵니다.
                        self.failed_chunks_count += 1
                        #  실패한 청크 정보 기록
                        if last_error:
                            try:
                                update_metadata_for_chunk_failure(input_file_path_for_metadata, chunk_index, last_error)
                                logger.debug(f"  💾 {current_chunk_info_msg} 실패 정보 메타데이터에 기록 완료")
                            except Exception as meta_fail_e:
                                logger.error(f"  ❌ {current_chunk_info_msg} 실패 정보 메타데이터 기록 중 오류: {meta_fail_e}")
                    
                    # 3단계: 진행률 계산 및 통합 로깅 (2개 로그 → 1개)
                    progress_percentage = (self.processed_chunks_count / total_chunks) * 100
                    success_rate = (self.successful_chunks_count / self.processed_chunks_count) * 100 if self.processed_chunks_count > 0 else 0
                    
                    # 매 10% 또는 마지막 청크에서만 상세 로그 출력 (로그 빈도 최적화)
                    should_log_progress = (self.processed_chunks_count % max(1, total_chunks // 10) == 0) or (self.processed_chunks_count == total_chunks)
                    if should_log_progress:
                        logger.info(f"  📈 진행률: {progress_percentage:.0f}% ({self.processed_chunks_count}/{total_chunks}) | 성공률: {success_rate:.0f}% (✅{self.successful_chunks_count} ❌{self.failed_chunks_count})")


                    if progress_callback:
                        if success:
                            status_msg_for_dto = f"✅ 청크 {chunk_index + 1}/{total_chunks} 완료 ({total_time:.1f}초)"
                        else:
                            status_msg_for_dto = f"❌ 청크 {chunk_index + 1}/{total_chunks} 실패 ({total_time:.1f}초)"
                            if last_error:
                                status_msg_for_dto += f" - {last_error[:50]}..."

                        progress_dto = TranslationJobProgressDTO(
                            total_chunks=total_chunks,
                            processed_chunks=self.processed_chunks_count,
                            successful_chunks=self.successful_chunks_count,
                            failed_chunks=self.failed_chunks_count,
                            current_status_message=status_msg_for_dto,
                            current_chunk_processing=chunk_index + 1,
                            last_error_message=last_error
                        )
                        progress_callback(progress_dto)
                else:
                    # stop_requested가 True이면, 아무 작업도 수행하지 않고 조용히 종료합니다.
                    # 이 좀비 스레드의 결과는 버려지며, 어떤 공유 상태도 오염시키지 않습니다.
                    logger.warning(f"  ⚠️ {current_chunk_info_msg}의 최종 처리(진행률, 메타데이터)를 건너뜁니다 (시스템 중지됨).")
            
            logger.debug(f"  {current_chunk_info_msg} 처리 완료 반환: {success}")
            return success



    def start_translation(
        self,
        input_file_path: Union[str, Path],
        output_file_path: Union[str, Path],
        progress_callback: Optional[Callable[[TranslationJobProgressDTO], None]] = None,
        status_callback: Optional[Callable[[str], None]] = None,
        tqdm_file_stream: Optional[Any] = None,
        blocking: bool = False, # blocking 매개변수 추가
        retranslate_failed_only: bool = False # 실패 청크 재번역 모드
    ) -> None:
        # === 용어집 동적 로딩 로직 추가 ===
        try:
            input_p = Path(input_file_path)
            # 설정에서 용어집 파일 접미사를 가져옵니다.
            glossary_suffix = self.config.get("glossary_output_json_filename_suffix", "_simple_glossary.json")
            # 현재 입력 파일에 해당하는 용어집 파일 경로를 추정합니다.
            assumed_glossary_path = input_p.parent / f"{input_p.stem}{glossary_suffix}"

            # config에 설정된 경로와 추정된 경로를 확인합니다.
            # 1. 추정된 경로에 파일이 존재하면, 해당 경로를 사용하도록 설정을 덮어씁니다.
            # 2. 그렇지 않으면, config에 명시된 경로(사용자가 수동으로 지정한 경로)를 그대로 사용합니다.
            
            glossary_to_use = None
            if assumed_glossary_path.exists():
                glossary_to_use = str(assumed_glossary_path)
                logger.info(f"'{input_p.name}'에 대한 용어집 '{assumed_glossary_path.name}'을(를) 자동으로 발견하여 사용합니다.")
            else:
                # 수동으로 설정된 경로가 있다면 그것을 사용합니다.
                manual_path = self.config.get("glossary_json_path")
                if manual_path and Path(manual_path).exists():
                    glossary_to_use = manual_path
                    logger.info(f"자동으로 발견된 용어집이 없어, 설정된 경로 '{manual_path}'의 용어집을 사용합니다.")
                else:
                    logger.info(f"'{input_p.name}'에 대한 용어집을 찾을 수 없어, 용어집 없이 번역을 진행합니다.")

            # 찾은 용어집 경로를 런타임 설정에 반영하여 TranslationService가 로드하도록 합니다.
            if self.translation_service:
                # TranslationService가 새로운 설정을 기반으로 용어집을 다시 로드하도록 합니다.
                self.config['glossary_json_path'] = glossary_to_use
                self.translation_service.config = self.config
                self.translation_service._load_glossary_data() # TranslationService 내부의 용어집 데이터를 갱신합니다.

        except Exception as e:
            logger.error(f"용어집 동적 로딩 중 오류 발생: {e}", exc_info=True)
            # 용어집 로딩에 실패해도 번역은 계속 진행하도록 합니다.
        # === 용어집 동적 로딩 로직 끝 ===
        
        # 스레드 생성 및 시작 부분 수정
        thread = threading.Thread(
            target=self._translation_task, # 실제 번역 로직을 별도 메서드로 분리
            args=(input_file_path, output_file_path, progress_callback, status_callback, tqdm_file_stream, retranslate_failed_only),
            daemon=not blocking # blocking 모드가 아닐 때만 데몬 스레드로 설정
        )
        thread.start()

        if blocking:
            thread.join() # blocking 모드일 경우 스레드가 끝날 때까지 대기


    def _translation_task( # start_translation의 스레드 실행 로직을 이 메서드로 이동
        self,
        input_file_path: Union[str, Path],
        output_file_path: Union[str, Path],
        progress_callback: Optional[Callable[[TranslationJobProgressDTO], None]] = None,
        status_callback: Optional[Callable[[str], None]] = None,
        tqdm_file_stream: Optional[Any] = None,
        retranslate_failed_only: bool = False
    ):
        if not self.translation_service or not self.chunk_service:
            logger.error("번역 서비스 실패: 서비스가 초기화되지 않았습니다.")
            if status_callback: status_callback("오류: 서비스 초기화 실패")
            raise BtgServiceException("번역 서비스가 초기화되지 않았습니다. 설정을 확인하세요.")

        with self._translation_lock:
            if self.is_translation_running:
                logger.warning("번역 작업이 이미 실행 중입니다.")
                if status_callback: status_callback("경고: 번역 작업 이미 실행 중")
                return
            self.is_translation_running = True
            self.stop_requested = False
            self.processed_chunks_count = 0
            self.successful_chunks_count = 0
            self.failed_chunks_count = 0

        logger.info(f"번역 서비스 시작: 입력={input_file_path}, 최종 출력={output_file_path}")
        if status_callback: status_callback("번역 시작됨...")

        # TranslationService에 중단 확인 콜백 설정
        if self.translation_service:
            self.translation_service.set_stop_check_callback(lambda: self.stop_requested)
            logger.debug("TranslationService에 중단 확인 콜백이 설정되었습니다.")

        input_file_path_obj = Path(input_file_path)
        final_output_file_path_obj = Path(output_file_path)
        metadata_file_path = get_metadata_file_path(input_file_path_obj)
        loaded_metadata: Dict[str, Any] = {}
        resume_translation = False
        total_chunks = 0 

        try:
            with self._progress_lock: 
                if metadata_file_path.exists():
                    try:
                        loaded_metadata = load_metadata(metadata_file_path)
                        if loaded_metadata: logger.info(f"기존 메타데이터 로드 성공: {metadata_file_path}")
                        else: logger.warning(f"메타데이터 파일 '{metadata_file_path}'이 비어있거나 유효하지 않습니다. 새로 시작합니다.")
                    except json.JSONDecodeError as json_err:
                        logger.error(f"메타데이터 파일 '{metadata_file_path}' 손상 (JSONDecodeError): {json_err}. 새로 번역을 시작합니다.")
                        delete_file(metadata_file_path) 
                    except Exception as e: 
                        logger.error(f"메타데이터 파일 '{metadata_file_path}' 로드 중 오류: {e}. 새로 번역을 시작합니다.", exc_info=True)
                        delete_file(metadata_file_path) 
                else:
                    logger.info(f"기존 메타데이터 파일을 찾을 수 없습니다: {metadata_file_path}. 새로 시작합니다.")

            current_config_hash = _hash_config_for_metadata(self.config)
            previous_config_hash = loaded_metadata.get("config_hash")

            if previous_config_hash and previous_config_hash == current_config_hash:
                resume_translation = True
                logger.info("설정 해시가 일치하여 이어하기 모드로 설정합니다.")
            elif previous_config_hash: 
                logger.warning("현재 설정이 이전 메타데이터의 설정과 다릅니다. 새로 번역을 시작합니다.")
                loaded_metadata = {} 
                resume_translation = False
            
            file_content = read_text_file(input_file_path_obj)
            if not file_content:
                logger.warning(f"입력 파일이 비어있습니다: {input_file_path_obj}")
                if status_callback: status_callback("완료: 입력 파일 비어있음")
                with self._progress_lock:
                    if not loaded_metadata.get("config_hash"): 
                         loaded_metadata = create_new_metadata(input_file_path_obj, 0, self.config)
                    loaded_metadata["status"] = "completed"; loaded_metadata["total_chunks"] = 0
                    loaded_metadata["last_updated"] = time.time()
                    save_metadata(metadata_file_path, loaded_metadata)
                if progress_callback: progress_callback(TranslationJobProgressDTO(0,0,0,0,"입력 파일 비어있음"))
                with self._translation_lock: self.is_translation_running = False
                return

            all_chunks: List[str] = self.chunk_service.create_chunks_from_file_content(file_content, self.config.get("chunk_size", 6000))
            total_chunks = len(all_chunks) 
            logger.info(f"총 {total_chunks}개의 청크로 분할됨.")

            # 임시 파일(current_run.tmp) 제거, 청크 백업 파일(.chunked.txt)만 단일 소스로 사용
            chunked_output_file_path = final_output_file_path_obj.with_suffix('.chunked.txt')
            with self._progress_lock: 

                if not resume_translation or not loaded_metadata.get("config_hash"): 
                    logger.info("새로운 메타데이터를 생성하거나 덮어씁니다 (새로 시작 또는 설정 변경).")
                    loaded_metadata = create_new_metadata(input_file_path_obj, total_chunks, self.config)
                    logger.info(f"새로 번역을 시작하므로 최종 출력 파일 '{final_output_file_path_obj}'을 삭제하고 새로 생성합니다.")
                    delete_file(final_output_file_path_obj) 
                    final_output_file_path_obj.touch() 
                    # 청크 백업 파일도 초기화
                    delete_file(chunked_output_file_path)
                    chunked_output_file_path.touch()
                else: 
                    if loaded_metadata.get("total_chunks") != total_chunks:
                        logger.warning(f"입력 파일의 청크 수가 변경되었습니다 ({loaded_metadata.get('total_chunks')} -> {total_chunks}). 메타데이터를 새로 생성합니다.")
                        loaded_metadata = create_new_metadata(input_file_path_obj, total_chunks, self.config)
                        resume_translation = False 
                        logger.info(f"청크 수 변경으로 인해 새로 번역을 시작합니다. 최종 출력 파일 '{final_output_file_path_obj}'을 다시 초기화합니다.")
                        delete_file(final_output_file_path_obj); final_output_file_path_obj.touch()
                        # 청크 백업 파일도 초기화
                        delete_file(chunked_output_file_path); chunked_output_file_path.touch()
                    else:
                        logger.info(f"이어하기 모드: 메타데이터 상태를 'in_progress'로 업데이트합니다.")
                    loaded_metadata["status"] = "in_progress" 
                
                loaded_metadata["last_updated"] = time.time()
                save_metadata(metadata_file_path, loaded_metadata)

            # 이어하기 시나리오에서, 혹시 마지막에 불완전한 청크가 있다면 정리(정규식 매칭 가능한 완전 청크만 보존)
            try:
                if chunked_output_file_path.exists():
                    existing_chunks = load_chunks_from_file(chunked_output_file_path)
                    save_merged_chunks_to_file(chunked_output_file_path, existing_chunks)
                    logger.info("청크 파일을 스캔하여 완전한 청크만 유지하도록 정리했습니다.")
            except Exception as sanitize_e:
                logger.warning(f"청크 파일 정리 중 경고: {sanitize_e}")

            chunks_to_process_with_indices: List[Tuple[int, str]] = []
            if retranslate_failed_only:
                if "failed_chunks" in loaded_metadata and loaded_metadata["failed_chunks"]:
                    failed_indices = {int(k) for k in loaded_metadata["failed_chunks"].keys()}
                    for i, chunk_text in enumerate(all_chunks):
                        if i in failed_indices:
                            chunks_to_process_with_indices.append((i, chunk_text))
                    logger.info(f"실패 청크 재번역 모드: {len(chunks_to_process_with_indices)}개 대상.")
                else:
                    logger.info("실패한 청크가 없어 재번역을 건너뜁니다.")
            elif resume_translation and "translated_chunks" in loaded_metadata:
                with self._progress_lock:
                    previously_translated_indices = {int(k) for k in loaded_metadata.get("translated_chunks", {}).keys()}
                    self.successful_chunks_count = len(previously_translated_indices)
                    self.processed_chunks_count = self.successful_chunks_count 
                    self.failed_chunks_count = 0 
                for i, chunk_text in enumerate(all_chunks):
                    if i not in previously_translated_indices:
                        chunks_to_process_with_indices.append((i, chunk_text))
                logger.info(f"이어하기: {self.successful_chunks_count}개 이미 완료, {len(chunks_to_process_with_indices)}개 추가 번역 대상.")
            else: 
                chunks_to_process_with_indices = list(enumerate(all_chunks))
                logger.info(f"새로 번역: {len(chunks_to_process_with_indices)}개 번역 대상.")
                
            if not chunks_to_process_with_indices and total_chunks > 0 : 
                logger.info("번역할 새로운 청크가 없습니다 (모든 청크가 이미 번역됨).")
                if status_callback: status_callback("완료: 모든 청크 이미 번역됨")
                with self._progress_lock:
                    loaded_metadata["status"] = "completed"
                    loaded_metadata["last_updated"] = time.time()
                    save_metadata(metadata_file_path, loaded_metadata)
                if progress_callback:
                    progress_callback(TranslationJobProgressDTO(
                        total_chunks, self.processed_chunks_count, self.successful_chunks_count,
                        self.failed_chunks_count, "모든 청크 이미 번역됨"
                    ))
                if status_callback: status_callback("완료: 모든 청크 이미 번역됨")
                with self._translation_lock: self.is_translation_running = False
                # current_run.tmp는 더 이상 사용하지 않음
                return

            initial_status_msg = "번역 준비 중..."
            if resume_translation: initial_status_msg = f"이어하기 준비 (남은 청크: {len(chunks_to_process_with_indices)})"
            if progress_callback:
                with self._progress_lock:
                    progress_callback(TranslationJobProgressDTO(
                        total_chunks, self.processed_chunks_count, self.successful_chunks_count,
                        self.failed_chunks_count, initial_status_msg,
                        (chunks_to_process_with_indices[0][0] + 1 if chunks_to_process_with_indices else None) 
                    ))
            
            max_workers = self.config.get("max_workers", os.cpu_count() or 1)
            if not isinstance(max_workers, int) or max_workers <= 0:
                logger.warning(f"잘못된 max_workers 값 ({max_workers}), 기본값 (CPU 코어 수 또는 1)으로 설정합니다.")
                max_workers = os.cpu_count() or 1
            
            logger.info(f"최대 {max_workers} 스레드로 병렬 번역 (대상: {len(chunks_to_process_with_indices)} 청크)...")

            pbar = None
            if tqdm_file_stream and chunks_to_process_with_indices: 
                pbar = tqdm(total=len(chunks_to_process_with_indices), 
                            desc="청크 번역", 
                            unit="청크", 
                            file=tqdm_file_stream, 
                            initial=0, 
                            leave=False,
                            smoothing=0.1)


            self.executor = ThreadPoolExecutor(max_workers=max_workers)
            try:
                future_to_chunk_index: Dict[Any, int] = {}
                for i, chunk_text in chunks_to_process_with_indices:
                    if self.stop_requested:
                        logger.info("새 작업 제출 중단됨 (사용자 요청).")
                        break
                    future = self.executor.submit(self._translate_and_save_chunk, i, chunk_text,
                                            chunked_output_file_path,
                                            total_chunks, 
                                            input_file_path_obj, progress_callback)
                    future_to_chunk_index[future] = i

                if self.stop_requested and not future_to_chunk_index:
                    logger.info("번역 시작 전 중지 요청됨.")
                    if pbar: pbar.close()

                # as_completed에서 결과를 기다리되, stop_requested가 True가 되면 루프를 중단합니다.
                for future in as_completed(future_to_chunk_index.keys()):
                    if self.stop_requested:
                        # 실행 중인 future들을 취소합니다.
                        for f in future_to_chunk_index.keys():
                            if not f.done():
                                f.cancel()
                        logger.info("진행 중인 작업들이 취소되었습니다.")
                        break
                    chunk_idx_completed = future_to_chunk_index[future]
                    try:
                        future.result()
                        
                        # 실시간 품질 검사 (최소 5개 이상 처리되었을 때부터)
                        if self.processed_chunks_count >= 5:
                            try:
                                # 메타데이터 로드 (최신 상태 반영)
                                current_metadata = load_metadata(metadata_file_path)
                                suspicious_chunks = self.quality_check_service.analyze_translation_quality(current_metadata)
                                
                                # 이번에 완료된 청크가 의심 목록에 있는지 확인
                                for chunk in suspicious_chunks:
                                    if chunk['chunk_index'] == chunk_idx_completed:
                                        issue_type = chunk['issue_type']
                                        z_score = chunk['z_score']
                                        ratio = chunk['ratio']
                                        
                                        # 원문 미리보기 생성 (사용자가 문제 파악 용이)
                                        source_text = all_chunks[chunk_idx_completed] if chunk_idx_completed < len(all_chunks) else ""
                                        text_preview = source_text[:80].replace('\n', ' ') + ('...' if len(source_text) > 80 else '')
                                        
                                        if issue_type == "omission":
                                            logger.warning(f"⚠️ 번역 누락 의심 (청크 {chunk_idx_completed}): 비율 {ratio:.2f}, Z-Score {z_score} | 원문: {text_preview}")
                                        elif issue_type == "hallucination":
                                            logger.warning(f"⚠️ AI 환각 의심 (청크 {chunk_idx_completed}): 비율 {ratio:.2f}, Z-Score {z_score} | 원문: {text_preview}")
                            except Exception as qc_e:
                                logger.warning(f"품질 검사 중 오류 발생 (무시됨): {qc_e}")

                    except Exception as e_future:
                        logger.error(f"병렬 작업 (청크 {chunk_idx_completed}) 실행 중 오류 (as_completed): {e_future}", exc_info=True)
                    finally:
                        if pbar: pbar.update(1)
            finally:
                if pbar: pbar.close()
                self.executor.shutdown(wait=False) # 모든 스레드가 즉시 종료되도록 보장
                logger.info("ThreadPoolExecutor가 종료되었습니다. 결과 병합을 시작합니다.") 


            logger.info("모든 대상 청크 처리 완료. 결과 병합 및 최종 저장 시작...")
            # 단일 소스(.chunked.txt)에서 최종 병합 대상 로드
            final_merged_chunks: Dict[int, str] = {}
            try:
                final_merged_chunks = load_chunks_from_file(chunked_output_file_path)
                logger.info(f"최종 병합 대상 청크 수: {len(final_merged_chunks)}")
            except Exception as e:
                logger.error(f"청크 파일 '{chunked_output_file_path}' 로드 중 오류: {e}. 최종 저장이 불안정할 수 있습니다.", exc_info=True)
            
            try:
                save_merged_chunks_to_file(final_output_file_path_obj, final_merged_chunks)
                logger.info(f"최종 번역 결과가 '{final_output_file_path_obj}'에 저장되었습니다.")
                  # ✅ 개선: 후처리 실행 (설정에서 활성화된 경우)
                if self.config.get("enable_post_processing", True):
                    logger.info("번역 완료 후 후처리를 시작합니다...")
                    try:
                        # 1. 먼저 청크 인덱스가 포함된 백업 파일 저장 (후처리 전 원본 보존)
                        chunked_backup_path = final_output_file_path_obj.with_suffix('.chunked.txt')
                        save_merged_chunks_to_file(chunked_backup_path, final_merged_chunks)
                        logger.info(f"이어하기용 청크 백업 파일 저장 완료: {chunked_backup_path}")
                        
                        # 2. 청크 단위 후처리 (헤더 제거, HTML 정리 등)
                        processed_chunks = self.post_processing_service.post_process_merged_chunks(final_merged_chunks, self.config)
                        
                        # 3. 후처리된 내용을 임시로 저장 (청크 인덱스는 여전히 포함)
                        save_merged_chunks_to_file(final_output_file_path_obj, processed_chunks)
                        logger.info("청크 단위 후처리 완료 및 임시 저장됨.")
                        
                        # 4. 최종적으로 청크 인덱스 마커 제거 (사용자가 보는 최종 파일)
                        if self.post_processing_service.remove_chunk_indexes_from_final_file(final_output_file_path_obj):
                            logger.info("최종 출력 파일에서 청크 인덱스 마커 제거 완료.")
                        else:
                            logger.warning("청크 인덱스 마커 제거에 실패했습니다.")
                            
                        # 5. 후처리된 내용도 백업 파일에 업데이트 (청크 인덱스 포함)
                        save_merged_chunks_to_file(chunked_backup_path, processed_chunks)
                        logger.info(f"후처리된 청크 백업 파일 업데이트 완료: {chunked_backup_path}")
                        
                    except Exception as post_proc_e:
                        logger.error(f"후처리 중 오류 발생: {post_proc_e}. 후처리를 건너뜁니다.", exc_info=True)
                        # 후처리 실패 시에도 청크 백업 파일은 보장
                        try:
                            chunked_backup_path = final_output_file_path_obj.with_suffix('.chunked.txt')
                            save_merged_chunks_to_file(chunked_backup_path, final_merged_chunks)
                            logger.info(f"후처리 실패 시 원본 청크 백업 파일 저장: {chunked_backup_path}")
                        except Exception as backup_fallback_e:
                            logger.error(f"원본 청크 백업 파일 저장 중 오류: {backup_fallback_e}", exc_info=True)
                else:
                    logger.info("후처리가 설정에서 비활성화되어 건너뜁니다.")
                    # 후처리가 비활성화된 경우에도 청크 백업 파일은 저장 (이어하기용)
                    try:
                        chunked_backup_path = final_output_file_path_obj.with_suffix('.chunked.txt')
                        save_merged_chunks_to_file(chunked_backup_path, final_merged_chunks)
                        logger.info(f"청크 인덱스 포함 백업 파일 저장: {chunked_backup_path} (후처리 비활성화됨)")
                    except Exception as backup_e:
                        logger.error(f"청크 백업 파일 저장 중 오류: {backup_e}", exc_info=True)
                        
            except Exception as e:
                logger.error(f"최종 번역 결과 파일 '{final_output_file_path_obj}' 저장 중 오류: {e}", exc_info=True)
                raise BtgFileHandlerException(f"최종 출력 파일 저장 오류: {e}", original_exception=e)

            # current_run.tmp는 더 이상 사용하지 않음

            final_status_msg = "번역 완료."
            with self._progress_lock:
                # 항상 최신 메타데이터를 다시 로드하여 덮어쓰기 문제를 방지합니다.
                try:
                    # 작업 시작 시점의 메타데이터가 아닌, 파일에 기록된 최신 상태를 가져옵니다.
                    loaded_metadata = load_metadata(metadata_file_path)
                    if not loaded_metadata:
                        logger.warning("최종 메타데이터 저장 단계에서 메타데이터 파일을 로드할 수 없습니다. 새 메타데이터를 생성합니다.")
                        # 이 경우, 정보가 일부 유실될 수 있지만 최소한의 구조는 보존합니다.
                        loaded_metadata = create_new_metadata(input_file_path_obj, total_chunks, self.config)
                    else:
                        logger.info("최종 저장을 위해 최신 메타데이터를 성공적으로 다시 로드했습니다.")
                except Exception as e:
                    logger.error(f"최종 메타데이터 저장 전, 최신 정보 로드 실패: {e}. 일부 정보가 유실될 수 있습니다.")
                    # loaded_metadata는 이전 상태를 유지하지만, 오류를 기록합니다.

                if self.stop_requested:
                    final_status_msg = "번역 중단됨."
                    loaded_metadata["status"] = "stopped"
                elif self.failed_chunks_count > 0:
                    final_status_msg = f"번역 완료 (실패 {self.failed_chunks_count}개 포함)."
                    loaded_metadata["status"] = "completed_with_errors"
                elif self.successful_chunks_count == total_chunks: 
                    final_status_msg = "번역 완료 (모든 청크 성공)."
                    loaded_metadata["status"] = "completed"
                else: 
                    if self.processed_chunks_count == total_chunks: 
                        final_status_msg = f"번역 완료 (성공: {self.successful_chunks_count}/{total_chunks}, 실패: {self.failed_chunks_count})."
                        loaded_metadata["status"] = "completed_with_errors" if self.failed_chunks_count > 0 else "completed_with_pending" 
                    else: 
                        final_status_msg = f"번역 처리됨 (처리 시도: {self.processed_chunks_count}/{total_chunks}, 성공: {self.successful_chunks_count})."
                        loaded_metadata["status"] = "unknown_incomplete" 
                logger.info(f"최종 상태 메시지: {final_status_msg}")

                loaded_metadata["last_updated"] = time.time()
                save_metadata(metadata_file_path, loaded_metadata)

            if status_callback: status_callback(final_status_msg)
            if progress_callback:
                with self._progress_lock:
                    progress_callback(TranslationJobProgressDTO(
                        total_chunks, self.processed_chunks_count,
                        self.successful_chunks_count, self.failed_chunks_count,
                        final_status_msg
                    ))

        except Exception as e:
            logger.error(f"번역 서비스 중 예상치 못한 오류 발생: {e}", exc_info=True)
            if status_callback: status_callback(f"오류 발생: {e}")
            tc_for_error_dto = total_chunks if 'total_chunks' in locals() and total_chunks > 0 else 0
            pc_for_error_dto = self.processed_chunks_count if hasattr(self, 'processed_chunks_count') else 0
            sc_for_error_dto = self.successful_chunks_count if hasattr(self, 'successful_chunks_count') else 0
            fc_for_error_dto = self.failed_chunks_count if hasattr(self, 'failed_chunks_count') else 0

            if progress_callback: progress_callback(TranslationJobProgressDTO(
                tc_for_error_dto, pc_for_error_dto, sc_for_error_dto, fc_for_error_dto, f"오류 발생: {e}"
            ))
            
            with self._progress_lock:
                error_metadata = {}
                try:
                    error_metadata = load_metadata(metadata_file_path) 
                except Exception as load_err:
                    logger.error(f"오류 발생 후 메타데이터 로드 실패: {load_err}")

                if not error_metadata.get("config_hash"): 
                    error_metadata = create_new_metadata(input_file_path_obj, tc_for_error_dto, self.config)

                error_metadata["status"] = "error"
                error_metadata["last_updated"] = time.time()
                if 'total_chunks' in locals(): error_metadata["total_chunks"] = total_chunks 
                save_metadata(metadata_file_path, error_metadata)

            # current_run.tmp는 더 이상 사용하지 않음
            raise BtgServiceException(f"번역 서비스 오류: {e}", original_exception=e) from e
        finally:
            # 중지 요청이 있었던 경우, is_translation_running은 이미 False로 설정되었을 수 있습니다.
            if not self.stop_requested:
                with self._translation_lock:
                    self.is_translation_running = False

    def stop_translation(self):
        if not self.is_translation_running:
            logger.info("번역 작업이 실행 중이 아니므로 중지할 수 없습니다.")
            return

        logger.info("번역 중지를 요청합니다...")
        self.stop_requested = True

        # ThreadPoolExecutor를 직접 종료하여 실행 중인 모든 스레드를 정리합니다.
        if hasattr(self, 'executor') and self.executor:
            # shutdown(wait=False)는 대기 중인 future를 취소하고, 실행 중인 스레드가 완료될 때까지 기다리지 않습니다.
            self.executor.shutdown(wait=False)
            logger.info("ThreadPoolExecutor가 종료되었습니다.")

        # is_translation_running 플래그를 False로 설정하여 새로운 작업이 시작되지 않도록 합니다.
        with self._translation_lock:
            self.is_translation_running = False
        
        logger.info("번역 작업이 중지되었습니다.")

    def request_stop_translation(self):
        if self.is_translation_running:
            logger.info("번역 중지 요청 수신됨.")
            self.stop_translation()
        else:
            logger.info("실행 중인 번역 작업이 없어 중지 요청을 무시합니다.")


if __name__ == '__main__':
    import logging
    from logging import DEBUG # type: ignore

    logger.setLevel(DEBUG)

    test_output_dir = Path("test_app_service_output")
    test_output_dir.mkdir(exist_ok=True)

    temp_config_path = test_output_dir / "temp_config.json"
    sample_app_config_data = {
        "api_key": os.environ.get("GOOGLE_API_KEY", "YOUR_DEFAULT_API_KEY_FOR_TEST"),
        "service_account_file_path": None, 
        "use_vertex_ai": False, 
        "gcp_project": None, 
        "gcp_location": None, 
        "model_name": "gemini-2.0-flash",
        "temperature": 0.7,
        "top_p": 0.9,
        "prompts": "Translate to Korean: {{slot}}",
        "chunk_size": 50, 
        "glossary_json_path": str(test_output_dir / "sample_glossary.json"), # Changed from pronouns_csv
        "requests_per_minute": 60,
        # "max_glossary_entries": 5, # 용어집으로 대체되면서 이 설정은 직접 사용되지 않을 수 있음
        "glossary_sampling_ratio": 100.0, # Changed from pronoun_sample_ratio
        "max_workers": 2 
    }

    with open(temp_config_path, "w", encoding="utf-8") as f:
        json.dump(sample_app_config_data, f, indent=4)

    sample_glossary_file = test_output_dir / "sample_glossary.json" # Changed from sample_pronoun_file
    sample_glossary_content = [
        {"keyword": "BTG", "translated_keyword": "비티지", "source_language": "en", "target_language": "ko", "occurrence_count": 10},
        {"keyword": "Gemini", "translated_keyword": "제미니", "source_language": "en", "target_language": "ko", "occurrence_count": 5}
    ]
    with open(sample_glossary_file, "w", encoding="utf-8") as f: # Changed to JSON
        json.dump(sample_glossary_content, f, indent=4, ensure_ascii=False)

    temp_input_file = test_output_dir / "sample_input.txt"
    temp_input_content = (
        "Hello BTG.\nThis is a test for the Gemini API.\n"
        "We are testing the application service layer.\n"
        "Another line for chunking. And one more for Gemini.\n"
        "This is the fifth line.\nAnd the sixth line is here.\n"
        "Seventh line for more data.\nEighth line, almost done."
    )
    with open(temp_input_file, "w", encoding="utf-8") as f:
        f.write(temp_input_content)

    temp_output_file = test_output_dir / "sample_output.txt"
    
    temp_metadata_file = get_metadata_file_path(temp_input_file)
    if temp_metadata_file.exists():
        delete_file(temp_metadata_file)
    if temp_output_file.exists():
        delete_file(temp_output_file)


    app_service: Optional[AppService] = None
    try:
        app_service = AppService(config_file_path=temp_config_path)
        logger.info("AppService 인스턴스 생성 성공.")
    except Exception as e:
        logger.error(f"AppService 초기화 실패: {e}", exc_info=True)
        exit()

    if app_service and app_service.gemini_client:
        print("\n--- 모델 목록 조회 테스트 ---")
        try:
            models = app_service.get_available_models()
            if models:
                logger.info(f"조회된 모델 수: {len(models)}")
                for m in models[:2]: 
                    logger.info(f"  - {m.get('display_name', m.get('name'))}")
            else:
                logger.info("사용 가능한 모델이 없습니다.")
        except BtgApiClientException as e:
            logger.error(f"모델 목록 조회 실패: {e}")
        except Exception as e_models:
            logger.error(f"모델 목록 조회 중 예상치 못한 오류: {e_models}", exc_info=True)

    else:
        logger.warning("Gemini 클라이언트가 없어 모델 목록 조회 테스트를 건너뜁니다.")

    if app_service and app_service.glossary_service: # Changed from pronoun_service
        print("\n--- 용어집 추출 테스트 ---") # Changed
        try:
            def _glossary_progress_dto_cb(dto: GlossaryExtractionProgressDTO): # Changed DTO and function name
                logger.debug(f"용어집 진행 DTO: {dto.processed_segments}/{dto.total_segments} - {dto.current_status_message} (추출 항목: {dto.extracted_entries_count})") # Changed fields
        
            result_path = app_service.extract_glossary( # Changed method
                temp_input_file,
                progress_callback=_glossary_progress_dto_cb, # Changed callback
                seed_glossary_path=sample_glossary_file # Optionally provide a seed path for testing
            )
            logger.info(f"용어집 추출 완료, 결과 파일: {result_path}") # Changed
        except Exception as e:
            logger.error(f"용어집 추출 테스트 실패: {e}", exc_info=True) # Changed
    else:
        logger.warning("Glossary 서비스가 없어 용어집 추출 테스트를 건너뜁니다.") # Changed

    if app_service and app_service.translation_service and app_service.gemini_client: # Ensure client exists for translation
        print("\n--- 번역 테스트 (병렬 처리) ---")
        try:
            test_tqdm_stream = sys.stdout 

            def _trans_progress_dto(dto: TranslationJobProgressDTO):
                logger.debug(f"번역 진행 DTO: {dto.current_chunk_processing or '-'}/{dto.total_chunks}, 성공: {dto.successful_chunks}, 실패: {dto.failed_chunks} - {dto.current_status_message}")
                pass

            def _trans_status(status_msg):
                logger.info(f"번역 상태: {status_msg}")


            app_service.start_translation(
                temp_input_file,
                temp_output_file,
                _trans_progress_dto,
                _trans_status,
                tqdm_file_stream=test_tqdm_stream 
            )

            start_time = time.time()
            while app_service.is_translation_running and (time.time() - start_time) < 120: 
                time.sleep(0.5)

            if app_service.is_translation_running:
                logger.warning("번역 작업이 시간 내에 완료되지 않았습니다 (테스트). 중지 요청...")
                app_service.request_stop_translation()
                time.sleep(2) 

            if temp_output_file.exists():
                logger.info(f"번역 완료, 결과 파일: {temp_output_file}")
            else:
                logger.error("번역 결과 파일이 생성되지 않았습니다.")
        except Exception as e:
            logger.error(f"번역 테스트 실패: {e}", exc_info=True)
    else:
        logger.warning("Translation 서비스가 없어 번역 테스트를 건너뜁니다.")

    logger.info("AppService 테스트 완료.")
