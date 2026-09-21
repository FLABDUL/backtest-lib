# StockFit Point-in-Time Backtest Demo

**Status:** Approved
**Date:** 20 September 2026

## Intent

Use the limited StockFit trial to build a small, credible integration and quant case study on top of `backtest-lib`. Codex will drive implementation and verification during the trial; a separate learning guide will later help Hakim trace and understand the finished Python code.

The work will live in the existing `feat/stockfit-point-in-time-demo` worktree. It will initially be an example integration rather than a change to the library's public API.

## Success criteria

By the end of the week, the project should:

- authenticate to StockFit and exercise company details, price history and income-statement endpoints;
- convert the selected responses into the structures needed by `backtest-lib`;
- use `dateFiled` to prevent financial information from entering a simulation before it was available;
- run one deliberately simple, untuned fundamental strategy against an equal-weight baseline;
- report reproducible performance statistics and limitations without making an alpha claim;
- run unit tests without network access, using synthetic fixtures that are safe to publish;
- provide a concise case-study draft, derived chart or table assets, and a retrospective learning guide.

## Non-goals

This week will not attempt to build:

- a general market-data provider framework or complete StockFit SDK;
- a large or permanently mirrored StockFit dataset;
- live trading, portfolio optimisation or parameter searching;
- a web application;
- a package release, licence change or public ownership claim;
- changes to the core `backtest-lib` API unless the example exposes a concrete blocker and a revised design is approved.

## Proposed structure

The integration will remain visibly separate from the library core:

```text
examples/stockfit/
    client.py       # Narrow authenticated HTTP client with injectable transport
    transforms.py   # StockFit responses to Polars/backtest-lib inputs
    strategy.py     # Point-in-time signal selection and portfolio rule
    demo.py         # Reproducible command-line demonstration
tests/unit/examples/stockfit/
    ...             # Synthetic response and behaviour tests
docs/
    ...             # Usage, methodology, limitations and learning guide
```

The client will cover only the three endpoints needed by the demonstration. Its HTTP transport will be injectable so authentication, query construction, timeouts and failures can be tested without live requests. The implementation should avoid adding a runtime dependency unless that materially simplifies or strengthens the result.

## Data flow

```text
local token
    -> narrow StockFit client
    -> bounded response validation
    -> price and financial transforms
    -> MarketView plus dateFiled-gated signals
    -> backtest-lib strategy and equal-weight baseline
    -> derived metrics, charts and case-study material
```

Price observations will be ordered and normalised before they enter `MarketView`. Financial records will be usable only from their filing date, never merely from the period-end date. The strategy will perform an as-of lookup for each simulated decision date.

## Trial usage and data handling

- Use a fixed demonstration cohort of roughly three to five liquid companies and explicit date bounds.
- Make the smallest live requests needed to establish access, schema and usable history.
- Keep the StockFit token in a process environment variable or a private file outside the repository. Never print, log, commit or place it in notebooks.
- Keep any temporary raw responses outside Git and narrowly bounded. Delete them when no longer required if the provider terms or trial conditions require that.
- Store only invented synthetic fixtures in the public project. Website material will contain derived results and methodology, not redistributed raw StockFit data.
- Record endpoint coverage, dates and limitations in a sanitised run summary without recording credentials or full proprietary responses.

## Client behaviour and failures

The client will provide explicit handling for:

- invalid requests or symbols (`400`);
- missing or invalid authentication (`401`);
- unavailable plan features (`403`);
- rate limiting (`429`), including surfaced retry/rate-limit metadata without uncontrolled automatic retries;
- timeouts, connection failures, malformed JSON and missing/null fields.

Errors should be actionable and sanitised. No exception or diagnostic output may contain the bearer token.

## Quant methodology

The demonstration will use one transparent rule based on revenue growth and profitability, with no parameter search during the trial. A simple equal-weight portfolio over the same cohort and dates will be the baseline.

The write-up will state that this is an engineering and point-in-time-data demonstration, not evidence of a deployable trading edge. It will explicitly discuss at least:

- the small, static cohort and survivorship bias;
- the limited trial history and any plan-dependent coverage;
- price adjustment assumptions;
- filing-date availability and remaining reporting-lag caveats;
- transaction costs, liquidity and other omitted market frictions.

## Test and verification strategy

Implementation will proceed test-first in small slices:

1. Write a failing behaviour test.
2. Add the minimum code to pass it.
3. Refactor while keeping the focused and baseline suites green.

Unit tests will use synthetic StockFit-shaped data and cover authentication headers, parameters, timeouts, status handling, timestamp ordering, null or missing values, duplicate observations and point-in-time behaviour immediately before and on a filing date.

Live access will be confined to an explicit smoke command or test that skips when no token is configured. It will never be part of the default test suite. The existing pytest, Ruff and Pyrefly checks will remain the regression baseline; the known Windows documentation filename collision will be documented separately rather than attributed to this feature.

## Week plan

### Day 1 — Access and contract

Rotate and store the exposed credential safely, perform three minimal endpoint calls, capture only schema/coverage observations, and freeze the small demonstration cohort and date range.

### Day 2 — Narrow client

Build the tested client for authentication, parameters, timeouts and relevant HTTP failures.

### Day 3 — Point-in-time transforms

Convert price data into `MarketView` input and financial records into an as-of signal table gated by `dateFiled`.

### Day 4 — Strategy and baseline

Implement the untuned fundamental rule and equal-weight comparator, then run both through `backtest-lib`.

### Day 5 — Results and limitations

Produce deterministic derived metrics and visual material, then review the methodology for look-ahead and interpretation risks.

### Day 6 — Hardening and documentation

Expand edge-case tests, improve errors, document setup and offline reproduction, and ensure no restricted raw data or secrets are tracked.

### Day 7 — Portfolio and learning hand-off

Prepare the website case-study draft, demonstration script and a guided code-reading path with breakpoints and exercises for retrospective learning.

## Deliverables

- A narrow StockFit example client, transforms and strategy.
- Synthetic unit fixtures plus an opt-in live smoke check.
- A reproducible demonstration and equal-weight comparison.
- Derived metrics and one or two presentation-quality visual assets.
- Setup, methodology and limitations documentation.
- A website case-study draft that remains unpublished until separately approved.
- A retrospective Python learning guide tied to the implemented files and tests.

## Acceptance criteria

The implementation is ready for hand-off when:

- all three required endpoint contracts have been verified during the trial;
- a financial value cannot influence a simulated date earlier than its `dateFiled` value;
- the demonstration and baseline run reproducibly from documented commands;
- default tests require neither network access nor a StockFit credential;
- focused tests and the existing regression checks pass, with unrelated known limitations clearly separated;
- repository and generated output scans contain no credential or proprietary raw response;
- the case study accurately distinguishes demonstrated engineering from unproven investment performance.

No commit, push, publication or deployment is included without separate explicit approval.
