"""
설정 저장·불러오기를 한 경로로 모으는 조정자.

각 탭은 자기 위젯 값을 설정 딕셔너리에 써 넣는 `apply_to_config(cfg)`와,
서비스 설정으로 위젯을 다시 채우는 `load_config_into_ui()`만 맡는다. 파일 저장은 여기서만 한다.

예전에는 설정 탭과 용어집 탭이 각자 저장했다. 설정 탭은 서비스 설정의 복사본에 자기 값만 덮어
저장했기 때문에, 아직 저장되지 않은 용어집 탭 값(예: 동적 용어집 주입 체크)을 옛 값으로 되돌렸다.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Protocol

from core.exceptions import BtgConfigException
from infrastructure.logger_config import setup_logger

logger = setup_logger(__name__)


class ConfigSection(Protocol):
    def apply_to_config(self, cfg: Dict[str, Any]) -> None: ...

    def load_config_into_ui(self) -> None: ...


class ConfigCoordinator:
    def __init__(self, app_service: Any, sections: List[ConfigSection] | None = None) -> None:
        self.app_service = app_service
        self._sections: List[ConfigSection] = list(sections or [])

    def register(self, section: ConfigSection) -> None:
        if section not in self._sections:
            self._sections.append(section)

    def collect(self) -> Dict[str, Any]:
        """서비스 설정 복사본에 모든 탭의 현재 위젯 값을 모은다."""
        cfg = copy.deepcopy(getattr(self.app_service, "config", {}) or {})
        for section in self._sections:
            section.apply_to_config(cfg)
        return cfg

    def save(self) -> None:
        """모든 탭의 값을 모아 한 번에 저장한다. 실패하면 BtgConfigException."""
        cfg = self.collect()
        if self.app_service.save_app_config(cfg) is False:
            raise BtgConfigException("config.json 저장에 실패했습니다.")

    def save_quietly(self, reason: str) -> bool:
        """작업 흐름을 막지 않아야 하는 저장(번역 시작, 용어집 동작 등). 실패는 로그만 남긴다."""
        try:
            self.save()
            return True
        except Exception as e:
            logger.warning(f"설정 저장 실패 ({reason}): {e}")
            return False

    def reload(self) -> None:
        """config.json을 다시 읽고 모든 탭 화면을 채운다."""
        self.app_service.load_app_config()
        for section in self._sections:
            section.load_config_into_ui()
