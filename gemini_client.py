# gemini_client.py
import os
import logging
import time
import random
import re
import json
import requests
from pathlib import Path
from typing import Dict, Any, Optional, Union, List

# Google 관련 imports
from google import genai
from google.genai import types as genai_types
from google.genai.types import FinishReason  
from google.genai import errors as genai_errors
from google.auth.exceptions import GoogleAuthError, RefreshError
from google.api_core import exceptions as api_core_exceptions
from google.oauth2.service_account import Credentials as ServiceAccountCredentials


logger = logging.getLogger(__name__)

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


    def __init__(self,
                 auth_credentials: Optional[Union[str, List[str], Dict[str, Any]]] = None,
                 project: Optional[str] = None,
                 location: Optional[str] = None):
        logger.debug(f"[GeminiClient.__init__] 시작. auth_credentials 타입: {type(auth_credentials)}, project: '{project}', location: '{location}'")

        self.client: Optional[genai.Client] = None 
        self.auth_mode: Optional[str] = None 
        self.api_keys_list: List[str] = []
        self.current_api_key_index: int = 0
        self.current_api_key: Optional[str] = None
        self.vertex_project: Optional[str] = None
        self.vertex_location: Optional[str] = None
        self.vertex_credentials: Optional[ServiceAccountCredentials] = None

        service_account_info: Optional[Dict[str, Any]] = None
        is_api_key_mode = False

        if isinstance(auth_credentials, list) and all(isinstance(key, str) for key in auth_credentials):
            self.api_keys_list = [key.strip() for key in auth_credentials if key.strip()]
            if self.api_keys_list:
                is_api_key_mode = True
        elif isinstance(auth_credentials, str):
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

        use_vertex_env_str = os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "false").lower()
        explicit_vertex_flag = use_vertex_env_str == "true"

        if service_account_info: 
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
        elif explicit_vertex_flag: 
            self.auth_mode = "VERTEX_AI"
            logger.info("GOOGLE_GENAI_USE_VERTEXAI=true 감지. Vertex AI 모드로 설정 (ADC 또는 환경 기반 인증 기대).")
            self.vertex_project = project or os.environ.get("GOOGLE_CLOUD_PROJECT")
            self.vertex_location = location or os.environ.get("GOOGLE_CLOUD_LOCATION") or "asia-northeast3"
            if not self.vertex_project:
                raise GeminiInvalidRequestException("Vertex AI 사용 시 프로젝트 ID가 필수입니다 (인자 또는 GOOGLE_CLOUD_PROJECT 환경 변수).")
            if not self.vertex_location: 
                raise GeminiInvalidRequestException("Vertex AI 사용 시 위치(location)가 필수입니다.")
            logger.info(f"Vertex AI 모드 (ADC) 설정: project='{self.vertex_project}', location='{self.vertex_location}'")
        elif is_api_key_mode:
            self.auth_mode = "API_KEY"
            if not self.api_keys_list:
                 raise GeminiInvalidRequestException("API 키 모드이지만 유효한 API 키가 제공되지 않았습니다.")
            self.current_api_key = self.api_keys_list[self.current_api_key_index]
            logger.info(f"API 키 모드 설정. 사용 키: {self.current_api_key[:5]}...")
        elif os.environ.get("GOOGLE_API_KEY"): 
            env_api_key = os.environ.get("GOOGLE_API_KEY","").strip()
            if env_api_key:
                self.auth_mode = "API_KEY"
                self.api_keys_list = [env_api_key] # 환경 변수 키를 목록의 첫 번째로 사용
                self.current_api_key = self.api_keys_list[0]
                logger.info(f"GOOGLE_API_KEY 환경 변수 사용. 키: {self.current_api_key[:5]}...")
            else: 
                raise GeminiInvalidRequestException("GOOGLE_API_KEY 환경 변수가 설정되어 있으나 값이 비어있습니다.")
        else:
            raise GeminiInvalidRequestException("클라이언트 초기화를 위한 유효한 인증 정보(API 키 또는 서비스 계정)를 찾을 수 없습니다.")

        try:
            if self.auth_mode == "VERTEX_AI":
                # 신 SDK에서 Vertex AI Client 초기화 방식 확인 필요
                # 마이그레이션 가이드에 따르면 genai.Client(vertexai=True, project=...) 형태일 수 있음
                # 또는 vertexai.init(project=...) 후 genai.Client() 일 수도 있음
                # 우선은 project, location, credentials를 직접 전달 시도
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
            elif self.auth_mode == "API_KEY":
                if not self.current_api_key:
                    raise GeminiInvalidRequestException("API 키가 설정되지 않았습니다.")
                
                # API 키를 환경변수에도 설정 (Client가 자동 인식하도록)
                if not os.environ.get("GOOGLE_API_KEY"):
                    os.environ["GOOGLE_API_KEY"] = self.current_api_key
                    logger.info(f"GOOGLE_API_KEY 환경변수 설정: {self.current_api_key[:5]}...")
                
                try:
                    # 새로운 SDK에서는 api_key 파라미터로 직접 전달
                    self.client = genai.Client(api_key=self.current_api_key)
                    logger.info(f"Gemini Developer API용 Client 초기화 완료 (API 키: {self.current_api_key[:5]}...)")
                except Exception as e:
                    logger.error(f"API 키 직접 전달 방식 실패: {e}")
                    try:
                        # 환경변수 방식으로 재시도
                        self.client = genai.Client()
                        logger.info(f"Gemini Developer API용 Client 초기화 완료 (환경변수 방식)")
                    except Exception as e2:
                        logger.error(f"환경변수 방식도 실패: {e2}")
                        raise GeminiInvalidRequestException(f"Client 초기화 실패: {e2}") from e2
            else: 
                raise GeminiInvalidRequestException("클라이언트 초기화 로직 오류: 인증 모드가 설정되었으나 필요한 정보가 부족합니다.")
        except AttributeError as e_attr: # 'Client' 클래스 또는 인자 관련 오류
             logger.error(f"Client 초기화 중 AttributeError: {e_attr}. 'from google import genai'로 가져온 모듈에 Client 클래스가 없거나, 인자가 다를 수 있습니다.", exc_info=True)
             raise GeminiInvalidRequestException(f"SDK Client 초기화 실패 (AttributeError): {e_attr}") from e_attr
        except GoogleAuthError as auth_e:
            logger.error(f"Gemini 클라이언트 인증 오류: {auth_e}", exc_info=True)
            if isinstance(auth_e, RefreshError) and 'invalid_scope' in str(auth_e).lower():
                 logger.error(f"OAuth 범위 문제로 인한 인증 실패 가능성: {auth_e}")
                 raise GeminiInvalidRequestException(f"클라이언트 인증 실패 (OAuth 범위 문제 가능성): {auth_e}") from auth_e
            raise GeminiInvalidRequestException(f"클라이언트 인증 실패: {auth_e}") from auth_e
        except Exception as e: 
            logger.error(f"Gemini API 클라이언트 생성 중 예상치 못한 오류: {type(e).__name__} - {e}", exc_info=True)
            raise GeminiInvalidRequestException(f"Gemini API 클라이언트 생성 실패: {e}") from e

    def _normalize_model_name(self, model_name: str, for_api_key_mode: bool = False) -> str:
        # API 키 모드에서 client.models.generate_content 사용 시,
        # 모델 이름에 API 키를 포함해야 할 수 있음: "models/gemini-pro?key=YOUR_API_KEY"
        # 또는 "gemini-pro"만 전달하고 API 키는 Client가 환경 변수 등에서 가져오도록 함.
        # Vertex AI는 보통 "gemini-1.5-pro-001" 또는 전체 경로.
        
        if for_api_key_mode and self.current_api_key:
            # `models/` 접두사가 없는 경우 추가하고, API 키를 쿼리 파라미터로 추가
            # 예: "gemini-1.5-flash-latest" -> "models/gemini-1.5-flash-latest?key=YOUR_API_KEY"
            # 이미 "models/"가 있으면 키만 추가
            if not model_name.startswith("models/"):
                model_name_with_prefix = f"models/{model_name}"
            else:
                model_name_with_prefix = model_name
            
            # 이미 키가 포함되어 있는지 확인 (중복 추가 방지)
            if "?key=" not in model_name_with_prefix:
                logger.debug(f"API 키 모드: 모델 이름 '{model_name_with_prefix}'에 API 키 추가.")
                return f"{model_name_with_prefix}?key={self.current_api_key}"
            else: # 이미 키가 포함된 경우 (예: 설정 파일에서 직접 입력)
                logger.debug(f"API 키 모드: 모델 이름 '{model_name_with_prefix}'에 이미 API 키가 포함되어 있습니다.")
                return model_name_with_prefix # 이미 키가 포함된 경우 그대로 반환
        
        # Vertex AI 또는 API 키가 없는 경우 (환경 변수 사용 기대)
        return model_name


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
        if not self.client:
             raise GeminiApiException("Gemini 클라이언트가 초기화되지 않았습니다.")
        if not model_name:
            raise ValueError("모델 이름이 제공되지 않았습니다.")
        
        # API 키 모드이고, 현재 키가 설정되어 있으며, 환경 변수 GOOGLE_API_KEY가 없는 경우에만 모델 이름에 키 추가
        # 환경 변수가 설정되어 있다면 Client가 이를 사용할 것으로 기대.
        is_api_key_mode_for_norm = self.auth_mode == "API_KEY" and bool(self.current_api_key) and not os.environ.get("GOOGLE_API_KEY")
        effective_model_name = self._normalize_model_name(model_name, for_api_key_mode=is_api_key_mode_for_norm)
        
        final_contents: List[Union[str, genai_types.Part]] = [] # genai_types.Part 사용
        if system_instruction_text:
            # 신 SDK에서 시스템 프롬프트를 contents의 일부로 전달하는 방식이 있다면 여기에 구현
            # 예: final_contents.append(genai_types.Part(text=system_instruction_text, role="system")) (SDK가 지원한다면)
            logger.warning("system_instruction_text는 현재 client.models.generate_content에서 직접 지원되지 않을 수 있습니다. 프롬프트에 포함시켜주세요.")
        
        if isinstance(prompt, str):
            final_contents.append(prompt)
        elif isinstance(prompt, list):
            final_contents.extend(prompt)
        else:
            raise ValueError("프롬프트는 문자열 또는 (문자열 또는 Part 객체의) 리스트여야 합니다.")

        total_keys = len(self.api_keys_list) if self.auth_mode == "API_KEY" and self.api_keys_list else 1
        attempted_keys_count = 0

        while attempted_keys_count < total_keys:
            current_retry_for_this_key = 0
            current_backoff = initial_backoff
            
            if self.auth_mode == "API_KEY":
                current_key_for_log = self.current_api_key or os.environ.get("GOOGLE_API_KEY", "N/A")
                logger.info(f"API 키 '{current_key_for_log[:5]}...'로 작업 시도.")
                # _normalize_model_name에서 API 키가 모델명에 포함되도록 수정했으므로, 여기서 추가 작업 불필요
            elif self.auth_mode == "VERTEX_AI":
                 logger.info(f"Vertex AI 모드로 작업 시도 (프로젝트: {self.vertex_project}).")
            
            if not self.client: 
                logger.error("generate_text: self.client가 유효하지 않습니다.")
                if self.auth_mode == "API_KEY": break 
                else: raise GeminiApiException("클라이언트가 유효하지 않으며 복구할 수 없습니다 (Vertex).")

            while current_retry_for_this_key <= max_retries:
                try:
                    logger.info(f"모델 '{effective_model_name}'에 텍스트 생성 요청 (시도: {current_retry_for_this_key + 1}/{max_retries + 1})")

                    # 수정된 코드
                    if stream:
                        response = self.client.models.generate_content_stream(
                            model=effective_model_name,
                            contents=final_contents,
                            config=genai_types.GenerateContentConfig(
                                **generation_config_dict
                            ) if generation_config_dict else None
                        )
                    else:
                        response = self.client.models.generate_content(
                            model=effective_model_name,
                            contents=final_contents,
                            config=genai_types.GenerateContentConfig(
                                **generation_config_dict
                            ) if generation_config_dict else None
                        )


                    if self._is_content_safety_error(response=response):
                        # ... (안전 오류 처리) ...
                        raise GeminiContentSafetyException("콘텐츠 안전 문제로 응답 차단")

                    if hasattr(response, 'text') and response.text is not None:
                        return response.text
                    elif hasattr(response, 'candidates') and response.candidates:
                        # ... (candidates 처리) ...
                        for candidate in response.candidates:
                            if hasattr(candidate, 'finish_reason') and candidate.finish_reason == FinishReason.STOP:
                                if hasattr(candidate, 'content') and candidate.content and hasattr(candidate.content, 'parts') and candidate.content.parts:
                                    return "".join(part.text for part in candidate.content.parts if hasattr(part, "text") and part.text)
                                return "" # 내용 없는 정상 종료
                        raise GeminiApiException("텍스트 생성이 비정상적으로 종료되었거나 내용이 없습니다.")
                    
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
            elif self.auth_mode == "VERTEX_AI": 
                logger.error("Vertex AI 모드에서 복구 불가능한 오류 발생 또는 최대 재시도 도달.")
                raise GeminiApiException("Vertex AI 요청이 최대 재시도 후에도 실패했습니다.")

        raise GeminiAllApiKeysExhaustedException("모든 API 키를 사용한 시도 후에도 텍스트 생성에 최종 실패했습니다.")


    def _rotate_api_key_and_reconfigure(self) -> bool:
        if not self.api_keys_list or len(self.api_keys_list) == 0:
            logger.warning("API 키 목록이 비어있어 키 회전을 수행할 수 없습니다.")
            return False

        self.current_api_key_index = (self.current_api_key_index + 1) % len(self.api_keys_list)
        self.current_api_key = self.api_keys_list[self.current_api_key_index]
        logger.info(f"다음 API 키로 전환 시도: {self.current_api_key[:5]}... (인덱스: {self.current_api_key_index})")

        try:
            # Client 객체는 API 키를 직접 받지 않는다고 가정하고, 환경 변수 GOOGLE_API_KEY를
            # 이 시점에 업데이트하거나, _normalize_model_name에서 처리하도록 함.
            # 여기서는 self.current_api_key만 업데이트하고, Client는 그대로 둠.
            # self.client = google_genai_top_level.Client() # Client 재생성은 불필요할 수 있음
            logger.info(f"현재 API 키를 '{self.current_api_key[:5]}...'로 업데이트했습니다.")
            return True
        except Exception as e:
            logger.error(f"API 키 업데이트 중 오류: {e}", exc_info=True)
            # self.client = None # Client 재생성 실패 시 None으로 설정할 수 있으나, 여기서는 current_api_key만 변경
            return False

    def list_models(self) -> List[Dict[str, Any]]:
        """모델 목록 조회 - API 키 모드는 REST API 직접 호출, Vertex AI는 기존 로직 사용"""
        
        if self.auth_mode == "API_KEY":
            return self._list_models_via_rest_api()
        elif self.auth_mode == "VERTEX_AI":
            return self._list_models_via_client()
        else:
            raise GeminiApiException("인증 모드가 설정되지 않았습니다.")
    
    def _list_models_via_rest_api(self) -> List[Dict[str, Any]]:
        """API 키 모드: REST API 직접 호출"""
        if not self.api_keys_list:
            raise GeminiApiException("API 키가 설정되지 않았습니다.")
        
        total_keys = len(self.api_keys_list)
        attempted_keys = 0
        
        while attempted_keys < total_keys:
            current_api_key = self.api_keys_list[self.current_api_key_index]
            
            try:
                logger.info(f"REST API로 모델 목록 조회 중 (API 키: {current_api_key[:5]}...)")
                
                # REST API 엔드포인트 호출
                url = f"https://generativelanguage.googleapis.com/v1beta/models?key={current_api_key}"
                headers = {
                    'Content-Type': 'application/json',
                }
                
                response = requests.get(url, headers=headers, timeout=30)
                
                if response.status_code == 200:
                    data = response.json()
                    models_info = []
                    
                    if 'models' in data:
                        for model_data in data['models']:
                            # 실험용 모델 필터링 개선
                            standard_name = self._extract_standard_model_name_from_api(model_data)
                            
                            models_info.append({
                                "name": model_data.get("name", ""),
                                "short_name": standard_name,
                                "base_model_id": model_data.get("baseModelId", ""),
                                "version": model_data.get("version", ""),
                                "display_name": model_data.get("displayName", ""),
                                "description": model_data.get("description", ""),
                                "input_token_limit": model_data.get("inputTokenLimit", 0),
                                "output_token_limit": model_data.get("outputTokenLimit", 0),
                                "supported_actions": model_data.get("supportedGenerationMethods", []),
                            })
                    
                    logger.info(f"REST API로 {len(models_info)}개의 모델을 조회했습니다.")
                    return models_info
                    
                elif response.status_code == 401:
                    logger.warning(f"API 키 인증 실패 (401): {current_api_key[:5]}...")
                    attempted_keys += 1
                    if attempted_keys < total_keys:
                        self._rotate_api_key_and_reconfigure()
                        continue
                    else:
                        raise GeminiInvalidRequestException("모든 API 키에서 인증 실패")
                        
                elif response.status_code == 429:
                    logger.warning(f"API 사용량 제한 (429): {current_api_key[:5]}...")
                    attempted_keys += 1
                    if attempted_keys < total_keys:
                        self._rotate_api_key_and_reconfigure()
                        continue
                    else:
                        raise GeminiRateLimitException("모든 API 키에서 사용량 제한")
                        
                else:
                    error_text = response.text
                    logger.error(f"REST API 호출 실패 ({response.status_code}): {error_text}")
                    attempted_keys += 1
                    if attempted_keys < total_keys:
                        self._rotate_api_key_and_reconfigure()
                        continue
                    else:
                        raise GeminiApiException(f"REST API 호출 실패: {response.status_code} - {error_text}")
                        
            except requests.exceptions.RequestException as e:
                logger.error(f"REST API 요청 중 네트워크 오류: {e}")
                attempted_keys += 1
                if attempted_keys < total_keys:
                    self._rotate_api_key_and_reconfigure()
                    continue
                else:
                    raise GeminiApiException(f"네트워크 오류로 모델 목록 조회 실패: {e}")
                    
            except Exception as e:
                logger.error(f"REST API 호출 중 예상치 못한 오류: {e}", exc_info=True)
                attempted_keys += 1
                if attempted_keys < total_keys:
                    self._rotate_api_key_and_reconfigure()
                    continue
                else:
                    raise GeminiApiException(f"모델 목록 조회 중 알 수 없는 오류: {e}")
        
        raise GeminiAllApiKeysExhaustedException("모든 API 키로 REST API 호출에 실패했습니다.")

    def _list_models_via_client(self) -> List[Dict[str, Any]]:
        """Vertex AI 모드: 기존 클라이언트 로직 사용"""
        if not self.client:
            raise GeminiApiException("Vertex AI 클라이언트가 초기화되지 않았습니다.")
        
        try:
            logger.info(f"Vertex AI 클라이언트로 모델 목록 조회 중 (프로젝트: {self.vertex_project})...")
            models_info = []
            
            for m in self.client.models.list():
                standard_model_name = self._get_standard_model_name(m)
                models_info.append({
                    "name": m.name,
                    "short_name": standard_model_name,
                    "base_model_id": getattr(m, "base_model_id", ""),
                    "version": getattr(m, "version", ""),
                    "display_name": m.display_name,
                    "description": m.description,
                    "input_token_limit": getattr(m, "input_token_limit", 0),
                    "output_token_limit": getattr(m, "output_token_limit", 0),
                    "supported_actions": getattr(m, "supported_actions", []),
                })
            
            logger.info(f"Vertex AI에서 {len(models_info)}개의 모델을 조회했습니다.")
            return models_info
            
        except Exception as e:
            logger.error(f"Vertex AI 모델 목록 조회 중 오류: {e}", exc_info=True)
            raise GeminiApiException(f"Vertex AI 모델 목록 조회 실패: {e}")

    def _extract_standard_model_name_from_api(self, model_data: Dict[str, Any]) -> str:
        """REST API 응답에서 표준 모델명 추출 (실험용 모델 형식 개선)"""
        
        # 1순위: baseModelId 사용
        if model_data.get("baseModelId"):
            return model_data["baseModelId"]
        
        # 2순위: name에서 추출
        model_name = model_data.get("name", "")
        if model_name and "/" in model_name:
            extracted_name = model_name.split("/")[-1]
            
            return extracted_name
        
        # 3순위: displayName 정규화
        display_name = model_data.get("displayName", "")
        if display_name:
            normalized = display_name.lower().replace(" ", "-")
            
            # 실험용 모델 패턴 검사
            if self._is_experimental_model(normalized):
                return self._normalize_experimental_model_name(normalized)
            
            return normalized
        
        return "unknown-model"

    



    


if __name__ == '__main__':
    # ... (테스트 코드는 이전과 유사하게 유지하되, Client 및 generate_content 호출 방식 변경에 맞춰 수정 필요) ...
    print("Gemini 클라이언트 (신 SDK 패턴) 테스트 시작...")
    logging.basicConfig(level=logging.INFO) 

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
            
            models_dev = client_dev_single.list_models()
            if models_dev:
                print(f"  [정보] DEV API 모델 수: {len(models_dev)}. 첫 모델: {models_dev[0].get('display_name', models_dev[0].get('short_name')) if models_dev else '없음'}")
                test_model_name = "gemini-1.5-flash-latest" # 신 SDK에서는 'models/' 접두사 없이 사용 가능할 수 있음
                
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
