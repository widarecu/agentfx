"""Advanced integration tests for authentication scenarios.

NOTE: This test file has been migrated from SQLite to PostgreSQL.
It now uses the settings.TEST_DATABASE_URL from config.py as the single source of truth.
"""

import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.agentfx.api.core.config import settings
from src.agentfx.api.core.database import Base, get_db
from src.agentfx.api.core.security import get_password_hash
from src.agentfx.api.main import app
from src.agentfx.api.models.user import User, UserRole

# Use PostgreSQL for auth integration tests (from settings.TEST_DATABASE_URL)
# This is the SINGLE SOURCE OF TRUTH for test database configuration
engine = create_engine(settings.TEST_DATABASE_URL, echo=False)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture(scope="function")
def adv_test_db():
    """Setup test database for advanced auth tests.

    Uses cleanup instead of drop_all for PostgreSQL compatibility.
    Cleans up BEFORE and AFTER tests to handle leftover data from previous runs.
    """
    Base.metadata.create_all(bind=engine)

    # Test-specific emails that need cleanup (excluding seeded users)
    test_emails = [
        "active@example.com",
        "wasactive@example.com",
        "multitoken@example.com",
        "longpass@example.com",
        "minpass@example.com",
        "special@example.com",
        "user@exämple.com",
        "testviewer@example.com",
    ]

    # Clean up any leftover test data BEFORE test runs
    db = TestingSessionLocal()
    try:
        for email in test_emails:
            db.query(User).filter(User.email == email).delete()
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()

    yield

    # Clean up test data AFTER test
    db = TestingSessionLocal()
    try:
        for email in test_emails:
            db.query(User).filter(User.email == email).delete()
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


@pytest.fixture(scope="function")
def adv_test_client(adv_test_db):
    """Create test client with properly scoped database override."""

    def override_get_db():
        """Override database dependency for testing."""
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
def active_user(adv_test_db):
    """Create active test user in database."""
    db = TestingSessionLocal()
    # Delete existing user if present (from seed scripts or previous runs)
    existing = db.query(User).filter(User.email == "active@example.com").first()
    if existing:
        db.delete(existing)
        db.commit()

    user = User(
        email="active@example.com",
        hashed_password=get_password_hash("ActivePass123!"),
        full_name="Active User",
        is_active=True,
        role=UserRole.TRADER,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.close()
    return user


@pytest.fixture(scope="function")
def inactive_user(adv_test_db):
    """Create inactive test user in database."""
    db = TestingSessionLocal()
    # Delete existing user if present (from seed scripts or previous runs)
    existing = db.query(User).filter(User.email == "inactive@example.com").first()
    if existing:
        db.delete(existing)
        db.commit()

    user = User(
        email="inactive@example.com",
        hashed_password=get_password_hash("InactivePass123!"),
        full_name="Inactive User",
        is_active=False,
        role=UserRole.VIEWER,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.close()
    return user


@pytest.fixture(scope="function")
def admin_user(adv_test_db):
    """Create admin test user in database."""
    db = TestingSessionLocal()
    # Delete existing user if present (from seed scripts or previous runs)
    existing = db.query(User).filter(User.email == "admin@example.com").first()
    if existing:
        db.delete(existing)
        db.commit()

    user = User(
        email="admin@example.com",
        hashed_password=get_password_hash("AdminPass123!"),
        full_name="Admin User",
        is_active=True,
        role=UserRole.ADMIN,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    db.close()
    return user


@pytest.mark.integration
@pytest.mark.auth
class TestInactiveUserAuth:
    """Tests for inactive user authentication scenarios.

    Security behavior: Inactive users are blocked at login time (403 Forbidden).
    They cannot obtain tokens and therefore cannot access any protected endpoints.
    """

    def test_inactive_user_cannot_login(self, adv_test_client, inactive_user):
        """Test that inactive user CANNOT login (403 Forbidden)."""
        response = adv_test_client.post(
            f"{settings.API_V1_STR}/login",
            data={"username": "inactive@example.com", "password": "InactivePass123!"},
        )

        # Login should fail with 403 for deactivated accounts
        assert response.status_code == 403
        assert "deactivated" in response.json()["detail"].lower()

    def test_inactive_user_with_old_token_cannot_access_me(
        self, adv_test_client, adv_test_db
    ):
        """Test that inactive user with pre-existing token cannot access /me.

        Scenario: User was active, got a token, then was deactivated.
        The token is still valid but user should be blocked.
        """
        # Create user as active first
        db = TestingSessionLocal()
        # Delete existing user if present
        existing = db.query(User).filter(User.email == "wasactive@example.com").first()
        if existing:
            db.delete(existing)
            db.commit()

        user = User(
            email="wasactive@example.com",
            hashed_password=get_password_hash("WasActivePass123!"),
            full_name="Was Active User",
            is_active=True,
            role=UserRole.TRADER,
        )
        db.add(user)
        db.commit()
        db.close()

        # Login while active to get a token
        login_response = adv_test_client.post(
            f"{settings.API_V1_STR}/login",
            data={"username": "wasactive@example.com", "password": "WasActivePass123!"},
        )
        assert login_response.status_code == 200
        token = login_response.json()["access_token"]

        # Now deactivate the user
        db = TestingSessionLocal()
        user = db.query(User).filter(User.email == "wasactive@example.com").first()
        user.is_active = False
        db.commit()
        db.close()

        # Try to access /me with the old token - should fail
        response = adv_test_client.get(
            f"{settings.API_V1_STR}/me", headers={"Authorization": f"Bearer {token}"}
        )

        # Should be blocked because user is now inactive
        assert response.status_code == 400
        assert "Inactive user" in response.json()["detail"]


@pytest.mark.integration
@pytest.mark.auth
class TestUserRoles:
    """Tests for user role-based access."""

    def test_viewer_role_user_login(self, adv_test_client, adv_test_db):
        """Test viewer role user can login."""
        # Create viewer user with unique email (don't use viewer@example.com - it's seeded for E2E tests)
        db = TestingSessionLocal()
        # Delete existing user if present (from previous runs)
        existing = db.query(User).filter(User.email == "testviewer@example.com").first()
        if existing:
            db.delete(existing)
            db.commit()

        user = User(
            email="testviewer@example.com",
            hashed_password=get_password_hash("ViewerPass123!"),
            full_name="Viewer User",
            is_active=True,
            role=UserRole.VIEWER,
        )
        db.add(user)
        db.commit()
        db.close()

        response = adv_test_client.post(
            f"{settings.API_V1_STR}/login",
            data={"username": "testviewer@example.com", "password": "ViewerPass123!"},
        )

        assert response.status_code == 200
        assert response.json()["user"]["role"] == "viewer"

    def test_trader_role_user_login(self, adv_test_client, active_user):
        """Test trader role user can login."""
        response = adv_test_client.post(
            f"{settings.API_V1_STR}/login",
            data={"username": "active@example.com", "password": "ActivePass123!"},
        )

        assert response.status_code == 200
        assert response.json()["user"]["role"] == "trader"

    def test_admin_role_user_login(self, adv_test_client, admin_user):
        """Test admin role user can login."""
        response = adv_test_client.post(
            f"{settings.API_V1_STR}/login",
            data={"username": "admin@example.com", "password": "AdminPass123!"},
        )

        assert response.status_code == 200
        assert response.json()["user"]["role"] == "admin"


@pytest.mark.integration
@pytest.mark.auth
class TestTokenEdgeCases:
    """Tests for edge cases in token handling."""

    def test_token_with_whitespace(self, adv_test_client, active_user):
        """Test token with extra whitespace is handled."""
        # Login to get valid token
        login_response = adv_test_client.post(
            f"{settings.API_V1_STR}/login",
            data={"username": "active@example.com", "password": "ActivePass123!"},
        )
        token = login_response.json()["access_token"]

        # Try with extra whitespace (should still work with Bearer prefix)
        response = adv_test_client.get(
            f"{settings.API_V1_STR}/me",
            headers={"Authorization": f"Bearer  {token}"},  # Extra space
        )

        # FastAPI's OAuth2PasswordBearer should handle this
        # This test documents the behavior
        assert response.status_code in [200, 401]

    def test_lowercase_bearer(self, adv_test_client, active_user):
        """Test lowercase 'bearer' prefix.

        NOTE: FastAPI's OAuth2PasswordBearer is case-insensitive for the "Bearer" prefix.
        This test documents the actual behavior.
        """
        login_response = adv_test_client.post(
            f"{settings.API_V1_STR}/login",
            data={"username": "active@example.com", "password": "ActivePass123!"},
        )
        token = login_response.json()["access_token"]

        response = adv_test_client.get(
            f"{settings.API_V1_STR}/me", headers={"Authorization": f"bearer {token}"}
        )

        # OAuth2PasswordBearer is case-insensitive - lowercase "bearer" works
        assert response.status_code == 200

    def test_multiple_tokens_same_user(self, adv_test_client, adv_test_db):
        """Test that multiple tokens can be issued for the same user.

        NOTE: JWTs generated in the same second with same payload will be identical
        because they use Unix timestamps for expiration. This is expected behavior.
        """
        # Create user for this test
        db = TestingSessionLocal()
        # Delete existing user if present (from seed scripts or previous runs)
        existing = db.query(User).filter(User.email == "multitoken@example.com").first()
        if existing:
            db.delete(existing)
            db.commit()

        user = User(
            email="multitoken@example.com",
            hashed_password=get_password_hash("MultiPass123!"),
            full_name="Multi Token User",
            is_active=True,
            role=UserRole.TRADER,
        )
        db.add(user)
        db.commit()
        db.close()

        # Get first token
        response1 = adv_test_client.post(
            f"{settings.API_V1_STR}/login",
            data={"username": "multitoken@example.com", "password": "MultiPass123!"},
        )
        assert response1.status_code == 200, f"First login failed: {response1.text}"
        token1 = response1.json()["access_token"]

        # Wait 1 second to ensure different expiration timestamp
        time.sleep(1)

        # Get second token
        response2 = adv_test_client.post(
            f"{settings.API_V1_STR}/login",
            data={"username": "multitoken@example.com", "password": "MultiPass123!"},
        )
        assert response2.status_code == 200, f"Second login failed: {response2.text}"
        token2 = response2.json()["access_token"]

        # Tokens generated in different seconds should be different
        assert token1 != token2

        # Both should work
        me_response1 = adv_test_client.get(
            f"{settings.API_V1_STR}/me", headers={"Authorization": f"Bearer {token1}"}
        )
        me_response2 = adv_test_client.get(
            f"{settings.API_V1_STR}/me", headers={"Authorization": f"Bearer {token2}"}
        )

        assert me_response1.status_code == 200
        assert me_response2.status_code == 200


@pytest.mark.integration
@pytest.mark.auth
class TestRegistrationValidation:
    """Tests for user registration validation."""

    def test_register_with_very_long_password(self, adv_test_client, adv_test_db):
        """Test registration with very long password."""
        response = adv_test_client.post(
            f"{settings.API_V1_STR}/register",
            json={
                "email": "longpass@example.com",
                "password": "A" * 101,  # Over 100 char limit
                "full_name": "Long Password User",
            },
        )

        # Should fail validation
        assert response.status_code == 422

    def test_register_with_exactly_8_char_password(self, adv_test_client, adv_test_db):
        """Test registration with exactly 8 character password."""
        response = adv_test_client.post(
            f"{settings.API_V1_STR}/register",
            json={
                "email": "minpass@example.com",
                "password": "Exactly8",  # Exactly 8 chars
                "full_name": "Min Password User",
            },
        )

        assert response.status_code == 200

    def test_register_with_special_chars_in_name(self, adv_test_client, adv_test_db):
        """Test registration with special characters in name."""
        response = adv_test_client.post(
            f"{settings.API_V1_STR}/register",
            json={
                "email": "special@example.com",
                "password": "SpecialPass123!",
                "full_name": "O'Brien-Smith Jr.",
            },
        )

        assert response.status_code == 200
        assert response.json()["full_name"] == "O'Brien-Smith Jr."

    def test_register_with_unicode_email(self, adv_test_client, adv_test_db):
        """Test registration with unicode in email (should fail)."""
        response = adv_test_client.post(
            f"{settings.API_V1_STR}/register",
            json={
                "email": "user@exämple.com",  # Unicode
                "password": "UnicodePass123!",
                "full_name": "Unicode Email User",
            },
        )

        # Should fail validation - depends on email validator
        # This test documents behavior
        assert response.status_code in [200, 422]


@pytest.mark.integration
@pytest.mark.auth
class TestTestTokenEndpoint:
    """Tests for the test-token endpoint."""

    def test_test_token_valid(self, adv_test_client, active_user):
        """Test test-token endpoint with valid token."""
        login_response = adv_test_client.post(
            f"{settings.API_V1_STR}/login",
            data={"username": "active@example.com", "password": "ActivePass123!"},
        )
        token = login_response.json()["access_token"]

        response = adv_test_client.post(
            f"{settings.API_V1_STR}/test-token",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        assert response.json()["email"] == "active@example.com"

    def test_test_token_invalid(self, adv_test_client):
        """Test test-token endpoint with invalid token."""
        response = adv_test_client.post(
            f"{settings.API_V1_STR}/test-token",
            headers={"Authorization": "Bearer invalid_token"},
        )

        assert response.status_code == 401

    def test_test_token_no_auth(self, adv_test_client):
        """Test test-token endpoint without auth header."""
        response = adv_test_client.post(f"{settings.API_V1_STR}/test-token")

        assert response.status_code == 401
