---
status: completed
created_at: 2026-03-04
updated_at: 2026-10-04
files_edited:
  - partita_bot/notifications.py
  - tests/test_notifications_multicity.py
  - tests/test_bot_handlers.py
  - thoughts/shared/status/2026-03-04-onboarding-on-immediate-send.md
  - tests/test_scheduler.py
  - substrate/traces/operations/2026-03-04-onboarding-multi-city-fix.md
  - .dockerignore
  - .github/CONTRIBUTING.md
  - .gitignore
  - README.md
  - scripts/verify_container.py
  - tests/test_verify_container.py
rationale:
  - Prevent onboarding notifications from being skipped when the first city has no events.
  - Deliver every eventful configured city within the daily batch, rather than only the first.
  - Verify the real container image safely with the approved Podman alternative, without live credentials or user data.
supporting_docs:
  - thoughts/shared/status/2026-03-04-onboarding-on-immediate-send.md
  - substrate/traces/operations/2026-03-03-multi-city-city-only-validation.md
  - https://console.upstage.ai/api/systemone
  - https://openrouter.ai/upstage/solar-decide
  - https://pydantic.dev/docs/ai/models/typesafe/
---

# Summary of changes

- Adjusted notification fan-out logic so users are not marked as notified when a city has no events, allowing subsequent cities to deliver onboarding notifications.
- Added a targeted multi-city test to ensure one notification is queued when only later cities have events.
- Updated bot handler tests to stub fetchers correctly and to pin notification window values for the “outside window” scenario.

## Technical reasoning

- In `process_notifications`, adding users to `notified_users_today` on a no-event city prevented later cities with events from sending messages. Removing that mark keeps the per-day single-send guarantee while still permitting the first eventful city to queue a message.
- The new test simulates a two-city onboarding path (first city empty, second city with events) to guard the regression.
- Bot handler fakes now expose `fetch_event_message`, and the window is monkeypatched in the outside-window test to match the intended coverage.

## Impact assessment

- Users with multiple cities now receive onboarding notifications even if earlier cities have no events.
- No change to users with a single city or to the daily scheduler flow.
- Test coverage adds a regression guard for multi-city onboarding.

## Validation steps

- `ruff check .`
- `pytest --cov=. --cov-report=term`

## Update 2026-10-04: all eventful cities in the daily batch

### Summary of changes

The local patch fixes first-city-only delivery in `process_notifications`. Scheduler, onboarding, and admin bulk notifications now queue a separate unchanged message for every eventful city configured by an eligible user. The March work above remains historical evidence; its first-eventful-city single-send decision is superseded by this correction.

### Technical reasoning

The previous `notified_users_today` set skipped later cities after the first successful queue. Removing that set alone would not suffice: `update_last_notification` immediately mutates the SQLAlchemy user object, so the next city would fail the daily eligibility check.

The patch snapshots users already notified before the batch, then allows all remaining city groups to queue. Separate sets count skipped users once and update each successfully notified user's timestamp once. It reuses existing grouping, rich-message formatting, and queue serialization without aggregation, dependencies, schema changes, or scheduler redesign. The root cause was verified before delegation, making a single bounded backend implementation sufficient instead of a competitive race.

The existing one-message multi-city test was renamed and strengthened to assert two distinct city messages. Added tests cover three cities, overlapping subscriptions, access restrictions, repeated same-day runs, already-notified users, empty and failing cities in both orders, queue failures, manual timestamps, and rich-message metadata. An integration test exercises the real cached fetcher, formatter, queue, delivery worker, and sent-row persistence; only the Telegram boundary is simulated, and any HTTP request fails the test.

### Impact assessment

Users can receive up to three city messages in one daily batch. Fetches remain shared per distinct city, and subsequent same-day batches remain suppressed per user. `notifications_sent` counts queued messages, not unique users; `already_notified` counts users once. Access filtering, manual cooldowns, rich links, and preview settings are unchanged.

Partial success still marks the user notified for the day: a city that fails after another succeeds has no per-city retry on later runs. That existing limitation is not fixed by this patch. No production credentials, local user database, providers, or running deployments were changed. The patch is uncommitted and unpublished because mandatory Docker verification remains blocked.

### Solar Decide and Jev assessment

Documentation was checked on 2026-10-04 using a web-research subagent and independently fetched sources. [Upstage's System One API](https://console.upstage.ai/api/systemone) documents Solar Decide as a beta decision model returning choices, scores, and yes/no probabilities rather than generated prose. [OpenRouter's Solar Decide description](https://openrouter.ai/upstage/solar-decide) confirms it shares the System One schema with Jev. [Pydantic AI's Jev documentation](https://pydantic.dev/docs/ai/models/typesafe/) identifies Jev as TypeSafe's decision model and warns about dates, arithmetic, adversarial input, and thresholds that must be tested on labelled examples.

These models do not retrieve current local events or generate canonical city names. They cannot directly replace Exa's web-backed gate or the existing city/team classification contract, which also returns free-form city names. The notification fan-out bug is deterministic application logic and needs no model.

The most plausible experiment is semantic duplicate detection on ambiguous candidate pairs already retrieved from public sources; event-category classification is another possible use if a concrete requirement arises. Keep exact date/source checks and existing city validation in code. Do not use model probabilities as factual confirmation, security enforcement, or a reason to silently drop uncertain events. Queue routing and daily deduplication should remain deterministic.

Recommendation: no integration now. First measure a shadow-mode classifier against labelled Italian event pairs and the existing URL-based deduplication baseline. Evaluate accuracy, false merges, latency, actual cost, and provider retention before deciding. No model inference or Italian-language benchmark was performed, so neither provider is selected.

### Validation steps

- Baseline: clean, synchronized `main`, HEAD `107d0015e8447c305b51148c74a34ff87c09904f`; no repository `AGENTS.md`, directives, or expectations directories.
- Independently inspected the diff and actual contents of all three changed Python files; `git diff --check` passed.
- `ruff check .` passed using a disposable Python 3.13.5 environment.
- `pytest --cov=. --cov-report=term --cov-branch` passed: 361 tests, 100% line and branch coverage for `partita_bot/notifications.py`, 87% total including tests. The suite ran from a disposable repository copy excluding `.env` and `data`, with API credentials unset.
- `docker bake`, Compose build/start, log inspection, and teardown commands were attempted; all returned exit 127 because `docker` is unavailable. No container was started.
- `quality-gate-a` returned `PASS` for the judgment pass, explicitly retaining the Docker blocker. Its non-blocking advisory notes that the timestamp-update guard is covered but its exact call count is not asserted.
- `security-review-specialist-a` returned `PASS` for the focused read-only implementation-delta review.
- The complete deterministic gate is blocked, and the quality cursor has not advanced beyond the session baseline. The remaining action is to run the mandatory Docker checks on a Docker-capable host before publication or deployment.

### Resumption after scope confirmation

The user confirmed that classifiers must not be integrated and that the multi-city bug must be corrected. The existing local correction was independently rechecked against the original baseline: `main` remains synchronized, the code and tests are unchanged from the reviewed 361-test patch, and `git diff --check` passes. No further application changes are needed for the reported first-city suppression.

All four mandatory Docker commands were attempted again and returned exit 127. Rootless Podman 5.4.2 is available, but the repository currently requires Docker checks; it was not silently substituted or reported as a passing Docker gate. Publication remains blocked pending Docker verification or explicit approval to amend the verification contract to equivalent checks on the available container runtime.

## Update 2026-10-04: approved Podman verification and gate closure

### Summary of changes

The user approved adapting the mandatory container checks to rootless Podman. `.github/CONTRIBUTING.md` now requires lint, the full test suite, and `python -m scripts.verify_container --runtime podman` (or Docker). This is an executed equivalent build, runtime, log inspection, and teardown check, not acceptance of the previous missing-Docker failure. No classifiers were integrated.

The standard-library verifier builds the unchanged production Dockerfile and starts its actual entrypoint in bot and admin modes. The bot branch runs the offline cached multi-city worker path with only Telegram delivery simulated. The admin branch starts real Gunicorn and checks unauthenticated, wrong-password, and authenticated dashboard requests. README documents this scope, including the untested live scheduler and polling loop.

### Technical reasoning

The local Compose configuration can bind real `data/` and load real API credentials. The replacement verifier uses synthetic environment values, no host data mounts, no exposed ports, and network-isolated containers. `.dockerignore` prevents credential files, SQLite data, caches, and private status records from entering the build context. The image's non-root user and production entrypoints are unchanged.

Each service gets private volatile scratch storage. Mode 1777 permits the existing non-root user to create its synthetic database without unsupported UID/GID tmpfs options; these permissions do not affect host files or production volumes. Read-only root filesystems, dropped capabilities, no-new-privileges, and bounded memory, processes, and CPU remain enforced.

Independent inspection required explicit checks of both the runtime CLI status and the contained worker exit code, successful log retrieval and admin shutdown, finite timeouts, and cleanup failure propagation. Regression tests cover these cases, interrupted runs, and both runtime argument variants. Newly added comments violated the repository rule and were removed through bounded sequential delegation, preserving the useful portability fix. No comments, dependencies, production Compose changes, or CI modifications were added.

### Impact assessment

The multi-city application fix remains unchanged from the reviewed patch. The verification contract now has a reproducible local Podman or Docker alternative, with no real Telegram/Exa traffic or user-database access. It does not prove live Telegram polling or production deployment. All task-owned container resources were removed; unrelated images and services were not touched.

### Validation steps

- Rechecked synchronized default `main` against `107d0015e8447c305b51148c74a34ff87c09904f`; preserved the earlier fix and separated its changes from the approved verification follow-up in an ignored workspace snapshot.
- Independently read changed files and inspected diffs; verified the tested artifact copies match the worktree and `git diff --check` passes.
- `ruff check .` passed.
- `pytest --cov=. --cov-report=term --cov-branch` passed: 406 tests, notifications 100% line/branch coverage, verifier 88% combined line/branch coverage, and 88% total including tests. Tests ran from an isolated copy without `.env` or real data, using Python 3.11.16 with API credentials unset and dotenv loading disabled.
- The real Podman 5.4.2 image check passed using image Python 3.13: `VERIFY_WORKER_SMOKE_OK` reported two cities and two deliveries; admin requests returned 401, 401, and 200; Gunicorn stopped cleanly; `VERIFY_CONTAINER_OK` confirmed cleanup.
- Independently confirmed no task-owned containers or image tags remain, and removed the exact task-pulled Python base-image ID without pruning unrelated resources.
- `quality-gate-a` returned `PASS`; `security-review-specialist-a` returned `PASS`. The non-blocking quality advisory notes that the tests import `freezegun` through the already installed `pytest-freezer` dependency; no dependency change was necessary for the verified environment.
- The complete revised deterministic and judgment gates pass, so the previous publication blocker is closed and the quality cursor advances to this verified candidate. No blocking security finding remains.
- GitHub's current repository policy was checked: default branch `main`, active account has push/admin permission, no applicable rulesets, and no branch protection. Normal direct publication is authorized; no force push, bypass, or policy change is needed.
