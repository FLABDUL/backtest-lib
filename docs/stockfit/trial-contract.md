# StockFit Trial Contract Check

**Executed:** 21 September 2026
**Symbol:** AAPL
**Mode:** Three bounded, read-only live requests; raw responses were not persisted.

## Endpoint results

| Endpoint | Status | Sanitised observation |
| --- | ---: | --- |
| `/api/company/details` | 200 | Resolved CIK and company name; the response uses `symbols` rather than a singular `symbol` field. |
| `/api/price/history` | 200 | Six daily adjusted observations covering 2–10 January 2025; adjustment timestamp and delay notice fields were present. |
| `/api/financials/income-statement` | 200 | Four annual periods; all four included a top-level `dateFiled`; revenue and operating income were available. |

## Observed response shape

Company details top-level keys:

```text
address, cik, city, country, description, exchanges, fiscalYearEndDay,
fiscalYearEndMonth, industry, industryGroup, investorRelationsUrl, ipoDate,
logoUrl, name, phone, sector, sic, state, status, symbols, type, webUrl, zip
```

Price history top-level keys:

```text
adjustedAsOf, cik, data, delayNotice, latestQuote, resolution, symbol
```

Income-statement top-level keys:

```text
dateFiled, derived, facts, fiscalPeriod, fiscalYear, period, sources
```

The annual statement fact set included the fields required by the approved score: `revenue` and `operatingIncome`. The response also exposed filing source maps needed to reconstruct values before later restatements, including changes reported by ordinary later filings.

A follow-up aggregate check over ten annual rows per demonstration ticker found
numeric `before` provenance on one MSFT source and one COST source. Both sources
had `amendment: false`, confirming that rollback cannot be limited to sources
explicitly marked as amendments. No financial values or source identifiers were
retained.

## Trial entitlement and limits

The three calls succeeded without plan restriction. Response headers reported a limit of 500 requests per minute and 500,000 requests per day. The remaining daily count decreased by one for each call, confirming quota accounting. The deliberately short price request was fully covered, so no history clamping was observable in this check; the longer live demonstration must report its actual returned coverage.

No bearer token, raw price value, raw financial value, source accession number or response body is recorded in this document.
