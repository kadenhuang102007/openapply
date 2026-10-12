"""Fill a Greenhouse, Lever, or Ashby application form, then hand it to you.

Step by step:
1. Launch a visible Chromium window (Playwright) and open the ATS-hosted
   application page for the job.
2. Look for CAPTCHA widgets (reCAPTCHA / hCaptcha / Turnstile). If present we
   note it; we never interact with them. You solve it, if asked.
3. Read every visible form field and work out its human label (the <label>,
   aria-label, placeholder, or nearby legend text).
4. Match labels to your profile with plain rules:
   first/last/full/preferred name, email, phone, LinkedIn, GitHub, website,
   school, location, graduation, and the resume file input (the PDF for the
   resume variant the matcher picked). For School and Location dropdowns we type
   the value and pick the first suggestion ONLY if its text clearly matches;
   otherwise we clear it and leave it for you. Long-answer boxes are matched to the
   drafted answers from `openapply tailor` by word overlap with the question.
5. NEVER filled: selects, radios, and checkboxes; anything about work
   authorization, sponsorship, visas, citizenship, gender, race, ethnicity,
   veteran or disability status; passwords. These are logged as "left for you".
6. Print and save a log of what was filled and what wasn't, then STOP. The
   browser stays open until you press Enter in the terminal. You review every
   field and click Submit yourself. The only click anywhere in this module is
   on a dropdown option, through `_safe_click_option`, which refuses buttons,
   links, inputs, and anything labelled submit/apply/next/continue. We never
   press Enter (that can submit a form implicitly).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..config import GENERATED_DIR
from ..models import Job

# Labels we must never fill automatically.
SENSITIVE = re.compile(
    r"authori[sz]|sponsor|\bvisa|citizen|immigration|gender|\bsex\b|pronoun|\brace\b|ethnic|hispanic|latin[oax]|"
    r"veteran|disab|accommodat|criminal|conviction|salary expectation|ssn|social security|date of birth|"
    r"birth|\bage\b|password|religio|sexual orientation|lgbt|transgender|eeo|demographic",
    re.I,
)

# label regex -> profile key. Order matters: first match wins.
FIELD_RULES: list[tuple[str, str]] = [
    (r"preferred (first )?name|nickname", "preferred_name"),
    (r"first\s*name|given name|^first$", "first_name"),
    (r"last\s*name|surname|family name|^last$", "last_name"),
    (r"^(full\s*)?name\b|your name|legal name|^name\s*\*?$", "full_name"),
    (r"e-?mail", "email"),
    (r"phone|mobile|cell", "phone"),
    (r"linkedin", "linkedin"),
    (r"github", "github"),
    (r"website|portfolio|personal (site|url)|other (link|url)", "website"),
    (r"^(school|university|college)( name)?\b|which (school|university)|current school", "school"),
    (r"graduation (date|year)|expected graduation|grad(uation)? (year|date)|when do you graduate", "graduation"),
    (r"^(major|field of study|discipline|area of study)", "majors"),
    (r"^degree\b", "degree"),
    (r"current location|^location\b|^city\b|where are you (currently )?(located|based)", "location"),
]

_COLLECT_JS = r"""
() => {
  const out = [];
  const els = Array.from(document.querySelectorAll('input, textarea, select'));
  let idx = 0;
  for (const el of els) {
    const type = (el.getAttribute('type') || el.tagName).toLowerCase();
    if (['hidden','submit','button','image','reset'].includes(type)) continue;
    const rect = el.getBoundingClientRect();
    const visible = !!(rect.width || rect.height || el.getClientRects().length);
    if (!visible && type !== 'file') continue;
    // hidden validation proxies (e.g. behind react-select dropdowns) are not real fields
    if (type !== 'file' && (el.tabIndex < 0 || el.getAttribute('aria-hidden') === 'true' ||
        parseFloat(getComputedStyle(el).opacity) === 0)) continue;
    let label = '';
    if (el.id) {
      const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (l) label = l.innerText;
    }
    if (!label) { const l = el.closest('label'); if (l) label = l.innerText; }
    if (!label && el.getAttribute('aria-labelledby')) {
      label = el.getAttribute('aria-labelledby').split(/\s+/)
        .map(id => (document.getElementById(id) || {}).innerText || '').join(' ');
    }
    if (!label) label = el.getAttribute('aria-label') || '';
    if (!label) {
      const fs = el.closest('fieldset'); const lg = fs && fs.querySelector('legend');
      if (lg) label = lg.innerText;
    }
    if (!label) {
      const wrap = el.closest('div');
      const lab = wrap && wrap.parentElement && wrap.parentElement.querySelector('label');
      if (lab) label = lab.innerText;
    }
    if (!label) label = el.getAttribute('placeholder') || '';
    const key = 'oa' + (idx++);
    el.setAttribute('data-openapply', key);
    out.push({
      key, type, tag: el.tagName.toLowerCase(),
      name: el.getAttribute('name') || '', id: el.id || '',
      label: (label || '').replace(/\s+/g, ' ').trim().slice(0, 300),
      required: el.required || el.getAttribute('aria-required') === 'true' || /\*\s*$/.test(label || ''),
      value: (type === 'file') ? '' : (el.value || ''),
      role: el.getAttribute('role') || '',
    });
  }
  return out;
}
"""

_CAPTCHA_JS = r"""
() => !!document.querySelector(
  'iframe[src*="recaptcha"], iframe[src*="hcaptcha"], iframe[src*="turnstile"], .g-recaptcha, .h-captcha, .cf-turnstile'
)
"""


# Dropdown (combobox) fields we may type into and pick an option for, only when the
# first suggested option clearly matches what we typed.
TYPEAHEAD_KEYS = {"school", "location"}
_FORBIDDEN_CLICK = re.compile(r"submit|apply|send|finish|complete|next|continue|save", re.I)


def _safe_click_option(option) -> None:
    """Click a dropdown option. Refuses anything that looks like a button or Submit."""
    info = option.evaluate(
        "e => [e.tagName, (e.getAttribute('type')||''), (e.getAttribute('role')||''), (e.innerText||'')]"
    )
    tag, typ, role, text = info
    if tag in ("BUTTON", "INPUT", "A", "FORM") or typ == "submit" or role == "button" or (
        role != "option" and _FORBIDDEN_CLICK.search(text)
    ):
        raise RuntimeError(f"refusing to click {tag} {text[:40]!r}: openapply never clicks buttons")
    option.click()


def _option_matches(typed: str, option_text: str) -> bool:
    """Every significant word we meant (minus 'of', 'the', 2-letter state codes) is in the option."""
    want = [w for w in re.findall(r"[a-z]+", typed.lower()) if len(w) > 2 and w not in ("the", "and")]
    have = option_text.lower()
    return bool(want) and all(w in have for w in want)


def _typeahead(page, loc, value: str) -> str | None:
    """Type into a dropdown, pick the matching suggestion, or clear it and return None.

    Tries the full value, then its last word (e.g. 'Berkeley'), since some school
    lists only match short queries. Options are read from this dropdown's own
    listbox (aria-controls), never from other menus on the page.
    """
    words = re.findall(r"[A-Za-z]+", value)
    queries = [value] + ([words[-1]] if len(words) > 1 and len(words[-1]) > 2 else [])
    for q in queries:
        loc.fill("")
        loc.press_sequentially(q, delay=25)  # real keystrokes; react-select ignores .fill()
        page.wait_for_timeout(2500)
        listbox = loc.get_attribute("aria-controls")
        scope = f"#{listbox} " if listbox else ".select__menu "
        options = page.locator(f'{scope}[role="option"], {scope}.select__option')
        for i in range(min(options.count(), 25)):
            text = options.nth(i).inner_text().strip()
            if _option_matches(value, text):
                _safe_click_option(options.nth(i))
                return text
    loc.fill("")
    return None


@dataclass
class FieldLog:
    label: str
    kind: str
    action: str  # filled | uploaded | skipped
    detail: str = ""
    required: bool = False


@dataclass
class FillReport:
    job_id: str
    url: str
    captcha_present: bool = False
    filled: list[FieldLog] = field(default_factory=list)
    not_filled: list[FieldLog] = field(default_factory=list)
    error: str = ""

    def save(self) -> Path:
        out = GENERATED_DIR / self.job_id
        out.mkdir(parents=True, exist_ok=True)
        path = out / "fill_log.json"
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        return path


def profile_values(profile: dict) -> dict[str, str]:
    name = profile.get("name") or {}
    majors = profile.get("majors") or []
    first, last = name.get("first", ""), name.get("last", "")
    return {
        "first_name": first,
        "last_name": last,
        "preferred_name": name.get("preferred") or first,
        "full_name": f"{first} {last}".strip(),
        "email": profile.get("email", ""),
        "phone": str(profile.get("phone", "")),
        "linkedin": profile.get("linkedin", ""),
        "github": profile.get("github", ""),
        "website": profile.get("website", ""),
        "school": profile.get("school", ""),
        "graduation": str(profile.get("graduation", "")),
        "majors": " & ".join(majors) if isinstance(majors, list) else str(majors),
        "degree": profile.get("degree", ""),
        "location": profile.get("location", ""),
    }


def classify_field(label: str, name: str = "") -> str | None:
    """Which profile key a text field wants, or None. Sensitive labels -> None."""
    text = f"{label} {name}".strip()
    if SENSITIVE.search(text):
        return None
    for pattern, key in FIELD_RULES:
        if re.search(pattern, label.strip(), re.I) or (name and re.search(pattern, name.replace("_", " "), re.I)):
            return key
    return None


def _words(s: str) -> set[str]:
    stop = {"the", "a", "an", "to", "of", "and", "or", "you", "your", "in", "at", "for", "is", "are", "this",
            "why", "what", "how", "do", "we", "us", "our", "with", "on", "please", "tell", "about"}
    return {w for w in re.findall(r"[a-z]+", s.lower()) if w not in stop and len(w) > 2}


def match_answer(label: str, answers: dict[str, str], cover_letter: str | None) -> tuple[str, str] | None:
    """Pick the drafted answer whose question best overlaps this textarea's label."""
    if re.search(r"cover letter", label, re.I) and cover_letter:
        return "cover letter", cover_letter
    lw = _words(label)
    best, best_score = None, 0.0
    for q, a in answers.items():
        qw = _words(q)
        if not qw or not lw:
            continue
        score = len(lw & qw) / min(len(lw), len(qw))
        if score > best_score:
            best, best_score = (q, a), score
    return best if best_score >= 0.5 else None


def fill_application(
    job: Job,
    profile: dict,
    resume_pdf: Path | None,
    answers: dict[str, str] | None = None,
    cover_letter: str | None = None,
    close_after: bool = False,
    headless: bool = False,
    wait_ms: int = 4000,
) -> FillReport:
    from playwright.sync_api import sync_playwright

    answers = answers or {}
    values = profile_values(profile)
    report = FillReport(job.id, job.url)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        try:
            page.goto(job.url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(wait_ms)  # let React-based forms render
            report.captcha_present = bool(page.evaluate(_CAPTCHA_JS))
            fields = page.evaluate(_COLLECT_JS)
            resume_done = False
            for f in fields:
                loc = page.locator(f'[data-openapply="{f["key"]}"]')
                label = f["label"] or f["name"] or f["id"]
                kind = f["type"]
                log = FieldLog(label=label, kind=kind, action="skipped", required=bool(f["required"]))
                if kind == "file":
                    is_resume = re.search(r"resume|cv", f"{label} {f['name']} {f['id']}", re.I)
                    if is_resume and not resume_done and resume_pdf and resume_pdf.exists():
                        loc.set_input_files(str(resume_pdf))
                        resume_done = True
                        log.action, log.detail = "uploaded", resume_pdf.name
                    else:
                        log.detail = "file upload left for you"
                elif f["role"] == "combobox" and not SENSITIVE.search(label) and \
                        classify_field(label, f["name"]) in TYPEAHEAD_KEYS and values.get(classify_field(label, f["name"])):
                    value = values[classify_field(label, f["name"])]
                    picked = _typeahead(page, loc, value)
                    if picked:
                        log.action, log.detail = "filled", f"picked dropdown option {picked!r}"
                    else:
                        log.detail = f"dropdown: no option clearly matched {value!r}; left for you"
                elif f["tag"] == "select" or kind in ("radio", "checkbox") or f["role"] == "combobox":
                    log.detail = "choice field: left for you"
                elif kind == "password":
                    log.detail = "password: never filled"
                elif SENSITIVE.search(label):
                    log.detail = "sensitive (authorization/demographic): left for you"
                elif f["value"]:
                    log.detail = "already had a value"
                elif f["tag"] == "textarea":
                    hit = match_answer(label, answers, cover_letter)
                    if hit:
                        loc.fill(hit[1])
                        log.action, log.detail = "filled", f"drafted answer: {hit[0][:60]}"
                    else:
                        log.detail = "no matching drafted answer"
                else:
                    key = classify_field(label, f["name"])
                    value = values.get(key or "", "")
                    if key and value:
                        loc.fill(value)
                        log.action, log.detail = "filled", key
                    elif key:
                        log.detail = f"no `{key}` in your profile.yaml"
                    else:
                        hit = match_answer(label, answers, None) if len(label) > 25 else None
                        if hit:
                            loc.fill(hit[1])
                            log.action, log.detail = "filled", f"drafted answer: {hit[0][:60]}"
                        else:
                            log.detail = "unrecognized field"
                (report.filled if log.action != "skipped" else report.not_filled).append(log)
        except Exception as e:
            report.error = f"{type(e).__name__}: {e}"
        report.save()
        if close_after:
            browser.close()
            return report
        _print_report(report)
        print("\n>>> Review every field in the browser window and click Submit YOURSELF.")
        print(">>> openapply will not submit. Press Enter here when you're done to close the browser.")
        try:
            input()
        except EOFError:
            pass
        browser.close()
    return report


def _print_report(report: FillReport) -> None:
    print(f"\nFilled {len(report.filled)} field(s); left {len(report.not_filled)} for you.")
    if report.captcha_present:
        print("CAPTCHA detected: openapply did not touch it. Complete it yourself if prompted.")
    for f in report.filled:
        print(f"  [filled] {f.label[:70]}  <- {f.detail}")
    for f in report.not_filled:
        req = " (REQUIRED)" if f.required else ""
        print(f"  [yours]  {f.label[:70]}{req}  - {f.detail}")
    if report.error:
        print(f"Error: {report.error}")
