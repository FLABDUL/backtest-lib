from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

type AuditStatus = Literal["pass", "review", "fail"]

STATUS_SEVERITY: dict[AuditStatus, int] = {
    "pass": 0,
    "review": 1,
    "fail": 2,
}

REASON_ORDER = (
    "company_response_invalid",
    "company_symbol_mismatch",
    "company_sector_missing",
    "company_industry_missing",
    "price_response_invalid",
    "price_symbol_mismatch",
    "price_empty",
    "price_observation_invalid",
    "price_duplicate_timestamp",
    "price_start_late",
    "price_end_stale",
    "price_gap_large",
)

_FAIL_REASONS = {
    "company_response_invalid",
    "company_symbol_mismatch",
    "price_response_invalid",
    "price_symbol_mismatch",
    "price_empty",
    "price_observation_invalid",
    "price_duplicate_timestamp",
}


@dataclass(frozen=True, slots=True)
class MetadataAudit:
    symbol: str
    company_name: str | None
    sector: str | None
    industry: str | None
    stable_identifiers_present: bool
    status: AuditStatus
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PriceAudit:
    start: date | None
    end: date | None
    observations: int
    duplicate_count: int
    invalid_count: int
    out_of_order_count: int
    largest_gap_days: int | None
    status: AuditStatus
    reasons: tuple[str, ...]


def worst_status(*statuses: AuditStatus) -> AuditStatus:
    return max(statuses, key=lambda status: STATUS_SEVERITY[status], default="pass")


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _ordered_reasons(reasons: set[str]) -> tuple[str, ...]:
    return tuple(reason for reason in REASON_ORDER if reason in reasons)


def _status_for_reasons(reasons: tuple[str, ...]) -> AuditStatus:
    if any(reason in _FAIL_REASONS for reason in reasons):
        return "fail"
    if reasons:
        return "review"
    return "pass"


def audit_company_metadata(symbol: str, payload: object) -> MetadataAudit:
    normalised_symbol = symbol.strip().upper()
    if not isinstance(payload, Mapping):
        return MetadataAudit(
            symbol=normalised_symbol,
            company_name=None,
            sector=None,
            industry=None,
            stable_identifiers_present=False,
            status="fail",
            reasons=("company_response_invalid",),
        )

    symbols = payload.get("symbols")
    if not (
        isinstance(symbols, list)
        and all(isinstance(item, str) for item in symbols)
        and normalised_symbol in {item.strip().upper() for item in symbols}
    ):
        return MetadataAudit(
            symbol=normalised_symbol,
            company_name=_text(payload.get("name")),
            sector=_text(payload.get("sector")),
            industry=_text(payload.get("industry")),
            stable_identifiers_present=False,
            status="fail",
            reasons=("company_symbol_mismatch",),
        )

    sector = _text(payload.get("sector"))
    industry = _text(payload.get("industry"))
    reasons: set[str] = set()
    if sector is None:
        reasons.add("company_sector_missing")
    if industry is None:
        reasons.add("company_industry_missing")
    ordered_reasons = _ordered_reasons(reasons)

    return MetadataAudit(
        symbol=normalised_symbol,
        company_name=_text(payload.get("name")),
        sector=sector,
        industry=industry,
        stable_identifiers_present=any(
            _text(payload.get(identifier)) is not None
            for identifier in ("cik", "cusip", "figi")
        ),
        status=_status_for_reasons(ordered_reasons),
        reasons=ordered_reasons,
    )


def audit_prices(
    symbol: str,
    payload: object,
    *,
    request_start: date,
    execution_date: date,
) -> PriceAudit:
    normalised_symbol = symbol.strip().upper()
    if not isinstance(payload, Mapping):
        return _invalid_price_response()

    returned_symbol = payload.get("symbol")
    observations = payload.get("data")
    if not isinstance(returned_symbol, str) or not isinstance(observations, list):
        return _invalid_price_response()
    if returned_symbol.strip().upper() != normalised_symbol:
        return _empty_price_audit("price_symbol_mismatch")
    if not observations:
        return _empty_price_audit("price_empty")

    valid_timestamps: list[float] = []
    unique_dates: dict[float, date] = {}
    invalid_count = 0
    duplicate_count = 0
    out_of_order_count = 0
    previous_timestamp: float | None = None

    for observation in observations:
        parsed = _parse_price_observation(observation)
        if parsed is None:
            invalid_count += 1
            continue
        timestamp, observation_date = parsed
        if previous_timestamp is not None and timestamp < previous_timestamp:
            out_of_order_count += 1
        previous_timestamp = timestamp
        valid_timestamps.append(timestamp)
        if timestamp in unique_dates:
            duplicate_count += 1
        else:
            unique_dates[timestamp] = observation_date

    dates = sorted(unique_dates.values())
    start = dates[0] if dates else None
    end = dates[-1] if dates else None
    gaps = [
        (later - earlier).days
        for earlier, later in zip(dates, dates[1:], strict=False)
    ]
    largest_gap_days = max(gaps, default=None)

    reasons: set[str] = set()
    if invalid_count:
        reasons.add("price_observation_invalid")
    if duplicate_count:
        reasons.add("price_duplicate_timestamp")
    if start is not None and start > request_start + timedelta(days=31):
        reasons.add("price_start_late")
    if end is not None and end < execution_date - timedelta(days=7):
        reasons.add("price_end_stale")
    if largest_gap_days is not None and largest_gap_days > 7:
        reasons.add("price_gap_large")

    ordered_reasons = _ordered_reasons(reasons)
    return PriceAudit(
        start=start,
        end=end,
        observations=len(unique_dates),
        duplicate_count=duplicate_count,
        invalid_count=invalid_count,
        out_of_order_count=out_of_order_count,
        largest_gap_days=largest_gap_days,
        status=_status_for_reasons(ordered_reasons),
        reasons=ordered_reasons,
    )


def _parse_price_observation(observation: object) -> tuple[float, date] | None:
    if not isinstance(observation, list) or len(observation) != 2:
        return None
    timestamp, price = observation
    if (
        isinstance(timestamp, bool)
        or not isinstance(timestamp, (int, float))
        or not math.isfinite(timestamp)
        or isinstance(price, bool)
        or not isinstance(price, (int, float))
        or not math.isfinite(price)
        or price <= 0
    ):
        return None
    try:
        observation_date = datetime.fromtimestamp(timestamp / 1000, tz=UTC).date()
    except (OSError, OverflowError, ValueError):
        return None
    return float(timestamp), observation_date


def _invalid_price_response() -> PriceAudit:
    return _empty_price_audit("price_response_invalid")


def _empty_price_audit(reason: str) -> PriceAudit:
    return PriceAudit(
        start=None,
        end=None,
        observations=0,
        duplicate_count=0,
        invalid_count=0,
        out_of_order_count=0,
        largest_gap_days=None,
        status="fail",
        reasons=(reason,),
    )
