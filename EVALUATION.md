# Evaluation methodology

This document describes the implemented benchmark, including its September 2026 transfer pilot. It is not a claim that the synthetic environments reproduce real customer operations.

## Unit of evaluation

A system is a declared model, agent source, harness, and resource budget. An **agent session** uses development information to produce a deployment. A **workload replicate** evaluates that frozen deployment on another generated stream. A **case mechanism** defines the operational problem. None of these counts substitutes for independently sampled customer projects.

The transfer pilot uses three frozen texts × two cases, one agent session per condition, and three final seeds (701–703). It is a comparison of instruction conditions with a fixed model and harness, not a comparison of model generations. `transfer` currently requires one agent session per condition; additional independent repetitions would need an explicitly revised experimental protocol.

## Repeated Grok experiment

The newer [`repeat` experiment](experiments/grok45-v1/README.md) adds external-effect recovery and runs 45 fresh sessions: three frozen instruction conditions × three mechanisms × five repetitions. Each policy is evaluated on five workload seeds, nested within its session. Average over seeds inside each session first, then report the five session means and their sample standard deviation. The original `transfer` command remains a separate one-session pilot.

Recovery simulates ambiguous acceptance, idempotency semantics, delayed status visibility, and loss of client process state. Its configuration controls timeout action, retry key, response to absence, query wait, and state persistence. Actual SQLite effects and audit rows determine completion, duplicate effects, unresolved jobs, queries, retries, and synthetic recovery delay. The three endpoint variants are one authored mechanism, not three independent cases. Repeated deliveries appear as separate job observations across phases; phase totals are not unique lifetime jobs.

The primary condition requires completed deployment and session termination, plus full correct support coverage without wrong/duplicate effects or backlog; exact inventory with no stale/duplicate writes; or exactly-once confirmed recovery without unresolved jobs. SLA and resource metrics remain separate. See the [results and limitations](experiments/grok45-v1/REPORT.md): 19 sessions met their condition, and all other sessions timed out. All deployed policies met their quality condition.

```bash
./bench repeat --registration experiments/grok45-v1/registration.execution.json --out runs/new-grok45
```

The supplied registration archives a local catalog path. For another machine, copy the registration, update the path to its included catalog, and record that change. Real calls require the authenticated OpenCodex route. `--resume` reuses verified completed sessions, including failures; it refuses ambiguous interrupted sessions rather than silently trying again. The concurrency cap was amended from three to two before any case session, following case-free connection checks. No timeout or prompt changes occurred during the 45 sessions.

## Cases and measurements

| Property | Support routing | Inventory reconciliation |
|---|---|---|
| Incumbent | Default manual-routing policy | Arrival-order absolute writes, no deduplication, process-local metadata |
| Allowed change | Text routing rules, fallback queue, deduplication | Deduplication key, arrival/revision ordering, absolute/delta writes, process/durable metadata |
| Phases | Active, increased volume, changed wording | Active, increased volume, restart simulation |
| State between phases | Each phase starts fresh | Stock continues; restart closes/reopens the connection and loses TEMP metadata |
| Main quality measurements | Correct unique tickets, correct within SLA, wrong routes, duplicate effects, backlog | Correct entities, absolute stock error, stale writes, duplicate writes |
| Synthetic operator cost | Five minutes per completed manual event | Five minutes per incorrect entity plus stale and duplicate writes |

Support backlog counts events; it is not interchangeable with unique uncompleted tickets. Correct-on-time counts unique tickets processed correctly at least once within the SLA. Duplicate and wrong effects remain separate measurements. p95 latency covers completed events only and must be read with backlog.

Inventory truth is the latest released revision, not the last arriving message. Final stock alone is insufficient: stale and duplicate writes are measured even if later writes repair the stock. Inventory's primary target condition requires exact stock and zero stale/duplicate writes at all nine checkpoints, plus deployment and a finished session. Each checkpoint is a phase end; the write-error counts cover the phase interval.

All operator costs are predefined simulation estimates, not observations of human labor. Metrics from the two cases are not combined into a total score.

## Session protocol and information access

The agent receives a runbook, development transactions and diagnostics, the action interface, and the relevant policy schema. It can `inspect`, `configure`, `replay`, `request_approval`, `deploy`, and `finish`. A configuration change invalidates prior approval. Only a policy matching its approval hash can deploy. Approval is mechanical and does not model human judgment or persuasion.

Pilot limits are 10 actions and 2 development replays per session, 120 seconds per adapter call, and 131,072 output bytes per call. Up to two independent sessions run concurrently. These are not equal-token or equal-dollar budgets. Usage is reported when supplied by the provider; unavailable usage is null. Shared-host elapsed time includes adapter overhead and is not isolated model latency.

Agent texts were frozen before target implementation. Agents could adapt their configuration within each case but could not revise their instruction source. After every development session ended, the runner wrote `deployments.freeze.json`; only then did final evaluation begin. Evaluation results were not supplied as development feedback, and no best-of retries were selected.

The initial local registration used an unavailable model. Model selection changed to `gpt-5.5` following a case-free availability probe, and concurrency was recorded before case calls. The original and amended files remain in [the experiment directory](experiments/transfer-v1/). These records are not externally timestamped preregistration.

## Baselines and comparisons

For every case/seed, the incumbent and candidate execute the same workload. Baseline hashes are also checked across arms. This supports paired comparisons **inside the authored simulator**. It does not identify real-world counterfactual net contribution: an executable incumbent is not an empirically validated baseline twin.

For support, sum each metric over the three phases and report the mean over evaluation seeds. Retain phase results so a handoff regression cannot hide behind an aggregate. For inventory, report every checkpoint and all raw metrics. The pilot does not calculate p-values from workload seeds or issue a general FDE ability ranking.

To compare models, hold agent/harness/budgets fixed and change only the model identifier. To compare agents, hold model/harness fixed. If multiple components change, label the result a system comparison. Evaluator changes require bridge runs; do not subtract scores from incompatible versions. `compare` checks compatibility for normal `run` outputs; transfer has its own per-case report and is not an input to that command.

## Failure reporting

Exit 0 means execution completed without recorded agent failures. Exit 1 means completed results contain agent failures or rejected actions; exit 2 indicates input or execution infrastructure errors. These are execution statuses, not benchmark-validity labels.

Keep failed sessions, unapproved deployment attempts, schema errors, transport failures, and costs. Distinguish an interaction error from task outcome failure: the pilot retained two `unexpected_policy_payload` rejections that the agents recovered from. Policy/API changes can confound portability with task reasoning.

Those classes are named in `fdebench.execution-ontology.v0.1`: `interaction.*`, `execution.timeout`, `execution.budget_exhaustion`, `execution.deployment_failure`, `task.*`, and `handoff.post_shock_regression`. Do not collapse them into one failure rate. `./bench ontology --artifact path.json` classifies a stored session or episode. Field intake remains `fdebench.field-ontology.PROVISIONAL`.

## Artifacts and reproduction

Each transfer output contains:

- `registration.json`, portable `replay-registration.json`, and `agents/`: configuration and source snapshots.
- Per-condition `session.json`: actions, policy, status, violations, and usage.
- `deployments.freeze.json`: policy/source hashes recorded before final evaluation.
- Per-seed `evaluation.json` and SQLite files: incumbent/candidate measurements and transaction evidence.
- `results.json`, `report.md`, and `manifest.json`: full results, summary, result hash, and evaluator identity.

The evaluator identity hashes the runtime Python source files; it is not a complete machine or dependency attestation. Dependencies are pinned separately in `uv.lock`. Stored database paths describe the original execution location; relocate by their suffix under `runs/transfer-v1` when reading the evidence ZIP. Inventory's three phase records refer to one final database containing phase-tagged history.

To inspect the recorded experiment without model calls:

From the repository root, use:

```bash
(cd experiments/transfer-v1 && shasum -a 256 -c bundle.sha256)
unzip experiments/transfer-v1/evidence_bundle.zip -d /tmp/fde-transfer-evidence
```

Choose a fresh extraction directory. The ZIP includes `FILES.sha256.json`, an artifact audit script, runtime sources, and installation packages. The recorded [artifact audit](experiments/transfer-v1/artifact-audit.json) verifies file/policy identities and persisted ledger consistency; it does not independently validate the task design. Source and deterministic workloads can be reproduced, while new model calls need not produce identical policies, tokens, or timings.

For local implementation checks:

```bash
uv run pytest -q
uv run ruff check .
uv run basedpyright
```

On removable filesystems, set `UV_PROJECT_ENVIRONMENT` to a local filesystem directory, as the `bench` wrapper does. Scripted examples exercise mechanics without model calls. Real model trials require a logged-in Codex CLI and available configured model.

## Improvement and claim limits

`evolve` implements recursive and fixed-optimizer lineages, preserving proposals and freezing all generations before evaluation. Its scripted demonstration verifies execution plumbing; it is not evidence of AI learning. The transfer pilot examines an existing model-generated revision but neither generates multiple improvement generations nor demonstrates recursive improvement benefits.

There are two authored synthetic mechanisms and zero real customer case families. The target is new relative to instruction authorship, not necessarily model pretraining. Public generators, shared development/evaluation transport patterns, a narrow policy space, one session per condition, and same-project authorship limit interpretation. The engine is not a secure sandbox for adversarial agents. Actual FDE substitution needs empirical operational validation and separately designed multi-day human comparisons.
