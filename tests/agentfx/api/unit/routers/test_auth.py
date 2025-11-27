"""Unit tests for the authentication router."""

from datetime import datetime
from fastapi import FastAPI, status
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from unittest.mock import MagicMock

from src.agentfx.api.routers import auth
from src.agentfx.api import models
from src.agentfx.api.core.config import settings
from src.agentfx.api.core.database import get_db
from src.agentfx.api.core.security import create_access_token
from src.agentfx.api.models.user import UserRole

# Override settings for testing
settings.TESTING = True

# Mock the database session and dependencies
db = MagicMock(spec=Session)
# Dependency overrides will be set on the test app

# Create a test app with the auth router
test_app = FastAPI()
test_app.include_router(auth.router, prefix=settings.API_V1_STR)

# Override dependencies for the test app
test_app.dependency_overrides[get_db] = lambda: db

# Create test client
client = TestClient(test_app)
API_PREFIX = settings.API_V1_STR

# Test data
TEST_USER_EMAIL = "test@example.com"
# Use a secure password that's under 72 bytes
TEST_USER_PASSWORD = "TestPass123!"
TEST_USER_FULL_NAME = "Test User"


class TestAuthRouter:
    """Test cases for the authentication router."""

    def test_login_success(self):
        """Test successful user login."""
        # Mock the database query
        mock_user = models.User(
            id=1,
            email=TEST_USER_EMAIL,
            # Argon2 hash of 'TestPass123!'
            hashed_password="$argon2id$v=19$m=65536,t=3,p=4$LwUghNA65zzH2DuHECIEwA$IyRepd8xMd5pH16X3hl3dfYplQ61DZGhcUbalKgbQEs",
            full_name=TEST_USER_FULL_NAME,
            is_active=True,
            role=UserRole.TRADER,
            created_at=datetime.utcnow(),
        )
        db.query.return_value.filter.return_value.first.return_value = mock_user

        # Test login data - must use form data, not JSON
        login_data = {
            "username": TEST_USER_EMAIL,
            "password": "TestPass123!",  # The actual password that matches the hash
        }

        # Make the request with the correct endpoint path and form data
        response = client.post(
            f"{API_PREFIX}/login",
            data=login_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        # Assert the response
        assert response.status_code == status.HTTP_200_OK, f"Response: {response.text}"
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert "user" in data
        assert data["user"]["email"] == TEST_USER_EMAIL
        assert data["user"]["full_name"] == TEST_USER_FULL_NAME

    def test_login_invalid_credentials(self):
        """Test login with invalid credentials."""
        # Mock the database query to return None (user not found)
        db.query.return_value.filter.return_value.first.return_value = None

        # Test login data with invalid credentials
        login_data = {
            "username": "nonexistent@example.com",
            "password": "wrongpassword",
            "grant_type": "password",
        }

        # Make the request with the correct endpoint path and form data
        response = client.post(
            f"{API_PREFIX}/login",
            data={
                "username": login_data["username"],
                "password": login_data["password"],
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        # Assert the response
        assert (
            response.status_code == status.HTTP_401_UNAUTHORIZED
        ), f"Response: {response.text}"
        data = response.json()
        assert "detail" in data
        assert data["detail"] == "Incorrect email or password"

    def test_register_user(self):
        """Test user registration."""
        # Mock the database query to return None (user doesn't exist)
        db.query.return_value.filter.return_value.first.return_value = None

        # Mock the database add and commit
        new_user = None

        def save_user(user):
            nonlocal new_user
            new_user = user
            user.id = 1
            user.created_at = datetime.utcnow()  # Add created_at to match the schema
            return None

        db.add.side_effect = save_user
        db.commit.return_value = None

        # Test registration data with a secure password under 72 bytes
        register_data = {
            "email": "newuser@example.com",
            "password": "NewPass123!",  # 10 chars, secure enough for testing
            "full_name": "New User",
        }

        # Make the request with the correct endpoint path
        response = client.post(f"{API_PREFIX}/register", json=register_data)

        # Assert the response
        assert response.status_code == status.HTTP_200_OK, f"Response: {response.text}"
        data = response.json()
        assert "id" in data
        assert data["email"] == register_data["email"]
        assert data["full_name"] == register_data["full_name"]
        assert "hashed_password" not in data  # Password should not be returned

    def test_register_existing_user(self):
        """Test registration with an existing email."""
        # Mock the database query to return a user (email already exists)
        existing_user = models.User(
            id=1,
            email=TEST_USER_EMAIL,
            hashed_password="hashed_password",
            full_name=TEST_USER_FULL_NAME,
            is_active=True,
            role=UserRole.TRADER,
        )
        db.query.return_value.filter.return_value.first.return_value = existing_user

        # Test registration data with existing email
        register_data = {
            "email": TEST_USER_EMAIL,
            "password": "NewPass123!",
            "full_name": "Existing User",
        }

        # Make the request with the correct endpoint path
        response = client.post(f"{API_PREFIX}/register", json=register_data)

        # Assert the response
        assert (
            response.status_code == status.HTTP_400_BAD_REQUEST
        ), f"Response: {response.text}"
        data = response.json()
        assert "detail" in data
        assert (
            "already exists" in data["detail"]
        ), f"Unexpected error message: {data['detail']}"

    def test_get_current_user(self):
        """Test getting the current user with a valid token."""
        # Create a test user with created_at set
        test_user = models.User(
            id=1,
            email=TEST_USER_EMAIL,
            hashed_password="hashed_password",
            full_name=TEST_USER_FULL_NAME,
            is_active=True,
            role=UserRole.TRADER,
            created_at=datetime.utcnow(),
        )

        # Create a valid token
        token = create_access_token(data={"sub": str(test_user.id)})

        # Mock the database query
        db.query.return_value.filter.return_value.first.return_value = test_user

        # Make the request with the correct endpoint path
        response = client.get(
            f"{API_PREFIX}/me", headers={"Authorization": f"Bearer {token}"}
        )

        # Assert the response
        assert response.status_code == status.HTTP_200_OK, f"Response: {response.text}"
        data = response.json()
        assert data["email"] == test_user.email
        assert data["full_name"] == test_user.full_name
        assert "hashed_password" not in data  # Sensitive data should not be returned

    def test_login_missing_credentials(self):
        """Test login with missing credentials."""
        # Test with missing username
        response = client.post(
            f"{API_PREFIX}/login",
            data={"password": "TestPass123!"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        # Should return 422 for validation error (missing username)
        assert (
            response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        ), f"Response: {response.text}"

        # Test with missing password
        response = client.post(
            f"{API_PREFIX}/login",
            data={"username": "test@example.com"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        # Should return 422 for validation error (missing password)
        assert (
            response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        ), f"Response: {response.text}"

        # Test with empty data
        response = client.post(f"{API_PREFIX}/login", data={"grant_type": "password"})
        assert (
            response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        ), f"Response: {response.text}"

    def test_get_current_user_invalid_token(self):
        """Test getting current user with invalid token."""
        # Test with invalid token
        response = client.get(
            f"{API_PREFIX}/me", headers={"Authorization": "Bearer invalid_token"}
        )
        assert (
            response.status_code == status.HTTP_401_UNAUTHORIZED
        ), f"Response: {response.text}"
        data = response.json()
        assert "detail" in data
        assert "Could not validate credentials" in data["detail"]

        # Test with missing Authorization header
        response = client.get(f"{API_PREFIX}/me", headers={})
        assert (
            response.status_code == status.HTTP_401_UNAUTHORIZED
        ), f"Response: {response.text}"

        # Test with invalid token format (no Bearer)
        response = client.get(
            f"{API_PREFIX}/me", headers={"Authorization": "InvalidTokenFormat"}
        )
        assert (
            response.status_code == status.HTTP_401_UNAUTHORIZED
        ), f"Response: {response.text}"

    def test_register_invalid_data(self):
        """Test user registration with invalid data."""
        # Test with invalid email
        response = client.post(
            f"{API_PREFIX}/register",
            json={
                "email": "invalid-email",
                "password": "Test123!",
                "full_name": "Test User",
            },
        )
        assert (
            response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        ), f"Response: {response.text}"

        # Test with short password
        response = client.post(
            f"{API_PREFIX}/register",
            json={
                "email": "test@example.com",
                "password": "123",
                "full_name": "Test User",
            },
        )
        assert (
            response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        ), f"Response: {response.text}"

        # Test with missing required fields
        response = client.post(
            f"{API_PREFIX}/register", json={"email": "test@example.com"}
        )
        assert (
            response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        ), f"Response: {response.text}"

    def test_test_token_endpoint(self):
        """Test the test-token endpoint."""
        # Create a test user with created_at set
        test_user = models.User(
            id=1,
            email=TEST_USER_EMAIL,
            hashed_password="hashed_password",
            full_name=TEST_USER_FULL_NAME,
            is_active=True,
            role=UserRole.TRADER,
            created_at=datetime.utcnow(),
        )

        # Create a valid token
        token = create_access_token(data={"sub": str(test_user.id)})

        # Mock the database query
        db.query.return_value.filter.return_value.first.return_value = test_user

        # Test with valid token
        response = client.post(
            f"{API_PREFIX}/test-token", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == status.HTTP_200_OK, f"Response: {response.text}"
        data = response.json()
        assert data["email"] == test_user.email
        assert "hashed_password" not in data
