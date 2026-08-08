"""Evolution-run API router (v0.3.0 Autonomous Strategy Loop).

Endpoints:
- POST   /api/v1/evolution-runs                          (TRADER+, 202)
- GET    /api/v1/evolution-runs                          (paginated)
- GET    /api/v1/evolution-runs/{run_id}                 (detail)
- DELETE /api/v1/evolution-runs/{run_id}                 (TRADER+, terminal-state only)
- POST   /api/v1/evolution-runs/{run_id}/stop            (TRADER+, manual halt)
- GET    /api/v1/evolution-runs/{run_id}/generations     (lineage)
- GET    /api/v1/evolution-runs/{run_id}/genomes/{id}    (genome detail)
- GET    /api/v1/evolution-runs/{run_id}/decisions       (decision log)

See planning/requirements/US-003-evolution-run-lifecycle.md and
US-016-evolution-observability.md for the full contract.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..core import rbac
from ..core.database import get_db
from ..services import evolution as evolution_service
from ..services import genome_share as genome_share_service
from ..services import user as user_service

router = APIRouter()


def _resolve_backtest_slice(
    *,
    folds: list,
    full_window: tuple[datetime, datetime],
    window: Literal["training", "oos", "full"],
) -> tuple[datetime, datetime]:
    """Pick the (start, end) for the on-demand genome backtest.

    Extracted so the slice-dispatch logic has its own focused unit-test
    surface — the rest of the endpoint is mostly DB/auth/HTTP wiring that
    is exercised by integration tests when those land.
    """
    # Lazy import to keep router import-time light.
    from agentfx.core.validation import oos_window, training_window

    if window == "training":
        return training_window(folds)
    if window == "oos":
        return oos_window(folds)
    return full_window


def _resolve_pinned_spread_pips(
    config_dict: dict,
    instrument: str,
) -> float | None:
    """Pick the spread the worker pinned at run-start, NOT the live spread.

    The on-demand backtest endpoint replays each genome at the exact cost
    model the GA + validator paid; otherwise its Sharpe/CAGR/MDD drift
    from the persisted walk-forward fold values whenever the live spread
    has changed between run-completion and re-execution.

    Lookup order mirrors the worker's snapshotting order:
        1. `effective_spread_pips_per_pair[instrument]` — v0.4.0+ per-pair
           dict (one entry per instrument the run touched).
        2. `effective_spread_pips` — legacy scalar (v0.3.0+ single-pair).
        3. `spread_pips` — the user's original pinned value (often `null`
           when the user wanted the live snapshot).
        4. None — caller will let `run_backtest_from_db` re-lookup live,
           which is *only* correct for runs whose worker never pinned
           anything (i.e. nothing else above is set).
    """
    eff_per_pair = config_dict.get("effective_spread_pips_per_pair") or {}
    if isinstance(eff_per_pair, dict):
        pinned = eff_per_pair.get(instrument)
        if pinned is not None:
            return float(pinned)
    legacy = config_dict.get("effective_spread_pips")
    if legacy is not None:
        return float(legacy)
    original = config_dict.get("spread_pips")
    if original is not None:
        return float(original)
    return None


def _ensure_trader(current_user: models.User) -> None:
    if current_user.role not in (
        models.UserRole.TRADER,
        models.UserRole.ADMIN,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="TRADER or ADMIN role required",
        )


def _ensure_admin(current_user: models.User) -> None:
    if current_user.role != models.UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="ADMIN role required",
        )


# ─────────────────────────────────────────────────────────────────────────
# Run lifecycle
# ─────────────────────────────────────────────────────────────────────────


@router.post(
    "/evolution-runs",
    response_model=schemas.EvolutionRunCreated,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_evolution_run(
    body: schemas.EvolutionRunCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
) -> schemas.EvolutionRunCreated:
    """Schedule a new evolution run.

    The run is persisted in QUEUED status and the worker is invoked via
    BackgroundTasks. The actual loop runs in a forked subprocess with
    `resource.setrlimit()` caps.
    """
    _ensure_trader(current_user)

    run = evolution_service.create_run(db, body, created_by=current_user.id)

    # Lazy import to avoid circular dependency on app startup
    from ..workers.evolution_runner import schedule_run

    background_tasks.add_task(schedule_run, run.id)

    estimated_max_generations = (
        body.stop_conditions.max_generations
        if body.stop_conditions
        else schemas.StopConditionsSchema().max_generations
    )

    return schemas.EvolutionRunCreated(
        run_id=run.id,
        status=run.status.value,
        status_url=f"/api/v1/evolution-runs/{run.id}",
        instrument=run.instrument,
        granularity=run.granularity,
        estimated_max_generations=estimated_max_generations,
        created_at=run.created_at,
    )


@router.get("/evolution-runs", response_model=schemas.EvolutionRunList)
def list_evolution_runs(
    page: int = 1,
    page_size: int = 10,
    status_filter: str | None = None,
    instrument: str | None = None,
    scope: rbac.Scope = "my",
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
) -> schemas.EvolutionRunList:
    rbac.require_admin_for_scope_all(current_user, scope)
    skip = (page - 1) * page_size
    # ADMIN + scope=all → unfiltered listing; every other case is scoped
    # to the caller's own rows (US-018). See rbac.scope_query_to_user for
    # the equivalent query-layer helper — this endpoint uses the service
    # layer's created_by kwarg because service.list_runs handles paging.
    created_by_filter = (
        None
        if current_user.role == models.UserRole.ADMIN and scope == "all"
        else current_user.id
    )
    items, total = evolution_service.list_runs(
        db,
        skip=skip,
        limit=page_size,
        status=status_filter,
        instrument=instrument,
        created_by=created_by_filter,
    )
    return schemas.EvolutionRunList(
        items=[schemas.EvolutionRunPublic.model_validate(r) for r in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/evolution-runs/{run_id}", response_model=schemas.EvolutionRunPublic)
def get_evolution_run(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
) -> schemas.EvolutionRunPublic:
    run = evolution_service.get_run(db, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Evolution run not found"
        )
    rbac.require_owner_or_admin(run, current_user, resource="evolution run")
    return schemas.EvolutionRunPublic.model_validate(run)


@router.delete("/evolution-runs/{run_id}")
def delete_evolution_run(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
) -> dict[str, bool | str]:
    """Delete a run and all its lineage (generations, genomes, validation
    folds, decisions cascade via FK ON DELETE CASCADE).

    TRADER+ — same auth as create/stop. Refuses with 409 if the run is
    still queued or running; stop it first via POST .../stop.
    """
    _ensure_trader(current_user)
    run = evolution_service.get_run(db, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Evolution run not found"
        )
    rbac.require_owner_or_admin(run, current_user, resource="evolution run")
    if run.status in (
        models.EvolutionRunStatus.QUEUED,
        models.EvolutionRunStatus.RUNNING,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(f"Run is {run.status.value}; stop it first before deleting."),
        )
    db.delete(run)
    db.commit()
    return {"success": True, "message": "Run deleted"}


@router.post("/evolution-runs/{run_id}/stop", response_model=schemas.EvolutionRunPublic)
def stop_evolution_run(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
) -> schemas.EvolutionRunPublic:
    """Request a manual halt. The worker will SIGTERM the child process if
    it's running; if the run hasn't started yet, status flips to STOPPED
    immediately."""
    _ensure_trader(current_user)
    # Ownership check must fire BEFORE the state transition so a
    # non-owner cannot force-halt another user's run.
    run_row = evolution_service.get_run(db, run_id)
    if run_row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Evolution run not found"
        )
    rbac.require_owner_or_admin(run_row, current_user, resource="evolution run")

    run = evolution_service.stop_run(db, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Evolution run not found"
        )
    # Best-effort: if the worker is running, kill the child process. The
    # worker's monitor will see the exit and finalize state.
    if run.process_pid:
        from ..workers.evolution_runner import signal_run_stop

        signal_run_stop(run.process_pid)

    return schemas.EvolutionRunPublic.model_validate(run)


# ─────────────────────────────────────────────────────────────────────────
# Sub-resources: generations / genomes / decisions
# ─────────────────────────────────────────────────────────────────────────


@router.get(
    "/evolution-runs/{run_id}/generations",
    response_model=schemas.GenerationList,
)
def list_run_generations(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
) -> schemas.GenerationList:
    run = evolution_service.get_run(db, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Evolution run not found"
        )
    rbac.require_owner_or_admin(run, current_user, resource="evolution run")

    gens = evolution_service.list_generations(db, run_id)
    return schemas.GenerationList(
        items=[schemas.GenerationPublic.model_validate(g) for g in gens],
        total=len(gens),
    )


@router.get(
    "/evolution-runs/{run_id}/genomes",
    response_model=schemas.StrategyGenomeSummaryList,
)
def list_run_genomes(
    run_id: str,
    generation_index: int | None = None,
    origin: str | None = None,
    limit: int = 2000,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
) -> schemas.StrategyGenomeSummaryList:
    """List all genomes in a run, in a lightweight projection.

    Used by the portal's run-wide lineage tree (Tier 3 of the UX fix).
    Caller can filter by generation or origin; default returns every
    genome for the run (capped at `limit`).
    """
    run = evolution_service.get_run(db, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Evolution run not found"
        )
    rbac.require_owner_or_admin(run, current_user, resource="evolution run")

    rows = evolution_service.list_run_genome_summaries(
        db,
        run_id,
        generation_index=generation_index,
        origin=origin,
        limit=limit,
    )
    return schemas.StrategyGenomeSummaryList(
        items=[schemas.StrategyGenomeSummary.model_validate(r) for r in rows],
        total=len(rows),
    )


@router.get(
    "/evolution-runs/{run_id}/genomes/{genome_id}",
    response_model=schemas.StrategyGenomePublic,
)
def get_genome_detail(
    run_id: str,
    genome_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
) -> schemas.StrategyGenomePublic:
    # Load the parent run first so we can enforce ownership at the run
    # level — genome IDs are effectively opaque to the caller so a bare
    # 404 here would leak nothing, but the run-level check keeps the
    # invariant "no cross-user reads" consistent across every endpoint.
    run = evolution_service.get_run(db, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Evolution run not found"
        )
    rbac.require_owner_or_admin(run, current_user, resource="evolution run")

    detail = evolution_service.get_genome_with_lineage(db, run_id, genome_id)
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Genome not found"
        )
    # The service returns a dict with mixed plain values + ORM ValidationFold
    # rows. Pydantic's `from_attributes=True` (BaseSchema) lets us serialize
    # the folds directly.
    return schemas.StrategyGenomePublic.model_validate(detail)


@router.get(
    "/evolution-runs/{run_id}/genomes/{genome_id}/export",
    response_model=schemas.GenomeExportPayload,
)
def export_genome(
    run_id: str,
    genome_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
) -> schemas.GenomeExportPayload:
    """v0.7.x — Cross-user genome sharing (export).

    Returns the `.agenome.json` payload for a genome the caller
    owns. Frontend saves the response body as
    `{genome_id}.agenome.json`; the peer imports it via
    `POST /api/v1/genomes/import`.

    Content is hashed (SHA-256 over canonical JSON minus the hash
    field) so the importer can detect tampering. Not signed —
    exporter attribution is trust-on-first-file, not authenticated.
    """
    try:
        return genome_share_service.export_genome_payload(
            db,
            evolution_run_id=run_id,
            genome_id=genome_id,
            user=current_user,
        )
    except ValueError as e:
        # Message differentiates "not found" (404) vs "not owned"
        # (403) — matches the existing genome-detail endpoint's
        # convention.
        msg = str(e)
        if "not owned" in msg:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail=msg
            ) from e
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=msg) from e


@router.post(
    "/genomes/import",
    response_model=schemas.GenomeImportResponse,
    status_code=status.HTTP_201_CREATED,
)
def import_genome(
    body: schemas.GenomeExportPayload,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
) -> schemas.GenomeImportResponse:
    """v0.7.x — Cross-user genome sharing (import).

    Creates shadow rows (EvolutionRun + Generation + StrategyGenome
    + ValidationFolds, all with new IDs) scoped to the importer.
    The genome then appears in the importer's approval queue and
    can be deployed like any native genome.

    Rejects with 400 on any validation failure — schema version
    unsupported, content hash mismatch, DSL grammar drift, etc.
    """
    from ..services.genome_share import GenomeImportError

    try:
        return genome_share_service.import_genome_payload(
            db, payload=body, importer_user=current_user
        )
    except GenomeImportError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e


@router.get(
    "/evolution-runs/{run_id}/genomes/{genome_id}/backtest",
    response_model=schemas.GenomeBacktestResult,
)
def get_genome_backtest(
    run_id: str,
    genome_id: str,
    window: Literal["training", "oos", "full"] = Query(
        default="training",
        description=(
            "Which slice of the run's data window to backtest. "
            "`training` = folds 0..K-2 concatenated (matches the GA's "
            "fitness). `oos` = the held-out final fold (matches Gate B). "
            "`full` = the entire effective data window."
        ),
    ),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
) -> schemas.GenomeBacktestResult:
    """Re-run the backtest for one genome and return full trade-level detail.

    The GA only persists the genome's fitness + walk-forward fold summary;
    individual trades and per-bar signals are not stored. To match the
    "drill into a trial" experience users have for optimisation jobs, we
    re-execute the backtest on demand from the genome's DSL spec + the
    requested slice of the run's data window. Result is deterministic
    (same DSL + same candles = same trades), so re-running is safe and
    idempotent.

    The `window` query param picks the slice — see the schema docstring
    for the three options. Defaults to `training` so existing callers
    (before this param existed) keep their current behaviour.

    Compute cost: ~1s on 120K candles. The page is interactive — re-run
    on every navigation — so no caching needed yet. If this becomes a hot
    path, persist results to a `genome_backtest_cache` table keyed by
    (genome_id, window, data_window_hash).
    """
    # Lazy imports — the backtest engine pulls in pandas + numpy which we
    # don't want at router-import time.
    from agentfx.api.workers.evolution_runner import _clamp_window_to_data
    from agentfx.core.validation import (
        make_walk_forward_folds,
    )
    from agentfx.services.backtest import run_backtest_from_db

    run = evolution_service.get_run(db, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Evolution run not found"
        )
    rbac.require_owner_or_admin(run, current_user, resource="evolution run")

    genome_detail = evolution_service.get_genome_with_lineage(db, run_id, genome_id)
    if genome_detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Genome not found"
        )

    # Reconstruct the same training window the orchestrator used so the
    # backtest's metrics line up with the displayed `fitness` value.
    #
    # Prefer the *effective* window the worker pinned at run-start (set
    # on every run that completed snapshotting — v0.3.0+). Fall back to
    # the user-supplied window dates, then to a rolling `data_years`
    # window for ancient runs that pre-date the dates-first form. Using
    # the effective dates means the re-run matches the GA's training
    # window exactly even months later, instead of drifting with `now()`.
    config_dict = run.config_json or {}
    eff_start_raw = config_dict.get("effective_data_window_start") or config_dict.get(
        "data_window_start"
    )
    eff_end_raw = config_dict.get("effective_data_window_end") or config_dict.get(
        "data_window_end"
    )
    if eff_start_raw and eff_end_raw:
        requested_window = (
            datetime.fromisoformat(eff_start_raw),
            datetime.fromisoformat(eff_end_raw),
        )
    else:
        data_years = int(config_dict.get("data_years", 5))
        requested_window = (
            datetime.now(tz=run.created_at.tzinfo) - timedelta(days=data_years * 365),
            datetime.now(tz=run.created_at.tzinfo),
        )
    clamped = _clamp_window_to_data(
        db,
        instrument=run.instrument,
        granularity=run.granularity,
        requested_window=requested_window,
    )
    if clamped is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"No candle data available for {run.instrument}:{run.granularity}; "
                f"cannot rerun the backtest."
            ),
        )

    # Pick the slice the caller asked for. Folds are computed exactly the
    # same way the orchestrator + validator computed them at run time, so
    # the returned trades + metrics line up bar-for-bar with what the GA
    # saw (training) or what Gate B was judged on (oos).
    n_folds = int(config_dict.get("n_validation_folds", 4))
    folds = make_walk_forward_folds(start=clamped[0], end=clamped[1], k=n_folds)
    slice_start, slice_end = _resolve_backtest_slice(
        folds=folds, full_window=clamped, window=window
    )

    backtest_kwargs: dict = {
        "account_balance": float(config_dict.get("account_balance", 10000.0)),
        "position_size": int(config_dict.get("position_size", 10000)),
        "leverage": int(config_dict.get("leverage", 30)),
    }
    # Use the spread the worker pinned at run-start so the endpoint's
    # replay matches the persisted walk-forward fold metrics. Live-spread
    # drift between run completion and re-execution previously produced
    # mismatching Sharpe/CAGR/MDD on the genome backtest page.
    pinned_spread = _resolve_pinned_spread_pips(config_dict, run.instrument)
    if pinned_spread is not None:
        backtest_kwargs["spread_pips"] = pinned_spread

    result = run_backtest_from_db(
        instrument=run.instrument,
        granularity=run.granularity,
        start=slice_start,
        end=slice_end,
        strategy_name="dsl",
        strategy_params={"spec": genome_detail["dsl_json"]},
        capture_signals=True,
        **backtest_kwargs,
    )

    return schemas.GenomeBacktestResult(
        run_id=run.id,
        genome_id=genome_id,
        instrument=run.instrument,
        granularity=run.granularity,
        window=window,
        data_range_start=slice_start,
        data_range_end=slice_end,
        metrics=result.get("metrics", {}),
        trades=result.get("trades", []),
        signals=result.get("signals", []),
        sizing=result.get("sizing"),
    )


@router.get(
    "/evolution-runs/{run_id}/decisions", response_model=schemas.DecisionLogList
)
def list_run_decisions(
    run_id: str,
    page: int = 1,
    page_size: int = 50,
    agent_type: str | None = None,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
) -> schemas.DecisionLogList:
    run = evolution_service.get_run(db, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Evolution run not found"
        )
    rbac.require_owner_or_admin(run, current_user, resource="evolution run")

    skip = (page - 1) * page_size
    items, total = evolution_service.list_decisions(
        db, run_id, skip=skip, limit=page_size, agent_type=agent_type
    )
    return schemas.DecisionLogList(
        items=[schemas.DecisionLogPublic.model_validate(d) for d in items],
        total=total,
        page=page,
        page_size=page_size,
    )


# ─────────────────────────────────────────────────────────────────────────
# ADMIN validator override (US-027)
# ─────────────────────────────────────────────────────────────────────────


@router.post(
    "/strategy-genomes/{genome_id}/override-validator",
    response_model=schemas.StrategyGenomePublic,
)
def override_validator_verdict_endpoint(
    genome_id: str,
    body: schemas.ValidatorOverrideRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(user_service.get_current_active_user),
) -> schemas.StrategyGenomePublic:
    """Flip `strategy_genomes.validator_passed` with an audit-log entry.

    Retires the raw-SQL demo-unblock workaround Wilson used during the
    v0.6.0 smoke-test. ADMIN only; TRADER / VIEWER get 403. Reason is
    required (min 20 chars) to force a self-documenting audit trail —
    the `USER_VALIDATOR_OVERRIDE` DecisionLog row records previous /
    new / reason / actor for anyone reading history later.
    """
    _ensure_admin(current_user)
    try:
        genome = evolution_service.override_validator_verdict(
            db,
            genome_id=genome_id,
            new_value=body.new_value,
            reason=body.reason,
            actor_user=current_user,
        )
    except evolution_service.GenomeNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Strategy genome {genome_id} not found",
        )

    # Return the same shape as get_genome_detail so the frontend can
    # replace its cached genome row directly. We reload with lineage so
    # the response includes validation_folds — matches the detail-page
    # query cache key.
    detail = evolution_service.get_genome_with_lineage(
        db,
        run_id=(
            db.query(models.Generation.run_id)
            .filter(models.Generation.id == genome.generation_id)
            .scalar()
        ),
        genome_id=genome.id,
    )
    if detail is None:
        # Should not happen — we just wrote to this row. If it does, the
        # override succeeded but the response can't include lineage.
        # Fall through with a minimal projection.
        return schemas.StrategyGenomePublic.model_validate(
            {
                "id": genome.id,
                "generation_id": genome.generation_id,
                "generation_index": 0,
                "dsl_json": genome.dsl_json,
                "parent_ids": list(genome.parent_ids or []),
                "origin": (
                    genome.origin.value
                    if hasattr(genome.origin, "value")
                    else str(genome.origin)
                ),
                "fitness": genome.fitness,
                "metrics_json": genome.metrics_json,
                "validator_passed": genome.validator_passed,
                "validation_failures": list(genome.validation_failures or []),
                "llm_reasoning": genome.llm_reasoning,
                "llm_call_id": genome.llm_call_id,
                "created_at": genome.created_at,
                "children": [],
                "validation_folds": [],
            }
        )
    return schemas.StrategyGenomePublic.model_validate(detail)


# Used internally by the worker to derive a default data window when the
# request body doesn't specify one. Exposed as a helper so tests can
# mock the clock easily.
def default_data_window_for(years: int) -> tuple[datetime, datetime]:
    from datetime import UTC

    end = datetime.now(UTC)
    start = end - timedelta(days=years * 365)
    return start, end
