"""
auth.py - create and verify JWTs.

  * The FastAPI backend calls create_token() when a user logs in.
  * The MCP server calls decode_token() on every request to find out
    WHO is calling (user_id) and WHAT they are allowed to do (scopes).
"""

from datetime import datetime, timedelta, timezone

import jwt  # PyJWT

from common.config import JWT_ALGORITHM, JWT_AUDIENCE, JWT_EXPIRY_MINUTES, JWT_ISSUER, JWT_SECRET


# When: POST /api/login, after the email + password are verified.
# Use:  Builds and signs the JWT carrying the user's id, role and scopes.
def create_token(user: dict) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user["id"]),     # subject = the user id
        "name": user["name"],
        "email": user["email"],
        "role": user["role"],       # learner | support | admin
        "scope": " ".join(user["scopes"]),  # permissions (role defaults or custom), e.g. "faq:read tickets:read ..."
        "iss": JWT_ISSUER,          # issuer: our backend
        "aud": JWT_AUDIENCE,        # audience: only the MCP server should accept it
        "iat": now,
        "exp": now + timedelta(minutes=JWT_EXPIRY_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


# When: On every request: by the MCP server's JWTTokenVerifier and by the backend's get_current_user.
# Use:  Verifies the JWT and returns its claims (raises if the token is invalid or expired).
def decode_token(token: str) -> dict:
    """
    Verify signature, expiry, issuer and audience.
    Raises jwt.InvalidTokenError (or a subclass) if anything is wrong.
    """
    return jwt.decode(
        token,
        JWT_SECRET,
        algorithms=[JWT_ALGORITHM],
        audience=JWT_AUDIENCE,
        issuer=JWT_ISSUER,
        options={"require": ["exp", "iss", "aud", "sub", "scope"]},
    )