from __future__ import annotations

import traceback
from datetime import date
from typing import Any
from urllib.error import URLError

import pytest

from examples.stockfit.client import StockFitClient, StockFitError, safe_rate_headers


def test_safe_rate_headers_keeps_only_allow_listed_metadata() -> None:
    assert safe_rate_headers(
        {
            "X-RateLimit-Limit": "50",
            "Rate-Limit-Remaining": "49",
            "Retry-After": "2",
            "Authorization": "private",
            "X-Request-Id": "private-id",
        }
    ) == {
        "x-ratelimit-limit": "50",
        "rate-limit-remaining": "49",
        "retry-after": "2",
    }


def test_price_history_builds_authenticated_bounded_request() -> None:
    seen: list[tuple[Any, float]] = []

    def transport(request, timeout):
        seen.append((request, timeout))
        return 200, {"X-RateLimit-Remaining": "49"}, b'{"symbol":"AAPL","data":[]}'

    client = StockFitClient("secret-token", transport=transport, timeout=3.5)
    result = client.price_history(
        "aapl", date(2025, 1, 1), date(2025, 1, 31), resolution="1d"
    )

    request, timeout = seen[0]
    assert result["symbol"] == "AAPL"
    assert request.get_header("Authorization") == "Bearer secret-token"
    assert "symbol=AAPL" in request.full_url
    assert "resolution=1d" in request.full_url
    assert "from=2025-01-01" in request.full_url
    assert "to=2025-01-31" in request.full_url
    assert "adjusted=true" in request.full_url
    assert timeout == 3.5
    assert client.last_response_headers == {"X-RateLimit-Remaining": "49"}


def test_company_details_returns_an_object() -> None:
    def transport(request, timeout):
        assert request.full_url.endswith("/api/company/details?symbol=MSFT")
        return 200, {}, b'{"symbol":"MSFT","name":"Synthetic Corp"}'

    result = StockFitClient("token", transport=transport).company_details(" msft ")

    assert result == {"symbol": "MSFT", "name": "Synthetic Corp"}


def test_income_statement_returns_a_list_with_expected_query() -> None:
    def transport(request, timeout):
        assert "symbol=COST" in request.full_url
        assert "period=annual" in request.full_url
        assert "limit=8" in request.full_url
        assert "splitAdjust=true" in request.full_url
        return 200, {}, b'[{"period":"2024-08-31","facts":{}}]'

    result = StockFitClient("token", transport=transport).income_statement(
        "cost", limit=8
    )

    assert result == [{"period": "2024-08-31", "facts": {}}]


@pytest.mark.parametrize("status", [400, 401, 403, 429])
def test_http_errors_expose_status_without_credentials(status: int) -> None:
    def transport(request, timeout):
        return status, {}, b'{"message":"request rejected"}'

    client = StockFitClient("secret-token", transport=transport)

    with pytest.raises(StockFitError) as exc_info:
        client.company_details("AAPL")

    assert exc_info.value.status == status
    assert "request rejected" in str(exc_info.value)
    assert "secret-token" not in repr(exc_info.value)


def test_error_body_token_is_redacted() -> None:
    def transport(request, timeout):
        return 401, {}, b'{"message":"invalid secret-token"}'

    client = StockFitClient("secret-token", transport=transport)

    with pytest.raises(StockFitError) as exc_info:
        client.company_details("AAPL")

    assert "[REDACTED]" in str(exc_info.value)
    assert "secret-token" not in str(exc_info.value)
    assert "secret-token" not in repr(exc_info.value)


def test_transport_error_traceback_does_not_expose_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing_urlopen(request, timeout):
        raise URLError("connection failed for secret-token")

    monkeypatch.setattr("examples.stockfit.client.urlopen", failing_urlopen)
    client = StockFitClient("secret-token")

    with pytest.raises(StockFitError) as exc_info:
        client.company_details("AAPL")

    rendered = "".join(
        traceback.format_exception(
            type(exc_info.value), exc_info.value, exc_info.value.__traceback__
        )
    )
    assert "secret-token" not in rendered


def test_malformed_json_is_rejected() -> None:
    client = StockFitClient(
        "token", transport=lambda request, timeout: (200, {}, b"not-json")
    )

    with pytest.raises(StockFitError, match="invalid JSON"):
        client.company_details("AAPL")


@pytest.mark.parametrize(
    ("method", "body", "expected"),
    [
        ("company", b"[]", "JSON object"),
        ("price", b"[]", "JSON object"),
        ("income", b"{}", "JSON array"),
    ],
)
def test_successful_response_with_wrong_shape_is_rejected(
    method: str, body: bytes, expected: str
) -> None:
    client = StockFitClient("token", transport=lambda request, timeout: (200, {}, body))

    with pytest.raises(StockFitError, match=expected):
        if method == "company":
            client.company_details("AAPL")
        elif method == "price":
            client.price_history("AAPL", date(2025, 1, 1), date(2025, 1, 2))
        else:
            client.income_statement("AAPL")


def test_income_statement_rejects_non_object_items() -> None:
    client = StockFitClient(
        "token", transport=lambda request, timeout: (200, {}, b"[1]")
    )

    with pytest.raises(StockFitError, match="objects"):
        client.income_statement("AAPL")


def test_blank_token_is_rejected() -> None:
    with pytest.raises(ValueError, match="token must not be blank"):
        StockFitClient("  ")


@pytest.mark.parametrize("symbol", ["", "   "])
def test_blank_symbol_is_rejected(symbol: str) -> None:
    client = StockFitClient("token")

    with pytest.raises(ValueError, match="symbol must not be blank"):
        client.company_details(symbol)


def test_overlong_symbol_is_rejected() -> None:
    client = StockFitClient("token")

    with pytest.raises(ValueError, match="at most 10"):
        client.company_details("ABCDEFGHIJK")


def test_reversed_price_bounds_are_rejected() -> None:
    client = StockFitClient("token")

    with pytest.raises(ValueError, match="start must not be after end"):
        client.price_history("AAPL", date(2025, 2, 1), date(2025, 1, 1))


def test_invalid_resolution_is_rejected() -> None:
    client = StockFitClient("token")

    with pytest.raises(ValueError, match="resolution"):
        client.price_history(
            "AAPL", date(2025, 1, 1), date(2025, 2, 1), resolution="hourly"
        )


@pytest.mark.parametrize("limit", [0, 81])
def test_invalid_income_statement_limit_is_rejected(limit: int) -> None:
    client = StockFitClient("token")

    with pytest.raises(ValueError, match="limit must be between 1 and 80"):
        client.income_statement("AAPL", limit=limit)


def test_invalid_income_statement_period_is_rejected() -> None:
    client = StockFitClient("token")

    with pytest.raises(ValueError, match="period"):
        client.income_statement("AAPL", period="monthly")
