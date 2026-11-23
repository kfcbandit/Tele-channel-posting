#!/usr/bin/env python3
"""
dump_month_jobs.py — companion to maritime_monday.py

Fetches the MaritimeONE job portal the same way maritime_monday.py does
(reads the embedded Inertia "data-page" JSON — no fragile HTML scraping),
but instead of building a Telegram post for last week, it dumps EVERY job
posted — or extended by the employer — so far this calendar month into a
clean JSON file. Extended posts are flagged with "extended": true.

Usage:
    pip install -r requirements.txt   # if you haven't already, from the repo root
    python3 dump_month_jobs.py

Output:
    august_jobs.json (or whatever month it is when you run it) in the
    current folder — send that file back to Claude to turn into HTML.
"""

import calendar
import html as html_lib
import json
import re
from datetime import date, timedelta

import requests

PORTAL_URL = "https://www.maritimeone.sg/job-listing"
JOB_URL = "https://www.maritimeone.sg/job-detail/{}"
COMPANY_URL = "https://www.maritimeone.sg/company-detail/{}"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
)


def fetch_jobs():
    resp = requests.get(PORTAL_URL, headers={"User-Agent": USER_AGENT}, timeout=60)
    resp.raise_for_status()
    match = re.search(r'data-page="([^"]*)"', resp.text)
    if not match:
        raise RuntimeError("Could not find embedded job data on the page — "
                            "the portal's structure may have changed.")
    payload = json.loads(html_lib.unescape(match.group(1)))
    jobs = payload.get("props", {}).get("jobs", [])
    if not isinstance(jobs, list):
        raise RuntimeError("Unexpected job data shape on the page.")
    return jobs


def parse_iso(raw):
    try:
        return date.fromisoformat((raw or "")[:10])
    except (ValueError, TypeError):
        return None


def created_date(job):
    return parse_iso(job.get("created_yyyy_mm_dd") or job.get("created"))


def subtract_duration(d, duration):
    """Step back from date d by a listing duration like '1 Month', '3 Months', '14 Days'."""
    m = re.match(r"\s*(\d+)\s*(day|week|month|year)", (duration or "").lower())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2)
    if unit == "day":
        return d - timedelta(days=n)
    if unit == "week":
        return d - timedelta(weeks=n)
    months = n * 12 if unit == "year" else n
    y, mo = divmod(d.year * 12 + d.month - 1 - months, 12)
    mo += 1
    return d.replace(year=y, month=mo, day=min(d.day, calendar.monthrange(y, mo)[1]))


def job_date(job):
    """Date the listing last went live: its original post date, or the date the
    employer last extended it.

    When a post is extended the portal keeps the original `created` date but
    resets the expiry to (extension date + listing duration). So we work the
    extension date back out as expiry - duration, falling back to `modified`
    if the duration can't be parsed. Never earlier than the original post.
    """
    created = created_date(job)
    expiry = parse_iso(job.get("expiry_yyyy_mm_dd"))
    relisted = subtract_duration(expiry, job.get("duration")) if expiry else None
    if relisted is None:
        relisted = parse_iso(job.get("modified"))
    if created and relisted:
        return max(created, relisted)
    return created or relisted


def clean(text):
    return (text or "").strip()


def main():
    today = date.today()
    month_start = today.replace(day=1)

    jobs = fetch_jobs()

    out = []
    for job in jobs:
        d = job_date(job)
        if not d or d < month_start or d > today:
            continue
        created = created_date(job)
        comp = job.get("company") or {}
        cid = comp.get("companyid")
        out.append({
            "title": clean(job.get("jobTitle")),
            "job_url": JOB_URL.format(job.get("jobid")) if job.get("jobid") else None,
            "company": clean(comp.get("companyName")),
            "company_url": COMPANY_URL.format(cid) if cid else None,
            "active_employer": bool(comp.get("hasBadge")),
            "posted": d.isoformat(),
            "originally_posted": created.isoformat() if created else None,
            # True only when the post would have been missed without the
            # extension, i.e. it was first posted before this month.
            "extended": bool(created and created < month_start),
        })

    out.sort(key=lambda j: j["posted"], reverse=True)

    filename = f"{today.strftime('%B').lower()}_jobs.json"
    with open(filename, "w") as f:
        json.dump(out, f, indent=2)

    n_active = sum(1 for j in out if j["active_employer"])
    n_extended = sum(1 for j in out if j["extended"])
    print(f"Wrote {len(out)} job(s) posted or extended {month_start.isoformat()}–{today.isoformat()} "
          f"to {filename} ({n_active} from Active Employers, {len(out) - n_active} other; "
          f"{n_extended} were extensions of older posts).")


if __name__ == "__main__":
    main()
