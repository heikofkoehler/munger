import sys
import yfinance as yf
import pandas as pd
from core.database import _yf_db_get, _yf_db_set

_fund_cache: dict = {}  # keyed by ticker

def get_fund_details(ticker: str) -> dict:
    """
    Fetch expense ratio and top holdings for a fund ticker.
    Returns: {"expense_ratio": float or None, "holdings": [{"ticker": str, "weight": float}, ...]}
    """
    if ticker in _fund_cache:
        return _fund_cache[ticker]

    try:
        t = yf.Ticker(ticker)
        info = t.info
        raw_ratio = info.get("netExpenseRatio") or info.get("expenseRatio")
        # yfinance returns these as percentages (e.g. 0.03 for 0.03%), 
        # so divide by 100 for decimal representation (0.0003)
        expense_ratio = float(raw_ratio) / 100 if raw_ratio is not None else None

        holdings = []
        if hasattr(t, "funds_data") and t.funds_data.top_holdings is not None:
            df_holdings = t.funds_data.top_holdings
            if not df_holdings.empty:
                # The index is the ticker symbol
                for symbol, row in df_holdings.iterrows():
                    weight = row.get("Holding Percent") or row.get("Weight") or 0.0
                    holdings.append({"ticker": str(symbol), "weight": float(weight)})

        res = {"expense_ratio": expense_ratio, "holdings": holdings}
        _fund_cache[ticker] = res
        return res
    except Exception as e:
        print(f"Error fetching fund details for {ticker}: {e}", file=sys.stderr)
        return {"expense_ratio": None, "holdings": []}

YFINANCE_SKIP_TICKERS: set = {"FCASH", "CUR:USD"}
_market_cache: dict = {}  # keyed by ticker string

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
        if p.get("ticker") and p["ticker"] not in YFINANCE_SKIP_TICKERS
    }

    # 1. Fetch data for primary tickers
    for t in unique_tickers:
        if t in _market_cache:
            continue
        cached = _yf_db_get(t, "market")
        if cached:
            _market_cache[t] = cached
            continue
        try:
            ticker_obj = yf.Ticker(t)
            info = ticker_obj.info
            _market_cache[t] = {k: info.get(yf_key) for k, yf_key in _YF_MAP.items()}

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
                            u_info = yf.Ticker(ut).info
                            _market_cache[ut] = {k: u_info.get(yf_key) for k, yf_key in _YF_MAP.items()}
                            _yf_db_set(ut, "market", _market_cache[ut])
                        except Exception:
                            _market_cache[ut] = {k: None for k in _FIELDS}

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

            _yf_db_set(t, "market", _market_cache[t])
        except Exception:
            _market_cache[t] = {k: None for k in _FIELDS}

    enriched = []
    for pos in positions:
        p = dict(pos)
        ticker = p.get("ticker", "")
        if ticker and ticker not in YFINANCE_SKIP_TICKERS:
            market = _market_cache.get(ticker, {k: None for k in _FIELDS})
        else:
            market = {k: None for k in _FIELDS}
        p.update(market)
        enriched.append(p)

    return enriched
