"""Pure transforms from StockFit-shaped payloads to backtest-lib inputs."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any

import polars as pl

import backtest_lib as btl


def _utc_date(timestamp_ms: int | float) -> date:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).date()


def _filing_date(value: object) -> date:
    if not isinstance(value, str):
        raise ValueError("dateFiled must be an ISO date string")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid dateFiled value: {value!r}") from exc


def _numeric(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    converted = float(value)
    return converted if math.isfinite(converted) else None


def fact_as_of(statement: Mapping[str, Any], fact: str, as_of: date) -> float | None:
    """Return a fact as it was known on a date, rolling back later amendments."""

    original_filing_date = _filing_date(statement.get("dateFiled"))
    if as_of < original_filing_date:
        return None

    facts = statement.get("facts")
    if not isinstance(facts, Mapping):
        return None
    value = _numeric(facts.get(fact))
    if value is None:
        return None

    sources = statement.get("sources", {})
    if not isinstance(sources, Mapping):
        raise ValueError("sources must be an object")

    rollbacks: list[tuple[date, float]] = []
    for source in sources.values():
        if not isinstance(source, Mapping):
            continue
        amendment_date = _filing_date(source.get("dateFiled"))
        if amendment_date <= as_of:
            continue
        source_facts = source.get("facts")
        if not isinstance(source_facts, Mapping):
            continue
        fact_change = source_facts.get(fact)
        if not isinstance(fact_change, Mapping):
            continue
        before = _numeric(fact_change.get("before"))
        if before is not None:
            rollbacks.append((amendment_date, before))

    for _, before in sorted(rollbacks, reverse=True):
        value = before
    return value


def price_frame(payloads: Mapping[str, Mapping[str, Any]]) -> pl.DataFrame:
    """Return sorted close prices on dates shared by every requested symbol."""

    if not payloads:
        raise ValueError("price payloads must contain at least one security")

    series_by_symbol: dict[str, dict[date, float]] = {}
    for requested_symbol, payload in payloads.items():
        symbol = requested_symbol.strip().upper()
        response_symbol = payload.get("symbol")
        if not isinstance(response_symbol, str) or response_symbol.upper() != symbol:
            raise ValueError(
                f"response symbol {response_symbol!r} does not match {symbol!r}"
            )

        observations = payload.get("data")
        if not isinstance(observations, list):
            raise ValueError(f"price data for {symbol} must be a JSON array")

        values: dict[date, float] = {}
        for observation in observations:
            if not isinstance(observation, list) or len(observation) != 2:
                raise ValueError(
                    f"price observation for {symbol} must be a two-item array"
                )
            timestamp, raw_price = observation
            if isinstance(timestamp, bool) or not isinstance(timestamp, int | float):
                raise ValueError(f"timestamp for {symbol} must be numeric")
            if isinstance(raw_price, bool) or not isinstance(raw_price, int | float):
                raise ValueError(f"price for {symbol} must be numeric")
            price = float(raw_price)
            if not math.isfinite(price) or price <= 0:
                raise ValueError(f"price for {symbol} must be finite and positive")
            observation_date = _utc_date(timestamp)
            if observation_date in values:
                raise ValueError(
                    f"duplicate price date for {symbol}: {observation_date}"
                )
            values[observation_date] = price
        series_by_symbol[symbol] = values

    shared_dates = set.intersection(
        *(set(values) for values in series_by_symbol.values())
    )
    if not shared_dates:
        raise ValueError("price payloads have no shared price dates")
    ordered_dates = sorted(shared_dates)

    columns: dict[str, list[date] | list[float]] = {"date": ordered_dates}
    columns.update(
        {
            symbol: [values[period] for period in ordered_dates]
            for symbol, values in series_by_symbol.items()
        }
    )
    return pl.DataFrame(columns)


def _period_date(statement: Mapping[str, Any]) -> date:
    value = statement.get("period")
    if not isinstance(value, str):
        raise ValueError("statement period must be an ISO date string")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"invalid statement period: {value!r}") from exc


def _score_as_of(
    ordered_statements: list[Mapping[str, Any]], as_of: date
) -> tuple[float, int]:
    available_indexes = [
        index
        for index, statement in enumerate(ordered_statements)
        if _filing_date(statement.get("dateFiled")) <= as_of
    ]
    if not available_indexes:
        return 0.0, 0

    current_index = available_indexes[-1]
    if current_index == 0:
        return 0.0, 0

    current = ordered_statements[current_index]
    previous = ordered_statements[current_index - 1]
    if _period_date(current).year != _period_date(previous).year + 1:
        return 0.0, 0
    current_revenue = fact_as_of(current, "revenue", as_of)
    previous_revenue = fact_as_of(previous, "revenue", as_of)
    operating_income = fact_as_of(current, "operatingIncome", as_of)
    if (
        current_revenue is None
        or previous_revenue is None
        or operating_income is None
        or current_revenue <= 0
        or previous_revenue <= 0
    ):
        return 0.0, 0

    score = (
        current_revenue / previous_revenue - 1.0 + operating_income / current_revenue
    )
    if not math.isfinite(score):
        return 0.0, 0
    return score, 1


def signal_frames(
    statements: Mapping[str, Sequence[Mapping[str, Any]]], dates: list[date]
) -> dict[str, pl.DataFrame]:
    """Build score and eligibility frames using facts knowable on each price date."""

    if not statements:
        raise ValueError("statements must contain at least one security")
    ordered_by_symbol: dict[str, list[Mapping[str, Any]]] = {}
    for symbol, records in statements.items():
        ordered = sorted(records, key=_period_date)
        periods = [_period_date(statement) for statement in ordered]
        if len(periods) != len(set(periods)):
            raise ValueError(f"duplicate statement period for {symbol.strip().upper()}")
        ordered_by_symbol[symbol.strip().upper()] = ordered

    score_columns: dict[str, list[date] | list[float]] = {"date": dates}
    eligibility_columns: dict[str, list[date] | list[int]] = {"date": dates}
    for symbol, ordered_statements in ordered_by_symbol.items():
        scores: list[float] = []
        eligibility: list[int] = []
        for as_of in dates:
            score, eligible = _score_as_of(ordered_statements, as_of)
            scores.append(score)
            eligibility.append(eligible)
        score_columns[symbol] = scores
        eligibility_columns[symbol] = eligibility

    return {
        "fundamental_score": pl.DataFrame(score_columns),
        "fundamental_eligible": pl.DataFrame(eligibility_columns),
    }


def build_market(
    price_payloads: Mapping[str, Mapping[str, Any]],
    statements: Mapping[str, Sequence[Mapping[str, Any]]],
) -> btl.MarketView:
    """Build an aligned market with point-in-time fundamental signals."""

    prices = price_frame(price_payloads)
    symbols = prices.columns[1:]
    statement_symbols = {symbol.strip().upper() for symbol in statements}
    if set(symbols) != statement_symbols:
        raise ValueError("price and statement securities must match")
    ordered_statements = {
        symbol: statements[
            next(key for key in statements if key.strip().upper() == symbol)
        ]
        for symbol in symbols
    }
    dates = prices["date"].to_list()
    signals: dict[str, Any] = signal_frames(ordered_statements, dates)
    return btl.MarketView(prices=prices, signals=signals)
