"""
PySide6 Settings Tab Multi-Provider (Claude / Codex CLI / OpenAI Compatible) UI Unit Tests
"""

import sys
import unittest
from unittest.mock import MagicMock, patch
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

    def test_vertex_toggle_shows_service_account_rows(self):
        """Vertex 체크 시 서비스 계정·GCP 행이 보이고 API 키 입력이 잠긴다"""
        vertex_rows = (self.tab.sa_row_widget, self.tab.gcp_project_edit, self.tab.gcp_location_edit)
        for w in vertex_rows:
            self.assertFalse(self.tab.api_form.isRowVisible(w))

        with patch.object(QtWidgets.QMessageBox, "information") as info:
            self.tab.use_vertex_check.setChecked(True)
        info.assert_called_once()  # 서비스 계정 경로가 비어 있으면 안내
        for w in vertex_rows:
            self.assertTrue(self.tab.api_form.isRowVisible(w))
            self.assertTrue(w.isEnabled())
        self.assertFalse(self.tab.api_keys_edit.isEnabled())

        self.tab.use_vertex_check.setChecked(False)
        for w in vertex_rows:
            self.assertFalse(self.tab.api_form.isRowVisible(w))
        self.assertTrue(self.tab.api_keys_edit.isEnabled())

    def test_vertex_state_ignored_for_non_gemini_provider(self):
        """Vertex가 켜져 있어도 다른 프로바이더에서는 Vertex 행을 숨기고 API 키를 연다"""
        with patch.object(QtWidgets.QMessageBox, "information"):
            self.tab.use_vertex_check.setChecked(True)
        self.tab.provider_combo.setCurrentIndex(self.tab.provider_combo.findData("claude_cli"))
        self.assertFalse(self.tab.api_form.isRowVisible(self.tab.sa_row_widget))
        self.assertTrue(self.tab.api_keys_edit.isEnabled())

    def test_load_config_with_vertex_does_not_prompt(self):
        """Vertex가 저장된 설정을 불러올 때는 안내창 없이 행만 표시한다"""
        self.mock_app_service.config = {"llm_provider": "gemini", "use_vertex_ai": True}
        with patch.object(QtWidgets.QMessageBox, "information") as info:
            self.tab._load_config()
        info.assert_not_called()
        self.assertTrue(self.tab.use_vertex_check.isChecked())
        self.assertTrue(self.tab.api_form.isRowVisible(self.tab.sa_row_widget))
        self.assertFalse(self.tab.api_keys_edit.isEnabled())

    def test_api_keys_are_scoped_per_provider(self):
        """프로바이더를 바꾸면 키 입력칸도 그 프로바이더 몫으로 바뀌고, 돌아오면 복원된다"""
        self.mock_app_service.config = dict(self.mock_app_service.config, api_keys=["AQ.gemini-1", "AQ.gemini-2"])
        self.tab._load_config()
        self.assertEqual(self.tab.api_keys_edit.toPlainText(), "AQ.gemini-1\nAQ.gemini-2")

        self.tab.provider_combo.setCurrentIndex(self.tab.provider_combo.findData("claude_cli"))
        self.assertEqual(self.tab.api_keys_edit.toPlainText(), "")  # 비어 있으면 로그인 세션 사용
        cfg = self.tab._build_provider_config_from_ui()
        self.assertIsNone(cfg["claude_cli_api_key"])
        self.assertEqual(cfg["api_keys"], [])

        self.tab.api_keys_edit.setPlainText("sk-ant-own")
        self.tab.provider_combo.setCurrentIndex(self.tab.provider_combo.findData("codex_cli"))
        self.assertEqual(self.tab.api_keys_edit.toPlainText(), "")

        self.tab.provider_combo.setCurrentIndex(self.tab.provider_combo.findData("claude_cli"))
        self.assertEqual(self.tab.api_keys_edit.toPlainText(), "sk-ant-own")
        self.tab.provider_combo.setCurrentIndex(self.tab.provider_combo.findData("gemini"))
        self.assertEqual(self.tab.api_keys_edit.toPlainText(), "AQ.gemini-1\nAQ.gemini-2")

    def test_save_keeps_each_provider_key_in_its_own_field(self):
        """Claude 화면에서 저장해도 Gemini 키 목록이 Anthropic 키로 덮이지 않는다"""
        self.mock_app_service.config = dict(self.mock_app_service.config, api_keys=["AQ.gemini-1"])
        self.tab._load_config()
        self.tab.provider_combo.setCurrentIndex(self.tab.provider_combo.findData("claude_cli"))
        self.tab.api_keys_edit.setPlainText("sk-ant-own")

        self.tab._save_config_to_service()

        saved_cfg = self.mock_app_service.save_app_config.call_args[0][0]
        self.assertEqual(saved_cfg["api_keys"], ["AQ.gemini-1"])
        self.assertEqual(saved_cfg["claude_cli_api_key"], "sk-ant-own")
        self.assertEqual(saved_cfg["codex_cli_api_key"], "")

    def test_load_config_restores_cli_key(self):
        """저장된 CLI 전용 키는 해당 프로바이더 화면에서 다시 보인다"""
        self.mock_app_service.config = {
            "llm_provider": "codex_cli", "api_keys": ["AQ.gemini-1"], "codex_cli_api_key": "sk-openai-own",
        }
        self.tab._load_config()
        self.assertEqual(self.tab.api_keys_edit.toPlainText(), "sk-openai-own")
        self.assertEqual(self.tab._build_provider_config_from_ui()["codex_cli_api_key"], "sk-openai-own")

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
        # Antigravity CLI는 키를 쓰지 않으므로 키 입력칸을 숨긴다
        self.assertFalse(self.tab.api_form.isRowVisible(self.tab.api_keys_edit))
        self.tab.provider_combo.setCurrentIndex(self.tab.provider_combo.findData("gemini"))
        self.assertTrue(self.tab.api_form.isRowVisible(self.tab.api_keys_edit))
        self.tab.provider_combo.setCurrentIndex(self.tab.provider_combo.findData("antigravity_cli"))
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

    def test_switch_to_ollama(self):
        """Ollama 전환 시 서버 주소·컨텍스트 길이 표시, PageFold 비활성화, 기본 주소 채움"""
        idx = self.tab.provider_combo.findData("ollama")
        self.assertNotEqual(idx, -1)
        self.tab.provider_combo.setCurrentIndex(idx)

        self.assertFalse(self.tab.api_form.isRowVisible(self.tab.cli_path_edit))
        self.assertTrue(self.tab.api_form.isRowVisible(self.tab.base_url_edit))
        self.assertTrue(self.tab.api_form.isRowVisible(self.tab.ollama_num_ctx_spin))
        self.assertIn("선택 사항", self.tab.api_keys_edit.placeholderText())
        self.assertFalse(self.tab.enable_pagefold_check.isEnabled())
        # OpenAI 호환 URL이 남아 있으면 Ollama 기본 주소로 바뀐다
        self.assertEqual(self.tab.base_url_edit.text(), "http://localhost:11434")

        items = [self.tab.model_name_combo.itemText(i) for i in range(self.tab.model_name_combo.count())]
        self.assertIn("gemma3:12b", items)

        # 다시 OpenAI 호환으로 돌아가면 저장된 URL 복원
        self.tab.provider_combo.setCurrentIndex(self.tab.provider_combo.findData("openai_compatible"))
        self.assertEqual(self.tab.base_url_edit.text(), "https://api.openai.com/v1/chat/completions")
        self.assertFalse(self.tab.api_form.isRowVisible(self.tab.ollama_num_ctx_spin))

    def test_save_config_with_ollama(self):
        idx = self.tab.provider_combo.findData("ollama")
        self.tab.provider_combo.setCurrentIndex(idx)
        self.tab.base_url_edit.setText("http://gpu-box:11434")
        self.tab.model_name_combo.setCurrentText("exaone3.5:32b")
        self.tab.ollama_num_ctx_spin.setValue(32768)

        self.tab._save_config_to_service()

        saved_cfg = self.mock_app_service.save_app_config.call_args[0][0]
        self.assertEqual(saved_cfg.get("llm_provider"), "ollama")
        self.assertEqual(saved_cfg.get("ollama_base_url"), "http://gpu-box:11434")
        self.assertEqual(saved_cfg.get("ollama_model"), "exaone3.5:32b")
        self.assertEqual(saved_cfg.get("ollama_num_ctx"), 32768)
        # OpenAI 호환 설정은 건드리지 않는다
        self.assertEqual(saved_cfg.get("openai_compatible_base_url"), "https://api.openai.com/v1/chat/completions")

    def test_load_config_with_ollama(self):
        self.mock_app_service.config = {
            "llm_provider": "ollama",
            "ollama_base_url": "http://gpu-box:11434",
            "ollama_model": "my-finetune:latest",
            "ollama_num_ctx": 8192,
            "openai_compatible_base_url": "https://api.deepseek.com/v1/chat/completions",
        }
        self.tab._load_config()

        self.assertEqual(self.tab.provider_combo.currentData(), "ollama")
        self.assertEqual(self.tab.base_url_edit.text(), "http://gpu-box:11434")
        self.assertEqual(self.tab.model_name_combo.currentText(), "my-finetune:latest")
        self.assertEqual(self.tab.ollama_num_ctx_spin.value(), 8192)

    def test_ui_config_for_ollama_health_check(self):
        self.tab.provider_combo.setCurrentIndex(self.tab.provider_combo.findData("ollama"))
        self.tab.model_name_combo.setCurrentText("qwen3:14b")
        cfg = self.tab._build_provider_config_from_ui()
        self.assertEqual(cfg["llm_provider"], "ollama")
        self.assertEqual(cfg["ollama_model"], "qwen3:14b")
        self.assertEqual(cfg["ollama_base_url"], "http://localhost:11434")
        self.assertEqual(cfg["ollama_api_key"], "")


if __name__ == "__main__":
    unittest.main()
