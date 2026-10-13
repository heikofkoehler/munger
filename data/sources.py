import os
import pandas as pd

EXPECTED_COLUMNS = [
    "account_id", "account_name", "account_mask", "institution_name",
    "holding_name", "ticker", "type_display", "quantity", "value",
    "security_id", "security_name", "price_updated",
]

def load_from_csv(path: str):
    """Load holdings from a local CSV file."""
    df = pd.read_csv(path)
    return df

def _user_file(name: str) -> str:
    """
    Resolve a user-managed file (OAuth credentials / token): prefer the
    historical CWD location so existing setups keep working; fall back to the
    workspace root so the desktop app (which has no reliable CWD) has a
    stable home for it. Absolute paths are returned unchanged.
    """
    if os.path.isabs(name) or os.path.exists(name):
        return name
    from core.workspace import resolve_workspace
    return str(resolve_workspace() / name)


def load_from_sheets(sheet_id: str):
    """
    Load holdings from Google Sheets via OAuth2 Authorization Code Flow.

    Credentials JSON path is read from GOOGLE_CREDENTIALS_PATH env var
    (default: credentials.json). Token is stored/refreshed in token.json.
    Relative paths resolve to CWD first, then the workspace root.
    """
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds_path = _user_file(os.environ.get("GOOGLE_CREDENTIALS_PATH", "credentials.json"))
    token_path = _user_file("token.json")

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

    headers = [str(h).strip() for h in rows[0]]
    # Ensure all data rows match headers length
    data = [row[:len(headers)] + [None] * max(0, len(headers) - len(row)) for row in rows[1:]]
    df = pd.DataFrame(data, columns=headers)

    # Clean numeric columns from currency signs and comma separators
    for col in ["value", "quantity", "cost_basis"]:
        if col in df.columns:
            df[col] = (
                df[col]
                .astype(str)
                .str.replace("$", "", regex=False)
                .str.replace(",", "", regex=False)
                .str.strip()
            )
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df

def load(sheet_id: str = None, csv_path: str = None, monarch_json: str = None, override_path: str = None, settings: dict = None):
    """
    Dispatcher: load from Monarch JSON, CSV, or Google Sheets (checked in that order).

    Precedence for each source: explicit argument > settings.json ("data_source")
    > environment variable. settings["data_source"]["kind"] may be "auto"
    (default), "monarch_json", "csv", or "sheets" to pin a single source.

    If override_path is provided, it attempts to load that file based on its extension.
    """
    if override_path and os.path.exists(override_path):
        if override_path.endswith(".json"):
            print(f"Loading from override JSON: {override_path}", flush=True)
            from monarch import load_from_json
            return load_from_json(override_path)
        else:
            print(f"Loading from override CSV: {override_path}", flush=True)
            return load_from_csv(override_path)

    ds = (settings or {}).get("data_source", {}) or {}
    kind = ds.get("kind", "auto")

    monarch_json = monarch_json or ds.get("monarch_json_path") or os.environ.get("MONARCH_JSON_PATH")
    csv_path = csv_path or ds.get("csv_path") or os.environ.get("CSV_PATH")
    sheet_id = sheet_id or ds.get("sheet_id") or os.environ.get("SHEET_ID")

    def _want(name: str) -> bool:
        return kind == "auto" or kind == name

    if _want("monarch_json") and monarch_json and os.path.exists(monarch_json):
        print(f"Loading from Monarch JSON: {monarch_json}", flush=True)
        from monarch import load_from_json
        return load_from_json(monarch_json)
    if _want("csv") and csv_path and os.path.exists(csv_path):
        print(f"Loading from CSV: {csv_path}", flush=True)
        return load_from_csv(csv_path)
    if _want("sheets") and sheet_id:
        print(f"Loading from Google Sheets: {sheet_id}", flush=True)
        return load_from_sheets(sheet_id)

    raise ValueError(
        "No data source configured or found. Set MONARCH_JSON_PATH, CSV_PATH, or SHEET_ID "
        "(or configure data_source in <workspace>/settings.json) and ensure the local files exist."
    )
