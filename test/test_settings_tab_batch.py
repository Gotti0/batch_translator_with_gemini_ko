"""
설정 탭의 배치 번역 모드 UI 테스트 (모드 카드 활성화 조건, 배치 작업 패널 버튼·폴링 상태).
"""

import sys
import time
import unittest
from unittest.mock import MagicMock

from PySide6 import QtWidgets

from app.batch_translation_service import BatchSummary
from gui_qt.tabs_qt.settings_tab_qt import SettingsTabQt


def _summary(active: bool, remaining, collected_result=False):
    job = {
        "name": "batches/1", "display_name": "btg-novel-r1-1", "chunks": [0, 1, 2],
        "state": "JOB_STATE_RUNNING" if active else "JOB_STATE_SUCCEEDED",
        "submitted_at": time.time() - 3900, "collected": not active,
    }
    if collected_result:
        job.update({"succeeded": 2, "blocked": 1, "errored": 0})
    return BatchSummary(total_chunks=3, translated=3 - len(remaining), remaining=list(remaining), jobs=[job], round=1,
                        blocked=len(remaining))


class TestSettingsTabBatch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

    def setUp(self):
        self.svc = MagicMock()
        self.svc.config = {"llm_provider": "gemini", "model_name": "gemini-3.8-flash", "batch_poll_interval_seconds": 60}
        self.svc.config_manager.get_default_config.return_value = {}
        self.svc.current_translation_task = None
        self.svc.get_batch_summary.return_value = None
        self.tab = SettingsTabQt(self.svc)
        self.tab.input_edit.setText("/tmp/novel.txt")

    def tearDown(self):
        self.tab._batch_timer.stop()
        self.tab.deleteLater()

    def _select_batch(self):
        self.tab.mode_selector._on_card_clicked("batch")

    def test_batch_card_exists_and_shows_panel(self):
        self.assertIn("batch", self.tab.mode_selector.cards)
        self.assertFalse(self.tab.batch_group.isVisibleTo(self.tab))
        self._select_batch()
        self.assertTrue(self.tab.batch_group.isVisibleTo(self.tab))
        self.assertEqual(self.tab.start_btn.text(), "배치 제출 / 상태 확인")
        self.assertIn("제출한 배치 작업이 없습니다", self.tab.batch_status_label.text())

        self.tab.mode_selector._on_card_clicked("standard")
        self.assertFalse(self.tab.batch_group.isVisibleTo(self.tab))
        self.assertEqual(self.tab.start_btn.text(), "번역 시작")

    def test_batch_card_disabled_for_other_providers_and_vertex(self):
        self._select_batch()
        self.tab.provider_combo.setCurrentIndex(self.tab.provider_combo.findData("ollama"))
        card = self.tab.mode_selector.cards["batch"]
        self.assertFalse(card.isEnabled())
        self.assertIn("Gemini API", card.toolTip())
        self.assertEqual(self.tab.mode_selector.get_current_mode(), "standard")

        self.tab.provider_combo.setCurrentIndex(self.tab.provider_combo.findData("gemini"))
        self.assertTrue(card.isEnabled())
        self.tab.use_vertex_check.setChecked(True)
        self.assertFalse(card.isEnabled())
        self.assertIn("Vertex", card.toolTip())

    def test_active_batch_enables_polling(self):
        self.svc.get_batch_summary.return_value = _summary(active=True, remaining=[0, 1, 2])
        self._select_batch()

        self.assertTrue(self.tab.batch_refresh_btn.isEnabled())
        self.assertTrue(self.tab.batch_cancel_btn.isEnabled())
        self.assertFalse(self.tab.batch_realtime_btn.isEnabled())
        self.assertTrue(self.tab._batch_timer.isActive())
        self.assertEqual(self.tab._batch_timer.interval(), 60_000)
        self.assertEqual(self.tab.batch_jobs_table.rowCount(), 1)
        self.assertEqual(self.tab.batch_jobs_table.item(0, 1).text(), "1–3 (3)")
        self.assertEqual(self.tab.batch_jobs_table.item(0, 3).text(), "1시간 5분")
        self.assertIn("배치 진행 중", self.tab.batch_status_label.text())

    def test_collected_with_remaining_enables_finish_options(self):
        self.svc.get_batch_summary.return_value = _summary(active=False, remaining=[1], collected_result=True)
        self._select_batch()

        self.assertFalse(self.tab.batch_refresh_btn.isEnabled())
        for btn in (self.tab.batch_realtime_btn, self.tab.batch_resubmit_btn, self.tab.batch_keep_btn):
            self.assertTrue(btn.isEnabled())
        self.assertFalse(self.tab._batch_timer.isActive())
        self.assertEqual(self.tab.batch_jobs_table.item(0, 4).text(), "성공 2 · 검열 1 · 오류 0")
        self.assertIn("미완료 1개", self.tab.batch_status_label.text())
        self.assertEqual(self.tab.progress_bar.value(), 66)

    def test_complete_batch_disables_all_actions(self):
        self.svc.get_batch_summary.return_value = _summary(active=False, remaining=[], collected_result=True)
        self._select_batch()
        for btn in (self.tab.batch_refresh_btn, self.tab.batch_cancel_btn, self.tab.batch_realtime_btn,
                    self.tab.batch_resubmit_btn, self.tab.batch_keep_btn):
            self.assertFalse(btn.isEnabled())
        self.assertIn("배치 번역 완료", self.tab.batch_status_label.text())

    def test_mode_saved_to_config(self):
        self._select_batch()
        self.tab._save_config_to_service()
        saved = self.svc.save_app_config.call_args[0][0]
        self.assertEqual(saved["translation_mode"], "batch")


if __name__ == "__main__":
    unittest.main()


class TestBatchKeyField(TestSettingsTabBatch):
    def test_batch_key_saved_and_loaded(self):
        self.assertEqual(self.tab.batch_key_edit.echoMode(), QtWidgets.QLineEdit.Password)
        self.tab.batch_key_edit.setText(" paid-key ")
        self.tab._save_config_to_service()
        self.assertEqual(self.svc.save_app_config.call_args[0][0]["batch_api_key"], "paid-key")

        self.svc.config = {"llm_provider": "gemini", "batch_api_key": "loaded-key"}
        self.tab._load_config()
        self.assertEqual(self.tab.batch_key_edit.text(), "loaded-key")


class TestMemorySection(TestSettingsTabBatch):
    def test_memory_settings_saved_loaded_and_toggle(self):
        self.assertFalse(self.tab.enable_memory_check.isChecked())
        self.assertFalse(self.tab.voyage_key_edit.isEnabled())
        self.assertEqual(self.tab.voyage_key_edit.echoMode(), QtWidgets.QLineEdit.Password)

        self.tab.enable_memory_check.setChecked(True)
        self.assertTrue(self.tab.voyage_key_edit.isEnabled())
        self.tab.voyage_key_edit.setText(" pa-key ")
        self.tab.voyage_model_combo.setCurrentText("voyage-4")
        self.tab.memory_top_k_spin.setValue(2)
        self.tab._save_config_to_service()
        saved = self.svc.save_app_config.call_args[0][0]
        self.assertEqual(
            (saved["enable_translation_memory"], saved["voyage_api_key"], saved["voyage_model"], saved["memory_top_k"]),
            (True, "pa-key", "voyage-4", 2),
        )

        self.svc.config = {"llm_provider": "gemini", "enable_translation_memory": True, "voyage_api_key": "k2",
                           "voyage_model": "voyage-4-large", "memory_top_k": 5, "memory_min_similarity": 0.7}
        self.tab._load_config()
        self.assertTrue(self.tab.enable_memory_check.isChecked())
        self.assertEqual(self.tab.voyage_key_edit.text(), "k2")
        self.assertEqual(self.tab.voyage_model_combo.currentText(), "voyage-4-large")
        self.assertEqual(self.tab.memory_top_k_spin.value(), 5)
        self.assertAlmostEqual(self.tab.memory_min_sim_spin.value(), 0.7)


class TestMemoryGardenUI(TestSettingsTabBatch):
    def test_depth_and_extraction_saved_loaded(self):
        self.tab.enable_memory_check.setChecked(True)
        self.assertFalse(self.tab.memory_extract_model_edit.isEnabled())
        self.tab.memory_extract_check.setChecked(True)
        self.assertTrue(self.tab.memory_extract_model_edit.isEnabled())
        self.tab.memory_extract_model_edit.setText("gemini-lite")
        self.tab.memory_depth_combo.setCurrentIndex(self.tab.memory_depth_combo.findData("deep"))
        self.tab._save_config_to_service()
        saved = self.svc.save_app_config.call_args[0][0]
        self.assertEqual((saved["memory_depth"], saved["enable_memory_extraction"], saved["memory_extraction_model"]),
                         ("deep", True, "gemini-lite"))

        self.svc.config = {"llm_provider": "gemini", "enable_translation_memory": True, "memory_depth": "fast"}
        self.tab._load_config()
        self.assertEqual(self.tab.memory_depth_combo.currentData(), "fast")
        self.assertFalse(self.tab.memory_extract_check.isChecked())

    def test_memory_garden_dialog_renders(self):
        from gui_qt.tabs_qt.settings_tab_qt import MemoryGardenDialog
        overview = {
            "summary": {"canon": 2, "entity": 1, "episode": 10, "edges": 5, "cold": 1},
            "entities": [{"name": "リリア", "aliases": ["リリちゃん"], "translated": "릴리아", "note": "반말",
                          "category": "character", "last_chunk": 4, "fire_count": 3, "cold": False,
                          "evidence": [{"chunk": 0, "excerpt": "「行くよ」とリリアが言った。"}]}],
            "merge_log": [{"absorbed": "リリちゃん", "into": "リリア"}],
        }
        dlg = MemoryGardenDialog(overview, self.tab)
        self.assertEqual(dlg.table.rowCount(), 1)
        self.assertEqual(dlg.table.item(0, 0).text(), "リリア (リリちゃん)")
        self.assertEqual(dlg.table.item(0, 3).text(), "청크 5")
        dlg.deleteLater()
