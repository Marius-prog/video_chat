"""Errors raised by the service layer. Messages are safe to show to end users."""
from __future__ import annotations


class ServiceError(Exception):
    status = 400
    code = "error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class InvalidCredentials(ServiceError):
    status = 401
    code = "invalid_credentials"


class AccountExists(ServiceError):
    status = 409
    code = "account_exists"


class NotAuthenticated(ServiceError):
    status = 401
    code = "not_authenticated"


class NotFound(ServiceError):
    status = 404
    code = "not_found"


class Forbidden(ServiceError):
    status = 403
    code = "forbidden"


class RateLimited(ServiceError):
    status = 429
    code = "rate_limited"

    def __init__(self, retry_after: int):
        super().__init__(f"Too many attempts. Try again in {retry_after} seconds.")
        self.retry_after = retry_after


class RtcUnavailable(ServiceError):
    status = 503
    code = "rtc_unavailable"
