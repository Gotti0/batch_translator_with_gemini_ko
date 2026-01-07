# gemini_client.py
import os
import logging
import time
import random
import re
import json
import asyncio
from pathlib import Path
from typing import Dict, Any, Iterable, Optional, Union, List

# Google 관련 imports
from google import genai
from google.genai import types as genai_types # ThinkingConfig 포함
from google.genai.types import FinishReason  
from google.genai import errors as genai_errors
from google.auth.exceptions import GoogleAuthError, RefreshError
from google.api_core import exceptions as api_core_exceptions
from google.oauth2.service_account import Credentials as ServiceAccountCredentials


# Assuming logger_config is in infrastructure.logging
try:
    from ..infrastructure.logger_config import setup_logger # Relative import if logger_config is in the same parent package
except ImportError:
    from infrastructure.logger_config import setup_logger # Absolute for fallback or direct run
logger = setup_logger(__name__)

class GeminiApiException(Exception):
    """Gemini API 호출 관련 기본 예외 클래스"""
    def __init__(self, message: str, original_exception: Optional[Exception] = None):
        super().__init__(message)
        self.original_exception = original_exception

class GeminiRateLimitException(GeminiApiException):
    """API 사용량 제한 관련 예외 (429, QUOTA_EXCEEDED)"""
    pass

class GeminiContentSafetyException(GeminiApiException):
    """콘텐츠 안전 관련 예외 (SAFETY 필터링)"""
    pass

class GeminiInvalidRequestException(GeminiApiException):
    """잘못된 요청 관련 예외 (400, INVALID_ARGUMENT)"""
    pass

class GeminiAllApiKeysExhaustedException(GeminiApiException):
    """모든 API 키가 소진되거나 유효하지 않을 때 발생하는 예외"""
    pass

# Vertex AI 공식 API 오류 기준 추가 예외 클래스들

class BlockedPromptException(GeminiContentSafetyException):
    """프롬프트가 안전 필터에 의해 차단된 경우 발생하는 예외"""
    pass

class SafetyException(GeminiContentSafetyException):
    """안전성 필터에 의해 응답이 차단된 경우 발생하는 예외"""
    pass

class QuotaExceededException(GeminiRateLimitException):
    """API 할당량 초과 시 발생하는 예외"""
    pass

class ResourceExhaustedException(GeminiRateLimitException):
    """리소스 소진 시 발생하는 예외 (503)"""
    pass

class PermissionDeniedException(GeminiInvalidRequestException):
    """권한 거부 시 발생하는 예외 (403)"""
    pass

class UnauthenticatedException(GeminiInvalidRequestException):
    """인증 실패 시 발생하는 예외 (401)"""
    pass

class ModelNotFoundException(GeminiInvalidRequestException):
    """요청한 모델을 찾을 수 없을 때 발생하는 예외 (404)"""
    pass

class InternalServerException(GeminiApiException):
    """내부 서버 오류 시 발생하는 예외 (500)"""
    pass

class ServiceUnavailableException(GeminiApiException):
    """서비스 사용 불가 시 발생하는 예외 (503)"""
    pass

class InvalidModelException(GeminiInvalidRequestException):
    """유효하지 않은 모델명 사용 시 발생하는 예외"""
    pass

class ContentFilterException(GeminiContentSafetyException):
    """콘텐츠 필터링으로 인한 예외"""
    pass





class GeminiClient:
    _RATE_LIMIT_PATTERNS = [
        "rateLimitExceeded", "429", "Too Many Requests", "QUOTA_EXCEEDED",
        "The model is overloaded", "503", "Service Unavailable", 
        "Resource has been exhausted", "RESOURCE_EXHAUSTED"
    ]
    _TIMEOUT_SECONDS = 500.0

    _CONTENT_SAFETY_PATTERNS = [
        "PROHIBITED_CONTENT", "SAFETY", "response was blocked",
        "BLOCKED_PROMPT", "SAFETY_BLOCKED", "blocked due to safety",
        "INTERNAL", "500", "504", "DEADLINE_EXCEEDED"
    ]

    _INVALID_REQUEST_PATTERNS = [
        "Invalid API key", "API key not valid", "Permission denied",
        "Invalid model name", "model is not found", "400 Bad Request",
        "Invalid JSON payload", "Could not find model", 
        "Publisher Model .* not found", "invalid_scope", "INVALID_ARGUMENT",
        "UNAUTHENTICATED", "PERMISSION_DENIED", "NOT_FOUND"
    ]

    _VERTEX_AI_SCOPES = ['https://www.googleapis.com/auth/cloud-platform']
    _QUOTA_COOLDOWN_SECONDS = 100 # 100초


    def _get_api_key_identifier(self, api_key: str) -> str:
        """API 키의 안전한 식별자를 반환합니다."""
        if not self.api_keys_list or api_key not in self.api_keys_list:
            return f"단일키(...{api_key[-8:]})"
        
        key_index = self.api_keys_list.index(api_key)
        total_keys = len(self.api_keys_list)
        return f"키#{key_index+1}/{total_keys}(...{api_key[-8:]})"

    def _normalize_model_name(self, model_name: str, for_api_key_mode: bool = False) -> str:
        """
        모델명을 정규화합니다.
        API 키 모드에서는 모델명에 API 키를 포함시킬 수 있습니다.
        
        Args:
            model_name: 원본 모델명
            for_api_key_mode: API 키 모드인지 여부
            
        Returns:
            정규화된 모델명
        """
        if not model_name:
            raise ValueError("모델명이 제공되지 않았습니다.")
        
        # API 키 모드에서 현재 API 키를 모델명에 포함
        if for_api_key_mode and self.current_api_key:
            # google-genai SDK에서는 모델명에 API 키를 직접 포함시키지 않을 수 있음
            # 대신 Client 인스턴스가 API 키를 관리
            # 여기서는 단순히 모델명을 반환하되, 로그용으로 키 정보를 포함
            key_id = self._get_api_key_identifier(self.current_api_key)
            logger.debug(f"모델명 정규화: '{model_name}' (사용 키: {key_id})")
            return model_name
        
        # Vertex AI 모드 또는 환경 변수 API 키 모드에서는 모델명 그대로 사용
        return model_name

    def __init__(self,
                 auth_credentials: Optional[Union[str, List[str], Dict[str, Any]]] = None,
                 project: Optional[str] = None,
                 location: Optional[str] = None,
                 requests_per_minute: Optional[float] = None):
        
        logger.debug(f"[GeminiClient.__init__] 시작. auth_credentials 타입: {type(auth_credentials)}, project: '{project}', location: '{location}'")
        
        # Initialize all attributes first
        self.auth_mode: Optional[str] = None
        self.client: Optional[genai.Client] = None
        self.api_keys_list: List[str] = []
        self.current_api_key_index: int = 0
        self.current_api_key: Optional[str] = None
        self.client_pool: Dict[str, genai.Client] = {}
        self._key_rotation_lock = asyncio.Lock()
        self.key_quota_failure_times: Dict[str, float] = {}
        
        # Vertex AI related attributes
        self.vertex_credentials: Optional[Any] = None
        self.vertex_project: Optional[str] = None
        self.vertex_location: Optional[str] = None
        
        # HTTP Client Options for Timeout
        # [수정됨] google-genai SDK는 timeout을 밀리초 단위의 정수(int)로 받습니다.
        # 기존 client_args={'timeout': ...} 방식은 작동하지 않음이 확인되었습니다.
        timeout_ms = int(self._TIMEOUT_SECONDS * 1000)
        self.http_options = genai_types.HttpOptions(timeout=timeout_ms)
        
        # RPM control
        self.requests_per_minute = requests_per_minute or 140.0
        self.delay_between_requests = 60.0 / self.requests_per_minute  # 요청 간 지연 시간 계산
        self.last_request_timestamp = 0.0
        self._rpm_lock = asyncio.Lock()

        # Determine authentication mode and process credentials
        service_account_info: Optional[Dict[str, Any]] = None
        is_api_key_mode = False

        if isinstance(auth_credentials, list) and all(isinstance(key, str) for key in auth_credentials):
            # Multiple API keys provided
            self.api_keys_list = [key.strip() for key in auth_credentials if key.strip()]
            self.auth_mode = "API_KEY"
            is_api_key_mode = True
            
            # Create client instances for each API key
            successful_keys = []
            
            for key_value in self.api_keys_list:
                try:
                    sdk_client = genai.Client(api_key=key_value, http_options=self.http_options)
                    self.client_pool[key_value] = sdk_client
                    successful_keys.append(key_value)
                    
                    key_id = self._get_api_key_identifier(key_value)
                    logger.info(f"API {key_id}에 대한 SDK 클라이언트 생성 성공.")
                except Exception as e_sdk_init:
                    key_id = self._get_api_key_identifier(key_value)
                    logger.warning(f"API {key_id}에 대한 SDK 클라이언트 생성 실패: {e_sdk_init}")
            
            if successful_keys:
                self.api_keys_list = successful_keys
                self.current_api_key_index = 0
                self.current_api_key = self.api_keys_list[self.current_api_key_index]
                self.client = self.client_pool.get(self.current_api_key)
                
                key_id = self._get_api_key_identifier(self.current_api_key)
                logger.info(f"API 키 모드 설정 완료. 활성 클라이언트 풀 크기: {len(self.client_pool)}. 현재 사용 키: {key_id}")
            else:
                logger.error("모든 API 키에 대한 클라이언트 생성이 실패했습니다.")
                raise GeminiInvalidRequestException("제공된 API 키들로 유효한 클라이언트를 생성할 수 없습니다.")

        elif isinstance(auth_credentials, str):
            # Single API key or service account JSON string
            try:
                parsed_json = json.loads(auth_credentials)
                if isinstance(parsed_json, dict) and parsed_json.get("type") == "service_account":
                    service_account_info = parsed_json
                else: 
                    if auth_credentials.strip():
                        self.api_keys_list = [auth_credentials.strip()]
                        is_api_key_mode = True
            except json.JSONDecodeError: 
                if auth_credentials.strip():
                    self.api_keys_list = [auth_credentials.strip()]
                    is_api_key_mode = True

        elif isinstance(auth_credentials, dict) and auth_credentials.get("type") == "service_account":
            service_account_info = auth_credentials

        # Handle Vertex AI mode
        use_vertex_env_str = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "false").lower()
        explicit_vertex_flag = use_vertex_env_str == "true"

        if service_account_info: 
            # Vertex AI with service account
            self._setup_vertex_ai_with_service_account(service_account_info, project, location)
        elif explicit_vertex_flag: 
            # Vertex AI with ADC
            self._setup_vertex_ai_with_adc(project, location)
        elif is_api_key_mode:
            # API Key mode
            self._setup_api_key_mode()
        elif os.environ.get("GOOGLE_API_KEY"):
            # Environment variable API key
            self._setup_environment_api_key()
        else:
            raise GeminiInvalidRequestException("클라이언트 초기화를 위한 유효한 인증 정보(API 키 또는 서비스 계정)를 찾을 수 없습니다.")

        # Final client initialization
        try:
            if self.auth_mode == "VERTEX_AI":
                self._initialize_vertex_client()
            elif self.auth_mode == "API_KEY":
                self._initialize_api_key_client()
        except Exception as e:
            logger.error(f"클라이언트 초기화 실패: {e}", exc_info=True)
            raise

    def _setup_api_key_mode(self):
        """API 키 모드 설정"""
        self.auth_mode = "API_KEY"
        
        if not self.client and self.api_keys_list:
            # Single API key case - create client
            api_key = self.api_keys_list[0]
            try:
                self.client = genai.Client(api_key=api_key, http_options=self.http_options)
                self.current_api_key = api_key
                self.current_api_key_index = 0
                
                key_id = self._get_api_key_identifier(api_key)
                logger.info(f"단일 API 키 모드 설정 완료: {key_id}")
            except Exception as e:
                logger.error(f"단일 API 키 클라이언트 생성 실패: {e}")
                raise GeminiInvalidRequestException(f"API 키로 클라이언트 생성 실패: {e}")

    def _setup_environment_api_key(self):
        """환경 변수 API 키 설정"""
        self.auth_mode = "API_KEY"
        env_api_key = os.environ.get("GOOGLE_API_KEY")
        
        try:
            self.client = genai.Client(http_options=self.http_options)  # Environment variable will be used
            self.current_api_key = env_api_key
            self.api_keys_list = [env_api_key] if env_api_key else []
            
            logger.info(f"환경 변수 API 키로 클라이언트 생성 성공: ...{env_api_key[-8:] if env_api_key else 'N/A'}")
        except Exception as e:
            logger.error(f"환경 변수 API 키 클라이언트 생성 실패: {e}")
            raise GeminiInvalidRequestException(f"환경 변수 API 키로 클라이언트 생성 실패: {e}")

    def _initialize_api_key_client(self):
        """API 키 클라이언트 최종 초기화"""
        if not self.client:
            logger.info("Gemini Developer API용 Client 초기화 (API 키는 환경 변수 또는 호출 시 전달 가정).")
        else:
            logger.info("API 키 클라이언트가 이미 초기화되었습니다.")

    def _setup_vertex_ai_with_service_account(self, service_account_info: Dict[str, Any], project: Optional[str], location: Optional[str]):
        """서비스 계정 정보를 사용하여 Vertex AI 모드 설정"""
        self.auth_mode = "VERTEX_AI"
        logger.info("서비스 계정 정보 감지. Vertex AI 모드로 설정 시도.")
        try:
            self.vertex_credentials = ServiceAccountCredentials.from_service_account_info(
                service_account_info,
                scopes=self._VERTEX_AI_SCOPES
            )
            logger.info(f"서비스 계정 정보로부터 Credentials 객체 생성 완료 (범위: {self._VERTEX_AI_SCOPES}).")
        except Exception as e_sa_cred:
            logger.error(f"서비스 계정 정보로 Credentials 객체 생성 중 오류: {e_sa_cred}", exc_info=True)
            raise GeminiInvalidRequestException(f"서비스 계정 인증 정보 처리 중 오류: {e_sa_cred}") from e_sa_cred

        self.vertex_project = project or service_account_info.get("project_id") or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.vertex_location = location or os.environ.get("GOOGLE_CLOUD_LOCATION") or "asia-northeast3" 
        if not self.vertex_project:
            raise GeminiInvalidRequestException("Vertex AI 사용 시 프로젝트 ID가 필수입니다 (인자, SA JSON, 또는 GOOGLE_CLOUD_PROJECT 환경 변수).")
        if not self.vertex_location:
            raise GeminiInvalidRequestException("Vertex AI 사용 시 위치(location)가 필수입니다.")
        logger.info(f"Vertex AI 모드 설정: project='{self.vertex_project}', location='{self.vertex_location}'")

    def _setup_vertex_ai_with_adc(self, project: Optional[str], location: Optional[str]):
        """ADC (Application Default Credentials)를 사용하여 Vertex AI 모드 설정"""
        self.auth_mode = "VERTEX_AI"
        logger.info("GOOGLE_GENAI_USE_VERTEXAI=true 감지. Vertex AI 모드로 설정 (ADC 또는 환경 기반 인증 기대).")
        self.vertex_project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
        self.vertex_location = location or os.environ.get("GOOGLE_CLOUD_LOCATION") or "asia-northeast3"
        if not self.vertex_project:
            raise GeminiInvalidRequestException("Vertex AI 사용 시 프로젝트 ID가 필수입니다 (인자 또는 GOOGLE_CLOUD_PROJECT 환경 변수).")
        if not self.vertex_location: 
            raise GeminiInvalidRequestException("Vertex AI 사용 시 위치(location)가 필수입니다.")
        logger.info(f"Vertex AI 모드 (ADC) 설정: project='{self.vertex_project}', location='{self.vertex_location}'")

    def _initialize_vertex_client(self):
        """Vertex AI 클라이언트 초기화"""
        client_options = {}
        if self.vertex_project: client_options['project'] = self.vertex_project
        if self.vertex_location: client_options['location'] = self.vertex_location
        if self.vertex_credentials: client_options['credentials'] = self.vertex_credentials
        client_options['vertexai'] = True
        client_options['http_options'] = self.http_options
        
        # google-genai SDK에서는 Client()가 project, location 등을 직접 받지 않을 수 있음.
        # 이 경우, vertexai.init() 등을 사용해야 할 수 있음.
        # 우선은 이전 google.generativeai SDK의 Client와 유사하게 시도.
        self.client = genai.Client(**client_options)
        logger.info(f"Vertex AI용 Client 초기화 시도: {client_options}")


    async def _apply_rpm_delay(self):
        """요청 속도 제어를 위한 지연 적용 (동시성 개선) - Async"""
        if self.delay_between_requests <= 0:
            return

        sleep_time = 0
        async with self._rpm_lock:
            current_time = time.time()
            
            # 다음 요청이 가능한 가장 빠른 시간을 계산합니다.
            # (이전 요청 예약 시간 + 딜레이)와 현재 시간 중 더 나중의 시간을 선택하여,
            # 여러 스레드가 동시에 요청할 때 순차적으로 실행되도록 예약합니다.
            next_slot = max(self.last_request_timestamp + self.delay_between_requests, current_time)
            
            sleep_time = next_slot - current_time
            
            # 현재 요청이 실행될 예약 시간을 다음 요청을 위해 기록합니다.
            self.last_request_timestamp = next_slot

        if sleep_time > 0:
            # [[가이드]] sleep_time이 1초 이상일 경우, INFO 레벨로 로깅하여 지연 상황을 쉽게 인지하도록 함
            log_level = logging.INFO if sleep_time >= 1.0 else logging.DEBUG
            
            # 예약된 시작 시간 로깅 추가
            scheduled_start_time_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.last_request_timestamp))
            
            logger.log(log_level, f"RPM({self.requests_per_minute}) 제어: 다음 요청까지 {sleep_time:.3f}초 대기합니다. (예약된 시작: {scheduled_start_time_str})")
            await asyncio.sleep(sleep_time)

    def _is_rate_limit_error(self, error_obj: Any) -> bool:
        from google.api_core import exceptions as gapi_exceptions
    
        if isinstance(error_obj, (
            gapi_exceptions.ResourceExhausted,
            gapi_exceptions.DeadlineExceeded,
            gapi_exceptions.TooManyRequests
        )):
            return True
            
        return any(re.search(pattern, str(error_obj), re.IGNORECASE) 
                for pattern in self._RATE_LIMIT_PATTERNS)


    def _is_content_safety_error(self, response: Optional[Any] = None, error_obj: Optional[Any] = None) -> bool:
        if response:
            if hasattr(response, 'prompt_feedback') and response.prompt_feedback and response.prompt_feedback.block_reason:
                return True
            if hasattr(response, 'candidates') and response.candidates:
                for candidate in response.candidates:
                    if hasattr(candidate, 'finish_reason') and candidate.finish_reason == FinishReason.SAFETY:
                        return True
        
        # BlockedError 체크 제거 또는 수정
        # if isinstance(error_obj, genai_errors.BlockedError):  # 이 줄 주석 처리
        #     return True
        
        # 대신 문자열 패턴 매칭만 사용
        return any(re.search(pattern, str(error_obj), re.IGNORECASE) for pattern in self._CONTENT_SAFETY_PATTERNS)


    def _is_invalid_request_error(self, error_obj: Any) -> bool:
    # Google API Core의 표준 예외들 사용
        from google.api_core import exceptions as gapi_exceptions
        
        if isinstance(error_obj, (
            gapi_exceptions.InvalidArgument,
            gapi_exceptions.NotFound, 
            gapi_exceptions.PermissionDenied,
            gapi_exceptions.FailedPrecondition,
            gapi_exceptions.Unauthenticated
        )):
            return True
        
        return any(re.search(pattern, str(error_obj), re.IGNORECASE) 
                for pattern in self._INVALID_REQUEST_PATTERNS)



    # NOTE: Synchronous generate_text removed as part of async migration.
    # Use generate_text_async instead.


    async def _rotate_api_key_and_reconfigure(self) -> bool:
        logger.debug("API 키 회전: 락 획득 대기 중...")
        async with self._key_rotation_lock:
            logger.debug("API 키 회전: 락 획득.")
            if not self.api_keys_list or len(self.api_keys_list) <= 1: # No keys or only one successful key
                logger.warning("API 키 목록이 비어있거나 단일 유효 키만 있어 회전할 수 없습니다.")
                # If only one key, and it failed, there's nothing to rotate to.
                # If it's the only key and it's causing issues, this method shouldn't be called
                # or it should indicate no other options.
                self.client = None # Mark that no valid client is available after attempting rotation
                return False

            original_index = self.current_api_key_index
            for i in range(len(self.api_keys_list)): # Iterate once through all available successful keys
                self.current_api_key_index = (original_index + 1 + i) % len(self.api_keys_list)
                next_key = self.api_keys_list[self.current_api_key_index]

                # 키가 할당량 소진으로 쿨다운 중인지 확인
                last_failure = self.key_quota_failure_times.get(next_key)
                if last_failure and (time.time() - last_failure) < self._QUOTA_COOLDOWN_SECONDS:
                    key_id = self._get_api_key_identifier(next_key)
                    logger.info(f"API {key_id}는 최근 할당량 소진으로 인해 건너뜁니다 (쿨다운 중).")
                    continue  # 쿨다운 중인 키는 건너뛰고 다음 키를 시도
                
                # Check if a client for this key exists in our pool
                if next_key in self.client_pool:
                    self.current_api_key = next_key
                    self.client = self.client_pool[self.current_api_key]
                    
                    key_id = self._get_api_key_identifier(self.current_api_key)
                    logger.info(f"API 키를 {key_id}로 성공적으로 회전하고 클라이언트를 업데이트했습니다.")
                    return True
                else:
                    key_id = self._get_api_key_identifier(next_key)
                    logger.warning(f"회전 시도 중 API {key_id}에 대한 클라이언트를 풀에서 찾을 수 없습니다.")
            
            logger.error("유효한 다음 API 키로 회전하지 못했습니다. 모든 풀의 클라이언트가 유효하지 않을 수 있습니다.")
            self.client = None # No valid client found after trying all pooled keys
            logger.debug("API 키 회전: 락 해제.")
            return False

    async def list_models_async(self) -> List[Dict[str, Any]]:
        """비동기 모델 목록 조회"""
        if not self.client: 
             logger.error("list_models_async: self.client가 초기화되지 않았습니다.")
             raise GeminiApiException("모델 목록 조회 실패: 클라이언트가 유효하지 않습니다.")

        total_keys_for_list = len(self.api_keys_list) if self.auth_mode == "API_KEY" and self.api_keys_list else 1
        attempted_keys_for_list_models = 0

        while attempted_keys_for_list_models < total_keys_for_list:
            try:
                await self._apply_rpm_delay() 
                logger.info(f"사용 가능한 모델 목록 조회 중 (현재 API 키 인덱스: {self.current_api_key_index if self.auth_mode == 'API_KEY' else 'N/A'})...")
                models_info = []
                if not self.client: 
                    raise GeminiApiException("list_models_async: 루프 내에서 Client가 유효하지 않음.")

                # client.aio.models.list() returns an async iterator
                async for m in await self.client.aio.models.list(): 
                    full_model_name = m.name
                    short_model_name = ""
                    if isinstance(full_model_name, str):
                        short_model_name = full_model_name.split('/')[-1] if '/' in full_model_name else full_model_name
                    else: 
                        short_model_name = str(full_model_name)
                    
                    models_info.append({
                        "name": full_model_name,
                        "short_name": short_model_name, 
                        "base_model_id": getattr(m, "base_model_id", ""), 
                        "version": getattr(m, "version", ""), 
                        "display_name": m.display_name,
                        "description": m.description,
                        "input_token_limit": getattr(m, "input_token_limit", 0), 
                        "output_token_limit": getattr(m, "output_token_limit", 0), 
                    })
                logger.info(f"{len(models_info)}개의 모델을 찾았습니다.")
                return models_info

            except (GoogleAuthError, Exception) as e: 
                error_message = str(e)
                logger.warning(f"모델 목록 조회 중 API/인증 오류 발생: {type(e).__name__} - {error_message}")
                if isinstance(e, RefreshError) and 'invalid_scope' in error_message.lower():
                    logger.error(f"OAuth 범위 문제로 모델 목록 조회 실패: {error_message}")
                    raise GeminiInvalidRequestException(f"OAuth 범위 문제로 모델 목록 조회 실패: {error_message}") from e

                if self.auth_mode == "API_KEY" and self.api_keys_list and len(self.api_keys_list) > 1:
                    attempted_keys_for_list_models += 1 
                    if attempted_keys_for_list_models >= total_keys_for_list: 
                        logger.error("모든 API 키를 사용하여 모델 목록 조회에 실패했습니다.")
                        raise GeminiAllApiKeysExhaustedException("모든 API 키로 모델 목록 조회에 실패했습니다.") from e
                    
                    logger.info("다음 API 키로 회전하여 모델 목록 조회 재시도...")
                    if not await self._rotate_api_key_and_reconfigure():
                        logger.error("API 키 회전 또는 클라이언트 재설정 실패 (list_models_async).")
                        raise GeminiAllApiKeysExhaustedException("API 키 회전 중 문제 발생 또는 모든 키 시도됨 (list_models_async).") from e
                    if not self.client: 
                        logger.error("API 키 회전 후 유효한 클라이언트가 없습니다 (list_models_async).")
                        raise GeminiAllApiKeysExhaustedException("API 키 회전 후 유효한 클라이언트를 찾지 못했습니다 (list_models_async).")
                else: 
                    logger.error(f"모델 목록 조회 실패 (키 회전 불가 또는 Vertex 모드): {error_message}")
                    raise GeminiApiException(f"모델 목록 조회 실패: {error_message}") from e
        
        raise GeminiApiException("모델 목록 조회에 실패했습니다 (알 수 없는 내부 오류).")


    def _is_quota_exhausted_error(self, error_obj: Any) -> bool:
        """
        할당량 소진(RESOURCE_EXHAUSTED, QUOTA_EXCEEDED) 오류를 구체적으로 감지합니다.
        이런 오류의 경우 즉시 다음 API 키로 회전해야 합니다.
        """
        from google.api_core import exceptions as gapi_exceptions
        
        # Google API Core의 ResourceExhausted 예외 체크
        if isinstance(error_obj, gapi_exceptions.ResourceExhausted):
            return True
        
        # 특정 할당량 관련 패턴 체크
        quota_patterns = [
            "RESOURCE_EXHAUSTED", 
            "QUOTA_EXCEEDED",
            "Quota exceeded",
            "quota.*exceeded",
            "Resource has been exhausted",
            "resource.*exhausted"
        ]
        
        error_str = str(error_obj).lower()
        return any(re.search(pattern.lower(), error_str) for pattern in quota_patterns)

    # ============================================================================
    # 비동기 메서드 (Phase 2: asyncio 마이그레이션)
    # ============================================================================

    async def generate_text_async(
        self,
        prompt: Union[str, List[genai_types.Content]],
        model_name: str,
        generation_config_dict: Optional[Dict[str, Any]] = None,
        safety_settings_list_of_dicts: Optional[List[Dict[str, Any]]] = None,
        thinking_budget: Optional[int] = None,
        system_instruction_text: Optional[str] = None,
        max_retries: int = 5,
        initial_backoff: float = 2.0,
        max_backoff: float = 60.0,
        stream: bool = False,
        timeout: Optional[float] = None
    ) -> Optional[Union[str, Any]]:
        """
        비동기 텍스트 생성 메서드 (generate_text의 비동기 버전)
        
        Args:
            prompt: 프롬프트 (문자열 또는 Content 리스트)
            model_name: 모델명
            generation_config_dict: 생성 설정
            safety_settings_list_of_dicts: 안전성 설정 (무시됨)
            thinking_budget: 사고 예산
            system_instruction_text: 시스템 지시문
            max_retries: 최대 재시도 횟수
            initial_backoff: 초기 백오프 시간(초)
            max_backoff: 최대 백오프 시간(초)
            stream: 스트리밍 여부
            timeout: 타임아웃 시간(초)
            
        Returns:
            생성된 텍스트 또는 구조화된 출력
            
        Raises:
            asyncio.CancelledError: 작업이 취소된 경우
            asyncio.TimeoutError: 타임아웃 초과
            GeminiApiException: API 관련 오류
        """
        try:
            if timeout:
                return await asyncio.wait_for(
                    self._generate_text_async_impl(
                        prompt, model_name, generation_config_dict,
                        safety_settings_list_of_dicts, thinking_budget,
                        system_instruction_text, max_retries,
                        initial_backoff, max_backoff, stream
                    ),
                    timeout=timeout
                )
            else:
                return await self._generate_text_async_impl(
                    prompt, model_name, generation_config_dict,
                    safety_settings_list_of_dicts, thinking_budget,
                    system_instruction_text, max_retries,
                    initial_backoff, max_backoff, stream
                )
        except asyncio.TimeoutError:
            logger.error(f"API 호출 타임아웃 ({timeout}초): {model_name}")
            raise
        except asyncio.CancelledError:
            logger.info(f"API 호출이 취소됨: {model_name}")
            raise

    async def _generate_text_async_impl(
        self,
        prompt: Union[str, List[genai_types.Content]],
        model_name: str,
        generation_config_dict: Optional[Dict[str, Any]],
        safety_settings_list_of_dicts: Optional[List[Dict[str, Any]]],
        thinking_budget: Optional[int],
        system_instruction_text: Optional[str],
        max_retries: int,
        initial_backoff: float,
        max_backoff: float,
        stream: bool
    ) -> Optional[Union[str, Any]]:
        """generate_text의 실제 비동기 구현 (client.aio 사용)"""
        if not self.client:
            raise GeminiApiException("Gemini 클라이언트가 초기화되지 않았습니다.")
        if not model_name:
            raise ValueError("모델 이름이 제공되지 않았습니다.")
        
        is_api_key_mode_for_norm = self.auth_mode == "API_KEY" and bool(self.current_api_key) and not os.environ.get("GOOGLE_API_KEY")
        effective_model_name = self._normalize_model_name(model_name, for_api_key_mode=is_api_key_mode_for_norm)
        
        if isinstance(prompt, str):
            final_sdk_contents = [genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=prompt)])]
        elif isinstance(prompt, list) and all(isinstance(item, genai_types.Content) for item in prompt):
            final_sdk_contents = prompt
        else:
            raise ValueError("프롬프트는 문자열 또는 Content 객체의 리스트여야 합니다.")

        total_keys = len(self.api_keys_list) if self.auth_mode == "API_KEY" and self.api_keys_list else 1
        attempted_keys_count = 0

        while attempted_keys_count < total_keys:
            current_retry_for_this_key = 0
            current_backoff = initial_backoff
            
            if self.auth_mode == "API_KEY":
                key_id = self._get_api_key_identifier(self.current_api_key)
                logger.info(f"API {key_id}로 작업 시도.")
            elif self.auth_mode == "VERTEX_AI":
                logger.info(f"Vertex AI 모드로 작업 시도 (프로젝트: {self.vertex_project}).")
            
            if not self.client:
                logger.error("generate_text_async: self.client가 유효하지 않습니다.")
                if self.auth_mode == "API_KEY":
                    break
                else:
                    raise GeminiApiException("클라이언트가 유효하지 않으며 복구할 수 없습니다 (Vertex).")
            
            while current_retry_for_this_key <= max_retries:
                try:
                    # RPM 속도 제한 적용 (비동기 버전)
                    await self._apply_rpm_delay()
                    
                    logger.info(f"모델 '{effective_model_name}'에 텍스트 생성 요청 (시도: {current_retry_for_this_key + 1}/{max_retries + 1})")
                    
                    final_generation_config_params = generation_config_dict.copy() if generation_config_dict else {}
                    if 'http_options' not in final_generation_config_params:
                        final_generation_config_params['http_options'] = self.http_options
                    
                    if system_instruction_text and system_instruction_text.strip():
                        final_generation_config_params['system_instruction'] = system_instruction_text
                    
                    # 항상 OFF으로 안전 설정 강제 적용
                    if safety_settings_list_of_dicts:
                        logger.warning("safety_settings_list_of_dicts가 제공되었지만, 안전 설정이 모든 카테고리에 대해 OFF으로 강제 적용되어 무시됩니다.")
                    
                    # Thinking config 관련 필드를 미리 제거 (GenerateContentConfig에서 허용되지 않음)
                    thinking_level_from_dict = final_generation_config_params.pop("thinking_level", None)
                    thinking_budget_from_dict = final_generation_config_params.pop("thinking_budget", None)
                    
                    # Thinking config - 모델 타입에 따라 적절한 파라미터만 사용
                    check_name = effective_model_name.lower()
                    thinking_config = None
                    
                    if "gemini-3" in check_name:
                        # Gemini 3.0: thinking_level만 사용
                        # ThinkingLevel은 CaseInSensitiveEnum이므로 소문자도 작동하지만, 
                        # 명시적으로 enum 값 또는 대문자 문자열 사용 권장
                        level = thinking_level_from_dict or genai_types.ThinkingLevel.HIGH
                        thinking_config = genai_types.ThinkingConfig(thinking_level=level)
                        logger.info(f"Gemini 3 감지: Thinking Level='{level}' 적용.")
                        
                    elif "gemini-2.5" in check_name:
                        # Gemini 2.5: thinking_budget만 사용 (우선순위: 인자 > dict > 기본값)
                        if thinking_budget is not None:
                            budget = thinking_budget
                        else:
                            budget = thinking_budget_from_dict if thinking_budget_from_dict is not None else -1
                        thinking_config = genai_types.ThinkingConfig(thinking_budget=budget)
                        logger.info(f"Gemini 2.5 감지: Thinking Budget={budget} 적용.")
                        
                    if thinking_config:
                        final_generation_config_params['thinking_config'] = thinking_config
                    
                    forced_safety_settings = [
                        genai_types.SafetySetting(category=c, threshold=genai_types.HarmBlockThreshold.BLOCK_NONE)
                        for c in [
                            genai_types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                            genai_types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                            genai_types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                            genai_types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                            genai_types.HarmCategory.HARM_CATEGORY_CIVIC_INTEGRITY,
                        ]
                    ]
                    final_generation_config_params['safety_settings'] = forced_safety_settings
                    
                    sdk_generation_config = genai_types.GenerateContentConfig(**final_generation_config_params) if final_generation_config_params else None
                    
                    text_content_from_api: Optional[str] = None
                    if stream:
                        response_stream = await self.client.aio.models.generate_content_stream(
                            model=effective_model_name,
                            contents=final_sdk_contents,
                            config=sdk_generation_config
                        )
                        aggregated_parts = []
                        async for chunk_response in response_stream:
                            if hasattr(chunk_response, 'text') and chunk_response.text:
                                aggregated_parts.append(chunk_response.text)
                            if self._is_content_safety_error(response=chunk_response):
                                raise GeminiContentSafetyException("콘텐츠 안전 문제로 스트림 응답 차단")
                        text_content_from_api = "".join(aggregated_parts)
                    else:
                        response = await self.client.aio.models.generate_content(
                            model=effective_model_name,
                            contents=final_sdk_contents,
                            config=sdk_generation_config,
                        )
                        
                        if sdk_generation_config and sdk_generation_config.response_schema and \
                           sdk_generation_config.response_mime_type == "application/json" and \
                           hasattr(response, 'parsed') and response.parsed is not None:
                            return response.parsed
                        
                        if self._is_content_safety_error(response=response):
                            raise GeminiContentSafetyException("콘텐츠 안전 문제로 응답 차단")
                        
                        if hasattr(response, 'text') and response.text is not None:
                            text_content_from_api = response.text
                        elif hasattr(response, 'candidates') and response.candidates:
                            for candidate in response.candidates:
                                if hasattr(candidate, 'finish_reason') and candidate.finish_reason == FinishReason.STOP:
                                    if hasattr(candidate, 'content') and candidate.content and hasattr(candidate.content, 'parts'):
                                        text_content_from_api = "".join(part.text for part in candidate.content.parts if hasattr(part, "text") and part.text)
                                        break
                            if text_content_from_api is None:
                                text_content_from_api = ""
                    
                    if text_content_from_api is not None:
                        is_json_response_expected = generation_config_dict and \
                                                    generation_config_dict.get("response_mime_type") == "application/json"
                        if is_json_response_expected:
                            try:
                                cleaned_json_str = re.sub(r'^```json\s*', '', text_content_from_api.strip(), flags=re.IGNORECASE)
                                cleaned_json_str = re.sub(r'\s*```$', '', cleaned_json_str, flags=re.IGNORECASE)
                                return json.loads(cleaned_json_str.strip())
                            except json.JSONDecodeError as e_parse:
                                logger.warning(f"JSON 응답 파싱 실패: {e_parse}")
                                return text_content_from_api
                        else:
                            if not text_content_from_api.strip():
                                raise GeminiContentSafetyException("모델로부터 유효한 텍스트 응답을 받지 못했습니다 (빈 응답).")
                            return text_content_from_api
                    
                    raise GeminiApiException("모델로부터 유효한 텍스트 응답을 받지 못했습니다.")
                
                except GeminiContentSafetyException:
                    raise
                except asyncio.CancelledError:
                    logger.info(f"비동기 API 호출이 취소됨: {effective_model_name}")
                    raise
                except Exception as e:
                    error_message = str(e)
                    logger.warning(f"API 관련 오류 발생: {type(e).__name__} - {error_message}")
                    
                    if self._is_invalid_request_error(e):
                        if self.auth_mode == "API_KEY":
                            break
                        else:
                            raise GeminiInvalidRequestException(f"복구 불가능한 요청 오류: {error_message}") from e
                    elif self._is_rate_limit_error(e):
                        if self._is_quota_exhausted_error(e):
                            if self.current_api_key:
                                self.key_quota_failure_times[self.current_api_key] = time.time()
                            break
                        if current_retry_for_this_key < max_retries:
                            await asyncio.sleep(current_backoff + random.uniform(0,1))
                            current_retry_for_this_key += 1
                            current_backoff = min(current_backoff * 2, max_backoff)
                            continue
                        else:
                            break
                    elif "timeout" in error_message.lower() or "timed out" in error_message.lower():
                        if current_retry_for_this_key < max_retries:
                            await asyncio.sleep(current_backoff + random.uniform(0,1))
                            current_retry_for_this_key += 1
                            current_backoff = min(current_backoff * 2, max_backoff)
                            continue
                        else:
                            break
                    else:
                        if current_retry_for_this_key < max_retries:
                            await asyncio.sleep(current_backoff + random.uniform(0,1))
                            current_retry_for_this_key += 1
                            current_backoff = min(current_backoff * 2, max_backoff)
                            continue
                        else:
                            break
            
            attempted_keys_count += 1
            if attempted_keys_count < total_keys and self.auth_mode == "API_KEY":
                if not await self._rotate_api_key_and_reconfigure():
                    raise GeminiAllApiKeysExhaustedException("유효한 다음 API 키로 전환할 수 없습니다.")
            elif self.auth_mode == "VERTEX_AI":
                raise GeminiApiException("Vertex AI 요청이 최대 재시도 후에도 실패했습니다.")

        raise GeminiAllApiKeysExhaustedException("모든 API 키를 사용한 시도 후에도 텍스트 생성에 최종 실패했습니다.")


if __name__ == '__main__':
    import asyncio
    
    async def main_test():
        # ... (테스트 코드는 이전과 유사하게 유지하되, Client 및 generate_content 호출 방식 변경에 맞춰 수정 필요) ...
        print("Gemini 클라이언트 (신 SDK 패턴) 테스트 시작...")
        logging.basicConfig(level=logging.INFO)  # type: ignore

        api_key_single_valid = os.environ.get("TEST_GEMINI_API_KEY_SINGLE_VALID")
        sa_json_string_valid = os.environ.get("TEST_VERTEX_SA_JSON_STRING_VALID")
        gcp_project_for_vertex = os.environ.get("TEST_GCP_PROJECT_FOR_VERTEX") 
        gcp_location_for_vertex_from_env = os.environ.get("TEST_GCP_LOCATION_FOR_VERTEX", "asia-northeast3")

        print("\n--- 시나리오 1: Gemini Developer API (유효한 단일 API 키 - 환경 변수 사용) ---")
        if api_key_single_valid:
            original_env_key = os.environ.get("GOOGLE_API_KEY")
            os.environ["GOOGLE_API_KEY"] = api_key_single_valid
            try:
                client_dev_single = GeminiClient() # auth_credentials 없이 환경 변수 사용
                print(f"  [성공] Gemini Developer API 클라이언트 생성 (환경변수 GOOGLE_API_KEY 사용)")
                
                models_dev = await client_dev_single.list_models_async() # type: ignore
                if models_dev:
                    print(f"  [정보] DEV API 모델 수: {len(models_dev)}. 첫 모델: {models_dev[0].get('display_name', models_dev[0].get('short_name'))}")
                    test_model_name = "gemini-2.0-flash" # 신 SDK에서는 'models/' 접두사 없이 사용 가능할 수 있음
                    
                    print(f"  [테스트] 텍스트 생성 (모델: {test_model_name})...")
                    # API 키는 Client가 환경 변수에서 가져오거나, 모델 이름에 포함시켜야 함.
                    # 여기서는 Client가 환경 변수를 사용한다고 가정.
                    response = await client_dev_single.generate_text_async("Hello Gemini with new SDK!", model_name=test_model_name)
                    print(f"  [응답] {response[:100] if response else '없음'}...")
                else:
                    print("  [경고] DEV API에서 모델 목록을 가져오지 못했습니다.")
            except Exception as e:
                print(f"  [오류] 시나리오 1: {type(e).__name__} - {e}")
                logger.error("시나리오 1 상세 오류:", exc_info=True)
            finally:
                if original_env_key is not None: os.environ["GOOGLE_API_KEY"] = original_env_key
                else: os.environ.pop("GOOGLE_API_KEY", None)
        else:
            print("  [건너뜀] TEST_GEMINI_API_KEY_SINGLE_VALID 환경 변수 없음.")

        print("\n--- 시나리오 2: Vertex AI API (유효한 서비스 계정 JSON 문자열) ---")
        if sa_json_string_valid and gcp_project_for_vertex: 
            try:
                client_vertex_json_str = GeminiClient(
                    auth_credentials=sa_json_string_valid,
                    project=gcp_project_for_vertex, 
                    location=gcp_location_for_vertex_from_env 
                )
                print(f"  [성공] Vertex AI API 클라이언트 생성 (SA JSON, project='{client_vertex_json_str.vertex_project}', location='{client_vertex_json_str.vertex_location}')")
                
                models_vertex_json = await client_vertex_json_str.list_models_async()
                if models_vertex_json:
                    print(f"  [정보] Vertex AI 모델 수: {len(models_vertex_json)}. 첫 모델: {models_vertex_json[0].get('display_name', models_vertex_json[0].get('short_name')) if models_vertex_json else '없음'}")
                    
                    test_vertex_model_name_short = "gemini-1.5-flash-001" # 예시
                    found_vertex_model_info = next((m for m in models_vertex_json if m.get('short_name') == test_vertex_model_name_short), None)
                    
                    if not found_vertex_model_info and models_vertex_json: 
                        found_vertex_model_info = next((m for m in models_vertex_json if "text" in m.get("name","").lower() and "vision" not in m.get("name","").lower()), models_vertex_json[0])

                    if found_vertex_model_info:
                        actual_vertex_model_to_test = found_vertex_model_info['short_name'] or found_vertex_model_info['name']
                        print(f"  [테스트] 텍스트 생성 (모델: {actual_vertex_model_to_test})...")
                        response = await client_vertex_json_str.generate_text_async("Hello Vertex AI with new SDK!", model_name=actual_vertex_model_to_test)
                        print(f"  [응답] {response[:100] if response else '없음'}...")
                    else:
                        print(f"  [경고] 텍스트 생성을 위한 적절한 Vertex 모델을 찾지 못했습니다.")
                else:
                    print("  [경고] Vertex AI에서 모델을 가져오지 못했습니다.")
            except Exception as e:
                print(f"  [오류] 시나리오 2: {type(e).__name__} - {e}")
                logger.error("시나리오 2 상세 오류:", exc_info=True)
        else:
            print("  [건너뜀] TEST_VERTEX_SA_JSON_STRING_VALID 또는 TEST_GCP_PROJECT_FOR_VERTEX 환경 변수 없음.")
        
        print("\nGemini 클라이언트 (신 SDK 패턴) 테스트 종료.")

    asyncio.run(main_test())