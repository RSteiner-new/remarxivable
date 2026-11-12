#!/usr/bin/env python3
"""Parse an arXiv /list/<cat>/new page into the pipeline's papers.json schema.

Used when the catchup endpoint is unreliable. The /new page carries full
abstracts, so no DataCite pass is needed.

usage: parse_new_listing.py <saved.html> <YYYY-MM-DD> <out.json>
"""
import html as _html
import json
import re
import sys
from datetime import datetime, timezone


def text(fragment):
    """Strip tags, unescape entities, collapse whitespace."""
    s = re.sub(r"<[^>]+>", " ", fragment)
    s = _html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def strip_descriptor(s, word):
    return re.sub(r"^%s:\s*" % word, "", s).strip()


def parse(path, date):
    raw = open(path, encoding="utf-8", errors="replace").read()

    heading = ""
    m = re.search(r"<h3>\s*(Showing new listings for[^<]*)</h3>", raw)
    if m:
        heading = text(m.group(1))

    # Section boundaries: each <h3> starts a section that runs to the next <h3>.
    marks = [(m.start(), text(m.group(1))) for m in re.finditer(r"<h3>(.*?)</h3>", raw, re.S)]
    marks.append((len(raw), "END"))

    sections = []
    for i in range(len(marks) - 1):
        label = marks[i][1].lower()
        if "new submissions" in label:
            kind = "new"
        elif "cross submissions" in label or "cross-lists" in label:
            kind = "cross"
        else:
            continue  # replacements and the page heading
        sections.append((kind, raw[marks[i][0]:marks[i + 1][0]], marks[i][1]))

    papers, counts = [], {"new": 0, "cross": 0}
    seen = set()
    for kind, blob, label in sections:
        mc = re.search(r"of\s+(\d+)\s+entries", label)
        if mc:
            counts[kind] = int(mc.group(1))
        # dt/dd pairs
        for dt, dd in re.findall(r"<dt>(.*?)</dt>\s*<dd>(.*?)</dd>", blob, re.S):
            mid = re.search(r"arXiv:(\d{4}\.\d{4,5})", dt)
            if not mid:
                continue
            pid = mid.group(1)
            if pid in seen:
                continue
            seen.add(pid)

            def grab(cls, src=dd):
                mm = re.search(r"<div class='list-%s[^']*'>(.*?)</div>" % cls, src, re.S)
                return text(mm.group(1)) if mm else ""

            title = strip_descriptor(grab("title"), "Title")
            authors_raw = grab("authors")
            authors = [a.strip() for a in strip_descriptor(authors_raw, "Authors?").split(",") if a.strip()]
            comments = strip_descriptor(grab("comments"), "Comments")
            subjects = strip_descriptor(grab("subjects"), "Subjects")

            cats = re.findall(r"\(([a-zA-Z-]+\.[A-Za-z-]+)\)", subjects)
            primary = ""
            mp = re.search(r'<span class="primary-subject">(.*?)</span>', dd, re.S)
            if mp:
                mpp = re.search(r"\(([a-zA-Z-]+\.[A-Za-z-]+)\)", text(mp.group(1)))
                if mpp:
                    primary = mpp.group(1)
            if not primary and cats:
                primary = cats[0]

            mab = re.search(r"<p class='mathjax'>(.*?)</p>", dd, re.S)
            abstract = text(mab.group(1)) if mab else ""

            papers.append({
                "id": pid,
                "type": kind,
                "title": title,
                "authors": authors,
                "categories": cats,
                "primary": primary,
                "comments": comments,
                "abstract": abstract,
            })

    return {
        "date": date,
        "source": "https://arxiv.org/list/math.CO/new",
        "papers": papers,
        "listed_new": counts["new"],
        "listed_cross": counts["cross"],
        "heading": heading,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def main():
    src, date, out = sys.argv[1], sys.argv[2], sys.argv[3]
    data = parse(src, date)
    json.dump(data, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    n_new = sum(1 for p in data["papers"] if p["type"] == "new")
    n_cross = sum(1 for p in data["papers"] if p["type"] == "cross")
    noabs = sum(1 for p in data["papers"] if not p["abstract"])
    print("heading:", data["heading"])
    print("header counts: %d new, %d cross-lists" % (data["listed_new"], data["listed_cross"]))
    print("wrote %s: %d papers (%d new, %d cross-listed); %d without abstract"
          % (out, len(data["papers"]), n_new, n_cross, noabs))
    if noabs:
        print("WARNING: %d papers have no abstract" % noabs)
    if n_new != data["listed_new"] or n_cross != data["listed_cross"]:
        print("WARNING: parsed counts differ from header counts")


if __name__ == "__main__":
    main()
