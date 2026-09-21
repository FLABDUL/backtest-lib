from __future__ import annotations

from datetime import UTC, date, datetime, time

import pytest

from examples.stockfit.audit import (
    audit_company_metadata,
    audit_prices,
    worst_status,
)


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
