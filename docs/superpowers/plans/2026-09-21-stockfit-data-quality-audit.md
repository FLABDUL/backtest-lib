# StockFit Data-Quality Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an offline-first, privacy-safe audit of StockFit company, price and annual income-statement quality across a frozen twelve-company cohort.

**Architecture:** Pure functions in `examples/stockfit/audit.py` turn decoded provider-shaped values into typed audit records with stable status and reason codes. `examples/stockfit/audit_cli.py` owns sequential client calls, safe failure classification, deterministic JSON/CSV/SVG rendering and recoverable directory publication; normal tests never use the network.

**Tech Stack:** Python 3.14, standard-library JSON/CSV/path/date utilities, Altair, pytest, Ruff and Pyrefly.

**Spec:** `docs/superpowers/specs/2026-09-21-stockfit-data-quality-audit-design.md`

## Global Constraints

- Keep all provider-specific code under `examples/stockfit`; do not change the public `backtest-lib` API.
- Freeze the live cohort as `AAPL, MSFT, JPM, BAC, XOM, CVX, JNJ, PFE, WMT, COST, CAT, UNP` in that order.
- Request adjusted daily prices from `2021-01-01` through the UTC execution date and ten annual income-statement periods with split adjustment enabled.
- A complete live run makes exactly 36 sequential requests and performs no automatic retries.
- Default execution is offline, deterministic and independent of both the network and `STOCKFIT_TOKEN`.
- Persist no prices, financial values, accession numbers, source-map keys, response bodies or credentials.
- Add no runtime dependency; reuse the existing standard-library client and Altair.
- Use fixed status thresholds and stable reason ordering; do not calculate a composite quality score.
- Do not alter the strategy, run a larger-cohort backtest or make an investment-performance claim.
- Do not push, publish or deploy without separate user approval.

## Review Focus

- A response symbol that does not match the requested company must fail rather than be attributed to the wrong row; Task 1 tests price and company-symbol mismatches.
- Provider text beginning with spreadsheet formula characters must be neutralised in CSV and omitted from the cohort-only JSON summary; Task 5 tests `=`, `+`, `-` and `@` prefixes.
- Partly malformed nested statement facts, sources or derived metadata must produce stable fail reasons without leaking the offending value; Task 2 tests each nested shape.
- Re-running into an existing output directory, including a simulated publication failure, must leave either the complete old set or complete new set and must reject unrelated files; Task 5 tests rollback and directory guards.
- Exact temporal boundaries must be deterministic: 31-day late starts, seven-day stale ends, seven-day price gaps and 90-day filing lags pass, while one day beyond each threshold reviews; Tasks 1 and 2 test both sides.

---

## File Structure

- `examples/stockfit/audit.py`: immutable audit records, status/reason ordering, pure company, price, statement and cohort calculations.
- `examples/stockfit/audit_cli.py`: offline/live collection, safe errors, rate metadata, deterministic serialisation, charting, recoverable artifact publication and CLI entry point.
- `examples/stockfit/client.py`: expose the existing allow-listed rate-header filter for reuse.
- `examples/stockfit/smoke.py`: consume the shared rate-header helper without behaviour changes.
- `examples/stockfit/fixtures/{AAPL,MSFT,COST}-company.json`: invented company metadata for the offline audit.
- `tests/unit/examples/stockfit/test_audit.py`: pure rule and aggregation coverage.
- `tests/unit/examples/stockfit/test_audit_cli.py`: orchestration, safety, serialisation and publication coverage.
- `tests/e2e/test_stockfit_audit.py`: deterministic offline command and artifact contract.
- `artifacts/stockfit-audit/`: tracked synthetic summary, CSV and heatmap.
- `artifacts/stockfit-audit-live/`: tracked derived live summary, CSV and heatmap after verification.
- `docs/stockfit/data-quality-audit.md`: sanitised method, results and limitations.
- `docs/stockfit/README.md`: command and document links.

### Task 1: Company and price audit primitives

**Files:**
- Create: `examples/stockfit/audit.py`
- Create: `tests/unit/examples/stockfit/test_audit.py`

**Interfaces:**
- Consumes: decoded provider-shaped objects, requested symbol, `request_start: date`, `execution_date: date`.
- Produces: `AuditStatus`, `MetadataAudit`, `PriceAudit`, `worst_status(...)`, `audit_company_metadata(...)`, and `audit_prices(...)` for later tasks.

- [ ] **Step 1: Write failing status, metadata and price tests**

Create records through public functions, not internal helpers:

```python
from datetime import UTC, date, datetime, time

import pytest

from examples.stockfit.audit import (
    audit_company_metadata,
    audit_prices,
    worst_status,
)


def price_payload(symbol: str, dates: list[date]) -> dict[str, object]:
    timestamps = [
        int(datetime.combine(day, time(), tzinfo=UTC).timestamp() * 1000)
        for day in dates
    ]
    return {"symbol": symbol, "data": [[stamp, 100.0] for stamp in timestamps]}


def test_worst_status_uses_fail_review_pass_severity() -> None:
    assert worst_status("pass", "review") == "review"
    assert worst_status("review", "fail", "pass") == "fail"


def test_company_metadata_rejects_wrong_symbol() -> None:
    result = audit_company_metadata(
        "AAPL",
        {"symbols": ["MSFT"], "name": "Wrong", "sector": "Technology"},
    )
    assert result.status == "fail"
    assert result.reasons == ("company_symbol_mismatch",)


def test_company_metadata_reviews_missing_industry() -> None:
    result = audit_company_metadata(
        "AAPL",
        {"symbols": ["AAPL"], "name": "Example", "sector": "Technology"},
    )
    assert result.status == "review"
    assert result.reasons == ("company_industry_missing",)


@pytest.mark.parametrize(
    ("returned_start", "expected"),
    [(date(2021, 2, 1), "pass"), (date(2021, 2, 2), "review")],
)
def test_price_start_boundary(returned_start: date, expected: str) -> None:
    result = audit_prices(
        "AAA",
        price_payload("AAA", [returned_start, date(2021, 2, 8)]),
        request_start=date(2021, 1, 1),
        execution_date=date(2021, 2, 8),
    )
    assert result.status == expected


def test_price_symbol_mismatch_fails() -> None:
    result = audit_prices(
        "AAA",
        price_payload("BBB", [date(2021, 1, 4)]),
        request_start=date(2021, 1, 1),
        execution_date=date(2021, 1, 4),
    )
    assert result.status == "fail"
    assert "price_symbol_mismatch" in result.reasons
```

Add explicit tests for an empty series; malformed two-item observations;
non-numeric, non-finite and non-positive prices; duplicate timestamps; raw
out-of-order input; a stale end at seven and eight days; and a largest gap at
seven and eight days. Assert dates/counts and the complete reason tuple so
reason ordering is pinned.

- [ ] **Step 2: Run the tests and verify RED**

Run:

```powershell
uv run pytest tests/unit/examples/stockfit/test_audit.py -v
```

Expected: collection fails because `examples.stockfit.audit` does not exist.

- [ ] **Step 3: Implement immutable records and pure checks**

Define these public types and constants:

```python
from dataclasses import dataclass
from datetime import date
from typing import Literal

type AuditStatus = Literal["pass", "review", "fail"]

STATUS_SEVERITY = {"pass": 0, "review": 1, "fail": 2}
REASON_ORDER = (
    "company_response_invalid",
    "company_symbol_mismatch",
    "company_sector_missing",
    "company_industry_missing",
    "price_response_invalid",
    "price_symbol_mismatch",
    "price_empty",
    "price_observation_invalid",
    "price_duplicate_timestamp",
    "price_start_late",
    "price_end_stale",
    "price_gap_large",
)


@dataclass(frozen=True, slots=True)
class MetadataAudit:
    symbol: str
    company_name: str | None
    sector: str | None
    industry: str | None
    stable_identifiers_present: bool
    status: AuditStatus
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PriceAudit:
    start: date | None
    end: date | None
    observations: int
    duplicate_count: int
    invalid_count: int
    out_of_order_count: int
    largest_gap_days: int | None
    status: AuditStatus
    reasons: tuple[str, ...]
```

`audit_prices` must examine the original sequence before sorting. Convert valid
millisecond timestamps to UTC dates, count adjacent raw timestamp reversals,
and use only valid unique observations for coverage/gap calculations. Any
invalid observation or duplicate timestamp is a fail. Out-of-order valid
observations are informational only. Use `timedelta(days=31)`,
`timedelta(days=7)` and an integer gap comparison so exact boundaries match the
spec.

Company symbol validation accepts only a list of strings containing the
normalised requested symbol. Stable identifiers are present when at least one
of `cik`, `cusip` or `figi` has a non-empty value; retain only the Boolean.

- [ ] **Step 4: Run focused checks and verify GREEN**

Run:

```powershell
uv run pytest tests/unit/examples/stockfit/test_audit.py -v
uv run ruff check examples/stockfit/audit.py tests/unit/examples/stockfit/test_audit.py
uv run pyrefly check
```

Expected: all new tests pass, Ruff passes and Pyrefly reports zero errors.

- [ ] **Step 5: Commit the independently usable price audit**

```powershell
git add examples/stockfit/audit.py tests/unit/examples/stockfit/test_audit.py
git commit -m "feat: add StockFit price quality audit"
```

### Task 2: Annual-statement and provenance audit

**Files:**
- Modify: `examples/stockfit/audit.py`
- Modify: `tests/unit/examples/stockfit/test_audit.py`

**Interfaces:**
- Consumes: `statements: object` containing annual StockFit-shaped rows.
- Produces: `StatementAudit`, `CompanyAudit`, `audit_statements(...)`, and `combine_company_audit(...)`.

- [ ] **Step 1: Write failing statement-boundary and nested-shape tests**

Add this helper so every row has explicit `period`, `fiscalYear`, `dateFiled`,
`facts`, `sources` and `derived`, then add the concrete cases below:

```python
def statement(
    period: str,
    filed: str,
    *,
    fiscal_year: int | None = None,
    revenue: object = 100.0,
    operating_income: object = 10.0,
    sources: object | None = None,
    derived: object | None = None,
) -> dict[str, object]:
    return {
        "period": period,
        "fiscalYear": int(period[:4]) if fiscal_year is None else fiscal_year,
        "fiscalPeriod": "FY",
        "dateFiled": filed,
        "facts": {"revenue": revenue, "operatingIncome": operating_income},
        "sources": {} if sources is None else sources,
        "derived": [] if derived is None else derived,
    }


def test_filing_lag_boundary() -> None:
    passing = audit_statements([statement("2024-12-31", "2025-03-31")])
    reviewing = audit_statements([statement("2024-12-31", "2025-04-01")])
    assert passing.max_filing_lag_days == 90
    assert "filing_lag_large" not in passing.reasons
    assert reviewing.max_filing_lag_days == 91
    assert "filing_lag_large" in reviewing.reasons


def test_negative_filing_lag_fails() -> None:
    result = audit_statements([statement("2024-12-31", "2024-12-30")])
    assert result.status == "fail"
    assert "filing_before_period_end" in result.reasons


def test_numeric_before_is_counted_when_amendment_is_false() -> None:
    row = statement(
        "2024-12-31",
        "2025-02-01",
        sources={
            "private-source-id": {
                "dateFiled": "2026-02-01",
                "amendment": False,
                "facts": {"revenue": {"before": 100.0}},
            }
        },
    )
    result = audit_statements([row])
    assert result.source_entry_count == 1
    assert result.before_fact_count == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [("facts", []), ("sources", []), ("derived", {})],
)
def test_malformed_nested_statement_shape_fails(field, value) -> None:
    row = statement("2024-12-31", "2025-02-01")
    row[field] = value
    result = audit_statements([row])
    assert result.status == "fail"
    assert result.reasons == ("statement_row_invalid",)
```

Also test malformed dates, duplicate `fiscalYear`, missing years, fewer than
five rows, absent/invalid `fiscalYear` fallback, zero/negative revenue,
required-fact completeness, a usable consecutive pair, no usable consecutive
pair, median filing lag for odd/even counts, non-numeric `before`, and derived
fact incidence.

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```powershell
uv run pytest tests/unit/examples/stockfit/test_audit.py -v
```

Expected: failures report missing `audit_statements`, `StatementAudit` and
`CompanyAudit`.

- [ ] **Step 3: Implement statement calculations and combined records**

Add:

```python
@dataclass(frozen=True, slots=True)
class StatementAudit:
    statement_count: int
    fiscal_year_start: int | None
    fiscal_year_end: int | None
    missing_fiscal_year_count: int
    duplicate_fiscal_year_count: int
    revenue_completeness_pct: float
    operating_income_completeness_pct: float
    median_filing_lag_days: float | None
    max_filing_lag_days: int | None
    source_entry_count: int
    before_fact_count: int
    derived_fact_count: int
    fact_count: int
    derived_fact_incidence_pct: float
    status: AuditStatus
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CompanyAudit:
    symbol: str
    metadata: MetadataAudit
    prices: PriceAudit
    statements: StatementAudit
    status: AuditStatus
    reasons: tuple[str, ...]
```

Completeness denominator is the number of structurally valid annual rows.
Count a fact as complete only when it is a finite non-Boolean number. A usable
pair requires consecutive fiscal years, positive prior/current revenue and
finite current operating income. Count each source object once and each numeric
`facts[*].before` once; never retain its key or value.

Use `statistics.median` for filing lag. Prefer a valid integer `fiscalYear`;
otherwise use the validated period year and add `fiscal_year_fallback`. Sort
reasons through the single module-level order rather than set iteration.
Treat an absent `derived` field as an empty list for compatibility with the
existing labelled fixtures; if present, `derived` must be a list of strings.

- [ ] **Step 4: Run focused and regression checks**

```powershell
uv run pytest tests/unit/examples/stockfit/test_audit.py tests/unit/examples/stockfit/test_transforms.py -v
uv run ruff check examples/stockfit/audit.py tests/unit/examples/stockfit/test_audit.py
uv run pyrefly check
```

Expected: all tests pass with no lint or type errors.

- [ ] **Step 5: Commit the statement audit**

```powershell
git add examples/stockfit/audit.py tests/unit/examples/stockfit/test_audit.py
git commit -m "feat: audit StockFit filing quality"
```

### Task 3: Cohort aggregation and safe summary model

**Files:**
- Modify: `examples/stockfit/audit.py`
- Modify: `tests/unit/examples/stockfit/test_audit.py`

**Interfaces:**
- Consumes: ordered `Sequence[CompanyAudit]`, requested cohort, request bounds, failures and allow-listed rate metadata.
- Produces: `AuditRun`, `summarise_cohort(...)`, fixed `CHECK_ORDER`, and heatmap cell derivation.

- [ ] **Step 1: Write failing cohort and summary tests**

Add tests that prove:

```python
def test_cohort_summary_uses_actual_shared_price_intersection() -> None:
    run = summarise_cohort(
        cohort=("AAA", "BBB"),
        records=(
            company_audit("AAA", "2021-01-04", "2025-01-03"),
            company_audit("BBB", "2021-02-01", "2024-12-31"),
        ),
        failures={},
        request_start=date(2021, 1, 1),
        execution_time=FIXED_NOW,
        mode="live",
        rate_limit={},
    )
    assert run.shared_price_start == date(2021, 2, 1)
    assert run.shared_price_end == date(2024, 12, 31)
    assert run.status_counts == {"pass": 2, "review": 0, "fail": 0}


def test_failure_makes_run_incomplete_without_fake_company_row() -> None:
    run = summarise_cohort(
        cohort=("AAA", "BBB"),
        records=(company_audit("AAA"),),
        failures={"BBB": "timeout"},
        request_start=date(2021, 1, 1),
        execution_time=FIXED_NOW,
        mode="live",
        rate_limit={},
    )
    assert run.run_status == "incomplete"
    assert run.missing_symbols == ("BBB",)
    assert "timeout" in run.failure_categories
```

Test empty shared coverage, stable cohort ordering, status distributions,
min/median/max completeness and lag summaries, summed provenance counts,
unknown failure-category rejection, and heatmap cells for every value in
`CHECK_ORDER`.

- [ ] **Step 2: Run the tests and verify RED**

```powershell
uv run pytest tests/unit/examples/stockfit/test_audit.py -v
```

Expected: failures report missing `AuditRun` and `summarise_cohort`.

- [ ] **Step 3: Implement aggregation without raw-row duplication**

Define:

```python
type AuditMode = Literal["synthetic", "live"]

CHECK_ORDER = (
    "company_metadata",
    "price_coverage",
    "price_integrity",
    "statement_coverage",
    "fact_completeness",
    "filing_lag",
    "provenance",
)


@dataclass(frozen=True, slots=True)
class AuditRun:
    mode: AuditMode
    generated_at: datetime
    cohort: tuple[str, ...]
    request_start: date
    request_end: date
    records: tuple[CompanyAudit, ...]
    run_status: Literal["complete", "incomplete"]
    missing_symbols: tuple[str, ...]
    failure_categories: tuple[str, ...]
    shared_price_start: date | None
    shared_price_end: date | None
    status_counts: dict[AuditStatus, int]
    rate_limit: dict[str, str]
```

Expose pure methods or functions that return a cohort-summary mapping and
derived heatmap rows. The JSON summary mapping must include thresholds and
reason definitions but never serialise `records`; CSV and heatmap functions in
Task 5 consume `AuditRun.records` directly.

- [ ] **Step 4: Run focused quality checks**

```powershell
uv run pytest tests/unit/examples/stockfit/test_audit.py -v
uv run ruff check examples/stockfit/audit.py tests/unit/examples/stockfit/test_audit.py
uv run pyrefly check
```

- [ ] **Step 5: Commit cohort aggregation**

```powershell
git add examples/stockfit/audit.py tests/unit/examples/stockfit/test_audit.py
git commit -m "feat: summarise StockFit cohort quality"
```

### Task 4: Sequential live collection and safe failures

**Files:**
- Modify: `examples/stockfit/client.py`
- Modify: `examples/stockfit/smoke.py`
- Create: `examples/stockfit/audit_cli.py`
- Modify: `tests/unit/examples/stockfit/test_client.py`
- Modify: `tests/unit/examples/stockfit/test_smoke.py`
- Create: `tests/unit/examples/stockfit/test_audit_cli.py`

**Interfaces:**
- Consumes: `StockFitClient`, frozen cohort, dates and pure audit functions.
- Produces: `safe_rate_headers(...)`, `AuditRunAborted`, and `collect_live_audit(client, *, execution_time) -> AuditRun`.

- [ ] **Step 1: Write failing shared-header and collection tests**

Move the existing smoke allow-list expectations into client tests, then add a
fake audit client that records calls and rotates response headers. Pin:

```python
def test_successful_live_collection_makes_three_ordered_calls_per_symbol() -> None:
    client = FakeAuditClient()
    run = collect_live_audit(client, execution_time=FIXED_NOW)
    assert client.calls == [
        call
        for symbol in LIVE_COHORT
        for call in (
            ("company_details", symbol),
            ("price_history", symbol, date(2021, 1, 1), FIXED_NOW.date(), "1d", True),
            ("income_statement", symbol, "annual", 10, True),
        )
    ]
    assert len(client.calls) == 36
    assert run.run_status == "complete"


@pytest.mark.parametrize("status", [401, 403, 429])
def test_global_http_failures_abort_without_continuing(status: int) -> None:
    client = FakeAuditClient(fail_at=2, status=status, message="private body")
    with pytest.raises(AuditRunAborted) as exc_info:
        collect_live_audit(client, execution_time=FIXED_NOW)
    assert len(client.calls) == 2
    assert "private body" not in str(exc_info.value)


def test_timeout_marks_symbol_missing_and_continues() -> None:
    client = FakeAuditClient(timeout_symbol="JPM")
    run = collect_live_audit(client, execution_time=FIXED_NOW)
    assert run.run_status == "incomplete"
    assert run.missing_symbols == ("JPM",)
    assert "timeout" in run.failure_categories
    assert client.calls[-1][1] == "UNP"
```

Also test HTTP 400/500 categorisation, malformed decoded payloads becoming
fail rows, token/body exclusion from complete formatted tracebacks, rate-header
allow-listing and the frozen cohort constant.

- [ ] **Step 2: Run tests and verify RED**

```powershell
uv run pytest tests/unit/examples/stockfit/test_client.py tests/unit/examples/stockfit/test_smoke.py tests/unit/examples/stockfit/test_audit_cli.py -v
```

Expected: missing shared helper and audit CLI symbols fail.

- [ ] **Step 3: Share rate-header filtering and implement collection**

Move `_safe_rate_headers` from `smoke.py` to this public helper in `client.py`:

```python
def safe_rate_headers(headers: Mapping[str, str]) -> dict[str, str]:
    allowed = ("ratelimit", "rate-limit", "retry-after")
    return {
        name.lower(): str(value)
        for name, value in headers.items()
        if any(marker in name.lower() for marker in allowed)
    }
```

Update `smoke.py` to import it. In `audit_cli.py`, define the fixed constants,
an `AuditClient` protocol matching the three methods, and a sanitised
`AuditRunAborted` that carries only `category`, `status` and safe rate headers.

`collect_live_audit` calls each endpoint inside a per-symbol block, captures
safe headers immediately after each response, and never incorporates
`str(StockFitError)` in a saved or displayed value. Map status `401`, `403` and
`429` to global aborts; map status `None` to `timeout_or_connection`; map other
non-success statuses to `http_<status>`. A `StockFitError` carrying a successful
HTTP status means response-shape validation failed: supply an invalid sentinel
to the owning pure audit function, continue the other calls for that symbol and
produce the appropriate fail row rather than labelling the run incomplete.
Continue after per-company transport failures.

- [ ] **Step 4: Run focused and existing client/smoke tests**

```powershell
uv run pytest tests/unit/examples/stockfit/test_client.py tests/unit/examples/stockfit/test_smoke.py tests/unit/examples/stockfit/test_audit_cli.py -v
uv run ruff check examples/stockfit/client.py examples/stockfit/smoke.py examples/stockfit/audit_cli.py tests/unit/examples/stockfit
uv run pyrefly check
```

- [ ] **Step 5: Commit safe request orchestration**

```powershell
git add examples/stockfit/client.py examples/stockfit/smoke.py examples/stockfit/audit_cli.py tests/unit/examples/stockfit
git commit -m "feat: orchestrate StockFit audit requests"
```

### Task 5: Deterministic artifacts and recoverable publication

**Files:**
- Modify: `examples/stockfit/audit_cli.py`
- Modify: `tests/unit/examples/stockfit/test_audit_cli.py`

**Interfaces:**
- Consumes: `AuditRun` and an output `Path`.
- Produces: `summary_mapping(...)`, `company_csv(...)`, `quality_chart(...)`, and `publish_artifacts(run, destination)`.

- [ ] **Step 1: Write failing serialisation and publication tests**

Pin exact filenames and deterministic bytes for a fixed `AuditRun`:

```python
def test_publish_writes_only_the_three_derived_artifacts(tmp_path) -> None:
    destination = tmp_path / "audit"
    publish_artifacts(example_run(), destination)
    assert sorted(path.name for path in destination.iterdir()) == [
        "company-quality.csv",
        "data-quality.svg",
        "summary.json",
    ]


def test_csv_neutralises_formula_prefix_and_summary_stays_cohort_only(tmp_path) -> None:
    run = example_run(company_name='=HYPERLINK("bad")')
    publish_artifacts(run, tmp_path / "audit")
    csv_text = (tmp_path / "audit" / "company-quality.csv").read_text()
    summary = json.loads((tmp_path / "audit" / "summary.json").read_text())
    assert "'=HYPERLINK" in csv_text
    assert summary["cohort"] == ["AAA"]
```

Parameterise the formula test for `=`, `+`, `-` and `@`. Assert the JSON omits
company rows and forbidden keys (`prices`, `facts`, `sources`, `accession`).
Assert the CSV contains declared columns in frozen cohort order and formatted
percentages, empty optional values and stable semicolon-separated reasons.
Assert the SVG includes title, provenance subtitle, row/check labels and status
text but no `tooltip`, `vega-embed` or `download` marker.

Add three publication-safety tests:

1. A second successful run replaces all three bytes.
2. An unrelated file in the destination causes refusal without changes.
3. Monkeypatch the staging-directory rename to fail after the old directory is
   moved; assert rollback restores the complete old set and leaves no staging
   or backup directory.

- [ ] **Step 2: Run tests and verify RED**

```powershell
uv run pytest tests/unit/examples/stockfit/test_audit_cli.py -v
```

Expected: missing serialisation/publication functions fail.

- [ ] **Step 3: Implement deterministic renderers**

Use `json.dumps(payload, indent=2, sort_keys=True) + "\n"`. Use `csv.DictWriter`
with `lineterminator="\n"` and an explicit field list. Neutralise untrusted text
for CSV only:

```python
def _csv_safe(value: str | None) -> str:
    if value is None:
        return ""
    return f"'{value}" if value.startswith(("=", "+", "-", "@")) else value
```

Build a fixed-order Altair heatmap from derived `(symbol, check, status)` rows.
Use colour plus a one-character status label (`P`, `R`, `F`), disable tooltips,
and save directly as SVG inside the staging directory.

- [ ] **Step 4: Implement recoverable directory publication**

Before publishing, resolve the destination and require every existing file to
be one of the three expected filenames. Render all outputs into a unique
temporary directory under `destination.parent`. If a destination exists,
rename it to a unique backup; rename staging to destination; restore the backup
on any failure; remove the backup only after success. Validate that staging and
backup resolve inside `destination.parent` before cleanup.

Do not use recursive deletion on a path that has not passed that containment
check. Tests use temporary directories and injected rename failure rather than
touching real artifacts.

- [ ] **Step 5: Run focused checks and commit**

```powershell
uv run pytest tests/unit/examples/stockfit/test_audit_cli.py -v
uv run ruff check examples/stockfit/audit_cli.py tests/unit/examples/stockfit/test_audit_cli.py
uv run pyrefly check
git add examples/stockfit/audit_cli.py tests/unit/examples/stockfit/test_audit_cli.py
git commit -m "feat: render StockFit audit artifacts"
```

### Task 6: Offline-first CLI and synthetic demonstration

**Files:**
- Modify: `examples/stockfit/audit_cli.py`
- Create: `examples/stockfit/fixtures/AAPL-company.json`
- Create: `examples/stockfit/fixtures/MSFT-company.json`
- Create: `examples/stockfit/fixtures/COST-company.json`
- Modify: `tests/unit/examples/stockfit/test_audit_cli.py`
- Create: `tests/e2e/test_stockfit_audit.py`
- Create: `artifacts/stockfit-audit/.gitkeep`

**Interfaces:**
- Consumes: existing labelled price/income fixtures, new labelled company fixtures and optional live client.
- Produces: `run_offline_audit(destination: Path, *, now: datetime) -> AuditRun`, `run_live_audit(token: str, destination: Path, *, now: datetime, client_factory: Callable[[str], AuditClient]) -> AuditRun`, `parse_args(argv)`, `main(argv, *, now, client_factory)`, offline `summary.json`, `company-quality.csv`, and `data-quality.svg`.

- [ ] **Step 1: Create invented company fixtures**

Each file must use the existing wrapper and round invented metadata:

```json
{
  "fixtureNotice": "Invented synthetic data; not a StockFit response",
  "payload": {
    "symbols": ["AAPL"],
    "name": "Synthetic AAPL Example",
    "sector": "Synthetic Technology",
    "industry": "Synthetic Devices",
    "cik": "synthetic-present"
  }
}
```

Use matching synthetic values for MSFT and COST. Do not copy live company
metadata beyond the public ticker strings.

- [ ] **Step 2: Write failing offline and CLI end-to-end tests**

```python
def test_default_cli_is_offline_and_deterministic(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("STOCKFIT_TOKEN", raising=False)
    now = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
    first = run_offline_audit(tmp_path, now=now)
    first_bytes = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    second = run_offline_audit(tmp_path, now=now)
    assert first == second
    assert first_bytes == {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    assert first.mode == "synthetic"


def test_live_cli_requires_process_token(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("STOCKFIT_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="Set STOCKFIT_TOKEN"):
        main(["--live", "--output-dir", str(tmp_path)])


def test_incomplete_live_run_writes_labelled_outputs_and_exits_two(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("STOCKFIT_TOKEN", "invented-token")
    result = main(
        ["--live", "--output-dir", str(tmp_path)],
        now=FIXED_NOW,
        client_factory=incomplete_fake_client,
    )
    assert result == 2
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["run_status"] == "incomplete"
```

Also test `--help`, explicit live output defaults, successful live exit zero,
global abort leaving an existing destination unchanged, fixture-notice
validation, stdout containing only the summary mapping, and no environment
lookup on the offline path.

- [ ] **Step 3: Run tests and verify RED**

```powershell
uv run pytest tests/unit/examples/stockfit/test_audit_cli.py tests/e2e/test_stockfit_audit.py -v
```

- [ ] **Step 4: Implement fixture loading and CLI entry points**

Use these command defaults:

```python
OFFLINE_OUTPUT_DIR = Path("artifacts/stockfit-audit")
LIVE_OUTPUT_DIR = Path("artifacts/stockfit-audit-live")
OFFLINE_GENERATED_AT = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit StockFit data quality.")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    now: datetime | None = None,
    client_factory: Callable[[str], AuditClient] = StockFitClient,
) -> int:
    args = parse_args(argv)
    execution_time = now or (datetime.now(UTC) if args.live else OFFLINE_GENERATED_AT)
    destination = args.output_dir or (
        LIVE_OUTPUT_DIR if args.live else OFFLINE_OUTPUT_DIR
    )
    if args.live:
        token = os.environ.get("STOCKFIT_TOKEN", "").strip()
        if not token:
            raise SystemExit("Set STOCKFIT_TOKEN in this process before --live.")
        try:
            run = run_live_audit(
                token,
                destination,
                now=execution_time,
                client_factory=client_factory,
            )
        except AuditRunAborted as exc:
            safe_abort = {
                "category": exc.category,
                "status": exc.status,
                "rate_limit": exc.rate_limit,
            }
            raise SystemExit(json.dumps(safe_abort, sort_keys=True)) from None
    else:
        run = run_offline_audit(destination, now=execution_time)
    print(json.dumps(summary_mapping(run), indent=2, sort_keys=True))
    return 0 if run.run_status == "complete" else 2
```

When `--output-dir` is omitted, select the offline or live directory after
parsing `--live`. `run_offline_audit` validates every wrapper notice and audits
the three existing fixture symbols. `main` prints the safe cohort summary,
returns `0` for complete and `2` for incomplete, and converts
`AuditRunAborted` to a one-line sanitised `SystemExit` without a traceback.
The offline default uses `OFFLINE_GENERATED_AT`, making repeated default CLI
runs byte-for-byte deterministic; a supplied `now` remains available to tests.
`run_live_audit` constructs the injected client, calls `collect_live_audit`,
publishes the returned run and returns it. `run_offline_audit` loads the nine
labelled fixtures, builds three `CompanyAudit` records, summarises, publishes
and returns the synthetic run.

- [ ] **Step 5: Run the offline command twice and verify determinism**

```powershell
uv run python -m examples.stockfit.audit_cli --output-dir artifacts/stockfit-audit
Get-FileHash artifacts/stockfit-audit/summary.json, artifacts/stockfit-audit/company-quality.csv, artifacts/stockfit-audit/data-quality.svg
uv run python -m examples.stockfit.audit_cli --output-dir artifacts/stockfit-audit
Get-FileHash artifacts/stockfit-audit/summary.json, artifacts/stockfit-audit/company-quality.csv, artifacts/stockfit-audit/data-quality.svg
```

Expected: identical hashes on both default offline runs, no token requirement
and exactly three derived files.

- [ ] **Step 6: Run the focused and repository suites**

```powershell
uv run pytest tests/unit/examples/stockfit tests/e2e/test_stockfit_demo.py tests/e2e/test_stockfit_audit.py -v
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pyrefly check
```

- [ ] **Step 7: Commit the offline-first audit**

```powershell
git add examples/stockfit/audit_cli.py examples/stockfit/fixtures tests/unit/examples/stockfit/test_audit_cli.py tests/e2e/test_stockfit_audit.py artifacts/stockfit-audit
git commit -m "feat: add offline-first StockFit audit CLI"
```

### Task 7: Live audit, documentation and whole-branch verification

**Files:**
- Create: `artifacts/stockfit-audit-live/summary.json`
- Create: `artifacts/stockfit-audit-live/company-quality.csv`
- Create: `artifacts/stockfit-audit-live/data-quality.svg`
- Create: `docs/stockfit/data-quality-audit.md`
- Modify: `docs/stockfit/README.md`
- Modify only if verification finds defects: audit implementation/tests.

**Interfaces:**
- Consumes: explicit live CLI, external token file and all derived outputs.
- Produces: a sanitised audit result, portfolio-ready method/results document and a fully reviewed branch.

- [ ] **Step 1: Run the explicit live audit without echoing the token**

```powershell
$secretPath = "$env:USERPROFILE\.codex\secrets\stockfit-token.txt"
$env:STOCKFIT_TOKEN = [IO.File]::ReadAllText($secretPath).Trim()
try {
  uv run python -m examples.stockfit.audit_cli --live --output-dir artifacts/stockfit-audit-live
} finally {
  Remove-Item Env:STOCKFIT_TOKEN -ErrorAction SilentlyContinue
}
```

Expected: exactly twelve cohort rows, `run_status` truthfully reports complete
or incomplete, and the directory contains only the three derived files. Do not
persist console logs containing provider response details.

- [ ] **Step 2: Validate live output contract without printing sensitive data**

Use a small PowerShell check that prints filenames, row count, cohort, run
status and reason-code counts only. Assert:

- CSV symbols equal the frozen cohort in order for a complete run.
- JSON contains no per-company rows or forbidden raw keys.
- No numeric price or financial columns exist in the CSV.
- The SVG subtitle identifies derived StockFit data.
- `summary.json` and CSV agree on status counts.

If the run is incomplete, document the sanitised category and preserve the
truthful result; do not fabricate or substitute symbols.

- [ ] **Step 3: Write the results document from derived evidence only**

Create `docs/stockfit/data-quality-audit.md` with:

1. `Draft — not published`.
2. Frozen cohort and predeclared thresholds.
3. Request count, execution date and actual shared coverage.
4. Pass/review/fail counts and the most common reason codes.
5. Aggregate filing lag, required-fact completeness and provenance counts.
6. Interpretive limitations: current-ticker survivorship, small cohort,
   trial-depth limits, sector accounting differences and provider dependence.
7. A direct statement that no raw values or source identifiers were retained.

Use no alpha, provider-perfection or production-readiness claim. Link this
document and both offline/live commands from `docs/stockfit/README.md`.

- [ ] **Step 4: Inspect the SVGs visually**

Convert SVG to temporary PNG only if the viewer requires it. Verify row/check
labels, title/subtitle, status text, accessible contrast, complete legend and
no clipping. Inspect both synthetic and live charts; do not commit conversion
files.

- [ ] **Step 5: Run final security and content scans**

```powershell
rg -l --hidden --glob '!.git/**' --glob '!*.lock' 'fl_[A-Za-z0-9_-]{8,}' .
rg -l --hidden --glob '!.git/**' 'Authorization:\s*Bearer\s+[A-Za-z0-9_-]{8,}' .
rg -n --hidden '"(prices|facts|sources|accessionNumber)"\s*:' artifacts/stockfit-audit artifacts/stockfit-audit-live docs/stockfit
rg -n --glob '*.svg' 'tooltip|vega-embed|download' artifacts/stockfit-audit artifacts/stockfit-audit-live
git diff --check
```

Expected: zero credential values, raw-provider objects and interactive/export
markers. The literal scan expressions inside plan files may self-match only
when plans are included; classify those separately rather than ignoring a
deliverable match.

- [ ] **Step 6: Run complete verification**

```powershell
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run pyrefly check
just doctest
uv run python -m examples.stockfit.audit_cli --help
uv run python -m examples.stockfit.audit_cli --output-dir artifacts/stockfit-audit
git status --short
git diff --stat
```

Expected: all checks pass, the default audit remains offline, only approved
files changed and no core library or licence file changed.

- [ ] **Step 7: Request whole-branch review and resolve findings**

Give a fresh reviewer the spec, this plan, the complete branch range and live
summary metadata. Require review of rule boundaries, privacy, partial failures,
artifact publication, tests and documentation. Fix every Critical or Important
finding test-first, rerun complete verification and request a focused re-review.

- [ ] **Step 8: Commit the verified live evidence and documentation**

```powershell
git add artifacts/stockfit-audit artifacts/stockfit-audit-live docs/stockfit examples/stockfit tests
git diff --cached --check
git commit -m "docs: record StockFit data-quality audit"
```

Do not push, publish or deploy. Preserve the worktree for user review.
