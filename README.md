# lit-agent

A daily literature monitoring agent for stem cell and cancer biology research. Scrapes RSS feeds from 35+ journals, uses Claude AI to filter articles for relevance to your research program, and emails a digest every morning.

Built by Will Hudson at BCM, adapted for use

## What it does

Every day at 8 AM, the agent:

1. Fetches new articles from a curated list of journals and bioRxiv feeds
1. Sends each article’s title and abstract to Claude, which evaluates relevance against a detailed research profile
1. Emails a formatted HTML digest with summaries of highlighted articles, a “May Be of Interest” section for borderline articles, and a count of everything reviewed

## How relevance filtering works

Journals are organized into three tiers that control how strictly Claude filters:

- **Tier 1** — broad net; include if there is any plausible connection to the research
- **Tier 2** — standard filter; include if clearly relevant
- **Tier 3** — strict filter; only include strong direct matches

Priority authors and terms can be configured to nudge borderline articles toward inclusion while still applying scientific judgment.

Articles are tracked by ID (an MD5 hash of title + URL) and stored in `seen_articles.json`, which is committed back to the repo after each run. Entries older than 90 days are pruned automatically so the file stays small.

## Setup

### Prerequisites

- A free [GitHub](https://github.com) account
- An [Anthropic API key](https://console.anthropic.com) (pay-per-use; expect ~$0.05–0.20/day)
- A Gmail account with [2-Step Verification](https://myaccount.google.com/security) enabled

### 1. Fork or clone this repository

Make it private if you prefer — the code contains no credentials.

### 2. Get an Anthropic API key

Sign up at [console.anthropic.com](https://console.anthropic.com), add a payment method, and create an API key under **API Keys**.

### 3. Generate a Gmail App Password

Go to [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords), create an app password named `lit-agent`, and copy the 16-character code (remove spaces when saving).

### 4. Add GitHub Secrets

Go to your repo → **Settings → Secrets and variables → Actions → New repository secret** and add:

|Secret name         |Value                                      |
|--------------------|-------------------------------------------|
|`ANTHROPIC_API_KEY` |Your Anthropic API key (`sk-ant-...`)      |
|`GMAIL_ADDRESS`     |Your Gmail address (`you@gmail.com`)       |
|`GMAIL_APP_PASSWORD`|The 16-character App Password (no spaces)  |
|`RECIPIENT_EMAILS`  |Comma-separated list of recipient addresses|

### 5. Enable workflow write permissions

Go to **Settings → Actions → General → Workflow permissions** and select **Read and write permissions**. This allows the agent to commit `seen_articles.json` back to the repo after each run.

### 6. Customize the research profile

Edit `lit_agent.py` and update:

- `RESEARCH_PROFILE` — describe your lab’s focus areas, disease contexts, key molecules, and technologies
- `RELEVANCE_CRITERIA` — define what to include and exclude
- `PRIORITY_AUTHORS` — authors whose work should receive extra weight
- `PRIORITY_TERMS` — keywords that nudge borderline articles toward inclusion
- `FEEDS` — add, remove, or re-tier journals to match your field

### 7. Test it

Go to **Actions → Daily Literature Digest → Run workflow** to trigger a manual run. Check the logs and your inbox. The first run will process the full backlog from all feeds; subsequent runs will only see new articles.

## Schedule

The agent runs daily at 14:00 UTC (9:00 AM CDT / 10:00 AM CST). To change the time, edit the cron line in `.github/workflows/daily_digest.yml`:

```yaml
- cron: "0 14 * * *"   # 9 AM CDT (summer)
- cron: "0 15 * * *"   # 9 AM CST (winter)
```

GitHub’s scheduler can run up to ~30 minutes late during high-demand periods — this is normal.

## Cost

|Service       |Cost                                            |
|--------------|------------------------------------------------|
|Anthropic API |~$0.05–0.20/day depending on article volume     |
|GitHub Actions|Free (uses ~100 of the 2,000 free minutes/month)|
|Gmail         |Free                                            |

## Files

```
lit-agent/
├── lit_agent.py              # main agent script
├── requirements.txt          # Python dependencies (anthropic, feedparser)
├── seen_articles.json        # auto-generated; tracks processed articles
└── .github/
    └── workflows/
        └── daily_digest.yml  # GitHub Actions schedule and workflow
```

## Troubleshooting

**No email received** — check the Actions run log for errors, verify all four secrets are set correctly, and check your spam folder.

**Feed parse warnings** — harmless in most cases; feedparser is strict about XML formatting but still extracts articles. A `text/html` warning means the feed URL is broken and returning a webpage instead.

**`KeyError` for a secret** — the secret name in GitHub doesn’t match what the code expects. Double-check spelling and that the secret was saved before the run.

**Duplicate articles** — if `seen_articles.json` is lost or reset, the next run will reprocess everything. This results in a longer email that one day but no other harm.
