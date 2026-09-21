from __future__ import annotations

import csv
import json
import traceback
from dataclasses import replace
from datetime import UTC, date, datetime, time
from io import StringIO
from pathlib import Path

import pytest

from examples.stockfit import audit_cli
from examples.stockfit.audit import summarise_cohort
from examples.stockfit.audit_cli import (
    LIVE_COHORT,
    AuditRunAborted,
    _load_fixture,
    collect_live_audit,
    company_csv,
    main,
    parse_args,
    publish_artifacts,
    quality_chart,
    run_offline_audit,
    summary_mapping,
)
from examples.stockfit.client import StockFitError

FIXED_NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


def timestamp(day: date) -> int:
    return int(datetime.combine(day, time(), tzinfo=UTC).timestamp() * 1000)


class FakeAuditClient:
    def __init__(
        self,
        *,
        fail_at: int | None = None,
        status: int | None = None,
        message: str = "request failed",
        timeout_symbol: str | None = None,
        malformed_price_symbol: str | None = None,
    ) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.last_response_headers: dict[str, str] = {}
        self.fail_at = fail_at
        self.status = status
        self.message = message
        self.timeout_symbol = timeout_symbol
        self.malformed_price_symbol = malformed_price_symbol

    def _before_call(self, symbol: str) -> None:
        self.last_response_headers = {
            "X-RateLimit-Remaining": str(100 - len(self.calls)),
            "Authorization": "private-token",
        }
        if self.fail_at == len(self.calls):
            raise StockFitError(self.message, status=self.status)
        if self.timeout_symbol == symbol:
            self.timeout_symbol = None
            raise StockFitError(self.message)

    def company_details(self, symbol: str) -> dict[str, object]:
        self.calls.append(("company_details", symbol))
        self._before_call(symbol)
        return {
            "symbols": [symbol],
            "name": f"{symbol} Company",
            "sector": "Example",
            "industry": "Example",
        }

    def price_history(
        self,
        symbol: str,
        start: date,
        end: date,
        *,
        resolution: str = "1d",
        adjusted: bool = True,
    ) -> dict[str, object]:
        self.calls.append(("price_history", symbol, start, end, resolution, adjusted))
        self._before_call(symbol)
        if self.malformed_price_symbol == symbol:
            raise StockFitError("private malformed response", status=200)
        return {
            "symbol": symbol,
            "data": [[timestamp(start), 100.0], [timestamp(end), 101.0]],
        }

    def income_statement(
        self,
        symbol: str,
        *,
        period: str = "annual",
        limit: int = 20,
        split_adjust: bool = True,
    ) -> list[dict[str, object]]:
        self.calls.append(("income_statement", symbol, period, limit, split_adjust))
        self._before_call(symbol)
        return [
            {
                "period": f"{year}-12-31",
                "fiscalYear": year,
                "fiscalPeriod": "FY",
                "dateFiled": f"{year + 1}-02-01",
                "facts": {"revenue": 100.0, "operatingIncome": 10.0},
                "sources": {},
                "derived": [],
            }
            for year in range(2020, 2025)
        ]


def test_frozen_live_cohort_is_sector_diversified_and_stable() -> None:
    assert LIVE_COHORT == (
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


def test_successful_live_collection_makes_three_ordered_calls_per_symbol() -> None:
    client = FakeAuditClient()

    run = collect_live_audit(client, execution_time=FIXED_NOW)

    assert client.calls == [
        call
        for symbol in LIVE_COHORT
        for call in (
            ("company_details", symbol),
            (
                "price_history",
                symbol,
                date(2021, 1, 1),
                FIXED_NOW.date(),
                "1d",
                True,
            ),
            ("income_statement", symbol, "annual", 10, True),
        )
    ]
    assert len(client.calls) == 36
    assert run.run_status == "complete"
    assert run.rate_limit == {"x-ratelimit-remaining": "64"}


@pytest.mark.parametrize("status", [401, 403, 429])
def test_global_http_failures_abort_without_continuing(status: int) -> None:
    client = FakeAuditClient(
        fail_at=2,
        status=status,
        message="private body with private-token",
    )

    with pytest.raises(AuditRunAborted) as exc_info:
        collect_live_audit(client, execution_time=FIXED_NOW)

    assert len(client.calls) == 2
    assert exc_info.value.status == status
    assert exc_info.value.category == f"http_{status}"
    rendered = "".join(
        traceback.format_exception(
            type(exc_info.value), exc_info.value, exc_info.value.__traceback__
        )
    )
    assert "private body" not in rendered
    assert "private-token" not in rendered


def test_timeout_marks_symbol_missing_and_continues() -> None:
    client = FakeAuditClient(timeout_symbol="JPM", message="private timeout body")

    run = collect_live_audit(client, execution_time=FIXED_NOW)

    assert run.run_status == "incomplete"
    assert run.missing_symbols == ("JPM",)
    assert "timeout_or_connection" in run.failure_categories
    assert client.calls[-1][1] == "UNP"


@pytest.mark.parametrize("status", [400, 500])
def test_company_http_failure_is_categorised_and_collection_continues(
    status: int,
) -> None:
    client = FakeAuditClient(fail_at=1, status=status, message="private body")

    run = collect_live_audit(client, execution_time=FIXED_NOW)

    assert run.missing_symbols == ("AAPL",)
    assert run.failure_categories == (f"http_{status}",)
    assert client.calls[-1][1] == "UNP"


def test_success_status_shape_error_becomes_fail_row_and_continues_calls() -> None:
    client = FakeAuditClient(malformed_price_symbol="JPM")

    run = collect_live_audit(client, execution_time=FIXED_NOW)

    jpm = next(record for record in run.records if record.symbol == "JPM")
    assert run.run_status == "complete"
    assert jpm.status == "fail"
    assert jpm.reasons == ("price_response_invalid",)
    assert ("income_statement", "JPM", "annual", 10, True) in client.calls


def example_run(
    *,
    symbol: str = "AAPL",
    company_name: str | None = "Example Company",
):
    live_run = collect_live_audit(FakeAuditClient(), execution_time=FIXED_NOW)
    record = next(record for record in live_run.records if record.symbol == symbol)
    record = replace(
        record,
        metadata=replace(record.metadata, company_name=company_name),
    )
    return summarise_cohort(
        cohort=(symbol,),
        records=(record,),
        failures={},
        request_start=date(2021, 1, 1),
        execution_time=FIXED_NOW,
        mode="synthetic",
        rate_limit={},
    )


def artifact_bytes(destination: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in destination.iterdir()}


def test_publish_writes_only_the_three_derived_artifacts(tmp_path: Path) -> None:
    destination = tmp_path / "audit"

    publish_artifacts(example_run(), destination)

    assert sorted(path.name for path in destination.iterdir()) == [
        "company-quality.csv",
        "data-quality.svg",
        "summary.json",
    ]


@pytest.mark.parametrize("prefix", ["=", "+", "-", "@"])
def test_csv_neutralises_formula_prefix_and_summary_stays_cohort_only(
    tmp_path: Path,
    prefix: str,
) -> None:
    run = example_run(company_name=f'{prefix}HYPERLINK("bad")')

    publish_artifacts(run, tmp_path / "audit")
    csv_text = (tmp_path / "audit" / "company-quality.csv").read_text()
    summary = json.loads((tmp_path / "audit" / "summary.json").read_text())

    assert f"'{prefix}HYPERLINK" in csv_text
    assert summary["cohort"] == ["AAPL"]


def test_summary_is_deterministic_cohort_only_and_omits_forbidden_keys() -> None:
    run = example_run()

    first = summary_mapping(run)
    second = summary_mapping(run)

    def keys(value: object) -> set[str]:
        if isinstance(value, dict):
            return {str(key).lower() for key in value} | {
                nested for item in value.values() for nested in keys(item)
            }
        if isinstance(value, list):
            return {nested for item in value for nested in keys(item)}
        return set()

    assert first == second
    assert "records" not in first
    for forbidden in ("prices", "facts", "sources", "accession"):
        assert forbidden not in keys(first)


def test_company_csv_has_frozen_order_formatting_and_stable_reasons() -> None:
    live_run = collect_live_audit(FakeAuditClient(), execution_time=FIXED_NOW)
    run = summarise_cohort(
        cohort=("AAPL", "MSFT"),
        records=(live_run.records[1], live_run.records[0]),
        failures={},
        request_start=date(2021, 1, 1),
        execution_time=FIXED_NOW,
        mode="synthetic",
        rate_limit={},
    )

    rows = list(csv.DictReader(StringIO(company_csv(run))))

    assert [row["symbol"] for row in rows] == ["AAPL", "MSFT"]
    assert rows[0]["revenue_completeness_pct"] == "100.00"
    assert rows[0]["reasons"] == "price_gap_large"
    assert "price_values" not in rows[0]
    assert "facts" not in rows[0]


def test_company_csv_formats_empty_optional_values() -> None:
    run = example_run(company_name=None)
    record = run.records[0]
    record = replace(
        record,
        prices=replace(
            record.prices,
            start=None,
            end=None,
            largest_gap_days=None,
        ),
    )
    run = replace(run, records=(record,))

    row = next(csv.DictReader(StringIO(company_csv(run))))

    assert row["company_name"] == ""
    assert row["price_start"] == ""
    assert row["price_end"] == ""
    assert row["largest_gap_days"] == ""


def test_svg_contains_labels_and_no_interactive_markers(tmp_path: Path) -> None:
    output = tmp_path / "quality.svg"

    quality_chart(example_run()).save(output)
    svg = output.read_text()

    assert "StockFit data quality" in svg
    assert "Synthetic demonstration data; not a StockFit response" in svg
    assert "Provenance incidence is informational" in svg
    assert "AAPL" in svg
    assert "company_metadata" in svg
    assert ">R<" in svg or ">P<" in svg or ">F<" in svg
    assert "tooltip" not in svg.lower()
    assert "vega-embed" not in svg.lower()
    assert "download" not in svg.lower()


def test_live_svg_identifies_live_derived_metadata(tmp_path: Path) -> None:
    output = tmp_path / "quality.svg"
    run = replace(example_run(), mode="live")

    quality_chart(run).save(output)
    svg = output.read_text()

    assert "Derived StockFit live metadata; raw provider data is not retained" in svg
    assert "Provenance incidence is informational" in svg


def test_second_successful_publish_replaces_all_three_artifacts(tmp_path: Path) -> None:
    destination = tmp_path / "audit"
    publish_artifacts(example_run(symbol="AAPL"), destination)
    first = artifact_bytes(destination)

    publish_artifacts(example_run(symbol="MSFT"), destination)
    second = artifact_bytes(destination)

    assert first.keys() == second.keys()
    assert all(first[name] != second[name] for name in first)


def test_unrelated_destination_file_refuses_without_changes(tmp_path: Path) -> None:
    destination = tmp_path / "audit"
    destination.mkdir()
    unrelated = destination / "notes.txt"
    unrelated.write_text("keep me")
    before = artifact_bytes(destination)

    with pytest.raises(ValueError, match="unrelated"):
        publish_artifacts(example_run(), destination)

    assert artifact_bytes(destination) == before


def test_failed_directory_swap_restores_old_set_and_cleans_temporary_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "audit"
    publish_artifacts(example_run(symbol="AAPL"), destination)
    before = artifact_bytes(destination)
    real_rename = audit_cli._rename_path
    failed = False

    def fail_staging_rename(source: Path, target: Path) -> None:
        nonlocal failed
        if "-staging-" in source.name and not failed:
            failed = True
            raise OSError("injected staging rename failure")
        real_rename(source, target)

    monkeypatch.setattr(audit_cli, "_rename_path", fail_staging_rename)

    with pytest.raises(OSError, match="injected"):
        publish_artifacts(example_run(symbol="MSFT"), destination)

    assert artifact_bytes(destination) == before
    assert not any("-staging-" in path.name for path in tmp_path.iterdir())
    assert not any("-backup-" in path.name for path in tmp_path.iterdir())


def test_default_cli_is_offline_and_deterministic(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("STOCKFIT_TOKEN", raising=False)

    first = run_offline_audit(tmp_path, now=FIXED_NOW)
    first_bytes = artifact_bytes(tmp_path)
    second = run_offline_audit(tmp_path, now=FIXED_NOW)

    assert first == second
    assert first_bytes == artifact_bytes(tmp_path)
    assert first.mode == "synthetic"
    assert first.cohort == ("AAPL", "MSFT", "COST")


def test_live_cli_requires_process_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("STOCKFIT_TOKEN", raising=False)

    with pytest.raises(SystemExit, match="Set STOCKFIT_TOKEN"):
        main(["--live", "--output-dir", str(tmp_path)], now=FIXED_NOW)


def test_incomplete_live_run_writes_labelled_outputs_and_exits_two(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("STOCKFIT_TOKEN", "invented-token")

    result = main(
        ["--live", "--output-dir", str(tmp_path)],
        now=FIXED_NOW,
        client_factory=lambda token: FakeAuditClient(timeout_symbol="JPM"),
    )

    assert result == 2
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["run_status"] == "incomplete"
    assert summary["missing_symbols"] == ["JPM"]


def test_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        parse_args(["--help"])

    assert exc_info.value.code == 0
    assert "Audit StockFit data quality" in capsys.readouterr().out


def test_live_default_output_directory_is_selected_after_parsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("STOCKFIT_TOKEN", "invented-token")
    seen: list[Path] = []

    def fake_run_live(token, destination, *, now, client_factory):
        seen.append(destination)
        return example_run()

    monkeypatch.setattr(audit_cli, "run_live_audit", fake_run_live)

    assert main(["--live"], now=FIXED_NOW) == 0
    assert seen == [audit_cli.LIVE_OUTPUT_DIR]


def test_successful_live_main_returns_zero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("STOCKFIT_TOKEN", "invented-token")

    result = main(
        ["--live", "--output-dir", str(tmp_path)],
        now=FIXED_NOW,
        client_factory=lambda token: FakeAuditClient(),
    )

    assert result == 0


def test_global_abort_leaves_existing_destination_unchanged(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_offline_audit(tmp_path, now=FIXED_NOW)
    before = artifact_bytes(tmp_path)
    monkeypatch.setenv("STOCKFIT_TOKEN", "invented-token")

    with pytest.raises(SystemExit) as exc_info:
        main(
            ["--live", "--output-dir", str(tmp_path)],
            now=FIXED_NOW,
            client_factory=lambda token: FakeAuditClient(
                fail_at=1,
                status=401,
                message="private body invented-token",
            ),
        )

    assert artifact_bytes(tmp_path) == before
    assert "private body" not in str(exc_info.value)
    assert "invented-token" not in str(exc_info.value)
    assert json.loads(str(exc_info.value))["category"] == "http_401"


def test_fixture_notice_is_required(tmp_path: Path) -> None:
    fixture = tmp_path / "bad.json"
    fixture.write_text(json.dumps({"fixtureNotice": "wrong", "payload": {}}))

    with pytest.raises(ValueError, match="fixtureNotice"):
        _load_fixture(fixture)


def test_offline_path_does_not_read_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fail_token_lookup() -> str:
        raise AssertionError("unexpected STOCKFIT_TOKEN lookup")

    monkeypatch.setattr(audit_cli, "_stockfit_token", fail_token_lookup)

    assert main(["--output-dir", str(tmp_path)], now=FIXED_NOW) == 0


def test_stdout_contains_only_summary_mapping(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--output-dir", str(tmp_path)], now=FIXED_NOW) == 0

    printed = json.loads(capsys.readouterr().out)
    assert printed["mode"] == "synthetic"
    assert "records" not in printed
