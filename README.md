# remarxivable

A weekday ranking of the 30 most notable new combinatorics preprints on arXiv (math.CO),
scored by estimated journal level with a full-text plausibility screen. Updated every weekday morning.

Live page: https://rsteiner-new.github.io/remarxivable/

- `index.html` — the page (static; reads `data/index.json` and `data/<date>.json`)
- `data/` — one JSON file per listing day plus the index of days
- `tools/pipeline.py` — the data pipeline used by the daily job; `tools/PROMPT.md` — the daily job's instructions

Scores and screens are produced automatically by a language model. They are a reading aid, not peer review.
