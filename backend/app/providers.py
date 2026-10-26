from dataclasses import dataclass
from typing import Any, Protocol

import requests

from backend.app.ingestion import ALPHA_VANTAGE_URL, _parse_daily_response
from backend.app.settings import get_market_api_key


class ProviderError(RuntimeError):
    pass


class UnsupportedTickerError(ProviderError):
    pass


class ProviderRateLimitError(ProviderError):
    pass


@dataclass(frozen=True)
class DailyHistory:
    provider: str
    prices: list[dict[str, Any]]


class MarketDataProvider(Protocol):
    name: str

    def get_daily_history(self, ticker: str) -> DailyHistory: ...


class AlphaVantageProvider:
    name = "alphavantage"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.api_key = api_key or get_market_api_key()
        self.session = session or requests.Session()
        self._owns_session = session is None

    def close(self) -> None:
        if self._owns_session:
            self.session.close()

    def get_daily_history(self, ticker: str) -> DailyHistory:
        response = self.session.get(
            ALPHA_VANTAGE_URL,
            params={
                "function": "TIME_SERIES_DAILY",
                "symbol": ticker,
                "outputsize": "compact",
                "apikey": self.api_key,
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            rate_message = payload.get("Note") or payload.get("Information")
            if rate_message:
                raise ProviderRateLimitError(str(rate_message))
            if payload.get("Error Message"):
                raise UnsupportedTickerError(str(payload["Error Message"]))
        try:
            prices = _parse_daily_response(payload)
        except RuntimeError as exc:
            raise ProviderError(str(exc)) from exc
        return DailyHistory(provider=self.name, prices=prices)
