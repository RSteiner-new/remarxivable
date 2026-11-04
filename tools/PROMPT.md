# remarxivable — daily build instructions

These steps build one weekday's shortlist for **remarxivable**: a page showing, for each
weekday, the 30 most notable new arXiv combinatorics (math.CO) preprints plus the day's
survey/expository papers, for research mathematicians working mainly in structural,
extremal and probabilistic graph theory.

Substitutions made by the caller: **P** = `tools/pipeline.py`, **Q** = `tools/parse_new_listing.py`,
**I** = the current `data/index.json`, **H** = the saved `index.html`, **DATE** = the listing
date (UTC, `YYYY-MM-DD`), and the run folder (`./run` unless the caller says otherwise).

`ARTIFACT_URL = https://claude.ai/code/artifact/9cf48666-4ac6-428b-bd2c-f31537ea77b9`

Work unattended: never ask questions, make sensible choices, finish the job.

---

## DO NOT USE THE CATCHUP ENDPOINT

`https://arxiv.org/catchup/math.CO/DATE` serves a **partially-populated** listing that fills
in over hours. On 2026-11-03 it reported **5** papers for a day that really had **155**; on
2026-11-04 it showed 32 entries at 02:58 UTC and 41 at 03:30. `pipeline.py fetch` exits 0 on
such a page, so no retry logic catches it, and a short day published as `"final"` looks
complete and is worse than a missing one.

The authoritative source is the live listing page **`https://arxiv.org/list/math.CO/new`**:
its `<h3>` heading names the single day it covers ("Showing new listings for \<weekday>,
\<d> \<Month> \<year>"), its section headers give exact counts, and its entries carry full
abstracts, so no DataCite pass is ever needed.

**A normal math.CO weekday is 40–130 papers.** Never build a day from a listing whose heading
names a different date, and never publish a day whose screened count is implausibly low.

If `Q` (`tools/parse_new_listing.py`) is not on disk, download it first:
Artifact action `read` with `url` = ARTIFACT_URL, `path` = `"tools/parse_new_listing.py"`,
`out_dir` = the run folder — then use the path it reports as Q.

---

## STEP 1 — FETCH THE DAY'S LISTING WITH ABSTRACTS

1. `curl -sS --max-time 60 "https://arxiv.org/list/math.CO/new" -o run/live_new.html && grep -o "<h3>[^<]*</h3>" run/live_new.html | head -6`
2. Read the heading.
   - If it names DATE → go to 3.
   - If it names an **earlier** day, arXiv has not finished announcing DATE. `sleep 540`
     (Bash `timeout` 580000; never sleep 600 or more in one call) and repeat 1, up to **5
     attempts**. If the heading still names an earlier day after 5 attempts, check
     <https://status.arxiv.org/> for a reported outage, report "no listing for DATE" with the
     reason if one is given, and **stop** — do not fall back to catchup.
3. `python3 -I Q run/live_new.html DATE run/papers.json`
   It prints the heading, the header counts (N new, M cross-lists) and the parsed counts.
   The two must agree and no paper may lack an abstract. If the parsed counts fall short, or
   many abstracts are missing, the page is still being written: `sleep 540` and repeat from 1.

**Fallback — only if `curl` cannot reach arxiv.org at all** (a network error, not a short page).
Load WebFetch with ToolSearch (`select:WebFetch`) and fetch `https://arxiv.org/list/math.CO/new`
asking for: the verbatim `<h3>` heading and section-count lines, then a JSON array with one
object per entry under *New submissions* and *Cross submissions* (skip *Replacement
submissions*), in page order, with keys `id`, `section` (`new`/`cross`), `title` (verbatim,
keep `$...$`), `authors`, `comments`, `subjects`, `abstract` (verbatim and complete); nothing
omitted or summarized. Only if the heading names DATE, write the array to `run/listing.json`
and run `python3 P listing --in run/listing.json --date DATE --out run/papers.json
--expect-new N --expect-cross M`, then `python3 P datacite --papers run/papers.json` for any
paper still lacking an abstract.

---

## STEP 2 — SCORING AND SURVEY FLAGS (judgement)

`python3 P compact --papers run/papers.json --out run/compact.txt` → a plain-text view, one
block per paper (`[i] id (type; categories) title` / `C:` comments / `A:` abstract).

Read `run/compact.txt` **completely**, with `sed -n '<a>,<b>p'` in pieces of about **70 lines**
— every abstract must be seen before scoring; a much larger piece is truncated and silently
loses papers.

Then write `run/scores.json` in one Write call:

```
{"<id>": {"score": <int 1-100>, "venue": "<=6 words", "reason": "<=15 words",
          "survey": <true|false>, "interest": <int 1-100, only when survey is true>}, ...}
```

Every id in `papers.json` must appear. Scores must be **distinct at least through the top 45**,
so no tie decides the 30-paper cut-off; below that ties are harmless.

`"survey": true` means the paper's main purpose is to present known results rather than prove
new ones — a survey, expository or review article, lecture notes, a monograph or book, a
problem collection, a historical account (signals: "survey", "expository", "we review",
"overview", "lecture notes", "this book", a very long page count with no stated new theorem).
A research paper with a long introduction is **not** a survey. `interest` rates how topical the
surveyed subject is for this audience (structural/extremal/probabilistic graph theory highest;
other combinatorics middle; subjects far from combinatorics low). Surveys are kept out of the
main shortlist and at most 3 (by interest) appear in their own section; on most days there are
none — do not force any.

### Scoring rubric

The score estimates the level of journal in which the result belongs, weighted for this
audience. Judge the mathematics from the abstract and the Comments line — never the authors or
their institutions.

- **90–100 Landmark** (Annals, JAMS, Inventiones, Acta): resolves a famous longstanding open
  problem (a named conjecture, a decades-old question of Erdős and others) or introduces a
  method that will reshape an area.
- **80–89 Major** (Duke, Advances in Mathematics, GAFA, JEMS, Proc. LMS, the very best
  Combinatorica papers): a major advance on a well-known problem, a disproof of a well-known
  conjecture, broad interest beyond its subfield.
- **70–79 Strong** (Combinatorica, JCTB, Random Structures & Algorithms, IMRN, Trans. AMS,
  Israel J. Math., Forum Math. Sigma; STOC/FOCS level for algorithmic work): a clear improvement
  of the state of the art on a problem many combinatorialists know, a completed
  characterization, sharp bounds.
- **60–69 Solid** (JCTA, Electronic J. Combin., SIAM J. Discrete Math., J. Graph Theory, CPC,
  European J. Combin., Algebraic Combinatorics, Annals of Combinatorics, Discrete &
  Computational Geometry): solid new theorems with clean statements, of real interest to
  specialists.
- **45–59 Specialized** (Discrete Mathematics, Graphs and Combinatorics, Discrete Applied Math.,
  Australasian J. Combin., Ars Math. Contemp., Linear Algebra Appl.): incremental progress,
  narrow classes of objects, technical extensions.
- **30–44 Incremental**: small generalizations, routine computations, parameter tweaks, very
  narrow objects, new proofs of known results.
- **1–29 Limited**: no clear new theorem, claims that cannot be assessed, or papers whose
  math.CO tag is incidental (cross-lists far from combinatorics). Surveys are scored here too,
  but they are handled by the survey flag rather than the score.

Decide the band first, then the exact score inside it.

*Signals up*: a named conjecture proved or disproved; a longstanding question answered; sharp
or optimal bounds; general theorems replacing case-by-case results; a new technique with
several applications; a Comments line saying "to appear in \<journal>" (score near that
journal's band).
*Signals down*: results confined to tiny classes; results that follow easily from standard
tools; "we initiate the study of" a new invariant without external motivation; purely
computational verification; an abstract that does not state its main theorem.

*Audience weighting*: the readers are graph theorists and combinatorialists with a taste for
structural graph theory (minors, induced subgraphs, colouring, treewidth and width parameters,
planar graphs, digraphs, matroids), extremal combinatorics (Turán- and Ramsey-type problems,
hypergraphs, set systems, additive combinatorics) and probabilistic combinatorics (random
graphs and processes, thresholds, the probabilistic method). Add 3–8 points to papers squarely
in these areas; subtract 3–10 from papers whose combinatorial content is thin or whose primary
home is another field (analytic number theory, category theory, representation theory, computer
vision, mathematical physics), unless the result matters to combinatorialists. Algebraic,
enumerative and geometric combinatorics are neither boosted nor penalized.

*Landmark clause*: when an abstract states that a famous named problem or a long-open question
(for example one attributed to Erdős) is resolved, score it at least 88 unless the abstract
itself shows red flags (a claim inconsistent with known results, no statement of what is
proved); never state or imply that a paper is wrong.

*Calibration*: on a typical day 0–2 papers score 85 or more, about 4–8 score 70–84, and the
median new submission scores 50–60. Use the whole range. The `reason` is one neutral, specific
phrase about the result; the `venue` is a short journal-level phrase such as
"JCTB / Combinatorica level". Both stay private in `run/scores.json` — they are never published.

---

## STEP 3 — BUILD

```
python3 P build --papers run/papers.json --scores run/scores.json --index I \
        --out run/site --status final --private run/private_selection.json
python3 P check --file run/site/data/DATE.json
```

Fix any reported problems (missing or invalid scores) and rerun. `check` verifies that no
score, rank or verdict leaks into the published file.

---

## STEP 4 — PUBLISH

Before the first publish of a session, do one Artifact action `read` with `url` = ARTIFACT_URL
and **no** path (a read *with* a path does not count as having viewed the artifact).

Artifact `publish` with `file_path` = H, `url` = ARTIFACT_URL, and
`files = {"data/DATE.json": "run/site/data/DATE.json", "data/index.json": "run/site/data/index.json"}`
(real DATE in the key). Pass nothing else — never `run/private_selection.json`.

A publish can be refused because you have not viewed the live version, because a newer version
exists, or because a file you are replacing was not read in this session. In each case do the
reads it asks for (`read` with no path, and `read` with the named path), rebuild with the
freshly downloaded `data/index.json` as `--index` so the other run's dates are merged, and
publish again. **Never use `force`.**

Then Artifact `read` with `path` = `"data/index.json"` and confirm DATE is present with status
`"final"` and the full screened count.

---

## HARD CONSTRAINTS

- Never modify `index.html`, `tools/pipeline.py` or `tools/parse_new_listing.py`.
- Never publish scores, ranks or reasons. The build tool strips them; never add them back.
- Never pass `favicon`, `icon`, `title`, `capabilities` or `contract` when publishing.
- Never use `force` on publish.
- Bash times out after 120 s by default and is capped near 10 minutes: pass `timeout` in
  milliseconds for anything longer, and never `sleep 600` or more in one call.

## PERMANENT GAPS

**2026-10-13** (US Columbus Day) and **2026-10-29** (arXiv's 10-28 maintenance overran — "No
announcement Wednesday evening") are correct, permanent gaps. Never backfill either, and never
fetch 2026-10-29 again. Do not backfill any past date from catchup: the live listing page only
ever carries the most recent announcement, so a past day cannot be rebuilt correctly, and a
catchup-based backfill is what produced the bad 2026-11-03 entry in the first place.
