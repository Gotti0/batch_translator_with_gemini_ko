import pytest
from PySide6 import QtWidgets, QtCore
from unittest.mock import MagicMock
from gui_qt.tabs_qt.settings_tab_qt import SettingsTabQt
from gui_qt.components_qt.mode_card import ModeSelectorGroup

@pytest.fixture
def mock_app_service():
    service = MagicMock()
    service.config = {
        "api_keys": [],
        "use_vertex_ai": False,
        "model_name": "gemini-2.0-flash",
        "translation_mode": "standard",
        "temperature": 0.7,
        "top_p": 0.9,
        "chunk_size": 6000,
        "max_workers": 4,
        "requests_per_minute": 60.0,
        "novel_language": "auto",
        "novel_language_fallback": "ja",
        "prompts": "Translate this: {{slot}}",
        "enable_prefill_translation": False,
        "prefill_system_instruction": "",
        "prefill_cached_history": [],
        "use_content_safety_retry": True,
        "max_content_safety_split_attempts": 3,
        "min_content_safety_chunk_size": 100,
        "input_files": [],
        "output_file": None
    }
    service.current_translation_task = None
    return service

def test_settings_tab_instantiation(qtbot, mock_app_service):
    """SettingsTabQt 인스턴스화 및 기본 부모 설정 검증 (Signal source deleted 방지)"""
    tab = SettingsTabQt(mock_app_service)
    qtbot.addWidget(tab)
    
    # mode_selector가 올바르게 부모를 가지고 있는지 확인
    assert tab.mode_selector is not None
    assert tab.mode_selector.parent() is not None
    # layout에 포함되어 있는지 간접 확인 (C++ 객체가 살아있는지 확인)
    assert tab.mode_selector.isVisible() is False # 아직 창이 안 떴으므로 False일 수 있지만 객체 접근은 가능해야 함
    
    # 시그널 연결 확인
    # RuntimeError: Signal source has been deleted 가 발생하지 않아야 함
    try:
        tab.mode_selector.mode_changed.emit("integrity")
    except RuntimeError as e:
        pytest.fail(f"Signal emission failed: {e}")

def test_mode_selector_group_parenting(qtbot):
    """ModeSelectorGroup과 ModeCard 간의 부모 설정 검증"""
    parent = QtWidgets.QWidget()
    qtbot.addWidget(parent)
    
    selector = ModeSelectorGroup(parent)
    assert selector.parent() == parent
    
    # 각 카드가 selector를 부모로 가지는지 확인
    for card in selector.cards.values():
        assert card.parent() == selector

def test_mode_change_signal_propagation(qtbot, mock_app_service):
    """모드 변경 시그널이 SettingsTabQt에 올바르게 전달되는지 확인"""
    tab = SettingsTabQt(mock_app_service)
    qtbot.addWidget(tab)
    
    # 초기 모드 확인
    assert mock_app_service.config["translation_mode"] == "standard"
    
    # integrity 모드로 변경 시뮬레이션
    with qtbot.waitSignal(tab.mode_selector.mode_changed, timeout=1000):
        tab.mode_selector.cards["integrity"].clicked.emit("integrity")
    
    # AppService 설정에 반영되었는지 확인
    assert mock_app_service.config["translation_mode"] == "integrity"

def test_settings_tab_ui_sync_on_mode_change(qtbot, mock_app_service):
    """모드 변경에 따른 UI 비활성화/활성화 상태 동기화 검증"""
    tab = SettingsTabQt(mock_app_service)
    qtbot.addWidget(tab)
    
    # standard 모드에서는 청크 크기 활성화
    tab.mode_selector.mode_changed.emit("standard")
    assert tab.chunk_size_spin.isEnabled() is True
    
    # epub 모드에서는 청크 크기 비활성화 (EPUB 모드 특화 로직)
    tab.mode_selector.mode_changed.emit("epub")
    assert tab.chunk_size_spin.isEnabled() is False
