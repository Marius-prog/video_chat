import pytest

from videochat.security import passwords


def test_roundtrip_and_format():
    stored = passwords.hash_password("hunter2 hunter2", n=1024)
    scheme, n, r, p, salt, digest = stored.split("$")
    assert (scheme, n, r, p) == ("scrypt", "1024", "8", "1")
    assert passwords.verify_password("hunter2 hunter2", stored)
    assert not passwords.verify_password("hunter2 hunter3", stored)


def test_salts_differ():
    assert passwords.hash_password("same", n=1024) != passwords.hash_password("same", n=1024)


def test_unicode_is_normalised():
    stored = passwords.hash_password("ｐａｓｓ１２３", n=1024)  # full-width
    assert passwords.verify_password("pass123", stored)


@pytest.mark.parametrize(
    "garbage",
    ["", "x", "scrypt$", "scrypt$a$b$c$d$e", "md5$1$2$3$AA$BB", "scrypt$1024$8$1$!!$!!", "a$b$c$d$e$f$g", None, 12],
)
def test_malformed_hashes_never_verify_and_never_raise(garbage):
    assert passwords.verify_password("whatever", garbage) is False


def test_stored_parameters_are_bounded():
    # a corrupted row must not be able to request gigabytes of memory
    assert passwords.verify_password("x", "scrypt$1073741824$8$1$AAAA$AAAA") is False
    assert passwords.verify_password("x", "scrypt$1000$8$1$AAAA$AAAA") is False  # not a power of two
    assert passwords.verify_password("x", "scrypt$1024$9999$1$AAAA$AAAA") is False


def test_needs_rehash():
    weak = passwords.hash_password("pw", n=1024)
    assert passwords.needs_rehash(weak, n=2048)
    assert not passwords.needs_rehash(weak, n=1024)
    assert passwords.needs_rehash("garbage")


def test_default_cost_is_memory_hard():
    # 2**15 * 8 * 128 bytes = 32 MiB per hash
    assert passwords.SCRYPT_N >= 2**15 and passwords.SCRYPT_R >= 8


def test_dummy_hash_is_valid_and_cached():
    assert passwords.dummy_hash(1024) is passwords.dummy_hash(1024)
    assert passwords.verify_password("not-a-real-password", passwords.dummy_hash(1024))
