"""Unit tests for security module functions."""

import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from jose import jwt

from src.agentfx.api.core.security import (
    verify_password,
    get_password_hash,
    create_access_token,
    decode_token,
    get_current_user,
    get_current_active_user,
    get_current_user_ws,
)
from src.agentfx.api.core.config import settings
from src.agentfx.api.models.user import User, UserRole


class TestPasswordFunctions:
    """Tests for password hashing and verification."""

    def test_get_password_hash_returns_hash(self):
        """Test that get_password_hash returns an Argon2 hash."""
        password = "TestPassword123!"
        hashed = get_password_hash(password)

        assert hashed is not None
        assert hashed != password
        assert hashed.startswith("$argon2")

    def test_get_password_hash_different_for_same_password(self):
        """Test that same password produces different hashes (salt)."""
        password = "TestPassword123!"
        hash1 = get_password_hash(password)
        hash2 = get_password_hash(password)

        # Different salts should produce different hashes
        assert hash1 != hash2

    def test_verify_password_correct(self):
        """Test that verify_password returns True for correct password."""
        password = "TestPassword123!"
        hashed = get_password_hash(password)

        assert verify_password(password, hashed) is True

    def test_verify_password_incorrect(self):
        """Test that verify_password returns False for wrong password."""
        password = "TestPassword123!"
        wrong_password = "WrongPassword456!"
        hashed = get_password_hash(password)

        assert verify_password(wrong_password, hashed) is False

    def test_verify_password_empty(self):
        """Test password verification with empty password."""
        hashed = get_password_hash("SomePassword123!")
        assert verify_password("", hashed) is False


class TestTokenFunctions:
    """Tests for JWT token creation and decoding."""

    def test_create_access_token_basic(self):
        """Test creating a basic access token."""
        data = {"sub": "test@example.com"}
        token = create_access_token(data)

        assert token is not None
        assert isinstance(token, str)
        assert len(token) > 0

    def test_create_access_token_with_expiry(self):
        """Test creating token with custom expiration."""
        data = {"sub": "test@example.com"}
        expires = timedelta(hours=2)
        token = create_access_token(data, expires_delta=expires)

        # Decode and check expiration
        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        assert "exp" in payload
        assert payload["sub"] == "test@example.com"

    def test_create_access_token_default_expiry(self):
        """Test that default expiry is 15 minutes when not specified."""
        data = {"sub": "test@example.com"}
        token = create_access_token(data)

        payload = jwt.decode(
            token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM]
        )
        # Check exp is roughly 15 minutes from now
        import calendar

        now_timestamp = calendar.timegm(datetime.utcnow().utctimetuple())
        expected_exp = now_timestamp + (15 * 60)  # 15 minutes

        # Allow 5 seconds tolerance
        assert abs(payload["exp"] - expected_exp) < 5

    def test_decode_token_valid(self):
        """Test decoding a valid token."""
        data = {"sub": "test@example.com"}
        token = create_access_token(data, expires_delta=timedelta(hours=1))

        payload = decode_token(token)

        assert payload["sub"] == "test@example.com"

    def test_decode_token_invalid(self):
        """Test decoding an invalid token raises JWTError."""
        from jose import JWTError

        with pytest.raises(JWTError):
            decode_token("invalid.token.here")

    def test_decode_token_tampered(self):
        """Test decoding a tampered token fails."""
        from jose import JWTError

        data = {"sub": "test@example.com"}
        token = create_access_token(data, expires_delta=timedelta(hours=1))

        # Tamper with the token
        tampered_token = token[:-5] + "xxxxx"

        with pytest.raises(JWTError):
            decode_token(tampered_token)


class TestGetCurrentUser:
    """Tests for get_current_user dependency."""

    @pytest.mark.asyncio
    async def test_get_current_user_valid_token(self):
        """Test getting current user with valid token."""
        # Create mock user
        mock_user = User(
            id=1,
            email="test@example.com",
            hashed_password="hashed",
            full_name="Test User",
            is_active=True,
            role=UserRole.TRADER,
            created_at=datetime.utcnow(),
        )

        # Create valid token
        token = create_access_token(
            data={"sub": "test@example.com"}, expires_delta=timedelta(hours=1)
        )

        # Mock database
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_user

        # Call function
        user = await get_current_user(db=mock_db, token=token)

        assert user.email == "test@example.com"
        assert user.full_name == "Test User"

    @pytest.mark.asyncio
    async def test_get_current_user_expired_token(self):
        """Test that expired token raises correct exception."""
        from fastapi import HTTPException

        # Create expired token
        token = create_access_token(
            data={"sub": "test@example.com"},
            expires_delta=timedelta(seconds=-10),  # Already expired
        )

        mock_db = MagicMock()

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(db=mock_db, token=token)

        assert exc_info.value.status_code == 401
        assert "Token has expired" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_get_current_user_invalid_token(self):
        """Test that invalid token raises credentials exception."""
        from fastapi import HTTPException

        mock_db = MagicMock()

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(db=mock_db, token="invalid_token")

        assert exc_info.value.status_code == 401
        assert "Could not validate credentials" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_get_current_user_no_subject(self):
        """Test token without subject claim fails."""
        from fastapi import HTTPException

        # Create token without 'sub'
        token = jwt.encode(
            {"exp": datetime.utcnow() + timedelta(hours=1)},
            settings.SECRET_KEY,
            algorithm=settings.ALGORITHM,
        )

        mock_db = MagicMock()

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(db=mock_db, token=token)

        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_get_current_user_user_not_found(self):
        """Test when user in token doesn't exist in database."""
        from fastapi import HTTPException

        token = create_access_token(
            data={"sub": "nonexistent@example.com"}, expires_delta=timedelta(hours=1)
        )

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            await get_current_user(db=mock_db, token=token)

        assert exc_info.value.status_code == 401


class TestGetCurrentActiveUser:
    """Tests for get_current_active_user dependency."""

    @pytest.mark.asyncio
    async def test_get_current_active_user_active(self):
        """Test that active user passes through."""
        mock_user = User(
            id=1,
            email="test@example.com",
            hashed_password="hashed",
            full_name="Test User",
            is_active=True,
            role=UserRole.TRADER,
            created_at=datetime.utcnow(),
        )

        result = await get_current_active_user(mock_user)

        assert result == mock_user

    @pytest.mark.asyncio
    async def test_get_current_active_user_inactive(self):
        """Test that inactive user raises exception."""
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
            await get_current_active_user(mock_user)

        assert exc_info.value.status_code == 400
        assert "Inactive user" in exc_info.value.detail


class TestWebSocketAuth:
    """Tests for WebSocket authentication."""

    @pytest.mark.asyncio
    async def test_get_current_user_ws_no_token(self):
        """Test WebSocket auth with no token returns None."""
        result = await get_current_user_ws(None)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_current_user_ws_empty_token(self):
        """Test WebSocket auth with empty token returns None."""
        result = await get_current_user_ws("")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_current_user_ws_valid_token(self):
        """Test WebSocket auth with valid token."""
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

        # Mock the database generator
        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_user

        with patch("src.agentfx.api.core.security.get_db") as mock_get_db:
            mock_get_db.return_value = iter([mock_db])
            result = await get_current_user_ws(token)

        assert result is not None
        assert result.email == "test@example.com"

    @pytest.mark.asyncio
    async def test_get_current_user_ws_bearer_prefix(self):
        """Test WebSocket auth strips Bearer prefix."""
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

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_user

        with patch("src.agentfx.api.core.security.get_db") as mock_get_db:
            mock_get_db.return_value = iter([mock_db])
            result = await get_current_user_ws(f"Bearer {token}")

        assert result is not None
        assert result.email == "test@example.com"

    @pytest.mark.asyncio
    async def test_get_current_user_ws_invalid_token(self):
        """Test WebSocket auth with invalid token returns None."""
        result = await get_current_user_ws("invalid_token")
        assert result is None

    @pytest.mark.asyncio
    async def test_get_current_user_ws_inactive_user(self):
        """Test WebSocket auth with inactive user returns None."""
        mock_user = User(
            id=1,
            email="test@example.com",
            hashed_password="hashed",
            full_name="Inactive User",
            is_active=False,
            role=UserRole.TRADER,
            created_at=datetime.utcnow(),
        )

        token = create_access_token(
            data={"sub": "test@example.com"}, expires_delta=timedelta(hours=1)
        )

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = mock_user

        with patch("src.agentfx.api.core.security.get_db") as mock_get_db:
            mock_get_db.return_value = iter([mock_db])
            result = await get_current_user_ws(token)

        assert result is None

    @pytest.mark.asyncio
    async def test_get_current_user_ws_user_not_found(self):
        """Test WebSocket auth when user not in database returns None."""
        token = create_access_token(
            data={"sub": "nonexistent@example.com"}, expires_delta=timedelta(hours=1)
        )

        mock_db = MagicMock()
        mock_db.query.return_value.filter.return_value.first.return_value = None

        with patch("src.agentfx.api.core.security.get_db") as mock_get_db:
            mock_get_db.return_value = iter([mock_db])
            result = await get_current_user_ws(token)

        assert result is None
