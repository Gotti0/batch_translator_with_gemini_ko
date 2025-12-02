"""
용어집 관리 탭

용어집 추출, 편집, 관리를 담당하는 탭입니다.
"""

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
import threading
import json
import logging
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable

from gui.tabs.base_tab import BaseTab
from gui.components.tooltip import Tooltip
from gui.components.scrollable_frame import ScrollableFrame
from gui.dialogs.glossary_editor import GlossaryEditorWindow

# 예외 클래스 임포트
from core.exceptions import (
    BtgFileHandlerException,
    BtgApiClientException,
    BtgServiceException,
    BtgBusinessLogicException,
)
from core.dtos import GlossaryExtractionProgressDTO


class GlossaryTab(BaseTab):
    """용어집 관리 탭 클래스"""
    
    def __init__(
        self, 
        parent: tk.Widget, 
        app_service, 
        logger,
        # 콜백 함수들
        get_input_files: Optional[Callable[[], List[str]]] = None,
        get_chunk_size: Optional[Callable[[], int]] = None,
        on_glossary_path_changed: Optional[Callable[[str], None]] = None,
    ):
        """
        Args:
            parent: 부모 위젯 (Notebook)
            app_service: AppService 인스턴스
            logger: 로거 인스턴스
            get_input_files: 입력 파일 목록을 가져오는 콜백 (SettingsTab에서)
            get_chunk_size: 청크 크기를 가져오는 콜백 (SettingsTab에서)
            on_glossary_path_changed: 용어집 경로 변경 시 호출되는 콜백
        """
        super().__init__(parent, app_service, logger)
        
        # 콜백 함수 저장
        self._get_input_files = get_input_files
        self._get_chunk_size = get_chunk_size
        self._on_glossary_path_changed = on_glossary_path_changed
        
        # 스크롤 프레임
        self.scroll_frame: Optional[ScrollableFrame] = None
        
        # 상태 변수
        self.glossary_stop_requested = False
        
        # === JSON 경로 섹션 위젯 ===
        self.glossary_json_path_entry: Optional[ttk.Entry] = None
        self.browse_glossary_json_button = None
        
        # === 추출 버튼 및 진행률 위젯 ===
        self.extract_glossary_button = None
        self.stop_glossary_button = None
        self.glossary_progress_label: Optional[ttk.Label] = None
        
        # === 추출 설정 위젯 ===
        self.sample_ratio_scale: Optional[ttk.Scale] = None
        self.sample_ratio_label: Optional[ttk.Label] = None
        self.advanced_var: Optional[tk.BooleanVar] = None
        self.advanced_frame: Optional[ttk.Frame] = None
        self.extraction_temp_scale: Optional[ttk.Scale] = None
        self.extraction_temp_label: Optional[ttk.Label] = None
        self.user_override_glossary_prompt_text: Optional[scrolledtext.ScrolledText] = None
        
        # === 액션 버튼 위젯 ===
        self.save_glossary_settings_button = None
        self.reset_glossary_settings_button = None
        self.preview_glossary_settings_button = None
        self.glossary_status_label: Optional[ttk.Label] = None
        
        # === 용어집 표시 영역 위젯 ===
        self.glossary_display_text: Optional[scrolledtext.ScrolledText] = None
        self.load_glossary_button = None
        self.copy_glossary_button = None
        self.save_displayed_glossary_button = None
        self.edit_glossary_button = None
        
        # === 동적 용어집 주입 설정 위젯 ===
        self.enable_dynamic_glossary_injection_var: Optional[tk.BooleanVar] = None
        self.max_glossary_entries_injection_entry: Optional[ttk.Entry] = None
        self.max_glossary_chars_injection_entry: Optional[ttk.Entry] = None

    def create_widgets(self) -> ttk.Frame:
        """
        용어집 탭 위젯들을 생성합니다.
        
        Returns:
            생성된 탭의 메인 프레임
        """
        # 스크롤 가능한 프레임 생성
        self.scroll_frame = ScrollableFrame(self.parent)
        self.frame = self.scroll_frame.main_frame
        
        glossary_frame = self.scroll_frame.scrollable_frame
        
        # 각 섹션 위젯 생성
        self._create_path_section(glossary_frame)
        self._create_extraction_settings_section(glossary_frame)
        self._create_action_section(glossary_frame)
        self._create_display_section(glossary_frame)
        self._create_dynamic_injection_section(glossary_frame)
        
        # 이벤트 바인딩
        self._bind_events()
        
        # 고급 설정 초기 숨김
        if self.advanced_frame:
            self.advanced_frame.grid_remove()
        
        return self.frame

    # ========== 섹션 생성 메서드 ==========
    
    def _create_path_section(self, parent: ttk.Frame) -> None:
        """용어집 JSON 파일 경로 섹션 생성"""
        path_frame = ttk.Labelframe(parent, text="용어집 JSON 파일", padding="10")
        path_frame.pack(fill="x", padx=5, pady=5)
        
        # JSON 파일 경로 입력
        glossary_json_path_label = ttk.Label(path_frame, text="JSON 파일 경로:")
        glossary_json_path_label.grid(row=0, column=0, padx=5, pady=5, sticky="w")
        Tooltip(glossary_json_path_label, "사용할 용어집 JSON 파일의 경로입니다.\n추출 기능을 사용하면 자동으로 채워지거나, 직접 입력/선택할 수 있습니다.")
        
        self.glossary_json_path_entry = ttk.Entry(path_frame, width=50)
        self.glossary_json_path_entry.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        
        self.browse_glossary_json_button = ttk.Button(
            path_frame, 
            text="찾아보기", 
            command=self._browse_glossary_json
        )
        self.browse_glossary_json_button.grid(row=0, column=2, padx=5, pady=5)
        
        # 추출/중지 버튼 프레임
        glossary_action_button_frame = ttk.Frame(path_frame)
        glossary_action_button_frame.grid(row=2, column=0, columnspan=3, pady=10)
        
        self.extract_glossary_button = ttk.Button(
            glossary_action_button_frame, 
            text="선택한 입력 파일에서 용어집 추출", 
            command=self._extract_glossary_thread
        )
        self.extract_glossary_button.pack(side="left", padx=5)
        Tooltip(self.extract_glossary_button, "'설정 및 번역' 탭에서 선택된 입력 파일을 분석하여 용어집을 추출하고, 그 결과를 아래 텍스트 영역에 표시합니다.")
        
        self.stop_glossary_button = ttk.Button(
            glossary_action_button_frame, 
            text="추출 중지", 
            command=self._request_stop_glossary_extraction, 
            state=tk.DISABLED
        )
        self.stop_glossary_button.pack(side="left", padx=5)
        Tooltip(self.stop_glossary_button, "진행 중인 용어집 추출 작업을 중지하고 현재까지의 결과로 저장합니다.")
        
        # 진행률 레이블
        self.glossary_progress_label = ttk.Label(path_frame, text="용어집 추출 대기 중...")
        self.glossary_progress_label.grid(row=3, column=0, columnspan=3, padx=5, pady=2)
        Tooltip(self.glossary_progress_label, "용어집 추출 작업의 진행 상태를 표시합니다.")
    
    def _create_extraction_settings_section(self, parent: ttk.Frame) -> None:
        """용어집 추출 설정 섹션 생성"""
        extraction_settings_frame = ttk.Labelframe(parent, text="용어집 추출 설정", padding="10")
        extraction_settings_frame.pack(fill="x", padx=5, pady=5)
        
        # 샘플링 비율 설정
        sample_ratio_label_widget = ttk.Label(extraction_settings_frame, text="샘플링 비율 (%):")
        sample_ratio_label_widget.grid(row=0, column=0, padx=5, pady=5, sticky="w")
        Tooltip(sample_ratio_label_widget, "용어집 추출 시 전체 텍스트 중 분석할 비율입니다.\n100%로 설정하면 전체 텍스트를 분석합니다.")
        
        sample_ratio_frame = ttk.Frame(extraction_settings_frame)
        sample_ratio_frame.grid(row=0, column=1, columnspan=2, padx=5, pady=5, sticky="ew")
        
        self.sample_ratio_scale = ttk.Scale(
            sample_ratio_frame, 
            from_=5.0, 
            to=100.0, 
            orient="horizontal", 
            length=200,
            command=self._update_sample_ratio_label
        )
        self.sample_ratio_scale.pack(side="left", padx=(0, 10))
        Tooltip(self.sample_ratio_scale, "용어집 추출 샘플링 비율을 조절합니다 (5.0% ~ 100.0%).")
        
        self.sample_ratio_label = ttk.Label(sample_ratio_frame, text="25.0%", width=8)
        self.sample_ratio_label.pack(side="left")
        Tooltip(self.sample_ratio_label, "현재 설정된 샘플링 비율입니다.")
        
        # 고급 설정 체크박스
        self.advanced_var = tk.BooleanVar()
        advanced_check = ttk.Checkbutton(
            extraction_settings_frame, 
            text="고급 설정 표시", 
            variable=self.advanced_var,
            command=self._toggle_advanced_settings
        )
        advanced_check.grid(row=4, column=0, columnspan=3, padx=5, pady=(15, 5), sticky="w")
        Tooltip(advanced_check, "용어집 추출에 사용될 추출 온도 설정을 표시하거나 숨깁니다.")
        
        # 고급 설정 프레임 (초기에는 숨김)
        self.advanced_frame = ttk.Frame(extraction_settings_frame)
        self.advanced_frame.grid(row=5, column=0, columnspan=3, padx=5, pady=5, sticky="ew")
        
        # 추출 온도 설정
        extraction_temp_label_widget = ttk.Label(self.advanced_frame, text="추출 온도:")
        extraction_temp_label_widget.grid(row=0, column=0, padx=5, pady=5, sticky="w")
        Tooltip(extraction_temp_label_widget, "용어집 추출 시 모델 응답의 무작위성입니다.\n낮을수록 일관적, 높을수록 다양하지만 덜 정확할 수 있습니다.")
        
        self.extraction_temp_scale = ttk.Scale(
            self.advanced_frame,
            from_=0.0,
            to=1.0,
            orient="horizontal",
            length=150,
            command=self._update_extraction_temp_label
        )
        self.extraction_temp_scale.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        self.extraction_temp_scale.set(0.3)  # 기본값
        Tooltip(self.extraction_temp_scale, "용어집 추출 온도를 조절합니다 (0.0 ~ 1.0).")
        
        self.extraction_temp_label = ttk.Label(self.advanced_frame, text="0.30", width=6)
        self.extraction_temp_label.grid(row=0, column=2, padx=5, pady=5)
        Tooltip(self.extraction_temp_label, "현재 설정된 용어집 추출 온도입니다.")
        
        # 사용자 재정의 추출 프롬프트
        user_override_glossary_prompt_label = ttk.Label(
            self.advanced_frame, 
            text="사용자 재정의 추출 프롬프트:"
        )
        user_override_glossary_prompt_label.grid(row=1, column=0, padx=5, pady=5, sticky="nw")
        Tooltip(user_override_glossary_prompt_label, 
                "용어집 추출 시 사용할 사용자 정의 프롬프트입니다.\n"
                "비워두면 기본 프롬프트를 사용합니다.\n"
                "플레이스홀더: {target_lang_name}, {target_lang_code}, {novelText}")
        
        self.user_override_glossary_prompt_text = scrolledtext.ScrolledText(
            self.advanced_frame, 
            wrap=tk.WORD, 
            height=8, 
            width=60
        )
        self.user_override_glossary_prompt_text.grid(row=1, column=1, columnspan=2, padx=5, pady=5, sticky="ew")
        Tooltip(self.user_override_glossary_prompt_text, "사용자 정의 프롬프트를 입력하세요. JSON 응답 형식을 유지해야 합니다.")
    
    def _create_action_section(self, parent: ttk.Frame) -> None:
        """액션 버튼 섹션 생성"""
        glossary_action_frame = ttk.Frame(parent, padding="10")
        glossary_action_frame.pack(fill="x", padx=5, pady=5)
        
        # 설정 저장 버튼
        self.save_glossary_settings_button = ttk.Button(
            glossary_action_frame,
            text="용어집 설정 저장",
            command=self._save_glossary_settings
        )
        self.save_glossary_settings_button.pack(side="left", padx=5)
        Tooltip(self.save_glossary_settings_button, "현재 용어집 탭의 설정을 config.json 파일에 저장합니다.")
        
        # 설정 초기화 버튼
        self.reset_glossary_settings_button = ttk.Button(
            glossary_action_frame, 
            text="기본값으로 초기화", 
            command=self._reset_glossary_settings
        )
        self.reset_glossary_settings_button.pack(side="left", padx=5)
        Tooltip(self.reset_glossary_settings_button, "용어집 탭의 모든 설정을 프로그램 기본값으로 되돌립니다.")
        
        # 설정 미리보기 버튼
        self.preview_glossary_settings_button = ttk.Button(
            glossary_action_frame,
            text="설정 미리보기", 
            command=self._preview_glossary_settings
        )
        self.preview_glossary_settings_button.pack(side="right", padx=5)
        Tooltip(self.preview_glossary_settings_button, "현재 용어집 설정이 실제 추출에 미칠 영향을 간략하게 미리봅니다.")
        
        # 상태 표시 레이블
        self.glossary_status_label = ttk.Label(
            glossary_action_frame,
            text="",
            font=("Arial", 9),
            foreground="gray"
        )
        self.glossary_status_label.pack(side="bottom", pady=5)
        Tooltip(self.glossary_status_label, "용어집 설정 변경 및 저장 상태를 표시합니다.")
    
    def _create_display_section(self, parent: ttk.Frame) -> None:
        """추출된 용어집 표시 섹션 생성"""
        glossary_display_frame = ttk.Labelframe(parent, text="추출된 용어집 (JSON)", padding="10")
        glossary_display_frame.pack(fill="both", expand=True, padx=5, pady=5)
        Tooltip(glossary_display_frame, "추출되거나 불러온 용어집의 내용이 JSON 형식으로 표시됩니다.")
        
        # 용어집 표시 텍스트 영역
        self.glossary_display_text = scrolledtext.ScrolledText(
            glossary_display_frame, 
            wrap=tk.WORD, 
            height=10, 
            width=70
        )
        self.glossary_display_text.pack(fill="both", expand=True, padx=5, pady=5)
        Tooltip(self.glossary_display_text, "용어집 내용입니다. 직접 편집은 불가능하며, 'JSON 저장'으로 파일 저장 후 수정할 수 있습니다.")
        
        # 버튼 프레임
        glossary_display_buttons_frame = ttk.Frame(glossary_display_frame)
        glossary_display_buttons_frame.pack(fill="x", pady=5)
        
        self.load_glossary_button = ttk.Button(
            glossary_display_buttons_frame, 
            text="용어집 불러오기", 
            command=self._load_glossary_to_display
        )
        self.load_glossary_button.pack(side="left", padx=5)
        Tooltip(self.load_glossary_button, "기존 용어집 JSON 파일을 불러와 아래 텍스트 영역에 표시합니다.")
        
        self.copy_glossary_button = ttk.Button(
            glossary_display_buttons_frame, 
            text="JSON 복사", 
            command=self._copy_glossary_json
        )
        self.copy_glossary_button.pack(side="left", padx=5)
        Tooltip(self.copy_glossary_button, "아래 텍스트 영역에 표시된 용어집 JSON 내용을 클립보드에 복사합니다.")
        
        self.save_displayed_glossary_button = ttk.Button(
            glossary_display_buttons_frame, 
            text="JSON 저장", 
            command=self._save_displayed_glossary_json
        )
        self.save_displayed_glossary_button.pack(side="left", padx=5)
        Tooltip(self.save_displayed_glossary_button, "아래 텍스트 영역에 표시된 용어집 JSON 내용을 새 파일로 저장합니다.")
        
        self.edit_glossary_button = ttk.Button(
            glossary_display_buttons_frame, 
            text="용어집 편집", 
            command=self._open_glossary_editor
        )
        self.edit_glossary_button.pack(side="left", padx=5)
        Tooltip(self.edit_glossary_button, "표시된 용어집 내용을 별도의 편집기 창에서 수정합니다.")
    
    def _create_dynamic_injection_section(self, parent: ttk.Frame) -> None:
        """동적 용어집 주입 설정 섹션 생성"""
        dynamic_glossary_frame = ttk.Labelframe(parent, text="동적 용어집 주입 설정", padding="10")
        dynamic_glossary_frame.pack(fill="x", padx=5, pady=5)
        
        # 동적 주입 활성화 체크박스
        self.enable_dynamic_glossary_injection_var = tk.BooleanVar(value=False)
        enable_dynamic_glossary_injection_check = ttk.Checkbutton(
            dynamic_glossary_frame,
            text="동적 용어집 주입 활성화",
            variable=self.enable_dynamic_glossary_injection_var,
            command=self._on_glossary_setting_changed
        )
        enable_dynamic_glossary_injection_check.grid(row=0, column=0, columnspan=2, padx=5, pady=5, sticky="w")
        Tooltip(enable_dynamic_glossary_injection_check, "번역 시 현재 청크와 관련된 용어집 항목을 자동으로 프롬프트에 주입합니다.")
        
        # 청크당 최대 주입 항목 수
        max_entries_injection_label = ttk.Label(dynamic_glossary_frame, text="청크당 최대 주입 항목 수:")
        max_entries_injection_label.grid(row=1, column=0, padx=5, pady=5, sticky="w")
        Tooltip(max_entries_injection_label, "하나의 번역 청크에 주입될 용어집 항목의 최대 개수입니다.")
        
        self.max_glossary_entries_injection_entry = ttk.Entry(dynamic_glossary_frame, width=5)
        self.max_glossary_entries_injection_entry.grid(row=1, column=1, padx=5, pady=5, sticky="w")
        Tooltip(self.max_glossary_entries_injection_entry, "최대 주입 항목 수를 입력하세요.")
        
        # 청크당 최대 주입 문자 수
        max_chars_injection_label = ttk.Label(dynamic_glossary_frame, text="청크당 최대 주입 문자 수:")
        max_chars_injection_label.grid(row=2, column=0, padx=5, pady=5, sticky="w")
        Tooltip(max_chars_injection_label, "하나의 번역 청크에 주입될 용어집 내용의 최대 총 문자 수입니다.")
        
        self.max_glossary_chars_injection_entry = ttk.Entry(dynamic_glossary_frame, width=10)
        self.max_glossary_chars_injection_entry.grid(row=2, column=1, padx=5, pady=5, sticky="w")
        Tooltip(self.max_glossary_chars_injection_entry, "최대 주입 문자 수를 입력하세요.")
    
    def _bind_events(self) -> None:
        """이벤트 바인딩"""
        # 설정 변경 감지 이벤트 바인딩
        if self.sample_ratio_scale:
            self.sample_ratio_scale.bind("<ButtonRelease-1>", self._on_glossary_setting_changed)
        
        if self.extraction_temp_scale:
            self.extraction_temp_scale.bind("<ButtonRelease-1>", self._on_glossary_setting_changed)
        
        if self.user_override_glossary_prompt_text:
            self.user_override_glossary_prompt_text.bind("<KeyRelease>", self._on_glossary_setting_changed)

    # ========== 파일/UI 업데이트 메서드 ==========
    
    def _browse_glossary_json(self) -> None:
        """용어집 JSON 파일 선택"""
        initial_dir = ""
        input_file_path = ""
        
        # 콜백을 통해 입력 파일 목록 가져오기
        if self._get_input_files:
            input_files = self._get_input_files()
            if input_files:
                input_file_path = input_files[0]
        
        if input_file_path and Path(input_file_path).exists():
            initial_dir = str(Path(input_file_path).parent)
        
        filepath = filedialog.askopenfilename(
            title="용어집 JSON 파일 선택",
            filetypes=(("JSON 파일", "*.json"), ("모든 파일", "*.*")),
            initialdir=initial_dir
        )
        
        if filepath:
            self._update_glossary_json_path_entry(filepath)
            # 경로 변경 콜백 호출
            if self._on_glossary_path_changed:
                self._on_glossary_path_changed(filepath)
    
    def _update_glossary_json_path_entry(self, path_str: str) -> None:
        """용어집 경로 엔트리 업데이트"""
        if self.glossary_json_path_entry:
            self.glossary_json_path_entry.delete(0, tk.END)
            self.glossary_json_path_entry.insert(0, path_str)
        
        # AppService config에도 업데이트
        if self.app_service:
            self.app_service.config["glossary_json_path"] = path_str
    
    def _update_sample_ratio_label(self, value) -> None:
        """샘플링 비율 레이블 업데이트"""
        try:
            ratio = float(value)
            if self.sample_ratio_label:
                self.sample_ratio_label.config(text=f"{ratio:.1f}%")
        except (ValueError, TypeError):
            pass
    
    def _update_extraction_temp_label(self, value) -> None:
        """추출 온도 레이블 업데이트"""
        try:
            temp = float(value)
            if self.extraction_temp_label:
                self.extraction_temp_label.config(text=f"{temp:.2f}")
        except (ValueError, TypeError):
            pass
    
    def _toggle_advanced_settings(self) -> None:
        """고급 설정 표시/숨김 토글"""
        if self.advanced_var and self.advanced_frame:
            if self.advanced_var.get():
                self.advanced_frame.grid()
            else:
                self.advanced_frame.grid_remove()
    
    def _update_glossary_status_label(self, message: str) -> None:
        """용어집 설정 상태 레이블 업데이트"""
        if self.glossary_status_label:
            self.glossary_status_label.config(text=message)
            
            # 3초 후 기본 메시지로 복귀
            if hasattr(self, 'frame') and self.frame:
                self.frame.after(3000, lambda: self._reset_status_label())
    
    def _reset_status_label(self) -> None:
        """상태 레이블을 기본 메시지로 복귀"""
        if self.glossary_status_label:
            self.glossary_status_label.config(text="⏸️ 설정 변경 대기 중...")
    
    def _on_glossary_setting_changed(self, event=None) -> None:
        """용어집 설정 변경 감지"""
        self._update_glossary_status_label("⚠️ 설정이 변경됨 (저장 필요)")
        
        # 저장 버튼 강조
        if self.save_glossary_settings_button:
            try:
                self.save_glossary_settings_button.config(bootstyle="warning")
            except Exception:
                # bootstyle이 지원되지 않는 경우 무시
                pass

    # ========== 용어집 추출 메서드 ==========
    
    def _extract_glossary_thread(self) -> None:
        """용어집 추출 스레드 시작"""
        app_service = self.app_service
        if not app_service:
            messagebox.showerror("오류", "애플리케이션 서비스가 초기화되지 않았습니다.")
            return
        
        # 입력 파일 가져오기 (콜백 사용)
        input_files = self.get_input_files_callback() if self.get_input_files_callback else []
        if not input_files:
            messagebox.showwarning("경고", "입력 파일을 먼저 추가해주세요.")
            return
        
        input_file = input_files[0]  # 첫 번째 파일 사용
        
        if not Path(input_file).exists():
            messagebox.showerror("오류", f"입력 파일을 찾을 수 없습니다: {input_file}")
            return
        
        try:
            # UI 설정을 현재 상태로 업데이트 (SettingsTab에서 가져옴)
            # 참고: 실제로는 main_gui가 _get_config_from_ui()를 호출해서 처리
            
            if not app_service.gemini_client:
                if not messagebox.askyesno("API 설정 경고", 
                                           "API 클라이언트가 초기화되지 않았습니다. 계속 진행하시겠습니까?"):
                    return
                    
        except ValueError as ve:
            messagebox.showerror("입력 오류", f"설정값 오류: {ve}")
            return
        except Exception as e:
            messagebox.showerror("오류", f"용어집 추출 시작 전 설정 오류: {e}")
            self.logger.error(f"용어집 추출 시작 전 설정 오류: {e}", exc_info=True)
            return
        
        self.glossary_progress_label.config(text="용어집 추출 시작 중...")
        self.logger.info(f"용어집 추출 시작: {input_file}")
        
        # 버튼 상태 및 플래그 관리
        self.glossary_stop_requested = False
        self.extract_glossary_button.config(state=tk.DISABLED)
        self.stop_glossary_button.config(state=tk.NORMAL)
        
        def _extraction_task_wrapper():
            try:
                if app_service:
                    result_json_path = app_service.extract_glossary(
                        input_file,
                        progress_callback=self._update_glossary_extraction_progress,
                        seed_glossary_path=app_service.config.get("glossary_json_path"),
                        user_override_glossary_extraction_prompt=app_service.config.get(
                            "user_override_glossary_extraction_prompt"
                        ),
                        stop_check=lambda: self.glossary_stop_requested
                    )
                    
                    if self.glossary_stop_requested:
                        self.parent.after(0, lambda: messagebox.showinfo(
                            "중지됨", 
                            f"용어집 추출이 중지되었습니다.\n현재까지의 결과가 저장되었습니다: {result_json_path}"
                        ))
                    else:
                        self.parent.after(0, lambda: messagebox.showinfo(
                            "성공", 
                            f"용어집 추출 완료!\n결과 파일: {result_json_path}"
                        ))
                    
                    self.parent.after(0, lambda: self.glossary_progress_label.config(
                        text=f"추출 완료: {result_json_path.name}"
                    ))
                    self.parent.after(0, lambda: self._update_glossary_json_path_entry(str(result_json_path)))
                    
                    # 결과를 표시 영역에 로드
                    if result_json_path and result_json_path.exists():
                        with open(result_json_path, 'r', encoding='utf-8') as f_res:
                            lore_content = f_res.read()
                        self.parent.after(0, lambda: self._display_glossary_content(lore_content))
            
            except (BtgFileHandlerException, BtgApiClientException, 
                    BtgServiceException, BtgBusinessLogicException) as e_btg:
                self.logger.error(f"용어집 추출 중 BTG 예외 발생: {e_btg}", exc_info=True)
                self.parent.after(0, lambda: messagebox.showerror(
                    "추출 오류", 
                    f"용어집 추출 중 오류: {e_btg}"
                ))
                self.parent.after(0, lambda: self.glossary_progress_label.config(text="오류 발생"))
            except Exception as e_unknown:
                self.logger.error(f"용어집 추출 중 알 수 없는 예외 발생: {e_unknown}", exc_info=True)
                self.parent.after(0, lambda: messagebox.showerror(
                    "알 수 없는 오류", 
                    f"용어집 추출 중 예상치 못한 오류: {e_unknown}"
                ))
                self.parent.after(0, lambda: self.glossary_progress_label.config(
                    text="알 수 없는 오류 발생"
                ))
            finally:
                self.parent.after(0, lambda: self.extract_glossary_button.config(state=tk.NORMAL))
                self.parent.after(0, lambda: self.stop_glossary_button.config(state=tk.DISABLED))
                self.logger.info("용어집 추출 스레드 종료.")
        
        thread = threading.Thread(target=_extraction_task_wrapper, daemon=True)
        thread.start()
    
    def _update_glossary_extraction_progress(self, dto: GlossaryExtractionProgressDTO) -> None:
        """추출 진행률 업데이트"""
        def _update():
            if not self.parent.winfo_exists():
                return
            msg = (f"{dto.current_status_message} "
                   f"({dto.processed_segments}/{dto.total_segments}, "
                   f"추출 항목: {dto.extracted_entries_count})")
            self.glossary_progress_label.config(text=msg)
        
        if self.parent.winfo_exists():
            self.parent.after(0, _update)
    
    def _request_stop_glossary_extraction(self) -> None:
        """용어집 추출 중지 요청"""
        self.glossary_stop_requested = True
        self.logger.info("용어집 추출 중지 요청됨.")
    
    def _show_sampling_estimate(self) -> None:
        """샘플링 비율에 따른 예상 처리량 표시"""
        # 입력 파일 가져오기 (콜백 사용)
        input_files = self.get_input_files_callback() if self.get_input_files_callback else []
        if not input_files:
            return
        
        input_file = input_files[0]
        
        if not input_file or not Path(input_file).exists():
            return
        
        try:
            # 파일 크기 기반 추정
            file_size = Path(input_file).stat().st_size
            chunk_size = self.get_chunk_size_callback() if self.get_chunk_size_callback else 6000
            estimated_chunks = file_size // chunk_size if chunk_size > 0 else 0
            
            sample_ratio = self.sample_ratio_scale.get() / 100.0
            estimated_sample_chunks = int(estimated_chunks * sample_ratio)
            
            # 추정 정보 (현재는 로깅만)
            estimate_text = f"예상 분석 청크: {estimated_sample_chunks}/{estimated_chunks}"
            self.logger.debug(estimate_text)
            
        except Exception:
            pass  # 추정 실패 시 무시

    # ========== 설정 관리 메서드 ==========
    
    def _save_glossary_settings(self) -> None:
        """용어집 관련 설정만 저장"""
        app_service = self.app_service
        if not app_service:
            messagebox.showerror("오류", "AppService가 초기화되지 않았습니다.")
            return
        
        try:
            # 현재 전체 설정 가져오기
            current_config = app_service.config.copy()
            
            # 용어집 관련 설정만 업데이트
            glossary_config = self.get_config()
            current_config.update(glossary_config)
            
            # 설정 저장
            if app_service.save_app_config(current_config):
                messagebox.showinfo("성공", "용어집 설정이 저장되었습니다.")
                self.logger.info("용어집 설정 저장 완료.")
                self._update_glossary_status_label("✅ 설정 저장됨")
                
                # 저장 버튼 스타일 복원
                if self.save_glossary_settings_button:
                    try:
                        self.save_glossary_settings_button.config(bootstyle="success")
                    except Exception:
                        pass
            else:
                messagebox.showerror("오류", "용어집 설정 저장에 실패했습니다.")
                
        except Exception as e:
            messagebox.showerror("오류", f"설정 저장 중 오류: {e}")
            self.logger.error(f"용어집 설정 저장 오류: {e}", exc_info=True)
    
    def _reset_glossary_settings(self) -> None:
        """용어집 설정을 기본값으로 초기화"""
        app_service = self.app_service
        if not app_service or not app_service.config_manager:
            messagebox.showerror("오류", "AppService 또는 ConfigManager가 초기화되지 않았습니다.")
            return
        
        result = messagebox.askyesno(
            "설정 초기화", 
            "용어집 설정을 기본값으로 초기화하시겠습니까?"
        )
        
        if result:
            try:
                # 기본값 로드
                default_config = app_service.config_manager.get_default_config()
                
                # UI에 기본값 적용
                if self.sample_ratio_scale:
                    self.sample_ratio_scale.set(default_config.get("glossary_sampling_ratio", 10.0))
                if self.extraction_temp_scale:
                    self.extraction_temp_scale.set(default_config.get("glossary_extraction_temperature", 0.3))
                if self.user_override_glossary_prompt_text:
                    self.user_override_glossary_prompt_text.delete('1.0', tk.END)
                    self.user_override_glossary_prompt_text.insert(
                        '1.0', 
                        default_config.get("user_override_glossary_extraction_prompt", "")
                    )
                
                # 동적 주입 설정 초기화
                if self.enable_dynamic_glossary_injection_var:
                    self.enable_dynamic_glossary_injection_var.set(
                        default_config.get("enable_dynamic_glossary_injection", False)
                    )
                if self.max_glossary_entries_injection_entry:
                    self.max_glossary_entries_injection_entry.delete(0, tk.END)
                    self.max_glossary_entries_injection_entry.insert(
                        0, str(default_config.get("max_glossary_entries_per_chunk_injection", 3))
                    )
                if self.max_glossary_chars_injection_entry:
                    self.max_glossary_chars_injection_entry.delete(0, tk.END)
                    self.max_glossary_chars_injection_entry.insert(
                        0, str(default_config.get("max_glossary_chars_per_chunk_injection", 500))
                    )
                
                # 레이블 업데이트
                if self.sample_ratio_scale:
                    self._update_sample_ratio_label(str(self.sample_ratio_scale.get()))
                if self.extraction_temp_scale:
                    self._update_extraction_temp_label(str(self.extraction_temp_scale.get()))
                
                self._update_glossary_status_label("🔄 기본값으로 초기화됨")
                self.logger.info("용어집 설정이 기본값으로 초기화되었습니다.")
                
            except Exception as e:
                messagebox.showerror("오류", f"기본값 로드 중 오류: {e}")
    
    def _preview_glossary_settings(self) -> None:
        """현재 설정의 예상 효과 미리보기"""
        try:
            # 입력 파일 가져오기 (콜백 사용)
            input_files = self.get_input_files_callback() if self.get_input_files_callback else []
            if not input_files:
                messagebox.showwarning("파일 없음", "'설정 및 번역' 탭에서 입력 파일을 먼저 추가하고 선택해주세요.")
                return
            
            input_file = input_files[0]
            
            if not input_file or not Path(input_file).exists():
                messagebox.showwarning("파일 없음", f"선택한 파일을 찾을 수 없습니다: {input_file}")
                return
            
            # 현재 설정 값들
            sample_ratio = self.sample_ratio_scale.get() if self.sample_ratio_scale else 10.0
            extraction_temp = self.extraction_temp_scale.get() if self.extraction_temp_scale else 0.3
            
            # 파일 크기 기반 추정
            file_size = Path(input_file).stat().st_size
            chunk_size = self.get_chunk_size_callback() if self.get_chunk_size_callback else 6000
            estimated_chunks = max(1, file_size // chunk_size) if chunk_size > 0 else 1
            estimated_sample_chunks = max(1, int(estimated_chunks * sample_ratio / 100.0))
            
            # 미리보기 정보 표시
            preview_msg = (
                f"📊 용어집 추출 설정 미리보기\n\n"
                f"📁 입력 파일: {Path(input_file).name}\n"
                f"📏 파일 크기: {file_size:,} 바이트\n"
                f"🧩 예상 청크 수: {estimated_chunks:,}개\n"
                f"🎯 분석할 샘플: {estimated_sample_chunks:,}개 ({sample_ratio:.1f}%)\n"
                f"🌡️ 추출 온도: {extraction_temp:.2f}\n\n"
                f"⏱️ 예상 처리 시간: {estimated_sample_chunks * 2:.0f}~{estimated_sample_chunks * 5:.0f}초"
            )
            
            messagebox.showinfo("설정 미리보기", preview_msg)
        except Exception as e:
            messagebox.showerror("오류", f"미리보기 중 오류: {e}")
    
    def get_config(self) -> Dict[str, Any]:
        """
        UI에서 용어집 관련 설정값을 추출합니다.
        
        Returns:
            용어집 설정 딕셔너리
        """
        if not self.app_service:
            self.logger.error("AppService not initialized in get_config")
            return {}
        
        try:
            config = {
                "glossary_json_path": (
                    self.glossary_json_path_entry.get().strip() or None
                ) if self.glossary_json_path_entry else None,
                
                "glossary_sampling_ratio": (
                    self.sample_ratio_scale.get()
                ) if self.sample_ratio_scale else 10.0,
                
                "glossary_extraction_temperature": (
                    self.extraction_temp_scale.get()
                ) if self.extraction_temp_scale else 0.3,
                
                # 동적 용어집 주입 설정
                "enable_dynamic_glossary_injection": (
                    self.enable_dynamic_glossary_injection_var.get()
                ) if self.enable_dynamic_glossary_injection_var else False,
                
                "max_glossary_entries_per_chunk_injection": int(
                    self.max_glossary_entries_injection_entry.get() or "3"
                ) if self.max_glossary_entries_injection_entry else 3,
                
                "max_glossary_chars_per_chunk_injection": int(
                    self.max_glossary_chars_injection_entry.get() or "500"
                ) if self.max_glossary_chars_injection_entry else 500,
                
                "user_override_glossary_extraction_prompt": (
                    self.user_override_glossary_prompt_text.get("1.0", tk.END).strip()
                ) if self.user_override_glossary_prompt_text else "",
            }
            
            # None 값 필터링
            return {k: v for k, v in config.items() if v is not None}
        except Exception as e:
            raise ValueError(f"용어집 설정 값 오류: {e}")
    
    def load_config(self, config: Dict[str, Any]) -> None:
        """
        설정값을 UI에 반영합니다.
        
        Args:
            config: 적용할 설정값 딕셔너리
        """
        try:
            # 용어집 JSON 경로
            glossary_json_path_val = config.get("glossary_json_path")
            if self.glossary_json_path_entry:
                self.glossary_json_path_entry.delete(0, tk.END)
                self.glossary_json_path_entry.insert(
                    0, 
                    glossary_json_path_val if glossary_json_path_val is not None else ""
                )
            
            # 샘플링 비율
            sample_ratio = config.get("glossary_sampling_ratio", 10.0)
            if self.sample_ratio_scale:
                self.sample_ratio_scale.set(sample_ratio)
            if self.sample_ratio_label:
                self.sample_ratio_label.config(text=f"{sample_ratio:.1f}%")
            
            # 추출 온도
            extraction_temp = config.get("glossary_extraction_temperature", 0.3)
            if self.extraction_temp_scale:
                self.extraction_temp_scale.set(extraction_temp)
            if self.extraction_temp_label:
                self.extraction_temp_label.config(text=f"{extraction_temp:.2f}")
            
            # 동적 용어집 주입 설정
            if self.enable_dynamic_glossary_injection_var:
                self.enable_dynamic_glossary_injection_var.set(
                    config.get("enable_dynamic_glossary_injection", False)
                )
            
            if self.max_glossary_entries_injection_entry:
                self.max_glossary_entries_injection_entry.delete(0, tk.END)
                self.max_glossary_entries_injection_entry.insert(
                    0, 
                    str(config.get("max_glossary_entries_per_chunk_injection", 3))
                )
            
            if self.max_glossary_chars_injection_entry:
                self.max_glossary_chars_injection_entry.delete(0, tk.END)
                self.max_glossary_chars_injection_entry.insert(
                    0, 
                    str(config.get("max_glossary_chars_per_chunk_injection", 500))
                )
            
            # 사용자 정의 추출 프롬프트
            if self.user_override_glossary_prompt_text:
                self.user_override_glossary_prompt_text.delete('1.0', tk.END)
                self.user_override_glossary_prompt_text.insert(
                    '1.0',
                    config.get("user_override_glossary_extraction_prompt", "")
                )
            
            self.logger.debug("용어집 탭 설정 로드 완료")
            
        except Exception as e:
            self.logger.error(f"용어집 설정 로드 오류: {e}", exc_info=True)

    # ========== 용어집 표시/편집 메서드 ==========
    
    def _display_glossary_content(self, content: str) -> None:
        """용어집 내용을 표시 영역에 표시"""
        if self.glossary_display_text:
            self.glossary_display_text.config(state=tk.NORMAL)
            self.glossary_display_text.delete('1.0', tk.END)
            self.glossary_display_text.insert('1.0', content)
            self.glossary_display_text.config(state=tk.DISABLED)
    
    def _load_glossary_to_display(self) -> None:
        """파일에서 용어집을 로드하여 표시"""
        filepath = filedialog.askopenfilename(
            title="용어집 JSON 파일 선택", 
            filetypes=(("JSON 파일", "*.json"), ("모든 파일", "*.*"))
        )
        
        if filepath:
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                self._display_glossary_content(content)
                if self.glossary_json_path_entry:
                    self.glossary_json_path_entry.delete(0, tk.END)
                    self.glossary_json_path_entry.insert(0, filepath)
                self.logger.info(f"용어집 파일 로드됨: {filepath}")
            except Exception as e:
                messagebox.showerror("오류", f"용어집 파일 로드 실패: {e}")
                self.logger.error(f"용어집 파일 로드 실패: {e}")
    
    def _copy_glossary_json(self) -> None:
        """표시된 용어집 JSON을 클립보드에 복사"""
        if not self.glossary_display_text:
            return
            
        content = self.glossary_display_text.get('1.0', tk.END).strip()
        if content:
            self.parent.clipboard_clear()
            self.parent.clipboard_append(content)
            messagebox.showinfo("성공", "용어집 JSON 내용이 클립보드에 복사되었습니다.")
            self.logger.info("용어집 JSON 클립보드에 복사됨.")
        else:
            messagebox.showwarning("경고", "복사할 내용이 없습니다.")
    
    def _save_displayed_glossary_json(self) -> None:
        """표시된 용어집 JSON을 파일로 저장"""
        if not self.glossary_display_text:
            return
            
        content = self.glossary_display_text.get('1.0', tk.END).strip()
        if not content:
            messagebox.showwarning("경고", "저장할 내용이 없습니다.")
            return
        
        filepath = filedialog.asksaveasfilename(
            title="용어집 JSON으로 저장", 
            defaultextension=".json", 
            filetypes=(("JSON 파일", "*.json"), ("모든 파일", "*.*"))
        )
        if filepath:
            try:
                with open(filepath, 'w', encoding='utf-8') as f:
                    f.write(content)
                messagebox.showinfo("성공", f"용어집이 성공적으로 저장되었습니다: {filepath}")
                self.logger.info(f"표시된 용어집 저장됨: {filepath}")
            except Exception as e:
                messagebox.showerror("오류", f"용어집 저장 실패: {e}")
                self.logger.error(f"표시된 용어집 저장 실패: {e}")
    
    def _open_glossary_editor(self) -> None:
        """용어집 편집기 창 열기"""
        if not self.glossary_display_text:
            return
            
        current_json_str = self.glossary_display_text.get('1.0', tk.END).strip()
        if not current_json_str:
            if not messagebox.askyesno(
                "용어집 비어있음", 
                "표시된 용어집 내용이 없습니다. 새 용어집을 만드시겠습니까?"
            ):
                return
            current_json_str = "[]"  # 새 용어집을 위한 빈 리스트
        
        try:
            # JSON 유효성 검사
            json.loads(current_json_str)
        except json.JSONDecodeError as e:
            messagebox.showerror(
                "JSON 오류", 
                f"용어집 내용이 유효한 JSON 형식이 아닙니다: {e}"
            )
            return
        
        # 입력 파일 경로 가져오기 (콜백 사용)
        input_file_path = ""
        input_files = self._get_input_files() if self._get_input_files else []
        if input_files:
            input_file_path = input_files[0]
        
        editor_window = GlossaryEditorWindow(
            self.parent, 
            current_json_str, 
            self._handle_glossary_editor_save, 
            input_file_path
        )
        editor_window.grab_set()  # Modal-like behavior
    
    def _handle_glossary_editor_save(self, updated_json_str: str) -> None:
        """용어집 편집기 저장 콜백"""
        self._display_glossary_content(updated_json_str)
        self.logger.info("용어집 편집기에서 변경 사항이 적용되었습니다.")
        
        # 파일 저장 확인
        if messagebox.askyesno(
            "파일 저장 확인", 
            "편집된 용어집을 현재 설정된 JSON 파일 경로에 저장하시겠습니까?"
        ):
            glossary_file_path = (
                self.glossary_json_path_entry.get() 
                if self.glossary_json_path_entry else ""
            )
            if glossary_file_path:
                try:
                    with open(glossary_file_path, 'w', encoding='utf-8') as f:
                        f.write(updated_json_str)
                    messagebox.showinfo(
                        "저장 완료", 
                        f"용어집이 '{glossary_file_path}'에 저장되었습니다."
                    )
                    self.logger.info(f"편집된 용어집 파일 저장됨: {glossary_file_path}")
                except Exception as e:
                    messagebox.showerror("파일 저장 오류", f"용어집 파일 저장 실패: {e}")
                    self.logger.error(f"편집된 용어집 파일 저장 실패: {e}")
            else:
                messagebox.showwarning(
                    "경로 없음", 
                    "용어집 JSON 파일 경로가 설정되지 않았습니다. 'JSON 저장' 버튼을 사용하거나 경로를 설정해주세요."
                )

    # ========== 유틸리티 메서드 ==========
    
    def get_glossary_path(self) -> str:
        """
        현재 용어집 경로를 반환합니다.
        
        Returns:
            용어집 파일 경로
        """
        if self.glossary_json_path_entry:
            return self.glossary_json_path_entry.get().strip()
        return ""
    
    def set_glossary_path(self, path: str) -> None:
        """
        용어집 경로를 설정합니다.
        
        Args:
            path: 설정할 용어집 파일 경로
        """
        if self.glossary_json_path_entry:
            self.glossary_json_path_entry.delete(0, tk.END)
            self.glossary_json_path_entry.insert(0, path)
            
            # 콜백 호출
            if self._on_glossary_path_changed and path:
                self._on_glossary_path_changed(path)
    
    def get_displayed_glossary_json(self) -> str:
        """
        현재 표시된 용어집 JSON 내용을 반환합니다.
        
        Returns:
            표시된 JSON 문자열
        """
        if self.glossary_display_text:
            return self.glossary_display_text.get('1.0', tk.END).strip()
        return ""
