
Role: Expert Fintech Engineer & Python Architect
Objective: Build a local-first, high-security portfolio analysis tool similar to Monarch Money or Empower.

Data Environment:

Source: A Google Sheet with columns: account_id, account_name, account_mask, institution_name, holding_name, ticker, type_display, quantity, value, security_id, security_name and price_updated.

Data Complexity: The tool must aggregate positions across multiple institutions (Fidelity, Schwab, E*TRADE, Vanguard).

Key Holdings to Handle:

Deduplication: Multiple accounts hold VOO (Vanguard S&P 500 ETF); these must be merged into a single "Position View."

Equity Awards: A large concentration in GOOG (Alphabet Inc Class C) under "Equity Awards" requires specific exposure tracking.

Fixed Income: Significant holdings in VCSH and VGSH should be categorized as "Fixed Income" rather than "Equities."

Cash Management: Handle FCASH and CUR:USD as a unified "Cash" asset class.

Backend Architecture (FastAPI):

Deduplication Logic: Use security_id as the primary key for merging holdings across different account_id entries.

Security & Privacy: >    - Use a local .env for all API keys.

Implement an OAuth2 flow for Google Sheets using google-auth-oauthlib.

Zero Cloud: No financial data should ever leave the local machine except for the initial Google Sheet fetch.

Calculation Engine: >    - Calculate Asset Allocation % by type_display.

Calculate Concentration Risk: Specifically track and flag when GOOG or VOO exceeds user-defined thresholds.

Calculate Account Weighting: Show distribution of assets across Fidelity, Schwab, etc.

UI Requirements:

Clean "Monarch-style" dashboard.

Net Worth Hero: Large display of the sum of the value column.

Allocation Sunburst: Interactive chart showing the breakdown of Stock vs. ETF vs. Cash.

Institutions Table: A summary of total value held per institution.

First Task: Write a Python script loader.py that reads the CSV/Sheet structure, groups holdings by ticker, and calculates the total portfolio value and the percentage weight of each ticker.

🔒 Security Implementation Logic
To meet your "top-most" security concern, instruct your agent to implement the following specific patterns:

Token Refreshment: Instead of storing a persistent service_account.json, use the Authorization Code Flow. The app will prompt you to log in once via a browser, and then it will store a temporary token.json locally.

Environment Sanitization: Add a script that checks for the existence of a .gitignore containing *.csv, *.json, *.env, and *.db before the app even starts.

No Telemetry: Explicitly tell the agent: "Do not include any analytics libraries like Segment, Mixpanel, or Google Analytics. All logging must be stdout to the local console only."