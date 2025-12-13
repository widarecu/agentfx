"""Unit tests for user service functions."""

import pytest
from datetime import datetime
from unittest.mock import MagicMock
from sqlalchemy.orm import Session

from src.agentfx.api.services import user as user_service
from src.agentfx.api import schemas
from src.agentfx.api.models.user import User, UserRole
from src.agentfx.api.core.security import get_password_hash


class TestUserServiceGet:
    """Tests for user get operations."""

    def test_get_user_by_id_found(self):
        """Test getting a user by ID when user exists."""
        mock_user = User(
            id=1,
            email="test@example.com",
            hashed_password="hashed",
            full_name="Test User",
            is_active=True,
            role=UserRole.TRADER,
            created_at=datetime.utcnow(),
        )

        mock_db = MagicMock(spec=Session)
        mock_db.query.return_value.filter.return_value.first.return_value = mock_user

        result = user_service.get(mock_db, user_id=1)

        assert result is not None
        assert result.id == 1
        assert result.email == "test@example.com"

    def test_get_user_by_id_not_found(self):
        """Test getting a user by ID when user doesn't exist."""
        mock_db = MagicMock(spec=Session)
        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = user_service.get(mock_db, user_id=999)

        assert result is None

    def test_get_user_by_email_found(self):
        """Test getting a user by email when user exists."""
        mock_user = User(
            id=1,
            email="test@example.com",
            hashed_password="hashed",
            full_name="Test User",
            is_active=True,
            role=UserRole.TRADER,
            created_at=datetime.utcnow(),
        )

        mock_db = MagicMock(spec=Session)
        mock_db.query.return_value.filter.return_value.first.return_value = mock_user

        result = user_service.get_by_email(mock_db, email="test@example.com")

        assert result is not None
        assert result.email == "test@example.com"

    def test_get_user_by_email_not_found(self):
        """Test getting a user by email when user doesn't exist."""
        mock_db = MagicMock(spec=Session)
        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = user_service.get_by_email(mock_db, email="nonexistent@example.com")

        assert result is None


class TestUserServiceAuthenticate:
    """Tests for user authentication service."""

    def test_authenticate_success(self):
        """Test successful authentication."""
        password = "TestPass123!"
        hashed = get_password_hash(password)

        mock_user = User(
            id=1,
            email="test@example.com",
            hashed_password=hashed,
            full_name="Test User",
            is_active=True,
            role=UserRole.TRADER,
            created_at=datetime.utcnow(),
        )

        mock_db = MagicMock(spec=Session)
        mock_db.query.return_value.filter.return_value.first.return_value = mock_user

        result = user_service.authenticate(
            mock_db, email="test@example.com", password=password
        )

        assert result is not None
        assert result.email == "test@example.com"

    def test_authenticate_wrong_password(self):
        """Test authentication with wrong password."""
        password = "TestPass123!"
        hashed = get_password_hash(password)

        mock_user = User(
            id=1,
            email="test@example.com",
            hashed_password=hashed,
            full_name="Test User",
            is_active=True,
            role=UserRole.TRADER,
            created_at=datetime.utcnow(),
        )

        mock_db = MagicMock(spec=Session)
        mock_db.query.return_value.filter.return_value.first.return_value = mock_user

        result = user_service.authenticate(
            mock_db, email="test@example.com", password="WrongPass!"
        )

        assert result is None

    def test_authenticate_user_not_found(self):
        """Test authentication when user doesn't exist."""
        mock_db = MagicMock(spec=Session)
        mock_db.query.return_value.filter.return_value.first.return_value = None

        result = user_service.authenticate(
            mock_db, email="nonexistent@example.com", password="anypass"
        )

        assert result is None


class TestUserServiceCreate:
    """Tests for user creation service."""

    def test_create_user_success(self):
        """Test successful user creation."""
        user_data = schemas.UserCreate(
            email="newuser@example.com",
            password="NewPass123!",
            full_name="New User",
            is_active=True,
            role=UserRole.TRADER,
        )

        mock_db = MagicMock(spec=Session)

        # Capture the user that gets added
        created_user = None

        def capture_add(user):
            nonlocal created_user
            created_user = user
            user.id = 1
            user.created_at = datetime.utcnow()

        mock_db.add.side_effect = capture_add
        mock_db.commit.return_value = None
        mock_db.refresh.return_value = None

        result = user_service.create(mock_db, obj_in=user_data)

        assert result is not None
        assert result.email == "newuser@example.com"
        assert result.full_name == "New User"
        # Verify password was hashed
        assert result.hashed_password != "NewPass123!"
        assert result.hashed_password.startswith("$argon2")

    def test_create_user_with_default_role(self):
        """Test user creation with default role."""
        user_data = schemas.UserCreate(
            email="newuser@example.com",
            password="NewPass123!",
            full_name="New User",
        )

        mock_db = MagicMock(spec=Session)

        def capture_add(user):
            user.id = 1
            user.created_at = datetime.utcnow()

        mock_db.add.side_effect = capture_add
        mock_db.commit.return_value = None
        mock_db.refresh.return_value = None

        result = user_service.create(mock_db, obj_in=user_data)

        assert result.role == UserRole.VIEWER  # Default role


class TestUserServiceUpdate:
    """Tests for user update service."""

    def test_update_user_basic_fields(self):
        """Test updating user basic fields."""
        existing_user = User(
            id=1,
            email="old@example.com",
            hashed_password="existing_hash",
            full_name="Old Name",
            is_active=True,
            role=UserRole.VIEWER,
            created_at=datetime.utcnow(),
        )

        update_data = schemas.UserUpdate(
            full_name="New Name",
            role=UserRole.TRADER,
        )

        mock_db = MagicMock(spec=Session)
        mock_db.add.return_value = None
        mock_db.commit.return_value = None
        mock_db.refresh.return_value = None

        result = user_service.update(mock_db, db_obj=existing_user, obj_in=update_data)

        assert result.full_name == "New Name"
        assert result.role == UserRole.TRADER
        # Email should remain unchanged since not in update
        assert result.email == "old@example.com"

    def test_update_user_password(self):
        """Test updating user password."""
        existing_user = User(
            id=1,
            email="test@example.com",
            hashed_password="old_hash",
            full_name="Test User",
            is_active=True,
            role=UserRole.VIEWER,
            created_at=datetime.utcnow(),
        )

        update_data = schemas.UserUpdate(
            password="NewPassword123!",
        )

        mock_db = MagicMock(spec=Session)
        mock_db.add.return_value = None
        mock_db.commit.return_value = None
        mock_db.refresh.return_value = None

        result = user_service.update(mock_db, db_obj=existing_user, obj_in=update_data)

        # Password should be hashed
        assert result.hashed_password != "NewPassword123!"
        assert result.hashed_password.startswith("$argon2")
        assert result.hashed_password != "old_hash"

    def test_update_user_email(self):
        """Test updating user email."""
        existing_user = User(
            id=1,
            email="old@example.com",
            hashed_password="existing_hash",
            full_name="Test User",
            is_active=True,
            role=UserRole.VIEWER,
            created_at=datetime.utcnow(),
        )

        update_data = schemas.UserUpdate(
            email="new@example.com",
        )

        mock_db = MagicMock(spec=Session)
        mock_db.add.return_value = None
        mock_db.commit.return_value = None
        mock_db.refresh.return_value = None

        result = user_service.update(mock_db, db_obj=existing_user, obj_in=update_data)

        assert result.email == "new@example.com"

    def test_update_user_deactivate(self):
        """Test deactivating a user."""
        existing_user = User(
            id=1,
            email="test@example.com",
            hashed_password="existing_hash",
            full_name="Test User",
            is_active=True,
            role=UserRole.VIEWER,
            created_at=datetime.utcnow(),
        )

        update_data = schemas.UserUpdate(
            is_active=False,
        )

        mock_db = MagicMock(spec=Session)
        mock_db.add.return_value = None
        mock_db.commit.return_value = None
        mock_db.refresh.return_value = None

        result = user_service.update(mock_db, db_obj=existing_user, obj_in=update_data)

        assert result.is_active is False


class TestUserServiceCurrentUser:
    """Tests for get_current_user in user service."""

    def test_get_current_user_valid(self):
        """Test getting current user with valid token."""
        from datetime import timedelta
        from src.agentfx.api.core.security import create_access_token

        mock_user = User(
            id=1,
            email="test@example.com",
            hashed_password="hashed",
            full_name="Test User",
            is_active=True,
            role=UserRole.TRADER,
            created_at=datetime.utcnow(),
        )

        token = create_access_token(
            data={"sub": "test@example.com"}, expires_delta=timedelta(hours=1)
        )

        mock_db = MagicMock(spec=Session)
        mock_db.query.return_value.filter.return_value.first.return_value = mock_user

        result = user_service.get_current_user(db=mock_db, token=token)

        assert result is not None
        assert result.email == "test@example.com"

    def test_get_current_user_expired_token(self):
        """Test getting current user with expired token."""
        from datetime import timedelta
        from fastapi import HTTPException
        from src.agentfx.api.core.security import create_access_token

        token = create_access_token(
            data={"sub": "test@example.com"},
            expires_delta=timedelta(seconds=-10),  # Already expired
        )

        mock_db = MagicMock(spec=Session)

        with pytest.raises(HTTPException) as exc_info:
            user_service.get_current_user(db=mock_db, token=token)

        assert exc_info.value.status_code == 401
        assert "Token has expired" in exc_info.value.detail

    def test_get_current_user_invalid_token(self):
        """Test getting current user with invalid token."""
        from fastapi import HTTPException

        mock_db = MagicMock(spec=Session)

        with pytest.raises(HTTPException) as exc_info:
            user_service.get_current_user(db=mock_db, token="invalid_token")

        assert exc_info.value.status_code == 401
        assert "Could not validate credentials" in exc_info.value.detail

    def test_get_current_user_user_not_found(self):
        """Test getting current user when user doesn't exist."""
        from datetime import timedelta
        from fastapi import HTTPException
        from src.agentfx.api.core.security import create_access_token

        token = create_access_token(
            data={"sub": "nonexistent@example.com"}, expires_delta=timedelta(hours=1)
        )

        mock_db = MagicMock(spec=Session)
        mock_db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            user_service.get_current_user(db=mock_db, token=token)

        assert exc_info.value.status_code == 401


class TestUserServiceActiveUser:
    """Tests for get_current_active_user in user service."""

    def test_get_current_active_user_active(self):
        """Test getting current active user when user is active."""
        mock_user = User(
            id=1,
            email="test@example.com",
            hashed_password="hashed",
            full_name="Test User",
            is_active=True,
            role=UserRole.TRADER,
            created_at=datetime.utcnow(),
        )

        result = user_service.get_current_active_user(current_user=mock_user)

        assert result == mock_user

    def test_get_current_active_user_inactive(self):
        """Test getting current active user when user is inactive."""
        from fastapi import HTTPException

        mock_user = User(
            id=1,
            email="test@example.com",
            hashed_password="hashed",
            full_name="Inactive User",
            is_active=False,
            role=UserRole.TRADER,
            created_at=datetime.utcnow(),
        )

        with pytest.raises(HTTPException) as exc_info:
            user_service.get_current_active_user(current_user=mock_user)

        assert exc_info.value.status_code == 400
        assert "Inactive user" in exc_info.value.detail
