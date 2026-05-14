#!/usr/bin/env python3
"""
Literature Monitoring Agent for Dr. William H. Hudson
Fetches new articles from immunology/cancer journals, filters for relevance
using Claude, and sends a daily HTML email digest.
"""

import os
import json
import smtplib
import hashlib
import logging
import feedparser
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
import anthropic

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
RECIPIENT_EMAIL = ["william.hudson@bcm.edu", "Colby.Hofferek@bcm.edu", "Dylan.Pfannenstiel@bcm.edu", "angela.addison@bcm.edu", "maria.stegantseva@bcm.edu", "oscar.romero@bcm.edu", 
                  "sean.hyslop@bcm.edu", "amanda.xia@bcm.edu"]
SENDER_EMAIL    = os.environ["GMAIL_ADDRESS"]       # set in GitHub secrets
GMAIL_APP_PASS  = os.environ["GMAIL_APP_PASSWORD"]  # set in GitHub secrets
ANTHROPIC_KEY   = os.environ["ANTHROPIC_API_KEY"]   # set in GitHub secrets
SEEN_FILE      = "seen_articles.json"  # committed to repo after each run
LOOKBACK_HOURS = 48                    # catch any articles missed yesterday
PRUNE_DAYS     = 90                    # drop seen entries older than this
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# RSS / Atom feeds, organized by tier
#
# Tier 1 - Broad net: include if *possibly* relevant
# Tier 2 - Standard:  include if *clearly* relevant
# Tier 3 - Strict:    include only if *highly* relevant, strong match required
# ---------------------------------------------------------------------------

FEEDS = {
    # --- Tier 1 ---
    "Nature":             ("https://www.nature.com/nature.rss",                          1),
    "Science":            ("https://www.science.org/rss/news_current.xml",               1),
    "Cell":               ("https://www.cell.com/cell/inpress.rss",                      1),
    "Nature Immunology":  ("https://www.nature.com/ni.rss",                              1),
    "Immunity":           ("https://www.cell.com/immunity/inpress.rss",                  1),
    "Science Immunology": ("https://www.science.org/journal/sciimmunol/rss",             1),
    "JEM":                ("https://rupress.org/jem/rss/ahead",                          1),

    # --- Tier 2 ---
    "PNAS":                         ("https://www.pnas.org/rss/current.xml",                           2),
    "Nature Communications":        ("https://www.nature.com/ncomms.rss",                              2),
    "JCI":                          ("https://www.jci.org/feed/rss",                                   2),
    "Cancer Cell":                  ("https://www.cell.com/cancer-cell/inpress.rss",                   2),
    "Journal of Clinical Oncology": ("https://ascopubs.org/action/showFeed?type=etoc&feed=rss&jc=jco", 2),
    "Nature Cancer":                ("https://www.nature.com/natcancer.rss",                           2),
    "Cancer Discovery":             ("https://aacrjournals.org/cancerdiscovery/rss/ahead",             2),
    "Nature Methods":               ("https://www.nature.com/nmeth.rss",                               2),
    "Nature Biotechnology":         ("https://www.nature.com/nbt.rss",                                 2),
    "bioRxiv (Immunology)":         ("https://connect.biorxiv.org/biorxiv_xml.php?subject=immunology", 2),
    "bioRxiv (Cancer Biology)":     ("https://connect.biorxiv.org/biorxiv_xml.php?subject=cancer_biology", 2),

    # --- Tier 3 ---
    "Cell Reports":               ("https://www.cell.com/cell-reports/inpress.rss",                         3),
    "Cancer Immunology Research": ("https://aacrjournals.org/cancerimmunolres/rss/ahead",                   3),
    "Frontiers in Immunology":    ("https://www.frontiersin.org/journals/immunology/rss",                   3),
    "Journal of Immunology":      ("https://www.jimmunol.org/rss/current.xml",                              3),
    "Cancer Research":            ("https://aacrjournals.org/cancerres/rss/ahead",                          3),
    "eLife":                      ("https://elifesciences.org/rss/recent.xml",                              3),
    "Clinical Cancer Research":   ("https://aacrjournals.org/clincancerres/rss/ahead",                      3),
    "Cell Systems":               ("https://www.cell.com/cell-systems/inpress.rss",                         3),
    "Genome Biology":             ("https://genomebiology.biomedcentral.com/articles/most-recent/rss.xml",  3),
    "Nucleic Acids Research":     ("https://academic.oup.com/rss/site_5168/3091.xml",                       3),
    "Cell Reports Medicine":      ("https://www.cell.com/cell-reports-medicine/inpress.rss",                3),
    "npj Precision Oncology":     ("https://www.nature.com/npjprecisiononcology.rss",                       3),
    "Mucosal Immunology":         ("https://www.nature.com/mi.rss",                                         3),
    "European Journal of Immunology": ("https://onlinelibrary.wiley.com/feed/15214141/most-recent",         3),
    "bioRxiv (Genomics)":         ("https://connect.biorxiv.org/biorxiv_xml.php?subject=genomics",          3),
}

# Tier thresholds passed to Claude
TIER_INSTRUCTIONS = {
    1: "TIER 1 journal (high-profile, broad scope). Apply a BROAD filter: include this article if there is ANY plausible connection to the researcher's work, even tangential.",
    2: "TIER 2 journal (strong immunology/cancer focus). Apply a STANDARD filter: include if the article is clearly relevant to the researcher's core interests.",
    3: "TIER 3 journal (high-volume or broad-scope). Apply a STRICT filter: include ONLY if the article is a strong, direct match for the researcher's specific focus areas. Err on the side of exclusion.",
}

# ---------------------------------------------------------------------------
# Research profile (passed to Claude as context)
# ---------------------------------------------------------------------------

RESEARCH_PROFILE = """
Dr. Hudson's lab studies CD8+ T cell biology in chronic disease - primarily
T cell exhaustion in cancer and persistent viral infection.

PRIMARY FOCUS AREAS:
- T cell exhaustion: mechanisms, transcriptional/epigenetic regulation,
  differentiation states (stem-like/TCF1+, transitory, terminally exhausted)
- Tumor-infiltrating lymphocytes (TILs): phenotype, function, spatial organization
- Immunotherapy: checkpoint blockade (PD-1/PD-L1), response vs. resistance,
  "cold" tumor microenvironments
- Spatial immunology: Visium, Xenium, spatial TCR sequencing, tissue-level
  immune mapping
- Single-cell multi-omics: scRNA-seq, spectral flow cytometry
- Sex-specific immune signaling: androgen-mediated pathways in T cell
  function and exhaustion
- Antigen-specific T cells: clonal identity, TCR repertoire, tissue niches

DISEASE CONTEXTS:
- Solid tumors: HNSCC, brain metastases, lung cancer, glioma
- Chronic viral infection (LCMV model and human)

KEY MOLECULES / PATHWAYS:
- PD-1, TCF-1 (TCF7), TOX, TIM-3, LAG-3, CD7, CD101, TIGIT
- mTOR signaling in T cell differentiation
- TGF-β in exhaustion and stem-like T cell maintenance
- lncRNA regulation of immune cell fate
- Nrf2/oxidative stress in tumor-immune interactions

TECHNOLOGIES:
- Visium and Xenium spatial transcriptomics
- Spatial TCR clonotype mapping
- Spectral flow cytometry
- Organoid-T cell co-culture systems
- scRNA-seq analysis pipelines

TRANSLATIONAL INTERESTS:
- Biomarkers of immunotherapy response
- Converting immunotherapy-resistant ("cold") tumors
- T cell spatial organization as a predictor of patient outcomes
"""

RELEVANCE_CRITERIA = """
INCLUDE if the article:
- Reports new findings on T cell exhaustion, dysfunction, or stemness
- Studies TILs or antigen-specific T cells in any solid tumor context
- Describes spatial or single-cell methods applicable to immune cell mapping
- Investigates PD-1 pathway biology or checkpoint blockade response/resistance
- Examines how tissue niches or TME shape T cell fate
- Reports sex- or hormone-mediated differences in T cell function or tumor immunity
- Develops spatial transcriptomics, TCR sequencing, or multi-omic immune profiling
- Presents clinical data linking T cell phenotypes to immunotherapy outcomes

EXCLUDE if primarily about:
- B cells, innate immunity, or autoimmunity (unless direct T cell angle)
- Non-immune aspects of cancer biology (unless immunotherapy connection)
- Structural biology unrelated to above
- Non-cancer/non-infection contexts unless directly about exhaustion biology
"""

# ---------------------------------------------------------------------------
# Seen-articles cache (avoids duplicate digests)
# ---------------------------------------------------------------------------

def load_seen() -> set:
    """Load seen article IDs from file, ignoring entries older than PRUNE_DAYS."""
    if not Path(SEEN_FILE).exists():
        return set()
    with open(SEEN_FILE) as f:
        data = json.load(f)
    # Support both old format (list of IDs) and new format (dict of id -> ISO timestamp)
    if isinstance(data, list):
        return set(data)
    cutoff = datetime.now(timezone.utc) - timedelta(days=PRUNE_DAYS)
    return {
        aid for aid, ts in data.items()
        if datetime.fromisoformat(ts) > cutoff
    }


def save_seen(seen_ids: set, existing_timestamps: dict):
    """Save seen IDs with timestamps, pruning entries older than PRUNE_DAYS."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=PRUNE_DAYS)
    now_str = datetime.now(timezone.utc).isoformat()
    merged = {
        aid: ts for aid, ts in existing_timestamps.items()
        if datetime.fromisoformat(ts) > cutoff
    }
    for aid in seen_ids:
        if aid not in merged:
            merged[aid] = now_str
    with open(SEEN_FILE, "w") as f:
        json.dump(merged, f, indent=2)
    log.info(f"Seen cache: {len(merged)} entries (pruned to {PRUNE_DAYS}-day window)")


def article_id(title: str, link: str) -> str:
    return hashlib.md5(f"{title}{link}".encode()).hexdigest()

# ---------------------------------------------------------------------------
# Feed fetching
# ---------------------------------------------------------------------------

def fetch_articles(lookback_hours: int = LOOKBACK_HOURS) -> tuple[list[dict], dict]:
    """Returns (new articles, existing timestamps dict for save_seen)."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)

    existing_timestamps: dict = {}
    if Path(SEEN_FILE).exists():
        with open(SEEN_FILE) as f:
            data = json.load(f)
        if isinstance(data, dict):
            existing_timestamps = data

    seen = load_seen()
    articles = []

    for journal, (url, tier) in FEEDS.items():
        log.info(f"Fetching {journal} (Tier {tier}) ...")
        try:
            feed = feedparser.parse(url)
            if feed.bozo:
                log.warning(f"  Feed parse warning for {journal}: {feed.bozo_exception}")
        except Exception as e:
            log.error(f"  Could not fetch {journal}: {e}")
            continue

        for entry in feed.entries:
            pub = None
            for attr in ("published_parsed", "updated_parsed"):
                if hasattr(entry, attr) and getattr(entry, attr):
                    import time
                    pub = datetime.fromtimestamp(
                        time.mktime(getattr(entry, attr)), tz=timezone.utc
                    )
                    break

            if pub and pub < cutoff:
                continue

            title   = entry.get("title", "").strip()
            link    = entry.get("link", "").strip()
            summary = entry.get("summary", entry.get("description", "")).strip()

            aid = article_id(title, link)
            if aid in seen:
                continue

            articles.append({
                "id":       aid,
                "journal":  journal,
                "tier":     tier,
                "title":    title,
                "link":     link,
                "abstract": summary[:2000],
                "pub_date": pub.strftime("%b %d, %Y") if pub else "recent",
            })

    log.info(f"Fetched {len(articles)} new articles across all feeds.")
    return articles, existing_timestamps

# ---------------------------------------------------------------------------
# Claude relevance filtering
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = f"""You are a scientific literature filter for an immunology research lab.
Your job is to evaluate whether journal articles are relevant to the researcher's work.

RESEARCHER PROFILE:
{RESEARCH_PROFILE}

RELEVANCE CRITERIA:
{RELEVANCE_CRITERIA}

You will receive a list of articles as JSON. Each article includes a "tier_instruction" field
that tells you how strictly to apply the filter for that journal. Follow these tier instructions.

Respond ONLY with valid JSON (no markdown, no preamble) in this exact format:
{{
  "results": [
    {{
      "id": "<article id>",
      "relevant": true or false,
      "borderline": true or false,
      "summary": "<2-3 sentence summary for an expert immunologist: what was studied, the key finding, and the main conclusion or implication. Focus on the science - do not include a sentence about why this is relevant to the lab.>"
    }},
    ...
  ]
}}

Rules:
- relevant=true: passes the tier filter and matches relevance criteria
- borderline=true (and relevant=false): might be of interest but does not clearly pass the tier filter
- relevant=false and borderline=false: exclude entirely
- Summaries must be written for an expert immunologist - use technical terms freely
- If abstract is empty or too short to judge, set relevant=false, borderline=false
"""


def filter_articles(articles: list[dict]) -> tuple[list[dict], list[dict]]:
    """Returns (highlighted, borderline) lists with summaries added."""
    if not articles:
        return [], []

    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    highlighted = []
    borderline  = []

    for i in range(0, len(articles), 20):
        batch = articles[i:i+20]
        payload = [
            {
                "id":               a["id"],
                "journal":          a["journal"],
                "tier_instruction": TIER_INSTRUCTIONS[a["tier"]],
                "title":            a["title"],
                "abstract":         a["abstract"],
            }
            for a in batch
        ]

        log.info(f"Filtering batch {i//20 + 1} ({len(batch)} articles) ...")
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(payload)}],
        )

        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        try:
            result = json.loads(raw)
        except json.JSONDecodeError as e:
            log.error(f"JSON parse error from Claude: {e}\nRaw: {raw[:500]}")
            continue

        result_map = {r["id"]: r for r in result.get("results", [])}
        for art in batch:
            r = result_map.get(art["id"])
            if not r:
                continue
            if r.get("relevant"):
                highlighted.append({**art, "summary": r.get("summary", "")})
            elif r.get("borderline"):
                borderline.append({**art, "summary": r.get("summary", "")})

    log.info(f"Highlighted: {len(highlighted)}, Borderline: {len(borderline)}")
    return highlighted, borderline

# ---------------------------------------------------------------------------
# HTML email construction
# ---------------------------------------------------------------------------

def build_email_html(highlighted: list[dict], borderline: list[dict],
                     total_fetched: int) -> str:
    date_str = datetime.now().strftime("%A, %B %d, %Y")

    def article_card(art: dict, compact: bool = False) -> str:
        link_html = (f'<a href="{art["link"]}" style="color:#1a5276;text-decoration:none;">'
                     f'{art["title"]}</a>') if art["link"] else art["title"]
        tier_colors = {1: "#1a5276", 2: "#1e8449", 3: "#7d6608"}
        tier_labels = {1: "T1", 2: "T2", 3: "T3"}
        tier = art.get("tier", 2)
        tier_badge = (f'<span style="font-size:10px;font-weight:700;color:white;'
                      f'background:{tier_colors.get(tier,"#888")};padding:1px 5px;'
                      f'border-radius:3px;margin-right:6px;font-family:monospace;">'
                      f'{tier_labels.get(tier,"")}</span>')
        meta = (f'<span style="color:#7f8c8d;font-size:13px;">'
                f'{art["journal"]} &bull; {art["pub_date"]}</span>')
        if compact:
            blurb = (f'<p style="margin:4px 0 0;font-size:13px;color:#555;">'
                     f'{art.get("summary","")}</p>') if art.get("summary") else ""
            return (f'<div style="margin-bottom:14px;padding-bottom:14px;'
                    f'border-bottom:1px solid #eee;">'
                    f'<p style="margin:0 0 2px;font-size:14px;font-weight:600;">'
                    f'{tier_badge}{link_html}</p>'
                    f'{meta}{blurb}</div>')
        else:
            summary_html = (
                f'<p style="margin:8px 0 0;font-size:14px;line-height:1.6;color:#333;">'
                f'{art.get("summary","")}</p>'
            ) if art.get("summary") else ""
            return (f'<div style="margin-bottom:24px;padding:16px;background:#f8f9fa;'
                    f'border-left:4px solid {tier_colors.get(tier,"#888")};border-radius:4px;">'
                    f'<p style="margin:0 0 4px;font-size:15px;font-weight:700;">'
                    f'{tier_badge}{link_html}</p>'
                    f'{meta}{summary_html}</div>')

    highlighted_html = "".join(article_card(a) for a in highlighted) if highlighted else (
        '<p style="color:#888;font-style:italic;">No clearly relevant articles today.</p>'
    )
    borderline_html = "".join(article_card(a, compact=True) for a in borderline) if borderline else ""
    borderline_section = (
        f'<h2 style="font-size:16px;color:#555;border-bottom:1px solid #ddd;'
        f'padding-bottom:6px;margin-top:32px;">May Be of Interest</h2>'
        f'{borderline_html}'
    ) if borderline else ""

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="font-family:Georgia,serif;max-width:700px;margin:0 auto;padding:24px;color:#222;background:#fff;">
  <div style="border-bottom:3px solid #1a5276;padding-bottom:12px;margin-bottom:24px;">
    <h1 style="margin:0;font-size:22px;color:#1a5276;letter-spacing:0.5px;">
      Daily Literature Digest
    </h1>
    <p style="margin:4px 0 0;font-size:14px;color:#888;">{date_str}</p>
  </div>

  <h2 style="font-size:16px;color:#1a5276;border-bottom:1px solid #ddd;padding-bottom:6px;">
    Highlighted Articles ({len(highlighted)})
  </h2>
  {highlighted_html}

  {borderline_section}

  <div style="margin-top:32px;padding-top:12px;border-top:1px solid #eee;
       font-size:12px;color:#aaa;">
    {total_fetched} articles reviewed &bull; {len(highlighted)} highlighted
    &bull; {len(borderline)} borderline &bull; Generated by lit-agent
    <br><span style="margin-top:4px;display:inline-block;">
    <span style="background:#1a5276;color:white;padding:1px 5px;border-radius:3px;font-family:monospace;font-size:10px;">T1</span> broad filter &nbsp;
    <span style="background:#1e8449;color:white;padding:1px 5px;border-radius:3px;font-family:monospace;font-size:10px;">T2</span> standard filter &nbsp;
    <span style="background:#7d6608;color:white;padding:1px 5px;border-radius:3px;font-family:monospace;font-size:10px;">T3</span> strict filter
    </span>
  </div>
</body>
</html>"""

# ---------------------------------------------------------------------------
# Send email via Gmail SMTP
# ---------------------------------------------------------------------------

def send_email(html_body: str, n_highlighted: int):
    date_str = datetime.now().strftime("%b %d, %Y")
    subject  = f"Literature Digest - {date_str} ({n_highlighted} articles)"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = SENDER_EMAIL
    msg["To"] = ", ".join(RECIPIENT_EMAIL)
    msg.attach(MIMEText(html_body, "html"))

    log.info(f"Sending email to {RECIPIENT_EMAIL} ...")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(SENDER_EMAIL, GMAIL_APP_PASS)
        server.sendmail(SENDER_EMAIL, RECIPIENT_EMAIL, msg.as_string())
    log.info("Email sent.")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    log.info("=== Literature Agent starting ===")

    articles, existing_timestamps = fetch_articles()
    total = len(articles)

    if not articles:
        log.info("No new articles found. Sending brief notice.")
        html = build_email_html([], [], 0)
        send_email(html, 0)
        return

    highlighted, borderline = filter_articles(articles)

    seen_ids = set(a["id"] for a in articles)
    save_seen(seen_ids, existing_timestamps)

    html = build_email_html(highlighted, borderline, total)
    send_email(html, len(highlighted))
    log.info("=== Done ===")


if __name__ == "__main__":
    main()
