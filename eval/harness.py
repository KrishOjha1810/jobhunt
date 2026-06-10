"""Run the JobHunt ranker over a labeled set and report match-quality metrics.

This reproduces the production ranking path (matcher.rank_matches -> role filter ->
blended_score re-rank) WITHOUT the database, so it's deterministic and fast. The
embedding/trending/collab signals are neutral here (no vectors in fixtures); the harness
isolates the keyword + blended scoring that runs for every user.

Usage:
    python -m eval.harness                  # metrics on eval/labels.json
    python -m eval.harness --labels foo.json
    python -m eval.harness --ablate         # per-signal weight ablation table

Grow eval/labels.json from real digests (see eval/README.md and eval/import_events.py).
"""
import argparse
import copy
import json
import os
import sys

# Run from repo root so `app` imports resolve.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import matcher  # noqa: E402
from eval import metrics as M  # noqa: E402

MIN_SCORE = int(os.environ.get("MIN_SCORE", "3"))  # mirror app.config default


def rank_for_user(user, jobs):
    """Return the ranked list of job dicts for one user, mirroring runner.py.

    Pure with respect to matcher.SCORE_WEIGHTS / SCORE_BIAS, so an ablation can swap
    those module globals and re-call this to see the effect.
    """
    keywords = user.get("keywords") or []
    locations = user.get("locations") or []
    cats = user.get("categories") or []
    uyears = int(user.get("years") or 0)

    ranked = matcher.rank_matches(jobs, keywords, locations, MIN_SCORE, uyears, cats)
    if cats:
        ranked = [j for j in ranked if (j.get("category") or matcher.categorize(j)) in cats]

    # Blended re-score (SCORE_V2 path). Warm-start theta from chosen roles, optionally
    # replay the user's events so learning is exercised when fixtures provide them.
    theta = {("cat:" + c): 0.35 for c in cats}
    for ev in user.get("events") or []:  # chronological list of {event, category}
        reward = _EVENT_REWARD.get(ev.get("event"), 0.0)
        if reward and ev.get("category"):
            theta = matcher.pref_update(theta, {"cat:" + ev["category"]: 1.0}, reward)
    top_cats = sorted([(w, k.split(":", 1)[1]) for k, w in theta.items()
                       if k.startswith("cat:") and w > 0], reverse=True)
    ctx = {
        "theta": theta, "trending": {}, "collab": {}, "source_q": {},
        "user_top_cats": [c for _, c in top_cats[:3]], "uyears": uyears,
        "sem_baseline": None, "user_cats": cats,
        "india_user": any("india" in (l or "").lower() for l in locations),
    }
    for j in ranked:
        j["score"], _ = matcher.blended_score(j, ctx)
    ranked.sort(key=lambda j: j.get("score", 0), reverse=True)
    return ranked


# Mirror db.EVENT_REWARD so the harness can replay learning without importing the DB.
_EVENT_REWARD = {
    "shown": -0.1, "ignored": -0.3, "clicked": 0.4, "saved": 0.6, "applied": 1.0,
    "external_applied": 1.0, "not_interested": -1.0, "rejected": -0.2,
    "good_match": 0.8, "bad_match": -1.0,
}


def evaluate(data, ks=(3, 5, 10)):
    """Run every user, return (per_user_rows, aggregate_dict)."""
    jobs = data["jobs"]
    by_url = {j["url"]: j for j in jobs}
    per_user = []
    for user in data["users"]:
        rel = user.get("relevant") or {}  # {url: grade 0..3}
        ranked = rank_for_user(user, copy.deepcopy(jobs))
        ranked_grades = [int(rel.get(j["url"], 0)) for j in ranked]
        all_grades = [int(g) for g in rel.values()]
        row = M.evaluate_ranking(ranked_grades, all_grades, ks=ks)
        row["user"] = user.get("id", "?")
        per_user.append(row)
    return per_user, M.mean_metrics(per_user)


def _fmt(metrics, ks):
    cols = ["mrr"] + [f"p@{k}" for k in ks] + [f"r@{k}" for k in ks] + [f"ndcg@{k}" for k in ks]
    return cols, [f"{metrics.get(c, 0.0):.3f}" for c in cols]


def print_report(data, ks=(3, 5, 10)):
    per_user, agg = evaluate(data, ks=ks)
    cols, _ = _fmt(agg, ks)
    header = "user".ljust(12) + "".join(c.rjust(9) for c in cols)
    print(header)
    print("-" * len(header))
    for row in per_user:
        _, vals = _fmt(row, ks)
        print(row["user"].ljust(12) + "".join(v.rjust(9) for v in vals))
    print("-" * len(header))
    _, vals = _fmt(agg, ks)
    print("MEAN".ljust(12) + "".join(v.rjust(9) for v in vals))
    return agg


def ablate(data, ks=(3, 5, 10)):
    """Zero out each signal weight in turn; report the NDCG@5 delta vs baseline.

    A signal whose removal barely moves the metric is not earning its weight; a large
    drop means it's load-bearing. This is the cheap way to sanity-check the hand-tuned
    SCORE_WEIGHTS without a full training run.
    """
    baseline_weights = dict(matcher.SCORE_WEIGHTS)
    _, base_agg = evaluate(data, ks=ks)
    base = base_agg.get("ndcg@5", 0.0)
    print(f"\nAblation (baseline ndcg@5 = {base:.3f}); delta = with-signal-removed minus baseline\n")
    print("signal".ljust(12) + "ndcg@5".rjust(9) + "delta".rjust(9))
    print("-" * 30)
    rows = []
    for sig in baseline_weights:
        matcher.SCORE_WEIGHTS = dict(baseline_weights, **{sig: 0.0})
        _, agg = evaluate(data, ks=ks)
        v = agg.get("ndcg@5", 0.0)
        rows.append((sig, v, v - base))
    matcher.SCORE_WEIGHTS = baseline_weights  # restore
    for sig, v, d in sorted(rows, key=lambda r: r[2]):  # most-load-bearing first
        print(sig.ljust(12) + f"{v:.3f}".rjust(9) + f"{d:+.3f}".rjust(9))


def main(argv=None):
    ap = argparse.ArgumentParser(description="JobHunt match-quality eval harness")
    ap.add_argument("--labels", default=os.path.join(os.path.dirname(__file__), "labels.json"))
    ap.add_argument("--ablate", action="store_true", help="run per-signal weight ablation")
    args = ap.parse_args(argv)

    with open(args.labels) as f:
        data = json.load(f)

    print(f"Labels: {args.labels}  ({len(data['users'])} users, {len(data['jobs'])} jobs)\n")
    print_report(data)
    if args.ablate:
        ablate(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
