#!/usr/bin/env python3
"""
MaritimeMonday — build the weekly MaritimeONE job post and send it via a Telegram bot.

What it does:
  1. Fetches https://www.maritimeone.sg/job-listing and reads the structured job
     data the page embeds (Laravel/Inertia "data-page" JSON) — no HTML scraping.
  2. Keeps only jobs POSTED IN THE PREVIOUS Mon-Sun week (relative to the run date,
     in Singapore time). E.g. a post built on Mon 22 Jun covers 15-21 Jun.
  3. Splits companies into "Active Employers" (badged) and "Other job opportunities"
     (not badged). Each COMPANY NAME links to its MaritimeONE company page and each
     POSITION links to its job page. Messages are sent with HTML formatting.
  4. Sends the result to you as a DRAFT (your private chat with the bot), or
     publishes a previously-saved draft to the public channel.

The "Target Audience" line for each job is chosen by a documented rule set — see the
GUIDELINE comment above target_audience(). Both --send-draft and --publish build the post
fresh from the portal; a draft and its publish happen in the same Mon-Sun week, so they
produce the same post. Nothing is stored between runs and nothing is written to the repo.

Modes (CLI):
  --dry-run       Build and PRINT the post. No Telegram calls. (default)
  --send-draft    Build the post and send it to your private review chat.
  --publish       Build the post and send it to the public channel.

Environment variables:
  TELEGRAM_BOT_TOKEN       Bot token from @BotFather            (needed for --send-draft / --publish)
  TELEGRAM_REVIEW_CHAT_ID  Your private chat id with the bot    (needed for --send-draft)
  TELEGRAM_CHANNEL_ID      Channel @username or -100... id      (needed for --publish)
  PORTAL_URL               Override the job listing URL         (optional)
  TIMEZONE                 IANA tz for the week window          (optional, default Asia/Singapore)
"""

import argparse
import html as html_lib
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

PORTAL_URL = os.environ.get("PORTAL_URL", "https://www.maritimeone.sg/job-listing")
TIMEZONE = os.environ.get("TIMEZONE", "Asia/Singapore")
PORTAL_LINK = "https://www.maritimeone.sg/job-listing"
JOB_URL = "https://www.maritimeone.sg/job-detail/{}"
COMPANY_URL = "https://www.maritimeone.sg/company-detail/{}"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
)

NUMBER_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣",
                "6️⃣", "7️⃣", "8️⃣", "9️⃣", "\U0001f51f"]

# Show at most this many companies under "Other job opportunities" (1..10 -> keeps the
# 1-10 emoji numbering, no "11." overflow). Active Employers (badged) are always shown.
MAX_OTHER_COMPANIES = 10

# Keep the whole post inside ONE Telegram message (Telegram's hard limit is 4096 visible
# characters). Each company lists at most MAX_POSITIONS_PER_COMPANY roles (the rest become
# a "+N more" link), and each Target Audience line starts at AUDIENCE_MAX_LEN characters
# and is auto-shortened further if the post is still too long for one message.
MAX_POSITIONS_PER_COMPANY = 3
AUDIENCE_MAX_LEN = 90
TELEGRAM_LIMIT = 4096

# Section-header phrases that must NEVER be used as a Target Audience line. Matched
# case-insensitively against a normalised line (trailing "(...)" and ":" removed).
HEADER_PHRASES = {
    "requirements", "requirement", "job requirements", "minimum requirements",
    "qualifications", "qualification", "key qualifications", "key qualifications & skills",
    "qualifications & skills", "preferred qualifications", "education",
    "education and certification", "certification", "certifications", "skills", "key skills",
    "desired skills", "technical competencies", "competencies", "personal attributes",
    "attributes", "experience", "work experience", "relevant experience", "responsibilities",
    "key responsibilities", "job description", "job scope", "scope", "overview",
    "role overview", "the role", "about us", "about you", "about the role",
    "about the company", "who we are looking for", "what we're looking for",
    "what we are looking for", "what we offer", "we offer", "what you'll do",
    "what you will do", "what you bring", "your profile", "the ideal candidate",
    "nice to have", "must have", "eligibility", "eligibility criteria", "who should apply",
    "benefits", "why join us", "target audience", "requirements & qualifications",
    "candidate profile", "candidate requirements", "position requirements",
    "professional requirements", "academic requirements", "educational requirements",
    "educational qualifications", "academic qualifications", "core competencies",
    "key attributes", "key requirements", "what you'll need", "what you need",
    "skills & experience", "skills and experience", "experience & skills",
    "requirements and responsibilities", "roles and responsibilities",
    "roles & responsibilities", "duties and responsibilities", "job duties",
    "who you are", "your responsibilities", "your qualifications", "your skills",
    "profile", "requisites", "prerequisites", "requirements/qualifications",
}

LEGAL_SUFFIX = re.compile(
    r"[\s,]+(pte\.?\s*ltd\.?|private\s+limited|pte\.?\s*limited|"
    r"co\.?,?\s*ltd\.?|company\s+limited|limited|l\.?l\.?p\.?|l\.?l\.?c\.?|inc\.?)\.?\s*$",
    re.IGNORECASE,
)

# Internal supervisor notes some employers append to a job title, e.g. "(Sup: Serena)".
SUP_NOTE = re.compile(r"\s*\(\s*sup\b[^)]*\)\s*$", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Fetching & parsing
# --------------------------------------------------------------------------- #
def fetch_jobs(url=PORTAL_URL):
    """Return the list of job dicts embedded in the portal page."""
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=60)
    resp.raise_for_status()
    match = re.search(r'data-page="([^"]*)"', resp.text)
    if not match:
        raise RuntimeError("Could not find embedded job data (data-page) on the page. "
                           "The portal's structure may have changed.")
    payload = json.loads(html_lib.unescape(match.group(1)))
    jobs = payload.get("props", {}).get("jobs", [])
    if not isinstance(jobs, list):
        raise RuntimeError("Unexpected job data shape on the page.")
    return jobs


def job_date(job):
    """Best-effort posting date for a job as a datetime.date, or None."""
    raw = job.get("created_yyyy_mm_dd") or (job.get("created") or "")[:10]
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------- #
# Date window: previous Monday-Sunday
# --------------------------------------------------------------------------- #
def previous_week_window(today=None):
    """(start, end) dates for the Mon-Sun week before this week's Monday."""
    if today is None:
        today = datetime.now(ZoneInfo(TIMEZONE)).date()
    this_monday = today - timedelta(days=today.weekday())  # Mon of current week
    start = this_monday - timedelta(days=7)                # previous Monday
    end = this_monday - timedelta(days=1)                  # previous Sunday
    return start, end


# --------------------------------------------------------------------------- #
# Target Audience extraction
# --------------------------------------------------------------------------- #
# GUIDELINE — how the one-line "Target Audience" is chosen from a job's requirement
# HTML. The requirement is split into blocks (each <p>, <li>, <h*> or <div>). Whether a
# block is a SECTION HEADER is judged from its TEXT, NOT its tag — some employers put
# real requirement text inside <h3>/<strong> and some put headers in a plain <p>, so the
# tag is unreliable. A block is treated as a header and skipped when ANY of these hold:
#   • its text (after removing a trailing "(...)") matches a known header phrase such as
#     "Education", "Key Qualifications & Skills", "Technical Competencies", "Requirements"; or
#   • it ends with ":" and is short (e.g. "Skills to be developed for Intern:"); or
#   • it is ALL-CAPS and short; or is only a "(...)" note; or
#   • it is a very short (<= 3 words) bold or heading-tag label.
# The Target Audience is the FIRST remaining block that reads like real content:
# >= 4 words and containing lower-case letters. Fallbacks progressively relax this.
# Long lines are trimmed to ~220 characters; if only headers exist, it shows "—".
BLOCK_RE = re.compile(r"(?is)<(p|li|h[1-6]|div|tr)\b[^>]*>(.*?)</\1>")
BOLD_RE = re.compile(r"(?is)<(?:strong|b|u)\b[^>]*>(.*?)</(?:strong|b|u)>")
MARKER_RE = re.compile(r"^(?:[•●▪‣◦·–—\-\*]+|\(?[a-zA-Z0-9]{1,2}[.\)])\s*")


def _plain(fragment):
    """Strip tags/entities from an HTML fragment and drop a leading bullet/marker."""
    text = re.sub(r"<[^>]+>", "", fragment)
    text = html_lib.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return MARKER_RE.sub("", text).strip()


def _bold_ratio(fragment):
    """Fraction of visible, non-bullet characters that sit inside <strong>/<b>/<u>."""
    def squeeze(chunk):
        return re.sub(r"[\s•·●▪‣◦]+", "", html_lib.unescape(re.sub(r"<[^>]+>", "", chunk)))
    total = squeeze(fragment)
    if not total:
        return 0.0
    bold = squeeze("".join(BOLD_RE.findall(fragment)))
    return min(len(bold) / len(total), 1.0)


def _requirement_blocks(raw):
    """List of (text, is_heading_tag, bold_ratio) for each block in the HTML."""
    if not raw:
        return []
    norm = re.sub(r"(?i)<\s*br\s*/?>", "</p><p>", raw)
    norm = re.sub(r"(?i)</?(ul|ol)[^>]*>", "", norm)
    blocks = []
    for tag, inner in BLOCK_RE.findall(norm):
        text = _plain(inner)
        if text:
            blocks.append((text, bool(re.fullmatch(r"h[1-6]", tag.lower())),
                           _bold_ratio(inner)))
    if not blocks:  # no block-level tags — fall back to line splitting
        for line in _plain(re.sub(r"(?i)</?p[^>]*>", "\n", norm)).split("\n"):
            if line.strip():
                blocks.append((line.strip(), False, 0.0))
    return blocks


def _norm_header(text):
    text = text.replace("’", "'").replace("‘", "'")
    text = re.sub(r"\s*\([^)]*\)\s*$", "", text)           # drop a trailing "(...)"
    return re.sub(r"\s+", " ", text).strip().strip(":").strip().lower()


def _is_heading(text, is_htag, bold_ratio):
    # Tag-agnostic: some employers put REAL content inside <h*>/<strong> (and some put
    # headers in plain <p>), so the tag alone can't decide. Judge by the TEXT; use the
    # heading tag / bold emphasis only as a tie-breaker for very short labels.
    if _norm_header(text) in HEADER_PHRASES:            # known section label
        return True
    words = text.split()
    label = text.rstrip().rstrip(":").rstrip()          # text before a trailing colon
    if text.rstrip().endswith(":") and len(label.split()) <= 7:
        return True
    if any(c.isalpha() for c in text) and text.upper() == text and len(words) <= 8:
        return True                                     # short ALL-CAPS label
    if re.fullmatch(r"\(.*\)", text.strip()):           # only a "(...)" note
        return True
    if (is_htag or bold_ratio >= 0.7) and len(words) <= 3:   # tiny emphasised label
        return True
    return False


def _truncate(text, max_len):
    text = text.strip()
    if len(text) <= max_len:
        return text
    return text[:max_len].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


def target_audience(raw, max_len=220):
    """Pick the best one-line Target Audience from a requirement HTML blob."""
    blocks = _requirement_blocks(raw)
    for text, is_htag, bold in blocks:               # ideal: a real content sentence
        if (not _is_heading(text, is_htag, bold)
                and len(text.split()) >= 4 and any(c.islower() for c in text)):
            return _truncate(text, max_len)
    for text, is_htag, bold in blocks:               # relax: any non-heading line
        if not _is_heading(text, is_htag, bold) and len(text.split()) >= 3:
            return _truncate(text, max_len)
    for text, is_htag, bold in blocks:               # last resort: any non-heading line
        if not _is_heading(text, is_htag, bold) and text.strip():
            return _truncate(text, max_len)
    return ""                                         # only headers present -> caller shows "—"


def clean_company(name):
    name = (name or "").strip()
    prev = None
    while name and name != prev:           # strip stacked suffixes like "Co., Ltd."
        prev = name
        name = LEGAL_SUFFIX.sub("", name).strip().rstrip(",")
    return re.sub(r"\s{2,}", " ", name).strip()


# --------------------------------------------------------------------------- #
# Grouping & formatting
# --------------------------------------------------------------------------- #
def group_by_company(jobs):
    """Preserve portal order; return list of (company_dict, [jobs])."""
    order = []
    groups = {}
    for job in jobs:
        comp = job.get("company") or {}
        cid = comp.get("companyid") or comp.get("companyName") or id(job)
        if cid not in groups:
            groups[cid] = (comp, [])
            order.append(cid)
        groups[cid][1].append(job)
    return [groups[cid] for cid in order]


def esc(text):
    """Escape &, <, > so free text is safe inside an HTML-parse-mode message."""
    return html_lib.escape(text or "", quote=False)


def job_link(job):
    """Position title (minus internal "(Sup: …)" notes) linked to its job page."""
    title = SUP_NOTE.sub("", (job.get("jobTitle") or "").strip()).strip()
    title = esc(title) or "—"
    jid = job.get("jobid")
    return f'<a href="{JOB_URL.format(jid)}">{title}</a>' if jid else title


def format_company_block(prefix, company, jobs, audience_len=AUDIENCE_MAX_LEN, bold_name=False):
    name = esc(clean_company(company.get("companyName", "")) or "Company")
    cid = company.get("companyid")
    name_html = f'<a href="{COMPANY_URL.format(cid)}">{name}</a>' if cid else name
    if bold_name:
        name_html = f"<b>{name_html}</b>"

    shown = jobs[:MAX_POSITIONS_PER_COMPANY]
    extra = len(jobs) - len(shown)
    if extra:
        label = f"➕ {extra} more role(s) at this company"
        more = [f'<a href="{COMPANY_URL.format(cid)}">{label}</a>' if cid else label]
    else:
        more = []

    def audience(job):
        return esc(target_audience(job.get("requirement", ""), audience_len)) or "—"

    out = [f"{prefix} {name_html}", ""]
    if len(shown) == 1:
        out += ["• Position(s):", job_link(shown[0])] + more
        out += ["", "• Target Audience:", audience(shown[0])]
    else:
        out.append("• Position(s):")
        out += [f"({chr(97 + i)}) {job_link(job)}" for i, job in enumerate(shown)]
        out += more
        out += ["", "• Target Audience:"]
        out += [f"({chr(97 + i)}) {audience(job)}" for i, job in enumerate(shown)]
    return "\n".join(out)


def build_post(jobs, window, audience_len=AUDIENCE_MAX_LEN):
    start, end = window
    recent = [j for j in jobs if (d := job_date(j)) and start <= d <= end]

    badged = [j for j in recent if (j.get("company") or {}).get("hasBadge")]
    other = [j for j in recent if not (j.get("company") or {}).get("hasBadge")]

    if not recent:
        rng = f"{start.strftime('%d %b')}–{end.strftime('%d %b %Y')}"
        return None, f"No new MaritimeONE job postings were listed for {rng}.", (len(badged), len(other))

    parts = [
        "\U0001f30a #MaritimeMonday: Check out the internship and job opportunities for the week!",
        "",
        "\U0001f4bc JOB OPPORTUNITIES",
    ]

    if badged:
        parts += ["", "Active Employers"]
        for company, comp_jobs in group_by_company(badged):
            parts += ["", format_company_block("✔️", company, comp_jobs, audience_len, bold_name=True)]

    if other:
        parts += ["", "Other job opportunities:"]
        other_groups = group_by_company(other)[:MAX_OTHER_COMPANIES]
        for idx, (company, comp_jobs) in enumerate(other_groups, start=1):
            prefix = NUMBER_EMOJI[idx - 1] if idx <= len(NUMBER_EMOJI) else f"{idx}."
            parts += ["", format_company_block(prefix, company, comp_jobs, audience_len)]

    parts += [
        "",
        f"\U0001f449\U0001f3fc To view more maritime job opportunities, visit our job portal at {PORTAL_LINK}",
    ]
    text = "\n".join(parts)
    return text, text, (len(badged), len(other))


def visible_len(text):
    """Length Telegram enforces: the visible text after HTML <a> tags are parsed out."""
    return len(re.sub(r'<a href="[^"]*">', "", text).replace("</a>", ""))


def build_fitted_post(jobs, window):
    """Build the post, shortening Target Audience lines so it fits one message when it can."""
    text, message, counts = build_post(jobs, window, AUDIENCE_MAX_LEN)
    for audience_len in (70, 55, 45):
        if visible_len(message) <= TELEGRAM_LIMIT - 40:
            break
        text, message, counts = build_post(jobs, window, audience_len)
    return text, message, counts


# --------------------------------------------------------------------------- #
# Telegram
# --------------------------------------------------------------------------- #
def _top_level_blocks(text):
    """Split into whole top-level blocks, keeping each company intact. The Position(s) and
    Target Audience sub-blocks start with "• " and are merged back into their company."""
    blocks = []
    for piece in text.split("\n\n"):
        if piece.startswith("• ") and blocks:
            blocks[-1] += "\n\n" + piece
        else:
            blocks.append(piece)
    return blocks


def split_message(text, limit=TELEGRAM_LIMIT - 40):
    """Fewest messages that each stay within Telegram's visible-char limit, never splitting
    a company block (so an HTML link is never cut in half)."""
    if visible_len(text) <= limit:
        return [text]
    chunks, current = [], ""
    for block in _top_level_blocks(text):
        candidate = f"{current}\n\n{block}" if current else block
        if current and visible_len(candidate) > limit:
            chunks.append(current)
            current = block
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def telegram_send(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for chunk in split_message(text):
        resp = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=30,
        )
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram sendMessage failed: {data}")


def require_env(name):
    value = (os.environ.get(name) or "").strip()
    if not value:
        sys.exit(f"Error: environment variable {name} is not set.")
    return value


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    parser = argparse.ArgumentParser(description="Build/send the weekly MaritimeMonday job post.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="Print the post; no Telegram (default).")
    group.add_argument("--send-draft", action="store_true", help="Build the post and send it to your private review chat.")
    group.add_argument("--publish", action="store_true", help="Build the post and send it to the public channel.")
    args = parser.parse_args()

    window = previous_week_window()
    jobs = fetch_jobs()
    _, message, (n_badged, n_other) = build_fitted_post(jobs, window)
    print(f"[info] Window {window[0].isoformat()} to {window[1].isoformat()}: "
          f"{n_badged} active-employer job(s), {n_other} other job(s).", file=sys.stderr)

    if args.send_draft:
        token = require_env("TELEGRAM_BOT_TOKEN")
        review = require_env("TELEGRAM_REVIEW_CHAT_ID")
        telegram_send(token, review, message)
        print(f"Draft sent to review chat {review}.")
    elif args.publish:
        token = require_env("TELEGRAM_BOT_TOKEN")
        channel = require_env("TELEGRAM_CHANNEL_ID")
        telegram_send(token, channel, message)
        print(f"Published to channel {channel}.")
    else:
        print(message)


if __name__ == "__main__":
    main()
