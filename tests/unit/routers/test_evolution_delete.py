"""Unit tests for DELETE /api/v1/evolution-runs/{run_id}.

Covers the behaviour added when the endpoint was lowered from ADMIN to
TRADER+ and gained a 409 guard against deleting a run that is still
queued or running. The cascade itself (run -> generations -> genomes ->
folds + decisions) is covered by the SQLAlchemy model layer; here we
only exercise the router-level auth and status-state checks.
"""

from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException, status

from agentfx.api import models
from agentfx.api.routers import evolution as evolution_router


@pytest.fixture
def mock_db():
    return Mock()


@pytest.fixture
def trader_user():
    user = Mock(spec=models.User)
    user.id = "user_trader"
    user.email = "trader@example.com"
    user.role = models.UserRole.TRADER
    return user


@pytest.fixture
def viewer_user():
    user = Mock(spec=models.User)
    user.id = "user_viewer"
    user.email = "viewer@example.com"
    user.role = models.UserRole.VIEWER
    return user


def _make_run(status_value: models.EvolutionRunStatus) -> Mock:
    run = Mock(spec=models.EvolutionRun)
    run.id = "evorun_test_001"
    run.status = status_value
    return run


class TestDeleteEvolutionRun:
    def test_trader_can_delete_completed_run(self, mock_db, trader_user):
        run = _make_run(models.EvolutionRunStatus.COMPLETED)
        with patch(
            "agentfx.api.routers.evolution.evolution_service.get_run",
            return_value=run,
        ):
            result = evolution_router.delete_evolution_run(
                run_id=run.id, db=mock_db, current_user=trader_user
            )
        mock_db.delete.assert_called_once_with(run)
        mock_db.commit.assert_called_once()
        assert result == {"success": True, "message": "Run deleted"}

    @pytest.mark.parametrize(
        "terminal_status",
        [
            models.EvolutionRunStatus.COMPLETED,
            models.EvolutionRunStatus.FAILED,
            models.EvolutionRunStatus.STOPPED,
        ],
    )
    def test_trader_can_delete_any_terminal_state(
        self, mock_db, trader_user, terminal_status
    ):
        run = _make_run(terminal_status)
        with patch(
            "agentfx.api.routers.evolution.evolution_service.get_run",
            return_value=run,
        ):
            evolution_router.delete_evolution_run(
                run_id=run.id, db=mock_db, current_user=trader_user
            )
        mock_db.delete.assert_called_once_with(run)

    @pytest.mark.parametrize(
        "in_flight_status",
        [
            models.EvolutionRunStatus.QUEUED,
            models.EvolutionRunStatus.RUNNING,
        ],
    )
    def test_delete_in_flight_run_returns_409(
        self, mock_db, trader_user, in_flight_status
    ):
        run = _make_run(in_flight_status)
        with patch(
            "agentfx.api.routers.evolution.evolution_service.get_run",
            return_value=run,
        ):
            with pytest.raises(HTTPException) as excinfo:
                evolution_router.delete_evolution_run(
                    run_id=run.id, db=mock_db, current_user=trader_user
                )
        assert excinfo.value.status_code == status.HTTP_409_CONFLICT
        assert "stop it first" in excinfo.value.detail.lower()
        mock_db.delete.assert_not_called()
        mock_db.commit.assert_not_called()

    def test_delete_unknown_run_returns_404(self, mock_db, trader_user):
        with patch(
            "agentfx.api.routers.evolution.evolution_service.get_run",
            return_value=None,
        ):
            with pytest.raises(HTTPException) as excinfo:
                evolution_router.delete_evolution_run(
                    run_id="evorun_nope", db=mock_db, current_user=trader_user
                )
        assert excinfo.value.status_code == status.HTTP_404_NOT_FOUND
        mock_db.delete.assert_not_called()

    def test_viewer_cannot_delete(self, mock_db, viewer_user):
        # Auth check fires before the run lookup, so no get_run patch needed.
        with pytest.raises(HTTPException) as excinfo:
            evolution_router.delete_evolution_run(
                run_id="evorun_test_001", db=mock_db, current_user=viewer_user
            )
        assert excinfo.value.status_code == status.HTTP_403_FORBIDDEN
        mock_db.delete.assert_not_called()
