"""Run every source, merge duplicates, and fill in missing descriptions.

Step by step:
1. For each company in config/companies.yaml, call its ATS API (Greenhouse,
   Lever, or Ashby). A failing board is logged and skipped, never fatal.
2. Pull the two community lists (SimplifyJobs README, zshah101 CSV).
3. De-duplicate. Two postings are the same job if they share
   (a) the same ATS + ATS job id, or
   (b) the same normalized (company, title, link), or
   (c) the same link outright (one URL is one posting, even if two lists word
       the title differently).
   When merging we keep the richer copy (the one with a description) and
   union the sources and flags.
4. `enrich_descriptions` (called later, only for jobs that survived the filter)
   fetches full descriptions from the public ATS APIs for community-list jobs,
   so the matcher has real text to compare against your resume.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field

from ..config import load_companies
from ..http import PoliteClient
from ..models import Job
from ..text import norm_company, norm_link, norm_title
from .ashby import fetch_ashby, parse_ashby, API as ASHBY_API
from .greenhouse import fetch_greenhouse, fetch_greenhouse_description
from .lever import fetch_lever, fetch_lever_description
from .simplify import fetch_simplify
from .zshah import fetch_zshah

log = logging.getLogger(__name__)

FETCHERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby}


@dataclass
class DiscoveryStats:
    per_source: Counter = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)
    simplify_url: str = ""
    total_raw: int = 0
    total_unique: int = 0


def _keys(job: Job) -> list[tuple]:
    keys = [("id", job.id), ("link", norm_company(job.company), norm_title(job.title), norm_link(job.url))]
    if job.ats != "other" and job.ats_job_id:
        keys.insert(0, ("ats", job.ats, job.ats_job_id))
    return keys


def _merge(a: Job, b: Job) -> Job:
    """Keep the copy with the description (API copy), fold in the other's extras."""
    rich, other = (a, b) if len(a.description) >= len(b.description) else (b, a)
    merged = rich.model_copy(deep=True)
    merged.sources = sorted(set(a.sources) | set(b.sources))
    merged.source_flags = sorted(set(a.source_flags) | set(b.source_flags))
    merged.skills_hint = sorted(set(a.skills_hint) | set(b.skills_hint))
    merged.season = rich.season or other.season
    merged.category = rich.category or other.category
    merged.ats_board = rich.ats_board or other.ats_board
    if not merged.location:
        merged.location = other.location
    return merged


def dedupe(jobs: list[Job]) -> list[Job]:
    out: list[Job] = []
    index: dict[tuple, int] = {}
    for job in jobs:
        hit = next((index[k] for k in _keys(job) if k in index), None)
        if hit is None:
            out.append(job)
            pos = len(out) - 1
        else:
            out[hit] = _merge(out[hit], job)
            pos = hit
        for k in _keys(out[pos]):
            index[k] = pos
    return out


def discover_all(client: PoliteClient, include_community: bool = True) -> tuple[list[Job], DiscoveryStats]:
    stats = DiscoveryStats()
    jobs: list[Job] = []
    cfg = load_companies()
    for ats, entries in (cfg.get("boards") or {}).items():
        for entry in entries or []:
            slug, name = (entry, None) if isinstance(entry, str) else (entry["slug"], entry.get("name"))
            try:
                found = FETCHERS[ats](client, slug, name)
                stats.per_source[f"{ats}:{slug}"] = len(found)
                jobs.extend(found)
            except Exception as e:  # one bad board must not sink the run
                stats.errors.append(f"{ats}:{slug}: {e}")
                log.warning("board %s:%s failed: %s", ats, slug, e)
    if include_community:
        try:
            found, url = fetch_simplify(client)
            stats.simplify_url = url
            stats.per_source["simplify"] = len(found)
            jobs.extend(found)
        except Exception as e:
            stats.errors.append(f"simplify: {e}")
        try:
            found = fetch_zshah(client)
            stats.per_source["zshah"] = len(found)
            jobs.extend(found)
        except Exception as e:
            stats.errors.append(f"zshah: {e}")
    stats.total_raw = len(jobs)
    unique = dedupe(jobs)
    stats.total_unique = len(unique)
    return unique, stats


API_ATS = ("greenhouse", "lever", "ashby")  # ATSs with a public description API


def enrich_descriptions(client: PoliteClient, jobs: list[Job], limit: int = 400) -> int:
    """Fetch descriptions for ATS-hosted jobs that lack one. Returns how many were filled."""
    filled = 0
    ashby_boards: dict[str, dict[str, str]] = {}
    todo = [j for j in jobs if not j.has_description and j.ats in API_ATS and j.ats_board and j.ats_job_id]
    for job in todo[:limit]:
        try:
            if job.ats == "greenhouse":
                job.description = fetch_greenhouse_description(client, job.ats_board, job.ats_job_id)
            elif job.ats == "lever":
                job.description = fetch_lever_description(client, job.ats_board, job.ats_job_id)
            elif job.ats == "ashby":
                if job.ats_board not in ashby_boards:
                    data = client.get_json(ASHBY_API.format(name=job.ats_board))
                    ashby_boards[job.ats_board] = {
                        j.ats_job_id: j.description for j in parse_ashby(job.ats_board, data)
                    }
                job.description = ashby_boards[job.ats_board].get(job.ats_job_id, "")
            filled += bool(job.description)
        except Exception as e:  # posting may have closed since the list was updated
            log.info("enrich %s failed: %s", job.id, e)
    return filled
