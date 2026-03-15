
## Code Agent Context: Financial Logic & Data Schema

### **I. Required Data Objects (`yfinance` Schema)**

The agent must target the following specific keys within the `yfinance` Ticker object to populate the formulas:

* **Valuation Base:** `ticker.cashflow.loc['Free Cash Flow'][0]` (use `[0:3].mean()` if current is negative).
* **Risk Metrics:** `ticker.info['beta']` and the latest closing price of `^TNX` (10-Year Treasury) / 100.
* **Capital Structure:** * `E`: `ticker.info['marketCap']`
* `D`: `ticker.info['totalDebt']`
* `Cash`: `ticker.info['totalCash']`


* **Profitability/Tax:** * `Interest Expense`: `ticker.financials.loc['Interest Expense'][0]`
* `Tax Rate`: `ticker.financials.loc['Tax Provision'][0] / ticker.financials.loc['Pretax Income'][0]` (default to 21% if missing).


* **Growth Profile:** `ticker.growth_estimates` or `ticker.info['earningsQuarterlyGrowth']`.

---

### **II. Mathematical Logic Sequence**

#### **1. The Discount Rate (WACC)**

The agent must derive the Cost of Capital using the Capital Asset Pricing Model (CAPM) as the primary input for equity.

* **Cost of Equity ($Re$):** 
$$Re = Rf + (\beta \times ERP)$$


*(Use $ERP = 0.0438$ as base; $Rf = \text{Current Yield of } ^TNX$)*
* **Cost of Debt ($Rd$):**

$$Rd = \frac{\text{Interest Expense}}{\text{Total Debt}} \times (1 - \text{Tax Rate})$$


* **WACC Calculation:**

$$WACC = \left( \frac{E}{E+D} \times Re \right) + \left( \frac{D}{E+D} \times Rd \right)$$



#### **2. 2-Stage Cash Flow Projection**

* **Stage 1 (High Growth):** For $t = 1$ to $5$:

$$PV_t = \frac{FCF_0 \times (1 + g)^t}{(1 + WACC)^t}$$


* **Stage 2 (Terminal Value):** 
$$TV = \frac{FCF_5 \times (1 + g_{terminal})}{WACC - g_{terminal}}$$


$$PV_{TV} = \frac{TV}{(1 + WACC)^5}$$



*(Note: Constrain $g_{terminal}$ to be $\leq Rf$ or $3\%$ to ensure model stability.)*

#### **3. Value Conversion**

* **Enterprise Value ($EV$):** $\sum_{t=1}^{5} PV_t + PV_{TV}$
* **Equity Value:** $EV + \text{Total Cash} - \text{Total Debt}$
* **Intrinsic Value per Share:** $\frac{\text{Equity Value}}{\text{Shares Outstanding}}$

---

### **III. Sensitivity Matrix Logic**

The agent should generate a 2D array (Matrix) of outcomes to stress-test the assumptions:

* **Axis X (Growth):** Range $[g - 2\%, g + 2\%]$ in $0.5\%$ increments.
* **Axis Y (WACC):** Range $[WACC - 1\%, WACC + 1\%]$ in $0.25\%$ increments.

---

### **IV. Handling Anomalies**

1. **High-Cash Companies:** For firms where $Cash > Debt$, the agent must ensure "Net Debt" is treated as a negative number in the Equity Value formula (thereby adding it).
2. **Growth Convergence:** If $g > WACC$, the formula will fail (divide by zero/negative). The agent must cap $g$ at $WACC - 0.5\%$ to prevent mathematical errors in the Terminal Value calculation.