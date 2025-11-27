"""Integration tests for the authentication flow."""

import time
import pytest
from fastapi import status
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient

from src.agentfx.api.core.config import settings
from src.agentfx.api.core.security import create_access_token
from src.agentfx.api.models.user import User, UserRole
from src.agentfx.api.core.security import get_password_hash

# Test user data
TEST_USER_EMAIL = "testuser@example.com"
TEST_USER_PASSWORD = "TestPass123!"
TEST_USER_FULL_NAME = "Test User"


@pytest.fixture
def test_user(db_session: Session) -> User:
    """Create a test user in the database."""
    # Clean up any existing test user
    db_session.query(User).filter(User.email == TEST_USER_EMAIL).delete()
    db_session.commit()

    # Create a new test user
    hashed_password = get_password_hash(TEST_USER_PASSWORD)
    user = User(
        email=TEST_USER_EMAIL,
        hashed_password=hashed_password,
        full_name=TEST_USER_FULL_NAME,
        is_active=True,
        role=UserRole.TRADER,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def test_register_user(client: TestClient, db_session: Session):
    """Test user registration with valid data."""
    # Clean up any existing test user
    db_session.query(User).filter(User.email == TEST_USER_EMAIL).delete()
    db_session.commit()

    # Test registration data
    user_data = {
        "email": TEST_USER_EMAIL,
        "password": TEST_USER_PASSWORD,
        "full_name": TEST_USER_FULL_NAME,
    }

    # Make the registration request
    response = client.post(f"{settings.API_V1_STR}/register", json=user_data)

    # Assert the response
    assert response.status_code == status.HTTP_200_OK, f"Response: {response.text}"
    data = response.json()
    assert "id" in data
    assert data["email"] == TEST_USER_EMAIL
    assert data["full_name"] == TEST_USER_FULL_NAME
    assert "hashed_password" not in data

    # Verify the user was created in the database
    user = db_session.query(User).filter(User.email == TEST_USER_EMAIL).first()
    assert user is not None
    assert user.email == TEST_USER_EMAIL
    assert user.full_name == TEST_USER_FULL_NAME
    assert user.is_active is True


def test_register_duplicate_email(client: TestClient, test_user: User):
    """Test registration with an email that's already registered."""
    user_data = {
        "email": TEST_USER_EMAIL,
        "password": "AnotherPassword123!",
        "full_name": "Another User",
    }

    response = client.post(f"{settings.API_V1_STR}/register", json=user_data)

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    data = response.json()
    assert "detail" in data
    assert "already exists" in data["detail"].lower()


def test_login_success(client: TestClient, test_user: User):
    """Test successful user login."""
    login_data = {"username": TEST_USER_EMAIL, "password": TEST_USER_PASSWORD}

    response = client.post(
        f"{settings.API_V1_STR}/login",
        data=login_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == status.HTTP_200_OK, f"Response: {response.text}"
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert "user" in data
    assert data["user"]["email"] == TEST_USER_EMAIL


def test_login_invalid_credentials(client: TestClient, test_user: User):
    """Test login with invalid credentials."""
    login_data = {"username": TEST_USER_EMAIL, "password": "wrongpassword"}

    response = client.post(
        f"{settings.API_V1_STR}/login",
        data=login_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    data = response.json()
    assert "detail" in data
    assert "incorrect email or password" in data["detail"].lower()


def test_protected_endpoint_unauthorized(client: TestClient):
    """Test accessing a protected endpoint without authentication."""
    response = client.get(f"{settings.API_V1_STR}/me")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    data = response.json()
    assert "detail" in data
    assert "not authenticated" in data["detail"].lower()


def test_protected_endpoint_authorized(client: TestClient, test_user: User):
    """Test accessing a protected endpoint with a valid token."""
    # First, log in to get a token
    login_data = {"username": TEST_USER_EMAIL, "password": TEST_USER_PASSWORD}

    login_response = client.post(
        f"{settings.API_V1_STR}/login",
        data=login_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    token = login_response.json()["access_token"]

    # Now use the token to access a protected endpoint
    response = client.get(
        f"{settings.API_V1_STR}/me", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["email"] == TEST_USER_EMAIL
    assert data["full_name"] == TEST_USER_FULL_NAME
    assert "hashed_password" not in data


def test_expired_token(client: TestClient, test_user: User):
    """Test that an expired token is rejected."""
    from datetime import datetime, timedelta, timezone
    from src.agentfx.api.core.config import settings

    print("\n=== Starting expired token test with past expiration ===")

    # Create a token that expired 1 hour ago
    expires_delta = timedelta(hours=-1)
    token_data = {
        "sub": test_user.email,
    }

    print(
        f"Creating token with expiration time: {datetime.now(timezone.utc) + expires_delta} (UTC)"
    )

    # Create the token with the custom expiration
    token = create_access_token(
        data={"sub": token_data["sub"]}, expires_delta=expires_delta
    )

    # Try to use the expired token
    print(f"Current time: {datetime.now(timezone.utc)} (UTC)")
    print("Sending request with already expired token...")

    print(f"  - Current time: {datetime.utcnow()} (UTC)")

    print("Waiting 2 seconds for token to expire...")
    time.sleep(2)
    print(f"Current time after sleep: {datetime.utcnow()} (UTC)")
    sleep_time = 2  # seconds
    print(f"\nWaiting {sleep_time} seconds for token to expire...")
    time.sleep(sleep_time)

    print(f"\nCurrent time after sleep: {datetime.utcnow()} (UTC)")
    print("Sending request with expired token...")
    response = client.get(
        f"{settings.API_V1_STR}/me", headers={"Authorization": f"Bearer {token}"}
    )

    print(f"Response status: {response.status_code}")
    print(f"Response body: {response.text}")

    # Check for 401 Unauthorized status code
    assert (
        response.status_code == status.HTTP_401_UNAUTHORIZED
    ), f"Expected 401 Unauthorized, got {response.status_code}: {response.text}"

    data = response.json()
    assert "detail" in data, f"Response missing 'detail' field: {data}"

    # Check for the specific expired token error message
    assert (
        data["detail"] == "Token has expired"
    ), f"Expected 'Token has expired' error, got: {data['detail']}"


def test_invalid_token(client: TestClient):
    """Test that an invalid token is rejected."""
    response = client.get(
        f"{settings.API_V1_STR}/me", headers={"Authorization": "Bearer invalid_token"}
    )

    assert response.status_code == status.HTTP_401_UNAUTHORIZED
    data = response.json()
    assert "detail" in data
    assert "could not validate credentials" in data["detail"].lower()
