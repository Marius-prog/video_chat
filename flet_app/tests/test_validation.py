import pytest

from videochat.security import validation as v
from videochat.security.validation import ValidationError


@pytest.mark.parametrize("raw,expected", [("Alice", "alice"), ("  bob_99 ", "bob_99"), ("ＡＢＣ", "abc")])
def test_username_is_normalised(raw, expected):
    assert v.validate_username(raw) == expected


@pytest.mark.parametrize(
    "raw", [None, 5, "", "  ", "ab", "a" * 31, "has space", "dash-ed", "semi;colon", "<script>", "ünï", "x" * 600]
)
def test_username_rejects(raw):
    with pytest.raises(ValidationError):
        v.validate_username(raw)


def test_email_is_lowercased_and_validated():
    assert v.validate_email("  Foo.Bar+tag@Example.COM ") == "foo.bar+tag@example.com"
    for bad in ["", "nope", "a@b", "a@@b.com", "a b@c.com", "a..b@c.com", "@c.com", "a@-c.com", "a" * 65 + "@c.com", None]:
        with pytest.raises(ValidationError):
            v.validate_email(bad)


class TestPassword:
    def test_accepts_a_good_passphrase_unchanged(self):
        pw = "  correct horse battery  "
        assert v.validate_password(pw) == pw  # never trimmed

    @pytest.mark.parametrize(
        "pw",
        ["short", "a" * 11, "x" * 129, " " * 20, "123456789012", "aaaaaaaaaaaaaaaa", "ababababababab", "qwertyuiop12", "Password1234"],
    )
    def test_rejects_weak(self, pw):
        with pytest.raises(ValidationError):
            v.validate_password(pw)

    def test_rejects_username_and_email_parts(self):
        with pytest.raises(ValidationError):
            v.validate_password("my-alice-secret-phrase", username="alice")
        with pytest.raises(ValidationError):
            v.validate_password("xx-jdoe-xx-yy-zz-123", email="jdoe@example.com")

    def test_short_username_is_not_used_as_a_needle(self):
        v.validate_password("a perfectly fine passphrase", username="a")

    def test_non_string(self):
        with pytest.raises(ValidationError):
            v.validate_password(123456789012)


class TestRoomName:
    def test_uppercases_and_trims(self):
        assert v.validate_room_name(" team-1_x ") == "TEAM-1_X"

    def test_length_limits(self):
        assert v.validate_room_name("a" * 64) == "A" * 64
        with pytest.raises(ValidationError):
            v.validate_room_name("a" * 65)

    @pytest.mark.parametrize("raw", ["", " ", "has space", "a/b", "a;b", "../etc", "<b>", "é", None, 7, "ß"])
    def test_rejects(self, raw):
        with pytest.raises(ValidationError):
            v.validate_room_name(raw)


class TestDisplayName:
    @pytest.mark.parametrize("raw,expected", [("Alice", "Alice"), ("  Jo   Ann ", "Jo Ann"), ("Zoë", "Zoë"), ("李雷", "李雷"), ("O'Neil-Smith", "O'Neil-Smith")])
    def test_accepts(self, raw, expected):
        assert v.validate_display_name(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        ["a", "x" * 51, "<script>alert(1)</script>", "a​b", "ab‮", "bad\x00name", "bad\nname", "--", "..", "a&b", None, 3],
    )
    def test_rejects(self, raw):
        with pytest.raises(ValidationError):
            v.validate_display_name(raw)


class TestUid:
    def test_accepts_ints_and_digit_strings(self):
        assert v.validate_uid(1) == 1
        assert v.validate_uid("42") == 42
        assert v.validate_uid(2**32 - 1) == 2**32 - 1

    @pytest.mark.parametrize("raw", [0, -1, 2**32, "abc", "", None, True, "1.5", [1]])
    def test_rejects(self, raw):
        with pytest.raises(ValidationError):
            v.validate_uid(raw)


def test_validation_error_carries_field():
    with pytest.raises(ValidationError) as info:
        v.validate_username("x")
    assert info.value.field == "username"
