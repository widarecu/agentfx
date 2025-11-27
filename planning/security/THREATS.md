# Threats (Initial) and Mitigations

- Broker/API outages → circuit breakers, retries, paper fallback
- Model overfitting → OOS holdout, walk-forward, leakage checks
- Data gaps/corruption → checksums, NaN guards, fail-closed
- Secrets leakage → no secrets in repo, scoped keys, rotation
- Latency spikes → SLO alerts, degrade gracefully (reduce frequency/size)
