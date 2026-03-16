import pandas as pd
from data.normalization import normalize_ticker, CASH_TICKERS, FIXED_INCOME_TICKERS, MUTUAL_FUND_TICKERS

_TAX_RULES = [
    ("Roth", "Tax-Exempt (Roth)"),  # must precede IRA so "Roth IRA" → exempt
    ("IRA",  "Tax-Deferred"),
    ("401",  "Tax-Deferred"),
]

def _classify_account(account_name: str) -> str:
    for pattern, bucket in _TAX_RULES:
        if pattern in account_name:
            return bucket
    return "Taxable"

def calculate_tax_buckets(df_raw) -> dict:
    """
    Group accounts into tax buckets based on account_name patterns.

    Returns a dict with total_value and per-bucket breakdown including
    accounts and their holdings sorted by value desc.
    """
    df = df_raw.copy()
    df["value"] = pd.to_numeric(df["value"], errors="coerce").fillna(0)

    total_value = float(df["value"].sum())
    buckets: dict = {}

    for account_name, acct_df in df.groupby("account_name"):
        bucket_label = _classify_account(str(account_name))
        acct_value = float(acct_df["value"].sum())
        institution = str(acct_df["institution_name"].iloc[0]) if len(acct_df) else ""

        has_cost_basis = "cost_basis" in acct_df.columns
        holdings = []
        for _, row in acct_df.iterrows():
            if float(row["value"]) < 0.01:
                continue
            
            ticker = normalize_ticker(str(row["ticker"]) or f"UNKNOWN_{row['security_id']}")
            
            # Apply asset class overrides consistently
            type_display = str(row["type_display"])
            if ticker in CASH_TICKERS: type_display = "Cash"
            elif ticker in FIXED_INCOME_TICKERS: type_display = "Fixed Income"
            elif ticker in MUTUAL_FUND_TICKERS: type_display = "Mutual Fund"

            holdings.append({
                "ticker": ticker,
                "security_name": str(row["security_name"]),
                "quantity": round(float(pd.to_numeric(row["quantity"], errors="coerce") or 0), 6),
                "value": round(float(row["value"]), 2),
                "cost_basis": round(float(cb), 2) if has_cost_basis and not pd.isna(cb := pd.to_numeric(row["cost_basis"], errors="coerce")) else None,
                "type_display": type_display,
            })
        
        holdings.sort(key=lambda h: h["value"], reverse=True)

        account_entry = {
            "account_name": str(account_name),
            "institution_name": institution,
            "value": round(acct_value, 2),
            "holdings": holdings,
        }

        if bucket_label not in buckets:
            buckets[bucket_label] = {"value": 0.0, "accounts": []}
        buckets[bucket_label]["value"] += acct_value
        buckets[bucket_label]["accounts"].append(account_entry)

    # Sort accounts within each bucket by value desc
    for label, bucket in buckets.items():
        bucket["value"] = round(bucket["value"], 2)
        bucket["weight_pct"] = round(bucket["value"] / total_value * 100, 4) if total_value else 0.0
        bucket["accounts"].sort(key=lambda a: a["value"], reverse=True)

    # Sort buckets by value desc
    sorted_buckets = dict(
        sorted(buckets.items(), key=lambda x: x[1]["value"], reverse=True)
    )

    return {
        "total_value": round(total_value, 2),
        "buckets": sorted_buckets,
    }
