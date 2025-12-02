"""
메인 GUI 윈도우 클래스

배치 번역기의 메인 윈도우를 관리합니다.
탭 컴포넌트들을 조합하고 탭 간 통신을 조율합니다.
"""

import tkinter as tk
from tkinter import messagebox
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
import logging
from pathlib import Path
from typing import Optional, Dict, Any

# 4계층 아키텍처의 AppService 및 예외 임포트
from app.app_service import AppService
from core.exceptions import BtgConfigException
from infrastructure.logger_config import setup_logger

# 탭 컴포넌트 임포트
from gui.tabs.settings_tab import SettingsTab
from gui.tabs.glossary_tab import GlossaryTab
from gui.tabs.log_tab import LogTab

# 로거 설정
GUI_LOGGER_NAME = __name__
logger = setup_logger(GUI_LOGGER_NAME)


class BatchTranslatorGUI:
    """
    배치 번역기 메인 GUI 클래스
    
    탭 컴포넌트들을 생성하고 조합하여 전체 GUI를 구성합니다.
    탭 간 통신을 위한 콜백 함수들을 제공합니다.
    """
    
    def __init__(self, master: tk.Tk):
        """
        메인 윈도우 초기화
        
        Args:
            master: Tk 루트 윈도우
        """
        self.master = master
        master.title("BTG - 배치 번역기 (4-Tier Refactored)")
        master.geometry("950x800")
        
        # AppService 초기화
        self.app_service: Optional[AppService] = None
        self._init_app_service()
        
        if not self.app_service:
            logger.critical("AppService 초기화 실패로 GUI를 시작할 수 없습니다.")
            return
        
        # 종료 핸들러 등록
        self.master.protocol("WM_DELETE_WINDOW", self._on_closing)
        
        # 상태 변수
        self.stop_requested = False
        
        # 노트북 (탭 컨테이너) 생성
        self.notebook = ttk.Notebook(master, bootstyle="primary")
        
        # 탭 인스턴스 생성
        self._create_tabs()
        
        # 탭을 노트북에 추가
        self._add_tabs_to_notebook()
        
        # 노트북 배치
        self.notebook.pack(expand=True, fill='both')
        
        # 초기 설정 로드
        self._load_initial_config_to_ui()
        
        logger.info("BatchTranslatorGUI 초기화 완료")
    
    def _init_app_service(self) -> None:
        """AppService 초기화"""
        try:
            config_file = Path("config.json")
            self.app_service = AppService(config_file_path=config_file)
            logger.info(f"AppService 인스턴스가 '{config_file}' 설정으로 생성되었습니다.")
        except BtgConfigException as e:
            logger.error(f"설정 파일 오류로 AppService 초기화 실패: {e}")
            messagebox.showerror(
                "설정 오류", 
                f"설정 파일 처리 중 오류가 발생했습니다: {e}\n기본 설정으로 시도합니다."
            )
            try:
                self.app_service = AppService()
                logger.info("AppService가 기본 설정으로 초기화되었습니다.")
            except Exception as e_fallback:
                logger.critical(f"AppService 기본 설정 초기화마저 실패: {e_fallback}")
                messagebox.showerror(
                    "치명적 오류", 
                    f"애플리케이션 서비스 초기화에 실패했습니다: {e_fallback}"
                )
                self.app_service = None
        except Exception as e:
            logger.critical(f"AppService 초기화 중 예상치 못한 오류: {e}", exc_info=True)
            messagebox.showerror(
                "초기화 오류", 
                f"애플리케이션 서비스 초기화 중 심각한 오류 발생: {e}"
            )
            self.app_service = None
    
    def _create_tabs(self) -> None:
        """탭 인스턴스들을 생성"""
        # 로그 탭 먼저 생성 (다른 탭에서 로그 핸들러 필요)
        self.log_tab = LogTab(
            parent=self.notebook,
            app_service=self.app_service,
            logger=logger
        )
        
        # 설정 및 번역 탭
        self.settings_tab = SettingsTab(
            parent=self.notebook,
            app_service=self.app_service,
            logger=logger,
            log_callback=self._log_message,
            on_translation_complete=self._on_translation_complete,
            get_tqdm_stream=self._get_tqdm_stream
        )
        
        # 용어집 관리 탭
        self.glossary_tab = GlossaryTab(
            parent=self.notebook,
            app_service=self.app_service,
            logger=logger,
            get_input_files=self._get_input_files,
            get_chunk_size=self._get_chunk_size,
            on_glossary_path_changed=self._on_glossary_path_changed
        )
    
    def _add_tabs_to_notebook(self) -> None:
        """탭을 노트북에 추가"""
        # 탭 위젯 생성 및 추가
        settings_frame = self.settings_tab.create_widgets()
        glossary_frame = self.glossary_tab.create_widgets()
        log_frame = self.log_tab.create_widgets()
        
        self.notebook.add(settings_frame, text='설정 및 번역')
        self.notebook.add(glossary_frame, text='용어집 관리')
        self.notebook.add(log_frame, text='실행 로그')
    
    def _load_initial_config_to_ui(self) -> None:
        """UI에 초기 설정 로드"""
        if not self.app_service:
            logger.warning("AppService가 초기화되지 않아 UI에 설정을 로드할 수 없습니다.")
            return
        
        try:
            config = self.app_service.config
            logger.info("UI 설정 로드 시작")
            
            # 각 탭에 설정 로드
            self.settings_tab.load_config(config)
            self.glossary_tab.load_config(config)
            
            logger.info("UI에 설정 로드 완료")
        except Exception as e:
            logger.error(f"설정 UI 반영 중 오류: {e}", exc_info=True)
            messagebox.showerror("오류", f"설정 UI 반영 중 예상치 못한 오류: {e}")
    
    def _get_config_from_ui(self) -> Dict[str, Any]:
        """
        모든 탭에서 설정값을 수집합니다.
        
        Returns:
            전체 설정 딕셔너리
        """
        config = {}
        
        # 각 탭에서 설정 수집
        config.update(self.settings_tab.get_config())
        config.update(self.glossary_tab.get_config())
        
        return config
    
    def _save_settings(self) -> bool:
        """
        전체 설정 저장
        
        Returns:
            저장 성공 여부
        """
        if not self.app_service:
            messagebox.showerror("오류", "AppService가 초기화되지 않았습니다.")
            return False
        
        try:
            config = self._get_config_from_ui()
            if self.app_service.save_app_config(config):
                messagebox.showinfo("성공", "설정이 저장되었습니다.")
                logger.info("전체 설정 저장 완료")
                return True
            else:
                messagebox.showerror("오류", "설정 저장에 실패했습니다.")
                return False
        except Exception as e:
            logger.error(f"설정 저장 중 오류: {e}", exc_info=True)
            messagebox.showerror("오류", f"설정 저장 중 오류: {e}")
            return False
    
    # ========== 탭 간 통신 콜백 ==========
    
    def _get_input_files(self) -> list:
        """
        설정 탭에서 입력 파일 목록 가져오기
        
        Returns:
            입력 파일 경로 목록
        """
        return self.settings_tab.get_input_files()
    
    def _get_chunk_size(self) -> int:
        """
        설정 탭에서 청크 크기 가져오기
        
        Returns:
            청크 크기
        """
        return self.settings_tab.get_chunk_size()
    
    def _on_glossary_path_changed(self, path: str) -> None:
        """
        용어집 경로 변경 시 호출
        
        Args:
            path: 새로운 용어집 파일 경로
        """
        logger.debug(f"용어집 경로 변경됨: {path}")
        # 필요시 settings_tab에 경로 전달
        # self.settings_tab.set_glossary_path(path)
    
    def _on_translation_complete(self, success: bool, message: str = "") -> None:
        """
        번역 완료 시 호출
        
        Args:
            success: 성공 여부
            message: 완료 메시지
        """
        if success:
            logger.info(f"번역 완료: {message}")
        else:
            logger.warning(f"번역 실패 또는 중단: {message}")
    
    def _get_tqdm_stream(self):
        """
        TQDM 스트림 반환
        
        Returns:
            TqdmToTkinter 인스턴스
        """
        return self.log_tab.get_tqdm_stream()
    
    def _log_message(self, message: str, level: str = "INFO", exc_info: bool = False) -> None:
        """
        로그 메시지 출력
        
        Args:
            message: 로그 메시지
            level: 로그 레벨 (INFO, WARNING, ERROR, DEBUG)
            exc_info: 예외 정보 포함 여부
        """
        level_upper = level.upper()
        if level_upper == "INFO":
            logger.info(message, exc_info=exc_info)
        elif level_upper == "WARNING":
            logger.warning(message, exc_info=exc_info)
        elif level_upper == "ERROR":
            logger.error(message, exc_info=exc_info)
        elif level_upper == "DEBUG":
            logger.debug(message, exc_info=exc_info)
        else:
            logger.info(message, exc_info=exc_info)
    
    # ========== 종료 처리 ==========
    
    def _on_closing(self) -> None:
        """종료 처리"""
        app_service = self.app_service
        if app_service and app_service.is_translation_running:
            if messagebox.askokcancel(
                "종료 확인", 
                "번역 작업이 진행 중입니다. 정말로 종료하시겠습니까?"
            ):
                self.stop_requested = True
                app_service.request_stop_translation()
                logger.info("사용자 종료 요청으로 번역 중단 시도.")
                self._check_if_stopped_and_destroy()
        else:
            self.master.destroy()
    
    def _check_if_stopped_and_destroy(self) -> None:
        """번역이 멈출 때까지 확인 후 창 닫기"""
        if not self.app_service or not self.app_service.is_translation_running:
            self.master.destroy()
        else:
            # 아직 실행 중이면 100ms 후에 다시 확인
            self.master.after(100, self._check_if_stopped_and_destroy)


def run_gui() -> None:
    """GUI 실행 함수"""
    # ttkbootstrap 테마 적용된 윈도우 생성
    root = ttk.Window(themename="cosmo")
    
    app = BatchTranslatorGUI(root)
    
    # AppService가 초기화되지 않았으면 종료
    if not app.app_service:
        root.destroy()
        return
    
    root.mainloop()


if __name__ == "__main__":
    run_gui()
