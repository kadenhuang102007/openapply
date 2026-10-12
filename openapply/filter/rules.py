"""Filter rules, driven by config/filters.yaml.

For each job we answer five questions, in this order:
1. Is it an internship?             (title matches internship_pattern)
2. Is it in a wanted role family?   (PM / SWE / DS / ML title patterns)
3. Is it excluded?                  (PhD, MBA, hardware, ...)
4. Is it the right season?          (Summer 2027; wrong years/terms in the title drop it)
5. Is it in the US?                 (any listed location is in the US, or Remote)
Jobs that pass get `flags` for language you should read before applying:
no_sponsorship, us_citizenship, export_control, season_unconfirmed, ...
Flags never drop a job unless your profile says you need sponsorship.
6. Is it an established company?    (only when `established_only` is on; see tiers.py)
   Every job gets its company tier recorded either way, so `openapply rank
   --include-startups` / `--established-only` can switch views without
   re-running discovery. A job dropped here keeps its families and flags, and
   its reason is "not an established company".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..models import Job
from .tiers import FAANG_PLUS, NOT_ESTABLISHED, CompanyTiers, TierMatch

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS",
    "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY",
    "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC",
}
STATE_NAMES = (
    "alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|georgia|hawaii|"
    "idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|michigan|"
    "minnesota|mississippi|missouri|montana|nebraska|nevada|new hampshire|new jersey|new mexico|"
    "new york|north carolina|north dakota|ohio|oklahoma|oregon|pennsylvania|rhode island|"
    "south carolina|south dakota|tennessee|texas|utah|vermont|virginia|washington|west virginia|"
    "wisconsin|wyoming"
)
US_CITIES = (
    "sf|nyc|san francisco|new york|seattle|boston|austin|chicago|los angeles|bay area|"
    "silicon valley|palo alto|mountain view|menlo park|sunnyvale|san jose|san mateo|redwood city|"
    "cupertino|santa clara|berkeley|oakland|san diego|denver|boulder|atlanta|miami|dallas|houston|"
    "pittsburgh|philadelphia|portland|salt lake city|washington dc|washington, d\\.?c|"
    "arlington|reston|costa mesa|irvine|culver city|brooklyn|cambridge, ma|raleigh|durham|"
    "nashville|detroit|minneapolis|phoenix|columbus|princeton|stamford|jersey city"
)
_US = re.compile(
    rf"\b(united states|usa|u\.s\.a?\.?|us|{STATE_NAMES}|{US_CITIES})\b", re.I
)
_STATE_ABBR = re.compile(r"(?:,|\s)\s*([A-Z]{2})\b")
_NON_US = re.compile(
    r"\b(canada|ontario|british columbia|quebec|toronto|vancouver|montreal|waterloo|ottawa|calgary|"
    r"united kingdom|uk|england|london|ireland|dublin|india|bangalore|bengaluru|hyderabad|"
    r"germany|berlin|munich|france|paris|netherlands|amsterdam|spain|madrid|poland|warsaw|"
    r"israel|tel aviv|singapore|japan|tokyo|china|beijing|shanghai|hong kong|taiwan|korea|seoul|"
    r"australia|sydney|melbourne|mexico|brazil|sao paulo|argentina|switzerland|zurich|sweden|"
    r"stockholm|denmark|copenhagen|emea|apac|latam|europe|romania|serbia|portugal|lisbon|italy|"
    r"czech|prague|austria|vienna|belgium|brussels|norway|oslo|finland|helsinki|greece|athens|turkey|istanbul|"
    r"czech|prague|ukraine|philippines|vietnam|indonesia|uae|dubai|south africa|nigeria|kenya)\b",
    re.I,
)
_SPLIT = re.compile(r"\s*(?:;|•|\||/| or |\n)\s*")


def is_us_location(location: str) -> bool | None:
    """True if any listed place is in the US, False if all are abroad, None if unknown."""
    if not location or not location.strip():
        return None
    parts = [p for p in _SPLIT.split(location) if p.strip()]
    saw_remote_only = False
    saw_abroad = False
    for part in parts:
        if _NON_US.search(part):
            saw_abroad = True
            continue
        if _US.search(part):
            return True
        if any(m in US_STATES for m in _STATE_ABBR.findall(part)):
            return True
        if re.search(r"\bremote\b|\banywhere\b", part, re.I):
            saw_remote_only = True
    if saw_remote_only:
        # "Vienna, Austria ; Remote" means remote from Austria, not from the US
        return False if saw_abroad else None  # remote with no country named
    return False


def classify_families(title: str, families: dict[str, str]) -> list[str]:
    return [name for name, pat in families.items() if re.search(pat, title, re.I)]


@dataclass
class FilterResult:
    keep: bool
    reason: str = ""
    families: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    tier: TierMatch | None = None  # set when a tier list was passed in


def _season_check(job: Job, cfg: dict) -> tuple[bool, str, bool]:
    """Returns (ok, reason, confirmed)."""
    year = str(cfg.get("year", 2027))
    term = cfg.get("term", "summer").lower()
    title = job.title.lower()
    years_in_title = set(re.findall(r"\b(20\d\d)\b", title))
    if years_in_title and year not in years_in_title:
        return False, f"title says {'/'.join(sorted(years_in_title))}", False
    if re.search(cfg.get("wrong_term_pattern", r"$^"), title) and term not in title:
        return False, "off-cycle term in title", False
    if job.season and year in job.season and term in job.season.lower():
        return True, "", True
    if year in years_in_title:
        return True, "", True
    if re.search(rf"{term}\s*(of\s*)?{year}|{year}\s*{term}", job.description, re.I):
        return True, "", True
    return True, "", False


def apply_filters(job: Job, cfg: dict, needs_sponsorship: bool = False,
                  tiers: CompanyTiers | None = None, established_only: bool = False) -> FilterResult:
    res = _base_filters(job, cfg, needs_sponsorship)
    if tiers is None:
        return res
    res.tier = tiers.classify(job)
    if res.keep and established_only and not res.tier.established:
        res.keep, res.reason = False, NOT_ESTABLISHED
    return res


def _base_filters(job: Job, cfg: dict, needs_sponsorship: bool) -> FilterResult:
    """Questions 1-5 plus the warning flags."""
    title = job.title
    if job.closed:
        return FilterResult(False, "closed")
    if not re.search(cfg["internship_pattern"], title, re.I):
        return FilterResult(False, "not an internship")
    families = [f for f in classify_families(title, cfg["role_families"]) if f in cfg["wanted_families"]]
    if not families:
        return FilterResult(False, "not a wanted role family")
    if cfg.get("exclude_title") and re.search(cfg["exclude_title"], title, re.I):
        return FilterResult(False, "excluded title keyword", families)
    if "advanced_degree" in job.source_flags:
        return FilterResult(False, "advanced degree required", families)

    ok, why, confirmed = _season_check(job, cfg.get("season", {}))
    if not ok:
        return FilterResult(False, why, families)
    flags = [] if confirmed else ["season_unconfirmed"]
    if not confirmed and cfg.get("season", {}).get("require_confirmed"):
        return FilterResult(False, "season unconfirmed", families)

    us = is_us_location(job.location)
    loc_cfg = cfg.get("location", {})
    if us is False:
        return FilterResult(False, "outside the US", families)
    if us is None:
        if "remote" in job.location.lower():
            if not loc_cfg.get("allow_remote", True):
                return FilterResult(False, "remote, country unknown", families)
            flags.append("remote_country_unknown")
        else:
            if not loc_cfg.get("allow_unknown", True):
                return FilterResult(False, "no location", families)
            flags.append("location_unknown")

    text = f"{title}\n{job.description}"
    for flag, pat in (cfg.get("flags") or {}).items():
        if re.search(pat, text, re.I):
            flags.append(flag)
    for flag in job.source_flags:
        if flag != FAANG_PLUS and flag not in flags:  # 🔥 is a company signal, not a warning
            flags.append(flag)
    if needs_sponsorship and ({"no_sponsorship", "us_citizenship"} & set(flags)):
        return FilterResult(False, "no sponsorship / citizens only", families, flags)
    return FilterResult(True, "", families, flags)
