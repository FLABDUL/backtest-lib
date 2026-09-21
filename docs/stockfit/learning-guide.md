# 90-minute retrospective learning guide

Open the repository in VS Code and select
`.venv\Scripts\python.exe` as the interpreter. Use the Run and Debug view with
`tests/e2e/test_stockfit_demo.py`, or start with:

```powershell
uv run pytest tests/e2e/test_stockfit_demo.py -q
```

The objective is to trace one piece of synthetic data from request-shaped input
to a portfolio decision. Use the offline path; no token is needed.

## 0–15 minutes: client boundary

Read `examples/stockfit/client.py`. Set breakpoints at:

- `StockFitClient.price_history` where query parameters are constructed;
- `StockFitClient._get` immediately before the request is opened;
- `_get` where the HTTP status is validated;
- `_get` where the decoded JSON shape is validated.

Run the focused client tests and inspect `url`, `query`, `status` and the decoded
object. Notice that exception text contains context but never the bearer token
or response body.

## 15–40 minutes: time-aware transforms

Read `examples/stockfit/transforms.py`. Set breakpoints at:

- `fact_as_of` after `original_filing_date` is parsed;
- the reverse-sorted source rollback loop;
- `_score_as_of` after `available_indexes` is built;
- `_score_as_of` where the score and eligibility flag are returned;
- `signal_frames` while iterating over an `as_of` date.

Run:

```powershell
uv run pytest tests/unit/examples/stockfit/test_transforms.py -q
```

Watch how the same statement produces a different fact before and after a later
filing date.

## 40–55 minutes: portfolio decision

Read `examples/stockfit/strategy.py`. In the inner `strategy` function, stop
after `score` and `eligible` are read, then again after `ranked` is created.
Inspect the deterministic sort key and the weights returned by
`target_weights`. Run:

```powershell
uv run pytest tests/unit/examples/stockfit/test_strategy.py -q
```

## 55–75 minutes: complete backtest

Read `examples/stockfit/demo.py`. Set breakpoints at:

- `run_demo` immediately after `build_market`;
- each `btl.Backtest(...)` construction;
- `Backtest.run` in `src/backtest_lib/backtest/__init__.py`;
- `_metrics` when the results are converted to the safe summary.

Step through one decision period. Follow `market.signals` into the strategy,
the returned decision into the engine, and the new holdings into the NAV. Then
run the offline command and open `artifacts/stockfit/nav-comparison.svg`.

## 75–90 minutes: six exercises

Use one exercise per short debugging session; rerun the smallest relevant test
after every change.

1. Change one synthetic `dateFiled` value and predict the first eligible price
   date before running the transform test.
2. Put breakpoints around a fixture's two later sources and write down each value
   produced while rollback proceeds from newest to oldest.
3. Remove `operatingIncome` from one synthetic filing and confirm that the
   security becomes ineligible rather than receiving a partial score.
4. Change `make_fundamental_strategy(top_n=2)` to `top_n=1`, predict the target
   weights, then compare the generated summary.
5. Trace the first decision for the cash-starting fundamental strategy and the
   equal-weight comparator; explain why their entry dates can differ.
6. Add one failing test for a malformed price observation or statement fact,
   then make the smallest implementation change needed to pass it.

Finish by reverting experimental fixture or strategy changes unless you intend
to develop them as a separate, reviewed feature.
