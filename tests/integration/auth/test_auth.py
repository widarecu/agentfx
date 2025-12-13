"""Integration tests for JWT authentication using real FastAPI app.

NOTE: This test file has been migrated from SQLite to PostgreSQL.
It now uses the settings.TEST_DATABASE_URL from config.py as the single source of truth.
"""

import pytest
import time
from datetime import timedelta
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.agentfx.api.main import app
from src.agentfx.api.core.database import get_db, Base
from src.agentfx.api.core.config import settings
from src.agentfx.api.models.user import User, UserRole
from src.agentfx.api.core.security import get_password_hash, create_access_token

# Use PostgreSQL for auth integration tests (from settings.TEST_DATABASE_URL)
# This is the SINGLE SOURCE OF TRUTH for test database configuration
engine = create_engine(settings.TEST_DATABASE_URL, echo=False)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Test data
TEST_USER = {
    "email": "test@example.com",
    "password": "TestPass123!",
    "full_name": "Test User",
}


@pytest.fixture(scope="function")
def auth_test_db():
    """Setup test database for auth integration tests.

    Uses transaction-based cleanup for test isolation.
    """
    # Ensure tables exist
    Base.metadata.create_all(bind=engine)

    yield

    # Clean up test data (delete users created during tests)
    db = TestingSessionLocal()
    try:
        db.query(User).delete()
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


@pytest.fixture(scope="function")
def auth_test_client(auth_test_db):
    """Create test client with properly scoped database override for auth tests."""

    def override_get_db():
        try:
            db = TestingSessionLocal()
            yield db
        finally:
            db.close()

    # Set override within fixture scope
    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    yield client
    # Clean up only our override
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture(scope="function")
def auth_test_user(auth_test_db):
    """Create test user in database for auth tests.

    Handles existing users (from seeding scripts) by deleting first.
    """
    db = TestingSessionLocal()
    # Delete existing user if present (from seed scripts or previous runs)
    existing = db.query(User).filter(User.email == TEST_USER["email"]).first()
    if existing:
        db.delete(existing)
        db.commit()

    user = User(
        email=TEST_USER["email"],
        hashed_password=get_password_hash(TEST_USER["password"]),
        full_name=TEST_USER["full_name"],
        is_active=True,
        role=UserRole.VIEWER,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.close()
    return user


@pytest.mark.integration
@pytest.mark.auth
def test_auth_login_success(auth_test_client, auth_test_user):
    """Test successful login with JWT token."""
    # Login with correct credentials
    response = auth_test_client.post(
        f"{settings.API_V1_STR}/login",
        data={"username": TEST_USER["email"], "password": TEST_USER["password"]},
    )

    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["user"]["email"] == TEST_USER["email"]
    assert data["user"]["full_name"] == TEST_USER["full_name"]
    assert "hashed_password" not in data["user"]


@pytest.mark.integration
@pytest.mark.auth
def test_auth_login_failure(auth_test_client):
    """Test login failure with invalid credentials."""
    # Login with wrong password
    response = auth_test_client.post(
        f"{settings.API_V1_STR}/login",
        data={"username": TEST_USER["email"], "password": "wrongpassword"},
    )

    assert response.status_code == 401
    assert "detail" in response.json()


@pytest.mark.integration
@pytest.mark.auth
def test_auth_me_with_valid_token(auth_test_client, auth_test_user):
    """Test getting current user with valid JWT token."""
    # First login to get token
    login_response = auth_test_client.post(
        f"{settings.API_V1_STR}/login",
        data={"username": TEST_USER["email"], "password": TEST_USER["password"]},
    )
    token = login_response.json()["access_token"]

    # Use token to get current user
    response = auth_test_client.get(
        f"{settings.API_V1_STR}/me", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["email"] == TEST_USER["email"]
    assert data["full_name"] == TEST_USER["full_name"]
    assert "hashed_password" not in data


@pytest.mark.integration
@pytest.mark.auth
def test_auth_me_without_token(auth_test_client):
    """Test getting current user without token should fail."""
    response = auth_test_client.get(f"{settings.API_V1_STR}/me")
    assert response.status_code == 401


@pytest.mark.integration
@pytest.mark.auth
def test_auth_me_with_invalid_token(auth_test_client):
    """Test getting current user with invalid token should fail."""
    response = auth_test_client.get(
        f"{settings.API_V1_STR}/me", headers={"Authorization": "Bearer invalid_token"}
    )
    assert response.status_code == 401


@pytest.mark.integration
@pytest.mark.auth
def test_jwt_token_expiration(auth_test_client, auth_test_user):
    """Test JWT token expiration behavior."""
    # Create token with very short expiration (1 second) using email as subject
    short_lived_token = create_access_token(
        data={"sub": auth_test_user.email}, expires_delta=timedelta(seconds=1)
    )

    # Token should work immediately
    response = auth_test_client.get(
        f"{settings.API_V1_STR}/me",
        headers={"Authorization": f"Bearer {short_lived_token}"},
    )

    assert response.status_code == 200

    # Wait for token to expire
    time.sleep(2)

    # Same token should now fail
    response = auth_test_client.get(
        f"{settings.API_V1_STR}/me",
        headers={"Authorization": f"Bearer {short_lived_token}"},
    )
    assert response.status_code == 401
    assert "Token has expired" in response.json()["detail"]


@pytest.mark.integration
@pytest.mark.auth
def test_jwt_token_long_expiration(auth_test_client, auth_test_user):
    """Test JWT token with longer expiration works correctly."""
    # Create token with longer expiration (1 hour) using email as subject
    long_lived_token = create_access_token(
        data={"sub": auth_test_user.email}, expires_delta=timedelta(hours=1)
    )

    # Token should work
    response = auth_test_client.get(
        f"{settings.API_V1_STR}/me",
        headers={"Authorization": f"Bearer {long_lived_token}"},
    )
    assert response.status_code == 200

    # Should still work after a short wait
    time.sleep(1)
    response = auth_test_client.get(
        f"{settings.API_V1_STR}/me",
        headers={"Authorization": f"Bearer {long_lived_token}"},
    )
    assert response.status_code == 200


@pytest.mark.integration
@pytest.mark.auth
def test_auth_register_new_user(auth_test_client):
    """Test user registration endpoint."""
    new_user_data = {
        "email": "newuser@example.com",
        "password": "NewPass123!",
        "full_name": "New User",
    }

    # Register new user
    response = auth_test_client.post(
        f"{settings.API_V1_STR}/register", json=new_user_data
    )

    assert response.status_code == 200
    data = response.json()
    assert data["email"] == new_user_data["email"]
    assert data["full_name"] == new_user_data["full_name"]
    assert "hashed_password" not in data


@pytest.mark.integration
@pytest.mark.auth
def test_auth_register_duplicate_email(auth_test_client, auth_test_user):
    """Test registration with duplicate email should fail."""
    response = auth_test_client.post(
        f"{settings.API_V1_STR}/register",
        json={
            "email": TEST_USER["email"],  # Already exists
            "password": "AnotherPass123!",
            "full_name": "Another User",
        },
    )

    assert response.status_code == 400
    assert "already exists" in response.json()["detail"].lower()


@pytest.mark.integration
@pytest.mark.auth
def test_full_auth_flow(auth_test_client, auth_test_db):
    """Test complete authentication flow: register -> login -> access."""
    # 1. Register new user
    register_data = {
        "email": "flowtest@example.com",
        "password": "FlowPass123!",
        "full_name": "Flow Test User",
    }

    register_response = auth_test_client.post(
        f"{settings.API_V1_STR}/register", json=register_data
    )
    assert register_response.status_code == 200

    # 2. Login to get token
    login_response = auth_test_client.post(
        f"{settings.API_V1_STR}/login",
        data={
            "username": register_data["email"],
            "password": register_data["password"],
        },
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # 3. Access protected endpoint with token
    me_response = auth_test_client.get(
        f"{settings.API_V1_STR}/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert me_response.status_code == 200
    assert me_response.json()["email"] == register_data["email"]

    # 4. Login again (should work)
    login_response2 = auth_test_client.post(
        f"{settings.API_V1_STR}/login",
        data={
            "username": register_data["email"],
            "password": register_data["password"],
        },
    )
    assert login_response2.status_code == 200
    new_token = login_response2.json()["access_token"]

    # 5. Access with new token
    me_response2 = auth_test_client.get(
        f"{settings.API_V1_STR}/me", headers={"Authorization": f"Bearer {new_token}"}
    )
    assert me_response2.status_code == 200
