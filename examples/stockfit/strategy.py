"""Transparent strategies for the StockFit point-in-time demonstration."""

from __future__ import annotations

from collections.abc import Sequence

import backtest_lib as btl
from backtest_lib.engine.decision import Decision
from backtest_lib.market import MarketView


def make_fundamental_strategy(top_n: int = 2) -> btl.Strategy:
    """Rank eligible securities by the point-in-time fundamental score."""

    if top_n < 1:
        raise ValueError("top_n must be at least 1")

    def strategy(universe: Sequence[str], market: MarketView) -> Decision:
        score = market.signals["fundamental_score"].by_period[-1]
        eligible = market.signals["fundamental_eligible"].by_period[-1]
        ranked = sorted(
            (security for security in universe if int(eligible[security]) == 1),
            key=lambda security: (-float(score[security]), security),
        )[:top_n]
        if not ranked:
            return btl.hold()
        weight = 1.0 / len(ranked)
        return btl.target_weights(
            {security: weight for security in ranked}, fill_cash=True
        )

    return strategy


def equal_weight_strategy(universe: Sequence[str]) -> Decision:
    """Target equal weights across the comparison universe."""

    weight = 1.0 / len(universe)
    return btl.target_weights(
        {security: weight for security in universe}, fill_cash=True
    )
