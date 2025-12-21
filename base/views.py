"""
Video chat views with military-grade security.

This module implements secure video chat functionality with:
- User authentication (login, register, logout)
- Rate limiting to prevent abuse
- Input validation and sanitization
- CSRF protection
- Comprehensive error handling
- Security logging
- Session management
"""

import json
import logging
import random
import re
import time
from typing import Dict, Any, Optional

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.http import JsonResponse, HttpRequest, HttpResponse
from django.shortcuts import render, redirect
from django.utils.html import escape
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods, require_GET, require_POST
from ratelimit.decorators import ratelimit

from agora_token_builder import RtcTokenBuilder
from .models import RoomMember

# Configure logging
logger = logging.getLogger('base')

# Security constants
MAX_CHANNEL_NAME_LENGTH = 64
MAX_USERNAME_LENGTH = 100
MIN_USERNAME_LENGTH = 2
ALLOWED_CHANNEL_CHARS = re.compile(r'^[a-zA-Z0-9_-]+$')
ALLOWED_USERNAME_CHARS = re.compile(r'^[a-zA-Z0-9_\s-]+$')
TOKEN_EXPIRATION_SECONDS = 3600  # 1 hour
MAX_UID = 2**32 - 1  # Agora max UID


# ==============================================================================
# UTILITY FUNCTIONS
# ==============================================================================

def validate_channel_name(channel_name: Optional[str]) -> str:
    """
    Validate and sanitize channel name.

    Args:
        channel_name: The channel name to validate

    Returns:
        Sanitized channel name

    Raises:
        ValidationError: If channel name is invalid
    """
    if not channel_name:
        raise ValidationError("Channel name is required")

    # Remove whitespace and convert to string
    channel_name = str(channel_name).strip()

    # Check length
    if len(channel_name) > MAX_CHANNEL_NAME_LENGTH:
        raise ValidationError(f"Channel name too long (max {MAX_CHANNEL_NAME_LENGTH} characters)")

    if len(channel_name) < 1:
        raise ValidationError("Channel name cannot be empty")

    # Check allowed characters
    if not ALLOWED_CHANNEL_CHARS.match(channel_name):
        raise ValidationError("Channel name contains invalid characters (only alphanumeric, underscore, and hyphen allowed)")

    return escape(channel_name)


def validate_username(username: Optional[str]) -> str:
    """
    Validate and sanitize username.

    Args:
        username: The username to validate

    Returns:
        Sanitized username

    Raises:
        ValidationError: If username is invalid
    """
    if not username:
        raise ValidationError("Username is required")

    # Remove excess whitespace
    username = ' '.join(str(username).split())

    # Check length
    if len(username) > MAX_USERNAME_LENGTH:
        raise ValidationError(f"Username too long (max {MAX_USERNAME_LENGTH} characters)")

    if len(username) < MIN_USERNAME_LENGTH:
        raise ValidationError(f"Username too short (min {MIN_USERNAME_LENGTH} characters)")

    # Check allowed characters
    if not ALLOWED_USERNAME_CHARS.match(username):
        raise ValidationError("Username contains invalid characters")

    return escape(username)


def validate_uid(uid: Any) -> str:
    """
    Validate UID.

    Args:
        uid: The UID to validate

    Returns:
        Validated UID as string

    Raises:
        ValidationError: If UID is invalid
    """
    if uid is None:
        raise ValidationError("UID is required")

    try:
        uid_int = int(uid)
        if uid_int < 0 or uid_int > MAX_UID:
            raise ValidationError(f"UID out of valid range (0-{MAX_UID})")
    except (ValueError, TypeError):
        raise ValidationError("UID must be a valid integer")

    return str(uid)


def get_agora_credentials() -> Dict[str, str]:
    """
    Retrieve Agora credentials from environment.

    Returns:
        Dictionary with app_id and app_certificate

    Raises:
        ValidationError: If credentials are not configured
    """
    app_id = settings.AGORA_APP_ID
    app_certificate = settings.AGORA_APP_CERTIFICATE

    if not app_id or not app_certificate:
        logger.error("Agora credentials not configured in environment")
        raise ValidationError("Video chat service is not properly configured")

    return {
        'app_id': app_id,
        'app_certificate': app_certificate
    }


def csrf_failure(request: HttpRequest, reason: str = "") -> JsonResponse:
    """
    Custom CSRF failure handler.

    Args:
        request: The HTTP request
        reason: Reason for CSRF failure

    Returns:
        JSON response with error
    """
    logger.warning(f"CSRF failure: {reason} from IP {request.META.get('REMOTE_ADDR')}")
    return JsonResponse({
        'error': 'CSRF verification failed',
        'detail': 'Request rejected for security reasons'
    }, status=403)


# ==============================================================================
# AUTHENTICATION VIEWS
# ==============================================================================

@ratelimit(key='ip', rate='10/m', block=True)
def login_view(request: HttpRequest) -> HttpResponse:
    """
    Handle user login.
    """
    if request.user.is_authenticated:
        return redirect('lobby')

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')

        if not username or not password:
            messages.error(request, 'AUTHENTICATION REQUIRED: Enter credentials')
            return render(request, 'base/login.html')

        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)
            logger.info(f"User logged in: {username}")
            messages.success(request, f'ACCESS GRANTED: Welcome, {user.username}')
            return redirect('lobby')
        else:
            logger.warning(f"Failed login attempt for: {username}")
            messages.error(request, 'ACCESS DENIED: Invalid credentials')

    return render(request, 'base/login.html')


@ratelimit(key='ip', rate='5/m', block=True)
def register_view(request: HttpRequest) -> HttpResponse:
    """
    Handle user registration.
    """
    if request.user.is_authenticated:
        return redirect('lobby')

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password1 = request.POST.get('password1', '')
        password2 = request.POST.get('password2', '')
        callsign = request.POST.get('callsign', '').strip()

        # Validation
        errors = []

        if not username or len(username) < 3:
            errors.append('Callsign must be at least 3 characters')

        if not ALLOWED_USERNAME_CHARS.match(username):
            errors.append('Callsign contains invalid characters')

        if len(password1) < 8:
            errors.append('Password must be at least 8 characters')

        if password1 != password2:
            errors.append('Passwords do not match')

        if User.objects.filter(username=username).exists():
            errors.append('Callsign already in use')

        if errors:
            for error in errors:
                messages.error(request, f'REGISTRATION FAILED: {error}')
            return render(request, 'base/register.html')

        # Create user
        try:
            user = User.objects.create_user(
                username=username,
                password=password1,
                first_name=callsign or username
            )
            login(request, user)
            logger.info(f"New user registered: {username}")
            messages.success(request, f'REGISTRATION COMPLETE: Welcome to the unit, {username}')
            return redirect('lobby')
        except Exception as e:
            logger.error(f"Registration failed: {str(e)}")
            messages.error(request, 'REGISTRATION FAILED: System error')

    return render(request, 'base/register.html')


@login_required
def logout_view(request: HttpRequest) -> HttpResponse:
    """
    Handle user logout.
    """
    username = request.user.username
    logout(request)
    logger.info(f"User logged out: {username}")
    messages.success(request, 'SIGNED OUT: Session terminated')
    return redirect('login')


# ==============================================================================
# MAIN VIEWS
# ==============================================================================

@login_required
@require_GET
def lobby(request: HttpRequest) -> HttpResponse:
    """
    Render the lobby page where users enter room details.

    Args:
        request: The HTTP request

    Returns:
        Rendered lobby template
    """
    return render(request, 'base/lobby.html')


@login_required
@require_GET
@ensure_csrf_cookie
def room(request: HttpRequest) -> HttpResponse:
    """
    Render the video chat room page.

    Args:
        request: The HTTP request

    Returns:
        Rendered room template
    """
    # Verify session has required data
    if not request.session.get('room_name'):
        logger.warning(f"Unauthorized room access attempt from IP {request.META.get('REMOTE_ADDR')}")
        return redirect('lobby')

    return render(request, 'base/room.html', {'user': request.user})


@require_GET
@ratelimit(key='ip', rate='10/m', block=True)  # Max 10 token requests per minute per IP
def getToken(request: HttpRequest) -> JsonResponse:
    """
    Generate Agora RTC token for a channel.

    Rate limited to prevent abuse. Validates channel name and generates
    a secure token with 1-hour expiration.

    Args:
        request: The HTTP request with 'channel' parameter

    Returns:
        JSON response with token and UID
    """
    try:
        # Validate channel name
        channel_name = validate_channel_name(request.GET.get('channel'))

        # Get credentials from environment
        credentials = get_agora_credentials()

        # Generate cryptographically secure UID
        uid = random.SystemRandom().randint(1, MAX_UID)

        # Token parameters
        current_timestamp = int(time.time())
        privilege_expired_ts = current_timestamp + TOKEN_EXPIRATION_SECONDS
        role = 1  # Publisher role

        # Build token
        token = RtcTokenBuilder.buildTokenWithUid(
            credentials['app_id'],
            credentials['app_certificate'],
            channel_name,
            uid,
            role,
            privilege_expired_ts
        )

        # Store in session for validation
        request.session['room_name'] = channel_name
        request.session['uid'] = uid
        request.session['token_issued_at'] = current_timestamp

        logger.info(f"Token generated for channel '{channel_name}' (UID: {uid})")

        return JsonResponse({
            'token': token,
            'uid': uid,
            'app_id': credentials['app_id']  # Send app_id securely from backend
        })

    except ValidationError as e:
        logger.warning(f"Token request validation failed: {str(e)}")
        return JsonResponse({'error': str(e)}, status=400)

    except Exception as e:
        logger.error(f"Token generation failed: {str(e)}")
        return JsonResponse({'error': 'Failed to generate token'}, status=500)


@require_POST
@ratelimit(key='ip', rate='30/m', block=True)  # Max 30 member creations per minute per IP
def createMember(request: HttpRequest) -> JsonResponse:
    """
    Create a new room member record.

    Rate limited and fully validated. Uses CSRF protection.

    Args:
        request: The HTTP request with JSON body containing name, UID, room_name

    Returns:
        JSON response with member data
    """
    try:
        # Parse and validate JSON
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            raise ValidationError("Invalid JSON data")

        # Validate all inputs
        username = validate_username(data.get('name'))
        uid = validate_uid(data.get('UID'))
        channel_name = validate_channel_name(data.get('room_name'))

        # Verify session matches request (prevent session hijacking)
        session_uid = request.session.get('uid')
        session_room = request.session.get('room_name')

        if session_uid and str(session_uid) != uid:
            logger.warning(f"UID mismatch: session={session_uid}, request={uid}")
            raise ValidationError("Session validation failed")

        if session_room and session_room != channel_name:
            logger.warning(f"Room mismatch: session={session_room}, request={channel_name}")
            raise ValidationError("Session validation failed")

        # Check for existing member with same UID in room
        existing = RoomMember.objects.filter(uid=uid, room_name=channel_name).first()
        if existing:
            # Update existing member
            existing.name = username
            existing.insession = True
            existing.save()
            member = existing
            created = False
        else:
            # Create new member
            member = RoomMember.objects.create(
                name=username,
                uid=uid,
                room_name=channel_name,
                insession=True
            )
            created = True

        action = "created" if created else "updated"
        logger.info(f"Member {action}: {username} (UID: {uid}) in room '{channel_name}'")

        return JsonResponse({
            'name': member.name,
            'uid': member.uid,
            'created': created
        })

    except ValidationError as e:
        logger.warning(f"Member creation validation failed: {str(e)}")
        return JsonResponse({'error': str(e)}, status=400)

    except Exception as e:
        logger.error(f"Member creation failed: {str(e)}")
        return JsonResponse({'error': 'Failed to create member'}, status=500)


@require_GET
@ratelimit(key='ip', rate='60/m', block=True)  # Max 60 member queries per minute per IP
def getMember(request: HttpRequest) -> JsonResponse:
    """
    Retrieve member information by UID and room name.

    Args:
        request: The HTTP request with UID and room_name parameters

    Returns:
        JSON response with member name
    """
    try:
        # Validate inputs
        uid = validate_uid(request.GET.get('UID'))
        room_name = validate_channel_name(request.GET.get('room_name'))

        # Query member
        try:
            member = RoomMember.objects.get(uid=uid, room_name=room_name)
        except RoomMember.DoesNotExist:
            logger.warning(f"Member not found: UID {uid} in room '{room_name}'")
            return JsonResponse({'error': 'Member not found'}, status=404)

        return JsonResponse({'name': member.name})

    except ValidationError as e:
        logger.warning(f"Get member validation failed: {str(e)}")
        return JsonResponse({'error': str(e)}, status=400)

    except Exception as e:
        logger.error(f"Get member failed: {str(e)}")
        return JsonResponse({'error': 'Failed to retrieve member'}, status=500)


@require_POST
@ratelimit(key='ip', rate='30/m', block=True)  # Max 30 deletions per minute per IP
def deleteMember(request: HttpRequest) -> JsonResponse:
    """
    Delete a room member record.

    Rate limited and validates session ownership. Uses CSRF protection.

    Args:
        request: The HTTP request with JSON body containing name, UID, room_name

    Returns:
        JSON response confirming deletion
    """
    try:
        # Parse and validate JSON
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            raise ValidationError("Invalid JSON data")

        # Validate all inputs
        username = validate_username(data.get('name'))
        uid = validate_uid(data.get('UID'))
        room_name = validate_channel_name(data.get('room_name'))

        # Verify session ownership (users can only delete their own member record)
        session_uid = request.session.get('uid')
        if session_uid and str(session_uid) != uid:
            logger.warning(f"Unauthorized delete attempt: session UID {session_uid} tried to delete UID {uid}")
            raise ValidationError("Unauthorized: You can only remove yourself from rooms")

        # Query and delete member
        try:
            member = RoomMember.objects.get(name=username, uid=uid, room_name=room_name)
            member.delete()
            logger.info(f"Member deleted: {username} (UID: {uid}) from room '{room_name}'")
        except RoomMember.DoesNotExist:
            # Don't reveal whether member exists
            logger.warning(f"Delete failed - member not found: UID {uid} in room '{room_name}'")
            pass

        # Clear session data
        if 'room_name' in request.session:
            del request.session['room_name']
        if 'uid' in request.session:
            del request.session['uid']
        if 'token_issued_at' in request.session:
            del request.session['token_issued_at']

        return JsonResponse({'status': 'success', 'message': 'Member removed'})

    except ValidationError as e:
        logger.warning(f"Member deletion validation failed: {str(e)}")
        return JsonResponse({'error': str(e)}, status=400)

    except Exception as e:
        logger.error(f"Member deletion failed: {str(e)}")
        return JsonResponse({'error': 'Failed to delete member'}, status=500)
