import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from videochat.config import Settings  # noqa: E402
from videochat.container import build_container  # noqa: E402
from videochat.data.db import Database  # noqa: E402
from videochat.security.ratelimit import RateLimiter  # noqa: E402
from videochat.services.accounts import AccountService  # noqa: E402
from videochat.services.rooms import RoomService  # noqa: E402

# Obviously fake credentials: the real Agora project is never used by tests.
FAKE_APP_ID = "0123456789abcdef0123456789abcdef"
FAKE_CERT = "fedcba9876543210fedcba9876543210"
GOOD_PASSWORD = "correct horse battery staple"


class Clock:
    def __init__(self, now: float = 1_800_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_settings(**overrides) -> Settings:
    values = dict(
        secret_key="test-secret-key-" + "x" * 40,
        database_url="sqlite://",
        agora_app_id=FAKE_APP_ID,
        agora_app_certificate=FAKE_CERT,
        allowed_hosts=("testserver", "localhost", "127.0.0.1"),
        scrypt_n=1024,  # fast hashing in tests; production enforces >= 2**14
    )
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def settings():
    return make_settings()


@pytest.fixture
def db():
    database = Database("sqlite://")
    database.create_all()
    yield database
    database.dispose()


@pytest.fixture
def limiter():
    return RateLimiter()


@pytest.fixture
def accounts(db, settings, limiter, clock):
    return AccountService(db, settings, limiter, clock=clock)


@pytest.fixture
def rooms(db, settings, limiter, clock):
    return RoomService(db, settings, limiter, clock=clock)


@pytest.fixture
def container(settings):
    c = build_container(settings, db=Database("sqlite://"))
    yield c
    c.db.dispose()


@pytest.fixture
def alice(accounts):
    accounts.register("alice", "alice@example.com", GOOD_PASSWORD)
    return accounts.login("alice", GOOD_PASSWORD).principal


@pytest.fixture
def bob(accounts):
    accounts.register("bob", "bob@example.com", GOOD_PASSWORD)
    return accounts.login("bob", GOOD_PASSWORD).principal
