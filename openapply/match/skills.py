"""A skills vocabulary for explainable matching.

Each canonical skill maps to a regex. `find_skills(text)` returns the set of
canonical skills a text mentions. The scorer compares the job's set with the
resume's set to say exactly which skills matched and which are missing.
Add to SKILLS freely; keep patterns specific enough not to fire on ordinary words.
"""

from __future__ import annotations

import re

SKILLS: dict[str, str] = {
    # languages
    "Python": r"\bpython\b",
    "SQL": r"\bsql\b|postgres|mysql|sqlite",
    "R": r"\bR\b(?! ?&)(?=[\s,;/)]|$)",
    "Java": r"\bjava\b(?!script)",
    "JavaScript": r"\bjavascript\b|\bjs\b",
    "TypeScript": r"\btypescript\b",
    "C++": r"c\+\+",
    "C#": r"c#|\.net\b",
    "Go": r"\bgolang\b|\bgo\b(?= ?(,|/|and|or|\)))",
    "Rust": r"\brust\b",
    "Scala": r"\bscala\b",
    "Kotlin": r"\bkotlin\b",
    "Swift": r"\bswift\b",
    "MATLAB": r"\bmatlab\b",
    # web / backend
    "React": r"\breact\b",
    "Next.js": r"next\.?js",
    "Node.js": r"node\.?js",
    "FastAPI": r"fastapi",
    "Django/Flask": r"\bdjango\b|\bflask\b",
    "REST APIs": r"\brestful\b|\brest apis?\b|\bapi design\b",
    "GraphQL": r"graphql",
    "PostgreSQL": r"postgres(ql)?|supabase",
    "Docker": r"\bdocker\b|container",
    "Kubernetes": r"kubernetes|\bk8s\b",
    "AWS": r"\baws\b|amazon web services",
    "GCP": r"\bgcp\b|google cloud",
    "Azure": r"\bazure\b",
    "Git": r"\bgit\b|github",
    "Linux": r"\blinux\b|\bunix\b",
    "Distributed systems": r"distributed system",
    "Testing": r"unit test|pytest|vitest|test automation|automated test|integration test",
    # data / ML
    "pandas": r"\bpandas\b",
    "NumPy": r"\bnumpy\b",
    "scikit-learn": r"scikit|sklearn",
    "PyTorch": r"pytorch|\btorch\b",
    "TensorFlow": r"tensorflow|keras",
    "Spark": r"\bspark\b|pyspark|databricks",
    "Machine learning": r"machine learning|\bml\b",
    "Deep learning": r"deep learning|neural net",
    "LLMs": r"\bllms?\b|large language model|generative ai|genai|\bgpt\b|anthropic|openai|claude",
    "NLP": r"\bnlp\b|natural language",
    "Computer vision": r"computer vision",
    "Statistics": r"statistic|probability|hypothesis test|bayesian",
    "Regression": r"regression",
    "Econometrics": r"econometric|causal inference|fixed[- ]effects|difference[- ]in[- ]differences|regression discontinuity",
    "A/B testing": r"a/b test|experimentation|experiment design|split test",
    "Data visualization": r"visuali[sz]ation|matplotlib|dashboards?|plotly|streamlit",
    "Tableau": r"tableau",
    "Excel/Sheets": r"\bexcel\b|spreadsheet|google sheets",
    "ETL/pipelines": r"\betl\b|data pipeline|pipelines",
    "Gradient boosting": r"lightgbm|xgboost|gradient[- ]boost",
    # product / business
    "Product management": r"product manag|product requirement|\bprd\b|roadmap",
    "User research": r"user research|customer (interview|research)|usability|user feedback|product feedback",
    "Product analytics": r"product analytics|conversion funnel|user retention|engagement (data|metrics)|\bkpis?\b",
    "Go-to-market": r"go[- ]to[- ]market|\bgtm\b|growth (marketing|team|strateg)|user acquisition",
    "Stakeholder communication": r"stakeholder|cross[- ]functional",
    "Content/marketing": r"content strateg|content marketing|social media|growth marketing|marketing (campaign|strateg|intern)|brand awareness",
    "Economics": r"\beconomics\b|economist",
    "Finance/markets": r"prediction market|trading|financial market|market microstructure",
}

_COMPILED = {name: re.compile(pat, re.I if name != "R" else 0) for name, pat in SKILLS.items()}


def find_skills(text: str) -> set[str]:
    return {name for name, rx in _COMPILED.items() if rx.search(text or "")}


def normalize_hints(hints: list[str]) -> set[str]:
    """Map a source's own skill tags (e.g. zshah's 'Pandas; LLMs') onto our vocabulary."""
    return find_skills(" , ".join(hints))
