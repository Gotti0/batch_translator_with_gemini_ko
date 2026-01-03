"""
PySide6 Settings Tab (minimal async-enabled)
- Input/Output file selection
- Start/Cancel translation using AppService async APIs
- Progress/status display
"""

from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from typing import Optional

from PySide6 import QtCore, QtGui, QtWidgets
from qasync import asyncSlot

from core.dtos import TranslationJobProgressDTO
from gui_qt.dialogs_qt.prefill_history_editor_qt import PrefillHistoryEditorDialogQt


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


class SettingsTabQt(QtWidgets.QWidget):
    """Minimal PySide6 settings tab to drive async translation"""

    progress_signal = QtCore.Signal(object)  # TranslationJobProgressDTO
    status_signal = QtCore.Signal(str)
    completion_signal = QtCore.Signal(bool, str)

    def __init__(self, app_service, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.app_service = app_service
        self._loop = asyncio.get_event_loop()
        self.prefill_history = []
        self._model_cache: list[str] = []

        self._build_ui()
        self._wire_signals()
        self._load_config()

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
        api_form = QtWidgets.QFormLayout(api_group)

        self.api_keys_edit = QtWidgets.QPlainTextEdit()
        self.api_keys_edit.setPlaceholderText("API 키를 줄바꿈으로 구분하여 입력")
        self.use_vertex_check = QtWidgets.QCheckBox("Vertex AI 사용")
        self.sa_path_edit = QtWidgets.QLineEdit()
        sa_browse = QtWidgets.QPushButton("찾기")
        sa_browse.clicked.connect(self._browse_sa)
        sa_row = QtWidgets.QHBoxLayout()
        sa_row.addWidget(self.sa_path_edit)
        sa_row.addWidget(sa_browse)
        self.gcp_project_edit = QtWidgets.QLineEdit()
        self.gcp_location_edit = QtWidgets.QLineEdit()

        # 모델 콤보 (editable) - 기본 후보 + 사용자 입력 유지
        self.model_name_combo = NoWheelComboBox()
        self.model_name_combo.setEditable(True)
        self.model_name_combo.setInsertPolicy(QtWidgets.QComboBox.NoInsert)
        self.model_name_combo.addItems([
            "gemini-2.0-flash",
            "gemini-2.0-pro",
            "gemini-2.5-pro",
            "gemini-3.0-pro",
            "gemini-3.0-flash",
        ])

        self.model_refresh_btn = QtWidgets.QPushButton("모델 목록 새로고침")
        self.model_progress = QtWidgets.QProgressBar()
        self.model_progress.setRange(0, 0)
        self.model_progress.setTextVisible(False)
        self.model_progress.setFixedHeight(10)
        self.model_progress.setVisible(False)
        model_row = QtWidgets.QHBoxLayout()
        model_row.addWidget(self.model_name_combo)
        model_row.addWidget(self.model_refresh_btn)
        model_row.addWidget(self.model_progress)

        api_form.addRow("API 키 목록", self.api_keys_edit)
        api_form.addRow("Vertex AI", self.use_vertex_check)
        api_form.addRow("서비스 계정 JSON", self._wrap(sa_row))
        api_form.addRow("GCP 프로젝트", self.gcp_project_edit)
        api_form.addRow("GCP 위치", self.gcp_location_edit)
        api_form.addRow("모델 이름", self._wrap(model_row))

        # --- 생성 파라미터 ---
        gen_group = QtWidgets.QGroupBox("생성 파라미터")
        gen_form = QtWidgets.QFormLayout(gen_group)

        # Temperature (0.0 ~ 2.0)
        self.temperature_slider = NoWheelSlider(QtCore.Qt.Horizontal)
        self.temperature_slider.setRange(0, 200)
        self.temperature_slider.setValue(70)
        self.temperature_label = QtWidgets.QLabel("0.70")
        self.temperature_label.setMinimumWidth(40)
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
        self.top_p_label = QtWidgets.QLabel("0.90")
        self.top_p_label.setMinimumWidth(40)
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
        self.thinking_budget_label = QtWidgets.QLabel("-1 (비활성)")
        self.thinking_budget_label.setMinimumWidth(80)
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

        gen_form.addRow("Temperature", self._wrap(temp_row))
        gen_form.addRow("Top P", self._wrap(top_p_row))
        gen_form.addRow("Thinking Budget", self._wrap(budget_row))
        gen_form.addRow("Thinking Level", self.thinking_level_combo)

        # --- 파일/처리 설정 ---
        file_group = QtWidgets.QGroupBox("파일 / 처리")
        file_form = QtWidgets.QFormLayout(file_group)

        self.input_edit = QtWidgets.QLineEdit()
        self.output_edit = QtWidgets.QLineEdit()
        browse_in = QtWidgets.QPushButton("파일 선택")
        browse_out = QtWidgets.QPushButton("출력 경로")
        browse_in.clicked.connect(self._browse_input)
        browse_out.clicked.connect(self._browse_output)

        in_row = QtWidgets.QHBoxLayout()
        in_row.addWidget(self.input_edit)
        in_row.addWidget(browse_in)
        out_row = QtWidgets.QHBoxLayout()
        out_row.addWidget(self.output_edit)
        out_row.addWidget(browse_out)

        self.chunk_size_spin = NoWheelSpinBox()
        self.chunk_size_spin.setRange(500, 20000)
        self.chunk_size_spin.setSingleStep(500)
        self.max_workers_spin = NoWheelSpinBox()
        self.max_workers_spin.setRange(1, 64)
        self.rpm_spin = NoWheelSpinBox()
        self.rpm_spin.setRange(0, 2000)
        self.rpm_spin.setSingleStep(10)

        file_form.addRow("입력 파일", self._wrap(in_row))
        file_form.addRow("출력 파일", self._wrap(out_row))
        file_form.addRow("청크 크기", self.chunk_size_spin)
        file_form.addRow("동시 작업 수", self.max_workers_spin)
        file_form.addRow("분당 요청 수(RPM)", self.rpm_spin)

        # --- 언어 설정 ---
        lang_group = QtWidgets.QGroupBox("언어 설정")
        lang_form = QtWidgets.QFormLayout(lang_group)
        self.novel_lang_edit = QtWidgets.QLineEdit()
        self.novel_fallback_edit = QtWidgets.QLineEdit()
        lang_form.addRow("출발 언어", self.novel_lang_edit)
        lang_form.addRow("자동감지 실패 폴백", self.novel_fallback_edit)

        # --- 프롬프트 ---
        prompt_group = QtWidgets.QGroupBox("프롬프트")
        prompt_vbox = QtWidgets.QVBoxLayout(prompt_group)
        self.prompt_edit = QtWidgets.QPlainTextEdit()
        self.prompt_edit.setPlaceholderText("번역 프롬프트: {{slot}}과 {{glossary_context}} 지원")
        prompt_vbox.addWidget(self.prompt_edit)

        # --- 프리필 ---
        prefill_group = QtWidgets.QGroupBox("프리필(Prefill)")
        prefill_vbox = QtWidgets.QVBoxLayout(prefill_group)
        self.enable_prefill_check = QtWidgets.QCheckBox("프리필 번역 사용")
        self.prefill_system_edit = QtWidgets.QPlainTextEdit()
        self.prefill_system_edit.setPlaceholderText("시스템 지침")
        self.edit_history_btn = QtWidgets.QPushButton("프리필 히스토리 편집 (준비 중)")
        self.edit_history_btn.setEnabled(True)
        self.edit_history_btn.clicked.connect(self._open_prefill_history_dialog)
        prefill_vbox.addWidget(self.enable_prefill_check)
        prefill_vbox.addWidget(self.prefill_system_edit)
        prefill_vbox.addWidget(self.edit_history_btn)

        # --- 콘텐츠 안전 ---
        safety_group = QtWidgets.QGroupBox("콘텐츠 안전 재시도")
        safety_form = QtWidgets.QFormLayout(safety_group)
        self.use_content_safety_check = QtWidgets.QCheckBox("검열 오류 시 청크 분할 재시도")
        self.max_split_spin = NoWheelSpinBox()
        self.max_split_spin.setRange(1, 10)
        self.min_chunk_spin = NoWheelSpinBox()
        self.min_chunk_spin.setRange(50, 5000)
        self.min_chunk_spin.setSingleStep(50)
        safety_form.addRow(self.use_content_safety_check)
        safety_form.addRow("최대 분할 시도", self.max_split_spin)
        safety_form.addRow("최소 청크 크기", self.min_chunk_spin)

        # --- 액션/진행 표시 ---
        self.start_btn = QtWidgets.QPushButton("번역 시작")
        self.cancel_btn = QtWidgets.QPushButton("취소")
        self.cancel_btn.setEnabled(False)
        self.save_config_btn = QtWidgets.QPushButton("설정 저장")
        self.load_config_btn = QtWidgets.QPushButton("설정 불러오기")

        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.cancel_btn)
        btn_row.addWidget(self.save_config_btn)
        btn_row.addWidget(self.load_config_btn)

        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.status_label = QtWidgets.QLabel("대기 중")

        # 전체 배치
        layout.addWidget(api_group)
        layout.addWidget(gen_group)
        layout.addWidget(file_group)
        layout.addWidget(lang_group)
        layout.addWidget(prompt_group)
        layout.addWidget(prefill_group)
        layout.addWidget(safety_group)
        layout.addLayout(btn_row)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.status_label)
        layout.addStretch(1)
        
        # 스크롤 영역에 컨텐츠 설정
        scroll_area.setWidget(scroll_content)
        main_layout.addWidget(scroll_area)

    def _wire_signals(self) -> None:
        self.start_btn.clicked.connect(self._on_start_clicked)
        self.cancel_btn.clicked.connect(self._on_cancel_clicked)
        self.save_config_btn.clicked.connect(self._on_save_config_clicked)
        self.load_config_btn.clicked.connect(self._on_load_config_clicked)
        self.progress_signal.connect(self._on_progress)
        self.status_signal.connect(self._on_status)
        self.completion_signal.connect(self._on_completion)

        self.use_vertex_check.stateChanged.connect(self._on_vertex_toggle)
        self.model_name_combo.currentTextChanged.connect(self._on_model_changed)
        self.model_name_combo.editTextChanged.connect(self._on_model_changed)
        self.model_refresh_btn.clicked.connect(self._refresh_model_list)
            # Removed duplicate connection

    def _wrap(self, layout: QtWidgets.QLayout) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        w.setLayout(layout)
        return w

    def _browse_input(self) -> None:
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "입력 파일 선택")
        if file_path:
            self.input_edit.setText(file_path)
            # 출력 기본값: 입력과 동일한 폴더/확장자 처리
            candidate = str(Path(file_path).with_suffix(".translated.txt"))
            if not self.output_edit.text():
                self.output_edit.setText(candidate)

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

    async def _run_translation(self, input_path: str, output_path: str) -> None:
        try:
            await self.app_service.start_translation_async(
                input_file_path=input_path,
                output_file_path=output_path,
                progress_callback=self._progress_cb,
                status_callback=self._status_cb,
                tqdm_file_stream=None,
                retranslate_failed_only=False,
            )
            self.completion_signal.emit(True, "완료")
        except asyncio.CancelledError:
            self.completion_signal.emit(False, "취소됨")
            raise
        except Exception as e:  # pragma: no cover - UI 표시 목적
            self.completion_signal.emit(False, f"오류: {e}")

    def _load_config(self) -> None:
        cfg = getattr(self.app_service, "config", {}) or {}
        api_keys = cfg.get("api_keys") or []
        if isinstance(api_keys, list):
            self.api_keys_edit.setPlainText("\n".join(api_keys))
        elif isinstance(api_keys, str):
            self.api_keys_edit.setPlainText(api_keys)

        self.use_vertex_check.setChecked(bool(cfg.get("use_vertex_ai", False)))
        self.sa_path_edit.setText(str(cfg.get("service_account_file_path") or ""))
        self.gcp_project_edit.setText(str(cfg.get("gcp_project") or ""))
        self.gcp_location_edit.setText(str(cfg.get("gcp_location") or ""))
        model_val = str(cfg.get("model_name") or "")
        if model_val and model_val not in [self.model_name_combo.itemText(i) for i in range(self.model_name_combo.count())]:
            self.model_name_combo.addItem(model_val)
        self.model_name_combo.setCurrentText(model_val)

        temp_val = float(cfg.get("temperature", 0.7))
        self.temperature_slider.setValue(int(temp_val * 100))
        
        top_p_val = float(cfg.get("top_p", 0.9))
        self.top_p_slider.setValue(int(top_p_val * 100))

        thinking_budget = cfg.get("thinking_budget")
        if thinking_budget is not None:
            try:
                self.thinking_budget_slider.setValue(int(thinking_budget))
            except Exception:
                self.thinking_budget_slider.setValue(-1)
        else:
            self.thinking_budget_slider.setValue(-1)
        self.thinking_level_combo.setCurrentText(str(cfg.get("thinking_level", "high")))

        chunk_size = cfg.get("chunk_size", 6000)
        if isinstance(chunk_size, int):
            self.chunk_size_spin.setValue(chunk_size)
        max_workers = cfg.get("max_workers", 4)
        if isinstance(max_workers, int):
            self.max_workers_spin.setValue(max_workers)
        rpm = cfg.get("requests_per_minute", 60)
        try:
            self.rpm_spin.setValue(int(rpm))
        except Exception:
            self.rpm_spin.setValue(60)

        self.novel_lang_edit.setText(str(cfg.get("novel_language", "auto")))
        self.novel_fallback_edit.setText(str(cfg.get("novel_language_fallback", "ja")))

        prompts_val = cfg.get("prompts", "")
        if isinstance(prompts_val, str):
            self.prompt_edit.setPlainText(prompts_val)
        elif isinstance(prompts_val, (list, tuple)) and prompts_val:
            self.prompt_edit.setPlainText(str(prompts_val[0]))

        self.enable_prefill_check.setChecked(bool(cfg.get("enable_prefill_translation", False)))
        self.prefill_system_edit.setPlainText(str(cfg.get("prefill_system_instruction", "")))

        self.prefill_history = copy.deepcopy(cfg.get("prefill_cached_history", []) or [])
        self._update_prefill_button_text()

        # Vertex/모델 상태 조정
        self._on_vertex_toggle(self.use_vertex_check.checkState())
        self._on_model_changed(self.model_name_combo.currentText())

        self.use_content_safety_check.setChecked(bool(cfg.get("use_content_safety_retry", True)))
        self.max_split_spin.setValue(int(cfg.get("max_content_safety_split_attempts", 3)))
        self.min_chunk_spin.setValue(int(cfg.get("min_content_safety_chunk_size", 100)))

    def _save_config_to_service(self) -> None:
        cfg = getattr(self.app_service, "config", {}) or {}
        api_keys = [line.strip() for line in self.api_keys_edit.toPlainText().splitlines() if line.strip()]
        if api_keys:
            cfg["api_keys"] = api_keys
        cfg["use_vertex_ai"] = self.use_vertex_check.isChecked()
        cfg["service_account_file_path"] = self.sa_path_edit.text().strip() or None
        cfg["gcp_project"] = self.gcp_project_edit.text().strip() or None
        cfg["gcp_location"] = self.gcp_location_edit.text().strip() or None
        cfg["model_name"] = self.model_name_combo.currentText().strip() or None
        cfg["temperature"] = self.temperature_slider.value() / 100.0
        cfg["top_p"] = self.top_p_slider.value() / 100.0
        cfg["thinking_budget"] = int(self.thinking_budget_slider.value()) if self.thinking_budget_slider.isEnabled() else None
        cfg["thinking_level"] = self.thinking_level_combo.currentText() if self.thinking_level_combo.isEnabled() else None
        cfg["chunk_size"] = int(self.chunk_size_spin.value())
        cfg["max_workers"] = int(self.max_workers_spin.value())
        cfg["requests_per_minute"] = int(self.rpm_spin.value())
        cfg["novel_language"] = self.novel_lang_edit.text().strip() or "auto"
        cfg["novel_language_fallback"] = self.novel_fallback_edit.text().strip() or "ja"
        cfg["prompts"] = self.prompt_edit.toPlainText()
        cfg["enable_prefill_translation"] = self.enable_prefill_check.isChecked()
        cfg["prefill_system_instruction"] = self.prefill_system_edit.toPlainText()
        cfg["prefill_cached_history"] = copy.deepcopy(self.prefill_history)
        cfg["use_content_safety_retry"] = self.use_content_safety_check.isChecked()
        cfg["max_content_safety_split_attempts"] = int(self.max_split_spin.value())
        cfg["min_content_safety_chunk_size"] = int(self.min_chunk_spin.value())
        self.app_service.config = cfg
        try:
            self.app_service.save_app_config(cfg)
        except Exception:
            # 저장 실패는 UI에서만 알림
            pass

    @asyncSlot()
    async def _on_start_clicked(self) -> None:
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

        # 이미 실행 중이면 예외 발생하도록 방지
        if self.app_service.current_translation_task and not self.app_service.current_translation_task.done():
            QtWidgets.QMessageBox.warning(self, "실행 중", "이미 번역이 실행 중입니다.")
            self.start_btn.setEnabled(True)
            self.cancel_btn.setEnabled(False)
            return

        # Task 실행
        self._translation_task = asyncio.create_task(
            self._run_translation(input_path, output_path)
        )
        try:
            await self._translation_task
        finally:
            self._translation_task = None

    @asyncSlot()
    async def _on_cancel_clicked(self) -> None:
        if self.app_service and self.app_service.current_translation_task:
            self.app_service.current_translation_task.cancel()
        self.status_label.setText("취소 요청...")
        self.cancel_btn.setEnabled(False)

    def _on_save_config_clicked(self) -> None:
        """'설정 저장' 버튼 클릭 시 config.json에 저장"""
        try:
            self._save_config_to_service()
            QtWidgets.QMessageBox.information(
                self,
                "저장 성공",
                "config.json에 설정이 저장되었습니다."
            )
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self,
                "저장 실패",
                f"설정 저장 중 오류 발생: {e}"
            )

    def _on_load_config_clicked(self) -> None:
        """'설정 불러오기' 버튼 클릭 시 config.json에서 다시 로드"""
        try:
            # AppService의 config를 다시 로드
            if hasattr(self.app_service, 'load_app_config'):
                self.app_service.config = self.app_service.load_app_config()
            elif hasattr(self.app_service, 'reload_config'):
                self.app_service.reload_config()
            
            # GUI에 반영
            self._load_config()
            
            QtWidgets.QMessageBox.information(
                self,
                "불러오기 성공",
                "config.json에서 설정을 불러왔습니다."
            )
        except Exception as e:
            QtWidgets.QMessageBox.warning(
                self,
                "불러오기 실패",
                f"설정 불러오기 중 오류 발생: {e}"
            )

    # Qt slots (UI thread)
    @QtCore.Slot(object)
    def _on_progress(self, dto: TranslationJobProgressDTO) -> None:
        try:
            if dto.total_chunks:
                pct = int((dto.processed_chunks / dto.total_chunks) * 100)
                self.progress_bar.setValue(pct)
            self.status_label.setText(dto.current_status_message or "")
        except Exception:
            # UI 갱신 실패는 무시
            pass

    @QtCore.Slot(str)
    def _on_status(self, message: str) -> None:
        self.status_label.setText(message)

    @QtCore.Slot(bool, str)
    def _on_completion(self, success: bool, message: str) -> None:
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        if success:
            self.progress_bar.setValue(100)
        self.status_label.setText(message)

    @QtCore.Slot(int)
    def _on_vertex_toggle(self, state: int) -> None:
        enabled = state == QtCore.Qt.Checked
        self.sa_path_edit.setEnabled(enabled)
        self.gcp_project_edit.setEnabled(enabled)
        self.gcp_location_edit.setEnabled(enabled)
        # Vertex 사용 시 API 키 입력 비활성화, 미사용 시 활성화
        self.api_keys_edit.setEnabled(not enabled)
        if enabled and not self.sa_path_edit.text().strip():
            QtWidgets.QMessageBox.information(
                self,
                "서비스 계정 필요",
                "Vertex AI를 사용하려면 서비스 계정 JSON 경로를 지정하세요.",
            )

    @QtCore.Slot(str)
    def _on_model_changed(self, model_name: str) -> None:
        name = (model_name or "").lower()

        # 현재 선택 값을 보존하여 목록 재구성 후 다시 적용
        current_level = self.thinking_level_combo.currentText()

        # Gemini 3: Thinking Level on, Budget off
        if "gemini-3" in name:
            self.thinking_level_combo.setEnabled(True)
            values = ["minimal", "low", "medium", "high"] if "flash" in name else ["low", "high"]
            self.thinking_level_combo.blockSignals(True)
            self.thinking_level_combo.clear()
            self.thinking_level_combo.addItems(values)
            # keep current if valid else default high
            if current_level in values:
                self.thinking_level_combo.setCurrentText(current_level)
            else:
                self.thinking_level_combo.setCurrentText("high")
            self.thinking_level_combo.blockSignals(False)

            self.thinking_budget_slider.setEnabled(False)

        # Gemini 2.5: Budget on, Level off
        elif "gemini-2.5" in name:
            self.thinking_level_combo.setEnabled(False)
            self.thinking_budget_slider.setEnabled(True)

        # Other models: Budget on, Level off (fallback)
        else:
            self.thinking_level_combo.setEnabled(False)
            self.thinking_budget_slider.setEnabled(True)

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
            loop = asyncio.get_event_loop()
            models = await loop.run_in_executor(None, self.app_service.get_available_models)
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
