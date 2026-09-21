from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, time

import pytest

from examples.stockfit.audit import (
    CHECK_ORDER,
    AuditStatus,
    audit_company_metadata,
    audit_prices,
    audit_statements,
    cohort_summary,
    combine_company_audit,
    heatmap_rows,
    summarise_cohort,
    worst_status,
)

FIXED_NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


def timestamp(day: date) -> int:
    return int(datetime.combine(day, time(), tzinfo=UTC).timestamp() * 1000)


def price_payload(symbol: str, dates: list[date]) -> dict[str, object]:
    return {
        "symbol": symbol,
        "data": [[timestamp(day), 100.0] for day in dates],
    }


def test_worst_status_uses_fail_review_pass_severity() -> None:
    assert worst_status("pass", "review") == "review"
    assert worst_status("review", "fail", "pass") == "fail"
    assert worst_status() == "pass"


def test_company_metadata_rejects_wrong_symbol() -> None:
    result = audit_company_metadata(
        "AAPL",
        {"symbols": ["MSFT"], "name": "Wrong", "sector": "Technology"},
    )

    assert result.status == "fail"
    assert result.reasons == ("company_symbol_mismatch",)


def test_company_metadata_reviews_missing_sector_and_industry_in_stable_order() -> None:
    result = audit_company_metadata(
        "AAPL",
        {"symbols": ["AAPL"], "name": "Example"},
    )

    assert result.status == "review"
    assert result.reasons == (
        "company_sector_missing",
        "company_industry_missing",
    )


def test_company_metadata_records_identifier_presence_without_value() -> None:
    result = audit_company_metadata(
        " aapl ",
        {
            "symbols": ["AAPL"],
            "name": "Example",
            "sector": "Technology",
            "industry": "Devices",
            "cik": "private-identifier",
        },
    )

    assert result.symbol == "AAPL"
    assert result.stable_identifiers_present is True
    assert result.status == "pass"


def test_company_metadata_rejects_non_object_response() -> None:
    result = audit_company_metadata("AAPL", [])

    assert result.status == "fail"
    assert result.reasons == ("company_response_invalid",)


@pytest.mark.parametrize(
    ("returned_start", "expected_status", "expected_reasons"),
    [
        (date(2021, 2, 1), "pass", ()),
        (date(2021, 2, 2), "review", ("price_start_late",)),
    ],
)
def test_price_start_boundary(
    returned_start: date,
    expected_status: str,
    expected_reasons: tuple[str, ...],
) -> None:
    result = audit_prices(
        "AAA",
        price_payload("AAA", [returned_start, date(2021, 2, 8)]),
        request_start=date(2021, 1, 1),
        execution_date=date(2021, 2, 8),
    )

    assert result.status == expected_status
    assert result.reasons == expected_reasons


@pytest.mark.parametrize(
    ("returned_end", "expected_status", "expected_reasons"),
    [
        (date(2021, 1, 8), "pass", ()),
        (date(2021, 1, 7), "review", ("price_end_stale",)),
    ],
)
def test_price_end_boundary(
    returned_end: date,
    expected_status: str,
    expected_reasons: tuple[str, ...],
) -> None:
    result = audit_prices(
        "AAA",
        price_payload("AAA", [returned_end]),
        request_start=date(2021, 1, 1),
        execution_date=date(2021, 1, 15),
    )

    assert result.status == expected_status
    assert result.reasons == expected_reasons


@pytest.mark.parametrize(
    ("second_date", "expected_status", "expected_reasons"),
    [
        (date(2021, 1, 8), "pass", ()),
        (date(2021, 1, 9), "review", ("price_gap_large",)),
    ],
)
def test_price_gap_boundary(
    second_date: date,
    expected_status: str,
    expected_reasons: tuple[str, ...],
) -> None:
    result = audit_prices(
        "AAA",
        price_payload("AAA", [date(2021, 1, 1), second_date]),
        request_start=date(2021, 1, 1),
        execution_date=second_date,
    )

    assert result.largest_gap_days == (second_date - date(2021, 1, 1)).days
    assert result.status == expected_status
    assert result.reasons == expected_reasons


def test_price_symbol_mismatch_fails() -> None:
    result = audit_prices(
        "AAA",
        price_payload("BBB", [date(2021, 1, 4)]),
        request_start=date(2021, 1, 1),
        execution_date=date(2021, 1, 4),
    )

    assert result.status == "fail"
    assert result.reasons == ("price_symbol_mismatch",)


def test_empty_price_series_fails_without_coverage_reviews() -> None:
    result = audit_prices(
        "AAA",
        {"symbol": "AAA", "data": []},
        request_start=date(2021, 1, 1),
        execution_date=date(2021, 1, 4),
    )

    assert result.observations == 0
    assert result.status == "fail"
    assert result.reasons == ("price_empty",)


@pytest.mark.parametrize(
    "observation",
    [
        [timestamp(date(2021, 1, 4))],
        ["not-a-timestamp", 100.0],
        [timestamp(date(2021, 1, 4)), "not-a-price"],
        [timestamp(date(2021, 1, 4)), float("nan")],
        [timestamp(date(2021, 1, 4)), 0.0],
        [timestamp(date(2021, 1, 4)), -1.0],
    ],
)
def test_invalid_price_observation_fails(observation: list[object]) -> None:
    result = audit_prices(
        "AAA",
        {"symbol": "AAA", "data": [observation]},
        request_start=date(2021, 1, 1),
        execution_date=date(2021, 1, 4),
    )

    assert result.invalid_count == 1
    assert result.status == "fail"
    assert result.reasons == ("price_observation_invalid",)


def test_duplicate_price_timestamp_fails_and_is_counted() -> None:
    stamp = timestamp(date(2021, 1, 4))
    result = audit_prices(
        "AAA",
        {"symbol": "AAA", "data": [[stamp, 100.0], [stamp, 101.0]]},
        request_start=date(2021, 1, 1),
        execution_date=date(2021, 1, 4),
    )

    assert result.observations == 1
    assert result.duplicate_count == 1
    assert result.status == "fail"
    assert result.reasons == ("price_duplicate_timestamp",)


def test_out_of_order_prices_are_counted_then_normalised() -> None:
    result = audit_prices(
        "AAA",
        price_payload("AAA", [date(2021, 1, 5), date(2021, 1, 4)]),
        request_start=date(2021, 1, 1),
        execution_date=date(2021, 1, 5),
    )

    assert result.start == date(2021, 1, 4)
    assert result.end == date(2021, 1, 5)
    assert result.observations == 2
    assert result.out_of_order_count == 1
    assert result.status == "pass"
    assert result.reasons == ()


def test_invalid_and_duplicate_reasons_follow_declared_order() -> None:
    stamp = timestamp(date(2021, 1, 4))
    result = audit_prices(
        "AAA",
        {
            "symbol": "AAA",
            "data": [[stamp, 100.0], [stamp, 101.0], [stamp + 1, None]],
        },
        request_start=date(2021, 1, 1),
        execution_date=date(2021, 1, 4),
    )

    assert result.status == "fail"
    assert result.reasons == (
        "price_observation_invalid",
        "price_duplicate_timestamp",
    )


@pytest.mark.parametrize("payload", [[], {"symbol": "AAA"}, {"data": []}])
def test_invalid_price_response_shape_fails(payload: object) -> None:
    result = audit_prices(
        "AAA",
        payload,
        request_start=date(2021, 1, 1),
        execution_date=date(2021, 1, 4),
    )

    assert result.status == "fail"
    assert result.reasons == ("price_response_invalid",)


def statement(
    period: str,
    filed: str,
    *,
    fiscal_year: object | None = None,
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


def five_statements() -> list[dict[str, object]]:
    return [
        statement(f"{year}-12-31", f"{year + 1}-02-01") for year in range(2020, 2025)
    ]


def test_filing_lag_boundary() -> None:
    passing_rows = five_statements()
    passing_rows[-1] = statement("2024-12-31", "2025-03-31")
    reviewing_rows = five_statements()
    reviewing_rows[-1] = statement("2024-12-31", "2025-04-01")

    passing = audit_statements(passing_rows)
    reviewing = audit_statements(reviewing_rows)

    assert passing.max_filing_lag_days == 90
    assert "filing_lag_large" not in passing.reasons
    assert reviewing.max_filing_lag_days == 91
    assert "filing_lag_large" in reviewing.reasons


def test_negative_filing_lag_fails() -> None:
    rows = five_statements()
    rows[-1] = statement("2024-12-31", "2024-12-30")
    result = audit_statements(rows)

    assert result.status == "fail"
    assert "filing_before_period_end" in result.reasons


def test_numeric_before_is_counted_when_amendment_is_false() -> None:
    rows = five_statements()
    rows[-1] = statement(
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
    result = audit_statements(rows)

    assert result.source_entry_count == 1
    assert result.before_fact_count == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [("facts", []), ("sources", []), ("derived", {})],
)
def test_malformed_nested_statement_shape_fails(field: str, value: object) -> None:
    row = statement("2024-12-31", "2025-02-01")
    row[field] = value
    result = audit_statements([row])

    assert result.status == "fail"
    assert result.reasons == ("statement_row_invalid",)


@pytest.mark.parametrize(
    ("field", "value"),
    [("period", "not-a-date"), ("dateFiled", "2025-02-30")],
)
def test_malformed_statement_dates_fail(field: str, value: object) -> None:
    row = statement("2024-12-31", "2025-02-01")
    row[field] = value

    assert audit_statements([row]).reasons == ("statement_row_invalid",)


def test_duplicate_and_missing_fiscal_years_are_counted() -> None:
    rows = [
        statement("2020-12-31", "2021-02-01"),
        statement("2022-12-31", "2023-02-01"),
        statement("2023-12-31", "2024-02-01", fiscal_year=2022),
        statement("2024-12-31", "2025-02-01"),
        statement("2025-12-31", "2026-02-01"),
    ]
    result = audit_statements(rows)

    assert result.duplicate_fiscal_year_count == 1
    assert result.missing_fiscal_year_count == 2
    assert "fiscal_year_duplicate" in result.reasons
    assert "fiscal_year_gap" in result.reasons
    assert result.status == "fail"


def test_short_statement_history_is_reviewed() -> None:
    result = audit_statements(five_statements()[:4])

    assert result.status == "review"
    assert result.reasons == ("statement_history_short",)


@pytest.mark.parametrize("fiscal_year", ["2024", True])
def test_invalid_fiscal_year_uses_period_year(fiscal_year: object) -> None:
    rows = five_statements()
    rows[-1] = statement(
        "2024-12-31",
        "2025-02-01",
        fiscal_year=fiscal_year,
    )
    result = audit_statements(rows)

    assert result.fiscal_year_end == 2024
    assert "fiscal_year_fallback" in result.reasons
    assert result.status == "review"


def test_absent_fiscal_year_uses_period_year() -> None:
    rows = five_statements()
    rows[-1].pop("fiscalYear")
    result = audit_statements(rows)

    assert result.fiscal_year_end == 2024
    assert "fiscal_year_fallback" in result.reasons


def test_required_fact_completeness_counts_only_finite_numbers() -> None:
    rows = five_statements()
    rows[0] = statement("2020-12-31", "2021-02-01", revenue=None)
    rows[1] = statement("2021-12-31", "2022-02-01", operating_income=float("inf"))
    result = audit_statements(rows)

    assert result.revenue_completeness_pct == 80.0
    assert result.operating_income_completeness_pct == 80.0
    assert "revenue_incomplete" in result.reasons
    assert "operating_income_incomplete" in result.reasons


@pytest.mark.parametrize("revenue", [0.0, -1.0])
def test_non_positive_revenue_is_not_usable_for_consecutive_pair(
    revenue: float,
) -> None:
    rows = [
        statement("2020-12-31", "2021-02-01", revenue=revenue),
        statement("2021-12-31", "2022-02-01"),
        statement("2023-12-31", "2024-02-01"),
        statement("2025-12-31", "2026-02-01"),
        statement("2027-12-31", "2028-02-01"),
    ]
    result = audit_statements(rows)

    assert "no_usable_consecutive_pair" in result.reasons
    assert result.status == "fail"


def test_usable_consecutive_pair_passes() -> None:
    result = audit_statements(five_statements())

    assert result.status == "pass"
    assert "no_usable_consecutive_pair" not in result.reasons


def test_filing_lag_median_handles_odd_and_even_counts() -> None:
    odd = audit_statements(
        [
            statement("2022-12-31", "2023-01-10"),
            statement("2023-12-31", "2024-01-20"),
            statement("2024-12-31", "2025-01-30"),
        ]
    )
    even = audit_statements(
        [
            statement("2021-12-31", "2022-01-10"),
            statement("2022-12-31", "2023-01-20"),
            statement("2023-12-31", "2024-01-30"),
            statement("2024-12-31", "2025-02-09"),
        ]
    )

    assert odd.median_filing_lag_days == 20.0
    assert even.median_filing_lag_days == 25.0


def test_nonnumeric_before_is_ignored_and_derived_incidence_is_measured() -> None:
    rows = five_statements()
    rows[-1] = statement(
        "2024-12-31",
        "2025-02-01",
        sources={
            "private-source-id": {"facts": {"revenue": {"before": "not-numeric"}}}
        },
        derived=["operatingMargin"],
    )
    result = audit_statements(rows)

    assert result.before_fact_count == 0
    assert result.derived_fact_count == 1
    assert result.fact_count == 10
    assert result.derived_fact_incidence_pct == 10.0


@pytest.mark.parametrize("payload", [{}, None, "bad"])
def test_invalid_statement_response_shape_fails(payload: object) -> None:
    result = audit_statements(payload)

    assert result.status == "fail"
    assert result.reasons == ("statement_response_invalid",)


def test_company_audit_combines_status_and_reasons_in_declared_order() -> None:
    metadata = audit_company_metadata(
        "AAA", {"symbols": ["AAA"], "name": "Example", "sector": "Industrials"}
    )
    prices = audit_prices(
        "AAA",
        price_payload("AAA", [date(2021, 1, 1), date(2021, 1, 9)]),
        request_start=date(2021, 1, 1),
        execution_date=date(2021, 1, 9),
    )
    statements = audit_statements(five_statements())

    result = combine_company_audit(" aaa ", metadata, prices, statements)

    assert result.symbol == "AAA"
    assert result.status == "review"
    assert result.reasons == ("company_industry_missing", "price_gap_large")


def company_audit(
    symbol: str,
    start: date = date(2021, 1, 4),
    end: date = date(2025, 1, 3),
    *,
    status: AuditStatus = "pass",
):
    metadata = audit_company_metadata(
        symbol,
        {
            "symbols": [symbol],
            "name": f"{symbol} Company",
            "sector": "Technology",
            "industry": "Software",
        },
    )
    prices = replace(
        audit_prices(
            symbol,
            price_payload(symbol, [end]),
            request_start=end,
            execution_date=end,
        ),
        start=start,
        end=end,
    )
    result = combine_company_audit(
        symbol, metadata, prices, audit_statements(five_statements())
    )
    return replace(result, status=status)


def test_cohort_summary_uses_actual_shared_price_intersection() -> None:
    run = summarise_cohort(
        cohort=("AAA", "BBB"),
        records=(
            company_audit("AAA", date(2021, 1, 4), date(2025, 1, 3)),
            company_audit("BBB", date(2021, 2, 1), date(2024, 12, 31)),
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
    assert run.failure_categories == ("timeout",)
    assert tuple(record.symbol for record in run.records) == ("AAA",)


def test_non_overlapping_price_ranges_have_no_shared_coverage() -> None:
    run = summarise_cohort(
        cohort=("AAA", "BBB"),
        records=(
            company_audit("AAA", date(2021, 1, 1), date(2021, 1, 31)),
            company_audit("BBB", date(2021, 2, 1), date(2021, 2, 28)),
        ),
        failures={},
        request_start=date(2021, 1, 1),
        execution_time=FIXED_NOW,
        mode="synthetic",
        rate_limit={},
    )

    assert run.shared_price_start is None
    assert run.shared_price_end is None


def test_records_and_missing_symbols_follow_frozen_cohort_order() -> None:
    run = summarise_cohort(
        cohort=("AAA", "BBB", "CCC"),
        records=(company_audit("CCC"), company_audit("AAA")),
        failures={"BBB": "http_500"},
        request_start=date(2021, 1, 1),
        execution_time=FIXED_NOW,
        mode="live",
        rate_limit={"ratelimit-remaining": "7"},
    )

    assert tuple(record.symbol for record in run.records) == ("AAA", "CCC")
    assert run.missing_symbols == ("BBB",)
    assert run.failure_categories == ("http_500",)


def test_cohort_metrics_include_distributions_and_provenance_totals() -> None:
    first = company_audit("AAA")
    second = company_audit("BBB")
    first = replace(
        first,
        statements=replace(
            first.statements,
            revenue_completeness_pct=80.0,
            operating_income_completeness_pct=60.0,
            median_filing_lag_days=30.0,
            max_filing_lag_days=45,
            source_entry_count=2,
            before_fact_count=1,
            derived_fact_count=3,
        ),
    )
    second = replace(
        second,
        statements=replace(
            second.statements,
            revenue_completeness_pct=100.0,
            operating_income_completeness_pct=100.0,
            median_filing_lag_days=50.0,
            max_filing_lag_days=70,
            source_entry_count=4,
            before_fact_count=2,
            derived_fact_count=5,
        ),
    )
    run = summarise_cohort(
        cohort=("AAA", "BBB"),
        records=(first, second),
        failures={},
        request_start=date(2021, 1, 1),
        execution_time=FIXED_NOW,
        mode="synthetic",
        rate_limit={},
    )

    summary = cohort_summary(run)

    assert summary["revenue_completeness_pct"] == {
        "min": 80.0,
        "median": 90.0,
        "max": 100.0,
    }
    assert summary["operating_income_completeness_pct"] == {
        "min": 60.0,
        "median": 80.0,
        "max": 100.0,
    }
    assert summary["median_filing_lag_days"] == {
        "min": 30.0,
        "median": 40.0,
        "max": 50.0,
    }
    assert summary["max_filing_lag_days"] == {
        "min": 45.0,
        "median": 57.5,
        "max": 70.0,
    }
    assert summary["provenance_totals"] == {
        "source_entries": 6,
        "numeric_before_facts": 3,
        "derived_facts": 8,
    }
    assert "records" not in summary
    assert summary["thresholds"]
    assert summary["reason_definitions"]


def test_unknown_failure_category_is_rejected() -> None:
    with pytest.raises(ValueError, match="failure category"):
        summarise_cohort(
            cohort=("AAA",),
            records=(),
            failures={"AAA": "private provider body"},
            request_start=date(2021, 1, 1),
            execution_time=FIXED_NOW,
            mode="live",
            rate_limit={},
        )


def test_heatmap_has_every_check_for_every_company() -> None:
    run = summarise_cohort(
        cohort=("AAA", "BBB"),
        records=(company_audit("AAA"), company_audit("BBB")),
        failures={},
        request_start=date(2021, 1, 1),
        execution_time=FIXED_NOW,
        mode="synthetic",
        rate_limit={},
    )

    rows = heatmap_rows(run)

    assert len(rows) == len(run.records) * len(CHECK_ORDER)
    assert tuple(row["check"] for row in rows[: len(CHECK_ORDER)]) == CHECK_ORDER
    assert {row["status"] for row in rows} == {"pass"}
