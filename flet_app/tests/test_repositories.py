"""The repositories against a real (in-memory SQLite) database: the one layer that is allowed to speak SQL."""
import pytest
from sqlalchemy import select

from videochat.data.models import AuthSession, Room, RoomMember
from videochat.data.repositories import AccountRepository, DuplicateRecord, RoomRepository


@pytest.fixture
def accounts_repo(db):
    return AccountRepository(db)


@pytest.fixture
def rooms_repo(db):
    return RoomRepository(db)


def add_user(repo, name="alice", email=None):
    return repo.add_user(username=name, email=email or f"{name}@example.com", password_hash="scrypt$x", created_at=100)


class TestAccountRepository:
    def test_add_and_find_users(self, accounts_repo):
        user = add_user(accounts_repo)
        assert user.id and accounts_repo.get_user(user.id).email == "alice@example.com"
        assert accounts_repo.find_user_by_username("alice").id == user.id
        assert accounts_repo.find_user_by_email("alice@example.com").id == user.id
        assert accounts_repo.find_user_by_username("alice@example.com") is None
        assert accounts_repo.get_user(999) is None

    @pytest.mark.parametrize("name,email", [("alice", "other@example.com"), ("other", "alice@example.com")])
    def test_duplicate_username_or_email_is_reported_not_raised_as_sql(self, accounts_repo, name, email):
        add_user(accounts_repo)
        with pytest.raises(DuplicateRecord):
            add_user(accounts_repo, name, email)

    def test_record_login_stamps_the_user_and_optionally_replaces_the_hash(self, accounts_repo, db):
        user = add_user(accounts_repo)
        first = accounts_repo.record_login(user.id, token_hash="h1", now=200, expires_at=900, ip="1.1.1.1", user_agent="ua")
        second = accounts_repo.record_login(user.id, token_hash="h2", now=300, expires_at=900, ip="", user_agent="", password_hash="scrypt$new")
        assert first.id != second.id and first.last_seen_at == 200
        stored = accounts_repo.get_user(user.id)
        assert (stored.last_login_at, stored.password_hash) == (300, "scrypt$new")
        with db.session() as s:
            assert len(s.scalars(select(AuthSession)).all()) == 2

    def test_find_sessions_by_token_hash_and_by_id_return_the_user_too(self, accounts_repo):
        user = add_user(accounts_repo)
        record = accounts_repo.record_login(user.id, token_hash="h1", now=200, expires_at=900, ip="", user_agent="")
        found, who = accounts_repo.find_session_by_token_hash("h1")
        assert (found.id, who.username) == (record.id, "alice")
        assert accounts_repo.find_session(record.id, user.id)[0].id == record.id
        assert accounts_repo.find_session(record.id, user.id + 1) is None  # another user's id: not theirs
        assert accounts_repo.find_session_by_token_hash("nope") is None

    def test_touch_revoke_and_replace_password(self, accounts_repo):
        user = add_user(accounts_repo)
        keep = accounts_repo.record_login(user.id, token_hash="k", now=200, expires_at=900, ip="", user_agent="")
        other = accounts_repo.record_login(user.id, token_hash="o", now=200, expires_at=900, ip="", user_agent="")
        accounts_repo.touch_session(keep.id, 250)
        assert accounts_repo.find_session(keep.id, user.id)[0].last_seen_at == 250

        accounts_repo.replace_password(user.id, "scrypt$changed", 300, keep_session_id=keep.id)
        assert accounts_repo.get_user(user.id).password_hash == "scrypt$changed"
        assert accounts_repo.find_session(keep.id, user.id)[0].revoked_at is None
        assert accounts_repo.find_session(other.id, user.id)[0].revoked_at == 300

        accounts_repo.revoke_session("k", 400)
        accounts_repo.revoke_session("k", 500)  # already revoked: the first timestamp stays
        assert accounts_repo.find_session(keep.id, user.id)[0].revoked_at == 400

    def test_delete_dead_sessions_counts_only_long_dead_ones(self, accounts_repo):
        user = add_user(accounts_repo)
        accounts_repo.record_login(user.id, token_hash="expired", now=100, expires_at=150, ip="", user_agent="")
        accounts_repo.record_login(user.id, token_hash="revoked", now=100, expires_at=900, ip="", user_agent="")
        accounts_repo.record_login(user.id, token_hash="live", now=100, expires_at=900, ip="", user_agent="")
        accounts_repo.revoke_session("revoked", 160)
        assert accounts_repo.delete_dead_sessions(cutoff=200) == 2
        assert accounts_repo.find_session_by_token_hash("live") is not None


class TestRoomRepository:
    def test_rooms_are_unique_by_name(self, rooms_repo, accounts_repo):
        owner = add_user(accounts_repo)
        room = rooms_repo.add_room(name="LOBBY", owner_id=owner.id, created_at=100)
        assert rooms_repo.find_room("LOBBY").id == room.id and rooms_repo.find_room("OTHER") is None
        with pytest.raises(DuplicateRecord):
            rooms_repo.add_room(name="LOBBY", owner_id=owner.id, created_at=101)

    def test_uids_are_unique_per_room_only(self, rooms_repo, accounts_repo):
        owner = add_user(accounts_repo)
        a = rooms_repo.add_room(name="A", owner_id=owner.id, created_at=100)
        b = rooms_repo.add_room(name="B", owner_id=owner.id, created_at=100)
        rooms_repo.add_member(room_id=a.id, user_id=owner.id, uid=7, display_name="Alice", joined_at=100)
        rooms_repo.add_member(room_id=b.id, user_id=owner.id, uid=7, display_name="Alice", joined_at=100)
        with pytest.raises(DuplicateRecord):
            rooms_repo.add_member(room_id=a.id, user_id=owner.id, uid=7, display_name="Again", joined_at=101)

    def test_membership_lookups_are_scoped_to_room_uid_and_user(self, rooms_repo, accounts_repo):
        alice, bob = add_user(accounts_repo), add_user(accounts_repo, "bob")
        room = rooms_repo.add_room(name="A", owner_id=alice.id, created_at=100)
        rooms_repo.add_member(room_id=room.id, user_id=alice.id, uid=7, display_name="The Boss", joined_at=100)
        member, found_room, user = rooms_repo.find_membership("A", 7, alice.id)
        assert (member.uid, found_room.name, user.username) == (7, "A", "alice")
        assert rooms_repo.find_membership("A", 7, bob.id) is None
        assert rooms_repo.find_membership("B", 7, alice.id) is None
        assert rooms_repo.find_member_name("A", 7) == ("The Boss", "alice")
        assert rooms_repo.find_member_name("A", 8) is None

    def test_mark_left_is_scoped_and_mark_present_undoes_it(self, rooms_repo, accounts_repo):
        alice, bob = add_user(accounts_repo), add_user(accounts_repo, "bob")
        room = rooms_repo.add_room(name="A", owner_id=alice.id, created_at=100)
        member = rooms_repo.add_member(room_id=room.id, user_id=alice.id, uid=7, display_name="Alice", joined_at=100)
        rooms_repo.mark_left("A", 7, bob.id, 200)  # not his uid
        assert rooms_repo.find_membership("A", 7, alice.id)[0].left_at is None
        rooms_repo.mark_left("A", 7, alice.id, 200)
        rooms_repo.mark_left("A", 7, alice.id, 300)  # already gone: the first timestamp stays
        assert rooms_repo.find_membership("A", 7, alice.id)[0].left_at == 200
        rooms_repo.mark_present(member.id)
        assert rooms_repo.find_membership("A", 7, alice.id)[0].left_at is None

    def test_recent_and_owned_rooms(self, rooms_repo, accounts_repo):
        alice, bob = add_user(accounts_repo), add_user(accounts_repo, "bob")
        mine = rooms_repo.add_room(name="MINE", owner_id=alice.id, created_at=100)
        bobs = rooms_repo.add_room(name="BOBS", owner_id=bob.id, created_at=110)
        rooms_repo.add_member(room_id=mine.id, user_id=alice.id, uid=1, display_name="A", joined_at=100)
        rooms_repo.add_member(room_id=bobs.id, user_id=alice.id, uid=2, display_name="A", joined_at=120)
        rooms_repo.add_member(room_id=mine.id, user_id=alice.id, uid=3, display_name="A", joined_at=130)
        assert rooms_repo.recent_rooms(alice.id, limit=5) == [("MINE", alice.id, 130), ("BOBS", bob.id, 120)]
        assert rooms_repo.recent_rooms(alice.id, limit=1) == [("MINE", alice.id, 130)]
        assert rooms_repo.owned_rooms(alice.id) == [("MINE", 100)]

    def test_delete_members(self, rooms_repo, accounts_repo, db):
        alice = add_user(accounts_repo)
        room = rooms_repo.add_room(name="A", owner_id=alice.id, created_at=100)
        rooms_repo.add_member(room_id=room.id, user_id=alice.id, uid=1, display_name="left long ago", joined_at=100)
        rooms_repo.add_member(room_id=room.id, user_id=alice.id, uid=2, display_name="abandoned", joined_at=100)
        rooms_repo.add_member(room_id=room.id, user_id=alice.id, uid=3, display_name="live", joined_at=500)
        rooms_repo.mark_left("A", 1, alice.id, 150)
        assert rooms_repo.delete_members(left_before=200, abandoned_before=400) == 2
        with db.session() as s:
            assert [m.uid for m in s.scalars(select(RoomMember))] == [3]
            assert s.scalar(select(Room)) is not None
