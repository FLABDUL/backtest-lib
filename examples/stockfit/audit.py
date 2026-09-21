from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from statistics import median
from typing import Literal, TypeGuard

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
    "statement_response_invalid",
    "statement_row_invalid",
    "filing_before_period_end",
    "fiscal_year_duplicate",
    "no_usable_consecutive_pair",
    "statement_history_short",
    "fiscal_year_gap",
    "fiscal_year_fallback",
    "revenue_incomplete",
    "operating_income_incomplete",
    "filing_lag_large",
)

_FAIL_REASONS = {
    "company_response_invalid",
    "company_symbol_mismatch",
    "price_response_invalid",
    "price_symbol_mismatch",
    "price_empty",
    "price_observation_invalid",
    "price_duplicate_timestamp",
    "statement_response_invalid",
    "statement_row_invalid",
    "filing_before_period_end",
    "fiscal_year_duplicate",
    "no_usable_consecutive_pair",
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


@dataclass(frozen=True, slots=True)
class StatementAudit:
    statement_count: int
    fiscal_year_start: int | None
    fiscal_year_end: int | None
    missing_fiscal_year_count: int
    duplicate_fiscal_year_count: int
    revenue_completeness_pct: float
    operating_income_completeness_pct: float
    median_filing_lag_days: float | None
    max_filing_lag_days: int | None
    source_entry_count: int
    before_fact_count: int
    derived_fact_count: int
    fact_count: int
    derived_fact_incidence_pct: float
    status: AuditStatus
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CompanyAudit:
    symbol: str
    metadata: MetadataAudit
    prices: PriceAudit
    statements: StatementAudit
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


def audit_statements(statements: object) -> StatementAudit:
    if not isinstance(statements, list):
        return _empty_statement_audit("statement_response_invalid")

    years: list[int] = []
    filing_lags: list[int] = []
    annual_facts: dict[int, Mapping[object, object]] = {}
    revenue_complete = 0
    operating_income_complete = 0
    source_entry_count = 0
    before_fact_count = 0
    derived_fact_count = 0
    fact_count = 0
    reasons: set[str] = set()

    for row in statements:
        parsed = _parse_statement_row(row)
        if parsed is None:
            return _empty_statement_audit("statement_row_invalid")
        period, filed, fiscal_year, used_fallback, facts, sources, derived = parsed
        if used_fallback:
            reasons.add("fiscal_year_fallback")
        years.append(fiscal_year)
        annual_facts.setdefault(fiscal_year, facts)

        lag = (filed - period).days
        filing_lags.append(lag)
        if lag < 0:
            reasons.add("filing_before_period_end")

        revenue = facts.get("revenue")
        operating_income = facts.get("operatingIncome")
        if _is_finite_number(revenue):
            revenue_complete += 1
        if _is_finite_number(operating_income):
            operating_income_complete += 1

        fact_count += len(facts)
        derived_fact_count += len(derived)
        source_entry_count += len(sources)
        before_fact_count += _count_numeric_before_facts(sources)

    statement_count = len(statements)
    unique_years = sorted(set(years))
    fiscal_year_start = unique_years[0] if unique_years else None
    fiscal_year_end = unique_years[-1] if unique_years else None
    duplicate_count = len(years) - len(unique_years)
    missing_count = (
        fiscal_year_end - fiscal_year_start + 1 - len(unique_years)
        if fiscal_year_start is not None and fiscal_year_end is not None
        else 0
    )
    revenue_pct = _percentage(revenue_complete, statement_count)
    operating_income_pct = _percentage(operating_income_complete, statement_count)

    if duplicate_count:
        reasons.add("fiscal_year_duplicate")
    if not _has_usable_consecutive_pair(annual_facts):
        reasons.add("no_usable_consecutive_pair")
    if statement_count < 5:
        reasons.add("statement_history_short")
    if missing_count:
        reasons.add("fiscal_year_gap")
    if revenue_pct < 100.0:
        reasons.add("revenue_incomplete")
    if operating_income_pct < 100.0:
        reasons.add("operating_income_incomplete")
    if filing_lags and max(filing_lags) > 90:
        reasons.add("filing_lag_large")

    ordered_reasons = _ordered_reasons(reasons)
    return StatementAudit(
        statement_count=statement_count,
        fiscal_year_start=fiscal_year_start,
        fiscal_year_end=fiscal_year_end,
        missing_fiscal_year_count=missing_count,
        duplicate_fiscal_year_count=duplicate_count,
        revenue_completeness_pct=revenue_pct,
        operating_income_completeness_pct=operating_income_pct,
        median_filing_lag_days=float(median(filing_lags)) if filing_lags else None,
        max_filing_lag_days=max(filing_lags, default=None),
        source_entry_count=source_entry_count,
        before_fact_count=before_fact_count,
        derived_fact_count=derived_fact_count,
        fact_count=fact_count,
        derived_fact_incidence_pct=_percentage(derived_fact_count, fact_count),
        status=_status_for_reasons(ordered_reasons),
        reasons=ordered_reasons,
    )


def combine_company_audit(
    symbol: str,
    metadata: MetadataAudit,
    prices: PriceAudit,
    statements: StatementAudit,
) -> CompanyAudit:
    reasons = _ordered_reasons(
        set(metadata.reasons) | set(prices.reasons) | set(statements.reasons)
    )
    return CompanyAudit(
        symbol=symbol.strip().upper(),
        metadata=metadata,
        prices=prices,
        statements=statements,
        status=worst_status(metadata.status, prices.status, statements.status),
        reasons=reasons,
    )


def _parse_statement_row(
    row: object,
) -> tuple[
    date,
    date,
    int,
    bool,
    Mapping[object, object],
    Mapping[object, object],
    list[str],
] | None:
    if not isinstance(row, Mapping):
        return None
    period = _parse_date(row.get("period"))
    filed = _parse_date(row.get("dateFiled"))
    facts = row.get("facts")
    sources = row.get("sources")
    derived = row.get("derived", [])
    if (
        period is None
        or filed is None
        or not isinstance(facts, Mapping)
        or not isinstance(sources, Mapping)
        or not isinstance(derived, list)
        or not all(isinstance(item, str) for item in derived)
        or not _valid_sources(sources)
    ):
        return None

    fiscal_year_value = row.get("fiscalYear")
    valid_fiscal_year = isinstance(fiscal_year_value, int) and not isinstance(
        fiscal_year_value, bool
    )
    fiscal_year = fiscal_year_value if valid_fiscal_year else period.year
    return period, filed, fiscal_year, not valid_fiscal_year, facts, sources, derived


def _parse_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _valid_sources(sources: Mapping[object, object]) -> bool:
    for source in sources.values():
        if not isinstance(source, Mapping):
            return False
        source_facts = source.get("facts", {})
        if not isinstance(source_facts, Mapping):
            return False
    return True


def _count_numeric_before_facts(sources: Mapping[object, object]) -> int:
    count = 0
    for source in sources.values():
        if not isinstance(source, Mapping):
            continue
        source_facts = source.get("facts", {})
        if not isinstance(source_facts, Mapping):
            continue
        for fact in source_facts.values():
            if isinstance(fact, Mapping) and _is_finite_number(fact.get("before")):
                count += 1
    return count


def _is_finite_number(value: object) -> TypeGuard[int | float]:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


def _has_usable_consecutive_pair(
    annual_facts: Mapping[int, Mapping[object, object]],
) -> bool:
    for year in sorted(annual_facts):
        prior = annual_facts.get(year - 1)
        current = annual_facts[year]
        if prior is None:
            continue
        prior_revenue = prior.get("revenue")
        current_revenue = current.get("revenue")
        current_operating_income = current.get("operatingIncome")
        if (
            _is_finite_number(prior_revenue)
            and prior_revenue > 0
            and _is_finite_number(current_revenue)
            and current_revenue > 0
            and _is_finite_number(current_operating_income)
        ):
            return True
    return False


def _percentage(numerator: int, denominator: int) -> float:
    return 100.0 * numerator / denominator if denominator else 0.0


def _empty_statement_audit(reason: str) -> StatementAudit:
    return StatementAudit(
        statement_count=0,
        fiscal_year_start=None,
        fiscal_year_end=None,
        missing_fiscal_year_count=0,
        duplicate_fiscal_year_count=0,
        revenue_completeness_pct=0.0,
        operating_income_completeness_pct=0.0,
        median_filing_lag_days=None,
        max_filing_lag_days=None,
        source_entry_count=0,
        before_fact_count=0,
        derived_fact_count=0,
        fact_count=0,
        derived_fact_incidence_pct=0.0,
        status="fail",
        reasons=(reason,),
    )
