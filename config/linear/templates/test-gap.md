## Why
_Name the stage this advances: discovery, backtest, validation, paper, live readiness, or platform reliability. Explain why it matters._

## What
_Describe the missing coverage and how a bug could slip through._

## Out of scope
_What behaviour or areas will not change._

**Off-limits:** `dev` / `uat` profiles, live OANDA keys, pushing to `main`.

## Evidence
_Link the escaped bug, uncovered code path, or other evidence of the gap._

## Done when
- [ ] Given _starting conditions_, when _action_, then _observable outcome_. **Test level:** _unit / integration / E2E_. **Command:** `make …`.
_Show that the proposed test fails when the behaviour is broken._

_Use one checkbox per criterion, each with a test level and verification command. Use the test profile for E2E._

## Open questions
_None, or up to three questions starting with `[NEEDS CLARIFICATION]`. If any remain, add `needs-wilson`._

**Suggested priority:** _Urgent / High / Medium / Low — one sentence explaining why._

>>> Agent notes

- **Files:** _Paths confirmed by grep._
- **Current behaviour:** _What the code does today._
- **Approach:** _Proposed tests and tasks, in order._
- **Dependencies:** _Issues, commits, or prerequisites (or none)._
- **Size:** _S / M / L (split L before filing)._

>>>
