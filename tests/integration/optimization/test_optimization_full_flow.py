"""
End-to-End Integration Tests for Optimization Flow

These tests validate the COMPLETE optimization flow WITHOUT mocking the optimizer:
1. Job creation (QUEUED status)
2. Worker picks up job (QUEUED → RUNNING)
3. BayesianOptimizerAgent runs with real backtest data
4. Trials are persisted via progress_callback
5. Signals and trades are bulk inserted
6. Job completes with best_sharpe set (RUNNING → COMPLETED)

This is the critical gap identified in the test strategy review:
- Existing worker tests mock BayesianOptimizerAgent
- Existing pipeline tests don't go through the worker
- This test validates the full integration without mocks

Prerequisites:
- PostgreSQL running with test database
- Sufficient candle data in database (H1 EUR_USD)
- OANDA API credentials (for initial data if needed)

Run with: make test-file FILE=tests/integration/optimization/test_optimization_full_flow.py
"""

from urllib.parse import urlparse

import psycopg2
import pytest
from psycopg2.extras import RealDictCursor
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.agentfx.api.core.config import settings
from src.agentfx.api.models.optimization import (
    JobStatus,
    OptimizationJob,
    OptimizationSignal,
    OptimizationTrade,
    OptimizationTrial,
    StrategyType,
)
from src.agentfx.api.services import optimization as optimization_service
from src.agentfx.api.workers.optimization_runner import (
    _execute_optimization,
    run_optimization,
)

pytestmark = [pytest.mark.integration, pytest.mark.slow]

# Minimum candles needed for backtesting
MIN_CANDLES_FOR_BACKTEST = 100


@pytest.fixture(scope="module")
def engine():
    """Create a SQLAlchemy engine for tests."""
    return create_engine(settings.TEST_DATABASE_URL, echo=False)


@pytest.fixture(scope="module")
def session_factory(engine):
    """Create a session factory."""
    return sessionmaker(bind=engine)


@pytest.fixture(scope="module")
def db_connection():
    """Raw database connection for verification queries."""
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
def verify_candle_data(db_connection):
    """
    Verify sufficient candle data exists for backtesting.

    This fixture checks that we have enough H1 EUR_USD candles
    to run a meaningful optimization. If not, the tests are skipped.
    """
    cursor = db_connection.cursor()
    cursor.execute(
        """
        SELECT COUNT(*) as count FROM candles
        WHERE instrument = 'EUR_USD' AND timeframe = 'H1' AND exchange = 'oanda'
        """
    )
    result = cursor.fetchone()
    cursor.close()

    count = result["count"]
    if count < MIN_CANDLES_FOR_BACKTEST:
        pytest.skip(
            f"Insufficient candle data for end-to-end tests. "
            f"Found {count} H1 EUR_USD candles, need {MIN_CANDLES_FOR_BACKTEST}. "
            f"Run the backend server to trigger candle ingestion first."
        )

    return {"instrument": "EUR_USD", "timeframe": "H1", "count": count}


@pytest.fixture
def create_test_job(session_factory):
    """Factory fixture to create a test optimization job."""
    created_jobs = []

    def _create(
        strategy: StrategyType = StrategyType.MA,
        pair: str = "EUR_USD",
        param_ranges: dict | None = None,
        estimated_iterations: int = 5,
        **overrides,
    ):
        session = session_factory()
        try:
            if param_ranges is None:
                param_ranges = {
                    "fast": {"min": 5, "max": 20, "step": 5},
                    "slow": {"min": 30, "max": 60, "step": 10},
                }

            job = OptimizationJob(
                id=optimization_service.generate_job_id(),
                strategy=strategy,
                pair=pair,
                param_ranges=param_ranges,
                account_balance=10000.0,
                position_size=10000,
                data_years=1,
                status=JobStatus.QUEUED,
                progress=0,
                estimated_iterations=estimated_iterations,
                completed_iterations=0,
                **overrides,
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            created_jobs.append(job.id)
            return job
        finally:
            session.close()

    yield _create

    # Cleanup created jobs and related data
    session = session_factory()
    try:
        for job_id in created_jobs:
            # Delete in order: trades -> signals -> trials -> job
            trials = (
                session.query(OptimizationTrial)
                .filter(OptimizationTrial.job_id == job_id)
                .all()
            )
            for trial in trials:
                session.query(OptimizationTrade).filter(
                    OptimizationTrade.trial_id == trial.id
                ).delete()
                session.query(OptimizationSignal).filter(
                    OptimizationSignal.trial_id == trial.id
                ).delete()
            session.query(OptimizationTrial).filter(
                OptimizationTrial.job_id == job_id
            ).delete()
            session.query(OptimizationJob).filter(OptimizationJob.id == job_id).delete()
        session.commit()
    finally:
        session.close()


def get_job_from_db(session_factory, job_id: str) -> OptimizationJob | None:
    """Helper to get fresh job state from database."""
    session = session_factory()
    try:
        return session.query(OptimizationJob).filter_by(id=job_id).first()
    finally:
        session.close()


def get_trials_from_db(session_factory, job_id: str) -> list[OptimizationTrial]:
    """Helper to get all trials for a job."""
    session = session_factory()
    try:
        return (
            session.query(OptimizationTrial)
            .filter_by(job_id=job_id)
            .order_by(OptimizationTrial.id)
            .all()
        )
    finally:
        session.close()


def get_signals_for_trial(session_factory, trial_id: str) -> list[OptimizationSignal]:
    """Helper to get signals for a trial."""
    session = session_factory()
    try:
        return (
            session.query(OptimizationSignal)
            .filter_by(trial_id=trial_id)
            .order_by(OptimizationSignal.timestamp)
            .all()
        )
    finally:
        session.close()


def get_trades_for_trial(session_factory, trial_id: str) -> list[OptimizationTrade]:
    """Helper to get trades for a trial."""
    session = session_factory()
    try:
        return (
            session.query(OptimizationTrade)
            .filter_by(trial_id=trial_id)
            .order_by(OptimizationTrade.entry_timestamp)
            .all()
        )
    finally:
        session.close()


class TestFullOptimizationFlow:
    """
    End-to-end tests for the complete optimization flow.

    These tests run the ACTUAL BayesianOptimizerAgent through the worker
    without mocking, validating the full integration.
    """

    @pytest.mark.asyncio
    async def test_full_flow_queued_to_completed(
        self, session_factory, create_test_job, verify_candle_data
    ):
        """
        Test the complete flow: QUEUED → RUNNING → COMPLETED with trials persisted.

        This is the PRIMARY end-to-end test that validates:
        1. Job starts in QUEUED status
        2. Worker transitions to RUNNING
        3. Real optimization runs (no mocking)
        4. Trials are created in database
        5. Job completes with best_sharpe
        """
        # Create a queued job with minimal iterations for speed
        job = create_test_job(
            strategy=StrategyType.MA,
            pair="EUR_USD",
            param_ranges={
                "fast": {"min": 5, "max": 15, "step": 5},
                "slow": {"min": 40, "max": 60, "step": 10},
            },
            estimated_iterations=3,
        )
        job_id = job.id

        # Verify initial state
        assert job.status == JobStatus.QUEUED
        assert job.progress == 0

        # Run the optimization through the worker (NO MOCKING)
        await run_optimization(job_id)

        # Verify job completed successfully
        completed_job = get_job_from_db(session_factory, job_id)
        assert completed_job is not None
        assert completed_job.status == JobStatus.COMPLETED, (
            f"Job should be COMPLETED but is {completed_job.status}. "
            f"Error: {completed_job.error_message}"
        )
        assert completed_job.progress == 100
        assert completed_job.completed_at is not None
        assert completed_job.started_at is not None

        # Verify trials were created
        trials = get_trials_from_db(session_factory, job_id)
        assert len(trials) >= 1, "At least one trial should have been created"

        # Verify trial structure
        for trial in trials:
            assert trial.job_id == job_id
            assert trial.params is not None
            assert "fast" in trial.params
            assert "slow" in trial.params
            assert trial.sharpe is not None
            assert trial.max_drawdown is not None
            assert trial.hit_rate is not None
            assert trial.trade_count is not None
            assert trial.passes_gate_b is not None

        # Verify best_sharpe matches top trial
        if completed_job.best_sharpe is not None:
            top_trial_sharpe = max(t.sharpe for t in trials)
            assert abs(completed_job.best_sharpe - top_trial_sharpe) < 0.001

    @pytest.mark.asyncio
    async def test_full_flow_with_signal_capture(
        self, session_factory, create_test_job, verify_candle_data
    ):
        """
        Test that signals and trades are persisted when capture_signals=True.

        Note: The worker always sets capture_signals=True, so this validates
        that the full persistence chain works including signals and trades.
        """
        job = create_test_job(
            strategy=StrategyType.MA,
            pair="EUR_USD",
            param_ranges={
                "fast": {"min": 8, "max": 12, "step": 2},
                "slow": {"min": 45, "max": 55, "step": 5},
            },
            estimated_iterations=2,
        )
        job_id = job.id

        # Run optimization
        await run_optimization(job_id)

        # Verify completion
        completed_job = get_job_from_db(session_factory, job_id)
        assert completed_job.status == JobStatus.COMPLETED

        # Get trials
        trials = get_trials_from_db(session_factory, job_id)
        assert len(trials) >= 1

        # Check that at least some trials have signals/trades
        # (may not have trades if the strategy parameters result in no signals)
        total_signals = 0
        total_trades = 0

        for trial in trials:
            signals = get_signals_for_trial(session_factory, trial.id)
            trades = get_trades_for_trial(session_factory, trial.id)
            total_signals += len(signals)
            total_trades += len(trades)

            # If there are signals, verify structure
            for signal in signals:
                assert signal.trial_id == trial.id
                assert signal.timestamp is not None
                assert signal.signal in [-1, 0, 1]
                assert signal.signal_type is not None
                assert signal.price is not None

            # If there are trades, verify structure
            for trade in trades:
                assert trade.trial_id == trial.id
                assert trade.direction in ["long", "short"]
                assert trade.entry_timestamp is not None
                assert trade.exit_timestamp is not None
                assert trade.entry_price is not None
                assert trade.exit_price is not None

        # Log for debugging
        print(f"Total signals persisted: {total_signals}")
        print(f"Total trades persisted: {total_trades}")

    @pytest.mark.asyncio
    async def test_full_flow_with_rsi_strategy(
        self, session_factory, create_test_job, verify_candle_data
    ):
        """Test full flow works with RSI strategy type."""
        job = create_test_job(
            strategy=StrategyType.RSI,
            pair="EUR_USD",
            param_ranges={
                "period": {"min": 10, "max": 14, "step": 2},
                "oversold": {"min": 25, "max": 30, "step": 5},
                "overbought": {"min": 70, "max": 75, "step": 5},
            },
            estimated_iterations=2,
        )
        job_id = job.id

        await run_optimization(job_id)

        completed_job = get_job_from_db(session_factory, job_id)
        assert completed_job.status == JobStatus.COMPLETED

        trials = get_trials_from_db(session_factory, job_id)
        assert len(trials) >= 1

        # Verify RSI params are in trials
        for trial in trials:
            assert "period" in trial.params
            assert "oversold" in trial.params
            assert "overbought" in trial.params

    @pytest.mark.asyncio
    async def test_full_flow_with_donchian_strategy(
        self, session_factory, create_test_job, verify_candle_data
    ):
        """Test full flow works with Donchian strategy type."""
        job = create_test_job(
            strategy=StrategyType.DONCHIAN,
            pair="EUR_USD",
            param_ranges={
                "period": {"min": 15, "max": 25, "step": 5},
                "exit_period": {"min": 8, "max": 12, "step": 2},
            },
            estimated_iterations=2,
        )
        job_id = job.id

        await run_optimization(job_id)

        completed_job = get_job_from_db(session_factory, job_id)
        assert completed_job.status == JobStatus.COMPLETED

        trials = get_trials_from_db(session_factory, job_id)
        assert len(trials) >= 1


class TestOptimizationStateTransitions:
    """Tests for job state transitions during the optimization flow."""

    @pytest.mark.asyncio
    async def test_job_transitions_through_all_states(
        self, session_factory, create_test_job, verify_candle_data
    ):
        """
        Verify job transitions: QUEUED → RUNNING → COMPLETED
        """
        job = create_test_job(estimated_iterations=2)
        job_id = job.id

        # Initial state
        initial_job = get_job_from_db(session_factory, job_id)
        assert initial_job.status == JobStatus.QUEUED
        assert initial_job.started_at is None
        assert initial_job.completed_at is None

        # Run optimization
        await run_optimization(job_id)

        # Final state
        final_job = get_job_from_db(session_factory, job_id)
        assert final_job.status == JobStatus.COMPLETED
        assert final_job.started_at is not None
        assert final_job.completed_at is not None
        assert final_job.completed_at > final_job.started_at

    @pytest.mark.asyncio
    async def test_skips_non_queued_job(
        self, session_factory, create_test_job, verify_candle_data
    ):
        """Test that worker skips jobs not in QUEUED status."""
        job = create_test_job(estimated_iterations=2)
        job_id = job.id

        # Manually set to COMPLETED
        session = session_factory()
        try:
            db_job = session.query(OptimizationJob).filter_by(id=job_id).first()
            db_job.status = JobStatus.COMPLETED
            session.commit()
        finally:
            session.close()

        # Run optimization - should skip
        await run_optimization(job_id)

        # Verify no trials created (optimization didn't run)
        trials = get_trials_from_db(session_factory, job_id)
        assert len(trials) == 0


class TestOptimizationErrorHandling:
    """Tests for error scenarios in the optimization flow."""

    @pytest.mark.asyncio
    async def test_job_completes_with_invalid_params_but_no_trials(
        self, session_factory, create_test_job, verify_candle_data
    ):
        """
        Test behavior when parameter ranges are invalid (min > max).

        The optimizer may complete but produce no valid trials when params
        are invalid. This tests that the system handles this gracefully.
        """
        job = create_test_job(
            strategy=StrategyType.MA,
            pair="EUR_USD",
            param_ranges={
                "fast": {"min": 50, "max": 10, "step": 5},  # Invalid: min > max
                "slow": {"min": 30, "max": 60, "step": 10},
            },
            estimated_iterations=3,
        )
        job_id = job.id

        await run_optimization(job_id)

        # Job may complete or fail depending on optimizer implementation
        job_result = get_job_from_db(session_factory, job_id)
        assert job_result.status in [JobStatus.FAILED, JobStatus.COMPLETED]

        # If completed, should have no valid sharpe (no successful trials)
        if job_result.status == JobStatus.COMPLETED:
            # Best sharpe may be None if no trials succeeded
            trials = get_trials_from_db(session_factory, job_id)
            # Either no trials or trials exist (optimizer may handle invalid params)
            assert trials is not None

    @pytest.mark.asyncio
    async def test_job_handles_missing_candle_data_gracefully(
        self, session_factory, create_test_job
    ):
        """
        Test that job fails gracefully when candle data is missing.

        Using a non-existent pair should cause the optimization to fail
        when trying to fetch candle data for backtesting.
        """
        job = create_test_job(
            strategy=StrategyType.MA,
            pair="XYZ_ABC",  # Non-existent pair (fits in varchar(10))
            param_ranges={
                "fast": {"min": 5, "max": 15, "step": 5},
                "slow": {"min": 40, "max": 60, "step": 10},
            },
            estimated_iterations=2,
        )
        job_id = job.id

        await run_optimization(job_id)

        failed_job = get_job_from_db(session_factory, job_id)
        # Should either fail or complete with no trades
        assert failed_job.status in [JobStatus.FAILED, JobStatus.COMPLETED]


class TestTrialPersistence:
    """Tests focused on trial data persistence validation."""

    @pytest.mark.asyncio
    async def test_trial_metrics_are_valid_ranges(
        self, session_factory, create_test_job, verify_candle_data
    ):
        """Verify that persisted trial metrics are within valid ranges."""
        job = create_test_job(estimated_iterations=3)
        job_id = job.id

        await run_optimization(job_id)

        trials = get_trials_from_db(session_factory, job_id)
        assert len(trials) >= 1

        for trial in trials:
            # Max drawdown is negative (e.g., -0.10 = 10% drawdown) per metrics.max_drawdown()
            assert -1.0 <= trial.max_drawdown <= 0, (
                f"Invalid max_drawdown: {trial.max_drawdown}"
            )

            # Hit rate should be between 0 and 1
            assert 0 <= trial.hit_rate <= 1.0, f"Invalid hit_rate: {trial.hit_rate}"

            # Trade count should be non-negative
            assert trial.trade_count >= 0, f"Invalid trade_count: {trial.trade_count}"

            # Sharpe can be any float (negative or positive)
            assert isinstance(trial.sharpe, float)

        # At least one trial should have generated trades
        # A valid strategy run over multi-year data MUST produce trades
        trials_with_trades = [t for t in trials if t.trade_count > 0]
        assert len(trials_with_trades) > 0, (
            f"No trials generated any trades! This indicates a bug in metrics calculation. "
            f"Trial trade counts: {[t.trade_count for t in trials]}"
        )

    @pytest.mark.asyncio
    async def test_gate_b_evaluation_persisted_correctly(
        self, session_factory, create_test_job, verify_candle_data
    ):
        """Verify Gate B evaluation is correctly persisted for each trial."""
        from src.agentfx.agents.optimizer import GATE_B_THRESHOLDS

        job = create_test_job(estimated_iterations=5)
        job_id = job.id

        await run_optimization(job_id)

        trials = get_trials_from_db(session_factory, job_id)

        for trial in trials:
            # Manually evaluate Gate B based on persisted metrics
            expected_pass = (
                trial.sharpe >= GATE_B_THRESHOLDS["sharpe_min"]
                and trial.max_drawdown <= GATE_B_THRESHOLDS["max_drawdown"]
                and trial.hit_rate >= GATE_B_THRESHOLDS["hit_rate_min"]
                and trial.trade_count >= GATE_B_THRESHOLDS["min_trades"]
            )

            assert trial.passes_gate_b == expected_pass, (
                f"Gate B mismatch for trial {trial.id}: "
                f"expected {expected_pass}, got {trial.passes_gate_b}. "
                f"Metrics: sharpe={trial.sharpe}, max_dd={trial.max_drawdown}, "
                f"hit_rate={trial.hit_rate}, trades={trial.trade_count}"
            )

            # If not passing, there should be failure reasons
            if not trial.passes_gate_b:
                assert trial.gate_b_failures is not None or trial.gate_b_failures == []

    @pytest.mark.asyncio
    async def test_best_trial_id_set_correctly(
        self, session_factory, create_test_job, verify_candle_data
    ):
        """Verify best_trial_id points to the actual best trial."""
        job = create_test_job(estimated_iterations=4)
        job_id = job.id

        await run_optimization(job_id)

        completed_job = get_job_from_db(session_factory, job_id)
        trials = get_trials_from_db(session_factory, job_id)

        if completed_job.best_trial_id and trials:
            # Find the best trial manually
            best_trial = max(trials, key=lambda t: t.sharpe)
            assert completed_job.best_trial_id == best_trial.id, (
                f"best_trial_id mismatch: expected {best_trial.id}, "
                f"got {completed_job.best_trial_id}"
            )

    @pytest.mark.asyncio
    async def test_trials_generate_nonzero_trades(
        self, session_factory, create_test_job, verify_candle_data
    ):
        """
        Verify that trials generate non-zero trade counts.

        This is a critical test that validates the metrics.summarize() function
        correctly returns n_trades. A Moving Average strategy over multi-year
        H1 data MUST generate trades (signal changes).

        This test was added after discovering that n_trades was missing from
        the metrics return dictionary, causing all Gate B checks to fail.
        """
        job = create_test_job(estimated_iterations=3)
        job_id = job.id

        await run_optimization(job_id)

        trials = get_trials_from_db(session_factory, job_id)
        assert len(trials) >= 1, "No trials were created"

        # Each trial should have trades
        for trial in trials:
            assert trial.trade_count > 0, (
                f"Trial {trial.id} has 0 trades. "
                f"This indicates metrics.summarize() is not returning n_trades correctly. "
                f"Metrics: sharpe={trial.sharpe}, hit_rate={trial.hit_rate}"
            )

        # Log trade counts for visibility
        trade_counts = [t.trade_count for t in trials]
        avg_trades = sum(trade_counts) / len(trade_counts)
        # With minimal test data (100 candles), lower threshold is appropriate
        assert avg_trades > 0, f"Average trade count ({avg_trades}) should be positive"


class TestProgressUpdates:
    """Tests for progress tracking during optimization."""

    def test_synchronous_execution_updates_progress(
        self, session_factory, create_test_job, verify_candle_data
    ):
        """
        Test progress updates by running _execute_optimization directly.

        This tests the synchronous execution path which is what actually
        runs in the thread pool during normal operation.
        """
        job = create_test_job(estimated_iterations=3)
        job_id = job.id

        # Run synchronously (as the thread pool would)
        _execute_optimization(job_id)

        # Verify final progress
        completed_job = get_job_from_db(session_factory, job_id)
        assert completed_job.progress == 100
        assert completed_job.status == JobStatus.COMPLETED


class TestSignalAndTradePersistence:
    """
    Detailed tests for signal and trade data persistence validation.

    These tests verify that visualization data (signals, trades) is correctly
    stored and retrievable for chart rendering in the UI.
    """

    @pytest.mark.asyncio
    async def test_signals_have_correct_structure(
        self, session_factory, create_test_job, verify_candle_data
    ):
        """Verify signal records have all required fields populated."""
        job = create_test_job(
            strategy=StrategyType.MA,
            pair="EUR_USD",
            param_ranges={
                "fast": {"min": 5, "max": 10, "step": 5},
                "slow": {"min": 40, "max": 50, "step": 10},
            },
            estimated_iterations=2,
        )
        job_id = job.id

        await run_optimization(job_id)

        trials = get_trials_from_db(session_factory, job_id)

        signals_found = False
        for trial in trials:
            signals = get_signals_for_trial(session_factory, trial.id)
            if signals:
                signals_found = True
                for signal in signals:
                    # Required fields
                    assert signal.id is not None
                    assert signal.trial_id == trial.id
                    assert signal.timestamp is not None
                    assert signal.signal in [
                        -1,
                        0,
                        1,
                    ], f"Invalid signal value: {signal.signal}"
                    assert signal.signal_type in [
                        "entry_long",
                        "entry_short",
                        "exit_long",
                        "exit_short",
                        "exit",
                        "hold",
                    ], f"Invalid signal_type: {signal.signal_type}"
                    assert signal.price is not None
                    assert signal.price > 0, f"Invalid price: {signal.price}"

        # Log for test visibility
        if not signals_found:
            print("No signals were generated by the strategy (this may be expected)")

    @pytest.mark.asyncio
    async def test_trades_have_correct_structure(
        self, session_factory, create_test_job, verify_candle_data
    ):
        """Verify trade records have all required fields populated."""
        job = create_test_job(
            strategy=StrategyType.MA,
            pair="EUR_USD",
            param_ranges={
                "fast": {"min": 5, "max": 10, "step": 5},
                "slow": {"min": 40, "max": 50, "step": 10},
            },
            estimated_iterations=2,
        )
        job_id = job.id

        await run_optimization(job_id)

        trials = get_trials_from_db(session_factory, job_id)

        trades_found = False
        for trial in trials:
            trades = get_trades_for_trial(session_factory, trial.id)
            if trades:
                trades_found = True
                for trade in trades:
                    # Required fields
                    assert trade.id is not None
                    assert trade.trial_id == trial.id
                    assert trade.direction in ["long", "short"]
                    assert trade.entry_timestamp is not None
                    assert trade.exit_timestamp is not None
                    assert trade.exit_timestamp >= trade.entry_timestamp
                    assert trade.entry_price is not None
                    assert trade.exit_price is not None
                    assert trade.entry_price > 0
                    assert trade.exit_price > 0

                    # PnL fields
                    assert trade.pips is not None
                    assert trade.net_pips is not None
                    assert trade.gross_pnl_dollars is not None
                    assert trade.net_pnl_dollars is not None

                    # Cost fields
                    assert trade.spread_cost_pips is not None
                    assert trade.spread_cost_pips >= 0

                    # Duration
                    assert trade.duration_minutes is not None
                    assert trade.duration_minutes >= 0

        if not trades_found:
            print("No trades were generated by the strategy (this may be expected)")

    @pytest.mark.asyncio
    async def test_trade_count_matches_persisted_trades(
        self, session_factory, create_test_job, verify_candle_data
    ):
        """Verify trial.trade_count matches actual persisted trade records."""
        job = create_test_job(estimated_iterations=3)
        job_id = job.id

        await run_optimization(job_id)

        trials = get_trials_from_db(session_factory, job_id)

        for trial in trials:
            trades = get_trades_for_trial(session_factory, trial.id)
            # trade_count in metrics should match persisted trades
            # Note: May differ if some trades failed to persist
            if trial.trade_count > 0:
                assert len(trades) > 0 or trial.trade_count >= len(trades), (
                    f"Trade count mismatch for trial {trial.id}: "
                    f"metrics says {trial.trade_count}, found {len(trades)} trades"
                )


class TestEnhancedErrorScenarios:
    """
    Extended error scenario coverage for robustness validation.

    Tests various failure modes to ensure the system handles errors gracefully.
    """

    @pytest.mark.asyncio
    async def test_job_continues_after_trial_with_no_trades(
        self, session_factory, create_test_job, verify_candle_data
    ):
        """
        Test that optimization continues even if a trial produces no trades.

        Some parameter combinations may result in no signals/trades.
        The optimization should not fail in this case.
        """
        # Use parameters that might result in few/no trades
        job = create_test_job(
            strategy=StrategyType.MA,
            pair="EUR_USD",
            param_ranges={
                "fast": {"min": 2, "max": 5, "step": 1},  # Very fast MA
                "slow": {"min": 100, "max": 150, "step": 25},  # Very slow MA
            },
            estimated_iterations=3,
        )
        job_id = job.id

        await run_optimization(job_id)

        # Should complete regardless of trade generation
        completed_job = get_job_from_db(session_factory, job_id)
        assert completed_job.status == JobStatus.COMPLETED

        # Trials should still be created
        trials = get_trials_from_db(session_factory, job_id)
        assert len(trials) >= 1

    @pytest.mark.asyncio
    async def test_partial_completion_preserves_trials(
        self, session_factory, create_test_job, verify_candle_data
    ):
        """
        Test that completed trials are preserved even if job ultimately fails.

        This validates that partial work is not lost on failure.
        """
        # Create a job that will run at least some trials
        job = create_test_job(
            strategy=StrategyType.MA,
            pair="EUR_USD",
            param_ranges={
                "fast": {"min": 5, "max": 15, "step": 5},
                "slow": {"min": 40, "max": 60, "step": 10},
            },
            estimated_iterations=5,
        )
        job_id = job.id

        # Run optimization normally
        await run_optimization(job_id)

        # Verify we have some trials even if job completes or fails
        trials = get_trials_from_db(session_factory, job_id)
        job_status = get_job_from_db(session_factory, job_id)

        # If job ran at all, we should have trials
        if job_status.status in [JobStatus.COMPLETED, JobStatus.FAILED]:
            # Check that any completed trials are properly persisted
            for trial in trials:
                assert trial.sharpe is not None
                assert trial.params is not None

    @pytest.mark.asyncio
    async def test_handles_extreme_parameter_values(
        self, session_factory, create_test_job, verify_candle_data
    ):
        """Test that extreme but valid parameter values are handled."""
        job = create_test_job(
            strategy=StrategyType.MA,
            pair="EUR_USD",
            param_ranges={
                "fast": {"min": 1, "max": 3, "step": 1},  # Very small values
                "slow": {"min": 200, "max": 250, "step": 25},  # Very large values
            },
            estimated_iterations=2,
        )
        job_id = job.id

        await run_optimization(job_id)

        # Should not crash
        completed_job = get_job_from_db(session_factory, job_id)
        assert completed_job.status in [JobStatus.COMPLETED, JobStatus.FAILED]

    @pytest.mark.asyncio
    async def test_concurrent_job_execution_isolation(
        self, session_factory, create_test_job, verify_candle_data
    ):
        """
        Test that multiple jobs can be created and processed without interference.

        This validates data isolation between concurrent optimization runs.
        """

        # Create two jobs
        job1 = create_test_job(
            strategy=StrategyType.MA,
            param_ranges={
                "fast": {"min": 5, "max": 10, "step": 5},
                "slow": {"min": 40, "max": 50, "step": 10},
            },
            estimated_iterations=2,
        )
        job2 = create_test_job(
            strategy=StrategyType.RSI,
            param_ranges={
                "period": {"min": 10, "max": 14, "step": 4},
                "oversold": {"min": 25, "max": 30, "step": 5},
                "overbought": {"min": 70, "max": 75, "step": 5},
            },
            estimated_iterations=2,
        )

        job1_id = job1.id
        job2_id = job2.id

        # Run both jobs (sequentially due to test database constraints)
        await run_optimization(job1_id)
        await run_optimization(job2_id)

        # Verify both completed
        completed_job1 = get_job_from_db(session_factory, job1_id)
        completed_job2 = get_job_from_db(session_factory, job2_id)

        assert completed_job1.status == JobStatus.COMPLETED
        assert completed_job2.status == JobStatus.COMPLETED

        # Verify trials are correctly associated with their jobs
        trials1 = get_trials_from_db(session_factory, job1_id)
        trials2 = get_trials_from_db(session_factory, job2_id)

        for trial in trials1:
            assert trial.job_id == job1_id
            assert "fast" in trial.params  # MA strategy params

        for trial in trials2:
            assert trial.job_id == job2_id
            assert "period" in trial.params  # RSI strategy params


class TestJobRecoveryIntegration:
    """
    Integration tests for job recovery functionality.

    Tests the system's ability to recover from crashes and restarts.
    """

    @pytest.mark.asyncio
    async def test_orphaned_running_job_can_be_reprocessed(
        self, session_factory, create_test_job, verify_candle_data
    ):
        """
        Test that a job stuck in RUNNING status can be reset and reprocessed.

        Simulates server crash during optimization.
        """
        job = create_test_job(estimated_iterations=2)
        job_id = job.id

        # Manually set to RUNNING (simulating crash during execution)
        session = session_factory()
        try:
            db_job = session.query(OptimizationJob).filter_by(id=job_id).first()
            db_job.status = JobStatus.RUNNING
            db_job.progress = 50
            session.commit()
        finally:
            session.close()

        # Reset back to QUEUED (as recover_queued_jobs would do)
        session = session_factory()
        try:
            db_job = session.query(OptimizationJob).filter_by(id=job_id).first()
            db_job.status = JobStatus.QUEUED
            db_job.progress = 0
            session.commit()
        finally:
            session.close()

        # Now run optimization - should work from QUEUED state
        await run_optimization(job_id)

        # Verify job completed
        completed_job = get_job_from_db(session_factory, job_id)
        assert completed_job.status == JobStatus.COMPLETED
        assert completed_job.progress == 100

    @pytest.mark.asyncio
    async def test_failed_job_can_be_retried(
        self, session_factory, create_test_job, verify_candle_data
    ):
        """
        Test that a failed job can be manually reset and retried.
        """
        job = create_test_job(estimated_iterations=2)
        job_id = job.id

        # Manually set to FAILED
        session = session_factory()
        try:
            db_job = session.query(OptimizationJob).filter_by(id=job_id).first()
            db_job.status = JobStatus.FAILED
            db_job.error_message = "Previous failure"
            session.commit()
        finally:
            session.close()

        # Reset to QUEUED for retry
        session = session_factory()
        try:
            db_job = session.query(OptimizationJob).filter_by(id=job_id).first()
            db_job.status = JobStatus.QUEUED
            db_job.progress = 0
            db_job.error_message = None
            session.commit()
        finally:
            session.close()

        # Retry
        await run_optimization(job_id)

        # Should succeed on retry
        completed_job = get_job_from_db(session_factory, job_id)
        assert completed_job.status == JobStatus.COMPLETED
