"""
monarch.py — Monarch Money portfolio fetcher

Fetches portfolio data from Monarch Money's GraphQL API and stores the full
JSON response locally. Token is obtained from the Monarch UI (open DevTools →
Network tab → any request → Authorization: Token <value>).

Usage:
    python monarch.py                    # uses MONARCH_TOKEN env var
    python monarch.py --token TOKEN
    python monarch.py --token TOKEN --output monarch_response.json
"""

import argparse
import json
import os
import sys
from datetime import date, timedelta

from dotenv import load_dotenv

load_dotenv()

MONARCH_API_URL = "https://api.monarch.com/graphql"

_PORTFOLIO_QUERY = """
query Web_GetPortfolio($portfolioInput: PortfolioInput) {
  portfolio(input: $portfolioInput) {
    performance {
      totalValue
      totalBasis
      totalChangePercent
      totalChangeDollars
      oneDayChangePercent
      __typename
    }
    aggregateHoldings {
      edges {
        node {
          id
          quantity
          basis
          totalValue
          securityPriceChangeDollars
          securityPriceChangePercent
          lastSyncedAt
          holdings {
            id
            type
            typeDisplay
            name
            ticker
            closingPrice
            closingPriceUpdatedAt
            quantity
            value
            costBasis
            account {
              id
              mask
              institution {
                id
                name
                __typename
              }
              type { name display __typename }
              subtype { name display __typename }
              displayName
              __typename
            }
            taxLots {
              id
              createdAt
              acquisitionDate
              acquisitionQuantity
              costBasisPerUnit
              __typename
            }
            __typename
          }
          security {
            id
            name
            ticker
            currentPrice
            currentPriceUpdatedAt
            closingPrice
            type
            typeDisplay
            __typename
          }
          __typename
        }
        __typename
      }
      __typename
    }
    __typename
  }
}
"""


def fetch(token: str, output_path: str = "monarch_response.json") -> dict:
    """
    POST to Monarch GraphQL, save full JSON response to output_path, return parsed dict.
    """
    import requests

    today = date.today()
    start = today - timedelta(days=90)

    payload = {
        "operationName": "Web_GetPortfolio",
        "variables": {
            "portfolioInput": {
                "startDate": start.isoformat(),
                "endDate": today.isoformat(),
            }
        },
        "query": _PORTFOLIO_QUERY,
    }
    headers = {
        "accept": "*/*",
        "authorization": f"Token {token}",
        "client-platform": "web",
        "content-type": "application/json",
        "monarch-client": "monarch-core-web-app-graphql",
        "origin": "https://app.monarch.com",
    }

    resp = requests.post(MONARCH_API_URL, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if "errors" in data:
        raise ValueError(f"Monarch GraphQL errors: {data['errors']}")

    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"Saved Monarch response → {output_path}", flush=True)
    return data


def to_dataframe(data: dict):
    """
    Flatten a Monarch portfolio response into a DataFrame matching the existing
    holdings schema:
      account_id, account_name, account_mask, institution_name,
      holding_name, ticker, type_display, quantity, value,
      security_id, security_name, price_updated
    """
    import pandas as pd

    rows = []
    edges = data["data"]["portfolio"]["aggregateHoldings"]["edges"]

    for edge in edges:
        node = edge["node"]
        sec = node["security"]
        # Prefer security-level ticker (e.g. BRK-B) over holding-level (BRKB)
        security_ticker = sec.get("ticker") or ""
        security_id = sec["id"]
        security_name = sec["name"]

        for h in node["holdings"]:
            acct = h["account"]
            ticker = security_ticker or h.get("ticker") or ""
            rows.append({
                "account_id":       acct["id"],
                "account_name":     acct["displayName"],
                "account_mask":     acct.get("mask") or "",
                "institution_name": acct["institution"]["name"],
                "holding_name":     h["name"],
                "ticker":           ticker,
                "type_display":     h["typeDisplay"],
                "quantity":         h["quantity"],
                "value":            h["value"],
                "security_id":      security_id,
                "security_name":    security_name,
                "price_updated":    h.get("closingPriceUpdatedAt") or "",
            })

    return pd.DataFrame(rows)


def load_from_json(path: str):
    """Load a stored Monarch response JSON and return a holdings DataFrame."""
    with open(path) as f:
        data = json.load(f)
    return to_dataframe(data)


def main():
    parser = argparse.ArgumentParser(description="Fetch Monarch Money portfolio data")
    parser.add_argument("--token", help="Monarch API token (or set MONARCH_TOKEN env var)")
    parser.add_argument(
        "--output",
        default=os.environ.get("MONARCH_JSON_PATH", "monarch_response.json"),
        help="Output JSON path (default: monarch_response.json)",
    )
    args = parser.parse_args()

    token = args.token or os.environ.get("MONARCH_TOKEN")
    if not token:
        print("ERROR: Provide --token TOKEN or set MONARCH_TOKEN env var", file=sys.stderr)
        sys.exit(1)

    data = fetch(token, args.output)
    df = to_dataframe(data)

    perf = data["data"]["portfolio"]["performance"]
    print(f"Total value:  ${perf['totalValue']:,.2f}")
    print(f"Total basis:  ${perf['totalBasis']:,.2f}")
    print(f"Total gain:   ${perf['totalChangeDollars']:,.2f} ({perf['totalChangePercent']:.1f}%)")
    print(f"Positions:    {len(df)} holdings across {df['account_name'].nunique()} accounts")
    print(f"Securities:   {df['security_id'].nunique()} unique securities")


if __name__ == "__main__":
    main()
