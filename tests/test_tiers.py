"""Company tiers: alias-aware matching, the 🔥 / Workday signals, and the established-only filter."""

import pytest

from openapply.filter import NOT_ESTABLISHED, CompanyTiers, apply_filters, company_key, load_tiers
from openapply.models import Job
from openapply.tracker.db import Tracker


@pytest.fixture(scope="module")
def tiers():
    return load_tiers()


def job(company, url="https://job-boards.greenhouse.io/co/jobs/1", source_flags=(), title="Software Engineer Intern 2027",
        jid="gh-1"):
    return Job(id=jid, company=company, title=title, location="San Francisco, CA", url=url,
               source_flags=list(source_flags))


@pytest.mark.parametrize("name, canonical, tier", [
    ("Alphabet Inc.", "Google", "big_tech"),
    ("Google LLC", "Google", "big_tech"),
    ("Meta Platforms, Inc.", "Meta", "big_tech"),
    ("Amazon Web Services (AWS)", "Amazon", "big_tech"),
    ("ByteDance", "ByteDance", "big_tech"),
    ("JPMorgan Chase & Co.", "JPMorgan Chase", "finance"),
    ("JPMorganChase", "JPMorgan Chase", "finance"),
    ("J.P. Morgan", "JPMorgan Chase", "finance"),
    ("BNY Mellon", "BNY", "finance"),
    ("D. E. Shaw & Co., L.P.", "D. E. Shaw", "quant"),
    ("IMC Trading", "IMC Trading", "quant"),
    ("Hudson River Trading", "Hudson River Trading", "quant"),
    ("The Home Depot", "The Home Depot", "enterprise"),
    ("Home Depot, Inc.", "The Home Depot", "enterprise"),
    ("Hewlett Packard (HP)", "HP", "top_tech"),
    ("Hewlett Packard Enterprise", "Hewlett Packard Enterprise", "top_tech"),
    ("The Cigna Group", "Cigna", "enterprise"),
    ("General Dynamics Information Technology", "General Dynamics", "enterprise"),
    ("Ernst & Young", "EY", "enterprise"),
    ("AT&T Inc.", "AT&T", "enterprise"),
    ("S&P Global", "S&P Global", "finance"),
    ("anduril industries", "Anduril", "top_tech"),
    ("Lowe’s", "Lowe's", "enterprise"),  # curly apostrophe
])
def test_alias_matching(tiers, name, canonical, tier):
    assert tiers.match(name) == (canonical, tier)


@pytest.mark.parametrize("name", [
    "Metadata Inc",          # starts with "Meta" but is not Meta
    "Sigma Computing",       # not Two Sigma
    "Cadence Solutions",     # not Cadence Design Systems
    "Googly Eyes LLC",
    "Interplay",
    "",
])
def test_no_false_matches(tiers, name):
    assert tiers.match(name) is None


def test_company_key_normalization():
    assert company_key("JPMorgan Chase & Co.") == company_key("JPMorganChase") == "jpmorganchase"
    assert company_key("The Walt Disney Company") == "waltdisney"
    assert company_key("Nestlé S.A.") == company_key("Nestle")
    # one-word names are never stripped down to nothing
    assert company_key("Group") == "group"


def test_alias_collision_is_rejected():
    data = {"tiers": {"a": {"companies": [{"Foo": ["Bar Inc"]}]}, "b": {"companies": ["Bar"]}}}
    with pytest.raises(ValueError):
        CompanyTiers(data)


def test_signals(tiers):
    google = tiers.classify(job("Google", source_flags=["faang_plus"]))
    assert google.tier == "big_tech" and "SimplifyJobs 🔥 FAANG+" in google.note

    hot_unknown = tiers.classify(job("Some Unlisted Co", source_flags=["faang_plus"]))
    assert hot_unknown.established and hot_unknown.tier == "faang_plus"

    workday_only = tiers.classify(job("Regional Widgets", url="https://widgets.wd5.myworkdayjobs.com/en-US/careers/job/1"))
    assert not workday_only.established
    assert "workday" in workday_only.signals and "weak signal" in workday_only.note
    assert workday_only.note.startswith("not on the established-company list")


def test_established_only_filter(filters_cfg, tiers):
    big = apply_filters(job("Datadog"), filters_cfg, tiers=tiers, established_only=True)
    assert big.keep and big.tier.label == "top tech"

    small = apply_filters(job("Tiny Seed Startup"), filters_cfg, tiers=tiers, established_only=True)
    assert not small.keep and small.reason == NOT_ESTABLISHED and small.families == ["swe"]

    # off by default: the tier is still recorded, but nothing is dropped for it
    loose = apply_filters(job("Tiny Seed Startup"), filters_cfg, tiers=tiers)
    assert loose.keep and not loose.tier.established

    # other drop reasons win over the company check
    senior = apply_filters(job("Google", title="Senior Software Engineer"), filters_cfg, tiers=tiers, established_only=True)
    assert senior.reason == "not an internship"


def test_faang_plus_is_not_a_warning_flag(filters_cfg, tiers):
    r = apply_filters(job("AMD", source_flags=["faang_plus"]), filters_cfg, tiers=tiers)
    assert "faang_plus" not in r.flags


def test_tracker_views(tmp_path, filters_cfg, tiers):
    """Discovery ran with established_only on; the CLI can still show startups, and vice versa."""
    t = Tracker(tmp_path / "t.db")
    run = t.start_run("discover")
    for i, company in enumerate(["Google", "Tiny Seed Startup"]):
        j = job(company, jid=f"gh-{i}")
        r = apply_filters(j, filters_cfg, tiers=tiers, established_only=True)
        t.upsert_job(j, run, r.keep, r.reason, r.families, r.flags, r.tier.tier, r.tier.note)
    t.commit()
    names = lambda rows: sorted(r["company"] for r in rows)  # noqa: E731
    assert names(t.filtered()) == ["Google"]
    assert names(t.filtered(established_only=True)) == ["Google"]
    assert names(t.filtered(established_only=False)) == ["Google", "Tiny Seed Startup"]
    assert names(t.new_in_run(run, established_only=True)) == ["Google"]
    assert t.get("gh-1")["filter_reason"] == NOT_ESTABLISHED


def test_simplify_fire_marker(fixture_text):
    from openapply.discovery.simplify import parse_simplify_readme

    jobs = parse_simplify_readme(fixture_text("simplify_readme.md"))
    tiktok = [j for j in jobs if j.company == "TikTok"]
    assert len(tiktok) == 2 and all("faang_plus" in j.source_flags for j in tiktok)  # ↳ row inherits 🔥
    assert not any("faang_plus" in j.source_flags for j in jobs if j.company == "Electronic Arts")
