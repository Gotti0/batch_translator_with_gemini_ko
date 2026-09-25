"""
PySide6 Settings Tab (minimal async-enabled)
- Input/Output file selection
- Start/Cancel translation using AppService async APIs
- Progress/status display
"""

from __future__ import annotations

import asyncio
import copy
import time
from pathlib import Path
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets
from qasync import asyncSlot

from core.dtos import TranslationJobProgressDTO
from core.config.config_manager import DEFAULT_REQUESTS_PER_MINUTE
from infrastructure.logger_config import setup_logger
from gui_qt.components_qt.tooltip_qt import TooltipQt
from gui_qt.config_coordinator import ConfigCoordinator
from infrastructure.reasoning_options import DEFAULT_CHOICE_LABEL, reasoning_spec_for

logger = setup_logger(__name__)
from gui_qt.components_qt.mode_card import ModeSelectorGroup
from gui_qt.dialogs_qt.prefill_history_editor_qt import PrefillHistoryEditorDialogQt


# 프로바이더별 API 키 설정 필드. Antigravity CLI는 키를 쓰지 않아 저장하지 않는다.
PROVIDER_KEY_FIELDS = {
    "gemini": "api_keys",
    "claude_cli": "claude_cli_api_key",
    "codex_cli": "codex_cli_api_key",
    "openai_compatible": "openai_compatible_api_key",
    "ollama": "ollama_api_key",
}

# 프로바이더별 CLI 실행 경로 설정 필드와 기본 커맨드 이름
CLI_PATH_FIELDS = {
    "claude_cli": ("claude_cli_path", "claude"),
    "codex_cli": ("codex_cli_path", "codex"),
    "antigravity_cli": ("antigravity_cli_path", "agy"),
}

# "추론 강도" 행을 쓰는 프로바이더. Gemini는 기존 Thinking Level/Budget 행을 쓴다.
EFFORT_PROVIDERS = ("claude_cli", "codex_cli", "antigravity_cli", "ollama", "openai_compatible")


class NoWheelSpinBox(QtWidgets.QSpinBox):
    """QSpinBox that ignores wheel events when not focused"""
    def __init__(self, parent=None):
        super().__init__(parent)
        # 마우스 호버로 포커스를 받지 않도록 설정 (클릭/탭 키만 허용)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class NoWheelDoubleSpinBox(QtWidgets.QDoubleSpinBox):
    """QDoubleSpinBox that ignores wheel events when not focused"""
    def __init__(self, parent=None):
        super().__init__(parent)
        # 마우스 호버로 포커스를 받지 않도록 설정 (클릭/탭 키만 허용)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class NoWheelSlider(QtWidgets.QSlider):
    """QSlider that ignores wheel events unless focused to keep scroll usability"""
    def __init__(self, orientation: QtCore.Qt.Orientation, parent=None):
        super().__init__(orientation, parent)
        # 클릭/탭 시에만 포커스, 호버만으로는 포커스되지 않도록
        self.setFocusPolicy(QtCore.Qt.ClickFocus)

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class NoWheelComboBox(QtWidgets.QComboBox):
    """QComboBox that ignores wheel events unless focused"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(QtCore.Qt.ClickFocus)

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        if self.hasFocus():
            super().wheelEvent(event)
        else:
            event.ignore()


class ResizablePlainTextEdit(QtWidgets.QWidget):
    """QPlainTextEdit with a resize grip in the bottom-right corner"""
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        
        # Main text edit
        self.text_edit = QtWidgets.QPlainTextEdit()
        self.text_edit.setMinimumHeight(100)
        layout.addWidget(self.text_edit)
        
        # Resize handle
        self._resize_handle = QtWidgets.QLabel("⋰")
        self._resize_handle.setAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignBottom)
        self._resize_handle.setStyleSheet("QLabel { color: gray; font-size: 16px; padding: 2px; }")
        self._resize_handle.setFixedHeight(20)
        self._resize_handle.setCursor(QtCore.Qt.SizeFDiagCursor)
        self._resize_handle.setMouseTracking(True)
        layout.addWidget(self._resize_handle)
        
        self._resizing = False
        self._resize_start_pos = None
        self._resize_start_height = None
        
        # Install event filter on resize handle
        self._resize_handle.installEventFilter(self)
        
    def eventFilter(self, obj, event):
        if obj == self._resize_handle:
            if event.type() == QtCore.QEvent.MouseButtonPress:
                if event.button() == QtCore.Qt.LeftButton:
                    self._resizing = True
                    self._resize_start_pos = event.globalPosition().toPoint()
                    self._resize_start_height = self.height()
                    return True
            elif event.type() == QtCore.QEvent.MouseMove:
                if self._resizing:
                    delta = event.globalPosition().toPoint().y() - self._resize_start_pos.y()
                    new_height = max(120, self._resize_start_height + delta)
                    # Use setMinimumHeight to preserve the height without fixing it
                    self.setMinimumHeight(new_height)
                    self.updateGeometry()
                    return True
            elif event.type() == QtCore.QEvent.MouseButtonRelease:
                if event.button() == QtCore.Qt.LeftButton and self._resizing:
                    self._resizing = False
                    return True
        return super().eventFilter(obj, event)
        
    def setPlaceholderText(self, text: str) -> None:
        self.text_edit.setPlaceholderText(text)
        
    def toPlainText(self) -> str:
        return self.text_edit.toPlainText()
        
    def setPlainText(self, text: str) -> None:
        self.text_edit.setPlainText(text)


class MemoryGardenDialog(QtWidgets.QDialog):
    """기억뜰 보기: 인물 가지(메모·마지막 등장·호박 여부)와 최근 자동 흡수 기록."""

    def __init__(self, overview: dict, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("기억뜰")
        self.resize(820, 520)
        layout = QtWidgets.QVBoxLayout(self)
        s = overview.get("summary", {})
        layout.addWidget(QtWidgets.QLabel(
            f"용어(캐논) {s.get('canon', 0)} · 인물 {s.get('entity', 0)} · 번역 문단 {s.get('episode', 0)} · "
            f"연결 {s.get('edges', 0)} · 호박 속 {s.get('cold', 0)}"
        ))
        self.table = QtWidgets.QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["이름", "번역", "메모 (말투·호칭)", "마지막 등장", "원문 닻"])
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        for e in overview.get("entities", []):
            row = self.table.rowCount()
            self.table.insertRow(row)
            name = e["name"] + (f" ({', '.join(e['aliases'])})" if e.get("aliases") else "")
            last = "호박 속" if e.get("cold") else (f"청크 {e['last_chunk'] + 1}" if e.get("last_chunk", -1) >= 0 else "")
            anchor = e["evidence"][-1]["excerpt"] if e.get("evidence") else ""
            for col, value in enumerate([name, e.get("translated", ""), e.get("note", ""), last, anchor]):
                self.table.setItem(row, col, QtWidgets.QTableWidgetItem(value))
        self.table.resizeColumnsToContents()
        layout.addWidget(self.table, 1)
        merges = overview.get("merge_log", [])
        if merges:
            layout.addWidget(QtWidgets.QLabel(
                "최근 자동 흡수: " + " · ".join(f"{m['absorbed']} → {m['into']}" for m in merges[:10])
            ))
        close = QtWidgets.QPushButton("닫기")
        close.clicked.connect(self.accept)
        layout.addWidget(close, 0, QtCore.Qt.AlignRight)


class SettingsTabQt(QtWidgets.QWidget):
    """Minimal PySide6 settings tab to drive async translation"""

    progress_signal = QtCore.Signal(object)  # TranslationJobProgressDTO
    status_signal = QtCore.Signal(str)
    completion_signal = QtCore.Signal(bool, str, dict)  # success, message, stats_dict

    def __init__(self, app_service, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.app_service = app_service
        self._loop = asyncio.get_event_loop()
        self.prefill_history = []
        self._model_cache: list[str] = []
        self._tqdm_stream = None  # LogTab에서 주입받을 TQDM 스트림
        self._translation_start_time = None  # ETA 계산용 시작 시간
        self._translation_start_chunks = 0  # 번역 시작 시점의 이미 처리된 청크 수 (이어하기 대응)
        self._total_chunks = 0  # 완료 통계용: 총 청크 수
        self._final_processed_chunks = 0  # 완료 통계용: 최종 처리 청크 수
        # 키·CLI 경로·추론 강도 입력칸은 하나씩이지만 값은 프로바이더별로 따로 보관한다
        # (다른 회사 키나 다른 CLI의 실행 경로가 섞여 쓰이지 않도록)
        self._provider_key_text: dict[str, str] = {}
        self._cli_path_text: dict[str, str] = {}
        self._effort_value: dict[str, Optional[str]] = {}
        self._shown_provider: Optional[str] = None  # 지금 입력칸에 값이 보이는 프로바이더
        # 저장은 조정자 한 곳에서 한다. 메인 창이 모든 탭을 묶은 조정자로 교체한다.
        self._config_coordinator = ConfigCoordinator(app_service, [self])

        self._build_ui()
        self._wire_signals()
        self._load_config()

    def set_tqdm_stream(self, tqdm_stream) -> None:
        """LogTab에서 TQDM 스트림 주입"""
        self._tqdm_stream = tqdm_stream

    def _build_ui(self) -> None:
        # 메인 레이아웃에 스크롤 영역 추가
        main_layout = QtWidgets.QVBoxLayout(self)
        scroll_area = QtWidgets.QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        
        # 스크롤 가능한 컨텐츠 위젯
        scroll_content = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(scroll_content)

        # --- API/모델 섹션 ---
        api_group = QtWidgets.QGroupBox("API / 모델")
        self.api_form = QtWidgets.QFormLayout(api_group)

        self.provider_combo = NoWheelComboBox()
        self.provider_combo.addItem("Google Gemini API (클라우드)", "gemini")
        self.provider_combo.addItem("Google Antigravity CLI (로컬 CLI - agy.exe)", "antigravity_cli")
        self.provider_combo.addItem("Anthropic Claude (로컬 CLI - claude.exe)", "claude_cli")
        self.provider_combo.addItem("OpenAI Codex / GPT (로컬 CLI - codex.exe)", "codex_cli")
        self.provider_combo.addItem("Ollama (로컬 LLM 서버)", "ollama")
        self.provider_combo.addItem("OpenAI 호환 API (DeepSeek, vLLM 등)", "openai_compatible")
        TooltipQt(
            self.provider_combo,
            "번역에 사용할 AI 공급자를 선택합니다.\n"
            "로컬 CLI를 선택하면 별도 API 키 없이 기존 구독(Claude Pro/ChatGPT Plus) 세션으로 번역할 수 있습니다.",
        )

        self.auth_check_btn = QtWidgets.QPushButton("인증 / 연결 테스트")
        TooltipQt(
            self.auth_check_btn,
            "선택한 AI 공급자의 로그인 세션(Claude Code/ChatGPT Plus) 또는 API 키의 유효성을 사전에 테스트합니다.",
        )
        provider_row = QtWidgets.QHBoxLayout()
        provider_row.addWidget(self.provider_combo, 1)
        provider_row.addWidget(self.auth_check_btn)

        self.cli_path_edit = QtWidgets.QLineEdit("claude")
        TooltipQt(self.cli_path_edit, "로컬 CLI 실행 파일 경로 또는 커맨드 이름입니다 (예: claude 또는 codex).")

        self.base_url_edit = QtWidgets.QLineEdit("https://api.openai.com/v1/chat/completions")
        TooltipQt(
            self.base_url_edit,
            "OpenAI 호환 API: 채팅 완성 엔드포인트 전체 URL (예: https://api.deepseek.com/v1/chat/completions)\n"
            "Ollama: 서버 주소 (예: http://localhost:11434)",
        )

        self.ollama_num_ctx_spin = NoWheelSpinBox()
        self.ollama_num_ctx_spin.setRange(0, 1048576)
        self.ollama_num_ctx_spin.setSingleStep(2048)
        self.ollama_num_ctx_spin.setValue(16384)
        self.ollama_num_ctx_spin.setSpecialValueText("서버 기본값")
        TooltipQt(
            self.ollama_num_ctx_spin,
            "Ollama 컨텍스트 길이(토큰)입니다.\n"
            "Ollama 기본값(2K~4K)은 번역 프롬프트보다 짧아 앞부분이 경고 없이 잘립니다.\n"
            "청크 크기 6,000자 기준 16384 이상을 권장하며, 클수록 VRAM을 더 사용합니다.",
        )

        self.api_keys_edit = QtWidgets.QPlainTextEdit()
        self.api_keys_edit.setPlaceholderText("API 키를 줄바꿈으로 구분하여 입력")
        TooltipQt(self.api_keys_edit, "API 키를 줄바꿈으로 구분하여 입력합니다.\n여러 키를 사용하면 로테이션됩니다.")
        self.use_vertex_check = QtWidgets.QCheckBox("Vertex AI 사용")
        TooltipQt(self.use_vertex_check, "Google Cloud Vertex AI를 사용하여 API를 호출합니다.\n서비스 계정 JSON 파일이 필요합니다.")
        self.sa_path_edit = QtWidgets.QLineEdit()
        TooltipQt(self.sa_path_edit, "Vertex AI 서비스 계정 JSON 파일 경로입니다.")
        sa_browse = QtWidgets.QPushButton("찾기")
        TooltipQt(sa_browse, "서비스 계정 JSON 파일을 선택합니다.")
        sa_browse.clicked.connect(self._browse_sa)
        sa_row = QtWidgets.QHBoxLayout()
        sa_row.addWidget(self.sa_path_edit)
        sa_row.addWidget(sa_browse)
        self.sa_row_widget = self._wrap(sa_row)
        self.gcp_project_edit = QtWidgets.QLineEdit()
        TooltipQt(self.gcp_project_edit, "GCP 프로젝트 ID를 입력합니다.")
        self.gcp_location_edit = QtWidgets.QLineEdit()
        TooltipQt(self.gcp_location_edit, "GCP 리전을 입력합니다 (예: us-central1).")

        # 모델 콤보 (editable) - 기본 후보 + 사용자 입력 유지
        self.model_name_combo = NoWheelComboBox()
        self.model_name_combo.setEditable(True)
        self.model_name_combo.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self.model_name_combo.addItems([
            "gemini-2.0-flash",
            "gemini-2.0-pro",
            "gemini-2.5-pro",
            "gemini-3-pro-preview",
            "gemini-3-flash-preview",
        ])
        TooltipQt(self.model_name_combo, "번역에 사용할 모델을 선택하거나 직접 입력합니다.")

        self.model_refresh_btn = QtWidgets.QPushButton("모델 목록 새로고침")
        TooltipQt(self.model_refresh_btn, "API 또는 CLI에서 사용 가능한 모델 목록을 불러옵니다.")
        self.model_progress = QtWidgets.QProgressBar()
        self.model_progress.setRange(0, 0)
        self.model_progress.setTextVisible(False)
        self.model_progress.setFixedHeight(10)
        self.model_progress.setVisible(False)
        model_row = QtWidgets.QHBoxLayout()
        model_row.addWidget(self.model_name_combo)
        model_row.addWidget(self.model_refresh_btn)
        model_row.addWidget(self.model_progress)

        self.api_form.addRow("AI 프로바이더", self._wrap(provider_row))
        self.api_form.addRow("CLI 실행 경로", self.cli_path_edit)
        self.api_form.addRow("API Base URL", self.base_url_edit)
        self.api_form.addRow("컨텍스트 길이", self.ollama_num_ctx_spin)
        self.api_form.addRow("API 키 목록", self.api_keys_edit)
        self.api_form.addRow("Vertex AI", self.use_vertex_check)
        self.api_form.addRow("서비스 계정 JSON", self.sa_row_widget)
        self.api_form.addRow("GCP 프로젝트", self.gcp_project_edit)
        self.api_form.addRow("GCP 위치", self.gcp_location_edit)
        self.api_form.addRow("모델 이름", self._wrap(model_row))

        # --- 번역 모드 섹션 (신규 Phase 2) ---
        mode_group = QtWidgets.QGroupBox("번역 파이프라인 모드")
        mode_vbox = QtWidgets.QVBoxLayout(mode_group)
        self.mode_selector = ModeSelectorGroup(self)
        mode_vbox.addWidget(self.mode_selector)
        
        # --- 생성 파라미터 ---
        gen_group = QtWidgets.QGroupBox("생성 파라미터")
        gen_form = QtWidgets.QFormLayout(gen_group)

        # Temperature (0.0 ~ 2.0)
        self.temperature_slider = NoWheelSlider(QtCore.Qt.Horizontal)
        self.temperature_slider.setRange(0, 200)
        self.temperature_slider.setValue(70)
        TooltipQt(self.temperature_slider, "모델 출력의 무작위성을 조절합니다 (0.0 ~ 2.0).\n낮을수록 일관적이고 예측 가능하며, 높을수록 창의적이지만 예측 불가능합니다.\n번역에는 0.5~1.0 권장.")
        self.temperature_label = QtWidgets.QLabel("0.70")
        self.temperature_label.setMinimumWidth(40)
        TooltipQt(self.temperature_label, "현재 설정된 Temperature 값입니다.")
        self.temperature_slider.valueChanged.connect(
            lambda v: self.temperature_label.setText(f"{v/100:.2f}")
        )
        temp_row = QtWidgets.QHBoxLayout()
        temp_row.addWidget(self.temperature_slider)
        temp_row.addWidget(self.temperature_label)

        # Top P (0.0 ~ 1.0)
        self.top_p_slider = NoWheelSlider(QtCore.Qt.Horizontal)
        self.top_p_slider.setRange(0, 100)
        self.top_p_slider.setValue(90)
        TooltipQt(self.top_p_slider, "Nucleus Sampling 파라미터입니다 (0.0 ~ 1.0).\n다음 토큰 선택 시 고려할 확률 누적 범위를 설정합니다.\n0.9는 상위 90% 확률의 토큰만 고려한다는 의미입니다.")
        self.top_p_label = QtWidgets.QLabel("0.90")
        self.top_p_label.setMinimumWidth(40)
        TooltipQt(self.top_p_label, "현재 설정된 Top P 값입니다.")
        self.top_p_slider.valueChanged.connect(
            lambda v: self.top_p_label.setText(f"{v/100:.2f}")
        )
        top_p_row = QtWidgets.QHBoxLayout()
        top_p_row.addWidget(self.top_p_slider)
        top_p_row.addWidget(self.top_p_label)

        # Thinking Budget (-1 ~ 32000)
        self.thinking_budget_slider = NoWheelSlider(QtCore.Qt.Horizontal)
        self.thinking_budget_slider.setRange(-1, 32000)
        self.thinking_budget_slider.setSingleStep(128)
        self.thinking_budget_slider.setValue(-1)
        TooltipQt(self.thinking_budget_slider, "Gemini 2.5 전용 파라미터입니다.\n복잡한 추론에 사용할 토큰 예산을 설정합니다.\n-1: 비활성화, 양수: 사고에 사용할 최대 토큰 수.\n값이 클수록 더 깊은 추론이 가능하지만 비용이 증가합니다.")
        self.thinking_budget_label = QtWidgets.QLabel("-1 (비활성)")
        self.thinking_budget_label.setMinimumWidth(80)
        TooltipQt(self.thinking_budget_label, "현재 설정된 Thinking Budget 값입니다.")
        self.thinking_budget_slider.valueChanged.connect(
            lambda v: self.thinking_budget_label.setText(
                "-1 (비활성)" if v == -1 else str(v)
            )
        )
        budget_row = QtWidgets.QHBoxLayout()
        budget_row.addWidget(self.thinking_budget_slider)
        budget_row.addWidget(self.thinking_budget_label)

        self.thinking_level_combo = NoWheelComboBox()
        self.thinking_level_combo.addItems(["low", "high"])
        TooltipQt(self.thinking_level_combo, "Gemini 3 전용 파라미터입니다.\n모델의 추론 깊이 수준을 설정합니다.\nminimal/low/medium/high (Flash는 4단계, Pro는 2단계).\n높을수록 더 신중하게 추론하지만 응답 시간이 길어집니다.")

        self.reasoning_effort_combo = NoWheelComboBox()
        TooltipQt(
            self.reasoning_effort_combo,
            "CLI/로컬 프로바이더의 추론 강도입니다.\n"
            "'기본값'이면 옵션을 넘기지 않고 CLI/서버 설정을 따릅니다.\n"
            "높을수록 더 깊이 추론하지만 느려지고 사용량이 늘어납니다.",
        )

        self.gen_form = gen_form
        self.thinking_budget_row = self._wrap(budget_row)
        gen_form.addRow("Temperature", self._wrap(temp_row))
        gen_form.addRow("Top P", self._wrap(top_p_row))
        gen_form.addRow("Thinking Budget", self.thinking_budget_row)
        gen_form.addRow("Thinking Level", self.thinking_level_combo)
        gen_form.addRow("추론 강도", self.reasoning_effort_combo)

        # --- 파일/처리 설정 ---
        file_group = QtWidgets.QGroupBox("파일 / 처리")
        file_form = QtWidgets.QFormLayout(file_group)

        self.input_edit = QtWidgets.QLineEdit()
        TooltipQt(self.input_edit, "번역할 입력 파일의 경로입니다.")
        self.output_edit = QtWidgets.QLineEdit()
        TooltipQt(self.output_edit, "번역 결과를 저장할 출력 파일의 경로입니다.")
        browse_in = QtWidgets.QPushButton("파일 선택")
        TooltipQt(browse_in, "입력 파일을 선택합니다.")
        browse_out = QtWidgets.QPushButton("출력 경로")
        TooltipQt(browse_out, "출력 파일 경로를 설정합니다.")
        browse_in.clicked.connect(self._browse_input)
        browse_out.clicked.connect(self._browse_output)

        in_row = QtWidgets.QHBoxLayout()
        in_row.addWidget(self.input_edit)
        in_row.addWidget(browse_in)
        out_row = QtWidgets.QHBoxLayout()
        out_row.addWidget(self.output_edit)
        out_row.addWidget(browse_out)

        self.chunk_size_spin = NoWheelSpinBox()
        self.chunk_size_spin.setRange(500, 100000)
        self.chunk_size_spin.setSingleStep(500)
        TooltipQt(self.chunk_size_spin, "텍스트를 분할하는 청크의 크기(문자 수)입니다.\n크면 API 호출이 줄지만 품질이 떨어질 수 있습니다.")
        self.max_workers_spin = NoWheelSpinBox()
        self.max_workers_spin.setRange(1, 64)
        TooltipQt(self.max_workers_spin, "동시에 처리할 최대 작업 수입니다.\nAPI 할당량에 따라 조절하세요.")
        self.rpm_spin = NoWheelDoubleSpinBox()
        self.rpm_spin.setRange(0, 2000)
        self.rpm_spin.setSingleStep(0.1)
        self.rpm_spin.setDecimals(1)
        TooltipQt(self.rpm_spin, "분당 최대 API 요청 수입니다.\n0으로 설정하면 제한 없음 (float 지원).")

        file_form.addRow("입력 파일", self._wrap(in_row))
        file_form.addRow("출력 파일", self._wrap(out_row))
        file_form.addRow("청크 크기", self.chunk_size_spin)
        file_form.addRow("동시 작업 수", self.max_workers_spin)
        file_form.addRow("분당 요청 수(RPM)", self.rpm_spin)

        # --- 언어 설정 ---
        lang_group = QtWidgets.QGroupBox("언어 설정")
        lang_form = QtWidgets.QFormLayout(lang_group)
        self.novel_lang_edit = QtWidgets.QLineEdit()
        TooltipQt(self.novel_lang_edit, "원문의 언어를 지정합니다 (예: English, Japanese).")
        self.novel_fallback_edit = QtWidgets.QLineEdit()
        TooltipQt(self.novel_fallback_edit, "언어 자동감지 실패 시 사용할 폴백 언어입니다.")
        lang_form.addRow("출발 언어", self.novel_lang_edit)
        lang_form.addRow("자동감지 실패 폴백", self.novel_fallback_edit)

        # --- 프롬프트 ---
        prompt_group = QtWidgets.QGroupBox("프롬프트")
        prompt_vbox = QtWidgets.QVBoxLayout(prompt_group)
        self.prompt_edit = ResizablePlainTextEdit()
        self.prompt_edit.setPlaceholderText("번역 프롬프트: {{slot}}과 {{glossary_context}} 지원")
        TooltipQt(self.prompt_edit, "번역 시 모델에 제공할 프롬프트입니다.\n{{slot}}에 텍스트가, {{glossary_context}}에 용어집이 삽입됩니다.")
        prompt_vbox.addWidget(self.prompt_edit)

        # --- 프리필 ---
        prefill_group = QtWidgets.QGroupBox("프리필(Prefill)")
        prefill_vbox = QtWidgets.QVBoxLayout(prefill_group)
        self.enable_prefill_check = QtWidgets.QCheckBox("프리필 번역 사용")
        TooltipQt(self.enable_prefill_check, "프리필 모드를 활성화하여 모델엔 예시를 제공합니다.")
        self.prefill_system_edit = QtWidgets.QPlainTextEdit()
        self.prefill_system_edit.setPlaceholderText("시스템 지침")
        TooltipQt(self.prefill_system_edit, "프리필 모드에서 사용할 시스템 지침입니다.")
        self.edit_history_btn = QtWidgets.QPushButton("프리필 히스토리 편집 (준비 중)")
        TooltipQt(self.edit_history_btn, "프리필에 사용할 예시 대화 히스토리를 편집합니다.")
        self.edit_history_btn.setEnabled(True)
        self.edit_history_btn.clicked.connect(self._open_prefill_history_dialog)
        prefill_vbox.addWidget(self.enable_prefill_check)
        prefill_vbox.addWidget(self.prefill_system_edit)
        prefill_vbox.addWidget(self.edit_history_btn)

        # --- 콘텐츠 안전 ---
        safety_group = QtWidgets.QGroupBox("콘텐츠 안전 재시도")
        safety_form = QtWidgets.QFormLayout(safety_group)
        self.use_content_safety_check = QtWidgets.QCheckBox("검열 오류 시 청크 분할 재시도")
        TooltipQt(
            self.use_content_safety_check,
            "콘텐츠 안전 오류 발생 시 청크를 분할하여 재시도합니다.\n"
            "무결성 모드에서는 모델이 JSON을 제대로 돌려주지 못한 경우도 같은 스위치로 묶입니다.\n"
            "끄면 분할 없이 해당 청크의 원문이 그대로 남습니다.",
        )
        self.max_split_spin = NoWheelSpinBox()
        self.max_split_spin.setRange(1, 10)
        TooltipQt(
            self.max_split_spin,
            "최대 분할 시도 횟수입니다.\n"
            "무결성 모드에서는 청크를 쪼갤 최대 깊이로 쓰입니다.\n"
            "한 단계마다 조각 수가 두 배가 되므로 요청 수도 함께 늘어납니다.",
        )
        self.min_chunk_spin = NoWheelSpinBox()
        self.min_chunk_spin.setRange(50, 5000)
        self.min_chunk_spin.setSingleStep(50)
        TooltipQt(
            self.min_chunk_spin,
            "분할 시 최소 청크 크기입니다(글자 수).\n"
            "무결성 모드에서는 청크에 담긴 본문 길이의 합으로 잽니다.\n"
            "이 크기보다 작아지면 더 쪼개지 않고 원문을 남깁니다.",
        )
        safety_form.addRow(self.use_content_safety_check)
        safety_form.addRow("최대 분할 시도", self.max_split_spin)
        safety_form.addRow("최소 청크 크기", self.min_chunk_spin)

        # --- PageFold 토큰 최적화 (PDF 압축) ---
        # --- 번역 장기기억 (Voyage 임베딩) ---
        self.memory_group = QtWidgets.QGroupBox("번역 장기기억 (Voyage 임베딩)")
        memory_form = QtWidgets.QFormLayout(self.memory_group)
        self.enable_memory_check = QtWidgets.QCheckBox("번역 기억 사용")
        TooltipQt(
            self.enable_memory_check,
            "앞에서 번역한 비슷한 문단을 예시로 넣어 호칭·말투·용어를 일관되게 유지합니다.\n"
            "번역 시작 전에 원문 전체와 용어집을 Voyage AI로 임베딩합니다 (원문이 Voyage로 전송됩니다).\n"
            "결과는 입력 파일 옆 '<파일명>_memory' 폴더에 저장되어 이어하기 때 다시 임베딩하지 않습니다.",
        )
        self.voyage_key_edit = QtWidgets.QLineEdit()
        self.voyage_key_edit.setEchoMode(QtWidgets.QLineEdit.Password)
        self.voyage_key_edit.setPlaceholderText("Voyage API 키 (pa-...)")
        self.voyage_model_combo = NoWheelComboBox()
        self.voyage_model_combo.setEditable(True)
        self.voyage_model_combo.addItems(["voyage-4-lite", "voyage-4", "voyage-4-large"])
        TooltipQt(self.voyage_model_combo, "voyage-4-lite: 가장 저렴 (1M 토큰당 $0.02).\n4 시리즈끼리는 벡터가 호환됩니다. 모델을 바꾸면 기억을 새로 만듭니다.")
        self.memory_top_k_spin = NoWheelSpinBox()
        self.memory_top_k_spin.setRange(0, 10)
        self.memory_top_k_spin.setValue(3)
        TooltipQt(self.memory_top_k_spin, "청크마다 넣을 번역 예시 수입니다. 늘릴수록 번역 요청의 입력 토큰이 늘어납니다.")
        self.memory_min_sim_spin = NoWheelDoubleSpinBox()
        self.memory_min_sim_spin.setRange(0.0, 1.0)
        self.memory_min_sim_spin.setSingleStep(0.05)
        self.memory_min_sim_spin.setDecimals(2)
        self.memory_min_sim_spin.setValue(0.55)
        TooltipQt(self.memory_min_sim_spin, "이보다 유사도가 낮은 문단은 예시로 쓰지 않습니다.")
        self.memory_depth_combo = NoWheelComboBox()
        self.memory_depth_combo.addItem("빠르게 — 직접 나온 이름만", "fast")
        self.memory_depth_combo.addItem("무난하게 — 강한 연결은 멀리도", "balanced")
        self.memory_depth_combo.addItem("깊게 — 가라앉은 기억까지", "deep")
        self.memory_depth_combo.setCurrentIndex(1)
        TooltipQt(
            self.memory_depth_combo,
            "연상 확산 깊이입니다. 원문에 나온 이름·용어에서 연결된 기억으로 얼마나 멀리 퍼질지 정합니다.\n"
            "오래 등장하지 않은 기억은 '호박 속'으로 가라앉으며, 깊게를 고르면 약한 연결로도 다시 떠오릅니다.",
        )
        self.memory_extract_check = QtWidgets.QCheckBox("인물 메모 자동 추출 (청크마다 LLM 호출 1회 추가)")
        TooltipQt(
            self.memory_extract_check,
            "번역이 끝난 청크에서 인물의 말투·호칭·1인칭·관계를 뽑아 기억에 넣습니다.\n"
            "번역과 별도로 뒤에서 실행되어 번역 속도를 늦추지 않습니다. 저렴한 모델을 지정하는 것을 권장합니다.",
        )
        self.memory_extract_model_edit = QtWidgets.QLineEdit()
        self.memory_extract_model_edit.setPlaceholderText("추출 모델 (비우면 번역 모델 사용)")
        self.memory_view_btn = QtWidgets.QPushButton("기억뜰 보기")
        TooltipQt(self.memory_view_btn, "현재 입력 파일의 기억뜰(인물 메모, 머지 기록)을 봅니다.")
        self.memory_test_btn = QtWidgets.QPushButton("Voyage 연결 테스트")
        memory_key_row = QtWidgets.QHBoxLayout()
        memory_key_row.addWidget(self.voyage_key_edit, 1)
        memory_key_row.addWidget(self.memory_test_btn)
        memory_form.addRow(self.enable_memory_check)
        memory_form.addRow("Voyage API 키", self._wrap(memory_key_row))
        memory_form.addRow("임베딩 모델", self.voyage_model_combo)
        memory_form.addRow("예시 수", self.memory_top_k_spin)
        memory_form.addRow("최소 유사도", self.memory_min_sim_spin)
        memory_form.addRow("연상 깊이", self.memory_depth_combo)
        memory_form.addRow(self.memory_extract_check)
        memory_form.addRow("추출 모델", self.memory_extract_model_edit)
        memory_form.addRow("", self.memory_view_btn)

        pagefold_group = QtWidgets.QGroupBox("PageFold 토큰 최적화")
        pagefold_form = QtWidgets.QFormLayout(pagefold_group)

        self.enable_pagefold_check = QtWidgets.QCheckBox("PageFold 활성화 (긴 컨텍스트/용어집 PDF 압축)")
        TooltipQt(
            self.enable_pagefold_check,
            "Gemini의 네이티브 PDF 문서 처리 특성을 활용하여 긴 용어집 및 컨텍스트를\n"
            "초소형 폰트 PDF로 패키징해 입력 토큰 비용을 최대 90% 이상 절감합니다.",
        )

        self.pagefold_mode_combo = NoWheelComboBox()
        self.pagefold_mode_combo.addItem("용어집/설정 참조 모드 (Reference - 권장)", "reference")
        self.pagefold_mode_combo.addItem("특정 태그 <pdf> 모드 (Marked Tags)", "marked")
        self.pagefold_mode_combo.addItem("원문 청크 전체 모드 (Chunk - 실험적)", "chunk")
        TooltipQt(
            self.pagefold_mode_combo,
            "Reference: 방대한 용어집과 세계관 지침을 PDF로 접어 고정 ~280 토큰으로 참조합니다.\n"
            "Marked Tags: 프롬프트 내 <pdf>...</pdf> 태그 안의 텍스트만 PDF로 변환합니다.\n"
            "Chunk: 원문 번역 청크 자체를 PDF로 전달합니다 (문장 누락 주의).",
        )

        self.pagefold_font_size_spin = NoWheelDoubleSpinBox()
        self.pagefold_font_size_spin.setRange(0.5, 12.0)
        self.pagefold_font_size_spin.setSingleStep(0.5)
        self.pagefold_font_size_spin.setDecimals(1)
        self.pagefold_font_size_spin.setValue(1.0)
        TooltipQt(
            self.pagefold_font_size_spin,
            "PDF 렌더링 시 사용할 폰트 크기(pt)입니다 (기본값: 1.0pt).\n"
            "작을수록 한 페이지에 더 많은 텍스트가 압축되어 토큰을 절약할 수 있습니다.",
        )

        pagefold_form.addRow(self.enable_pagefold_check)
        pagefold_form.addRow("작동 모드", self.pagefold_mode_combo)
        pagefold_form.addRow("폰트 크기(pt)", self.pagefold_font_size_spin)

        # --- 배치 작업 (배치 번역 모드 전용) ---
        self.batch_group = QtWidgets.QGroupBox("배치 작업")
        batch_vbox = QtWidgets.QVBoxLayout(self.batch_group)
        self.batch_key_edit = QtWidgets.QLineEdit()
        self.batch_key_edit.setEchoMode(QtWidgets.QLineEdit.Password)
        self.batch_key_edit.setPlaceholderText("선택 사항: 비우면 API 키 목록의 첫 키로 제출")
        TooltipQt(
            self.batch_key_edit,
            "배치 제출에 쓸 API 키입니다. 무료 티어 키는 Batch API를 사용할 수 없으므로 결제가 설정된 유료 키를 입력하세요.\n"
            "작업은 제출한 키로만 조회되므로, 진행 중에는 이 키를 바꾸거나 지우지 마세요.",
        )
        batch_key_row = QtWidgets.QFormLayout()
        batch_key_row.addRow("배치용 API 키 (유료)", self.batch_key_edit)
        self.batch_status_label = QtWidgets.QLabel("제출한 배치 작업이 없습니다.")
        self.batch_status_label.setWordWrap(True)
        self.batch_jobs_table = QtWidgets.QTableWidget(0, 5)
        self.batch_jobs_table.setHorizontalHeaderLabels(["작업", "청크", "상태", "제출 후", "결과"])
        self.batch_jobs_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.batch_jobs_table.verticalHeader().setVisible(False)
        self.batch_jobs_table.horizontalHeader().setStretchLastSection(True)
        self.batch_jobs_table.setMaximumHeight(160)
        self.batch_refresh_btn = QtWidgets.QPushButton("지금 확인")
        TooltipQt(self.batch_refresh_btn, "배치 작업 상태를 바로 조회하고, 끝난 작업의 결과를 가져옵니다.")
        self.batch_cancel_btn = QtWidgets.QPushButton("작업 취소")
        TooltipQt(self.batch_cancel_btn, "진행 중인 배치 작업을 취소합니다. 이미 끝난 결과는 가져옵니다.")
        self.batch_realtime_btn = QtWidgets.QPushButton("실시간으로 마무리")
        TooltipQt(self.batch_realtime_btn, "미완료 청크를 표준 모드로 바로 번역합니다.\n검열로 빠진 청크는 분할 재시도가 있는 이 방법을 권장합니다.")
        self.batch_resubmit_btn = QtWidgets.QPushButton("다시 배치 제출")
        TooltipQt(self.batch_resubmit_btn, "미완료 청크만 모아 배치로 다시 제출합니다 (비용 50%, 최대 24시간).")
        self.batch_keep_btn = QtWidgets.QPushButton("그대로 저장")
        TooltipQt(self.batch_keep_btn, "미완료 청크는 실패 표시와 원문으로 채워 최종 파일을 저장합니다.")
        batch_btn_row = QtWidgets.QHBoxLayout()
        for btn in (self.batch_refresh_btn, self.batch_cancel_btn, self.batch_realtime_btn,
                    self.batch_resubmit_btn, self.batch_keep_btn):
            batch_btn_row.addWidget(btn)
        batch_vbox.addLayout(batch_key_row)
        batch_vbox.addWidget(self.batch_status_label)
        batch_vbox.addWidget(self.batch_jobs_table)
        batch_vbox.addLayout(batch_btn_row)
        self.batch_group.setVisible(False)

        # 앱이 켜져 있는 동안 진행 중인 배치 작업을 주기적으로 조회한다
        self._batch_timer = QtCore.QTimer(self)
        self._batch_busy = False

        # --- 액션/진행 표시 ---
        self.start_btn = QtWidgets.QPushButton("번역 시작")
        TooltipQt(self.start_btn, "현재 설정으로 번역을 시작합니다.")
        self.cancel_btn = QtWidgets.QPushButton("취소")
        TooltipQt(self.cancel_btn, "진행 중인 번역 작업을 취소합니다.")
        self.cancel_btn.setEnabled(False)

        # 번역 시작/취소 묶음. 동작과 상태는 이 탭이 관리하고, 메인 창이 탭 영역 밖으로 옮겨 배치한다.
        self.action_bar = QtWidgets.QWidget()
        btn_row = QtWidgets.QHBoxLayout(self.action_bar)
        btn_row.setContentsMargins(0, 0, 0, 0)
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.cancel_btn)

        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.status_label = QtWidgets.QLabel("대기 중")

        # 전체 배치
        layout.addWidget(api_group)
        layout.addWidget(mode_group)
        layout.addWidget(gen_group)
        layout.addWidget(file_group)
        layout.addWidget(lang_group)
        layout.addWidget(prompt_group)
        layout.addWidget(prefill_group)
        layout.addWidget(safety_group)
        layout.addWidget(pagefold_group)
        layout.addWidget(self.memory_group)
        layout.addWidget(self.batch_group)
        layout.addWidget(self.action_bar)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.status_label)
        layout.addStretch(1)
        
        # 스크롤 영역에 컨텐츠 설정
        scroll_area.setWidget(scroll_content)
        main_layout.addWidget(scroll_area)

    def _wire_signals(self) -> None:
        self.start_btn.clicked.connect(self._on_start_clicked)
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        self.progress_signal.connect(self._on_progress)
        self.status_signal.connect(self._on_status)
        self.completion_signal.connect(self._on_completion, QtCore.Qt.QueuedConnection)

        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        self.use_vertex_check.stateChanged.connect(self._on_vertex_toggle)
        self.model_name_combo.currentTextChanged.connect(self._on_model_changed)
        self.model_name_combo.editTextChanged.connect(self._on_model_changed)
        self.model_refresh_btn.clicked.connect(self._refresh_model_list)
        self.auth_check_btn.clicked.connect(self._on_auth_check_clicked)
        self.mode_selector.mode_changed.connect(self._on_mode_changed)
        self.enable_pagefold_check.toggled.connect(self._on_pagefold_toggled)
        self.enable_memory_check.toggled.connect(self._on_memory_toggled)
        self.memory_test_btn.clicked.connect(self._on_memory_test_clicked)
        self.memory_extract_check.toggled.connect(lambda _: self._on_memory_toggled(self.enable_memory_check.isChecked()))
        self.memory_view_btn.clicked.connect(self._on_memory_view_clicked)
        self.batch_refresh_btn.clicked.connect(self._on_batch_refresh_clicked)
        self.batch_cancel_btn.clicked.connect(self._on_batch_cancel_clicked)
        self.batch_realtime_btn.clicked.connect(self._on_batch_realtime_clicked)
        self.batch_resubmit_btn.clicked.connect(self._on_batch_resubmit_clicked)
        self.batch_keep_btn.clicked.connect(self._on_batch_keep_clicked)
        self._batch_timer.timeout.connect(self._on_batch_timer)
        self.input_edit.editingFinished.connect(self._update_batch_panel)

    def _swap_provider_fields_to(self, provider: str) -> None:
        """입력칸 값(API 키, CLI 경로, 추론 강도)을 이전 프로바이더 몫으로 보관하고 새 프로바이더의 값을 보여준다."""
        shown = self._shown_provider
        if shown is not None:
            self._provider_key_text[shown] = self.api_keys_edit.toPlainText()
            if shown in CLI_PATH_FIELDS:
                self._cli_path_text[shown] = self.cli_path_edit.text()
            if shown in EFFORT_PROVIDERS:
                self._effort_value[shown] = self.reasoning_effort_combo.currentData()
        if provider != shown:
            self.api_keys_edit.setPlainText(self._provider_key_text.get(provider, ""))
            if provider in CLI_PATH_FIELDS:
                self.cli_path_edit.setText(self._cli_path_text.get(provider, CLI_PATH_FIELDS[provider][1]))
            self._fill_effort_combo(provider)
        self._shown_provider = provider

    def _fill_effort_combo(self, provider: str) -> None:
        """추론 강도 선택 항목을 프로바이더 명세로 다시 채우고 보관된 값을 고른다."""
        spec = reasoning_spec_for(provider)
        combo = self.reasoning_effort_combo
        combo.blockSignals(True)
        combo.clear()
        if provider in EFFORT_PROVIDERS and spec.kind == "level":
            combo.addItem(DEFAULT_CHOICE_LABEL, None)
            for value, label in spec.choices:
                combo.addItem(label, value)
            stored = self._effort_value.get(provider)
            index = combo.findData(stored) if stored is not None else 0
            combo.setCurrentIndex(index if index >= 0 else 0)
        combo.blockSignals(False)

    def _api_keys_for(self, provider: str) -> list[str]:
        """해당 프로바이더 몫으로 입력된 키 목록 (지금 보이는 프로바이더면 입력칸에서 읽는다)."""
        if provider == self._shown_provider:
            text = self.api_keys_edit.toPlainText()
        else:
            text = self._provider_key_text.get(provider, "")
        return [line.strip() for line in text.splitlines() if line.strip()]

    def _cli_path_for(self, provider: str) -> str:
        """해당 CLI 프로바이더 몫의 실행 경로 (비어 있으면 기본 커맨드 이름)."""
        _field, default = CLI_PATH_FIELDS[provider]
        if provider == self._shown_provider:
            text = self.cli_path_edit.text()
        else:
            text = self._cli_path_text.get(provider, default)
        return text.strip() or default

    def _effort_for(self, provider: str) -> Optional[str]:
        """해당 프로바이더 몫의 추론 강도 (None이면 옵션을 넘기지 않음)."""
        if provider == self._shown_provider:
            value = self.reasoning_effort_combo.currentData()
        else:
            value = self._effort_value.get(provider)
        return reasoning_spec_for(provider).normalize(value)

    def _on_provider_changed(self, _=None) -> None:
        """AI 공급자 변경 시 입력 폼 표시 및 추천 모델 목록 조정"""
        provider = self.provider_combo.currentData() or "gemini"
        self._swap_provider_fields_to(provider)
        is_gemini = (provider == "gemini")
        is_claude = (provider == "claude_cli")
        is_codex = (provider == "codex_cli")
        is_agy = (provider == "antigravity_cli")
        is_openai_compat = (provider == "openai_compatible")
        is_ollama = (provider == "ollama")

        self.api_form.setRowVisible(self.cli_path_edit, is_claude or is_codex or is_agy)
        self.api_form.setRowVisible(self.base_url_edit, is_openai_compat or is_ollama)
        self.api_form.setRowVisible(self.ollama_num_ctx_spin, is_ollama)
        # Antigravity CLI는 로그인된 Google 계정 세션만 쓰고 키를 받지 않는다
        self.api_form.setRowVisible(self.api_keys_edit, provider in PROVIDER_KEY_FIELDS)
        self.api_form.setRowVisible(self.use_vertex_check, is_gemini)

        # 프로바이더별 API 키 안내문(Placeholder) 동적 전환
        if is_claude:
            self.api_keys_edit.setPlaceholderText("선택 사항: Claude Pro 세션 대신 별도 Anthropic API 키(sk-ant-...)를 사용하려면 입력 (비워두면 로컬 구독 세션 사용)")
        elif is_codex:
            self.api_keys_edit.setPlaceholderText("선택 사항: ChatGPT Plus 세션 대신 별도 OpenAI API 키(sk-...)를 사용하려면 입력 (비워두면 로컬 구독 세션 사용)")
        elif is_ollama:
            self.api_keys_edit.setPlaceholderText("선택 사항: 인증 프록시나 원격 Ollama에 Bearer 토큰이 필요할 때만 입력 (로컬 Ollama는 비워두세요)")
        elif is_openai_compat:
            self.api_keys_edit.setPlaceholderText("API 키 입력 (인증 불필요한 로컬 서버는 비워둘 수 있음)")
        else:
            self.api_keys_edit.setPlaceholderText("API 키를 줄바꿈으로 구분하여 입력 (여러 키 등록 시 자동 로테이션)")

        self._apply_vertex_state()

        # Base URL 설정 (CLI 경로는 프로바이더별 보관값으로 위에서 바뀐다)
        cfg = getattr(self.app_service, "config", {}) or {}
        if is_openai_compat:
            saved_url = cfg.get("openai_compatible_base_url", "https://api.openai.com/v1/chat/completions")
            current = self.base_url_edit.text().strip()
            if not current or current == (cfg.get("ollama_base_url") or "http://localhost:11434"):
                self.base_url_edit.setText(saved_url)
        elif is_ollama:
            saved_url = cfg.get("ollama_base_url") or "http://localhost:11434"
            current = self.base_url_edit.text().strip()
            if not current or current == cfg.get("openai_compatible_base_url", "https://api.openai.com/v1/chat/completions"):
                self.base_url_edit.setText(saved_url)

        # PageFold 토글 가능 여부 (Gemini만 지원)
        self._update_batch_availability()
        self.enable_pagefold_check.setEnabled(is_gemini)
        if not is_gemini:
            self.enable_pagefold_check.setChecked(False)

        # 모델 목록 추천 항목 갱신 및 캐시 초기화
        self._model_cache = None
        self.model_name_combo.clear()

        if is_gemini:
            models = ["gemini-2.0-flash", "gemini-2.0-pro", "gemini-2.5-pro", "gemini-3-pro-preview", "gemini-3-flash-preview"]
            target_model = cfg.get("model_name", "gemini-2.0-flash")
        elif is_claude:
            models = ["default", "claude-sonnet-5", "claude-opus-5-5", "claude-haiku-4-5-20251001", "claude-fable-5-1", "claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022", "claude-3-opus-20240229"]
            target_model = cfg.get("claude_cli_model", "default")
        elif is_codex:
            models = ["default", "gpt-5.5", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-6-astra", "o3", "o3-mini", "gpt-4o"]
            target_model = cfg.get("codex_cli_model", "gpt-5.5")
        elif is_agy:
            models = ["default", "gemini-3.8-flash-high", "gemini-3.8-flash-medium", "gemini-3.7-flash-high", "gemini-3.7-flash-medium", "gemini-3.1-pro-high", "claude-sonnet-4-6", "claude-opus-4-6-thinking", "gpt-oss-120b-medium"]
            target_model = cfg.get("antigravity_cli_model", "default")
        elif is_ollama:
            models = ["gemma3:12b", "gemma3:27b", "qwen3:14b", "qwen3:32b", "exaone3.5:7.8b", "exaone3.5:32b", "llama3.1:8b"]
            target_model = cfg.get("ollama_model") or ""
        else:
            models = ["default", "gpt-4o", "gpt-4o-mini", "deepseek-chat", "deepseek-reasoner"]
            target_model = cfg.get("openai_compatible_model", "default")

        self.model_name_combo.addItems(models)
        if target_model and target_model not in models:
            self.model_name_combo.addItem(target_model)
        if target_model:
            self.model_name_combo.setCurrentText(target_model)
        else:
            self.model_name_combo.setCurrentIndex(0)
        # 모델 이름이 그대로면 시그널이 나오지 않으므로 직접 반영
        self._apply_reasoning_ui()

    def _on_memory_toggled(self, checked: bool) -> None:
        for w in (self.voyage_key_edit, self.voyage_model_combo, self.memory_top_k_spin,
                  self.memory_min_sim_spin, self.memory_test_btn, self.memory_depth_combo,
                  self.memory_extract_check):
            w.setEnabled(checked)
        self.memory_extract_model_edit.setEnabled(checked and self.memory_extract_check.isChecked())

    def _on_memory_view_clicked(self) -> None:
        overview = None
        input_path = self.input_edit.text().strip()
        if input_path and hasattr(self.app_service, "get_memory_overview"):
            overview = self.app_service.get_memory_overview(input_path)
        if not overview:
            QtWidgets.QMessageBox.information(self, "기억뜰", "아직 이 파일의 기억뜰이 없습니다. 번역 기억을 켜고 번역하면 자라기 시작합니다.")
            return
        MemoryGardenDialog(overview, self).exec()

    @asyncSlot()
    async def _on_memory_test_clicked(self) -> None:
        self.memory_test_btn.setEnabled(False)
        try:
            cfg = {
                "embedding_provider": "voyage",
                "voyage_api_key": self.voyage_key_edit.text().strip(),
                "voyage_model": self.voyage_model_combo.currentText().strip() or "voyage-4-lite",
                "voyage_output_dimension": (getattr(self.app_service, "config", {}) or {}).get("voyage_output_dimension", 512),
            }
            ok, message = await self.app_service.check_embedding_health_async(cfg)
            box = QtWidgets.QMessageBox.information if ok else QtWidgets.QMessageBox.warning
            box(self, "Voyage 연결 테스트", message)
        finally:
            self.memory_test_btn.setEnabled(self.enable_memory_check.isChecked())

    def _on_pagefold_toggled(self, checked: bool) -> None:
        """PageFold 활성화 여부에 따라 세부 옵션 활성/비활성화"""
        self.pagefold_mode_combo.setEnabled(checked)
        self.pagefold_font_size_spin.setEnabled(checked)

    def _on_mode_changed(self, mode_id: str) -> None:
        """번역 모드 변경 시 설정 업데이트 및 UI 조정"""
        if self.app_service:
            self.app_service.config["translation_mode"] = mode_id
            logger.info(f"UI 번역 모드 변경됨: {mode_id}")
            
            # 모드에 따른 특수 UI 조정 (예: EPUB 모드일 때 청크 크기 숨기기 등)
            is_epub = mode_id == "epub"
            self.chunk_size_spin.setEnabled(not is_epub)
        self._update_batch_panel()
            # 무결성/EPUB 모드일 때 안전 재시도 옵션 강제 활성화 고려 가능

    def _wrap(self, layout: QtWidgets.QLayout) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        w.setLayout(layout)
        return w

    def _browse_input(self) -> None:
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "입력 파일 선택")
        if file_path:
            self.input_edit.setText(file_path)
            # 출력 기본값: 입력과 동일한 폴더/확장자 처리
            p = Path(file_path)
            candidate = str(p.parent / f"{p.stem}_translated{p.suffix}")
            self.output_edit.setText(candidate)
            # config dict에 즉시 반영 (Glossary 탭 등 다른 탭에서 바로 참조 가능)
            self.app_service.config["input_files"] = [file_path]
            self.app_service.config["output_file"] = candidate

    def _browse_output(self) -> None:
        file_path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "출력 파일 선택")
        if file_path:
            self.output_edit.setText(file_path)

    def _browse_sa(self) -> None:
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "서비스 계정 JSON 선택")
        if file_path:
            self.sa_path_edit.setText(file_path)

    def _progress_cb(self, dto: TranslationJobProgressDTO) -> None:
        # 콜백은 이벤트 루프 스레드에서 호출되므로 직접 emit
        self.progress_signal.emit(dto)

    def _status_cb(self, message: str) -> None:
        self.status_signal.emit(message)

    async def _run_translation(self, input_path: str, output_path: str, translation_mode_override: Optional[str] = None) -> None:
        try:
            await self.app_service.start_translation_async(
                input_file_path=input_path,
                output_file_path=output_path,
                progress_callback=self._progress_cb,
                status_callback=self._status_cb,
                tqdm_file_stream=self._tqdm_stream,
                retranslate_failed_only=False,
                translation_mode_override=translation_mode_override,
            )
            if (translation_mode_override or self.mode_selector.get_current_mode()) == "batch":
                # 배치는 제출·조회로 끝난다. 결과는 폴링이 수거하므로 완료 창을 띄우지 않는다.
                summary = self.app_service.get_batch_summary(input_path)
                message = summary.describe() if summary else "배치 작업이 없습니다."
                self.completion_signal.emit(True, message, {"batch": True})
                return
            # 완료 통계 계산
            elapsed = time.time() - self._translation_start_time if self._translation_start_time else 0
            actual_processed = self._final_processed_chunks - self._translation_start_chunks
            stats = {
                "success": True,
                "total_chunks": self._total_chunks,
                "processed_chunks": self._final_processed_chunks,
                "newly_processed": actual_processed,
                "elapsed_seconds": elapsed,
            }
            self.completion_signal.emit(True, "번역 완료", stats)
        except asyncio.CancelledError:
            elapsed = time.time() - self._translation_start_time if self._translation_start_time else 0
            stats = {
                "success": False,
                "total_chunks": self._total_chunks,
                "processed_chunks": self._final_processed_chunks,
                "newly_processed": self._final_processed_chunks - self._translation_start_chunks,
                "elapsed_seconds": elapsed,
                "reason": "사용자 취소",
            }
            self.completion_signal.emit(False, "취소됨", stats)
            raise
        except Exception as e:  # pragma: no cover - UI 표시 목적
            elapsed = time.time() - self._translation_start_time if self._translation_start_time else 0
            stats = {
                "success": False,
                "total_chunks": self._total_chunks,
                "processed_chunks": self._final_processed_chunks,
                "newly_processed": self._final_processed_chunks - self._translation_start_chunks,
                "elapsed_seconds": elapsed,
                "error": str(e),
            }
            self.completion_signal.emit(False, f"오류 발생", stats)

    def _load_config(self) -> None:
        cfg = getattr(self.app_service, "config", {}) or {}
        
        # ConfigManager로부터 기본값 가져오기 (Source of Truth)
        defaults = {}
        if hasattr(self.app_service, "config_manager"):
            defaults = self.app_service.config_manager.get_default_config()

        # 프로바이더별 키 보관함을 먼저 채운다. 입력칸에 남은 이전 내용은 버린다.
        gemini_keys = cfg.get("api_keys") or []
        self._provider_key_text = {
            "gemini": "\n".join(gemini_keys) if isinstance(gemini_keys, list) else str(gemini_keys),
        }
        for provider_id, field in PROVIDER_KEY_FIELDS.items():
            if provider_id != "gemini":
                self._provider_key_text[provider_id] = str(cfg.get(field) or "")
        self._cli_path_text = {
            provider_id: str(cfg.get(field) or defaults.get(field) or default)
            for provider_id, (field, default) in CLI_PATH_FIELDS.items()
        }
        self._effort_value = {
            provider_id: reasoning_spec_for(provider_id).normalize(cfg.get(reasoning_spec_for(provider_id).config_key))
            for provider_id in EFFORT_PROVIDERS
        }
        self._shown_provider = None

        # 프로바이더 로드
        provider = str(cfg.get("llm_provider", defaults.get("llm_provider", "gemini")))
        idx = self.provider_combo.findData(provider)
        if idx != -1:
            self.provider_combo.setCurrentIndex(idx)
        else:
            self.provider_combo.setCurrentIndex(0)
        # 같은 인덱스면 시그널이 나오지 않으므로 직접 반영
        self._swap_provider_fields_to(self.provider_combo.currentData() or "gemini")

        # Base URL 로드
        if provider == "ollama":
            self.base_url_edit.setText(str(cfg.get("ollama_base_url") or defaults.get("ollama_base_url") or "http://localhost:11434"))
        else:
            self.base_url_edit.setText(str(cfg.get("openai_compatible_base_url", defaults.get("openai_compatible_base_url", "https://api.openai.com/v1/chat/completions"))))
        try:
            self.ollama_num_ctx_spin.setValue(int(cfg.get("ollama_num_ctx", defaults.get("ollama_num_ctx", 16384)) or 0))
        except (TypeError, ValueError):
            self.ollama_num_ctx_spin.setValue(16384)

        # 입력/출력 파일 경로 로드
        input_files = cfg.get("input_files", []) or []
        self.input_edit.setText(input_files[0] if input_files else "")
        self.output_edit.setText(str(cfg.get("output_file", "") or ""))
        
        # 로드 중에는 "서비스 계정 필요" 안내가 뜨지 않도록 시그널을 막는다 (상태는 아래에서 반영)
        self.use_vertex_check.blockSignals(True)
        self.use_vertex_check.setChecked(bool(cfg.get("use_vertex_ai", defaults.get("use_vertex_ai", False))))
        self.use_vertex_check.blockSignals(False)
        self.sa_path_edit.setText(str(cfg.get("service_account_file_path") or ""))
        self.gcp_project_edit.setText(str(cfg.get("gcp_project") or ""))
        self.gcp_location_edit.setText(str(cfg.get("gcp_location") or ""))

        # 프로바이더별 폼 가시성 및 추천 모델 목록 동기화
        self._on_provider_changed()

        # 프로바이더별 모델명 로드
        if provider == "claude_cli":
            model_val = str(cfg.get("claude_cli_model") or defaults.get("claude_cli_model", "default"))
        elif provider == "codex_cli":
            model_val = str(cfg.get("codex_cli_model") or defaults.get("codex_cli_model", "gpt-5.5"))
        elif provider == "antigravity_cli":
            model_val = str(cfg.get("antigravity_cli_model") or defaults.get("antigravity_cli_model", "default"))
        elif provider == "openai_compatible":
            model_val = str(cfg.get("openai_compatible_model") or defaults.get("openai_compatible_model", "default"))
        elif provider == "ollama":
            model_val = str(cfg.get("ollama_model") or defaults.get("ollama_model") or "")
        else:
            model_val = str(cfg.get("model_name") or defaults.get("model_name", "gemini-2.0-flash"))

        if model_val and model_val not in [self.model_name_combo.itemText(i) for i in range(self.model_name_combo.count())]:
            self.model_name_combo.addItem(model_val)
        if model_val:
            self.model_name_combo.setCurrentText(model_val)

        temp_val = float(cfg.get("temperature", defaults.get("temperature", 0.7)))
        self.temperature_slider.setValue(int(temp_val * 100))
        
        top_p_val = float(cfg.get("top_p", defaults.get("top_p", 0.9)))
        self.top_p_slider.setValue(int(top_p_val * 100))

        thinking_budget = cfg.get("thinking_budget")
        if thinking_budget is not None:
            try:
                self.thinking_budget_slider.setValue(int(thinking_budget))
            except Exception:
                self.thinking_budget_slider.setValue(-1)
        else:
            self.thinking_budget_slider.setValue(-1)
        self.thinking_level_combo.setCurrentText(str(cfg.get("thinking_level", defaults.get("thinking_level", "high"))))

        chunk_size = cfg.get("chunk_size", defaults.get("chunk_size", 6000))
        if isinstance(chunk_size, int):
            self.chunk_size_spin.setValue(chunk_size)
        max_workers = cfg.get("max_workers", defaults.get("max_workers", 4))
        if isinstance(max_workers, int):
            self.max_workers_spin.setValue(max_workers)
        rpm = cfg.get("requests_per_minute", defaults.get("requests_per_minute", DEFAULT_REQUESTS_PER_MINUTE))
        try:
            self.rpm_spin.setValue(float(rpm))
        except Exception:
            self.rpm_spin.setValue(DEFAULT_REQUESTS_PER_MINUTE)

        self.novel_lang_edit.setText(str(cfg.get("novel_language", defaults.get("novel_language", "auto"))))
        self.novel_fallback_edit.setText(str(cfg.get("novel_language_fallback", defaults.get("novel_language_fallback", "ja"))))

        prompts_val = cfg.get("prompts", defaults.get("prompts", ""))
        if isinstance(prompts_val, str):
            self.prompt_edit.setPlainText(prompts_val)
        elif isinstance(prompts_val, (list, tuple)) and prompts_val:
            self.prompt_edit.setPlainText(str(prompts_val[0]))

        self.enable_prefill_check.setChecked(bool(cfg.get("enable_prefill_translation", defaults.get("enable_prefill_translation", False))))
        self.prefill_system_edit.setPlainText(str(cfg.get("prefill_system_instruction", defaults.get("prefill_system_instruction", ""))))

        self.prefill_history = copy.deepcopy(cfg.get("prefill_cached_history", []) or [])
        # 기본값 로드 시에도 cached history가 없으면 default_config 참조 고려 가능
        if not self.prefill_history and defaults.get("prefill_cached_history"):
             self.prefill_history = copy.deepcopy(defaults.get("prefill_cached_history"))

        self._update_prefill_button_text()

        # Vertex/모델 상태 조정
        self._apply_vertex_state()
        self._update_batch_availability()
        self._on_model_changed(self.model_name_combo.currentText())

        self.use_content_safety_check.setChecked(bool(cfg.get("use_content_safety_retry", defaults.get("use_content_safety_retry", True))))
        self.max_split_spin.setValue(int(cfg.get("max_content_safety_split_attempts", defaults.get("max_content_safety_split_attempts", 3))))
        self.min_chunk_spin.setValue(int(cfg.get("min_content_safety_chunk_size", defaults.get("min_content_safety_chunk_size", 100))))

        # PageFold 토큰 최적화 설정 로드
        self.enable_pagefold_check.setChecked(
            bool(cfg.get("enable_pagefold", defaults.get("enable_pagefold", True)))
        )
        pf_mode = str(cfg.get("pagefold_mode", defaults.get("pagefold_mode", "reference")))
        idx = self.pagefold_mode_combo.findData(pf_mode)
        if idx != -1:
            self.pagefold_mode_combo.setCurrentIndex(idx)
        else:
            self.pagefold_mode_combo.setCurrentIndex(0)
        pf_font_size = float(cfg.get("pagefold_font_size", defaults.get("pagefold_font_size", 1.0)))
        self.pagefold_font_size_spin.setValue(pf_font_size)
        self._on_pagefold_toggled(self.enable_pagefold_check.isChecked())

        # 번역 장기기억 설정 로드
        self.enable_memory_check.setChecked(bool(cfg.get("enable_translation_memory", defaults.get("enable_translation_memory", False))))
        self.voyage_key_edit.setText(str(cfg.get("voyage_api_key") or ""))
        self.voyage_model_combo.setCurrentText(str(cfg.get("voyage_model") or defaults.get("voyage_model") or "voyage-4-lite"))
        self.memory_top_k_spin.setValue(int(cfg.get("memory_top_k", defaults.get("memory_top_k", 3))))
        self.memory_min_sim_spin.setValue(float(cfg.get("memory_min_similarity", defaults.get("memory_min_similarity", 0.55))))
        depth_idx = self.memory_depth_combo.findData(str(cfg.get("memory_depth", defaults.get("memory_depth", "balanced"))))
        self.memory_depth_combo.setCurrentIndex(depth_idx if depth_idx != -1 else 1)
        self.memory_extract_check.setChecked(bool(cfg.get("enable_memory_extraction", defaults.get("enable_memory_extraction", False))))
        self.memory_extract_model_edit.setText(str(cfg.get("memory_extraction_model") or ""))
        self._on_memory_toggled(self.enable_memory_check.isChecked())
        
        self.batch_key_edit.setText(str(cfg.get("batch_api_key") or ""))

        # 번역 모드 선택 동기화 (신규)
        mode_val = str(cfg.get("translation_mode", "standard"))
        self.mode_selector._on_card_clicked(mode_val)

    def set_config_coordinator(self, coordinator: ConfigCoordinator) -> None:
        self._config_coordinator = coordinator

    def load_config_into_ui(self) -> None:
        self._load_config()

    def _save_config_to_service(self) -> None:
        """모든 탭의 설정을 모아 저장한다 (번역 시작 등 작업 흐름은 저장 실패로 막지 않는다)."""
        self._config_coordinator.save_quietly("설정 탭")

    def apply_to_config(self, cfg: dict) -> None:
        """설정 탭 위젯 값을 cfg에 써 넣는다. 파일 저장은 하지 않는다."""
        # 입력/출력 파일 경로 저장
        input_path = self.input_edit.text().strip()
        output_path = self.output_edit.text().strip()
        cfg["input_files"] = [input_path] if input_path else []
        cfg["output_file"] = output_path or None
        
        # 키는 프로바이더별 몫만 해당 필드에 저장한다 (Gemini 목록에 다른 회사 키가 섞이지 않도록)
        gemini_keys = self._api_keys_for("gemini")
        if gemini_keys:
            cfg["api_keys"] = gemini_keys
        for provider_id, field in PROVIDER_KEY_FIELDS.items():
            if provider_id != "gemini":
                keys = self._api_keys_for(provider_id)
                cfg[field] = keys[0] if keys else ""
        cfg["use_vertex_ai"] = self.use_vertex_check.isChecked()
        cfg["service_account_file_path"] = self.sa_path_edit.text().strip() or None
        cfg["gcp_project"] = self.gcp_project_edit.text().strip() or None
        cfg["gcp_location"] = self.gcp_location_edit.text().strip() or None
        # 프로바이더별 설정 저장
        provider = self.provider_combo.currentData() or "gemini"
        cfg["llm_provider"] = provider
        selected_model = self.model_name_combo.currentText().strip()

        for provider_id, (field, _default) in CLI_PATH_FIELDS.items():
            cfg[field] = self._cli_path_for(provider_id)
        if provider == "claude_cli":
            cfg["claude_cli_model"] = selected_model or "default"
        elif provider == "codex_cli":
            cfg["codex_cli_model"] = selected_model or "gpt-5.5"
        elif provider == "antigravity_cli":
            cfg["antigravity_cli_model"] = selected_model or "default"
        elif provider == "openai_compatible":
            cfg["openai_compatible_base_url"] = self.base_url_edit.text().strip()
            cfg["openai_compatible_model"] = selected_model or "default"
        elif provider == "ollama":
            cfg["ollama_base_url"] = self.base_url_edit.text().strip() or "http://localhost:11434"
            cfg["ollama_model"] = selected_model
            cfg["ollama_num_ctx"] = int(self.ollama_num_ctx_spin.value())
        else:
            cfg["model_name"] = selected_model or None
        cfg["temperature"] = self.temperature_slider.value() / 100.0
        cfg["top_p"] = self.top_p_slider.value() / 100.0
        # Gemini 추론 설정은 Gemini를 고른 상태에서만 바꾼다.
        # 예전에는 다른 프로바이더에서 저장하면 비활성 칸이라 None으로 지워졌다.
        if provider == "gemini":
            gemini_spec = reasoning_spec_for("gemini", selected_model)
            if gemini_spec.kind == "level":
                cfg["thinking_level"] = self.thinking_level_combo.currentText()
            elif gemini_spec.kind == "budget":
                cfg["thinking_budget"] = int(self.thinking_budget_slider.value())
        for provider_id in EFFORT_PROVIDERS:
            cfg[reasoning_spec_for(provider_id).config_key] = self._effort_for(provider_id)
        cfg["chunk_size"] = int(self.chunk_size_spin.value())
        cfg["max_workers"] = int(self.max_workers_spin.value())
        cfg["requests_per_minute"] = float(self.rpm_spin.value())
        cfg["novel_language"] = self.novel_lang_edit.text().strip() or "auto"
        cfg["novel_language_fallback"] = self.novel_fallback_edit.text().strip() or "ja"
        cfg["prompts"] = self.prompt_edit.toPlainText()
        cfg["enable_prefill_translation"] = self.enable_prefill_check.isChecked()
        cfg["prefill_system_instruction"] = self.prefill_system_edit.toPlainText()
        cfg["prefill_cached_history"] = copy.deepcopy(self.prefill_history)
        cfg["use_content_safety_retry"] = self.use_content_safety_check.isChecked()
        cfg["max_content_safety_split_attempts"] = int(self.max_split_spin.value())
        cfg["min_content_safety_chunk_size"] = int(self.min_chunk_spin.value())
        cfg["translation_mode"] = self.mode_selector.get_current_mode()
        cfg["batch_api_key"] = self.batch_key_edit.text().strip()
        cfg["enable_pagefold"] = self.enable_pagefold_check.isChecked()
        cfg["pagefold_mode"] = self.pagefold_mode_combo.currentData() or "reference"
        cfg["pagefold_font_size"] = float(self.pagefold_font_size_spin.value())
        cfg["enable_translation_memory"] = self.enable_memory_check.isChecked()
        cfg["voyage_api_key"] = self.voyage_key_edit.text().strip()
        cfg["voyage_model"] = self.voyage_model_combo.currentText().strip() or "voyage-4-lite"
        cfg["memory_top_k"] = int(self.memory_top_k_spin.value())
        cfg["memory_min_similarity"] = float(self.memory_min_sim_spin.value())
        cfg["memory_depth"] = self.memory_depth_combo.currentData() or "balanced"
        cfg["enable_memory_extraction"] = self.memory_extract_check.isChecked()
        cfg["memory_extraction_model"] = self.memory_extract_model_edit.text().strip()

    @asyncSlot()
    async def _on_start_clicked(self) -> None:
        await self._start_translation_flow()

    async def _start_translation_flow(self, translation_mode_override: Optional[str] = None) -> None:
        input_path = self.input_edit.text().strip()
        output_path = self.output_edit.text().strip()
        if not input_path or not output_path:
            QtWidgets.QMessageBox.warning(self, "입력 필요", "입력/출력 경로를 모두 지정하세요.")
            return

        # 설정을 AppService에 반영
        self._save_config_to_service()

        # 버튼 상태
        self.start_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.status_label.setText("번역 시작...")
        self.progress_bar.setValue(0)
        
        # ETA 계산을 위한 시작 시간 및 초기 청크 수 기록 (이어하기 대응)
        import time
        self._translation_start_time = time.time()
        self._translation_start_chunks = -1  # -1은 미초기화 상태, 첫 콜백에서 설정됨

        # 이미 실행 중이면 예외 발생하도록 방지
        if self.app_service.current_translation_task and not self.app_service.current_translation_task.done():
            QtWidgets.QMessageBox.warning(self, "실행 중", "이미 번역이 실행 중입니다.")
            self.start_btn.setEnabled(True)
            self.cancel_btn.setEnabled(False)
            self._translation_start_time = None
            self._translation_start_chunks = 0
            return

        # Task 실행
        self._translation_task = asyncio.create_task(
            self._run_translation(input_path, output_path, translation_mode_override)
        )
        try:
            await self._translation_task
        except asyncio.CancelledError:
            pass
        finally:
            self._translation_task = None

    @asyncSlot()
    async def _on_cancel_clicked(self) -> None:
        # 즉시 UI 반응: 모든 버튼 비활성화
        self.start_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.status_label.setText("취소 처리 중... (진행 중인 작업 정리)")
        
        # 취소 요청 (비동기)
        if self.app_service:
            await self.app_service.cancel_translation_async()
        
        # 완료 시 UI 복구는 completion_signal에서 처리됨

    # ------------------------------------------------------------------
    # 배치 번역
    # ------------------------------------------------------------------

    def _batch_unavailable_reason_from_ui(self) -> Optional[str]:
        if (self.provider_combo.currentData() or "gemini") != "gemini":
            return "배치 번역은 Google Gemini API 프로바이더에서만 사용할 수 있습니다."
        if self.use_vertex_check.isChecked():
            return "배치 번역은 Vertex AI를 지원하지 않습니다. Gemini API 키를 사용하세요."
        return None

    def _update_batch_availability(self) -> None:
        reason = self._batch_unavailable_reason_from_ui()
        self.mode_selector.set_mode_available("batch", reason is None, reason or "")

    def _is_batch_mode(self) -> bool:
        return self.mode_selector.get_current_mode() == "batch"

    def _current_batch_summary(self):
        input_path = self.input_edit.text().strip()
        if not input_path or not hasattr(self.app_service, "get_batch_summary"):
            return None
        try:
            return self.app_service.get_batch_summary(input_path)
        except Exception as e:  # pragma: no cover - 표시 실패는 무시
            logger.warning(f"배치 현황 조회 실패: {e}")
            return None

    def _update_batch_panel(self, summary=None) -> None:
        """배치 모드일 때 작업 목록·버튼·폴링 타이머를 현재 메타데이터에 맞춘다."""
        is_batch = self._is_batch_mode()
        self.batch_group.setVisible(is_batch)
        self.start_btn.setText("배치 제출 / 상태 확인" if is_batch else "번역 시작")
        if not is_batch:
            self._batch_timer.stop()
            return

        summary = summary if summary is not None else self._current_batch_summary()
        self.batch_jobs_table.setRowCount(0)
        if summary is None or not summary.jobs:
            self.batch_status_label.setText("제출한 배치 작업이 없습니다. '배치 제출 / 상태 확인'을 누르면 미번역 청크를 제출합니다.")
            active = remaining = False
        else:
            text = summary.describe()
            if summary.config_changed:
                text += "\n제출 후 번역 설정이 바뀌었습니다. 진행 중인 작업은 제출 당시 설정으로 번역됩니다."
            self.batch_status_label.setText(text)
            now = time.time()
            for job in summary.jobs:
                row = self.batch_jobs_table.rowCount()
                self.batch_jobs_table.insertRow(row)
                chunks = job.get("chunks") or []
                chunk_range = f"{chunks[0] + 1}–{chunks[-1] + 1} ({len(chunks)})" if chunks else "-"
                elapsed_min = int((now - float(job.get("submitted_at") or now)) / 60)
                elapsed = f"{elapsed_min // 60}시간 {elapsed_min % 60}분" if elapsed_min >= 60 else f"{elapsed_min}분"
                result = (
                    f"성공 {job.get('succeeded', 0)} · 검열 {job.get('blocked', 0)} · 오류 {job.get('errored', 0)}"
                    if job.get("collected") and "succeeded" in job else ""
                )
                values = [job.get("name") or job.get("display_name", ""), chunk_range,
                          str(job.get("state", "")), elapsed, result]
                for col, value in enumerate(values):
                    self.batch_jobs_table.setItem(row, col, QtWidgets.QTableWidgetItem(value))
            active, remaining = summary.active, bool(summary.remaining)
            if summary.total_chunks:
                self.progress_bar.setValue(int(summary.translated * 100 / summary.total_chunks))

        busy = self._batch_busy or bool(
            self.app_service.current_translation_task and not self.app_service.current_translation_task.done()
        )
        self.batch_refresh_btn.setEnabled(active and not busy)
        self.batch_cancel_btn.setEnabled(active and not busy)
        for btn in (self.batch_realtime_btn, self.batch_resubmit_btn, self.batch_keep_btn):
            btn.setEnabled(remaining and not active and not busy)

        if active:
            interval_s = int((getattr(self.app_service, "config", {}) or {}).get("batch_poll_interval_seconds", 60) or 60)
            if not self._batch_timer.isActive() or self._batch_timer.interval() != interval_s * 1000:
                self._batch_timer.start(max(10, interval_s) * 1000)
        else:
            self._batch_timer.stop()

    async def _run_batch_action(self, label: str, action) -> None:
        if self._batch_busy:
            return
        self._batch_busy = True
        self._update_batch_panel()
        self.status_label.setText(f"{label}...")
        try:
            result = await action()
            if isinstance(result, str):
                self.status_label.setText(result)
        except Exception as e:
            logger.error(f"배치 작업 처리 실패 ({label}): {e}", exc_info=True)
            self.status_label.setText(f"{label} 실패: {e}")
            QtWidgets.QMessageBox.warning(self, "배치 작업", f"{label} 중 오류가 발생했습니다.\n{e}")
        finally:
            self._batch_busy = False
            self._update_batch_panel()

    def _batch_paths(self):
        return self.input_edit.text().strip(), self.output_edit.text().strip()

    @asyncSlot()
    async def _on_batch_timer(self) -> None:
        if not self._is_batch_mode() or self._batch_busy:
            return
        summary = self._current_batch_summary()
        if not summary or not summary.active:
            self._batch_timer.stop()
            return
        await self._batch_refresh()

    @asyncSlot()
    async def _on_batch_refresh_clicked(self) -> None:
        await self._batch_refresh()

    async def _batch_refresh(self) -> None:
        input_path, output_path = self._batch_paths()

        async def action():
            summary = await self.app_service.refresh_batch_async(input_path, output_path, self._status_cb)
            if summary.complete:
                self._show_tray_notification(True, summary.translated, summary.total_chunks, None)
            return summary.describe()

        await self._run_batch_action("배치 상태 확인", action)

    @asyncSlot()
    async def _on_batch_cancel_clicked(self) -> None:
        if QtWidgets.QMessageBox.question(self, "배치 작업 취소", "진행 중인 배치 작업을 취소할까요?") != QtWidgets.QMessageBox.Yes:
            return
        input_path, _ = self._batch_paths()

        async def action():
            return (await self.app_service.cancel_batch_async(input_path, self._status_cb)).describe()

        await self._run_batch_action("배치 작업 취소", action)

    @asyncSlot()
    async def _on_batch_realtime_clicked(self) -> None:
        # 표준 모드 이어하기가 metadata의 translated_chunks에 없는 청크만 번역한다
        await self._start_translation_flow(translation_mode_override="standard")

    @asyncSlot()
    async def _on_batch_resubmit_clicked(self) -> None:
        self._save_config_to_service()
        input_path, _ = self._batch_paths()

        async def action():
            return (await self.app_service.resubmit_batch_async(input_path, self._status_cb)).describe()

        await self._run_batch_action("배치 재제출", action)

    @asyncSlot()
    async def _on_batch_keep_clicked(self) -> None:
        input_path, output_path = self._batch_paths()

        async def action():
            count = await self.app_service.save_batch_with_failures_async(input_path, output_path, self._status_cb)
            return f"저장 완료: 미완료 청크 {count}개는 실패 표시와 원문으로 채웠습니다."

        await self._run_batch_action("최종 파일 저장", action)

    def _build_provider_config_from_ui(self) -> dict:
        """저장하지 않은 현재 UI 상태로 클라이언트 생성용 설정을 만든다 (연결 테스트·모델 조회용)."""
        provider = self.provider_combo.currentData() or "gemini"
        # 현재 프로바이더 몫의 키만 쓴다
        api_keys = self._api_keys_for(provider)
        first_key = api_keys[0] if api_keys else ""
        return {
            "llm_provider": provider,
            "api_keys": api_keys if provider == "gemini" else [],
            "api_key": first_key if provider == "gemini" else "",
            "use_vertex_ai": self.use_vertex_check.isChecked(),
            "service_account_file_path": self.sa_path_edit.text().strip() or None,
            "gcp_project": self.gcp_project_edit.text().strip() or None,
            "gcp_location": self.gcp_location_edit.text().strip() or None,
            "claude_cli_path": self._cli_path_for("claude_cli"),
            "claude_cli_model": self.model_name_combo.currentText().strip() or "default",
            "claude_cli_api_key": (first_key or None) if provider == "claude_cli" else None,
            "codex_cli_path": self._cli_path_for("codex_cli"),
            "codex_cli_model": self.model_name_combo.currentText().strip() or "gpt-5.5",
            "codex_cli_api_key": (first_key or None) if provider == "codex_cli" else None,
            "antigravity_cli_path": self._cli_path_for("antigravity_cli"),
            "antigravity_cli_model": self.model_name_combo.currentText().strip() or "default",
            "openai_compatible_base_url": self.base_url_edit.text().strip(),
            "openai_compatible_api_key": first_key if provider == "openai_compatible" else "",
            "openai_compatible_model": self.model_name_combo.currentText().strip() or "default",
            "model_name": self.model_name_combo.currentText().strip(),
            "ollama_base_url": self.base_url_edit.text().strip() or "http://localhost:11434",
            "ollama_model": self.model_name_combo.currentText().strip(),
            "ollama_api_key": first_key if provider == "ollama" else "",
            "ollama_num_ctx": int(self.ollama_num_ctx_spin.value()),
            **{reasoning_spec_for(p).config_key: self._effort_for(p) for p in EFFORT_PROVIDERS},
        }

    @asyncSlot()
    async def _on_auth_check_clicked(self) -> None:
        """현재 UI에 설정된 프로바이더 인증 및 통신 상태를 테스트"""
        self.auth_check_btn.setEnabled(False)
        orig_text = self.auth_check_btn.text()
        self.auth_check_btn.setText("점검 중...")
        try:
            temp_cfg = self._build_provider_config_from_ui()

            if hasattr(self.app_service, "check_llm_health_async"):
                success, message = await self.app_service.check_llm_health_async(temp_cfg)
            else:
                success, message = False, "AppService에 check_llm_health_async 메서드가 없습니다."

            if success:
                QtWidgets.QMessageBox.information(
                    self,
                    "인증 / 연결 성공",
                    f"{message}"
                )
            else:
                QtWidgets.QMessageBox.warning(
                    self,
                    "인증 / 연결 필요",
                    f"{message}"
                )
        except Exception as e:
            QtWidgets.QMessageBox.critical(
                self,
                "점검 실패",
                f"인증 점검 중 오류 발생: {e}"
            )
        finally:
            self.auth_check_btn.setEnabled(True)
            self.auth_check_btn.setText(orig_text)

    # Qt slots (UI thread)
    @QtCore.Slot(object)
    def _on_progress(self, dto: TranslationJobProgressDTO) -> None:
        try:
            if dto.total_chunks and dto.total_chunks > 0:
                pct = int((dto.processed_chunks / dto.total_chunks) * 100)
                self.progress_bar.setValue(pct)
                
                # 통계 정보 업데이트 (완료 시 사용)
                self._total_chunks = dto.total_chunks
                self._final_processed_chunks = dto.processed_chunks
                
                # 이어하기 시작 시점의 청크 수 초기화 (첫 콜백에서만)
                # processed_chunks - 1 = 첫 번째 청크 완료 직전의 값 (새 번역: 0, 이어하기: 기존 완료 수)
                if self._translation_start_chunks == -1:
                    self._translation_start_chunks = dto.processed_chunks - 1
                
                # ETA 계산 (이어하기 robust 대응)
                if self._translation_start_time and dto.processed_chunks > 0:
                    import time
                    elapsed = time.time() - self._translation_start_time
                    
                    # 실제로 처리한 청크 수 = 현재 - 시작 시점 (이어하기 대응)
                    actual_processed = dto.processed_chunks - self._translation_start_chunks
                    
                    # 최소 1초 경과 후 ETA 계산 (초반 불안정성 방지)
                    if elapsed >= 1.0 and actual_processed > 0:
                        chunks_per_sec = actual_processed / elapsed
                        remaining_chunks = dto.total_chunks - dto.processed_chunks
                        
                        if chunks_per_sec > 0:
                            eta_seconds = remaining_chunks / chunks_per_sec
                            
                            # ETA 포맷팅
                            if eta_seconds < 60:
                                eta_str = f"{int(eta_seconds)}초"
                            elif eta_seconds < 3600:
                                minutes = int(eta_seconds / 60)
                                seconds = int(eta_seconds % 60)
                                eta_str = f"{minutes}분 {seconds}초"
                            else:
                                hours = int(eta_seconds / 3600)
                                minutes = int((eta_seconds % 3600) / 60)
                                eta_str = f"{hours}시간 {minutes}분"
                            
                            # 상태 메시지에 ETA 추가
                            base_msg = dto.current_status_message or ""
                            status_msg = f"{base_msg} | 진행: {dto.processed_chunks}/{dto.total_chunks} ({pct}%) | ETA: {eta_str}"
                        else:
                            # chunks_per_sec == 0 (이론적으로 불가능하지만 방어)
                            status_msg = f"{dto.current_status_message or ''} | 진행: {dto.processed_chunks}/{dto.total_chunks} ({pct}%)"
                    else:
                        # 1초 미만 경과 시 ETA 계산 생략
                        status_msg = f"{dto.current_status_message or ''} | 진행: {dto.processed_chunks}/{dto.total_chunks} ({pct}%)"
                else:
                    status_msg = dto.current_status_message or ""
            else:
                status_msg = dto.current_status_message or ""
            
            self.status_label.setText(status_msg)
        except Exception:
            # UI 갱신 실패는 무시
            pass

    @QtCore.Slot(str)
    def _on_status(self, message: str) -> None:
        self.status_label.setText(message)

    @QtCore.Slot(bool, str, dict)
    def _on_completion(self, success: bool, message: str, stats: dict) -> None:
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        if stats.get("batch"):
            self.status_label.setText(message)
            self._translation_start_time = None
            self._translation_start_chunks = 0
            self._update_batch_panel()
            return
        if success:
            self.progress_bar.setValue(100)
        self.status_label.setText(message)
        self._update_batch_panel()
        
        # 완료 팝업 다이얼로그 표시
        self._show_completion_dialog(success, message, stats)
        
        # 시작 시간 및 청크 카운터 초기화
        self._translation_start_time = None
        self._translation_start_chunks = 0
        self._total_chunks = 0
        self._final_processed_chunks = 0
    
    def _show_completion_dialog(self, success: bool, message: str, stats: dict) -> None:
        """완료 또는 오류 다이얼로그 표시 (통계 정보 최적화)"""
        elapsed = stats.get("elapsed_seconds", 0)
        total_chunks = stats.get("total_chunks", 0)
        processed_chunks = stats.get("processed_chunks", 0)
        newly_processed = stats.get("newly_processed", 0)
        error_detail = stats.get("error") or stats.get("reason")
        
        # 시스템 트레이 알림 표시 (앱이 최소화되어 있어도 알림 가능)
        self._show_tray_notification(success, newly_processed, total_chunks, error_detail)
        
        # 시간 포맷팅 함수
        def format_elapsed(seconds):
            if seconds < 60:
                return f"{int(seconds)}초"
            elif seconds < 3600:
                minutes = int(seconds / 60)
                secs = int(seconds % 60)
                return f"{minutes}분 {secs}초"
            else:
                hours = int(seconds / 3600)
                minutes = int((seconds % 3600) / 60)
                return f"{hours}시간 {minutes}분"
        
        elapsed_str = format_elapsed(elapsed)
        
        # 속도 계산 (분당 처리 청크 수)
        speed_str = "N/A"
        if elapsed > 0 and newly_processed > 0:
            chunks_per_min = (newly_processed / elapsed) * 60
            speed_str = f"{chunks_per_min:.1f} 청크/분"
            
        # 다이얼로그 기본 설정
        dlg = QtWidgets.QMessageBox(self)
        dlg.setStandardButtons(QtWidgets.QMessageBox.Ok)
        
        if success:
            title = "번역 완료"
            dlg.setIcon(QtWidgets.QMessageBox.Information)
            summary = f"총 {total_chunks}개 중 {newly_processed}개의 청크를 새로 번역했습니다.\n소요 시간: {elapsed_str}"
        else:
            title = f"번역 중단 ({error_detail or '알 수 없는 원인'})"
            dlg.setIcon(QtWidgets.QMessageBox.Warning)
            summary = f"진행 중 중단되었습니다. (완료: {processed_chunks}/{total_chunks})\n소요 시간: {elapsed_str}"

        dlg.setWindowTitle(title)
        dlg.setText(title)
        dlg.setInformativeText(summary)
        
        # 상세 정보 구성 (실제 상세 데이터 추가)
        detailed_info = [
            f"결과 메시지: {message}",
            f"입력 파일: {self.input_edit.text()}",
            f"출력 파일: {self.output_edit.text()}",
            f"사용 모델: {self.model_name_combo.currentText()}",
            "",
            f"전체 청크: {total_chunks} 개",
            f"기존 처리분: {processed_chunks - newly_processed} 개",
            f"이번 세션 처리: {newly_processed} 개",
            f"최종 완료율: {processed_chunks}/{total_chunks} ({int(processed_chunks/total_chunks*100) if total_chunks > 0 else 0}%)",
            "",
            f"실제 소요 시간: {int(elapsed)}초 ({elapsed_str})",
            f"평균 처리 속도: {speed_str}"
        ]
        
        if error_detail:
            detailed_info.append(f"\n추가 정보: {error_detail}")
            
        dlg.setDetailedText("\n".join(detailed_info))
        dlg.exec()

    def _apply_vertex_state(self) -> None:
        """Vertex 체크 상태에 맞춰 서비스 계정·GCP 행 표시와 API 키 입력 가능 여부를 맞춘다."""
        is_gemini = (self.provider_combo.currentData() or "gemini") == "gemini"
        is_vertex = is_gemini and self.use_vertex_check.isChecked()
        for w in (self.sa_row_widget, self.gcp_project_edit, self.gcp_location_edit):
            self.api_form.setRowVisible(w, is_vertex)
            w.setEnabled(is_vertex)
        # Vertex 사용 시 API 키 입력 비활성화, 미사용 시 활성화
        self.api_keys_edit.setEnabled(not is_vertex)

    @QtCore.Slot(int)
    def _on_vertex_toggle(self, _state: int = 0) -> None:
        # stateChanged는 int를 넘기는데 PySide6 6.4+에서는 int와 Qt.Checked 비교가 항상 False라
        # 인자 대신 위젯 상태를 직접 읽는다.
        self._apply_vertex_state()
        self._update_batch_availability()
        if self.use_vertex_check.isChecked() and not self.sa_path_edit.text().strip():
            QtWidgets.QMessageBox.information(
                self,
                "서비스 계정 필요",
                "Vertex AI를 사용하려면 서비스 계정 JSON 경로를 지정하세요.",
            )

    @QtCore.Slot(str)
    def _on_model_changed(self, model_name: str) -> None:
        self._apply_reasoning_ui(model_name)

    def _apply_reasoning_ui(self, model_name: Optional[str] = None) -> None:
        """프로바이더·모델에 맞는 추론 행만 보여준다 (reasoning_options 명세 기준)."""
        provider = self.provider_combo.currentData() or "gemini"
        if model_name is None:
            model_name = self.model_name_combo.currentText()
        spec = reasoning_spec_for(provider, model_name)
        is_gemini = provider == "gemini"
        gemini_level = is_gemini and spec.kind == "level"
        gemini_budget = is_gemini and spec.kind == "budget"

        if gemini_level:
            # 현재 선택 값을 보존하여 목록 재구성 후 다시 적용
            current_level = self.thinking_level_combo.currentText()
            self.thinking_level_combo.blockSignals(True)
            self.thinking_level_combo.clear()
            self.thinking_level_combo.addItems(list(spec.values))
            self.thinking_level_combo.setCurrentText(
                current_level if current_level in spec.values else (spec.default or spec.values[-1])
            )
            self.thinking_level_combo.blockSignals(False)

        self.thinking_level_combo.setEnabled(gemini_level)
        self.thinking_budget_slider.setEnabled(gemini_budget)
        self.gen_form.setRowVisible(self.thinking_level_combo, gemini_level)
        self.gen_form.setRowVisible(self.thinking_budget_row, gemini_budget)
        self.gen_form.setRowVisible(self.reasoning_effort_combo, (not is_gemini) and spec.kind == "level")

    @asyncSlot()
    async def _refresh_model_list(self, force: bool = False) -> None:
        if not hasattr(self.app_service, "get_available_models"):
            QtWidgets.QMessageBox.warning(self, "모델 조회 불가", "AppService에 모델 조회 메서드가 없습니다.")
            return

        current_text = self.model_name_combo.currentText().strip()
        # Use cache if available and not forcing
        if self._model_cache and not force:
            self._apply_model_names(self._model_cache, current_text)
            return

        self._set_model_progress(True)
        try:
            provider = self.provider_combo.currentData() or "gemini"
            if provider == "gemini":
                models = await self.app_service.get_available_models()
            else:
                # 저장 전 UI에서 고른 프로바이더·서버 기준으로 조회한다.
                models = await self.app_service.get_available_models(self._build_provider_config_from_ui())
        except Exception as e:  # pragma: no cover - UI alert path
            self._set_model_progress(False)
            retry = QtWidgets.QMessageBox.question(
                self,
                "조회 실패",
                f"모델 목록 조회 중 오류: {e}\n다시 시도하시겠습니까?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            )
            if retry == QtWidgets.QMessageBox.Yes:
                await self._refresh_model_list(force=True)
            elif self._model_cache:
                QtWidgets.QMessageBox.information(
                    self,
                    "캐시 사용",
                    "캐시에 저장된 모델 목록을 사용합니다.",
                )
                self._apply_model_names(self._model_cache, current_text)
            else:
                QtWidgets.QMessageBox.information(
                    self,
                    "로그 확인",
                    "자세한 오류는 logs 폴더의 최신 run_* 디렉터리를 확인하세요.",
                )
            return
        finally:
            self._set_model_progress(False)

        names = []
        for m in models or []:
            display_name = m.get("short_name") or m.get("display_name") or m.get("name")
            if display_name and isinstance(display_name, str):
                names.append(display_name.strip())
        names = sorted({n for n in names if n})

        if not names:
            QtWidgets.QMessageBox.information(self, "모델 없음", "조회된 모델이 없습니다.")
            return

        self._model_cache = names
        self._apply_model_names(names, current_text)

    def _apply_model_names(self, names: list[str], current_text: str) -> None:
        self.model_name_combo.blockSignals(True)
        self.model_name_combo.clear()
        self.model_name_combo.addItems(names)
        if current_text and current_text in names:
            self.model_name_combo.setCurrentText(current_text)
        elif names:
            self.model_name_combo.setCurrentText(names[0])
        self.model_name_combo.blockSignals(False)
        self._on_model_changed(self.model_name_combo.currentText())

    def _set_model_progress(self, active: bool) -> None:
        self.model_refresh_btn.setEnabled(not active)
        self.model_progress.setVisible(active)

    def _update_prefill_button_text(self) -> None:
        count = len(self.prefill_history)
        self.edit_history_btn.setText(f"프리필 히스토리 편집 (현재 {count} 턴)")

    def _open_prefill_history_dialog(self) -> None:
        result = PrefillHistoryEditorDialogQt.edit(
            self,
            self.prefill_history,
            system_instruction=self.prefill_system_edit.toPlainText(),
        )
        if result is None:
            return
        new_history, new_system_inst = result
        self.prefill_history = new_history
        if new_system_inst is not None:
            self.prefill_system_edit.setPlainText(new_system_inst)
        self._update_prefill_button_text()

    def _show_tray_notification(
        self,
        success: bool,
        newly_processed: int,
        total_chunks: int,
        error_detail: Optional[str] = None
    ) -> None:
        """시스템 트레이 알림 표시 (메인 윈도우로 위임)"""
        # 부모 윈도우 탐색 (BatchTranslatorWindow 찾기)
        main_window = self.window()
        if not main_window:
            return
        
        # show_tray_notification 메서드가 있는지 확인
        if not hasattr(main_window, "show_tray_notification"):
            return
        
        if success:
            title = "✅ 번역 완료"
            message = f"{newly_processed}개 청크 번역 완료 (전체 {total_chunks}개)"
            icon_type = "info"
        else:
            title = "⚠️ 번역 중단"
            reason = error_detail or "알 수 없는 원인"
            message = f"번역이 중단되었습니다: {reason}"
            icon_type = "warning"
        
        main_window.show_tray_notification(title, message, icon_type, duration_ms=8000)
