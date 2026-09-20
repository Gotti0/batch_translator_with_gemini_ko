"""KeyPool 단위 테스트: 등록 순서가 가장 앞선 키 선택, 제외, 쿨다운."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from infrastructure.key_pool import KeyPool


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def make(keys=("a", "b", "c"), cooldown=100.0):
    clock = FakeClock()
    return KeyPool(keys, clock=clock, cooldown_seconds=cooldown), clock


def test_same_key_is_reused_until_it_cools_down():
    """소진되지 않은 키는 계속 쓴다. 요청마다 키가 도는 것은 의도가 아니다."""
    pool, _ = make()
    assert [pool.acquire() for _ in range(5)] == ["a"] * 5


def test_next_key_only_after_cooldown():
    """키가 바뀌는 유일한 계기는 쿨다운이다."""
    pool, _ = make()
    assert pool.acquire() == "a"
    pool.mark_exhausted("a")
    assert pool.acquire() == "b"
    pool.mark_exhausted("b")
    assert pool.acquire() == "c"


def test_recovered_key_regains_priority():
    """쿨다운이 풀린 키는 앞자리를 되찾아, 뒤쪽 키의 하루 한도를 더 헐지 않는다."""
    pool, clock = make(cooldown=100.0)
    pool.mark_exhausted("a", cooldown_seconds=60.0)
    assert pool.acquire() == "b"

    clock.now = 60.0
    assert pool.acquire() == "a"


def test_exclude_skips_keys():
    pool, _ = make()
    assert pool.acquire(exclude={"a", "b"}) == "c"
    assert pool.acquire(exclude={"a", "b", "c"}) is None


def test_cooling_down_key_is_skipped_until_cooldown_ends():
    pool, clock = make(cooldown=100.0)
    pool.mark_exhausted("a")
    assert pool.is_cooling_down("a")
    assert pool.acquire() == "b"

    clock.now = 99.9
    assert pool.is_cooling_down("a")
    clock.now = 100.0
    assert not pool.is_cooling_down("a")
    assert pool.acquire() == "a"


def test_model_scoped_cooldown_keeps_key_for_other_models():
    """하루 한도는 (키, 모델) 단위라 한 모델이 막혀도 같은 키로 다른 모델은 쓴다."""
    pool, _ = make()
    pool.mark_exhausted("a", cooldown_seconds=3600.0, model="flash")
    assert pool.acquire(model="flash") == "b"
    assert pool.acquire(model="pro") == "a"


def test_custom_cooldown_duration():
    pool, clock = make()
    pool.mark_exhausted("a", cooldown_seconds=5.0)
    clock.now = 5.0
    assert not pool.is_cooling_down("a")


def test_all_cooling_down_returns_none_and_reports_earliest_release():
    pool, clock = make()
    pool.mark_exhausted("a", cooldown_seconds=30.0)
    pool.mark_exhausted("b", cooldown_seconds=10.0)
    pool.mark_exhausted("c", cooldown_seconds=20.0)
    assert pool.acquire() is None
    assert pool.next_available_at() == 10.0
    clock.now = 50.0
    assert pool.next_available_at() is None


def test_duplicate_keys_are_collapsed():
    pool, _ = make(keys=("a", "a", "b"))
    assert pool.keys == ["a", "b"]


def test_empty_pool():
    pool, _ = make(keys=())
    assert pool.acquire() is None
    assert pool.next_available_at() is None
