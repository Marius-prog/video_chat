import pytest
from sqlalchemy import select

from videochat.data.models import AuthSession, User
from videochat.security import passwords
from videochat.security.validation import ValidationError
from videochat.services.errors import AccountExists, InvalidCredentials, NotAuthenticated, RateLimited

from conftest import GOOD_PASSWORD


def test_register_creates_a_normalised_user_with_a_hashed_password(accounts, db):
    info = accounts.register("Alice", "Alice@Example.com", GOOD_PASSWORD)
    assert (info.username, info.email) == ("alice", "alice@example.com")
    with db.session() as s:
        user = s.scalar(select(User))
        assert user.password_hash.startswith("scrypt$") and GOOD_PASSWORD not in user.password_hash


@pytest.mark.parametrize("username,email", [("alice", "other@example.com"), ("ALICE", "other@example.com"), ("other", "ALICE@example.com")])
def test_duplicate_username_or_email_is_refused_with_one_generic_message(accounts, username, email):
    accounts.register("alice", "alice@example.com", GOOD_PASSWORD)
    with pytest.raises(AccountExists) as info:
        accounts.register(username, email, GOOD_PASSWORD)
    assert info.value.message == "That username or email is already registered."


def test_register_validates_input(accounts):
    with pytest.raises(ValidationError):
        accounts.register("al", "a@example.com", GOOD_PASSWORD)
    with pytest.raises(ValidationError):
        accounts.register("alice", "not-an-email", GOOD_PASSWORD)
    with pytest.raises(ValidationError):
        accounts.register("alice", "a@example.com", "short")


def test_register_is_rate_limited_per_ip(accounts):
    for i in range(10):
        accounts.register(f"user{i:02d}", f"u{i}@example.com", GOOD_PASSWORD, ip="9.9.9.9")
    with pytest.raises(RateLimited):
        accounts.register("one_more", "more@example.com", GOOD_PASSWORD, ip="9.9.9.9")


class TestLogin:
    def test_by_username_and_by_email(self, accounts):
        accounts.register("alice", "alice@example.com", GOOD_PASSWORD)
        assert accounts.login("ALICE", GOOD_PASSWORD).principal.user.username == "alice"
        assert accounts.login("Alice@Example.com", GOOD_PASSWORD).principal.user.username == "alice"

    def test_wrong_password_and_unknown_user_look_identical(self, accounts):
        accounts.register("alice", "alice@example.com", GOOD_PASSWORD)
        with pytest.raises(InvalidCredentials) as wrong:
            accounts.login("alice", "definitely wrong password")
        with pytest.raises(InvalidCredentials) as unknown:
            accounts.login("nobody", "definitely wrong password")
        assert wrong.value.message == unknown.value.message == "Invalid username or password."

    def test_unknown_user_still_costs_a_hash_verification(self, accounts, monkeypatch):
        calls = []
        real = passwords.verify_password
        monkeypatch.setattr(passwords, "verify_password", lambda pw, stored: calls.append(stored) or real(pw, stored))
        with pytest.raises(InvalidCredentials):
            accounts.login("nobody", "whatever whatever")
        assert len(calls) == 1 and calls[0] == passwords.dummy_hash(1024)

    @pytest.mark.parametrize("bad", [None, "", "x" * 1000, 12345])
    def test_garbage_input_is_just_invalid_credentials(self, accounts, bad):
        accounts.register("alice", "alice@example.com", GOOD_PASSWORD)
        with pytest.raises(InvalidCredentials):
            accounts.login(bad, bad)
        with pytest.raises(InvalidCredentials):
            accounts.login("alice", bad)

    def test_brute_force_is_throttled_then_a_success_resets_the_pair(self, accounts):
        accounts.register("alice", "alice@example.com", GOOD_PASSWORD)
        for _ in range(4):
            with pytest.raises(InvalidCredentials):
                accounts.login("alice", "wrong wrong wrong", ip="1.1.1.1")
        accounts.login("alice", GOOD_PASSWORD, ip="1.1.1.1")  # 5th attempt, correct: allowed and resets
        for _ in range(4):
            with pytest.raises(InvalidCredentials):
                accounts.login("alice", "wrong wrong wrong", ip="1.1.1.1")
        with pytest.raises(InvalidCredentials):
            accounts.login("alice", "wrong wrong wrong", ip="1.1.1.1")  # 5th wrong
        with pytest.raises(RateLimited):
            accounts.login("alice", GOOD_PASSWORD, ip="1.1.1.1")  # 6th: blocked even with the right password

    def test_per_username_limit_applies_across_ips(self, accounts):
        accounts.register("alice", "alice@example.com", GOOD_PASSWORD)
        for i in range(10):
            with pytest.raises(InvalidCredentials):
                accounts.login("alice", "wrong wrong wrong", ip=f"10.0.0.{i}")
        with pytest.raises(RateLimited):
            accounts.login("alice", "wrong wrong wrong", ip="10.0.0.99")

    def test_inactive_user_cannot_log_in(self, accounts, db):
        accounts.register("alice", "alice@example.com", GOOD_PASSWORD)
        with db.session() as s:
            s.scalar(select(User)).is_active = False
        with pytest.raises(InvalidCredentials):
            accounts.login("alice", GOOD_PASSWORD)

    def test_old_weak_hash_is_upgraded_on_login(self, accounts, db):
        accounts.register("alice", "alice@example.com", GOOD_PASSWORD)
        with db.session() as s:
            s.scalar(select(User)).password_hash = passwords.hash_password(GOOD_PASSWORD, n=512)
        accounts.login("alice", GOOD_PASSWORD)
        with db.session() as s:
            assert s.scalar(select(User)).password_hash.split("$")[1] == "1024"

    def test_only_a_hash_of_the_token_is_stored(self, accounts, db):
        accounts.register("alice", "alice@example.com", GOOD_PASSWORD)
        result = accounts.login("alice", GOOD_PASSWORD)
        with db.session() as s:
            record = s.scalar(select(AuthSession))
            assert result.token not in (record.token_hash, record.ip, record.user_agent)
            assert len(record.token_hash) == 64


class TestSessions:
    def test_resolve_logout_and_unknown_tokens(self, accounts):
        accounts.register("alice", "alice@example.com", GOOD_PASSWORD)
        token = accounts.login("alice", GOOD_PASSWORD).token
        assert accounts.resolve(token).user.username == "alice"
        accounts.logout(token)
        assert accounts.resolve(token) is None
        for junk in (None, "", "nope", "x" * 500, 123):
            assert accounts.resolve(junk) is None
        accounts.logout("nope")  # must not raise

    def test_absolute_expiry(self, accounts, clock, settings):
        accounts.register("alice", "alice@example.com", GOOD_PASSWORD)
        token = accounts.login("alice", GOOD_PASSWORD).token
        for _ in range(7):  # stay active so the idle timeout never fires
            clock.advance(settings.session_ttl / 7 - 1)
            assert accounts.resolve(token) is not None
        clock.advance(10)
        assert accounts.resolve(token) is None

    def test_idle_expiry_and_activity_extends_it(self, accounts, clock, settings):
        accounts.register("alice", "alice@example.com", GOOD_PASSWORD)
        token = accounts.login("alice", GOOD_PASSWORD).token
        clock.advance(settings.session_idle_ttl - 100)
        assert accounts.resolve(token) is not None  # touches last_seen
        clock.advance(settings.session_idle_ttl - 100)
        assert accounts.resolve(token) is not None
        clock.advance(settings.session_idle_ttl + 1)
        assert accounts.resolve(token) is None

    def test_logout_all_revokes_every_login(self, accounts):
        accounts.register("alice", "alice@example.com", GOOD_PASSWORD)
        a = accounts.login("alice", GOOD_PASSWORD)
        b = accounts.login("alice", GOOD_PASSWORD)
        accounts.logout_all(a.principal.user.id)
        assert accounts.resolve(a.token) is None and accounts.resolve(b.token) is None

    def test_session_active_follows_revocation(self, accounts):
        accounts.register("alice", "alice@example.com", GOOD_PASSWORD)
        login = accounts.login("alice", GOOD_PASSWORD)
        p = login.principal
        assert accounts.session_active(p.session_id, p.user.id)
        assert not accounts.session_active(p.session_id, p.user.id + 1)  # wrong owner
        accounts.logout(login.token)
        assert not accounts.session_active(p.session_id, p.user.id)

    def test_purge_removes_only_long_dead_sessions(self, accounts, clock, settings):
        accounts.register("alice", "alice@example.com", GOOD_PASSWORD)
        old = accounts.login("alice", GOOD_PASSWORD)
        accounts.logout(old.token)
        clock.advance(3 * 24 * 3600)
        fresh = accounts.login("alice", GOOD_PASSWORD)
        assert accounts.purge_expired() == 1
        assert accounts.resolve(fresh.token) is not None


class TestChangePassword:
    def test_changes_password_and_revokes_other_sessions(self, accounts):
        accounts.register("alice", "alice@example.com", GOOD_PASSWORD)
        mine = accounts.login("alice", GOOD_PASSWORD)
        other = accounts.login("alice", GOOD_PASSWORD)
        accounts.change_password(mine.principal.user.id, GOOD_PASSWORD, "a brand new passphrase", keep_session_id=mine.principal.session_id)
        assert accounts.resolve(mine.token) is not None
        assert accounts.resolve(other.token) is None
        with pytest.raises(InvalidCredentials):
            accounts.login("alice", GOOD_PASSWORD)
        accounts.login("alice", "a brand new passphrase")

    def test_requires_the_current_password_and_a_strong_new_one(self, accounts):
        accounts.register("alice", "alice@example.com", GOOD_PASSWORD)
        uid = accounts.login("alice", GOOD_PASSWORD).principal.user.id
        with pytest.raises(InvalidCredentials):
            accounts.change_password(uid, "wrong wrong wrong", "a brand new passphrase")
        with pytest.raises(ValidationError):
            accounts.change_password(uid, GOOD_PASSWORD, "short")
        with pytest.raises(ValidationError):
            accounts.change_password(uid, GOOD_PASSWORD, "alice-alice-alice")

    def test_is_rate_limited(self, accounts):
        accounts.register("alice", "alice@example.com", GOOD_PASSWORD)
        uid = accounts.login("alice", GOOD_PASSWORD).principal.user.id
        for _ in range(5):
            with pytest.raises(InvalidCredentials):
                accounts.change_password(uid, "wrong wrong wrong", "a brand new passphrase")
        with pytest.raises(RateLimited):
            accounts.change_password(uid, GOOD_PASSWORD, "a brand new passphrase")

    def test_unknown_user(self, accounts):
        with pytest.raises(NotAuthenticated):
            accounts.change_password(999, "x", "y")
