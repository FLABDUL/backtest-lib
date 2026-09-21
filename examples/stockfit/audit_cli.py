"""Bounded orchestration for the StockFit data-quality audit."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date, datetime
from typing import Protocol

from examples.stockfit.audit import (
    AuditRun,
    audit_company_metadata,
    audit_prices,
    audit_statements,
    combine_company_audit,
    summarise_cohort,
)
from examples.stockfit.client import JsonObject, StockFitError, safe_rate_headers

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
AUDIT_START = date(2021, 1, 1)
STATEMENT_LIMIT = 10
_GLOBAL_FAILURE_STATUSES = frozenset({401, 403, 429})
_INVALID_RESPONSE = object()


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
