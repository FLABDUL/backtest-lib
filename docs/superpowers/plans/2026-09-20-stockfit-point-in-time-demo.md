# StockFit Point-in-Time Backtest Demo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a narrow, tested StockFit integration that demonstrates a point-in-time fundamental backtest and produces truthful portfolio-ready documentation and derived results.

**Architecture:** Keep provider-specific code under `examples/stockfit`, outside the library's public API. A small standard-library HTTP client feeds validated StockFit-shaped mappings into pure Polars transforms; those transforms reconstruct amended facts as they were known on each date, align them to price dates, and supply time-fenced `MarketView` signals to simple strategies. The default demo runs from invented offline fixtures, while explicit live modes use `STOCKFIT_TOKEN` and emit only sanitised summaries and derived outputs.

**Tech Stack:** Python 3.14, standard-library `urllib`, Polars, backtest-lib, pytest, Ruff, Pyrefly, Altair/Vega-Lite.

**Spec:** `docs/superpowers/specs/2026-09-20-stockfit-point-in-time-demo-design.md`

## Global Constraints

- Cover only `GET /api/company/details`, `GET /api/price/history`, and `GET /api/financials/income-statement`.
- Keep the integration outside the `backtest_lib` public API unless a separately approved design changes that boundary.
- Use a fixed cohort of `AAPL`, `MSFT`, and `COST`; use annual statements and daily adjusted prices.
- Never print, log, commit, cache inside Git, or place a StockFit token in a notebook.
- Never commit StockFit raw responses; public fixtures must be invented and visibly synthetic.
- Treat facts as knowable only on or after the original `dateFiled`, and reconstruct values superseded by later amendments from `sources[*].facts[*].before`.
- Use one transparent score: year-on-year revenue growth plus operating margin; do not tune it during the trial.
- Compare against an equal-weight strategy over identical securities, dates, capital, and monthly decision schedule.
- Default tests and the default demo must not require a network connection or credential.
- Do not commit, push, publish, deploy, or alter licensing without separate explicit approval.

## Review Focus

- A token appearing in an HTTP error body, exception or debug representation must still be redacted; Task 1 adds a hostile-body test.
- A successful HTTP response with valid JSON of the wrong top-level shape must fail at the client boundary; Task 1 tests object-versus-list contracts.
- Multiple amendments to the same fact must roll back in reverse filing order for historical dates; Task 3 tests a two-amendment chain.
- Securities with missing or zero prior revenue must remain ineligible rather than produce infinity or a misleading zero score; Task 3 tests both cases.
- A monthly decision before any company has a valid score must remain in cash rather than inherit a future or default allocation; Task 4 tests the no-signal period.

---

## File map

- `examples/stockfit/__init__.py`: package marker and short example description.
- `examples/stockfit/client.py`: narrow authenticated client, transport seam, validation and sanitised errors.
- `examples/stockfit/smoke.py`: explicit live entitlement/schema check that prints metadata only.
- `examples/stockfit/transforms.py`: price-frame conversion, amendment rollback, point-in-time score frames and `MarketView` construction.
- `examples/stockfit/strategy.py`: fundamental ranking strategy and equal-weight comparator.
- `examples/stockfit/demo.py`: offline-by-default and explicit live backtest orchestration, metrics and chart output.
- `examples/stockfit/fixtures/*.json`: invented StockFit-shaped data for offline demonstration and tests.
- `tests/unit/examples/stockfit/test_client.py`: client request, validation, status and redaction behaviour.
- `tests/unit/examples/stockfit/test_smoke.py`: sanitised summary and missing-token behaviour.
- `tests/unit/examples/stockfit/test_transforms.py`: price alignment, point-in-time and amendment behaviour.
- `tests/unit/examples/stockfit/test_strategy.py`: selection, cash and baseline decisions.
- `tests/e2e/test_stockfit_demo.py`: deterministic offline end-to-end run.
- `docs/stockfit/README.md`: setup, commands, architecture and data-handling rules.
- `docs/stockfit/methodology.md`: score, point-in-time treatment and limitations.
- `docs/stockfit/case-study-draft.md`: unpublished website copy based only on verified results.
- `docs/stockfit/learning-guide.md`: retrospective tracing path, breakpoints and exercises.
- `artifacts/stockfit/.gitkeep`: destination for derived summaries and charts; raw responses are prohibited.

### Task 1: Narrow StockFit HTTP client

**Files:**
- Create: `examples/stockfit/__init__.py`
- Create: `examples/stockfit/client.py`
- Test: `tests/unit/examples/stockfit/test_client.py`

**Interfaces:**
- Consumes: a bearer token string and an optional `Transport` callable.
- Produces: `StockFitClient.company_details(symbol) -> JsonObject`, `price_history(symbol, start, end, resolution="1d") -> JsonObject`, and `income_statement(symbol, period="annual", limit=20) -> list[JsonObject]`.

- [ ] **Step 1: Create the package and write failing request-construction tests**

```python
# tests/unit/examples/stockfit/test_client.py
from datetime import date

from examples.stockfit.client import StockFitClient


def test_price_history_builds_authenticated_bounded_request() -> None:
    seen = []

    def transport(request, timeout):
        seen.append((request, timeout))
        return 200, {}, b'{"symbol":"AAPL","data":[]}'

    client = StockFitClient("secret-token", transport=transport, timeout=3.5)
    result = client.price_history(
        "aapl", date(2025, 1, 1), date(2025, 1, 31), resolution="1d"
    )

    request, timeout = seen[0]
    assert result["symbol"] == "AAPL"
    assert request.get_header("Authorization") == "Bearer secret-token"
    assert "symbol=AAPL" in request.full_url
    assert "from=2025-01-01" in request.full_url
    assert "to=2025-01-31" in request.full_url
    assert timeout == 3.5
```

- [ ] **Step 2: Run the focused test and confirm the import fails**

Run: `uv run pytest tests/unit/examples/stockfit/test_client.py::test_price_history_builds_authenticated_bounded_request -v`

Expected: FAIL because `examples.stockfit.client` does not exist.

- [ ] **Step 3: Implement the client boundary and injectable standard-library transport**

```python
# examples/stockfit/client.py
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

type JsonObject = dict[str, Any]
type Transport = Callable[[Request, float], tuple[int, Mapping[str, str], bytes]]

BASE_URL = "https://api.stockfit.io/v1"


class StockFitError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _urlopen_transport(
    request: Request, timeout: float
) -> tuple[int, Mapping[str, str], bytes]:
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()
    except (TimeoutError, URLError) as exc:
        raise StockFitError("StockFit request failed before a response arrived") from exc


@dataclass(slots=True)
class StockFitClient:
    token: str = field(repr=False)
    base_url: str = BASE_URL
    timeout: float = 10.0
    transport: Transport = field(default=_urlopen_transport, repr=False)
    last_response_headers: dict[str, str] = field(
        default_factory=dict, init=False, repr=False
    )
```

Implement `_get(path, params, expected_type)`, uppercase/trimmed symbol validation, ISO dates, and the three public endpoint methods. Save response headers in `last_response_headers` so callers can inspect quota metadata without accessing the token. Encode booleans as lowercase JSON strings. Parse an error's JSON `message` only after replacing any occurrence of `self.token` with `[REDACTED]`; otherwise use a status-only message.

- [ ] **Step 4: Add failure and shape tests**

Add parameterised tests for `400`, `401`, `403`, and `429`, asserting `StockFitError.status`. Add malformed JSON and wrong-shape tests. Include a hostile error body containing `secret-token` and assert the token appears in neither `str(exc_info.value)` nor `repr(exc_info.value)`. Test blank tokens, blank symbols, reversed date bounds, invalid resolution, `limit=0`, and a valid income-statement list.

- [ ] **Step 5: Run and format the client slice**

Run:

```powershell
uv run pytest tests/unit/examples/stockfit/test_client.py -v
uv run ruff check examples/stockfit/client.py tests/unit/examples/stockfit/test_client.py
uv run ruff format --check examples/stockfit/client.py tests/unit/examples/stockfit/test_client.py
```

Expected: all client tests pass; Ruff reports no errors or reformatting required.

- [ ] **Step 6: Review the uncommitted slice**

Run: `git diff --check; git status --short`

Expected: only the approved design/plan and Task 1 files are changed. Do not commit.

### Task 2: Sanitised live smoke check and trial contract record

**Files:**
- Create: `examples/stockfit/smoke.py`
- Create: `tests/unit/examples/stockfit/test_smoke.py`
- Create: `docs/stockfit/trial-contract.md`

**Interfaces:**
- Consumes: `StockFitClient` and `STOCKFIT_TOKEN` in the process environment.
- Produces: `summarise_contract(client, symbol="AAPL") -> dict[str, object]` containing identifiers, counts, key names and date coverage—but no bearer token or raw fact values.

- [ ] **Step 1: Write failing sanitisation tests**

```python
def test_summary_contains_shape_not_raw_financial_values(fake_client) -> None:
    summary = summarise_contract(fake_client, symbol="AAPL")
    encoded = json.dumps(summary)
    assert summary["price_history"]["observations"] == 2
    assert summary["income_statement"]["periods"] == 2
    assert "facts_keys" in summary["income_statement"]
    assert "100000000" not in encoded
    assert "token" not in encoded.lower()
```

Also test that `main()` exits with a concise instruction when `STOCKFIT_TOKEN` is absent and never falls back to the external secret-file path itself.

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run: `uv run pytest tests/unit/examples/stockfit/test_smoke.py -v`

Expected: FAIL because `examples.stockfit.smoke` does not exist.

- [ ] **Step 3: Implement the bounded smoke call**

Use exactly:

```python
DETAIL_SYMBOL = "AAPL"
PRICE_START = date(2025, 1, 1)
PRICE_END = date(2025, 1, 10)
STATEMENT_LIMIT = 4
```

Call company details once, daily adjusted price history once, and annual income statements once. Return only resolved symbol/CIK/name, observation counts, first/last dates, top-level key names, available fact-key names, statement count, filing-date presence and rate-limit header metadata if exposed by the client. Print the summary with `json.dumps(..., indent=2, sort_keys=True)`.

- [ ] **Step 4: Run the unit tests**

Run: `uv run pytest tests/unit/examples/stockfit/test_smoke.py -v`

Expected: PASS without network access.

- [ ] **Step 5: Load the rotated token without printing it and execute the live smoke check**

Run in PowerShell:

```powershell
$secretPath = "$env:USERPROFILE\.codex\secrets\stockfit-token.txt"
$env:STOCKFIT_TOKEN = [System.IO.File]::ReadAllText($secretPath).Trim()
uv run python -m examples.stockfit.smoke
Remove-Item Env:STOCKFIT_TOKEN
```

Expected: JSON reports successful access to all three endpoint families. If an endpoint returns `403`, record the entitlement limitation and adjust the demonstration scope before continuing; do not substitute an unapproved endpoint.

- [ ] **Step 6: Record the sanitised contract**

Write `docs/stockfit/trial-contract.md` with the execution date, endpoint/status table, returned top-level keys, observation/period counts, price coverage, presence of `dateFiled`, rate-limit metadata, and any plan clamping. Do not include raw prices, financial values, response bodies, headers containing credentials, or the token.

- [ ] **Step 7: Scan and review the uncommitted slice**

Run:

```powershell
uv run pytest tests/unit/examples/stockfit/test_client.py tests/unit/examples/stockfit/test_smoke.py -q
rg -n --hidden --glob '!.git/**' --glob '!*.lock' 'Bearer |STOCKFIT_TOKEN=' .
git diff --check
```

Expected: tests pass; the scan finds documentation/code references only, with no credential value. Do not commit.

### Task 3: Price and point-in-time fundamental transforms

**Files:**
- Create: `examples/stockfit/transforms.py`
- Test: `tests/unit/examples/stockfit/test_transforms.py`

**Interfaces:**
- Consumes: mappings of symbol to StockFit price objects and annual statement lists.
- Produces: `price_frame(payloads) -> pl.DataFrame`, `fact_as_of(statement, fact, as_of) -> float | None`, `signal_frames(statements, dates) -> dict[str, pl.DataFrame]`, and `build_market(price_payloads, statements) -> btl.MarketView`.

- [ ] **Step 1: Write failing price-frame tests**

```python
def test_price_frame_sorts_and_intersects_dates() -> None:
    payloads = {
        "AAA": {"symbol": "AAA", "data": [[1735862400000, 12.0], [1735689600000, 10.0]]},
        "BBB": {"symbol": "BBB", "data": [[1735689600000, 20.0], [1735776000000, 21.0], [1735862400000, 22.0]]},
    }
    assert price_frame(payloads).to_dict(as_series=False) == {
        "date": [date(2025, 1, 1), date(2025, 1, 3)],
        "AAA": [10.0, 12.0],
        "BBB": [20.0, 22.0],
    }
```

Add tests rejecting a duplicate timestamp, `null` value, malformed tuple, non-positive price, mismatched response symbol, empty intersection and an empty cohort.

- [ ] **Step 2: Implement and verify `price_frame`**

Convert Unix milliseconds to UTC calendar dates, preserve caller symbol order, sort ascending and use only complete dates shared by every security. Reject duplicate symbol/date pairs instead of silently choosing a value.

Run: `uv run pytest tests/unit/examples/stockfit/test_transforms.py -k price -v`

Expected: all price tests pass.

- [ ] **Step 3: Write failing filing-date and amendment tests**

```python
def test_fact_as_of_rolls_back_multiple_amendments() -> None:
    statement = {
        "dateFiled": "2023-02-01",
        "facts": {"revenue": 130.0},
        "sources": {
            "base": {"dateFiled": "2023-02-01", "amendment": False, "facts": {"revenue": {}}},
            "a1": {"dateFiled": "2023-06-01", "amendment": True, "facts": {"revenue": {"before": 100.0}}},
            "a2": {"dateFiled": "2023-09-01", "amendment": True, "facts": {"revenue": {"before": 120.0}}},
        },
    }
    assert fact_as_of(statement, "revenue", date(2023, 1, 31)) is None
    assert fact_as_of(statement, "revenue", date(2023, 3, 1)) == 100.0
    assert fact_as_of(statement, "revenue", date(2023, 7, 1)) == 120.0
    assert fact_as_of(statement, "revenue", date(2023, 10, 1)) == 130.0
```

Also test an amendment without `before` leaves the then-known value unchanged, malformed dates raise `ValueError`, and missing facts return `None`.

- [ ] **Step 4: Implement and verify `fact_as_of`**

Start with the current fact. For amendment sources strictly later than `as_of`, process filing dates newest-to-oldest and replace the working value with each numeric `before`. Return `None` before the statement's top-level original `dateFiled`.

Run: `uv run pytest tests/unit/examples/stockfit/test_transforms.py -k fact_as_of -v`

Expected: all fact-history tests pass.

- [ ] **Step 5: Write failing signal-alignment tests**

Use two consecutive annual statements for each synthetic security. Assert:

```python
signals = signal_frames(statements, [date(2023, 1, 31), date(2023, 2, 1)])
assert signals["fundamental_eligible"]["AAA"].to_list() == [0, 1]
assert signals["fundamental_score"]["AAA"].to_list()[0] == 0.0
assert signals["fundamental_score"]["AAA"].to_list()[1] == pytest.approx(
    (120.0 / 100.0 - 1.0) + (24.0 / 120.0)
)
```

Add tests for exact-day availability, future filing exclusion, zero prior revenue, missing revenue, missing operating income, latest eligible fiscal period, and amendment-aware scores.

- [ ] **Step 6: Implement `signal_frames` and `build_market`**

For every price date and security, select the latest fiscal period whose original `dateFiled` is not later than the price date. Compute revenue growth against the preceding fiscal period and operating margin using `fact_as_of`. Emit `fundamental_eligible=1` only when both calculations are finite and prior revenue is positive; otherwise emit eligibility `0` and score `0.0`.

Construct:

```python
return btl.MarketView(
    prices=prices,
    signals={
        "fundamental_score": signals["fundamental_score"],
        "fundamental_eligible": signals["fundamental_eligible"],
    },
)
```

- [ ] **Step 7: Run and review the transform slice**

Run:

```powershell
uv run pytest tests/unit/examples/stockfit/test_transforms.py -v
uv run ruff check examples/stockfit/transforms.py tests/unit/examples/stockfit/test_transforms.py
uv run ruff format --check examples/stockfit/transforms.py tests/unit/examples/stockfit/test_transforms.py
git diff --check
```

Expected: PASS with no formatting defects. Do not commit.

### Task 4: Fundamental strategy and equal-weight baseline

**Files:**
- Create: `examples/stockfit/strategy.py`
- Test: `tests/unit/examples/stockfit/test_strategy.py`

**Interfaces:**
- Consumes: a time-fenced `MarketView` with `fundamental_score` and `fundamental_eligible` signals.
- Produces: `make_fundamental_strategy(top_n=2) -> btl.Strategy` and `equal_weight_strategy(universe) -> Decision`.

- [ ] **Step 1: Write failing strategy tests**

Create small `MarketView` fixtures and execute returned strategy callables directly. Assert that:

```python
decision = make_fundamental_strategy(top_n=2)(
    universe=("AAA", "BBB", "CCC"), market=market.truncated_to(2)
)
assert decision.target_weights == {"AAA": 0.5, "CCC": 0.5}
assert decision.fill_cash is True
```

Use the actual `TargetWeightsDecision` attribute names from `src/backtest_lib/engine/decision/__init__.py`. Add tests that future scores remain fenced, ineligible high scores are ignored, ties break by symbol for deterministic selection, `top_n < 1` raises, and a no-signal date returns `hold()`.

- [ ] **Step 2: Run the strategy tests and confirm failure**

Run: `uv run pytest tests/unit/examples/stockfit/test_strategy.py -v`

Expected: FAIL because `examples.stockfit.strategy` does not exist.

- [ ] **Step 3: Implement the two transparent strategies**

```python
def make_fundamental_strategy(top_n: int = 2) -> btl.Strategy:
    if top_n < 1:
        raise ValueError("top_n must be at least 1")

    def strategy(universe, market):
        score = market.signals["fundamental_score"].by_period[-1]
        eligible = market.signals["fundamental_eligible"].by_period[-1]
        ranked = sorted(
            (sec for sec in universe if int(eligible[sec]) == 1),
            key=lambda sec: (-float(score[sec]), sec),
        )[:top_n]
        if not ranked:
            return btl.hold()
        weight = 1.0 / len(ranked)
        return btl.target_weights({sec: weight for sec in ranked}, fill_cash=True)

    return strategy


def equal_weight_strategy(universe):
    weight = 1.0 / len(universe)
    return btl.target_weights({sec: weight for sec in universe}, fill_cash=True)
```

- [ ] **Step 4: Add a small backtest test for the pre-signal cash state**

Run a three-security backtest from `btl.cash(1_000_000)` over dates where eligibility starts after the first monthly decision. Assert the fundamental portfolio has zero gross exposure before the first eligible decision and non-zero exposure afterwards. Run the equal-weight comparator over the same market and schedule.

- [ ] **Step 5: Run and review the strategy slice**

Run:

```powershell
uv run pytest tests/unit/examples/stockfit/test_strategy.py -v
uv run ruff check examples/stockfit/strategy.py tests/unit/examples/stockfit/test_strategy.py
uv run ruff format --check examples/stockfit/strategy.py tests/unit/examples/stockfit/test_strategy.py
git diff --check
```

Expected: PASS. Do not commit.

### Task 5: Offline fixtures and end-to-end demo

**Files:**
- Create: `examples/stockfit/demo.py`
- Create: `examples/stockfit/fixtures/AAPL-price.json`
- Create: `examples/stockfit/fixtures/AAPL-income.json`
- Create: corresponding invented files for `MSFT` and `COST`
- Create: `tests/e2e/test_stockfit_demo.py`
- Create: `artifacts/stockfit/.gitkeep`

**Interfaces:**
- Consumes: invented fixture payloads by default or explicit live responses with `--live`.
- Produces: `run_demo(price_payloads, statements, output_dir, *, mode) -> dict[str, object]`, `run_offline_demo(output_dir) -> dict[str, object]`, `summary.json`, and `nav-comparison.svg` containing derived results only.

- [ ] **Step 1: Create visibly synthetic fixture payloads**

Each JSON file must include `"fixtureNotice": "Invented synthetic data; not a StockFit response"`. Use 30 or more monthly-equivalent daily dates across at least three year boundaries, two or more annual filings per company, distinct filing dates, one missing fact and one amendment trail. Values must be simple invented round numbers and must not be copied from live output.

- [ ] **Step 2: Write the failing end-to-end test**

```python
def test_offline_demo_writes_deterministic_derived_outputs(tmp_path) -> None:
    summary = run_offline_demo(output_dir=tmp_path)
    assert summary["mode"] == "synthetic"
    assert summary["cohort"] == ["AAPL", "MSFT", "COST"]
    assert set(summary["strategies"]) == {"fundamental", "equal_weight"}
    assert (tmp_path / "summary.json").exists()
    assert (tmp_path / "nav-comparison.svg").exists()
    assert "fixtureNotice" not in (tmp_path / "summary.json").read_text()
```

Run: `uv run pytest tests/e2e/test_stockfit_demo.py -v`

Expected: FAIL because the demo module does not exist.

- [ ] **Step 3: Implement orchestration and matched backtests**

Use `build_market`, `btl.cash(1_000_000)`, `decision_schedule="monthly"`, `make_fundamental_strategy(top_n=2)`, and `equal_weight_strategy`. Extract only these metrics for each result: starting/ending date, observations, total return, annualised return, annualised volatility, Sharpe ratio, maximum drawdown, average turnover and ending NAV.

Reject live execution unless `--live` was explicitly supplied and `STOCKFIT_TOKEN` is non-empty. Live mode must request the same cohort and a bounded range of `2021-01-01` through the execution date, accepting provider-side history clamping but reporting the actual shared coverage.

- [ ] **Step 4: Implement derived outputs without raw data**

Write `summary.json` with provenance mode, execution timestamp, actual coverage, cohort, fixed methodology and metrics. Build `nav-comparison.svg` from the two NAV series with a subtitle that says either `Synthetic demonstration data` or `Derived from StockFit data; raw data not redistributed`. Never serialize price frames, financial statements, filing source maps or response bodies into `artifacts/stockfit`.

- [ ] **Step 5: Verify the default command is offline and deterministic**

Run twice:

```powershell
uv run python -m examples.stockfit.demo --output-dir artifacts/stockfit
Get-FileHash artifacts/stockfit/summary.json, artifacts/stockfit/nav-comparison.svg
```

Normalise the generated timestamp when comparing deterministic content, or inject a fixed clock in the end-to-end test. Expected: identical metrics/chart data and no network or token requirement.

- [ ] **Step 6: Run the explicit live demonstration once**

```powershell
$secretPath = "$env:USERPROFILE\.codex\secrets\stockfit-token.txt"
$env:STOCKFIT_TOKEN = [System.IO.File]::ReadAllText($secretPath).Trim()
uv run python -m examples.stockfit.demo --live --output-dir artifacts/stockfit-live
Remove-Item Env:STOCKFIT_TOKEN
```

Expected: all three symbols share a non-empty date intersection, at least two usable annual filings exist per eligible company, and only derived files are written. If live coverage is insufficient, preserve the truthful limitation and keep the offline demonstration as the reproducible showcase; do not fabricate coverage.

- [ ] **Step 7: Run and review the end-to-end slice**

Run:

```powershell
uv run pytest tests/e2e/test_stockfit_demo.py -v
uv run ruff check examples/stockfit tests/e2e/test_stockfit_demo.py
uv run ruff format --check examples/stockfit tests/e2e/test_stockfit_demo.py
rg -n --hidden --glob '!.git/**' --glob '!*.lock' 'Bearer |STOCKFIT_TOKEN=' .
git diff --check
```

Expected: PASS; secret scan contains code/documentation references only. Do not commit.

### Task 6: Documentation, case study and retrospective learning guide

**Files:**
- Create: `docs/stockfit/README.md`
- Create: `docs/stockfit/methodology.md`
- Create: `docs/stockfit/case-study-draft.md`
- Create: `docs/stockfit/learning-guide.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: verified commands, live contract record and derived live/offline results.
- Produces: accurate setup/use documentation, an unpublished portfolio narrative, and a practical tracing curriculum.

- [ ] **Step 1: Write setup and command documentation**

Document Python/uv setup, offline demo, opt-in smoke/live commands, external token loading, output locations and the rule against committing raw data. Add a short `README.md` section linking to `docs/stockfit/README.md`, clearly labelling the integration as an example.

- [ ] **Step 2: Document the methodology precisely**

Define:

```text
revenue_growth = current_revenue / previous_revenue - 1
operating_margin = current_operating_income / current_revenue
fundamental_score = revenue_growth + operating_margin
```

Explain original filing-date gating, reverse-order amendment rollback, monthly decisions, top-two equal weighting, cash before eligibility and the equal-weight comparator. Include survivorship bias, fixed current-ticker cohort, history clamping, adjustment-snapshot risk, missing facts, reporting lag, no trading costs/liquidity, and no parameter search.

- [ ] **Step 3: Draft the case study from verified evidence only**

Use sections: problem, why point-in-time matters, architecture, test strategy, demonstration result, limitations, and what was learned. Insert only metrics from the derived live summary; if live execution was limited, say so plainly and present synthetic results only as an engineering demonstration. Mark the file `Draft — not published` and make no alpha or production-readiness claim.

- [ ] **Step 4: Write the retrospective learning guide**

Provide a 90-minute path through `client.py`, `transforms.py`, `strategy.py`, and `demo.py`. Name exact breakpoints: request construction, `_get` response validation, `fact_as_of`, signal selection, strategy ranking and `Backtest.run`. Include six exercises: alter a synthetic filing date, inspect amendment rollback, introduce a missing fact, change `top_n`, compare cash/equal-weight starts, and write one new failure test.

- [ ] **Step 5: Verify documentation references and commands**

Run:

```powershell
rg -n "T[B]D|T[O]DO|paste.*token|guarantee|proven alpha" docs/stockfit README.md
uv run python -m examples.stockfit.smoke --help
uv run python -m examples.stockfit.demo --help
uv run python -m examples.stockfit.demo --output-dir artifacts/stockfit
git diff --check
```

Expected: no placeholders or unsafe token guidance; both help commands and offline demo succeed. Do not publish.

### Task 7: Whole-branch verification and security review

**Files:**
- Modify only files requiring fixes found by verification.

**Interfaces:**
- Consumes: all prior tasks.
- Produces: a verified uncommitted working tree and a concise results/limitations hand-off.

- [ ] **Step 1: Run the focused suite**

Run:

```powershell
uv run pytest tests/unit/examples/stockfit tests/e2e/test_stockfit_demo.py -v
```

Expected: all StockFit tests pass with the network disabled and no environment token.

- [ ] **Step 2: Run repository-wide checks**

Run:

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pyrefly check
```

Expected: pytest, Ruff and Pyrefly pass. Run the existing doctest command recorded during repository onboarding and report the known Windows `Cash.rst`/`cash.rst` case-collision separately if it remains the only failure.

- [ ] **Step 3: Inspect generated assets**

Open `artifacts/stockfit/nav-comparison.svg` and the live counterpart if one was produced. Verify legibility, correct labelling, matching date coverage and no raw-data tooltip/export embedded in the SVG.

- [ ] **Step 4: Perform final secret and raw-data scans**

Run:

```powershell
git status --short
git diff --check
git diff --stat
rg -n --hidden --glob '!.git/**' --glob '!*.lock' 'Authorization: Bearer [A-Za-z0-9_-]+|STOCKFIT_TOKEN=.+|fl_[A-Za-z0-9_-]+' .
rg -n --hidden --glob '!.git/**' '"sources"\s*:\s*\{' artifacts docs README.md
```

Expected: no credential values and no raw source maps outside invented fixture files. If a potential secret is found, stop, remove it, rotate the affected credential, and repeat the scan.

- [ ] **Step 5: Review the entire diff without committing**

Run: `git diff -- . ':(exclude)uv.lock'`

Confirm that every changed file belongs to this approved feature, public fixtures say they are invented, live outputs are derived, and no core API or licence file changed. Leave the branch uncommitted for Hakim's review.
