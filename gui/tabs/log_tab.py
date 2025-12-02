"""
로그 탭 컴포넌트

실행 로그를 표시하는 탭입니다.
"""

import tkinter as tk
from tkinter import scrolledtext
import ttkbootstrap as ttk
import logging
from typing import Dict, Any, Optional

from gui.tabs.base_tab import BaseTab
from gui.components.log_handlers import GuiLogHandler, TqdmToTkinter
from gui.components.tooltip import Tooltip


class LogTab(BaseTab):
    """실행 로그 탭"""
    
    def __init__(self, parent: tk.Widget, app_service, logger):
        """
        Args:
            parent: 부모 위젯 (Notebook)
            app_service: AppService 인스턴스
            logger: 로거 인스턴스
        """
        super().__init__(parent, app_service, logger)
        self.log_text: Optional[scrolledtext.ScrolledText] = None
        self.gui_log_handler: Optional[GuiLogHandler] = None
        self.tqdm_stream: Optional[TqdmToTkinter] = None
    
    def create_widgets(self) -> ttk.Frame:
        """로그 탭 위젯 생성"""
        self.frame = ttk.Frame(self.parent, padding="10")
        
        # 로그 텍스트 영역
        self.log_text = scrolledtext.ScrolledText(
            self.frame, 
            wrap=tk.WORD, 
            state=tk.DISABLED, 
            height=20
        )
        self.log_text.pack(fill="both", expand=True, padx=5, pady=5)
        Tooltip(self.log_text, "애플리케이션의 주요 동작 및 오류 로그가 표시됩니다.")
        
        # 커스텀 핸들러 생성 및 등록
        self.gui_log_handler = GuiLogHandler(self.log_text)
        
        # GUI 핸들러를 위한 별도의 포맷터 생성 및 설정
        gui_formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s', 
            '%H:%M:%S'
        )
        self.gui_log_handler.setFormatter(gui_formatter)
        
        # 루트 로거에 핸들러 추가 (모든 모듈의 로그 캡처)
        root_logger = logging.getLogger()
        root_logger.addHandler(self.gui_log_handler)
        
        # 기존 로거 설정 유지
        if self.logger:
            self.logger.setLevel(logging.INFO)
        
        # TQDM 스트림 생성
        self.tqdm_stream = TqdmToTkinter(self.log_text)
        
        return self.frame
    
    def get_config(self) -> Dict[str, Any]:
        """로그 탭은 설정이 없음"""
        return {}
    
    def load_config(self, config: Dict[str, Any]) -> None:
        """로그 탭은 설정 로드가 필요 없음"""
        pass
    
    def get_tqdm_stream(self) -> Optional[TqdmToTkinter]:
        """TQDM 스트림 반환"""
        return self.tqdm_stream
    
    def get_log_handler(self) -> Optional[GuiLogHandler]:
        """로그 핸들러 반환"""
        return self.gui_log_handler
    
    def log_to_widget(self, message: str, level: str = "INFO", exc_info: bool = False):
        """
        위젯에 직접 로그 메시지 출력
        
        Args:
            message: 로그 메시지
            level: 로그 레벨
            exc_info: 예외 정보 포함 여부
        """
        gui_specific_logger = logging.getLogger(__name__ + "_gui")
        log_func = getattr(gui_specific_logger, level.lower(), gui_specific_logger.info)
        log_func(message, exc_info=exc_info)
