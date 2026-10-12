"""SimplifyJobs "Summer 2027 Internships" README -> `Job`s.

The README (branch `dev`) is a set of HTML tables, one per section
("Software Engineering", "Product Management", "Data Science, AI & Machine
Learning", "Quantitative Finance", "Hardware Engineering").

Step by step:
1. Walk the file line by line, remembering the current `## ... Roles` heading.
2. Inside <tr> ... </tr>, collect the five <td> cells:
   Company | Role | Location | Application | Age.
3. A company cell of "↳" means "same company as the row above".
4. A multi-location cell is a <details> block with <br>-separated cities.
5. The Application cell has an "Apply" link (the real posting) and a Simplify
   link; we keep the real one. A 🔒 means the role is closed and is skipped.
6. Emoji flags become machine flags: 🛂 no sponsorship, 🇺🇸 US citizenship,
   🎓 advanced degree required.
If the 2027 repo ever disappears we fall back to the Summer 2026 repo.
"""

from __future__ import annotations

import re

from ..http import PoliteClient
from ..models import Job
from ..text import html_to_text
from ._community import community_job

URLS = [
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/README.md",
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/dev/README.md",
]

_ROW = re.compile(r"<tr>(.*?)</tr>", re.S)
_CELL = re.compile(r"<td>(.*?)</td>", re.S)
_HREF = re.compile(r'<a href="([^"]+)"[^>]*>(.*?)</a>', re.S)
_FLAGS = {"🛂": "no_sponsorship", "🇺🇸": "us_citizenship", "🎓": "advanced_degree"}


def _clean(cell: str) -> str:
    cell = re.sub(r"<br\s*/?>", "; ", cell)
    cell = re.sub(r"<summary>.*?</summary>", "", cell, flags=re.S)
    return html_to_text(cell).replace("\n", " ").strip()


def _apply_link(cell: str) -> str | None:
    links = [href for href, inner in _HREF.findall(cell)]
    real = [h for h in links if "simplify.jobs" not in h]
    return (real or links or [None])[0]


def parse_simplify_readme(md: str, season: str = "Summer 2027") -> list[Job]:
    jobs: list[Job] = []
    section = None
    last_company = ""
    # Split into chunks at section headings so each row knows its category.
    for chunk in re.split(r"(?m)^## ", md):
        heading = chunk.split("\n", 1)[0].strip()
        if "Internship Roles" in heading:
            section = re.sub(r"^[^\w]+", "", heading).replace(" Internship Roles", "").strip()
        else:
            continue
        for row in _ROW.findall(chunk):
            cells = _CELL.findall(row)
            if len(cells) < 4:
                continue
            company_cell, role_cell, loc_cell, app_cell = cells[:4]
            company = _clean(company_cell).replace("🔥", "").strip()
            if company in ("↳", ""):
                company = last_company
            else:
                last_company = company
            if "🔒" in row:
                continue  # closed
            link = _apply_link(app_cell)
            if not link:
                continue
            raw_title = _clean(role_cell)
            flags = [flag for emoji, flag in _FLAGS.items() if emoji in raw_title]
            title = raw_title
            for emoji in list(_FLAGS) + ["🔥"]:
                title = title.replace(emoji, "")
            jobs.append(
                community_job(
                    company=company,
                    title=title,
                    location=_clean(loc_cell),
                    link=link,
                    source="simplify",
                    season=season,
                    category=section,
                    source_flags=flags,
                )
            )
    return jobs


def fetch_simplify(client: PoliteClient) -> tuple[list[Job], str]:
    """Return (jobs, url actually used)."""
    for url in URLS:
        resp = client.get(url)
        if resp.status_code == 200:
            season = "Summer 2027" if "2027" in url else "Summer 2026"
            return parse_simplify_readme(resp.text, season=season), url
    return [], ""
