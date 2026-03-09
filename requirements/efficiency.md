### **Requirement Specification: Portfolio Efficiency & Cost Optimization**

**Objective:** The application shall implement a dedicated "Efficiency" module to quantify, visualize, and optimize the structural costs (expense ratios and fee drag) associated with the portfolio. The goal is to shift from abstract percentage-based reporting to tangible dollar-impact analysis over long-term investment horizons.

---

### **1. Functional Requirements**

#### **1.1. Annualized Real-Cost Calculation**

* **Description:** The system must calculate the absolute dollar amount lost to management fees annually.
* **Logic:** For each position where `type_display` is "ETF" or "Mutual Fund", calculate:
`Annual Expense ($) = Current Value * Expense Ratio`.
* **Aggregation:** Display a "Total Annual Fee Bill" as a primary KPI at the top of the tab.
* **Data Source:** Integration with `yfinance` to fetch the `expenseRatio` attribute for all tickers.

#### **1.2. The "Wealth Gap" Projection (Fee Drag)**

* **Description:** A longitudinal projection showing the compounding impact of the current Weighted Expense Ratio (WER) versus a low-cost benchmark (e.g., 0.05%).
* **Logic:** Project the current total portfolio value ($X,XXX,XXX.XX) over 10, 20, and 30 years.
* **Scenario A (Current):** Compounded growth at 7% minus the current WER.
* **Scenario B (Optimized):** Compounded growth at 7% minus 0.05%.


* **Visualization:** A stacked area chart or line chart representing the "Lost Wealth" (the delta between Scenarios A and B).

#### **1.3. Efficiency Benchmarking & Substitution Logic**

* **Description:** Identify high-cost outliers and suggest lower-cost equivalent instruments.
* **Logic:** * Flag any asset with an expense ratio $>0.20\%$ (e.g., certain institutional mutual fund trusts).
* Compare the flagged asset's expense ratio against a category-standard benchmark (e.g., VOO for Large Cap, BND for Total Bond).
* Display "Potential Annual Savings" in dollars if the asset were switched to the benchmark.


* **Thresholds:** Highlight assets in "Red" if the ratio is $>0.50\%$ and "Amber" if between $0.20\% - 0.50\%$.

#### **1.4. Historical Efficiency Trend**

* **Description:** Track the WER of the portfolio over time as assets are moved or new contributions are made.
* **Logic:** Persist the calculated WER into the local SQLite `DailySnapshot` table.

---

### **2. Technical Specifications (Python/FastAPI Backend)**

* **Endpoint:** `GET /api/v1/efficiency`
* **Data Model:** * `weighted_expense_ratio`: Float (calculated as $\sum(Weight_i * ExpenseRatio_i)$).
* `total_annual_cost`: Float.
* `projection_data`: Array of objects containing `{year, current_scenario_val, optimized_scenario_val, wealth_gap}`.


* **Calculation Utility:**
```python
def calculate_compounded_drag(principal, rate, years, fee):
    # A = P(1 + (r - f))^t
    return principal * ((1 + (rate - fee)) ** years)

```



---

### **3. UI/UX Requirements**

* **Stat Cards:**
* **Annual Fee Bill:** $X,XXX/year.
* **Weighted Expense Ratio:** X.XX%.
* **30-Year Opportunity Cost:** $XXX,XXX (Total projected wealth gap).


* **Interactive Table:**
* Columns: `Ticker`, `Holding Name`, `Exp. Ratio`, `Annual Cost ($)`, `Efficiency Status`.
* Ability to sort by `Annual Cost ($)` to immediately identify the most "expensive" holdings in absolute terms.


* **Comparison Tooltip:** Hovering over a high-cost fund should show: *"This fund costs you $X more per year than a standard 0.03% ETF."*

### **4. Security & Privacy Constraints**

* **Local Processing:** All fee-drag and wealth-gap projections must be calculated on the local Python backend.
* **No Third-Party Analytics:** The UI must not transmit any specific ticker quantities or dollar values to external servers for the sake of projection calculations. All market data fetches (expense ratios) must be handled via the backend ticker-only requests.