"""The end-to-end pipeline the CLI commands share.

discover_and_filter()
  1. start a run in the tracker
  2. discovery.run.discover_all  -> unique jobs from every source
  3. filter.apply_filters        -> keep / drop with a reason, families, flags,
                                    and the company tier (big tech, quant, ...).
                                    With `established_only: true` in your profile,
                                    unknown companies drop as "not an established company".
  4. tracker.upsert_job for every job (dropped ones too, so you can audit why)
  5. enrich descriptions for kept jobs that lack one (public ATS APIs only),
     skipping jobs whose description we already stored in an earlier run

rank()
  6. score every kept job from the latest run against your resume variants,
     startups included, so switching --established-only / --include-startups
     never needs a re-score; the company-tier note is appended to the explanation
  7. optionally rerank the top N with Claude (cached: a job is only sent once),
     within the view you asked for
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field

from .config import load_filters, load_profile
from .discovery.run import API_ATS, discover_all, enrich_descriptions
from .filter import NOT_ESTABLISHED, apply_filters, load_tiers
from .http import PoliteClient
from .match import llm as llm_mod
from .match.resume import load_resumes
from .match.scorer import score_jobs
from .models import Job
from .tracker.db import Tracker


@dataclass
class PipelineStats:
    run_id: int = 0
    per_source: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)
    simplify_url: str = ""
    fetched_raw: int = 0
    unique: int = 0
    kept: int = 0
    drop_reasons: dict = field(default_factory=dict)
    families: dict = field(default_factory=dict)
    flags: dict = field(default_factory=dict)
    enriched: int = 0
    established_only: bool = False
    kept_any_company: int = 0  # kept before the established-company check
    established: int = 0  # of those, how many are established companies
    tiers: dict = field(default_factory=dict)  # tier -> count among kept_any_company


def established_only(profile: dict) -> bool:
    """The profile switch `established_only: true` (default off)."""
    return str(profile.get("established_only", False)).strip().lower() in ("true", "yes", "y", "1")


def needs_sponsorship(profile: dict) -> bool:
    v = str((profile.get("work_authorization") or {}).get("needs_sponsorship_now_or_future", "")).strip().lower()
    return v in ("yes", "true", "y")


def discover_and_filter(tracker: Tracker, kind: str = "discover", enrich_limit: int = 400,
                        progress=print) -> PipelineStats:
    stats = PipelineStats()
    stats.run_id = tracker.start_run(kind)
    client = PoliteClient(min_delay=0.5)
    try:
        progress("Fetching job boards and community lists...")
        jobs, ds = discover_all(client)
        stats.per_source, stats.errors, stats.simplify_url = dict(ds.per_source), ds.errors, ds.simplify_url
        stats.fetched_raw, stats.unique = ds.total_raw, ds.total_unique

        cfg, profile = load_filters(), load_profile()
        sponsor = needs_sponsorship(profile)
        tiers, stats.established_only = load_tiers(), established_only(profile)
        kept: list[Job] = []
        reasons, fams, flags, tier_counts = Counter(), Counter(), Counter(), Counter()
        for job in jobs:
            res = apply_filters(job, cfg, needs_sponsorship=sponsor, tiers=tiers,
                                established_only=stats.established_only)
            if res.keep or res.reason == NOT_ESTABLISHED:
                stats.kept_any_company += 1
                tier_counts[res.tier.tier or "not established"] += 1
            # reuse a description stored in an earlier run
            if res.keep and not job.has_description:
                row = tracker.get(job.id)
                if row is not None:
                    old = tracker.job(row)
                    if old.has_description:
                        job.description = old.description
            tracker.upsert_job(job, stats.run_id, res.keep, res.reason, res.families, res.flags,
                               res.tier.tier, res.tier.note)
            if res.keep:
                kept.append(job)
                fams.update(res.families)
                flags.update(res.flags)
            else:
                reasons[res.reason] += 1
        tracker.commit()
        stats.kept = len(kept)
        stats.tiers = dict(tier_counts)
        stats.established = stats.kept_any_company - tier_counts.get("not established", 0)
        stats.drop_reasons, stats.families, stats.flags = dict(reasons), dict(fams), dict(flags)

        todo = [j for j in kept if not j.has_description and j.ats in API_ATS]
        if todo and enrich_limit:
            progress(f"Fetching full descriptions for {min(len(todo), enrich_limit)} kept jobs from public ATS APIs...")
            stats.enriched = enrich_descriptions(client, todo, limit=enrich_limit)
            for j in todo:
                if j.description:
                    tracker.update_description(j)
            tracker.commit()
    finally:
        client.close()
        tracker.finish_run(stats.run_id, stats.__dict__)
    return stats


def rank(tracker: Tracker, use_llm: bool = False, llm_top: int = 25, progress=print,
         established: bool | None = None) -> list:
    """Score all kept jobs; `established` only narrows which ones the Claude rerank looks at."""
    profile = load_profile()
    resumes = load_resumes(profile)
    if not resumes:
        raise RuntimeError("No resumes found. Configure `resumes:` in config/profile.yaml or add files to resumes/.")
    rows = tracker.filtered(established_only=False)
    jobs = [tracker.job(r) for r in rows]
    families = {r["id"]: json.loads(r["families"]) for r in rows}
    notes = {r["id"]: r["tier_note"] for r in rows}
    results = score_jobs(jobs, resumes, families)
    for m in results:
        explanation = m.explanation() + (f" | {notes[m.job_id]}" if notes.get(m.job_id) else "")
        tracker.set_score(m.job_id, m.score, m.variant, explanation)
    tracker.commit()
    if use_llm:
        if not llm_mod.available():
            progress("No ANTHROPIC_API_KEY found; skipping the Claude rerank (baseline scores kept).")
        else:
            top = tracker.ranked(llm_top, established_only=established)
            for r in top:
                if r["llm_score"] is not None:
                    continue
                res = llm_mod.llm_score(tracker.job(r), resumes[r["variant"]])
                if res:
                    tracker.set_llm(r["id"], res.score, res.reason)
            tracker.commit()
    return results
