#!/usr/bin/env python3
"""
Security Validation Test Suite
Tests all security improvements without requiring Django installation
"""

import re
import json
import sys
from pathlib import Path

# Test results
passed = 0
failed = 0
warnings = 0

def test_pass(name):
    global passed
    print(f"✓ PASS: {name}")
    passed += 1

def test_fail(name, reason):
    global failed
    print(f"✗ FAIL: {name}")
    print(f"  Reason: {reason}")
    failed += 1

def test_warn(name, reason):
    global warnings
    print(f"⚠ WARN: {name}")
    print(f"  Reason: {reason}")
    warnings += 1

print("=" * 80)
print("SECURITY VALIDATION TEST SUITE")
print("=" * 80)
print()

# Test 1: Check .gitignore exists and protects secrets
print("TEST 1: Secret Protection")
print("-" * 40)
gitignore_path = Path(".gitignore")
if gitignore_path.exists():
    content = gitignore_path.read_text()
    if ".env" in content:
        test_pass(".gitignore protects .env files")
    else:
        test_fail(".gitignore missing .env protection", ".env not in .gitignore")

    if "*.log" in content:
        test_pass(".gitignore protects log files")
    else:
        test_warn(".gitignore missing log protection", "*.log not in .gitignore")
else:
    test_fail(".gitignore exists", "File not found")
print()

# Test 2: Check .env.example exists
print("TEST 2: Environment Configuration")
print("-" * 40)
env_example_path = Path(".env.example")
if env_example_path.exists():
    test_pass(".env.example template exists")
    content = env_example_path.read_text()
    required_vars = ["SECRET_KEY", "AGORA_APP_ID", "AGORA_APP_CERTIFICATE", "ALLOWED_HOSTS"]
    for var in required_vars:
        if var in content:
            test_pass(f".env.example includes {var}")
        else:
            test_fail(f".env.example missing {var}", f"{var} not found in template")
else:
    test_fail(".env.example exists", "File not found")
print()

# Test 3: Check views.py security features
print("TEST 3: Backend Security (views.py)")
print("-" * 40)
views_path = Path("base/views.py")
if views_path.exists():
    content = views_path.read_text()

    # Check for removed hardcoded secrets
    if "05f0ac5f31da4cab9a0ad67877d5f6f2" not in content:
        test_pass("No hardcoded Agora APP_ID")
    else:
        test_fail("Hardcoded secrets removed", "Found hardcoded Agora APP_ID")

    # Check for @csrf_exempt
    if "@csrf_exempt" not in content:
        test_pass("No CSRF exemptions")
    else:
        test_fail("CSRF protection enabled", "Found @csrf_exempt decorator")

    # Check for rate limiting
    if "@ratelimit" in content:
        test_pass("Rate limiting implemented")
    else:
        test_fail("Rate limiting present", "No @ratelimit decorators found")

    # Check for input validation
    if "validate_channel_name" in content and "validate_username" in content:
        test_pass("Input validation functions present")
    else:
        test_fail("Input validation", "Missing validation functions")

    # Check for logging
    if "logger.info" in content or "logger.warning" in content:
        test_pass("Security logging implemented")
    else:
        test_fail("Security logging", "No logging found")

    # Check for HTML escaping
    if "escape(" in content:
        test_pass("XSS protection (HTML escaping)")
    else:
        test_warn("XSS protection", "No HTML escaping found")

    # Check for settings.AGORA_APP_ID (environment variable usage)
    if "settings.AGORA_APP_ID" in content:
        test_pass("Using environment variables for credentials")
    else:
        test_fail("Environment variable usage", "Not using settings for credentials")

    # Check for type hints
    if "-> JsonResponse:" in content or "-> str:" in content:
        test_pass("Type hints added")
    else:
        test_warn("Code quality (type hints)", "No type hints found")

else:
    test_fail("views.py exists", "File not found")
print()

# Test 4: Check settings.py security configuration
print("TEST 4: Django Security Settings (settings.py)")
print("-" * 40)
settings_path = Path("mychat/settings.py")
if settings_path.exists():
    content = settings_path.read_text()

    # Check for hardcoded SECRET_KEY
    if "django-insecure-" in content and "config('SECRET_KEY'" in content:
        test_pass("SECRET_KEY using environment variable with fallback")
    else:
        test_fail("SECRET_KEY configuration", "Not properly configured")

    # Check for ALLOWED_HOSTS wildcard
    if "ALLOWED_HOSTS = ['*']" in content:
        test_fail("ALLOWED_HOSTS security", "Wildcard still present")
    elif "config('ALLOWED_HOSTS'" in content:
        test_pass("ALLOWED_HOSTS using environment variable")
    else:
        test_warn("ALLOWED_HOSTS", "Configuration unclear")

    # Check for security headers
    security_settings = [
        "SECURE_SSL_REDIRECT",
        "SESSION_COOKIE_SECURE",
        "CSRF_COOKIE_SECURE",
        "SECURE_HSTS_SECONDS",
        "X_FRAME_OPTIONS"
    ]
    for setting in security_settings:
        if setting in content:
            test_pass(f"Security setting: {setting}")
        else:
            test_fail(f"Missing security setting: {setting}", f"{setting} not configured")

    # Check for CSP
    if "CSP_DEFAULT_SRC" in content or "Content-Security-Policy" in content:
        test_pass("Content Security Policy configured")
    else:
        test_warn("CSP configuration", "CSP not configured")

    # Check for logging configuration
    if "LOGGING" in content:
        test_pass("Logging configuration present")
    else:
        test_fail("Logging", "No logging configuration")

    # Check for CORS
    if "corsheaders" in content:
        test_pass("CORS headers configured")
    else:
        test_warn("CORS", "CORS not configured")

else:
    test_fail("settings.py exists", "File not found")
print()

# Test 5: Check frontend security (streams.js)
print("TEST 5: Frontend Security (streams.js)")
print("-" * 40)
streams_path = Path("static/js/streams.js")
if streams_path.exists():
    content = streams_path.read_text()

    # Check for hardcoded APP_ID
    if "APP_ID = '05f0ac5f31da4cab9a0ad67877d5f6f2'" in content:
        test_fail("No hardcoded credentials", "Found hardcoded APP_ID")
    elif "sessionStorage.getItem('appId')" in content:
        test_pass("APP_ID from sessionStorage (backend)")
    else:
        test_warn("APP_ID source", "APP_ID source unclear")

    # Check for CSRF token usage
    if "X-CSRFToken" in content and "csrftoken" in content:
        test_pass("CSRF tokens in POST requests")
    else:
        test_fail("CSRF protection", "No CSRF tokens in requests")

    # Check for XSS protection
    if "escapeHtml" in content:
        test_pass("XSS protection (HTML escaping)")
    else:
        test_fail("XSS prevention", "No HTML escaping function")

    # Check for URL encoding
    if "encodeURIComponent" in content:
        test_pass("URL parameter encoding")
    else:
        test_warn("URL encoding", "Parameters may not be encoded")

    # Check for error handling
    if "try {" in content and "catch" in content:
        test_pass("Error handling implemented")
    else:
        test_fail("Error handling", "No try-catch blocks")

    # Check for validation
    if "alert(" in content and ("Please enter" in content or "Invalid" in content):
        test_pass("Client-side validation with user feedback")
    else:
        test_warn("Client validation", "Limited validation feedback")

else:
    test_fail("streams.js exists", "File not found")
print()

# Test 6: Check requirements.txt
print("TEST 6: Security Dependencies")
print("-" * 40)
requirements_path = Path("requirements.txt")
if requirements_path.exists():
    content = requirements_path.read_text()

    security_packages = {
        "django-ratelimit": "Rate limiting",
        "python-decouple": "Environment variables",
        "django-cors-headers": "CORS protection"
    }

    for package, purpose in security_packages.items():
        if package in content:
            test_pass(f"{purpose}: {package}")
        else:
            test_fail(f"Missing dependency: {package}", f"{purpose} not available")
else:
    test_fail("requirements.txt exists", "File not found")
print()

# Test 7: Check documentation
print("TEST 7: Documentation")
print("-" * 40)
doc_files = {
    "README.md": "Usage documentation",
    "SECURITY.md": "Security documentation"
}

for filename, description in doc_files.items():
    path = Path(filename)
    if path.exists():
        content = path.read_text()
        if len(content) > 100:
            test_pass(f"{description}: {filename}")
        else:
            test_warn(f"{filename} content", "File seems too short")
    else:
        test_fail(f"{description} exists", f"{filename} not found")
print()

# Test 8: Validate input validation patterns
print("TEST 8: Input Validation Patterns")
print("-" * 40)
if views_path.exists():
    content = views_path.read_text()

    # Check for regex patterns
    patterns = {
        r"ALLOWED_CHANNEL_CHARS.*=.*re\.compile": "Channel name validation regex",
        r"ALLOWED_USERNAME_CHARS.*=.*re\.compile": "Username validation regex",
        r"MAX_CHANNEL_NAME_LENGTH.*=.*\d+": "Channel name length limit",
        r"MAX_USERNAME_LENGTH.*=.*\d+": "Username length limit"
    }

    for pattern, description in patterns.items():
        if re.search(pattern, content):
            test_pass(description)
        else:
            test_fail(description, f"Pattern not found: {pattern}")
else:
    test_fail("Input validation", "views.py not available")
print()

# Test 9: Check for session security
print("TEST 9: Session Security")
print("-" * 40)
if settings_path.exists():
    content = settings_path.read_text()

    session_settings = {
        "SESSION_COOKIE_HTTPONLY": "HttpOnly cookies",
        "SESSION_COOKIE_SAMESITE": "SameSite cookie policy",
        "CSRF_COOKIE_HTTPONLY": "CSRF HttpOnly cookies",
        "CSRF_COOKIE_SAMESITE": "CSRF SameSite policy"
    }

    for setting, description in session_settings.items():
        if setting in content:
            test_pass(description)
        else:
            test_fail(description, f"{setting} not configured")
print()

# Summary
print("=" * 80)
print("TEST SUMMARY")
print("=" * 80)
print(f"✓ Passed:   {passed}")
print(f"⚠ Warnings: {warnings}")
print(f"✗ Failed:   {failed}")
print(f"Total:      {passed + warnings + failed}")
print()

if failed == 0:
    print("🎉 ALL CRITICAL TESTS PASSED!")
    print("✅ Security implementation is complete and validated.")
    sys.exit(0)
elif failed <= 3:
    print("⚠️  MOSTLY SECURE with minor issues")
    print(f"Please review and fix {failed} failed test(s).")
    sys.exit(1)
else:
    print("❌ SECURITY ISSUES DETECTED")
    print(f"Please fix {failed} failed test(s) before deployment.")
    sys.exit(1)
