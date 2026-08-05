# MaritimeMonday bot

Automatically builds your weekly `#MaritimeMonday` job post from the
[MaritimeONE Career Portal](https://www.maritimeone.sg/job-listing) and sends it
to you on Telegram every Monday for review before it goes live.

## How it works

```
Every Monday morning ─► GitHub Actions runs the script
                        │
                        ├─ reads new jobs posted LAST WEEK (Mon–Sun)
                        ├─ "Active Employers"  = badged companies (✔️)
                        ├─ "Other opportunities" = everyone else (1️⃣…🔟, max 10)
                        ├─ formats the post in your house style
                        │
                        └─► sends it to YOUR private Telegram chat as a DRAFT

You read the draft on your phone.
  • Happy? -> run the "MaritimeMonday publish" action -> it rebuilds the same
              week's post and sends it to the channel.
```

Nothing is ever posted to the public channel automatically — publishing is always
your decision. Nothing is stored between runs; the draft and the published post are
built from the same week's data, so they match.

## The weekly rule

A post built on **Monday** covers jobs posted the **previous Monday–Sunday**.
Example: a post on **Mon 22 Jun** includes jobs posted **15–21 Jun**.

## One-time setup

### 1. Create the Telegram bot
1. In Telegram, message **@BotFather**, send `/newbot`, follow the prompts.
2. Copy the **bot token** it gives you (looks like `123456:AAE...`). Keep it secret.

### 2. Find your chat ids
1. Open your new bot (search its @username), press **START**, send it `hi`.
2. Add the bot as an **Administrator** of your channel (so it can post there),
   and post anything in the channel.
3. On your computer, run:
   ```bash
   pip install -r requirements.txt
   TELEGRAM_BOT_TOKEN=PASTE_YOUR_TOKEN python3 get_chat_id.py
   ```
   It prints the chats it can see. Note:
   - the **private** chat id  → this is your `TELEGRAM_REVIEW_CHAT_ID`
   - the **channel** id (or just use `@yourchannelusername`) → `TELEGRAM_CHANNEL_ID`

### 3. Put this code on GitHub
1. Create a new (free) repository at <https://github.com/new>.
2. Upload all the files in this folder to it (drag-and-drop works on GitHub).

### 4. Add the three secrets
In your repo: **Settings → Secrets and variables → Actions → New repository secret**.
Add each of these:

| Secret name               | Value                                              |
|---------------------------|----------------------------------------------------|
| `TELEGRAM_BOT_TOKEN`      | the bot token from BotFather                       |
| `TELEGRAM_REVIEW_CHAT_ID` | your private chat id (where drafts are sent)        |
| `TELEGRAM_CHANNEL_ID`     | your channel id, e.g. `@maritimechannel` or `-100…` |

That's it. The Monday schedule is already configured.

## Using it each week

1. **Monday ~8am (SGT):** you receive the draft in your private Telegram chat.
2. **Review it** in Telegram. The output is auto-formatted (see the rules below); if a
   line looks off, it's usually a one-word tweak to the script's lists — see "Good to know".
3. **Publish:** go to the **Actions** tab → **MaritimeMonday publish** → **Run workflow**.
   It rebuilds the same week's post and sends it to your channel. Publishing any day within
   that same Mon–Sun week produces the same post you reviewed.

You can also trigger the draft early anytime: **Actions → MaritimeMonday draft → Run workflow**.

## Preview it locally (optional)

No bot/token needed — just prints what the post would look like:
```bash
pip install -r requirements.txt
python3 maritime_monday.py --dry-run
```

## Changing the schedule time

Edit the `cron` line in [.github/workflows/draft.yml](.github/workflows/draft.yml).
GitHub uses **UTC**. Singapore is UTC+8, so subtract 8 hours:
- `0 0 * * 1`  → Monday 08:00 SGT  (current setting)
- `0 1 * * 1`  → Monday 09:00 SGT
- `30 23 * * 0` → Monday 07:30 SGT (Sunday 23:30 UTC)

## Links

Every **company name** links to its MaritimeONE company page and every **position**
links to its job page, so readers can tap straight through to the listing.

## How the "Target Audience" line is chosen

Each job's requirement text is split into blocks (paragraphs and bullet points), and the
script picks the first block that reads like a real requirement — deliberately **skipping
section headers**. Whether a block is a header is decided from its **text, not its HTML
tag**: some employers wrongly put real requirements inside heading/bold tags, and some put
headers in plain paragraphs, so the tag can't be trusted. A block is skipped as a header
when any of these hold:

- its wording matches a known header phrase — e.g. *Education*, *Qualifications*,
  *Key Qualifications & Skills*, *Technical Competencies*, *Who We Are Looking For*,
  *What We Offer*, *Responsibilities* (a trailing "(…)" is ignored, so
  *Key Qualifications & Skills (…)* is caught too);
- it ends with a colon and is short (e.g. *Skills to be developed for Intern:*);
- it is ALL-CAPS and short, or is only a parenthetical "(…)";
- it is a very short (≤3-word) bold or heading label.

The chosen line is the first **non-header** block with at least 4 words and some lower-case
letters (long lines are trimmed to ~220 characters). If a job's requirement contains *only*
headers with no real text, the line shows `—` for you to fill in.

To tune it, edit the `HEADER_PHRASES` list near the top of
[maritime_monday.py](maritime_monday.py) — add any header wording you keep seeing slip through.

## Good to know

- **Company names** sometimes come in ALL CAPS or with `Pte Ltd` from the portal.
  The script trims `Pte Ltd`-style suffixes; fix any odd casing in the review step.
- **Target Audience** lines are auto-selected using the rules above. If one still reads
  like a header, add that wording to `HEADER_PHRASES` in the script and it won't be picked
  again. If a job lists no real requirement, the line shows `—`.
- Internal notes some employers add to a title (e.g. "(Sup: Serena)") are removed
  automatically.
- Only jobs **posted in the previous Mon–Sun week** are included (nothing older, nothing
  from the current week). The "Other job opportunities" list is capped at **10 companies**
  — change `MAX_OTHER_COMPANIES` near the top of
  [maritime_monday.py](maritime_monday.py) if you want a different number.
- The script reads the portal's own embedded data (no fragile scraping). If MaritimeONE
  ever rebuilds their site and the script stops finding jobs, that's the thing to revisit.
- `--dry-run` (default) prints the post; `--send-draft` sends it to your review chat;
  `--publish` sends it to the channel. Both build fresh from the portal — nothing is stored
  between runs. The GitHub workflows call the right one for you.
