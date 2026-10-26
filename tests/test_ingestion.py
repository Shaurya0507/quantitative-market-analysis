from datetime import date
from decimal import Decimal

import pytest

from backend.app.ingestion import _parse_daily_response, ingest_ticker


def test_parse_daily_response_converts_alpha_vantage_records():
    payload = {
        "Time Series (Daily)": {
            "2026-10-23": {
                "1. open": "100.0000",
                "2. high": "105.0000",
                "3. low": "99.0000",
                "4. close": "104.0000",
                "5. volume": "12345",
            }
        }
    }

    records = _parse_daily_response(payload)

    assert records == [
        {
            "price_date": date(2026, 10, 23),
            "open_price": Decimal("100.0000"),
            "high_price": Decimal("105.0000"),
            "low_price": Decimal("99.0000"),
            "close_price": Decimal("104.0000"),
            "volume": 12345,
        }
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"Note": "API call frequency exceeded"},
        {"Time Series (Daily)": {}},
        {
            "Time Series (Daily)": {
                "2026-10-23": {
                    "1. open": "100",
                    "2. high": "101",
                    "3. low": "99",
                    "4. close": "102",
                    "5. volume": "1",
                }
            }
        },
    ],
)
def test_parse_daily_response_rejects_provider_errors_and_invalid_data(payload):
    with pytest.raises((RuntimeError, ValueError)):
        _parse_daily_response(payload)


def test_ingest_ticker_rejects_invalid_symbol_before_network_or_database_access():
    with pytest.raises(ValueError, match="Invalid ticker symbol"):
        ingest_ticker("not a ticker")
