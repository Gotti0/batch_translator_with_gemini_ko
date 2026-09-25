"""
설정 저장 경로 단일화 테스트.

예전에는 설정 탭이 저장할 때 서비스 설정 복사본에 자기 값만 덮어써서, 아직 저장되지 않은
용어집 탭 값(동적 용어집 주입 체크 등)을 옛 값으로 되돌렸다.
"""

import copy
from unittest.mock import MagicMock, patch

import pytest
from PySide6 import QtWidgets

from core.exceptions import BtgConfigException
from gui_qt.config_coordinator import ConfigCoordinator
from gui_qt.tabs_qt.glossary_tab_qt import GlossaryTabQt
from gui_qt.tabs_qt.settings_tab_qt import SettingsTabQt


@pytest.fixture
def service():
    svc = MagicMock()
    svc.config = {
        "llm_provider": "gemini",
        "api_keys": ["AQ.key"],
        "enable_dynamic_glossary_injection": False,
        "max_glossary_entries_per_chunk_injection": 3,
        "temperature": 0.7,
    }
    svc.config_manager.get_default_config.return_value = {}
    svc.saved = []

    def save_app_config(cfg):
        # 실제 AppService처럼 저장 후 파일에서 다시 읽은 결과를 config로 둔다
        svc.saved.append(copy.deepcopy(cfg))
        svc.config = copy.deepcopy(cfg)
        return True

    svc.save_app_config.side_effect = save_app_config
    svc.load_app_config.side_effect = lambda: svc.config
    return svc


@pytest.fixture
def tabs(qtbot, service):
    settings = SettingsTabQt(service)
    glossary = GlossaryTabQt(service)
    qtbot.addWidget(settings)
    qtbot.addWidget(glossary)
    coordinator = ConfigCoordinator(service)
    for tab in (settings, glossary):
        coordinator.register(tab)
        tab.set_config_coordinator(coordinator)
    return settings, glossary, coordinator


def test_glossary_change_survives_settings_save(tabs, service):
    """용어집 탭에서 체크만 하고 번역을 시작해도(설정 탭 저장) 체크가 저장된다"""
    settings, glossary, _ = tabs
    glossary.enable_injection_check.setChecked(True)
    glossary.max_entries_spin.setValue(7)

    settings._save_config_to_service()

    assert service.saved[-1]["enable_dynamic_glossary_injection"] is True
    assert service.saved[-1]["max_glossary_entries_per_chunk_injection"] == 7


def test_settings_change_survives_glossary_save(tabs, service):
    """용어집 탭의 부수 저장(추출·편집 등)도 설정 탭의 미저장 값을 함께 저장한다"""
    settings, glossary, _ = tabs
    settings.temperature_slider.setValue(35)

    glossary._save_config()

    assert service.saved[-1]["temperature"] == pytest.approx(0.35)


def test_reload_refreshes_every_tab(tabs, service):
    settings, glossary, coordinator = tabs
    service.config = dict(service.config, enable_dynamic_glossary_injection=True, temperature=0.2)

    coordinator.reload()

    assert glossary.enable_injection_check.isChecked()
    assert settings.temperature_slider.value() == 20


def test_save_reports_failure(service):
    service.save_app_config.side_effect = lambda cfg: False
    coordinator = ConfigCoordinator(service)
    with pytest.raises(BtgConfigException):
        coordinator.save()
    assert coordinator.save_quietly("테스트") is False


def test_main_window_owns_save_buttons(qtbot, service):
    """저장·불러오기 버튼은 메인 창에 있고, 두 탭이 같은 조정자를 쓴다"""
    from gui_qt import main_window_qt

    service.current_translation_task = None
    with patch.object(main_window_qt, "AppService", return_value=service), \
         patch.object(QtWidgets.QSystemTrayIcon, "isSystemTrayAvailable", return_value=False):
        window = main_window_qt.BatchTranslatorWindow()
    qtbot.addWidget(window)

    assert not hasattr(window.settings_tab, "save_config_btn")
    assert window.settings_tab._config_coordinator is window.config_coordinator
    assert window.glossary_tab._config_coordinator is window.config_coordinator

    # 번역 시작/취소는 설정 탭 밖(탭 영역 아래)에 있어 다른 탭에서도 보인다
    action_bar = window.settings_tab.action_bar
    assert not window.settings_tab.isAncestorOf(action_bar)
    assert window.centralWidget().isAncestorOf(window.settings_tab.start_btn)
    assert window.centralWidget().isAncestorOf(window.settings_tab.cancel_btn)

    window.glossary_tab.enable_injection_check.setChecked(True)
    window.save_config_btn.click()
    assert service.saved[-1]["enable_dynamic_glossary_injection"] is True
