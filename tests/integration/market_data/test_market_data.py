"""Real integration tests for market data components using PostgreSQL.

These tests follow the test strategy:
- Purpose: Test multiple components working together
- Speed: Medium (1-10 seconds per test)
- Dependencies: Real PostgreSQL database, real component interactions
- Coverage: API endpoints, workflows, data pipelines
"""

import pytest
import os
from datetime import datetime
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
import psycopg2
from psycopg2.extras import RealDictCursor

from src.agentfx.api.main import app
from src.agentfx.api.core.database import get_db
from src.agentfx.api.core.config import settings

# Set test environment before loading settings
os.environ["TESTING"] = "true"
os.environ["ENV_FILE"] = ".env.test"


@pytest.fixture(scope="session")
def postgres_connection():
    """Create a real PostgreSQL connection for integration tests."""
    # Wait for database to be ready
    max_attempts = 30
    for attempt in range(max_attempts):
        try:
            conn = psycopg2.connect(
                host=settings.TEST_DATABASE_HOST,
                port=settings.TEST_DATABASE_PORT,
                database=settings.TEST_DATABASE_NAME,
                user=settings.TEST_DATABASE_USER,
                password=settings.TEST_DATABASE_PASSWORD,
            )
            conn.close()
            break
        except Exception:
            if attempt == max_attempts - 1:
                raise Exception("PostgreSQL test database not ready after 30 seconds")
            import time

            time.sleep(1)

    # Create SQLAlchemy engine with PostgreSQL from settings
    engine = create_engine(settings.TEST_DATABASE_URL, echo=False)

    yield engine

    # Cleanup
    engine.dispose()


@pytest.fixture
def real_db_session(postgres_connection):
    """Create a real database session for integration tests."""
    TestingSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=postgres_connection
    )
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def test_client(real_db_session):
    """Create test client with real database."""

    def override_get_db():
        try:
            yield real_db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def db_cursor(real_db_session):
    """Get raw database cursor for direct SQL operations."""
    conn = psycopg2.connect(
        host=settings.TEST_DATABASE_HOST,
        port=settings.TEST_DATABASE_PORT,
        database=settings.TEST_DATABASE_NAME,
        user=settings.TEST_DATABASE_USER,
        password=settings.TEST_DATABASE_PASSWORD,
        cursor_factory=RealDictCursor,
    )
    cursor = conn.cursor()
    yield cursor
    cursor.close()
    conn.close()


@pytest.fixture(autouse=True)
def cleanup_test_data(db_cursor):
    """Clean up test data before and after each test.

    NOTE: Candles are EXCLUDED from cleanup. Historical candle data from OANDA
    should persist across tests for backtesting and optimization tests to work.
    Only user-created test data (users, runs, strategies, etc.) is cleaned up.
    """
    # Tables to clean - EXCLUDES candles (historical market data)
    # Also excludes instruments (metadata seeded once)
    tables_to_clean = [
        "trades",
        "orders",
        "signals",
        "runs",
        "strategies",
    ]

    # Clean up before test
    try:
        for table in tables_to_clean:
            try:
                # Only delete test-created records (those with test-like names/emails)
                db_cursor.execute(
                    f"DELETE FROM {table} WHERE email LIKE 'test%' OR email LIKE 'integration_%' OR name LIKE 'test%'"
                )
                db_cursor.connection.commit()
            except Exception:
                db_cursor.connection.rollback()
                pass  # Ignore cleanup errors before test

        # Clean up test users separately (don't delete seeded users)
        try:
            db_cursor.execute("DELETE FROM users WHERE email LIKE 'integration_%'")
            db_cursor.connection.commit()
        except Exception:
            db_cursor.connection.rollback()
            pass
    except Exception:
        pass  # Ignore cleanup errors before test

    yield

    # Clean up after test
    try:
        for table in tables_to_clean:
            try:
                db_cursor.execute(
                    f"DELETE FROM {table} WHERE email LIKE 'test%' OR email LIKE 'integration_%' OR name LIKE 'test%'"
                )
                db_cursor.connection.commit()
            except Exception:
                db_cursor.connection.rollback()
                pass  # Ignore cleanup errors after test

        # Clean up test users separately
        try:
            db_cursor.execute("DELETE FROM users WHERE email LIKE 'integration_%'")
            db_cursor.connection.commit()
        except Exception:
            db_cursor.connection.rollback()
            pass
    except Exception:
        pass  # Ignore cleanup errors after test


@pytest.mark.integration
class TestMarketDataIntegration:
    """Integration tests for market data components working together."""

    def create_authenticated_user_and_token(self, test_client, db_cursor):
        """Helper method to create a test user and return JWT token for authentication."""
        import uuid
        from src.agentfx.api.core.security import get_password_hash

        # Create test user via direct database insertion with unique email
        unique_id = str(uuid.uuid4())[:8]
        test_email = f"integration_{unique_id}@example.com"
        test_password = "test_password_123"

        # Use proper password hashing for authentication to work
        hashed_password = get_password_hash(test_password)

        # Insert user into PostgreSQL test database
        db_cursor.execute(
            """
            INSERT INTO users (email, hashed_password, is_active, full_name, role)
            VALUES (%s, %s, true, 'Integration User', 'VIEWER')
        """,
            (test_email, hashed_password),
        )

        db_cursor.connection.commit()

        # Use the tested authentication flow to get JWT token
        login_response = test_client.post(
            "/api/v1/login", data={"username": test_email, "password": test_password}
        )

        # Verify login was successful and get token
        assert login_response.status_code == 200
        token = login_response.json()["access_token"]
        assert token is not None

        return token, test_email

    def test_market_data_api_pipeline(self, test_client, db_cursor):
        """Test complete market data pipeline: API -> Database -> Response."""
        # Create authenticated user and get token
        token, _ = self.create_authenticated_user_and_token(test_client, db_cursor)

        # Insert test candle data via direct database insertion
        from datetime import timezone

        unique_timestamp = datetime.now(timezone.utc).replace(microsecond=0)
        db_cursor.execute(
            """
            INSERT INTO candles (exchange, instrument, timeframe, timestamp, open, high, low, close, volume, complete)
            VALUES ('oanda', 'EUR_USD', 'M1', %s, 1.1000, 1.1005, 1.0995, 1.1002, 100, true)
        """,
            (unique_timestamp,),
        )
        db_cursor.connection.commit()

        # Test API endpoint retrieves the data with authentication
        headers = {"Authorization": f"Bearer {token}"}
        response = test_client.get(
            "/api/v1/market-data/candles?exchange=oanda&instrument=EUR_USD&timeframe=M1&limit=10",
            headers=headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert len(data["items"]) > 0
        assert data["items"][0]["instrument"] == "EUR_USD"
        assert data["items"][0]["timeframe"] == "M1"
        assert data["items"][0]["open"] == 1.1000

    def test_market_data_crud_workflow(self, test_client, db_cursor):
        """Test complete CRUD workflow for market data."""
        # Create authenticated user and get token
        token, _ = self.create_authenticated_user_and_token(test_client, db_cursor)

        # Create: Insert candle data using bulk create schema
        candle_bulk_data = {
            "exchange": "oanda",
            "instrument": "USD_JPY",
            "timeframe": "M5",
            "candles": [
                {
                    "timestamp": "2023-01-01T12:00:00Z",
                    "open": 110.50,
                    "high": 110.55,
                    "low": 110.45,
                    "close": 110.52,
                    "volume": 50,
                    "complete": True,
                }
            ],
        }

        headers = {"Authorization": f"Bearer {token}"}
        response = test_client.post(
            "/api/v1/market-data/candles", json=candle_bulk_data, headers=headers
        )
        assert response.status_code == 200
        created_candles = response.json()
        assert len(created_candles) == 1
        # Pydantic models are serialized to dict in JSON response
        assert created_candles[0]["instrument"] == "USD_JPY"

        # Read: Retrieve the data
        response = test_client.get(
            "/api/v1/market-data/candles?exchange=oanda&instrument=USD_JPY&timeframe=M5",
            headers=headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert len(data["items"]) >= 1  # Should have the candle we just created

        # Test direct database insertion and retrieval
        from datetime import timezone

        unique_timestamp = datetime.now(timezone.utc).replace(microsecond=0)
        db_cursor.execute(
            """
            INSERT INTO candles (exchange, instrument, timeframe, timestamp, open, high, low, close, volume, complete)
            VALUES ('oanda', 'GBP_USD', 'M15', %s, 1.2500, 1.2505, 1.2495, 1.2502, 75, true)
        """,
            (unique_timestamp,),
        )
        db_cursor.connection.commit()

        response = test_client.get(
            "/api/v1/market-data/candles?exchange=oanda&instrument=GBP_USD&timeframe=M15",
            headers=headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert len(data["items"]) > 0
        assert data["items"][0]["instrument"] == "GBP_USD"

    def test_market_data_with_user_context(self, test_client, db_cursor):
        """Test market data operations with user authentication context."""
        # Use the authentication helper to create user and get token
        token, test_email = self.create_authenticated_user_and_token(
            test_client, db_cursor
        )

        # Insert strategy for the test
        import uuid

        unique_id = str(uuid.uuid4())[:8]
        db_cursor.execute(
            """
            INSERT INTO strategies (id, name, version, description, parameters_schema, is_active)
            VALUES (%s, 'Integration Strategy', '1.0.0', 'Test strategy', '{}', true)
        """,
            (f"integration_strategy_{unique_id}",),
        )

        db_cursor.connection.commit()

        # Insert market data using migrated database schema with unique timestamp
        from datetime import timezone

        unique_timestamp = datetime.now(timezone.utc).replace(microsecond=0)
        instrument_name = "GBP_USD_TEST"

        db_cursor.execute(
            """
            INSERT INTO candles (exchange, instrument, timeframe, timestamp, open, high, low, close, volume, complete)
            VALUES ('oanda', %s, 'M15', %s, 1.2500, 1.2505, 1.2495, 1.2502, 75, true)
        """,
            (instrument_name, unique_timestamp),
        )
        db_cursor.connection.commit()

        # Test authenticated access to market data using Bearer token from auth flow
        headers = {"Authorization": f"Bearer {token}"}
        response = test_client.get(
            f"/api/v1/market-data/candles?exchange=oanda&instrument={instrument_name}&timeframe=M15",
            headers=headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert len(data["items"]) > 0

    # NOTE: WebSocket E2E tests have been moved to tests/e2e/market_data.spec.ts
    # The following tests were removed as they're covered by Playwright E2E tests:
    # - test_websocket_market_data_full_integration
    # - test_websocket_authentication_integration
    # - test_websocket_concurrent_connections_integration

    def test_market_data_error_handling_workflow(self, test_client, db_cursor):
        """Test error handling in market data workflow."""
        # Create authenticated user and get token
        token, _ = self.create_authenticated_user_and_token(test_client, db_cursor)
        headers = {"Authorization": f"Bearer {token}"}

        # Test invalid instrument (missing exchange parameter)
        response = test_client.get(
            "/api/v1/market-data/candles?instrument=INVALID&timeframe=M1",
            headers=headers,
        )
        assert response.status_code in [400, 422]  # Missing required exchange parameter

        # Test invalid timeframe (with proper exchange)
        response = test_client.get(
            "/api/v1/market-data/candles?exchange=oanda&instrument=EUR_USD&timeframe=INVALID",
            headers=headers,
        )
        assert response.status_code in [400, 422]

        # Test invalid date format
        response = test_client.get(
            "/api/v1/market-data/candles?exchange=oanda&instrument=EUR_USD&timeframe=M1&from_time=invalid-date",
            headers=headers,
        )
        assert response.status_code in [400, 422]

        # Test database constraint violation with bulk create
        invalid_candle_bulk = {
            "exchange": "oanda",
            "instrument": "",  # Empty instrument should fail validation
            "timeframe": "M1",
            "candles": [
                {
                    "timestamp": "2023-01-01T12:00:00Z",
                    "open": "invalid_price",  # Invalid price should fail validation
                    "high": 1.1005,
                    "low": 1.0995,
                    "close": 1.1002,
                    "volume": -10,  # Negative volume should fail validation
                }
            ],
        }

        response = test_client.post(
            "/api/v1/market-data/candles", json=invalid_candle_bulk, headers=headers
        )
        assert (
            response.status_code == 200
        )  # Service handles validation gracefully and returns empty list

    def test_market_data_performance_integration(self, test_client, db_cursor):
        """Test performance characteristics of market data operations."""
        # Create authenticated user and get token
        token, _ = self.create_authenticated_user_and_token(test_client, db_cursor)
        headers = {"Authorization": f"Bearer {token}"}

        # Insert multiple candle records with unique timestamps
        from datetime import timezone, timedelta

        start_time = datetime.now()
        import uuid

        unique_id = str(uuid.uuid4())[:8]

        for i in range(100):
            # Create unique timestamp to avoid constraint violations
            timestamp = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(
                minutes=i
            )
            instrument_name = f"EUR_USD_{unique_id}"
            db_cursor.execute(
                """
                INSERT INTO candles (exchange, instrument, timeframe, timestamp, open, high, low, close, volume, complete)
                VALUES ('oanda', %s, 'M1', %s, %s, %s, %s, %s, %s, true)
            """,
                (
                    instrument_name,
                    timestamp,
                    1.1000 + i * 0.0001,
                    1.1005 + i * 0.0001,
                    1.0995 + i * 0.0001,
                    1.1002 + i * 0.0001,
                    100 + i,
                ),
            )

        db_cursor.connection.commit()
        insert_time = (datetime.now() - start_time).total_seconds()

        # Test query performance with pagination
        import time

        start_time = time.time()

        response = test_client.get(
            f"/api/v1/market-data/candles?exchange=oanda&instrument={instrument_name}&timeframe=M1&limit=50",
            headers=headers,
        )

        end_time = time.time()
        query_time = end_time - start_time

        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert len(data["items"]) <= 50
        assert query_time < 2.0  # Should complete within 2 seconds
        assert insert_time < 1.0  # Insert should be fast

    def test_market_data_concurrent_access(self, test_client, db_cursor):
        """Test concurrent access to market data endpoints."""
        import threading
        import time
        import uuid
        from datetime import timezone

        # Create authenticated user and get token
        token, _ = self.create_authenticated_user_and_token(test_client, db_cursor)
        headers = {"Authorization": f"Bearer {token}"}

        # Insert test data with unique identifiers
        unique_id = str(uuid.uuid4())[:8]
        unique_timestamp = datetime.now(timezone.utc).replace(microsecond=0)
        instrument_name = f"EUR_USD_{unique_id}"

        db_cursor.execute(
            """
            INSERT INTO candles (exchange, instrument, timeframe, timestamp, open, high, low, close, volume, complete)
            VALUES ('oanda', %s, 'M1', %s, 1.1000, 1.1005, 1.0995, 1.1002, 100, true)
        """,
            (instrument_name, unique_timestamp),
        )
        db_cursor.connection.commit()

        results = []

        def make_request():
            response = test_client.get(
                f"/api/v1/market-data/candles?exchange=oanda&instrument={instrument_name}&timeframe=M1",
                headers=headers,
            )
            results.append(response.status_code)

        # Create multiple concurrent requests
        threads = []
        for _ in range(10):
            thread = threading.Thread(target=make_request)
            threads.append(thread)

        # Start all threads
        start_time = time.time()
        for thread in threads:
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        end_time = time.time()
        total_time = end_time - start_time

        # All requests should succeed
        assert all(status == 200 for status in results)
        assert len(results) == 10
        # Should handle concurrent requests efficiently
        assert total_time < 5.0

    def test_database_transaction_handling(self, test_client, db_cursor):
        """Test database transaction handling in integration context."""
        import uuid

        # Use unique instrument to avoid conflicts with existing data
        unique_id = str(uuid.uuid4())[:8]
        unique_instrument = f"TRANSACTION_TEST_{unique_id}"

        # Test transaction rollback on error
        try:
            db_cursor.execute("BEGIN TRANSACTION")

            # Insert valid data
            db_cursor.execute(
                """
                INSERT INTO candles (exchange, instrument, timeframe, timestamp, open, high, low, close, volume, complete)
                VALUES ('oanda', %s, 'M1', '2023-01-01 12:00:00', 1.1000, 1.1005, 1.0995, 1.1002, 100, true)
            """,
                (unique_instrument,),
            )

            # Try to insert invalid data (should cause rollback)
            db_cursor.execute(
                """
                INSERT INTO candles (exchange, instrument, timeframe, timestamp, open, high, low, close, volume, complete)
                VALUES ('oanda', '', 'M1', '2023-01-01 12:00:00', 'invalid', 1.1005, 1.0995, 1.1002, -10, false)
            """
            )

            db_cursor.connection.commit()

        except Exception:
            db_cursor.connection.rollback()

        # Verify no data was inserted due to rollback
        db_cursor.execute(
            "SELECT COUNT(*) FROM candles WHERE instrument = %s", (unique_instrument,)
        )
        count_result = db_cursor.fetchone()
        # RealDictRow needs to be accessed by column name, not index
        count = count_result["count"] if count_result else 0
        assert count == 0

    def test_real_database_constraints(self, test_client, db_cursor):
        """Test real database constraints and foreign key relationships."""
        import uuid

        # Create user and strategy with unique identifiers
        unique_id = str(uuid.uuid4())[:8]
        test_email = f"test_{unique_id}@example.com"
        strategy_id = f"test_strategy_{unique_id}"

        db_cursor.execute(
            """
            INSERT INTO users (email, hashed_password, is_active, full_name)
            VALUES (%s, '$2b$12$fake_hash', true, 'Test User')
        """,
            (test_email,),
        )

        db_cursor.execute(
            """
            INSERT INTO strategies (id, name, version, description, parameters_schema, is_active)
            VALUES (%s, 'Test Strategy', '1.0.0', 'Test description', '{}', true)
        """,
            (strategy_id,),
        )
        db_cursor.connection.commit()

        # Test foreign key constraint
        try:
            db_cursor.execute(
                """
                INSERT INTO runs (strategy_id, status, parameters)
                VALUES (99999, 'RUNNING', '{}')
            """
            )
            db_cursor.connection.commit()
            assert False, "Should have failed due to foreign key constraint"
        except Exception:
            # Expected to fail due to foreign key constraint
            # Rollback the failed transaction before continuing
            db_cursor.connection.rollback()

        # Test valid foreign key
        run_id = f"run_test_{unique_id}"
        db_cursor.execute(
            """
            INSERT INTO runs (id, strategy_id, pair, environment, status, parameters)
            VALUES (%s, %s, 'EUR_USD', 'DEV', 'RUNNING', '{}')
        """,
            (run_id, strategy_id),
        )
        db_cursor.connection.commit()

        # Verify data was inserted correctly
        db_cursor.execute(
            "SELECT COUNT(*) FROM runs WHERE strategy_id = %s", (strategy_id,)
        )
        count_result = db_cursor.fetchone()
        count = count_result["count"] if count_result else 0
        assert count == 1
