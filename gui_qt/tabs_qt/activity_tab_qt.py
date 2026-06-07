# gui_qt/tabs_qt/activity_tab_qt.py
from typing import Optional, Dict
from PySide6 import QtCore, QtWidgets, QtGui
from infrastructure.logger_config import setup_logger

logger = setup_logger(__name__)

class ActivityItem(QtWidgets.QWidget):
    """타임라인의 한 항목을 나타내는 위젯"""
    def __init__(self, timestamp: str, icon: str, message: str, color: str, parent=None):
        super().__init__(parent)
        self.setObjectName("ActivityItem")
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(12)
        
        # 페이드인 애니메이션을 위한 투명도 효과
        self.opacity_effect = QtWidgets.QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.opacity_effect)
        self.opacity_anim = QtCore.QPropertyAnimation(self.opacity_effect, b"opacity")
        self.opacity_anim.setDuration(400)
        self.opacity_anim.setStartValue(0.0)
        self.opacity_anim.setEndValue(1.0)
        self.opacity_anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)

        # 시간 표시
        self.time_label = QtWidgets.QLabel(timestamp)
        self.time_label.setStyleSheet("color: #A1A1AA; font-size: 11px; min-width: 60px;")
        layout.addWidget(self.time_label)

        # 아이콘 및 수직선 컨테이너
        icon_layout = QtWidgets.QVBoxLayout()
        icon_layout.setSpacing(0)
        
        self.icon_label = QtWidgets.QLabel(icon)
        self.icon_label.setStyleSheet(f"font-size: 14px; color: {color};")
        self.icon_label.setAlignment(QtCore.Qt.AlignCenter)
        icon_layout.addWidget(self.icon_label)
        
        layout.addLayout(icon_layout)

        # 메시지
        self.msg_label = QtWidgets.QLabel(message)
        self.msg_label.setStyleSheet(f"color: #FFFFFF; font-size: 13px;")
        self.msg_label.setWordWrap(True)
        layout.addWidget(self.msg_label, 1)

class ActivityTimelineWidget(QtWidgets.QWidget):
    """수직 타임라인 로그 위젯"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout = QtWidgets.QVBoxLayout(self)
        self.layout.setContentsMargins(16, 16, 16, 16)
        self.layout.setSpacing(0)
        self.layout.setAlignment(QtCore.Qt.AlignTop)

    def add_activity(self, message: str, level: str = "INFO"):
        import time
        timestamp = time.strftime("%H:%M:%S")
        
        # 메시지 내용에 따른 아이콘 및 색상 선정
        icon = "●"
        color = "#8E75FF" # Primary
        
        msg_lower = message.lower()
        if "실패" in msg_lower or "error" in msg_lower or "⚠️" in msg_lower:
            icon = "⚠️"
            color = "#F87171" # Error
        elif "완료" in msg_lower or "success" in msg_lower or "🎯" in msg_lower:
            icon = "✅"
            color = "#4ADE80" # Success
        elif "retry" in msg_lower or "재시도" in msg_lower or "split" in msg_lower:
            icon = "🔄"
            color = "#FBBF24" # Warning
        elif "시작" in msg_lower:
            icon = "🚀"
            color = "#8E75FF"
        elif "📦" in message:
            icon = "📦"
            color = "#C3A1FF"

        item = ActivityItem(timestamp, icon, message, color)
        self.layout.insertWidget(0, item) # 최신 항목이 위로
        item.opacity_anim.start() # Phase 5: 애니메이션 시작

class ActivityTabQt(QtWidgets.QWidget):
    """실시간 활동 타임라인 탭"""
    def __init__(self, app_service, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self.app_service = app_service
        
        main_layout = QtWidgets.QVBoxLayout(self)
        
        # 헤더
        header = QtWidgets.QLabel("실시간 활동 타임라인")
        header.setStyleSheet("font-size: 18px; font-weight: bold; color: #FFFFFF; margin-bottom: 8px;")
        main_layout.addWidget(header)

        # 타임라인 스크롤 영역
        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setObjectName("SectionCard")
        
        self.timeline = ActivityTimelineWidget()
        self.scroll_area.setWidget(self.timeline)
        
        main_layout.addWidget(self.scroll_area)

        # 로그 연동을 위한 시그널 연결 로직은 필요시 추가
        # (현재는 AppService나 LogTab의 emitter를 통해 전달받는 구조 권장)

    def add_log(self, message: str, level: str = "INFO"):
        self.timeline.add_activity(message, level)

    def update_theme(self, theme: str):
        # DESIGN.md에 따른 색상 유지
        pass
