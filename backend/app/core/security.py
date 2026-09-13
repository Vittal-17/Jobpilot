import secrets
import hashlib
import bcrypt

# Dummy hash for timing attack mitigation (hash of "dummy" with a standard work factor)
# Calculated once to avoid generating a new salt every time, which ensures stable timing.
DUMMY_PASSWORD_HASH = "$2b$12$IS3BfN3Mz9aDg46VDBkP4eKXKySKU1oJwkR3N.fv7ks8kjaMUS19."

def get_password_hash(password: str) -> str:
    """Hash a password using bcrypt. Rejects passwords over 72 bytes."""
    pwd_bytes = password.encode('utf-8')
    if len(pwd_bytes) > 72:
        raise ValueError("Password exceeds maximum supported length of 72 bytes.")

    salt = bcrypt.gensalt()
    hashed_password = bcrypt.hashpw(password=pwd_bytes, salt=salt)
    return hashed_password.decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against a bcrypt hash. Rejects passwords over 72 bytes."""
    pwd_bytes = plain_password.encode('utf-8')
    if len(pwd_bytes) > 72:
        return False

    hashed_password_bytes = hashed_password.encode('utf-8')
    try:
        return bcrypt.checkpw(password=pwd_bytes, hashed_password=hashed_password_bytes)
    except ValueError:
        return False

def generate_session_token() -> str:
    """Generate a cryptographically secure random session token."""
    return secrets.token_urlsafe(32)

def hash_session_token(token: str) -> str:
    """One-way hash a session token for safe storage (SHA-256)."""
    return hashlib.sha256(token.encode('utf-8')).hexdigest()

def verify_dummy_password(plain_password: str) -> bool:
    """Run a bcrypt verification against a dummy hash to mitigate timing attacks."""
    pwd_bytes = plain_password.encode('utf-8')
    # Use a bounded dummy input if oversized, to ensure bcrypt work is still performed
    if len(pwd_bytes) > 72:
        pwd_bytes = b"bounded_dummy_password_input"

    hashed_password_bytes = DUMMY_PASSWORD_HASH.encode('utf-8')
    try:
        return bcrypt.checkpw(password=pwd_bytes, hashed_password=hashed_password_bytes)
    except ValueError:
        return False
