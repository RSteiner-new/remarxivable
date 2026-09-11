#!/usr/bin/env python3
"""remarxivable data pipeline.

Sub-commands (all write JSON, all print a short human-readable summary):

  listing  --in RAW.json --date YYYY-MM-DD --out papers.json
           Normalise a transcription of the arXiv math.CO catchup/listing page
           (new submissions + cross-lists; replacements are dropped).
           Prints the identifiers that still lack an abstract.

  merge    --papers papers.json --absdir DIR
           Fill in abstracts/comments/subjects from per-paper files DIR/<id>.json
           (transcriptions of https://arxiv.org/abs/<id>). Updates papers.json in place.

  build    --papers papers.json --scores scores.json --index index.json --out SITEDIR [--top 30]
           Validate scores, rank, and write SITEDIR/data/<date>.json plus an updated
           SITEDIR/data/index.json (newest first). scores.json maps id -> {score, venue, reason}.

  check    --file SITEDIR/data/<date>.json
           Validate a built day file.

The transcriptions may come from an LLM, so parsing is deliberately tolerant.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys

TOP_DEFAULT = 30

TIERS = [  # lower bound, label, description used in the site legend
    (90, "Landmark", "Annals / JAMS / Inventiones / Acta level: resolves a famous problem or introduces a transformative method"),
    (80, "Major", "Duke / Advances / GAFA / JEMS / best Combinatorica papers: a major advance on a well-known problem"),
    (70, "Strong", "Combinatorica / JCTB / RSA / IMRN / Trans. AMS: a clear improvement on a problem of wide interest"),
    (60, "Solid", "JCTA / EJC / SIAM J. Discrete Math / JGT / CPC / EuJC: a solid contribution of interest to specialists"),
    (45, "Specialized", "Discrete Math. / Graphs & Combin. / DAM / AJC: incremental or narrowly specialized"),
    (30, "Incremental", "Small generalizations, routine computations, or very narrow scope"),
    (1, "Limited", "Hard to assess from the abstract, mainly expository, or marginal to combinatorics"),
]

ID_RE = re.compile(r"(\d{4}\.\d{4,5})(?:v\d+)?")
OLD_ID_RE = re.compile(r"([a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?")
CAT_RE = re.compile(r"\(([a-z\-]+(?:\.[A-Za-z\-]+)?)\)")


def tier_for(score):
    for lo, label, _ in TIERS:
        if score >= lo:
            return label
    return TIERS[-1][1]


def load_json_tolerant(path):
    txt = open(path, encoding="utf-8").read().strip()
    # strip markdown fences
    txt = re.sub(r"^```(?:json)?\s*", "", txt)
    txt = re.sub(r"\s*```\s*$", "", txt)
    try:
        return json.loads(txt)
    except json.JSONDecodeError:
        pass
    # cut to the outermost bracket pair
    starts = [i for i in (txt.find("["), txt.find("{")) if i >= 0]
    if starts:
        s = min(starts)
        e = max(txt.rfind("]"), txt.rfind("}"))
        cand = txt[s : e + 1]
        cand = re.sub(r",(\s*[\]}])", r"\1", cand)  # trailing commas
        return json.loads(cand)
    raise SystemExit(f"could not parse JSON in {path}")


def norm_id(s):
    if not s:
        return None
    s = str(s)
    m = ID_RE.search(s)
    if m:
        return m.group(1)
    m = OLD_ID_RE.search(s)
    if m:
        return m.group(1)
    return None


def flip_name(n):
    """DataCite writes 'Family, Given'; the listing writes 'Given Family'."""
    n = n.strip()
    if n.count(",") == 1 and not re.search(r"\bJr\.?|\bSr\.?|\bI{2,3}$", n):
        fam, giv = [x.strip() for x in n.split(",")]
        if fam and giv:
            return f"{giv} {fam}"
    return n


def norm_authors(a, flip=False):
    if a is None:
        return []
    if isinstance(a, str):
        a = re.sub(r"^\s*Authors?:\s*", "", a)
        parts = re.split(r",\s*|\s+and\s+|;\s*", a)
        return [p.strip() for p in parts if p.strip()]
    out = []
    for x in a:
        if isinstance(x, dict):
            x = x.get("name") or x.get("author") or " ".join(filter(None, [x.get("givenName"), x.get("familyName")]))
        x = str(x).strip()
        if flip:
            x = flip_name(x)
        if x:
            out.append(x)
    return out


MSC_RE = re.compile(r"\b\d{2}[A-Z]\d{2}\b|\b\d{2}-\d{2}\b|\b\d{2}[A-Z]xx\b")


def norm_cats(subjects):
    """'Combinatorics (math.CO); Discrete Mathematics (cs.DM)' -> ['math.CO','cs.DM'].
    Accepts a string or a list (DataCite style, where MSC codes and 'FOS: ...' lines are mixed in)."""
    if subjects is None:
        return []
    items = subjects if isinstance(subjects, list) else [subjects]
    cats = []
    for s in items:
        s = str(s)
        found = CAT_RE.findall(s)
        if found:
            cats.extend(found)
        elif re.fullmatch(r"[a-z\-]+\.[A-Z]{2}|[a-z\-]+", s.strip()) and not s.strip().startswith("FOS"):
            cats.append(s.strip())  # bare 'math.CO'
    seen, out = set(), []
    for c in cats:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def norm_msc(subjects, explicit=None):
    if explicit:
        return clean_text(explicit)
    if isinstance(subjects, list):
        codes = []
        for s in subjects:
            s = str(s)
            if not CAT_RE.search(s) and not s.startswith("FOS"):
                codes.extend(MSC_RE.findall(s))
        return ", ".join(dict.fromkeys(codes))
    return ""


def norm_section(v, cats):
    v = (str(v or "")).lower()
    if "replace" in v:
        return "replace"
    if "cross" in v:
        return "cross"
    if v in ("new", "new submissions", "new submission"):
        return "new"
    # infer from primary category when the transcription did not label it
    if cats and cats[0] != "math.CO":
        return "cross"
    return "new"


def clean_text(t):
    if t is None:
        return ""
    t = str(t)
    t = re.sub(r"^\s*(Abstract|Title|Comments?|Subjects?)\s*:\s*", "", t, flags=re.I)
    t = t.replace("\r", "")
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def normalise_entry(e):
    if not isinstance(e, dict):
        return None
    pid = norm_id(e.get("id") or e.get("arxiv_id") or e.get("identifier") or e.get("arxiv") or e.get("link") or e.get("url"))
    if not pid:
        return None
    cats = norm_cats(e.get("subjects") or e.get("categories") or e.get("subject"))
    section = norm_section(e.get("section") or e.get("type") or e.get("announce_type") or e.get("kind"), cats)
    return {
        "id": pid,
        "type": section,
        "title": clean_text(e.get("title")),
        "authors": norm_authors(e.get("authors") or e.get("author") or e.get("creators")),
        "categories": cats,
        "primary": cats[0] if cats else ("math.CO" if section == "new" else ""),
        "comments": clean_text(e.get("comments") or e.get("comment")),
        "abstract": clean_text(e.get("abstract") or e.get("summary")),
    }


def find_entries(raw):
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for k in ("papers", "entries", "items", "results", "new", "cross"):
            if isinstance(raw.get(k), list):
                lst = list(raw[k])
                # a dict with separate new/cross lists
                for k2 in ("new", "cross"):
                    if k2 != k and isinstance(raw.get(k2), list):
                        for x in raw[k2]:
                            if isinstance(x, dict):
                                x.setdefault("section", k2)
                        lst.extend(raw[k2])
                return lst
        # maybe keyed by id
        vals = [v for v in raw.values() if isinstance(v, dict)]
        if vals:
            return vals
    return []


def cmd_listing(a):
    raw = load_json_tolerant(a.inp)
    entries = find_entries(raw)
    papers, seen = [], set()
    dropped_replace = 0
    for e in entries:
        p = normalise_entry(e)
        if not p:
            continue
        if p["type"] == "replace":
            dropped_replace += 1
            continue
        if p["id"] in seen:
            continue
        seen.add(p["id"])
        papers.append(p)
    if a.primary_only:
        papers = [p for p in papers if p["primary"] == "math.CO"]
    out = {"date": a.date, "source": f"https://arxiv.org/catchup/math.CO/{a.date}", "papers": papers}
    json.dump(out, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    missing = [p["id"] for p in papers if len(p["abstract"]) < 40]
    n_new = sum(1 for p in papers if p["type"] == "new")
    n_cross = len(papers) - n_new
    print(f"{len(papers)} papers kept ({n_new} new, {n_cross} cross-listed); {dropped_replace} replacements dropped")
    if a.expect_new is not None and a.expect_new != n_new:
        print(f"WARNING: page header says {a.expect_new} new submissions but {n_new} were transcribed - re-fetch the missing ones")
    if a.expect_cross is not None and a.expect_cross != n_cross:
        print(f"WARNING: page header says {a.expect_cross} cross submissions but {n_cross} were transcribed - re-fetch the missing ones")
    print(f"{len(missing)} without abstract:")
    print(" ".join(missing))
    print("DataCite URLs:")
    for p in papers:
        if len(p["abstract"]) < 40:
            print(f"https://api.datacite.org/dois/10.48550/arxiv.{p['id']}")


def iter_records(absdir):
    """Yield (id, record) from every JSON file in absdir. A file may hold one record or a
    list of records; the id comes from the record ('id'/'doi'/'url') or else from the file name."""
    for fn in sorted(os.listdir(absdir)):
        if not fn.endswith(".json"):
            continue
        try:
            raw = load_json_tolerant(os.path.join(absdir, fn))
        except (SystemExit, json.JSONDecodeError):
            print(f"skip unparsable {fn}")
            continue
        recs = raw if isinstance(raw, list) else [raw]
        for r in recs:
            if not isinstance(r, dict) or r.get("error"):
                continue
            pid = norm_id(r.get("id") or r.get("arxiv_id") or r.get("doi") or r.get("url")) or (norm_id(fn) if len(recs) == 1 else None)
            if pid:
                yield pid, r


def cmd_merge(a):
    data = json.load(open(a.papers, encoding="utf-8"))
    by_id = {p["id"]: p for p in data["papers"]}
    filled, unknown = 0, []
    for pid, raw in iter_records(a.absdir):
        if pid not in by_id:
            unknown.append(pid)
            continue
        p = by_id[pid]
        abs_ = clean_text(raw.get("abstract") or raw.get("summary") or raw.get("description"))
        if len(abs_) >= 40 and len(p["abstract"]) < 40:
            p["abstract"] = abs_
            filled += 1
        if not p["title"] and raw.get("title"):
            p["title"] = clean_text(raw.get("title"))
        if not p["authors"] and (raw.get("authors") or raw.get("author") or raw.get("creators")):
            p["authors"] = norm_authors(raw.get("authors") or raw.get("author") or raw.get("creators"), flip=True)
        if not p["comments"] and (raw.get("comments") or raw.get("comment")):
            p["comments"] = clean_text(raw.get("comments") or raw.get("comment"))
        subj = raw.get("subjects") or raw.get("categories")
        if not p["categories"] and subj:
            p["categories"] = norm_cats(subj)
            p["primary"] = p["categories"][0] if p["categories"] else p["primary"]
        msc = norm_msc(subj, raw.get("msc"))
        if msc and not p.get("msc"):
            p["msc"] = msc
        jr = clean_text(raw.get("journal_ref"))
        if jr:
            p["journal_ref"] = jr
    json.dump(data, open(a.papers, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    still = [p["id"] for p in data["papers"] if len(p["abstract"]) < 40]
    if unknown:
        print(f"note: {len(unknown)} records did not match any listed paper: {' '.join(unknown)}")
    print(f"filled {filled} abstracts; {len(still)} still missing:")
    print(" ".join(still))


CHECK_STATUSES = ("ok", "concerns", "gap", "unchecked")


def load_scores(path):
    scores = load_json_tolerant(path)
    if isinstance(scores, list):  # allow [{id, score, ...}]
        scores = {norm_id(s.get("id")): s for s in scores if isinstance(s, dict)}
    else:
        scores = {norm_id(k): v for k, v in scores.items()}
    return scores


def norm_check(c):
    """Full-text check record -> {"status", "note", "pages"}. Accepts None."""
    if not isinstance(c, dict):
        return {"status": "unchecked", "note": "", "pages": None}
    status = str(c.get("status") or c.get("verdict") or "unchecked").strip().lower()
    aliases = {"pass": "ok", "fine": "ok", "plausible": "ok", "no concerns": "ok", "minor": "concerns",
               "minor concerns": "concerns", "serious": "gap", "serious gap": "gap", "exclude": "gap",
               "excluded": "gap", "fail": "gap", "pending": "unchecked", "not checked": "unchecked"}
    status = aliases.get(status, status)
    if status not in CHECK_STATUSES:
        status = "unchecked"
    pages = c.get("pages")
    try:
        pages = int(pages) if pages not in (None, "") else None
    except (TypeError, ValueError):
        pages = None
    return {"status": status, "note": clean_text(c.get("note") or c.get("summary") or ""), "pages": pages}


def cmd_build(a):
    data = json.load(open(a.papers, encoding="utf-8"))
    scores = load_scores(a.scores)
    checks = {}
    if a.checks and os.path.exists(a.checks):
        checks = {k: v for k, v in load_scores(a.checks).items() if k}
    problems = []
    ranked, excluded = [], []
    for p in data["papers"]:
        s = scores.get(p["id"])
        if not isinstance(s, dict):
            problems.append(f"no score for {p['id']}")
            continue
        try:
            sc = int(round(float(s.get("score"))))
        except (TypeError, ValueError):
            problems.append(f"bad score for {p['id']}: {s.get('score')!r}")
            continue
        if not 1 <= sc <= 100:
            problems.append(f"score out of range for {p['id']}: {sc}")
            continue
        q = dict(p)
        q["score"] = sc
        q["tier"] = tier_for(sc)
        q["venue"] = clean_text(s.get("venue") or s.get("journal") or "")
        q["reason"] = clean_text(s.get("reason") or s.get("rationale") or "")
        q["abs"] = f"https://arxiv.org/abs/{p['id']}"
        q["pdf"] = f"https://arxiv.org/pdf/{p['id']}"
        chk = norm_check(checks.get(p["id"]) or s.get("check"))
        q["check"] = chk
        if chk["status"] == "gap":
            excluded.append({"id": p["id"], "title": p["title"], "score": sc, "note": chk["note"]})
            continue
        ranked.append(q)
    if problems:
        print("PROBLEMS:\n  " + "\n  ".join(problems))
        if not a.force:
            raise SystemExit("fix scores.json (every paper needs an integer score 1-100) or pass --force to drop unscored papers")
    extra = set(scores) - {p["id"] for p in data["papers"]}
    if extra:
        print(f"note: {len(extra)} scored ids are not in the listing and were ignored: {' '.join(sorted(x for x in extra if x))}")
    ranked.sort(key=lambda q: (-q["score"], 0 if q["type"] == "new" else 1, q["id"]))
    for i, q in enumerate(ranked, 1):
        q["rank"] = i
    top = ranked[: a.top]
    n_new = sum(1 for p in data["papers"] if p["type"] == "new")
    n_checked = sum(1 for q in ranked if q["check"]["status"] in ("ok", "concerns")) + len(excluded)
    day = {
        "date": data["date"],
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": a.status,
        "source": data.get("source", f"https://arxiv.org/catchup/math.CO/{data['date']}"),
        "screened": len(data["papers"]),
        "screened_new": n_new,
        "screened_cross": len(data["papers"]) - n_new,
        "scored": len(ranked) + len(excluded),
        "checked": n_checked,
        "checked_shown": sum(1 for q in top if q["check"]["status"] in ("ok", "concerns")),
        "excluded": len(excluded),
        "shown": len(top),
        "papers": top,
    }
    if excluded:
        print("EXCLUDED after full-text check (not published on the page):")
        for e in excluded:
            print(f"  {e['id']} (score {e['score']}) {e['title'][:70]} — {e['note']}")
    os.makedirs(os.path.join(a.out, "data"), exist_ok=True)
    day_path = os.path.join(a.out, "data", f"{data['date']}.json")
    json.dump(day, open(day_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    # index
    index = {"days": []}
    if a.index and os.path.exists(a.index):
        try:
            index = load_json_tolerant(a.index)
        except SystemExit:
            print("warning: could not parse existing index.json, starting fresh")
    days = [d for d in index.get("days", []) if d.get("date") != data["date"]]
    days.append({"date": data["date"], "screened": day["screened"], "shown": day["shown"], "status": day["status"],
                 "checked": day["checked"], "generated_at": day["generated_at"]})
    days.sort(key=lambda d: d["date"], reverse=True)
    index = {"site": "remarxivable", "category": "math.CO", "updated_at": day["generated_at"], "days": days}
    idx_path = os.path.join(a.out, "data", "index.json")
    json.dump(index, open(idx_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"wrote {day_path} ({day['shown']} of {day['screened']} papers, status {day['status']}, "
          f"{day['checked']} full texts checked, {day['excluded']} excluded) and {idx_path} ({len(days)} days)")
    for q in top[:5]:
        print(f"  #{q['rank']:>2} {q['score']:>3} {q['tier']:<12} {q['id']}  [{q['check']['status']}] {q['title'][:70]}")


def cmd_candidates(a):
    """List the papers whose full text should be checked, in priority order: everything that would be
    shown (top N by abstract score) plus a margin of alternates, skipping ids already checked."""
    data = json.load(open(a.papers, encoding="utf-8"))
    scores = load_scores(a.scores)
    done = set()
    if a.checks and os.path.exists(a.checks):
        done = {k for k, v in load_scores(a.checks).items() if norm_check(v)["status"] != "unchecked"}
    rows = []
    for p in data["papers"]:
        s = scores.get(p["id"])
        if isinstance(s, dict):
            try:
                rows.append((int(round(float(s.get("score")))), p))
            except (TypeError, ValueError):
                pass
    rows.sort(key=lambda r: (-r[0], 0 if r[1]["type"] == "new" else 1, r[1]["id"]))
    todo = [(sc, p) for sc, p in rows[: a.top + a.margin] if p["id"] not in done]
    print(f"{len(todo)} papers to check (top {a.top} + {a.margin} alternates, {len(done)} already checked):")
    for sc, p in todo:
        print(f"{sc:>3} {p['id']}  https://arxiv.org/pdf/{p['id']}  {p['title'][:70]}")


def cmd_check(a):
    d = json.load(open(a.file, encoding="utf-8"))
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", d["date"]), "bad date"
    assert isinstance(d["papers"], list) and d["papers"], "no papers"
    last = 101
    for p in d["papers"]:
        for k in ("id", "title", "authors", "abstract", "score", "tier", "reason", "abs", "rank"):
            assert k in p, f"{p.get('id')} missing {k}"
        assert 1 <= p["score"] <= last, "not sorted"
        last = p["score"]
    print(f"ok: {d['date']} {d['shown']}/{d['screened']} papers, top score {d['papers'][0]['score']}")


HTML_SKELETON_HEAD = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
                      '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
                      '<style>:root{color-scheme:light}body{margin:0;font:14px system-ui,sans-serif;background:#f4f5f7}'
                      'img{max-width:100%}[hidden]{display:none!important}</style>\n</head>\n<body>\n')
HTML_SKELETON_TAIL = '\n</body>\n</html>\n'


def cmd_wrap(a):
    """Turn the artifact-style page (no doctype/head/body) into a complete HTML document for plain hosting."""
    body = open(a.inp, encoding="utf-8").read()
    if body.lstrip().lower().startswith("<!doctype"):
        html = body
    else:
        html = HTML_SKELETON_HEAD + body + HTML_SKELETON_TAIL
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    open(a.out, "w", encoding="utf-8").write(html)
    print(f"wrote {a.out} ({len(html)} bytes)")


def github_request(method, url, token, data=None):
    import urllib.request
    import urllib.error
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, method=method, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "remarxivable-pipeline",
        "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            txt = r.read().decode()
            return r.status, (json.loads(txt) if txt else {})
    except urllib.error.HTTPError as e:
        txt = e.read().decode(errors="replace")
        try:
            return e.code, json.loads(txt)
        except json.JSONDecodeError:
            return e.code, {"message": txt}


def cmd_github_push(a):
    """Upload files to a GitHub repository through the contents API (one commit per file).
    --files takes LOCAL:REMOTE pairs, e.g. run/site/data/2026-09-11.json:data/2026-09-11.json"""
    import base64
    token = os.environ.get(a.token_env, "") or a.token
    if not token:
        raise SystemExit(f"no token: set ${a.token_env} or pass --token")
    ok = 0
    for spec in a.files:
        local, _, remote = spec.partition(":")
        remote = remote or local
        content = open(local, "rb").read()
        url = f"https://api.github.com/repos/{a.repo}/contents/{remote}"
        status, cur = github_request("GET", url + f"?ref={a.branch}", token)
        sha = cur.get("sha") if status == 200 else None
        if sha and base64.b64decode(cur.get("content", "").encode()) == content:
            print(f"unchanged {remote}")
            ok += 1
            continue
        payload = {"message": f"{a.message}: {remote}", "content": base64.b64encode(content).decode(), "branch": a.branch}
        if sha:
            payload["sha"] = sha
        status, resp = github_request("PUT", url, token, payload)
        if status in (200, 201):
            print(f"pushed {remote} ({len(content)} bytes)")
            ok += 1
        else:
            print(f"FAILED {remote}: HTTP {status} {resp.get('message')}")
    if ok != len(a.files):
        raise SystemExit(f"{len(a.files)-ok} file(s) failed")
    print(f"all {ok} file(s) are on {a.repo}@{a.branch}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("listing"); s.add_argument("--in", dest="inp", required=True); s.add_argument("--date", required=True)
    s.add_argument("--out", required=True); s.add_argument("--primary-only", action="store_true")
    s.add_argument("--expect-new", type=int, default=None, help="count from the 'New submissions (showing X of Y)' header")
    s.add_argument("--expect-cross", type=int, default=None, help="count from the 'Cross submissions (showing X of Y)' header")
    s.set_defaults(fn=cmd_listing)
    s = sub.add_parser("merge"); s.add_argument("--papers", required=True); s.add_argument("--absdir", required=True); s.set_defaults(fn=cmd_merge)
    s = sub.add_parser("build"); s.add_argument("--papers", required=True); s.add_argument("--scores", required=True)
    s.add_argument("--checks", default=None, help="JSON mapping id -> {status: ok|concerns|gap|unchecked, note, pages}")
    s.add_argument("--status", default="final", choices=["provisional", "final"], help="provisional = full-text checks still to come")
    s.add_argument("--index", default=None); s.add_argument("--out", required=True); s.add_argument("--top", type=int, default=TOP_DEFAULT)
    s.add_argument("--force", action="store_true"); s.set_defaults(fn=cmd_build)
    s = sub.add_parser("candidates"); s.add_argument("--papers", required=True); s.add_argument("--scores", required=True)
    s.add_argument("--checks", default=None); s.add_argument("--top", type=int, default=TOP_DEFAULT); s.add_argument("--margin", type=int, default=6)
    s.set_defaults(fn=cmd_candidates)
    s = sub.add_parser("check"); s.add_argument("--file", required=True); s.set_defaults(fn=cmd_check)
    s = sub.add_parser("wrap"); s.add_argument("--in", dest="inp", required=True); s.add_argument("--out", required=True); s.set_defaults(fn=cmd_wrap)
    s = sub.add_parser("github-push"); s.add_argument("--repo", required=True, help="owner/name")
    s.add_argument("--branch", default="main"); s.add_argument("--token-env", default="GITHUB_TOKEN"); s.add_argument("--token", default="")
    s.add_argument("--message", default="remarxivable update"); s.add_argument("--files", nargs="+", required=True, help="LOCAL:REMOTE pairs")
    s.set_defaults(fn=cmd_github_push)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
