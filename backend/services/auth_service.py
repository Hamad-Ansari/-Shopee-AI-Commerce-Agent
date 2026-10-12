"""Authentication: PBKDF2 password hashing + single-show API keys.

Secrets never appear in prompts, logs or API responses — only the sha256 hash of an
API key is stored, and the plaintext key is returned exactly once at registration.
"""

import hashlib
import hmac
import secrets

from backend.config import get_settings
from backend.database import models

_PBKDF2_ITERATIONS = 120_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${_PBKDF2_ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _algo, iterations, salt_hex, hash_hex = stored.split("$")
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


def generate_api_key() -> str:
    return f"sb_{secrets.token_urlsafe(32)}"


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def register(db, user_id: str, password: str, name: str = "", role: str = "buyer"):
    """Create a user and return (user, plaintext_api_key). The key is shown once."""
    role = role if role in ("buyer", "seller") else "buyer"
    api_key = generate_api_key()
    user = models.User(
        id=user_id,
        name=name or user_id,
        role=role,
        spending_limit_pkr=get_settings().default_spending_limit_pkr,
        password_hash=hash_password(password),
        api_key_hash=hash_api_key(api_key),
    )
    db.add(user)
    db.commit()
    return user, api_key


def authenticate_by_api_key(db, api_key: str):
    """Resolve a Bearer API key to a user, or None."""
    if not api_key:
        return None
    return db.query(models.User).filter_by(api_key_hash=hash_api_key(api_key)).first()
