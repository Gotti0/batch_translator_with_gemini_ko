"""
탭 기본 인터페이스

모든 탭 컴포넌트의 기본 클래스를 정의합니다.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
import tkinter as tk
import ttkbootstrap as ttk


class BaseTab(ABC):
    """모든 탭의 기본 클래스"""
    
    def __init__(self, parent: tk.Widget, app_service, logger):
        """
        Args:
            parent: 부모 위젯 (보통 Notebook)
            app_service: AppService 인스턴스
            logger: 로거 인스턴스
        """
        self.parent = parent
        self.app_service = app_service
        self.logger = logger
        self.frame: Optional[ttk.Frame] = None
    
    @abstractmethod
    def create_widgets(self) -> ttk.Frame:
        """
        탭 위젯들을 생성하고 프레임을 반환합니다.
        
        Returns:
            생성된 탭의 메인 프레임
        """
        pass
    
    @abstractmethod
    def get_config(self) -> Dict[str, Any]:
        """
        현재 UI 상태에서 설정값을 추출합니다.
        
        Returns:
            설정값 딕셔너리
        """
        pass
    
    @abstractmethod
    def load_config(self, config: Dict[str, Any]) -> None:
        """
        설정값을 UI에 반영합니다.
        
        Args:
            config: 적용할 설정값 딕셔너리
        """
        pass
    
    def log_message(self, message: str, level: str = "INFO", exc_info: bool = False):
        """
        로그 메시지를 출력합니다.
        
        Args:
            message: 로그 메시지
            level: 로그 레벨 (INFO, WARNING, ERROR, DEBUG)
            exc_info: 예외 정보 포함 여부
        """
        if self.logger:
            log_func = getattr(self.logger, level.lower(), self.logger.info)
            log_func(message, exc_info=exc_info)
