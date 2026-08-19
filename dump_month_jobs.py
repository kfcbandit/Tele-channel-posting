#!/usr/bin/env python3
"""
dump_month_jobs.py — companion to maritime_monday.py

Fetches the MaritimeONE job portal the same way maritime_monday.py does
(reads the embedded Inertia "data-page" JSON — no fragile HTML scraping),
but instead of building a Telegram post for last week, it dumps EVERY job
posted so far this calendar month into a clean JSON file.

Usage:
    pip install -r requirements.txt   # if you haven't already, from the repo root
    python3 dump_month_jobs.py

Output:
    august_jobs.json (or whatever month it is when you run it) in the
    current folder — send that file back to Claude to turn into HTML.
"""

import html as html_lib
import json
import re
from datetime import date

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


def job_date(job):
    raw = job.get("created_yyyy_mm_dd") or (job.get("created") or "")[:10]
    try:
        return date.fromisoformat(raw)
    except (ValueError, TypeError):
        return None


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
        comp = job.get("company") or {}
        cid = comp.get("companyid")
        out.append({
            "title": clean(job.get("jobTitle")),
            "job_url": JOB_URL.format(job.get("jobid")) if job.get("jobid") else None,
            "company": clean(comp.get("companyName")),
            "company_url": COMPANY_URL.format(cid) if cid else None,
            "active_employer": bool(comp.get("hasBadge")),
            "posted": d.isoformat(),
        })

    out.sort(key=lambda j: j["posted"], reverse=True)

    filename = f"{today.strftime('%B').lower()}_jobs.json"
    with open(filename, "w") as f:
        json.dump(out, f, indent=2)

    n_active = sum(1 for j in out if j["active_employer"])
    print(f"Wrote {len(out)} job(s) posted {month_start.isoformat()}–{today.isoformat()} "
          f"to {filename} ({n_active} from Active Employers, {len(out) - n_active} other).")


if __name__ == "__main__":
    main()
