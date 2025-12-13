# Engineering Standards

This document defines the technology stack, coding standards, and development practices for AgentFX. All SDLC agents must follow these standards.

---

## Technology Stack (LOCKED)

These technology choices are locked for the project. Do not introduce alternatives without an ADR.

### Backend

| Component | Technology | Version | Notes |
|-----------|------------|---------|-------|
| Language | Python | 3.11+ | Use modern syntax (match, \|, etc.) |
| Framework | FastAPI | 0.100+ | Async endpoints, Pydantic v2 |
| ORM | SQLAlchemy | 2.0+ | New-style declarative mapping |
| Database | PostgreSQL | 15+ | Use JSONB for flexible schemas |
| Migrations | Alembic | 1.12+ | All schema changes via migrations |
| Password Hashing | Argon2 | - | Via argon2-cffi |
| JWT | python-jose | - | RS256 or HS256 |
| HTTP Client | httpx | 0.25+ | Async support |
| Testing | pytest | 7.x+ | With pytest-asyncio |
| Coverage | coverage.py | - | Minimum 80% |
| Linting | ruff | - | Fast Python linter |
| Formatting | black | - | Line length 88 |
| Type Checking | mypy | - | Incremental mode |

### Frontend

| Component | Technology | Version | Notes |
|-----------|------------|---------|-------|
| Runtime | Node.js | 18+ | LTS version |
| Framework | React | 18+ | Functional components only |
| Build | Vite | 5.x | Fast HMR |
| Styling | Tailwind CSS | 3.x | No custom CSS unless necessary |
| State | React Context | - | Redux only if justified |
| HTTP Client | fetch/axios | - | Native fetch preferred |
| E2E Testing | Playwright | 1.x | Cross-browser |
| Linting | ESLint | - | With TypeScript support |

### Infrastructure

| Component | Technology | Notes |
|-----------|------------|-------|
| Container | Docker | Multi-stage builds |
| Orchestration | Docker Compose | Local development |
| CI/CD | GitHub Actions | (future) |
| Secrets | Environment variables | dotenv for local |

---

## Repository Layout

```
src/agentfx/
├── core/           # Domain logic (pure Python, NO I/O)
│   ├── policy.py   # Risk/exposure checks
│   ├── strategy.py # Strategy abstractions
│   ├── metrics.py  # Performance calculations
│   └── gates.py    # Gate B thresholds
├── adapters/       # External system integrations
│   ├── oanda.py    # Broker adapter
│   └── storage/    # Database adapters
├── agents/         # Autonomous trading agents
│   ├── base.py     # BaseAgent ABC
│   ├── optimizer.py
│   ├── validator.py
│   ├── discovery.py
│   ├── evolution.py
│   └── orchestrator.py
├── services/       # Business orchestration
│   ├── backtest.py
│   ├── paper_runner.py
│   └── ingest.py
├── api/            # FastAPI application
│   ├── main.py
│   ├── core/       # Config, DB, security
│   ├── models/     # SQLAlchemy models
│   ├── routers/    # REST endpoints
│   ├── schemas/    # Pydantic schemas
│   └── services/   # API-specific services
└── shared/         # Common utilities
    └── config.py

tests/
├── unit/           # Fast, isolated tests
├── integration/    # Tests with real dependencies
├── e2e/            # Full-stack tests (Playwright)
├── contracts/      # API contract tests
└── performance/    # Performance benchmarks
```

---

## Coding Standards

### Python

| Rule | Standard |
|------|----------|
| Line length | 88 characters (black default) |
| Import order | stdlib, third-party, local (isort) |
| Docstrings | Google style for public APIs |
| Type hints | Required for function signatures |
| Exception handling | Specific exceptions, not bare `except:` |
| Async | Use `async/await` for I/O operations |

### TypeScript/React

| Rule | Standard |
|------|----------|
| Components | Functional with hooks only |
| Props | TypeScript interfaces required |
| State | useState/useContext preferred |
| Effects | Cleanup in useEffect required |
| Styling | Tailwind utility classes |

---

## Testing Standards

### Coverage Requirements

| Test Type | Minimum Coverage | Trigger |
|-----------|-----------------|---------|
| Unit | 80% | Every PR |
| Integration | Key paths | Every PR |
| E2E | Critical flows | Pre-release |

### Test Markers (pytest)

```python
@pytest.mark.unit          # Fast, isolated
@pytest.mark.integration   # Real dependencies
@pytest.mark.e2e           # Full-stack
@pytest.mark.market_data   # Market data specific
@pytest.mark.oanda_real    # Requires OANDA API
```

### Test File Naming

| Type | Pattern | Example |
|------|---------|---------|
| Unit test | `test_<module>.py` | `test_optimizer.py` |
| Integration | `test_<feature>_integration.py` | `test_auth_integration.py` |
| E2E | `<feature>.spec.ts` | `auth_flows.spec.ts` |

---

## Configuration Standards

### Environment Files

| File | Purpose |
|------|---------|
| `.env.development` | Local development |
| `.env.test` | Test environment |
| `.env.staging` | Staging deployment |
| `.env.production` | Production (secrets via vault) |

### Required Environment Variables

```bash
# Core
ENVIRONMENT=development|test|staging|production
DATABASE_URL=postgresql://...
SECRET_KEY=<random-32-chars>

# OANDA (trading)
OANDA_API_KEY=<api-key>
OANDA_ACCOUNT_ID=<account-id>
OANDA_ENVIRONMENT=practice|live

# Feature flags
WEBSOCKET_ENABLED=true|false
```

---

## API Standards

### REST Conventions

| Aspect | Convention |
|--------|------------|
| Versioning | `/api/v1/` prefix |
| Resources | Plural nouns (`/users`, `/strategies`) |
| Actions | HTTP verbs (GET, POST, PUT, DELETE) |
| Async jobs | Return 202 with job_id |
| Errors | RFC 7807 Problem Details |

### Response Format

```json
// Success
{
  "data": { ... },
  "meta": { "page": 1, "total": 100 }
}

// Error
{
  "type": "validation_error",
  "title": "Invalid input",
  "status": 400,
  "detail": "Field 'email' is required"
}
```

### Authentication

- Bearer token in `Authorization` header
- JWT with configurable expiration
- Role-based access: `VIEWER`, `TRADER`, `ADMIN`

---

## Quality Gates

### Pre-Commit (Local)

```bash
make lint      # ruff + black --check
make format    # black (auto-fix)
```

### PR Merge Requirements

- [ ] All unit tests pass
- [ ] Coverage >= 80%
- [ ] Type check passes (mypy)
- [ ] Lint passes (ruff)
- [ ] No secrets in diff

### Pre-Release Requirements

- [ ] Integration tests pass
- [ ] E2E tests pass
- [ ] Security scan clean
- [ ] Performance benchmarks within SLOs

---

## Makefile Targets

```bash
make venv          # Create virtual environment
make install       # Install production deps
make dev           # Install dev deps + pre-commit
make test          # Run all tests
make test-unit     # Run unit tests only
make test-integration  # Run integration tests
make lint          # Run ruff + black --check
make format        # Run black formatter
make typecheck     # Run mypy
```

---

## Version Control

### Branch Strategy

| Branch | Purpose |
|--------|---------|
| `main` | Production-ready code |
| `feature/*` | New features |
| `fix/*` | Bug fixes |
| `release/*` | Release preparation |

### Commit Message Format

```
<type>(<scope>): <description>

Types: feat, fix, docs, test, refactor, chore
Scope: agents, api, core, portal, tests

Example: feat(agents): add optimizer grid search
```
