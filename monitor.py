"""
Competitor pricing snapshots -> Claude analysis -> daily email digest
---------------------------------------------------------------------
Each morning, diff today's scraped competitor pricing against yesterday's,
filter out noise, ask Claude to interpret the meaningful changes, and
produce an HTML email digest plus a CSV change log.

This file is the demo entry point. It runs end-to-end on bundled JSON
snapshots with zero credentials. Swapping in real scraping + Claude
+ SMTP/SendGrid is a small, well-marked change (see README).

Author: Lucas A. (portfolio sample)
"""
from __future__ import annotations

import csv
import json
import os
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent
INPUT_DIR = PROJECT_ROOT / "sample_snapshots"
OUTPUT_DIR = PROJECT_ROOT / "output"
OUTPUT_HTML = OUTPUT_DIR / "daily_digest.html"
OUTPUT_CSV = OUTPUT_DIR / "changes.csv"
OUTPUT_LOG = OUTPUT_DIR / "run_log.txt"

YESTERDAY = INPUT_DIR / "yesterday.json"
TODAY = INPUT_DIR / "today.json"

USE_REAL_CLAUDE = bool(os.environ.get("ANTHROPIC_API_KEY"))

# Significance thresholds — keep "minor adjustment" noise off the digest.
NOISE_PCT_THRESHOLD = 5.0   # percent change below this is noise
NOISE_DOLLAR_THRESHOLD = 2.0  # absolute change below this is noise

# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass
class Change:
    vendor: str
    tier: str
    kind: str          # price_change | new_tier | removed_tier | scrape_failed
    significance: str  # high | medium | low
    old_price: Optional[float] = None
    new_price: Optional[float] = None
    delta_usd: Optional[float] = None
    delta_pct: Optional[float] = None
    note: str = ""

    # Filled by enrichment
    interpretation: str = ""
    suggested_action: str = ""

    @property
    def is_noise(self) -> bool:
        return self.significance == "low"


# ---------------------------------------------------------------------------
# Step 1 — Diff the two snapshots
# ---------------------------------------------------------------------------


def index_products(snapshot: dict) -> dict[tuple[str, str], dict]:
    """Build {(vendor, tier): product} for fast lookup."""
    out: dict[tuple[str, str], dict] = {}
    for c in snapshot.get("competitors", []):
        for p in c.get("products", []):
            out[(c["vendor"], p["tier"])] = p
    return out


def diff_snapshots(yesterday: dict, today: dict) -> list[Change]:
    changes: list[Change] = []

    y_index = index_products(yesterday)
    t_index = index_products(today)

    # Scrape failures first — they affect every other comparison for that vendor
    y_status = {c["vendor"]: c.get("scrape_status", "?") for c in yesterday.get("competitors", [])}
    t_status = {c["vendor"]: c.get("scrape_status", "?") for c in today.get("competitors", [])}
    for vendor, status in t_status.items():
        if status != "success" and y_status.get(vendor) == "success":
            err = next(
                (c.get("error", "") for c in today.get("competitors", []) if c["vendor"] == vendor),
                "",
            )
            changes.append(Change(
                vendor=vendor, tier="(site)", kind="scrape_failed",
                significance="medium",
                note=err or "Scrape failed today; scraped successfully yesterday.",
            ))

    # Compare every product known in either snapshot
    keys = set(y_index) | set(t_index)
    for key in sorted(keys):
        vendor, tier = key
        # Skip if today's vendor failed to scrape — we can't make claims
        if t_status.get(vendor) != "success":
            continue

        y = y_index.get(key)
        t = t_index.get(key)

        if y and not t:
            changes.append(Change(
                vendor=vendor, tier=tier, kind="removed_tier",
                significance="high",
                old_price=y["price_usd"],
                note=f"Tier removed from {vendor}'s pricing page.",
            ))
        elif t and not y:
            changes.append(Change(
                vendor=vendor, tier=tier, kind="new_tier",
                significance="high",
                new_price=t["price_usd"],
                note=t.get("notes") or f"New tier launched by {vendor}.",
            ))
        elif y and t and y["price_usd"] != t["price_usd"]:
            old, new = y["price_usd"], t["price_usd"]
            delta = round(new - old, 2)
            pct = (delta / old * 100) if old else 100.0
            sig = _score_significance(delta, pct)
            changes.append(Change(
                vendor=vendor, tier=tier, kind="price_change",
                significance=sig,
                old_price=old, new_price=new,
                delta_usd=delta, delta_pct=round(pct, 1),
            ))
    return changes


def _score_significance(delta_usd: float, delta_pct: float) -> str:
    abs_pct = abs(delta_pct)
    abs_usd = abs(delta_usd)
    if abs_pct >= 15 or abs_usd >= 5:
        return "high"
    if abs_pct >= NOISE_PCT_THRESHOLD or abs_usd >= NOISE_DOLLAR_THRESHOLD:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Step 2 — Ask Claude to interpret the meaningful changes
# ---------------------------------------------------------------------------

CHANGE_PROMPT = """You are a competitive intelligence analyst for a SaaS product
called TaskFlow Pro. Given the competitor pricing change below, return STRICT JSON:
  - interpretation: one sentence on what this likely signals (pricing pressure,
    upmarket move, new segment, technical glitch, etc.)
  - suggested_action: one sentence with a concrete next step for our team.

Change:
{change_json}
"""


def enrich_with_claude(changes: list[Change]) -> None:
    for c in changes:
        if c.is_noise:
            continue
        result = _ask_claude(c)
        c.interpretation = result["interpretation"]
        c.suggested_action = result["suggested_action"]


def _ask_claude(change: Change) -> dict:
    if USE_REAL_CLAUDE:
        from anthropic import Anthropic  # type: ignore
        client = Anthropic()
        message = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=300,
            messages=[{
                "role": "user",
                "content": CHANGE_PROMPT.format(change_json=json.dumps(asdict(change), indent=2)),
            }],
        )
        return json.loads(message.content[0].text)
    return _mock_interpret(change)


def _mock_interpret(c: Change) -> dict:
    """Deterministic stand-in for Claude. Mirrors the same output shape."""
    if c.kind == "scrape_failed":
        return {
            "interpretation": (
                f"{c.vendor}'s pricing page returned an error today; could be a "
                "deployment, an outage, or anti-bot rate-limiting."
            ),
            "suggested_action": (
                f"Manually check {c.vendor}'s pricing page in 24h. If it's still "
                "down, the scraper selector may need updating."
            ),
        }
    if c.kind == "new_tier":
        return {
            "interpretation": (
                f"{c.vendor} just opened a new tier (\"{c.tier}\" at "
                f"${c.new_price:,.2f}). Most often this signals a deliberate "
                "move into a new segment — either a free/cheap tier to capture "
                "top-of-funnel, or a premium tier to capture enterprise."
            ),
            "suggested_action": (
                f"Review the new {c.tier} tier's positioning page within 24h. "
                "If it overlaps our TaskFlow Pro target user, consider how we "
                "differentiate or whether we need a matching tier."
            ),
        }
    if c.kind == "removed_tier":
        return {
            "interpretation": (
                f"{c.vendor} removed their {c.tier} tier — typically a sign "
                "they're consolidating SKUs or sunsetting a low-margin product."
            ),
            "suggested_action": (
                f"Check whether existing {c.tier} customers are being "
                "force-migrated up; their dissatisfied users may be reachable."
            ),
        }
    if c.kind == "price_change":
        direction = "drop" if c.delta_usd < 0 else "increase"
        if direction == "drop" and c.significance == "high":
            return {
                "interpretation": (
                    f"{c.vendor} dropped {c.tier} by ${abs(c.delta_usd):,.2f} "
                    f"({c.delta_pct:+.1f}%). A double-digit cut on a paid tier "
                    "usually means they're either reacting to churn or being "
                    "undercut by someone else in the space."
                ),
                "suggested_action": (
                    "Watch our trial-to-paid conversion this week — if the cut "
                    "is a reaction to our growth, we may see pricing pressure "
                    "next. Don't match yet; wait one more week of data."
                ),
            }
        if direction == "increase" and c.significance == "high":
            return {
                "interpretation": (
                    f"{c.vendor} raised {c.tier} by ${c.delta_usd:,.2f} "
                    f"({c.delta_pct:+.1f}%). Increases of this size on a top "
                    "tier typically validate that the segment will bear higher "
                    "pricing."
                ),
                "suggested_action": (
                    "If we're considering an upmarket pricing move, this is "
                    "supporting evidence. Worth modeling a 10-15% lift on our "
                    "equivalent tier."
                ),
            }
        return {
            "interpretation": (
                f"{c.vendor} adjusted {c.tier} by ${c.delta_usd:+,.2f} "
                f"({c.delta_pct:+.1f}%) — meaningful but not dramatic."
            ),
            "suggested_action": "Note and monitor. No immediate action needed.",
        }
    return {"interpretation": "(no analysis)", "suggested_action": ""}


# ---------------------------------------------------------------------------
# Step 3 — Render HTML digest + CSV
# ---------------------------------------------------------------------------


def render_html(changes: list[Change], today_snap: dict, yesterday_snap: dict) -> str:
    today_date = today_snap.get("scraped_at", "")[:10]
    yesterday_date = yesterday_snap.get("scraped_at", "")[:10]
    high = [c for c in changes if c.significance == "high"]
    medium = [c for c in changes if c.significance == "medium"]
    low = [c for c in changes if c.significance == "low"]

    css = """
      body { font-family: -apple-system, system-ui, sans-serif; color: #0f172a;
             max-width: 640px; margin: 0 auto; padding: 24px;
             background: #f8fafc; }
      .card { background: white; border-radius: 8px; padding: 20px;
              margin-bottom: 12px; border: 1px solid #e2e8f0;
              box-shadow: 0 1px 2px rgba(0,0,0,0.04); }
      h1 { font-size: 22px; margin: 0 0 4px; }
      h2 { font-size: 16px; margin: 24px 0 8px; color: #334155; }
      .meta { color: #64748b; font-size: 13px; margin-bottom: 16px; }
      .badge { display: inline-block; font-size: 11px; font-weight: 600;
               padding: 2px 8px; border-radius: 999px; margin-left: 6px; }
      .badge.high { background: #fee2e2; color: #991b1b; }
      .badge.medium { background: #fef3c7; color: #92400e; }
      .badge.low { background: #e0e7ff; color: #3730a3; }
      .vendor { font-weight: 600; }
      .delta { font-variant-numeric: tabular-nums; font-weight: 500; }
      .delta.up { color: #b91c1c; }
      .delta.down { color: #15803d; }
      .action { color: #0f172a; background: #f1f5f9; padding: 8px 12px;
                border-left: 3px solid #0f172a; margin-top: 8px;
                font-size: 14px; border-radius: 0 4px 4px 0; }
      .footer { color: #64748b; font-size: 12px; margin-top: 24px;
                text-align: center; }
    """

    parts = [
        "<!doctype html>", "<html><head><meta charset='utf-8'>",
        "<style>", css, "</style></head><body>",
        "<div class='card'>",
        f"<h1>TaskFlow Pro — Competitive Pricing Digest</h1>",
        f"<div class='meta'>{today_date}  ·  diffed against {yesterday_date}  ·  "
        f"{len(high)} high-signal, {len(medium)} medium, {len(low)} filtered as noise</div>",
        "</div>",
    ]

    if high:
        parts.append("<h2>:rotating_light: High-signal changes</h2>".replace(":rotating_light:", "🚨"))
        for c in high:
            parts.append(_render_change_card(c))
    if medium:
        parts.append("<h2>Medium signal</h2>")
        for c in medium:
            parts.append(_render_change_card(c))
    if not high and not medium:
        parts.append("<div class='card'>:white_check_mark: No meaningful changes today.</div>".replace(":white_check_mark:", "✅"))

    if low:
        names = ", ".join(f"{c.vendor} {c.tier} ({c.delta_pct:+.1f}%)" for c in low)
        parts.append(
            "<div class='card' style='color:#64748b;font-size:13px'>"
            f"<b>Filtered as noise:</b> {names}"
            "</div>"
        )

    parts.append(
        "<div class='footer'>"
        "Generated automatically by your competitor pricing monitor. "
        "Reply to mute a vendor or change the threshold."
        "</div>"
    )
    parts.append("</body></html>")
    return "\n".join(parts)


def _render_change_card(c: Change) -> str:
    if c.kind == "price_change":
        direction = "down" if c.delta_usd < 0 else "up"
        arrow = "↓" if c.delta_usd < 0 else "↑"
        delta_html = (
            f"<span class='delta {direction}'>"
            f"${c.old_price:,.2f} → ${c.new_price:,.2f} "
            f"({arrow}{abs(c.delta_pct):.1f}%)</span>"
        )
        headline = f"<span class='vendor'>{c.vendor}</span> · {c.tier} · {delta_html}"
    elif c.kind == "new_tier":
        headline = (
            f"<span class='vendor'>{c.vendor}</span> · NEW tier "
            f"\"{c.tier}\" at ${c.new_price:,.2f}"
        )
    elif c.kind == "removed_tier":
        headline = (
            f"<span class='vendor'>{c.vendor}</span> · removed "
            f"\"{c.tier}\" (was ${c.old_price:,.2f})"
        )
    elif c.kind == "scrape_failed":
        headline = (
            f"<span class='vendor'>{c.vendor}</span> · pricing page "
            "could not be scraped"
        )
    else:
        headline = f"<span class='vendor'>{c.vendor}</span> · {c.tier}"

    parts = [
        "<div class='card'>",
        f"<div>{headline}<span class='badge {c.significance}'>{c.significance}</span></div>",
    ]
    if c.interpretation:
        parts.append(f"<p style='margin:8px 0 0;color:#475569'>{c.interpretation}</p>")
    if c.suggested_action:
        parts.append(f"<div class='action'><b>Suggested action:</b> {c.suggested_action}</div>")
    if c.note and not c.interpretation:
        parts.append(f"<p style='margin:8px 0 0;color:#475569'>{c.note}</p>")
    parts.append("</div>")
    return "\n".join(parts)


def write_csv(changes: list[Change], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "vendor", "tier", "kind", "significance",
            "old_price_usd", "new_price_usd", "delta_usd", "delta_pct",
            "interpretation", "suggested_action", "note",
        ])
        for c in changes:
            w.writerow([
                c.vendor, c.tier, c.kind, c.significance,
                f"{c.old_price:.2f}" if c.old_price is not None else "",
                f"{c.new_price:.2f}" if c.new_price is not None else "",
                f"{c.delta_usd:+.2f}" if c.delta_usd is not None else "",
                f"{c.delta_pct:+.1f}" if c.delta_pct is not None else "",
                c.interpretation, c.suggested_action, c.note,
            ])


def send_email(html: str, subject: str) -> None:
    """Send the digest. In production:

        import smtplib
        from email.mime.text import MIMEText
        msg = MIMEText(html, "html")
        msg["Subject"] = subject
        msg["From"] = os.environ["DIGEST_FROM"]
        msg["To"] = os.environ["DIGEST_TO"]
        with smtplib.SMTP_SSL(os.environ["SMTP_HOST"], 465) as s:
            s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
            s.send_message(msg)
    """
    if os.environ.get("SMTP_HOST"):
        pass  # real send path documented above


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    log: list[str] = []

    def lg(msg: str) -> None:
        stamped = f"[{datetime.utcnow().strftime('%H:%M:%S')}] {msg}"
        print(stamped)
        log.append(stamped)

    lg(f"Loading snapshots: {YESTERDAY.name} and {TODAY.name}")
    yesterday = json.loads(YESTERDAY.read_text())
    today = json.loads(TODAY.read_text())

    lg("Diffing snapshots")
    changes = diff_snapshots(yesterday, today)
    counts = {"high": 0, "medium": 0, "low": 0}
    for c in changes:
        counts[c.significance] += 1
    lg(f"Found {len(changes)} changes  ·  high={counts['high']}, medium={counts['medium']}, low={counts['low']}")

    mode = "real Claude API" if USE_REAL_CLAUDE else "mock interpreter (set ANTHROPIC_API_KEY for real run)"
    lg(f"Enriching meaningful changes with {mode}")
    enrich_with_claude(changes)

    lg(f"Writing HTML digest -> {OUTPUT_HTML.name}")
    html = render_html(changes, today, yesterday)
    OUTPUT_HTML.write_text(html, encoding="utf-8")

    lg(f"Writing CSV -> {OUTPUT_CSV.name}")
    write_csv(changes, OUTPUT_CSV)

    if os.environ.get("SMTP_HOST"):
        lg("Sending email")
        send_email(html, f"Pricing digest — {today.get('scraped_at','')[:10]}")
    else:
        lg("Skipping email send (no SMTP_HOST set)")

    OUTPUT_LOG.write_text("\n".join(log), encoding="utf-8")
    lg("Run complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
