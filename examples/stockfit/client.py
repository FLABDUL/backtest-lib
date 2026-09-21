"""Narrow, testable client for the StockFit endpoints used by this example."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

type JsonObject = dict[str, Any]
type Transport = Callable[[Request, float], tuple[int, Mapping[str, str], bytes]]

BASE_URL = "https://api.stockfit.io/v1"
RESOLUTIONS = frozenset({"1d", "1wk", "1mo"})
STATEMENT_PERIODS = frozenset({"annual", "quarter", "ttm"})


def safe_rate_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Return only non-sensitive rate-limit response metadata."""

    allowed = ("ratelimit", "rate-limit", "retry-after")
    return {
        name.lower(): str(value)
        for name, value in headers.items()
        if any(marker in name.lower() for marker in allowed)
    }


class StockFitError(RuntimeError):
    """A sanitised StockFit transport or response error."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _urlopen_transport(
    request: Request, timeout: float
) -> tuple[int, Mapping[str, str], bytes]:
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read()
    except HTTPError as exc:
        return exc.code, dict(exc.headers or {}), exc.read()
    except (TimeoutError, URLError):
        raise StockFitError(
            "StockFit request failed before a response arrived"
        ) from None


def _normalise_symbol(symbol: str) -> str:
    normalised = symbol.strip().upper()
    if not normalised:
        raise ValueError("symbol must not be blank")
    if len(normalised) > 10:
        raise ValueError("symbol must be at most 10 characters")
    return normalised


@dataclass(slots=True)
class StockFitClient:
    """Client for the three StockFit calls required by the demonstration."""

    token: str = field(repr=False)
    base_url: str = BASE_URL
    timeout: float = 10.0
    transport: Transport = field(default=_urlopen_transport, repr=False)
    last_response_headers: dict[str, str] = field(
        default_factory=dict, init=False, repr=False
    )

    def __post_init__(self) -> None:
        self.token = self.token.strip()
        self.base_url = self.base_url.rstrip("/")
        if not self.token:
            raise ValueError("token must not be blank")
        if self.timeout <= 0:
            raise ValueError("timeout must be positive")

    def _get(
        self,
        path: str,
        params: Mapping[str, str | int | bool],
        expected_type: type[dict] | type[list],
    ) -> JsonObject | list[JsonObject]:
        encoded_params = {
            key: str(value).lower() if isinstance(value, bool) else value
            for key, value in params.items()
        }
        request = Request(
            f"{self.base_url}{path}?{urlencode(encoded_params)}",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.token}",
                "User-Agent": "backtest-lib-stockfit-example/1",
            },
        )
        status, headers, body = self.transport(request, self.timeout)
        self.last_response_headers = dict(headers)

        if not 200 <= status < 300:
            detail = ""
            try:
                error_payload = json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                error_payload = None
            if isinstance(error_payload, dict) and isinstance(
                error_payload.get("message"), str
            ):
                detail = error_payload["message"].replace(self.token, "[REDACTED]")
            message = f"StockFit request failed with HTTP {status}"
            if detail:
                message = f"{message}: {detail}"
            raise StockFitError(message, status=status)

        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise StockFitError(
                "StockFit returned invalid JSON", status=status
            ) from exc

        if not isinstance(payload, expected_type):
            expected_name = "JSON object" if expected_type is dict else "JSON array"
            raise StockFitError(
                f"StockFit returned a response that was not a {expected_name}",
                status=status,
            )
        if expected_type is list and not all(
            isinstance(item, dict) for item in payload
        ):
            raise StockFitError(
                "StockFit returned a JSON array containing items that were not objects",
                status=status,
            )
        return payload

    def company_details(self, symbol: str) -> JsonObject:
        payload = self._get(
            "/api/company/details", {"symbol": _normalise_symbol(symbol)}, dict
        )
        assert isinstance(payload, dict)
        return payload

    def price_history(
        self,
        symbol: str,
        start: date,
        end: date,
        *,
        resolution: str = "1d",
        adjusted: bool = True,
    ) -> JsonObject:
        if start > end:
            raise ValueError("start must not be after end")
        if resolution not in RESOLUTIONS:
            raise ValueError(f"resolution must be one of {sorted(RESOLUTIONS)}")
        payload = self._get(
            "/api/price/history",
            {
                "symbol": _normalise_symbol(symbol),
                "resolution": resolution,
                "from": start.isoformat(),
                "to": end.isoformat(),
                "adjusted": adjusted,
            },
            dict,
        )
        assert isinstance(payload, dict)
        return payload

    def income_statement(
        self,
        symbol: str,
        *,
        period: str = "annual",
        limit: int = 20,
        split_adjust: bool = True,
    ) -> list[JsonObject]:
        if period not in STATEMENT_PERIODS:
            raise ValueError(f"period must be one of {sorted(STATEMENT_PERIODS)}")
        if not 1 <= limit <= 80:
            raise ValueError("limit must be between 1 and 80")
        payload = self._get(
            "/api/financials/income-statement",
            {
                "symbol": _normalise_symbol(symbol),
                "period": period,
                "limit": limit,
                "splitAdjust": split_adjust,
            },
            list,
        )
        assert isinstance(payload, list)
        return payload
