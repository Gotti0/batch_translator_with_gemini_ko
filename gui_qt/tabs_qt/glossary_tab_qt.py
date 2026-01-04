"""
PySide6 Glossary Tab
- Glossary extraction and display
- Async extraction via AppService (run in executor)
"""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from typing import Optional, List, Dict, Any

from PySide6 import QtCore, QtGui, QtWidgets
from qasync import asyncSlot

from core.dtos import GlossaryExtractionProgressDTO
from gui_qt.components_qt.tooltip_qt import TooltipQt
from gui_qt.dialogs_qt.prefill_history_editor_qt import PrefillHistoryEditorDialogQt
from gui_qt.dialogs_qt.glossary_editor_qt import GlossaryEditorDialogQt


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


class GlossaryTabQt(QtWidgets.QWidget):
    progress_signal = QtCore.Signal(object)  # GlossaryExtractionProgressDTO
    status_signal = QtCore.Signal(str)
    completion_signal = QtCore.Signal(bool, str, object)  # success, msg, result_path

    def __init__(self, app_service, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.app_service = app_service
        self._loop = asyncio.get_event_loop()
        self._extraction_task: Optional[asyncio.Task] = None
        self._stop_requested = False
        self._prefill_history: List[Dict[str, Any]] = []

        self._build_ui()
        self._wire_signals()
        self._load_config()

    # ---------- UI ----------
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

        # Path & actions
        path_group = QtWidgets.QGroupBox("용어집 JSON 파일")
        path_form = QtWidgets.QFormLayout(path_group)
        self.glossary_path_edit = QtWidgets.QLineEdit()
        TooltipQt(self.glossary_path_edit, "사용할 용어집 JSON 파일의 경로입니다.\n추출 기능을 사용하면 자동으로 채워지거나, 직접 입력/선택할 수 있습니다.")
        browse_glossary = QtWidgets.QPushButton("찾기")
        TooltipQt(browse_glossary, "용어집 JSON 파일을 선택합니다.")
        browse_glossary.clicked.connect(self._browse_glossary_json)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(self.glossary_path_edit)
        row.addWidget(browse_glossary)
        path_form.addRow("JSON 경로", row)

        self.extract_btn = QtWidgets.QPushButton("선택한 입력 파일에서 용어집 추출")
        TooltipQt(self.extract_btn, "'설정 및 번역' 탭에서 선택된 입력 파일을 분석하여 용어집을 추출하고,\n그 결과를 아래 텍스트 영역에 표시합니다.")
        self.stop_btn = QtWidgets.QPushButton("추출 중지")
        TooltipQt(self.stop_btn, "진행 중인 용어집 추출 작업을 중지하고 현재까지의 결과로 저장합니다.")
        self.stop_btn.setEnabled(False)
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addWidget(self.extract_btn)
        btn_row.addWidget(self.stop_btn)
        path_form.addRow(btn_row)

        self.progress_label = QtWidgets.QLabel("용어집 추출 대기 중...")
        TooltipQt(self.progress_label, "용어집 추출 작업의 진행 상태를 표시합니다.")
        path_form.addRow(self.progress_label)

        # Extraction settings
        settings_group = QtWidgets.QGroupBox("용어집 추출 설정")
        settings_form = QtWidgets.QFormLayout(settings_group)

        # Sample ratio (5.0 ~ 100.0%)
        self.sample_ratio_slider = NoWheelSlider(QtCore.Qt.Horizontal)
        self.sample_ratio_slider.setRange(50, 1000)  # 5.0 ~ 100.0 (0.1 단위)
        self.sample_ratio_slider.setValue(100)  # 10.0%
        TooltipQt(self.sample_ratio_slider, "용어집 추출 샘플링 비율을 조절합니다 (5.0% ~ 100.0%).\n100%로 설정하면 전체 텍스트를 분석합니다.")
        self.sample_ratio_label = QtWidgets.QLabel("10.0 %")
        self.sample_ratio_label.setMinimumWidth(60)
        self.sample_ratio_slider.valueChanged.connect(
            lambda v: self.sample_ratio_label.setText(f"{v/10:.1f} %")
        )
        sample_row = QtWidgets.QHBoxLayout()
        sample_row.addWidget(self.sample_ratio_slider)
        sample_row.addWidget(self.sample_ratio_label)

        # Extraction temperature (0.0 ~ 2.0)
        self.extraction_temp_slider = NoWheelSlider(QtCore.Qt.Horizontal)
        self.extraction_temp_slider.setRange(0, 200)
        self.extraction_temp_slider.setValue(30)  # 0.30
        TooltipQt(self.extraction_temp_slider, "용어집 추출 온도를 조절합니다 (0.0 ~ 2.0).\n낮을수록 일관적, 높을수록 다양하지만 덜 정확할 수 있습니다.")
        self.extraction_temp_label = QtWidgets.QLabel("0.30")
        self.extraction_temp_label.setMinimumWidth(60)
        TooltipQt(self.extraction_temp_label, "현재 설정된 용어집 추출 온도입니다.")
        self.extraction_temp_slider.valueChanged.connect(
            lambda v: self.extraction_temp_label.setText(f"{v/100:.2f}")
        )
        temp_row = QtWidgets.QHBoxLayout()
        temp_row.addWidget(self.extraction_temp_slider)
        temp_row.addWidget(self.extraction_temp_label)

        self.user_prompt_edit = QtWidgets.QPlainTextEdit()
        self.user_prompt_edit.setPlaceholderText("사용자 정의 추출 프롬프트 (옵션)")
        TooltipQt(self.user_prompt_edit, "용어집 추출 시 사용할 사용자 정의 프롬프트입니다.\n비워두면 기본 프롬프트를 사용합니다.\n플레이스홀더: {target_lang_name}, {target_lang_code}, {novelText}")

        prefill_box = QtWidgets.QHBoxLayout()
        self.enable_prefill_check = QtWidgets.QCheckBox("용어집 추출 프리필 활성화")
        TooltipQt(self.enable_prefill_check, "용어집 추출 시 프리필(Few-shot) 모드를 활성화합니다.\n이를 통해 모델에 추출 예시를 제공하여 정확도를 높일 수 있습니다.")
        self.edit_prefill_btn = QtWidgets.QPushButton("프리필/히스토리 편집")
        TooltipQt(self.edit_prefill_btn, "용어집 추출 프리필에 사용될 시스템 지침과 예시 대화(Few-shot history)를 편집합니다.")
        prefill_box.addWidget(self.enable_prefill_check)
        prefill_box.addWidget(self.edit_prefill_btn)

        settings_form.addRow("샘플링 비율", self._wrap(sample_row))
        settings_form.addRow("추출 온도", self._wrap(temp_row))
        settings_form.addRow("사용자 프롬프트", self.user_prompt_edit)
        settings_form.addRow(prefill_box)

        # Dynamic injection
        injection_group = QtWidgets.QGroupBox("동적 용어집 주입")
        injection_form = QtWidgets.QFormLayout(injection_group)
        self.enable_injection_check = QtWidgets.QCheckBox("동적 용어집 주입 활성화")
        TooltipQt(self.enable_injection_check, "번역 시 현재 청크와 관련된 용어집 항목을 자동으로 프롬프트에 주입합니다.")
        self.max_entries_spin = NoWheelSpinBox()
        self.max_entries_spin.setRange(1, 50)
        TooltipQt(self.max_entries_spin, "하나의 번역 청크에 주입될 용어집 항목의 최대 개수입니다.")
        self.max_chars_spin = NoWheelSpinBox()
        self.max_chars_spin.setRange(50, 10000)
        TooltipQt(self.max_chars_spin, "하나의 번역 청크에 주입될 용어집 내용의 최대 총 문자 수입니다.")
        self.max_chars_spin.setSingleStep(50)
        injection_form.addRow(self.enable_injection_check)
        injection_form.addRow("청크당 최대 항목 수", self.max_entries_spin)
        injection_form.addRow("청크당 최대 문자 수", self.max_chars_spin)

        # Display area
        display_group = QtWidgets.QGroupBox("추출된 용어집 (JSON)")
        TooltipQt(display_group, "추출되거나 불러온 용어집의 내용이 JSON 형식으로 표시됩니다.")
        display_vbox = QtWidgets.QVBoxLayout(display_group)
        self.glossary_display = QtWidgets.QPlainTextEdit()
        self.glossary_display.setReadOnly(True)
        TooltipQt(self.glossary_display, "용어집 내용입니다. 직접 편집은 불가능하며, 'JSON 저장'으로 파일 저장 후 수정할 수 있습니다.")
        display_vbox.addWidget(self.glossary_display)

        display_btn_row = QtWidgets.QHBoxLayout()
        self.load_glossary_btn = QtWidgets.QPushButton("용어집 불러오기")
        TooltipQt(self.load_glossary_btn, "기존 용어집 JSON 파일을 불러와 아래 텍스트 영역에 표시합니다.")
        self.copy_glossary_btn = QtWidgets.QPushButton("JSON 복사")
        TooltipQt(self.copy_glossary_btn, "아래 텍스트 영역에 표시된 용어집 JSON 내용을 클립보드에 복사합니다.")
        self.save_glossary_btn = QtWidgets.QPushButton("JSON 저장")
        TooltipQt(self.save_glossary_btn, "아래 텍스트 영역에 표시된 용어집 JSON 내용을 새 파일로 저장합니다.")
        self.edit_glossary_btn = QtWidgets.QPushButton("용어집 편집")
        TooltipQt(self.edit_glossary_btn, "표시된 용어집 내용을 별도의 편집기 창에서 수정합니다.")
        display_btn_row.addWidget(self.load_glossary_btn)
        display_btn_row.addWidget(self.copy_glossary_btn)
        display_btn_row.addWidget(self.save_glossary_btn)
        display_btn_row.addWidget(self.edit_glossary_btn)
        display_vbox.addLayout(display_btn_row)

        layout.addWidget(path_group)
        layout.addWidget(settings_group)
        layout.addWidget(injection_group)
        layout.addWidget(display_group, 1)
        
        # 스크롤 영역에 컨텐츠 설정
        scroll_area.setWidget(scroll_content)
        main_layout.addWidget(scroll_area)

    # ---------- signal wiring ----------
    def _wire_signals(self) -> None:
        self.extract_btn.clicked.connect(self._on_extract_clicked)
        self.stop_btn.clicked.connect(self._on_stop_clicked)
        self.progress_signal.connect(self._on_progress)
        self.status_signal.connect(self._on_status)
        self.completion_signal.connect(self._on_completion)
        self.edit_prefill_btn.clicked.connect(self._open_prefill_editor)
        self.load_glossary_btn.clicked.connect(self._load_glossary_to_display)
        self.copy_glossary_btn.clicked.connect(self._copy_glossary_json)
        self.save_glossary_btn.clicked.connect(self._save_glossary_json)
        self.edit_glossary_btn.clicked.connect(self._open_glossary_editor)

    # ---------- config ----------
    def _load_config(self) -> None:
        cfg = getattr(self.app_service, "config", {}) or {}
        self.glossary_path_edit.setText(str(cfg.get("glossary_json_path") or ""))
        
        sample_val = float(cfg.get("glossary_sampling_ratio", 10.0))
        self.sample_ratio_slider.setValue(int(sample_val * 10))
        
        temp_val = float(cfg.get("glossary_extraction_temperature", 0.3))
        self.extraction_temp_slider.setValue(int(temp_val * 100))
        
        self.user_prompt_edit.setPlainText(str(cfg.get("user_override_glossary_extraction_prompt", "")))

        self.enable_prefill_check.setChecked(bool(cfg.get("enable_glossary_prefill", False)))
        self._prefill_history = copy.deepcopy(cfg.get("glossary_prefill_cached_history", []) or [])

        self.enable_injection_check.setChecked(bool(cfg.get("enable_dynamic_glossary_injection", False)))
        self.max_entries_spin.setValue(int(cfg.get("max_glossary_entries_per_chunk_injection", 3)))
        self.max_chars_spin.setValue(int(cfg.get("max_glossary_chars_per_chunk_injection", 500)))

    def _save_config(self) -> None:
        cfg = getattr(self.app_service, "config", {}) or {}
        cfg["glossary_json_path"] = self.glossary_path_edit.text().strip() or None
        cfg["glossary_sampling_ratio"] = self.sample_ratio_slider.value() / 10.0
        cfg["glossary_extraction_temperature"] = self.extraction_temp_slider.value() / 100.0
        cfg["user_override_glossary_extraction_prompt"] = self.user_prompt_edit.toPlainText()
        cfg["enable_glossary_prefill"] = self.enable_prefill_check.isChecked()
        cfg["glossary_prefill_cached_history"] = copy.deepcopy(self._prefill_history)
        cfg["enable_dynamic_glossary_injection"] = self.enable_injection_check.isChecked()
        cfg["max_glossary_entries_per_chunk_injection"] = int(self.max_entries_spin.value())
        cfg["max_glossary_chars_per_chunk_injection"] = int(self.max_chars_spin.value())
        self.app_service.config = cfg
        try:
            self.app_service.save_app_config(cfg)
        except Exception:
            pass

    # ---------- actions ----------
    def _browse_glossary_json(self) -> None:
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "용어집 JSON 파일 선택", filter="JSON Files (*.json);;All Files (*)")
        if file_path:
            self.glossary_path_edit.setText(file_path)
            self._save_config()

    def _progress_cb(self, dto: GlossaryExtractionProgressDTO) -> None:
        self.progress_signal.emit(dto)

    def _status_cb(self, msg: str) -> None:
        self.status_signal.emit(msg)

    @asyncSlot()
    async def _on_extract_clicked(self) -> None:
        if self._extraction_task and not self._extraction_task.done():
            QtWidgets.QMessageBox.warning(self, "실행 중", "이미 용어집 추출이 실행 중입니다.")
            return

        input_files = self.app_service.config.get("input_files") or []
        if not input_files:
            QtWidgets.QMessageBox.warning(self, "입력 필요", "Settings 탭에서 입력 파일을 선택하세요.")
            return
        input_file = input_files[0]
        if not Path(input_file).exists():
            QtWidgets.QMessageBox.warning(self, "파일 없음", f"입력 파일을 찾을 수 없습니다: {input_file}")
            return

        self._save_config()
        self._stop_requested = False
        self.extract_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress_label.setText("용어집 추출 시작...")

        self._extraction_task = asyncio.create_task(
            self.app_service.extract_glossary_async(
                input_file,
                progress_callback=self._progress_cb,
                seed_glossary_path=self.glossary_path_edit.text().strip() or None,
                user_override_glossary_extraction_prompt=self.user_prompt_edit.toPlainText(),
                stop_check=lambda: self._stop_requested,
            )
        )
        try:
            result_path = await self._extraction_task
            self.completion_signal.emit(True, "용어집 추출 완료", result_path)
        except asyncio.CancelledError:
            self.completion_signal.emit(False, "취소됨", None)
            raise
        except Exception as e:
            self.completion_signal.emit(False, f"오류: {e}", None)
        finally:
            self._extraction_task = None

    @asyncSlot()
    async def _on_stop_clicked(self) -> None:
        self._stop_requested = True
        self.stop_btn.setEnabled(False)
        if self._extraction_task:
            self._extraction_task.cancel()

    # ---------- slots ----------
    @QtCore.Slot(object)
    def _on_progress(self, dto: GlossaryExtractionProgressDTO) -> None:
        msg = (
            f"{dto.current_status_message} "
            f"({dto.processed_segments}/{dto.total_segments}, 추출 항목: {dto.extracted_entries_count})"
        )
        self.progress_label.setText(msg)

    @QtCore.Slot(str)
    def _on_status(self, msg: str) -> None:
        self.progress_label.setText(msg)

    @QtCore.Slot(bool, str, object)
    def _on_completion(self, success: bool, msg: str, result_path: Optional[Path]) -> None:
        self.extract_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        if success:
            self.progress_label.setText(msg)
            if result_path:
                self.glossary_path_edit.setText(str(result_path))
                try:
                    with open(result_path, "r", encoding="utf-8") as f:
                        self._display_glossary_content(f.read())
                except Exception:
                    pass
        else:
            self.progress_label.setText(msg)

    def _display_glossary_content(self, content: str) -> None:
        self.glossary_display.setReadOnly(False)
        self.glossary_display.setPlainText(content)
        self.glossary_display.setReadOnly(True)

    def _load_glossary_to_display(self) -> None:
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "용어집 JSON 파일 선택", filter="JSON Files (*.json);;All Files (*)")
        if not file_path:
            return
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                self._display_glossary_content(f.read())
            self.glossary_path_edit.setText(file_path)
            self._save_config()
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "로드 실패", f"용어집 파일 로드 실패: {e}")

    def _copy_glossary_json(self) -> None:
        content = self.glossary_display.toPlainText().strip()
        if not content:
            QtWidgets.QMessageBox.information(self, "복사", "복사할 내용이 없습니다.")
            return
        cb = QtWidgets.QApplication.clipboard()
        cb.setText(content)
        QtWidgets.QMessageBox.information(self, "복사", "용어집 JSON이 클립보드에 복사되었습니다.")

    def _save_glossary_json(self) -> None:
        content = self.glossary_display.toPlainText().strip()
        if not content:
            QtWidgets.QMessageBox.warning(self, "저장", "저장할 내용이 없습니다.")
            return
        file_path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "용어집 JSON으로 저장", filter="JSON Files (*.json);;All Files (*)")
        if not file_path:
            return
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            QtWidgets.QMessageBox.information(self, "저장", f"저장 완료: {file_path}")
        except Exception as e:
            QtWidgets.QMessageBox.warning(self, "저장 실패", f"저장 실패: {e}")

    def _open_glossary_editor(self) -> None:
        current_json = self.glossary_display.toPlainText().strip() or "[]"
        input_files = self.app_service.config.get("input_files") if hasattr(self.app_service, "config") else []
        input_path = (input_files or [None])[0]

        updated_json = GlossaryEditorDialogQt.edit(
            parent=self,
            glossary_json_str=current_json,
            input_file_path=input_path,
        )

        if updated_json is None:
            return

        # 최신 내용을 표시하고 저장
        self._display_glossary_content(updated_json)
        self._save_config()

    def _open_prefill_editor(self) -> None:
        result = PrefillHistoryEditorDialogQt.edit(
            self,
            self._prefill_history,
            system_instruction=self.app_service.config.get("glossary_prefill_system_instruction", ""),
        )
        if result is None:
            return
        new_history, new_sys_inst = result
        self._prefill_history = new_history
        if new_sys_inst is not None:
            self.app_service.config["glossary_prefill_system_instruction"] = new_sys_inst
        self._save_config()

    # ---------- utils ----------
    def _wrap(self, layout: QtWidgets.QLayout) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        w.setLayout(layout)
        return w

    def _set_model_progress(self, active: bool) -> None:
        pass  # placeholder for API parity with Settings; not used here

