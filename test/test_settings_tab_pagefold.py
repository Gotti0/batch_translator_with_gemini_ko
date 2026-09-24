"""
PySide6 Settings Tab PageFold 토큰 최적화 연동 유닛 테스트
"""

import sys
import unittest
from unittest.mock import MagicMock
from PySide6 import QtWidgets

from gui_qt.tabs_qt.settings_tab_qt import SettingsTabQt


class TestSettingsTabPageFold(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance()
        if cls.app is None:
            cls.app = QtWidgets.QApplication(sys.argv)

    def setUp(self):
        self.mock_app_service = MagicMock()
        self.mock_app_service.config = {
            "enable_pagefold": True,
            "pagefold_mode": "reference",
            "pagefold_font_size": 1.5,
        }
        self.mock_app_service.config_manager = MagicMock()
        self.mock_app_service.config_manager.get_default_config.return_value = {
            "enable_pagefold": True,
            "pagefold_mode": "reference",
            "pagefold_font_size": 1.0,
        }
        self.tab = SettingsTabQt(self.mock_app_service)

    def tearDown(self):
        self.tab.deleteLater()

    def test_pagefold_ui_elements_exist(self):
        """PageFold UI 위젯 존재 및 초기값 검증"""
        self.assertTrue(hasattr(self.tab, "enable_pagefold_check"))
        self.assertTrue(hasattr(self.tab, "pagefold_mode_combo"))
        self.assertTrue(hasattr(self.tab, "pagefold_font_size_spin"))
        self.assertTrue(self.tab.enable_pagefold_check.isChecked())
        self.assertEqual(self.tab.pagefold_mode_combo.currentData(), "reference")
        self.assertAlmostEqual(self.tab.pagefold_font_size_spin.value(), 1.5)

    def test_pagefold_toggle(self):
        """체크박스 토글 시 세부 입력 위젯 활성/비활성 검증"""
        self.tab.enable_pagefold_check.setChecked(False)
        self.assertFalse(self.tab.pagefold_mode_combo.isEnabled())
        self.assertFalse(self.tab.pagefold_font_size_spin.isEnabled())

        self.tab.enable_pagefold_check.setChecked(True)
        self.assertTrue(self.tab.pagefold_mode_combo.isEnabled())
        self.assertTrue(self.tab.pagefold_font_size_spin.isEnabled())

    def test_save_config_pagefold(self):
        """설정 저장 시 PageFold 항목 직렬화 검증"""
        self.tab.enable_pagefold_check.setChecked(True)
        idx = self.tab.pagefold_mode_combo.findData("marked")
        self.tab.pagefold_mode_combo.setCurrentIndex(idx)
        self.tab.pagefold_font_size_spin.setValue(2.0)

        self.tab._save_config_to_service()
        self.mock_app_service.save_app_config.assert_called()
        saved_cfg = self.mock_app_service.save_app_config.call_args[0][0]
        self.assertTrue(saved_cfg["enable_pagefold"])
        self.assertEqual(saved_cfg["pagefold_mode"], "marked")
        self.assertEqual(saved_cfg["pagefold_font_size"], 2.0)


if __name__ == "__main__":
    unittest.main()
