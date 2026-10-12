"""Greenhouse public Job Board API -> `Job`s.

Step by step:
1. GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
   returns {"jobs": [...]} with HTML-escaped descriptions in `content`.
2. Each job becomes a `Job` with ats="greenhouse", board=token, id=Greenhouse id.
3. The apply link is the Greenhouse-hosted page (job-boards.greenhouse.io/...),
   because companies' own careers pages often wrap it in an iframe.
"""

from __future__ import annotations

from ..ats_links import canonical_apply_url
from ..http import PoliteClient
from ..models import Job, make_job_id
from ..text import html_to_text

API = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
ONE = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}"


def parse_greenhouse(token: str, data: dict, company: str | None = None) -> list[Job]:
    jobs = []
    for j in data.get("jobs", []):
        job_id = str(j["id"])
        url = canonical_apply_url("greenhouse", token, job_id, j.get("absolute_url", ""))
        jobs.append(
            Job(
                id=make_job_id("greenhouse", job_id, url),
                company=company or j.get("company_name") or token,
                title=(j.get("title") or "").strip(),
                location=((j.get("location") or {}).get("name") or "").strip(),
                url=url,
                ats="greenhouse",
                ats_board=token,
                ats_job_id=job_id,
                description=html_to_text(j.get("content", "")),
                sources=["greenhouse_api"],
                posted_at=j.get("first_published") or j.get("updated_at"),
            )
        )
    return jobs


def fetch_greenhouse(client: PoliteClient, token: str, company: str | None = None) -> list[Job]:
    return parse_greenhouse(token, client.get_json(API.format(token=token)), company)


def fetch_greenhouse_description(client: PoliteClient, token: str, job_id: str) -> str:
    data = client.get_json(ONE.format(token=token, job_id=job_id))
    return html_to_text(data.get("content", ""))
