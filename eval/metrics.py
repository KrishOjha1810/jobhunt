"""Ranking metrics. Pure functions, no IO.

Relevance is graded 0..3 (0 = irrelevant, 3 = perfect). A "hit" for the
precision/recall family is any grade >= REL_THRESHOLD.
"""
import math

REL_THRESHOLD = 2  # grade >= this counts as a relevant result


def precision_at_k(ranked_grades, k):
    """Fraction of the top-k that are relevant (grade >= threshold)."""
    if k <= 0:
        return 0.0
    top = ranked_grades[:k]
    if not top:
        return 0.0
    hits = sum(1 for g in top if g >= REL_THRESHOLD)
    return hits / float(k)


def recall_at_k(ranked_grades, k, total_relevant):
    """Fraction of all relevant items that appear in the top-k."""
    if total_relevant <= 0:
        return 0.0
    hits = sum(1 for g in ranked_grades[:k] if g >= REL_THRESHOLD)
    return hits / float(total_relevant)


def dcg(grades):
    """Discounted cumulative gain with the standard 2^rel - 1 gain."""
    return sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(grades))


def ndcg_at_k(ranked_grades, k, all_grades):
    """NDCG@k: DCG of the ranking's top-k over DCG of the ideal ordering.

    all_grades is every relevance grade available for this user (used to build
    the ideal ranking), ranked_grades is the grades in the order we produced.
    """
    actual = dcg(ranked_grades[:k])
    ideal = dcg(sorted(all_grades, reverse=True)[:k])
    return (actual / ideal) if ideal > 0 else 0.0


def mrr(ranked_grades):
    """Reciprocal rank of the first relevant result (0 if none)."""
    for i, g in enumerate(ranked_grades):
        if g >= REL_THRESHOLD:
            return 1.0 / (i + 1)
    return 0.0


def evaluate_ranking(ranked_grades, all_grades, ks=(3, 5, 10)):
    """Return a metrics dict for one user's ranking.

    ranked_grades: grades of the jobs THIS user was shown, in ranked order.
    all_grades:    grades of every labeled job for this user (for recall/ideal-DCG).
    """
    total_relevant = sum(1 for g in all_grades if g >= REL_THRESHOLD)
    out = {"mrr": mrr(ranked_grades), "n_relevant": total_relevant}
    for k in ks:
        out[f"p@{k}"] = precision_at_k(ranked_grades, k)
        out[f"r@{k}"] = recall_at_k(ranked_grades, k, total_relevant)
        out[f"ndcg@{k}"] = ndcg_at_k(ranked_grades, k, all_grades)
    return out


def mean_metrics(per_user):
    """Average each metric across users (skipping users with no relevant labels)."""
    rows = [m for m in per_user if m.get("n_relevant", 0) > 0]
    if not rows:
        return {}
    skip = {"n_relevant", "user"}
    keys = [k for k, v in rows[0].items() if k not in skip and isinstance(v, (int, float))]
    return {k: sum(r[k] for r in rows) / len(rows) for k in keys}
