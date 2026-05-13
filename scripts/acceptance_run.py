#!/usr/bin/env python3
"""Acceptance verification harness for the v0.3.0 Autonomous Strategy Loop.

Runs a full evolution run against the live test stack (no stubs) and
dumps observable output for each of the 5 acceptance criteria from the
plan. Intended for human review before merging the v0.3.0 branch.

Pre-requisites (you must set these up manually):

  1. Docker test stack running: `make docker-up PROFILE=test`
  2. Test users seeded:
       docker compose --profile test exec backend-test \
         python scripts/seed_test_data.py
  3. Test candles seeded (synthetic, deterministic):
       docker compose --profile test exec backend-test \
         python scripts/seed_test_candles.py
  4. ANTHROPIC_API_KEY in .env.test if you want real LLM injection.
     Without it the orchestrator runs without LLM (criterion 5 is N/A).

The script:
  - Logs in as the TRADER test user
  - POSTs a small evolution run (population=10, max_generations=4, M1 EUR_USD)
  - Polls /evolution-runs/{id} every 3s until status is terminal
  - Dumps generations, top genomes, validation folds, decision log
  - Prints a per-criterion summary at the end

Usage:
  python scripts/acceptance_run.py              # run + dump
  python scripts/acceptance_run.py --quick      # smaller config, faster
  python scripts/acceptance_run.py --no-llm     # force LLM injector off
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
import time
from datetime import datetime, timezone
from typing import Any

import requests

BASE_URL = os.environ.get("AGENTFX_BASE_URL", "http://localhost:8001/api/v1")
TRADER_EMAIL = os.environ.get("ACCEPTANCE_USER", "trader@example.com")
TRADER_PASSWORD = os.environ.get("ACCEPTANCE_PASSWORD", "traderpassword")
POLL_INTERVAL_SECONDS = 3
TERMINAL_STATUSES = {"completed", "failed", "stopped"}


# ─────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────


def hr(title: str = "") -> None:
    bar = "─" * 70
    if title:
        print(f"\n{bar}\n  {title}\n{bar}")
    else:
        print(bar)


def fmt_dt(s: str | None) -> str:
    if not s:
        return "—"
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).strftime("%H:%M:%S")
    except ValueError:
        return s


def login() -> str:
    r = requests.post(
        f"{BASE_URL}/auth/login",
        data={"username": TRADER_EMAIL, "password": TRADER_PASSWORD},
        timeout=10,
    )
    if r.status_code != 200:
        raise SystemExit(
            f"Login failed ({r.status_code}): {r.text}\n"
            f"Did you seed test users? "
            f"`docker compose --profile test exec backend-test "
            f"python scripts/seed_test_data.py`"
        )
    return r.json()["access_token"]


def api_get(path: str, token: str, **params) -> Any:
    r = requests.get(
        f"{BASE_URL}{path}",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def api_post(path: str, token: str, json_body: dict) -> Any:
    r = requests.post(
        f"{BASE_URL}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=json_body,
        timeout=30,
    )
    if not r.ok:
        try:
            err = r.json()
        except Exception:
            err = r.text
        raise SystemExit(
            f"POST {path} failed ({r.status_code}): {json.dumps(err, indent=2)}"
        )
    return r.json()


# ─────────────────────────────────────────────────────────────────────────
# Run lifecycle
# ─────────────────────────────────────────────────────────────────────────


def kick_off_run(token: str, *, quick: bool, no_llm: bool) -> str:
    """Create a small evolution run + return the run_id."""
    population_size = 5 if quick else 10
    max_generations = 2 if quick else 4
    body = {
        "instrument": "EUR_USD",
        "granularity": "M1",
        "data_years": 1,
        "config": {
            "population_size": population_size,
            "llm_injection_every_n": 2,  # injects at gen 2 (and 4 if reached)
            "n_llm_proposals": 3,
            "rng_seed": 7,
        },
        "stop_conditions": {
            "max_generations": max_generations,
            "max_wall_clock_seconds": 1800,
            "target_fitness": 999.0,  # never trips — runs the full schedule
            "no_improvement_generations": 99,
        },
        "llm_budget_usd": 0.50 if quick else 2.00,
    }
    if no_llm:
        body["llm_budget_usd"] = 0.0  # zero budget = injector skips all calls

    print("POST /evolution-runs body:")
    print(textwrap.indent(json.dumps(body, indent=2), "  "))
    created = api_post("/evolution-runs", token, body)
    run_id = created["run_id"]
    print(f"\n→ scheduled {run_id} (status={created['status']})")
    return run_id


def poll_until_terminal(run_id: str, token: str) -> dict:
    """Poll the run every POLL_INTERVAL_SECONDS until status is terminal.

    Streams a one-line status update every iteration so the human watching
    can see progress.
    """
    started = time.time()
    last_gen = -1
    while True:
        try:
            run = api_get(f"/evolution-runs/{run_id}", token)
        except requests.HTTPError as e:
            print(f"  poll error: {e}", file=sys.stderr)
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        status = run["status"]
        gen = run.get("current_generation", 0)
        cost = run.get("llm_cost_usd", "0")
        elapsed = int(time.time() - started)
        if gen != last_gen or elapsed % 15 == 0:
            print(f"  [{elapsed:>4d}s] status={status:9s} gen={gen} llm_cost=${cost}")
            last_gen = gen

        if status in TERMINAL_STATUSES:
            return run

        time.sleep(POLL_INTERVAL_SECONDS)


# ─────────────────────────────────────────────────────────────────────────
# Acceptance criteria evaluation
# ─────────────────────────────────────────────────────────────────────────


def evaluate_criteria(
    run: dict, generations: list, decisions: list, best: dict | None
) -> list[tuple[str, bool, str]]:
    """Return a list of (criterion_label, passed, note) tuples."""
    results = []

    # Criterion 1: cron declaration exists (verified by file presence)
    cron_in_toml = False
    try:
        with open("railway.toml") as f:
            cron_in_toml = "trigger_nightly_evolution.py" in f.read()
    except FileNotFoundError:
        pass
    results.append(
        (
            "1. Scheduled Railway cron declared",
            cron_in_toml,
            "railway.toml [[deploy.cronJobs]] present"
            if cron_in_toml
            else "missing — add [[deploy.cronJobs]] to railway.toml",
        )
    )

    # Criterion 2: run completes unattended and persists state
    status = run.get("status")
    completed = status == "completed"
    stop_reason = run.get("stop_reason")
    results.append(
        (
            "2. Run completes unattended",
            completed,
            f"status={status} stop_reason={stop_reason} "
            f"generations_persisted={len(generations)}",
        )
    )

    # Criterion 3: at least one validated genome differs structurally from seeds
    # We check if there's any genome marked validator_passed=True.
    # The validator only ran on the run-best genome (v1 behaviour).
    has_validated = best is not None and best.get("validator_passed") is True
    structurally_different = False
    if has_validated:
        # Best genome's spec must differ from any of the 3 known seeds.
        # We accept "different" as any non-default fast/slow/window value
        # or different indicator combination.
        dsl = best["dsl_json"]
        long = (dsl.get("entry") or {}).get("long") or {}
        # Seed MA = sma(20) > sma(50); seed RSI = rsi(14) < 30; seed Donchian = close > donchian_high(20)
        # If long is None or short is None, that's also "different".
        seed_signatures = [
            {"ind": "sma", "args_lhs": [20], "args_rhs": [50], "cmp": ">"},
            {"ind": "rsi", "args_lhs": [14], "cmp": "<", "rhs": 30.0},
            {"ind": "close", "args_lhs": [], "cmp": ">", "rhs_ind": "donchian_high"},
        ]
        # Crude check: serialize the spec and compare to canonical seed strings
        from agentfx.core.dsl.seeds import SEED_SPECS

        seed_dumps = {
            json.dumps(s.model_dump(mode="json"), sort_keys=True) for s in SEED_SPECS
        }
        this_dump = json.dumps(dsl, sort_keys=True)
        structurally_different = this_dump not in seed_dumps
    results.append(
        (
            "3. At least one validated genome differs from gen-0 seeds",
            has_validated and structurally_different,
            f"best validated={has_validated} structurally_different={structurally_different}"
            if has_validated
            else "no validated genome — best may have failed Gate B",
        )
    )

    # Criterion 4: decision log captures *why* — reasoning text on entries
    reasoning_count = sum(1 for d in decisions if d.get("reasoning_text"))
    has_reasoning = reasoning_count > 0
    has_lifecycle_actions = {
        "run_started",
        "init_population",
        "evaluate_generation",
        "validate",
        "run_completed",
    }.issubset({d["action"] for d in decisions})
    results.append(
        (
            "4. Decision log lets a reviewer reconstruct *why*",
            has_reasoning and has_lifecycle_actions,
            f"total_decisions={len(decisions)} "
            f"with_reasoning={reasoning_count} "
            f"lifecycle_covered={has_lifecycle_actions}",
        )
    )

    # Criterion 5: LLM cost stays under per-run budget
    cost = float(run.get("llm_cost_usd", "0"))
    budget = float((run.get("config_json") or {}).get("llm_budget_usd", 5.0))
    under_budget = cost <= budget
    # If LLM was disabled (no key or zero budget) report N/A
    llm_invoked_any = any(g.get("llm_invoked") for g in generations)
    if not llm_invoked_any:
        results.append(
            (
                "5. LLM cost stays under budget (within this run)",
                True,  # vacuously true — no calls made
                f"no LLM injection occurred (budget=${budget}, cost=${cost}) — N/A this run",
            )
        )
    else:
        results.append(
            (
                "5. LLM cost stays under budget (within this run)",
                under_budget,
                f"budget=${budget} actual=${cost} ({'OK' if under_budget else 'OVER'})",
            )
        )

    return results


# ─────────────────────────────────────────────────────────────────────────
# Output dump
# ─────────────────────────────────────────────────────────────────────────


def dump_run_summary(run: dict) -> None:
    hr("RUN SUMMARY")
    print(f"  run_id           : {run['id']}")
    print(f"  status           : {run['status']}")
    print(f"  stop_reason      : {run.get('stop_reason')}")
    print(f"  instrument       : {run['instrument']} {run['granularity']}")
    print(f"  current_gen      : {run['current_generation']}")
    print(f"  best_genome_id   : {run.get('best_genome_id')}")
    print(f"  llm_cost_usd     : ${run.get('llm_cost_usd')}")
    print(f"  process_pid      : {run.get('process_pid')}")
    print(f"  started_at       : {fmt_dt(run.get('started_at'))}")
    print(f"  completed_at     : {fmt_dt(run.get('completed_at'))}")


def dump_generations(generations: list) -> None:
    hr("GENERATIONS")
    print(f"  {'idx':<4} {'best_fitness':<14} {'llm_invoked':<12} {'completed_at':<10}")
    for g in generations:
        bf = g.get("best_fitness")
        bf_str = f"{bf:.4f}" if bf is not None else "—"
        print(
            f"  {g['index']:<4} {bf_str:<14} "
            f"{'YES' if g['llm_invoked'] else 'no':<12} "
            f"{fmt_dt(g.get('completed_at')):<10}"
        )


def dump_best_genome(best: dict | None) -> None:
    hr("BEST GENOME (run-best, validated)")
    if best is None:
        print("  (no best genome — run may have failed)")
        return
    print(f"  id                  : {best['id']}")
    print(f"  origin              : {best['origin']}")
    print(f"  fitness (training)  : {best.get('fitness')}")
    print(f"  validator_passed    : {best.get('validator_passed')}")
    print(f"  validation_failures : {best.get('validation_failures')}")
    print(f"  parents             : {best.get('parent_ids')}")
    print(f"  llm_reasoning       : {best.get('llm_reasoning') or '—'}")
    print("\n  DSL spec:")
    print(textwrap.indent(json.dumps(best["dsl_json"], indent=2), "    "))
    if best.get("validation_folds"):
        print("\n  Walk-forward folds:")
        for f in best["validation_folds"]:
            oos = " ← OOS" if f["is_oos"] else ""
            sharpe = f.get("sharpe")
            sharpe_str = f"{sharpe:.3f}" if sharpe is not None else "—"
            print(
                f"    fold {f['fold_index']}: sharpe={sharpe_str:>6s} "
                f"trades={f.get('n_trades')} "
                f"gate_b={'pass' if f.get('passed_gate_b') else 'fail'}{oos}"
            )


def dump_decision_log(decisions: list) -> None:
    hr(f"DECISION LOG  ({len(decisions)} entries)")
    for d in decisions:
        ts = fmt_dt(d.get("created_at"))
        agent = d["agent_type"]
        action = d["action"]
        reason = d.get("reasoning_text") or ""
        gen = d.get("generation_id") or ""
        gen_short = gen.split("_")[-1] if gen else ""
        line = f"  [{ts}] {agent:>12s}.{action:<24s}"
        if gen_short:
            line += f" (gen {gen_short})"
        if reason:
            line += f"\n      → {reason}"
        print(line)


def dump_criteria(results: list[tuple[str, bool, str]]) -> int:
    hr("ACCEPTANCE CRITERIA")
    fail_count = 0
    for label, passed, note in results:
        mark = "✓" if passed else "✗"
        if not passed:
            fail_count += 1
        print(f"  {mark} {label}")
        print(f"      {note}")
    return fail_count


# ─────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Use a tiny config (pop=5, gens=2, budget=$0.50) for fast feedback.",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Force LLM injector off (sets budget to $0 — orchestrator skips calls).",
    )
    args = parser.parse_args()

    hr("ACCEPTANCE: AgentFX v0.3.0 Autonomous Strategy Loop")
    print(f"  base url : {BASE_URL}")
    print(f"  user     : {TRADER_EMAIL}")
    print(
        f"  mode     : {'quick' if args.quick else 'full'}{', no LLM' if args.no_llm else ''}"
    )

    token = login()
    print("  ✓ logged in")

    run_id = kick_off_run(token, quick=args.quick, no_llm=args.no_llm)

    hr("POLLING")
    run = poll_until_terminal(run_id, token)

    # Pull all the observable state for the dump
    try:
        generations = api_get(f"/evolution-runs/{run_id}/generations", token)["items"]
    except Exception as e:
        print(f"  generations fetch failed: {e}")
        generations = []

    decisions = []
    try:
        page = 1
        while True:
            resp = api_get(
                f"/evolution-runs/{run_id}/decisions",
                token,
                page=page,
                page_size=100,
            )
            decisions.extend(resp["items"])
            if len(decisions) >= resp["total"]:
                break
            page += 1
    except Exception as e:
        print(f"  decisions fetch failed: {e}")

    best = None
    if run.get("best_genome_id"):
        try:
            best = api_get(
                f"/evolution-runs/{run_id}/genomes/{run['best_genome_id']}", token
            )
        except Exception as e:
            print(f"  best genome fetch failed: {e}")

    # Dump
    dump_run_summary(run)
    dump_generations(generations)
    dump_best_genome(best)
    dump_decision_log(decisions)
    fail_count = dump_criteria(evaluate_criteria(run, generations, decisions, best))

    print()
    hr("RESULT")
    if fail_count == 0:
        print("  All criteria observed cleanly. Ready for merge review.")
    else:
        print(f"  {fail_count} criteria did NOT pass — review the output above.")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    # Allow `from agentfx.core.dsl.seeds import SEED_SPECS` to work when
    # this script is run from the repo root.
    sys.path.insert(
        0,
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"),
    )
    sys.exit(main())
