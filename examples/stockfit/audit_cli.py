"""Bounded orchestration for the StockFit data-quality audit."""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from io import StringIO
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import uuid4

import altair as alt

from examples.stockfit.audit import (
    CHECK_ORDER,
    AuditRun,
    audit_company_metadata,
    audit_prices,
    audit_statements,
    cohort_summary,
    combine_company_audit,
    heatmap_rows,
    summarise_cohort,
)
from examples.stockfit.client import (
    JsonObject,
    StockFitClient,
    StockFitError,
    safe_rate_headers,
)

LIVE_COHORT = (
    "AAPL",
    "MSFT",
    "JPM",
    "BAC",
    "XOM",
    "CVX",
    "JNJ",
    "PFE",
    "WMT",
    "COST",
    "CAT",
    "UNP",
)
OFFLINE_COHORT = ("AAPL", "MSFT", "COST")
AUDIT_START = date(2021, 1, 1)
STATEMENT_LIMIT = 10
OFFLINE_OUTPUT_DIR = Path("artifacts/stockfit-audit")
LIVE_OUTPUT_DIR = Path("artifacts/stockfit-audit-live")
OFFLINE_GENERATED_AT = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
FIXTURE_DIR = Path(__file__).with_name("fixtures")
FIXTURE_NOTICE = "Invented synthetic data; not a StockFit response"
_GLOBAL_FAILURE_STATUSES = frozenset({401, 403, 429})
_INVALID_RESPONSE = object()
ARTIFACT_FILENAMES = frozenset(
    {"summary.json", "company-quality.csv", "data-quality.svg"}
)
_MANAGED_EXISTING_FILENAMES = ARTIFACT_FILENAMES | {".gitkeep"}
CSV_FIELDS = (
    "symbol",
    "company_name",
    "sector",
    "industry",
    "stable_identifiers_present",
    "price_start",
    "price_end",
    "price_observations",
    "price_duplicate_count",
    "price_invalid_count",
    "price_out_of_order_count",
    "largest_gap_days",
    "statement_count",
    "fiscal_year_start",
    "fiscal_year_end",
    "missing_fiscal_year_count",
    "duplicate_fiscal_year_count",
    "revenue_completeness_pct",
    "operating_income_completeness_pct",
    "median_filing_lag_days",
    "max_filing_lag_days",
    "source_entry_count",
    "before_fact_count",
    "derived_fact_count",
    "derived_fact_incidence_pct",
    "status",
    "reasons",
)


class AuditClient(Protocol):
    last_response_headers: dict[str, str]

    def company_details(self, symbol: str) -> JsonObject: ...

    def price_history(
        self,
        symbol: str,
        start: date,
        end: date,
        *,
        resolution: str = "1d",
        adjusted: bool = True,
    ) -> JsonObject: ...

    def income_statement(
        self,
        symbol: str,
        *,
        period: str = "annual",
        limit: int = 20,
        split_adjust: bool = True,
    ) -> list[JsonObject]: ...


class AuditRunAborted(RuntimeError):
    """A sanitised global failure that makes further requests unsafe."""

    def __init__(
        self,
        *,
        category: str,
        status: int,
        rate_limit: Mapping[str, str],
    ) -> None:
        self.category = category
        self.status = status
        self.rate_limit = dict(rate_limit)
        super().__init__(f"StockFit audit aborted: {category} (status={status})")


def collect_live_audit(
    client: AuditClient,
    *,
    execution_time: datetime,
) -> AuditRun:
    """Collect the frozen cohort sequentially with at most three calls each."""

    records = []
    failures: dict[str, str] = {}
    rate_limit: dict[str, str] = {}

    for symbol in LIVE_COHORT:
        company_payload, failure, headers = _call_safely(
            client, lambda symbol=symbol: client.company_details(symbol)
        )
        rate_limit.update(headers)
        if failure is not None:
            failures[symbol] = failure
            continue

        price_payload, failure, headers = _call_safely(
            client,
            lambda symbol=symbol: client.price_history(
                symbol,
                AUDIT_START,
                execution_time.date(),
                resolution="1d",
                adjusted=True,
            ),
        )
        rate_limit.update(headers)
        if failure is not None:
            failures[symbol] = failure
            continue

        statement_payload, failure, headers = _call_safely(
            client,
            lambda symbol=symbol: client.income_statement(
                symbol,
                period="annual",
                limit=STATEMENT_LIMIT,
                split_adjust=True,
            ),
        )
        rate_limit.update(headers)
        if failure is not None:
            failures[symbol] = failure
            continue

        metadata = audit_company_metadata(symbol, company_payload)
        prices = audit_prices(
            symbol,
            price_payload,
            request_start=AUDIT_START,
            execution_date=execution_time.date(),
        )
        statements = audit_statements(statement_payload)
        records.append(combine_company_audit(symbol, metadata, prices, statements))
        del company_payload, price_payload, statement_payload

    return summarise_cohort(
        cohort=LIVE_COHORT,
        records=records,
        failures=failures,
        request_start=AUDIT_START,
        execution_time=execution_time,
        mode="live",
        rate_limit=rate_limit,
    )


def run_offline_audit(destination: Path, *, now: datetime) -> AuditRun:
    """Audit only labelled invented fixtures and publish derived outputs."""

    records = []
    for symbol in OFFLINE_COHORT:
        company_payload = _load_fixture(FIXTURE_DIR / f"{symbol}-company.json")
        price_payload = _load_fixture(FIXTURE_DIR / f"{symbol}-price.json")
        statement_payload = _load_fixture(FIXTURE_DIR / f"{symbol}-income.json")
        records.append(
            combine_company_audit(
                symbol,
                audit_company_metadata(symbol, company_payload),
                audit_prices(
                    symbol,
                    price_payload,
                    request_start=AUDIT_START,
                    execution_date=now.date(),
                ),
                audit_statements(statement_payload),
            )
        )
        del company_payload, price_payload, statement_payload

    run = summarise_cohort(
        cohort=OFFLINE_COHORT,
        records=records,
        failures={},
        request_start=AUDIT_START,
        execution_time=now,
        mode="synthetic",
        rate_limit={},
    )
    publish_artifacts(run, destination)
    return run


def run_live_audit(
    token: str,
    destination: Path,
    *,
    now: datetime,
    client_factory: Callable[[str], AuditClient] = StockFitClient,
) -> AuditRun:
    """Collect a live audit, then publish only its derived records."""

    run = collect_live_audit(client_factory(token), execution_time=now)
    publish_artifacts(run, destination)
    return run


def _load_fixture(path: Path) -> object:
    try:
        wrapper = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not load fixture: {path.name}") from exc
    if not isinstance(wrapper, dict) or wrapper.get("fixtureNotice") != FIXTURE_NOTICE:
        raise ValueError(f"fixtureNotice is missing or invalid: {path.name}")
    if "payload" not in wrapper:
        raise ValueError(f"fixture payload is missing: {path.name}")
    return wrapper["payload"]


def _call_safely(
    client: AuditClient,
    operation: Callable[[], object],
) -> tuple[object, str | None, dict[str, str]]:
    try:
        payload = operation()
    except StockFitError as exc:
        headers = safe_rate_headers(client.last_response_headers)
        status = exc.status
        if status in _GLOBAL_FAILURE_STATUSES:
            assert status is not None
            raise AuditRunAborted(
                category=f"http_{status}",
                status=status,
                rate_limit=headers,
            ) from None
        if status is not None and 200 <= status < 300:
            return _INVALID_RESPONSE, None, headers
        category = "timeout_or_connection" if status is None else f"http_{status}"
        return _INVALID_RESPONSE, category, headers
    return payload, None, safe_rate_headers(client.last_response_headers)


def summary_mapping(run: AuditRun) -> dict[str, object]:
    """Return the deterministic cohort-only JSON payload."""

    return cohort_summary(run)


def company_csv(run: AuditRun) -> str:
    """Render the approved per-company derived fields as deterministic CSV."""

    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    for record in run.records:
        metadata = record.metadata
        prices = record.prices
        statements = record.statements
        writer.writerow(
            {
                "symbol": _csv_safe(record.symbol),
                "company_name": _csv_safe(metadata.company_name),
                "sector": _csv_safe(metadata.sector),
                "industry": _csv_safe(metadata.industry),
                "stable_identifiers_present": str(
                    metadata.stable_identifiers_present
                ).lower(),
                "price_start": _date_text(prices.start),
                "price_end": _date_text(prices.end),
                "price_observations": prices.observations,
                "price_duplicate_count": prices.duplicate_count,
                "price_invalid_count": prices.invalid_count,
                "price_out_of_order_count": prices.out_of_order_count,
                "largest_gap_days": _optional_text(prices.largest_gap_days),
                "statement_count": statements.statement_count,
                "fiscal_year_start": _optional_text(statements.fiscal_year_start),
                "fiscal_year_end": _optional_text(statements.fiscal_year_end),
                "missing_fiscal_year_count": statements.missing_fiscal_year_count,
                "duplicate_fiscal_year_count": statements.duplicate_fiscal_year_count,
                "revenue_completeness_pct": _percentage_text(
                    statements.revenue_completeness_pct
                ),
                "operating_income_completeness_pct": _percentage_text(
                    statements.operating_income_completeness_pct
                ),
                "median_filing_lag_days": _optional_text(
                    statements.median_filing_lag_days
                ),
                "max_filing_lag_days": _optional_text(statements.max_filing_lag_days),
                "source_entry_count": statements.source_entry_count,
                "before_fact_count": statements.before_fact_count,
                "derived_fact_count": statements.derived_fact_count,
                "derived_fact_incidence_pct": _percentage_text(
                    statements.derived_fact_incidence_pct
                ),
                "status": record.status,
                "reasons": ";".join(record.reasons),
            }
        )
    return output.getvalue()


def quality_chart(run: AuditRun) -> alt.LayerChart:
    """Build a fixed-order, non-interactive status heatmap."""

    values = [{**row, "label": row["status"][0].upper()} for row in heatmap_rows(run)]
    data = alt.Data(values=values)
    base = alt.Chart(data).encode(
        x=alt.X("check:N", sort=cast(Any, list(CHECK_ORDER)), title=None),
        y=alt.Y(
            "symbol:N",
            sort=[record.symbol for record in run.records],
            title=None,
        ),
    )
    rectangles = base.mark_rect().encode(
        color=alt.Color(
            "status:N",
            scale=alt.Scale(
                domain=["pass", "review", "fail"],
                range=["#2e7d32", "#ed9b40", "#c62828"],
            ),
            legend=alt.Legend(title="Status"),
        )
    )
    labels = base.mark_text(color="white", fontWeight="bold").encode(text="label:N")
    return (rectangles + labels).properties(
        width=560,
        height=max(80, 34 * len(run.records)),
        title=alt.TitleParams(
            text="StockFit data quality",
            subtitle=(
                "Derived provenance is informational; raw provider data is not retained"
            ),
        ),
    )


def publish_artifacts(run: AuditRun, destination: Path) -> None:
    """Render and atomically publish the complete three-file artifact set."""

    destination = destination.resolve(strict=False)
    parent = destination.parent
    parent.mkdir(parents=True, exist_ok=True)
    _validate_existing_destination(destination)

    staging = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}-staging-", dir=parent)
    ).resolve()
    backup = (parent / f".{destination.name}-backup-{uuid4().hex}").resolve()
    _require_direct_child(staging, parent)
    _require_direct_child(backup, parent)

    try:
        (staging / "summary.json").write_text(
            json.dumps(summary_mapping(run), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        (staging / "company-quality.csv").write_text(
            company_csv(run), encoding="utf-8", newline=""
        )
        quality_chart(run).save(staging / "data-quality.svg")

        had_destination = destination.exists()
        if had_destination:
            _rename_path(destination, backup)
        try:
            _rename_path(staging, destination)
        except BaseException:
            if backup.exists():
                _rename_path(backup, destination)
            raise
        if backup.exists():
            _safe_remove_tree(backup, parent)
    except BaseException:
        if staging.exists():
            _safe_remove_tree(staging, parent)
        raise


def _csv_safe(value: str | None) -> str:
    if value is None:
        return ""
    return f"'{value}" if value.startswith(("=", "+", "-", "@")) else value


def _date_text(value: date | None) -> str:
    return value.isoformat() if value else ""


def _optional_text(value: int | float | None) -> str:
    return "" if value is None else str(value)


def _percentage_text(value: float) -> str:
    return f"{value:.2f}"


def _validate_existing_destination(destination: Path) -> None:
    if not destination.exists():
        return
    if not destination.is_dir():
        raise ValueError("artifact destination must be a directory")
    unrelated = [
        path.name
        for path in destination.iterdir()
        if not path.is_file() or path.name not in _MANAGED_EXISTING_FILENAMES
    ]
    if unrelated:
        raise ValueError(
            f"artifact destination contains unrelated entries: {unrelated}"
        )


def _rename_path(source: Path, target: Path) -> None:
    source.rename(target)


def _require_direct_child(path: Path, parent: Path) -> None:
    if path.resolve(strict=False).parent != parent.resolve():
        raise ValueError("temporary artifact path escaped its intended parent")


def _safe_remove_tree(path: Path, parent: Path) -> None:
    _require_direct_child(path, parent)
    shutil.rmtree(path)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit StockFit data quality.")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args(argv)


def _stockfit_token() -> str:
    return os.environ.get("STOCKFIT_TOKEN", "").strip()


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
        token = _stockfit_token()
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


if __name__ == "__main__":
    raise SystemExit(main())
