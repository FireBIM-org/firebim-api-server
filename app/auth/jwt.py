import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from sqlmodel import Session

from app.core.config import settings
from app.models.database import get_db
from app.models.models import User

# Bearer token extraction
security = HTTPBearer()

# Cached JWKS client for verifying Authentik-signed tokens
_jwks_client = None


def get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        jwks_url = f"{settings.AUTHENTIK_ISSUER_URL.rstrip('/')}/jwks/"
        _jwks_client = PyJWKClient(jwks_url)
    return _jwks_client


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    """Validate an Authentik-issued JWT and return the local user (auto-provisioned)."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    token = credentials.credentials

    try:
        jwks_client = get_jwks_client()
        signing_key = jwks_client.get_signing_key_from_jwt(token)

        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "ES256"],
            issuer=settings.AUTHENTIK_ISSUER_URL,
            options={"verify_aud": False},
        )

        sub: str = payload.get("sub")
        if sub is None:
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception from None

    # Find or auto-provision user based on Authentik subject
    user = db.query(User).filter(User.authentik_sub == sub).first()

    if user is None:
        email = payload.get("email", "")
        username = payload.get("preferred_username", sub)

        user = User(
            authentik_sub=sub,
            username=username,
            email=email,
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        # Keep local profile in sync with Authentik claims
        updated = False
        email = payload.get("email")
        username = payload.get("preferred_username")

        if email and user.email != email:
            user.email = email
            updated = True
        if username and user.username != username:
            user.username = username
            updated = True
        if updated:
            db.commit()
            db.refresh(user)

    return user


async def get_current_active_user(current_user: User = Depends(get_current_user)):
    """Check if the current user is active."""
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user
