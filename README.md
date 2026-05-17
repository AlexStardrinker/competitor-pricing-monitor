# Competitor pricing monitor → Claude analysis → daily email digest

A Python automation that watches competitor pricing pages, diffs each day
against the previous one, asks Claude to interpret the meaningful changes,
and ships a clean HTML email each morning with the small set of moves
worth a human looking at.

This is a **portfolio sample** demonstrating a class of automation I build
for clients: a recurring observation loop where the AI doesn't just transform
data, it acts as an analyst — filtering noise, interpreting intent, and
suggesting a next step. The demo runs end-to-end on bundled JSON snapshots
with zero credentials. Swapping in real scraping, real Claude, and real
email is documented below.

---

## What it does

Each morning, the script:

1. **Loads today's competitor pricing snapshot and yesterday's** (in production,
   the snapshots come from a scheduled scraper hitting each competitor's
   `/pricing` page; the demo ships pre-captured JSON snapshots).
2. **Diffs them** at the tier level — new tier launched, tier removed, price
   change, scrape failure.
3. **Scores significance** using a deterministic rule set so a $0.50 rounding
   tweak never makes the morning digest:
   - **high:** price change ≥15% or ≥$5; any new/removed tier
   - **medium:** price change 5–15% or $2–5; scrape failures
   - **low:** below both thresholds — gets quietly grouped at the bottom
4. **Asks Claude to interpret each meaningful change** — one sentence on what
   it likely signals, one sentence on what to do about it.
5. **Writes `output/daily_digest.html`** — a styled, responsive email-ready
   HTML file you can pipe into SMTP, SendGrid, or Postmark unmodified.
6. **Writes `output/changes.csv`** — the full diff for archival / analysis
   downstream.

## Example output (from the bundled sample snapshots)

5 changes found across 6 competitors:

> :rotating_light: **ClickUp · Business**  ·  $19.00 → $15.00 (↓21.1%)
> *A double-digit cut on a paid tier usually means they're either reacting to
> churn or being undercut by someone else in the space.*
> **Suggested action:** Watch our trial-to-paid conversion this week — if the
> cut is a reaction to our growth, we may see pricing pressure next. Don't
> match yet; wait one more week of data.

> :rotating_light: **Linear · NEW tier** "Free" at $0.00
> *Linear just opened a new tier. Most often this signals a deliberate move
> into a new segment — either a free/cheap tier to capture top-of-funnel,
> or a premium tier to capture enterprise.*
> **Suggested action:** Review the new Free tier's positioning page within
> 24h. If it overlaps our target user, consider how we differentiate.

> :rotating_light: **Notion · Enterprise**  ·  $25.00 → $32.00 (↑28.0%)
> *Increases of this size on a top tier typically validate that the segment
> will bear higher pricing.*
> **Suggested action:** If we're considering an upmarket move, this is
> supporting evidence. Worth modeling a 10–15% lift on our equivalent tier.

> ⚠ **Monday.com**  ·  pricing page could not be scraped (HTTP 503)
> *Could be a deployment, an outage, or anti-bot rate-limiting.*

Filtered as noise: Asana Starter ($13.49 → $13.99, +3.7%).

Open `output/daily_digest.html` in a browser to see the actual rendered email.

## Run the demo

```bash
git clone <this repo>
cd competitor-pricing-monitor
python3 monitor.py
```

That's it — no API keys, no installation. Outputs land in `output/`.

## Wire up real APIs

Three swaps to take this to production. All are tagged in the source with
clear comments.

| Integration | Where | What you need |
|-------------|-------|---------------|
| **Scraping** | Replace the JSON snapshot load with a scheduled scraper per competitor (Playwright is the most reliable; falls back to Requests + BeautifulSoup for static pages). Snapshots get archived as JSON per day. | Whatever scraping library you prefer; respect each vendor's robots.txt |
| **Claude analysis** | Already wired — set `ANTHROPIC_API_KEY` and the script swaps from the rule-based mock interpreter to a real Claude call. | Anthropic API key |
| **Email delivery** | `send_email()` has the SMTP code commented and ready. Or swap for SendGrid / Postmark / Resend — they all accept the same HTML. | `SMTP_HOST`, `SMTP_USER`, `SMTP_PASS`, `DIGEST_FROM`, `DIGEST_TO` |

Schedule with cron, GitHub Actions, or any orchestrator:

```cron
0 8 * * *  cd /path/to/repo && python3 monitor.py
```

## Project layout

```
competitor-pricing-monitor/
├── monitor.py                          # Main script (~370 lines, single file)
├── sample_snapshots/
│   ├── yesterday.json                  # T-1 pricing snapshot
│   └── today.json                      # T pricing snapshot
├── output/
│   ├── daily_digest.html               # Email-ready HTML (gets written here)
│   ├── changes.csv                     # Full diff log
│   └── run_log.txt
├── .env.example
└── README.md
```

## Design notes

A few decisions worth calling out:

- **Significance scoring is independent of the LLM.** The thresholds (15%/$5
  for high, 5%/$2 for medium) are pure Python rules. Claude only sees the
  changes that already cleared the noise filter. This makes the noise
  reduction reproducible, debuggable, and free.
- **Claude interprets, doesn't decide.** Every meaningful change ships with
  *both* an interpretation and a suggested action, but the email is built so
  the human reading it is still the one making the call.
- **The mock interpreter is a feature.** The demo runs anywhere without API
  keys, and the deterministic rules double as fallback behavior when the
  Anthropic API has an outage.
- **HTML email is inline-styled.** Most email clients strip `<style>` tags
  or rewrite them, so production renders should inline the CSS via a small
  template engine (premailer is the usual choice). The demo uses a `<style>`
  block for readability in the file viewer.

## What I'd build next (for a real client)

- **Per-vendor mute thresholds** — quarterly fee-resetters like Notion make
  3% adjustments constantly; raise their noise threshold to 8% so they
  don't dominate the medium-signal bucket.
- **Cohort tracking** — store snapshots in SQLite or Postgres and compute
  rolling 30-day average prices. Lets the digest say "first change in 6
  weeks" or "third drop this quarter."
- **Slack delivery alongside email** — pricing intel often gets discussed in
  a #competitive channel; the same HTML can become Slack blocks with minor
  formatting.
- **Watchlist alerts** — push notification (or paging) if a specific tier
  drops below a threshold (e.g., "alert me the second any competitor goes
  below $10 on a Pro tier").
- **Multi-region pricing detection** — many SaaS vendors charge differently
  by region; scrape with rotating IPs and flag when regional pricing
  diverges.

---

**Built by Lucas A.** — data analyst & Python automation specialist.
Available on Fiverr for similar automations: scheduled scrapers, AI-enriched
reports, document processing, data pipelines, email digest systems.
