# MVP Constraints (Proposed)

This summarizes baseline constraints for the MVP. It references detailed principles, NFRs, and security constraints.

References:
- Architecture Principles: ./architecture/PRINCIPLES.md
- NFRs: ./architecture/NFRS.md
- Security Constraints: ./security/CONSTRAINTS.md

## 1) Risk & Capital
- Risk per trade: ≤ 0.5% of trading capital
- Daily loss limit: 2% of capital (auto-halt for the day)
- Max drawdown: 10% (system halt, manual review required)
- Max concurrent positions: 5
- Per-pair exposure cap: 30% of capital
- Leverage cap: 10x (or broker minimum if lower)

## 2) Market Scope & Sessions
- Instruments: Major FX pairs only (EURUSD, GBPUSD, USDJPY, USDCHF, AUDUSD, USDCAD, NZDUSD)
- Trading sessions: 24/5 permitted, with scheduled maintenance window daily (e.g., 15 minutes) for housekeeping
- News filter: Off for MVP; add later as a feature (documented risk)

## 3) Strategy & Orders
- Order types: market or limit with time-in-force good-till-cancel (GTC) or immediate-or-cancel (IOC)
- No pyramiding for MVP; single active position per pair per strategy
- Max order rate: ≤ 10 orders/min across all pairs (burst control)
- Slippage model (backtest/paper): fixed 0.5 pip per side + spread; revisit after measurement
- Transaction cost assumption: broker spread only (no commissions) unless broker dictates otherwise; broker leverage financing costs should be factored into the Sharpe ratio

## 4) Data & Validation
- Data granularity: 1m bars for decisioning; optional 5s tick aggregation later
- Lookback window: ≥ 180 days for features; ≥ 365 days preferred where available
- Backtesting protocol: rolling walk-forward with final OOS holdout ≥ 6 months
- Minimum trades in OOS: ≥ 300 trades or ≥ 3 months of activity (whichever later)
- OOS thresholds to pass Gate B:
  - Sharpe ≥ 1.0
  - Deflated Sharpe probability ≥ 95% by default (`AFX_DSR_THRESHOLD`).
    Calculated once over the contiguous walk-forward OOS period from daily
    net equity returns (annualized with √252 for display). UTC days wholly
    closed under the FX market calendar are excluded; open days count even
    if the strategy does not trade. The zero-edge
    variance of the daily Sharpe estimates is approximated as `1/(T-1)`
    for `T` daily observations; the candidate's sampling error also
    accounts for skewness and kurtosis.
  - The Deflated Sharpe trial count includes each genome evaluated in the
    batch and prior evaluations across runs and warm-start lineages on the
    same instrument and granularity. Two windows pool attempts when their
    shared interval is at least 50% of the shorter window. Training-only
    trials and imported copies do not count.
  - Max drawdown ≤ 10%
  - Hit rate ≥ 48%
  - Turnover within broker limits; average holding period ≥ 5 minutes (no HFT)
  - PnL after slippage and spread positive and stable across regimes

## 5) Execution & Rollout
- Paper first: minimum 2 weeks of paper with metrics above thresholds before Live
- Future live deploy flow: the operator chooses risk per trade for each deployment. Default to **Safer** (the genome's risk per trade, capped at the 0.5% MVP live limit). Offer **Same as paper** (the genome's own risk per trade) as an explicit choice. This requirement does not authorize relaxing the current live limit; any live use above that limit requires a separately approved policy change.
- Latency SLOs: Paper p95 < 1s; Live p95 < 500ms end-to-end decision loop
- Reliability: auto-retry with backoff; circuit breaker on 5 consecutive broker failures or p95 latency > 2x SLO for 10 minutes
- Rollback: disable strategy via feature flag; fail-safe to flat positions

## 6) Observability & Audit
- Required dashboards: strategy metrics (Sharpe, MDD, exposure), order latency, error rates
- Alerts: daily loss hit, circuit breaker trip, API error surge, latency SLO breach
- Audit: persist order decision logs (inputs signature, strategy version, output action) and experiment lineage

## 7) Security (Baseline)
- Secrets via env/secret manager only; no secrets in repo or logs
- Least-privilege API keys; rotate every ≤ 90 days
- Restrict outbound network to broker/data endpoints; log all broker calls

Approval of this document is required to proceed with MVP feature proposals.
