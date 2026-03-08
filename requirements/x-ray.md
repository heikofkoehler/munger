### **Requirement: Holistic Asset Exposure (ETF and Mutual Fund Look-through)**

**Objective:**
The application shall implement a "Look-through" (X-Ray) analysis module to calculate and visualize the true concentration of individual securities across the entire portfolio, accounting for both direct equity holdings and indirect exposure through composite instruments.

**Functional Specifications:**

1. **Exposure Aggregation:** The system must identify overlapping positions where a specific security is held as a standalone equity and as an underlying constituent within Exchange-Traded Funds (ETFs) or Mutual Funds.
2. **Calculation Logic:** For any given ticker, the "True Exposure" must be calculated using the following formula:
* *Total Exposure Value = (Market Value of Direct Shares) + Σ (Market Value of Fund × Weight of Security within Fund)*.


3. **Data Integration:** The backend must integrate with a financial data API (e.g., yfinance or a dedicated fundamental data provider) to fetch current fund constituent weightings.
4. **Risk Alerting:** The system should provide a "True Concentration" metric that flags when the combined (direct + indirect) exposure to a single entity exceeds a user-defined risk threshold (e.g., 10% of total net worth).

**Technical Implementation Details for Coding Agents:**

* **Data Model:** Extend the `Position` schema to include a `is_composite` flag for ETFs/Funds.
* **Processing:** Implement a background task to cache constituent data for all held ETFs to minimize API latency during dashboard renders.
* **UI Representation:** In the "Top Holdings" view, display a breakdown for each major position showing "Direct Value" vs. "Indirect Fund Value" to provide transparency into how the total exposure was derived.