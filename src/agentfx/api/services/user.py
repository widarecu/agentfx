"""User service - handles user CRUD operations and authentication dependencies.

This module provides:
- User CRUD operations (get, create, update)
- Authentication helpers (authenticate)
- Authentication dependencies (re-exported from security module for convenience)
"""

from fastapi import Depends, HTTPException, status
from jose import JWTError
from jose.exceptions import ExpiredSignatureError
from sqlalchemy.orm import Session

from .. import models, schemas
from ..core import security
from ..core.database import get_db
from ..models.user import UserRole

# =============================================================================
# User CRUD Operations
# =============================================================================


def get(db: Session, user_id: int) -> models.User | None:
    """Get a user by ID."""
    return db.query(models.User).filter(models.User.id == user_id).first()


def get_by_email(db: Session, email: str) -> models.User | None:
    """Get a user by email."""
    return db.query(models.User).filter(models.User.email == email).first()


def authenticate(db: Session, email: str, password: str) -> models.User | None:
    """Authenticate a user with email and password."""
    user = get_by_email(db, email=email)
    if not user:
        return None
    if not security.verify_password(password, user.hashed_password):
        return None
    return user


def create(db: Session, obj_in: schemas.UserCreate) -> models.User:
    """Create a new user."""
    hashed_password = security.get_password_hash(obj_in.password)
    db_user = models.User(
        email=obj_in.email,
        hashed_password=hashed_password,
        full_name=obj_in.full_name,
        is_active=obj_in.is_active,
        role=obj_in.role,
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user


def update(db: Session, db_obj: models.User, obj_in: schemas.UserUpdate) -> models.User:
    """Update a user."""
    update_data = obj_in.dict(exclude_unset=True)

    if "password" in update_data and update_data["password"]:
        hashed_password = security.get_password_hash(update_data["password"])
        del update_data["password"]
        update_data["hashed_password"] = hashed_password

    for field, value in update_data.items():
        setattr(db_obj, field, value)

    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj


# =============================================================================
# Authentication Dependencies
# These are wrappers around security.py functions for convenient use in routers.
# They maintain the same interface to avoid breaking changes.
# =============================================================================


def get_current_user(
    db: Session = Depends(get_db), token: str = Depends(security.oauth2_scheme)
) -> models.User:
    """
    Get current user from token.

    This is a synchronous wrapper that can be used as a FastAPI dependency.
    For the async version, use security.get_current_user directly.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = security.decode_token(token)
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except JWTError:
        raise credentials_exception

    user = get_by_email(db, email=username)
    if user is None:
        raise credentials_exception
    return user


def get_current_active_user(
    current_user: models.User = Depends(get_current_user),
) -> models.User:
    """Get current active user - raises 400 if user is inactive."""
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user


def get_current_admin_user(
    current_user: models.User = Depends(get_current_active_user),
) -> models.User:
    """
    Get current admin user.

    Checks that the user has ADMIN role. This replaces the old
    get_current_active_superuser which referenced a non-existent is_superuser field.
    """
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The user doesn't have enough privileges",
        )
    return current_user


# Keep old name as alias for backward compatibility
get_current_active_superuser = get_current_admin_user
