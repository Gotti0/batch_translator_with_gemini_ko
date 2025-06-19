# gemini_client.py
import os
import logging
import time
import random
import re
import json
from pathlib import Path
import threading # Added for thread safety
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

    _CONTENT_SAFETY_PATTERNS = [
        "PROHIBITED_CONTENT", "SAFETY", "response was blocked",
        "BLOCKED_PROMPT", "SAFETY_BLOCKED", "blocked due to safety"
    ]

    _INVALID_REQUEST_PATTERNS = [
        "Invalid API key", "API key not valid", "Permission denied",
        "Invalid model name", "model is not found", "400 Bad Request",
        "Invalid JSON payload", "Could not find model", 
        "Publisher Model .* not found", "invalid_scope", "INVALID_ARGUMENT",
        "UNAUTHENTICATED", "PERMISSION_DENIED", "NOT_FOUND"
    ]

    _VERTEX_AI_SCOPES = ['https://www.googleapis.com/auth/cloud-platform']


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
                 requests_per_minute: Optional[int] = None):
        
        logger.debug(f"[GeminiClient.__init__] 시작. auth_credentials 타입: {type(auth_credentials)}, project: '{project}', location: '{location}'")
        
        # Initialize all attributes first
        self.auth_mode: Optional[str] = None
        self.client: Optional[genai.Client] = None
        self.api_keys_list: List[str] = []
        self.current_api_key_index: int = 0
        self.current_api_key: Optional[str] = None
        self.client_pool: Dict[str, genai.Client] = {}
        self._key_rotation_lock = threading.Lock()
        
        # Vertex AI related attributes
        self.vertex_credentials: Optional[Any] = None
        self.vertex_project: Optional[str] = None
        self.vertex_location: Optional[str] = None
          # RPM control
        self.requests_per_minute = requests_per_minute or 140
        self.delay_between_requests = 60.0 / self.requests_per_minute  # 요청 간 지연 시간 계산
        self.last_request_timestamp = 0.0
        self._rpm_lock = threading.Lock()

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
                    sdk_client = genai.Client(api_key=key_value)
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
                self.client = genai.Client(api_key=api_key)
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
            self.client = genai.Client()  # Environment variable will be used
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
        
        # google-genai SDK에서는 Client()가 project, location 등을 직접 받지 않을 수 있음.
        # 이 경우, vertexai.init() 등을 사용해야 할 수 있음.
        # 우선은 이전 google.generativeai SDK의 Client와 유사하게 시도.
        self.client = genai.Client(**client_options)
        logger.info(f"Vertex AI용 Client 초기화 시도: {client_options}")


    def _apply_rpm_delay(self):
        """요청 속도 제어를 위한 지연 적용 (동시성 개선)"""
        if self.delay_between_requests <= 0:
            return

        sleep_time = 0
        with self._rpm_lock:
            current_time = time.time()
            
            # 다음 요청이 가능한 가장 빠른 시간을 계산합니다.
            # (이전 요청 예약 시간 + 딜레이)와 현재 시간 중 더 나중의 시간을 선택하여,
            # 여러 스레드가 동시에 요청할 때 순차적으로 실행되도록 예약합니다.
            next_slot = max(self.last_request_timestamp + self.delay_between_requests, current_time)
            
            sleep_time = next_slot - current_time
            
            # 현재 요청이 실행될 예약 시간을 다음 요청을 위해 기록합니다.
            self.last_request_timestamp = next_slot

        if sleep_time > 0:
            logger.debug(f"RPM: {self.requests_per_minute}, Sleeping for {sleep_time:.3f}s to maintain rate limit.")
            time.sleep(sleep_time)

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



    def generate_text(
        self,
        prompt: Union[str, List[genai_types.Content]], # 변경: List[Content] 지원
        model_name: str,
        generation_config_dict: Optional[Dict[str, Any]] = None,
        safety_settings_list_of_dicts: Optional[List[Dict[str, Any]]] = None,
        thinking_budget: Optional[int] = None, # 사고 예산 파라미터 추가
        system_instruction_text: Optional[str] = None, 
        max_retries: int = 5, 
        initial_backoff: float = 2.0,
        max_backoff: float = 60.0,
        stream: bool = False
    ) -> Optional[Union[str, Any]]: 
        if not self.client:
            # 클라이언트가 초기화되지 않은 경우, 여기서 API 키 회전을 시도하는 것은 의미가 없을 수 있음.
            # 초기화 실패는 더 근본적인 문제일 가능성이 높음.
             raise GeminiApiException("Gemini 클라이언트가 초기화되지 않았습니다.")
        if not model_name:
            raise ValueError("모델 이름이 제공되지 않았습니다.")
        
        # API 키 모드이고, 현재 키가 설정되어 있으며, 환경 변수 GOOGLE_API_KEY가 없는 경우에만 모델 이름에 키 추가
        # 환경 변수가 설정되어 있다면 Client가 이를 사용할 것으로 기대.
        is_api_key_mode_for_norm = self.auth_mode == "API_KEY" and bool(self.current_api_key) and not os.environ.get("GOOGLE_API_KEY")
        effective_model_name = self._normalize_model_name(model_name, for_api_key_mode=is_api_key_mode_for_norm)
        
        final_sdk_contents: Iterable[genai_types.Content]
        if system_instruction_text:
            logger.debug(f"System instruction 제공됨: {system_instruction_text[:100]}...")
        
        if isinstance(prompt, str):
            # 단일 문자열 프롬프트는 사용자 역할의 단일 Content 객체로 변환
            final_sdk_contents = [genai_types.Content(role="user", parts=[genai_types.Part.from_text(text=prompt)])] # 명시적으로 text= 사용
        elif isinstance(prompt, list) and all(isinstance(item, genai_types.Content) for item in prompt):
            final_sdk_contents = prompt # 이미 List[Content] 형태이면 그대로 사용
        else:
            raise ValueError("프롬프트는 문자열 또는 Content 객체의 리스트여야 합니다.")

        total_keys = len(self.api_keys_list) if self.auth_mode == "API_KEY" and self.api_keys_list else 1
        attempted_keys_count = 0

        while attempted_keys_count < total_keys:
            current_retry_for_this_key = 0
            current_backoff = initial_backoff
            
            if self.auth_mode == "API_KEY":
                current_key_for_log = self.current_api_key # self.current_api_key should be set
                logger.info(f"API {current_key_for_log}로 작업 시도.")
                # _normalize_model_name에서 API 키가 모델명에 포함되도록 수정했으므로, 여기서 추가 작업 불필요
            elif self.auth_mode == "VERTEX_AI":
                 logger.info(f"Vertex AI 모드로 작업 시도 (프로젝트: {self.vertex_project}).")
            
            if not self.client: 
                logger.error("generate_text: self.client가 유효하지 않습니다.")
                if self.auth_mode == "API_KEY": break 
                else: raise GeminiApiException("클라이언트가 유효하지 않으며 복구할 수 없습니다 (Vertex).")

            while current_retry_for_this_key <= max_retries:
                try:
                    self._apply_rpm_delay() # RPM 지연 적용
                    logger.info(f"모델 '{effective_model_name}'에 텍스트 생성 요청 (시도: {current_retry_for_this_key + 1}/{max_retries + 1})")

                    # generation_config 및 safety_settings 준비
                    final_generation_config_params = generation_config_dict.copy() if generation_config_dict else {} # type: ignore
                    

                    # 항상 BLOCK_NONE으로 안전 설정 강제 적용
                    # 사용자가 safety_settings_list_of_dicts를 제공하더라도 무시됩니다.
                    
                    if safety_settings_list_of_dicts:
                        logger.warning("safety_settings_list_of_dicts가 제공되었지만, 안전 설정이 모든 카테고리에 대해 BLOCK_NONE으로 강제 적용되어 무시됩니다.")

                    # system_instruction을 GenerateContentConfig에 포함
                    if system_instruction_text and system_instruction_text.strip():
                        final_generation_config_params['system_instruction'] = system_instruction_text
                    elif 'system_instruction' in final_generation_config_params and not (system_instruction_text and system_instruction_text.strip()):
                         # generation_config_dict에 system_instruction이 있었는데, system_instruction_text가 비어있으면 제거
                         del final_generation_config_params['system_instruction']
                    
                    # thinking_budget이 제공되면 thinking_config를 설정합니다.
                    if thinking_budget is not None:
                        final_generation_config_params['thinking_config'] = genai_types.ThinkingConfig(
                            thinking_budget=thinking_budget
                        )
                        logger.info(f"Thinking budget 설정됨: {thinking_budget}")


                    forced_safety_settings = [
                        genai_types.SafetySetting(
                            category=genai_types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                            threshold=genai_types.HarmBlockThreshold.BLOCK_NONE,
                        ),
                        genai_types.SafetySetting(
                            category=genai_types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                            threshold=genai_types.HarmBlockThreshold.BLOCK_NONE,
                        ),
                        genai_types.SafetySetting(
                            category=genai_types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                            threshold=genai_types.HarmBlockThreshold.BLOCK_NONE,
                        ),
                        genai_types.SafetySetting(
                            category=genai_types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                            threshold=genai_types.HarmBlockThreshold.BLOCK_NONE,
                        ),
                        genai_types.SafetySetting(
                            category=genai_types.HarmCategory.HARM_CATEGORY_CIVIC_INTEGRITY,
                            threshold=genai_types.HarmBlockThreshold.BLOCK_NONE,
                        ),                    ]
                    final_generation_config_params['safety_settings'] = forced_safety_settings
                    
                    # response_schema 디버깅 로깅 추가
                    if 'response_schema' in final_generation_config_params:
                        logger.debug(f"response_schema 내용: {final_generation_config_params['response_schema']}")
                        logger.debug(f"response_schema 타입: {type(final_generation_config_params['response_schema'])}")

                    
                    sdk_generation_config = genai_types.GenerateContentConfig(**final_generation_config_params) if final_generation_config_params else None

                    
                    text_content_from_api: Optional[str] = None
                    if stream:
                        response = self.client.models.generate_content_stream(
                            model=effective_model_name,
                            contents=final_sdk_contents,
                            config=sdk_generation_config
                            # system_instruction 파라미터 제거
                        )
                        aggregated_parts = []
                        for chunk_response in response:
                            if hasattr(chunk_response, 'text') and chunk_response.text:
                                aggregated_parts.append(chunk_response.text)
                            elif hasattr(chunk_response, 'candidates') and chunk_response.candidates:
                                for candidate in chunk_response.candidates:
                                    # Streaming candidates might not always have finish_reason == STOP for intermediate parts
                                    # We primarily care about the content parts.
                                    if hasattr(candidate, 'content') and candidate.content and hasattr(candidate.content, 'parts') and candidate.content.parts:
                                        aggregated_parts.append("".join(part.text for part in candidate.content.parts if hasattr(part, "text") and part.text))
                            if self._is_content_safety_error(response=chunk_response): # 스트림의 각 청크 응답에 대해 안전성 검사
                                raise GeminiContentSafetyException("콘텐츠 안전 문제로 스트림의 일부 응답 차단")                    
                        text_content_from_api = "".join(aggregated_parts)
                    else:
                        response = self.client.models.generate_content(
                            model=effective_model_name,
                            contents=final_sdk_contents,
                            config=sdk_generation_config,
                            # system_instruction 파라미터 제거
                        )

                        # [[가이드]] 응답 객체 전체 분석 로깅 추가
                        logger.debug("비스트리밍 API 응답 객체 속성:")
                        for attr_name in dir(response):
                            if not attr_name.startswith('_'):
                                try:
                                    attr_value = getattr(response, attr_name)
                                    # 값의 길이가 너무 길면 일부만 로깅
                                    value_str = str(attr_value)
                                    if len(value_str) > 200:
                                        value_str = value_str[:200] + "..."
                                    logger.debug(f"  response.{attr_name}: {value_str}")
                                except Exception:
                                    logger.debug(f"  response.{attr_name}: <접근 불가>")
                        
                        # 구조화된 출력 (스키마 사용 시) 처리
                        if sdk_generation_config and sdk_generation_config.response_schema and \
                           sdk_generation_config.response_mime_type == "application/json" and \
                           hasattr(response, 'parsed') and response.parsed is not None:
                            logger.debug("GeminiClient: response.parsed를 구조화된 출력으로 반환합니다.")
                            return response.parsed

                        if self._is_content_safety_error(response=response):
                            raise GeminiContentSafetyException("콘텐츠 안전 문제로 응답 차단")
                        if hasattr(response, 'text') and response.text is not None:
                            text_content_from_api = response.text
                        elif hasattr(response, 'candidates') and response.candidates:
                            for candidate in response.candidates:
                                if hasattr(candidate, 'finish_reason') and candidate.finish_reason == FinishReason.STOP:
                                    if hasattr(candidate, 'content') and candidate.content and hasattr(candidate.content, 'parts') and candidate.content.parts:
                                        text_content_from_api = "".join(part.text for part in candidate.content.parts if hasattr(part, "text") and part.text)
                                        break 
                            if text_content_from_api is None:
                                text_content_from_api = ""

                    if text_content_from_api is not None:
                        # generation_config_dict가 None일 수 있으므로 확인
                        is_json_response_expected = generation_config_dict and \
                                                    generation_config_dict.get("response_mime_type") == "application/json"

                        if is_json_response_expected:
                            try:
                                logger.debug("GeminiClient에서 JSON 응답 파싱 시도 중.")
                                # 간단한 Markdown 코드 블록 제거
                                cleaned_json_str = re.sub(r'^```json\s*', '', text_content_from_api.strip(), flags=re.IGNORECASE)
                                cleaned_json_str = re.sub(r'\s*```$', '', cleaned_json_str, flags=re.IGNORECASE)
                                return json.loads(cleaned_json_str.strip()) # 파싱된 Python 객체 반환
                            except json.JSONDecodeError as e_parse:
                                logger.warning(f"GeminiClient에서 JSON 응답 파싱 실패 (mime type이 application/json임에도 불구하고): {e_parse}. 원본 문자열 반환. 원본: {text_content_from_api[:200]}...")
                                return text_content_from_api # 파싱 실패 시 원본 문자열 반환
                        else:
                            return text_content_from_api # JSON이 아니면 그냥 텍스트 반환
                    
                    raise GeminiApiException("모델로부터 유효한 텍스트 응답을 받지 못했습니다.")

                except GeminiContentSafetyException:
                # 콘텐츠 안전 예외는 즉시 상위로 전파
                    raise
                except GoogleAuthError as auth_e:
                    logger.warning(f"인증 오류 발생: {auth_e}")
                    if self._is_invalid_request_error(auth_e):
                        logger.error(f"복구 불가능한 인증 오류: {auth_e}")
                        if self.auth_mode == "API_KEY":
                            break
                        else:
                            raise GeminiInvalidRequestException(f"복구 불가능한 인증 오류: {auth_e}") from auth_e
                    # 인증 오류도 재시도 로직 적용
                    elif current_retry_for_this_key < max_retries:
                        time.sleep(current_backoff + random.uniform(0,1))
                        current_retry_for_this_key += 1
                        current_backoff = min(current_backoff * 2, max_backoff)
                        continue
                    else:
                        break
                except Exception as e:
                    error_message = str(e)
                    logger.warning(f"API 관련 오류 발생: {type(e).__name__} - {error_message}")
                    
                    if self._is_invalid_request_error(e):
                        logger.error(f"복구 불가능한 요청 오류 (현재 키/설정): {error_message}")
                        if self.auth_mode == "API_KEY":
                            break
                        else:
                            raise GeminiInvalidRequestException(f"복구 불가능한 요청 오류: {error_message}") from e
                    elif self._is_rate_limit_error(e):
                        logger.warning(f"API 사용량 제한/리소스 부족 감지: {error_message}")
                        
                        # RESOURCE_EXHAUSTED (할당량 초과) 또는 QUOTA_EXCEEDED인 경우 즉시 키 회전
                        if self._is_quota_exhausted_error(e):
                            current_key_id = self._get_api_key_identifier(self.current_api_key) if self.current_api_key else "Unknown"
                            logger.warning(f"할당량 소진 감지 ({current_key_id}). 즉시 다음 키로 회전을 시도합니다.")
                            break  # 현재 키에 대한 재시도 루프 탈출하여 키 회전 실행
                        
                        # 일반적인 rate limit의 경우 재시도 수행
                        if current_retry_for_this_key < max_retries:
                            time.sleep(current_backoff + random.uniform(0,1))
                            current_retry_for_this_key += 1
                            current_backoff = min(current_backoff * 2, max_backoff)
                            continue
                        else:
                            break
                    else:
                        if current_retry_for_this_key < max_retries:
                            time.sleep(current_backoff + random.uniform(0,1))
                            current_retry_for_this_key += 1
                            current_backoff = min(current_backoff * 2, max_backoff)
                            continue
                        else:
                            break
            
            attempted_keys_count += 1
            if attempted_keys_count < total_keys and self.auth_mode == "API_KEY":
                if not self._rotate_api_key_and_reconfigure():
                    logger.error("다음 API 키로 전환하거나 클라이언트를 재설정하는 데 실패했습니다.")
                    raise GeminiAllApiKeysExhaustedException("유효한 다음 API 키로 전환할 수 없습니다.")
                if not self.client: # Check if rotation resulted in a valid client
                    logger.error("API 키 회전 후 유효한 클라이언트가 없습니다. 모든 키가 소진된 것으로 간주합니다.")
                    raise GeminiAllApiKeysExhaustedException("API 키 회전 후 유효한 클라이언트를 찾지 못했습니다.")
            elif self.auth_mode == "VERTEX_AI": 
                logger.error("Vertex AI 모드에서 복구 불가능한 오류 발생 또는 최대 재시도 도달.")
                raise GeminiApiException("Vertex AI 요청이 최대 재시도 후에도 실패했습니다.")

        raise GeminiAllApiKeysExhaustedException("모든 API 키를 사용한 시도 후에도 텍스트 생성에 최종 실패했습니다.")


    def _rotate_api_key_and_reconfigure(self) -> bool:
        with self._key_rotation_lock:
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
            return False

    def list_models(self) -> List[Dict[str, Any]]:
        if not self.client: 
             logger.error("list_models: self.client가 초기화되지 않았습니다.")
             raise GeminiApiException("모델 목록 조회 실패: 클라이언트가 유효하지 않습니다.")

        # API 키 모드에서 list_models가 API 키를 어떻게 사용하는지 확인 필요.
        # genai.Client()가 환경 변수를 사용한다면, _rotate_api_key_and_reconfigure에서
        # 환경 변수를 설정하거나, 여기서 list_models 호출 시점에 임시로 설정할 수 있음.
        # 또는, list_models가 API 키를 인자로 받는다면 전달해야 함.
        # 현재는 Client가 환경 변수를 통해 API 키를 인지한다고 가정.

        total_keys_for_list = len(self.api_keys_list) if self.auth_mode == "API_KEY" and self.api_keys_list else 1
        attempted_keys_for_list_models = 0

        while attempted_keys_for_list_models < total_keys_for_list:
            try:
                self._apply_rpm_delay() # RPM 지연 적용
                logger.info(f"사용 가능한 모델 목록 조회 중 (현재 API 키 인덱스: {self.current_api_key_index if self.auth_mode == 'API_KEY' else 'N/A'})...")
                models_info = []
                if not self.client: 
                    raise GeminiApiException("list_models: 루프 내에서 Client가 유효하지 않음.")

                for m in self.client.models.list(): 
                    full_model_name = m.name
                    short_model_name = ""
                    if isinstance(full_model_name, str):
                        short_model_name = full_model_name.split('/')[-1] if '/' in full_model_name else full_model_name
                    else: # Should not happen based on type hints from SDK
                        short_model_name = str(full_model_name) # Fallback, log warning if necessary
                    
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
                    if not self._rotate_api_key_and_reconfigure():
                        logger.error("API 키 회전 또는 클라이언트 재설정 실패 (list_models).")
                        raise GeminiAllApiKeysExhaustedException("API 키 회전 중 문제 발생 또는 모든 키 시도됨 (list_models).") from e
                    if not self.client: # Check if rotation resulted in a valid client
                        logger.error("API 키 회전 후 유효한 클라이언트가 없습니다 (list_models).")
                        raise GeminiAllApiKeysExhaustedException("API 키 회전 후 유효한 클라이언트를 찾지 못했습니다 (list_models).")
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

if __name__ == '__main__':
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
            
            models_dev = client_dev_single.list_models() # type: ignore
            if models_dev:
                print(f"  [정보] DEV API 모델 수: {len(models_dev)}. 첫 모델: {models_dev[0].get('display_name', models_dev[0].get('short_name'))}")
                test_model_name = "gemini-2.0-flash" # 신 SDK에서는 'models/' 접두사 없이 사용 가능할 수 있음
                
                print(f"  [테스트] 텍스트 생성 (모델: {test_model_name})...")
                # API 키는 Client가 환경 변수에서 가져오거나, 모델 이름에 포함시켜야 함.
                # 여기서는 Client가 환경 변수를 사용한다고 가정.
                response = client_dev_single.generate_text("Hello Gemini with new SDK!", model_name=test_model_name)
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
            
            models_vertex_json = client_vertex_json_str.list_models()
            if models_vertex_json:
                print(f"  [정보] Vertex AI 모델 수: {len(models_vertex_json)}. 첫 모델: {models_vertex_json[0].get('display_name', models_vertex_json[0].get('short_name')) if models_vertex_json else '없음'}")
                
                test_vertex_model_name_short = "gemini-1.5-flash-001" # 예시
                found_vertex_model_info = next((m for m in models_vertex_json if m.get('short_name') == test_vertex_model_name_short), None)
                
                if not found_vertex_model_info and models_vertex_json: 
                    found_vertex_model_info = next((m for m in models_vertex_json if "text" in m.get("name","").lower() and "vision" not in m.get("name","").lower()), models_vertex_json[0])

                if found_vertex_model_info:
                    actual_vertex_model_to_test = found_vertex_model_info['short_name'] or found_vertex_model_info['name']
                    print(f"  [테스트] 텍스트 생성 (모델: {actual_vertex_model_to_test})...")
                    response = client_vertex_json_str.generate_text("Hello Vertex AI with new SDK!", model_name=actual_vertex_model_to_test)
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