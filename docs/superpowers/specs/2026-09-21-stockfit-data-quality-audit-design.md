# StockFit Cross-Sectional Data-Quality Audit

**Status:** Approved
**Date:** 21 September 2026

## Intent

Use the remaining StockFit trial to produce a recruiter-facing assessment of
whether a small cross-section of US company data is structurally suitable for
point-in-time research. The audit measures data availability, temporal
integrity and provenance; it does not search for a trading strategy or make an
investment-performance claim.

The implementation will extend the existing `examples/stockfit` integration
without changing the public `backtest-lib` API. Raw provider responses will be
processed in memory and discarded. Only derived audit metadata and visual
summaries may be persisted.

## Frozen cohort

The live cohort is fixed before observing audit outcomes:

```text
AAPL, MSFT   technology
JPM, BAC     financials
XOM, CVX     energy
JNJ, PFE     healthcare
WMT, COST    consumer staples
CAT, UNP     industrials
```

The sector labels above explain the selection; live company metadata remains
the source for the reported sector and industry fields. The audit must not add,
remove or substitute symbols in response to their results.

The requested price range is 1 January 2021 through the live execution date.
Annual income statements use a limit of ten periods with split adjustment
enabled. The default offline demonstration continues to use the existing
invented AAPL, MSFT and COST fixtures.

## Approaches considered

### Selected: metadata-only cross-sectional audit

Reuse company details, adjusted price history and annual income statements.
This is sufficient to examine coverage, filing lag, missing facts, fiscal gaps
and point-in-time provenance while keeping the network and schema surface
small.

### Deferred: all three financial statements

Adding balance-sheet and cash-flow endpoints would broaden coverage but would
roughly triple the provider schema and test surface. It is not needed to answer
the current data-quality question.

### Deferred: twelve-stock strategy study

Running the strategy over the larger cohort would mix data-quality findings
with sector-specific accounting comparability and investment interpretation.
Robustness testing remains a separate, later phase.

## Architecture

The audit remains outside the library's public API:

```text
examples/stockfit/
    client.py       existing authenticated network boundary
    audit.py        pure company/cohort audit calculations and output models
    audit_cli.py    offline-by-default orchestration and explicit live mode
tests/unit/examples/stockfit/
    test_audit.py
    test_audit_cli.py
tests/e2e/
    test_stockfit_audit.py
artifacts/
    stockfit-audit/         deterministic synthetic outputs
    stockfit-audit-live/    derived live outputs
```

`audit.py` accepts already-decoded mappings and sequences. It performs no I/O
and has no credential or environment access. `audit_cli.py` owns fixture
loading, bounded client calls and artifact writing. The existing
`StockFitClient` remains the sole HTTP boundary.

No new runtime dependency is required. JSON and CSV use the standard library;
the heatmap uses the existing Altair dependency.

## Live data flow

For each frozen symbol, in cohort order:

1. Fetch company details.
2. Fetch adjusted daily prices over the fixed request range.
3. Fetch up to ten annual income-statement periods.
4. Calculate the per-company audit record in memory.
5. Release all references to response payloads before advancing.

After all companies have been examined, calculate cohort aggregates and write
the three derived artifacts. Requests are sequential and there is no automatic
retry. A complete live run therefore makes 36 bounded calls.

The default command is offline and does not inspect the environment for a
token. Live execution requires both `--live` and a non-empty
`STOCKFIT_TOKEN`.

## Per-company audit record

Each row in `company-quality.csv` contains only the following derived or public
metadata:

- symbol, company name, sector and industry;
- whether stable identifiers were present, without retaining their values;
- returned price start, end and observation count;
- duplicate, invalid and out-of-order price observation counts;
- largest calendar gap between consecutive observations;
- returned annual statement count and fiscal-year range;
- missing and duplicate fiscal-year counts;
- revenue and operating-income completeness percentages;
- median and maximum days from fiscal period end to original filing date;
- source-entry count, numeric `before` entry count and derived-fact incidence;
- status and a stable, sanitised list of reason codes.

The CSV must not contain prices, revenues, operating income, accession numbers,
source-map keys, response bodies, bearer values or free-text error bodies.

## Assessment rules

The audit uses transparent checks rather than a composite score. Overall status
is the most severe triggered status: `fail` outranks `review`, which outranks
`pass`.

### Fail conditions

- A response has the wrong top-level JSON shape.
- Price history is empty or contains a duplicate timestamp, non-numeric value,
  non-finite value or non-positive value.
- An annual row has a malformed period or filing date.
- An original filing date precedes its fiscal period end.
- Annual statement periods contain duplicate fiscal years.
- No consecutive pair of annual periods has usable positive revenue and usable
  operating income for the later period.

### Review conditions

- Company sector or industry metadata is missing.
- The returned price start is more than 31 calendar days after the requested
  start.
- The returned price end is more than seven calendar days before the execution
  date.
- The largest calendar gap between prices exceeds seven days.
- Fewer than five annual periods are returned.
- At least one fiscal year is missing between the earliest and latest returned
  annual period.
- Revenue or operating-income completeness is below 100 percent.
- The maximum filing lag exceeds 90 days.

Fiscal-year duplicate and gap checks use the response's numeric `fiscalYear`.
If that field is absent or invalid, the calendar year of the validated `period`
is used as a fallback and the company is marked for review.

### Informational measures

Out-of-order observations are counted before normalisation but do not change
status when timestamps remain unique and values valid. The count of derived
facts and the presence or absence of restatement provenance are informational:
a company is not defective merely because no fact was restated.

Numeric `before` provenance is counted by dated source entry regardless of the
source's `amendment` flag. This matches the provider behaviour already observed
during the point-in-time demonstration.

## Cohort summary

`summary.json` contains:

- provenance mode (`synthetic` or `live`) and UTC execution timestamp;
- frozen cohort, request bounds and actual shared price coverage;
- run status (`complete` or `incomplete`);
- counts of pass, review and fail companies;
- cohort price and statement coverage ranges;
- completeness distributions for the two required facts;
- median and maximum filing-lag summaries;
- counts of source entries, numeric `before` provenance and derived facts;
- the full assessment thresholds and reason-code definitions;
- allow-listed rate-limit metadata and sanitised failure categories;
- a statement that raw provider data was not retained.

The summary does not copy the per-company CSV rows verbatim and contains no raw
financial or price values.

## Visual output

`data-quality.svg` is a static heatmap with companies as rows and audit checks
as columns. Cells use labelled pass, review and fail states; text or symbols
supplement colour so the chart remains readable without relying solely on
colour perception.

The SVG has no tooltip, embedded raw dataset, download control or interactive
export. Its subtitle says either `Synthetic demonstration data` or
`Derived from StockFit data; raw data not redistributed`.

## Failure handling

- HTTP `401` or `403` aborts the run because authentication or entitlement is
  global. No new artifacts are published and existing outputs are left intact.
- HTTP `429` aborts without retry and reports only allow-listed rate-limit
  metadata.
- A timeout or connection failure marks the affected company incomplete,
  continues with the cohort and makes the process exit non-zero.
- Missing fields, shortened history and fiscal gaps are audit findings rather
  than orchestration exceptions.
- Malformed company data produces a sanitised fail record and processing
  continues where the response boundary remains trustworthy.

An incomplete run may write derived artifacts, but `run_status` must be
`incomplete`, the missing symbols must be listed, and the process must exit
non-zero. Artifact files are written through temporary siblings and replaced
only after all three representations are successfully rendered, preventing a
mixed old/new output set.

No error, traceback, CSV cell, JSON value or chart label may contain a bearer
token, response body, financial value, price value, accession number or source
identifier.

## Testing strategy

Development proceeds test-first. Network-free tests cover:

- every pass, review and fail rule at its exact boundary;
- price ordering, duplicate timestamps, invalid values and gap calculation;
- fiscal-year duplicates and gaps;
- filing-lag calculation and malformed dates;
- required-fact completeness, derived facts and source provenance;
- ordinary later filings whose numeric `before` values have
  `amendment: false`;
- worst-status aggregation and stable reason ordering;
- authentication, rate-limit, timeout and partial-run orchestration;
- token and raw-value exclusion from exceptions and outputs;
- deterministic JSON, CSV and SVG bytes for a fixed clock;
- offline-by-default CLI behaviour and exact live request bounds.

The existing repository suite, Ruff, formatter and Pyrefly remain mandatory.
The live run is manual and explicit, followed by credential, raw-data and SVG
export scans plus visual inspection.

## Success criteria

- The default audit runs offline without a token or network access.
- A live run audits exactly the twelve frozen symbols with at most 36 requests.
- Every judgement is explainable through a declared metric and reason code.
- Outputs contain only the approved derived/public metadata.
- Partial execution is visible and returns a non-zero exit status.
- All focused and repository-wide checks pass.
- The resulting table, summary and heatmap are suitable for later case-study
  review without claiming provider perfection or investment performance.

## Non-goals

- Persisting or mirroring StockFit response payloads.
- Adding balance-sheet, cash-flow, as-reported or unrelated endpoints.
- Changing the strategy, ranking securities or presenting investment results.
- Constructing a survivorship-bias-free historical universe.
- Comparing financial values across sectors.
- Building a general provider SDK, database, dashboard or web application.
- Publishing, pushing or deploying artifacts without separate approval.

## References

- [StockFit point-in-time data guide](https://developer.stockfit.io/blog/point-in-time-data-backtesting)
- [StockFit fundamentals data model](https://developer.stockfit.io/blog/holy-grail-api-stock-backtesting)
- [StockFit discussion of sector-specific metrics](https://developer.stockfit.io/blog/fundamental-stock-analysis)
