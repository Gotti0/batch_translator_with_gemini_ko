"""
프로바이더별 추론 강도(thinking / effort) 옵션 명세.

UI(설정 탭)와 실제 호출(클라이언트)이 같은 표를 보도록 한 곳에 둔다.
값이 None이면 옵션을 넘기지 않고 CLI/서버 기본값에 맡긴다. 기존 사용자의 동작은 그대로다.

허용 값 확인 근거 (2026-09):
- Claude CLI `--effort`: `claude --help` → low, medium, high, xhigh, max
- Codex CLI `model_reasoning_effort`: gpt-5.5 서버 오류 메시지 → none, minimal, low, medium, high, xhigh, max
- Antigravity CLI `--effort`: `agy --help` → low, medium, high, max
- Ollama `think`: `ollama run --help` → true/false, 일부 모델은 high/medium/low
- OpenAI 호환 `reasoning_effort`: 서버마다 다르다. 확인하지 않았으므로 기본값은 넘기지 않음
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class ReasoningSpec:
    """추론 옵션 한 종류.

    kind: "level"(단계 선택) | "budget"(정수 예산, Gemini 2.5) | "none"(지원 안 함)
    config_key: 값을 저장하는 설정 키
    choices: (값, 표시 이름) 목록. level일 때만 쓴다.
    allow_default: 맨 앞에 "기본값 (CLI/서버에 맡김)"(값 None)을 둘지
    default: allow_default가 False일 때 쓸 기본 값
    """

    kind: str
    config_key: Optional[str] = None
    choices: Tuple[Tuple[str, str], ...] = ()
    allow_default: bool = True
    default: Optional[str] = None

    @property
    def values(self) -> Tuple[str, ...]:
        return tuple(value for value, _ in self.choices)

    def normalize(self, value: object) -> Optional[str]:
        """허용 값이면 문자열로, 아니면 기본값(None 또는 default)으로 돌린다."""
        if value is None:
            return None if self.allow_default else self.default
        text = str(value).strip().lower()
        if text in self.values:
            return text
        return None if self.allow_default else self.default


DEFAULT_CHOICE_LABEL = "기본값 (CLI/서버에 맡김)"

_LEVELS = {
    "minimal": "minimal",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "xhigh",
    "max": "max",
    "none": "none (추론 끔)",
}


def _levels(*names: str) -> Tuple[Tuple[str, str], ...]:
    return tuple((name, _LEVELS[name]) for name in names)


NO_REASONING = ReasoningSpec(kind="none")

CLAUDE_CLI = ReasoningSpec(
    kind="level", config_key="claude_cli_effort",
    choices=_levels("low", "medium", "high", "xhigh", "max"),
)
CODEX_CLI = ReasoningSpec(
    kind="level", config_key="codex_cli_effort",
    choices=_levels("none", "minimal", "low", "medium", "high", "xhigh", "max"),
)
ANTIGRAVITY_CLI = ReasoningSpec(
    kind="level", config_key="antigravity_cli_effort",
    choices=_levels("low", "medium", "high", "max"),
)
OLLAMA = ReasoningSpec(
    kind="level", config_key="ollama_think",
    choices=(("false", "끄기"), ("true", "켜기"), ("low", "low"), ("medium", "medium"), ("high", "high")),
)
OPENAI_COMPATIBLE = ReasoningSpec(
    kind="level", config_key="openai_compatible_reasoning_effort",
    choices=_levels("minimal", "low", "medium", "high"),
)
GEMINI_3_FLASH = ReasoningSpec(
    kind="level", config_key="thinking_level",
    choices=_levels("minimal", "low", "medium", "high"), allow_default=False, default="high",
)
GEMINI_3_PRO = ReasoningSpec(
    kind="level", config_key="thinking_level",
    choices=_levels("low", "high"), allow_default=False, default="high",
)
GEMINI_BUDGET = ReasoningSpec(kind="budget", config_key="thinking_budget")


def reasoning_spec_for(provider: Optional[str], model_name: Optional[str] = None) -> ReasoningSpec:
    """프로바이더(와 Gemini는 모델)에 맞는 추론 옵션 명세."""
    provider = (provider or "gemini").strip().lower()
    if provider == "claude_cli":
        return CLAUDE_CLI
    if provider == "codex_cli":
        return CODEX_CLI
    if provider == "antigravity_cli":
        return ANTIGRAVITY_CLI
    if provider == "ollama":
        return OLLAMA
    if provider == "openai_compatible":
        return OPENAI_COMPATIBLE
    if provider == "gemini":
        name = (model_name or "").lower()
        if "gemini-3" in name:
            return GEMINI_3_FLASH if "flash" in name else GEMINI_3_PRO
        # Gemini 3 외에는 기존대로 예산(thinking_budget)을 쓴다 (GeminiClient도 같은 기준)
        return GEMINI_BUDGET
    return NO_REASONING


def ollama_think_payload(value: Optional[str]):
    """Ollama 요청의 think 필드 값. None이면 필드를 넣지 않는다."""
    if value is None:
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    return value
