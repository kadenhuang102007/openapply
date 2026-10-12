"""The fabrication checker flags numbers and names not in the sources."""

from openapply.tailor.fabrication import check_fabrication

RESUME = ("Grew user community to 30,000+ at vly.ai. Generated 100,000 impressions. "
          "Built Python pipelines over 122K snapshots with Professor Terrance Odean at Haas.")
TBD = RESUME + " Analyzed 300+ posts in Python."


def test_clean_draft_passes():
    draft = "At vly.ai I grew the community to 30,000+. I built Python pipelines over 122K snapshots at Haas."
    rep = check_fabrication(draft, [RESUME])
    assert rep.clean and not rep.unverified


def test_number_format_equivalence():
    rep = check_fabrication("My posts drew 100K impressions.", [RESUME])
    assert rep.clean  # 100K == 100,000


def test_invented_number_and_company_flagged():
    draft = "I grew revenue by 45% at Stripe and led a team of 12."
    rep = check_fabrication(draft, [RESUME])
    assert "45%" in rep.unknown_numbers and "12" in rep.unknown_numbers
    assert "Stripe" in rep.unknown_names
    assert not rep.clean


def test_job_posting_names_are_allowed():
    draft = "I'm excited about the Data Science Intern role at Figma."
    rep = check_fabrication(draft, [RESUME, "Figma", "Data Science Intern"])
    assert rep.clean


def test_tbd_only_facts_are_unverified():
    draft = "I analyzed 300+ posts in Python."
    rep = check_fabrication(draft, [RESUME], [TBD])
    assert rep.clean and rep.unverified == ["300+"]
