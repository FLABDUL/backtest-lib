from __future__ import annotations

import json
from datetime import date

import pytest

from examples.stockfit import smoke


class FakeClient:
    def __init__(self) -> None:
        self.last_response_headers: dict[str, str] = {}
        self.calls: list[tuple[object, ...]] = []

    def company_details(self, symbol: str) -> dict[str, object]:
        self.calls.append(("company_details", symbol))
        self.last_response_headers = {
            "x-ratelimit-limit": "50",
            "x-ratelimit-remaining": "49",
            "authorization": "must-not-be-copied",
        }
        return {
            "symbols": ["AAPL"],
            "cik": 320193,
            "name": "Synthetic Apple Example",
            "description": "raw narrative must not be copied",
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
        self.last_response_headers = {"x-ratelimit-remaining": "48"}
        return {
            "symbol": "AAPL",
            "resolution": "1d",
            "adjustedAsOf": "2026-09-20T20:00:00Z",
            "data": [[1735689600000, 123.45], [1735776000000, 125.0]],
            "latestQuote": {"close": 125.0},
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
        self.last_response_headers = {"x-ratelimit-remaining": "47"}
        return [
            {
                "period": "2023-09-30",
                "dateFiled": "2023-11-01",
                "facts": {"revenue": 90_000_000, "operatingIncome": 9_000_000},
                "sources": {"synthetic": {"dateFiled": "2023-11-01"}},
            },
            {
                "period": "2024-09-30",
                "dateFiled": "2024-11-01",
                "facts": {
                    "revenue": 100_000_000,
                    "operatingIncome": 12_000_000,
                },
                "sources": {"synthetic": {"dateFiled": "2024-11-01"}},
            },
        ]


def test_summary_contains_shape_not_raw_financial_values() -> None:
    client = FakeClient()

    summary = smoke.summarise_contract(client, symbol="AAPL")
    encoded = json.dumps(summary)

    assert summary["price_history"]["observations"] == 2
    assert summary["income_statement"]["periods"] == 2
    assert "facts_keys" in summary["income_statement"]
    assert "100000000" not in encoded
    assert "123.45" not in encoded
    assert "synthetic" not in encoded
    assert "authorization" not in encoded.lower()
    assert "token" not in encoded.lower()


def test_summary_uses_bounded_calls_and_safe_rate_headers() -> None:
    client = FakeClient()

    summary = smoke.summarise_contract(client, symbol="aapl")

    assert client.calls == [
        ("company_details", "AAPL"),
        (
            "price_history",
            "AAPL",
            date(2025, 1, 1),
            date(2025, 1, 10),
            "1d",
            True,
        ),
        ("income_statement", "AAPL", "annual", 4, True),
    ]
    assert summary["company_details"]["rate_limit"] == {
        "x-ratelimit-limit": "50",
        "x-ratelimit-remaining": "49",
    }
    assert summary["price_history"]["rate_limit"] == {"x-ratelimit-remaining": "48"}
    assert summary["income_statement"]["rate_limit"] == {"x-ratelimit-remaining": "47"}


def test_summary_reports_keys_dates_and_filing_coverage() -> None:
    summary = smoke.summarise_contract(FakeClient(), symbol="AAPL")

    assert summary["company_details"]["top_level_keys"] == [
        "cik",
        "description",
        "name",
        "symbols",
    ]
    assert summary["company_details"]["symbols"] == ["AAPL"]
    assert summary["price_history"]["first_date"] == "2025-01-01"
    assert summary["price_history"]["last_date"] == "2025-01-02"
    assert summary["income_statement"]["date_filed_present"] == 2
    assert summary["income_statement"]["facts_keys"] == [
        "operatingIncome",
        "revenue",
    ]


def test_main_requires_environment_token_without_secret_file_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("STOCKFIT_TOKEN", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        smoke.main([])

    message = str(exc_info.value)
    assert "Set STOCKFIT_TOKEN" in message
    assert ".codex" not in message
    assert "stockfit-token.txt" not in message
