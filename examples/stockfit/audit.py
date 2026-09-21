from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from statistics import median
from typing import Literal, TypeGuard

type AuditStatus = Literal["pass", "review", "fail"]
type AuditMode = Literal["synthetic", "live"]

CHECK_ORDER = (
    "company_metadata",
    "price_coverage",
    "price_integrity",
    "statement_coverage",
    "fact_completeness",
    "filing_lag",
    "provenance",
)

THRESHOLDS = {
    "price_start_delay_days": 31,
    "price_end_staleness_days": 7,
    "price_gap_days": 7,
    "minimum_annual_statements": 5,
    "required_fact_completeness_pct": 100.0,
    "maximum_filing_lag_days": 90,
}

REASON_DEFINITIONS = {
    "company_response_invalid": "Company response is not an object.",
    "company_symbol_mismatch": (
        "Company response does not include the requested symbol."
    ),
    "company_sector_missing": "Sector metadata is missing.",
    "company_industry_missing": "Industry metadata is missing.",
    "price_response_invalid": "Price response has an invalid shape.",
    "price_symbol_mismatch": "Price response symbol differs from the request.",
    "price_empty": "Price response contains no observations.",
    "price_observation_invalid": "At least one price observation is invalid.",
    "price_duplicate_timestamp": "Price observations contain a duplicate timestamp.",
    "price_start_late": "Price coverage starts more than 31 days after requested.",
    "price_end_stale": "Price coverage ends more than seven days before execution.",
    "price_gap_large": "A calendar gap between prices exceeds seven days.",
    "statement_response_invalid": "Statement response is not a list.",
    "statement_row_invalid": "At least one annual statement row is malformed.",
    "filing_before_period_end": "A filing date precedes its fiscal period end.",
    "fiscal_year_duplicate": "Annual statements contain a duplicate fiscal year.",
    "no_usable_consecutive_pair": "No consecutive usable annual pair exists.",
    "statement_history_short": "Fewer than five annual statements were returned.",
    "fiscal_year_gap": "At least one fiscal year is missing from the returned range.",
    "fiscal_year_fallback": "A fiscal year was inferred from a validated period date.",
    "revenue_incomplete": "Revenue completeness is below 100 percent.",
    "operating_income_incomplete": (
        "Operating-income completeness is below 100 percent."
    ),
    "filing_lag_large": "Maximum filing lag exceeds 90 days.",
}

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


@dataclass(frozen=True, slots=True)
class AuditRun:
    mode: AuditMode
    generated_at: datetime
    cohort: tuple[str, ...]
    request_start: date
    request_end: date
    records: tuple[CompanyAudit, ...]
    run_status: Literal["complete", "incomplete"]
    missing_symbols: tuple[str, ...]
    failure_categories: tuple[str, ...]
    shared_price_start: date | None
    shared_price_end: date | None
    status_counts: dict[AuditStatus, int]
    rate_limit: dict[str, str]


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
        (later - earlier).days for earlier, later in zip(dates, dates[1:], strict=False)
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
) -> (
    tuple[
        date,
        date,
        int,
        bool,
        Mapping[object, object],
        Mapping[object, object],
        list[str],
    ]
    | None
):
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


def summarise_cohort(
    *,
    cohort: Sequence[str],
    records: Sequence[CompanyAudit],
    failures: Mapping[str, str],
    request_start: date,
    execution_time: datetime,
    mode: AuditMode,
    rate_limit: Mapping[str, str],
) -> AuditRun:
    normalised_cohort = tuple(symbol.strip().upper() for symbol in cohort)
    if len(set(normalised_cohort)) != len(normalised_cohort):
        raise ValueError("cohort symbols must be unique")

    records_by_symbol: dict[str, CompanyAudit] = {}
    for record in records:
        symbol = record.symbol.strip().upper()
        if symbol not in normalised_cohort or symbol in records_by_symbol:
            raise ValueError("record symbols must be unique members of the cohort")
        records_by_symbol[symbol] = record

    normalised_failures = {
        symbol.strip().upper(): category for symbol, category in failures.items()
    }
    if not set(normalised_failures).issubset(normalised_cohort):
        raise ValueError("failure symbols must be members of the cohort")
    for category in normalised_failures.values():
        if not _valid_failure_category(category):
            raise ValueError(f"unknown failure category: {category!r}")

    ordered_records = tuple(
        records_by_symbol[symbol]
        for symbol in normalised_cohort
        if symbol in records_by_symbol
    )
    missing_symbols = tuple(
        symbol for symbol in normalised_cohort if symbol not in records_by_symbol
    )
    failure_categories = tuple(
        dict.fromkeys(
            normalised_failures[symbol]
            for symbol in normalised_cohort
            if symbol in normalised_failures
        )
    )

    starts = [record.prices.start for record in ordered_records]
    ends = [record.prices.end for record in ordered_records]
    shared_start: date | None = None
    shared_end: date | None = None
    if ordered_records and all(value is not None for value in starts + ends):
        candidate_start = max(value for value in starts if value is not None)
        candidate_end = min(value for value in ends if value is not None)
        if candidate_start <= candidate_end:
            shared_start = candidate_start
            shared_end = candidate_end

    status_counts: dict[AuditStatus, int] = {
        "pass": 0,
        "review": 0,
        "fail": 0,
    }
    for record in ordered_records:
        status_counts[record.status] += 1

    return AuditRun(
        mode=mode,
        generated_at=execution_time,
        cohort=normalised_cohort,
        request_start=request_start,
        request_end=execution_time.date(),
        records=ordered_records,
        run_status="incomplete" if missing_symbols else "complete",
        missing_symbols=missing_symbols,
        failure_categories=failure_categories,
        shared_price_start=shared_start,
        shared_price_end=shared_end,
        status_counts=status_counts,
        rate_limit=dict(
            sorted((str(key), str(value)) for key, value in rate_limit.items())
        ),
    )


def cohort_summary(run: AuditRun) -> dict[str, object]:
    records = run.records
    return {
        "mode": run.mode,
        "generated_at": run.generated_at.isoformat(),
        "cohort": list(run.cohort),
        "request_start": run.request_start.isoformat(),
        "request_end": run.request_end.isoformat(),
        "run_status": run.run_status,
        "missing_symbols": list(run.missing_symbols),
        "failure_categories": list(run.failure_categories),
        "shared_price_start": (
            run.shared_price_start.isoformat() if run.shared_price_start else None
        ),
        "shared_price_end": (
            run.shared_price_end.isoformat() if run.shared_price_end else None
        ),
        "status_counts": dict(run.status_counts),
        "revenue_completeness_pct": _distribution(
            [record.statements.revenue_completeness_pct for record in records]
        ),
        "operating_income_completeness_pct": _distribution(
            [record.statements.operating_income_completeness_pct for record in records]
        ),
        "median_filing_lag_days": _distribution(
            [
                value
                for record in records
                if (value := record.statements.median_filing_lag_days) is not None
            ]
        ),
        "max_filing_lag_days": _distribution(
            [
                float(value)
                for record in records
                if (value := record.statements.max_filing_lag_days) is not None
            ]
        ),
        "provenance_totals": {
            "source_entries": sum(
                record.statements.source_entry_count for record in records
            ),
            "numeric_before_facts": sum(
                record.statements.before_fact_count for record in records
            ),
            "derived_facts": sum(
                record.statements.derived_fact_count for record in records
            ),
        },
        "rate_limit": dict(run.rate_limit),
        "thresholds": dict(THRESHOLDS),
        "reason_definitions": dict(REASON_DEFINITIONS),
        "raw_provider_data_retained": False,
    }


def heatmap_rows(run: AuditRun) -> tuple[dict[str, str], ...]:
    rows: list[dict[str, str]] = []
    for record in run.records:
        statuses = _check_statuses(record)
        rows.extend(
            {
                "symbol": record.symbol,
                "check": check,
                "status": statuses[check],
            }
            for check in CHECK_ORDER
        )
    return tuple(rows)


def _valid_failure_category(category: str) -> bool:
    return (
        category in {"timeout", "timeout_or_connection"}
        or re.fullmatch(r"http_[1-5][0-9]{2}", category) is not None
    )


def _distribution(values: Sequence[float]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "median": None, "max": None}
    return {
        "min": float(min(values)),
        "median": float(median(values)),
        "max": float(max(values)),
    }


def _check_statuses(record: CompanyAudit) -> dict[str, AuditStatus]:
    return {
        "company_metadata": record.metadata.status,
        "price_coverage": _status_for_selected_reasons(
            record.reasons,
            {"price_empty", "price_start_late", "price_end_stale", "price_gap_large"},
        ),
        "price_integrity": _status_for_selected_reasons(
            record.reasons,
            {
                "price_response_invalid",
                "price_symbol_mismatch",
                "price_observation_invalid",
                "price_duplicate_timestamp",
            },
        ),
        "statement_coverage": _status_for_selected_reasons(
            record.reasons,
            {
                "statement_response_invalid",
                "statement_row_invalid",
                "fiscal_year_duplicate",
                "no_usable_consecutive_pair",
                "statement_history_short",
                "fiscal_year_gap",
                "fiscal_year_fallback",
            },
        ),
        "fact_completeness": _status_for_selected_reasons(
            record.reasons,
            {"revenue_incomplete", "operating_income_incomplete"},
        ),
        "filing_lag": _status_for_selected_reasons(
            record.reasons,
            {"filing_before_period_end", "filing_lag_large"},
        ),
        "provenance": "pass",
    }


def _status_for_selected_reasons(
    reasons: Sequence[str], selected: set[str]
) -> AuditStatus:
    return _status_for_reasons(
        tuple(reason for reason in reasons if reason in selected)
    )
