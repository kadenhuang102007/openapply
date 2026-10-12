"""Workday: walk the multi-step application wizard, fill what we safely can, STOP at Review.

Workday (`*.myworkdayjobs.com`) is different from Greenhouse/Lever/Ashby: every
company has its own Workday account, and the application is a wizard of pages
(My Information -> My Experience -> Application Questions -> Voluntary
Disclosures -> Self Identify -> Review). Here is what openapply does.

Step by step:
1. Open a Chromium window with a PERSISTENT profile in data/browser-profile/
   (gitignored). Your Workday logins live there, so you sign in once per
   company and later runs are already signed in.
2. Open the job page and click "Apply". In the "Start Your Application" popup,
   pick "Apply Manually"; "Autofill with Resume" is used only if it's the only
   choice (then we upload your resume PDF on that page).
3. Work out where we are, using Workday's stable `data-automation-id`
   attributes and the active step title in the progress bar
   (`progressBarActiveStep`). See `classify_state` / `step_from_title`.
4. Sign-in / Create Account page: openapply prints a message and WAITS while you
   sign in or create the account yourself. It never fills the email/password
   boxes, never clicks "Sign In" or "Create Account", and never stores a
   password. It continues on its own once the wizard appears.
5. My Information: first/last name, email, phone (digits), phone type, address
   (line 1, city, state, postal code, country) from config/profile.yaml. "How
   did you hear about us?" is left alone unless your profile sets
   `how_did_you_hear`. Choice questions (e.g. "Have you worked here before?")
   come only from saved answers (answers.py).
6. My Experience: upload your resume PDF, then fill the repeating sections,
   clicking Workday's "Add" button once per entry: Work Experience from the
   profile's `experience:` list (title, company, location, dates, "I currently
   work here", description), Education (school, degree, field of study, years),
   and Websites (GitHub, personal site), plus the LinkedIn box.
7. Application Questions, Voluntary Disclosures, Self Identify (and any step we
   don't recognize): ONLY saved answers are used. Anything still empty is
   listed, and openapply pauses until you've filled it and pressed Enter.
8. Between steps it clicks "Save and Continue" (only a button whose text is
   exactly Save and Continue / Continue / Next). If Workday shows errors and
   stays on the same step, it pauses for you to fix them.
9. Review: openapply STOPS. It never clicks Submit: `_click_next` refuses the
   Review step and any button whose text isn't a plain "continue", and
   `_click` refuses anything labelled submit / sign in / create account /
   delete. The per-step log of what was filled and what was left is printed
   and saved to generated/<job_id>/fill_log.json.

Widgets: Workday dropdowns are buttons that open a listbox (`_pick_dropdown`);
multi-selects are search boxes where Workday only searches after Enter
(`_pick_multiselect` presses Enter inside that search box, the one place in
openapply that presses Enter); dates are separate month/year spin boxes
(`_fill_date`).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..config import DATA_DIR
from ..models import Job
from .answers import SENSITIVE, SavedAnswers, allowed, choose_many, choose_option, is_yes, why_not
from .filler import FieldLog, FillReport, _safe_click_option, classify_field, print_report, profile_values
from .session import LABEL_JS, Waiter, merge_capture

BROWSER_PROFILE = DATA_DIR / "browser-profile"

# Multiplies every wait in this module. Tests set it low; real Workday pages need the full time.
PATIENCE = 1.0


def _sleep(page, ms: int) -> None:
    page.wait_for_timeout(max(20, int(ms * PATIENCE)))

_WORKDAY_HOST = re.compile(r"(^|\.)myworkdayjobs\.com$|(^|\.)myworkdaysite\.com$|(^|\.)myworkday\.com$", re.I)


def is_workday_url(url: str) -> bool:
    from urllib.parse import urlparse

    return bool(url) and bool(_WORKDAY_HOST.search(urlparse(url.strip()).netloc.split(":")[0]))


# ----------------------------------------------------------------------------- where are we?

STEP_PATTERNS: list[tuple[str, str]] = [
    ("signin", r"sign ?in|create account|log ?in"),
    ("autofill_resume", r"autofill|quick apply|upload (your )?(resume|cv)"),
    ("my_information", r"my information|personal (information|details)|contact information"),
    ("my_experience", r"my experience|^experience"),
    ("self_identify", r"self[- ]?identif"),
    ("voluntary_disclosures", r"voluntary disclosure|disclosures"),
    ("application_questions", r"application questions|questions|questionnaire"),
    ("review", r"^review"),
]

PAGE_IDS = {
    "applyFlowMyInfoPage": "my_information",
    "applyFlowMyExpPage": "my_experience",
    "applyFlowPrimaryQuestionsPage": "application_questions",
    "applyFlowSecondaryQuestionsPage": "application_questions",
    "applyFlowVoluntaryDisclosuresPage": "voluntary_disclosures",
    "applyFlowSelfIdentifyPage": "self_identify",
    "applyFlowSelfIdPage": "self_identify",
    "applyFlowReviewPage": "review",
    "applyFlowAutoFillPage": "autofill_resume",
}

QUESTION_STEPS = {"application_questions", "voluntary_disclosures", "self_identify", "other"}

STATE_JS = r"""
() => {
  const vis = (el) => !!(el && el.getClientRects().length);
  const q = (s) => Array.from(document.querySelectorAll(s)).filter(vis);
  const act = q('[data-automation-id="progressBarActiveStep"]')[0];
  const lines = act ? act.innerText.split('\n').map(s => s.trim()).filter(Boolean) : [];
  const next = q('[data-automation-id="bottom-navigation-next-button"], [data-automation-id="pageFooterNextButton"]')[0];
  return {
    activeStep: lines.length ? lines[lines.length - 1] : '',
    pageIds: q('[data-automation-id^="applyFlow"]').map(e => e.getAttribute('data-automation-id')),
    applyButton: q('[data-automation-id="adventureButton"]').length > 0,
    options: ['applyManually', 'autofillWithResume', 'useMyLastApplication']
      .filter(id => q(`[data-automation-id="${id}"]`).length > 0),
    signin: q('[data-automation-id="signInContent"], [data-automation-id="signInFormo"], ' +
              '[data-automation-id="createAccountSubmitButton"], [data-automation-id="signInSubmitButton"], ' +
              'input[type="password"]').length > 0,
    headings: q('h2, h3').map(e => (e.innerText || '').trim()).filter(Boolean).slice(0, 8),
    next: next ? (next.innerText || '').trim() : '',
    errors: q('[data-automation-id="errorMessage"], [data-automation-id="errorBanner"]')
      .map(e => (e.innerText || '').trim()).filter(Boolean).slice(0, 10),
    url: location.href,
  };
}
"""


def step_from_title(title: str) -> str | None:
    t = (title or "").strip().lower()
    t = re.sub(r"^(current )?step \d+ of \d+\s*", "", t)
    for step, pattern in STEP_PATTERNS:
        if re.search(pattern, t):
            return step
    return None


def classify_state(info: dict) -> tuple[str, str]:
    """(state, step). state: job_page | apply_options | signin | wizard | unknown."""
    if info.get("signin"):
        return "signin", "signin"
    if info.get("activeStep"):
        step = step_from_title(info["activeStep"]) or "other"
        return ("signin", "signin") if step == "signin" else ("wizard", step)
    for pid in info.get("pageIds") or []:
        if pid in PAGE_IDS:
            return "wizard", PAGE_IDS[pid]
    if info.get("options"):
        return "apply_options", ""
    if "applyFlowPage" in (info.get("pageIds") or []):
        return "wizard", "other"
    if info.get("applyButton"):
        return "job_page", ""
    return "unknown", ""


def read_state(page) -> tuple[str, str, dict]:
    try:
        info = page.evaluate(STATE_JS)
    except Exception:  # navigating
        return "unknown", "", {}
    state, step = classify_state(info)
    return state, step, info


# ----------------------------------------------------------------------------- fields

WD_FIELDS_JS = "() => {" + LABEL_JS + r"""
  const vis = (el) => !!el.getClientRects().length;
  const root = document.querySelector('[data-automation-id="applyFlowPage"]') || document.body;
  const out = []; let idx = 0;
  const els = root.querySelectorAll('input, textarea, select, button[aria-haspopup="listbox"]');
  for (const el of els) {
    const aid = el.getAttribute('data-automation-id') || '';
    const type = (el.getAttribute('type') || el.tagName).toLowerCase();
    if (aid === 'beecatcher') continue;                 // Workday's honeypot box: never touch
    if (['hidden', 'submit', 'image', 'reset'].includes(type)) continue;
    if (el.tagName === 'INPUT' && type === 'button') continue;
    if (!vis(el) && type !== 'file' && type !== 'checkbox' && type !== 'radio') continue;
    if (el.closest('[data-automation-id="progressBar"]')) continue;
    if ((type === 'checkbox' || type === 'radio') && !vis(el) && !vis(el.parentElement)) continue;
    let kind;
    if (type === 'file') kind = 'file';
    else if (el.tagName === 'BUTTON') kind = 'dropdown';
    else if (aid === 'dateSectionMonth-input') kind = 'month';
    else if (aid === 'dateSectionYear-input') kind = 'year';
    else if (aid === 'dateSectionDay-input') kind = 'day';
    else if (aid === 'searchBox' || el.closest('[data-automation-id="multiselectInputContainer"]')) kind = 'multiselect';
    else if (type === 'radio' || type === 'checkbox') kind = type;
    else if (el.tagName === 'SELECT') kind = 'select';
    else if (el.tagName === 'TEXTAREA') kind = 'textarea';
    else if (type === 'password') kind = 'password';
    else kind = 'text';
    const ff = el.closest('[data-automation-id^="formField-"]');
    const ffName = ff ? ff.getAttribute('data-automation-id').slice('formField-'.length) : '';
    const id = el.id || '';
    let fname = ffName;
    if (!fname && id.includes('--')) fname = id.split('--').pop().replace(/-dateSection.*$/, '');
    if (!fname) fname = el.getAttribute('name') || aid || '';
    let group = '', panel = 0;
    let m = id.match(/^([A-Za-z]+)-(\d+)--/);
    if (m) { group = m[1]; panel = +m[2]; }
    if (!group) {
      for (let a = el.parentElement; a && a !== root; a = a.parentElement) {
        const v = a.getAttribute('data-automation-id') || '';
        const mm = v.match(/^([A-Za-z]+)-(\d+)$/);
        if (mm) { group = mm[1]; panel = +mm[2]; break; }
      }
    }
    if (!group) {
      const g = el.closest('[role="group"][aria-labelledby]');
      const mm = g && g.getAttribute('aria-labelledby').match(/^([A-Za-z-]+?)-(\d+)-panel$/);
      if (mm) { group = mm[1]; panel = +mm[2]; }
    }
    let value = '';
    if (kind === 'dropdown') { value = (el.innerText || '').trim(); if (/^select one$/i.test(value)) value = ''; }
    else if (kind === 'multiselect') {
      const box = ff || el.parentElement;
      value = Array.from(box.querySelectorAll('[data-automation-id="selectedItem"]'))
        .map(p => (p.innerText || '').trim()).filter(Boolean).join('; ');
    } else if (kind !== 'file' && kind !== 'radio' && kind !== 'checkbox') value = el.value || '';
    const labelEl = ff && ff.querySelector('label');
    const required = el.required || el.getAttribute('aria-required') === 'true' ||
      !!(ff && ff.querySelector('abbr[title="required"], [data-automation-id="requiredIndicator"]')) ||
      /\*/.test(labelEl ? labelEl.innerText : '');
    const key = 'wd' + (idx++);
    el.setAttribute('data-openapply', key);
    out.push({
      key, kind, aid, id, fname, group, panel,
      name: el.getAttribute('name') || '',
      label: OA_ownLabel(el) || OA_question(el),
      question: OA_question(el),
      option: (kind === 'radio' || kind === 'checkbox') ? OA_ownLabel(el) : '',
      options: el.tagName === 'SELECT' ? Array.from(el.options).map(o => o.text.trim()) : [],
      value, checked: !!el.checked, required,
    });
  }
  return out;
}
"""

ADD_BUTTON_JS = r"""
(headingSource) => {
  const re = new RegExp(headingSource, 'i');
  const vis = (el) => !!el.getClientRects().length;
  const isAdd = (b) => b.getAttribute('data-automation-id') === 'add-button' ||
    /^add( another)?\b/i.test((b.innerText || '').trim()) || /^add\b/i.test(b.getAttribute('aria-label') || '');
  for (const b of document.querySelectorAll('button')) {
    if (vis(b) && re.test((b.getAttribute('aria-label') || '').replace(/^add\s+(another\s+)?/i, ''))
        && isAdd(b)) { b.setAttribute('data-openapply-add', '1'); return true; }
  }
  for (const h of document.querySelectorAll('h2, h3, h4')) {
    if (!vis(h) || !re.test((h.innerText || '').trim())) continue;
    for (let a = h.parentElement, depth = 0; a && depth < 6; a = a.parentElement, depth++) {
      const btns = Array.from(a.querySelectorAll('button')).filter(b => vis(b) && isAdd(b));
      if (btns.length) { btns[btns.length - 1].setAttribute('data-openapply-add', '1'); return true; }
    }
  }
  return false;
}
"""

# fname (lowercased, last "--" segment) -> what it holds
WD_NAMES: dict[str, str] = {
    "firstname": "first_name", "lastname": "last_name", "middlename": "middle_name",
    "addressline1": "address_line1", "addressline2": "address_line2", "city": "city",
    "postalcode": "postal_code", "countryregion": "state", "country": "country",
    "phonetype": "phone_type", "phone-device-type": "phone_type",
    "phonenumber": "phone", "phone-number": "phone", "email": "email", "emailaddress": "email",
    "source": "how_did_you_hear", "sourceprompt": "how_did_you_hear", "sourcedropdown": "how_did_you_hear",
    "linkedinaccount": "linkedin", "linkedinquestion": "linkedin", "linkedin": "linkedin",
    "jobtitle": "title", "companyname": "company", "company": "company", "location": "location",
    "currentlyworkhere": "current", "startdate": "start", "enddate": "end",
    "roledescription": "description", "description": "description",
    "schoolname": "school", "school": "school", "degree": "degree",
    "fieldofstudy": "field_of_study", "field-of-study": "field_of_study",
    "gradeaverage": "gpa", "gpa": "gpa", "firstyearattended": "start", "lastyearattended": "end",
    "url": "url",
}


def field_role(f: dict) -> str | None:
    """What a Workday field holds, from its automation id / id / name (then its label)."""
    name = (f.get("fname") or "").split("--")[-1].lower()
    if name in WD_NAMES:
        return WD_NAMES[name]
    if f.get("kind") in ("text", "textarea") and not f.get("group"):
        key = classify_field(f.get("label") or "", f.get("name") or "")
        return {"preferred_name": None, "full_name": None, "location": None}.get(key, key) if key else None
    return None


def group_of(f: dict) -> str:
    g = (f.get("group") or "").lower()
    if "work" in g or "experience" in g:
        return "work"
    if "educ" in g:
        return "education"
    if "web" in g or "url" in g:
        return "website"
    return g


# ----------------------------------------------------------------------------- profile values

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
    "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
    "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
    "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def parse_month_year(text) -> tuple[int | None, int | None, bool]:
    """'September 2026' -> (9, 2026, False); 'Present' -> (None, None, True); '2025' -> (None, 2025, False)."""
    s = str(text or "").strip().lower()
    if not s:
        return None, None, False
    if re.fullmatch(r"present|current|now|ongoing", s):
        return None, None, True
    m = re.fullmatch(r"(\d{4})-(\d{1,2})(-\d{1,2})?", s)
    if m:
        return int(m.group(2)), int(m.group(1)), False
    m = re.fullmatch(r"(\d{1,2})/(\d{4})", s)
    if m:
        return int(m.group(1)), int(m.group(2)), False
    year = re.search(r"(19|20)\d{2}", s)
    mon = next((n for k, n in MONTHS.items() if re.search(rf"\b{k}", s)), None)
    return mon, int(year.group(0)) if year else None, False


def phone_digits(phone: str) -> str:
    d = re.sub(r"\D", "", str(phone or ""))
    return d[1:] if len(d) == 11 and d.startswith("1") else d


def workday_values(profile: dict, saved: SavedAnswers | None = None) -> dict[str, str]:
    base = profile_values(profile)
    addr = profile.get("address") or {}
    loc = [p.strip() for p in str(profile.get("location") or "").split(",")]
    state = str(addr.get("state") or (loc[1] if len(loc) > 1 else "")).strip()
    country = str(addr.get("country") or "").strip() or \
        str((saved.topics.get("country") if saved else "") or "").strip()
    return {
        "first_name": base["first_name"], "last_name": base["last_name"], "email": base["email"],
        "phone": phone_digits(base["phone"]), "phone_type": str(profile.get("phone_type") or "Mobile"),
        "address_line1": str(addr.get("line1") or ""), "address_line2": str(addr.get("line2") or ""),
        "city": str(addr.get("city") or (loc[0] if loc and loc[0] else "")),
        "state": US_STATES.get(state.upper(), state), "postal_code": str(addr.get("postal_code") or ""),
        "country": country, "how_did_you_hear": str(profile.get("how_did_you_hear") or ""),
        "linkedin": base["linkedin"],
    }


def experience_entries(profile: dict) -> list[dict]:
    return [e for e in (profile.get("experience") or []) if isinstance(e, dict) and (e.get("company") or e.get("title"))]


def education_entries(profile: dict) -> list[dict]:
    edu = [e for e in (profile.get("education") or []) if isinstance(e, dict) and e.get("school")]
    if edu:
        return edu
    if not profile.get("school"):
        return []
    majors = profile.get("majors") or []
    return [{"school": profile.get("school"), "degree": profile.get("degree", ""),
             "field_of_study": majors[0] if isinstance(majors, list) and majors else majors,
             "start": "", "end": profile.get("graduation", "")}]


def degree_candidates(degree: str) -> list[str]:
    """'B.S.' -> ['B.S.', 'Bachelor of Science', "Bachelor's Degree", 'Bachelors', 'Bachelor']."""
    d = re.sub(r"[^a-z]", "", str(degree or "").lower())
    out = [str(degree)] if degree else []
    if d in ("bs", "bsc", "bachelorofscience"):
        out += ["Bachelor of Science", "BS", "Bachelor's Degree", "Bachelors Degree", "Bachelors", "Bachelor"]
    elif d in ("ba", "bachelorofarts"):
        out += ["Bachelor of Arts", "BA", "Bachelor's Degree", "Bachelors Degree", "Bachelors", "Bachelor"]
    elif d.startswith("bachelor"):
        out += ["Bachelor's Degree", "Bachelors", "Bachelor"]
    elif d in ("ms", "msc", "masterofscience"):
        out += ["Master of Science", "MS", "Master's Degree", "Masters"]
    return [c for c in out if c]


# ----------------------------------------------------------------------------- safe clicks

_FORBIDDEN = re.compile(r"submit|sign ?in|log ?in|create account|delete|remove|withdraw|forgot", re.I)
_NEXT_IDS = ("bottom-navigation-next-button", "pageFooterNextButton")
_NEXT_TEXT = re.compile(r"^(save and continue|continue|next)$", re.I)


def _click(loc, why: str = "") -> None:
    """Click a Workday control, refusing anything that could submit, sign in, create an account, or delete."""
    tag, typ, aid, text, aria = loc.evaluate(
        "e => [e.tagName, e.getAttribute('type') || '', e.getAttribute('data-automation-id') || '',"
        " (e.innerText || e.value || '').trim().slice(0, 80), e.getAttribute('aria-label') || '']"
    )
    if typ == "submit" or _FORBIDDEN.search(f"{aid} {text} {aria}"):
        raise RuntimeError(f"refusing to click {tag} {text or aid!r} ({why}): openapply never submits, "
                           "signs in, creates accounts, or deletes")
    loc.click()


def _click_next(page, step: str) -> None:
    """Click "Save and Continue". Never on Review, never a button that says anything else (e.g. Submit)."""
    if step == "review" or read_state(page)[1] == "review":
        raise RuntimeError("openapply never moves past the Review step")
    btn = page.locator(", ".join(f'[data-automation-id="{i}"]' for i in _NEXT_IDS)).locator("visible=true").first
    if btn.count() == 0:
        raise RuntimeError("no Save and Continue button found")
    text = btn.inner_text().strip()
    if not _NEXT_TEXT.match(text):
        raise RuntimeError(f"refusing to click {text!r}: only Save and Continue / Continue / Next")
    btn.click()


# ----------------------------------------------------------------------------- widgets

def _loc(page, f: dict):
    return page.locator(f'[data-openapply="{f["key"]}"]')


def _fill_text(page, f: dict, value: str) -> None:
    loc = _loc(page, f)
    loc.fill(str(value))
    loc.blur()


def _visible_options(page):
    return page.locator('[role="listbox"] [role="option"], [data-automation-id="promptOption"]').locator("visible=true")


def _option_texts(opts, limit: int = 300) -> list[str]:
    return [opts.nth(i).inner_text().strip() for i in range(min(opts.count(), limit))]


def _pick_dropdown(page, f: dict, wanted: list[str]) -> str | None:
    """Open a Workday dropdown button, click the option best matching one of `wanted`, else close it."""
    _click(_loc(page, f), "open dropdown")
    _sleep(page, 600)
    for attempt in range(2):
        opts = page.locator('[role="listbox"] [role="option"]').locator("visible=true")
        texts = _option_texts(opts)
        for w in wanted:
            pick = choose_option(w, texts)
            if pick:
                opt = opts.nth(texts.index(pick))
                _safe_click_option(opt)
                _sleep(page, 400)
                return pick
        if attempt == 0 and wanted:  # long lists render lazily: type to jump, then look again
            page.keyboard.type(wanted[0][:8], delay=40)
            _sleep(page, 600)
    page.keyboard.press("Escape")
    return None


def _pick_multiselect(page, f: dict, wanted: list[str]) -> str | None:
    """Type into a Workday search box, press Enter IN THAT BOX to search, click the matching result."""
    loc = _loc(page, f)
    for w in wanted:
        if not w:
            continue
        _click(loc, "focus search box")
        loc.fill("")
        loc.press_sequentially(w, delay=20)
        loc.press("Enter")  # Workday searches on Enter; this box is not part of a submittable form
        _sleep(page, 1500)
        opts = _visible_options(page)
        texts = _option_texts(opts, 60)
        pick = choose_option(w, texts)
        if pick:
            _safe_click_option(opts.nth(texts.index(pick)))
            _sleep(page, 500)
            page.keyboard.press("Escape")
            return pick
    loc.fill("")
    page.keyboard.press("Escape")
    return None


def _fill_date(page, month_f: dict | None, year_f: dict | None, when) -> str | None:
    mon, year, _ = parse_month_year(when)
    if year is None or year_f is None:
        return None
    if month_f is not None and mon is None:
        return None
    done = []
    if month_f is not None:
        loc = _loc(page, month_f)
        _click(loc, "date month")
        loc.press_sequentially(f"{mon:02d}", delay=30)
        done.append(f"{mon:02d}")
    loc = _loc(page, year_f)
    _click(loc, "date year")
    loc.press_sequentially(str(year), delay=30)
    loc.blur()
    done.append(str(year))
    return "/".join(done)


def _check(page, f: dict) -> None:
    loc = _loc(page, f)
    try:
        loc.check(timeout=3000)
    except Exception:
        loc.check(force=True, timeout=3000)


# ----------------------------------------------------------------------------- the flow

@dataclass
class WorkdayFlow:
    page: object
    profile: dict
    saved: SavedAnswers
    resume_pdf: Path | None
    report: FillReport
    waiter: Waiter
    company: str = ""
    close_after: bool = False
    values: dict = field(default_factory=dict)
    handled: set = field(default_factory=set)

    def __post_init__(self):
        self.values = self.values or workday_values(self.profile, self.saved)

    def collect(self) -> list[dict]:
        return self.page.evaluate(WD_FIELDS_JS)

    @staticmethod
    def ident(f: dict) -> str:
        return f["id"] or f"{f['group']}{f['panel']}|{f['fname']}|{f['kind']}|{f['label']}|{f['option']}"

    def log(self, step: str, f: dict | None, label: str, action: str, detail: str, required: bool = False) -> None:
        if f is not None:
            self.handled.add(self.ident(f))
        self.report.add(FieldLog(label=label, kind=(f or {}).get("kind", ""), action=action, detail=detail,
                                 required=required or bool((f or {}).get("required"))), step)


def _answer_choice(flow: WorkdayFlow, step: str, f: dict, group: list[dict]) -> bool:
    """Fill one choice question from saved answers. Returns True if filled."""
    page = flow.page
    question = f["question"] or f["label"]
    for g in group:
        flow.handled.add(flow.ident(g))
    match = flow.saved.lookup(question, flow.company)
    if not allowed(match, flow.profile):
        flow.log(step, f, question, "skipped", "choice field: " + why_not(match))
        return False
    ans = match.answer
    pick = None
    if f["kind"] == "dropdown" and isinstance(ans, str):
        pick = _pick_dropdown(page, f, [ans])
    elif f["kind"] == "multiselect":
        pick = _pick_multiselect(page, f, ans if isinstance(ans, list) else [ans])
    elif f["kind"] == "select" and isinstance(ans, str):
        pick = choose_option(ans, f["options"])
        if pick:
            _loc(page, f).select_option(label=pick)
    elif f["kind"] == "radio" and isinstance(ans, str):
        pick = choose_option(ans, [g["option"] for g in group])
        if pick:
            _check(page, next(g for g in group if g["option"] == pick))
    elif f["kind"] == "checkbox":
        if len(group) == 1:
            if is_yes(ans):
                _check(page, f)
                pick = "checked"
            else:
                flow.log(step, f, question, "skipped", f"checkbox left unchecked (saved answer {ans!r})")
                return False
        else:
            picks = choose_many(ans, [g["option"] for g in group])
            for g in group:
                if g["option"] in picks:
                    _check(page, g)
            pick = ", ".join(picks) if picks else None
    if pick:
        flow.log(step, f, question, "filled", f"saved answer `{match.key}` -> {pick!r}")
        return True
    flow.log(step, f, question, "skipped", f"no option clearly matched saved answer {ans!r}")
    return False


def _choice_groups(fields: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for f in fields:
        if f["kind"] in ("radio", "checkbox"):
            groups.setdefault(f"{f['kind']}|{f['name'] or f['question']}|{f['group']}{f['panel']}", []).append(f)
    return groups


def _leftovers(flow: WorkdayFlow, step: str, fields: list[dict]) -> list[dict]:
    """Log every field nobody handled yet: choice questions via saved answers, the rest left for you.
    Returns the fields that are still empty and need you."""
    need: list[dict] = []
    groups = _choice_groups(fields)
    seen_groups: set[str] = set()
    for f in fields:
        if flow.ident(f) in flow.handled or f["kind"] in ("file",):
            continue
        label = f["question"] if f["kind"] in ("radio", "checkbox", "dropdown", "select") else f["label"]
        label = label or f["fname"] or f["id"]
        if f["kind"] in ("radio", "checkbox"):
            gk = f"{f['kind']}|{f['name'] or f['question']}|{f['group']}{f['panel']}"
            if gk in seen_groups:
                continue
            seen_groups.add(gk)
            group = groups.get(gk, [f])
            if any(g["checked"] for g in group):
                for g in group:
                    flow.handled.add(flow.ident(g))
                flow.log(step, f, label, "skipped", "already had a value")
                continue
            if not _answer_choice(flow, step, f, group):
                need.append(f)
        elif f["kind"] in ("dropdown", "select", "multiselect"):
            if f["value"]:
                flow.log(step, f, label, "skipped", "already had a value")
            elif not _answer_choice(flow, step, f, [f]):
                need.append(f)
        elif f["kind"] == "password":
            flow.log(step, f, label, "skipped", "password: never filled")
        elif f["value"]:
            flow.log(step, f, label, "skipped", "already had a value")
        elif SENSITIVE.search(label):
            flow.log(step, f, label, "skipped", "sensitive (authorization/demographic): left for you")
            need.append(f)
        else:
            flow.log(step, f, label, "skipped", "text answer: left for you")
            need.append(f)
    return need


def fill_my_information(flow: WorkdayFlow, step: str = "my_information") -> list[dict]:
    page, v = flow.page, flow.values
    fields = flow.collect()
    need: list[dict] = []
    for f in fields:
        role = field_role(f)
        label = f["label"] or f["question"] or f["fname"]
        if role is None or f["kind"] in ("radio", "checkbox"):
            continue
        if role == "how_did_you_hear":
            if not v["how_did_you_hear"]:
                flow.log(step, f, label, "skipped", "left alone (no how_did_you_hear in profile.yaml)")
                if f["required"] and not f["value"]:
                    need.append(f)
                continue
            if f["value"]:
                flow.log(step, f, label, "skipped", "already had a value")
                continue
            pick = (_pick_multiselect if f["kind"] == "multiselect" else _pick_dropdown)(page, f, [v["how_did_you_hear"]]) \
                if f["kind"] in ("multiselect", "dropdown") else None
            if f["kind"] == "text":
                _fill_text(page, f, v["how_did_you_hear"])
                pick = v["how_did_you_hear"]
            flow.log(step, f, label, "filled" if pick else "skipped",
                     f"how_did_you_hear -> {pick!r}" if pick else f"no option matched {v['how_did_you_hear']!r}")
            continue
        value = v.get(role, "")
        if f["value"]:
            flow.log(step, f, label, "skipped", "already had a value")
        elif f["kind"] in ("dropdown", "multiselect", "select"):
            if role not in ("country", "state", "phone_type") or not value:
                if role in ("country", "state", "phone_type"):
                    flow.log(step, f, label, "skipped", f"no `{role}` in your profile.yaml")
                continue  # other dropdowns: saved answers in _leftovers
            if f["kind"] == "select":
                pick = choose_option(value, f["options"])
                if pick:
                    _loc(page, f).select_option(label=pick)
            else:
                pick = (_pick_dropdown if f["kind"] == "dropdown" else _pick_multiselect)(page, f, [value])
            flow.log(step, f, label, "filled" if pick else "skipped",
                     f"{role} -> {pick!r}" if pick else f"no option clearly matched {value!r}")
        elif value:
            _fill_text(page, f, value)
            flow.log(step, f, label, "filled", role)
        elif role in ("middle_name", "address_line2"):
            flow.handled.add(flow.ident(f))
            flow.log(step, f, label, "skipped", "optional; left blank")
        else:
            flow.log(step, f, label, "skipped", f"no `{role}` in your profile.yaml")
            if f["required"]:
                need.append(f)
    return need + _leftovers(flow, step, flow.collect())


def _panel_fields(fields: list[dict], grp: str) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for f in fields:
        if group_of(f) == grp and f["panel"]:
            out.setdefault(f["panel"], []).append(f)
    return dict(sorted(out.items()))


def _fill_panel(flow: WorkdayFlow, step: str, title: str, pf: list[dict], entry: dict, grp: str) -> None:
    page = flow.page
    roles: dict[str, list[dict]] = {}
    for f in pf:
        roles.setdefault(field_role(f) or f["fname"].lower(), []).append(f)

    def one(role: str, kind: str | None = None) -> dict | None:
        for f in roles.get(role, []):
            if kind is None or f["kind"] == kind:
                return f
        return None

    def text(role: str, value) -> None:
        f = one(role)
        if f is None:
            return
        label = f"{title}: {f['label'] or role}"
        value = re.sub(r"\s+", " ", str(value or "")).strip()
        if f["value"]:
            flow.log(step, f, label, "skipped", "already had a value")
        elif not value:
            flow.log(step, f, label, "skipped", f"no `{role}` for this entry in profile.yaml")
        elif f["kind"] in ("text", "textarea"):
            _fill_text(page, f, value)
            flow.log(step, f, label, "filled", role)
        else:
            cands = degree_candidates(value) if role == "degree" else [value]
            if role == "degree":
                saved_deg = flow.saved.topics.get("degree")
                if saved_deg:
                    cands.insert(0, str(saved_deg))
            if f["kind"] == "select":
                pick = next((p for p in (choose_option(c, f["options"]) for c in cands) if p), None)
                if pick:
                    _loc(page, f).select_option(label=pick)
            else:
                pick = (_pick_dropdown if f["kind"] == "dropdown" else _pick_multiselect)(page, f, cands)
            flow.log(step, f, label, "filled" if pick else "skipped",
                     f"{role} -> {pick!r}" if pick else f"no option clearly matched {value!r}")

    def when(role: str, value) -> None:
        mf, yf = one(role, "month"), one(role, "year")
        if yf is None:
            return
        label = f"{title}: {role} date"
        for f in (mf, yf):
            if f is not None:
                flow.handled.add(flow.ident(f))
        if yf["value"]:
            flow.log(step, yf, label, "skipped", "already had a value")
            return
        done = _fill_date(page, mf, yf, value)
        flow.log(step, yf, label, "filled" if done else "skipped",
                 f"{role} -> {done}" if done else f"no usable {role} date ({value!r}) in profile.yaml")

    if grp == "work":
        _, _, present = parse_month_year(entry.get("end"))
        text("title", entry.get("title"))
        text("company", entry.get("company"))
        text("location", entry.get("location"))
        cur = one("current")
        if cur is not None:
            if present and not cur["checked"]:
                _check(page, cur)
                flow.log(step, cur, f"{title}: I currently work here", "filled", "current (end: Present)")
            else:
                flow.handled.add(flow.ident(cur))
        when("start", entry.get("start"))
        if not present:
            when("end", entry.get("end"))
        else:
            for f in roles.get("end", []):
                flow.handled.add(flow.ident(f))
        text("description", entry.get("description"))
    elif grp == "education":
        text("school", entry.get("school"))
        text("degree", entry.get("degree"))
        text("field_of_study", entry.get("field_of_study") or entry.get("major"))
        if entry.get("gpa"):
            text("gpa", entry.get("gpa"))
        when("start", entry.get("start"))
        when("end", entry.get("end"))
    elif grp == "website":
        text("url", entry.get("url"))


def _fill_repeating(flow: WorkdayFlow, step: str, grp: str, heading: str, title: str, entries: list[dict],
                    max_entries: int = 8) -> None:
    page = flow.page
    if not entries:
        return
    fields = flow.collect()
    panels = _panel_fields(fields, grp)
    for i, entry in enumerate(entries[:max_entries]):
        if i >= len(panels):
            if not page.evaluate(ADD_BUTTON_JS, heading):
                flow.log(step, None, f"{title} {i + 1}", "skipped", f"no Add button found for the {title} section")
                return
            add = page.locator('[data-openapply-add="1"]').first
            _click(add, f"add {title}")
            page.evaluate("() => document.querySelectorAll('[data-openapply-add]')"
                          ".forEach(b => b.removeAttribute('data-openapply-add'))")
            _sleep(page, 1200)
            fields = flow.collect()
            panels = _panel_fields(fields, grp)
            if i >= len(panels):
                flow.log(step, None, f"{title} {i + 1}", "skipped", f"clicked Add but no new {title} panel appeared")
                return
        number = list(panels)[i]
        _fill_panel(flow, step, f"{title} {i + 1}", panels[number], entry, grp)
        fields = flow.collect()
        panels = _panel_fields(fields, grp)
        # keep the handled-set in sync with the re-collected keys
    if len(entries) > max_entries:
        flow.log(step, None, title, "skipped", f"only the first {max_entries} entries are filled")


def _upload_resume(flow: WorkdayFlow, step: str) -> None:
    page = flow.page
    files = [f for f in flow.collect() if f["kind"] == "file"]
    if not files:
        return
    f = files[0]
    done = page.locator('[data-automation-id="file-upload-successful"], [data-automation-id="file-upload-item"]')\
        .locator("visible=true").count()
    if done:
        flow.log(step, f, "Resume/CV", "skipped", "a file is already uploaded")
    elif flow.resume_pdf and Path(flow.resume_pdf).exists():
        _loc(page, f).set_input_files(str(flow.resume_pdf))
        _sleep(page, 3000)
        flow.log(step, f, "Resume/CV", "uploaded", Path(flow.resume_pdf).name)
    else:
        flow.log(step, f, "Resume/CV", "skipped", "no resume PDF configured", required=True)
    for g in files[1:]:
        flow.log(step, g, g["label"] or "File upload", "skipped", "extra file upload left for you")


def fill_my_experience(flow: WorkdayFlow, step: str = "my_experience") -> list[dict]:
    p = flow.profile
    _upload_resume(flow, step)
    _fill_repeating(flow, step, "work", r"work experience", "Work Experience", experience_entries(p))
    _fill_repeating(flow, step, "education", r"^education", "Education", education_entries(p))
    fields = flow.collect()
    has_linkedin_box = any(field_role(f) == "linkedin" for f in fields)
    links = [u for u in ([] if has_linkedin_box else [p.get("linkedin")]) + [p.get("github"), p.get("website")] if u]
    _fill_repeating(flow, step, "website", r"^websites?", "Website", [{"url": u} for u in links])
    for f in flow.collect():
        if field_role(f) == "linkedin" and flow.ident(f) not in flow.handled:
            if f["value"]:
                flow.log(step, f, f["label"] or "LinkedIn", "skipped", "already had a value")
            elif p.get("linkedin"):
                _fill_text(flow.page, f, p["linkedin"])
                flow.log(step, f, f["label"] or "LinkedIn", "filled", "linkedin")
    # re-collect: keys change after every Add, so match handled fields by stable identity
    return _leftovers(flow, step, flow.collect())


def fill_questions(flow: WorkdayFlow, step: str) -> list[dict]:
    return _leftovers(flow, step, flow.collect())


def fill_autofill_resume(flow: WorkdayFlow, step: str = "autofill_resume") -> list[dict]:
    _upload_resume(flow, step)
    return []


HANDLERS = {
    "my_information": fill_my_information,
    "my_experience": fill_my_experience,
    "autofill_resume": fill_autofill_resume,
}

SIGNIN_MESSAGE = (
    "\n>>> Workday needs you to sign in (or create an account) for {company}.\n"
    ">>> Do it yourself in the browser window. openapply never creates accounts, never types or stores passwords.\n"
    ">>> Your login is kept in data/browser-profile/, so it's once per company. openapply continues on its own "
    "as soon as the application wizard appears."
)


def _wait_change(page, before: tuple[str, str], timeout_ms: int = 15000) -> tuple[str, str, dict]:
    end = time.monotonic() + timeout_ms * PATIENCE / 1000
    state, step, info = read_state(page)
    while time.monotonic() < end:
        state, step, info = read_state(page)
        if (state, step) != before and state != "unknown":
            return state, step, info
        _sleep(page, 500)
    return state, step, info


def run_flow(flow: WorkdayFlow, max_actions: int = 40) -> FillReport:
    """Drive the wizard from wherever the page is now until Review (or until we need you)."""
    page, report = flow.page, flow.report
    announced_signin = False
    attempts: dict[str, int] = {}
    state, step, info = _wait_change(page, ("unknown", ""), 20000)
    for _ in range(max_actions):
        if state == "job_page":
            _click(page.locator('[data-automation-id="adventureButton"]').locator("visible=true").first, "Apply")
            state, step, info = _wait_change(page, ("job_page", ""))
            continue
        if state == "apply_options":
            opts = info.get("options") or []
            choice = "applyManually" if "applyManually" in opts else \
                "autofillWithResume" if "autofillWithResume" in opts else None
            if choice is None:
                report.stopped_at = "apply options (only 'Use My Last Application'; pick it yourself)"
                return report
            _click(page.locator(f'[data-automation-id="{choice}"]').locator("visible=true").first, choice)
            state, step, info = _wait_change(page, ("apply_options", ""))
            continue
        if state == "signin":
            report.stopped_at = "signin"
            if flow.close_after:
                return report
            if not announced_signin:
                announced_signin = True
                ok = flow.waiter.pause(page, SIGNIN_MESSAGE.format(company=flow.company or "this company"),
                                       until=lambda: read_state(page)[0] not in ("signin", "unknown"))
            else:
                ok = flow.waiter.pause(page, ">>> Still on the sign-in page. Finish signing in, then press Enter.",
                                       until=lambda: read_state(page)[0] not in ("signin", "unknown"))
            if not ok:
                return report
            state, step, info = _wait_change(page, ("signin", "signin"), 8000)
            continue
        if state == "wizard":
            report.stopped_at = step
            if step == "review":
                return report
            attempts[step] = attempts.get(step, 0) + 1
            if attempts[step] > 3:
                if flow.close_after or not flow.waiter.pause(
                        page, f">>> openapply is stuck on '{info.get('activeStep') or step}'. Finish this step "
                              "yourself (click Save and Continue), then press Enter.",
                        until=lambda: read_state(page)[1] != step):
                    return report
                state, step, info = read_state(page)
                continue
            handler = HANDLERS.get(step)
            need = handler(flow, step) if handler else fill_questions(flow, step)
            if need:
                if flow.close_after:
                    return report
                names = "\n".join(f"      - {(f['question'] or f['label'] or f['fname'])[:90]}"
                                  f"{' (required)' if f['required'] else ''}" for f in need[:25])
                ok = flow.waiter.pause(
                    page, f"\n>>> [{step}] {len(need)} field(s) need you (no saved answer, or not openapply's to fill):"
                          f"\n{names}\n>>> Fill them in the browser. openapply clicks Save and Continue after you press "
                          "Enter (or move on yourself).",
                    until=lambda: read_state(page)[1] != step)
                if not ok:
                    return report
                state, step2, info = read_state(page)
                if step2 != step:
                    step = step2
                    continue
            if read_state(page)[1] == "review":
                state, step, info = read_state(page)
                continue
            _click_next(page, step)
            before = ("wizard", step)
            state, step, info = _wait_change(page, before, 15000)
            if (state, step) == before:
                errs = "; ".join(info.get("errors") or []) or "the page didn't move on"
                report.stopped_at = f"{step} (Workday errors: {errs[:200]})"
                if flow.close_after or not flow.waiter.pause(
                        page, f">>> Workday stayed on this step: {errs[:300]}\n>>> Fix it in the browser, then press "
                              "Enter (or click Save and Continue yourself).",
                        until=lambda: read_state(page)[1] != before[1]):
                    return report
                state, step, info = read_state(page)
                if (state, step) == before:
                    _click_next(page, step)
                    state, step, info = _wait_change(page, before, 15000)
            continue
        # unknown page: give it a moment, then hand it to you
        state, step, info = _wait_change(page, ("unknown", ""), 10000)
        if state == "unknown":
            report.stopped_at = f"unrecognized page ({(info or {}).get('url', page.url)})"
            if flow.close_after or not flow.waiter.pause(
                    page, ">>> openapply doesn't recognize this page. Get to the application wizard yourself, then "
                          "press Enter.", until=lambda: read_state(page)[0] != "unknown"):
                return report
            state, step, info = read_state(page)
    report.stopped_at = report.stopped_at or "gave up after too many steps"
    return report


def fill_workday(job: Job, profile: dict, resume_pdf: Path | None, saved: SavedAnswers | None = None,
                 close_after: bool = False, headless: bool = False, wait_seconds: int | None = None,
                 profile_dir: Path | None = None, on_ready=None) -> FillReport:
    """Launch the persistent browser, run the wizard, print the log, and hand off to you.

    `on_ready(page, report) -> bool` runs with the page still open after the wizard stops (auto-submit
    guardrails live there; they only ever act on the Review step). True means it submitted."""
    from playwright.sync_api import sync_playwright

    saved = saved or SavedAnswers()
    report = FillReport(job.id, job.url, ats="workday")
    profile_dir = profile_dir or BROWSER_PROFILE
    profile_dir.mkdir(parents=True, exist_ok=True)
    interactive = None if not close_after else False
    waiter = Waiter(wait_seconds, interactive=interactive, on_tick=lambda pg: merge_capture(report.captured, pg))
    with sync_playwright() as p:
        try:
            ctx = p.chromium.launch_persistent_context(str(profile_dir), headless=headless, viewport=None)
        except Exception as e:
            report.error = (f"couldn't open the browser profile in {profile_dir} ({type(e).__name__}). "
                            "Is another openapply window still open?")
            report.save()
            print_report(report)
            return report
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        flow = WorkdayFlow(page, profile, saved, resume_pdf, report, waiter, company=job.company,
                           close_after=close_after)
        try:
            page.goto(job.url, wait_until="domcontentloaded", timeout=60000)
            run_flow(flow)
        except Exception as e:
            report.error = f"{type(e).__name__}: {e}"
        report.save()
        print_report(report)
        done = False
        if on_ready is not None:
            try:
                done = bool(on_ready(page, report))
            except Exception as e:
                report.error = report.error or f"auto-submit: {type(e).__name__}: {e}"
        if done:
            pass
        elif report.stopped_at == "review":
            print("\n>>> Reached Review. openapply stops here and will NOT click Submit. "
                  "Check every page, then submit yourself.")
        elif report.stopped_at == "signin":
            print("\n>>> Stopped at the Workday sign-in page. Sign in yourself, then run `openapply apply` again.")
        if not close_after and not done:
            merge_capture(report.captured, page)
            try:
                waiter.hand_off(page)
            except Exception:
                pass
        try:
            ctx.close()
        except Exception:
            pass
    return report

