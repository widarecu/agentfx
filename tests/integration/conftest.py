"""Test database configuration for integration tests.

IMPORTANT: This module uses settings from config.py via settings.TEST_DATABASE_URL
as the single source of truth for database configuration. No hardcoded credentials.

Environment Setup:
- ENVIRONMENT=test is set in tests/conftest.py before any imports
- This ensures config.py loads .env.test which contains the correct DATABASE_URL
"""

import os
import sys

# Ensure environment is set (should already be done by root conftest.py)
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("TESTING", "true")

# Add src to path if not already
if os.path.join(os.path.dirname(__file__), "..", "..", "src") not in sys.path:
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from collections.abc import Generator
from datetime import UTC
from urllib.parse import urlparse

import psycopg2
import pytest
from psycopg2.extras import RealDictCursor

# Import settings AFTER environment is set
from src.agentfx.api.core.config import settings


def parse_database_url(url: str) -> dict:
    """Parse a PostgreSQL connection URL into psycopg2 connection params."""
    parsed = urlparse(url)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "database": parsed.path.lstrip("/") if parsed.path else "agentfx_test",
        "user": parsed.username or "test_user",
        "password": parsed.password or "test_password",
    }


# Derive test database settings from settings.TEST_DATABASE_URL
# This is the SINGLE SOURCE OF TRUTH for database configuration
TEST_DB_SETTINGS = parse_database_url(settings.TEST_DATABASE_URL)


class TestDatabaseManager:
    """Manages test database connections and setup."""

    def __init__(self):
        self.settings = TEST_DB_SETTINGS
        self.connection = None

    def start_test_db(self):
        """Start the test database using Docker Compose."""
        import subprocess

        try:
            result = subprocess.run(
                ["docker-compose", "-f", "docker-compose.test.yml", "up", "-d"],
                capture_output=True,
                text=True,
                cwd=os.getcwd(),
            )
            if result.returncode != 0:
                raise Exception(f"Failed to start test database: {result.stderr}")
            return True
        except Exception as e:
            print(f"Error starting test database: {e}")
            return False

    def stop_test_db(self):
        """Stop the test database."""
        import subprocess

        try:
            subprocess.run(
                ["docker-compose", "-f", "docker-compose.test.yml", "down", "-v"],
                capture_output=True,
                text=True,
                cwd=os.getcwd(),
            )
            return True
        except Exception as e:
            print(f"Error stopping test database: {e}")
            return False

    def wait_for_db(self, max_attempts: int = 30) -> bool:
        """Wait for database to be ready."""
        import time

        for attempt in range(max_attempts):
            try:
                conn = psycopg2.connect(**self.settings)
                conn.close()
                return True
            except Exception:
                time.sleep(1)
        return False

    def get_connection(self):
        """Get a database connection."""
        if not self.connection:
            self.connection = psycopg2.connect(
                **self.settings, cursor_factory=RealDictCursor
            )
        return self.connection

    def cleanup_test_data(self):
        """Clean up test data between tests.

        NOTE: Candles and instruments are PRESERVED.
        - Candles contain historical market data from OANDA needed for backtesting
        - Instruments contain metadata seeded once at startup

        Only test-created transactional data (orders, trades, runs, etc.) is cleaned.
        """
        conn = self.get_connection()

        # Rollback any aborted transactions first
        try:
            conn.rollback()
        except Exception:
            pass

        cursor = conn.cursor()

        try:
            # Delete from tables in dependency order (children first, parents last)
            # EXCLUDES: candles (historical data), instruments (metadata)
            tables_in_order = [
                "orders",
                "trades",
                "runs",
                "strategies",
                "market_data_updates",
            ]

            print(
                f"Cleaning up tables (preserving candles/instruments): {tables_in_order}"
            )

            for table in tables_in_order:
                try:
                    cursor.execute(f"DELETE FROM {table}")
                    print(f"Deleted from {table}: {cursor.rowcount} rows")
                except Exception as e:
                    # Table might not exist or have different constraints
                    print(f"Skipping {table}: {e}")
                    conn.rollback()

            # Clean up only test-created users (preserve seeded users)
            try:
                cursor.execute("DELETE FROM users WHERE email LIKE 'integration_%'")
                print(f"Deleted integration test users: {cursor.rowcount} rows")
            except Exception as e:
                print(f"Skipping user cleanup: {e}")
                conn.rollback()

            conn.commit()
            print("Database cleanup completed (candles/instruments preserved)")
        except Exception as e:
            print(f"Error during cleanup: {e}")
            conn.rollback()
        finally:
            cursor.close()

    def close(self):
        """Close database connection."""
        if self.connection:
            self.connection.close()
            self.connection = None


# Global test database manager
test_db_manager = TestDatabaseManager()


@pytest.fixture(scope="session")
def test_database():
    """Session-wide test database fixture.

    This fixture connects to an existing PostgreSQL database.
    Set USE_DOCKER_DB=true to use Docker Compose for the test database.
    By default, it expects a local PostgreSQL to be running.
    """
    use_docker = os.getenv("USE_DOCKER_DB", "false").lower() == "true"

    if use_docker:
        # Start test database via Docker
        if not test_db_manager.start_test_db():
            raise Exception("Failed to start test database via Docker")

    # Wait for database to be ready (whether Docker or local)
    if not test_db_manager.wait_for_db():
        if use_docker:
            test_db_manager.stop_test_db()
        raise Exception(
            "Test database not ready after 30 seconds. Ensure PostgreSQL is running."
        )

    yield test_db_manager

    # Cleanup only if using Docker
    if use_docker:
        test_db_manager.stop_test_db()


@pytest.fixture
def db_connection(test_database):
    """Database connection fixture for individual tests."""
    conn = test_database.get_connection()
    yield conn
    # Cleanup test data after each test
    test_database.cleanup_test_data()


@pytest.fixture
def db_cursor(db_connection):
    """Database cursor fixture."""
    cursor = db_connection.cursor()
    yield cursor
    cursor.close()


@pytest.fixture
def seed_instruments(db_connection):
    """Seed instrument metadata before tests.

    This fixture mirrors the seed_instruments.py script behavior.
    It inserts all 6 supported instruments into the database.
    """
    cursor = db_connection.cursor()

    instruments = [
        ("EUR_USD", "EUR/USD", "CURRENCY", -4),
        ("GBP_USD", "GBP/USD", "CURRENCY", -4),
        ("USD_JPY", "USD/JPY", "CURRENCY", -2),
        ("AUD_USD", "AUD/USD", "CURRENCY", -4),
        ("USD_CHF", "USD/CHF", "CURRENCY", -4),
        ("USD_CAD", "USD/CAD", "CURRENCY", -4),
    ]

    for symbol, display_name, inst_type, pip_loc in instruments:
        cursor.execute(
            """
            INSERT INTO instruments (symbol, display_name, instrument_type, pip_location, is_active, is_tradeable)
            VALUES (%s, %s, %s, %s, true, true)
            ON CONFLICT (symbol) DO NOTHING
            """,
            (symbol, display_name, inst_type, pip_loc),
        )

    db_connection.commit()
    cursor.close()
    return instruments


@pytest.fixture
def seed_test_candles(db_connection, seed_instruments):
    """Seed test candle data for integration tests.

    This creates sample candle data for testing without requiring real OANDA API.
    """
    from datetime import datetime, timedelta

    cursor = db_connection.cursor()

    # Create 100 candles for EUR_USD M1
    base_time = datetime.now(UTC) - timedelta(hours=2)

    for i in range(100):
        timestamp = base_time + timedelta(minutes=i)
        # Generate realistic price movements
        open_price = 1.1000 + (i * 0.0001)
        close_price = open_price + 0.0002
        high_price = max(open_price, close_price) + 0.0003
        low_price = min(open_price, close_price) - 0.0002

        cursor.execute(
            """
            INSERT INTO candles (
                exchange, instrument, timeframe, timestamp,
                open, high, low, close, volume, complete
            )
            VALUES ('oanda', 'EUR_USD', 'M1', %s, %s, %s, %s, %s, %s, true)
            ON CONFLICT (instrument, timeframe, timestamp) DO NOTHING
            """,
            (timestamp, open_price, high_price, low_price, close_price, 1000 + i * 10),
        )

    db_connection.commit()
    cursor.close()

    return {"instrument": "EUR_USD", "timeframe": "M1", "count": 100}


# =============================================================================
# SQLAlchemy-based fixtures for Phase 2 migration
# These fixtures use SQLAlchemy for database operations, enabling:
# - Transaction-based test isolation (rollback instead of DELETE)
# - Consistent database access pattern with production code
# - Proper dependency injection for testing production code paths
# =============================================================================

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

# Import database models and base
from src.agentfx.api.core.database import Base  # noqa: E402


@pytest.fixture(scope="session")
def test_engine():
    """Create a SQLAlchemy engine for the test database.

    This engine connects to the same database as TEST_DATABASE_URL from settings.
    It's a session-scoped fixture so the engine is reused across all tests.
    """
    engine = create_engine(settings.TEST_DATABASE_URL, echo=False)
    # Ensure tables exist
    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture(scope="function")
def db_session(test_engine) -> Generator[Session, None, None]:
    """Create a SQLAlchemy session with transaction isolation.

    Each test runs in its own transaction that is rolled back at the end,
    ensuring complete test isolation without needing DELETE statements.

    Use this fixture for tests that call production code using SQLAlchemy.
    """
    connection = test_engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection)()

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def test_client_with_db(db_session):
    """FastAPI test client with SQLAlchemy database override.

    This fixture overrides the get_db dependency to use the test session,
    enabling integration tests that call actual API endpoints with proper
    database isolation.
    """
    from fastapi.testclient import TestClient
    from src.agentfx.api.core.database import get_db
    from src.agentfx.api.main import app

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture(scope="session", autouse=True)
def _session_seed_test_candles(test_engine):
    """Pre-seed deterministic candle data once per integration session.

    With INGESTION_ENABLED=false in .env.test the OANDA worker no longer
    runs during tests, so optimization/pipeline tests that previously
    expected the worker to populate Postgres now need candles seeded
    explicitly. We invoke scripts/seed_test_candles.py via subprocess so
    it runs in a fresh process with .env.test loaded the same way the
    application would. Idempotent: the script no-ops if EUR_USD M1
    candles already exist.
    """
    import subprocess
    import sys

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    subprocess.run(
        [sys.executable, "scripts/seed_test_candles.py"],
        check=True,
        cwd=project_root,
        env={**os.environ, "ENVIRONMENT": "test", "PYTHONPATH": "src"},
    )
    yield
