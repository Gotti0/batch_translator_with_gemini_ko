"""
PySide6 Review Tab
- Inspect and edit translated chunks
- Retranslate single chunk via executor to avoid UI blocking
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets
from qasync import asyncSlot
from tqdm import tqdm

from gui_qt.components_qt.tooltip_qt import TooltipQt
from infrastructure import file_handler
from infrastructure.file_handler import read_text_file, write_text_file
from infrastructure.logger_config import setup_logger
from utils.chunk_service import ChunkService
from utils.quality_check_service import QualityCheckService
from utils.post_processing_service import PostProcessingService
from domain.review_providers.factory import get_review_provider

# 로깅 설정 (커스텀 로거 사용)
logger = setup_logger("review_tab", log_level=logging.DEBUG, log_to_console=True, log_to_file=True)


class NumericSortProxyModel(QtCore.QSortFilterProxyModel):
    """숫자 정렬을 지원하는 커스텀 프록시 모델"""
    
    def lessThan(self, left: QtCore.QModelIndex, right: QtCore.QModelIndex) -> bool:
        """
        두 아이템 비교 (정렬용)
        
        ID 컴럼(0번)은 UserRole에 저장된 정수값으로 비교하고,
        나머지 컴럼은 기본 문자열 비교 사용
        """
        # ID 컴럼인 경우 정수 비교
        if left.column() == 0:
            left_data = left.data(QtCore.Qt.UserRole)
            right_data = right.data(QtCore.Qt.UserRole)
            
            # UserRole에 정수값이 있으면 정수 비교
            if left_data is not None and right_data is not None:
                return left_data < right_data
        
        # 나머지는 기본 문자열 비교
        return super().lessThan(left, right)


class RetranslateOptionDialog(QtWidgets.QDialog):
    def __init__(self, target_count: int, is_mass: bool = True, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("재번역 설정")
        self.resize(350, 200)
        
        layout = QtWidgets.QVBoxLayout(self)
        
        if is_mass:
            msg = f"총 {target_count}개의 문제 청크가 발견되었습니다.\n"
        else:
            msg = f"선택한 청크(#{target_count})를 재번역합니다.\n"
        msg += "재번역 시, 청크를 얼마나 잘게 쪼갤까요?"
        layout.addWidget(QtWidgets.QLabel(msg))
        
        self.radio_lv1 = QtWidgets.QRadioButton("1레벨 (절반으로 분할)")
        self.radio_lv2 = QtWidgets.QRadioButton("2레벨 (1/4로 분할)")
        self.radio_lv3 = QtWidgets.QRadioButton("3레벨 (1천자 고정으로 분할)")
        self.radio_lv1.setChecked(True)
        
        layout.addWidget(self.radio_lv1)
        layout.addWidget(self.radio_lv2)
        layout.addWidget(self.radio_lv3)
        
        layout.addStretch(1)
        btn_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        layout.addWidget(btn_box)

    def get_selected_level(self) -> int:
        if self.radio_lv2.isChecked(): return 2
        if self.radio_lv3.isChecked(): return 3
        return 1


class ReviewTabQt(QtWidgets.QWidget):
    status_signal = QtCore.Signal(str)

    def __init__(self, app_service, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.app_service = app_service
        self.chunk_service = ChunkService()
        self.quality_service = QualityCheckService()
        self.post_processing_service = PostProcessingService()
        self._loop = asyncio.get_event_loop()

        # state
        self.current_input_file: Optional[str] = None
        self.current_metadata: Optional[Dict[str, Any]] = None
        self.source_chunks: Dict[int, str] = {}
        self.translated_chunks: Dict[int, str] = {}
        self.suspicious_chunks: List[Dict[str, Any]] = []
        self._source_cache: Dict[str, Dict[int, str]] = {}
        self._source_cache_info: Dict[str, Tuple[float, int]] = {}
        self._tqdm_stream = None  # LogTab에서 주입받을 TQDM 스트림
        self.provider = None # 다형성을 위한 Review Provider

        # ui refs
        self.file_path_edit: Optional[QtWidgets.QLineEdit] = None
        self.table: Optional[QtWidgets.QTableView] = None
        self.model: Optional[QtGui.QStandardItemModel] = None
        self.proxy: Optional[QtCore.QSortFilterProxyModel] = None
        self.source_view: Optional[QtWidgets.QPlainTextEdit] = None
        self.trans_view: Optional[QtWidgets.QPlainTextEdit] = None
        self.status_label: Optional[QtWidgets.QLabel] = None
        self.stats_label: Optional[QtWidgets.QLabel] = None

        self._build_ui()
        self._wire_signals()
        self.status_signal.connect(self._set_status)

    def set_tqdm_stream(self, tqdm_stream) -> None:
        """LogTab에서 TQDM 스트림 주입"""
        self._tqdm_stream = tqdm_stream

    # ---------- UI ----------
    def _build_ui(self) -> None:
        # 메인 레이아웃에 스크롤 영역 추가
        main_layout = QtWidgets.QVBoxLayout(self)
        scroll_area = QtWidgets.QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        
        # 스크롤 가능한 컨텐츠 위젟
        scroll_content = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(scroll_content)

        file_group = QtWidgets.QGroupBox("입력 파일 선택")
        file_form = QtWidgets.QHBoxLayout(file_group)
        self.file_path_edit = QtWidgets.QLineEdit()
        TooltipQt(self.file_path_edit, "검토할 번역 파일의 경로입니다.")
        browse_btn = QtWidgets.QPushButton("찾아보기")
        TooltipQt(browse_btn, "검토할 파일을 선택합니다.")
        sync_btn = QtWidgets.QPushButton("설정탭 동기화")
        TooltipQt(sync_btn, "설정 탭의 입력 파일 경로를 가져옵니다.")
        load_btn = QtWidgets.QPushButton("로드")
        TooltipQt(load_btn, "선택한 파일의 번역 내용을 불러옵니다.")
        refresh_btn = QtWidgets.QPushButton("새로고침")
        TooltipQt(refresh_btn, "현재 파일을 다시 불러옵니다.")
        for btn in (browse_btn, sync_btn, load_btn, refresh_btn):
            btn.setMinimumWidth(90)
        file_form.addWidget(QtWidgets.QLabel("입력 파일:"))
        file_form.addWidget(self.file_path_edit, 1)
        file_form.addWidget(browse_btn)
        file_form.addWidget(sync_btn)
        file_form.addWidget(load_btn)
        file_form.addWidget(refresh_btn)
        layout.addWidget(file_group)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)

        left_widget = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_widget)

        self.stats_label = QtWidgets.QLabel("청크: 0개 | 성공: 0 | 실패: 0 | 의심: 0")
        TooltipQt(self.stats_label, "현재 로드된 파일의 번역 통계 정보입니다.")
        
        # 필터링 UI (신규 Phase 4)
        filter_layout = QtWidgets.QHBoxLayout()
        filter_layout.addWidget(QtWidgets.QLabel("필터:"))
        self.filter_combo = QtWidgets.QComboBox()
        self.filter_combo.addItems(["모두 보기", "✅ 성공만", "❌ 실패만", "⚠️ 의심(누락/환각)만"])
        self.filter_combo.currentTextChanged.connect(self._apply_filter)
        filter_layout.addWidget(self.filter_combo)
        filter_layout.addStretch(1)
        
        left_layout.addWidget(self.stats_label)
        left_layout.addLayout(filter_layout)

        self.model = QtGui.QStandardItemModel(0, 6)
        self.model.setHorizontalHeaderLabels(["ID", "상태", "원문", "번역", "비율", "Z-Score"])
        self.proxy = NumericSortProxyModel(self)  # 정수 정렬 지원 프록시 모델
        self.proxy.setSourceModel(self.model)
        self.proxy.setSortCaseSensitivity(QtCore.Qt.CaseInsensitive)

        self.table = QtWidgets.QTableView()
        TooltipQt(self.table, "번역 청크 목록입니다. 클릭하여 상세 내용을 확인하거나 다중 선택할 수 있습니다.")
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        # ID 컬럼 기본 정렬: 오름차순
        self.table.sortByColumn(0, QtCore.Qt.AscendingOrder)
        left_layout.addWidget(self.table, 1)

        btns = QtWidgets.QVBoxLayout()
        self.mass_retranslate_btn = QtWidgets.QPushButton("의심 청크 일괄 재번역")
        self.mass_retranslate_btn.setObjectName("PrimaryButton")
        TooltipQt(self.mass_retranslate_btn, "품질 이슈(누락/환각)가 있거나 실패한 모든 청크를 정밀 모드(강제 분할)로 일괄 재번역합니다.")
        
        self.retranslate_btn = QtWidgets.QPushButton("선택 청크 재번역")
        TooltipQt(self.retranslate_btn, "선택한 청크만 다시 번역합니다.")
        
        self.edit_btn = QtWidgets.QPushButton("수동 수정")
        TooltipQt(self.edit_btn, "선택한 청크의 번역을 수동으로 수정합니다.")
        self.reset_btn = QtWidgets.QPushButton("초기화")
        TooltipQt(self.reset_btn, "수정 내용을 취소하고 원래 번역으로 돌립니다.")
        self.confirm_btn = QtWidgets.QPushButton("확정")
        TooltipQt(self.confirm_btn, "선택한 청크의 번역을 확정하고 파일에 저장합니다.")
        self.copy_src_btn = QtWidgets.QPushButton("원문 복사")
        TooltipQt(self.copy_src_btn, "선택한 청크의 원문을 클립보드에 복사합니다.")
        self.copy_trans_btn = QtWidgets.QPushButton("번역 복사")
        TooltipQt(self.copy_trans_btn, "선택한 청크의 번역을 클립보드에 복사합니다.")
        
        for b in (self.mass_retranslate_btn, self.retranslate_btn, self.edit_btn, self.reset_btn, self.confirm_btn, self.copy_src_btn, self.copy_trans_btn):
            b.setMinimumHeight(32)
            btns.addWidget(b)
        btns.addStretch(1)
        btn_container = QtWidgets.QWidget()
        btn_container.setLayout(btns)

        left_buttons_split = QtWidgets.QHBoxLayout()
        left_buttons_split.addWidget(left_widget, 4)
        left_buttons_split.addWidget(btn_container, 1)
        left_panel = QtWidgets.QWidget()
        left_panel.setLayout(left_buttons_split)

        splitter.addWidget(left_panel)

        right_widget = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_widget)

        src_group = QtWidgets.QGroupBox("원문")
        src_layout = QtWidgets.QVBoxLayout(src_group)
        self.source_view = QtWidgets.QPlainTextEdit()
        self.source_view.setReadOnly(True)
        TooltipQt(self.source_view, "선택한 청크의 원문 내용입니다.")
        src_layout.addWidget(self.source_view)

        trans_group = QtWidgets.QGroupBox("번역문 (편집 가능)")
        trans_layout = QtWidgets.QVBoxLayout(trans_group)
        self.trans_view = QtWidgets.QPlainTextEdit()
        # Phase 4: 직접 편집 가능하게 설정
        self.trans_view.setReadOnly(False)
        TooltipQt(self.trans_view, "선택한 청크의 번역 내용입니다. 여기서 직접 수정하고 '저장' 버튼을 누를 수 있습니다.")
        trans_layout.addWidget(self.trans_view)

        # 사이드바 저장 버튼 추가 (Phase 4)
        self.quick_save_btn = QtWidgets.QPushButton("현재 변경사항 저장")
        self.quick_save_btn.setObjectName("PrimaryButton")
        self.quick_save_btn.clicked.connect(self._on_quick_save_clicked)
        trans_layout.addWidget(self.quick_save_btn)

        right_layout.addWidget(src_group, 1)
        right_layout.addWidget(trans_group, 1)

        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        layout.addWidget(splitter, 1)

        bottom = QtWidgets.QHBoxLayout()
        self.status_label = QtWidgets.QLabel("파일을 선택하고 로드 버튼을 클릭하세요.")
        TooltipQt(self.status_label, "현재 작업의 상태를 표시합니다.")
        bottom.addWidget(self.status_label, 1)
        self.final_btn = QtWidgets.QPushButton("최종 파일 생성")
        TooltipQt(self.final_btn, "모든 번역 청크를 병합하여 최종 파일을 생성합니다.")
        self.integrity_btn = QtWidgets.QPushButton("무결성 검사")
        TooltipQt(self.integrity_btn, "번역 파일의 무결성을 검사하고 문제를 표시합니다.")
        bottom.addWidget(self.integrity_btn)
        bottom.addWidget(self.final_btn)
        layout.addLayout(bottom)
        
        # 스크롤 영역에 컨텐츠 설정
        scroll_area.setWidget(scroll_content)
        main_layout.addWidget(scroll_area)

        # store buttons for handlers
        self.browse_btn = browse_btn
        self.sync_btn = sync_btn
        self.load_btn = load_btn
        self.refresh_btn = refresh_btn

    def _wire_signals(self) -> None:
        self.browse_btn.clicked.connect(self._browse_file)
        self.sync_btn.clicked.connect(self._sync_from_settings)
        self.load_btn.clicked.connect(self._on_load_clicked)
        self.refresh_btn.clicked.connect(self._on_refresh_clicked)
        self.mass_retranslate_btn.clicked.connect(self._on_mass_retranslate_clicked)
        self.retranslate_btn.clicked.connect(self._on_retranslate_clicked)
        self.edit_btn.clicked.connect(self._on_edit_clicked)
        self.reset_btn.clicked.connect(self._on_reset_clicked)
        self.confirm_btn.clicked.connect(self._on_confirm_clicked)
        self.copy_src_btn.clicked.connect(self._on_copy_source)
        self.copy_trans_btn.clicked.connect(self._on_copy_translated)
        self.final_btn.clicked.connect(self._on_generate_final)
        self.integrity_btn.clicked.connect(self._on_check_integrity)
        if self.table:
            sel_model = self.table.selectionModel()
            if sel_model:
                sel_model.selectionChanged.connect(self._on_selection_changed)

    # ---------- helpers ----------
    def update_theme(self, theme: str) -> None:
        """
        테마 변경 시 호출되는 메서드
        
        Args:
            theme: "dark" 또는 "light"
        """
        # 현재 로드된 데이터가 있으면 테이블 다시 렌더링
        if self.current_metadata and self.model:
            # 현재 선택 상태 저장
            selected_rows = self._selected_indices()
            
            # 테이블 다시 그리기
            self._populate_table()
            
            # 선택 상태 복원
            if selected_rows and self.table:
                selection_model = self.table.selectionModel()
                for row_idx in selected_rows:
                    # proxy 모델에서 해당 ID를 찾아 선택
                    for i in range(self.proxy.rowCount()):
                        source_idx = self.proxy.mapToSource(self.proxy.index(i, 0))
                        item = self.model.item(source_idx.row(), 0)
                        if item and item.data(QtCore.Qt.UserRole) == row_idx:
                            self.table.selectRow(i)
                            break
            
            logger.debug(f"ReviewTab 테마 업데이트됨: {theme}")
    
    def _is_dark_theme(self) -> bool:
        """시스템 테마가 다크 모드인지 감지"""
        palette = QtWidgets.QApplication.palette()
        bg_color = palette.color(QtGui.QPalette.Window)
        # 배경색의 밝기로 다크 테마 판단 (밝기 < 128이면 다크)
        return bg_color.lightness() < 128
    
    def _get_color_palette(self, status_type: str) -> Tuple[QtGui.QColor, QtGui.QColor]:
        """
        상태별 배경색과 텍스트 색상을 테마에 맞게 반환
        
        Args:
            status_type: 'pending', 'success', 'warning_omission', 'warning_hallucination', 'failed'
        
        Returns:
            (배경색, 텍스트색) 튜플
        """
        is_dark = self._is_dark_theme()
        
        if is_dark:
            # 다크 테마용 색상 (어두운 배경 + 밝은 텍스트)
            palettes = {
                'pending': (QtGui.QColor("#3a3a3a"), QtGui.QColor("#b0b0b0")),      # 어두운 회색 + 밝은 회색
                'success': (QtGui.QColor("#1e4620"), QtGui.QColor("#90ee90")),      # 어두운 초록 + 밝은 초록
                'warning_omission': (QtGui.QColor("#4a3800"), QtGui.QColor("#ffd700")),  # 어두운 노랑 + 밝은 노랑
                'warning_hallucination': (QtGui.QColor("#3d2a4d"), QtGui.QColor("#dda0dd")),  # 어두운 보라 + 밝은 보라
                'failed': (QtGui.QColor("#4a1a1a"), QtGui.QColor("#ff6b6b")),       # 어두운 빨강 + 밝은 빨강
            }
        else:
            # 라이트 테마용 색상 (밝은 배경 + 어두운 텍스트)
            palettes = {
                'pending': (QtGui.QColor("#e2e3e5"), QtGui.QColor("#212529")),      # 밝은 회색 + 어두운 텍스트
                'success': (QtGui.QColor("#d4edda"), QtGui.QColor("#155724")),      # 밝은 초록 + 어두운 초록
                'warning_omission': (QtGui.QColor("#fff3cd"), QtGui.QColor("#856404")),  # 밝은 노랑 + 어두운 노랑
                'warning_hallucination': (QtGui.QColor("#e2d5f1"), QtGui.QColor("#5a2d82")),  # 밝은 보라 + 어두운 보라
                'failed': (QtGui.QColor("#f8d7da"), QtGui.QColor("#721c24")),       # 밝은 빨강 + 어두운 빨강
            }
        
        return palettes.get(status_type, (QtGui.QColor("#ffffff"), QtGui.QColor("#000000")))
    
    def _set_status(self, msg: str) -> None:
        if self.status_label:
            self.status_label.setText(msg)

    def _set_busy(self, busy: bool) -> None:
        for btn in (
            self.load_btn, self.refresh_btn, self.retranslate_btn, self.edit_btn,
            self.reset_btn, self.confirm_btn, self.copy_src_btn, self.copy_trans_btn,
            self.final_btn, self.integrity_btn
        ):
            btn.setEnabled(not busy)

    def _browse_file(self) -> None:
        file_path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "입력 파일 선택", filter="Text Files (*.txt);;All Files (*)")
        if file_path and self.file_path_edit:
            self.file_path_edit.setText(file_path)

    def _sync_from_settings(self) -> None:
        files = self.app_service.config.get("input_files") if self.app_service and self.app_service.config else []
        if files:
            if self.file_path_edit:
                self.file_path_edit.setText(files[0])
            self._set_status(f"설정 탭 동기화: {Path(files[0]).name}")
        else:
            QtWidgets.QMessageBox.warning(self, "경고", "설정 탭에 선택된 입력 파일이 없습니다.")

    @asyncSlot()
    async def _on_load_clicked(self) -> None:
        if not self.file_path_edit:
            return
        path = self.file_path_edit.text().strip()
        await self._load_metadata_from_path(path)

    @asyncSlot()
    async def _on_refresh_clicked(self) -> None:
        if not self.current_input_file:
            QtWidgets.QMessageBox.information(self, "정보", "먼저 파일을 로드하세요.")
            return
        await self._load_metadata_from_path(self.current_input_file, silent=True)

    # ---------- data load ----------
    def _get_cache_key(self, file_path: str) -> Tuple[float, int]:
        try:
            p = Path(file_path)
            stat = p.stat()
            return (stat.st_mtime, stat.st_size)
        except Exception:
            return (0, 0)

    def _load_source_chunks(self, file_path: str) -> Dict[int, str]:
        if file_path in self._source_cache and file_path in self._source_cache_info:
            if self._source_cache_info[file_path] == self._get_cache_key(file_path):
                return self._source_cache[file_path]

        content = read_text_file(file_path)
        if not content:
            return {}
        chunk_size = 6000
        if self.app_service and self.app_service.config:
            chunk_size = self.app_service.config.get("chunk_size", 6000)
        chunks_list = self.chunk_service.create_chunks_from_file_content(content, chunk_size)
        result = {i: chunk for i, chunk in enumerate(chunks_list)}
        self._source_cache[file_path] = result
        self._source_cache_info[file_path] = self._get_cache_key(file_path)
        return result

    def _get_translated_chunked_file_path(self, input_file_path: str) -> Path:
        """
        청크 백업 파일 경로 반환
        
        - 입력: /path/to/file.txt
        - 출력: /path/to/file_translated_chunked.txt
        """
        p = Path(input_file_path)
        return p.parent / f"{p.stem}_translated_chunked.txt"

    def _get_final_output_file_path(self, input_file_path: str) -> Path:
        p = Path(input_file_path)
        return p.parent / f"{p.stem}_translated{p.suffix}"

    def _load_metadata_sync(self, file_path: str):
        """
        메타데이터 및 청크 동기 로드 (Provider 다형성 적용)
        """
        provider = get_review_provider(file_path, self.app_service)
        metadata = provider.load_metadata(file_path)
        if not metadata:
            raise ValueError("메타데이터가 없습니다. 먼저 번역을 실행하세요.")
        
        source_chunks = provider.load_source_chunks(file_path)
        translated_chunks = provider.load_translated_chunks(file_path)
        
        suspicious = self.quality_service.analyze_translation_quality(metadata)
        
        return metadata, source_chunks, translated_chunks, suspicious, provider

    async def _load_metadata_from_path(self, file_path: str, silent: bool = False) -> None:
        if not file_path:
            if not silent:
                QtWidgets.QMessageBox.warning(self, "경고", "파일 경로를 입력하세요.")
            return
        if not Path(file_path).exists():
            QtWidgets.QMessageBox.critical(self, "오류", f"파일이 존재하지 않습니다: {file_path}")
            return

        self._set_busy(True)
        self._set_status("데이터 로드 중...")
        try:
            metadata, source, translated, suspicious, provider = await self._loop.run_in_executor(
                None, lambda: self._load_metadata_sync(file_path)
            )
            self.provider = provider
            self.current_input_file = file_path
            self.current_metadata = metadata
            self.source_chunks = source
            self.translated_chunks = translated
            self.suspicious_chunks = suspicious
            self._populate_table()
            self._update_statistics()
            self._set_status(f"로드 완료: {Path(file_path).name}")
            if not silent:
                QtWidgets.QMessageBox.information(self, "정보", "데이터 로드 완료")
        except Exception as e:
            if not silent:
                QtWidgets.QMessageBox.critical(self, "오류", f"데이터 로드 실패: {e}")
            self._set_status(f"로드 실패: {e}")
        finally:
            self._set_busy(False)

    def _make_item(self, text: str, align_right: bool = False, sort_value: Optional[int] = None) -> QtGui.QStandardItem:
        """
        테이블 아이템 생성
        
        Args:
            text: 표시할 텍스트
            align_right: 오른쪽 정렬 여부
            sort_value: 정렬용 정수값 (설정 시 UserRole에 저장)
        """
        item = QtGui.QStandardItem(text)
        align = QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter if align_right else QtCore.Qt.AlignCenter
        item.setTextAlignment(align)
        # 정렬용 정수값 저장 (숫자 정렬을 위해)
        if sort_value is not None:
            item.setData(sort_value, QtCore.Qt.UserRole)
        return item

    def _populate_table(self) -> None:
        if not self.model:
            return
        self.model.removeRows(0, self.model.rowCount())
        if not self.current_metadata:
            return

        translated_chunks = self.current_metadata.get("translated_chunks", {})
        failed_chunks = self.current_metadata.get("failed_chunks", {})
        suspicious_map = {}
        for susp in self.suspicious_chunks:
            idx = susp.get("chunk_index")
            if idx is not None:
                suspicious_map[idx] = susp

        total = self.current_metadata.get("total_chunks", 0)
        for i in range(total):
            idx_str = str(i)
            status = "⏳"
            status_type = 'pending'
            src_len = "-"
            trans_len = "-"
            ratio = "-"
            z_score = "-"

            if idx_str in translated_chunks:
                info = translated_chunks[idx_str]
                if isinstance(info, dict):
                    raw_src = info.get("source_length")
                    raw_trans = info.get("translated_length")
                    raw_ratio = info.get("ratio")
                    
                    if (raw_src is None or raw_src == "-") and i in self.source_chunks:
                        raw_src = len(self.source_chunks[i])
                    if (raw_trans is None or raw_trans == "-") and i in self.translated_chunks:
                        raw_trans = len(self.translated_chunks[i])
                    if (raw_ratio is None or raw_ratio == "-") and raw_src and raw_trans:
                        try:
                            s_val, t_val = int(raw_src), int(raw_trans)
                            if s_val > 0:
                                raw_ratio = t_val / s_val
                        except (ValueError, TypeError):
                            pass
                            
                    src_len = str(raw_src) if raw_src is not None else "-"
                    trans_len = str(raw_trans) if raw_trans is not None else "-"
                    if isinstance(raw_ratio, float):
                        ratio = f"{raw_ratio:.2f}"
                    else:
                        ratio = str(raw_ratio) if raw_ratio is not None else "-"
                if i in suspicious_map:
                    issue = suspicious_map[i].get("issue_type", "")
                    z = suspicious_map[i].get("z_score", 0)
                    z_score = f"{z:.2f}"
                    if issue == "omission":
                        status = "⚠️ 누락"
                        status_type = 'warning_omission'
                    elif issue == "hallucination":
                        status = "⚠️ 환각"
                        status_type = 'warning_hallucination'
                    else:
                        status = "✅"
                        status_type = 'success'
                else:
                    status = "✅"
                    status_type = 'success'
            elif idx_str in failed_chunks:
                status = "❌"
                status_type = 'failed'
            
            # 테마별 동적 색상 적용
            bg_color, fg_color = self._get_color_palette(status_type)

            row_items = [
                self._make_item(str(i), sort_value=i),  # ID: 정수값 저장하여 숫자 정렬
                self._make_item(status),
                self._make_item(src_len, True),
                self._make_item(trans_len, True),
                self._make_item(ratio, True),
                self._make_item(z_score, True),
            ]
            for it in row_items:
                it.setData(bg_color, QtCore.Qt.BackgroundRole)
                it.setData(fg_color, QtCore.Qt.ForegroundRole)
            self.model.appendRow(row_items)

        if self.table:
            self.table.resizeColumnsToContents()

    def _update_statistics(self) -> None:
        if not self.stats_label or not self.current_metadata:
            return
        total = self.current_metadata.get("total_chunks", 0)
        success = len(self.current_metadata.get("translated_chunks", {}))
        failed = len(self.current_metadata.get("failed_chunks", {}))
        suspicious = len(self.suspicious_chunks)
        self.stats_label.setText(f"청크: {total}개 | 성공: {success} | 실패: {failed} | 의심: {suspicious}")

    def _selected_indices(self) -> List[int]:
        if not self.table:
            return []
        indexes = self.table.selectionModel().selectedRows()
        result = []
        for idx in indexes:
            try:
                source_row = self.proxy.mapToSource(idx)
                item = self.model.item(source_row.row(), 0)
                # UserRole에 저장된 정수값 직접 조회
                val = item.data(QtCore.Qt.UserRole)
                if val is not None:
                    result.append(val)
                else:
                    # 폴백: 텍스트로 변환
                    result.append(int(item.text()))
            except Exception:
                pass
        return sorted(set(result))

    def _selected_index_single(self) -> Optional[int]:
        indices = self._selected_indices()
        if not indices:
            QtWidgets.QMessageBox.warning(self, "경고", "청크를 선택하세요.")
            return None
        if len(indices) > 1:
            QtWidgets.QMessageBox.warning(self, "경고", "하나의 청크만 선택하세요.")
            return None
        return indices[0]

    def _on_selection_changed(self, *args) -> None:
        if not self.table:
            return
        indexes = self.table.selectionModel().selectedRows()
        if not indexes:
            return
        try:
            source_row = self.proxy.mapToSource(indexes[0])
            idx_val = int(self.model.item(source_row.row(), 0).text())
            self._show_preview(idx_val)
        except Exception:
            pass

    def _show_preview(self, chunk_idx: int) -> None:
        if self.source_view:
            self.source_view.setPlainText(self.source_chunks.get(chunk_idx, "(원문을 찾을 수 없습니다)"))
        if self.trans_view:
            self.trans_view.setPlainText(self.translated_chunks.get(chunk_idx, "(번역문을 찾을 수 없습니다)"))

    # ---------- actions ----------
    @asyncSlot()
    async def _on_mass_retranslate_clicked(self) -> None:
        if not self.current_input_file or not self.current_metadata:
            return
            
        # 1. 대상 식별 (실패 또는 의심 청크)
        failed_indices = [int(k) for k in self.current_metadata.get("failed_chunks", {}).keys()]
        suspicious_indices = [s.get("chunk_index") for s in self.suspicious_chunks if s.get("chunk_index") is not None]
        target_indices = sorted(list(set(failed_indices + suspicious_indices)))
        
        if not target_indices:
            QtWidgets.QMessageBox.information(self, "정보", "재번역이 필요한 의심/실패 청크가 없습니다.")
            return
            
        # 2. 사용자 확인 (커스텀 다이얼로그)
        dlg = RetranslateOptionDialog(len(target_indices), is_mass=True, parent=self)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
            
        split_level = dlg.get_selected_level()
            
        self._set_busy(True)
        self._set_status(f"일괄 재번역 시작... (0/{len(target_indices)})")
        
        # 3. 병렬 처리 설정 (Semaphore)
        # config.json의 max_workers가 있다면 사용, 없으면 안전하게 3개
        max_parallel = self.app_service.config.get("max_workers", 3) if self.app_service else 3
        semaphore = asyncio.Semaphore(max_parallel)
        
        async def retranslate_task(idx: int) -> bool:
            async with semaphore:
                try:
                    chunk_file_path = self._get_translated_chunked_file_path(self.current_input_file)
                    
                    src_text = self.source_chunks.get(idx, "")
                    if hasattr(self, 'provider') and self.provider:
                        new_trans = await self.provider.retranslate_chunk(str(idx), src_text, split_level=split_level)
                        if new_trans:
                            self._save_chunk_translation(idx, new_trans)
                            return True
                        return False

                    success, _ = await self.app_service.translate_single_chunk_with_force_split(
                        self.current_input_file,
                        str(chunk_file_path),
                        idx,
                        split_level=split_level
                    )
                    return success
                except Exception as e:
                    logger.error(f"청크 #{idx} 일괄 재번역 중 오류: {e}")
                    return False

        # 4. 작업 실행 및 진행 상황 업데이트
        tasks = [retranslate_task(idx) for idx in target_indices]
        completed = 0
        
        # tqdm 인스턴스 생성 (LogTab의 스트림 사용)
        pbar = None
        if self._tqdm_stream:
            pbar = tqdm(total=len(target_indices), file=self._tqdm_stream, desc="일괄 재번역")
        
        for coro in asyncio.as_completed(tasks):
            success = await coro
            completed += 1
            if pbar:
                pbar.update(1)
            self._set_status(f"일괄 재번역 진행 중... ({completed}/{len(target_indices)})")
            
        if pbar:
            pbar.close()
            
        # 5. 최종 결과 알림 및 UI 갱신
        logger.info(f"🎯 일괄 재번역 전체 처리 완료 ({len(target_indices)}개 처리됨)")
        self._set_status(f"일괄 재번역 완료 ({len(target_indices)}개 처리됨)")
        QtWidgets.QMessageBox.information(self, "완료", f"{len(target_indices)}개의 청크에 대한 정밀 재번역이 완료되었습니다.")
        await self._load_metadata_from_path(self.current_input_file, silent=True)
        self._set_busy(False)

    @asyncSlot()
    async def _on_retranslate_clicked(self) -> None:
        selected_indices = self._selected_indices()
        if not selected_indices:
            QtWidgets.QMessageBox.warning(self, "경고", "재번역할 청크를 하나 이상 선택하세요.")
            return
        if not self.current_input_file:
            return
        if not self.app_service or not self.app_service.translation_service:
            QtWidgets.QMessageBox.critical(self, "오류", "번역 서비스가 초기화되지 않았습니다.")
            return
        if self.app_service.current_translation_task and not self.app_service.current_translation_task.done():
            QtWidgets.QMessageBox.warning(self, "경고", "현재 번역 작업이 진행 중입니다. 완료 후 시도하세요.")
            return
            
        # 다중 선택 여부에 따라 메시지가 다르게 나오도록 is_mass 활용
        is_multiple = len(selected_indices) > 1
        dlg = RetranslateOptionDialog(
            target_count=len(selected_indices) if is_multiple else selected_indices[0], 
            is_mass=is_multiple, 
            parent=self
        )
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
            
        split_level = dlg.get_selected_level()

        self._set_busy(True)
        self._set_status(f"선택 청크 재번역 시작... (0/{len(selected_indices)})")

        # 병렬 처리 설정
        max_parallel = self.app_service.config.get("max_workers", 3) if self.app_service else 3
        semaphore = asyncio.Semaphore(max_parallel)
        
        chunk_file_path = self._get_translated_chunked_file_path(self.current_input_file)
        
        async def retranslate_task(idx: int) -> bool:
            async with semaphore:
                try:
                    def progress_cb(msg: str) -> None:
                        self.status_signal.emit(msg)
                    
                    src_text = self.source_chunks.get(idx, "")
                    if hasattr(self, 'provider') and self.provider:
                        new_trans = await self.provider.retranslate_chunk(str(idx), src_text, split_level=split_level)
                        if new_trans:
                            self._save_chunk_translation(idx, new_trans)
                            return True
                        return False
                        
                    success, _ = await self.app_service.translate_single_chunk_with_force_split(
                        self.current_input_file,
                        str(chunk_file_path),
                        idx,
                        split_level=split_level,
                        progress_callback=progress_cb
                    )
                    return success
                except Exception as e:
                    logger.error(f"청크 #{idx} 재번역 중 오류: {e}")
                    return False

        # tqdm 인스턴스 생성 (LogTab의 스트림 사용)
        pbar = None
        if self._tqdm_stream:
            pbar = tqdm(total=len(selected_indices), file=self._tqdm_stream, desc="선택 청크 재번역")

        tasks = [retranslate_task(idx) for idx in selected_indices]
        completed = 0
        success_count = 0

        try:
            for coro in asyncio.as_completed(tasks):
                success = await coro
                completed += 1
                if success:
                    success_count += 1
                if pbar:
                    pbar.update(1)
                self._set_status(f"선택 청크 재번역 진행 중... ({completed}/{len(selected_indices)})")
                
            self._set_status(f"재번역 완료 ({success_count}/{len(selected_indices)}개 성공)")
            logger.info(f"🎯 선택 청크 재번역 처리 완료 ({success_count}/{len(selected_indices)}개 성공)")
            QtWidgets.QMessageBox.information(self, "성공", f"총 {success_count}개의 청크 재번역이 완료되었습니다.")
            await self._load_metadata_from_path(self.current_input_file, silent=True)
        except Exception as e:
            self._set_status(f"재번역 오류: {e}")
            QtWidgets.QMessageBox.critical(self, "재번역 오류", str(e))
        finally:
            if pbar:
                pbar.close()
            self._set_busy(False)

    def _on_edit_clicked(self) -> None:
        chunk_idx = self._selected_index_single()
        if chunk_idx is None or not self.current_input_file:
            return

        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle(f"청크 #{chunk_idx} 수정")
        dlg.resize(700, 520)
        vbox = QtWidgets.QVBoxLayout(dlg)

        vbox.addWidget(QtWidgets.QLabel("원문"))
        src_edit = QtWidgets.QPlainTextEdit()
        src_edit.setPlainText(self.source_chunks.get(chunk_idx, ""))
        src_edit.setReadOnly(True)
        vbox.addWidget(src_edit)

        vbox.addWidget(QtWidgets.QLabel("번역문 (수정 가능)"))
        trans_edit = QtWidgets.QPlainTextEdit()
        trans_edit.setPlainText(self.translated_chunks.get(chunk_idx, ""))
        vbox.addWidget(trans_edit, 1)

        btn_box = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Save | QtWidgets.QDialogButtonBox.Cancel)
        vbox.addWidget(btn_box)

        def on_save() -> None:
            new_text = trans_edit.toPlainText().strip()
            if not new_text:
                QtWidgets.QMessageBox.warning(dlg, "경고", "번역문을 입력하세요.")
                return
            try:
                self._save_chunk_translation(chunk_idx, new_text)
                QtWidgets.QMessageBox.information(dlg, "성공", "저장되었습니다.")
                dlg.accept()
                asyncio.create_task(self._load_metadata_from_path(self.current_input_file, silent=True))
            except Exception as e:
                QtWidgets.QMessageBox.critical(dlg, "오류", f"저장 실패: {e}")

        btn_box.accepted.connect(on_save)
        btn_box.rejected.connect(dlg.reject)
        dlg.exec()

    def _save_chunk_translation(self, chunk_idx: int, translation: str) -> None:
        if not self.current_input_file:
            raise ValueError("파일이 로드되지 않았습니다.")
        self.translated_chunks[chunk_idx] = translation
        
        if hasattr(self, 'provider') and self.provider:
            self.provider.save_translated_chunk(self.current_input_file, chunk_idx, translation, self.translated_chunks)
        else:
            translated_path = self._get_translated_chunked_file_path(self.current_input_file)
            file_handler.save_merged_chunks_to_file(translated_path, self.translated_chunks)
        source_len = len(self.source_chunks.get(chunk_idx, ""))
        trans_len = len(translation)
        file_handler.update_metadata_for_chunk_completion(
            self.current_input_file,
            chunk_idx,
            source_length=source_len,
            translated_length=trans_len,
        )

    def _on_reset_clicked(self) -> None:
        chunk_indices = self._selected_indices()
        if not chunk_indices or not self.current_input_file:
            return
        count = len(chunk_indices)
        preview = ", ".join(f"#{c}" for c in chunk_indices[:5])
        if count > 5:
            preview += f" 외 {count - 5}개"
        if QtWidgets.QMessageBox.question(
            self,
            "초기화 확인",
            f"선택한 {count}개 청크의 번역 기록을 삭제하시겠습니까?\n대상: {preview}",
        ) != QtWidgets.QMessageBox.Yes:
            return
        try:
            translated = self.current_metadata.get("translated_chunks", {}) if self.current_metadata else {}
            failed = self.current_metadata.get("failed_chunks", {}) if self.current_metadata else {}
            for idx in chunk_indices:
                key = str(idx)
                translated.pop(key, None)
                failed.pop(key, None)
            if self.current_metadata is not None:
                self.current_metadata["translated_chunks"] = translated
                self.current_metadata["failed_chunks"] = failed
                self.current_metadata["status"] = "in_progress"
                file_handler.save_metadata(self.current_input_file, self.current_metadata)
            QtWidgets.QMessageBox.information(self, "성공", f"{count}개 청크 초기화 완료")
            asyncio.create_task(self._load_metadata_from_path(self.current_input_file, silent=True))
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "오류", f"초기화 실패: {e}")

    def _on_confirm_clicked(self) -> None:
        chunk_indices = self._selected_indices()
        if not chunk_indices:
            return
        before = len(self.suspicious_chunks)
        self.suspicious_chunks = [s for s in self.suspicious_chunks if s.get("chunk_index") not in chunk_indices]
        removed = before - len(self.suspicious_chunks)
        self._populate_table()
        self._update_statistics()
        self._set_status(f"{len(chunk_indices)}개 청크 확정 (경고 해제 {removed}개)")

    def _on_copy_source(self) -> None:
        indices = self._selected_indices()
        if not indices:
            return
        texts = [self.source_chunks.get(i, "") for i in indices if i in self.source_chunks]
        if texts:
            QtWidgets.QApplication.clipboard().setText("\n\n".join(texts))
            self._set_status(f"{len(texts)}개 원문 복사 완료")

    def _on_copy_translated(self) -> None:
        indices = self._selected_indices()
        if not indices:
            return
        texts = [self.translated_chunks.get(i, "") for i in indices if i in self.translated_chunks]
        if texts:
            QtWidgets.QApplication.clipboard().setText("\n\n".join(texts))
            self._set_status(f"{len(texts)}개 번역문 복사 완료")

    @asyncSlot()
    async def _on_generate_final(self) -> None:
        if not self.current_input_file:
            QtWidgets.QMessageBox.warning(self, "경고", "먼저 파일을 로드하세요.")
            return
        if not self.translated_chunks:
            QtWidgets.QMessageBox.warning(self, "경고", "번역된 청크가 없습니다.")
            return
        total = self.current_metadata.get("total_chunks", 0) if self.current_metadata else 0
        translated_count = len(self.translated_chunks)
        if translated_count < total:
            if QtWidgets.QMessageBox.question(
                self,
                "미완료 경고",
                f"전체 {total}개 중 {translated_count}개만 번역되었습니다. 계속하시겠습니까?",
            ) != QtWidgets.QMessageBox.Yes:
                return

        self._set_busy(True)
        self._set_status("최종 파일 생성 중...")

        def task() -> Path:
            if hasattr(self, 'provider') and self.provider:
                final_str = self.provider.generate_final_file(self.current_input_file, self.translated_chunks)
                return Path(final_str)
                
            final_output_path = self._get_final_output_file_path(self.current_input_file)
            chunked_path = self._get_translated_chunked_file_path(self.current_input_file)
            file_handler.save_merged_chunks_to_file(chunked_path, self.translated_chunks)
            enable_post_processing = True
            if self.app_service and self.app_service.config:
                enable_post_processing = self.app_service.config.get("enable_post_processing", True)
            chunks_to_merge = self.translated_chunks.copy()
            if enable_post_processing:
                try:
                    config = self.app_service.config if self.app_service else {}
                    chunks_to_merge = self.post_processing_service.post_process_merged_chunks(chunks_to_merge, config)
                except Exception:
                    pass
            sorted_indices = sorted(chunks_to_merge.keys())
            merged_parts = [chunks_to_merge[i] for i in sorted_indices]
            final_content = "\n\n".join(merged_parts)
            final_content = re.sub(r"\n{3,}", "\n\n", final_content).strip()
            write_text_file(final_output_path, final_content)
            return final_output_path

        try:
            result_path = await self._loop.run_in_executor(None, task)
            self._set_status(f"최종 파일 생성 완료: {result_path.name}")
            QtWidgets.QMessageBox.information(self, "성공", f"최종 파일 생성 완료\n{result_path}")
        except Exception as e:
            self._set_status(f"최종 파일 생성 실패: {e}")
            QtWidgets.QMessageBox.critical(self, "오류", f"최종 파일 생성 실패: {e}")
        finally:
            self._set_busy(False)

    def _on_check_integrity(self) -> None:
        if not self.current_input_file or not self.current_metadata:
            QtWidgets.QMessageBox.warning(self, "경고", "먼저 파일을 로드하세요.")
            return
        issues: List[str] = []
        meta_total = self.current_metadata.get("total_chunks", 0)
        actual_source = len(self.source_chunks)
        actual_translated = len(self.translated_chunks)
        if meta_total != actual_source and actual_source > 0:
            issues.append(f"• 메타데이터 청크 수({meta_total})와 원문 청크 수({actual_source}) 불일치")
        translated_meta = self.current_metadata.get("translated_chunks", {})
        for idx_str in translated_meta:
            try:
                idx = int(idx_str)
                if idx not in self.translated_chunks:
                    issues.append(f"• 청크 #{idx}: 메타데이터에 있으나 번역 파일에 없음")
            except ValueError:
                issues.append(f"• 잘못된 청크 인덱스: {idx_str}")
        if issues:
            QtWidgets.QMessageBox.warning(self, "무결성 검사", "\n".join(issues))
        else:
            QtWidgets.QMessageBox.information(self, "무결성 검사", "문제가 발견되지 않았습니다.")
        self._set_status(f"무결성 검사 완료 (문제 {len(issues)}건)")

    def _apply_filter(self, filter_text: str) -> None:
        """선택된 필터에 따라 테이블 행 숨기기/보이기"""
        if not self.proxy:
            return
            
        if filter_text == "모두 보기":
            self.proxy.setFilterFixedString("")
        elif "성공" in filter_text:
            self.proxy.setFilterFixedString("✅")
        elif "실패" in filter_text:
            self.proxy.setFilterFixedString("❌")
        elif "의심" in filter_text:
            self.proxy.setFilterRegularExpression("⚠️")

    def _on_quick_save_clicked(self) -> None:
        """오른쪽 편집창에서 수정한 내용을 즉시 저장"""
        chunk_idx = self._selected_index_single()
        if chunk_idx is None:
            return
            
        new_text = self.trans_view.toPlainText().strip()
        if not new_text:
            QtWidgets.QMessageBox.warning(self, "경고", "번역문을 입력하세요.")
            return
            
        try:
            self._save_chunk_translation(chunk_idx, new_text)
            self._set_status(f"청크 #{chunk_idx} 수동 수정 저장 완료")
            # 테이블 데이터 갱신 (비율 등)
            asyncio.create_task(self._load_metadata_from_path(self.current_input_file, silent=True))
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "오류", f"저장 실패: {e}")

    # ---------- config bridge ----------
    def get_config(self) -> Dict[str, Any]:
        return {}

    def load_config(self, config: Dict[str, Any]) -> None:
        pass

