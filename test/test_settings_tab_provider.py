"""
PySide6 Settings Tab Multi-Provider (Claude / Codex CLI / OpenAI Compatible) UI Unit Tests
"""

import sys
import unittest
from unittest.mock import MagicMock
from PySide6 import QtWidgets

from gui_qt.tabs_qt.settings_tab_qt import SettingsTabQt


class TestSettingsTabProvider(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance()
        if cls.app is None:
            cls.app = QtWidgets.QApplication(sys.argv)

    def setUp(self):
        self.mock_app_service = MagicMock()
        self.mock_app_service.config = {
            "llm_provider": "gemini",
            "model_name": "gemini-2.0-flash",
            "claude_cli_path": "claude",
            "claude_cli_model": "default",
            "codex_cli_path": "codex",
            "codex_cli_model": "gpt-5.5",
            "openai_compatible_base_url": "https://api.openai.com/v1/chat/completions",
            "openai_compatible_model": "default",
            "enable_pagefold": True,
        }
        self.mock_app_service.config_manager = MagicMock()
        self.mock_app_service.config_manager.get_default_config.return_value = {
            "llm_provider": "gemini",
            "model_name": "gemini-2.0-flash",
            "claude_cli_path": "claude",
            "claude_cli_model": "default",
            "codex_cli_path": "codex",
            "codex_cli_model": "gpt-5.5",
            "openai_compatible_base_url": "https://api.openai.com/v1/chat/completions",
            "openai_compatible_model": "default",
            "enable_pagefold": True,
        }
        self.tab = SettingsTabQt(self.mock_app_service)

    def tearDown(self):
        self.tab.deleteLater()

    def test_initial_gemini_provider_ui_state(self):
        """기본 Gemini 프로바이더 초기 UI 상태 검증"""
        self.assertEqual(self.tab.provider_combo.currentData(), "gemini")
        # Gemini는 API 키 입력창이 보이고 CLI 경로/Base URL은 숨겨져야 함
        self.assertTrue(self.tab.api_form.isRowVisible(self.tab.api_keys_edit))
        self.assertFalse(self.tab.api_form.isRowVisible(self.tab.cli_path_edit))
        self.assertFalse(self.tab.api_form.isRowVisible(self.tab.base_url_edit))
        self.assertTrue(self.tab.enable_pagefold_check.isEnabled())

    def test_switch_to_claude_cli(self):
        """Claude CLI 프로바이더 전환 시 UI 가시성 및 PageFold 비활성화 검증"""
        idx = self.tab.provider_combo.findData("claude_cli")
        self.assertNotEqual(idx, -1)
        self.tab.provider_combo.setCurrentIndex(idx)

        self.assertTrue(self.tab.api_form.isRowVisible(self.tab.cli_path_edit))
        self.assertFalse(self.tab.api_form.isRowVisible(self.tab.base_url_edit))
        # API 키 입력창은 선택 사항 안내와 함께 표시됨
        self.assertTrue(self.tab.api_form.isRowVisible(self.tab.api_keys_edit))
        self.assertIn("선택 사항", self.tab.api_keys_edit.placeholderText())
        self.assertFalse(self.tab.api_form.isRowVisible(self.tab.use_vertex_check))
        # PageFold는 비활성화 및 미체크
        self.assertFalse(self.tab.enable_pagefold_check.isEnabled())
        self.assertFalse(self.tab.enable_pagefold_check.isChecked())

        # Claude 추천 모델 목록 확인
        items = [self.tab.model_name_combo.itemText(i) for i in range(self.tab.model_name_combo.count())]
        self.assertIn("default", items)
        self.assertIn("claude-3-5-sonnet-20241022", items)

    def test_switch_to_codex_cli(self):
        """Codex CLI 프로바이더 전환 시 UI 가시성 및 추천 모델 검증"""
        idx = self.tab.provider_combo.findData("codex_cli")
        self.assertNotEqual(idx, -1)
        self.tab.provider_combo.setCurrentIndex(idx)

        self.assertTrue(self.tab.api_form.isRowVisible(self.tab.cli_path_edit))
        self.assertFalse(self.tab.api_form.isRowVisible(self.tab.base_url_edit))
        self.assertTrue(self.tab.api_form.isRowVisible(self.tab.api_keys_edit))
        self.assertIn("선택 사항", self.tab.api_keys_edit.placeholderText())
        self.assertFalse(self.tab.enable_pagefold_check.isEnabled())

        # Codex 추천 모델 목록 확인
        items = [self.tab.model_name_combo.itemText(i) for i in range(self.tab.model_name_combo.count())]
        self.assertIn("default", items)
        self.assertIn("gpt-5.5", items)

    def test_auth_check_button_exists(self):
        """인증/연결 테스트 버튼 존재 확인"""
        self.assertTrue(hasattr(self.tab, "auth_check_btn"))
        self.assertTrue(self.tab.auth_check_btn.isEnabled())

    def test_switch_to_antigravity_cli(self):
        """Antigravity CLI 프로바이더 전환 시 UI 가시성 및 추천 모델 검증"""
        idx = self.tab.provider_combo.findData("antigravity_cli")
        self.assertNotEqual(idx, -1)
        self.tab.provider_combo.setCurrentIndex(idx)

        self.assertTrue(self.tab.api_form.isRowVisible(self.tab.cli_path_edit))
        self.assertFalse(self.tab.api_form.isRowVisible(self.tab.base_url_edit))
        self.assertTrue(self.tab.api_form.isRowVisible(self.tab.api_keys_edit))
        self.assertIn("Antigravity CLI", self.tab.api_keys_edit.placeholderText())
        self.assertFalse(self.tab.enable_pagefold_check.isEnabled())

        # AGY 추천 모델 목록 확인
        items = [self.tab.model_name_combo.itemText(i) for i in range(self.tab.model_name_combo.count())]
        self.assertIn("default", items)
        self.assertIn("gemini-3.8-flash-high", items)

    def test_switch_to_openai_compatible(self):
        """OpenAI Compatible 프로바이더 전환 시 UI 가시성 검증"""
        idx = self.tab.provider_combo.findData("openai_compatible")
        self.assertNotEqual(idx, -1)
        self.tab.provider_combo.setCurrentIndex(idx)

        self.assertFalse(self.tab.api_form.isRowVisible(self.tab.cli_path_edit))
        self.assertTrue(self.tab.api_form.isRowVisible(self.tab.base_url_edit))
        self.assertTrue(self.tab.api_form.isRowVisible(self.tab.api_keys_edit))

    def test_save_config_with_claude_cli(self):
        """Claude CLI 설정 저장 직렬화 검증"""
        idx = self.tab.provider_combo.findData("claude_cli")
        self.tab.provider_combo.setCurrentIndex(idx)
        self.tab.cli_path_edit.setText("C:\\Users\\Test\\.local\\bin\\claude.exe")
        self.tab.model_name_combo.setCurrentText("claude-3-5-sonnet-20241022")

        self.tab._save_config_to_service()

        self.mock_app_service.save_app_config.assert_called()
        saved_cfg = self.mock_app_service.save_app_config.call_args[0][0]
        self.assertEqual(saved_cfg.get("llm_provider"), "claude_cli")
        self.assertEqual(saved_cfg.get("claude_cli_path"), "C:\\Users\\Test\\.local\\bin\\claude.exe")
        self.assertEqual(saved_cfg.get("claude_cli_model"), "claude-3-5-sonnet-20241022")

    def test_load_config_with_codex_cli(self):
        """Codex CLI 설정 로드 및 역직렬화 검증"""
        custom_cfg = {
            "llm_provider": "codex_cli",
            "codex_cli_path": "C:\\Codex\\codex.exe",
            "codex_cli_model": "gpt-5.6-sol",
            "enable_pagefold": False,
        }
        self.mock_app_service.config = custom_cfg
        self.tab._load_config()

        self.assertEqual(self.tab.provider_combo.currentData(), "codex_cli")
        self.assertEqual(self.tab.cli_path_edit.text(), "C:\\Codex\\codex.exe")
        self.assertEqual(self.tab.model_name_combo.currentText(), "gpt-5.6-sol")


if __name__ == "__main__":
    unittest.main()
