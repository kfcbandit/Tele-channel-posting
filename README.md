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
                        ├─ "Other opportunities" = everyone else (1️⃣2️⃣3️⃣…)
                        ├─ formats the post in your house style
                        │
                        └─► sends it to YOUR private Telegram chat as a DRAFT
                            and saves it to out/latest_draft.txt

You read the draft on your phone.
  • Happy?  -> run the "MaritimeMonday publish" action -> it posts to the channel.
  • Want edits? -> edit out/latest_draft.txt on GitHub first, then publish.
```

Nothing is ever posted to the public channel automatically — publishing is always
your decision.

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
2. **Review it.** To change anything, open `out/latest_draft.txt` on GitHub, click
   the pencil ✏️, edit, and **Commit changes**.
3. **Publish:** go to the **Actions** tab → **MaritimeMonday publish** → **Run workflow**.
   The bot posts the (edited) draft to your channel.

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

## Good to know

- **Company names** sometimes come in ALL CAPS or with `Pte Ltd` from the portal.
  The script trims `Pte Ltd`-style suffixes; fix any odd casing in the review step.
- **Target Audience** lines are auto-summarised from each job's requirements (first
  meaningful line). They're a solid starting point — tidy them in the draft if needed.
- The script reads the portal's own embedded data (no fragile scraping). If MaritimeONE
  ever rebuilds their site and the script stops finding jobs, that's the thing to revisit.
- `--dry-run` (default) prints only; `--send-draft` sends + saves; `--publish` posts the
  saved draft. The GitHub workflows call the right one for you.
