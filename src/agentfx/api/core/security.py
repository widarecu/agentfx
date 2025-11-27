from datetime import datetime, timedelta
from typing import Any
from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
import logging

from .config import settings
from .database import get_db
from ..models.user import User

logger = logging.getLogger(__name__)

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_STR}/auth/login")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    # Convert to Unix timestamp (seconds since epoch)
    to_encode.update({"exp": int(expire.timestamp())})
    encoded_jwt = jwt.encode(
        to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM
    )
    return encoded_jwt


def decode_token(token: str) -> dict[str, Any]:
    """
    Decode and verify a JWT token.

    Args:
        token: The JWT token to decode

    Returns:
        The decoded token payload

    Raises:
        JWTError: If the token is invalid or expired
    """
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])


async def get_current_user(
    db: Session = Depends(get_db), token: str = Depends(oauth2_scheme)
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        try:
            # First try to decode with expiration check
            payload = jwt.decode(
                token,
                settings.SECRET_KEY,
                algorithms=[settings.ALGORITHM],
                options={"verify_exp": True},  # Explicitly enable expiration check
            )

            username: str = payload.get("sub")
            if username is None:
                raise credentials_exception

        except ExpiredSignatureError:
            # Handle expired token with specific error message
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has expired",
                headers={"WWW-Authenticate": "Bearer"},
            )

        except JWTError:
            # For any other JWT error, return generic credentials error
            raise credentials_exception

    except HTTPException:
        # Re-raise HTTP exceptions (including our custom ones)
        raise

    except Exception:
        # Log unexpected errors but don't expose them to the client
        raise credentials_exception

    user = db.query(User).filter(User.email == username).first()
    if user is None:
        raise credentials_exception

    return user


async def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user


# WebSocket authentication
async def get_current_user_ws(token: str | None = None) -> User | None:
    """
    Authenticate WebSocket connection using JWT token.
    Returns the user if authenticated, None otherwise.
    """
    if not token:
        logger.warning("No token provided for WebSocket connection")
        return None

    try:
        # Remove 'Bearer ' prefix if present
        if token.lower().startswith("bearer "):
            token = token[7:].strip()

        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        username: str = payload.get("sub")
        if username is None:
            logger.warning("No username in token")
            return None

        # Get database session
        db = next(get_db())
        user = db.query(User).filter(User.email == username).first()

        if user is None or not user.is_active:
            logger.warning(f"User not found or inactive: {username}")
            return None

        logger.info(f"Authenticated WebSocket user: {username}")
        return user

    except JWTError as e:
        logger.warning(f"JWT error in WebSocket auth: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"Error in WebSocket auth: {str(e)}")
        return None
