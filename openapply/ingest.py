"""Turn a posting link you found yourself into a `Job` (for `openapply add <url>`).

Step by step:
1. `parse_ats_url` works out which system hosts the link: Greenhouse, Lever,
   Ashby, or Workday. Anything else is refused with a plain message.
2. One polite request to that system's PUBLIC job API fetches the title,
   location, company and full description:
   - Greenhouse: boards-api.greenhouse.io/v1/boards/{board}/jobs/{id}
   - Lever:      api.lever.co/v0/postings/{company}/{id}
   - Ashby:      the company's public job board (Ashby has no single-job endpoint)
   - Workday:    {tenant}.wdN.myworkdayjobs.com/wday/cxs/{tenant}/{site}/job/...
                 (the JSON the career site itself loads; no login needed)
3. The result is a normal `Job` with the same id openapply would give it in
   discovery, so adding a link that discovery also finds never duplicates it.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .ats_links import canonical_apply_url, parse_ats_url
from .discovery.ashby import API as ASHBY_API, parse_ashby
from .discovery.greenhouse import ONE as GH_ONE, parse_greenhouse
from .discovery.lever import ONE as LEVER_ONE, _one as lever_one
from .http import PoliteClient
from .models import Job, make_job_id
from .text import html_to_text, strip_tracking


class UnsupportedLink(ValueError):
    pass


def _pretty(slug: str) -> str:
    return re.sub(r"[-_]+", " ", slug or "").strip().title()


def company_from_workday(org_name: str, tenant: str) -> str:
    """'1001 Amgen Inc.' -> 'Amgen Inc.'; falls back to the tenant name."""
    name = re.sub(r"^\d+\s+", "", (org_name or "").strip())
    return name or _pretty(tenant)


def workday_api_url(url: str) -> str:
    p = urlparse(url)
    parts = [x for x in p.path.split("/") if x]
    if parts and re.fullmatch(r"[a-z]{2}-[A-Z]{2}", parts[0]):
        parts = parts[1:]
    tenant = p.netloc.split(".")[0]
    site, rest = parts[0], "/".join(parts[1:])
    return f"https://{p.netloc}/wday/cxs/{tenant}/{site}/{rest}"


def parse_workday(url: str, data: dict) -> Job:
    ref = parse_ats_url(url)
    info = data.get("jobPostingInfo") or {}
    tenant = ref.board.split("/")[0] if ref else urlparse(url).netloc.split(".")[0]
    clean = url.split("?")[0]
    return Job(
        id=make_job_id("workday", ref.job_id if ref else None, clean),
        company=company_from_workday((data.get("hiringOrganization") or {}).get("name", ""), tenant),
        title=(info.get("title") or "").strip(),
        location=(info.get("location") or "").strip(),
        url=clean,
        ats="workday",
        ats_board=ref.board if ref else None,
        ats_job_id=ref.job_id if ref else None,
        description=html_to_text(info.get("jobDescription") or ""),
        sources=["added"],
        posted_at=info.get("startDate"),
        closed=info.get("canApply") is False,
    )


def job_from_url(url: str, client: PoliteClient | None = None) -> Job:
    url = strip_tracking(url.strip())
    ref = parse_ats_url(url)
    if ref is None:
        raise UnsupportedLink(f"not a Greenhouse, Lever, Ashby, or Workday posting link: {url}")
    own = client is None
    client = client or PoliteClient(min_delay=0.5)
    try:
        if ref.ats == "greenhouse":
            if not ref.board:
                raise UnsupportedLink("this Greenhouse link doesn't name the company board; open the posting and "
                                      "copy the job-boards.greenhouse.io link instead")
            data = client.get_json(GH_ONE.format(token=ref.board, job_id=ref.job_id) + "?content=true")
            job = parse_greenhouse(ref.board, {"jobs": [data]}, data.get("company_name") or _pretty(ref.board))[0]
        elif ref.ats == "lever":
            data = client.get_json(LEVER_ONE.format(company=ref.board, job_id=ref.job_id))
            job = lever_one(ref.board, data, _pretty(ref.board))
        elif ref.ats == "ashby":
            jobs = parse_ashby(ref.board, client.get_json(ASHBY_API.format(name=ref.board)), _pretty(ref.board))
            job = next((j for j in jobs if j.ats_job_id == ref.job_id), None)
            if job is None:
                raise UnsupportedLink(f"Ashby board {ref.board!r} has no open job {ref.job_id}")
        else:
            job = parse_workday(url, client.get_json(workday_api_url(url)))
    finally:
        if own:
            client.close()
    job.sources = sorted(set(job.sources) | {"added"})
    if job.ats in ("greenhouse", "lever", "ashby"):
        job.url = canonical_apply_url(job.ats, job.ats_board, job.ats_job_id, job.url)
    return job
