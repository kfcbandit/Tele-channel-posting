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
GUIDELINE comment above target_audience(). Because links use HTML, the saved draft in
out/latest_draft.txt contains <a href="...">...</a> tags: when hand-editing it, change
the visible words and leave the tags intact.

Modes (CLI):
  --dry-run       Build and PRINT the post. No Telegram calls. (default)
  --send-draft    Build, send the post to your review chat, save it to out/latest_draft.txt
  --publish       Read out/latest_draft.txt and post it to the channel

Environment variables:
  TELEGRAM_BOT_TOKEN       Bot token from @BotFather            (needed for --send-draft / --publish)
  TELEGRAM_REVIEW_CHAT_ID  Your private chat id with the bot    (needed for --send-draft)
  TELEGRAM_CHANNEL_ID      Channel @username or -100... id      (needed for --publish)
  PORTAL_URL               Override the job listing URL         (optional)
  TIMEZONE                 IANA tz for the week window          (optional, default Asia/Singapore)
  DRAFT_PATH               Where the draft is saved/read        (optional, default out/latest_draft.txt)
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
DRAFT_PATH = os.environ.get("DRAFT_PATH", os.path.join("out", "latest_draft.txt"))
PORTAL_LINK = "https://www.maritimeone.sg/job-listing"
JOB_URL = "https://www.maritimeone.sg/job-detail/{}"
COMPANY_URL = "https://www.maritimeone.sg/company-detail/{}"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
)

NUMBER_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣",
                "6️⃣", "7️⃣", "8️⃣", "9️⃣", "\U0001f51f"]

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
}

LEGAL_SUFFIX = re.compile(
    r"[\s,]+(pte\.?\s*ltd\.?|private\s+limited|pte\.?\s*limited|"
    r"co\.?,?\s*ltd\.?|company\s+limited|limited|l\.?l\.?p\.?|l\.?l\.?c\.?|inc\.?)\.?\s*$",
    re.IGNORECASE,
)


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
# HTML. The requirement is split into blocks (each <p>, <li>, <h*> or <div>). A block
# is treated as a SECTION HEADER and skipped when ANY of these is true:
#   • it is an <h1>-<h6> heading; or
#   • (almost) all its text is bold/underlined — e.g. <strong>Requirements:</strong>.
#     A leading bold bullet like "<strong>• </strong>" does NOT count as bold text; or
#   • its text (after removing a trailing "(...)") matches a known header phrase such as
#     "Education", "Key Qualifications & Skills", "Technical Competencies"; or
#   • it ends with ":" and is short; or is ALL-CAPS and short; or is only "(...)".
# The Target Audience is the FIRST remaining block that reads like real content:
# >= 4 words and containing lower-case letters. Fallbacks progressively relax this so
# something sensible is always returned. Long lines are trimmed to ~220 characters.
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
    if is_htag or bold_ratio >= 0.7:
        return True
    if _norm_header(text) in HEADER_PHRASES:
        return True
    words = text.split()
    label = text.rstrip().rstrip(":").rstrip()          # text before a trailing colon
    if text.rstrip().endswith(":") and len(label.split()) <= 7:
        return True
    if any(c.isalpha() for c in text) and text.upper() == text and len(words) <= 8:
        return True
    if re.fullmatch(r"\(.*\)", text.strip()):
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
    """Position title as an <a> link to its job page (plain text if no id)."""
    title = esc((job.get("jobTitle") or "").strip()) or "—"
    jid = job.get("jobid")
    return f'<a href="{JOB_URL.format(jid)}">{title}</a>' if jid else title


def format_company_block(prefix, company, jobs):
    name = esc(clean_company(company.get("companyName", "")) or "Company")
    cid = company.get("companyid")
    name_html = f'<a href="{COMPANY_URL.format(cid)}">{name}</a>' if cid else name

    out = [f"{prefix} {name_html}", ""]
    if len(jobs) == 1:
        out += [
            "• Position(s):",
            job_link(jobs[0]),
            "",
            "• Target Audience:",
            esc(target_audience(jobs[0].get("requirement", ""))) or "—",
        ]
    else:
        out.append("• Position(s):")
        for i, job in enumerate(jobs):
            out.append(f"({chr(97 + i)}) {job_link(job)}")
        out.append("")
        out.append("• Target Audience:")
        for i, job in enumerate(jobs):
            out.append(f"({chr(97 + i)}) {esc(target_audience(job.get('requirement', ''))) or '—'}")
    return "\n".join(out)


def build_post(jobs, window):
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
            parts += ["", format_company_block("✔️", company, comp_jobs)]

    if other:
        parts += ["", "Other job opportunities:"]
        for idx, (company, comp_jobs) in enumerate(group_by_company(other), start=1):
            prefix = NUMBER_EMOJI[idx - 1] if idx <= len(NUMBER_EMOJI) else f"{idx}."
            parts += ["", format_company_block(prefix, company, comp_jobs)]

    parts += [
        "",
        f"\U0001f449\U0001f3fc To view more maritime job opportunities, visit our job portal at {PORTAL_LINK}",
    ]
    text = "\n".join(parts)
    return text, text, (len(badged), len(other))


# --------------------------------------------------------------------------- #
# Telegram
# --------------------------------------------------------------------------- #
def split_message(text, limit=4000):
    if len(text) <= limit:
        return [text]
    chunks, current = [], ""
    for block in text.split("\n\n"):
        addition = ("\n\n" if current else "") + block
        if len(current) + len(addition) > limit:
            if current:
                chunks.append(current)
                current = ""
            if len(block) > limit:
                for i in range(0, len(block), limit):
                    chunks.append(block[i:i + limit])
            else:
                current = block
        else:
            current += addition
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
    group.add_argument("--send-draft", action="store_true", help="Send draft to your review chat + save it.")
    group.add_argument("--publish", action="store_true", help="Publish the saved draft to the channel.")
    args = parser.parse_args()

    if args.publish:
        if not os.path.exists(DRAFT_PATH):
            sys.exit(f"Error: no saved draft at {DRAFT_PATH}. Run --send-draft first.")
        with open(DRAFT_PATH, encoding="utf-8") as fh:
            draft = fh.read().strip()
        if not draft:
            sys.exit("Error: saved draft is empty; nothing to publish.")
        token = require_env("TELEGRAM_BOT_TOKEN")
        channel = require_env("TELEGRAM_CHANNEL_ID")
        telegram_send(token, channel, draft)
        print(f"Published saved draft to channel {channel}.")
        return

    window = previous_week_window()
    jobs = fetch_jobs()
    post_text, message, (n_badged, n_other) = build_post(jobs, window)
    rng = f"{window[0].isoformat()} to {window[1].isoformat()}"
    print(f"[info] Window {rng}: {n_badged} active-employer job(s), {n_other} other job(s).",
          file=sys.stderr)

    if args.send_draft:
        token = require_env("TELEGRAM_BOT_TOKEN")
        review = require_env("TELEGRAM_REVIEW_CHAT_ID")
        telegram_send(token, review, message)
        os.makedirs(os.path.dirname(DRAFT_PATH) or ".", exist_ok=True)
        with open(DRAFT_PATH, "w", encoding="utf-8") as fh:
            fh.write(post_text or "")   # empty file when there were no jobs
        print(f"Draft sent to review chat {review} and saved to {DRAFT_PATH}.")
    else:
        print(message)


if __name__ == "__main__":
    main()
