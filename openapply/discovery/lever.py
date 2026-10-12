"""Lever public Postings API -> `Job`s.

Step by step:
1. GET https://api.lever.co/v0/postings/{company}?mode=json returns a JSON list.
2. Description = opening + body + each "lists" section (requirements etc.) + closing.
3. The apply link is the Lever-hosted /apply page.
"""

from __future__ import annotations

from ..ats_links import canonical_apply_url
from ..http import PoliteClient
from ..models import Job, make_job_id
from ..text import html_to_text

API = "https://api.lever.co/v0/postings/{company}?mode=json"
ONE = "https://api.lever.co/v0/postings/{company}/{job_id}?mode=json"


def _description(p: dict) -> str:
    parts = [p.get("descriptionPlain") or html_to_text(p.get("description", ""))]
    for section in p.get("lists", []) or []:
        parts.append(section.get("text", ""))
        parts.append(html_to_text(section.get("content", "")))
    parts.append(p.get("additionalPlain") or html_to_text(p.get("additional", "")))
    return "\n".join(x for x in parts if x).strip()


def _one(company_slug: str, p: dict, company: str | None) -> Job:
    cats = p.get("categories") or {}
    locs = cats.get("allLocations") or ([cats["location"]] if cats.get("location") else [])
    job_id = p["id"]
    url = canonical_apply_url("lever", company_slug, job_id, p.get("applyUrl") or p.get("hostedUrl", ""))
    return Job(
        id=make_job_id("lever", job_id, url),
        company=company or company_slug,
        title=(p.get("text") or "").strip(),
        location=" ; ".join(locs),
        url=url,
        ats="lever",
        ats_board=company_slug,
        ats_job_id=job_id,
        description=_description(p),
        sources=["lever_api"],
        posted_at=str(p.get("createdAt") or "") or None,
    )


def parse_lever(company_slug: str, data: list, company: str | None = None) -> list[Job]:
    return [_one(company_slug, p, company) for p in data]


def fetch_lever(client: PoliteClient, company_slug: str, company: str | None = None) -> list[Job]:
    return parse_lever(company_slug, client.get_json(API.format(company=company_slug)), company)


def fetch_lever_description(client: PoliteClient, company_slug: str, job_id: str) -> str:
    return _description(client.get_json(ONE.format(company=company_slug, job_id=job_id)))
