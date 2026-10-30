#!/usr/bin/env python3
"""
Input Validation Function Tests
Tests the validation logic for channels, usernames, and UIDs
"""

import re

# Reproduce validation logic from views.py
MAX_CHANNEL_NAME_LENGTH = 64
MAX_USERNAME_LENGTH = 100
MIN_USERNAME_LENGTH = 2
ALLOWED_CHANNEL_CHARS = re.compile(r'^[a-zA-Z0-9_-]+$')
ALLOWED_USERNAME_CHARS = re.compile(r'^[a-zA-Z0-9_\s-]+$')
MAX_UID = 2**32 - 1

def validate_channel_name(channel_name):
    """Test channel name validation"""
    if not channel_name:
        raise ValueError("Channel name is required")

    channel_name = str(channel_name).strip()

    if len(channel_name) > MAX_CHANNEL_NAME_LENGTH:
        raise ValueError(f"Channel name too long (max {MAX_CHANNEL_NAME_LENGTH} characters)")

    if len(channel_name) < 1:
        raise ValueError("Channel name cannot be empty")

    if not ALLOWED_CHANNEL_CHARS.match(channel_name):
        raise ValueError("Channel name contains invalid characters")

    return channel_name

def validate_username(username):
    """Test username validation"""
    if not username:
        raise ValueError("Username is required")

    username = ' '.join(str(username).split())

    if len(username) > MAX_USERNAME_LENGTH:
        raise ValueError(f"Username too long (max {MAX_USERNAME_LENGTH} characters)")

    if len(username) < MIN_USERNAME_LENGTH:
        raise ValueError(f"Username too short (min {MIN_USERNAME_LENGTH} characters)")

    if not ALLOWED_USERNAME_CHARS.match(username):
        raise ValueError("Username contains invalid characters")

    return username

def validate_uid(uid):
    """Test UID validation"""
    if uid is None:
        raise ValueError("UID is required")

    try:
        uid_int = int(uid)
        if uid_int < 0 or uid_int > MAX_UID:
            raise ValueError(f"UID out of valid range (0-{MAX_UID})")
    except (ValueError, TypeError):
        raise ValueError("UID must be a valid integer")

    return str(uid)

# Test cases
print("=" * 80)
print("INPUT VALIDATION FUNCTION TESTS")
print("=" * 80)
print()

passed = 0
failed = 0

def test(description, func, value, should_pass=True):
    global passed, failed
    try:
        result = func(value)
        if should_pass:
            print(f"✓ PASS: {description}")
            print(f"  Input: '{value}' → Output: '{result}'")
            passed += 1
        else:
            print(f"✗ FAIL: {description}")
            print(f"  Expected error but got: '{result}'")
            failed += 1
    except Exception as e:
        if not should_pass:
            print(f"✓ PASS: {description}")
            print(f"  Input: '{value}' → Error: {str(e)}")
            passed += 1
        else:
            print(f"✗ FAIL: {description}")
            print(f"  Unexpected error: {str(e)}")
            failed += 1
    print()

# Channel Name Tests
print("CHANNEL NAME VALIDATION")
print("-" * 40)
test("Valid channel: alphanumeric", validate_channel_name, "TestRoom123", True)
test("Valid channel: with underscore", validate_channel_name, "test_room", True)
test("Valid channel: with hyphen", validate_channel_name, "test-room", True)
test("Valid channel: uppercase", validate_channel_name, "TESTROOM", True)
test("Invalid: empty string", validate_channel_name, "", False)
test("Invalid: spaces", validate_channel_name, "test room", False)
test("Invalid: special chars", validate_channel_name, "test@room", False)
test("Invalid: too long (65 chars)", validate_channel_name, "a" * 65, False)
test("Invalid: SQL injection attempt", validate_channel_name, "'; DROP TABLE--", False)
test("Invalid: XSS attempt", validate_channel_name, "<script>alert(1)</script>", False)

# Username Tests
print("USERNAME VALIDATION")
print("-" * 40)
test("Valid username: simple", validate_username, "John", True)
test("Valid username: with space", validate_username, "John Doe", True)
test("Valid username: with hyphen", validate_username, "John-Doe", True)
test("Valid username: with numbers", validate_username, "User123", True)
test("Valid username: whitespace trimmed", validate_username, "  John  Doe  ", True)
test("Invalid: too short (1 char)", validate_username, "A", False)
test("Invalid: empty", validate_username, "", False)
test("Invalid: special chars", validate_username, "John@Doe", False)
test("Invalid: too long (101 chars)", validate_username, "a" * 101, False)
test("Invalid: XSS attempt", validate_username, "<img src=x>", False)

# UID Tests
print("UID VALIDATION")
print("-" * 40)
test("Valid UID: small number", validate_uid, 123, True)
test("Valid UID: large number", validate_uid, 1000000, True)
test("Valid UID: string number", validate_uid, "456", True)
test("Valid UID: max value", validate_uid, MAX_UID, True)
test("Valid UID: zero", validate_uid, 0, True)
test("Invalid: negative", validate_uid, -1, False)
test("Invalid: too large", validate_uid, MAX_UID + 1, False)
test("Invalid: string", validate_uid, "not_a_number", False)
test("Invalid: None", validate_uid, None, False)
test("Invalid: float", validate_uid, "123.45", False)

# Edge Cases
print("EDGE CASE TESTS")
print("-" * 40)
test("Channel: exactly max length (64)", validate_channel_name, "a" * 64, True)
test("Username: exactly min length (2)", validate_username, "AB", True)
test("Username: exactly max length (100)", validate_username, "a" * 100, True)
test("Username: multiple spaces normalized", validate_username, "John    Doe", True)
test("UID: boundary test (2^32-2)", validate_uid, MAX_UID - 1, True)

# Security Tests
print("SECURITY INJECTION TESTS")
print("-" * 40)
test("Channel: path traversal", validate_channel_name, "../../../etc/passwd", False)
test("Channel: null byte", validate_channel_name, "test\x00room", False)
test("Username: HTML injection", validate_username, "<b>Bold</b>", False)
test("Username: JavaScript", validate_username, "javascript:alert(1)", False)
test("UID: SQL injection", validate_uid, "1 OR 1=1", False)

print("=" * 80)
print("TEST SUMMARY")
print("=" * 80)
print(f"✓ Passed: {passed}")
print(f"✗ Failed: {failed}")
print(f"Total:    {passed + failed}")
print()

if failed == 0:
    print("🎉 ALL VALIDATION TESTS PASSED!")
    print("✅ Input validation is working correctly.")
else:
    print(f"❌ {failed} test(s) failed.")
    print("Please review validation logic.")
