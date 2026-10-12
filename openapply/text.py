"""Small text helpers: HTML to plain text, and normalization for de-duplication."""

from __future__ import annotations

import html
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

_TAG = re.compile(r"<[^>]+>")
_BLOCK = re.compile(r"</?(p|div|br|li|ul|ol|h[1-6]|tr)[^>]*>", re.I)
_TRACKING = {"utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "ref", "src", "source"}


def html_to_text(raw: str) -> str:
    """Turn (possibly HTML-escaped) HTML into readable plain text."""
    if not raw:
        return ""
    s = html.unescape(raw)  # Greenhouse double-escapes its content
    s = _BLOCK.sub("\n", s)
    s = _TAG.sub(" ", s)
    s = html.unescape(s).replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n\n", s)
    return s.strip()


def strip_tracking(url: str) -> str:
    """Remove utm/ref tracking parameters so the same link compares equal."""
    p = urlparse(url.strip())
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k.lower() not in _TRACKING]
    return urlunparse(p._replace(query=urlencode(query), fragment=""))


def norm_link(url: str) -> str:
    p = urlparse(strip_tracking(url).lower())
    return f"{p.netloc.removeprefix('www.')}{p.path.rstrip('/')}" + (f"?{p.query}" if p.query else "")


_CORP = re.compile(r"\b(inc|llc|ltd|corp|corporation|co|company|technologies|labs?|the)\b\.?")


def norm_company(name: str) -> str:
    s = _CORP.sub(" ", name.lower())
    return re.sub(r"[^a-z0-9]", "", s)


def norm_title(title: str) -> str:
    s = re.sub(r"[^a-z0-9 ]", " ", title.lower())
    return re.sub(r"\s+", " ", s).strip()
