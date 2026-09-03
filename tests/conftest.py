"""Pytest configuration and shared fixtures.

IMPORTANT: Environment variables MUST be set before any imports that load settings.
The settings singleton is created at import time in config.py.
"""

import os
import sys
from pathlib import Path

# =============================================================================
# CRITICAL: Load .env.test FIRST, before any imports that load settings.
# This ensures all environment variables (including OANDA_API_KEY) are available
# to both pydantic-settings (agentfx.api.core.config) AND direct os.environ reads
# (agentfx.shared.config).
# =============================================================================
from dotenv import load_dotenv

# Find project root and load .env.test
_project_root = Path(__file__).parent.parent
_env_test_path = _project_root / ".env.test"
if _env_test_path.exists():
    load_dotenv(_env_test_path, override=True)

# Force set test environment after loading .env.test
_prev_env = os.environ.get("ENVIRONMENT")
os.environ["ENVIRONMENT"] = "test"
os.environ["TESTING"] = "true"

# Test-correctness invariant: ingestion worker MUST be off during pytest.
# When enabled, `TestClient(app)` triggers FastAPI lifespan startup → OANDA
# backfill → 60s teardown timeouts that hang the test session. This is
# enforced in code (not in .env.test) so a developer can't accidentally
# flip it on for the test path while keeping Docker behaviour separate.
# See CLAUDE.md → "Ingestion toggle: three-surface model".
os.environ["INGESTION_ENABLED"] = "false"

# Test-correctness invariant: outbound email notifications MUST be off
# during pytest. `post_paper_run_alert` short-circuits before any
# `smtplib.SMTP` connection when this is False, so no test can leak
# real email via a mocked-but-forgotten path or an integration test
# that exercises the runner's terminal-state code. Individual
# notification tests still enable it explicitly via monkeypatch when
# they need to assert the send path. See `agentfx.notifications.email`.
os.environ["NOTIFICATIONS_ENABLED"] = "false"

# Warn if we had to override an incorrect environment setting
# This helps developers identify when they're not using the correct test command
if _prev_env and _prev_env != "test":
    import warnings

    warnings.warn(
        f"Test environment forced from '{_prev_env}' to 'test'. "
        "Use 'make test-unit' or 'make test-integration' to avoid this warning. "
        "See CLAUDE.md for canonical test commands.",
        UserWarning,
        stacklevel=1,
    )

# Add src to Python path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# Now safe to import modules that depend on settings
import asyncio  # noqa: E402
from collections.abc import Generator  # noqa: E402
from unittest.mock import MagicMock  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from src.agentfx.api import models  # noqa: E402
from src.agentfx.api.core.database import get_db  # noqa: E402
from src.agentfx.api.main import app  # noqa: E402
from src.agentfx.api.models.user import UserRole  # noqa: E402


@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_db() -> Generator[Session, None, None]:
    """Create a mock database session."""
    mock = MagicMock(spec=Session)
    yield mock


@pytest.fixture
def test_user() -> models.User:
    """Create a test user fixture.

    Uses VIEWER role to align with seed_test_data.py and follow least-privilege.
    For tests requiring elevated permissions, use test_admin_user or create
    a specific fixture with the needed role.
    """
    return models.User(
        id=1,
        email="test@example.com",
        hashed_password="hashed_password",
        is_active=True,
        role=UserRole.VIEWER,
    )


@pytest.fixture
def test_admin_user() -> models.User:
    """Create a test admin user fixture."""
    return models.User(
        id=2,
        email="admin@example.com",
        hashed_password="hashed_password",
        is_active=True,
        role=UserRole.ADMIN,
    )


@pytest.fixture
def test_client(mock_db) -> Generator[TestClient, None, None]:
    """Create a test client with mocked database."""
    app.dependency_overrides[get_db] = lambda: mock_db
    client = TestClient(app)
    yield client
    # Clean up
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def sample_candle_data() -> list:
    """Sample candle data for testing."""
    return [
        {
            "timestamp": "2023-01-01T12:00:00Z",
            "open": 1.1000,
            "high": 1.1005,
            "low": 1.0995,
            "close": 1.1002,
            "volume": 1000,
            "exchange": "oanda",
            "instrument": "EUR_USD",
            "timeframe": "M1",
        },
        {
            "timestamp": "2023-01-01T12:01:00Z",
            "open": 1.1002,
            "high": 1.1008,
            "low": 1.1000,
            "close": 1.1006,
            "volume": 1200,
            "exchange": "oanda",
            "instrument": "EUR_USD",
            "timeframe": "M1",
        },
    ]


@pytest.fixture
def sample_instrument_data() -> list:
    """Sample instrument data for testing (legacy format for API compatibility)."""
    return [
        {
            "symbol": "EUR_USD",
            "name": "Euro/US Dollar",
            "exchange": "oanda",
            "asset_class": "forex",
            "enabled": True,
        },
        {
            "symbol": "GBP_USD",
            "name": "British Pound/US Dollar",
            "exchange": "oanda",
            "asset_class": "forex",
            "enabled": True,
        },
    ]


@pytest.fixture
def sample_instrument_model_data() -> list:
    """Sample instrument data matching the Instrument model schema."""
    return [
        {"symbol": "EUR_USD", "display_name": "EUR/USD", "pip_location": -4},
        {"symbol": "GBP_USD", "display_name": "GBP/USD", "pip_location": -4},
        {"symbol": "USD_JPY", "display_name": "USD/JPY", "pip_location": -2},
        {"symbol": "AUD_USD", "display_name": "AUD/USD", "pip_location": -4},
        {"symbol": "USD_CHF", "display_name": "USD/CHF", "pip_location": -4},
        {"symbol": "USD_CAD", "display_name": "USD/CAD", "pip_location": -4},
    ]


@pytest.fixture
def mock_instrument():
    """Create a mock Instrument model instance."""
    mock = MagicMock()
    mock.id = 1
    mock.symbol = "EUR_USD"
    mock.display_name = "EUR/USD"
    mock.instrument_type = "CURRENCY"
    mock.pip_location = -4
    mock.trade_units_precision = 0
    mock.minimum_trade_size = 1.0
    mock.maximum_trade_size = 10000000.0
    mock.is_tradeable = True
    mock.is_active = True
    return mock


@pytest.fixture
def mock_websocket():
    """Create a mock WebSocket for testing."""
    mock = MagicMock()
    mock.accept = MagicMock()
    mock.send_text = MagicMock()
    mock.send_json = MagicMock()
    mock.close = MagicMock()
    mock.receive_json = MagicMock()
    mock.receive_text = MagicMock()
    return mock


@pytest.fixture
def mock_market_data_stream():
    """Create a mock market data stream."""
    mock = MagicMock()
    mock.connect = MagicMock()
    mock.disconnect = MagicMock()
    mock.subscribe = MagicMock()
    mock.unsubscribe = MagicMock()
    mock.active = False
    mock.subscribed_instruments = set()
    return mock


# Test markers as defined in TEST_STRATEGY.md
def pytest_configure(config):
    """Configure pytest markers."""
    config.addinivalue_line("markers", "unit: Unit tests")
    config.addinivalue_line("markers", "integration: Integration tests")
    config.addinivalue_line("markers", "e2e: End-to-end tests")
    config.addinivalue_line("markers", "performance: Performance tests")
    config.addinivalue_line("markers", "slow: Slow running tests")
    config.addinivalue_line("markers", "auth: Authentication tests")
    config.addinivalue_line("markers", "market_data: Market data tests")
    config.addinivalue_line("markers", "strategies: Strategy tests")
    config.addinivalue_line("markers", "backtest: Backtest tests")
    config.addinivalue_line("markers", "ingestion: Candle ingestion worker tests")
    config.addinivalue_line(
        "markers", "oanda_real: Tests requiring real OANDA API connection"
    )


# Test collection hooks
def pytest_collection_modifyitems(config, items):
    """Modify test collection to add markers based on test location."""
    for item in items:
        # Add markers based on file path
        if "unit/" in str(item.fspath):
            item.add_marker(pytest.mark.unit)
        elif "integration/" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
        elif "e2e/" in str(item.fspath):
            item.add_marker(pytest.mark.e2e)
        elif "performance/" in str(item.fspath):
            item.add_marker(pytest.mark.performance)

        # Add markers based on test module
        if "auth" in str(item.fspath):
            item.add_marker(pytest.mark.auth)
        elif "market_data" in str(item.fspath):
            item.add_marker(pytest.mark.market_data)
        elif "strategies" in str(item.fspath):
            item.add_marker(pytest.mark.strategies)
        elif "backtest" in str(item.fspath):
            item.add_marker(pytest.mark.backtest)


# Environment-specific test configuration
@pytest.fixture(scope="session", autouse=True)
def test_environment():
    """Verify test environment is properly configured.

    Environment variables (ENVIRONMENT=test, TESTING=true) are now set at the
    TOP of this file BEFORE any imports. This ensures config.py loads .env.test
    when the settings singleton is created.

    Database Configuration:
    - Unit tests: Use mocked database (no real DB connection needed)
    - Integration tests: Use PostgreSQL via settings.TEST_DATABASE_URL
    """
    # Verify environment is correctly set (should already be done at module level)
    assert os.environ.get("ENVIRONMENT") == "test", (
        "ENVIRONMENT must be 'test' - check conftest.py module-level setup"
    )
    assert os.environ.get("TESTING") == "true", (
        "TESTING must be 'true' - check conftest.py module-level setup"
    )

    yield

    # No cleanup needed - environment variables persist for entire test session


# Cleanup fixtures
@pytest.fixture(autouse=True)
def cleanup_test_data():
    """Automatically clean up test data after each test."""
    yield
    # Clean up any temporary files or data created during tests
    temp_files = [
        "./test_agentfx.db",
        "./test_*.log",
    ]

    import glob

    for pattern in temp_files:
        for file in glob.glob(pattern):
            try:
                os.remove(file)
            except OSError:
                pass
