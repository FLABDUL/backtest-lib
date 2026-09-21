# StockFit data-quality audit

**Draft — not published**

## Purpose and method

This audit tests whether a small, predeclared StockFit cohort is structurally
usable for research with `backtest-lib`. It evaluates coverage, integrity,
required-fact completeness, filing timing and provenance. It does not measure
alpha, certify the provider, or establish production readiness.

The frozen cohort is AAPL, MSFT, JPM, BAC, XOM, CVX, JNJ, PFE, WMT, COST, CAT
and UNP. The selection spans technology, banking, energy, healthcare,
consumer and industrial businesses without claiming to represent the wider
market.

The rules were fixed before the live run:

| Check | Threshold |
| --- | --- |
| Price start | Review if more than 31 calendar days after the requested start |
| Price end | Review if more than 7 calendar days before execution |
| Price gap | Review if a calendar gap exceeds 7 days |
| Annual history | Review if fewer than 5 periods are returned |
| Required facts | Review if revenue or operating-income completeness is below 100% |
| Filing lag | Review if the maximum lag exceeds 90 days |
| Integrity | Fail malformed responses, invalid or duplicate prices, invalid filings, duplicate fiscal years, or no usable consecutive annual pair |

The live command made 36 sequential requests: company metadata, adjusted daily
prices and annual income statements for each of the 12 tickers. Requests were
not retried.

## Live result

The audit ran on 21 September 2026 and completed all 36 requests. Shared price
coverage across the cohort ran from 4 January 2021 to 18 September 2026.

| Status | Companies |
| --- | ---: |
| Pass | 9 |
| Review | 0 |
| Fail | 3 |

The most frequent reason codes were:

| Reason code | Companies |
| --- | ---: |
| `no_usable_consecutive_pair` | 3 |
| `operating_income_incomplete` | 3 |
| `revenue_incomplete` | 1 |
| `statement_history_short` | 1 |

JPM and BAC failed because incomplete operating income left no usable
consecutive annual pair under the predeclared rule. XOM returned no annual
rows in this trial request, so it failed the usable-pair check and was also
marked for short history and incomplete required facts. These are narrow
audit outcomes, not judgements about the companies or their securities.

Across the cohort, revenue completeness ranged from 0% to 100%, with a median
of 100%. Operating-income completeness also ranged from 0% to 100%, with a
median of 100%. The median of company-level median filing lags was 46.5 days;
those medians ranged from 30 to 55.5 days. Company maximum filing lags ranged
from 34 to 59 days, with a cohort median of 57 days.

The retained provenance aggregates contain 202 source entries, 214 numeric
`before` facts and 892 derived facts. Provenance incidence is informational and
does not alter a company's status by itself.

## Reproduction

Run the deterministic synthetic audit without a token or network access:

```powershell
uv run python -m examples.stockfit.audit_cli --output-dir artifacts/stockfit-audit
```

Run the explicit live audit with a token held outside the repository:

```powershell
$secretPath = "$env:USERPROFILE\.codex\secrets\stockfit-token.txt"
$env:STOCKFIT_TOKEN = [IO.File]::ReadAllText($secretPath).Trim()
try {
  uv run python -m examples.stockfit.audit_cli --live --output-dir artifacts/stockfit-audit-live
} finally {
  Remove-Item Env:STOCKFIT_TOKEN -ErrorAction SilentlyContinue
}
```

Both modes publish only `summary.json`, `company-quality.csv` and
`data-quality.svg`.

## Limitations

- Current ticker selection introduces survivorship bias and does not recreate
  a historical investable universe.
- Twelve companies are too few for broad conclusions about provider quality.
- The trial's request depth and duration limit temporal and endpoint coverage.
- Sector accounting differences make one uniform fact rule deliberately
  conservative, especially for banks and energy businesses.
- Results depend on the provider response observed on the execution date and
  may change when upstream data changes.
- Passing these checks means only that the declared structural rules passed;
  it does not establish economic correctness, investment merit or operational
  suitability.

No raw prices, financial values, response bodies, source-map keys, accession
numbers, stable identifier values or bearer credentials were retained. The
committed outputs contain public labels and derived audit metadata only.
