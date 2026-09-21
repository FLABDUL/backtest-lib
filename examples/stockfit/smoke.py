"""Bounded, sanitised live check for the StockFit trial."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any, Protocol

from examples.stockfit.client import JsonObject, StockFitClient, safe_rate_headers

DETAIL_SYMBOL = "AAPL"
PRICE_START = date(2025, 1, 1)
PRICE_END = date(2025, 1, 10)
STATEMENT_LIMIT = 4


class ContractClient(Protocol):
    """The client surface needed by the smoke summary."""

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


def _timestamp_date(value: object) -> str | None:
    if not isinstance(value, int | float):
        return None
    return datetime.fromtimestamp(value / 1000, tz=UTC).date().isoformat()


def _price_dates(payload: Mapping[str, Any]) -> tuple[str | None, str | None]:
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        return None, None
    timestamps = [item[0] for item in data if isinstance(item, list) and len(item) == 2]
    if not timestamps:
        return None, None
    return _timestamp_date(timestamps[0]), _timestamp_date(timestamps[-1])


def summarise_contract(
    client: ContractClient, symbol: str = DETAIL_SYMBOL
) -> JsonObject:
    """Call the three approved endpoints and return metadata only."""

    normalised_symbol = symbol.strip().upper()

    company = client.company_details(normalised_symbol)
    company_rate = safe_rate_headers(client.last_response_headers)

    prices = client.price_history(
        normalised_symbol,
        PRICE_START,
        PRICE_END,
        resolution="1d",
        adjusted=True,
    )
    price_rate = safe_rate_headers(client.last_response_headers)

    statements = client.income_statement(
        normalised_symbol,
        period="annual",
        limit=STATEMENT_LIMIT,
        split_adjust=True,
    )
    statement_rate = safe_rate_headers(client.last_response_headers)

    data = prices.get("data")
    observation_count = len(data) if isinstance(data, list) else 0
    first_date, last_date = _price_dates(prices)
    facts_keys = sorted(
        {
            key
            for statement in statements
            for key in (
                statement.get("facts", {}).keys()
                if isinstance(statement.get("facts"), dict)
                else ()
            )
        }
    )

    return {
        "company_details": {
            "symbols": company.get("symbols"),
            "cik": company.get("cik"),
            "name": company.get("name"),
            "top_level_keys": sorted(company),
            "rate_limit": company_rate,
        },
        "price_history": {
            "symbol": prices.get("symbol"),
            "resolution": prices.get("resolution"),
            "observations": observation_count,
            "first_date": first_date,
            "last_date": last_date,
            "adjusted_as_of_present": bool(prices.get("adjustedAsOf")),
            "top_level_keys": sorted(prices),
            "rate_limit": price_rate,
        },
        "income_statement": {
            "periods": len(statements),
            "date_filed_present": sum(
                isinstance(statement.get("dateFiled"), str) for statement in statements
            ),
            "facts_keys": facts_keys,
            "top_level_keys": sorted(
                {key for statement in statements for key in statement}
            ),
            "rate_limit": statement_rate,
        },
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run three bounded StockFit calls and print schema metadata only."
    )
    parser.add_argument("--symbol", default=DETAIL_SYMBOL)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    token = os.environ.get("STOCKFIT_TOKEN", "").strip()
    if not token:
        raise SystemExit(
            "Set STOCKFIT_TOKEN in this process before running the smoke check."
        )
    summary = summarise_contract(StockFitClient(token), symbol=args.symbol)
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
