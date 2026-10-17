# AGENTS.md — rules for every AI agent working in this repository

This repository is the **Backend & Ingestion** module of CivicWater Sentinel. Several people
and AI agents work on it without sharing chat history: the files below are the only shared
memory. These rules are mandatory.

## 0. Map

| Need | Read |
|---|---|
| What exists and what does not | `docs/status.md` |
| Components, data flow, ownership | `docs/architecture.md` |
| Tasks, priorities, acceptance criteria | `docs/implementation-plan.md` |
| Contracts with other modules | `docs/integration-contracts.md`, `src/sentinel/schemas/` |
| Blockers and owners | `docs/dependencies.md`, `docs/questions-for-team.md` |
| Decisions and why | `docs/adr/` |
| How to test / measure | `docs/testing.md`, `docs/performance.md` |

Commands: `just --list`. Gates: `just check` (format → lint → typecheck → test).

## 1. Before starting

1. Read this file and any `AGENTS.md` in the directories you will modify.
2. Read `README.md`, `docs/architecture.md`, `docs/status.md`.
3. Read `docs/implementation-plan.md` and `docs/dependencies.md`.
4. Identify the task ID (`BE-NNN`) and its dependencies. No ID → add the task to the plan first.
5. Check for an existing implementation or ADR (`rg -n <keyword> src docs`).
6. State the scope of the change and the files you expect to touch.
7. Identify conflicts with contracts or with other members' modules. Do not redo existing
   work without stating why.

## 2. While implementing

- Respect module boundaries: `api` (HTTP only) → `services` (rules) → ports
  (`forecasting`, `detection`, `audit`). No business rules in routes.
- Reuse the contracts in `sentinel.schemas`. Incompatible contract changes are **proposed
  first** (issue/PR + `docs/questions-for-team.md`), never merged silently (contracts §6).
- New or changed architectural decision → new/updated ADR (`docs/adr/README.md`).
- Every new behaviour gets a test in the same change.
- Never invent data, sensor IDs, units, metrics, test results or APIs of other modules.
  Unknown → mark as provisional and register the question.
- Do not modify other owners' modules without need; do not delete others' work without
  authorisation.
- No credentials, tokens, `.env`, datasets (BattLeDIM), model artefacts or large files in git.
- Never hide mock limitations: mocks keep `is_mock=True`; never present mock output as
  leak detection; never wire a mock where a real component was requested.
- Record observed bottlenecks and optimisation ideas in `docs/performance.md` (with the
  measurement), not as unmeasured claims.
- Keep changes small and reviewable.

## 3. Before declaring a task finished

1. Run the relevant gates (`just check` at least for the touched code).
2. Record the exact commands you ran.
3. Report which tests passed and which failed (paste the summary line).
4. Update `docs/implementation-plan.md` (task status).
5. Update `docs/status.md` (date, summary, verification section).
6. Update `docs/dependencies.md` if a dependency/blocker changed.
7. Update `CHANGELOG.md` (section `Unreleased`) for relevant changes.
8. Update ADRs and `docs/integration-contracts.md` (+ `docs/examples/`) when affected.
9. List files created, modified, removed.
10. State limitations, risks, technical debt and future optimisations.

If tests cannot be run, mark them `NOT_RUN`, explain why, and do **not** report success.
If two documents disagree about status, investigate the code and reconcile them; never
pick one arbitrarily.

## 4. Mandatory completion report

Every task ends with this report (in the PR description or the final message):

```text
Task: BE-NNN — <description>
Status: DONE | IN_PROGRESS | BLOCKED
Implemented: <concrete changes>
Files changed: <path — purpose>
Tests: <commands run> → <observed results, e.g. "92 passed">
Contracts: <interfaces created/changed, version>
Dependencies resolved: <what was unblocked>
Pending: <what is missing>
Blockers: <owner / information needed>
Bottlenecks: <performance/reliability problems seen>
Future optimisations: <proposal — priority — justification>
Risks and technical debt: <remaining limitations>
Next recommended action: <one concrete task>
```

The report must reflect the real state of the code. "Implemented and tested" alone is not a
report.

## 5. Persistent state

`docs/status.md` is the project state. Keep it current **in the same change** as the code:
last update, summary, done / in progress / blocked tasks, pending items, external
dependencies, risks, known bottlenecks, future optimisations, next step, verification.

## 6. Git and collaboration

- Branch per task (`feat/`, `fix/`, `docs/`, `refactor/`, `test/`), PR into `main`.
  No direct commits to `main`.
- Conventional Commits, imperative subject ≤ 72 chars, English.
- Small, coherent commits. Inspect `git status` and `git diff` before every commit/push.
- Never: `push --force`, `reset --hard`, `clean -fd`, history rewrite of pushed branches,
  `--no-verify` (the pre-commit hook runs gitleaks; a finding must be investigated).
- Preserve other people's work; resolve conflicts, do not discard.

## 7. Facts that are easy to get wrong

- The data is **simulated** (BattLeDIM L-Town). Never describe results as validated on a
  real network.
- Sensor IDs in `config/sensors.provisional.json` are **placeholders**.
- History, results, alerts and metrics live **in memory** and are lost on restart (ADR-003).
- The detector is stateful: one instance per process, called in order (contracts §3).
- The window timestamp is the **end** of its 5-minute interval (contracts §1).
- Start the server with `just dev` (factory: `uvicorn sentinel.main:create_app --factory`);
  run a single worker.
