# Agent Development Protocols

This document defines standards for AI agents (Claude, planner, developer, tester, reviewer) working on the AgentFX codebase. All SDLC agents MUST follow these protocols.

---

## Quick Start for Agents

Before starting any task:
1. Read `CLAUDE.md` for project overview
2. Read `planning/BACKLOG.md` for current work items
3. Read `planning/requirements/US-XXX.md` for specific requirements
4. Check `planning/architecture/STANDARDS.md` for tech stack

After completing any task:
1. Run verification commands (see below)
2. Update relevant documentation
3. Mark backlog items as complete

---

## Code Generation Rules

### MUST Follow

| Rule | Verification |
|------|--------------|
| All Python code passes ruff | `make lint` |
| All Python code passes black | `make format --check` |
| All new code has unit tests | `make test-unit` |
| Coverage >= 80% for new code | `make test-unit` with coverage |
| All API endpoints have Pydantic schemas | Manual review |
| All database changes have migrations | `alembic history` |
| Type hints on all function signatures | `make typecheck` |

### MUST NOT Do

| Rule | Why |
|------|-----|
| Never commit secrets or credentials | Security |
| Never modify production DB without migration | Data integrity |
| Never bypass policy engine checks | Trading safety |
| Never promote strategies without Gate B | Risk management |
| Never use `except:` without specific exception | Error handling |
| Never add dependencies without ADR | Tech stack control |

---

## Verification Commands

Before marking ANY task as complete, run these commands:

### Backend Tasks

```bash
# Minimum verification (every change)
make lint && make typecheck && make test-unit

# Full verification (before PR)
make lint && make typecheck && make test

# With coverage report
PYTHONPATH=src pytest tests/unit/ --cov=src/agentfx --cov-report=term-missing
```

### Frontend Tasks

```bash
# Minimum verification
cd portal/web && npm run lint && npm run build

# With E2E tests
cd portal/web && npm run test:e2e
```

### Database Tasks

```bash
# Verify migrations
alembic history
alembic check

# Test migration
alembic upgrade head
alembic downgrade -1
alembic upgrade head
```

---

## File Creation Standards

### Python Files

```python
"""
Module description (one line).

Extended description if needed.
"""

from __future__ import annotations

# stdlib imports
import os
from datetime import datetime
from typing import Any

# third-party imports
from fastapi import APIRouter
from pydantic import BaseModel

# local imports
from agentfx.core.policy import PolicyConfig


def my_function(param: str, count: int = 10) -> dict[str, Any]:
    """
    Brief description.

    Args:
        param: Description of param
        count: Description of count

    Returns:
        Description of return value

    Raises:
        ValueError: When param is empty
    """
    if not param:
        raise ValueError("param cannot be empty")
    return {"param": param, "count": count}
```

### Test Files

```python
"""Tests for module_name."""

import pytest
from unittest.mock import Mock, patch

from agentfx.module import function_to_test


class TestFunctionToTest:
    """Tests for function_to_test."""

    def test_basic_case(self):
        """Test basic functionality."""
        result = function_to_test("input")
        assert result == expected

    def test_edge_case(self):
        """Test edge case with empty input."""
        with pytest.raises(ValueError):
            function_to_test("")

    @pytest.mark.integration
    def test_with_database(self, db_session):
        """Test with real database."""
        # Integration test code
        pass
```

### React Components

```tsx
/**
 * ComponentName - Brief description
 */

import React, { useState, useEffect } from 'react'

interface ComponentNameProps {
  /** Description of prop */
  title: string
  /** Optional prop with default */
  count?: number
}

export default function ComponentName({
  title,
  count = 0
}: ComponentNameProps) {
  const [state, setState] = useState<string>('')

  useEffect(() => {
    // Effect logic
    return () => {
      // Cleanup
    }
  }, [])

  return (
    <div className="p-4">
      <h1 className="text-xl font-bold">{title}</h1>
      <span>{count}</span>
    </div>
  )
}
```

---

## Naming Conventions

### Files

| Type | Pattern | Example |
|------|---------|---------|
| Python module | `snake_case.py` | `market_data.py` |
| Python test | `test_<module>.py` | `test_market_data.py` |
| React component | `PascalCase.tsx` | `MarketData.tsx` |
| React test | `<Component>.spec.ts` | `MarketData.spec.ts` |
| API router | `<resource>.py` | `strategies.py` |
| SQLAlchemy model | `<entity>.py` | `experiment.py` |

### Code

| Type | Convention | Example |
|------|------------|---------|
| Python class | PascalCase | `OptimizerAgent` |
| Python function | snake_case | `run_backtest` |
| Python constant | UPPER_SNAKE | `MAX_RETRIES` |
| Python variable | snake_case | `user_count` |
| React component | PascalCase | `MarketDataPage` |
| React hook | camelCase with use | `useMarketData` |
| TypeScript interface | PascalCase | `UserProfile` |
| CSS class | kebab-case (Tailwind) | `bg-slate-900` |

---

## API Endpoint Protocols

### Creating New Endpoints

1. Define Pydantic schemas in `api/schemas/<resource>.py`
2. Create router in `api/routers/<resource>.py`
3. Register router in `api/main.py`
4. Add tests in `tests/unit/routers/test_<resource>.py`
5. Document in relevant US-XXX requirement

### Endpoint Template

```python
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..schemas.resource import ResourceCreate, ResourceResponse
from ..services.resource import ResourceService

router = APIRouter()


@router.post("/", response_model=ResourceResponse, status_code=status.HTTP_201_CREATED)
def create_resource(
    data: ResourceCreate,
    db: Session = Depends(get_db),
) -> ResourceResponse:
    """
    Create a new resource.

    - **field1**: Description
    - **field2**: Description
    """
    service = ResourceService(db)
    return service.create(data)


@router.get("/{resource_id}", response_model=ResourceResponse)
def get_resource(
    resource_id: str,
    db: Session = Depends(get_db),
) -> ResourceResponse:
    """Get resource by ID."""
    service = ResourceService(db)
    resource = service.get(resource_id)
    if not resource:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Resource {resource_id} not found",
        )
    return resource
```

---

## Database Protocols

### Creating New Models

1. Define model in `api/models/<entity>.py`
2. Create migration: `alembic revision --autogenerate -m "add <entity>"`
3. Review generated migration
4. Test: `alembic upgrade head && alembic downgrade -1 && alembic upgrade head`

### Model Template

```python
from datetime import datetime
from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship

from ..core.database import Base


class Experiment(Base):
    __tablename__ = "experiments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    job_id = Column(UUID(as_uuid=True), ForeignKey("optimization_jobs.id"), nullable=False)
    strategy_name = Column(String(50), nullable=False, index=True)
    pair = Column(String(10), nullable=False, index=True)
    params = Column(JSONB, nullable=False)
    metrics = Column(JSONB, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    job = relationship("OptimizationJob", back_populates="experiments")
```

---

## Agent-Specific Protocols

### Optimizer Agent

When implementing optimizer functionality:
- Use existing `run_backtest()` from `services/backtest.py`
- Store results in `experiments` table
- Respect rate limits (50 optimization runs/hour)
- Log all experiment runs with full lineage

### Validator Agent

When implementing validation:
- Enforce Gate B thresholds exactly as specified in NFRS.md
- Use walk-forward validation with OOS holdout
- Never approve strategies that fail thresholds
- Log all validation results

### Discovery Agent

When implementing discovery:
- Generate from STRATEGY_TEMPLATES only
- Log hypothesis reasoning
- Queue for optimization, don't run directly
- Limit exploration to defined param ranges

---

## Error Handling Protocol

### Backend Errors

```python
# DO: Specific exceptions with context
try:
    result = broker_api.execute_order(order)
except BrokerTimeoutError as e:
    logger.warning(f"Broker timeout for order {order.id}: {e}")
    raise HTTPException(status_code=504, detail="Broker timeout")
except BrokerRejectionError as e:
    logger.error(f"Order rejected: {e.reason}")
    raise HTTPException(status_code=400, detail=e.reason)

# DON'T: Bare except
try:
    result = broker_api.execute_order(order)
except:  # Never do this
    pass
```

### Frontend Errors

```tsx
// DO: Handle errors gracefully
try {
  const data = await api.fetchData()
  setData(data)
} catch (error) {
  if (error instanceof ApiError) {
    setError(error.message)
  } else {
    setError('An unexpected error occurred')
    console.error(error)
  }
}
```

---

## Documentation Protocol

### When to Update Docs

| Action | Update |
|--------|--------|
| New endpoint | Add to API docs, update US-XXX |
| New config option | Update CLAUDE.md, .env.example |
| New agent | Update CLAUDE.md agent table |
| Schema change | Update relevant US-XXX API contract |
| Breaking change | Create ADR |

### Required Docstrings

- All public functions
- All classes
- All API endpoints (FastAPI description)
- All complex algorithms

---

## Checklist for Task Completion

Before marking a task complete, verify:

- [ ] Code passes `make lint`
- [ ] Code passes `make typecheck`
- [ ] Unit tests pass `make test-unit`
- [ ] New code has tests (80%+ coverage)
- [ ] No secrets in code
- [ ] API endpoints have schemas
- [ ] Database changes have migrations
- [ ] Documentation updated if needed
- [ ] Relevant US-XXX updated if needed
- [ ] BACKLOG.md item marked as DONE

---

## Quick Reference

```
VERIFICATION COMMANDS
---------------------
make lint              # Linting
make typecheck         # Type checking
make test-unit         # Unit tests
make test              # All tests

FILE LOCATIONS
--------------
Domain logic:     src/agentfx/core/
Adapters:         src/agentfx/adapters/
Agents:           src/agentfx/agents/
API:              src/agentfx/api/
Unit tests:       tests/unit/
Integration:      tests/integration/
E2E:              tests/e2e/
Requirements:     planning/requirements/

KEY THRESHOLDS
--------------
Test coverage:    >= 80%
Line length:      88 chars
Gate B Sharpe:    >= 1.0
Gate B MDD:       <= 10%
```
