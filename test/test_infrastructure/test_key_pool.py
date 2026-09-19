"""KeyPool 단위 테스트: 가장 오래 쉰 키 선택, 제외, 쿨다운."""
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


def use(pool, **kw):
    key = pool.acquire(**kw)
    if key is not None:
        pool.mark_used(key)
    return key


def test_unused_keys_first_in_registration_order():
    pool, _ = make()
    assert [use(pool) for _ in range(3)] == ["a", "b", "c"]


def test_least_recently_used_key_comes_next():
    pool, _ = make()
    for _ in range(3):
        use(pool)
    pool.mark_used("a")  # a를 다시 씀 → 가장 오래 쉰 키는 b
    assert use(pool) == "b"
    assert use(pool) == "c"
    assert use(pool) == "a"


def test_acquire_without_mark_used_does_not_change_order():
    pool, _ = make()
    assert pool.acquire() == "a"
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
