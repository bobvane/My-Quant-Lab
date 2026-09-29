"""Secret handling helpers.

API keys are stored encrypted at rest using a key derived from
``settings.secret_key`` (Fernet when available, otherwise a keyed XOR fallback
with an explicit warning so the operator knows to install ``cryptography``).
Secrets are never returned through the API and never logged.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os

from app.core.config import settings

logger = logging.getLogger(__name__)

__all__ = ["decrypt_secret", "encrypt_secret", "mask_secret"]


def _fernet() -> object | None:
    try:
        from cryptography.fernet import Fernet  # type: ignore

        key = base64.urlsafe_b64encode(hashlib.sha256(settings.secret_key.encode()).digest())
        return Fernet(key)
    except ImportError:  # pragma: no cover - optional dependency
        logger.warning("cryptography not installed; using fallback obfuscation")
        return None


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a secret for storage."""

    fernet = _fernet()
    if fernet is not None:
        return fernet.encrypt(plaintext.encode()).decode()  # type: ignore[attr-defined]
    key = hashlib.sha256(settings.secret_key.encode()).digest()
    data = plaintext.encode()
    xored = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
    return "fallback:" + base64.urlsafe_b64encode(xored).decode()


def decrypt_secret(ciphertext: str) -> str:
    """Decrypt a stored secret."""

    if ciphertext.startswith("fallback:"):
        key = hashlib.sha256(settings.secret_key.encode()).digest()
        raw = base64.urlsafe_b64decode(ciphertext[len("fallback:") :])
        return bytes(b ^ key[i % len(key)] for i, b in enumerate(raw)).decode()
    fernet = _fernet()
    if fernet is None:  # pragma: no cover
        raise RuntimeError("cryptography is required to decrypt this secret")
    return fernet.decrypt(ciphertext.encode()).decode()  # type: ignore[attr-defined]


def mask_secret(value: str | None, keep: int = 4) -> str:
    """Return a masked representation safe for logs and UI."""

    if not value:
        return ""
    if len(value) <= keep:
        return "*" * len(value)
    return f"{value[:keep]}{'*' * (len(value) - keep)}"


def secret_is_set(value: str | None) -> bool:
    return bool(value and os.environ.get("SECRET_MASKING_ENABLED", "1") == "1")
