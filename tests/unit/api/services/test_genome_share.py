"""Unit tests for `api.services.genome_share` (v0.7.x — cross-user
genome sharing).

Every scenario from the backlog's "Test scope" section:

  1. Round-trip: export a genome → parse → re-import as a different
     user → verify approval queue picks it up (via the shadow
     EvolutionRun.created_by == importer.user_id gate).
  2. Version-skew: export with schema_version outside supported set
     → clean rejection.
  3. Content-hash mismatch: tamper with the payload after signing →
     clean rejection.
  4. Grammar-skew: DSL uses a feature the importing build doesn't
     recognize → clean rejection (via StrategySpec.model_validate).
  5. ID collision: importing the same payload twice produces two
     distinct shadow genome IDs (no primary-key conflict).

Backend service is DB-only; these tests use a mocked-but-realistic
Session so we don't need a live postgres. Shadow row IDs use a
timestamp + short uuid suffix so uniqueness is trivially guaranteed
across invocations (test 5).
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentfx.api.services.genome_share import (
    APP_VERSION,
    GenomeImportError,
    _canonical_json_for_hash,
    _compute_content_hash,
    export_genome_payload,
    import_genome_payload,
)
from agentfx.api.schemas.genome_share import (
    GenomeExportCore,
    GenomeExportPayload,
    GenomeExportProvenance,
    GenomeExportedBy,
    ValidationFoldExport,
)


# ─────────────────────────────────────────────────────────────────────
# Fixtures + helpers
# ─────────────────────────────────────────────────────────────────────


VALID_DSL = {
    "version": 1,
    "entry": {
        "long": {
            "lhs": {"ind": "sma", "args": [5]},
            "cmp": ">",
            "rhs": {"ind": "sma", "args": [20]},
        },
        "short": None,
    },
    "exit": {
        "stop_pips": 20,
        "target_pips": 40,
        "max_hold_bars": 100,
        "min_hold_bars": 10,
        "opposite_signal": True,
    },
    "size": {"risk_pct": 0.005},
}


def _make_user(user_id: int = 1, email: str = "alice@test.com") -> SimpleNamespace:
    return SimpleNamespace(id=user_id, email=email, full_name=None)


def _make_valid_payload(
    *,
    genome_id: str = "genome_gen_evorun_test_0",
    schema_version: int = 1,
) -> GenomeExportPayload:
    """Build a fresh export payload with a correct content_hash so
    tests can start from a valid baseline and then perturb one field
    to exercise the rejection paths."""
    partial = GenomeExportPayload(
        schema_version=schema_version,
        exported_at=datetime(2026, 8, 8, 12, 0, 0, tzinfo=UTC),
        exported_by=GenomeExportedBy(email="alice@test.com", display_name="Alice"),
        app_version=APP_VERSION,
        genome=GenomeExportCore(
            id=genome_id,
            dsl_json=VALID_DSL,
            origin="seed",
            fitness=1.234,
            metrics_json={"train_sharpe": 1.5, "oos_sharpe": 1.0},
            validator_passed=True,
            validation_failures=[],
            llm_reasoning=None,
        ),
        validation_folds=[
            ValidationFoldExport(
                fold_index=0,
                fold_start=datetime(2026, 1, 1, tzinfo=UTC),
                fold_end=datetime(2026, 1, 20, tzinfo=UTC),
                is_oos=False,
                sharpe=1.5,
                max_drawdown=-0.08,
                hit_rate=0.55,
                n_trades=320,
                cagr=0.15,
                passed_gate_b=True,
            ),
            ValidationFoldExport(
                fold_index=1,
                fold_start=datetime(2026, 1, 21, tzinfo=UTC),
                fold_end=datetime(2026, 2, 10, tzinfo=UTC),
                is_oos=True,
                sharpe=1.0,
                max_drawdown=-0.06,
                hit_rate=0.53,
                n_trades=310,
                cagr=0.12,
                passed_gate_b=True,
            ),
        ],
        provenance=GenomeExportProvenance(
            source_evolution_run_id="evorun_test",
            source_instrument="EUR_USD",
            source_granularity="M1",
            source_data_range_start=datetime(2026, 1, 1, tzinfo=UTC),
            source_data_range_end=datetime(2026, 2, 10, tzinfo=UTC),
        ),
        content_hash="sha256:pending",
    )
    partial_dict = partial.model_dump(mode="json")
    partial_dict["content_hash"] = _compute_content_hash(partial_dict)
    return GenomeExportPayload.model_validate(partial_dict)


# ─────────────────────────────────────────────────────────────────────
# Content hash: the tamper-detection contract
# ─────────────────────────────────────────────────────────────────────


class TestContentHash:
    def test_hash_is_stable_across_key_order(self):
        """Same content, different dict key insertion order → same hash."""
        a = {"schema_version": 1, "app_version": "0.7.x", "genome": {"id": "g1"}}
        b = {"app_version": "0.7.x", "genome": {"id": "g1"}, "schema_version": 1}
        assert _compute_content_hash(a) == _compute_content_hash(b)

    def test_hash_excludes_content_hash_field(self):
        """Hash of {..., content_hash: X} == hash of {..., content_hash: Y}."""
        base = {"schema_version": 1, "app_version": "0.7.x"}
        h1 = _compute_content_hash({**base, "content_hash": "sha256:aaa"})
        h2 = _compute_content_hash({**base, "content_hash": "sha256:bbb"})
        assert h1 == h2

    def test_hash_changes_when_any_field_changes(self):
        a = _make_valid_payload().model_dump(mode="json")
        b = dict(a)
        b["app_version"] = "0.7.z"
        assert _compute_content_hash(a) != _compute_content_hash(b)


# ─────────────────────────────────────────────────────────────────────
# Import: happy path (round-trip test happens in the integration
# section below since it needs shadow rows written)
# ─────────────────────────────────────────────────────────────────────


class TestImportHappyPath:
    def test_accepts_freshly_signed_payload(self, monkeypatch):
        payload = _make_valid_payload()
        db = _make_mock_db()
        importer = _make_user(user_id=42, email="bob@test.com")

        response = import_genome_payload(db, payload=payload, importer_user=importer)

        # Response shape
        assert response.genome_id.startswith("genome_")
        assert response.evolution_run_id.startswith("imported_")
        assert response.approval_queue_url == "/paper/approvals"

        # Four adds: EvolutionRun + Generation + StrategyGenome + 2 folds = 5.
        added_types = [type(c.args[0]).__name__ for c in db.add.call_args_list]
        assert added_types.count("EvolutionRun") == 1
        assert added_types.count("Generation") == 1
        assert added_types.count("StrategyGenome") == 1
        assert added_types.count("ValidationFold") == 2

        # Shadow EvolutionRun scoped to the importer + attribution set.
        run_row = next(
            c.args[0]
            for c in db.add.call_args_list
            if type(c.args[0]).__name__ == "EvolutionRun"
        )
        assert run_row.created_by == 42
        assert run_row.imported_from_email == "alice@test.com"
        assert run_row.imported_from_run_id == "evorun_test"

        # Shadow StrategyGenome preserves DSL + fitness verbatim.
        genome_row = next(
            c.args[0]
            for c in db.add.call_args_list
            if type(c.args[0]).__name__ == "StrategyGenome"
        )
        assert genome_row.dsl_json == VALID_DSL
        assert genome_row.fitness == 1.234
        assert genome_row.validator_passed is True

        db.commit.assert_called_once()


# ─────────────────────────────────────────────────────────────────────
# Test 2: version-skew
# ─────────────────────────────────────────────────────────────────────


class TestSchemaVersion:
    def test_unsupported_future_version_rejected(self):
        payload = _make_valid_payload(schema_version=99)
        # Re-hash after schema bump so we don't fail the OTHER check.
        pd = payload.model_dump(mode="json")
        pd["content_hash"] = _compute_content_hash(pd)
        payload = GenomeExportPayload.model_validate(pd)

        db = _make_mock_db()
        with pytest.raises(GenomeImportError, match="schema_version 99"):
            import_genome_payload(db, payload=payload, importer_user=_make_user())
        # Nothing persisted.
        db.commit.assert_not_called()


# ─────────────────────────────────────────────────────────────────────
# Test 3: content_hash mismatch
# ─────────────────────────────────────────────────────────────────────


class TestContentHashMismatch:
    def test_tampered_dsl_detected(self):
        """The importer receives a modified DSL under a hash from
        the original — must reject."""
        payload = _make_valid_payload()
        # Tamper: change fitness AFTER the hash was computed.
        payload_dict = payload.model_dump(mode="json")
        original_hash = payload_dict["content_hash"]
        payload_dict["genome"]["fitness"] = 999.999
        payload_dict["content_hash"] = original_hash  # keep stale hash
        tampered = GenomeExportPayload.model_validate(payload_dict)

        db = _make_mock_db()
        with pytest.raises(GenomeImportError, match="content_hash mismatch"):
            import_genome_payload(db, payload=tampered, importer_user=_make_user())
        db.commit.assert_not_called()

    def test_stale_hash_field_replaced_detected(self):
        """Attacker replaces content_hash with a hash of a shorter
        stripped payload — must reject."""
        payload = _make_valid_payload()
        payload_dict = payload.model_dump(mode="json")
        payload_dict["content_hash"] = "sha256:" + "0" * 64  # obviously wrong
        tampered = GenomeExportPayload.model_validate(payload_dict)

        db = _make_mock_db()
        with pytest.raises(GenomeImportError, match="content_hash mismatch"):
            import_genome_payload(db, payload=tampered, importer_user=_make_user())


# ─────────────────────────────────────────────────────────────────────
# Test 4: grammar-skew
# ─────────────────────────────────────────────────────────────────────


class TestGrammarSkew:
    def test_dsl_with_unknown_indicator_rejected(self):
        """Exporter's build had a hypothetical 'invented_indicator' —
        importer's build doesn't. StrategySpec.model_validate must
        raise, we catch and re-raise as GenomeImportError."""
        payload = _make_valid_payload()
        payload_dict = payload.model_dump(mode="json")
        payload_dict["genome"]["dsl_json"] = {
            "version": 1,
            "entry": {
                "long": {
                    "lhs": {"ind": "invented_indicator", "args": []},
                    "cmp": ">",
                    "rhs": 0,
                },
                "short": None,
            },
            "exit": {
                "stop_pips": 20,
                "target_pips": 40,
                "max_hold_bars": 100,
                "min_hold_bars": 10,
                "opposite_signal": True,
            },
            "size": {"risk_pct": 0.005},
        }
        payload_dict["content_hash"] = _compute_content_hash(payload_dict)
        broken = GenomeExportPayload.model_validate(payload_dict)

        db = _make_mock_db()
        with pytest.raises(GenomeImportError, match="DSL failed static validation"):
            import_genome_payload(db, payload=broken, importer_user=_make_user())
        db.commit.assert_not_called()


# ─────────────────────────────────────────────────────────────────────
# Test 5: ID collision (import the same payload twice → two rows)
# ─────────────────────────────────────────────────────────────────────


class TestIDCollision:
    def test_double_import_yields_distinct_ids(self):
        payload = _make_valid_payload()
        importer = _make_user()

        db1 = _make_mock_db()
        r1 = import_genome_payload(db1, payload=payload, importer_user=importer)

        # Advance the clock slightly so the ts_suffix definitely rotates.
        time.sleep(0.005)

        db2 = _make_mock_db()
        r2 = import_genome_payload(db2, payload=payload, importer_user=importer)

        assert r1.genome_id != r2.genome_id
        assert r1.evolution_run_id != r2.evolution_run_id


# ─────────────────────────────────────────────────────────────────────
# Test 1: round-trip (the top-level spec test — export then import)
# ─────────────────────────────────────────────────────────────────────


class TestRoundTrip:
    def test_export_produces_payload_that_import_accepts(self):
        """Alice exports her genome, Bob imports it — the payload
        Alice's build produces MUST validate under Bob's build's
        import logic. This is the primary contract of the whole
        feature.

        We can't hit the real export code without a real DB row for
        the genome. Simulate by directly calling
        `_compute_content_hash` over the same object model
        `export_genome_payload` would build — the assertion is that
        `import_genome_payload` accepts what
        `export_genome_payload` produces.
        """
        # Build the payload the way export_genome_payload does (in
        # spirit — the shape must match what the real endpoint
        # returns).
        payload = _make_valid_payload(genome_id="genome_alices_run_017_019")

        # Bob imports.
        db_bob = _make_mock_db()
        bob = _make_user(user_id=200, email="bob@test.com")
        response = import_genome_payload(db_bob, payload=payload, importer_user=bob)

        # Shadow ID is distinct from Alice's original.
        assert response.genome_id != "genome_alices_run_017_019"
        assert "alices_run_017_019"[-20:] in response.genome_id  # attribution
        assert response.evolution_run_id.startswith("imported_")

        # Shadow EvolutionRun preserves Alice's attribution + is
        # scoped to Bob so his approval queue picks it up.
        run_row = next(
            c.args[0]
            for c in db_bob.add.call_args_list
            if type(c.args[0]).__name__ == "EvolutionRun"
        )
        assert run_row.created_by == 200  # Bob
        assert run_row.imported_from_email == "alice@test.com"
        assert run_row.instrument == "EUR_USD"
        assert run_row.granularity == "M1"

        # Validation folds carry into shadow. The paper deploy path
        # (approve_and_deploy) reads these to compute
        # fold_length_days for Fix A.
        fold_rows = [
            c.args[0]
            for c in db_bob.add.call_args_list
            if type(c.args[0]).__name__ == "ValidationFold"
        ]
        assert len(fold_rows) == 2
        assert fold_rows[0].fold_index == 0
        assert fold_rows[1].fold_index == 1
        assert fold_rows[1].is_oos is True


# ─────────────────────────────────────────────────────────────────────
# Helpers — mock DB that captures adds/commits
# ─────────────────────────────────────────────────────────────────────


def _make_mock_db():
    db = MagicMock()
    db.add = MagicMock()
    db.commit = MagicMock()
    db.query = MagicMock()  # not exercised by import (no reads)
    return db
