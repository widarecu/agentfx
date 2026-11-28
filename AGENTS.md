# Agent guidance

Read [CLAUDE.md](CLAUDE.md) first. Before changing any files, consult its
[Read before changing](CLAUDE.md#read-before-changing) table and read the files
it lists for the paths you will touch.

If a PR you raised conflicts with `main`, you resolve it: merge `origin/main`
into your branch, fix the conflicts, run `make lint` and `make test-unit`, and
push. A Money-path PR then needs Wilson's approval again on the new commit.
See section 4 of the
[delivery pipeline runbook](planning/runbooks/delivery-pipeline.md#4-review-normal-and-money-path).
