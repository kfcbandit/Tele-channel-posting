#!/usr/bin/env python3
"""
MaritimeMonday — build the weekly MaritimeONE job post and send it via a Telegram bot.

What it does:
  1. Fetches https://www.maritimeone.sg/job-listing and reads the structured job
     data the page embeds (Laravel/Inertia "data-page" JSON) — no HTML scraping.
  2. Keeps only jobs POSTED IN THE PREVIOUS Mon-Sun week (relative to the run date,
     in Singapore time). E.g. a post built on Mon 22 Jun covers 15-21 Jun.
  3. Splits companies into "Active Employers" (badged) and "Other job opportunities"
     (not badged), and formats the post in the #MaritimeMonday house style.
  4. Sends the result to you as a DRAFT (your private chat with the bot), or
     publishes a previously-saved draft to the public channel.

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

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
)

NUMBER_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣",
                "6️⃣", "7️⃣", "8️⃣", "9️⃣", "\U0001f51f"]

# Lines in the requirement text that are headers/boilerplate, not the actual audience.
BOILERPLATE = re.compile(
    r"^(who we are looking for|what we('re| are)? looking for|requirements?|"
    r"qualifications?|the role|role overview|responsibilities|key responsibilities|"
    r"job (description|requirements?|scope)|about (the role|us|you|the company)|"
    r"we offer|what we offer|benefits?|why join us|your profile|the ideal candidate|"
    r"candidate requirements?|target audience|preferred qualifications?)\s*[:.]?\s*$",
    re.IGNORECASE,
)

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
# Text helpers
# --------------------------------------------------------------------------- #
def html_to_lines(raw):
    """Convert an HTML fragment to a list of clean text lines."""
    if not raw:
        return []
    text = re.sub(r"(?i)<\s*br\s*/?>", "\n", raw)
    text = re.sub(r"(?i)</\s*(p|div|li|tr|h[1-6]|ul|ol)\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_lib.unescape(text)
    lines = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t ]+", " ", line).strip()
        # strip a leading bullet / list marker: •, ·, -, *, (a), 1., a)
        line = re.sub(r"^(?:[•●▪‣◦·–—\-\*]+|\(?[a-zA-Z0-9]{1,2}[\.\)])\s*",
                      "", line).strip()
        if line:
            lines.append(line)
    return lines


def _is_header_line(line):
    """True if a line is a section header/label rather than real audience text."""
    norm = line.replace("’", "'").replace("‘", "'")
    if BOILERPLATE.match(norm):
        return True
    words = line.split()
    if line.endswith(":") and len(words) <= 5:
        return True
    if any(c.isalpha() for c in line) and line.upper() == line and len(words) <= 7:
        return True
    return False


def condense_requirement(raw, max_len=220):
    """Return a one-line 'Target Audience' summary from a requirement HTML blob."""
    lines = html_to_lines(raw)
    chosen = ""
    for line in lines:
        if len(line) < 3 or _is_header_line(line):
            continue
        chosen = line
        break
    if not chosen and lines:
        chosen = lines[0]
    if len(chosen) > max_len:
        cut = chosen[:max_len].rsplit(" ", 1)[0].rstrip(",;:")
        chosen = cut + "…"
    return chosen


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


def format_company_block(prefix, company, jobs):
    name = clean_company(company.get("companyName", "")) or "Company"
    titles = [(j.get("jobTitle") or "").strip() for j in jobs]
    audiences = [condense_requirement(j.get("requirement", "")) for j in jobs]

    out = [f"{prefix} {name}", ""]
    if len(jobs) == 1:
        out.append("• Position(s):")
        out.append(titles[0] or "—")
        out.append("")
        out.append("• Target Audience:")
        out.append(audiences[0] or "—")
    else:
        out.append("• Position(s):")
        for i, title in enumerate(titles):
            out.append(f"({chr(97 + i)}) {title or '—'}")
        out.append("")
        out.append("• Target Audience:")
        for i, aud in enumerate(audiences):
            out.append(f"({chr(97 + i)}) {aud or '—'}")
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
            json={"chat_id": chat_id, "text": chunk, "disable_web_page_preview": True},
            timeout=30,
        )
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram sendMessage failed: {data}")


def require_env(name):
    value = os.environ.get(name)
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
