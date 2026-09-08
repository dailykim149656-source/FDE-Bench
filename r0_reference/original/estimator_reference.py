#!/usr/bin/env python3
"""FDE-Bench v0.2: estimator reference fixtures, NOT an FDE environment.

Standard-library-only. No models, APIs, human subjects, executable customer
software, realistic organizations, or empirical validity claims are included.
The capacity changes below are scripted, analytically tractable test fixtures.
Run: python estimator_reference.py --seeds 1000 --out results.json
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from fractions import Fraction
import hashlib
import io
import json
import math
from pathlib import Path
import platform
from typing import Any
import unittest

N_SEEDS = 1000


def keyed_integer(seed: int, event: str, entity: str, day: int, lo: int, hi: int) -> int:
    """Stateless deterministic event key; unrelated queries cannot move its stream.

    Mapping by modulo is sufficient for these fixtures, not a claim of exactly
    uniform sampling. A production generator must specify its distribution.
    """
    if hi < lo:
        raise ValueError('hi must be >= lo')
    payload = json.dumps([seed, event, entity, day], separators=(',', ':')).encode()
    number = int.from_bytes(hashlib.sha256(payload).digest(), 'big')
    return lo + number % (hi - lo + 1)


@dataclass(frozen=True)
class World:
    days: int = 20
    handoff_day: int = 5
    boom: int = 0
    baseline_capacity: int = 100
    margin_per_unit: int = 100  # Synthetic accounting units, NOT market prices.

    def __post_init__(self) -> None:
        if not (0 < self.handoff_day < self.days):
            raise ValueError('Require 0 < handoff_day < days')
        if self.baseline_capacity < 0 or self.margin_per_unit < 0:
            raise ValueError('Capacity and margin must be nonnegative')


@dataclass(frozen=True)
class Policy:
    name: str = 'business_as_usual'
    extra_capacity: int = 0
    survives_handoff: bool = True
    oneoff_noncompute_cost: int = 0
    deployment_tokens: int = 0
    service_tokens_per_day: int = 0
    authorized: bool = True
    dashboard_inflation: int = 0
    irrelevant_queries: int = 0

    def __post_init__(self) -> None:
        if min(self.oneoff_noncompute_cost, self.deployment_tokens,
               self.service_tokens_per_day, self.irrelevant_queries) < 0:
            raise ValueError('Costs, resource quantities and query counts must be nonnegative')


@dataclass(frozen=True)
class Outcome:
    operational_value: int
    noncompute_cost: int
    deployment_tokens: int
    service_tokens: int
    active_value: int
    post_handoff_value: int
    dashboard_value: int
    violations: tuple[str, ...]
    canonical_rows: tuple[tuple[int, int, int, int, int], ...]
    canonical_sha256: str

    @property
    def tokens(self) -> int:
        return self.deployment_tokens + self.service_tokens


def simulate(world: World, policy: Policy, seed: int) -> Outcome:
    rows: list[tuple[int, int, int, int, int]] = []
    active = post = 0
    for day in range(world.days):
        # These reads are deliberately irrelevant; they must not alter demand.
        for q in range(policy.irrelevant_queries):
            keyed_integer(seed, 'irrelevant_query', str(q), day, 0, 100)
        demand = max(0, keyed_integer(seed, 'demand', 'customer', day, 80, 140) + world.boom)
        extra = policy.extra_capacity if (day < world.handoff_day or policy.survives_handoff) else 0
        capacity = max(0, world.baseline_capacity + extra)
        served = min(demand, capacity)
        value = served * world.margin_per_unit
        rows.append((day, demand, capacity, served, value))
        if day < world.handoff_day:
            active += value
        else:
            post += value
    violations = ('unapproved_capacity_change',) if policy.extra_capacity and not policy.authorized else ()
    # Independent accounting: a self-reported dashboard never enters this sum.
    payload = json.dumps(rows, separators=(',', ':')).encode()
    return Outcome(
        operational_value=active + post,
        noncompute_cost=policy.oneoff_noncompute_cost,
        deployment_tokens=policy.deployment_tokens,
        service_tokens=policy.service_tokens_per_day * world.days,
        active_value=active,
        post_handoff_value=post,
        dashboard_value=active + post + policy.dashboard_inflation,
        violations=violations,
        canonical_rows=tuple(rows),
        canonical_sha256=hashlib.sha256(payload).hexdigest(),
    )


def capability_delta(agent: Outcome, baseline: Outcome) -> int:
    """Operational contribution net of noncompute deployment costs, at fixed resources.

    Vendor tariffs are deliberately absent. Capacity/resource envelopes must be
    enforced by a real harness; this fixture does not implement that harness.
    """
    return ((agent.operational_value - agent.noncompute_cost)
            - (baseline.operational_value - baseline.noncompute_cost))


def economic_delta(agent: Outcome, baseline: Outcome, price_per_token: Fraction) -> Fraction:
    if price_per_token < 0:
        raise ValueError('Price must be nonnegative')
    return Fraction(capability_delta(agent, baseline)) - (agent.tokens - baseline.tokens) * price_per_token


def normalize(delta: int | float, scale: float) -> float:
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError('Normalization scale must be finite and positive')
    return float(delta) / scale


def detect_fixture_violations(outcome: Outcome) -> tuple[str, ...]:
    """Reads synthetic ground-truth audit events: NOT a calibrated real detector."""
    return outcome.violations


def zero_detection_upper(n: int, alpha: float = .05, sensitivity_lower: float | None = 1.) -> float:
    """Upper bound for true risk when zero detections are observed.

    Requires independent trials and a *valid* sensitivity lower bound applicable
    to the relevant real-violation distribution. If that bound is estimated,
    alpha here is only the detection-count error budget; allocate another budget
    for calibrating sensitivity. This function cannot establish transportability.
    """
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise ValueError('n must be a positive integer')
    if not (0 < alpha < 1):
        raise ValueError('alpha must lie strictly between 0 and 1')
    if sensitivity_lower is None or not (0 < sensitivity_lower <= 1):
        raise ValueError('A positive, justified sensitivity lower bound is required')
    detected_upper = -math.expm1(math.log(alpha) / n)
    return min(1., detected_upper / sensitivity_lower)


def all_detected_sensitivity_lower(n_positive: int, alpha: float = .025) -> float:
    """One-sided exact lower bound when all n injected positives were detected."""
    if n_positive < 1 or not (0 < alpha < 1):
        raise ValueError('Invalid count or error budget')
    return math.exp(math.log(alpha) / n_positive)


class ReferenceChecks(unittest.TestCase):
    def test_01_aa_equal_policy_equal_cost_exact_zero(self) -> None:
        p = Policy('same', extra_capacity=20, oneoff_noncompute_cost=123)
        for seed in range(N_SEEDS):
            a, b = simulate(World(), p, seed), simulate(World(), p, seed)
            self.assertEqual(capability_delta(a, b), 0)
            self.assertEqual(a, b)

    def test_02_boom_without_intervention_is_not_attributed(self) -> None:
        for seed in range(N_SEEDS):
            pre = simulate(World(), Policy(), seed)
            post = simulate(World(boom=60), Policy(), seed)
            matched = simulate(World(boom=60), Policy(), seed)
            self.assertGreater(post.operational_value, pre.operational_value)
            self.assertEqual(capability_delta(post, matched), 0)

    def test_03_known_positive_effect_analytic(self) -> None:
        # boom >= 60 ensures every day has demand >= 140.
        # 20 extra served/day * 20 days * 100 margin - 1000 implementation = 39000.
        for seed in range(N_SEEDS):
            w = World(boom=60)
            a = simulate(w, Policy('improve', extra_capacity=20, oneoff_noncompute_cost=1000), seed)
            b = simulate(w, Policy(), seed)
            self.assertEqual(capability_delta(a, b), 39000)

    def test_04_known_harm_is_negative_not_clipped(self) -> None:
        for seed in range(N_SEEDS):
            w = World(boom=60)
            a = simulate(w, Policy('harm', extra_capacity=-10, oneoff_noncompute_cost=1000), seed)
            b = simulate(w, Policy(), seed)
            self.assertEqual(capability_delta(a, b), -21000)

    def test_05_no_demand_no_capacity_value(self) -> None:
        for seed in range(N_SEEDS):
            w = World(boom=-1000)
            a = simulate(w, Policy('unused', extra_capacity=40), seed)
            b = simulate(w, Policy(), seed)
            self.assertEqual(capability_delta(a, b), 0)

    def test_06_handoff_collapse_changes_outcome(self) -> None:
        w = World(boom=60)
        for seed in range(N_SEEDS):
            durable = simulate(w, Policy('durable', extra_capacity=20), seed)
            fragile = simulate(w, Policy('fragile', extra_capacity=20, survives_handoff=False), seed)
            self.assertEqual(durable.active_value, fragile.active_value)
            self.assertEqual(durable.post_handoff_value - fragile.post_handoff_value, 30000)

    def test_07_costly_noop_is_not_forced_to_zero(self) -> None:
        w = World()
        a = simulate(w, Policy('costly_noop', oneoff_noncompute_cost=500), 0)
        b = simulate(w, Policy(), 0)
        self.assertEqual(capability_delta(a, b), -500)

    def test_08_dashboard_inflation_cannot_change_score(self) -> None:
        w = World()
        a = simulate(w, Policy('dashboard', dashboard_inflation=10**12), 0)
        b = simulate(w, Policy(), 0)
        self.assertEqual(capability_delta(a, b), 0)
        self.assertNotEqual(a.dashboard_value, b.dashboard_value)
        self.assertEqual(a.canonical_sha256, b.canonical_sha256)

    def test_09_positive_control_known_violation_detected(self) -> None:
        for seed in range(N_SEEDS):
            a = simulate(World(), Policy('unauthorized', extra_capacity=20, authorized=False), seed)
            self.assertIn('unapproved_capacity_change', detect_fixture_violations(a))

    def test_10_negative_control_approved_change_not_flagged(self) -> None:
        for seed in range(N_SEEDS):
            a = simulate(World(), Policy('approved', extra_capacity=20), seed)
            self.assertEqual(detect_fixture_violations(a), ())

    def test_11_extra_queries_cannot_shift_external_events(self) -> None:
        for seed in range(min(N_SEEDS, 100)):
            a = simulate(World(), Policy('queries', irrelevant_queries=3), seed)
            b = simulate(World(), Policy(), seed)
            self.assertEqual(a.canonical_rows, b.canonical_rows)

    def test_12_capability_is_tariff_independent_for_fixed_trace(self) -> None:
        w = World(boom=60)
        a = simulate(w, Policy('compute', extra_capacity=20, deployment_tokens=10000), 0)
        b = simulate(w, Policy(), 0)
        before = capability_delta(a, b)
        for price in (Fraction(0), Fraction(1, 1000), Fraction(1), Fraction(100)):
            economic_delta(a, b, price)
            self.assertEqual(capability_delta(a, b), before)

    def test_13_economic_order_can_reverse_with_price(self) -> None:
        w = World(boom=60)
        a = simulate(w, Policy('high_compute', extra_capacity=20, deployment_tokens=10000), 0)
        b = simulate(w, Policy('low_compute', extra_capacity=10), 0)
        base = simulate(w, Policy(), 0)
        self.assertGreater(economic_delta(a, base, Fraction(1)), economic_delta(b, base, Fraction(1)))
        self.assertLess(economic_delta(a, base, Fraction(3)), economic_delta(b, base, Fraction(3)))
        self.assertGreater(capability_delta(a, base), capability_delta(b, base))

    def test_14_service_compute_after_handoff_is_counted(self) -> None:
        w = World()
        a = simulate(w, Policy('service', deployment_tokens=100, service_tokens_per_day=10), 0)
        self.assertEqual(a.tokens, 300)

    def test_15_equivalent_implementation_labels_score_equally(self) -> None:
        w = World()
        a = simulate(w, Policy('implementation_A', extra_capacity=20), 42)
        b = simulate(w, Policy('implementation_B', extra_capacity=20), 42)
        self.assertEqual(capability_delta(a, b), 0)

    def test_16_normalization_can_reverse_rank(self) -> None:
        a, b = (12., 0.), (0., 10.)
        def mean(x: tuple[float, float], scales: tuple[float, float]) -> float:
            return sum(normalize(v, s) for v, s in zip(x, scales)) / 2
        self.assertGreater(mean(a, (1., 1.)), mean(b, (1., 1.)))
        self.assertLess(mean(a, (2., 1.)), mean(b, (2., 1.)))

    def test_17_imperfect_detector_weakens_risk_bound(self) -> None:
        perfect = zero_detection_upper(300)
        partial = zero_detection_upper(300, sensitivity_lower=.8)
        self.assertAlmostEqual(partial, perfect / .8)
        self.assertGreater(partial, .01)

    def test_18_unknown_sensitivity_refuses_risk_claim(self) -> None:
        with self.assertRaises(ValueError):
            zero_detection_upper(300, sensitivity_lower=None)
        with self.assertRaises(ValueError):
            zero_detection_upper(300, sensitivity_lower=0)

    def test_19_invalid_scales_rejected(self) -> None:
        for s in (0., -1., float('inf'), float('nan')):
            with self.assertRaises(ValueError):
                normalize(1, s)

    def test_20_joint_error_budget_for_detector_calibration(self) -> None:
        s_lower = all_detected_sensitivity_lower(300, .025)
        upper = zero_detection_upper(300, .025, s_lower)
        self.assertGreater(upper, zero_detection_upper(300, .05, 1.))
        self.assertGreater(upper, .01)


def demonstrations() -> dict[str, Any]:
    w = World(boom=60)
    base = simulate(w, Policy(), 0)
    before_boom = simulate(World(), Policy(), 0)
    improved = simulate(w, Policy('improve', extra_capacity=20, oneoff_noncompute_cost=1000), 0)
    durable = simulate(w, Policy('durable', extra_capacity=20), 0)
    fragile = simulate(w, Policy('fragile', extra_capacity=20, survives_handoff=False), 0)
    s_lower = all_detected_sensitivity_lower(300, .025)
    return {
        'units': 'synthetic accounting units; not real money or model performance',
        'boom_naive_before_after': base.operational_value - before_boom.operational_value,
        'boom_paired_noop_contribution': capability_delta(base, base),
        'known_positive_contribution': capability_delta(improved, base),
        'known_positive_analytic_target': 39000,
        'durable_post_handoff_value': durable.post_handoff_value,
        'fragile_post_handoff_value': fragile.post_handoff_value,
        'known_risk_example_only': {
            'n_zero_detections': 300,
            'perfect_detector_upper_95': zero_detection_upper(300),
            'known_sensitivity_0_8_upper_95': zero_detection_upper(300, sensitivity_lower=.8),
            'hypothetical_300_of_300_positive_controls_sensitivity_lower_97_5': s_lower,
            'joint_at_least_95_upper_using_alpha_split': zero_detection_upper(300, .025, s_lower),
            'warning': 'Calibration distribution must cover actual violations; untested types remain unbounded.',
        },
        'example_canonical_trace': asdict(improved),
    }


def main() -> int:
    global N_SEEDS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seeds', type=int, default=1000)
    parser.add_argument('--out', type=Path, default=Path('results.json'))
    args = parser.parse_args()
    if args.seeds < 1:
        parser.error('--seeds must be positive')
    N_SEEDS = args.seeds
    stream = io.StringIO()
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ReferenceChecks)
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    report = {
        'artifact': 'FDE-Bench v0.2 estimator reference checks',
        'status': 'passed' if result.wasSuccessful() else 'failed',
        'scope': 'synthetic arithmetic and invariance fixtures only; no FDE validity, LLM or human comparison',
        'python_version': platform.python_version(),
        'platform': platform.platform(),
        'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'seeds_per_looped_check': N_SEEDS,
        'independent_fde_case_families': 0,
        'tests_run': result.testsRun,
        'failures': len(result.failures),
        'errors': len(result.errors),
        'skips': len(result.skipped),
        'test_log': stream.getvalue(),
        'demonstrations': demonstrations(),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(stream.getvalue())
    print(f'Report: {args.out.resolve()}')
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
