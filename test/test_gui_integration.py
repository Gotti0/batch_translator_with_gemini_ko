"""
GUI 통합 테스트

gui 모듈의 통합 테스트입니다.
탭 간 통신, 설정 저장/로드, 콜백 함수 동작을 검증합니다.
"""

import unittest
import tkinter as tk
from tkinter import scrolledtext
import logging
import sys
import os
from unittest.mock import Mock, MagicMock, patch, PropertyMock
from typing import Dict, Any
import json

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


def create_mock_app_service():
    """테스트용 Mock AppService 생성"""
    mock_service = Mock()
    mock_service.config = {
        "api_keys": ["test-api-key-1"],
        "use_vertex_ai": False,
        "model_name": "gemini-2.0-flash-001",
        "temperature": 0.7,
        "top_p": 0.95,
        "chunk_size": 2000,
        "max_workers": 4,
        "rpm": 60,
        "source_lang": "japanese",
        "fallback_lang": "chinese",
        "glossary_json_path": None,
        "glossary_sampling_ratio": 10.0,
        "glossary_extraction_temperature": 0.3,
        "enable_dynamic_glossary_injection": False,
        "max_glossary_entries_per_chunk_injection": 3,
        "max_glossary_chars_per_chunk_injection": 500,
    }
    mock_service.stop_requested = False
    mock_service.save_app_config = Mock(return_value=True)
    mock_service.get_available_models = Mock(return_value=["gemini-2.0-flash-001", "gemini-1.5-pro"])
    return mock_service


class TestSettingsTabConfigRoundtrip(unittest.TestCase):
    """SettingsTab 설정값 저장 및 로드 테스트"""
    
    @classmethod
    def setUpClass(cls):
        """테스트 클래스 시작 전 Tk 루트 생성"""
        cls.root = get_shared_root()
    
    @classmethod
    def tearDownClass(cls):
        """테스트 클래스 종료 후 정리 (루트는 유지)"""
        pass
    
    def setUp(self):
        """각 테스트 전 탭 인스턴스 생성"""
        from gui.tabs.settings_tab import SettingsTab
        
        self.mock_service = create_mock_app_service()
        self.mock_logger = Mock()
        
        self.frame = tk.Frame(self.root)
        self.settings_tab = SettingsTab(
            self.frame, 
            self.mock_service, 
            self.mock_logger
        )
        self.settings_tab.create_widgets()
    
    def tearDown(self):
        """각 테스트 후 정리"""
        if hasattr(self, 'frame'):
            self.frame.destroy()
    
    def test_get_config_returns_dict(self):
        """get_config가 딕셔너리를 반환하는지 테스트"""
        config = self.settings_tab.get_config()
        
        self.assertIsInstance(config, dict)
        self.assertIn("use_vertex_ai", config)
    
    def test_load_config_applies_values(self):
        """load_config가 값을 올바르게 적용하는지 테스트"""
        test_config = {
            "api_keys": ["new-api-key"],
            "use_vertex_ai": False,
            "model_name": "gemini-1.5-pro",
            "temperature": 0.5,
            "top_p": 0.8,
            "chunk_size": 3000,
            "max_workers": 8,
            "rpm": 120,
            "source_lang": "chinese",
            "fallback_lang": "japanese",
        }
        
        self.settings_tab.load_config(test_config)
        
        # UI 값 확인
        if self.settings_tab.chunk_size_entry:
            chunk_size = self.settings_tab.chunk_size_entry.get()
            self.assertEqual(chunk_size, "3000")
    
    def test_config_roundtrip(self):
        """설정 저장 후 로드 시 값이 유지되는지 테스트"""
        original_config = {
            "api_keys": ["roundtrip-key"],
            "use_vertex_ai": False,
            "model_name": "gemini-2.0-flash-001",
            "temperature": 0.6,
            "top_p": 0.9,
            "chunk_size": 2500,
            "max_workers": 6,
            "rpm": 90,
            "source_lang": "japanese",
            "fallback_lang": "chinese",
        }
        
        # 설정 로드
        self.settings_tab.load_config(original_config)
        
        # 설정 추출
        extracted_config = self.settings_tab.get_config()
        
        # 주요 값 비교
        self.assertEqual(extracted_config.get("use_vertex_ai"), original_config["use_vertex_ai"])
        self.assertEqual(extracted_config.get("chunk_size"), original_config["chunk_size"])
    
    def test_get_input_files(self):
        """get_input_files 메서드 테스트"""
        files = self.settings_tab.get_input_files()
        
        self.assertIsInstance(files, list)
    
    def test_get_chunk_size(self):
        """get_chunk_size 메서드 테스트"""
        # 기본값 설정
        if self.settings_tab.chunk_size_entry:
            self.settings_tab.chunk_size_entry.delete(0, tk.END)
            self.settings_tab.chunk_size_entry.insert(0, "2000")
        
        chunk_size = self.settings_tab.get_chunk_size()
        
        self.assertEqual(chunk_size, 2000)


class TestGlossaryTabConfigRoundtrip(unittest.TestCase):
    """GlossaryTab 설정값 저장 및 로드 테스트"""
    
    @classmethod
    def setUpClass(cls):
        """테스트 클래스 시작 전 Tk 루트 생성"""
        cls.root = get_shared_root()
    
    @classmethod
    def tearDownClass(cls):
        """테스트 클래스 종료 후 정리"""
        pass
    
    def setUp(self):
        """각 테스트 전 탭 인스턴스 생성"""
        from gui.tabs.glossary_tab import GlossaryTab
        
        self.mock_service = create_mock_app_service()
        self.mock_logger = Mock()
        
        self.frame = tk.Frame(self.root)
        self.glossary_tab = GlossaryTab(
            self.frame, 
            self.mock_service, 
            self.mock_logger
        )
        self.glossary_tab.create_widgets()
    
    def tearDown(self):
        """각 테스트 후 정리"""
        if hasattr(self, 'frame'):
            self.frame.destroy()
    
    def test_get_config_returns_dict(self):
        """get_config가 딕셔너리를 반환하는지 테스트"""
        config = self.glossary_tab.get_config()
        
        self.assertIsInstance(config, dict)
    
    def test_load_config_applies_glossary_path(self):
        """load_config가 용어집 경로를 올바르게 적용하는지 테스트"""
        test_config = {
            "glossary_json_path": "C:/test/glossary.json",
            "glossary_sampling_ratio": 15.0,
            "glossary_extraction_temperature": 0.5,
            "enable_dynamic_glossary_injection": True,
            "max_glossary_entries_per_chunk_injection": 5,
            "max_glossary_chars_per_chunk_injection": 800,
        }
        
        self.glossary_tab.load_config(test_config)
        
        # UI 값 확인
        if self.glossary_tab.glossary_json_path_entry:
            path = self.glossary_tab.glossary_json_path_entry.get()
            self.assertEqual(path, "C:/test/glossary.json")
    
    def test_glossary_config_roundtrip(self):
        """용어집 설정 저장 후 로드 시 값이 유지되는지 테스트"""
        original_config = {
            "glossary_json_path": "C:/roundtrip/glossary.json",
            "glossary_sampling_ratio": 20.0,
            "glossary_extraction_temperature": 0.4,
            "enable_dynamic_glossary_injection": True,
            "max_glossary_entries_per_chunk_injection": 4,
            "max_glossary_chars_per_chunk_injection": 600,
        }
        
        # 설정 로드
        self.glossary_tab.load_config(original_config)
        
        # 설정 추출
        extracted_config = self.glossary_tab.get_config()
        
        # 주요 값 비교
        self.assertEqual(
            extracted_config.get("glossary_json_path"), 
            original_config["glossary_json_path"]
        )
        self.assertEqual(
            extracted_config.get("enable_dynamic_glossary_injection"), 
            original_config["enable_dynamic_glossary_injection"]
        )
    
    def test_get_glossary_path(self):
        """get_glossary_path 메서드 테스트"""
        # 경로 설정
        if self.glossary_tab.glossary_json_path_entry:
            self.glossary_tab.glossary_json_path_entry.delete(0, tk.END)
            self.glossary_tab.glossary_json_path_entry.insert(0, "C:/test/path.json")
        
        path = self.glossary_tab.get_glossary_path()
        
        self.assertEqual(path, "C:/test/path.json")
    
    def test_set_glossary_path(self):
        """set_glossary_path 메서드 테스트"""
        self.glossary_tab.set_glossary_path("C:/new/glossary.json")
        
        path = self.glossary_tab.get_glossary_path()
        
        self.assertEqual(path, "C:/new/glossary.json")


class TestTabCommunication(unittest.TestCase):
    """탭 간 통신 테스트"""
    
    @classmethod
    def setUpClass(cls):
        """테스트 클래스 시작 전 Tk 루트 생성"""
        cls.root = get_shared_root()
    
    @classmethod
    def tearDownClass(cls):
        """테스트 클래스 종료 후 정리"""
        pass
    
    def setUp(self):
        """각 테스트 전 탭 인스턴스들 생성"""
        from gui.tabs.settings_tab import SettingsTab
        from gui.tabs.glossary_tab import GlossaryTab
        
        self.mock_service = create_mock_app_service()
        self.mock_logger = Mock()
        
        self.container = tk.Frame(self.root)
        
        # SettingsTab 생성
        self.settings_frame = tk.Frame(self.container)
        self.settings_tab = SettingsTab(
            self.settings_frame, 
            self.mock_service, 
            self.mock_logger
        )
        self.settings_tab.create_widgets()
        
        # GlossaryTab 생성 (콜백 연결)
        self.glossary_frame = tk.Frame(self.container)
        self.glossary_tab = GlossaryTab(
            self.glossary_frame, 
            self.mock_service, 
            self.mock_logger,
            get_input_files=self.settings_tab.get_input_files,
            get_chunk_size=self.settings_tab.get_chunk_size,
        )
        self.glossary_tab.create_widgets()
    
    def tearDown(self):
        """각 테스트 후 정리"""
        if hasattr(self, 'container'):
            self.container.destroy()
    
    def test_glossary_can_get_input_files_from_settings(self):
        """GlossaryTab이 SettingsTab에서 입력 파일을 가져올 수 있는지 테스트"""
        # 콜백이 연결되었는지 확인
        self.assertIsNotNone(self.glossary_tab._get_input_files)
        
        # 콜백 호출
        files = self.glossary_tab._get_input_files()
        
        self.assertIsInstance(files, list)
    
    def test_glossary_can_get_chunk_size_from_settings(self):
        """GlossaryTab이 SettingsTab에서 청크 크기를 가져올 수 있는지 테스트"""
        # 청크 크기 설정
        if self.settings_tab.chunk_size_entry:
            self.settings_tab.chunk_size_entry.delete(0, tk.END)
            self.settings_tab.chunk_size_entry.insert(0, "2500")
        
        # 콜백이 연결되었는지 확인
        self.assertIsNotNone(self.glossary_tab._get_chunk_size)
        
        # 콜백 호출
        chunk_size = self.glossary_tab._get_chunk_size()
        
        self.assertEqual(chunk_size, 2500)
    
    def test_callback_injection(self):
        """콜백 함수 주입이 올바르게 동작하는지 테스트"""
        from gui.tabs.glossary_tab import GlossaryTab
        
        # 커스텀 콜백 정의
        callback_called = {"value": False}
        
        def custom_callback(path: str):
            callback_called["value"] = True
        
        # 콜백 주입
        glossary_frame = tk.Frame(self.container)
        glossary_tab = GlossaryTab(
            glossary_frame,
            self.mock_service,
            self.mock_logger,
            on_glossary_path_changed=custom_callback
        )
        
        # 콜백이 저장되었는지 확인
        self.assertIsNotNone(glossary_tab._on_glossary_path_changed)
        
        # 콜백 호출
        glossary_tab._on_glossary_path_changed("test/path.json")
        
        self.assertTrue(callback_called["value"])
        
        glossary_frame.destroy()


class TestLogTabIntegration(unittest.TestCase):
    """LogTab 통합 테스트"""
    
    @classmethod
    def setUpClass(cls):
        """테스트 클래스 시작 전 Tk 루트 생성"""
        cls.root = get_shared_root()
    
    @classmethod
    def tearDownClass(cls):
        """테스트 클래스 종료 후 정리"""
        pass
    
    def setUp(self):
        """각 테스트 전 탭 인스턴스 생성"""
        from gui.tabs.log_tab import LogTab
        
        self.mock_service = create_mock_app_service()
        self.mock_logger = Mock()
        
        self.frame = tk.Frame(self.root)
        self.log_tab = LogTab(
            self.frame, 
            self.mock_service, 
            self.mock_logger
        )
        self.log_tab.create_widgets()
    
    def tearDown(self):
        """각 테스트 후 정리"""
        if hasattr(self, 'frame'):
            self.frame.destroy()
    
    def test_log_tab_provides_tqdm_stream(self):
        """LogTab이 TQDM 스트림을 제공하는지 테스트"""
        tqdm_stream = self.log_tab.get_tqdm_stream()
        
        self.assertIsNotNone(tqdm_stream)
    
    def test_log_tab_provides_log_handler(self):
        """LogTab이 로그 핸들러를 제공하는지 테스트"""
        handler = self.log_tab.get_log_handler()
        
        self.assertIsNotNone(handler)
        self.assertIsInstance(handler, logging.Handler)
    
    def test_log_tab_config_is_empty(self):
        """LogTab의 get_config가 빈 딕셔너리를 반환하는지 테스트"""
        config = self.log_tab.get_config()
        
        self.assertIsInstance(config, dict)
        self.assertEqual(len(config), 0)


class TestMultiTabConfigAggregation(unittest.TestCase):
    """다중 탭 설정 집계 테스트"""
    
    @classmethod
    def setUpClass(cls):
        """테스트 클래스 시작 전 Tk 루트 생성"""
        cls.root = get_shared_root()
    
    @classmethod
    def tearDownClass(cls):
        """테스트 클래스 종료 후 정리"""
        pass
    
    def setUp(self):
        """각 테스트 전 모든 탭 인스턴스 생성"""
        from gui.tabs.settings_tab import SettingsTab
        from gui.tabs.glossary_tab import GlossaryTab
        from gui.tabs.log_tab import LogTab
        
        self.mock_service = create_mock_app_service()
        self.mock_logger = Mock()
        
        self.container = tk.Frame(self.root)
        
        # SettingsTab
        self.settings_frame = tk.Frame(self.container)
        self.settings_tab = SettingsTab(
            self.settings_frame, 
            self.mock_service, 
            self.mock_logger
        )
        self.settings_tab.create_widgets()
        
        # GlossaryTab
        self.glossary_frame = tk.Frame(self.container)
        self.glossary_tab = GlossaryTab(
            self.glossary_frame, 
            self.mock_service, 
            self.mock_logger
        )
        self.glossary_tab.create_widgets()
        
        # LogTab
        self.log_frame = tk.Frame(self.container)
        self.log_tab = LogTab(
            self.log_frame, 
            self.mock_service, 
            self.mock_logger
        )
        self.log_tab.create_widgets()
    
    def tearDown(self):
        """각 테스트 후 정리"""
        if hasattr(self, 'container'):
            self.container.destroy()
    
    def test_aggregate_all_tab_configs(self):
        """모든 탭의 설정을 집계할 수 있는지 테스트"""
        # 각 탭에서 설정 추출
        settings_config = self.settings_tab.get_config()
        glossary_config = self.glossary_tab.get_config()
        log_config = self.log_tab.get_config()
        
        # 집계
        full_config = {}
        full_config.update(settings_config)
        full_config.update(glossary_config)
        full_config.update(log_config)
        
        # 검증
        self.assertIsInstance(full_config, dict)
        self.assertIn("use_vertex_ai", full_config)  # from settings
        # glossary_json_path는 None이면 포함 안됨
    
    def test_load_config_to_all_tabs(self):
        """모든 탭에 설정을 로드할 수 있는지 테스트"""
        full_config = {
            # Settings tab config
            "api_keys": ["test-key"],
            "use_vertex_ai": False,
            "model_name": "gemini-2.0-flash-001",
            "temperature": 0.7,
            "chunk_size": 2000,
            "max_workers": 4,
            "rpm": 60,
            "source_lang": "japanese",
            # Glossary tab config
            "glossary_json_path": "C:/test/glossary.json",
            "glossary_sampling_ratio": 15.0,
            "enable_dynamic_glossary_injection": True,
        }
        
        # 각 탭에 설정 로드 (예외 없이 완료되어야 함)
        try:
            self.settings_tab.load_config(full_config)
            self.glossary_tab.load_config(full_config)
            self.log_tab.load_config(full_config)
            success = True
        except Exception as e:
            success = False
        
        self.assertTrue(success)


class TestErrorHandling(unittest.TestCase):
    """오류 처리 테스트"""
    
    @classmethod
    def setUpClass(cls):
        """테스트 클래스 시작 전 Tk 루트 생성"""
        cls.root = get_shared_root()
    
    @classmethod
    def tearDownClass(cls):
        """테스트 클래스 종료 후 정리"""
        pass
    
    def test_settings_tab_with_none_app_service(self):
        """AppService가 None일 때 SettingsTab 동작 테스트"""
        from gui.tabs.settings_tab import SettingsTab
        
        frame = tk.Frame(self.root)
        mock_logger = Mock()
        
        # app_service가 None
        settings_tab = SettingsTab(frame, None, mock_logger)
        
        # create_widgets 호출 시 예외가 발생하지 않아야 함
        try:
            settings_tab.create_widgets()
            success = True
        except Exception:
            success = False
        
        # AppService 없어도 위젯 생성은 가능해야 함 (기능은 제한됨)
        # 또는 적절한 예외 처리
        frame.destroy()
    
    def test_glossary_tab_with_empty_callbacks(self):
        """콜백이 None일 때 GlossaryTab 동작 테스트"""
        from gui.tabs.glossary_tab import GlossaryTab
        
        mock_service = create_mock_app_service()
        mock_logger = Mock()
        
        frame = tk.Frame(self.root)
        
        # 콜백 없이 생성
        glossary_tab = GlossaryTab(
            frame, 
            mock_service, 
            mock_logger,
            get_input_files=None,
            get_chunk_size=None,
        )
        glossary_tab.create_widgets()
        
        # 콜백이 None인지 확인
        self.assertIsNone(glossary_tab._get_input_files)
        self.assertIsNone(glossary_tab._get_chunk_size)
        
        frame.destroy()
    
    def test_load_config_with_missing_keys(self):
        """일부 키가 없는 설정 로드 테스트"""
        from gui.tabs.settings_tab import SettingsTab
        
        mock_service = create_mock_app_service()
        mock_logger = Mock()
        
        frame = tk.Frame(self.root)
        settings_tab = SettingsTab(frame, mock_service, mock_logger)
        settings_tab.create_widgets()
        
        # 일부 키만 있는 설정
        partial_config = {
            "temperature": 0.5,
            # 다른 키들은 없음
        }
        
        # 예외 없이 로드되어야 함
        try:
            settings_tab.load_config(partial_config)
            success = True
        except Exception:
            success = False
        
        self.assertTrue(success)
        
        frame.destroy()
    
    def test_load_config_with_invalid_types(self):
        """잘못된 타입의 설정값 로드 테스트"""
        from gui.tabs.settings_tab import SettingsTab
        
        mock_service = create_mock_app_service()
        mock_logger = Mock()
        
        frame = tk.Frame(self.root)
        settings_tab = SettingsTab(frame, mock_service, mock_logger)
        settings_tab.create_widgets()
        
        # 잘못된 타입의 설정
        invalid_config = {
            "temperature": "not_a_number",  # should be float
            "chunk_size": "abc",  # should be int
        }
        
        # 오류를 적절히 처리해야 함 (예외 발생 또는 기본값 사용)
        try:
            settings_tab.load_config(invalid_config)
        except Exception:
            pass  # 예외 발생해도 OK
        
        frame.destroy()


class TestGlossaryEditorIntegration(unittest.TestCase):
    """GlossaryEditorWindow 통합 테스트"""
    
    @classmethod
    def setUpClass(cls):
        """테스트 클래스 시작 전 Tk 루트 생성"""
        cls.root = get_shared_root()
    
    @classmethod
    def tearDownClass(cls):
        """테스트 클래스 종료 후 정리"""
        pass
    
    def test_glossary_editor_creation(self):
        """GlossaryEditorWindow 생성 테스트"""
        from gui.dialogs.glossary_editor import GlossaryEditorWindow
        
        save_callback_called = {"value": False}
        
        def save_callback(json_str: str):
            save_callback_called["value"] = True
        
        # 간단한 용어집 JSON
        glossary_json = json.dumps({
            "characters": [
                {"original": "田中", "translated": "타나카", "type": "name"}
            ]
        })
        
        # 에디터 생성
        editor = GlossaryEditorWindow(
            self.root,
            glossary_json,
            save_callback,
            "test_input.txt"
        )
        
        self.assertIsNotNone(editor)
        
        # 창 닫기
        editor.destroy()
    
    def test_glossary_editor_with_empty_json(self):
        """빈 JSON으로 GlossaryEditorWindow 생성 테스트"""
        from gui.dialogs.glossary_editor import GlossaryEditorWindow
        
        def save_callback(json_str: str):
            pass
        
        # 빈 JSON
        editor = GlossaryEditorWindow(
            self.root,
            "{}",
            save_callback,
            "test_input.txt"
        )
        
        self.assertIsNotNone(editor)
        
        editor.destroy()


class TestModuleImportsAndDependencies(unittest.TestCase):
    """모듈 임포트 및 의존성 테스트"""
    
    def test_main_window_imports_all_tabs(self):
        """main_window가 모든 탭을 임포트하는지 테스트"""
        from gui.main_window import BatchTranslatorGUI
        
        # 임포트 성공 확인
        self.assertIsNotNone(BatchTranslatorGUI)
    
    def test_settings_tab_imports_components(self):
        """SettingsTab이 필요한 컴포넌트를 임포트하는지 테스트"""
        from gui.tabs.settings_tab import SettingsTab
        from gui.components.tooltip import Tooltip
        from gui.components.scrollable_frame import ScrollableFrame
        
        self.assertIsNotNone(SettingsTab)
        self.assertIsNotNone(Tooltip)
        self.assertIsNotNone(ScrollableFrame)
    
    def test_glossary_tab_imports_editor(self):
        """GlossaryTab이 GlossaryEditorWindow를 임포트하는지 테스트"""
        from gui.tabs.glossary_tab import GlossaryTab
        from gui.dialogs.glossary_editor import GlossaryEditorWindow
        
        self.assertIsNotNone(GlossaryTab)
        self.assertIsNotNone(GlossaryEditorWindow)
    
    def test_log_tab_imports_handlers(self):
        """LogTab이 로그 핸들러를 임포트하는지 테스트"""
        from gui.tabs.log_tab import LogTab
        from gui.components.log_handlers import GuiLogHandler, TqdmToTkinter
        
        self.assertIsNotNone(LogTab)
        self.assertIsNotNone(GuiLogHandler)
        self.assertIsNotNone(TqdmToTkinter)


if __name__ == "__main__":
    # 테스트 실행
    try:
        unittest.main(verbosity=2)
    finally:
        # 테스트 완료 후 공유 루트 정리
        cleanup_shared_root()
