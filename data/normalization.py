import pandas as pd

TICKER_ALIASES = {
    "GOOG":  "GOOGL", # Alphabet Inc.
    "BRK-A": "BRK-B", # Berkshire Hathaway
    "BRKA":  "BRK-B",
    "BRKB":  "BRK-B",
}

TICKER_OVERRIDES = {
    "UNKNOWN_189993187450742649": "VBTIX", # Vanguard Total Bond Market Index Fund
    "UNKNOWN_189993188208175994": "VFFSX", # Vanguard 500 Index Fund
    "Inst Tot Bd Mkt Ix Tr":      "VBTIX",
    "Instl 500 Index Trust":      "VFFSX",
}

def normalize_ticker(ticker: str, aggregate_classes: bool = False) -> str:
    """
    Normalize ticker symbols to a standard format.
    Converts . to - (e.g., BRK.B -> BRK-B).
    
    If aggregate_classes is True, it will also map different share classes 
    to a single master ticker (e.g., GOOG -> GOOGL).
    """
    if not ticker or not isinstance(ticker, str):
        return ""
    
    # Check manual overrides first
    if ticker in TICKER_OVERRIDES:
        ticker = TICKER_OVERRIDES[ticker]

    t = ticker.strip().upper()
    # Standardize on Yahoo Finance format (hyphen instead of dot/slash)
    t = t.replace(".", "-").replace("/", "-")
    # Remove any extra spaces around the hyphen
    if "-" in t:
        parts = [p.strip() for p in t.split("-")]
        t = "-".join(parts)
    
    # Strip common class suffixes if they don't have a hyphen yet
    # e.g. "BRKB" -> "BRK-B" logic handled above partially, but let's be explicit
    if t == "BRKB": t = "BRK-B"
    if t == "BRKA": t = "BRK-B" if aggregate_classes else "BRK-A"

    if aggregate_classes:
        return TICKER_ALIASES.get(t, t)
    
    return t

def deduplicate(df):
    """
    Deduplicate holdings by ticker (position view).

    Sums quantity and value across accounts. Preserves security_id, security_name,
    type_display from the first occurrence per ticker.
    """
    df = df.copy()
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce").fillna(0)
    df["value"] = pd.to_numeric(df["value"], errors="coerce").fillna(0)
    
    # 1. Apply TICKER_OVERRIDES first (using security_id or raw name)
    # This ensures that items with empty tickers are merged correctly.
    def get_initial_ticker(row):
        t = row.get("ticker") or ""
        if not t or t.startswith("UNKNOWN"):
            # Check if security_id or name is in overrides
            sid = str(row.get("security_id", ""))
            name = str(row.get("security_name", ""))
            return TICKER_OVERRIDES.get(sid, TICKER_OVERRIDES.get(name, t))
        return t

    df["ticker"] = df.apply(get_initial_ticker, axis=1)

    # 2. Normalize tickers for consistent display and merging.
    if "ticker" in df.columns:
        df["ticker"] = df["ticker"].apply(lambda t: normalize_ticker(t, aggregate_classes=False))

    # Identify by ticker. If ticker is empty, use security_id as fallback
    df["group_id"] = df["ticker"].where(df["ticker"] != "", df["security_id"])

    # Metadata to preserve from first occurrence
    meta = df.groupby("group_id")[["ticker", "security_id", "security_name", "type_display"]].first()

    # Summed numeric columns
    numeric_cols = ["quantity", "value"]
    if "cost_basis" in df.columns:
        df["cost_basis"] = pd.to_numeric(df["cost_basis"], errors="coerce").fillna(0)
        numeric_cols.append("cost_basis")
    numeric = df.groupby("group_id")[numeric_cols].sum()

    result = meta.join(numeric).reset_index(drop=True)
    return result

CASH_TICKERS = {"FCASH", "CUR:USD", "SPAXX", "FDRXX"}
FIXED_INCOME_TICKERS = {"VCSH", "VGSH", "BND", "AGG", "VBTIX"}
MUTUAL_FUND_TICKERS = {"VFFSX"}

def normalize_asset_class(df):
    """Normalize type_display based on ticker overrides."""
    df = df.copy()
    df.loc[df["ticker"].isin(CASH_TICKERS), "type_display"] = "Cash"
    df.loc[df["ticker"].isin(FIXED_INCOME_TICKERS), "type_display"] = "Fixed Income"
    df.loc[df["ticker"].isin(MUTUAL_FUND_TICKERS), "type_display"] = "Mutual Fund"
    return df
