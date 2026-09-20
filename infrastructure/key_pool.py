# key_pool.py
"""
API 키 선택과 쿨다운을 맡는 키 풀.

키는 쓸 수 있는 동안 계속 쓰고, 쿨다운에 들어갔을 때에만 다음 키로 넘어간다. 그래서 선택 규칙은
"쿨다운이 아니면서 등록 순서가 가장 앞선 키"다. 소진되지 않은 키를 요청마다 번갈아 쓰면 키가
고르게 조금씩 깎여 어느 키가 유난히 빨리 죽는지 알 수 없고, 모델 전체가 과부하일 때는 아무 소득
없이 키마다 하루 한도를 1회씩 뜯긴다(503도 하루 한도를 쓴다).

앞쪽 키부터 차례로 소진되므로 뒤쪽 키는 손대지 않은 채 남고, 로그에서 키가 바뀐 지점 사이의
요청 수가 곧 그 키의 수명이 된다. 재시도는 호출자가 같은 키를 그대로 쓰므로 여기서 다루지 않는다.
"""
import time
from typing import Callable, Dict, Iterable, List, Optional, Tuple


class KeyPool:
    """키별 쿨다운 만료 시각만 안다.

    쿨다운은 키 전체에 걸거나 특정 모델에만 걸 수 있다. 무료 티어 한도는 (키, 모델) 단위라
    한 모델의 하루 한도를 다 써도 같은 키로 다른 모델은 쓸 수 있다.
    """

    DEFAULT_COOLDOWN_SECONDS = 100.0

    def __init__(
        self,
        keys: Iterable[str],
        *,
        clock: Callable[[], float] = time.monotonic,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
    ):
        self._keys: List[str] = list(dict.fromkeys(keys))
        self._clock = clock
        self._cooldown_seconds = cooldown_seconds
        self._cooldown_until: Dict[Tuple[str, Optional[str]], float] = {}

    @property
    def keys(self) -> List[str]:
        return list(self._keys)

    def acquire(self, exclude: Iterable[str] = (), model: Optional[str] = None) -> Optional[str]:
        """쿨다운이 아니고 exclude에 없는 키 중 등록 순서가 가장 앞선 키.

        쿨다운이 풀린 키는 다시 앞자리를 되찾는다. 분당 한도로 잠깐 비켜섰던 키가 풀리면
        그 키로 돌아가, 뒤쪽 키의 하루 한도를 필요 이상으로 헐지 않는다.
        """
        excluded = set(exclude)
        for key in self._keys:
            if key not in excluded and not self.is_cooling_down(key, model):
                return key
        return None

    def mark_exhausted(self, key: str, cooldown_seconds: Optional[float] = None, model: Optional[str] = None) -> None:
        """키를 쿨다운에 넣는다. model을 주면 그 모델에 대해서만 쿨다운한다."""
        duration = self._cooldown_seconds if cooldown_seconds is None else cooldown_seconds
        until = self._clock() + duration
        slot = (key, model)
        self._cooldown_until[slot] = max(until, self._cooldown_until.get(slot, float("-inf")))

    def is_cooling_down(self, key: str, model: Optional[str] = None) -> bool:
        """키 전체 쿨다운 또는 (model을 주면) 그 모델의 쿨다운 중인지."""
        now = self._clock()
        slots = [(key, None)] + ([(key, model)] if model is not None else [])
        return any(self._cooldown_until.get(slot, float("-inf")) > now for slot in slots)

    def next_available_at(self) -> Optional[float]:
        """쿨다운 중인 키 가운데 가장 먼저 풀리는 시각. 쿨다운 중인 키가 없으면 None."""
        now = self._clock()
        pending = [until for until in self._cooldown_until.values() if until > now]
        return min(pending) if pending else None
