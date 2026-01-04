"""
PySide6 Tooltip 컴포넌트
Qt 네이티브 툴팁 기능을 향상시킨 래퍼 클래스
"""

from typing import Optional
from PySide6 import QtCore, QtWidgets


class TooltipQt:
    """
    Qt 위젯에 향상된 툴팁을 설정하는 헬퍼 클래스
    
    기존 tkinter Tooltip과 유사한 인터페이스 제공:
        TooltipQt(widget, "설명 텍스트")
    
    Features:
        - Qt 네이티브 툴팁 활용 (안정성, 접근성)
        - Rich text 지원 (HTML)
        - 멀티라인 지원 (\n 자동 변환)
        - 다크/라이트 테마 자동 대응
    
    생명 주기:
        1. 초기화: TooltipQt(widget, text) 호출
        2. Qt 이벤트 루프가 자동으로 hover 감지
        3. 마우스 진입 후 기본 지연 시간(~500ms) 후 툴팁 표시
        4. 마우스 이탈 또는 클릭 시 자동 숨김
    
    Example:
        # 기본 사용
        TooltipQt(button, "이 버튼을 클릭하세요")
        
        # 멀티라인
        TooltipQt(
            slider,
            "첫 번째 줄\n두 번째 줄\n세 번째 줄"
        )
        
        # Rich text (HTML)
        TooltipQt(
            checkbox,
            "<b>굵은 텍스트</b><br><i>기울임 텍스트</i>",
            rich_text=True
        )
    """
    
    # 전역 스타일 템플릿 (다크/라이트 테마 대응)
    LIGHT_THEME_STYLE = """
        QToolTip {
            background-color: #ffffe0;
            color: #000000;
            border: 1px solid #999999;
            padding: 4px 6px;
            font-size: 9pt;
            border-radius: 3px;
        }
    """
    
    DARK_THEME_STYLE = """
        QToolTip {
            background-color: #2b2b2b;
            color: #f0f0f0;
            border: 1px solid #555555;
            padding: 4px 6px;
            font-size: 9pt;
            border-radius: 3px;
        }
    """
    
    def __init__(
        self,
        widget: QtWidgets.QWidget,
        text: str,
        rich_text: bool = False,
        duration_ms: Optional[int] = None
    ):
        """
        Qt 위젯에 툴팁을 설정합니다.
        
        Args:
            widget: 툴팁을 적용할 위젯
            text: 툴팁 텍스트
                  - plain text: \n으로 줄바꿈 지원
                  - rich_text=True: HTML 태그 지원
            rich_text: HTML 형식 사용 여부 (기본값: False)
            duration_ms: 툴팁 표시 시간(밀리초). None=무제한
        """
        self.widget = widget
        self.text = text
        self.rich_text = rich_text
        
        # 텍스트 형식 처리
        if rich_text:
            # HTML은 그대로 사용
            tooltip_text = text
        else:
            # Plain text는 \n을 <br>로 변환 (멀티라인 지원)
            tooltip_text = text.replace('\n', '<br>')
        
        # Qt 네이티브 툴팁 설정
        widget.setToolTip(tooltip_text)
        
        # 툴팁 표시 시간 설정 (옵션)
        if duration_ms is not None:
            widget.setToolTipDuration(duration_ms)
    
    @classmethod
    def apply_global_style(
        cls,
        app: QtWidgets.QApplication,
        theme: str = "dark"
    ) -> None:
        """
        애플리케이션 전체에 툴팁 스타일 적용
        
        Args:
            app: QApplication 인스턴스
            theme: "light" 또는 "dark"
        
        Example:
            # main.py에서
            app = QApplication(sys.argv)
            TooltipQt.apply_global_style(app, theme="dark")
        """
        style = cls.DARK_THEME_STYLE if theme == "dark" else cls.LIGHT_THEME_STYLE
        
        # 기존 스타일시트에 추가 (덮어쓰지 않음)
        current_stylesheet = app.styleSheet()
        if "QToolTip" not in current_stylesheet:
            app.setStyleSheet(current_stylesheet + style)
    
    @classmethod
    def update_global_theme(
        cls,
        app: QtWidgets.QApplication,
        theme: str
    ) -> None:
        """
        테마 변경 시 툴팁 스타일 업데이트
        
        Args:
            app: QApplication 인스턴스
            theme: "light" 또는 "dark"
        """
        import re
        
        # 기존 QToolTip 스타일 제거 후 새로 적용
        current = app.styleSheet()
        
        # 정규표현식으로 QToolTip 블록 제거 (더 안전)
        # QToolTip { ... } 패턴을 찾아서 제거
        pattern = r'QToolTip\s*\{[^}]*\}'
        new_stylesheet = re.sub(pattern, '', current, flags=re.DOTALL)
        
        # 새 테마 스타일 추가
        style = cls.DARK_THEME_STYLE if theme == "dark" else cls.LIGHT_THEME_STYLE
        app.setStyleSheet(new_stylesheet + style)


# 간편 함수 (tkinter Tooltip과 호환성 제공)
def set_tooltip(
    widget: QtWidgets.QWidget,
    text: str,
    rich_text: bool = False
) -> TooltipQt:
    """
    간편 함수: 위젯에 툴팁 설정
    
    Args:
        widget: 툴팁을 적용할 위젯
        text: 툴팁 텍스트
        rich_text: HTML 형식 사용 여부
    
    Returns:
        TooltipQt 인스턴스
    
    Example:
        set_tooltip(button, "클릭하여 저장")
    """
    return TooltipQt(widget, text, rich_text=rich_text)
