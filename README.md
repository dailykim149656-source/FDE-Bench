# FDE-Bench

An executable research prototype for evaluating how AI systems diagnose, configure, deploy, and hand off changes in unfamiliar operational workflows.

**Current scope: two authored synthetic mechanisms, zero real customer case families.** The benchmark records transaction outcomes rather than grading persuasive answers. It does not establish AGI, human FDE replacement, or recursive improvement gains.

[한국어 프로젝트 설명](README_KO.md) · [Evaluation methodology](EVALUATION.md) · [Transfer experiment report (한국어)](experiments/transfer-v1/REPORT.md)

## Latest experiment: frozen instructions on a new mechanism

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

The inventory case has a different mechanism and generator, authored after instruction freezing. Its implementation worker did not see the frozen texts, but it is from the same project and is not independent author replication. Development and final workloads share transport patterns. Neither real-world causal attribution nor population generalization follows from this pilot.

Further evidence requires more independently sourced mechanisms, repeated agent sessions, intervention-response validation using actual operational records, and a separate multi-day human comparison. Previously exposed tests cannot become fresh holdouts again.

[한국어 research rationale](README_KO.md) · [Earlier engine evidence](validation/README.md) · [Recovered R0 arithmetic checks](r0_recovery.md) · [Fieldwork protocol](r1_fieldwork/README.md)
