"""Ashby public Posting API -> `Job`s.

Step by step:
1. GET https://api.ashbyhq.com/posting-api/job-board/{name}?includeCompensation=true
   returns {"jobs": [...]}; unlisted jobs have isListed=false and are skipped.
2. Location = primary location plus any secondary locations.
3. The apply link is the Ashby-hosted /application page.
Ashby has no single-job endpoint, so enrichment fetches the whole board once.
"""

from __future__ import annotations

from ..ats_links import canonical_apply_url
from ..http import PoliteClient
from ..models import Job, make_job_id
from ..text import html_to_text

API = "https://api.ashbyhq.com/posting-api/job-board/{name}?includeCompensation=true"


def parse_ashby(board: str, data: dict, company: str | None = None) -> list[Job]:
    jobs = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        if j.get("isRemote") and not any("remote" in l.lower() for l in locs):
            locs.append("Remote")
        job_id = j["id"]
        url = canonical_apply_url("ashby", board, job_id, j.get("applyUrl") or j.get("jobUrl", ""))
        jobs.append(
            Job(
                id=make_job_id("ashby", job_id, url),
                company=company or board,
                title=(j.get("title") or "").strip(),
                location=" ; ".join(l for l in locs if l),
                url=url,
                ats="ashby",
                ats_board=board,
                ats_job_id=job_id,
                description=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml", "")),
                sources=["ashby_api"],
                posted_at=j.get("publishedAt"),
            )
        )
    return jobs


def fetch_ashby(client: PoliteClient, board: str, company: str | None = None) -> list[Job]:
    return parse_ashby(board, client.get_json(API.format(name=board)), company)
