#!/usr/bin/env python3
"""Read-only wick-fill and Gate B replay over persisted walk-forward folds.

Run with PYTHONPATH=src and an explicit PostgreSQL DATABASE_URL in the
environment. Supply historical paper source genome IDs privately when their
run rows are no longer present. Output contains private staging identifiers.
"""

import argparse
import multiprocessing
import os
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import String, bindparam, text

from agentfx.agents.validator import (
    FoldEvaluation,
    _aggregate_oos_metrics,
    _oos_fold_floor_failures,
)
from agentfx.core.dsl import compiler
from agentfx.core.gates import GATE_B_THRESHOLDS, evaluate_gate_b
from agentfx.core.validation import WalkForwardFold
from agentfx.services import backtest


@dataclass
class Genome:
    id: str
    stored: bool | None
    spec: dict
    instrument: str
    granularity: str
    config: dict
    folds: list[WalkForwardFold]
    cohort: bool
    historical_paper: bool
    approved: bool

    def candle_window(self, fold):
        return self.instrument, self.granularity, fold.start, fold.end


@dataclass
class Score:
    sharpe: float | None
    aggregate_failures: list[str]
    fold_failures: list[str]

    @property
    def old_pass(self):
        return not self.aggregate_failures

    @property
    def new_pass(self):
        return self.old_pass and not self.fold_failures


@dataclass
class Replay:
    genome: Genome
    scores: tuple[Score, Score, Score] | None = None
    missing: str | None = None
    missing_training_folds: list[int] = field(default_factory=list)


def load_genomes(connection, before, paper_sources):
    rows = connection.execute(
        text("""
            WITH cohort AS (
                SELECT id FROM strategy_genomes
                WHERE validator_passed IS TRUE AND created_at < :before
                    AND dsl_json->'exit'->>'eval_mode' = 'wick'
                    AND CAST(dsl_json->'exit'->>'min_hold_bars' AS INTEGER) > 1
            ), historical_paper AS (
                SELECT DISTINCT strategy_genome_id FROM runs
                WHERE environment = 'PAPER' AND created_at < :before
                    AND strategy_genome_id IS NOT NULL
            ), approvals AS (
                SELECT DISTINCT strategy_genome_id FROM runs
                WHERE environment = 'PAPER' AND approved_at IS NOT NULL
                    AND status IN ('APPROVED', 'RUNNING', 'PAUSED')
                    AND strategy_genome_id IS NOT NULL
            )
            SELECT g.id, g.validator_passed, g.dsl_json, er.instrument,
                er.granularity, er.config_json, c.id IS NOT NULL AS cohort,
                h.strategy_genome_id IS NOT NULL AS historical_paper,
                a.strategy_genome_id IS NOT NULL AS approved
            FROM strategy_genomes g
            JOIN generations ge ON ge.id = g.generation_id
            JOIN evolution_runs er ON er.id = ge.run_id
            LEFT JOIN cohort c ON c.id = g.id
            LEFT JOIN historical_paper h ON h.strategy_genome_id = g.id
            LEFT JOIN approvals a ON a.strategy_genome_id = g.id
            WHERE c.id IS NOT NULL OR a.strategy_genome_id IS NOT NULL
                OR h.strategy_genome_id IS NOT NULL OR g.id IN :paper_sources
            ORDER BY g.id
        """).bindparams(bindparam("paper_sources", expanding=True, type_=String)),
        {"before": before, "paper_sources": sorted(paper_sources)},
    ).all()
    folds = defaultdict(list)
    for fold in connection.execute(
        text("""
            SELECT genome_id, fold_index, fold_start, fold_end, is_oos
            FROM validation_folds WHERE genome_id IN :ids
            ORDER BY genome_id, fold_index
        """).bindparams(bindparam("ids", expanding=True, type_=String)),
        {"ids": [row.id for row in rows]},
    ):
        folds[fold.genome_id].append(
            WalkForwardFold(
                fold.fold_index, fold.fold_start, fold.fold_end, fold.is_oos
            )
        )
    return [
        Genome(
            row.id,
            row.validator_passed,
            row.dsl_json,
            row.instrument,
            row.granularity,
            row.config_json,
            folds[row.id],
            row.cohort,
            row.historical_paper or row.id in paper_sources,
            row.approved,
        )
        for row in rows
    ]


def replay_fold(genome, fold, spread, *, gap_aware, shifted_trigger):
    apply_exits = compiler._apply_exits

    def apply_variant(
        raw_pos,
        close,
        high,
        low,
        exit_rules,
        pip_size,
        open_=None,
        *,
        wick_half_spread=0.0,
    ):
        return apply_exits(
            raw_pos,
            close,
            high,
            low,
            exit_rules,
            pip_size,
            open_=open_ if gap_aware else None,
            wick_half_spread=wick_half_spread if shifted_trigger else 0.0,
        )

    with patch.object(compiler, "_apply_exits", apply_variant):
        return backtest.run_backtest_from_db(
            instrument=genome.instrument,
            granularity=genome.granularity,
            start=fold.start,
            end=fold.end,
            strategy_name="dsl",
            strategy_params={"spec": genome.spec},
            spread_pips=spread,
            account_balance=float(genome.config.get("account_balance", 10000.0)),
            leverage=int(genome.config.get("leverage", 30)),
            capture_signals=True,
        )["metrics"]


def score_folds(folds, overrides):
    oos = [fold for fold in folds if fold.is_oos]
    aggregate = _aggregate_oos_metrics(oos)
    _, failures = evaluate_gate_b(aggregate, overrides=overrides)
    return Score(
        aggregate.get("sharpe"), failures, _oos_fold_floor_failures(oos, overrides)
    )


def replay_genome(genome):
    if not genome.folds or not any(fold.is_oos for fold in genome.folds):
        return Replay(genome, missing="No persisted out-of-sample folds")
    config = genome.config
    spread = config.get("effective_spread_pips_per_pair", {}).get(genome.instrument)
    if spread is None:
        spread = config.get("effective_spread_pips", config.get("spread_pips"))
    if spread is None:
        return Replay(genome, missing="No saved spread for the evolution run")
    overrides = {
        "min_cagr": float(config.get("min_cagr_pct", GATE_B_THRESHOLDS["min_cagr"])),
    }
    evaluations = ([], [], [])
    missing_training_folds = []
    for fold in genome.folds:
        try:
            for out, (gap_aware, shifted_trigger) in zip(
                evaluations, ((False, False), (True, False), (True, True)), strict=True
            ):
                metrics = replay_fold(
                    genome,
                    fold,
                    float(spread),
                    gap_aware=gap_aware,
                    shifted_trigger=shifted_trigger,
                )
                if fold.is_oos:
                    passed, failures = evaluate_gate_b(metrics, overrides=overrides)
                    out.append(FoldEvaluation(fold, metrics, passed, failures))
        except FileNotFoundError as exc:
            if fold.is_oos:
                return Replay(genome, missing=str(exc))
            missing_training_folds.append(fold.index)
    return Replay(
        genome,
        scores=tuple(score_folds(out, overrides) for out in evaluations),
        missing_training_folds=missing_training_folds,
    )


def replay_all(genomes, jobs):
    load = backtest.load_price_df_from_db
    candles = {}
    for genome in genomes:
        for fold in genome.folds:
            key = genome.candle_window(fold)
            if key not in candles:
                try:
                    candles[key] = load(*key)
                except FileNotFoundError:
                    candles[key] = None

    def cached_candles(instrument, granularity, start, end):
        df = candles[instrument, granularity, start, end]
        if df is None:
            raise FileNotFoundError(
                f"No candles for {instrument}/{granularity}: {start} – {end}"
            )
        return df

    # Freeze the same candles and financing across every fill variant.
    financing = {
        instrument: backtest._lookup_financing_rates(instrument)
        for instrument in sorted({genome.instrument for genome in genomes})
    }
    with (
        patch.object(backtest, "load_price_df_from_db", cached_candles),
        patch.object(backtest, "_lookup_financing_rates", financing.__getitem__),
    ):
        if jobs == 1:
            yield from map(replay_genome, genomes)
        else:
            with ProcessPoolExecutor(
                max_workers=jobs, mp_context=multiprocessing.get_context("fork")
            ) as executor:
                yield from executor.map(replay_genome, genomes)


def verdict(passed):
    return {True: "Pass", False: "Fail", None: "Unvalidated"}[passed]


def score_cell(score, new_rule=False):
    passed = score.new_pass if new_rule else score.old_pass
    sharpe = f"{score.sharpe:.3f}" if score.sharpe is not None else "n/a"
    failures = score.aggregate_failures + (score.fold_failures if new_rule else [])
    return f"{verdict(passed)} · {sharpe}" + (
        " — " + "; ".join(failures) if failures else ""
    )


def render_report(replays, before, unresolved_sources):
    counts = defaultdict(int)
    rows = []
    for replay in replays:
        g = replay.genome
        labels = ", ".join(
            label
            for included, label in (
                (g.cohort, "cohort"),
                (g.historical_paper, "original paper"),
                (g.approved, "current paper approval"),
            )
            if included
        )
        counts["cohort"] += g.cohort
        prefix = f"| `{g.id}` | {labels} | {verdict(g.stored)} | "
        if replay.scores is None:
            counts["missing"] += 1
            rows.append(prefix + f"— | — | — | — | — | {replay.missing} |")
            continue
        a, b, current = replay.scores
        causes = []
        if replay.missing_training_folds:
            indices = ", ".join(str(index) for index in replay.missing_training_folds)
            causes.append(f"No candles for training fold indices {indices}; OOS scored")
        if g.stored is not None and g.stored != a.old_pass:
            counts["baseline"] += 1
            causes.append("Stored ≠ A")
        for key, label, prior, after in (
            ("gap", "Gap-aware fill", a.old_pass, b.old_pass),
            ("spread", "Half-spread trigger", b.old_pass, current.old_pass),
            ("fold", "Fold floor", current.old_pass, current.new_pass),
        ):
            if prior != after:
                counts[key] += 1
                causes.append(f"{label}: {verdict(prior)} → {verdict(after)}")
        counts["approved_fail"] += g.approved and not current.new_pass
        counts["paper_fail"] += g.historical_paper and not current.new_pass
        rows.append(
            prefix
            + " | ".join(
                (
                    score_cell(a),
                    score_cell(b),
                    score_cell(b, True),
                    score_cell(current),
                    score_cell(current, True),
                    "; ".join(causes) or "—",
                )
            )
            + " |"
        )
    for source in sorted(unresolved_sources):
        rows.append(
            f"| `{source}` | original paper | Unknown | — | — | — | — | — | Genome not found |"
        )

    return "\n".join(
        [
            "# Wick genome re-validation",
            "",
            f"- Historical cohort (created before {before:%Y-%m-%d} UTC): {counts['cohort']}. "
            f"Unique genomes assessed: {len(replays) + len(unresolved_sources)}.",
            f"- Verdict flips from gap-aware fills: {counts['gap']}; from the new "
            f"fold rule on current fills: {counts['fold']}; from the half-spread "
            f"trigger under old Gate B: {counts['spread']}.",
            f"- Stored verdict mismatches vs A: {counts['baseline']}; "
            f"missing replay inputs: {counts['missing'] + len(unresolved_sources)}.",
            f"- Current paper-approved genomes failing C: {counts['approved_fail']}; "
            f"original paper source genomes failing C: {counts['paper_fail']}.",
            "",
            "A uses fixed stop/target fills and old Gate B. B changes only gap-aware "
            "fills; the B+floor column scores those same folds under new Gate B. "
            "Current-old adds the half-spread wick trigger without changing Gate B; "
            "C scores those same current-main folds under new Gate B.",
            "Each verdict is followed by median out-of-sample Sharpe and any failure "
            "reasons. The original candle ranges, saved run costs/thresholds and "
            "currently available candles and financing settings were used. Stored "
            "mismatches are not attributed to either rule change.",
            "No approvals, paper runs or staging records were changed.",
            "",
            "| Genome | Scope | Stored | A | B | B+floor | Current-old | C | Verdict changes |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            *rows,
            "",
        ]
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--before",
        type=date.fromisoformat,
        required=True,
        help="Exclusive UTC creation cutoff for historical genomes and paper runs",
    )
    parser.add_argument("--expected-cohort", type=int, required=True)
    parser.add_argument(
        "--paper-source-genome",
        action="append",
        default=[],
        help="Additional historical source genome, e.g. for a deleted paper run",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jobs", type=int, choices=range(1, 9), default=1)
    args = parser.parse_args()
    if not os.environ.get("DATABASE_URL", "").startswith(
        ("postgresql://", "postgresql+psycopg2://")
    ):
        parser.error("An explicit PostgreSQL DATABASE_URL is required")
    os.environ["PGOPTIONS"] = (
        os.environ.get("PGOPTIONS", "")
        + " -c default_transaction_read_only=on -c statement_timeout=60000"
    ).strip()
    os.environ.setdefault("PGCONNECT_TIMEOUT", "12")
    from agentfx.api.core.database import engine

    before = datetime.combine(args.before, time.min, tzinfo=UTC)
    sources = set(args.paper_source_genome)
    with engine.connect() as conn:
        if conn.exec_driver_sql("SHOW transaction_read_only").scalar_one() != "on":
            parser.error("PostgreSQL connection is not read-only")
        genomes = load_genomes(conn, before, sources)
    actual = sum(genome.cohort for genome in genomes)
    if actual != args.expected_cohort:
        parser.error(
            f"Found {actual} historical genomes, expected {args.expected_cohort}"
        )

    replays = []
    for replay in replay_all(genomes, args.jobs):
        replays.append(replay)
        print(f"Replayed {len(replays)}/{len(genomes)}", file=sys.stderr, flush=True)
    report = render_report(replays, before, sources - {g.id for g in genomes})
    with open(args.output, "x", encoding="utf-8", opener=_private_file) as handle:
        handle.write(report)


def _private_file(path, flags):
    return os.open(path, flags, 0o600)


if __name__ == "__main__":
    main()
