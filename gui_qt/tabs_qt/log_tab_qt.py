"""
PySide6 Log Tab
- Displays filtered application logs inside GUI
- Includes tqdm stream redirection for progress output
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from PySide6 import QtCore, QtWidgets


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

        layout.addWidget(self.text_widget)
        
        # 스크롤 영역에 컨텐츠 설정
        scroll_area.setWidget(scroll_content)
        main_layout.addWidget(scroll_area)

        self._setup_logging()

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
        color = {
            "DEBUG": "gray",
            "INFO": "black",
            "WARNING": "#FF8C00",
            "ERROR": "red",
            "CRITICAL": "red",
            "TQDM": "green",
        }.get(level, "black")
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


# Late import for QColor/QTextCursor after QtGui is available
from PySide6 import QtGui  # noqa: E402  # isort:skip
