from __future__ import annotations

from datetime import date

import polars as pl
import pytest

import backtest_lib as btl
from backtest_lib.engine.decision import HoldDecision, TargetWeightsDecision
from examples.stockfit.strategy import equal_weight_strategy, make_fundamental_strategy


def make_market() -> btl.MarketView:
    dates = [date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)]
    prices = pl.DataFrame(
        {"date": dates, "AAA": [10.0] * 3, "BBB": [20.0] * 3, "CCC": [30.0] * 3}
    )
    scores = pl.DataFrame(
        {
            "date": dates,
            "AAA": [0.0, 0.8, 0.1],
            "BBB": [0.0, 99.0, 10.0],
            "CCC": [0.0, 0.7, 0.2],
        }
    )
    eligible = pl.DataFrame(
        {
            "date": dates,
            "AAA": [0, 1, 1],
            "BBB": [0, 0, 1],
            "CCC": [0, 1, 1],
        }
    )
    return btl.MarketView(
        prices=prices,
        signals={
            "fundamental_score": scores,
            "fundamental_eligible": eligible,
        },
    )


def test_fundamental_strategy_selects_top_eligible_scores() -> None:
    market = make_market()

    decision = make_fundamental_strategy(top_n=2)(
        universe=("AAA", "BBB", "CCC"), market=market.truncated_to(2)
    )

    assert isinstance(decision, TargetWeightsDecision)
    assert decision.target_weights == {"AAA": 0.5, "CCC": 0.5}
    assert decision.fill_cash is True


def test_fundamental_strategy_cannot_see_future_score() -> None:
    market = make_market()

    decision = make_fundamental_strategy(top_n=1)(
        universe=market.securities, market=market.truncated_to(2)
    )

    assert isinstance(decision, TargetWeightsDecision)
    assert decision.target_weights == {"AAA": 1.0}


def test_fundamental_strategy_breaks_ties_by_symbol() -> None:
    market = make_market()
    tied = pl.DataFrame(
        {
            "date": [date(2025, 1, 2)],
            "AAA": [0.5],
            "BBB": [0.5],
            "CCC": [0.5],
        }
    )
    eligible = pl.DataFrame(
        {
            "date": [date(2025, 1, 2)],
            "AAA": [1],
            "BBB": [1],
            "CCC": [1],
        }
    )
    tied_market = btl.MarketView(
        prices=market.prices.close.by_period[:1],
        signals={"fundamental_score": tied, "fundamental_eligible": eligible},
    )

    decision = make_fundamental_strategy(top_n=2)(
        universe=("CCC", "BBB", "AAA"), market=tied_market
    )

    assert isinstance(decision, TargetWeightsDecision)
    assert decision.target_weights == {"AAA": 0.5, "BBB": 0.5}


def test_fundamental_strategy_holds_when_no_security_is_eligible() -> None:
    market = make_market().truncated_to(1)

    decision = make_fundamental_strategy()(universe=market.securities, market=market)

    assert isinstance(decision, HoldDecision)


def test_fundamental_strategy_rejects_non_positive_top_n() -> None:
    with pytest.raises(ValueError, match="top_n must be at least 1"):
        make_fundamental_strategy(top_n=0)


def test_equal_weight_strategy_targets_every_security() -> None:
    decision = equal_weight_strategy(("AAA", "BBB", "CCC"))

    assert isinstance(decision, TargetWeightsDecision)
    assert decision.target_weights == pytest.approx(
        {"AAA": 1 / 3, "BBB": 1 / 3, "CCC": 1 / 3}
    )
    assert decision.fill_cash is True


def test_monthly_backtest_stays_in_cash_until_signal_is_visible() -> None:
    dates = [
        date(2025, 1, 2),
        date(2025, 2, 3),
        date(2025, 3, 3),
        date(2025, 4, 1),
    ]
    prices = pl.DataFrame(
        {"date": dates, "AAA": [10.0] * 4, "BBB": [20.0] * 4, "CCC": [30.0] * 4}
    )
    scores = pl.DataFrame(
        {
            "date": dates,
            "AAA": [0.0, 0.6, 0.6, 0.6],
            "BBB": [0.0, 0.5, 0.5, 0.5],
            "CCC": [0.0, 0.4, 0.4, 0.4],
        }
    )
    eligible = pl.DataFrame(
        {
            "date": dates,
            "AAA": [0, 1, 1, 1],
            "BBB": [0, 1, 1, 1],
            "CCC": [0, 1, 1, 1],
        }
    )
    market = btl.MarketView(
        prices=prices,
        signals={
            "fundamental_score": scores,
            "fundamental_eligible": eligible,
        },
    )

    fundamental = btl.Backtest(
        make_fundamental_strategy(top_n=2),
        market,
        btl.cash(1_000_000),
        decision_schedule="monthly",
    ).run()
    baseline = btl.Backtest(
        equal_weight_strategy,
        market,
        btl.cash(1_000_000),
        decision_schedule="monthly",
    ).run()

    assert list(fundamental.gross_exposure)[:3] == [0.0, 0.0, 0.0]
    assert list(fundamental.gross_exposure)[3] > 0.0
    assert list(baseline.gross_exposure)[:2] == [0.0, 0.0]
    assert list(baseline.gross_exposure)[2] > 0.0
