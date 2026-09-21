# StockFit point-in-time fundamental backtest

**Draft — not published**

## Problem

I wanted to extend `backtest-lib` with a realistic external-data example while
keeping the integration small enough to understand and test. The chosen task
was to combine adjusted prices with annual revenue and operating-income facts,
then compare a transparent fundamental ranking with an equal-weight portfolio.

## Why point-in-time matters

A backtest can look into the future accidentally if it uses a financial value
before the filing date or applies a later amendment to earlier decisions. The
integration therefore gates each statement by its original filing date and
rolls amended facts back to the value available on each historical decision
date. Missing inputs make a security ineligible rather than silently filling a
value.

## Architecture

The example has four narrow layers:

1. `client.py` performs the three approved HTTP reads and validates response
   status and shape without embedding credentials.
2. `transforms.py` aligns prices, reconstructs facts as of each date and builds
   the library's `MarketView` signals.
3. `strategy.py` contains the top-two ranking rule and the matched equal-weight
   comparator.
4. `demo.py` runs both portfolios and emits only derived metrics and a NAV
   comparison chart.

The default path uses explicitly labelled invented fixtures. Live calls require
the `--live` flag and a process-scoped `STOCKFIT_TOKEN`.

## Test strategy

The implementation was developed from failing tests. Unit tests cover request
construction, status and schema failures, secret redaction, price alignment,
filing-date gates, multi-source rollback, missing facts, deterministic
ranking and strategy weights. End-to-end tests assert deterministic offline
artefacts, prevent raw fields from entering outputs, and keep live use opt-in.
The complete repository suite passed 322 tests, with 3 skipped and 12
deselected; Ruff and Pyrefly also passed.

## Demonstration result

One verified live run on 21 September 2026 used AAPL, MSFT and COST. The common
price coverage contained 1,434 observations from 4 January 2021 to
18 September 2026; both completed backtest series contained 1,425 observations
through 4 September 2026.

The fundamental portfolio ended at 2,578,038.91 nominal units from 1,000,000,
a total return of 157.80%. Its annualised return was 18.23%, annualised volatility
24.19%, Sharpe ratio 0.67, maximum drawdown -32.14%, and average turnover 0.37%
per engine period.

The equal-weight comparator ended at 2,662,720.38 nominal units, a total return
of 166.27%.
Its annualised return was 18.91%, annualised volatility 20.61%, Sharpe ratio
0.82, maximum drawdown -27.95%, and average turnover 0.45% per engine period.
In this narrow demonstration, the comparator finished ahead and had better
risk-adjusted metrics.

## Limitations

This is an engineering demonstration, not evidence of a profitable investment
strategy or production readiness. It uses a fixed three-stock current-ticker
cohort, so survivorship and selection bias are substantial. It does not model
costs, spread, slippage, liquidity, taxes or market impact. Provider history may
be clamped, adjusted-price history may use a current adjustment snapshot, and
restatement reconstruction depends on the source history exposed by the API.
There was no out-of-sample study, universe reconstruction or parameter search.

## What I learned

The most important lesson was that data timing is part of strategy logic, not
just data cleaning. A small client and pure transform functions made it possible
to test awkward cases—especially restatements and missing facts—without live API
calls. A deliberately weak but matched comparator also made the result more
honest: the new signal did not beat simple equal weighting in this sample.
