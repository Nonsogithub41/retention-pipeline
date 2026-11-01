#!/usr/bin/env python3
"""
AI insight layer for the retention pipeline (Google Gemini, free tier).
=======================================================================
Runs AFTER retention_pipeline.py. It reads the pipeline's summary.json (and the
top at-risk customers) and asks an LLM (Google Gemini) to write a short,
plain-language weekly briefing for a manager. Writes output/insight.md.

Needs GEMINI_API_KEY in the environment (set as a GitHub Actions secret).
Get a free key at https://aistudio.google.com (no billing required).
If the key or the API call is unavailable, it writes a plain fallback summary
so the workflow never breaks.

Usage:
    python generate_insight.py --outdir output
"""
import argparse
import json
import os


def load_context(outdir):
    with open(os.path.join(outdir, "summary.json")) as f:
        summary = json.load(f)
    top = []
    p = os.path.join(outdir, "at_risk_customers.csv")
    if os.path.exists(p):
        import csv
        with open(p) as f:
            top = list(csv.DictReader(f))[:10]
    return summary, top


def build_prompt(s, top):
    seg_lines = "\n".join(f"- {k}: {v} customers" for k, v in s.get("segment_counts", {}).items())
    top_lines = "\n".join(
        f"- Customer {t.get('CustomerID')}: {t.get('segment')}, {t.get('frequency')} orders, "
        f"GBP {float(t.get('monetary', 0)):,.0f} spent, last seen {t.get('recency')} days ago"
        for t in top
    )
    return f"""You are a customer-retention analyst writing a short weekly briefing for a busy, non-technical manager. Use plain language and specific numbers. No jargon, no markdown headings.

This week's figures (e-commerce store, data through {s.get('data_through')}):
- Total revenue: GBP {s.get('total_revenue'):,.0f}
- Identified customers: {s.get('customers'):,}
- Repeat-customer rate: {s.get('repeat_rate_pct')}%
- Revenue at risk (At Risk + Lost): GBP {s.get('revenue_at_risk'):,.0f} across {s.get('at_risk_customers'):,} customers
- Alert threshold: GBP {s.get('alert_threshold'):,} (alert is {'ON' if s.get('alert') else 'off'})

Customer segments:
{seg_lines}

Top customers to win back (by past spend):
{top_lines}

Write, in under 180 words:
1. Two or three sentences explaining the retention picture this week in plain words.
2. A line "Top 3 actions:" followed by three short, concrete actions the team should take."""


def fallback(s):
    return (
        f"Automated summary (AI step unavailable). Revenue at risk is "
        f"GBP {s.get('revenue_at_risk'):,.0f} across {s.get('at_risk_customers'):,} customers "
        f"(At Risk + Lost), against a threshold of GBP {s.get('alert_threshold'):,}. Total revenue is "
        f"GBP {s.get('total_revenue'):,.0f} from {s.get('customers'):,} identified customers, with a "
        f"{s.get('repeat_rate_pct')}% repeat rate. Win-back effort should start with the highest-spending "
        f"At Risk customers."
    )


def generate(outdir, model):
    s, top = load_context(outdir)
    out_path = os.path.join(outdir, "insight.md")
    header = f"# Weekly retention insight\n\n_Data through {s.get('data_through')}._\n\n"

    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        open(out_path, "w").write(header + fallback(s) + "\n\n_(No GEMINI_API_KEY set; plain fallback used.)_\n")
        print("No API key found; wrote fallback insight.")
        return

    try:
        from google import genai
        client = genai.Client(api_key=key)
        resp = client.models.generate_content(model=model, contents=build_prompt(s, top))
        text = (resp.text or "").strip()
        if not text:
            raise RuntimeError("empty response from the model")
        open(out_path, "w").write(header + text + "\n")
        print("Wrote AI insight.")
    except Exception as e:
        open(out_path, "w").write(header + fallback(s) + f"\n\n_(AI step failed: {e}; plain fallback used.)_\n")
        print(f"AI step failed ({e}); wrote fallback instead.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="output")
    ap.add_argument("--model", default="gemini-2.0-flash")
    args = ap.parse_args()
    generate(args.outdir, args.model)
