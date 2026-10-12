"""The fabrication checker: did a draft say something your resume doesn't?

A draft may only REORDER and EMPHASIZE what is already true. This module enforces
that mechanically, as a backstop to the prompt rules:

1. Numbers. Every number in the draft (100K, 30,000+, 99%, $5M, 1,994) is parsed
   to a value (100K -> 100000) and must appear somewhere in the sources
   (resume, profile, job posting, the question itself).
2. Names. Every Capitalized word that isn't the first word of a sentence, and
   isn't a common word on the allowlist, must appear in the sources
   (case-insensitive). This catches invented companies, schools, tools, people.
3. Unverified. If a number or name appears ONLY inside a \\tbd{...} draft bullet
   of your resume, it is reported separately: true-to-the-file, but not yet
   confirmed by you.
The checker flags; it doesn't rewrite. You read the flags before you submit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_NUM = re.compile(r"\$?\d[\d,]*(?:\.\d+)?\s*(?:%|[kKmM]\b|\+)?")
_WORD = re.compile(r"[A-Z][A-Za-z0-9&.+\-']*[A-Za-z0-9+]|[A-Z]")

COMMON = {
    "I", "I'm", "I've", "I'd", "I'll", "Dear", "Sincerely", "Best", "Regards", "Thank", "Thanks", "Hiring",
    "Team", "Manager", "Intern", "Internship", "Summer", "Fall", "Spring", "Winter", "January", "February",
    "March", "April", "May", "June", "July", "August", "September", "October", "November", "December",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "AI", "ML", "US", "U.S.", "Product", "Software",
    "Engineer", "Engineering", "Data", "Science", "Scientist", "Machine", "Learning", "Program", "Role",
    "B.S.", "BS", "Bachelor", "Bachelor's", "University", "Economics", "Python", "SQL", "PM", "SWE", "OK",
    "Mr", "Ms", "Mrs", "Dr", "Hello", "Hi", "Yours", "Warm", "Warmly", "Cheers",
}


def _value(token: str) -> float | None:
    t = token.replace("$", "").replace(",", "").replace("+", "").replace("%", "").strip()
    mult = 1.0
    if t[-1:].lower() == "k":
        mult, t = 1e3, t[:-1]
    elif t[-1:].lower() == "m":
        mult, t = 1e6, t[:-1]
    try:
        return round(float(t.strip()) * mult, 4)
    except ValueError:
        return None


def numbers_in(text: str) -> set[float]:
    vals = set()
    for tok in _NUM.findall(text or ""):
        v = _value(tok)
        if v is not None:
            vals.add(v)
    # ranges like "16--24%" / "16-24%" contribute both ends via the regex already
    return vals


def _candidate_names(text: str) -> list[str]:
    names = []
    for sentence in re.split(r"(?<=[.!?:])\s+|\n+", text):
        words = list(_WORD.finditer(sentence))
        for m in words:
            w = m.group(0).rstrip(".'")
            if m.start() == 0 or sentence[: m.start()].strip() in ("-", "*", "•", '"', "("):
                continue  # sentence-initial capital
            if w in COMMON or len(w) < 2:
                continue
            names.append(w)
    return names


@dataclass
class FabricationReport:
    unknown_numbers: list[str] = field(default_factory=list)
    unknown_names: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)  # only supported by \tbd draft bullets

    @property
    def clean(self) -> bool:
        return not (self.unknown_numbers or self.unknown_names)

    def summary(self) -> str:
        if self.clean and not self.unverified:
            return "PASS: every number and name traces back to your resume, profile, or the posting."
        lines = []
        if self.unknown_numbers:
            lines.append("NOT IN SOURCES (numbers): " + ", ".join(self.unknown_numbers))
        if self.unknown_names:
            lines.append("NOT IN SOURCES (names): " + ", ".join(self.unknown_names))
        if self.unverified:
            lines.append("ONLY IN \\tbd DRAFT BULLETS (confirm before sending): " + ", ".join(self.unverified))
        return "\n".join(lines)


def check_fabrication(draft: str, verified_sources: list[str], unverified_sources: list[str] | None = None) -> FabricationReport:
    verified = "\n".join(verified_sources)
    unverified = "\n".join(unverified_sources or [])
    v_nums, u_nums = numbers_in(verified), numbers_in(unverified)
    v_low, u_low = verified.lower(), unverified.lower()
    rep = FabricationReport()
    seen = set()
    for tok in _NUM.findall(draft):
        v = _value(tok)
        if v is None or tok in seen:
            continue
        seen.add(tok)
        if v in v_nums:
            continue
        (rep.unverified if v in u_nums else rep.unknown_numbers).append(tok.strip())
    for name in _candidate_names(draft):
        if name in seen:
            continue
        seen.add(name)
        if name.lower() in v_low:
            continue
        (rep.unverified if name.lower() in u_low else rep.unknown_names).append(name)
    return rep
