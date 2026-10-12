"""Draft a cover letter and answers to common application questions.

Two modes, same guarantee (never invent experience):

Extractive (default, no API key needed)
1. Rank your resume bullets by TF-IDF similarity to the posting.
2. The cover letter is a short template filled ONLY with your profile basics and
   your top 3 bullets, verbatim.
3. Each question gets the 2 most relevant bullets plus a clearly marked
   [YOUR WORDS: ...] slot for the part only you can write (e.g. "why us").

Claude (with --llm and ANTHROPIC_API_KEY)
1. The prompt gives Claude your resume, profile basics, the posting, and the
   questions, with hard rules: reuse only facts from the resume; no new numbers,
   employers, tools, or outcomes; say less rather than guess.
2. Output is structured JSON (cover letter + answers).

Either way, every draft then goes through fabrication.check_fabrication, and the
report is saved next to the drafts in generated/<job_id>/.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from ..config import GENERATED_DIR
from ..match.resume import Resume
from ..match.skills import find_skills
from ..models import Job
from .fabrication import FabricationReport, check_fabrication

DEFAULT_QUESTIONS = [
    "Why are you interested in this role at {company}?",
    "Tell us about a project you're proud of.",
    "Describe a time you used data to make a decision.",
]

MODEL = "claude-opus-5-5"

SYSTEM = """You help a student draft internship application materials.
HARD RULES (breaking any of these makes the draft unusable):
1. Use ONLY facts stated in the <resume> and <profile>. You may reorder, select, and emphasize them.
2. Never add a number, metric, employer, school, tool, technology, award, or outcome that is not in the resume.
3. Never claim familiarity with the company beyond what the <posting> itself says.
4. If a question asks for something the resume doesn't support, write a brief honest answer and add
   "[YOUR WORDS: ...]" describing what the student should add personally.
5. Plain, specific, first-person voice. No filler like "I am thrilled". Cover letter under 220 words;
   each answer under 120 words."""


class DraftAnswer(BaseModel):
    question: str
    answer: str


class DraftOutput(BaseModel):
    cover_letter: str
    answers: list[DraftAnswer]


@dataclass
class Drafts:
    cover_letter: str
    answers: list[tuple[str, str]]
    report: FabricationReport
    mode: str
    files: list[Path] = field(default_factory=list)

    def answers_dict(self) -> dict[str, str]:
        return dict(self.answers)


def rank_bullets(job: Job, bullets: list[str], top: int = 3, extra_query: str = "") -> list[str]:
    """Most relevant resume bullets: TF-IDF similarity plus a bonus per shared skill."""
    bullets = [b for b in bullets if not re.match(r"^\w+\s*:", b)]  # skip "Coursework: ..." lines
    if not bullets:
        return []
    query = f"{job.title} {job.title} {job.description} {extra_query}"
    vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True)
    m = vec.fit_transform([query] + bullets)
    sims = cosine_similarity(m[0], m[1:]).ravel()
    job_skills = find_skills(query)
    bonus = [0.08 * len(find_skills(b) & job_skills) for b in bullets]
    order = sorted(range(len(bullets)), key=lambda i: -(sims[i] + bonus[i]))
    return [bullets[i] for i in order[:top]]


def _basics(profile: dict) -> dict:
    name = profile.get("name") or {}
    majors = profile.get("majors") or []
    return {
        "full_name": f"{name.get('first', '')} {name.get('last', '')}".strip(),
        "school": profile.get("school", ""),
        "majors": " & ".join(majors) if isinstance(majors, list) else str(majors),
        "graduation": profile.get("graduation", ""),
        "email": profile.get("email", ""),
    }


def extractive_drafts(job: Job, resume: Resume, profile: dict, questions: list[str]) -> tuple[str, list[tuple[str, str]]]:
    b = _basics(profile)
    top = rank_bullets(job, resume.bullets, 3)
    lines = [
        f"Dear {job.company} hiring team,",
        "",
        f"I'm applying for the {job.title} position. I study {b['majors']} at {b['school']} "
        f"(graduating {b['graduation']}). A few things from my resume that line up with this role:",
        "",
        *[f"- {x}" for x in top],
        "",
        f"[YOUR WORDS: one or two sentences on why {job.company} specifically.]",
        "",
        "Thank you for your time and consideration.",
        "",
        b["full_name"],
    ]
    answers = []
    for q in questions:
        q = q.format(company=job.company, title=job.title)
        picks = rank_bullets(job, resume.bullets, 2, extra_query=q)
        body = " ".join(b.rstrip(".") + "." for b in picks)
        answers.append((q, f"{body}\n\n[YOUR WORDS: connect this to the question in your own voice.]"))
    return "\n".join(lines), answers


def llm_drafts(job: Job, resume: Resume, profile: dict, questions: list[str], client=None) -> tuple[str, list[tuple[str, str]]]:
    import anthropic

    client = client or anthropic.Anthropic()
    qs = [q.format(company=job.company, title=job.title) for q in questions]
    prompt = (
        f"<resume>\n{resume.text}\n</resume>\n\n<profile>\n{json.dumps(_basics(profile))}\n</profile>\n\n"
        f"<posting company=\"{job.company}\" title=\"{job.title}\">\n{job.description}\n</posting>\n\n"
        "<questions>\n" + "\n".join(f"- {q}" for q in qs) + "\n</questions>\n\n"
        "Write the cover letter and answer each question, following the hard rules."
    )
    resp = client.beta.messages.parse(
        model=MODEL,
        max_tokens=8000,
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        output_config={"effort": "medium"},
        output_format=DraftOutput,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        raise RuntimeError("Claude declined or returned no draft; use the extractive mode instead.")
    out = resp.parsed_output
    return out.cover_letter, [(a.question, a.answer) for a in out.answers]


def make_drafts(job: Job, resume: Resume, profile: dict, use_llm: bool = False, write: bool = True) -> Drafts:
    questions = profile.get("questions") or DEFAULT_QUESTIONS
    if use_llm:
        letter, answers = llm_drafts(job, resume, profile, questions)
        mode = f"claude ({MODEL})"
    else:
        letter, answers = extractive_drafts(job, resume, profile, questions)
        mode = "extractive"
    draft_text = letter + "\n" + "\n".join(a for _, a in answers)
    draft_text = re.sub(r"\[YOUR WORDS:[^\]]*\]", "", draft_text)  # placeholders aren't claims
    sources = [resume.verified_text, json.dumps(profile, default=str), job.company, job.title,
               job.description, job.location, *questions]
    report = check_fabrication(draft_text, sources, [resume.text])
    drafts = Drafts(letter, answers, report, mode)
    if write:
        out = GENERATED_DIR / job.id
        out.mkdir(parents=True, exist_ok=True)
        (out / "cover_letter.md").write_text(letter + "\n", encoding="utf-8")
        (out / "answers.md").write_text(
            "\n\n".join(f"## {q}\n\n{a}" for q, a in answers) + "\n", encoding="utf-8")
        (out / "fabrication_check.txt").write_text(f"mode: {mode}\n{report.summary()}\n", encoding="utf-8")
        (out / "answers.json").write_text(json.dumps(drafts.answers_dict(), indent=2), encoding="utf-8")
        drafts.files = [out / "cover_letter.md", out / "answers.md", out / "fabrication_check.txt"]
    return drafts
