"""Offline-first StockFit point-in-time backtest demonstration."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

import altair as alt

import backtest_lib as btl
from backtest_lib.backtest.results import BacktestResults
from examples.stockfit.client import JsonObject, StockFitClient
from examples.stockfit.strategy import (
    equal_weight_strategy,
    make_fundamental_strategy,
)
from examples.stockfit.transforms import build_market

COHORT = ("AAPL", "MSFT", "COST")
FIXTURE_NOTICE = "Invented synthetic data; not a StockFit response"
FIXTURE_DIR = Path(__file__).with_name("fixtures")
DEFAULT_OUTPUT_DIR = Path("artifacts/stockfit")
LIVE_START = date(2021, 1, 1)
INITIAL_CAPITAL = 1_000_000


def _load_fixture(path: Path) -> object:
    wrapper = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(wrapper, dict) or wrapper.get("fixtureNotice") != FIXTURE_NOTICE:
        raise ValueError(f"fixture is missing the synthetic-data notice: {path}")
    if "payload" not in wrapper:
        raise ValueError(f"fixture is missing its payload: {path}")
    return wrapper["payload"]


def load_offline_payloads() -> tuple[
    dict[str, JsonObject], dict[str, list[JsonObject]]
]:
    """Load and validate the public invented fixtures."""

    prices: dict[str, JsonObject] = {}
    statements: dict[str, list[JsonObject]] = {}
    for symbol in COHORT:
        price_payload = _load_fixture(FIXTURE_DIR / f"{symbol}-price.json")
        statement_payload = _load_fixture(FIXTURE_DIR / f"{symbol}-income.json")
        if not isinstance(price_payload, dict):
            raise ValueError(f"price fixture for {symbol} must contain an object")
        if not isinstance(statement_payload, list) or not all(
            isinstance(item, dict) for item in statement_payload
        ):
            raise ValueError(f"income fixture for {symbol} must contain object rows")
        prices[symbol] = price_payload
        statements[symbol] = statement_payload
    return prices, statements


def _period_label(value: object) -> str:
    return str(value)[:10]


def _metrics(results: BacktestResults) -> JsonObject:
    return {
        "starting_date": _period_label(results.periods[0]),
        "ending_date": _period_label(results.periods[-1]),
        "observations": len(results.periods),
        "total_return": float(results.total_return),
        "annualised_return": float(results.annualized_return),
        "annualised_volatility": float(results.annualized_volatility),
        "sharpe": None if results.sharpe is None else float(results.sharpe),
        "maximum_drawdown": float(results.max_drawdown),
        "average_turnover": float(results.avg_turnover),
        "ending_nav": float(results.nav[-1]),
    }


def _chart_rows(
    fundamental: BacktestResults, baseline: BacktestResults
) -> list[dict[str, str | float]]:
    rows: list[dict[str, str | float]] = []
    for label, results in (
        ("Fundamental", fundamental),
        ("Equal weight", baseline),
    ):
        rows.extend(
            {
                "date": _period_label(period),
                "nav": float(nav),
                "strategy": label,
            }
            for period, nav in zip(results.periods, results.nav, strict=True)
        )
    return rows


def _write_chart(
    output_path: Path,
    fundamental: BacktestResults,
    baseline: BacktestResults,
    mode: Literal["synthetic", "live"],
) -> None:
    subtitle = (
        "Synthetic demonstration data"
        if mode == "synthetic"
        else "Derived from StockFit data; raw data not redistributed"
    )
    chart = (
        alt.Chart(alt.Data(values=_chart_rows(fundamental, baseline)))
        .mark_line(strokeWidth=2)
        .encode(
            x=alt.X("date:T", title="Date"),
            y=alt.Y("nav:Q", title="Portfolio value"),
            color=alt.Color("strategy:N", title="Strategy"),
        )
        .properties(
            width=760,
            height=420,
            title=alt.Title("Point-in-time strategy comparison", subtitle=[subtitle]),
        )
    )
    chart.save(output_path)


def run_demo(
    price_payloads: Mapping[str, Mapping[str, Any]],
    statements: Mapping[str, Sequence[Mapping[str, Any]]],
    output_dir: str | Path,
    *,
    mode: Literal["synthetic", "live"],
    now: datetime | None = None,
) -> JsonObject:
    """Run matched strategies and write derived metrics and a NAV chart."""

    generated_at = now or datetime.now(UTC)
    market = build_market(price_payloads, statements)
    fundamental = btl.Backtest(
        make_fundamental_strategy(top_n=2),
        market,
        btl.cash(INITIAL_CAPITAL),
        decision_schedule="monthly",
    ).run()
    baseline = btl.Backtest(
        equal_weight_strategy,
        market,
        btl.cash(INITIAL_CAPITAL),
        decision_schedule="monthly",
    ).run()

    summary: JsonObject = {
        "mode": mode,
        "generated_at": generated_at.astimezone(UTC).isoformat(),
        "cohort": list(market.securities),
        "coverage": {
            "start": _period_label(market.periods[0]),
            "end": _period_label(market.periods[-1]),
            "observations": len(market.periods),
        },
        "methodology": {
            "score": "year-on-year revenue growth plus operating margin",
            "selection": "top two eligible securities, equal weighted",
            "decision_schedule": "monthly",
            "initial_capital": INITIAL_CAPITAL,
            "initial_position": "cash",
            "baseline": "equal weight over the same cohort and dates",
        },
        "strategies": {
            "fundamental": _metrics(fundamental),
            "equal_weight": _metrics(baseline),
        },
    }

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    _write_chart(destination / "nav-comparison.svg", fundamental, baseline, mode)
    return summary


def run_offline_demo(
    output_dir: str | Path = DEFAULT_OUTPUT_DIR, *, now: datetime | None = None
) -> JsonObject:
    prices, statements = load_offline_payloads()
    return run_demo(prices, statements, output_dir, mode="synthetic", now=now)


def run_live_demo(
    token: str,
    output_dir: str | Path,
    *,
    now: datetime | None = None,
) -> JsonObject:
    generated_at = now or datetime.now(UTC)
    client = StockFitClient(token)
    prices = {
        symbol: client.price_history(
            symbol, LIVE_START, generated_at.date(), resolution="1d", adjusted=True
        )
        for symbol in COHORT
    }
    statements = {
        symbol: client.income_statement(
            symbol, period="annual", limit=10, split_adjust=True
        )
        for symbol in COHORT
    }
    return run_demo(
        prices,
        statements,
        output_dir,
        mode="live",
        now=generated_at,
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the offline StockFit-shaped demo or an explicit live trial run."
        )
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Use live StockFit data. Requires STOCKFIT_TOKEN.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for derived summary and chart files.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.live:
        token = os.environ.get("STOCKFIT_TOKEN", "").strip()
        if not token:
            raise SystemExit(
                "Set STOCKFIT_TOKEN in this process before running with --live."
            )
        summary = run_live_demo(token, args.output_dir)
    else:
        summary = run_offline_demo(args.output_dir)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
