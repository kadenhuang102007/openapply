"""Company tiers: is this posting from an established company, and which kind?

Step by step:
1. `load_tiers` reads config/company_tiers.yaml. Each tier (big_tech, top_tech,
   quant, finance, enterprise) lists companies as a canonical name plus aliases.
2. Every name and alias is turned into a matching key by `company_key`:
   lowercase, accents removed, "&" read as "and", a leading "The" and trailing
   legal words (Inc, LLC, Corp, Co, Holdings, Group, ...) dropped, then every
   space and punctuation mark removed. "JPMorgan Chase & Co." and
   "JPMorganChase" both become "jpmorganchase". Two companies sharing a key is
   a config mistake, so the loader refuses it.
3. `CompanyTiers.match(name)` looks the key up exactly (no fuzzy guessing, so
   "Meta" can never match "Metadata"). If the name has a "(...)" part, e.g.
   "Hewlett Packard (HP)", the name without it and the part inside are tried too.
4. `CompanyTiers.classify(job)` adds two signals from the posting itself:
   - SimplifyJobs marks FAANG+ rows with 🔥. The parser records that as the
     source flag `faang_plus`. It counts as established on its own, because
     SimplifyJobs curates that marker by hand.
   - A myworkdayjobs.com link means the company pays for Workday, which small
     startups rarely do. That is a WEAK signal: it is written into the
     explanation, but never makes a company established by itself.
5. The result is a `TierMatch`: the tier (or None), the canonical company name,
   and a one-line plain-English note saying why.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from ..config import CONFIG_DIR, load_yaml
from ..models import Job

FAANG_PLUS = "faang_plus"  # source flag set by the SimplifyJobs parser for 🔥 rows
FAANG_PLUS_TIER = "faang_plus"
FAANG_PLUS_LABEL = "FAANG+"
NOT_ESTABLISHED = "not an established company"

_LEGAL_SUFFIXES = {
    "inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation", "co", "company",
    "plc", "holdings", "holding", "group", "lp", "llp", "sa", "ag", "nv", "se", "gmbh", "and",
}
_PARENS = re.compile(r"\(([^)]*)\)")


def company_key(name: str) -> str:
    """Normalize a company name into the key used for matching (see the module docstring)."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " and ").replace("+", " and ")
    words = re.findall(r"[a-z0-9]+", s.replace(".", ""))  # "J.P." -> "jp", "Amazon.com" -> "amazoncom"
    if words and words[0] == "the":
        words = words[1:]
    while len(words) > 1 and words[-1] in _LEGAL_SUFFIXES:
        words = words[:-1]
    return "".join(words)


def _candidate_keys(name: str) -> list[str]:
    """The name itself, the name without any (...) part, and each (...) part alone."""
    keys = [company_key(name)]
    if "(" in name:
        keys.append(company_key(_PARENS.sub(" ", name)))
        keys.extend(company_key(inner) for inner in _PARENS.findall(name))
    return [k for k in dict.fromkeys(keys) if k]


@dataclass
class TierMatch:
    tier: str | None = None  # e.g. "big_tech"; None = not established
    label: str = ""  # e.g. "big tech", shown in `openapply rank`
    company: str | None = None  # canonical name from the tier list
    signals: list[str] = field(default_factory=list)  # "list", "faang_plus", "workday"

    @property
    def established(self) -> bool:
        return self.tier is not None

    @property
    def note(self) -> str:
        """One line for the explanation, e.g. 'established: Google (big tech); SimplifyJobs 🔥 FAANG+'."""
        parts = []
        if self.company:
            parts.append(f"established: {self.company} ({self.label})")
        if FAANG_PLUS in self.signals:
            parts.append("SimplifyJobs 🔥 FAANG+" + ("" if self.company else " (counts as established)"))
        if "workday" in self.signals:
            parts.append("Workday-hosted (weak signal, not counted on its own)")
        if not self.established:
            parts.insert(0, "not on the established-company list")
        return "; ".join(parts)


class CompanyTiers:
    """The tier list from config/company_tiers.yaml, indexed by matching key."""

    def __init__(self, data: dict):
        self.labels: dict[str, str] = {}
        self.index: dict[str, tuple[str, str]] = {}  # key -> (canonical name, tier)
        for tier, spec in (data.get("tiers") or {}).items():
            self.labels[tier] = spec.get("label", tier.replace("_", " "))
            for entry in spec.get("companies") or []:
                if isinstance(entry, str):
                    name, aliases = entry, []
                else:
                    (name, aliases), = entry.items()
                    aliases = aliases or []
                for alias in [name, *aliases]:
                    key = company_key(str(alias))
                    prev = self.index.get(key)
                    if prev and prev[0] != name:
                        raise ValueError(f"company_tiers.yaml: {alias!r} under {name!r} collides with {prev[0]!r}")
                    self.index[key] = (name, tier)
        self.labels[FAANG_PLUS_TIER] = FAANG_PLUS_LABEL

    @classmethod
    def from_file(cls, path: Path) -> "CompanyTiers":
        return cls(load_yaml(path))

    def match(self, company: str) -> tuple[str, str] | None:
        """(canonical name, tier) for a company name, or None if it's not on the list."""
        for key in _candidate_keys(company):
            if key in self.index:
                return self.index[key]
        return None

    def classify(self, job: Job) -> TierMatch:
        signals = []
        hit = self.match(job.company)
        if hit:
            signals.append("list")
        if FAANG_PLUS in job.source_flags:
            signals.append(FAANG_PLUS)
        if "myworkdayjobs.com" in job.url.lower():
            signals.append("workday")
        if hit:
            name, tier = hit
            return TierMatch(tier, self.labels[tier], name, signals)
        if FAANG_PLUS in signals:
            return TierMatch(FAANG_PLUS_TIER, FAANG_PLUS_LABEL, None, signals)
        return TierMatch(None, "", None, signals)  # Workday alone is never enough


@lru_cache(maxsize=1)
def load_tiers() -> CompanyTiers:
    return CompanyTiers.from_file(CONFIG_DIR / "company_tiers.yaml")
