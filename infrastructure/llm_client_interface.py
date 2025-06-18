# llm_client_interface.py
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Union

class LLMClientInterface(ABC):
    """
    범용 LLM 클라이언트 인터페이스.
    
    다양한 LLM 서비스 (Gemini, OpenAI, GitHub Copilot 등)에 대한 
    공통 인터페이스를 제공합니다.
    """
    
    @abstractmethod
    def generate_completion(
        self, 
        messages: List[Dict[str, Any]], 
        **kwargs
    ) -> str:
        """
        주어진 메시지 목록을 기반으로 텍스트 완성을 생성합니다.
        
        Args:
            messages (List[Dict[str, Any]]): 대화 메시지 목록
                예: [{"role": "user", "content": "안녕하세요"}]
            **kwargs: 추가 생성 옵션
                - model_name (str): 사용할 모델명
                - temperature (float): 생성 온도
                - max_tokens (int): 최대 토큰 수
                - system_instruction (str): 시스템 지시사항
                - stream (bool): 스트리밍 여부
                
        Returns:
            str: 생성된 텍스트 응답
            
        Raises:
            Exception: 생성 중 오류 발생 시
        """
        pass
    
    @abstractmethod
    def get_available_models(self) -> List[str]:
        """
        사용 가능한 모델 목록을 반환합니다.
        
        Returns:
            List[str]: 사용 가능한 모델명 목록
            
        Raises:
            Exception: 모델 목록 조회 중 오류 발생 시
        """
        pass
    
    @abstractmethod
    def is_available(self) -> bool:
        """
        클라이언트가 현재 사용 가능한지 확인합니다.
        
        Returns:
            bool: 사용 가능하면 True, 그렇지 않으면 False
        """
        pass
    
    @abstractmethod
    def validate_config(self) -> Dict[str, str]:
        """
        클라이언트 설정의 유효성을 검사합니다.
        
        Returns:
            Dict[str, str]: 설정 검증 결과. 키는 설정명, 값은 오류 메시지
                           오류가 없으면 빈 딕셔너리 반환
        """
        pass
    
    def get_client_type(self) -> str:
        """
        클라이언트 타입을 반환합니다.
        
        Returns:
            str: 클라이언트 타입 (예: "gemini", "openai", "github_copilot")
        """
        return self.__class__.__name__.lower().replace("client", "")
    
    def supports_streaming(self) -> bool:
        """
        스트리밍을 지원하는지 확인합니다.
        
        기본적으로 False를 반환하며, 필요시 하위 클래스에서 오버라이드합니다.
        
        Returns:
            bool: 스트리밍 지원 여부
        """
        return False
    
    def supports_system_instruction(self) -> bool:
        """
        시스템 지시사항을 지원하는지 확인합니다.
        
        기본적으로 True를 반환하며, 필요시 하위 클래스에서 오버라이드합니다.
        
        Returns:
            bool: 시스템 지시사항 지원 여부
        """
        return True
    
    def get_max_context_length(self) -> Optional[int]:
        """
        최대 컨텍스트 길이를 반환합니다.
        
        기본적으로 None을 반환하며, 필요시 하위 클래스에서 오버라이드합니다.
        
        Returns:
            Optional[int]: 최대 컨텍스트 길이 (토큰 수), 알 수 없으면 None
        """
        return None
    
    def get_supported_formats(self) -> List[str]:
        """
        지원하는 입력/출력 형식을 반환합니다.
        
        기본적으로 텍스트만 지원한다고 가정합니다.
        
        Returns:
            List[str]: 지원하는 형식 목록 (예: ["text", "image", "audio"])
        """
        return ["text"]
