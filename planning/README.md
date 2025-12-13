# Planning and Governance

This directory contains the agentic-friendly planning system for AgentFX.

---

## Quick Start for AI Agents

1. **Current Work**: See [BACKLOG.md](BACKLOG.md) for version-grouped tasks
2. **Requirements**: See [requirements/](requirements/) for executable user stories
3. **Standards**: See [architecture/AGENT_PROTOCOLS.md](architecture/AGENT_PROTOCOLS.md) for development rules
4. **Roadmap**: See [ROADMAP.md](ROADMAP.md) for release milestones

---

## Directory Structure

```
planning/
├── README.md               # This file
├── BACKLOG.md              # Version-grouped task list
├── ROADMAP.md              # Agentic milestone releases (v0.1.0 → v1.0.0)
├── DECISIONS.md            # ADR index
├── MVP_CONSTRAINTS.md      # Trading risk limits and constraints
│
├── architecture/           # Engineering governance
│   ├── PRINCIPLES.md       # DO/DO NOT architectural rules
│   ├── STANDARDS.md        # Technology stack and coding standards
│   ├── NFRS.md             # SLOs, latency, availability targets
│   ├── TEST_STRATEGY.md    # Test organization and patterns
│   └── AGENT_PROTOCOLS.md  # Rules for SDLC agents
│
├── security/               # Security governance
│   ├── CONSTRAINTS.md      # Security requirements
│   ├── THREATS.md          # Threat model
│   └── CHECKS.md           # Pre-merge security checks
│
├── requirements/           # Executable user stories (US-XXX)
│   ├── US-001-*.md         # Given-When-Then with verification commands
│   └── US-002-*.md         # API contracts, files involved
│
└── archive/                # Historical documents
    ├── templates/          # Legacy templates (superseded by US-XXX format)
    └── *.md                # Archived FEAT, ADR, etc.

# Note: UI wireframes moved to portal/web/wireframes/
```

---

## Workflow Overview

### Development Workflow

```
TODO → IN_PROGRESS → DONE
```

Simple status tracking in BACKLOG.md. Each task links to requirements.

### Strategy Promotion Workflow

```
Discovery → Optimize → Validate (Gate B) → [Human Approval] → Paper → [Approval] → Live
```

Gate B thresholds (from MVP_CONSTRAINTS.md):
- Sharpe ratio >= 1.0
- Max drawdown <= 10%
- Hit rate >= 48%
- Minimum 300 trades in OOS period

---

## Document Hierarchy

The planning documents form a hierarchy from high-level vision to detailed requirements:

```
ROADMAP.md          → High-level milestones (v0.1.0 → v1.0.0)
    ↓                 Defines themes, goals, exit criteria per version
BACKLOG.md          → Version-grouped task list
    ↓                 Each task links to a requirement (US-XXX, TECH-XXX)
requirements/       → Executable user stories
                      Given-When-Then, API contracts, verification commands
```

### How They Link Together

1. **ROADMAP.md** defines version milestones (e.g., "v0.1.0 - Foundation")
2. **BACKLOG.md** lists tasks for each version, linking to requirements: `Req: [US-010](requirements/US-010-dashboard-integration.md)`
3. **requirements/US-XXX.md** provides testable acceptance criteria with verification commands

### v0.1.0 Requirements (All DONE)

| Requirement | Description | Status |
|-------------|-------------|--------|
| [US-010](requirements/US-010-dashboard-integration.md) | Dashboard with user info and system status | DONE |
| [US-011](requirements/US-011-market-data-error-handling.md) | Market Data page graceful error handling | DONE |
| [US-012](requirements/US-012-remove-broken-rundetail.md) | Remove broken RunDetail navigation | DONE |
| [TECH-010](requirements/TECH-010-fix-test-infrastructure.md) | Fix test infrastructure | DONE |

### v0.2.0 Requirements (Planned)

| Requirement | Description | Status |
|-------------|-------------|--------|
| [US-013](requirements/US-013-websocket-streaming.md) | Real-time WebSocket streaming (deferred from v0.1.0) | TODO |
| [US-001](requirements/US-001-optimization-runs.md) | Create and run optimization jobs | TODO |
| [US-002](requirements/US-002-view-optimization.md) | View optimization results | TODO |

---

## Key Documents

| Document | Purpose | Audience |
|----------|---------|----------|
| [BACKLOG.md](BACKLOG.md) | Current tasks by version | All agents |
| [ROADMAP.md](ROADMAP.md) | Release milestones | Product, all agents |
| [requirements/US-*.md](requirements/) | Testable requirements | Developer, tester agents |
| [architecture/PRINCIPLES.md](architecture/PRINCIPLES.md) | DO/DO NOT rules | All agents |
| [architecture/STANDARDS.md](architecture/STANDARDS.md) | Tech stack, coding rules | Developer agents |
| [architecture/AGENT_PROTOCOLS.md](architecture/AGENT_PROTOCOLS.md) | SDLC agent rules | All agents |
| [architecture/NFRS.md](architecture/NFRS.md) | SLOs, targets | All agents |
| [MVP_CONSTRAINTS.md](MVP_CONSTRAINTS.md) | Trading risk limits | Trading agents |

---

## For AI Agents: How to Use This System

### Starting a Task

1. Check BACKLOG.md for `TODO` items in current version
2. Find linked requirement (US-XXX) in requirements/
3. Read the requirement's:
   - Given-When-Then (what to build)
   - API Contract (exact interfaces)
   - Files Involved (where to code)
   - Verification Commands (how to test)

### During Development

1. Follow AGENT_PROTOCOLS.md for coding standards
2. Follow STANDARDS.md for technology choices
3. Follow PRINCIPLES.md for architectural decisions
4. Run verification commands frequently

### Completing a Task

1. Run all verification commands from the requirement
2. Update BACKLOG.md: change `TODO` → `DONE`
3. Update requirement if API contract changed
4. Document any architectural decisions in ADR

---

## Version Milestones

| Version | Theme | Status |
|---------|-------|--------|
| v0.1.0 | Foundation (Auth, Market Data) | DONE |
| v0.2.0 | Parameter Optimizer Agent | TODO |
| v0.3.0 | Validation Agent | TODO |
| v0.4.0 | Discovery Agent | TODO |
| v0.5.0 | Orchestrator & Evolution | TODO |
| v1.0.0 | Production Autonomy | TODO |

---

## Links to Other Docs

- Project overview: [/CLAUDE.md](/CLAUDE.md)
- Source structure: [/src/agentfx/](/src/agentfx/)
- Test structure: [/tests/](/tests/)
