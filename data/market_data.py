import sys
import os
import yfinance as yf
import pandas as pd
from core.database import _yf_db_get, _yf_db_set

_fund_cache: dict = {}  # keyed by ticker

DEFAULT_FUND_EXPENSE_RATIOS: dict = {
    "VOO": 0.0003,
    "VFFSX": 0.0001,
    "SCHF": 0.0006,
    "VCSH": 0.0004,
    "VGSH": 0.0004,
    "VBTIX": 0.00035,
    "SPY": 0.0009,
    "IVV": 0.0003,
    "BND": 0.0003,
    "AGG": 0.0003,
}

def get_fund_details(ticker: str) -> dict:
    """
    Fetch expense ratio and top holdings for a fund ticker.
    Returns: {"expense_ratio": float or None, "holdings": [{"ticker": str, "weight": float}, ...]}
    """
    if ticker in _fund_cache:
        return _fund_cache[ticker]

    holdings = []
    expense_ratio = DEFAULT_FUND_EXPENSE_RATIOS.get(ticker)

    # 1. Check local CSV override for S&P 500 index funds first (guaranteed look-through)
    #    Workspace cache wins; legacy ./vanguard_voo_holdings.csv still honored.
    from core.workspace import resolve_cached_file
    csv_path = str(resolve_cached_file("vanguard_voo_holdings.csv"))
    if ticker in ["VOO", "VFFSX", "SPY", "IVV"] and os.path.exists(csv_path):
        try:
            df_csv = pd.read_csv(csv_path)
            for _, row in df_csv.head(100).iterrows():
                w = float(row["weight_pct"]) / 100.0 if not pd.isna(row["weight_pct"]) else 0.0
                holdings.append({"ticker": str(row["ticker"]), "weight": w})
        except Exception as e:
            print(f"Error reading {csv_path}: {e}", file=sys.stderr)

    # 2. Attempt yfinance enrichment for expense ratio and holdings if needed
    try:
        t = yf.Ticker(ticker)
        info = t.info
        raw_ratio = info.get("netExpenseRatio") or info.get("expenseRatio")
        if raw_ratio is not None:
            expense_ratio = float(raw_ratio) / 100

        if not holdings and hasattr(t, "funds_data") and t.funds_data.top_holdings is not None:
            df_holdings = t.funds_data.top_holdings
            if not df_holdings.empty:
                for symbol, row in df_holdings.iterrows():
                    weight = row.get("Holding Percent") or row.get("Weight") or 0.0
                    holdings.append({"ticker": str(symbol), "weight": float(weight)})
    except Exception:
        # Fallback to defaults already set above
        pass

    res = {"expense_ratio": expense_ratio, "holdings": holdings}
    _fund_cache[ticker] = res
    return res

YFINANCE_SKIP_TICKERS: set = {
    "FCASH", "CUR:USD", "CUR-USD", "USD-USD", "USD", "SPAXX", "FDRXX",
    "QZONQ", "QOKIQ", "QHUNQ", "QUSCQ", "CASH"
}
_market_cache: dict = {}  # keyed by ticker string

def clear_market_cache() -> None:
    """Clear in-memory market and fund caches."""
    _market_cache.clear()
    _fund_cache.clear()

def enrich_with_market_data(positions: list) -> list:
    """
    Enrich each position dict with market data from yfinance.

    Adds: dividend_yield, dividend_rate, ex_dividend_date, payout_ratio,
          trailing_eps, forward_eps, trailing_pe, forward_pe,
          market_cap, sector, industry, earnings_timestamp.
    Fields are None if ticker is skipped or lookup fails.
    Only ticker symbols leave the machine.
    """
    _FIELDS = [
        "dividend_yield", "dividend_rate", "ex_dividend_date", "payout_ratio",
        "trailing_eps", "forward_eps", "trailing_pe", "forward_pe",
        "market_cap", "sector", "industry", "earnings_timestamp",
    ]
    _YF_MAP = {
        "dividend_yield":    "dividendYield",
        "dividend_rate":     "dividendRate",
        "ex_dividend_date":  "exDividendDate",
        "payout_ratio":      "payoutRatio",
        "trailing_eps":      "trailingEps",
        "forward_eps":       "forwardEps",
        "trailing_pe":       "trailingPE",
        "forward_pe":        "forwardPE",
        "market_cap":        "marketCap",
        "sector":            "sector",
        "industry":          "industry",
        "earnings_timestamp": "earningsTimestamp",
    }

    # Collect unique tickers to fetch
    unique_tickers = {
        p["ticker"] for p in positions
        if p.get("ticker") and p["ticker"] not in YFINANCE_SKIP_TICKERS and p.get("type_display") != "Cash"
    }

    # 1. Fetch data for primary tickers
    for t in unique_tickers:
        if t in _market_cache:
            continue
        cached = _yf_db_get(t, "market")
        if cached and any(cached.get(k) is not None for k in ["dividend_yield", "trailing_pe", "market_cap"]):
            _market_cache[t] = cached
            continue
        try:
            ticker_obj = yf.Ticker(t)
            info = ticker_obj.info or {}
            entry = {k: info.get(yf_key) for k, yf_key in _YF_MAP.items()}

            # Robust dividend yield fallback (yfinance sometimes uses trailingAnnualDividendYield in decimal)
            if entry["dividend_yield"] is None:
                if info.get("trailingAnnualDividendYield") is not None:
                    entry["dividend_yield"] = info.get("trailingAnnualDividendYield") * 100.0
                elif info.get("yield") is not None:
                    entry["dividend_yield"] = info.get("yield") * 100.0

            # Robust dividend rate fallback
            if entry["dividend_rate"] is None:
                if info.get("trailingAnnualDividendRate") is not None:
                    entry["dividend_rate"] = info.get("trailingAnnualDividendRate")

            # Cross-calculate dividend_rate / dividend_yield if current price is available
            price = info.get("currentPrice") or info.get("regularMarketPrice") or info.get("previousClose")
            if price and price > 0:
                if entry["dividend_rate"] is None and entry["dividend_yield"] is not None:
                    entry["dividend_rate"] = round((entry["dividend_yield"] / 100.0) * price, 4)
                elif entry["dividend_yield"] is None and entry["dividend_rate"] is not None:
                    entry["dividend_yield"] = round((entry["dividend_rate"] / price) * 100.0, 4)

            _market_cache[t] = entry

            # 2. Look-through for ETFs
            # If it's an ETF and missing Trailing PE, or if we want better accuracy via look-through
            if info.get("quoteType") == "ETF":
                details = get_fund_details(t)
                if details.get("holdings"):
                    total_earn_yield = 0.0
                    weight_covered = 0.0

                    # Collect and fetch underlying tickers if not in cache
                    underlying_tickers = [h["ticker"] for h in details["holdings"] if h["ticker"] not in _market_cache]
                    for ut in underlying_tickers:
                        ut_cached = _yf_db_get(ut, "market")
                        if ut_cached:
                            _market_cache[ut] = ut_cached
                            continue
                        try:
                            u_info = yf.Ticker(ut).info or {}
                            _market_cache[ut] = {k: u_info.get(yf_key) for k, yf_key in _YF_MAP.items()}
                            _yf_db_set(ut, "market", _market_cache[ut])
                        except Exception:
                            stale_ut = _yf_db_get(ut, "market", allow_stale=True)
                            _market_cache[ut] = stale_ut if stale_ut else {k: None for k in _FIELDS}

                    for h in details["holdings"]:
                        h_ticker = h["ticker"]
                        h_data = _market_cache.get(h_ticker, {})
                        pe = h_data.get("trailing_pe")
                        if pe and pe > 0:
                            total_earn_yield += (1.0 / pe) * h["weight"]
                            weight_covered += h["weight"]

                    if weight_covered > 0.10: # Only override if we have decent coverage
                        avg_yield = total_earn_yield / weight_covered
                        if avg_yield > 0:
                            _market_cache[t]["trailing_pe"] = 1.0 / avg_yield
                            print(f"Look-through: ETF {t} calculated PE {1.0/avg_yield:.2f} via {weight_covered:.1%} coverage", flush=True)

            if any(_market_cache[t].get(k) is not None for k in ["dividend_yield", "dividend_rate", "trailing_pe", "market_cap"]):
                _yf_db_set(t, "market", _market_cache[t])
        except Exception:
            stale = _yf_db_get(t, "market", allow_stale=True)
            if stale:
                _market_cache[t] = stale
            else:
                _market_cache[t] = {k: None for k in _FIELDS}

    enriched = []
    for pos in positions:
        p = dict(pos)
        ticker = p.get("ticker", "")
        is_cash = p.get("type_display") == "Cash" or ticker in YFINANCE_SKIP_TICKERS
        if ticker and not is_cash:
            market = _market_cache.get(ticker, {k: None for k in _FIELDS})
        else:
            market = {k: None for k in _FIELDS}
            if is_cash:
                market["sector"] = "Cash"
        p.update(market)
        from metrics.tax import classify_dividend_treatment
        p["dividend_treatment"] = classify_dividend_treatment(ticker, p.get("type_display", ""), p.get("sector", ""))
        enriched.append(p)

    return enriched
