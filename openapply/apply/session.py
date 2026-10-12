"""Hand-off helpers shared by every filler: waiting for you, and noticing what you picked.

Step by step:
1. `Waiter` decides how openapply waits for you.
   - In a terminal (stdin is a TTY), it waits for you to press Enter.
   - With `--wait-seconds N`, it keeps the browser open N seconds in total and
     then closes it. This is for scripts and other non-interactive use.
   - Not a terminal and no --wait-seconds: it keeps the browser open until you
     close the window (up to 4 hours), instead of closing it instantly. (Before
     this, a non-TTY stdin made `input()` return at once and the browser closed.)
2. While it waits, it reads your terminal on a background thread and keeps
   looking at the page every ~1.5 seconds. So a wait can also end on its own,
   e.g. as soon as you've finished signing in to Workday.
3. Each look runs `CAPTURE_JS`, which reads every choice field you've set
   (dropdowns, radio buttons, checkboxes, Workday's custom dropdowns and
   multi-selects) together with its question. Snapshots are merged by question,
   so answers are remembered even if the page changes afterwards (for example
   after you click Submit and see the confirmation page).
4. `ask_yes_no` asks a y/N question through the same terminal reader, so it
   never fights the background thread for your keystrokes. Without a terminal
   it returns the default and says so.
"""

from __future__ import annotations

import queue
import sys
import threading
import time
from typing import Callable

# Finds the human question for any field: label[for], aria-labelledby, fieldset
# legend, Workday formField wrapper, Lever/Ashby/Greenhouse question containers.
LABEL_JS = r"""
const OA_clean = (s) => (s || '').replace(/\s+/g, ' ').replace(/\s*\*\s*$/, '').replace(/\s*Required\s*$/i, '')
  .replace(/\s*Select One\s*$/i, '').replace(/\s*\*\s*$/, '').trim();
const OA_text = (el) => el ? (el.innerText || el.textContent || '') : '';
function OA_ownLabel(el) {
  let t = '';
  if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) t = OA_text(l); }
  if (!t) { const l = el.closest('label'); if (l) t = OA_text(l); }
  if (!t && el.getAttribute('aria-labelledby')) {
    t = el.getAttribute('aria-labelledby').split(/\s+/).map(id => OA_text(document.getElementById(id))).join(' ');
  }
  if (!t) t = el.getAttribute('aria-label') || '';
  return OA_clean(t);
}
function OA_question(el) {
  // A text box / select / dropdown is best named by its own <label for>; the container
  // heuristics below are only for radio and checkbox groups (and unlabeled fields).
  const t = (el.getAttribute('type') || '').toLowerCase();
  if (t !== 'radio' && t !== 'checkbox') { const own = OA_ownLabel(el); if (own) return own; }
  const fs = el.closest('fieldset');
  if (fs) { const lg = fs.querySelector('legend'); if (lg && OA_clean(OA_text(lg))) return OA_clean(OA_text(lg)); }
  const grp = el.closest('[role="radiogroup"],[role="group"]');
  if (grp && grp.getAttribute('aria-labelledby')) {
    const t = grp.getAttribute('aria-labelledby').split(/\s+/).map(id => OA_text(document.getElementById(id))).join(' ');
    if (OA_clean(t)) return OA_clean(t);
  }
  const ff = el.closest('[data-automation-id^="formField-"]');
  if (ff) {
    const l = ff.querySelector('label, legend, [data-automation-id="richText"]');
    if (l && OA_clean(OA_text(l))) return OA_clean(OA_text(l));
  }
  const q = el.closest('.application-question, [class*="fieldEntry"], [class*="question"], .field');
  if (q) {
    const l = q.querySelector('.application-label, label, legend, [class*="label"], [class*="Label"]');
    if (l && !l.contains(el) && OA_clean(OA_text(l))) return OA_clean(OA_text(l));
  }
  return OA_ownLabel(el);
}
function OA_singleValue(el) {
  // react-select shows the chosen option in a sibling div, not in the <input>
  for (let a = el.parentElement, i = 0; a && i < 5; a = a.parentElement, i++) {
    const sv = a.querySelector('[class*="singleValue"], [class*="single-value"]');
    if (sv) return OA_clean(OA_text(sv));
  }
  return '';
}
"""

CAPTURE_JS = "() => {" + LABEL_JS + r"""
  const out = [];
  const seen = new Set();
  const visible = (el) => !!(el.getClientRects().length);
  for (const el of document.querySelectorAll('select')) {
    if (!visible(el) || el.selectedIndex < 0) continue;
    const opt = el.options[el.selectedIndex];
    if (!opt || !el.value) continue;
    out.push({label: OA_question(el), value: OA_clean(opt.text), kind: 'select'});
  }
  const groups = {};
  for (const el of document.querySelectorAll('input[type="radio"], input[type="checkbox"]')) {
    if (el.getAttribute('data-automation-id') === 'beecatcher') continue;
    const q = OA_question(el); const own = OA_ownLabel(el);
    const g = (groups[q] = groups[q] || {label: q, kind: el.type, items: [], checked: []});
    g.items.push(own);
    if (el.checked) g.checked.push(own);
  }
  for (const g of Object.values(groups)) {
    if (!g.checked.length) continue;
    if (g.kind === 'radio') out.push({label: g.label, value: g.checked[0], kind: 'radio'});
    else if (g.items.length === 1) out.push({label: g.label, value: 'Yes', kind: 'checkbox'});
    else out.push({label: g.label, value: g.checked, kind: 'checkbox-group'});
  }
  // Workday custom dropdowns: <button aria-haspopup="listbox">Selected text</button>
  for (const el of document.querySelectorAll('button[aria-haspopup="listbox"]')) {
    if (!visible(el)) continue;
    const v = OA_clean(OA_text(el));
    if (!v || /^select one$/i.test(v)) continue;
    out.push({label: OA_question(el), value: v, kind: 'dropdown'});
  }
  // Workday multi-selects: chosen items show as pills inside the formField
  for (const ff of document.querySelectorAll('[data-automation-id^="formField-"]')) {
    const pills = Array.from(ff.querySelectorAll('[data-automation-id="selectedItem"]')).map(p => OA_clean(OA_text(p))).filter(Boolean);
    if (!pills.length) continue;
    const box = ff.querySelector('input') || ff;
    out.push({label: OA_question(box), value: pills.length === 1 ? pills[0] : pills, kind: 'multiselect'});
  }
  // react-select (Greenhouse) single values
  for (const input of document.querySelectorAll('input[role="combobox"]')) {
    const v = OA_singleValue(input);
    if (v) out.push({label: OA_question(input), value: v, kind: 'combobox'});
  }
  return out.filter(o => o.label && o.value && !seen.has(o.label) && seen.add(o.label));
}
"""


class _Lines:
    """One background thread reading terminal lines into a queue."""

    def __init__(self) -> None:
        self.q: queue.Queue[str | None] = queue.Queue()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self) -> None:
        while True:
            line = sys.stdin.readline()
            if not line:
                self.q.put(None)
                return
            self.q.put(line.rstrip("\n"))

    def drain(self) -> None:
        while not self.q.empty():
            self.q.get_nowait()

    def get(self, timeout: float | None = None):
        """A line, None on end-of-input, or the string sentinel "\0timeout"."""
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return "\0timeout"


_lines: _Lines | None = None


def _reader() -> _Lines:
    global _lines
    if _lines is None:
        _lines = _Lines()
    return _lines


def stdin_is_tty() -> bool:
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def ask_yes_no(question: str, default: bool = False) -> bool:
    """y/N prompt that shares the terminal reader. No terminal -> the default."""
    if not stdin_is_tty():
        print(f"{question} [{'Y/n' if default else 'y/N'}] (no terminal; using default: {'yes' if default else 'no'})")
        return default
    r = _reader()
    r.drain()
    print(f"{question} [{'Y/n' if default else 'y/N'}] ", end="", flush=True)
    line = r.get()
    if line is None or line == "\0timeout":
        return default
    line = line.strip().lower()
    return default if not line else line in ("y", "yes")


class Waiter:
    """How openapply waits for you (terminal Enter, a fixed number of seconds, or the window closing)."""

    MAX_UNATTENDED = 4 * 3600

    def __init__(self, wait_seconds: int | None = None, interactive: bool | None = None,
                 on_tick: Callable[[object], None] | None = None) -> None:
        self.interactive = stdin_is_tty() if interactive is None else interactive
        self.wait_seconds = wait_seconds
        self.deadline = time.monotonic() + wait_seconds if wait_seconds is not None else None
        self.on_tick = on_tick

    @property
    def mode(self) -> str:
        if self.deadline is not None:
            return "seconds"
        return "enter" if self.interactive else "window"

    def expired(self) -> bool:
        return self.deadline is not None and time.monotonic() >= self.deadline

    def _tick(self, page) -> bool:
        """One poll: snapshot choices, sleep ~1.5s. False if the page/browser is gone."""
        try:
            if page.is_closed():
                return False
            if self.on_tick:
                self.on_tick(page)
            page.wait_for_timeout(1500)
            return True
        except Exception:
            return not page.is_closed() if hasattr(page, "is_closed") else False

    def pause(self, page, message: str, until: Callable[[], bool] | None = None) -> bool:
        """Wait for you. Returns True if you continued (Enter, or `until()` came true), False if time ran
        out or the window was closed."""
        print(message, flush=True)
        if self.mode == "enter":
            print(">>> Press Enter here to continue.", flush=True)
            _reader().drain()
        elif self.mode == "seconds":
            left = max(0, int(self.deadline - time.monotonic()))
            print(f">>> Not waiting for Enter (--wait-seconds): {left}s left before openapply closes the browser.")
        else:
            print(">>> stdin is not a terminal, so openapply can't read Enter. It will keep the browser open until "
                  "you close the window (or pass --wait-seconds N).", flush=True)
        start = time.monotonic()
        while True:
            try:
                if until and until():
                    return True
            except Exception:
                pass
            if self.mode == "enter":
                line = _reader().get(timeout=0.05)
                if line is None:  # stdin closed: fall back to waiting on the window
                    self.interactive = False
                elif line != "\0timeout":
                    return True
            if self.expired() or time.monotonic() - start > self.MAX_UNATTENDED:
                return False
            if not self._tick(page):
                return False

    def hand_off(self, page) -> None:
        """Final wait before closing the browser (you review and submit)."""
        if self.mode == "enter":
            msg = ">>> openapply will not submit. Press Enter here when you're done to close the browser."
        elif self.mode == "seconds":
            msg = f">>> Keeping the browser open {max(0, int(self.deadline - time.monotonic()))}s (--wait-seconds)."
        else:
            msg = ">>> openapply will not submit. Close the browser window when you're done."
        self.pause(page, msg)


def merge_capture(store: dict[str, dict], page) -> None:
    """Snapshot the page's chosen values into `store`, keyed by question label."""
    try:
        for item in page.evaluate(CAPTURE_JS):
            store[item["label"]] = item
    except Exception:
        pass  # page navigating or closed; keep what we had
