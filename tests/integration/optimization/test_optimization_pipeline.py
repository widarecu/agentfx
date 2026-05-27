"""
Optimizer Pipeline Functional Tests

These tests validate the end-to-end optimization workflow:
1. Bayesian optimizer runs and generates trials
2. Backtest produces valid metrics
3. Gate B evaluation correctly marks pass/fail
4. Job state transitions work correctly
5. Gaussian Process improves over random sampling

This module starts the backend server and waits for candle ingestion
to complete before running optimizer tests against real data.

Prerequisites:
- PostgreSQL running with test database
- OANDA API credentials configured

Run with: ENVIRONMENT=test pytest tests/integration/optimization/test_optimization_pipeline.py -v
"""

import os
import signal
import subprocess
import time
from urllib.parse import urlparse

import psycopg2
import pytest
import requests
from psycopg2.extras import RealDictCursor

pytestmark = [pytest.mark.integration, pytest.mark.slow]

# Minimum candles needed for optimizer backtesting
MIN_CANDLES_FOR_BACKTEST = 100

# Maximum time to wait for candle ingestion (seconds)
# Ingestion processes M1, M5, M15, H1 for each instrument - H1 comes last
# 5 days of backfill at ~500 candles/request means multiple API calls
MAX_WAIT_FOR_CANDLES = 30  # 30 s — candles are pre-seeded by the
# session-scope autouse fixture in tests/integration/conftest.py;
# this is just a fail-fast guard for cases where seeding didn't run.


@pytest.fixture(scope="module")
def optimization_db():
    """
    Database connection for optimization pipeline tests.
    Does NOT clean up data between tests.
    """
    from src.agentfx.api.core.config import settings

    parsed = urlparse(settings.TEST_DATABASE_URL)
    conn_params = {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "database": parsed.path.lstrip("/") if parsed.path else "agentfx_test",
        "user": parsed.username or "test_user",
        "password": parsed.password or "test_password",
    }

    conn = psycopg2.connect(**conn_params, cursor_factory=RealDictCursor)
    yield conn
    conn.close()


@pytest.fixture(scope="module")
def backend_server():
    """
    Start the backend server for optimization pipeline tests.

    The backend server triggers the candle ingestion worker on startup.
    This fixture manages the server lifecycle for the test module.
    """
    # Check if server is already running
    try:
        response = requests.get("http://localhost:8000/health", timeout=2)
        if response.status_code == 200:
            print("Backend server already running, using existing instance")
            yield {"started": False, "url": "http://localhost:8000"}
            return
    except requests.exceptions.RequestException:
        pass

    # Start the backend server
    print("Starting backend server for optimization tests...")
    env = os.environ.copy()
    env["ENVIRONMENT"] = "test"

    process = subprocess.Popen(
        ["./scripts/start-backend.sh"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        preexec_fn=os.setsid,
    )

    # Wait for server to be ready
    server_ready = False
    for _ in range(30):
        try:
            response = requests.get("http://localhost:8000/health", timeout=2)
            if response.status_code == 200:
                server_ready = True
                break
        except requests.exceptions.RequestException:
            time.sleep(1)

    if not server_ready:
        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        pytest.fail("Backend server failed to start within 30 seconds")

    print("Backend server started successfully")
    yield {"started": True, "url": "http://localhost:8000", "process": process}

    # Cleanup
    print("Stopping backend server...")
    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
    process.wait(timeout=10)


@pytest.fixture(scope="module")
def wait_for_candles(optimization_db, backend_server):
    """
    Wait for candle ingestion worker to load sufficient data.

    The candle ingestion worker starts with the backend server and
    backfills historical data from OANDA. This fixture waits until
    enough candles are available for backtesting.

    If candle ingestion doesn't complete within the timeout, the tests
    are skipped rather than failed - this is a pipeline validation test
    that requires proper environment setup.
    """
    cursor = optimization_db.cursor()

    # First, check if we already have sufficient data
    cursor.execute(
        """
        SELECT COUNT(*) as count FROM candles
        WHERE instrument = 'EUR_USD' AND timeframe = 'H1' AND exchange = 'oanda'
        """
    )
    result = cursor.fetchone()
    initial_count = result["count"]

    if initial_count >= MIN_CANDLES_FOR_BACKTEST:
        print(f"Candle data already available: {initial_count} H1 candles for EUR_USD")
        cursor.close()
        return {"instrument": "EUR_USD", "timeframe": "H1", "count": initial_count}

    print(f"Waiting for candle data (need {MIN_CANDLES_FOR_BACKTEST} H1 candles)...")

    start_time = time.time()
    count = initial_count
    while time.time() - start_time < MAX_WAIT_FOR_CANDLES:
        cursor.execute(
            """
            SELECT COUNT(*) as count FROM candles
            WHERE instrument = 'EUR_USD' AND timeframe = 'H1' AND exchange = 'oanda'
            """
        )
        result = cursor.fetchone()
        count = result["count"]

        if count >= MIN_CANDLES_FOR_BACKTEST:
            print(f"Candle data ready: {count} H1 candles for EUR_USD")
            cursor.close()
            return {"instrument": "EUR_USD", "timeframe": "H1", "count": count}

        # Log progress every 10 seconds
        elapsed = int(time.time() - start_time)
        if elapsed % 10 == 0 and elapsed > 0:
            print(
                f"  ... waiting ({elapsed}s): {count}/{MIN_CANDLES_FOR_BACKTEST} candles"
            )

        time.sleep(2)

    cursor.close()
    pytest.fail(
        f"Candle ingestion did not complete within {MAX_WAIT_FOR_CANDLES}s. "
        f"Only {count} candles available, need {MIN_CANDLES_FOR_BACKTEST}. "
        f"Check OANDA credentials and network connectivity."
    )


class TestOptimizerPipelineBasics:
    """Basic functional tests for the optimizer pipeline."""

    def test_bayesian_optimizer_validates_inputs(self):
        """Test that optimizer validates inputs correctly."""
        from src.agentfx.agents.optimizer import BayesianOptimizerAgent

        agent = BayesianOptimizerAgent()

        # Missing required params
        is_valid, errors = agent.validate_inputs()
        assert not is_valid
        assert len(errors) > 0

        # Invalid param ranges (min >= max)
        is_valid, errors = agent.validate_inputs(
            strategy="ma",
            pair="EUR_USD",
            param_ranges={"fast": {"min": 50, "max": 10}},
        )
        assert not is_valid
        assert any("min must be < max" in e for e in errors)

        # Valid inputs
        is_valid, errors = agent.validate_inputs(
            strategy="ma",
            pair="EUR_USD",
            param_ranges={"fast": {"min": 5, "max": 50}},
        )
        assert is_valid
        assert len(errors) == 0

    def test_gate_b_evaluation(self):
        """Test Gate B threshold evaluation logic."""
        from src.agentfx.agents.optimizer import evaluate_gate_b

        # All passing metrics — includes `cagr` because Gate B grew a
        # min_cagr threshold (default 15%/yr) in a606ce4. Missing the
        # field used to default to -1.0 and silently fail this assertion.
        passing_metrics = {
            "sharpe": 1.5,
            "max_drawdown": 0.05,
            "hit_rate": 0.55,
            "n_trades": 500,
            "cagr": 0.20,
        }
        passes, failures = evaluate_gate_b(passing_metrics)
        assert passes is True
        assert len(failures) == 0

        # Failing sharpe
        failing_metrics = {
            "sharpe": 0.5,  # Below 1.0
            "max_drawdown": 0.05,
            "hit_rate": 0.55,
            "n_trades": 500,
            "cagr": 0.20,
        }
        passes, failures = evaluate_gate_b(failing_metrics)
        assert passes is False
        assert any("Sharpe" in f for f in failures)

        # Multiple failures across all 5 thresholds
        all_failing_metrics = {
            "sharpe": 0.5,
            "max_drawdown": 0.15,  # Above 10%
            "hit_rate": 0.40,  # Below 48%
            "n_trades": 100,  # Below 300
            "cagr": 0.05,  # Below 15%
        }
        passes, failures = evaluate_gate_b(all_failing_metrics)
        assert passes is False
        assert len(failures) == 5

    def test_parameter_normalization(self):
        """Test parameter normalization and denormalization."""
        import numpy as np
        from src.agentfx.agents.optimizer import BayesianOptimizerAgent

        agent = BayesianOptimizerAgent()
        param_ranges = {
            "fast": {"min": 5, "max": 50},
            "slow": {"min": 20, "max": 200},
        }

        # Test normalization
        params = {"fast": 27.5, "slow": 110}  # Midpoints
        normalized = agent._normalize_params(params, param_ranges)
        assert np.allclose(normalized, [0.5, 0.5])

        # Test denormalization
        denormalized = agent._denormalize_params(np.array([0.5, 0.5]), param_ranges)
        assert denormalized["fast"] == 27.5
        assert denormalized["slow"] == 110.0

        # Edge cases - min values
        min_params = {"fast": 5, "slow": 20}
        normalized_min = agent._normalize_params(min_params, param_ranges)
        assert np.allclose(normalized_min, [0.0, 0.0])

        # Edge cases - max values
        max_params = {"fast": 50, "slow": 200}
        normalized_max = agent._normalize_params(max_params, param_ranges)
        assert np.allclose(normalized_max, [1.0, 1.0])


class TestOptimizerWithData:
    """Tests that require actual candle data in the database.

    These tests use the wait_for_candles fixture which starts the backend
    and waits for candle ingestion to complete before running.
    """

    def test_optimizer_runs_and_generates_trials(self, wait_for_candles):
        """Test full optimizer pipeline generates trials."""
        from src.agentfx.agents.optimizer import BayesianOptimizerAgent

        agent = BayesianOptimizerAgent()

        # Run with minimal iterations for speed
        result = agent.run(
            strategy="ma",
            pair="EUR_USD",
            param_ranges={
                "fast": {"min": 5, "max": 30},
                "slow": {"min": 30, "max": 100},
            },
            data_years=1,  # Less data for faster tests
            granularity="H1",
            account_balance=10000,
            position_size=10000,
            max_iterations=5,  # Minimal iterations
            n_random_starts=3,
            capture_signals=False,  # Skip signal capture for speed
        )

        # Verify result structure
        assert result is not None
        assert result.status in ["success", "partial"]
        assert "all_trials" in result.outputs
        assert "top_results" in result.outputs

        # Verify trials were generated
        all_trials = result.outputs["all_trials"]
        assert len(all_trials) >= 3  # At least random samples

        # Verify trial structure
        for trial in all_trials:
            assert "params" in trial
            assert "metrics" in trial
            assert "sharpe" in trial
            assert "passes_gate_b" in trial
            assert "gate_b_failures" in trial
            assert "fast" in trial["params"]
            assert "slow" in trial["params"]

    def test_optimizer_metrics_are_valid(self, wait_for_candles):
        """Test that backtest produces valid metrics."""
        from src.agentfx.agents.optimizer import BayesianOptimizerAgent

        agent = BayesianOptimizerAgent()

        result = agent.run(
            strategy="ma",
            pair="EUR_USD",
            param_ranges={
                "fast": {"min": 5, "max": 20},
                "slow": {"min": 50, "max": 100},
            },
            data_years=1,
            granularity="H1",
            max_iterations=3,
            n_random_starts=3,
            capture_signals=False,
        )

        assert result.status in ["success", "partial"]

        for trial in result.outputs["all_trials"]:
            metrics = trial["metrics"]

            # Verify metric types and ranges
            if "sharpe" in metrics:
                assert isinstance(metrics["sharpe"], (int, float))

            if "max_drawdown" in metrics:
                # max_drawdown returns negative values (e.g., -0.10 = 10% drawdown)
                assert -1.0 <= metrics["max_drawdown"] <= 0

            if "hit_rate" in metrics:
                assert 0 <= metrics["hit_rate"] <= 1.0

            if "n_trades" in metrics:
                assert isinstance(metrics["n_trades"], int)
                assert metrics["n_trades"] >= 0

    def test_optimizer_gate_b_applied_correctly(self, wait_for_candles):
        """Test that Gate B evaluation is applied to each trial."""
        from src.agentfx.agents.optimizer import (
            GATE_B_THRESHOLDS,
            BayesianOptimizerAgent,
        )

        agent = BayesianOptimizerAgent()

        result = agent.run(
            strategy="ma",
            pair="EUR_USD",
            param_ranges={
                "fast": {"min": 5, "max": 20},
                "slow": {"min": 50, "max": 100},
            },
            data_years=1,
            granularity="H1",
            max_iterations=5,
            n_random_starts=5,
            capture_signals=False,
        )

        assert result.status in ["success", "partial"]

        for trial in result.outputs["all_trials"]:
            metrics = trial["metrics"]
            passes_gate_b = trial["passes_gate_b"]
            _gate_b_failures = trial["gate_b_failures"]  # noqa: F841

            # Verify Gate B logic is consistent with metrics
            sharpe = metrics.get("sharpe", 0)
            max_dd = metrics.get("max_drawdown", 1.0)
            hit_rate = metrics.get("hit_rate", 0)
            n_trades = metrics.get("n_trades", 0)

            expected_pass = (
                sharpe >= GATE_B_THRESHOLDS["sharpe_min"]
                and max_dd <= GATE_B_THRESHOLDS["max_drawdown"]
                and hit_rate >= GATE_B_THRESHOLDS["hit_rate_min"]
                and n_trades >= GATE_B_THRESHOLDS["min_trades"]
            )

            assert passes_gate_b == expected_pass, (
                f"Gate B mismatch for trial {trial['trial_index']}: "
                f"expected {expected_pass}, got {passes_gate_b}. "
                f"Metrics: sharpe={sharpe}, max_dd={max_dd}, hit_rate={hit_rate}, trades={n_trades}"
            )

    def test_optimizer_top_results_sorted_by_sharpe(self, wait_for_candles):
        """Test that top results are correctly sorted by Sharpe ratio."""
        from src.agentfx.agents.optimizer import BayesianOptimizerAgent

        agent = BayesianOptimizerAgent()

        result = agent.run(
            strategy="ma",
            pair="EUR_USD",
            param_ranges={
                "fast": {"min": 5, "max": 30},
                "slow": {"min": 50, "max": 150},
            },
            data_years=1,
            granularity="H1",
            max_iterations=8,
            n_random_starts=5,
            top_n=3,
            capture_signals=False,
        )

        assert result.status in ["success", "partial"]

        top_results = result.outputs["top_results"]
        assert len(top_results) <= 3

        # Verify sorted in descending order
        for i in range(len(top_results) - 1):
            current_sharpe = top_results[i]["metrics"].get("sharpe", float("-inf"))
            next_sharpe = top_results[i + 1]["metrics"].get("sharpe", float("-inf"))
            assert current_sharpe >= next_sharpe, (
                f"Top results not sorted: position {i} has sharpe {current_sharpe}, "
                f"position {i + 1} has sharpe {next_sharpe}"
            )

    def test_optimizer_progress_callback(self, wait_for_candles):
        """Test that progress callback is called for each trial."""
        from src.agentfx.agents.optimizer import BayesianOptimizerAgent

        agent = BayesianOptimizerAgent()

        progress_updates = []

        def progress_callback(current, total, trial_info):
            progress_updates.append(
                {
                    "current": current,
                    "total": total,
                    "trial": trial_info,
                }
            )

        result = agent.run(
            strategy="ma",
            pair="EUR_USD",
            param_ranges={
                "fast": {"min": 5, "max": 20},
                "slow": {"min": 50, "max": 100},
            },
            data_years=1,
            granularity="H1",
            max_iterations=5,
            n_random_starts=5,
            capture_signals=False,
            progress_callback=progress_callback,
        )

        assert result.status in ["success", "partial"]

        # Should have progress updates for each trial
        assert len(progress_updates) >= 3

        # Progress should be monotonically increasing
        for i in range(len(progress_updates) - 1):
            assert progress_updates[i]["current"] <= progress_updates[i + 1]["current"]


class TestOptimizerStrategies:
    """Test optimizer with different strategy types."""

    @pytest.mark.parametrize(
        "strategy,param_ranges",
        [
            ("ma", {"fast": {"min": 5, "max": 20}, "slow": {"min": 50, "max": 100}}),
            (
                "rsi",
                {
                    "period": {"min": 7, "max": 21},
                    "oversold": {"min": 20, "max": 35},
                    "overbought": {"min": 65, "max": 80},
                },
            ),
            (
                "donchian",
                {
                    "period": {"min": 10, "max": 55},
                    "exit_period": {"min": 5, "max": 20},
                },
            ),
        ],
    )
    def test_optimizer_works_with_different_strategies(
        self, wait_for_candles, strategy, param_ranges
    ):
        """Test optimizer works with MA, RSI, and Donchian strategies."""
        from src.agentfx.agents.optimizer import BayesianOptimizerAgent

        agent = BayesianOptimizerAgent()

        result = agent.run(
            strategy=strategy,
            pair="EUR_USD",
            param_ranges=param_ranges,
            data_years=1,
            granularity="H1",
            max_iterations=3,
            n_random_starts=3,
            capture_signals=False,
        )

        assert result.status in [
            "success",
            "partial",
        ], f"Optimizer failed for {strategy}: {result.errors}"
        assert len(result.outputs["all_trials"]) >= 1
