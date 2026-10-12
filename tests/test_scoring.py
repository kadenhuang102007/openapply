"""Scoring: LaTeX stripping, skill detection, ranking order, variant choice."""

from pathlib import Path

from openapply.match.resume import load_resume, strip_latex
from openapply.match.scorer import score_jobs
from openapply.match.skills import find_skills
from openapply.models import Job

TEX = r"""
\documentclass{article}
\begin{document}
\section{Experience}
\resumeSubheading{Acme Analytics}{2026}{Data Intern}{}
\resumeItemListStart
  \resumeItem{Built \textbf{Python/pandas} pipelines over \textbf{122K} rows and fit regression models in scikit-learn}
  \resumeItem{\tbd{Shipped a React dashboard used by \textbf{40} analysts}}
\resumeItemListEnd
\section{Skills}
Python, SQL, R, pandas % a comment
\href{https://example.com}{\underline{portfolio}} 16\% lift
\end{document}
"""


def test_strip_latex():
    text = strip_latex(TEX)
    assert "\\" not in text and "{" not in text
    assert "Acme Analytics" in text and "122K" in text and "16% lift" in text
    assert "a comment" not in text and "example.com" not in text and "portfolio" in text


def test_resume_tbd_split(tmp_path: Path):
    p = tmp_path / "r.tex"
    p.write_text(TEX)
    r = load_resume("swe", p, families=["swe"])
    assert len(r.bullets) == 1 and len(r.tbd_bullets) == 1
    assert "React dashboard" in r.text and "React dashboard" not in r.verified_text


def test_find_skills():
    s = find_skills("Experience with Python, SQL and PyTorch; familiarity with A/B testing. JavaScript a plus.")
    assert {"Python", "SQL", "PyTorch", "A/B testing", "JavaScript"} <= s
    assert "Java" not in s  # JavaScript must not count as Java


def _job(i, title, desc):
    return Job(id=f"j{i}", company="Co", title=title, url=f"https://x/{i}", description=desc * 3)


def test_ranking_order(tmp_path: Path):
    p = tmp_path / "r.tex"
    p.write_text(TEX)
    resumes = {"swe": load_resume("swe", p, families=["swe", "ds"])}
    data = _job(1, "Data Science Intern", "We use Python, pandas, SQL and regression models with scikit-learn "
                                          "to analyze data pipelines and build dashboards for analysts. ")
    sales = _job(2, "Sales Intern", "Cold call prospects, manage CRM in Salesforce, negotiate contracts, "
                                    "travel to client sites and hit quarterly quota targets. ")
    other = _job(3, "Marketing Intern", "Plan events, write copy, coordinate vendors, manage a budget spreadsheet "
                                        "and support the brand team with campaign logistics. ")
    res = {m.job_id: m for m in score_jobs([data, sales, other], resumes, {})}
    assert res["j1"].score > res["j2"].score
    assert {"Python", "pandas", "SQL"} <= set(res["j1"].matched)
    assert "matched:" in res["j1"].explanation()


def test_variant_choice_by_family(tmp_path: Path):
    p = tmp_path / "r.tex"
    p.write_text(TEX)
    resumes = {
        "pm": load_resume("pm", p, families=["pm"]),
        "swe": load_resume("swe", p, families=["swe"]),
    }
    j = _job(1, "Product Manager Intern", "Own the roadmap and work with engineers. " * 20)
    out = score_jobs([j], resumes, {"j1": ["pm"]})
    assert out[0].variant == "pm"


def test_thin_description_flagged(tmp_path: Path):
    p = tmp_path / "r.tex"
    p.write_text(TEX)
    resumes = {"swe": load_resume("swe", p)}
    thin = Job(id="t", company="Co", title="Software Engineer Intern", url="https://x", skills_hint=["Python"])
    out = score_jobs([thin], resumes, {})
    assert out[0].thin and "thin description" in out[0].explanation()
