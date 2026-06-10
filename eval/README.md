# Match-quality eval harness

The matcher has 10 hand-tuned signal weights (`SCORE_WEIGHTS` in `app/matcher.py`) and,
until now, no way to tell whether a change helps or hurts. This harness is the instrument:
it runs the real ranking path over a labeled set and reports standard IR metrics, plus a
per-signal ablation.

## Run it

```bash
# from the repo root, with the venv
.venv/bin/python -m eval.harness            # metrics on eval/labels.json
.venv/bin/python -m eval.harness --ablate   # + per-signal weight ablation
.venv/bin/python -m eval.harness --labels path/to/other.json
```

Output is per-user and mean: MRR, precision@k, recall@k, NDCG@k (k = 3, 5, 10).

## What it measures

- **precision@k** , of the top k delivered, how many are relevant (grade >= 2).
- **recall@k** , of all the user's relevant jobs, how many made the top k.
- **NDCG@k** , graded ranking quality vs. the ideal ordering (rewards putting the best
  jobs highest, not just somewhere in the list).
- **MRR** , how high the first relevant job lands.
- **ablation** , zero each signal weight in turn and report the NDCG@5 delta. A signal
  whose removal barely moves the metric isn't earning its weight; a big drop means it's
  load-bearing. Cheap sanity check on the hand-tuned weights.

## What it does NOT do

It runs the keyword + blended-score path without the database. Embedding / trending /
collaborative signals are neutral here (no vectors in fixtures), so the harness isolates
the part that runs for every user on every digest. To evaluate semantic re-ranking,
extend the fixtures with a `_sem` field per job and a `sem_baseline` in the ctx.

## The label format (`labels.json`)

```jsonc
{
  "jobs": [
    {"url": "...", "title": "...", "company": "...", "location": "...",
     "source": "greenhouse", "posted_at": null, "description": "..."}
  ],
  "users": [
    {
      "id": "backend-mid",
      "keywords": ["python", "django", ...],
      "locations": ["india"],
      "years": 4,
      "categories": ["Backend"],
      "events": [{"event": "applied", "category": "Backend"}],   // optional: exercises learning
      "relevant": {"https://j/1": 3, "https://j/2": 2, "https://j/9": 0}  // grade 0..3
    }
  ]
}
```

Grades: **3** = perfect (apply today), **2** = relevant, **1** = weak/edge, **0** =
irrelevant. Only grade >= 2 counts as a "hit" for precision/recall (tune
`REL_THRESHOLD` in `metrics.py`).

## Growing the label set (the important part)

The seed `labels.json` is a scaffold of three synthetic personas. It's too clean to be
informative , real digests have near-misses and noise that the seed lacks. The cheapest
source of truth is your own delivered digests plus the tracker's apply/dismiss signals:

```bash
.venv/bin/python -m eval.import_events --out eval/labels.json
```

This pulls recent `events` rows and maps user actions to grades
(applied/saved = 3, clicked = 2, shown-only = 1, not_interested = 0). Hand-correct the
result , implicit signals are noisy (a shown-but-ignored job isn't necessarily bad).
Aim for 100+ judged (user, job) pairs before trusting the ablation.

## Workflow for tuning a weight

1. `--ablate` to see which signals are load-bearing on your real labels.
2. Change a weight (or add a signal) in `app/matcher.py`.
3. Re-run the harness; keep the change only if NDCG@5 / precision@3 improve.
4. Commit the labels alongside the code so results are reproducible.
