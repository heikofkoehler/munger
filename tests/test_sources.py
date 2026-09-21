import pytest
import pandas as pd
from unittest.mock import patch
from data.sources import load, EXPECTED_COLUMNS

@patch("os.environ.get")
@patch("monarch.load_from_json")
def test_load_dispatcher_monarch(mock_load_json, mock_env_get):
    # Mock environment to only return monarch JSON path
    def mock_env(key, default=None):
        if key == "MONARCH_JSON_PATH": return "mock_monarch.json"
        return default
    mock_env_get.side_effect = mock_env
    
    with patch("os.path.exists", return_value=True):
        load()
        mock_load_json.assert_called_once_with("mock_monarch.json")

@patch("os.environ.get")
@patch("data.sources.load_from_csv")
def test_load_dispatcher_csv(mock_load_csv, mock_env_get):
    # Mock environment to only return CSV path
    def mock_env(key, default=None):
        if key == "CSV_PATH": return "mock_data.csv"
        return default
    mock_env_get.side_effect = mock_env
    
    with patch("os.path.exists", return_value=True):
        load()
        mock_load_csv.assert_called_once_with("mock_data.csv")

def test_load_dispatcher_no_source():
    with patch("os.environ.get", return_value=None):
        with pytest.raises(ValueError, match="No data source configured"):
            load()

@patch("pandas.read_csv")
def test_load_from_csv(mock_read_csv):
    from data.sources import load_from_csv
    load_from_csv("mock_file.csv")
    mock_read_csv.assert_called_once_with("mock_file.csv")

@patch("os.environ.get")
@patch("data.sources.load_from_sheets")
def test_load_dispatcher_sheets(mock_load_sheets, mock_env_get):
    def mock_env(key, default=None):
        if key == "SHEET_ID": return "mock_sheet_123"
        return default
    mock_env_get.side_effect = mock_env

    with patch("os.path.exists", return_value=False):
        load()
        mock_load_sheets.assert_called_once_with("mock_sheet_123")

@patch("os.path.exists", return_value=True)
@patch("google.oauth2.credentials.Credentials.from_authorized_user_file")
@patch("googleapiclient.discovery.build")
def test_load_from_sheets(mock_build, mock_creds_file, mock_exists):
    mock_creds = mock_creds_file.return_value
    mock_creds.valid = True

    mock_service = mock_build.return_value
    mock_values = mock_service.spreadsheets.return_value.values.return_value.get.return_value.execute
    mock_values.return_value = {
        "values": [
            ["ticker", "quantity", "value", "cost_basis", "security_name", "type_display", "security_id"],
            ["AAPL", "10", "$1,500.00", "$1,200.00", "Apple Inc.", "Stock", "sec_1"],
            ["VOO", "5", "2,500.00", "", "Vanguard S&P 500", "ETF", "sec_2"],
        ]
    }
    from data.sources import load_from_sheets
    df = load_from_sheets("mock_sheet_id")
    assert len(df) == 2
    assert "ticker" in df.columns
    assert df.iloc[0]["value"] == 1500.0
    assert df.iloc[0]["cost_basis"] == 1200.0
    assert df.iloc[1]["value"] == 2500.0
    assert pd.isna(df.iloc[1]["cost_basis"])

@patch("os.path.exists", return_value=True)
@patch("data.sources.load_from_csv")
def test_load_settings_csv_used_when_env_empty(mock_load_csv, mock_exists):
    with patch("os.environ.get", return_value=None):
        load(settings={"data_source": {"kind": "auto", "csv_path": "settings.csv"}})
        mock_load_csv.assert_called_once_with("settings.csv")

@patch("os.path.exists", return_value=True)
@patch("data.sources.load_from_csv")
def test_load_explicit_arg_beats_settings(mock_load_csv, mock_exists):
    with patch("os.environ.get", return_value=None):
        load(csv_path="explicit.csv",
             settings={"data_source": {"kind": "auto", "csv_path": "settings.csv"}})
        mock_load_csv.assert_called_once_with("explicit.csv")

@patch("os.path.exists", return_value=True)
@patch("data.sources.load_from_sheets")
def test_load_kind_pin_skips_other_sources(mock_load_sheets, mock_exists):
    with patch("os.environ.get", return_value=None):
        load(settings={"data_source": {"kind": "sheets",
                                       "csv_path": "ignored.csv",
                                       "sheet_id": "s1"}})
        mock_load_sheets.assert_called_once_with("s1")
