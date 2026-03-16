import pandas as pd
from data.normalization import normalize_ticker
from data.market_data import _market_cache

def calculate_metrics(df) -> dict:
    """
    Calculate portfolio metrics.

    Returns:
        {
            "total_value": float,
            "positions": [{"ticker", "security_name", "value", "weight_pct", "type_display"}, ...],
            "allocation": {"Stock": pct, "ETF": pct, ...},
        }
    """
    total = df["value"].sum()

    positions = []
    for _, row in df.iterrows():
        ticker = normalize_ticker(row["ticker"] or f"UNKNOWN_{row['security_id']}", aggregate_classes=False)
        weight = (row["value"] / total * 100) if total else 0.0
        positions.append({
            "ticker": ticker,
            "security_name": row["security_name"],
            "value": round(float(row["value"]), 2),
            "weight_pct": round(float(weight), 4),
            "type_display": row["type_display"],
            "quantity": round(float(row["quantity"]), 6),
        })

    # Sort by value descending
    positions.sort(key=lambda p: p["value"], reverse=True)

    # Allocation by type_display
    alloc_raw = df.groupby("type_display")["value"].sum()
    allocation = {k: round(v / total * 100, 4) for k, v in alloc_raw.items()} if total else {}

    return {
        "total_value": round(float(total), 2),
        "positions": positions,
        "allocation": allocation,
    }

def calculate_institutions(df_raw) -> list:
    """Summarize total value per institution from the raw (pre-dedup) DataFrame."""
    df = df_raw.copy()
    df["value"] = pd.to_numeric(df["value"], errors="coerce").fillna(0)
    grouped = df.groupby("institution_name")["value"].sum().reset_index()
    total = grouped["value"].sum()
    grouped["weight_pct"] = (grouped["value"] / total * 100).round(4)
    grouped = grouped.sort_values("value", ascending=False)
    return grouped.to_dict(orient="records")

def calculate_sector_allocation(positions: list) -> dict:
    """
    Calculate portfolio allocation by economic sector.
    Returns: {"Technology": 40.5, "Financial Services": 20.1, ...}
    """
    # Use global market cache to resolve sectors for tickers
    sector_values = {}
    total_market_value = 0

    for p in positions:
        ticker = p.get("ticker")
        val = p.get("value") or 0
        
        # Default to "Other/Unknown" if no sector found
        sector = "Other/Unknown"
        if p.get("type_display") == "Fixed Income":
            sector = "Fixed Income"
        elif p.get("type_display") == "Cash":
            sector = "Cash"
        elif ticker and ticker in _market_cache:
            sector = _market_cache[ticker].get("sector") or "Other/Unknown"

        sector_values[sector] = sector_values.get(sector, 0.0) + val
        total_market_value += val

    if total_market_value == 0:
        return {}

    # Convert to percentages
    return {
        s: round((v / total_market_value) * 100, 2)
        for s, v in sector_values.items()
    }
