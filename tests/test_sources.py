import pytest
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
