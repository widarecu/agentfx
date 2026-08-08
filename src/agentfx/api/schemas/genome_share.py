"""Schemas for cross-user genome sharing (import/export) — v0.7.x.

See planning/BACKLOG.md "v0.7.x — Cross-user genome sharing" for the
full spec. Payload structure is schema-versioned so the importer can
reject files from incompatible builds cleanly instead of 500ing.

Trust posture (v1): the exporter's email is embedded but NOT
cryptographically verified. Content hash is SHA-256 of the canonical
JSON with the `content_hash` field itself omitted; catches tampering
but doesn't authenticate the sender. Sufficient for the
"share with a friend" workflow.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from .base import BaseSchema


SUPPORTED_SCHEMA_VERSIONS: tuple[int, ...] = (1,)
"""Import payloads with `schema_version` outside this tuple are
rejected with a clear message. Bump when adding a required field or
changing wire semantics; keep the older reader logic around for at
least one release."""


class ValidationFoldExport(BaseSchema):
    """One walk-forward fold's metrics — duplicated on import so the
    fold-anchored paper runner (Fix A) can read the same window
    lengths at deploy time."""

    fold_index: int
    fold_start: datetime
    fold_end: datetime
    is_oos: bool
    sharpe: float | None = None
    max_drawdown: float | None = None
    hit_rate: float | None = None
    n_trades: int | None = None
    cagr: float | None = None
    passed_gate_b: bool | None = None
    regime_direction: str | None = None
    regime_volatility: str | None = None
    regime_trend: str | None = None


class GenomeExportCore(BaseSchema):
    """The genome fields the importer needs to deploy the strategy.

    Everything necessary to render lineage, evaluate + approve, then
    deploy to paper. Excludes exporter-owned metadata (creator IDs,
    child genome IDs, etc.)."""

    id: str = Field(..., min_length=1, max_length=200)
    dsl_json: dict
    origin: str
    """Preserved as a string ("llm" / "mutation" / "crossover" / "seed"
    / "warm_start") so the importer sees where it came from. Not
    coerced to the local GenomeOrigin enum until DB insert."""

    fitness: float | None = None
    metrics_json: dict | None = None
    validator_passed: bool | None = None
    validation_failures: list = Field(default_factory=list)
    llm_reasoning: str | None = None


class GenomeExportProvenance(BaseSchema):
    """Where the genome came from, purely for display on the
    importer's side. Never dereferenced (source_evolution_run_id
    lives in the EXPORTER's DB)."""

    source_evolution_run_id: str
    source_instrument: str
    source_granularity: str
    source_data_range_start: datetime | None = None
    source_data_range_end: datetime | None = None


class GenomeExportedBy(BaseSchema):
    """Exporter attribution. Email is NOT authenticated in v1 —
    trust is out-of-band ("Wilson sent me the file")."""

    email: str = Field(..., min_length=1, max_length=200)
    display_name: str | None = Field(default=None, max_length=200)


class GenomeExportPayload(BaseSchema):
    """The complete `.agenome.json` file contents.

    Both the response of GET /export and the request body of POST
    /import. Schema-versioned so builds can evolve independently.
    """

    schema_version: int = Field(default=1, ge=1)
    exported_at: datetime
    exported_by: GenomeExportedBy
    app_version: str = Field(..., min_length=1, max_length=40)
    genome: GenomeExportCore
    validation_folds: list[ValidationFoldExport] = Field(default_factory=list)
    provenance: GenomeExportProvenance
    content_hash: str = Field(..., min_length=1, max_length=200)
    """`sha256:...` — SHA-256 of the canonical JSON of this payload
    with `content_hash` omitted. Recomputed on import; mismatch
    rejects the payload."""


class GenomeImportResponse(BaseSchema):
    """Return value of POST /genomes/import."""

    genome_id: str
    """The importer-side shadow genome id (new id — never collides
    with the exporter's)."""

    evolution_run_id: str
    """The importer-side shadow evolution_run_id (also new)."""

    approval_queue_url: str
    """Relative URL the frontend can push to so the user lands on
    the queue and sees their new genome ready to approve."""
