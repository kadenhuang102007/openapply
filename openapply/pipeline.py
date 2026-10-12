"""The end-to-end pipeline the CLI commands share.

discover_and_filter()
  1. start a run in the tracker
  2. discovery.run.discover_all  -> unique jobs from every source
  3. filter.apply_filters        -> keep / drop with a reason, families, flags
  4. tracker.upsert_job for every job (dropped ones too, so you can audit why)
  5. enrich descriptions for kept jobs that lack one (public ATS APIs only),
     skipping jobs whose description we already stored in an earlier run

rank()
  6. score every kept job from the latest run against your resume variants
  7. optionally rerank the top N with Claude (cached: a job is only sent once)
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field

from .config import load_filters, load_profile
from .discovery.run import discover_all, enrich_descriptions
from .filter import apply_filters
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
        kept: list[Job] = []
        reasons, fams, flags = Counter(), Counter(), Counter()
        for job in jobs:
            res = apply_filters(job, cfg, needs_sponsorship=sponsor)
            # reuse a description stored in an earlier run
            if res.keep and not job.has_description:
                row = tracker.get(job.id)
                if row is not None:
                    old = tracker.job(row)
                    if old.has_description:
                        job.description = old.description
            tracker.upsert_job(job, stats.run_id, res.keep, res.reason, res.families, res.flags)
            if res.keep:
                kept.append(job)
                fams.update(res.families)
                flags.update(res.flags)
            else:
                reasons[res.reason] += 1
        tracker.commit()
        stats.kept = len(kept)
        stats.drop_reasons, stats.families, stats.flags = dict(reasons), dict(fams), dict(flags)

        todo = [j for j in kept if not j.has_description and j.ats != "other"]
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


def rank(tracker: Tracker, use_llm: bool = False, llm_top: int = 25, progress=print) -> list:
    profile = load_profile()
    resumes = load_resumes(profile)
    if not resumes:
        raise RuntimeError("No resumes found. Configure `resumes:` in config/profile.yaml or add files to resumes/.")
    rows = tracker.filtered()
    jobs = [tracker.job(r) for r in rows]
    families = {r["id"]: json.loads(r["families"]) for r in rows}
    results = score_jobs(jobs, resumes, families)
    for m in results:
        tracker.set_score(m.job_id, m.score, m.variant, m.explanation())
    tracker.commit()
    if use_llm:
        if not llm_mod.available():
            progress("No ANTHROPIC_API_KEY found; skipping the Claude rerank (baseline scores kept).")
        else:
            top = tracker.ranked(llm_top)
            for r in top:
                if r["llm_score"] is not None:
                    continue
                res = llm_mod.llm_score(tracker.job(r), resumes[r["variant"]])
                if res:
                    tracker.set_llm(r["id"], res.score, res.reason)
            tracker.commit()
    return results
