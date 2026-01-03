"""
GUI 컴포넌트 테스트

gui/components 및 gui/tabs 모듈의 단위 테스트입니다.
"""

import unittest
import tkinter as tk
from tkinter import scrolledtext
import logging
import sys
import os
from unittest.mock import Mock, MagicMock, patch
from typing import Dict, Any

# 프로젝트 루트를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# 모든 테스트에서 공유할 단일 Tk 루트 (Tcl 환경 문제 방지)
_shared_root = None


def get_shared_root():
    """공유 Tk 루트 반환 (없으면 생성)"""
    global _shared_root
    if _shared_root is None or not _shared_root.winfo_exists():
        _shared_root = tk.Tk()
        _shared_root.withdraw()
    return _shared_root


def cleanup_shared_root():
    """공유 Tk 루트 정리"""
    global _shared_root
    if _shared_root is not None:
        try:
            _shared_root.destroy()
        except tk.TclError:
            pass
        _shared_root = None


class TestTooltip(unittest.TestCase):
    """Tooltip 클래스 테스트"""
    
    @classmethod
    def setUpClass(cls):
        """테스트 클래스 시작 전 Tk 루트 생성"""
        cls.root = get_shared_root()
    
    @classmethod
    def tearDownClass(cls):
        """테스트 클래스 종료 후 정리 (루트는 유지)"""
        pass  # 공유 루트는 여기서 파괴하지 않음
    
    def setUp(self):
        """각 테스트 전 위젯 생성"""
        self.button = tk.Button(self.root, text="Test Button")
        self.button.pack()
    
    def tearDown(self):
        """각 테스트 후 위젯 정리"""
        self.button.destroy()
    
    def test_tooltip_creation(self):
        """Tooltip 생성 테스트"""
        from gui.components.tooltip import Tooltip
        
        tooltip = Tooltip(self.button, "테스트 툴팁")
        
        self.assertIsNotNone(tooltip)
        self.assertEqual(tooltip.text, "테스트 툴팁")
        self.assertEqual(tooltip.widget, self.button)
        self.assertIsNone(tooltip.tooltip_window)
    
    def test_tooltip_initial_state(self):
        """Tooltip 초기 상태 테스트"""
        from gui.components.tooltip import Tooltip
        
        tooltip = Tooltip(self.button, "초기 상태 테스트")
        
        self.assertIsNone(tooltip.id)
        self.assertEqual(tooltip.enter_x_root, 0)
        self.assertEqual(tooltip.enter_y_root, 0)
    
    def test_tooltip_schedule_unschedule(self):
        """Tooltip 스케줄링 테스트"""
        from gui.components.tooltip import Tooltip
        
        tooltip = Tooltip(self.button, "스케줄 테스트")
        
        # 스케줄링
        tooltip.schedule()
        self.assertIsNotNone(tooltip.id)
        
        # 스케줄 취소
        tooltip.unschedule()
        self.assertIsNone(tooltip.id)
    
    def test_tooltip_leave_hides_tip(self):
        """마우스 나갈 때 툴팁 숨김 테스트"""
        from gui.components.tooltip import Tooltip
        
        tooltip = Tooltip(self.button, "나가기 테스트")
        tooltip.schedule()
        
        # leave 호출
        tooltip.leave()
        
        self.assertIsNone(tooltip.id)
        self.assertIsNone(tooltip.tooltip_window)


class TestScrollableFrame(unittest.TestCase):
    """ScrollableFrame 클래스 테스트"""
    
    @classmethod
    def setUpClass(cls):
        """테스트 클래스 시작 전 Tk 루트 생성"""
        cls.root = get_shared_root()
    
    @classmethod
    def tearDownClass(cls):
        """테스트 클래스 종료 후 정리 (루트는 유지)"""
        pass
    
    def test_scrollable_frame_creation(self):
        """ScrollableFrame 생성 테스트"""
        from gui.components.scrollable_frame import ScrollableFrame
        
        scroll_frame = ScrollableFrame(self.root)
        
        self.assertIsNotNone(scroll_frame.main_frame)
        self.assertIsNotNone(scroll_frame.canvas)
        self.assertIsNotNone(scroll_frame.scrollbar)
        self.assertIsNotNone(scroll_frame.scrollable_frame)
    
    def test_scrollable_frame_with_height(self):
        """ScrollableFrame 높이 지정 테스트"""
        from gui.components.scrollable_frame import ScrollableFrame
        
        height = 300
        scroll_frame = ScrollableFrame(self.root, height=height)
        
        # canvas의 높이가 설정되었는지 확인
        self.assertIsNotNone(scroll_frame.canvas)
    
    def test_scrollable_frame_pack(self):
        """ScrollableFrame pack 메서드 테스트"""
        from gui.components.scrollable_frame import ScrollableFrame
        
        scroll_frame = ScrollableFrame(self.root)
        
        # pack 호출 시 예외가 발생하지 않아야 함
        try:
            scroll_frame.pack(fill="both", expand=True)
            success = True
        except Exception as e:
            success = False
        
        self.assertTrue(success)
    
    def test_scrollable_frame_add_widgets(self):
        """ScrollableFrame에 위젯 추가 테스트"""
        from gui.components.scrollable_frame import ScrollableFrame
        
        scroll_frame = ScrollableFrame(self.root)
        
        # scrollable_frame에 위젯 추가
        label = tk.Label(scroll_frame.scrollable_frame, text="테스트 라벨")
        label.pack()
        
        button = tk.Button(scroll_frame.scrollable_frame, text="테스트 버튼")
        button.pack()
        
        # 위젯들이 정상적으로 추가되었는지 확인
        children = scroll_frame.scrollable_frame.winfo_children()
        self.assertEqual(len(children), 2)


class TestGuiLogHandler(unittest.TestCase):
    """GuiLogHandler 클래스 테스트"""
    
    @classmethod
    def setUpClass(cls):
        """테스트 클래스 시작 전 Tk 루트 생성"""
        cls.root = get_shared_root()
    
    @classmethod
    def tearDownClass(cls):
        """테스트 클래스 종료 후 정리 (루트는 유지)"""
        pass
    
    def setUp(self):
        """각 테스트 전 ScrolledText 위젯 생성"""
        self.text_widget = scrolledtext.ScrolledText(self.root)
        self.text_widget.pack()
    
    def tearDown(self):
        """각 테스트 후 위젯 정리"""
        self.text_widget.destroy()
    
    def test_log_handler_creation(self):
        """GuiLogHandler 생성 테스트"""
        from gui.components.log_handlers import GuiLogHandler
        
        handler = GuiLogHandler(self.text_widget)
        
        self.assertIsNotNone(handler)
        self.assertEqual(handler.text_widget, self.text_widget)
    
    def test_log_handler_tag_config(self):
        """GuiLogHandler 태그 설정 테스트"""
        from gui.components.log_handlers import GuiLogHandler
        
        handler = GuiLogHandler(self.text_widget)
        
        # 태그들이 설정되었는지 확인
        tag_names = self.text_widget.tag_names()
        self.assertIn("INFO", tag_names)
        self.assertIn("DEBUG", tag_names)
        self.assertIn("WARNING", tag_names)
        self.assertIn("ERROR", tag_names)
        self.assertIn("CRITICAL", tag_names)
        self.assertIn("TQDM", tag_names)
    
    def test_log_handler_emit_error(self):
        """GuiLogHandler ERROR 레벨 emit 테스트"""
        from gui.components.log_handlers import GuiLogHandler
        
        handler = GuiLogHandler(self.text_widget)
        handler.setFormatter(logging.Formatter('%(message)s'))
        
        # ERROR 레벨 로그 레코드 생성
        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="",
            lineno=0,
            msg="에러 테스트 메시지",
            args=(),
            exc_info=None
        )
        
        # emit 호출
        handler.emit(record)
        
        # after 콜백이 처리되도록 업데이트
        self.root.update()
        
        # 텍스트가 추가되었는지 확인 (ERROR 레벨은 표시됨)
        content = self.text_widget.get("1.0", tk.END)
        self.assertIn("에러 테스트 메시지", content)
    
    def test_log_handler_filter_info(self):
        """GuiLogHandler INFO 레벨 필터링 테스트 (⚠️ 없으면 필터됨)"""
        from gui.components.log_handlers import GuiLogHandler
        
        handler = GuiLogHandler(self.text_widget)
        handler.setFormatter(logging.Formatter('%(message)s'))
        
        # INFO 레벨 로그 레코드 생성 (⚠️ 없음)
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="일반 INFO 메시지",
            args=(),
            exc_info=None
        )
        
        # emit 호출
        handler.emit(record)
        
        # after 콜백이 처리되도록 업데이트
        self.root.update()
        
        # ⚠️가 없는 INFO는 필터링되어 표시되지 않음
        content = self.text_widget.get("1.0", tk.END)
        self.assertNotIn("일반 INFO 메시지", content)
    
    def test_log_handler_quality_warning_passes(self):
        """GuiLogHandler 품질 경고(⚠️) 통과 테스트"""
        from gui.components.log_handlers import GuiLogHandler
        
        handler = GuiLogHandler(self.text_widget)
        handler.setFormatter(logging.Formatter('%(message)s'))
        
        # ⚠️가 포함된 INFO 레벨 로그 레코드
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="⚠️ 품질 이슈 발생",
            args=(),
            exc_info=None
        )
        
        handler.emit(record)
        self.root.update()
        
        # ⚠️가 포함된 메시지는 표시됨
        content = self.text_widget.get("1.0", tk.END)
        self.assertIn("품질 이슈 발생", content)


class TestTqdmToTkinter(unittest.TestCase):
    """TqdmToTkinter 클래스 테스트"""
    
    @classmethod
    def setUpClass(cls):
        """테스트 클래스 시작 전 Tk 루트 생성"""
        cls.root = get_shared_root()
    
    @classmethod
    def tearDownClass(cls):
        """테스트 클래스 종료 후 정리 (루트는 유지)"""
        pass
    
    def setUp(self):
        """각 테스트 전 ScrolledText 위젯 생성"""
        self.text_widget = scrolledtext.ScrolledText(self.root)
        self.text_widget.pack()
    
    def tearDown(self):
        """각 테스트 후 위젯 정리"""
        self.text_widget.destroy()
    
    def test_tqdm_stream_creation(self):
        """TqdmToTkinter 생성 테스트"""
        from gui.components.log_handlers import TqdmToTkinter
        
        stream = TqdmToTkinter(self.text_widget)
        
        self.assertIsNotNone(stream)
        self.assertEqual(stream.widget, self.text_widget)
    
    def test_tqdm_stream_write_empty(self):
        """TqdmToTkinter 빈 문자열 write 테스트"""
        from gui.components.log_handlers import TqdmToTkinter
        
        stream = TqdmToTkinter(self.text_widget)
        
        # 빈 문자열 또는 공백만 있는 경우 무시됨
        stream.write("")
        stream.write("   ")
        
        self.root.update()
        
        content = self.text_widget.get("1.0", tk.END).strip()
        self.assertEqual(content, "")


class TestBaseTab(unittest.TestCase):
    """BaseTab 추상 클래스 테스트"""
    
    @classmethod
    def setUpClass(cls):
        """테스트 클래스 시작 전 Tk 루트 생성"""
        cls.root = get_shared_root()
    
    @classmethod
    def tearDownClass(cls):
        """테스트 클래스 종료 후 정리 (루트는 유지)"""
        pass
    
    def test_base_tab_is_abstract(self):
        """BaseTab이 추상 클래스인지 테스트"""
        from gui.tabs.base_tab import BaseTab
        
        mock_service = Mock()
        mock_logger = Mock()
        
        # 추상 클래스이므로 직접 인스턴스화 시도 시 TypeError 발생
        with self.assertRaises(TypeError):
            BaseTab(self.root, mock_service, mock_logger)
    
    def test_base_tab_concrete_implementation(self):
        """BaseTab 구현체 생성 테스트"""
        from gui.tabs.base_tab import BaseTab
        import ttkbootstrap as ttk
        
        class ConcreteTab(BaseTab):
            """테스트용 구현체"""
            
            def create_widgets(self) -> ttk.Frame:
                self.frame = ttk.Frame(self.parent)
                return self.frame
            
            def get_config(self) -> Dict[str, Any]:
                return {"test_key": "test_value"}
            
            def load_config(self, config: Dict[str, Any]) -> None:
                pass
        
        mock_service = Mock()
        mock_logger = Mock()
        
        tab = ConcreteTab(self.root, mock_service, mock_logger)
        
        self.assertIsNotNone(tab)
        self.assertEqual(tab.parent, self.root)
        self.assertEqual(tab.app_service, mock_service)
        self.assertEqual(tab.logger, mock_logger)
    
    def test_base_tab_log_message(self):
        """BaseTab log_message 메서드 테스트"""
        from gui.tabs.base_tab import BaseTab
        import ttkbootstrap as ttk
        
        class ConcreteTab(BaseTab):
            def create_widgets(self) -> ttk.Frame:
                return ttk.Frame(self.parent)
            
            def get_config(self) -> Dict[str, Any]:
                return {}
            
            def load_config(self, config: Dict[str, Any]) -> None:
                pass
        
        mock_logger = Mock()
        tab = ConcreteTab(self.root, Mock(), mock_logger)
        
        # 각 로그 레벨 테스트
        tab.log_message("INFO 테스트", "INFO")
        mock_logger.info.assert_called_once()
        
        tab.log_message("WARNING 테스트", "WARNING")
        mock_logger.warning.assert_called_once()
        
        tab.log_message("ERROR 테스트", "ERROR")
        mock_logger.error.assert_called_once()
    
    def test_base_tab_get_config_returns_dict(self):
        """BaseTab get_config 반환 타입 테스트"""
        from gui.tabs.base_tab import BaseTab
        import ttkbootstrap as ttk
        
        class ConcreteTab(BaseTab):
            def create_widgets(self) -> ttk.Frame:
                return ttk.Frame(self.parent)
            
            def get_config(self) -> Dict[str, Any]:
                return {
                    "setting1": "value1",
                    "setting2": 123,
                    "setting3": True
                }
            
            def load_config(self, config: Dict[str, Any]) -> None:
                pass
        
        tab = ConcreteTab(self.root, Mock(), Mock())
        config = tab.get_config()
        
        self.assertIsInstance(config, dict)
        self.assertEqual(config["setting1"], "value1")
        self.assertEqual(config["setting2"], 123)
        self.assertEqual(config["setting3"], True)


class TestLogTab(unittest.TestCase):
    """LogTab 클래스 테스트"""
    
    @classmethod
    def setUpClass(cls):
        """테스트 클래스 시작 전 Tk 루트 생성"""
        cls.root = get_shared_root()
    
    @classmethod
    def tearDownClass(cls):
        """테스트 클래스 종료 후 정리 (루트는 유지)"""
        pass
    
    def test_log_tab_creation(self):
        """LogTab 생성 테스트"""
        from gui.tabs.log_tab import LogTab
        
        mock_service = Mock()
        mock_logger = Mock()
        
        log_tab = LogTab(self.root, mock_service, mock_logger)
        
        self.assertIsNotNone(log_tab)
    
    def test_log_tab_create_widgets(self):
        """LogTab create_widgets 테스트"""
        from gui.tabs.log_tab import LogTab
        
        log_tab = LogTab(self.root, Mock(), Mock())
        frame = log_tab.create_widgets()
        
        self.assertIsNotNone(frame)
        self.assertIsNotNone(log_tab.log_text)
    
    def test_log_tab_get_config_empty(self):
        """LogTab get_config가 빈 딕셔너리 반환 테스트"""
        from gui.tabs.log_tab import LogTab
        
        log_tab = LogTab(self.root, Mock(), Mock())
        log_tab.create_widgets()
        
        config = log_tab.get_config()
        
        self.assertIsInstance(config, dict)
        self.assertEqual(len(config), 0)
    
    def test_log_tab_load_config_no_error(self):
        """LogTab load_config가 오류 없이 실행되는지 테스트"""
        from gui.tabs.log_tab import LogTab
        
        log_tab = LogTab(self.root, Mock(), Mock())
        log_tab.create_widgets()
        
        # 어떤 설정을 전달해도 오류가 발생하지 않아야 함
        try:
            log_tab.load_config({"some_key": "some_value"})
            success = True
        except Exception:
            success = False
        
        self.assertTrue(success)
    
    def test_log_tab_get_tqdm_stream(self):
        """LogTab get_tqdm_stream 테스트"""
        from gui.tabs.log_tab import LogTab
        
        log_tab = LogTab(self.root, Mock(), Mock())
        log_tab.create_widgets()
        
        tqdm_stream = log_tab.get_tqdm_stream()
        
        self.assertIsNotNone(tqdm_stream)
    
    def test_log_tab_get_log_handler(self):
        """LogTab get_log_handler 테스트"""
        from gui.tabs.log_tab import LogTab
        
        log_tab = LogTab(self.root, Mock(), Mock())
        log_tab.create_widgets()
        
        handler = log_tab.get_log_handler()
        
        self.assertIsNotNone(handler)


class TestComponentIntegration(unittest.TestCase):
    """컴포넌트 간 통합 테스트"""
    
    @classmethod
    def setUpClass(cls):
        """테스트 클래스 시작 전 Tk 루트 생성"""
        cls.root = get_shared_root()
    
    @classmethod
    def tearDownClass(cls):
        """테스트 클래스 종료 후 정리 (루트는 유지)"""
        pass
    
    def test_tooltip_with_scrollable_frame(self):
        """ScrollableFrame 내부 위젯에 Tooltip 적용 테스트"""
        from gui.components.scrollable_frame import ScrollableFrame
        from gui.components.tooltip import Tooltip
        
        scroll_frame = ScrollableFrame(self.root)
        scroll_frame.pack()
        
        # ScrollableFrame 내부에 버튼 추가
        button = tk.Button(scroll_frame.scrollable_frame, text="도움말 버튼")
        button.pack()
        
        # 버튼에 Tooltip 추가
        tooltip = Tooltip(button, "이것은 도움말 툴팁입니다")
        
        self.assertIsNotNone(tooltip)
        self.assertEqual(tooltip.widget, button)
    
    def test_log_handler_with_logger(self):
        """GuiLogHandler를 실제 Logger에 연결하는 테스트"""
        from gui.components.log_handlers import GuiLogHandler
        
        text_widget = scrolledtext.ScrolledText(self.root)
        text_widget.pack()
        
        # 로거 생성 및 핸들러 연결
        logger = logging.getLogger("test_integration")
        logger.setLevel(logging.DEBUG)
        
        handler = GuiLogHandler(text_widget)
        handler.setFormatter(logging.Formatter('%(levelname)s - %(message)s'))
        logger.addHandler(handler)
        
        # 로그 출력 (ERROR 레벨은 표시됨)
        logger.error("통합 테스트 에러 메시지")
        
        self.root.update()
        
        content = text_widget.get("1.0", tk.END)
        self.assertIn("통합 테스트 에러 메시지", content)
        
        # 정리
        logger.removeHandler(handler)
        text_widget.destroy()


class TestImports(unittest.TestCase):
    """GUI 모듈 임포트 테스트"""
    
    def test_import_tooltip(self):
        """Tooltip 임포트 테스트"""
        from gui.components.tooltip import Tooltip
        self.assertIsNotNone(Tooltip)
    
    def test_import_scrollable_frame(self):
        """ScrollableFrame 임포트 테스트"""
        from gui.components.scrollable_frame import ScrollableFrame
        self.assertIsNotNone(ScrollableFrame)
    
    def test_import_log_handlers(self):
        """log_handlers 모듈 임포트 테스트"""
        from gui.components.log_handlers import GuiLogHandler, TqdmToTkinter
        self.assertIsNotNone(GuiLogHandler)
        self.assertIsNotNone(TqdmToTkinter)
    
    def test_import_base_tab(self):
        """BaseTab 임포트 테스트"""
        from gui.tabs.base_tab import BaseTab
        self.assertIsNotNone(BaseTab)
    
    def test_import_log_tab(self):
        """LogTab 임포트 테스트"""
        from gui.tabs.log_tab import LogTab
        self.assertIsNotNone(LogTab)
    
    def test_import_settings_tab(self):
        """SettingsTab 임포트 테스트"""
        from gui.tabs.settings_tab import SettingsTab
        self.assertIsNotNone(SettingsTab)
    
    def test_import_glossary_tab(self):
        """GlossaryTab 임포트 테스트"""
        from gui.tabs.glossary_tab import GlossaryTab
        self.assertIsNotNone(GlossaryTab)
    
    def test_import_main_window(self):
        """main_window 모듈 임포트 테스트"""
        from gui.main_window import BatchTranslatorGUI, run_gui
        self.assertIsNotNone(BatchTranslatorGUI)
        self.assertIsNotNone(run_gui)
    
    def test_import_glossary_editor(self):
        """GlossaryEditorWindow 임포트 테스트"""
        from gui.dialogs.glossary_editor import GlossaryEditorWindow
        self.assertIsNotNone(GlossaryEditorWindow)


if __name__ == "__main__":
    # 테스트 실행
    try:
        unittest.main(verbosity=2)
    finally:
        # 테스트 완료 후 공유 루트 정리
        cleanup_shared_root()
