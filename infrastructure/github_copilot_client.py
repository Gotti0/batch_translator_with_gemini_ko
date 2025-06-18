# github_copilot_client.py
from typing import Dict, Any, List, Optional
import logging

try:
    from .openai_compatible_client import OpenAICompatibleClient, OpenAICompatibleException
    from ..core.config.config_manager import ConfigManager
except ImportError:
    from infrastructure.openai_compatible_client import OpenAICompatibleClient, OpenAICompatibleException
    from core.config.config_manager import ConfigManager

try:
    from ..infrastructure.logger_config import setup_logger
except ImportError:
    from infrastructure.logger_config import setup_logger

logger = setup_logger(__name__)

class GitHubCopilotClient(OpenAICompatibleClient):
    """
    GitHub Copilot API 전용 클라이언트
    
    ConfigManager와 연동하여 설정을 자동으로 로드하고,
    GitHub Copilot API의 특성에 맞게 최적화된 클라이언트입니다.
    """
    
    def __init__(self, config_manager: Optional[ConfigManager] = None):
        """
        GitHub Copilot 클라이언트 초기화
        
        Args:
            config_manager: 설정 관리자 인스턴스. None이면 기본 인스턴스 생성
        """
        self.config_manager = config_manager or ConfigManager()
        
        # GitHub Copilot 설정 로드
        copilot_config = self.config_manager.get_github_copilot_config()
        
        if not copilot_config.get("github_copilot_enabled", False):
            raise OpenAICompatibleException("GitHub Copilot이 비활성화되어 있습니다.")
        
        api_key = copilot_config.get("github_copilot_access_token", "")
        if not api_key:
            raise OpenAICompatibleException("GitHub Copilot 액세스 토큰이 설정되지 않았습니다.")
        
        api_url = copilot_config.get("github_copilot_api_url", "https://api.githubcopilot.com/chat/completions")
        model_name = copilot_config.get("github_copilot_model_name", "gpt-4o")
        
        # 부모 클래스 초기화
        super().__init__(
            api_url=api_url,
            api_key=api_key,
            model_name=model_name,
            timeout=60,
            max_retries=3,
            retry_delay=1.0,
            user_agent="Neo-Batch-Translator-GitHub-Copilot/1.0"
        )
        
        logger.info(f"GitHub Copilot 클라이언트 초기화 완료: {model_name}")
    
    def _prepare_payload(
        self,
        messages: List[Dict[str, Any]],
        **kwargs
    ) -> Dict[str, Any]:
        """
        GitHub Copilot API 특성에 맞게 페이로드를 준비합니다.
        
        Args:
            messages: 표준 메시지 형식 리스트
            **kwargs: 추가 생성 옵션
            
        Returns:
            Dict[str, Any]: GitHub Copilot API 요청용 페이로드
        """
        # 기본 페이로드 준비
        payload = super()._prepare_payload(messages, **kwargs)
        
        # GitHub Copilot 설정 로드
        copilot_config = self.config_manager.get_github_copilot_config()
        
        # GitHub Copilot 특성에 맞는 메시지 처리
        processed_messages = []
        has_system_message = False
        
        for message in payload["messages"]:
            role = message["role"]
            content = message["content"]
            
            # GitHub Copilot은 첫 번째 시스템 메시지를 특별히 처리할 수 있음
            if role == "system":
                if not has_system_message and copilot_config.get("github_copilot_prompt_has_first_system", True):
                    has_system_message = True
                    processed_messages.append(message)
                else:
                    # 추가 시스템 메시지는 user 역할로 변환
                    logger.debug("추가 시스템 메시지를 user 역할로 변환")
                    processed_messages.append({
                        "role": "user",
                        "content": f"[System] {content}"
                    })
            else:
                processed_messages.append(message)
        
        # 교대 역할 요구사항 확인
        if copilot_config.get("github_copilot_prompt_requires_alternate_role", True):
            processed_messages = self._ensure_alternating_roles(processed_messages)
        
        payload["messages"] = processed_messages
        
        # GitHub Copilot 기본 파라미터 설정
        if "temperature" not in payload:
            payload["temperature"] = 0.7
        
        if "max_tokens" not in payload:
            payload["max_tokens"] = 4000
        
        logger.debug(f"GitHub Copilot 페이로드 준비 완료: {len(processed_messages)}개 메시지")
        return payload
    
    def _ensure_alternating_roles(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        메시지 역할이 교대로 나타나도록 보장합니다.
        
        GitHub Copilot API는 user-assistant 역할이 교대로 나타나는 것을 선호할 수 있습니다.
        
        Args:
            messages: 원본 메시지 목록
            
        Returns:
            List[Dict[str, Any]]: 역할이 교대로 조정된 메시지 목록
        """
        if not messages:
            return messages
        
        processed = []
        last_role = None
        
        for message in messages:
            current_role = message["role"]
            
            # 같은 역할이 연속으로 나타나는 경우 처리
            if current_role == last_role and current_role != "system":
                if current_role == "user":
                    # 연속된 user 메시지를 하나로 합침
                    if processed and processed[-1]["role"] == "user":
                        processed[-1]["content"] += f"\n\n{message['content']}"
                        continue
                elif current_role == "assistant":
                    # 연속된 assistant 메시지 사이에 빈 user 메시지 삽입
                    processed.append({
                        "role": "user",
                        "content": "[계속]"
                    })
            
            processed.append(message)
            last_role = current_role
        
        return processed
    
    def reload_config(self) -> bool:
        """
        설정을 다시 로드하고 클라이언트를 재구성합니다.
        
        Returns:
            bool: 재구성 성공 여부
        """
        try:
            copilot_config = self.config_manager.get_github_copilot_config()
            
            if not copilot_config.get("github_copilot_enabled", False):
                logger.warning("GitHub Copilot이 비활성화되어 있습니다.")
                return False
            
            # 새 설정으로 업데이트
            self.api_key = copilot_config.get("github_copilot_access_token", "")
            self.api_url = copilot_config.get("github_copilot_api_url", "https://api.githubcopilot.com/chat/completions")
            self.model_name = copilot_config.get("github_copilot_model_name", "gpt-4o")
            
            # 세션 헤더 업데이트
            self.session.headers.update({
                "Authorization": f"Bearer {self.api_key}"
            })
            
            logger.info("GitHub Copilot 설정 재로드 완료")
            return True
            
        except Exception as e:
            logger.error(f"설정 재로드 실패: {e}")
            return False
    
    def validate_config(self) -> Dict[str, str]:
        """
        GitHub Copilot 클라이언트 설정의 유효성을 검사합니다.
        
        Returns:
            Dict[str, str]: 설정 검증 결과
        """
        errors = super().validate_config()
        
        # ConfigManager를 통한 GitHub Copilot 설정 검증
        copilot_validation = self.config_manager.validate_github_copilot_config()
        errors.update(copilot_validation)
        
        return errors
    
    def get_client_type(self) -> str:
        """클라이언트 타입을 반환합니다."""
        return "github_copilot"
    
    def get_supported_formats(self) -> List[str]:
        """GitHub Copilot은 현재 텍스트만 지원합니다."""
        return ["text"]
    
    def get_max_context_length(self) -> Optional[int]:
        """
        GitHub Copilot의 최대 컨텍스트 길이를 반환합니다.
        
        Returns:
            Optional[int]: 최대 컨텍스트 길이 (토큰 수)
        """
        # GitHub Copilot의 일반적인 컨텍스트 길이
        # 실제 값은 사용하는 모델에 따라 다를 수 있음
        if "gpt-4o" in self.model_name.lower():
            return 128000
        elif "gpt-4" in self.model_name.lower():
            return 32768
        elif "claude" in self.model_name.lower():
            return 200000
        else:
            return 8192  # 기본값

if __name__ == "__main__":
    # 테스트 코드
    print("GitHub Copilot 클라이언트 테스트 시작...")
    
    try:
        # ConfigManager 없이 테스트 (실제 토큰 없음)
        from core.config.config_manager import ConfigManager
        
        config_manager = ConfigManager()
        copilot_config = config_manager.get_github_copilot_config()
        
        print(f"GitHub Copilot 활성화: {copilot_config['github_copilot_enabled']}")
        print(f"액세스 토큰 설정: {'예' if copilot_config['github_copilot_access_token'] else '아니오'}")
        
        if copilot_config['github_copilot_enabled'] and copilot_config['github_copilot_access_token']:
            # 실제 토큰이 있는 경우에만 클라이언트 생성
            client = GitHubCopilotClient(config_manager)
            print("✅ GitHub Copilot 클라이언트 생성 성공")
            print(f"클라이언트 타입: {client.get_client_type()}")
            print(f"모델명: {client.model_name}")
            
            # 설정 검증
            validation_errors = client.validate_config()
            if validation_errors:
                print("설정 검증 오류:", validation_errors)
            else:
                print("✅ 모든 설정이 유효합니다.")
        else:
            print("GitHub Copilot이 비활성화되어 있거나 토큰이 설정되지 않았습니다.")
            print("테스트를 위해 모의 설정을 사용합니다.")
            
            # 모의 설정으로 테스트
            config_manager.update_github_copilot_config({
                "github_copilot_enabled": True,
                "github_copilot_access_token": "test_token"
            })
            
            client = GitHubCopilotClient(config_manager)
            print("✅ 모의 GitHub Copilot 클라이언트 생성 성공")
            
            # 설정 복원
            config_manager.update_github_copilot_config({
                "github_copilot_enabled": False,
                "github_copilot_access_token": ""
            })
    
    except Exception as e:
        print(f"❌ 테스트 실패: {e}")
    
    print("GitHub Copilot 클라이언트 테스트 종료.")
