# Warren Buffett Valuation Methodology

This document outlines the core financial metrics and qualitative criteria used by Warren Buffett to value stocks. These requirements serve as a blueprint for implementing "Buffett Style" analysis in the Munger application.

## 1. Core Financial Metrics (Quantitative)

### Owner Earnings (True Cash Flow)
Buffett prefers "Owner Earnings" over GAAP Net Income or EBITDA.
**Formula:**
`Owner Earnings = Net Income + Depreciation & Amortization - Average Annual Maintenance CapEx`
*   *Maintenance CapEx:* The capital required to maintain existing unit volume and competitive position (excludes growth CapEx).

### Intrinsic Value (Simplified DCF)
Intrinsic value is the discounted value of all cash that can be taken out of a business during its remaining life.
*   **Projection Period:** Typically 10 years.
*   **Discount Rate:** Long-term U.S. Government Bond rate (Risk-Free Rate). Use a floor (e.g., 10%) if rates are artificially low.
*   **Terminal Value:** Conservative growth rate (e.g., GDP growth) beyond year 10.

### Return on Equity (ROE)
*   **Target:** Consistent ROE > 15-20%.
*   **Significance:** Indicates how effectively management employs shareholder capital without excessive leverage.

### Debt-to-Equity
*   **Preference:** Low debt. The company should be able to pay off long-term debt with 3-4 years of earnings.
*   **Significance:** High-quality businesses generate enough cash to fund operations without heavy borrowing.

### Profit Margins
*   **Gross Margin:** Ideally > 40%.
*   **Net Margin:** High and stable compared to industry peers.
*   **Significance:** High margins signal a "Moat" (pricing power).

### Retained Earnings Test
*   **Requirement:** For every $1 retained by the company, it must create at least $1 in market value for shareholders over time.

---

## 2. The Economic Moat (Qualitative)

A durable competitive advantage that protects high returns on capital.

| Moat Type | Description |
| :--- | :--- |
| **Brand Power** | Consumers are willing to pay a premium (e.g., Coca-Cola, Apple). |
| **Switching Costs** | High friction or cost for customers to move to a competitor (e.g., Software). |
| **Network Effect** | Product becomes more valuable as more people use it (e.g., Amex). |
| **Cost Advantage** | Lower production/operating costs than all competitors (e.g., GEICO). |

---

## 3. Implementation Strategy for Munger App

To implement this analysis, the app needs the following data points (per ticker):
1.  **Historical Earnings (10 yrs):** To verify consistency.
2.  **Maintenance CapEx Estimate:** Can be approximated from Cash Flow Statement (CapEx) vs. Depreciation.
3.  **Balance Sheet Data:** Total Debt, Shareholder Equity.
4.  **Risk-Free Rate:** Current 10-year Treasury Yield (via API).

### Valuation Output
*   **Intrinsic Value Price:** The calculated "fair" price.
*   **Margin of Safety Price:** Buy price at 20-30% discount to Intrinsic Value.
*   **Quality Score:** Based on ROE, Debt, and Margin consistency.
