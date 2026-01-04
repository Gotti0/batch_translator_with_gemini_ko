"""
PySide6 Log Tab
- Displays filtered application logs inside GUI
- Includes tqdm stream redirection for progress output
"""

from __future__ import annotations

import logging
import time
from typing import Optional, Dict

from PySide6 import QtCore, QtWidgets, QtGui

from gui_qt.components_qt.tooltip_qt import TooltipQt


class _QtLogEmitter(QtCore.QObject):
    message = QtCore.Signal(str, str)  # text, level


class QtGuiLogHandler(logging.Handler):
    """Logging handler that forwards messages to a Qt widget via signal."""

    def __init__(self, emitter: _QtLogEmitter) -> None:
        super().__init__()
        self.emitter = emitter

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            is_chunk_complete = "🎯" in msg and "전체 처리 완료" in msg
            if "⚠️" not in msg and not is_chunk_complete and record.levelno < logging.ERROR:
                return
            level_name = record.levelname
            self.emitter.message.emit(msg, level_name)
        except Exception:
            self.handleError(record)


class TqdmToQt:
    """Minimal stream object to send tqdm output to Qt widget."""

    def __init__(self, emitter: _QtLogEmitter) -> None:
        self.emitter = emitter

    def write(self, buf: str) -> None:
        text = buf.strip()
        if not text:
            return
        timestamp = time.strftime("%H:%M:%S")
        line = f"{timestamp} - {text}"
        self.emitter.message.emit(line, "TQDM")

    def flush(self) -> None:  # pragma: no cover - compatibility
        return


class LogTabQt(QtWidgets.QWidget):
    def __init__(self, app_service=None, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.app_service = app_service
        self._emitter = _QtLogEmitter()
        self._handler: Optional[QtGuiLogHandler] = None
        self._tqdm_stream: Optional[TqdmToQt] = None
        self._color_palette: Dict[str, str] = {}

        # 메인 레이아웃에 스크롤 영역 추가
        main_layout = QtWidgets.QVBoxLayout(self)
        scroll_area = QtWidgets.QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        
        # 스크롤 가능한 컨텐츠 위젯
        scroll_content = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(scroll_content)
        
        self.text_widget = QtWidgets.QPlainTextEdit()
        self.text_widget.setReadOnly(True)
        self.text_widget.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        TooltipQt(self.text_widget, "애플리케이션 실행 로그와 번역 진행 상황이 표시됩니다.\n오류 및 경고 메시지를 확인할 수 있습니다.")

        layout.addWidget(self.text_widget)
        
        # 스크롤 영역에 컨텐츠 설정
        scroll_area.setWidget(scroll_content)
        main_layout.addWidget(scroll_area)

        self._setup_logging()
        self._update_color_palette()

    def _is_dark_theme(self) -> bool:
        """시스템 테마가 다크 모드인지 확인"""
        palette = self.palette()
        bg_color = palette.color(QtGui.QPalette.Window)
        # 배경색의 밝기가 128보다 작으면 다크 테마로 판단
        return bg_color.lightness() < 128

    def _update_color_palette(self) -> None:
        """현재 테마에 맞는 로그 레벨별 색상 팔레트 생성"""
        is_dark = self._is_dark_theme()
        
        if is_dark:
            # 다크 테마: 밝은 색상 사용
            self._color_palette = {
                "DEBUG": "#808080",      # 중간 회색
                "INFO": "#e0e0e0",       # 밝은 회색 (기존 black 대체)
                "WARNING": "#FFB347",    # 밝은 주황색
                "ERROR": "#ff6b6b",      # 밝은 빨강
                "CRITICAL": "#ff3333",   # 더 밝은 빨강
                "TQDM": "#90ee90",       # 밝은 녹색
            }
        else:
            # 라이트 테마: 어두운 색상 사용
            self._color_palette = {
                "DEBUG": "#666666",      # 어두운 회색
                "INFO": "#000000",       # 검정
                "WARNING": "#FF8C00",    # 다크 오렌지
                "ERROR": "#cc0000",      # 어두운 빨강
                "CRITICAL": "#8b0000",   # 더 어두운 빨강
                "TQDM": "#006400",       # 어두운 녹색
            }

    def _setup_logging(self) -> None:
        self._emitter.message.connect(self._append_message)
        self._handler = QtGuiLogHandler(self._emitter)
        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s", "%H:%M:%S")
        self._handler.setFormatter(formatter)
        root_logger = logging.getLogger()
        root_logger.addHandler(self._handler)
        if self.app_service and hasattr(self.app_service, "logger"):
            try:
                self.app_service.logger.setLevel(logging.INFO)
            except Exception:
                pass
        self._tqdm_stream = TqdmToQt(self._emitter)

    @QtCore.Slot(str, str)
    def _append_message(self, msg: str, level: str) -> None:
        # 동적 색상 팔레트 사용 (다크 테마 지원)
        color = self._color_palette.get(level, "#e0e0e0" if self._is_dark_theme() else "#000000")
        
        cursor = self.text_widget.textCursor()
        cursor.movePosition(QtGui.QTextCursor.End)
        fmt = cursor.charFormat()
        fmt.setForeground(QtGui.QColor(color))
        cursor.setCharFormat(fmt)
        cursor.insertText(msg + "\n")
        self.text_widget.setTextCursor(cursor)
        self.text_widget.ensureCursorVisible()

    def get_tqdm_stream(self) -> Optional[TqdmToQt]:
        return self._tqdm_stream

    def get_log_handler(self) -> Optional[QtGuiLogHandler]:
        return self._handler

    def get_config(self):
        return {}

    def load_config(self, config):
        pass

    def closeEvent(self, event):  # type: ignore[override]
        try:
            if self._handler:
                logging.getLogger().removeHandler(self._handler)
        except Exception:
            pass
        super().closeEvent(event)

    def changeEvent(self, event: QtCore.QEvent) -> None:
        """테마 변경 감지 및 색상 팔레트 업데이트"""
        if event.type() == QtCore.QEvent.PaletteChange:
            self._update_color_palette()
        super().changeEvent(event)


# Late import for QColor/QTextCursor after QtGui is available
# QtGui는 이제 상단에서 import됨
