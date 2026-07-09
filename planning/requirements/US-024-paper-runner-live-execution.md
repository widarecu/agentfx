# US-024: Paper Runner Live DSL Execution

**Version:** v0.6.1 — Live Execution & Observability
**Priority:** P0 — Must Have (unblocks every other v0.6.1 item)
**Status:** TODO

## Genesis

Wilson's 24-hour UAT run on 2026-07-08 halted with **zero trades** and unchanged balance because `paper_runner._execute_loop` shipped as a monitoring skeleton — it polls balance and risk gates every 5 seconds but never fetches candles, never compiles the DSL, and never calls `place_market_order`. Confirmed by `grep -n "generate_signals\|place_market_order\|StrategySpec" src/agentfx/services/paper_runner.py` → no matches. The `# Real cadence lands in PR-G` comment I left inside the loop was accurate for the initial ship but shouldn't have survived the UAT deployment un-triaged. US-024 closes that gap.

## Given-When-Then

**GIVEN** a `runs` row where `status=RUNNING`, `environment=PAPER`, `strategy_genome_id` set, `strategy_genomes.validator_passed=true`, and the owning user's `user_oanda_credentials` are `verified_at` non-null
  **AND** the paper runner subprocess is executing `_execute_loop`
**WHEN** the current time crosses a granularity boundary (e.g. `:00` on M1, `HH:00` on H1)
**THEN** the runner:

1. Reloads the `Run` row (kill-flag check); halts via `_flatten_and_halt('USER_KILL')` if `kill_requested_at` set.
2. Calls `fetch_recent_candles(instrument, granularity, bars=strategy.min_lookback + 50)` against the shared `candles` table.
3. If the newest candle timestamp is older than `2 * granularity_seconds`, writes a `USER_SIGNAL_SKIPPED_STALE` decision-log entry, updates `next_signal_at`, and continues the loop.
4. Otherwise calls `compile_dsl_spec(strategy_genome.dsl_json)` → `Strategy` and `strategy.generate_signals(candles_df)` — same code path the backtest engine uses, so training-vs-live parity is preserved.
5. Reads the last row's signal, fetches current OANDA position via `client.get_position(instrument)`, and calls `decide_action(signal_direction, signal_units, current_net_units)` (helper already in `paper_runner.py`).
6. Executes the action:
   - `place` → `client.place_market_order(instrument, signed_units, stop_loss_pips, take_profit_pips)`, inserts a `Trade` row with entry_timestamp / entry_price / quantity / side / signal_reason, writes `USER_ORDER_PLACED` decision log.
   - `close` → `client.close_all_positions(instrument)`, updates matching open `Trade` row with exit_timestamp / exit_price / pnl / pnl_pct, writes `USER_POSITION_CLOSED` decision log.
   - `hold` → no-op, writes `USER_SIGNAL_EVALUATED` decision log (throttled to one entry per hour to keep the table volume bounded).
7. Inserts a `PaperEquitySnapshot` row (see US-026) with the post-action balance and unrealized_pnl.
8. Advances `next_signal_at = align_to_granularity(now + granularity_seconds)`.

## Loop cadence — two internal timers, one subprocess

**GIVEN** the runner is running
**WHEN** the top-of-loop tick fires (every ~5 seconds)
**THEN**:

- **Always** run the health tick: reload run row, check `kill_requested_at`, call `get_account_balance`, update `run.current_balance` / `run.pnl`, evaluate risk gates. This preserves the sub-5s kill-switch guarantee from v0.6.0.
- **Only if `now >= next_signal_at`** run the signal tick as described above. Signal work is wrapped in `contextlib.timeout(30)` so a stuck `generate_signals` cannot starve the health tick beyond one iteration.

`align_to_granularity(t)` rounds up to the next UTC boundary — for M1 `ceil(t / 60) * 60`, for H1 the next `HH:00`. Prevents drift accumulation over long runs.

## Acceptance Criteria

### Runner service (`src/agentfx/services/paper_runner.py`)

- [ ] `_execute_loop` rewritten to interleave the health tick and the granularity-aligned signal tick per the specification above. The existing `_snapshot_balance_if_needed`, `_flatten_and_halt`, and `_mark_terminal` helpers are reused unchanged.
- [ ] New helper `_signal_tick(*, db, run, client, strategy, instrument, granularity_seconds)` extracted so unit tests can drive one tick in isolation.
- [ ] New helper `align_to_granularity(now_ts, granularity_seconds) -> float` — pure, tested for M1 / M5 / M15 / M30 / H1 / H4 / D boundaries.
- [ ] New helper `_min_lookback(strategy) -> int` — returns the max lookback across the compiled strategy's indicators + a 50-bar safety margin.
- [ ] SL/TP sourcing: `run.risk_config.get('stop_loss_pips')` / `.get('take_profit_pips')` passed to `place_market_order` when set, otherwise `None`.
- [ ] Position-flip handling: `decide_action` returns `close` OR `place` per tick. A signal flip (long → short) closes on tick N, then places on the *next* signal tick — explicit two-step in the trades table.
- [ ] Decision log volume-control: `USER_SIGNAL_EVALUATED` entries are written at most once per hour per run (dedupe by `run_id` + hour bucket). `USER_ORDER_PLACED` / `USER_POSITION_CLOSED` / `USER_SIGNAL_SKIPPED_STALE` always write.

### Trade row persistence

- [ ] On order placement: `Trade` row inserted with `run_id`, `entry_timestamp=now_utc`, `entry_price=fill_price`, `quantity=abs(signed_units)`, `side='long' if signed_units > 0 else 'short'`, `signal_reason=last_signal.reason` (indicator name + threshold that triggered the signal), `origin='live'`.
- [ ] On position close: matching open `Trade` row (`run_id`, no `exit_timestamp`) is UPDATE'd with `exit_timestamp=now_utc`, `exit_price=fill_price`, `pnl`, `pnl_pct`.
- [ ] `origin` column added to `trades` if it doesn't already exist (Alembic migration in this US or PR-A of US-025, whichever lands first).

### Tests

- [ ] `tests/unit/services/test_paper_runner_signal_tick.py` (new) — mocked `OandaClient`, mocked `fetch_recent_candles`, pre-baked DSL emitting a known signal series; assert one tick calls `place_market_order` with expected units and inserts a Trade row with the right `signal_reason`.
- [ ] `tests/unit/services/test_align_to_granularity.py` (new) — table-driven for every granularity in `granularityToSeconds`. Edge cases: exactly on the boundary, one second before, DST boundary (shouldn't matter because we round in UTC).
- [ ] `tests/unit/services/test_paper_runner.py` (updated) — existing health-loop tests pre-set `next_signal_at = now + 3600` so they don't accidentally trigger signal work.
- [ ] `tests/integration/test_paper_runner_full_cycle.py` (new) — respx-based fake OANDA server; seed `candles` table with 200 M1 bars containing a clear upward trend; deploy an SMA-crossover genome; drive 5 signal ticks; assert 2+ orders placed, both Trade rows have entry + exit populated, `runs.pnl` = sum of trade pnls, kill switch mid-cycle closes cleanly.
- [ ] `tests/unit/services/test_signal_tick_stale_candles.py` (new) — signal tick with `latest_candle_timestamp = now - 3 * granularity_seconds` writes `USER_SIGNAL_SKIPPED_STALE` and returns without placing an order.

### Coverage & style

- [ ] Runner keeps unit test coverage ≥ 80% (matches v0.6.0 gate). Subprocess entry point can be `# pragma: no cover` — same convention as v0.6.0's `run_paper_run`.
- [ ] No `time.sleep(5)` literal in the signal-tick path — cadence is controlled by `next_signal_at`. Health-tick sleep stays `sleep_fn(5.0)` (injectable for tests).

## User-approved decisions locked in the plan doc

- Candle staleness → **skip + log**, do not halt.
- OANDA v20 streaming → **defer to v0.6.2**; keep polling.
- Order timing → **candle close only**, no intra-bar.
- Position flip → **two-step (close then place)**, explicit in decision log.
- SL/TP → **`run.risk_config` when set, otherwise no broker-side SL/TP** (daily-loss halt remains the safety net).

## Files Involved

- `src/agentfx/services/paper_runner.py` — rewrite `_execute_loop`, add helpers
- `src/agentfx/services/paper_policy.py` — unchanged, reused
- `src/agentfx/core/dsl/compiler.py` — unchanged, `compile_dsl_spec` reused
- `src/agentfx/adapters/oanda.py` — unchanged, order methods already in place from PR-E of v0.6.0
- `src/agentfx/api/models/run.py` — add `origin` column to `Trade` if not present
- `migrations/versions/2026_07_XX_trade_origin.py` (new, if needed)
- `tests/unit/services/test_paper_runner.py` — update health-loop tests
- `tests/unit/services/test_paper_runner_signal_tick.py` (new)
- `tests/unit/services/test_align_to_granularity.py` (new)
- `tests/unit/services/test_signal_tick_stale_candles.py` (new)
- `tests/integration/test_paper_runner_full_cycle.py` (new)

## Verification

1. `make test-unit` — new signal-tick + alignment + stale-candle unit tests pass; existing runner health-loop tests still pass.
2. `make test-integration` — full-cycle test passes with the fake-OANDA respx server; 2+ trades persisted, kill switch mid-cycle observes the flag and closes cleanly.
3. **UAT manual verification (part of the v0.6.1 exit gate):**
   - Deploy a fresh SMA-crossover genome on UAT.
   - Watch OANDA Practice dashboard — an order appears within one M1 tick of the runner starting.
   - `/paper/:id` decision log shows `USER_ORDER_PLACED` with the signal reason.
   - Kill switch closes the position on OANDA within 5 seconds and the run status transitions to `HALTED_MANUAL`.
