"""Evolution-run service layer.

Two responsibilities:

1. CRUD on runs / generations / genomes / decisions for the API
   (`get_run`, `get_runs`, `create_run`, `get_genome_with_lineage`, etc.)

2. The SQLAlchemy-backed `EvolutionStatePersister` implementation that the
   orchestrator depends on (`SqlAlchemyPersister`). The orchestrator runs
   inside a forked child process; this class owns the child's DB session.

The split keeps the API endpoints simple (call functions, return ORM rows
that get serialized via Pydantic schemas) while giving the orchestrator a
typed dependency it can mock.

See planning/requirements/US-003-evolution-run-lifecycle.md and
US-016-evolution-observability.md.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import desc
from sqlalchemy.orm import Session

from agentfx.agents.evolution import Genome
from agentfx.agents.validator import FoldEvaluation
from agentfx.api import models, schemas

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────
# ID generation
# ─────────────────────────────────────────────────────────────────────────


def generate_run_id() -> str:
    """`evorun_{YYYYMMDD}_{8 hex}` — sortable, traceable in logs."""
    today = datetime.now(UTC).strftime("%Y%m%d")
    short = uuid.uuid4().hex[:8]
    return f"evorun_{today}_{short}"


def generate_generation_id(run_id: str, index: int) -> str:
    return f"gen_{run_id}_{index:03d}"


def generate_genome_id(generation_id: str, index_in_gen: int) -> str:
    return f"genome_{generation_id}_{index_in_gen:03d}"


def generate_validation_fold_id(genome_id: str, fold_index: int) -> str:
    return f"fold_{genome_id}_{fold_index}"


# ─────────────────────────────────────────────────────────────────────────
# Run CRUD
# ─────────────────────────────────────────────────────────────────────────


def get_run(db: Session, run_id: str) -> models.EvolutionRun | None:
    return (
        db.query(models.EvolutionRun).filter(models.EvolutionRun.id == run_id).first()
    )


def find_prior_winners(
    db: Session,
    *,
    instrument: str,
    granularity: str,
    limit: int = 3,
    exclude_run_id: str | None = None,
    data_window_start: datetime | None = None,
    data_window_end: datetime | None = None,
    min_cagr_pct: float | None = None,
) -> list[dict]:
    """Top-K genomes (by fitness) from completed runs evaluated under the
    SAME criteria as the new run, with anti-cascade safeguards.

    Used by the orchestrator's gen-0 seeding to warm-start a new run
    with the best surviving strategies from prior runs against the
    same instrument/granularity. The "completed" status guard avoids
    pulling genomes from runs that crashed mid-evaluation. The
    exclude_run_id parameter prevents a re-started run from importing
    its own genomes.

    **Config-match guard (staging 2026-06-17):** a genome's `fitness`
    column is only comparable across runs that used the same
    evaluation rubric — same data window, same min_cagr_pct. A run
    scored on 1 year of data with min_cagr_pct=0.10 can produce a
    fitness of 1.5 that a 2-year / min_cagr_pct=0.15 run wouldn't
    reach with the same spec. Without matching config, the picker
    consistently chose the "loosest" prior run's elites, which then
    re-evaluated to deeply negative fitness on the new run's harder
    rules — every subsequent run started from genuinely bad seeds.
    When the matching config args are passed, the picker filters to
    only those runs. When omitted (legacy callers / tests), all
    completed runs match (preserves prior behaviour).

    **Anti-cascade safeguards (staging 2026-06-19).** The previous
    "top-K by fitness within a tier" design had a fatal failure mode:
    when prior runs converged on a local optimum, the picker passed
    that optimum forward to the next run, the next run inherited it
    via warm-start and couldn't beat it, and `no_improvement` fired
    again — and so on across runs. Same symptom as the May-27
    monoculture bug (`b60473a`) and the June-17 cross-config bug
    (`16d086d`), different mechanism. Two structural changes:

      A. PLATEAU EXCLUSION. Two-layer defence against spec recycling:

         Layer 1 (run-level, PR #11): skip a run from the candidate
         pool when `stop_reason == 'no_improvement'` AND that run's
         `best_genome.origin == 'warm_start'`. Semantic: this run's
         GA inherited a starting point and couldn't improve on it —
         passing the same elite forward just re-creates the plateau.
         Runs whose `best_genome.origin` is `mutation` / `crossover`
         / `llm` / `seed` represent genuine GA discovery (even if
         they later plateaued) — those still warm-start the next run.

         Layer 2 (spec-level, 2026-06-25 loophole fix): collect the
         canonical dsl_json keys of ALL `no_improvement` runs' best
         genomes, regardless of origin. Any candidate genome in the
         picker whose canonical spec matches one of those keys is
         excluded even if its source run is not Layer-1-excluded.
         This closes the "laundering" loophole: Run 1 stops with
         no_improvement, best genome is origin=crossover (not
         warm_start), so Layer 1 leaves Run 1 in the pool. Runs
         2/3/4 warm-start from Run 1's elite, also hit
         no_improvement (filtered by Layer 1), but Run 1's crossover
         genome keeps laundering as a warm-start candidate for
         every subsequent run. Layer 2 stops this by keying on the
         spec itself, not the origin (UAT cascade 2026-06-25:
         runs 11f90484 → d266f133 → 9902de96 → b5f74e0e).

         Genomes with `validator_passed=True` ALWAYS bypass BOTH
         layers — Gate B clearance is real edge regardless of how
         the surrounding run terminated.

      B. SOURCE-RUN DIVERSITY. After the elite-spec dedupe (`b60473a`
         part 2), enforce at most ONE spec per `source_run_id` in
         the picked list for non-validator-passed tiers. Forces gen-0
         to draw from multiple historical runs when possible, so a
         single dominant lineage cannot fill all warm-start slots.
         Validator-passed genomes bypass this rule — multiple Gate B
         clearances from the same run are all real winners.

    Filter strategy (most-to-least preferred), top up until we have
    `limit` candidates:
      1. validator_passed = TRUE — Gate B clearance; the gold standard
         for inheritance. Bypasses anti-cascade safeguards (real
         winners trump plateau/diversity heuristics).
      2. fitness > 0 — at least a positive Sharpe on training; not
         brilliant but not garbage either. Subject to plateau
         exclusion + source-run diversity.

    The previous tier 3 (`fitness > -50`, "carry-forward losing
    strategies") was REMOVED. Combined with tier-2 + plateau filter +
    source diversity, tier-3 was promoting confirmed-losing strategies
    as starting points. When tiers 1 and 2 are empty, the right
    answer is to fall back to canonical seeds and let the GA + LLM
    explore fresh — not to seed with confirmed losers.

    If all tiers come back empty (fresh DB, or every candidate run
    was filtered by plateau/config), return [] and the orchestrator
    falls back to canonical seeds. A WARNING-level log line records
    the reason so the audit trail is preserved.

    Returns a list of {id, dsl_json, fitness, source_run_id} dicts —
    callers compile dsl_json via StrategySpec.model_validate for the
    GA's population.
    """
    base_filters = [
        models.EvolutionRun.instrument == instrument,
        models.EvolutionRun.granularity == granularity,
        models.EvolutionRun.status == "completed",
        models.StrategyGenome.fitness.isnot(None),
    ]
    if exclude_run_id is not None:
        base_filters.append(models.Generation.run_id != exclude_run_id)

    # Cross-config fitness comparison is invalid — the fitness column
    # is only meaningful within a single (data window, min_cagr_pct)
    # rubric. When the caller passes any config arg, pre-resolve which
    # run IDs match in Python (database-portable: the JSON column is
    # generic JSON not JSONB, so `.astext` and JSONB operators aren't
    # available cross-engine). Run counts are O(10s) — Python filtering
    # is fine; the heavy query is then constrained to the matching
    # subset.
    has_config_filter = (
        data_window_start is not None
        or data_window_end is not None
        or min_cagr_pct is not None
    )
    if has_config_filter:
        candidate_runs = (
            db.query(models.EvolutionRun.id, models.EvolutionRun.config_json)
            .filter(
                models.EvolutionRun.instrument == instrument,
                models.EvolutionRun.granularity == granularity,
                models.EvolutionRun.status == "completed",
            )
            .all()
        )

        def _matches(cfg: dict) -> bool:
            if cfg is None:
                return False
            if data_window_start is not None:
                v = cfg.get("effective_data_window_start") or cfg.get(
                    "data_window_start"
                )
                if v != data_window_start.isoformat():
                    return False
            if data_window_end is not None:
                v = cfg.get("effective_data_window_end") or cfg.get("data_window_end")
                if v != data_window_end.isoformat():
                    return False
            if min_cagr_pct is not None:
                v = cfg.get("min_cagr_pct")
                # Tolerate int vs float JSON serialisation (0.15 vs
                # "0.15" vs 0.15000000001) via a small epsilon.
                if v is None or abs(float(v) - float(min_cagr_pct)) > 1e-9:
                    return False
            return True

        matching_ids = [r.id for r in candidate_runs if _matches(r.config_json)]
        if not matching_ids:
            logger.info(
                "warm-start: no completed prior runs match the new run's "
                "rubric (data_window=%s..%s, min_cagr_pct=%s) for %s %s — "
                "falling back to canonical seeds",
                data_window_start,
                data_window_end,
                min_cagr_pct,
                instrument,
                granularity,
            )
            return []  # no run matches the rubric; no warm-start
        base_filters.append(models.EvolutionRun.id.in_(matching_ids))

    # Anti-cascade: two-layer plateau exclusion (UAT 2026-06-25 cascade
    # loophole fix).
    #
    # LAYER 1 — run-level exclusion (origin-keyed, PR #11):
    #   Identify runs whose best genome's origin is `warm_start` AND that
    #   hit `no_improvement`. Those runs' best genomes are by definition
    #   local optima the GA inherited and couldn't escape — passing them
    #   forward re-creates the same plateau (staging 2026-06-19 cascade).
    #
    # LAYER 2 — spec-level exclusion (canonical-keyed, 2026-06-25 fix):
    #   The loophole: a run can stop with `no_improvement` while its best
    #   genome has origin=`crossover` / `mutation` / `llm` / `seed` (i.e.
    #   genuine GA work). Layer 1 correctly leaves that run's elite in the
    #   candidate pool. But if a *subsequent* run warm-starts from that
    #   elite and also hits `no_improvement`, Layer 1 filters the
    #   subsequent run — yet the ORIGINAL run's elite is still visible and
    #   selectable as a warm-start for yet another run. The cascade
    #   continues indefinitely: Runs 2/3/4 are filtered but Run 1's
    #   crossover genome keeps laundering through. Fix: collect the
    #   canonical spec keys of ALL `no_improvement` runs' best genomes
    #   (regardless of origin) into `plateau_spec_keys`, then filter any
    #   candidate whose spec key appears in that set. Layer 1 is retained
    #   as secondary safety (overlapping coverage; existing tests rely on
    #   it). Genomes with `validator_passed=True` bypass both layers.
    import json as _json

    def _canonical_spec_key(dsl: Any) -> str:
        """Canonical JSON key for plateau-spec comparison.

        Normalises both str and already-parsed dict forms (the JSON
        column comes back differently depending on the DB driver) so
        two genomes with byte-identical DSLs always produce the same
        key regardless of map order."""
        if isinstance(dsl, str):
            try:
                obj = _json.loads(dsl)
            except (TypeError, ValueError):
                return dsl
        else:
            obj = dsl
        return _json.dumps(obj, sort_keys=True, separators=(",", ":"))

    plateau_run_ids: set[str] = set()
    plateau_spec_keys: set[str] = set()
    candidate_runs_for_plateau = (
        db.query(
            models.EvolutionRun.id,
            models.EvolutionRun.stop_reason,
            models.EvolutionRun.best_genome_id,
        )
        .filter(
            models.EvolutionRun.instrument == instrument,
            models.EvolutionRun.granularity == granularity,
            models.EvolutionRun.status == "completed",
            models.EvolutionRun.stop_reason == "no_improvement",
            models.EvolutionRun.best_genome_id.isnot(None),
        )
        .all()
    )
    if candidate_runs_for_plateau:
        best_ids = [r.best_genome_id for r in candidate_runs_for_plateau]

        # Layer 1: origin-keyed run exclusion (PR #11 behaviour preserved).
        warm_start_best = (
            db.query(models.StrategyGenome.id)
            .filter(
                models.StrategyGenome.id.in_(best_ids),
                models.StrategyGenome.origin == models.GenomeOrigin.WARM_START,
            )
            .all()
        )
        warm_start_best_ids = {row.id for row in warm_start_best}
        plateau_run_ids = {
            r.id
            for r in candidate_runs_for_plateau
            if r.best_genome_id in warm_start_best_ids
        }

        # Layer 2: canonical-spec exclusion (2026-06-25 loophole fix).
        # Fetch dsl_json for ALL no_improvement best genomes, not just
        # warm_start ones — any spec that a no_improvement run peaked at
        # is a proven local optimum we should not recycle.
        all_best_genomes = (
            db.query(models.StrategyGenome.id, models.StrategyGenome.dsl_json)
            .filter(models.StrategyGenome.id.in_(best_ids))
            .all()
        )
        plateau_spec_keys = {
            _canonical_spec_key(row.dsl_json)
            for row in all_best_genomes
            if row.dsl_json is not None
        }

        if plateau_run_ids:
            logger.info(
                "warm-start: plateau-excluding %d run(s) whose elite was "
                "inherited via warm-start and the GA hit no_improvement "
                "(cascade prevention, layer 1): %s",
                len(plateau_run_ids),
                sorted(plateau_run_ids),
            )
        if plateau_spec_keys:
            logger.info(
                "warm-start: plateau-spec-excluding %d canonical spec(s) "
                "that appeared as the best genome in a no_improvement run "
                "(cascade prevention, layer 2 — UAT 2026-06-25 loophole fix)",
                len(plateau_spec_keys),
            )

    def _query(extra_filters: list, *, allow_plateau_runs: bool = False):
        """Build the candidate query.

        `allow_plateau_runs=True` is set ONLY for the validator-passed
        tier — Gate B clearance is real edge regardless of how the
        surrounding run terminated, so we must not let plateau
        exclusion suppress it. Every other tier respects the plateau
        filter.

        Plateau exclusion has two layers:
          - Layer 1 (run-level): drop entire runs whose best genome has
            origin=warm_start AND stop_reason=no_improvement.
          - Layer 2 (spec-level): post-query Python filter; any candidate
            whose canonical dsl_json key matches a no_improvement run's
            best genome is skipped in `_add_until_full`, regardless of
            which run the candidate came from. This catches the laundering
            loophole where Run 1's crossover elite is plateau'd but
            survives Layer 1 because its origin is not warm_start.

        Pulls a wider pool than `limit` so the Python-side dedupe +
        source-run-diversity + spec-level plateau rules below have room
        to drop candidates without exhausting the pool. 20× the limit is
        generous: real population sizes are ≤ 30, so even a
        fully-carryover-dominated prior run can't produce more than ~30
        duplicates per generation, and we usually want only 3–5.
        """
        q = (
            db.query(
                models.StrategyGenome.id,
                models.StrategyGenome.dsl_json,
                models.StrategyGenome.fitness,
                models.Generation.run_id,
            )
            .join(
                models.Generation,
                models.StrategyGenome.generation_id == models.Generation.id,
            )
            .join(
                models.EvolutionRun,
                models.Generation.run_id == models.EvolutionRun.id,
            )
            .filter(*(base_filters + extra_filters))
        )
        if not allow_plateau_runs and plateau_run_ids:
            q = q.filter(models.Generation.run_id.notin_(plateau_run_ids))
        return q.order_by(desc(models.StrategyGenome.fitness)).limit(
            max(limit * 20, 100)
        )

    def _spec_key(dsl_json: Any) -> str:
        """Canonical key for dedup. JSON columns can come back as either
        a string (older Postgres drivers) or already-parsed dict
        (psycopg2 JSON adapter); we normalise both to a sorted-keys
        string so byte-identical specs collide regardless of map order."""
        import json

        if isinstance(dsl_json, str):
            try:
                obj = json.loads(dsl_json)
            except (TypeError, ValueError):
                return dsl_json
        else:
            obj = dsl_json
        return json.dumps(obj, sort_keys=True, separators=(",", ":"))

    rows: list = []
    seen_specs: set[str] = set()  # canonical DSL keys we've already added
    seen_source_runs: set[str] = set()  # source_run_ids we've already added

    def _add_until_full(
        candidates,
        *,
        enforce_source_diversity: bool,
        allow_plateau_specs: bool = False,
    ) -> None:
        """Append `candidates` into `rows`, skipping any whose canonical
        spec we've already seen. Order is preserved (highest fitness
        first within the candidate list), so the strongest representative
        of each unique spec wins its slot.

        When `enforce_source_diversity=True`, also skip any candidate
        whose `source_run_id` we've already pulled from — forces the
        picked list to draw from multiple historical runs so a single
        dominant lineage cannot fill all the warm-start slots.
        Validator-passed genomes call this with the rule OFF, since
        multiple Gate B clearances from the same run are all real
        winners worth preserving.

        When `allow_plateau_specs=False` (the default), also skip any
        candidate whose canonical dsl_json key appears in
        `plateau_spec_keys` — Layer 2 of the cascade prevention logic
        (UAT 2026-06-25 loophole fix). Validator-passed genomes set
        `allow_plateau_specs=True` so Gate B clearance always wins
        regardless of plateau history.
        """
        for r in candidates:
            if len(rows) >= limit:
                return
            key = _spec_key(r.dsl_json)
            if key in seen_specs:
                continue
            if enforce_source_diversity and r.run_id in seen_source_runs:
                continue
            if not allow_plateau_specs and key in plateau_spec_keys:
                continue
            seen_specs.add(key)
            seen_source_runs.add(r.run_id)
            rows.append(r)

    # Tier 1: genomes that passed validation (Gate B on OOS). The gold
    # standard for inheritance — bypasses BOTH layers of plateau exclusion
    # AND source-run diversity (real winners trump heuristics).
    _add_until_full(
        _query(
            [models.StrategyGenome.validator_passed.is_(True)],
            allow_plateau_runs=True,
        ).all(),
        enforce_source_diversity=False,
        allow_plateau_specs=True,
    )

    # Tier 2: genomes with positive training fitness (real Sharpe > 0,
    # not the zero-trade penalty floor of −100 nor the legacy
    # fitness=0.0 degenerate cases that re-compute as -100 today).
    # Top up to `limit` if tier 1 didn't fill. Subject to BOTH layers of
    # plateau exclusion + source-run diversity.
    if len(rows) < limit:
        _add_until_full(
            _query([models.StrategyGenome.fitness > 0]).all(),
            enforce_source_diversity=True,
            allow_plateau_specs=False,
        )

    # Audit log when the picker came up short — the orchestrator falls
    # back to canonical seeds for the missing slots; recording why is
    # what stops the next round of plateau debugging from being pure
    # archaeology.
    if len(rows) < limit:
        logger.info(
            "warm-start: returning %d of %d requested candidates for %s %s. "
            "Tier 1 (validator_passed) and tier 2 (fitness>0) combined "
            "produced %d unique specs after plateau exclusion + source-run "
            "diversity — orchestrator will fill remaining slots from "
            "canonical seeds.",
            len(rows),
            limit,
            instrument,
            granularity,
            len(rows),
        )

    return [
        {
            "id": row.id,
            "dsl_json": row.dsl_json,
            "fitness": float(row.fitness) if row.fitness is not None else None,
            "source_run_id": row.run_id,
        }
        for row in rows
    ]


def list_runs(
    db: Session,
    *,
    skip: int = 0,
    limit: int = 10,
    status: str | None = None,
    instrument: str | None = None,
) -> tuple[list[models.EvolutionRun], int]:
    """Paginated listing. Returns (items, total)."""
    q = db.query(models.EvolutionRun)
    if status:
        q = q.filter(models.EvolutionRun.status == status)
    if instrument:
        q = q.filter(models.EvolutionRun.instrument == instrument)
    total = q.count()
    items = (
        q.order_by(desc(models.EvolutionRun.created_at)).offset(skip).limit(limit).all()
    )
    return items, total


def create_run(
    db: Session,
    create: schemas.EvolutionRunCreate,
    *,
    created_by: int | None = None,
) -> models.EvolutionRun:
    """Persist a new evolution_run row in QUEUED status. The actual
    loop is launched separately by the worker."""
    # Merge any partial override into the full defaults so callers don't
    # have to repeat every field.
    defaults = schemas.OrchestratorConfigSchema().model_dump()
    overrides = (
        create.config.model_dump(exclude_unset=True)
        if create.config is not None
        else {}
    )
    config_dict = {**defaults, **overrides}

    stop_defaults = schemas.StopConditionsSchema().model_dump()
    stop_overrides = (
        create.stop_conditions.model_dump(exclude_unset=True)
        if create.stop_conditions is not None
        else {}
    )
    stop_dict = {**stop_defaults, **stop_overrides}
    # Capture the caller-facing fields too so the row is fully self-describing
    config_dict.setdefault("instrument", create.instrument)
    config_dict.setdefault("granularity", create.granularity)
    # Only persist `data_years` when the caller actually sent it. The
    # v0.4.0+ form always pins explicit dates and omits this; the worker's
    # `_resolve_requested_window` defaults to 5 if it has to fall back.
    if create.data_years is not None:
        config_dict["data_years"] = create.data_years
    config_dict["llm_budget_usd"] = create.llm_budget_usd
    # Optional explicit data window. Stored verbatim as user-requested
    # input. The worker will write the *effective* (post-clamp) window
    # into `effective_data_window_start` / `effective_data_window_end`
    # at run-start so reproductions don't depend on `now()` ever again.
    if create.data_window_start is not None:
        config_dict["data_window_start"] = create.data_window_start.isoformat()
    if create.data_window_end is not None:
        config_dict["data_window_end"] = create.data_window_end.isoformat()

    run = models.EvolutionRun(
        id=generate_run_id(),
        status=models.EvolutionRunStatus.QUEUED,
        instrument=create.instrument,
        granularity=create.granularity,
        config_json=config_dict,
        stop_conditions_json=stop_dict,
        created_by=created_by,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def stop_run(db: Session, run_id: str) -> models.EvolutionRun | None:
    """Mark a run for manual stop. The worker (next chunk) actually
    sends SIGTERM to the child process."""
    run = get_run(db, run_id)
    if run is None:
        return None
    if run.status not in (
        models.EvolutionRunStatus.QUEUED,
        models.EvolutionRunStatus.RUNNING,
    ):
        return run  # idempotent — already stopped/completed/failed
    run.status = models.EvolutionRunStatus.STOPPED
    run.stop_reason = "manual_stop"
    run.completed_at = datetime.now(UTC)
    db.commit()
    db.refresh(run)
    return run


def find_orphaned_running_runs(db: Session) -> list[models.EvolutionRun]:
    """Runs in RUNNING state on app startup are orphaned by definition —
    their child process died with the previous app instance. The worker's
    recovery hook flips them back to QUEUED so they can be re-scheduled.
    """
    return (
        db.query(models.EvolutionRun)
        .filter(models.EvolutionRun.status == models.EvolutionRunStatus.RUNNING)
        .all()
    )


def find_queued_runs(db: Session) -> list[models.EvolutionRun]:
    return (
        db.query(models.EvolutionRun)
        .filter(models.EvolutionRun.status == models.EvolutionRunStatus.QUEUED)
        .order_by(models.EvolutionRun.created_at)
        .all()
    )


# ─────────────────────────────────────────────────────────────────────────
# Generation / genome / decision read paths
# ─────────────────────────────────────────────────────────────────────────


def list_generations(db: Session, run_id: str) -> list[models.Generation]:
    return (
        db.query(models.Generation)
        .filter(models.Generation.run_id == run_id)
        .order_by(models.Generation.index)
        .all()
    )


def get_genome_with_lineage(
    db: Session, run_id: str, genome_id: str
) -> dict[str, Any] | None:
    """Fetch one genome with its lineage and validation folds.

    Returns a plain dict the router can serialize — using a dict (rather
    than the ORM object) lets us include synthetic fields (children
    summaries, generation_index) the schema requires but the model
    doesn't carry directly.
    """
    genome = (
        db.query(models.StrategyGenome)
        .filter(models.StrategyGenome.id == genome_id)
        .first()
    )
    if genome is None:
        return None

    # Generation must belong to the run (defence in depth — caller's
    # responsibility too, but cheap to verify).
    generation = (
        db.query(models.Generation)
        .filter(models.Generation.id == genome.generation_id)
        .first()
    )
    if generation is None or generation.run_id != run_id:
        return None

    # Children: any genome whose parent_ids JSON list contains this id.
    # Postgres-specific operator is jsonb @>, but the column is JSON not
    # JSONB; fall back to a Python-side filter using a coarse query.
    candidate_children = (
        db.query(models.StrategyGenome, models.Generation.index.label("gen_index"))
        .join(
            models.Generation,
            models.StrategyGenome.generation_id == models.Generation.id,
        )
        .filter(models.Generation.run_id == run_id)
        .filter(models.Generation.index > generation.index)
        .all()
    )
    children = [
        {"id": c[0].id, "generation_index": c[1]}
        for c in candidate_children
        if isinstance(c[0].parent_ids, list) and genome.id in c[0].parent_ids
    ]

    folds = (
        db.query(models.ValidationFold)
        .filter(models.ValidationFold.genome_id == genome.id)
        .order_by(models.ValidationFold.fold_index)
        .all()
    )

    return {
        "id": genome.id,
        "generation_id": genome.generation_id,
        "generation_index": generation.index,
        "dsl_json": genome.dsl_json,
        "parent_ids": list(genome.parent_ids or []),
        "origin": (
            genome.origin.value if hasattr(genome.origin, "value") else genome.origin
        ),
        "fitness": genome.fitness,
        "metrics_json": genome.metrics_json,
        "validator_passed": genome.validator_passed,
        "validation_failures": list(genome.validation_failures or []),
        "llm_reasoning": genome.llm_reasoning,
        "llm_call_id": genome.llm_call_id,
        "created_at": genome.created_at,
        "children": children,
        "validation_folds": folds,
    }


def list_decisions(
    db: Session,
    run_id: str,
    *,
    skip: int = 0,
    limit: int = 50,
    agent_type: str | None = None,
) -> tuple[list[models.DecisionLog], int]:
    """Paginated decision-log query for the portal."""
    q = db.query(models.DecisionLog).filter(models.DecisionLog.run_id == run_id)
    if agent_type:
        q = q.filter(models.DecisionLog.agent_type == agent_type)
    total = q.count()
    items = q.order_by(models.DecisionLog.created_at).offset(skip).limit(limit).all()
    return items, total


def list_run_genome_summaries(
    db: Session,
    run_id: str,
    *,
    generation_index: int | None = None,
    origin: str | None = None,
    limit: int = 2000,
) -> list[dict[str, Any]]:
    """Lightweight list of all genomes in a run, projection-only.

    Used by the portal's run-wide lineage tree. Excludes the heavy
    `dsl_json` blob and validation folds — the user fetches those by
    clicking through to the genome detail page. At pop=30 × gens=30 a
    run has ~900 genomes; the projection keeps the payload to roughly
    100KB.

    Filters:
        generation_index: limit to one generation (None = all)
        origin: limit to one origin (None = all)
    """
    q = (
        db.query(models.StrategyGenome, models.Generation.index.label("gen_index"))
        .join(
            models.Generation,
            models.StrategyGenome.generation_id == models.Generation.id,
        )
        .filter(models.Generation.run_id == run_id)
    )
    if generation_index is not None:
        q = q.filter(models.Generation.index == generation_index)
    if origin is not None:
        q = q.filter(models.StrategyGenome.origin == origin)
    rows = (
        q.order_by(models.Generation.index, models.StrategyGenome.id).limit(limit).all()
    )

    return [
        {
            "id": g.id,
            "generation_index": gen_index,
            "parent_ids": g.parent_ids or [],
            "origin": g.origin.value if hasattr(g.origin, "value") else str(g.origin),
            "fitness": g.fitness,
            "validator_passed": g.validator_passed,
        }
        for g, gen_index in rows
    ]


# ─────────────────────────────────────────────────────────────────────────
# SQLAlchemy-backed EvolutionStatePersister (orchestrator dependency)
# ─────────────────────────────────────────────────────────────────────────


class SqlAlchemyPersister:
    """Real implementation of `agents.orchestrator.EvolutionStatePersister`.

    Owns the child process's DB session. The orchestrator runs inside a
    forked child; this class is constructed once at the start of that
    child, then receives every `update_run`/`save_generation`/etc. call.

    Each method commits its own transaction — the orchestrator doesn't
    know about transactions, and we want incremental durable writes
    (an interrupted run leaves a partial-but-readable state behind).
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    # ── update_run ──────────────────────────────────────────────────

    def update_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        current_generation: int | None = None,
        best_genome_id: str | None = None,
        llm_cost_usd: float | None = None,
        stop_reason: str | None = None,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        run = (
            self.db.query(models.EvolutionRun)
            .filter(models.EvolutionRun.id == run_id)
            .first()
        )
        if run is None:
            logger.error(
                "update_run for unknown run_id=%s — skipping (orchestrator bug?)",
                run_id,
            )
            return

        if status is not None:
            try:
                run.status = models.EvolutionRunStatus(status)
            except ValueError:
                logger.warning("ignoring unknown status=%s on run %s", status, run_id)
        if current_generation is not None:
            run.current_generation = current_generation
        if best_genome_id is not None:
            run.best_genome_id = best_genome_id
        if llm_cost_usd is not None:
            run.llm_cost_usd = llm_cost_usd
        if stop_reason is not None:
            run.stop_reason = stop_reason
        if started_at is not None:
            run.started_at = started_at
        if completed_at is not None:
            run.completed_at = completed_at

        self.db.commit()

    # ── save_generation ─────────────────────────────────────────────

    def save_generation(
        self,
        run_id: str,
        generation_index: int,
        genomes: list[Genome],
        *,
        llm_invoked: bool,
        llm_call_id: str | None,
        best_fitness: float | None,
    ) -> None:
        """Persist the generation row + every genome row.

        Mutates `genome.id` on each input genome to the assigned
        semantic key — the orchestrator relies on this for parent_ids
        recording in subsequent generations.
        """
        gen_id = generate_generation_id(run_id, generation_index)
        now = datetime.now(UTC)

        # Upsert the generation row. If we're re-running due to crash
        # recovery, the row may already exist — overwrite metadata.
        existing = (
            self.db.query(models.Generation)
            .filter(models.Generation.id == gen_id)
            .first()
        )
        if existing is None:
            generation = models.Generation(
                id=gen_id,
                run_id=run_id,
                index=generation_index,
                started_at=now,
                completed_at=now,
                llm_invoked=llm_invoked,
                llm_call_id=llm_call_id,
                best_fitness=best_fitness,
            )
            self.db.add(generation)
        else:
            existing.completed_at = now
            existing.llm_invoked = llm_invoked
            existing.llm_call_id = llm_call_id
            existing.best_fitness = best_fitness

        # Assign IDs and persist each genome
        for i, genome in enumerate(genomes):
            if genome.id is None:
                genome.id = generate_genome_id(gen_id, i)

            existing_genome = (
                self.db.query(models.StrategyGenome)
                .filter(models.StrategyGenome.id == genome.id)
                .first()
            )
            if existing_genome is not None:
                continue  # already persisted; skip

            origin_value = (
                genome.origin.value
                if hasattr(genome.origin, "value")
                else str(genome.origin)
            )
            row = models.StrategyGenome(
                id=genome.id,
                generation_id=gen_id,
                dsl_json=genome.spec.model_dump(mode="json"),
                parent_ids=list(genome.parent_ids),
                origin=models.GenomeOrigin(origin_value),
                fitness=(genome.fitness_value if genome.has_fitness() else None),
                # Per-fold training metrics + penalty breakdown. May be
                # None for elites that carried over without re-evaluation
                # or for genomes whose backtest failed before metrics
                # could be collected.
                metrics_json=(genome.metrics if genome.metrics else None),
                validator_passed=None,
                validation_failures=[],
                llm_reasoning=genome.llm_reasoning,
                llm_call_id=genome.llm_call_id,
            )
            self.db.add(row)

        self.db.commit()

    # ── save_validation ─────────────────────────────────────────────

    def save_validation(
        self,
        genome_id: str,
        folds: list[FoldEvaluation],
        *,
        validator_passed: bool,
        validation_failures: list[str],
    ) -> None:
        # Persist each fold row
        for fe in folds:
            fold_id = generate_validation_fold_id(genome_id, fe.fold.index)
            existing = (
                self.db.query(models.ValidationFold)
                .filter(models.ValidationFold.id == fold_id)
                .first()
            )
            if existing is not None:
                continue

            row = models.ValidationFold(
                id=fold_id,
                genome_id=genome_id,
                fold_index=fe.fold.index,
                fold_start=fe.fold.start,
                fold_end=fe.fold.end,
                is_oos=fe.fold.is_oos,
                sharpe=fe.metrics.get("sharpe"),
                max_drawdown=fe.metrics.get("max_drawdown"),
                hit_rate=fe.metrics.get("hit_rate"),
                n_trades=fe.metrics.get("n_trades"),
                cagr=fe.metrics.get("cagr"),
                passed_gate_b=fe.passed_gate_b,
                # v0.4.0: per-fold regime label. None when the orchestrator
                # couldn't compute it (e.g. candle load failed) — the UI
                # renders "—" in that case.
                regime_volatility=(
                    fe.regime.volatility if fe.regime is not None else None
                ),
                regime_trend=(fe.regime.trend if fe.regime is not None else None),
                regime_direction=(
                    fe.regime.direction if fe.regime is not None else None
                ),
            )
            self.db.add(row)

        # Update the genome's validator status
        genome = (
            self.db.query(models.StrategyGenome)
            .filter(models.StrategyGenome.id == genome_id)
            .first()
        )
        if genome is not None:
            genome.validator_passed = validator_passed
            genome.validation_failures = list(validation_failures)

        self.db.commit()

    # ── log_decision ────────────────────────────────────────────────

    def log_decision(
        self,
        run_id: str,
        *,
        agent_type: str,
        action: str,
        generation_index: int | None = None,
        inputs_json: dict[str, Any] | None = None,
        outputs_json: dict[str, Any] | None = None,
        reasoning_text: str | None = None,
        llm_call_id: str | None = None,
    ) -> None:
        # The orchestrator can log a decision *for* a future generation
        # (e.g. LLM injection happens between gen N-1 completion and gen N
        # evaluation, but the proposals are conceptually "for" gen N). If
        # gen N's row doesn't exist in the DB yet, the FK constraint on
        # decision_logs.generation_id would fail. Detect that case and
        # store generation_id=None — the decision still logs cleanly, and
        # callers can correlate via the action + outputs_json instead.
        generation_id: str | None = None
        if generation_index is not None:
            candidate_id = generate_generation_id(run_id, generation_index)
            exists = (
                self.db.query(models.Generation.id)
                .filter(models.Generation.id == candidate_id)
                .first()
                is not None
            )
            generation_id = candidate_id if exists else None

        row = models.DecisionLog(
            id=uuid.uuid4().hex,
            run_id=run_id,
            generation_id=generation_id,
            agent_type=agent_type,
            action=action,
            inputs_json=inputs_json,
            outputs_json=outputs_json,
            reasoning_text=reasoning_text,
            llm_call_id=llm_call_id,
        )
        self.db.add(row)
        self.db.commit()

    # ── find_prior_winners ──────────────────────────────────────────

    def find_prior_winners(
        self,
        *,
        instrument: str,
        granularity: str,
        limit: int,
        exclude_run_id: str | None = None,
        data_window_start: datetime | None = None,
        data_window_end: datetime | None = None,
        min_cagr_pct: float | None = None,
    ) -> list[dict]:
        """Thin wrapper around the module-level helper so the
        orchestrator can warm-start via its Persister Protocol."""
        return find_prior_winners(
            self.db,
            instrument=instrument,
            granularity=granularity,
            limit=limit,
            exclude_run_id=exclude_run_id,
            data_window_start=data_window_start,
            data_window_end=data_window_end,
            min_cagr_pct=min_cagr_pct,
        )


__all__ = [
    "SqlAlchemyPersister",
    "create_run",
    "find_orphaned_running_runs",
    "find_queued_runs",
    "generate_genome_id",
    "generate_generation_id",
    "generate_run_id",
    "generate_validation_fold_id",
    "get_genome_with_lineage",
    "get_run",
    "list_decisions",
    "list_generations",
    "list_run_genome_summaries",
    "list_runs",
    "stop_run",
]
