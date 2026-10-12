"""Recognize Greenhouse / Lever / Ashby / Workday links and turn them into (ats, board, job id).

Why this matters: community lists (SimplifyJobs, zshah101) give us a raw apply
link. If that link is really a Greenhouse/Lever/Ashby posting we can
1. de-duplicate it against the same job we pulled from the official API, and
2. fetch the full job description from the public API for better matching, and
3. open the ATS's own application form, which apply/ knows how to fill.

Workday links (`{tenant}.wd{N}.myworkdayjobs.com/{site}/job/{place}/{slug}_{R-123}`)
are recognized too: ats="workday", board="{tenant}/{site}", and the job id is
"{tenant}:{requisition id}" (requisition ids are only unique inside one company).
There's no public description API we use during discovery, so Workday jobs keep
their link as-is and are filled by apply/workday.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


@dataclass(frozen=True)
class AtsRef:
    ats: str
    board: str | None
    job_id: str


def parse_ats_url(url: str) -> AtsRef | None:
    if not url:
        return None
    p = urlparse(url.strip())
    host = p.netloc.lower()
    path = p.path
    qs = parse_qs(p.query)

    if host.endswith("greenhouse.io"):
        m = re.match(r"^/([^/]+)/jobs/(\d+)", path)
        if m and m.group(1) != "embed":
            return AtsRef("greenhouse", m.group(1), m.group(2))
        if "token" in qs:  # boards.greenhouse.io/embed/job_app?for=board&token=123
            return AtsRef("greenhouse", (qs.get("for") or [None])[0], qs["token"][0])
    if "gh_jid" in qs and qs["gh_jid"][0].isdigit():  # company careers page embedding Greenhouse
        return AtsRef("greenhouse", None, qs["gh_jid"][0])

    if host == "jobs.lever.co":
        m = re.match(rf"^/([^/]+)/({_UUID})", path, re.I)
        if m:
            return AtsRef("lever", m.group(1), m.group(2).lower())

    if host == "jobs.ashbyhq.com":
        m = re.match(rf"^/([^/]+)/({_UUID})", path, re.I)
        if m:
            return AtsRef("ashby", m.group(1), m.group(2).lower())

    m = re.match(r"^([a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com$", host)
    if m:
        tenant = m.group(1)
        parts = [x for x in path.split("/") if x]
        if parts and re.fullmatch(r"[a-z]{2}-[A-Z]{2}", parts[0]):
            parts = parts[1:]  # language prefix, e.g. en-US
        if len(parts) >= 3 and "job" in parts:
            site = parts[0]
            req = re.search(r"_((?:JR|R|REQ)?-?[A-Za-z]*\d[\w-]*)$", parts[-1])
            job_id = f"{tenant}:{req.group(1) if req else parts[-1]}"
            return AtsRef("workday", f"{tenant}/{site}", job_id)
    return None


def canonical_apply_url(ats: str, board: str | None, job_id: str | None, fallback: str) -> str:
    """The ATS-hosted application page apply/ knows how to fill."""
    if not (board and job_id):
        return fallback
    if ats == "greenhouse":
        return f"https://job-boards.greenhouse.io/{board}/jobs/{job_id}"
    if ats == "lever":
        return f"https://jobs.lever.co/{board}/{job_id}/apply"
    if ats == "ashby":
        return f"https://jobs.ashbyhq.com/{board}/{job_id}/application"
    return fallback  # Workday and others: the posting link itself
