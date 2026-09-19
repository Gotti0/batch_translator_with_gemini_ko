# key_pool.py
"""
API 키 선택과 쿨다운을 맡는 키 풀.

새 요청마다 쿨다운이 아닌 키 중 가장 오래 쉰 키를 고른다. 재시도는 호출자가 같은 키를 그대로
쓰므로 여기서 다루지 않는다. 사용자가 어느 키를 소진했는지 알 수 있어야 하기 때문이다.
"""
import time
from typing import Callable, Dict, Iterable, List, Optional, Tuple


class KeyPool:
    """키별 마지막 사용 순번과 쿨다운 만료 시각만 안다.

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
        self._use_seq = 0
        self._last_used: Dict[str, int] = {}
        self._cooldown_until: Dict[Tuple[str, Optional[str]], float] = {}

    @property
    def keys(self) -> List[str]:
        return list(self._keys)

    def acquire(self, exclude: Iterable[str] = (), model: Optional[str] = None) -> Optional[str]:
        """쿨다운이 아니고 exclude에 없는 키 중 가장 오래 쉰 키. 한 번도 안 쓴 키가 먼저, 동률이면 등록 순서."""
        excluded = set(exclude)
        candidates = [
            (self._last_used.get(key, -1), index, key)
            for index, key in enumerate(self._keys)
            if key not in excluded and not self.is_cooling_down(key, model)
        ]
        return min(candidates)[2] if candidates else None

    def mark_used(self, key: str) -> None:
        self._use_seq += 1
        self._last_used[key] = self._use_seq

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
