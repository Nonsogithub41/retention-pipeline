#!/usr/bin/env python3
"""
Retention pipeline — Nordwind e-commerce BI
===========================================
Turns raw transaction data into the files the Power BI dashboard reads,
and prints a JSON summary (the numbers the automation/alert layer watches).

This is the Colab notebook "2.1 Data cleaning & customer prep" consolidated
into one script that runs top to bottom with no one watching.

Usage:
    python retention_pipeline.py --input online_retail.csv --outdir output

Outputs (written to --outdir):
    online_retail_clean.csv      cleaned line-level sales
    customer_rfm_segments.csv    one scored row per customer (Power BI reads this)
    cohort_retention.csv         cohort retention matrix (long format)
    at_risk_customers.csv        the actionable win-back list (At Risk + Lost)
    summary.json                 headline numbers for the alert layer
"""

import argparse
import json
import sys
from datetime import datetime

import pandas as pd

# ---- config: the business rules live here, in one place -------------------
AT_RISK_SEGMENTS = ["At Risk", "Lost"]      # what counts as "at risk"
ALERT_THRESHOLD_GBP = 1_200_000             # alarm trips above this £ at risk


# ---- the segmentation rule (unchanged from the notebook) ------------------
def segment(row):
    r, f = row["R"], row["F"]
    if r >= 4 and f >= 4: return "Champions"        # recent + frequent
    if r >= 3 and f >= 3: return "Loyal"
    if r >= 4 and f <= 2: return "New / Promising"  # recent, not yet frequent
    if r <= 2 and f >= 3: return "At Risk"          # used to buy a lot, gone quiet
    if r <= 2 and f <= 2: return "Lost"
    return "Needs Attention"


def run(input_path, outdir):
    import os
    os.makedirs(outdir, exist_ok=True)
    out = lambda name: os.path.join(outdir, name)

    # --- Step 1-2: load + real dates ---------------------------------------
    df = pd.read_csv(input_path, encoding="ISO-8859-1")
    df["InvoiceDate"] = pd.to_datetime(df["InvoiceDate"])

    # --- Step 3: clean the CustomerID (keep anonymous as <NA>) --------------
    df["CustomerID"] = df["CustomerID"].astype("Int64")

    # --- Step 4: remove non-sales rows -------------------------------------
    cancel = df["InvoiceNo"].astype(str).str.startswith("C")
    df = df[~cancel & (df["Quantity"] > 0) & (df["UnitPrice"] > 0)].copy()
    df = df.drop_duplicates().copy()

    # --- Step 5: revenue + save clean table --------------------------------
    df["Revenue"] = df["Quantity"] * df["UnitPrice"]
    df.to_csv(out("online_retail_clean.csv"), index=False)
    total_revenue = float(df["Revenue"].sum())

    # --- Step 6: customer view ---------------------------------------------
    cust_df = df[df["CustomerID"].notna()].copy()
    customers = cust_df.groupby("CustomerID").agg(
        frequency=("InvoiceNo", "nunique"),
        monetary=("Revenue", "sum"),
        first_purchase=("InvoiceDate", "min"),
        last_purchase=("InvoiceDate", "max"),
    ).reset_index()

    one_time = int((customers["frequency"] == 1).sum())
    repeat = int((customers["frequency"] > 1).sum())

    # --- Step 7: recency ----------------------------------------------------
    snapshot = df["InvoiceDate"].max() + pd.Timedelta(days=1)
    customers["recency"] = (snapshot - customers["last_purchase"]).dt.days

    # --- Step 8: score 1-5 on R, F, M --------------------------------------
    customers["R"] = pd.qcut(customers["recency"], 5, labels=range(5, 0, -1)).astype(int)
    customers["F"] = pd.qcut(customers["frequency"].rank(method="first"), 5, labels=range(1, 6)).astype(int)
    customers["M"] = pd.qcut(customers["monetary"], 5, labels=range(1, 6)).astype(int)

    # --- Step 9: named segments --------------------------------------------
    customers["segment"] = customers.apply(segment, axis=1)
    customers.to_csv(out("customer_rfm_segments.csv"), index=False)

    # --- Step 10: at-risk list + value number ------------------------------
    at_risk = customers[customers["segment"].isin(AT_RISK_SEGMENTS)].copy()
    at_risk = at_risk.sort_values("monetary", ascending=False)
    at_risk.to_csv(out("at_risk_customers.csv"), index=False)
    revenue_at_risk = float(at_risk["monetary"].sum())

    # --- cohort retention matrix (for the heatmap) -------------------------
    c = cust_df.copy()
    c["InvoiceMonth"] = c["InvoiceDate"].dt.to_period("M")
    first = c.groupby("CustomerID")["InvoiceMonth"].min().rename("CohortMonth")
    c = c.join(first, on="CustomerID")
    c["MonthsSince"] = (c["InvoiceMonth"].dt.year - c["CohortMonth"].dt.year) * 12 \
                       + (c["InvoiceMonth"].dt.month - c["CohortMonth"].dt.month)
    grp = c.groupby(["CohortMonth", "MonthsSince"])["CustomerID"].nunique().reset_index(name="ActiveCustomers")
    sizes = grp[grp["MonthsSince"] == 0][["CohortMonth", "ActiveCustomers"]].rename(columns={"ActiveCustomers": "CohortSize"})
    cohort = grp.merge(sizes, on="CohortMonth")
    cohort["RetentionPct"] = (cohort["ActiveCustomers"] / cohort["CohortSize"] * 100).round(1)
    cohort["CohortMonth"] = cohort["CohortMonth"].astype(str)
    cohort.sort_values(["CohortMonth", "MonthsSince"]).to_csv(out("cohort_retention.csv"), index=False)

    # --- summary: the numbers the alert layer watches ----------------------
    seg_counts = customers["segment"].value_counts().to_dict()
    summary = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "data_through": str(df["InvoiceDate"].max().date()),
        "total_revenue": round(total_revenue, 2),
        "customers": int(len(customers)),
        "repeat_customers": repeat,
        "one_time_customers": one_time,
        "repeat_rate_pct": round(repeat / len(customers) * 100, 1),
        "revenue_at_risk": round(revenue_at_risk, 2),
        "at_risk_customers": int(len(at_risk)),
        "segment_counts": {k: int(v) for k, v in seg_counts.items()},
        "alert_threshold": ALERT_THRESHOLD_GBP,
        "alert": revenue_at_risk > ALERT_THRESHOLD_GBP,
    }
    with open(out("summary.json"), "w") as f:
        json.dump(summary, f, indent=2)

    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="online_retail.csv")
    ap.add_argument("--outdir", default="output")
    args = ap.parse_args()

    s = run(args.input, args.outdir)

    # human-readable recap to the log
    print("=" * 46)
    print("RETENTION PIPELINE — RUN COMPLETE")
    print("=" * 46)
    print(f"data through      : {s['data_through']}")
    print(f"total revenue     : GBP {s['total_revenue']:,.0f}")
    print(f"customers         : {s['customers']:,}")
    print(f"repeat rate       : {s['repeat_rate_pct']}%")
    print(f"revenue at risk   : GBP {s['revenue_at_risk']:,.0f}  ({s['at_risk_customers']:,} customers)")
    print(f"alert (> GBP {s['alert_threshold']:,}) : {'YES — notify' if s['alert'] else 'no'}")
    print("-" * 46)
    for seg in ["Champions", "Loyal", "At Risk", "Lost", "Needs Attention", "New / Promising"]:
        if seg in s["segment_counts"]:
            print(f"  {seg:<16}: {s['segment_counts'][seg]:>5,}")
    print("=" * 46)
    # also emit the JSON on one line (handy for n8n / GitHub Actions to parse)
    print("SUMMARY_JSON=" + json.dumps(s))
