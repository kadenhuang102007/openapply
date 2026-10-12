"""Shared logic for community lists: turn (company, title, location, link) into a Job.

If the link is a Greenhouse/Lever/Ashby posting, we record the ATS ids so the job
merges with the API copy and can be enriched and auto-filled later.
"""

from __future__ import annotations

from ..ats_links import canonical_apply_url, parse_ats_url
from ..models import Job, make_job_id
from ..text import strip_tracking


GENERIC_COMPANY = {"", "internship", "internships", "intern", "careers", "jobs", "unknown", "n/a"}


def community_job(*, company: str, title: str, location: str, link: str, source: str, **extra) -> Job:
    link = strip_tracking(link)
    ref = parse_ats_url(link)
    ats, board, job_id = (ref.ats, ref.board, ref.job_id) if ref else ("other", None, None)
    url = canonical_apply_url(ats, board, job_id, link)
    if company.strip().lower() in GENERIC_COMPANY and board:
        company = board.replace("-", " ").title()  # upstream list had a placeholder name
    return Job(
        id=make_job_id(ats, job_id, url),
        company=company.strip(),
        title=title.strip(),
        location=location.strip(),
        url=url,
        ats=ats,
        ats_board=board,
        ats_job_id=job_id,
        sources=[source],
        **extra,
    )
