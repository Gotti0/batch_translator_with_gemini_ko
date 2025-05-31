# app_service.py
from pathlib import Path
# typing 모듈에서 Tuple을 임포트합니다.
from typing import Dict, Any, Optional, List, Callable, Union, Tuple
import os
import json
import csv
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from tqdm import tqdm # tqdm 임포트 확인
import sys # sys 임포트 확인 (tqdm_file_stream=sys.stdout 에 사용될 수 있음)

try:
    from .logger_config import setup_logger
except ImportError:
    from logger_config import setup_logger

try:
    # file_handler에서 필요한 함수들을 import 합니다.
    from .file_handler import (
        read_text_file, write_text_file,
        save_chunk_with_index_to_file, get_metadata_file_path, delete_file,
        load_chunks_from_file,
        create_new_metadata, save_metadata, load_metadata,
        update_metadata_for_chunk_completion,
        _hash_config_for_metadata,
        save_merged_chunks_to_file
    )
    from .config_manager import ConfigManager
    from .gemini_client import GeminiClient, GeminiAllApiKeysExhaustedException, GeminiInvalidRequestException
    from .translation_service import TranslationService # Keep
    from .lorebook_service import LorebookService 
    from .chunk_service import ChunkService
    from .exceptions import BtgServiceException, BtgConfigException, BtgFileHandlerException, BtgApiClientException, BtgTranslationException, BtgBusinessLogicException
    from .dtos import TranslationJobProgressDTO, LorebookExtractionProgressDTO # DTO 임포트 확인
    from .post_processing_service import PostProcessingService
except ImportError:
    # Fallback imports
    from file_handler import (
        read_text_file, write_text_file,
        save_chunk_with_index_to_file, get_metadata_file_path, delete_file,
        load_chunks_from_file,
        create_new_metadata, save_metadata, load_metadata,
        update_metadata_for_chunk_completion,
        _hash_config_for_metadata,
        save_merged_chunks_to_file
    )
    from config_manager import ConfigManager
    from gemini_client import GeminiClient, GeminiAllApiKeysExhaustedException, GeminiInvalidRequestException
    from translation_service import TranslationService
    from lorebook_service import LorebookService
    from chunk_service import ChunkService
    from exceptions import BtgServiceException, BtgConfigException, BtgFileHandlerException, BtgApiClientException, BtgTranslationException, BtgBusinessLogicException
    from dtos import TranslationJobProgressDTO, LorebookExtractionProgressDTO # DTO 임포트 확인
    from post_processing_service import PostProcessingService

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
        self.lorebook_service: Optional[LorebookService] = None # Renamed from pronoun_service
        self.chunk_service = ChunkService()

        self.is_translation_running = False
        self.stop_requested = False
        self._translation_lock = threading.Lock()
        self._progress_lock = threading.Lock()
        self.processed_chunks_count = 0
        self.successful_chunks_count = 0
        self.failed_chunks_count = 0
        self.post_processing_service = PostProcessingService()

        self.load_app_config()

    def load_app_config(self) -> Dict[str, Any]:
        logger.info("애플리케이션 설정 로드 중...")
        try:
            self.config = self.config_manager.load_config()
            logger.info("애플리케이션 설정 로드 완료.")

            auth_credentials_for_gemini_client: Optional[Union[str, List[str], Dict[str, Any]]] = None
            use_vertex = self.config.get("use_vertex_ai", False)
            gcp_project_from_config = self.config.get("gcp_project")
            gcp_location = self.config.get("gcp_location")
            sa_file_path_str = self.config.get("service_account_file_path")

            logger.debug(f"[AppService.load_app_config] Vertex AI 사용 여부 (use_vertex): {use_vertex}")
            logger.debug(f"[AppService.load_app_config] 설정 파일 내 GCP 프로젝트 (gcp_project_from_config): '{gcp_project_from_config}'")
            logger.debug(f"[AppService.load_app_config] 설정 파일 내 GCP 위치 (gcp_location): '{gcp_location}'")
            logger.debug(f"[AppService.load_app_config] 서비스 계정 파일 경로 (sa_file_path_str): '{sa_file_path_str}'")

            if use_vertex:
                logger.info("Vertex AI 사용 모드입니다.")
                if sa_file_path_str:
                    sa_file_path = Path(sa_file_path_str)
                    if sa_file_path.is_file():
                        try:
                            auth_credentials_for_gemini_client = read_text_file(sa_file_path)
                            logger.info(f"Vertex AI 서비스 계정 파일 로드 성공: {sa_file_path}")
                        except Exception as e:
                            logger.error(f"Vertex AI 서비스 계정 파일 읽기 실패 ({sa_file_path}): {e}")
                            auth_credentials_for_gemini_client = None
                    else:
                        logger.warning(f"Vertex AI 서비스 계정 파일 경로가 유효하지 않거나 파일이 아닙니다: {sa_file_path_str}")
                        auth_credentials_for_gemini_client = self.config.get("auth_credentials")
                        if auth_credentials_for_gemini_client:
                             logger.info("서비스 계정 파일 경로가 유효하지 않아 'auth_credentials' 값을 직접 사용 시도합니다.")
                elif self.config.get("auth_credentials"):
                    auth_credentials_for_gemini_client = self.config.get("auth_credentials")
                    logger.info("Vertex AI 사용으로 설정되었으나 서비스 계정 파일 경로가 없어, 'auth_credentials' 값을 직접 사용 시도합니다.")
            else:
                logger.info("Gemini Developer API 사용 모드입니다.")
                api_keys_list = self.config.get("api_keys", [])
                if api_keys_list:
                    auth_credentials_for_gemini_client = api_keys_list
                    logger.info(f"{len(api_keys_list)}개의 API 키 목록을 사용합니다.")
                elif self.config.get("api_key"):
                    auth_credentials_for_gemini_client = [self.config.get("api_key")]
                    logger.info("단일 API 키를 사용합니다 (api_keys 목록이 비어 있음).")
                elif self.config.get("auth_credentials"):
                    auth_credentials_for_gemini_client = [self.config.get("auth_credentials")]
                    logger.info("auth_credentials 값을 API 키로 사용 시도합니다 (api_key, api_keys 모두 없음).")
                else:
                    logger.warning("Gemini Developer API 모드이지만 사용할 API 키가 설정에 없습니다.")
                    auth_credentials_for_gemini_client = None

            should_initialize_client = False
            if auth_credentials_for_gemini_client:
                if isinstance(auth_credentials_for_gemini_client, str) and auth_credentials_for_gemini_client.strip():
                    should_initialize_client = True
                elif isinstance(auth_credentials_for_gemini_client, list) and auth_credentials_for_gemini_client:
                    should_initialize_client = True
                elif isinstance(auth_credentials_for_gemini_client, dict):
                    should_initialize_client = True
            elif use_vertex and not auth_credentials_for_gemini_client and \
                 (gcp_project_from_config or os.environ.get("GOOGLE_CLOUD_PROJECT")): # ADC 사용 기대
                should_initialize_client = True
                logger.info("Vertex AI 사용 및 프로젝트 ID 존재 (설정 또는 환경변수)로 클라이언트 초기화 조건 충족 (인증정보는 ADC 기대).")


            logger.debug(f"[AppService.load_app_config] GeminiClient 초기화 전: should_initialize_client={should_initialize_client}")
            logger.debug(f"[AppService.load_app_config] auth_credentials_for_gemini_client 타입: {type(auth_credentials_for_gemini_client)}")
            if isinstance(auth_credentials_for_gemini_client, str) and len(auth_credentials_for_gemini_client) > 200:
                 logger.debug(f"[AppService.load_app_config] auth_credentials_for_gemini_client (일부): {auth_credentials_for_gemini_client[:100]}...{auth_credentials_for_gemini_client[-100:]}")
            elif isinstance(auth_credentials_for_gemini_client, dict):
                 logger.debug(f"[AppService.load_app_config] auth_credentials_for_gemini_client (키 목록): {list(auth_credentials_for_gemini_client.keys())}")
            else:
                 logger.debug(f"[AppService.load_app_config] auth_credentials_for_gemini_client: {auth_credentials_for_gemini_client}")

            if should_initialize_client:
                try:
                    project_to_pass_to_client = gcp_project_from_config if gcp_project_from_config and gcp_project_from_config.strip() else None
                    rpm_value = self.config.get("requests_per_minute")
                    logger.info(f"GeminiClient 초기화 시도: project='{project_to_pass_to_client}', location='{gcp_location}', RPM='{rpm_value}'")
                    self.gemini_client = GeminiClient(
                        auth_credentials=auth_credentials_for_gemini_client,
                        project=project_to_pass_to_client,
                        location=gcp_location,
                        requests_per_minute=rpm_value
                    )
                except GeminiInvalidRequestException as e_inv:
                    logger.error(f"GeminiClient 초기화 실패 (잘못된 요청/인증): {e_inv}")
                    self.gemini_client = None
                except Exception as e_client:
                    logger.error(f"GeminiClient 초기화 중 예상치 못한 오류 발생: {e_client}", exc_info=True)
                    self.gemini_client = None
            else:
                logger.warning("API 키 또는 Vertex AI 설정이 충분하지 않아 Gemini 클라이언트 초기화를 시도하지 않습니다.")
                self.gemini_client = None

            if self.gemini_client:
                self.translation_service = TranslationService(self.gemini_client, self.config)
                self.lorebook_service = LorebookService(self.gemini_client, self.config) # Changed to LorebookService
                logger.info("TranslationService 및 LorebookService가 성공적으로 초기화되었습니다.") # Message updated
            else:
                self.translation_service = None
                self.lorebook_service = None # Renamed
                logger.warning("Gemini 클라이언트가 초기화되지 않아 번역 및 고유명사 서비스가 비활성화됩니다.")

            return self.config
        except FileNotFoundError as e:
            logger.error(f"설정 파일 찾기 실패: {e}")
            self.config = self.config_manager.get_default_config()
            logger.warning("기본 설정으로 계속 진행합니다. Gemini 클라이언트는 초기화되지 않을 수 있습니다.")
            self.gemini_client = None
            self.translation_service = None # Keep
            self.lorebook_service = None # Renamed
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
                self.load_app_config()
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
            logger.error(f"모델 목록 조회 중 예상치 못한 오류: {e}", exc_info=True)
            raise BtgServiceException(f"모델 목록 조회 중 오류: {e}", original_exception=e) from e

    def extract_lorebook( # Renamed from extract_pronouns
        self,
        input_file_path: Union[str, Path],
        progress_callback: Optional[Callable[[LorebookExtractionProgressDTO], None]] = None, # DTO Changed
        novel_language_code: Optional[str] = None, # 명시적 언어 코드 전달
        seed_lorebook_path: Optional[Union[str, Path]] = None # CLI에서 전달된 시드 로어북 경로
        # tqdm_file_stream is not typically used by lorebook extraction directly in AppService,
        # but can be passed down if LorebookService supports it (currently it doesn't directly)
        # For CLI, tqdm is handled in the CLI module itself.
    ) -> Path:
        if not self.lorebook_service: # Changed from pronoun_service
            logger.error("로어북 추출 서비스 실패: 서비스가 초기화되지 않았습니다.") # Message updated
            raise BtgServiceException("로어북 추출 서비스가 초기화되지 않았습니다. 설정을 확인하세요.") # Message updated

        logger.info(f"로어북 추출 서비스 시작: {input_file_path}, 시드 파일: {seed_lorebook_path}") # Message updated
        try:
            file_content = read_text_file(input_file_path)
            if not file_content:
                logger.warning(f"입력 파일이 비어있습니다: {input_file_path}")
                # For lorebook, an empty input means an empty lorebook, unless a seed is provided.
                # LorebookService.extract_and_save_lorebook handles empty content.

            # 로어북 추출 시 사용할 언어 코드 결정
            # 1. 명시적으로 전달된 novel_language_code
            # 2. 설정 파일의 novel_language (통합됨)
            # 3. None (LorebookService에서 자체적으로 처리하거나 언어 특정 기능 비활성화)
            lang_code_for_extraction = novel_language_code or self.config.get("novel_language") # 통합된 설정 사용
            result_path = self.lorebook_service.extract_and_save_lorebook( # Method changed
                file_content, # Pass content directly
                input_file_path, 
                lang_code_for_extraction, # 결정된 언어 코드 전달
                progress_callback, # 콜백 위치 변경
                seed_lorebook_path=seed_lorebook_path # 시드 로어북 경로 전달
            )
            logger.info(f"로어북 추출 완료. 결과 파일: {result_path}") # Message updated

            return result_path
        except FileNotFoundError as e:
            logger.error(f"로어북 추출을 위한 입력 파일을 찾을 수 없습니다: {input_file_path}") # Message updated
            if progress_callback:
                progress_callback(LorebookExtractionProgressDTO(0,0,f"오류: 입력 파일 없음 - {e.filename}",0)) # DTO Changed
            raise BtgFileHandlerException(f"입력 파일 없음: {input_file_path}", original_exception=e) from e
        except (BtgBusinessLogicException, BtgApiClientException) as e: # BtgPronounException replaced with BtgBusinessLogicException
            logger.error(f"로어북 추출 중 오류: {e}") # Message updated
            if progress_callback:
                progress_callback(LorebookExtractionProgressDTO(0,0,f"오류: {e}",0)) # DTO Changed
            raise
        except Exception as e: 
            logger.error(f"로어북 추출 서비스 중 예상치 못한 오류: {e}", exc_info=True)  # Message updated
            if progress_callback:
                progress_callback(LorebookExtractionProgressDTO(0,0,f"예상치 못한 오류: {e}",0)) # DTO Changed
            raise BtgServiceException(f"로어북 추출 중 오류: {e}", original_exception=e) from e # Message updated


    def _translate_and_save_chunk(self, chunk_index: int, chunk_text: str,
                            current_run_output_file: Path,
                            total_chunks: int,
                            input_file_path_for_metadata: Path,
                            progress_callback: Optional[Callable[[TranslationJobProgressDTO], None]] = None) -> bool:
        current_chunk_info_msg = f"청크 {chunk_index + 1}/{total_chunks}"
        
        # 청크 분석 및 상세 정보 로깅
        chunk_lines = chunk_text.count('\n') + 1
        chunk_words = len(chunk_text.split())
        chunk_chars = len(chunk_text)
        chunk_preview = chunk_text[:100].replace('\n', ' ') + '...' if len(chunk_text) > 100 else chunk_text
        
        logger.info(f"{current_chunk_info_msg} 처리 시작")
        logger.info(f"  📝 청크 내용 미리보기: {chunk_preview}")
        logger.debug(f"  📊 청크 통계: 글자 수={chunk_chars}, 단어 수={chunk_words}, 줄 수={chunk_lines}")
        
        start_time = time.time()
        last_error = None
        success = False

        

        try:
            if self.stop_requested:
                logger.info(f"{current_chunk_info_msg} ⏸️ 처리 중지됨 (사용자 요청)")
                return False

            if not self.translation_service:
                raise BtgServiceException("TranslationService가 초기화되지 않았습니다.")

            # 번역 설정 정보 로깅
            use_content_safety_retry = self.config.get("use_content_safety_retry", True)
            max_split_attempts = self.config.get("max_content_safety_split_attempts", 3)
            min_chunk_size = self.config.get("min_content_safety_chunk_size", 100)
            model_name = self.config.get("model_name", "gemini-1.5-flash-latest")
            
            logger.debug(f"  ⚙️ 번역 설정: 모델={model_name}, 안전재시도={use_content_safety_retry}")
            if use_content_safety_retry:
                logger.debug(f"  🔄 검열 재시도 설정: 최대시도={max_split_attempts}, 최소크기={min_chunk_size}")
                logger.info(f"  🔄 {current_chunk_info_msg} 번역 API 호출 시작...")
                translation_start_time = time.time()
                
            if use_content_safety_retry:
                logger.debug(f"  🛡️ 콘텐츠 안전 재시도 모드로 번역 시작")
                translated_chunk = self.translation_service.translate_text_with_content_safety_retry(
                    chunk_text, max_split_attempts, min_chunk_size
                )
            else:
                logger.debug(f"  📝 일반 번역 모드로 번역 시작")
                translated_chunk = self.translation_service.translate_text(chunk_text)
            
            translation_time = time.time() - translation_start_time
            translated_length = len(translated_chunk) if translated_chunk else 0
            
            logger.info(f"  ✅ {current_chunk_info_msg} 번역 완료 (소요: {translation_time:.2f}초)")
            logger.debug(f"    번역 결과 길이: {translated_length} 글자")
            logger.debug(f"    번역 속도: {chunk_chars/translation_time:.1f} 글자/초" if translation_time > 0 else "    번역 속도: 즉시 완료")# 새로운 검열 재시도 로직 사용
            # 파일 저장 과정 로깅
            logger.debug(f"  💾 {current_chunk_info_msg} 결과 저장 시작...")
            save_start_time = time.time()
            
            save_chunk_with_index_to_file(current_run_output_file, chunk_index, translated_chunk)
            
            save_time = time.time() - save_start_time
            logger.debug(f"  💾 파일 저장 완료 (소요: {save_time:.3f}초)")
            
            success = True
            
            total_processing_time = time.time() - start_time
            logger.info(f"  🎯 {current_chunk_info_msg} 전체 처리 완료 (총 소요: {total_processing_time:.2f}초)")

        except BtgTranslationException as e_trans:
            processing_time = time.time() - start_time
            logger.error(f"  ❌ {current_chunk_info_msg} 번역 실패 (소요: {processing_time:.2f}초)")
            logger.error(f"    오류 유형: 번역 서비스 오류")
            logger.error(f"    오류 내용: {e_trans}")
            
            if "콘텐츠 안전 문제" in str(e_trans):
                logger.warning(f"    🛡️ 콘텐츠 검열로 인한 실패")
            
            save_chunk_with_index_to_file(current_run_output_file, chunk_index, f"[번역 실패: {e_trans}]")
            last_error = str(e_trans)
            success = False

        except BtgApiClientException as e_api:
            processing_time = time.time() - start_time
            logger.error(f"  ❌ {current_chunk_info_msg} API 오류로 번역 실패 (소요: {processing_time:.2f}초)")
            logger.error(f"    오류 유형: API 클라이언트 오류")
            logger.error(f"    오류 내용: {e_api}")
            
            # API 오류 유형별 분류
            if "사용량 제한" in str(e_api) or "429" in str(e_api):
                logger.warning(f"    ⚠️ API 사용량 제한 오류")
            elif "키" in str(e_api).lower() or "인증" in str(e_api):
                logger.warning(f"    🔑 API 인증 관련 오류")
            
            save_chunk_with_index_to_file(current_run_output_file, chunk_index, f"[API 오류로 번역 실패: {e_api}]")
            last_error = str(e_api)
            success = False

        except Exception as e_gen:
            processing_time = time.time() - start_time
            logger.error(f"  ❌ {current_chunk_info_msg} 예상치 못한 오류 (소요: {processing_time:.2f}초)", exc_info=True)
            logger.error(f"    오류 유형: {type(e_gen).__name__}")
            logger.error(f"    오류 내용: {e_gen}")
            
            save_chunk_with_index_to_file(current_run_output_file, chunk_index, f"[알 수 없는 오류로 번역 실패: {e_gen}]")
            last_error = str(e_gen)
            success = False
                    

        
        finally:
            total_time = time.time() - start_time
            with self._progress_lock:
                # 1단계: 먼저 processed_chunks_count 증가
                self.processed_chunks_count += 1
                
                # 2단계: 결과에 따라 성공/실패 카운트 업데이트
                if success:
                    self.successful_chunks_count += 1
                    # 메타데이터 업데이트
                elif not self.stop_requested:
                    self.failed_chunks_count += 1
                
                # 3단계: 모든 카운트 업데이트 완료 후 진행률 계산
                progress_percentage = (self.processed_chunks_count / total_chunks) * 100
                logger.info(f"  📈 전체 진행률: {progress_percentage:.1f}% ({self.processed_chunks_count}/{total_chunks})")
                
                # 성공률 계산
                if self.processed_chunks_count > 0:
                    success_rate = (self.successful_chunks_count / self.processed_chunks_count) * 100
                    logger.info(f"  📊 성공률: {success_rate:.1f}% (성공: {self.successful_chunks_count}, 실패: {self.failed_chunks_count})")

                # 예상 완료 시간 계산 (선택사항)
                if total_time > 0 and self.processed_chunks_count > 0:
                    avg_time_per_chunk = total_time / 1  # 현재 청크 기준
                    remaining_chunks = total_chunks - self.processed_chunks_count
                    estimated_remaining_time = remaining_chunks * avg_time_per_chunk
                    logger.debug(f"  ⏱️ 예상 남은 시간: {estimated_remaining_time:.1f}초 (평균 {avg_time_per_chunk:.2f}초/청크)")


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
                
            logger.debug(f"  🏁 {current_chunk_info_msg} 처리 완료 반환: {success}")
            return success



    def start_translation(
        self,
        input_file_path: Union[str, Path],
        output_file_path: Union[str, Path],
        progress_callback: Optional[Callable[[TranslationJobProgressDTO], None]] = None,
        status_callback: Optional[Callable[[str], None]] = None,
        tqdm_file_stream: Optional[Any] = None 
    ) -> None:
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

            current_run_output_file_path = final_output_file_path_obj.with_suffix(final_output_file_path_obj.suffix + '.current_run.tmp')
            with self._progress_lock: 
                delete_file(current_run_output_file_path) 
                current_run_output_file_path.touch()

                if not resume_translation or not loaded_metadata.get("config_hash"): 
                    logger.info("새로운 메타데이터를 생성하거나 덮어씁니다 (새로 시작 또는 설정 변경).")
                    loaded_metadata = create_new_metadata(input_file_path_obj, total_chunks, self.config)
                    logger.info(f"새로 번역을 시작하므로 최종 출력 파일 '{final_output_file_path_obj}'을 삭제하고 새로 생성합니다.")
                    delete_file(final_output_file_path_obj) 
                    final_output_file_path_obj.touch() 
                else: 
                    if loaded_metadata.get("total_chunks") != total_chunks:
                        logger.warning(f"입력 파일의 청크 수가 변경되었습니다 ({loaded_metadata.get('total_chunks')} -> {total_chunks}). 메타데이터를 새로 생성합니다.")
                        loaded_metadata = create_new_metadata(input_file_path_obj, total_chunks, self.config)
                        resume_translation = False 
                        logger.info(f"청크 수 변경으로 인해 새로 번역을 시작합니다. 최종 출력 파일 '{final_output_file_path_obj}'을 다시 초기화합니다.")
                        delete_file(final_output_file_path_obj); final_output_file_path_obj.touch()
                    else:
                        logger.info(f"이어하기 모드: 메타데이터 상태를 'in_progress'로 업데이트합니다.")
                    loaded_metadata["status"] = "in_progress" 
                
                loaded_metadata["last_updated"] = time.time()
                save_metadata(metadata_file_path, loaded_metadata)

            chunks_to_process_with_indices: List[Tuple[int, str]] = []
            if resume_translation and "translated_chunks" in loaded_metadata:
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
                with self._translation_lock: self.is_translation_running = False
                delete_file(current_run_output_file_path) 
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
                            leave=False)


            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_to_chunk_index: Dict[Any, int] = {}
                for i, chunk_text in chunks_to_process_with_indices:
                    if self.stop_requested:
                        logger.info("새 작업 제출 중단됨 (사용자 요청).")
                        break
                    future = executor.submit(self._translate_and_save_chunk, i, chunk_text,
                                    current_run_output_file_path,
                                    total_chunks, 
                                    input_file_path_obj, progress_callback)
                    future_to_chunk_index[future] = i

                if self.stop_requested and not future_to_chunk_index: 
                     logger.info("번역 시작 전 중지 요청됨.")
                     if pbar: pbar.close() 

                for future in as_completed(future_to_chunk_index.keys()):
                    chunk_idx_completed = future_to_chunk_index[future]
                    try:
                        future.result() 
                    except Exception as e_future:
                        logger.error(f"병렬 작업 (청크 {chunk_idx_completed + 1}) 실행 중 오류 (as_completed): {e_future}", exc_info=True)
                    finally:
                        if pbar: pbar.update(1) 

            if pbar: pbar.close() 


            logger.info("모든 대상 청크 처리 완료. 결과 병합 및 최종 저장 시작...")
            newly_translated_chunks: Dict[int, str] = {}
            previously_translated_chunks_from_main_output: Dict[int, str] = {}

            try:
                newly_translated_chunks = load_chunks_from_file(current_run_output_file_path)
            except Exception as e:
                logger.error(f"임시 번역 파일 '{current_run_output_file_path}' 로드 중 오류: {e}. 병합이 불안정할 수 있습니다.", exc_info=True)

            final_merged_chunks: Dict[int, str] = {}
            if resume_translation and final_output_file_path_obj.exists(): 
                logger.info(f"이전 번역 결과 파일 '{final_output_file_path_obj}'에서 청크를 로드합니다.")
                try:
                    previously_translated_chunks_from_main_output = load_chunks_from_file(final_output_file_path_obj)
                    final_merged_chunks.update(previously_translated_chunks_from_main_output)
                    logger.info(f"{len(previously_translated_chunks_from_main_output)}개의 이전 청크 로드됨.")
                except Exception as e:
                    logger.error(f"이전 최종 출력 파일 '{final_output_file_path_obj}' 로드 중 오류: {e}. 이전 내용은 병합되지 않을 수 있습니다.", exc_info=True)

            final_merged_chunks.update(newly_translated_chunks) 
            logger.info(f"{len(newly_translated_chunks)}개의 새 청크 추가/덮어쓰기됨. 총 {len(final_merged_chunks)} 청크 병합 준비 완료.")

            try:
                save_merged_chunks_to_file(final_output_file_path_obj, final_merged_chunks)
                logger.info(f"최종 번역 결과가 '{final_output_file_path_obj}'에 저장되었습니다.")
            except Exception as e:
                logger.error(f"최종 번역 결과 파일 '{final_output_file_path_obj}' 저장 중 오류: {e}", exc_info=True)
                raise BtgFileHandlerException(f"최종 출력 파일 저장 오류: {e}", original_exception=e)

            delete_file(current_run_output_file_path)
            logger.info(f"임시 파일 '{current_run_output_file_path}' 삭제됨.")

            final_status_msg = "번역 완료."
            with self._progress_lock:
                if self.stop_requested:
                    # 중단 시 최신 메타데이터 재로드하여 완료된 청크 정보 보존
                    try:
                        current_metadata = load_metadata(metadata_file_path)
                        if current_metadata and current_metadata.get("translated_chunks"):
                            # 최신 메타데이터가 있으면 이를 사용
                            loaded_metadata = current_metadata
                            logger.info(f"중단 시 최신 메타데이터 재로드 완료. 보존된 청크: {len(current_metadata.get('translated_chunks', {}))}")
                        else:
                            logger.warning("중단 시 최신 메타데이터 로드 실패 또는 빈 데이터, 시작 시점 메타데이터 사용")
                    except Exception as e:
                        logger.error(f"중단 시 최신 메타데이터 재로드 실패: {e}. 시작 시점 메타데이터 사용")
                    
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

            if 'current_run_output_file_path' in locals() and current_run_output_file_path.exists():
                delete_file(current_run_output_file_path)
                logger.info(f"오류 발생으로 임시 파일 '{current_run_output_file_path}' 삭제 시도됨.")
            raise BtgServiceException(f"번역 서비스 오류: {e}", original_exception=e) from e
        finally:
            with self._translation_lock:
                self.is_translation_running = False

        try:
            # 1단계: 청크 내용 후처리 (청크 인덱스는 유지)
            logger.info("번역 결과 청크 내용 후처리 시작...")
            processed_chunks = self.post_processing_service.post_process_merged_chunks(final_merged_chunks)
            
            # 2단계: 후처리된 청크들을 파일에 저장 (청크 인덱스 포함)
            save_merged_chunks_to_file(final_output_file_path_obj, processed_chunks)
            logger.info(f"후처리된 번역 결과가 '{final_output_file_path_obj}'에 저장되었습니다.")
            
            # 3단계: 최종 파일에서 청크 인덱스 마커들 제거
            logger.info("최종 파일에서 청크 인덱스 제거 중...")
            index_removal_success = self.post_processing_service.remove_chunk_indexes_from_final_file(final_output_file_path_obj)
            
            if index_removal_success:
                logger.info(f"최종 후처리 완료: '{final_output_file_path_obj}' (청크 인덱스 제거됨)")
            else:
                logger.warning(f"청크 인덱스 제거에 실패했지만 번역 파일은 저장됨: '{final_output_file_path_obj}'")
            
        except Exception as e:
            logger.error(f"최종 번역 결과 파일 '{final_output_file_path_obj}' 저장 중 오류: {e}", exc_info=True)
            raise BtgFileHandlerException(f"최종 출력 파일 저장 오류: {e}", original_exception=e)

    def request_stop_translation(self):
        if self.is_translation_running:
            logger.info("번역 중지 요청 수신됨.")
            with self._translation_lock: 
                self.stop_requested = True
        else:
            logger.info("실행 중인 번역 작업이 없어 중지 요청을 무시합니다.")


if __name__ == '__main__':
    import logging

    logger.setLevel(logging.DEBUG) 

    test_output_dir = Path("test_app_service_output")
    test_output_dir.mkdir(exist_ok=True)

    temp_config_path = test_output_dir / "temp_config.json"
    sample_app_config_data = {
        "api_key": os.environ.get("GOOGLE_API_KEY", "YOUR_DEFAULT_API_KEY_FOR_TEST"),
        "service_account_file_path": None, 
        "use_vertex_ai": False, 
        "gcp_project": None, 
        "gcp_location": None, 
        "model_name": "gemini-1.5-flash-latest",
        "temperature": 0.7,
        "top_p": 0.9,
        "prompts": "Translate to Korean: {{slot}}",
        "chunk_size": 50, 
        "pronouns_csv": str(test_output_dir / "sample_pronouns.csv"),
        "requests_per_minute": 60,
        # "max_pronoun_entries": 5, # 로어북으로 대체되면서 이 설정은 직접 사용되지 않을 수 있음
        "pronoun_sample_ratio": 100.0, 
        "max_workers": 2 
    }

    with open(temp_config_path, "w", encoding="utf-8") as f:
        json.dump(sample_app_config_data, f, indent=4)

    sample_pronoun_file = test_output_dir / "sample_pronouns.csv"
    with open(sample_pronoun_file, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["외국어", "한국어", "등장횟수"]) 
        writer.writerow(["BTG", "비티지", "10"])
        writer.writerow(["Gemini", "제미니", "5"])

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

    if app_service and app_service.lorebook_service: # Changed from pronoun_service
        print("\n--- 로어북 추출 테스트 ---") # Changed
        try:
            def _lorebook_progress_dto_cb(dto: LorebookExtractionProgressDTO): # Changed DTO
                logger.debug(f"로어북 진행 DTO: {dto.processed_segments}/{dto.total_segments} - {dto.current_status_message} (추출 항목: {dto.extracted_entries_count})") # Changed fields

            result_path = app_service.extract_lorebook( # Changed method
                temp_input_file,
                progress_callback=_lorebook_progress_dto_cb,
                seed_lorebook_path=None # Optionally provide a seed path for testing
            )
            logger.info(f"로어북 추출 완료, 결과 파일: {result_path}") # Changed
        except Exception as e:
            logger.error(f"로어북 추출 테스트 실패: {e}", exc_info=True) # Changed
    else:
        logger.warning("Lorebook 서비스가 없어 로어북 추출 테스트를 건너뜁니다.") # Changed

    if app_service and app_service.translation_service:
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
