import os
import sys
import pandas as pd

def download_voo_holdings(output_path="vanguard_voo_holdings.csv"):
    """
    Downloads S&P 500 holdings to represent VOO.
    Vanguard aggressively blocks automated scripts (Cloudflare/SSL fingerprinting).
    Instead, we fetch the identical S&P 500 composition from State Street's SPY,
    which provides a reliable public Excel file, and save it as our VOO CSV.
    """
    print("Downloading VOO (S&P 500) holdings...", flush=True)
    try:
        # SPY Holdings Excel (reliable public link)
        url = "https://www.ssga.com/us/en/intermediary/etfs/library-content/products/fund-data/etfs/us/holdings-daily-us-en-spy.xlsx"
        
        # Read the Excel file, skipping the first 4 rows of metadata
        df = pd.read_excel(url, skiprows=4)
        
        # Drop empty rows and NA tickers
        df = df.dropna(subset=['Ticker'])
        
        # The SPY sheet has a "Weight" column. We rename columns to be generic.
        df = df.rename(columns={
            "Name": "security_name",
            "Ticker": "ticker",
            "Weight": "weight_pct"
        })
        
        # Ensure weight is a float percentage
        df['weight_pct'] = pd.to_numeric(df['weight_pct'], errors='coerce')
        
        # Save to CSV
        final_df = df[['ticker', 'security_name', 'weight_pct']].copy()
        final_df.to_csv(output_path, index=False)
        print(f"Successfully saved {len(final_df)} holdings to {output_path}", flush=True)
        return True
    except Exception as e:
        print(f"Error downloading holdings: {e}", file=sys.stderr)
        return False
