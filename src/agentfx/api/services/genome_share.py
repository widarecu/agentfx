"""Service layer for cross-user genome sharing (v0.7.x).

Two operations:

- **export_genome_payload**: reads an exporter-owned genome (verified
  by evolution_run.created_by == user.id), packs it into a
  `GenomeExportPayload`, computes a SHA-256 content hash over the
  canonical JSON (with `content_hash` omitted so it's a hash OF the
  content, not OF the hash), and returns.

- **import_genome_payload**: validates a payload (schema version,
  content hash, DSL parses under the current grammar), creates
  SHADOW rows scoped to the importer (new EvolutionRun +
  Generation + StrategyGenome + ValidationFold — all with new IDs
  so they can't collide with the exporter's if they're on the same
  DB), and returns the new IDs.

The shadow rows mean the importer's approval queue picks up the
genome naturally (`evolution_runs.created_by == importer.user_id`
gate) and the paper deploy path (`approve_and_deploy` →
`fold_length_days` computation) works without special-casing
imports.

Trust posture: content_hash is tamper-detection (bit-flip), not
authentication (impersonation). The exporter's email is embedded
verbatim; no signature. v2 could add ECDSA if impersonation becomes
a real threat.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from agentfx.core.dsl.grammar import StrategySpec

from .. import models
from ..schemas.genome_share import (
    SUPPORTED_SCHEMA_VERSIONS,
    GenomeExportCore,
    GenomeExportPayload,
    GenomeExportProvenance,
    GenomeExportedBy,
    GenomeImportResponse,
    ValidationFoldExport,
)


APP_VERSION = "0.7.x"
"""Recorded on exports so the importer can diagnose grammar/feature
gaps. Bump when adding a genome-shape-affecting feature."""


# ─────────────────────────────────────────────────────────────────────
# Content hash — canonical JSON of the payload minus `content_hash`
# ─────────────────────────────────────────────────────────────────────


def _js_normalize(obj):
    """Coerce values to match JavaScript's JSON round-trip semantics.

    Wilson UAT 2026-08-08: content_hash mismatched on a straight
    export-then-import because the browser silently rewrites
    `12.0` as `12` when it JSON.parse + JSON.stringify the payload
    on the way from the export download to the import POST. Python's
    `json` module preserves the `.0`; JS drops it. Hash of the two
    dicts differs.

    Fix: apply a JS-equivalent normalization to the hash input on
    BOTH sides. Whole-value floats become ints; nested dicts + lists
    are walked recursively. Sub-1 floats (0.005) and non-integer
    floats (9.884254...) are left alone — JS preserves those bytes.

    NOT a general-purpose normalizer; only used for hashing. The
    stored `dsl_json` / `metrics_json` on both sides keep their
    original types."""
    if isinstance(obj, float) and obj.is_integer():
        return int(obj)
    if isinstance(obj, dict):
        return {k: _js_normalize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_js_normalize(v) for v in obj]
    return obj


def _canonical_json_for_hash(payload_dict: dict) -> bytes:
    """Return the canonical JSON of `payload_dict` with the
    `content_hash` field STRIPPED — this is what gets hashed.

    Sorted keys + no whitespace = same output every time regardless
    of dict insertion order. UTF-8 encoded so hash bytes are stable
    across platforms. `_js_normalize` collapses whole-value floats
    to ints so JS-side round-tripping doesn't perturb the hash
    (see the docstring on `_js_normalize` for the exact scenario)."""
    stripped = {k: v for k, v in payload_dict.items() if k != "content_hash"}
    normalized = _js_normalize(stripped)
    return json.dumps(
        normalized, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")


def _compute_content_hash(payload_dict: dict) -> str:
    """`sha256:<hex>` over the canonical JSON of the payload minus
    `content_hash`. Match format that other tooling may parse."""
    return (
        f"sha256:{hashlib.sha256(_canonical_json_for_hash(payload_dict)).hexdigest()}"
    )


# ─────────────────────────────────────────────────────────────────────
# Export
# ─────────────────────────────────────────────────────────────────────


def export_genome_payload(
    db: Session,
    *,
    evolution_run_id: str,
    genome_id: str,
    user: models.User,
) -> GenomeExportPayload:
    """Build the export payload for one genome owned by `user`.

    Raises:
        ValueError: genome/run not found, or user doesn't own the
            evolution run.
    """
    run = (
        db.query(models.EvolutionRun)
        .filter(models.EvolutionRun.id == evolution_run_id)
        .one_or_none()
    )
    if run is None:
        raise ValueError(f"evolution_run {evolution_run_id} not found")
    if run.created_by != user.id:
        raise ValueError(
            f"evolution_run {evolution_run_id} is not owned by user {user.id}"
        )

    genome = (
        db.query(models.StrategyGenome)
        .filter(models.StrategyGenome.id == genome_id)
        .one_or_none()
    )
    if genome is None:
        raise ValueError(f"genome {genome_id} not found")

    # Verify genome belongs to the run (guards against cross-run
    # access by ID-guessing a valid genome_id).
    generation = (
        db.query(models.Generation)
        .filter(models.Generation.id == genome.generation_id)
        .one_or_none()
    )
    if generation is None or generation.run_id != evolution_run_id:
        raise ValueError(
            f"genome {genome_id} does not belong to evolution_run {evolution_run_id}"
        )

    validation_folds = (
        db.query(models.ValidationFold)
        .filter(models.ValidationFold.genome_id == genome_id)
        .order_by(models.ValidationFold.fold_index)
        .all()
    )

    # Data range lives inside config_json. Some legacy runs may not
    # have it — export nulls if absent so the importer's UI shows
    # "unknown" rather than choking on missing keys.
    cfg = run.config_json or {}
    data_start = cfg.get("data_start_date") or cfg.get("data_range_start")
    data_end = cfg.get("data_end_date") or cfg.get("data_range_end")

    provenance = GenomeExportProvenance(
        source_evolution_run_id=run.id,
        source_instrument=run.instrument,
        source_granularity=run.granularity,
        source_data_range_start=_parse_dt(data_start),
        source_data_range_end=_parse_dt(data_end),
    )

    exported_by = GenomeExportedBy(
        email=user.email,
        display_name=getattr(user, "full_name", None),
    )

    genome_core = GenomeExportCore(
        id=genome.id,
        dsl_json=genome.dsl_json,
        origin=(
            genome.origin.value
            if hasattr(genome.origin, "value")
            else str(genome.origin)
        ),
        fitness=genome.fitness,
        metrics_json=genome.metrics_json,
        validator_passed=genome.validator_passed,
        validation_failures=list(genome.validation_failures or []),
        llm_reasoning=genome.llm_reasoning,
    )

    fold_exports = [
        ValidationFoldExport(
            fold_index=f.fold_index,
            fold_start=f.fold_start,
            fold_end=f.fold_end,
            is_oos=f.is_oos,
            sharpe=f.sharpe,
            max_drawdown=f.max_drawdown,
            hit_rate=f.hit_rate,
            n_trades=f.n_trades,
            cagr=getattr(f, "cagr", None),
            passed_gate_b=getattr(f, "passed_gate_b", None),
            regime_direction=getattr(f, "regime_direction", None),
            regime_volatility=getattr(f, "regime_volatility", None),
            regime_trend=getattr(f, "regime_trend", None),
        )
        for f in validation_folds
    ]

    # Build the payload WITHOUT content_hash first, dump it, hash it,
    # then set the hash.
    partial = GenomeExportPayload(
        schema_version=1,
        exported_at=datetime.now(UTC),
        exported_by=exported_by,
        app_version=APP_VERSION,
        genome=genome_core,
        validation_folds=fold_exports,
        provenance=provenance,
        content_hash="sha256:pending",
    )
    partial_dict = partial.model_dump(mode="json")
    partial_dict["content_hash"] = _compute_content_hash(partial_dict)
    return GenomeExportPayload.model_validate(partial_dict)


# ─────────────────────────────────────────────────────────────────────
# Import
# ─────────────────────────────────────────────────────────────────────


class GenomeImportError(ValueError):
    """Raised for any validation failure during import. Router maps
    to 400 with the message. Subclass of ValueError so existing
    ValueError handlers cover it."""


def import_genome_payload(
    db: Session,
    *,
    payload: GenomeExportPayload,
    importer_user: models.User,
) -> GenomeImportResponse:
    """Validate a payload and create shadow rows scoped to
    `importer_user`. Returns the new (shadow) IDs.

    Raises:
        GenomeImportError: schema unsupported, content hash mismatch,
            DSL parse failure, etc.
    """
    # ── 1. Schema version ─────────────────────────────────────────
    if payload.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise GenomeImportError(
            f"schema_version {payload.schema_version} not supported "
            f"(this build supports {SUPPORTED_SCHEMA_VERSIONS})"
        )

    # ── 2. Content hash ───────────────────────────────────────────
    payload_dict = payload.model_dump(mode="json")
    provided_hash = payload_dict.pop("content_hash", None)
    expected_hash = _compute_content_hash({**payload_dict, "content_hash": ""})
    if provided_hash != expected_hash:
        raise GenomeImportError(
            f"content_hash mismatch — payload may be corrupted or tampered. "
            f"Provided: {provided_hash}, expected: {expected_hash}"
        )

    # ── 3. DSL grammar validation ────────────────────────────────
    # Parses the JSON into a StrategySpec — catches unknown
    # indicators, malformed comparisons, unsupported grammar
    # versions. If any of these fire, we reject cleanly instead of
    # deferring the failure to first-tick paper execution.
    try:
        StrategySpec.model_validate(payload.genome.dsl_json)
    except Exception as e:  # noqa: BLE001 — Pydantic raises many types
        raise GenomeImportError(
            f"DSL failed static validation: {e}. The exporter's build may "
            f"have used a grammar feature not present in this build."
        ) from e

    # ── 4. Shadow row creation ────────────────────────────────────
    # Timestamped-suffixed IDs so importing the same genome twice
    # (or a different user importing the same genome later) each
    # produces its own row without collision. `imported_` prefix
    # keeps them visually distinct in DB dumps + logs.
    ts_suffix = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    shadow_run_id = f"imported_{ts_suffix}"
    shadow_gen_id = f"gen_{shadow_run_id}_0"
    shadow_genome_id = f"genome_{shadow_gen_id}_from_{payload.genome.id[-20:]}"

    now = datetime.now(UTC)

    # Preserve the exporter's data range in config_json so the
    # importer's backtest window selector shows sensible dates.
    cfg = {
        "_import_source": {
            "source_evolution_run_id": payload.provenance.source_evolution_run_id,
            "source_instrument": payload.provenance.source_instrument,
            "source_granularity": payload.provenance.source_granularity,
            "source_data_range_start": _iso(payload.provenance.source_data_range_start),
            "source_data_range_end": _iso(payload.provenance.source_data_range_end),
            "exported_at": _iso(payload.exported_at),
            "exported_by_email": payload.exported_by.email,
            "exported_by_display_name": payload.exported_by.display_name,
            "app_version": payload.app_version,
            "schema_version": payload.schema_version,
        }
    }
    if payload.provenance.source_data_range_start:
        cfg["data_start_date"] = _iso(payload.provenance.source_data_range_start)
    if payload.provenance.source_data_range_end:
        cfg["data_end_date"] = _iso(payload.provenance.source_data_range_end)

    shadow_run = models.EvolutionRun(
        id=shadow_run_id,
        status=models.EvolutionRunStatus.COMPLETED,
        instrument=payload.provenance.source_instrument,
        granularity=payload.provenance.source_granularity,
        config_json=cfg,
        stop_conditions_json={},
        current_generation=1,
        llm_cost_usd=0,
        created_by=importer_user.id,
        imported_from_email=payload.exported_by.email,
        imported_from_run_id=payload.provenance.source_evolution_run_id,
        created_at=now,
        started_at=now,
        completed_at=now,
    )
    db.add(shadow_run)

    shadow_gen = models.Generation(
        id=shadow_gen_id,
        run_id=shadow_run_id,
        index=0,
        started_at=now,
        completed_at=now,
        best_fitness=payload.genome.fitness,
    )
    db.add(shadow_gen)

    origin_enum = _coerce_origin(payload.genome.origin)
    shadow_genome = models.StrategyGenome(
        id=shadow_genome_id,
        generation_id=shadow_gen_id,
        dsl_json=payload.genome.dsl_json,
        parent_ids=[],  # imports have no parents in the importer's tree
        origin=origin_enum,
        fitness=payload.genome.fitness,
        metrics_json=payload.genome.metrics_json,
        validator_passed=payload.genome.validator_passed,
        validation_failures=payload.genome.validation_failures,
        llm_reasoning=payload.genome.llm_reasoning,
    )
    db.add(shadow_genome)

    for fold in payload.validation_folds:
        shadow_fold = models.ValidationFold(
            id=f"fold_{shadow_genome_id}_{fold.fold_index}",
            genome_id=shadow_genome_id,
            fold_index=fold.fold_index,
            fold_start=fold.fold_start,
            fold_end=fold.fold_end,
            is_oos=fold.is_oos,
            sharpe=fold.sharpe,
            max_drawdown=fold.max_drawdown,
            hit_rate=fold.hit_rate,
            n_trades=fold.n_trades,
        )
        # Optional columns — set defensively (older DB schemas may
        # not have them on `ValidationFold`).
        for optional_field in (
            "cagr",
            "passed_gate_b",
            "regime_direction",
            "regime_volatility",
            "regime_trend",
        ):
            if hasattr(shadow_fold, optional_field):
                setattr(shadow_fold, optional_field, getattr(fold, optional_field))
        db.add(shadow_fold)

    db.commit()

    return GenomeImportResponse(
        genome_id=shadow_genome_id,
        evolution_run_id=shadow_run_id,
        approval_queue_url="/paper/approvals",
    )


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _parse_dt(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt
        except ValueError:
            return None
    return None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _coerce_origin(origin_str: str) -> models.GenomeOrigin:
    """String → GenomeOrigin enum. Falls back to 'seed' if the
    exporter used an origin value this build doesn't recognise
    (defensive; grammar version check should catch most drift)."""
    for member in models.GenomeOrigin:
        if member.value == origin_str:
            return member
    return models.GenomeOrigin.SEED
