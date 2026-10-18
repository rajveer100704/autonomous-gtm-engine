# Outcome-Driven Feedback Benchmark

> **Synthetic simulator only. These results are not real campaign evidence and must not trigger production promotion.**

- Seed: `20261019`
- Scenarios: 5
- Training cohort per scenario: 3000
- Held-out cohort per scenario: 2000
- Split: Independent seed-derived training and held-out potential-outcome populations. Policy fitting sees training rows only.

## Positive-control results (mean across scenarios)

| Policy | Reply rate | Meeting rate | Won rate | Regret vs oracle | Difference vs baseline | Held-out gate | Deployable |
|---|---:|---:|---:|---:|---:|---|:---:|
| `randomized_baseline_v1` | 22.440% | 8.490% | 1.850% | 8.350% | +0.000% | reference/oracle | yes |
| `global_best_v1` | 23.190% | 8.520% | 1.870% | 7.600% | +0.750% | no_clear_measured_lift | yes |
| `segment_best_v1` | 27.490% | 10.320% | 2.410% | 3.300% | +5.050% | passes_synthetic_heldout_gate | yes |
| `oracle_upper_bound` | 30.790% | 11.780% | 2.850% | 0.000% | +8.350% | reference/oracle | no |

## Equal-effect negative control

Scenarios: 5; mean segment-aware reply-rate difference vs baseline: **+0.570%**.

Finite-sample noise can produce positive or negative differences; this is a regression control, not a hypothesis test about real campaigns.

## Assumptions and limitations

- All results are synthetic and are not evidence of real-world uplift.
- All policies within a scenario are scored on the same held-out lead potential outcomes.
- The oracle is a non-deployable theoretical upper bound that uses simulator ground truth only for scoring.
- The bootstrap interval is per-scenario paired differences; the multi-scenario report shows variation rather than a production guarantee.
- This benchmark is separate from the queue throughput benchmark.

## Reproduce

```powershell
python -m gtm_engine.optimization.report --seed 20261019
```
