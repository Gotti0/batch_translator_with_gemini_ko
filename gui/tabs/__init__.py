"""
GUI 탭 모듈

각 탭 컴포넌트들을 포함합니다.
"""

from gui.tabs.base_tab import BaseTab
from gui.tabs.settings_tab import SettingsTab
from gui.tabs.glossary_tab import GlossaryTab
from gui.tabs.log_tab import LogTab

__all__ = ['BaseTab', 'SettingsTab', 'GlossaryTab', 'LogTab']
