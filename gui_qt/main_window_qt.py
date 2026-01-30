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
from gui_qt.components_qt.tooltip_qt import TooltipQt

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
    
    # 테마 변경 시그널 (str: "dark" | "light")
    theme_changed = QtCore.Signal(str)

    def __init__(self, loop: Optional[asyncio.AbstractEventLoop] = None, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self._loop = loop or asyncio.get_event_loop()
        self.app_service: Optional[AppService] = None
        self._current_theme: str = "dark"  # 기본 테마
        
        # 시스템 트레이 아이콘
        self.tray_icon: Optional[QtWidgets.QSystemTrayIcon] = None
        
        # 탭 참조 저장 (테마 변경 시그널 연결용)
        self.settings_tab = None
        self.glossary_tab = None
        self.review_tab = None
        self.log_tab = None

        self.setWindowTitle("BTG - Batch Translator (PySide6)")
        self.resize(1100, 800)

        # 초기 테마 설정 (dark)
        self._apply_theme("dark")

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
        
        # 시스템 트레이 설정
        self._setup_system_tray()

    def _apply_theme(self, theme: str) -> None:
        """테마 적용 (qdarktheme + 툴팁 스타일)"""
        self._current_theme = theme
        
        # qdarktheme 설정
        if theme == "dark":
            qdarktheme.setup_theme(theme="dark", custom_colors={"primary": "#29B6F6"})
        else:
            qdarktheme.setup_theme(theme="light", custom_colors={"primary": "#1976D2"})
        
        # 전역 툴팁 스타일 적용
        app = QtWidgets.QApplication.instance()
        if app:
            TooltipQt.apply_global_style(app, theme=theme)
            logger.debug(f"테마 적용 완료: {theme}")

    def _setup_statusbar(self) -> None:
        """상태바 생성 및 테마 토글 버튼 추가"""
        statusbar = QtWidgets.QStatusBar()
        self.setStatusBar(statusbar)
        
        # 테마 토글 버튼 (상태바 오른쪽에 고정)
        # 버튼 텍스트: 현재 적용된 테마를 표시 (클릭 시 반대 테마로 전환)
        self.theme_toggle_btn = QtWidgets.QPushButton("🌙 다크")
        self.theme_toggle_btn.setToolTip("클릭하여 라이트 테마로 전환")
        self.theme_toggle_btn.clicked.connect(self._toggle_theme)
        self.theme_toggle_btn.setFixedSize(80, 22)
        self.theme_toggle_btn.setStyleSheet("QPushButton { padding: 2px 8px; }")
        statusbar.addPermanentWidget(self.theme_toggle_btn)
        
        # 기본 상태 메시지
        statusbar.showMessage("준비됨")

    def _toggle_theme(self) -> None:
        """라이트/다크 테마 전환"""
        new_theme = "light" if self._current_theme == "dark" else "dark"
        
        # 테마 적용
        self._apply_theme(new_theme)
        
        # 버튼 텍스트 업데이트
        if new_theme == "dark":
            self.theme_toggle_btn.setText("🌙 다크")
            self.theme_toggle_btn.setToolTip("클릭하여 라이트 테마로 전환")
        else:
            self.theme_toggle_btn.setText("☀️ 라이트")
            self.theme_toggle_btn.setToolTip("클릭하여 다크 테마로 전환")
        
        # 테마 변경 시그널 emit (모든 탭에 알림)
        self.theme_changed.emit(new_theme)
        logger.info(f"테마 변경됨: {new_theme}")

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
                self.settings_tab = SettingsTabQt(self.app_service)
            except Exception as e:  # pragma: no cover - 방어적
                logger.error(f"SettingsTabQt 생성 실패, 플레이스홀더로 대체: {e}")
                self.settings_tab = PlaceholderTab("설정 및 번역")
        else:
            self.settings_tab = PlaceholderTab("설정 및 번역")

        # Glossary 탭
        if GlossaryTabQt and self.app_service:
            try:
                self.glossary_tab = GlossaryTabQt(self.app_service)
            except Exception as e:  # pragma: no cover - 방어적
                logger.error(f"GlossaryTabQt 생성 실패, 플레이스홀더로 대체: {e}")
                self.glossary_tab = PlaceholderTab("용어집 관리")
        else:
            self.glossary_tab = PlaceholderTab("용어집 관리")

        # Review 탭
        if ReviewTabQt and self.app_service:
            try:
                self.review_tab = ReviewTabQt(self.app_service)
                # 테마 변경 시그널 연결
                self.theme_changed.connect(self.review_tab.update_theme)
            except Exception as e:  # pragma: no cover - 방어적
                logger.error(f"ReviewTabQt 생성 실패, 플레이스홀더로 대체: {e}")
                self.review_tab = PlaceholderTab("검토 및 수정")
        else:
            self.review_tab = PlaceholderTab("검토 및 수정")

        tab_widget.addTab(self.settings_tab, "설정 및 번역")
        tab_widget.addTab(self.glossary_tab, "용어집 관리")
        tab_widget.addTab(self.review_tab, "검토 및 수정")

        # Log 탭
        if LogTabQt:
            try:
                self.log_tab = LogTabQt(self.app_service)
                # 테마 변경 시그널 연결
                self.theme_changed.connect(self.log_tab.update_theme)
                # Settings 탭에 TQDM 스트림 주입
                if isinstance(self.settings_tab, SettingsTabQt):
                    self.settings_tab.set_tqdm_stream(self.log_tab.get_tqdm_stream())
            except Exception as e:  # pragma: no cover - 방어적
                logger.error(f"LogTabQt 생성 실패, 플레이스홀더로 대체: {e}")
                self.log_tab = PlaceholderTab("실행 로그")
        else:
            self.log_tab = PlaceholderTab("실행 로그")

        tab_widget.addTab(self.log_tab, "실행 로그")
        self.setCentralWidget(tab_widget)

    def _setup_system_tray(self) -> None:
        """시스템 트레이 아이콘 설정"""
        if not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
            logger.warning("시스템 트레이를 사용할 수 없는 환경입니다.")
            return
        
        # 트레이 아이콘 생성
        self.tray_icon = QtWidgets.QSystemTrayIcon(self)
        
        # 아이콘 설정 (윈도우 아이콘 재사용 또는 기본 아이콘)
        app_icon = self.windowIcon()
        if app_icon.isNull():
            # 기본 아이콘 (앱 아이콘이 없는 경우)
            app_icon = self.style().standardIcon(QtWidgets.QStyle.SP_ComputerIcon)
        self.tray_icon.setIcon(app_icon)
        self.tray_icon.setToolTip("BTG - Batch Translator")
        
        # 트레이 메뉴 구성
        tray_menu = QtWidgets.QMenu()
        
        show_action = tray_menu.addAction("창 보이기")
        show_action.triggered.connect(self._show_window_from_tray)
        
        tray_menu.addSeparator()
        
        quit_action = tray_menu.addAction("종료")
        quit_action.triggered.connect(self.close)
        
        self.tray_icon.setContextMenu(tray_menu)
        
        # 트레이 아이콘 클릭 시 창 보이기
        self.tray_icon.activated.connect(self._on_tray_activated)
        
        # 트레이 아이콘 표시
        self.tray_icon.show()
        logger.info("시스템 트레이 아이콘 초기화 완료")

    def _on_tray_activated(self, reason: QtWidgets.QSystemTrayIcon.ActivationReason) -> None:
        """트레이 아이콘 클릭 이벤트 처리"""
        if reason == QtWidgets.QSystemTrayIcon.ActivationReason.Trigger:
            # 싱글 클릭 시 창 보이기
            self._show_window_from_tray()
        elif reason == QtWidgets.QSystemTrayIcon.ActivationReason.DoubleClick:
            # 더블 클릭 시 창 보이기
            self._show_window_from_tray()

    def _show_window_from_tray(self) -> None:
        """트레이에서 창 복원"""
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def show_tray_notification(
        self,
        title: str,
        message: str,
        icon_type: str = "info",
        duration_ms: int = 5000
    ) -> None:
        """
        시스템 트레이 알림 표시
        
        Args:
            title: 알림 제목
            message: 알림 내용
            icon_type: 아이콘 종류 ("info", "warning", "critical", "none")
            duration_ms: 표시 시간 (밀리초, 기본 5초)
        """
        if not self.tray_icon:
            logger.debug("시스템 트레이가 없어서 알림을 표시할 수 없습니다.")
            return
        
        icon_map = {
            "info": QtWidgets.QSystemTrayIcon.MessageIcon.Information,
            "warning": QtWidgets.QSystemTrayIcon.MessageIcon.Warning,
            "critical": QtWidgets.QSystemTrayIcon.MessageIcon.Critical,
            "none": QtWidgets.QSystemTrayIcon.MessageIcon.NoIcon,
        }
        icon = icon_map.get(icon_type, QtWidgets.QSystemTrayIcon.MessageIcon.Information)
        
        self.tray_icon.showMessage(title, message, icon, duration_ms)
        logger.debug(f"트레이 알림 표시: {title}")

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # type: ignore[name-defined]
        """창 닫기 시 현재 번역 작업이 있으면 취소 시도"""
        try:
            # 시스템 트레이 아이콘 숨기기
            if self.tray_icon:
                self.tray_icon.hide()
            
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
