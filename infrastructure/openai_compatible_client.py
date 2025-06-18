# openai_compatible_client.py
import json
import time
import requests
from typing import Dict, Any, List, Optional
import logging

try:
    from .llm_client_interface import LLMClientInterface
except ImportError:
    from infrastructure.llm_client_interface import LLMClientInterface

try:
    from ..infrastructure.logger_config import setup_logger
except ImportError:
    from infrastructure.logger_config import setup_logger

logger = setup_logger(__name__)

class OpenAICompatibleException(Exception):
    """OpenAI 호환 API 클라이언트 관련 기본 예외"""
    def __init__(self, message: str, status_code: Optional[int] = None, original_exception: Optional[Exception] = None):
        super().__init__(message)
        self.status_code = status_code
        self.original_exception = original_exception

class OpenAICompatibleRateLimitException(OpenAICompatibleException):
    """API 사용량 제한 관련 예외"""
    pass

class OpenAICompatibleAuthException(OpenAICompatibleException):
    """인증 관련 예외"""
    pass

class OpenAICompatibleInvalidRequestException(OpenAICompatibleException):
    """잘못된 요청 관련 예외"""
    pass

class OpenAICompatibleClient(LLMClientInterface):
    """
    OpenAI 호환 API (GitHub Copilot 등)를 위한 클라이언트 구현
    
    GitHub Copilot API는 OpenAI ChatCompletions API와 호환되는 형식을 사용합니다.
    """
    
    def __init__(
        self,
        api_url: str,
        api_key: str,
        model_name: str = "gpt-4o",
        timeout: int = 60,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        user_agent: str = "Neo-Batch-Translator/1.0"
    ):
        """
        OpenAI 호환 클라이언트 초기화
        
        Args:
            api_url: API 엔드포인트 URL
            api_key: API 키 또는 액세스 토큰
            model_name: 사용할 모델명
            timeout: 요청 타임아웃 (초)
            max_retries: 최대 재시도 횟수
            retry_delay: 재시도 간격 (초)
            user_agent: User-Agent 헤더
        """
        self.api_url = api_url.rstrip('/')
        self.api_key = api_key
        self.model_name = model_name
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        
        # HTTP 세션 설정
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": user_agent
        })
        
        logger.info(f"OpenAI 호환 클라이언트 초기화: {self.api_url}, 모델: {self.model_name}")
    
    def _prepare_payload(
        self,
        messages: List[Dict[str, Any]],
        **kwargs
    ) -> Dict[str, Any]:
        """
        입력 메시지를 GitHub Copilot API 형식으로 변환합니다.
        
        Args:
            messages: 표준 메시지 형식 리스트
            **kwargs: 추가 생성 옵션
            
        Returns:
            Dict[str, Any]: API 요청에 사용할 페이로드
        """
        # 기본 페이로드 구성
        payload = {
            "model": kwargs.get("model_name", self.model_name),
            "messages": [],
            "stream": kwargs.get("stream", False)
        }
        
        # 선택적 매개변수들
        if "temperature" in kwargs:
            payload["temperature"] = float(kwargs["temperature"])
        
        if "max_tokens" in kwargs:
            payload["max_tokens"] = int(kwargs["max_tokens"])
        
        if "top_p" in kwargs:
            payload["top_p"] = float(kwargs["top_p"])
        
        if "frequency_penalty" in kwargs:
            payload["frequency_penalty"] = float(kwargs["frequency_penalty"])
        
        if "presence_penalty" in kwargs:
            payload["presence_penalty"] = float(kwargs["presence_penalty"])
        
        if "stop" in kwargs:
            payload["stop"] = kwargs["stop"]
        
        # 메시지 변환
        converted_messages = []
        
        for message in messages:
            role = message.get("role", "user")
            content = message.get("content", "")
            
            # OpenAI API 표준 역할 검증
            if role not in ["system", "user", "assistant"]:
                logger.warning(f"알 수 없는 역할 '{role}'을 'user'로 변경합니다.")
                role = "user"
            
            converted_message = {
                "role": role,
                "content": content
            }
            
            # 추가 속성이 있으면 포함
            if "name" in message:
                converted_message["name"] = message["name"]
            
            converted_messages.append(converted_message)
        
        payload["messages"] = converted_messages
        
        logger.debug(f"API 페이로드 준비 완료: {len(converted_messages)}개 메시지")
        return payload
    
    def _handle_api_error(self, response: requests.Response) -> None:
        """
        API 오류 응답을 처리하고 적절한 예외를 발생시킵니다.
        
        Args:
            response: HTTP 응답 객체
            
        Raises:
            OpenAICompatibleException: 상황에 맞는 예외
        """
        status_code = response.status_code
        
        try:
            error_data = response.json()
            error_message = error_data.get("error", {}).get("message", "알 수 없는 오류")
            error_type = error_data.get("error", {}).get("type", "unknown")
        except (json.JSONDecodeError, AttributeError):
            error_message = f"HTTP {status_code}: {response.text[:200]}"
            error_type = "unknown"
        
        logger.error(f"API 오류 - 상태 코드: {status_code}, 메시지: {error_message}")
        
        if status_code == 401:
            raise OpenAICompatibleAuthException(
                f"인증 실패: {error_message}",
                status_code=status_code
            )
        elif status_code == 429:
            raise OpenAICompatibleRateLimitException(
                f"사용량 제한 초과: {error_message}",
                status_code=status_code
            )
        elif status_code in [400, 422]:
            raise OpenAICompatibleInvalidRequestException(
                f"잘못된 요청: {error_message}",
                status_code=status_code
            )
        else:
            raise OpenAICompatibleException(
                f"API 오류 ({status_code}): {error_message}",
                status_code=status_code
            )
    
    def _parse_response(self, response_data: Dict[str, Any]) -> str:
        """
        API 응답을 파싱하여 텍스트를 추출합니다.
        
        Args:
            response_data: API 응답 데이터
            
        Returns:
            str: 생성된 텍스트
            
        Raises:
            OpenAICompatibleException: 응답 파싱 실패 시
        """
        try:
            choices = response_data.get("choices", [])
            if not choices:
                raise OpenAICompatibleException("응답에 선택지가 없습니다.")
            
            first_choice = choices[0]
            message = first_choice.get("message", {})
            content = message.get("content", "")
            
            if not content:
                # 스트리밍이 아닌 경우 delta에서 확인
                delta = first_choice.get("delta", {})
                content = delta.get("content", "")
            
            if not content:
                logger.warning("응답에서 내용을 찾을 수 없습니다.")
                return ""
            
            return content
            
        except (KeyError, TypeError, IndexError) as e:
            error_msg = f"응답 파싱 실패: {e}"
            logger.error(error_msg)
            raise OpenAICompatibleException(error_msg) from e
    
    def generate_completion(
        self,
        messages: List[Dict[str, Any]],
        **kwargs
    ) -> str:
        """
        LLMClientInterface의 generate_completion 메서드 구현.
        
        Args:
            messages: 대화 메시지 목록
            **kwargs: 추가 생성 옵션
            
        Returns:
            str: 생성된 텍스트 응답
            
        Raises:
            OpenAICompatibleException: API 호출 실패 시
        """
        if not messages:
            raise OpenAICompatibleInvalidRequestException("메시지가 비어있습니다.")
        
        # 페이로드 준비
        payload = self._prepare_payload(messages, **kwargs)
        
        last_exception = None
        
        # 재시도 로직
        for attempt in range(self.max_retries + 1):
            try:
                logger.debug(f"API 요청 시도 {attempt + 1}/{self.max_retries + 1}")
                
                response = self.session.post(
                    self.api_url,
                    json=payload,
                    timeout=self.timeout
                )
                
                # 성공적인 응답 처리
                if response.status_code == 200:
                    response_data = response.json()
                    result = self._parse_response(response_data)
                    
                    logger.info(f"API 요청 성공: {len(result)}자 응답")
                    return result
                
                # 오류 응답 처리
                self._handle_api_error(response)
                
            except (OpenAICompatibleRateLimitException, requests.Timeout) as e:
                last_exception = e
                if attempt < self.max_retries:
                    delay = self.retry_delay * (2 ** attempt)  # 지수 백오프
                    logger.warning(f"재시도 대기: {delay}초 (시도 {attempt + 1})")
                    time.sleep(delay)
                    continue
                else:
                    logger.error(f"최대 재시도 횟수 초과: {e}")
                    raise
                    
            except (OpenAICompatibleAuthException, OpenAICompatibleInvalidRequestException):
                # 인증 오류나 잘못된 요청은 재시도하지 않음
                raise
                
            except requests.RequestException as e:
                last_exception = e
                if attempt < self.max_retries:
                    delay = self.retry_delay * (2 ** attempt)
                    logger.warning(f"네트워크 오류로 재시도: {delay}초 (시도 {attempt + 1})")
                    time.sleep(delay)
                    continue
                else:
                    error_msg = f"네트워크 요청 실패: {e}"
                    logger.error(error_msg)
                    raise OpenAICompatibleException(error_msg) from e
        
        # 모든 재시도 실패
        if last_exception:
            raise last_exception
        else:
            raise OpenAICompatibleException("알 수 없는 오류로 요청 실패")
    
    def get_available_models(self) -> List[str]:
        """
        사용 가능한 모델 목록을 반환합니다.
        
        OpenAI 호환 API의 경우 /models 엔드포인트를 사용하거나,
        GitHub Copilot처럼 고정된 모델을 사용하는 경우 설정된 모델을 반환합니다.
        
        Returns:
            List[str]: 사용 가능한 모델명 목록
        """
        try:
            # /models 엔드포인트 시도
            models_url = f"{self.api_url.replace('/chat/completions', '/models')}"
            response = self.session.get(models_url, timeout=30)
            
            if response.status_code == 200:
                models_data = response.json()
                models = models_data.get("data", [])
                return [model.get("id", "") for model in models if model.get("id")]
            else:
                logger.warning(f"모델 목록 조회 실패: HTTP {response.status_code}")
                return [self.model_name]  # 기본 모델 반환
                
        except Exception as e:
            logger.warning(f"모델 목록 조회 중 오류: {e}")
            return [self.model_name]  # 기본 모델 반환
    
    def is_available(self) -> bool:
        """
        클라이언트가 현재 사용 가능한지 확인합니다.
        
        Returns:
            bool: 사용 가능하면 True
        """
        if not self.api_key or not self.api_url:
            return False
        
        try:
            # 간단한 테스트 요청
            test_messages = [{"role": "user", "content": "test"}]
            payload = self._prepare_payload(test_messages, max_tokens=1)
            
            response = self.session.post(
                self.api_url,
                json=payload,
                timeout=10
            )
            
            # 200 또는 400 (잘못된 요청이지만 API는 동작)이면 사용 가능
            return response.status_code in [200, 400]
            
        except Exception as e:
            logger.debug(f"가용성 확인 실패: {e}")
            return False
    
    def validate_config(self) -> Dict[str, str]:
        """
        클라이언트 설정의 유효성을 검사합니다.
        
        Returns:
            Dict[str, str]: 설정 검증 결과
        """
        errors = {}
        
        if not self.api_url:
            errors["api_url"] = "API URL이 설정되지 않았습니다."
        elif not self.api_url.startswith(("http://", "https://")):
            errors["api_url"] = "올바른 URL 형식이 아닙니다."
        
        if not self.api_key:
            errors["api_key"] = "API 키가 설정되지 않았습니다."
        
        if not self.model_name:
            errors["model_name"] = "모델명이 설정되지 않았습니다."
        
        if self.timeout <= 0:
            errors["timeout"] = "타임아웃은 0보다 커야 합니다."
        
        return errors
    
    def supports_streaming(self) -> bool:
        """OpenAI 호환 API는 스트리밍을 지원합니다."""
        return True
    
    def get_max_context_length(self) -> Optional[int]:
        """
        모델별 최대 컨텍스트 길이를 반환합니다.
        
        Returns:
            Optional[int]: 최대 컨텍스트 길이 (토큰 수)
        """
        # 일반적인 OpenAI 모델 컨텍스트 길이
        model_context_lengths = {
            "gpt-4o": 128000,
            "gpt-4o-mini": 128000,
            "gpt-4": 8192,
            "gpt-4-32k": 32768,
            "gpt-3.5-turbo": 4096,
            "gpt-3.5-turbo-16k": 16384,
            "claude-3-sonnet": 200000,
            "claude-3-haiku": 200000,
            "claude-3-opus": 200000,
        }
        
        return model_context_lengths.get(self.model_name.lower(), 4096)
    
    def get_supported_formats(self) -> List[str]:
        """OpenAI 호환 API는 기본적으로 텍스트를 지원합니다."""
        return ["text"]
    
    def get_client_type(self) -> str:
        """클라이언트 타입을 반환합니다."""
        return "openai_compatible"

if __name__ == "__main__":
    # 테스트 코드
    print("OpenAI 호환 클라이언트 테스트 시작...")
    
    # 기본 설정으로 클라이언트 생성 (실제 API 키 없이)
    try:
        client = OpenAICompatibleClient(
            api_url="https://api.githubcopilot.com/chat/completions",
            api_key="test_key",
            model_name="gpt-4o"
        )
        
        print("✅ OpenAICompatibleClient 인스턴스 생성 성공")
        print(f"클라이언트 타입: {client.get_client_type()}")
        print(f"스트리밍 지원: {client.supports_streaming()}")
        print(f"최대 컨텍스트 길이: {client.get_max_context_length()}")
        print(f"지원 형식: {client.get_supported_formats()}")
        
        # 설정 검증
        validation_errors = client.validate_config()
        if validation_errors:
            print("설정 검증 오류:", validation_errors)
        else:
            print("✅ 모든 설정이 유효합니다.")
        
        # 페이로드 준비 테스트
        test_messages = [
            {"role": "user", "content": "안녕하세요"}
        ]
        payload = client._prepare_payload(test_messages, temperature=0.7, max_tokens=100)
        print("✅ 페이로드 준비 테스트 성공")
        print(f"페이로드 메시지 수: {len(payload['messages'])}")
        
        # 사용 가능성 확인 (실제 API 호출 없이)
        print(f"사용 가능 여부 (모의): {bool(client.api_key and client.api_url)}")
        
    except Exception as e:
        print(f"❌ 테스트 실패: {e}")
    
    print("OpenAI 호환 클라이언트 테스트 종료.")
