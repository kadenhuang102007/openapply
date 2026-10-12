"""zshah101's "Automated List of Summer 2027 and Fall 2026 Tech Internships" -> `Job`s.

That repo (default branch `main`) is itself an engine that polls ~5,000 job
boards. Its README shows only part of the list as markdown tables, but it also
commits the full dataset as `data/internships.csv`, so we read the CSV.

CSV columns we use: id ("greenhouse:board:jobid"), company, title, season
("Summer 2027" / "Fall 2026" / "Not stated"), location, sponsorship
("unknown" / "no-sponsorship" / "citizens-only" / "offers"), skills, posted_at, url.

Step by step:
1. Download the CSV. Skip rows whose season is "Fall 2026" (off-cycle for us).
2. Map sponsorship to our flags; keep their skill tags as a hint for matching.
3. If the CSV is unavailable, fall back to parsing the README's markdown tables.
"""

from __future__ import annotations

import csv
import io
import re

from ..http import PoliteClient
from ..models import Job
from ._community import community_job

REPO = "zshah101/Automated-List-Of-Summer-2027-and-Fall-2026-Tech-Internships"
CSV_URL = f"https://raw.githubusercontent.com/{REPO}/main/data/internships.csv"
README_URL = f"https://raw.githubusercontent.com/{REPO}/main/README.md"

_SPONSOR = {"no-sponsorship": "no_sponsorship", "citizens-only": "us_citizenship"}


def parse_zshah_csv(text: str) -> list[Job]:
    jobs = []
    for r in csv.DictReader(io.StringIO(text)):
        season = (r.get("season") or "").strip()
        if season == "Fall 2026":
            continue
        if not r.get("url"):
            continue
        flags = [_SPONSOR[r["sponsorship"]]] if r.get("sponsorship") in _SPONSOR else []
        jobs.append(
            community_job(
                company=r["company"],
                title=r["title"],
                location=r.get("location", ""),
                link=r["url"],
                source="zshah",
                season=season if season.startswith("Summer") else None,
                category=r.get("category") or None,
                source_flags=flags,
                skills_hint=[s.strip() for s in (r.get("skills") or "").split(";") if s.strip()],
                posted_at=r.get("posted_at") or None,
            )
        )
    return jobs


_MD_ROW = re.compile(r"^\| (.+?) \| (.+?) \| \[Apply\]\((\S+?)\) \| (.*?) \|", re.M)


def parse_zshah_readme(md: str) -> list[Job]:
    """Fallback: the README's '## Summer 2027' markdown table only."""
    jobs = []
    for chunk in re.split(r"(?m)^## ", md):
        if not chunk.startswith("Summer 2027"):
            continue
        for company, title, link, location in _MD_ROW.findall(chunk):
            flags = []
            if "🛂" in title:
                flags.append("no_sponsorship")
            if "🇺🇸" in title:
                flags.append("us_citizenship")
            title = re.sub(r"[🛂🇺🇸🆕]", "", title).strip()
            company = company.replace("✓", "").strip()
            jobs.append(
                community_job(company=company, title=title, location=location, link=link,
                              source="zshah", season="Summer 2027", source_flags=flags)
            )
    return jobs


def fetch_zshah(client: PoliteClient) -> list[Job]:
    resp = client.get(CSV_URL)
    if resp.status_code == 200 and resp.text.startswith("id,"):
        return parse_zshah_csv(resp.text)
    return parse_zshah_readme(client.get_text(README_URL))
