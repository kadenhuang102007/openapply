"""Opt-in auto-submit: submit ONLY when every guardrail passes, keep a receipt, otherwise stop for you.

Off by default. It needs BOTH `auto_submit: true` in config/profile.yaml AND a
command that asks for it (`openapply apply <id> --submit`, `openapply add`,
`openapply autopilot`). With either missing, openapply fills and stops, as always.

Step by step:
1. Before opening the browser, `job_checks` looks at the job itself:
   - the company is on the established-company list (for a link you pasted
     yourself with `openapply add`, this only WARNS: you chose it);
   - its match score is at least `auto_submit_min_score` (default 60);
   - fewer than `auto_submit_daily_limit` (default 10) auto-submissions today
     (counted from today's receipts);
   - you haven't already applied to this link (same URL or id, any status from
     submitted onwards), and there's no earlier receipt for it;
   - if `openapply tailor` drafted text for it, the saved fabrication check says PASS.
2. After filling, `page_checks` looks at the live page:
   - every REQUIRED field has a value (dropdowns, radio groups, files included);
   - no CAPTCHA or bot challenge (openapply never solves or bypasses one);
   - nothing typed into the form still contains a "[YOUR WORDS" placeholder;
   - Workday: we're on the Review step and Workday shows no validation errors.
3. If anything fails, nothing is submitted. You get the reasons, the job goes on
   the `openapply todo` list, and the browser behaves as it always did.
4. If everything passes: a screenshot and a snapshot of every field and value
   are taken, the Submit button is clicked (Greenhouse/Lever/Ashby: the form's
   own submit button; Workday: the Review step's "Submit"), and openapply waits
   up to 30 seconds for a confirmation ("Thank you for applying", "Application
   submitted", Workday's congratulations page).
5. generated/<job_id>/receipt.json (+ receipt_before.png / receipt_after.png)
   records the time, link, resume, every field and value, and the confirmation
   text. A receipt is written even when no confirmation was seen, so the same
   job is never submitted twice.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from ..config import GENERATED_DIR
from .session import LABEL_JS

PLACEHOLDER_MARK = "[YOUR WORDS"

FORM_SNAPSHOT_JS = "() => {" + LABEL_JS + r"""
  const out = [];
  const vis = (el) => !!el.getClientRects().length;
  const radios = {};
  for (const el of document.querySelectorAll('input, textarea, select')) {
    const type = (el.getAttribute('type') || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset', 'password'].includes(type)) continue;
    if (el.getAttribute('data-automation-id') === 'beecatcher') continue;
    if (type !== 'file' && !vis(el)) continue;
    if (type !== 'file' && type !== 'radio' && type !== 'checkbox' &&
        (el.tabIndex < 0 || el.getAttribute('aria-hidden') === 'true')) continue;
    const required = el.required || el.getAttribute('aria-required') === 'true';
    if (type === 'radio') {
      const k = el.name || OA_question(el);
      const g = (radios[k] = radios[k] || {label: OA_question(el), value: '', required: false, kind: 'radio'});
      if (el.checked) g.value = OA_ownLabel(el);
      g.required = g.required || required;
      continue;
    }
    let value = el.value || '';
    if (type === 'file') value = Array.from(el.files || []).map(f => f.name).join(', ');
    else if (type === 'checkbox') value = el.checked ? 'checked' : '';
    else if (el.tagName === 'SELECT') value = el.value ? el.options[el.selectedIndex].text.trim() : '';
    else if (el.getAttribute('role') === 'combobox') value = OA_singleValue(el) || value;
    out.push({label: OA_question(el) || OA_ownLabel(el) || el.name || el.id, value, required, kind: type});
  }
  for (const g of Object.values(radios)) out.push(g);
  // Workday dropdown buttons
  for (const b of document.querySelectorAll('button[aria-haspopup="listbox"]')) {
    if (!vis(b)) continue;
    const ff = b.closest('[data-automation-id^="formField-"]');
    const required = b.getAttribute('aria-required') === 'true' ||
      !!(ff && ff.querySelector('abbr[title="required"]'));
    const v = (b.innerText || '').trim();
    out.push({label: OA_question(b), value: /^select one$/i.test(v) ? '' : v, required, kind: 'dropdown'});
  }
  return out;
}
"""

BOT_CHECK_JS = r"""
() => !!document.querySelector(
  'iframe[src*="recaptcha"], iframe[src*="hcaptcha"], iframe[src*="turnstile"], iframe[src*="challenges.cloudflare"], ' +
  '.g-recaptcha, .h-captcha, .cf-turnstile, #challenge-form, [data-automation-id="captcha"]'
) || /verify you are (a )?human|are you a robot|unusual traffic|checking your browser/i.test(document.body.innerText || '')
"""

CONFIRM = re.compile(
    r"thank(s| you) for (applying|your application|your interest|submitting)|application (has been |was )?"
    r"(submitted|received)|we('ve| have) received your application|successfully (submitted|applied)|"
    r"congratulations|you('ve| have) (successfully )?applied",
    re.I,
)

SUBMIT_TEXT = re.compile(r"^(submit( (your )?application)?|apply( now)?|send application)$", re.I)


@dataclass
class SubmitSettings:
    enabled: bool = False
    min_score: int = 60
    daily_limit: int = 10


def settings(profile: dict) -> SubmitSettings:
    on = str(profile.get("auto_submit", False)).strip().lower() in ("true", "yes", "1", "on")
    return SubmitSettings(on, int(profile.get("auto_submit_min_score", 60) or 0),
                          int(profile.get("auto_submit_daily_limit", 10) or 0))


@dataclass
class Outcome:
    status: str = "not_attempted"   # submitted | unconfirmed | needs_you | would_submit | not_attempted
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    receipt: str = ""

    @property
    def reason(self) -> str:
        return "; ".join(self.reasons)


def receipts_today(gen_dir: Path | None = None, today: str | None = None) -> int:
    """How many auto-submit receipts were written today (local date)."""
    gen_dir = gen_dir or GENERATED_DIR
    today = today or datetime.now().date().isoformat()
    n = 0
    for p in gen_dir.glob("*/receipt.json"):
        try:
            if json.loads(p.read_text())["submitted_at"][:10] == today:
                n += 1
        except Exception:
            continue
    return n


def job_checks(row, tracker, profile: dict, gen_dir: Path | None = None, user_chose: bool = False,
               today: str | None = None) -> tuple[list[str], list[str]]:
    """Guardrails that don't need the browser. Returns (reasons to stop, warnings)."""
    gen_dir = gen_dir or GENERATED_DIR
    s = settings(profile)
    reasons, warnings = [], []
    if not row["company_tier"]:
        msg = f"{row['company']} is not on the established-company list"
        (warnings if user_chose else reasons).append(msg + (" (you added it yourself, so not blocking)" if user_chose else ""))
    score = row["llm_score"] if row["llm_score"] is not None else row["score"]
    if score is None or score < s.min_score:
        reasons.append(f"match score {score} is below auto_submit_min_score {s.min_score}")
    done = receipts_today(gen_dir, today)
    if done >= s.daily_limit:
        reasons.append(f"daily auto-submit limit reached ({done}/{s.daily_limit})")
    prior = tracker.already_submitted(row["url"], row["id"])
    if prior is not None:
        reasons.append(f"already applied ({prior['id']}, status {prior['status']})")
    if (gen_dir / row["id"] / "receipt.json").exists():
        reasons.append("a submit receipt already exists for this job")
    fab = gen_dir / row["id"] / "fabrication_check.txt"
    if (gen_dir / row["id"] / "answers.json").exists() or (gen_dir / row["id"] / "cover_letter.md").exists():
        if not fab.exists():
            reasons.append("drafts exist but there's no fabrication check (run `openapply tailor`)")
        elif "PASS" not in fab.read_text(encoding="utf-8"):
            reasons.append("fabrication check did not pass (see generated/<id>/fabrication_check.txt)")
    return reasons, warnings


def page_checks(snapshot: list[dict], bot_check: bool, report=None, workday_state: dict | None = None) -> list[str]:
    """Guardrails on the live page right before submitting."""
    reasons = []
    if bot_check or (report is not None and report.captcha_present):
        reasons.append("CAPTCHA / bot check on the page (openapply never solves these)")
    missing = [f["label"] or "(unlabeled)" for f in snapshot if f.get("required") and not str(f.get("value") or "").strip()]
    if missing:
        reasons.append(f"{len(missing)} required field(s) empty: " + "; ".join(m[:60] for m in missing[:6]))
    holders = [f["label"] for f in snapshot if PLACEHOLDER_MARK.lower() in str(f.get("value") or "").lower()]
    if holders:
        reasons.append("a '[YOUR WORDS' placeholder is still in: " + "; ".join(h[:60] for h in holders[:4]))
    if report is not None and report.ats == "workday":
        if report.stopped_at != "review":
            reasons.append(f"Workday didn't reach Review (stopped at: {report.stopped_at or 'start'})")
        if workday_state and workday_state.get("errors"):
            reasons.append("Workday shows errors: " + "; ".join(workday_state["errors"])[:200])
    if report is not None and report.error:
        reasons.append(f"filler error: {report.error[:150]}")
    return reasons


def _confirmation(page, timeout_s: int = 30) -> str:
    for _ in range(timeout_s * 2):
        try:
            text = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
            m = CONFIRM.search(text) or CONFIRM.search(page.url.replace("-", " "))
            if m:
                return m.group(0)
            if re.search(r"/(confirmation|thank[-_]?you|submitted|success)\b", page.url, re.I):
                return page.url
        except Exception:
            pass  # navigating
        page.wait_for_timeout(500)
    return ""


def click_submit(page, ats: str) -> str:
    """Click the real Submit button. Only ever called after every guardrail passed."""
    if ats == "workday":
        from .workday import read_state

        if read_state(page)[1] != "review":
            raise RuntimeError("Workday submit is only allowed from the Review step")
        btn = page.locator('[data-automation-id="bottom-navigation-next-button"], '
                           '[data-automation-id="pageFooterNextButton"]').locator("visible=true").first
        text = btn.inner_text().strip()
        if text.lower() != "submit":
            raise RuntimeError(f"expected Workday's Submit button on Review, found {text!r}")
        btn.click()
        return text
    cands = page.locator('button[type="submit"], input[type="submit"], button').locator("visible=true")
    for i in range(min(cands.count(), 60)):
        b = cands.nth(i)
        text = (b.inner_text() or b.get_attribute("value") or "").strip()
        if SUBMIT_TEXT.match(text):
            b.click()
            return text
    raise RuntimeError("no Submit button found")


def write_receipt(job, page, snapshot: list[dict], resume_pdf, report, confirmation: str, clicked: str,
                  gen_dir: Path | None = None, before_png: str = "", after_png: str = "") -> Path:
    gen_dir = gen_dir or GENERATED_DIR
    out = gen_dir / job.id
    out.mkdir(parents=True, exist_ok=True)
    data = {
        "job_id": job.id, "company": job.company, "title": job.title, "url": job.url, "ats": report.ats or job.ats,
        "submitted_at": datetime.now().isoformat(timespec="seconds"),
        "confirmed": bool(confirmation), "confirmation": confirmation, "submit_button": clicked,
        "resume": str(resume_pdf or ""), "final_url": getattr(page, "url", ""),
        "fields": [{"label": f["label"], "value": f["value"]} for f in snapshot],
        "filled_by_openapply": [asdict(f) for f in report.filled],
        "screenshots": [p for p in (before_png, after_png) if p],
    }
    path = out / "receipt.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return path


def try_submit(page, job, report, resume_pdf, pre_reasons: list[str], warnings: list[str], dry_run: bool = False,
               gen_dir: Path | None = None) -> Outcome:
    """Run the page guardrails and, only if everything passes (and not dry_run), submit and keep a receipt."""
    gen_dir = gen_dir or GENERATED_DIR
    out = Outcome(warnings=list(warnings))
    try:
        snapshot = page.evaluate(FORM_SNAPSHOT_JS)
        bot = bool(page.evaluate(BOT_CHECK_JS))
        wstate = None
        if report.ats == "workday":
            from .workday import read_state

            wstate = read_state(page)[2]
    except Exception as e:
        out.status, out.reasons = "needs_you", [f"couldn't read the page ({type(e).__name__})"]
        return out
    out.reasons = list(pre_reasons) + page_checks(snapshot, bot, report, wstate)
    if out.reasons:
        out.status = "needs_you"
        return out
    if dry_run:
        out.status = "would_submit"
        return out
    d = gen_dir / job.id
    d.mkdir(parents=True, exist_ok=True)
    before = str(d / "receipt_before.png")
    try:
        page.screenshot(path=before, full_page=True)
    except Exception:
        before = ""
    clicked = click_submit(page, report.ats or job.ats)
    confirmation = _confirmation(page)
    after = str(d / "receipt_after.png")
    try:
        page.screenshot(path=after, full_page=True)
    except Exception:
        after = ""
    out.receipt = str(write_receipt(job, page, snapshot, resume_pdf, report, confirmation, clicked, gen_dir,
                                    before, after))
    if confirmation:
        out.status = "submitted"
    else:
        out.status = "unconfirmed"
        out.reasons = ["clicked Submit but saw no confirmation; check the site, then "
                       f"`openapply mark {job.id} submitted` if it went through"]
    return out
