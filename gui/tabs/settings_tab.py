"""
설정 및 번역 탭

API 설정, 파일 설정, 프롬프트 설정 및 번역 실행을 담당하는 탭입니다.
"""

import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
import ttkbootstrap as ttk
from ttkbootstrap.constants import *
import threading
import os
from pathlib import Path
from typing import Optional, Dict, Any, List, Callable
import json
import logging

from gui.tabs.base_tab import BaseTab
from gui.components.tooltip import Tooltip
from gui.components.scrollable_frame import ScrollableFrame


class SettingsTab(BaseTab):
    """설정 및 번역 탭 클래스"""
    
    def __init__(
        self, 
        parent: tk.Widget, 
        app_service, 
        logger,
        # 콜백 함수들
        on_translation_progress: Optional[Callable] = None,
        on_translation_status: Optional[Callable] = None,
        on_translation_complete: Optional[Callable] = None,
        get_glossary_path: Optional[Callable[[], str]] = None,
        get_tqdm_stream: Optional[Callable] = None,
        on_glossary_path_changed: Optional[Callable[[str], None]] = None,
    ):
        """
        Args:
            parent: 부모 위젯 (Notebook)
            app_service: AppService 인스턴스
            logger: 로거 인스턴스
            on_translation_progress: 번역 진행률 업데이트 콜백
            on_translation_status: 번역 상태 업데이트 콜백
            on_translation_complete: 번역 완료 시 콜백
            get_glossary_path: 용어집 경로를 가져오는 콜백
            get_tqdm_stream: TQDM 스트림을 가져오는 콜백
            on_glossary_path_changed: 용어집 경로 변경 시 호출되는 콜백
        """
        super().__init__(parent, app_service, logger)
        
        # 콜백 함수 저장
        self._on_translation_progress = on_translation_progress
        self._on_translation_status = on_translation_status
        self._on_translation_complete = on_translation_complete
        self._get_glossary_path = get_glossary_path
        self._get_tqdm_stream = get_tqdm_stream
        self._on_glossary_path_changed = on_glossary_path_changed
        
        # 스크롤 프레임
        self.scroll_frame: Optional[ScrollableFrame] = None
        
        # 상태 변수
        self.stop_requested = False
        self._translation_thread: Optional[threading.Thread] = None
        self._client_needs_refresh = False
        
        # === API 및 인증 설정 위젯 ===
        self.api_keys_label: Optional[ttk.Label] = None
        self.api_keys_text: Optional[scrolledtext.ScrolledText] = None
        self.use_vertex_ai_var: Optional[tk.BooleanVar] = None
        self.use_vertex_ai_check = None
        self.service_account_file_label: Optional[ttk.Label] = None
        self.service_account_file_entry: Optional[ttk.Entry] = None
        self.browse_sa_file_button = None
        self.gcp_project_label: Optional[ttk.Label] = None
        self.gcp_project_entry: Optional[ttk.Entry] = None
        self.gcp_location_label: Optional[ttk.Label] = None
        self.gcp_location_entry: Optional[ttk.Entry] = None
        self.model_name_combobox: Optional[ttk.Combobox] = None
        self.refresh_models_button = None
        
        # === 생성 파라미터 위젯 ===
        self.temperature_scale: Optional[ttk.Scale] = None
        self.temperature_label: Optional[ttk.Label] = None
        self.top_p_scale: Optional[ttk.Scale] = None
        self.top_p_label: Optional[ttk.Label] = None
        self.thinking_budget_entry: Optional[ttk.Entry] = None
        
        # === 파일 및 처리 설정 위젯 ===
        self.input_file_listbox: Optional[tk.Listbox] = None
        self.add_files_button = None
        self.remove_file_button = None
        self.output_file_entry: Optional[ttk.Entry] = None
        self.browse_output_button = None
        self.chunk_size_entry: Optional[ttk.Entry] = None
        self.max_workers_entry: Optional[ttk.Entry] = None
        self.rpm_entry: Optional[ttk.Entry] = None
        
        # === 언어 설정 위젯 ===
        self.novel_language_entry: Optional[ttk.Entry] = None
        self.novel_language_fallback_entry: Optional[ttk.Entry] = None
        
        # === 프롬프트 설정 위젯 ===
        self.prompt_text: Optional[scrolledtext.ScrolledText] = None
        
        # === 프리필 설정 위젯 ===
        self.enable_prefill_var: Optional[tk.BooleanVar] = None
        self.enable_prefill_check = None
        self.prefill_system_instruction_text: Optional[scrolledtext.ScrolledText] = None
        self.prefill_cached_history_text: Optional[scrolledtext.ScrolledText] = None
        
        # === 콘텐츠 안전 재시도 설정 위젯 ===
        self.use_content_safety_retry_var: Optional[tk.BooleanVar] = None
        self.use_content_safety_retry_check = None
        self.max_split_attempts_entry: Optional[ttk.Entry] = None
        self.min_chunk_size_entry: Optional[ttk.Entry] = None
        
        # === 액션 버튼 및 진행률 위젯 ===
        self.save_settings_button = None
        self.load_settings_button = None
        self.start_button = None
        self.retry_failed_button = None
        self.stop_button = None
        self.progress_bar: Optional[ttk.Progressbar] = None
        self.progress_label: Optional[ttk.Label] = None
        
        # Vertex AI 필드 참조 (toggle용)
        self._vertex_widgets: List[tk.Widget] = []

    def create_widgets(self) -> ttk.Frame:
        """
        설정 탭 위젯들을 생성합니다.
        
        Returns:
            생성된 탭의 메인 프레임
        """
        # 스크롤 가능한 프레임 생성
        self.scroll_frame = ScrollableFrame(self.parent)
        self.frame = self.scroll_frame.main_frame
        
        settings_frame = self.scroll_frame.scrollable_frame
        
        # 각 섹션 위젯 생성
        self._create_api_section(settings_frame)
        self._create_generation_params_section(settings_frame)
        self._create_file_section(settings_frame)
        self._create_language_section(settings_frame)
        self._create_prompt_section(settings_frame)
        self._create_prefill_section(settings_frame)
        self._create_content_safety_section(settings_frame)
        self._create_action_section(settings_frame)
        self._create_progress_section(settings_frame)
        
        # 초기 상태 설정
        self._toggle_vertex_fields()
        
        return self.frame

    def _create_api_section(self, parent: ttk.Frame) -> None:
        """API 및 인증 설정 섹션 생성"""
        api_frame = ttk.Labelframe(parent, text="API 및 인증 설정", padding="10")
        api_frame.pack(fill="x", padx=5, pady=5)
        
        # API 키 입력
        self.api_keys_label = ttk.Label(api_frame, text="API 키 목록 (Gemini Developer, 한 줄에 하나씩):")
        self.api_keys_label.grid(row=0, column=0, padx=5, pady=5, sticky="nw")
        Tooltip(self.api_keys_label, "Gemini Developer API를 사용할 경우 API 키를 입력합니다.\n여러 개일 경우 한 줄에 하나씩 입력하세요.")
        
        self.api_keys_text = scrolledtext.ScrolledText(api_frame, width=58, height=3, wrap=tk.WORD)
        self.api_keys_text.grid(row=0, column=1, columnspan=2, padx=5, pady=5, sticky="ew")
        Tooltip(self.api_keys_text, "사용할 Gemini API 키 목록입니다.")
        self.api_keys_text.bind('<KeyRelease>', self._on_api_key_changed)
        
        # Vertex AI 설정
        self.use_vertex_ai_var = tk.BooleanVar()
        self.use_vertex_ai_check = ttk.Checkbutton(
            api_frame, 
            text="Vertex AI 사용", 
            variable=self.use_vertex_ai_var, 
            command=self._toggle_vertex_fields
        )
        self.use_vertex_ai_check.grid(row=1, column=0, columnspan=3, padx=5, pady=2, sticky="w")
        Tooltip(self.use_vertex_ai_check, "Google Cloud Vertex AI API를 사용하려면 선택하세요.\n서비스 계정 JSON 파일 또는 ADC 인증이 필요합니다.")

        # 서비스 계정 파일
        self.service_account_file_label = ttk.Label(api_frame, text="서비스 계정 JSON 파일 (Vertex AI):")
        self.service_account_file_label.grid(row=2, column=0, padx=5, pady=5, sticky="w")
        Tooltip(self.service_account_file_label, "Vertex AI 인증에 사용할 서비스 계정 JSON 파일의 경로입니다.")
        self.service_account_file_entry = ttk.Entry(api_frame, width=50)
        self.service_account_file_entry.grid(row=2, column=1, padx=5, pady=5, sticky="ew")
        Tooltip(self.service_account_file_entry, "Vertex AI 서비스 계정 파일 경로를 입력하거나 '찾아보기'로 선택하세요.")
        self.browse_sa_file_button = ttk.Button(api_frame, text="찾아보기", command=self._browse_service_account_file)
        self.browse_sa_file_button.grid(row=2, column=2, padx=5, pady=5)
        Tooltip(self.browse_sa_file_button, "서비스 계정 JSON 파일을 찾습니다.")

        # GCP 프로젝트 ID
        self.gcp_project_label = ttk.Label(api_frame, text="GCP 프로젝트 ID (Vertex AI):")
        self.gcp_project_label.grid(row=3, column=0, padx=5, pady=5, sticky="w")
        Tooltip(self.gcp_project_label, "Vertex AI 사용 시 필요한 Google Cloud Project ID입니다.")
        self.gcp_project_entry = ttk.Entry(api_frame, width=30)
        self.gcp_project_entry.grid(row=3, column=1, padx=5, pady=5, sticky="ew")
        Tooltip(self.gcp_project_entry, "GCP 프로젝트 ID를 입력하세요.")

        # GCP 위치
        self.gcp_location_label = ttk.Label(api_frame, text="GCP 위치 (Vertex AI):")
        self.gcp_location_label.grid(row=4, column=0, padx=5, pady=5, sticky="w")
        Tooltip(self.gcp_location_label, "Vertex AI 모델이 배포된 GCP 리전입니다 (예: asia-northeast3).")
        self.gcp_location_entry = ttk.Entry(api_frame, width=30)
        self.gcp_location_entry.grid(row=4, column=1, padx=5, pady=5, sticky="ew")
        Tooltip(self.gcp_location_entry, "GCP 리전을 입력하세요.")

        # 모델 이름
        model_name_label = ttk.Label(api_frame, text="모델 이름:")
        model_name_label.grid(row=5, column=0, padx=5, pady=5, sticky="w")
        Tooltip(model_name_label, "번역에 사용할 AI 모델의 이름입니다.")
        self.model_name_combobox = ttk.Combobox(api_frame, width=57) 
        self.model_name_combobox.grid(row=5, column=1, padx=5, pady=5, sticky="ew")
        Tooltip(self.model_name_combobox, "사용 가능한 모델 목록에서 선택하거나 직접 입력하세요.\n'새로고침' 버튼으로 목록을 업데이트할 수 있습니다.")
        self.refresh_models_button = ttk.Button(api_frame, text="새로고침", command=self._update_model_list_ui)
        self.refresh_models_button.grid(row=5, column=2, padx=5, pady=5)
        Tooltip(self.refresh_models_button, "사용 가능한 모델 목록을 API에서 새로 가져옵니다.")
    
    def _create_generation_params_section(self, parent: ttk.Frame) -> None:
        """생성 파라미터 섹션 생성"""
        gen_param_frame = ttk.Labelframe(parent, text="생성 파라미터", padding="10")
        gen_param_frame.pack(fill="x", padx=5, pady=5)
        
        # Temperature 설정
        temperature_param_label = ttk.Label(gen_param_frame, text="Temperature:")
        temperature_param_label.grid(row=0, column=0, padx=5, pady=5, sticky="w")
        Tooltip(temperature_param_label, "모델 응답의 무작위성 조절 (낮을수록 결정적, 높을수록 다양).")
        self.temperature_scale = ttk.Scale(
            gen_param_frame, 
            from_=0.0, 
            to=2.0, 
            orient="horizontal", 
            length=200,
            command=lambda v: self.temperature_label.config(text=f"{float(v):.2f}")
        )
        self.temperature_scale.grid(row=0, column=1, padx=5, pady=5, sticky="ew")
        Tooltip(self.temperature_scale, "Temperature 값을 조절합니다 (0.0 ~ 2.0).")
        self.temperature_label = ttk.Label(gen_param_frame, text="0.00")
        self.temperature_label.grid(row=0, column=2, padx=5, pady=5)
        
        # Top P 설정
        top_p_param_label = ttk.Label(gen_param_frame, text="Top P:")
        top_p_param_label.grid(row=1, column=0, padx=5, pady=5, sticky="w")
        Tooltip(top_p_param_label, "모델이 다음 단어를 선택할 때 고려하는 확률 분포의 누적합 (낮을수록 집중적, 높을수록 다양).")
        self.top_p_scale = ttk.Scale(
            gen_param_frame, 
            from_=0.0, 
            to=1.0, 
            orient="horizontal", 
            length=200,
            command=lambda v: self.top_p_label.config(text=f"{float(v):.2f}")
        )
        self.top_p_scale.grid(row=1, column=1, padx=5, pady=5, sticky="ew")
        Tooltip(self.top_p_scale, "Top P 값을 조절합니다 (0.0 ~ 1.0).")
        self.top_p_label = ttk.Label(gen_param_frame, text="0.00")
        self.top_p_label.grid(row=1, column=2, padx=5, pady=5)

        # Thinking Budget 설정
        thinking_budget_param_label = ttk.Label(gen_param_frame, text="Thinking Budget:")
        thinking_budget_param_label.grid(row=2, column=0, padx=5, pady=5, sticky="w")
        Tooltip(thinking_budget_param_label, "모델이 추론에 사용할 토큰 수 (Gemini 2.5 모델).\nFlash: 0-24576, Pro: 128-32768.\n비워두면 자동 또는 모델 기본값 사용.")
        self.thinking_budget_entry = ttk.Entry(gen_param_frame, width=10)
        self.thinking_budget_entry.grid(row=2, column=1, padx=5, pady=5, sticky="w")
        Tooltip(self.thinking_budget_entry, "Thinking Budget 값을 정수로 입력하세요.\nFlash 모델에서 0은 기능 비활성화입니다.\n비워두는것을 추천함")
    
    def _create_file_section(self, parent: ttk.Frame) -> None:
        """파일 및 처리 설정 섹션 생성"""
        file_chunk_frame = ttk.Labelframe(parent, text="파일 및 처리 설정", padding="10")
        file_chunk_frame.pack(fill="x", padx=5, pady=5)
        
        # 입력 파일 섹션
        input_file_frame = ttk.Labelframe(file_chunk_frame, text="입력 파일 목록", padding="5")
        input_file_frame.grid(row=0, column=0, columnspan=3, sticky="ew", padx=5, pady=5)

        self.input_file_listbox = tk.Listbox(input_file_frame, selectmode=tk.EXTENDED, width=70, height=5)
        self.input_file_listbox.pack(side="left", fill="both", expand=True, padx=5, pady=5)
        
        listbox_scrollbar = ttk.Scrollbar(input_file_frame, orient="vertical", command=self.input_file_listbox.yview)
        listbox_scrollbar.pack(side="right", fill="y")
        self.input_file_listbox.config(yscrollcommand=listbox_scrollbar.set)

        # 파일 추가/삭제 버튼 프레임
        file_button_frame = ttk.Frame(input_file_frame)
        file_button_frame.pack(side="left", fill="y", padx=5)

        self.add_files_button = ttk.Button(file_button_frame, text="파일 추가", command=self._browse_input_files)
        self.add_files_button.pack(pady=2, fill="x")
        Tooltip(self.add_files_button, "번역할 파일을 목록에 추가합니다.")
        
        self.remove_file_button = ttk.Button(file_button_frame, text="선택 삭제", command=self._remove_selected_files)
        self.remove_file_button.pack(pady=2, fill="x")
        Tooltip(self.remove_file_button, "목록에서 선택한 파일을 제거합니다.")

        # 출력 파일
        output_file_label_widget = ttk.Label(file_chunk_frame, text="출력 파일 (단일 모드):")
        output_file_label_widget.grid(row=1, column=0, padx=5, pady=5, sticky="w")
        Tooltip(output_file_label_widget, "단일 파일 번역 시 사용될 출력 파일 경로입니다.\n(배치 처리 시에는 각 파일별로 자동 생성됩니다)")
        self.output_file_entry = ttk.Entry(file_chunk_frame, width=50)
        self.output_file_entry.grid(row=1, column=1, padx=5, pady=5, sticky="ew")
        Tooltip(self.output_file_entry, "번역 결과를 저장할 파일 경로를 입력하거나 '찾아보기'로 선택하세요.")
        self.browse_output_button = ttk.Button(file_chunk_frame, text="찾아보기", command=self._browse_output_file)
        self.browse_output_button.grid(row=1, column=2, padx=5, pady=5)
        Tooltip(self.browse_output_button, "번역 결과를 저장할 출력 파일을 선택합니다.")
        
        # 청크 크기 및 작업자 수
        chunk_worker_frame = ttk.Frame(file_chunk_frame)
        chunk_worker_frame.grid(row=2, column=0, columnspan=3, sticky="ew", pady=5)
        
        chunk_size_label_widget = ttk.Label(chunk_worker_frame, text="청크 크기:")
        chunk_size_label_widget.pack(side="left", padx=(0, 5))
        Tooltip(chunk_size_label_widget, "API 요청당 처리할 텍스트의 최대 문자 수입니다.")      
        self.chunk_size_entry = ttk.Entry(chunk_worker_frame, width=10)
        self.chunk_size_entry.pack(side="left", padx=(0, 15))
        Tooltip(self.chunk_size_entry, "청크 크기를 입력하세요 (예: 6000).")
        
        max_workers_label_widget = ttk.Label(chunk_worker_frame, text="최대 작업자 수:")
        max_workers_label_widget.pack(side="left", padx=(10, 5))
        Tooltip(max_workers_label_widget, "동시에 실행할 번역 스레드의 최대 개수입니다.")       
        self.max_workers_entry = ttk.Entry(chunk_worker_frame, width=5)
        self.max_workers_entry.pack(side="left")
        self.max_workers_entry.insert(0, str(os.cpu_count() or 1))
        Tooltip(self.max_workers_entry, "최대 작업자 수를 입력하세요 (예: 4).")
        
        # RPM 설정
        rpm_label_widget = ttk.Label(chunk_worker_frame, text="분당 요청 수 (RPM):")
        rpm_label_widget.pack(side="left", padx=(10, 5))
        Tooltip(rpm_label_widget, "API에 분당 보낼 수 있는 최대 요청 수입니다. 0은 제한 없음을 의미합니다.")
        
        self.rpm_entry = ttk.Entry(chunk_worker_frame, width=5)
        self.rpm_entry.pack(side="left")
        Tooltip(self.rpm_entry, "분당 요청 수를 입력하세요 (예: 60).")
    
    def _create_language_section(self, parent: ttk.Frame) -> None:
        """언어 설정 섹션 생성"""
        language_settings_frame = ttk.Labelframe(parent, text="언어 설정", padding="10")
        language_settings_frame.pack(fill="x", padx=5, pady=5)

        novel_lang_label = ttk.Label(language_settings_frame, text="소설/번역 출발 언어:")
        novel_lang_label.grid(row=0, column=0, padx=5, pady=5, sticky="w")
        Tooltip(novel_lang_label, "번역할 원본 텍스트의 언어 코드입니다 (예: ko, ja, en).\n'auto'로 설정 시 언어를 자동으로 감지합니다.")
        self.novel_language_entry = ttk.Entry(language_settings_frame, width=10)
        self.novel_language_entry.grid(row=0, column=1, padx=5, pady=5, sticky="w")
        self.novel_language_entry.insert(0, "auto") 
        Tooltip(self.novel_language_entry, "언어 코드를 입력하세요 (BCP-47 형식).")
        ttk.Label(language_settings_frame, text="(예: ko, ja, en, auto)").grid(row=0, column=2, padx=5, pady=5, sticky="w")

        novel_lang_fallback_label = ttk.Label(language_settings_frame, text="언어 자동감지 실패 시 폴백:")
        novel_lang_fallback_label.grid(row=1, column=0, padx=5, pady=5, sticky="w")
        Tooltip(novel_lang_fallback_label, "출발 언어 자동 감지 실패 시 사용할 기본 언어 코드입니다.")
        self.novel_language_fallback_entry = ttk.Entry(language_settings_frame, width=10)
        self.novel_language_fallback_entry.grid(row=1, column=1, padx=5, pady=5, sticky="w")
        self.novel_language_fallback_entry.insert(0, "ja")
        Tooltip(self.novel_language_fallback_entry, "폴백 언어 코드를 입력하세요.")
        ttk.Label(language_settings_frame, text="(예: ko, ja, en)").grid(row=1, column=2, padx=5, pady=5, sticky="w")
    
    def _create_prompt_section(self, parent: ttk.Frame) -> None:
        """프롬프트 설정 섹션 생성"""
        prompt_frame = ttk.Labelframe(parent, text="프롬프트 설정", padding="10")
        prompt_frame.pack(fill="both", expand=True, padx=5, pady=5)

        # 번역 프롬프트
        chat_prompt_label = ttk.Label(prompt_frame, text="번역 프롬프트 (Chat/User Prompt):")
        chat_prompt_label.pack(anchor="w", padx=5, pady=(10, 0))
        Tooltip(prompt_frame, "번역 모델에 전달할 프롬프트입니다.\n{{slot}}은 번역할 텍스트 청크로 대체됩니다.\n{{glossary_context}}는 용어집 내용으로 대체됩니다.")
        self.prompt_text = scrolledtext.ScrolledText(prompt_frame, wrap=tk.WORD, height=10, width=70)
        self.prompt_text.pack(fill="both", expand=True, padx=5, pady=5)
    
    def _create_prefill_section(self, parent: ttk.Frame) -> None:
        """프리필 설정 섹션 생성"""
        prefill_frame = ttk.Labelframe(parent, text="프리필(Prefill) 번역 설정", padding="10")
        prefill_frame.pack(fill="both", expand=True, padx=5, pady=5)
        Tooltip(prefill_frame, "모델에 초기 컨텍스트(시스템 지침 및 대화 기록)를 제공하여 번역 품질을 향상시킬 수 있습니다.")

        self.enable_prefill_var = tk.BooleanVar()
        self.enable_prefill_check = ttk.Checkbutton(prefill_frame, text="프리필 번역 사용", variable=self.enable_prefill_var)
        self.enable_prefill_check.pack(anchor="w", padx=5, pady=(5, 0))
        Tooltip(self.enable_prefill_check, "활성화 시 아래의 프리필 시스템 지침과 캐시된 히스토리를 사용합니다.")

        prefill_system_instruction_label = ttk.Label(prefill_frame, text="프리필 시스템 지침:")
        prefill_system_instruction_label.pack(anchor="w", padx=5, pady=(5, 0))
        Tooltip(prefill_system_instruction_label, "프리필 모드에서 사용할 시스템 레벨 지침입니다.")
        self.prefill_system_instruction_text = scrolledtext.ScrolledText(prefill_frame, wrap=tk.WORD, height=10, width=70)
        self.prefill_system_instruction_text.pack(fill="both", expand=True, padx=5, pady=5)

        prefill_cached_history_label = ttk.Label(prefill_frame, text="프리필 캐시된 히스토리 (JSON 형식):")
        prefill_cached_history_label.pack(anchor="w", padx=5, pady=(5, 0))
        Tooltip(prefill_cached_history_label, "미리 정의된 대화 기록을 JSON 형식으로 입력합니다.\n예: [{\"role\": \"user\", \"parts\": [\"안녕\"]}, {\"role\": \"model\", \"parts\": [\"안녕하세요.\"]}]")
        self.prefill_cached_history_text = scrolledtext.ScrolledText(prefill_frame, wrap=tk.WORD, height=10, width=70)
        self.prefill_cached_history_text.pack(fill="both", expand=True, padx=5, pady=5)
    
    def _create_content_safety_section(self, parent: ttk.Frame) -> None:
        """콘텐츠 안전 재시도 설정 섹션 생성"""
        content_safety_frame = ttk.Labelframe(parent, text="콘텐츠 안전 재시도 설정", padding="10")
        content_safety_frame.pack(fill="x", padx=5, pady=5)

        self.use_content_safety_retry_var = tk.BooleanVar()
        self.use_content_safety_retry_check = ttk.Checkbutton(
            content_safety_frame,
            text="검열 오류시 청크 분할 재시도 사용",
            variable=self.use_content_safety_retry_var
        )
        Tooltip(self.use_content_safety_retry_check, "API에서 콘텐츠 안전 문제로 응답이 차단될 경우,\n텍스트를 더 작은 조각으로 나누어 재시도합니다.")
        self.use_content_safety_retry_check.grid(row=0, column=0, columnspan=3, padx=5, pady=2, sticky="w")
        
        max_split_label = ttk.Label(content_safety_frame, text="최대 분할 시도:")
        max_split_label.grid(row=1, column=0, padx=5, pady=5, sticky="w")
        Tooltip(max_split_label, "콘텐츠 안전 문제 발생 시 청크를 나누어 재시도할 최대 횟수입니다.")
        self.max_split_attempts_entry = ttk.Entry(content_safety_frame, width=5)
        self.max_split_attempts_entry.grid(row=1, column=1, padx=5, pady=5, sticky="w")
        self.max_split_attempts_entry.insert(0, "3")
        Tooltip(self.max_split_attempts_entry, "최대 분할 시도 횟수를 입력하세요.")
        
        min_chunk_label = ttk.Label(content_safety_frame, text="최소 청크 크기:")
        min_chunk_label.grid(row=2, column=0, padx=5, pady=5, sticky="w")
        Tooltip(min_chunk_label, "분할 재시도 시 청크가 이 크기보다 작아지지 않도록 합니다.")
        self.min_chunk_size_entry = ttk.Entry(content_safety_frame, width=10)
        self.min_chunk_size_entry.grid(row=2, column=1, padx=5, pady=5, sticky="w")
        self.min_chunk_size_entry.insert(0, "100")
        Tooltip(self.min_chunk_size_entry, "최소 청크 크기를 입력하세요.")
    
    def _create_action_section(self, parent: ttk.Frame) -> None:
        """액션 버튼 섹션 생성"""
        action_frame = ttk.Frame(parent, padding="10")
        action_frame.pack(fill="x", padx=5, pady=5)
        
        self.save_settings_button = ttk.Button(action_frame, text="설정 저장", command=self._save_settings)
        self.save_settings_button.pack(side="left", padx=5)
        Tooltip(self.save_settings_button, "현재 UI에 입력된 모든 설정을 config.json 파일에 저장합니다.")
        
        self.load_settings_button = ttk.Button(action_frame, text="설정 불러오기", command=self._load_settings_ui)
        self.load_settings_button.pack(side="left", padx=5)
        Tooltip(self.load_settings_button, "config.json 파일에서 설정을 불러와 UI에 적용합니다.")
        
        self.start_button = ttk.Button(action_frame, text="번역 시작", command=self._start_translation_thread_with_resume_check)
        self.start_button.pack(side="right", padx=5)
        Tooltip(self.start_button, "현재 설정으로 입력 파일의 번역 작업을 시작합니다.")

        self.retry_failed_button = ttk.Button(action_frame, text="실패 청크 재시도", command=self._start_failed_chunks_translation_thread)
        self.retry_failed_button.pack(side="right", padx=5)
        Tooltip(self.retry_failed_button, "선택한 파일의 메타데이터에 기록된 실패한 청크들만 다시 번역합니다.")
        
        self.stop_button = ttk.Button(action_frame, text="중지", command=self._request_stop_translation, state=tk.DISABLED)
        self.stop_button.pack(side="right", padx=5)
        Tooltip(self.stop_button, "현재 진행 중인 번역 작업을 중지 요청합니다.")
    
    def _create_progress_section(self, parent: ttk.Frame) -> None:
        """진행률 표시 섹션 생성"""
        progress_frame = ttk.Frame(parent)
        progress_frame.pack(fill="x", padx=15, pady=10)
        
        self.progress_bar = ttk.Progressbar(progress_frame, orient="horizontal", length=300, mode="determinate")
        self.progress_bar.pack(fill="x", pady=5)
        Tooltip(self.progress_bar, "번역 작업의 전체 진행률을 표시합니다.")
        
        self.progress_label = ttk.Label(progress_frame, text="대기 중...")
        self.progress_label.pack(pady=2)
        Tooltip(self.progress_label, "번역 작업의 현재 상태 및 진행 상황을 텍스트로 표시합니다.")

    # ========== 파일 브라우저 메서드 ==========
    
    def _browse_service_account_file(self) -> None:
        """서비스 계정 JSON 파일 선택"""
        filepath = filedialog.askopenfilename(
            title="서비스 계정 JSON 파일 선택",
            filetypes=(("JSON 파일", "*.json"), ("모든 파일", "*.*"))
        )
        if filepath and self.service_account_file_entry:
            self.service_account_file_entry.delete(0, tk.END)
            self.service_account_file_entry.insert(0, filepath)
    
    def _browse_input_files(self) -> None:
        """입력 파일 선택"""
        filepaths = filedialog.askopenfilenames(
            title="입력 파일 선택",
            filetypes=(("텍스트 파일", "*.txt"), ("모든 파일", "*.*"))
        )
        if filepaths and self.input_file_listbox:
            for filepath in filepaths:
                if filepath not in self.input_file_listbox.get(0, tk.END):
                    self.input_file_listbox.insert(tk.END, filepath)
            # 첫 번째 파일을 기준으로 출력 경로 제안
            self._propose_paths_from_first_input()
    
    def _propose_paths_from_first_input(self) -> None:
        """첫 번째 입력 파일을 기반으로 출력 경로 제안"""
        if not self.input_file_listbox or self.input_file_listbox.size() == 0:
            return
            
        first_file = self.input_file_listbox.get(0)
        p = Path(first_file)
        
        # 출력 파일 경로 제안
        suggested_output = p.parent / f"{p.stem}_translated{p.suffix}"
        if self.output_file_entry:
            self.output_file_entry.delete(0, tk.END)
            self.output_file_entry.insert(0, str(suggested_output))
        
        # 용어집 경로 제안 (콜백이 있으면 호출)
        if self.app_service and self._on_glossary_path_changed:
            suffix = self.app_service.config.get('glossary_output_json_filename_suffix', '_glossary.json')
            suggested_glossary = p.parent / f"{p.stem}{suffix}"
            self._on_glossary_path_changed(str(suggested_glossary))
    
    def _remove_selected_files(self) -> None:
        """선택된 파일 목록에서 제거"""
        if not self.input_file_listbox:
            return
        selected_indices = self.input_file_listbox.curselection()
        # 인덱스가 큰 것부터 삭제해야 순서가 꼬이지 않음
        for index in reversed(selected_indices):
            self.input_file_listbox.delete(index)
    
    def _browse_output_file(self) -> None:
        """출력 파일 선택"""
        filepath = filedialog.asksaveasfilename(
            title="출력 파일 선택",
            defaultextension=".txt",
            filetypes=(("텍스트 파일", "*.txt"), ("모든 파일", "*.*"))
        )
        if filepath and self.output_file_entry:
            self.output_file_entry.delete(0, tk.END)
            self.output_file_entry.insert(0, filepath)

    # ========== 모델 관련 메서드 ==========
    
    def _update_model_list_ui(self) -> None:
        """모델 목록 UI 업데이트"""
        if not self.app_service:
            messagebox.showerror("오류", "AppService가 초기화되지 않았습니다.")
            self.log_message("모델 목록 업데이트 시도 실패: AppService 없음", "ERROR")
            return

        # 현재 사용자 입력 모델명 저장
        current_user_input_model = self.model_name_combobox.get() if self.model_name_combobox else ""
        
        try:
            self.log_message("모델 목록 새로고침 중...")
            
            # 클라이언트 유무 확인
            if not self.app_service.gemini_client:
                self.log_message(
                    "모델 목록 업데이트: Gemini 클라이언트가 초기화되지 않았습니다. "
                    "API 키 또는 Vertex AI 설정을 확인하고 '설정 저장' 후 다시 시도해주세요.", "WARNING"
                )
                messagebox.showwarning("인증 필요", 
                                       "모델 목록을 가져오려면 API 키 또는 Vertex AI 설정이 유효해야 합니다.\n"
                                       "설정을 확인하고 '설정 저장' 버튼을 누른 후 다시 시도해주세요.")
                self._reset_model_combobox(current_user_input_model)
                return
            
            # 모델 목록 조회
            models_data = self.app_service.get_available_models()
            
            # UI 모델 목록 구성
            model_display_names_for_ui = []
            for m in models_data:
                display_name = m.get("display_name")
                short_name = m.get("short_name")
                full_name = m.get("name")
                
                # 우선순위: short_name > display_name > full_name
                chosen_name_for_display = short_name or display_name or full_name
                
                if chosen_name_for_display and isinstance(chosen_name_for_display, str) and chosen_name_for_display.strip():
                    model_display_names_for_ui.append(chosen_name_for_display.strip())
            
            model_display_names_for_ui = sorted(list(set(model_display_names_for_ui)))
            
            if self.model_name_combobox:
                self.model_name_combobox['values'] = model_display_names_for_ui
            
            # 최적 모델 선택
            self._set_optimal_model_selection(current_user_input_model, model_display_names_for_ui)
            
            self.log_message(f"{len(model_display_names_for_ui)}개 모델 로드 완료.")

        except Exception as e:
            messagebox.showerror("오류", f"모델 목록 조회 중 오류: {e}")
            self.log_message(f"모델 목록 조회 중 오류: {e}", "ERROR", exc_info=True)
            self._reset_model_combobox(current_user_input_model)
    
    def _reset_model_combobox(self, current_user_input_model: str = "") -> None:
        """모델 콤보박스 초기화"""
        if self.model_name_combobox:
            self.model_name_combobox['values'] = []
            self.model_name_combobox.set(current_user_input_model)
    
    def _set_optimal_model_selection(self, current_user_input_model: str, model_display_names_for_ui: List[str]) -> None:
        """최적의 모델 자동 선택"""
        if not self.model_name_combobox:
            return
            
        config_model_name = self.app_service.config.get("model_name", "") if self.app_service else ""
        config_model_short_name = config_model_name.split('/')[-1] if '/' in config_model_name else config_model_name

        # 우선순위에 따른 모델 선택
        if current_user_input_model and current_user_input_model.strip() in model_display_names_for_ui:
            self.model_name_combobox.set(current_user_input_model)
        elif config_model_short_name and config_model_short_name in model_display_names_for_ui:
            self.model_name_combobox.set(config_model_short_name)
        elif config_model_name and config_model_name in model_display_names_for_ui:
            self.model_name_combobox.set(config_model_name)
        elif model_display_names_for_ui:
            self.model_name_combobox.set(model_display_names_for_ui[0])
        else:
            self.model_name_combobox.set("")
    
    def _on_api_key_changed(self, event=None) -> None:
        """API 키 변경 이벤트 핸들러"""
        # 다음 모델 새로고침 시 자동으로 재초기화되도록 플래그 설정
        self._client_needs_refresh = True
    
    def _toggle_vertex_fields(self) -> None:
        """Vertex AI 필드 토글"""
        if not self.use_vertex_ai_var:
            return
            
        use_vertex = self.use_vertex_ai_var.get()
        self.log_message(f"_toggle_vertex_fields 호출됨. use_vertex_ai_var: {use_vertex}", "DEBUG")
        
        api_related_state = tk.DISABLED if use_vertex else tk.NORMAL
        vertex_related_state = tk.NORMAL if use_vertex else tk.DISABLED

        # API 키 관련 필드
        if self.api_keys_label:
            self.api_keys_label.config(state=api_related_state)
        if self.api_keys_text:
            self.api_keys_text.config(state=api_related_state)
        
        # Vertex AI 관련 필드
        if self.service_account_file_label:
            self.service_account_file_label.config(state=vertex_related_state)
        if self.service_account_file_entry:
            self.service_account_file_entry.config(state=vertex_related_state)
        if self.browse_sa_file_button:
            self.browse_sa_file_button.config(state=vertex_related_state)
        if self.gcp_project_label:
            self.gcp_project_label.config(state=vertex_related_state)
        if self.gcp_project_entry:
            self.gcp_project_entry.config(state=vertex_related_state)
        if self.gcp_location_label:
            self.gcp_location_label.config(state=vertex_related_state)
        if self.gcp_location_entry:
            self.gcp_location_entry.config(state=vertex_related_state)
            
        self.log_message(f"Vertex 필드 상태: {vertex_related_state}, API 키 필드 상태: {api_related_state}", "DEBUG")

    # ========== 번역 제어 메서드 ==========
    
    def _start_translation_thread(self, retranslate_failed_only: bool = False) -> None:
        """번역 스레드 시작"""
        if not self.app_service:
            messagebox.showerror("오류", "애플리케이션 서비스가 초기화되지 않았습니다.")
            return

        input_files = self.get_input_files()
        if not input_files:
            messagebox.showwarning("경고", "입력 파일을 하나 이상 추가해주세요.")
            return

        self.stop_requested = False

        # Run the sequential translation in a separate thread
        self._translation_thread = threading.Thread(
            target=self._run_multiple_translations_sequentially,
            args=(input_files, retranslate_failed_only),
            daemon=True
        )
        self._translation_thread.start()
    
    def _start_translation_thread_with_resume_check(self) -> None:
        """이어하기 확인 후 번역 스레드 시작"""
        self._start_translation_thread(retranslate_failed_only=False)
    
    def _start_failed_chunks_translation_thread(self) -> None:
        """실패 청크 재번역 스레드 시작"""
        self._start_translation_thread(retranslate_failed_only=True)
    
    def _run_multiple_translations_sequentially(
        self, 
        input_files: List[str], 
        retranslate_failed_only: bool = False
    ) -> None:
        """
        입력 파일 목록을 순회하며 하나씩 번역을 실행하고, 모든 작업 완료 후 알림을 표시합니다.
        """
        # 부모 윈도우 참조 가져오기
        master = self._get_master_window()
        if not master:
            return
            
        # 작업 시작 시 버튼 상태 업데이트
        master.after(0, lambda: self.set_buttons_state(True))

        if not self.app_service:
            master.after(0, lambda: self.set_buttons_state(False))
            return

        total_files = len(input_files)
        completed_files = []
        failed_files = []

        # Apply UI settings before starting
        try:
            current_ui_config = self.get_config()
            self.app_service.load_app_config(runtime_overrides=current_ui_config)
            if not self.app_service.gemini_client:
                if not messagebox.askyesno("API 설정 경고", "API 클라이언트가 초기화되지 않았습니다.(인증 정보 확인 필요)\n계속 진행하시겠습니까?"):
                    master.after(0, lambda: self.set_buttons_state(False))
                    return
        except Exception as e:
            messagebox.showerror("오류", f"번역 시작 전 설정 오류: {e}")
            master.after(0, lambda: self.set_buttons_state(False))
            return

        for i, input_file in enumerate(input_files):
            if self.stop_requested:
                self.log_message("사용자 요청으로 다중 파일 번역 작업을 중단합니다.")
                break

            self.log_message(f"=== 파일 {i+1}/{total_files} 번역 시작: {Path(input_file).name} ===")
            master.after(0, lambda file=input_file: 
                self.update_progress_label(f"{Path(file).name} 번역 준비 중..."))

            p = Path(input_file)
            output_file = p.parent / f"{p.stem}_translated{p.suffix}"

            translation_done_event = threading.Event()
            translation_status = {"message": ""}

            def translation_finished_callback(message: str):
                """Callback to be invoked when translation is finished, stopped, or has an error."""
                if "완료" in message or "오류" in message or "중단" in message:
                    translation_status["message"] = message
                    translation_done_event.set()

            # TQDM 스트림 가져오기
            tqdm_stream = self._get_tqdm_stream() if self._get_tqdm_stream else None

            self.app_service.start_translation(
                input_file_path=input_file,
                output_file_path=str(output_file),
                progress_callback=self._update_translation_progress,
                status_callback=translation_finished_callback,
                tqdm_file_stream=tqdm_stream,
                retranslate_failed_only=retranslate_failed_only
            )

            translation_done_event.wait()  # Wait for the translation of the current file to complete

            if "오류" in translation_status["message"] or "중단" in translation_status["message"]:
                failed_files.append(Path(input_file).name)
            else:
                completed_files.append(Path(input_file).name)

            self.log_message(f"=== 파일 {i+1}/{total_files} 처리 완료: {Path(input_file).name} ===")

        # After all files are processed, show a final summary notification.
        final_title = "배치 번역 완료"
        final_message = f"총 {total_files}개 파일 중 {len(completed_files)}개 성공, {len(failed_files)}개 실패/중단.\n\n"
        if completed_files:
            final_message += f"성공:\n- " + "\n- ".join(completed_files)
        if failed_files:
            final_message += f"\n\n실패/중단:\n- " + "\n- ".join(failed_files)

        master.after(0, lambda: self._show_completion_notification(final_title, final_message))
        master.after(0, lambda: self.update_progress_label("모든 파일 작업 완료."))
        master.after(0, lambda: self.set_buttons_state(False))
    
    def _request_stop_translation(self) -> None:
        """번역 중지 요청"""
        if not self.app_service:
            return
        if self.app_service.is_translation_running:
            self.stop_requested = True  # GUI 레벨의 중지 플래그 설정
            self.app_service.request_stop_translation()
            self.log_message("번역 중지 요청됨.")
            if self.stop_button:
                self.stop_button.config(state=tk.DISABLED)  # 중지 버튼 비활성화
        else:
            self.log_message("실행 중인 번역 작업이 없습니다.")
    
    def _update_translation_progress(self, progress_dto) -> None:
        """번역 진행률 업데이트"""
        master = self._get_master_window()
        if not master:
            return
            
        def _update():
            if not master.winfo_exists():
                return
            if self.progress_bar and hasattr(progress_dto, 'processed_chunks') and hasattr(progress_dto, 'total_chunks'):
                progress_value = (progress_dto.processed_chunks / progress_dto.total_chunks) * 100 if progress_dto.total_chunks > 0 else 0
                self.progress_bar['value'] = progress_value
                
            status_text = f"{progress_dto.current_status_message} ({progress_dto.processed_chunks}/{progress_dto.total_chunks})"
            if hasattr(progress_dto, 'failed_chunks') and progress_dto.failed_chunks > 0:
                status_text += f" - 실패: {progress_dto.failed_chunks}"
            if hasattr(progress_dto, 'last_error_message') and progress_dto.last_error_message:
                status_text += f" (마지막 오류: {progress_dto.last_error_message[:30]}...)"
            self.update_progress_label(status_text)
            
        master.after(0, _update)
    
    def _update_translation_status(self, message: str) -> None:
        """번역 상태 업데이트"""
        master = self._get_master_window()
        if not master:
            return
            
        def _update():
            self.log_message(f"번역 상태: {message}")
            if "번역 시작됨" in message or "번역 중..." in message or "처리 중" in message or "준비 중" in message:
                self.set_buttons_state(True)
            elif "오류" in message or "중단" in message:
                self.set_buttons_state(False)
                
        master.after(0, _update)
    
    def _show_completion_notification(self, title: str, message: str) -> None:
        """번역 완료 알림 표시"""
        try:
            messagebox.showinfo(title, message)
        except Exception as e:
            self.log_message(f"번역 완료 알림 표시 중 오류: {e}", "ERROR")
    
    def _get_master_window(self) -> Optional[tk.Tk]:
        """
        최상위 윈도우 참조를 반환합니다.
        
        Returns:
            Tk 윈도우 인스턴스 또는 None
        """
        try:
            # parent에서 최상위 윈도우 찾기
            widget = self.parent
            while widget:
                if isinstance(widget, tk.Tk):
                    return widget
                widget = widget.master if hasattr(widget, 'master') else None
            return None
        except Exception:
            return None

    # ========== 설정 관리 메서드 ==========
    
    def _save_settings(self) -> None:
        """현재 설정 저장"""
        if not self.app_service:
            messagebox.showerror("오류", "AppService가 초기화되지 않았습니다.")
            return
        try:
            current_config = self.app_service.config.copy()
            ui_config = self.get_config()
            current_config.update(ui_config)
            self.app_service.save_app_config(current_config)
            messagebox.showinfo("성공", "설정이 성공적으로 저장되었습니다.")
            self.log_message("설정 저장됨.")
            # 저장 후 UI 다시 로드
            self.load_config(self.app_service.config)
        except ValueError as ve: 
            messagebox.showerror("입력 오류", f"설정값 오류: {ve}")
            self.log_message(f"설정값 입력 오류: {ve}", "ERROR")
        except Exception as e:
            messagebox.showerror("오류", f"설정 저장 중 예상치 못한 오류: {e}")
            self.log_message(f"설정 저장 중 예상치 못한 오류: {e}", "ERROR", exc_info=True)
    
    def _load_settings_ui(self) -> None:
        """설정 불러오기 UI"""
        if not self.app_service:
            messagebox.showerror("오류", "AppService가 초기화되지 않았습니다.")
            return
        try:
            self.app_service.load_app_config()
            self.load_config(self.app_service.config)
            messagebox.showinfo("성공", "설정을 성공적으로 불러왔습니다.")
            self.log_message("설정 불러옴.")
        except Exception as e:
            messagebox.showerror("오류", f"설정 불러오기 중 예상치 못한 오류: {e}")
            self.log_message(f"설정 불러오기 중 오류: {e}", "ERROR", exc_info=True)

    def get_config(self) -> Dict[str, Any]:
        """
        현재 UI 상태에서 설정값을 추출합니다.
        
        Returns:
            설정값 딕셔너리
        """
        # 프롬프트 내용
        prompt_content = ""
        if self.prompt_text:
            prompt_content = self.prompt_text.get("1.0", tk.END).strip()
        
        # 프리필 시스템 지침
        prefill_system_instruction_content = ""
        if self.prefill_system_instruction_text:
            prefill_system_instruction_content = self.prefill_system_instruction_text.get("1.0", tk.END).strip()
        
        # Vertex AI 사용 여부
        use_vertex = self.use_vertex_ai_var.get() if self.use_vertex_ai_var else False

        # API 키 목록
        api_keys_list = []
        if self.api_keys_text:
            api_keys_str = self.api_keys_text.get("1.0", tk.END).strip()
            api_keys_list = [key.strip() for key in api_keys_str.splitlines() if key.strip()]
       
        # 최대 작업자 수 유효성 검사
        max_workers_val = os.cpu_count() or 1
        if self.max_workers_entry:
            try:
                max_workers_val = int(self.max_workers_entry.get())
                if max_workers_val <= 0:
                    max_workers_val = os.cpu_count() or 1 
                    messagebox.showwarning("입력 오류", f"최대 작업자 수는 1 이상이어야 합니다. 기본값 ({max_workers_val})으로 설정됩니다.")
                    self.max_workers_entry.delete(0, tk.END)
                    self.max_workers_entry.insert(0, str(max_workers_val))
            except ValueError:
                max_workers_val = os.cpu_count() or 1 
                messagebox.showwarning("입력 오류", f"최대 작업자 수는 숫자여야 합니다. 기본값 ({max_workers_val})으로 설정됩니다.")
                self.max_workers_entry.delete(0, tk.END)
                self.max_workers_entry.insert(0, str(max_workers_val))

        # RPM 유효성 검사
        rpm_val = 60.0
        if self.rpm_entry:
            try:
                rpm_val = float(self.rpm_entry.get() or "60.0")
                if rpm_val < 0:
                    rpm_val = 0.0  # 0은 제한 없음, 음수는 0으로
            except ValueError:
                rpm_val = 60.0
                messagebox.showwarning("입력 오류", f"분당 요청 수는 숫자여야 합니다. 기본값 ({rpm_val})으로 설정됩니다.")
                self.rpm_entry.delete(0, tk.END)
                self.rpm_entry.insert(0, str(rpm_val))

        # 프리필 캐시된 히스토리 JSON 파싱
        prefill_cached_history_obj = []
        if self.prefill_cached_history_text:
            prefill_cached_history_json_str = self.prefill_cached_history_text.get("1.0", tk.END).strip()
            if prefill_cached_history_json_str:
                try:
                    prefill_cached_history_obj = json.loads(prefill_cached_history_json_str)
                    if not isinstance(prefill_cached_history_obj, list):
                        messagebox.showwarning("입력 오류", "프리필 캐시된 히스토리는 JSON 배열이어야 합니다. 기본값 []으로 설정됩니다.")
                        prefill_cached_history_obj = []
                except json.JSONDecodeError:
                    messagebox.showwarning("입력 오류", "프리필 캐시된 히스토리 형식이 잘못되었습니다 (JSON 파싱 실패). 기본값 []으로 설정됩니다.")
                    prefill_cached_history_obj = []

        # Thinking Budget 유효성 검사
        thinking_budget_ui_val: Optional[int] = None
        if self.thinking_budget_entry:
            thinking_budget_str = self.thinking_budget_entry.get().strip()
            if thinking_budget_str:
                try:
                    thinking_budget_ui_val = int(thinking_budget_str)
                except ValueError:
                    messagebox.showwarning("입력 오류", f"Thinking Budget은 숫자여야 합니다. '{thinking_budget_str}'은(는) 유효하지 않습니다. 이 값은 무시됩니다.")
                    self.thinking_budget_entry.delete(0, tk.END)
                    thinking_budget_ui_val = None

        # 용어집 경로 가져오기 (콜백 사용 또는 None)
        glossary_path = None
        if self._get_glossary_path:
            glossary_path = self._get_glossary_path()

        # 설정 딕셔너리 구성
        config_data: Dict[str, Any] = {
            # API 설정
            "api_keys": api_keys_list if not use_vertex else [],
            "service_account_file_path": self.service_account_file_entry.get().strip() if use_vertex and self.service_account_file_entry else None,
            "use_vertex_ai": use_vertex,
            "gcp_project": self.gcp_project_entry.get().strip() if use_vertex and self.gcp_project_entry else None,
            "gcp_location": self.gcp_location_entry.get().strip() if use_vertex and self.gcp_location_entry else None,
            "model_name": self.model_name_combobox.get().strip() if self.model_name_combobox else "",
            
            # 생성 파라미터
            "temperature": self.temperature_scale.get() if self.temperature_scale else 0.7,
            "top_p": self.top_p_scale.get() if self.top_p_scale else 0.9,
            "thinking_budget": thinking_budget_ui_val,
            
            # 파일 및 처리 설정
            "chunk_size": int(self.chunk_size_entry.get() or "6000") if self.chunk_size_entry else 6000,
            "max_workers": max_workers_val, 
            "requests_per_minute": rpm_val,
            
            # 언어 설정
            "novel_language": self.novel_language_entry.get().strip() or "auto" if self.novel_language_entry else "auto",
            "novel_language_fallback": self.novel_language_fallback_entry.get().strip() or "ja" if self.novel_language_fallback_entry else "ja",
            
            # 프롬프트 설정
            "prompts": prompt_content,
            
            # 프리필 설정
            "enable_prefill_translation": self.enable_prefill_var.get() if self.enable_prefill_var else False,
            "prefill_system_instruction": prefill_system_instruction_content,
            "prefill_cached_history": prefill_cached_history_obj,
            
            # 용어집 설정 (GlossaryTab에서 관리하지만 경로는 여기서도 참조 가능)
            "glossary_json_path": glossary_path,
            
            # 콘텐츠 안전 재시도 설정
            "use_content_safety_retry": self.use_content_safety_retry_var.get() if self.use_content_safety_retry_var else True,
            "max_content_safety_split_attempts": int(self.max_split_attempts_entry.get() or "3") if self.max_split_attempts_entry else 3,
            "min_content_safety_chunk_size": int(self.min_chunk_size_entry.get() or "100") if self.min_chunk_size_entry else 100,
        }
        
        return config_data
    
    def load_config(self, config: Dict[str, Any]) -> None:
        """
        설정값을 UI에 반영합니다.
        
        Args:
            config: 적용할 설정값 딕셔너리
        """
        try:
            self.log_message("UI 설정 로드 시작", "DEBUG")
            
            # API 키 목록
            if self.api_keys_text:
                self.api_keys_text.config(state=tk.NORMAL)
                self.api_keys_text.delete('1.0', tk.END)
                api_keys_list = config.get("api_keys", [])
                if api_keys_list:
                    self.api_keys_text.insert('1.0', "\n".join(api_keys_list))
            
            # 서비스 계정 파일
            if self.service_account_file_entry:
                self.service_account_file_entry.delete(0, tk.END)
                sa_file_path = config.get("service_account_file_path")
                self.service_account_file_entry.insert(0, sa_file_path if sa_file_path is not None else "")

            # Vertex AI 사용 여부
            if self.use_vertex_ai_var:
                use_vertex_ai_val = config.get("use_vertex_ai", False)
                self.use_vertex_ai_var.set(use_vertex_ai_val)
            
            # GCP 프로젝트
            if self.gcp_project_entry:
                self.gcp_project_entry.delete(0, tk.END)
                gcp_project_val = config.get("gcp_project")
                self.gcp_project_entry.insert(0, gcp_project_val if gcp_project_val is not None else "")

            # GCP 위치
            if self.gcp_location_entry:
                self.gcp_location_entry.delete(0, tk.END)
                gcp_location_val = config.get("gcp_location")
                self.gcp_location_entry.insert(0, gcp_location_val if gcp_location_val is not None else "")

            # Vertex AI 필드 토글
            self._toggle_vertex_fields()
            
            # 모델 이름
            if self.model_name_combobox:
                model_name_from_config = config.get("model_name", "gemini-2.0-flash")
                self.model_name_combobox.set(model_name_from_config)

            # Temperature
            if self.temperature_scale and self.temperature_label:
                temperature_val = config.get("temperature", 0.7)
                try:
                    self.temperature_scale.set(float(temperature_val))
                    self.temperature_label.config(text=f"{self.temperature_scale.get():.2f}")
                except (ValueError, TypeError):
                    self.temperature_scale.set(0.7)
                    self.temperature_label.config(text="0.70")

            # Top P
            if self.top_p_scale and self.top_p_label:
                top_p_val = config.get("top_p", 0.9)
                try:
                    self.top_p_scale.set(float(top_p_val))
                    self.top_p_label.config(text=f"{self.top_p_scale.get():.2f}")
                except (ValueError, TypeError):
                    self.top_p_scale.set(0.9)
                    self.top_p_label.config(text="0.90")

            # Thinking Budget
            if self.thinking_budget_entry:
                thinking_budget_val = config.get("thinking_budget")
                self.thinking_budget_entry.delete(0, tk.END)
                if thinking_budget_val is not None:
                    self.thinking_budget_entry.insert(0, str(thinking_budget_val))

            # 청크 크기
            if self.chunk_size_entry:
                chunk_size_val = config.get("chunk_size", 6000)
                self.chunk_size_entry.delete(0, tk.END)
                self.chunk_size_entry.insert(0, str(chunk_size_val))
            
            # 최대 작업자 수
            if self.max_workers_entry:
                max_workers_val = config.get("max_workers", os.cpu_count() or 1)
                self.max_workers_entry.delete(0, tk.END)
                self.max_workers_entry.insert(0, str(max_workers_val))

            # RPM
            if self.rpm_entry:
                rpm_val = config.get("requests_per_minute", 60)
                self.rpm_entry.delete(0, tk.END)
                self.rpm_entry.insert(0, str(rpm_val))

            # 언어 설정
            if self.novel_language_entry:
                novel_lang_val = config.get("novel_language", "auto")
                self.novel_language_entry.delete(0, tk.END)
                self.novel_language_entry.insert(0, novel_lang_val)

            if self.novel_language_fallback_entry:
                novel_lang_fallback_val = config.get("novel_language_fallback", "ja")
                self.novel_language_fallback_entry.delete(0, tk.END)
                self.novel_language_fallback_entry.insert(0, novel_lang_fallback_val)

            # 프롬프트
            if self.prompt_text:
                prompts_val = config.get("prompts", "")
                self.prompt_text.delete('1.0', tk.END)
                if isinstance(prompts_val, str):
                    self.prompt_text.insert('1.0', prompts_val)
                elif isinstance(prompts_val, (list, tuple)) and prompts_val:
                    self.prompt_text.insert('1.0', str(prompts_val[0]))

            # 프리필 설정
            if self.enable_prefill_var:
                self.enable_prefill_var.set(config.get("enable_prefill_translation", False))
            
            if self.prefill_system_instruction_text:
                prefill_system_instruction_val = config.get("prefill_system_instruction", "")
                self.prefill_system_instruction_text.delete('1.0', tk.END)
                self.prefill_system_instruction_text.insert('1.0', prefill_system_instruction_val)

            if self.prefill_cached_history_text:
                prefill_cached_history_obj = config.get("prefill_cached_history", [])
                try:
                    prefill_cached_history_json_str = json.dumps(prefill_cached_history_obj, indent=2, ensure_ascii=False)
                except TypeError:
                    prefill_cached_history_json_str = "[]"
                self.prefill_cached_history_text.delete('1.0', tk.END)
                self.prefill_cached_history_text.insert('1.0', prefill_cached_history_json_str)

            # 콘텐츠 안전 재시도 설정
            if self.use_content_safety_retry_var:
                use_content_safety_retry_val = config.get("use_content_safety_retry", True)
                self.use_content_safety_retry_var.set(use_content_safety_retry_val)

            if self.max_split_attempts_entry:
                max_split_attempts_val = config.get("max_content_safety_split_attempts", 3)
                self.max_split_attempts_entry.delete(0, tk.END)
                self.max_split_attempts_entry.insert(0, str(max_split_attempts_val))

            if self.min_chunk_size_entry:
                min_chunk_size_val = config.get("min_content_safety_chunk_size", 100)
                self.min_chunk_size_entry.delete(0, tk.END)
                self.min_chunk_size_entry.insert(0, str(min_chunk_size_val))
            
            self.log_message("UI에 설정 로드 완료.", "DEBUG")
            
        except Exception as e:
            messagebox.showerror("오류", f"설정 UI 반영 중 예상치 못한 오류: {e}")
            self.log_message(f"설정 UI 반영 중 오류: {e}", "ERROR", exc_info=True)

    # ========== 유틸리티 메서드 ==========
    
    def get_input_files(self) -> List[str]:
        """
        현재 입력 파일 목록을 반환합니다.
        
        Returns:
            입력 파일 경로 목록
        """
        if self.input_file_listbox:
            return list(self.input_file_listbox.get(0, tk.END))
        return []
    
    def get_chunk_size(self) -> int:
        """
        현재 청크 크기를 반환합니다.
        
        Returns:
            청크 크기 (기본값: 6000)
        """
        try:
            if self.chunk_size_entry:
                return int(self.chunk_size_entry.get())
        except ValueError:
            pass
        return 6000
    
    def set_buttons_state(self, translation_running: bool) -> None:
        """
        번역 실행 상태에 따라 버튼 상태를 설정합니다.
        
        Args:
            translation_running: 번역이 실행 중인지 여부
        """
        if translation_running:
            if self.start_button:
                self.start_button.config(state=tk.DISABLED)
            if self.retry_failed_button:
                self.retry_failed_button.config(state=tk.DISABLED)
            if self.stop_button:
                self.stop_button.config(state=tk.NORMAL)
        else:
            if self.start_button:
                self.start_button.config(state=tk.NORMAL)
            if self.retry_failed_button:
                self.retry_failed_button.config(state=tk.NORMAL)
            if self.stop_button:
                self.stop_button.config(state=tk.DISABLED)
    
    def update_progress(self, value: int, maximum: int = 100) -> None:
        """
        진행률 바를 업데이트합니다.
        
        Args:
            value: 현재 값
            maximum: 최대 값
        """
        if self.progress_bar:
            self.progress_bar.config(maximum=maximum, value=value)
    
    def update_progress_label(self, text: str) -> None:
        """
        진행률 레이블을 업데이트합니다.
        
        Args:
            text: 표시할 텍스트
        """
        if self.progress_label:
            self.progress_label.config(text=text)
