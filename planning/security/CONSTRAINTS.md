# Security Constraints (MVP)

- Secrets Management
  - Use environment variables or a secret manager; never commit secrets
  - Rotate broker/API keys at least every 90 days (record rotation date)
- Authentication/Authorization
  - Service accounts only; least privilege on broker/data APIs
  - No interactive credentials in CI
- Data Protection
  - Store only necessary data; redact PII (none expected for forex)
  - Encrypt at rest if cloud storage is used
- Network/Access
  - Restrict outbound traffic to required broker/data endpoints
  - Log all broker API calls (endpoint, status, latency)
- Operational Safeguards
  - Risk limits configurable: max risk/trade, daily loss stop, max drawdown
  - Circuit breaker on consecutive errors or latency breaches
- Audit & Compliance
  - Keep an order decision log: strategy version, inputs signature, outputs
  - Keep experiment lineage: dataset version, seed, code hash, metrics
