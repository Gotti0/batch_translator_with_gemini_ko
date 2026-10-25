"""
제거된 프로바이더 설정을 불러올 때의 이전 규칙 검증.
"""

import json

from core.config.config_manager import ConfigManager


def test_cursor_cli_is_migrated_to_grok_cli(tmp_path):
    # 그대로 두면 팩토리의 기본 분기로 빠져 사용자가 모르게 Gemini 키로 번역한다
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"llm_provider": "cursor_cli", "cursor_cli_model": "auto"}), encoding="utf-8")

    config = ConfigManager(path).load_config()

    assert config["llm_provider"] == "grok_cli"
    assert config["grok_cli_path"] == "grok"
    assert config["grok_cli_model"] == "default"


def test_other_providers_are_untouched(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"llm_provider": "antigravity_cli"}), encoding="utf-8")

    assert ConfigManager(path).load_config()["llm_provider"] == "antigravity_cli"
