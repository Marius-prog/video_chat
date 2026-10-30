import pytest
from sqlalchemy import select

from videochat.data.models import Room, RoomMember
from videochat.security import tickets
from videochat.security.validation import ValidationError
from videochat.services.errors import Forbidden, NotFound, RateLimited


@pytest.fixture
def verify(settings, clock):
    # the services run on a fake clock that is not wall-clock time, so verify tickets against the same one
    return lambda grant: tickets.verify_ticket(settings.secret_key, grant.ticket, now=clock())


def test_first_join_creates_the_room_and_makes_the_joiner_owner(rooms, alice, db):
    grant = rooms.join(alice.user.id, alice.session_id, "team-sync", "Alice A")
    assert grant.room == "TEAM-SYNC" and grant.created_room
    with db.session() as s:
        room = s.scalar(select(Room))
        assert room.name == "TEAM-SYNC" and room.owner_id == alice.user.id


def test_second_user_joins_the_existing_room_without_taking_ownership(rooms, alice, bob, db):
    rooms.join(alice.user.id, alice.session_id, "lobby", "Alice")
    grant = rooms.join(bob.user.id, bob.session_id, "LOBBY", "Bob")
    assert not grant.created_room
    with db.session() as s:
        assert s.scalar(select(Room)).owner_id == alice.user.id
        assert len(s.scalars(select(RoomMember)).all()) == 2


def test_ticket_binds_user_session_room_and_uid(rooms, alice, settings, verify):
    grant = rooms.join(alice.user.id, alice.session_id, "lobby", "Alice")
    t = verify(grant)
    assert (t.user_id, t.session_id, t.room, t.name, t.uid) == (alice.user.id, alice.session_id, "LOBBY", "Alice", grant.uid)
    assert 1 <= grant.uid <= 2**32 - 1
    assert t.expires_at == grant.expires_at


def test_every_join_gets_its_own_uid(rooms, alice):
    uids = {rooms.join(alice.user.id, alice.session_id, "lobby", "Alice").uid for _ in range(12)}
    assert len(uids) == 12


@pytest.mark.parametrize("room,name", [("bad room", "Alice"), ("", "Alice"), ("lobby", "<b>"), ("lobby", "x")])
def test_join_validates_input(rooms, alice, room, name):
    with pytest.raises(ValidationError):
        rooms.join(alice.user.id, alice.session_id, room, name)


def test_join_is_rate_limited_per_user(rooms, alice):
    for _ in range(15):
        rooms.join(alice.user.id, alice.session_id, "lobby", "Alice")
    with pytest.raises(RateLimited):
        rooms.join(alice.user.id, alice.session_id, "lobby", "Alice")


def test_closed_rooms_cannot_be_joined(rooms, alice, bob, db):
    rooms.join(alice.user.id, alice.session_id, "lobby", "Alice")
    with db.session() as s:
        s.scalar(select(Room)).is_active = False
    with pytest.raises(Forbidden):
        rooms.join(bob.user.id, bob.session_id, "lobby", "Bob")


class TestAuthorize:
    def test_a_fresh_ticket_authorises_and_returns_the_verified_username(self, rooms, alice, settings, verify):
        grant = rooms.join(alice.user.id, alice.session_id, "lobby", "Not My Real Name")
        member = rooms.authorize(verify(grant))
        assert (member.name, member.username, member.uid) == ("Not My Real Name", "alice", grant.uid)

    def test_missing_membership_is_rejected(self, rooms, alice, settings, db, verify):
        grant = rooms.join(alice.user.id, alice.session_id, "lobby", "Alice")
        with db.session() as s:
            for m in s.scalars(select(RoomMember)):
                s.delete(m)
        with pytest.raises(NotFound):
            rooms.authorize(verify(grant))

    def test_a_ticket_cannot_be_used_by_another_user_id(self, rooms, alice, bob, settings, verify):
        grant = rooms.join(alice.user.id, alice.session_id, "lobby", "Alice")
        t = verify(grant)
        forged = tickets.RoomTicket(**{**t.__dict__, "user_id": bob.user.id})
        with pytest.raises(NotFound):
            rooms.authorize(forged)

    def test_authorize_marks_the_member_present_again(self, rooms, alice, settings, db, verify):
        grant = rooms.join(alice.user.id, alice.session_id, "lobby", "Alice")
        rooms.leave("LOBBY", grant.uid, alice.user.id)
        with db.session() as s:
            assert s.scalar(select(RoomMember)).left_at is not None
        rooms.authorize(verify(grant))
        with db.session() as s:
            assert s.scalar(select(RoomMember)).left_at is None


def test_leave_is_scoped_to_the_owner_of_the_uid(rooms, alice, bob, db):
    grant = rooms.join(alice.user.id, alice.session_id, "lobby", "Alice")
    rooms.leave("LOBBY", grant.uid, bob.user.id)  # not his: ignored
    with db.session() as s:
        assert s.scalar(select(RoomMember)).left_at is None
    rooms.leave("LOBBY", grant.uid, alice.user.id)
    rooms.leave("LOBBY", grant.uid, alice.user.id)  # idempotent
    with db.session() as s:
        assert s.scalar(select(RoomMember)).left_at is not None


def test_member_lookup_is_scoped_to_the_room(rooms, alice, bob):
    a = rooms.join(alice.user.id, alice.session_id, "room-a", "Alice")
    assert rooms.member("ROOM-A", a.uid).username == "alice"
    assert rooms.member("ROOM-B", a.uid) is None  # same uid, other room: not disclosed
    assert rooms.member("ROOM-A", a.uid + 1) is None


def test_recent_rooms_most_recent_first_with_ownership_flag(rooms, alice, bob, clock):
    rooms.join(alice.user.id, alice.session_id, "mine", "Alice")
    clock.advance(10)
    rooms.join(bob.user.id, bob.session_id, "bobs", "Bob")
    clock.advance(10)
    rooms.join(alice.user.id, alice.session_id, "bobs", "Alice")
    result = rooms.recent_rooms(alice.user.id)
    assert [(r.name, r.is_owner) for r in result] == [("BOBS", False), ("MINE", True)]
    assert rooms.recent_rooms(alice.user.id, limit=1)[0].name == "BOBS"
    assert [r.name for r in rooms.owned_rooms(alice.user.id)] == ["MINE"]


def test_purge_stale_members(rooms, alice, clock, settings):
    done = rooms.join(alice.user.id, alice.session_id, "lobby", "Alice")
    abandoned = rooms.join(alice.user.id, alice.session_id, "lobby", "Alice")
    rooms.leave("LOBBY", done.uid, alice.user.id)
    clock.advance(31 * 24 * 3600)
    live = rooms.join(alice.user.id, alice.session_id, "lobby", "Alice")
    assert rooms.purge_stale_members() == 2  # the one that left long ago + the abandoned one
    assert rooms.member("LOBBY", live.uid) is not None
    assert rooms.member("LOBBY", abandoned.uid) is None
