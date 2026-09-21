# StockFit point-in-time example

This example connects `backtest-lib` to a deliberately small StockFit data
adapter. It demonstrates how to turn adjusted prices and filing-dated annual
income statements into a point-in-time fundamental strategy and a matched
equal-weight comparator.

The default run is offline. Its fixtures are invented, synthetic data shaped
like the fields consumed from StockFit; they are not StockFit responses and
must not be treated as investment evidence.

## Setup

From the repository root, install Python 3.14 and `uv`, then let `uv` create the
project environment:

```powershell
python --version
uv sync
uv run python --version
```

Run the deterministic offline demonstration:

```powershell
uv run python -m examples.stockfit.demo --output-dir artifacts/stockfit
```

It writes only derived output:

- `artifacts/stockfit/summary.json`
- `artifacts/stockfit/nav-comparison.svg`

Run the deterministic offline data-quality audit:

```powershell
uv run python -m examples.stockfit.audit_cli --output-dir artifacts/stockfit-audit
```

Its method and current draft results are documented in
[`data-quality-audit.md`](data-quality-audit.md).

## Opt-in live checks

Keep the StockFit token outside the repository. One PowerShell pattern is to
read an already-created private file into the current process, run the command,
and remove the environment variable immediately afterwards:

```powershell
$secretPath = "$env:USERPROFILE\.codex\secrets\stockfit-token.txt"
$env:STOCKFIT_TOKEN = [IO.File]::ReadAllText($secretPath).Trim()
try {
  uv run python -m examples.stockfit.smoke
} finally {
  Remove-Item Env:STOCKFIT_TOKEN -ErrorAction SilentlyContinue
}
```

The smoke command makes three bounded requests for one symbol and prints schema
metadata only. The observed contract is recorded in
[`trial-contract.md`](trial-contract.md).

The live demonstration is a separate, explicit action:

```powershell
$secretPath = "$env:USERPROFILE\.codex\secrets\stockfit-token.txt"
$env:STOCKFIT_TOKEN = [IO.File]::ReadAllText($secretPath).Trim()
try {
  uv run python -m examples.stockfit.demo --live --output-dir artifacts/stockfit-live
} finally {
  Remove-Item Env:STOCKFIT_TOKEN -ErrorAction SilentlyContinue
}
```

It requests AAPL, MSFT and COST, then keeps only a derived summary and SVG NAV
chart in `artifacts/stockfit-live`. Do not commit bearer tokens, raw StockFit
responses, prices, financial values, filing source maps, or API logs. Review
generated artefacts before deciding whether they belong in version control.

The twelve-company live data-quality audit is also explicit and writes only
derived audit metadata:

```powershell
$secretPath = "$env:USERPROFILE\.codex\secrets\stockfit-token.txt"
$env:STOCKFIT_TOKEN = [IO.File]::ReadAllText($secretPath).Trim()
try {
  uv run python -m examples.stockfit.audit_cli --live --output-dir artifacts/stockfit-audit-live
} finally {
  Remove-Item Env:STOCKFIT_TOKEN -ErrorAction SilentlyContinue
}
```

## Design and learning material

- [`methodology.md`](methodology.md) defines the signal and time gates.
- [`case-study-draft.md`](case-study-draft.md) is an unpublished portfolio
  narrative based on the verified live run.
- [`learning-guide.md`](learning-guide.md) provides a 90-minute code-tracing
  session for revisiting the implementation in VS Code.
