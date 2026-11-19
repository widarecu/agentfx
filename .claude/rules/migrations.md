---
paths:
  - "migrations/**"
  - "alembic.ini"
  - "src/agentfx/api/models/**"
---

# Database migrations

- The backend container runs `alembic upgrade head` on start-up, so a broken migration stops the whole stack (symptom: `relation "users" does not exist`; check `make docker-logs PROFILE=test`).
- **Keep a single head.** Before opening the PR, run `alembic heads` on your branch, then check the result still has one head when combined with `origin/main`. `feat/agent-fleet` and `main` both receive migrations (hotfixes go straight to `main`), so two heads can appear only at promote time:

  ```bash
  git fetch origin main
  git log --oneline origin/main -- migrations/versions | head   # migrations main has that you don't
  ENVIRONMENT=test PYTHONPATH=src .venv/bin/alembic heads
  ```

  If `main` added a migration after your branch point, set your `down_revision` to the latest head on `main`.
- Test against the test profile only: `make docker-up PROFILE=test`, then `make test-integration`.
