### **Requirement: Risk Reporting and Cost Efficiency Analysis**

**Objective:**
The application shall implement a comprehensive risk reporting module to identify asset concentration and analyze the total cost of ownership for the portfolio. This module will move beyond line-item analysis to provide a holistic view of financial health and portfolio efficiency.

---

### **1. Functional Requirements**

#### **1.1. Aggregated Single-Stock Concentration Monitoring**

* **Definition:** The system shall calculate the "True Exposure" for every individual security held in the portfolio.
* **Calculation Logic:** True Exposure must be the sum of the market value of direct equity holdings plus the proportional market value of that same security held within ETFs or Mutual Funds (using constituent weight data).
* **Threshold Alerting:** The system shall proactively flag any security where the True Exposure exceeds **10.0% of the total Portfolio Net Worth**.
* **Visualization:** A dedicated "Risk Dashboard" must display these high-concentration assets with a breakdown of direct vs. indirect (fund-based) exposure.

#### **1.2. Weighted Expense Ratio (WER) Calculation**

* **Definition:** The system shall calculate the annual cost of the portfolio based on the expense ratios of all composite instruments (ETFs and Mutual Funds).
* **Calculation Logic:** * For each fund, calculate the *Position Cost* = (Market Value of Position × Expense Ratio).
* Calculate the *Total Portfolio Cost* = Σ (All Position Costs).
* Calculate the **Weighted Expense Ratio** = (Total Portfolio Cost / Total Portfolio Net Worth).


* **Data Source:** The system must fetch current expense ratios for all fund tickers (e.g., VOO at 0.03%) to ensure the calculation remains accurate as holdings or fund fees change.

---

### **2. Technical & UI Specifications**

* **Look-through Depth:** The look-through engine shall fetch at least the top 10–25 holdings for any fund identified in the portfolio to ensure meaningful concentration risk assessment.
* **Data Persistence:** Concentration and expense metrics shall be calculated on every data refresh and stored in the local database to allow for historical cost and risk trend analysis.
* **UI Component:** The Risk Dashboard shall include:
* A "Concentration Heatmap" showing exposure across all underlying entities.
* A "Portfolio Efficiency Score" based on the Weighted Expense Ratio compared to a user-defined benchmark.
* Visual indicators (e.g., red/amber/green) for concentration levels relative to the 10% threshold.