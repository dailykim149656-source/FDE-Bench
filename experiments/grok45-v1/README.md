# Grok 4.6 repeated-session experiment

This experiment fixes the model route to `xai/grok-4.6` through a locally authenticated OpenCodex proxy. It compares three existing instruction texts across support routing, inventory reconciliation, and a newly authored external-effect recovery mechanism.

[Completed results](REPORT.md): 19/45 sessions deployed, finished, and met case conditions; 26 sessions timed out. All deployed policies met their quality condition. [Evidence audit](artifact-audit.json).

## Registered design

- 3 instruction conditions × 3 mechanisms × 5 fresh agent sessions = **45 sessions**.
- Each frozen deployment receives the same five final workload seeds (9101–9105). These are nested workload replicates, not 225 independent agents or customer cases.
- At most 10 actions and 2 development replays per session; 120 seconds and 131,072 captured output bytes per adapter call. Up to two sessions run concurrently, in a deterministic shuffled submission order.
- All 45 sessions finish and their policies freeze before any final evaluation. Finished failures are retained. There is no selection of best retries and no final-feedback prompt tuning.
- The three instruction texts are byte-identical to the prior transfer pilot. The recovery implementation worker did not read them or the prior task generators. Same-project authorship remains; independent real customer case families = 0.

[registration.json](registration.json) records the sources, order, budgets and analysis before case trials. This is a local record, not externally authenticated preregistration. The selected model's catalog entry is snapshotted. Neither the model slug nor the local proxy establishes an immutable provider model snapshot.

[Execution amendment](registration.execution.json) lowers concurrency from three to two before case trials. Three concurrent case-free probes timed out and proxy health became unavailable; after `opencodex ensure`, two probes succeeded. This does not isolate concurrency as the cause. Original records and all probe outcomes are retained.

## New mechanism

An external job may take effect even when its response is lost. Endpoint behavior differs in retry identity and status visibility. A client must recover without duplicate effects or lost jobs, including after loss of process-local state. Development diagnostic traces expose the operational semantics; final schedules combine timeout, delayed visibility and restart. The workload seed varies concrete instances, not independently sourced mechanisms.

The policy interface is constrained recovery configuration, not arbitrary code synthesis. A single robust policy may work across endpoint variants; success alone therefore does not prove that an agent inferred a distinct policy for each variant. Query counts and synthetic recovery delay remain separate outcome dimensions. Restart is simulated process-state loss, not an operating-system crash test.

## Analysis

Report raw measurements for each session averaged over its five workload seeds, then mean, range and sample standard deviation across the five agent sessions in each condition. No cross-case score or p-values from workload seeds are reported.

Case quality conditions:

- Support: full correct coverage of 280 unique tickets, zero wrong routes, duplicate effects and backlog across all three phases. SLA throughput and synthetic manual cost remain separate metrics.
- Inventory: exact stock and zero stale/duplicate writes at every phase checkpoint.
- Recovery: all jobs completed with no duplicate effects or unresolved jobs at every phase checkpoint.

A completed deployment and finished session are additionally required for a session to meet its case condition. Recovered interface rejections remain reported separately. These are distinct case-specific conditions, not a common ability scale.

Failure reporting uses observable symptoms: transport errors, protocol rejections, missing deployment, incomplete coverage, duplicate or stale effects, and phase-specific degradation. Cognitive causes such as 'misunderstanding' are not assigned from a model's explanation alone. All-success and all-failure outcomes are valid; no task tuning will be performed to force discrimination.

## Execution

```bash
./bench repeat --registration experiments/grok45-v1/registration.execution.json --out runs/grok45-v1
```

This invokes real models and consumes provider usage. Local proxy authentication remains outside the repository. The registration's catalog path is machine-specific; for an independent rerun, copy the registration to a new experiment and update that path to the supplied model catalog, recording the change.

```bash
./bench repeat --registration experiments/grok45-v1/registration.execution.json --out runs/grok45-v1 --resume
```

Resume verifies runtime, registration, source and checkpoint identities. It reuses completed sessions, including failed ones. An interrupted session with an ambiguous outcome is not silently rerun. A changed evaluator requires a new experiment version. The public local runner is cooperative execution, not an adversarial-agent sandbox.

## Prior evidence

The [earlier transfer pilot](../transfer-v1/REPORT.md) used a different model route and evaluator version and had one session per condition. Do not interpret differences from that experiment as a controlled Grok-versus-GPT comparison. This experiment tests repeated instruction-condition performance and failure modes, not recursive self-improvement or AGI.
