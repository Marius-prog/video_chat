"""Service rules exercised against in-memory fake repositories: no database, no SQLAlchemy.

The fakes also make the two concurrency paths testable that a real database cannot
reproduce on demand: a uid collision when joining, and a room created by someone else
between the lookup and the insert.
"""
import itertools
from typing import Optional

import pytest

from videochat.data.models import AuthSession, Room, RoomMember, User
from videochat.data.repositories import DuplicateRecord
from videochat.security import passwords
from videochat.services.accounts import AccountService
from videochat.services.errors import InvalidCredentials
from videochat.services.rooms import RoomService

from conftest import GOOD_PASSWORD


class FakeAccountRepository:
    def __init__(self):
        self.users: dict[int, User] = {}
        self.sessions: dict[int, AuthSession] = {}
        self._ids = itertools.count(1)

    def add_user(self, *, username, email, password_hash, created_at):
        if any(u.username == username or u.email == email for u in self.users.values()):
            raise DuplicateRecord()
        user = User(id=next(self._ids), username=username, email=email, password_hash=password_hash,
                    is_active=True, created_at=created_at, last_login_at=None)
        self.users[user.id] = user
        return user

    def get_user(self, user_id):
        return self.users.get(user_id)

    def find_user_by_username(self, username):
        return next((u for u in self.users.values() if u.username == username), None)

    def find_user_by_email(self, email):
        return next((u for u in self.users.values() if u.email == email), None)

    def record_login(self, user_id, *, token_hash, now, expires_at, ip, user_agent, password_hash=None):
        user = self.users[user_id]
        user.last_login_at = now
        if password_hash is not None:
            user.password_hash = password_hash
        record = AuthSession(id=next(self._ids), token_hash=token_hash, user_id=user_id, created_at=now,
                             expires_at=expires_at, last_seen_at=now, revoked_at=None, ip=ip, user_agent=user_agent)
        self.sessions[record.id] = record
        return record

    def find_session_by_token_hash(self, token_hash):
        return next(((r, self.users[r.user_id]) for r in self.sessions.values() if r.token_hash == token_hash), None)

    def find_session(self, session_id, user_id):
        record = self.sessions.get(session_id)
        return (record, self.users[user_id]) if record and record.user_id == user_id else None

    def touch_session(self, session_id, now):
        self.sessions[session_id].last_seen_at = now

    def revoke_session(self, token_hash, now):
        for record in self.sessions.values():
            if record.token_hash == token_hash and record.revoked_at is None:
                record.revoked_at = now

    def revoke_user_sessions(self, user_id, now, *, keep_session_id=None):
        for record in self.sessions.values():
            if record.user_id == user_id and record.revoked_at is None and record.id != keep_session_id:
                record.revoked_at = now

    def replace_password(self, user_id, password_hash, now, *, keep_session_id=None):
        self.users[user_id].password_hash = password_hash
        self.revoke_user_sessions(user_id, now, keep_session_id=keep_session_id)

    def delete_dead_sessions(self, cutoff):
        dead = [i for i, r in self.sessions.items() if r.expires_at < cutoff or (r.revoked_at is not None and r.revoked_at < cutoff)]
        for i in dead:
            del self.sessions[i]
        return len(dead)


class FakeRoomRepository:
    """Only what ``join`` needs, plus knobs to fail the first N inserts the way a busy database would."""

    def __init__(self, *, uid_collisions: int = 0, room_races: int = 0):
        self.rooms: dict[str, Room] = {}
        self.members: list[RoomMember] = []
        self.uid_collisions = uid_collisions
        self.room_races = room_races
        self._ids = itertools.count(1)

    def find_room(self, name) -> Optional[Room]:
        return self.rooms.get(name)

    def add_room(self, *, name, owner_id, created_at):
        if self.room_races:  # another worker inserted the same name a moment ago
            self.room_races -= 1
            self.rooms[name] = Room(id=next(self._ids), name=name, owner_id=owner_id + 1000, created_at=created_at, is_active=True)
            raise DuplicateRecord()
        if name in self.rooms:
            raise DuplicateRecord()
        room = Room(id=next(self._ids), name=name, owner_id=owner_id, created_at=created_at, is_active=True)
        self.rooms[name] = room
        return room

    def add_member(self, *, room_id, user_id, uid, display_name, joined_at):
        if self.uid_collisions:
            self.uid_collisions -= 1
            raise DuplicateRecord()
        member = RoomMember(id=next(self._ids), room_id=room_id, user_id=user_id, uid=uid, display_name=display_name, joined_at=joined_at, left_at=None)
        self.members.append(member)
        return member


@pytest.fixture
def fake_accounts():
    return FakeAccountRepository()


@pytest.fixture
def accounts(fake_accounts, settings, limiter, clock):
    return AccountService(fake_accounts, settings, limiter, clock=clock)


class TestAccountRulesWithoutADatabase:
    def test_login_records_a_session_and_upgrades_a_weak_hash(self, accounts, fake_accounts, clock):
        accounts.register("alice", "alice@example.com", GOOD_PASSWORD)
        user = fake_accounts.find_user_by_username("alice")
        user.password_hash = passwords.hash_password(GOOD_PASSWORD, n=512)
        result = accounts.login("Alice@Example.com", GOOD_PASSWORD, ip="1.1.1.1", user_agent="ua")
        record = fake_accounts.sessions[result.principal.session_id]
        assert (record.user_id, record.ip, record.user_agent, record.last_seen_at) == (user.id, "1.1.1.1", "ua", int(clock()))
        assert result.token not in record.token_hash and user.password_hash.split("$")[1] == "1024"
        assert user.last_login_at == int(clock())

    def test_unknown_user_still_costs_one_hash_verification(self, accounts, monkeypatch):
        calls = []
        monkeypatch.setattr(passwords, "verify_password", lambda pw, stored: calls.append(stored) or False)
        with pytest.raises(InvalidCredentials):
            accounts.login("nobody", "whatever whatever")
        assert calls == [passwords.dummy_hash(1024)]

    def test_activity_touches_a_session_at_most_once_a_minute(self, accounts, fake_accounts, clock):
        accounts.register("alice", "alice@example.com", GOOD_PASSWORD)
        token = accounts.login("alice", GOOD_PASSWORD).token
        started = int(clock())
        clock.advance(30)
        assert accounts.resolve(token) is not None
        assert next(iter(fake_accounts.sessions.values())).last_seen_at == started  # too soon to write
        clock.advance(31)
        assert accounts.resolve(token) is not None
        assert next(iter(fake_accounts.sessions.values())).last_seen_at == started + 61

    def test_change_password_keeps_only_the_current_session(self, accounts, fake_accounts):
        accounts.register("alice", "alice@example.com", GOOD_PASSWORD)
        mine = accounts.login("alice", GOOD_PASSWORD)
        other = accounts.login("alice", GOOD_PASSWORD)
        accounts.change_password(mine.principal.user.id, GOOD_PASSWORD, "a brand new passphrase", keep_session_id=mine.principal.session_id)
        assert fake_accounts.sessions[mine.principal.session_id].revoked_at is None
        assert fake_accounts.sessions[other.principal.session_id].revoked_at is not None
        assert accounts.resolve(mine.token) is not None and accounts.resolve(other.token) is None


class TestJoinConcurrencyPaths:
    def test_a_uid_collision_makes_join_draw_another_uid(self, settings, limiter, clock):
        repo = FakeRoomRepository(uid_collisions=2)
        rooms = RoomService(repo, settings, limiter, clock=clock)
        grant = rooms.join(1, 1, "lobby", "Alice")
        assert repo.uid_collisions == 0 and len(repo.members) == 1 and repo.members[0].uid == grant.uid

    def test_eight_collisions_in_a_row_is_an_error_not_a_loop(self, settings, limiter, clock):
        rooms = RoomService(FakeRoomRepository(uid_collisions=8), settings, limiter, clock=clock)
        with pytest.raises(RuntimeError):
            rooms.join(1, 1, "lobby", "Alice")

    def test_a_room_created_concurrently_is_joined_not_duplicated(self, settings, limiter, clock):
        repo = FakeRoomRepository(room_races=1)
        rooms = RoomService(repo, settings, limiter, clock=clock)
        grant = rooms.join(1, 1, "lobby", "Alice")
        assert not grant.created_room and repo.rooms["LOBBY"].owner_id == 1001  # the other worker's room won
        assert repo.members[0].room_id == repo.rooms["LOBBY"].id
