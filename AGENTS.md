# AGENTS.md

This file defines the default development workflow for this repository. The goal is to cut down on repeated discussion, reduce rework, and keep changes consistent with the current project structure.

Where this file disagrees with the scripts, workflows, or code actually in the repository, the executable content wins. Fix the documentation as part of the related change so the rules do not drift further.

## 1. Hard rules

- Respect the existing directory boundaries:
  - Backend logic belongs in `src/`, `data_provider/`, `api/`, `bot/`
  - Web frontend changes go in `apps/dsa-web/`
  - Desktop changes go in `apps/dsa-desktop/`
  - Deployment and pipeline changes go in `scripts/`, `.github/workflows/`, `docker/`
- Do not run `git commit`, `git tag`, or `git push` without explicit confirmation.
- Write commit messages in English. Do not add `Co-Authored-By`.
- Never hardcode secrets, accounts, paths, model names, ports, or environment-specific logic.
- Prefer reusing existing modules, configuration entry points, scripts, and tests over adding a parallel implementation.
- Stability comes before opportunistic optimisation. Hold back on refactors, abstractions, and infrastructure migrations that the current task does not directly require.
- When adding a configuration option, update `.env.example` and the related documentation in the same change.
- When user-visible capabilities, CLI/API behaviour, deployment, notifications, or report structure change, update the related documentation and `docs/CHANGELOG.md` in the same change.
- When changing report formatting, report rendering, or the Web UI, the PR description must include screenshots of the affected report or page. Prefer before/after comparisons where a difference exists; if a screenshot is impossible, explain why and supply alternative visual evidence.
- Screenshots from issue/PR discussion, review screenshots, one-off acceptance screenshots, and other temporary visual evidence must not be merged as repository files. Put them in the PR description, PR comments, GitHub attachments, Actions artifacts, or an externally accessible evidence link. Diagrams that genuinely belong in long-lived product documentation are the exception, but their filenames and meaning must not be tied to a specific issue or PR number.
- The `[Unreleased]` section of `docs/CHANGELOG.md` uses a **flat format**: one entry per line, written as `- [type] description`, where type is one of `新功能` (feature) / `改进` (improvement) / `修复` (fix) / `文档` (docs) / `测试` (test) / `chore`. **Do not add `### category headings` inside `[Unreleased]`** — this keeps merge conflicts down across concurrent PRs. At release time the maintainer consolidates the entries into the formal, headed format.
- `README.md` is only for front-page material: what the project is, an overview of core capabilities, quick start, main entry points, and sponsorship/partnership information. Do not update the README unless necessary — it should not keep growing.
- Finer-grained module behaviour, page interactions, topic-specific configuration, troubleshooting notes, field contracts, implementation semantics, and edge cases belong in the corresponding `docs/*.md` or topic document, not in the README.
- When changing one half of a bilingual document pair, assess whether the other half needs the same change. If you do not sync it, say why in the delivery notes.
- Comments, docstrings, and log messages are judged on clarity and accuracy. English is not mandatory, but they should match the surrounding context of the file.

## 1.1 PR title convention (advisory, non-blocking)

- Prefer `<type>: <what changed>` as the PR title, for example `fix: restore lost market review history`. Preferred types are `fix`/`feat`/`refactor`/`docs`/`chore`/`test`/`ci`.
- The title should describe the actual change. Do not add `[codex]`, `codex`, `autocode`, `copilot`, or any other tool/agent source prefix.
- This convention exists for readability and consistency. It is not on its own a review blocker.

## 1.2 Contribution quality bar

- This repository does not accept PRs that substitute stacked-up code, a wider diff, or patch-by-patch responses to review for real design convergence.
- Contribution quality is judged on whether it solves a clearly stated problem, minimises blast radius, keeps existing contracts consistent, and covers the real risk paths. It is not judged on lines added, file count, feature marketing, or looking complete.
- Please do not treat this repository as a low-cost sandbox, a resume showcase, or a contribution-farming target. Every PR must demonstrate that the author understands the current system contracts and has done basic self-review, integration, and verification.
- Using AI assistance is not itself a problem. The problem is submitting AI-generated code that has not been semantically reviewed by a human, not verified, and not converged. Such PRs are treated as low-quality submissions.
- After review feedback, appending a local patch only at the spot the reviewer named is not acceptable. The author must re-examine every entry point, configuration, test, document, workflow, and user-visible path that shares the same business semantics.
- If a PR keeps producing the same class of problem across several review rounds — contract drift, repeated fallbacks, tests that bypass the real risk layer, a PR body that does not match the actual diff — the maintainer may ask for it to be closed and redone rather than continuing point-by-point review.

## 2. Governance of AI collaboration assets

- `AGENTS.md` is the single source of truth for in-repository AI collaboration rules.
- `CLAUDE.md` must be a symlink to `AGENTS.md`, for compatibility with the Claude ecosystem.
- `.github/copilot-instructions.md` and `.github/instructions/*.instructions.md` are mirrors or layered supplements for GitHub Copilot / Coding Agent. Where they conflict with this file, `AGENTS.md` wins.
- Repository collaboration skills live in `.claude/skills/`; analysis output lives in `.claude/reviews/`. The former may be committed; the latter is treated as a local artifact by default.
- The root `SKILL.md` and `docs/openclaw-skill-integration.md` are product or external-integration documentation, not a source of truth for repository collaboration rules.
- If an `.agents/skills/` directory or any other agent-specific directory is added later, establish the single source of truth first, then sync via a script or mirror. Maintaining several copies of the same content by hand over the long term is not allowed.
- When changing AI collaboration governance assets, run:

```bash
python scripts/check_ai_assets.py
```

## 3. Repository at a glance

- What this project is: an intelligent stock analysis system covering A-shares, Hong Kong, and US markets.
- Main flow: fetch data -> technical analysis / news retrieval -> LLM analysis -> generate report -> push notifications.
- Key entry points:
  - `main.py`: main entry point for analysis tasks
  - `server.py`: FastAPI service entry point
  - `apps/dsa-web/`: web frontend
  - `apps/dsa-desktop/`: Electron desktop app
  - `.github/workflows/`: CI, releases, daily tasks
- Core responsibilities:
  - `src/core/`: main flow orchestration
  - `src/services/`: business service layer
  - `src/repositories/`: data access layer
  - `src/reports/`: report generation
  - `src/schemas/`: schemas and data structures
  - `data_provider/`: multi-source adapters and fallback
  - `api/`: FastAPI API
  - `bot/`: bot integrations
  - `scripts/`: local scripts
  - `.github/scripts/`: GitHub automation scripts
  - `tests/`: pytest tests
  - `docs/`: documentation

## 4. Common commands

### Running the application

```bash
python main.py
python main.py --debug
python main.py --dry-run
python main.py --stocks HCM,TCB,FPT
python main.py --market-review
python main.py --schedule
python main.py --serve
python main.py --serve-only
uvicorn server:app --reload --host 0.0.0.0 --port 8000
```

### Backend verification

```bash
pip install -r requirements.txt
pip install flake8 pytest
./scripts/ci_gate.sh
python -m pytest -m "not network"
python -m py_compile <changed_python_files>
```

### Web / Desktop

```bash
cd apps/dsa-web
npm ci
npm run lint
npm run build

cd ../dsa-desktop
npm install
npm run build
```

### PR / CI evidence

```bash
gh pr view <pr_number>
gh pr checks <pr_number>
gh run view <run_id> --log-failed
```

## 5. Default workflow

1. Identify the task type first: `fix / feat / refactor / docs / chore / test / review`
2. Read the existing implementation, configuration, tests, scripts, workflows, and documentation before changing anything.
3. Identify the change boundary: backend / API / Web / Desktop / Workflow / Docs / AI collaboration assets.
4. Determine whether the change touches a high-risk area: configuration semantics, API / schema, data source fallback, report structure, authentication, scheduling, the release process, or the desktop startup chain.
5. Make only the minimal change that the current task directly requires. Do not smuggle in unrelated refactors.
6. If documentation, scripts, or workflow descriptions turn out to be inconsistent, trust the actual code and workflows first, then decide whether to fix the documentation along the way.
7. After the change, run the checks in the verification matrix below.
8. Final delivery should by default cover:
   - What changed
   - Why it changed this way
   - What was verified
   - What was not verified
   - Risks
   - How to roll back

## 6. Verification matrix

### CI coverage

The repository CI currently consists of:

| Check           | Source                                | Description                                                                                              | Blocking             |
| --------------- | ------------------------------------- | -------------------------------------------------------------------------------------------------------- | -------------------- |
| `ai-governance` | `.github/workflows/ci.yml`            | Validates the relationship between `AGENTS.md` / `CLAUDE.md` / `.github` instructions / `.claude/skills` | Yes                  |
| `backend-gate`  | `.github/workflows/ci.yml`            | Runs `./scripts/ci_gate.sh`                                                                              | Yes                  |
| `docker-build`  | `.github/workflows/ci.yml`            | Docker build plus an import smoke test of key modules                                                    | Yes                  |
| `web-gate`      | `.github/workflows/ci.yml`            | Runs `npm run lint` + `npm run build` when the frontend changes                                          | Yes (when triggered) |
| `network-smoke` | `.github/workflows/network-smoke.yml` | `pytest -m network` + `scripts/test.sh quick`                                                            | No, observational    |
| `pr-review`     | `.github/workflows/pr-review.yml`     | PR static checks + AI review + automatic labelling                                                       | No, advisory         |

If the PR already has the corresponding CI result, cite the CI conclusion directly. If CI does not cover the change, or the local environment differs substantially from CI, describe the local verification and the gap.

### By area of change

- Python backend changes:
  - Scope: `main.py`, `src/`, `data_provider/`, `api/`, `bot/`, `tests/`
  - Preferred: `./scripts/ci_gate.sh`
  - Minimum: `python -m py_compile <changed_python_files>`
  - If the change affects the API, task orchestration, report generation, notification sending, data source fallback, authentication, or scheduling, state in the delivery notes whether the corresponding path was covered.

- Web frontend changes:
  - Scope: `apps/dsa-web/`
  - Default: `cd apps/dsa-web && npm ci && npm run lint && npm run build`
  - If the change involves API integration, routing, state management, Markdown/chart rendering, or authentication state, state clearly in the delivery notes what is coupled and what risk is uncovered.

- Desktop changes:
  - Scope: `apps/dsa-desktop/`, `scripts/run-desktop.ps1`, `scripts/build-desktop*.ps1`, `scripts/build-*.sh`, `docs/desktop-package.md`
  - Default: build the web app first, then the desktop app
  - If platform constraints prevent full verification, state explicitly whether the web build output, the Electron build, and the effect on the Release workflow were verified.

- API / schema / authentication changes:
  - Scope: `api/**`, `src/schemas/**`, `src/services/**`, `apps/dsa-web/**`, `apps/dsa-desktop/**`
  - Cover at least the corresponding backend verification plus a build of the affected clients.
  - If login, cookies, sessions, polling state, field additions/removals, or enum changes are involved, spell out the compatibility impact.

- Documentation and governance file changes:
  - Scope: `README.md`, `docs/**`, `AGENTS.md`, `.github/copilot-instructions.md`, `.github/instructions/**`, `.claude/skills/**`
  - Code tests are not required.
  - Confirm that commands, configuration options, filenames, and workflow names match the actual repository.
  - When changing AI collaboration governance assets, run `python scripts/check_ai_assets.py`.

- Workflow / script / Docker changes:
  - Scope: `.github/**`, `scripts/**`, `docker/**`
  - Run the local verification closest to the change.
  - On delivery, state which pipeline, release path, or deployment path is affected.
  - If Docker or GitHub Actions verification was not run, state why and the potential risk.

- Network or third-party dependency changes:
  - Run the offline or deterministic checks first.
  - Confirm that timeouts, retries, fallbacks, error messages, and degradation paths still hold.
  - If online verification was not run, state why.

## 7. Stability guardrails

- Configuration and runtime entry points:
  - When changing `.env` semantics, default values, CLI arguments, how the service starts, or scheduling semantics, assess the impact on local runs, Docker, GitHub Actions, the API, Web, and Desktop.
  - New configuration should aim for "works without being set, adds capability when set". Avoid piling on switches and mutually exclusive modes.

- Data sources and fallback:
  - When changing `data_provider/`, pay attention to source priority, degradation on failure, field normalisation, caching, and timeout strategy.
  - A single source failing must not bring down the whole analysis flow, unless the requirement explicitly calls for fail-fast.

- API / Web / Desktop compatibility:
  - When changing the API, schemas, authentication, or report payloads, check backend, Web, and Desktop compatibility together.
  - Prefer adding fields, keeping old fields, or providing a compatibility layer. Do not break existing clients silently.

- Reports / prompts / notifications:
  - When changing report structure, prompts, extractors, notification templates, or the bot chain, check that upstream inputs and downstream consumers remain compatible.
  - A single notification channel failing must not bring down the main analysis flow, unless the requirement explicitly calls for fail-fast.
  - When changing `EXTRACT_PROMPT` in `src/services/image_stock_extractor.py`, include the complete updated prompt in the PR description.

- Workflows / releases / packaging:
  - When changing automatic tagging, Release, Docker publishing, the daily analysis job, or desktop packaging, assess trigger conditions, artifact paths, permission boundaries, and how to roll back.
  - Automatic tagging stays opt-in by default: only a commit title containing `#patch`, `#minor`, or `#major` triggers a version bump, unless the requirement explicitly calls for a different release policy.

## 8. Issue / PR / skill workflow

- The repository already has these skills; prefer reusing them:
  - `.claude/skills/analyze-issue/SKILL.md`
  - `.claude/skills/analyze-pr/SKILL.md`
  - `.claude/skills/fix-issue/SKILL.md`
- If the task is clearly issue analysis, PR review, or issue fixing, follow the corresponding skill and save the output to `.claude/reviews/`.
- The commands, templates, verification order, and delivery structure inside a skill must stay consistent with `AGENTS.md`.
- Before creating or updating a PR, reviewing a PR, or analysing an issue, sync to the latest baseline first: check the working tree state and run `git fetch --all --prune`; if the working tree is clean and the current branch can fast-forward, run `git pull --ff-only`. If there are local changes, a conflict state, risky untracked files, or the branch cannot fast-forward, do not force a branch switch, stash, reset, or overwrite local state. PR review and issue analysis may instead work from the already-fetched remote refs / PR head; in that case, record in the analysis document why the local working tree was not updated, what the current local HEAD is, and which remote baseline was used. PR creation and updates should first state the difference between the current branch and the target baseline, and where necessary ask the user to confirm a rebase, a merge, or continuing on the current branch.
- Skills should read CI and workflow evidence first, then decide whether local verification needs to be added.
- Apart from the safe fast-forward sync described above for PR creation/update and PR/issue analysis, skills must not run `git pull`, `git push`, `git tag`, `gh pr create`, or any other operation that changes remote or current branch state by default. These operations require user confirmation.
- Default PR review order:
  1. Necessity
  2. Relevance
  3. Title suggestion (`<type>: <what changed>`, with no tool/agent prefix; not a hard blocker)
  4. Description completeness (against `.github/PULL_REQUEST_TEMPLATE.md`)
  5. Verification evidence
  6. Implementation correctness
  7. Merge decision
- For `fix` PRs, state: the original problem, the root cause, the fix, and the regression risk.
- Merge blockers:
  - Correctness or security problems
  - A blocking CI check that did not pass
  - A PR description that materially contradicts the actual change
  - No rollback plan
  - Recurring unconverged contract drift, stacked patches, or distorted verification evidence

## 8.1 Handling review feedback; no stacked patches

When you handle review feedback, you must not append a local patch only where the reviewer pointed and then claim everything is fixed. First re-read the business contract the reviewer identified, then check every entry point, configuration, test, document, workflow, and user-visible path that shares the same semantics.

Work through review feedback in this order:

1. List each problem the reviewer raised.
2. Explain the root cause. Describing which lines changed is not enough.
3. Find every path affected by the same semantics — runtime, API/Web, CLI, diagnostics, workflow, docs, tests.
4. Fix the complete contract, not just the currently failing test or the line under the comment.
5. Add regression tests covering the reviewer's counterexample, verify at the real entry point, or state explicitly why verification is not possible.
6. Update the PR body so that scope, verification results, compatibility, risks, and the rollback plan match the current head.

If you cannot achieve that convergence, do not keep stacking patches and do not claim the PR is ready for merge. Say instead that the PR needs to be split, closed and redone, or that you need the maintainer to confirm a new minimal scope.

The following are treated as low-quality PRs:

- Using a broad fallback, silent degradation, or `return False/None/[]` to paper over an unclear contract.
- Tests that mock away the real risk layer and only prove the local implementation passes.
- Claiming the problem is closed once CI is green, without covering the counterexample the reviewer raised.
- A PR body inconsistent with the actual diff, the verification results, or the compatibility risk.
- Continuing to append scattered patches after review instead of reconverging on the full semantics.
- The same business semantics behaving inconsistently across runtime, Web/API, docs, workflow, and tests.

Green CI only means the automated checks passed. It does not replace human semantic convergence, and on its own it does not prove the reviewer's counterexample is closed.

## 9. Delivery and release

- Default delivery structure:
  - `What changed`
  - `Why it changed this way`
  - `What was verified`
  - `What was not verified`
  - `Risks`
  - `How to roll back`
- For a `docs` task you may simply write `Docs only, tests not run`, but still state whether commands and filenames were checked.
- Automatic tagging does not fire by default. Only a commit title containing `#patch`, `#minor`, or `#major` triggers a version bump.
- Manual tags must be annotated tags.
- User-visible changes should be merged through a PR, with labels and verification notes filled in.
