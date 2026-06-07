# gui_qt/components_qt/mode_card.py
from typing import Optional, Callable
from PySide6 import QtCore, QtWidgets, QtGui

class ModeCard(QtWidgets.QFrame):
    """
    번역 모드 선택을 위한 카드 컴포넌트
    - 클릭 시 선택 상태 시각화
    - 모드 제목, 아이콘, 간단한 설명 포함
    """
    clicked = QtCore.Signal(str)  # 모드 ID를 전달하는 시그널

    def __init__(
        self, 
        mode_id: str, 
        title: str, 
        description: str, 
        icon_text: str,
        parent: Optional[QtWidgets.QWidget] = None
    ):
        super().__init__(parent)
        self.mode_id = mode_id
        self.setObjectName("SectionCard")  # styles.qss의 스타일 적용
        self._selected = False
        
        # 레이아웃 설정
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        # 아이콘 (이모지 또는 텍스트)
        self.icon_label = QtWidgets.QLabel(icon_text)
        self.icon_label.setStyleSheet("font-size: 24px;")
        self.icon_label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self.icon_label)

        # 제목
        self.title_label = QtWidgets.QLabel(title)
        self.title_label.setStyleSheet("font-weight: bold; font-size: 16px; color: #FFFFFF;")
        self.title_label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self.title_label)

        # 설명
        self.desc_label = QtWidgets.QLabel(description)
        self.desc_label.setStyleSheet("color: #A1A1AA; font-size: 12px;")
        self.desc_label.setAlignment(QtCore.Qt.AlignCenter)
        self.desc_label.setWordWrap(True)
        layout.addWidget(self.desc_label)

        # 커서 설정
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setFixedWidth(280)
        self.setFixedHeight(160)

    def set_selected(self, selected: bool):
        self._selected = selected
        if selected:
            self.setStyleSheet("""
                QFrame#SectionCard {
                    background-color: rgba(142, 117, 255, 0.15);
                    border: 2px solid #8E75FF;
                    border-radius: 12px;
                }
            """)
        else:
            self.setStyleSheet("") # 기본 styles.qss 스타일로 복구

    def mousePressEvent(self, event: QtGui.QMouseEvent):
        if event.button() == QtCore.Qt.LeftButton:
            self.clicked.emit(self.mode_id)
        super().mousePressEvent(event)

class ModeSelectorGroup(QtWidgets.QWidget):
    """모드 카드들을 가로로 배치하고 상태를 관리하는 그룹 위젯"""
    mode_changed = QtCore.Signal(str)

    def __init__(self, parent: Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        layout.setAlignment(QtCore.Qt.AlignCenter)

        self.cards = {}
        
        # 모드 정의
        modes = [
            ("standard", "표준 번역", "빠르고 자연스러운 흐름 중심의 일반 텍스트 번역 모드입니다.", "📝"),
            ("integrity", "무결성 번역", "줄 단위 누락 방지 및 정확한 매핑을 보장하는 정밀 번역 모드입니다.", "🔒"),
            ("epub", "EPUB 번역", "HTML 구조와 스타일을 그대로 유지하며 전자책을 번역하는 모드입니다.", "📚")
        ]

        for m_id, title, desc, icon in modes:
            card = ModeCard(m_id, title, desc, icon)
            card.clicked.connect(self._on_card_clicked)
            layout.addWidget(card)
            self.cards[m_id] = card

        # 기본 선택
        self._current_mode = "standard"
        self.cards["standard"].set_selected(True)

    def _on_card_clicked(self, mode_id: str):
        if self._current_mode == mode_id:
            return
            
        # 이전 선택 해제
        self.cards[self._current_mode].set_selected(False)
        # 새 선택 설정
        self._current_mode = mode_id
        self.cards[mode_id].set_selected(True)
        
        self.mode_changed.emit(mode_id)

    def get_current_mode(self) -> str:
        return self._current_mode
