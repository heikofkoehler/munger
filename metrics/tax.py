import pandas as pd
from data.normalization import normalize_ticker, CASH_TICKERS, FIXED_INCOME_TICKERS, MUTUAL_FUND_TICKERS

_TAX_RULES = [
    ("Roth", "Tax-Exempt (Roth)"),  # must precede IRA so "Roth IRA" → exempt
    ("IRA",  "Tax-Deferred"),
    ("401",  "Tax-Deferred"),
]

REIT_TICKERS = {
    "O", "PLD", "WELL", "VNQ", "AMT", "EQIX", "PSA", "DLR", "CCI", "SPG", "VICI", "AVB", "EQR"
}

def classify_dividend_treatment(ticker: str = "", type_display: str = "", sector: str = "") -> str:
    """
    Classifies dividend tax treatment as 'Qualified' or 'Non-Qualified'.
    - Fixed Income (bonds, Treasuries), Cash (money market sweeps), and REITs
      are Non-Qualified (taxed as regular income).
    - Operating corporate stocks and equity ETFs/mutual funds are Qualified.
    """
    t = (ticker or "").upper().replace(".", "-")
    td = (type_display or "").strip()
    sec = (sector or "").strip()

    if td in ("Fixed Income", "Cash") or t in CASH_TICKERS or t in FIXED_INCOME_TICKERS:
        return "Non-Qualified"
    if sec in ("Real Estate", "Financial Services / BDC") or t in REIT_TICKERS:
        return "Non-Qualified"
    if td in ("Stock", "Equity", "Equity / ETF", "ETF", "Mutual Fund"):
        return "Qualified"
    
    return "Qualified"

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
                "dividend_treatment": classify_dividend_treatment(ticker, type_display),
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

    # Calculate gains summary broken out by Taxable vs. Retirement (Tax-Deferred & Tax-Exempt)
    def _compute_bucket_gains(bucket_names):
        val_with_basis = 0.0
        basis_total = 0.0
        pos_gain = 0.0
        neg_loss = 0.0

        for b_name in bucket_names:
            if b_name not in buckets:
                continue
            for acct in buckets[b_name]["accounts"]:
                for h in acct["holdings"]:
                    if h.get("cost_basis") is not None:
                        val = float(h["value"])
                        basis = float(h["cost_basis"])
                        val_with_basis += val
                        basis_total += basis
                        diff = val - basis
                        if diff >= 0:
                            pos_gain += diff
                        else:
                            neg_loss += diff

        net = pos_gain + neg_loss
        pct = (net / basis_total * 100.0) if basis_total > 0 else 0.0
        return {
            "value_with_basis": round(val_with_basis, 2),
            "cost_basis": round(basis_total, 2),
            "unrealized_gain": round(pos_gain, 2),
            "unrealized_loss": round(neg_loss, 2),
            "net_gain": round(net, 2),
            "gain_pct": round(pct, 2),
        }

    taxable_gains = _compute_bucket_gains(["Taxable"])
    retirement_gains = _compute_bucket_gains(["Tax-Deferred", "Tax-Exempt (Roth)"])

    tot_val_with_basis = taxable_gains["value_with_basis"] + retirement_gains["value_with_basis"]
    tot_basis = taxable_gains["cost_basis"] + retirement_gains["cost_basis"]
    tot_pos_gain = taxable_gains["unrealized_gain"] + retirement_gains["unrealized_gain"]
    tot_neg_loss = taxable_gains["unrealized_loss"] + retirement_gains["unrealized_loss"]
    tot_net = tot_pos_gain + tot_neg_loss
    tot_pct = (tot_net / tot_basis * 100.0) if tot_basis > 0 else 0.0

    total_informational = {
        "value_with_basis": round(tot_val_with_basis, 2),
        "cost_basis": round(tot_basis, 2),
        "unrealized_gain": round(tot_pos_gain, 2),
        "unrealized_loss": round(tot_neg_loss, 2),
        "net_gain": round(tot_net, 2),
        "gain_pct": round(tot_pct, 2),
    }

    return {
        "total_value": round(total_value, 2),
        "buckets": sorted_buckets,
        "gains_summary": {
            "taxable": taxable_gains,
            "retirement": retirement_gains,
            "total_informational": total_informational,
        },
    }
