# Methodology

## Data and alignment

The demonstration uses a fixed current-ticker cohort of AAPL, MSFT and COST.
For each ticker it requests adjusted daily price history and annual income
statements. Price series are validated, sorted and restricted to dates shared
by every member of the cohort. No missing price is forward-filled.

The requested live start is 1 January 2021, but the effective period is always
the returned common coverage. The verified run covered 4 January 2021 to
18 September 2026. Backtest results end on 4 September 2026 because the engine
stops at the final monthly decision-schedule boundary and truncates results to
that completed iteration.

## Point-in-time signal

At each price date, a statement becomes eligible only when its original
`dateFiled` is on or before that date. The latest eligible fiscal period and
its preceding annual period provide the inputs:

```text
revenue_growth = current_revenue / previous_revenue - 1
operating_margin = current_operating_income / current_revenue
fundamental_score = revenue_growth + operating_margin
```

Both revenues must be positive, and revenue and operating income must be
present and finite. Otherwise the security is ineligible at that date.

StockFit can expose the current restated fact plus dated source history. When
evaluating an earlier date, `fact_as_of` walks later sources from newest to
oldest and replaces the current value with each relevant numeric `before`
value. This applies whether the source is marked as an amendment or an ordinary
later filing, because either can report a changed fact. The reverse-order
rollback reconstructs the value knowable at the decision date rather than
leaking a later restatement backwards.

## Portfolio rules

The backtest starts with 1,000,000 nominal portfolio units in cash and makes
decisions monthly. The
fundamental strategy ranks eligible securities by descending score, with the
ticker as a deterministic tie-break, then assigns equal weights to the top two.
It remains at its existing position—initially cash—until at least one security
is eligible. The comparator targets equal weights across the same three
securities on the same dates and also starts in cash.

There is no parameter search: the score formula, top-two rule, cohort, monthly
schedule and comparator were specified before inspecting the demonstration
result.

## Limitations

- **Survivorship bias:** the fixed cohort consists of current tickers and does
  not reconstruct the investable universe at each historical date.
- **History clamping:** actual coverage depends on what the provider returns,
  not solely on the requested dates.
- **Adjustment-snapshot risk:** adjusted prices may reflect a present-day
  corporate-action snapshot. That can differ from adjustments knowable in the
  past.
- **Incomplete restatement provenance:** rollback is only as complete as the
  exposed source map and its `before` values.
- **Missing facts:** absent or non-positive revenues and absent operating income
  make a security ineligible; no estimate or imputation is used.
- **Reporting lag:** original filing dates gate availability, but ingestion or
  market reaction delays are not modelled.
- **Simplified execution:** the simulation omits trading costs, bid-ask spread,
  slippage, taxes, liquidity limits and market impact.
- **Small sample:** three hand-picked companies and roughly five and a half
  years cannot establish general performance.
- **Synthetic fixture metrics:** offline fixtures use sparse invented dates.
  Metrics annualised by the engine's daily-period convention are useful for
  determinism tests only, not interpretation.
