# app_service.py
from pathlib import Path
# typing 모듈에서 Tuple을 임포트합니다.
from typing import Dict, Any, Optional, List, Callable, Union, Tuple
import os
import json
import csv
import logging
import asyncio  # asyncio 임포트 추가
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
    from ..core.config.config_manager import ConfigManager, DEFAULT_REQUESTS_PER_MINUTE
    from infrastructure.gemini_client import GeminiClient, GeminiAllApiKeysExhaustedException, GeminiInvalidRequestException, GeminiServiceUnavailableException
    from domain.translation_service import TranslationService
    from domain.glossary_service import SimpleGlossaryService
    from ..utils.chunk_service import ChunkService
    from ..core.exceptions import BtgServiceException, BtgConfigException, BtgFileHandlerException, BtgApiClientException, BtgTranslationException, BtgBusinessLogicException
    from ..core.dtos import TranslationJobProgressDTO, GlossaryExtractionProgressDTO
    from ..utils.post_processing_service import PostProcessingService
    from ..utils.quality_check_service import QualityCheckService
    from app.batch_translation_service import BatchTranslationService, BatchSummary
    from infrastructure.embedding_client import create_embedding_client
    from domain.translation_memory import TranslationMemoryStore
    from utils.lang_utils import normalize_language_code
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
    from core.config.config_manager import ConfigManager, DEFAULT_REQUESTS_PER_MINUTE
    from infrastructure.gemini_client import GeminiClient, GeminiAllApiKeysExhaustedException, GeminiInvalidRequestException, GeminiServiceUnavailableException
    from domain.translation_service import TranslationService
    from domain.glossary_service import SimpleGlossaryService
    from utils.chunk_service import ChunkService
    from core.exceptions import BtgServiceException, BtgConfigException, BtgFileHandlerException, BtgApiClientException, BtgTranslationException, BtgBusinessLogicException
    from core.dtos import TranslationJobProgressDTO, GlossaryExtractionProgressDTO
    from utils.post_processing_service import PostProcessingService
    from utils.quality_check_service import QualityCheckService
    from infrastructure.llm_client_factory import LLMClientFactory
    from infrastructure.base_client import BaseLLMClient
    from app.batch_translation_service import BatchTranslationService, BatchSummary
    from infrastructure.embedding_client import create_embedding_client
    from domain.translation_memory import TranslationMemoryStore
    from utils.lang_utils import normalize_language_code

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
        # 배치 번역 클라이언트 생성 함수 (API 키 → GeminiBatchClient). None이면 기본 구현, 테스트에서 교체한다.
        self.batch_client_factory: Optional[Callable[[str], Any]] = None
        # 임베딩 클라이언트 생성 함수 (설정 → BaseEmbeddingClient). None이면 기본 구현, 테스트에서 교체한다.
        self.embedding_client_factory: Optional[Callable[[Dict[str, Any]], Any]] = None

        # === 비동기 마이그레이션: Lock 제거, Task 객체 기반 상태 관리 ===
        # 기존 상태 플래그 제거 (asyncio는 단일 스레드)
        # self.is_translation_running: bool
        # self.stop_requested: bool
        
        # Task 객체로 상태 관리 (Lock 불필요)
        self.current_translation_task: Optional[asyncio.Task] = None
        self.current_glossary_task: Optional[asyncio.Task] = None
        
        # 취소 신호 이벤트 (Promise.race 패턴)
        self.cancel_event: asyncio.Event = asyncio.Event()
        self.cancel_glossary_event: asyncio.Event = asyncio.Event()
        
        # 카운터 (asyncio 단일 스레드이므로 Lock 불필요)
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

            provider = str(self.config.get("llm_provider", "gemini")).lower().strip()
            should_initialize_client = False

            if provider in ("claude_cli", "codex_cli", "antigravity_cli"):
                should_initialize_client = True
                logger.info(f"로컬 CLI 런타임 프로바이더 활성화: {provider}")
            elif provider == "ollama":
                should_initialize_client = True
                logger.info("Ollama 프로바이더 활성화")
            elif provider == "openai_compatible":
                if self.config.get("openai_compatible_base_url"):
                    should_initialize_client = True
                    logger.info("OpenAI 호환 API 프로바이더 활성화")
                else:
                    logger.warning("OpenAI 호환 API Base URL이 지정되지 않았습니다.")
            else:
                # Gemini 공급자
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
                    logger.info(f"LLMClientFactory를 통한 클라이언트 초기화 (프로바이더: {provider})")
                    self.gemini_client = LLMClientFactory.create_client(
                        config=self.config,
                        auth_credentials=auth_credentials_for_gemini_client,
                        requests_per_minute=self.config.get("requests_per_minute"),
                    )
                except Exception as e_client:
                    logger.error(f"{provider} 클라이언트 초기화 오류: {e_client}", exc_info=True)
                    self.gemini_client = None
            else:
                logger.warning(f"설정이 충분하지 않아 {provider} 클라이언트 초기화를 시도하지 않습니다.")
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

    async def get_available_models(self, config_override: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        사용 가능한 모델 목록을 조회합니다.

        Args:
            config_override: 저장되지 않은 UI 설정으로 조회할 때 사용할 설정 딕셔너리.
                None이면 현재 초기화된 클라이언트를 사용합니다.
        """
        client = self.gemini_client
        if config_override is not None:
            try:
                client = LLMClientFactory.create_client(
                    config=config_override,
                    requests_per_minute=config_override.get("requests_per_minute"),
                )
            except Exception as e:
                raise BtgServiceException(f"모델 조회용 클라이언트 생성 실패: {e}", original_exception=e) from e
        if not client:
            logger.error("모델 목록 조회 실패: Gemini 클라이언트가 초기화되지 않았습니다.")
            raise BtgServiceException("Gemini 클라이언트가 초기화되지 않았습니다. API 키 또는 Vertex AI 설정을 확인하세요.")
        logger.info("사용 가능한 모델 목록 조회 서비스 호출됨.")
        try:
            models_data = await client.list_models_async()
            result = []
            for m in models_data:
                if isinstance(m, dict):
                    result.append(m)
                else:
                    result.append({"name": str(m), "short_name": str(m), "display_name": str(m)})
            logger.info(f"총 {len(result)}개의 모델을 반환합니다.")
            return result
            
        except BtgApiClientException as e:
            logger.error(f"모델 목록 조회 중 API 오류: {e}")
            raise
        except Exception as e:
            logger.error(f"모델 목록 조회 중 예상치 못한 오류: {e}", exc_info=True) # type: ignore
            raise BtgServiceException(f"모델 목록 조회 중 오류: {e}", original_exception=e) from e

    async def check_llm_health_async(self, config_override: Optional[Dict[str, Any]] = None) -> tuple[bool, str]:
        """
        지정된(또는 현재 활성화된) LLM 클라이언트의 인증 및 연결 상태를 테스트합니다.

        Args:
            config_override: 테스트에 사용할 설정 딕셔너리 (None이면 현재 self.config 사용)

        Returns:
            tuple[bool, str]: (성공 여부, 진단 메시지)
        """
        cfg = config_override or self.config or {}
        try:
            temp_client = LLMClientFactory.create_client(
                config=cfg,
                requests_per_minute=cfg.get("requests_per_minute"),
            )
            return await temp_client.check_health_async()
        except Exception as e:
            logger.error(f"LLM 헬스체크 중 예외: {e}", exc_info=True)
            return False, f"클라이언트 생성 또는 점검 실패: {e}"

    def extract_glossary(
        self,
        input_file_path: Union[str, Path],
        progress_callback: Optional[Callable[[GlossaryExtractionProgressDTO], None]] = None,
        novel_language_code: Optional[str] = None,
        seed_glossary_path: Optional[Union[str, Path]] = None,
        user_override_glossary_extraction_prompt: Optional[str] = None,
        stop_check: Optional[Callable[[], bool]] = None
    ) -> Path:
        """
        용어집을 추출합니다 (동기 래퍼).
        내부적으로 asyncio.run()을 사용하여 비동기 버전을 호출합니다.
        
        Note: CLI 및 테스트 호환성을 위한 동기 래퍼입니다.
        새로운 코드에서는 extract_glossary_async()를 직접 사용하세요.
        """
        logger.info("[동기 래퍼] extract_glossary 호출 -> extract_glossary_async로 전환")
        return asyncio.run(
            self.extract_glossary_async(
                input_file_path=input_file_path,
                progress_callback=progress_callback,
                novel_language_code=novel_language_code,
                seed_glossary_path=seed_glossary_path,
                user_override_glossary_extraction_prompt=user_override_glossary_extraction_prompt,
                stop_check=stop_check
            )
        )

    async def extract_glossary_async(
        self,
        input_file_path: Union[str, Path],
        progress_callback: Optional[Callable[[GlossaryExtractionProgressDTO], None]] = None,
        novel_language_code: Optional[str] = None,
        seed_glossary_path: Optional[Union[str, Path]] = None,
        user_override_glossary_extraction_prompt: Optional[str] = None,
        stop_check: Optional[Callable[[], bool]] = None
    ) -> Path:
        """
        용어집을 비동기적으로 추출합니다. (경합 기반 취소 패턴 적용)
        
        Args:
            input_file_path: 분석할 입력 파일 경로
            progress_callback: 진행 상황 콜백
            novel_language_code: 명시적 언어 코드
            seed_glossary_path: 시드 용어집 경로
            user_override_glossary_extraction_prompt: 사용자 정의 프롬프트
            stop_check: 중지 확인 콜백
            
        Returns:
            생성된 용어집 파일 경로
        """
        if not self.glossary_service:
            logger.error("용어집 추출 서비스 실패: 서비스가 초기화되지 않았습니다.")
            raise BtgServiceException("용어집 추출 서비스가 초기화되지 않았습니다. 설정을 확인하세요.")
        
        # 이미 실행 중이면 예외 발생
        if self.current_glossary_task and not self.current_glossary_task.done():
            raise BtgServiceException("용어집 추출이 이미 실행 중입니다. 기존 작업을 먼저 취소하세요.")

        logger.info(f"비동기 용어집 추출 프로세스 시작: {input_file_path}")

        # 취소 이벤트 초기화
        self.cancel_glossary_event.clear()

        # 추출 실행 Task 생성
        extraction_task = asyncio.create_task(
            self._do_glossary_extraction_async(
                input_file_path,
                progress_callback,
                novel_language_code,
                seed_glossary_path,
                user_override_glossary_extraction_prompt,
                stop_check
            ),
            name="glossary_extraction_main"
        )

        # 취소 감시 Task 생성 (Watchdog)
        cancel_watch_task = asyncio.create_task(
            self._wait_for_glossary_cancel(),
            name="glossary_cancel_watcher"
        )

        self.current_glossary_task = extraction_task

        try:
            # 🏁 Promise.race 경합
            done, pending = await asyncio.wait(
                [extraction_task, cancel_watch_task],
                return_when=asyncio.FIRST_COMPLETED
            )

            for task in done:
                if task == cancel_watch_task:
                    # ❌ 취소가 먼저 완료됨 (이벤트 발생)
                    logger.warning("🚨 취소 감지기에 의해 용어집 추출 작업 중단 결정")
                    extraction_task.cancel()
                    try:
                        await extraction_task
                    except asyncio.CancelledError:
                        logger.info("용어집 추출 Task 취소 및 중간 결과 저장 완료")
                    
                    # 취소 시에도 생성된 파일 경로를 반환하여 GUI가 표시할 수 있게 함
                    output_path = self.glossary_service.get_glossary_output_path(input_file_path)
                    
                    if progress_callback:
                        progress_callback(GlossaryExtractionProgressDTO(0, 0, "사용자에 의해 중단됨 (부분 저장됨)", 0))
                    
                    return output_path
                else:
                    # ✅ 추출 작업이 완료됨
                    cancel_watch_task.cancel()
                    result_path = await task
                    logger.info(f"비동기 용어집 추출 성공: {result_path}")
                    return result_path

        except asyncio.CancelledError:
            logger.info("용어집 추출 프로세스가 종료되었습니다.")
            raise
        except Exception as e:
            logger.error(f"용어집 추출 중 심각한 오류: {e}", exc_info=True)
            if progress_callback:
                progress_callback(GlossaryExtractionProgressDTO(0, 0, f"오류: {e}", 0))
            raise
        finally:
            self.current_glossary_task = None
            for t in [extraction_task, cancel_watch_task]:
                if not t.done(): t.cancel()

    def extract_text_from_file(self, file_path: Union[str, Path]) -> str:
        """
        파일 확장자에 따라 알맞은 방식으로 텍스트 내용을 추출합니다.
        - .epub: EpubReviewProvider를 통해 모든 텍스트 노드를 파싱하여 추출
        - 기타 (.txt 등): read_text_file을 통한 UTF-8 텍스트 로드
        """
        path = Path(file_path)
        if not path.exists():
            logger.error(f"파일을 찾을 수 없습니다: {file_path}")
            raise FileNotFoundError(f"파일을 찾을 수 없습니다: {file_path}")

        ext = path.suffix.lower()
        if ext == '.epub':
            try:
                from domain.review_providers.epub_provider import EpubReviewProvider
                provider = EpubReviewProvider(self)
                source_chunks = provider.load_source_chunks(str(path))
                if not source_chunks:
                    logger.warning(f"EPUB 파일에서 텍스트 노드를 추출하지 못했습니다: {file_path}")
                    return ""
                return "\n\n".join(source_chunks.values())
            except Exception as e:
                logger.error(f"EPUB 텍스트 추출 중 오류 발생 ({file_path}): {e}", exc_info=True)
                raise
        else:
            return read_text_file(path)

    async def _do_glossary_extraction_async(
        self,
        input_file_path: Union[str, Path],
        progress_callback: Optional[Callable[[GlossaryExtractionProgressDTO], None]] = None,
        novel_language_code: Optional[str] = None,
        seed_glossary_path: Optional[Union[str, Path]] = None,
        user_override_glossary_extraction_prompt: Optional[str] = None,
        stop_check: Optional[Callable[[], bool]] = None
    ) -> Path:
        """실제 용어집 추출 루프 오케스트레이션 (AppService 책임)"""
        all_extracted_entries = []
        seed_entries = []
        try:
            file_content = self.extract_text_from_file(input_file_path)
            if not file_content:
                logger.warning("입력 파일이 비어 있어 작업을 중단합니다.")
                return Path(input_file_path)

            # 1. 초기 데이터 준비 (도메인 서비스 활용)
            seed_entries = self.glossary_service.load_seed_glossary(seed_glossary_path)
            sample_segments = self.glossary_service.prepare_segments(file_content)
            num_samples = len(sample_segments)
            
            if num_samples == 0:
                final_entries = self.glossary_service.finalize_glossary([], seed_entries)
                output_path = self.glossary_service.get_glossary_output_path(input_file_path)
                self.glossary_service.save_glossary_to_json(final_entries, output_path)
                return output_path

            # 2. 루프 실행 설정
            # 세마포어는 동시에 떠 있는 작업 수만 제한한다. API 요청 간격과 동시 진행 1개는
            # GeminiClient의 스케줄러가 보장하므로 여기서 RPM을 계산하지 않는다.
            max_workers = self.config.get("max_workers", 4)
            semaphore = asyncio.Semaphore(max_workers)

            async def run_extract(segment: str):
                if self.cancel_glossary_event.is_set(): raise asyncio.CancelledError()

                async with semaphore:
                    # 과부하(503)는 이 세그먼트가 성공할 때까지 다시 시도한다. 무결성 번역 청크와
                    # 같은 방침이다. 건너뛰면 그 부분의 용어가 최종 용어집에서 조용히 빠지는데,
                    # 과부하는 대개 일시적이라 기다리면 풀린다. 상한을 두지 않으므로 과부하가
                    # 풀리지 않으면 무한히 머물며, 멈추는 판단은 취소 요청에 맡긴다.
                    # 시도 간격은 서킷브레이커의 정지와 스케줄러의 RPM 간격이 잡는다.
                    overload_attempts = 0
                    while True:
                        if self.cancel_glossary_event.is_set(): raise asyncio.CancelledError()
                        try:
                            return await self.glossary_service._extract_glossary_entries_from_segment_via_api_async(
                                segment, user_override_glossary_extraction_prompt, stop_check
                            )
                        except asyncio.CancelledError:
                            raise
                        except Exception as e:
                            # 검열·파싱 오류 등은 다시 보내도 결과가 같을 가능성이 높아 그대로 올린다.
                            if not self._is_overload_failure(e):
                                raise
                            overload_attempts += 1
                            logger.warning(
                                f"세그먼트 과부하로 실패해 다시 시도합니다 "
                                f"(재시도 {overload_attempts}회째): {e}"
                            )

            async def run_indexed(idx: int, segment: str):
                """세그먼트 하나를 처리해 (번호, 추출 항목, 오류)를 돌려준다. 취소는 그대로 전파한다."""
                try:
                    return idx, await run_extract(segment), None
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    return idx, None, e

            async def run_pass(indexed_segments):
                """세그먼트들을 처리해 추출 항목을 모으고, 실패한 세그먼트를 {번호: 예외}로 돌려준다."""
                tasks = [asyncio.create_task(run_indexed(idx, seg)) for idx, seg in indexed_segments]
                failures = {}
                done_count = 0
                try:
                    for next_done in asyncio.as_completed(tasks):
                        idx, entries, error = await next_done
                        done_count += 1
                        if error is not None:
                            failures[idx] = error
                            logger.error(f"세그먼트 처리 실패: {error}")
                        elif entries:
                            all_extracted_entries.extend(entries)
                        if progress_callback:
                            progress_callback(GlossaryExtractionProgressDTO(
                                num_samples, done_count,
                                f"추출 중 ({done_count}/{len(tasks)})",
                                len(all_extracted_entries) + len(seed_entries)
                            ))
                except asyncio.CancelledError:
                    for t in tasks:
                        if not t.done(): t.cancel()
                    raise
                return failures

            # 3. 작업 실행 (순차 생성, 병렬 처리)
            failures = await run_pass(list(enumerate(sample_segments)))

            if failures:
                missing = sorted(failures)
                logger.warning(
                    f"용어집 추출: 세그먼트 {len(missing)}/{num_samples}개가 실패해 결과에서 빠졌습니다 "
                    f"(세그먼트 번호 {[i + 1 for i in missing]}). 해당 부분의 용어가 누락됐을 수 있습니다."
                )

            # 4. 마무리 (도메인 서비스 활용)
            if progress_callback:
                status = f"완료 (세그먼트 {len(failures)}개 누락)" if failures else "완료"
                progress_callback(GlossaryExtractionProgressDTO(
                    num_samples, num_samples, status, len(all_extracted_entries) + len(seed_entries)
                ))
            final_entries = self.glossary_service.finalize_glossary(all_extracted_entries, seed_entries)
            output_path = self.glossary_service.get_glossary_output_path(input_file_path)
            self.glossary_service.save_glossary_to_json(final_entries, output_path)
            
            return output_path
        except asyncio.CancelledError:
            # ✨ 취소 시 중간 결과 저장 로직 ✨
            if all_extracted_entries or seed_entries:
                logger.info(f"작업 취소 감지: 현재까지 추출된 {len(all_extracted_entries)}개 항목을 저장합니다.")
                try:
                    final_entries = self.glossary_service.finalize_glossary(all_extracted_entries, seed_entries)
                    output_path = self.glossary_service.get_glossary_output_path(input_file_path)
                    self.glossary_service.save_glossary_to_json(final_entries, output_path)
                except Exception as save_err:
                    logger.error(f"중간 결과 저장 중 오류: {save_err}")
            raise
        except Exception as e:
            logger.error(f"_do_glossary_extraction_async 내부 오류: {e}")
            raise

    @staticmethod
    def _is_overload_failure(error: BaseException) -> bool:
        """예외 사슬(original_exception, __cause__)에 503 과부하 실패가 있는지."""
        seen = set()
        while error is not None and id(error) not in seen:
            seen.add(id(error))
            if isinstance(error, GeminiServiceUnavailableException):
                return True
            error = getattr(error, "original_exception", None) or error.__cause__
        return False

    async def cancel_glossary_async(self) -> None:
        """용어집 추출 즉시 취소 요청"""
        if self.current_glossary_task and not self.current_glossary_task.done():
            logger.info("🚨 용어집 추출 취소 이벤트 발생!")
            self.cancel_glossary_event.set()

    async def _wait_for_glossary_cancel(self) -> None:
        """취소 신호 대기용 Task"""
        await self.cancel_glossary_event.wait()
        logger.info("⏱️ 용어집 취소 신호가 감지되었습니다.")
        raise asyncio.CancelledError("GLOSSARY_CANCELLED")
    # _translate_and_save_chunk() 동기 메서드 제거됨
    # 비동기 버전 _translate_and_save_chunk_async()를 사용하세요

    # ===== 비동기 메서드 (PySide6 마이그레이션) =====
    
    async def start_translation_async(
        self,
        input_file_path: Union[str, Path],
        output_file_path: Union[str, Path],
        progress_callback: Optional[Callable[[TranslationJobProgressDTO], None]] = None,
        status_callback: Optional[Callable[[str], None]] = None,
        tqdm_file_stream: Optional[Any] = None,
        retranslate_failed_only: bool = False,
        translation_mode_override: Optional[str] = None
    ) -> None:
        """
        비동기 번역 시작 (GUI에서 @asyncSlot()으로 호출)
        
        :param input_file_path: 입력 파일 경로
        :param output_file_path: 출력 파일 경로
        :param progress_callback: 진행 상황 콜백
        :param status_callback: 상태 변경 콜백
        :param tqdm_file_stream: 진행률 표시 스트림
        :param retranslate_failed_only: 실패한 청크만 재번역
        :param translation_mode_override: 설정 대신 쓸 번역 모드 (배치 미완료 청크를 실시간으로 마무리할 때 "standard")
        :raises BtgServiceException: 이미 번역 중인 경우
        """
        # 이미 실행 중이면 예외 발생
        if self.current_translation_task and not self.current_translation_task.done():
            raise BtgServiceException("번역이 이미 실행 중입니다. 먼저 현재 작업을 완료하거나 취소하세요.")
        
        logger.info(f"비동기 번역 시작: {input_file_path} → {output_file_path}")
        if status_callback:
            status_callback("번역 준비 중...")
        
        # === 용어집 동적 로딩 로직 ===
        try:
            input_p = Path(input_file_path)
            glossary_suffix = self.config.get("glossary_output_json_filename_suffix", "_simple_glossary.json")
            assumed_glossary_path = input_p.parent / f"{input_p.stem}{glossary_suffix}"
            
            glossary_to_use = None
            if assumed_glossary_path.exists():
                glossary_to_use = str(assumed_glossary_path)
                logger.info(f"용어집 '{assumed_glossary_path.name}' 자동 발견 및 사용")
            else:
                manual_path = self.config.get("glossary_json_path")
                if manual_path and Path(manual_path).exists():
                    glossary_to_use = manual_path
                    logger.info(f"설정된 용어집 사용: '{manual_path}'")
                else:
                    logger.info(f"용어집을 찾을 수 없어 용어집 없이 진행")
            
            if self.translation_service:
                self.config['glossary_json_path'] = glossary_to_use
                self.translation_service.config = self.config
                self.translation_service._load_glossary_data()
        except Exception as e:
            logger.error(f"용어집 동적 로딩 중 오류: {e}", exc_info=True)
        # === 용어집 동적 로딩 로직 끝 ===
        
        # ✨ Promise.race 패턴 구현 ✨
        # 취소 이벤트 초기화 (새 번역 시작)
        self.cancel_event.clear()
        
        # 번역 Task 생성
        translation_task = asyncio.create_task(
            self._do_translation_async(
                input_file_path,
                output_file_path,
                progress_callback,
                status_callback,
                tqdm_file_stream,
                retranslate_failed_only,
                translation_mode_override
            ),
            name="translation_main"
        )
        
        # 취소 감시 Task 생성 (cancelPromise 역할)
        cancel_watch_task = asyncio.create_task(
            self._wait_for_cancel(),
            name="cancel_watcher"
        )
        
        # current_translation_task는 번역 Task로 설정 (상태 관리용)
        self.current_translation_task = translation_task
        
        logger.info("🏁 Promise.race 시작: 번역 vs 취소 경합")
        
        try:
            # 🏁 Promise.race: 먼저 완료되는 Task의 결과를 반환
            done, pending = await asyncio.wait(
                [translation_task, cancel_watch_task],
                return_when=asyncio.FIRST_COMPLETED
            )
            
            # 결과 처리: 누가 이겼는가?
            for task in done:
                if task == cancel_watch_task:
                    # ❌ 취소 승리: 번역 Task 취소
                    logger.warning("❌ 취소 승리! 번역 Task 취소 중...")
                    translation_task.cancel()
                    
                    # 번역 Task 종료 대기 (정리 작업 완료 보장)
                    try:
                        await translation_task
                    except asyncio.CancelledError:
                        logger.info("✅ 번역 Task 취소 완료")
                    
                    if status_callback:
                        status_callback("중단됨")
                    raise asyncio.CancelledError("사용자에 의해 취소됨")
                
                else:
                    # ✅ 번역 승리: 취소 감시 Task 정리
                    logger.info("✅ 번역 승리! 취소 감시 Task 정리")
                    cancel_watch_task.cancel()
                    
                    # 번역 결과 반환 (await로 예외 전파)
                    await translation_task
                    
        except asyncio.CancelledError:
            logger.info("번역이 사용자에 의해 취소되었습니다")
            if status_callback:
                status_callback("중단됨")
            raise
        except Exception as e:
            logger.error(f"번역 중 오류: {e}", exc_info=True)
            if status_callback:
                status_callback(f"오류: {e}")
            raise
        finally:
            # 정리: 나머지 Task 취소
            for task in [translation_task, cancel_watch_task]:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            
            self.current_translation_task = None
            logger.info("🧹 Promise.race 종료 및 정리 완료")
    
    async def cancel_translation_async(self) -> None:
        """
        비동기 번역 취소 (즉시 반응, Promise.race 패턴)
        
        asyncio.Event를 사용하여 취소 신호를 즉시 전파합니다.
        TypeScript의 cancelPromise.reject()와 동일한 패턴입니다.
        """
        if self.current_translation_task and not self.current_translation_task.done():
            logger.info("🚨 번역 취소 요청됨 (취소 이벤트 발생)")
            self.cancel_event.set()  # ✅ 즉시 취소 신호 발생
        else:
            logger.warning("현재 실행 중인 번역 작업이 없습니다")
    
    async def _wait_for_cancel(self) -> None:
        """
        취소 이벤트 대기 Task (TypeScript cancelPromise 역할)
        
        이 Task는 cancel_event.wait()로 대기하다가,
        취소 신호가 발생하면 즉시 CancelledError를 발생시킵니다.
        
        Promise.race에서 이 Task가 먼저 완료되면,
        번역 Task를 취소하고 사용자에게 취소를 알립니다.
        """
        await self.cancel_event.wait()
        logger.info("⏱️ 취소 신호 감지됨. CancelledError 발생")
        raise asyncio.CancelledError("CANCELLED_BY_USER")

    async def _do_translation_async(
        self,
        input_file_path: Union[str, Path],
        output_file_path: Union[str, Path],
        progress_callback: Optional[Callable[[TranslationJobProgressDTO], None]] = None,
        status_callback: Optional[Callable[[str], None]] = None,
        tqdm_file_stream: Optional[Any] = None,
        retranslate_failed_only: bool = False,
        translation_mode_override: Optional[str] = None
    ) -> None:
        """
        비동기 번역 메인 로직
        
        - Lock 제거 (asyncio 단일 스레드)
        - 상태는 Task 객체로 관리
        - ThreadPoolExecutor 제거 (asyncio.gather 사용)
        """
        # 서비스 검증
        if not self.translation_service or not self.chunk_service:
            logger.error("번역 서비스 실패: 서비스가 초기화되지 않았습니다.")
            if status_callback:
                status_callback("오류: 서비스 초기화 실패")
            raise BtgServiceException("번역 서비스가 초기화되지 않았습니다. 설정을 확인하세요.")
        
        # 상태 초기화 (Lock 불필요)
        self.processed_chunks_count = 0
        self.successful_chunks_count = 0
        self.failed_chunks_count = 0
        
        input_file_path_obj = Path(input_file_path)
        final_output_file_path_obj = Path(output_file_path)
        
        translation_mode = translation_mode_override or self.config.get("translation_mode", "standard")
        logger.info(f"비동기 번역 시작 (모드: {translation_mode}): {input_file_path} → {output_file_path}")

        # 🚀 배치 모드: 제출하거나, 진행 중인 작업을 조회·수거한다 (결과는 최대 24시간 뒤)
        if translation_mode == "batch":
            await self._do_batch_translation_async(
                input_file_path_obj, final_output_file_path_obj, progress_callback, status_callback
            )
            return

        # 🚀 EPUB 모드 특수 처리
        if translation_mode == "epub" or input_file_path_obj.suffix.lower() == ".epub":
            if status_callback:
                status_callback("EPUB 번역 중...")
            await self.translation_service.translate_epub(input_file_path, output_file_path)
            
            # 메타데이터 영속화 (애플리케이션 레이어 처리)
            epub_meta = {
                "pipeline_type": "epub",
                "status": "completed",
                "last_updated": time.time()
            }
            save_metadata(input_file_path_obj, epub_meta)
            
            if status_callback:
                status_callback("EPUB 번역 완료!")
            if progress_callback:
                progress_callback(TranslationJobProgressDTO(
                    total_chunks=1, processed_chunks=1, successful_chunks=1, failed_chunks=0,
                    current_status_message="EPUB 번역 완료"
                ))
            return

        # 🚀 무결성 모드 특수 처리 (전체 텍스트를 한 번에 넘기되 내부적으로 청킹)
        if translation_mode == "integrity":
            if status_callback:
                status_callback("무결성 번역 시작 (줄 단위 검증)...")
            try:
                metadata_file_path = get_metadata_file_path(input_file_path_obj)
                
                # 📍 메타데이터 핸들링 (애플리케이션 레이어)
                loaded_metadata = load_metadata(input_file_path_obj) or {
                    "pipeline_type": "integrity",
                    "total_chunks": 0,
                    "translated_chunks": {},
                    "failed_chunks": {},
                    "status": "in_progress",
                    "last_updated": time.time()
                }
                loaded_metadata["pipeline_type"] = "integrity"
                loaded_metadata["status"] = "in_progress"
                loaded_metadata["last_updated"] = time.time()
                save_metadata(metadata_file_path, loaded_metadata)

                def integrity_progress_handler(dto: TranslationJobProgressDTO):
                    # Progress DTO 수신 시 메타데이터 갱신 및 파일 저장 (애플리케이션 레이어 위임)
                    loaded_metadata["total_chunks"] = dto.total_chunks
                    translated_dict = loaded_metadata.get("translated_chunks", {})
                    if not isinstance(translated_dict, dict):
                        translated_dict = {}
                    for idx in range(dto.successful_chunks):
                        translated_dict[str(idx)] = {"status": "success"}
                    loaded_metadata["translated_chunks"] = translated_dict
                    loaded_metadata["status"] = "in_progress" if dto.processed_chunks < dto.total_chunks else "completed"
                    loaded_metadata["last_updated"] = time.time()
                    save_metadata(metadata_file_path, loaded_metadata)
                    
                    if progress_callback:
                        progress_callback(dto)

                file_content = read_text_file(input_file_path_obj)
                translated_text = await self.translation_service.translate_text_integrity(
                    file_content,
                    output_path_for_progress=final_output_file_path_obj,
                    progress_callback=integrity_progress_handler,
                    status_callback=status_callback,
                    tqdm_file_stream=tqdm_file_stream
                )
                write_text_file(final_output_file_path_obj, translated_text)
                
                # 📍 완결 후 최종 상태 갱신
                loaded_metadata["status"] = "completed"
                loaded_metadata["last_updated"] = time.time()
                save_metadata(metadata_file_path, loaded_metadata)

                if status_callback:
                    status_callback("무결성 번역 완료!")
                return
            except Exception as e_int:
                logger.error(f"무결성 번역 중 오류: {e_int}")
                if status_callback:
                    status_callback(f"무결성 번역 실패: {e_int}")
                raise

        # 🚀 표준 모드 (기존 청크 기반 로직)
        if status_callback:
            status_callback("표준 번역 시작됨...")
        
        metadata_file_path = get_metadata_file_path(input_file_path_obj)
        loaded_metadata: Dict[str, Any] = {}
        resume_translation = False
        total_chunks = 0
        
        try:
            # 메타데이터 로드
            if metadata_file_path.exists():
                try:
                    loaded_metadata = load_metadata(metadata_file_path)
                    if loaded_metadata:
                        logger.info(f"기존 메타데이터 로드 성공: {metadata_file_path}")
                    else:
                        logger.warning(f"메타데이터 파일이 비어있습니다. 새로 시작합니다.")
                except json.JSONDecodeError as json_err:
                    logger.error(f"메타데이터 파일 손상 (JSONDecodeError): {json_err}. 새로 번역을 시작합니다.")
                    delete_file(metadata_file_path)
                except Exception as e:
                    logger.error(f"메타데이터 파일 로드 중 오류: {e}. 새로 번역을 시작합니다.", exc_info=True)
                    delete_file(metadata_file_path)
            else:
                logger.info(f"기존 메타데이터 파일을 찾을 수 없습니다. 새로 시작합니다.")
            
            # 파일 읽기 (비동기 아님, 로컬 I/O이므로 동기 유지)
            try:
                file_content = read_text_file(input_file_path_obj)
            except Exception as file_read_err:
                logger.error(f"입력 파일 읽기 실패: {file_read_err}", exc_info=True)
                if status_callback:
                    status_callback(f"오류: 파일 읽기 실패 - {file_read_err}")
                raise
            
            # 청크 분할
            all_chunks = self.chunk_service.create_chunks_from_file_content(
                file_content,
                self.config.get("chunk_size", 6000)
            )
            total_chunks = len(all_chunks)
            logger.info(f"파일이 {total_chunks}개 청크로 분할됨")
            
            # 청크 백업 파일 경로 생성 (입력 파일 기준)
            # input.txt → input_translated_chunked.txt
            chunked_output_file_path = input_file_path_obj.parent / f"{input_file_path_obj.stem}_translated_chunked.txt"
            
            # 설정 해시 확인 (이어하기 가능 여부 판단)
            current_config_hash = _hash_config_for_metadata(self.config)
            previous_config_hash = loaded_metadata.get("config_hash")
            
            if previous_config_hash and previous_config_hash == current_config_hash:
                # 청크 수 변경 감지
                if loaded_metadata.get("total_chunks") != total_chunks:
                    logger.warning(f"입력 파일의 청크 수가 변경되었습니다 ({loaded_metadata.get('total_chunks')} -> {total_chunks}). 메타데이터를 새로 생성합니다.")
                    resume_translation = False
                    loaded_metadata = create_new_metadata(input_file_path_obj, total_chunks, self.config)
                    loaded_metadata["status"] = "in_progress"
                    loaded_metadata["last_updated"] = time.time()
                    save_metadata(metadata_file_path, loaded_metadata)
                    logger.info("청크 수 변경으로 새 메타데이터 저장 완료")
                    
                    # 출력 파일 초기화
                    delete_file(final_output_file_path_obj)
                    final_output_file_path_obj.touch()
                    # 청크 백업 파일도 초기화
                    delete_file(chunked_output_file_path)
                    chunked_output_file_path.touch()
                    logger.info(f"출력 파일 및 청크 백업 파일 초기화 완료: {final_output_file_path_obj}")
                else:
                    resume_translation = True
                    # 이어하기 시 메타데이터 상태 업데이트
                    loaded_metadata["status"] = "in_progress"
                    loaded_metadata["last_updated"] = time.time()
                    save_metadata(metadata_file_path, loaded_metadata)
                    logger.info("이전 번역을 계속 진행합니다 (설정 동일)")
            else:
                # config_hash 없거나 불일치 → 새로 시작
                if not previous_config_hash:
                    logger.info("설정 해시 없음 (오래된 메타데이터) → 새로운 번역을 시작합니다")
                else:
                    logger.info("새로운 번역을 시작합니다 (설정 변경)")
                resume_translation = False
                loaded_metadata = create_new_metadata(input_file_path_obj, total_chunks, self.config)
                loaded_metadata["status"] = "in_progress"
                loaded_metadata["last_updated"] = time.time()
                save_metadata(metadata_file_path, loaded_metadata)
                logger.info("새 메타데이터 생성 및 저장 완료")
                
                # 출력 파일 초기화
                delete_file(final_output_file_path_obj)
                final_output_file_path_obj.touch()
                # 청크 백업 파일도 초기화
                delete_file(chunked_output_file_path)
                chunked_output_file_path.touch()
                logger.info(f"출력 파일 및 청크 백업 파일 초기화 완료: {final_output_file_path_obj}")
            
            # 이어하기 시나리오에서, 혹시 마지막에 불완전한 청크가 있다면 정리
            try:
                if chunked_output_file_path.exists():
                    existing_chunks = load_chunks_from_file(chunked_output_file_path)
                    save_merged_chunks_to_file(chunked_output_file_path, existing_chunks)
                    logger.info("청크 파일을 스캔하여 완전한 청크만 유지하도록 정리했습니다.")
            except Exception as sanitize_e:
                logger.warning(f"청크 파일 정리 중 경고: {sanitize_e}")
            
            # 이어하기 또는 새로 시작
            if resume_translation:
                # 이미 번역된 청크 찾기
                translated_chunks = loaded_metadata.get("translated_chunks", {})
                failed_chunks = loaded_metadata.get("failed_chunks", {})
                
                # 🔧 이어하기 시 이미 완료된 청크 수로 초기화
                self.processed_chunks_count = len(translated_chunks)
                self.successful_chunks_count = len(translated_chunks)
                logger.info(f"이어하기: processed_chunks_count 초기화 → {self.processed_chunks_count}")
                
                if retranslate_failed_only:
                    # 실패한 청크만 재번역 (안전한 딕셔너리 체크)
                    if failed_chunks:
                        chunks_to_process = [
                            (i, chunk) for i, chunk in enumerate(all_chunks)
                            if str(i) in failed_chunks
                        ]
                        logger.info(f"실패 청크 재번역 모드: {len(chunks_to_process)}개 대상")
                    else:
                        chunks_to_process = []
                        logger.info("실패한 청크가 없어 재번역을 건너뜁니다")
                else:
                    # 모든 미번역 청크 처리
                    chunks_to_process = [
                        (i, chunk) for i, chunk in enumerate(all_chunks)
                        if str(i) not in translated_chunks
                    ]
                    logger.info(f"이어하기: {len(translated_chunks)}개 이미 완료, {len(chunks_to_process)}개 추가 번역 대상")
            else:
                chunks_to_process = list(enumerate(all_chunks))
                logger.info(f"새로 번역: {len(chunks_to_process)}개 번역 대상")
            
            if not chunks_to_process and total_chunks > 0:
                logger.info("번역할 새로운 청크가 없습니다 (모든 청크가 이미 번역됨)")
                if status_callback:
                    status_callback("완료: 모든 청크 이미 번역됨")
                loaded_metadata["status"] = "completed"
                loaded_metadata["last_updated"] = time.time()
                save_metadata(metadata_file_path, loaded_metadata)
                return
            
            logger.info(f"처리 대상: {len(chunks_to_process)} 청크 (총 {total_chunks}개)")
            
            # 메타데이터 상태 업데이트 (번역 시작)
            if loaded_metadata.get("status") != "in_progress":
                loaded_metadata["status"] = "in_progress"
                loaded_metadata["last_updated"] = time.time()
                save_metadata(metadata_file_path, loaded_metadata)
                logger.info("번역 시작: 메타데이터 상태를 'in_progress'로 업데이트")
            
            # 번역 장기기억: 원문·용어집 인덱싱과 이미 번역된 청크 반영 (켜져 있을 때만)
            await self._prepare_translation_memory_async(input_file_path_obj, all_chunks, status_callback)

            # 청크 병렬 처리 (청크 백업 파일에 저장)
            await self._translate_chunks_async(
                chunks_to_process,
                chunked_output_file_path,
                total_chunks,
                metadata_file_path,
                input_file_path_obj,
                progress_callback,
                tqdm_file_stream
            )
            
            await self._merge_and_finalize_async(
                chunked_output_file_path,
                final_output_file_path_obj,
                metadata_file_path,
                status_callback,
            )
            
        except asyncio.CancelledError:
            logger.info("비동기 번역이 취소되었습니다")
            if status_callback:
                status_callback("중단됨")
            raise
        except Exception as e:
            logger.error(f"비동기 번역 중 오류: {e}", exc_info=True)
            if status_callback:
                status_callback(f"오류: {e}")
            raise

    # ============================================================================
    # 번역 장기기억 (임베딩)
    # ============================================================================

    async def _prepare_translation_memory_async(
        self,
        input_path: Path,
        chunks: List[str],
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        """
        번역 기억을 준비해 TranslationService에 연결한다.

        원문 문단과 용어집을 임베딩하고(내용이 같으면 캐시 재사용), 청크 백업 파일에 이미 있는 번역을
        기억에 반영한다. 실패해도 번역은 기억 없이 계속한다.
        """
        ts = self.translation_service
        if ts is not None:
            ts.translation_memory = None
        if ts is None or not self.config.get("enable_translation_memory", False):
            return
        try:
            factory = self.embedding_client_factory or create_embedding_client
            embedder = factory(self.config)
            store = TranslationMemoryStore.open(input_path, embedder.model, embedder.output_dimension)
            if status_callback:
                status_callback("번역 기억 인덱싱 중 (원문·용어집 임베딩)...")
            await store.index_source_async(chunks, embedder)
            target = normalize_language_code(self.config.get("target_translation_language", "ko"))
            glossary = [e for e in (ts.glossary_entries_for_injection or []) if e.target_language == target]
            await store.index_glossary_async(glossary, embedder)

            chunked = input_path.parent / f"{input_path.stem}_translated_chunked.txt"
            if chunked.exists():
                for idx, text in load_chunks_from_file(chunked).items():
                    if 0 <= idx < len(chunks) and not text.startswith("[번역 실패") and not text.startswith("[타임아웃"):
                        store.record_translation(idx, chunks[idx], text, save=False)
                store.save(vectors_changed=False)
            ts.translation_memory = store
            info = store.summary()
            logger.info(f"번역 기억 준비 완료: 문단 {info['paragraphs']}개 (번역됨 {info['translated']}), 용어 {info['glossary']}개")
        except Exception as e:
            logger.warning(f"번역 기억을 준비하지 못해 기억 없이 번역합니다: {e}", exc_info=True)
            if status_callback:
                status_callback(f"번역 기억 준비 실패 (기억 없이 번역): {e}")

    def _record_translation_memory(self, chunk_index: int, source_text: str, translated_text: str) -> None:
        memory = getattr(self.translation_service, "translation_memory", None)
        if memory is None or not translated_text:
            return
        try:
            memory.record_translation(chunk_index, source_text, translated_text)
        except Exception as e:
            logger.warning(f"번역 기억 기록 실패 (청크 {chunk_index}): {e}")

    async def check_embedding_health_async(self, config_override: Optional[Dict[str, Any]] = None) -> tuple[bool, str]:
        """임베딩 프로바이더(Voyage) 연결을 점검한다."""
        cfg = config_override or self.config or {}
        try:
            factory = self.embedding_client_factory or create_embedding_client
            return await factory(cfg).check_health_async()
        except Exception as e:
            return False, f"임베딩 클라이언트 생성 또는 점검 실패: {e}"

    # ============================================================================
    # 배치 번역 (Gemini Batch API)
    # ============================================================================

    def _get_batch_service(self) -> BatchTranslationService:
        if not isinstance(self.gemini_client, GeminiClient) or not self.translation_service:
            raise BtgServiceException("배치 번역은 Google Gemini API 프로바이더에서만 사용할 수 있습니다.")
        service = BatchTranslationService(
            self.config, self.translation_service, self.gemini_client, self.chunk_service,
            batch_client_factory=getattr(self, "batch_client_factory", None),
        )
        reason = service.check_available()
        if reason:
            raise BtgServiceException(reason)
        return service

    def batch_unavailable_reason(self) -> Optional[str]:
        """배치 모드를 쓸 수 없는 이유 (GUI 카드 비활성화용). 쓸 수 있으면 None."""
        try:
            self._get_batch_service()
            return None
        except BtgServiceException as e:
            return e.message

    def get_batch_summary(self, input_file_path: Union[str, Path]) -> Optional[BatchSummary]:
        """네트워크 없이 메타데이터만 읽어 배치 현황을 돌려준다. 배치 기록이 없으면 None."""
        input_path = Path(input_file_path)
        if not input_path.exists() or not (load_metadata(input_path) or {}).get("batch"):
            return None
        try:
            return self._get_batch_service().summarize(input_path)
        except BtgServiceException:
            return None

    def _emit_batch_progress(self, summary: BatchSummary, progress_callback, status_callback) -> None:
        if status_callback:
            status_callback(summary.describe())
        if progress_callback:
            progress_callback(TranslationJobProgressDTO(
                total_chunks=summary.total_chunks,
                processed_chunks=summary.translated,
                successful_chunks=summary.translated,
                failed_chunks=len(summary.remaining) if not summary.active else 0,
                current_status_message=summary.describe(),
            ))

    async def _finalize_batch_if_complete(self, service: BatchTranslationService, summary: BatchSummary,
                                          input_path: Path, output_path: Path, status_callback) -> None:
        if summary.complete and summary.total_chunks > 0:
            await self._merge_and_finalize_async(
                service.chunked_output_path(input_path), output_path,
                get_metadata_file_path(input_path), status_callback,
            )

    async def _do_batch_translation_async(
        self,
        input_path: Path,
        output_path: Path,
        progress_callback: Optional[Callable[[TranslationJobProgressDTO], None]] = None,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        """
        시작 버튼의 배치 동작.

        - 진행 중인 작업이 있으면 상태를 조회하고 끝난 결과를 수거한다.
        - 아직 한 번도 제출하지 않은 청크가 있으면 제출한다.
        - 모든 청크가 채워지면 표준 모드와 같은 병합·후처리로 최종 파일을 쓴다.
        미완료(검열·오류) 청크의 재처리는 사용자가 고른다 (실시간 마무리 / 재제출 / 그대로 저장).
        """
        service = self._get_batch_service()
        summary = service.summarize(input_path)
        if summary.active:
            summary = await service.refresh_async(input_path, status_callback)
        else:
            failed = (load_metadata(input_path) or {}).get("failed_chunks") or {}
            never_tried = [i for i in summary.remaining if str(i) not in failed] if summary.jobs else summary.remaining
            if never_tried:
                await self._prepare_translation_memory_async(input_path, service._load_chunks(input_path), status_callback)
                summary = await service.submit_async(input_path, status_callback)
        await self._finalize_batch_if_complete(service, summary, input_path, output_path, status_callback)
        self._emit_batch_progress(summary, progress_callback, status_callback)

    async def refresh_batch_async(
        self,
        input_file_path: Union[str, Path],
        output_file_path: Union[str, Path],
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> BatchSummary:
        """배치 작업 상태를 조회·수거하고, 모두 끝났으면 최종 파일을 쓴다 (폴링용)."""
        service = self._get_batch_service()
        input_path, output_path = Path(input_file_path), Path(output_file_path)
        summary = await service.refresh_async(input_path, status_callback)
        await self._finalize_batch_if_complete(service, summary, input_path, output_path, status_callback)
        return summary

    async def resubmit_batch_async(
        self,
        input_file_path: Union[str, Path],
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> BatchSummary:
        """미완료 청크만 모아 다음 라운드 배치로 다시 제출한다."""
        service = self._get_batch_service()
        input_path = Path(input_file_path)
        # 이전 라운드에서 번역된 청크가 번역 기억의 예시가 된다
        await self._prepare_translation_memory_async(input_path, service._load_chunks(input_path), status_callback)
        return await service.submit_async(input_path, status_callback)

    async def cancel_batch_async(
        self,
        input_file_path: Union[str, Path],
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> BatchSummary:
        return await self._get_batch_service().cancel_async(Path(input_file_path), status_callback)

    async def save_batch_with_failures_async(
        self,
        input_file_path: Union[str, Path],
        output_file_path: Union[str, Path],
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> int:
        """미완료 청크는 실패 표시와 원문으로 채워 최종 파일을 쓴다. 채운 청크 수를 돌려준다."""
        service = self._get_batch_service()
        input_path = Path(input_file_path)
        count = service.write_failure_placeholders(input_path)
        await self._merge_and_finalize_async(
            service.chunked_output_path(input_path), Path(output_file_path),
            get_metadata_file_path(input_path), status_callback,
        )
        return count

    async def _merge_and_finalize_async(
        self,
        chunked_output_file_path: Path,
        final_output_file_path_obj: Path,
        metadata_file_path: Path,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> None:
        """
        청크 백업 파일을 인덱스 순으로 병합하고 후처리해 최종 파일을 쓴 뒤 메타데이터를 완료로 표시한다.

        표준 모드와 배치 모드가 공유한다.
        """
        logger.info("모든 청크 처리 완료. 결과 병합 및 최종 저장 시작...")
        
        # 청크 백업 파일에서 최종 병합 대상 로드 및 인덱스 정렬
        final_merged_chunks: Dict[int, str] = {}
        try:
            # 병렬 번역으로 인해 뒤섞인 백업 파일을 정렬하기 위해 먼저 로드
            final_merged_chunks = load_chunks_from_file(chunked_output_file_path)
            # 정렬된 순서로 백업 파일 다시 저장 (유저 요청: 인덱스 정렬)
            save_merged_chunks_to_file(chunked_output_file_path, final_merged_chunks)
            logger.info(f"청크 백업 파일 인덱스 정렬 완료 및 로드: {len(final_merged_chunks)}개 청크")
        except Exception as e:
            logger.error(f"청크 파일 '{chunked_output_file_path}' 로드 및 정렬 중 오류: {e}. 최종 저장이 불안정할 수 있습니다.", exc_info=True)
        
        try:
            # ✅ 후처리 실행 (설정에서 활성화된 경우)
            if self.config.get("enable_post_processing", True):
                logger.info("번역 완료 후 후처리를 시작합니다 (비동기 스레드 위임)...")
                try:
                    # 1. 별도 스레드에서 가공 및 마커 제거까지 한 번에 수행 (이벤트 루프 차단 방지)
                    final_text = await asyncio.to_thread(
                        self.post_processing_service.post_process_and_clean_chunks,
                        final_merged_chunks,
                        self.config
                    )
                    
                    # 2. 최종 결과물만 단 한 번 저장
                    await asyncio.to_thread(write_text_file, final_output_file_path_obj, final_text)
                    logger.info(f"후처리 및 최종 파일 저장 완료 (단일 I/O): {final_output_file_path_obj}")
                        
                except Exception as post_proc_e:
                    logger.error(f"후처리 중 오류 발생: {post_proc_e}. 기본 병합 저장을 시도합니다.", exc_info=True)
                    # 후처리 실패 시 원본 병합 결과를 최종 출력 파일에 저장 (인덱스 제거 시도)
                    await asyncio.to_thread(save_merged_chunks_to_file, final_output_file_path_obj, final_merged_chunks)
                    await asyncio.to_thread(self.post_processing_service.remove_chunk_indexes_from_final_file, final_output_file_path_obj)
            else:
                logger.info("후처리가 설정에서 비활성화되었습니다. 기본 병합 저장을 진행합니다.")
                # 후처리가 비활성화된 경우에도 인덱스는 제거하여 저장
                await asyncio.to_thread(save_merged_chunks_to_file, final_output_file_path_obj, final_merged_chunks)
                await asyncio.to_thread(self.post_processing_service.remove_chunk_indexes_from_final_file, final_output_file_path_obj)
                logger.info(f"후처리 없이 최종 결과 저장 완료: {final_output_file_path_obj}")
            
            # 청크 백업 파일(이어하기용)은 이미 chunked_output_file_path에 존재함
            logger.info(f"✅ 번역 완료! 최종 파일: {final_output_file_path_obj}, 백업: {chunked_output_file_path}")
            
            if status_callback:
                status_callback("완료!")
        except Exception as merge_err:
            logger.error(f"최종 저장 중 오류: {merge_err}", exc_info=True)
            if status_callback:
                status_callback(f"오류: 최종 저장 실패 - {merge_err}")
            raise
        
        # 메타데이터 최종 업데이트
        # ⚠️ 중요: 각 청크 처리 중 update_metadata_for_chunk_completion이 파일을 업데이트했으므로,
        # 메모리의 loaded_metadata가 아닌 최신 파일 내용을 로드하여 status만 업데이트
        try:
            current_metadata = load_metadata(metadata_file_path)
            current_metadata["status"] = "completed"
            current_metadata["last_updated"] = time.time()
            save_metadata(metadata_file_path, current_metadata)
            logger.info(f"메타데이터 최종 업데이트 완료: {len(current_metadata.get('translated_chunks', {}))}개 청크 정보 보존")
        except Exception as meta_save_err:
            logger.error(f"메타데이터 최종 저장 중 오류: {meta_save_err}", exc_info=True)
            # 실패해도 번역 파일은 정상이므로 계속 진행

    async def _translate_chunks_async(
        self,
        chunks: List[Tuple[int, str]],
        output_file: Path,
        total_chunks: int,
        metadata_file_path: Path,
        input_file_path: Path,
        progress_callback: Optional[Callable[[TranslationJobProgressDTO], None]] = None,
        tqdm_file_stream: Optional[Any] = None
    ) -> None:
        """
        청크들을 비동기로 병렬 처리
        
        - 세마포어로 동시 실행 수 제한 (max_workers 적용)
        - RPM 속도 제한 적용
        - Task.cancel()로 즉시 취소 가능
        - tqdm 진행률 표시 지원
        """
        if not chunks:
            logger.info("처리할 청크가 없습니다")
            return
        
        max_workers = self.config.get("max_workers", 4)
        rpm = self.config.get("requests_per_minute", DEFAULT_REQUESTS_PER_MINUTE)

        logger.info(f"비동기 청크 병렬 처리 시작: {len(chunks)} 청크 (동시 작업: {max_workers}, RPM: {rpm})")

        # 세마포어: 동시에 떠 있는 작업 수만 제한한다. API 요청 간격과 동시 진행 1개는
        # GeminiClient의 스케줄러가 보장하므로 여기서 RPM을 계산하지 않는다.
        semaphore = asyncio.Semaphore(max_workers)
        
        # tqdm 진행률 표시 (비동기 환경에서도 사용 가능)
        pbar = None
        if tqdm_file_stream:
            try:
                from tqdm import tqdm
                pbar = tqdm(
                    total=len(chunks),
                    desc="번역 진행",
                    unit="청크",
                    file=tqdm_file_stream,
                    ncols=100,
                    bar_format='{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]'
                )
                logger.debug(f"비동기 tqdm 진행률 표시 초기화 완료 (총 {len(chunks)} 청크)")
            except ImportError:
                logger.warning("tqdm을 가져올 수 없습니다. 진행률 표시가 비활성화됩니다.")
            except Exception as tqdm_init_e:
                logger.error(f"tqdm 초기화 중 오류: {tqdm_init_e}. 진행률 표시를 건너뜁니다.")
        
        async def rate_limited_translate(chunk_index: int, chunk_text: str) -> bool:
            """세마포어로 동시 작업 수를 제한하는 번역 함수 (요청 속도는 GeminiClient가 제어)"""
            
            # ✅ 취소 신호 확인 (세마포어 진입 전에 즉시 반응)
            if self.cancel_event.is_set():
                logger.info(f"청크 {chunk_index + 1} 취소 신호 감지하여 건너뜀")
                raise asyncio.CancelledError("취소 신호 감지")
            
            # 세마포어로 동시 실행 제한
            async with semaphore:
                # ✅ 세마포어 진입 후 다시 취소 신호 확인 (대기 중 신호 받을 수 있음)
                if self.cancel_event.is_set():
                    logger.info(f"청크 {chunk_index + 1} 세마포어 대기 중 취소 신호 감지")
                    raise asyncio.CancelledError("취소 신호 감지")
                
                return await self._translate_and_save_chunk_async(
                    chunk_index,
                    chunk_text,
                    output_file,
                    total_chunks,
                    metadata_file_path,
                    input_file_path,
                    progress_callback
                )
        
        # Task 리스트 생성
        tasks = []
        for chunk_index, chunk_text in chunks:
            task = asyncio.create_task(rate_limited_translate(chunk_index, chunk_text))
            tasks.append(task)
        
        # 모든 Task 완료 대기 (예외 무시)
        logger.info(f"{len(tasks)}개 비동기 Task 실행 중...")
        
        try:
            # 비동기로 Task들을 처리하면서 tqdm 업데이트
            results = []
            for task in asyncio.as_completed(tasks):
                try:
                    result = await task
                    results.append(result)
                    # tqdm 업데이트
                    if pbar:
                        pbar.update(1)
                except Exception as e:
                    results.append(e)
                    if pbar:
                        pbar.update(1)
        finally:
            # tqdm 종료
            if pbar:
                try:
                    pbar.close()
                    logger.debug("비동기 tqdm 진행률 표시 종료")
                except Exception as pbar_close_e:
                    logger.warning(f"tqdm 종료 중 오류: {pbar_close_e}")
        
        # 결과 분석
        success_count = 0
        error_count = 0
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                if not isinstance(result, asyncio.CancelledError):
                    logger.error(f"청크 {i} 처리 중 예외: {result}")
                error_count += 1
            else:
                if result:
                    success_count += 1
        
        logger.info(f"청크 병렬 처리 완료: 성공 {success_count}, 실패 {error_count}")

    async def _translate_and_save_chunk_async(
        self,
        chunk_index: int,
        chunk_text: str,
        output_file: Path,
        total_chunks: int,
        metadata_file_path: Path,
        input_file_path: Path,
        progress_callback: Optional[Callable[[TranslationJobProgressDTO], None]] = None
    ) -> bool:
        """
        비동기 청크 처리 (동기 버전과 동일한 로깅 구조)
        
        - Lock 제거 (asyncio 단일 스레드)
        - 비동기 번역 호출
        - 파일 쓰기는 순차 처리
        - 타임아웃 처리 포함
        """
        current_chunk_info_msg = f"청크 {chunk_index + 1}/{total_chunks}"
        
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
        
        last_error = ""
        success = False
        translated_chunk = ""
        
        try:
            # 빈 청크 체크
            if not chunk_text.strip():
                logger.warning(f"  ⚠️ {current_chunk_info_msg} 빈 청크 (건너뜀)")
                return False
            
            # 번역 설정 로드
            model_name = self.config.get("model_name", "gemini-2.0-flash")
            
            # 번역 설정 상세는 DEBUG에서만 출력
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(f"  ⚙️ 설정: 모델={model_name}, 타임아웃=300초")
            
            translation_start_time = time.time()
            
            # 비동기 번역 호출 (timeout은 GeminiClient의 http_options에 의해 자동 적용)
            try:
                translated_chunk = await self.translation_service.translate_chunk_async(
                    chunk_text
                )

                # 내용이 있는 청크가 빈 번역문으로 돌아오면 실패로 다룬다. 아래층
                # translate_text_async가 빈 응답을 예외로 바꾸므로 정상 경로에서는 오지 않지만,
                # 그 방어가 뚫리면 빈 번역문이 완료로 기록되고 이어하기가 다시 집지 않아
                # 결과물에 구멍이 남는다. 실패로 두면 그 청크는 원문이 보존되고 다시 시도된다.
                if chunk_text.strip() and not translated_chunk.strip():
                    raise BtgTranslationException("번역 결과가 비어있습니다.")

                success = True
                
                translation_time = time.time() - translation_start_time
                translated_length = len(translated_chunk)
                
                # 번역 성능 상세는 DEBUG에서만
                if logger.isEnabledFor(logging.DEBUG):
                    speed = chunk_chars / translation_time if translation_time > 0 else 0
                    logger.debug(f"  ✅ 번역완료: {translated_length}자, {translation_time:.2f}초, {speed:.0f}자/초")
                
            except asyncio.TimeoutError:
                translation_time = time.time() - translation_start_time
                logger.error(f"  ❌ {current_chunk_info_msg} 타임아웃 (300초 초과, 실제: {translation_time:.1f}초)")
                translated_chunk = f"[타임아웃으로 번역 실패]\n\n--- 원문 내용 ---\n{chunk_text}"
                last_error = "Timeout (300초 초과)"
                success = False
                
            except asyncio.CancelledError:
                logger.warning(f"  ⚠️ {current_chunk_info_msg} 취소됨")
                raise
            
            # 파일 저장 (Lock 불필요, asyncio 단일 스레드)
            save_chunk_with_index_to_file(output_file, chunk_index, translated_chunk)
            
            if success:
                ratio = len(translated_chunk) / len(chunk_text) if len(chunk_text) > 0 else 0.0
                total_processing_time = time.time() - start_time
                logger.info(f"  🎯 {current_chunk_info_msg} 전체 처리 완료 (총 소요: {total_processing_time:.2f}초, 길이비율: {ratio:.2f})")
            
        except BtgTranslationException as e_trans:
            processing_time = time.time() - start_time
            error_type = "콘텐츠 검열" if "콘텐츠 안전 문제" in str(e_trans) else "번역 서비스"
            logger.error(f"  ❌ {current_chunk_info_msg} 실패: {error_type} - {e_trans} ({processing_time:.2f}초)")
            
            save_chunk_with_index_to_file(output_file, chunk_index, f"[번역 실패: {e_trans}]\n\n--- 원문 내용 ---\n{chunk_text}")
            last_error = str(e_trans)
            success = False
            
        except BtgApiClientException as e_api:
            processing_time = time.time() - start_time
            # API 오류 유형 판별
            error_detail = ""
            if "사용량 제한" in str(e_api) or "429" in str(e_api):
                error_detail = " [사용량 제한]"
            elif "키" in str(e_api).lower() or "인증" in str(e_api):
                error_detail = " [인증 오류]"
            logger.error(f"  ❌ {current_chunk_info_msg} API 오류{error_detail}: {e_api} ({processing_time:.2f}초)")
            
            save_chunk_with_index_to_file(output_file, chunk_index, f"[API 오류로 번역 실패: {e_api}]\n\n--- 원문 내용 ---\n{chunk_text}")
            last_error = str(e_api)
            success = False
            
        except asyncio.CancelledError:
            logger.info(f"  ⚠️ {current_chunk_info_msg} 취소됨 (CancelledError)")
            raise
            
        except Exception as e_gen:
            processing_time = time.time() - start_time
            logger.error(f"  ❌ {current_chunk_info_msg} 예상치 못한 오류: {type(e_gen).__name__} - {e_gen} ({processing_time:.2f}초)", exc_info=True)
            
            try:
                save_chunk_with_index_to_file(
                    output_file,
                    chunk_index,
                    f"[알 수 없는 오류로 번역 실패: {e_gen}]\n\n--- 원문 내용 ---\n{chunk_text}"
                )
            except Exception as save_err:
                logger.error(f"  ❌ 실패 청크 저장 중 오류: {save_err}")
            
            last_error = str(e_gen)
            success = False
        
        finally:
            total_time = time.time() - start_time
            # 상태 업데이트 (Lock 불필요, asyncio 단일 스레드)
            self.processed_chunks_count += 1
            if success:
                self.successful_chunks_count += 1
                self._record_translation_memory(chunk_index, chunk_text, translated_chunk)
                # ✅ 메타데이터 업데이트: translated_chunks에 완료된 청크 기록
                try:
                    metadata_updated = update_metadata_for_chunk_completion(
                        input_file_path,
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
            else:
                self.failed_chunks_count += 1
                # ❌ 실패한 청크 정보 기록
                if last_error:
                    try:
                        update_metadata_for_chunk_failure(input_file_path, chunk_index, last_error)
                        logger.debug(f"  💾 {current_chunk_info_msg} 실패 정보 메타데이터에 기록 완료")
                    except Exception as meta_fail_e:
                        logger.error(f"  ❌ {current_chunk_info_msg} 실패 정보 메타데이터 기록 중 오류: {meta_fail_e}")
            
            # 진행률 계산 및 통합 로깅 (2개 로그 → 1개)
            progress_percentage = (self.processed_chunks_count / total_chunks) * 100
            success_rate = (self.successful_chunks_count / self.processed_chunks_count) * 100 if self.processed_chunks_count > 0 else 0
            
            # 매 10% 또는 마지막 청크에서만 상세 로그 출력 (로그 빈도 최적화)
            should_log_progress = (self.processed_chunks_count % max(1, total_chunks // 10) == 0) or (self.processed_chunks_count == total_chunks)
            if should_log_progress:
                logger.info(f"  📈 진행률: {progress_percentage:.0f}% ({self.processed_chunks_count}/{total_chunks}) | 성공률: {success_rate:.0f}% (✅{self.successful_chunks_count} ❌{self.failed_chunks_count})")
            
            # 진행률 콜백
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
            
            logger.debug(f"  {current_chunk_info_msg} 처리 완료 반환: {success}")
            return success

    # ===== 끝: 비동기 메서드 =====

    # === LEGACY SYNC METHODS REMOVED ===
    # 다음 메서드들은 비동기 마이그레이션으로 인해 제거되었습니다:
    # - start_translation() (구 L1304-L1360)
    # - _translation_task() (구 L1363-L1756)
    # - stop_translation() (구 L1757-L1775)
    # 대신 start_translation_async()를 사용하세요.
    # CLI: asyncio.run(app_service.start_translation_async(...))
    # GUI (PySide6): await app_service.start_translation_async(...) with @asyncSlot







    def request_stop_translation(self):
        """
        비동기 번역 작업 취소 (즉시 반응)
        
        Task.cancel()을 사용하여 현재 진행 중인 asyncio Task를 즉시 취소합니다.
        기존 스레드 기반의 5-30초 대비 <1초로 개선됩니다.
        """
        if self.current_translation_task and not self.current_translation_task.done():
            logger.info("번역 취소 요청됨 (Task.cancel() 호출)")
            self.current_translation_task.cancel()
        else:
            logger.warning("현재 실행 중인 번역 작업이 없습니다")

    async def translate_single_chunk_with_force_split(
        self,
        input_file: str,
        chunked_output_file: str,
        chunk_idx: int,
        split_level: int = 1,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> tuple[bool, str]:
        """
        특정 청크를 강제 분할(Depth 1 시작)로 재번역합니다. (비동기)
        """
        if not self.translation_service:
            return False, "번역 서비스가 초기화되지 않았습니다."

        try:
            # 1. 원문 로드
            content = read_text_file(input_file)
            chunk_size = self.config.get("chunk_size", 6000)
            chunks_list = self.chunk_service.create_chunks_from_file_content(content, chunk_size)
            
            if chunk_idx >= len(chunks_list):
                return False, f"잘못된 청크 인덱스: {chunk_idx}"
            
            source_text = chunks_list[chunk_idx]
            if progress_callback:
                progress_callback(f"청크 #{chunk_idx} 강제 분할 재번역 시작 (Level {split_level})...")

            # 2. 강제 분할 번역 실행
            max_split = self.config.get("max_content_safety_split_attempts", 3)
            min_size = self.config.get("min_content_safety_chunk_size", 100)
            
            translated_text = await self.translation_service.translate_text_force_split_async(
                source_text, max_split, min_size, split_level=split_level
            )

            # 3. 결과 저장 및 메타데이터 갱신
            # (기존 logic 재사용을 위해 동기 래퍼 호출 가능하나, 여기서는 직접 처리 권장)
            from infrastructure import file_handler
            translated_chunks = file_handler.load_chunks_from_file(Path(chunked_output_file))
            translated_chunks[chunk_idx] = translated_text
            file_handler.save_merged_chunks_to_file(Path(chunked_output_file), translated_chunks)
            
            self._record_translation_memory(chunk_idx, source_text, translated_text)
            file_handler.update_metadata_for_chunk_completion(
                input_file,
                chunk_idx,
                source_length=len(source_text),
                translated_length=len(translated_text)
            )
            
            return True, translated_text

        except Exception as e:
            logger.error(f"청크 #{chunk_idx} 강제 분할 재번역 중 오류: {e}")
            return False, str(e)

    def translate_single_chunk(
        self,
        input_file_path: Union[str, Path],
        chunk_file_path: Union[str, Path],
        chunk_index: int,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> Tuple[bool, str]:
        """
        단일 청크를 재번역합니다.
        
        Args:
            input_file_path: 원본 입력 파일 경로.
            chunk_file_path: 업데이트할 청크 파일 경로.
            chunk_index: 재번역할 청크 인덱스
            progress_callback: 진행 상태 콜백 (상태 메시지)
            
        Returns:
            Tuple[bool, str]: (성공 여부, 번역된 텍스트 또는 오류 메시지)
        """
        if not self.translation_service:
            error_msg = "TranslationService가 초기화되지 않았습니다."
            logger.error(error_msg)
            return False, error_msg
        
        input_file_path_obj = Path(input_file_path)
        chunk_file_path_obj = Path(chunk_file_path)
        
        try:
            # 1. 원문을 원본 파일에서 동적으로 청킹하여 로드
            if not input_file_path_obj.exists():
                error_msg = f"원본 입력 파일이 존재하지 않습니다: {input_file_path_obj}"
                logger.error(error_msg)
                return False, error_msg
            
            file_content = read_text_file(input_file_path_obj)
            if not file_content:
                error_msg = "원본 파일이 비어있습니다."
                logger.error(error_msg)
                return False, error_msg
            
            chunk_size = self.config.get('chunk_size', 6000)
            all_chunks = self.chunk_service.create_chunks_from_file_content(file_content, chunk_size)
            
            if chunk_index >= len(all_chunks):
                error_msg = f"청크 #{chunk_index}가 범위를 벗어났습니다 (총 {len(all_chunks)}개)."
                logger.error(error_msg)
                return False, error_msg
            
            chunk_text = all_chunks[chunk_index]
            
            if progress_callback:
                progress_callback(f"청크 #{chunk_index} 번역 중...")
            
            logger.info(f"단일 청크 재번역 시작: 청크 #{chunk_index} (길이: {len(chunk_text)}자)")
            
            # 2. 번역 설정 로드
            use_content_safety_retry = self.config.get("use_content_safety_retry", True)
            max_split_attempts = self.config.get("max_content_safety_split_attempts", 3)
            min_chunk_size = self.config.get("min_content_safety_chunk_size", 100)
            
            # 3. 용어집 동적 로딩 (입력 파일에 맞는 용어집 자동 발견)
            try:
                glossary_suffix = self.config.get("glossary_output_json_filename_suffix", "_simple_glossary.json")
                assumed_glossary_path = input_file_path_obj.parent / f"{input_file_path_obj.stem}{glossary_suffix}"
                
                if assumed_glossary_path.exists():
                    self.config['glossary_json_path'] = str(assumed_glossary_path)
                    self.translation_service.config = self.config
                    self.translation_service._load_glossary_data()
                    logger.debug(f"재번역을 위해 용어집 로드: {assumed_glossary_path.name}")
            except Exception as e_glossary:
                logger.warning(f"용어집 로딩 중 오류 (무시하고 계속): {e_glossary}")
            
            # 4. 번역 수행 (비동기 버전 사용)
            start_time = time.time()
            
            # asyncio.run()을 사용하여 비동기 메서드 호출
            if use_content_safety_retry:
                translated_text = asyncio.run(
                    self.translation_service.translate_text_with_content_safety_retry_async(
                        chunk_text, max_split_attempts, min_chunk_size
                    )
                )
            else:
                translated_text = asyncio.run(
                    self.translation_service.translate_text_async(chunk_text)
                )
            
            translation_time = time.time() - start_time
            
            if not translated_text:
                error_msg = "번역 결과가 비어있습니다."
                logger.error(f"청크 #{chunk_index} 재번역 실패: {error_msg}")
                return False, error_msg
            
            # 5. 번역된 청크 파일 업데이트 (전달받은 chunk_file_path_obj 사용)
            translated_chunked_path = chunk_file_path_obj
            
            # 기존 번역된 청크 로드
            translated_chunks = {}
            if translated_chunked_path.exists():
                translated_chunks = load_chunks_from_file(translated_chunked_path)
            
            # 해당 청크 업데이트
            translated_chunks[chunk_index] = translated_text
            
            # 파일에 저장
            save_merged_chunks_to_file(translated_chunked_path, translated_chunks)
            
            self._record_translation_memory(chunk_index, chunk_text, translated_text)
            # 6. 메타데이터 업데이트
            update_metadata_for_chunk_completion(
                input_file_path_obj,
                chunk_index,
                source_length=len(chunk_text),
                translated_length=len(translated_text)
            )
            
            logger.info(f"청크 #{chunk_index} 재번역 완료 ({translation_time:.2f}초, {len(translated_text)}자)")
            
            if progress_callback:
                progress_callback(f"청크 #{chunk_index} 재번역 완료!")
            
            return True, translated_text
            
        except BtgTranslationException as e_trans:
            error_msg = f"번역 오류: {e_trans}"
            logger.error(f"청크 #{chunk_index} 재번역 실패: {error_msg}")
            
            # 실패 정보 기록
            try:
                update_metadata_for_chunk_failure(input_file_path_obj, chunk_index, str(e_trans))
            except Exception:
                pass
                
            return False, error_msg
            
        except BtgApiClientException as e_api:
            error_msg = f"API 오류: {e_api}"
            logger.error(f"청크 #{chunk_index} 재번역 실패: {error_msg}")
            
            try:
                update_metadata_for_chunk_failure(input_file_path_obj, chunk_index, str(e_api))
            except Exception:
                pass
                
            return False, error_msg
            
        except Exception as e_gen:
            error_msg = f"예상치 못한 오류: {e_gen}"
            logger.error(f"청크 #{chunk_index} 재번역 실패: {error_msg}", exc_info=True)
            
            try:
                update_metadata_for_chunk_failure(input_file_path_obj, chunk_index, str(e_gen))
            except Exception:
                pass
                
            return False, error_msg


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
