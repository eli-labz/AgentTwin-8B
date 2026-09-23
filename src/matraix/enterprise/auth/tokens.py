"""Opaque bearer tokens for service accounts. Raw tokens are never stored."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets

__all__ = ["generate_token", "hash_token", "token_prefix", "verify_token"]

TOKEN_PREFIX = "atsk_"
_PEPPER_ENV = "MATRIX_ENTERPRISE_TOKEN_PEPPER"


def _pepper() -> bytes:
    return os.environ.get(_PEPPER_ENV, "").encode("utf-8")


def generate_token() -> str:
    """Return a fresh high-entropy bearer token (shown to the caller once)."""
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Salted (peppered) sha256 hex digest; the only form persisted."""
    return hashlib.sha256(_pepper() + token.encode("utf-8")).hexdigest()


def token_prefix(token: str) -> str:
    return token[: len(TOKEN_PREFIX) + 6]


def verify_token(token: str, stored_hash: str) -> bool:
    return hmac.compare_digest(hash_token(token), stored_hash)
