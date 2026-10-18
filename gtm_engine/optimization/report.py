"""CLI to generate a deterministic outcome-feedback benchmark report."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from gtm_engine.optimization.simulator import (
    DEFAULT_SEED, SIMULATOR_SCHEMA_VERSION, evaluate_scenario,
)


def build_report(seed: int = DEFAULT_SEED, train_size: int = 3000, evaluation_size: int = 2000,
                 scenarios: int = 5) -> dict:
    if scenarios < 1 or scenarios > 100:
        raise ValueError("scenarios must be between 1 and 100")
    positive, negative = [], []
    for i in range(scenarios):
        scenario_seed = seed + i
        positive.append(evaluate_scenario(scenario_seed, train_size, evaluation_size, equal_effect=False))
        # Use separate seeds/cohorts for equal-effect negative control.
        negative.append(evaluate_scenario(seed + 10000 + i, train_size, evaluation_size, equal_effect=True))
    policy_names = ("randomized_baseline_v1", "global_best_v1", "segment_best_v1", "oracle_upper_bound")
    aggregate = {}
    for name in policy_names:
        values = [s["policies"][name] for s in positive]
        aggregate[name] = {
            "mean_reply_rate": round(sum(v["reply_rate"] for v in values) / len(values), 6),
            "mean_meeting_rate": round(sum(v["meeting_rate"] for v in values) / len(values), 6),
            "mean_won_rate": round(sum(v["won_rate"] for v in values) / len(values), 6),
            "mean_bounce_rate": round(sum(v["bounce_rate"] for v in values) / len(values), 6),
            "mean_unsubscribe_rate": round(sum(v["unsubscribe_rate"] for v in values) / len(values), 6),
            "mean_regret_vs_oracle": round(sum(v["regret_vs_oracle"] for v in values) / len(values), 6),
            "mean_difference_vs_baseline": round(sum(v["difference_vs_baseline"] for v in values) / len(values), 6),
            "samples_per_scenario": evaluation_size,
            "deployable": name != "oracle_upper_bound",
        }
    negative_deltas = [s["policies"]["segment_best_v1"]["difference_vs_baseline"] for s in negative]
    negative_summary = {
        "scenario_count": scenarios,
        "mean_segment_policy_difference_vs_baseline": round(sum(negative_deltas) / len(negative_deltas), 6),
        "scenario_differences": [round(v, 6) for v in negative_deltas],
        "caveat": "Finite-sample noise can produce positive or negative differences; this is a regression control, not a hypothesis test about real campaigns.",
    }
    return {
        "schema_version": SIMULATOR_SCHEMA_VERSION,
        "seed": seed,
        "generator_assumptions": "Synthetic only; technical and operations segments have opposing preferred variants in the positive-control scenario.",
        "train_size": train_size, "heldout_evaluation_size": evaluation_size,
        "scenario_count": scenarios,
        "split_method": "Independent seed-derived training and held-out potential-outcome populations. Policy fitting sees training rows only.",
        "metrics": aggregate,
        "positive_control_scenarios": positive,
        "equal_effect_negative_control": negative_summary,
        "negative_control_scenarios": negative,
        "caveats": [
            "All results are synthetic and are not evidence of real-world uplift.",
            "All policies within a scenario are scored on the same held-out lead potential outcomes.",
            "The oracle is a non-deployable theoretical upper bound that uses simulator ground truth only for scoring.",
            "The bootstrap interval is per-scenario paired differences; the multi-scenario report shows variation rather than a production guarantee.",
            "This benchmark is separate from the queue throughput benchmark.",
        ],
    }


def render_markdown(report: dict) -> str:
    lines = [
        "# Outcome-Driven Feedback Benchmark",
        "",
        "> **Synthetic simulator only. These results are not real campaign evidence and must not trigger production promotion.**",
        "",
        f"- Seed: `{report['seed']}`",
        f"- Scenarios: {report['scenario_count']}",
        f"- Training cohort per scenario: {report['train_size']}",
        f"- Held-out cohort per scenario: {report['heldout_evaluation_size']}",
        f"- Split: {report['split_method']}",
        "",
        "## Positive-control results (mean across scenarios)",
        "",
        "| Policy | Reply rate | Meeting rate | Won rate | Regret vs oracle | Difference vs baseline | Held-out gate | Deployable |",
        "|---|---:|---:|---:|---:|---:|---|:---:|",
    ]
    for name, item in report["metrics"].items():
        gate_status = "reference/oracle" if name in ("randomized_baseline_v1", "oracle_upper_bound") else ", ".join(sorted({s["policies"][name]["heldout_evidence_gate"]["status"] for s in report["positive_control_scenarios"]}))
        lines.append(f"| `{name}` | {item['mean_reply_rate']:.3%} | {item['mean_meeting_rate']:.3%} | {item['mean_won_rate']:.3%} | {item['mean_regret_vs_oracle']:.3%} | {item['mean_difference_vs_baseline']:+.3%} | {gate_status} | {'yes' if item['deployable'] else 'no'} |")
    neg = report["equal_effect_negative_control"]
    lines += [
        "", "## Equal-effect negative control", "",
        f"Scenarios: {neg['scenario_count']}; mean segment-aware reply-rate difference vs baseline: **{neg['mean_segment_policy_difference_vs_baseline']:+.3%}**.",
        "", neg["caveat"], "", "## Assumptions and limitations", "",
    ]
    lines.extend(f"- {c}" for c in report["caveats"])
    lines += ["", "## Reproduce", "", f"```powershell\npython -m gtm_engine.optimization.report --seed {report['seed']}\n```", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--train-size", type=int, default=3000)
    parser.add_argument("--evaluation-size", type=int, default=2000)
    parser.add_argument("--scenarios", type=int, default=5)
    parser.add_argument("--output-dir", default="reports")
    args = parser.parse_args(argv)
    if args.train_size < 100 or args.evaluation_size < 100 or args.scenarios < 1:
        parser.error("train-size and evaluation-size must be >= 100; scenarios must be >= 1")
    report = build_report(args.seed, args.train_size, args.evaluation_size, args.scenarios)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "outcome_feedback_benchmark.json"
    md_path = output_dir / "outcome_feedback_benchmark.md"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    print("Mean held-out reply rates (synthetic):")
    for name, item in report["metrics"].items():
        print(f"  {name}: {item['mean_reply_rate']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
