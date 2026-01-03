"""
PySide6 메인 윈도우 (qasync 연동)
- 기존 tkinter GUI 대체용 셸
- 앞으로 Settings/Glossary/Review/Log 탭을 순차 이식 예정
"""

from __future__ import annotations

import asyncio
from typing import Optional

from PySide6 import QtCore, QtWidgets
import qdarktheme
from app.app_service import AppService
from core.exceptions import BtgConfigException
from infrastructure.logger_config import setup_logger

# Qt 탭 구현 (점진 이식)
try:
    from gui_qt.tabs_qt.settings_tab_qt import SettingsTabQt
except Exception:  # pragma: no cover - 초기 단계에서 없을 수 있음
    SettingsTabQt = None  # type: ignore

try:
    from gui_qt.tabs_qt.glossary_tab_qt import GlossaryTabQt
except Exception:  # pragma: no cover - 초기 단계에서 없을 수 있음
    GlossaryTabQt = None  # type: ignore

try:
    from gui_qt.tabs_qt.review_tab_qt import ReviewTabQt
except Exception:  # pragma: no cover - 초기 단계에서 없을 수 있음
    ReviewTabQt = None  # type: ignore

try:
    from gui_qt.tabs_qt.log_tab_qt import LogTabQt
except Exception:  # pragma: no cover - 초기 단계에서 없을 수 있음
    LogTabQt = None  # type: ignore

logger = setup_logger(__name__)


class PlaceholderTab(QtWidgets.QWidget):
    """탭 이식 전까지 사용할 임시 플레이스홀더"""

    def __init__(self, title: str, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QVBoxLayout(self)
        label = QtWidgets.QLabel(f"{title} (PySide6 placeholder)")
        label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addStretch(1)
        layout.addWidget(label)
        layout.addStretch(1)


class BatchTranslatorWindow(QtWidgets.QMainWindow):
    """PySide6 메인 윈도우 골격"""

    def __init__(self, loop: Optional[asyncio.AbstractEventLoop] = None, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._loop = loop or asyncio.get_event_loop()
        self.app_service: Optional[AppService] = None
        self._current_theme: str = "dark"  # 기본 테마

        self.setWindowTitle("BTG - Batch Translator (PySide6)")
        self.resize(1100, 800)

        # AppService 초기화
        self._init_app_service()
        if not self.app_service:
            # 치명 오류 시 창을 닫고 이벤트 루프 중단
            QtWidgets.QMessageBox.critical(self, "AppService 오류", "AppService를 초기화할 수 없어 종료합니다.")
            QtCore.QTimer.singleShot(0, self.close)
            return

        # 탭 위젯 구성 (플레이스홀더 + 점진 이식 탭)
        self._setup_tabs()
        
        # 상태바 설정 (테마 토글 버튼 포함)
        self._setup_statusbar()

    def _setup_statusbar(self) -> None:
        """상태바 생성 및 테마 토글 버튼 추가"""
        statusbar = QtWidgets.QStatusBar()
        self.setStatusBar(statusbar)
        
        # 테마 토글 버튼 (상태바 오른쪽에 고정)
        self.theme_toggle_btn = QtWidgets.QPushButton("☀️ 라이트")
        self.theme_toggle_btn.setToolTip("라이트/다크 테마 전환")
        self.theme_toggle_btn.clicked.connect(self._toggle_theme)
        self.theme_toggle_btn.setFixedSize(80, 22)
        self.theme_toggle_btn.setStyleSheet("QPushButton { padding: 2px 8px; }")
        statusbar.addPermanentWidget(self.theme_toggle_btn)
        
        # 기본 상태 메시지
        statusbar.showMessage("준비됨")

    def _toggle_theme(self) -> None:
        """라이트/다크 테마 전환"""
        if self._current_theme == "dark":
            self._current_theme = "light"
            qdarktheme.setup_theme(theme="light", custom_colors={"primary": "#1976D2"})
            self.theme_toggle_btn.setText("🌙 다크")
        else:
            self._current_theme = "dark"
            qdarktheme.setup_theme(theme="dark", custom_colors={"primary": "#29B6F6"})
            self.theme_toggle_btn.setText("☀️ 라이트")

    def _init_app_service(self) -> None:
        try:
            self.app_service = AppService()
            logger.info("AppService 초기화 완료 (PySide6)")
        except BtgConfigException as e:
            logger.error(f"설정 오류로 AppService 초기화 실패: {e}")
            QtWidgets.QMessageBox.warning(
                self,
                "설정 오류",
                f"설정 파일 처리 중 오류 발생: {e}\n기본 설정으로 재시도하세요."
            )
            try:
                self.app_service = AppService()
                logger.info("기본 설정으로 AppService 재초기화 성공")
            except Exception as inner:
                logger.critical(f"AppService 재초기화 실패: {inner}")
                self.app_service = None
        except Exception as e:  # pragma: no cover - 방어적 코드
            logger.critical(f"AppService 초기화 중 예상치 못한 오류: {e}", exc_info=True)
            self.app_service = None

    def _setup_tabs(self) -> None:
        tab_widget = QtWidgets.QTabWidget()

        # Settings 탭: 실제 Qt 구현이 존재하면 사용, 아니면 플레이스홀더
        if SettingsTabQt and self.app_service:
            try:
                settings_tab = SettingsTabQt(self.app_service)
            except Exception as e:  # pragma: no cover - 방어적
                logger.error(f"SettingsTabQt 생성 실패, 플레이스홀더로 대체: {e}")
                settings_tab = PlaceholderTab("설정 및 번역")
        else:
            settings_tab = PlaceholderTab("설정 및 번역")

        # Glossary 탭
        if GlossaryTabQt and self.app_service:
            try:
                glossary_tab = GlossaryTabQt(self.app_service)
            except Exception as e:  # pragma: no cover - 방어적
                logger.error(f"GlossaryTabQt 생성 실패, 플레이스홀더로 대체: {e}")
                glossary_tab = PlaceholderTab("용어집 관리")
        else:
            glossary_tab = PlaceholderTab("용어집 관리")

        # Review 탭
        if ReviewTabQt and self.app_service:
            try:
                review_tab = ReviewTabQt(self.app_service)
            except Exception as e:  # pragma: no cover - 방어적
                logger.error(f"ReviewTabQt 생성 실패, 플레이스홀더로 대체: {e}")
                review_tab = PlaceholderTab("검토 및 수정")
        else:
            review_tab = PlaceholderTab("검토 및 수정")

        tab_widget.addTab(settings_tab, "설정 및 번역")
        tab_widget.addTab(glossary_tab, "용어집 관리")
        tab_widget.addTab(review_tab, "검토 및 수정")

        # Log 탭
        if LogTabQt:
            try:
                log_tab = LogTabQt(self.app_service)
                # Settings 탭에 TQDM 스트림 주입
                if isinstance(settings_tab, SettingsTabQt):
                    settings_tab.set_tqdm_stream(log_tab.get_tqdm_stream())
            except Exception as e:  # pragma: no cover - 방어적
                logger.error(f"LogTabQt 생성 실패, 플레이스홀더로 대체: {e}")
                log_tab = PlaceholderTab("실행 로그")
        else:
            log_tab = PlaceholderTab("실행 로그")

        tab_widget.addTab(log_tab, "실행 로그")
        self.setCentralWidget(tab_widget)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[name-defined]
        """창 닫기 시 현재 번역 작업이 있으면 취소 시도"""
        try:
            if self.app_service and self.app_service.current_translation_task:
                if not self.app_service.current_translation_task.done():
                    self.app_service.current_translation_task.cancel()
                    logger.info("창 종료: 진행 중 번역 Task.cancel() 호출")
        except Exception:
            # 종료 경로에서는 로깅만 남기고 무시
            logger.exception("창 종료 처리 중 오류")
        finally:
            # 루프가 있다면 중단
            if self._loop and self._loop.is_running():
                self._loop.stop()
            event.accept()


# QtGui를 늦게 임포트 (순환 방지)
from PySide6 import QtGui  # noqa: E402  # isort:skip
