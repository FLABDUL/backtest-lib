from __future__ import annotations

import traceback
from datetime import UTC, date, datetime, time

import pytest

from examples.stockfit.audit_cli import (
    LIVE_COHORT,
    AuditRunAborted,
    collect_live_audit,
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
        self.calls.append(
            ("price_history", symbol, start, end, resolution, adjusted)
        )
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
        self.calls.append(
            ("income_statement", symbol, period, limit, split_adjust)
        )
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
