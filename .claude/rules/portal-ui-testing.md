---
paths:
  - "portal/web/src/**"
---

# Portal UI: state-dependent rendering must be tested (AFX-55)

Any PR that touches a Settings component, or any portal page whose rendering depends on a backend opt-in flag, must include a Vitest for the pure selector that drives the rendering, covering **every branch the user can land on**.

Build and type checks do not catch a component that stops branching. AFX-7 shipped a Pool column that read "Free" whenever opt-in was off, though the DB column and types were right. Testing one state is not enough: pin every visible label change.

**Pattern: pure selector, node Vitest, no DOM**

1. Move the branching out of the JSX into a pure function `selectX(state, props) → { kind, label, ... }` that returns everything the user sees (labels, which cards render, tone classes, disabled flags). The component only renders what the selector returns.
2. Add `portal/web/tests/unit/<x>.test.ts` with one `it` per branch, asserting the label.
3. Do **not** pull in `@testing-library/react` or jsdom for this. Full-page coverage of every branch belongs in Playwright.

Templates:
- `selectPoolColumnView` in `portal/web/src/components/OandaAccountsTable.tsx`, pinned by `tests/unit/poolColumnView.test.ts`.
- `selectSettingsView`, `selectFleetPoolOptInView`, `selectVerifyOutcome` in `portal/web/src/pages/settingsView.ts`, pinned by `tests/unit/settingsView.test.ts`.

**CI gate:** `scripts/check_settings_vitest.py` (job `settings-ui-test-bar` in `ci.yml` and `agent-fleet-ci.yml`; locally `make check-settings-vitest`) flags a PR that changes a `SETTINGS_SURFACE` file without adding or changing a `portal/web/tests/unit/**/*.test.ts`. It is advisory for now. When Settings starts rendering a new local component, add it to `SETTINGS_SURFACE` (the unit suite fails until you do).
