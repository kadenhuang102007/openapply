"""The one data shape every part of openapply agrees on: `Job`.

Step by step:
1. Every source (Greenhouse, Lever, Ashby, SimplifyJobs, zshah101's list) has its
   own JSON or table format. Each parser translates its format into `Job`.
2. A `Job` knows which applicant-tracking system (ATS) hosts it and that system's
   own job id, when we can tell. That id is the strongest de-duplication key,
   because Greenhouse ids, Lever UUIDs and Ashby UUIDs are globally unique.
3. `Job.id` is a short, typeable id for the CLI, e.g. `gh-8015792003` or
   `ab-81f09568`.
"""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, Field

ATS = Literal["greenhouse", "lever", "ashby", "other"]

_PREFIX = {"greenhouse": "gh", "lever": "lv", "ashby": "ab"}


def make_job_id(ats: str, ats_job_id: str | None, url: str) -> str:
    """Build the short CLI id. Known ATS -> prefix + native id; otherwise a URL hash."""
    if ats in _PREFIX and ats_job_id:
        native = ats_job_id if ats == "greenhouse" else ats_job_id.split("-")[0]
        return f"{_PREFIX[ats]}-{native}"
    return "x-" + hashlib.sha1(url.encode()).hexdigest()[:10]


class Job(BaseModel):
    """One job posting, normalized."""

    id: str
    company: str
    title: str
    location: str = ""
    url: str  # best link to the posting / application
    ats: ATS = "other"
    ats_board: str | None = None  # e.g. Greenhouse board token "figma"
    ats_job_id: str | None = None
    description: str = ""  # plain text
    sources: list[str] = Field(default_factory=list)  # which feeds reported it
    season: str | None = None  # source's own season label, e.g. "Summer 2027"
    category: str | None = None  # source's own category, e.g. "Product Management"
    source_flags: list[str] = Field(default_factory=list)  # e.g. "no_sponsorship" from an emoji
    skills_hint: list[str] = Field(default_factory=list)  # skill tags a source already extracted
    posted_at: str | None = None
    closed: bool = False

    @property
    def has_description(self) -> bool:
        return len(self.description) >= 300
