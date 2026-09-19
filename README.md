# FDE-Bench

An executable research prototype for evaluating how AI systems diagnose, configure, deploy, and hand off changes in unfamiliar operational workflows.

**Current scope: three authored synthetic mechanisms, zero real customer case families.** The benchmark records transaction outcomes rather than grading persuasive answers. It does not establish AGI, human FDE replacement, or recursive improvement gains.

The executable world is named `fdebench.execution-ontology.v0.1`. Field intake stays `fdebench.field-ontology.PROVISIONAL` and is not locked before real interviews. Timeout, protocol rejection, and task-outcome errors are different classes, not one failure rate. `./bench ontology` dumps the catalog; `./bench ontology --artifact path.json` classifies a stored session or episode.

[한국어 프로젝트 설명](README_KO.md) · [Evaluation methodology](EVALUATION.md) · [Transfer experiment report (한국어)](experiments/transfer-v1/REPORT.md)

## Latest results: 45 repeated Grok 4.6 sessions

Using `xai/grok-4.6` through OpenCodex, we ran three frozen instruction sets on three mechanisms, with five fresh agent sessions per condition. All policies froze before 225 final workload evaluations.

| Case | Ancestor | Source-derived revision | Neutral |
|---|---:|---:|---:|
| Support routing | 2/5 | 0/5 | 0/5 |
| Inventory reconciliation | 3/5 | 4/5 | 4/5 |
| External-effect recovery | 2/5 | 2/5 | 2/5 |

Cells count sessions that deployed, finished, and met their case-specific quality condition. **19/45 did so; all 26 remaining sessions ended in the 120-second invocation timeout.** All 19 deployed policies met quality requirements across their five final seeds. There were also 25 tool-protocol rejections, including recovered errors. The task outcomes did not discriminate among deployed policies; the observed differences are dominated by execution reliability and cannot establish a general ability ranking.

[Results and failure analysis (한국어)](experiments/grok45-v1/REPORT.md) · [Design and commands](experiments/grok45-v1/README.md) · [Machine-readable summary](experiments/grok45-v1/analysis.json) · [Evidence ZIP](experiments/grok45-v1/evidence_bundle.zip)

Verification: 97 tests, lint/type checks, CLI execution/resume, package build, and 1,350 phase ledger hashes checked across 1,050 referenced evaluation databases. There are still zero independently sourced real customer case families. This is not a controlled comparison with the earlier model pilot below.

<a id="latest-experiment-frozen-instructions-on-a-new-mechanism"></a>

## Earlier experiment: frozen instructions on a new mechanism

We froze three instruction texts before implementing an inventory reconciliation case, then ran each with `gpt-5.5` on both support routing and inventory. The texts were an ancestor, a revision previously generated from support development feedback, and a neutral control. The revised text was **not previously established as an improvement**.

All six development sessions and deployment policies were frozen before final workload evaluation. Each condition had **one agent session**, followed by three workload seeds. Seeds are workload replicates, not independent projects or repeated agent samples.

| Frozen instructions | Inventory quality checkpoints met | Support correctly processed | Support correctly processed within SLA | Support backlog |
|---|---:|---:|---:|---:|
| Ancestor | 9/9 | 280 | 180 | 0 |
| Neutral | 9/9 | 280 | 180 | 0 |
| Source-derived revision | 9/9 | 237.33 | 156.33 | 42.67 |

Inventory requires exact stock at each checkpoint and zero stale or duplicate writes during each phase. The incumbent inventory policy met **0/9** checkpoints. Support numbers are phase totals averaged over three evaluation seeds; backlog counts unprocessed events, while correct processing counts unique tickets.

**Observed result:** all three texts adapted successfully to the new synthetic mechanism. The target saturated and did not distinguish these conditions. The revised text's support execution had lower throughput; this single draw does not establish its expected performance. No recursive improvement advantage was demonstrated.

All six sessions deployed and finished. Two rejected actions were retained, each subsequently recovered; the run therefore returned `completed_with_agent_failures` (CLI exit 1). Success at the target quality condition does not erase interaction failures.

**Evidence:** [experiment design](experiments/transfer-v1/README.md), [execution registration](experiments/transfer-v1/registration.execution.json), [results and interpretation](experiments/transfer-v1/REPORT.md), [artifact audit](experiments/transfer-v1/artifact-audit.json), [complete evidence ZIP](experiments/transfer-v1/evidence_bundle.zip), [ZIP checksum](experiments/transfer-v1/bundle.sha256). The ZIP contains results, sessions, SQLite ledgers, frozen policies, sources, and installable packages. Automated verification recorded 56 tests, lint/type checks, and an audit of 72 evaluation databases and 108 phase ledger hashes. These are implementation checks, not scientific validation.

## Quick start

Requirements: macOS/Linux, Python 3.12+, and `uv`. Run commands from the repository root. Each output directory must be new.

```bash
git clone https://github.com/dailykim149656-source/FDE-Bench.git
cd FDE-Bench
./bench --help

# Deterministic scripted controls; no model account required.
./bench run --suite examples/compare.json --out runs/controls

# Exercise the generation and fixed-optimizer control paths with scripted agents.
./bench evolve --suite examples/evolve.json --generations 2 --out runs/evolution
```

To rerun the transfer protocol, install and authenticate the Codex CLI with access to the configured model. This invokes real models and consumes account usage. Availability and outputs may change; the model name is not an authenticated immutable provider snapshot.

```bash
./bench transfer \
  --registration experiments/transfer-v1/registration.execution.json \
  --out runs/transfer-rerun
```

The public source and workload generators are available to readers. They are withheld from normal agent observations by the protocol, **not protected by a security boundary**. The local adapters are for cooperative agents, not untrusted submissions.

## How evaluation works

1. Present an incumbent system, runbook, development observations, and a constrained configuration interface.
2. Allow inspection, configuration, and budgeted development replay. Approval is bound to the current policy hash; deployment requires that approval.
3. Freeze all policies before final evaluation. Run incumbent and candidate on identical workload streams.
4. Measure actual SQLite transaction effects, phase quality, backlog, synthetic operator cost, violations, and provider-reported usage. Preserve raw units; do not combine them into an arbitrary score.
5. Report each case and condition separately, including failures and the number of agent sessions, workloads, and independently sourced cases.

[Evaluation methodology](EVALUATION.md) defines metrics, budgets, handoff behavior, comparison rules, and reproduction limits. [Agent interface and examples (한국어)](examples/README.md) describes the JSON protocol and improvement loop.

## Research scope and next evidence

The intended questions are whether model/agent/harness changes improve FDE task performance, whether improvement transfers, and eventually whether systems can substitute for human FDE work. Current changes are constrained policies, not arbitrary customer codebase engineering or organizational negotiation.

The inventory and recovery cases have different mechanisms and generators, authored after instruction freezing. Its implementation worker did not see the frozen texts, but it is from the same project and is not independent author replication. Development and final workloads share transport patterns. Neither real-world causal attribution nor population generalization follows from this pilot.

Further evidence requires more independently sourced mechanisms, repeated agent sessions, intervention-response validation using actual operational records, and a separate multi-day human comparison. Previously exposed tests cannot become fresh holdouts again.

[한국어 research rationale](README_KO.md) · [Earlier engine evidence](validation/README.md) · [Recovered R0 arithmetic checks](r0_recovery.md) · [Fieldwork protocol](r1_fieldwork/README.md)
