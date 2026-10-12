"""Read resumes and turn LaTeX into plain text.

Step by step:
1. Resume variants come from `resumes:` in config/profile.yaml (each has a .tex or
   .txt path, an optional PDF to upload, and the role families it's for). If none
   are configured, every resumes/*.tex|*.txt file becomes a variant.
2. For LaTeX we keep only the document body, drop comments and layout commands,
   unwrap formatting commands (\\textbf{x} -> x, \\href{url}{text} -> text), and
   unescape \\& \\% \\_ etc.
3. A `\\tbd{...}` macro marks a draft bullet you haven't confirmed yet. Its text is
   kept for matching, but the fabrication checker treats facts that ONLY appear
   inside \\tbd as unverified.
4. `bullets` are the individual \\resumeItem{...} lines; the tailor uses them to
   build drafts that only ever reuse your own sentences.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ..config import PROJECT_ROOT

_DROP_WITH_ARGS = re.compile(
    r"\\(vspace|hspace|usepackage|documentclass|setlength|addtolength|newcommand|renewcommand|"
    r"titleformat|input|pdfgentounicode|urlstyle|pagestyle|fancyhf|fancyfoot)\*?(\[[^\]]*\])?(\{[^{}]*\})*"
)


def _matching_brace(s: str, open_idx: int) -> int:
    depth = 0
    for i in range(open_idx, len(s)):
        if s[i] == "{" and (i == 0 or s[i - 1] != "\\"):
            depth += 1
        elif s[i] == "}" and (i == 0 or s[i - 1] != "\\"):
            depth -= 1
            if depth == 0:
                return i
    return len(s) - 1


def extract_macro_args(tex: str, macro: str) -> list[str]:
    """All first-argument contents of \\macro{...}, with nested braces handled."""
    out = []
    for m in re.finditer(rf"\\{macro}\s*\{{", tex):
        start = m.end() - 1
        end = _matching_brace(tex, start)
        out.append(tex[start + 1 : end])
    return out


def strip_latex(tex: str) -> str:
    if "\\begin{document}" in tex:
        tex = tex.split("\\begin{document}", 1)[1]
    tex = tex.replace("\\end{document}", "")
    tex = re.sub(r"(?<!\\)%.*", "", tex)  # comments
    tex = _DROP_WITH_ARGS.sub(" ", tex)
    tex = re.sub(r"\\href\{[^{}]*\}", "", tex)  # keep the link text, drop the URL
    tex = re.sub(r"\$\|\$", " | ", tex)
    tex = re.sub(r"\$[^$]*\$", " ", tex)  # inline math (bullets, separators)
    for a, b in (("\\&", "&"), ("\\%", "%"), ("\\$", "$"), ("\\_", "_"), ("\\#", "#"), ("--", "-"), ("\\\\", "\n")):
        tex = tex.replace(a, b)
    tex = re.sub(r"\\(begin|end)\{[^}]*\}(\[[^\]]*\])?(\{[^}]*\})?", "\n", tex)
    tex = re.sub(r"\\[a-zA-Z]+\*?(\[[^\]]*\])?", " ", tex)  # remaining commands, keep their {args}
    tex = tex.replace("{", " ").replace("}", " ")
    tex = re.sub(r"[ \t]+", " ", tex)
    return re.sub(r"\n\s*\n+", "\n", tex).strip()


@dataclass
class Resume:
    variant: str
    label: str
    path: Path
    pdf: Path | None
    families: list[str]
    text: str  # everything, including \tbd drafts
    verified_text: str  # with \tbd drafts removed
    bullets: list[str] = field(default_factory=list)
    tbd_bullets: list[str] = field(default_factory=list)


def _remove_tbd(tex: str) -> str:
    out, i = [], 0
    for m in re.finditer(r"\\tbd\s*\{", tex):
        if m.start() < i:
            continue
        out.append(tex[i : m.start()])
        i = _matching_brace(tex, m.end() - 1) + 1
    out.append(tex[i:])
    return "".join(out)


def load_resume(variant: str, path: Path, pdf: Path | None = None, families=None, label: str | None = None) -> Resume:
    raw = path.read_text(encoding="utf-8")
    if path.suffix == ".tex":
        items = extract_macro_args(raw, "resumeItem")
        bullets = [strip_latex(x) for x in items if "\\tbd" not in x]
        tbd = [strip_latex(x) for x in items if "\\tbd" in x]
        text, verified = strip_latex(raw), strip_latex(_remove_tbd(raw))
    else:
        text = verified = raw
        bullets = [l.lstrip("-•* ").strip() for l in raw.splitlines() if l.strip().startswith(("-", "•", "*"))]
        tbd = []
    if pdf is None and path.with_suffix(".pdf").exists():
        pdf = path.with_suffix(".pdf")
    return Resume(variant, label or variant, path, pdf, list(families or []), text, verified, bullets, tbd)


def load_resumes(profile: dict) -> dict[str, Resume]:
    variants = (profile.get("resumes") or {}).get("variants") or {}
    out: dict[str, Resume] = {}
    for name, v in variants.items():
        p = Path(v["path"]).expanduser()
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        if not p.exists():
            continue
        pdf = Path(v["pdf"]).expanduser() if v.get("pdf") else None
        out[name] = load_resume(name, p, pdf, v.get("families"), v.get("label"))
    if not out:
        for p in sorted((PROJECT_ROOT / "resumes").glob("*")):
            if p.suffix in (".tex", ".txt"):
                out[p.stem] = load_resume(p.stem, p)
    return out
