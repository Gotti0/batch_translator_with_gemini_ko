"""
용어집 편집기의 "저장 후 닫기"가 번역이 실제로 읽는 용어집 파일에 쓰는지 확인한다.

번역은 입력 파일 옆의 <입력파일명>_simple_glossary.json을 설정 경로보다 먼저 쓴다. 편집 결과를
화면과 config.json에만 반영하던 때는 번역에 반영되지 않고 재시작하면 사라졌다.
"""
import copy
import json
from unittest.mock import MagicMock, patch

import pytest
from PySide6 import QtWidgets

from gui_qt.config_coordinator import ConfigCoordinator
from gui_qt.tabs_qt import glossary_tab_qt
from gui_qt.tabs_qt.glossary_tab_qt import GlossaryTabQt

EDITED = [{"keyword": "小王", "translated_keyword": "샤오왕", "target_language": "ko", "occurrence_count": 12}]


@pytest.fixture
def make_tab(qtbot):
    def build(config):
        svc = MagicMock()
        svc.config = config
        svc.config_manager.get_default_config.return_value = {}
        svc.saved = []

        def save_app_config(cfg):
            svc.saved.append(copy.deepcopy(cfg))
            svc.config = copy.deepcopy(cfg)
            return True

        svc.save_app_config.side_effect = save_app_config
        svc.load_app_config.side_effect = lambda: svc.config
        tab = GlossaryTabQt(svc)
        qtbot.addWidget(tab)
        coordinator = ConfigCoordinator(svc)
        coordinator.register(tab)
        tab.set_config_coordinator(coordinator)
        return tab, svc
    return build


def _edit_and_save(tab):
    edited_json = json.dumps(EDITED, ensure_ascii=False, indent=2)
    with patch.object(glossary_tab_qt.GlossaryEditorDialogQt, "edit", return_value=edited_json):
        tab._open_glossary_editor()


def test_save_writes_glossary_next_to_input_file(make_tab, tmp_path):
    """입력 파일 옆 용어집이 없으면 새로 만들고, 탭의 용어집 경로도 그 파일로 맞춘다."""
    input_file = tmp_path / "novel.txt"
    input_file.write_text("本文", encoding="utf-8")
    tab, svc = make_tab({"input_files": [str(input_file)]})

    _edit_and_save(tab)

    target = tmp_path / "novel_simple_glossary.json"
    assert json.loads(target.read_text(encoding="utf-8")) == EDITED
    assert tab.glossary_path_edit.text() == str(target)
    assert svc.saved[-1]["glossary_json_path"] == str(target)


def test_save_overwrites_existing_glossary_next_to_input_file(make_tab, tmp_path):
    """설정 경로가 다른 파일을 가리켜도, 번역이 먼저 쓰는 입력 파일 옆 용어집에 쓴다."""
    input_file = tmp_path / "novel.txt"
    input_file.write_text("本文", encoding="utf-8")
    target = tmp_path / "novel_simple_glossary.json"
    target.write_text("[]", encoding="utf-8")
    other = tmp_path / "other.json"
    other.write_text("[]", encoding="utf-8")
    tab, _ = make_tab({"input_files": [str(input_file)], "glossary_json_path": str(other)})

    _edit_and_save(tab)

    assert json.loads(target.read_text(encoding="utf-8")) == EDITED
    assert json.loads(other.read_text(encoding="utf-8")) == []


def test_save_without_input_file_uses_configured_path(make_tab, tmp_path):
    """입력 파일이 없으면 설정된 용어집 경로에 쓴다."""
    configured = tmp_path / "manual.json"
    configured.write_text("[]", encoding="utf-8")
    tab, _ = make_tab({"input_files": [], "glossary_json_path": str(configured)})

    _edit_and_save(tab)

    assert json.loads(configured.read_text(encoding="utf-8")) == EDITED


def test_save_without_any_path_asks_where_to_save(make_tab, tmp_path):
    chosen = tmp_path / "chosen.json"
    tab, _ = make_tab({"input_files": []})

    with patch.object(QtWidgets.QFileDialog, "getSaveFileName", return_value=(str(chosen), "")):
        _edit_and_save(tab)

    assert json.loads(chosen.read_text(encoding="utf-8")) == EDITED
    assert tab.glossary_path_edit.text() == str(chosen)
