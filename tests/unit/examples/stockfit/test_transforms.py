from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any

import numpy as np
import pytest

from examples.stockfit.transforms import (
    build_market,
    fact_as_of,
    price_frame,
    signal_frames,
)


def make_statement(
    period: str,
    filed: str,
    revenue: float | None,
    operating_income: float | None,
    *,
    sources: Mapping[str, Any] | None = None,
) -> dict[str, object]:
    facts: dict[str, float] = {}
    if revenue is not None:
        facts["revenue"] = revenue
    if operating_income is not None:
        facts["operatingIncome"] = operating_income
    return {
        "period": period,
        "dateFiled": filed,
        "facts": facts,
        "sources": sources or {},
    }


def test_price_frame_sorts_and_intersects_dates() -> None:
    payloads = {
        "AAA": {
            "symbol": "AAA",
            "data": [[1735862400000, 12.0], [1735689600000, 10.0]],
        },
        "BBB": {
            "symbol": "BBB",
            "data": [
                [1735689600000, 20.0],
                [1735776000000, 21.0],
                [1735862400000, 22.0],
            ],
        },
    }

    assert price_frame(payloads).to_dict(as_series=False) == {
        "date": [date(2025, 1, 1), date(2025, 1, 3)],
        "AAA": [10.0, 12.0],
        "BBB": [20.0, 22.0],
    }


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ([[1735689600000, 10.0], [1735689600000, 11.0]], "duplicate"),
        ([[1735689600000, None]], "numeric"),
        ([[1735689600000]], "two-item"),
        ([[1735689600000, 0.0]], "positive"),
        ([[1735689600000, -1.0]], "positive"),
    ],
)
def test_price_frame_rejects_invalid_observations(
    data: list[list[object]], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        price_frame({"AAA": {"symbol": "AAA", "data": data}})


def test_price_frame_rejects_response_symbol_mismatch() -> None:
    with pytest.raises(ValueError, match="does not match"):
        price_frame(
            {
                "AAA": {
                    "symbol": "BBB",
                    "data": [[1735689600000, 10.0]],
                }
            }
        )


def test_price_frame_rejects_empty_date_intersection() -> None:
    with pytest.raises(ValueError, match="shared price dates"):
        price_frame(
            {
                "AAA": {"symbol": "AAA", "data": [[1735689600000, 10.0]]},
                "BBB": {"symbol": "BBB", "data": [[1735776000000, 20.0]]},
            }
        )


def test_price_frame_rejects_empty_cohort() -> None:
    with pytest.raises(ValueError, match="at least one"):
        price_frame({})


def test_fact_as_of_rolls_back_multiple_amendments() -> None:
    statement = {
        "dateFiled": "2023-02-01",
        "facts": {"revenue": 130.0},
        "sources": {
            "base": {
                "dateFiled": "2023-02-01",
                "amendment": False,
                "facts": {"revenue": {}},
            },
            "a1": {
                "dateFiled": "2023-06-01",
                "amendment": True,
                "facts": {"revenue": {"before": 100.0}},
            },
            "a2": {
                "dateFiled": "2023-09-01",
                "amendment": True,
                "facts": {"revenue": {"before": 120.0}},
            },
        },
    }

    assert fact_as_of(statement, "revenue", date(2023, 1, 31)) is None
    assert fact_as_of(statement, "revenue", date(2023, 3, 1)) == 100.0
    assert fact_as_of(statement, "revenue", date(2023, 7, 1)) == 120.0
    assert fact_as_of(statement, "revenue", date(2023, 10, 1)) == 130.0


def test_fact_as_of_keeps_value_when_amendment_has_no_before() -> None:
    statement = {
        "dateFiled": "2023-02-01",
        "facts": {"revenue": 130.0},
        "sources": {
            "amendment": {
                "dateFiled": "2023-09-01",
                "amendment": True,
                "facts": {"revenue": {}},
            }
        },
    }

    assert fact_as_of(statement, "revenue", date(2023, 3, 1)) == 130.0


def test_fact_as_of_rolls_back_before_value_from_ordinary_later_filing() -> None:
    statement = {
        "dateFiled": "2023-02-01",
        "facts": {"revenue": 130.0},
        "sources": {
            "later-filing": {
                "dateFiled": "2023-09-01",
                "amendment": False,
                "facts": {"revenue": {"before": 100.0}},
            }
        },
    }

    assert fact_as_of(statement, "revenue", date(2023, 3, 1)) == 100.0
    assert fact_as_of(statement, "revenue", date(2023, 10, 1)) == 130.0


@pytest.mark.parametrize(
    "statement",
    [
        {"dateFiled": "not-a-date", "facts": {"revenue": 1.0}},
        {
            "dateFiled": "2023-02-01",
            "facts": {"revenue": 2.0},
            "sources": {
                "bad": {
                    "dateFiled": "not-a-date",
                    "amendment": True,
                    "facts": {"revenue": {"before": 1.0}},
                }
            },
        },
    ],
)
def test_fact_as_of_rejects_malformed_filing_dates(
    statement: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="dateFiled"):
        fact_as_of(statement, "revenue", date(2023, 3, 1))


def test_fact_as_of_returns_none_for_missing_or_non_numeric_fact() -> None:
    statement = {
        "dateFiled": "2023-02-01",
        "facts": {"description": "not numeric"},
    }

    assert fact_as_of(statement, "revenue", date(2023, 3, 1)) is None
    assert fact_as_of(statement, "description", date(2023, 3, 1)) is None


def test_signal_frames_gates_score_until_exact_filing_date() -> None:
    statements = {
        "AAA": [
            make_statement("2021-12-31", "2022-02-01", 100.0, 10.0),
            make_statement("2022-12-31", "2023-02-01", 120.0, 24.0),
        ]
    }

    signals = signal_frames(statements, [date(2023, 1, 31), date(2023, 2, 1)])

    assert signals["fundamental_eligible"]["AAA"].to_list() == [0, 1]
    assert signals["fundamental_score"]["AAA"].to_list()[0] == 0.0
    assert signals["fundamental_score"]["AAA"].to_list()[1] == pytest.approx(
        (120.0 / 100.0 - 1.0) + (24.0 / 120.0)
    )


@pytest.mark.parametrize(
    "statements",
    [
        {
            "AAA": [
                make_statement("2021-12-31", "2022-02-01", 0.0, 1.0),
                make_statement("2022-12-31", "2023-02-01", 120.0, 24.0),
            ]
        },
        {
            "AAA": [
                make_statement("2021-12-31", "2022-02-01", None, 10.0),
                make_statement("2022-12-31", "2023-02-01", 120.0, 24.0),
            ]
        },
        {
            "AAA": [
                make_statement("2021-12-31", "2022-02-01", 100.0, 10.0),
                make_statement("2022-12-31", "2023-02-01", 120.0, None),
            ]
        },
    ],
)
def test_signal_frames_marks_incomplete_inputs_ineligible(
    statements: dict[str, list[dict[str, object]]],
) -> None:
    signals = signal_frames(statements, [date(2023, 2, 1)])

    assert signals["fundamental_eligible"]["AAA"].to_list() == [0]
    assert signals["fundamental_score"]["AAA"].to_list() == [0.0]


def test_signal_frames_uses_latest_available_fiscal_period() -> None:
    statements = {
        "AAA": [
            make_statement("2020-12-31", "2021-02-01", 80.0, 8.0),
            make_statement("2021-12-31", "2022-02-01", 100.0, 10.0),
            make_statement("2022-12-31", "2023-02-01", 150.0, 45.0),
        ]
    }

    scores = signal_frames(statements, [date(2022, 3, 1), date(2023, 3, 1)])[
        "fundamental_score"
    ]["AAA"].to_list()

    assert scores[0] == pytest.approx((100.0 / 80.0 - 1.0) + 0.1)
    assert scores[1] == pytest.approx((150.0 / 100.0 - 1.0) + 0.3)


def test_signal_frames_marks_non_consecutive_annual_periods_ineligible() -> None:
    statements = {
        "AAA": [
            make_statement("2020-12-31", "2021-02-01", 100.0, 10.0),
            make_statement("2022-12-31", "2023-02-01", 144.0, 14.4),
        ]
    }

    signals = signal_frames(statements, [date(2023, 3, 1)])

    assert signals["fundamental_eligible"]["AAA"].to_list() == [0]
    assert signals["fundamental_score"]["AAA"].to_list() == [0.0]


def test_signal_frames_rejects_duplicate_fiscal_periods() -> None:
    statements = {
        "AAA": [
            make_statement("2021-12-31", "2022-02-01", 100.0, 10.0),
            make_statement("2021-12-31", "2022-03-01", 105.0, 11.0),
        ]
    }

    with pytest.raises(ValueError, match="duplicate statement period"):
        signal_frames(statements, [date(2022, 4, 1)])


def test_signal_frames_reconstructs_amended_prior_revenue() -> None:
    prior_sources = {
        "amendment": {
            "dateFiled": "2023-06-01",
            "amendment": True,
            "facts": {"revenue": {"before": 100.0}},
        }
    }
    statements = {
        "AAA": [
            make_statement(
                "2021-12-31",
                "2022-02-01",
                110.0,
                10.0,
                sources=prior_sources,
            ),
            make_statement("2022-12-31", "2023-02-01", 120.0, 24.0),
        ]
    }

    scores = signal_frames(statements, [date(2023, 3, 1), date(2023, 7, 1)])[
        "fundamental_score"
    ]["AAA"].to_list()

    assert scores[0] == pytest.approx((120.0 / 100.0 - 1.0) + 0.2)
    assert scores[1] == pytest.approx((120.0 / 110.0 - 1.0) + 0.2)


def test_build_market_aligns_prices_and_point_in_time_signals() -> None:
    prices = {
        "AAA": {
            "symbol": "AAA",
            "data": [[1675209600000, 10.0], [1675296000000, 11.0]],
        }
    }
    statements = {
        "AAA": [
            make_statement("2021-12-31", "2022-02-01", 100.0, 10.0),
            make_statement("2022-12-31", "2023-02-01", 120.0, 24.0),
        ]
    }

    market = build_market(prices, statements)

    assert market.periods == (
        np.datetime64("2023-02-01T00:00:00.000000"),
        np.datetime64("2023-02-02T00:00:00.000000"),
    )
    assert market.securities == ("AAA",)
    assert list(market.signals["fundamental_eligible"].by_security["AAA"]) == [
        1,
        1,
    ]
