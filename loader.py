"""
loader.py — Munger portfolio data loader

Reads holdings from Google Sheets or local CSV, deduplicates by security_id,
normalizes asset classes, calculates portfolio metrics, and flags concentration risk.
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv()


# ---------------------------------------------------------------------------
# 1. Startup .gitignore check
# ---------------------------------------------------------------------------

def check_gitignore():
    """Verify .gitignore exists and contains required security patterns."""
    required = {"*.csv", "*.json", "*.env", "*.db"}
    gitignore_path = os.path.join(os.path.dirname(__file__), ".gitignore")

    if not os.path.exists(gitignore_path):
        raise RuntimeError(".gitignore not found — refusing to start. "
                           "Create .gitignore with: *.csv, *.json, *.env, *.db")

    with open(gitignore_path) as f:
        lines = {line.strip() for line in f if line.strip() and not line.startswith("#")}

    missing = required - lines
    if missing:
        raise RuntimeError(
            f".gitignore is missing required patterns: {sorted(missing)}. "
            "Add them before running."
        )


# ---------------------------------------------------------------------------
# 2. Data loading
# ---------------------------------------------------------------------------

EXPECTED_COLUMNS = [
    "account_id", "account_name", "account_mask", "institution_name",
    "holding_name", "ticker", "type_display", "quantity", "value",
    "security_id", "security_name", "price_updated",
]


def load_from_csv(path: str):
    """Load holdings from a local CSV file."""
    import pandas as pd
    df = pd.read_csv(path)
    return df


def load_from_sheets(sheet_id: str):
    """
    Load holdings from Google Sheets via OAuth2 Authorization Code Flow.

    Credentials JSON path is read from GOOGLE_CREDENTIALS_PATH env var
    (default: credentials.json). Token is stored/refreshed in token.json.
    """
    import pandas as pd
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds_path = os.environ.get("GOOGLE_CREDENTIALS_PATH", "credentials.json")
    token_path = "token.json"

    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(creds_path):
                raise FileNotFoundError(
                    f"OAuth credentials file not found: {creds_path}. "
                    "Download it from Google Cloud Console and set GOOGLE_CREDENTIALS_PATH."
                )
            flow = InstalledAppFlow.from_client_secrets_file(creds_path, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_path, "w") as token_file:
            token_file.write(creds.to_json())

    service = build("sheets", "v4", credentials=creds)
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range="A1:Z")
        .execute()
    )
    rows = result.get("values", [])
    if not rows:
        raise ValueError("Sheet returned no data.")

    headers = rows[0]
    data = rows[1:]
    df = pd.DataFrame(data, columns=headers)
    return df


def load(sheet_id: str = None, csv_path: str = None):
    """
    Dispatcher: load from CSV if csv_path or CSV_PATH env is set, else from Sheets.
    """
    csv_path = csv_path or os.environ.get("CSV_PATH")
    sheet_id = sheet_id or os.environ.get("SHEET_ID")

    if csv_path:
        print(f"Loading from CSV: {csv_path}", flush=True)
        return load_from_csv(csv_path)
    if sheet_id:
        print(f"Loading from Google Sheets: {sheet_id}", flush=True)
        return load_from_sheets(sheet_id)

    raise ValueError(
        "No data source configured. Set CSV_PATH or SHEET_ID environment variable."
    )


# ---------------------------------------------------------------------------
# 3. Deduplication
# ---------------------------------------------------------------------------

def deduplicate(df):
    """
    Deduplicate holdings by security_id (position view).

    Sums quantity and value across accounts. Preserves ticker, security_name,
    type_display from the first occurrence per security_id.
    """
    import pandas as pd

    df = df.copy()
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce").fillna(0)
    df["value"] = pd.to_numeric(df["value"], errors="coerce").fillna(0)

    # Metadata to preserve from first occurrence
    meta = df.groupby("security_id")[["ticker", "security_name", "type_display"]].first()

    # Summed numeric columns
    numeric = df.groupby("security_id")[["quantity", "value"]].sum()

    result = meta.join(numeric).reset_index()
    return result


# ---------------------------------------------------------------------------
# 4. Asset class normalization
# ---------------------------------------------------------------------------

CASH_TICKERS = {"FCASH", "CUR:USD"}
FIXED_INCOME_TICKERS = {"VCSH", "VGSH"}


def normalize_asset_class(df):
    """Normalize type_display based on ticker overrides."""
    df = df.copy()
    df.loc[df["ticker"].isin(CASH_TICKERS), "type_display"] = "Cash"
    df.loc[df["ticker"].isin(FIXED_INCOME_TICKERS), "type_display"] = "Fixed Income"
    return df


# ---------------------------------------------------------------------------
# 5. Metrics calculation
# ---------------------------------------------------------------------------

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
        weight = (row["value"] / total * 100) if total else 0.0
        positions.append({
            "ticker": row["ticker"],
            "security_name": row["security_name"],
            "value": round(float(row["value"]), 2),
            "weight_pct": round(float(weight), 4),
            "type_display": row["type_display"],
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


# ---------------------------------------------------------------------------
# 6. Concentration risk
# ---------------------------------------------------------------------------

DEFAULT_THRESHOLDS = {
    "GOOG": float(os.environ.get("CONC_THRESHOLD_GOOG", 10.0)),
    "VOO": float(os.environ.get("CONC_THRESHOLD_VOO", 20.0)),
}


def check_concentration(df, thresholds: dict = None) -> list:
    """
    Flag tickers that exceed concentration thresholds.

    Returns list of dicts: {"ticker", "weight_pct", "threshold", "flagged"}.
    Only watched tickers are included.
    """
    if thresholds is None:
        thresholds = DEFAULT_THRESHOLDS

    total = df["value"].sum()
    results = []
    for ticker, threshold in thresholds.items():
        mask = df["ticker"] == ticker
        position_value = df.loc[mask, "value"].sum()
        weight = (position_value / total * 100) if total else 0.0
        results.append({
            "ticker": ticker,
            "weight_pct": round(float(weight), 4),
            "threshold": threshold,
            "flagged": weight > threshold,
        })
    return results


# ---------------------------------------------------------------------------
# 7. CLI entry point
# ---------------------------------------------------------------------------

def main():
    check_gitignore()

    df_raw = load()
    df = deduplicate(df_raw)
    df = normalize_asset_class(df)
    metrics = calculate_metrics(df)
    concentration = check_concentration(df)

    try:
        from tabulate import tabulate
        _tabulate = tabulate
    except ImportError:
        def _tabulate(rows, headers=(), tablefmt="simple", **_):
            lines = ["  ".join(str(h) for h in headers)]
            for row in rows:
                lines.append("  ".join(str(c) for c in row))
            return "\n".join(lines)

    print(f"\nTotal Portfolio Value: ${metrics['total_value']:,.2f}\n")

    # Allocation summary
    alloc_rows = sorted(metrics["allocation"].items(), key=lambda x: x[1], reverse=True)
    print(_tabulate(
        [(k, f"{v:.2f}%") for k, v in alloc_rows],
        headers=["Asset Class", "Weight"],
        tablefmt="simple",
    ))
    print()

    # Positions table
    pos_rows = [
        (p["ticker"], p["security_name"][:40], f"${p['value']:,.2f}", f"{p['weight_pct']:.2f}%", p["type_display"])
        for p in metrics["positions"]
    ]
    print(_tabulate(
        pos_rows,
        headers=["Ticker", "Name", "Value", "Weight", "Type"],
        tablefmt="simple",
    ))
    print()

    # Concentration flags
    flags = [c for c in concentration if c["flagged"]]
    if flags:
        print("CONCENTRATION RISK FLAGS:")
        for f in flags:
            print(f"  {f['ticker']}: {f['weight_pct']:.2f}% (threshold: {f['threshold']}%)")
    else:
        watched = ", ".join(c["ticker"] for c in concentration)
        print(f"No concentration flags triggered (watched: {watched}).")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError, FileNotFoundError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
