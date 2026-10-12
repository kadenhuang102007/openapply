"""Baseline matcher: an explainable 0-100 score for every job.

Step by step, for each job and each resume variant:
1. Skill coverage. Find the vocabulary skills the job mentions (title,
   description, and any skill tags the source gave us). Coverage is the share of
   those skills your resume also mentions, smoothed so a job naming 1 skill
   doesn't swing to 0 or 100: (matched + 1) / (job_skills + 2).
2. Text similarity. TF-IDF (unigrams + bigrams, English stop words removed) is
   fit on all jobs plus your resumes. Raw cosine similarity is small and depends
   on how much boilerplate a posting has, so we convert it to a percentile among
   today's jobs that have a real description (1.0 = most similar posting).
3. Score = 100 * (0.5 * coverage + 0.5 * similarity percentile).
   Coverage is smoothed as (matched + 1) / (job_skills + 3) so a posting that
   names only 2-3 skills can't look like a perfect match.
4. Resume variant. If the job's role family (PM / SWE / DS / ML) is listed under a
   variant's `families`, that variant is used; otherwise the higher-scoring one.
5. Explanation: matched skills, missing skills, and a "thin description" note
   when the job has little text (e.g. Workday links we can't fetch). Those jobs
   get a below-median similarity (0.35) and a 10% confidence haircut, because we
   only saw the title and a few tags; open them to judge for yourself.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from ..models import Job
from .resume import Resume
from .skills import find_skills, normalize_hints

COVERAGE_WEIGHT = 0.5
SIMILARITY_WEIGHT = 0.5
THIN_SIMILARITY = 0.35
THIN_HAIRCUT = 0.9


@dataclass
class MatchResult:
    job_id: str
    score: int
    variant: str
    matched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    similarity: float = 0.0
    thin: bool = False

    def explanation(self) -> str:
        parts = []
        if self.matched:
            parts.append("matched: " + ", ".join(self.matched))
        if self.missing:
            parts.append("missing: " + ", ".join(self.missing))
        if self.thin:
            parts.append("thin description (title/tags only)")
        return "; ".join(parts) or "no vocabulary skills found in posting"


def job_document(job: Job) -> str:
    return f"{job.title}\n{job.title}\n{job.description}"  # title twice: it's the strongest signal


def _pick_variant(families: list[str], resumes: dict[str, Resume], scores: dict[str, float]) -> str:
    for fam in families:  # first family wins: PM before SWE etc. follows config order
        owners = [name for name, r in resumes.items() if fam in r.families]
        if owners:
            return max(owners, key=lambda n: scores[n])
    return max(scores, key=scores.get)


def score_jobs(jobs: list[Job], resumes: dict[str, Resume], families: dict[str, list[str]]) -> list[MatchResult]:
    if not jobs or not resumes:
        return []
    names = list(resumes)
    docs = [job_document(j) for j in jobs]
    vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True, min_df=1, max_df=0.9)
    matrix = vec.fit_transform(docs + [resumes[n].text for n in names])
    job_m, res_m = matrix[: len(jobs)], matrix[len(jobs) :]
    sims = cosine_similarity(job_m, res_m)
    resume_skills = {n: find_skills(resumes[n].text) for n in names}
    # percentile of each job's cosine among jobs with real descriptions, per resume
    rich_idx = [i for i, j in enumerate(jobs) if j.has_description]
    pct = {}
    for k in range(len(names)):
        col = sorted(sims[i, k] for i in rich_idx)
        for i in rich_idx:
            pct[(i, k)] = bisect_right(col, sims[i, k]) / max(len(col), 1)

    results = []
    for i, job in enumerate(jobs):
        job_skills = find_skills(job_document(job)) | normalize_hints(job.skills_hint)
        thin = not job.has_description
        per_variant: dict[str, tuple[float, set, float]] = {}
        for k, n in enumerate(names):
            matched = job_skills & resume_skills[n]
            coverage = (len(matched) + 1) / (len(job_skills) + 3)
            sim = THIN_SIMILARITY if thin else pct[(i, k)]
            total = COVERAGE_WEIGHT * coverage + SIMILARITY_WEIGHT * sim
            per_variant[n] = (total * (THIN_HAIRCUT if thin else 1.0), matched, sims[i, k])
        chosen = _pick_variant(families.get(job.id, []), resumes, {n: v[0] for n, v in per_variant.items()})
        total, matched, raw_sim = per_variant[chosen]
        results.append(
            MatchResult(
                job_id=job.id,
                score=round(100 * total),
                variant=chosen,
                matched=sorted(matched),
                missing=sorted(job_skills - matched),
                similarity=float(raw_sim),
                thin=thin,
            )
        )
    return results
