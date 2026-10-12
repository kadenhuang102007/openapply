"""Optional Claude rerank: a 0-100 fit score and a two-sentence reason.

Step by step:
1. Needs ANTHROPIC_API_KEY in .env (or any credential the Anthropic SDK can find).
   Without one, `available()` is False and openapply silently uses the baseline.
2. For each job we send your resume text and the posting, and ask for JSON
   matching `LLMScore` (structured outputs, so we never parse free text).
3. Model: claude-opus-5-5 at low effort (this is a quick judgment call), with
   server-side refusal fallbacks enabled so a rare refusal doesn't drop the job.
4. The prompt tells Claude to judge only from the given text and not to assume
   experience the resume doesn't state.
"""

from __future__ import annotations

import os

from pydantic import BaseModel, Field

from ..models import Job
from .resume import Resume

MODEL = "claude-opus-5-5"

SYSTEM = (
    "You assess how well a student's resume fits an internship posting. "
    "Judge only from the resume and posting text provided. Do not assume skills or "
    "experience the resume does not state. Weigh: required skills present, relevance "
    "of past work, eligibility signals (class year, degree, location). "
    "Score 0-100 where 50 is a plausible but unremarkable fit and 80+ means the resume "
    "clearly matches most stated requirements. The reason must be exactly two sentences: "
    "the strongest match, then the biggest gap."
)


class LLMScore(BaseModel):
    score: int = Field(description="0-100 fit score")
    reason: str = Field(description="Exactly two sentences: strongest match, then biggest gap.")


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def llm_score(job: Job, resume: Resume, client=None) -> LLMScore | None:
    import anthropic

    client = client or anthropic.Anthropic()
    prompt = (
        f"<resume>\n{resume.text}\n</resume>\n\n"
        f"<posting company=\"{job.company}\" title=\"{job.title}\" location=\"{job.location}\">\n"
        f"{job.description or '(no description available; judge from the title)'}\n</posting>"
    )
    resp = client.beta.messages.parse(
        model=MODEL,
        max_tokens=2000,
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        output_config={"effort": "low"},
        output_format=LLMScore,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        return None
    out = resp.parsed_output
    out.score = max(0, min(100, out.score))
    return out
