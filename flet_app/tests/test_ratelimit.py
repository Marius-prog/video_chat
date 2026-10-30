import pytest

from videochat.security.ratelimit import RateLimiter, RateLimitExceeded


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_allows_up_to_limit_then_blocks():
    lim = RateLimiter(clock=FakeClock())
    assert [lim.hit("k", 3, 60).allowed for _ in range(4)] == [True, True, True, False]


def test_window_slides():
    clock = FakeClock()
    lim = RateLimiter(clock=clock)
    for _ in range(3):
        assert lim.hit("k", 3, 60).allowed
    clock.t += 59
    assert not lim.hit("k", 3, 60).allowed
    clock.t += 2  # first three hits are now older than 60s
    assert lim.hit("k", 3, 60).allowed


def test_retry_after_reports_the_wait():
    clock = FakeClock()
    lim = RateLimiter(clock=clock)
    lim.hit("k", 1, 60)
    clock.t += 20
    decision = lim.hit("k", 1, 60)
    assert not decision.allowed and 39 < decision.retry_after <= 40


def test_blocked_hits_do_not_extend_the_block():
    clock = FakeClock()
    lim = RateLimiter(clock=clock)
    lim.hit("k", 1, 60)
    for _ in range(50):
        lim.hit("k", 1, 60)
    clock.t += 61
    assert lim.hit("k", 1, 60).allowed


def test_keys_are_independent_and_reset_works():
    lim = RateLimiter(clock=FakeClock())
    assert lim.hit("a", 1, 60).allowed and not lim.hit("a", 1, 60).allowed
    assert lim.hit("b", 1, 60).allowed
    lim.reset("a")
    assert lim.hit("a", 1, 60).allowed


def test_enforce_raises_with_integer_retry_after():
    lim = RateLimiter(clock=FakeClock())
    lim.enforce("k", 1, 60)
    with pytest.raises(RateLimitExceeded) as info:
        lim.enforce("k", 1, 60)
    assert isinstance(info.value.retry_after, int) and info.value.retry_after >= 1


def test_memory_is_bounded_when_keys_are_rotated():
    lim = RateLimiter(clock=FakeClock(), max_keys=100)
    for i in range(1000):
        lim.hit(f"attacker-{i}", 5, 60)
    assert len(lim._events) <= 100
